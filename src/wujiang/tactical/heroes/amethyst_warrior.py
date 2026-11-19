"""R354 紫晶战士: movement, spell growth, and water-only follow-up."""

from __future__ import annotations

from typing import Any

from wujiang.tactical.engine.core import ActionError, ActionMiss, Battle, DamageContext, HeroUnit, Position, Skill, Trait, Unit
from wujiang.tactical.heroes.ai_candidate_plan import SkillCandidatePlan
from wujiang.tactical.heroes.common import is_mana_drain_immune, payload_direction, payload_position, positions_to_dict
from wujiang.tactical.heroes.mechanics.next_five_effects import damage_followup_effect_applies
from wujiang.tactical.heroes.mechanics.next_five_geometry import ALL_DIRECTIONS
from wujiang.tactical.heroes.mechanics.piercing_control import piercing_nondamage_recipient


def _is_water_hero(unit: Unit) -> bool:
    return (isinstance(unit, HeroUnit) and not unit.is_summon and not unit.is_clone
            and getattr(unit, "attribute", None) == "水")


class AmethystMomentumTrait(Trait):
    """One owner for temporary spell growth and focus movement."""

    def __init__(self) -> None:
        super().__init__("紫晶战意", "其他单位每次正式使用技能，攻守各+1至本人下轮开始。")
        self.spell_growth = 0
        self.defense_spent = 0
        self.extra_moves = 0

    def modify_stat(self, stat_name: str, value: float) -> float:
        if stat_name == "attack":
            return value + self.spell_growth
        if stat_name == "defense":
            return value + self.spell_growth - self.defense_spent
        return value

    def modify_normal_move_actions_per_turn(self, value: int) -> int:
        return value + self.extra_moves

    def on_owner_turn_start(self, battle: Battle) -> None:
        self.spell_growth = 0
        self.defense_spent = 0
        self.extra_moves = 0

    def on_any_skill_declared(self, battle: Battle, actor: Unit, skill: Skill) -> None:
        owner = self.owner
        if owner is None or owner is actor or not owner.alive or owner.position is None:
            return
        self.spell_growth += 1
        battle.log_public_event(f"{actor.name} 使用【{skill.name}】，{owner.name} 的紫晶战意令攻守各+1。",
                                source=actor, target=owner)

    def to_public_dict(self, battle: Battle) -> dict[str, Any]:
        data = super().to_public_dict(battle)
        data.update(spell_growth=self.spell_growth, defense_spent=self.defense_spent,
                    extra_moves=self.extra_moves)
        return data


class AmethystChainManaTrait(Trait):
    def __init__(self) -> None:
        super().__init__("紫晶回魔", "本体每次行动被另一单位连锁，魔+2。")

    def on_action_chained(self, battle: Battle, declared_action: Any, reaction: Any) -> None:
        owner = self.owner
        if owner is None or declared_action.actor_id != owner.unit_id or reaction.actor_id == owner.unit_id:
            return
        gained = owner.gain_mana(2)
        if gained:
            battle.log_public_event(f"{owner.name} 的行动被连锁，魔+{gained:g}。", source=owner)


def _momentum(actor: Unit) -> AmethystMomentumTrait:
    trait = next((item for item in actor.traits if isinstance(item, AmethystMomentumTrait)), None)
    if trait is None:
        raise ActionError("紫晶战意未装配。")
    return trait


