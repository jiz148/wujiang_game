"""R347: Pei Xuan's guardian supplies, judges and repositions the team."""

from __future__ import annotations

from itertools import combinations
from typing import Any

from wujiang.tactical.engine.core import (
    ActionError, Battle, DamageContext, HealContext, Position, Skill, Stats, StatusEffect, Trait, Unit,
    is_army_soldier,
)
from wujiang.tactical.heroes.base import AbstractHero
from wujiang.tactical.heroes.mechanics.guardian_spirit import (
    GuardianTetherTrait, SummonGuardianSkill, present,
)


def _true_hero(unit: Unit) -> bool:
    return not unit.is_summon and not unit.is_clone and not is_army_soldier(unit)


class DizhengSupplyTrait(Trait):
    def __init__(self) -> None:
        super().__init__("地正补给", "周围5×5所有单位在每名己方武将回合开始时魔+1、血+1/4。")

    def beneficiaries(self, battle: Battle) -> list[Unit]:
        owner = self.owner
        return ([unit for unit in battle.all_units() if present(unit)
                 and battle.distance_between_units(owner, unit) <= 2]
                if present(owner) else [])

    def on_any_turn_start(self, battle: Battle, active_unit: Unit) -> None:
        owner = self.owner
        if not present(owner) or active_unit.player_id != owner.player_id or not _true_hero(active_unit):
            return
        for unit in self.beneficiaries(battle):
            gained = unit.gain_mana(1)
            if gained:
                battle.log(f"{unit.name} 因地正补给回复 {gained} 点魔力。")
            if unit.alive and (unit.current_hp < unit.max_health - 1e-9 or unit.allow_overheal):
                battle.heal(HealContext(source=owner, target=unit, amount=0.25,
                                        action_name="地正补给"))


class ActiveSealStatus(StatusEffect):
    def __init__(self) -> None:
        super().__init__("魔封拳", "直到下个本人回合结束不能使用主动技能。",
                         duration=1, tick_scope="owner_turn_end")

    def blocks_skill_use(self, battle: Battle, actor: Unit, skill: Skill) -> tuple[bool, str]:
        if actor is self.owner and skill.timing == "active":
            return True, "魔封拳封锁了主动技能。"
        return False, ""


class DizhengGuardian(AbstractHero):
    hero_code = "guardian_dizheng"
    hero_name = "守护灵。地正"
    role = "召唤物"
    attribute = "土"
    race = "守护灵"
    level = 1
    base_stats = Stats(attack=2, defense=999, speed=3, attack_range=1, mana=0)

    def __init__(self, player_id: int) -> None:
        super().__init__(player_id, is_summon=True)

    def build_skills(self) -> list[Skill]:
        return [RewardPunishmentSkill(), MagicSealFistSkill(), StarAdvanceSkill()]

    def build_traits(self) -> list[Trait]:
        return [GuardianTetherTrait(), DizhengSupplyTrait()]


