from __future__ import annotations

from abc import ABC, abstractmethod
from collections import deque
from copy import deepcopy
from contextlib import contextmanager, nullcontext
from dataclasses import dataclass, field, fields, is_dataclass, replace
import heapq
from itertools import count
import random
import weakref
from typing import Any, Callable, Iterable, Literal, Optional

from wujiang.tactical.engine.army import (
    army_public_state,
    command_hero_units,
    default_army_orders,
    is_army_soldier,
    living_army_units,
    parse_army_slot,
    resolve_army_phase,
    with_army_turn_slots,
)


@contextmanager
def battle_state_rollback(battle: "Battle", *, ai_probe: bool = True) -> Iterable[None]:
    """Reuse the same reversible calculation boundary for AI and reaction causality."""
    def value_copy(value: Any) -> Any:
        if isinstance(value, dict):
            return {key: value_copy(item) for key, item in value.items()}
        if isinstance(value, list):
            return [value_copy(item) for item in value]
        if isinstance(value, tuple):
            return tuple(value_copy(item) for item in value)
        if isinstance(value, set):
            return set(value)
        if isinstance(value, deque):
            return deque(value_copy(item) for item in value)
        return value

    def state(obj: Any) -> dict[str, Any]:
        if hasattr(obj, "__dict__"):
            return {key: value_copy(value) for key, value in obj.__dict__.items()}
        return {item.name: value_copy(getattr(obj, item.name)) for item in fields(obj)}

    units = list({unit.unit_id: unit for unit in [*battle.all_units(), *battle.destroyed_units]}.values())
    objects = [battle, *units, *battle.field_effects]
    if battle.pending_chain is not None:
        objects.extend([battle.pending_chain, battle.pending_chain.queued_action])
        objects.extend(battle.pending_chain.chosen_reactions)
        for options in battle.pending_chain.options_by_unit.values():
            objects.extend(options)
    objects.extend(battle.pending_followup_actions)
    if battle.resolving_action is not None:
        objects.append(battle.resolving_action)
    for unit in units:
        components = list(unit.iter_components())
        objects.extend(components)
        for component in components:
            objects.extend(component.runtime_children())
        objects.append(unit.base_stats)
    # QueuedAction, ReactionWindow and ReactionOption use slots. Their mutable
    # payloads and reactor lists must roll back with units, including nested probes.
    saved = [(obj, state(obj)) for obj in {id(obj): obj for obj in objects}.values()
             if hasattr(obj, "__dict__") or (is_dataclass(obj) and not obj.__dataclass_params__.frozen)]
    stats = [(unit.base_stats, replace(unit.base_stats)) for unit in units]
    rng = random.getstate()
    try:
        if ai_probe:
            battle._ai_probe_active = True
        else:
            battle.on_replay_checkpoint = None
        yield
    finally:
        for obj, snapshot in reversed(saved):
            if hasattr(obj, "__dict__"):
                obj.__dict__.clear()
                obj.__dict__.update(snapshot)
            else:
                for name, value in snapshot.items():
                    setattr(obj, name, value)
        for original, snapshot in stats:
            for name in ("attack", "defense", "speed", "attack_range", "mana"):
                setattr(original, name, getattr(snapshot, name))
        random.setstate(rng)


DEFAULT_HERO_TURN_LIMIT = 200
SKIRMISH_HERO_TURN_LIMIT = 50
SIEGE_HERO_TURN_LIMIT = 200
ENCOUNTER_HERO_TURN_LIMIT = 50


_id_counter = count(1)


class ActionError(RuntimeError):
    """Raised when an action cannot be performed."""


class ActionMiss(ActionError):
    """Raised when a queued action resolves on its original cell but misses."""


class DamageChoiceRequired(Exception):
    def __init__(self, *, unit_id: str, action_name: str, damage: float,
                 event_index: int, stats: list[str] | None = None,
                 kind: str = "stat", options: list[str] | None = None) -> None:
        self.unit_id = unit_id
        self.action_name = action_name
        self.damage = damage
        self.event_index = event_index
        self.stats = stats or []
        self.kind = kind
        self.options = options or []


@dataclass(frozen=True, slots=True)
class Position:
    x: int
    y: int

    def distance_to(self, other: "Position") -> int:
        return max(abs(self.x - other.x), abs(self.y - other.y))

    def offset(self, dx: int, dy: int) -> "Position":
        return Position(self.x + dx, self.y + dy)

    def to_dict(self) -> dict[str, int]:
        return {"x": self.x, "y": self.y}


@dataclass(slots=True)
class Stats:
    attack: int
    defense: int
    speed: int
    attack_range: int
    mana: float

    def to_dict(self) -> dict[str, float]:
        return {
            "attack": self.attack,
            "defense": self.defense,
            "speed": self.speed,
            "attack_range": self.attack_range,
            "mana": self.mana,
        }


class DamageRule(ABC):
    name = "abstract"

    @abstractmethod
    def calculate_damage(self, attack_power: float, defense: float) -> float:
        raise NotImplementedError


class SummaryDamageRule(DamageRule):
    """Implements the markdown rule under docs/."""

    name = "summary"

    def calculate_damage(self, attack_power: float, defense: float) -> float:
        if attack_power > defense:
            return 1.0
        gap = max(defense - attack_power + 1, 1)
        return 1 / (2 ** gap)


class SpreadsheetDamageRule(DamageRule):
    """Keeps the alternate Excel damage rule available as a strategy."""

    name = "spreadsheet"

    def calculate_damage(self, attack_power: float, defense: float) -> float:
        if attack_power > defense:
            return 1.0
        gap = max((defense - attack_power) * 2, 1)
        return 1 / gap


class BattleComponent(ABC):
    """Common hook surface shared by skills, traits, and statuses."""

    kind = "component"
    all_skills_pierce_shields = False
    ignores_incoming_piercing = False
    separate_basic_attack_windows = False

    def limit_hp_loss(self, amount: float) -> float:
        """Pure final-loss limit; may reduce, never amplify, an individual loss."""
        return amount

    def __init__(self, name: str, description: str = "") -> None:
        self.name = name
        self.description = description
        self.owner: Optional["Unit"] = None
        self.component_id = f"cmp-{next(_id_counter)}"

    def bind(self, owner: "Unit") -> "BattleComponent":
        self.owner = owner
        return self

    def sync_linked_state(self, battle: "Battle") -> None:
        return None

    def blocks_direct_effects(self) -> bool:
        return False

    def blocks_skill_non_damage_effects(self) -> bool:
        return False

    def grants_magic_immunity_to(self, battle: "Battle", unit: "Unit") -> bool:
        return False

    def projected_trait_for(self, battle: "Battle", unit: "Unit") -> Optional["Trait"]:
        return None

    def grants_block_counter(self, battle: "Battle", unit: "Unit") -> bool:
        return False

    def allied_basic_heal_amount(self, battle: "Battle", actor: "Unit", target: "Unit") -> float:
        return 0.0

    def resolve_allied_basic_support(self, battle: "Battle", actor: "Unit", target: "Unit") -> bool:
        return False

    def modify_stat(self, stat_name: str, value: float) -> float:
        return value

    def modify_attack_actions_per_turn(self, value: int) -> int:
        return value

    def can_use_basic_attack(self, battle: "Battle", actor: "Unit", payload: dict[str, Any]) -> tuple[bool, str]:
        return True, ""

    def modify_targeting_range(self, value: int) -> int:
        return value

    def ignores_direct_unit_target_line(self, battle: "Battle", actor: "Unit") -> bool:
        return False

    def modify_normal_move_distance(self, value: int) -> int:
        return value

    def modify_normal_move_actions_per_turn(self, value: int) -> int:
        return value

    def modify_skill_mana_cost(
        self,
        battle: "Battle",
        actor: "Unit",
        skill: "Skill",
        payload: Optional[dict[str, Any]],
        cost: float,
    ) -> float:
        return cost

    def allows_split_normal_movement(self, battle: "Battle", actor: "Unit") -> bool:
        return False

    def normal_movement_step_cost(
        self,
        battle: "Battle",
        unit: "Unit",
        start: "Position",
        end: "Position",
        current_cost: int,
    ) -> int:
        return current_cost

    def on_owner_turn_start(self, battle: "Battle") -> None:
        return None

    def on_owner_turn_end(self, battle: "Battle") -> None:
        return None

    def on_any_turn_start(self, battle: "Battle", active_unit: "Unit") -> None:
        return None

    def on_any_turn_end(self, battle: "Battle", ended_player_id: int) -> None:
        return None

    def on_targeted(self, battle: "Battle", ctx: "TargetContext") -> None:
        return None

    def on_owner_action_declared(
        self,
        battle: "Battle",
        action_type: str,
        payload: dict[str, Any],
    ) -> None:
        return None

    def on_basic_attack_finished(
        self,
        battle: "Battle",
        actor: "Unit",
        payload: dict[str, Any],
        damage_contexts: list["DamageContext"],
        missed: bool,
    ) -> None:
        return None

    def on_target_action_declared(self, battle: "Battle", actor: "Unit", action_type: str, payload: dict[str, Any]) -> None:
        return None

    def on_unit_moved(self, battle: "Battle", ctx: "MoveContext") -> None:
        return None

    def on_before_normal_move(self, battle: "Battle", ctx: "MoveContext") -> None:
        """Pay validated normal movement costs before moving or entering effects."""
        return None

    def on_before_damage(self, battle: "Battle", ctx: "DamageContext") -> None:
        return None

    def on_final_damage(self, battle: "Battle", ctx: "DamageContext") -> None:
        """React after ordinary defenses, immediately before HP loss."""
        return None

    def on_after_damage(self, battle: "Battle", ctx: "DamageContext") -> None:
        return None

    def on_battle_damage(self, battle: "Battle", ctx: "DamageContext") -> None:
        """Observe each actual HP loss once, before local after-damage callbacks."""
        return None

    def lethal_damage_remaining_hp(self, battle: "Battle", ctx: "DamageContext") -> Optional[float]:
        """Offer retained HP only after all defenses and loss limits pass."""
        return None

    def runtime_children(self) -> Iterable["BattleComponent"]:
        return ()

    def provided_components(self) -> Iterable["BattleComponent"]:
        """Temporary capabilities owned by this component, active only while it is present."""
        return ()

    def accepts_status(self, status: "StatusEffect") -> bool:
        return True

    def modify_received_stat_change(self, status: "BattleComponent", stat_name: str, before: float, after: float) -> float:
        return after

    def on_owner_banished(self, battle: "Battle") -> None:
        return None

    def on_damage_cancelled(self, battle: "Battle", ctx: "DamageContext") -> None:
        return None

    def permits_immune_heal(self, ctx: "HealContext") -> bool:
        """Only an explicitly identified component may exempt its own healing."""
        return False

    def on_before_heal(self, battle: "Battle", ctx: "HealContext") -> None:
        return None

    def on_after_heal(self, battle: "Battle", ctx: "HealContext") -> None:
        return None

    def on_owner_removed(self, battle: "Battle") -> None:
        return None

    def on_owned_summon_destroyed(self, battle: "Battle", summon: "Unit") -> None:
        return None

    def on_enter_battle(self, battle: "Battle") -> None:
        return None

    def on_destroyed_hero_count_changed(self, battle: "Battle", count: int) -> None:
        return None

    def on_removed(self, battle: "Battle") -> None:
        return None

    def blocks_skill_use(
        self,
        battle: "Battle",
        actor: "Unit",
        skill: "Skill",
    ) -> tuple[bool, str]:
        return False, ""

    def can_attack_target(
        self,
        battle: "Battle",
        actor: "Unit",
        target: "Unit",
    ) -> tuple[bool, str]:
        return True, ""

    def can_attack_target_with_payload(
        self,
        battle: "Battle",
        actor: "Unit",
        target: "Unit",
        payload: Optional[dict[str, Any]] = None,
    ) -> tuple[bool, str]:
        return True, ""

    def basic_attack_action_entries(
        self,
        battle: "Battle",
        actor: "Unit",
    ) -> list[dict[str, Any]]:
        return []

    def basic_attack_preview(
        self,
        battle: "Battle",
        actor: "Unit",
        payload: Optional[dict[str, Any]] = None,
    ) -> Optional[dict[str, Any]]:
        return None

    def basic_attack_payload_metadata(
        self,
        battle: "Battle",
        actor: "Unit",
        payload: Optional[dict[str, Any]] = None,
    ) -> dict[str, Any]:
        return {}

    def basic_attack_origins(self, battle: "Battle", actor: "Unit", payload: Optional[dict[str, Any]]) -> Optional[list["Position"]]:
        return None

    def before_basic_attack_resolution(self, battle: "Battle", actor: "Unit", payload: dict[str, Any]) -> bool:
        return True

    def forced_normal_move_paths(self, battle: "Battle", actor: "Unit") -> Optional[list[list["Position"]]]:
        return None

    def basic_attack_area_cells(
        self,
        battle: "Battle",
        actor: "Unit",
        payload: Optional[dict[str, Any]] = None,
    ) -> Optional[list["Position"]]:
        return None

    def basic_attack_area_affects_allies(
        self,
        battle: "Battle",
        actor: "Unit",
        payload: Optional[dict[str, Any]] = None,
    ) -> bool:
        return False

    def allows_block_counter(self, battle: "Battle", actor: "Unit") -> bool:
        return True

    def to_public_dict(self, battle: "Battle") -> dict[str, Any]:
        return {
            "id": self.component_id,
            "name": self.name,
            "description": self.description,
            "kind": self.kind,
        }


class Trait(BattleComponent):
    kind = "trait"


class StatusEffect(BattleComponent):
    kind = "status"

    def __init__(
        self,
        name: str,
        description: str = "",
        *,
        duration: Optional[int] = None,
        tick_scope: Literal["owner_turn_start", "owner_turn_end", "any_turn_end"] = "owner_turn_end",
    ) -> None:
        super().__init__(name=name, description=description)
        self.duration = duration
        self.tick_scope = tick_scope

    def decrement(self, battle: "Battle") -> None:
        if self.duration is None:
            return
        self.duration -= 1
        if self.duration <= 0 and self.owner is not None:
            self.owner.remove_status(self, battle)

    def on_owner_turn_start(self, battle: "Battle") -> None:
        if self.tick_scope == "owner_turn_start":
            self.decrement(battle)

    def on_owner_turn_end(self, battle: "Battle") -> None:
        if self.tick_scope == "owner_turn_end":
            self.decrement(battle)

    def on_any_turn_end(self, battle: "Battle", ended_player_id: int) -> None:
        if self.tick_scope == "any_turn_end":
            self.decrement(battle)

    def to_public_dict(self, battle: "Battle") -> dict[str, Any]:
        data = super().to_public_dict(battle)
        data["duration"] = self.duration
        return data


class BattleFieldEffect(BattleComponent):
    kind = "field"

    def blocks_action_effect(self, battle: "Battle", unit: "Unit", queued_action: "QueuedAction") -> bool:
        return False

    def on_after_damage_modifiers(self, battle: "Battle", ctx: "DamageContext") -> None:
        return None

    def on_after_target_modifiers(self, battle: "Battle", ctx: "TargetContext") -> None:
        return None

    def blocks_unit_movement(self, battle: "Battle", unit: "Unit") -> bool:
        return False

    def modify_unit_stat(self, battle: "Battle", unit: "Unit", stat_name: str, value: float) -> float:
        return value

    def on_unit_entered(self, battle: "Battle", unit: "Unit") -> None:
        return None

    def __init__(
        self,
        name: str,
        description: str = "",
        *,
        duration: Optional[int] = None,
    ) -> None:
        super().__init__(name=name, description=description)
        self.duration = duration

    def merge_into_existing(
        self,
        battle: "Battle",
        existing_effects: list["BattleFieldEffect"],
    ) -> bool:
        return False

    def on_any_turn_end(self, battle: "Battle", ended_player_id: int) -> None:
        if self.duration is None:
            return
        self.duration -= 1
        if self.duration <= 0:
            battle.remove_field_effect(self)

    def on_turn_start(self, battle: "Battle", active_unit: Optional["Unit"]) -> None:
        return None

    def blocks_forced_movement(self, battle: "Battle", position: "Position") -> bool:
        return False

    def affected_cells(self, battle: "Battle") -> list["Position"]:
        return []

    def board_marker(self, battle: "Battle") -> str:
        return self.name[:2]

    def to_public_dict(self, battle: "Battle") -> dict[str, Any]:
        data = super().to_public_dict(battle)
        data["duration"] = self.duration
        data["cells"] = [cell.to_dict() for cell in self.affected_cells(battle)]
        data["board_marker"] = self.board_marker(battle)
        weather_name = getattr(self, "weather_name", None)
        if weather_name is not None:
            data["weather_name"] = weather_name
            data["weather_owner_player_id"] = getattr(self, "weather_owner_player_id", None)
        return data


class TemporaryDefenseStatus(StatusEffect):
    def __init__(
        self,
        name: str,
        defense_delta: float,
        description: str,
        *,
        expire_with_chain: bool = False,
    ) -> None:
        super().__init__(name, description, duration=1, tick_scope="owner_turn_end")
        self.defense_delta = defense_delta
        self.expire_with_chain = expire_with_chain

    def modify_stat(self, stat_name: str, value: float) -> float:
        if stat_name == "defense":
            return value + self.defense_delta
        return value

    def on_after_damage(self, battle: "Battle", ctx: "DamageContext") -> None:
        if self.owner is None:
            return
        if ctx.target.unit_id != self.owner.unit_id:
            return
        self.owner.remove_status(self, battle)


