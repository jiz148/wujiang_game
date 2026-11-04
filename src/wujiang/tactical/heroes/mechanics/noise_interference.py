"""R094 noise wave and interference."""

from __future__ import annotations

from wujiang.tactical.heroes.ai_candidate_plan import (
    DAMAGE_DEDUPED_RANKED_CANDIDATES,
    UNIT_DEDUPED_RANKED_CANDIDATES,
)

from typing import Any
from wujiang.tactical.engine.core import Battle
from wujiang.tactical.engine.core import DamageContext
from wujiang.tactical.engine.core import HeroUnit
from wujiang.tactical.engine.core import Position
from wujiang.tactical.engine.core import Skill
from wujiang.tactical.heroes.ai_policy import AISkillScoreContext
from wujiang.tactical.heroes.ai_impact_rule import ReviewedR15ImpactMixin
from wujiang.tactical.heroes.common import StatModifierStatus
from wujiang.tactical.heroes.common import match_payload_pattern
from wujiang.tactical.heroes.common import pattern_selection_preview
from wujiang.tactical.heroes.mechanics.true_blade_bird import skill_has_movement_effect
from wujiang.tactical.heroes.mechanics.next_five_effects import apply_piercing_status_effect
from wujiang.tactical.heroes.mechanics.next_five_effects import damage_followup_effect_applies
from wujiang.tactical.heroes.mechanics.next_five_effects import remote_rectangle_patterns


class NoiseWaveStatus(StatModifierStatus):
    def __init__(self, applied_turn_number: int | None = None) -> None:
        super().__init__(
            "乱音电波",
            speed_delta=-1,
            description="速 -1，且不能使用带有位移效果的技能，直到下回合结束前。",
            duration=1,
            tick_scope="owner_turn_end",
        )
        self.applied_turn_number = applied_turn_number

    def on_owner_turn_end(self, battle: Battle) -> None:
        if battle.turn_number != self.applied_turn_number:
            super().on_owner_turn_end(battle)

    def blocks_skill_use(self, battle: Battle, actor: HeroUnit, skill: Skill) -> tuple[bool, str]:
        owner = self.owner
        if owner is not None and actor.unit_id == owner.unit_id and skill_has_movement_effect(skill):
            return True, "乱音电波状态下不能使用带有位移效果的技能。"
        return False, ""


