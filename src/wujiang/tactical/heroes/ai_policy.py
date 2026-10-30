"""Stateless hero-owned extensions to shared tactical AI scoring."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Iterable, Protocol

if TYPE_CHECKING:
    from wujiang.tactical.engine.core import Battle, Position, QueuedAction, Unit


@dataclass(slots=True)
class AreaAttackScoreState:
    remaining_mana: float


class SharedSkillScoring(Protocol):
    """Generic calculations available to hero policies without importing ai.py."""

    def skill_from_action(self, actor: Unit, action: dict, code: str) -> Any: ...
    def effect_units(self, battle: Battle, actor: Unit, skill: Any, payload: dict) -> Iterable[Unit]: ...
    def damage_score(self, battle: Battle, actor: Unit, skill: Any, payload: dict, profile: Any) -> float: ...
    def r17_effect_score(self, battle: Battle, actor: Unit, skill: Any, payload: dict, profile: Any) -> float: ...
    def r18_effect_score(self, battle: Battle, actor: Unit, skill: Any, payload: dict, profile: Any) -> float: ...
    def r19_effect_score(self, battle: Battle, actor: Unit, skill: Any, payload: dict, profile: Any) -> float: ...
    def owned_effect_score(self, battle: Battle, actor: Unit, skill: Any, payload: dict, profile: Any) -> float: ...
    def attack_damage(self, battle: Battle, source: Unit, target: Unit) -> float: ...
    def reachable_positions(self, battle: Battle, unit: Unit) -> list[Position]: ...
    def reachable_positions_for(self, battle: Battle, unit: Unit, distance: int) -> list[Position]: ...
    def best_move_score(self, battle: Battle, unit: Unit, cells: list[Position], profile: Any) -> float: ...
    def effect_cells(self, battle: Battle, actor: Unit, skill: Any, payload: dict) -> list[Position]: ...
    def area_damage_value(self, battle: Battle, actor: Unit, skill: Any, payload: dict,
                          cells: list[Position]) -> float: ...
    def move_score(self, battle: Battle, actor: Unit, destination: Position, role: str, profile: Any) -> float: ...
    def hero_style(self, actor: Unit) -> str: ...
    def estimate_damage(self, battle: Battle, target: Unit, power: float,
                        *, ignore_shield: bool = False) -> float: ...
    def distance_to_position(self, battle: Battle, unit: Unit, destination: Position) -> int: ...
    def hostile_value(self, target: Unit) -> float: ...
    def friendly_penalty(self, target: Unit) -> float: ...
    def destination(self, payload: dict) -> Position | None: ...
    def summon_position_score(self, battle: Battle, actor: Unit,
                              destination: Position) -> float: ...
    def hostile_units(self, battle: Battle, actor: Unit) -> Iterable[Unit]: ...
    def is_move_skill(self, code: str) -> bool: ...
    def preview_positions(self, raw_cells: Any) -> list[Position]: ...
    def is_damaging_skill(self, code: str) -> bool: ...
    def is_heal_skill(self, code: str) -> bool: ...
    def hell_shuffle_score(self, battle: Battle, actor: Unit, payload: dict) -> float: ...
    def electromagnetic_weather_score(self, battle: Battle, actor: Unit, profile: Any) -> float: ...
    def paid_skill_value(self, battle: Battle, actor: Unit, payload: dict) -> float: ...
    def bird_skill_score(self, battle: Battle, actor: Unit, payload: dict) -> float: ...
    def stone_summon_score(self, battle: Battle, actor: Unit, code: str,
                           payload: dict, profile: Any) -> float: ...
    def effective_enemy_impact(self, battle: Battle, actor: Unit,
                               action: dict, payload: dict) -> bool: ...
    def r12_effect_score(self, battle: Battle, actor: Unit, skill: Any,
                         payload: dict, profile: Any) -> float: ...
    def r13_effect_score(self, battle: Battle, actor: Unit, skill: Any,
                         payload: dict, profile: Any) -> float: ...
    def r13_bat_score(self, battle: Battle, actor: Unit, skill: Any,
                      payload: dict, profile: Any) -> float: ...
    def sun_slash_score(self, battle: Battle, actor: Unit, skill: Any, payload: dict) -> float: ...
    def control_score(self, battle: Battle, actor: Unit, skill: Any,
                      payload: dict, profile: Any) -> float: ...
    def winged_leap_score(self, battle: Battle, actor: Unit, payload: dict) -> float: ...
    def snow_avalanche_score(self, battle: Battle, actor: Unit,
                             skill: Any, payload: dict) -> float: ...
    def morning_holy_light_score(self, battle: Battle, actor: Unit,
                                  skill: Any, payload: dict) -> float: ...
    def probe_rollback(self, battle: Battle) -> Any: ...
    def action_state(self, battle: Battle) -> dict: ...
    def action_delta(self, actor: Unit, before: dict) -> float: ...
    def best_attack_score(self, battle: Battle, actor: Unit, profile: Any) -> float: ...
    def position_value(self, battle: Battle, actor: Unit, target: Unit) -> float: ...


class SharedCandidateGeometry(Protocol):
    """Preview conversion owned by shared AI, callable from hero policies."""

    def preview_positions(self, raw_cells: Any) -> list[Position]: ...
    def positions_to_payload(self, cells: Iterable[Position]) -> list[dict[str, int]]: ...


@dataclass(frozen=True, slots=True)
class AISkillCandidateContext:
    battle: Battle
    actor: Unit
    action: dict
    geometry: SharedCandidateGeometry

    @property
    def code(self) -> str:
        return str(self.action.get("code") or "")

    @property
    def base_payload(self) -> dict[str, Any]:
        return {"type": "skill", "unit_id": self.actor.unit_id,
                "skill_code": self.code}

    def positions(self, raw_cells: Any) -> list[Position]:
        return self.geometry.preview_positions(raw_cells)


@dataclass(frozen=True, slots=True)
class AIReactionCandidateContext:
    battle: Battle
    actor: Unit
    queued_action: Any
    option: dict
    geometry: SharedCandidateGeometry

    @property
    def code(self) -> str:
        return str(self.option.get("action_code") or "")

    @property
    def base_payload(self) -> dict[str, Any]:
        return {"type": "chain_react", "unit_id": self.actor.unit_id,
                "action_code": self.code}

    def positions(self, raw_cells: Any) -> list[Position]:
        return self.geometry.preview_positions(raw_cells)

    def payload_cells(self, cells: Iterable[Position]) -> list[dict[str, int]]:
        return self.geometry.positions_to_payload(cells)


class SharedReactionScoring(Protocol):
    """Shared reaction queries; hero and skill objects do not import ai.py."""

    def base_score(self, battle: Battle, actor: Unit, queued: QueuedAction,
                   option: dict, payload: dict, profile: Any) -> float: ...
    def positions(self, raw_cells: Any) -> list[Position]: ...
    def damage(self, battle: Battle, target: Unit, power: float) -> float: ...
    def primary_target(self, battle: Battle, payload: dict) -> Unit | None: ...
    def threat(self, battle: Battle, target: Unit, queued: QueuedAction) -> float: ...
    def skill_context(self, battle: Battle, actor: Unit, payload: dict,
                      profile: Any, code: str) -> AISkillScoreContext: ...
    def destination(self, payload: dict) -> Position | None: ...
    def destination_still_targeted(self, battle: Battle, actor: Unit,
                                   destination: Position, queued: QueuedAction) -> bool: ...
    def simulated_reaction(self, battle: Battle, actor: Unit, queued: QueuedAction,
                           payload: dict, skill: Any, reservation_cost: float) -> float: ...
    def paid_reaction(self, battle: Battle, actor: Unit, queued: QueuedAction,
                      payload: dict, code: str, multi_target: bool) -> float: ...
    def counter_reaction(self, battle: Battle, actor: Unit, queued: QueuedAction,
                         with_followup: bool) -> float: ...
    def barrier_prevention(self, battle: Battle, actor: Unit, queued: QueuedAction,
                           payload: dict, code: str) -> float: ...
    def queued_skill_damaging(self, queued: QueuedAction) -> bool: ...
    def shield_protected_ids(self, battle: Battle) -> set[str]: ...
    def unit_danger(self, battle: Battle, unit: Unit) -> float: ...
    def outcome_snapshot(self, battle: Battle) -> dict[str, tuple]: ...
    def outcome_value(self, actor: Unit, before: dict[str, tuple]) -> float: ...
    def public_score(self, battle: Battle, actor: Unit, queued: QueuedAction,
                     option: dict, payload: dict, profile: Any) -> float: ...
    def proxy_target(self, battle: Battle, actor: Unit, queued: QueuedAction) -> Unit: ...
    def hostile_units(self, battle: Battle, actor: Unit) -> Iterable[Unit]: ...
    def copied_skill_action(self, battle: Battle, actor: Unit, skill: Any) -> dict: ...
    def attack_window_value(self, battle: Battle, actor: Unit, profile: Any) -> float: ...
    def temporary_defense_prevention(self, battle: Battle, actor: Unit,
                                     queued: QueuedAction) -> float: ...
    def retaliation_penalty(self, battle: Battle, actor: Unit,
                            targets: Iterable[Unit]) -> float: ...
    def counter_spawn_penalty(self, battle: Battle, actor: Unit,
                              target: Unit) -> float: ...
    def hostile_value(self, target: Unit) -> float: ...
    def damage_mana_reserve(self, actor: Unit) -> float: ...


@dataclass(frozen=True, slots=True)
class AIReactionScoreContext:
    battle: Battle
    actor: Unit
    queued_action: QueuedAction
    option: dict
    payload: dict
    profile: Any
    shared: SharedReactionScoring

    @property
    def code(self) -> str:
        return str(self.option.get("action_code") or "")

    def base_score(self, source_code: str) -> float:
        return self.shared.base_score(self.battle, self.actor, self.queued_action,
                                      {**self.option, "action_code": source_code},
                                      self.payload, self.profile)

    def positions(self) -> list[Position]:
        return self.shared.positions(self.payload.get("cells"))

    def damage(self, target: Unit, power: float) -> float:
        return self.shared.damage(self.battle, target, power)

    def primary_target(self) -> Unit | None:
        return self.shared.primary_target(self.battle, self.payload)

    def threat(self, target: Unit) -> float:
        return self.shared.threat(self.battle, target, self.queued_action)

    def skill_context(self, code: str) -> AISkillScoreContext:
        return self.shared.skill_context(self.battle, self.actor, self.payload,
                                         self.profile, code)

    def destination(self) -> Position | None:
        return self.shared.destination(self.payload)

    def destination_still_targeted(self, destination: Position) -> bool:
        return self.shared.destination_still_targeted(self.battle, self.actor,
                                                       destination, self.queued_action)

    def simulated_reaction(self, skill: Any, reservation_cost: float) -> float:
        return self.shared.simulated_reaction(self.battle, self.actor,
                                              self.queued_action, self.payload,
                                              skill, reservation_cost)

    def paid_reaction(self, *, multi_target: bool) -> float:
        return self.shared.paid_reaction(self.battle, self.actor, self.queued_action,
                                         self.payload, self.code, multi_target)

    def counter_reaction(self, *, with_followup: bool) -> float:
        return self.shared.counter_reaction(self.battle, self.actor,
                                            self.queued_action, with_followup)

    def barrier_prevention(self) -> float:
        return self.shared.barrier_prevention(self.battle, self.actor,
                                              self.queued_action, self.payload, self.code)

    def queued_skill_damaging(self) -> bool:
        return self.shared.queued_skill_damaging(self.queued_action)

    def shield_protected_ids(self) -> set[str]:
        return self.shared.shield_protected_ids(self.battle)

    def unit_danger(self, unit: Unit) -> float:
        return self.shared.unit_danger(self.battle, unit)

    def outcome_snapshot(self) -> dict[str, tuple]:
        return self.shared.outcome_snapshot(self.battle)

    def outcome_value(self, before: dict[str, tuple]) -> float:
        return self.shared.outcome_value(self.actor, before)

    def score_as(self, code: str, payload: dict) -> float:
        return self.shared.public_score(self.battle, self.actor, self.queued_action,
            {**self.option, "action_code": code},
            {**payload, "action_code": code}, self.profile)

    def proxy_target(self) -> Unit:
        return self.shared.proxy_target(self.battle, self.actor, self.queued_action)

    def hostile_units(self) -> Iterable[Unit]:
        return self.shared.hostile_units(self.battle, self.actor)

    def prepare_copied_skill(self, skill: Any) -> dict:
        return self.shared.copied_skill_action(self.battle, self.actor, skill)

    def attack_window_value(self, actor: Unit) -> float:
        return self.shared.attack_window_value(self.battle, actor, self.profile)

    def temporary_defense_prevention(self) -> float:
        return self.shared.temporary_defense_prevention(self.battle, self.actor,
                                                         self.queued_action)

    def retaliation_penalty(self, targets: Iterable[Unit]) -> float:
        return self.shared.retaliation_penalty(self.battle, self.actor, targets)

    def counter_spawn_penalty(self, target: Unit) -> float:
        return self.shared.counter_spawn_penalty(self.battle, self.actor, target)

    def hostile_value(self, target: Unit) -> float:
        return self.shared.hostile_value(target)

    def damage_mana_reserve(self) -> float:
        return self.shared.damage_mana_reserve(self.actor)


class FreeReactionScoring:
    """Scores engine-owned block/counter actions through the same reaction context."""

    def score(self, context: AIReactionScoreContext) -> float:
        if context.code == "block":
            proxy_target = context.proxy_target()
            threat = context.threat(proxy_target)
            score = threat + context.temporary_defense_prevention() + 20.0
            if proxy_target.current_hp <= max(0.25, threat / 100.0):
                score += 55.0
            return score
        if context.code == "counter":
            actor = context.actor
            attacker = context.battle.get_unit(context.queued_action.actor_id)
            expected = context.damage(attacker, context.battle.basic_attack_preview_power(actor))
            score = expected * 90.0 + context.hostile_value(attacker) * 0.3
            if expected >= attacker.current_hp - 1e-9:
                score += 80.0
            score -= context.counter_spawn_penalty(attacker)
            score -= context.retaliation_penalty([attacker])
            for trait in actor.traits:
                bonus = getattr(trait, "ai_counter_bonus", None)
                if bonus is not None:
                    score += bonus(context)
            return score
        raise ValueError(f"Unsupported free reaction: {context.code}")


class ShieldReactionScoring:
    """Scores the shared one-shield defense against declared recipients."""

    def score(self, context: AIReactionScoreContext) -> float:
        battle, actor, queued, payload = (context.battle, context.actor,
                                           context.queued_action, context.payload)
        if queued.payload.get("ignore_shield"):
            return -20.0
        target_ids = [str(unit_id) for unit_id in payload.get("target_unit_ids", []) if unit_id]
        if payload.get("target_unit_id"):
            target_ids.append(str(payload["target_unit_id"]))
        targets = [battle.units.get(unit_id) for unit_id in dict.fromkeys(target_ids)]
        targets = [unit for unit in targets if unit is not None]
        targets = [unit for unit in targets if battle.target_can_chain_against(unit, queued)]
        if not targets:
            return -20.0
        if any(unit.unit_id in context.shield_protected_ids() for unit in targets):
            return -40.0
        score = 0.0
        urgent = False
        for target in targets:
            target_threat = context.threat(target)
            score += target_threat + 20.0
            if target.current_hp <= max(0.25, target_threat / 100.0):
                score += 55.0
                urgent = True
            if target.unit_id == actor.unit_id and target_threat > 0:
                score += 18.0
                urgent = True
        skill = None
        try:
            skill = actor.get_skill(context.code)
            cost = float(skill.mana_cost_for_payload(battle, actor, payload))
        except Exception:
            cost = 0.0
        if actor.current_mana - cost < context.damage_mana_reserve() - 1e-9 and not urgent:
            score -= 120.0
        if queued.payload.get("half_ignore_shield"):
            score -= 15.0
        penalty = getattr(skill, "ai_shield_reservation_penalty", None)
        if penalty is not None:
            score -= penalty(context)
        return score


@dataclass(frozen=True, slots=True)
class AISkillScoreContext:
    """One skill evaluation; shared services stay behind a narrow interface."""

    battle: Battle
    actor: Unit
    action: dict
    payload: dict
    profile: Any
    shared: SharedSkillScoring

    def skill(self, code: str) -> Any:
        return self.shared.skill_from_action(self.actor, self.action, code)

    def effect_units(self, skill: Any) -> Iterable[Unit]:
        return self.shared.effect_units(self.battle, self.actor, skill, self.payload)

    def damage_score(self, skill: Any) -> float:
        return self.shared.damage_score(self.battle, self.actor, skill, self.payload, self.profile)

    def r17_effect_score(self, skill: Any) -> float:
        return self.shared.r17_effect_score(self.battle, self.actor, skill, self.payload, self.profile)

    def r18_effect_score(self, skill: Any) -> float:
        return self.shared.r18_effect_score(self.battle, self.actor, skill, self.payload, self.profile)

    def r19_effect_score(self, skill: Any) -> float:
        return self.shared.r19_effect_score(self.battle, self.actor, skill, self.payload, self.profile)

    def owned_effect_score(self, skill: Any) -> float:
        return self.shared.owned_effect_score(self.battle, self.actor, skill, self.payload, self.profile)

    def attack_damage(self, source: Unit, target: Unit) -> float:
        return self.shared.attack_damage(self.battle, source, target)

    def reachable_positions(self, unit: Unit) -> list[Position]:
        return self.shared.reachable_positions(self.battle, unit)

    def reachable_positions_for(self, unit: Unit, distance: int) -> list[Position]:
        return self.shared.reachable_positions_for(self.battle, unit, distance)

    def best_move_score(self, unit: Unit, cells: list[Position]) -> float:
        return self.shared.best_move_score(self.battle, unit, cells, self.profile)

    def move_score_for(self, unit: Unit, destination: Position) -> float:
        return self.shared.move_score(self.battle, unit, destination,
                                      self.shared.hero_style(unit), self.profile)

    def effect_cells(self, skill: Any) -> list[Position]:
        return self.shared.effect_cells(self.battle, self.actor, skill, self.payload)

    def area_damage_value(self, skill: Any, cells: list[Position]) -> float:
        return self.shared.area_damage_value(self.battle, self.actor, skill, self.payload, cells)

    def move_score(self, destination: Position, role: str) -> float:
        return self.shared.move_score(self.battle, self.actor, destination, role, self.profile)

    def hero_style(self) -> str:
        return self.shared.hero_style(self.actor)

    def estimate_damage(self, target: Unit, power: float,
                        *, ignore_shield: bool = False) -> float:
        return self.shared.estimate_damage(self.battle, target, power,
                                           ignore_shield=ignore_shield)

    def distance_to_position(self, unit: Unit, destination: Position) -> int:
        return self.shared.distance_to_position(self.battle, unit, destination)

    def hostile_value(self, target: Unit) -> float:
        return self.shared.hostile_value(target)

    def friendly_penalty(self, target: Unit) -> float:
        return self.shared.friendly_penalty(target)

    def destination(self) -> Position | None:
        return self.shared.destination(self.payload)

    def summon_position_score(self, destination: Position) -> float:
        return self.shared.summon_position_score(self.battle, self.actor, destination)

    def hostile_units(self) -> Iterable[Unit]:
        return self.shared.hostile_units(self.battle, self.actor)

    def is_move_skill(self, code: str) -> bool:
        return self.shared.is_move_skill(code)

    def positions(self, raw_cells: Any) -> list[Position]:
        return self.shared.preview_positions(raw_cells)

    def is_damaging_skill(self, code: str) -> bool:
        return self.shared.is_damaging_skill(code)

    def is_heal_skill(self, code: str) -> bool:
        return self.shared.is_heal_skill(code)

    def hell_shuffle_score(self) -> float:
        return self.shared.hell_shuffle_score(self.battle, self.actor, self.payload)

    def electromagnetic_weather_score(self) -> float:
        return self.shared.electromagnetic_weather_score(self.battle, self.actor, self.profile)

    def paid_skill_value(self) -> float:
        return self.shared.paid_skill_value(self.battle, self.actor, self.payload)

    def bird_skill_score(self) -> float:
        return self.shared.bird_skill_score(self.battle, self.actor, self.payload)

    def stone_summon_score(self, code: str) -> float:
        return self.shared.stone_summon_score(self.battle, self.actor, code,
                                              self.payload, self.profile)

    def effective_enemy_impact(self) -> bool:
        return self.shared.effective_enemy_impact(self.battle, self.actor,
                                                  self.action, self.payload)

    def r12_effect_score(self, skill: Any) -> float:
        return self.shared.r12_effect_score(self.battle, self.actor, skill,
                                            self.payload, self.profile)

    def r13_effect_score(self, skill: Any) -> float:
        return self.shared.r13_effect_score(self.battle, self.actor, skill,
                                            self.payload, self.profile)

    def r13_bat_score(self, skill: Any) -> float:
        return self.shared.r13_bat_score(self.battle, self.actor, skill,
                                         self.payload, self.profile)

    def sun_slash_score(self, skill: Any) -> float:
        return self.shared.sun_slash_score(self.battle, self.actor, skill, self.payload)

    def control_score(self, skill: Any) -> float:
        return self.shared.control_score(self.battle, self.actor, skill,
                                         self.payload, self.profile)

    def winged_leap_score(self) -> float:
        return self.shared.winged_leap_score(self.battle, self.actor, self.payload)

    def snow_avalanche_score(self, skill: Any) -> float:
        return self.shared.snow_avalanche_score(self.battle, self.actor, skill, self.payload)

    def morning_holy_light_score(self, skill: Any) -> float:
        return self.shared.morning_holy_light_score(self.battle, self.actor,
                                                    skill, self.payload)

    def probe_rollback(self) -> Any:
        return self.shared.probe_rollback(self.battle)

    def action_state(self) -> dict:
        return self.shared.action_state(self.battle)

    def action_delta(self, before: dict) -> float:
        return self.shared.action_delta(self.actor, before)

    def best_attack_score(self) -> float:
        return self.shared.best_attack_score(self.battle, self.actor, self.profile)

    def best_attack_score_for(self, unit: Unit) -> float:
        return self.shared.best_attack_score(self.battle, unit, self.profile)

    def position_value(self, target: Unit) -> float:
        return self.shared.position_value(self.battle, self.actor, target)


class HeroAIPolicy:
    """Override only the decisions a hero owns; keep shared scoring generic."""

    contextual_skill_codes: frozenset[str] = frozenset()
    contextual_reaction_codes: frozenset[str] = frozenset()
    contextual_reaction_prefixes: tuple[str, ...] = ()
    reaction_score_phase = "before_hidden_source"

    def contextual_reaction_score(self, context: AIReactionScoreContext) -> float | None:
        return None

    def skill_candidate_payloads(self, context: AISkillCandidateContext) -> list[dict] | None:
        return None

    def skill_candidate_patterns(self, context: AISkillCandidateContext,
                                 patterns: list) -> list | None:
        return None

    def reaction_candidate_payloads(self, context: AIReactionCandidateContext) -> list[dict] | None:
        return None

    def early_move_score_bonus(self, battle: Battle, actor: Unit,
                               destination: Position) -> float:
        return 0.0

    def post_forced_move_score_bonus(self, battle: Battle, actor: Unit,
                                    destination: Position) -> float:
        return 0.0

    def late_move_score_bonus(self, battle: Battle, actor: Unit,
                              destination: Position, allies: list[Unit]) -> float:
        return 0.0

    def aura_move_score_bonus(self, battle: Battle, actor: Unit,
                              destination: Position) -> float:
        return 0.0

    def post_fast_move_score(self, battle: Battle, actor: Unit,
                             destination: Position, enemies: list[Unit],
                             damaging_skill_codes: set[str], score: float) -> float:
        return score

    def focused_move_shortlist(self, battle: Battle, actor: Unit,
                               cells: list[Position]) -> list[Position] | None:
        return None

    def move_candidate_score_bonus(self, battle: Battle, actor: Unit,
                                   destination: Position) -> float:
        return 0.0

    def begin_area_attack_score(self, battle: Battle, actor: Unit,
                                payload: dict, drain_room: float) -> object | None:
        return None

    def area_hit_bonus(self, battle: Battle, actor: Unit, target: Unit,
                       expected_damage: float, state: object | None) -> float:
        return 0.0

    def single_target_attack_bonus(self, battle: Battle, actor: Unit, target: Unit,
                                   payload: dict, expected_damage: float) -> float:
        return 0.0

    def attack_followup_bonus(self, battle: Battle, actor: Unit, target: Unit,
                              payload: dict, expected_damage: float) -> float:
        return 0.0

    def early_attack_synergy_bonus(self, battle: Battle, actor: Unit,
                                   target: Unit, damage: float) -> float:
        return 0.0

    def late_attack_synergy_bonus(self, battle: Battle, actor: Unit,
                                  target: Unit, damage: float) -> float:
        return 0.0

    def target_attack_synergy_bonus(self, battle: Battle, target: Unit,
                                    attacker: Unit, damage: float) -> float:
        return 0.0

    def skill_score_bonus(self, battle: Battle, actor: Unit, action: dict,
                          payload: dict, score: float) -> float:
        return 0.0

    def priority_skill_score(self, battle: Battle, actor: Unit, code: str,
                             payload: dict) -> float | None:
        return None

    def contextual_skill_score(self, context: AISkillScoreContext) -> float | None:
        return None

    def sandstorm_score(self, battle: Battle, actor: Unit, score: float) -> float:
        return score


class PaidReactionAIPolicy(HeroAIPolicy):
    """Configured use of the shared paid-defense probe for one hero's skills."""

    reaction_score_phase = "after_hidden_source"

    def __init__(self, codes: Iterable[str], *, multi_target: bool) -> None:
        self.contextual_reaction_codes = frozenset(codes)
        self.multi_target = multi_target

    def contextual_reaction_score(self, context: AIReactionScoreContext) -> float | None:
        if context.code not in self.contextual_reaction_codes:
            return None
        return context.paid_reaction(multi_target=self.multi_target)


