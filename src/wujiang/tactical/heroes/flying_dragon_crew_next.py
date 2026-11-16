"""Object-owned rules for the Atlanta fighter and gunner (source rows 332–333)."""

from __future__ import annotations

from typing import Any

from wujiang.tactical.engine.core import ActionError, Battle, BattleFieldEffect, DamageContext, Position, Skill, Trait, Unit
from wujiang.tactical.heroes.ai_policy import AISkillScoreContext
from wujiang.tactical.heroes.common import is_mana_drain_immune, payload_target_unit, positions_to_dict
from wujiang.tactical.heroes.flying_dragon_ship import CREW_NAME, ShipCrewMovePolicy, allied_ship
from wujiang.tactical.heroes.mechanics.piercing_control import piercing_nondamage_recipient


def _crew_descendant(battle: Battle, target: Unit) -> bool:
    if CREW_NAME in target.name:
        return True
    root_id = battle.controlling_hero_id(target)
    root = battle.units.get(root_id) if root_id else None
    return bool(root is not None and CREW_NAME in root.name)


def _outer_cells(battle: Battle, actor: Unit) -> list[Position]:
    body = set(battle.unit_cells(actor))
    return sorted({cell.offset(dx, dy) for cell in body for dx in (-1, 0, 1) for dy in (-1, 0, 1)
                   if battle.in_bounds(cell.offset(dx, dy)) and cell.offset(dx, dy) not in body},
                  key=lambda cell: (cell.y, cell.x))


class SilverHeavyArmorField(BattleFieldEffect):
    """A moving, finite armor aura; the cast restriction does not limit its lifetime."""

    def __init__(self, source_unit_id: str, source_player_id: int) -> None:
        super().__init__("银制的重甲", "本人守+1；周围飞盗团武将及其召唤物不受伤害，至下一敌方英雄回合结束。")
        self.source_unit_id = source_unit_id
        self.source_player_id = source_player_id

    def source(self, battle: Battle) -> Unit | None:
        unit = battle.units.get(self.source_unit_id)
        return unit if unit is not None and unit.alive and not unit.banished and unit.position is not None else None

    def protected(self, battle: Battle, target: Unit) -> bool:
        source = self.source(battle)
        if source is None or target is source or not _crew_descendant(battle, target):
            return False
        return any(cell in self.affected_cells(battle) for cell in battle.unit_cells(target))

    def affected_cells(self, battle: Battle) -> list[Position]:
        source = self.source(battle)
        return _outer_cells(battle, source) if source is not None else []

    def modify_unit_stat(self, battle: Battle, unit: Unit, stat_name: str, value: float) -> float:
        return value + 1 if stat_name == "defense" and unit is self.source(battle) else value

    def on_before_damage(self, battle: Battle, ctx: DamageContext) -> None:
        if not ctx.cancelled and self.protected(battle, ctx.target):
            # Keep the hit/effect chain intact. Clones need cancellation because
            # their destruction precedes the ordinary HP calculation.
            if ctx.target.is_clone:
                ctx.cancelled = True
                ctx.preserve_followup_effects = True
            else:
                ctx.raw_damage = 0.0
                ctx.modifier_receipts.add("silver_heavy_armor")
            battle.log_public_event(f"{ctx.target.name} 受到【银制的重甲】保护，未受伤害。",
                                    source=ctx.source, target=ctx.target)

    def on_any_turn_end(self, battle: Battle, ended_player_id: int) -> None:
        if self.source(battle) is None:
            battle.remove_field_effect(self)
            return
        active = battle.units.get(str(battle.current_turn_slot_unit_id() or ""))
        if (ended_player_id != self.source_player_id and active is not None
                and active.player_id != self.source_player_id and not active.is_summon and not active.is_clone):
            battle.remove_field_effect(self)

    def board_marker(self, battle: Battle) -> str:
        return "重甲"


