"""AIReactionScoring: shared AI scoring and queries for reaction."""

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
from wujiang.tactical.heroes.ai_policy import (AIReactionCandidateContext,
    AIReactionScoreContext, AISkillCandidateContext, AISkillScoreContext,
    BarrierShieldAIPolicy, FreeReactionScoring, ShieldReactionScoring)
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

class AIReactionScoringServices(Protocol):
    """The exact room services used by this scoring family."""
    AREA_ESCAPE_REACTION_CODES: Any
    DifficultyProfile: Any
    REACTION_SHIELD_CODES: Any
    _FREE_REACTION_SCORING: Any
    _SHARED_REACTION_SCORING: Any
    _SHIELD_REACTION_SCORING: Any
    _score_reaction_payload: Callable[..., Any]
    ai_terrain_unit: Callable[..., Any]
    ally_unit_value: Callable[..., Any]
    area_escape_reaction_available: Callable[..., Any]
    best_future_artillery_area_value: Callable[..., Any]
    distance_to_position: Callable[..., Any]
    estimate_damage: Callable[..., Any]
    friendly_fire_penalty: Callable[..., Any]
    hero_style: Callable[..., Any]
    hostile_unit_value: Callable[..., Any]
    incoming_threat_score: Callable[..., Any]
    lethal_counter_reaction_available: Callable[..., Any]
    nova_paid_action_adjustment: Callable[..., Any]
    payload_destination: Callable[..., Any]
    preview_positions: Callable[..., Any]
    score_move_destination: Callable[..., Any]
    skill_attack_power: Callable[..., Any]
    skill_damage_score: Callable[..., Any]
    skill_effect_cells: Callable[..., Any]
    skill_effect_units: Callable[..., Any]


