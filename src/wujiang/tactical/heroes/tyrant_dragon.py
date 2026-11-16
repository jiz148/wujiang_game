"""R340 tyrant dragon: mana-point economy, sacrifices and area control."""

from __future__ import annotations

from itertools import combinations
from typing import Any

from wujiang.tactical.prediction import random
from wujiang.tactical.engine.core import (
    ActionError, Battle, DamageContext, HealContext, Position, Skill, StatusEffect,
    Trait, Unit,
)
from wujiang.tactical.heroes.common import FlagStatus, match_payload_pattern, positions_to_dict
from wujiang.tactical.heroes.mechanics.area_helpers import (
    area_patterns_preview, combined_remote_rectangle_patterns,
)
from wujiang.tactical.heroes.mechanics.declared_area import DeclaredAreaSkillMixin
from wujiang.tactical.heroes.mechanics.mana_point_cost import ManaPointCostSkill
from wujiang.tactical.heroes.mechanics.next_five_effects import apply_piercing_status_effect
from wujiang.tactical.heroes.mechanics.next_five_geometry import square_around_cells


def _present(unit: Unit | None) -> bool:
    return bool(unit is not None and unit.alive and not unit.banished and unit.position is not None)


def _payload(actor: Unit, code: str, **extras: Any) -> dict[str, Any]:
    return {"type": "skill", "unit_id": actor.unit_id, "skill_code": code, **extras}


class TyrantDragonPointsTrait(Trait):
    def __init__(self) -> None:
        super().__init__("暴君龙魔力点", "初始魔力点等于基础魔；普攻声明+1点；本人轮开始按点数回血。")

    def bind(self, owner: Unit) -> "TyrantDragonPointsTrait":
        super().bind(owner)
        owner.mana_points = float(owner.base_stats.mana)
        return self

    def on_owner_action_declared(self, battle: Battle, action_type: str,
                                 payload: dict[str, Any]) -> None:
        if action_type == "attack" and _present(self.owner):
            gained = self.owner.gain_mana_points(1)
            if gained:
                battle.log_public_event(f"{self.owner.name} 普攻蓄积 {gained:g} 魔力点。",
                                        source=self.owner, target=self.owner)

    def on_owner_turn_start(self, battle: Battle) -> None:
        if _present(self.owner) and self.owner.mana_points > 0:
            battle.heal(HealContext(source=self.owner, target=self.owner,
                                    amount=self.owner.mana_points / 4,
                                    action_name="暴君龙魔力点回复"))


class InfernoWoundStatus(StatusEffect):
    def __init__(self, lost: float) -> None:
        super().__init__("炼狱之火伤口", "这部分实际失血在本场不能回复。")
        self.unrecoverable_hp = max(0.0, float(lost))

    def max_recoverable_hp(self) -> float | None:
        return max(0.0, self.owner.max_health - self.unrecoverable_hp) if self.owner else None

    def to_public_dict(self, battle: Battle) -> dict[str, Any]:
        data = super().to_public_dict(battle)
        data["unrecoverable_hp"] = round(self.unrecoverable_hp, 4)
        return data


