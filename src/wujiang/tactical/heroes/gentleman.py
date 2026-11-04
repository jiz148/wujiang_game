"""R325: path shaped movement, fixed path clones, and shared damage choices."""

from __future__ import annotations

from typing import Any

from wujiang.tactical.engine.core import (
    ActionError, Battle, DamageContext, HeroUnit, MoveContext, Position, Skill, Trait, Unit,
)
from wujiang.tactical.heroes.ai_policy import AISkillScoreContext
from wujiang.tactical.heroes.ai_candidate_plan import TargetDestinationCandidates
from wujiang.tactical.heroes.common import payload_position, payload_target_unit, positions_to_dict
from wujiang.tactical.heroes.hero_kit import FragmentMappedHeroKit, register_hero_kit
from wujiang.tactical.heroes.mechanics.clone_split import StandardCloneSummon, maybe_swap_new_clones


_DIRECTIONS = ((-1, -1), (0, -1), (1, -1), (-1, 0), (1, 0), (-1, 1), (0, 1), (1, 1))


def gentleman_crossings(battle: Battle, actor: Unit, path: list[Position]) -> list[tuple[Unit, Position]]:
    """Preserve the cell immediately before entering each enemy body."""
    events: list[tuple[Unit, Position]] = []
    for target, entered in battle.path_crossing_events(actor, path):
        if target.player_id == actor.player_id:
            continue
        for index, step in enumerate(path[1:], 1):
            if step == entered:
                events.append((target, path[index - 1]))
                break
    return events


