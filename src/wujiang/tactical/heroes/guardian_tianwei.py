"""R345: Shi Jin's ranged guardian and its control/formation skills."""

from __future__ import annotations

from itertools import combinations
from typing import Any

from wujiang.tactical.engine.core import ActionError, Battle, DamageContext, Position, QueuedAction, Skill, Stats, StatusEffect, Trait, Unit
from wujiang.tactical.heroes.base import AbstractHero
from wujiang.tactical.heroes.common import payload_direction, payload_target_unit
from wujiang.tactical.heroes.mechanics.guardian_spirit import GuardianTetherTrait, SummonGuardianSkill, named_guardians, present
from wujiang.tactical.heroes.mechanics.next_five_effects import apply_piercing_status_effect
from wujiang.tactical.heroes.mechanics.next_five_geometry import ALL_DIRECTIONS


class TianweiGuardian(AbstractHero):
    hero_code = "guardian_tianwei"
    hero_name = "守护灵。天微"
    role = "召唤物"
    attribute = "土"
    race = "守护灵"
    level = 1
    base_stats = Stats(attack=2, defense=999, speed=3, attack_range=5, mana=0)

    def __init__(self, player_id: int) -> None:
        super().__init__(player_id, is_summon=True)

    def build_skills(self) -> list[Skill]:
        return [FiveTigerPrisonSkill(), BeckoningHandSkill(), StarGatherSkill()]

    def build_traits(self) -> list[Trait]:
        return [GuardianTetherTrait()]


class SummonTianweiSkill(SummonGuardianSkill):
    def __init__(self) -> None:
        super().__init__("summon_guardian_tianwei", "守护灵。天微", TianweiGuardian)
        self.description = "每场一次；在本人周围召唤攻2、守无限、速3、范5的天微守护灵。"


def _hostile_targets(battle: Battle, actor: Unit) -> list[Unit]:
    return [unit for unit in battle.all_units() if present(unit) and unit.player_id != actor.player_id
            and battle.unit_target_in_range_and_line(actor, unit, actor.targeting_range())
            and battle.unit_can_be_selected(unit, actor=actor)[0]]


class FiveTigerPrisonStatus(StatusEffect):
    def __init__(self) -> None:
        super().__init__("五虎牢", "攻-1；普通和主动技能移动前需另付2魔。", duration=1)

    def modify_stat(self, stat_name: str, value: float) -> float:
        return value - 1 if stat_name == "attack" else value

    def on_before_normal_move(self, battle: Battle, ctx: Any) -> None:
        if self.owner is not ctx.unit:
            return
        if self.owner.current_mana < 2:
            raise ActionError("五虎牢：移动前还需支付2魔。")
        self.owner.current_mana = round(self.owner.current_mana - 2, 2)
        battle.log(f"{self.owner.name} 为突破五虎牢支付2魔。")

    def modify_skill_mana_cost(self, battle: Battle, actor: Unit, skill: Skill,
                               payload: dict[str, Any] | None, cost: float) -> float:
        return cost + 2 if actor is self.owner and skill.is_active and skill.moves_owner_on_use(battle, actor, payload) else cost