class CounterReactionAIPolicy(HeroAIPolicy):
    """Value a counterattack through the shared attack probe."""

    reaction_score_phase = "after_hidden_source"
    contextual_reaction_codes = frozenset({"counter"})

    def __init__(self, *, with_followup: bool) -> None:
        self.with_followup = with_followup

    def contextual_reaction_score(self, context: AIReactionScoreContext) -> float | None:
        if context.code != "counter":
            return None
        return context.counter_reaction(with_followup=self.with_followup)


class BarrierShieldAIPolicy(HeroAIPolicy):
    """The barrier dragon's shield choice, including next round's lost use."""

    reaction_score_phase = "after_hidden_source"
    contextual_reaction_codes = frozenset({"ion_shield", "quantum_shield"})

    def contextual_reaction_score(self, context: AIReactionScoreContext) -> float | None:
        if context.code not in self.contextual_reaction_codes:
            return None
        prevention = context.barrier_prevention()
        if prevention <= 0:
            return -1000.0
        if context.code == "quantum_shield":
            prevention -= 12.0
            ion = context.actor.get_skill("ion_shield")
            if ion.can_react_with_payload(context.battle, context.actor,
                                          context.queued_action, context.payload)[0]:
                prevention -= 30.0
        return prevention


class CompositeReactionAIPolicy(HeroAIPolicy):
    """Combine independent reaction rules on one hero without overwriting either."""

    reaction_score_phase = "after_hidden_source"

    def __init__(self, *rules: HeroAIPolicy) -> None:
        self.rules = rules
        self.contextual_reaction_codes = frozenset().union(
            *(rule.contextual_reaction_codes for rule in rules))

    def contextual_reaction_score(self, context: AIReactionScoreContext) -> float | None:
        for rule in self.rules:
            if context.code in rule.contextual_reaction_codes:
                result = rule.contextual_reaction_score(context)
                if result is not None:
                    return result
        return None