class SummonDizhengSkill(SummonGuardianSkill):
    def __init__(self) -> None:
        super().__init__("summon_guardian_dizheng", "守护灵。地正", DizhengGuardian)
        self.description = "每场一次；在本人周围召唤攻2、守无限、速3、范1且周围5×5补给的地正守护灵。"
        self._damage_turn = -1
        self._damagers: set[str] = set()

    def on_battle_damage(self, battle: Battle, ctx: DamageContext) -> None:
        """Keep reward accounting alive when Pei's innate traits are sealed."""
        owner = self.owner
        if owner is None or ctx.source is None or ctx.actual_damage <= 0 or ctx.cancelled:
            return
        if self._damage_turn != battle.turn_number:
            self._damage_turn = battle.turn_number
            self._damagers.clear()
        if (ctx.source.player_id == owner.player_id and _true_hero(ctx.source)
                and ctx.target.player_id != owner.player_id):
            self._damagers.add(ctx.source.unit_id)

    def damaged_this_turn(self, battle: Battle, unit: Unit) -> bool:
        return self._damage_turn == battle.turn_number and unit.unit_id in self._damagers

    def ai_payload_value(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> float | None:
        try:
            cell = Position(int(payload["x"]), int(payload["y"]))
        except (KeyError, TypeError, ValueError):
            return -100.0
        if cell not in self.available_cells(battle, actor):
            return -100.0
        value = 35.0
        for unit in battle.all_units():
            if not present(unit) or min((body.distance_to(cell) for body in battle.unit_cells(unit)), default=99) > 2:
                continue
            gain = min(1.0, max(0.0, unit.max_mana() - unit.current_mana)) * 10.0
            gain += min(0.25, max(0.0, unit.max_health - unit.current_hp)) * 70.0
            value += gain if unit.player_id == actor.player_id else -gain * 1.5
        return value


class RewardPunishmentSkill(Skill):
    ai_payload_value_owned = True
    ai_enemy_impact_requirement = False

    def __init__(self) -> None:
        super().__init__("reward_punishment", "论赏罚",
                         "每回合一次；本轮已对敌造成真实伤害的场上己方武将魔+1，其余魔-1。",
                         max_uses_per_turn=1, target_mode="self")

    def recipients(self, battle: Battle, actor: Unit) -> list[Unit]:
        return [unit for unit in battle.all_units() if present(unit) and unit.player_id == actor.player_id
                and _true_hero(unit)]

    def ledger(self, battle: Battle, actor: Unit) -> SummonDizhengSkill | None:
        summoner = battle.units.get(actor.summoner_id or "")
        skill = summoner.get_skill("summon_guardian_dizheng") if summoner else None
        return skill if isinstance(skill, SummonDizhengSkill) else None

    def preview(self, battle: Battle, actor: Unit) -> dict[str, Any]:
        units = self.recipients(battle, actor)
        return {"cells": [cell.to_dict() for unit in units for cell in battle.unit_cells(unit)],
                "target_unit_ids": [unit.unit_id for unit in units], "secondary_cells": [],
                "requires_target": False}

    def get_target_units_for_payload(self, battle: Battle, actor: Unit,
                                     payload: dict[str, Any]) -> list[Unit]:
        return self.recipients(battle, actor)

    def execute(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> None:
        ledger = self.ledger(battle, actor)
        for unit in self.recipients(battle, actor):
            damaged = ledger is not None and ledger.damaged_this_turn(battle, unit)
            if damaged:
                changed = unit.gain_mana(1)
                battle.log(f"{unit.name} 因论赏罚有功，魔力+{changed}。")
            else:
                changed = unit.drain_mana(1)
                battle.log(f"{unit.name} 因论赏罚无功，魔力-{changed}。")

    def ai_payload_value(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> float | None:
        ledger = self.ledger(battle, actor)
        score = -10.0
        for unit in self.recipients(battle, actor):
            damaged = ledger is not None and ledger.damaged_this_turn(battle, unit)
            if damaged:
                score += min(1.0, max(0.0, unit.max_mana() - unit.current_mana)) * 30.0
            else:
                score -= min(1.0, unit.current_mana) * (40.0 if unit is battle.units.get(actor.summoner_id or "") else 30.0)
        return score

    def ai_priority_score(self, context: Any) -> float:
        return self.ai_payload_value(context.battle, context.actor, context.payload) or -100


class MagicSealFistSkill(Skill):
    ai_payload_value_owned = True
    ai_probe_hostile_effect = True

    def __init__(self) -> None:
        super().__init__("magic_seal_fist", "魔封拳",
                         "每回合一次；给予通常技能伤，实际受伤者到下个本人回合结束不能使用主动技能。",
                         max_uses_per_turn=1, target_mode="enemy")

    def targets(self, battle: Battle, actor: Unit) -> list[Unit]:
        return [unit for unit in battle.all_units() if present(unit) and unit.player_id != actor.player_id
                and battle.unit_target_in_range_and_line(actor, unit, actor.targeting_range())
                and battle.unit_can_be_selected(unit, actor=actor)[0]]

    def preview(self, battle: Battle, actor: Unit) -> dict[str, Any]:
        targets = self.targets(battle, actor)
        return {"cells": [cell.to_dict() for unit in targets for cell in battle.unit_cells(unit)],
                "target_unit_ids": [unit.unit_id for unit in targets], "secondary_cells": [],
                "requires_target": True}

    def get_target_units_for_payload(self, battle: Battle, actor: Unit,
                                     payload: dict[str, Any]) -> list[Unit]:
        target = battle.units.get(str(payload.get("target_unit_id") or ""))
        return [target] if target is not None else []

    def can_use(self, battle: Battle, actor: Unit,
                payload: dict[str, Any] | None = None) -> tuple[bool, str]:
        ok, reason = super().can_use(battle, actor, payload)
        if not ok:
            return ok, reason
        if payload and payload.get("target_unit_id") and str(payload["target_unit_id"]) not in {
                target.unit_id for target in self.targets(battle, actor)}:
            return False, "魔封拳目标不在合法范内。"
        return (True, "") if self.targets(battle, actor) else (False, "没有合法的魔封拳目标。")

    def execute(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> None:
        target = battle.units.get(str(payload.get("resolved_target_unit_id") or payload.get("target_unit_id") or ""))
        if target not in self.targets(battle, actor):
            battle.log("魔封拳未命中仍在合法范围的单位。")
            return
        ctx = battle.resolve_damage(DamageContext(source=actor, target=target, attack_power=actor.stat("attack"),
                                                  is_skill=True, action_name=self.name,
                                                  tags={"skill", self.code}))
        if ctx.actual_damage > 0 and ctx.target.alive:
            ctx.target.add_status(ActiveSealStatus(), source=actor)
            battle.log(f"{ctx.target.name} 被魔封拳封锁主动技能，直到下个本人回合结束。")

    def ai_payload_value(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> float | None:
        target = battle.units.get(str(payload.get("target_unit_id") or ""))
        if target not in self.targets(battle, actor) or target.magic_immunity:
            return -100.0
        damage = 0.0 if target.total_shields() else min(target.current_hp, battle.damage_rule.calculate_damage(
            actor.stat("attack"), target.stat("defense")))
        active_count = sum(skill.timing == "active" and skill.cooldown_remaining <= 0 for skill in target.skills)
        return damage * 100.0 + (active_count * 18.0 if damage > 0 and not target.has_status("魔封拳") else 0.0) - 12.0

    def ai_priority_score(self, context: Any) -> float:
        return self.ai_payload_value(context.battle, context.actor, context.payload) or -100


class StarAdvanceSkill(Skill):
    ai_payload_value_owned = True
    ai_enemy_impact_requirement = False
    is_displacement_skill = True
    requires_direct_unit_target_line = False

    def moves_owner_on_use(self, battle: Battle, actor: Unit,
                           payload: dict[str, Any] | None) -> bool:
        return False

    def __init__(self) -> None:
        super().__init__("star_advance", "占星术。进",
                         "每场一次；最多两名己方守护灵或召唤者共选纵向，依次尽量移动最多3格。",
                         max_uses_per_battle=1, target_mode="unit")

    def targets(self, battle: Battle, actor: Unit) -> list[Unit]:
        return [unit for unit in battle.all_units() if present(unit) and unit.player_id == actor.player_id
                and unit is not actor and ("守护灵" in unit.name or unit.unit_id == actor.summoner_id)]

    def preview(self, battle: Battle, actor: Unit) -> dict[str, Any]:
        units = self.targets(battle, actor)
        return {"cells": [cell.to_dict() for unit in units for cell in battle.unit_cells(unit)],
                "target_unit_ids": [unit.unit_id for unit in units], "secondary_cells": [],
                "requires_target": True,
                "selection": {"mode": "multi_unit", "min_targets": 1, "max_targets": 2,
                              "directions": [{"dx": 0, "dy": -1, "label": "向上"},
                                             {"dx": 0, "dy": 1, "label": "向下"}],
                              "prompt": "选择一或两名己方守护灵或召唤者，再选择共同的纵向移动方向。"}}

    def _selection(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> tuple[list[Unit], int]:
        ids = payload.get("target_unit_ids")
        if not isinstance(ids, list) or not 1 <= len(ids) <= 2 or len(set(map(str, ids))) != len(ids):
            raise ActionError("占星进须选择一或两名不同的单位。")
        targets = self.targets(battle, actor)
        chosen = [battle.units.get(str(unit_id)) for unit_id in ids]
        if any(unit not in targets for unit in chosen):
            raise ActionError("占星进目标必须是己方守护灵或召唤者。")
        direction = payload.get("direction") or {}
        if not isinstance(direction, dict) or direction.get("dx") != 0 or direction.get("dy") not in (-1, 1):
            raise ActionError("占星进只可选择向上或向下的共同方向。")
        return chosen, int(direction["dy"])

    def can_use(self, battle: Battle, actor: Unit,
                payload: dict[str, Any] | None = None) -> tuple[bool, str]:
        ok, reason = super().can_use(battle, actor, payload)
        if not ok:
            return ok, reason
        if payload and ("target_unit_ids" in payload or "direction" in payload):
            try:
                self._selection(battle, actor, payload)
            except (ActionError, TypeError, ValueError) as error:
                return False, str(error)
        return (True, "") if self.targets(battle, actor) else (False, "没有合格的占星进目标。")

    def get_target_units_for_payload(self, battle: Battle, actor: Unit,
                                     payload: dict[str, Any]) -> list[Unit]:
        try:
            return self._selection(battle, actor, payload)[0]
        except (ActionError, TypeError, ValueError):
            return []

    @staticmethod
    def _front_first(units: list[Unit], dy: int) -> list[Unit]:
        return sorted(units, key=lambda unit: (unit.position.y * dy * -1, unit.position.x, unit.unit_id))

    @staticmethod
    def _destination(battle: Battle, unit: Unit, dy: int) -> Position | None:
        if not present(unit) or unit.cannot_move or unit.direct_effects_blocked() or unit.skill_non_damage_effects_blocked():
            return None
        for steps in (3, 2, 1):
            target = unit.position.offset(0, dy * steps)
            if not battle.in_bounds(target) or not battle.can_place_unit(unit, target, mover=unit):
                continue
            try:
                battle.find_path(unit, target, max_distance=steps, straight_only=True)
            except ActionError:
                continue
            return target
        return None

    def planned_destinations(self, battle: Battle, chosen: list[Unit], dy: int) -> list[tuple[Unit, Position, Position | None]]:
        """Score the same front-first occupancy that execution will encounter."""
        original = {unit.unit_id: unit.position for unit in chosen}
        planned: list[tuple[Unit, Position, Position | None]] = []
        try:
            for unit in self._front_first(chosen, dy):
                start = unit.position
                target = self._destination(battle, unit, dy)
                planned.append((unit, start, target))
                if target is not None:
                    unit.position = target
            return planned
        finally:
            for unit in chosen:
                unit.position = original[unit.unit_id]

    def execute(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> None:
        chosen, dy = self._selection(battle, actor, payload)
        for unit in self._front_first(chosen, dy):
            target = self._destination(battle, unit, dy)
            if target is None:
                battle.log(f"{unit.name} 的占星进位移被阻挡。")
                continue
            try:
                battle.move_unit(unit, target, via_skill=True, forced=True, straight_only=True,
                                 max_distance=3, tags={"star_advance"})
            except ActionError:
                battle.log(f"{unit.name} 的占星进位移未能结算。")

    def ai_payload_candidates(self, battle: Battle, actor: Unit,
                              action: dict[str, Any]) -> list[dict[str, Any]] | None:
        targets = self.targets(battle, actor)
        base = {"type": "skill", "unit_id": actor.unit_id, "skill_code": self.code}
        payloads = [{**base, "target_unit_ids": [unit.unit_id for unit in chosen],
                     "direction": {"dx": 0, "dy": dy}}
                    for count in (1, 2) for chosen in combinations(targets, count) for dy in (-1, 1)]
        return sorted(payloads, key=lambda item: self.ai_payload_value(battle, actor, item) or -1000,
                      reverse=True)[:16]

    def ai_candidate_payloads(self, context: Any) -> list[dict[str, Any]]:
        return self.ai_payload_candidates(context.battle, context.actor, context.action) or []

    def ai_payload_value(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> float | None:
        try:
            chosen, dy = self._selection(battle, actor, payload)
        except (ActionError, TypeError, ValueError):
            return -1000.0
        enemies = [unit for unit in battle.all_units() if present(unit) and unit.player_id != actor.player_id]
        score = -35.0
        planned = self.planned_destinations(battle, chosen, dy)
        tether_pairs: list[tuple[Unit, Unit]] = []
        for unit in battle.all_units():
            if (not present(unit) or unit.player_id != actor.player_id or not unit.summoner_id
                    or not any(isinstance(trait, GuardianTetherTrait) for trait in unit.traits)):
                continue
            summoner = battle.units.get(unit.summoner_id)
            if present(summoner):
                tether_pairs.append((unit, summoner))
        projected_positions: dict[str, Position] = {}
        broken_tethers: set[str] = set()
        for moved, _, target in planned:
            if target is None:
                continue
            projected_positions[moved.unit_id] = target
            for guardian, summoner in tether_pairs:
                if (guardian.unit_id in broken_tethers
                        or moved.unit_id not in {guardian.unit_id, summoner.unit_id}
                        or guardian.position.distance_to(summoner.position) > 6):
                    continue
                after = projected_positions.get(guardian.unit_id, guardian.position).distance_to(
                    projected_positions.get(summoner.unit_id, summoner.position))
                if after > 6:
                    score -= 2000.0
                    broken_tethers.add(guardian.unit_id)
        for unit, start, target in planned:
            if target is None:
                continue
            before = min((start.distance_to(enemy.position) for enemy in enemies), default=8)
            after = min((target.distance_to(enemy.position) for enemy in enemies), default=8)
            if unit.unit_id == actor.summoner_id:
                score += max(0, after - before) * (12 if unit.current_hp <= 0.5 else 4)
                score -= max(0, before - after) * (9 if unit.current_hp <= 0.5 else 2)
            else:
                score += max(0, before - after) * 10
                score -= max(0, after - before) * 4
            score += min(3, abs(target.y - start.y)) * 3
        return score

    def ai_priority_score(self, context: Any) -> float:
        return self.ai_payload_value(context.battle, context.actor, context.payload) or -1000
