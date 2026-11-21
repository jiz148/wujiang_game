"""R131 Francis: repeated speed, nonlethal scorch and public six-sided volley."""

from __future__ import annotations

from typing import Any

from wujiang.tactical.engine.core import Battle, DamageContext, Position, Skill, StatusEffect, Trait, Unit
from wujiang.tactical.heroes.common import (
    ShensuSkill, match_payload_pattern, payload_target_unit, positions_to_dict,
)
from wujiang.tactical.heroes.mechanics.area_helpers import area_patterns_preview
from wujiang.tactical.heroes.mechanics.electric_wind import front_rectangle_patterns
from wujiang.tactical.prediction import direct_scoring


class FrancisUnlimitedShensu(ShensuSkill):
    def __init__(self) -> None:
        super().__init__()
        self.max_uses_per_turn = None
        self.description = "费1魔，本轮下一次普通移动+3；本人轮内使用次数不限。"


class FrancisTurnMana(Trait):
    def __init__(self) -> None:
        super().__init__("光羽回魔", "每次己方进攻轮结束时魔+5，受魔上限限制。")

    def on_any_turn_end(self, battle: Battle, ended_player_id: int) -> None:
        owner = self.owner
        if owner is not None and owner.alive and ended_player_id == owner.player_id:
            gained = owner.gain_mana(5)
            if gained:
                battle.log_public_event(f"{owner.name} 在己方轮末回魔 {gained:g}。", source=owner)


class SearingMark(StatusEffect):
    def __init__(self, source_unit_id: str) -> None:
        super().__init__("灼热之箭", "受影响单位的接下来三次进攻回合结束时各失魔1、失血1/8；致命的那次失血无效。")
        self.source_unit_id = source_unit_id
        self.remaining_ticks = 3

    def to_public_dict(self, battle: Battle) -> dict[str, Any]:
        data = super().to_public_dict(battle)
        data["duration"] = self.remaining_ticks
        return data

    def on_before_hp_loss(self, battle: Battle, ctx: DamageContext) -> None:
        if (ctx.target is self.owner and "searing_arrow_dot" in ctx.tags
                and ctx.raw_damage is not None and ctx.raw_damage >= ctx.target.current_hp):
            ctx.raw_damage = 0.0
            battle.log_public_event(f"{ctx.target.name} 的灼热持续伤害本次会致命，伤害无效。",
                                    source=battle.units.get(self.source_unit_id), target=ctx.target)

    def on_owner_turn_end(self, battle: Battle) -> None:
        target = self.owner
        if target is None or not target.alive:
            return
        lost = target.drain_mana(1)
        if lost:
            battle.log_public_event(f"{target.name} 的灼热烫伤使魔-1。",
                                    source=battle.units.get(self.source_unit_id), target=target)
        source = battle.units.get(self.source_unit_id)
        battle.resolve_damage(DamageContext(
            source=source, target=target, attack_power=0,
            raw_damage=0.125, is_skill=True, from_field_effect=True,
            ignore_shield=True, ignore_magic_immunity=True, cannot_evade=True,
            action_name="灼热之箭持续伤害", tags={"searing_arrow_dot"},
        ))
        self.remaining_ticks -= 1
        if self.remaining_ticks <= 0 and self in target.statuses:
            target.remove_status(self, battle)