class NoiseWaveSkill(ReviewedR15ImpactMixin, Skill):
    def ai_r15_control_value(self, battle: Battle, target: HeroUnit) -> float:
        if target.has_status("乱音电波"):
            return 0.0
        useful = [skill for skill in target.skills
                  if not target.cannot_use_skills and not getattr(skill, "locked", False) and skill.cooldown_remaining <= 0
                  and (skill.max_uses_per_battle is None or skill.uses_this_battle < skill.max_uses_per_battle)
                  and skill.mana_cost <= target.current_mana
                  and not any(component.blocks_skill_use(battle, target, skill)[0] for component in target.iter_components())]
        slow = 14.0 if target.stat("speed") > 1 and not target.cannot_move and not target.cannot_normal_move else 0.0
        return slow + 24.0 * sum(skill_has_movement_effect(skill) for skill in useful)

    def ai_r15_post_effect_bonus(self, actor, unit, *, alive, player, mana, value, statuses, control):
        return control if "乱音电波" not in statuses and unit.has_status("乱音电波") else 0.0

    def ai_priority_score(self, context: AISkillScoreContext) -> float:
        return context.r15_effect_score(self)

    ai_candidate_plan = UNIT_DEDUPED_RANKED_CANDIDATES
    def __init__(self) -> None:
        super().__init__(
            "noise_wave",
            "乱音电波",
            "普通技能：费 1 魔，每回合最多 1 次；3*3 无伤害破魔效果，命中单位速 -1 且不能使用位移技能直到下回合结束前。",
            mana_cost=1,
            max_uses_per_turn=1,
            target_mode="cell",
        )

    def patterns(self, battle: Battle, actor: HeroUnit) -> list[list[Position]]:
        return remote_rectangle_patterns(battle, actor, 3, 3)

    def chosen_cells(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> list[Position]:
        return match_payload_pattern(payload, self.patterns(battle, actor))

    def execute(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> None:
        cells = self.chosen_cells(battle, actor, payload)
        seen: set[str] = set()
        for unit in battle.units_at_cells(cells):
            unit = battle.effect_recipient(unit)
            if unit.unit_id in seen:
                continue
            seen.add(unit.unit_id)
            if unit.magic_immunity:
                continue
            apply_piercing_status_effect(
                battle,
                actor,
                unit,
                action_name="乱音电波",
                status=NoiseWaveStatus(battle.turn_number),
                is_skill=True,
                tags={"skill", "noise_wave"},
                ignore_targeting_restrictions=True,
            )

    def preview(self, battle: Battle, actor: HeroUnit) -> dict[str, Any]:
        preview = pattern_selection_preview(self.patterns(battle, actor))
        cell_keys = {(cell["x"], cell["y"]) for cell in preview["cells"]}
        targets = [
            unit.unit_id
            for unit in battle.all_units()
            if battle.unit_can_be_selected(unit, actor=actor)[0]
            and any((cell.x, cell.y) in cell_keys for cell in battle.unit_cells(unit))
        ]
        preview.update({"target_unit_ids": targets, "secondary_cells": [], "requires_target": True})
        return preview

    def get_target_cells_for_payload(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> list[Position]:
        return self.chosen_cells(battle, actor, payload)

    def get_target_units_for_payload(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> list[HeroUnit]:
        return battle.units_at_cells(self.chosen_cells(battle, actor, payload))  # type: ignore[return-value]

    def ignores_shield_for_payload(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> bool:
        return True


class InterferenceSkill(ReviewedR15ImpactMixin, Skill):
    def ai_r15_post_effect_bonus(self, actor, unit, *, alive, player, mana, value, statuses, control):
        if alive and unit.alive and player != actor.player_id and unit.player_id == actor.player_id:
            return 70.0 + value * 0.55
        return 0.0

    def ai_hostile_target_impact(self, battle, actor, payload, target):
        return target.is_clone or (target.is_summon and target.player_id != actor.player_id)

    def ai_priority_score(self, context: AISkillScoreContext) -> float:
        return context.r15_effect_score(self)

    ai_candidate_plan = DAMAGE_DEDUPED_RANKED_CANDIDATES
    def __init__(self) -> None:
        super().__init__(
            "interference",
            "干扰",
            "普通技能：2 轮一次，远程10*10；双方受当前攻普通技能伤害，有效命中破坏分身/复制体并夺取存活召唤物；保留原召唤来源、状态和回合槽。",
            cooldown_turns=2,
            target_mode="cell",
        )

    def patterns(self, battle: Battle, actor: HeroUnit) -> list[list[Position]]:
        return remote_rectangle_patterns(battle, actor, 10, 10)

    def chosen_cells(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> list[Position]:
        return match_payload_pattern(payload, self.patterns(battle, actor))

    def execute(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> None:
        cells = self.chosen_cells(battle, actor, payload)
        seen: set[str] = set()
        for unit in list(battle.units_at_cells(cells)):
            unit = battle.effect_recipient(unit)
            if not unit.alive or unit.position is None or unit.banished:
                continue
            if unit.unit_id in seen:
                continue
            seen.add(unit.unit_id)
            ctx = battle.resolve_damage(DamageContext(
                source=actor, target=unit, attack_power=actor.stat("attack"), is_skill=True,
                action_name="干扰", area_cell_hits=battle.unit_hit_count_for_cells(unit, cells),
                tags={"skill", "attack", "interference"},
            ))
            if not unit.alive or not damage_followup_effect_applies(ctx):
                continue
            effect_ctx = battle.validate_target(
                actor, unit, action_name="干扰", is_skill=True,
                is_hostile=unit.player_id != actor.player_id, ignore_targeting_restrictions=True,
                resolve_defenses=False, tags={"skill", "interference"},
            )
            if effect_ctx.cancelled:
                continue
            if unit.is_clone:
                unit.alive = False
                battle.log_public_event(f"{unit.name} 被【干扰】破坏。", source=actor, target=unit)
                continue
            if unit.is_summon and unit.player_id != actor.player_id:
                battle.transfer_unit_control(unit, actor)
                battle.log(f"{actor.name} 用【干扰】取得了 {unit.name} 的控制权。")
        battle.cleanup_dead_units()

    def preview(self, battle: Battle, actor: HeroUnit) -> dict[str, Any]:
        preview = pattern_selection_preview(self.patterns(battle, actor))
        cell_keys = {(cell["x"], cell["y"]) for cell in preview["cells"]}
        targets = [
            unit.unit_id
            for unit in battle.all_units()
            if battle.unit_can_be_selected(unit, actor=actor)[0]
            and any((cell.x, cell.y) in cell_keys for cell in battle.unit_cells(unit))
        ]
        preview.update({"target_unit_ids": targets, "secondary_cells": [], "requires_target": True})
        return preview

    def get_target_cells_for_payload(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> list[Position]:
        return self.chosen_cells(battle, actor, payload)

    def get_target_units_for_payload(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> list[HeroUnit]:
        cells = self.chosen_cells(battle, actor, payload)
        return battle.units_at_cells(cells)  # type: ignore[return-value]


