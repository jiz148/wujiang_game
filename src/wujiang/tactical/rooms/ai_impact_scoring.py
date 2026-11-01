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
        if unit.hero_code in {"excel_r238", "excel_r239", "excel_r240", "excel_r242", "excel_r243", "excel_r244", "excel_r247", "excel_r248"}:
            nearest_enemy = min((self.services.distance_to_position(battle, enemy, destination) for enemy in enemies), default=8)
            effective_defense = 4.0 if unit.hero_code == "excel_r243" else unit.stat("defense")
            danger = sum(max(0.0, enemy.stat("attack") - effective_defense + 1.0) * 12.0
                         for enemy in enemies if self.services.distance_to_position(battle, enemy, destination)
                         <= enemy.normal_move_distance() + enemy.targeting_range())
            pressure = (22.0 if unit.hero_code in {"excel_r238", "excel_r243", "excel_r244"} else 13.0)
            safety = 1.3 if unit.hero_code in {"excel_r242", "excel_r239", "excel_r247"} else 0.8
            dog_space = (sum(battle.in_bounds(destination.offset(dx, dy))
                             and battle.can_place_unit(unit, destination.offset(dx, dy), ignore=unit, mover=unit)
                             for dx in (-1, 0, 1) for dy in (-1, 0, 1) if dx or dy)
                         if unit.hero_code == "excel_r247" else 0)
            return -nearest_enemy * pressure - danger * safety + dog_space * 3.0
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
        if code in {"vain_giant_shadow", "gladiator_soul", "electronic_repair"}:
            return False
        if code == "gladiator_gale":
            return False
        if code == "migratory_bird_mark":
            target = battle.units.get(str(payload.get("target_unit_id") or ""))
            return target is not None and target.player_id != actor.player_id
        if code in self.services.MOVE_SKILL_CODES or code in self.services.SUMMON_SKILL_CODES or code in self.services.HEAL_SKILL_CODES:
            return False
        if code in {"fuma_trap", "paralysis_card", "poison_card", "drain_card", "descent_moment", "smoke_spray", "afterimage"}:
            return False
        if code == "weapon_copy":
            return False
        if code in self.services.ALLY_BUFF_SKILL_CODES or code in self.services.SELF_BUFF_SKILL_CODES:
            return False
        if code in {"stance", "great_holy_light"}:
            return False
        if code == "great_funeral":
            return False
        if code in self.services.DAMAGING_SKILL_CODES or code in self.services.HOSTILE_EFFECT_SKILL_CODES:
            return True
        if target_mode == "cell" and preview.get("requires_target"):
            return True
        if target_mode == "cell":
            try:
                skill = self.services.skill_from_ai_action(actor, action, code)
                if not self.services.skill_effect_units(battle, actor, skill, payload):
                    return True
            except Exception:
                return True
        try:
            skill = self.services.skill_from_ai_action(actor, action, code)
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
        if code == "demon_link":
            target = battle.units.get(str(payload.get("target_unit_id") or ""))
            return bool(target is not None and target.player_id != actor.player_id
                        and not target.magic_immunity and not target.skill_non_damage_effects_blocked()
                        and target.dodge_charges <= 0)
        if code in {"bird_dash", "bird_dash_free"}:
            return self.services.r215219_skill_score(battle, actor, payload) > 0
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
        if code == "ancient_cursed_rust":
            from wujiang.tactical.heroes.mechanics.ancient_mecha import ancient_cursed_rust_targets

            return any(target.player_id != actor.player_id and not target.magic_immunity
                       and (target.total_shields() > 0 or target.dodge_charges > 0
                            or not target.has_status("古代伤害适应·普攻"))
                       for target in ancient_cursed_rust_targets(battle, skill.cells(battle, actor)))
        impact_rule = getattr(skill, "ai_effective_impact_rule", None)
        if impact_rule is not None and impact_rule.phase == "before_targets":
            return impact_rule.has_impact(self.services._skill_score_context(
                battle, actor, action, payload, self.services.difficulty_profile("standard")), skill)
        targets = [unit for unit in self.services.skill_effect_units(battle, actor, skill, payload) if unit.player_id != actor.player_id]
        if code == "ancient_jaw":
            return any(not target.magic_immunity and not target.skill_non_damage_effects_blocked()
                       and target.dodge_charges <= 0 for target in targets)
        if impact_rule is not None and impact_rule.phase == "after_targets":
            return impact_rule.has_impact(self.services._skill_score_context(
                battle, actor, action, payload, self.services.difficulty_profile("standard")), skill)
        if code == "kaiser_fist" and targets:
            if self.services.kaiser_fist_score(battle, actor, skill, payload, self.services.difficulty_profile("standard")) > 0:
                return True
        if code in {"interference", "noise_wave", "purify_mana", "sacred_duel"}:
            return self.services.reviewed_r15_effect_score(battle, actor, skill, payload, self.services.difficulty_profile("standard")) > 0
        if code == "heaven_punishment":
            return self.services.score_skill_payload(battle, actor, action, payload, self.services.difficulty_profile("standard"), instant_only=False) > 0
        if code == "fuma_shuriken":
            return self.services.reviewed_r16_damage_result(battle, actor, lambda: skill.execute(battle, actor, payload)) > 8.0
        if not targets:
            return False
        cells = self.services.skill_effect_cells(battle, actor, skill, payload)
        if code == "gale":
            return any(unit.is_summon or unit.is_clone or unit.position is not None for unit in targets)
        if code in self.services.HOSTILE_EFFECT_SKILL_CODES:
            for target in targets:
                if self.services.hostile_skill_effect_target_has_impact(battle, actor, skill, payload, target):
                    return True
        if code in self.services.DAMAGING_SKILL_CODES:
            for target in targets:
                if code == "undead_boy_devour":
                    impact = probe_skill_raw_damage_impact(
                        battle,
                        actor,
                        skill,
                        target,
                        raw_damage=round(target.current_hp / 2, 4),
                        ignore_shield=True,
                    )
                    if impact.changed_target or impact.damage > 0:
                        return True
                    continue
                attack_power = self.services.skill_attack_power(battle, actor, skill, payload, target, cells)
                ignore_shield = bool(skill.ignores_shield_for_payload(battle, actor, payload))
                if code == "illumination_light":
                    ignore_shield = target.attribute == "暗"
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
        if code == "cat_taunt_roar":
            if target.magic_immunity or target.direct_effects_blocked() or target.skill_non_damage_effects_blocked():
                return False
            if target.total_shields() > 0 or target.dodge_charges > 0 or target.is_clone:
                return True
            return not any(
                str(getattr(status, "required_attack_target_id", "") or "") == actor.unit_id
                and not bool(getattr(status, "requirement_satisfied", False))
                for status in target.statuses
            )
        if code in {"sun_slash", "magnetic_wave", "magic_claw", "chain_pull", "premature_burial", "erasure", "dragon_slash", "eagle_eye"}:
            with ai_probe_rollback(battle):
                before = unit_impact_signature(target)
                probe = dict(payload)
                probe.update(skill.queued_payload_metadata(battle, actor, probe))
                skill.execute(battle, actor, probe)
                return unit_impact_signature(target) != before
        if code == "heaven_punishment":
            selected_code = str(payload.get("disabled_skill_code") or "")
            return bool(selected_code) and any(
                getattr(target_skill, "timing", None) == "active"
                and target_skill.code == selected_code
                and not any(getattr(status, "skill_code", None) == selected_code for status in target.statuses)
                for target_skill in skill.public_active_skills(battle, target)
            )
        if code == "purify_mana":
            return target.current_mana > 0 or target.total_shields() > 0
        if code == "interference":
            return target.is_clone or (target.is_summon and target.player_id != actor.player_id)
        if code == "fantasy_move":
            return payload.get("x") is not None and payload.get("y") is not None
        if code == "vain_giant_shadow":
            return not target.cannot_attack and not target.has_status("虚荣巨影")
        if code == "heaven_lock":
            return not target.has_status("天锁") and not target.cannot_normal_move and target.stat("speed") > 0 and not getattr(target, "world_seed_terrain", False)
        if code in {"electric_wind", "snow_avalanche", "sacred_duel", "gale", "eagle_eye", "thor_destroy_lightning"}:
            return True
        if code in {"drain_mana", "large_drain_mana"} and target.current_mana <= 0 and target.total_shields() <= 0:
            return False
        if code == "erasure" and not any(status.name == "抹杀计数点" for status in target.statuses):
            return False
        try:
            return probe_target_effect_impact(
                battle,
                actor,
                target,
                action_name=str(skill.name),
                is_skill=True,
                ignore_shield=bool(skill.ignores_shield_for_payload(battle, actor, payload)),
                half_ignore_shield=bool(skill.half_ignores_shield_for_payload(battle, actor, payload)),
                extra_effect_applies=lambda probe_target: self.services.hostile_skill_extra_effect_applies(code, probe_target),
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
            if payload.get("skill_code") == "gravity_field":
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
        if skill.code == "ancient_cursed_rust":
            from wujiang.tactical.heroes.mechanics.ancient_mecha import ancient_cursed_rust_targets

            return ancient_cursed_rust_targets(battle, skill.get_target_cells_for_payload(battle, actor, payload))
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