class AmethystFocusSkill(Skill):
    ai_payload_value_owned = True
    ai_enemy_impact_requirement = False

    def __init__(self) -> None:
        super().__init__("amethyst_focus", "集中", "每本人轮最多2次；当前守大于2时守-1，本轮普通移动次数+1。",
                         max_uses_per_turn=2, target_mode="self")

    def can_use(self, battle: Battle, actor: HeroUnit,
                payload: dict[str, Any] | None = None) -> tuple[bool, str]:
        ok, reason = super().can_use(battle, actor, payload)
        if not ok:
            return ok, reason
        return (True, "") if actor.stat("defense") > 2 else (False, "当前守须大于2。")

    def execute(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> None:
        if actor.stat("defense") <= 2:
            raise ActionMiss("集中结算时守已降至2以下。")
        trait = _momentum(actor)
        trait.defense_spent += 1
        trait.extra_moves += 1
        battle.log_public_event(f"{actor.name} 集中：守-1，本轮普通移动次数+1。", source=actor)

    def ai_payload_candidates(self, battle: Battle, actor: Unit,
                              action: dict[str, Any]) -> list[dict[str, Any]]:
        return [{"type": "skill", "unit_id": actor.unit_id, "skill_code": self.code}]

    def ai_candidate_payloads(self, context: Any) -> list[dict[str, Any]]:
        return self.ai_payload_candidates(context.battle, context.actor, context.action)

    def ai_payload_value(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> float:
        if actor.stat("defense") <= 2 or actor.normal_move_actions_used < actor.normal_move_actions_per_turn():
            return -1000.0
        landings = battle.reachable_positions(actor, max_distance=actor.normal_move_distance())
        enemies = [unit for unit in battle.enemy_units(actor.player_id) if unit.position is not None]
        if not landings or not enemies or actor.position is None:
            return -1000.0
        before = min(battle.distance_between_units(actor, enemy) for enemy in enemies)
        after = min(min(cell.distance_to(enemy.position) for enemy in enemies) for cell in landings)
        return 22.0 if after < before else -1000.0

    def ai_priority_score(self, context: Any) -> float:
        return self.ai_payload_value(context.battle, context.actor, context.payload)


_AREA_START = {
    (-1, -1): (-2, -2), (0, -1): (-1, -2), (1, -1): (1, -2),
    (-1, 0): (-2, -1), (1, 0): (1, 0),
    (-1, 1): (-2, 1), (0, 1): (0, 1), (1, 1): (1, 1),
}


def _tidal_cells(battle: Battle, landing: Position, direction: tuple[int, int]) -> list[Position]:
    start_x, start_y = _AREA_START[direction]
    return [cell for x in range(2) for y in range(2)
            if battle.in_bounds(cell := landing.offset(start_x + x, start_y + y))]


def _resolve_water_drain(battle: Battle, actor: Unit, queued: Any) -> None:
    target = battle.units.get(queued.payload.get("water_target_id", ""))
    if target is None or not target.alive or target.position is None or not _is_water_hero(target):
        return
    recipient = piercing_nondamage_recipient(battle, actor, target,
                                             action_name="潮汐拳·吸魔（大）", code="amethyst_tidal_drain")
    if recipient is None or not _is_water_hero(recipient):
        return
    if is_mana_drain_immune(recipient):
        battle.log_public_event(f"{recipient.name} 无法被吸魔。", source=actor, target=recipient)
        return
    lost = recipient.drain_mana(1)
    gained = actor.gain_mana(lost)
    if lost:
        battle.record_skill_effect(recipient)
    battle.log_public_event(f"{actor.name} 的潮汐拳从{recipient.name}吸取{lost:g}魔，自身回复{gained:g}魔。",
                            source=actor, target=recipient)


def _resolve_tidal_damage(battle: Battle, actor: Unit, queued: Any) -> None:
    cells = battle.payload_positions(queued.payload, "cells")
    for target in battle.effect_units_at_cells(cells, ignore=actor):
        ctx = battle.resolve_damage(DamageContext(
            source=actor, target=target, attack_power=float(queued.payload["attack_power"]),
            is_skill=True, action_name="潮汐拳",
            area_cell_hits=battle.unit_hit_count_for_cells(target, cells),
            tags={"skill", "amethyst_tidal_fist"},
        ))
        if not damage_followup_effect_applies(ctx) or target.player_id == actor.player_id or not _is_water_hero(target):
            continue
        battle.queue_skill_effect_action(
            actor=actor, display_name="潮汐拳·吸魔（大）", effect_code="amethyst_tidal_drain",
            payload={"water_target_id": target.unit_id}, target_units=[target],
            include_cell_units=False, effect_resolver=_resolve_water_drain,
        )


class AmethystTidalFistSkill(Skill):
    # The default first-64 cut discards profitable landing+direction pairs.
    # Rank cheaply here, then run full scoring only for the best few.
    ai_candidate_plan = SkillCandidatePlan(prefilter_limit=16)
    ai_payload_value_owned = True
    ai_enemy_impact_requirement = False
    target_cells_are_geometry_only = True
    is_displacement_skill = True

    def __init__(self) -> None:
        super().__init__("amethyst_tidal_fist", "潮汐拳",
                         "每本人轮一次；先移动恰2格，再朝一方向打贴身2×2普通伤；命中的敌方水武将另受破魔吸魔（大）。",
                         max_uses_per_turn=1, target_mode="cell")

    def landings(self, battle: Battle, actor: Unit) -> list[Position]:
        return [] if actor.cannot_move or actor.position is None else battle.reachable_positions(
            actor, max_distance=2, exact_distance=2)

    def _aim(self, battle: Battle, actor: Unit,
             payload: dict[str, Any]) -> tuple[Position, list[Position]]:
        landing = payload_position(payload)
        direction = payload_direction(payload)
        if landing not in self.landings(battle, actor) or direction not in _AREA_START:
            raise ActionError("潮汐拳需要合法的恰两格落点及八方向之一。")
        cells = _tidal_cells(battle, landing, direction)
        if not cells:
            raise ActionError("潮汐拳所选方向在场外。")
        return landing, cells

    def can_use(self, battle: Battle, actor: HeroUnit,
                payload: dict[str, Any] | None = None) -> tuple[bool, str]:
        ok, reason = super().can_use(battle, actor, payload)
        if not ok:
            return ok, reason
        if not self.landings(battle, actor):
            return False, "当前没有恰两格合法落点。"
        if payload is not None and ("x" in payload or "direction" in payload):
            try:
                self._aim(battle, actor, payload)
            except (ActionError, KeyError, TypeError, ValueError):
                return False, "请选择潮汐拳合法落格和方向。"
        return True, ""

    def preview(self, battle: Battle, actor: Unit) -> dict[str, Any]:
        landings = self.landings(battle, actor)
        return {"cells": positions_to_dict(landings), "target_unit_ids": [],
                "secondary_cells": positions_to_dict([cell for landing in landings
                                                       for direction in _AREA_START
                                                       for cell in _tidal_cells(battle, landing, direction)]),
                "requires_target": True,
                "selection": {"mode": "cell_direction", "directions": [
                    {"dx": dx, "dy": dy, "label": f"{dx:+d},{dy:+d}"} for dx, dy in ALL_DIRECTIONS]}}

    def get_target_cells_for_payload(self, battle: Battle, actor: Unit,
                                     payload: dict[str, Any]) -> list[Position]:
        return self._aim(battle, actor, payload)[1]

    def get_target_units_for_payload(self, battle: Battle, actor: Unit,
                                     payload: dict[str, Any]) -> list[Unit]:
        return []  # The move precedes a separately chained damage segment.

    def execute(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> None:
        try:
            landing, cells = self._aim(battle, actor, payload)
            movement = battle.move_unit(actor, landing, via_skill=True,
                                        max_distance=2, exact_distance=2,
                                        tags={"amethyst_tidal_fist"})
        except ActionError as exc:
            raise ActionMiss("潮汐拳的两格落点已失效。") from exc
        if movement.cancelled or actor.position != landing:
            raise ActionMiss("潮汐拳未完成两格位移，拳伤落空。")
        if not actor.alive or actor.position is None or actor.banished:
            return
        battle.queue_skill_effect_action(
            actor=actor, display_name="潮汐拳·拳伤", effect_code="amethyst_tidal_damage",
            payload={"cells": positions_to_dict(cells), "attack_power": actor.stat("attack"),
                     "refresh_cell_targets": True}, target_cells=cells,
            effect_resolver=_resolve_tidal_damage,
        )

    def ai_payload_candidates(self, battle: Battle, actor: Unit,
                              action: dict[str, Any]) -> list[dict[str, Any]]:
        return [{"type": "skill", "unit_id": actor.unit_id, "skill_code": self.code,
                 "x": landing.x, "y": landing.y, "direction": {"dx": dx, "dy": dy}}
                for landing in self.landings(battle, actor) for dx, dy in ALL_DIRECTIONS
                if _tidal_cells(battle, landing, (dx, dy))]

    def ai_candidate_payloads(self, context: Any) -> list[dict[str, Any]]:
        return self.ai_payload_candidates(context.battle, context.actor, context.action)

    def _area_value(self, battle: Battle, actor: Unit,
                    landing: Position, cells: list[Position]) -> float:
        value = 0.0
        for target in battle.effect_units_at_cells(cells, ignore=actor):
            damage = battle.damage_rule.calculate_damage(actor.stat("attack"), target.stat("defense"))
            if target.total_shields() or target.magic_immunity:
                damage = 0.0
            damage = min(target.current_hp, damage)
            impact = damage * 100 + (28 if damage >= target.current_hp > 0 else 0)
            if damage > 0 and _is_water_hero(target) and not is_mana_drain_immune(target):
                impact += min(1, target.current_mana) * 36
            value += impact if target.player_id != actor.player_id else -impact * 1.5
        if actor.position is not None:
            value += min(12.0, landing.distance_to(actor.position) * 2.0)
        return value - 18.0

    def ai_prefilter_score(self, battle: Battle, actor: Unit,
                           payload: dict[str, Any], shared: Any) -> float:
        # Payloads originate from legal landing/direction enumeration; avoid
        # re-running the pathfinder for every cheap shortlist estimate.
        try:
            landing = payload_position(payload)
            direction = payload_direction(payload)
            if direction not in _AREA_START:
                return -1000.0
        except (ActionError, KeyError, TypeError, ValueError):
            return -1000.0
        return self._area_value(battle, actor, landing, _tidal_cells(battle, landing, direction))

    def ai_payload_value(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> float:
        try:
            landing, cells = self._aim(battle, actor, payload)
        except (ActionError, KeyError, TypeError, ValueError):
            return -1000.0
        return self._area_value(battle, actor, landing, cells)

    def ai_priority_score(self, context: Any) -> float:
        return self.ai_payload_value(context.battle, context.actor, context.payload)