class AIReactionScoring:
    """Owns this coherent family; the room keeps compatibility calls."""

    def __init__(self, services: AIReactionScoringServices) -> None:
        self.services = services

    def score_reaction_payload(self,
        battle: Battle,
        reactor: Unit,
        queued_action: QueuedAction,
        option: dict[str, Any],
        payload: dict[str, Any],
        profile: DifficultyProfile,
    ) -> float:
        score = self.services._score_reaction_payload(battle, reactor, queued_action, option, payload, profile)
        if score > -1000.0:
            adjustment = self.services.nova_paid_action_adjustment(battle, reactor, str(option.get("action_code") or ""), payload)
            if adjustment <= -1000.0:
                return -1000.0
            score += adjustment
        return score


    def _score_reaction_payload(self,
        battle: Battle,
        reactor: Unit,
        queued_action: QueuedAction,
        option: dict[str, Any],
        payload: dict[str, Any],
        profile: DifficultyProfile,
    ) -> float:
        code = str(option.get("action_code") or "")
        reaction_policy = getattr(reactor, "ai_policy", None)
        reaction_skill = next((skill for skill in reactor.skills if skill.code == code), None)
        skill_scorer = getattr(reaction_skill, "ai_reaction_score", None)

        def object_reaction_score(phase: str) -> float | None:
            if (reaction_policy is not None and reaction_policy.reaction_score_phase == phase
                    and (code in reaction_policy.contextual_reaction_codes or any(
                        code.startswith(prefix) for prefix in reaction_policy.contextual_reaction_prefixes))):
                selected = reaction_policy.contextual_reaction_score(
                    AIReactionScoreContext(battle, reactor, queued_action, option, payload,
                                           profile, self.services._SHARED_REACTION_SCORING))
                if selected is not None:
                    return selected
            if skill_scorer is not None and getattr(reaction_skill, "ai_reaction_score_phase",
                                                    "before_hidden_source") == phase:
                return skill_scorer(AIReactionScoreContext(
                    battle, reactor, queued_action, option, payload,
                    profile, self.services._SHARED_REACTION_SCORING))
            return None

        selected = object_reaction_score("before_hidden_source")
        if selected is not None:
            return selected
        if forecasting_active() and queued_action.actor_id not in battle.units:
            if code not in self.services.REACTION_SHIELD_CODES | {"block", "counter", "evasion", "natsume_wind_wall"}:
                return -1000.0
            threat = self.services.incoming_threat_score(battle, reactor, queued_action)
            if threat <= 0:
                return -1000.0
            # Use only the public declared effect and our own resource cost. No
            # location, health, identity or counter-kill bonus from the hidden unit.
            cost = float(option.get("mana_cost", 0.0) or 0.0)
            note("public_unknown_source_defenses")
            return threat * 1.1 + profile.support_bonus - cost * 18.0
        selected = object_reaction_score("after_hidden_source")
        if selected is not None:
            return selected
        if code in {"block", "counter"}:
            return self.services._FREE_REACTION_SCORING.score(AIReactionScoreContext(
                battle, reactor, queued_action, option, payload, profile,
                self.services._SHARED_REACTION_SCORING))
        if code in self.services.REACTION_SHIELD_CODES:
            return self.services._SHIELD_REACTION_SCORING.score(AIReactionScoreContext(
                battle, reactor, queued_action, option, payload, profile,
                self.services._SHARED_REACTION_SCORING))
        return 0.0


    def temporary_defense_prevention_score(self,
        battle: Battle,
        reactor: Unit,
        queued_action: QueuedAction,
    ) -> float:
        before = self.services.incoming_threat_score(battle, reactor, queued_action)
        with ai_probe_rollback(battle):
            probe_reactor = battle.get_unit(reactor.unit_id)
            probe_reactor.add_status(
                TemporaryDefenseStatus(
                    "AI格挡估值",
                    defense_delta=1,
                    description="仅用于AI评估。",
                    expire_with_chain=True,
                )
            )
            after = self.services.incoming_threat_score(battle, probe_reactor, queued_action)
        return max(0.0, before - after)


    def destination_still_in_queued_target_area(self,
        battle: Battle,
        reactor: Unit,
        destination: Position,
        queued_action: QueuedAction,
    ) -> bool:
        if not queued_action.target_cells:
            return False
        target_keys = {(cell.x, cell.y) for cell in queued_action.target_cells}
        return any((cell.x, cell.y) in target_keys for cell in battle.unit_cells_at(reactor, destination))


    def taunt_control_score(self,
        battle: Battle,
        actor: Unit,
        skill: Any,
        payload: dict[str, Any],
        code: str,
        targets: list[Unit],
        profile: DifficultyProfile,
    ) -> float:
        enemies = [unit for unit in targets if unit.player_id != actor.player_id]
        allies = [unit for unit in targets if unit.player_id == actor.player_id]
        if not enemies:
            return -20.0
        score = 0.0
        cells = self.services.skill_effect_cells(battle, actor, skill, payload)
        for unit in enemies:
            if unit.magic_immunity:
                continue
            if code == "cat_taunt_roar":
                if unit.direct_effects_blocked() or unit.skill_non_damage_effects_blocked():
                    continue
                if unit.total_shields() > 0:
                    score += 16.0
                if unit.dodge_charges > 0:
                    score += 10.0
                    continue
                if unit.is_clone:
                    score += self.services.hostile_unit_value(unit) * 0.45 + 70.0
                    continue
                score += self.services.hostile_unit_value(unit) * 0.25
            else:
                attack_power = self.services.skill_attack_power(battle, actor, skill, payload, unit, cells)
                damage = estimate_skill_damage(
                    battle,
                    actor,
                    skill,
                    payload,
                    unit,
                    attack_power,
                    cells=cells,
                    ignore_shield=True,
                    half_ignore_shield=False,
                )
                score += damage * 100.0 + self.services.hostile_unit_value(unit) * 0.45
                if damage >= unit.current_hp - 1e-9:
                    score += 90.0
                    continue
            already_taunted = any(
                str(getattr(status, "required_attack_target_id", "") or "") == actor.unit_id
                and not bool(getattr(status, "requirement_satisfied", False))
                for status in unit.statuses
            )
            if already_taunted:
                score -= 18.0
                continue
            active_skills = sum(1 for skill in unit.skills if getattr(skill, "timing", None) in {"active", "instant"})
            incoming = self.services.estimate_damage(battle, actor, unit.stat("attack"))
            control_value = 42.0 + unit.attack_actions_per_turn() * 16.0 + active_skills * 9.0
            if code == "cat_taunt_roar":
                control_value += min(1.0, unit.current_mana) * 18.0
                control_value -= incoming * 55.0
            else:
                counter_damage = self.services.estimate_damage(battle, unit, actor.stat("attack"))
                control_value += counter_damage * 70.0
                control_value -= incoming * 45.0
            score += control_value
        for unit in allies:
            if unit.magic_immunity:
                continue
            if code == "cat_taunt_roar":
                if unit.direct_effects_blocked() or unit.skill_non_damage_effects_blocked():
                    continue
                if unit.total_shields() > 0:
                    score -= 16.0
                if unit.dodge_charges > 0:
                    score -= 10.0
                    continue
                if unit.is_clone:
                    score -= self.services.friendly_fire_penalty(unit)
                    continue
                already_taunted = any(
                    str(getattr(status, "required_attack_target_id", "") or "") == actor.unit_id
                    and not bool(getattr(status, "requirement_satisfied", False))
                    for status in unit.statuses
                )
                if not already_taunted:
                    active_skills = sum(1 for ally_skill in unit.skills
                                        if getattr(ally_skill, "timing", None) in {"active", "instant"})
                    incoming = self.services.estimate_damage(battle, actor, unit.stat("attack"))
                    score -= 42.0 + unit.attack_actions_per_turn() * 16.0 + active_skills * 9.0
                    score -= incoming * 55.0
            else:
                attack_power = self.services.skill_attack_power(battle, actor, skill, payload, unit, cells)
                damage = estimate_skill_damage(
                    battle,
                    actor,
                    skill,
                    payload,
                    unit,
                    attack_power,
                    cells=cells,
                    ignore_shield=True,
                    half_ignore_shield=False,
                )
                score -= self.services.friendly_fire_penalty(unit) + damage * 120.0
                if damage < unit.current_hp - 1e-9:
                    score -= unit.attack_actions_per_turn() * 22.0
        if code == "ring_taunt":
            score += len(enemies) * 10.0
        return score + profile.aggressive_bonus


    def cycle_summon_score(self,
        battle: Battle,
        actor: Unit,
        code: str,
        destination: Optional[Position],
        profile: DifficultyProfile,
    ) -> float:
        if destination is None:
            return -10.0
        enemies = [
            unit
            for unit in battle.enemy_units(actor.player_id)
            if unit.alive and unit.position is not None and not unit.banished
        ]
        if not enemies:
            return 8.0
        nearest = min(self.services.distance_to_position(battle, enemy, destination) for enemy in enemies)
        future_attack_reach = sum(
            1
            for enemy in enemies
            if self.services.distance_to_position(battle, enemy, destination) <= 6
        )
        exposed_threat = sum(
            max(0.0, enemy.stat("attack") - 2.0)
            for enemy in enemies
            if self.services.distance_to_position(battle, enemy, destination)
            <= enemy.normal_move_distance() + enemy.targeting_range()
        )
        adjacent_open = sum(
            1
            for cell in battle.neighbors(destination)
            if battle.in_bounds(cell) and not battle.units_at_cells([cell]) and (cell.x, cell.y) not in battle.blocked_cells
        )
        score = 44.0 + future_attack_reach * 18.0 + max(0.0, 6.0 - nearest) * 5.0
        score -= exposed_threat * 18.0
        if nearest <= 1:
            score -= 32.0
        if code == "summon_bicycle":
            score += adjacent_open * 5.0
            if adjacent_open == 0:
                score -= 80.0
        else:
            score += adjacent_open * 1.5
        score += profile.aggressive_bonus * 0.5
        return score


    def sphinx_cannon_score(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> float:
        destination = self.services.payload_destination(payload)
        if destination is None:
            return -10.0
        enemies = [unit for unit in battle.enemy_units(actor.player_id) if unit.alive and unit.position is not None and not unit.banished]
        if not enemies:
            return 12.0
        distances = [self.services.distance_to_position(battle, enemy, destination) for enemy in enemies]
        # A remote 3*3 pattern is legal when at least one of its cells is within range,
        # so an enemy up to two cells beyond range can still be covered by its edge.
        in_artillery_reach = sum(1 for distance in distances if distance <= 9)
        nearest = min(distances)
        safety = min(5.0, float(nearest)) * 6.0
        danger_penalty = 36.0 if nearest <= 1 else (12.0 if nearest == 2 else 0.0)
        future_area_value = self.services.best_future_artillery_area_value(battle, actor, destination)
        existing_cannons = [
            unit
            for unit in battle.player_units(actor.player_id)
            if str(getattr(unit, "hero_code", "")) == "sphinx_cannon" and unit.alive and unit.position is not None
        ]
        spacing = min((self.services.distance_to_position(battle, cannon, destination) for cannon in existing_cannons), default=3)
        diversification = min(3.0, float(spacing)) * 4.0 if existing_cannons else 0.0
        return 42.0 + in_artillery_reach * 16.0 + safety - danger_penalty + future_area_value + diversification


    def best_future_artillery_area_value(self, battle: Battle, actor: Unit, destination: Position) -> float:
        best = 0.0
        for start_x in range(-2, battle.width):
            for start_y in range(-2, battle.height):
                cells = [
                    Position(start_x + dx, start_y + dy)
                    for dx in range(3)
                    for dy in range(3)
                    if battle.in_bounds(Position(start_x + dx, start_y + dy))
                ]
                if not cells:
                    continue
                if not any(max(abs(cell.x - destination.x), abs(cell.y - destination.y)) <= 7 for cell in cells):
                    continue
                value = 0.0
                for unit in battle.effect_units_at_cells(cells):
                    hit_count = max(1, battle.unit_hit_count_for_cells(unit, cells))
                    if unit.player_id == actor.player_id:
                        value -= self.services.friendly_fire_penalty(unit) * 0.16
                    else:
                        value += 18.0 + self.services.hostile_unit_value(unit) * 0.12 + (hit_count - 1) * 12.0
                        if self.services.ai_terrain_unit(unit):
                            value += 28.0
                wall_hits = sum((cell.x, cell.y) in battle.blocked_cells for cell in cells)
                value += min(3, wall_hits) * 8.0
                best = max(best, value)
        return best


    def ai_terrain_unit(self, unit: Unit) -> bool:
        return bool(
            getattr(unit, "world_seed_terrain", False)
            or getattr(unit, "is_terrain", False)
            or getattr(unit, "role", "") == "地形单位"
        )


    def area_escape_reaction_available(self,
        battle: Battle,
        actor: Unit,
        skill: Any,
        payload: dict[str, Any],
        target: Unit,
        cells: list[Position],
        *,
        queued_action: QueuedAction | None = None,
    ) -> bool:
        """Whether a target can currently leave an entire declared area by reaction.

        This is intentionally based on public, legal reaction options.  It does not
        predict which option the opponent will choose, but lets irreversible area
        actions discount a defeat that the opponent can already avoid.
        """
        if not cells:
            return False
        if queued_action is None:
            try:
                queued_action = battle.build_queued_action(payload)
            except (ActionError, KeyError, TypeError, ValueError):
                return False
        cell_keys = {(cell.x, cell.y) for cell in cells}
        for option in battle.available_reaction_options(target, queued_action):
            if str(option.action_code) not in self.services.AREA_ESCAPE_REACTION_CODES:
                continue
            preview = option.preview if isinstance(option.preview, dict) else {}
            for destination in self.services.preview_positions(preview.get("cells")):
                footprint = target.footprint_cells_at(destination)
                if footprint and all((cell.x, cell.y) not in cell_keys for cell in footprint):
                    return True
        return False


    def lethal_counter_reaction_available(self,
        battle: Battle,
        actor: Unit,
        payload: dict[str, Any],
        *,
        queued_action: QueuedAction | None = None,
    ) -> bool:
        """A currently legal counter can defeat the caster before the action resolves.

        Probe the real counter resolution so existing shields, immunity and survival
        effects matter. A speed-2 shield the caster could otherwise cast cannot be
        assumed to answer a speed-2 counter. The probe leaves no state or log behind.
        """
        if queued_action is None:
            try:
                queued_action = battle.build_queued_action(payload)
            except (ActionError, KeyError, TypeError, ValueError):
                return False
        for enemy in battle.enemy_units(actor.player_id):
            if not any(option.action_code == "counter" for option in battle.available_reaction_options(enemy, queued_action)):
                continue
            with ai_probe_rollback(battle):
                battle.resolve_reaction_action(enemy, "counter", queued_action)
                if not actor.alive:
                    return True
        return False


    def solar_judgment_score(self,
        battle: Battle,
        actor: Unit,
        skill: Any,
        payload: dict[str, Any],
        profile: DifficultyProfile,
    ) -> float:
        try:
            queued_action = battle.build_queued_action(payload)
        except (ActionError, KeyError, TypeError, ValueError):
            queued_action = None
        if queued_action is not None and self.services.lethal_counter_reaction_available(
            battle, actor, payload, queued_action=queued_action,
        ):
            return -1000.0
        cells = self.services.skill_effect_cells(battle, actor, skill, payload)
        affected = self.services.skill_effect_units(battle, actor, skill, payload)
        enemies = [unit for unit in affected if unit.player_id != actor.player_id]
        allies = [unit for unit in affected if unit.player_id == actor.player_id]
        score = self.services.skill_damage_score(battle, actor, skill, payload, profile)

        @expected_local(battle)
        def projected_damage(target: Unit) -> float:
            attack_power = self.services.skill_attack_power(battle, actor, skill, payload, target, cells)
            return estimate_skill_damage(
                battle,
                actor,
                skill,
                payload,
                target,
                attack_power,
                cells=cells,
                ignore_shield=bool(skill.ignores_shield_for_payload(battle, actor, payload)),
                half_ignore_shield=bool(skill.half_ignores_shield_for_payload(battle, actor, payload)),
            )

        escapable_enemies = [
            target
            for target in enemies
            if queued_action is not None and self.services.area_escape_reaction_available(
                battle, actor, skill, payload, target, cells, queued_action=queued_action,
            )
        ]
        for target in escapable_enemies:
            damage = projected_damage(target)
            score -= damage * 85.0 + self.services.hostile_unit_value(target) * 0.25
            if damage >= target.current_hp - 1e-9:
                score -= 70.0

        reliable_enemy_defeats = [
            target
            for target in enemies
            if target not in escapable_enemies and projected_damage(target) >= target.current_hp - 1e-9
        ]
        allied_defeats = [target for target in allies if projected_damage(target) >= target.current_hp - 1e-9]
        if allied_defeats:
            enemy_loss_value = sum(self.services.hostile_unit_value(target) for target in reliable_enemy_defeats)
            ally_loss_value = sum(self.services.ally_unit_value(target) for target in allied_defeats)
            if len(allied_defeats) >= len(reliable_enemy_defeats) or ally_loss_value >= enemy_loss_value:
                return -1000.0

        defended_targets = [unit for unit in enemies if unit.total_shields() > 0 or bool(getattr(unit, "magic_immunity", False))]
        score += len(defended_targets) * 58.0
        if len(enemies) >= 2:
            score += (len(enemies) - 1) * 72.0

        cell_keys = {(cell.x, cell.y) for cell in cells}
        wall_hits = sum(key in battle.blocked_cells for key in cell_keys)
        terrain_value = min(4, wall_hits) * 18.0
        for unit in affected:
            if not self.services.ai_terrain_unit(unit):
                continue
            terrain_value += 45.0 if unit.player_id != actor.player_id else -75.0
        for effect in battle.field_effects:
            if not getattr(effect, "is_terrain", False):
                continue
            if not any((cell.x, cell.y) in cell_keys for cell in effect.affected_cells(battle)):
                continue
            effect_player = getattr(effect, "player_id", None)
            terrain_value += -55.0 if effect_player == actor.player_id else 28.0
        score += terrain_value

        high_value_single_finish = False
        if len(enemies) == 1:
            target = enemies[0]
            attack_power = self.services.skill_attack_power(battle, actor, skill, payload, target, cells)
            damage = estimate_skill_damage(
                battle,
                actor,
                skill,
                payload,
                target,
                attack_power,
                cells=cells,
                ignore_shield=bool(skill.ignores_shield_for_payload(battle, actor, payload)),
                half_ignore_shield=bool(skill.half_ignores_shield_for_payload(battle, actor, payload)),
            )
            high_value_single_finish = (
                damage >= target.current_hp - 1e-9
                and target.current_hp >= 0.5
                and self.services.hostile_unit_value(target) >= 90.0
            )
            if target.current_hp <= 0.25 and not defended_targets and terrain_value <= 0:
                score -= 100.0

        breakthrough = len(enemies) >= 2 or bool(defended_targets) or terrain_value > 0 or high_value_single_finish
        if not breakthrough:
            score -= 130.0
        if not enemies and terrain_value <= 0:
            score -= 160.0
        if score < profile.once_per_battle_threshold:
            score -= 45.0
        return score


    def iron_chain_path_score(self,
        battle: Battle,
        actor: Unit,
        skill: Any,
        payload: dict[str, Any],
        profile: DifficultyProfile,
    ) -> float:
        score = self.services.skill_damage_score(battle, actor, skill, payload, profile)
        targets = [unit for unit in self.services.skill_effect_units(battle, actor, skill, payload) if unit.player_id != actor.player_id]
        if not targets:
            return -15.0
        try:
            cells = list(skill.chosen_cells(battle, actor, payload))
            anchor_info = skill.anchor_for_cells(battle, actor, cells)
        except (ActionError, AttributeError, TypeError, ValueError):
            anchor_info = None
        if anchor_info is None:
            return score - 45.0

        anchor_kind, anchor_unit, anchor_cells, _ = anchor_info
        try:
            destination = skill.destination_for_anchor(battle, actor, anchor_cells)
        except (ActionError, AttributeError, TypeError, ValueError):
            destination = None
        if destination is None:
            score -= 55.0
        else:
            score += self.services.score_move_destination(battle, actor, destination, self.services.hero_style(actor), profile) * 0.45
            exposure = 0.0
            for enemy in living_hostile_combatants(battle, actor.player_id):
                if self.services.distance_to_position(battle, enemy, destination) > enemy.normal_move_distance() + enemy.targeting_range():
                    continue
                expected_hit = battle.damage_rule.calculate_damage(enemy.stat("attack"), actor.stat("defense"))
                exposure += expected_hit * 75.0 + self.services.hostile_unit_value(enemy) * 0.06
                exposure += max(0, enemy.attack_actions_per_turn() - 1) * 12.0
            score -= exposure

        if anchor_kind == "unit" and anchor_unit is not None:
            if anchor_unit.player_id != actor.player_id:
                score += 32.0
                if destination is not None and actor.attacks_used < actor.attack_actions_per_turn():
                    score += 24.0
            else:
                score -= 18.0
        else:
            score -= 10.0
        score += 24.0
        score += max((self.services.hostile_unit_value(unit) for unit in targets), default=0.0) * 0.2
        if getattr(skill, "uses_this_turn", 0) == 0:
            score += 12.0
        return score


    def reviewed_attack_synergy(self, battle: Battle, actor: Unit, target: Unit, damage: float) -> float:
        score = 0.0
        attack_policy = getattr(actor, "ai_policy", None)
        if attack_policy is not None:
            score += attack_policy.early_attack_synergy_bonus(battle, actor, target, damage)
        if actor.has_status("终结") and damage > 0 and not target.is_clone and not actor.cannot_heal:
            recovery = min(0.25, max(0.0, actor.max_health - actor.current_hp))
            score += recovery * 160.0 + (65.0 if recovery > 0 and actor.current_hp <= 0.25 else 0.0)
        if attack_policy is not None:
            score += attack_policy.late_attack_synergy_bonus(battle, actor, target, damage)
        target_policy = getattr(target, "ai_policy", None)
        if target_policy is not None:
            score += target_policy.target_attack_synergy_bonus(battle, target, actor, damage)
        return score


    def sandstorm_value(self, battle: Battle, actor: Unit) -> float:
        score = 0.0
        for unit in battle.all_units():
            if not unit.alive or unit.banished or battle.unit_in_weather("沙尘", unit):
                continue
            value = (0.0 if unit.attribute == "土" else (0.125 if unit.has_flying else 0.0625) * 100.0)
            if unit.is_stealthed():
                value += 22.0
            score += value if unit.player_id != actor.player_id else -value
        weather_policy = getattr(actor, "ai_policy", None)
        if weather_policy is not None:
            score = weather_policy.sandstorm_score(battle, actor, score)
        return score


_BOUND_SERVICE: AIReactionScoring | None = None

def bind_room(service: AIReactionScoring) -> None:
    global _BOUND_SERVICE
    _BOUND_SERVICE = service

def _require_service() -> AIReactionScoring:
    if _BOUND_SERVICE is None:
        raise RuntimeError("AI room services have not been bound")
    return _BOUND_SERVICE

@expected_numeric(independent=True)
def score_reaction_payload(
    battle: Battle,
    reactor: Unit,
    queued_action: QueuedAction,
    option: dict[str, Any],
    payload: dict[str, Any],
    profile: DifficultyProfile,
) -> float:
    return _require_service().score_reaction_payload(battle, reactor, queued_action, option, payload, profile)


@expected_numeric(independent=True)
def _score_reaction_payload(
    battle: Battle,
    reactor: Unit,
    queued_action: QueuedAction,
    option: dict[str, Any],
    payload: dict[str, Any],
    profile: DifficultyProfile,
) -> float:
    return _require_service()._score_reaction_payload(battle, reactor, queued_action, option, payload, profile)


@expected_numeric(independent=True)
def temporary_defense_prevention_score(
    battle: Battle,
    reactor: Unit,
    queued_action: QueuedAction,
) -> float:
    return _require_service().temporary_defense_prevention_score(battle, reactor, queued_action)


@expected_numeric(independent=True, boolean=True)
def destination_still_in_queued_target_area(
    battle: Battle,
    reactor: Unit,
    destination: Position,
    queued_action: QueuedAction,
) -> bool:
    return _require_service().destination_still_in_queued_target_area(battle, reactor, destination, queued_action)


@expected_numeric(independent=True)
def taunt_control_score(
    battle: Battle,
    actor: Unit,
    skill: Any,
    payload: dict[str, Any],
    code: str,
    targets: list[Unit],
    profile: DifficultyProfile,
) -> float:
    return _require_service().taunt_control_score(battle, actor, skill, payload, code, targets, profile)


@expected_numeric(independent=True)
def cycle_summon_score(
    battle: Battle,
    actor: Unit,
    code: str,
    destination: Optional[Position],
    profile: DifficultyProfile,
) -> float:
    return _require_service().cycle_summon_score(battle, actor, code, destination, profile)


@expected_numeric(independent=True)
def sphinx_cannon_score(battle: Battle, actor: Unit, payload: dict[str, Any]) -> float:
    return _require_service().sphinx_cannon_score(battle, actor, payload)


@expected_numeric(independent=True)
def best_future_artillery_area_value(battle: Battle, actor: Unit, destination: Position) -> float:
    return _require_service().best_future_artillery_area_value(battle, actor, destination)


def ai_terrain_unit(unit: Unit) -> bool:
    return _require_service().ai_terrain_unit(unit)


@expected_numeric(independent=True, boolean=True)
def area_escape_reaction_available(
    battle: Battle,
    actor: Unit,
    skill: Any,
    payload: dict[str, Any],
    target: Unit,
    cells: list[Position],
    *,
    queued_action: QueuedAction | None = None,
) -> bool:
    """Whether a target can currently leave an entire declared area by reaction.

    This is intentionally based on public, legal reaction options.  It does not
    predict which option the opponent will choose, but lets irreversible area
    actions discount a defeat that the opponent can already avoid.
    """
    return _require_service().area_escape_reaction_available(battle, actor, skill, payload, target, cells, queued_action=queued_action)


@expected_numeric(independent=True, boolean=True)
def lethal_counter_reaction_available(
    battle: Battle,
    actor: Unit,
    payload: dict[str, Any],
    *,
    queued_action: QueuedAction | None = None,
) -> bool:
    """A currently legal counter can defeat the caster before the action resolves.

    Probe the real counter resolution so existing shields, immunity and survival
    effects matter. A speed-2 shield the caster could otherwise cast cannot be
    assumed to answer a speed-2 counter. The probe leaves no state or log behind.
    """
    return _require_service().lethal_counter_reaction_available(battle, actor, payload, queued_action=queued_action)


@expected_numeric(independent=True)
def solar_judgment_score(
    battle: Battle,
    actor: Unit,
    skill: Any,
    payload: dict[str, Any],
    profile: DifficultyProfile,
) -> float:
    return _require_service().solar_judgment_score(battle, actor, skill, payload, profile)


@expected_numeric(independent=True)
def iron_chain_path_score(
    battle: Battle,
    actor: Unit,
    skill: Any,
    payload: dict[str, Any],
    profile: DifficultyProfile,
) -> float:
    return _require_service().iron_chain_path_score(battle, actor, skill, payload, profile)


@expected_numeric(independent=True)
def reviewed_attack_synergy(battle: Battle, actor: Unit, target: Unit, damage: float) -> float:
    return _require_service().reviewed_attack_synergy(battle, actor, target, damage)


@expected_numeric(independent=True)
def sandstorm_value(battle: Battle, actor: Unit) -> float:
    return _require_service().sandstorm_value(battle, actor)
