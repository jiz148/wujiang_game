"""ReactionCandidateBuilder for the shared AI candidate pipeline."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Iterable, Optional, Protocol
from wujiang.tactical.engine.core import ActionError, Battle, Position, QueuedAction, Unit
from wujiang.tactical.prediction import forecasting_active, note
from wujiang.tactical.heroes.ai_policy import AIReactionCandidateContext
from wujiang.tactical.heroes.ai_candidate_plan import DEFAULT_CANDIDATES
from wujiang.tactical.rooms.ai_targets import living_hostile_combatants

if TYPE_CHECKING:
    from wujiang.tactical.rooms.ai import AICandidate, DifficultyProfile

class ReactionCandidateBuilderServices(Protocol):
    AICandidate: Any
    DAMAGING_SKILL_CODES: Any
    DifficultyProfile: Any
    REACTION_SHIELD_CODES: Any
    _SHARED_CANDIDATE_GEOMETRY: Any
    agency_borrowed_skill_payloads: Any
    choose_declared_target_cell: Any
    dedupe_payloads: Any
    hostile_unit_value: Any
    incoming_threat_score: Any
    positions_to_payload: Any
    preview_positions: Any
    reaction_payload_is_legal: Any
    required_attack_target: Any
    score_reaction_payload: Any
    with_enemy_view: Any

class ReactionCandidateBuilder:
    """Own reaction candidates construction."""

    def __init__(self, services: ReactionCandidateBuilderServices) -> None:
        self.services = services

    def build_reaction_candidates(self,
        battle: Battle,
        reactor: Unit,
        queued_action: QueuedAction,
        option: dict[str, Any],
        profile: DifficultyProfile,
    ) -> list[AICandidate]:
        unknown_source = forecasting_active() and queued_action.actor_id not in battle.units
        try:
            payloads = self.reaction_payloads_for_option(battle, reactor, queued_action, option)
        except (ActionError, KeyError):
            if not unknown_source:
                raise
            note("unobservable_reaction_payloads_skipped")
            return []
        candidates: list[self.services.AICandidate] = []
        for payload in payloads:
            # The real engine supplied this frozen reaction option. An invisible
            # source is not reintroduced to validate a public defensive response.
            if unknown_source and payload.get("target_unit_id") and str(payload["target_unit_id"]) not in battle.units:
                continue
            if not unknown_source and not self.services.reaction_payload_is_legal(battle, reactor, queued_action, payload):
                continue
            score = self.services.score_reaction_payload(battle, reactor, queued_action, option, payload, profile)
            candidates.append(self.services.AICandidate(payload=payload, score=score, summary=f"react:{option.get('action_code')}"))
        return candidates


    def attack_payloads_for_action(self, battle: Battle, actor: Unit, action: dict[str, Any]) -> list[dict[str, Any]]:
        preview = action.get("preview", {}) or {}
        base_payload = {"type": "attack", "unit_id": actor.unit_id}
        base_payload.update(dict(action.get("attack_payload") or {}))
        if base_payload.get("attack_variant") == "crow_spawn":
            return [{**base_payload, "x": cell.x, "y": cell.y}
                    for cell in self.services.preview_positions(preview.get("cells"))]
        selection = dict(preview.get("selection") or {})
        mode = str(selection.get("mode") or "")
        payloads: list[dict[str, Any]] = []
        if mode == "choice_pattern":
            for choice in selection.get("choices", []):
                code = str(choice.get("code") or "")
                patterns = choice.get("patterns") or []
                for pattern in patterns:
                    cells = self.services.preview_positions(pattern)
                    payloads.extend(
                        self.attack_payloads_for_cells(
                            battle,
                            actor,
                            base_payload,
                            cells,
                            choice_code=code,
                        )
                    )
        elif mode == "pattern_cells":
            for pattern in selection.get("patterns", []):
                cells = self.services.preview_positions(pattern)
                if not cells:
                    continue
                if preview.get("requires_target") and not preview.get("target_unit_ids"):
                    continue
                payload = dict(base_payload)
                payload["cells"] = self.services.positions_to_payload(cells)
                payloads.append(payload)
        elif mode == "direction":
            for direction in selection.get("directions", []):
                payload = dict(base_payload)
                if isinstance(direction, dict):
                    code = str(direction.get("code") or direction.get("direction") or "")
                else:
                    code = str(direction or "")
                if not code:
                    continue
                payload["direction"] = code
                payloads.append(payload)
        else:
            preview_cells = self.services.preview_positions(preview.get("cells"))
            for target_id in preview.get("target_unit_ids", []):
                target = battle.get_unit(str(target_id))
                if not target.alive or target.position is None or target.banished:
                    continue
                declared = self.services.choose_declared_target_cell(battle, target, preview_cells)
                if declared is None:
                    declared = battle.declared_cell_for_target(actor, target, base_payload)
                payload = {
                    **base_payload,
                    "target_unit_id": target.unit_id,
                }
                if declared is not None:
                    payload["x"] = declared.x
                    payload["y"] = declared.y
                payloads.append(payload)
        return self.services.dedupe_payloads(payloads)


    def attack_payloads_for_cells(self,
        battle: Battle,
        actor: Unit,
        base_payload: dict[str, Any],
        cells: list[Position],
        *,
        choice_code: Optional[str] = None,
    ) -> list[dict[str, Any]]:
        payloads: list[dict[str, Any]] = []
        seen: set[str] = set()
        forced_target = self.services.required_attack_target(battle, actor)
        for unit in battle.effect_units_at_cells(cells):
            if (
                unit.player_id == actor.player_id
                and (forced_target is None or unit.unit_id != forced_target.unit_id)
            ) or unit.unit_id in seen:
                continue
            if not battle.unit_can_be_selected(unit, actor=actor)[0]:
                continue
            seen.add(unit.unit_id)
            payload = dict(base_payload)
            payload["target_unit_id"] = unit.unit_id
            if choice_code:
                payload["choice_code"] = choice_code
            declared = self.services.choose_declared_target_cell(battle, unit, cells) or battle.declared_cell_for_target(actor, unit, payload)
            if declared is not None:
                payload["x"] = declared.x
                payload["y"] = declared.y
            payloads.append(payload)
        return payloads


    def reaction_payloads_for_option(self,
        battle: Battle,
        reactor: Unit,
        queued_action: QueuedAction,
        option: dict[str, Any],
    ) -> list[dict[str, Any]]:
        action_code = str(option.get("action_code") or "")
        base_payload = {"type": "chain_react", "unit_id": reactor.unit_id, "action_code": action_code}
        candidate_policy = getattr(reactor, "ai_policy", None)
        if candidate_policy is not None:
            selected = candidate_policy.reaction_candidate_payloads(
                AIReactionCandidateContext(battle, reactor, queued_action, option,
                                           self.services._SHARED_CANDIDATE_GEOMETRY))
            if selected is not None:
                return selected
        if action_code in {"block", "counter", "knockback", "hell_pressure", "hell_pressure_free"}:
            return [base_payload]
        if action_code == "agency_borrowed_skill":
            return [{**base_payload, "contract_payload": payload["contract_payload"]}
                    for payload in self.services.agency_borrowed_skill_payloads(battle, reactor)]
        preview = option.get("preview", {}) or {}
        selection = dict(preview.get("selection") or {})
        mode = str(selection.get("mode") or "")
        if action_code == "lao_damage_stat_cancel":
            return [{**base_payload, "stat_name": str(entry["code"])} for entry in selection.get("stats", [])]
        if action_code == "floating_cannon_cover":
            skill = reactor.get_skill(action_code)
            return [{**base_payload, "target_unit_id": target.unit_id, "cannon_unit_id": cannon.unit_id}
                    for target in skill._threatened_units(battle, reactor, queued_action)
                    for cannon in skill._cannons_near(battle, reactor, target)]
        if action_code in self.services.REACTION_SHIELD_CODES:
            target_ids = self.shield_targets_for_reaction(battle, reactor, queued_action, preview, action_code=action_code)
            if target_ids:
                return [
                    {**base_payload, "target_unit_ids": target_ids[:count]}
                    for count in range(1, len(target_ids) + 1)
                ]
            return []
        if action_code == "backstep_shot":
            return self.backstep_payloads(base_payload, preview)
        if mode == "multi_unit":
            target_ids = [str(unit_id) for unit_id in preview.get("target_unit_ids", [])]
            return [{**base_payload, "target_unit_ids": target_ids}] if target_ids else []
        cell_payloads = []
        for cell in self.services.preview_positions(preview.get("cells")):
            payload = {**base_payload, "x": cell.x, "y": cell.y}
            follow_up_map = dict(preview.get("follow_up_target_ids_by_cell") or {})
            target_ids = follow_up_map.get(f"{cell.x},{cell.y}") or []
            if target_ids:
                payload["target_unit_id"] = str(target_ids[0])
            cell_payloads.append(payload)
        if cell_payloads:
            return cell_payloads
        if preview.get("target_unit_ids"):
            return [{**base_payload, "target_unit_id": str(preview["target_unit_ids"][0])}]
        return []


    def backstep_payloads(self, base_payload: dict[str, Any], preview: dict[str, Any]) -> list[dict[str, Any]]:
        payloads: list[dict[str, Any]] = []
        follow_up_map = dict(preview.get("follow_up_target_ids_by_cell") or {})
        for cell in self.services.preview_positions(preview.get("cells")):
            key = f"{cell.x},{cell.y}"
            target_ids = [str(unit_id) for unit_id in follow_up_map.get(key, [])]
            payloads.append({**base_payload, "x": cell.x, "y": cell.y})
            if target_ids:
                payloads.append({**base_payload, "x": cell.x, "y": cell.y, "target_unit_id": target_ids[0]})
        return payloads


    def shield_targets_for_reaction(self,
        battle: Battle,
        reactor: Unit,
        queued_action: QueuedAction,
        preview: dict[str, Any],
        *,
        action_code: str,
    ) -> list[str]:
        threatened = [
            unit
            for unit_id in preview.get("target_unit_ids", [])
            for unit in [battle.units.get(str(unit_id))]
            if unit is not None
        ]
        threatened = [unit for unit in threatened if unit.alive and unit.position is not None and not unit.banished]
        if not threatened:
            proxy = battle.reaction_proxy_target(reactor, queued_action)
            threatened = [proxy] if proxy is not None else []
        already_protected = self.queued_shield_protected_unit_ids(battle)
        if not threatened:
            return []
        threatened.sort(
            key=lambda unit: (
                unit.unit_id not in already_protected,
                self.services.incoming_threat_score(battle, unit, queued_action) >= unit.current_hp * 100.0 - 1e-9,
                unit.unit_id == reactor.unit_id,
                self.services.incoming_threat_score(battle, unit, queued_action),
                self.services.hostile_unit_value(unit),
            ),
            reverse=True,
        )
        selection = dict(preview.get("selection") or {})
        max_targets = int(selection.get("max_targets") or len(threatened))
        try:
            skill = reactor.get_skill(action_code)
            per_target_cost = float(getattr(skill, "mana_cost", 0.0) or 0.0)
        except Exception:
            per_target_cost = 0.0
        if per_target_cost <= 0:
            return [unit.unit_id for unit in threatened[:max(1, max_targets)]]
        multiplier = float(queued_action.payload.get("reaction_mana_multiplier", 1.0) or 1.0)
        per_target_cost = max(0.0, per_target_cost * multiplier)
        affordable = min(max_targets, int((reactor.current_mana + 1e-9) // per_target_cost))
        if affordable <= 0:
            return []
        return [unit.unit_id for unit in threatened[:affordable]]


    def queued_shield_protected_unit_ids(self, battle: Battle) -> set[str]:
        window = battle.pending_chain
        if window is None:
            return set()
        protected: set[str] = set()
        for reaction in window.chosen_reactions:
            payload = reaction.payload if isinstance(reaction.payload, dict) else {}
            if str(payload.get("action_code") or "") not in self.services.REACTION_SHIELD_CODES:
                continue
            protected.update(str(unit_id) for unit_id in payload.get("target_unit_ids", []) if unit_id)
            if payload.get("target_unit_id"):
                protected.add(str(payload["target_unit_id"]))
        return protected


    def active_damage_mana_reserve(self, actor: Unit) -> float:
        costs = [
            float(getattr(skill, "mana_cost", 0.0) or 0.0)
            for skill in actor.skills
            if str(getattr(skill, "timing", "active")) in {"active", "instant"}
            and str(getattr(skill, "code", "")) in self.services.DAMAGING_SKILL_CODES
            and float(getattr(skill, "mana_cost", 0.0) or 0.0) > 0
            and int(getattr(skill, "cooldown_remaining", 0) or 0) <= 0
            and (
                getattr(skill, "max_uses_per_battle", None) is None
                or int(getattr(skill, "uses_this_battle", 0) or 0) < int(skill.max_uses_per_battle)
            )
        ]
        return min(costs) if costs else 0.0
