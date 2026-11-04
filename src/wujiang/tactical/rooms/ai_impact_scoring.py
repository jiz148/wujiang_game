"""AIImpactQueries: shared AI scoring and queries for impact."""

from __future__ import annotations

from typing import Any, Callable, Protocol
from typing import Any, Callable, Iterable, Optional
from wujiang.tactical.rooms.ai_probe import ai_probe_rollback
from wujiang.tactical.rooms.ai_targets import living_hostile_combatants
from wujiang.tactical.rooms.ai_damage_probes import (
    DamageImpact, estimate_attack_damage, estimate_skill_damage,
    probe_attack_damage_impact, probe_skill_damage_impact,
    probe_skill_raw_damage_impact, probe_target_effect_impact,
    status_impact_signature, unit_impact_signature,
)
from wujiang.tactical.prediction import (expected_numeric, expected_local, forecasting_active, direct_scoring, rules_enabled,
    prediction_session, record_candidates, note, expected_attack_damage,
    attack_damage_distribution, sum_distributions, prediction_rollback, ForecastRandom, RULES_VERSION,
    expected_vector, joint_utilities_enabled, disable_joint_utilities, stable_observations, UnstableObservation)
from wujiang.tactical.engine.core import (
    ActionError,
    Battle,
    DamageContext,
    HealContext,
    HeroUnit,
    Position,
    QueuedAction,
    TemporaryDefenseStatus,
    Unit,
    battle_state_rollback,
)

class AIImpactQueriesServices(Protocol):
    """The exact room services used by this scoring family."""
    ALLY_BUFF_SKILL_CODES: Any
    DAMAGING_SKILL_CODES: Any
    DifficultyProfile: Any
    HEAL_SKILL_CODES: Any
    HOSTILE_EFFECT_SKILL_CODES: Any
    MOVE_SKILL_CODES: Any
    SELF_BUFF_SKILL_CODES: Any
    SUMMON_SKILL_CODES: Any
    _skill_score_context: Callable[..., Any]
    agency_borrowed_payload_context: Any
    attack_payload_enemy_targets: Callable[..., Any]
    attack_target_has_effective_impact: Callable[..., Any]
    difficulty_profile: Callable[..., Any]
    distance_to_position: Callable[..., Any]
    hostile_skill_effect_target_has_impact: Callable[..., Any]
    hostile_skill_extra_effect_applies: Callable[..., Any]
    kaiser_fist_score: Callable[..., Any]
    mimic_payload_context: Any
    payload_destination: Callable[..., Any]
    preview_positions: Callable[..., Any]
    r215219_skill_score: Callable[..., Any]
    required_attack_target: Callable[..., Any]
    reviewed_r15_effect_score: Callable[..., Any]
    reviewed_r16_damage_result: Callable[..., Any]
    score_skill_payload: Callable[..., Any]
    skill_attack_power: Callable[..., Any]
    skill_effect_cells: Callable[..., Any]
    skill_effect_units: Callable[..., Any]
    skill_from_ai_action: Any
    skill_payload_has_effective_enemy_impact: Callable[..., Any]
    skill_payload_requires_enemy_impact: Callable[..., Any]


