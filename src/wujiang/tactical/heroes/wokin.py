"""R051 巨大的战士。窝金：四格身体、反弹、直线追击和三件大招。"""

from __future__ import annotations

from typing import Any

from wujiang.tactical.engine.core import (
    ActionError, Battle, DamageContext, HealContext, Position, Skill, Trait, Unit,
)
from wujiang.tactical.heroes.common import (
    match_payload_pattern, pattern_selection_preview, pattern_signature, positions_to_dict,
)
from wujiang.tactical.heroes.mechanics.next_five_geometry import (
    ALL_DIRECTIONS, square_around_cells,
)


_ULTIMATE_CODES = frozenset({"breath_cannon", "shockwave", "hundred_fists"})


def _present(unit: Unit) -> bool:
    return unit.alive and not unit.banished and unit.position is not None


class WokinMeleeRebound(Trait):
    def __init__(self) -> None:
        super().__init__("近身弹开", "外邻单位近身普攻或用技能针对本人时，被向外弹开1格。")

    def on_target_action_declared(self, battle: Battle, actor: Unit,
                                  action_type: str, payload: dict[str, Any]) -> None:
        owner = self.owner
        if (owner is None or actor is owner or not _present(owner) or not _present(actor)
                or action_type not in {"attack", "skill"}
                or battle.distance_between_units(owner, actor) > 1
                or payload.get("wokin_rebound_done")):
            return
        payload["wokin_rebound_done"] = True
        current_distance = battle.distance_between_units(owner, actor)
        candidates = []
        for cell in battle.neighbors(actor.position):
            if not battle.can_place_unit(actor, cell, ignore=actor, mover=actor):
                continue
            future_body = battle.unit_cells_at(actor, cell)
            future_distance = min(a.distance_to(b) for a in battle.unit_cells(owner)
                                  for b in future_body)
            if future_distance > current_distance:
                candidates.append((future_distance, cell))
        for _, destination in sorted(candidates, key=lambda row: (-row[0], row[1].y, row[1].x)):
            try:
                battle.move_unit(actor, destination, forced=True, max_distance=1,
                                 tags={"wokin_rebound"})
            except ActionError:
                continue
            battle.log_public_event(f"{actor.name} 近身针对 {owner.name}，被弹开1格。",
                                    source=owner, target=actor)
            break


class WokinStraightPursuit(Trait):
    bonus = 2

    def __init__(self) -> None:
        super().__init__("直线追击", "普通直线移动靠近敌方单位时，该次可多移动2格。")

    def modify_normal_move_distance(self, value: int) -> int:
        return value + self.bonus

    def _base_distance(self, actor: Unit) -> int:
        return max(0, actor.normal_move_distance() - self.bonus)

    def _within_base(self, battle: Battle, actor: Unit, destination: Position) -> bool:
        try:
            battle.find_path(actor, destination, max_distance=self._base_distance(actor),
                             use_movement_cost=True)
        except ActionError:
            return False
        return True

    def _approaches_an_enemy(self, battle: Battle, actor: Unit,
                             destination: Position) -> bool:
        if actor.position is None:
            return False
        before_body = battle.unit_cells(actor)
        after_body = battle.unit_cells_at(actor, destination)
        for enemy in battle.enemy_units(actor.player_id):
            if not _present(enemy):
                continue
            enemy_body = battle.unit_cells(enemy)
            before = min(a.distance_to(b) for a in before_body for b in enemy_body)
            after = min(a.distance_to(b) for a in after_body for b in enemy_body)
            if after < before:
                return True
        return False

    def normal_move_bonus_path(self, battle: Battle, actor: Unit,
                               destination: Position) -> list[Position] | None:
        if actor is not self.owner or actor.position is None or self._within_base(battle, actor, destination):
            return None
        dx, dy = destination.x - actor.position.x, destination.y - actor.position.y
        if not (dx == 0 or dy == 0 or abs(dx) == abs(dy)):
            return None
        if not self._approaches_an_enemy(battle, actor, destination):
            return None
        try:
            return battle.find_path(actor, destination,
                                    max_distance=actor.remaining_normal_move_distance(battle),
                                    straight_only=True, use_movement_cost=True)
        except ActionError:
            return None

    def normal_move_destination_allowed(self, battle: Battle, actor: Unit,
                                        destination: Position) -> bool:
        return (self._within_base(battle, actor, destination)
                or self.normal_move_bonus_path(battle, actor, destination) is not None)

    def normal_move_path_allowed(self, battle: Battle, actor: Unit,
                                 path: list[Position]) -> bool:
        if len(path) < 2:
            return True
        cost = sum(battle.normal_movement_step_cost(actor, before, after)
                   for before, after in zip(path, path[1:]))
        if cost <= self._base_distance(actor):
            return True
        return self.normal_move_bonus_path(battle, actor, path[-1]) == path


