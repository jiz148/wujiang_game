"""SkillCandidateBuilder for the shared AI candidate pipeline."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Iterable, Optional, Protocol
from wujiang.tactical.engine.core import ActionError, Battle, Position, QueuedAction, Unit
from wujiang.tactical.prediction import forecasting_active, note
from wujiang.tactical.heroes.ai_policy import AIReactionCandidateContext
from wujiang.tactical.heroes.ai_candidate_plan import DEFAULT_CANDIDATES
from wujiang.tactical.rooms.ai_targets import living_hostile_combatants

if TYPE_CHECKING:
    from wujiang.tactical.rooms.ai import AICandidate, DifficultyProfile

class _AreaRecipientIndex:
    """Occupancy for ONE read-only batch, never an action or probe cache.

    Query order stays cell-major then unit insertion order. Mapping riders to
    mounts still uses Battle.effect_units, preserving its recipient deduplication.
    """

    def __init__(self, battle: Battle) -> None:
        self.battle = battle
        self.occupants: dict[Position, list[Unit]] = {}
        self.bodies: dict[int, tuple[Position, ...]] = {}
        for unit in battle.all_units():
            cells = tuple(battle.unit_cells(unit))
            self.bodies[id(unit)] = cells
            for cell in dict.fromkeys(cells):
                self.occupants.setdefault(cell, []).append(unit)

    def at(self, cells: Iterable[Position]) -> list[Unit]:
        units: list[Unit] = []
        seen: set[str] = set()
        for cell in cells:
            for unit in self.occupants.get(cell, ()):
                if unit.unit_id not in seen:
                    seen.add(unit.unit_id)
                    units.append(unit)
        return self.battle.effect_units(units)


class SkillCandidateBuilderServices(Protocol):
    AICandidate: Any
    DifficultyProfile: Any
    _CANDIDATE_PLAN_SERVICES: Any
    cat_retaliation_action_penalty: Any
    hostile_unit_value: Any
    payload_is_legal: Any
    preview_positions: Any
    score_skill_payload: Any
    skill_effect_units: Any
    skill_from_ai_action: Any
    skill_payload_has_effective_enemy_impact: Any
    skill_payload_requires_enemy_impact: Any
    skill_payloads_for_action: Any
    with_enemy_view: Any
    wuchang_action_success_probability: Any

class SkillCandidateBuilder:
    """Own skill candidates construction."""

    def __init__(self, services: SkillCandidateBuilderServices) -> None:
        self.services = services

    def build_skill_candidates(self,
        battle: Battle,
        actor: Unit,
        action: dict[str, Any],
        profile: DifficultyProfile,
        *,
        instant_only: bool,
    ) -> list[AICandidate]:
        payloads = self.services.skill_payloads_for_action(battle, actor, action)
        code = str(action.get("code") or "")
        skill = self.services.skill_from_ai_action(actor, action, code)
        plan = getattr(skill, "ai_candidate_plan", DEFAULT_CANDIDATES)
        payloads = plan.dedupe_payloads(battle, actor, skill, payloads,
                                        self.services._CANDIDATE_PLAN_SERVICES)
        pre_scored: dict[str, float] = {}
        prequalified_payloads: set[str] = set()
        payloads, pre_scored, prequalified_payloads = plan.prepare_payloads(
            battle, actor, action, skill, payloads, profile, instant_only,
            self.services._CANDIDATE_PLAN_SERVICES, pre_scored, prequalified_payloads)
        candidates: list[self.services.AICandidate] = []
        selection_mode = str((action.get("preview", {}) or {}).get("selection", {}).get("mode") or "")
        for payload in payloads:
            generated_from_preview = bool(payload.get("cells")) and selection_mode in {"pattern_cells", "choice_pattern", ""}
            needs_explicit_legality = code in {"heaven_punishment"}
            if (needs_explicit_legality or not generated_from_preview) and not self.services.payload_is_legal(battle, payload):
                continue
            if self.should_throttle_unlimited_nonhostile_skill(battle, actor, action, payload):
                continue
            if repr(payload) not in prequalified_payloads and self.services.skill_payload_requires_enemy_impact(battle, actor, action, payload) and not self.services.skill_payload_has_effective_enemy_impact(
                battle,
                actor,
                action,
                payload,
            ):
                continue
            score = pre_scored[repr(payload)] if repr(payload) in pre_scored else self.services.score_skill_payload(battle, actor, action, payload, profile, instant_only=instant_only)
            try:
                skill = self.services.skill_from_ai_action(actor, action, code)
                targets = self.services.skill_effect_units(battle, actor, skill, payload)
            except (ActionError, KeyError, ValueError):
                targets = []
            chance = self.services.wuchang_action_success_probability(battle, actor, payload)
            score = score * chance - (1.0 - chance) * float(action.get("mana_cost") or 0.0) * 12.0
            score -= self.services.cat_retaliation_action_penalty(battle, actor, targets)
            candidates.append(self.services.AICandidate(payload=payload, score=score, summary=f"skill:{action.get('code')}"))
        return candidates


    def dedupe_area_payloads_by_affected_units(self,
        battle: Battle,
        payloads: list[dict[str, Any]],
        *,
        clones_and_summons_only: bool,
    ) -> list[dict[str, Any]]:
        unique: dict[tuple[str, ...], dict[str, Any]] = {}
        # Small batches keep the direct query to avoid paying index setup cost.
        targets_at = _AreaRecipientIndex(battle).at if len(payloads) >= 8 else battle.effect_units_at_cells
        for payload in payloads:
            units = targets_at(self.services.preview_positions(payload.get("cells")))
            if clones_and_summons_only:
                units = [unit for unit in units if unit.is_clone or unit.is_summon]
            signature = tuple(sorted(unit.unit_id for unit in units))
            unique.setdefault(signature, payload)
        return list(unique.values())


    def dedupe_damage_area_payloads(self, battle: Battle, payloads: list[dict[str, Any]]) -> list[dict[str, Any]]:
        unique: dict[tuple[tuple[str, int], ...], dict[str, Any]] = {}
        index = _AreaRecipientIndex(battle) if len(payloads) >= 8 else None
        targets_at = index.at if index is not None else battle.effect_units_at_cells
        for payload in payloads:
            cells = self.services.preview_positions(payload.get("cells"))
            cell_keys = {(cell.x, cell.y) for cell in cells}
            signature = tuple(
                sorted(
                    (unit.unit_id, sum((cell.x, cell.y) in cell_keys for cell in
                                      (index.bodies[id(unit)] if index is not None else battle.unit_cells(unit))))
                    for unit in targets_at(cells)
                )
            )
            unique.setdefault(signature, payload)
        return list(unique.values())


    def should_throttle_unlimited_nonhostile_skill(self, battle: Battle, actor: Unit, action: dict[str, Any], payload: dict[str, Any]) -> bool:
        """Prevent AI from repeatedly spending a turn/mana on unlimited utility skills."""
        if str(action.get("timing") or "") == "passive":
            return False
        try:
            skill = self.services.skill_from_ai_action(actor, action, str(action.get("code") or payload.get("skill_code") or ""))
        except Exception:
            return False
        if skill.code in {"blood_art", "sacrifice_ritual", "paralysis_card", "poison_card", "drain_card", "judgment_stone", "mimic_skill", "fried_inspire", "eagle_eye", "electronic_storm"}:
            return False
        if getattr(skill, "max_uses_per_turn", None) is not None:
            return False
        if getattr(skill, "max_uses_per_battle", None) is not None:
            return False
        if getattr(skill, "cooldown_turns", 0):
            return False
        if float(getattr(skill, "uses_this_turn", 0) or 0) <= 0:
            return False
        if self.services.skill_payload_requires_enemy_impact(battle, actor, action, payload):
            return False
        return True


    def trim_skill_payloads_for_ai(self,
        battle: Battle,
        actor: Unit,
        payloads: list[dict[str, Any]],
        *,
        limit: int,
    ) -> list[dict[str, Any]]:
        if len(payloads) <= limit:
            return payloads
        if not any(payload.get("cells") for payload in payloads):
            return payloads[:limit]

        # No battle action is executed while these keys are calculated. Reuse only
        # geometry, leaving every stat evaluation and floating-point sum unchanged.
        targets_at = _AreaRecipientIndex(battle).at

        def sort_key(payload: dict[str, Any]) -> tuple[float, int]:
            cells = self.services.preview_positions(payload.get("cells"))
            if not cells:
                return (0.0, 0)
            enemies = 0
            allies = 0
            enemy_value = 0.0
            for unit in targets_at(cells):
                if unit.player_id == actor.player_id:
                    allies += 1
                else:
                    enemies += 1
                    enemy_value += self.services.hostile_unit_value(unit)
            return (enemies * 1000.0 + enemy_value - allies * 250.0 - len(cells) * 0.01, -len(cells))

        return sorted(payloads, key=sort_key, reverse=True)[:limit]
