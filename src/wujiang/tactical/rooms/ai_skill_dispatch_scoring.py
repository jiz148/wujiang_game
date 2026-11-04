"""AISkillScoreCoordinator: shared AI scoring and queries for skill_dispatch."""

from __future__ import annotations

from typing import Any, Callable, Protocol
from wujiang.tactical.engine.core import ActionError, Battle, Unit
from wujiang.tactical.rooms.ai_skill_catalog import (
    SUMMON_SKILL_CODES, HEAL_SKILL_CODES, ALLY_BUFF_SKILL_CODES,
    SELF_BUFF_SKILL_CODES, MOVE_SKILL_CODES, DAMAGING_SKILL_CODES,
    CONTROL_SKILL_CODES,
)
from wujiang.tactical.rooms.ai_types import DifficultyProfile
from wujiang.tactical.rooms.ai_formation_scoring import (
    follow_catch_up_skill_penalty, score_move_destination,
)
from wujiang.tactical.rooms.ai_effect_early_scoring import (
    skill_owned_effect_score, skill_damage_score, skill_control_bonus,
)
from wujiang.tactical.rooms.ai_support_scoring import ally_buff_score
from wujiang.tactical.rooms.ai_self_generic_scoring import (
    self_buff_score, generic_skill_score, summon_position_score,
)
from wujiang.tactical.rooms.ai_impact_scoring import skill_effect_units
from wujiang.tactical.rooms.ai_weather_scoring import (
    primary_target_unit, hero_style, payload_destination,
)

class AISkillScoreCoordinatorServices(Protocol):
    """The exact room services used by this scoring family."""
    _score_skill_payload: Callable[..., Any]
    _skill_score_context: Callable[..., Any]
    agency_borrowed_payload_context: Any
    build_zero_crossing_move_candidates: Callable[..., Any]
    copied_skill_action: Any
    mimic_payload_context: Any
    reviewed_r16_damage_result: Callable[..., Any]
    score_skill_payload: Callable[..., Any]
    skill_from_ai_action: Any
    skill_payloads_for_action: Any


class AISkillScoreCoordinator:
    """Owns this coherent family; the room keeps compatibility calls."""

    def __init__(self, services: AISkillScoreCoordinatorServices) -> None:
        self.services = services

    def _score_skill_payload(self,
        battle: Battle,
        actor: Unit,
        action: dict[str, Any],
        payload: dict[str, Any],
        profile: DifficultyProfile,
        *,
        instant_only: bool,
    ) -> float:
        code = str(action.get("code") or payload.get("skill_code") or "")
        priority_policy = getattr(actor, "ai_policy", None)
        if priority_policy is not None:
            priority_score = priority_policy.priority_skill_score(battle, actor, code, payload)
            if priority_score is not None:
                return priority_score
        try:
            priority_skill = self.services.skill_from_ai_action(actor, action, code)
        except (ActionError, KeyError, ValueError):
            priority_skill = None
        priority_scorer = getattr(priority_skill, "ai_priority_score", None)
        if priority_scorer is not None:
            priority_value = priority_scorer(self.services._skill_score_context(
                battle, actor, action, payload, profile, instant_only=instant_only))
            if priority_value is not None:
                return priority_value
        if priority_policy is not None:
            if code in priority_policy.contextual_skill_codes:
                contextual_score = priority_policy.contextual_skill_score(
                    self.services._skill_score_context(battle, actor, action, payload, profile))
                if contextual_score is not None:
                    return contextual_score
        skill = self.services.skill_from_ai_action(actor, action, code)
        if getattr(skill, "ai_early_effect_score", False):
            return skill_owned_effect_score(battle, actor, skill, payload, profile)
        targets = skill_effect_units(battle, actor, skill, payload)
        role = hero_style(actor)
        if code in MOVE_SKILL_CODES:
            destination = payload_destination(payload)
            if destination is None:
                return -5.0
            origin_score = score_move_destination(battle, actor, actor.position, role, profile) if actor.position is not None else 0.0
            score = score_move_destination(battle, actor, destination, role, profile) - origin_score - 1.0
            context = self.services._skill_score_context(battle, actor, action, payload, profile,
                                                         instant_only=instant_only)
            if priority_policy is not None:
                score += priority_policy.move_skill_score_bonus(context, destination)
            move_scorer = getattr(skill, "ai_move_score", None)
            if move_scorer is not None:
                score = move_scorer(context, score, destination)
            return score
        if code in SUMMON_SKILL_CODES:
            destination = payload_destination(payload)
            if priority_policy is not None:
                summon_score = priority_policy.summon_skill_score(
                    self.services._skill_score_context(battle, actor, action, payload, profile,
                                                       instant_only=instant_only))
                if summon_score is not None:
                    return summon_score
            score = 42.0
            if destination is not None:
                score += summon_position_score(battle, actor, destination)
            score += getattr(skill, "ai_summon_base_bonus", 0.0)
            score += follow_catch_up_skill_penalty(battle, actor)
            return score
        if code in HEAL_SKILL_CODES:
            healed = primary_target_unit(battle, payload, targets)
            if healed is None:
                healed = actor
            heal_scorer = getattr(skill, "ai_heal_score", None)
            if heal_scorer is not None:
                return heal_scorer(self.services._skill_score_context(
                    battle, actor, action, payload, profile, instant_only=instant_only), healed)
            missing = max(0.0, healed.max_health - healed.current_hp)
            score = missing * 120.0
            return score
        if code in ALLY_BUFF_SKILL_CODES:
            target = primary_target_unit(battle, payload, targets)
            if target is None:
                return -2.0
            score = ally_buff_score(battle, actor, code, target, profile,
                                    skill=skill, action=action, payload=payload)
            return score
        if code in SELF_BUFF_SKILL_CODES:
            return (self_buff_score(battle, actor, code, profile, skill=skill, action=action, payload=payload)
                    + follow_catch_up_skill_penalty(battle, actor))
        if code in DAMAGING_SKILL_CODES or code in CONTROL_SKILL_CODES:
            score = skill_damage_score(battle, actor, skill, payload, profile)
            score += skill_control_bonus(battle, actor, code, payload, targets, profile,
                                         instant_only=instant_only, skill=skill, action=action)
            if skill.max_uses_per_battle is not None and skill.max_uses_per_battle <= 1 and score < profile.once_per_battle_threshold:
                score -= 32.0
            return score
        return generic_skill_score(battle, actor, code, targets, profile)
