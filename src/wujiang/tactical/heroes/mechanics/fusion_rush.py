"""R030 fusion circle, nuclear rush, and death burst."""

from __future__ import annotations

from typing import Any
from wujiang.tactical.engine.core import ActionError
from wujiang.tactical.engine.core import Battle
from wujiang.tactical.engine.core import DamageContext
from wujiang.tactical.engine.core import HeroUnit
from wujiang.tactical.engine.core import Position
from wujiang.tactical.engine.core import Skill
from wujiang.tactical.engine.core import StatusEffect
from wujiang.tactical.engine.core import Trait
from wujiang.tactical.heroes.common import positions_to_dict
from wujiang.tactical.heroes.mechanics.deadly_bow import DeadlyBowSkill
from wujiang.tactical.heroes.next_five import position_key
from wujiang.tactical.heroes.next_five import square_around_cells


class FusionCircleAttackTrait(Trait):
    def __init__(self) -> None:
        super().__init__("攻击一周", "普攻同时命中自身外圈全部敌方，每个实际受体一次。")

    def basic_attack_area_affects_allies(self, battle, actor, payload=None):
        return False

    def basic_attack_area_cells(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> list[Position] | None:
        if actor.has_status("核冲"):
            return None
        if "attack_cells" in payload:
            return battle.payload_positions(payload, "attack_cells")
        own_cells = battle.unit_cells(actor)
        own = {position_key(cell) for cell in own_cells}
        return [cell for cell in square_around_cells(battle, own_cells, radius=1) if position_key(cell) not in own]

    def basic_attack_payload_metadata(self, battle, actor, payload):
        if actor.has_status("核冲"):
            return {}
        if payload.get("reaction_attack"):
            return {}
        return {"single_hit_per_recipient": True, "area_attack": True,
                "attack_cells": positions_to_dict(self.basic_attack_area_cells(battle, actor, payload) or [])}

    def basic_attack_preview(self, battle, actor, payload):
        if actor.has_status("核冲"):
            return None
        cells = self.basic_attack_area_cells(battle, actor, payload) or []
        return {"cells": positions_to_dict(cells), "secondary_cells": [], "requires_target": True,
                "target_unit_ids": [unit.unit_id for unit in battle.effect_units_at_cells(cells)
                                    if actor.is_enemy_of(unit) and battle.unit_can_be_selected(unit, actor=actor)[0]]}


class NuclearRushStatus(StatusEffect):
    DIRECTIONS = DeadlyBowSkill.DIRECTIONS

    def basic_attack_area_affects_allies(self, battle, actor, payload=None):
        return False

    def __init__(self) -> None:
        super().__init__("核冲", "攻 +1；普攻攻击前4格，之后尝试移到声明的第5格；落点不合法仍完成攻击。", duration=None)

    def modify_stat(self, stat_name: str, value: float) -> float:
        return value + 1 if stat_name == "attack" else value

    def line(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> list[Position]:
        if actor.position is None:
            return []
        code = str(payload.get("choice_code") or payload.get("direction") or "")
        direction = self.DIRECTIONS.get(code) if code else (payload.get("dx"), payload.get("dy"))
        if direction not in self.DIRECTIONS.values():
            raise ActionError("核冲需要选择八方向之一。")
        dx, dy = direction
        # Keep the fifth cell even when outside the board; never substitute the last valid cell.
        return [actor.position.offset(dx * step, dy * step) for step in range(1, 6)]

    def basic_attack_payload_metadata(self, battle, actor, payload):
        if payload.get("reaction_attack"):
            return {}
        if payload.get("nuclear_rush_declared"):
            return {}
        if not payload.get("queued_resolution") and not any(key in payload for key in ("choice_code", "direction", "dx", "dy")):
            return {}
        line = self.line(battle, actor, payload)
        cells = [cell for cell in line[:4] if battle.in_bounds(cell)]
        if not cells:
            raise ActionError("该方向在棋盘内没有可攻击的格子。")
        if payload.get("cells") and set(battle.payload_positions(payload, "cells")) != set(cells):
            raise ActionError("请选择该方向的完整攻击区域。")
        return {"nuclear_rush_declared": True, "nuclear_rush_landing": line[4].to_dict(),
                "attack_cells": positions_to_dict(cells), "single_hit_per_recipient": True, "area_attack": True}

    def basic_attack_area_cells(self, battle, actor, payload):
        if "attack_cells" in payload:
            return battle.payload_positions(payload, "attack_cells")
        metadata = self.basic_attack_payload_metadata(battle, actor, payload)
        return battle.payload_positions(metadata, "attack_cells") if metadata else None

    def basic_attack_preview(self, battle, actor, payload):
        choices, destinations, cells, landing_cells = [], [], set(), set()
        for code in self.DIRECTIONS:
            line = self.line(battle, actor, {"direction": code})
            area = [cell for cell in line[:4] if battle.in_bounds(cell)]
            if not area:
                continue
            pattern = positions_to_dict(area)
            landing = line[4]
            legal = (not actor.cannot_move and battle.can_place_unit(actor, landing, ignore=actor, mover=actor))
            if legal:
                landing_cells.update(actor.footprint_cells_at(landing))
            choices.append({"code": code, "label": {"north": "北", "northeast": "东北", "east": "东", "southeast": "东南", "south": "南", "southwest": "西南", "west": "西", "northwest": "西北"}[code] + ("：可落位" if legal else "：仅攻击"), "patterns": [pattern]})
            destinations.append({"choice_code": code, "pattern": pattern,
                                 "destination_cells": positions_to_dict(actor.footprint_cells_at(landing)) if legal else []})
            cells.update(area)
        return {"cells": positions_to_dict(sorted(cells, key=lambda c: (c.y, c.x))),
                "secondary_cells": positions_to_dict(sorted(landing_cells, key=lambda c: (c.y, c.x))),
                "target_unit_ids": [unit.unit_id for unit in battle.effect_units_at_cells(list(cells))
                                    if actor.is_enemy_of(unit) and battle.unit_can_be_selected(unit, actor=actor)[0]],
                "requires_target": True, "pattern_destinations": destinations,
                "selection": {"mode": "choice_pattern", "choices": choices, "ordered": False,
                              "choice_prompt": "选择核冲方向。", "cell_prompt": "选择前4格（边界裁剪）；预览第5格落点。"}}

    def on_basic_attack_finished(self, battle, actor, payload, damage_contexts, missed):
        if (actor is not self.owner or not payload.get("nuclear_rush_declared") or payload.get("basic_attack_cancelled")
                or not actor.alive or actor.banished or actor.position is None or actor.unit_id not in battle.units):
            return
        landing = payload.get("nuclear_rush_landing")
        if not isinstance(landing, dict):
            return
        destination = Position(int(landing["x"]), int(landing["y"]))
        # Resolution uses the old attack origin, but the move starts at the real current body.
        actor.position = getattr(actor, "_resolution_actual_position", None) or actor.position
        if destination == actor.position:
            return
        try:
            battle.move_unit(actor, destination, via_skill=True, allow_anywhere=True, tags={"nuclear_rush"})
        except ActionError as exc:
            battle.log(f"{actor.name} 的核冲攻击已完成，第5格未能落位：{exc}")


class NuclearRushSkill(Skill):
    def __init__(self) -> None:
        super().__init__("nuclear_rush", "核冲", "开关技能：回合开始使用；攻 +1，普攻攻击4格并尝试移动到第5格；实际开关后魔力点 +1（最多6）。", max_uses_per_turn=1, target_mode="self")

    def can_use(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any] | None = None) -> tuple[bool, str]:
        ok, reason = super().can_use(battle, actor, payload)
        if not ok:
            return ok, reason
        if actor.actions_taken_this_turn or actor.move_used or actor.attacks_used > 0 or actor.performed_active_skill:
            return False, "核冲只能在回合开始阶段使用。"
        return True, ""

    def execute(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> None:
        if actor.direct_effects_blocked():
            return
        before = actor.has_status("核冲")
        existing = actor.get_status("核冲")
        if existing is not None:
            actor.remove_status(existing, battle)
        else:
            actor.add_status(NuclearRushStatus())
        if before != actor.has_status("核冲"):
            actor.gain_mana_points(1)
            battle.log(f"{actor.name} {'开启' if actor.has_status('核冲') else '关闭'}核冲，魔力点 {actor.mana_points:g}/6。")


class FusionDeathExplosionTrait(Trait):
    def __init__(self) -> None:
        super().__init__("聚变爆炸", "每次被破坏后，最后身体周围5×5双方受体各承受一次以魔力点为攻值的半破魔场地伤害。")
        self.last_exploded_destruction = 0

    def on_owner_removed(self, battle: Battle) -> None:
        owner = self.owner
        event = getattr(owner, "destruction_count", 0)
        if (owner is None or owner.alive or owner.banished or owner.is_clone or owner.is_summon
                or not event or event <= self.last_exploded_destruction):
            return
        self.last_exploded_destruction = event
        body = getattr(owner, "last_destroyed_cells", [])
        cells = square_around_cells(battle, body, radius=2)
        power = min(max(owner.mana_points, 0.0), 6.0)
        battle.log(f"{owner.name} 被破坏后引发聚变爆炸：最后身体周围5×5，攻值 {power:g}，半破魔，影响双方。")
        for target in list(battle.effect_units_at_cells(cells)):
            if target.unit_id == owner.unit_id or not target.alive or target.banished:
                continue
            battle.resolve_damage(DamageContext(source=owner, target=target, attack_power=power,
                                                is_skill=False, half_ignore_shield=True, from_field_effect=True,
                                                action_name="聚变爆炸", tags={"fusion_explosion", "area_attack"}))

