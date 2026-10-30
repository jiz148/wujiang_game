"""R373: the paid, per-use instant form of an existing electronic skill."""

from __future__ import annotations

from contextlib import contextmanager
from typing import Any, Iterator

from wujiang.tactical.engine.core import ActionError, Battle, QueuedAction, Skill, Trait, Unit
from wujiang.tactical.heroes.ai_policy import (AIReactionCandidateContext,
    AIReactionScoreContext, AISkillCandidateContext, HeroAIPolicy)
from wujiang.tactical.heroes.hero_kit import HeroKit, register_hero_kit


class ZeroManaOverdriveTrait(Trait):
    def __init__(self) -> None:
        super().__init__("零魔超频", "当前魔为0时，攻、守、速各+2；魔力变化立即重算。")

    def modify_stat(self, stat_name: str, value: float) -> float:
        if stat_name in {"attack", "defense", "speed"} and self.owner is not None and self.owner.current_mana == 0:
            return value + 2
        return value


class InstantConversionTrait(Trait):
    def __init__(self) -> None:
        super().__init__("电子超频", "使用任意技能时可额外费1魔，将本次技能变成随时使用。")


class ElectronicInfinityAIPolicy(HeroAIPolicy):
    """Keep paid instant candidates tied to their source skill's target model."""

    contextual_reaction_prefixes = ("infinity_instant__",)

    def contextual_reaction_score(self, context: AIReactionScoreContext) -> float | None:
        if not context.code.startswith("infinity_instant__"):
            return None
        source_code = context.code.rsplit("__", 1)[-1]
        if source_code == "ion_shield":
            base = context.base_score("ion_shield")
            return base - 26.0 if base > 0 else -1000.0
        if source_code in {"machine_gun", "missile"}:
            cells = context.positions()
            if not cells:
                return -1000.0
            battle, reactor = context.battle, context.actor
            power = reactor.stat("attack") + (2.0 if reactor.current_mana > 0 else 0.0)
            net = 0.0
            for target in battle.effect_units_at_cells(cells):
                if target.magic_immunity:
                    continue
                hit = context.damage(target, power + max(0, battle.unit_hit_count_for_cells(target, cells) - 1))
                net += hit * (110.0 if target.player_id != reactor.player_id else -165.0)
            return net - 28.0 if net > 0 else -1000.0
        return -1000.0

    @staticmethod
    def _ranked_patterns(context: AISkillCandidateContext | AIReactionCandidateContext,
                         patterns: list) -> list[tuple[float, Any, list]]:
        ranked = []
        for pattern in patterns:
            cells = context.positions(pattern)
            net = sum((1.0 if unit.player_id != context.actor.player_id else -1.6)
                      * max(1, context.battle.unit_hit_count_for_cells(unit, cells))
                      for unit in context.battle.effect_units_at_cells(cells)
                      if not unit.magic_immunity)
            if net > 0:
                ranked.append((net, pattern, cells))
        ranked.sort(key=lambda entry: entry[0], reverse=True)
        return ranked[:24]

    def skill_candidate_patterns(self, context: AISkillCandidateContext,
                                 patterns: list) -> list | None:
        if not context.code.startswith("infinity_instant__"):
            return None
        return [pattern for _, pattern, _ in self._ranked_patterns(context, patterns)]

    def reaction_candidate_payloads(self, context: AIReactionCandidateContext) -> list[dict] | None:
        if not context.code.startswith("infinity_instant__"):
            return None
        source_code = context.code.rsplit("__", 1)[-1]
        preview = context.option.get("preview") or {}
        if source_code == "ion_shield":
            return [{**context.base_payload, "target_unit_ids": [str(unit_id)]}
                    for unit_id in preview.get("target_unit_ids", [])]
        if source_code in {"machine_gun", "missile"}:
            patterns = (preview.get("selection") or {}).get("patterns", [])
            return [{**context.base_payload, "cells": context.payload_cells(cells)}
                    for _, _, cells in self._ranked_patterns(context, patterns)]
        if source_code == "shensu":
            return []
        return None


@contextmanager
def _source_as_instant(source: Skill) -> Iterator[None]:
    original = source.timing
    source.timing = "instant"
    try:
        yield
    finally:
        source.timing = original


