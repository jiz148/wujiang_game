"""R042 Wing God: a line arrow, sacrificed flight, and moving allied shelter."""

from __future__ import annotations

from typing import Any
from wujiang.tactical.query_purity import readonly_query_methods

from wujiang.tactical.engine.core import ActionError, Battle, DamageContext, Position, Skill, StatusEffect, Trait, Unit
from wujiang.tactical.heroes.common import FlyingTrait, match_payload_pattern, pattern_selection_preview, positions_to_dict
from wujiang.tactical.heroes.mechanics.piercing_control import piercing_nondamage_recipient


class SaintArrowSlow(StatusEffect):
    def __init__(self) -> None:
        super().__init__("圣箭迟缓", "速-1，最低1。")

    def modify_stat(self, stat_name: str, value: float) -> float:
        return max(1.0, value - 1.0) if stat_name == "speed" else value


class SaintArrowSkill(Skill):
    ai_payload_value_owned = True

    def __init__(self) -> None:
        super().__init__("saint_arrow", "圣箭", "费1魔；选直线范内一格造成技能伤，并施破魔速-1，最低1。",
                         mana_cost=1, target_mode="cell")

    def patterns(self, battle: Battle, actor: Unit) -> list[list[Position]]:
        origins = battle.unit_cells(actor)
        return [[cell] for y in range(battle.height) for x in range(battle.width)
                if (cell := Position(x, y)) not in origins
                and any(battle.cells_are_straight_aligned(origin, cell)
                        and origin.distance_to(cell) <= actor.targeting_range() for origin in origins)]

    def chosen_cells(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> list[Position]:
        return match_payload_pattern(payload, self.patterns(battle, actor))

    def preview(self, battle: Battle, actor: Unit) -> dict[str, Any]:
        return {**pattern_selection_preview(self.patterns(battle, actor)),
                "target_unit_ids": [], "secondary_cells": [], "requires_target": True}

    def get_target_cells_for_payload(self, battle: Battle, actor: Unit,
                                     payload: dict[str, Any]) -> list[Position]:
        return self.chosen_cells(battle, actor, payload)

    def get_target_units_for_payload(self, battle: Battle, actor: Unit,
                                     payload: dict[str, Any]) -> list[Unit]:
        return list(battle.effect_units_at_cells(self.chosen_cells(battle, actor, payload), ignore=actor))

    def ignores_shield_for_payload(self, battle: Battle, actor: Unit,
                                   payload: dict[str, Any]) -> bool:
        # The separate slow is shield-piercing, so shielding must not hide its chain window.
        return True

    def queued_payload_metadata(self, battle: Battle, actor: Unit,
                                payload: dict[str, Any]) -> dict[str, Any]:
        return {"ignore_shield": True}

    def ai_payload_candidates(self, battle: Battle, actor: Unit,
                              action: dict[str, Any]) -> list[dict[str, Any]]:
        return [{"type": "skill", "unit_id": actor.unit_id, "skill_code": self.code,
                 "cells": positions_to_dict(cells)} for cells in self.patterns(battle, actor)]

    def ai_candidate_payloads(self, context: Any) -> list[dict[str, Any]]:
        return self.ai_payload_candidates(context.battle, context.actor, context.action)

    def ai_effective_enemy_impact(self, battle: Battle, actor: Unit,
                                  payload: dict[str, Any], targets: list[Unit] | None = None,
                                  shared: Any = None, action: dict[str, Any] | None = None) -> bool:
        return any(target.player_id != actor.player_id and not target.magic_immunity
                   for target in self.get_target_units_for_payload(battle, actor, payload))

    def ai_payload_value(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> float:
        try:
            targets = self.get_target_units_for_payload(battle, actor, payload)
        except ActionError:
            return -1000.0
        value = 0.0
        for target in targets:
            if target.magic_immunity and target.player_id != actor.player_id:
                continue
            damage = max(0.0, actor.stat("attack") - target.stat("defense"))
            slow = 0.0 if target.stat("speed") <= 1 else 24.0
            value += (damage * 60.0 + slow) * (1.0 if target.player_id != actor.player_id else -1.5)
        return value - 12.0

    def ai_priority_score(self, context: Any) -> float:
        return self.ai_payload_value(context.battle, context.actor, context.payload)

    def execute(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> None:
        cell = self.chosen_cells(battle, actor, payload)[0]
        for target in battle.effect_units_at_cells([cell], ignore=actor):
            battle.resolve_damage(DamageContext(
                source=actor, target=target, attack_power=actor.stat("attack"), is_skill=True,
                action_name=self.name, tags={"skill", "saint_arrow"},
            ))
            if not target.alive:
                continue
            recipient = piercing_nondamage_recipient(
                battle, actor, target, action_name=self.name, code=self.code)
            if recipient is None or not recipient.alive:
                continue
            recipient.add_status(SaintArrowSlow(), source=actor)
            battle.record_skill_effect(recipient)
            battle.log_public_event(f"{recipient.name} 受圣箭影响，速-1。", source=actor, target=recipient)


class FoldedWingsStatus(StatusEffect):
    suppresses_native_flying = True

    def __init__(self) -> None:
        super().__init__("折翼", "永久失去原生飞行。")


class FoldWingsSkill(Skill):
    ai_payload_value_owned = True
    ai_enemy_impact_requirement = False

    def __init__(self) -> None:
        super().__init__("fold_wings", "折翼", "本场一次；失去原生飞行，魔+4，重置天使之翼。",
                         max_uses_per_battle=1, target_mode="self")

    def preview(self, battle: Battle, actor: Unit) -> dict[str, Any]:
        return {"cells": positions_to_dict(battle.unit_cells(actor)), "target_unit_ids": [actor.unit_id],
                "secondary_cells": [], "requires_target": False}

    def ai_payload_candidates(self, battle: Battle, actor: Unit,
                              action: dict[str, Any]) -> list[dict[str, Any]]:
        return [{"type": "skill", "unit_id": actor.unit_id, "skill_code": self.code}]

    def ai_candidate_payloads(self, context: Any) -> list[dict[str, Any]]:
        return self.ai_payload_candidates(context.battle, context.actor, context.action)

    def ai_payload_value(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> float:
        mana_value = max(0.0, min(4.0, actor.max_mana() - actor.current_mana)) * 12.0
        wings = actor.get_skill("angel_wings")
        reset_value = 30.0 if wings.uses_this_battle > 0 else 0.0
        return mana_value + reset_value - (24.0 if actor.has_flying else 0.0)

    def ai_priority_score(self, context: Any) -> float:
        return self.ai_payload_value(context.battle, context.actor, context.payload)

    def execute(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> None:
        actor.add_status(FoldedWingsStatus(), source=actor)
        FlyingTrait.refresh_flight_state(actor)
        gained = actor.gain_mana(4)
        actor.get_skill("angel_wings").uses_this_battle = 0
        battle.log_public_event(f"{actor.name} 折翼，失去原生飞行，魔+{gained:g}，天使之翼可再次使用。",
                                source=actor, target=actor)


class AngelWingsStatus(StatusEffect):
    def __init__(self) -> None:
        super().__init__("天使之翼", "本人周围5×5内己方单位物免，持续3轮。",
                         duration=3, tick_scope="owner_turn_start")

    def grants_physical_immunity_to(self, battle: Battle, unit: Unit) -> bool:
        owner = self.owner
        if owner is None or unit.player_id != owner.player_id:
            return False
        return any(max(abs(a.x - b.x), abs(a.y - b.y)) <= 2
                   for a in battle.unit_cells(owner) for b in battle.unit_cells(unit))


class AngelWingsSkill(Skill):
    ai_payload_value_owned = True
    ai_enemy_impact_requirement = False

    def __init__(self) -> None:
        super().__init__("angel_wings", "天使之翼", "本场一次；本人周围5×5的己方单位物免3轮。",
                         max_uses_per_battle=1, target_mode="self")

    def preview(self, battle: Battle, actor: Unit) -> dict[str, Any]:
        origins = battle.unit_cells(actor)
        return {"cells": positions_to_dict(origins), "target_unit_ids": [actor.unit_id],
                "secondary_cells": positions_to_dict([Position(x, y) for y in range(battle.height)
                                                       for x in range(battle.width)
                                                       if any(max(abs(origin.x - x), abs(origin.y - y)) <= 2
                                                              for origin in origins)]), "requires_target": False}

    def ai_payload_candidates(self, battle: Battle, actor: Unit,
                              action: dict[str, Any]) -> list[dict[str, Any]]:
        return [{"type": "skill", "unit_id": actor.unit_id, "skill_code": self.code}]

    def ai_candidate_payloads(self, context: Any) -> list[dict[str, Any]]:
        return self.ai_payload_candidates(context.battle, context.actor, context.action)

    def ai_payload_value(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> float:
        allies = [unit for unit in battle.player_units(actor.player_id) if unit.alive and unit.position is not None
                  and any(max(abs(a.x - b.x), abs(a.y - b.y)) <= 2
                          for a in battle.unit_cells(actor) for b in battle.unit_cells(unit))]
        enemies = [unit for unit in battle.enemy_units(actor.player_id) if unit.position is not None]
        exposed = sum(1 for ally in allies if not ally.physical_immunity and any(
            battle.distance_between_units(ally, enemy) <= enemy.targeting_range() + 2
            for enemy in enemies))
        return 22.0 + 31.0 * exposed if exposed else -10.0

    def ai_priority_score(self, context: Any) -> float:
        return self.ai_payload_value(context.battle, context.actor, context.payload)

    def execute(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> None:
        previous = actor.get_status("天使之翼")
        if previous is not None:
            actor.remove_status(previous, battle)
        actor.add_status(AngelWingsStatus(), source=actor)
        battle.log_public_event(f"{actor.name} 展开天使之翼，周围己方单位获得物免。", source=actor)


class WingGodRecoveryTrait(Trait):
    def __init__(self) -> None:
        super().__init__("静翼回魔", "本人轮未攻击且未使用技能时，轮末魔+2。")

    def on_owner_turn_end(self, battle: Battle) -> None:
        owner = self.owner
        if owner is None or any(action == "attack" or action.startswith("skill:")
                                for action in owner.actions_taken_this_turn):
            return
        gained = owner.gain_mana(2)
        if gained:
            battle.log_public_event(f"{owner.name} 静翼回魔，魔+{gained:g}。", source=owner)


@readonly_query_methods('basic_attack_payload_metadata')
class WingGodLongShotTrait(Trait):
    def __init__(self) -> None:
        super().__init__("四格强弓", "普攻距离恰4格时伤害+2。")

    def basic_attack_payload_metadata(self, battle: Battle, actor: Unit,
                                      payload: dict[str, Any] | None = None) -> dict[str, Any]:
        target = battle.units.get(str((payload or {}).get("target_unit_id") or ""))
        return {"attack_bonus": 2} if target is not None and target.position is not None and (
            battle.distance_between_units(actor, target) == 4) else {}
