from __future__ import annotations

import random
from copy import deepcopy
from collections import deque
from contextlib import contextmanager
from dataclasses import dataclass
from functools import wraps
from itertools import combinations
from typing import Any, Callable, Iterable, Optional

from wujiang.tactical.engine.core import (
    ActionError,
    Battle,
    DamageContext,
    HealContext,
    Position,
    QueuedAction,
    TemporaryDefenseStatus,
    Unit,
    battle_state_rollback,
)


AI_DIFFICULTIES = {"easy", "standard", "aggressive"}

SUPPORT_HERO_CODES = {"blood_eater", "ellie", "bard", "element_hunter", "chanter", "excel_r139", "excel_r031"}
SUMMON_SKILL_CODES = {
    "sacrifice_ritual",
    "medusa",
    "thunder_god",
    "earth_walker",
    "split",
    "motor_horse",
    "summon_dragon",
    "summon_great_unicorn",
    "summon_mage_cloak",
    "detach_mage_cloak",
    "floating_cannons",
    "judgment_stone",
    "world_seed",
    "royal_soldier",
    "summon_remi_bat",
    "sphinx_cannon",
    "summon_bicycle",
    "summon_unicycle",
    "summon_medium_stone",
    "summon_small_stone",
}
HEAL_SKILL_CODES = {"heal", "heal_mount", "mech_enhancement"}
ALLY_BUFF_SKILL_CODES = {"blood_guard", "blood_art", "blood_dance", "defend_twice", "baptism", "chant", "experiment", "fried_inspire", "agency_contract", "rainbow_mirror"}
SELF_BUFF_SKILL_CODES = {
    "weapon_transfer", "red_charge", "infinite", "infinite_armor", "infinite_robe",
    "red_heat", "essence", "stillness",
    "shensu",
    "harden",
    "stealth",
    "into_darkness",
    "water_wave",
    "crystal_ball",
    "headshot",
    "six_blade_style",
    "n_skill",
    "form_shift",
    "mountain_god_muro",
    "mountain_escape",
    "mountain_awakening",
    "big_shensu",
    "wuchang_mist",
    "inner_dimension_sword",
    "kings_insight",
    "nuclear_rush",
    "floating_cannon_berserk",
    "nian_spirit_pressure",
    "black_cat_form",
    "big_avalanche",
    "martial_god_seal",
    "pandemonium",
    "sky_sanctuary",
    "wetland_grassland",
    "messenger_reincarnation",
    "messenger_creation",
    "beetle_full_force",
}
MOVE_SKILL_CODES = {"leap", "fly_leap", "fate_kick", "crazy_sand", "plasma_thruster", "mounted_leap", "jirobo_follow_step"}
MOVE_SKILL_CODES |= {"zero_dash", "fuma_pursuit", "true_blade_air_slash"}
MOVE_SKILL_CODES.update({"iron_chain_path", "ghost_step", "frey_quick_flash"})
MOVE_SKILL_CODES.update({"flash_slash", "bird_soul", "bird_soul_free"})
AREA_ESCAPE_REACTION_CODES = {"evasion", "backstep_shot", "card_transposition", "shadow_counter", "ghost_step"}
DAMAGING_SKILL_CODES = {
    "paralyzing_glove",
    "machine_gun",
    "pierce",
    "complete_burn",
    "blizzard",
    "great_funeral",
    "judgment_fire",
    "rending",
    "wind_sand",
    "crazy_sand",
    "dragon_breath",
    "rock_cannon",
    "remote_dragon_breath",
    "water_wave_cannon",
    "apocalypse",
    "missile",
    "laser",
    "magnetic_wave",
    "dragon_slash",
    "whirlwind_attack",
    "lao_wave_bullet",
    "lao_mage_hand",
    "demon_blade",
    "nuclear_mutation",
    "gravity_field",
    "migratory_bird_mark",
    "deadly_bow",
    "large_pierce_plus",
    "large_pierce",
    "hundred_bird_burial",
    "remi_chaos",
    "black_cat_paw",
    "nian_large_dragon_breath",
    "nian_roar",
    "kaiser_fist",
    "fuma_shuriken",
    "fantasy_move",
    "undead_boy_devour",
    "illumination_light",
    "hell_slash",
    "vitality_blast",
    "sun_slash",
    "thor_heavy_hammer",
    "thor_rage_impact",
    "thor_destroy_lightning",
    "beetle_spear",
    "electronic_laser",
    "solar_flame",
    "solar_judgment",
    "iron_chain_path",
    "gladiator_claw",
    "gladiator_gale",
    "battle_hurricane",
    "earth_shatter",
    "bird_dash",
    "bird_dash_free",
}
CONTROL_SKILL_CODES = {
    "curse",
    "mana_pull",
    "paralyzing_glove",
    "complete_burn",
    "blizzard",
    "doom_light",
    "magnetic_wave",
    "stance",
    "plant_growth",
    "paralysis_card",
    "poison_card",
    "drain_card",
    "magic_claw",
    "chain_pull",
    "dragon_slash",
    "smoke_spray",
    "heaven_punishment",
    "electric_wind",
    "snow_avalanche",
    "sacred_duel",
    "morning_holy_light",
    "gale",
    "heaven_lock",
    "hundred_bird_burial",
    "nian_roar",
    "nian_jade_flash",
    "interference",
    "noise_wave",
    "purify_mana",
    "fantasy_move",
    "thor_destroy_lightning",
    "eagle_eye",
    "cat_taunt_roar",
    "ring_taunt",
}
REACTION_SHIELD_CODES = {
    "magic_wall",
    "magic_shield",
    "light_wall",
    "stone_wall",
    "ion_shield",
    "quantum_shield",
    "protection",
    "natsume_wind_wall",
    "floating_cannon_cover",
}
DAMAGING_SKILL_CODES.update({"frey_quick_flash", "frey_god_stab", "frey_lion_spear"})

HOSTILE_EFFECT_SKILL_CODES = (
    CONTROL_SKILL_CODES
    | {
        "drain_mana",
        "large_drain_mana",
        "premature_burial",
        "erasure",
        "descent_moment",
        "great_funeral",
        "judgment_fire",
        "rock_absorb",
        "wind_sand",
        "vain_giant_shadow",
    }
) - {"stance"}


@dataclass(slots=True)
class DifficultyProfile:
    action_threshold: float
    reaction_threshold: float
    instant_threshold: float
    aggressive_bonus: float
    support_bonus: float
    once_per_battle_threshold: float


@dataclass(slots=True)
class AICandidate:
    payload: dict[str, Any]
    score: float
    summary: str


def difficulty_profile(name: str) -> DifficultyProfile:
    normalized = str(name or "standard").strip().lower()
    if normalized == "easy":
        return DifficultyProfile(
            action_threshold=25.0,
            reaction_threshold=55.0,
            instant_threshold=90.0,
            aggressive_bonus=0.0,
            support_bonus=8.0,
            once_per_battle_threshold=95.0,
        )
    if normalized == "aggressive":
        return DifficultyProfile(
            action_threshold=12.0,
            reaction_threshold=35.0,
            instant_threshold=45.0,
            aggressive_bonus=18.0,
            support_bonus=0.0,
            once_per_battle_threshold=30.0,
        )
    return DifficultyProfile(
        action_threshold=18.0,
        reaction_threshold=42.0,
        instant_threshold=60.0,
        aggressive_bonus=10.0,
        support_bonus=4.0,
        once_per_battle_threshold=55.0,
    )


@contextmanager
def ai_enemy_view(battle: Battle, player_id: int) -> Iterable[None]:
    """Use the same clone disguise as the opponent UI while evaluating choices."""
    if getattr(battle, "_ai_view_player_id", None) == player_id:
        yield
        return
    clones = [unit for unit in battle.all_units() if unit.is_clone and unit.player_id != player_id]
    if not clones:
        yield
        return
    with ai_probe_rollback(battle):
        battle._ai_view_player_id = player_id
        for clone in clones:
            source = battle.units.get(battle.controlling_hero_id(clone))
            perceived = deepcopy(source if source is not None else clone)
            for attribute in ("unit_id", "player_id", "alive", "banished", "position", "footprint_offsets",
                              "mount_owner_id", "mounted_on_unit_id", "ridden_by_unit_id"):
                setattr(perceived, attribute, deepcopy(getattr(clone, attribute)))
            perceived.is_clone = False
            perceived._battle_ref = getattr(clone, "_battle_ref", None)
            battle.units[clone.unit_id] = perceived
        yield


def with_enemy_view(participant_name: str) -> Callable:
    def decorate(function: Callable) -> Callable:
        @wraps(function)
        def wrapped(battle: Battle, *args: Any, **kwargs: Any) -> Any:
            participant = args[0] if args else kwargs[participant_name]
            if isinstance(participant, Unit):
                units = [participant]
            else:
                units = list(participant)
                if args:
                    args = (units, *args[1:])
                else:
                    kwargs[participant_name] = units
            players = {unit.player_id for unit in units}
            if len(players) != 1:
                return function(battle, *args, **kwargs)
            with ai_enemy_view(battle, next(iter(players))):
                return function(battle, *args, **kwargs)
        return wrapped
    return decorate


@with_enemy_view("actor")
def choose_turn_action(battle: Battle, actor: Unit, difficulty: str) -> dict[str, Any]:
    clear_follow_anchor_cache(battle)
    profile = difficulty_profile(difficulty)
    candidates, move_candidates = turn_action_candidates(battle, actor, profile)
    best_non_move = best_candidate(candidates)
    best_move = best_candidate(move_candidates)
    if best_non_move is not None and best_non_move.score >= profile.action_threshold:
        return best_non_move.payload
    if best_move is not None and best_move.score >= 0:
        return best_move.payload
    if best_non_move is not None and best_non_move.score > 0:
        return best_non_move.payload
    return {"type": "end_turn"}


@with_enemy_view("units")
def choose_turn_bundle_action(battle: Battle, units: Iterable[Unit], difficulty: str) -> tuple[dict[str, Any], Optional[Unit]]:
    clear_follow_anchor_cache(battle)
    profile = difficulty_profile(difficulty)
    non_move_candidates: list[tuple[Unit, AICandidate]] = []
    move_candidates: list[tuple[Unit, AICandidate]] = []
    fallback_candidates: list[tuple[Unit, AICandidate]] = []
    for unit in units:
        if not unit.can_take_turn_actions(battle):
            continue
        actor_candidates, actor_moves = turn_action_candidates(battle, unit, profile)
        for candidate in actor_candidates:
            if candidate.score >= profile.action_threshold:
                non_move_candidates.append((unit, candidate))
            elif candidate.score > 0:
                fallback_candidates.append((unit, candidate))
        for candidate in actor_moves:
            if candidate.score >= 0:
                move_candidates.append((unit, candidate))

    chosen = best_unit_candidate(non_move_candidates)
    if chosen is not None:
        return chosen[1].payload, chosen[0]
    chosen = best_unit_candidate(move_candidates)
    if chosen is not None:
        return chosen[1].payload, chosen[0]
    chosen = best_unit_candidate(fallback_candidates)
    if chosen is not None:
        return chosen[1].payload, chosen[0]
    return {"type": "end_turn"}, None


def turn_action_candidates(
    battle: Battle,
    actor: Unit,
    profile: DifficultyProfile,
) -> tuple[list[AICandidate], list[AICandidate]]:
    action_snapshot = battle.action_snapshot_for(actor)
    candidates: list[AICandidate] = []
    move_candidates: list[AICandidate] = []
    preserve_new_stealth = actor.is_stealthed() and any(
        action in {"skill:stealth", "skill:extra_stealth"}
        for action in actor.actions_taken_this_turn
    )
    for action in action_snapshot.get("actions", []):
        if not action.get("available"):
            continue
        kind = str(action.get("kind") or "")
        if kind == "move":
            move_candidates.extend(build_move_candidates(battle, actor, action, profile))
        elif preserve_new_stealth:
            continue
        elif kind == "attack":
            candidates.extend(build_attack_candidates(battle, actor, action, profile))
        elif kind == "skill":
            candidates.extend(build_skill_candidates(battle, actor, action, profile, instant_only=False))
    if any(candidate.summary in {"skill:stealth", "skill:extra_stealth"} for candidate in candidates):
        can_reposition = any(candidate.score >= 0 for candidate in move_candidates)
        has_followup_action = any(
            candidate.score > 0 and candidate.summary not in {"skill:stealth", "skill:extra_stealth"}
            for candidate in candidates
        )
        if not can_reposition or has_followup_action:
            candidates = [
                candidate for candidate in candidates
                if candidate.summary not in {"skill:stealth", "skill:extra_stealth"}
            ]
    return candidates, move_candidates


@with_enemy_view("units")
def choose_instant_action(battle: Battle, units: Iterable[Unit], difficulty: str) -> Optional[dict[str, Any]]:
    clear_follow_anchor_cache(battle)
    profile = difficulty_profile(difficulty)
    candidates: list[AICandidate] = []
    for unit in units:
        snapshot = battle.action_snapshot_for(unit)
        for action in snapshot.get("actions", []):
            if action.get("kind") != "skill" or not action.get("available"):
                continue
            if str(action.get("timing") or "") != "instant":
                continue
            candidates.extend(build_skill_candidates(battle, unit, action, profile, instant_only=True))
    chosen = best_candidate(candidates)
    if chosen is None or chosen.score < profile.instant_threshold:
        return None
    return chosen.payload


@with_enemy_view("reactor")
def choose_chain_reaction(
    battle: Battle,
    reactor: Unit,
    options: list[dict[str, Any]],
    difficulty: str,
) -> Optional[dict[str, Any]]:
    queued_action = battle.pending_chain.queued_action if battle.pending_chain is not None else None
    if queued_action is None:
        return None
    profile = difficulty_profile(difficulty)
    candidates: list[AICandidate] = []
    for option in options:
        candidates.extend(build_reaction_candidates(battle, reactor, queued_action, option, profile))
    chosen = best_candidate(candidates)
    if chosen is None or chosen.score < profile.reaction_threshold:
        return None
    return chosen.payload


@with_enemy_view("unit")
def choose_respawn_action(battle: Battle, unit: Unit, options: list[Position], difficulty: str) -> Optional[dict[str, Any]]:
    if not options:
        return None
    profile = difficulty_profile(difficulty)
    role = hero_style(unit)
    best: tuple[float, Position] | None = None
    for destination in options:
        score = score_respawn_destination(battle, unit, destination, role, profile)
        if best is None or score > best[0]:
            best = (score, destination)
    if best is None:
        return None
    destination = best[1]
    return {"type": "respawn_select", "unit_id": unit.unit_id, "x": destination.x, "y": destination.y}


def choose_damage_choice_action(battle: Battle) -> dict[str, Any]:
    prompt = battle.pending_damage_choice
    if prompt is None:
        raise ActionError("当前没有待选择的伤害。")
    unit = battle.get_unit(prompt["unit_id"])
    if prompt.get("kind") == "end_dash":
        with ai_enemy_view(battle, unit.player_id):
            return choose_uesugi_end_dash_action(battle, prompt, unit)
    if prompt.get("kind") == "electronic_teleport":
        with ai_enemy_view(battle, unit.player_id):
            return choose_electronic_teleport_action(battle, prompt, unit)
    if prompt.get("kind") in {"rotation", "attack_swap", "formation", "optional_swap", "optional_placement"}:
        with ai_enemy_view(battle, unit.player_id):
            return choose_rotation_damage_choice_action(battle, prompt, unit)
    damage = float(prompt["damage"])
    stats = list(prompt["stats"])
    choice = "decline"
    if stats and (damage >= unit.current_hp or damage >= max(0.4, unit.current_hp * 0.45)):
        costs = {"attack": 48, "defense": 52, "speed": 40, "attack_range": 44}
        choice = min(stats, key=lambda name: (costs[name] + (30 if unit.stat(name) <= 2 else 0), name))
    return {"type": "damage_choice", "unit_id": unit.unit_id, "stat_name": choice}


def choose_rotation_damage_choice_action(battle: Battle, prompt: dict[str, Any], unit: Unit) -> dict[str, Any]:
    units = list(battle.all_units())
    initial = {
        candidate.unit_id: (candidate.current_hp, candidate.current_mana,
                            candidate.total_shields(), candidate.alive,
                            reviewed_r17_position_value(battle, unit, candidate),
                            alexander_magic_coverage_value(battle, unit, candidate) if prompt.get("kind") == "attack_swap" else 0.0)
        for candidate in units
    }

    def outcome(choice: str) -> float:
        with battle_state_rollback(battle, ai_probe=False):
            # The outer enemy-view context is itself a probe; replay the real
            # damage hooks against that visible view, then restore every state.
            battle._ai_probe_active = False
            battle.pending_damage_choice = None
            battle._damage_choice_active = True
            battle._damage_choice_decisions = [*prompt["decisions"], choice]
            battle._damage_choice_event_index = 0
            battle._damage_choice_probe_default = True
            try:
                battle._perform_action_steps(deepcopy(prompt["action_payload"]))
            except (ActionError, KeyError, ValueError, TypeError):
                return -1000.0
            score = 0.0
            for candidate in units:
                hp, mana, shields, alive, position_value, magic_value = initial[candidate.unit_id]
                value = (hp - candidate.current_hp) * 100.0
                value += (mana - candidate.current_mana) * 18.0
                value += max(0, shields - candidate.total_shields()) * 20.0
                if alive and not candidate.alive:
                    value += 90.0
                score += -value if candidate.player_id == unit.player_id else value
                if candidate.position is not None and candidate.alive:
                    position_gain = reviewed_r17_position_value(battle, unit, candidate) - position_value
                    score += position_gain * (0.8 if candidate.player_id == unit.player_id else -0.8)
                    if mandatory and candidate.player_id == unit.player_id and candidate.unit_id != unit.unit_id:
                        score += alexander_magic_coverage_value(battle, unit, candidate) - magic_value
            return score

    mandatory = prompt.get("kind") in {"attack_swap", "formation"}
    baseline = outcome("decline") if not mandatory else -1000.0
    best = (baseline + 8.0, "decline")
    for ally_id in prompt["options"]:
        score = outcome(ally_id)
        if mandatory and best[1] == "decline" or score > best[0]:
            best = (score, ally_id)
    return {"type": "damage_choice", "unit_id": unit.unit_id, "target_unit_id": best[1]}


def choose_uesugi_end_dash_action(battle: Battle, prompt: dict[str, Any], unit: Unit) -> dict[str, Any]:
    trait = next((component for component in unit.iter_components() if component.name == "轮末穿行"), None)
    if trait is None:
        return {"type": "damage_choice", "unit_id": unit.unit_id, "target_unit_id": "decline"}
    routes = trait.routes(battle, unit)
    best = (8.0, "decline")
    for choice in prompt.get("options", []):
        path = routes.get(choice)
        if path is None:
            continue
        with ai_probe_rollback(battle):
            before = r23_action_state(battle)
            position_before = reviewed_r17_position_value(battle, unit, unit)
            try:
                battle.move_unit(unit, path[-1], forced=True, ignore_units=True, path=path[1:],
                                 max_distance=6, exact_distance=6, straight_only=True,
                                 tags={"uesugi_end_dash", f"source:{unit.unit_id}"})
                for target, _ in battle.path_crossing_events(unit, path):
                    if unit.alive and target.alive:
                        battle.resolve_damage(DamageContext(
                            source=unit, target=target, attack_power=max(0.0, unit.stat("attack") - 1),
                            is_skill=False, from_field_effect=True, action_name="轮末穿人伤害",
                            tags={"movement", "pass_through_damage"}))
            except (ActionError, KeyError, TypeError, ValueError):
                continue
            score = r23_action_delta(unit, before)
            score += 0.9 * (reviewed_r17_position_value(battle, unit, unit) - position_before)
            if score > best[0]:
                best = (score, choice)
    return {"type": "damage_choice", "unit_id": unit.unit_id, "target_unit_id": best[1]}


def choose_electronic_teleport_action(battle: Battle, prompt: dict[str, Any], unit: Unit) -> dict[str, Any]:
    trait = next((component for component in unit.iter_components()
                  if component.name == "电子龙轮末瞬移"), None)
    if trait is None:
        return {"type": "damage_choice", "unit_id": unit.unit_id, "target_unit_id": "decline"}
    routes = trait.routes(battle, unit)
    original_value = reviewed_r17_position_value(battle, unit, unit)
    original_hp = unit.current_hp
    best = (6.0, "decline")
    for choice in prompt.get("options", []):
        destination = routes.get(choice)
        if destination is None:
            continue
        with ai_probe_rollback(battle):
            try:
                battle.move_unit(unit, destination, forced=True, allow_anywhere=True,
                                 tags={"electronic_teleport", f"source:{unit.unit_id}"})
            except (ActionError, KeyError, TypeError, ValueError):
                continue
            score = reviewed_r17_position_value(battle, unit, unit) - original_value
            score += (unit.current_hp - original_hp) * 100.0
            if score > best[0]:
                best = (score, choice)
    return {"type": "damage_choice", "unit_id": unit.unit_id, "target_unit_id": best[1]}


def build_move_candidates(
    battle: Battle,
    actor: Unit,
    action: dict[str, Any],
    profile: DifficultyProfile,
) -> list[AICandidate]:
    if battle.mounted_unit_for(actor) is not None:
        return []
    role = hero_style(actor)
    current_score = score_move_destination(battle, actor, actor.position, role, profile) if actor.position is not None else 0.0
    candidates: list[AICandidate] = []
    if getattr(actor, "hero_code", "") == "excel_r118":
        candidates.extend(build_zero_crossing_move_candidates(battle, actor, role, profile))
    for cell in preview_positions(action.get("preview", {}).get("cells")):
        payload = {"type": "move", "unit_id": actor.unit_id, "x": cell.x, "y": cell.y}
        if not payload_is_legal(battle, payload):
            continue
        score = score_move_destination(battle, actor, cell, role, profile) - current_score - 1.0
        if actor.hero_code == "excel_r118":
            try:
                path = battle.find_path(actor, cell, max_distance=actor.remaining_normal_move_distance(battle), use_movement_cost=True)
                score += zero_path_effect_score(battle, actor, path)
            except ActionError:
                continue
        if actor.hero_code == "excel_r120":
            score = blind_paid_move_score(battle, actor, cell, profile)
        if getattr(actor, "hero_code", "") == "judgment_stone" and any(unit.player_id != actor.player_id for unit in battle.units_at(cell)):
            score = judgment_stone_collision_move_score(battle, actor, cell, profile)
            if score <= 0:
                continue
        candidates.append(AICandidate(payload=payload, score=score, summary=f"move:{cell.x},{cell.y}"))
    return candidates


def direct_adjacent_path(start: Position, destination: Position) -> list[Position]:
    path: list[Position] = []
    current = start
    while current != destination:
        dx = 0 if current.x == destination.x else (1 if destination.x > current.x else -1)
        dy = 0 if current.y == destination.y else (1 if destination.y > current.y else -1)
        current = current.offset(dx, dy)
        path.append(current)
    return path


def build_zero_crossing_move_candidates(
    battle: Battle,
    actor: Unit,
    role: str,
    profile: DifficultyProfile,
) -> list[AICandidate]:
    if actor.position is None:
        return []
    remaining = int(actor.remaining_normal_move_distance(battle))
    if remaining < 2:
        return []
    current_score = score_move_destination(battle, actor, actor.position, role, profile)
    candidates: list[AICandidate] = []
    for target in battle.enemy_units(actor.player_id):
        target_cells = set(battle.unit_cells(target))
        for ingress in target_cells:
            for exit_cell in battle.neighbors(ingress):
                if exit_cell in target_cells:
                    continue
                if not battle.can_place_unit(actor, exit_cell, ignore=actor, mover=actor):
                    continue
                path = direct_adjacent_path(actor.position, ingress)
                if len(path) + 1 > remaining:
                    continue
                path.append(exit_cell)
                while len(path) + 2 <= remaining:
                    path.extend([ingress, exit_cell])
                payload = {
                    "type": "move",
                    "unit_id": actor.unit_id,
                    "x": exit_cell.x,
                    "y": exit_cell.y,
                    "path": [cell.to_dict() for cell in path],
                }
                if not payload_is_legal(battle, payload):
                    continue
                crossing_events = battle.path_crossing_units(actor, [actor.position, *path])
                score = score_move_destination(battle, actor, exit_cell, role, profile) - current_score - 1.0
                score += zero_path_effect_score(battle, actor, [actor.position, *path])
                candidates.append(
                    AICandidate(
                        payload=payload,
                        score=score,
                        summary=f"move:zero-crossings={len(crossing_events)}:{exit_cell.x},{exit_cell.y}",
                    )
                )
    candidates.sort(key=lambda candidate: candidate.score, reverse=True)
    return candidates[:24]


@with_enemy_view("actor")
def build_attack_candidates(
    battle: Battle,
    actor: Unit,
    action: dict[str, Any],
    profile: DifficultyProfile,
) -> list[AICandidate]:
    payloads = attack_payloads_for_action(battle, actor, action)
    candidates: list[AICandidate] = []
    for payload in payloads:
        if not payload_is_legal(battle, payload):
            continue
        copied_support = (actor.has_status("武装复制") and str(payload.get("target_unit_id") or "") in battle.units
                          and battle.units[str(payload["target_unit_id"])].player_id == actor.player_id
                          and red_attack_value(battle, actor, payload) > 0)
        natsume_support = natsume_support_attack_score(battle, actor, payload) > 0
        if not copied_support and not natsume_support and actor.hero_code != "n" and allied_attack_heal_score(battle, actor, payload) <= 0 and not attack_payload_has_effective_enemy_impact(battle, actor, payload) and not attack_payload_satisfies_required_target(
            battle,
            actor,
            payload,
        ):
            continue
        score = score_attack_payload(battle, actor, payload, profile)
        score -= stone_attack_spawn_penalty(battle, actor, payload)
        score *= wuchang_action_success_probability(battle, actor, payload)
        score -= cat_retaliation_action_penalty(battle, actor, attack_effect_units(battle, actor, payload))
        candidates.append(AICandidate(payload=payload, score=score, summary=f"attack:{payload.get('target_unit_id')}"))
    return candidates


@with_enemy_view("actor")
def build_skill_candidates(
    battle: Battle,
    actor: Unit,
    action: dict[str, Any],
    profile: DifficultyProfile,
    *,
    instant_only: bool,
) -> list[AICandidate]:
    payloads = skill_payloads_for_action(battle, actor, action)
    code = str(action.get("code") or "")
    if code in {"battle_hurricane", "water_wave_cannon", "earth_shatter", "satellite_cannon"}:
        payloads = dedupe_damage_area_payloads(battle, payloads)
    if code == "interference":
        payloads = dedupe_damage_area_payloads(battle, payloads)
    if code == "noise_wave":
        payloads = dedupe_area_payloads_by_affected_units(battle, payloads, clones_and_summons_only=False)
    if code == "fuma_shuriken":
        payloads = dedupe_damage_area_payloads(battle, payloads)
    pre_scored: dict[str, float] = {}
    prequalified_payloads: set[str] = set()
    if code in {"paralysis_card", "poison_card", "drain_card", "sacrifice_ritual", "electronic_repair", "satellite_cannon", "descent_moment", "smoke_spray", "dragon_slash", "world_seed", "mimic_skill", "frey_quick_flash", "frey_god_stab", "frey_lion_spear", "royal_soldier", "agency_contract", "agency_borrowed_skill", "morning_holy_light", "lao_wave_bullet", "interference", "noise_wave", "fuma_shuriken", "fuma_trap", "fantasy_move", "rainbow_mirror", "true_blade_air_slash", "eagle_eye", "missile", "gladiator_claw", "gladiator_gale", "battle_hurricane", "flash_slash", "bird_dash", "bird_dash_free", "bird_soul", "bird_soul_free"}:
        if code == "frey_quick_flash":
            unique = {}
            skill = skill_from_ai_action(actor, action, code)
            for candidate in payloads:
                try:
                    _, strike, destination = skill.validate_selection(battle, actor, candidate)
                    unique.setdefault((destination, strike), candidate)
                except (ActionError, ValueError, TypeError, KeyError):
                    continue
            payloads = list(unique.values())
        for candidate in payloads:
            pre_scored[repr(candidate)] = score_skill_payload(battle, actor, action, candidate, profile, instant_only=instant_only)
        ranked_payloads = sorted(payloads, key=lambda candidate: pre_scored[repr(candidate)], reverse=True)
        payloads = []
        for candidate in ranked_payloads:
            key = repr(candidate)
            if skill_payload_requires_enemy_impact(battle, actor, action, candidate) and not skill_payload_has_effective_enemy_impact(
                battle, actor, action, candidate,
            ):
                continue
            prequalified_payloads.add(key)
            payloads.append(candidate)
            if len(payloads) >= 64:
                break
    else:
        payloads = trim_skill_payloads_for_ai(battle, actor, payloads, limit=64)
    candidates: list[AICandidate] = []
    selection_mode = str((action.get("preview", {}) or {}).get("selection", {}).get("mode") or "")
    for payload in payloads:
        generated_from_preview = bool(payload.get("cells")) and selection_mode in {"pattern_cells", "choice_pattern", ""}
        needs_explicit_legality = code in {"heaven_punishment"}
        if (needs_explicit_legality or not generated_from_preview) and not payload_is_legal(battle, payload):
            continue
        if should_throttle_unlimited_nonhostile_skill(battle, actor, action, payload):
            continue
        if repr(payload) not in prequalified_payloads and skill_payload_requires_enemy_impact(battle, actor, action, payload) and not skill_payload_has_effective_enemy_impact(
            battle,
            actor,
            action,
            payload,
        ):
            continue
        score = pre_scored[repr(payload)] if repr(payload) in pre_scored else score_skill_payload(battle, actor, action, payload, profile, instant_only=instant_only)
        try:
            skill = skill_from_ai_action(actor, action, code)
            targets = skill_effect_units(battle, actor, skill, payload)
        except (ActionError, KeyError, ValueError):
            targets = []
        chance = wuchang_action_success_probability(battle, actor, payload)
        score = score * chance - (1.0 - chance) * float(action.get("mana_cost") or 0.0) * 12.0
        score -= cat_retaliation_action_penalty(battle, actor, targets)
        candidates.append(AICandidate(payload=payload, score=score, summary=f"skill:{action.get('code')}"))
    return candidates


def dedupe_area_payloads_by_affected_units(
    battle: Battle,
    payloads: list[dict[str, Any]],
    *,
    clones_and_summons_only: bool,
) -> list[dict[str, Any]]:
    unique: dict[tuple[str, ...], dict[str, Any]] = {}
    for payload in payloads:
        units = battle.effect_units_at_cells(preview_positions(payload.get("cells")))
        if clones_and_summons_only:
            units = [unit for unit in units if unit.is_clone or unit.is_summon]
        signature = tuple(sorted(unit.unit_id for unit in units))
        unique.setdefault(signature, payload)
    return list(unique.values())


def dedupe_damage_area_payloads(battle: Battle, payloads: list[dict[str, Any]]) -> list[dict[str, Any]]:
    unique: dict[tuple[tuple[str, int], ...], dict[str, Any]] = {}
    for payload in payloads:
        cells = preview_positions(payload.get("cells"))
        signature = tuple(
            sorted(
                (unit.unit_id, battle.unit_hit_count_for_cells(unit, cells))
                for unit in battle.effect_units_at_cells(cells)
            )
        )
        unique.setdefault(signature, payload)
    return list(unique.values())


def should_throttle_unlimited_nonhostile_skill(battle: Battle, actor: Unit, action: dict[str, Any], payload: dict[str, Any]) -> bool:
    """Prevent AI from repeatedly spending a turn/mana on unlimited utility skills."""
    if str(action.get("timing") or "") == "passive":
        return False
    try:
        skill = skill_from_ai_action(actor, action, str(action.get("code") or payload.get("skill_code") or ""))
    except Exception:
        return False
    if skill.code in {"blood_art", "sacrifice_ritual", "paralysis_card", "poison_card", "drain_card", "judgment_stone", "mimic_skill", "fried_inspire", "eagle_eye"}:
        return False
    if getattr(skill, "max_uses_per_turn", None) is not None:
        return False
    if getattr(skill, "max_uses_per_battle", None) is not None:
        return False
    if getattr(skill, "cooldown_turns", 0):
        return False
    if float(getattr(skill, "uses_this_turn", 0) or 0) <= 0:
        return False
    if skill_payload_requires_enemy_impact(battle, actor, action, payload):
        return False
    return True


def trim_skill_payloads_for_ai(
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

    def sort_key(payload: dict[str, Any]) -> tuple[float, int]:
        cells = preview_positions(payload.get("cells"))
        if not cells:
            return (0.0, 0)
        enemies = 0
        allies = 0
        enemy_value = 0.0
        for unit in battle.effect_units_at_cells(cells):
            if unit.player_id == actor.player_id:
                allies += 1
            else:
                enemies += 1
                enemy_value += hostile_unit_value(unit)
        return (enemies * 1000.0 + enemy_value - allies * 250.0 - len(cells) * 0.01, -len(cells))

    return sorted(payloads, key=sort_key, reverse=True)[:limit]


@with_enemy_view("reactor")
def build_reaction_candidates(
    battle: Battle,
    reactor: Unit,
    queued_action: QueuedAction,
    option: dict[str, Any],
    profile: DifficultyProfile,
) -> list[AICandidate]:
    payloads = reaction_payloads_for_option(battle, reactor, queued_action, option)
    candidates: list[AICandidate] = []
    for payload in payloads:
        if not reaction_payload_is_legal(battle, reactor, queued_action, payload):
            continue
        score = score_reaction_payload(battle, reactor, queued_action, option, payload, profile)
        candidates.append(AICandidate(payload=payload, score=score, summary=f"react:{option.get('action_code')}"))
    return candidates


def attack_payloads_for_action(battle: Battle, actor: Unit, action: dict[str, Any]) -> list[dict[str, Any]]:
    preview = action.get("preview", {}) or {}
    base_payload = {"type": "attack", "unit_id": actor.unit_id}
    base_payload.update(dict(action.get("attack_payload") or {}))
    selection = dict(preview.get("selection") or {})
    mode = str(selection.get("mode") or "")
    payloads: list[dict[str, Any]] = []
    if mode == "choice_pattern":
        for choice in selection.get("choices", []):
            code = str(choice.get("code") or "")
            patterns = choice.get("patterns") or []
            for pattern in patterns:
                cells = preview_positions(pattern)
                payloads.extend(
                    attack_payloads_for_cells(
                        battle,
                        actor,
                        base_payload,
                        cells,
                        choice_code=code,
                    )
                )
    elif mode == "pattern_cells":
        for pattern in selection.get("patterns", []):
            cells = preview_positions(pattern)
            if not cells:
                continue
            if preview.get("requires_target") and not preview.get("target_unit_ids"):
                continue
            payload = dict(base_payload)
            payload["cells"] = positions_to_payload(cells)
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
        preview_cells = preview_positions(preview.get("cells"))
        for target_id in preview.get("target_unit_ids", []):
            target = battle.get_unit(str(target_id))
            if not target.alive or target.position is None or target.banished:
                continue
            declared = choose_declared_target_cell(battle, target, preview_cells)
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
    return dedupe_payloads(payloads)


def attack_payloads_for_cells(
    battle: Battle,
    actor: Unit,
    base_payload: dict[str, Any],
    cells: list[Position],
    *,
    choice_code: Optional[str] = None,
) -> list[dict[str, Any]]:
    payloads: list[dict[str, Any]] = []
    seen: set[str] = set()
    forced_target = required_attack_target(battle, actor)
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
        declared = choose_declared_target_cell(battle, unit, cells) or battle.declared_cell_for_target(actor, unit, payload)
        if declared is not None:
            payload["x"] = declared.x
            payload["y"] = declared.y
        payloads.append(payload)
    return payloads


def skill_payloads_for_action(battle: Battle, actor: Unit, action: dict[str, Any]) -> list[dict[str, Any]]:
    preview = action.get("preview", {}) or {}
    code = str(action.get("code") or "")
    target_mode = str(action.get("target_mode") or "none")
    if code in MOVE_SKILL_CODES and actor.cannot_move:
        return []
    if battle.mounted_unit_for(actor) is not None and code in MOVE_SKILL_CODES and code != "mounted_leap":
        return []
    base_payload = {"type": "skill", "unit_id": actor.unit_id, "skill_code": code}
    selection = dict(preview.get("selection") or {})
    mode = str(selection.get("mode") or "")
    if code == "mimic_skill":
        return mimic_skill_payloads(battle, actor, action)
    if code == "royal_soldier":
        return royal_soldier_payloads(battle, actor, action)
    if code == "agency_contract":
        return agency_contract_payloads(battle, actor, action)
    if code == "agency_borrowed_skill":
        return agency_borrowed_skill_payloads(battle, actor)
    if code == "heaven_punishment":
        return heaven_punishment_payloads(battle, actor, action)
    if code == "rock_absorb":
        return rock_absorb_payloads(battle, actor, action)
    if code == "rock_cannon":
        return rock_cannon_payloads(battle, actor)
    if code == "crazy_sand":
        return [{**base_payload, "cells": entry["pattern"], "x": entry["destination"]["x"], "y": entry["destination"]["y"]}
                for entry in preview.get("pattern_destinations", [])]
    if code in {"split", "earth_walker"}:
        return split_payloads(battle, actor, action)
    if code in {"descent_moment", "mana_pull"}:
        payloads: list[dict[str, Any]] = []
        destinations_by_target = preview.get("destinations_by_target", {}) or {}
        for target_id in preview.get("target_unit_ids", []):
            for cell in preview_positions(destinations_by_target.get(str(target_id))):
                payloads.append(
                    {
                        "type": "skill",
                        "unit_id": actor.unit_id,
                        "skill_code": code,
                        "target_unit_id": str(target_id),
                        "dest_x": cell.x,
                        "dest_y": cell.y,
                    }
                )
        return dedupe_payloads(payloads)
    if code in {"fantasy_move", "rainbow_mirror"}:
        payloads: list[dict[str, Any]] = []
        destinations_by_target = preview.get("destinations_by_target", {}) or {}
        for target_id in preview.get("target_unit_ids", []):
            for cell in preview_positions(destinations_by_target.get(str(target_id))):
                payloads.append(
                    {
                        "type": "skill",
                        "unit_id": actor.unit_id,
                        "skill_code": code,
                        "target_unit_id": str(target_id),
                        "x": cell.x,
                        "y": cell.y,
                    }
                )
        return dedupe_payloads(payloads)
    if code == "true_blade_air_slash":
        payloads: list[dict[str, Any]] = []
        destinations_by_target = preview.get("destinations_by_target", {}) or {}
        for target_id in preview.get("target_unit_ids", []):
            for cell in preview_positions(destinations_by_target.get(str(target_id))):
                payloads.append(
                    {
                        "type": "skill",
                        "unit_id": actor.unit_id,
                        "skill_code": code,
                        "target_unit_id": str(target_id),
                        "x": cell.x,
                        "y": cell.y,
                    }
                )
        return dedupe_payloads(payloads)
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
            return dedupe_payloads(payloads)
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
        return dedupe_payloads(payloads)
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
            return dedupe_payloads(payloads)
        if mode == "pattern_cells":
            payloads: list[dict[str, Any]] = []
            for pattern in selection.get("patterns", []):
                payload = {
                    "type": "skill",
                    "unit_id": actor.unit_id,
                    "skill_code": code,
                    "cells": positions_to_payload(preview_positions(pattern)),
                }
                payloads.append(payload)
                if code == "lao_wave_bullet":
                    free_payload = dict(payload)
                    free_payload["free_cast"] = True
                    payloads.append(free_payload)
            return dedupe_payloads(payloads)
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
                            "cells": positions_to_payload(preview_positions(pattern)),
                        }
                    )
            return dedupe_payloads(payloads)
        if mode == "body_direction":
            return rock_cannon_payloads(battle, actor)
        if mode == "revive_unit_cell":
            payloads: list[dict[str, Any]] = []
            for candidate in selection.get("candidates", []):
                revive_unit_id = str(candidate.get("id") or "")
                for cell in preview_positions(candidate.get("cells")):
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
            return dedupe_payloads(payloads)
        payloads = []
        for cell in preview_positions(preview.get("cells")):
            payload = {"type": "skill", "unit_id": actor.unit_id, "skill_code": code, "x": cell.x, "y": cell.y}
            payload.update(direction_payload_for_cell_skill(actor, action, cell))
            payloads.append(payload)
        return dedupe_payloads(payloads)
    return []


def heaven_punishment_payloads(battle: Battle, actor: Unit, action: dict[str, Any]) -> list[dict[str, Any]]:
    preview = action.get("preview", {}) or {}
    selection = dict(preview.get("selection") or {})
    allowed_ids = set(preview.get("target_unit_ids") or [])
    payloads: list[dict[str, Any]] = []
    for pattern in selection.get("patterns", []):
        cells = preview_positions(pattern)
        cell_keys = {(cell.x, cell.y) for cell in cells}
        for target in battle.enemy_units(actor.player_id):
            if target.unit_id not in allowed_ids:
                continue
            if not any((cell.x, cell.y) in cell_keys for cell in battle.unit_cells(target)):
                continue
            for target_skill in skill_from_ai_action(actor, action, "heaven_punishment").public_active_skills(battle, target):
                if getattr(target_skill, "timing", None) != "active":
                    continue
                if target_skill.max_uses_per_battle is not None and target_skill.uses_this_battle >= target_skill.max_uses_per_battle:
                    continue
                if any(getattr(status, "skill_code", None) == target_skill.code for status in target.statuses):
                    continue
                payloads.append(
                    {
                        "type": "skill",
                        "unit_id": actor.unit_id,
                        "skill_code": "heaven_punishment",
                        "cells": positions_to_payload(cells),
                        "target_unit_id": target.unit_id,
                        "disabled_skill_code": target_skill.code,
                    }
                )
    return dedupe_payloads(payloads)


def skill_from_ai_action(actor: Unit, action: dict[str, Any], code: str) -> Any:
    skill = action.get("_skill_object")
    if skill is not None:
        return skill
    return actor.get_skill(code)


def copied_skill_action(battle: Battle, actor: Unit, copied: Any) -> dict[str, Any]:
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


def mimic_skill_payloads(battle: Battle, actor: Unit, action: dict[str, Any]) -> list[dict[str, Any]]:
    mimic = actor.get_skill("mimic_skill")
    preview = action.get("preview", {}) or mimic.preview(battle, actor)
    payloads, seen = [], set()
    for entry in (preview.get("selection") or {}).get("targets", []):
        source = battle.get_unit(entry["unit_id"])
        for definition in entry.get("skills", []):
            if not definition.get("action", {}).get("available"):
                continue
            copied = mimic.borrowed_skill(actor, source, definition["code"])
            copied_action = {**definition["action"], "_skill_object": copied}
            with mimic.copying(actor, copied, reserve_point=True):
                inner_payloads = skill_payloads_for_action(battle, actor, copied_action)
            for inner in inner_payloads:
                signature = repr((copied.code, sorted(inner.items())))
                if signature in seen:
                    continue
                payload = {"type": "skill", "unit_id": actor.unit_id, "skill_code": "mimic_skill",
                           "target_unit_id": source.unit_id, "mimic_skill_code": copied.code, "copied_payload": inner}
                if not payload_is_legal(battle, payload):
                    continue
                seen.add(signature)
                payloads.append(payload)
    return payloads


def mimic_payload_context(battle: Battle, actor: Unit, payload: dict[str, Any]) -> Optional[tuple[Unit, Any, dict[str, Any], dict[str, Any]]]:
    try:
        mimic = actor.get_skill("mimic_skill")
        source, copied, inner = mimic._target_skill(battle, actor, payload)
        with mimic.copying(actor, copied, reserve_point=True):
            if not copied.can_use(battle, actor, inner)[0]:
                return None
            action = copied_skill_action(battle, actor, copied)
        return source, copied, inner, action
    except (ActionError, AttributeError, KeyError, TypeError, ValueError):
        return None


def royal_soldier_payloads(battle: Battle, actor: Unit, action: dict[str, Any]) -> list[dict[str, Any]]:
    selection = (action.get("preview") or {}).get("selection") or {}
    return [{"type": "skill", "unit_id": actor.unit_id, "skill_code": "royal_soldier",
             "choice_code": choice["code"], "cells": pattern}
            for choice in selection.get("choices", []) for pattern in choice.get("patterns", [])]


def agency_contract_payloads(battle: Battle, actor: Unit, action: dict[str, Any]) -> list[dict[str, Any]]:
    preview = action.get("preview", {}) or {}
    selection = dict(preview.get("selection") or {})
    if selection.get("attached"):
        return [{"type": "skill", "unit_id": actor.unit_id, "skill_code": "agency_contract"}]
    target_entries = {
        str(entry.get("unit_id")): entry
        for entry in selection.get("targets", [])
        if isinstance(entry, dict) and entry.get("unit_id")
    }
    stats = [str(stat) for stat in selection.get("stats", [])] or ["attack", "defense", "speed", "attack_range", "mana"]
    payloads: list[dict[str, Any]] = []
    for target_id in preview.get("target_unit_ids", []):
        try:
            target = battle.get_unit(str(target_id))
        except Exception:
            continue
        entry = target_entries.get(target.unit_id) or {}
        skill_codes = [str(skill.get("code")) for skill in entry.get("skills", []) if isinstance(skill, dict) and skill.get("code")]
        if not skill_codes:
            skill_codes = [skill.code for skill in target.skills if getattr(skill, "timing", None) in {"active", "instant"}]
        for skill_code in skill_codes:
            for stat_name in stats:
                payloads.append(
                    {
                        "type": "skill",
                        "unit_id": actor.unit_id,
                        "skill_code": "agency_contract",
                        "target_unit_id": target.unit_id,
                        "stat_name": stat_name,
                        "copied_skill_code": skill_code,
                    }
                )
    return dedupe_payloads(payloads)


def agency_borrowed_skill_payloads(battle: Battle, actor: Unit) -> list[dict[str, Any]]:
    try:
        wrapper = actor.get_skill("agency_borrowed_skill")
        _, copied, _ = wrapper.target_skill(battle, actor, {})
        with wrapper.copying(actor, copied):
            copied_action = copied_skill_action(battle, actor, copied)
            payloads = skill_payloads_for_action(battle, actor, copied_action)
        return dedupe_payloads([{"type": "skill", "unit_id": actor.unit_id, "skill_code": wrapper.code,
                                 "contract_payload": dict(payload)} for payload in payloads])
    except (ActionError, KeyError, TypeError, ValueError):
        return []


def agency_borrowed_payload_context(battle: Battle, actor: Unit, payload: dict[str, Any]) -> Optional[tuple[Unit, Any, dict[str, Any], dict[str, Any]]]:
    try:
        wrapper = actor.get_skill("agency_borrowed_skill")
        carrier, copied, inner = wrapper.target_skill(battle, actor, payload)
        with wrapper.copying(actor, copied):
            return carrier, copied, inner, copied_skill_action(battle, actor, copied)
    except (ActionError, KeyError, TypeError, ValueError):
        return None


def direction_payload_for_cell_skill(actor: Unit, action: dict[str, Any], cell: Position) -> dict[str, Any]:
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


def split_payloads(battle: Battle, actor: Unit, action: dict[str, Any]) -> list[dict[str, Any]]:
    code = str(action.get("code") or "split")
    skill = actor.get_skill(code)
    preview = action.get("preview", {}) or {}
    candidate_cells = preview_positions(preview.get("cells"))
    required = int((preview.get("selection") or {}).get("required_cells") or getattr(skill, "clone_count", 3))
    probe = skill._clone_probe(actor)  # type: ignore[attr-defined]
    selected: list[Position] = []
    occupied: set[tuple[int, int]] = set()
    enemies = living_hostile_combatants(battle, actor.player_id) if code == "earth_walker" else []
    def placement_key(cell: Position) -> float:
        return -min(distance_to_position(battle, enemy, cell) for enemy in enemies) if enemies else distance_to_position(battle, actor, cell)
    for cell in sorted(candidate_cells, key=placement_key):
        footprint_keys = {(footprint.x, footprint.y) for footprint in battle.unit_cells_at(probe, cell)}
        if occupied & footprint_keys:
            continue
        occupied.update(footprint_keys)
        selected.append(cell)
        if len(selected) >= required:
            break
    if len(selected) != required:
        return []
    payload = {
        "type": "skill",
        "unit_id": actor.unit_id,
        "skill_code": code,
        "cells": positions_to_payload(selected),
    }
    try:
        skill.selected_destinations(battle, actor, payload)
    except Exception:
        return []
    return [payload]


def rock_absorb_payloads(battle: Battle, actor: Unit, action: dict[str, Any]) -> list[dict[str, Any]]:
    skill = actor.get_skill("rock_absorb")
    preview = action.get("preview", {}) or {}
    selection = dict(preview.get("selection") or {})
    required = int(selection.get("required_cells") or 0)
    candidate_cells = preview_positions(preview.get("cells"))
    selected_cells: list[Position] = []
    if required:
        connected = {(cell.x, cell.y) for cell in battle.unit_cells(actor)}
        enemies = living_hostile_combatants(battle, actor.player_id)
        while len(selected_cells) < required:
            frontier = [cell for cell in candidate_cells if (cell.x, cell.y) not in connected
                        and any((cell.x + dx, cell.y + dy) in connected for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)))]
            if not frontier:
                break
            cell = min(frontier, key=lambda cell: min((battle.unit_distance_to_cell(enemy, cell) for enemy in enemies), default=0))
            selected_cells.append(cell)
            connected.add((cell.x, cell.y))
        if len(selected_cells) != required:
            return []
    payloads: list[dict[str, Any]] = []
    for stat_entry in selection.get("stats", []):
        stat_name = str(stat_entry.get("code") or "")
        payload = {
            "type": "skill",
            "unit_id": actor.unit_id,
            "skill_code": "rock_absorb",
            "stat_name": stat_name,
            "cells": positions_to_payload(selected_cells),
        }
        try:
            skill.selected_stat(payload)
            skill.selected_growth_cells(battle, actor, payload, required)
        except Exception:
            continue
        payloads.append(payload)
    return payloads


def rock_cannon_payloads(battle: Battle, actor: Unit) -> list[dict[str, Any]]:
    skill = actor.get_skill("rock_cannon")
    body = battle.unit_cells(actor)
    if len(body) <= 1:
        return []
    payloads: list[dict[str, Any]] = []
    candidate_groups: list[list[Position]] = [[cell] for cell in body]
    candidate_groups.extend([list(group) for group in combinations(body[:12], 2)
                             if len(body) > 2 and abs(group[0].x - group[1].x) + abs(group[0].y - group[1].y) == 1])
    if len(body) > 2:
        for keep_cell in body:
            group = [cell for cell in body if cell != keep_cell]
            if group:
                candidate_groups.append(group)
    seen_groups: set[tuple[tuple[int, int], ...]] = set()
    for group in candidate_groups:
        key = tuple(sorted((cell.x, cell.y) for cell in group))
        if key in seen_groups:
            continue
        seen_groups.add(key)
        for dx, dy in (
            (0, -1),
            (1, -1),
            (1, 0),
            (1, 1),
            (0, 1),
            (-1, 1),
            (-1, 0),
            (-1, -1),
        ):
            payload = {
                "type": "skill",
                "unit_id": actor.unit_id,
                "skill_code": "rock_cannon",
                "cells": positions_to_payload(group),
                "direction": {"dx": dx, "dy": dy},
            }
            try:
                skill.validate_selection(battle, actor, payload)
            except Exception:
                continue
            payloads.append(payload)
    return payloads


def reaction_payloads_for_option(
    battle: Battle,
    reactor: Unit,
    queued_action: QueuedAction,
    option: dict[str, Any],
) -> list[dict[str, Any]]:
    action_code = str(option.get("action_code") or "")
    base_payload = {"type": "chain_react", "unit_id": reactor.unit_id, "action_code": action_code}
    if action_code in {"block", "counter", "knockback"}:
        return [base_payload]
    if action_code == "agency_borrowed_skill":
        return [{**base_payload, "contract_payload": payload["contract_payload"]}
                for payload in agency_borrowed_skill_payloads(battle, reactor)]
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
    if action_code in REACTION_SHIELD_CODES:
        target_ids = shield_targets_for_reaction(battle, reactor, queued_action, preview, action_code=action_code)
        if target_ids:
            return [
                {**base_payload, "target_unit_ids": target_ids[:count]}
                for count in range(1, len(target_ids) + 1)
            ]
        return []
    if action_code == "backstep_shot":
        return backstep_payloads(base_payload, preview)
    if mode == "multi_unit":
        target_ids = [str(unit_id) for unit_id in preview.get("target_unit_ids", [])]
        return [{**base_payload, "target_unit_ids": target_ids}] if target_ids else []
    cell_payloads = []
    for cell in preview_positions(preview.get("cells")):
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


def backstep_payloads(base_payload: dict[str, Any], preview: dict[str, Any]) -> list[dict[str, Any]]:
    payloads: list[dict[str, Any]] = []
    follow_up_map = dict(preview.get("follow_up_target_ids_by_cell") or {})
    for cell in preview_positions(preview.get("cells")):
        key = f"{cell.x},{cell.y}"
        target_ids = [str(unit_id) for unit_id in follow_up_map.get(key, [])]
        payloads.append({**base_payload, "x": cell.x, "y": cell.y})
        if target_ids:
            payloads.append({**base_payload, "x": cell.x, "y": cell.y, "target_unit_id": target_ids[0]})
    return payloads


def shield_targets_for_reaction(
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
    already_protected = queued_shield_protected_unit_ids(battle)
    if not threatened:
        return []
    threatened.sort(
        key=lambda unit: (
            unit.unit_id not in already_protected,
            incoming_threat_score(battle, unit, queued_action) >= unit.current_hp * 100.0 - 1e-9,
            unit.unit_id == reactor.unit_id,
            incoming_threat_score(battle, unit, queued_action),
            hostile_unit_value(unit),
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


def queued_shield_protected_unit_ids(battle: Battle) -> set[str]:
    window = battle.pending_chain
    if window is None:
        return set()
    protected: set[str] = set()
    for reaction in window.chosen_reactions:
        payload = reaction.payload if isinstance(reaction.payload, dict) else {}
        if str(payload.get("action_code") or "") not in REACTION_SHIELD_CODES:
            continue
        protected.update(str(unit_id) for unit_id in payload.get("target_unit_ids", []) if unit_id)
        if payload.get("target_unit_id"):
            protected.add(str(payload["target_unit_id"]))
    return protected


def active_damage_mana_reserve(actor: Unit) -> float:
    costs = [
        float(getattr(skill, "mana_cost", 0.0) or 0.0)
        for skill in actor.skills
        if str(getattr(skill, "timing", "active")) in {"active", "instant"}
        and str(getattr(skill, "code", "")) in DAMAGING_SKILL_CODES
        and float(getattr(skill, "mana_cost", 0.0) or 0.0) > 0
        and int(getattr(skill, "cooldown_remaining", 0) or 0) <= 0
        and (
            getattr(skill, "max_uses_per_battle", None) is None
            or int(getattr(skill, "uses_this_battle", 0) or 0) < int(skill.max_uses_per_battle)
        )
    ]
    return min(costs) if costs else 0.0


def living_hostile_combatants(battle: Battle, player_id: int) -> list[Unit]:
    return [
        unit
        for unit in battle.enemy_units(player_id)
        if unit.alive
        and unit.position is not None
        and not unit.banished
        and not bool(getattr(unit, "is_siege_structure", False))
    ]


def hero_ai_style_for(battle: Battle, actor: Unit) -> str:
    styles = getattr(battle, "hero_ai_styles", None) or {}
    raw = styles.get(int(actor.player_id)) or styles.get(str(actor.player_id)) or "rush"
    return "follow" if str(raw) == "follow" else "rush"


FOLLOW_COMFORT_RADIUS = 5
FOLLOW_FRONT_PACK_SLACK = 4
FOLLOW_LEAD_BY_ORDER = {
    "hold": 1,
    "advance": 3,
    "seek": 3,
    "retreat": 2,
}
FOLLOW_ANCHOR_EXCLUDED_KINDS = frozenset({"cannon", "arrow_tower"})


def _chebyshev_cells(origin: Position, destination: Position) -> int:
    return max(abs(int(destination.x) - int(origin.x)), abs(int(destination.y) - int(origin.y)))


def _axis_heading(origin: Position, target: Position) -> tuple[int, int]:
    dx = int(target.x) - int(origin.x)
    dy = int(target.y) - int(origin.y)
    sx = 0 if dx == 0 else (1 if dx > 0 else -1)
    sy = 0 if dy == 0 else (1 if dy > 0 else -1)
    return (sx, sy)


def _clamp_follow_cell(battle: Battle, x: int, y: int) -> Position:
    max_x = max(0, int(getattr(battle, "width", 1)) - 1)
    max_y = max(0, int(getattr(battle, "height", 1)) - 1)
    return Position(min(max(0, x), max_x), min(max(0, y), max_y))


def _follow_heading_for_army(
    battle: Battle,
    player_id: int,
    centroid: Position,
    command: dict[str, str],
) -> tuple[int, int]:
    from wujiang.tactical.engine.army import march_direction

    if str(command.get("order") or "") == "seek":
        enemies = living_hostile_combatants(battle, player_id)
        if enemies:
            nearest = min(enemies, key=lambda enemy: _chebyshev_cells(centroid, enemy.position))
            return _axis_heading(centroid, nearest.position)
    return march_direction(command)


def _is_follow_anchor_unit(unit: Unit) -> bool:
    if not unit.alive or unit.banished or unit.position is None:
        return False
    if bool(getattr(unit, "is_summon", False) or getattr(unit, "is_clone", False)):
        return False
    from wujiang.tactical.engine.army import army_kind_for_unit, is_army_soldier

    if not is_army_soldier(unit):
        return False
    if bool(getattr(unit, "is_siege_structure", False)):
        return False
    kind = str(army_kind_for_unit(unit) or "")
    return kind not in FOLLOW_ANCHOR_EXCLUDED_KINDS


def _follow_front_pack(units: list[Unit], heading: tuple[int, int]) -> list[Unit]:
    if not units:
        return []
    if heading == (0, 0):
        return units
    alongs = [
        int(unit.position.x) * heading[0] + int(unit.position.y) * heading[1]
        for unit in units
    ]
    front_along = max(alongs)
    pack = [
        unit
        for unit, along in zip(units, alongs)
        if along >= front_along - FOLLOW_FRONT_PACK_SLACK
    ]
    return pack or units


def clear_follow_anchor_cache(battle: Battle) -> None:
    battle._army_anchor_cache = {}


def follow_anchor_state(battle: Battle, player_id: int) -> tuple[Optional[Position], tuple[int, int]]:
    cache = getattr(battle, "_army_anchor_cache", None)
    if not isinstance(cache, dict):
        cache = {}
        battle._army_anchor_cache = cache
    key = int(player_id)
    if key in cache:
        return cache[key]
    from wujiang.tactical.engine.army import command_for_kind, default_army_orders

    soldiers = [unit for unit in battle.player_units(player_id) if _is_follow_anchor_unit(unit)]
    if not soldiers:
        cache[key] = (None, (0, 0))
        return cache[key]
    probe = Position(
        int(round(sum(unit.position.x for unit in soldiers) / len(soldiers))),
        int(round(sum(unit.position.y for unit in soldiers) / len(soldiers))),
    )
    orders = getattr(battle, "army_orders", None) or default_army_orders()
    command = command_for_kind(orders, player_id, "infantry")
    heading = _follow_heading_for_army(battle, player_id, probe, command)
    pack = _follow_front_pack(soldiers, heading)
    centroid = Position(
        int(round(sum(unit.position.x for unit in pack) / len(pack))),
        int(round(sum(unit.position.y for unit in pack) / len(pack))),
    )
    lead = FOLLOW_LEAD_BY_ORDER.get(str(command.get("order") or ""), 3)
    if heading == (0, 0):
        lead = 0
    anchor = _clamp_follow_cell(battle, centroid.x + heading[0] * lead, centroid.y + heading[1] * lead)
    cache[key] = (anchor, heading)
    return cache[key]


def army_anchor_cell(battle: Battle, player_id: int) -> Optional[Position]:
    return follow_anchor_state(battle, player_id)[0]


def follow_catch_up_skill_penalty(battle: Battle, actor: Unit) -> float:
    if hero_ai_style_for(battle, actor) != "follow" or actor.position is None:
        return 0.0
    if bool(getattr(actor, "is_summon", False) or getattr(actor, "is_clone", False)):
        return 0.0
    anchor, heading = follow_anchor_state(battle, actor.player_id)
    if anchor is None or heading == (0, 0):
        return 0.0
    current_along = int(actor.position.x) * heading[0] + int(actor.position.y) * heading[1]
    front_along = int(anchor.x) * heading[0] + int(anchor.y) * heading[1]
    behind = front_along - current_along
    if behind < 3:
        return 0.0
    return -30.0


def follow_leash_adjustment(army_dist: int, nearest_enemy: float, offensive_gain: float) -> float:
    over = army_dist - FOLLOW_COMFORT_RADIUS
    if over <= 0:
        return (FOLLOW_COMFORT_RADIUS - army_dist) * 1.5
    penalty = over * 10.0 + over * over * 3.0
    if nearest_enemy <= 2 and army_dist <= FOLLOW_COMFORT_RADIUS + 2:
        penalty *= 0.2
    elif offensive_gain > 0:
        penalty *= 0.35
    return -penalty


def follow_formation_adjustment(
    destination: Position,
    current: Optional[Position],
    anchor: Position,
    heading: tuple[int, int],
    nearest_enemy: float,
    offensive_gain: float,
    *,
    is_summon: bool = False,
) -> float:
    dest_dist = _chebyshev_cells(anchor, destination)
    current_dist = _chebyshev_cells(anchor, current) if current is not None else dest_dist
    if heading == (0, 0):
        score = follow_leash_adjustment(dest_dist, nearest_enemy, offensive_gain)
        if dest_dist < current_dist:
            score += float(current_dist - dest_dist) * 3.0
        return score
    dest_along = int(destination.x) * heading[0] + int(destination.y) * heading[1]
    front_along = int(anchor.x) * heading[0] + int(anchor.y) * heading[1]
    ahead = dest_along - front_along
    score = 0.0
    if current is not None:
        current_along = int(current.x) * heading[0] + int(current.y) * heading[1]
        if current_along < front_along and dest_along > current_along and dest_along <= front_along + 1:
            score += float(min(dest_along, front_along) - current_along) * 8.0
    slack = 2 if is_summon else 1
    if ahead > slack:
        over = ahead - slack
        penalty = over * 10.0 + over * over * 3.0
        if nearest_enemy <= 2 and dest_dist <= FOLLOW_COMFORT_RADIUS + 2:
            penalty *= 0.2
        elif offensive_gain > 0:
            penalty *= 0.35
        score -= penalty
    elif dest_dist <= FOLLOW_COMFORT_RADIUS:
        score += float(FOLLOW_COMFORT_RADIUS - dest_dist) * 1.5
    return score


def score_move_destination(
    battle: Battle,
    actor: Unit,
    destination: Position,
    role: str,
    profile: DifficultyProfile,
) -> float:
    enemies = living_hostile_combatants(battle, actor.player_id)
    allies = [unit for unit in battle.player_units(actor.player_id) if unit.unit_id != actor.unit_id and unit.alive and unit.position is not None and not unit.banished]
    if not enemies:
        return -10.0
    forced_target = required_attack_target(battle, actor)
    movement_targets = [forced_target] if forced_target is not None else enemies
    nearest_enemy = min(distance_to_position(battle, enemy, destination) for enemy in movement_targets)
    current_distance = min(distance_between_units(battle, actor, enemy) for enemy in movement_targets)
    score = float(current_distance - nearest_enemy) * 6.0
    if forced_target is not None:
        score += float(current_distance - nearest_enemy) * 30.0
        if nearest_enemy <= actor.targeting_range():
            score += 140.0
    offensive_gain = offensive_reach_score_at(battle, actor, destination)
    score += offensive_gain * 18.0
    score += great_fire_funeral_alignment_score_at(battle, actor, destination)
    follow_behind = False
    if hero_ai_style_for(battle, actor) == "follow":
        anchor, heading = follow_anchor_state(battle, actor.player_id)
        if anchor is not None and heading != (0, 0) and actor.position is not None:
            current_along = int(actor.position.x) * heading[0] + int(actor.position.y) * heading[1]
            front_along = int(anchor.x) * heading[0] + int(anchor.y) * heading[1]
            follow_behind = front_along - current_along >= 3
        if anchor is not None:
            score += follow_formation_adjustment(
                destination,
                actor.position,
                anchor,
                heading,
                nearest_enemy,
                offensive_gain,
                is_summon=bool(getattr(actor, "is_summon", False) or getattr(actor, "is_clone", False)),
            )
    if role == "support" and not follow_behind:
        nearest_ally = min((distance_to_position(battle, ally, destination) for ally in allies), default=2)
        score += max(0.0, 3.0 - nearest_ally) * 6.0
        score += max(0.0, nearest_enemy - 1.0) * profile.support_bonus
    elif role != "support":
        score += max(0.0, 4.0 - nearest_enemy) * (4.0 + profile.aggressive_bonus / 6.0)
    if actor.hero_code == "soul_wraith":
        with ai_probe_rollback(battle):
            actor.position = destination
            immunity = next(trait for trait in actor.traits if trait.name == "孤魂魔免")
            if immunity.active(battle):
                score += 28.0
            else:
                # Skill pressure is dangerous with the explicit 0.5 defense.
                for enemy in enemies:
                    if any(skill.timing in {"active", "instant"} and skill.code in DAMAGING_SKILL_CODES and enemy.current_mana >= skill.mana_cost for skill in enemy.skills):
                        if distance_between_units(battle, actor, enemy) <= enemy.normal_move_distance() + max(2, enemy.targeting_range()):
                            score -= 48.0
    if destination != actor.position and not actor.moved_this_turn and any(trait.name == "原地回复" for trait in actor.traits):
        score -= min(1.0, max(0.0, actor.max_mana() - actor.current_mana)) * 18.0
        if not actor.cannot_heal:
            score -= min(0.25, max(0.0, actor.max_health - actor.current_hp)) * 120.0
    score += fried_aura_position_value(battle, actor, destination)
    if actor.hero_code == "excel_r139":
        score += sola_harvest_position_value(battle, actor, destination) - sola_harvest_position_value(battle, actor, actor.position)
    if actor.hero_code == "excel_r166":
        score += electric_auto_position_value(battle, actor, destination) - electric_auto_position_value(battle, actor, actor.position)
    if actor.hero_code in {"excel_r188", "excel_r224"}:
        before = r20_line_window_value(battle, actor, profile)
        with ai_probe_rollback(battle):
            actor.position = destination
            score += 0.75 * (r20_line_window_value(battle, actor, profile) - before)
    if actor.hero_code == "excel_r225":
        with ai_probe_rollback(battle):
            actor.position = destination
            score += r21_barrier_position_value(battle, actor)
    if actor.hero_code == "excel_r264":
        immediate = r21_winged_contact_value(battle, actor, allow_leap=False, allow_move=False)
        with ai_probe_rollback(battle):
            moved = destination != actor.position
            actor.position = destination
            if moved:
                actor.normal_move_actions_used = actor.normal_move_actions_per_turn()
                actor.normal_move_steps_used = actor.normal_move_distance()
            score += 0.7 * max(0.0, r21_winged_contact_value(battle, actor) - immediate)
    if actor.hero_code in {"excel_r327", "excel_r352"}:
        with ai_probe_rollback(battle):
            actor.position = destination
            score += 0.8 * r22_contact_value(battle, actor, allow_move=False)
            if actor.hero_code == "excel_r327":
                score -= r22_gazer_window_risk(battle, actor)
    if actor.hero_code == "excel_r379" or actor.has_status("菊之遗击"):
        with ai_probe_rollback(battle):
            actor.position = destination
            score += 0.7 * r23_attack_plan_value(battle, actor) - 2.0 * natsume_unit_danger(battle, actor)
    if actor.hero_code == "excel_r031":
        with ai_probe_rollback(battle):
            actor.position = destination
            score += natsume_position_plan_value(battle, actor) - natsume_unit_danger(battle, actor)
    if actor.hero_code == "excel_r030":
        with ai_probe_rollback(battle):
            actor.position = destination
            score += fusion_attack_plan_value(battle, actor, allow_move=False) - fusion_position_risk(battle, actor)
    return score


def required_attack_target(battle: Battle, actor: Unit) -> Optional[Unit]:
    """Expose reusable taunt-style target contracts to movement and attack scoring."""
    for component in actor.iter_components():
        target_id = str(getattr(component, "required_attack_target_id", "") or "")
        if not target_id:
            continue
        forces = getattr(component, "forces_attack_target", None)
        if callable(forces) and not bool(forces(battle)):
            continue
        target = battle.units.get(target_id)
        if target is not None and target.alive and target.position is not None and not target.banished:
            return target
    return None


def attack_effect_units(battle: Battle, actor: Unit, payload: dict[str, Any]) -> list[Unit]:
    resolved_payload = battle.resolved_basic_attack_payload(actor, payload)
    if "attack_cells" in resolved_payload:
        return [unit for unit in battle.effect_units_at_cells(battle.payload_positions(resolved_payload, "attack_cells"))
                if unit.player_id != actor.player_id or resolved_payload.get("friendly_fire")]
    target_id = str(payload.get("target_unit_id") or "")
    if target_id:
        target = battle.units.get(target_id)
        return [target] if target is not None else []
    resolved_payload = battle.resolved_basic_attack_payload(actor, payload)
    cells = battle.payload_positions(resolved_payload, "attack_cells")
    if not cells:
        cells = preview_positions(payload.get("cells"))
    return battle.effect_units_at_cells(cells)


def has_cat_retaliation(unit: Unit) -> bool:
    return any(
        component.name == "猫叔反制" and component.__class__.__name__ == "CatRetaliationTrait"
        for component in unit.iter_components()
    )


def cat_retaliation_action_penalty(battle: Battle, actor: Unit, targets: Iterable[Unit]) -> float:
    """Price the mana loss, forced pull, and post-action lock caused by targeting Cat Uncle."""
    cats = {target.unit_id: target for target in targets if target.unit_id != actor.unit_id and has_cat_retaliation(target)}
    if not cats:
        return 0.0
    remaining_active_options = sum(
        1
        for skill in actor.skills
        if str(getattr(skill, "timing", "active")) in {"active", "instant"}
        and int(getattr(skill, "cooldown_remaining", 0) or 0) <= 0
        and (
            getattr(skill, "max_uses_per_turn", None) is None
            or int(getattr(skill, "uses_this_turn", 0) or 0) < int(skill.max_uses_per_turn)
        )
    )
    can_still_move = actor.remaining_normal_move_distance(battle) > 0
    lock_cost = (28.0 if can_still_move else 8.0) + min(3, remaining_active_options) * 14.0
    mana_cost = min(1.0, actor.current_mana) * 30.0
    penalty = 0.0
    for cat in cats.values():
        hostile_pull_cost = 0.0
        if cat.player_id != actor.player_id:
            hostile_pull_cost = 26.0 + cat.stat("attack") * cat.attack_actions_per_turn() * 6.0
        penalty += mana_cost + lock_cost + hostile_pull_cost
    return penalty


def score_attack_payload(
    battle: Battle,
    actor: Unit,
    payload: dict[str, Any],
    profile: DifficultyProfile,
) -> float:
    if actor.hero_code == "excel_r056":
        return r13_attack_value(battle, actor, payload)
    if actor.hero_code == "excel_r379" or payload.get("attack_variant") == "kiku_legacy":
        return r23_attack_value(battle, actor, payload)
    if payload.get("target_unit_id"):
        target = battle.units.get(str(payload["target_unit_id"]))
        if target is not None and (actor.hero_code in {"excel_r327", "excel_r337", "excel_r352"}
                                  or battle.effect_recipient(target).hero_code == "excel_r327"):
            return r22_attack_value(battle, actor, target, payload)
    if actor.hero_code in {"excel_r225", "excel_r264", "excel_r326"} and payload.get("target_unit_id"):
        return r21_attack_value(battle, actor, battle.get_unit(str(payload["target_unit_id"])), payload)
    target = battle.units.get(str(payload.get("target_unit_id") or ""))
    if actor.hero_code in {"excel_r187", "excel_r188", "excel_r224"} and target is not None and target.player_id != actor.player_id:
        power = battle.basic_attack_preview_power(actor, payload)
        impact = probe_attack_damage_impact(battle, actor, target, payload, attack_power=power)
        value = min(target.current_hp, impact.damage) * 100.0
        if impact.damage >= target.current_hp - 1e-9:
            value += 90.0
        elif impact.changed_target and impact.damage <= 0:
            value += 20.0
        from wujiang.tactical.heroes.excel_roster import weather_race_eligible
        if value > 0 and not actor.direct_effects_blocked() and weather_race_eligible(actor, "恶魔") and battle.unit_in_weather("万魔殿", actor):
            value += 0.5 * min(max(0.0, target.current_hp - impact.damage), impact.damage) * 100.0
        return value - 4.0
    if target is not None and target.player_id == actor.player_id and any(component.name == "风壁赠予" for component in actor.iter_components()):
        return natsume_support_attack_score(battle, actor, payload)
    if actor.hero_code == "excel_r030":
        return fusion_attack_value(battle, actor, payload)
    if actor.hero_code == "excel_r029" or actor.has_status("无限") or actor.has_status("武装复制"):
        return red_attack_value(battle, actor, payload)
    if not payload.get("target_unit_id"):
        resolved_payload = battle.resolved_basic_attack_payload(actor, payload)
        cells = battle.payload_positions(resolved_payload, "attack_cells")
        if not cells:
            cells = preview_positions(payload.get("cells"))
        attack_power = battle.basic_attack_preview_power(actor, payload)
        score = 0.0
        forced_target = required_attack_target(battle, actor)
        drain_room = max(0.0, actor.max_mana() - actor.current_mana)
        for target in battle.effect_units_at_cells(cells):
            if target.player_id == actor.player_id:
                if resolved_payload.get("friendly_fire") or any(component.basic_attack_area_affects_allies(battle, actor, resolved_payload) for component in actor.iter_components()):
                    score -= friendly_fire_penalty(target)
                if forced_target is not None and target.unit_id == forced_target.unit_id:
                    score += 650.0
                continue
            hit_count = max(1, battle.unit_hit_count_for_cells(target, cells) if cells else 1)
            expected_damage = estimate_attack_damage(
                battle,
                actor,
                target,
                resolved_payload,
                attack_power=attack_power,
                area_cell_hits=hit_count,
            )
            score += min(target.current_hp, expected_damage) * 100.0
            if actor.hero_code == "soul_wraith" and expected_damage > 0 and not target.is_clone:
                from wujiang.tactical.heroes.common import is_mana_drain_immune
                drain = 0.0 if is_mana_drain_immune(target) else min(1.0, target.current_mana)
                gain = min(drain, drain_room)
                drain_room -= gain
                score += drain * 14.0 + gain * 26.0
            if actor.hero_code == "dragon_mount":
                with ai_probe_rollback(battle):
                    defense, speed = target.stat("defense"), target.stat("speed")
                    battle.resolve_attack_damage(actor, target, action_name="龙爪", payload=resolved_payload, tags={"area_attack"})
                    score += max(0.0, defense - target.stat("defense")) * 26.0 + max(0.0, speed - target.stat("speed")) * 14.0
            if expected_damage >= target.current_hp - 1e-9:
                score += 95.0
            score += hostile_unit_value(target) * 0.65
            forced_target = required_attack_target(battle, actor)
            if forced_target is not None and target.unit_id == forced_target.unit_id:
                score += 500.0
        if hero_style(actor) != "support":
            score += profile.aggressive_bonus
        return score
    target = battle.get_unit(str(payload["target_unit_id"]))
    if actor.has_status("里次元大剑") or getattr(actor, "hero_code", "") == "excel_r028":
        if target.player_id != actor.player_id:
            return feiwang_attack_value(battle, actor, target, payload)
    if target.player_id == actor.player_id and battle.allied_basic_heal_amount(actor, target) > 0:
        return allied_attack_heal_score(battle, actor, payload)
    attack_power = battle.basic_attack_preview_power(actor, payload)
    cells = battle.payload_positions(battle.resolved_basic_attack_payload(actor, payload), "attack_cells")
    hit_count = max(1, battle.unit_hit_count_for_cells(target, cells) if cells else 1)
    expected_damage = estimate_attack_damage(
        battle,
        actor,
        target,
        payload,
        attack_power=attack_power,
        area_cell_hits=hit_count,
    )
    score = min(expected_damage, target.current_hp) * 100.0
    if expected_damage >= target.current_hp - 1e-9:
        score += 95.0
    score += hostile_unit_value(target) * 0.8
    if hero_style(actor) != "support":
        score += profile.aggressive_bonus
    if str(payload.get("attack_variant") or "") == "triple":
        ordinary = {**payload, "attack_variant": "default"}
        normal_damage = estimate_attack_damage(battle, actor, target, ordinary, attack_power=battle.basic_attack_preview_power(actor, ordinary))
        score -= 35.0 if min(normal_damage, target.current_hp) >= min(expected_damage, target.current_hp) else 8.0 + max(0.0, min(target.current_hp, normal_damage * 3) - expected_damage) * 100.0
    if actor.hero_code == "li" and expected_damage > 0 and not actor.cannot_heal:
        score += min(0.25, max(0.0, actor.max_health - actor.current_hp)) * 140.0
    if actor.hero_code == "erasure_apostle" and expected_damage >= target.current_hp - 1e-9:
        score += min(target.current_mana, max(0.0, actor.max_mana() - actor.current_mana)) * 26.0
        if not target.is_clone and not target.is_summon and actor.get_skill("erasure").uses_this_battle > 0:
            score += 55.0
    if actor.hero_code == "n":
        score += 18.0 + (20.0 if actor.current_mana < 1 else 0.0)
    forced_target = required_attack_target(battle, actor)
    if forced_target is not None and target.unit_id == forced_target.unit_id:
        score += 500.0
    score += reviewed_attack_synergy(battle, actor, target, expected_damage)
    if actor.hero_code == "excel_r194":
        score += alexander_expected_swap_value(battle, actor)
    return score


def alexander_expected_swap_value(battle: Battle, actor: Unit) -> float:
    """Price the mandatory post-attack swap before spending an attack."""
    from wujiang.tactical.heroes.excel_roster import gladiator_swap, gladiator_swap_destinations

    allies = [unit for unit in battle.player_units(actor.player_id)
              if unit.unit_id != actor.unit_id and unit.alive and not unit.banished
              and gladiator_swap_destinations(battle, actor, unit, via_skill=False) is not None]
    if not allies:
        return 0.0
    units = list(battle.player_units(actor.player_id))
    before_positions = {unit.unit_id: reviewed_r17_position_value(battle, actor, unit) for unit in units}
    before_magic = {unit.unit_id: alexander_magic_coverage_value(battle, actor, unit) for unit in units}
    best = -1000.0
    for ally in allies:
        with ai_probe_rollback(battle):
            before_state = r23_action_state(battle)
            if not gladiator_swap(battle, actor, ally, skill_code="alexander_attack_swap", via_skill=False):
                continue
            value = r23_action_delta(actor, before_state)
            value += sum((reviewed_r17_position_value(battle, actor, unit) - before_positions[unit.unit_id]) * 0.65
                         + alexander_magic_coverage_value(battle, actor, unit) - before_magic[unit.unit_id]
                         for unit in units)
            best = max(best, value)
    return max(-100.0, min(100.0, best)) if best > -1000.0 else 0.0


def alexander_magic_coverage_value(battle: Battle, actor: Unit, ally: Unit) -> float:
    if ally.player_id != actor.player_id or not ally.magic_immunity or not ally.alive or ally.position is None:
        return 0.0
    threats = [enemy for enemy in battle.enemy_units(actor.player_id)
               if enemy.alive and enemy.position is not None and not enemy.banished and not enemy.cannot_use_skills
               and any(skill.timing == "active" and skill.code in DAMAGING_SKILL_CODES for skill in enemy.skills)
               and battle.distance_between_units(enemy, ally) <= enemy.normal_move_distance() + enemy.targeting_range()]
    if not threats:
        return 0.0
    return min(48.0, max(min(ally.current_hp, estimate_damage(battle, ally, enemy.stat("attack"))) * 80.0
                           for enemy in threats))


def score_skill_payload(
    battle: Battle,
    actor: Unit,
    action: dict[str, Any],
    payload: dict[str, Any],
    profile: DifficultyProfile,
    *,
    instant_only: bool,
) -> float:
    score = _score_skill_payload(battle, actor, action, payload, profile, instant_only=instant_only)
    if actor.hero_code == "excel_r123" and score > 0:
        skill = skill_from_ai_action(actor, action, str(action.get("code") or payload.get("skill_code") or ""))
        if skill.timing == "active":
            after_payment = actor.current_mana - skill.mana_cost_for_payload(battle, actor, payload)
            score += 0.5 * min(1.0, max(0.0, actor.max_mana() - after_payment)) * 24.0
    return score


def _score_skill_payload(
    battle: Battle,
    actor: Unit,
    action: dict[str, Any],
    payload: dict[str, Any],
    profile: DifficultyProfile,
    *,
    instant_only: bool,
) -> float:
    code = str(action.get("code") or payload.get("skill_code") or "")
    if code == "electronic_repair":
        return electronic_repair_score(battle, actor, payload)
    if code == "satellite_cannon":
        return r23_paid_skill_value(battle, actor, payload)
    if code in {"flash_slash", "bird_dash", "bird_dash_free", "bird_soul", "bird_soul_free"}:
        return r215219_skill_score(battle, actor, payload)
    if code == "gladiator_soul":
        return r23_paid_skill_value(battle, actor, payload)
    if code == "water_wave_cannon":
        return reviewed_r18_effect_score(battle, actor, skill_from_ai_action(actor, action, code), payload, profile)
    if code == "battle_hurricane":
        skill = skill_from_ai_action(actor, action, code)
        if not skill.nearby_allies(battle, actor):
            return -1000.0
        value = r23_paid_skill_value(battle, actor, payload)
        return value if value > 0 else -1000.0
    if code == "earth_shatter":
        if not skill_payload_has_effective_enemy_impact(battle, actor, action, payload):
            return -1000.0
        value = r23_paid_skill_value(battle, actor, payload)
        return value - 28.0 if value > 28.0 else -1000.0
    if code in {"summon_medium_stone", "summon_small_stone"}:
        return stone_summon_score(battle, actor, code, payload, profile)
    if code in {"gladiator_claw", "gladiator_gale"}:
        return reviewed_r17_effect_score(battle, actor, skill_from_ai_action(actor, action, code), payload, profile)
    if code in {"demon_blade", "nuclear_mutation", "gravity_field", "punisher_heal", "sanctuary_banish", "sanctuary_judgment"}:
        return r12_effect_score(battle, actor, skill_from_ai_action(actor, action, code), payload, profile)
    if code in {"hundred_bird_burial", "remi_chaos", "nian_large_dragon_breath", "nian_roar", "nian_jade_flash", "nian_dragon_dance"} or (actor.hero_code == "excel_r056" and code == "pierce"):
        return r13_effect_score(battle, actor, skill_from_ai_action(actor, action, code), payload, profile)
    if code == "summon_remi_bat":
        return r13_bat_score(battle, actor, skill_from_ai_action(actor, action, code), payload, profile)
    if code == "sun_slash":
        return r23_sun_slash_score(battle, actor, skill_from_ai_action(actor, action, code), payload)
    if actor.hero_code == "excel_r379" and code == "pierce":
        return r23_paid_skill_value(battle, actor, payload)
    if code == "heal" and actor.hero_code == "excel_r337":
        return r22_heal_score(battle, actor, skill_from_ai_action(actor, action, code), payload)
    if code in {"eagle_eye", "vain_giant_shadow"}:
        return r21_control_score(battle, actor, skill_from_ai_action(actor, action, code), payload, profile)
    if code == "fly_leap" and actor.hero_code == "excel_r264":
        return r21_winged_leap_score(battle, actor, payload)
    if code == "missile" or (actor.hero_code in {"excel_r264", "excel_r326"} and code in {"pierce", "remote_pierce"}):
        return reviewed_r18_effect_score(battle, actor, skill_from_ai_action(actor, action, code), payload, profile)
    if code in {"machine_gun", "electronic_laser", "vitality_blast", "dragon_breath", "remote_dragon_breath"}:
        return reviewed_r18_effect_score(battle, actor, skill_from_ai_action(actor, action, code), payload, profile)
    if code == "black_cat_paw":
        skill = skill_from_ai_action(actor, action, code)
        targets = skill_effect_units(battle, actor, skill, payload)
        if not any(unit.player_id != actor.player_id for unit in targets):
            return -1000.0
        with ai_probe_rollback(battle):
            before = {unit.unit_id: (unit.current_hp, unit.current_mana, unit.total_shields()) for unit in targets}
            before_mana = actor.current_mana
            skill.execute(battle, actor, payload)
            score = (actor.current_mana - before_mana) * 18.0
            for unit in targets:
                hp, mana, shields = before[unit.unit_id]
                value = max(0.0, hp - unit.current_hp) * 160.0 + max(0.0, mana - unit.current_mana) * 24.0
                value += max(0, shields - unit.total_shields()) * 20.0
                if not unit.alive:
                    value += 80.0
                score += value if unit.player_id != actor.player_id else -value * 1.3
        return score if score > 0 else -1000.0
    if code == "snow_avalanche":
        return r14_snow_avalanche_score(battle, actor, skill_from_ai_action(actor, action, code), payload)
    if code == "heaven_punishment":
        skill = skill_from_ai_action(actor, action, code)
        targets = skill_effect_units(battle, actor, skill, payload)
        selected_code = str(payload.get("disabled_skill_code") or "")
        target = battle.units.get(str(payload.get("target_unit_id") or ""))
        if target is None or not selected_code:
            return -1000.0
        control_value = skill_control_bonus(battle, actor, code, payload, targets, profile, instant_only=instant_only)
        with ai_probe_rollback(battle):
            before = {unit.unit_id: (unit.current_hp, unit.total_shields()) for unit in targets}
            sealed_before = any(getattr(status, "skill_code", None) == selected_code for status in target.statuses)
            try:
                skill.execute(battle, actor, payload)
            except ActionError:
                return -1000.0
            score = 0.0
            for unit in targets:
                hp, shields = before[unit.unit_id]
                value = max(0.0, hp - unit.current_hp) * 155.0 + max(0, shields - unit.total_shields()) * 20.0
                if not unit.alive:
                    value += 80.0
                score += value if unit.player_id != actor.player_id else -value * 1.35
            sealed_after = any(getattr(status, "skill_code", None) == selected_code for status in target.statuses)
            if sealed_after and not sealed_before:
                score += max(0.0, control_value)
        return score if score > 0 else -1000.0
    if code == "morning_holy_light":
        return morning_holy_light_score(battle, actor, skill_from_ai_action(actor, action, code), payload)
    if code == "lao_wave_bullet":
        skill = skill_from_ai_action(actor, action, code)
        score = skill_damage_score(battle, actor, skill, payload, profile)
        cost = skill.mana_cost_for_payload(battle, actor, payload)
        return score - cost * (30.0 if actor.current_mana <= 1.0 else 15.0)
    if code == "punisher_heal":
        target = battle.units.get(str(payload.get("target_unit_id") or ""))
        if target is None or target.player_id != actor.player_id or not target.alive:
            return -1000.0
        missing_hp = max(0.0, target.max_health - target.current_hp)
        missing_mana = max(0.0, target.max_mana() - target.current_mana)
        if target.unit_id == actor.unit_id:
            missing_mana = max(0.0, missing_mana - 1.0)
        hp_gain = min(0.25, missing_hp)
        mana_gain = min(1.0, missing_mana)
        return hp_gain * (240.0 if target.current_hp <= 0.5 else 150.0) + mana_gain * 35.0 - 9.0 if hp_gain or mana_gain else -1000.0
    if code == "sanctuary_banish":
        targets = actor.get_skill(code).targets(battle, actor)
        return sum(35.0 + (35.0 if not target.cannot_attack else 0.0)
                   + 12.0 * sum(skill.timing == "active" for skill in target.skills)
                   for target in targets if target.alive and not target.has_status("圣殿放逐")) - 8.0 if targets else -1000.0
    if code == "sanctuary_judgment":
        targets = actor.get_skill(code).targets(battle, actor)
        score = 0.0
        for target in targets:
            if target.magic_immunity:
                continue
            per_hit = estimate_damage(battle, target, target.stat("attack"), ignore_shield=True)
            shielded_hits = min(5, target.total_shields() + target.dodge_charges)
            damage = min(target.current_hp, max(0, 5 - shielded_hits) * per_hit)
            score += damage * 115.0 + (90.0 if damage >= target.current_hp - 1e-9 else 0.0)
        return score - 18.0 if score > 0 else -1000.0
    if code == "gravity_field":
        skill = actor.get_skill(code)
        try:
            center = skill.chosen_center(battle, actor, payload)
        except (ActionError, TypeError, ValueError):
            return -1000.0
        score = 0.0
        for side, weight in ((1, 1), (2, 3), (4, 3), (8, 1)):
            cells = skill.cells_for_side(battle, center, side)
            for target in battle.units_at_cells(cells):
                if not target.alive:
                    continue
                hits = 1 if any(trait.name == "多格范围伤害保护" for trait in target.traits) else battle.unit_hit_count_for_cells(target, cells)
                attack_power = actor.stat("attack") + max(0, hits - 1)
                damage = 0.0 if target.magic_immunity or target.dodge_charges > 0 else estimate_damage(
                    battle, target, attack_power, half_ignore_shield=True,
                )
                drain = 0.0 if any(getattr(component, "prevents_mana_drain", False) for component in target.iter_components()) else min(1.0, target.current_mana)
                sign = 1.0 if target.player_id != actor.player_id else -1.5
                score += weight / 8.0 * sign * (
                    min(target.current_hp, damage) * 105.0
                    + drain * 25.0
                    + (75.0 if damage > 0 and damage >= target.current_hp - 1e-9 else 0.0)
                )
        return score - 10.0 if score > 0 else -1000.0
    if code == "remi_chaos":
        destination = payload_destination(payload)
        if destination is None or actor.position is None or actor.cannot_move:
            return -1000.0
        skill = skill_from_ai_action(actor, action, code)
        damage_score = skill_damage_score(battle, actor, skill, payload, profile)
        position_gain = score_move_destination(battle, actor, destination, hero_style(actor), profile) - score_move_destination(
            battle, actor, actor.position, hero_style(actor), profile,
        )
        score = damage_score + position_gain * 0.7
        return score if score >= profile.once_per_battle_threshold else score - 32.0
    if code == "nian_roar":
        target = battle.units.get(str(payload.get("target_unit_id") or ""))
        if target is None or not target.alive or target.magic_immunity:
            return -1000.0
        damage_score = skill_damage_score(battle, actor, skill_from_ai_action(actor, action, code), payload, profile)
        future_targets = [unit for unit in battle.player_units(actor.player_id)
                          if unit.alive and unit.unit_id != actor.unit_id and unit.position is not None
                          and battle.distance_between_units(target, unit) <= target.targeting_range() + target.normal_move_distance()]
        control = min(90.0, len(future_targets) * 35.0) if not target.has_status("怒吼") and target.current_hp > 0.25 else 0.0
        return damage_score + control
    if code == "nian_jade_flash":
        affected = skill_effect_units(battle, actor, skill_from_ai_action(actor, action, code), payload)
        score = 0.0
        for target in affected:
            if target.unit_id == actor.unit_id or target.magic_immunity or target.cannot_heal:
                continue
            missing = max(0.0, target.max_health - target.current_hp)
            natural = any(trait.name in {"自然回血", "自然回复"} for trait in target.traits)
            healing_skills = sum(skill.code in HEAL_SKILL_CODES or skill.code in {"punisher_heal", "nian_dragon_dance"}
                                 for skill in target.skills)
            value = missing * 70.0 + (18.0 if natural else 0.0) + healing_skills * 22.0
            score += value if target.player_id != actor.player_id else -value * 1.5
        return score - 8.0 if score > 8.0 else -1000.0
    if code == "equip_mage_cloak":
        target = battle.units.get(str(payload.get("target_unit_id") or ""))
        if target is None or target.player_id != actor.player_id or target.position is None:
            return -1000.0
        nearby_enemy = any(battle.distance_between_units(target, enemy) <= target.normal_move_distance() + target.targeting_range()
                           for enemy in living_hostile_combatants(battle, actor.player_id))
        return 26.0 + (24.0 if nearby_enemy else 0.0) + (15.0 if target.stat("speed") <= 3 else 0.0)
    if code == "detach_mage_cloak":
        status = actor.get_status("法师斗篷")
        cloak = battle.units.get(getattr(status, "cloak_unit_id", "")) if status is not None else None
        destination = payload_destination(payload)
        if cloak is None or destination is None:
            return -1000.0
        recover = max(0.0, cloak.max_health - cloak.current_hp) * 70.0
        reach = sum(18.0 for enemy in living_hostile_combatants(battle, actor.player_id)
                    if max(abs(destination.x - enemy.position.x), abs(destination.y - enemy.position.y)) <= cloak.targeting_range())
        return recover + reach + summon_position_score(battle, actor, destination) * 0.25 - 48.0
    if code == "lao_mage_hand":
        skill = skill_from_ai_action(actor, action, code)
        target = battle.units.get(str(payload.get("target_unit_id") or ""))
        if target is None or target.position is None:
            return -1000.0
        before_position = target.position
        before_hp = target.current_hp
        before_shields = target.total_shields()
        with ai_probe_rollback(battle):
            try:
                skill.execute(battle, actor, payload)
            except ActionError:
                return -1000.0
            damage = max(0.0, before_hp - target.current_hp)
            shield_break = max(0, before_shields - target.total_shields())
            move_gain = 0.0
            if target.alive and target.position is not None and target.position != before_position:
                style = hero_style(target)
                move_gain = (score_move_destination(battle, target, before_position, style, profile)
                             - score_move_destination(battle, target, target.position, style, profile)) * 0.5
            score = damage * 100.0 + shield_break * 16.0 + move_gain
            if not target.alive:
                score += 90.0
            return score - 8.0 if score > 0 else -1000.0
    if code in {"natsume_wind_word", "natsume_dispel"}:
        return natsume_skill_score(battle, actor, skill_from_ai_action(actor, action, code), payload)
    if actor.hero_code == "excel_r030" and code == "pierce":
        return fusion_pierce_score(battle, actor, payload)
    if code == "mimic_skill":
        context = mimic_payload_context(battle, actor, payload)
        if context is None:
            return -1000.0
        _, copied, copied_payload, copied_action = context
        mimic = actor.get_skill("mimic_skill")
        with mimic.copying(actor, copied, reserve_point=True):
            value = score_skill_payload(battle, actor, copied_action, copied_payload, profile, instant_only=instant_only)
            mana = copied.mana_cost_for_payload(battle, actor, copied_payload)
        return value - (18.0 if actor.mana_points > 1 else 30.0) - mana * 8.0
    if code == "agency_borrowed_skill":
        context = agency_borrowed_payload_context(battle, actor, payload)
        if context is None:
            return -10.0
        _, copied, copied_payload, copied_action = context
        if copied.code in SELF_BUFF_SKILL_CODES:
            return -1000.0
        with actor.get_skill("agency_borrowed_skill").copying(actor, copied):
            if not copied.can_use(battle, actor, copied_payload)[0]:
                return -1000.0
            return score_skill_payload(battle, actor, copied_action, copied_payload, profile, instant_only=instant_only)
    if code == "guardian_finale":
        return guardian_finale_score(battle, actor, profile)
    if code in {"fantasy_move", "rainbow_mirror", "true_blade_air_slash", "undead_boy_devour"}:
        return reviewed_r17_effect_score(battle, actor, skill_from_ai_action(actor, action, code), payload, profile)
    if code in {"illumination_light", "thor_heavy_hammer", "thor_rage_impact", "thor_destroy_lightning"} or (actor.hero_code == "excel_r138" and code in {"pierce", "dragon_breath"}):
        return reviewed_r18_effect_score(battle, actor, skill_from_ai_action(actor, action, code), payload, profile)
    if code in {"hell_slash", "electric_wind", "beetle_spear"}:
        return reviewed_r19_effect_score(battle, actor, skill_from_ai_action(actor, action, code), payload, profile)
    if code == "fuma_shuriken":
        skill = skill_from_ai_action(actor, action, code)
        return reviewed_r16_damage_result(battle, actor, lambda: skill.execute(battle, actor, payload)) - 8.0
    if code == "agency_contract":
        return agency_contract_score(battle, actor, payload, profile)
    if code == "weapon_copy":
        return weapon_copy_score(battle, actor, payload, profile)
    if code in {"weapon_transfer", "red_charge", "infinite", "infinite_armor", "infinite_robe"}:
        return red_preparation_score(battle, actor, code, profile)
    if code == "deadly_bow":
        return deadly_bow_score(battle, actor, action, payload, profile)
    if code == "wuchang_mist":
        return wuchang_mist_score(battle, actor, profile)
    if code == "split" and actor.hero_code == "excel_r027":
        return wuchang_split_score(battle, actor, profile)
    if code == "migratory_bird_mark":
        return migratory_bird_mark_score(battle, actor, action, payload, profile)
    skill = skill_from_ai_action(actor, action, code)
    if code in {"royal_soldier", "fried_inspire"}:
        return fried_skill_score(battle, actor, skill, payload, profile)
    if code in {"frey_quick_flash", "frey_god_stab", "frey_lion_spear"}:
        return frey_skill_score(battle, actor, skill, payload, profile)
    if code in {"iaido_charge", "ghost_step", "time_stop", "focus_reset"}:
        return perfect_swordsman_skill_score(battle, actor, skill, payload, profile)
    if code in {"premature_burial", "erasure", "descent_moment", "dragon_slash", "smoke_spray", "extra_stealth"}:
        return reviewed_r06_skill_score(battle, actor, skill, payload, profile)
    if code in {"rock_absorb", "rock_cannon", "doom_light", "apocalypse"}:
        return resource_effect_score(battle, actor, skill, payload, profile)
    if code in {"blood_guard", "blood_art", "blood_dance", "sacrifice_ritual"}:
        return blood_support_score(battle, actor, skill, payload, profile)
    if code in {"paralysis_card", "poison_card", "drain_card", "magic_claw"}:
        return chanter_card_score(battle, actor, skill, payload, profile)
    if code in {"chain_pull", "whirlwind_attack"}:
        return li_area_skill_score(battle, actor, skill, payload, profile)
    if code == "magnetic_wave":
        return magnetic_wave_score(battle, actor, skill, payload)
    targets = skill_effect_units(battle, actor, skill, payload)
    role = hero_style(actor)
    if code == "iron_chain_path":
        return iron_chain_path_score(battle, actor, skill, payload, profile)
    if code == "sphinx_cannon":
        return sphinx_cannon_score(battle, actor, payload)
    if code == "solar_judgment":
        return solar_judgment_score(battle, actor, skill, payload, profile)
    if code == "curse":
        eligible = [unit for unit in targets if unit.player_id != actor.player_id and not unit.has_status("诅咒")]
        if actor.current_hp <= 0.5 or not eligible:
            return -1000.0
        # A curse is delayed attrition, not an immediate attack using Ellie's attack stat.
        return sum(unit.current_hp * 80.0 for unit in eligible) - 60.0 - (35.0 if actor.current_hp <= 0.75 else 0.0)
    if code == "mana_pull":
        target = primary_target_unit(battle, payload, targets)
        if target is None or target.position is None or payload.get("dest_x") is None or payload.get("dest_y") is None:
            return -1000.0
        destination = Position(int(payload["dest_x"]), int(payload["dest_y"]))
        style = hero_style(target)
        change = score_move_destination(battle, target, destination, style, profile) - score_move_destination(battle, target, target.position, style, profile)
        return change - 12.0 if target.player_id == actor.player_id else -change + (20.0 if not target.cannot_normal_move else 0.0) - 12.0
    if code in {"cat_taunt_roar", "ring_taunt"}:
        return taunt_control_score(battle, actor, skill, payload, code, targets, profile)
    if code == "jirobo_follow_step":
        destination = payload_destination(payload)
        if destination is None or actor.position is None:
            return -1000.0
        gain = score_move_destination(battle, actor, destination, role, profile) - score_move_destination(
            battle, actor, actor.position, role, profile,
        )
        return gain + 8.0 if gain > 3.0 else -8.0
    if code in MOVE_SKILL_CODES:
        destination = payload_destination(payload)
        if destination is None:
            return -5.0
        origin_score = score_move_destination(battle, actor, actor.position, role, profile) if actor.position is not None else 0.0
        score = score_move_destination(battle, actor, destination, role, profile) - origin_score - 1.0
        if code == "fate_kick" and actor.position is not None:
            dx, dy = destination.x - actor.position.x, destination.y - actor.position.y
            if dx or dy:
                impact = destination.offset(0 if dx == 0 else (1 if dx > 0 else -1), 0 if dy == 0 else (1 if dy > 0 else -1))
                target = battle.unit_at(impact) if battle.in_bounds(impact) else None
                if target is not None and target.player_id != actor.player_id:
                    score += 0.5 * (hostile_unit_value(target) - ally_unit_value(actor))
                    score -= max(0, actor.attack_actions_per_turn() - actor.attacks_used) * 22.0
        if code == "zero_dash":
            score += zero_dash_score(battle, actor, skill, payload)
        if code == "fuma_pursuit":
            score += fuma_pursuit_score(battle, actor, skill, payload, profile)
        if code == "crazy_sand":
            score += skill_damage_score(battle, actor, skill, payload, profile) - 15.0
        if code == "mounted_leap" and actor.position is not None:
            before = masamune_attack_plan_value(battle, actor, profile)
            with ai_probe_rollback(battle):
                battle.clear_mounted_state(actor)
                actor.position = destination
                followup = next((candidate for candidate in actor.skills if candidate.code == "six_blade_style"), None)
                if followup is not None and followup.can_use(battle, actor, {})[0]:
                    followup.execute(battle, actor, {})
                after = masamune_attack_plan_value(battle, actor, profile)
            score += min(100.0, after - before) - 40.0
        if code == "true_blade_air_slash":
            score += true_blade_air_slash_score(battle, actor, skill, payload, profile)
        return score
    if code == "judgment_stone":
        return judgment_stone_score(battle, actor, payload, profile)
    if code == "world_seed":
        return world_seed_score(battle, actor, payload, profile)
    if code in SUMMON_SKILL_CODES:
        destination = payload_destination(payload)
        if code == "summon_remi_bat":
            if destination is None:
                return -1000.0
            reachable = [enemy for enemy in living_hostile_combatants(battle, actor.player_id)
                         if enemy.position is not None and max(abs(enemy.position.x - destination.x),
                                                               abs(enemy.position.y - destination.y)) <= 4]
            cover = any(enemy.position is not None and battle.distance_between_units(actor, enemy) <= 3
                        for enemy in living_hostile_combatants(battle, actor.player_id))
            return 30.0 + summon_position_score(battle, actor, destination) + len(reachable) * 25.0 + (12.0 if cover else 0.0) if reachable or cover else -8.0
        if code == "split" and actor.hero_code == "n":
            if best_available_attack_score(battle, actor, profile) > 0:
                return -1000.0
            nearby = any(distance_between_units(battle, actor, enemy) <= enemy.normal_move_distance() + enemy.targeting_range() for enemy in living_hostile_combatants(battle, actor.player_id))
            return 22.0 if nearby and actor.current_mana >= 2.5 else -20.0
        if code == "split" and actor.hero_code == "excel_r118":
            if best_available_attack_score(battle, actor, profile) > profile.action_threshold:
                return -1000.0
            dash = actor.get_skill("zero_dash")
            if dash.can_use(battle, actor, {})[0]:
                dash_action = copied_skill_action(battle, actor, dash)
                for dash_payload in skill_payloads_for_action(battle, actor, dash_action):
                    if zero_dash_score(battle, actor, dash, dash_payload) > profile.action_threshold:
                        return -1000.0
            if actor.position is not None and actor.remaining_normal_move_distance(battle) > 0:
                if any(candidate.score > profile.action_threshold
                       for candidate in build_zero_crossing_move_candidates(battle, actor, hero_style(actor), profile)):
                    return -1000.0
        if code == "split" and actor.hero_code == "excel_r136":
            if best_available_attack_score(battle, actor, profile) > profile.action_threshold:
                return -1000.0
            for other in actor.skills:
                if other.code not in {"true_blade_air_slash", "pierce", "oboro_meditate"} or not other.can_use(battle, actor, {})[0]:
                    continue
                other_action = copied_skill_action(battle, actor, other)
                for other_payload in skill_payloads_for_action(battle, actor, other_action):
                    if score_skill_payload(battle, actor, other_action, other_payload, profile, instant_only=False) > profile.action_threshold:
                        return -1000.0
        if code == "split" and actor.hero_code == "excel_r113":
            if best_available_attack_score(battle, actor, profile) > 0:
                return -1000.0
            for other in actor.skills:
                if other.code not in {"purify_mana", "sacred_duel", "dragon_breath", "drain_mana"}:
                    continue
                if not other.can_use(battle, actor, {})[0]:
                    continue
                other_action = copied_skill_action(battle, actor, other)
                for other_payload in skill_payloads_for_action(battle, actor, other_action):
                    if score_skill_payload(battle, actor, other_action, other_payload, profile, instant_only=False) > profile.action_threshold:
                        return -1000.0
        if code == "earth_walker":
            if actor.has_status("土行者") or not living_hostile_combatants(battle, actor.player_id):
                return -1000.0
            if best_available_attack_score(battle, actor, profile) > 0:
                return -1000.0
            nearby = any(distance_between_units(battle, actor, enemy) <= enemy.normal_move_distance() + enemy.targeting_range() for enemy in living_hostile_combatants(battle, actor.player_id))
            return 24.0 + max(0.0, actor.max_health - actor.current_hp) * 45.0 if nearby else -8.0
        if code in {"summon_bicycle", "summon_unicycle"}:
            return cycle_summon_score(battle, actor, code, destination, profile)
        score = 42.0
        if destination is not None:
            score += summon_position_score(battle, actor, destination)
        if code in {"earth_walker", "split"}:
            score += 10.0
        score += follow_catch_up_skill_penalty(battle, actor)
        return score
    if code == "nian_dragon_dance":
        missing_hp = 0.0 if actor.cannot_heal else max(0.0, actor.max_health - actor.current_hp)
        missing_mana = max(0.0, actor.max_mana() - actor.current_mana)
        restored_mana = min(4.0, missing_mana)
        if missing_hp <= 0 and restored_mana < 2:
            return -8.0
        return missing_hp * 140.0 + restored_mana * 24.0
    if code == "oboro_meditate":
        missing_mana = max(0.0, actor.max_mana() - actor.current_mana)
        restored = min(1.5, missing_mana)
        return restored * 34.0 if restored >= 0.5 else -8.0
    if code in HEAL_SKILL_CODES:
        healed = primary_target_unit(battle, payload, targets)
        if code == "heal_mount":
            healed = battle.mounted_unit_for(actor)
        if healed is None:
            healed = actor
        if code in {"heal", "heal_mount", "mech_enhancement"}:
            with ai_probe_rollback(battle):
                before_hp = healed.current_hp
                before_defense = healed.stat("defense")
                skill.execute(battle, actor, payload)
                gain = healed.current_hp - before_hp
                defense_gain = healed.stat("defense") - before_defense
            return gain * 200.0 + max(0.0, defense_gain) * 24.0 + (50.0 if gain > 0 and healed.current_hp <= 0.5 else 0.0) if gain > 0 or defense_gain > 0 else -1000.0
        missing = max(0.0, healed.max_health - healed.current_hp)
        score = missing * 120.0
        if code == "mech_enhancement":
            score += 24.0
        return score
    if code == "rainbow_mirror":
        return rainbow_mirror_score(battle, actor, payload, targets, profile)
    if code == "recover_mana":
        target = primary_target_unit(battle, payload, targets)
        if target is None or target.player_id != actor.player_id:
            return -1000.0
        cost = skill.mana_cost_for_payload(battle, actor, payload)
        available = target.current_mana - (cost if target.unit_id == actor.unit_id else 0.0)
        gained = min(1.0, max(0.0, target.max_mana() - available))
        if gained <= 0 or (target.unit_id == actor.unit_id and gained <= cost):
            return -1000.0
        return gained * 48.0 - cost * 18.0
    if code in ALLY_BUFF_SKILL_CODES:
        target = primary_target_unit(battle, payload, targets)
        if target is None:
            return -2.0
        score = ally_buff_score(battle, actor, code, target, profile)
        return score
    if code in SELF_BUFF_SKILL_CODES:
        return self_buff_score(battle, actor, code, profile) + follow_catch_up_skill_penalty(battle, actor)
    if code == "great_funeral":
        return great_fire_funeral_score(battle, actor, skill, payload, profile)
    if code == "wind_sand":
        if not targets:
            return -1000.0
        return skill_damage_score(battle, actor, skill, payload, profile) + sandstorm_value(battle, actor)
    if code in {"stance", "great_holy_light", "plant_growth", "smoke_spray"}:
        return field_skill_score(battle, actor, code, targets, profile)
    if code in {"drain_mana", "large_drain_mana"}:
        return drain_mana_score(battle, actor, targets, profile)
    if code == "kaiser_fist":
        return kaiser_fist_score(battle, actor, skill, payload, profile)
    if code == "interference":
        return reviewed_r15_effect_score(battle, actor, skill, payload, profile)
    if code == "noise_wave":
        return reviewed_r15_effect_score(battle, actor, skill, payload, profile)
    if code == "purify_mana":
        return reviewed_r15_effect_score(battle, actor, skill, payload, profile)
    if code == "sacred_duel":
        return reviewed_r15_effect_score(battle, actor, skill, payload, profile)
    if code == "fuma_trap":
        return fuma_trap_score(battle, actor, payload, profile)
    if code == "fantasy_move":
        return fantasy_move_score(battle, actor, skill, payload, targets, profile)
    if code == "friendly_mirror":
        return friendly_mirror_score(battle, actor, profile)
    if code == "electric_wind":
        return reviewed_r19_effect_score(battle, actor, skill, payload, profile)
    if code == "vain_giant_shadow":
        return vain_giant_shadow_score(battle, actor, payload, targets, profile)
    if code == "undead_boy_devour":
        return undead_boy_devour_score(battle, actor, skill, payload, targets, profile)
    if code == "illumination_light":
        return illumination_light_score(battle, actor, skill, payload, targets, profile)
    if code in DAMAGING_SKILL_CODES or code in CONTROL_SKILL_CODES:
        score = skill_damage_score(battle, actor, skill, payload, profile)
        score += skill_control_bonus(battle, actor, code, payload, targets, profile, instant_only=instant_only)
        if skill.max_uses_per_battle is not None and skill.max_uses_per_battle <= 1 and score < profile.once_per_battle_threshold:
            score -= 32.0
        return score
    return generic_skill_score(battle, actor, code, targets, profile)


def score_reaction_payload(
    battle: Battle,
    reactor: Unit,
    queued_action: QueuedAction,
    option: dict[str, Any],
    payload: dict[str, Any],
    profile: DifficultyProfile,
) -> float:
    code = str(option.get("action_code") or "")
    if reactor.hero_code == "excel_r192" and code == "gladiator_roar":
        return andrew_reaction_score(battle, reactor, queued_action, payload, code)
    if (reactor.hero_code == "excel_r337" and code == "light_wall") or (reactor.hero_code == "excel_r352" and code == "evasion"):
        return r22_paid_reaction_score(battle, reactor, queued_action, payload, code)
    if reactor.hero_code in {"excel_r327", "excel_r337", "excel_r352"} and code == "counter":
        source = battle.get_unit(queued_action.actor_id)
        return r22_attack_value(battle, reactor, source, {"reaction_attack": True}) - stone_attack_spawn_penalty(
            battle, reactor, {"type": "attack", "unit_id": reactor.unit_id, "target_unit_id": source.unit_id})
    if reactor.hero_code == "excel_r225" and code in {"ion_shield", "quantum_shield"}:
        return r21_barrier_shield_score(battle, reactor, queued_action, payload, code)
    if reactor.hero_code == "excel_r326" and code == "counter":
        source = battle.get_unit(queued_action.actor_id)
        return r21_attack_value(battle, reactor, source, {"reaction_attack": True}) - stone_attack_spawn_penalty(
            battle, reactor, {"type": "attack", "unit_id": reactor.unit_id, "target_unit_id": source.unit_id})
    if code == "lao_damage_stat_cancel":
        if queued_action.action_type == "skill" and str(queued_action.payload.get("skill_code") or "") not in DAMAGING_SKILL_CODES:
            return -1000.0
        threat = incoming_threat_score(battle, reactor, queued_action)
        stat = str(payload.get("stat_name") or "")
        cost = {"attack": 48.0, "defense": 52.0, "speed": 40.0, "attack_range": 44.0}.get(stat)
        if cost is None or threat <= 0:
            return -1000.0
        if reactor.stat(stat) <= 2:
            cost += 30.0
        if reactor.current_hp <= threat / 100.0:
            threat += 100.0
        return threat - cost
    if code == "natsume_wind_wall":
        return natsume_wall_score(battle, reactor, queued_action, payload)
    if code == "floating_cannon_cover":
        skill = reactor.get_skill(code)
        if not skill.can_react_with_payload(battle, reactor, queued_action, payload)[0]:
            return -1000.0
        target = battle.get_unit(str(payload["target_unit_id"]))
        cannon = battle.get_unit(str(payload["cannon_unit_id"]))
        threat = incoming_threat_score(battle, target, queued_action)
        if target.player_id != reactor.player_id:
            threat = -threat
        cost = 42.0 + (18.0 if cannon.attacks_used < cannon.attack_actions_per_turn() else 0.0)
        if target.player_id == reactor.player_id and target.current_hp <= threat / 100.0:
            threat += 100.0
        return threat - cost
    if code == "agency_borrowed_skill":
        context = agency_borrowed_payload_context(battle, reactor, payload)
        if context is None:
            return -1000.0
        _, copied, inner, _ = context
        with reactor.get_skill(code).copying(reactor, copied):
            return score_reaction_payload(battle, reactor, queued_action, dict(option, action_code=copied.code),
                                          dict(inner, action_code=copied.code), profile)
    attacker = battle.get_unit(queued_action.actor_id)
    proxy_target = battle.reaction_proxy_target(reactor, queued_action) or reactor
    threat = incoming_threat_score(battle, proxy_target, queued_action)
    if code == "time_stop":
        return perfect_swordsman_skill_score(battle, reactor, reactor.get_skill(code), payload, profile) + threat
    if code == "ghost_step":
        destination = payload_destination(payload)
        if destination is None or destination_still_in_queued_target_area(battle, reactor, destination, queued_action):
            return -1000.0
        return threat + 4.0 + score_move_destination(battle, reactor, destination, hero_style(reactor), profile) * 0.2
    if code == "foresight":
        return threat + 100.0 if threat > 0 else 45.0
    if code in REACTION_SHIELD_CODES:
        if queued_action.payload.get("ignore_shield"):
            return -20.0
        target_ids = [str(unit_id) for unit_id in payload.get("target_unit_ids", []) if unit_id]
        if payload.get("target_unit_id"):
            target_ids.append(str(payload["target_unit_id"]))
        targets = [battle.units.get(unit_id) for unit_id in dict.fromkeys(target_ids)]
        targets = [unit for unit in targets if unit is not None]
        targets = [unit for unit in targets if battle.target_can_chain_against(unit, queued_action)]
        if not targets:
            return -20.0
        if any(unit.unit_id in queued_shield_protected_unit_ids(battle) for unit in targets):
            return -40.0
        score = 0.0
        urgent = False
        for target in targets:
            target_threat = incoming_threat_score(battle, target, queued_action)
            score += target_threat + 20.0
            if target.current_hp <= max(0.25, target_threat / 100.0):
                score += 55.0
                urgent = True
            if target.unit_id == reactor.unit_id and target_threat > 0:
                score += 18.0
                urgent = True
        try:
            skill = reactor.get_skill(code)
            cost = float(skill.mana_cost_for_payload(battle, reactor, payload))
        except Exception:
            cost = 0.0
        if reactor.current_mana - cost < active_damage_mana_reserve(reactor) - 1e-9 and not urgent:
            score -= 120.0
        if queued_action.payload.get("half_ignore_shield"):
            score -= 15.0
        if code == "quantum_shield":
            # Ion gives the same shield without sacrificing the following round.
            ion = next((skill for skill in reactor.skills if skill.code == "ion_shield"), None)
            if ion is not None and ion.can_react_with_payload(battle, reactor, queued_action, payload)[0]:
                score -= 30.0
        return score
    if code == "block":
        prevention = temporary_defense_prevention_score(battle, reactor, queued_action)
        score = threat + prevention + 20.0
        if proxy_target.current_hp <= max(0.25, threat / 100.0):
            score += 55.0
        return score
    if code == "counter":
        expected = estimate_damage(battle, attacker, battle.basic_attack_preview_power(reactor), ignore_shield=False, half_ignore_shield=False)
        score = expected * 90.0 + hostile_unit_value(attacker) * 0.3
        if expected >= attacker.current_hp - 1e-9:
            score += 80.0
        score -= stone_attack_spawn_penalty(battle, reactor, {"type": "attack", "unit_id": reactor.unit_id,
                                                               "target_unit_id": attacker.unit_id})
        score -= cat_retaliation_action_penalty(battle, reactor, [attacker])
        return score
    if code == "beetle_armor_deploy":
        return beetle_armor_reaction_score(battle, reactor, queued_action, profile)
    if code == "evasion":
        destination = payload_destination(payload)
        if destination is None:
            return -5.0
        if destination_still_in_queued_target_area(battle, reactor, destination, queued_action):
            return -50.0
        return threat + score_move_destination(battle, reactor, destination, hero_style(reactor), profile) / 2.0 + 18.0
    if code == "backstep_shot":
        destination = payload_destination(payload)
        if destination is None:
            return -5.0
        score = threat + score_move_destination(battle, reactor, destination, hero_style(reactor), profile) / 2.0 + 12.0
        if destination_still_in_queued_target_area(battle, reactor, destination, queued_action):
            score -= threat + 30.0
        if payload.get("target_unit_id"):
            expected = estimate_damage(battle, attacker, battle.basic_attack_preview_power(reactor))
            score += expected * 85.0 + profile.aggressive_bonus
        return score
    if code == "shadow_counter":
        destination = payload_destination(payload)
        if destination is None:
            return -1000.0
        skill = reactor.get_skill(code)
        cells = skill.affected_cells(battle, reactor.position) if reactor.position else []
        marks = 0
        with ai_probe_rollback(battle):
            for unit in battle.effect_units_at_cells(cells):
                if unit.player_id == reactor.player_id:
                    continue
                ctx = battle.validate_target(reactor, unit, action_name=skill.name, is_skill=True,
                                             is_hostile=True, ignore_targeting_restrictions=True)
                if not ctx.cancelled:
                    marks += 1
        escape = not destination_still_in_queued_target_area(battle, reactor, destination, queued_action)
        return (threat + 25.0 if escape else -35.0) + marks * 24.0 + score_move_destination(battle, reactor, destination, hero_style(reactor), profile) * 0.25 - 12.0
    if code == "card_transposition":
        destination = payload_destination(payload)
        if destination is None:
            return -5.0
        if destination_still_in_queued_target_area(battle, reactor, destination, queued_action):
            return -1000.0
        return threat + score_move_destination(battle, reactor, destination, hero_style(reactor), profile) / 2.0 + 22.0
    if code == "knockback":
        score = threat * 0.8 + 18.0
        adjacent_units = [
            unit
            for unit in battle.all_units()
            if unit.unit_id != reactor.unit_id
            and unit.position is not None
            and distance_between_units(battle, reactor, unit) <= 1
        ]
        nearby_enemies = [unit for unit in adjacent_units if unit.player_id != reactor.player_id]
        nearby_allies = [unit for unit in adjacent_units if unit.player_id == reactor.player_id]
        future_melee_value = sum(
            estimate_damage(battle, enemy, reactor.stat("attack")) * 55.0
            for enemy in nearby_enemies
        )
        score -= min(110.0, future_melee_value)
        score -= len(nearby_allies) * 12.0
        urgent = proxy_target.current_hp <= max(0.25, threat / 100.0)
        if reactor.current_mana <= 1.0 + 1e-9 and not urgent:
            score -= 55.0
        return score
    if code == "boxer_block_counter":
        expected = estimate_damage(battle, attacker, battle.basic_attack_preview_power(reactor), ignore_shield=False, half_ignore_shield=False)
        prevention = temporary_defense_prevention_score(battle, reactor, queued_action)
        missing_hp = max(0.0, reactor.max_health - reactor.current_hp)
        lifesteal_value = min(0.25, missing_hp) * 100.0 if expected > 0 else 0.0
        score = threat + prevention + expected * 95.0 + lifesteal_value + 36.0
        if expected >= attacker.current_hp - 1e-9:
            score += 90.0
        score -= cat_retaliation_action_penalty(battle, reactor, [attacker])
        return score
    return 0.0


def temporary_defense_prevention_score(
    battle: Battle,
    reactor: Unit,
    queued_action: QueuedAction,
) -> float:
    before = incoming_threat_score(battle, reactor, queued_action)
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
        after = incoming_threat_score(battle, probe_reactor, queued_action)
    return max(0.0, before - after)


def destination_still_in_queued_target_area(
    battle: Battle,
    reactor: Unit,
    destination: Position,
    queued_action: QueuedAction,
) -> bool:
    if not queued_action.target_cells:
        return False
    target_keys = {(cell.x, cell.y) for cell in queued_action.target_cells}
    return any((cell.x, cell.y) in target_keys for cell in battle.unit_cells_at(reactor, destination))


def taunt_control_score(
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
    cells = skill_effect_cells(battle, actor, skill, payload)
    for unit in enemies:
        if unit.magic_immunity:
            continue
        attack_power = skill_attack_power(battle, actor, skill, payload, unit, cells)
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
        score += damage * 100.0 + hostile_unit_value(unit) * 0.45
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
        incoming = estimate_damage(battle, actor, unit.stat("attack"))
        control_value = 42.0 + unit.attack_actions_per_turn() * 16.0 + active_skills * 9.0
        if code == "cat_taunt_roar":
            control_value += min(1.0, unit.current_mana) * 18.0
            control_value -= incoming * 55.0
        else:
            counter_damage = estimate_damage(battle, unit, actor.stat("attack"))
            control_value += counter_damage * 70.0
            control_value -= incoming * 45.0
        score += control_value
    for unit in allies:
        if unit.magic_immunity:
            continue
        attack_power = skill_attack_power(battle, actor, skill, payload, unit, cells)
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
        score -= friendly_fire_penalty(unit) + damage * 120.0
        if damage < unit.current_hp - 1e-9:
            score -= unit.attack_actions_per_turn() * 22.0
    if code == "ring_taunt":
        score += len(enemies) * 10.0
    return score + profile.aggressive_bonus


def cycle_summon_score(
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
    nearest = min(distance_to_position(battle, enemy, destination) for enemy in enemies)
    future_attack_reach = sum(
        1
        for enemy in enemies
        if distance_to_position(battle, enemy, destination) <= 6
    )
    exposed_threat = sum(
        max(0.0, enemy.stat("attack") - 2.0)
        for enemy in enemies
        if distance_to_position(battle, enemy, destination)
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


def sphinx_cannon_score(battle: Battle, actor: Unit, payload: dict[str, Any]) -> float:
    destination = payload_destination(payload)
    if destination is None:
        return -10.0
    enemies = [unit for unit in battle.enemy_units(actor.player_id) if unit.alive and unit.position is not None and not unit.banished]
    if not enemies:
        return 12.0
    distances = [distance_to_position(battle, enemy, destination) for enemy in enemies]
    # A remote 3*3 pattern is legal when at least one of its cells is within range,
    # so an enemy up to two cells beyond range can still be covered by its edge.
    in_artillery_reach = sum(1 for distance in distances if distance <= 9)
    nearest = min(distances)
    safety = min(5.0, float(nearest)) * 6.0
    danger_penalty = 36.0 if nearest <= 1 else (12.0 if nearest == 2 else 0.0)
    future_area_value = best_future_artillery_area_value(battle, actor, destination)
    existing_cannons = [
        unit
        for unit in battle.player_units(actor.player_id)
        if str(getattr(unit, "hero_code", "")) == "sphinx_cannon" and unit.alive and unit.position is not None
    ]
    spacing = min((distance_to_position(battle, cannon, destination) for cannon in existing_cannons), default=3)
    diversification = min(3.0, float(spacing)) * 4.0 if existing_cannons else 0.0
    return 42.0 + in_artillery_reach * 16.0 + safety - danger_penalty + future_area_value + diversification


def best_future_artillery_area_value(battle: Battle, actor: Unit, destination: Position) -> float:
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
                    value -= friendly_fire_penalty(unit) * 0.16
                else:
                    value += 18.0 + hostile_unit_value(unit) * 0.12 + (hit_count - 1) * 12.0
                    if ai_terrain_unit(unit):
                        value += 28.0
            wall_hits = sum((cell.x, cell.y) in battle.blocked_cells for cell in cells)
            value += min(3, wall_hits) * 8.0
            best = max(best, value)
    return best


def ai_terrain_unit(unit: Unit) -> bool:
    return bool(
        getattr(unit, "world_seed_terrain", False)
        or getattr(unit, "is_terrain", False)
        or getattr(unit, "role", "") == "地形单位"
    )


def area_escape_reaction_available(
    battle: Battle,
    actor: Unit,
    skill: Any,
    payload: dict[str, Any],
    target: Unit,
    cells: list[Position],
) -> bool:
    """Whether a target can currently leave an entire declared area by reaction.

    This is intentionally based on public, legal reaction options.  It does not
    predict which option the opponent will choose, but lets irreversible area
    actions discount a defeat that the opponent can already avoid.
    """
    if not cells:
        return False
    try:
        queued_action = battle.build_queued_action(payload)
    except (ActionError, KeyError, TypeError, ValueError):
        return False
    cell_keys = {(cell.x, cell.y) for cell in cells}
    for option in battle.available_reaction_options(target, queued_action):
        if str(option.action_code) not in AREA_ESCAPE_REACTION_CODES:
            continue
        preview = option.preview if isinstance(option.preview, dict) else {}
        for destination in preview_positions(preview.get("cells")):
            footprint = target.footprint_cells_at(destination)
            if footprint and all((cell.x, cell.y) not in cell_keys for cell in footprint):
                return True
    return False


def lethal_counter_reaction_available(battle: Battle, actor: Unit, payload: dict[str, Any]) -> bool:
    """A currently legal counter can defeat the caster before the action resolves.

    Probe the real counter resolution so existing shields, immunity and survival
    effects matter. A speed-2 shield the caster could otherwise cast cannot be
    assumed to answer a speed-2 counter. The probe leaves no state or log behind.
    """
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


def solar_judgment_score(
    battle: Battle,
    actor: Unit,
    skill: Any,
    payload: dict[str, Any],
    profile: DifficultyProfile,
) -> float:
    if lethal_counter_reaction_available(battle, actor, payload):
        return -1000.0
    cells = skill_effect_cells(battle, actor, skill, payload)
    affected = skill_effect_units(battle, actor, skill, payload)
    enemies = [unit for unit in affected if unit.player_id != actor.player_id]
    allies = [unit for unit in affected if unit.player_id == actor.player_id]
    score = skill_damage_score(battle, actor, skill, payload, profile)

    def projected_damage(target: Unit) -> float:
        attack_power = skill_attack_power(battle, actor, skill, payload, target, cells)
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
        if area_escape_reaction_available(battle, actor, skill, payload, target, cells)
    ]
    for target in escapable_enemies:
        damage = projected_damage(target)
        score -= damage * 85.0 + hostile_unit_value(target) * 0.25
        if damage >= target.current_hp - 1e-9:
            score -= 70.0

    reliable_enemy_defeats = [
        target
        for target in enemies
        if target not in escapable_enemies and projected_damage(target) >= target.current_hp - 1e-9
    ]
    allied_defeats = [target for target in allies if projected_damage(target) >= target.current_hp - 1e-9]
    if allied_defeats:
        enemy_loss_value = sum(hostile_unit_value(target) for target in reliable_enemy_defeats)
        ally_loss_value = sum(ally_unit_value(target) for target in allied_defeats)
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
        if not ai_terrain_unit(unit):
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
        attack_power = skill_attack_power(battle, actor, skill, payload, target, cells)
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
            and hostile_unit_value(target) >= 90.0
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


def iron_chain_path_score(
    battle: Battle,
    actor: Unit,
    skill: Any,
    payload: dict[str, Any],
    profile: DifficultyProfile,
) -> float:
    score = skill_damage_score(battle, actor, skill, payload, profile)
    targets = [unit for unit in skill_effect_units(battle, actor, skill, payload) if unit.player_id != actor.player_id]
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
        score += score_move_destination(battle, actor, destination, hero_style(actor), profile) * 0.45
        exposure = 0.0
        for enemy in living_hostile_combatants(battle, actor.player_id):
            if distance_to_position(battle, enemy, destination) > enemy.normal_move_distance() + enemy.targeting_range():
                continue
            expected_hit = battle.damage_rule.calculate_damage(enemy.stat("attack"), actor.stat("defense"))
            exposure += expected_hit * 75.0 + hostile_unit_value(enemy) * 0.06
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
    score += max((hostile_unit_value(unit) for unit in targets), default=0.0) * 0.2
    if getattr(skill, "uses_this_turn", 0) == 0:
        score += 12.0
    return score


def reviewed_attack_synergy(battle: Battle, actor: Unit, target: Unit, damage: float) -> float:
    score = 0.0
    if actor.hero_code == "excel_r027" and 0 < damage < target.current_hp and not target.direct_effects_blocked():
        lock = target.get_status("无常普攻封锁")
        if lock is not None:
            score += max(0, 2 - (lock.duration or 0)) * 12.0
        else:
            score += (0.0 if target.cannot_attack else target.attack_actions_per_turn() * 32.0)
            if not target.cannot_use_skills:
                score += min(4, len(target.skills)) * 18.0
    if actor.has_status("终结") and damage > 0 and not target.is_clone and not actor.cannot_heal:
        recovery = min(0.25, max(0.0, actor.max_health - actor.current_hp))
        score += recovery * 160.0 + (65.0 if recovery > 0 and actor.current_hp <= 0.25 else 0.0)
    if actor.hero_code == "excel_r023" and damage > 0 and not actor.cannot_heal:
        score += min(0.25, max(0.0, actor.max_health - actor.current_hp)) * 140.0
    if actor.hero_code == "undead_king_lina":
        reward = next((trait for trait in actor.traits if trait.name == "击破重置"), None)
        if reward is not None and not reward.used_this_turn and reward._eligible_target(target) and damage >= target.current_hp:
            score += 55.0 + min(target.current_mana, max(0.0, actor.max_mana() - actor.current_mana)) * 18.0
        lock = next((trait for trait in actor.traits if trait.name == "执念目标"), None)
        remaining = max(1, actor.attack_actions_per_turn() - actor.attacks_used)
        if lock is not None and not lock.locked_target_id and damage * remaining < target.current_hp:
            score -= 28.0
    if actor.hero_code == "doomlight_dragon" and damage > 0 and not target.has_status("末日光"):
        score += min(target.current_hp, 1.0) * 32.0
    if target.hero_code == "doomlight_dragon" and not actor.has_status("末日光"):
        score -= min(actor.current_hp, 1.0) * 55.0
    return score


def sandstorm_value(battle: Battle, actor: Unit) -> float:
    score = 0.0
    for unit in battle.all_units():
        if not unit.alive or unit.banished or battle.unit_in_weather("沙尘", unit):
            continue
        value = (0.0 if unit.attribute == "土" else (0.125 if unit.has_flying else 0.0625) * 100.0)
        if unit.is_stealthed():
            value += 22.0
        score += value if unit.player_id != actor.player_id else -value
    if actor.hero_code == "undead_king_lina" and not battle.unit_in_weather("沙尘", actor):
        with ai_probe_rollback(battle):
            hp_before = actor.current_hp
            battle.heal(HealContext(source=actor, target=actor, amount=0.25, action_name="AI沙尘恢复估值"))
            score += max(0.0, actor.current_hp - hp_before) * 80.0
        score += min(1.0, max(0.0, actor.max_mana() - actor.current_mana)) * 16.0
    return score


def resource_effect_score(battle: Battle, actor: Unit, skill: Any, payload: dict[str, Any], profile: DifficultyProfile) -> float:
    """Price actual effect outcomes, paid HP and consumed body cells without running reactions."""
    code = skill.code
    units = list(battle.all_units())
    if code == "rock_cannon":
        try:
            selected, direction, _ = skill.validate_selection(battle, actor, payload)
            impact_cells = [cell for center in skill.impact_positions(battle, actor, selected, direction)
                            for cell in (Position(center.x + dx, center.y + dy) for dx in (-1, 0, 1) for dy in (-1, 0, 1))]
            if not any(unit.player_id != actor.player_id for unit in battle.units_at_cells(impact_cells)):
                return -1000.0
        except ActionError:
            return -1000.0
    weights = {"attack": 18.0, "defense": 17.0, "speed": 10.0, "attack_range": 10.0, "mana": 14.0}
    before = {unit.unit_id: (unit.current_hp, unit.alive, unit.total_shields(), unit.has_status("末日光"),
                            {stat: unit.stat(stat) for stat in weights}, unit.current_mana) for unit in units}
    with ai_probe_rollback(battle):
        try:
            probe = dict(payload)
            probe.update(skill.queued_payload_metadata(battle, actor, probe))
            if code == "apocalypse":
                skill.prepay_resources(battle, actor, probe)
            if code == "rock_cannon":
                battle.pending_followup_actions = deque()
            skill.execute(battle, actor, probe)
            if code == "rock_cannon":
                while battle.pending_followup_actions:
                    battle.resolve_skill_effect(actor, battle.pending_followup_actions.popleft())
        except (ActionError, ValueError, KeyError):
            return -1000.0
        if not actor.alive:
            return -1000.0
        score = -8.0
        new_doom_damage = 0.0
        for unit in units:
            hp, alive, shields, had_doom, stats, mana = before[unit.unit_id]
            friendly = unit.player_id == actor.player_id
            sign = 1.0 if friendly else -1.0
            score += (unit.current_hp - hp) * sign * (135.0 if friendly else 110.0)
            if alive and not unit.alive:
                score += -240.0 if friendly else 160.0
            score += (unit.total_shields() - shields) * sign * 8.0
            if code == "rock_absorb":
                score += sum((unit.stat(stat) - stats[stat]) * weight * sign for stat, weight in weights.items())
                score += (unit.current_mana - mana) * sign * 14.0
            if unit.alive and not had_doom and unit.has_status("末日光"):
                expected_loss = unit.current_hp * (1.0 - 0.5 ** 4)
                score += expected_loss * (-120.0 if friendly else 85.0)
                if not friendly:
                    new_doom_damage += expected_loss
        if code == "doom_light" and new_doom_damage == 0:
            return -1000.0
        if actor.hero_code == "doomlight_dragon" and new_doom_damage > 0:
            hp = actor.current_hp
            battle.heal(HealContext(source=actor, target=actor, amount=0.25, action_name="AI吸收估值"))
            if actor.current_hp > hp:
                score += new_doom_damage * 35.0
            actor.current_hp = hp
        if code == "rock_cannon":
            score -= len(payload.get("cells", [])) * 12.0
            if len(battle.unit_cells(actor)) == 1:
                score -= 12.0
        enemies = living_hostile_combatants(battle, actor.player_id)
        if code == "apocalypse" and enemies and actor.current_hp <= 0.5:
            score -= 85.0
        if skill.max_uses_per_battle == 1 and score < profile.once_per_battle_threshold:
            score -= 24.0
        return score


def blood_support_score(battle: Battle, actor: Unit, skill: Any, payload: dict[str, Any], profile: DifficultyProfile) -> float:
    if skill.code == "sacrifice_ritual":
        try:
            target = skill.candidate_by_payload(battle, payload)
            destination = payload_destination(payload)
        except ActionError:
            return -1000.0
        if target.player_id != actor.player_id or destination is None:
            return -1000.0
        enemies = living_hostile_combatants(battle, actor.player_id)
        exposed = sum(1 for enemy in enemies if distance_to_position(battle, enemy, destination) <= enemy.targeting_range())
        value = (65.0 if not target.is_summon else 30.0) + min(40.0, target.stat("attack") * target.attack_actions_per_turn() * 5.0) + min(20.0, target.max_mana() * 4.0)
        return value - exposed * 35.0 - (45.0 if actor.mana_points >= 8 else 12.0)
    target = battle.units.get(str(payload.get("target_unit_id") or ""))
    if target is None or target.player_id != actor.player_id:
        return -1000.0
    if skill.code != "blood_dance" and any(status.name == skill.name and getattr(status, "source_unit_id", None) == actor.unit_id for status in target.statuses):
        return -1000.0
    units = list(dict.fromkeys([actor, target])) if skill.code == "blood_dance" else [target]
    before = {unit.unit_id: (unit.current_hp, unit.current_mana, unit.stat("attack"), unit.stat("defense")) for unit in units}
    attack_before = best_available_attack_score(battle, target, profile) if skill.code == "blood_art" and target is actor else 0.0
    with ai_probe_rollback(battle):
        try:
            skill.execute(battle, actor, payload)
        except ActionError:
            return -1000.0
        score = -float(skill.mana_cost) * 16.0
        for unit in units:
            hp, mana, attack, defense = before[unit.unit_id]
            score += (unit.current_hp - hp) * 180.0 + (unit.current_mana - mana) * 30.0
            nearby = [enemy for enemy in living_hostile_combatants(battle, actor.player_id) if distance_between_units(battle, unit, enemy) <= unit.targeting_range() + unit.normal_move_distance()]
            score += max(0.0, unit.stat("attack") - attack) * min(3, unit.attack_actions_per_turn()) * (26.0 if nearby else 5.0)
            score += max(0.0, unit.stat("defense") - defense) * (45.0 if nearby else 5.0)
        attack_after = best_available_attack_score(battle, actor, profile) if skill.code == "blood_art" and target is actor else 0.0
    if attack_after > attack_before + 1.0:
        score = max(score, attack_after + 25.0)
    if skill.code == "blood_dance":
        for unit in units:
            if unit.position is not None and not unit.cannot_move and unit.remaining_normal_move_distance(battle) > 0:
                current = score_move_destination(battle, unit, unit.position, hero_style(unit), profile)
                cells = battle.reachable_positions(unit, max_distance=unit.remaining_normal_move_distance(battle), use_movement_cost=True)
                gain = max((score_move_destination(battle, unit, cell, hero_style(unit), profile) - current for cell in cells), default=0.0)
                if gain > 8.0 and before[unit.unit_id][0] > 0.25:
                    score -= 80.0 + gain
    return score


def li_attack_plan_value(battle: Battle, actor: Unit) -> float:
    remaining = max(0, actor.attack_actions_per_turn() - actor.attacks_used)
    essence = actor.get_status("精华")
    charges = min(remaining, getattr(essence, "charges", 0))
    candidates = []
    for enemy in living_hostile_combatants(battle, actor.player_id):
        if distance_between_units(battle, actor, enemy) > actor.targeting_range() + actor.remaining_normal_move_distance(battle) or enemy.physical_immunity:
            continue
        normal = estimate_damage(battle, enemy, actor.stat("attack"))
        piercing = estimate_damage(battle, enemy, actor.stat("attack"), ignore_shield=True)
        value = min(enemy.current_hp, normal * (remaining - charges) + piercing * charges) * 110.0
        candidates.append(value)
    return max(candidates, default=0.0)


def li_area_skill_score(battle: Battle, actor: Unit, skill: Any, payload: dict[str, Any], profile: DifficultyProfile) -> float:
    targets = skill_effect_units(battle, actor, skill, payload)
    before = {unit.unit_id: (unit.current_hp, unit.alive, unit.position) for unit in dict.fromkeys([actor, *targets])}
    with ai_probe_rollback(battle):
        try:
            probe = dict(payload)
            probe.update(skill.queued_payload_metadata(battle, actor, probe))
            skill.execute(battle, actor, probe)
        except ActionError:
            return -1000.0
        score = -skill.mana_cost * 20.0
        for unit in dict.fromkeys([actor, *targets]):
            hp, alive, position = before[unit.unit_id]
            sign = 1.0 if unit.player_id != actor.player_id else -1.0
            score += sign * (hp - unit.current_hp) * (120.0 if sign > 0 else 180.0)
            if alive and not unit.alive:
                score += sign * 110.0
            if skill.code == "chain_pull" and unit.position != position and unit.position is not None:
                score += sign * 45.0
                if sign > 0:
                    score += min(unit.current_hp, estimate_damage(battle, unit, actor.stat("attack")) * max(0, actor.attack_actions_per_turn() - actor.attacks_used)) * 100.0
        if skill.max_uses_per_battle == 1 and score < profile.once_per_battle_threshold:
            score -= 35.0
        return score


def chanter_card_score(battle: Battle, actor: Unit, skill: Any, payload: dict[str, Any], profile: DifficultyProfile) -> float:
    from wujiang.tactical.heroes.common import is_mana_drain_immune
    targets = [unit for unit in skill_effect_units(battle, actor, skill, payload) if unit.player_id != actor.player_id]
    if skill.code == "magic_claw":
        before = {unit.unit_id: unit.cannot_move for unit in targets}
        with ai_probe_rollback(battle):
            probe = dict(payload)
            probe.update(skill.queued_payload_metadata(battle, actor, probe))
            skill.execute(battle, actor, probe)
            return sum(55.0 for unit in targets if unit.cannot_move and not before[unit.unit_id]) - 24.0
    card_type = skill.card_type
    score = -20.0
    for target in targets:
        if target.magic_immunity:
            continue
        if card_type == "paralysis":
            covered = any(getattr(effect, "card_type", None) == "paralysis" and getattr(effect, "owner_player_id", None) != target.player_id and set(battle.unit_cells(target)).intersection(effect.affected_cells(battle)) for effect in battle.field_effects)
            if not covered and not target.cannot_use_skills:
                score += 30.0 + min(4, len(target.skills)) * 10.0
        elif card_type == "drain":
            if not is_mana_drain_immune(target):
                score += min(1.0, target.current_mana) * (38.0 + (16.0 if actor.current_mana < actor.max_mana() else 0.0))
        else:
            with ai_probe_rollback(battle):
                ctx = battle.resolve_damage(DamageContext(source=actor, target=target, attack_power=2, is_skill=True, action_name="毒牌", ignore_shield=True, from_field_effect=True))
                score += ctx.actual_damage * (150.0 if target.cannot_move else 100.0) + (45.0 if ctx.destroyed_as_clone else 0.0)
    destination = payload_destination(payload)
    enemies = living_hostile_combatants(battle, actor.player_id)
    own_cards = [effect for effect in battle.field_effects if getattr(effect, "owner_unit_id", None) == actor.unit_id and hasattr(effect, "card_type")]
    if destination is not None and actor.position is not None and enemies and actor.current_mana >= 1.5 and battle.can_place_unit(actor, destination, ignore=actor, mover=actor):
        current_distance = min(distance_between_units(battle, actor, enemy) for enemy in enemies)
        safe_distance = min(distance_to_position(battle, enemy, destination) for enemy in enemies)
        has_escape = any(min(distance_to_position(battle, enemy, card.center) for enemy in enemies) >= current_distance + 2 and battle.can_place_unit(actor, card.center, ignore=actor, mover=actor) for card in own_cards)
        if current_distance <= 3 and safe_distance >= current_distance + 2 and not has_escape:
            score += 65.0
    if actor.current_mana < 1.5:
        score -= 25.0
    return score


def perfect_swordsman_skill_score(battle: Battle, actor: Unit, skill: Any, payload: dict[str, Any], profile: DifficultyProfile) -> float:
    if skill.code == "focus_reset":
        stop = next((item for item in actor.skills if item.code == "time_stop"), None)
        return 65.0 if stop is not None and stop.uses_this_battle > 0 and actor.mana_points >= 3 else -1000.0
    if skill.code == "iaido_charge":
        if actor.has_status("聚气。拔刀斩"):
            return -1000.0
        best_attack = best_available_attack_score(battle, actor, profile)
        remaining = max(0, actor.attack_actions_per_turn() - actor.attacks_used)
        if best_attack > 0:
            threats = [enemy for enemy in living_hostile_combatants(battle, actor.player_id)
                       if distance_between_units(battle, actor, enemy) <= enemy.normal_move_distance() + enemy.targeting_range()
                       and not battle.attack_ignores_shield(enemy, actor)]
            preventable = sorted((estimate_attack_damage(battle, enemy, actor, {}, attack_power=enemy.stat("attack"))
                                  for enemy in threats), reverse=True)[:remaining]
            can_kill_now = any(battle.attack_target_allowed(actor, enemy)[0]
                               and estimate_attack_damage(battle, actor, enemy, {}, attack_power=actor.stat("attack")) >= enemy.current_hp
                               for enemy in threats)
            if remaining and sum(preventable) >= actor.current_hp > 0 and not can_kill_now:
                return best_attack + 35.0
            return -1000.0
        nearby = any(distance_between_units(battle, actor, enemy) <= 4 for enemy in living_hostile_combatants(battle, actor.player_id))
        return 30.0 + remaining * (12.0 if nearby else 2.0) + (18.0 if actor.mana_points < 3 else 0.0)
    if skill.code == "ghost_step":
        destination = payload_destination(payload)
        if destination is None or actor.position is None:
            return -1000.0
        style = hero_style(actor)
        gain = score_move_destination(battle, actor, destination, style, profile) - score_move_destination(battle, actor, actor.position, style, profile)
        before = best_available_attack_score(battle, actor, profile)
        with ai_probe_rollback(battle):
            try:
                skill.execute(battle, actor, payload)
            except ActionError:
                return -1000.0
            gain += max(0.0, best_available_attack_score(battle, actor, profile) - before)
        return gain - 22.0 - (20.0 if actor.current_mana <= 1 else 0.0)
    if skill.code == "time_stop":
        if not skill.condition(battle, actor)[0]:
            return -1000.0
        active = battle.current_turn_unit()
        if active is None:
            return -1000.0
        attacks = max(0, active.attack_actions_per_turn() - active.attacks_used)
        skills_left = sum(1 for item in active.skills if item.timing == "active" and item.can_use(battle, active, {})[0])
        retaliation = sum(estimate_damage(battle, enemy, 5 if actor.has_status("聚气。拔刀斩") else actor.stat("attack")) * 45.0
                          for enemy in living_hostile_combatants(battle, actor.player_id) if distance_between_units(battle, actor, enemy) <= 3)
        return min(3, attacks) * 28.0 + min(3, skills_left) * 12.0 + retaliation - 45.0
    return -1000.0


def reviewed_r06_skill_score(battle: Battle, actor: Unit, skill: Any, payload: dict[str, Any],
                             profile: DifficultyProfile) -> float:
    code = str(skill.code)
    if code == "extra_stealth":
        if actor.is_stealthed() or best_available_attack_score(battle, actor, profile) > 0:
            return -1000.0
        threats = living_hostile_combatants(battle, actor.player_id)
        return 42.0 if any(distance_between_units(battle, actor, enemy) <= enemy.normal_move_distance() + enemy.targeting_range() for enemy in threats) else -20.0
    if code == "smoke_spray":
        cells = skill.chosen_cells(battle, actor, payload)
        keys = {(cell.x, cell.y) for cell in cells}
        score = -20.0
        for unit in battle.all_units():
            if not unit.alive or unit.banished or unit.position is None:
                continue
            body = battle.effect_recipient(unit)
            if not any((cell.x, cell.y) in keys for cell in battle.unit_cells(body)):
                continue
            if any(effect.name == "喷烟" and effect.unit_in_area(battle, unit) for effect in battle.field_effects):
                continue
            allied = unit.player_id == actor.player_id
            current = battle.unit_belongs_to_current_turn(unit)
            attacks = max(0, unit.attack_actions_per_turn() - (unit.attacks_used if current else 0))
            active = sum(candidate.timing == "active" and candidate.code != "smoke_spray"
                         and candidate.cooldown_remaining == 0
                         and (not current or candidate.max_uses_per_turn is None or candidate.uses_this_turn < candidate.max_uses_per_turn)
                         and unit.current_mana >= candidate.mana_cost for candidate in unit.skills)
            denied = (0 if unit.cannot_attack else attacks * 24.0) + min(3, active) * 15.0
            if denied <= 0:
                continue
            escape = False
            if not unit.cannot_move and not unit.cannot_normal_move:
                distance = unit.remaining_normal_move_distance(battle) if current else unit.normal_move_distance()
                escape = any(not any((cell.x, cell.y) in keys for cell in battle.unit_cells_at(body, dest))
                             for dest in battle.reachable_positions(body, max_distance=distance))
            value = denied * (0.55 if escape else 1.0) + 8.0
            score += -value * 1.25 if allied else value
        return score
    if code == "premature_burial":
        target = battle.units.get(str(payload.get("target_unit_id") or ""))
        if target is None or target.player_id == actor.player_id:
            return -1000.0
        from wujiang.tactical.heroes.next_five import erasure_counter_count
        target = battle.effect_recipient(target)
        before = erasure_counter_count(target, actor.unit_id)
        with ai_probe_rollback(battle):
            skill.execute(battle, actor, payload)
            if erasure_counter_count(target, actor.unit_id) <= before:
                return -1000.0
        finish = 35.0 if (before + 1) * 0.25 >= target.current_hp else 0.0
        anchor = 20.0 if erasure_counter_count(target) == 0 and actor.current_mana >= 1 else 0.0
        return 30.0 + finish + anchor
    if code == "descent_moment":
        target = battle.units.get(str(payload.get("target_unit_id") or ""))
        if target is None or target.player_id == actor.player_id:
            return -1000.0
        recipient = battle.effect_recipient(target)
        hp, mana, alive = recipient.current_hp, actor.current_mana, recipient.alive
        with ai_probe_rollback(battle):
            try:
                skill.execute(battle, actor, payload)
                for _ in range(2):
                    if not target.alive or not recipient.alive:
                        break
                    attack = {"target_unit_id": target.unit_id}
                    if not battle.attack_target_allowed(actor, target, payload=attack)[0]:
                        break
                    battle.basic_attack_with_payload(actor, target, attack)
            except ActionError:
                return -1000.0
            damage = hp - recipient.current_hp
            if damage <= 0:
                return -1000.0
            danger = sum(18.0 for enemy in living_hostile_combatants(battle, actor.player_id)
                         if distance_between_units(battle, actor, enemy) <= enemy.targeting_range() and enemy.unit_id != target.unit_id)
            return damage * 135.0 + (110.0 if alive and not recipient.alive else 0.0) + max(0.0, actor.current_mana - mana) * 24.0 - 25.0 - danger - (30.0 if mana < 1.5 else 0.0)
    units = skill.targets(battle, actor) if code == "erasure" else skill.get_target_units_for_payload(battle, actor, payload)
    before = {unit.unit_id: (unit.current_hp, unit.alive, unit.stat("speed"), unit.position, unit.total_shields()) for unit in units}
    owner_hp, owner_mana = actor.current_hp, actor.current_mana
    with ai_probe_rollback(battle):
        try:
            probe = dict(payload)
            probe.update(skill.queued_payload_metadata(battle, actor, probe))
            skill.execute(battle, actor, probe)
        except ActionError:
            return -1000.0
        score = -12.0 if code == "erasure" else -4.0
        for unit in units:
            hp, alive, speed, position, shields = before[unit.unit_id]
            hostile = unit.player_id != actor.player_id
            sign = 1.0 if hostile else -1.6
            value = (hp - unit.current_hp) * 130.0 + (shields - unit.total_shields()) * 8.0
            if alive and not unit.alive:
                value += 115.0
            if code == "dragon_slash" and unit.alive:
                value += max(0.0, speed - unit.stat("speed")) * 15.0
                if unit.position != position:
                    value += 25.0 + (15.0 if actor.attacks_used < actor.attack_actions_per_turn() else 0.0)
            score += sign * value
        score += max(0.0, actor.current_mana - owner_mana) * 24.0
        score -= max(0.0, owner_hp - actor.current_hp) * 150.0
        return score


def magnetic_wave_score(battle: Battle, actor: Unit, skill: Any, payload: dict[str, Any]) -> float:
    units = skill_effect_units(battle, actor, skill, payload)
    before = {unit.unit_id: (unit.current_hp, unit.current_mana, unit.turn_ready, unit.alive, unit.total_shields()) for unit in units}
    with ai_probe_rollback(battle):
        try:
            probe = dict(payload)
            probe.update(skill.queued_payload_metadata(battle, actor, probe))
            skill.execute(battle, actor, probe)
        except (ActionError, ValueError):
            return -1000.0
        score = -40.0  # Two mana points could instead become two mana guards.
        for unit in units:
            hp, mana, ready, alive, shields = before[unit.unit_id]
            hostile = unit.player_id != actor.player_id
            sign = 1.0 if hostile else -1.0
            score += sign * ((hp - unit.current_hp) * (110.0 if hostile else 160.0) + (mana - unit.current_mana) * 16.0 + (shields - unit.total_shields()) * 8.0)
            if alive and not unit.alive:
                score += sign * 160.0
            if ready and not unit.turn_ready and unit.alive and battle.unit_belongs_to_current_turn(unit):
                attacks = max(0, unit.attack_actions_per_turn() - unit.attacks_used)
                score += sign * (25.0 + min(4, attacks) * 18.0 + (15.0 if unit.remaining_normal_move_distance(battle) > 0 else 0.0))
        return score


def masamune_attack_plan_value(battle: Battle, actor: Unit, profile: DifficultyProfile) -> float:
    best = 0.0
    remaining = max(0, actor.attack_actions_per_turn() - actor.attacks_used)
    for action in battle.action_snapshot_for(actor).get("actions", []):
        if action.get("kind") != "attack" or not action.get("available"):
            continue
        for payload in attack_payloads_for_action(battle, actor, action):
            if not payload_is_legal(battle, payload):
                continue
            target = battle.get_unit(payload["target_unit_id"])
            resolved = battle.resolved_basic_attack_payload(actor, payload)
            damage = estimate_attack_damage(battle, actor, target, payload, attack_power=battle.basic_attack_preview_power(actor, payload))
            total = min(target.current_hp, damage * (remaining // max(1, int(resolved.get("attack_cost", 1)))))
            best = max(best, total * 100.0 + (95.0 if total >= target.current_hp - 1e-9 else 0.0))
    return best


def allied_attack_heal_score(battle: Battle, actor: Unit, payload: dict[str, Any]) -> float:
    target = battle.units.get(str(payload.get("target_unit_id") or ""))
    if target is None:
        return 0.0
    amount = battle.allied_basic_heal_amount(actor, target)
    if amount <= 0:
        return 0.0
    with ai_probe_rollback(battle):
        before = target.current_hp
        battle.heal(HealContext(source=actor, target=target, amount=amount, action_name="攻击己方加血"))
        gained = target.current_hp - before
    if gained <= 0:
        return -1000.0
    threat = max((estimate_damage(battle, target, enemy.stat("attack"))
                  for enemy in living_hostile_combatants(battle, actor.player_id)
                  if distance_between_units(battle, target, enemy) <= enemy.targeting_range() + enemy.normal_move_distance()), default=0.0)
    saves = target.current_hp <= threat < target.current_hp + gained
    return gained * 150.0 + (90.0 if saves else 0.0)


def fried_aura_position_value(battle: Battle, actor: Unit, destination: Position) -> float:
    from wujiang.tactical.heroes.excel_roster import FriedAuraTrait
    auras = [trait for unit in battle.all_units() for trait in unit.traits if isinstance(trait, FriedAuraTrait)]
    if not auras:
        return 0.0
    old = actor.position
    actor.position = destination
    try:
        recipients = [unit for unit in battle.player_units(actor.player_id) if unit.is_clone or unit.is_summon] if any(aura.owner is actor for aura in auras) else [actor]
        return sum(12.0 + max((sum(max(0.0, aura.owner.stat(stat) - getattr(aura.owner.base_stats, stat))
                                  for stat in ("attack", "defense", "speed", "attack_range"))
                              for aura in auras if aura.covers(battle, unit)), default=0.0) * 5.0
                   for unit in recipients if any(aura.covers(battle, unit) for aura in auras))
    finally:
        actor.position = old


def fried_skill_score(battle: Battle, actor: Unit, skill: Any, payload: dict[str, Any], profile: DifficultyProfile) -> float:
    if skill.code == "fried_inspire":
        try:
            target = skill.validate_target(battle, actor, payload)
        except ActionError:
            return -1000.0
        if target.has_status("鼓舞") or not target.can_take_turn_actions(battle) or target.cannot_move or target.cannot_normal_move:
            return -1000.0
        from wujiang.tactical.heroes.excel_roster import FriedInspireStatus
        before_cells = battle.reachable_positions(target, max_distance=target.remaining_normal_move_distance(battle), use_movement_cost=True)
        before_value = max((score_move_destination(battle, target, pos, hero_style(target), profile) for pos in before_cells), default=0.0)
        with ai_probe_rollback(battle):
            target.add_status(FriedInspireStatus(), source=actor)
            after_cells = battle.reachable_positions(target, max_distance=target.remaining_normal_move_distance(battle), use_movement_cost=True)
            gain = max((score_move_destination(battle, target, pos, hero_style(target), profile) for pos in after_cells), default=0.0) - before_value
        return gain + 10.0 - skill.mana_cost_for_payload(battle, actor, payload) * 12.0 if gain > 1 else -1000.0
    try:
        (attack, defense, reach), destination = skill.validate_selection(battle, actor, payload)
    except (ActionError, ValueError, TypeError):
        return -1000.0
    enemies = living_hostile_combatants(battle, actor.player_id)
    if not enemies:
        return -1000.0
    from wujiang.tactical.heroes.excel_roster import RoyalSoldierSummon
    with ai_probe_rollback(battle):
        probe = RoyalSoldierSummon(actor.player_id, actor.unit_id, attack, defense, reach)
        probe.position, probe._battle_ref = destination, actor._battle_ref
        best = max(estimate_damage(battle, enemy, probe.stat("attack")) * 100.0
                   + (24.0 if battle.distance_between_units(probe, enemy) <= probe.targeting_range() + probe.normal_move_distance() else 0.0)
                   - max(0.0, battle.distance_between_units(probe, enemy) - probe.targeting_range() - probe.normal_move_distance()) * 8.0
                   for enemy in enemies)
        danger = max((estimate_damage(battle, probe, enemy.stat("attack")) for enemy in enemies
                      if battle.distance_between_units(probe, enemy) <= enemy.targeting_range() + enemy.normal_move_distance()), default=0.0)
        support = 12.0 if battle.unit_can_use_block_counter(probe) else 0.0
        return 30.0 + best + support - danger * 35.0 - skill.mana_cost_for_payload(battle, actor, payload) * 12.0


def frey_skill_score(battle: Battle, actor: Unit, skill: Any, payload: dict[str, Any], profile: DifficultyProfile) -> float:
    """Score the declared ring/rays and the same landing choices exposed to players."""
    try:
        cells = skill_effect_cells(battle, actor, skill, payload)
        destination = skill.validate_selection(battle, actor, payload)[2] if skill.code == "frey_quick_flash" else None
    except (ActionError, ValueError, TypeError, KeyError):
        return -1000.0
    score = 0.0
    for target in battle.effect_units_at_cells(cells, ignore=actor):
        damage = estimate_skill_damage(
            battle, actor, skill, payload, target,
            skill_attack_power(battle, actor, skill, payload, target, cells),
            cells=cells, ignore_shield=skill.ignores_shield_for_payload(battle, actor, payload),
            half_ignore_shield=False,
        )
        loss = min(target.current_hp, damage)
        if loss <= 0:
            continue
        value = loss * 100.0
        if damage >= target.current_hp - 1e-9:
            value += 90.0
        score += value if target.player_id != actor.player_id else -value * 1.35
    if destination is not None and actor.position is not None:
        origin = actor.position
        role = hero_style(actor)
        score += score_move_destination(battle, actor, destination, role, profile) - score_move_destination(battle, actor, origin, role, profile)

        def pressure(position: Position) -> float:
            previous = actor.position
            actor.position = position
            try:
                total = sum(
                    estimate_damage(battle, actor, enemy.stat("attack")) * max(1, enemy.attack_actions_per_turn())
                    for enemy in living_hostile_combatants(battle, actor.player_id)
                    if not enemy.cannot_attack and battle.distance_between_units(actor, enemy) <= enemy.targeting_range()
                )
                return total * 100.0 + (100.0 if total >= actor.current_hp - 1e-9 and total > 0 else 0.0)
            finally:
                actor.position = previous
        score += pressure(origin) - pressure(destination)
        return score - 12.0
    if score <= 0:
        return -1000.0
    return score - (28.0 if skill.max_uses_per_battle is not None else 6.0)


def morning_holy_light_score(battle: Battle, actor: Unit, skill: Any, payload: dict[str, Any]) -> float:
    """Value the declared rectangle after actual defenses and friendly effects."""
    try:
        cells = skill.get_target_cells_for_payload(battle, actor, payload)
    except ActionError:
        return -1000.0
    targets = [target for target in battle.effect_units_at_cells(cells)
               if target.player_id == actor.player_id or not target.is_stealthed()]
    if not targets:
        return -1000.0
    with ai_probe_rollback(battle):
        before = {target.unit_id: (target.current_hp, target.alive, target.total_shields(),
                                   getattr(target.get_status("被动封锁"), "duration", 0) or 0)
                  for target in targets}
        try:
            skill.execute(battle, actor, payload)
        except (ActionError, KeyError, TypeError, ValueError):
            return -1000.0
        score = 0.0
        for target in targets:
            hp, alive, shields, old_lock = before[target.unit_id]
            value = max(0.0, hp - target.current_hp) * 115.0
            value += max(0, shields - target.total_shields()) * 20.0
            if alive and not target.alive:
                value += 90.0
            new_lock = getattr(target.get_status("被动封锁"), "duration", 0) or 0
            if new_lock > old_lock:
                passive_count = sum(candidate.timing == "passive" or candidate.passive
                                    for candidate in target.skills)
                if passive_count:
                    value += (22.0 + min(3, passive_count) * 12.0) * min(1.0, new_lock - old_lock)
            score += value if target.player_id != actor.player_id else -value * 1.5
        score -= skill.mana_cost_for_payload(battle, actor, payload) * 12.0
        return score if score > 0 else -1000.0


def skill_damage_score(
    battle: Battle,
    actor: Unit,
    skill: Any,
    payload: dict[str, Any],
    profile: DifficultyProfile,
) -> float:
    code = str(skill.code)
    affected = skill_effect_units(battle, actor, skill, payload)
    score = 0.0
    cells = skill_effect_cells(battle, actor, skill, payload)
    for unit in affected:
        if unit.player_id == actor.player_id:
            score -= friendly_fire_penalty(unit)
            continue
        if code == "morning_holy_light":
            if unit.attribute == "暗":
                score += min(5.0, unit.current_hp) * 100.0
            score += hostile_unit_value(unit) * 0.5
            continue
        attack_power = skill_attack_power(battle, actor, skill, payload, unit, cells)
        ignore_shield = bool(skill.ignores_shield_for_payload(battle, actor, payload))
        half_ignore_shield = bool(skill.half_ignores_shield_for_payload(battle, actor, payload))
        damage = estimate_skill_damage(
            battle,
            actor,
            skill,
            payload,
            unit,
            attack_power,
            cells=cells,
            ignore_shield=ignore_shield,
            half_ignore_shield=half_ignore_shield,
        )
        score += damage * 100.0
        score += hostile_unit_value(unit) * 0.5
        if damage >= unit.current_hp - 1e-9:
            score += 90.0
            if actor.hero_code == "undead_king_lina":
                reward = next((trait for trait in actor.traits if trait.name == "击破重置"), None)
                if reward is not None and not reward.used_this_turn and reward._eligible_target(unit):
                    score += 55.0
    if code in {"judgment_fire", "great_funeral", "laser", "missile", "machine_gun", "pierce", "large_pierce_plus", "remote_dragon_breath", "dragon_breath", "magnetic_wave", "whirlwind_attack"}:
        score += len([unit for unit in affected if unit.player_id != actor.player_id]) * 18.0
    if hero_style(actor) != "support":
        score += profile.aggressive_bonus
    return score


def skill_control_bonus(
    battle: Battle,
    actor: Unit,
    code: str,
    payload: dict[str, Any],
    targets: list[Unit],
    profile: DifficultyProfile,
    *,
    instant_only: bool,
) -> float:
    enemies = [unit for unit in targets if unit.player_id != actor.player_id]
    if code == "magnetic_wave":
        # The complete damage/control result is priced by magnetic_wave_score.
        return 0.0
    if code == "paralyzing_glove":
        return sum(hostile_unit_value(unit) * 0.6 for unit in enemies) + 45.0
    if code == "curse":
        return sum(unit.current_hp * 80.0 for unit in enemies)
    if code == "doom_light":
        return len(enemies) * 32.0 + sum(unit.current_hp * 40.0 for unit in enemies)
    if code in {"complete_burn", "blizzard"}:
        status_name = "完全燃烧" if code == "complete_burn" else "暴风雪"
        return sum(24.0 if not unit.has_status(status_name) else 0.0 for unit in enemies)
    if code == "morning_holy_light":
        return len(enemies) * 30.0 + sum(40.0 for unit in enemies if unit.attribute == "暗")
    if code == "eagle_eye":
        return sum(
            42.0
            + (0 if unit.cannot_attack else 24.0)
            + sum(1 for skill in unit.skills if skill.timing == "active") * 12.0
            for unit in enemies
            if not unit.has_status("鹰眼")
        )
    if code == "hundred_bird_burial":
        from wujiang.tactical.heroes.excel_roster import skill_has_movement_effect
        return sum(24.0 + 18.0 * sum(skill_has_movement_effect(skill) for skill in unit.skills)
                   for unit in enemies if not unit.has_status("百鸟葬禁位移"))
    if code == "snow_avalanche":
        return sum(
            52.0 + 9.0 * sum(skill.timing == "active" for skill in unit.skills)
            for unit in enemies if not unit.has_status("雪崩") and unit.alive
        )
    if code == "thor_destroy_lightning":
        return sum(54.0 for unit in enemies if unit.attribute != "雷" and not unit.has_status("毁灭电击"))
    if code in {"drain_mana", "large_drain_mana"}:
        return sum(min(unit.current_mana, 1.0) * 45.0 for unit in enemies)
    if code == "mana_pull":
        return 20.0 if enemies else 6.0
    if code == "stance":
        return field_skill_score(battle, actor, code, targets, profile)
    if code == "plant_growth":
        return field_skill_score(battle, actor, code, targets, profile)
    if code == "smoke_spray":
        return field_skill_score(battle, actor, code, targets, profile)
    if code == "gale":
        return gale_score(battle, actor, payload, profile)
    if code == "heaven_lock":
        target = primary_target_unit(battle, payload, targets)
        if target is None or target.player_id == actor.player_id or target.has_status("天锁") or target.cannot_normal_move or target.stat("speed") <= 0:
            return -1000.0
        with ai_probe_rollback(battle):
            skill = actor.get_skill(code)
            try:
                skill.execute(battle, actor, payload)
            except ActionError:
                return -1000.0
            if not target.has_status("天锁"):
                return -1000.0
        return 24.0 + min(6.0, target.stat("speed")) * 7.0 - 1.5 * 12.0
    if code == "sun_slash":
        return r23_sun_slash_score(battle, actor, actor.get_skill(code), payload)
    if code == "heaven_punishment":
        selected_code = str(payload.get("disabled_skill_code") or "")
        target = primary_target_unit(battle, payload, targets)
        if target is None or not selected_code:
            return -120.0
        from wujiang.tactical.heroes.excel_roster import HeavenPunishmentSkill

        selected = next((skill for skill in HeavenPunishmentSkill.public_active_skills(battle, target) if skill.code == selected_code), None)
        if selected is None or any(getattr(status, "skill_code", None) == selected_code for status in target.statuses):
            return -120.0
        if selected.max_uses_per_battle is not None and selected.uses_this_battle >= selected.max_uses_per_battle:
            return -120.0
        score = 32.0 if selected.cooldown_remaining > 0 else 55.0
        if selected_code in DAMAGING_SKILL_CODES or selected_code in CONTROL_SKILL_CODES:
            score += 35.0
        if selected_code in SUMMON_SKILL_CODES or selected_code in HEAL_SKILL_CODES:
            score += 24.0
        if getattr(selected, "max_uses_per_battle", None) == 1 and getattr(selected, "uses_this_battle", 0) == 0:
            score += 20.0
        if selected.mana_cost > target.max_mana():
            score -= 45.0
        return score
    if code in {"paralysis_card", "poison_card", "drain_card", "magic_claw"}:
        if code == "drain_card":
            return sum(min(unit.current_mana, 1.0) * 35.0 + hostile_unit_value(unit) * 0.12 for unit in enemies)
        if code == "poison_card":
            return len(enemies) * 28.0 + sum(unit.current_hp * 18.0 for unit in enemies)
        if code == "magic_claw":
            return len(enemies) * 30.0
        return len(enemies) * 32.0
    return 0.0


def drain_mana_score(battle: Battle, actor: Unit, targets: list[Unit], profile: DifficultyProfile) -> float:
    enemies = [unit for unit in targets if unit.player_id != actor.player_id]
    if not enemies:
        return -4.0
    return sum(min(unit.current_mana, 1.0) * 55.0 + hostile_unit_value(unit) * 0.2 for unit in enemies)


def r12_future_banish_value(battle: Battle, target: Unit, profile: DifficultyProfile) -> float:
    """Value attacks and currently usable active skills in the target's next owner slot."""
    with ai_probe_rollback(battle):
        battle.turn_number += 1
        battle.active_player = target.player_id
        battle._exclusive_turn_unit_id = battle.unit_turn_slot_id(target) or target.unit_id
        battle.resolving_action = None
        target.turn_ready = True
        attack = r19_attack_window_value(battle, target, profile)
        active = 0.0
        if not target.cannot_use_skills:
            for skill in target.skills:
                if skill.timing != "active" or skill.code in {"sanctuary_banish", "mountain_awakening"}:
                    continue
                try:
                    if not skill.can_use(battle, target, {})[0]:
                        continue
                    action = copied_skill_action(battle, target, skill)
                    payloads = skill_payloads_for_action(battle, target, action)
                    ranked = sorted(payloads, key=lambda payload: sum(
                        unit.player_id != target.player_id for unit in skill_effect_units(battle, target, skill, payload)
                    ), reverse=True)
                    for payload in ranked[:6]:
                        heals = skill.code in HEAL_SKILL_CODES | {"punisher_heal", "nian_dragon_dance"}
                        if heals:
                            if not any(unit.player_id == target.player_id and unit.current_hp < unit.max_health and not unit.cannot_heal
                                       for unit in skill_effect_units(battle, target, skill, payload)):
                                continue
                        elif not skill_payload_has_effective_enemy_impact(battle, target, action, payload):
                            continue
                        active = max(active, score_skill_payload(battle, target, action, payload, profile, instant_only=False))
                except (ActionError, KeyError, TypeError, ValueError):
                    continue
        return max(0.0, attack, active)


def r12_realized_effect_value(battle: Battle, actor: Unit, skill: Any, payload: dict[str, Any],
                              profile: DifficultyProfile, *, gravity_values: list[int] | None = None) -> float:
    with ai_probe_rollback(battle):
        units = list(battle.all_units())
        before = {unit.unit_id: (unit.current_hp, unit.current_mana, unit.alive, unit.total_shields(),
                                 unit.has_status("圣殿放逐")) for unit in units}
        control_before = {}
        if skill.code == "sanctuary_banish":
            control_before = {unit.unit_id: r12_future_banish_value(battle, unit, profile)
                              for unit in units if unit.player_id != actor.player_id and unit.alive}
        try:
            queued = battle.build_queued_action({"type": "skill", "unit_id": actor.unit_id, **payload, "skill_code": skill.code})
            if gravity_values is not None:
                queued.payload["gravity_coin_values"] = gravity_values
                queued.payload["gravity_side"] = gravity_values[0] * gravity_values[1] * gravity_values[2]
            battle.pending_followup_actions.clear()
            battle.prepay_skill_resources(skill, actor, queued.payload)
            queued.payload["resources_prepaid"] = True
            actor.notify_action_declared(battle, "skill", queued.payload)
            queued.payload["declared_source_attack"] = actor.stat("attack")
            battle.resolve_queued_action(queued)
            while battle.pending_followup_actions:
                battle.resolve_queued_action(battle.pending_followup_actions.popleft())
        except (ActionError, KeyError, TypeError, ValueError):
            return -1000.0
        score = 0.0
        for unit in units:
            hp, mana, alive, shields, locked = before[unit.unit_id]
            mana_weight = 42.0 if skill.code == "punisher_heal" and unit.player_id == actor.player_id and unit.unit_id != actor.unit_id else 22.0
            value = (hp - unit.current_hp) * 115.0 + (mana - unit.current_mana) * mana_weight
            value += max(0, shields - unit.total_shields()) * 20.0
            if alive and not unit.alive:
                value += 90.0
            if skill.code == "sanctuary_banish" and unit.alive and not locked and unit.has_status("圣殿放逐"):
                value += max(0.0, control_before.get(unit.unit_id, 0.0)
                             - r12_future_banish_value(battle, unit, profile))
            score += value if unit.player_id != actor.player_id else -value * (1.3 if hp > unit.current_hp else 1.0)
        return score


def r12_effect_score(battle: Battle, actor: Unit, skill: Any, payload: dict[str, Any],
                     profile: DifficultyProfile) -> float:
    if skill.code == "gravity_field":
        branches = (([1, 1, 1], 1), ([2, 1, 1], 3), ([2, 2, 1], 3), ([2, 2, 2], 1))
        score = sum(weight * r12_realized_effect_value(battle, actor, skill, payload, profile,
                                                       gravity_values=values) for values, weight in branches) / 8.0
    else:
        score = r12_realized_effect_value(battle, actor, skill, payload, profile)
    if score <= 0:
        return -1000.0
    if skill.max_uses_per_battle == 1 and score < profile.once_per_battle_threshold:
        score -= 32.0
    return score


def r12_costly_window(battle: Battle, actor: Unit, profile: DifficultyProfile) -> float:
    best = 0.0
    for skill in actor.skills:
        if skill.timing != "active" or skill.mana_cost <= 0 or skill.code in {"mountain_god_muro", "mountain_escape", "mountain_awakening"}:
            continue
        if skill.code not in DAMAGING_SKILL_CODES | HOSTILE_EFFECT_SKILL_CODES | HEAL_SKILL_CODES:
            continue
        try:
            if not skill.can_use(battle, actor, {})[0]:
                continue
            action = copied_skill_action(battle, actor, skill)
            payloads = skill_payloads_for_action(battle, actor, action)
            ranked = sorted(payloads, key=lambda payload: sum(
                unit.player_id != actor.player_id for unit in skill_effect_units(battle, actor, skill, payload)
            ), reverse=True)
            for payload in ranked[:8]:
                if not payload_is_legal(battle, payload):
                    continue
                if skill.code in HEAL_SKILL_CODES:
                    if not any(unit.player_id == actor.player_id and unit.current_hp < unit.max_health and not unit.cannot_heal
                               for unit in skill_effect_units(battle, actor, skill, payload)):
                        continue
                elif not skill_payload_has_effective_enemy_impact(battle, actor, action, payload):
                    continue
                best = max(best, score_skill_payload(battle, actor, action, payload, profile, instant_only=False))
        except (ActionError, KeyError, TypeError, ValueError):
            continue
    return best


def r12_preparation_score(battle: Battle, actor: Unit, code: str, profile: DifficultyProfile) -> float:
    if code == "mountain_god_muro":
        if actor.has_status("山神术。室王"):
            return -8.0
        before = r12_costly_window(battle, actor, profile)
        with ai_probe_rollback(battle):
            actor.get_skill(code).execute(battle, actor, {})
            after = r12_costly_window(battle, actor, profile)
        gain = max(0.0, after - before)
        return max(profile.once_per_battle_threshold + 35.0, gain * 1.5 + 30.0) if gain > 0 else -8.0
    if code == "mountain_escape":
        if actor.has_status("遁术。神山"):
            return -8.0
        before_risk = r19_incoming_position_risk(battle, actor)
        critically_low = actor.current_hp <= actor.max_health * 0.25
        likely_lethal = before_risk >= actor.current_hp * 100.0 and before_risk > 0
        if not critically_low and not likely_lethal:
            return -1000.0
        before_output = r19_attack_window_value(battle, actor, profile)
        hp = actor.current_hp
        with ai_probe_rollback(battle):
            actor.get_skill(code).execute(battle, actor, {})
            healing = max(0.0, actor.current_hp - hp)
            after_risk = r19_incoming_position_risk(battle, actor)
            after_output = r19_attack_window_value(battle, actor, profile)
        gain = healing * 150.0 + max(0.0, before_risk - after_risk) * 1.3
        gain += min(1.0, max(0.0, actor.max_mana() - actor.current_mana)) * 18.0
        gain -= max(0.0, before_output - after_output) * 0.85
        if likely_lethal:
            return gain + 200.0 if gain > 14.0 else -8.0
        # At critical HP without immediate lethal pressure, use available skills first.
        return min(profile.once_per_battle_threshold + 5.0, gain - 14.0) if gain > 14.0 else -8.0
    used = [skill for skill in actor.skills if skill.max_uses_per_battle == 1 and skill.uses_this_battle > 0]
    if not used or sum(status.name == "山神计数点" for status in actor.statuses) < 8:
        return -8.0
    gain = 0.0
    with ai_probe_rollback(battle):
        for skill in used:
            skill.uses_this_battle = 0
            status = actor.get_status(skill.name)
            if status is not None:
                actor.remove_status(status, battle)
            if skill.code in {"mountain_god_muro", "mountain_escape"}:
                gain = max(gain, r12_preparation_score(battle, actor, skill.code, profile))
    return gain + 30.0 if gain > 0 else -8.0


def r13_recovery_value(battle: Battle, target: Unit) -> float:
    if target.cannot_heal:
        return 0.0
    missing = max(0.0, target.max_health - target.current_hp)
    if missing <= 0:
        return 0.0
    natural = any(trait.name in {"自然回血", "自然回复"} for trait in target.traits)
    active = 0.0
    with ai_probe_rollback(battle):
        battle.turn_number += 1
        battle.active_player = target.player_id
        battle._exclusive_turn_unit_id = battle.unit_turn_slot_id(target) or target.unit_id
        target.turn_ready = True
        battle.resolving_action = None
        for candidate in target.skills:
            if candidate.code not in HEAL_SKILL_CODES | {"punisher_heal", "nian_dragon_dance"} or candidate.timing != "active":
                continue
            try:
                if not candidate.can_use(battle, target, {})[0]:
                    continue
                action = copied_skill_action(battle, target, candidate)
                payloads = [probe for probe in skill_payloads_for_action(battle, target, action)
                            if target in skill_effect_units(battle, target, candidate, probe)]
                for probe in payloads[:8]:
                    with ai_probe_rollback(battle):
                        hp = target.current_hp
                        queued = battle.build_queued_action(probe)
                        battle.prepay_skill_resources(candidate, target, queued.payload)
                        queued.payload["resources_prepaid"] = True
                        target.notify_action_declared(battle, "skill", queued.payload)
                        battle.resolve_queued_action(queued)
                        gain = max(0.0, target.current_hp - hp)
                        if gain > 0:
                            active = max(active, gain * 100.0 + 22.0)
            except (ActionError, KeyError, ValueError, TypeError):
                continue
    return (min(missing, 0.25) * 100.0 if natural else 0.0) + active


def r13_attack_value(battle: Battle, actor: Unit, payload: dict[str, Any]) -> float:
    with ai_probe_rollback(battle):
        units = list(battle.all_units())
        before = {unit.unit_id: (unit.current_hp, unit.current_mana, unit.alive, unit.total_shields()) for unit in units}
        try:
            queued = battle.build_queued_action({"type": "attack", "unit_id": actor.unit_id, **payload})
            actor.notify_action_declared(battle, "attack", queued.payload)
            queued.payload["declared_source_attack"] = actor.stat("attack")
            battle.resolve_queued_action(queued)
        except (ActionError, KeyError, ValueError, TypeError):
            return -1000.0
        score = 0.0
        for unit in units:
            hp, mana, alive, shields = before[unit.unit_id]
            value = (hp - unit.current_hp) * 100.0 + (mana - unit.current_mana) * 18.0
            value += max(0, shields - unit.total_shields()) * 20.0 + (80.0 if alive and not unit.alive else 0.0)
            score += value if unit.player_id != actor.player_id else -value * (1.3 if hp > unit.current_hp else 1.0)
        return score - 4.0 if score > 0 else -1000.0


def r13_effect_score(battle: Battle, actor: Unit, skill: Any, payload: dict[str, Any], profile: DifficultyProfile) -> float:
    from wujiang.tactical.heroes.excel_roster import skill_has_movement_effect
    with ai_probe_rollback(battle):
        units = list(battle.all_units())
        before = {unit.unit_id: (unit.current_hp, unit.current_mana, unit.alive, unit.total_shields(),
                                 {status.name for status in unit.statuses}) for unit in units}
        control = {}
        for unit in skill_effect_units(battle, actor, skill, payload):
            if skill.code == "hundred_bird_burial":
                control[unit.unit_id] = 24.0 * sum(skill_has_movement_effect(candidate) and not unit.cannot_use_skills
                    and candidate.cooldown_remaining == 0 and candidate.mana_cost <= unit.current_mana
                    and not any(component.blocks_skill_use(battle, unit, candidate)[0] for component in unit.iter_components())
                    for candidate in unit.skills)
            elif skill.code == "nian_jade_flash":
                control[unit.unit_id] = r13_recovery_value(battle, unit)
        protected = [unit for unit in battle.player_units(actor.player_id) if unit.alive and unit.unit_id != actor.unit_id]
        risks = {unit.unit_id: r19_incoming_position_risk(battle, unit) for unit in protected} if skill.code == "nian_roar" else {}
        mana_risk = r19_incoming_position_risk(battle, actor) if actor.hero_code == "excel_r056" and skill.mana_cost > 0 else 0.0
        try:
            queued = battle.build_queued_action({"type": "skill", "unit_id": actor.unit_id, **payload, "skill_code": skill.code})
            battle.pending_followup_actions.clear()
            battle.prepay_skill_resources(skill, actor, queued.payload)
            queued.payload["resources_prepaid"] = True
            actor.notify_action_declared(battle, "skill", queued.payload)
            queued.payload["declared_source_attack"] = actor.stat("attack")
            battle.resolve_queued_action(queued)
            while battle.pending_followup_actions:
                battle.resolve_queued_action(battle.pending_followup_actions.popleft())
        except (ActionError, KeyError, ValueError, TypeError):
            return -1000.0
        score = 0.0
        for unit in units:
            hp, mana, alive, shields, statuses = before[unit.unit_id]
            value = (hp - unit.current_hp) * 100.0 + (mana - unit.current_mana) * 18.0
            value += max(0, shields - unit.total_shields()) * 20.0 + (80.0 if alive and not unit.alive else 0.0)
            name = "百鸟葬禁位移" if skill.code == "hundred_bird_burial" else "碧玉闪光"
            if unit.alive and name not in statuses and unit.has_status(name):
                value += control.get(unit.unit_id, 0.0)
            score += value if unit.player_id != actor.player_id else -value * (1.3 if hp > unit.current_hp else 1.0)
        if skill.code == "nian_roar" and any(unit.alive and unit.has_status("怒吼") and "怒吼" not in before[unit.unit_id][4] for unit in units):
            score += min(90.0, sum(max(0.0, risk - r19_incoming_position_risk(battle, battle.get_unit(unit_id)))
                                   for unit_id, risk in risks.items() if battle.units.get(unit_id) is not None))
        if skill.code == "remi_chaos" and actor.alive:
            old_position = battle.declared_source_position(queued.payload)
            score += (score_move_destination(battle, actor, actor.position, hero_style(actor), profile)
                      - score_move_destination(battle, actor, old_position, hero_style(actor), profile)) * 0.7
        if actor.hero_code == "excel_r056" and actor.alive and actor.current_mana < 2 and skill.mana_cost > 0:
            score -= min(80.0, max(0.0, r19_incoming_position_risk(battle, actor) - mana_risk))
        if skill.code == "nian_dragon_dance":
            score -= 20.0  # Preserve the two-round recovery when only a little mana is missing.
        if score <= 0:
            return -1000.0
        if skill.max_uses_per_battle == 1 and score < profile.once_per_battle_threshold:
            score -= 32.0
        return score


def r13_bat_score(battle: Battle, actor: Unit, skill: Any, payload: dict[str, Any], profile: DifficultyProfile) -> float:
    with ai_probe_rollback(battle):
        original = set(battle.units)
        risk = r19_incoming_position_risk(battle, actor)
        try:
            skill.execute(battle, actor, dict(payload))
        except (ActionError, KeyError, ValueError):
            return -1000.0
        summon = next((unit for unit in battle.all_units() if unit.unit_id not in original and unit.hero_code == "remi_bat"), None)
        if summon is None or not summon.can_take_turn_actions(battle):
            return -1000.0
        output = r19_attack_window_value(battle, summon, profile)
        cover = max(0.0, risk - r19_incoming_position_risk(battle, actor))
        return output + min(25.0, output * 0.4) + cover - 8.0 if output > 0 or cover > 0 else -8.0


def r14_snow_avalanche_score(battle: Battle, actor: Unit, skill: Any, payload: dict[str, Any]) -> float:
    targets = skill_effect_units(battle, actor, skill, payload)
    with ai_probe_rollback(battle):
        before = {unit.unit_id: (unit.current_hp, unit.alive, unit.total_shields(), unit.has_status("雪崩"),
                                 not unit.cannot_attack, not unit.cannot_move,
                                 sum(candidate.timing == "active" and candidate.cooldown_remaining == 0
                                     and candidate.mana_cost <= unit.current_mana
                                     for candidate in unit.skills) if not unit.cannot_use_skills else 0)
                  for unit in targets}
        try:
            skill.execute(battle, actor, dict(payload))
        except (ActionError, KeyError, ValueError):
            return -1000.0
        score = -skill.mana_cost_for_payload(battle, actor, payload) * 18.0
        for unit in targets:
            hp, alive, shields, locked, attack, movement, active = before[unit.unit_id]
            value = max(0.0, hp - unit.current_hp) * 100.0 + max(0, shields - unit.total_shields()) * 20.0
            if alive and not unit.alive:
                value += 80.0
            if unit.alive and not locked and unit.has_status("雪崩"):
                value += (24.0 if attack else 0.0) + (28.0 if movement else 0.0) + 9.0 * active
            score += value if unit.player_id != actor.player_id else -value * 1.3
        return score if score > 0 else -1000.0


def r14_incoming_skill_loss(battle: Battle, actor: Unit) -> float:
    total = 0.0
    for enemy in living_hostile_combatants(battle, actor.player_id):
        with ai_probe_rollback(battle):
            battle.turn_number += 1
            battle.active_player, battle._exclusive_turn_unit_id, enemy.turn_ready = enemy.player_id, enemy.unit_id, True
            battle.resolving_action = None
            best = 0.0
            for skill in enemy.skills:
                if skill.timing != "active" or skill.code not in DAMAGING_SKILL_CODES:
                    continue
                try:
                    if not skill.can_use(battle, enemy, {})[0]:
                        continue
                    action = copied_skill_action(battle, enemy, skill)
                    payloads = trim_skill_payloads_for_ai(battle, enemy, skill_payloads_for_action(battle, enemy, action), limit=16)
                    for payload in payloads:
                        target = battle.effect_recipient(actor)
                        if target not in skill_effect_units(battle, enemy, skill, payload):
                            continue
                        with ai_probe_rollback(battle):
                            hp = target.current_hp
                            battle.pending_followup_actions.clear()
                            queued = battle.build_queued_action(payload)
                            battle.prepay_skill_resources(skill, enemy, queued.payload)
                            queued.payload["resources_prepaid"] = True
                            enemy.notify_action_declared(battle, "skill", queued.payload)
                            queued.payload["declared_source_attack"] = enemy.stat("attack")
                            battle.resolve_queued_action(queued)
                            while battle.pending_followup_actions:
                                battle.resolve_queued_action(battle.pending_followup_actions.popleft())
                            best = max(best, max(0.0, hp - target.current_hp) * 110.0)
                except (ActionError, KeyError, ValueError, TypeError):
                    continue
            total += best
    return total


def r14_cat_form_score(battle: Battle, actor: Unit, profile: DifficultyProfile) -> float:
    from wujiang.tactical.heroes.excel_roster import BlackCatFormStatus
    before = r14_incoming_skill_loss(battle, actor) + r19_incoming_position_risk(battle, actor)
    output = r14_cat_attack_and_retreat_value(battle, actor)
    with ai_probe_rollback(battle):
        existing = actor.get_status("化猫")
        if existing is not None:
            actor.remove_status(existing, battle)
        else:
            actor.add_status(BlackCatFormStatus())
        after = r14_incoming_skill_loss(battle, actor) + r19_incoming_position_risk(battle, actor)
        new_output = r14_cat_attack_and_retreat_value(battle, actor)
        value = before - after + new_output - output
        return value if value > 18.0 else -8.0


def r14_cat_attack_and_retreat_value(battle: Battle, actor: Unit) -> float:
    enemies = living_hostile_combatants(battle, actor.player_id)
    if actor.position is None or actor.cannot_attack or not enemies:
        return 0.0
    origins = [actor.position]
    distance = actor.remaining_normal_move_distance(battle)
    if distance > 0 and not actor.cannot_move and not actor.cannot_normal_move:
        origins += battle.reachable_positions(actor, max_distance=distance, use_movement_cost=True)
    best = 0.0
    for target in battle.effect_units(enemies):
        with ai_probe_rollback(battle):
            legal = []
            for origin in origins:
                actor.position = origin
                if battle.attack_target_allowed(actor, target)[0]:
                    legal.append(origin)
        for origin in sorted(legal, key=lambda cell: cell.distance_to(actor.position))[:8]:
            with ai_probe_rollback(battle):
                try:
                    if origin != actor.position:
                        battle.move_unit(actor, origin)
                    hp, alive = target.current_hp, target.alive
                    queued = battle.build_queued_action({"type": "attack", "unit_id": actor.unit_id,
                                                        "target_unit_id": target.unit_id})
                    actor.notify_action_declared(battle, "attack", queued.payload)
                    queued.payload["declared_source_attack"] = actor.stat("attack")
                    battle.resolve_queued_action(queued)
                    if not actor.alive or actor.banished:
                        continue
                    gain = max(0.0, hp - target.current_hp) * 100.0 + (80.0 if alive and not target.alive else 0.0)
                    escapes = [actor.position]
                    remaining = actor.remaining_normal_move_distance(battle)
                    if remaining > 0 and not actor.cannot_move and not actor.cannot_normal_move:
                        escapes += battle.reachable_positions(actor, max_distance=remaining, use_movement_cost=True)
                    escape_risk = float("inf")
                    for cell in sorted(set(escapes), key=lambda cell: min((cell.distance_to(enemy.position) for enemy in enemies
                                                                        if enemy.position is not None), default=0), reverse=True)[:8]:
                        with ai_probe_rollback(battle):
                            if cell != actor.position:
                                battle.move_unit(actor, cell)
                            escape_risk = min(escape_risk, r19_incoming_position_risk(battle, actor))
                    best = max(best, gain - escape_risk)
                except (ActionError, KeyError, ValueError, TypeError):
                    continue
    return best


def kaiser_fist_score(
    battle: Battle,
    actor: Unit,
    skill: Any,
    payload: dict[str, Any],
    profile: DifficultyProfile,
) -> float:
    targets = [unit for unit in skill_effect_units(battle, actor, skill, payload) if unit.player_id != actor.player_id]
    if not targets:
        return -20.0
    target = targets[0]
    with ai_probe_rollback(battle):
        hp, mana, shields, alive = target.current_hp, actor.current_mana, target.total_shields(), target.alive
        value = hostile_unit_value(target)
        try:
            skill.execute(battle, actor, dict(payload))
        except (ActionError, KeyError, ValueError):
            return -1000.0
        damage = max(0.0, hp - target.current_hp)
        # Clone destruction is not HP damage for the compensation clause.
        if target.is_clone:
            damage = 0.0
        score = damage * 100.0 + max(0.0, actor.current_mana - mana) * 28.0
        score += max(0, shields - target.total_shields()) * 42.0
        if alive and not target.alive:
            score += 60.0 + value * 0.25
        if damage > 0:
            score += value * 0.45 + profile.aggressive_bonus
        return score if score > 0 else -100.0


def r15_control_value(battle: Battle, target: Unit, code: str) -> float:
    from wujiang.tactical.heroes.excel_roster import skill_has_movement_effect

    status_name = "乱音电波" if code == "noise_wave" else "神圣决斗"
    if target.has_status(status_name):
        return 0.0
    useful = [skill for skill in target.skills
              if not target.cannot_use_skills and not getattr(skill, "locked", False) and skill.cooldown_remaining <= 0
              and (skill.max_uses_per_battle is None or skill.uses_this_battle < skill.max_uses_per_battle)
              and skill.mana_cost <= target.current_mana
              and not any(component.blocks_skill_use(battle, target, skill)[0] for component in target.iter_components())]
    if code == "noise_wave":
        slow = 14.0 if target.stat("speed") > 1 and not target.cannot_move and not target.cannot_normal_move else 0.0
        return slow + 24.0 * sum(skill_has_movement_effect(skill) for skill in useful)
    movement = 18.0 + min(5.0, target.stat("speed")) * 8.0 if not target.cannot_move and not target.cannot_normal_move else 0.0
    return movement + 26.0 * sum(skill.timing == "active" for skill in useful)


def reviewed_r15_effect_score(
    battle: Battle,
    actor: Unit,
    skill: Any,
    payload: dict[str, Any],
    profile: DifficultyProfile,
) -> float:
    code = str(skill.code)
    targets = skill_effect_units(battle, actor, skill, payload)
    control = {unit.unit_id: r15_control_value(battle, unit, code) for unit in targets} if code in {"noise_wave", "sacred_duel"} else {}
    with ai_probe_rollback(battle):
        before = {unit.unit_id: (unit.current_hp, unit.alive, unit.player_id, unit.current_mana,
                                unit.total_shields(), hostile_unit_value(unit),
                                {status.name for status in unit.statuses}) for unit in battle.all_units()}
        probe = dict(payload)
        probe.update(skill.queued_payload_metadata(battle, actor, probe))
        try:
            skill.execute(battle, actor, probe)
        except (ActionError, KeyError, ValueError):
            return -1000.0
        score = -skill.mana_cost_for_payload(battle, actor, probe) * 18.0
        for unit_id, (hp, alive, player, mana, shields, value, statuses) in before.items():
            unit = battle.units.get(unit_id)
            lost_hp = max(0.0, hp - (unit.current_hp if unit is not None else 0.0))
            destroyed = alive and (unit is None or not unit.alive)
            impact = lost_hp * 100.0 + (60.0 + value * 0.25 if destroyed else 0.0)
            if unit is not None:
                impact += max(0, shields - unit.total_shields()) * 14.0
                if code == "interference" and alive and unit.alive and player != actor.player_id and unit.player_id == actor.player_id:
                    impact += 70.0 + value * 0.55
                if code == "purify_mana":
                    impact += max(0.0, mana - unit.current_mana) * 34.0
                if code in {"noise_wave", "sacred_duel"}:
                    status_name = "乱音电波" if code == "noise_wave" else "神圣决斗"
                    if status_name not in statuses and unit.has_status(status_name):
                        impact += control.get(unit_id, 0.0)
            score += impact if player != actor.player_id else -impact * 1.3
        return score + profile.aggressive_bonus if score > 0 else score - 100.0


def electric_control_opportunities(battle: Battle, unit: Unit) -> float:
    if not unit.alive or unit.banished or unit.position is None:
        return 0.0
    skills = 0
    if not unit.cannot_use_skills:
        for skill in unit.skills:
            if skill.cooldown_remaining > 0 or (skill.max_uses_per_battle is not None and skill.uses_this_battle >= skill.max_uses_per_battle):
                continue
            if unit.current_mana < skill.mana_cost or any(component.blocks_skill_use(battle, unit, skill)[0] for component in unit.iter_components()):
                continue
            if any(effect.blocks_skill_use(battle, unit, skill)[0] for effect in battle.field_effects):
                continue
            skills += 1
    movement = 0.0 if unit.cannot_move or unit.cannot_normal_move else max(0.0, unit.stat("speed") - 1.0) * 6.0
    return min(4, skills) * 14.0 + movement


def reviewed_r19_effect_score(battle: Battle, actor: Unit, skill: Any, payload: dict[str, Any], profile: DifficultyProfile) -> float:
    score = reviewed_r18_effect_score(battle, actor, skill, payload, profile)
    if skill.code == "electric_wind":
        with ai_probe_rollback(battle):
            before = {unit.unit_id: electric_control_opportunities(battle, unit) for unit in battle.all_units()}
            try:
                queued = battle.build_queued_action(payload)
                battle.prepay_skill_resources(skill, actor, queued.payload)
                queued.payload["declared_source_attack"] = actor.stat("attack")
                battle.resolving_action = queued
                skill.execute(battle, actor, queued.payload)
            except (ActionError, KeyError, TypeError, ValueError):
                return -1000.0
            for unit in battle.all_units():
                if unit.alive and unit.has_status("电风"):
                    lost = max(0.0, before.get(unit.unit_id, 0.0) - electric_control_opportunities(battle, unit))
                    score += lost if unit.player_id != actor.player_id else -lost * 1.4
    if skill.max_uses_per_battle == 1 and score < profile.once_per_battle_threshold:
        score -= 32.0
    return score


def electric_auto_position_value(battle: Battle, actor: Unit, destination: Position | None) -> float:
    from wujiang.tactical.heroes.excel_roster import ElectricWindSkill, square_around_cells
    if destination is None or not actor.alive or actor.banished:
        return 0.0
    wind = ElectricWindSkill()
    if actor.cannot_use_skills or any(component.blocks_skill_use(battle, actor, wind)[0] for component in actor.iter_components()):
        return 0.0
    if not actor.direct_effects_blocked() and any(effect.blocks_skill_use(battle, actor, wind)[0] for effect in battle.field_effects):
        return 0.0
    with ai_probe_rollback(battle):
        actor.position = destination
        units = list(battle.all_units())
        before = {unit.unit_id: (unit.current_hp, unit.alive, unit.total_shields(), electric_control_opportunities(battle, unit)) for unit in units}
        cells = square_around_cells(battle, battle.unit_cells(actor), radius=2)
        wind.apply_to_units(battle, actor, battle.effect_units_at_cells(cells, ignore=battle.effect_recipient(actor)), action_name="自动电风", cells=cells)
        value = 0.0
        for unit in units:
            hp, alive, shields, control = before[unit.unit_id]
            impact = max(0.0, hp - unit.current_hp) * 100 + max(0, shields - unit.total_shields()) * 20
            if alive and not unit.alive:
                impact += 90
            elif unit.alive and unit.has_status("电风"):
                impact += max(0.0, control - electric_control_opportunities(battle, unit))
            value += impact if unit.player_id != actor.player_id else -impact * 1.4
        return value * 0.6


def r19_attack_window_value(battle: Battle, actor: Unit, profile: DifficultyProfile) -> float:
    if actor.position is None or actor.banished or not actor.alive:
        return 0.0
    origins = [actor.position]
    if not actor.cannot_move and not actor.cannot_normal_move:
        origins += battle.reachable_positions(actor, max_distance=actor.remaining_normal_move_distance(battle), use_movement_cost=True)
    value = 0.0
    for target in battle.effect_units(living_hostile_combatants(battle, actor.player_id)):
        if not battle.unit_can_be_selected(target, actor=actor)[0]:
            continue
        if not actor.cannot_attack and actor.attacks_used < actor.attack_actions_per_turn() and any(distance_to_position(battle, target, origin) <= actor.targeting_range() for origin in origins):
            with ai_probe_rollback(battle):
                actor.position = next(origin for origin in origins if distance_to_position(battle, target, origin) <= actor.targeting_range())
                if battle.attack_target_allowed(actor, target)[0]:
                    hp, alive, shields = target.current_hp, target.alive, target.total_shields()
                    battle.resolve_attack_damage(actor, target, action_name="AI普攻窗口", tags=set(), payload={})
                    damage = max(0.0, hp - target.current_hp)
                    value = max(value, damage * 100 + max(0, shields - target.total_shields()) * 20
                                + (70 if alive and not target.alive else 0))
    if not actor.cannot_use_skills:
        for skill in actor.skills:
            if skill.code not in {"hell_slash", "beetle_spear"} or skill.cooldown_remaining > 0:
                continue
            if skill.max_uses_per_battle is not None and skill.uses_this_battle >= skill.max_uses_per_battle:
                continue
            if any(component.blocks_skill_use(battle, actor, skill)[0] for component in actor.iter_components()):
                continue
            if not actor.direct_effects_blocked() and any(effect.blocks_skill_use(battle, actor, skill)[0] for effect in battle.field_effects):
                continue
            for cells in skill.patterns(battle, actor):
                payload = {"type": "skill", "unit_id": actor.unit_id, "skill_code": skill.code, "cells": [{"x": cell.x, "y": cell.y} for cell in cells]}
                value = max(value, reviewed_r19_effect_score(battle, actor, skill, payload, profile))
    return value


def r19_incoming_position_risk(battle: Battle, actor: Unit, *, until_next_enemy_end: bool = False) -> float:
    target = battle.effect_recipient(actor)
    value = 0.0
    enemies = living_hostile_combatants(battle, actor.player_id)
    if until_next_enemy_end:
        order = battle.turn_order_unit_ids
        first = next((battle.units.get(order[(battle.turn_slot_index + step) % len(order)])
                      for step in range(1, len(order) + 1)
                      if battle.units.get(order[(battle.turn_slot_index + step) % len(order)]) is not None
                      and battle.units[order[(battle.turn_slot_index + step) % len(order)]].player_id != actor.player_id
                      and battle.units[order[(battle.turn_slot_index + step) % len(order)]].alive), None)
        ids = {unit.unit_id for unit in battle.turn_bundle_units(first)} if first is not None else set()
        enemies = [enemy for enemy in enemies if enemy.unit_id in ids]
    for enemy in enemies:
        if enemy.cannot_attack:
            continue
        with ai_probe_rollback(battle):
            battle.resolving_action = None
            battle.active_player, battle._exclusive_turn_unit_id, enemy.turn_ready = enemy.player_id, enemy.unit_id, True
            enemy.attacks_used = enemy.normal_move_actions_used = enemy.normal_move_steps_used = 0
            enemy.move_used = False
            origins = [enemy.position]
            if not enemy.cannot_move and not enemy.cannot_normal_move:
                origins += battle.reachable_positions(enemy, max_distance=enemy.remaining_normal_move_distance(battle), use_movement_cost=True)
            best = 0.0
            for origin in origins:
                enemy.position = origin
                if not battle.attack_target_allowed(enemy, target)[0]:
                    continue
                with ai_probe_rollback(battle):
                    hp, alive = target.current_hp, target.alive
                    battle.resolve_attack_damage(enemy, target, action_name="AI近敌风险")
                    best = max(best, max(0.0, hp - target.current_hp) * 100 + (90 if alive and not target.alive else 0))
            value += best
    return value


def r19_preparation_score(battle: Battle, actor: Unit, code: str, profile: DifficultyProfile) -> float:
    before = r19_attack_window_value(battle, actor, profile)
    short_window = code == "martial_god_seal"
    danger = r19_incoming_position_risk(battle, actor, until_next_enemy_end=short_window)
    target = battle.effect_recipient(actor)
    hp, mana = target.current_hp, target.current_mana
    with ai_probe_rollback(battle):
        actor.get_skill(code).execute(battle, actor, {})
        after = r19_attack_window_value(battle, actor, profile)
        risk = r19_incoming_position_risk(battle, actor, until_next_enemy_end=short_window)
        recovery = max(0.0, target.current_hp - hp) * 130
        # Temporary mana is valuable only while output or a near-term defense can use it.
        if (after > 0 or danger > 0) and any(skill.mana_cost > 0 for skill in target.skills):
            recovery += max(0.0, target.current_mana - mana) * 12
        gain = after - before + danger - risk + recovery
    if gain <= 8.0:
        return -20.0
    return max(after, before) + gain + 18.0


def beetle_armor_reaction_score(battle: Battle, reactor: Unit, queued_action: QueuedAction, profile: DifficultyProfile) -> float:
    from dataclasses import replace
    target = battle.effect_recipient(reactor)
    results = []
    survival = []
    for deployed in (False, True):
        with ai_probe_rollback(battle):
            if deployed:
                reactor.get_skill("beetle_armor_deploy").execute(battle, reactor, {})
            battle.resolve_queued_action(replace(queued_action, payload=deepcopy(queued_action.payload)))
            results.append(target.current_hp * 100 + (90 if target.alive else 0))
            survival.append(target.alive)
    gain = results[1] - results[0]
    if not survival[0] and survival[1]:
        gain += 70.0
    with ai_probe_rollback(battle):
        battle.resolving_action = None
        battle.active_player, battle._exclusive_turn_unit_id, target.turn_ready = target.player_id, target.unit_id, True
        target.move_used, target.normal_move_actions_used, target.normal_move_steps_used = False, 0, 0
        target.attacks_used = 0
        sacrifice = r19_attack_window_value(battle, target, profile) * 0.7
        sacrifice += min(3, sum(skill.timing in {"passive", "reaction"} and skill.code != "beetle_armor_deploy" and skill.cooldown_remaining == 0 and skill.mana_cost <= target.current_mana for skill in target.skills)) * 12
    return gain - sacrifice - 8.0 if gain > 0 else -1000.0


def r21_attack_value(battle: Battle, actor: Unit, target: Unit, payload: dict[str, Any]) -> float:
    target = battle.effect_recipient(target)
    with ai_probe_rollback(battle):
        hp, mana, shields, dodges = target.current_hp, target.current_mana, target.total_shields(), target.dodge_charges
        stats = {name: target.stat(name) for name in ("attack", "defense", "speed")}
        own_mana = actor.current_mana
        ctx = battle.resolve_attack_damage(actor, target, action_name="普攻", payload=payload)
        if ctx is None:
            return -4.0
        value = max(0.0, hp - target.current_hp) * 100.0 + (90.0 if hp > 0 and not target.alive else 0.0)
        value += max(0.0, mana - target.current_mana) * 14.0
        value += max(0, shields - target.total_shields()) * 20.0 + max(0, dodges - target.dodge_charges) * 12.0
        value += sum(max(0.0, stats[name] - target.stat(name)) * weight
                     for name, weight in (("attack", 24.0), ("defense", 30.0), ("speed", 14.0)))
        value = value if target.player_id != actor.player_id else -value * 1.3
        return value + max(0.0, actor.current_mana - own_mana) * 26.0 - 4.0


def r21_unit_action_opportunities(battle: Battle, unit: Unit, profile: DifficultyProfile) -> float:
    """Price legal attacks and useful ready active/instant casts in the unit's action window."""
    if not unit.alive or unit.banished or unit.position is None:
        return 0.0
    with ai_probe_rollback(battle):
        future = not unit.can_take_turn_actions(battle)
        battle.resolving_action = None
        battle.active_player = unit.player_id
        battle._exclusive_turn_unit_id = unit.unit_id
        unit.turn_ready = True
        if future:
            unit.attacks_used = unit.normal_move_actions_used = unit.normal_move_steps_used = 0
            unit.move_used = False
            for skill in unit.skills:
                skill.uses_this_turn = 0
        origin = unit.position
        destinations = [origin]
        if not unit.cannot_move and not unit.cannot_normal_move and unit.remaining_normal_move_distance(battle) > 0:
            destinations += battle.reachable_positions(unit, max_distance=unit.remaining_normal_move_distance(battle), use_movement_cost=True)
        best_attack = 0.0
        if not unit.cannot_attack and unit.attacks_used < unit.attack_actions_per_turn():
            for target in battle.effect_units(battle.enemy_units(unit.player_id)):
                if not battle.unit_can_be_selected(target, actor=unit)[0]:
                    continue
                for destination in sorted(set(destinations), key=lambda cell: cell.distance_to(target.position)):
                    unit.position = destination
                    if battle.attack_target_allowed(unit, target)[0]:
                        best_attack = max(best_attack, r21_attack_value(battle, unit, target, {"target_unit_id": target.unit_id}))
                        break
        unit.position = origin
        value = best_attack * max(0, unit.attack_actions_per_turn() - unit.attacks_used)
        for skill in unit.skills:
            if skill.timing not in {"active", "instant"} or skill.code in {"eagle_eye", "vain_giant_shadow"}:
                continue
            if not skill.can_use(battle, unit, {})[0]:
                continue
            action = copied_skill_action(battle, unit, skill)
            payloads = skill_payloads_for_action(battle, unit, action)
            if any(payload.get("cells") for payload in payloads):
                payloads = dedupe_damage_area_payloads(battle, payloads)
            best = 0.0
            for payload in trim_skill_payloads_for_ai(battle, unit, payloads, limit=16):
                best = max(best, reviewed_r18_effect_score(battle, unit, skill, payload, profile))
            value += best
        return value


def r21_control_score(battle: Battle, actor: Unit, skill: Any, payload: dict[str, Any], profile: DifficultyProfile) -> float:
    with ai_probe_rollback(battle):
        targets = battle.effect_units(skill.get_target_units_for_payload(battle, actor, payload))
        if not targets:
            return -1000.0
        if skill.code == "vain_giant_shadow" and actor in targets and not actor.cannot_attack:
            preview = battle.basic_attack_preview_for_payload(actor)
            if any(r21_attack_value(battle, actor, battle.get_unit(unit_id), {"target_unit_id": unit_id}) > 0
                   for unit_id in preview.get("target_unit_ids", [])):
                return -1000.0
        before = {unit.unit_id: r21_unit_action_opportunities(battle, unit, profile) for unit in targets}
        status_name = "鹰眼" if skill.code == "eagle_eye" else "虚荣巨影"
        refresh = {}
        for unit in targets:
            existing = unit.get_status(status_name)
            if existing is not None:
                with ai_probe_rollback(battle):
                    remaining = existing.duration or 0
                    unit.remove_status(existing, battle)
                    plain = r21_unit_action_opportunities(battle, unit, profile)
                    refresh[unit.unit_id] = (before[unit.unit_id] - plain, remaining)
        score = reviewed_r18_effect_score(battle, actor, skill, payload, profile)
        try:
            queued = battle.build_queued_action(payload)
            battle.prepay_skill_resources(skill, actor, queued.payload)
            battle.resolving_action = queued
            skill.execute(battle, actor, queued.payload)
        except (ActionError, KeyError, TypeError, ValueError):
            return -1000.0
        for unit in targets:
            if not unit.has_status(status_name):
                continue
            delta = r21_unit_action_opportunities(battle, unit, profile) - before[unit.unit_id]
            if unit.unit_id in refresh:
                effect_delta, old_duration = refresh[unit.unit_id]
                extra = max(0, (unit.get_status(status_name).duration or 0) - old_duration)
                delta += effect_delta * min(0.5, extra * 0.25)
            if unit.player_id == actor.player_id:
                score += delta * (1.3 if delta < 0 else 1.0)
            else:
                score -= delta * (1.3 if delta > 0 else 1.0)
        return score


def r21_winged_contact_value(battle: Battle, actor: Unit, *, allow_leap: bool = True, allow_move: bool = True) -> float:
    if actor.position is None or actor.cannot_attack or actor.attacks_used >= actor.attack_actions_per_turn():
        return 0.0
    with ai_probe_rollback(battle):
        origin = actor.position
        destinations = {origin}
        if allow_move and not actor.cannot_move and not actor.cannot_normal_move and actor.remaining_normal_move_distance(battle) > 0:
            destinations.update(battle.reachable_positions(actor, max_distance=actor.remaining_normal_move_distance(battle), use_movement_cost=True))
        leap = actor.get_skill("fly_leap")
        if allow_leap and leap.can_use(battle, actor, {})[0]:
            landings = set()
            for start in list(destinations):
                actor.position = start
                landings.update(preview_positions(leap.preview(battle, actor).get("cells")))
            destinations.update(landings)
            if allow_move and not actor.cannot_normal_move:
                for landing in landings:
                    actor.position = landing
                    destinations.update(battle.reachable_positions(actor, max_distance=actor.normal_move_distance(), use_movement_cost=True))
        best = 0.0
        for target in battle.effect_units(battle.enemy_units(actor.player_id)):
            if not battle.unit_can_be_selected(target, actor=actor)[0]:
                continue
            for cell in sorted(destinations, key=lambda position: position.distance_to(target.position)):
                actor.position = cell
                if battle.attack_target_allowed(actor, target)[0]:
                    best = max(best, r21_attack_value(battle, actor, target, {"target_unit_id": target.unit_id}))
                    break
        return best


def r21_winged_leap_score(battle: Battle, actor: Unit, payload: dict[str, Any]) -> float:
    before = r21_winged_contact_value(battle, actor, allow_leap=False)
    with ai_probe_rollback(battle):
        try:
            skill = actor.get_skill("fly_leap")
            queued = battle.build_queued_action(payload)
            battle.prepay_skill_resources(skill, actor, queued.payload)
            skill.execute(battle, actor, queued.payload)
        except (ActionError, KeyError, TypeError, ValueError):
            return -1000.0
        after = r21_winged_contact_value(battle, actor, allow_leap=False)
        return after - before - 8.0


def r21_barrier_position_value(battle: Battle, actor: Unit) -> float:
    from wujiang.tactical.heroes.excel_roster import ElectronicBarrierAuraField
    fields = [effect for effect in battle.field_effects if isinstance(effect, ElectronicBarrierAuraField) and effect.source_unit_id == actor.unit_id]
    cells = {cell for effect in fields for cell in effect.affected_cells(battle)}
    value = 0.0
    for target in battle.effect_units_at_cells(list(cells)):
        best = 0.0
        for attacker in battle.effect_units(battle.enemy_units(target.player_id)):
            if attacker.position is None or not battle.unit_can_be_selected(target, actor=attacker)[0]:
                continue
            with ai_probe_rollback(battle):
                origins = [attacker.position]
                if not attacker.cannot_move and not attacker.cannot_normal_move:
                    origins += battle.reachable_positions(attacker, max_distance=attacker.normal_move_distance(), use_movement_cost=True)
                for origin in sorted(set(origins), key=lambda cell: cell.distance_to(target.position)):
                    attacker.position = origin
                    if not battle.attack_target_allowed(attacker, target)[0]:
                        continue
                    payload = {"target_unit_id": target.unit_id}
                    power = battle.basic_attack_preview_power(attacker, payload)
                    protected = probe_attack_damage_impact(battle, attacker, target, payload, attack_power=power).damage
                    battle.field_effects = [effect for effect in battle.field_effects if effect not in fields]
                    unprotected = probe_attack_damage_impact(battle, attacker, target, payload, attack_power=power).damage
                    best = max(best, min(target.current_hp, unprotected) - min(target.current_hp, protected))
                    break
        value += best * (100.0 if target.player_id == actor.player_id else -130.0)
    return value


def r21_queued_loss(battle: Battle, target: Unit, queued: QueuedAction) -> float:
    target = battle.effect_recipient(target)
    with ai_probe_rollback(battle):
        hp, mana = target.current_hp, target.current_mana
        stats = {name: target.stat(name) for name in ("attack", "defense", "speed")}
        before = unit_impact_signature(target)
        try:
            battle.resolve_queued_action(deepcopy(queued))
        except (ActionError, KeyError, TypeError, ValueError):
            return 0.0
        value = max(0.0, hp - target.current_hp) * 100.0 + max(0.0, mana - target.current_mana) * 24.0
        value += sum(max(0.0, stats[name] - target.stat(name)) * weight
                     for name, weight in (("attack", 24.0), ("defense", 30.0), ("speed", 14.0)))
        if hp > 0 and not target.alive:
            value += 90.0
        if value <= 0 and unit_impact_signature(target) != before and target.alive:
            # A real new control status counts, consuming only a shield does not.
            if any(status.name in {"鹰眼", "虚荣巨影", "雪崩", "毁灭电击", "电风"} for status in target.statuses):
                value += 30.0
        return value


def r21_barrier_shield_score(battle: Battle, actor: Unit, queued: QueuedAction, payload: dict[str, Any], code: str) -> float:
    skill = actor.get_skill(code)
    if not skill.can_react_with_payload(battle, actor, queued, payload)[0]:
        return -1000.0
    targets = battle.effect_units(skill.chosen_targets(battle, actor, payload))
    before = {target.unit_id: r21_queued_loss(battle, target, queued) for target in targets}
    with ai_probe_rollback(battle):
        skill.react(battle, actor, payload, queued)
        prevention = sum(max(0.0, before[target.unit_id] - r21_queued_loss(battle, target, queued)) for target in targets)
    if prevention <= 0:
        return -1000.0
    if code == "quantum_shield":
        # The third protection is available now at the cost of all next-round uses.
        prevention -= 12.0
        ion = actor.get_skill("ion_shield")
        if ion.can_react_with_payload(battle, actor, queued, payload)[0]:
            prevention -= 30.0
    return prevention


def vain_giant_shadow_score(
    battle: Battle,
    actor: Unit,
    payload: dict[str, Any],
    targets: list[Unit],
    profile: DifficultyProfile,
) -> float:
    target = primary_target_unit(battle, payload, targets)
    if target is None or target.has_status("虚荣巨影"):
        return -100.0
    damaging_skills = sum(1 for skill in target.skills if skill.code in DAMAGING_SKILL_CODES)
    if target.player_id == actor.player_id:
        if not target.cannot_attack or damaging_skills == 0:
            return -24.0
        return 22.0 + damaging_skills * 12.0 + ally_unit_value(target) * 0.1
    if target.cannot_attack:
        return -100.0
    attack_pressure = target.attack_actions_per_turn() * 24.0 + target.stat("attack") * 10.0
    skill_risk = damaging_skills * 12.0
    return 58.0 + attack_pressure - skill_risk + hostile_unit_value(target) * 0.16 + profile.aggressive_bonus


def purify_mana_score(
    battle: Battle,
    actor: Unit,
    payload: dict[str, Any],
    targets: list[Unit],
    profile: DifficultyProfile,
) -> float:
    target = primary_target_unit(battle, payload, targets)
    if target is None or target.player_id == actor.player_id:
        return -100.0
    if target.total_shields() > 0:
        return 48.0 + hostile_unit_value(target) * 0.12
    drained = min(5.0, target.current_mana)
    if drained <= 0:
        return -100.0
    return drained * 34.0 + hostile_unit_value(target) * 0.18 + profile.aggressive_bonus


def sacred_duel_score(
    battle: Battle,
    actor: Unit,
    payload: dict[str, Any],
    targets: list[Unit],
    profile: DifficultyProfile,
) -> float:
    target = primary_target_unit(battle, payload, targets)
    if target is None or target.player_id == actor.player_id or target.has_status("神圣决斗"):
        return -100.0
    active_skills = sum(1 for skill in target.skills if skill.timing == "active")
    mobility = max(0.0, target.stat("speed")) * 8.0
    return 72.0 + active_skills * 18.0 + mobility + hostile_unit_value(target) * 0.24 + profile.aggressive_bonus


def payload_step_direction(payload: dict[str, Any]) -> tuple[int, int] | None:
    raw = payload.get("direction")
    if isinstance(raw, dict) and raw.get("dx") is not None and raw.get("dy") is not None:
        return int(raw["dx"]), int(raw["dy"])
    if payload.get("dx") is not None and payload.get("dy") is not None:
        return int(payload["dx"]), int(payload["dy"])
    return None


def score_damage_cells(
    battle: Battle,
    actor: Unit,
    skill: Any,
    payload: dict[str, Any],
    cells: list[Position],
    profile: DifficultyProfile,
    *,
    ignore_shield: bool,
) -> float:
    score = 0.0
    for target in battle.effect_units_at_cells(cells):
        if target.unit_id == actor.unit_id:
            continue
        if target.player_id == actor.player_id:
            score -= friendly_fire_penalty(target)
            continue
        impact = probe_skill_damage_impact(
            battle,
            actor,
            skill,
            payload,
            target,
            actor.stat("attack"),
            cells=cells,
            ignore_shield=ignore_shield,
            half_ignore_shield=False,
        )
        score += impact.damage * 100.0 + hostile_unit_value(target) * 0.45
        if impact.changed_target and impact.damage <= 0:
            score += 35.0
        if impact.damage >= target.current_hp - 1e-9:
            score += 90.0
    return score + profile.aggressive_bonus if score > 0 else score


def zero_dash_score(battle: Battle, actor: Unit, skill: Any, payload: dict[str, Any]) -> float:
    destination = payload_destination(payload)
    if destination is None:
        return -20.0
    try:
        path = battle.find_path(actor, destination, max_distance=8, exact_distance=8, straight_only=True, ignore_units=True)
    except Exception:
        return -20.0
    return zero_path_effect_score(battle, actor, path, via_skill=True)


def reviewed_r16_damage_result(battle: Battle, actor: Unit, operation: Any, *, try_passage_evasion: bool = False) -> float:
    """Evaluate the complete sequence, including defenses consumed and deaths."""
    with ai_probe_rollback(battle):
        before = [(unit, unit.current_hp, unit.total_shields(), unit.dodge_charges, unit.alive)
                  for unit in battle.all_units()]
        mana_before = actor.current_mana
        try:
            operation()
            evasion_used = False
            while battle.pending_chain is not None:
                window = battle.pending_chain
                if try_passage_evasion and not evasion_used and window.queued_action.payload.get("pass_through_damage"):
                    future_cells = {cell for queued in battle.pending_followup_actions for cell in queued.target_cells}
                    hazard_cells = future_cells | set(window.queued_action.target_cells)
                    for unit_id in window.pending_reactor_ids:
                        if not any(option.action_code == "evasion" for option in window.options_by_unit.get(unit_id, [])):
                            continue
                        target = battle.get_unit(unit_id)
                        skill = target.skill_map().get("evasion")
                        if skill is None:
                            continue
                        exits = [cell for cell in skill.evade_cells(battle, target)
                                 if not hazard_cells.intersection(target.footprint_cells_at(cell))]
                        if not exits:
                            continue
                        while battle.pending_chain is not None and battle.pending_chain.current_unit_id() != unit_id:
                            battle.perform_action({"type": "chain_skip"})
                        if battle.pending_chain is not None:
                            exit_cell = exits[0]
                            battle.perform_action({"type": "chain_react", "unit_id": unit_id,
                                                   "action_code": "evasion", "x": exit_cell.x, "y": exit_cell.y})
                            evasion_used = True
                            break
                    if evasion_used:
                        continue
                battle.finalize_reaction_window()
        except ActionError:
            return -1000.0
        score = (actor.current_mana - mana_before) * 24.0
        for unit, hp, shields, dodges, alive in before:
            value = max(0.0, hp - unit.current_hp) * 100.0
            value += max(0, shields - unit.total_shields()) * 20.0 + max(0, dodges - unit.dodge_charges) * 12.0
            if alive and not unit.alive:
                value += 90.0
            score += value if unit.player_id != actor.player_id else -value * 1.3
        return score


def zero_path_effect_score(battle: Battle, actor: Unit, path: list[Position], *, via_skill: bool = False) -> float:
    operation = lambda: battle.move_unit(actor, path[-1], path=path[1:], via_skill=via_skill,
                                         ignore_units=True, max_distance=len(path) - 1)
    baseline = reviewed_r16_damage_result(battle, actor, operation)
    if not any(unit.player_id != actor.player_id and unit.skill_map().get("evasion")
               for unit in battle.path_crossing_units(actor, path)):
        return baseline
    evasion_case = reviewed_r16_damage_result(battle, actor, operation, try_passage_evasion=True)
    return min(baseline, (baseline + evasion_case) / 2)


def blind_paid_move_score(battle: Battle, actor: Unit, destination: Position, profile: DifficultyProfile) -> float:
    if actor.position is None:
        return -1000.0
    before_attack = best_available_attack_score(battle, actor, profile)
    before_position_score = score_move_destination(battle, actor, actor.position, hero_style(actor), profile)
    with ai_probe_rollback(battle):
        before_mana = actor.current_mana
        before_hp = actor.current_hp
        try:
            battle.move_unit(actor, destination)
        except ActionError:
            return -1000.0
        if not actor.alive:
            return -1000.0
        cost = before_mana - actor.current_mana
        attack_gain = best_available_attack_score(battle, actor, profile) - before_attack
        position_gain = score_move_destination(battle, actor, destination, hero_style(actor), profile) - before_position_score
        score = position_gain + max(0.0, attack_gain) * 0.8 - cost * 24.0
        score -= max(0.0, before_hp - actor.current_hp) * 130.0
        if actor.current_mana < 0.5 and any(distance_between_units(battle, actor, enemy) <= enemy.normal_move_distance() + enemy.targeting_range()
                                           for enemy in living_hostile_combatants(battle, actor.player_id)):
            score -= 35.0
        return score if attack_gain > 0 or position_gain > cost * 24.0 else -20.0


def fuma_pursuit_score(
    battle: Battle,
    actor: Unit,
    skill: Any,
    payload: dict[str, Any],
    profile: DifficultyProfile,
) -> float:
    return reviewed_r16_damage_result(battle, actor, lambda: skill.execute(battle, actor, payload))


def fuma_trap_score(battle: Battle, actor: Unit, payload: dict[str, Any], profile: DifficultyProfile) -> float:
    center = payload_destination(payload)
    if center is None:
        return -20.0
    if any(
        getattr(effect, "name", "") == "陷阱"
        and getattr(effect, "source_unit_id", None) == actor.unit_id
        and getattr(effect, "center", None) == center
        for effect in battle.field_effects
    ):
        return -30.0
    from wujiang.tactical.heroes.excel_roster import FumaTrapEffect
    effect = FumaTrapEffect(actor.unit_id, actor.player_id, center)
    score = reviewed_r16_damage_result(battle, actor, lambda: effect.on_any_turn_end(battle, 3 - actor.player_id))
    return score - 12.0


def reviewed_r17_position_value(battle: Battle, actor: Unit, unit: Unit) -> float:
    if unit.position is None or not unit.alive or unit.banished:
        return 0.0
    if unit.player_id == actor.player_id:
        return offensive_reach_score_at(battle, unit, unit.position) * 30.0 - natsume_unit_danger(battle, unit) * 1.4
    return sum(estimate_attack_damage(battle, unit, ally, {}, attack_power=unit.stat("attack")) * 80.0
               for ally in battle.player_units(actor.player_id)
               if ally.position is not None and distance_between_units(battle, unit, ally) <= unit.normal_move_distance() + unit.targeting_range())


def reviewed_r17_effect_score(battle: Battle, actor: Unit, skill: Any, payload: dict[str, Any], profile: DifficultyProfile) -> float:
    with ai_probe_rollback(battle):
        units = list(battle.all_units())
        before = {unit.unit_id: (unit.current_hp, unit.current_mana, unit.total_shields(), unit.dodge_charges,
                                 unit.alive, unit.position, reviewed_r17_position_value(battle, actor, unit))
                  for unit in units}
        try:
            queued = battle.build_queued_action(payload)
            battle.prepay_skill_resources(skill, actor, queued.payload)
            queued.payload["declared_source_attack"] = actor.stat("attack")
            battle.resolving_action = queued
            skill.execute(battle, actor, queued.payload)
        except (ActionError, KeyError, ValueError, TypeError):
            return -1000.0
        score = 0.0
        for unit in units:
            hp, mana, shields, dodges, alive, position, position_value = before[unit.unit_id]
            hp_loss = hp - unit.current_hp
            value = hp_loss * 100.0 + (mana - unit.current_mana) * 24.0
            value += max(0, shields - unit.total_shields()) * 20.0 + max(0, dodges - unit.dodge_charges) * 12.0
            if alive and not unit.alive:
                value += 90.0
            if unit.player_id == actor.player_id:
                score -= value * (1.3 if hp_loss > 0 else 1.1)
            else:
                score += value
            if unit.position != position:
                delta = reviewed_r17_position_value(battle, actor, unit) - position_value
                score += delta if unit.player_id == actor.player_id else -delta
        before_hp = before[actor.unit_id][0]
        if (actor.alive and before_hp < actor.max_health / 2 <= actor.current_hp
                and any(component.name == "不死保留" for component in actor.iter_components())):
            score += 65.0
        return score - 8.0


def fantasy_move_score(
    battle: Battle,
    actor: Unit,
    skill: Any,
    payload: dict[str, Any],
    targets: list[Unit],
    profile: DifficultyProfile,
) -> float:
    return reviewed_r17_effect_score(battle, actor, skill, payload, profile)


def rainbow_mirror_score(
    battle: Battle,
    actor: Unit,
    payload: dict[str, Any],
    targets: list[Unit],
    profile: DifficultyProfile,
) -> float:
    return reviewed_r17_effect_score(battle, actor, actor.get_skill("rainbow_mirror"), payload, profile)


def friendly_mirror_score(battle: Battle, actor: Unit, profile: DifficultyProfile) -> float:
    from wujiang.tactical.heroes.excel_roster import FriendlyMirrorStatus
    if actor.has_status("友好镜"):
        return -1000.0
    risk = r19_incoming_position_risk(battle, actor)
    with ai_probe_rollback(battle):
        actor.add_status(FriendlyMirrorStatus())
        score = max(0.0, risk - r19_incoming_position_risk(battle, actor)) * 1.1
    for enemy in living_hostile_combatants(battle, actor.player_id):
        with ai_probe_rollback(battle):
            battle.turn_number += 1
            battle.active_player, battle._exclusive_turn_unit_id, enemy.turn_ready = enemy.player_id, enemy.unit_id, True
            battle.resolving_action = None
            best = 0.0
            for skill in enemy.skills:
                if skill.timing != "active" or skill.code not in DAMAGING_SKILL_CODES:
                    continue
                try:
                    if not skill.can_use(battle, enemy, {})[0]:
                        continue
                    action = copied_skill_action(battle, enemy, skill)
                    payloads = trim_skill_payloads_for_ai(battle, enemy, skill_payloads_for_action(battle, enemy, action), limit=16)
                    for payload in payloads:
                        if battle.effect_recipient(actor) not in skill_effect_units(battle, enemy, skill, payload):
                            continue
                        losses = []
                        for protected in (False, True):
                            with ai_probe_rollback(battle):
                                if protected:
                                    actor.add_status(FriendlyMirrorStatus())
                                target = battle.effect_recipient(actor)
                                hp = target.current_hp
                                battle.pending_followup_actions.clear()
                                queued = battle.build_queued_action(payload)
                                battle.prepay_skill_resources(skill, enemy, queued.payload)
                                queued.payload["resources_prepaid"] = True
                                enemy.notify_action_declared(battle, "skill", queued.payload)
                                queued.payload["declared_source_attack"] = enemy.stat("attack")
                                battle.resolve_queued_action(queued)
                                while battle.pending_followup_actions:
                                    battle.resolve_queued_action(battle.pending_followup_actions.popleft())
                                losses.append(max(0.0, hp - target.current_hp))
                        best = max(best, (losses[0] - losses[1]) * 110.0)
                except (ActionError, KeyError, ValueError, TypeError):
                    continue
            score += best
    return score if score > 0 else -25.0


def true_blade_air_slash_score(
    battle: Battle,
    actor: Unit,
    skill: Any,
    payload: dict[str, Any],
    profile: DifficultyProfile,
) -> float:
    return reviewed_r17_effect_score(battle, actor, skill, payload, profile)


def undead_boy_devour_score(
    battle: Battle,
    actor: Unit,
    skill: Any,
    payload: dict[str, Any],
    targets: list[Unit],
    profile: DifficultyProfile,
) -> float:
    return reviewed_r17_effect_score(battle, actor, skill, payload, profile)


def reviewed_r18_effect_score(battle: Battle, actor: Unit, skill: Any, payload: dict[str, Any], profile: DifficultyProfile) -> float:
    from wujiang.tactical.heroes.excel_roster import MessengerDamageSkillManaTrait
    with ai_probe_rollback(battle):
        units = list(battle.all_units())
        before = {unit.unit_id: (unit.current_hp, unit.current_mana, unit.total_shields(), unit.dodge_charges,
                                 unit.alive, unit.position, reviewed_r17_position_value(battle, actor, unit),
                                 bool(getattr(unit.get_status("毁灭电击"), "pending", False))) for unit in units}
        points = actor.mana_points
        pressure = 0.0
        try:
            queued = battle.build_queued_action(payload)
            if skill.code == "thor_heavy_hammer":
                for unit_id in queued.target_unit_ids:
                    target = battle.get_unit(unit_id)
                    paid = [target.get_skill(option.action_code).mana_cost_for_payload(battle, target, {})
                            for option in battle.available_reaction_options(target, queued) if option.action_type == "skill"]
                    pressure += max((min(25.0, cost * 16.0) for cost in paid if cost > 0), default=0.0)
            battle.prepay_skill_resources(skill, actor, queued.payload)
            for component in actor.iter_components():
                if isinstance(component, MessengerDamageSkillManaTrait):
                    component.on_owner_action_declared(battle, "skill", queued.payload)
            queued.payload["declared_source_attack"] = actor.stat("attack")
            battle.resolving_action = queued
            skill.execute(battle, actor, queued.payload)
        except (ActionError, KeyError, ValueError, TypeError):
            return -1000.0
        score, changed_enemy = 0.0, False
        for unit in units:
            hp, mana, shields, dodges, alive, position, position_value, pending = before[unit.unit_id]
            hp_loss = hp - unit.current_hp
            value = hp_loss * 100 + (mana - unit.current_mana) * 24
            value += max(0, shields - unit.total_shields()) * 20 + max(0, dodges - unit.dodge_charges) * 12
            if alive and not unit.alive:
                value += 90
            hostile = unit.player_id != actor.player_id
            score += value if hostile else -value * (1.3 if hp_loss > 0 else 1.1)
            changed_enemy |= hostile and (hp_loss > 0 or shields > unit.total_shields())
            if unit.position != position:
                delta = reviewed_r17_position_value(battle, actor, unit) - position_value
                score += -delta if hostile else delta
            if hostile and unit.alive and not pending and getattr(unit.get_status("毁灭电击"), "pending", False):
                score += unit.normal_move_distance() * 4 + (0 if unit.is_clone else unit.attack_actions_per_turn() * 20)
                score += min(30, sum(other.timing == "active" and other.mana_cost <= unit.current_mana for other in unit.skills) * 10)
        if actor.hero_code == "excel_r138" and actor.mana_points > points:
            score += 14.0 if points < 2 <= actor.mana_points else (3.0 if points < 4 else 0.0)
        if changed_enemy:
            score += pressure
        return score - 8.0


def sola_harvest_position_value(battle: Battle, actor: Unit, destination: Position | None) -> float:
    from wujiang.tactical.heroes.excel_roster import SolaHarvestAuraEffect
    if destination is None:
        return 0.0
    with ai_probe_rollback(battle):
        actor.position = destination
        auras = [set(effect.affected_cells(battle)) for effect in battle.field_effects
                 if isinstance(effect, SolaHarvestAuraEffect) and effect.player_id == actor.player_id]
        value = 0.0
        for unit in battle.effect_units(battle.player_units(actor.player_id)):
            if unit.position is None or unit.banished or unit.direct_effects_blocked():
                continue
            if not any(any(cell in cells for cell in battle.unit_cells(unit)) for cells in auras):
                continue
            if not unit.cannot_heal:
                value += min(0.25, max(0.0, unit.max_health - unit.current_hp)) * 130
            value += min(1.0, max(0.0, unit.max_mana() - unit.current_mana)) * 25
        return value


def messenger_refill_opportunity(battle: Battle, actor: Unit, profile: DifficultyProfile) -> float:
    if actor.direct_effects_blocked():
        return 0.0
    with ai_probe_rollback(battle):
        actor.current_mana = actor.max_mana()
        value = 0.0
        for code in ("pierce", "dragon_breath"):
            skill = actor.get_skill(code)
            if not skill.can_use(battle, actor, {})[0]:
                continue
            action = copied_skill_action(battle, actor, skill)
            for payload in skill_payloads_for_action(battle, actor, action):
                value = max(value, reviewed_r18_effect_score(battle, actor, skill, payload, profile))
        protection = actor.get_skill("protection")
        if not actor.cannot_use_skills and protection.cooldown_remaining <= 0:
            target = battle.effect_recipient(actor)
            risk = r19_incoming_position_risk(battle, actor)
            if risk > 0 and not target.direct_effects_blocked():
                with ai_probe_rollback(battle):
                    target.shields += protection.shield_amount
                    prevented = risk - r19_incoming_position_risk(battle, actor)
                if prevented > 0:
                    value = max(value, min(24.0, prevented))
        return value


def messenger_preparation_score(battle: Battle, actor: Unit, code: str, profile: DifficultyProfile) -> float:
    opportunity = messenger_refill_opportunity(battle, actor, profile)
    if opportunity <= 0:
        return -10.0
    if code == "messenger_creation":
        return (35.0 if actor.current_mana <= 1e-9 and actor.mana_points >= 1 else 16.0) if actor.mana_points < 2 else -10.0
    if actor.current_mana > 1e-9 or actor.mana_points < 2:
        return -10.0
    return min(180.0, opportunity) + min(actor.max_mana(), 5.0) * 24.0


def illumination_light_score(
    battle: Battle,
    actor: Unit,
    skill: Any,
    payload: dict[str, Any],
    targets: list[Unit],
    profile: DifficultyProfile,
) -> float:
    return reviewed_r18_effect_score(battle, actor, skill, payload, profile)


def ally_buff_score(battle: Battle, actor: Unit, code: str, target: Unit, profile: DifficultyProfile) -> float:
    target_value = ally_unit_value(target)
    missing_hp = max(0.0, target.max_health - target.current_hp)
    if code == "experiment":
        if target.has_status("实验倒计时") or target.position is None:
            return -1000.0
        enemies = living_hostile_combatants(battle, actor.player_id)
        reachable = [unit for unit in enemies if distance_between_units(battle, target, unit) <= target.normal_move_distance() + target.targeting_range() + 4]
        decisive = False
        if len(enemies) == 1 and battle.unit_belongs_to_current_turn(target) and not target.cannot_attack and target.attacks_used < target.attack_actions_per_turn():
            enemy = enemies[0]
            if distance_between_units(battle, target, enemy) <= target.targeting_range() + 2:
                attack = {"type": "attack", "unit_id": target.unit_id, "target_unit_id": enemy.unit_id}
                ordinary = estimate_attack_damage(battle, target, enemy, attack, attack_power=target.stat("attack"))
                boosted = estimate_attack_damage(battle, target, enemy, attack, attack_power=target.stat("attack") + 2)
                decisive = ordinary < enemy.current_hp <= boosted
        if not reachable or (not target.is_summon and target.current_hp > 0.5 and not decisive):
            return -1000.0
        # A healthy core hero's forced death is not a free positive buff.
        return 45.0 + missing_hp * 55.0 + min(3, len(reachable)) * 16.0 + (180.0 if decisive else 0.0) - (0.0 if target.is_summon else target_value * 0.25)
    if code == "defend_twice":
        if any(status.name == "守*2" and getattr(status, "source_unit_id", None) == actor.unit_id for status in target.statuses):
            return -1000.0
        return target_value * 0.4 + 18.0
    if code == "baptism":
        if target.magic_immunity or target.race != "人类":
            return -1000.0
        return target_value * 0.35 + 10.0
    if code == "chant":
        reserve = max(4.0, max((float(getattr(skill, "mana_point_cost", 0) or 0) for skill in target.skills), default=0.0))
        if target.player_id == actor.player_id and has_mana_point_skill(target) and target.mana_points < reserve:
            return 55.0 + target_value * 0.25
        return -1000.0
    return 12.0


def guardian_finale_opportunities(battle: Battle, actor: Unit) -> tuple[float, float, float, bool]:
    """Price reachable attacks and useful remaining breath casts without declaring actions."""
    enemies = living_hostile_combatants(battle, actor.player_id)
    if actor.position is None or not enemies:
        return 0.0, 0.0, 0.0, False
    attack_value = recovery = breath_value = 0.0
    can_finish = False
    origin = actor.position
    destinations = [origin]
    if not actor.cannot_move and not actor.cannot_normal_move and actor.remaining_normal_move_distance(battle) > 0:
        destinations.extend(battle.reachable_positions(actor, max_distance=actor.remaining_normal_move_distance(battle), use_movement_cost=True))
    leap = next((skill for skill in actor.skills if skill.code == "fly_leap"), None)
    if leap is not None and leap.can_use(battle, actor, {})[0]:
        destinations.extend(preview_positions(leap.preview(battle, actor).get("cells")))
    try:
        if actor.attacks_used < actor.attack_actions_per_turn() and not actor.cannot_attack:
            # Stat/defense values can depend on position, so evaluate each legal endpoint.
            for destination in dict.fromkeys(destinations):
                actor.position = destination
                for enemy in enemies:
                    if not battle.attack_target_allowed(actor, enemy)[0]:
                        continue
                    payload = {"type": "attack", "unit_id": actor.unit_id, "target_unit_id": enemy.unit_id}
                    damage = estimate_attack_damage(battle, actor, enemy, payload, attack_power=battle.basic_attack_preview_power(actor, payload))
                    if damage <= 0:
                        continue
                    value = min(enemy.current_hp, damage) * 100.0 + (90.0 if damage >= enemy.current_hp else 0.0)
                    attack_value = max(attack_value, value)
                    if actor.has_status("终结") and not actor.cannot_heal and not enemy.is_clone:
                        recovery = max(recovery, min(0.25, max(0.0, actor.max_health - actor.current_hp)))
                    can_finish |= len(enemies) == 1 and damage >= enemy.current_hp
        actor.position = origin
        breath = next((skill for skill in actor.skills if skill.code == "dragon_breath"), None)
        if breath is not None and breath.can_use(battle, actor, {})[0]:
            # Area recipients, friendly fire and effective damage all use battle rules.
            for cells in breath.patterns(battle, actor):
                payload = {"type": "skill", "unit_id": actor.unit_id, "skill_code": breath.code, "cells": [cell.to_dict() for cell in cells]}
                value = 0.0
                killed = set()
                for target in battle.effect_units_at_cells(cells, ignore=actor):
                    damage = estimate_skill_damage(battle, actor, breath, payload, target, actor.stat("attack"), cells=cells,
                                                   ignore_shield=breath.ignores_shield_for_payload(battle, actor, payload),
                                                   half_ignore_shield=breath.half_ignores_shield_for_payload(battle, actor, payload))
                    loss = min(target.current_hp, damage)
                    if target.player_id == actor.player_id:
                        value -= loss * 150.0 + (110.0 if loss >= target.current_hp else 0.0)
                    else:
                        value += loss * 100.0 + (90.0 if loss >= target.current_hp else 0.0)
                        if loss >= target.current_hp:
                            killed.add(target.unit_id)
                breath_value = max(breath_value, value)
                can_finish |= bool(enemies) and all(enemy.unit_id in killed for enemy in enemies)
    finally:
        actor.position = origin
    return attack_value, recovery, breath_value, can_finish


def guardian_finale_score(battle: Battle, actor: Unit, profile: DifficultyProfile) -> float:
    if actor.has_status("终结") or actor.direct_effects_blocked():
        return -1000.0
    with ai_probe_rollback(battle):
        before_attack, _, before_breath, _ = guardian_finale_opportunities(battle, actor)
        actor.get_skill("guardian_finale").execute(battle, actor, {})
        if not actor.has_status("终结"):
            return -1000.0
        after_attack, recovery, after_breath, can_finish = guardian_finale_opportunities(battle, actor)
        if max(after_attack, after_breath) <= 0:
            return -1000.0
        if actor.current_hp + recovery <= 0.25 and not can_finish:
            return -1000.0
        # The ongoing attack window sustains the irreversible upkeep; skill damage cannot heal.
        score = max(0.0, after_attack - before_attack) + max(0.0, after_breath - before_breath) * 0.6
        score += after_attack * 0.55 + recovery * 120.0 - 35.0
        if after_attack <= 0:
            score -= 35.0
        if actor.current_hp <= 0.5 and recovery <= 0 and not can_finish:
            score -= 45.0
        if can_finish:
            score += 50.0
        return score if score >= profile.once_per_battle_threshold else -10.0


def agency_contract_score(battle: Battle, actor: Unit, payload: dict[str, Any], profile: DifficultyProfile) -> float:
    skill = actor.get_skill("agency_contract")
    enemies = living_hostile_combatants(battle, actor.player_id)
    if actor.has_status("代行契约附着"):
        before_hp = {unit.unit_id: unit.current_hp for unit in enemies}
        before_mana = actor.current_mana
        with ai_probe_rollback(battle):
            declared = dict(payload, **skill.queued_payload_metadata(battle, actor, payload))
            skill.execute(battle, actor, declared)
            loss = sum(max(0, before_hp[unit.unit_id] - unit.current_hp) for unit in enemies)
            kills = sum(before_hp[unit.unit_id] > 0 and not unit.alive for unit in enemies)
            gain = max(0.0, actor.current_mana - before_mana)
            risk = sum(estimate_damage(battle, actor, enemy.stat("attack")) for enemy in enemies
                       if enemy.alive and actor.position is not None and distance_between_units(battle, actor, enemy) <= enemy.targeting_range() + enemy.normal_move_distance())
            return loss * 100.0 + kills * 90.0 + gain * 22.0 - risk * 70.0 - 30.0
    try:
        target, stat, copied = skill.validate_binding(battle, actor, payload)
    except (ActionError, KeyError, ValueError, TypeError):
        return -1000.0
    before_stat = actor.stat(stat)
    current_risk = sum(estimate_damage(battle, actor, enemy.stat("attack")) for enemy in enemies
                       if distance_between_units(battle, actor, enemy) <= enemy.targeting_range() + enemy.normal_move_distance())
    with ai_probe_rollback(battle):
        skill.execute(battle, actor, dict(payload, agency_cancel=False))
        wrapper = actor.get_skill("agency_borrowed_skill")
        best_copy = 0.0
        if not wrapper.movement_blocked(copied) and copied.code not in SELF_BUFF_SKILL_CODES:
            with wrapper.copying(actor, copied):
                action = copied_skill_action(battle, actor, copied)
                for inner in skill_payloads_for_action(battle, actor, action):
                    if copied.can_use(battle, actor, inner)[0] and payload_is_legal(battle, inner):
                        best_copy = max(best_copy, score_skill_payload(battle, actor, action, inner, profile, instant_only=False))
        attacks = [score_attack_payload(battle, actor, {"type": "attack", "unit_id": actor.unit_id, "target_unit_id": enemy.unit_id}, profile)
                   for enemy in enemies if battle.attack_target_allowed(actor, enemy)[0]]
        offense = max([best_copy, *attacks, 0.0])
        stat_gain = actor.stat(stat) - before_stat
        if offense <= 0 and current_risk <= 0:
            return -1000.0
        return offense * 0.65 + current_risk * 75.0 + stat_gain * 5.0 - 18.0


def natsume_unit_danger(battle: Battle, unit: Unit) -> float:
    if not unit.alive or unit.position is None:
        return 0.0
    damage = 0.0
    for enemy in living_hostile_combatants(battle, unit.player_id):
        if not battle.unit_can_be_selected(enemy, actor=unit)[0] or enemy.cannot_attack:
            continue
        movement = 0 if enemy.cannot_move or enemy.cannot_normal_move else enemy.normal_move_distance()
        if distance_between_units(battle, unit, enemy) <= movement + enemy.targeting_range():
            damage += estimate_attack_damage(battle, enemy, unit, {}, attack_power=enemy.stat("attack"))
    return min(unit.current_hp, damage) * 45.0 + (70.0 if damage >= unit.current_hp and damage > 0 else 0.0)


def natsume_support_attack_score(battle: Battle, actor: Unit, payload: dict[str, Any]) -> float:
    target = battle.units.get(str(payload.get("target_unit_id") or ""))
    if (target is None or target.player_id != actor.player_id
            or not any(component.name == "风壁赠予" for component in actor.iter_components())):
        return -1000.0
    target = battle.effect_recipient(target)
    if target.direct_effects_blocked() or not battle.attack_target_allowed(actor, target, payload=payload)[0]:
        return -1000.0
    before_mana, had_mark = target.current_mana, target.has_status("风壁计数点")
    with ai_probe_rollback(battle):
        battle.resolve_attack_damage(actor, target, action_name="友方普攻支援", payload=payload)
        gain = target.current_mana - before_mana
        mark_added = target.has_status("风壁计数点") and not had_mark
        unlock = any(skill.timing in {"active", "instant"} and before_mana < skill.mana_cost <= target.current_mana
                     and skill.cooldown_remaining <= 0 for skill in target.skills)
    value = gain * 42.0 + (18.0 if gain > 0 and unlock else 0.0)
    protectors = [unit for unit in battle.player_units(actor.player_id) if "natsume_wind_wall" in unit.skill_map()
                  and unit.alive and not unit.banished and unit.current_mana >= 1 and not unit.cannot_use_skills]
    if mark_added and protectors:
        pressure = natsume_unit_danger(battle, target)
        if pressure > 0:
            value += min(60.0, 25.0 + pressure * 0.5)
            if all(not battle.unit_target_in_range_and_line(unit, target, unit.targeting_range()) for unit in protectors):
                value += 18.0
    return value if value > 0 else -20.0


def natsume_skill_score(battle: Battle, actor: Unit, skill: Any, payload: dict[str, Any]) -> float:
    target = battle.units.get(str(payload.get("target_unit_id") or ""))
    target = battle.effect_recipient(target) if target is not None else None
    danger_before = natsume_unit_danger(battle, target) if target is not None else 0.0
    caster_danger = natsume_unit_danger(battle, actor) if target is not None and target.player_id != actor.player_id else 0.0
    with ai_probe_rollback(battle):
        before = red_outcome_snapshot(battle)
        hidden = {unit.unit_id for unit in battle.all_units() if unit.has_status("隐身")}
        declared = {**payload, **skill.queued_payload_metadata(battle, actor, payload)}
        skill.execute(battle, actor, declared)
        value = red_outcome_value(actor, before)
        for unit, hp, _, _, _, _ in before.values():
            if unit.player_id != actor.player_id:
                value -= max(0.0, unit.current_hp - hp) * 110.0
            if unit.alive and unit.unit_id in hidden and not unit.has_status("隐身"):
                value += 65.0 if unit.player_id != actor.player_id else -85.0
        if target is not None and target.alive:
            danger_after = natsume_unit_danger(battle, target)
            value += danger_before - danger_after if target.player_id == actor.player_id else (danger_after - danger_before) * 0.5
            if target.player_id != actor.player_id:
                value += caster_danger - natsume_unit_danger(battle, actor)
    return value if value > 0 else -20.0


def natsume_wall_score(battle: Battle, actor: Unit, queued_action: QueuedAction, payload: dict[str, Any]) -> float:
    from dataclasses import replace
    from wujiang.tactical.heroes.excel_roster import WindWallBlockStatus
    skill = actor.get_skill("natsume_wind_wall")
    if not skill.can_react_with_payload(battle, actor, queued_action, payload)[0]:
        return -1000.0
    ids = payload.get("target_unit_ids") or [payload.get("target_unit_id")]
    target = battle.units.get(str(ids[0] or ""))
    if target is None:
        targets = skill._selectable_targets(battle, actor, queued_action)
        target = targets[0] if len(targets) == 1 else None
    if target is None or target.unit_id in queued_shield_protected_unit_ids(battle):
        return -1000.0
    values = []
    for protected in (False, True):
        with ai_probe_rollback(battle):
            before = red_outcome_snapshot(battle)
            was_banished, previous_position = target.banished, target.position
            danger = natsume_unit_danger(battle, target)
            if protected:
                target.add_status(WindWallBlockStatus(), source=actor)
            battle.resolve_queued_action(replace(queued_action, payload=deepcopy(queued_action.payload)))
            value = red_outcome_value(actor, before)
            for unit, _, _, _, names, _ in before.values():
                if unit.player_id == actor.player_id:
                    current = {status.name for status in unit.statuses if status.name != "风壁"}
                    value -= len(current - names) * 45.0
                    value -= len(names - current) * 25.0
            if not was_banished and target.banished:
                value -= 110.0
            if target.alive and target.position != previous_position:
                value += danger - natsume_unit_danger(battle, target)
            values.append(value)
    gain = values[1] - values[0]
    return gain - 18.0 if gain > 0 else -1000.0


def natsume_position_plan_value(battle: Battle, actor: Unit) -> float:
    if not actor.can_take_turn_actions(battle):
        return 0.0
    best_attack = 0.0
    if not actor.cannot_attack and actor.attacks_used < actor.attack_actions_per_turn():
        preview = battle.basic_attack_preview_for_payload(actor, {})
        for payload in attack_payloads_for_action(battle, actor, {"preview": preview}):
            target = battle.units.get(str(payload.get("target_unit_id")))
            if target is not None and battle.attack_target_allowed(actor, target, payload=payload)[0]:
                best_attack = max(best_attack, natsume_support_attack_score(battle, actor, payload) if target.player_id == actor.player_id else red_attack_value(battle, actor, payload))
    word = actor.skill_map().get("natsume_wind_word")
    best_word = 0.0
    if word is not None and word.can_use(battle, actor)[0]:
        for target in word.targets(battle, actor):
            if target.player_id == actor.player_id and (target.current_hp < target.max_health or natsume_unit_danger(battle, target) > 0):
                best_word = max(best_word, natsume_skill_score(battle, actor, word, {"target_unit_id": target.unit_id}))
    return best_attack + best_word


def natsume_shensu_score(battle: Battle, actor: Unit) -> float:
    if actor.position is None or actor.move_used or actor.cannot_move or actor.cannot_normal_move or not actor.get_skill("shensu").can_use(battle, actor)[0]:
        return -1000.0
    if actor.current_mana < 2 and any(natsume_unit_danger(battle, ally) > 0 for ally in battle.player_units(actor.player_id)):
        return -20.0
    def reachable_value():
        origin, best = actor.position, 0.0
        try:
            positions = [origin, *battle.reachable_positions(actor, max_distance=actor.remaining_normal_move_distance(battle), use_movement_cost=True)]
            for position in positions:
                actor.position = position
                best = max(best, natsume_position_plan_value(battle, actor) - natsume_unit_danger(battle, actor) - origin.distance_to(position) * 2.0)
        finally:
            actor.position = origin
        return best
    before = reachable_value()
    with ai_probe_rollback(battle):
        actor.get_skill("shensu").execute(battle, actor, {})
        after = reachable_value()
    return after + 20.0 if after - before > 18.0 else -20.0


def fusion_position_risk(battle: Battle, actor: Unit) -> float:
    if not actor.alive or actor.position is None:
        return 0.0
    incoming = 0.0
    for enemy in living_hostile_combatants(battle, actor.player_id):
        if not battle.unit_can_be_selected(enemy, actor=actor)[0]:
            continue
        distance = distance_between_units(battle, actor, enemy)
        movement = 0 if enemy.cannot_move or enemy.cannot_normal_move else enemy.normal_move_distance()
        if not enemy.cannot_attack and distance <= movement + enemy.targeting_range():
            incoming += estimate_attack_damage(battle, enemy, actor, {}, attack_power=enemy.stat("attack")) * enemy.attack_actions_per_turn()
        if not actor.magic_immunity and not enemy.cannot_use_skills and distance <= movement + max(2, enemy.targeting_range()):
            if any(skill.code in DAMAGING_SKILL_CODES and skill.timing in {"active", "instant"}
                   and enemy.current_mana >= skill.mana_cost and skill.cooldown_remaining <= 0 for skill in enemy.skills):
                incoming += estimate_damage(battle, actor, enemy.stat("attack"))
    risk = min(actor.current_hp, incoming) * 70.0
    if incoming >= actor.current_hp and incoming > 0:
        risk += 90.0
        # Price collateral from a real death event, including chained explosions.
        # Enemy collateral can offset friendly collateral, but never makes suicide a reward.
        with ai_probe_rollback(battle):
            before = red_outcome_snapshot(battle)
            before.pop(actor.unit_id, None)
            actor.current_hp, actor.alive = 0.0, False
            battle.cleanup_dead_units()
            risk += max(0.0, -red_outcome_value(actor, before))
    return risk


def fusion_attack_value(battle: Battle, actor: Unit, payload: dict[str, Any]) -> float:
    with ai_probe_rollback(battle):
        before = red_outcome_snapshot(battle)
        resolved = battle.resolved_basic_attack_payload(actor, payload)
        cells = battle.basic_attack_area_cells_for_payload(actor, resolved)
        if cells is None:
            return -1000.0
        battle.basic_area_attack_with_payload(actor, resolved, cells)
        return red_outcome_value(actor, before) - fusion_position_risk(battle, actor)


def fusion_attack_plan_value(battle: Battle, actor: Unit, *, allow_move: bool = True) -> float:
    if (actor.position is None or not actor.can_take_turn_actions(battle) or actor.cannot_attack
            or actor.attacks_used >= actor.attack_actions_per_turn()):
        return 0.0
    origin, best = actor.position, 0.0
    destinations = [origin]
    if (allow_move and not actor.cannot_move and not actor.cannot_normal_move
            and actor.remaining_normal_move_distance(battle) > 0 and not actor.move_used):
        destinations += battle.reachable_positions(actor, max_distance=actor.remaining_normal_move_distance(battle), use_movement_cost=True)
    try:
        for destination in sorted(set(destinations), key=lambda cell: (cell.distance_to(origin), cell.y, cell.x)):
            actor.position = destination
            preview = battle.basic_attack_preview_for_payload(actor, {})
            seen = set()
            for payload in attack_payloads_for_action(battle, actor, {"preview": preview}):
                resolved = battle.resolved_basic_attack_payload(actor, payload)
                cells = battle.payload_positions(resolved, "attack_cells")
                key = tuple((cell.x, cell.y) for cell in cells)
                if key in seen:
                    continue
                seen.add(key)
                try:
                    value = fusion_attack_value(battle, actor, payload)
                except ActionError:
                    continue
                best = max(best, value - origin.distance_to(destination) * 2.0)
    finally:
        actor.position = origin
    return best


def fusion_preparation_score(battle: Battle, actor: Unit, code: str) -> float:
    skill = actor.get_skill(code)
    if not skill.can_use(battle, actor)[0] or actor.direct_effects_blocked():
        return -1000.0
    if code == "shensu" and (actor.move_used or actor.cannot_move or actor.cannot_normal_move):
        return -1000.0
    before = fusion_attack_plan_value(battle, actor)
    cost = skill.mana_cost_for_payload(battle, actor, {})
    reserve_penalty = 60.0 if actor.current_mana - cost < 0.5 and fusion_position_risk(battle, actor) > 0 else 0.0
    with ai_probe_rollback(battle):
        skill.execute(battle, actor, {})
        after = fusion_attack_plan_value(battle, actor)
    gain = after - before - cost * 18.0 - reserve_penalty
    return after + 25.0 if gain > 4.0 else -20.0


def fusion_pierce_score(battle: Battle, actor: Unit, payload: dict[str, Any]) -> float:
    skill = actor.get_skill("pierce")
    cost = skill.mana_cost_for_payload(battle, actor, payload)
    with ai_probe_rollback(battle):
        before = red_outcome_snapshot(battle)
        skill.execute(battle, actor, payload)
        value = red_outcome_value(actor, before)
        # A killing strike removes its threat before deciding whether to save evasion mana.
        reserve_penalty = 80.0 if actor.current_mana - cost < 0.5 and fusion_position_risk(battle, actor) > 0 else 0.0
    return value - cost * 18.0 - reserve_penalty


def red_outcome_snapshot(battle: Battle) -> dict[str, tuple]:
    return {unit.unit_id: (unit, unit.current_hp, unit.current_mana, unit.total_shields(),
                           {status.name for status in unit.statuses}, unit.alive)
            for unit in battle.all_units() if unit.alive and unit.position is not None and not unit.banished}


def red_outcome_value(actor: Unit, before: dict[str, tuple]) -> float:
    value = 0.0
    for unit, hp, mana, shields, statuses, alive in before.values():
        enemy = unit.player_id != actor.player_id
        loss = max(0.0, hp - unit.current_hp)
        impact = loss * 100.0 + (90.0 if alive and not unit.alive else 0.0)
        impact += max(0, shields - unit.total_shields()) * 14.0
        impact += max(0.0, mana - unit.current_mana) * 18.0
        if enemy:
            impact += len({status.name for status in unit.statuses} - statuses) * 22.0
        value += impact if enemy else -impact * 1.4
        if not enemy:
            value += max(0.0, unit.current_hp - hp) * 110.0 + max(0.0, unit.current_mana - mana) * 24.0
            if "风壁计数点" not in statuses and unit.has_status("风壁计数点"):
                value += 24.0
    return value


def red_attack_value(battle: Battle, actor: Unit, payload: dict[str, Any]) -> float:
    with ai_probe_rollback(battle):
        before = red_outcome_snapshot(battle)
        resolved = battle.resolved_basic_attack_payload(actor, payload)
        cells = battle.basic_attack_area_cells_for_payload(actor, resolved)
        if cells is not None:
            resolved.update(attack_cells=positions_to_payload(cells), area_attack=True)
            targets = [target for target in battle.effect_units_at_cells(cells) if target.player_id != actor.player_id]
        else:
            target = battle.units.get(str(payload.get("target_unit_id") or ""))
            if target is None or not battle.attack_target_allowed(actor, target, payload=payload)[0]:
                return -1000.0
            targets = [battle.effect_recipient(target)]
        contexts = []
        for target in targets:
            ctx = battle.resolve_attack_damage(actor, target, action_name="普攻", tags={"area_attack"} if cells is not None else None, payload=resolved)
            if ctx is not None:
                contexts.append(ctx)
        actor.notify_basic_attack_finished(battle, resolved, contexts, missed=not contexts)
        return red_outcome_value(actor, before)


def red_attack_plan_value(battle: Battle, actor: Unit) -> float:
    remaining = max(0, actor.attack_actions_per_turn() - actor.attacks_used)
    if not remaining or actor.position is None or actor.cannot_attack:
        return 0.0
    origin, best = actor.position, 0.0
    destinations = [origin]
    if not actor.has_status("无限") and not actor.cannot_move and not actor.cannot_normal_move and actor.remaining_normal_move_distance(battle) > 0:
        destinations += battle.reachable_positions(actor, max_distance=actor.remaining_normal_move_distance(battle), use_movement_cost=True)
    # One nearest firing endpoint per visible enemy keeps preparation evaluation bounded.
    pending = {unit.unit_id for unit in living_hostile_combatants(battle, actor.player_id)
               if battle.unit_can_be_selected(unit, actor=actor)[0]}
    seen = set()
    try:
        for destination in sorted(set(destinations), key=lambda cell: (cell.distance_to(origin), cell.y, cell.x)):
            actor.position = destination
            for spec in battle.basic_attack_action_specs(actor):
                action = {**spec, "preview": battle.basic_attack_preview_for_payload(actor, spec.get("attack_payload"))}
                for payload in attack_payloads_for_action(battle, actor, action):
                    target_id = str(payload.get("target_unit_id") or "")
                    if target_id and target_id not in pending:
                        continue
                    cells = battle.basic_attack_area_cells_for_payload(actor, payload)
                    key = tuple((cell.x, cell.y) for cell in cells) if cells is not None else (target_id,)
                    key += (str(payload.get("attack_variant") or "default"),)
                    if key in seen:
                        continue
                    seen.add(key)
                    pending.discard(target_id)
                    best = max(best, red_attack_value(battle, actor, payload) * remaining - origin.distance_to(destination) * 2)
            if not pending:
                break
    finally:
        actor.position = origin
    return best


def weapon_copy_score(battle: Battle, actor: Unit, payload: dict[str, Any], profile: DifficultyProfile) -> float:
    skill = actor.get_skill("weapon_copy")
    if actor.has_status("武装复制") or battle.units.get(str(payload.get("target_unit_id") or "")) not in skill.targets(battle, actor):
        return -1000.0
    before = red_attack_plan_value(battle, actor)
    with ai_probe_rollback(battle):
        skill.execute(battle, actor, payload)
        after = red_attack_plan_value(battle, actor)
        transfer = actor.skill_map().get("weapon_transfer")
        if transfer is not None and transfer.can_use(battle, actor)[0]:
            transfer.execute(battle, actor, {})
            after = max(after, red_attack_plan_value(battle, actor))
        infinite = actor.skill_map().get("infinite")
        if infinite is not None and infinite.can_use(battle, actor)[0]:
            infinite.execute(battle, actor, {})
            after = max(after, red_attack_plan_value(battle, actor) - profile.once_per_battle_threshold)
    # Compare with the preparation available without copying as well.
    with ai_probe_rollback(battle):
        transfer = actor.skill_map().get("weapon_transfer")
        if transfer is not None and transfer.can_use(battle, actor)[0]:
            transfer.execute(battle, actor, {})
            before = max(before, red_attack_plan_value(battle, actor))
        infinite = actor.skill_map().get("infinite")
        if infinite is not None and infinite.can_use(battle, actor)[0]:
            infinite.execute(battle, actor, {})
            before = max(before, red_attack_plan_value(battle, actor) - profile.once_per_battle_threshold)
    return after + 40.0 if after - before > 4 else -20.0


def deadly_bow_score(
    battle: Battle, actor: Unit, action: dict[str, Any], payload: dict[str, Any], profile: DifficultyProfile,
) -> float:
    skill = skill_from_ai_action(actor, action, "deadly_bow")
    try:
        declared = {**payload, **skill.queued_payload_metadata(battle, actor, payload)}
    except (ActionError, ValueError, TypeError):
        return -1000.0
    with ai_probe_rollback(battle):
        before = red_outcome_snapshot(battle)
        skill.execute(battle, actor, declared)
        value = red_outcome_value(actor, before)
    return value - skill.mana_cost_for_payload(battle, actor, payload) * 18.0 - actor.mana_points * 7.0


def red_preparation_score(battle: Battle, actor: Unit, code: str, profile: DifficultyProfile) -> float:
    skill = actor.get_skill(code)
    if code == "red_charge":
        if actor.mana_points >= getattr(actor, "max_mana_points", 5) or actor.direct_effects_blocked():
            return -20.0
        bow = actor.skill_map().get("deadly_bow")
        best_before = best_after = -1000.0
        if bow is not None and bow.can_use(battle, actor)[0]:
            for direction in bow.DIRECTIONS:
                payload = {"direction": direction}
                best_before = max(best_before, deadly_bow_score(battle, actor, {"code": bow.code}, payload, profile))
                with ai_probe_rollback(battle):
                    actor.gain_mana_points(1)
                    best_after = max(best_after, deadly_bow_score(battle, actor, {"code": bow.code}, payload, profile))
        return max(28.0, best_after + 30.0) if best_after > best_before + 3 else 24.0
    if code in {"infinite_armor", "infinite_robe"}:
        if (code == "infinite_armor" and (actor.physical_immunity or actor.has_status("无限铠甲"))
                or code == "infinite_robe" and actor.magic_immunity):
            return -20.0
        def exposure():
            result = 0.0
            for enemy in living_hostile_combatants(battle, actor.player_id):
                distance = distance_between_units(battle, actor, enemy)
                if code == "infinite_armor":
                    if not enemy.cannot_attack and distance <= enemy.targeting_range() + enemy.normal_move_distance():
                        result += estimate_attack_damage(battle, enemy, actor, {}, attack_power=enemy.stat("attack")) * enemy.attack_actions_per_turn()
                elif distance <= max(5, enemy.targeting_range()) + enemy.normal_move_distance() and not enemy.cannot_use_skills:
                    for incoming in enemy.skills:
                        if (incoming.code in DAMAGING_SKILL_CODES and incoming.cooldown_remaining == 0
                                and (incoming.max_uses_per_battle is None or incoming.uses_this_battle < incoming.max_uses_per_battle)
                                and enemy.current_mana >= incoming.mana_cost):
                            result += estimate_skill_damage(battle, enemy, incoming, {}, actor, enemy.stat("attack"), cells=[],
                                                            ignore_shield=incoming.ignores_shield_for_payload(battle, enemy, {}),
                                                            half_ignore_shield=False)
            return result
        before = exposure()
        with ai_probe_rollback(battle):
            skill.execute(battle, actor, {})
            saved = max(0.0, before - exposure())
        return saved * 130.0 + (90.0 if saved >= actor.current_hp and saved > 0 else 0.0) - 35.0
    before = red_attack_plan_value(battle, actor)
    with ai_probe_rollback(battle):
        skill.execute(battle, actor, {})
        after = red_attack_plan_value(battle, actor)
        if code == "weapon_transfer":
            infinite = actor.skill_map().get("infinite")
            if infinite is not None and infinite.can_use(battle, actor)[0]:
                infinite.execute(battle, actor, {})
                after = max(after, red_attack_plan_value(battle, actor) - profile.once_per_battle_threshold)
    gain = after - before
    if gain <= 4:
        return -20.0
    return after + (25.0 if code == "weapon_transfer" else 10.0) - (25.0 if code == "infinite" else 0.0)


def wuchang_action_success_probability(battle: Battle, actor: Unit, payload: dict[str, Any]) -> float:
    from wujiang.tactical.heroes.excel_roster import WuchangMistField
    if not battle.has_weather("无常之雾") or not WuchangMistField.affects_actor(actor):
        return 1.0
    if payload.get("type") == "attack":
        return 0.5
    if payload.get("type") != "skill":
        return 1.0
    skill = actor.get_skill(str(payload.get("skill_code") or ""))
    if skill.code == "mimic_skill":
        context = mimic_payload_context(battle, actor, payload)
        skill = context[1] if context is not None else skill
    elif skill.code == "agency_borrowed_skill":
        context = agency_borrowed_payload_context(battle, actor, payload)
        skill = context[1] if context is not None else skill
    return 0.5 if skill.timing == "active" else 1.0


def wuchang_exposed_action_value(battle: Battle, unit: Unit) -> float:
    """Upcoming attack/active opportunities; passive and instant defenses are unaffected."""
    if not unit.alive or unit.banished or unit.position is None or unit.is_clone:
        return 0.0
    attack = 0.0
    opponents = battle.effect_units(living_hostile_combatants(battle, unit.player_id))
    if not unit.cannot_attack:
        with ai_probe_rollback(battle):
            battle.resolving_action = None
            battle.active_player, battle._exclusive_turn_unit_id, unit.turn_ready = unit.player_id, unit.unit_id, True
            unit.attacks_used = unit.normal_move_actions_used = unit.normal_move_steps_used = 0
            unit.move_used = False
            origin = unit.position
            destinations = [origin]
            if not unit.cannot_move and not unit.cannot_normal_move:
                destinations.extend(battle.reachable_positions(
                    unit, max_distance=unit.remaining_normal_move_distance(battle), use_movement_cost=True))
            for destination in dict.fromkeys(destinations):
                unit.position = destination
                for target in opponents:
                    if not battle.attack_target_allowed(unit, target)[0]:
                        continue
                    damage = estimate_attack_damage(battle, unit, target, {}, attack_power=unit.stat("attack"))
                    attack = max(attack, min(target.current_hp, damage) * 100.0)
    active = []
    if not unit.cannot_use_skills:
        with ai_probe_rollback(battle):
            battle.resolving_action = None
            battle.active_player, battle._exclusive_turn_unit_id, unit.turn_ready = unit.player_id, unit.unit_id, True
            for skill in unit.skills:
                if skill.timing != "active":
                    continue
                try:
                    if not skill.can_use(battle, unit, {})[0]:
                        continue
                    if skill.code in DAMAGING_SKILL_CODES or skill.code in CONTROL_SKILL_CODES:
                        action = copied_skill_action(battle, unit, skill)
                        payloads = skill_payloads_for_action(battle, unit, action)
                        payloads = sorted(payloads, key=lambda payload: sum(
                            target.player_id != unit.player_id for target in skill_effect_units(battle, unit, skill, payload)
                        ), reverse=True)
                        if any(skill_payload_has_effective_enemy_impact(battle, unit, action, payload)
                               for payload in payloads):
                            active.append(36.0)
                    elif skill.code in HEAL_SKILL_CODES:
                        missing = max((ally.max_health - ally.current_hp for ally in battle.player_units(unit.player_id)
                                       if ally.alive and not ally.cannot_heal and not ally.direct_effects_blocked()), default=0.0)
                        if missing > 0:
                            active.append(min(0.25, missing) * 100.0)
                    elif skill.code in ALLY_BUFF_SKILL_CODES or skill.code in SELF_BUFF_SKILL_CODES or skill.code in SUMMON_SKILL_CODES:
                        active.append(24.0 if opponents else 0.0)
                except (ActionError, KeyError, TypeError, ValueError):
                    continue
    return attack * unit.attack_actions_per_turn() + sum(sorted(active, reverse=True)[:3])


def wuchang_mist_score(battle: Battle, actor: Unit, profile: DifficultyProfile) -> float:
    from wujiang.tactical.heroes.excel_roster import WuchangMistField
    if battle.has_weather("无常之雾"):
        return -1000.0
    value = 0.0
    for unit in battle.all_units():
        if not WuchangMistField.affects_actor(unit):
            continue
        pressure = wuchang_exposed_action_value(battle, unit) * 0.5
        value += pressure if unit.player_id != actor.player_id else -pressure
    # Permanent weather is worth several upcoming windows; friendly costs are equally real.
    score = value * 2.0 - 15.0
    return score if score >= profile.once_per_battle_threshold else -10.0


def migratory_bird_mark_score(
    battle: Battle, actor: Unit, action: dict[str, Any], payload: dict[str, Any], profile: DifficultyProfile,
) -> float:
    skill = skill_from_ai_action(actor, action, "migratory_bird_mark")
    selected = battle.units.get(str(payload.get("target_unit_id") or ""))
    if selected is None:
        return -1000.0
    target = battle.effect_recipient(selected)
    hp, old = target.current_hp, target.get_status("侯鸟标记")
    previous_duration = old.duration if old is not None else 0
    exposure = 0.0 if getattr(target, "hero_code", "") == "excel_r027" else wuchang_exposed_action_value(battle, target)
    with ai_probe_rollback(battle):
        skill.execute(battle, actor, payload)
        damage = max(0.0, hp - target.current_hp)
        marker = target.get_status("侯鸟标记")
        extra_duration = max(0, (marker.duration if marker is not None else 0) - (previous_duration or 0))
        immunity_value = exposure * 0.5 * extra_duration if target.alive and battle.has_weather("无常之雾") else 0.0
        if target.player_id == actor.player_id:
            return immunity_value - damage * 140.0 - (180.0 if not target.alive else 0.0) - 12.0
        return damage * 100.0 + (95.0 if not target.alive else 0.0) - immunity_value - 8.0


def wuchang_split_score(battle: Battle, actor: Unit, profile: DifficultyProfile) -> float:
    if best_available_attack_score(battle, actor, profile) >= profile.action_threshold:
        return -1000.0
    for code in ("migratory_bird_mark", "remote_dragon_breath", "wuchang_mist"):
        skill = actor.get_skill(code)
        if not skill.can_use(battle, actor, {})[0]:
            continue
        action = copied_skill_action(battle, actor, skill)
        for payload in skill_payloads_for_action(battle, actor, action):
            if payload_is_legal(battle, payload) and score_skill_payload(battle, actor, action, payload, profile, instant_only=False) >= profile.action_threshold:
                return -1000.0
    threatened = any(distance_between_units(battle, actor, enemy) <= enemy.normal_move_distance() + enemy.targeting_range()
                     for enemy in living_hostile_combatants(battle, actor.player_id))
    return 26.0 + max(0.0, actor.max_health - actor.current_hp) * 30.0 if threatened else -10.0


def self_buff_score(battle: Battle, actor: Unit, code: str, profile: DifficultyProfile) -> float:
    enemies = [unit for unit in battle.enemy_units(actor.player_id) if unit.alive and unit.position is not None and not unit.banished]
    if code in {"mountain_awakening", "mountain_god_muro", "mountain_escape"}:
        return r12_preparation_score(battle, actor, code, profile)
    if code == "red_heat":
        active = actor.has_status("红热")
        before = li_attack_plan_value(battle, actor)
        with ai_probe_rollback(battle):
            actor.get_skill(code).execute(battle, actor, {})
            after = li_attack_plan_value(battle, actor)
        half_hp_cost = actor.current_hp * 60.0 if not actor.magic_immunity and actor.total_shields() == 0 else 0.0
        gain = after - before + (half_hp_cost if active else -half_hp_cost)
        return 300.0 + gain if gain > 8.0 else -1000.0
    if code == "essence":
        if actor.has_status("精华"):
            return -1000.0
        before = li_attack_plan_value(battle, actor)
        with ai_probe_rollback(battle):
            actor.get_skill(code).execute(battle, actor, {})
            after = li_attack_plan_value(battle, actor)
        if after > before + 1.0:
            return max(best_available_attack_score(battle, actor, profile), after) + 30.0
        return min(2.0, max(0.0, actor.max_mana() - actor.current_mana)) * 24.0 - 12.0
    if code == "stillness":
        missing = max(0.0, actor.max_health - actor.current_hp)
        threatened = any(distance_between_units(battle, actor, enemy) <= enemy.targeting_range() + enemy.normal_move_distance() for enemy in enemies)
        if missing < 0.5 or actor.current_hp > 0.5 or not threatened:
            return -1000.0
        attack = best_available_attack_score(battle, actor, profile)
        return min(0.5, missing) * 200.0 + 75.0 - min(100.0, attack * 0.5)
    if code in {"messenger_reincarnation", "messenger_creation"}:
        return messenger_preparation_score(battle, actor, code, profile)
    if code == "beetle_full_force":
        return r19_preparation_score(battle, actor, code, profile)
    if code == "mountain_awakening":
        counters = sum(1 for status in actor.statuses if status.name == "山神计数点")
        if counters < 8:
            return -8.0
        used = [skill for skill in actor.skills if skill.max_uses_per_battle is not None and skill.uses_this_battle > 0]
        return 120.0 + len(used) * 70.0 if used else -8.0
    if code == "mountain_god_muro":
        if actor.has_status("山神术。室王") or not enemies:
            return -8.0
        costly = sum(skill.mana_cost > 0 and skill.timing == "active" for skill in actor.skills)
        return 75.0 + costly * 12.0 + (28.0 if actor.current_mana <= 1 else 0.0)
    if code == "mountain_escape":
        if actor.has_status("遁术。神山"):
            return -8.0
        missing = max(0.0, actor.max_health - actor.current_hp)
        threatened = any(distance_between_units(battle, actor, enemy) <= enemy.targeting_range() + enemy.normal_move_distance() for enemy in enemies)
        return missing * 130.0 + (65.0 if threatened else -25.0) if threatened or missing > 0.25 else -8.0
    if code == "nian_spirit_pressure":
        if actor.get_status("灵压") is not None or not enemies:
            return -8.0
        in_window = any(distance_between_units(battle, actor, enemy) <= actor.targeting_range() + actor.normal_move_distance()
                        or distance_between_units(battle, actor, enemy) <= enemy.targeting_range() + enemy.normal_move_distance()
                        for enemy in enemies)
        return 84.0 + profile.aggressive_bonus if in_window else -8.0
    if code == "black_cat_form":
        return r14_cat_form_score(battle, actor, profile)
    if code == "big_avalanche":
        if battle.has_weather("大雪崩") or not enemies:
            return -8.0
        from wujiang.tactical.heroes.excel_roster import BigAvalancheWeatherEffect

        net_value = 0.0
        for unit in battle.all_units():
            if not unit.alive or unit.banished or unit.position is None or unit.direct_effects_blocked():
                continue
            ally = unit.player_id == actor.player_id
            if BigAvalancheWeatherEffect.ice_eligible(battle, unit):
                value = 20.0 + min(3.0, unit.stat("speed")) * 4.0 if not unit.cannot_move and not unit.cannot_normal_move else 0.0
                net_value += value if ally else -value
                continue
            usable_active = sum(
                skill.timing == "active"
                and not unit.cannot_use_skills
                and not getattr(skill, "locked", False)
                and skill.cooldown_remaining == 0
                and (skill.max_uses_per_battle is None or skill.uses_this_battle < skill.max_uses_per_battle)
                and skill.mana_cost <= unit.current_mana
                and not any(component.blocks_skill_use(battle, unit, skill)[0] for component in unit.iter_components())
                for skill in unit.skills
            )
            value = (0.0 if unit.cannot_move else min(4.0, unit.stat("speed")) * 9.0) + min(3, usable_active) * 15.0
            net_value += -value if ally else value
        avalanche = next((skill for skill in actor.skills if skill.code == "snow_avalanche"), None)
        if avalanche is not None and not actor.cannot_use_skills and avalanche.cooldown_remaining <= 2:
            net_value += 24.0
        return 24.0 + net_value if net_value > 8.0 else -8.0
    if code == "martial_god_seal":
        if battle.effect_recipient(actor).has_status("魔界武神之印"):
            return -8.0
        return r19_preparation_score(battle, actor, code, profile)
    if code == "pandemonium":
        return r20_weather_cast_score(battle, actor, code, profile)
    if code == "sky_sanctuary":
        return r20_weather_cast_score(battle, actor, code, profile)
    if code == "wetland_grassland":
        return r20_weather_cast_score(battle, actor, code, profile)
    if code == "floating_cannon_berserk":
        from wujiang.tactical.heroes.excel_roster import FloatingCannonStatTrait, floating_cannons_for

        cannons = floating_cannons_for(battle, actor)
        if not cannons or not enemies:
            return -8.0

        protected: dict[str, Unit] = {}
        for ally in battle.player_units(actor.player_id):
            recipient = battle.effect_recipient(ally)
            if not recipient.alive or recipient.position is None or recipient.banished:
                continue
            if any(any(abs(cannon.position.x - cell.x) <= 3 and abs(cannon.position.y - cell.y) <= 3
                       for cell in battle.unit_cells(recipient)) for cannon in cannons):
                protected[recipient.unit_id] = recipient
        cover_value = max((min(160.0, r19_incoming_position_risk(battle, recipient)) * 0.5
                           for recipient in protected.values()), default=0.0)

        def array_value() -> float:
            total = 0.0
            berserk = actor.get_status("浮游炮狂暴化") is not None
            for cannon in cannons:
                if not cannon.turn_ready or cannon.attacks_used >= cannon.attack_actions_per_turn():
                    continue
                trait = next((item for item in cannon.traits if isinstance(item, FloatingCannonStatTrait)), None)
                if trait is None:
                    continue
                if berserk:
                    targets = [target for target in trait.nearest_targets(battle, cannon)
                               if trait.action_route(battle, cannon, target)[1]]
                else:
                    targets = [target for target in enemies
                               if battle.attack_target_allowed(cannon, target, payload={"target_unit_id": target.unit_id})[0]]
                damage = max((estimate_attack_damage(battle, cannon, target, {}, attack_power=cannon.stat("attack"))
                              for target in targets), default=0.0)
                total += damage * max(0, cannon.attack_actions_per_turn() - cannon.attacks_used) * 45.0
                if berserk and not targets and any(trait.action_route(battle, cannon, target)[0]
                                                for target in trait.nearest_targets(battle, cannon)):
                    total += 12.0
            if not berserk:
                total += cover_value
            return total

        before = array_value()
        with ai_probe_rollback(battle):
            actor.get_skill(code).execute(battle, actor, {})
            after = array_value()
        gain = after - before
        return 140.0 + gain if gain > 15.0 else -8.0
    if code == "wuchang_mist":
        return wuchang_mist_score(battle, actor, profile)
    if code == "crystal_ball":
        if actor.has_status("水晶球"):
            return -1000.0
        return 62.0 if any(distance_between_units(battle, actor, enemy) > actor.targeting_range() for enemy in enemies) else -8.0
    if code == "water_wave":
        if actor.has_status("水之波动"):
            return -1000.0
        return 120.0 if any(distance_between_units(battle, actor, enemy) <= actor.targeting_range() + 4 for enemy in enemies) else -8.0
    if code == "headshot":
        if actor.attacks_used >= actor.attack_actions_per_turn() or actor.has_status("爆头强化"):
            return -1000.0
        before = best_available_attack_score(battle, actor, profile)
        with ai_probe_rollback(battle):
            actor.get_skill("headshot").apply_to_self(battle, actor)
            actor.performed_active_skill = True
            after = best_available_attack_score(battle, actor, profile)
        return after + 20.0 if after > before + 1.0 else -1000.0
    if code == "six_blade_style":
        before = masamune_attack_plan_value(battle, actor, profile)
        before_score = best_available_attack_score(battle, actor, profile)
        with ai_probe_rollback(battle):
            actor.get_skill(code).execute(battle, actor, {})
            after = masamune_attack_plan_value(battle, actor, profile)
            after_score = best_available_attack_score(battle, actor, profile)
        return max(before_score, after_score) + 25.0 if after > before + 1.0 else -1000.0
    if code == "form_shift":
        before = best_available_attack_score(battle, actor, profile)
        with ai_probe_rollback(battle):
            actor.get_skill(code).execute(battle, actor, {})
            after = best_available_attack_score(battle, actor, profile)
        adjacent = any(distance_between_units(battle, actor, enemy) <= 1 for enemy in enemies)
        cards = [effect for effect in battle.field_effects if getattr(effect, "owner_unit_id", None) == actor.unit_id and hasattr(effect, "card_type")]
        return after + 25.0 if adjacent and after > before + 15.0 and (len(cards) >= 2 or actor.current_hp <= 0.5 or actor.current_mana < 1.5) else -1000.0
    if code == "into_darkness":
        if actor.has_status("遁入黑暗"):
            return -1000.0
        targets = battle.action_snapshot_for(actor).get("attack_targets") or []
        return 180.0 if targets and actor.attacks_used < actor.attack_actions_per_turn() else (38.0 if enemies else -8.0)
    if code == "stealth":
        return max(profile.action_threshold + 1.0, 34.0 if actor.current_hp <= 0.75 else 18.0)
    if code == "harden":
        return 28.0 if actor.current_hp <= 0.75 else 12.0
    if code == "mech_enhancement":
        return 38.0
    if code == "n_skill":
        if actor.mana_points < 1:
            return -1000.0
        if actor.current_mana < 1:
            return 160.0
        if actor.mana_points <= 2 and any(distance_between_units(battle, actor, enemy) <= enemy.normal_move_distance() + enemy.targeting_range() for enemy in enemies):
            return -20.0
        return 32.0
    if code == "big_shensu" and getattr(actor, "hero_code", "") == "excel_r028":
        return feiwang_shensu_score(battle, actor)
    if code == "shensu" and actor.hero_code == "excel_r352":
        return r22_ninja_shensu_score(battle, actor)
    if code == "shensu" and actor.hero_code == "excel_r031":
        return natsume_shensu_score(battle, actor)
    if code == "shensu" and actor.hero_code == "excel_r030":
        return fusion_preparation_score(battle, actor, code)
    if code in {"shensu", "big_shensu"}:
        enemies = [unit for unit in battle.enemy_units(actor.player_id) if unit.alive and unit.position is not None and not unit.banished]
        if not enemies or actor.move_used:
            return -6.0
        nearest = min(distance_between_units(battle, actor, unit) for unit in enemies)
        bonus = 10.0 if code == "big_shensu" else 0.0
        return (30.0 + bonus) if nearest > actor.normal_move_distance() else (8.0 + bonus)
    if code == "nuclear_rush":
        return fusion_preparation_score(battle, actor, code)
    if code == "inner_dimension_sword":
        return feiwang_stance_score(battle, actor)
    if code == "kings_insight":
        return kings_insight_score(battle, actor, profile)
    return 10.0


def best_available_attack_score(battle: Battle, actor: Unit, profile: DifficultyProfile) -> float:
    return max((candidate.score for action in battle.action_snapshot_for(actor).get("actions", []) if action.get("kind") == "attack" and action.get("available") for candidate in build_attack_candidates(battle, actor, action, profile)), default=0.0)


def field_skill_score(
    battle: Battle,
    actor: Unit,
    code: str,
    targets: list[Unit],
    profile: DifficultyProfile,
) -> float:
    allies = [unit for unit in battle.player_units(actor.player_id) if unit.alive and unit.position is not None and not unit.banished]
    enemies = [unit for unit in battle.enemy_units(actor.player_id) if unit.alive and unit.position is not None and not unit.banished]
    if code == "stance":
        nearby_allies = sum(1 for unit in allies if unit.unit_id != actor.unit_id and distance_between_units(battle, actor, unit) <= 3)
        nearby_enemies = sum(1 for unit in enemies if distance_between_units(battle, actor, unit) <= 4)
        return nearby_allies * 24.0 + nearby_enemies * 10.0 if nearby_allies else -1000.0
    if code == "great_holy_light":
        nearby_enemies = sum(1 for unit in enemies if not unit.cannot_normal_move and distance_between_units(battle, actor, unit) <= 5)
        if not nearby_enemies:
            return -8.0
        nearby_allies = sum(1 for unit in allies if distance_between_units(battle, actor, unit) <= 5)
        return nearby_enemies * 16.0 + nearby_allies * 10.0
    if code == "plant_growth":
        enemy_hits = sum(1 for unit in targets if unit.player_id != actor.player_id and not unit.cannot_normal_move)
        ally_hits = sum(1 for unit in targets if unit.player_id == actor.player_id and not unit.cannot_normal_move)
        return enemy_hits * 24.0 - ally_hits * 28.0 - 8.0
    if code == "smoke_spray":
        enemy_hits = len([unit for unit in targets if unit.player_id != actor.player_id])
        ally_hits = len([unit for unit in targets if unit.player_id == actor.player_id])
        return enemy_hits * 26.0 - ally_hits * 18.0 + 8.0
    return 6.0


def square_cells_around_position(battle: Battle, center: Position, *, radius: int) -> list[Position]:
    return [
        Position(x, y)
        for y in range(center.y - radius, center.y + radius + 1)
        for x in range(center.x - radius, center.x + radius + 1)
        if battle.in_bounds(Position(x, y))
    ]


def judgment_stone_collision_move_score(
    battle: Battle, actor: Unit, destination: Position, profile: DifficultyProfile,
) -> float:
    if not any(unit.player_id != actor.player_id for unit in battle.units_at(destination)):
        return 0.0
    cells = square_cells_around_position(battle, destination, radius=2)
    summoner = battle.units.get(getattr(actor, "summoner_id", ""))
    if summoner is not None and summoner.alive and any(cell in cells for cell in battle.unit_cells(summoner)):
        return -1000.0
    units = [unit for unit in battle.all_units() if unit is not actor]
    before = {unit.unit_id: (unit.current_hp, unit.alive) for unit in units}
    with ai_probe_rollback(battle):
        actor.position = destination
        from wujiang.tactical.heroes.excel_roster import apply_judgment_stone_explosion
        apply_judgment_stone_explosion(battle, actor, cells)
        score = -12.0
        for unit in units:
            hp, alive = before[unit.unit_id]
            loss = max(0.0, hp - unit.current_hp)
            death = alive and not unit.alive
            value = loss * 100.0 + (hostile_unit_value(unit) * 0.6 + 100.0 if death else 0.0)
            score += value if unit.player_id != actor.player_id else -value * 1.5
        return score


def judgment_stone_score(
    battle: Battle, actor: Unit, payload: dict[str, Any], profile: DifficultyProfile,
) -> float:
    destination = payload_destination(payload)
    if destination is None:
        return -1000.0
    from wujiang.tactical.heroes.excel_roster import JudgmentStoneSummon, alive_world_root_numbers
    stone = JudgmentStoneSummon(actor.player_id, actor.unit_id)
    with ai_probe_rollback(battle):
        if destination not in actor.get_skill("judgment_stone").summon_cells(battle, actor):
            return -1000.0
        battle.add_unit(stone, destination)
        reachable = set(battle.reachable_positions(stone, max_distance=6, ignore_units=True))
        targets = {cell for enemy in living_hostile_combatants(battle, actor.player_id) for cell in battle.unit_cells(enemy)}
        best = max((judgment_stone_collision_move_score(battle, stone, cell, profile) for cell in targets & reachable), default=-1000.0)
        ready = 2 in alive_world_root_numbers(battle, actor)
        cost = actor.get_skill("judgment_stone").mana_cost_for_payload(battle, actor, payload)
        if best > 0:
            return best * (0.85 if ready else 0.4) - cost * 22.0
        distance = min((destination.distance_to(cell) for cell in targets), default=999)
        waiting = sum(1 for unit in battle.all_units() if unit is not stone and unit.hero_code == "judgment_stone" and unit.summoner_id == actor.unit_id)
        return (18.0 - distance - waiting * 15.0 - cost * 30.0) if distance <= 10 else -1000.0


def world_seed_score(
    battle: Battle, actor: Unit, payload: dict[str, Any], profile: DifficultyProfile,
) -> float:
    skill = actor.get_skill("world_seed")
    try:
        anchor = skill.anchor(payload)
        edges = skill._edges(payload)
        numbers = skill._numbers(payload, edges)
        if not skill.legal_layout(battle, anchor, edges, numbers):
            return -1000.0
    except (ActionError, KeyError, ValueError):
        return -1000.0
    enemies = living_hostile_combatants(battle, actor.player_id)
    if not enemies:
        return -1000.0
    body = [anchor.offset(dx, dy) for dy in range(5) for dx in range(5)]
    distance = lambda unit, cells: min(a.distance_to(b) for a in battle.unit_cells(unit) for b in cells)
    nearest = min(distance(enemy, body) for enemy in enemies)
    score = 25.0 + max(0.0, 7.0 - nearest) * 6.0
    score += sum(16.0 for enemy in enemies if distance(enemy, body) <= 2)
    for number, _, _, cells in skill.root_placements(anchor, edges, numbers):
        exposure = sum(max(0.0, enemy.targeting_range() + min(4, enemy.stat("speed")) + 1 - distance(enemy, cells)) for enemy in enemies)
        score -= exposure * (2.5 if number == 2 else 2.0 if number == 3 else 1.0)
    score -= sum(2.0 for ally in battle.player_units(actor.player_id) if ally is not actor and not getattr(ally, "has_flying", False) and distance(ally, body) <= 1)
    return score


def feiwang_attack_value(battle: Battle, actor: Unit, target: Unit, payload: dict[str, Any]) -> float:
    from wujiang.tactical.heroes.common import is_mana_drain_immune
    recipients = [battle.effect_recipient(target)]
    if actor.has_status("里次元大剑"):
        cells = battle.basic_attack_area_cells_for_payload(actor, payload) or battle.unit_cells(target)
        recipients = battle.effect_units_at_cells(cells)
    score, room = 0.0, max(0.0, actor.max_mana() - actor.current_mana)
    for recipient in recipients:
        if recipient.player_id == actor.player_id:
            continue
        damage = estimate_attack_damage(battle, actor, recipient, {**payload, "attack_cells": [], "area_attack": True},
                                        attack_power=battle.basic_attack_preview_power(actor, payload), area_cell_hits=1)
        score += min(recipient.current_hp, damage) * 100.0
        if damage >= recipient.current_hp and damage > 0:
            score += 95.0 + (22.0 if not recipient.is_clone and not recipient.is_summon else 0.0)
        if (damage > 0 and not recipient.is_clone and not is_mana_drain_immune(recipient)
                and any(trait.name == "攻击吸魔" for trait in actor.traits)):
            drain = min(1.0, recipient.current_mana)
            gain = min(room, drain)
            room -= gain
            score += drain * 14.0 + gain * 26.0
        elif recipient.total_shields() > 0 and not recipient.physical_immunity:
            score += 12.0
    return score


def feiwang_attack_plan_value(battle: Battle, actor: Unit) -> float:
    if actor.position is None or actor.cannot_attack or actor.attacks_used >= actor.attack_actions_per_turn():
        return 0.0
    enemies = living_hostile_combatants(battle, actor.player_id)
    origin, best = actor.position, 0.0
    destinations = [origin]
    if not actor.cannot_move and not actor.cannot_normal_move and not actor.move_used:
        destinations += battle.reachable_positions(actor, max_distance=actor.remaining_normal_move_distance(battle), use_movement_cost=True)
    # For each primary, only its nearest legal firing endpoint is needed; no hypothetical actions/randomness.
    try:
        for target in enemies:
            for destination in sorted(destinations, key=lambda cell: (cell.distance_to(origin), cell.y, cell.x)):
                actor.position = destination
                if not battle.attack_target_allowed(actor, target)[0]:
                    continue
                payload = {"type": "attack", "unit_id": actor.unit_id, "target_unit_id": target.unit_id}
                best = max(best, feiwang_attack_value(battle, actor, target, payload) - origin.distance_to(destination) * 2.0)
                break
    finally:
        actor.position = origin
    return best


def feiwang_stance_score(battle: Battle, actor: Unit) -> float:
    before = feiwang_attack_plan_value(battle, actor)
    with ai_probe_rollback(battle):
        actor.get_skill("inner_dimension_sword").execute(battle, actor, {})
        after = feiwang_attack_plan_value(battle, actor)
    gain = after - before
    return max(after + 25.0, gain * 2.0 + 25.0) if gain > 4.0 else -20.0


def feiwang_shensu_score(battle: Battle, actor: Unit) -> float:
    if actor.move_used or actor.cannot_move or actor.cannot_normal_move:
        return -1000.0
    before = feiwang_attack_plan_value(battle, actor)
    with ai_probe_rollback(battle):
        actor.get_skill("big_shensu").execute(battle, actor, {})
        gain = feiwang_attack_plan_value(battle, actor) - before
    return gain + 25.0 if gain > 15.0 else -20.0


def kings_insight_score(battle: Battle, actor: Unit, profile: DifficultyProfile) -> float:
    if battle.has_weather("王者的看破") or actor.position is None:
        return -1000.0
    from dataclasses import replace
    from wujiang.tactical.heroes.excel_roster import KingsInsightField
    targets = living_hostile_combatants(battle, actor.player_id)
    origin = actor.position
    destinations = [origin]
    if not actor.move_used and not actor.cannot_move and not actor.cannot_normal_move:
        destinations += battle.reachable_positions(actor, max_distance=actor.remaining_normal_move_distance(battle), use_movement_cost=True)
    reactors: dict[str, str] = {}

    def collect_reactors(queued: QueuedAction) -> None:
        for enemy in targets:
            for option in battle.available_reaction_options(enemy, queued):
                if option.action_type == "skill" and enemy.get_skill(option.action_code).timing in {"passive", "reaction"}:
                    reactors[enemy.unit_id] = option.action_code
                    break

    if not actor.cannot_attack and actor.attacks_used < actor.attack_actions_per_turn():
        try:
            for target in targets:
                for destination in sorted(destinations, key=lambda cell: (cell.distance_to(origin), cell.y, cell.x)):
                    actor.position = destination
                    if not battle.attack_target_allowed(actor, target)[0]:
                        continue
                    queued = battle.build_queued_action({"type": "attack", "unit_id": actor.unit_id, "target_unit_id": target.unit_id})
                    for segment in queued.payload.get("separate_attack_segments", [{"target_unit_id": target.unit_id, "cells": [p.to_dict() for p in battle.unit_cells(target)]}]):
                        part = replace(queued, target_unit_ids=[segment["target_unit_id"]], target_cells=battle.payload_positions(segment, "cells"))
                        collect_reactors(part)
                    break
        finally:
            actor.position = origin
    for code in ("large_pierce_plus", "gale"):
        skill = actor.skill_map().get(code)
        if skill is None or not skill.can_use(battle, actor, {})[0]:
            continue
        for candidate in skill_payloads_for_action(battle, actor, copied_skill_action(battle, actor, skill)):
            try:
                value = gale_score(battle, actor, candidate, profile) if code == "gale" else skill_damage_score(battle, actor, skill, candidate, profile)
                if value < profile.action_threshold:
                    continue
                collect_reactors(battle.build_queued_action(candidate))
            except ActionError:
                continue
    if not reactors:
        return -20.0
    score = 0.0
    for unit_id in reactors:
        target = battle.get_unit(unit_id)
        before_hp = target.current_hp
        with ai_probe_rollback(battle):
            before_mana = sum(unit.current_mana for unit in battle.hero_units(actor.player_id))
            # Value the actual field effect, including defenses and capped restoration.
            skill = target.get_skill(reactors[unit_id])
            KingsInsightField(actor.player_id).on_owner_action_declared(
                battle, "skill", {"unit_id": unit_id, "skill_code": skill.code, "queued_resolution": True})
            score += (before_hp - target.current_hp) * 100.0
            score += max(0.0, sum(unit.current_mana for unit in battle.hero_units(actor.player_id)) - before_mana) * 16.0
    # Non-Fei-Wang allies may need defenses against hostile counter effects in the same turn.
    for ally in battle.player_units(actor.player_id):
        if getattr(ally, "hero_code", "") == "excel_r028" or ally.direct_effects_blocked():
            continue
        if any(skill.timing in {"passive", "reaction"} and skill.can_use(battle, ally, {})[0] for skill in ally.skills):
            if any(distance_between_units(battle, ally, enemy) <= enemy.targeting_range() for enemy in targets):
                score -= min(0.75, ally.current_hp) * 35.0
    return score + 28.0 if score > 10.0 else -20.0


def gale_score(battle: Battle, actor: Unit, payload: dict[str, Any], profile: DifficultyProfile) -> float:
    skill = actor.get_skill("gale")
    units = battle.effect_units_at_cells(skill.get_target_cells_for_payload(battle, actor, payload))
    before = {unit.unit_id: (unit.alive, unit.position, unit.is_stealthed(), unit.total_shields()) for unit in units}
    plan = feiwang_attack_plan_value(battle, actor)
    with ai_probe_rollback(battle):
        skill.execute(battle, actor, payload)
        score = -skill.mana_cost * 20.0
        for unit in units:
            alive, position, hidden, shields = before[unit.unit_id]
            sign = 1.0 if unit.player_id != actor.player_id else -1.5
            if alive and not unit.alive:
                score += sign * (110.0 + hostile_unit_value(unit) * 0.3)
            if hidden and not unit.is_stealthed():
                score += sign * 24.0
            score += sign * max(0, shields - unit.total_shields()) * 12.0
            if sign < 0 and position != unit.position and unit.alive:
                score -= 12.0
        score += (feiwang_attack_plan_value(battle, actor) - plan) * 1.3
        return score


def great_fire_funeral_score(
    battle: Battle,
    actor: Unit,
    skill: Any,
    payload: dict[str, Any],
    profile: DifficultyProfile,
) -> float:
    cells = skill_effect_cells(battle, actor, skill, payload)
    if not cells:
        return -20.0
    cell_keys = {(cell.x, cell.y) for cell in cells}
    enemies = [
        unit
        for unit in battle.enemy_units(actor.player_id)
        if unit.alive and unit.position is not None and not unit.banished
    ]
    allies = [
        unit
        for unit in battle.player_units(actor.player_id)
        if unit.unit_id != actor.unit_id and unit.alive and unit.position is not None and not unit.banished
    ]
    score = skill_damage_score(battle, actor, skill, payload, profile)
    direct_enemy_hits = 0
    existing_fire = set().union(*(set(getattr(effect, "cells", set())) for effect in battle.field_effects if getattr(effect, "name", "") == "大火葬余烬"))
    new_fire = cell_keys - existing_fire
    for enemy in enemies:
        occupied = {(cell.x, cell.y) for cell in battle.unit_cells(enemy)}
        if occupied & cell_keys:
            direct_enemy_hits += 1
            score += hostile_unit_value(enemy) * 0.25 + 34.0
        elif new_fire:
            nearest_to_fire = min((min(abs(cell.x - x) + abs(cell.y - y) for x, y in new_fire) for cell in battle.unit_cells(enemy)), default=99)
            if nearest_to_fire <= max(1, int(enemy.stat("speed"))):
                score += max(0.0, 4.0 - nearest_to_fire) * 10.0
    for ally in allies:
        occupied = {(cell.x, cell.y) for cell in battle.unit_cells(ally)}
        if occupied & cell_keys:
            score -= friendly_fire_penalty(ally) * 0.8
    if direct_enemy_hits == 0 and score < profile.action_threshold:
        return -12.0
    if direct_enemy_hits == 0 and not new_fire:
        return -1000.0
    if actor.stat("attack") > 1:
        score -= 20.0 * max(1, actor.attack_actions_per_turn())
        if actor.stat("attack") == 2 and any(any(candidate.timing == "active" for candidate in enemy.skills) for enemy in enemies):
            score += 24.0
    return score + profile.aggressive_bonus


def great_fire_funeral_alignment_score_at(battle: Battle, actor: Unit, destination: Position) -> float:
    if not any(getattr(skill, "code", "") == "great_funeral" and skill.cooldown_remaining <= 0 for skill in actor.skills):
        return 0.0
    enemies = [unit for unit in battle.enemy_units(actor.player_id) if unit.alive and unit.position is not None and not unit.banished]
    if not enemies:
        return 0.0
    aligned = 0
    near_line = 0
    for enemy in enemies:
        enemy_cells = battle.unit_cells(enemy)
        if any(cell.x == destination.x or cell.y == destination.y for cell in enemy_cells):
            aligned += 1
            continue
        distance_to_cross = min(min(abs(cell.x - destination.x), abs(cell.y - destination.y)) for cell in enemy_cells)
        if distance_to_cross <= 1:
            near_line += 1
    return aligned * 42.0 + near_line * 10.0


def generic_skill_score(
    battle: Battle,
    actor: Unit,
    code: str,
    targets: list[Unit],
    profile: DifficultyProfile,
) -> float:
    enemies = [unit for unit in targets if unit.player_id != actor.player_id]
    allies = [unit for unit in targets if unit.player_id == actor.player_id]
    if enemies:
        return len(enemies) * 18.0 + profile.aggressive_bonus
    if allies:
        return len(allies) * 12.0 + profile.support_bonus
    return 6.0


def summon_position_score(battle: Battle, actor: Unit, destination: Position) -> float:
    enemies = living_hostile_combatants(battle, actor.player_id)
    if not enemies:
        return 0.0
    nearest = min(distance_to_position(battle, unit, destination) for unit in enemies)
    return max(0.0, 4.0 - nearest) * 8.0


def stone_summon_score(battle: Battle, actor: Unit, code: str,
                       payload: dict[str, Any], profile: DifficultyProfile) -> float:
    if payload.get("x") is None or payload.get("y") is None:
        return -1000.0
    destination = Position(int(payload["x"]), int(payload["y"]))
    skill = actor.get_skill(code)
    if destination not in skill.available_cells(battle, actor):
        return -1000.0
    enemies = living_hostile_combatants(battle, actor.player_id)
    if not enemies:
        return -1000.0
    child = skill.child(actor)
    occupied = battle.unit_cells_at(child, destination)
    nearest = min(min(distance_to_position(battle, enemy, cell) for cell in occupied) for enemy in enemies)
    if nearest > 6:
        return -8.0
    value = (20.0 if code == "summon_medium_stone" else 10.0) + max(0, 6 - nearest) * 7.0
    allies = [unit for unit in battle.player_units(actor.player_id)
              if unit is not actor and unit.position is not None and unit.alive and not unit.banished]
    for ally in allies:
        if any(battle.distance_between_units(ally, actor) <= 2 and
               distance_to_position(battle, ally, cell) <= 1 for cell in occupied):
            value -= 14.0
    existing = sum(unit.hero_code == child.hero_code and unit.player_id == actor.player_id
                   for unit in battle.all_units())
    value -= max(0, existing - 2) * 12.0
    return value


def stone_attack_spawn_penalty(battle: Battle, actor: Unit, payload: dict[str, Any]) -> float:
    from wujiang.tactical.heroes.excel_roster import MediumStoneSummon, SmallStoneSummon, stone_summon_anchors
    penalty = 0.0
    for target in attack_payload_enemy_targets(battle, actor, payload):
        if target.hero_code not in {"excel_r197", "medium_stone"} or not target.alive:
            continue
        child = MediumStoneSummon(target.player_id) if target.hero_code == "excel_r197" else SmallStoneSummon(target.player_id)
        if not stone_summon_anchors(battle, target, child):
            continue
        likely_damage = battle.damage_rule.calculate_damage(actor.stat("attack"), target.stat("defense"))
        if target.total_shields() == 0 and likely_damage >= target.current_hp:
            continue
        penalty += 28.0 if target.hero_code == "excel_r197" else 14.0
    return penalty


def score_respawn_destination(
    battle: Battle,
    unit: Unit,
    destination: Position,
    role: str,
    profile: DifficultyProfile,
) -> float:
    enemies = living_hostile_combatants(battle, unit.player_id)
    allies = [ally for ally in battle.player_units(unit.player_id) if ally.unit_id != unit.unit_id and ally.alive and ally.position is not None and not ally.banished]
    nearest_enemy = min((distance_to_position(battle, enemy, destination) for enemy in enemies), default=8)
    nearest_ally = min((distance_to_position(battle, ally, destination) for ally in allies), default=8)
    score = nearest_enemy * (6.0 if role == "support" else 2.0)
    if role != "support":
        score -= nearest_ally
    return score


def attack_payload_has_effective_enemy_impact(battle: Battle, actor: Unit, payload: dict[str, Any]) -> bool:
    for target in attack_payload_enemy_targets(battle, actor, payload):
        if attack_target_has_effective_impact(battle, actor, target, payload):
            return True
    return False


def attack_payload_satisfies_required_target(battle: Battle, actor: Unit, payload: dict[str, Any]) -> bool:
    forced_target = required_attack_target(battle, actor)
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
        cells = preview_positions(payload.get("cells"))
    forced_cells = {(cell.x, cell.y) for cell in battle.unit_cells(forced_target)}
    return any((cell.x, cell.y) in forced_cells for cell in cells)


def attack_payload_enemy_targets(battle: Battle, actor: Unit, payload: dict[str, Any]) -> list[Unit]:
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
        cells = preview_positions(payload.get("cells"))
    return [unit for unit in battle.effect_units_at_cells(cells) if unit.player_id != actor.player_id]


def attack_target_has_effective_impact(battle: Battle, actor: Unit, target: Unit, payload: dict[str, Any]) -> bool:
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


def skill_payload_requires_enemy_impact(
    battle: Battle,
    actor: Unit,
    action: dict[str, Any],
    payload: dict[str, Any],
) -> bool:
    code = str(action.get("code") or payload.get("skill_code") or "")
    target_mode = str(action.get("target_mode") or "")
    preview = action.get("preview", {}) or {}
    if code == "mimic_skill":
        context = mimic_payload_context(battle, actor, payload)
        if context is None:
            return False
        _, copied, copied_payload, copied_action = context
        with actor.get_skill("mimic_skill").copying(actor, copied, reserve_point=True):
            return skill_payload_requires_enemy_impact(battle, actor, copied_action, copied_payload)
    if code == "agency_borrowed_skill":
        context = agency_borrowed_payload_context(battle, actor, payload)
        if context is None:
            return False
        _, copied, copied_payload, copied_action = context
        with actor.get_skill("agency_borrowed_skill").copying(actor, copied):
            return skill_payload_requires_enemy_impact(battle, actor, copied_action, copied_payload)
    if code in {"vain_giant_shadow", "gladiator_soul", "electronic_repair"}:
        return False
    if code == "gladiator_gale":
        return False
    if code == "migratory_bird_mark":
        target = battle.units.get(str(payload.get("target_unit_id") or ""))
        return target is not None and target.player_id != actor.player_id
    if code in MOVE_SKILL_CODES or code in SUMMON_SKILL_CODES or code in HEAL_SKILL_CODES:
        return False
    if code in {"fuma_trap", "paralysis_card", "poison_card", "drain_card", "descent_moment", "smoke_spray"}:
        return False
    if code == "weapon_copy":
        return False
    if code in ALLY_BUFF_SKILL_CODES or code in SELF_BUFF_SKILL_CODES:
        return False
    if code in {"stance", "great_holy_light"}:
        return False
    if code == "great_funeral":
        return False
    if code in DAMAGING_SKILL_CODES or code in HOSTILE_EFFECT_SKILL_CODES:
        return True
    if target_mode == "cell" and preview.get("requires_target"):
        return True
    if target_mode == "cell":
        try:
            skill = skill_from_ai_action(actor, action, code)
            if not skill_effect_units(battle, actor, skill, payload):
                return True
        except Exception:
            return True
    try:
        skill = skill_from_ai_action(actor, action, code)
        return any(unit.player_id != actor.player_id for unit in skill_effect_units(battle, actor, skill, payload))
    except Exception:
        return False


def skill_payload_has_effective_enemy_impact(
    battle: Battle,
    actor: Unit,
    action: dict[str, Any],
    payload: dict[str, Any],
) -> bool:
    code = str(action.get("code") or payload.get("skill_code") or "")
    if code in {"bird_dash", "bird_dash_free"}:
        return r215219_skill_score(battle, actor, payload) > 0
    if code == "mimic_skill":
        context = mimic_payload_context(battle, actor, payload)
        if context is None:
            return False
        _, copied, copied_payload, copied_action = context
        with actor.get_skill("mimic_skill").copying(actor, copied, reserve_point=True):
            return skill_payload_has_effective_enemy_impact(battle, actor, copied_action, copied_payload)
    if code == "agency_borrowed_skill":
        context = agency_borrowed_payload_context(battle, actor, payload)
        if context is None:
            return False
        _, copied, copied_payload, copied_action = context
        with actor.get_skill("agency_borrowed_skill").copying(actor, copied):
            return skill_payload_has_effective_enemy_impact(battle, actor, copied_action, copied_payload)
    try:
        skill = skill_from_ai_action(actor, action, code)
    except Exception:
        return False
    if code in {"demon_blade", "nuclear_mutation", "gravity_field", "sanctuary_banish", "sanctuary_judgment"}:
        return r12_effect_score(battle, actor, skill, payload, difficulty_profile("standard")) > 0
    targets = [unit for unit in skill_effect_units(battle, actor, skill, payload) if unit.player_id != actor.player_id]
    if code in {"hundred_bird_burial", "remi_chaos", "nian_large_dragon_breath", "nian_roar", "nian_jade_flash"}:
        return r13_effect_score(battle, actor, skill, payload, difficulty_profile("standard")) > 0
    if code == "sun_slash":
        return r23_sun_slash_score(battle, actor, skill, payload) > 0
    if code == "kaiser_fist" and targets:
        if kaiser_fist_score(battle, actor, skill, payload, difficulty_profile("standard")) > 0:
            return True
    if code in {"interference", "noise_wave", "purify_mana", "sacred_duel"}:
        return reviewed_r15_effect_score(battle, actor, skill, payload, difficulty_profile("standard")) > 0
    if code == "snow_avalanche":
        return r14_snow_avalanche_score(battle, actor, skill, payload) > 0
    if code == "heaven_punishment":
        return score_skill_payload(battle, actor, action, payload, difficulty_profile("standard"), instant_only=False) > 0
    if code == "fuma_shuriken":
        return reviewed_r16_damage_result(battle, actor, lambda: skill.execute(battle, actor, payload)) > 8.0
    if code in {"fantasy_move", "true_blade_air_slash", "undead_boy_devour"}:
        return reviewed_r17_effect_score(battle, actor, skill, payload, difficulty_profile("standard")) > 0
    if code in {"illumination_light", "thor_heavy_hammer", "thor_rage_impact", "thor_destroy_lightning"}:
        return reviewed_r18_effect_score(battle, actor, skill, payload, difficulty_profile("standard")) > 0
    if code == "hell_slash":
        # A real hit remains a candidate even when conserving the one-use ultimate
        # makes its strategic score too low to choose right now.
        return reviewed_r18_effect_score(battle, actor, skill, payload, difficulty_profile("standard")) > 0
    if code in {"electric_wind", "beetle_spear"}:
        return reviewed_r19_effect_score(battle, actor, skill, payload, difficulty_profile("standard")) > 0
    if not targets:
        return False
    cells = skill_effect_cells(battle, actor, skill, payload)
    if code == "gale":
        return any(unit.is_summon or unit.is_clone or unit.position is not None for unit in targets)
    if code in HOSTILE_EFFECT_SKILL_CODES:
        for target in targets:
            if hostile_skill_effect_target_has_impact(battle, actor, skill, payload, target):
                return True
    if code in DAMAGING_SKILL_CODES:
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
            attack_power = skill_attack_power(battle, actor, skill, payload, target, cells)
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


def hostile_skill_effect_target_has_impact(
    battle: Battle,
    actor: Unit,
    skill: Any,
    payload: dict[str, Any],
    target: Unit,
) -> bool:
    code = str(skill.code)
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
            extra_effect_applies=lambda probe_target: hostile_skill_extra_effect_applies(code, probe_target),
        )
    except Exception:
        return False


def hostile_skill_extra_effect_applies(code: str, target: Unit) -> bool:
    if code in {"drain_mana", "large_drain_mana"}:
        return target.current_mana > 0
    if code == "erasure":
        return any(status.name == "抹杀计数点" for status in target.statuses)
    return True


@dataclass(slots=True)
class DamageImpact:
    damage: float
    changed_target: bool


def estimate_attack_damage(
    battle: Battle,
    actor: Unit,
    target: Unit,
    payload: dict[str, Any],
    *,
    attack_power: float,
    area_cell_hits: int = 1,
) -> float:
    return probe_attack_damage_impact(
        battle,
        actor,
        target,
        payload,
        attack_power=attack_power,
        area_cell_hits=area_cell_hits,
    ).damage


def estimate_skill_damage(
    battle: Battle,
    actor: Unit,
    skill: Any,
    payload: dict[str, Any],
    target: Unit,
    attack_power: float,
    *,
    cells: list[Position],
    ignore_shield: bool,
    half_ignore_shield: bool,
) -> float:
    return probe_skill_damage_impact(
        battle,
        actor,
        skill,
        payload,
        target,
        attack_power,
        cells=cells,
        ignore_shield=ignore_shield,
        half_ignore_shield=half_ignore_shield,
    ).damage


def probe_attack_damage_impact(
    battle: Battle,
    actor: Unit,
    target: Unit,
    payload: dict[str, Any],
    *,
    attack_power: float,
    area_cell_hits: int = 1,
) -> DamageImpact:
    resolved_payload = battle.resolved_basic_attack_payload(actor, payload)
    attack_tags = {"attack"}
    attack_tags.update(set(resolved_payload.get("attack_tags", [])))
    with ai_probe_rollback(battle):
        probe_actor = battle.get_unit(actor.unit_id)
        probe_target = battle.get_unit(target.unit_id)
        before = unit_impact_signature(probe_target)
        target_ctx = battle.validate_target(
            probe_actor,
            probe_target,
            action_name=str(resolved_payload.get("attack_name") or "普攻"),
            is_skill=False,
            is_hostile=True,
            resolve_defenses=False,
            tags=set(attack_tags),
        )
        if target_ctx.cancelled:
            return DamageImpact(0.0, unit_impact_signature(probe_target) != before)
        damage_ctx = DamageContext(
            source=probe_actor,
            target=probe_target,
            attack_power=attack_power,
            is_skill=False,
            action_name=str(resolved_payload.get("attack_name") or "普攻"),
            ignore_shield=bool(target_ctx.ignore_shield or resolved_payload.get("ignore_shield")),
            half_ignore_shield=bool(target_ctx.half_ignore_shield or resolved_payload.get("half_ignore_shield")),
            ignore_magic_immunity=target_ctx.ignore_magic_immunity,
            cannot_evade=target_ctx.cannot_evade,
            tags=set(target_ctx.tags),
            area_cell_hits=max(1, int(area_cell_hits)),
        )
        battle.resolve_damage(damage_ctx)
        return DamageImpact(damage_ctx.actual_damage, unit_impact_signature(probe_target) != before)


def probe_skill_damage_impact(
    battle: Battle,
    actor: Unit,
    skill: Any,
    payload: dict[str, Any],
    target: Unit,
    attack_power: float,
    *,
    cells: list[Position],
    ignore_shield: bool,
    half_ignore_shield: bool,
) -> DamageImpact:
    hit_count = 1 if skill.code == "deadly_bow" else max(1, battle.unit_hit_count_for_cells(target, cells) if cells else 1)
    adjusted_attack_power = max(0.0, float(attack_power) - max(0, hit_count - 1))
    resolves_as_attack = str(skill.code) == "whirlwind_attack"
    if str(skill.code) in {"complete_burn", "blizzard"}:
        # Only the attached control pierces; the damage still stops at shields.
        ignore_shield = False
    target_tags = {"attack", "whirlwind"} if resolves_as_attack else {"skill", str(skill.code)}
    with ai_probe_rollback(battle):
        probe_actor = battle.get_unit(actor.unit_id)
        probe_target = battle.get_unit(target.unit_id)
        before = unit_impact_signature(probe_target)
        target_ctx = battle.validate_target(
            probe_actor,
            probe_target,
            action_name=str(skill.name),
            is_skill=not resolves_as_attack,
            is_hostile=True,
            ignore_shield=ignore_shield,
            half_ignore_shield=half_ignore_shield,
            ignore_magic_immunity=skill.ignores_magic_immunity_for_payload(battle, probe_actor, payload),
            cannot_evade=skill.cannot_evade_for_payload(battle, probe_actor, payload),
            ignore_targeting_restrictions=str(skill.code) in {"great_funeral", "judgment_fire", "wind_sand", "crazy_sand", "dragon_breath", "remote_dragon_breath", "rending", "complete_burn", "blizzard", "machine_gun", "missile", "laser", "magnetic_wave", "frey_quick_flash", "frey_god_stab", "frey_lion_spear", "demon_blade", "nuclear_mutation", "gravity_field"},
            resolve_defenses=False,
            damage_target=True,
            tags=set(target_tags),
        )
        if target_ctx.cancelled:
            return DamageImpact(0.0, unit_impact_signature(probe_target) != before)
        damage_ctx = DamageContext(
            source=probe_actor,
            target=probe_target,
            attack_power=adjusted_attack_power,
            is_skill=not resolves_as_attack,
            action_name=str(skill.name),
            ignore_shield=bool(target_ctx.ignore_shield or ignore_shield),
            half_ignore_shield=bool(target_ctx.half_ignore_shield or half_ignore_shield),
            ignore_magic_immunity=target_ctx.ignore_magic_immunity,
            cannot_evade=target_ctx.cannot_evade,
            tags=set(target_ctx.tags),
            area_cell_hits=hit_count,
        )
        battle.resolve_damage(damage_ctx)
        if str(skill.code) in {"complete_burn", "blizzard"}:
            skill.apply_followup_effect(battle, probe_target, damage_ctx)
        return DamageImpact(damage_ctx.damage, unit_impact_signature(probe_target) != before)


def probe_skill_raw_damage_impact(
    battle: Battle,
    actor: Unit,
    skill: Any,
    target: Unit,
    *,
    raw_damage: float,
    ignore_shield: bool,
) -> DamageImpact:
    with ai_probe_rollback(battle):
        probe_actor = battle.get_unit(actor.unit_id)
        probe_target = battle.get_unit(target.unit_id)
        before = unit_impact_signature(probe_target)
        target_ctx = battle.validate_target(
            probe_actor,
            probe_target,
            action_name=str(skill.name),
            is_skill=True,
            is_hostile=True,
            ignore_shield=ignore_shield,
            resolve_defenses=False,
            damage_target=True,
            tags={"skill", str(skill.code)},
        )
        if target_ctx.cancelled:
            return DamageImpact(0.0, unit_impact_signature(probe_target) != before)
        damage_ctx = DamageContext(
            source=probe_actor,
            target=probe_target,
            attack_power=0,
            raw_damage=raw_damage,
            is_skill=True,
            action_name=str(skill.name),
            ignore_shield=bool(target_ctx.ignore_shield or ignore_shield),
            ignore_magic_immunity=target_ctx.ignore_magic_immunity,
            cannot_evade=target_ctx.cannot_evade,
            tags=set(target_ctx.tags),
        )
        battle.resolve_damage(damage_ctx)
        return DamageImpact(damage_ctx.damage, unit_impact_signature(probe_target) != before)


def probe_target_effect_impact(
    battle: Battle,
    actor: Unit,
    target: Unit,
    *,
    action_name: str,
    is_skill: bool,
    ignore_shield: bool = False,
    half_ignore_shield: bool = False,
    extra_effect_applies: Optional[Callable[[Unit], bool]] = None,
) -> bool:
    with ai_probe_rollback(battle):
        probe_actor = battle.get_unit(actor.unit_id)
        probe_target = battle.get_unit(target.unit_id)
        before = unit_impact_signature(probe_target)
        ctx = battle.validate_target(
            probe_actor,
            probe_target,
            action_name=action_name,
            is_skill=is_skill,
            is_hostile=True,
            ignore_shield=ignore_shield,
            half_ignore_shield=half_ignore_shield,
            resolve_defenses=True,
            tags={"skill"} if is_skill else {"attack"},
        )
        changed = unit_impact_signature(probe_target) != before
        if changed:
            return True
        if ctx.cancelled:
            return False
        return extra_effect_applies(probe_target) if extra_effect_applies is not None else True


@contextmanager
def ai_probe_rollback(battle: Battle) -> Iterable[None]:
    with battle_state_rollback(battle):
        battle._forecasting_action = True
        yield


def unit_impact_signature(unit: Unit) -> tuple[Any, ...]:
    return (
        unit.alive,
        unit.turn_ready,
        unit.position,
        round(float(unit.current_hp), 6),
        round(float(unit.current_mana), 6),
        round(float(unit.mana_points), 6),
        unit.shields,
        unit.temporary_shields,
        unit.dodge_charges,
        unit.magic_immunity,
        unit.cannot_be_targeted,
        unit.cannot_move,
        unit.cannot_normal_move,
        unit.cannot_heal,
        unit.cannot_attack,
        unit.cannot_use_skills,
        tuple(status_impact_signature(status) for status in unit.statuses),
    )


def status_impact_signature(status: Any) -> tuple[Any, ...]:
    data = []
    for key, value in sorted(getattr(status, "__dict__", {}).items(), key=lambda item: str(item[0])):
        if key == "owner":
            continue
        if isinstance(value, (str, int, float, bool, type(None))):
            data.append((key, value))
        elif isinstance(value, set):
            data.append((key, tuple(sorted(value))))
        elif isinstance(value, list):
            data.append((key, tuple(repr(item) for item in value)))
        elif isinstance(value, dict):
            data.append((key, tuple(sorted((str(k), repr(v)) for k, v in value.items()))))
        else:
            data.append((key, repr(value)))
    return (type(status).__name__, tuple(data))


def payload_is_legal(battle: Battle, payload: dict[str, Any]) -> bool:
    try:
        if payload.get("skill_code") == "gravity_field":
            with ai_probe_rollback(battle):
                battle.build_queued_action(payload)
        else:
            battle.build_queued_action(payload)
        return True
    except Exception:
        return False


def reaction_payload_is_legal(
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
            destination = payload_destination(payload)
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


def skill_effect_units(battle: Battle, actor: Unit, skill: Any, payload: dict[str, Any]) -> list[Unit]:
    units: list[Unit] = []
    ignored = actor if skill.excludes_caster_from_effect else None
    def filter_units() -> list[Unit]:
        return [unit for unit in battle.effect_units(units, ignore=ignored) if not skill.excludes_allies_from_effect or unit.player_id != actor.player_id]
    payload_cells = preview_positions(payload.get("cells"))
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


def skill_effect_cells(battle: Battle, actor: Unit, skill: Any, payload: dict[str, Any]) -> list[Position]:
    payload_cells = preview_positions(payload.get("cells"))
    if payload_cells and skill.selection_cells_are_effect_cells:
        return payload_cells
    try:
        return list(skill.get_target_cells_for_payload(battle, actor, payload))
    except Exception:
        return []


def skill_attack_power(
    battle: Battle,
    actor: Unit,
    skill: Any,
    payload: dict[str, Any],
    target: Unit,
    cells: list[Position],
) -> float:
    code = str(skill.code)
    if code == "deadly_bow":
        return float(payload.get("deadly_bow_points", actor.mana_points))
    if code == "judgment_fire":
        return 6.0
    if code == "great_funeral":
        return 5.0
    if code == "kaiser_fist":
        return actor.stat("attack") + 1
    if code == "earth_shatter":
        bonus = 0.0 if actor.has_status("天崩地裂强化") else 1.0
        return actor.stat("attack") + bonus + (max(0, battle.unit_hit_count_for_cells(target, cells) - 1) if cells else 0)
    if code == "illumination_light":
        return 4.0
    if code == "true_blade_air_slash":
        return target.stat("defense") + 1
    if code == "dragon_slash":
        return 5.0
    if code == "lao_wave_bullet":
        return actor.stat("attack") - (1 if payload.get("free_cast") is True or payload.get("choice_code") == "free" else 0)
    if code == "demon_blade":
        blade_cells = preview_positions(payload.get("cells")) or cells
        target_cells = set(battle.unit_cells(target))
        return max((5.0 - index for index, cell in enumerate(blade_cells[:3]) if cell in target_cells), default=0.0)
    if code == "hundred_bird_burial":
        return actor.stat("attack") + 2.0
    if code == "snow_avalanche":
        return actor.stat("attack") + (1.0 if battle.has_weather("大雪崩") else 0.0)
    if code == "rock_cannon":
        selected_cells = preview_positions(payload.get("cells", []))
        return 3.0 + float(len(selected_cells))
    if code == "apocalypse":
        try:
            n = int(payload.get("choice_code", payload.get("n", 0)))
        except (TypeError, ValueError):
            n = 0
        return actor.stat("attack") + n
    hit_bonus = max(0, battle.unit_hit_count_for_cells(target, cells) - 1) if cells else 0
    return actor.stat("attack") + hit_bonus


def estimate_damage(
    battle: Battle,
    target: Unit,
    attack_power: float,
    *,
    ignore_shield: bool = False,
    half_ignore_shield: bool = False,
) -> float:
    attack_value = float(attack_power)
    if target.total_shields() > 0 and not ignore_shield:
        if half_ignore_shield:
            attack_value = max(0.0, attack_value - 1.0)
        else:
            return 0.0
    return target.damage_fraction_after_limits(battle.damage_rule.calculate_damage(attack_value, target.stat("defense")))


def r20_line_window_value(battle: Battle, actor: Unit, profile: DifficultyProfile) -> float:
    value = 0.0
    for skill in actor.skills:
        if skill.code not in {"vitality_blast", "electronic_laser", "machine_gun"} or not skill.can_use(battle, actor, {})[0]:
            continue
        seen: set[tuple[tuple[str, int], ...]] = set()
        for cells in skill.patterns(battle, actor):
            targets = battle.effect_units_at_cells(cells)
            signature = tuple(sorted((unit.unit_id, battle.unit_hit_count_for_cells(unit, cells)) for unit in targets))
            if signature in seen or not any(unit.player_id != actor.player_id for unit in targets):
                continue
            seen.add(signature)
            payload = {"type": "skill", "unit_id": actor.unit_id, "skill_code": skill.code,
                       "cells": [cell.to_dict() for cell in cells]}
            value = max(value, reviewed_r18_effect_score(battle, actor, skill, payload, profile))
    return value


def r20_weather_combat_value(battle: Battle, actor: Unit, profile: DifficultyProfile) -> float:
    from wujiang.tactical.heroes.excel_roster import weather_race_eligible
    value = 0.0
    for unit in list(battle.all_units()):
        if not unit.alive or unit.position is None or unit.banished:
            continue
        sign = 1.0 if unit.player_id == actor.player_id else -1.15
        with ai_probe_rollback(battle):
            # Future turn opportunities are measured without firing opening effects or RNG.
            if not unit.can_take_turn_actions(battle):
                battle.turn_number += 1
            battle.resolving_action = None
            battle.active_player = unit.player_id
            battle._exclusive_turn_unit_id = unit.unit_id
            unit.turn_ready = True
            unit.attacks_used = 0
            unit.normal_move_actions_used = unit.normal_move_steps_used = 0
            unit.move_used = False
            origins = [unit.position]
            if not unit.cannot_move and not unit.cannot_normal_move:
                origins += battle.reachable_positions(unit, max_distance=unit.remaining_normal_move_distance(battle), use_movement_cost=True)
            best, pool = 0.0, 0.0
            for target in battle.effect_units(living_hostile_combatants(battle, unit.player_id)):
                if unit.cannot_attack or not battle.unit_can_be_selected(target, actor=unit)[0]:
                    continue
                damage = 0.0
                current = unit.position
                for origin in origins:
                    if distance_to_position(battle, target, origin) > unit.targeting_range():
                        continue
                    unit.position = origin
                    if battle.attack_target_allowed(unit, target)[0]:
                        payload = {"target_unit_id": target.unit_id}
                        damage = max(damage, estimate_attack_damage(battle, unit, target, payload,
                                                                  attack_power=battle.basic_attack_preview_power(unit, payload)))
                        break
                unit.position = current
                if damage > 0:
                    best = max(best, min(target.current_hp, damage) * 100.0)
                    pool += target.current_hp * 100.0
            for status in list(unit.statuses):
                if status.name == "万魔殿追加攻击":
                    unit.remove_status(status, battle)
            attempts = float(unit.attack_actions_per_turn())
            if not unit.direct_effects_blocked() and weather_race_eligible(unit, "恶魔") and battle.unit_in_weather("万魔殿", unit):
                attempts *= 2.0  # Each completed attempt independently returns one with probability 1/2.
            value += sign * min(pool, best * attempts)
            if weather_race_eligible(unit, "天使") and battle.unit_in_weather("天空圣域", unit) and not unit.direct_effects_blocked():
                hp = 0.0 if unit.cannot_heal else min(0.25, max(0.0, unit.max_health - unit.current_hp))
                mana = min(1.0, max(0.0, unit.max_mana() - unit.current_mana)) if any(skill.mana_cost > 0 for skill in unit.skills) else 0.0
                value += sign * (hp * 130.0 + mana * 25.0)
            for skill in unit.skills:
                skill._uses_turn_number = battle.turn_number
                skill.uses_this_turn = 0
            value += sign * (r20_line_window_value(battle, unit, profile) + r20_piercing_skill_window_value(battle, unit, profile))
    return value


def r20_piercing_skill_window_value(battle: Battle, actor: Unit, profile: DifficultyProfile) -> float:
    value = 0.0
    for skill in actor.skills:
        if skill.code not in DAMAGING_SKILL_CODES:
            continue
        try:
            if not skill.can_use(battle, actor, {})[0]:
                continue
            action = {"code": skill.code, "target_mode": skill.target_mode, "preview": skill.preview(battle, actor)}
            generated = skill_payloads_for_action(battle, actor, action)
        except (ActionError, KeyError, ValueError, TypeError):
            continue
        payloads = dedupe_damage_area_payloads(battle, [payload for payload in generated if payload.get("cells")])
        payloads += [payload for payload in generated if not payload.get("cells")]
        for payload in trim_skill_payloads_for_ai(battle, actor, payloads, limit=16):
            try:
                if not (skill.ignores_shield_for_payload(battle, actor, payload)
                        or skill.half_ignores_shield_for_payload(battle, actor, payload)):
                    continue
                if not any(unit.player_id != actor.player_id for unit in skill_effect_units(battle, actor, skill, payload)):
                    continue
            except (ActionError, KeyError, ValueError, TypeError):
                continue
            value = max(value, reviewed_r18_effect_score(battle, actor, skill, payload, profile))
    return value


def r20_weather_cast_score(battle: Battle, actor: Unit, code: str, profile: DifficultyProfile) -> float:
    name = {"pandemonium": "万魔殿", "sky_sanctuary": "天空圣域", "wetland_grassland": "湿地草原"}[code]
    if any(getattr(effect, "global_weather", False)
           and battle.canonical_weather_name(getattr(effect, "weather_name", "")) == name for effect in battle.field_effects):
        return -1000.0
    before = r20_weather_combat_value(battle, actor, profile)
    with ai_probe_rollback(battle):
        actor.get_skill(code).execute(battle, actor, {})
        after = r20_weather_combat_value(battle, actor, profile)
    return after - before - 10.0


def incoming_threat_score(battle: Battle, target: Unit, queued_action: QueuedAction) -> float:
    if not battle.target_can_chain_against(target, queued_action):
        return 0.0
    payload = dict(queued_action.payload or {})
    source = battle.get_unit(queued_action.actor_id)
    if queued_action.action_type == "attack":
        attack_power = battle.basic_attack_preview_power(source, payload)
        return estimate_damage(
            battle,
            target,
            attack_power,
            ignore_shield=bool(payload.get("ignore_shield")),
            half_ignore_shield=bool(payload.get("half_ignore_shield")),
        ) * 100.0
    if queued_action.action_type == "skill_effect" and payload.get("effect_code") == "area_damage":
        attack_power = float(payload.get("attack_power", 0.0) or 0.0)
        return estimate_damage(
            battle,
            target,
            attack_power,
            ignore_shield=bool(payload.get("ignore_shield")),
            half_ignore_shield=bool(payload.get("half_ignore_shield")),
        ) * 100.0
    if queued_action.action_type in {"skill", "skill_effect"}:
        return 65.0
    return 0.0


def offensive_reach_score_at(battle: Battle, actor: Unit, destination: Position) -> int:
    current = actor.position
    actor.position = destination
    try:
        preview = battle.basic_attack_preview_for_payload(actor, {})
        target_ids = {
            str(unit_id)
            for unit_id in preview.get("target_unit_ids", [])
            if not bool(getattr(battle.units.get(str(unit_id)), "is_siege_structure", False))
        }
        return len(target_ids)
    except Exception:
        return 0
    finally:
        actor.position = current


def primary_target_unit(battle: Battle, payload: dict[str, Any], targets: list[Unit]) -> Optional[Unit]:
    target_id = payload.get("target_unit_id")
    if target_id:
        return battle.get_unit(str(target_id))
    if targets:
        return targets[0]
    return None


def hero_style(unit: Unit) -> str:
    code = str(getattr(unit, "hero_code", "") or "")
    return "support" if code in SUPPORT_HERO_CODES else "aggressive"


def has_mana_point_skill(unit: Unit) -> bool:
    return any(float(getattr(skill, "mana_point_cost", 0) or 0) > 0 or getattr(skill, "code", "") in {"magnetic_wave", "n_skill"} for skill in unit.skills)


def hostile_unit_value(unit: Unit) -> float:
    if bool(getattr(unit, "is_siege_structure", False)):
        return 0.0
    return (
        unit.level * 8.0
        + unit.stat("attack") * 5.0
        + unit.stat("defense") * 3.0
        + unit.stat("speed") * 3.0
        + unit.stat("attack_range") * 2.0
        + unit.current_mana
        + unit.current_hp * 24.0
    )


def ally_unit_value(unit: Unit) -> float:
    return hostile_unit_value(unit)


def friendly_fire_penalty(unit: Unit) -> float:
    return 70.0 + ally_unit_value(unit) * 0.6


def distance_between_units(battle: Battle, source: Unit, target: Unit) -> int:
    return battle.distance_between_units(source, target)


def distance_to_position(battle: Battle, unit: Unit, destination: Position) -> int:
    return battle.unit_distance_to_cell(unit, destination)


def preview_positions(raw_cells: Any) -> list[Position]:
    cells: list[Position] = []
    if not isinstance(raw_cells, list):
        return cells
    for cell in raw_cells:
        if not isinstance(cell, dict) or cell.get("x") is None or cell.get("y") is None:
            continue
        cells.append(Position(int(cell["x"]), int(cell["y"])))
    return cells


def positions_to_payload(cells: Iterable[Position]) -> list[dict[str, int]]:
    return [{"x": cell.x, "y": cell.y} for cell in cells]


def choose_declared_target_cell(battle: Battle, target: Unit, candidate_cells: list[Position]) -> Optional[Position]:
    occupied = set((cell.x, cell.y) for cell in candidate_cells)
    for cell in sorted(battle.unit_cells(target), key=lambda item: (item.y, item.x)):
        if (cell.x, cell.y) in occupied:
            return cell
    return None


def payload_destination(payload: dict[str, Any]) -> Optional[Position]:
    if payload.get("x") is None or payload.get("y") is None:
        return None
    return Position(int(payload["x"]), int(payload["y"]))


def best_candidate(candidates: Iterable[AICandidate]) -> Optional[AICandidate]:
    best: Optional[AICandidate] = None
    for candidate in candidates:
        if best is None or candidate.score > best.score:
            best = candidate
    return best


def best_unit_candidate(candidates: Iterable[tuple[Unit, AICandidate]]) -> Optional[tuple[Unit, AICandidate]]:
    best: Optional[tuple[Unit, AICandidate]] = None
    for unit, candidate in candidates:
        if best is None or candidate.score > best[1].score:
            best = (unit, candidate)
    return best


def dedupe_payloads(payloads: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    ordered: list[dict[str, Any]] = []
    seen: set[str] = set()
    for payload in payloads:
        key = repr(sorted_payload(payload))
        if key in seen:
            continue
        seen.add(key)
        ordered.append(payload)
    return ordered


def sorted_payload(payload: Any) -> Any:
    if isinstance(payload, dict):
        return [(key, sorted_payload(value)) for key, value in sorted(payload.items(), key=lambda item: str(item[0]))]
    if isinstance(payload, list):
        return [sorted_payload(value) for value in payload]
    return payload




def r23_action_state(battle: Battle) -> dict[str, tuple[Unit, float, float, int, int, bool, dict[str, float]]]:
    return {unit.unit_id: (unit, unit.current_hp, unit.current_mana, unit.total_shields(), unit.dodge_charges,
                          unit.alive, {name: unit.stat(name) for name in ("attack", "defense", "speed")})
            for unit in battle.all_units()}


def r23_action_delta(actor: Unit, before: dict[str, Any]) -> float:
    """Price actual outcomes for all recipients, including friendly losses."""
    score = 0.0
    for unit, hp, mana, shields, dodges, alive, stats in before.values():
        loss = (hp - unit.current_hp) * 100.0 + (mana - unit.current_mana) * 18.0
        loss += (shields - unit.total_shields()) * 20.0 + (dodges - unit.dodge_charges) * 12.0
        loss += sum((stats[name] - unit.stat(name)) * weight
                    for name, weight in (("attack", 24.0), ("defense", 30.0), ("speed", 14.0)))
        if alive and not unit.alive:
            loss += 90.0
        score += loss if unit.player_id != actor.player_id else -loss * 1.3
    return score


def r23_attack_value(battle: Battle, actor: Unit, payload: dict[str, Any]) -> float:
    with ai_probe_rollback(battle):
        battle._forecasting_action = True
        battle.pending_followup_actions.clear()
        before = r23_action_state(battle)
        try:
            queued = battle.build_queued_action({"type": "attack", "unit_id": actor.unit_id, **payload})
            battle.resolve_queued_action(queued)
            while battle.pending_followup_actions:
                battle.resolve_queued_action(battle.pending_followup_actions.popleft())
        except (ActionError, KeyError, TypeError, ValueError):
            return -1000.0
        return r23_action_delta(actor, before) - 4.0


def electronic_repair_score(battle: Battle, actor: Unit, payload: dict[str, Any]) -> float:
    skill = actor.get_skill("electronic_repair")
    try:
        target = skill.candidate_by_payload(battle, payload)
        destination = Position(int(payload["x"]), int(payload["y"]))
    except (ActionError, KeyError, TypeError, ValueError):
        return -1000.0
    if target.player_id != actor.player_id or destination not in skill.legal_destinations(battle, actor, target):
        return -1000.0
    with ai_probe_rollback(battle):
        try:
            skill.execute(battle, actor, payload)
        except (ActionError, KeyError, TypeError, ValueError):
            return -1000.0
        if not target.alive or target.position != destination:
            return -1000.0
        position_value = reviewed_r17_position_value(battle, actor, target)
        return 90.0 + 45.0 * target.current_hp + position_value * 0.8


def r23_paid_skill_value(battle: Battle, actor: Unit, payload: dict[str, Any]) -> float:
    """Use the same outcome scale as Kiku's attacks, including actual prepaid mana."""
    with ai_probe_rollback(battle):
        battle._forecasting_action = True
        battle.pending_followup_actions.clear()
        before = r23_action_state(battle)
        try:
            queued = battle.build_queued_action({"type": "skill", "unit_id": actor.unit_id, **payload})
            skill = actor.get_skill(queued.payload["skill_code"])
            battle.prepay_skill_resources(skill, actor, queued.payload)
            queued.payload["resources_prepaid"] = True
            actor.notify_action_declared(battle, "skill", queued.payload)
            queued.payload["declared_source_attack"] = actor.stat("attack")
            battle.resolve_queued_action(queued)
            while battle.pending_followup_actions:
                battle.resolve_queued_action(battle.pending_followup_actions.popleft())
        except (ActionError, KeyError, TypeError, ValueError):
            return -1000.0
        return r23_action_delta(actor, before) - 8.0


def r215219_skill_score(battle: Battle, actor: Unit, payload: dict[str, Any]) -> float:
    """Replay both sides, then price the actual landing and short attack window."""
    with ai_probe_rollback(battle):
        battle._forecasting_action = True
        battle.pending_followup_actions.clear()
        before = r23_action_state(battle)
        positions = {unit.unit_id: unit.position for unit in battle.all_units()}
        position_value = {unit.unit_id: reviewed_r17_position_value(battle, actor, unit)
                          for unit in battle.all_units()}
        try:
            queued = battle.build_queued_action({"type": "skill", "unit_id": actor.unit_id, **payload})
            skill = actor.get_skill(queued.payload["skill_code"])
            battle.prepay_skill_resources(skill, actor, queued.payload)
            queued.payload["resources_prepaid"] = True
            actor.notify_action_declared(battle, "skill", queued.payload)
            battle.resolve_queued_action(queued)
            while battle.pending_followup_actions:
                battle.resolve_queued_action(battle.pending_followup_actions.popleft())
        except (ActionError, KeyError, TypeError, ValueError):
            return -1000.0
        code = str(payload.get("skill_code") or "")
        score = r23_action_delta(actor, before) - 8.0
        if code.startswith("bird_soul"):
            old_stats = before[actor.unit_id][6]
            attack_gain = max(0.0, actor.stat("attack") - old_stats["attack"])
            defense_gain = max(0.0, actor.stat("defense") - old_stats["defense"])
            enemies = [unit for unit in living_hostile_combatants(battle, actor.player_id)
                       if unit.position is not None and battle.unit_can_be_selected(unit, actor=actor)[0]]
            nearest = min((distance_between_units(battle, actor, unit) for unit in enemies), default=10**6)
            skill_reach = max(actor.targeting_range(), 3)
            if nearest > skill_reach + actor.normal_move_distance():
                score -= attack_gain * 24.0 * 1.3
            elif nearest > skill_reach:
                score -= attack_gain * 24.0 * 1.3 * 0.5
            threatened = any(not enemy.cannot_attack and
                             distance_between_units(battle, enemy, actor)
                             <= (0 if enemy.cannot_move or enemy.cannot_normal_move
                                 else enemy.normal_move_distance()) + enemy.targeting_range()
                             for enemy in enemies)
            if not threatened:
                score -= defense_gain * 30.0 * 1.3
        for unit in battle.all_units():
            if unit.unit_id not in positions or unit.position == positions[unit.unit_id]:
                continue
            gain = reviewed_r17_position_value(battle, actor, unit) - position_value[unit.unit_id]
            score += gain * (0.8 if unit.player_id == actor.player_id else -0.8)
        if code.startswith("bird_dash") and score <= 0:
            return -1000.0
        return score


def r23_passive_defense_value(battle: Battle, defender: Unit) -> float:
    """At most one next legal prevention opportunity; names alone add no value."""
    if not defender.alive or defender.position is None or defender.banished:
        return 0.0
    with ai_probe_rollback(battle):
        battle._forecasting_action = True
        defender.statuses = [status for status in defender.statuses if status.name != "被动封锁"]
        battle.resolving_action = None
        best = 0.0
        for attacker in living_hostile_combatants(battle, defender.player_id):
            with ai_probe_rollback(battle):
                battle.turn_number += 1
                battle.active_player = attacker.player_id
                battle._exclusive_turn_unit_id = attacker.unit_id
                attacker.turn_ready = True
                attacker.attacks_used = attacker.normal_move_steps_used = attacker.normal_move_actions_used = 0
                attacker.move_used = False
                if attacker.cannot_attack:
                    continue
                origin = attacker.position
                positions = [origin]
                if not attacker.cannot_move and not attacker.cannot_normal_move:
                    positions += battle.reachable_positions(attacker, max_distance=attacker.remaining_normal_move_distance(battle), use_movement_cost=True)
                for position in sorted(positions, key=lambda cell: (cell.distance_to(origin), cell.y, cell.x)):
                    attacker.position = position
                    try:
                        queued = battle.build_queued_action({"type": "attack", "unit_id": attacker.unit_id,
                                                           "target_unit_id": defender.unit_id})
                    except (ActionError, KeyError, TypeError, ValueError):
                        continue
                    loss = r21_queued_loss(battle, defender, queued)
                    if loss <= 0:
                        break
                    for option in battle.available_reaction_options(defender, queued):
                        if option.action_type != "skill" or option.timing not in {"passive", "reaction"}:
                            continue
                        skill = defender.get_skill(option.action_code)
                        reactions = reaction_payloads_for_option(battle, defender, queued, option.to_public_dict())
                        if not reactions and not option.preview.get("requires_target", True):
                            reactions = [{"type": "chain_react", "unit_id": defender.unit_id, "action_code": option.action_code}]
                        for reaction in reactions:
                            if not skill.can_react_with_payload(battle, defender, queued, reaction)[0]:
                                continue
                            with ai_probe_rollback(battle):
                                cost = skill.mana_cost_for_payload(battle, defender, reaction)
                                hp, alive = defender.current_hp, defender.alive
                                defender.spend_mana(cost)
                                skill.react(battle, defender, reaction, deepcopy(queued))
                                saved = loss - r21_queued_loss(battle, defender, queued)
                                saved -= max(0.0, hp - defender.current_hp) * 100.0
                                saved -= 90.0 if alive and not defender.alive else 0.0
                                best = max(best, saved - cost * 18.0)
                    break
        return max(0.0, best)


def r23_sun_slash_score(battle: Battle, actor: Unit, skill: Any, payload: dict[str, Any]) -> float:
    selected = battle.units.get(str(payload.get("target_unit_id") or ""))
    if selected is None:
        return -1000.0
    target = battle.effect_recipient(selected)
    with ai_probe_rollback(battle):
        battle._forecasting_action = True
        before = r23_action_state(battle)
        old_lock = target.get_status("被动封锁")
        old_ends = int(old_lock.duration or 0) if old_lock is not None else 0
        try:
            actor.spend_mana(skill.mana_cost_for_payload(battle, actor, payload))
            skill.execute(battle, actor, payload)
        except (ActionError, KeyError, TypeError, ValueError):
            return -1000.0
        value = r23_action_delta(actor, before)
        lock = target.get_status("被动封锁")
        if lock is not None and target.alive:
            added = max(0, int(lock.duration or 0) - old_ends)
            prevention = r23_passive_defense_value(battle, target) * min(1.0, added / 3.0)
            value += prevention if target.player_id != actor.player_id else -prevention * 1.3
        # Preserve the finite ultimate when ordinary double attacks have equal value.
        return value - 18.0


def r23_attack_plan_value(battle: Battle, actor: Unit) -> float:
    if not actor.can_take_turn_actions(battle) or actor.cannot_attack:
        return 0.0
    best = 0.0
    for spec in battle.basic_attack_action_specs(actor):
        resolved = battle.resolved_basic_attack_payload(actor, spec.get("attack_payload"))
        if not battle.basic_attack_resource_allowed(actor, resolved)[0]:
            continue
        action = {**spec, "preview": battle.basic_attack_preview_for_payload(actor, resolved)}
        for payload in attack_payloads_for_action(battle, actor, action):
            best = max(best, r23_attack_value(battle, actor, payload))
    return best


def r22_attack_value(battle: Battle, actor: Unit, target: Unit, payload: dict[str, Any]) -> float:
    value = r21_attack_value(battle, actor, target, payload)
    if actor.hero_code != "excel_r352" or value <= 0 or target.player_id == actor.player_id:
        return value
    with ai_probe_rollback(battle):
        previous = {unit.unit_id for unit in battle.all_units()}
        ctx = battle.resolve_attack_damage(actor, target, action_name="普攻", payload=payload)
        if ctx is None:
            return value
        original_position = actor.position
        risk_before = natsume_unit_danger(battle, actor)
        actor.notify_basic_attack_finished(battle, payload, [ctx])
        new_clones = [unit for unit in battle.all_units() if unit.unit_id not in previous
                      and unit.is_clone and unit.summoner_id == actor.unit_id]
        if new_clones and any(distance_between_units(battle, new_clones[0], enemy)
                              <= enemy.normal_move_distance() + enemy.targeting_range()
                              for enemy in living_hostile_combatants(battle, actor.player_id)):
            value += 8.0
        if actor.position is not None and original_position is not None:
            swap_positions = {actor.position}
            swap_positions.update(unit.position for unit in battle.all_units()
                                  if unit.is_clone and unit.summoner_id == actor.unit_id
                                  and unit.alive and not unit.banished and unit.position is not None
                                  and unit.position != original_position)
            risks = []
            for position in swap_positions:
                actor.position = position
                risks.append(natsume_unit_danger(battle, actor))
            value += 0.7 * (risk_before - sum(risks) / len(risks))
        return value


def r22_contact_value(battle: Battle, actor: Unit, *, allow_move: bool = True) -> float:
    if not actor.alive or actor.position is None or actor.cannot_attack or actor.attacks_used >= actor.attack_actions_per_turn():
        return 0.0
    with ai_probe_rollback(battle):
        origin = actor.position
        positions = [origin]
        if allow_move and not actor.cannot_move and not actor.cannot_normal_move:
            positions += battle.reachable_positions(actor, max_distance=actor.remaining_normal_move_distance(battle), use_movement_cost=True)
        best = 0.0
        for target in living_hostile_combatants(battle, actor.player_id):
            if not battle.unit_can_be_selected(target, actor=actor)[0]:
                continue
            for position in sorted(positions, key=lambda cell: (cell.distance_to(origin), cell.y, cell.x)):
                actor.position = position
                if battle.attack_target_allowed(actor, target)[0]:
                    best = max(best, r22_attack_value(battle, actor, target, {"target_unit_id": target.unit_id}))
                    break
        return max(0.0, best)


def r22_gazer_window_risk(battle: Battle, actor: Unit) -> float:
    if not actor.alive or actor.position is None:
        return 0.0
    with ai_probe_rollback(battle):
        hp = actor.current_hp
        battle.resolving_action = None
        for enemy in list(living_hostile_combatants(battle, actor.player_id)):
            if not actor.alive or enemy.cannot_attack:
                continue
            battle.turn_number += 1
            battle.active_player = enemy.player_id
            battle._exclusive_turn_unit_id = enemy.unit_id
            enemy.turn_ready = True
            enemy.attacks_used = enemy.normal_move_actions_used = enemy.normal_move_steps_used = 0
            enemy.move_used = False
            positions = [enemy.position]
            if not enemy.cannot_move and not enemy.cannot_normal_move:
                positions += battle.reachable_positions(enemy, max_distance=enemy.remaining_normal_move_distance(battle), use_movement_cost=True)
            for position in sorted(positions, key=lambda cell: (distance_to_position(battle, actor, cell), cell.y, cell.x)):
                enemy.position = position
                if not battle.attack_target_allowed(enemy, actor)[0]:
                    continue
                # Repeated attacks share this hero window; each later enemy hero has a fresh window.
                for _ in range(enemy.attack_actions_per_turn()):
                    if not actor.alive:
                        break
                    battle.resolve_attack_damage(enemy, actor, action_name="普攻")
                break
        loss = max(0.0, hp - actor.current_hp)
        if actor.alive:
            before = actor.current_hp
            battle.heal(HealContext(source=actor, target=actor, amount=0.25, action_name="自然回血"))
            loss = max(0.0, loss - (actor.current_hp - before))
        return loss * 100.0 + (90.0 if not actor.alive else 0.0)


def r22_ninja_shensu_score(battle: Battle, actor: Unit) -> float:
    skill = actor.get_skill("shensu")
    if not skill.can_use(battle, actor, {})[0] or actor.move_used or actor.cannot_normal_move or actor.cannot_move:
        return -1000.0
    before = r22_contact_value(battle, actor)
    with ai_probe_rollback(battle):
        cost = skill.mana_cost_for_payload(battle, actor, {})
        actor.spend_mana(cost)
        skill.execute(battle, actor, {})
        skill.finalize_use(battle, actor)
        after = r22_contact_value(battle, actor)
        reserve = 0.0
        if actor.current_mana < 0.5 and any(distance_between_units(battle, actor, enemy) <= enemy.normal_move_distance() + enemy.targeting_range()
                                           for enemy in living_hostile_combatants(battle, actor.player_id)):
            reserve = 20.0
        return after - before - cost * 18.0 - reserve


def r22_heal_score(battle: Battle, actor: Unit, skill: Any, payload: dict[str, Any]) -> float:
    selected = battle.units.get(str(payload.get("target_unit_id") or ""))
    if selected is None:
        return -1000.0
    target = battle.effect_recipient(selected)
    with ai_probe_rollback(battle):
        hp, mana, alive = target.current_hp, actor.current_mana, target.alive
        actor.spend_mana(skill.mana_cost_for_payload(battle, actor, payload))
        skill.execute(battle, actor, payload)
        delta = target.current_hp - hp
        value = delta * 200.0 + (50.0 if delta > 0 and hp <= 0.5 else 0.0)
        value -= (mana - actor.current_mana) * 18.0
        if alive and not target.alive:
            value -= 90.0
        return value


def r22_paid_reaction_score(battle: Battle, actor: Unit, queued: QueuedAction, payload: dict[str, Any], code: str) -> float:
    skill = actor.get_skill(code)
    if not skill.can_react_with_payload(battle, actor, queued, payload)[0]:
        return -1000.0
    targets = battle.effect_units(skill.chosen_targets(battle, actor, payload)) if code == "light_wall" else [battle.effect_recipient(actor)]
    before = {target.unit_id: r21_queued_loss(battle, target, queued) for target in targets}
    with ai_probe_rollback(battle):
        hp_before = {target.unit_id: target.current_hp for target in targets}
        alive_before = {target.unit_id: target.alive for target in targets}
        cost = skill.mana_cost_for_payload(battle, actor, payload)
        actor.spend_mana(cost)
        skill.react(battle, actor, payload, deepcopy(queued))
        prevention = sum(before[target.unit_id] - r21_queued_loss(battle, target, queued)
                         - max(0.0, hp_before[target.unit_id] - target.current_hp) * 100.0
                         - (90.0 if alive_before[target.unit_id] and not target.alive else 0.0)
                         for target in targets)
        return prevention - cost * 18.0 if prevention > 0 else -1000.0


def andrew_reaction_score(battle: Battle, actor: Unit, queued: QueuedAction,
                          payload: dict[str, Any], code: str) -> float:
    skill = actor.get_skill(code)
    if not skill.can_react_with_payload(battle, actor, queued, payload)[0]:
        return -1000.0
    units = list(battle.all_units())
    initial = {
        unit.unit_id: (unit.current_hp, unit.current_mana, unit.total_shields(), unit.alive,
                       unit.position, reviewed_r17_position_value(battle, actor, unit))
        for unit in units
    }

    def outcome(with_reaction: bool) -> float:
        with ai_probe_rollback(battle):
            try:
                source_action = deepcopy(queued)
                if with_reaction:
                    skill.react(battle, actor, payload, source_action)
                battle.resolve_queued_action(source_action)
            except (ActionError, KeyError, ValueError, TypeError):
                return -1000.0
            score = 0.0
            for unit in units:
                hp, mana, shields, alive, position, position_value = initial[unit.unit_id]
                loss = (hp - unit.current_hp) * 100.0
                loss += (mana - unit.current_mana) * 18.0
                loss += max(0, shields - unit.total_shields()) * 20.0
                if alive and not unit.alive:
                    loss += 90.0
                score += -loss if unit.player_id == actor.player_id else loss
                if unit.position != position:
                    delta = reviewed_r17_position_value(battle, actor, unit) - position_value
                    score += delta * (0.8 if unit.player_id == actor.player_id else -0.8)
            return score

    before = outcome(False)
    after = outcome(True)
    if before <= -1000.0 or after <= -1000.0:
        return -1000.0
    return after - before - (8.0 if code == "gladiator_roar" else 4.0)