class WokinUltimateReset(Trait):
    def __init__(self) -> None:
        super().__init__("低血重置大招", "血首次降到3/4或以下时重置全部大招；回到高于3/4后可再次触发。")
        self.armed = True

    def on_after_damage(self, battle: Battle, ctx: DamageContext) -> None:
        owner = self.owner
        if (owner is None or ctx.target is not owner or ctx.actual_damage <= 0
                or not self.armed or not owner.alive):
            return
        before = owner.current_hp + ctx.actual_damage
        threshold = owner.max_health * 0.75
        if before > threshold >= owner.current_hp:
            self.armed = False
            reset = []
            for skill in owner.skills:
                if skill.code in _ULTIMATE_CODES and skill.uses_this_battle > 0:
                    skill.uses_this_battle = 0
                    skill.cooldown_remaining = 0
                    reset.append(skill.name)
            battle.log_public_event(
                f"{owner.name} 血降至3/4以下，重置大招" +
                (f"：{'、'.join(reset)}。" if reset else "（均尚未使用）。"),
                source=owner, target=owner,
            )

    def on_after_heal(self, battle: Battle, ctx: HealContext) -> None:
        if ctx.target is self.owner and ctx.actual_heal > 0 and ctx.target.current_hp > ctx.target.max_health * 0.75:
            self.armed = True


