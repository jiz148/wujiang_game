"""R358 ninja combo, team ultimate support, and stationary defense."""

from __future__ import annotations

from typing import Any

from wujiang.tactical.engine.core import ActionError, Battle, DamageContext, Position, Skill, StatusEffect, Trait, Unit
from wujiang.tactical.heroes.common import StatModifierStatus, payload_position


def _present(unit: Unit) -> bool:
    return unit.alive and not unit.banished and unit.position is not None


class NinjaExtraCastTrait(Trait):
    """A paid ordinary cast opens two nonrecursive, same-skill free casts."""

    def __init__(self) -> None:
        super().__init__("忍法连施", "使用非大招技能后，本轮可额外免费重用同一技能两次；不占原本每轮次数。")
        self.charges: dict[str, int] = {}
        self.charges_turn = 0

    def _sync(self, battle: Battle) -> None:
        if self.charges_turn != battle.turn_number:
            self.charges.clear()
            self.charges_turn = battle.turn_number

    def extra_turn_skill_use_available(self, battle: Battle, actor: Unit, skill: Skill) -> bool:
        return (actor is self.owner and not skill.is_ultimate and self.charges_turn == battle.turn_number
                and self.charges.get(skill.code, 0) > 0)

    def consume_extra_turn_skill_use(self, battle: Battle, actor: Unit, skill: Skill) -> None:
        self._sync(battle)
        self.charges[skill.code] -= 1
        battle.log(f"{actor.name} 以【忍法连施】免费重用【{skill.name}】，剩余 {self.charges[skill.code]} 次。")

    def on_own_skill_prepaid(self, battle: Battle, actor: Unit, skill: Skill,
                             *, used_extra_turn_use: bool) -> None:
        if actor is not self.owner or skill.is_ultimate or used_extra_turn_use:
            return
        self._sync(battle)
        self.charges[skill.code] = self.charges.get(skill.code, 0) + 2
        battle.log(f"{actor.name} 使用【{skill.name}】后，本轮可免费重用该技能两次。")


class NinjaShurikenDefenseStatus(StatModifierStatus):
    def __init__(self) -> None:
        super().__init__("爆破手里剑削守", defense_delta=-1,
                         description="本行动英雄回合守-1，最低为1。", duration=1, tick_scope="any_turn_end")


