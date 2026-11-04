"""R260 barbarian king battlefield skills and trait."""

from __future__ import annotations

from typing import Any

from wujiang.tactical.engine.core import (
    ActionError, Battle, DamageContext, HealContext, Position, QueuedAction, Skill,
    StatusEffect, Trait, Unit,
)
from wujiang.tactical.heroes.ai_policy import AISkillScoreContext
from wujiang.tactical.heroes.common import (
    DashMoveSkill, ensure_distance, line_patterns, match_payload_pattern,
    pattern_selection_preview, payload_target_unit, positions_to_dict,
)
from wujiang.tactical.heroes.mechanics.next_five_geometry import ALL_DIRECTIONS

class BarbarianAxeSkill(Skill):
    ai_candidate_requires_explicit_legality = True
    def ai_attack_power(self, battle, actor, payload, target, cells) -> float:
        return actor.stat("attack") - (1.0 if payload.get("choice_code") == "weak" else 0.0)

    bypasses_block_counter = True

    def ai_priority_score(self, context: AISkillScoreContext) -> float:
        battle, actor, payload = context.battle, context.actor, context.payload
        try:
            cells = self.chosen_cells(battle, actor, payload)
        except ActionError:
            return -1000.0
        power = actor.stat("attack") - int(self.weak(payload))
        score, enemy_hits = 0.0, 0
        for target in battle.effect_units_at_cells(cells):
            if target.magic_immunity or target.dodge_charges > 0:
                continue
            impact = min(target.current_hp, context.estimate_damage(target, power)) * 105.0
            impact += 9.0 if target.total_shields() else 0.0
            if target.player_id == actor.player_id:
                score -= impact * 1.5
            else:
                score += impact
                enemy_hits += 1
        if not enemy_hits:
            return -1000.0
        return score - (12.0 if self.weak(payload) else 27.0)

    def __init__(self) -> None:
        super().__init__("barbarian_axe", "疾风战斧", "贴身八方向四格直线伤双方；可选1.5魔完整攻或0.5魔攻-1；无视免费格挡反击。",
                         mana_cost=1.5, target_mode="cell")

    def weak(self, payload: dict[str, Any] | None) -> bool:
        return bool(payload and payload.get("choice_code") == "weak")

    def mana_cost_text(self) -> str:
        return "完整1.5魔／减攻1点费0.5魔"

    def can_use(self, battle: Battle, actor: Unit, payload: dict[str, Any] | None = None) -> tuple[bool, str]:
        if payload and payload.get("choice_code") not in {None, "full", "weak"}:
            return False, "请选择疾风战斧的完整或省魔模式。"
        # Action discovery has no chosen pattern yet. Show the skill whenever
        # the affordable weak mode can be declared; submitted choices pay exactly.
        probe = payload if payload and (payload.get("choice_code") is not None or payload.get("cells")) else {"choice_code": "weak"}
        return super().can_use(battle, actor, probe)

    def mana_cost_for_payload(self, battle: Battle, actor: Unit, payload: dict[str, Any] | None = None) -> float:
        if not self.weak(payload):
            return super().mana_cost_for_payload(battle, actor, payload)
        cost = 0.5
        for component in list(actor.iter_components()):
            if component is not self:
                cost = component.modify_skill_mana_cost(battle, actor, self, payload, cost)
        return max(0.0, float(cost))

    def patterns(self, battle: Battle, actor: Unit) -> list[list[Position]]:
        return line_patterns(battle, actor.position, ALL_DIRECTIONS, 4)

    def chosen_cells(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> list[Position]:
        return match_payload_pattern(payload, self.patterns(battle, actor))

    def execute(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> None:
        cells = self.chosen_cells(battle, actor, payload)
        for target in battle.effect_units_at_cells(cells):
            battle.resolve_damage(DamageContext(source=actor, target=target,
                attack_power=actor.stat("attack") - int(self.weak(payload)), is_skill=True,
                action_name=self.name, tags={"skill", "attack", self.code}))

    def preview(self, battle: Battle, actor: Unit) -> dict[str, Any]:
        preview = pattern_selection_preview(self.patterns(battle, actor))
        patterns = preview["selection"]["patterns"]
        preview.update({"target_unit_ids": [], "secondary_cells": [], "requires_target": True,
                        "selection": {"mode": "choice_pattern", "ordered": False,
                            "choices": [{"code": "full", "label": "1.5魔：完整攻值", "patterns": patterns},
                                        {"code": "weak", "label": "0.5魔：攻值-1", "patterns": patterns}],
                            "choice_prompt": "选择疾风战斧的费用和威力。"}})
        return preview

    def get_target_cells_for_payload(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> list[Position]:
        return self.chosen_cells(battle, actor, payload)

    def get_target_units_for_payload(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> list[Unit]:
        return battle.effect_units_at_cells(self.chosen_cells(battle, actor, payload))


class BarbarianHarassSkill(Skill):
    def ai_priority_score(self, context: AISkillScoreContext) -> float:
        battle, actor = context.battle, context.actor
        target = battle.units.get(str(context.payload.get("target_unit_id") or ""))
        if target is None or target.player_id == actor.player_id:
            return -1000.0
        recipient = battle.effect_recipient(target)
        score = context.damage_score(self)
        if recipient.name != "野人" and recipient.total_shields() == 0 and not recipient.magic_immunity:
            score += 32.0
        return score + (9.0 if self.code.endswith("_free") else -6.0)

    def __init__(self, *, free: bool = False) -> None:
        super().__init__("barbarian_harass_free" if free else "barbarian_harass", "人身骚扰（免费）" if free else "人身骚扰",
                         "近身技能伤害；真正命中后将实际受体本场显示名改为野人。每本人轮一次免费档。",
                         mana_cost=0 if free else 0.5, max_uses_per_turn=1 if free else None,
                         target_mode="unit")

    def execute(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> None:
        target = payload_target_unit(battle, payload)
        if target is actor:
            raise ActionError("人身骚扰必须选择自身以外的单位。")
        if not battle.is_declared_resolution(actor, payload, {"skill"}):
            ensure_distance(actor, target, 1)
        hit = battle.resolve_damage(DamageContext(source=actor, target=target,
            attack_power=actor.stat("attack"), is_skill=True, action_name="人身骚扰",
            tags={"skill", "attack", self.code}))
        if hit.actual_damage > 0 and hit.target.alive and not hit.target.banished and hit.target.name != "野人":
            old_name = hit.target.name
            hit.target.name = "野人"
            battle.log_public_event(f"{old_name} 被人身骚扰击中，本场名字变为【野人】。", source=actor, target=hit.target)

    def preview(self, battle: Battle, actor: Unit) -> dict[str, Any]:
        targets = [unit for unit in battle.all_units()
                   if unit is not actor and unit.alive and not unit.banished
                   and unit.position is not None and actor.position is not None
                   and battle.distance_between_units(actor, unit) <= 1]
        return {"cells": positions_to_dict([cell for unit in targets for cell in battle.unit_cells(unit)]),
                "target_unit_ids": [unit.unit_id for unit in targets], "secondary_cells": [],
                "requires_target": True}

    def get_target_units_for_payload(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> list[Unit]:
        return [payload_target_unit(battle, payload)]


class BarbarianGluttonyStatus(StatusEffect):
    DISPLACEMENT_CODES = frozenset({
        "leap", "fly_leap", "fate_kick", "crazy_sand", "plasma_thruster", "mounted_leap",
        "jirobo_follow_step", "zero_dash", "fuma_pursuit", "true_blade_air_slash",
        "iron_chain_path", "ghost_step", "frey_quick_flash", "flash_slash", "bird_soul",
        "bird_soul_free", "hell_teleport", "shield_charge", "fantasy_move",
    })

    def __init__(self, grant_turn: int) -> None:
        super().__init__("暴食", "守+3、速固定为1，不能使用位移技能；至下一实际本人回合结束。")
        self.grant_turn = grant_turn

    def modify_stat(self, stat_name: str, value: float) -> float:
        if stat_name == "defense":
            return value + 3
        if stat_name == "speed":
            return 1.0
        return value

    def blocks_skill_use(self, battle: Battle, actor: Unit, skill: Skill) -> tuple[bool, str]:
        if skill.code in self.DISPLACEMENT_CODES or isinstance(skill, DashMoveSkill):
            return True, "暴食期间不能使用位移技能。"
        return False, ""

    def on_owner_turn_end(self, battle: Battle) -> None:
        if self.owner is not None and battle.turn_number != self.grant_turn:
            self.owner.remove_status(self, battle)


class BarbarianGluttonySkill(Skill):
    def ai_priority_score(self, context: AISkillScoreContext) -> float:
        battle, actor = context.battle, context.actor
        missing = 0.0 if actor.cannot_heal else max(0.0, actor.max_health - actor.current_hp)
        enemies = list(context.hostile_units())
        danger = any(enemy.position is not None and battle.distance_between_units(enemy, actor)
                     <= enemy.normal_move_distance() + enemy.targeting_range() for enemy in enemies)
        gain = missing * (220.0 if actor.current_hp <= 0.5 else 150.0)
        gain += 32.0 if danger else 0.0
        gain -= 28.0 if actor.position is not None and any(enemy.position is not None
                     and battle.distance_between_units(enemy, actor) > 1 for enemy in enemies) else 0.0
        return gain - 12.0 if gain > 28.0 else -1000.0

    def __init__(self) -> None:
        super().__init__("barbarian_gluttony", "暴食", "每两本人轮一次；恢复至满血，至下一本人回合结束前守+3、速固定1且不能用位移技能。",
                         cooldown_turns=2, target_mode="self")

    def execute(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> None:
        battle.heal(HealContext(source=actor, target=actor,
                                amount=max(0.0, actor.max_health - actor.current_hp),
                                action_name=self.name, tags={"skill"}))
        old = actor.get_status("暴食")
        if old is not None:
            actor.remove_status(old, battle)
        actor.add_status(BarbarianGluttonyStatus(battle.turn_number), source=actor)
        battle.log_public_event(f"{actor.name} 暴食：守+3、速变为1，暂不能使用位移技能。", source=actor, target=actor)

    def preview(self, battle: Battle, actor: Unit) -> dict[str, Any]:
        return {"cells": positions_to_dict(battle.unit_cells(actor)), "target_unit_ids": [actor.unit_id],
                "secondary_cells": [], "requires_target": False}


class BarbarianWildmanTrait(Trait):
    def __init__(self) -> None:
        super().__init__("野人压制", "显示名恰为野人的单位伤害本体时威力-2，且不能连锁本体的动作。")

    def on_before_damage(self, battle: Battle, ctx: DamageContext) -> None:
        if self.owner is ctx.target and ctx.source is not None and ctx.source.name == "野人":
            if ctx.raw_damage is None:
                ctx.attack_power = max(0.0, ctx.attack_power - 2)
            else:
                ctx.raw_damage = max(0.0, ctx.raw_damage - 2)
            ctx.modifier_receipts.add("barbarian_wildman_reduction")

    def blocks_target_reaction(self, battle: Battle, reactor: Unit,
                               queued_action: QueuedAction) -> bool:
        return (reactor.name == "野人" and queued_action.action_type in
                {"attack", "skill", "skill_effect"})


