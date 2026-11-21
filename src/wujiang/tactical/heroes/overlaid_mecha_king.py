"""R082 超量机械王：四格机甲、钢加农与磁暴。"""

from __future__ import annotations

from typing import Any

from wujiang.tactical.engine.core import (
    ActionError, Battle, DamageContext, Position, Skill, Stats, StatusEffect, Trait, Unit,
)
from wujiang.tactical.heroes.base import AbstractHero
from wujiang.tactical.heroes.common import payload_position, positions_to_dict
from wujiang.tactical.heroes.mechanics.next_five_geometry import square_around_cells
from wujiang.tactical.heroes.mechanics.next_five_effects import damage_followup_effect_applies


class SteelCannon(AbstractHero):
    hero_code = "steel_cannon_r082"
    hero_name = "钢加农"
    role = "召唤物"
    attribute = "雷"
    race = "机甲"
    level = 1
    base_stats = Stats(attack=4, defense=7, speed=1, attack_range=5, mana=0)

    def __init__(self, player_id: int) -> None:
        super().__init__(player_id, is_summon=True)

    def build_skills(self) -> list[Skill]:
        return []

    def build_traits(self) -> list[Trait]:
        return []


class SummonSteelCannonSkill(Skill):
    ai_payload_value_owned = True
    ai_enemy_impact_requirement = False

    def __init__(self) -> None:
        super().__init__("summon_steel_cannon", "钢加农",
                         "0魔；每3本人轮一次，在身体外邻完整合法空格召唤攻4守7速1范5的钢加农。",
                         cooldown_turns=3, target_mode="cell")

    def destinations(self, battle: Battle, actor: Unit) -> list[Position]:
        if actor.position is None:
            return []
        body = set(battle.unit_cells(actor))
        ring = set(square_around_cells(battle, body, 1)) - body
        cannon = SteelCannon(actor.player_id)
        return sorted((cell for cell in ring if battle.can_place_unit(cannon, cell)),
                      key=lambda cell: (cell.y, cell.x))

    def can_use(self, battle: Battle, actor: Unit,
                payload: dict[str, Any] | None = None) -> tuple[bool, str]:
        ok, reason = super().can_use(battle, actor, payload)
        if not ok:
            return ok, reason
        cells = self.destinations(battle, actor)
        if not cells:
            return False, "钢加农没有完整合法的外邻落格。"
        if payload is not None and "x" in payload:
            try:
                if payload_position(payload) not in cells:
                    return False, "钢加农落格不合法。"
            except (ActionError, TypeError, ValueError):
                return False, "钢加农落格不合法。"
        return True, ""

    def preview(self, battle: Battle, actor: Unit) -> dict[str, Any]:
        return {"cells": positions_to_dict(self.destinations(battle, actor)),
                "target_unit_ids": [], "secondary_cells": positions_to_dict(battle.unit_cells(actor)),
                "requires_target": True}

    def get_target_cells_for_payload(self, battle: Battle, actor: Unit,
                                     payload: dict[str, Any]) -> list[Position]:
        cell = payload_position(payload)
        if cell not in self.destinations(battle, actor):
            raise ActionError("钢加农落格不合法。")
        return [cell]

    def ai_payload_candidates(self, battle: Battle, actor: Unit,
                              action: dict[str, Any]) -> list[dict[str, Any]]:
        return [{"type": "skill", "unit_id": actor.unit_id, "skill_code": self.code,
                 "x": cell.x, "y": cell.y} for cell in self.destinations(battle, actor)]

    def ai_candidate_payloads(self, context: Any) -> list[dict[str, Any]]:
        return self.ai_payload_candidates(context.battle, context.actor, context.action)

    def ai_payload_value(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> float:
        try:
            cell = self.get_target_cells_for_payload(battle, actor, payload)[0]
        except (ActionError, TypeError, ValueError):
            return -1000.0
        enemies = [enemy for enemy in battle.enemy_units(actor.player_id)
                   if enemy.alive and enemy.position is not None]
        nearest = min((battle.unit_distance_to_cell(enemy, cell) for enemy in enemies), default=8)
        return 50.0 + max(0, 6 - nearest) * 14.0

    def ai_priority_score(self, context: Any) -> float:
        return self.ai_payload_value(context.battle, context.actor, context.payload)

    def execute(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> None:
        try:
            cell = self.get_target_cells_for_payload(battle, actor, payload)[0]
            battle.summon_unit(SteelCannon(actor.player_id), cell, summoner=actor)
        except ActionError:
            battle.log_public_event(f"{actor.name} 的钢加农落格已失效。", source=actor)


class MagneticStormStatus(StatusEffect):
    suppresses_magic_immunity = True

    def __init__(self) -> None:
        super().__init__("磁暴失效", "速-1（最低1），魔免无效；无自然期限。")

    def modify_stat(self, stat_name: str, value: float) -> float:
        return max(1.0, value - 1) if stat_name == "speed" else value


class MagneticStormSkill(Skill):
    ai_payload_value_owned = True
    ai_enemy_impact_requirement = False
    target_cells_are_geometry_only = True

    def __init__(self) -> None:
        super().__init__("magnetic_storm", "磁暴",
                         "本场一次；身体外两格内双方受威力4破魔伤害且无视魔免；命中后速-1、魔免无效。",
                         max_uses_per_battle=1, target_mode="self")

    def cells(self, battle: Battle, actor: Unit) -> list[Position]:
        body = set(battle.unit_cells(actor))
        return [cell for cell in square_around_cells(battle, body, 2) if cell not in body]

    def get_target_cells_for_payload(self, battle: Battle, actor: Unit,
                                     payload: dict[str, Any]) -> list[Position]:
        return self.cells(battle, actor)

    def get_target_units_for_payload(self, battle: Battle, actor: Unit,
                                     payload: dict[str, Any]) -> list[Unit]:
        return list(battle.effect_units_at_cells(self.cells(battle, actor), ignore=actor))

    def queued_payload_metadata(self, battle: Battle, actor: Unit,
                                payload: dict[str, Any]) -> dict[str, Any]:
        return {"declared_storm_cells": positions_to_dict(self.cells(battle, actor))}

    def ignores_shield_for_payload(self, battle: Battle, actor: Unit,
                                   payload: dict[str, Any]) -> bool:
        return True

    def ignores_magic_immunity_for_payload(self, battle: Battle, actor: Unit,
                                           payload: dict[str, Any]) -> bool:
        return True

    def preview(self, battle: Battle, actor: Unit) -> dict[str, Any]:
        cells = self.cells(battle, actor)
        return {"cells": positions_to_dict(cells),
                "target_unit_ids": [unit.unit_id for unit in battle.effect_units_at_cells(cells, ignore=actor)],
                "secondary_cells": positions_to_dict(battle.unit_cells(actor)), "requires_target": False}

    def ai_payload_value(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> float:
        value = 0.0
        for target in self.get_target_units_for_payload(battle, actor, payload):
            if target.dodge_charges > 0:
                continue
            estimated = battle.damage_rule.calculate_damage(4, target.stat("defense"))
            benefit = min(target.current_hp, estimated) * 125.0
            if not target.skill_non_damage_effects_blocked():
                benefit += 25.0
            if target.magic_immunity and not target.skill_non_damage_effects_blocked():
                benefit += 30.0
            value += benefit if target.player_id != actor.player_id else -benefit * 1.5
        return value if value > 0 else -1000.0

    def ai_priority_score(self, context: Any) -> float:
        return self.ai_payload_value(context.battle, context.actor, context.payload)

    def execute(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> None:
        # Declaration exposes the entire ring for reactions. Re-evaluate actual
        # recipients after those reactions, keeping the declared geometry fixed.
        cells = battle.payload_positions(payload, "declared_storm_cells") or self.cells(battle, actor)
        for target in battle.effect_units_at_cells(cells, ignore=actor):
            result = battle.resolve_damage(DamageContext(
                source=actor, target=target, attack_power=4,
                is_skill=True, action_name="磁暴", ignore_shield=True,
                ignore_magic_immunity=True, area_cell_hits=1,
                tags={"skill", "magnetic_storm"},
            ))
            recipient = result.target
            if (not recipient.alive or not damage_followup_effect_applies(result)
                    or recipient.skill_non_damage_effects_blocked()):
                continue
            for status in list(recipient.statuses):
                if isinstance(status, MagneticStormStatus):
                    recipient.remove_status(status, battle)
            recipient.add_status(MagneticStormStatus(), source=actor)
            battle.record_skill_effect(recipient)
            battle.log_public_event(f"{recipient.name} 受磁暴影响，速度-1且魔免无效。",
                                    source=actor, target=recipient)
