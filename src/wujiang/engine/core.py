from __future__ import annotations

from abc import ABC, abstractmethod
from collections import deque
from contextlib import contextmanager, nullcontext
from dataclasses import dataclass, field
import heapq
from itertools import count
from typing import Any, Iterable, Literal, Optional


_id_counter = count(1)


class ActionError(RuntimeError):
    """Raised when an action cannot be performed."""


class ActionMiss(ActionError):
    """Raised when a queued action resolves on its original cell but misses."""


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

    def __init__(self, name: str, description: str = "") -> None:
        self.name = name
        self.description = description
        self.owner: Optional["Unit"] = None
        self.component_id = f"cmp-{next(_id_counter)}"

    def bind(self, owner: "Unit") -> "BattleComponent":
        self.owner = owner
        return self

    def modify_stat(self, stat_name: str, value: float) -> float:
        return value

    def modify_attack_actions_per_turn(self, value: int) -> int:
        return value

    def modify_targeting_range(self, value: int) -> int:
        return value

    def modify_normal_move_distance(self, value: int) -> int:
        return value

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

    def on_unit_moved(self, battle: "Battle", ctx: "MoveContext") -> None:
        return None

    def on_before_damage(self, battle: "Battle", ctx: "DamageContext") -> None:
        return None

    def on_after_damage(self, battle: "Battle", ctx: "DamageContext") -> None:
        return None

    def on_damage_cancelled(self, battle: "Battle", ctx: "DamageContext") -> None:
        return None

    def on_before_heal(self, battle: "Battle", ctx: "HealContext") -> None:
        return None

    def on_after_heal(self, battle: "Battle", ctx: "HealContext") -> None:
        return None

    def on_owner_removed(self, battle: "Battle") -> None:
        return None

    def on_enter_battle(self, battle: "Battle") -> None:
        return None

    def on_removed(self, battle: "Battle") -> None:
        return None

    def can_attack_target(
        self,
        battle: "Battle",
        actor: "Unit",
        target: "Unit",
    ) -> tuple[bool, str]:
        return True, ""

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
        tick_scope: Literal["owner_turn_end", "any_turn_end"] = "owner_turn_end",
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
    kind = "skill"

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
        if self.is_reaction:
            return False, "该技能需要在连锁时使用。"
        if not actor.can_take_turn_actions(battle):
            return False, "这个单位当前不能行动。"
        if actor.player_id != battle.active_player:
            return False, "还没有轮到这个单位行动。"
        if actor.banished:
            return False, "该单位暂时不在战场上。"
        if actor.cannot_use_skills:
            return False, "这个单位当前不能使用技能。"
        if self.cooldown_remaining > 0:
            return False, f"还需冷却 {self.cooldown_remaining} 个回合。"
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

    def on_owner_turn_start(self, battle: "Battle") -> None:
        self.uses_this_turn = 0

    def on_any_turn_end(self, battle: "Battle", ended_player_id: int) -> None:
        if self.cooldown_remaining > 0:
            self.cooldown_remaining -= 1

    def mana_cost_for_payload(
        self,
        battle: "Battle",
        actor: "Unit",
        payload: Optional[dict[str, Any]] = None,
    ) -> float:
        return self.mana_cost

    def mana_cost_text(self) -> Optional[str]:
        return None

    def prepay_resources(
        self,
        battle: "Battle",
        actor: "Unit",
        payload: Optional[dict[str, Any]] = None,
    ) -> None:
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
        if not self.is_reaction:
            return False, "不是连锁技能。"
        if actor.banished or not actor.alive:
            return False, "单位不在战场上。"
        if actor.cannot_use_skills:
            return False, "这个单位当前不能使用技能。"
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
        return False

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

    def to_public_dict(self, battle: "Battle") -> dict[str, Any]:
        data = super().to_public_dict(battle)
        data.update(
            {
                "code": self.code,
                "mana_cost": self.mana_cost,
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
    from_field_effect: bool = False
    cannot_evade: bool = False
    shield_consumed: bool = False
    cancelled: bool = False
    reason: str = ""
    tags: set[str] = field(default_factory=set)


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
    from_field_effect: bool = False
    cannot_evade: bool = False
    area_cell_hits: int = 1
    raw_damage: Optional[float] = None
    shield_consumed: bool = False
    cancelled: bool = False
    reason: str = ""
    lethal: bool = False
    destroyed_as_clone: bool = False
    tags: set[str] = field(default_factory=set)

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

    def to_public_dict(self) -> dict[str, Any]:
        return {
            "action_type": self.action_type,
            "actor_id": self.actor_id,
            "display_name": self.display_name,
            "speed": self.speed,
            "payload": self.payload,
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
        self.footprint_width = max(1, int(getattr(self, "footprint_width", 1)))
        self.footprint_height = max(1, int(getattr(self, "footprint_height", 1)))
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
        self.cannot_be_targeted = False
        self.cannot_move = False
        self.cannot_normal_move = False
        self.cannot_heal = False
        self.cannot_attack = is_clone
        self.cannot_use_skills = is_clone
        self.ignore_units_while_moving = False
        self.has_flying = False
        self.has_block_counter = False
        self.is_summon = is_summon
        self.is_clone = is_clone
        self.summoner_id: Optional[str] = None
        self.can_act_on_entry_turn = False
        self.turn_ready = True
        self.move_used = False
        self.attacks_used = 0
        self.performed_active_skill = False
        self.moved_this_turn = False
        self.actions_taken_this_turn: list[str] = []
        self.base_attack_actions_per_turn = 1
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

    def iter_components(self) -> Iterable[BattleComponent]:
        yield from self.skills
        yield from self.traits
        yield from self.statuses

    def skill_map(self) -> dict[str, Skill]:
        return {skill.code: skill for skill in self.skills}

    def get_skill(self, code: str) -> Skill:
        for skill in self.skills:
            if skill.code == code:
                return skill
        raise ActionError(f"{self.name} 没有技能 {code}")

    def add_status(self, status: StatusEffect) -> None:
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
        for component in list(self.iter_components()):
            component.on_owner_action_declared(battle, action_type, payload)

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

    def total_shields(self) -> int:
        return self.shields + self.temporary_shields

    def add_temporary_shields(self, amount: int) -> None:
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
        return round(max(float(self.stat("mana")), 0.0), 2)

    def clamp_mana(self) -> None:
        self.current_mana = round(min(max(self.current_mana, 0.0), self.max_mana()), 2)

    def gain_mana(self, amount: float) -> float:
        before = self.current_mana
        self.current_mana = round(self.current_mana + amount, 2)
        self.clamp_mana()
        return round(self.current_mana - before, 2)

    def spend_mana(self, amount: float) -> float:
        before = self.current_mana
        self.current_mana = round(self.current_mana - amount, 2)
        self.clamp_mana()
        return round(before - self.current_mana, 2)

    def gain_mana_points(self, amount: float) -> float:
        before = self.mana_points
        self.mana_points = round(max(self.mana_points + amount, 0.0), 2)
        return round(self.mana_points - before, 2)

    def spend_mana_points(self, amount: float) -> float:
        before = self.mana_points
        self.mana_points = round(max(self.mana_points - amount, 0.0), 2)
        return round(before - self.mana_points, 2)

    def stat(self, stat_name: Literal["attack", "defense", "speed", "attack_range", "mana"]) -> float:
        base_value = getattr(self.base_stats, stat_name)
        value = float(base_value)
        for component in self.iter_components():
            value = component.modify_stat(stat_name, value)
        if stat_name in {"attack", "defense", "speed", "attack_range"}:
            return max(1.0, value)
        return value

    def targeting_range(self) -> int:
        value = int(self.stat("attack_range"))
        for component in self.iter_components():
            value = component.modify_targeting_range(value)
        return max(1, value)

    def is_enemy_of(self, other: "Unit") -> bool:
        return self.player_id != other.player_id

    def heal_fraction(self, amount: float) -> None:
        self.current_hp = round(min(self.max_health, self.current_hp + amount), 4)

    def take_damage_fraction(self, amount: float) -> None:
        self.current_hp = round(max(0.0, self.current_hp - amount), 4)
        if self.current_hp <= 0:
            self.alive = False

    def can_take_turn_actions(self, battle: "Battle") -> bool:
        return (
            self.alive
            and not self.banished
            and self.player_id == battle.active_player
            and self.turn_ready
        )

    def refresh_for_turn(self, battle: "Battle") -> None:
        self.move_used = False
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
            "is_clone": self.is_clone,
            "turn_ready": self.turn_ready,
            "position": self.position.to_dict() if self.position else None,
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
            "attacks_used": self.attacks_used,
            "attacks_per_turn": self.attack_actions_per_turn(),
            "performed_active_skill": self.performed_active_skill,
            "moved_this_turn": self.moved_this_turn,
            "shields": self.shields,
            "temporary_shields": self.temporary_shields,
            "total_shields": self.total_shields(),
            "dodge_charges": self.dodge_charges,
            "magic_immunity": self.magic_immunity,
            "cannot_be_targeted": self.cannot_be_targeted,
            "cannot_move": self.cannot_move,
            "cannot_normal_move": self.cannot_normal_move,
            "cannot_heal": self.cannot_heal,
            "cannot_attack": self.cannot_attack,
            "cannot_use_skills": self.cannot_use_skills,
            "raw_skill_text": self.raw_skill_text,
            "raw_trait_text": self.raw_trait_text,
            "skills": [skill.to_public_dict(battle) for skill in self.skills],
            "traits": [trait.to_public_dict(battle) for trait in self.traits],
            "statuses": [status.to_public_dict(battle) for status in self.statuses],
        }


class HeroUnit(Unit, ABC):
    pass


class Battle:
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
        self.field_effects: list[BattleFieldEffect] = []
        self.active_player = 1
        self.turn_number = 1
        self.round_number = 1
        self.winner: Optional[int] = None
        self.logs: list[str] = []
        self.pending_chain: Optional[ReactionWindow] = None
        self.pending_respawn_unit_ids: list[str] = []
        self._log_suppression_depth = 0

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

    def add_unit(self, unit: Unit, position: Position) -> None:
        if not self.can_place_unit(unit, position, ignore=unit, mover=unit):
            raise ActionError("目标位置已被占用。")
        unit.position = position
        self.units[unit.unit_id] = unit
        self.log(f"{unit.name} 进入战场。")
        for component in list(unit.iter_components()):
            component.on_enter_battle(self)

    def remove_unit(self, unit: Unit) -> None:
        for component in list(unit.iter_components()):
            component.on_owner_removed(self)
        if unit.unit_id in self.units:
            del self.units[unit.unit_id]

    def add_field_effect(self, effect: BattleFieldEffect) -> None:
        if effect.merge_into_existing(self, self.field_effects):
            return
        self.field_effects.append(effect)
        self.log(f"场地效果【{effect.name}】生效。")

    def remove_field_effect(self, effect: BattleFieldEffect) -> None:
        if effect in self.field_effects:
            self.field_effects.remove(effect)
            self.log(f"场地效果【{effect.name}】结束。")

    def has_weather(self, name: str) -> bool:
        return any(getattr(effect, "weather_name", None) == name for effect in self.field_effects)

    def cell_has_weather(self, name: str, cell: Position) -> bool:
        for effect in self.field_effects:
            if getattr(effect, "weather_name", None) != name:
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
        return any(self.cell_has_weather(name, cell) for cell in self.unit_cells(unit))

    def start_battle(self) -> None:
        self.start_player_turn(self.active_player)

    def start_player_turn(self, player_id: int) -> None:
        self.active_player = player_id
        self.pending_respawn_unit_ids = []
        for unit in self.player_units(player_id):
            unit.refresh_for_turn(self)
        for unit in self.all_units():
            if unit.banished and unit.player_id == player_id:
                if unit.banish_turns_remaining > 0:
                    unit.banish_turns_remaining = max(unit.banish_turns_remaining - 1, 0)
                if unit.banish_turns_remaining == 0:
                    self.schedule_respawn(unit)
        self.advance_respawn_queue()
        self.log(f"第 {self.round_number} 轮，玩家 {player_id} 的回合开始。")
        self.check_win_condition()

    def end_turn(self) -> None:
        if self.pending_chain is not None:
            raise ActionError("当前正在等待连锁结算，不能结束回合。")
        ending_player = self.active_player
        for unit in self.player_units(ending_player):
            unit.finish_turn(self)
        for unit in self.all_units():
            if unit.total_shields() > 0:
                unit.clear_end_of_turn_shields()
        for effect in list(self.field_effects):
            effect.on_any_turn_end(self, ending_player)
        for unit in self.all_units():
            for component in list(unit.iter_components()):
                component.on_any_turn_end(self, ending_player)
        self.cleanup_dead_units()
        if self.winner is not None:
            return
        next_player = 2 if ending_player == 1 else 1
        if next_player == 1:
            self.round_number += 1
        self.turn_number += 1
        self.start_player_turn(next_player)

    def all_units(self) -> list[Unit]:
        return list(self.units.values())

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

    def can_place_unit(
        self,
        unit: Unit,
        position: Position,
        *,
        ignore: Optional[Unit] = None,
        mover: Optional[Unit] = None,
        ignore_units: bool = False,
    ) -> bool:
        for cell in self.unit_cells_at(unit, position):
            if not self.in_bounds(cell):
                return False
            if not ignore_units and self.is_occupied(cell, ignore=ignore, mover=mover or unit):
                return False
        return True

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

    def blocks_position_for(self, occupant: Unit, *, mover: Optional[Unit] = None) -> bool:
        if occupant.is_stealthed():
            return False
        if mover is not None and mover.is_stealthed():
            return False
        return True

    def is_occupied(
        self,
        position: Position,
        *,
        ignore: Optional[Unit] = None,
        mover: Optional[Unit] = None,
    ) -> bool:
        return any(self.blocks_position_for(unit, mover=mover) for unit in self.units_at(position, ignore=ignore))

    def unit_at(self, position: Position) -> Optional[Unit]:
        occupants = self.units_at(position)
        if not occupants:
            return None
        visible = [unit for unit in occupants if not unit.is_stealthed()]
        return visible[0] if visible else occupants[0]

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
        for unit in occupants:
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
            if unit.alive and not unit.is_summon and not unit.banished and unit.position is not None
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
        visited = {unit.position}
        direction: Optional[tuple[int, int]] = None
        distance_cost = 0
        for step in steps:
            if not self.in_bounds(step):
                raise ActionError("移动路径超出战场边界。")
            previous = path[-1]
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
            if step in visited:
                raise ActionError("移动路径不能重复经过同一个格子。")
            if not self.can_place_unit(unit, step, ignore=unit, mover=unit, ignore_units=ignore_units):
                raise ActionError("移动路径被阻挡。")
            visited.add(step)
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
                if self.can_place_unit(unit, Position(x, y), ignore=unit, mover=unit, ignore_units=ignore_units)
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
                    if not self.can_place_unit(unit, candidate, ignore=unit, mover=unit, ignore_units=ignore_units):
                        break
                    spent += self.normal_movement_step_cost(unit, previous, candidate) if use_movement_cost else 1
                    previous = candidate
                    if spent > max_distance:
                        break
                    distance_value = spent if use_movement_cost else step
                    if exact_distance is None or distance_value == exact_distance:
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
                    if not self.can_place_unit(unit, nxt, ignore=unit, mover=unit, ignore_units=ignore_units):
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
                if pos != unit.position and (exact_distance is None or dist == exact_distance)
            ]
        visited = {unit.position}
        queue: deque[tuple[Position, int]] = deque([(unit.position, 0)])
        result: list[Position] = []
        while queue:
            pos, dist = queue.popleft()
            if dist >= max_distance:
                continue
            for nxt in self.neighbors(pos):
                if nxt in visited:
                    continue
                if not self.can_place_unit(unit, nxt, ignore=unit, mover=unit, ignore_units=ignore_units):
                    continue
                visited.add(nxt)
                if exact_distance is None or dist + 1 == exact_distance:
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
                if not self.can_place_unit(unit, current, ignore=unit, mover=unit, ignore_units=ignore_units):
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
                    if not self.can_place_unit(unit, nxt, ignore=unit, mover=unit, ignore_units=ignore_units):
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
                next_dist = distances[pos] + 1
                if next_dist > max_distance or nxt in parents:
                    continue
                if not self.can_place_unit(unit, nxt, ignore=unit, mover=unit, ignore_units=ignore_units):
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
        if destination == unit.position:
            raise ActionError("目标位置不能与当前位置相同。")
        if not self.in_bounds(destination):
            raise ActionError("目标位置超出战场边界。")
        if not self.can_place_unit(unit, destination, ignore=unit, mover=unit, ignore_units=ignore_units):
            raise ActionError("目标位置已被占用。")
        if unit.cannot_move and not forced:
            raise ActionError(f"{unit.name} 当前无法移动。")
        if unit.cannot_normal_move and not via_skill and not forced:
            raise ActionError(f"{unit.name} 当前不能进行常规移动。")
        if max_distance is None:
            max_distance = unit.normal_move_distance() if not via_skill and not forced else int(unit.stat("speed"))
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
        unit.position = destination
        if not triggered_by_reaction:
            unit.moved_this_turn = True
            if not via_skill:
                unit.move_used = True
        self.log(f"{unit.name} 移动了。")
        for effect in list(self.field_effects):
            effect.on_unit_moved(self, ctx)
        for other in self.all_units():
            for component in list(other.iter_components()):
                component.on_unit_moved(self, ctx)
        self.cleanup_dead_units()
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
        from_field_effect: bool = False,
        cannot_evade: bool = False,
        resolve_defenses: bool = True,
        tags: Optional[set[str]] = None,
    ) -> TargetContext:
        ctx = TargetContext(
            actor=actor,
            target=target,
            action_name=action_name,
            is_skill=is_skill,
            is_hostile=is_hostile,
            ignore_shield=ignore_shield,
            half_ignore_shield=half_ignore_shield,
            ignore_magic_immunity=ignore_magic_immunity,
            from_field_effect=from_field_effect,
            cannot_evade=cannot_evade,
            tags=tags or set(),
        )
        if target.banished:
            ctx.cancelled = True
            ctx.reason = "目标暂时不在战场上。"
            return ctx
        if target.cannot_be_targeted and is_hostile:
            ctx.cancelled = True
            ctx.reason = f"{target.name} 当前无法被选中。"
            return ctx
        for component in list(actor.iter_components()):
            component.on_targeted(self, ctx)
        for effect in list(self.field_effects):
            effect.on_targeted(self, ctx)
        for component in list(target.iter_components()):
            component.on_targeted(self, ctx)
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
            ctx.reason = f"{target.name} 处于魔免状态。"
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
            return ctx
        if is_hostile and target.dodge_charges > 0 and not ctx.cannot_evade:
            target.dodge_charges -= 1
            ctx.cancelled = True
            ctx.reason = f"{target.name} 闪避了【{action_name}】。"
            return ctx
        return ctx

    def resolve_damage(self, ctx: DamageContext) -> DamageContext:
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

            for effect in list(self.field_effects):
                effect.on_before_damage(self, ctx)
            if ctx.source is not None:
                for component in list(ctx.source.iter_components()):
                    component.on_before_damage(self, ctx)
            for component in list(ctx.target.iter_components()):
                component.on_before_damage(self, ctx)
            if ctx.cancelled:
                if ctx.reason:
                    self.log_public_event(ctx.reason, source=ctx.source, target=ctx.target)
                notify_cancelled()
                return ctx
            if ctx.raw_damage is None and ctx.area_cell_hits > 1:
                ctx.attack_power += max(0, int(ctx.area_cell_hits) - 1)
            if (
                ctx.is_skill
                and not ctx.from_field_effect
                and ctx.target.magic_immunity
                and not ctx.ignore_magic_immunity
            ):
                ctx.cancelled = True
                ctx.reason = f"{ctx.target.name} 处于魔免状态。"
                self.log_public_event(ctx.reason, source=ctx.source, target=ctx.target)
                notify_cancelled()
                return ctx
            if ctx.target.total_shields() > 0:
                if ctx.ignore_shield:
                    ctx.target.consume_one_shield()
                    ctx.shield_consumed = True
                    self.log_public_event(
                        f"{ctx.target.name} 的 1 层护盾被【{ctx.action_name}】贯穿并打碎。",
                        source=ctx.source,
                        target=ctx.target,
                    )
                elif ctx.half_ignore_shield:
                    ctx.target.consume_one_shield()
                    ctx.shield_consumed = True
                    if ctx.raw_damage is None:
                        ctx.attack_power = max(0.0, ctx.attack_power - 1)
                    self.log_public_event(
                        f"{ctx.target.name} 的 1 层护盾被【{ctx.action_name}】半破魔打碎。",
                        source=ctx.source,
                        target=ctx.target,
                    )
                else:
                    ctx.target.consume_one_shield()
                    ctx.shield_consumed = True
                    ctx.cancelled = True
                    ctx.reason = f"{ctx.target.name} 的护盾挡下了伤害。"
                    self.log_public_event(ctx.reason, source=ctx.source, target=ctx.target)
                    notify_cancelled()
                    return ctx
            if ctx.target.dodge_charges > 0 and not ctx.cannot_evade:
                ctx.target.dodge_charges -= 1
                ctx.cancelled = True
                ctx.reason = f"{ctx.target.name} 闪避了伤害。"
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
                self.cleanup_dead_units()
                return ctx
            damage_amount = self.damage_rule.calculate_damage(ctx.attack_power, ctx.target.stat("defense"))
            if ctx.raw_damage is not None:
                damage_amount = ctx.raw_damage
            ctx.raw_damage = round(float(damage_amount), 4)
            ctx.target.take_damage_fraction(ctx.raw_damage)
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
            self.cleanup_dead_units()
            return ctx

    def expire_chain_temporary_statuses(self) -> None:
        for unit in self.all_units():
            unit.temporary_shields = 0
            for status in list(unit.statuses):
                if isinstance(status, TemporaryDefenseStatus) and status.expire_with_chain:
                    unit.remove_status(status, self)

    def heal(self, ctx: HealContext) -> HealContext:
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
            old_hp = ctx.target.current_hp
            ctx.target.heal_fraction(ctx.amount)
            gained = round(ctx.target.current_hp - old_hp, 4)
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
        if not actor.can_take_turn_actions(self):
            raise ActionError("这个单位当前不能行动。")
        if actor.attacks_used >= actor.attack_actions_per_turn():
            raise ActionError("本回合攻击次数已用完。")
        ignore_stealth = self.attack_ignores_stealth(actor, target)
        ok, reason = self.attack_target_allowed(actor, target, ignore_stealth=ignore_stealth)
        if not ok:
            raise ActionError(reason)
        actor.attacks_used += 1
        actor.actions_taken_this_turn.append("attack")
        self.resolve_attack_damage(actor, target, action_name="普攻")
        self.check_win_condition()

    def attack_ignores_shield(self, actor: Unit, target: Unit) -> bool:
        ignore_shield, _ = self.attack_shield_flags(actor, target)
        return ignore_shield

    def attack_shield_flags(self, actor: Unit, target: Unit) -> tuple[bool, bool]:
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
        return ctx.ignore_shield, ctx.half_ignore_shield

    def attack_ignores_stealth(self, actor: Unit, target: Unit) -> bool:
        return False

    def attack_target_allowed(
        self,
        actor: Unit,
        target: Unit,
        *,
        ignore_stealth: bool = False,
    ) -> tuple[bool, str]:
        block_reason = self.shared_stealth_action_block_reason(actor)
        if block_reason:
            return False, block_reason
        if actor.cannot_attack:
            return False, f"{actor.name} 当前不能攻击。"
        if actor.position is None or target.position is None:
            return False, "攻击对象不在战场上。"
        if self.distance_between_units(actor, target) > actor.targeting_range():
            return False, "目标超出普攻范围。"
        ok, reason = self.unit_can_be_selected(target, actor=actor, ignore_stealth=ignore_stealth)
        if not ok:
            return False, reason
        for component in list(actor.iter_components()):
            ok, reason = component.can_attack_target(self, actor, target)
            if not ok:
                return False, reason
        return True, ""

    def resolve_attack_damage(
        self,
        actor: Unit,
        target: Unit,
        *,
        action_name: str,
        tags: Optional[set[str]] = None,
    ) -> Optional[DamageContext]:
        with self.suppress_logs_for_stealth(target):
            attack_tags = {"attack"}
            if tags:
                attack_tags.update(tags)
            target_ctx = self.validate_target(
                actor,
                target,
                action_name=action_name,
                is_skill=False,
                is_hostile=True,
                resolve_defenses=False,
                tags=attack_tags,
            )
            if target_ctx.cancelled:
                self.log_public_event(target_ctx.reason, source=actor, target=target)
                actor.consume_attack_attempt_buffs(self)
                return None
            damage_ctx = DamageContext(
                source=actor,
                target=target,
                attack_power=actor.stat("attack"),
                is_skill=False,
                action_name=action_name,
                ignore_shield=target_ctx.ignore_shield,
                half_ignore_shield=target_ctx.half_ignore_shield,
                ignore_magic_immunity=target_ctx.ignore_magic_immunity,
                cannot_evade=target_ctx.cannot_evade,
                tags=set(target_ctx.tags),
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
    ) -> dict[str, Any]:
        sanitized = dict(preview)
        target_ids: list[str] = []
        target_cells: list[dict[str, int]] = []
        for unit_id in preview.get("target_unit_ids", []):
            unit = self.units.get(unit_id)
            if unit is None or unit.position is None:
                continue
            ok, _ = self.unit_can_be_selected(unit, actor=actor, ignore_stealth=ignore_stealth)
            if not ok:
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
                    return preferred
            return self.selectable_unit_at(declared, actor=actor, ignore_stealth=ignore_stealth)
        for unit_id in preferred_ids:
            unit = self.get_unit(unit_id)
            ok, _ = self.unit_can_be_selected(unit, actor=actor, ignore_stealth=ignore_stealth)
            if ok:
                return unit
        return None

    def resolve_from_declared_origin(
        self,
        actor: Unit,
        payload: dict[str, Any],
        resolver: Any,
    ) -> Any:
        declared = self.declared_source_position(payload)
        if declared is None or actor.position is None or actor.position == declared:
            return resolver()
        actual_position = actor.position
        actor.position = declared
        try:
            result = resolver()
        except Exception:
            actor.position = actual_position
            raise
        if actor.position == declared:
            actor.position = actual_position
        return result

    def use_skill(self, actor: Unit, skill_code: str, payload: dict[str, Any]) -> None:
        skill = actor.get_skill(skill_code)
        prepaid = bool(payload.get("resources_prepaid"))
        if not prepaid and not actor.can_take_turn_actions(self):
            raise ActionError("这个单位当前不能行动。")
        if not prepaid:
            ok, reason = skill.can_use(self, actor, payload)
            if not ok:
                raise ActionError(reason)
            skill.prepay_resources(self, actor, payload)
        target_id = payload.get("resolved_target_unit_id") or payload.get("target_unit_id")
        if target_id:
            target = self.get_unit(target_id)
            self.require_selectable_unit(
                target,
                actor=actor,
                action_name=skill.name,
                ignore_stealth=skill.ignores_stealth_for_payload(self, actor, payload),
                queued_resolution=bool(payload.get("queued_resolution")),
            )
        try:
            skill.execute(self, actor, payload)
        except ActionMiss as exc:
            self.log(str(exc) or f"【{skill.name}】落在原定格上，没有命中有效目标。")
        except ActionError as exc:
            if payload.get("queued_resolution") and payload.get("declared_target_x") is not None:
                self.log(f"【{skill.name}】落在原定格上，但没有命中有效目标。")
            else:
                raise exc
        skill.finalize_use(self, actor)
        actor.actions_taken_this_turn.append(f"skill:{skill.code}")
        self.check_win_condition()

    def build_queued_action(self, payload: dict[str, Any]) -> QueuedAction:
        action_type = payload.get("type")
        if action_type in {"end_turn", "pass_unit", "chain_react", "chain_skip"}:
            raise ActionError("该动作不能进入连锁栈。")
        queued_payload = dict(payload)
        queued_payload["queued_resolution"] = True
        actor = self.get_unit(payload["unit_id"])
        if action_type == "move":
            if not actor.can_take_turn_actions(self):
                raise ActionError("这个单位当前不能行动。")
            if actor.move_used:
                raise ActionError("本回合已经移动过了。")
            if actor.cannot_move:
                raise ActionError(f"{actor.name} 当前无法移动。")
            if actor.cannot_normal_move:
                raise ActionError(f"{actor.name} 当前不能进行常规移动。")
            explicit_steps = self.payload_positions(payload, "path")
            if explicit_steps:
                path = self.explicit_path(
                    actor,
                    explicit_steps,
                    max_distance=actor.normal_move_distance(),
                    use_movement_cost=True,
                )
                queued_payload["path"] = [step.to_dict() for step in path[1:]]
                queued_payload["x"] = path[-1].x
                queued_payload["y"] = path[-1].y
            elif payload.get("x") is None or payload.get("y") is None:
                raise ActionError("移动需要指定目标位置。")
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
            target = self.get_unit(payload["target_unit_id"])
            if not actor.can_take_turn_actions(self):
                raise ActionError("\u8fd9\u4e2a\u5355\u4f4d\u5f53\u524d\u4e0d\u80fd\u884c\u52a8\u3002")
            if actor.attacks_used >= actor.attack_actions_per_turn():
                raise ActionError("\u672c\u56de\u5408\u653b\u51fb\u6b21\u6570\u5df2\u7528\u5b8c\u3002")
            ignore_stealth = self.attack_ignores_stealth(actor, target)
            ok, reason = self.attack_target_allowed(actor, target, ignore_stealth=ignore_stealth)
            if not ok:
                raise ActionError(reason)
            queued_payload["declared_source_x"] = actor.position.x
            queued_payload["declared_source_y"] = actor.position.y
            queued_payload["declared_target_x"] = target.position.x
            queued_payload["declared_target_y"] = target.position.y
            ignore_shield, half_ignore_shield = self.attack_shield_flags(actor, target)
            queued_payload["ignore_shield"] = ignore_shield
            queued_payload["half_ignore_shield"] = half_ignore_shield
            queued_payload["ignore_stealth"] = ignore_stealth
            return QueuedAction(
                action_type="attack",
                actor_id=actor.unit_id,
                display_name="普攻",
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
            ok, reason = skill.can_use(self, actor, payload)
            if not ok:
                raise ActionError(reason)
            if actor.position is not None:
                queued_payload["declared_source_x"] = actor.position.x
                queued_payload["declared_source_y"] = actor.position.y
            queued_payload["ignore_shield"] = skill.ignores_shield_for_payload(self, actor, payload)
            queued_payload["half_ignore_shield"] = skill.half_ignores_shield_for_payload(self, actor, payload)
            queued_payload["ignore_stealth"] = skill.ignores_stealth_for_payload(self, actor, payload)
            if payload.get("target_unit_id"):
                target = self.get_unit(payload["target_unit_id"])
                self.require_selectable_unit(
                    target,
                    actor=actor,
                    action_name=skill.name,
                    ignore_stealth=queued_payload["ignore_stealth"],
                )
            target_units = [
                unit
                for unit in skill.get_target_units_for_payload(self, actor, payload)
                if unit.alive
            ]
            target_cells = list(skill.get_target_cells_for_payload(self, actor, payload))
            targets: list[str] = []
            for unit in [*target_units, *self.units_at_cells(target_cells)]:
                if unit.unit_id not in targets:
                    targets.append(unit.unit_id)
            declared_targets = self.payload_target_unit_ids(payload)
            if declared_targets:
                target = self.get_unit(declared_targets[0])
                if target.position is not None:
                    queued_payload["declared_target_x"] = target.position.x
                    queued_payload["declared_target_y"] = target.position.y
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

    def queued_action_effect_summary(self, queued_action: QueuedAction) -> str:
        actor = self.units.get(queued_action.actor_id)
        payload = queued_action.payload
        parts: list[str] = []

        if queued_action.action_type == "move":
            parts.append(self.format_action_effect_summary(queued_action.display_name, "仅改变站位，不会直接造成伤害或附加效果。"))
        elif queued_action.action_type == "attack":
            attack_power = actor.stat("attack") if actor is not None else 0
            parts.append(
                self.format_action_effect_summary(
                    queued_action.display_name,
                    f"对原声明格进行一次普攻，按攻 {self.format_summary_number(attack_power)} 结算伤害。",
                )
            )
        elif queued_action.action_type == "skill":
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
            if effect_code == "banish":
                turns = float(payload.get("banish_turns", 0))
                rounds = self.format_summary_number(turns / 2) if turns else "0"
                parts.append(
                    self.format_action_effect_summary(
                        queued_action.display_name,
                        f"若原声明格上的目标仍有效，则使其消失 {rounds}轮。",
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

        ordered: list[str] = []
        for part in parts:
            cleaned = str(part).strip()
            if cleaned and cleaned not in ordered:
                ordered.append(cleaned)
        return " ".join(ordered)

    def available_reaction_options(self, unit: Unit, queued_action: QueuedAction) -> list[ReactionOption]:
        if unit.total_shields() > 0:
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
        if unit.has_block_counter and queued_action.speed < 2 and unit.unit_id in queued_action.target_unit_ids:
            options.append(
                ReactionOption(
                    unit_id=unit.unit_id,
                    action_code="block",
                    action_name="格挡",
                    action_type="reaction_action",
                    timing="reaction",
                    chain_speed=2,
                    description="下一次伤害结算时守 +1，只持续到这次连锁结算结束。",
                    preview={"cells": [cell.to_dict() for cell in self.unit_cells(unit)], "target_unit_ids": [], "requires_target": False},
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
        return (
            queued_action.action_type in {"attack", "skill", "skill_effect"}
            and queued_action.speed == 1
            and unit.total_shields() > 0
            and not bool(queued_action.payload.get("ignore_shield"))
            and not bool(queued_action.payload.get("half_ignore_shield"))
        )

    def action_ignores_stealth(self, queued_action: QueuedAction) -> bool:
        return bool(queued_action.payload.get("ignore_stealth"))

    def target_can_chain_against(self, unit: Unit, queued_action: QueuedAction) -> bool:
        return unit.alive and unit.position is not None and not unit.banished

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
        return ReactionWindow(
            reactive_player_id=reactive_player_id,
            queued_action=queued_action,
            pending_reactor_ids=candidate_ids,
            options_by_unit=options_by_unit,
        )

    def present_reaction_window_or_resolve(self, queued_action: QueuedAction) -> None:
        with self.suppress_logs() if self.queued_action_hides_logs(queued_action) else nullcontext():
            window = self.create_reaction_window(queued_action)
            if window is None:
                if queued_action.speed < 3 and queued_action.hostile and queued_action.target_unit_ids:
                    target_names = "、".join(self.get_unit(unit_id).name for unit_id in queued_action.target_unit_ids)
                    self.log(f"{target_names} 没有可用的更快连锁，【{queued_action.display_name}】直接结算。")
                self.resolve_queued_action(queued_action)
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
            self.resolve_attack_damage(actor, source, action_name="\u53cd\u51fb", tags={"counter"})
            self.log(f"{actor.name} \u53d1\u52a8\u4e86\u53cd\u51fb\u3002")
            return
        raise ActionError("未知连锁动作。")

    def resolve_skill_effect(self, actor: Unit, queued_action: QueuedAction) -> None:
        payload = queued_action.payload
        effect_code = payload.get("effect_code")
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
        if effect_code == "banish":
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

    def resolve_queued_action(self, queued_action: QueuedAction) -> None:
        with self.suppress_logs() if self.queued_action_hides_logs(queued_action) else nullcontext():
            actor = self.units.get(queued_action.actor_id)
            if actor is None or not actor.alive or actor.banished:
                self.log(f"【{queued_action.display_name}】未能结算，因为行动者已不在战场。")
                return
            payload = queued_action.payload
            if queued_action.action_type == "move":
                destination = Position(int(payload["x"]), int(payload["y"]))
                path = self.payload_positions(payload, "path")
                self.move_unit(actor, destination, path=path or None)
                actor.actions_taken_this_turn.append("move")
                return
            if queued_action.action_type == "attack":
                target: Optional[Unit]
                declared = self.declared_target_position(payload)
                if declared is not None:
                    target = self.resolve_declared_target_unit(
                        actor,
                        payload,
                        ignore_stealth=bool(payload.get("ignore_stealth")),
                    )
                    if target is None or target.player_id == actor.player_id:
                        actor.attacks_used += 1
                        actor.actions_taken_this_turn.append("attack")
                        actor.consume_attack_attempt_buffs(self)
                        self.log(f"{actor.name} 的【普攻】打在 ({declared.x}, {declared.y})，没有命中有效目标。")
                        return
                else:
                    target = self.get_unit(payload["target_unit_id"])
                self.resolve_from_declared_origin(actor, payload, lambda: self.basic_attack(actor, target))
                return
            if queued_action.action_type == "skill":
                resolved_payload = dict(payload)
                resolved_target = self.resolve_declared_target_unit(
                    actor,
                    payload,
                    ignore_stealth=bool(payload.get("ignore_stealth")),
                )
                resolved_payload["resolved_target_unit_id"] = resolved_target.unit_id if resolved_target is not None else None
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
                return
            if queued_action.action_type == "reaction_action":
                self.resolve_reaction_action(actor, payload["action_code"], self.source_action_for_reaction(queued_action))
                return

    def finalize_reaction_window(self) -> None:
        if self.pending_chain is None:
            return
        queued = self.pending_chain.queued_action
        reactions = list(reversed(self.pending_chain.chosen_reactions))
        self.pending_chain = None
        try:
            for reaction in reactions:
                self.resolve_queued_action(reaction)
                if self.winner is not None:
                    return
            self.resolve_queued_action(queued)
        finally:
            self.expire_chain_temporary_statuses()

    def advance_reaction_window(self) -> None:
        if self.pending_chain is None:
            return
        while self.pending_chain.pending_reactor_ids and not self.pending_chain.options_by_unit.get(self.pending_chain.pending_reactor_ids[0]):
            self.pending_chain.pending_reactor_ids.pop(0)
        if not self.pending_chain.pending_reactor_ids:
            self.finalize_reaction_window()

    def start_action_or_chain(self, payload: dict[str, Any]) -> None:
        queued_action = self.build_queued_action(payload)
        actor = self.get_unit(queued_action.actor_id)
        with self.suppress_logs() if self.queued_action_hides_logs(queued_action) else nullcontext():
            reaction_window_timing = "before"
            if queued_action.action_type == "skill":
                skill = actor.get_skill(queued_action.payload["skill_code"])
                skill.prepay_resources(self, actor, queued_action.payload)
                queued_action.payload["resources_prepaid"] = True
                reaction_window_timing = skill.reaction_window_timing(self, actor, queued_action.payload)
            if queued_action.action_type in {"attack", "skill"}:
                actor.notify_action_declared(self, queued_action.action_type, queued_action.payload)
            if queued_action.action_type == "skill" and reaction_window_timing == "after":
                self.resolve_queued_action(queued_action)
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
            if unit.position is not None:
                self.log(f"{unit.name} 被击破。")
                unit.position = None
            self.remove_unit(unit)
        self.clear_all_stealth_if_all_heroes_stealthed()
        self.check_win_condition()

    def banish_unit(self, unit: Unit, turns: int) -> None:
        unit.banished = True
        unit.banish_turns_remaining = turns
        unit.banish_return_position = unit.position
        self.log(f"{unit.name} 消失了，暂时无法行动。")
        self.clear_all_stealth_if_all_heroes_stealthed()

    def summon_unit(self, unit: Unit, position: Position, *, summoner: Optional[Unit] = None) -> None:
        unit.summoner_id = summoner.unit_id if summoner is not None else None
        unit.can_act_on_entry_turn = True
        unit.turn_ready = False
        self.add_unit(unit, position)
        self.log(f"{unit.name} 被召唤到战场。")

    def check_win_condition(self) -> None:
        if self.winner is not None:
            return
        alive_players = {player_id for player_id in (1, 2) if self.hero_units(player_id)}
        if len(alive_players) == 1 and self.units:
            self.winner = alive_players.pop()
            self.log(f"玩家 {self.winner} 获胜。")

    def perform_action(self, payload: dict[str, Any]) -> None:
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
                    skill.prepay_resources(self, reactor, reaction_payload)
                    reaction_payload["resources_prepaid"] = True
                    target_ids = self.payload_target_unit_ids(reaction_payload)
                    if target_ids:
                        target = self.get_unit(target_ids[0])
                        if target.position is not None:
                            reaction_payload["declared_target_x"] = target.position.x
                            reaction_payload["declared_target_y"] = target.position.y
                    reactor.notify_action_declared(
                        self,
                        "skill",
                        {
                            "skill_code": chosen.action_code,
                            **reaction_payload,
                            "queued_resolution": True,
                        },
                    )
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
                    target_unit_ids=[self.pending_chain.queued_action.actor_id],
                    target_cells=[],
                    source_player_id=self.get_unit(current_unit_id).player_id,
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
            self.start_action_or_chain(payload)
            return
        unit = self.get_unit(payload["unit_id"])
        if action_type == "pass_unit":
            self.pass_turn(unit)
            return
        raise ActionError("未知动作类型。")

    def action_snapshot_for(self, unit: Unit) -> dict[str, Any]:
        move_targets = [
            pos.to_dict()
            for pos in self.reachable_positions(
                unit,
                max_distance=unit.normal_move_distance(),
                use_movement_cost=True,
            )
        ]
        attack_targets = []
        attack_cells = []
        for enemy in self.enemy_units(unit.player_id):
            ignore_stealth = self.attack_ignores_stealth(unit, enemy)
            if self.attack_target_allowed(unit, enemy, ignore_stealth=ignore_stealth)[0]:
                attack_targets.append(enemy.unit_id)
                attack_cells.extend(cell.to_dict() for cell in self.unit_cells(enemy))
        actions = []
        actions.append(
            {
                "code": "move",
                "name": "移动",
                "kind": "move",
                "timing": "active",
                "chain_speed": 1,
                "description": "普通移动。",
                "available": unit.can_take_turn_actions(self) and not unit.move_used and not unit.cannot_move and not unit.cannot_normal_move,
                "preview": {
                    "cells": move_targets,
                    "target_unit_ids": [],
                    "requires_target": True,
                    "selection": {
                        "mode": "move_path",
                        "max_steps": unit.normal_move_distance(),
                    },
                },
            }
        )
        attack_available = (
            unit.can_take_turn_actions(self)
            and unit.attacks_used < unit.attack_actions_per_turn()
            and not unit.cannot_attack
            and not self.shared_stealth_action_block_reason(unit)
        )
        actions.append(
            {
                "code": "attack",
                "name": "普攻",
                "kind": "attack",
                "timing": "active",
                "chain_speed": 1,
                "description": "普通攻击。",
                "available": attack_available,
                "preview": {"cells": attack_cells, "target_unit_ids": attack_targets, "requires_target": True},
            }
        )
        for skill in unit.skills:
            data = skill.to_public_dict(self)
            data["available"] = (
                unit.can_take_turn_actions(self) and skill.can_use(self, unit, {})[0]
                if skill.timing == "active"
                else False
            )
            data["kind"] = "skill"
            data["preview"] = self.filter_preview_targets(
                unit,
                skill.preview(self, unit),
                ignore_stealth=skill.ignores_stealth_for_payload(self, unit, {}),
                replace_cells=data["target_mode"] in {"ally", "enemy", "unit"},
            )
            actions.append(data)
        return {
            "move_targets": move_targets,
            "attack_targets": attack_targets,
            "skills": [skill.to_public_dict(self) for skill in unit.skills],
            "actions": actions,
            "can_move": unit.can_take_turn_actions(self) and not unit.move_used and not unit.cannot_move and not unit.cannot_normal_move,
            "attacks_left": max(unit.attack_actions_per_turn() - unit.attacks_used, 0),
        }

    def reaction_snapshot_for(self, unit: Unit) -> dict[str, Any]:
        if self.pending_chain is None:
            return {"actions": []}
        options = self.pending_chain.options_by_unit.get(unit.unit_id, [])
        return {"actions": [option.to_public_dict() for option in options]}

    def to_public_dict(self) -> dict[str, Any]:
        respawn_prompt = self.current_respawn_prompt()
        return {
            "board": {"width": self.width, "height": self.height},
            "active_player": self.active_player,
            "input_player": (
                respawn_prompt.player_id
                if respawn_prompt is not None
                else (self.pending_chain.reactive_player_id if self.pending_chain else self.active_player)
            ),
            "turn_number": self.turn_number,
            "round_number": self.round_number,
            "winner": self.winner,
            "damage_rule": self.damage_rule.name,
            "units": [unit.to_public_dict(self) for unit in self.all_units()],
            "field_effects": [effect.to_public_dict(self) for effect in self.field_effects],
            "pending_chain": self.pending_chain.to_public_dict(self) if self.pending_chain else None,
            "pending_respawn": respawn_prompt.to_public_dict() if respawn_prompt else None,
            "logs": self.logs,
        }
