"""AttackCandidateBuilder for the shared AI candidate pipeline."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Iterable, Optional, Protocol
from wujiang.tactical.engine.core import ActionError, Battle, Position, QueuedAction, Unit
from wujiang.tactical.prediction import forecasting_active, note
from wujiang.tactical.heroes.ai_policy import AIReactionCandidateContext
from wujiang.tactical.heroes.ai_candidate_plan import DEFAULT_CANDIDATES
from wujiang.tactical.rooms.ai_targets import living_hostile_combatants

if TYPE_CHECKING:
    from wujiang.tactical.rooms.ai import AICandidate, DifficultyProfile

class AttackCandidateBuilderServices(Protocol):
    AICandidate: Any
    DifficultyProfile: Any
    HEAL_SKILL_CODES: Any
    allied_attack_heal_score: Any
    attack_effect_units: Any
    attack_payload_has_effective_enemy_impact: Any
    attack_payload_satisfies_required_target: Any
    attack_payloads_for_action: Any
    cat_retaliation_action_penalty: Any
    estimate_damage: Any
    natsume_support_attack_score: Any
    payload_is_legal: Any
    preview_positions: Any
    red_attack_value: Any
    score_attack_payload: Any
    stone_attack_spawn_penalty: Any
    with_enemy_view: Any
    wuchang_action_success_probability: Any

class AttackCandidateBuilder:
    """Own attack candidates construction."""

    def __init__(self, services: AttackCandidateBuilderServices) -> None:
        self.services = services

    def build_attack_candidates(self,
        battle: Battle,
        actor: Unit,
        action: dict[str, Any],
        profile: DifficultyProfile,
    ) -> list[AICandidate]:
        payloads = self.services.attack_payloads_for_action(battle, actor, action)
        if any(trait.name == "古代炮台4×4普攻" for trait in actor.traits):
            payloads = sorted(payloads, key=lambda item: self.ancient_turret_prefilter_score(battle, actor, item),
                              reverse=True)[:20]
        candidates: list[self.services.AICandidate] = []
        for payload in payloads:
            if not self.services.payload_is_legal(battle, payload):
                continue
            copied_support = (actor.has_status("武装复制") and str(payload.get("target_unit_id") or "") in battle.units
                              and battle.units[str(payload["target_unit_id"])].player_id == actor.player_id
                              and self.services.red_attack_value(battle, actor, payload) > 0)
            natsume_support = self.services.natsume_support_attack_score(battle, actor, payload) > 0
            if not copied_support and not natsume_support and actor.hero_code != "n" and self.services.allied_attack_heal_score(battle, actor, payload) <= 0 and not self.services.attack_payload_has_effective_enemy_impact(battle, actor, payload) and not self.services.attack_payload_satisfies_required_target(
                battle,
                actor,
                payload,
            ):
                continue
            score = self.services.score_attack_payload(battle, actor, payload, profile)
            if actor.has_status("生锈的气味预备"):
                cells = self.services.preview_positions(payload.get("cells"))
                score += sum(32.0 + (20.0 if any(skill.code in self.services.HEAL_SKILL_CODES for skill in unit.skills) else 0.0)
                             for unit in battle.effect_units_at_cells(cells)
                             if unit.player_id != actor.player_id and not unit.cannot_heal
                             and not unit.physical_immunity and not unit.skill_non_damage_effects_blocked()
                             and unit.dodge_charges <= 0)
            score -= self.services.stone_attack_spawn_penalty(battle, actor, payload)
            score *= self.services.wuchang_action_success_probability(battle, actor, payload)
            score -= self.services.cat_retaliation_action_penalty(battle, actor, self.services.attack_effect_units(battle, actor, payload))
            candidates.append(self.services.AICandidate(payload=payload, score=score, summary=f"attack:{payload.get('target_unit_id')}"))
        return candidates


    def ancient_turret_prefilter_score(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> float:
        cells = self.services.preview_positions(payload.get("cells"))
        if not cells:
            return -1000.0
        score = 0.0
        for unit in battle.effect_units_at_cells(cells):
            recipient = battle.effect_recipient(unit)
            if not recipient.alive:
                continue
            if recipient.physical_immunity:
                impact = 0.0
            elif recipient.total_shields() > 0 and not actor.has_status("生锈的气味预备"):
                impact = 20.0
            elif recipient.player_id != actor.player_id and recipient.dodge_charges > 0:
                impact = 12.0
            else:
                power = actor.stat("attack") + max(0, battle.unit_hit_count_for_cells(recipient, cells) - 1)
                damage = min(recipient.current_hp, self.services.estimate_damage(battle, recipient, power))
                impact = damage * 100.0 + (80.0 if damage >= recipient.current_hp else 0.0)
            score += impact if recipient.player_id != actor.player_id else -impact * 1.6
        return score
