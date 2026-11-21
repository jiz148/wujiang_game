"""R045 无名的岩石兵: chosen square growth and damage/heal economy."""

from __future__ import annotations

from typing import Any

from wujiang.tactical.engine.core import (
    ActionError, Battle, DamageContext, HealContext, Position, StatusEffect,
    Trait, Unit,
)
from wujiang.tactical.heroes.mechanics.mana_point_cost import ManaPointCostSkill


_DIRECTIONS = (("nw", -1, -1, "左上"), ("ne", 0, -1, "右上"),
               ("sw", -1, 0, "左下"), ("se", 0, 0, "右下"))


class RockGrowthStatus(StatusEffect):
    def __init__(self) -> None:
        super().__init__("岩兵巨大化", "每层攻、守、速各+1；身体永久扩大。")
        self.stacks = 1

    def modify_stat(self, stat_name: str, value: float) -> float:
        return value + self.stacks if stat_name in {"attack", "defense", "speed"} else value

    def to_public_dict(self, battle: Battle) -> dict[str, Any]:
        data = super().to_public_dict(battle)
        data["stacks"] = self.stacks
        return data


class RockGrowSkill(ManaPointCostSkill):
    ai_payload_value_owned = True
    ai_enemy_impact_requirement = False

    def __init__(self) -> None:
        super().__init__("rock_grow", "巨大化", "每本人轮一次；耗2魔力点，选方向扩成下一正方形，攻守速各+1。",
                         mana_point_cost=2, max_uses_per_turn=1)

    @staticmethod
    def _square(battle: Battle, actor: Unit) -> tuple[int, int, int] | None:
        cells = battle.unit_cells(actor)
        if not cells:
            return None
        min_x, min_y = min(cell.x for cell in cells), min(cell.y for cell in cells)
        size = max(cell.x for cell in cells) - min_x + 1
        if max(cell.y for cell in cells) - min_y + 1 != size or len(cells) != size * size:
            return None
        return min_x, min_y, size

    def options(self, battle: Battle, actor: Unit) -> dict[str, list[Position]]:
        shape = self._square(battle, actor)
        if shape is None or actor.position is None:
            return {}
        min_x, min_y, size = shape
        previous = list(actor.footprint_offsets)
        options: dict[str, list[Position]] = {}
        try:
            for code, shift_x, shift_y, _ in _DIRECTIONS:
                cells = [Position(x, y)
                         for x in range(min_x + shift_x, min_x + shift_x + size + 1)
                         for y in range(min_y + shift_y, min_y + shift_y + size + 1)]
                if not all(battle.in_bounds(cell) for cell in cells):
                    continue
                actor.set_footprint_cells(cells)
                if battle.can_place_unit(actor, actor.position, ignore=actor, mover=actor):
                    options[code] = cells
        finally:
            actor.set_footprint_offsets(previous)
        return options

    def can_use(self, battle: Battle, actor: Unit,
                payload: dict[str, Any] | None = None) -> tuple[bool, str]:
        ok, reason = super().can_use(battle, actor, payload)
        if not ok:
            return ok, reason
        options = self.options(battle, actor)
        if not options:
            return False, "周围没有可供巨大化的完整正方形。"
        if payload is not None and "option_code" in payload and payload["option_code"] not in options:
            return False, "请选择当前合法的巨大化方向。"
        return True, ""

    def preview(self, battle: Battle, actor: Unit) -> dict[str, Any]:
        options = self.options(battle, actor)
        labels = {code: label for code, _, _, label in _DIRECTIONS}
        added = {code: [{"x": cell.x, "y": cell.y} for cell in cells
                        if cell not in set(battle.unit_cells(actor))]
                 for code, cells in options.items()}
        return {"cells": [], "target_unit_ids": [],
                "secondary_cells": [cell for values in added.values() for cell in values],
                "requires_target": True,
                "selection": {"mode": "option", "requires_unit": False,
                              "prompt": "选择身体向哪一侧扩大，再点击完成选择。",
                              "options": [{"code": code, "label": labels[code]} for code in options],
                              "cells_by_option": added}}

    def ai_payload_candidates(self, battle: Battle, actor: Unit,
                              action: dict[str, Any]) -> list[dict[str, Any]]:
        return [{"type": "skill", "unit_id": actor.unit_id,
                 "skill_code": self.code, "option_code": code}
                for code in self.options(battle, actor)]

    def ai_candidate_payloads(self, context: Any) -> list[dict[str, Any]]:
        return self.ai_payload_candidates(context.battle, context.actor, context.action)

    def ai_payload_value(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> float:
        cells = self.options(battle, actor).get(str(payload.get("option_code") or ""))
        if cells is None:
            return -1000.0
        enemies = battle.enemy_units(actor.player_id)
        proximity = min((min(max(abs(cell.x - enemy.position.x),
                                  abs(cell.y - enemy.position.y))
                             for cell in cells)
                         for enemy in enemies if enemy.position is not None), default=8)
        return 52.0 + max(0, 6 - proximity) * 3.0

    def ai_priority_score(self, context: Any) -> float:
        return self.ai_payload_value(context.battle, context.actor, context.payload)

    def execute(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> None:
        cells = self.options(battle, actor).get(str(payload.get("option_code") or ""))
        if cells is None:
            raise ActionError("巨大化方向已经不合法。")
        actor.set_footprint_cells(cells)
        status = next((item for item in actor.statuses if isinstance(item, RockGrowthStatus)), None)
        if status is None:
            actor.add_status(RockGrowthStatus(), source=actor)
        else:
            status.stacks += 1
        battle.log_public_event(f"{actor.name} 巨大化至 {actor.footprint_width}×{actor.footprint_height}。",
                                source=actor)


class RockAttackPointsTrait(Trait):
    def __init__(self) -> None:
        super().__init__("受攻击蓄能", "每次被声明普通攻击，获得2魔力点。")
        self._declarations: set[str] = set()

    def on_target_action_declared(self, battle: Battle, actor: Unit,
                                  action_type: str, payload: dict[str, Any]) -> None:
        if action_type != "attack" or self.owner is None or actor is self.owner:
            return
        key = str(payload.get("declaration_id", id(payload)))
        if key in self._declarations:
            return
        self._declarations.add(key)
        self.owner.gain_mana_points(2)


class RockStoredAttackStatus(StatusEffect):
    def __init__(self) -> None:
        super().__init__("岩兵蓄势", "下一次普攻前攻+1，最高4。")

    def modify_stat(self, stat_name: str, value: float) -> float:
        if stat_name != "attack":
            return value
        owner = self.owner
        growth = (next((status for status in owner.statuses
                        if isinstance(status, RockGrowthStatus)), None)
                  if owner is not None else None)
        if owner is not None and owner.base_stats.attack + (growth.stacks if growth else 0) >= 4:
            return value
        return max(value, min(4.0, value + 1))


class RockStoredAttackTrait(Trait):
    def __init__(self) -> None:
        super().__init__("不攻蓄势", "无普攻的本人轮末，攻+1至4，下一次普攻前有效。")

    def on_owner_turn_end(self, battle: Battle) -> None:
        owner = self.owner
        if owner is not None and owner.attacks_used == 0 and not owner.has_status("岩兵蓄势"):
            owner.add_status(RockStoredAttackStatus(), source=owner)

    def on_owner_action_declared(self, battle: Battle, action_type: str,
                                 payload: dict[str, Any]) -> None:
        if action_type == "attack" and self.owner is not None:
            status = self.owner.get_status("岩兵蓄势")
            if status is not None:
                self.owner.remove_status(status, battle)


class RockFullHealthRescueTrait(Trait):
    def __init__(self) -> None:
        super().__init__("满血半血保命", "满血时每次致命生命伤害改为剩半血。")

    def on_before_hp_loss(self, battle: Battle, ctx: DamageContext) -> None:
        owner = self.owner
        if (owner is not None and ctx.target is owner and owner.current_hp >= owner.max_health
                and ctx.raw_damage is not None and ctx.raw_damage >= owner.current_hp):
            ctx.raw_damage = round(owner.current_hp - owner.max_health / 2, 4)
            battle.log_public_event(f"{owner.name} 满血挡下致命一击，剩半血。", source=owner)


class RockHealPointTaxTrait(Trait):
    def __init__(self) -> None:
        super().__init__("回血耗点", "每次实际回血后魔力点-1。")
        self._last_formal_heal: HealContext | None = None

    def on_after_heal(self, battle: Battle, ctx: HealContext) -> None:
        if self._last_formal_heal is ctx:
            return  # The owner can be both source and target in Battle.heal.
        self._last_formal_heal = ctx
        if self.owner is ctx.target and ctx.actual_heal > 0:
            self.owner.spend_mana_points(1)

    def on_direct_heal_applied(self, battle: Battle, amount: float) -> None:
        if self.owner is not None and amount > 0:
            self.owner.spend_mana_points(1)
