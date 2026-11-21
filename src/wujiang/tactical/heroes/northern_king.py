"""R377 Northern King: iron soul proxy, shackle, and public damage coin."""

from __future__ import annotations

from typing import Any

from wujiang.tactical.engine.core import (
    ActionError, ActionMiss, Battle, DamageContext, Position, Skill, Stats,
    StatusEffect, Trait, Unit, direct_scoring,
)
from wujiang.tactical.heroes.base import AbstractHero
from wujiang.tactical.heroes.common import HardenSkill, KnockbackSkill, positions_to_dict
from wujiang.tactical.heroes.mechanics.half_pierce import HalfPierceAttackTrait
from wujiang.tactical.heroes.mechanics.world_seed import WorldSeedSkillEffectGuardTrait


def _present(unit: Unit | None) -> bool:
    return unit is not None and unit.alive and unit.position is not None and not unit.banished


def root_king(battle: Battle, actor: Unit) -> Unit:
    return battle.units.get(actor.summoner_id, actor) if isinstance(actor, IronSoul) else actor


def own_soul(battle: Battle, actor: Unit) -> IronSoul | None:
    root = root_king(battle, actor)
    return next((unit for unit in battle.all_units()
                 if isinstance(unit, IronSoul) and _present(unit)
                 and unit.summoner_id == root.unit_id), None)


class NorthernSkillGuardTrait(WorldSeedSkillEffectGuardTrait):
    def __init__(self) -> None:
        Trait.__init__(self, "北境技能效果免疫", "不受到伤害以外的技能效果。")

    def blocks_skill_use(self, battle: Battle, actor: Unit, skill: Skill) -> tuple[bool, str]:
        if actor is self.owner and skill.code == "harden":
            return True, f"{actor.name} 不受非伤害技能效果，无法获得变硬。"
        return False, ""


class NorthernDamageCoinTrait(Trait):
    def __init__(self) -> None:
        super().__init__("北境伤害硬币", "真正将受正数伤害时公开掷硬币：正面无效，反面伤害+1。")

    def on_before_hp_loss(self, battle: Battle, ctx: DamageContext) -> None:
        owner = self.owner
        if ctx.target is not owner or ctx.cancelled or ctx.raw_damage <= 0:
            return
        if direct_scoring():
            ctx.raw_damage += 1.0
            ctx.complete_block_chance = 1.0 - (1.0 - ctx.complete_block_chance) * 0.5
            ctx.complete_block_label = "北境伤害硬币"
            return
        if getattr(battle, "_ai_probe_active", False):
            ctx.raw_damage = round((ctx.raw_damage + 1.0) * 0.5, 4)
            return
        heads = battle.roll_chance(0.5, action_name="北境伤害硬币",
                                   beneficiary_player_id=owner.player_id)
        if heads:
            ctx.raw_damage = 0.0
            ctx.cancelled = True
            ctx.reason = f"{owner.name} 的硬币为正面，本次伤害无效。"
        else:
            ctx.raw_damage += 1.0
            ctx.reason = f"{owner.name} 的硬币为反面，本次伤害+1。"
        battle.log_public_event(ctx.reason, source=ctx.source, target=owner)


class NorthernSoulLockTrait(Trait):
    def __init__(self) -> None:
        super().__init__("铁魂魄代行", "同源铁魂魄在场时本体不能行动或连锁使用技能。")

    def blocks_skill_use(self, battle: Battle, actor: Unit, skill: Skill) -> tuple[bool, str]:
        if actor is self.owner and own_soul(battle, actor) is not None:
            return True, f"{actor.name} 的铁魂魄在场，本体不能行动。"
        return False, ""


def northern_can_take_turn_actions(self: Unit, battle: Battle) -> bool:
    return Unit.can_take_turn_actions(self, battle) and own_soul(battle, self) is None


class IronShackleSlow(StatusEffect):
    def __init__(self) -> None:
        super().__init__("铁枷锁减速", "速-2，最低1，持续目标一轮。",
                         duration=1, tick_scope="owner_turn_end")

    def modify_stat(self, stat_name: str, value: float) -> float:
        return max(1.0, value - 2.0) if stat_name == "speed" else value