class GentlemanSurpriseStepSkill(Skill):
    selection_cells_are_effect_cells = False

    def ai_priority_score(self, context: AISkillScoreContext) -> float | None:
        if context.actor.hero_code != "excel_r325":
            return None
        battle, actor, payload = context.battle, context.actor, context.payload
        if actor.position is None:
            return -1000.0
        try:
            route = self.route(battle, actor, payload)
        except ActionError:
            return -1000.0
        destination = route[-1]
        role = context.hero_style()
        score = (context.move_score(destination, role)
                 - context.move_score(actor.position, role))
        future_clones = set()
        for target, previous in gentleman_crossings(battle, actor, route):
            if previous in future_clones or previous == destination:
                continue
            future_clones.add(previous)
            occupied = any(unit is not actor and battle.unit_occupies(unit, previous)
                           for unit in battle.all_units())
            if occupied or (previous.x, previous.y) in battle.blocked_cells:
                continue
            score += 24.0 if target.player_id != actor.player_id else 0.0
            if target.position is not None and battle.distance_between_units(actor, target) <= 1:
                score += 7.0
        score += 15.0 if any(enemy.position is not None and
                            context.distance_to_position(enemy, destination) <= actor.targeting_range()
                            for enemy in context.hostile_units()) else 0.0
        return score - 7.0 if score > 9.0 else -1000.0

    def __init__(self) -> None:
        super().__init__("surprise_step", "突现", "每本人轮三次零魔；先一步，再沿左右垂线直行两步，可穿人并在穿敌前格造影。",
                         max_uses_per_turn=3, target_mode="cell")

    def routes(self, battle: Battle, actor: Unit) -> dict[Position, list[Position]]:
        if actor.position is None or actor.cannot_move:
            return {}
        routes: dict[Position, list[Position]] = {}
        for dx, dy in _DIRECTIONS:
            for side in (-1, 1):
                perpendicular = (side * -dy, side * dx)
                steps = [actor.position.offset(dx, dy),
                         actor.position.offset(dx + perpendicular[0], dy + perpendicular[1]),
                         actor.position.offset(dx + 2 * perpendicular[0], dy + 2 * perpendicular[1])]
                end = steps[-1]
                if not battle.can_place_unit(actor, end, ignore=actor, mover=actor):
                    continue
                try:
                    path = battle.explicit_path(actor, steps, max_distance=3, exact_distance=3,
                                                ignore_units=True)
                except ActionError:
                    continue
                routes[end] = path
        return routes

    def route(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> list[Position]:
        if payload.get("x") is not None and payload.get("y") is not None:
            destination = payload_position(payload)
        else:
            cells = payload.get("cells") or []
            if len(cells) != 1:
                raise ActionError("突现须选择唯一的折线终点。")
            destination = payload_position(cells[0])
        path = self.routes(battle, actor).get(destination)
        if path is None:
            raise ActionError("突现须先走一格，再沿垂线连续走两格并合法落地。")
        return path

    def can_use(self, battle: Battle, actor: Unit, payload: dict[str, Any] | None = None) -> tuple[bool, str]:
        ok, reason = super().can_use(battle, actor, payload)
        if not ok:
            return ok, reason
        if actor.cannot_move:
            return False, "当前不能位移。"
        if payload and (payload.get("x") is not None or payload.get("cells")):
            try:
                self.route(battle, actor, payload)
            except ActionError as exc:
                return False, str(exc)
        return (True, "") if self.routes(battle, actor) else (False, "没有合法的三格折线路径。")

    def execute(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> None:
        try:
            path = self.route(battle, actor, payload)
            battle.move_unit(actor, path[-1], via_skill=True, ignore_units=True, max_distance=3,
                             exact_distance=3, path=path[1:], tags={self.code, "movement"})
        except ActionError:
            battle.log_public_event(f"{actor.name} 的突现路径已失效。", source=actor)

    def preview(self, battle: Battle, actor: Unit) -> dict[str, Any]:
        routes = self.routes(battle, actor)
        return {
            "cells": positions_to_dict(routes), "target_unit_ids": [], "secondary_cells": [],
            "requires_target": True,
            "pattern_destinations": [
                {"pattern": [end.to_dict()], "destination_cells": positions_to_dict(path[1:]),
                 "clone_cells": positions_to_dict([cell for unit, cell in gentleman_crossings(battle, actor, path)
                                                  if battle.unit_can_be_selected(unit, actor=actor)[0]])}
                for end, path in routes.items()],
            "selection": {"mode": "pattern_cells", "ordered": False,
                          "patterns": [[end.to_dict()] for end in routes],
                          "cell_prompt": "选择折线终点；预览会显示实际路线和穿敌前的造影格。"},
        }

    def get_target_cells_for_payload(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> list[Position]:
        return self.route(battle, actor, payload)[1:]

    def get_target_units_for_payload(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> list[Unit]:
        return [unit for unit, _ in gentleman_crossings(battle, actor, self.route(battle, actor, payload))]


class GentlemanDuelSkill(TargetDestinationCandidates, Skill):
    ai_destination_fields = ("dest_x", "dest_y")
    requires_direct_unit_target_line = False

    def ai_priority_score(self, context: AISkillScoreContext) -> float | None:
        if context.actor.hero_code != "excel_r325":
            return None
        battle, actor, payload = context.battle, context.actor, context.payload
        try:
            target, destination = self.validate_choice(battle, actor, payload)
        except ActionError:
            return -1000.0
        if target.player_id == actor.player_id or target.magic_immunity or target.direct_effects_blocked():
            return -1000.0
        origin = actor.position
        if origin is None:
            return -1000.0
        role = context.hero_style()
        score = (context.move_score(destination, role)
                 - context.move_score(origin, role))
        if target.total_shields():
            score += 15.0
        if actor.attacks_used < actor.attack_actions_per_turn():
            score += 24.0 if context.distance_to_position(target, destination) <= actor.targeting_range() else 0.0
        return score - 12.0 if score > 17.0 else -1000.0

    def __init__(self) -> None:
        super().__init__("gentleman_duel", "绅士决斗", "两本人轮一次、范6、无伤破魔；命中后选择目标外邻合法格瞬移。",
                         cooldown_turns=2, target_mode="unit")

    def direct_unit_target_range(self, battle: Battle, actor: Unit, payload: dict[str, Any] | None = None) -> int:
        return 6

    def landing_cells(self, battle: Battle, actor: Unit, target: Unit) -> list[Position]:
        if actor.position is None or target.position is None or actor.cannot_move:
            return []
        body = set(battle.unit_cells(target))
        neighbors = {cell.offset(dx, dy) for cell in body for dx, dy in _DIRECTIONS} - body
        return sorted((cell for cell in neighbors
                       if battle.can_place_unit(actor, cell, ignore=actor, mover=actor)),
                      key=lambda cell: (cell.y, cell.x))

    def eligible_targets(self, battle: Battle, actor: Unit) -> list[Unit]:
        return [unit for unit in battle.all_units()
                if unit.unit_id != actor.unit_id and battle.unit_can_be_selected(unit, actor=actor)[0]
                and battle.unit_target_in_range_and_line(actor, unit, 6)
                and self.landing_cells(battle, actor, unit)]

    def validate_choice(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> tuple[Unit, Position]:
        target_id = payload.get("resolved_target_unit_id") or payload.get("target_unit_id")
        target = payload_target_unit(battle, {"target_unit_id": target_id})
        target = battle.effect_recipient(target)
        if target not in self.eligible_targets(battle, actor):
            raise ActionError("绅士决斗目标须在范6直线上且有合法贴身落格。")
        if payload.get("dest_x") is None or payload.get("dest_y") is None:
            raise ActionError("绅士决斗须选择目标旁的落格。")
        landing = payload_position(payload, "dest_x", "dest_y")
        if landing not in self.landing_cells(battle, actor, target):
            raise ActionError("绅士决斗的贴身落格已不合法。")
        return target, landing

    def can_use(self, battle: Battle, actor: Unit, payload: dict[str, Any] | None = None) -> tuple[bool, str]:
        ok, reason = super().can_use(battle, actor, payload)
        if not ok:
            return ok, reason
        if payload and payload.get("target_unit_id"):
            try:
                self.validate_choice(battle, actor, payload)
            except ActionError as exc:
                return False, str(exc)
        return (True, "") if self.eligible_targets(battle, actor) else (False, "范内没有可决斗并可贴身落位的单位。")

    def ignores_shield_for_payload(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> bool:
        return True

    def get_target_units_for_payload(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> list[Unit]:
        return [self.validate_choice(battle, actor, payload)[0]]

    def get_target_cells_for_payload(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> list[Position]:
        target, _ = self.validate_choice(battle, actor, payload)
        return battle.unit_cells(target)

    def preview(self, battle: Battle, actor: Unit) -> dict[str, Any]:
        targets = self.eligible_targets(battle, actor)
        return {
            "cells": positions_to_dict([cell for unit in targets for cell in battle.unit_cells(unit)]),
            "target_unit_ids": [unit.unit_id for unit in targets], "secondary_cells": [],
            "requires_target": True,
            "destinations_by_target": {unit.unit_id: positions_to_dict(self.landing_cells(battle, actor, unit))
                                       for unit in targets},
            "selection": {"mode": "unit_then_destination", "cell_prompt": "先选决斗单位，再选其周围的合法落格。"},
        }

    def execute(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> None:
        try:
            target, landing = self.validate_choice(battle, actor, payload)
        except ActionError:
            battle.log_public_event(f"{actor.name} 的绅士决斗目标或落格已失效。", source=actor)
            return
        result = battle.validate_target(actor, target, action_name=self.name, is_skill=True,
                                        is_hostile=target.player_id != actor.player_id,
                                        ignore_shield=True, damage_target=False,
                                        tags={"skill", self.code})
        if result.cancelled or not actor.alive or actor.banished or actor.position is None:
            return
        if target.total_shields() > 0:
            target.consume_one_shield()
            battle.record_shield_break_summary(actor, target, self.name)
            battle.log_public_event(f"{target.name} 的一层护盾被绅士决斗贯穿并打碎。", source=actor, target=target)
        if landing == actor.position:
            battle.log_public_event(f"{actor.name} 决斗命中后保持贴身位置。", source=actor, target=target)
            return
        if battle.can_place_unit(actor, landing, ignore=actor, mover=actor):
            try:
                battle.move_unit(actor, landing, via_skill=True, allow_anywhere=True,
                                 tags={self.code, "movement"})
            except ActionError:
                battle.log_public_event(f"{actor.name} 的绅士决斗贴身落格已失效。", source=actor)


class GentlemanCloneGuardTrait(Trait):
    def __init__(self, root_id: str) -> None:
        super().__init__("绅士分身免伤", "与真身共享每敌方武将回合两次可选免伤。")
        self.root_id = root_id

    def on_final_damage(self, battle: Battle, ctx: DamageContext) -> None:
        root = battle.units.get(self.root_id)
        if root is None or not root.alive or ctx.target is not self.owner:
            return
        guard = next((trait for trait in root.traits if isinstance(trait, GentlemanGuardTrait)), None)
        if guard is not None:
            guard.maybe_cancel(battle, ctx)


class GentlemanGuardTrait(Trait):
    def __init__(self) -> None:
        super().__init__("绅士防身", "每敌方武将回合本体和分身共用两次可选免伤；本体只受近身伤害。")
        self.choice_turn = -1
        self.used = 0

    @property
    def requires_damage_choice(self) -> bool:
        owner = self.owner
        battle_ref = getattr(owner, "_battle_ref", None) if owner is not None else None
        battle = battle_ref() if callable(battle_ref) else None
        return bool(battle is not None and self.remaining(battle) > 0)

    def remaining(self, battle: Battle) -> int:
        active = battle.current_turn_unit()
        if active is None or self.owner is None or active.player_id == self.owner.player_id:
            return 0
        if self.choice_turn != battle.turn_number:
            return 2
        return max(0, 2 - self.used)

    def on_before_damage(self, battle: Battle, ctx: DamageContext) -> None:
        owner = self.owner
        if owner is None or ctx.target is not owner or ctx.cancelled:
            return
        if (ctx.source is None or ctx.source.position is None or owner.position is None
                or battle.distance_between_units(ctx.source, owner) > 1):
            ctx.cancelled = True
            ctx.preserve_followup_effects = True
            ctx.reason = f"{owner.name} 不承受非近身生命伤害。"

    def on_final_damage(self, battle: Battle, ctx: DamageContext) -> None:
        if ctx.target is self.owner:
            self.maybe_cancel(battle, ctx)

    def maybe_cancel(self, battle: Battle, ctx: DamageContext) -> None:
        owner = self.owner
        if owner is None or not owner.alive or ctx.cancelled or self.remaining(battle) <= 0:
            return
        amount = ctx.raw_damage if ctx.raw_damage is not None else battle.damage_rule.calculate_damage(
            ctx.attack_power, ctx.target.stat("defense"))
        if not ctx.target.is_clone:
            if ctx.target.damage_fraction_after_limits(amount) <= 0:
                return
        decision = battle.optional_position_choice(owner, ["protect"],
                                                   action_name=f"绅士防身：{ctx.target.name}", kind="gentleman_guard",
                                                   damage=max(0.0, ctx.target.damage_fraction_after_limits(amount)),
                                                   protected_unit_id=ctx.target.unit_id)
        if decision != "protect":
            return
        if self.choice_turn != battle.turn_number:
            self.choice_turn, self.used = battle.turn_number, 0
        self.used += 1
        ctx.cancelled = True
        ctx.reason = f"{owner.name} 消耗一次绅士免伤，抵消了【{ctx.action_name}】的伤害。"

    def to_public_dict(self, battle: Battle) -> dict[str, Any]:
        data = super().to_public_dict(battle)
        data["remaining_enemy_turn_guards"] = self.remaining(battle)
        return data


class GentlemanCrossingCloneTrait(Trait):
    def __init__(self) -> None:
        super().__init__("穿人造影", "穿过敌方单位后，在进入其身体前的合法空格召唤一个分身，并随机决定是否与新分身换位。")

    def on_unit_moved(self, battle: Battle, ctx: MoveContext) -> None:
        owner = self.owner
        if (owner is None or ctx.unit is not owner or owner.is_clone or not owner.alive
                or owner.banished or owner.position is None or len(ctx.path) < 2):
            return
        for target, previous in gentleman_crossings(battle, owner, ctx.path):
            if not target.alive or target.banished:
                continue
            clone = StandardCloneSummon(owner.player_id, owner)
            clone.traits.append(GentlemanCloneGuardTrait(owner.unit_id).bind(clone))
            if not battle.can_place_unit(clone, previous):
                battle.log_public_event(f"{owner.name} 穿过敌人，但前一格不能造影。", source=owner)
                continue
            battle.summon_unit(clone, previous, summoner=owner)
            exchanged = maybe_swap_new_clones(owner, [clone])
            battle.log_public_event(f"{owner.name} 穿过敌方单位并留下分身"
                                    + ("，与分身换位。" if exchanged else "，本体留在原位。"),
                                    source=owner, target=clone)


GENTLEMAN_KIT = register_hero_kit(FragmentMappedHeroKit(
    "excel_r325",
    skills={"突现": GentlemanSurpriseStepSkill, "绅士决斗": GentlemanDuelSkill},
    traits={
        "穿对方单位后在穿人的前一格召唤一个分身": GentlemanCrossingCloneTrait,
        "每个对方回合可以使2次对此单位或此单位分身的伤害不计，被此效果影响后分身不会破坏": GentlemanGuardTrait,
    },
))