class Skill(BattleComponent, ABC):
    excludes_caster_from_effect = False
    excludes_allies_from_effect = False

    kind = "skill"
    requires_direct_unit_target_line = True
    target_unit_is_anchor = False
    target_cells_are_geometry_only = False
    selection_cells_are_effect_cells = True
    resolves_from_current_position = False

    def __init__(
        self,
        code: str,
        name: str,
        description: str,
        *,
        mana_cost: float = 0.0,
        cooldown_turns: int = 0,
        max_uses_per_turn: Optional[int] = None,
        max_uses_per_battle: Optional[int] = None,
        target_mode: Literal["none", "self", "ally", "enemy", "cell", "unit"] = "none",
        passive: bool = False,
        timing: Literal["active", "passive", "instant", "reaction"] = "active",
        direction_mode: Literal["none", "optional", "required"] = "none",
    ) -> None:
        super().__init__(name=name, description=description)
        self.code = code
        self.mana_cost = mana_cost
        self.cooldown_turns = cooldown_turns
        self.max_uses_per_turn = max_uses_per_turn
        self.max_uses_per_battle = max_uses_per_battle
        self.target_mode = target_mode
        self.passive = passive
        self.timing = "passive" if passive and timing == "active" else timing
        self.direction_mode = direction_mode
        self.uses_this_turn = 0
        self.uses_this_battle = 0
        self.cooldown_remaining = 0
        self._uses_turn_number: Optional[int] = None

    @property
    def is_active(self) -> bool:
        return self.timing == "active"

    @property
    def is_reaction(self) -> bool:
        return self.timing in {"passive", "instant", "reaction"}

    @property
    def chain_speed(self) -> int:
        mapping = {
            "active": 1,
            "passive": 2,
            "reaction": 2,
            "instant": 3,
        }
        return mapping[self.timing]

    def can_use(
        self,
        battle: "Battle",
        actor: "Unit",
        payload: Optional[dict[str, Any]] = None,
    ) -> tuple[bool, str]:
        self.sync_turn_scope(battle)
        if self.timing in {"passive", "reaction"}:
            return False, "该技能需要在连锁时使用。"
        can_use_as_instant = self.timing == "instant" and actor.player_id != battle.active_player
        if not can_use_as_instant and not actor.can_take_turn_actions(battle):
            return False, "这个单位当前不能行动。"
        if not can_use_as_instant and actor.player_id != battle.active_player:
            return False, "还没有轮到这个单位行动。"
        if actor.banished:
            return False, "该单位暂时不在战场上。"
        if actor.cannot_use_skills:
            return False, "这个单位当前不能使用技能。"
        for effect in ([] if actor.direct_effects_blocked() else list(battle.field_effects)):
            blocked, reason = effect.blocks_skill_use(battle, actor, self)
            if blocked:
                return False, reason
        for component in list(actor.iter_components()):
            blocked, reason = component.blocks_skill_use(battle, actor, self)
            if blocked:
                return False, reason
        if self.cooldown_remaining > 0 and not self.allows_additional_use_during_cooldown(battle, actor):
            return False, f"还需冷却 {self.cooldown_remaining} 个己方回合。"
        if self.max_uses_per_turn is not None and self.uses_this_turn >= self.max_uses_per_turn:
            return False, "本回合使用次数已满。"
        if self.max_uses_per_battle is not None and self.uses_this_battle >= self.max_uses_per_battle:
            return False, "本场战斗使用次数已满。"
        if actor.current_mana + 1e-9 < self.mana_cost_for_payload(battle, actor, payload):
            return False, "魔力不足。"
        block_reason = battle.shared_stealth_action_block_reason(actor)
        if block_reason:
            return False, block_reason
        return True, ""

    def allows_additional_use_during_cooldown(self, battle: "Battle", actor: "Unit") -> bool:
        return False

    def on_owner_turn_start(self, battle: "Battle") -> None:
        self.sync_turn_scope(battle)

    def on_any_turn_end(self, battle: "Battle", ended_player_id: int) -> None:
        # Cooldowns are rounds of the owning hero, not turns of every ally/enemy.
        if self.cooldown_remaining > 0 and battle.unit_belongs_to_current_turn(self.owner):
            self.cooldown_remaining -= 1

    def sync_turn_scope(self, battle: "Battle") -> None:
        current_turn_number = int(getattr(battle, "turn_number", 1) or 1)
        if self._uses_turn_number == current_turn_number:
            return
        self._uses_turn_number = current_turn_number
        self.uses_this_turn = 0

    def mana_cost_for_payload(
        self,
        battle: "Battle",
        actor: "Unit",
        payload: Optional[dict[str, Any]] = None,
    ) -> float:
        cost = float(self.mana_cost)
        if self.is_reaction and battle.pending_chain is not None:
            multiplier = float(battle.pending_chain.queued_action.payload.get("reaction_mana_multiplier", 1.0) or 1.0)
            cost *= max(0.0, multiplier)
        for component in list(actor.iter_components()):
            if component is self:
                continue
            cost = component.modify_skill_mana_cost(battle, actor, self, payload, cost)
        return max(0.0, float(cost))

    def mana_cost_text(self) -> Optional[str]:
        return None

    def direct_unit_target_range(
        self,
        battle: "Battle",
        actor: "Unit",
        payload: Optional[dict[str, Any]] = None,
    ) -> int:
        return actor.targeting_range()

    def prepay_resources(
        self,
        battle: "Battle",
        actor: "Unit",
        payload: Optional[dict[str, Any]] = None,
    ) -> None:
        self.sync_turn_scope(battle)
        actor.spend_mana(self.mana_cost_for_payload(battle, actor, payload))
        self.uses_this_turn += 1
        self.uses_this_battle += 1
        if self.cooldown_turns:
            self.cooldown_remaining = self.cooldown_turns

    def finalize_use(self, battle: "Battle", actor: "Unit") -> None:
        if self.timing == "active":
            actor.performed_active_skill = True

    def spend_resources(
        self,
        battle: "Battle",
        actor: "Unit",
        payload: Optional[dict[str, Any]] = None,
    ) -> None:
        self.prepay_resources(battle, actor, payload)
        self.finalize_use(battle, actor)

    @abstractmethod
    def execute(self, battle: "Battle", actor: "Unit", payload: dict[str, Any]) -> None:
        raise NotImplementedError

    def can_react_to(
        self,
        battle: "Battle",
        actor: "Unit",
        queued_action: "QueuedAction",
    ) -> tuple[bool, str]:
        self.sync_turn_scope(battle)
        if not self.is_reaction:
            return False, "不是连锁技能。"
        if actor.banished or not actor.alive:
            return False, "单位不在战场上。"
        if actor.cannot_use_skills:
            return False, "这个单位当前不能使用技能。"
        for effect in ([] if actor.direct_effects_blocked() else list(battle.field_effects)):
            blocked, reason = effect.blocks_skill_use(battle, actor, self)
            if blocked:
                return False, reason
        for component in list(actor.iter_components()):
            blocked, reason = component.blocks_skill_use(battle, actor, self)
            if blocked:
                return False, reason
        if queued_action.speed >= self.chain_speed:
            return False, "连锁速度不够快。"
        if self.cooldown_remaining > 0:
            return False, "技能冷却中。"
        if self.max_uses_per_turn is not None and self.uses_this_turn >= self.max_uses_per_turn:
            return False, "本回合使用次数已满。"
        if self.max_uses_per_battle is not None and self.uses_this_battle >= self.max_uses_per_battle:
            return False, "本场战斗使用次数已满。"
        if actor.current_mana + 1e-9 < self.mana_cost:
            return False, "魔力不足。"
        block_reason = battle.shared_stealth_action_block_reason(actor)
        if block_reason:
            return False, block_reason
        return True, ""

    def can_react_with_payload(
        self,
        battle: "Battle",
        actor: "Unit",
        queued_action: "QueuedAction",
        payload: Optional[dict[str, Any]] = None,
    ) -> tuple[bool, str]:
        ok, reason = self.can_react_to(battle, actor, queued_action)
        if not ok:
            return ok, reason
        if actor.current_mana + 1e-9 < self.mana_cost_for_payload(battle, actor, payload):
            return False, "魔力不足。"
        return True, ""

    def react(
        self,
        battle: "Battle",
        actor: "Unit",
        payload: dict[str, Any],
        queued_action: "QueuedAction",
    ) -> None:
        self.execute(battle, actor, payload)

    def on_target_missed(self, battle: "Battle", actor: "Unit", payload: dict[str, Any]) -> None:
        """Resolve an explicit miss benefit after the declared cell becomes empty."""
        return None

    def preview(self, battle: "Battle", actor: "Unit") -> dict[str, Any]:
        return {
            "cells": [],
            "target_unit_ids": [],
            "secondary_cells": [],
            "secondary_target_unit_ids": [],
            "requires_target": self.target_mode in {"ally", "enemy", "cell", "unit"},
        }

    def reaction_preview(
        self,
        battle: "Battle",
        actor: "Unit",
        queued_action: "QueuedAction",
    ) -> dict[str, Any]:
        return {"cells": [], "target_unit_ids": [], "secondary_cells": [], "requires_target": False}

    def reaction_window_timing(
        self,
        battle: "Battle",
        actor: "Unit",
        payload: dict[str, Any],
    ) -> Literal["before", "after"]:
        return "before"

    def target_can_react_to_effect(self, battle: "Battle", actor: "Unit", target: "Unit", payload: dict[str, Any]) -> bool:
        return True

    def get_target_units_for_payload(
        self,
        battle: "Battle",
        actor: "Unit",
        payload: dict[str, Any],
    ) -> list["Unit"]:
        if self.target_mode in {"ally", "enemy", "unit"} and payload.get("target_unit_id"):
            return [battle.get_unit(payload["target_unit_id"])]
        return []

    def get_target_cells_for_payload(
        self,
        battle: "Battle",
        actor: "Unit",
        payload: dict[str, Any],
    ) -> list["Position"]:
        if self.target_mode in {"ally", "enemy", "unit"} and payload.get("target_unit_id"):
            target = battle.get_unit(payload["target_unit_id"])
            return battle.unit_cells(target)
        if self.target_mode == "cell" and payload.get("x") is not None and payload.get("y") is not None:
            return [Position(int(payload["x"]), int(payload["y"]))]
        if self.target_mode == "self" and actor.position is not None:
            return battle.unit_cells(actor)
        return []

    def ignores_shield_for_payload(
        self,
        battle: "Battle",
        actor: "Unit",
        payload: dict[str, Any],
    ) -> bool:
        return any(component.all_skills_pierce_shields for component in actor.iter_components())

    def half_ignores_shield_for_payload(
        self,
        battle: "Battle",
        actor: "Unit",
        payload: dict[str, Any],
    ) -> bool:
        return False

    def ignores_stealth_for_payload(
        self,
        battle: "Battle",
        actor: "Unit",
        payload: dict[str, Any],
    ) -> bool:
        return False

    def ignores_magic_immunity_for_payload(
        self, battle: "Battle", actor: "Unit", payload: dict[str, Any]
    ) -> bool:
        return False

    def cannot_evade_for_payload(
        self,
        battle: "Battle",
        actor: "Unit",
        payload: dict[str, Any],
    ) -> bool:
        return False

    def queued_payload_metadata(
        self,
        battle: "Battle",
        actor: "Unit",
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        """Trusted metadata copied onto this skill's queued action."""
        return {}

    def build_wrapped_action(self, battle: "Battle", actor: "Unit", payload: dict[str, Any]) -> Optional["QueuedAction"]:
        return None

    def queued_reaction_payload_metadata(self, battle: "Battle", actor: "Unit", payload: dict[str, Any]) -> dict[str, Any]:
        return {}

    def to_public_dict(self, battle: "Battle") -> dict[str, Any]:
        self.sync_turn_scope(battle)
        owner = self.owner
        effective_mana_cost = self.mana_cost
        if owner is not None:
            effective_mana_cost = self.mana_cost_for_payload(battle, owner, {})
        data = super().to_public_dict(battle)
        data.update(
            {
                "code": self.code,
                "mana_cost": effective_mana_cost,
                "base_mana_cost": self.mana_cost,
                "mana_cost_text": self.mana_cost_text(),
                "cooldown_turns": self.cooldown_turns,
                "cooldown_remaining": self.cooldown_remaining,
                "max_uses_per_turn": self.max_uses_per_turn,
                "max_uses_per_battle": self.max_uses_per_battle,
                "target_mode": self.target_mode,
                "passive": self.passive,
                "timing": self.timing,
                "chain_speed": self.chain_speed,
                "direction_mode": self.direction_mode,
                "uses_this_turn": self.uses_this_turn,
                "uses_this_battle": self.uses_this_battle,
            }
        )
        return data


@dataclass(slots=True)
class MoveContext:
    unit: "Unit"
    start: Position
    end: Position
    path: list[Position]
    via_skill: bool = False
    triggered_by_reaction: bool = False
    tags: set[str] = field(default_factory=set)


@dataclass(slots=True)
class TargetContext:
    actor: "Unit"
    target: "Unit"
    action_name: str
    is_skill: bool
    is_hostile: bool
    ignore_shield: bool = False
    half_ignore_shield: bool = False
    ignore_magic_immunity: bool = False
    ignore_physical_immunity: bool = False
    from_field_effect: bool = False
    cannot_evade: bool = False
    shield_consumed: bool = False
    damage_target: bool = False
    cancelled: bool = False
    reason: str = ""
    tags: set[str] = field(default_factory=set)
    destroyed_as_clone: bool = False


@dataclass(slots=True)
class DamageContext:
    source: Optional["Unit"]
    target: "Unit"
    attack_power: float
    is_skill: bool
    action_name: str
    ignore_shield: bool = False
    half_ignore_shield: bool = False
    ignore_magic_immunity: bool = False
    ignore_physical_immunity: bool = False
    siege_shell: bool = False
    from_field_effect: bool = False
    cannot_evade: bool = False
    area_cell_hits: int = 1
    raw_damage: Optional[float] = None
    shield_consumed: bool = False
    cancelled: bool = False
    preserve_followup_effects: bool = False
    reason: str = ""
    lethal: bool = False
    destroyed_as_clone: bool = False
    tags: set[str] = field(default_factory=set)
    actual_damage: float = 0.0
    declared_source_attack: Optional[float] = None
    modifier_receipts: set[str] = field(default_factory=set)

    @property
    def damage(self) -> float:
        if self.destroyed_as_clone:
            return 0.0
        if self.lethal:
            return float(self.raw_damage or 1.0)
        return float(self.raw_damage or 0.0)


@dataclass(slots=True)
class HealContext:
    source: Optional["Unit"]
    target: "Unit"
    amount: float
    action_name: str
    cancelled: bool = False
    reason: str = ""
    tags: set[str] = field(default_factory=set)
    effect_source: Optional[BattleComponent] = None


@dataclass(slots=True)
class VisualEvent:
    event_id: int
    kind: Literal["attack", "skill", "defense"]
    display_name: str
    actor_id: Optional[str] = None
    actor_player_id: Optional[int] = None
    action_type: str = ""
    action_code: str = ""
    target_unit_ids: list[str] = field(default_factory=list)
    target_cells: list[Position] = field(default_factory=list)
    source_cell: Optional[Position] = None
    defense_reason: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_public_dict(self) -> dict[str, Any]:
        return {
            "id": self.event_id,
            "kind": self.kind,
            "display_name": self.display_name,
            "actor_id": self.actor_id,
            "actor_player_id": self.actor_player_id,
            "action_type": self.action_type,
            "action_code": self.action_code,
            "target_unit_ids": list(self.target_unit_ids),
            "target_cells": [cell.to_dict() for cell in self.target_cells],
            "source_cell": self.source_cell.to_dict() if self.source_cell is not None else None,
            "defense_reason": self.defense_reason,
            "metadata": dict(self.metadata),
        }


@dataclass(slots=True)
class QueuedAction:
    action_type: Literal["move", "attack", "skill", "skill_effect", "reaction_skill", "reaction_action"]
    actor_id: str
    display_name: str
    speed: int
    payload: dict[str, Any]
    description: str = ""
    target_unit_ids: list[str] = field(default_factory=list)
    target_cells: list[Position] = field(default_factory=list)
    source_player_id: Optional[int] = None
    hostile: bool = False
    reaction_source_id: Optional[str] = None
    suppress_logs: bool = False
    effect_resolver: Optional[Callable[["Battle", "Unit", "QueuedAction"], None]] = None

    def to_public_dict(self) -> dict[str, Any]:
        return {
            "action_type": self.action_type,
            "actor_id": self.actor_id,
            "display_name": self.display_name,
            "speed": self.speed,
            "payload": {key: value for key, value in self.payload.items() if key != "separate_attack_segments"},
            "description": self.description,
            "target_unit_ids": self.target_unit_ids,
            "target_cells": [cell.to_dict() for cell in self.target_cells],
            "source_player_id": self.source_player_id,
            "hostile": self.hostile,
            "reaction_source_id": self.reaction_source_id,
        }


@dataclass(slots=True)
class ReactionOption:
    unit_id: str
    action_code: str
    action_name: str
    action_type: Literal["skill", "reaction_action"]
    timing: str
    chain_speed: int
    description: str
    preview: dict[str, Any] = field(default_factory=dict)

    def to_public_dict(self) -> dict[str, Any]:
        return {
            "unit_id": self.unit_id,
            "action_code": self.action_code,
            "action_name": self.action_name,
            "action_type": self.action_type,
            "timing": self.timing,
            "chain_speed": self.chain_speed,
            "description": self.description,
            "preview": self.preview,
        }


@dataclass(slots=True)
class ReactionWindow:
    reactive_player_id: int
    queued_action: QueuedAction
    pending_reactor_ids: list[str]
    options_by_unit: dict[str, list[ReactionOption]]
    chosen_reactions: list[QueuedAction] = field(default_factory=list)
    decision_log: list[str] = field(default_factory=list)

    def current_unit_id(self) -> Optional[str]:
        return self.pending_reactor_ids[0] if self.pending_reactor_ids else None

    def to_public_dict(self, battle: "Battle") -> dict[str, Any]:
        return {
            "reactive_player_id": self.reactive_player_id,
            "queued_action": self.queued_action.to_public_dict(),
            "queued_action_effect_summary": battle.queued_action_effect_summary(self.queued_action),
            "pending_reactor_ids": self.pending_reactor_ids,
            "current_unit_id": self.current_unit_id(),
            "options_by_unit": {
                unit_id: [option.to_public_dict() for option in options]
                for unit_id, options in self.options_by_unit.items()
            },
            "chosen_reactions": [action.to_public_dict() for action in self.chosen_reactions],
            "decision_log": self.decision_log,
        }


@dataclass(slots=True)
class RespawnPrompt:
    unit_id: str
    player_id: int
    origin: Position
    options: list[Position]

    def to_public_dict(self) -> dict[str, Any]:
        return {
            "unit_id": self.unit_id,
            "player_id": self.player_id,
            "origin": self.origin.to_dict(),
            "options": [cell.to_dict() for cell in self.options],
        }


class Unit(ABC):
    def __getstate__(self) -> dict[str, Any]:
        state = dict(self.__dict__)
        state.pop("_battle_ref", None)
        return state

    def __init__(
        self,
        *,
        unit_id: str,
        player_id: int,
        name: str,
        title: str,
        role: str,
        attribute: str,
        race: str,
        level: int,
        base_stats: Stats,
        raw_skill_text: str,
        raw_trait_text: str,
        max_health: float = 1.0,
        is_summon: bool = False,
        is_clone: bool = False,
    ) -> None:
        self.unit_id = unit_id
        self.player_id = player_id
        self.name = name
        self.title = title
        self.role = role
        self.attribute = attribute
        self.race = race
        self.level = level
        self.base_stats = Stats(
            attack=base_stats.attack,
            defense=base_stats.defense,
            speed=base_stats.speed,
            attack_range=base_stats.attack_range,
            mana=base_stats.mana,
        )
        self.current_hp = max_health
        self.max_health = max_health
        self.current_mana = base_stats.mana
        self.mana_points = 0.0
        self.position: Optional[Position] = None
        self.last_position: Optional[Position] = None
        self.footprint_width = max(1, int(getattr(self, "footprint_width", 1)))
        self.footprint_height = max(1, int(getattr(self, "footprint_height", 1)))
        configured_stat_minimums = dict(getattr(self, "stat_minimums", {}))
        self.stat_minimums = {
            "attack": float(configured_stat_minimums.get("attack", 1.0)),
            "defense": float(configured_stat_minimums.get("defense", 1.0)),
            "speed": float(configured_stat_minimums.get("speed", 1.0)),
            "attack_range": float(configured_stat_minimums.get("attack_range", 1.0)),
        }
        base_offsets = getattr(self, "base_footprint_offsets", None)
        if base_offsets is None:
            base_offsets = [
                (dx, dy)
                for dx in range(self.footprint_width)
                for dy in range(self.footprint_height)
            ]
        self.base_footprint_offsets = self._normalize_footprint_offsets(base_offsets)
        current_offsets = getattr(self, "footprint_offsets", None)
        self.footprint_offsets = self._normalize_footprint_offsets(current_offsets or self.base_footprint_offsets)
        self._refresh_footprint_bounds()
        self.alive = True
        self.banished = False
        self.banish_return_position: Optional[Position] = None
        self.banish_turns_remaining = 0
        self.shields = 0
        self.temporary_shields = 0
        self.dodge_charges = 0
        self.magic_immunity = False
        self.physical_immunity = False
        self.cannot_be_targeted = False
        self.cannot_move = False
        self.cannot_normal_move = False
        self.cannot_heal = False
        self.cannot_attack = is_clone
        self.cannot_use_skills = is_clone
        self.allow_overheal = False
        self.ignore_units_while_moving = False
        self.allow_enemy_destination_overlap = False
        self.has_flying = False
        self.has_block_counter = False
        self.is_summon = is_summon
        self.is_clone = is_clone
        self.summoner_id: Optional[str] = None
        self.is_mount = False
        self.mount_owner_id: Optional[str] = None
        self.mounted_on_unit_id: Optional[str] = None
        self.ridden_by_unit_id: Optional[str] = None
        self.can_act_on_entry_turn = False
        self.turn_ready = True
        self.move_used = False
        self.normal_move_steps_used = 0
        self.normal_move_actions_used = 0
        self.attacks_used = 0
        self.performed_active_skill = False
        self.moved_this_turn = False
        self.actions_taken_this_turn: list[str] = []
        self.base_attack_actions_per_turn = 1
        self.allow_unbounded_mana = False
        self.raw_skill_text = raw_skill_text
        self.raw_trait_text = raw_trait_text
        self.skills: list[Skill] = [skill.bind(self) for skill in self.build_skills()]
        self.traits: list[Trait] = [trait.bind(self) for trait in self.build_traits()]
        self.statuses: list[StatusEffect] = []

    @abstractmethod
    def build_skills(self) -> list[Skill]:
        raise NotImplementedError

    @abstractmethod
    def build_traits(self) -> list[Trait]:
        raise NotImplementedError

    def projected_traits(self) -> Iterable[Trait]:
        battle_ref = getattr(self, "_battle_ref", None)
        battle = battle_ref() if battle_ref is not None else None
        if battle is None or not (self.is_summon or self.is_clone) or not self.alive or self.banished or self.position is None:
            return
        seen: set[str] = set()
        for source in battle.all_units():
            if not source.alive or source.banished or source.position is None:
                continue
            for trait in source.traits:
                projected = trait.projected_trait_for(battle, self)
                if projected is not None and projected.name not in seen:
                    seen.add(projected.name)
                    yield projected

    def iter_components(self) -> Iterable[BattleComponent]:
        yield from self.skills
        yield from self.traits
        seen_skills = {id(skill) for skill in self.skills}
        for status in self.statuses:
            yield status
            for component in status.provided_components():
                if isinstance(component, Skill):
                    if id(component) in seen_skills:
                        continue
                    seen_skills.add(id(component))
                yield component
        for skill in getattr(self, "_triggered_skill_definitions", {}).values():
            if id(skill) not in seen_skills:
                yield skill
        yield from self.projected_traits()

    def action_skills(self) -> list[Skill]:
        skills = list(self.skills)
        seen = {skill.code for skill in skills}
        for status in self.statuses:
            for component in status.provided_components():
                if isinstance(component, Skill) and component.code not in seen:
                    skills.append(component)
                    seen.add(component.code)
        return skills

    def skill_map(self) -> dict[str, Skill]:
        return {skill.code: skill for skill in self.action_skills()}

    def get_skill(self, code: str) -> Skill:
        for component in self.iter_components():
            if isinstance(component, Skill) and component.code == code:
                return component
        raise ActionError(f"{self.name} 没有技能 {code}")

    def add_status(self, status: StatusEffect, *, source: Optional["Unit"] = None) -> None:
        battle_ref = getattr(self, "_battle_ref", None)
        battle = battle_ref() if battle_ref is not None else None
        if source is None and battle is not None:
            source = battle.units.get(getattr(status, "source_unit_id", ""))
            action = battle.resolving_action
            if source is None and action is not None:
                source = battle.units.get(action.actor_id)
        if not hasattr(status, "is_skill_effect"):
            action = battle.resolving_action if battle is not None else None
            status.is_skill_effect = action is None or action.action_type in {"skill", "reaction_skill", "skill_effect"}
        if source is not None:
            status.source_player_id = source.player_id
        if any(not component.accepts_status(status) for component in self.iter_components()):
            return
        if (battle is not None and self.is_clone and getattr(status, "is_skill_effect", False)
                and source is not None and battle.destroy_clone_for_skill_effect(self, source=source, action_name=status.name)):
            return
        self.statuses.append(status.bind(self))

    def remove_status(self, status: StatusEffect, battle: Optional["Battle"] = None) -> None:
        if status in self.statuses:
            self.statuses.remove(status)
            if battle is not None:
                status.on_removed(battle)
            if battle is not None:
                battle.log(f"{self.name} 的状态【{status.name}】结束。")

    def has_status(self, name: str) -> bool:
        return any(status.name == name for status in self.statuses)

    def get_status(self, name: str) -> Optional[StatusEffect]:
        for status in self.statuses:
            if status.name == name:
                return status
        return None

    def is_stealthed(self) -> bool:
        return self.has_status("隐身")

    @staticmethod
    def _normalize_footprint_offsets(offsets: Iterable[tuple[int, int] | Position]) -> list[tuple[int, int]]:
        normalized: list[tuple[int, int]] = []
        seen: set[tuple[int, int]] = set()
        for offset in offsets:
            if isinstance(offset, Position):
                pair = (int(offset.x), int(offset.y))
            else:
                pair = (int(offset[0]), int(offset[1]))
            if pair in seen:
                continue
            seen.add(pair)
            normalized.append(pair)
        return normalized or [(0, 0)]

    def _refresh_footprint_bounds(self) -> None:
        xs = [dx for dx, _ in self.footprint_offsets]
        ys = [dy for _, dy in self.footprint_offsets]
        self.footprint_min_dx = min(xs)
        self.footprint_min_dy = min(ys)
        self.footprint_max_dx = max(xs)
        self.footprint_max_dy = max(ys)
        self.footprint_width = max(1, self.footprint_max_dx - self.footprint_min_dx + 1)
        self.footprint_height = max(1, self.footprint_max_dy - self.footprint_min_dy + 1)

    def set_footprint_offsets(self, offsets: Iterable[tuple[int, int] | Position]) -> None:
        self.footprint_offsets = self._normalize_footprint_offsets(offsets)
        self._refresh_footprint_bounds()

    def set_footprint_cells(self, cells: Iterable[Position]) -> None:
        if self.position is None:
            raise ActionError("单位不在战场上。")
        self.set_footprint_offsets((cell.x - self.position.x, cell.y - self.position.y) for cell in cells)

    def reset_footprint_to_base(self) -> None:
        self.set_footprint_offsets(self.base_footprint_offsets)

    def footprint_cells_at(self, position: Position) -> list[Position]:
        return [position.offset(dx, dy) for dx, dy in self.footprint_offsets]

    def footprint_cells(self) -> list[Position]:
        if self.position is None or not self.alive or self.banished:
            return []
        return self.footprint_cells_at(self.position)

    def notify_action_declared(
        self,
        battle: "Battle",
        action_type: str,
        payload: dict[str, Any],
    ) -> None:
        for effect in list(battle.field_effects):
            if self.direct_effects_blocked() and not getattr(effect, "observes_immune_actions", False):
                continue
            effect.on_owner_action_declared(battle, action_type, payload)
        for component in list(self.iter_components()):
            component.on_owner_action_declared(battle, action_type, payload)

    def notify_basic_attack_finished(
        self,
        battle: "Battle",
        payload: dict[str, Any],
        damage_contexts: list["DamageContext"],
        *,
        missed: bool = False,
    ) -> None:
        if getattr(battle, "_measuring_attack_success", False):
            battle._attack_probe_success = any(
                ctx.source is not None and ctx.source.unit_id == self.unit_id
                and not ctx.is_skill and not ctx.cancelled
                and (ctx.actual_damage > 0 or (ctx.destroyed_as_clone and not getattr(battle, "_attack_probe_hp_only", False)))
                for ctx in damage_contexts
            )
            return
        for component in list(self.iter_components()):
            component.on_basic_attack_finished(battle, self, payload, damage_contexts, missed)
        battle.notify_field_basic_attack_finished(self, payload, damage_contexts, missed=missed)

    def consume_attack_attempt_buffs(self, battle: "Battle") -> None:
        for status in list(self.statuses):
            if getattr(status, "consume_on_attack_attempt", False):
                self.remove_status(status, battle)

    def attack_actions_per_turn(self) -> int:
        value = self.base_attack_actions_per_turn
        for component in self.iter_components():
            value = component.modify_attack_actions_per_turn(value)
        return max(1, value)

    def normal_move_distance(self) -> int:
        value = int(self.stat("speed"))
        for component in self.iter_components():
            value = component.modify_normal_move_distance(value)
        return max(0, value)

    def normal_move_actions_per_turn(self) -> int:
        value = 1
        for component in self.iter_components():
            value = component.modify_normal_move_actions_per_turn(value)
        return max(1, int(value))

    def allows_split_normal_movement(self, battle: "Battle") -> bool:
        return any(component.allows_split_normal_movement(battle, self) for component in self.iter_components())

    def remaining_normal_move_distance(self, battle: "Battle") -> int:
        total = self.normal_move_distance()
        if not self.allows_split_normal_movement(battle):
            return 0 if self.normal_move_actions_used >= self.normal_move_actions_per_turn() else total
        return max(0, total - self.normal_move_steps_used)

    def total_shields(self) -> int:
        return self.shields + self.temporary_shields

    def add_temporary_shields(self, amount: int) -> None:
        if not self.direct_effects_blocked():
            self.temporary_shields += amount

    def consume_one_shield(self) -> bool:
        if self.temporary_shields > 0:
            self.temporary_shields -= 1
            return True
        if self.shields > 0:
            self.shields -= 1
            return True
        return False

    def clear_end_of_turn_shields(self) -> None:
        self.shields = 0
        self.temporary_shields = 0

    def max_mana(self) -> float:
        value = max(float(self.stat("mana")), 0.0)
        if self.allow_unbounded_mana:
            value = max(value, float(self.current_mana))
        return round(value, 2)

    def clamp_mana(self) -> None:
        self.current_mana = round(min(max(self.current_mana, 0.0), self.max_mana()), 2)

    def direct_effects_blocked(self) -> bool:
        return not getattr(self, "_paying_skill_cost", False) and any(component.blocks_direct_effects() for component in self.iter_components())

    def skill_non_damage_effects_blocked(self) -> bool:
        return any(component.blocks_skill_non_damage_effects() for component in self.iter_components())

    @property
    def magic_immunity(self) -> bool:
        if getattr(self, "_base_magic_immunity", False):
            return True
        battle_ref = getattr(self, "_battle_ref", None)
        battle = battle_ref() if battle_ref is not None else None
        if battle is None or not self.alive or self.banished or self.position is None:
            return False
        return any(
            trait.grants_magic_immunity_to(battle, self)
            for source in battle.all_units()
            if source.alive and not source.banished and source.position is not None
            for trait in source.traits
        )

    @magic_immunity.setter
    def magic_immunity(self, value: bool) -> None:
        self._base_magic_immunity = bool(value)

    def gain_mana(self, amount: float) -> float:
        if self.direct_effects_blocked():
            return 0.0
        before = self.current_mana
        self.current_mana = round(self.current_mana + amount, 2)
        self.clamp_mana()
        return round(self.current_mana - before, 2)

    def spend_mana(self, amount: float) -> float:
        if self.direct_effects_blocked():
            return 0.0
        if any(getattr(component, "prevents_mana_loss", False) for component in self.iter_components()):
            return 0.0
        before = self.current_mana
        self.current_mana = round(self.current_mana - amount, 2)
        self.clamp_mana()
        return round(before - self.current_mana, 2)

    def gain_mana_points(self, amount: float) -> float:
        if self.direct_effects_blocked():
            return 0.0
        before = self.mana_points
        self.mana_points = round(min(getattr(self, "max_mana_points", float("inf")), max(self.mana_points + amount, 0.0)), 2)
        return round(self.mana_points - before, 2)

    def drain_mana(self, amount: float) -> float:
        """Return the actual resource loss, distinct from paying a cost."""
        before = self.current_mana
        self.spend_mana(amount)
        return round(max(0.0, before - self.current_mana), 2)

    def spend_mana_points(self, amount: float) -> float:
        if self.direct_effects_blocked():
            return 0.0
        before = self.mana_points
        self.mana_points = round(max(self.mana_points - amount, 0.0), 2)
        return round(before - self.mana_points, 2)

    @property
    def cannot_move(self) -> bool:
        if getattr(self, "_cannot_move", False):
            return True
        battle_ref = getattr(self, "_battle_ref", None)
        battle = battle_ref() if battle_ref is not None else None
        return bool(battle is not None and any(
            effect.blocks_unit_movement(battle, self) for effect in battle.field_effects
        ))

    @cannot_move.setter
    def cannot_move(self, value: bool) -> None:
        self._cannot_move = bool(value)

    def stat(self, stat_name: Literal["attack", "defense", "speed", "attack_range", "mana"]) -> float:
        base_value = getattr(self.base_stats, stat_name)
        value = float(base_value)
        for component in self.iter_components():
            before = value
            value = component.modify_stat(stat_name, value)
            for trait in self.traits:
                value = trait.modify_received_stat_change(component, stat_name, before, value)
        battle_ref = getattr(self, "_battle_ref", None)
        battle = battle_ref() if battle_ref is not None else None
        if battle is not None:
            for effect in battle.field_effects:
                value = effect.modify_unit_stat(battle, self, stat_name, value)
        if stat_name in self.stat_minimums:
            return max(self.stat_minimums[stat_name], value)
        return value

    def targeting_range(self) -> int:
        value = int(self.stat("attack_range"))
        for component in self.iter_components():
            value = component.modify_targeting_range(value)
        return max(1, value)

    def is_enemy_of(self, other: "Unit") -> bool:
        return self.player_id != other.player_id

    def heal_fraction(self, amount: float, *, ignore_effect_immunity: bool = False) -> None:
        if self.direct_effects_blocked() and not ignore_effect_immunity:
            return
        if self.allow_overheal:
            self.current_hp = round(self.current_hp + amount, 4)
            return
        self.current_hp = round(min(self.max_health, self.current_hp + amount), 4)

    def damage_fraction_after_limits(self, amount: float) -> float:
        amount = max(0.0, float(amount))
        for component in self.iter_components():
            amount = min(amount, max(0.0, component.limit_hp_loss(amount)))
        return round(amount, 4)

    def take_damage_fraction(self, amount: float) -> None:
        amount = self.damage_fraction_after_limits(amount)
        self.current_hp = round(max(0.0, self.current_hp - amount), 4)
        if self.current_hp <= 0:
            self.alive = False

    def can_take_turn_actions(self, battle: "Battle") -> bool:
        return (
            self.alive
            and not self.banished
            and not getattr(self, "equipped_to_id", None)
            and self.player_id == battle.active_player
            and battle.unit_belongs_to_current_turn(self)
            and self.turn_ready
            and battle.clone_can_act_this_turn(self)
        )

    def refresh_for_turn(self, battle: "Battle") -> None:
        self.move_used = False
        self.normal_move_steps_used = 0
        self.normal_move_actions_used = 0
        self.attacks_used = 0
        self.performed_active_skill = False
        self.moved_this_turn = False
        self.actions_taken_this_turn = []
        if self.is_summon and not self.can_act_on_entry_turn:
            self.turn_ready = False
            self.can_act_on_entry_turn = True
        else:
            self.turn_ready = True
        for component in list(self.iter_components()):
            component.on_owner_turn_start(battle)

    def finish_turn(self, battle: "Battle") -> None:
        for component in list(self.iter_components()):
            component.on_owner_turn_end(battle)

    def to_public_dict(self, battle: "Battle") -> dict[str, Any]:
        return {
            "id": self.unit_id,
            "player_id": self.player_id,
            "name": self.name,
            "title": self.title,
            "role": self.role,
            "attribute": self.attribute,
            "race": self.race,
            "level": self.level,
            "alive": self.alive,
            "banished": self.banished,
            "banish_turns_remaining": self.banish_turns_remaining,
            "banish_return_position": self.banish_return_position.to_dict() if self.banish_return_position else None,
            "is_summon": self.is_summon,
            "hero_code": str(getattr(self, "hero_code", "") or ""),
            "is_army_soldier": is_army_soldier(self),
            "is_clone": self.is_clone,
            "is_mount": self.is_mount,
            "mount_owner_id": self.mount_owner_id,
            "mounted_on_unit_id": self.mounted_on_unit_id,
            "ridden_by_unit_id": self.ridden_by_unit_id,
            "turn_ready": self.turn_ready,
            "position": self.position.to_dict() if self.position else None,
            "last_position": (
                self.last_position.to_dict()
                if getattr(self, "last_position", None)
                else None
            ),
            "footprint": {
                "width": self.footprint_width,
                "height": self.footprint_height,
                "min_dx": self.footprint_min_dx,
                "min_dy": self.footprint_min_dy,
                "max_dx": self.footprint_max_dx,
                "max_dy": self.footprint_max_dy,
                "offsets": [{"x": dx, "y": dy} for dx, dy in self.footprint_offsets],
            },
            "occupied_cells": [cell.to_dict() for cell in battle.unit_cells(self)],
            "hp": self.current_hp,
            "max_hp": self.max_health,
            "mana": self.current_mana,
            "max_mana": self.max_mana(),
            "unbounded_mana": bool(self.allow_unbounded_mana),
            "mana_points": self.mana_points,
            "base_stats": self.base_stats.to_dict(),
            "stats": {
                "attack": self.stat("attack"),
                "defense": self.stat("defense"),
                "speed": self.stat("speed"),
                "attack_range": self.targeting_range(),
                "mana": self.current_mana,
                "max_mana": self.max_mana(),
                "mana_points": self.mana_points,
            },
            "move_used": self.move_used,
            "normal_move_steps_used": self.normal_move_steps_used,
            "normal_move_actions_used": self.normal_move_actions_used,
            "normal_move_actions_per_turn": self.normal_move_actions_per_turn(),
            "attacks_used": self.attacks_used,
            "attacks_per_turn": self.attack_actions_per_turn(),
            "performed_active_skill": self.performed_active_skill,
            "moved_this_turn": self.moved_this_turn,
            "shields": self.shields,
            "temporary_shields": self.temporary_shields,
            "total_shields": self.total_shields(),
            "dodge_charges": self.dodge_charges,
            "magic_immunity": self.magic_immunity,
            "physical_immunity": bool(getattr(self, "physical_immunity", False)),
            "cannot_be_targeted": self.cannot_be_targeted,
            "cannot_move": self.cannot_move,
            "cannot_normal_move": self.cannot_normal_move,
            "cannot_heal": self.cannot_heal,
            "cannot_attack": self.cannot_attack,
            "cannot_use_skills": self.cannot_use_skills,
            "is_siege_structure": bool(getattr(self, "is_siege_structure", False)),
            "siege_reload_cycle": bool(getattr(self, "siege_reload_cycle", False)),
            "siege_loaded": bool(getattr(self, "siege_loaded", False)),
            "siege_reload_state": str(
                getattr(self, "siege_reload_state", "")
                or ("ready" if bool(getattr(self, "siege_loaded", False)) else "empty")
            ),
            "siege_profile_id": str(getattr(self, "siege_profile_id", "") or ""),
            "siege_family": str(getattr(self, "siege_family", "") or ""),
            "siege_tier": int(getattr(self, "siege_tier", 0) or 0),
            "splash_radius": int(getattr(self, "splash_radius", 0) or 0),
            "ignore_units_while_moving": self.ignore_units_while_moving,
            "allow_enemy_destination_overlap": self.allow_enemy_destination_overlap,
            "standable_terrain": bool(getattr(self, "standable_terrain", False)),
            "raw_skill_text": self.raw_skill_text,
            "raw_trait_text": self.raw_trait_text,
            "skills": [skill.to_public_dict(battle) for skill in self.skills],
            "traits": [trait.to_public_dict(battle) for trait in [*self.traits, *self.projected_traits()]],
            "statuses": [status.to_public_dict(battle) for status in self.statuses],
        }

    def allows_destination_overlap_with(self, battle: "Battle", occupant: "Unit") -> bool:
        return self.allow_enemy_destination_overlap and occupant.player_id != self.player_id


class HeroUnit(Unit, ABC):
    pass


class Battle:
    def __setstate__(self, state: dict[str, Any]) -> None:
        self.__dict__.update(state)
        self.__dict__.setdefault("resolving_action", None)
        self.__dict__.setdefault("_separate_attack_sequences", {})
        self.__dict__.setdefault("pending_damage_choice", None)
        # Rebind after checkpoint loading or deepcopy; weak references are not saved.
        for unit in [*self.units.values(), *getattr(self, "destroyed_units", [])]:
            unit._battle_ref = weakref.ref(self)

    def __init__(
        self,
        *,
        width: int = 8,
        height: int = 8,
        damage_rule: Optional[DamageRule] = None,
    ) -> None:
        self.width = width
        self.height = height
        self.damage_rule = damage_rule or SummaryDamageRule()
        self.units: dict[str, Unit] = {}
        self.destroyed_units: list[Unit] = []
        self.field_effects: list[BattleFieldEffect] = []
        self.active_player = 1
        self.turn_number = 1
        self.round_number = 1
        self.winner: Optional[int] = None
        self.logs: list[str] = []
        self.pending_chain: Optional[ReactionWindow] = None
        self.pending_followup_actions: deque[QueuedAction] = deque()
        self._separate_attack_sequences: dict[str, dict[str, Any]] = {}
        self.pending_respawn_unit_ids: list[str] = []
        self.pending_damage_choice: Optional[dict[str, Any]] = None
        self.turn_order_unit_ids: list[str] = []
        self.turn_slot_index = 0
        self.active_turn_unit_id: Optional[str] = None
        self.legacy_player_turn_mode = False
        self.initial_hero_count = 0
        self.turn_timeout_limit = 0
        self.turn_timeout_winner = 2
        self.completed_turns = 0
        self._log_suppression_depth = 0
        self._draining_followup_actions = False
        self.visual_events: list[VisualEvent] = []
        self._next_visual_event_id = 1
        self.stale_queued_action_count = 0
        self._next_action_resolution_token = 1
        self._current_action_resolution_token: Optional[int] = None
        self.resolving_action: Optional[QueuedAction] = None
        self.combat_stats: dict[str, dict[str, Any]] = {}
        self.summary_events: list[dict[str, Any]] = []
        self._next_summary_event_id = 1
        self._summary_defeated_unit_ids: set[str] = set()
        self.win_reason_code = ""
        self.win_reason_text = ""
        self.blocked_cells: set[tuple[int, int]] = set()
        self.army_orders: dict[int, dict[str, str]] = default_army_orders()
        self.army_ai_players: set[int] = set()
        self.army_strike_wave: str = ""
        self.fast_ai_simulation = False
        self.on_replay_checkpoint = None
        self._replay_match_end_emitted = False

    @property
    def current_action_resolution_token(self) -> Optional[int]:
        """Stable only while one declared queued action is resolving."""
        return self._current_action_resolution_token

    def _emit_replay_checkpoint(self, reason: str) -> None:
        if not bool(getattr(self, "fast_ai_simulation", False)):
            return
        if reason == "match_end" and bool(getattr(self, "_replay_match_end_emitted", False)):
            return
        hook = getattr(self, "on_replay_checkpoint", None)
        if not callable(hook):
            return
        if reason == "match_end":
            self._replay_match_end_emitted = True
        hook(reason)

    def log(self, message: str) -> None:
        if self._log_suppression_depth > 0:
            return
        self.logs.append(message)
        self.logs = self.logs[-120:]

    def log_public_event(
        self,
        message: str,
        *,
        source: Unit | None = None,
        target: Unit | None = None,
    ) -> None:
        if target is not None and target.is_stealthed():
            return
        public_message = message
        if source is not None and source.is_stealthed():
            public_message = public_message.replace(source.name, "有单位")
        self.logs.append(public_message)
        self.logs = self.logs[-120:]

    def source_cell_from_payload(
        self,
        actor: Unit | None,
        payload: Optional[dict[str, Any]] = None,
    ) -> Position | None:
        if payload and payload.get("declared_source_x") is not None and payload.get("declared_source_y") is not None:
            return Position(int(payload["declared_source_x"]), int(payload["declared_source_y"]))
        if actor is not None and actor.position is not None:
            return actor.position
        return None

    def record_visual_event(
        self,
        *,
        kind: Literal["attack", "skill", "defense"],
        display_name: str,
        actor: Unit | None = None,
        action_type: str = "",
        action_code: str = "",
        target_unit_ids: Optional[list[str]] = None,
        target_cells: Optional[list[Position]] = None,
        source_cell: Position | None = None,
        defense_reason: str = "",
        metadata: Optional[dict[str, Any]] = None,
    ) -> None:
        if bool(getattr(self, "fast_ai_simulation", False)):
            return
        payload = dict(metadata or {})
        wave = str(getattr(self, "army_strike_wave", "") or "")
        if wave and "army_strike_wave" not in payload:
            payload["army_strike_wave"] = wave
        event = VisualEvent(
            event_id=self._next_visual_event_id,
            kind=kind,
            display_name=display_name,
            actor_id=actor.unit_id if actor is not None else None,
            actor_player_id=actor.player_id if actor is not None else None,
            action_type=action_type,
            action_code=action_code,
            target_unit_ids=list(target_unit_ids or []),
            target_cells=list(target_cells or []),
            source_cell=source_cell,
            defense_reason=defense_reason,
            metadata=payload,
        )
        self._next_visual_event_id += 1
        self.visual_events.append(event)
        self.visual_events = self.visual_events[-40:]

    def _find_summary_unit(self, unit_id: Optional[str]) -> Optional[Unit]:
        if not unit_id:
            return None
        unit = self.units.get(unit_id)
        if unit is not None:
            return unit
        return next((item for item in self.destroyed_units if item.unit_id == unit_id), None)

    def _summary_root_unit(self, unit: Optional[Unit]) -> Optional[Unit]:
        current = unit
        seen: set[str] = set()
        while current is not None and (current.is_summon or current.is_clone):
            if current.unit_id in seen:
                break
            seen.add(current.unit_id)
            parent_id = current.mount_owner_id or current.summoner_id
            parent = self._find_summary_unit(parent_id)
            if parent is None:
                break
            current = parent
        return current

    def _combat_stat_for(self, unit: Optional[Unit]) -> Optional[dict[str, Any]]:
        root = self._summary_root_unit(unit)
        if root is None:
            return None
        existing = self.combat_stats.get(root.unit_id)
        if existing is not None:
            return existing
        entry = {
            "unit_id": root.unit_id,
            "hero_code": str(getattr(root, "hero_code", "") or ""),
            "name": root.name,
            "player_id": root.player_id,
            "owner_seat_id": getattr(root, "owner_seat_id", None),
            "damage_dealt": 0.0,
            "healing_done": 0.0,
            "damage_taken": 0.0,
            "healing_received": 0.0,
            "kills": 0,
            "deaths": 0,
            "shields_broken": 0,
            "chain_reactions": 0,
            "actions": 0,
        }
        self.combat_stats[root.unit_id] = entry
        return entry

    def _append_summary_event(self, kind: str, **payload: Any) -> dict[str, Any]:
        event = {
            "event_id": self._next_summary_event_id,
            "kind": kind,
            "turn_index": max(1, int(self.completed_turns) + 1),
            **payload,
        }
        self._next_summary_event_id += 1
        self.summary_events.append(event)
        self.summary_events = self.summary_events[-300:]
        return event

    def record_rule_trigger_summary(
        self,
        rule_code: str,
        *,
        actor: Unit,
        target: Unit | None = None,
    ) -> dict[str, Any]:
        """Record a rule trigger with the enclosing declared-action identity."""
        return self._append_summary_event(
            "rule_trigger",
            rule_code=rule_code,
            action_resolution_token=self.current_action_resolution_token,
            actor_unit_id=actor.unit_id,
            actor_name=actor.name,
            target_unit_id=target.unit_id if target is not None else None,
            target_name=target.name if target is not None else None,
        )

    def record_damage_summary(self, ctx: DamageContext, actual_damage: float) -> None:
        amount = round(max(0.0, float(actual_damage)), 4)
        if amount <= 0:
            return
        source = self._summary_root_unit(ctx.source)
        target = self._summary_root_unit(ctx.target)
        source_stat = self._combat_stat_for(source)
        target_stat = self._combat_stat_for(target)
        if source_stat is not None:
            source_stat["damage_dealt"] = round(float(source_stat["damage_dealt"]) + amount, 4)
        if target_stat is not None:
            target_stat["damage_taken"] = round(float(target_stat["damage_taken"]) + amount, 4)
        self._append_summary_event(
            "damage",
            actor_unit_id=source.unit_id if source is not None else None,
            actor_name=source.name if source is not None else "场地效果",
            actor_player_id=source.player_id if source is not None else None,
            target_unit_id=target.unit_id if target is not None else ctx.target.unit_id,
            target_name=target.name if target is not None else ctx.target.name,
            target_player_id=target.player_id if target is not None else ctx.target.player_id,
            action_name=ctx.action_name,
            amount=amount,
        )

    def record_heal_summary(self, ctx: HealContext, actual_healing: float) -> None:
        amount = round(max(0.0, float(actual_healing)), 4)
        if amount <= 0:
            return
        source = self._summary_root_unit(ctx.source or ctx.target)
        target = self._summary_root_unit(ctx.target)
        source_stat = self._combat_stat_for(source)
        target_stat = self._combat_stat_for(target)
        if source_stat is not None:
            source_stat["healing_done"] = round(float(source_stat["healing_done"]) + amount, 4)
        if target_stat is not None:
            target_stat["healing_received"] = round(float(target_stat["healing_received"]) + amount, 4)
        self._append_summary_event(
            "healing",
            actor_unit_id=source.unit_id if source is not None else None,
            actor_name=source.name if source is not None else "场地效果",
            actor_player_id=source.player_id if source is not None else None,
            target_unit_id=target.unit_id if target is not None else ctx.target.unit_id,
            target_name=target.name if target is not None else ctx.target.name,
            target_player_id=target.player_id if target is not None else ctx.target.player_id,
            action_name=ctx.action_name,
            amount=amount,
        )

    def record_shield_break_summary(self, source: Optional[Unit], target: Unit, action_name: str) -> None:
        root_source = self._summary_root_unit(source)
        source_stat = self._combat_stat_for(root_source)
        if source_stat is not None:
            source_stat["shields_broken"] = int(source_stat["shields_broken"]) + 1
        self._append_summary_event(
            "shield_break",
            actor_unit_id=root_source.unit_id if root_source is not None else None,
            actor_name=root_source.name if root_source is not None else "场地效果",
            actor_player_id=root_source.player_id if root_source is not None else None,
            target_name=self._summary_root_unit(target).name if self._summary_root_unit(target) is not None else target.name,
            action_name=action_name,
            amount=1,
        )

    def record_defeat_summary(self, source: Optional[Unit], target: Unit, action_name: str) -> None:
        if target.unit_id in self._summary_defeated_unit_ids or target.is_summon or target.is_clone:
            return
        self._summary_defeated_unit_ids.add(target.unit_id)
        root_source = self._summary_root_unit(source)
        root_target = self._summary_root_unit(target)
        source_stat = self._combat_stat_for(root_source)
        target_stat = self._combat_stat_for(root_target)
        if source_stat is not None:
            source_stat["kills"] = int(source_stat["kills"]) + 1
        if target_stat is not None:
            target_stat["deaths"] = int(target_stat["deaths"]) + 1
        self._append_summary_event(
            "defeat",
            actor_unit_id=root_source.unit_id if root_source is not None else None,
            actor_name=root_source.name if root_source is not None else "场地效果",
            actor_player_id=root_source.player_id if root_source is not None else None,
            target_unit_id=root_target.unit_id if root_target is not None else target.unit_id,
            target_name=root_target.name if root_target is not None else target.name,
            target_player_id=root_target.player_id if root_target is not None else target.player_id,
            action_name=action_name,
            amount=1,
        )

    def record_postgame_action(self, actor: Optional[Unit], payload: dict[str, Any]) -> None:
        stat = self._combat_stat_for(actor)
        if stat is None:
            return
        action_type = str(payload.get("type") or "")
        if action_type not in {"chain_skip", "respawn_select", "end_turn", "damage_choice"}:
            stat["actions"] = int(stat["actions"]) + 1
        if action_type == "chain_react":
            stat["chain_reactions"] = int(stat["chain_reactions"]) + 1

    def combat_summary_entries(self) -> list[dict[str, Any]]:
        for unit in [*self.all_units(), *self.destroyed_units]:
            if not unit.is_summon and not unit.is_clone:
                self._combat_stat_for(unit)
        return [dict(entry) for entry in self.combat_stats.values()]

    def emit_visual_event_for_queued_action(self, actor: Unit, queued_action: QueuedAction) -> None:
        payload = queued_action.payload
        action_code = ""
        kind: Literal["attack", "skill", "defense"]
        target_unit_ids = list(queued_action.target_unit_ids)
        target_cells = list(queued_action.target_cells)

        if queued_action.action_type == "attack":
            kind = "attack"
            action_code = str(payload.get("attack_code") or "attack")
        elif queued_action.action_type == "skill":
            kind = "skill"
            action_code = str(payload.get("skill_code") or "")
        elif queued_action.action_type == "skill_effect":
            kind = "skill"
            action_code = str(payload.get("effect_code") or "")
            if not target_cells:
                target_cells = self.payload_positions(payload, "cells")
        elif queued_action.action_type == "reaction_skill":
            kind = "skill"
            action_code = str(payload.get("action_code") or "")
        elif queued_action.action_type == "reaction_action" and str(payload.get("action_code") or "") == "block":
            kind = "defense"
            action_code = "block"
            target_unit_ids = [actor.unit_id]
            target_cells = self.unit_cells(actor)
        elif queued_action.action_type == "reaction_action" and str(payload.get("action_code") or "") == "counter":
            kind = "attack"
            action_code = "counter"
            source = self.units.get(queued_action.reaction_source_id or "")
            target_unit_ids = [source.unit_id] if source is not None else []
            target_cells = self.unit_cells(source) if source is not None else []
        else:
            return

        self.record_visual_event(
            kind=kind,
            display_name=queued_action.display_name,
            actor=actor,
            action_type=queued_action.action_type,
            action_code=action_code,
            target_unit_ids=target_unit_ids,
            target_cells=target_cells,
            source_cell=self.source_cell_from_payload(actor, payload),
            metadata={
                "segment_index": payload.get("segment_index"),
                "segment_count": payload.get("segment_count"),
            },
        )

    def emit_defense_visual_event(
        self,
        *,
        source: Unit | None,
        target: Unit,
        action_name: str,
        defense_reason: str,
    ) -> None:
        self.record_visual_event(
            kind="defense",
            display_name=action_name,
            actor=source,
            action_type="defense",
            action_code=defense_reason,
            target_unit_ids=[target.unit_id],
            target_cells=self.unit_cells(target),
            source_cell=self.source_cell_from_payload(source),
            defense_reason=defense_reason,
        )

    @contextmanager
    def suppress_logs(self) -> Iterable[None]:
        self._log_suppression_depth += 1
        try:
            yield
        finally:
            self._log_suppression_depth = max(0, self._log_suppression_depth - 1)

    def suppress_logs_for_stealth(self, *units: Unit | None):
        if any(unit is not None and unit.is_stealthed() for unit in units):
            return self.suppress_logs()
        return nullcontext()

    def queued_action_hides_logs(self, queued_action: QueuedAction) -> bool:
        if queued_action.suppress_logs:
            return True
        actor = self.units.get(queued_action.actor_id)
        if actor is not None and actor.is_stealthed():
            return True
        for unit_id in queued_action.target_unit_ids:
            target = self.units.get(unit_id)
            if target is not None and target.is_stealthed():
                return True
        return False

    def add_unit(self, unit: Unit, position: Position, *, allow_stealth_overlap: bool = False, trigger_enter_effects: bool = True) -> None:
        if not self.can_place_unit(
            unit,
            position,
            ignore=unit,
            mover=unit,
            allow_stealth_overlap=allow_stealth_overlap,
        ):
            raise ActionError("目标位置已被占用。")
        unit.position = position
        unit._battle_ref = weakref.ref(self)
        if unit.is_summon and not unit.can_act_on_entry_turn and self.current_turn_unit() is not None:
            # Direct add_unit summons follow the same entry-turn delay as summon_unit.
            # Explicit entry-action exceptions set can_act_on_entry_turn before placement.
            unit.turn_ready = False
            unit.can_act_on_entry_turn = True
        if unit.is_summon and not hasattr(unit, "original_weather_race"):
            parent = self.units.get(unit.summoner_id or "")
            unit.original_weather_race = getattr(parent, "original_weather_race", parent.race if parent else unit.race)
        self.units[unit.unit_id] = unit
        self.log(f"{unit.name} 进入战场。")
        for component in list(unit.iter_components()):
            if trigger_enter_effects:
                component.on_enter_battle(self)
            component.on_destroyed_hero_count_changed(self, self.destroyed_hero_count())
        for effect in list(self.field_effects):
            effect.on_unit_entered(self, unit)

    def remove_unit(self, unit: Unit) -> None:
        if unit.unit_id not in self.units or getattr(unit, "_removal_in_progress", False):
            return
        self.capture_destruction_position(unit)
        unit._removal_in_progress = True
        try:
            self._remove_unit(unit)
        finally:
            unit._removal_in_progress = False

    def capture_destruction_position(self, unit: Unit) -> None:
        if unit.alive or unit.position is None:
            return
        # Damage may temporarily use the declared origin; death belongs to the real body.
        unit.last_position = getattr(unit, "_resolution_actual_position", None) or unit.position
        unit.last_destroyed_cells = unit.footprint_cells_at(unit.last_position)
        unit.destruction_count = getattr(unit, "destruction_count", 0) + 1
        unit.position = None
        self.log(f"{unit.name} 被击破。")
        if unit.is_summon and unit.summoner_id:
            summoner = self.units.get(unit.summoner_id)
            if summoner is not None and summoner.alive:
                for component in list(summoner.iter_components()):
                    component.on_owned_summon_destroyed(self, unit)

    def _remove_unit(self, unit: Unit) -> None:
        for component in list(unit.iter_components()):
            component.on_owner_removed(self)
        if unit.ridden_by_unit_id:
            rider = self.units.get(unit.ridden_by_unit_id)
            if rider is not None:
                self.clear_mounted_state(rider)
        elif unit.mounted_on_unit_id:
            self.clear_mounted_state(unit)
        if unit.unit_id in self.units:
            del self.units[unit.unit_id]

    def add_field_effect(self, effect: BattleFieldEffect, *, source: Unit | None = None) -> None:
        candidates = self.field_effects
        if getattr(effect, "global_weather", False):
            owner = source.player_id if source is not None else getattr(effect, "weather_owner_player_id", None)
            if owner is None:
                owner = getattr(effect, "source_player_id", None)
            if owner is None:
                weather_source = self.units.get(str(getattr(effect, "source_unit_id", "")))
                owner = weather_source.player_id if weather_source is not None else None
            if owner is None and self.resolving_action is not None:
                action_source = self.units.get(self.resolving_action.actor_id)
                owner = action_source.player_id if action_source is not None else None
            effect.weather_owner_player_id = owner
            effect.weather_owner_player_ids = {owner} if owner is not None else set()
            for existing in list(self.field_effects):
                if owner is None or not getattr(existing, "global_weather", False):
                    continue
                owners = set(getattr(existing, "weather_owner_player_ids", set()))
                if not owners and getattr(existing, "weather_owner_player_id", None) is not None:
                    owners.add(existing.weather_owner_player_id)
                if owner not in owners or self.canonical_weather_name(getattr(existing, "weather_name", "")) == self.canonical_weather_name(getattr(effect, "weather_name", "")):
                    continue
                owners.remove(owner)
                if owners:
                    existing.weather_owner_player_ids = owners
                    existing.weather_owner_player_id = next(iter(owners))
                    if getattr(existing, "source_player_id", None) == owner:
                        existing.source_player_id = existing.weather_owner_player_id
                else:
                    self.remove_field_effect(existing)
            candidates = [item for item in self.field_effects if getattr(item, "global_weather", False)
                          and (owner in getattr(item, "weather_owner_player_ids", set())
                               or self.canonical_weather_name(getattr(item, "weather_name", "")) == self.canonical_weather_name(getattr(effect, "weather_name", "")))]
        if effect.merge_into_existing(self, candidates):
            if getattr(effect, "global_weather", False) and owner is not None:
                for existing in candidates:
                    if self.canonical_weather_name(getattr(existing, "weather_name", "")) == self.canonical_weather_name(getattr(effect, "weather_name", "")):
                        existing.weather_owner_player_ids = set(getattr(existing, "weather_owner_player_ids", set())) | {owner}
                        existing.weather_owner_player_id = owner
                        break
            self.enforce_sandstorm_stealth_rules()
            return
        self.field_effects.append(effect)
        self.log(f"场地效果【{effect.name}】生效。")
        self.enforce_sandstorm_stealth_rules()

    def remove_field_effect(self, effect: BattleFieldEffect) -> None:
        if effect in self.field_effects:
            self.field_effects.remove(effect)
            self.log(f"场地效果【{effect.name}】结束。")
            if getattr(effect, "weather_name", None):
                for unit in self.all_units():
                    unit.clamp_mana()

    @staticmethod
    def canonical_weather_name(name: str) -> str:
        return "天空圣域" if name in {"天空圣域", "天空的圣域"} else name

    def weather_effect_applies(self, effect: BattleFieldEffect, unit: Unit) -> bool:
        """One contribution per named weather at the recipient's real footprint."""
        name = self.canonical_weather_name(getattr(effect, "weather_name", ""))
        position = getattr(unit, "_resolution_actual_position", None) or unit.position
        cells = self.unit_cells_at(unit, position) if position is not None and not unit.banished else []
        if not cells:
            return False
        for existing in self.field_effects:
            if self.canonical_weather_name(getattr(existing, "weather_name", "")) != name:
                continue
            affected = existing.affected_cells(self)
            if (getattr(existing, "global_weather", False) and not affected) or any(cell in affected for cell in cells):
                return existing is effect
        return False

    def notify_field_basic_attack_finished(self, actor: Unit, payload: dict[str, Any], contexts: list[DamageContext], *, missed: bool = False) -> None:
        if getattr(self, "_measuring_attack_success", False):
            return
        for effect in list(self.field_effects):
            effect.on_basic_attack_finished(self, actor, payload, contexts, missed)

    def has_weather(self, name: str) -> bool:
        return any(self.canonical_weather_name(getattr(effect, "weather_name", "")) == self.canonical_weather_name(name) for effect in self.field_effects)

    def cell_has_weather(self, name: str, cell: Position) -> bool:
        for effect in self.field_effects:
            if self.canonical_weather_name(getattr(effect, "weather_name", "")) != self.canonical_weather_name(name):
                continue
            affected = effect.affected_cells(self)
            if not affected:
                if getattr(effect, "global_weather", False):
                    return True
                continue
            if any(target.x == cell.x and target.y == cell.y for target in affected):
                return True
        return False

    def unit_in_weather(self, name: str, unit: Unit) -> bool:
        position = getattr(unit, "_resolution_actual_position", None) or unit.position
        return position is not None and not unit.banished and any(self.cell_has_weather(name, cell) for cell in self.unit_cells_at(unit, position))

    def enforce_sandstorm_stealth_rules(self) -> None:
        for unit in list(self.all_units()):
            if not unit.alive or unit.position is None or unit.banished:
                continue
            if not self.unit_in_weather("沙尘", unit):
                continue
            for status in list(unit.statuses):
                if status.name == "隐身" or getattr(status, "grants_stealth", False):
                    unit.remove_status(status, self)

    def start_battle(self) -> None:
        command_count = len([unit for unit in self.all_units() if not unit.is_summon and not is_army_soldier(unit)])
        self.initial_hero_count = command_count
        configured_limit = int(getattr(self, "turn_timeout_limit", 0) or 0)
        self.turn_timeout_limit = configured_limit if configured_limit > 0 else DEFAULT_HERO_TURN_LIMIT
        if int(getattr(self, "turn_timeout_winner", 0) or 0) not in {1, 2}:
            self.turn_timeout_winner = 2
        self.completed_turns = 0
        self.ensure_turn_order()
        self.start_current_turn()

    def end_turn(self) -> None:
        if self.pending_chain is not None:
            raise ActionError("当前正在等待连锁结算，不能结束回合。")
        if self.is_army_turn():
            player_id = self.army_turn_player_id()
            if player_id is not None:
                resolve_army_phase(self, player_id)
        self.resolve_turn_end()

    def is_army_turn(self) -> bool:
        return parse_army_slot(self.current_turn_slot_unit_id()) is not None

    def army_turn_player_id(self) -> Optional[int]:
        return parse_army_slot(self.current_turn_slot_unit_id())

    def has_command_hero_slots(self) -> bool:
        return any(parse_army_slot(unit_id) is None for unit_id in self.turn_order_unit_ids)

    def set_army_order(
        self,
        player_id: int,
        order: str,
        direction: str | None = None,
        kind: str | None = None,
        stride: str | None = None,
        ammo: str | None = None,
    ) -> dict[str, str]:
        from wujiang.tactical.engine.army import (
            apply_army_order,
            command_for_kind,
            normalize_army_command,
            normalize_army_kind,
            present_army_kinds,
        )

        team_id = 1 if int(player_id) != 2 else 2
        target_kind = None if kind in {None, ""} else normalize_army_kind(kind)
        previous = command_for_kind(self.army_orders, team_id, target_kind or "infantry")
        allowed_ammo = None
        ammo_options = (getattr(self, "siege_ammo_by_player", None) or {}).get(team_id)
        if ammo_options:
            allowed_ammo = [str(item.get("id") or item) for item in ammo_options]
        command = normalize_army_command(
            order,
            direction,
            player_id=team_id,
            previous=previous,
            stride=stride,
            ammo=ammo,
            allowed_ammo=allowed_ammo,
        )
        apply_army_order(
            self.army_orders,
            team_id,
            command,
            kind=target_kind,
            kinds=present_army_kinds(self, team_id) or None,
        )
        return dict(command)

    def ensure_turn_order(self) -> None:
        if self.turn_order_unit_ids:
            self.turn_order_unit_ids = with_army_turn_slots(self, self.turn_order_unit_ids)
            if self.turn_slot_index >= len(self.turn_order_unit_ids):
                self.turn_slot_index = 0
            self.active_turn_unit_id = self.current_turn_slot_unit_id()
            return
        hero_ids = [unit.unit_id for unit in command_hero_units(self, 1)] + [
            unit.unit_id for unit in command_hero_units(self, 2)
        ]
        if not hero_ids and not any(is_army_soldier(unit) for unit in self.all_units()):
            self.active_turn_unit_id = None
            return
        self.configure_turn_order(hero_ids)

    def configure_turn_order(self, hero_unit_ids: Iterable[str], *, starting_index: int = 0) -> None:
        self._exclusive_turn_unit_id = None
        self._pending_exclusive_turn_unit_id = None
        self.turn_order_unit_ids = with_army_turn_slots(self, hero_unit_ids)
        if not self.turn_order_unit_ids:
            self.turn_slot_index = 0
            self.active_turn_unit_id = None
            return
        self.turn_slot_index = max(0, min(int(starting_index), len(self.turn_order_unit_ids) - 1))
        self.active_turn_unit_id = self.turn_order_unit_ids[self.turn_slot_index]

    def current_turn_slot_unit_id(self) -> Optional[str]:
        if getattr(self, "_exclusive_turn_unit_id", None):
            return self._exclusive_turn_unit_id
        if not self.turn_order_unit_ids:
            return None
        if self.turn_slot_index >= len(self.turn_order_unit_ids):
            self.turn_slot_index = 0
        return self.turn_order_unit_ids[self.turn_slot_index]

    def current_turn_unit(self) -> Optional[Unit]:
        unit_id = self.current_turn_slot_unit_id()
        if unit_id is None or parse_army_slot(unit_id) is not None:
            return None
        return self.units.get(unit_id)

    def controlling_hero_id(self, unit: Unit | None) -> Optional[str]:
        if unit is None:
            return None
        controller_id = getattr(unit, "controller_hero_id", None)
        if controller_id:
            return controller_id
        if not unit.is_summon:
            return unit.unit_id
        if unit.mount_owner_id:
            return self.controlling_hero_id(self.units.get(unit.mount_owner_id))
        if unit.summoner_id:
            return self.controlling_hero_id(self.units.get(unit.summoner_id))
        return None

    def unit_turn_slot_id(self, unit: Unit) -> Optional[str]:
        # A control transfer changes the controller, not the original turn bundle.
        preserved = getattr(unit, "preserved_turn_slot_id", None)
        if preserved:
            return preserved
        if not unit.is_summon:
            return unit.unit_id
        parent = self.units.get(unit.mount_owner_id or unit.summoner_id or "")
        return self.unit_turn_slot_id(parent) if parent is not None else None

    def transfer_unit_control(self, unit: Unit, controller: Unit) -> None:
        if unit.player_id == controller.player_id:
            return
        unit.preserved_turn_slot_id = self.unit_turn_slot_id(unit)
        rider = self.rider_for(unit)
        if rider is not None:
            self.clear_mounted_state(rider)
        unit.player_id = controller.player_id
        unit.controller_hero_id = self.controlling_hero_id(controller)
        unit.owner_seat_id = getattr(controller, "owner_seat_id", None)

    def turn_bundle_units(
        self,
        hero: Unit | str | None,
        *,
        include_banished: bool = True,
    ) -> list[Unit]:
        hero_id = hero.unit_id if isinstance(hero, Unit) else hero
        if not hero_id:
            return []
        if getattr(self, "_exclusive_turn_unit_id", None) == hero_id:
            hero_unit = self.units.get(hero_id)
            return [hero_unit] if hero_unit is not None and hero_unit.alive and (include_banished or not hero_unit.banished) else []
        if self.legacy_player_turn_mode:
            hero_unit = self.units.get(hero_id)
            if hero_unit is None:
                return []
            units = [
                unit
                for unit in self.all_units()
                if unit.alive
                and (self.units.get(self.unit_turn_slot_id(unit) or "", unit).player_id
                     if getattr(unit, "preserved_turn_slot_id", None) else unit.player_id) == hero_unit.player_id
                and not is_army_soldier(unit)
                and (include_banished or not unit.banished)
            ]
            units.sort(key=lambda unit: (0 if unit.unit_id == hero_id else 1, unit.unit_id))
            return units
        units = [
            unit
            for unit in self.all_units()
            if unit.alive
            and self.unit_turn_slot_id(unit) == hero_id
            and not is_army_soldier(unit)
            and (include_banished or not unit.banished)
        ]
        units.sort(key=lambda unit: (0 if unit.unit_id == hero_id else 1, unit.unit_id))
        return units

    def current_turn_bundle_units(self, *, include_banished: bool = True) -> list[Unit]:
        return [unit for unit in self.turn_bundle_units(self.current_turn_slot_unit_id(), include_banished=include_banished)
                if unit.player_id == self.active_player]

    def unit_belongs_to_current_turn(self, unit: Unit | None) -> bool:
        if unit is None:
            return False
        if getattr(self, "_exclusive_turn_unit_id", None):
            return unit.unit_id == self._exclusive_turn_unit_id
        if self.is_army_turn() and is_army_soldier(unit):
            return unit.player_id == self.army_turn_player_id()
        if is_army_soldier(unit):
            return False
        if self.legacy_player_turn_mode:
            hero = self.current_turn_unit()
            slot_owner = self.units.get(self.unit_turn_slot_id(unit) or "", unit)
            return hero is not None and slot_owner.player_id == hero.player_id
        return self.unit_turn_slot_id(unit) == self.current_turn_slot_unit_id()

    def clone_can_act_this_turn(self, unit: Unit) -> bool:
        if not unit.is_clone:
            return True
        root_id = self.unit_turn_slot_id(unit)
        return not any(other.is_clone and other.unit_id != unit.unit_id
                       and self.unit_turn_slot_id(other) == root_id
                       and getattr(other, "_clone_action_turn", None) == self.turn_number
                       for other in [*self.all_units(), *self.destroyed_units])

    def destroy_clone_for_skill_effect(self, target: Unit, *, source: Unit | None, action_name: str) -> bool:
        if not target.is_clone or not target.alive or target.direct_effects_blocked():
            return False
        target.current_hp = 0.0
        target.alive = False
        self.log_public_event(f"{target.name} 的分身受到【{action_name}】技能效果而消散。", source=source, target=target)
        self.cleanup_dead_units()
        return True

    def units_can_act_in_current_turn(self) -> bool:
        return any(unit.can_take_turn_actions(self) for unit in self.current_turn_bundle_units())

    def advance_turn_slot_index(self) -> None:
        pending = getattr(self, "_pending_exclusive_turn_unit_id", None)
        self._pending_exclusive_turn_unit_id = None
        if pending is not None:
            self._exclusive_turn_unit_id = pending
            self.active_turn_unit_id = pending
            self.turn_number += 1
            return
        self._exclusive_turn_unit_id = None
        if not self.turn_order_unit_ids:
            self.active_turn_unit_id = None
            return
        previous_index = self.turn_slot_index
        self.turn_slot_index = (self.turn_slot_index + 1) % len(self.turn_order_unit_ids)
        if self.turn_slot_index <= previous_index:
            self.round_number += 1
        self.turn_number += 1
        self.active_turn_unit_id = self.turn_order_unit_ids[self.turn_slot_index]

    def start_current_turn(self) -> None:
        self.ensure_turn_order()
        if self.winner is not None or not self.turn_order_unit_ids:
            return
        attempts = 0
        while attempts < len(self.turn_order_unit_ids):
            hero_id = self.current_turn_slot_unit_id()
            army_player_id = parse_army_slot(hero_id)
            if army_player_id is not None:
                self.active_turn_unit_id = hero_id
                self.active_player = army_player_id
                if self.completed_turns > 0 and self.has_command_hero_slots():
                    if living_army_units(self, army_player_id):
                        resolve_army_phase(self, army_player_id)
                    if self.winner is not None:
                        return
                    self.resolve_turn_end(auto_started=True)
                    return
                self.log(f"第 {self.round_number} 轮，玩家 {army_player_id} 的军队回合开始。")
                self.check_win_condition()
                return
            hero = self.units.get(hero_id) if hero_id else None
            if hero is None or not hero.alive or hero.is_summon or is_army_soldier(hero):
                self.advance_turn_slot_index()
                attempts += 1
                continue
            self.active_turn_unit_id = hero.unit_id
            self.active_player = hero.player_id
            self._turn_control_completed_players = set()
            self.pending_respawn_unit_ids = []
            bundle_units = self.turn_bundle_units(hero, include_banished=True)
            for unit in bundle_units:
                unit.refresh_for_turn(self)
            for unit in bundle_units:
                if not unit.banished or getattr(unit, "attached_to_unit_id", None):
                    continue
                if unit.banish_turns_remaining > 0:
                    unit.banish_turns_remaining = max(unit.banish_turns_remaining - 1, 0)
                if unit.banish_turns_remaining == 0:
                    self.schedule_respawn(unit)
            self.advance_respawn_queue()
            self.enforce_sandstorm_stealth_rules()
            for observer in list(self.all_units()):
                for component in list(observer.iter_components()):
                    component.on_any_turn_start(self, hero)
            for effect in list(self.field_effects):
                effect.on_turn_start(self, hero)
            self.log(f"第 {self.round_number} 轮，玩家 {hero.player_id} 的【{hero.name}】回合开始。")
            self.check_win_condition()
            if self.winner is not None:
                return
            return

    def peek_next_turn_unit(self) -> Optional[Unit]:
        if self.winner is not None or not self.turn_order_unit_ids:
            return None
        total = len(self.turn_order_unit_ids)
        for offset in range(1, total + 1):
            index = (self.turn_slot_index + offset) % total
            slot_id = self.turn_order_unit_ids[index]
            if parse_army_slot(slot_id) is not None:
                continue
            hero = self.units.get(slot_id)
            if hero is None or not hero.alive or hero.is_summon or is_army_soldier(hero):
                continue
            return hero
        return None

    def resolve_turn_end(self, *, auto_started: bool = False) -> None:
        hero = self.current_turn_unit()
        ending_player = self.active_player
        bundle_units = self.turn_bundle_units(self.current_turn_slot_unit_id(), include_banished=True)
        completed_players = getattr(self, "_turn_control_completed_players", set())
        completed_players.add(ending_player)
        self._turn_control_completed_players = completed_players
        pending_players = sorted({unit.player_id for unit in bundle_units if unit.alive and not unit.banished}
                                 - completed_players)
        if pending_players:
            self.active_player = pending_players[0]
            self.log(f"原回合槽内交接给玩家 {self.active_player} 操作取得控制权的单位。")
            self._emit_replay_checkpoint("control_handoff")
            return
        # The shared global end still belongs to the original slot's side.
        if hero is not None:
            ending_player = hero.player_id
        if hero is not None and not auto_started:
            self.log(f"{hero.name} 结束了自己的回合。")
        elif self.is_army_turn() and not auto_started:
            self.log(f"玩家 {self.army_turn_player_id()} 的军队结束了回合。")
        for unit in bundle_units:
            unit.finish_turn(self)
        for unit in self.all_units():
            if unit.total_shields() > 0:
                unit.clear_end_of_turn_shields()
        for effect in list(self.field_effects):
            effect.on_any_turn_end(self, ending_player)
        for unit in self.all_units():
            for component in list(unit.iter_components()):
                component.on_any_turn_end(self, ending_player)
        self.enforce_sandstorm_stealth_rules()
        self.cleanup_dead_units()
        if self.winner is not None:
            self._emit_replay_checkpoint("match_end")
            return
        self.completed_turns += 1
        self._emit_replay_checkpoint("turn_end")
        if self.turn_timeout_limit > 0 and self.completed_turns >= self.turn_timeout_limit:
            winner = int(getattr(self, "turn_timeout_winner", 2) or 2)
            if winner not in {1, 2}:
                winner = 2
            self.winner = winner
            self.win_reason_code = "turn_limit"
            self.win_reason_text = f"达到 {self.turn_timeout_limit} 个武将回合上限后判定玩家 {self.winner} 获胜。"
            self._append_summary_event(
                "match_end",
                actor_unit_id=None,
                actor_name="系统",
                actor_player_id=self.winner,
                target_name="",
                action_name="回合上限判定",
                amount=0,
            )
            self.log(
                f"对局已达到 {self.turn_timeout_limit} 个武将回合上限，"
                f"判定玩家 {self.winner} 获胜。"
            )
            self._emit_replay_checkpoint("match_end")
            return
        self.advance_turn_slot_index()
        self.start_current_turn()

    def all_units(self) -> list[Unit]:
        return list(self.units.values())

    def sync_linked_units(self) -> None:
        if getattr(self, "_syncing_linked_units", False):
            return
        self._syncing_linked_units = True
        try:
            for unit in self.all_units():
                for component in list(unit.iter_components()):
                    component.sync_linked_state(self)
        finally:
            self._syncing_linked_units = False

    def prepay_skill_resources(self, skill: Skill, actor: Unit, payload: dict[str, Any]) -> None:
        previous = getattr(actor, "_paying_skill_cost", False)
        actor._paying_skill_cost = True
        try:
            skill.prepay_resources(self, actor, payload)
        finally:
            if previous:
                actor._paying_skill_cost = previous
            else:
                actor.__dict__.pop("_paying_skill_cost", None)

    def mounted_unit_for(self, rider: Unit | None) -> Optional[Unit]:
        if rider is None or not rider.mounted_on_unit_id:
            return None
        mount = self.units.get(rider.mounted_on_unit_id)
        if mount is None or not mount.alive or mount.banished or mount.position is None:
            return None
        return mount

    def rider_for(self, mount: Unit | None) -> Optional[Unit]:
        if mount is None or not mount.ridden_by_unit_id:
            return None
        rider = self.units.get(mount.ridden_by_unit_id)
        if rider is None or not rider.alive or rider.banished or rider.position is None:
            return None
        return rider

    def set_mounted_state(self, rider: Unit, mount: Unit) -> None:
        current_mount = self.mounted_unit_for(rider)
        if current_mount is not None and current_mount.unit_id != mount.unit_id:
            self.clear_mounted_state(rider)
        current_rider = self.rider_for(mount)
        if current_rider is not None and current_rider.unit_id != rider.unit_id:
            self.clear_mounted_state(current_rider)
        rider.mounted_on_unit_id = mount.unit_id
        mount.ridden_by_unit_id = rider.unit_id

    def clear_mounted_state(self, rider: Unit | None) -> None:
        if rider is None:
            return
        mount = self.units.get(rider.mounted_on_unit_id) if rider.mounted_on_unit_id else None
        rider.mounted_on_unit_id = None
        if mount is not None and mount.ridden_by_unit_id == rider.unit_id:
            mount.ridden_by_unit_id = None

    def units_can_overlap(self, left: Unit, right: Unit) -> bool:
        return (
            left.unit_id != right.unit_id
            and (
                left.mounted_on_unit_id == right.unit_id
                or right.mounted_on_unit_id == left.unit_id
                or (left.is_mount and left.mount_owner_id == right.unit_id)
                or (right.is_mount and right.mount_owner_id == left.unit_id)
                or getattr(left, "attached_to_unit_id", None) == right.unit_id
                or getattr(right, "attached_to_unit_id", None) == left.unit_id
                or left.allows_destination_overlap_with(self, right)
                or right.allows_destination_overlap_with(self, left)
            )
        )

    def effect_recipient(self, unit: Unit) -> Unit:
        mount = self.mounted_unit_for(unit)
        return mount if mount is not None else unit

    def effect_units(self, units: Iterable[Unit], *, ignore: Optional[Unit] = None) -> list[Unit]:
        recipients: list[Unit] = []
        seen: set[str] = set()
        ignored_id = self.effect_recipient(ignore).unit_id if ignore is not None else None
        for unit in units:
            recipient = self.effect_recipient(unit)
            if recipient.unit_id == ignored_id:
                continue
            if recipient.unit_id in seen:
                continue
            seen.add(recipient.unit_id)
            recipients.append(recipient)
        return recipients

    def effect_units_at_cells(self, cells: Iterable[Position], *, ignore: Optional[Unit] = None) -> list[Unit]:
        return self.effect_units(self.units_at_cells(cells), ignore=ignore)

    def reaction_proxy_target(self, unit: Unit, queued_action: QueuedAction) -> Optional[Unit]:
        if unit.unit_id in queued_action.target_unit_ids:
            return unit
        mount = self.mounted_unit_for(unit)
        if mount is not None and mount.unit_id in queued_action.target_unit_ids:
            return mount
        return None

    def unit_can_use_block_counter(self, unit: Unit) -> bool:
        components = list(unit.iter_components())
        if not unit.has_block_counter and not any(component.grants_block_counter(self, unit) for component in components):
            return False
        return all(component.allows_block_counter(self, unit) for component in components)

    def player_units(self, player_id: int) -> list[Unit]:
        return [
            unit
            for unit in self.units.values()
            if unit.player_id == player_id and unit.alive
        ]

    def enemy_units(self, player_id: int) -> list[Unit]:
        return [
            unit
            for unit in self.units.values()
            if unit.player_id != player_id and unit.alive
        ]

    def in_bounds(self, position: Position) -> bool:
        return 0 <= position.x < self.width and 0 <= position.y < self.height

    def unit_cells_at(self, unit: Unit, position: Position) -> list[Position]:
        return unit.footprint_cells_at(position)

    def unit_cells(self, unit: Unit) -> list[Position]:
        if unit.position is None or not unit.alive or unit.banished:
            return []
        return self.unit_cells_at(unit, unit.position)

    def unit_occupies(self, unit: Unit, position: Position) -> bool:
        return position in self.unit_cells(unit)

    def path_crossing_units(self, mover: Unit, path: list[Position]) -> list[Unit]:
        """Return an event each time the path enters a unit after fully leaving it."""
        return [unit for unit, _ in self.path_crossing_events(mover, path)]

    def path_crossing_events(self, mover: Unit, path: list[Position]) -> list[tuple[Unit, Position]]:
        """Return the recipient and entered body cell for each distinct passage."""
        if len(path) < 2:
            return []
        by_id: dict[str, Unit] = {}
        bodies: dict[str, set[Position]] = {}
        mover_recipient = self.effect_recipient(mover)
        for unit in self.all_units():
            recipient = self.effect_recipient(unit)
            if recipient.unit_id == mover_recipient.unit_id or not self.unit_cells(unit):
                continue
            by_id[recipient.unit_id] = recipient
            bodies.setdefault(recipient.unit_id, set()).update(self.unit_cells(unit))

        def overlapping_ids(anchor: Position) -> set[str]:
            mover_cells = set(self.unit_cells_at(mover, anchor))
            return {
                unit_id for unit_id, cells in bodies.items() if cells & mover_cells
            }

        previous_overlaps = overlapping_ids(path[0])
        crossed: list[tuple[Unit, Position]] = []
        for anchor in path[1:]:
            current_overlaps = overlapping_ids(anchor)
            mover_cells = set(self.unit_cells_at(mover, anchor))
            for unit_id, unit in by_id.items():
                if unit_id in current_overlaps - previous_overlaps:
                    entered_cell = min(bodies[unit_id] & mover_cells, key=lambda cell: (cell.y, cell.x))
                    crossed.append((unit, entered_cell))
            previous_overlaps = current_overlaps
        return crossed

    def can_place_unit(
        self,
        unit: Unit,
        position: Position,
        *,
        ignore: Optional[Unit] = None,
        mover: Optional[Unit] = None,
        ignore_units: bool = False,
        allow_stealth_overlap: bool = False,
    ) -> bool:
        for cell in self.unit_cells_at(unit, position):
            if not self.in_bounds(cell):
                return False
            if (cell.x, cell.y) in self.blocked_cells:
                return False
            if not ignore_units and self.is_occupied(
                cell,
                ignore=ignore,
                mover=mover or unit,
                allow_stealth_overlap=allow_stealth_overlap,
            ):
                return False
        return True

    def can_traverse_position(self, unit: Unit, position: Position, *, ignore_units: bool) -> bool:
        if ignore_units and unit.has_flying:
            return all(self.in_bounds(cell) for cell in self.unit_cells_at(unit, position))
        return self.can_place_unit(unit, position, ignore=unit, mover=unit, ignore_units=ignore_units)

    def unit_distance_to_cell(self, unit: Unit, cell: Position) -> int:
        cells = self.unit_cells(unit)
        if not cells:
            if unit.position is None:
                return 10**9
            cells = self.unit_cells_at(unit, unit.position)
        return min(origin.distance_to(cell) for origin in cells)

    def distance_between_units(self, left: Unit, right: Unit) -> int:
        left_cells = self.unit_cells(left)
        right_cells = self.unit_cells(right)
        if not left_cells or not right_cells:
            return 10**9
        return min(left_cell.distance_to(right_cell) for left_cell in left_cells for right_cell in right_cells)

    def cells_are_straight_aligned(self, start: Position, end: Position) -> bool:
        dx = end.x - start.x
        dy = end.y - start.y
        return dx == 0 or dy == 0 or abs(dx) == abs(dy)

    def unit_target_in_range_and_line(self, actor: Unit, target: Unit, max_distance: int) -> bool:
        actor_cells = self.unit_cells(actor)
        target_cells = self.unit_cells(target)
        if not actor_cells or not target_cells:
            return False
        if actor.unit_id == target.unit_id:
            return True
        ignore_line = any(component.ignores_direct_unit_target_line(self, actor) for component in actor.iter_components())
        for origin in actor_cells:
            for target_cell in target_cells:
                if origin.distance_to(target_cell) > max_distance:
                    continue
                if ignore_line or self.cells_are_straight_aligned(origin, target_cell):
                    return True
        return False

    def require_unit_target_in_range_and_line(
        self,
        actor: Unit,
        target: Unit,
        max_distance: int,
        *,
        action_name: str,
    ) -> None:
        if not self.unit_target_in_range_and_line(actor, target, max_distance):
            raise ActionError(f"{target.name} 不在【{action_name}】可直线选中的范围内。")

    def unit_hit_count_for_cells(self, unit: Unit, cells: Iterable[Position]) -> int:
        cell_keys = {(cell.x, cell.y) for cell in cells}
        return sum(1 for cell in self.unit_cells(unit) if (cell.x, cell.y) in cell_keys)

    def units_at(self, position: Position, *, ignore: Optional[Unit] = None) -> list[Unit]:
        units: list[Unit] = []
        for unit in self.units.values():
            if ignore is not None and unit.unit_id == ignore.unit_id:
                continue
            if unit.alive and not unit.banished and self.unit_occupies(unit, position):
                units.append(unit)
        return units

    def blocks_position_for(
        self,
        occupant: Unit,
        *,
        mover: Optional[Unit] = None,
        allow_stealth_overlap: bool = False,
    ) -> bool:
        if getattr(occupant, "attached_to_unit_id", None):
            return False
        if mover is not None and getattr(occupant, "standable_terrain", False) and not getattr(mover, "standable_terrain", False):
            return False
        if mover is not None and self.units_can_overlap(occupant, mover):
            return False
        if allow_stealth_overlap and occupant.is_stealthed():
            return False
        return True

    def is_occupied(
        self,
        position: Position,
        *,
        ignore: Optional[Unit] = None,
        mover: Optional[Unit] = None,
        allow_stealth_overlap: bool = False,
    ) -> bool:
        return any(
            self.blocks_position_for(unit, mover=mover, allow_stealth_overlap=allow_stealth_overlap)
            for unit in self.units_at(position, ignore=ignore)
        )

    def unit_at(self, position: Position) -> Optional[Unit]:
        occupants = self.units_at(position)
        if not occupants:
            return None
        visible = [unit for unit in occupants if not unit.is_stealthed()]
        return next((unit for unit in visible if not getattr(unit, "standable_terrain", False)), None) or (visible[0] if visible else occupants[0])

    def selectable_unit_at(
        self,
        position: Position,
        *,
        actor: Optional[Unit] = None,
        ignore_stealth: bool = False,
        preferred_unit_id: Optional[str] = None,
    ) -> Optional[Unit]:
        occupants = self.units_at(position)
        if preferred_unit_id:
            preferred = next((unit for unit in occupants if unit.unit_id == preferred_unit_id), None)
            if preferred is not None:
                ok, _ = self.unit_can_be_selected(preferred, actor=actor, ignore_stealth=ignore_stealth)
                if ok:
                    return preferred
        for unit in sorted(occupants, key=lambda candidate: bool(getattr(candidate, "standable_terrain", False))):
            ok, _ = self.unit_can_be_selected(unit, actor=actor, ignore_stealth=ignore_stealth)
            if ok:
                return unit
        return None

    def targetable_units_at(
        self,
        position: Position,
        *,
        actor: Optional[Unit] = None,
        ignore_stealth: bool = False,
    ) -> list[Unit]:
        targets: list[Unit] = []
        for unit in self.units_at(position):
            ok, _ = self.unit_can_be_selected(unit, actor=actor, ignore_stealth=ignore_stealth)
            if ok:
                targets.append(unit)
        return targets

    def units_sharing_position(self, unit: Unit) -> list[Unit]:
        if unit.position is None:
            return []
        shared: list[Unit] = []
        seen: set[str] = set()
        for cell in self.unit_cells(unit):
            for other in self.units_at(cell, ignore=unit):
                if other.unit_id in seen:
                    continue
                seen.add(other.unit_id)
                if other.alive and not other.banished:
                    shared.append(other)
        return shared

    def shared_stealth_action_block_reason(self, unit: Unit) -> str:
        if unit.position is None or not unit.is_stealthed():
            return ""
        if not self.units_sharing_position(unit):
            return ""
        return f"{unit.name} 隐身时若与其他单位同格，不能攻击或使用技能。"

    def controllable_hero_units(self, player_id: int) -> list[Unit]:
        return [
            unit
            for unit in self.player_units(player_id)
            if unit.alive and not unit.banished and not unit.is_summon
        ]

    def hero_units(self, player_id: int) -> list[Unit]:
        return [
            unit
            for unit in self.player_units(player_id)
            if unit.alive and not unit.is_summon
        ]

    def on_field_hero_units(self) -> list[Unit]:
        return [
            unit
            for unit in self.all_units()
            if unit.alive
            and not unit.is_summon
            and not is_army_soldier(unit)
            and not unit.banished
            and unit.position is not None
        ]

    def clear_all_stealth_if_all_heroes_stealthed(self) -> None:
        heroes = self.on_field_hero_units()
        if not heroes:
            return
        if any(not hero.has_status("隐身") for hero in heroes):
            return
        self.log("场上所有武将都处于隐身状态，所有在场武将的隐身自动解除。")
        for hero in heroes:
            stealth = hero.get_status("隐身")
            if stealth is not None:
                hero.remove_status(stealth, self)

    def respawn_options_for(self, unit: Unit) -> list[Position]:
        origin = unit.banish_return_position or unit.position
        if origin is None:
            return []
        if self.can_place_unit(unit, origin, ignore=unit, mover=unit):
            return [origin]
        best_distance: Optional[int] = None
        options: list[Position] = []
        for y in range(self.height):
            for x in range(self.width):
                cell = Position(x, y)
                if not self.can_place_unit(unit, cell, ignore=unit, mover=unit):
                    continue
                distance = origin.distance_to(cell)
                if best_distance is None or distance < best_distance:
                    best_distance = distance
                    options = [cell]
                elif distance == best_distance:
                    options.append(cell)
        return sorted(options, key=lambda cell: (cell.y, cell.x))

    def current_respawn_prompt(self) -> Optional[RespawnPrompt]:
        while self.pending_respawn_unit_ids:
            unit_id = self.pending_respawn_unit_ids[0]
            unit = self.units.get(unit_id)
            if unit is None or not unit.alive or not unit.banished:
                self.pending_respawn_unit_ids.pop(0)
                continue
            origin = unit.banish_return_position or unit.position
            if origin is None:
                self.pending_respawn_unit_ids.pop(0)
                continue
            options = self.respawn_options_for(unit)
            if not options:
                self.pending_respawn_unit_ids.pop(0)
                self.log(f"{unit.name} 暂时没有可重新出现的空格，将继续等待。")
                continue
            return RespawnPrompt(unit.unit_id, unit.player_id, origin, options)
        return None

    def restore_banished_unit(self, unit: Unit, destination: Position) -> None:
        origin = unit.banish_return_position or unit.position
        unit.banished = False
        unit.banish_turns_remaining = 0
        unit.position = destination
        self.sync_linked_units()
        if origin is not None and destination == origin:
            self.log(f"{unit.name} 在原位重新出现。")
        else:
            self.log(f"{unit.name} 在 ({destination.x}, {destination.y}) 重新出现。")
        self.clear_all_stealth_if_all_heroes_stealthed()

    def schedule_respawn(self, unit: Unit) -> None:
        options = self.respawn_options_for(unit)
        if not options:
            self.log(f"{unit.name} 暂时没有可重新出现的空格，将继续等待。")
            return
        origin = unit.banish_return_position or unit.position
        if origin is not None and len(options) == 1 and options[0] == origin:
            self.restore_banished_unit(unit, origin)
            return
        if unit.unit_id not in self.pending_respawn_unit_ids:
            self.pending_respawn_unit_ids.append(unit.unit_id)
            self.log(f"{unit.name} 即将重新出现，请选择其落点。")

    def advance_respawn_queue(self) -> None:
        while True:
            prompt = self.current_respawn_prompt()
            if prompt is None:
                return
            if len(prompt.options) == 1 and prompt.options[0] == prompt.origin:
                unit = self.get_unit(prompt.unit_id)
                self.pending_respawn_unit_ids.pop(0)
                self.restore_banished_unit(unit, prompt.origin)
                continue
            return

    def units_at_cells(self, cells: Iterable[Position]) -> list[Unit]:
        units: list[Unit] = []
        seen: set[str] = set()
        for cell in cells:
            for unit in self.units_at(cell):
                if unit.unit_id in seen:
                    continue
                seen.add(unit.unit_id)
                units.append(unit)
        return units

    def get_unit(self, unit_id: str) -> Unit:
        if unit_id not in self.units:
            raise ActionError("找不到目标单位。")
        return self.units[unit_id]

    def neighbors(self, position: Position) -> list[Position]:
        result: list[Position] = []
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                if dx == 0 and dy == 0:
                    continue
                candidate = position.offset(dx, dy)
                if self.in_bounds(candidate):
                    result.append(candidate)
        return result

    def line_positions(
        self,
        start: Position,
        direction: tuple[int, int],
        length: int,
    ) -> list[Position]:
        result: list[Position] = []
        current = start
        for _ in range(length):
            current = current.offset(*direction)
            if not self.in_bounds(current):
                break
            result.append(current)
        return result

    def terrain_step_allowed(self, unit: Unit, start: Position, end: Position) -> bool:
        if getattr(unit, "has_flying", False):
            return True
        cells = set(self.unit_cells_at(unit, start) + self.unit_cells_at(unit, end))
        return not any(
            other.unit_id != unit.unit_id and getattr(other, "standable_terrain", False)
            and cells.intersection(self.unit_cells(other))
            for other in self.all_units()
        )

    def explicit_path(
        self,
        unit: Unit,
        steps: list[Position],
        *,
        max_distance: int,
        exact_distance: Optional[int] = None,
        straight_only: bool = False,
        ignore_units: bool = False,
        allow_anywhere: bool = False,
        use_movement_cost: bool = False,
    ) -> list[Position]:
        ignore_units = ignore_units or unit.ignore_units_while_moving
        if unit.position is None:
            raise ActionError("单位不在战场上。")
        if not steps:
            raise ActionError("缺少移动路径。")
        path = [unit.position]
        direction: Optional[tuple[int, int]] = None
        distance_cost = 0
        for index, step in enumerate(steps):
            if not self.in_bounds(step):
                raise ActionError("移动路径超出战场边界。")
            previous = path[-1]
            if not self.terrain_step_allowed(unit, previous, step):
                raise ActionError("只有飞行单位可以移动到地形上或离开地形。")
            if allow_anywhere:
                path.append(step)
                distance_cost += 1
                continue
            dx = step.x - previous.x
            dy = step.y - previous.y
            if max(abs(dx), abs(dy)) != 1:
                raise ActionError("移动路径必须逐格相邻。")
            if straight_only:
                current_direction = (
                    0 if dx == 0 else dx // abs(dx),
                    0 if dy == 0 else dy // abs(dy),
                )
                if direction is None:
                    direction = current_direction
                elif current_direction != direction:
                    raise ActionError("该移动必须沿同一直线前进。")
            step_ignores_units = ignore_units and index < len(steps) - 1
            if not self.can_traverse_position(unit, step, ignore_units=step_ignores_units):
                raise ActionError("移动路径被阻挡。")
            distance_cost += self.normal_movement_step_cost(unit, previous, step) if use_movement_cost else 1
            path.append(step)
        if distance_cost > max_distance:
            raise ActionError("超出位移距离。")
        if exact_distance is not None and len(path) - 1 != exact_distance:
            raise ActionError(f"必须恰好位移 {exact_distance} 格。")
        return path

    def reachable_positions(
        self,
        unit: Unit,
        *,
        max_distance: int,
        exact_distance: Optional[int] = None,
        straight_only: bool = False,
        ignore_units: bool = False,
        allow_anywhere: bool = False,
        use_movement_cost: bool = False,
    ) -> list[Position]:
        ignore_units = ignore_units or unit.ignore_units_while_moving
        if unit.position is None:
            return []
        if allow_anywhere:
            return [
                Position(x, y)
                for x in range(self.width)
                for y in range(self.height)
                if self.terrain_step_allowed(unit, unit.position, Position(x, y))
                and self.can_place_unit(unit, Position(x, y), ignore=unit, mover=unit, ignore_units=False)
            ]
        if straight_only:
            result: list[Position] = []
            for direction in (
                (-1, -1),
                (-1, 0),
                (-1, 1),
                (0, -1),
                (0, 1),
                (1, -1),
                (1, 0),
                (1, 1),
            ):
                previous = unit.position
                spent = 0
                for step, candidate in enumerate(self.line_positions(unit.position, direction, max_distance), start=1):
                    if not self.terrain_step_allowed(unit, previous, candidate):
                        break
                    if not self.can_traverse_position(unit, candidate, ignore_units=ignore_units):
                        break
                    spent += self.normal_movement_step_cost(unit, previous, candidate) if use_movement_cost else 1
                    previous = candidate
                    if spent > max_distance:
                        break
                    distance_value = spent if use_movement_cost else step
                    if (
                        (exact_distance is None or distance_value == exact_distance)
                        and self.can_place_unit(unit, candidate, ignore=unit, mover=unit, ignore_units=False)
                    ):
                        result.append(candidate)
            return result
        if use_movement_cost:
            distances: dict[Position, int] = {unit.position: 0}
            queue_counter = count()
            heap: list[tuple[int, int, Position]] = [(0, next(queue_counter), unit.position)]
            while heap:
                dist, _, pos = heapq.heappop(heap)
                if dist != distances.get(pos):
                    continue
                for nxt in self.neighbors(pos):
                    if not self.terrain_step_allowed(unit, pos, nxt):
                        continue
                    step_ignores_units = ignore_units and nxt != unit.position
                    if not self.can_traverse_position(unit, nxt, ignore_units=step_ignores_units):
                        continue
                    next_dist = dist + self.normal_movement_step_cost(unit, pos, nxt)
                    if next_dist > max_distance:
                        continue
                    if next_dist >= distances.get(nxt, 10**9):
                        continue
                    distances[nxt] = next_dist
                    heapq.heappush(heap, (next_dist, next(queue_counter), nxt))
            return [
                pos
                for pos, dist in distances.items()
                if pos != unit.position
                and (exact_distance is None or dist == exact_distance)
                and self.can_place_unit(unit, pos, ignore=unit, mover=unit, ignore_units=False)
            ]
        visited = {unit.position}
        queue: deque[tuple[Position, int]] = deque([(unit.position, 0)])
        result: list[Position] = []
        while queue:
            pos, dist = queue.popleft()
            if dist >= max_distance:
                continue
            for nxt in self.neighbors(pos):
                if not self.terrain_step_allowed(unit, pos, nxt):
                    continue
                if nxt in visited:
                    continue
                if not self.can_traverse_position(unit, nxt, ignore_units=ignore_units):
                    continue
                visited.add(nxt)
                if (
                    (exact_distance is None or dist + 1 == exact_distance)
                    and self.can_place_unit(unit, nxt, ignore=unit, mover=unit, ignore_units=False)
                ):
                    result.append(nxt)
                queue.append((nxt, dist + 1))
        return result

    def find_path(
        self,
        unit: Unit,
        destination: Position,
        *,
        max_distance: int,
        exact_distance: Optional[int] = None,
        straight_only: bool = False,
        ignore_units: bool = False,
        allow_anywhere: bool = False,
        use_movement_cost: bool = False,
    ) -> list[Position]:
        ignore_units = ignore_units or unit.ignore_units_while_moving
        if unit.position is None:
            raise ActionError("单位不在战场上。")
        if destination == unit.position:
            return [unit.position]
        if not self.terrain_step_allowed(unit, unit.position, destination):
            raise ActionError("只有飞行单位可以移动到地形上或离开地形。")
        if allow_anywhere:
            return [unit.position, destination]
        if straight_only:
            dx = destination.x - unit.position.x
            dy = destination.y - unit.position.y
            if max(abs(dx), abs(dy)) > max_distance:
                raise ActionError("超出位移距离。")
            if dx != 0:
                dx = dx // abs(dx)
            if dy != 0:
                dy = dy // abs(dy)
            current = unit.position
            path = [current]
            spent = 0
            while current != destination:
                previous = current
                current = current.offset(dx, dy)
                if not self.terrain_step_allowed(unit, previous, current):
                    raise ActionError("只有飞行单位可以移动到地形上或离开地形。")
                step_ignores_units = ignore_units and current != destination
                if not self.can_traverse_position(unit, current, ignore_units=step_ignores_units):
                    raise ActionError("移动路径被阻挡。")
                spent += self.normal_movement_step_cost(unit, previous, current) if use_movement_cost else 1
                if spent > max_distance:
                    raise ActionError("超出位移距离。")
                path.append(current)
            if exact_distance is not None and len(path) - 1 != exact_distance:
                raise ActionError(f"必须恰好位移 {exact_distance} 格。")
            return path
        if use_movement_cost:
            queue_counter = count()
            heap: list[tuple[int, int, Position]] = [(0, next(queue_counter), unit.position)]
            parents: dict[Position, Optional[Position]] = {unit.position: None}
            distances: dict[Position, int] = {unit.position: 0}
            while heap:
                dist, _, pos = heapq.heappop(heap)
                if dist != distances.get(pos):
                    continue
                if pos == destination:
                    break
                for nxt in self.neighbors(pos):
                    if not self.terrain_step_allowed(unit, pos, nxt):
                        continue
                    step_ignores_units = ignore_units and nxt != destination
                    if not self.can_traverse_position(unit, nxt, ignore_units=step_ignores_units):
                        continue
                    next_dist = dist + self.normal_movement_step_cost(unit, pos, nxt)
                    if next_dist > max_distance:
                        continue
                    if next_dist >= distances.get(nxt, 10**9):
                        continue
                    parents[nxt] = pos
                    distances[nxt] = next_dist
                    heapq.heappush(heap, (next_dist, next(queue_counter), nxt))
            if destination not in parents:
                raise ActionError("找不到可行的移动路径。")
            path: list[Position] = []
            current: Optional[Position] = destination
            while current is not None:
                path.append(current)
                current = parents[current]
            path.reverse()
            if exact_distance is not None and len(path) - 1 != exact_distance:
                raise ActionError(f"必须恰好位移 {exact_distance} 格。")
            return path
        queue: deque[Position] = deque([unit.position])
        parents: dict[Position, Optional[Position]] = {unit.position: None}
        distances: dict[Position, int] = {unit.position: 0}
        while queue:
            pos = queue.popleft()
            if pos == destination:
                break
            for nxt in self.neighbors(pos):
                if not self.terrain_step_allowed(unit, pos, nxt):
                    continue
                next_dist = distances[pos] + 1
                if next_dist > max_distance or nxt in parents:
                    continue
                step_ignores_units = ignore_units and nxt != destination
                if not self.can_traverse_position(unit, nxt, ignore_units=step_ignores_units):
                    continue
                parents[nxt] = pos
                distances[nxt] = next_dist
                queue.append(nxt)
        if destination not in parents:
            raise ActionError("找不到可行的移动路径。")
        path: list[Position] = []
        current: Optional[Position] = destination
        while current is not None:
            path.append(current)
            current = parents[current]
        path.reverse()
        if exact_distance is not None and len(path) - 1 != exact_distance:
            raise ActionError(f"必须恰好位移 {exact_distance} 格。")
        return path

    def move_unit(
        self,
        unit: Unit,
        destination: Position,
        *,
        via_skill: bool = False,
        straight_only: bool = False,
        ignore_units: bool = False,
        allow_anywhere: bool = False,
        max_distance: Optional[int] = None,
        exact_distance: Optional[int] = None,
        path: Optional[list[Position]] = None,
        triggered_by_reaction: bool = False,
        tags: Optional[set[str]] = None,
        forced: bool = False,
    ) -> MoveContext:
        ignore_units = ignore_units or unit.ignore_units_while_moving
        if unit.position is None:
            raise ActionError("单位不在战场上。")
        if getattr(unit, "attached_to_unit_id", None):
            raise ActionError("附着期间不能独立移动。")
        if unit.direct_effects_blocked() and (forced or (via_skill and self.resolving_action is not None and self.resolving_action.actor_id != unit.unit_id)):
            raise ActionError(f"{unit.name} 不受外部位移效果影响。")
        if unit.skill_non_damage_effects_blocked() and via_skill:
            raise ActionError(f"{unit.name} 不受技能位移效果影响。")
        if via_skill and getattr(unit, "standable_terrain", False):
            raise ActionError("地形单位不受技能位移效果影响。")
        carried_rider = self.rider_for(unit)
        carried_rider_start = carried_rider.position if carried_rider is not None else None
        mounted_on = self.mounted_unit_for(unit)
        if destination == unit.position and path is None:
            raise ActionError("目标位置不能与当前位置相同。")
        if not self.in_bounds(destination):
            raise ActionError("目标位置超出战场边界。")
        if not self.can_place_unit(unit, destination, ignore=unit, mover=unit, ignore_units=False):
            raise ActionError("目标位置已被占用。")
        if unit.cannot_move and not forced:
            raise ActionError(f"{unit.name} 当前无法移动。")
        if unit.cannot_normal_move and not via_skill and not forced:
            raise ActionError(f"{unit.name} 当前不能进行常规移动。")
        if max_distance is None:
            max_distance = unit.remaining_normal_move_distance(self) if not via_skill and not forced else int(unit.stat("speed"))
        use_movement_cost = not via_skill and not forced
        path = (
            self.explicit_path(
                unit,
                path,
                max_distance=max_distance,
                exact_distance=exact_distance,
                straight_only=straight_only,
                ignore_units=ignore_units,
                allow_anywhere=allow_anywhere,
                use_movement_cost=use_movement_cost,
            )
            if path is not None
            else self.find_path(
                unit,
                destination,
                max_distance=max_distance,
                exact_distance=exact_distance,
                straight_only=straight_only,
                ignore_units=ignore_units,
                allow_anywhere=allow_anywhere,
                use_movement_cost=use_movement_cost,
            )
        )
        destination = path[-1]
        ctx = MoveContext(
            unit=unit,
            start=unit.position,
            end=destination,
            path=path,
            via_skill=via_skill,
            triggered_by_reaction=triggered_by_reaction,
            tags=tags or set(),
        )
        if not via_skill and not forced and not triggered_by_reaction and unit.is_clone:
            if not self.clone_can_act_this_turn(unit):
                raise ActionError("本武将回合已有另一个分身行动。")
        if not via_skill and not forced and not triggered_by_reaction:
            previous_payment = getattr(unit, "_paying_skill_cost", False)
            unit._paying_skill_cost = True
            try:
                for component in list(unit.iter_components()):
                    component.on_before_normal_move(self, ctx)
            finally:
                if previous_payment:
                    unit._paying_skill_cost = previous_payment
                else:
                    unit.__dict__.pop("_paying_skill_cost", None)
        if not via_skill and not forced and not triggered_by_reaction and unit.is_clone:
            unit._clone_action_turn = self.turn_number
        action = self.resolving_action
        if (via_skill and action is not None and action.action_type in {"skill", "reaction_skill", "skill_effect"}
                and action.actor_id != unit.unit_id and not action.payload.get("from_field_effect")
                and self.destroy_clone_for_skill_effect(unit, source=self.units.get(action.actor_id), action_name=action.display_name)):
            ctx.end, ctx.path = ctx.start, [ctx.start]
            return ctx
        unit.position = destination
        if hasattr(unit, "_resolution_actual_position"):
            unit._resolution_actual_position = destination
        if carried_rider is not None and carried_rider_start is not None:
            delta_x = destination.x - ctx.start.x
            delta_y = destination.y - ctx.start.y
            carried_rider.position = carried_rider_start.offset(delta_x, delta_y)
            carried_rider.moved_this_turn = True
        if mounted_on is not None and unit.position not in self.unit_cells(mounted_on):
            self.clear_mounted_state(unit)
        unit.moved_this_turn = unit.moved_this_turn or len(path) > 1
        if not triggered_by_reaction:
            if not via_skill and not forced:
                unit.normal_move_steps_used += max(0, len(path) - 1)
                unit.normal_move_actions_used += 1
                unit.move_used = True
        self.log(f"{unit.name} 移动了。")
        for effect in list(self.field_effects):
            effect.on_unit_moved(self, ctx)
        for other in self.all_units():
            for component in list(other.iter_components()):
                component.on_unit_moved(self, ctx)
        self.enforce_sandstorm_stealth_rules()
        self.cleanup_dead_units()
        if self.resolving_action is None and self.pending_chain is None:
            self.advance_followup_actions()
        return ctx

    def validate_target(
        self,
        actor: Unit,
        target: Unit,
        *,
        action_name: str,
        is_skill: bool,
        is_hostile: bool,
        ignore_shield: bool = False,
        half_ignore_shield: bool = False,
        ignore_magic_immunity: bool = False,
        ignore_physical_immunity: bool = False,
        from_field_effect: bool = False,
        cannot_evade: bool = False,
        ignore_targeting_restrictions: bool = False,
        resolve_defenses: bool = True,
        damage_target: bool = False,
        tags: Optional[set[str]] = None,
    ) -> TargetContext:
        target = self.effect_recipient(target)
        ctx = TargetContext(
            actor=actor,
            target=target,
            action_name=action_name,
            is_skill=is_skill,
            is_hostile=is_hostile,
            ignore_shield=ignore_shield,
            half_ignore_shield=half_ignore_shield,
            ignore_magic_immunity=ignore_magic_immunity,
            ignore_physical_immunity=ignore_physical_immunity,
            from_field_effect=from_field_effect,
            cannot_evade=cannot_evade,
            damage_target=damage_target,
            tags=tags or set(),
        )
        if not target.alive or target.position is None or target.banished:
            ctx.cancelled = True
            ctx.reason = "目标暂时不在战场上。"
            return ctx
        if not ignore_targeting_restrictions and target.cannot_be_targeted and is_hostile:
            ctx.cancelled = True
            ctx.reason = f"{target.name} 当前无法被选中。"
            return ctx
        for component in list(actor.iter_components()):
            component.on_targeted(self, ctx)
        for effect in list(self.field_effects):
            effect.on_targeted(self, ctx)
        for component in list(target.iter_components()):
            component.on_targeted(self, ctx)
        for effect in list(self.field_effects):
            effect.on_after_target_modifiers(self, ctx)
        if ctx.cancelled:
            return ctx
        if (
            is_hostile
            and is_skill
            and not ctx.from_field_effect
            and target.magic_immunity
            and not ctx.ignore_magic_immunity
        ):
            ctx.cancelled = True
            self.emit_defense_visual_event(
                source=actor,
                target=target,
                action_name=action_name,
                defense_reason="magic_immunity",
            )
            ctx.reason = f"{target.name} 处于魔免状态。"
            return ctx
        if (
            is_hostile
            and not is_skill
            and not ctx.from_field_effect
            and bool(getattr(target, "physical_immunity", False))
            and not ctx.ignore_physical_immunity
        ):
            ctx.cancelled = True
            self.emit_defense_visual_event(
                source=actor,
                target=target,
                action_name=action_name,
                defense_reason="physical_immunity",
            )
            ctx.reason = f"{target.name} 处于物免状态。"
            return ctx
        if not resolve_defenses:
            return ctx
        if is_hostile and target.total_shields() > 0:
            if ctx.ignore_shield or ctx.half_ignore_shield:
                return ctx
            target.consume_one_shield()
            ctx.shield_consumed = True
            ctx.cancelled = True
            ctx.reason = f"{target.name} 的护盾抵消了【{action_name}】。"
            self.emit_defense_visual_event(
                source=actor,
                target=target,
                action_name=action_name,
                defense_reason="shield",
            )
            return ctx
        if is_hostile and target.dodge_charges > 0 and not ctx.cannot_evade:
            target.dodge_charges -= 1
            ctx.cancelled = True
            ctx.reason = f"{target.name} 闪避了【{action_name}】。"
            self.emit_defense_visual_event(
                source=actor,
                target=target,
                action_name=action_name,
                defense_reason="dodge",
            )
            return ctx
        if ctx.is_skill and not ctx.damage_target and not ctx.from_field_effect:
            if self.destroy_clone_for_skill_effect(target, source=actor, action_name=action_name):
                ctx.destroyed_as_clone = True
                ctx.cancelled = True
                ctx.reason = "分身已因技能效果消散。"
        return ctx

    def resolve_damage(self, ctx: DamageContext) -> DamageContext:
        ctx.target = self.effect_recipient(ctx.target)
        if ctx.declared_source_attack is None and ctx.source is not None:
            queued = self.resolving_action
            if queued is not None and queued.actor_id == ctx.source.unit_id:
                ctx.declared_source_attack = queued.payload.get("declared_source_attack")
        with self.suppress_logs_for_stealth(ctx.target):
            if ctx.target.banished:
                ctx.cancelled = True
                ctx.reason = "目标不在战场上。"
                self.log_public_event(ctx.reason, source=ctx.source, target=ctx.target)
                return ctx

            def notify_cancelled() -> None:
                for effect in list(self.field_effects):
                    effect.on_damage_cancelled(self, ctx)
                if ctx.source is not None:
                    for component in list(ctx.source.iter_components()):
                        component.on_damage_cancelled(self, ctx)
                for component in list(ctx.target.iter_components()):
                    component.on_damage_cancelled(self, ctx)

            def notify_confirmed_destruction() -> None:
                if ctx.target.alive or ctx.source is None:
                    return
                for component in list(ctx.source.iter_components()):
                    hook = getattr(component, "on_confirmed_damage_destruction", None)
                    if callable(hook):
                        hook(self, ctx)

            for effect in list(self.field_effects):
                effect.on_before_damage(self, ctx)
            if ctx.source is not None:
                for component in list(ctx.source.iter_components()):
                    component.on_before_damage(self, ctx)
            for component in list(ctx.target.iter_components()):
                component.on_before_damage(self, ctx)
            if not ctx.cancelled and ctx.raw_damage is None and ctx.area_cell_hits > 1:
                ctx.attack_power += max(0, int(ctx.area_cell_hits) - 1)
            for effect in list(self.field_effects):
                effect.on_after_damage_modifiers(self, ctx)
            if ctx.cancelled:
                if ctx.reason:
                    self.log_public_event(ctx.reason, source=ctx.source, target=ctx.target)
                notify_cancelled()
                return ctx
            if (
                ctx.is_skill
                and not ctx.from_field_effect
                and ctx.target.magic_immunity
                and not ctx.ignore_magic_immunity
            ):
                ctx.cancelled = True
                self.emit_defense_visual_event(
                    source=ctx.source,
                    target=ctx.target,
                    action_name=ctx.action_name,
                    defense_reason="magic_immunity",
                )
                ctx.reason = f"{ctx.target.name} 处于魔免状态。"
                self.log_public_event(ctx.reason, source=ctx.source, target=ctx.target)
                notify_cancelled()
                return ctx
            if (
                not ctx.is_skill
                and not ctx.from_field_effect
                and bool(getattr(ctx.target, "physical_immunity", False))
                and not ctx.ignore_physical_immunity
            ):
                ctx.cancelled = True
                self.emit_defense_visual_event(
                    source=ctx.source,
                    target=ctx.target,
                    action_name=ctx.action_name,
                    defense_reason="physical_immunity",
                )
                ctx.reason = f"{ctx.target.name} 处于物免状态。"
                self.log_public_event(ctx.reason, source=ctx.source, target=ctx.target)
                notify_cancelled()
                return ctx
            if ctx.target.total_shields() > 0:
                if ctx.ignore_shield:
                    ctx.target.consume_one_shield()
                    ctx.shield_consumed = True
                    self.record_shield_break_summary(ctx.source, ctx.target, ctx.action_name)
                    self.emit_defense_visual_event(
                        source=ctx.source,
                        target=ctx.target,
                        action_name=ctx.action_name,
                        defense_reason="shield_break",
                    )
                    self.log_public_event(
                        f"{ctx.target.name} 的 1 层护盾被【{ctx.action_name}】贯穿并打碎。",
                        source=ctx.source,
                        target=ctx.target,
                    )
                elif ctx.half_ignore_shield:
                    ctx.target.consume_one_shield()
                    ctx.shield_consumed = True
                    self.record_shield_break_summary(ctx.source, ctx.target, ctx.action_name)
                    if ctx.raw_damage is None:
                        ctx.attack_power = max(0.0, ctx.attack_power - 1)
                    self.emit_defense_visual_event(
                        source=ctx.source,
                        target=ctx.target,
                        action_name=ctx.action_name,
                        defense_reason="shield_half_break",
                    )
                    self.log_public_event(
                        f"{ctx.target.name} 的 1 层护盾被【{ctx.action_name}】半破魔打碎。",
                        source=ctx.source,
                        target=ctx.target,
                    )
                else:
                    ctx.target.consume_one_shield()
                    ctx.shield_consumed = True
                    self.record_shield_break_summary(ctx.source, ctx.target, ctx.action_name)
                    ctx.cancelled = True
                    ctx.modifier_receipts.add("shield_blocked_damage")
                    ctx.reason = f"{ctx.target.name} 的护盾挡下了伤害。"
                    self.emit_defense_visual_event(
                        source=ctx.source,
                        target=ctx.target,
                        action_name=ctx.action_name,
                        defense_reason="shield",
                    )
                    self.log_public_event(ctx.reason, source=ctx.source, target=ctx.target)
                    notify_cancelled()
                    return ctx
            if ctx.target.dodge_charges > 0 and not ctx.cannot_evade:
                ctx.target.dodge_charges -= 1
                ctx.cancelled = True
                ctx.reason = f"{ctx.target.name} 闪避了伤害。"
                self.emit_defense_visual_event(
                    source=ctx.source,
                    target=ctx.target,
                    action_name=ctx.action_name,
                    defense_reason="dodge",
                )
                self.log_public_event(ctx.reason, source=ctx.source, target=ctx.target)
                notify_cancelled()
                return ctx
            for component in list(ctx.target.iter_components()):
                component.on_final_damage(self, ctx)
            if ctx.cancelled:
                if ctx.reason:
                    self.log_public_event(ctx.reason, source=ctx.source, target=ctx.target)
                notify_cancelled()
                return ctx
            if ctx.target.is_clone:
                ctx.destroyed_as_clone = True
                ctx.raw_damage = 0.0
                ctx.target.current_hp = 0.0
                ctx.target.alive = False
                self.log_public_event(
                    f"{ctx.target.name} 是分身，只要受到伤害就会直接破坏。",
                    source=ctx.source,
                    target=ctx.target,
                )
                for effect in list(self.field_effects):
                    effect.on_after_damage(self, ctx)
                if ctx.source is not None:
                    for component in list(ctx.source.iter_components()):
                        component.on_after_damage(self, ctx)
                for component in list(ctx.target.iter_components()):
                    component.on_after_damage(self, ctx)
                notify_confirmed_destruction()
                self.cleanup_dead_units()
                return ctx
            damage_amount = self.damage_rule.calculate_damage(ctx.attack_power, ctx.target.stat("defense"))
            if ctx.raw_damage is not None:
                damage_amount = ctx.raw_damage
            ctx.raw_damage = ctx.target.damage_fraction_after_limits(damage_amount)
            old_hp = ctx.target.current_hp
            if ctx.raw_damage >= old_hp and old_hp > 0:
                retained = [value for component in list(ctx.target.iter_components())
                            if (value := component.lethal_damage_remaining_hp(self, ctx)) is not None]
                if retained:
                    remaining_hp = min(old_hp, max(0.0, max(retained)))
                    ctx.raw_damage = max(0.0, old_hp - remaining_hp)
            ctx.target.take_damage_fraction(ctx.raw_damage)
            actual_damage = round(max(0.0, old_hp - ctx.target.current_hp), 4)
            ctx.actual_damage = actual_damage
            if actual_damage > 0:
                for observer in list(self.all_units()):
                    if observer.alive and observer.position is not None and not observer.banished:
                        for component in list(observer.iter_components()):
                            component.on_battle_damage(self, ctx)
            self.record_damage_summary(ctx, actual_damage)
            self.log_public_event(
                f"{ctx.target.name} 受到 {ctx.raw_damage} 点伤害。",
                source=ctx.source,
                target=ctx.target,
            )
            for effect in list(self.field_effects):
                effect.on_after_damage(self, ctx)
            if ctx.source is not None:
                for component in list(ctx.source.iter_components()):
                    component.on_after_damage(self, ctx)
            for component in list(ctx.target.iter_components()):
                component.on_after_damage(self, ctx)
            if not ctx.target.alive:
                self.record_defeat_summary(ctx.source, ctx.target, ctx.action_name)
            notify_confirmed_destruction()
            self.cleanup_dead_units()
            return ctx

    def expire_chain_temporary_statuses(self) -> None:
        for unit in self.all_units():
            unit.temporary_shields = 0
            for status in list(unit.statuses):
                if getattr(status, "expire_with_chain", False):
                    unit.remove_status(status, self)

    def heal(self, ctx: HealContext) -> HealContext:
        ctx.target = self.effect_recipient(ctx.target)
        with self.suppress_logs_for_stealth(ctx.target):
            for effect in list(self.field_effects):
                effect.on_before_heal(self, ctx)
            if ctx.source is not None:
                for component in list(ctx.source.iter_components()):
                    component.on_before_heal(self, ctx)
            for component in list(ctx.target.iter_components()):
                component.on_before_heal(self, ctx)
            if ctx.cancelled:
                return ctx
            if ctx.target.cannot_heal:
                ctx.cancelled = True
                ctx.reason = f"{ctx.target.name} 当前无法回复。"
                return ctx
            action = self.resolving_action
            if (action is not None and action.action_type in {"skill", "reaction_skill", "skill_effect"}
                    and ctx.source is not None and action.actor_id == ctx.source.unit_id and ctx.effect_source is None
                    and not action.payload.get("from_field_effect")
                    and self.destroy_clone_for_skill_effect(ctx.target, source=ctx.source, action_name=ctx.action_name)):
                ctx.cancelled = True
                return ctx
            old_hp = ctx.target.current_hp
            exempt = any(component.permits_immune_heal(ctx) for component in ctx.target.iter_components())
            ctx.target.heal_fraction(ctx.amount, ignore_effect_immunity=exempt)
            gained = round(ctx.target.current_hp - old_hp, 4)
            self.record_heal_summary(ctx, gained)
            self.log_public_event(
                f"{ctx.target.name} 回复了 {gained} 点生命。",
                source=ctx.source,
                target=ctx.target,
            )
            for effect in list(self.field_effects):
                effect.on_after_heal(self, ctx)
            if ctx.source is not None:
                for component in list(ctx.source.iter_components()):
                    component.on_after_heal(self, ctx)
            for component in list(ctx.target.iter_components()):
                component.on_after_heal(self, ctx)
            return ctx

    def basic_attack(self, actor: Unit, target: Unit) -> None:
        return self.basic_attack_with_payload(actor, target, None)

    def basic_attack_with_payload(
        self,
        actor: Unit,
        target: Unit,
        payload: Optional[dict[str, Any]] = None,
    ) -> None:
        if not actor.can_take_turn_actions(self):
            raise ActionError("这个单位当前不能行动。")
        resolved_payload = self.resolved_basic_attack_payload(actor, payload)
        attack_cost = int(resolved_payload.get("attack_cost", 1))
        ok, reason = self.basic_attack_resource_allowed(actor, resolved_payload)
        if not ok:
            raise ActionError(reason)
        ignore_stealth = self.attack_ignores_stealth(actor, target)
        ok, reason = self.attack_target_allowed(actor, target, ignore_stealth=ignore_stealth, payload=resolved_payload)
        if not ok:
            raise ActionError(reason)
        actor.attacks_used += attack_cost
        actor.actions_taken_this_turn.append("attack")
        if not resolved_payload.get("queued_resolution"):
            if not all(component.before_basic_attack_resolution(self, actor, resolved_payload) for component in list(actor.iter_components())):
                resolved_payload["basic_attack_cancelled"] = True
                actor.consume_attack_attempt_buffs(self)
                actor.notify_basic_attack_finished(self, resolved_payload, [], missed=True)
                return
        damage_ctx = self.resolve_attack_damage(
            actor,
            target,
            action_name=str(resolved_payload.get("attack_name") or "普攻"),
            payload=resolved_payload,
        )
        actor.notify_basic_attack_finished(
            self,
            resolved_payload,
            [damage_ctx] if damage_ctx is not None else [],
            missed=damage_ctx is None,
        )
        self.check_win_condition()

    def basic_attack_area_cells_for_payload(
        self,
        actor: Unit,
        payload: Optional[dict[str, Any]] = None,
    ) -> Optional[list[Position]]:
        if payload and payload.get("attack_variant") == "allied_heal":
            return None
        if payload and payload.get("queued_resolution"):
            declared_cells = self.payload_positions(payload, "attack_cells")
            if declared_cells:
                return declared_cells
        resolved_payload = self.resolved_basic_attack_payload(actor, payload)
        override = next((component for component in actor.iter_components()
                         if getattr(component, "overrides_basic_attack_shape", False)), None)
        if override is not None:
            return override.basic_attack_area_cells(self, actor, resolved_payload)
        for component in actor.iter_components():
            cells = component.basic_attack_area_cells(self, actor, resolved_payload)
            if cells is not None:
                return cells
        return None

    def basic_area_attack_with_payload(
        self,
        actor: Unit,
        payload: Optional[dict[str, Any]] = None,
        cells: Optional[list[Position]] = None,
    ) -> None:
        if not actor.can_take_turn_actions(self):
            raise ActionError("这个单位当前不能行动。")
        resolved_payload = self.resolved_basic_attack_payload(actor, payload)
        area_cells = cells if cells is not None else self.basic_attack_area_cells_for_payload(actor, resolved_payload)
        if area_cells is None:
            raise ActionError("这次攻击需要指定攻击区域。")
        attack_cost = int(resolved_payload.get("attack_cost", 1))
        ok, reason = self.basic_attack_resource_allowed(actor, resolved_payload)
        if not ok:
            raise ActionError(reason)
        block_reason = self.shared_stealth_action_block_reason(actor)
        if block_reason:
            raise ActionError(block_reason)
        if actor.cannot_attack:
            raise ActionError(f"{actor.name} 当前不能攻击。")
        for effect in list(self.field_effects):
            ok, reason = effect.can_attack_target(self, actor, actor)
            if not ok:
                raise ActionError(reason)
        attack_payload = dict(resolved_payload)
        attack_payload["attack_cells"] = [cell.to_dict() for cell in area_cells]
        actor.attacks_used += attack_cost
        actor.actions_taken_this_turn.append("attack")
        affects_allies = bool(attack_payload.get("friendly_fire")) or any(
            component.basic_attack_area_affects_allies(self, actor, attack_payload)
            for component in actor.iter_components()
        )
        forced_allied_target_id = str(attack_payload.get("forced_allied_attack_target_id") or "")
        impact = None
        raw_impact = attack_payload.get("impact_cell")
        if isinstance(raw_impact, dict) and raw_impact.get("x") is not None and raw_impact.get("y") is not None:
            impact = Position(int(raw_impact["x"]), int(raw_impact["y"]))
        from wujiang.tactical.engine.siege import is_siege_structure, structure_hit_by_impact

        targets = []
        for unit in self.effect_units_at_cells(area_cells):
            if (
                not affects_allies
                and unit.player_id == actor.player_id
                and unit.unit_id != forced_allied_target_id
            ):
                continue
            if attack_payload.get("structures_need_direct_hit") and is_siege_structure(unit):
                if not structure_hit_by_impact(self, unit, impact):
                    continue
            targets.append(unit)
        if not targets:
            actor.consume_attack_attempt_buffs(self)
            attack_name = str(attack_payload.get("attack_name") or "普攻")
            self.log(f"{actor.name} 的【{attack_name}】没有命中有效目标。")
            actor.notify_basic_attack_finished(self, attack_payload, [], missed=True)
            return
        attack_name = str(attack_payload.get("attack_name") or "普攻")
        damage_contexts: list[DamageContext] = []
        for target in targets:
            damage_ctx = self.resolve_attack_damage(
                actor,
                target,
                action_name=attack_name,
                tags={"area_attack"},
                payload=attack_payload,
            )
            if damage_ctx is not None:
                damage_contexts.append(damage_ctx)
        actor.notify_basic_attack_finished(self, attack_payload, damage_contexts, missed=not damage_contexts)
        self.check_win_condition()

    def attack_ignores_shield(self, actor: Unit, target: Unit, *, payload: Optional[dict[str, Any]] = None) -> bool:
        ignore_shield, _ = self.attack_shield_flags(actor, target, payload=payload)
        return ignore_shield

    def attack_shield_flags(
        self,
        actor: Unit,
        target: Unit,
        *,
        payload: Optional[dict[str, Any]] = None,
    ) -> tuple[bool, bool]:
        resolved_payload = self.resolved_basic_attack_payload(actor, payload)
        ctx = TargetContext(
            actor=actor,
            target=target,
            action_name="普攻",
            is_skill=False,
            is_hostile=True,
            tags={"attack"},
        )
        for component in list(actor.iter_components()):
            component.on_targeted(self, ctx)
        for effect in list(self.field_effects):
            effect.on_targeted(self, ctx)
        if resolved_payload.get("ignore_shield"):
            ctx.ignore_shield = True
        if resolved_payload.get("half_ignore_shield"):
            ctx.half_ignore_shield = True
        return ctx.ignore_shield, ctx.half_ignore_shield

    def attack_ignores_stealth(self, actor: Unit, target: Unit) -> bool:
        return False

    def attack_target_allowed(
        self,
        actor: Unit,
        target: Unit,
        *,
        ignore_stealth: bool = False,
        payload: Optional[dict[str, Any]] = None,
    ) -> tuple[bool, str]:
        block_reason = self.shared_stealth_action_block_reason(actor)
        if block_reason:
            return False, block_reason
        if actor.cannot_attack:
            return False, f"{actor.name} 当前不能攻击。"
        if not actor.alive or actor.banished or actor.position is None or target.position is None:
            return False, "攻击对象不在战场上。"
        ok, reason = self.unit_can_be_selected(target, actor=actor, ignore_stealth=ignore_stealth)
        if not ok:
            return False, reason
        for effect in list(self.field_effects):
            ok, reason = effect.can_attack_target(self, actor, target)
            if not ok:
                return False, reason
        origins = self.unit_cells(actor)
        for component in actor.iter_components():
            override = component.basic_attack_origins(self, actor, payload)
            if override is not None:
                origins = override
        declared_resolution = self.is_declared_resolution(actor, payload or {}, {"attack"})
        target_cells = self.unit_cells(target)
        if not declared_resolution and (
            not origins or min(origin.distance_to(cell) for origin in origins for cell in target_cells) > actor.targeting_range()
        ):
            return False, "目标超出普攻范围。"
        resolved_payload = self.resolved_basic_attack_payload(actor, payload)
        area_attack = (resolved_payload.get("attack_variant") != "allied_heal"
                       and self.basic_attack_area_cells_for_payload(actor, resolved_payload) is not None)
        ignore_line = any(component.ignores_direct_unit_target_line(self, actor) for component in actor.iter_components())
        if not declared_resolution and not area_attack and not ignore_line and not any(
            origin.distance_to(cell) <= actor.targeting_range() and self.cells_are_straight_aligned(origin, cell)
            for origin in origins for cell in target_cells
        ):
            return False, "目标不在普攻直线上。"
        from wujiang.tactical.engine.siege import siege_min_attack_range

        min_range = siege_min_attack_range(actor)
        if min_range and self.distance_between_units(actor, target) < min_range:
            return False, f"{actor.name} 无法对范 {min_range - 1} 开火。"
        if payload is not None and payload.get("x") is not None and payload.get("y") is not None:
            clicked = Position(int(payload["x"]), int(payload["y"]))
            if clicked not in target_cells:
                return False, "所点格子没有命中该目标。"
            if not declared_resolution and min(origin.distance_to(clicked) for origin in origins) > actor.targeting_range():
                return False, "所点目标格超出普攻范围。"
            if not declared_resolution and not area_attack and not ignore_line and not any(
                self.cells_are_straight_aligned(origin, clicked) for origin in origins
            ):
                return False, "所点目标格不在普攻直线上。"
        for component in list(actor.iter_components()):
            ok, reason = component.can_attack_target(self, actor, target)
            if not ok:
                return False, reason
        component_payload = None if payload is None else resolved_payload
        for component in list(actor.iter_components()):
            ok, reason = component.can_attack_target_with_payload(self, actor, target, component_payload)
            if not ok:
                return False, reason
        return True, ""

    def allied_basic_heal_amount(self, actor: Unit, target: Unit) -> float:
        if actor.player_id != target.player_id:
            return 0.0
        return max((component.allied_basic_heal_amount(self, actor, target) for component in actor.iter_components()), default=0.0)

    def resolve_attack_damage(
        self,
        actor: Unit,
        target: Unit,
        *,
        action_name: str,
        tags: Optional[set[str]] = None,
        payload: Optional[dict[str, Any]] = None,
    ) -> Optional[DamageContext]:
        with self.suppress_logs_for_stealth(target):
            attack_payload = dict(payload or {})
            if tags and "area_attack" not in tags:
                attack_payload["reaction_attack"] = True
            resolved_payload = self.resolved_basic_attack_payload(actor, attack_payload)
            attack_tags = {"attack"}
            if tags:
                attack_tags.update(tags)
            attack_tags.update(set(resolved_payload.get("attack_tags", [])))
            if target.player_id == actor.player_id:
                supported = False
                for component in actor.iter_components():
                    supported = component.resolve_allied_basic_support(self, actor, target) or supported
                if supported:
                    actor.consume_attack_attempt_buffs(self)
                    return DamageContext(source=actor, target=target, attack_power=0, is_skill=False,
                                         action_name="友方普攻支援", raw_damage=0, cancelled=True, tags=attack_tags)
            healing = self.allied_basic_heal_amount(actor, target)
            if target.player_id == actor.player_id and (healing > 0 or resolved_payload.get("allied_heal_attack")):
                if healing > 0:
                    self.heal(HealContext(source=actor, target=target, amount=healing, action_name="攻击己方加血"))
                else:
                    self.log("原声明的友方治疗因共享资格失效而落空。")
                actor.consume_attack_attempt_buffs(self)
                return DamageContext(source=actor, target=target, attack_power=0, is_skill=False,
                                     action_name="攻击己方加血", raw_damage=0, cancelled=True, tags=attack_tags)
            target_ctx = self.validate_target(
                actor,
                target,
                action_name=action_name,
                is_skill=False,
                is_hostile=True,
                ignore_targeting_restrictions=bool(resolved_payload.get("area_attack")),
                ignore_magic_immunity=bool(resolved_payload.get("ignore_magic_immunity")),
                ignore_physical_immunity=bool(resolved_payload.get("ignore_physical_immunity")),
                resolve_defenses=False,
                cannot_evade=bool(resolved_payload.get("cannot_evade")),
                tags=attack_tags,
            )
            if target_ctx.cancelled:
                self.log_public_event(target_ctx.reason, source=actor, target=target)
                actor.consume_attack_attempt_buffs(self)
                return None
            attack_power = actor.stat("attack") + float(resolved_payload.get("attack_bonus", 0.0) or 0.0)
            if resolved_payload.get("attack_power_override") is not None:
                attack_power = float(resolved_payload["attack_power_override"])
            if resolved_payload.get("siege_shell"):
                attack_tags.add("siege_shell")
            damage_ctx = DamageContext(
                source=actor,
                target=target,
                attack_power=attack_power,
                is_skill=False,
                action_name=action_name,
                ignore_shield=bool(target_ctx.ignore_shield or resolved_payload.get("ignore_shield")),
                half_ignore_shield=bool(target_ctx.half_ignore_shield or resolved_payload.get("half_ignore_shield")),
                ignore_magic_immunity=bool(target_ctx.ignore_magic_immunity or resolved_payload.get("ignore_magic_immunity")),
                ignore_physical_immunity=bool(target_ctx.ignore_physical_immunity or resolved_payload.get("ignore_physical_immunity")),
                siege_shell=bool(resolved_payload.get("siege_shell")),
                cannot_evade=target_ctx.cannot_evade,
                tags=set(target_ctx.tags),
                area_cell_hits=1 if resolved_payload.get("single_hit_per_recipient") else max(1, self.unit_hit_count_for_cells(target, self.payload_positions(resolved_payload, "attack_cells"))),
            )
            self.resolve_damage(damage_ctx)
            return damage_ctx

    def unit_can_be_selected(
        self,
        unit: Unit,
        *,
        actor: Unit | None = None,
        ignore_stealth: bool = False,
    ) -> tuple[bool, str]:
        if not unit.alive or unit.position is None or unit.banished:
            return False, "目标暂时不在战场上。"
        if getattr(unit, "attached_to_unit_id", None):
            return False, f"{unit.name} 正在附着，无法被选中。"
        if unit.cannot_be_targeted and not ignore_stealth:
            return False, f"{unit.name} 当前无法被选中。"
        if (
            unit.has_status("隐身")
            and not ignore_stealth
            and (actor is None or actor.player_id != unit.player_id)
        ):
            return False, f"{unit.name} 当前处于隐身状态。"
        return True, ""

    def require_selectable_unit(
        self,
        unit: Unit,
        *,
        actor: Unit | None = None,
        action_name: str,
        ignore_stealth: bool = False,
        queued_resolution: bool = False,
    ) -> None:
        ok, reason = self.unit_can_be_selected(unit, actor=actor, ignore_stealth=ignore_stealth)
        if ok:
            return
        if queued_resolution:
            raise ActionMiss(reason or f"【{action_name}】落在原定格上，没有命中有效目标。")
        raise ActionError(reason or f"{unit.name} 当前无法作为【{action_name}】的目标。")

    def filter_preview_targets(
        self,
        actor: Unit,
        preview: dict[str, Any],
        *,
        ignore_stealth: bool = False,
        replace_cells: bool = False,
        require_line_targeting: bool = False,
        line_target_range: Optional[int] = None,
    ) -> dict[str, Any]:
        sanitized = dict(preview)
        target_ids: list[str] = []
        target_cells: list[dict[str, int]] = []
        max_distance = actor.targeting_range() if line_target_range is None else line_target_range
        for unit_id in preview.get("target_unit_ids", []):
            unit = self.units.get(unit_id)
            if unit is None or unit.position is None:
                continue
            ok, _ = self.unit_can_be_selected(unit, actor=actor, ignore_stealth=ignore_stealth)
            if not ok:
                continue
            if require_line_targeting and not self.unit_target_in_range_and_line(actor, unit, max_distance):
                continue
            target_ids.append(unit.unit_id)
            target_cells.extend(cell.to_dict() for cell in self.unit_cells(unit))
        sanitized["target_unit_ids"] = target_ids
        if replace_cells:
            sanitized["cells"] = target_cells
        return sanitized

    def is_forced_movement_blocked(self, position: Position) -> bool:
        return any(effect.blocks_forced_movement(self, position) for effect in self.field_effects)

    def normal_movement_step_cost(self, unit: Unit, start: Position, end: Position) -> int:
        cost = 1
        for effect in list(self.field_effects):
            cost = effect.normal_movement_step_cost(self, unit, start, end, cost)
        for component in list(unit.iter_components()):
            cost = component.normal_movement_step_cost(self, unit, start, end, cost)
        return max(1, int(cost))

    def declared_source_position(self, payload: dict[str, Any]) -> Optional[Position]:
        if payload.get("declared_source_x") is None or payload.get("declared_source_y") is None:
            return None
        return Position(int(payload["declared_source_x"]), int(payload["declared_source_y"]))

    def declared_target_position(self, payload: dict[str, Any]) -> Optional[Position]:
        if payload.get("declared_target_x") is None or payload.get("declared_target_y") is None:
            return None
        return Position(int(payload["declared_target_x"]), int(payload["declared_target_y"]))

    def payload_positions(self, payload: dict[str, Any], key: str) -> list[Position]:
        cells = payload.get(key)
        if not isinstance(cells, list):
            return []
        result: list[Position] = []
        for cell in cells:
            if not isinstance(cell, dict) or cell.get("x") is None or cell.get("y") is None:
                raise ActionError("坐标格式不正确。")
            result.append(Position(int(cell["x"]), int(cell["y"])))
        return result

    def declared_cell_for_target(
        self,
        actor: Unit,
        target: Unit,
        payload: dict[str, Any],
    ) -> Optional[Position]:
        target_cells = self.unit_cells(target)
        if not target_cells:
            return target.position
        if payload.get("x") is not None and payload.get("y") is not None:
            clicked = Position(int(payload["x"]), int(payload["y"]))
            if clicked in target_cells:
                return clicked
        existing = self.declared_target_position(payload)
        if existing is not None and existing in target_cells:
            return existing
        if target.position in target_cells:
            return target.position
        actor_cells = self.unit_cells(actor)
        if not actor_cells and actor.position is not None:
            actor_cells = self.unit_cells_at(actor, actor.position)
        if actor_cells:
            return min(
                target_cells,
                key=lambda cell: (
                    min(origin.distance_to(cell) for origin in actor_cells),
                    cell.y,
                    cell.x,
                ),
            )
        return sorted(target_cells, key=lambda cell: (cell.y, cell.x))[0]

    def payload_target_unit_ids(self, payload: dict[str, Any]) -> list[str]:
        target_ids = payload.get("target_unit_ids")
        if isinstance(target_ids, list):
            result: list[str] = []
            seen: set[str] = set()
            for unit_id in target_ids:
                unit_key = str(unit_id or "").strip()
                if not unit_key or unit_key in seen:
                    continue
                seen.add(unit_key)
                result.append(unit_key)
            return result
        target_id = payload.get("target_unit_id")
        if target_id:
            return [str(target_id)]
        return []

    def resolve_declared_target_unit(
        self,
        actor: Unit,
        payload: dict[str, Any],
        *,
        ignore_stealth: bool = False,
    ) -> Optional[Unit]:
        preferred_ids = self.payload_target_unit_ids(payload)
        declared = self.declared_target_position(payload)
        if declared is not None:
            for unit_id in preferred_ids:
                preferred = self.selectable_unit_at(
                    declared,
                    actor=actor,
                    ignore_stealth=ignore_stealth,
                    preferred_unit_id=unit_id,
                )
                if preferred is not None:
                    return self.effect_recipient(preferred)
            selected = self.selectable_unit_at(declared, actor=actor, ignore_stealth=ignore_stealth)
            return self.effect_recipient(selected) if selected is not None else None
        for unit_id in preferred_ids:
            unit = self.units.get(unit_id)
            if unit is None:
                continue
            ok, _ = self.unit_can_be_selected(unit, actor=actor, ignore_stealth=ignore_stealth)
            if ok:
                return self.effect_recipient(unit)
        return None

    def resolve_from_declared_origin(
        self,
        actor: Unit,
        payload: dict[str, Any],
        resolver: Any,
    ) -> Any:
        declared = None if payload.get("_attack_pre_move_done") else self.declared_source_position(payload)
        if declared is None or actor.position is None or actor.position == declared:
            return resolver()
        actual_position = actor.position
        previous_actual = getattr(actor, "_resolution_actual_position", None)
        actor._resolution_actual_position = previous_actual or actual_position
        actor.position = declared
        try:
            result = resolver()
        except Exception:
            if actor.alive and not actor.banished and actor.position is not None:
                actor.position = getattr(actor, "_resolution_actual_position", actual_position)
            raise
        finally:
            resolved_actual = getattr(actor, "_resolution_actual_position", actual_position)
            if previous_actual is None:
                actor.__dict__.pop("_resolution_actual_position", None)
            else:
                actor._resolution_actual_position = previous_actual
        if actor.position == declared and resolved_actual == actual_position:
            actor.position = actual_position
        return result

    def is_declared_resolution(self, actor: Unit, payload: dict[str, Any], action_types: set[str]) -> bool:
        queued = self.resolving_action
        declaration_id = payload.get("declaration_id")
        return bool(queued is not None and queued.actor_id == actor.unit_id
                    and queued.action_type in action_types and queued.payload.get("queued_resolution")
                    and declaration_id and declaration_id == queued.payload.get("declaration_id"))

    def use_skill(self, actor: Unit, skill_code: str, payload: dict[str, Any]) -> None:
        skill = actor.get_skill(skill_code)
        prepaid = bool(payload.get("resources_prepaid"))
        if not prepaid and not actor.can_take_turn_actions(self):
            raise ActionError("这个单位当前不能行动。")
        if not prepaid:
            ok, reason = skill.can_use(self, actor, payload)
            if not ok:
                raise ActionError(reason)
            self.prepay_skill_resources(skill, actor, payload)
        if (
            payload.get("queued_resolution")
            and self.declared_target_position(payload) is not None
            and payload.get("target_unit_id")
            and not payload.get("resolved_target_unit_id")
            and skill.target_mode in {"ally", "enemy", "unit"}
            and not skill.target_unit_is_anchor
        ):
            self.log(f"【{skill.name}】落在原定格上，没有命中有效目标。")
            skill.on_target_missed(self, actor, payload)
            skill.finalize_use(self, actor)
            actor.actions_taken_this_turn.append(f"skill:{skill.code}")
            self.check_win_condition()
            return
        target_id = None if skill.target_unit_is_anchor else (payload.get("resolved_target_unit_id") or payload.get("target_unit_id"))
        if target_id:
            target = self.get_unit(target_id)
            self.require_selectable_unit(
                target,
                actor=actor,
                action_name=skill.name,
                ignore_stealth=skill.ignores_stealth_for_payload(self, actor, payload),
                queued_resolution=bool(payload.get("queued_resolution")),
            )
            if (skill.target_mode in {"ally", "enemy", "unit"} and skill.requires_direct_unit_target_line
                    and not self.is_declared_resolution(actor, payload, {"skill", "reaction_skill"})):
                self.require_unit_target_in_range_and_line(
                    actor,
                    target,
                    skill.direct_unit_target_range(self, actor, payload),
                    action_name=skill.name,
                )
        execution_turn = self.turn_number
        try:
            skill.execute(self, actor, payload)
        except ActionMiss as exc:
            self.log(str(exc) or f"【{skill.name}】落在原定格上，没有命中有效目标。")
            skill.on_target_missed(self, actor, payload)
        except ActionError as exc:
            if payload.get("queued_resolution") and payload.get("declared_target_x") is not None:
                self.log(f"【{skill.name}】落在原定格上，但没有命中有效目标。")
            else:
                raise exc
        skill.finalize_use(self, actor)
        if self.turn_number == execution_turn:
            actor.actions_taken_this_turn.append(f"skill:{skill.code}")
        self.check_win_condition()

    def resolved_basic_attack_payload(
        self,
        actor: Unit,
        payload: Optional[dict[str, Any]] = None,
    ) -> dict[str, Any]:
        resolved = dict(payload or {})
        components = list(actor.iter_components())
        override = next((component for component in components if getattr(component, "overrides_basic_attack_shape", False)), None)
        for component in components:
            if override is not None and (component is override or type(component).basic_attack_area_cells is not BattleComponent.basic_attack_area_cells
                                         or type(component).basic_attack_preview is not BattleComponent.basic_attack_preview):
                continue
            resolved.update(component.basic_attack_payload_metadata(self, actor, resolved))
        if override is not None and resolved.get("attack_variant") != "allied_heal":
            resolved.update(override.basic_attack_payload_metadata(self, actor, resolved))
        for component in components:
            if getattr(component, "applies_after_basic_attack_shape", False):
                resolved.update(component.basic_attack_payload_metadata(self, actor, resolved))
        if "attack_cost" not in resolved or resolved.get("attack_variant") == "allied_heal":
            resolved["attack_cost"] = 1
        for effect in list(self.field_effects):
            resolved.update(effect.basic_attack_payload_metadata(self, actor, resolved))
        return resolved

    def basic_attack_resource_allowed(self, actor: Unit, payload: dict[str, Any]) -> tuple[bool, str]:
        cost = int(payload.get("attack_cost", 1))
        if cost < 0:
            return False, "普攻次数消耗无效。"
        if payload.get("attack_variant") == "kiku_legacy" and not actor.has_status("菊之遗击"):
            return False, "当前没有【菊之遗击】。"
        if (cost > 0 and actor.attacks_used + cost > actor.attack_actions_per_turn()
                and not self.is_declared_resolution(actor, payload, {"attack"})):
            return False, "本回合攻击次数已用完。"
        for component in actor.iter_components():
            ok, reason = component.can_use_basic_attack(self, actor, payload)
            if not ok:
                return False, reason
        return True, ""

    def basic_attack_action_specs(self, unit: Unit) -> list[dict[str, Any]]:
        actions: list[dict[str, Any]] = [
            {
                "code": "attack",
                "name": "普攻",
                "description": "普通攻击。",
                "attack_payload": {"attack_variant": "default"},
            }
        ]
        if self.allied_basic_heal_amount(unit, unit) > 0:
            actions.append({
                "code": "allied_heal_attack", "name": "普攻治疗",
                "description": "消耗一次普攻，治疗一个己方单位1/4（可选自身）。",
                "attack_payload": {"attack_variant": "allied_heal"},
            })
        seen_codes = {entry["code"] for entry in actions}
        for component in unit.iter_components():
            for entry in component.basic_attack_action_entries(self, unit):
                code = str(entry.get("code") or "").strip()
                if not code or code in seen_codes:
                    continue
                seen_codes.add(code)
                actions.append(dict(entry))
        return actions

    def basic_attack_preview_for_payload(
        self,
        actor: Unit,
        payload: Optional[dict[str, Any]] = None,
    ) -> dict[str, Any]:
        resolved_payload = self.resolved_basic_attack_payload(actor, payload)
        if not self.basic_attack_resource_allowed(actor, resolved_payload)[0]:
            return {"cells": [], "target_unit_ids": [], "requires_target": True}
        healing_only = resolved_payload.get("attack_variant") == "allied_heal"
        if not healing_only:
            override = next((component for component in actor.iter_components() if getattr(component, "overrides_basic_attack_shape", False)), None)
            if override is not None:
                return override.basic_attack_preview(self, actor, resolved_payload)
            for component in actor.iter_components():
                preview = component.basic_attack_preview(self, actor, resolved_payload)
                if preview is not None:
                    return preview
        attack_targets: list[str] = []
        attack_cells: list[dict[str, int]] = []
        candidates = [] if healing_only else list(self.enemy_units(actor.player_id))
        candidates.extend(target for target in self.player_units(actor.player_id)
                          if self.allied_basic_heal_amount(actor, target) > 0
                          or resolved_payload.get("allow_allied_attack_target"))
        forced_target = self.forced_basic_attack_target(actor)
        if not healing_only and forced_target is not None and all(unit.unit_id != forced_target.unit_id for unit in candidates):
            candidates.append(forced_target)
        for target in candidates:
            ignore_stealth = self.attack_ignores_stealth(actor, target)
            if self.attack_target_allowed(actor, target, ignore_stealth=ignore_stealth, payload=resolved_payload)[0]:
                attack_targets.append(target.unit_id)
                valid_cells = []
                for cell in self.unit_cells(target):
                    cell_payload = dict(resolved_payload)
                    cell_payload["x"] = cell.x
                    cell_payload["y"] = cell.y
                    if self.attack_target_allowed(actor, target, ignore_stealth=ignore_stealth, payload=cell_payload)[0]:
                        valid_cells.append(cell)
                if not valid_cells:
                    valid_cells = self.unit_cells(target)
                attack_cells.extend(cell.to_dict() for cell in valid_cells)
        return {"cells": attack_cells, "target_unit_ids": attack_targets, "requires_target": True,
                "allied_heal_target_ids": [target_id for target_id in attack_targets if self.allied_basic_heal_amount(actor, self.get_unit(target_id)) > 0]}

    def forced_basic_attack_target(self, actor: Unit) -> Optional[Unit]:
        for component in actor.iter_components():
            target_id = str(getattr(component, "required_attack_target_id", "") or "")
            if not target_id:
                continue
            forces = getattr(component, "forces_attack_target", None)
            if callable(forces) and not bool(forces(self)):
                continue
            target = self.units.get(target_id)
            if target is not None and target.alive and target.position is not None and not target.banished:
                return target
        return None

    def build_queued_action(self, payload: dict[str, Any], *, reaction_to: Optional[QueuedAction] = None) -> QueuedAction:
        action_type = payload.get("type")
        if action_type in {"end_turn", "pass_unit", "chain_react", "chain_skip"}:
            raise ActionError("该动作不能进入连锁栈。")
        queued_payload = dict(payload)
        queued_payload["declaration_id"] = f"action-{next(_id_counter)}"
        queued_payload.pop("enemy_skill_prevented_attack", None)
        queued_payload.pop("passive_attack_preventer_ids", None)
        queued_payload.pop("evaded_unit_ids", None)
        queued_payload.pop("completed_evasion_unit_ids", None)
        queued_payload.pop("declared_source_attack", None)
        queued_payload.pop("descent_extra_declared", None)
        queued_payload.pop("_attack_pre_move_done", None)
        queued_payload.pop("allied_heal_attack", None)
        queued_payload.pop("wuchang_mist_checked", None)
        queued_payload.pop("action_failed_by_wuchang_mist", None)
        queued_payload.pop("separate_attack_segments", None)
        queued_payload.pop("separate_attack_index", None)
        queued_payload.pop("kings_insight_checked", None)
        queued_payload["queued_resolution"] = True
        actor = self.get_unit(payload["unit_id"])
        if action_type == "move":
            if not actor.can_take_turn_actions(self):
                raise ActionError("这个单位当前不能行动。")
            remaining_move = actor.remaining_normal_move_distance(self)
            if (
                actor.normal_move_actions_used >= actor.normal_move_actions_per_turn()
                and not actor.allows_split_normal_movement(self)
            ):
                raise ActionError("本回合已经移动过了。")
            if remaining_move <= 0:
                raise ActionError("本回合移动距离已经用完。")
            if actor.cannot_move:
                raise ActionError(f"{actor.name} 当前无法移动。")
            if actor.cannot_normal_move:
                raise ActionError(f"{actor.name} 当前不能进行常规移动。")
            explicit_steps = self.payload_positions(payload, "path")
            if explicit_steps:
                path = self.explicit_path(
                    actor,
                    explicit_steps,
                    max_distance=remaining_move,
                    use_movement_cost=True,
                )
                queued_payload["path"] = [step.to_dict() for step in path[1:]]
                queued_payload["x"] = path[-1].x
                queued_payload["y"] = path[-1].y
            elif payload.get("x") is None or payload.get("y") is None:
                raise ActionError("移动需要指定目标位置。")
            for component in actor.iter_components():
                forced_paths = component.forced_normal_move_paths(self, actor)
                if forced_paths is None:
                    continue
                destination = Position(int(queued_payload["x"]), int(queued_payload["y"]))
                selected = next((route for route in forced_paths if route[-1] == destination), None)
                if selected is None or (explicit_steps and path != selected):
                    raise ActionError("狂暴浮游炮必须沿最近目标的最短路线行动。")
                queued_payload["path"] = [step.to_dict() for step in selected[1:]]
            return QueuedAction(
                action_type="move",
                actor_id=actor.unit_id,
                display_name="移动",
                speed=1,
                payload=queued_payload,
                description="移动到指定格子，不会直接造成伤害或附加效果。",
                target_unit_ids=[],
                target_cells=[],
                source_player_id=actor.player_id,
                hostile=False,
                suppress_logs=actor.is_stealthed(),
            )
        if action_type == "attack":
            for key in ("attack_cost", "attack_power_override", "attack_bonus"):
                queued_payload.pop(key, None)
            queued_payload.pop("attack_cells", None)
            queued_payload.pop("single_hit_per_recipient", None)
            queued_payload.pop("nuclear_rush_landing", None)
            queued_payload.pop("nuclear_rush_declared", None)
            queued_payload.pop("basic_attack_cancelled", None)
            queued_payload.pop("attack_effect_summary", None)
            if queued_payload.get("attack_variant") != "allied_heal":
                override = next((component for component in actor.iter_components() if getattr(component, "overrides_basic_attack_shape", False)), None)
                if override is not None:
                    override.validate_declaration(self, actor, queued_payload)
            queued_payload = self.resolved_basic_attack_payload(actor, queued_payload)
            ok, reason = self.basic_attack_resource_allowed(actor, queued_payload)
            if not ok:
                raise ActionError(reason)
            forced_target = self.forced_basic_attack_target(actor)
            if forced_target is not None and forced_target.player_id == actor.player_id:
                queued_payload["forced_allied_attack_target_id"] = forced_target.unit_id
            separate = any(component.separate_basic_attack_windows for component in actor.iter_components())
            if separate and queued_payload.get("attack_variant") != "allied_heal":
                queued_payload.pop("attack_cells", None)
                primary = self.effect_recipient(self.get_unit(str(payload.get("target_unit_id") or "")))
                if primary.player_id == actor.player_id and (forced_target is None or forced_target.unit_id != primary.unit_id):
                    raise ActionError("需选择敌方普攻目标。")
                ok, reason = self.attack_target_allowed(actor, primary, payload=queued_payload)
                if not ok:
                    raise ActionError(reason)
            area_cells = self.basic_attack_area_cells_for_payload(actor, queued_payload)
            if area_cells is not None:
                attack_cost = int(queued_payload.get("attack_cost", 1))
                if not actor.can_take_turn_actions(self):
                    raise ActionError("这个单位当前不能行动。")
                block_reason = self.shared_stealth_action_block_reason(actor)
                if block_reason:
                    raise ActionError(block_reason)
                if actor.cannot_attack:
                    raise ActionError(f"{actor.name} 当前不能攻击。")
                for effect in list(self.field_effects):
                    ok, reason = effect.can_attack_target(self, actor, actor)
                    if not ok:
                        raise ActionError(reason)
                if actor.position is not None:
                    queued_payload["declared_source_x"] = actor.position.x
                    queued_payload["declared_source_y"] = actor.position.y
                queued_payload["attack_cells"] = [cell.to_dict() for cell in area_cells]
                affects_allies = bool(queued_payload.get("friendly_fire")) or any(
                    component.basic_attack_area_affects_allies(self, actor, queued_payload)
                    for component in actor.iter_components()
                )
                forced_allied_target_id = str(queued_payload.get("forced_allied_attack_target_id") or "")
                impact = None
                raw_impact = queued_payload.get("impact_cell")
                if isinstance(raw_impact, dict) and raw_impact.get("x") is not None and raw_impact.get("y") is not None:
                    impact = Position(int(raw_impact["x"]), int(raw_impact["y"]))
                from wujiang.tactical.engine.siege import is_siege_structure, structure_hit_by_impact

                target_units = []
                for unit in self.effect_units_at_cells(area_cells):
                    if (
                        not affects_allies
                        and unit.player_id == actor.player_id
                        and unit.unit_id != forced_allied_target_id
                    ):
                        continue
                    if queued_payload.get("structures_need_direct_hit") and is_siege_structure(unit):
                        if not structure_hit_by_impact(self, unit, impact):
                            continue
                    target_units.append(unit)
                if not target_units:
                    raise ActionError("攻击区域内没有有效目标。")
                if separate:
                    target_units.sort(key=lambda unit: (unit.unit_id != primary.unit_id, unit.position.y, unit.position.x, unit.unit_id))
                    segments = [{"target_unit_id": unit.unit_id, "cells": [cell.to_dict() for cell in self.unit_cells(unit) if cell in area_cells]}
                                for unit in target_units]
                    queued_payload["separate_attack_segments"] = segments
                    queued_payload["separate_attack_index"] = 0
                    area_cells = self.payload_positions(segments[0], "cells")
                    target_units = target_units[:1]
                target_ids: list[str] = []
                for unit in target_units:
                    if unit.unit_id not in target_ids:
                        target_ids.append(unit.unit_id)
                return QueuedAction(
                    action_type="attack",
                    actor_id=actor.unit_id,
                    display_name=str(queued_payload.get("attack_name") or "普攻"),
                    speed=1,
                    payload=queued_payload,
                    target_unit_ids=target_ids,
                    target_cells=area_cells,
                    source_player_id=actor.player_id,
                    hostile=True,
                    suppress_logs=actor.is_stealthed() or any(self.get_unit(unit_id).is_stealthed() for unit_id in target_ids),
                )
            target = self.effect_recipient(self.get_unit(payload["target_unit_id"]))
            if queued_payload.get("attack_variant") == "allied_heal" and self.allied_basic_heal_amount(actor, target) <= 0:
                raise ActionError("当前不能对这个单位使用普攻治疗。")
            attack_cost = int(queued_payload.get("attack_cost", 1))
            if not actor.can_take_turn_actions(self):
                raise ActionError("\u8fd9\u4e2a\u5355\u4f4d\u5f53\u524d\u4e0d\u80fd\u884c\u52a8\u3002")
            ignore_stealth = self.attack_ignores_stealth(actor, target)
            ok, reason = self.attack_target_allowed(actor, target, ignore_stealth=ignore_stealth, payload=queued_payload)
            if not ok:
                raise ActionError(reason)
            queued_payload["declared_source_x"] = actor.position.x
            queued_payload["declared_source_y"] = actor.position.y
            declared_target = self.declared_cell_for_target(actor, target, queued_payload)
            if declared_target is not None and queued_payload.get("x") is None and queued_payload.get("y") is None:
                candidate_cells = sorted(self.unit_cells(target), key=lambda cell: (cell.y, cell.x))
                valid_cells = [cell for cell in candidate_cells if self.attack_target_allowed(
                    actor, target, ignore_stealth=ignore_stealth,
                    payload={**queued_payload, "x": cell.x, "y": cell.y},
                )[0]]
                if valid_cells and declared_target not in valid_cells:
                    declared_target = valid_cells[0]
            if declared_target is not None:
                queued_payload["declared_target_x"] = declared_target.x
                queued_payload["declared_target_y"] = declared_target.y
            ignore_shield, half_ignore_shield = self.attack_shield_flags(actor, target, payload=queued_payload)
            queued_payload["ignore_shield"] = ignore_shield
            queued_payload["half_ignore_shield"] = half_ignore_shield
            queued_payload["ignore_stealth"] = ignore_stealth
            if self.allied_basic_heal_amount(actor, target) > 0:
                queued_payload["allow_allied_attack_target"] = True
                queued_payload["allied_heal_attack"] = True
            elif forced_target is not None and target.unit_id == forced_target.unit_id and target.player_id == actor.player_id:
                queued_payload["allow_allied_attack_target"] = True
            return QueuedAction(
                action_type="attack",
                actor_id=actor.unit_id,
                display_name=str(queued_payload.get("attack_name") or "普攻"),
                speed=1,
                payload=queued_payload,
                target_unit_ids=[target.unit_id],
                target_cells=self.unit_cells(target),
                source_player_id=actor.player_id,
                hostile=target.player_id != actor.player_id,
                suppress_logs=actor.is_stealthed() or target.is_stealthed(),
            )
        if action_type == "skill":
            skill = actor.get_skill(payload["skill_code"])
            ok, reason = skill.can_react_with_payload(self, actor, reaction_to, payload) if reaction_to is not None else skill.can_use(self, actor, payload)
            if not ok:
                raise ActionError(reason)
            wrapped = skill.build_wrapped_action(self, actor, payload)
            if wrapped is not None:
                return wrapped
            queued_payload.update(skill.queued_payload_metadata(self, actor, payload))
            queued_payload["resolves_from_current_position"] = skill.resolves_from_current_position
            if actor.position is not None:
                queued_payload["declared_source_x"] = actor.position.x
                queued_payload["declared_source_y"] = actor.position.y
            queued_payload["ignore_shield"] = skill.ignores_shield_for_payload(self, actor, payload) or any(
                component.all_skills_pierce_shields for component in actor.iter_components()
            )
            queued_payload["half_ignore_shield"] = skill.half_ignores_shield_for_payload(self, actor, payload)
            queued_payload["ignore_stealth"] = skill.ignores_stealth_for_payload(self, actor, payload)
            queued_payload["cannot_evade"] = skill.cannot_evade_for_payload(self, actor, payload)
            if skill.ignores_magic_immunity_for_payload(self, actor, payload):
                queued_payload["ignore_magic_immunity"] = True
            declared_targets = self.payload_target_unit_ids(payload)
            if skill.target_mode in {"ally", "enemy", "unit"} and skill.requires_direct_unit_target_line:
                for target_id in declared_targets:
                    target = self.get_unit(target_id)
                    self.require_selectable_unit(
                        target,
                        actor=actor,
                        action_name=skill.name,
                        ignore_stealth=queued_payload["ignore_stealth"],
                    )
                    self.require_unit_target_in_range_and_line(
                        actor,
                        target,
                        skill.direct_unit_target_range(self, actor, payload),
                        action_name=skill.name,
                    )
            elif payload.get("target_unit_id"):
                target = self.get_unit(payload["target_unit_id"])
                self.require_selectable_unit(
                    target,
                    actor=actor,
                    action_name=skill.name,
                    ignore_stealth=queued_payload["ignore_stealth"],
                )
            target_units = [
                unit
                for unit in skill.get_target_units_for_payload(self, actor, queued_payload)
                if unit.alive
            ]
            target_cells = list(skill.get_target_cells_for_payload(self, actor, queued_payload))
            targets: list[str] = []
            for unit in self.effect_units(
                [*target_units, *([] if skill.target_cells_are_geometry_only else self.units_at_cells(target_cells))],
                ignore=actor if skill.excludes_caster_from_effect else None,
            ):
                if skill.excludes_allies_from_effect and unit.player_id == actor.player_id:
                    continue
                if unit.unit_id not in targets:
                    targets.append(unit.unit_id)
            if declared_targets:
                target = self.get_unit(declared_targets[0])
                declared_target = self.declared_cell_for_target(actor, target, payload)
                if declared_target is not None:
                    queued_payload["declared_target_x"] = declared_target.x
                    queued_payload["declared_target_y"] = declared_target.y
            hostile = any(self.get_unit(unit_id).player_id != actor.player_id for unit_id in targets)
            return QueuedAction(
                action_type="skill",
                actor_id=actor.unit_id,
                display_name=skill.name,
                speed=skill.chain_speed,
                payload=queued_payload,
                target_unit_ids=targets,
                target_cells=target_cells,
                source_player_id=actor.player_id,
                hostile=hostile,
                suppress_logs=actor.is_stealthed() or any(self.get_unit(unit_id).is_stealthed() for unit_id in targets),
            )
        raise ActionError("未知动作类型。")

    def source_action_for_reaction(self, queued_action: QueuedAction) -> QueuedAction:
        payload = queued_action.payload
        source_cells = [
            Position(int(cell["x"]), int(cell["y"]))
            for cell in payload.get("source_target_cells", [])
        ]
        return QueuedAction(
            action_type=payload.get("source_action_type", "attack"),
            actor_id=payload["source_actor_id"],
            display_name=payload.get("source_display_name", ""),
            speed=int(payload.get("source_speed", 1)),
            payload=dict(payload.get("source_payload", {})),
            target_unit_ids=list(payload.get("source_target_unit_ids", [])),
            target_cells=source_cells,
            source_player_id=payload.get("source_player_id"),
            hostile=bool(payload.get("source_hostile", True)),
            reaction_source_id=queued_action.reaction_source_id,
        )

    def format_summary_number(self, value: float) -> str:
        rounded = round(float(value), 4)
        if int(rounded) == rounded:
            return str(int(rounded))
        return f"{rounded}".rstrip("0").rstrip(".")

    def format_action_effect_summary(self, action_name: str, description: str) -> str:
        cleaned_name = str(action_name or "动作").strip()
        cleaned_description = str(description or "").strip()
        if not cleaned_description:
            return f"【{cleaned_name}】"
        if cleaned_description.startswith(f"【{cleaned_name}】"):
            return cleaned_description
        return f"【{cleaned_name}】：{cleaned_description}"

    def basic_attack_preview_power(self, actor: Unit, payload: Optional[dict[str, Any]] = None) -> float:
        resolved_payload = self.resolved_basic_attack_payload(actor, payload)
        if resolved_payload.get("attack_power_override") is not None:
            return float(resolved_payload["attack_power_override"])
        attack_power = actor.stat("attack")
        attack_power += float(resolved_payload.get("attack_bonus", 0.0) or 0.0)
        for component in actor.iter_components():
            bonus_attack = float(getattr(component, "bonus_attack", 0.0) or 0.0)
            if bonus_attack:
                attack_power += bonus_attack
        return attack_power

    def basic_attack_effect_notes(self, actor: Unit, payload: Optional[dict[str, Any]] = None) -> list[str]:
        resolved_payload = self.resolved_basic_attack_payload(actor, payload)
        notes: list[str] = []
        attack_note = str(resolved_payload.get("attack_note") or "").strip()
        if attack_note:
            notes.append(self.format_action_effect_summary(str(resolved_payload.get("attack_name") or "普攻"), attack_note))
        for component in actor.iter_components():
            if getattr(component, "bonus_attack", 0.0) or getattr(component, "ignore_shield", False):
                notes.append(self.format_action_effect_summary(component.name, component.description))
            elif component.name == "压制射击":
                notes.append(self.format_action_effect_summary(component.name, component.description))
        return notes

    def queued_action_effect_summary(self, queued_action: QueuedAction) -> str:
        actor = self.units.get(queued_action.actor_id)
        payload = queued_action.payload
        parts: list[str] = []

        if queued_action.action_type == "move":
            parts.append(self.format_action_effect_summary(queued_action.display_name, "仅改变站位，不会直接造成伤害或附加效果。"))
        elif queued_action.action_type == "attack":
            attack_power = self.basic_attack_preview_power(actor, payload) if actor is not None else 0
            attack_cost = int(payload.get("attack_cost", 1))
            parts.append(
                self.format_action_effect_summary(
                    queued_action.display_name,
                    str(payload.get("attack_effect_summary") or f"对原声明格进行一次普攻，按攻 {self.format_summary_number(attack_power)} 结算伤害。"),
                )
            )
            if payload.get("separate_attack_segments"):
                parts.append(f"逐目标独立连锁，第 {int(payload.get('separate_attack_index', 0)) + 1}/{len(payload['separate_attack_segments'])} 段；整次只消耗一次普攻。")
            if attack_cost > 1:
                parts.append(f"这次普攻会占用 {attack_cost} 次普攻次数。")
            if actor is not None:
                parts.extend(self.basic_attack_effect_notes(actor, payload))
        elif queued_action.action_type == "skill":
            if payload.get("skill_code") == "gravity_field" and payload.get("gravity_coin_values"):
                parts.append(f"公开硬币结果 {payload['gravity_coin_values']}，冻结范围边长 {payload['gravity_side']}。")
            if payload.get("skill_code") == "deadly_bow":
                parts.append(f"声明点数攻值 {self.format_summary_number(float(payload.get('deadly_bow_points', 0)))}；已消耗全部魔力点，按原5格结算。")
            if actor is not None and payload.get("skill_code"):
                try:
                    skill = actor.get_skill(str(payload["skill_code"]))
                    if skill.description:
                        parts.append(self.format_action_effect_summary(queued_action.display_name, skill.description))
                except ActionError:
                    pass
            if not parts:
                parts.append(self.format_action_effect_summary(queued_action.display_name, "会按原声明继续结算。"))
        elif queued_action.action_type == "skill_effect":
            effect_code = str(payload.get("effect_code") or "")
            if queued_action.description:
                parts.append(self.format_action_effect_summary(queued_action.display_name, queued_action.description))
            elif effect_code == "banish":
                turns = float(payload.get("banish_turns", 0))
                rounds = self.format_summary_number(turns / 2) if turns else "0"
                parts.append(
                    self.format_action_effect_summary(
                        queued_action.display_name,
                        f"若原声明格上的目标仍有效，则使其消失 {rounds}轮。",
                    )
                )
            elif effect_code == "area_damage":
                attack_power = self.format_summary_number(float(payload.get("attack_power", 0)))
                parts.append(
                    self.format_action_effect_summary(
                        queued_action.display_name,
                        f"对原声明范围结算一次范围伤害，伤害值为 {attack_power}。",
                    )
                )
            else:
                parts.append(self.format_action_effect_summary(queued_action.display_name, "后续效果会继续结算。"))
        else:
            parts.append(self.format_action_effect_summary(queued_action.display_name, "会继续结算。"))

        if payload.get("ignore_shield"):
            parts.append("破魔")
        if payload.get("half_ignore_shield"):
            parts.append("半破魔")
        if payload.get("ignore_magic_immunity"):
            parts.append("无视魔免")
        if payload.get("cannot_evade"):
            parts.append("无法回避")

        target_names = [
            self.units[unit_id].name
            for unit_id in queued_action.target_unit_ids
            if unit_id in self.units and self.units[unit_id].alive
        ]
        if target_names:
            parts.append(f"影响单位：{'、'.join(target_names)}。")

        if queued_action.target_cells:
            cell_labels = "、".join(f"({cell.x}, {cell.y})" for cell in queued_action.target_cells)
            parts.append(f"原声明格：{cell_labels}。")
        if payload.get("nuclear_rush_declared") and isinstance(payload.get("nuclear_rush_landing"), dict):
            landing = payload["nuclear_rush_landing"]
            parts.append(f"攻击后尝试移动至声明的第5格 ({landing['x']}, {landing['y']})；无法落位仍完成攻击。")

        ordered: list[str] = []
        for part in parts:
            cleaned = str(part).strip()
            if cleaned and cleaned not in ordered:
                ordered.append(cleaned)
        return " ".join(ordered)

    def available_reaction_options(self, unit: Unit, queued_action: QueuedAction) -> list[ReactionOption]:
        if self.shield_auto_blocks_chain(unit, queued_action):
            return []
        options: list[ReactionOption] = []
        for skill in unit.skills:
            ok, _ = skill.can_react_to(self, unit, queued_action)
            if not ok:
                continue
            options.append(
                ReactionOption(
                    unit_id=unit.unit_id,
                    action_code=skill.code,
                    action_name=skill.name,
                    action_type="skill",
                    timing=skill.timing,
                    chain_speed=skill.chain_speed,
                    description=skill.description,
                    preview=skill.reaction_preview(self, unit, queued_action),
                )
            )
        reaction_target = self.reaction_proxy_target(unit, queued_action)
        if self.unit_can_use_block_counter(unit) and queued_action.speed < 2 and reaction_target is not None:
            options.append(
                ReactionOption(
                    unit_id=unit.unit_id,
                    action_code="block",
                    action_name="格挡",
                    action_type="reaction_action",
                    timing="reaction",
                    chain_speed=2,
                    description="下一次伤害结算时守 +1，只持续到这次连锁结算结束。",
                    preview={"cells": [cell.to_dict() for cell in self.unit_cells(reaction_target)], "target_unit_ids": [], "requires_target": False},
                )
            )
            attacker = self.units.get(queued_action.actor_id)
            if (
                attacker is not None
                and self.attack_target_allowed(unit, attacker)[0]
            ):
                options.append(
                    ReactionOption(
                        unit_id=unit.unit_id,
                        action_code="counter",
                        action_name="反击",
                        action_type="reaction_action",
                        timing="reaction",
                        chain_speed=2,
                        description="对你所连锁的攻击或技能使用者进行一次普攻式反击，需在自身攻击范围内。",
                        preview={"cells": [cell.to_dict() for cell in self.unit_cells(attacker)], "target_unit_ids": [attacker.unit_id], "requires_target": False},
                    )
                )
        return options

    def shield_auto_blocks_chain(self, unit: Unit, queued_action: QueuedAction) -> bool:
        if queued_action.payload.get("pass_through_damage"):
            return False
        recipient = self.effect_recipient(unit)
        anti_pierce = any(component.ignores_incoming_piercing for component in recipient.iter_components())
        actor = self.units.get(queued_action.actor_id)
        piercing_followup = queued_action.action_type == "attack" and actor is not None and any(
            callable(hook := getattr(component, "piercing_basic_attack_followup_applies", None))
            and hook(self, actor, recipient)
            for component in actor.iter_components()
        )
        return (
            queued_action.action_type in {"attack", "skill", "skill_effect"}
            and queued_action.speed == 1
            and recipient.total_shields() > 0
            and (anti_pierce or not (queued_action.payload.get("ignore_shield") or queued_action.payload.get("half_ignore_shield") or piercing_followup))
        )

    def action_ignores_stealth(self, queued_action: QueuedAction) -> bool:
        return bool(queued_action.payload.get("ignore_stealth"))

    def target_can_chain_against(self, unit: Unit, queued_action: QueuedAction) -> bool:
        if not unit.alive or unit.position is None or unit.banished:
            return False
        if any(effect.blocks_action_effect(self, self.effect_recipient(unit), queued_action) for effect in self.field_effects):
            return False
        if queued_action.action_type == "skill":
            source = self.units.get(queued_action.actor_id)
            skill = source.skill_map().get(queued_action.payload.get("skill_code")) if source is not None else None
            if skill is not None:
                return skill.target_can_react_to_effect(self, source, self.effect_recipient(unit), queued_action.payload)
        return True

    def reaction_affected_units(self, queued_action: QueuedAction) -> list[Unit]:
        actor = self.get_unit(queued_action.actor_id)
        affected: list[Unit] = []
        seen: set[str] = set()
        for unit_id in queued_action.target_unit_ids:
            if unit_id in seen:
                continue
            seen.add(unit_id)
            unit = self.get_unit(unit_id)
            if unit.player_id == actor.player_id:
                continue
            if not self.target_can_chain_against(unit, queued_action):
                continue
            if self.shield_auto_blocks_chain(unit, queued_action):
                continue
            affected.append(unit)
        return affected

    def create_reaction_window(self, queued_action: QueuedAction) -> Optional[ReactionWindow]:
        if queued_action.speed >= 3 or not queued_action.hostile or not queued_action.target_unit_ids:
            return None
        affected_units = self.reaction_affected_units(queued_action)
        if not affected_units:
            return None
        reactive_player_id = affected_units[0].player_id
        candidate_ids: list[str] = []
        options_by_unit: dict[str, list[ReactionOption]] = {}
        for unit in [*affected_units, *self.player_units(reactive_player_id)]:
            if unit.unit_id in candidate_ids:
                continue
            if not unit.alive or unit.position is None or unit.banished:
                continue
            options = self.available_reaction_options(unit, queued_action)
            if options:
                candidate_ids.append(unit.unit_id)
                options_by_unit[unit.unit_id] = options
        if not candidate_ids:
            return None
        candidate_ids.sort(
            key=lambda unit_id: (
                -float(self.get_unit(unit_id).stat("speed")),
                -int(self.get_unit(unit_id).level),
                random.random(),
            )
        )
        return ReactionWindow(
            reactive_player_id=reactive_player_id,
            queued_action=queued_action,
            pending_reactor_ids=candidate_ids,
            options_by_unit=options_by_unit,
        )

    def queue_followup_action(self, queued_action: QueuedAction) -> None:
        self.pending_followup_actions.append(queued_action)

    def build_skill_effect_action(
        self,
        *,
        actor: Unit,
        display_name: str,
        effect_code: str,
        payload: Optional[dict[str, Any]] = None,
        target_units: Optional[list[Unit]] = None,
        target_cells: Optional[list[Position]] = None,
        include_cell_units: bool = True,
        speed: int = 1,
        hostile: Optional[bool] = None,
        description: str = "",
        suppress_logs: Optional[bool] = None,
        effect_resolver: Optional[Callable[["Battle", Unit, QueuedAction], None]] = None,
        segment_index: Optional[int] = None,
        segment_count: Optional[int] = None,
    ) -> QueuedAction:
        cells = list(target_cells or [])
        resolved_payload = dict(payload or {})
        resolved_payload["effect_code"] = effect_code
        if actor.position is not None:
            resolved_payload.setdefault("declared_source_x", actor.position.x)
            resolved_payload.setdefault("declared_source_y", actor.position.y)
        if segment_index is not None:
            resolved_payload["segment_index"] = segment_index
        if segment_count is not None:
            resolved_payload["segment_count"] = segment_count
        cell_units = self.effect_units_at_cells(cells) if include_cell_units else []
        targets = self.effect_units([*(target_units or []), *cell_units])
        resolved_hostile = hostile if hostile is not None else any(unit.player_id != actor.player_id for unit in targets)
        resolved_suppress_logs = (
            suppress_logs
            if suppress_logs is not None
            else actor.is_stealthed() or any(unit.is_stealthed() for unit in targets)
        )
        return QueuedAction(
            action_type="skill_effect",
            actor_id=actor.unit_id,
            display_name=display_name,
            speed=speed,
            payload=resolved_payload,
            description=description,
            target_unit_ids=[unit.unit_id for unit in targets],
            target_cells=cells,
            source_player_id=actor.player_id,
            hostile=resolved_hostile,
            suppress_logs=resolved_suppress_logs,
            effect_resolver=effect_resolver,
        )

    def queue_skill_effect_action(self, **kwargs: Any) -> None:
        self.queue_followup_action(self.build_skill_effect_action(**kwargs))

    def queue_area_damage_effect(
        self,
        *,
        actor: Unit,
        display_name: str,
        cells: list[Position],
        attack_power: float,
        speed: int = 1,
        raw_damage: Optional[float] = None,
        ignore_shield: bool = False,
        half_ignore_shield: bool = False,
        ignore_magic_immunity: bool = False,
        cannot_evade: bool = False,
        tags: Optional[set[str]] = None,
        segment_index: Optional[int] = None,
        segment_count: Optional[int] = None,
    ) -> None:
        payload: dict[str, Any] = {
            "cells": [cell.to_dict() for cell in cells],
            "attack_power": attack_power,
            "ignore_shield": ignore_shield,
            "half_ignore_shield": half_ignore_shield,
            "ignore_magic_immunity": ignore_magic_immunity,
            "cannot_evade": cannot_evade,
            "tags": sorted(tags or set()),
        }
        if raw_damage is not None:
            payload["raw_damage"] = raw_damage
        self.queue_skill_effect_action(
            actor=actor,
            display_name=display_name,
            effect_code="area_damage",
            payload=payload,
            target_cells=list(cells),
            speed=speed,
            segment_index=segment_index,
            segment_count=segment_count,
        )

    def advance_followup_actions(self) -> None:
        if self._draining_followup_actions:
            return
        self._draining_followup_actions = True
        try:
            while self.pending_chain is None and self.pending_followup_actions:
                self.present_reaction_window_or_resolve(self.pending_followup_actions.popleft())
        finally:
            self._draining_followup_actions = False

    def present_reaction_window_or_resolve(self, queued_action: QueuedAction) -> None:
        if queued_action.payload.get("refresh_cell_targets"):
            actor = self.units.get(queued_action.actor_id)
            targets = self.effect_units_at_cells(queued_action.target_cells)
            queued_action.target_unit_ids = [unit.unit_id for unit in targets]
            queued_action.hostile = actor is not None and any(unit.player_id != actor.player_id for unit in targets)
        if queued_action.payload.get("separate_attack_segments"):
            actor = self.units.get(queued_action.actor_id)
            if actor is None or not actor.alive or actor.banished:
                self.resolve_queued_action(queued_action)
                return
            state = self._separate_attack_sequences.get(queued_action.payload["declaration_id"], {})
            queued_action.target_unit_ids = [unit.unit_id for unit in self.effect_units_at_cells(queued_action.target_cells)
                                            if unit.player_id != actor.player_id and unit.unit_id not in state.get("seen", ())]
        with self.suppress_logs() if self.queued_action_hides_logs(queued_action) else nullcontext():
            window = self.create_reaction_window(queued_action)
            if window is None:
                if queued_action.speed < 3 and queued_action.hostile and queued_action.target_unit_ids:
                    target_names = "、".join(self.get_unit(unit_id).name for unit_id in queued_action.target_unit_ids)
                    self.log(f"{target_names} 没有可用的更快连锁，【{queued_action.display_name}】直接结算。")
                self.resolve_queued_action(queued_action)
                self.advance_followup_actions()
                return
            self.pending_chain = window
            reactor = self.pending_chain.current_unit_id()
            self.log(f"等待玩家 {window.reactive_player_id} 的连锁响应。")
            if reactor is not None:
                self.log(f"{self.get_unit(reactor).name} 可以进行连锁。")

    def execute_reaction_option(
        self,
        option: ReactionOption,
        queued_action: QueuedAction,
        reaction_payload: Optional[dict[str, Any]] = None,
    ) -> None:
        actor = self.get_unit(option.unit_id)
        if option.action_type == "skill":
            skill = actor.get_skill(option.action_code)
            skill.react(self, actor, reaction_payload or {}, queued_action)
            skill.finalize_use(self, actor)
            self.log(f"{actor.name} 连锁使用【{skill.name}】。")
            return
        self.resolve_reaction_action(actor, option.action_code, queued_action)

    def resolve_reaction_action(self, actor: Unit, action_code: str, queued_action: QueuedAction) -> None:
        if action_code == "block":
            actor.add_status(
                TemporaryDefenseStatus(
                    "格挡",
                    defense_delta=1,
                    description="下一次伤害结算时守 +1。",
                    expire_with_chain=True,
                )
            )
            self.log(f"{actor.name} 进入格挡姿态。")
            return
        if action_code == "counter":
            source = self.units.get(queued_action.actor_id)
            if source is None:
                return
            ok, _ = self.attack_target_allowed(actor, source)
            if not ok:
                return
            ctx = self.resolve_attack_damage(actor, source, action_name="\u53cd\u51fb", tags={"counter"})
            actor.notify_basic_attack_finished(self, {"counter": True, "reaction_attack": True},
                                               [ctx] if ctx is not None else [], missed=ctx is None)
            self.log(f"{actor.name} \u53d1\u52a8\u4e86\u53cd\u51fb\u3002")
            return
        raise ActionError("未知连锁动作。")

    def resolve_skill_effect(self, actor: Unit, queued_action: QueuedAction) -> None:
        if queued_action.effect_resolver is not None:
            queued_action.effect_resolver(self, actor, queued_action)
            self.check_win_condition()
            return
        payload = queued_action.payload
        effect_code = payload.get("effect_code")
        if effect_code == "area_damage":
            cells = self.payload_positions(payload, "cells")
            if not cells:
                self.log(f"【{queued_action.display_name}】没有有效结算范围。")
                return
            for unit in self.effect_units_at_cells(cells):
                self.resolve_damage(
                    DamageContext(
                        source=actor,
                        target=unit,
                        attack_power=float(payload.get("attack_power", 0)),
                        is_skill=True,
                        action_name=queued_action.display_name,
                        ignore_shield=bool(payload.get("ignore_shield")),
                        half_ignore_shield=bool(payload.get("half_ignore_shield")),
                        ignore_magic_immunity=bool(payload.get("ignore_magic_immunity")),
                        cannot_evade=bool(payload.get("cannot_evade")),
                        area_cell_hits=self.unit_hit_count_for_cells(unit, cells),
                        raw_damage=float(payload["raw_damage"]) if payload.get("raw_damage") is not None else None,
                        tags=set(payload.get("tags", [])),
                    )
                )
            self.check_win_condition()
            return
        if effect_code == "pass_through_damage":
            cells = self.payload_positions(payload, "cells")
            for unit in self.effect_units_at_cells(cells):
                self.resolve_damage(DamageContext(
                    source=actor,
                    target=unit,
                    attack_power=float(payload.get("attack_power", 0)),
                    is_skill=False,
                    from_field_effect=True,
                    action_name=queued_action.display_name,
                    tags={"movement", "pass_through_damage"},
                ))
            self.check_win_condition()
            return
        if effect_code == "banish":
            target = self.resolve_declared_target_unit(
                actor,
                payload,
                ignore_stealth=bool(payload.get("ignore_stealth")),
            )
            if target is not None and target.player_id == actor.player_id:
                target = None
            if target is None:
                self.log(f"【{queued_action.display_name}】没有命中有效目标。")
                return
            target_ctx = self.validate_target(
                actor,
                target,
                action_name=queued_action.display_name,
                is_skill=True,
                is_hostile=True,
                ignore_shield=bool(payload.get("ignore_shield")),
                half_ignore_shield=bool(payload.get("half_ignore_shield")),
                ignore_magic_immunity=bool(payload.get("ignore_magic_immunity")),
                cannot_evade=bool(payload.get("cannot_evade")),
                tags=set(payload.get("tags", [])),
            )
            if target_ctx.cancelled:
                self.log(target_ctx.reason)
                return
            turns = int(payload.get("banish_turns", 0))
            self.banish_unit(target, turns)
            success_log = payload.get("success_log")
            if success_log:
                self.log(str(success_log).format(actor=actor.name, target=target.name))
            self.check_win_condition()
            return
        raise ActionError("未知技能后续效果。")

    def resolve_separate_attack_segment(self, actor: Unit, queued_action: QueuedAction) -> None:
        """A single declared basic attack, with one reaction window per frozen recipient footprint."""
        payload = queued_action.payload
        key = payload["declaration_id"]
        state = self._separate_attack_sequences.get(key)
        if state is None:
            return
        if actor.can_take_turn_actions(self) and not actor.cannot_attack:
            for target in self.effect_units_at_cells(queued_action.target_cells):
                if target.player_id == actor.player_id or target.unit_id in state["seen"]:
                    continue
                state["seen"].add(target.unit_id)
                hit_payload = {**payload, "attack_cells": [], "area_attack": True}
                ctx = self.resolve_attack_damage(actor, target, action_name=queued_action.display_name,
                                                 tags={"area_attack"}, payload=hit_payload)
                if ctx is not None:
                    state["contexts"].append(ctx)
        if getattr(self, "_measuring_attack_success", False):
            actor.notify_basic_attack_finished(self, payload, state["contexts"], missed=not state["contexts"])
            return
        index = int(payload.get("separate_attack_index", 0)) + 1
        segments = payload["separate_attack_segments"]
        if index < len(segments) and actor.alive and not actor.banished and self.winner is None:
            cells = self.payload_positions(segments[index], "cells")
            next_payload = {**payload, "separate_attack_index": index, "segment_index": index + 1,
                            "segment_count": len(segments)}
            next_payload.pop("evaded_unit_ids", None)
            next_payload.pop("completed_evasion_unit_ids", None)
            next_payload.pop("enemy_skill_prevented_attack", None)
            next_payload.pop("passive_attack_preventer_ids", None)
            self.queue_followup_action(replace(queued_action, payload=next_payload, target_cells=cells,
                                              target_unit_ids=[], description=f"扩散普攻第 {index + 1}/{len(segments)} 段；不另消耗普攻次数。"))
        else:
            contexts = state["contexts"]
            actor.consume_attack_attempt_buffs(self)
            actor.notify_basic_attack_finished(self, payload, contexts, missed=not contexts)
            self._separate_attack_sequences.pop(key, None)
            self.check_win_condition()

    def resolve_queued_action(self, queued_action: QueuedAction) -> None:
        previous_token = self._current_action_resolution_token
        previous_action = self.resolving_action
        self.resolving_action = queued_action
        resolution_token = self._next_action_resolution_token
        self._next_action_resolution_token += 1
        self._current_action_resolution_token = resolution_token
        try:
            self._resolve_queued_action(queued_action)
        finally:
            # One-effect defenses remain through all hooks, then expire even without a reaction window.
            declaration_id = queued_action.payload.get("declaration_id")
            for unit in {unit.unit_id: unit for unit in [*self.all_units(), *self.destroyed_units]}.values():
                for status in list(unit.statuses):
                    if (getattr(status, "expires_after_action_token", None) == resolution_token
                            or (declaration_id and getattr(status, "expires_after_declaration_id", None) == declaration_id)):
                        unit.remove_status(status, self)
            self._current_action_resolution_token = previous_token
            self.resolving_action = previous_action
            self.sync_linked_units()

    def _resolve_queued_action(self, queued_action: QueuedAction) -> None:
        with self.suppress_logs() if self.queued_action_hides_logs(queued_action) else nullcontext():
            actor = self.units.get(queued_action.actor_id)
            if actor is None or not actor.alive or actor.banished:
                self.stale_queued_action_count += 1
                self._separate_attack_sequences.pop(queued_action.payload.get("declaration_id"), None)
                self.log(f"【{queued_action.display_name}】未能结算，因为行动者已不在战场。")
                return
            if queued_action.action_type in {"attack", "skill"}:
                for observer in list(self.all_units()):
                    for component in list(observer.iter_components()):
                        redirect = getattr(component, "redirect_queued_action", None)
                        if callable(redirect):
                            redirect(self, queued_action)
            self.emit_visual_event_for_queued_action(actor, queued_action)
            payload = queued_action.payload
            if payload.get("action_failed_by_target_redirect"):
                if queued_action.action_type == "attack":
                    actor.attacks_used += int(payload.get("attack_cost", 1))
                    actor.actions_taken_this_turn.append("attack")
                    actor.consume_attack_attempt_buffs(self)
                    actor.notify_basic_attack_finished(self, payload, [], missed=True)
                elif queued_action.action_type == "skill":
                    skill = actor.get_skill(payload["skill_code"])
                    skill.finalize_use(self, actor)
                    actor.actions_taken_this_turn.append(f"skill:{skill.code}")
                self.log_public_event(f"{actor.name} 的【{queued_action.display_name}】改向后范不足，动作失败。", source=actor)
                return
            if queued_action.action_type == "move":
                destination = Position(int(payload["x"]), int(payload["y"]))
                path = self.payload_positions(payload, "path")
                self.move_unit(actor, destination, path=path or None)
                actor.actions_taken_this_turn.append("move")
                return
            if queued_action.action_type == "attack":
                if payload.get("separate_attack_segments") and int(payload.get("separate_attack_index", 0)) > 0:
                    self.resolve_separate_attack_segment(actor, queued_action)
                    return
                if not actor.can_take_turn_actions(self) or actor.cannot_attack:
                    payload["basic_attack_cancelled"] = True
                    actor.attacks_used += int(payload.get("attack_cost", 1))
                    actor.actions_taken_this_turn.append("attack")
                    actor.consume_attack_attempt_buffs(self)
                    actor.notify_basic_attack_finished(self, payload, [], missed=True)
                    self.log(f"{actor.name} 的已声明普攻因行动受限而未命中。")
                    return
                if payload.get("action_failed_by_wuchang_mist"):
                    payload["basic_attack_cancelled"] = True
                    attack_cost = int(payload.get("attack_cost", 1))
                    actor.attacks_used += attack_cost
                    actor.actions_taken_this_turn.append("attack")
                    actor.consume_attack_attempt_buffs(self)
                    actor.notify_basic_attack_finished(self, payload, [], missed=True)
                    self.log(f"{actor.name} 的攻击因【无常之雾】失败。")
                    return
                if not all(component.before_basic_attack_resolution(self, actor, payload) for component in list(actor.iter_components())):
                    payload["basic_attack_cancelled"] = True
                    actor.attacks_used += int(payload.get("attack_cost", 1))
                    actor.actions_taken_this_turn.append("attack")
                    actor.consume_attack_attempt_buffs(self)
                    actor.notify_basic_attack_finished(self, payload, [], missed=True)
                    self.log(f"{actor.name} 的攻击因前移失败而落空。")
                    return
                if payload.get("separate_attack_segments"):
                    actor.attacks_used += int(payload.get("attack_cost", 1))
                    actor.actions_taken_this_turn.append("attack")
                    self._separate_attack_sequences[payload["declaration_id"]] = {"seen": set(), "contexts": []}
                    self.resolve_separate_attack_segment(actor, queued_action)
                    return
                if self.basic_attack_area_cells_for_payload(actor, payload) is not None:
                    def resolve_area_attack() -> None:
                        area_cells = self.basic_attack_area_cells_for_payload(actor, payload)
                        self.basic_area_attack_with_payload(actor, payload, area_cells)

                    self.resolve_from_declared_origin(actor, payload, resolve_area_attack)
                    return
                target: Optional[Unit]
                declared = self.declared_target_position(payload)
                attack_cost = int(payload.get("attack_cost", 1))
                attack_name = str(payload.get("attack_name") or "普攻")
                if declared is not None:
                    target = self.resolve_declared_target_unit(
                        actor,
                        payload,
                        ignore_stealth=bool(payload.get("ignore_stealth")),
                    )
                    if target is None or (
                        target.player_id == actor.player_id and not payload.get("allow_allied_attack_target")
                    ):
                        actor.attacks_used += attack_cost
                        actor.actions_taken_this_turn.append("attack")
                        actor.consume_attack_attempt_buffs(self)
                        self.log(f"{actor.name} 的【{attack_name}】打在 ({declared.x}, {declared.y})，没有命中有效目标。")
                        actor.notify_basic_attack_finished(self, payload, [], missed=True)
                        return
                else:
                    target = self.effect_recipient(self.get_unit(payload["target_unit_id"]))
                self.resolve_from_declared_origin(actor, payload, lambda: self.basic_attack_with_payload(actor, target, payload))
                return
            if queued_action.action_type == "skill":
                if payload.get("action_failed_by_wuchang_mist"):
                    skill = actor.get_skill(payload["skill_code"])
                    skill.on_target_missed(self, actor, payload)
                    skill.finalize_use(self, actor)
                    actor.actions_taken_this_turn.append(f"skill:{skill.code}")
                    self.log(f"{actor.name} 的【{skill.name}】因【无常之雾】失败。")
                    return
                resolved_payload = dict(payload)
                resolved_target = self.resolve_declared_target_unit(
                    actor,
                    payload,
                    ignore_stealth=bool(payload.get("ignore_stealth")),
                )
                resolved_payload["resolved_target_unit_id"] = resolved_target.unit_id if resolved_target is not None else None
                if payload.get("resolves_from_current_position"):
                    self.use_skill(actor, payload["skill_code"], resolved_payload)
                else:
                    self.resolve_from_declared_origin(actor, payload, lambda: self.use_skill(actor, payload["skill_code"], resolved_payload))
                return
            if queued_action.action_type == "skill_effect":
                self.resolve_skill_effect(actor, queued_action)
                return
            if queued_action.action_type == "reaction_skill":
                source_action = self.source_action_for_reaction(queued_action)
                reaction_payload = dict(payload)
                resolved_target = self.resolve_declared_target_unit(
                    actor,
                    payload,
                    ignore_stealth=bool(payload.get("ignore_stealth")),
                )
                reaction_payload["resolved_target_unit_id"] = resolved_target.unit_id if resolved_target is not None else None
                option = ReactionOption(
                    unit_id=actor.unit_id,
                    action_code=payload["action_code"],
                    action_name=payload["action_name"],
                    action_type="skill",
                    timing="reaction",
                    chain_speed=queued_action.speed,
                    description=payload.get("description", ""),
                )
                self.execute_reaction_option(option, source_action, reaction_payload)
                if option.action_code == "evasion" and source_action.action_type == "attack":
                    # The reconstructed source is a copy. Return only the completed move receipt.
                    payload["completed_evasion_unit_ids"] = list(source_action.payload.get("completed_evasion_unit_ids", []))
                return
            if queued_action.action_type == "reaction_action":
                self.resolve_reaction_action(actor, payload["action_code"], self.source_action_for_reaction(queued_action))
                return

    def attack_would_deal_damage(self, queued: QueuedAction) -> bool:
        with battle_state_rollback(self), self.suppress_logs():
            self._measuring_attack_success = True
            self._attack_probe_success = False
            probe_actor = self.units.get(queued.actor_id)
            self._attack_probe_hp_only = probe_actor is not None and any(getattr(component, "reaction_prevention_requires_hp", False) for component in probe_actor.iter_components())
            try:
                self.resolve_queued_action(replace(queued, payload=dict(queued.payload)))
            except ActionError:
                return False
            return self._attack_probe_success

    def finalize_reaction_window(self) -> None:
        if self.pending_chain is None:
            return
        queued = self.pending_chain.queued_action
        reactions = list(reversed(self.pending_chain.chosen_reactions))
        queued.payload["enemy_reacted"] = any(reaction.source_player_id != queued.source_player_id for reaction in reactions)
        actor = self.units.get(queued.actor_id)
        track_prevention = queued.action_type == "attack" and actor is not None and any(
            getattr(component, "track_attack_reaction_prevention", False) for component in actor.iter_components()
        )
        queued.payload["enemy_skill_prevented_attack"] = False
        queued.payload["passive_attack_preventer_ids"] = []
        self.pending_chain = None
        chain_turn = self.turn_number
        try:
            for reaction in reactions:
                enemy_skill = track_prevention and reaction.action_type == "reaction_skill" and reaction.source_player_id != queued.source_player_id
                would_hit = self.attack_would_deal_damage(queued) if enemy_skill else False
                self.resolve_queued_action(reaction)
                if reaction.action_type == "reaction_skill" and reaction.payload.get("action_code") == "evasion":
                    completed = queued.payload.setdefault("completed_evasion_unit_ids", [])
                    for unit_id in reaction.payload.get("completed_evasion_unit_ids", []):
                        if unit_id not in completed:
                            completed.append(unit_id)
                if self.turn_number != chain_turn:
                    return
                if would_hit and not self.attack_would_deal_damage(queued):
                    queued.payload["enemy_skill_prevented_attack"] = True
                    reactor = self.units.get(reaction.actor_id)
                    if reactor is not None:
                        used = reactor.get_skill(reaction.payload.get("action_code") or reaction.payload["skill_code"])
                        if used.timing in {"passive", "reaction"}:
                            queued.payload["passive_attack_preventer_ids"].append(reactor.unit_id)
                if self.winner is not None:
                    return
            self.resolve_queued_action(queued)
        finally:
            self.expire_chain_temporary_statuses()
            self.advance_followup_actions()

    def advance_reaction_window(self) -> None:
        if self.pending_chain is None:
            return
        while self.pending_chain.pending_reactor_ids and not self.pending_chain.options_by_unit.get(self.pending_chain.pending_reactor_ids[0]):
            self.pending_chain.pending_reactor_ids.pop(0)
        if not self.pending_chain.pending_reactor_ids:
            self.finalize_reaction_window()

    def start_action_or_chain(self, payload: dict[str, Any]) -> None:
        self.enforce_sandstorm_stealth_rules()
        queued_action = self.build_queued_action(payload)
        actor = self.get_unit(queued_action.actor_id)
        with self.suppress_logs() if self.queued_action_hides_logs(queued_action) else nullcontext():
            reaction_window_timing = "before"
            if queued_action.action_type == "skill":
                skill = actor.get_skill(queued_action.payload["skill_code"])
                self.prepay_skill_resources(skill, actor, queued_action.payload)
                queued_action.payload["resources_prepaid"] = True
                reaction_window_timing = skill.reaction_window_timing(self, actor, queued_action.payload)
            if queued_action.action_type in {"attack", "skill"}:
                actor.notify_action_declared(self, queued_action.action_type, queued_action.payload)
                queued_action.payload["declared_source_attack"] = actor.stat("attack")
                declared_ids = [segment["target_unit_id"] for segment in queued_action.payload.get("separate_attack_segments", [])] or queued_action.target_unit_ids
                for target_id in declared_ids:
                    target = self.units.get(target_id)
                    if target is not None:
                        for component in list(target.iter_components()):
                            component.on_target_action_declared(self, actor, queued_action.action_type, queued_action.payload)
            if queued_action.payload.get("action_failed_by_wuchang_mist") or (queued_action.action_type == "skill" and reaction_window_timing == "after"):
                self.resolve_queued_action(queued_action)
                self.advance_followup_actions()
                return
            self.present_reaction_window_or_resolve(queued_action)

    def pass_turn(self, actor: Unit) -> None:
        actor.turn_ready = False
        self.log(f"{actor.name} 结束了自己的行动。")

    def cleanup_dead_units(self) -> None:
        while True:
            dead_ids = {unit.unit_id for unit in self.all_units() if not unit.alive}
            chained_summons = [
                unit
                for unit in self.all_units()
                if unit.alive and unit.summoner_id in dead_ids
            ]
            if not chained_summons:
                break
            for summon in chained_summons:
                summon.alive = False
                self.log(f"{summon.name} \u7684\u53ec\u5524\u8005\u5df2\u88ab\u51fb\u7834\uff0c\u56e0\u6b64\u4e00\u5e76\u6d88\u6563\u3002")
        dead_units = [unit for unit in self.all_units() if not unit.alive]
        for unit in dead_units:
            self.capture_destruction_position(unit)
            if unit.unit_id not in {destroyed.unit_id for destroyed in self.destroyed_units}:
                self.destroyed_units.append(unit)
            self.remove_unit(unit)
        self.sync_linked_units()
        self.notify_destroyed_hero_count_changed()
        self.clear_all_stealth_if_all_heroes_stealthed()
        self.check_win_condition()

    def destroyed_hero_count(self) -> int:
        units = {unit.unit_id: unit for unit in [*self.destroyed_units, *self.all_units()]}
        return sum(not unit.alive and isinstance(unit, HeroUnit)
                   and not unit.is_summon and not unit.is_clone and not is_army_soldier(unit)
                   for unit in units.values())

    def notify_destroyed_hero_count_changed(self) -> None:
        count = self.destroyed_hero_count()
        for unit in self.all_units():
            for component in list(unit.iter_components()):
                component.on_destroyed_hero_count_changed(self, count)

    def banish_unit(self, unit: Unit, turns: int) -> None:
        if unit.direct_effects_blocked():
            return
        action = self.resolving_action
        if (action is not None and action.action_type in {"skill", "reaction_skill", "skill_effect"}
                and not action.payload.get("from_field_effect")
                and self.destroy_clone_for_skill_effect(unit, source=self.units.get(action.actor_id), action_name=action.display_name)):
            return
        unit.banished = True
        unit.banish_turns_remaining = turns
        unit.banish_return_position = unit.position
        for component in list(unit.iter_components()):
            component.on_owner_banished(self)
        self.sync_linked_units()
        self.log(f"{unit.name} 消失了，暂时无法行动。")
        self.clear_all_stealth_if_all_heroes_stealthed()

    def summon_unit(
        self,
        unit: Unit,
        position: Position,
        *,
        summoner: Optional[Unit] = None,
        allow_stealth_overlap: bool = False,
    ) -> None:
        unit.summoner_id = summoner.unit_id if summoner is not None else None
        unit.can_act_on_entry_turn = True
        unit.turn_ready = False
        self.add_unit(unit, position, allow_stealth_overlap=allow_stealth_overlap)
        self.log(f"{unit.name} 被召唤到战场。")

    def check_win_condition(self) -> None:
        if self.winner is not None:
            return
        alive_players = {
            player_id
            for player_id in (1, 2)
            if any(not bool(getattr(unit, "is_siege_structure", False)) for unit in self.hero_units(player_id))
        }
        if len(alive_players) == 1 and self.units:
            self.winner = alive_players.pop()
            losing_player = 2 if self.winner == 1 else 1
            self.win_reason_code = "elimination"
            self.win_reason_text = f"玩家 {losing_player} 的全部武将被击破。"
            self._append_summary_event(
                "match_end",
                actor_unit_id=None,
                actor_name="系统",
                actor_player_id=self.winner,
                target_name=f"玩家 {losing_player}",
                action_name="全部武将被击破",
                amount=0,
            )
            self.log(f"玩家 {self.winner} 获胜。")
            self._emit_replay_checkpoint("match_end")

    def perform_action(self, payload: dict[str, Any]) -> None:
        pending = self.pending_damage_choice
        if pending is not None:
            if payload.get("type") != "damage_choice" or payload.get("unit_id") != pending["unit_id"]:
                raise ActionError("请先完成本次伤害选择。")
            if pending.get("kind") in {"rotation", "attack_swap", "formation"}:
                choice = str(payload.get("target_unit_id") or "")
                if (choice == "decline" and pending.get("kind") in {"attack_swap", "formation"}) or (choice != "decline" and choice not in pending["options"]):
                    raise ActionError("请选择当前提示中的合法对象与落点。")
            else:
                choice = str(payload.get("stat_name") or "")
                if choice != "decline" and choice not in pending["stats"]:
                    raise ActionError("请选择一项可降低的能力，或放弃抵消。")
            decisions = [*pending["decisions"], choice]
            self.pending_damage_choice = None
            try:
                self._perform_action_with_damage_choices(pending["action_payload"], decisions)
            except Exception:
                self.pending_damage_choice = pending
                raise
            return
        if payload.get("type") == "damage_choice":
            raise ActionError("现在没有待决定的伤害。")
        if not getattr(self, "_ai_probe_active", False) and any(
            getattr(component, "requires_damage_choice", False)
            for unit in self.all_units() if unit.alive and not unit.banished
            for component in unit.iter_components()
        ):
            self._perform_action_with_damage_choices(payload, [])
            return
        self._perform_action_steps(payload)

    def _perform_action_steps(self, payload: dict[str, Any]) -> None:
        self.sync_linked_units()
        self._perform_action(payload)
        self.sync_linked_units()
        self.finish_destroyed_active_turn_if_idle()

    def _perform_action_with_damage_choices(self, payload: dict[str, Any], decisions: list[str]) -> None:
        try:
            with battle_state_rollback(self, ai_probe=False):
                self._damage_choice_active = True
                self._damage_choice_decisions = decisions
                self._damage_choice_event_index = 0
                self._perform_action_steps(deepcopy(payload))
        except DamageChoiceRequired as required:
            self.pending_damage_choice = {
                "prompt_id": f"damage-{next(_id_counter)}",
                "unit_id": required.unit_id,
                "kind": required.kind,
                "action_name": required.action_name,
                "damage": required.damage,
                "event_index": required.event_index,
                "stats": required.stats,
                "options": required.options,
                "action_payload": deepcopy(payload),
                "decisions": list(decisions),
            }
            return
        self._damage_choice_active = True
        self._damage_choice_decisions = decisions
        self._damage_choice_event_index = 0
        try:
            self._perform_action_steps(deepcopy(payload))
        finally:
            self._damage_choice_active = False
            self._damage_choice_decisions = []
            self._damage_choice_event_index = 0

    def damage_stat_choice(self, ctx: DamageContext, stats: list[str]) -> Optional[str]:
        if not getattr(self, "_damage_choice_active", False) or getattr(self, "_ai_probe_active", False):
            return None
        event_index = int(getattr(self, "_damage_choice_event_index", 0))
        self._damage_choice_event_index = event_index + 1
        decisions = getattr(self, "_damage_choice_decisions", [])
        if event_index < len(decisions):
            stat = decisions[event_index]
            return stat if stat in stats else None
        if getattr(self, "_damage_choice_probe_default", False):
            return None
        amount = ctx.raw_damage if ctx.raw_damage is not None else self.damage_rule.calculate_damage(
            ctx.attack_power, ctx.target.stat("defense")
        )
        raise DamageChoiceRequired(
            unit_id=ctx.target.unit_id,
            action_name=("未知伤害" if ctx.source is not None and ctx.source.is_stealthed()
                         and ctx.source.player_id != ctx.target.player_id else ctx.action_name),
            damage=round(min(float(ctx.target.current_hp), float(ctx.target.damage_fraction_after_limits(amount))), 4),
            event_index=event_index,
            stats=list(stats),
        )

    def damage_rotation_choice(self, ctx: DamageContext, options: list[str]) -> Optional[str]:
        if not getattr(self, "_damage_choice_active", False) or getattr(self, "_ai_probe_active", False):
            return None
        event_index = int(getattr(self, "_damage_choice_event_index", 0))
        self._damage_choice_event_index = event_index + 1
        decisions = getattr(self, "_damage_choice_decisions", [])
        if event_index < len(decisions):
            choice = decisions[event_index]
            return choice if choice in options else None
        if getattr(self, "_damage_choice_probe_default", False):
            return None
        raise DamageChoiceRequired(
            unit_id=ctx.target.unit_id,
            action_name=("未知伤害" if ctx.source is not None and ctx.source.is_stealthed()
                         and ctx.source.player_id != ctx.target.player_id else ctx.action_name),
            damage=round(float(ctx.actual_damage), 4),
            event_index=event_index,
            kind="rotation",
            options=list(options),
        )

    def attack_swap_choice(self, actor: Unit, options: list[str]) -> Optional[str]:
        if not getattr(self, "_damage_choice_active", False) or getattr(self, "_ai_probe_active", False):
            return options[0] if options else None
        event_index = int(getattr(self, "_damage_choice_event_index", 0))
        self._damage_choice_event_index = event_index + 1
        decisions = getattr(self, "_damage_choice_decisions", [])
        if event_index < len(decisions):
            choice = decisions[event_index]
            return choice if choice in options else None
        if getattr(self, "_damage_choice_probe_default", False):
            return options[0] if options else None
        raise DamageChoiceRequired(
            unit_id=actor.unit_id,
            action_name="普攻换位",
            damage=0,
            event_index=event_index,
            kind="attack_swap",
            options=list(options),
        )

    def formation_placement_choice(self, actor: Unit, options: list[str], index: int) -> Optional[str]:
        if not options:
            return None
        if not getattr(self, "_damage_choice_active", False) or getattr(self, "_ai_probe_active", False):
            return options[0]
        event_index = int(getattr(self, "_damage_choice_event_index", 0))
        self._damage_choice_event_index = event_index + 1
        decisions = getattr(self, "_damage_choice_decisions", [])
        if event_index < len(decisions):
            choice = decisions[event_index]
            return choice if choice in options else None
        if getattr(self, "_damage_choice_probe_default", False):
            return options[0]
        raise DamageChoiceRequired(
            unit_id=actor.unit_id, action_name=f"剑斗布阵第{index}位", damage=0,
            event_index=event_index, kind="formation", options=list(options),
        )

    def finish_destroyed_active_turn_if_idle(self) -> None:
        if (
            self.winner is not None
            or self.pending_chain is not None
            or self.pending_followup_actions
            or self.pending_respawn_unit_ids
        ):
            return
        active_unit_id = self.current_turn_slot_unit_id()
        if active_unit_id is None or parse_army_slot(active_unit_id) is not None:
            return
        active_unit = self.units.get(active_unit_id)
        if active_unit is not None and active_unit.alive:
            return
        self.resolve_turn_end(auto_started=True)

    def _perform_action(self, payload: dict[str, Any]) -> None:
        if self.winner is not None:
            raise ActionError("对局已结束，请返回选将页面开始新的对局。")
        action_type = payload.get("type")
        if self.pending_respawn_unit_ids:
            if action_type != "respawn_select":
                raise ActionError("当前需要先为消失单位选择重新出现的位置。")
            prompt = self.current_respawn_prompt()
            if prompt is None:
                self.pending_respawn_unit_ids = []
                return
            if payload.get("unit_id") != prompt.unit_id:
                raise ActionError("现在需要先处理当前等待重新出现的单位。")
            destination = Position(int(payload["x"]), int(payload["y"]))
            unit = self.get_unit(prompt.unit_id)
            if destination not in self.respawn_options_for(unit):
                raise ActionError("该位置不能作为重新出现的落点。")
            if not self.can_place_unit(unit, destination, ignore=unit, mover=unit):
                raise ActionError("该位置已被占用，无法重新出现。")
            self.pending_respawn_unit_ids.pop(0)
            self.restore_banished_unit(unit, destination)
            self.advance_respawn_queue()
            return
        if self.pending_chain is not None:
            current_unit_id = self.pending_chain.current_unit_id()
            if action_type == "chain_skip":
                if current_unit_id is None:
                    self.finalize_reaction_window()
                    return
                unit = self.get_unit(current_unit_id)
                if unit.is_stealthed():
                    self.pending_chain.decision_log.append("有单位放弃了连锁。")
                else:
                    self.pending_chain.decision_log.append(f"{unit.name} 放弃连锁。")
                    self.log(f"{unit.name} 放弃了连锁。")
                self.pending_chain.pending_reactor_ids.pop(0)
                self.advance_reaction_window()
                return
            if action_type == "chain_react":
                if current_unit_id is None:
                    self.finalize_reaction_window()
                    return
                if payload.get("unit_id") != current_unit_id:
                    raise ActionError("现在还没有轮到这个单位连锁。")
                options = self.pending_chain.options_by_unit.get(current_unit_id, [])
                action_code = payload.get("action_code")
                chosen = next((option for option in options if option.action_code == action_code), None)
                if chosen is None:
                    raise ActionError("该单位当前不能使用这个连锁动作。")
                reactor = self.get_unit(current_unit_id)
                reaction_payload = {
                    key: value
                    for key, value in payload.items()
                    if key not in {"type", "unit_id", "action_code"}
                }
                if chosen.action_type == "skill":
                    skill = reactor.get_skill(chosen.action_code)
                    ok, reason = skill.can_react_with_payload(
                        self,
                        reactor,
                        self.pending_chain.queued_action,
                        reaction_payload,
                    )
                    if not ok:
                        raise ActionError(reason)
                    for field in ("kings_insight_checked", "wuchang_mist_checked", "action_failed_by_wuchang_mist"):
                        reaction_payload.pop(field, None)
                    reaction_payload.update(skill.queued_reaction_payload_metadata(self, reactor, reaction_payload))
                    self.prepay_skill_resources(skill, reactor, reaction_payload)
                    reaction_payload["resources_prepaid"] = True
                    target_ids = self.payload_target_unit_ids(reaction_payload)
                    if target_ids:
                        target = self.get_unit(target_ids[0])
                        declared_target = self.declared_cell_for_target(reactor, target, reaction_payload)
                        if declared_target is not None:
                            reaction_payload["declared_target_x"] = declared_target.x
                            reaction_payload["declared_target_y"] = declared_target.y
                    reactor.notify_action_declared(
                        self,
                        "skill",
                        {
                            **reaction_payload,
                            "unit_id": reactor.unit_id,
                            "skill_code": chosen.action_code,
                            "queued_resolution": True,
                        },
                    )
                    reaction_payload["declared_source_attack"] = reactor.stat("attack")
                    if reactor.unit_id not in self.units or not reactor.alive or reactor.banished:
                        skill.finalize_use(self, reactor)
                        self.pending_chain.decision_log.append(f"{reactor.name} 的连锁因自身离场而失效。")
                        self.log(f"{reactor.name} 的【{chosen.action_name}】未能结算，因为使用者已不在战场。")
                        self.pending_chain.pending_reactor_ids.pop(0)
                        self.advance_reaction_window()
                        return
                    if chosen.action_code == "evasion" and self.pending_chain.queued_action.action_type == "attack":
                        evaded = self.pending_chain.queued_action.payload.setdefault("evaded_unit_ids", [])
                        if reactor.unit_id not in evaded:
                            evaded.append(reactor.unit_id)
                queued = QueuedAction(
                    action_type="reaction_skill" if chosen.action_type == "skill" else "reaction_action",
                    actor_id=current_unit_id,
                    display_name=chosen.action_name,
                    speed=chosen.chain_speed,
                    payload={
                        "action_code": chosen.action_code,
                        "action_name": chosen.action_name,
                        "description": chosen.description,
                        **reaction_payload,
                        "source_action_type": self.pending_chain.queued_action.action_type,
                        "source_actor_id": self.pending_chain.queued_action.actor_id,
                        "source_display_name": self.pending_chain.queued_action.display_name,
                        "source_speed": self.pending_chain.queued_action.speed,
                        "source_payload": dict(self.pending_chain.queued_action.payload),
                        "source_target_unit_ids": list(self.pending_chain.queued_action.target_unit_ids),
                        "source_target_cells": [cell.to_dict() for cell in self.pending_chain.queued_action.target_cells],
                        "source_player_id": self.pending_chain.queued_action.source_player_id,
                        "source_hostile": self.pending_chain.queued_action.hostile,
                    },
                    target_unit_ids=list(dict.fromkeys([self.pending_chain.queued_action.actor_id, *[
                        unit.unit_id for unit in self.effect_units_at_cells(self.payload_positions(reaction_payload, "reaction_effect_cells"))
                        if unit.player_id != reactor.player_id
                    ]])),
                    target_cells=self.payload_positions(reaction_payload, "reaction_effect_cells"),
                    source_player_id=reactor.player_id,
                    hostile=True,
                    reaction_source_id=self.pending_chain.queued_action.actor_id,
                    suppress_logs=reactor.is_stealthed() or self.pending_chain.queued_action.suppress_logs,
                )
                self.pending_chain.chosen_reactions.append(queued)
                if reactor.is_stealthed():
                    self.pending_chain.decision_log.append("有单位进行了连锁选择。")
                else:
                    self.pending_chain.decision_log.append(f"{self.get_unit(current_unit_id).name} 选择了 {chosen.action_name}。")
                self.pending_chain.pending_reactor_ids.pop(0)
                self.advance_reaction_window()
                return
            raise ActionError("当前正在等待连锁响应。")
        if action_type == "end_turn":
            self.end_turn()
            return
        if action_type in {"move", "attack", "skill"}:
            actor_id = payload.get("unit_id")
            if actor_id:
                actor = self.get_unit(str(actor_id))
                if is_army_soldier(actor):
                    raise ActionError("士兵由军队指令统一行动，不能单独操控。")
            self.start_action_or_chain(payload)
            return
        unit = self.get_unit(payload["unit_id"])
        if is_army_soldier(unit):
            raise ActionError("士兵由军队指令统一行动，不能单独操控。")
        if action_type == "pass_unit":
            self.pass_turn(unit)
            return
        raise ActionError("未知动作类型。")

    def action_snapshot_for(self, unit: Unit) -> dict[str, Any]:
        move_targets = [
            pos.to_dict()
            for pos in self.reachable_positions(
                unit,
                max_distance=unit.remaining_normal_move_distance(self),
                use_movement_cost=True,
            )
        ]
        forced_movement = False
        for component in unit.iter_components():
            forced_paths = component.forced_normal_move_paths(self, unit)
            if forced_paths is not None:
                forced_movement = True
                move_targets = [route[-1].to_dict() for route in forced_paths]
        can_normal_move = (
            unit.can_take_turn_actions(self)
            and unit.remaining_normal_move_distance(self) > 0
            and not unit.cannot_move
            and not unit.cannot_normal_move
            and bool(move_targets)
        )
        attack_targets: list[str] = []
        attack_cells: list[dict[str, int]] = []
        actions = []
        actions.append(
            {
                "code": "move",
                "name": "移动",
                "kind": "move",
                "timing": "active",
                "chain_speed": 1,
                "description": "普通移动。",
                "available": can_normal_move,
                "preview": {
                    "cells": move_targets,
                    "target_unit_ids": [],
                    "requires_target": True,
                    "selection": {
                        "mode": "forced_destination" if forced_movement else "move_path",
                        "max_steps": unit.remaining_normal_move_distance(self),
                    },
                },
            }
        )
        attack_available = (
            unit.can_take_turn_actions(self)
            and not unit.cannot_attack
            and not self.shared_stealth_action_block_reason(unit)
        )
        seen_attack_targets: set[str] = set()
        seen_attack_cells: set[tuple[int, int]] = set()
        for spec in self.basic_attack_action_specs(unit):
            attack_payload = dict(spec.get("attack_payload") or {})
            resolved_payload = self.resolved_basic_attack_payload(unit, attack_payload)
            preview = self.basic_attack_preview_for_payload(unit, resolved_payload)
            action_available = attack_available and self.basic_attack_resource_allowed(unit, resolved_payload)[0]
            for unit_id in preview.get("target_unit_ids", []):
                if unit_id in seen_attack_targets:
                    continue
                seen_attack_targets.add(unit_id)
                attack_targets.append(unit_id)
            for cell in preview.get("cells", []):
                if not isinstance(cell, dict) or cell.get("x") is None or cell.get("y") is None:
                    continue
                key = (int(cell["x"]), int(cell["y"]))
                if key in seen_attack_cells:
                    continue
                seen_attack_cells.add(key)
                attack_cells.append({"x": key[0], "y": key[1]})
            action_entry = {
                "code": str(spec.get("code") or "attack"),
                "name": str(spec.get("name") or "普攻"),
                "kind": "attack",
                "timing": "active",
                "chain_speed": 1,
                "description": (str(spec.get("description") or "普通攻击。") + (" 可选择己方单位（含自身）治疗1/4，消耗一次普攻。" if preview.get("allied_heal_target_ids") and resolved_payload.get("attack_variant") != "allied_heal" else "")),
                "available": action_available,
                "preview": preview,
            }
            if attack_payload:
                action_entry["attack_payload"] = attack_payload
            if spec.get("is_attack_variant"):
                action_entry["is_attack_variant"] = True
            actions.append(action_entry)
        for skill in unit.action_skills():
            data = skill.to_public_dict(self)
            data["available"] = (
                unit.can_take_turn_actions(self) and skill.can_use(self, unit, {})[0]
                if skill.timing == "active"
                else skill.can_use(self, unit, {})[0]
                if skill.timing == "instant"
                else False
            )
            data["kind"] = "skill"
            data["preview"] = self.filter_preview_targets(
                unit,
                skill.preview(self, unit),
                ignore_stealth=skill.ignores_stealth_for_payload(self, unit, {}),
                replace_cells=data["target_mode"] in {"ally", "enemy", "unit"},
                require_line_targeting=data["target_mode"] in {"ally", "enemy", "unit"}
                and skill.requires_direct_unit_target_line,
                line_target_range=skill.direct_unit_target_range(self, unit, {}),
            )
            actions.append(data)
        return {
            "move_targets": move_targets,
            "attack_targets": attack_targets,
            "skills": [skill.to_public_dict(self) for skill in unit.skills],
            "actions": actions,
            "can_move": can_normal_move,
            "attacks_left": max(unit.attack_actions_per_turn() - unit.attacks_used, 0),
        }

    def reaction_snapshot_for(self, unit: Unit) -> dict[str, Any]:
        if self.pending_chain is None:
            return {"actions": []}
        options = self.pending_chain.options_by_unit.get(unit.unit_id, [])
        return {"actions": [option.to_public_dict() for option in options]}

    def unit_has_available_instant_action(self, unit: Unit) -> bool:
        if unit is None or not unit.alive or unit.banished:
            return False
        if self.pending_chain is not None or self.current_respawn_prompt() is not None:
            return False
        return any(skill.timing == "instant" and skill.can_use(self, unit, {})[0] for skill in unit.skills)

    def instant_action_units_for_player(self, player_id: int) -> list[Unit]:
        return [
            unit
            for unit in self.player_units(player_id)
            if self.unit_has_available_instant_action(unit)
        ]

    def to_public_dict(self) -> dict[str, Any]:
        respawn_prompt = self.current_respawn_prompt()
        damage_prompt = self.pending_damage_choice
        current_turn_unit = self.current_turn_unit()
        next_turn_unit = self.peek_next_turn_unit()
        army_turn = self.is_army_turn()
        return {
            "board": {
                "width": self.width,
                "height": self.height,
                "terrain": [
                    {"x": x, "y": y, "kind": "wall"}
                    for x, y in sorted(self.blocked_cells)
                ],
            },
            "active_player": self.active_player,
            "active_turn_unit_id": current_turn_unit.unit_id if current_turn_unit is not None else self.current_turn_slot_unit_id(),
            "control_handoff_unit_id": (
                next((unit.unit_id for unit in self.current_turn_bundle_units()), None)
                if current_turn_unit is not None and current_turn_unit.player_id != self.active_player else None
            ),
            "active_turn_unit_name": (
                current_turn_unit.name
                if current_turn_unit is not None
                else ("军队" if army_turn else None)
            ),
            "next_turn_unit_id": next_turn_unit.unit_id if next_turn_unit is not None else None,
            "next_turn_unit_name": next_turn_unit.name if next_turn_unit is not None else None,
            "next_turn_player_id": next_turn_unit.player_id if next_turn_unit is not None else None,
            "input_player": (
                self.get_unit(damage_prompt["unit_id"]).player_id
                if damage_prompt is not None
                else respawn_prompt.player_id
                if respawn_prompt is not None
                else (self.pending_chain.reactive_player_id if self.pending_chain else self.active_player)
            ),
            "turn_number": self.turn_number,
            "round_number": self.round_number,
            "completed_turns": self.completed_turns,
            "turn_timeout_limit": int(getattr(self, "turn_timeout_limit", 0) or 0),
            "turn_timeout_winner": int(getattr(self, "turn_timeout_winner", 2) or 2),
            "turn_order_unit_ids": list(self.turn_order_unit_ids),
            "winner": self.winner,
            "win_reason_code": self.win_reason_code if self.winner is not None else "",
            "win_reason_text": self.win_reason_text if self.winner is not None else "",
            "summary_event_count": self._next_summary_event_id - 1,
            "damage_rule": self.damage_rule.name,
            "units": [unit.to_public_dict(self) for unit in self.all_units()],
            "destroyed_units": [unit.to_public_dict(self) for unit in self.destroyed_units],
            "field_effects": [effect.to_public_dict(self) for effect in self.field_effects],
            "pending_chain": self.pending_chain.to_public_dict(self) if self.pending_chain else None,
            "pending_respawn": respawn_prompt.to_public_dict() if respawn_prompt else None,
            "pending_damage_choice": (
                {**{key: damage_prompt[key] for key in ("unit_id", "action_name", "damage", "event_index", "stats")},
                 "kind": damage_prompt.get("kind", "stat"), "options": damage_prompt.get("options", [])}
                if damage_prompt is not None else None
            ),
            "logs": self.logs,
            "visual_events": [event.to_public_dict() for event in self.visual_events],
            "is_army_turn": army_turn,
            "army": army_public_state(self),
        }