class IronShackleSkill(Skill):
    ai_payload_value_owned = True
    ai_enemy_impact_requirement = True

    def __init__(self) -> None:
        super().__init__("iron_shackle", "铁枷锁",
                         "范5，无伤，无视魔免；目标尽量向本人移动2格，速-2一轮。",
                         mana_cost=0.5, max_uses_per_turn=1, target_mode="unit")

    def targets(self, battle: Battle, actor: Unit) -> list[Unit]:
        if actor.position is None:
            return []
        return [unit for unit in battle.all_units()
                if _present(unit) and unit is not actor
                and battle.unit_can_be_selected(unit, actor=actor)[0]
                and any(origin.distance_to(cell) <= 5
                        and battle.cells_are_straight_aligned(origin, cell)
                        for origin in battle.unit_cells(actor) for cell in battle.unit_cells(unit))]

    def direct_unit_target_range(self, battle: Battle, actor: Unit,
                                 payload: dict[str, Any] | None = None) -> int:
        return 5

    def target(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> Unit:
        target = battle.units.get(str(payload.get("target_unit_id") or ""))
        if target not in self.targets(battle, actor):
            raise ActionError("铁枷锁需要选择范5内直线合法单位。")
        return target

    def ignores_magic_immunity_for_payload(self, battle: Battle, actor: Unit,
                                           payload: dict[str, Any]) -> bool:
        return True

    def queued_payload_metadata(self, battle: Battle, actor: Unit,
                                payload: dict[str, Any]) -> dict[str, Any]:
        return {**super().queued_payload_metadata(battle, actor, payload),
                "ignore_magic_immunity": True}

    def preview(self, battle: Battle, actor: Unit) -> dict[str, Any]:
        targets = self.targets(battle, actor)
        return {"cells": positions_to_dict([cell for unit in targets for cell in battle.unit_cells(unit)]),
                "target_unit_ids": [unit.unit_id for unit in targets],
                "secondary_cells": [], "requires_target": True}

    def get_target_cells_for_payload(self, battle: Battle, actor: Unit,
                                     payload: dict[str, Any]) -> list[Position]:
        return battle.unit_cells(self.target(battle, actor, payload))

    def get_target_units_for_payload(self, battle: Battle, actor: Unit,
                                     payload: dict[str, Any]) -> list[Unit]:
        return [self.target(battle, actor, payload)]

    def execute(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> None:
        target = self.target(battle, actor, payload)
        checked = battle.validate_target(actor, target, action_name=self.name,
                                         is_skill=True, is_hostile=target.player_id != actor.player_id,
                                         ignore_magic_immunity=True)
        if checked.cancelled:
            battle.log_public_event(checked.reason or "铁枷锁被防御。", source=actor, target=target)
            return
        recipient = checked.target
        if not getattr(recipient, "is_terrain", False):
            for _ in range(2):
                if recipient.position is None or actor.position is None:
                    break
                dx = (actor.position.x > recipient.position.x) - (actor.position.x < recipient.position.x)
                dy = (actor.position.y > recipient.position.y) - (actor.position.y < recipient.position.y)
                destination = recipient.position.offset(dx, dy)
                if not battle.can_place_unit(recipient, destination, ignore=recipient, mover=recipient):
                    break
                try:
                    battle.move_unit(recipient, destination, via_skill=True, forced=True,
                                     max_distance=2, tags={"iron_shackle"})
                except ActionError:
                    break
            recipient.add_status(IronShackleSlow(), source=actor)
        battle.log_public_event(f"{actor.name} 的铁枷锁命中 {recipient.name}。",
                                source=actor, target=recipient)

    def ai_payload_candidates(self, battle: Battle, actor: Unit,
                              action: dict[str, Any]) -> list[dict[str, Any]]:
        return [{"type": "skill", "unit_id": actor.unit_id, "skill_code": self.code,
                 "target_unit_id": unit.unit_id, "x": unit.position.x, "y": unit.position.y}
                for unit in self.targets(battle, actor)]

    def ai_candidate_payloads(self, context: Any) -> list[dict[str, Any]]:
        return self.ai_payload_candidates(context.battle, context.actor, context.action)

    def ai_payload_value(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> float:
        try:
            target = self.target(battle, actor, payload)
        except ActionError:
            return -1000.0
        if getattr(target, "is_terrain", False):
            return -1000.0
        if target.player_id == actor.player_id:
            return -80.0
        if target.total_shields() > 0 or target.dodge_charges > 0:
            return -20.0
        distance = battle.distance_between_units(actor, target)
        return 34.0 + (28.0 if distance > 1 else 0.0)

    def ai_effective_enemy_impact(self, battle: Battle, actor: Unit,
                                  payload: dict[str, Any], targets: list[Unit] | None = None,
                                  shared: Any = None, action: dict[str, Any] | None = None) -> bool:
        return self.ai_payload_value(battle, actor, payload) > 0

    def ai_priority_score(self, context: Any) -> float:
        return self.ai_payload_value(context.battle, context.actor, context.payload)


class IronSoulSkill(Skill):
    ai_payload_value_owned = True
    ai_enemy_impact_requirement = False

    def __init__(self) -> None:
        super().__init__("iron_soul", "铁魂魄", "每本人轮一次；范3空格召唤一具可当轮行动的铁魂魄。",
                         max_uses_per_turn=1, target_mode="cell")

    def cells(self, battle: Battle, actor: Unit) -> list[Position]:
        if actor.position is None or own_soul(battle, actor) is not None:
            return []
        probe = IronSoul(actor.player_id)
        return [cell for y in range(battle.height) for x in range(battle.width)
                for cell in (Position(x, y),)
                if not battle.units_at(cell)
                and battle.can_place_unit(probe, cell, mover=probe)
                and any(origin.distance_to(cell) <= 3
                        and battle.cells_are_straight_aligned(origin, cell)
                        for origin in battle.unit_cells(actor))]

    def can_use(self, battle: Battle, actor: Unit,
                payload: dict[str, Any] | None = None) -> tuple[bool, str]:
        ok, reason = super().can_use(battle, actor, payload)
        if not ok:
            return ok, reason
        if own_soul(battle, actor) is not None:
            return False, "同源铁魂魄仍在场上。"
        return True, ""

    def chosen_cell(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> Position:
        try:
            cell = Position(int(payload["x"]), int(payload["y"]))
        except (KeyError, TypeError, ValueError) as exc:
            raise ActionError("铁魂魄需要选择合法空格。") from exc
        if cell not in self.cells(battle, actor):
            raise ActionError("铁魂魄所选格不是范3内合法空格。")
        return cell

    def preview(self, battle: Battle, actor: Unit) -> dict[str, Any]:
        return {"cells": positions_to_dict(self.cells(battle, actor)),
                "target_unit_ids": [], "secondary_cells": [], "requires_target": True}

    def get_target_cells_for_payload(self, battle: Battle, actor: Unit,
                                     payload: dict[str, Any]) -> list[Position]:
        return [self.chosen_cell(battle, actor, payload)]

    def execute(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> None:
        try:
            cell = self.chosen_cell(battle, actor, payload)
        except ActionError as exc:
            raise ActionMiss(str(exc)) from exc
        soul = IronSoul(actor.player_id)
        battle.summon_unit(soul, cell, summoner=root_king(battle, actor))
        soul.turn_ready = True
        battle.log_public_event(f"{actor.name} 在 ({cell.x},{cell.y}) 召唤铁魂魄，本体停手。",
                                source=actor, target=soul)

    def ai_payload_candidates(self, battle: Battle, actor: Unit,
                              action: dict[str, Any]) -> list[dict[str, Any]]:
        return [{"type": "skill", "unit_id": actor.unit_id, "skill_code": self.code,
                 "x": cell.x, "y": cell.y} for cell in self.cells(battle, actor)]

    def ai_candidate_payloads(self, context: Any) -> list[dict[str, Any]]:
        return self.ai_payload_candidates(context.battle, context.actor, context.action)

    def ai_payload_value(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> float:
        try:
            cell = self.chosen_cell(battle, actor, payload)
        except ActionError:
            return -1000.0
        enemies = [unit for unit in battle.enemy_units(actor.player_id) if _present(unit)]
        if not enemies:
            return -1000.0
        distance = min(cell.distance_to(enemy.position) for enemy in enemies)
        return 100.0 - distance * 9.0

    def ai_priority_score(self, context: Any) -> float:
        return self.ai_payload_value(context.battle, context.actor, context.payload)


class IronSoul(AbstractHero):
    hero_code = "northern_iron_soul"
    hero_name = "铁魂魄"
    level = 4
    attribute = "土"
    race = "灵体"
    role = "狂战"
    base_stats = Stats(attack=4, defense=0.5, speed=4, attack_range=1, mana=2)
    stat_minimums = {"defense": 0.5}
    raw_skill_text = "变硬 铁枷锁 震开 铁魂魄"
    raw_trait_text = "不受到伤害以外的技能效果；受到伤害时掷硬币；普攻半破魔"

    def __init__(self, player_id: int) -> None:
        super().__init__(player_id, is_summon=True)

    def build_skills(self) -> list[Skill]:
        return [HardenSkill(), IronShackleSkill(), KnockbackSkill(), IronSoulSkill()]

    def build_traits(self) -> list[Trait]:
        return [NorthernSkillGuardTrait(), NorthernDamageCoinTrait(), HalfPierceAttackTrait()]