class SilverHeavyArmorSkill(Skill):
    ai_enemy_impact_requirement = False
    ai_payload_value_owned = True

    def __init__(self) -> None:
        super().__init__("silver_heavy_armor", "银制的重甲",
                         "0.5魔；仅未乘骑时使用。本人守+1，周围飞盗团武将及其召唤物免生命伤至下一敌方英雄回合结束。",
                         mana_cost=0.5, target_mode="self")

    @staticmethod
    def active_field(battle: Battle, actor: Unit) -> SilverHeavyArmorField | None:
        return next((effect for effect in battle.field_effects
                     if isinstance(effect, SilverHeavyArmorField) and effect.source_unit_id == actor.unit_id), None)

    def can_use(self, battle: Battle, actor: Unit, payload: dict[str, Any] | None = None) -> tuple[bool, str]:
        ok, reason = super().can_use(battle, actor, payload)
        if not ok:
            return ok, reason
        if battle.mounted_unit_for(actor) is not None:
            return False, "乘骑状态下不能使用银制的重甲。"
        return True, ""

    def execute(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> None:
        if battle.mounted_unit_for(actor) is not None:
            raise ActionError("乘骑状态下不能使用银制的重甲。")
        old = self.active_field(battle, actor)
        if old is not None:
            battle.remove_field_effect(old)
        battle.add_field_effect(SilverHeavyArmorField(actor.unit_id, actor.player_id), source=actor)
        battle.log_public_event(f"{actor.name} 装上银制的重甲，守+1并保护周围飞盗团。", source=actor)

    def preview(self, battle: Battle, actor: Unit) -> dict[str, Any]:
        outer = _outer_cells(battle, actor)
        covered = set(outer)
        return {"cells": positions_to_dict(battle.unit_cells(actor)),
                "secondary_cells": positions_to_dict(outer),
                "target_unit_ids": [unit.unit_id for unit in battle.all_units()
                                    if unit is not actor and unit.alive and unit.position is not None
                                    and _crew_descendant(battle, unit)
                                    and any(cell in covered for cell in battle.unit_cells(unit))],
                "requires_target": False}

    def simple_ai_cast_useful(self, battle: Battle, actor: Unit) -> bool:
        value = self.ai_payload_value(battle, actor, {})
        return value is not None and value > 0

    def ai_payload_value(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> float | None:
        if self.active_field(battle, actor) is not None or battle.mounted_unit_for(actor) is not None:
            return -1000.0
        nearby = [unit for unit in battle.all_units() if unit.alive and unit.position is not None
                  and unit is not actor and _crew_descendant(battle, unit)
                  and any(a.distance_to(b) == 1 for a in battle.unit_cells(actor) for b in battle.unit_cells(unit))]
        enemies = [unit for unit in battle.all_units() if unit.player_id != actor.player_id
                   and unit.alive and unit.position is not None]
        def threatened(unit: Unit) -> bool:
            return any(battle.distance_between_units(enemy, unit)
                       <= enemy.normal_move_distance() + enemy.targeting_range() for enemy in enemies)
        guard_value = sum((40.0 if unit.player_id == actor.player_id else -52.0)
                          for unit in nearby if threatened(unit))
        self_threatened = threatened(actor)
        if not self_threatened and guard_value == 0:
            return -1000.0
        return 12.0 + (18.0 if self_threatened else 0.0) + guard_value

    def ai_priority_score(self, context: AISkillScoreContext) -> float:
        value = self.ai_payload_value(context.battle, context.actor, context.payload)
        return value if value is not None else -1000.0


class SilverPistolSkill(Skill):
    ai_payload_value_owned = True

    def __init__(self) -> None:
        super().__init__("silver_pistol", "银制的手枪",
                         "0.5魔、每本人轮一次；范内直线单体无伤破魔吸取至多1魔。",
                         mana_cost=0.5, max_uses_per_turn=1, target_mode="unit")

    def ignores_shield_for_payload(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> bool:
        return True

    def targets(self, battle: Battle, actor: Unit) -> list[Unit]:
        if actor.position is None:
            return []
        return [unit for unit in battle.all_units() if unit.alive and unit.position is not None
                and not unit.banished
                and battle.unit_can_be_selected(unit, actor=actor)[0]
                and battle.unit_target_in_range_and_line(actor, unit, actor.targeting_range())]

    def can_use(self, battle: Battle, actor: Unit, payload: dict[str, Any] | None = None) -> tuple[bool, str]:
        ok, reason = super().can_use(battle, actor, payload)
        if not ok:
            return ok, reason
        targets = self.targets(battle, actor)
        if not targets:
            return False, "范内直线没有可选的手枪目标。"
        if payload and payload.get("target_unit_id") and not any(
                unit.unit_id == payload["target_unit_id"] for unit in targets):
            return False, "手枪目标必须在范内直线。"
        return True, ""

    def execute(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> None:
        target = payload_target_unit(battle, payload)
        if target not in self.targets(battle, actor):
            raise ActionError("银制的手枪目标已不在范内直线。")
        recipient = piercing_nondamage_recipient(battle, actor, target,
                                                 action_name=self.name, code=self.code)
        if recipient is None:
            return
        if is_mana_drain_immune(recipient):
            battle.log_public_event(f"{recipient.name} 无法被吸魔。", source=actor, target=recipient)
            return
        lost = recipient.drain_mana(1)
        gained = actor.gain_mana(lost)
        if lost:
            battle.record_skill_effect(recipient)
        battle.log_public_event(f"{actor.name} 的银制手枪从{recipient.name}吸取{lost:g}魔，回复{gained:g}魔。",
                                source=actor, target=recipient)

    def preview(self, battle: Battle, actor: Unit) -> dict[str, Any]:
        targets = self.targets(battle, actor)
        return {"cells": positions_to_dict([cell for unit in targets for cell in battle.unit_cells(unit)]),
                "target_unit_ids": [unit.unit_id for unit in targets], "secondary_cells": [], "requires_target": True}

    def ai_hostile_target_impact(self, battle: Battle, actor: Unit,
                                 payload: dict[str, Any], target: Unit) -> bool | None:
        if target.magic_immunity or target.skill_non_damage_effects_blocked():
            return False
        return (target.current_mana > 0 and not is_mana_drain_immune(target)) or target.total_shields() > 0

    def ai_payload_value(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> float | None:
        target = battle.units.get(str(payload.get("target_unit_id") or ""))
        if target is None or target not in self.targets(battle, actor):
            return -1000.0
        value = (min(1.0, target.current_mana) * (25.0 + (16.0 if actor.current_mana < actor.stat("mana") else 0.0))
                 + (18.0 if target.total_shields() > 0 else 0.0))
        if target.magic_immunity or target.skill_non_damage_effects_blocked():
            value = 0.0
        elif is_mana_drain_immune(target):
            value = 18.0 if target.total_shields() > 0 else 0.0
        return value if target.player_id != actor.player_id else -value * 1.5

    def ai_priority_score(self, context: AISkillScoreContext) -> float:
        return self.ai_payload_value(context.battle, context.actor, context.payload) or -1000.0

    def simple_ai_cast_useful(self, battle: Battle, actor: Unit) -> bool:
        return any(self.ai_payload_value(battle, actor, {"target_unit_id": unit.unit_id}) > 0
                   for unit in self.targets(battle, actor))


class FighterShipAttackPermissionTrait(Trait):
    def __init__(self) -> None:
        super().__init__("斗士乘船加攻", "斗士乘骑时，该船攻+1。")


class GunnerShipRangePermissionTrait(Trait):
    def __init__(self) -> None:
        super().__init__("炮手乘船加范", "炮手乘骑时，该船范+2。")


class GunnerShipAreaPermissionTrait(Trait):
    def __init__(self) -> None:
        super().__init__("炮手乘船区攻", "炮手乘骑时，该船普通攻击变为2×2。")


class FighterAIPolicy(ShipCrewMovePolicy):
    _simple_skill_priority = {**ShipCrewMovePolicy._simple_skill_priority,
                              "silver_heavy_armor": 2}

    @staticmethod
    def should_hold_position_for_armor(battle: Battle, actor: Unit) -> bool:
        skill = actor.get_skill("silver_heavy_armor")
        return (skill is not None and actor.current_mana >= skill.mana_cost
                and (skill.ai_payload_value(battle, actor, {}) or -1000.0) > 30.0)

    def early_move_score_bonus(self, battle: Battle, actor: Unit, destination: Position) -> float:
        if self.should_hold_position_for_armor(battle, actor):
            ship = allied_ship(battle, actor.player_id)
            return -55.0 if ship is not None and destination in battle.unit_cells(ship) else 0.0
        return super().early_move_score_bonus(battle, actor, destination)

    def tendency_boarding_destination(self, battle: Battle, actor: Unit,
                                      cells: list[Position]) -> Position | None:
        if self.should_hold_position_for_armor(battle, actor):
            ship = allied_ship(battle, actor.player_id)
            outside = [cell for cell in cells if ship is None or cell not in battle.unit_cells(ship)]
            if not outside:
                return None
            crew = [unit for unit in battle.player_units(actor.player_id) if unit is not actor
                    and unit.alive and unit.position is not None and _crew_descendant(battle, unit)]
            return max(outside, key=lambda cell: (
                sum(any(cell.distance_to(body) == 1 for body in battle.unit_cells(unit)) for unit in crew),
                -cell.distance_to(actor.position)))
        return super().tendency_boarding_destination(battle, actor, cells)


class GunnerAIPolicy(ShipCrewMovePolicy):
    _simple_skill_priority = {**ShipCrewMovePolicy._simple_skill_priority,
                              "silver_pistol": 3}
