"""Skill payload construction from battle previews and skill-owned candidate policies."""

from __future__ import annotations

from typing import Any, Collection, Iterable, Optional, Protocol
from wujiang.tactical.engine.core import ActionError, Battle, Position, Unit
from wujiang.tactical.heroes.ai_policy import AISkillCandidateContext, SharedCandidateGeometry

class SkillPayloadServices(Protocol):
    """The room operations needed to build legal skill payloads."""

    MOVE_SKILL_CODES: Collection[str]
    _SHARED_CANDIDATE_GEOMETRY: SharedCandidateGeometry

    def dedupe_payloads(self, payloads: list[dict]) -> list[dict]: ...
    def distance_to_position(self, battle: Battle, unit: Unit, destination: Position) -> int: ...
    def payload_is_legal(self, battle: Battle, payload: dict) -> bool: ...
    def positions_to_payload(self, cells: Iterable[Position]) -> list[dict[str, int]]: ...
    def preview_positions(self, raw_cells: Any) -> list[Position]: ...


class SkillPayloadBuilder:
    """Skill payload construction with explicit room services."""

    def __init__(self, services: SkillPayloadServices) -> None:
        self.services = services

    def dedupe_payloads(self, payloads: list[dict]) -> list[dict]:
        return self.services.dedupe_payloads(payloads)

    def payload_is_legal(self, battle: Battle, payload: dict) -> bool:
        return self.services.payload_is_legal(battle, payload)

    def _skill_owned_payloads(self, battle: Battle, actor: Unit,
                              action: dict[str, Any]) -> list[dict[str, Any]]:
        code = str(action.get("code") or "")
        skill = self.skill_from_ai_action(actor, action, code)
        return skill.ai_candidate_payloads(AISkillCandidateContext(
            battle, actor, action, self.services._SHARED_CANDIDATE_GEOMETRY, self))

    def skill_payloads_for_action(self, battle: Battle, actor: Unit, action: dict[str, Any]) -> list[dict[str, Any]]:
        preview = action.get("preview", {}) or {}
        code = str(action.get("code") or "")
        target_mode = str(action.get("target_mode") or "none")
        if code in self.services.MOVE_SKILL_CODES and actor.cannot_move:
            return []
        if battle.mounted_unit_for(actor) is not None and code in self.services.MOVE_SKILL_CODES and code != "mounted_leap":
            return []
        base_payload = {"type": "skill", "unit_id": actor.unit_id, "skill_code": code}
        selection = dict(preview.get("selection") or {})
        mode = str(selection.get("mode") or "")
        candidate_policy = getattr(actor, "ai_policy", None)
        if candidate_policy is not None:
            selected = candidate_policy.skill_candidate_payloads(
                AISkillCandidateContext(battle, actor, action, self.services._SHARED_CANDIDATE_GEOMETRY, self))
            if selected is not None:
                return selected
        try:
            skill_builder = getattr(self.skill_from_ai_action(actor, action, code), "ai_candidate_payloads", None)
        except (ActionError, KeyError):
            skill_builder = None
        if skill_builder is not None:
            return skill_builder(AISkillCandidateContext(
                battle, actor, action, self.services._SHARED_CANDIDATE_GEOMETRY, self))
        if mode == "crow_formation":
            return []
        if target_mode in {"none", "self"}:
            return [base_payload]
        if target_mode in {"ally", "enemy", "unit"}:
            if mode == "unit_direction":
                payloads: list[dict[str, Any]] = []
                for target_id in preview.get("target_unit_ids", []):
                    target = battle.get_unit(str(target_id))
                    declared = battle.declared_cell_for_target(actor, target, base_payload)
                    for direction in selection.get("directions", []):
                        if not isinstance(direction, dict) or direction.get("dx") is None or direction.get("dy") is None:
                            continue
                        payload = {
                            "type": "skill",
                            "unit_id": actor.unit_id,
                            "skill_code": code,
                            "target_unit_id": target.unit_id,
                            "direction": {"dx": int(direction["dx"]), "dy": int(direction["dy"])},
                        }
                        if declared is not None:
                            payload["x"] = declared.x
                            payload["y"] = declared.y
                        payloads.append(payload)
                return self.services.dedupe_payloads(payloads)
            if mode == "multi_unit":
                target_ids = [str(unit_id) for unit_id in preview.get("target_unit_ids", [])]
                return [{"type": "skill", "unit_id": actor.unit_id, "skill_code": code, "target_unit_ids": target_ids}] if target_ids else []
            payloads: list[dict[str, Any]] = []
            for target_id in preview.get("target_unit_ids", []):
                target = battle.get_unit(str(target_id))
                declared = battle.declared_cell_for_target(actor, target, base_payload)
                payload = {"type": "skill", "unit_id": actor.unit_id, "skill_code": code, "target_unit_id": target.unit_id}
                if declared is not None:
                    payload["x"] = declared.x
                    payload["y"] = declared.y
                payloads.append(payload)
            return self.services.dedupe_payloads(payloads)
        if target_mode == "cell":
            if mode == "direction":
                payloads: list[dict[str, Any]] = []
                for direction in selection.get("directions", []):
                    payload = dict(base_payload)
                    if isinstance(direction, str):
                        payload["direction"] = direction
                    elif isinstance(direction, dict):
                        if direction.get("code"):
                            payload["direction"] = str(direction["code"])
                        elif direction.get("dx") is not None and direction.get("dy") is not None:
                            payload["dx"] = int(direction["dx"])
                            payload["dy"] = int(direction["dy"])
                        else:
                            continue
                    else:
                        continue
                    payloads.append(payload)
                return self.services.dedupe_payloads(payloads)
            if mode == "pattern_cells":
                payloads: list[dict[str, Any]] = []
                patterns = selection.get("patterns", [])
                try:
                    variant_builder = getattr(self.skill_from_ai_action(actor, action, code),
                                              "ai_candidate_payload_variants", None)
                except (ActionError, KeyError):
                    variant_builder = None
                candidate_policy = getattr(actor, "ai_policy", None)
                if candidate_policy is not None:
                    selected = candidate_policy.skill_candidate_patterns(
                        AISkillCandidateContext(battle, actor, action, self.services._SHARED_CANDIDATE_GEOMETRY, self), patterns)
                    if selected is not None:
                        patterns = selected
                if candidate_policy is None or selected is None:
                    try:
                        skill_builder = getattr(self.skill_from_ai_action(actor, action, code), "ai_candidate_patterns", None)
                    except (ActionError, KeyError):
                        skill_builder = None
                    if skill_builder is not None:
                        patterns = skill_builder(AISkillCandidateContext(
                            battle, actor, action, self.services._SHARED_CANDIDATE_GEOMETRY, self), patterns)
                for pattern in patterns:
                    payload = {
                        "type": "skill",
                        "unit_id": actor.unit_id,
                        "skill_code": code,
                        "cells": self.services.positions_to_payload(self.services.preview_positions(pattern)),
                    }
                    if variant_builder is None:
                        payloads.append(payload)
                    else:
                        payloads.extend(variant_builder(AISkillCandidateContext(
                            battle, actor, action, self.services._SHARED_CANDIDATE_GEOMETRY, self), payload))
                return self.services.dedupe_payloads(payloads)
            if mode == "choice_pattern":
                payloads: list[dict[str, Any]] = []
                for choice in selection.get("choices", []):
                    choice_code = str(choice.get("code") or "")
                    for pattern in choice.get("patterns", []):
                        payloads.append(
                            {
                                "type": "skill",
                                "unit_id": actor.unit_id,
                                "skill_code": code,
                                "choice_code": choice_code,
                                "cells": self.services.positions_to_payload(self.services.preview_positions(pattern)),
                            }
                        )
                return self.services.dedupe_payloads(payloads)
            if mode == "body_direction":
                return self.rock_cannon_payloads(battle, actor)
            if mode == "revive_unit_cell":
                payloads: list[dict[str, Any]] = []
                for candidate in selection.get("candidates", []):
                    revive_unit_id = str(candidate.get("id") or "")
                    for cell in self.services.preview_positions(candidate.get("cells")):
                        payloads.append(
                            {
                                "type": "skill",
                                "unit_id": actor.unit_id,
                                "skill_code": code,
                                "revive_unit_id": revive_unit_id,
                                "x": cell.x,
                                "y": cell.y,
                            }
                        )
                return self.services.dedupe_payloads(payloads)
            payloads = []
            for cell in self.services.preview_positions(preview.get("cells")):
                payload = {"type": "skill", "unit_id": actor.unit_id, "skill_code": code, "x": cell.x, "y": cell.y}
                payload.update(self.direction_payload_for_cell_skill(actor, action, cell))
                payloads.append(payload)
            return self.services.dedupe_payloads(payloads)
        return []


    def heaven_punishment_payloads(self, battle: Battle, actor: Unit, action: dict[str, Any]) -> list[dict[str, Any]]:
        return self._skill_owned_payloads(battle, actor, action)


    def skill_from_ai_action(self, actor: Unit, action: dict[str, Any], code: str) -> Any:
        skill = action.get("_skill_object")
        if skill is not None:
            return skill
        return actor.get_skill(code)


    def copied_skill_action(self, battle: Battle, actor: Unit, copied: Any) -> dict[str, Any]:
        preview = battle.filter_preview_targets(
            actor,
            copied.preview(battle, actor),
            ignore_stealth=copied.ignores_stealth_for_payload(battle, actor, {}),
            replace_cells=copied.target_mode in {"ally", "enemy", "unit"},
            require_line_targeting=copied.target_mode in {"ally", "enemy", "unit"} and copied.requires_direct_unit_target_line,
            line_target_range=copied.direct_unit_target_range(battle, actor, {}),
        )
        return {
            "code": copied.code,
            "name": copied.name,
            "kind": "skill",
            "timing": copied.timing,
            "target_mode": copied.target_mode,
            "preview": preview,
            "_skill_object": copied,
        }


    def mimic_skill_payloads(self, battle: Battle, actor: Unit, action: dict[str, Any]) -> list[dict[str, Any]]:
        return self._skill_owned_payloads(battle, actor, action)


    def mimic_payload_context(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> Optional[tuple[Unit, Any, dict[str, Any], dict[str, Any]]]:
        try:
            return actor.get_skill("mimic_skill").ai_payload_context(battle, actor, payload, self)
        except (ActionError, AttributeError, KeyError, TypeError, ValueError):
            return None


    def royal_soldier_payloads(self, battle: Battle, actor: Unit, action: dict[str, Any]) -> list[dict[str, Any]]:
        return self._skill_owned_payloads(battle, actor, action)


    def agency_contract_payloads(self, battle: Battle, actor: Unit, action: dict[str, Any]) -> list[dict[str, Any]]:
        return self._skill_owned_payloads(battle, actor, action)


    def agency_borrowed_skill_payloads(self, battle: Battle, actor: Unit) -> list[dict[str, Any]]:
        return self._skill_owned_payloads(battle, actor, {"code": "agency_borrowed_skill"})


    def agency_borrowed_payload_context(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> Optional[tuple[Unit, Any, dict[str, Any], dict[str, Any]]]:
        try:
            return actor.get_skill("agency_borrowed_skill").ai_payload_context(battle, actor, payload, self)
        except (ActionError, KeyError):
            return None


    def direction_payload_for_cell_skill(self, actor: Unit, action: dict[str, Any], cell: Position) -> dict[str, Any]:
        if str(action.get("direction_mode") or "none") != "required":
            return {}
        if actor.position is None:
            return {}
        dx = cell.x - actor.position.x
        dy = cell.y - actor.position.y
        step_x = 0 if dx == 0 else (1 if dx > 0 else -1)
        step_y = 0 if dy == 0 else (1 if dy > 0 else -1)
        if step_x == 0 and step_y == 0:
            return {}
        return {"direction": {"dx": step_x, "dy": step_y}}


    def split_payloads(self, battle: Battle, actor: Unit, action: dict[str, Any]) -> list[dict[str, Any]]:
        return self._skill_owned_payloads(battle, actor, action)


    def rock_absorb_payloads(self, battle: Battle, actor: Unit, action: dict[str, Any]) -> list[dict[str, Any]]:
        return self._skill_owned_payloads(battle, actor, action)


    def rock_cannon_payloads(self, battle: Battle, actor: Unit) -> list[dict[str, Any]]:
        return self._skill_owned_payloads(battle, actor, {"code": "rock_cannon"})