class SearingArrowSkill(Skill):
    ai_payload_value_owned = True

    def __init__(self) -> None:
        super().__init__("searing_arrow", "灼热之箭",
                         "费1魔、每本人轮一次；伤害目标并赋不叠加破魔烫伤，受体接下来三次进攻回合结束时失魔1、失血1/8；致命的该次持续伤无效。",
                         mana_cost=1, max_uses_per_turn=1, target_mode="enemy")

    def ignores_shield_for_payload(self, battle: Battle, actor: Unit,
                                   payload: dict[str, Any]) -> bool:
        return True

    def preview(self, battle: Battle, actor: Unit) -> dict[str, Any]:
        targets = [unit for unit in battle.enemy_units(actor.player_id)
                   if unit.position is not None and battle.unit_target_in_range_and_line(
                       actor, unit, actor.targeting_range())]
        return {"cells": positions_to_dict([cell for unit in targets for cell in battle.unit_cells(unit)]),
                "target_unit_ids": [unit.unit_id for unit in targets],
                "secondary_cells": [], "requires_target": True}

    def ai_effective_enemy_impact(self, battle: Battle, actor: Unit,
                                  payload: dict[str, Any], targets: list[Unit] | None = None,
                                  shared: Any = None, action: dict[str, Any] | None = None) -> bool:
        target = battle.units.get(str(payload.get("target_unit_id")))
        return bool(target is not None and target.alive and target.player_id != actor.player_id
                    and not target.magic_immunity)

    def ai_payload_value(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> float:
        from wujiang.tactical.rooms.ai_probe import ai_probe_rollback

        target = battle.units.get(str(payload.get("target_unit_id")))
        if target is None or not target.alive or target.magic_immunity:
            return -1000.0
        with ai_probe_rollback(battle):
            source, recipient = battle.get_unit(actor.unit_id), battle.get_unit(target.unit_id)
            before = recipient.current_hp
            ctx = battle.resolve_damage(DamageContext(
                source=source, target=recipient, attack_power=source.stat("attack"),
                is_skill=True, action_name=self.name, tags={"skill", self.code}))
            damage = max(0.0, before - recipient.current_hp)
        mark = target.get_status("灼热之箭")
        future = 0.0 if mark is not None else min(3, target.current_mana) * 22.0 + min(0.375, max(0, target.current_hp - 0.125)) * 50.0
        return damage * 100.0 + future - 18.0

    def ai_priority_score(self, context: Any) -> float:
        return self.ai_payload_value(context.battle, context.actor, context.payload)

    def execute(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> None:
        target = payload_target_unit(battle, payload)
        if target.player_id == actor.player_id:
            return
        checked = battle.validate_target(actor, target, action_name=self.name,
                                         is_skill=True, is_hostile=True, ignore_shield=True,
                                         damage_target=True, record_effect_on_validation=False)
        if checked.cancelled:
            return
        recipient = checked.target
        battle.resolve_damage(DamageContext(
            source=actor, target=recipient, attack_power=actor.stat("attack"),
            is_skill=True, action_name=self.name, cannot_evade=True,
            tags={"skill", self.code},
        ))
        if recipient.alive and recipient.get_status("灼热之箭") is None:
            recipient.add_status(SearingMark(actor.unit_id), source=actor)
            battle.record_skill_effect(recipient)
            battle.log_public_event(f"{recipient.name} 被灼热之箭烫伤三轮。", source=actor,
                                    target=recipient)


class JusticeDescendsSkill(Skill):
    ai_payload_value_owned = True

    def __init__(self) -> None:
        super().__init__("justice_descends", "天降正义",
                         "费2魔、每本人轮一次；选身前3×3，公开六面骰并逐次造成对应次数伤害。",
                         mana_cost=2, max_uses_per_turn=1, target_mode="cell")

    def patterns(self, battle: Battle, actor: Unit) -> list[list[Position]]:
        return front_rectangle_patterns(battle, actor, depth=3, width=3)

    def _cells(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> list[Position]:
        return battle.payload_positions(payload, "declared_justice_cells") or match_payload_pattern(
            payload, self.patterns(battle, actor))

    def preview(self, battle: Battle, actor: Unit) -> dict[str, Any]:
        return area_patterns_preview(battle, actor, self.patterns(battle, actor))

    def get_target_cells_for_payload(self, battle: Battle, actor: Unit,
                                     payload: dict[str, Any]) -> list[Position]:
        return self._cells(battle, actor, payload)

    def get_target_units_for_payload(self, battle: Battle, actor: Unit,
                                     payload: dict[str, Any]) -> list[Unit]:
        return list(battle.effect_units_at_cells(self._cells(battle, actor, payload), ignore=actor))

    def queued_payload_metadata(self, battle: Battle, actor: Unit,
                                payload: dict[str, Any]) -> dict[str, Any]:
        return {"declared_justice_cells": positions_to_dict(self._cells(battle, actor, payload))}

    def ai_payload_candidates(self, battle: Battle, actor: Unit,
                              action: dict[str, Any]) -> list[dict[str, Any]]:
        return [{"type": "skill", "unit_id": actor.unit_id, "skill_code": self.code,
                 "cells": positions_to_dict(cells)} for cells in self.patterns(battle, actor)]

    def ai_candidate_payloads(self, context: Any) -> list[dict[str, Any]]:
        return self.ai_payload_candidates(context.battle, context.actor, context.action)

    def ai_effective_enemy_impact(self, battle: Battle, actor: Unit,
                                  payload: dict[str, Any], targets: list[Unit] | None = None,
                                  shared: Any = None, action: dict[str, Any] | None = None) -> bool:
        return any(unit.player_id != actor.player_id for unit in self.get_target_units_for_payload(battle, actor, payload))

    def ai_payload_value(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> float:
        from wujiang.tactical.rooms.ai_probe import ai_probe_rollback

        try:
            cells = self._cells(battle, actor, payload)
        except (TypeError, ValueError):
            return -1000.0
        if not cells:
            return -1000.0
        total = 0.0
        for count in range(1, 7):
            with ai_probe_rollback(battle):
                source = battle.get_unit(actor.unit_id)
                targets_before = {unit.unit_id: (unit.current_hp, unit.player_id)
                                  for unit in battle.effect_units_at_cells(cells, ignore=actor)}
                for _ in range(count):
                    for target_id in targets_before:
                        target = battle.units.get(target_id)
                        if target is not None and target.alive:
                            battle.resolve_damage(DamageContext(
                                source=source, target=target, attack_power=source.stat("attack"),
                                is_skill=True, action_name=self.name,
                                area_cell_hits=battle.unit_hit_count_for_cells(target, cells),
                                tags={"skill", self.code},
                            ))
                for target_id, (before, player_id) in targets_before.items():
                    target = battle.units.get(target_id)
                    after = target.current_hp if target is not None else 0.0
                    damage = min(before, max(0.0, before - after))
                    total += damage * (100.0 if player_id != actor.player_id else -150.0) / 6.0
        return total - 25.0 if total > 0 else -1000.0

    def ai_priority_score(self, context: Any) -> float:
        return self.ai_payload_value(context.battle, context.actor, context.payload)

    def execute(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> None:
        cells = self._cells(battle, actor, payload)
        if direct_scoring():
            # A uniform d6 performs hit n with probability (7-n)/6. Keep each
            # hit in the normal damage pipeline so shields and defenses apply.
            hit_probabilities = (1.0, 5 / 6, 4 / 6, 3 / 6, 2 / 6, 1 / 6)
        else:
            roll = battle.roll_die(6, action_name="天降正义掷骰", beneficiary_player_id=actor.player_id)
            battle.log_public_event(f"{actor.name} 的天降正义骰点为 {roll}。", source=actor)
            hit_probabilities = (1.0,) * roll
        target_ids = [unit.unit_id for unit in battle.effect_units_at_cells(cells, ignore=actor)]
        for probability in hit_probabilities:
            for target_id in target_ids:
                target = battle.units.get(target_id)
                if target is not None and target.alive:
                    battle.resolve_damage(DamageContext(
                        source=actor, target=target, attack_power=actor.stat("attack"),
                        is_skill=True, action_name=self.name,
                        forecast_hit_probability=probability,
                        area_cell_hits=battle.unit_hit_count_for_cells(target, cells),
                        tags={"skill", self.code},
                    ))