class FiveTigerPrisonSkill(Skill):
    ai_payload_value_owned = True
    ai_probe_hostile_effect = True

    def __init__(self) -> None:
        super().__init__("five_tiger_prison", "五虎牢", "每回合一次；破魔无伤命中，使目标一轮攻-1，主动移动前需付2魔。",
                         max_uses_per_turn=1, target_mode="enemy")

    def targets(self, battle: Battle, actor: Unit) -> list[Unit]:
        return _hostile_targets(battle, actor)

    def ai_hostile_target_impact(self, battle: Battle, actor: Unit,
                                 payload: dict[str, Any], target: Unit) -> bool | None:
        return (target in self.targets(battle, actor) and not target.magic_immunity
                and (not target.has_status("五虎牢") or target.total_shields() > 0))

    def ai_effective_enemy_impact(self, battle: Battle, actor: Unit, payload: dict[str, Any],
                                  targets: list[Unit] | None = None, shared: Any = None,
                                  action: dict[str, Any] | None = None) -> bool | None:
        if targets is None:
            return None
        return any(self.ai_hostile_target_impact(battle, actor, payload, target) for target in targets)

    def preview(self, battle: Battle, actor: Unit) -> dict[str, Any]:
        targets = self.targets(battle, actor)
        return {"cells": [cell.to_dict() for unit in targets for cell in battle.unit_cells(unit)],
                "target_unit_ids": [unit.unit_id for unit in targets], "secondary_cells": [], "requires_target": True}

    def get_target_units_for_payload(self, battle: Battle, actor: Unit,
                                     payload: dict[str, Any]) -> list[Unit]:
        return [payload_target_unit(battle, payload)]

    def can_use(self, battle: Battle, actor: Unit, payload: dict[str, Any] | None = None) -> tuple[bool, str]:
        ok, reason = super().can_use(battle, actor, payload)
        if not ok:
            return ok, reason
        if payload and payload.get("target_unit_id") and str(payload["target_unit_id"]) not in {
                unit.unit_id for unit in self.targets(battle, actor)}:
            return False, "五虎牢目标不在合法范内。"
        return (True, "") if self.targets(battle, actor) else (False, "没有可命中的敌方单位。")

    def execute(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> None:
        target = battle.units.get(str(payload.get("resolved_target_unit_id") or payload.get("target_unit_id") or ""))
        if target is None or target not in self.targets(battle, actor):
            battle.log("五虎牢未命中仍在合法范围的单位。")
            return
        if apply_piercing_status_effect(battle, actor, target, action_name=self.name,
                                        status=FiveTigerPrisonStatus(), is_skill=True,
                                        tags={"skill", self.code}):
            battle.log(f"{target.name} 被五虎牢所困，一轮内攻-1且移动前需支付2魔。")

    def ignores_shield_for_payload(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> bool:
        return True

    def ai_payload_value(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> float | None:
        target = battle.units.get(str(payload.get("target_unit_id") or ""))
        if target is None or target not in self.targets(battle, actor) or target.magic_immunity:
            return -100
        if target.has_status("五虎牢"):
            return -80
        return 30 + target.stat("attack") * 5 + (15 if target.current_mana < 2 else 0)

    def ai_priority_score(self, context: Any) -> float:
        return self.ai_payload_value(context.battle, context.actor, context.payload) or -100


def _beckoning_move(battle: Battle, actor: Unit, queued: QueuedAction) -> None:
    target = battle.units.get(str(queued.payload.get("reactor_id") or ""))
    if not present(target):
        return
    ctx = battle.validate_target(actor, target, action_name=queued.display_name, is_skill=True,
                                 is_hostile=target.player_id != actor.player_id, ignore_shield=True,
                                 resolve_defenses=False, ignore_targeting_restrictions=True,
                                 tags={"skill", "beckoning_hand"})
    if ctx.cancelled or target.skill_non_damage_effects_blocked():
        return
    if target.player_id != actor.player_id and target.total_shields() > 0:
        target.consume_one_shield()
        battle.log_public_event(f"{target.name} 的1层护盾被【招来手】位移击破。", source=actor, target=target)
    direction = (int(queued.payload["dx"]), int(queued.payload["dy"]))
    destination = target.position.offset(direction[0] * 3, direction[1] * 3)
    if battle.in_bounds(destination) and battle.can_place_unit(target, destination, mover=target):
        try:
            battle.move_unit(target, destination, via_skill=True, forced=True, straight_only=True,
                             max_distance=3, exact_distance=3, tags={"beckoning_hand"})
            battle.log(f"{target.name} 被招来手沿指定方向移动3格。")
            return
        except ActionError:
            pass
    battle.log(f"{target.name} 受到招来手的位移效果，但无法沿所选方向完整移动3格。")


class BeckoningHandSkill(Skill):
    ai_payload_value_owned = True

    def __init__(self) -> None:
        super().__init__("beckoning_hand", "招来手", "每回合一次；威力3伤。对此技能使用被动技能者，另受指定方向恰三格破魔位移。",
                         max_uses_per_turn=1, target_mode="enemy")

    def targets(self, battle: Battle, actor: Unit) -> list[Unit]:
        return _hostile_targets(battle, actor)

    def ai_effective_enemy_impact(self, battle: Battle, actor: Unit, payload: dict[str, Any],
                                  targets: list[Unit] | None = None, shared: Any = None,
                                  action: dict[str, Any] | None = None) -> bool | None:
        if targets is None:
            return None
        return any(target in self.targets(battle, actor) and not target.magic_immunity
                   for target in targets)

    def preview(self, battle: Battle, actor: Unit) -> dict[str, Any]:
        targets = self.targets(battle, actor)
        return {"cells": [cell.to_dict() for unit in targets for cell in battle.unit_cells(unit)],
                "target_unit_ids": [unit.unit_id for unit in targets], "secondary_cells": [], "requires_target": True,
                "selection": {"mode": "unit_direction", "directions": [
                    {"dx": dx, "dy": dy, "label": f"{dx:+d},{dy:+d}"} for dx, dy in ALL_DIRECTIONS]}}

    def get_target_units_for_payload(self, battle: Battle, actor: Unit,
                                     payload: dict[str, Any]) -> list[Unit]:
        return [payload_target_unit(battle, payload)]

    def can_use(self, battle: Battle, actor: Unit, payload: dict[str, Any] | None = None) -> tuple[bool, str]:
        ok, reason = super().can_use(battle, actor, payload)
        if not ok:
            return ok, reason
        if payload and payload.get("target_unit_id"):
            if str(payload["target_unit_id"]) not in {unit.unit_id for unit in self.targets(battle, actor)}:
                return False, "招来手目标不在合法范内。"
            try:
                if payload_direction(payload) not in ALL_DIRECTIONS:
                    return False, "请选择合法的位移方向。"
            except (KeyError, TypeError, ValueError):
                return False, "请选择位移方向。"
        return (True, "") if self.targets(battle, actor) else (False, "没有可命中的敌方单位。")

    def on_reaction_resolved(self, battle: Battle, declared_action: QueuedAction,
                             reaction: QueuedAction) -> None:
        reactor = battle.units.get(reaction.actor_id)
        if reactor is None or reactor.player_id == declared_action.source_player_id:
            return
        used = reactor.get_skill(str(reaction.payload.get("action_code") or ""))
        if used.timing not in {"passive", "reaction"}:
            return
        reacted = declared_action.payload.setdefault("beckoning_reactor_ids", [])
        if reactor.unit_id not in reacted:
            reacted.append(reactor.unit_id)

    def ai_payload_candidates(self, battle: Battle, actor: Unit,
                              action: dict[str, Any]) -> list[dict[str, Any]] | None:
        return [{"type": "skill", "unit_id": actor.unit_id, "skill_code": self.code,
                 "target_unit_id": target.unit_id, "direction": {"dx": dx, "dy": dy}}
                for target in self.targets(battle, actor) for dx, dy in ALL_DIRECTIONS]

    def execute(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> None:
        direction = payload_direction(payload)
        if direction not in ALL_DIRECTIONS:
            raise ActionError("招来手方向无效。")
        target = battle.units.get(str(payload.get("resolved_target_unit_id") or payload.get("target_unit_id") or ""))
        if target is not None and target in self.targets(battle, actor):
            battle.resolve_damage(DamageContext(source=actor, target=target, attack_power=3,
                                                is_skill=True, action_name=self.name,
                                                tags={"skill", self.code}))
        for reactor_id in dict.fromkeys(payload.get("beckoning_reactor_ids", [])):
            reactor = battle.units.get(str(reactor_id))
            if not present(reactor):
                continue
            battle.queue_skill_effect_action(actor=actor, display_name="招来手。被动位移", effect_code="beckoning_move",
                                             payload={"reactor_id": reactor_id, "dx": direction[0], "dy": direction[1]},
                                             target_units=[reactor], target_cells=battle.unit_cells(reactor),
                                             include_cell_units=False, effect_resolver=_beckoning_move)

    def ai_payload_value(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> float | None:
        target = battle.units.get(str(payload.get("target_unit_id") or ""))
        if target is None or target not in self.targets(battle, actor):
            return -100
        try:
            direction = payload_direction(payload)
        except (KeyError, TypeError, ValueError):
            return -100
        damage = (0 if target.total_shields() or target.magic_immunity else
                  min(target.current_hp, battle.damage_rule.calculate_damage(3, target.stat("defense"))))
        future = target.position.offset(*direction)
        summoner = battle.units.get(actor.summoner_id or "")
        safety = (future.distance_to(summoner.position) - target.position.distance_to(summoner.position)) * 3 if present(summoner) else 0
        return damage * 100 + safety + (8 if target.total_shields() else 0) - 20

    def ai_priority_score(self, context: Any) -> float:
        return self.ai_payload_value(context.battle, context.actor, context.payload) or -100


class StarGatherSkill(Skill):
    ai_payload_value_owned = True
    ai_enemy_impact_requirement = False
    is_displacement_skill = True

    def __init__(self) -> None:
        super().__init__("star_gather", "占星术。聚", "每场一次；选一至两名己方守护灵或召唤者，逐名瞬移到天微周围。",
                         max_uses_per_battle=1, target_mode="cell")

    def anchors(self, battle: Battle, actor: Unit) -> list[Unit]:
        anchors = named_guardians(battle, actor.player_id)
        summoner = battle.units.get(actor.summoner_id or "")
        if present(summoner) and summoner not in anchors:
            anchors.append(summoner)
        return anchors

    def moves_owner_on_use(self, battle: Battle, actor: Unit,
                           payload: dict[str, Any] | None) -> bool:
        return bool(payload and any(str(item.get("id") or "") == actor.unit_id
                                    for item in payload.get("placements", []) if isinstance(item, dict)))

    def candidates(self, battle: Battle, actor: Unit) -> list[tuple[Unit, list[Position]]]:
        if not present(actor):
            return []
        surroundings = {cell for body in battle.unit_cells(actor) for cell in battle.neighbors(body)}
        result = []
        summoner = battle.units.get(actor.summoner_id or "")
        for unit in self.anchors(battle, actor):
            cells = sorted((cell for cell in surroundings if cell != unit.position
                            and battle.can_place_unit(unit, cell, mover=unit)
                            and (unit is not actor or not present(summoner)
                                 or cell.distance_to(summoner.position) <= 6)), key=lambda cell: (cell.y, cell.x))
            if cells:
                result.append((unit, cells))
        return result

    def preview(self, battle: Battle, actor: Unit) -> dict[str, Any]:
        candidates = self.candidates(battle, actor)
        return {"cells": [cell.to_dict() for _, cells in candidates for cell in cells],
                "target_unit_ids": [unit.unit_id for unit, _ in candidates], "secondary_cells": [],
                "requires_target": True, "selection": {"mode": "unit_cells", "min_targets": 1,
                "max_targets": 2, "candidates": [{"id": unit.unit_id, "cells": [cell.to_dict() for cell in cells]}
                                                for unit, cells in candidates]}}

    def _placements(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> list[tuple[Unit, Position]]:
        raw = payload.get("placements")
        if not isinstance(raw, list) or not 1 <= len(raw) <= 2:
            raise ActionError("占星聚需选择一至两名单位及各自落格。")
        candidates = {unit.unit_id: set(cells) for unit, cells in self.candidates(battle, actor)}
        placements = []
        seen_ids, seen_cells = set(), set()
        for item in raw:
            if not isinstance(item, dict) or item.get("x") is None or item.get("y") is None:
                raise ActionError("占星聚落格格式无效。")
            unit_id = str(item.get("id") or "")
            cell = Position(int(item["x"]), int(item["y"]))
            if unit_id in seen_ids or cell in seen_cells or cell not in candidates.get(unit_id, set()):
                raise ActionError("占星聚只能选不同己方单位和互不冲突的合法落格。")
            seen_ids.add(unit_id)
            seen_cells.add(cell)
            placements.append((battle.get_unit(unit_id), cell))
        return placements

    def can_use(self, battle: Battle, actor: Unit, payload: dict[str, Any] | None = None) -> tuple[bool, str]:
        ok, reason = super().can_use(battle, actor, payload)
        if not ok:
            return ok, reason
        if payload and "placements" in payload:
            try:
                self._placements(battle, actor, payload)
            except (ActionError, KeyError, TypeError, ValueError) as exc:
                return False, str(exc)
        return (True, "") if self.candidates(battle, actor) else (False, "没有可聚拢的己方单位和合法落格。")

    def get_target_units_for_payload(self, battle: Battle, actor: Unit,
                                     payload: dict[str, Any]) -> list[Unit]:
        return [unit for unit, _ in self._placements(battle, actor, payload)]

    def get_target_cells_for_payload(self, battle: Battle, actor: Unit,
                                     payload: dict[str, Any]) -> list[Position]:
        return [cell for _, cell in self._placements(battle, actor, payload)]

    def execute(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> None:
        for unit, cell in self._placements(battle, actor, payload):
            if not present(unit) or not battle.can_place_unit(unit, cell, mover=unit):
                battle.log(f"{unit.name} 无法到达占星聚所选落格。")
                continue
            try:
                battle.move_unit(unit, cell, via_skill=True, allow_anywhere=True,
                                 tags={"star_gather"})
            except ActionError:
                battle.log(f"{unit.name} 的占星聚位移未能结算。")

    def ai_payload_candidates(self, battle: Battle, actor: Unit,
                              action: dict[str, Any]) -> list[dict[str, Any]] | None:
        placements = []
        for unit, cells in self.candidates(battle, actor):
            for cell in cells:
                placements.append({"id": unit.unit_id, "x": cell.x, "y": cell.y})
        base = {"type": "skill", "unit_id": actor.unit_id, "skill_code": self.code}
        singles = [{**base, "placements": [item]} for item in placements]
        pairs = [{**base, "placements": [first, second]} for first, second in combinations(placements, 2)
                 if first["id"] != second["id"] and (first["x"], first["y"]) != (second["x"], second["y"])]
        ranked = sorted([*singles, *pairs], key=lambda item: self.ai_payload_value(battle, actor, item) or -1000,
                        reverse=True)
        return ranked[:12]

    def ai_candidate_payloads(self, context: Any) -> list[dict[str, Any]]:
        return self.ai_payload_candidates(context.battle, context.actor, context.action) or []

    def ai_payload_value(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> float | None:
        try:
            placements = self._placements(battle, actor, payload)
        except (ActionError, KeyError, TypeError, ValueError):
            return -1000
        score = -25.0
        enemies = [unit for unit in battle.all_units() if present(unit) and unit.player_id != actor.player_id]
        for unit, cell in placements:
            before = min((unit.position.distance_to(enemy.position) for enemy in enemies), default=8)
            after = min((cell.distance_to(enemy.position) for enemy in enemies), default=8)
            score += max(0, before - after) * (7 if "守护灵" in unit.name else 3)
            score += max(0, 3 - after) * (3 if unit.stat("attack") >= 3 else -2)
            if unit is actor:
                summoner = battle.units.get(actor.summoner_id or "")
                if present(summoner):
                    score += max(0, actor.position.distance_to(summoner.position) - cell.distance_to(summoner.position)) * 10
        return score

    def ai_priority_score(self, context: Any) -> float:
        return self.ai_payload_value(context.battle, context.actor, context.payload) or -1000