class InfinityInstantSkill(Skill):
    """A visible second declaration mode; the original skill owns every quota."""

    def __init__(self, source: Skill) -> None:
        super().__init__(
            f"infinity_instant__{source.code}",
            f"{source.name}·超频",
            f"额外费1魔，把本次【{source.name}】变为速度3的随时技能；原费用、次数、冷却和效果照常。",
            mana_cost=source.mana_cost + 1,
            target_mode=source.target_mode,
            timing="instant",
            direction_mode=source.direction_mode,
        )
        self.source = source
        for field in (
            "requires_direct_unit_target_line", "target_unit_is_anchor",
            "target_cells_are_geometry_only", "selection_cells_are_effect_cells",
            "resolves_from_current_position", "excludes_caster_from_effect",
            "excludes_allies_from_effect",
        ):
            setattr(self, field, getattr(source, field))

    def mana_cost_for_payload(self, battle: Battle, actor: Unit,
                              payload: dict[str, Any] | None = None) -> float:
        with _source_as_instant(self.source):
            extra = 1.0
            if battle.pending_chain is not None:
                extra *= max(0.0, float(battle.pending_chain.queued_action.payload.get(
                    "reaction_mana_multiplier", 1.0) or 1.0))
            return round(self.source.mana_cost_for_payload(battle, actor, payload) + extra, 2)

    def _source_available(self, battle: Battle, actor: Unit,
                          payload: dict[str, Any] | None = None) -> tuple[bool, str]:
        if not self._conversion_enabled(actor):
            return False, "电子超频特性已被封锁。"
        if self.source not in actor.action_skills():
            return False, "原技能已经失去。"
        with _source_as_instant(self.source):
            return self.source.can_use(battle, actor, payload)

    def can_use(self, battle: Battle, actor: Unit,
                payload: dict[str, Any] | None = None) -> tuple[bool, str]:
        if self.source.timing in {"passive", "reaction"}:
            return False, "原被动技能仍须有合法受影响目标，不能空放。"
        ok, reason = self._source_available(battle, actor, payload)
        if not ok:
            return ok, reason
        return super().can_use(battle, actor, payload)

    def can_react_to(self, battle: Battle, actor: Unit,
                     queued_action: QueuedAction) -> tuple[bool, str]:
        if not self._conversion_enabled(actor):
            return False, "电子超频特性已被封锁。"
        if self.source not in actor.action_skills():
            return False, "原技能已经失去。"
        with _source_as_instant(self.source):
            ok, reason = self.source.can_react_to(battle, actor, queued_action)
        if not ok:
            return ok, reason
        return super().can_react_to(battle, actor, queued_action)

    @staticmethod
    def _conversion_enabled(actor: Unit) -> bool:
        return not actor.innate_traits_suppressed() and any(
            isinstance(trait, InstantConversionTrait) for trait in actor.traits)

    def can_react_with_payload(self, battle: Battle, actor: Unit,
                               queued_action: QueuedAction,
                               payload: dict[str, Any] | None = None) -> tuple[bool, str]:
        with _source_as_instant(self.source):
            ok, reason = self.source.can_react_with_payload(battle, actor, queued_action, payload)
        if not ok:
            return ok, reason
        ok, reason = super().can_react_with_payload(battle, actor, queued_action, payload)
        if not ok:
            return ok, reason
        if self.source.target_mode == "cell":
            try:
                self.source.get_target_cells_for_payload(battle, actor, dict(payload or {}))
            except (ActionError, KeyError, TypeError, ValueError) as exc:
                return False, str(exc) or "请选择合法区域。"
        return True, ""

    def prepay_resources(self, battle: Battle, actor: Unit,
                         payload: dict[str, Any] | None = None) -> None:
        total_cost = self.mana_cost_for_payload(battle, actor, payload)
        if actor.current_mana + 1e-9 < total_cost:
            raise ActionError("超频魔力不足。")
        original_mana = actor.current_mana
        with _source_as_instant(self.source):
            self.source.prepay_resources(battle, actor, payload)
        actor.spend_mana(max(0.0, total_cost - (original_mana - actor.current_mana)))
        self.uses_this_turn += 1
        self.uses_this_battle += 1

    def execute(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> None:
        self.source.execute(battle, actor, payload)

    def react(self, battle: Battle, actor: Unit, payload: dict[str, Any],
              queued_action: QueuedAction) -> None:
        self.source.react(battle, actor, payload, queued_action)

    def preview(self, battle: Battle, actor: Unit) -> dict[str, Any]:
        return self.source.preview(battle, actor)

    def reaction_preview(self, battle: Battle, actor: Unit,
                         queued_action: QueuedAction) -> dict[str, Any]:
        if self.source.timing in {"passive", "reaction"}:
            return self.source.reaction_preview(battle, actor, queued_action)
        return self.source.preview(battle, actor)

    def get_target_units_for_payload(self, battle: Battle, actor: Unit,
                                     payload: dict[str, Any]) -> list[Unit]:
        return self.source.get_target_units_for_payload(battle, actor, payload)

    def get_target_cells_for_payload(self, battle: Battle, actor: Unit,
                                     payload: dict[str, Any]) -> list[Any]:
        return self.source.get_target_cells_for_payload(battle, actor, payload)

    def direct_unit_target_range(self, battle: Battle, actor: Unit,
                                 payload: dict[str, Any] | None = None) -> int:
        return self.source.direct_unit_target_range(battle, actor, payload)

    def queued_payload_metadata(self, battle: Battle, actor: Unit,
                                payload: dict[str, Any]) -> dict[str, Any]:
        return self.source.queued_payload_metadata(battle, actor, payload)

    def queued_reaction_payload_metadata(self, battle: Battle, actor: Unit,
                                         payload: dict[str, Any]) -> dict[str, Any]:
        return self.source.queued_reaction_payload_metadata(battle, actor, payload)

    def ignores_shield_for_payload(self, battle: Battle, actor: Unit,
                                   payload: dict[str, Any]) -> bool:
        return self.source.ignores_shield_for_payload(battle, actor, payload)

    def ignores_magic_immunity_for_payload(self, battle: Battle, actor: Unit,
                                           payload: dict[str, Any]) -> bool:
        return self.source.ignores_magic_immunity_for_payload(battle, actor, payload)

    def to_public_dict(self, battle: Battle) -> dict[str, Any]:
        result = super().to_public_dict(battle)
        self.source.sync_turn_scope(battle)
        result.update({"source_skill_code": self.source.code,
                       "uses_this_turn": self.source.uses_this_turn,
                       "uses_this_battle": self.source.uses_this_battle,
                       "max_uses_per_turn": self.source.max_uses_per_turn,
                       "max_uses_per_battle": self.source.max_uses_per_battle,
                       "cooldown_remaining": self.source.cooldown_remaining,
                       "cooldown_turns": self.source.cooldown_turns,
                       "base_mana_cost": self.source.mana_cost + 1})
        return result


def infinity_action_skills(self: Unit) -> list[Skill]:
    """Keep conversion available for skills temporarily granted in battle."""
    if not hasattr(self, "skills"):
        return []
    current = Unit.action_skills(self)
    available_sources = {id(skill) for skill in current if not isinstance(skill, InfinityInstantSkill)}
    wrapped_sources = {id(skill.source) for skill in self.skills if isinstance(skill, InfinityInstantSkill)}
    for source in current:
        if isinstance(source, InfinityInstantSkill) or id(source) in wrapped_sources:
            continue
        self.skills.append(InfinityInstantSkill(source).bind(self))
        wrapped_sources.add(id(source))
    return [skill for skill in Unit.action_skills(self)
            if not isinstance(skill, InfinityInstantSkill) or id(skill.source) in available_sources]


def infinity_get_skill(self: Unit, code: str) -> Skill:
    if not code.startswith("infinity_instant__"):
        return Unit.get_skill(self, code)
    available = infinity_action_skills(self)
    skill = next((item for item in available if item.code == code), None)
    if skill is None:
        raise ActionError(f"{self.name} 没有可用技能 {code}")
    return skill


class ElectronicInfinityKit(HeroKit):
    """Own R373's per-skill instant modes and zero-mana unit ability."""

    code = "excel_r373"
    ai_policy = ElectronicInfinityAIPolicy()

    def augment_skills(self, skills: list[Skill]) -> list[Skill]:
        skills.extend(InfinityInstantSkill(source) for source in list(skills))
        return skills

    def extra_traits(self) -> list[Trait]:
        return [InstantConversionTrait(), ZeroManaOverdriveTrait()]

    def unit_attributes(self) -> dict[str, Any]:
        return {"action_skills": infinity_action_skills, "get_skill": infinity_get_skill}


ELECTRONIC_INFINITY_KIT = register_hero_kit(ElectronicInfinityKit())
