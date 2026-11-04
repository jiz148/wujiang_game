"""Reusable, immutable AI candidate preparation policies owned by skills.

Skill classes select a plan; the room adapter supplies generic scoring and
geometry services. A hero-specific deduplication can override
``ai_unique_candidate_payloads`` on its skill object.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


def _ordered_payload_key(value: Any) -> Any:
    if isinstance(value, dict):
        return [(key, _ordered_payload_key(item))
                for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))]
    if isinstance(value, list):
        return [_ordered_payload_key(item) for item in value]
    return value


class TargetDestinationCandidates:
    """Skill-owned target × preview-destination payload construction."""

    ai_destination_fields = ("x", "y")

    def ai_candidate_payloads(self, context: Any) -> list[dict]:
        preview = context.action.get("preview", {}) or {}
        destinations_by_target = preview.get("destinations_by_target", {}) or {}
        x_key, y_key = self.ai_destination_fields
        payloads: list[dict] = []
        for target_id in preview.get("target_unit_ids", []):
            for cell in context.positions(destinations_by_target.get(str(target_id))):
                payloads.append({**context.base_payload, "target_unit_id": str(target_id),
                                 x_key: cell.x, y_key: cell.y})
        ordered: list[dict] = []
        seen: set[str] = set()
        for payload in payloads:
            key = repr(_ordered_payload_key(payload))
            if key not in seen:
                seen.add(key)
                ordered.append(payload)
        return ordered


class MagicAreaPrefilter:
    """Cheap enemy/friendly area estimate before the expensive full replay."""

    def ai_prefilter_score(self, battle: Any, actor: Any, payload: dict,
                           shared: Any) -> float:
        cells = shared.positions(payload.get("cells"))
        if not cells:
            return -1000.0
        score = 0.0
        useful_enemy = False
        for target in battle.effect_units_at_cells(cells):
            recipient = battle.effect_recipient(target)
            if recipient.magic_immunity or not recipient.alive:
                continue
            if recipient.total_shields() > 0:
                impact = 20.0
            elif recipient.player_id != actor.player_id and recipient.dodge_charges > 0:
                impact = 12.0
            else:
                power = actor.stat("attack") + max(0, battle.unit_hit_count_for_cells(recipient, cells) - 1)
                damage = min(recipient.current_hp, shared.estimate_damage(battle, recipient, power))
                impact = damage * 100.0 + (90.0 if damage >= recipient.current_hp else 0.0)
            if recipient.player_id == actor.player_id:
                score -= impact * 1.3
            else:
                useful_enemy |= impact > 0
                score += impact
        return score - 8.0 if useful_enemy else -1000.0


@dataclass(frozen=True, slots=True)
class SkillCandidatePlan:
    dedupe_mode: str = ""
    ranked: bool = False
    positive_only: bool = False
    prefilter_limit: int = 0
    prefilter_positive: bool = False
    score_shortlist: bool = False

    def dedupe_payloads(self, battle: Any, actor: Any, skill: Any,
                        payloads: list[dict], shared: Any) -> list[dict]:
        unique = getattr(skill, "ai_unique_candidate_payloads", None)
        if unique is not None:
            payloads = unique(battle, actor, payloads)
        if self.dedupe_mode == "damage":
            return shared.dedupe_damage(battle, payloads)
        if self.dedupe_mode == "units":
            return shared.dedupe_units(battle, payloads)
        return payloads

    def prepare_payloads(self, battle: Any, actor: Any, action: dict, skill: Any,
                         payloads: list[dict], profile: Any, instant_only: bool,
                         shared: Any, pre_scored: dict[str, float],
                         prequalified: set[str]) -> tuple[list[dict], dict[str, float], set[str]]:
        if self.prefilter_limit:
            ranked = sorted(((skill.ai_prefilter_score(battle, actor, candidate, shared), candidate)
                             for candidate in payloads), key=lambda item: item[0], reverse=True)
            payloads = [candidate for score, candidate in ranked
                        if not self.prefilter_positive or score > -1000.0][:self.prefilter_limit]
            if self.score_shortlist:
                for candidate in payloads:
                    key = repr(candidate)
                    pre_scored[key] = shared.score(
                        battle, actor, action, candidate, profile, instant_only=instant_only)
                    prequalified.add(key)
        if not self.ranked:
            return shared.trim(battle, actor, payloads, limit=64), pre_scored, prequalified
        for candidate in payloads:
            pre_scored[repr(candidate)] = shared.score(
                battle, actor, action, candidate, profile, instant_only=instant_only)
        ranked_payloads = sorted(payloads, key=lambda candidate: pre_scored[repr(candidate)],
                                 reverse=True)
        selected: list[dict] = []
        for candidate in ranked_payloads:
            key = repr(candidate)
            if self.positive_only and pre_scored[key] <= 0:
                continue
            if not self.positive_only and shared.requires_enemy_impact(
                    battle, actor, action, candidate) and not shared.effective_enemy_impact(
                    battle, actor, action, candidate):
                continue
            prequalified.add(key)
            selected.append(candidate)
            if len(selected) >= 64:
                break
        return selected, pre_scored, prequalified


DEFAULT_CANDIDATES = SkillCandidatePlan()
RANKED_CANDIDATES = SkillCandidatePlan(ranked=True)
POSITIVE_RANKED_CANDIDATES = SkillCandidatePlan(ranked=True, positive_only=True)
DAMAGE_DEDUPED_CANDIDATES = SkillCandidatePlan(dedupe_mode="damage")
DAMAGE_DEDUPED_RANKED_CANDIDATES = SkillCandidatePlan(dedupe_mode="damage", ranked=True)
UNIT_DEDUPED_CANDIDATES = SkillCandidatePlan(dedupe_mode="units")
UNIT_DEDUPED_RANKED_CANDIDATES = SkillCandidatePlan(dedupe_mode="units", ranked=True)
MAGIC_AREA_SHORTLIST = SkillCandidatePlan(dedupe_mode="damage", prefilter_limit=12)
HELL_FIRE_SHORTLIST = SkillCandidatePlan(dedupe_mode="damage", prefilter_limit=12,
                                          prefilter_positive=True, score_shortlist=True)