class BrutalPolicySkill(Skill):
    requires_direct_unit_target_line = False
    ai_payload_value_owned = True
    ai_enemy_impact_requirement = False

    def __init__(self) -> None:
        super().__init__("brutal_policy", "残暴政策",
                         "每本人回合一次；选不同在场己方单位各一实有技能，使之本场失去，并按成功数获得魔力点。",
                         max_uses_per_turn=1, target_mode="none")

    def donor_options(self, battle: Battle, actor: Unit) -> list[tuple[Unit, Skill]]:
        return [(unit, skill) for unit in battle.player_units(actor.player_id)
                if _present(unit) and battle.effect_recipient(unit) is unit
                and not unit.direct_effects_blocked() and not unit.skill_non_damage_effects_blocked()
                for skill in unit.skills]

    def _selections(self, payload: dict[str, Any]) -> list[tuple[str, str]]:
        raw = payload.get("selections")
        if not isinstance(raw, list):
            return []
        return [(str(item.get("unit_id") or ""), str(item.get("skill_code") or ""))
                for item in raw if isinstance(item, dict)]

    def _valid(self, battle: Battle, actor: Unit, choices: list[tuple[str, str]]) -> bool:
        allowed = {(unit.unit_id, skill.code) for unit, skill in self.donor_options(battle, actor)}
        return bool(choices) and len({unit_id for unit_id, _ in choices}) == len(choices) and all(
            choice in allowed for choice in choices)

    def can_use(self, battle: Battle, actor: Unit,
                payload: dict[str, Any] | None = None) -> tuple[bool, str]:
        ok, reason = super().can_use(battle, actor, payload)
        if not ok:
            return ok, reason
        if payload and ("type" in payload or "selections" in payload):
            return (True, "") if self._valid(battle, actor, self._selections(payload)) else (
                False, "请选择不同在场己方单位各一项实有技能。")
        return (True, "") if self.donor_options(battle, actor) else (False, "没有可剥夺的己方技能。")

    def get_target_units_for_payload(self, battle: Battle, actor: Unit,
                                     payload: dict[str, Any]) -> list[Unit]:
        return [battle.units[unit_id] for unit_id, _ in self._selections(payload)
                if unit_id in battle.units]

    def get_target_cells_for_payload(self, battle: Battle, actor: Unit,
                                     payload: dict[str, Any]) -> list[Position]:
        return [cell for unit in self.get_target_units_for_payload(battle, actor, payload)
                for cell in battle.unit_cells(unit)]

    def execute(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> None:
        removed = 0
        for unit_id, skill_code in self._selections(payload):
            donor = battle.units.get(unit_id)
            if not _present(donor) or donor.player_id != actor.player_id:
                continue
            definition = next((skill for skill in donor.skills if skill.code == skill_code), None)
            if definition is None or donor.direct_effects_blocked() or donor.skill_non_damage_effects_blocked():
                continue
            donor.skills.remove(definition)
            removed += 1
            battle.log_public_event(f"{donor.name} 因{actor.name}的残暴政策失去【{definition.name}】。",
                                    source=actor, target=donor)
        if removed:
            gained = actor.gain_mana_points(removed)
            battle.log_public_event(f"{actor.name} 获得 {gained:g} 魔力点。", source=actor, target=actor)

    def preview(self, battle: Battle, actor: Unit) -> dict[str, Any]:
        by_unit: dict[str, dict[str, Any]] = {}
        for unit, skill in self.donor_options(battle, actor):
            entry = by_unit.setdefault(unit.unit_id, {"unit_id": unit.unit_id,
                                                       "name": unit.name, "skills": []})
            entry["skills"].append({"code": skill.code, "name": skill.name})
        targets = list(by_unit.values())
        return {"cells": [], "target_unit_ids": [target["unit_id"] for target in targets],
                "secondary_cells": [], "requires_target": True,
                "selection": {"mode": "unit_skill_multi", "targets": targets,
                              "prompt": "勾选要牺牲的己方单位，并为每个单位选一项技能。"}}

    def _candidate_payloads(self, battle: Battle, actor: Unit) -> list[dict[str, Any]]:
        options = sorted(self.donor_options(battle, actor),
                         key=lambda item: (self._loss_value(item[0], item[1]), item[0].unit_id, item[1].code))
        singles = [_payload(actor, self.code, selections=[{"unit_id": unit.unit_id,
                                                            "skill_code": skill.code}])
                   for unit, skill in options[:20]]
        cheapest = []
        for unit, skill in options:
            if unit.unit_id not in {candidate[0].unit_id for candidate in cheapest}:
                cheapest.append((unit, skill))
            if len(cheapest) == 3:
                break
        groups = [_payload(actor, self.code, selections=[{"unit_id": unit.unit_id,
                                                           "skill_code": skill.code} for unit, skill in group])
                  for size in (2, 3) for group in combinations(cheapest, size)]
        return singles + groups

    def ai_payload_candidates(self, battle: Battle, actor: Unit,
                              action: dict[str, Any]) -> list[dict[str, Any]] | None:
        return self._candidate_payloads(battle, actor)

    def ai_candidate_payloads(self, context: Any) -> list[dict[str, Any]]:
        return self._candidate_payloads(context.battle, context.actor)

    @staticmethod
    def _loss_value(donor: Unit, skill: Skill) -> float:
        if skill.max_uses_per_battle is not None and skill.uses_this_battle >= skill.max_uses_per_battle:
            return 2.0
        return (22.0 + min(8, donor.level) * 2.0 + (12.0 if skill.timing == "active" else 8.0)
                + (12.0 if donor.turn_ready and skill.uses_this_turn == 0 else 0.0))

    def ai_payload_value(self, battle: Battle, actor: Unit,
                         payload: dict[str, Any]) -> float | None:
        choices = self._selections(payload)
        if not self._valid(battle, actor, choices):
            return -1000.0
        lookup = {(unit.unit_id, skill.code): (unit, skill)
                  for unit, skill in self.donor_options(battle, actor)}
        point_value = (18.0 + (26.0 if actor.mana_points <= 1 else 12.0 if actor.mana_points <= 3 else 0.0)
                       + min(0.5, max(0.0, actor.max_health - actor.current_hp)) * 28.0)
        return sum(point_value - self._loss_value(*lookup[choice]) for choice in choices) - 5.0

    def ai_priority_score(self, context: Any) -> float:
        return self.ai_payload_value(context.battle, context.actor, context.payload)

class TyrantAreaMixin(DeclaredAreaSkillMixin):
    """Shared shape and payload behavior for this dragon's declared rectangles."""
    area_height: int

    def patterns(self, battle: Battle, actor: Unit) -> list[list[Position]]:
        return combined_remote_rectangle_patterns(battle, actor,
                                                  [(3, self.area_height), (self.area_height, 3)])

    def can_use(self, battle: Battle, actor: Unit,
                payload: dict[str, Any] | None = None) -> tuple[bool, str]:
        ok, reason = super().can_use(battle, actor, payload)
        if ok and payload and "cells" in payload:
            try:
                match_payload_pattern(payload, self.patterns(battle, actor))
            except ActionError as exc:
                return False, str(exc)
        return ok, reason

    def preview(self, battle: Battle, actor: Unit) -> dict[str, Any]:
        return area_patterns_preview(battle, actor, self.patterns(battle, actor))

    def get_target_cells_for_payload(self, battle: Battle, actor: Unit,
                                     payload: dict[str, Any]) -> list[Position]:
        return self.chosen_cells(battle, actor, payload)

    def get_target_units_for_payload(self, battle: Battle, actor: Unit,
                                     payload: dict[str, Any]) -> list[Unit]:
        return battle.effect_units_at_cells(self.chosen_cells(battle, actor, payload))

    def ignores_shield_for_payload(self, battle: Battle, actor: Unit,
                                   payload: dict[str, Any]) -> bool:
        return True

    def ai_payload_candidates(self, battle: Battle, actor: Unit,
                              action: dict[str, Any]) -> list[dict[str, Any]] | None:
        return _tyrant_area_payloads(self, battle, actor)

    def ai_candidate_payloads(self, context: Any) -> list[dict[str, Any]]:
        return _tyrant_area_payloads(self, context.battle, context.actor)

    def ai_priority_score(self, context: Any) -> float:
        return self.ai_payload_value(context.battle, context.actor, context.payload)


class InfernoFireSkill(TyrantAreaMixin, ManaPointCostSkill):
    ai_payload_value_owned = True
    area_height = 6

    def __init__(self) -> None:
        super().__init__("inferno_fire", "炼狱之火",
                         "每本人回合一次，费1魔力点；远程3×6破魔伤害，实际失血部分本场不能回复。",
                         mana_point_cost=1, max_uses_per_turn=1, target_mode="cell")

    def execute(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> None:
        cells = self.chosen_cells(battle, actor, payload)
        for target in battle.effect_units_at_cells(cells):
            ctx = battle.resolve_damage(DamageContext(
                source=actor, target=target, attack_power=actor.stat("attack"), is_skill=True,
                action_name=self.name, ignore_shield=True,
                area_cell_hits=battle.unit_hit_count_for_cells(target, cells),
                tags={"skill", self.code, "pierce"}))
            if ctx.actual_damage <= 0:
                continue
            recipient = ctx.target
            wound = next((status for status in recipient.statuses
                          if isinstance(status, InfernoWoundStatus)), None)
            if wound is None:
                recipient.add_status(InfernoWoundStatus(ctx.actual_damage), source=actor)
            else:
                wound.unrecoverable_hp = min(recipient.max_health,
                                             wound.unrecoverable_hp + ctx.actual_damage)
            battle.log_public_event(f"{recipient.name} 有 {ctx.actual_damage:g} 点失血不能回复。",
                                    source=actor, target=recipient)

    def ai_payload_value(self, battle: Battle, actor: Unit,
                         payload: dict[str, Any]) -> float | None:
        return _tyrant_area_score(self, battle, actor, payload, wound=True)

    def ai_effective_enemy_impact(self, battle: Battle, actor: Unit,
                                  payload: dict[str, Any], targets: list[Unit] | None = None,
                                  shared: Any = None, action: dict[str, Any] | None = None) -> bool:
        return self.ai_payload_value(battle, actor, payload) > 0


def _tyrant_area_score(skill: Skill, battle: Battle, actor: Unit,
                       payload: dict[str, Any], *, wound: bool = False,
                       control: bool = False,
                       cells: list[Position] | None = None) -> float:
    if cells is None:
        try:
            cells = skill.chosen_cells(battle, actor, payload)
        except ActionError:
            return -1000.0
    score = -12.0
    for target in battle.effect_units_at_cells(cells):
        if target.magic_immunity or target.direct_effects_blocked():
            continue
        if control:
            impact = 0.0 if target.cannot_normal_move else 18.0 + 8.0 * min(
                4, target.normal_move_distance())
        else:
            attack = actor.stat("attack") + max(0, battle.unit_hit_count_for_cells(target, cells) - 1)
            expected = min(target.current_hp, max(0.0, battle.damage_rule.calculate_damage(
                attack, target.stat("defense"))))
            impact = expected * (115.0 + (30.0 if wound else 0.0))
            if target.total_shields():
                impact += 10.0
        score += impact if target.player_id != actor.player_id else -impact * 1.6
    return score


def _tyrant_area_payloads(skill: Skill, battle: Battle, actor: Unit) -> list[dict[str, Any]]:
    ranked = []
    for cells in skill.patterns(battle, actor):
        payload = _payload(actor, skill.code, cells=positions_to_dict(cells))
        value = _tyrant_area_score(skill, battle, actor, payload,
                                   wound=isinstance(skill, InfernoFireSkill),
                                   control=isinstance(skill, DragonWingbeatSkill), cells=cells)
        if value is not None:
            ranked.append((value, payload))
    ranked.sort(key=lambda item: item[0], reverse=True)
    return [payload for _, payload in ranked[:24]]


class WingbeatImmobilityStatus(StatusEffect):
    def __init__(self, *, active_now: bool = False) -> None:
        super().__init__("巨龙振翅禁移动", "下一个本人轮不能普通移动，到该轮末解除。")
        self.activated = active_now
        self.pending_turns = 1
        self.flag_name = "cannot_normal_move"

    def bind(self, owner: Unit) -> "WingbeatImmobilityStatus":
        super().bind(owner)
        if self.activated:
            owner.cannot_normal_move = True
        return self

    @property
    def provided_flags(self) -> frozenset[str]:
        return frozenset({self.flag_name}) if self.activated else frozenset()

    def on_owner_turn_start(self, battle: Battle) -> None:
        if self.owner is not None and self.pending_turns > 0:
            self.pending_turns -= 1
            self.activated = True
            self.owner.cannot_normal_move = True

    def on_owner_turn_end(self, battle: Battle) -> None:
        if self.activated and self.owner is not None:
            self.activated = False
            if self.pending_turns == 0:
                self.owner.remove_status(self, battle)
            else:
                self._sync_move_flag()

    def _sync_move_flag(self) -> None:
        if self.owner is not None:
            self.owner.cannot_normal_move = any(
                "cannot_normal_move" in getattr(status, "provided_flags", ())
                or (isinstance(status, FlagStatus) and status.flag_name == "cannot_normal_move"
                    and status.value and getattr(status, "flag_active", True))
                for status in self.owner.statuses)

    def on_removed(self, battle: Battle) -> None:
        self._sync_move_flag()

    def to_public_dict(self, battle: Battle) -> dict[str, Any]:
        data = super().to_public_dict(battle)
        data["active_on_current_turn"] = self.activated
        data["pending_owner_turns"] = self.pending_turns
        return data


class DragonWingbeatSkill(TyrantAreaMixin, Skill):
    ai_payload_value_owned = True
    ai_enemy_impact_requirement = False
    area_height = 9

    def __init__(self) -> None:
        super().__init__("dragon_wingbeat", "巨龙振翅",
                         "2轮一次，远程3×9破魔；没有伤害，命中者下个本人轮不能普通移动。",
                         cooldown_turns=2, target_mode="cell")

    def execute(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> None:
        for target in battle.effect_units_at_cells(self.chosen_cells(battle, actor, payload)):
            previous = target.get_status("巨龙振翅禁移动")
            already_active = isinstance(previous, WingbeatImmobilityStatus) and previous.activated
            if apply_piercing_status_effect(battle, actor, target, action_name=self.name,
                                            status=WingbeatImmobilityStatus(active_now=already_active), is_skill=True,
                                            tags={"skill", self.code, "pierce"}):
                battle.record_skill_effect(target)
                battle.log_public_event(f"{target.name} 下个本人轮不能普通移动。",
                                        source=actor, target=target)

    def ai_payload_value(self, battle: Battle, actor: Unit,
                         payload: dict[str, Any]) -> float | None:
        return _tyrant_area_score(self, battle, actor, payload, control=True)


def _chaos_ring(battle: Battle, body: list[Position]) -> list[Position]:
    return sorted((cell for cell in square_around_cells(battle, body, radius=1)
                   if cell not in body), key=lambda cell: (cell.y, cell.x))


def _chaos_position_value(battle: Battle, actor: Unit, destination: Position,
                          reachable: list[Position] | None = None) -> float:
    if reachable is None:
        reachable = battle.reachable_positions(actor, max_distance=4,
                                               exact_distance=4, ignore_units=True)
    if destination not in reachable:
        return -1000.0
    cells = _chaos_ring(battle, actor.footprint_cells_at(destination))
    score = -8.0
    for target in battle.effect_units_at_cells(cells, ignore=actor):
        if target.magic_immunity or target.direct_effects_blocked():
            continue
        attack = actor.stat("attack") + max(0, battle.unit_hit_count_for_cells(target, cells) - 1)
        damage = min(target.current_hp, max(0.0, battle.damage_rule.calculate_damage(
            attack, target.stat("defense"))))
        impact = damage * 115.0 + (8.0 if target.total_shields() == 0 else 0.0)
        score += impact if target.player_id != actor.player_id else -impact * 1.6
    enemies = [unit for unit in battle.enemy_units(actor.player_id) if _present(unit)]
    if enemies:
        before = min(battle.distance_between_units(actor, unit) for unit in enemies)
        after = min(cell.distance_to(enemy_cell)
                    for cell in actor.footprint_cells_at(destination)
                    for unit in enemies for enemy_cell in battle.unit_cells(unit))
        score += max(-3, min(3, before - after)) * 3.0
    return score


def _resolve_chaos_damage(battle: Battle, actor: Unit, action: Any) -> None:
    cells = battle.payload_positions(action.payload, "cells")
    for target in battle.effect_units_at_cells(cells, ignore=actor):
        battle.resolve_damage(DamageContext(
            source=actor, target=target, attack_power=actor.stat("attack"), is_skill=True,
            action_name="混沌吐息", area_cell_hits=battle.unit_hit_count_for_cells(target, cells),
            tags={"skill", "chaos_breath"}))


def _resolve_chaos_stage(battle: Battle, actor: Unit, action: Any) -> None:
    if _present(actor):
        _choose_chaos_stage(battle, actor, int(action.payload["stage"]),
                            int(action.payload["limit"]))


def _choose_chaos_stage(battle: Battle, actor: Unit, stage: int, limit: int) -> None:
    if not _present(actor):
        return
    destinations = sorted(battle.reachable_positions(actor, max_distance=4,
                                                      exact_distance=4, ignore_units=True),
                          key=lambda cell: (cell.y, cell.x))
    options = [f"{cell.x},{cell.y}" for cell in destinations]
    if not options:
        battle.log_public_event(f"{actor.name} 的混沌吐息没有合法的四格落点，第{stage}段结束。",
                                source=actor)
        return
    choice = battle.optional_position_choice(actor, options,
        action_name=f"混沌吐息第{stage}/{limit}段：移动四格并喷吐，或结束",
        kind="optional_placement")
    if choice not in options:
        return
    x, y = (int(part) for part in choice.split(",", 1))
    try:
        battle.move_unit(actor, Position(x, y), via_skill=True, ignore_units=True,
                         max_distance=4, exact_distance=4, tags={"chaos_breath"})
    except ActionError:
        battle.log_public_event(f"{actor.name} 的混沌吐息落格已失效。", source=actor)
        return
    cells = _chaos_ring(battle, battle.unit_cells(actor))
    battle.queue_skill_effect_action(
        actor=actor, display_name=f"混沌吐息第{stage}段", effect_code="chaos_breath_damage",
        effect_resolver=_resolve_chaos_damage, payload={"cells": positions_to_dict(cells),
                                                        "refresh_cell_targets": True},
        target_cells=cells, segment_index=stage, segment_count=limit)
    if stage < limit:
        battle.queue_skill_effect_action(
            actor=actor, display_name=f"混沌吐息第{stage+1}段选择",
            effect_code="chaos_breath_stage", effect_resolver=_resolve_chaos_stage,
            payload={"stage": stage + 1, "limit": limit}, target_cells=[], target_units=[],
            include_cell_units=False, hostile=False)


class ChaosBreathSkill(ManaPointCostSkill):
    is_displacement_skill = True
    requires_choice_replay_when_used = True
    ai_payload_value_owned = True
    ai_optional_choice_owned = True
    ai_enemy_impact_requirement = False

    def __init__(self) -> None:
        super().__init__("chaos_breath", "混沌吐息",
                         "每本人回合一次，费1魔力点；掷1d6，最多逐段四格移动并伤害身体周围单位。",
                         mana_point_cost=1, max_uses_per_turn=1, target_mode="none")

    def can_use(self, battle: Battle, actor: Unit,
                payload: dict[str, Any] | None = None) -> tuple[bool, str]:
        ok, reason = super().can_use(battle, actor, payload)
        if not ok:
            return ok, reason
        if actor.cannot_move or not battle.reachable_positions(actor, max_distance=4,
                                                                exact_distance=4, ignore_units=True):
            return False, "当前没有合法的四格移动落点。"
        return True, ""

    def execute(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> None:
        rolled = random.randint(1, 6)
        battle.log_public_event(f"{actor.name} 混沌吐息掷出 {rolled}，最多可移动喷吐 {rolled} 段。",
                                source=actor)
        _choose_chaos_stage(battle, actor, 1, rolled)

    def preview(self, battle: Battle, actor: Unit) -> dict[str, Any]:
        cells = battle.reachable_positions(actor, max_distance=4, exact_distance=4,
                                           ignore_units=True)
        return {"cells": positions_to_dict(cells), "target_unit_ids": [],
                "secondary_cells": [], "requires_target": False,
                "repeat_limit": "1d6", "movement_distance": 4}

    def ai_payload_value(self, battle: Battle, actor: Unit,
                         payload: dict[str, Any]) -> float | None:
        destinations = battle.reachable_positions(actor, max_distance=4,
                                                  exact_distance=4, ignore_units=True)
        return max((_chaos_position_value(battle, actor, cell, destinations) for cell in destinations),
                   default=-1000.0)

    def ai_priority_score(self, context: Any) -> float:
        return self.ai_payload_value(context.battle, context.actor, context.payload)

    def ai_direct_effect_score(self, battle: Battle, actor: Unit,
                               payload: dict[str, Any]) -> float:
        # Every 1d6 result permits the first placement. Later stages need
        # fresh choices, so only value the guaranteed first placement here.
        return self.ai_payload_value(battle, actor, payload)

    def ai_optional_choice_score(self, battle: Battle, actor: Unit,
                                 prompt: dict[str, Any], option: str) -> float | None:
        if prompt.get("kind") != "optional_placement" or not str(
                prompt.get("action_name") or "").startswith("混沌吐息第"):
            return None
        try:
            x, y = (int(part) for part in option.split(",", 1))
        except (TypeError, ValueError):
            return -1000.0
        return _chaos_position_value(battle, actor, Position(x, y))
