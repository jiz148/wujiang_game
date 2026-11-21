"""R049: enemy-cell damage attempts become mana points and fuel Origin Burst."""

from __future__ import annotations

from math import ceil
from typing import Any

from wujiang.tactical.engine.core import (
    ActionError, Battle, DamageContext, HealContext, Position, Skill, Trait, Unit,
)
from wujiang.tactical.heroes.ai_candidate_plan import RANKED_CANDIDATES
from wujiang.tactical.heroes.common import (
    match_payload_pattern, pattern_selection_preview, positions_to_dict,
)
from wujiang.tactical.heroes.mechanics.next_five_effects import remote_rectangle_patterns
from wujiang.tactical.heroes.mechanics.next_five_geometry import ALL_DIRECTIONS


class DamageAttemptManaPoints(Trait):
    """Count occupied enemy cells when this unit declares a damage attempt."""

    declared_damage_codes = frozenset({"pierce", "sacrifice_sword", "origin_burst"})

    def __init__(self) -> None:
        super().__init__("敌格伤害尝试蓄点", "每次试图伤害对方单位所占格子，获得相同数量魔力点。")
        self.initialized = False

    def on_enter_battle(self, battle: Battle) -> None:
        if self.owner is not None and not self.initialized:
            self.owner.mana_points = float(self.owner.base_stats.mana)
            self.initialized = True

    def on_owner_action_declared(self, battle: Battle, action_type: str,
                                 payload: dict[str, Any]) -> None:
        owner = self.owner
        if owner is None or action_type not in {"attack", "skill"}:
            return
        if action_type == "attack":
            if payload.get("declared_target_x") is None or payload.get("declared_target_y") is None:
                return
            cells = [Position(int(payload["declared_target_x"]), int(payload["declared_target_y"]))]
        else:
            code = str(payload.get("skill_code") or "")
            if code not in self.declared_damage_codes:
                return
            skill = owner.skill_map().get(code)
            if skill is None:
                return
            try:
                cells = skill.get_target_cells_for_payload(battle, owner, payload)
            except (ActionError, KeyError, TypeError, ValueError):
                return
        gained = sum(any(unit.player_id != owner.player_id and unit.alive and not unit.banished
                         for unit in battle.units_at_cells([cell])) for cell in cells)
        if gained:
            owner.gain_mana_points(gained)
            battle.log_public_event(f"{owner.name} 尝试伤害 {gained} 个敌方占格，获得 {gained} 点魔力点。",
                                    source=owner)

    def on_before_damage(self, battle: Battle, ctx: DamageContext) -> None:
        owner = self.owner
        if (owner is None or ctx.source is not owner or ctx.target.player_id == owner.player_id
                or ctx.target.position is None):
            return
        action = battle.resolving_action
        if (action is not None and action.actor_id == owner.unit_id
                and action.action_type in {"attack", "skill"}
                and (action.action_type == "attack"
                     or str(action.payload.get("skill_code") or "") in self.declared_damage_codes)):
            return
        gained = max(1, int(ctx.area_cell_hits or 1))
        owner.gain_mana_points(gained)
        battle.log_public_event(f"{owner.name} 的后续伤害尝试获得 {gained} 点魔力点。", source=owner)