class AIImpactQueries:
    """Owns this coherent family; the room keeps compatibility calls."""

    def __init__(self, services: AIImpactQueriesServices) -> None:
        self.services = services

    def score_respawn_destination(self,
        battle: Battle,
        unit: Unit,
        destination: Position,
        role: str,
        profile: DifficultyProfile,
    ) -> float:
        enemies = living_hostile_combatants(battle, unit.player_id)
        for component in unit.traits:
            scorer = getattr(component, "ai_respawn_destination_score", None)
            if callable(scorer) and getattr(component, "respawn_ai_enabled", True):
                return scorer(battle, unit, destination, enemies, self.services.distance_to_position)
        allies = [ally for ally in battle.player_units(unit.player_id) if ally.unit_id != unit.unit_id and ally.alive and ally.position is not None and not ally.banished]
        nearest_enemy = min((self.services.distance_to_position(battle, enemy, destination) for enemy in enemies), default=8)
        nearest_ally = min((self.services.distance_to_position(battle, ally, destination) for ally in allies), default=8)
        score = nearest_enemy * (6.0 if role == "support" else 2.0)
        if role != "support":
            score -= nearest_ally
        return score


    def attack_payload_has_effective_enemy_impact(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> bool:
        for target in self.services.attack_payload_enemy_targets(battle, actor, payload):
            if self.services.attack_target_has_effective_impact(battle, actor, target, payload):
                return True
        return False


    def attack_payload_satisfies_required_target(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> bool:
        forced_target = self.services.required_attack_target(battle, actor)
        if forced_target is None:
            return False
        if str(payload.get("target_unit_id") or "") == forced_target.unit_id:
            return True
        try:
            resolved_payload = battle.resolved_basic_attack_payload(actor, payload)
            cells = battle.payload_positions(resolved_payload, "attack_cells")
            if not cells:
                cells = battle.basic_attack_area_cells_for_payload(actor, resolved_payload) or []
        except Exception:
            cells = self.services.preview_positions(payload.get("cells"))
        forced_cells = {(cell.x, cell.y) for cell in battle.unit_cells(forced_target)}
        return any((cell.x, cell.y) in forced_cells for cell in cells)


    def attack_payload_enemy_targets(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> list[Unit]:
        try:
            resolved_payload = battle.resolved_basic_attack_payload(actor, payload)
        except Exception:
            resolved_payload = dict(payload)
        cells = battle.payload_positions(resolved_payload, "attack_cells")
        if cells:
            return [unit for unit in battle.effect_units_at_cells(cells) if unit.player_id != actor.player_id]
        if payload.get("target_unit_id"):
            try:
                target = battle.effect_recipient(battle.get_unit(str(payload["target_unit_id"])))
            except Exception:
                return []
            return [target] if target.player_id != actor.player_id else []
        try:
            cells = battle.basic_attack_area_cells_for_payload(actor, resolved_payload) or []
        except Exception:
            cells = self.services.preview_positions(payload.get("cells"))
        return [unit for unit in battle.effect_units_at_cells(cells) if unit.player_id != actor.player_id]


    def attack_target_has_effective_impact(self, battle: Battle, actor: Unit, target: Unit, payload: dict[str, Any]) -> bool:
        try:
            resolved_payload = battle.resolved_basic_attack_payload(actor, payload)
            cells = battle.payload_positions(resolved_payload, "attack_cells")
            hit_count = max(1, battle.unit_hit_count_for_cells(target, cells) if cells else 1)
            impact = probe_attack_damage_impact(
                battle,
                actor,
                target,
                resolved_payload,
                attack_power=battle.basic_attack_preview_power(actor, resolved_payload),
                area_cell_hits=hit_count,
            )
            return impact.changed_target or impact.damage > 0
        except Exception:
            return False


    def skill_payload_requires_enemy_impact(self,
        battle: Battle,
        actor: Unit,
        action: dict[str, Any],
        payload: dict[str, Any],
    ) -> bool:
        code = str(action.get("code") or payload.get("skill_code") or "")
        target_mode = str(action.get("target_mode") or "")
        preview = action.get("preview", {}) or {}
        if code == "mimic_skill":
            context = self.services.mimic_payload_context(battle, actor, payload)
            if context is None:
                return False
            _, copied, copied_payload, copied_action = context
            with actor.get_skill("mimic_skill").copying(actor, copied, reserve_point=True):
                return self.services.skill_payload_requires_enemy_impact(battle, actor, copied_action, copied_payload)
        if code == "agency_borrowed_skill":
            context = self.services.agency_borrowed_payload_context(battle, actor, payload)
            if context is None:
                return False
            _, copied, copied_payload, copied_action = context
            with actor.get_skill("agency_borrowed_skill").copying(actor, copied):
                return self.services.skill_payload_requires_enemy_impact(battle, actor, copied_action, copied_payload)
        try:
            skill = self.services.skill_from_ai_action(actor, action, code)
        except Exception:
            skill = None
        if skill is not None:
            requirement = skill.ai_requires_enemy_impact(battle, actor, payload)
            if requirement is not None:
                return requirement
        if code in self.services.MOVE_SKILL_CODES or code in self.services.SUMMON_SKILL_CODES or code in self.services.HEAL_SKILL_CODES:
            return False
        if code in self.services.ALLY_BUFF_SKILL_CODES or code in self.services.SELF_BUFF_SKILL_CODES:
            return False
        if code in self.services.DAMAGING_SKILL_CODES or code in self.services.HOSTILE_EFFECT_SKILL_CODES:
            return True
        if target_mode == "cell" and preview.get("requires_target"):
            return True
        if target_mode == "cell":
            try:
                skill = skill or self.services.skill_from_ai_action(actor, action, code)
                if not self.services.skill_effect_units(battle, actor, skill, payload):
                    return True
            except Exception:
                return True
        try:
            skill = skill or self.services.skill_from_ai_action(actor, action, code)
            return any(unit.player_id != actor.player_id for unit in self.services.skill_effect_units(battle, actor, skill, payload))
        except Exception:
            return False


    def skill_payload_has_effective_enemy_impact(self,
        battle: Battle,
        actor: Unit,
        action: dict[str, Any],
        payload: dict[str, Any],
    ) -> bool:
        code = str(action.get("code") or payload.get("skill_code") or "")
        if code == "mimic_skill":
            context = self.services.mimic_payload_context(battle, actor, payload)
            if context is None:
                return False
            _, copied, copied_payload, copied_action = context
            with actor.get_skill("mimic_skill").copying(actor, copied, reserve_point=True):
                return self.services.skill_payload_has_effective_enemy_impact(battle, actor, copied_action, copied_payload)
        if code == "agency_borrowed_skill":
            context = self.services.agency_borrowed_payload_context(battle, actor, payload)
            if context is None:
                return False
            _, copied, copied_payload, copied_action = context
            with actor.get_skill("agency_borrowed_skill").copying(actor, copied):
                return self.services.skill_payload_has_effective_enemy_impact(battle, actor, copied_action, copied_payload)
        try:
            skill = self.services.skill_from_ai_action(actor, action, code)
        except Exception:
            return False
        owned_impact = skill.ai_effective_enemy_impact(battle, actor, payload, shared=self.services, action=action)
        if owned_impact is not None:
            return owned_impact
        impact_rule = getattr(skill, "ai_effective_impact_rule", None)
        if impact_rule is not None and impact_rule.phase == "before_targets":
            return impact_rule.has_impact(self.services._skill_score_context(
                battle, actor, action, payload, self.services.difficulty_profile("standard")), skill)
        targets = [unit for unit in self.services.skill_effect_units(battle, actor, skill, payload) if unit.player_id != actor.player_id]
        owned_impact = skill.ai_effective_enemy_impact(battle, actor, payload, targets, self.services, action)
        if owned_impact is not None:
            return owned_impact
        if impact_rule is not None and impact_rule.phase == "after_targets":
            return impact_rule.has_impact(self.services._skill_score_context(
                battle, actor, action, payload, self.services.difficulty_profile("standard")), skill)
        if not targets:
            return False
        cells = self.services.skill_effect_cells(battle, actor, skill, payload)
        if code in self.services.HOSTILE_EFFECT_SKILL_CODES:
            for target in targets:
                if self.services.hostile_skill_effect_target_has_impact(battle, actor, skill, payload, target):
                    return True
        if code in self.services.DAMAGING_SKILL_CODES:
            for target in targets:
                raw_impact = skill.ai_raw_damage_for_impact(target)
                if raw_impact is not None:
                    impact = probe_skill_raw_damage_impact(
                        battle,
                        actor,
                        skill,
                        target,
                        raw_damage=raw_impact[0],
                        ignore_shield=raw_impact[1],
                    )
                    if impact.changed_target or impact.damage > 0:
                        return True
                    continue
                attack_power = self.services.skill_attack_power(battle, actor, skill, payload, target, cells)
                ignore_shield = skill.ai_ignores_shield_for_target(battle, actor, payload, target)
                half_ignore_shield = bool(skill.half_ignores_shield_for_payload(battle, actor, payload))
                impact = probe_skill_damage_impact(
                    battle,
                    actor,
                    skill,
                    payload,
                    target,
                    attack_power,
                    cells=cells,
                    ignore_shield=ignore_shield,
                    half_ignore_shield=half_ignore_shield,
                )
                if impact.changed_target or impact.damage > 0:
                    return True
        return False


    def hostile_skill_effect_target_has_impact(self,
        battle: Battle,
        actor: Unit,
        skill: Any,
        payload: dict[str, Any],
        target: Unit,
    ) -> bool:
        code = str(skill.code)
        owned_impact = skill.ai_hostile_target_impact(battle, actor, payload, target)
        if owned_impact is not None:
            return owned_impact
        if skill.ai_probe_hostile_effect:
            with ai_probe_rollback(battle):
                before = unit_impact_signature(target)
                probe = dict(payload)
                probe.update(skill.queued_payload_metadata(battle, actor, probe))
                skill.execute(battle, actor, probe)
                return unit_impact_signature(target) != before
        if skill.ai_always_hostile_effect:
            return True
        try:
            return probe_target_effect_impact(
                battle,
                actor,
                target,
                action_name=str(skill.name),
                is_skill=True,
                ignore_shield=bool(skill.ignores_shield_for_payload(battle, actor, payload)),
                half_ignore_shield=bool(skill.half_ignores_shield_for_payload(battle, actor, payload)),
                extra_effect_applies=skill.ai_extra_effect_applies,
            )
        except Exception:
            return False


    def hostile_skill_extra_effect_applies(self, code: str, target: Unit) -> bool:
        if code in {"drain_mana", "large_drain_mana"}:
            return target.current_mana > 0
        if code == "erasure":
            return any(status.name == "抹杀计数点" for status in target.statuses)
        return True


    def payload_is_legal(self, battle: Battle, payload: dict[str, Any]) -> bool:
        try:
            skill = (battle.get_unit(str(payload["unit_id"])).get_skill(str(payload["skill_code"]))
                     if payload.get("skill_code") else None)
            if skill is not None and skill.ai_legality_requires_rollback:
                with ai_probe_rollback(battle):
                    battle.build_queued_action(payload)
            else:
                battle.build_queued_action(payload)
            return True
        except Exception:
            return False


    def reaction_payload_is_legal(self,
        battle: Battle,
        reactor: Unit,
        queued_action: QueuedAction,
        payload: dict[str, Any],
    ) -> bool:
        action_code = str(payload.get("action_code") or "")
        if action_code in {"block", "counter"}:
            return True
        try:
            skill = reactor.get_skill(action_code)
            stripped = {key: value for key, value in payload.items() if key not in {"type", "unit_id", "action_code"}}
            if action_code == "evasion":
                destination = self.services.payload_destination(payload)
                if destination is None or destination not in skill.evade_cells(battle, reactor):
                    return False
                pending_chain = battle.pending_chain
                if pending_chain is not None:
                    for chosen in pending_chain.chosen_reactions:
                        if chosen.actor_id == reactor.unit_id:
                            continue
                        if chosen.payload.get("action_code") != "evasion":
                            continue
                        if chosen.payload.get("x") == destination.x and chosen.payload.get("y") == destination.y:
                            return False
            ok, _ = skill.can_react_with_payload(battle, reactor, queued_action, stripped)
            return ok
        except Exception:
            return False


    def skill_effect_units(self, battle: Battle, actor: Unit, skill: Any, payload: dict[str, Any]) -> list[Unit]:
        owned_units = skill.ai_effect_units(battle, actor, payload)
        if owned_units is not None:
            return owned_units
        units: list[Unit] = []
        ignored = actor if skill.excludes_caster_from_effect else None
        def filter_units() -> list[Unit]:
            return [unit for unit in battle.effect_units(units, ignore=ignored) if not skill.excludes_allies_from_effect or unit.player_id != actor.player_id]
        payload_cells = self.services.preview_positions(payload.get("cells"))
        if payload_cells and skill.selection_cells_are_effect_cells:
            units.extend(battle.units_at_cells(payload_cells))
            return filter_units()
        try:
            units.extend(skill.get_target_units_for_payload(battle, actor, payload))
        except Exception:
            pass
        try:
            units.extend(battle.units_at_cells(skill.get_target_cells_for_payload(battle, actor, payload)))
        except Exception:
            pass
        return filter_units()


    def skill_effect_cells(self, battle: Battle, actor: Unit, skill: Any, payload: dict[str, Any]) -> list[Position]:
        payload_cells = self.services.preview_positions(payload.get("cells"))
        if payload_cells and skill.selection_cells_are_effect_cells:
            return payload_cells
        try:
            return list(skill.get_target_cells_for_payload(battle, actor, payload))
        except Exception:
            return []


    def skill_attack_power(self,
        battle: Battle,
        actor: Unit,
        skill: Any,
        payload: dict[str, Any],
        target: Unit,
        cells: list[Position],
    ) -> float:
        owned_attack_power = getattr(skill, "ai_attack_power", None)
        if owned_attack_power is not None:
            value = owned_attack_power(battle, actor, payload, target, cells)
            if value is not None:
                return value
        hit_bonus = max(0, battle.unit_hit_count_for_cells(target, cells) - 1) if cells else 0
        return actor.stat("attack") + hit_bonus


_BOUND_SERVICE: AIImpactQueries | None = None

def bind_room(service: AIImpactQueries) -> None:
    global _BOUND_SERVICE
    _BOUND_SERVICE = service

def _require_service() -> AIImpactQueries:
    if _BOUND_SERVICE is None:
        raise RuntimeError("AI room services have not been bound")
    return _BOUND_SERVICE

@expected_numeric(independent=True)
def score_respawn_destination(
    battle: Battle,
    unit: Unit,
    destination: Position,
    role: str,
    profile: DifficultyProfile,
) -> float:
    return _require_service().score_respawn_destination(battle, unit, destination, role, profile)


@expected_numeric(independent=True, boolean=True)
def attack_payload_has_effective_enemy_impact(battle: Battle, actor: Unit, payload: dict[str, Any]) -> bool:
    return _require_service().attack_payload_has_effective_enemy_impact(battle, actor, payload)


@expected_numeric(independent=True, boolean=True)
def attack_payload_satisfies_required_target(battle: Battle, actor: Unit, payload: dict[str, Any]) -> bool:
    return _require_service().attack_payload_satisfies_required_target(battle, actor, payload)


def attack_payload_enemy_targets(battle: Battle, actor: Unit, payload: dict[str, Any]) -> list[Unit]:
    return _require_service().attack_payload_enemy_targets(battle, actor, payload)


@expected_numeric(independent=True, boolean=True)
def attack_target_has_effective_impact(battle: Battle, actor: Unit, target: Unit, payload: dict[str, Any]) -> bool:
    return _require_service().attack_target_has_effective_impact(battle, actor, target, payload)




@expected_numeric(independent=True, boolean=True)
def hostile_skill_effect_target_has_impact(
    battle: Battle,
    actor: Unit,
    skill: Any,
    payload: dict[str, Any],
    target: Unit,
) -> bool:
    return _require_service().hostile_skill_effect_target_has_impact(battle, actor, skill, payload, target)


def hostile_skill_extra_effect_applies(code: str, target: Unit) -> bool:
    return _require_service().hostile_skill_extra_effect_applies(code, target)


@expected_numeric(independent=True, boolean=True)
def payload_is_legal(battle: Battle, payload: dict[str, Any]) -> bool:
    return _require_service().payload_is_legal(battle, payload)


@expected_numeric(independent=True, boolean=True)
def reaction_payload_is_legal(
    battle: Battle,
    reactor: Unit,
    queued_action: QueuedAction,
    payload: dict[str, Any],
) -> bool:
    return _require_service().reaction_payload_is_legal(battle, reactor, queued_action, payload)


def skill_effect_units(battle: Battle, actor: Unit, skill: Any, payload: dict[str, Any]) -> list[Unit]:
    return _require_service().skill_effect_units(battle, actor, skill, payload)


def skill_effect_cells(battle: Battle, actor: Unit, skill: Any, payload: dict[str, Any]) -> list[Position]:
    return _require_service().skill_effect_cells(battle, actor, skill, payload)


@expected_numeric
def skill_attack_power(
    battle: Battle,
    actor: Unit,
    skill: Any,
    payload: dict[str, Any],
    target: Unit,
    cells: list[Position],
) -> float:
    return _require_service().skill_attack_power(battle, actor, skill, payload, target, cells)
