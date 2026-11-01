"""R264 winged leopard leap and eagle eye."""

from __future__ import annotations

from wujiang.tactical.heroes.ai_candidate_plan import RANKED_CANDIDATES

from typing import Any
from wujiang.tactical.engine.core import ActionError
from wujiang.tactical.engine.core import Battle
from wujiang.tactical.engine.core import HeroUnit
from wujiang.tactical.engine.core import Position
from wujiang.tactical.engine.core import Skill
from wujiang.tactical.engine.core import StatusEffect
from wujiang.tactical.engine.core import Trait
from wujiang.tactical.heroes.ai_policy import AISkillScoreContext
from wujiang.tactical.heroes.common import DashMoveSkill
from wujiang.tactical.heroes.common import match_payload_pattern
from wujiang.tactical.heroes.common import pattern_selection_preview
from wujiang.tactical.heroes.common import pattern_signature
from wujiang.tactical.heroes.common import payload_position
from wujiang.tactical.heroes.mechanics.declared_area import DeclaredAreaSkillMixin
from wujiang.tactical.heroes.mechanics.next_five_effects import remote_rectangle_patterns


class WingedLeopardLeapSkill(DashMoveSkill):
    def ai_priority_score(self, context: AISkillScoreContext) -> float:
        return context.winged_leap_score()

    def __init__(self) -> None:
        super().__init__(
            "fly_leap",
            "飞跃",
            "普通技能：每回合一次，不费魔，直线飞行移动恰好 3 格。",
            max_distance=3,
            exact_distance=3,
            mana_cost=0,
            max_uses_per_turn=1,
            straight_only=True,
            ignore_units=True,
        )

    def queued_payload_metadata(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> dict[str, Any]:
        destination = payload_position(payload)
        if destination.to_dict() not in self.preview(battle, actor)["cells"]:
            raise ActionError("飞跃必须选择恰好3格且完整身体可落地的位置。")
        return {}


class WingedLeopardMoveResetTrait(Trait):
    def __init__(self) -> None:
        super().__init__("飞跃重置移动", "使用飞跃后重置本回合普通移动次数。")

    def on_unit_moved(self, battle: Battle, ctx: Any) -> None:
        owner = self.owner
        if owner is None or ctx.unit.unit_id != owner.unit_id or "fly_leap" not in ctx.tags:
            return
        owner.normal_move_actions_used = 0
        owner.normal_move_steps_used = 0
        owner.move_used = False
        battle.log(f"{owner.name} 使用飞跃，重置了本回合普通移动次数。")


class EagleEyeStatus(StatusEffect):
    def __init__(self) -> None:
        super().__init__("鹰眼", "不能普攻或使用主动技能。", duration=3, tick_scope="owner_turn_end")

    def can_attack_target(self, battle: Battle, actor: HeroUnit, target: HeroUnit) -> tuple[bool, str]:
        if self.owner is not None and actor.unit_id == self.owner.unit_id:
            return False, "鹰眼使该单位不能攻击。"
        return True, ""

    def blocks_skill_use(self, battle: Battle, actor: HeroUnit, skill: Skill) -> tuple[bool, str]:
        if self.owner is not None and actor.unit_id == self.owner.unit_id and skill.timing == "active":
            return True, "鹰眼使该单位不能使用主动技能。"
        return False, ""


class EagleEyeSkill(DeclaredAreaSkillMixin, Skill):
    ai_candidate_plan = RANKED_CANDIDATES
    def ai_priority_score(self, context: AISkillScoreContext) -> float:
        return context.control_score(self)

    def __init__(self) -> None:
        super().__init__("eagle_eye", "鹰眼", "普通技能：基础费 0.5 魔，每回合第一次免费；2*3 范围，无伤害，封锁攻击和主动技能 3 轮。", mana_cost=0.5, target_mode="cell")

    def mana_cost_for_payload(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any] | None = None) -> float:
        self.sync_turn_scope(battle)
        return 0.0 if self.uses_this_turn == 0 else super().mana_cost_for_payload(battle, actor, payload)

    def patterns(self, battle: Battle, actor: HeroUnit) -> list[list[Position]]:
        patterns = [*remote_rectangle_patterns(battle, actor, 2, 3), *remote_rectangle_patterns(battle, actor, 3, 2)]
        unique: dict[tuple[tuple[int, int], ...], list[Position]] = {}
        for pattern in patterns:
            unique.setdefault(pattern_signature(pattern), pattern)
        return list(unique.values())

    def chosen_cells(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> list[Position]:
        return battle.payload_positions(payload, "declared_area_cells") or match_payload_pattern(payload, self.patterns(battle, actor))

    def execute(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> None:
        cells = self.chosen_cells(battle, actor, payload)
        for target in battle.effect_units_at_cells(cells):
            ctx = battle.validate_target(
                actor,
                target,
                action_name="鹰眼",
                is_skill=True,
                is_hostile=target.player_id != actor.player_id,
                tags={"skill", "eagle_eye", "control"},
            )
            if ctx.cancelled:
                if ctx.reason:
                    battle.log_public_event(ctx.reason, source=actor, target=target)
                continue
            target = ctx.target
            existing = target.get_status("鹰眼")
            if existing is not None:
                target.remove_status(existing, battle)
            target.add_status(EagleEyeStatus())

    def preview(self, battle: Battle, actor: HeroUnit) -> dict[str, Any]:
        preview = pattern_selection_preview(self.patterns(battle, actor))
        cells = [cell for pattern in self.patterns(battle, actor) for cell in pattern]
        preview.update({
            "target_unit_ids": [unit.unit_id for unit in battle.effect_units_at_cells(cells)
                                if battle.unit_can_be_selected(unit, actor=actor)[0]],
            "secondary_cells": [],
            "requires_target": True,
        })
        return preview

    def get_target_cells_for_payload(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> list[Position]:
        return self.chosen_cells(battle, actor, payload)

    def get_target_units_for_payload(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> list[HeroUnit]:
        return list(battle.effect_units_at_cells(self.chosen_cells(battle, actor, payload)))  # type: ignore[return-value]