class WokinAreaUltimate(Skill):
    is_ultimate = True
    ai_payload_value_owned = True
    ai_enemy_impact_requirement = False

    def patterns(self, battle: Battle, actor: Unit) -> list[list[Position]]:
        raise NotImplementedError

    def chosen(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> list[Position]:
        declared = battle.payload_positions(payload, f"declared_{self.code}_cells")
        return declared or match_payload_pattern(payload, self.patterns(battle, actor))

    def preview(self, battle: Battle, actor: Unit) -> dict[str, Any]:
        preview = pattern_selection_preview(self.patterns(battle, actor))
        preview.update({"target_unit_ids": [], "secondary_cells": [], "requires_target": True})
        return preview

    def get_target_cells_for_payload(self, battle: Battle, actor: Unit,
                                     payload: dict[str, Any]) -> list[Position]:
        return self.chosen(battle, actor, payload)

    def get_target_units_for_payload(self, battle: Battle, actor: Unit,
                                     payload: dict[str, Any]) -> list[Unit]:
        return list(battle.effect_units_at_cells(self.chosen(battle, actor, payload), ignore=actor))

    def queued_payload_metadata(self, battle: Battle, actor: Unit,
                                payload: dict[str, Any]) -> dict[str, Any]:
        return {f"declared_{self.code}_cells": positions_to_dict(self.chosen(battle, actor, payload))}

    def execute(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> None:
        cells = self.chosen(battle, actor, payload)
        for target in battle.effect_units_at_cells(cells, ignore=actor):
            battle.resolve_damage(DamageContext(
                source=actor, target=target, attack_power=actor.stat("attack"),
                is_skill=True, action_name=self.name,
                area_cell_hits=battle.unit_hit_count_for_cells(target, cells),
                tags={"skill", "attack", self.code},
            ))

    def ai_payload_candidates(self, battle: Battle, actor: Unit,
                              action: dict[str, Any]) -> list[dict[str, Any]]:
        return [{"type": "skill", "unit_id": actor.unit_id, "skill_code": self.code,
                 "cells": positions_to_dict(cells)} for cells in self.patterns(battle, actor)]

    def ai_candidate_payloads(self, context: Any) -> list[dict[str, Any]]:
        return self.ai_payload_candidates(context.battle, context.actor, context.action)

    def ai_payload_value(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> float:
        try:
            cells = self.chosen(battle, actor, payload)
        except ActionError:
            return -1000.0
        score = 0.0
        has_enemy = False
        for target in battle.effect_units_at_cells(cells, ignore=actor):
            if target.magic_immunity:
                continue
            if target.total_shields() > 0:
                impact = 25.0
            else:
                power = actor.stat("attack") + max(0, battle.unit_hit_count_for_cells(target, cells) - 1)
                damage = min(target.current_hp, battle.damage_rule.calculate_damage(
                    power, target.stat("defense")))
                impact = damage * 100.0 + (70.0 if damage >= target.current_hp and damage > 0 else 0.0)
            if target.player_id == actor.player_id:
                score -= impact * 1.5
            else:
                has_enemy |= impact > 0
                score += impact
        return score if has_enemy and score > 0 else -1000.0

    def ai_effective_enemy_impact(self, battle: Battle, actor: Unit,
                                  payload: dict[str, Any], targets: list[Unit] | None = None,
                                  shared: Any = None, action: dict[str, Any] | None = None) -> bool:
        return self.ai_payload_value(battle, actor, payload) > 0

    def ai_priority_score(self, context: Any) -> float:
        return self.ai_payload_value(context.battle, context.actor, context.payload)


class BreathCannonSkill(WokinAreaUltimate):
    def __init__(self) -> None:
        super().__init__("breath_cannon", "气息炮", "本场一次；选择从身体边缘出发的六格直线，按攻伤双方。",
                         max_uses_per_battle=1, target_mode="cell")

    def patterns(self, battle: Battle, actor: Unit) -> list[list[Position]]:
        body = set(battle.unit_cells(actor))
        found: dict[tuple[tuple[int, int], ...], list[Position]] = {}
        for origin in body:
            for dx, dy in ALL_DIRECTIONS:
                if origin.offset(dx, dy) in body:
                    continue
                cells = battle.line_positions(origin, (dx, dy), 6)
                if cells:
                    found[pattern_signature(cells)] = cells
        return list(found.values())


class ShockwaveSkill(WokinAreaUltimate):
    def __init__(self) -> None:
        super().__init__("shockwave", "震波", "本场一次；身体外两圈所有单位受到一次按攻计算的技能伤害。",
                         max_uses_per_battle=1, target_mode="cell")

    def patterns(self, battle: Battle, actor: Unit) -> list[list[Position]]:
        body = set(battle.unit_cells(actor))
        cells = [cell for cell in square_around_cells(battle, body, 2) if cell not in body]
        return [cells] if cells else []


class HundredFistsSkill(WokinAreaUltimate):
    def __init__(self) -> None:
        super().__init__("hundred_fists", "百裂拳",
                         "本场一次；选择近身一个方向连续攻击3次，第三次半破魔。",
                         max_uses_per_battle=1, target_mode="cell")

    def patterns(self, battle: Battle, actor: Unit) -> list[list[Position]]:
        body = set(battle.unit_cells(actor))
        return [[cell] for cell in square_around_cells(battle, body, 1) if cell not in body]

    def execute(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> None:
        cells = self.chosen(battle, actor, payload)
        for index in range(3):
            for target in battle.effect_units_at_cells(cells, ignore=actor):
                if not target.alive:
                    continue
                battle.resolve_damage(DamageContext(
                    source=actor, target=target, attack_power=actor.stat("attack"),
                    is_skill=True, action_name=f"百裂拳第{index + 1}击",
                    half_ignore_shield=index == 2, area_cell_hits=1,
                    tags={"skill", "attack", "hundred_fists"},
                ))

    def ai_payload_value(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> float:
        try:
            cells = self.chosen(battle, actor, payload)
        except ActionError:
            return -1000.0
        score = 0.0
        useful_enemy = False
        for target in battle.effect_units_at_cells(cells, ignore=actor):
            if target.magic_immunity:
                continue
            shields = target.total_shields()
            hp = target.current_hp
            lost_shields = 0
            for index in range(3):
                power = actor.stat("attack")
                if shields > 0:
                    shields -= 1
                    lost_shields += 1
                    if index < 2:
                        continue
                    power = max(0.0, power - 1)
                hp -= min(hp, battle.damage_rule.calculate_damage(power, target.stat("defense")))
                if hp <= 0:
                    break
            impact = (target.current_hp - hp) * 100.0 + lost_shields * 20.0
            if hp <= 0 and target.current_hp > 0:
                impact += 70.0
            if target.player_id == actor.player_id:
                score -= impact * 1.5
            else:
                score += impact
                useful_enemy |= impact > 0
        return score if useful_enemy and score > 0 else -1000.0