class NinjaExplosiveShurikenSkill(Skill):
    ai_payload_value_owned = True

    def __init__(self) -> None:
        super().__init__("ninja_explosive_shuriken", "忍法。爆破手里剑",
                         "本人轮一次：范2点选1格，伤2；命中单位本轮守-1，到1。",
                         max_uses_per_turn=1, target_mode="cell")

    def target_cell(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> Position:
        cell = payload_position(payload)
        if not battle.in_bounds(cell) or battle.unit_distance_to_cell(actor, cell) > 2:
            raise ActionError("目标超出爆破手里剑范围。")
        return cell

    def preview(self, battle: Battle, actor: Unit) -> dict[str, Any]:
        cells = [Position(x, y).to_dict() for x in range(battle.width) for y in range(battle.height)
                 if battle.unit_distance_to_cell(actor, Position(x, y)) <= 2]
        return {"cells": cells, "target_unit_ids": [], "secondary_cells": [], "requires_target": True}

    def get_target_cells_for_payload(self, battle: Battle, actor: Unit,
                                     payload: dict[str, Any]) -> list[Position]:
        return [self.target_cell(battle, actor, payload)]

    def queued_payload_metadata(self, battle: Battle, actor: Unit,
                                payload: dict[str, Any]) -> dict[str, Any]:
        return {"declared_area_cells": [self.target_cell(battle, actor, payload).to_dict()]}

    def execute(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> None:
        cells = battle.payload_positions(payload, "declared_area_cells")
        cell = cells[0] if cells else self.target_cell(battle, actor, payload)
        for target in list(battle.units_at(cell)):
            if not _present(target):
                continue
            ctx = battle.resolve_damage(DamageContext(
                source=actor, target=target, attack_power=2, is_skill=True,
                action_name=self.name, tags={"skill", self.code},
                area_cell_hits=battle.unit_hit_count_for_cells(target, [cell]),
            ))
            if (target.alive and not ctx.cancelled and not ctx.shield_consumed
                    and not target.skill_non_damage_effects_blocked()):
                if target.stat("defense") > 1:
                    target.add_status(NinjaShurikenDefenseStatus(), source=actor)
                    battle.record_skill_effect(target)
                    battle.log(f"{target.name} 被【爆破手里剑】击中，本轮守-1。")

    def ai_effect_units(self, battle: Battle, actor: Unit,
                        payload: dict[str, Any]) -> list[Unit]:
        try:
            return [unit for unit in battle.units_at(self.target_cell(battle, actor, payload)) if _present(unit)]
        except (ActionError, KeyError, TypeError, ValueError):
            return []

    def ai_effective_enemy_impact(self, battle: Battle, actor: Unit, payload: dict[str, Any],
                                  targets: list[Unit] | None = None, shared: Any = None,
                                  action: dict[str, Any] | None = None) -> bool:
        return any(unit.player_id != actor.player_id and not unit.skill_non_damage_effects_blocked()
                   for unit in (targets if targets is not None else self.ai_effect_units(battle, actor, payload)))

    def ai_payload_value(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> float:
        value = 0.0
        for target in self.ai_effect_units(battle, actor, payload):
            if target.skill_non_damage_effects_blocked():
                continue
            sign = 1 if target.player_id != actor.player_id else -1
            damage = min(target.current_hp, battle.damage_rule.calculate_damage(2, target.stat("defense")))
            debuff = 18.0 if target.stat("defense") > 1 else 0.0
            value += sign * (damage * 95.0 + debuff)
        return value - 9.0

    def ai_priority_score(self, context: Any) -> float:
        return self.ai_payload_value(context.battle, context.actor, context.payload)


class NinjaFluteSpeedStatus(StatModifierStatus):
    def __init__(self) -> None:
        super().__init__("忍法魔笛加速", speed_delta=1, description="当前行动英雄回合速+1。",
                         duration=1, tick_scope="any_turn_end")


class NinjaFluteReuseStatus(StatusEffect):
    def __init__(self, eligible_codes: set[str]) -> None:
        super().__init__("忍法魔笛补用", "可再使用一次魔笛施放时已经使用过的大招。")
        self.eligible_codes = frozenset(eligible_codes)

    def additional_battle_skill_uses(self, battle: Battle, actor: Unit, skill: Skill) -> int:
        return int(actor is self.owner and skill.is_ultimate and skill.code in self.eligible_codes)

    def consume_additional_battle_skill_use(self, battle: Battle, actor: Unit, skill: Skill) -> None:
        actor.remove_status(self, battle)
        battle.log(f"{actor.name} 用【忍法。魔笛】补用了一次【{skill.name}】。")

    def on_owner_banished(self, battle: Battle) -> None:
        if self.owner is not None:
            self.owner.remove_status(self, battle)

    def on_owner_removed(self, battle: Battle) -> None:
        if self.owner is not None:
            self.owner.remove_status(self, battle)


class NinjaMagicFluteSkill(Skill):
    ai_payload_value_owned = True
    ai_enemy_impact_requirement = False

    def __init__(self) -> None:
        super().__init__("ninja_magic_flute", "忍法。魔笛",
                         "大招：其他在场己方刺客本轮速+1，并各可补用一次已使用过的大招。",
                         max_uses_per_battle=1, target_mode="self")

    def beneficiaries(self, battle: Battle, actor: Unit) -> list[Unit]:
        return [unit for unit in battle.all_units()
                if unit is not actor and unit.player_id == actor.player_id
                and _present(unit) and getattr(unit, "role", "") == "刺客"]

    def execute(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> None:
        for ally in self.beneficiaries(battle, actor):
            ally.add_status(NinjaFluteSpeedStatus(), source=actor)
            eligible = {skill.code for skill in ally.action_skills()
                        if skill.is_ultimate and skill.max_uses_per_battle is not None
                        and skill.uses_this_battle >= skill.max_uses_per_battle}
            if eligible:
                ally.add_status(NinjaFluteReuseStatus(eligible), source=actor)
            battle.record_skill_effect(ally)
            battle.log(f"{ally.name} 获得【忍法。魔笛】速+1{('及一次已用大招补用' if eligible else '')}。")

    def ai_payload_candidates(self, battle: Battle, actor: Unit,
                              action: dict[str, Any]) -> list[dict[str, Any]]:
        if self.ai_payload_value(battle, actor, {}) <= 0:
            return []
        return [{"type": "skill", "unit_id": actor.unit_id, "skill_code": self.code}]

    def ai_candidate_payloads(self, context: Any) -> list[dict[str, Any]]:
        return self.ai_payload_candidates(context.battle, context.actor, context.action)

    def ai_payload_value(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> float:
        value = 0.0
        for ally in self.beneficiaries(battle, actor):
            value += 8.0 if not ally.has_status("忍法魔笛加速") else 0.0
            if any(skill.is_ultimate and skill.max_uses_per_battle is not None
                   and skill.uses_this_battle >= skill.max_uses_per_battle
                   for skill in ally.action_skills()):
                value += 95.0
        return value - 18.0

    def ai_priority_score(self, context: Any) -> float:
        return self.ai_payload_value(context.battle, context.actor, context.payload)


class NinjaGroundLotusStatus(StatusEffect):
    def __init__(self) -> None:
        super().__init__("忍法坐地莲", "下个己方回合前不能移动或用本人位移技能，且不受伤害。",
                         duration=1, tick_scope="owner_turn_start")

    def can_use_normal_move(self, battle: Battle, actor: Unit) -> tuple[bool, str]:
        return False, "坐地莲期间不能普通移动。"

    def blocks_skill_use(self, battle: Battle, actor: Unit, skill: Skill) -> tuple[bool, str]:
        if skill.moves_owner_on_use(battle, actor, {}):
            return True, "坐地莲期间不能使用本人位移技能。"
        return False, ""

    def on_final_hp_loss(self, battle: Battle, ctx: DamageContext) -> None:
        if ctx.target is self.owner and ctx.raw_damage > 0:
            ctx.raw_damage = 0.0
            battle.log(f"{ctx.target.name} 的【忍法。坐地莲】挡下了伤害。")


class NinjaGroundLotusSkill(Skill):
    ai_payload_value_owned = True
    ai_enemy_impact_requirement = False

    def __init__(self) -> None:
        super().__init__("ninja_ground_lotus", "忍法。坐地莲",
                         "大招：至下个己方回合前无法移动/使用本人位移技能，且不受伤害。",
                         max_uses_per_battle=1, target_mode="self")

    def execute(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> None:
        actor.add_status(NinjaGroundLotusStatus(), source=actor)
        battle.record_skill_effect(actor)
        battle.log(f"{actor.name} 使用【忍法。坐地莲】，定点免伤至下个己方回合。")

    def ai_payload_candidates(self, battle: Battle, actor: Unit,
                              action: dict[str, Any]) -> list[dict[str, Any]]:
        return [{"type": "skill", "unit_id": actor.unit_id, "skill_code": self.code}]

    def ai_candidate_payloads(self, context: Any) -> list[dict[str, Any]]:
        return self.ai_payload_candidates(context.battle, context.actor, context.action)

    def ai_payload_value(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> float:
        if actor.has_status("忍法坐地莲"):
            return -1000.0
        threats = [enemy for enemy in battle.enemy_units(actor.player_id) if _present(enemy)
                   and battle.unit_distance_to_cell(enemy, actor.position) <= enemy.targeting_range() + 1]
        if not threats:
            return -35.0
        risk = sum(max(0.0, enemy.stat("attack") - actor.stat("defense")) for enemy in threats)
        return 22.0 + min(220.0, risk * 45.0) - (18.0 if not actor.move_used else 0.0)

    def ai_priority_score(self, context: Any) -> float:
        return self.ai_payload_value(context.battle, context.actor, context.payload)