class SacrificeSwordSkill(Skill):
    ai_payload_value_owned = True
    ai_candidate_plan = RANKED_CANDIDATES

    def __init__(self) -> None:
        super().__init__("sacrifice_sword", "祭纳之剑",
                         "每本人轮两次；直线四格按距离分别以伤害值5、4、3、2伤双方。",
                         max_uses_per_turn=2, target_mode="cell")

    def patterns(self, battle: Battle, actor: Unit) -> list[list[Position]]:
        if actor.position is None:
            return []
        seen: set[tuple[tuple[int, int], ...]] = set()
        result: list[list[Position]] = []
        body = set(battle.unit_cells(actor))
        for origin in battle.unit_cells(actor):
            for dx, dy in ALL_DIRECTIONS:
                cells = [origin.offset(dx * step, dy * step) for step in range(1, 5)]
                cells = [cell for cell in cells if battle.in_bounds(cell) and cell not in body]
                if not cells:
                    continue
                key = tuple((cell.x, cell.y) for cell in cells)
                if key not in seen:
                    seen.add(key)
                    result.append(cells)
        return result

    def chosen(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> list[Position]:
        if "declared_sword_cells" in payload:
            return battle.payload_positions(payload, "declared_sword_cells")
        return match_payload_pattern(payload, self.patterns(battle, actor))

    def queued_payload_metadata(self, battle: Battle, actor: Unit,
                                payload: dict[str, Any]) -> dict[str, Any]:
        return {"declared_sword_cells": positions_to_dict(self.chosen(battle, actor, payload))}

    def preview(self, battle: Battle, actor: Unit) -> dict[str, Any]:
        patterns = self.patterns(battle, actor)
        result = pattern_selection_preview(patterns)
        all_cells = list(dict.fromkeys(cell for pattern in patterns for cell in pattern))
        result.update({"target_unit_ids": [unit.unit_id for unit in battle.effect_units_at_cells(all_cells, ignore=actor)],
                       "secondary_cells": [], "requires_target": True})
        return result

    def get_target_cells_for_payload(self, battle: Battle, actor: Unit,
                                     payload: dict[str, Any]) -> list[Position]:
        return self.chosen(battle, actor, payload)

    def get_target_units_for_payload(self, battle: Battle, actor: Unit,
                                     payload: dict[str, Any]) -> list[Unit]:
        return battle.effect_units_at_cells(self.chosen(battle, actor, payload), ignore=actor)

    def ai_payload_candidates(self, battle: Battle, actor: Unit,
                              action: dict[str, Any]) -> list[dict[str, Any]]:
        return [{"type": "skill", "unit_id": actor.unit_id, "skill_code": self.code,
                 "cells": positions_to_dict(line)} for line in self.patterns(battle, actor)]

    def ai_candidate_payloads(self, context: Any) -> list[dict[str, Any]]:
        return self.ai_payload_candidates(context.battle, context.actor, context.action)

    def ai_payload_value(self, battle: Battle, actor: Unit,
                         payload: dict[str, Any]) -> float:
        try:
            cells = self.chosen(battle, actor, payload)
        except (ActionError, KeyError, TypeError, ValueError):
            return -1000.0
        value = -8.0
        for index, cell in enumerate(cells):
            for unit in battle.effect_units_at_cells([cell], ignore=actor):
                sign = 1.0 if unit.player_id != actor.player_id else -1.3
                if unit.magic_immunity:
                    continue
                if unit.total_shields() > 0:
                    value += sign * 20.0
                else:
                    power = 5 - index
                    damage = 1.0 if power > unit.stat("defense") else 1.0 / 2 ** (unit.stat("defense") - power + 1)
                    value += sign * min(unit.current_hp, damage) * 95.0
                if sign > 0:
                    value += 12.0
        return value

    def ai_prefilter_score(self, battle: Battle, actor: Unit,
                           payload: dict[str, Any], shared: Any) -> float:
        return self.ai_payload_value(battle, actor, payload)

    def ai_priority_score(self, context: Any) -> float:
        return self.ai_payload_value(context.battle, context.actor, context.payload)

    def ai_effective_enemy_impact(self, battle: Battle, actor: Unit,
                                  payload: dict[str, Any], targets: list[Unit] | None = None,
                                  shared: Any = None, action: dict[str, Any] | None = None) -> bool:
        return self.ai_payload_value(battle, actor, payload) > 0

    def execute(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> None:
        for index, cell in enumerate(self.chosen(battle, actor, payload)):
            for unit in battle.effect_units_at_cells([cell], ignore=actor):
                battle.resolve_damage(DamageContext(
                    source=actor, target=unit, attack_power=5 - index,
                    is_skill=True, action_name=self.name, tags={"skill", self.code}))


class OriginBurstSkill(Skill):
    ai_payload_value_owned = True
    ai_enemy_impact_requirement = False
    ai_candidate_plan = RANKED_CANDIDATES

    def __init__(self) -> None:
        super().__init__("origin_burst", "起源爆发",
                         "每本人轮一次；清空魔力点，依次补魔、回血，再以剩余点数n选n×n伤害区。",
                         max_uses_per_turn=1, target_mode="cell")

    def allocation(self, actor: Unit) -> tuple[int, int, int, int]:
        points = max(0, int(actor.mana_points))
        mana = min(points, ceil(max(0.0, actor.max_mana() - actor.current_mana) - 1e-9))
        health = min(points - mana, ceil(max(0.0, actor.max_health - actor.current_hp) / 0.25 - 1e-9))
        return points, mana, health, points - mana - health

    def patterns(self, battle: Battle, actor: Unit,
                 n: int | None = None) -> list[list[Position]]:
        size = self.allocation(actor)[3] if n is None else n
        if size <= 0:
            return []
        return remote_rectangle_patterns(battle, actor, size, size)

    def selected(self, battle: Battle, actor: Unit,
                 payload: dict[str, Any]) -> list[Position]:
        if "declared_origin_cells" in payload:
            return battle.payload_positions(payload, "declared_origin_cells")
        n = self.allocation(actor)[3]
        if n <= 0:
            return []
        return match_payload_pattern(payload, self.patterns(battle, actor, n))

    def can_use(self, battle: Battle, actor: Unit,
                payload: dict[str, Any] | None = None) -> tuple[bool, str]:
        ok, reason = super().can_use(battle, actor, payload)
        if not ok:
            return ok, reason
        if actor.mana_points <= 0:
            return False, "没有可消耗的魔力点。"
        if payload is not None and self.allocation(actor)[3] > 0 and "cells" in payload:
            try:
                self.selected(battle, actor, payload)
            except (ActionError, KeyError, TypeError, ValueError) as exc:
                return False, str(exc)
        return True, ""

    def queued_payload_metadata(self, battle: Battle, actor: Unit,
                                payload: dict[str, Any]) -> dict[str, Any]:
        points, mana, health, n = self.allocation(actor)
        return {"declared_origin_points": points, "declared_origin_mana": mana,
                "declared_origin_health": health, "declared_origin_size": n,
                "declared_origin_cells": positions_to_dict(self.selected(battle, actor, payload))}

    def prepay_resources(self, battle: Battle, actor: Unit,
                         payload: dict[str, Any] | None = None) -> None:
        points = int((payload or {}).get("declared_origin_points", actor.mana_points))
        super().prepay_resources(battle, actor, payload)
        actor.spend_mana_points(points)

    def preview(self, battle: Battle, actor: Unit) -> dict[str, Any]:
        points, mana, health, n = self.allocation(actor)
        patterns = self.patterns(battle, actor, n)
        result = pattern_selection_preview(patterns) if patterns else {"cells": [], "selection": {}}
        all_cells = list(dict.fromkeys(cell for pattern in patterns for cell in pattern))
        result.update({"target_unit_ids": [unit.unit_id for unit in battle.effect_units_at_cells(all_cells, ignore=actor)],
                       "secondary_cells": [], "requires_target": n > 0,
                       "resource_preview": {"points": points, "mana": mana, "health_quarters": health,
                                            "area_size": n}})
        return result

    def get_target_cells_for_payload(self, battle: Battle, actor: Unit,
                                     payload: dict[str, Any]) -> list[Position]:
        return self.selected(battle, actor, payload)

    def get_target_units_for_payload(self, battle: Battle, actor: Unit,
                                     payload: dict[str, Any]) -> list[Unit]:
        return battle.effect_units_at_cells(self.selected(battle, actor, payload), ignore=actor)

    def ai_payload_candidates(self, battle: Battle, actor: Unit,
                              action: dict[str, Any]) -> list[dict[str, Any]]:
        base = {"type": "skill", "unit_id": actor.unit_id, "skill_code": self.code}
        patterns = self.patterns(battle, actor)
        return [{**base, "cells": positions_to_dict(line)} for line in patterns] if patterns else [base]

    def ai_candidate_payloads(self, context: Any) -> list[dict[str, Any]]:
        return self.ai_payload_candidates(context.battle, context.actor, context.action)

    def ai_payload_value(self, battle: Battle, actor: Unit,
                         payload: dict[str, Any]) -> float:
        points, mana, health, n = self.allocation(actor)
        if points <= 0:
            return -1000.0
        value = mana * 16.0 + health * 31.0 - 8.0
        if n > 0:
            try:
                cells = self.selected(battle, actor, payload)
            except (ActionError, KeyError, TypeError, ValueError):
                return -1000.0
            for unit in battle.effect_units_at_cells(cells, ignore=actor):
                if unit.magic_immunity:
                    continue
                sign = 1.0 if unit.player_id != actor.player_id else -1.3
                if unit.total_shields() > 0:
                    value += sign * 20.0
                else:
                    power = actor.stat("attack") + max(0, battle.unit_hit_count_for_cells(unit, cells) - 1)
                    damage = 1.0 if power > unit.stat("defense") else 1.0 / 2 ** (unit.stat("defense") - power + 1)
                    value += sign * min(unit.current_hp, damage) * 100.0
        return value

    def ai_prefilter_score(self, battle: Battle, actor: Unit,
                           payload: dict[str, Any], shared: Any) -> float:
        return self.ai_payload_value(battle, actor, payload)

    def ai_priority_score(self, context: Any) -> float:
        return self.ai_payload_value(context.battle, context.actor, context.payload)

    def ai_effective_enemy_impact(self, battle: Battle, actor: Unit,
                                  payload: dict[str, Any], targets: list[Unit] | None = None,
                                  shared: Any = None, action: dict[str, Any] | None = None) -> bool:
        return self.ai_payload_value(battle, actor, payload) > 0

    def execute(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> None:
        mana = int(payload.get("declared_origin_mana", 0))
        health = int(payload.get("declared_origin_health", 0))
        if mana:
            actor.gain_mana(mana)
        for _ in range(health):
            battle.heal(HealContext(source=actor, target=actor, amount=0.25,
                                    action_name=self.name))
        cells = self.selected(battle, actor, payload)
        for unit in battle.effect_units_at_cells(cells, ignore=actor):
            battle.resolve_damage(DamageContext(
                source=actor, target=unit, attack_power=actor.stat("attack"),
                area_cell_hits=battle.unit_hit_count_for_cells(unit, cells),
                is_skill=True, action_name=self.name, tags={"skill", self.code}))


__all__ = ["DamageAttemptManaPoints", "SacrificeSwordSkill", "OriginBurstSkill"]
