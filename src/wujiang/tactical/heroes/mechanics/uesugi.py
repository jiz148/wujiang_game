"""R215 Uesugi flash and end-turn dash."""

from __future__ import annotations

from wujiang.tactical.heroes.ai_candidate_plan import RANKED_CANDIDATES

from typing import Any
from wujiang.tactical.engine.core import ActionError
from wujiang.tactical.engine.core import Battle
from wujiang.tactical.engine.core import DamageContext
from wujiang.tactical.engine.core import Position
from wujiang.tactical.engine.core import Skill
from wujiang.tactical.engine.core import Trait
from wujiang.tactical.engine.core import Unit
from wujiang.tactical.heroes.ai_policy import AISkillScoreContext
from wujiang.tactical.heroes.common import match_payload_pattern
from wujiang.tactical.heroes.common import pattern_selection_preview
from wujiang.tactical.heroes.common import positions_to_dict
from wujiang.tactical.heroes.mechanics.next_five_geometry import ALL_DIRECTIONS


def _text(value: Any) -> str:
    return str(value or "").strip()


class UesugiFlashSkill(Skill):
    ai_candidate_plan = RANKED_CANDIDATES
    def ai_priority_score(self, context: AISkillScoreContext) -> float:
        return context.bird_skill_score()

    def __init__(self) -> None:
        super().__init__("flash_slash", "一闪",
                         "0魔、3轮一次；八方向前5格破魔伤害，随后移到合法第6格。",
                         cooldown_turns=3, target_mode="cell")

    def routes(self, battle: Battle, actor: Unit) -> list[tuple[list[Position], Position]]:
        if actor.position is None:
            return []
        routes: list[tuple[list[Position], Position]] = []
        for dx, dy in ALL_DIRECTIONS:
            path = [actor.position.offset(dx * step, dy * step) for step in range(7)]
            if not all(battle.in_bounds(cell) for cell in path[1:]):
                continue
            try:
                battle.explicit_path(actor, path[1:], max_distance=6, exact_distance=6,
                                     straight_only=True, ignore_units=True)
            except ActionError:
                continue
            routes.append((path[1:6], path[6]))
        return routes

    def selected(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> tuple[list[Position], Position]:
        if payload.get("queued_resolution") and payload.get("flash_cells"):
            return ([Position(**cell) for cell in payload["flash_cells"]],
                    Position(**payload["flash_destination"]))
        cells = match_payload_pattern(payload, [line for line, _ in self.routes(battle, actor)])
        for line, destination in self.routes(battle, actor):
            if line == cells:
                return line, destination
        raise ActionError("一闪需要完整五格伤害线和合法第六格。")

    def can_use(self, battle: Battle, actor: Unit, payload: dict[str, Any] | None = None) -> tuple[bool, str]:
        ok, reason = super().can_use(battle, actor, payload)
        if not ok:
            return ok, reason
        if payload and payload.get("cells"):
            try:
                self.selected(battle, actor, payload)
            except (ActionError, KeyError, TypeError, ValueError) as exc:
                return False, str(exc)
        return (True, "") if self.routes(battle, actor) else (False, "没有完整合法的一闪路线。")

    def queued_payload_metadata(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> dict[str, Any]:
        cells, destination = self.selected(battle, actor, payload)
        return {"flash_cells": positions_to_dict(cells), "flash_destination": destination.to_dict(),
                "flash_origin": actor.position.to_dict() if actor.position is not None else None}

    def get_target_cells_for_payload(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> list[Position]:
        return self.selected(battle, actor, payload)[0]

    def get_target_units_for_payload(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> list[Unit]:
        return battle.effect_units_at_cells(self.get_target_cells_for_payload(battle, actor, payload), ignore=actor)

    def ignores_shield_for_payload(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> bool:
        return True

    def execute(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> None:
        cells, destination = self.selected(battle, actor, payload)
        for target in battle.effect_units_at_cells(cells, ignore=actor):
            if not actor.alive or actor.banished:
                break
            battle.resolve_damage(DamageContext(
                source=actor, target=target, attack_power=actor.stat("attack"),
                is_skill=True, action_name=self.name, ignore_shield=True,
                area_cell_hits=battle.unit_hit_count_for_cells(target, cells),
                tags={"skill", self.code},
            ))
        if not actor.alive or actor.banished or actor.position is None:
            return
        origin = Position(**payload["flash_origin"]) if payload.get("flash_origin") else actor.position
        if actor.position != origin:
            battle.log_public_event(f"{actor.name} 已离开一闪原位，第六格位移不再执行。", source=actor)
            return
        dx = (destination.x - origin.x) // 6
        dy = (destination.y - origin.y) // 6
        steps = [origin.offset(dx * step, dy * step) for step in range(1, 7)]
        try:
            battle.move_unit(actor, destination, via_skill=True, forced=True, ignore_units=True,
                             path=steps, max_distance=6, exact_distance=6, straight_only=True,
                             tags={self.code, f"source:{actor.unit_id}"})
        except ActionError:
            battle.log_public_event(f"{actor.name} 的一闪第六格已被占，保留已结算伤害。", source=actor)

    def preview(self, battle: Battle, actor: Unit) -> dict[str, Any]:
        routes = self.routes(battle, actor)
        result = pattern_selection_preview([line for line, _ in routes])
        result.update({
            "target_unit_ids": list(dict.fromkeys(
                unit.unit_id for line, _ in routes
                for unit in battle.effect_units_at_cells(line, ignore=actor)
                if battle.unit_can_be_selected(unit, actor=actor)[0])),
            "secondary_cells": positions_to_dict([destination for _, destination in routes]),
            "requires_target": True,
        })
        return result


class UesugiEndDashTrait(Trait):
    requires_damage_choice = True

    def __init__(self) -> None:
        super().__init__("轮末穿行", "每本人轮末可直线穿人移动恰好6格；每次穿过各造成当前攻-1特性伤害。")

    def routes(self, battle: Battle, owner: Unit) -> dict[str, list[Position]]:
        if owner.position is None:
            return {}
        routes: dict[str, list[Position]] = {}
        for dx, dy in ALL_DIRECTIONS:
            path = [owner.position.offset(dx * step, dy * step) for step in range(7)]
            try:
                battle.explicit_path(owner, path[1:], max_distance=6, exact_distance=6,
                                     straight_only=True, ignore_units=True)
            except ActionError:
                continue
            routes[f"{path[-1].x},{path[-1].y}"] = path
        return routes

    def on_before_owner_turn_end(self, battle: Battle) -> bool:
        owner = self.owner
        if owner is None or not owner.alive or owner.banished or owner.position is None or owner.cannot_move:
            return False
        routes = self.routes(battle, owner)
        choice = battle.optional_position_choice(owner, list(routes), action_name="军神轮末穿行",
                                                 kind="end_dash")
        if choice not in routes:
            return False
        path = routes[choice]
        try:
            ctx = battle.move_unit(owner, path[-1], forced=True, ignore_units=True, path=path[1:],
                                   max_distance=6, exact_distance=6, straight_only=True,
                                   tags={"uesugi_end_dash", f"source:{owner.unit_id}"})
        except ActionError:
            return False
        if not owner.alive or owner.banished:
            return True
        crossings = battle.path_crossing_events(owner, ctx.path)
        for index, (crossed_unit, cell) in enumerate(crossings, start=1):
            battle.queue_skill_effect_action(
                actor=owner, display_name="轮末穿人伤害", effect_code="pass_through_damage",
                payload={"cells": [cell.to_dict()], "attack_power": max(0.0, owner.stat("attack") - 1),
                         "pass_through_damage": True, "crossed_unit_id": crossed_unit.unit_id},
                target_units=[crossed_unit], target_cells=[cell], include_cell_units=False,
                segment_index=index, segment_count=len(crossings))
        battle.log_public_event(f"{owner.name} 轮末穿行至 ({path[-1].x},{path[-1].y})。", source=owner)
        return True


