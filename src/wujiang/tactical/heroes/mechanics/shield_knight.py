"""R259 shield knight charge and giant-shield passing behavior."""

from __future__ import annotations

from typing import Any

from wujiang.tactical.engine.core import (
    ActionError, Battle, DamageContext, HealContext, MoveContext, Position,
    Skill, StatusEffect, Trait, Unit,
)
from wujiang.tactical.heroes.common import (
    pattern_selection_preview, payload_position, positions_to_dict,
)
from wujiang.tactical.heroes.next_five import ALL_DIRECTIONS

class ShieldKnightChargeSkill(Skill):
    selection_cells_are_effect_cells = False

    def __init__(self) -> None:
        super().__init__("shield_charge", "冲锋", "每本人轮一次；沿八方向恰好冲八格到第九格，途中双方各受一次当前攻的破魔伤害。",
                         max_uses_per_turn=1, target_mode="cell")

    def routes(self, battle: Battle, actor: Unit) -> dict[Position, list[Position]]:
        if actor.position is None or actor.cannot_move:
            return {}
        routes: dict[Position, list[Position]] = {}
        for dx, dy in ALL_DIRECTIONS:
            steps = [actor.position.offset(dx * step, dy * step) for step in range(1, 9)]
            if not battle.in_bounds(steps[-1]) or not battle.can_place_unit(actor, steps[-1], ignore=actor, mover=actor):
                continue
            try:
                path = battle.explicit_path(actor, steps, max_distance=8, exact_distance=8,
                                            straight_only=True, ignore_units=True)
            except ActionError:
                continue
            routes[steps[-1]] = path
        return routes

    def route(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> list[Position]:
        if payload.get("x") is not None and payload.get("y") is not None:
            destination = payload_position(payload)
        else:
            selected = payload.get("cells") or []
            if len(selected) != 1:
                raise ActionError("冲锋需选择唯一的第九格落点。")
            destination = payload_position(selected[0])
        path = self.routes(battle, actor).get(destination)
        if path is None:
            raise ActionError("冲锋必须沿八方向完整前进八格，并落在合法的第九格。")
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
        return True, ""

    def execute(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> None:
        path = self.route(battle, actor, payload)
        targets = battle.path_crossing_units(actor, path)
        battle.move_unit(actor, path[-1], via_skill=True, ignore_units=True,
                         straight_only=True, max_distance=8, exact_distance=8,
                         path=path[1:], tags={self.code})
        for target in targets:
            if target.alive and not target.banished:
                battle.resolve_damage(DamageContext(source=actor, target=target,
                    attack_power=actor.stat("attack"), is_skill=True, ignore_shield=True,
                    action_name=self.name, tags={"skill", "attack", "pierce", self.code}))

    def preview(self, battle: Battle, actor: Unit) -> dict[str, Any]:
        routes = self.routes(battle, actor)
        preview = pattern_selection_preview([[cell] for cell in routes])
        preview.update({"target_unit_ids": [], "secondary_cells": [], "requires_target": True,
                        "pattern_destinations": [{"pattern": [end.to_dict()],
                                                  "destination_cells": positions_to_dict(path[1:])}
                                                 for end, path in routes.items()],
                        "selection": {"mode": "pattern_cells", "ordered": False,
                                      "patterns": [[end.to_dict()] for end in routes],
                                      "cell_prompt": "选择合法第九格落点；路径显示经过单位。"}})
        return preview

    def get_target_cells_for_payload(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> list[Position]:
        return self.route(battle, actor, payload)[1:]

    def get_target_units_for_payload(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> list[Unit]:
        return battle.path_crossing_units(actor, self.route(battle, actor, payload))

    def ignores_shield_for_payload(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> bool:
        return True


class ShieldKnightSpeedStatus(StatusEffect):
    def __init__(self, grant_turn: int) -> None:
        super().__init__("巨盾冲锋提速", "经过巨盾时速+1，持续到该单位下一实际武将回合结束。")
        self.grant_turn = grant_turn

    def modify_stat(self, stat_name: str, value: float) -> float:
        return value + 1 if stat_name == "speed" else value

    def on_owner_turn_end(self, battle: Battle) -> None:
        if self.owner is not None and battle.turn_number != self.grant_turn:
            self.owner.remove_status(self, battle)


class ShieldKnightPassingTrait(Trait):
    def __init__(self) -> None:
        super().__init__("巨盾经过补给", "本人每次完整经过显示名含巨盾的单位，使其血+1/4、魔+1、速+1至其下一实际武将回合结束。")

    def on_unit_moved(self, battle: Battle, ctx: MoveContext) -> None:
        owner = self.owner
        if owner is None or ctx.unit is not owner:
            return
        for target in battle.path_crossing_units(owner, ctx.path):
            if not target.alive or target.banished or "巨盾" not in target.name:
                continue
            battle.heal(HealContext(source=owner, target=target, amount=0.25,
                                    action_name="巨盾经过补给", effect_source=self))
            gained = target.gain_mana(1)
            speed_status = ShieldKnightSpeedStatus(battle.turn_number)
            speed_status.is_skill_effect = False
            target.add_status(speed_status, source=owner)
            battle.log_public_event(f"{target.name} 被巨盾骑士经过，魔+{gained}、速+1。", source=owner, target=target)


