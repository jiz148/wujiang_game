from __future__ import annotations

import random
from contextlib import contextmanager
from dataclasses import dataclass
from itertools import combinations
from typing import Any, Callable, Iterable, Optional

from wujiang.engine.core import ActionError, Battle, DamageContext, Position, QueuedAction, Unit


AI_DIFFICULTIES = {"easy", "standard", "aggressive"}

SUPPORT_HERO_CODES = {"ellie", "bard", "element_hunter", "chanter", "excel_r139"}
SUMMON_SKILL_CODES = {
    "medusa",
    "thunder_god",
    "earth_walker",
    "split",
    "motor_horse",
    "summon_dragon",
    "summon_great_unicorn",
    "summon_mage_cloak",
    "floating_cannons",
    "judgment_stone",
    "world_seed",
    "royal_soldier",
    "summon_remi_bat",
}
HEAL_SKILL_CODES = {"heal", "heal_mount", "mech_enhancement"}
ALLY_BUFF_SKILL_CODES = {"defend_twice", "baptism", "chant", "experiment", "fried_inspire", "agency_contract", "rainbow_mirror"}
SELF_BUFF_SKILL_CODES = {
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
}
MOVE_SKILL_CODES = {"fly_leap", "fate_kick", "crazy_sand", "plasma_thruster", "mounted_leap", "jirobo_follow_step"}
MOVE_SKILL_CODES |= {"zero_dash", "fuma_pursuit", "true_blade_air_slash"}
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
}
REACTION_SHIELD_CODES = {
    "magic_wall",
    "light_wall",
    "stone_wall",
    "ion_shield",
    "quantum_shield",
    "protection",
    "natsume_wind_wall",
    "floating_cannon_cover",
}
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


def choose_turn_action(battle: Battle, actor: Unit, difficulty: str) -> dict[str, Any]:
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


def choose_turn_bundle_action(battle: Battle, units: Iterable[Unit], difficulty: str) -> tuple[dict[str, Any], Optional[Unit]]:
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
    for action in action_snapshot.get("actions", []):
        if not action.get("available"):
            continue
        kind = str(action.get("kind") or "")
        if kind == "move":
            move_candidates.extend(build_move_candidates(battle, actor, action, profile))
        elif kind == "attack":
            candidates.extend(build_attack_candidates(battle, actor, action, profile))
        elif kind == "skill":
            candidates.extend(build_skill_candidates(battle, actor, action, profile, instant_only=False))
    return candidates, move_candidates


def choose_instant_action(battle: Battle, units: Iterable[Unit], difficulty: str) -> Optional[dict[str, Any]]:
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


def build_move_candidates(
    battle: Battle,
    actor: Unit,
    action: dict[str, Any],
    profile: DifficultyProfile,
) -> list[AICandidate]:
    if battle.mounted_unit_for(actor) is not None:
        return []
    role = hero_style(actor)
    candidates: list[AICandidate] = []
    if getattr(actor, "hero_code", "") == "excel_r118":
        candidates.extend(build_zero_crossing_move_candidates(battle, actor, role, profile))
    for cell in preview_positions(action.get("preview", {}).get("cells")):
        payload = {"type": "move", "unit_id": actor.unit_id, "x": cell.x, "y": cell.y}
        if not payload_is_legal(battle, payload):
            continue
        score = score_move_destination(battle, actor, cell, role, profile)
        if getattr(actor, "hero_code", "") == "judgment_stone":
            score += judgment_stone_collision_move_score(battle, actor, cell, profile)
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
                score = score_move_destination(battle, actor, exit_cell, role, profile)
                mana_room = max(0.0, actor.max_mana() - actor.current_mana)
                for crossed in crossing_events:
                    if crossed.player_id == actor.player_id:
                        score -= friendly_fire_penalty(crossed)
                    else:
                        score += actor.stat("attack") * 85.0 + hostile_unit_value(crossed) * 0.4
                score += min(mana_room, len(crossing_events) * 0.5) * 24.0
                candidates.append(
                    AICandidate(
                        payload=payload,
                        score=score,
                        summary=f"move:zero-crossings={len(crossing_events)}:{exit_cell.x},{exit_cell.y}",
                    )
                )
    candidates.sort(key=lambda candidate: candidate.score, reverse=True)
    return candidates[:24]


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
        if not attack_payload_has_effective_enemy_impact(battle, actor, payload):
            continue
        score = score_attack_payload(battle, actor, payload, profile)
        candidates.append(AICandidate(payload=payload, score=score, summary=f"attack:{payload.get('target_unit_id')}"))
    return candidates


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
    if code in {"interference", "noise_wave"}:
        payloads = dedupe_area_payloads_by_affected_units(battle, payloads, clones_and_summons_only=code == "interference")
    if code == "fuma_shuriken":
        payloads = dedupe_damage_area_payloads(battle, payloads)
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
        if skill_payload_requires_enemy_impact(battle, actor, action, payload) and not skill_payload_has_effective_enemy_impact(
            battle,
            actor,
            action,
            payload,
        ):
            continue
        score = score_skill_payload(battle, actor, action, payload, profile, instant_only=instant_only)
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
    for unit in battle.effect_units_at_cells(cells):
        if unit.player_id == actor.player_id or unit.unit_id in seen:
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
    if code == "split":
        return split_payloads(battle, actor, action)
    if code == "descent_moment":
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
    payloads: list[dict[str, Any]] = []
    for pattern in selection.get("patterns", []):
        cells = preview_positions(pattern)
        cell_keys = {(cell.x, cell.y) for cell in cells}
        for target in battle.enemy_units(actor.player_id):
            if not any((cell.x, cell.y) in cell_keys for cell in battle.unit_cells(target)):
                continue
            for target_skill in target.skills:
                if getattr(target_skill, "timing", None) != "active":
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
    try:
        mimic = actor.get_skill("mimic_skill")
    except Exception:
        return []
    preview = action.get("preview", {}) or mimic.preview(battle, actor)
    selection = dict(preview.get("selection") or {})
    target_entries = selection.get("targets") if selection.get("mode") == "mimic_skill" else None
    target_ids = [str(entry.get("unit_id")) for entry in target_entries or [] if isinstance(entry, dict) and entry.get("unit_id")]
    if not target_ids:
        target_ids = [str(unit_id) for unit_id in preview.get("target_unit_ids", [])]
    payloads: list[dict[str, Any]] = []
    for target_id in target_ids:
        try:
            target = battle.get_unit(target_id)
        except Exception:
            continue
        for copied in target.skills:
            if copied.timing not in {"active", "instant"} or copied.code == "mimic_skill":
                continue
            copied_action = copied_skill_action(battle, actor, copied)
            try:
                copied_payloads = skill_payloads_for_action(battle, actor, copied_action)
            except Exception:
                continue
            for copied_payload in copied_payloads:
                payload = {
                    "type": "skill",
                    "unit_id": actor.unit_id,
                    "skill_code": "mimic_skill",
                    "target_unit_id": target.unit_id,
                    "mimic_skill_code": copied.code,
                    "copied_payload": dict(copied_payload),
                }
                payloads.append(payload)
    return dedupe_payloads(payloads)


def mimic_payload_context(battle: Battle, actor: Unit, payload: dict[str, Any]) -> Optional[tuple[Unit, Any, dict[str, Any], dict[str, Any]]]:
    try:
        target = battle.get_unit(str(payload.get("target_unit_id") or ""))
        copied_code = str(payload.get("mimic_skill_code") or payload.get("copied_skill_code") or "")
        if not copied_code or copied_code == "mimic_skill":
            return None
        copied = target.get_skill(copied_code)
        copied_payload = dict(payload.get("mimic_payload") or payload.get("copied_payload") or {})
        copied_payload["type"] = "skill"
        copied_payload["unit_id"] = actor.unit_id
        copied_payload["skill_code"] = copied.code
        return target, copied, copied_payload, copied_skill_action(battle, actor, copied)
    except Exception:
        return None


def royal_soldier_payloads(battle: Battle, actor: Unit, action: dict[str, Any]) -> list[dict[str, Any]]:
    preview = action.get("preview", {}) or {}
    allocations = (preview.get("selection") or {}).get("allocations") or [
        {"attack": 5, "defense": 4, "range": 1},
        {"attack": 5, "defense": 3, "range": 2},
        {"attack": 4, "defense": 4, "range": 2},
        {"attack": 4, "defense": 3, "range": 3},
    ]
    payloads: list[dict[str, Any]] = []
    for cell in preview_positions(preview.get("cells")):
        for allocation in allocations:
            payloads.append(
                {
                    "type": "skill",
                    "unit_id": actor.unit_id,
                    "skill_code": "royal_soldier",
                    "x": cell.x,
                    "y": cell.y,
                    "attack": int(allocation.get("attack", 4)),
                    "defense": int(allocation.get("defense", 3)),
                    "range": int(allocation.get("range", 3)),
                }
            )
    return dedupe_payloads(payloads)


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
        _, copied, _ = wrapper.target_skill(battle, actor, {})  # type: ignore[attr-defined]
    except Exception:
        return []
    copied_action = copied_skill_action(battle, actor, copied)
    try:
        copied_payloads = skill_payloads_for_action(battle, actor, copied_action)
    except Exception:
        return []
    return dedupe_payloads(
        [
            {
                "type": "skill",
                "unit_id": actor.unit_id,
                "skill_code": "agency_borrowed_skill",
                "contract_payload": dict(copied_payload),
            }
            for copied_payload in copied_payloads
        ]
    )


def agency_borrowed_payload_context(battle: Battle, actor: Unit, payload: dict[str, Any]) -> Optional[tuple[Unit, Any, dict[str, Any], dict[str, Any]]]:
    try:
        wrapper = actor.get_skill("agency_borrowed_skill")
        carrier, copied, copied_payload = wrapper.target_skill(battle, actor, payload)  # type: ignore[attr-defined]
        copied_payload["type"] = "skill"
        copied_payload["unit_id"] = actor.unit_id
        copied_payload["skill_code"] = copied.code
        return carrier, copied, copied_payload, copied_skill_action(battle, actor, copied)
    except Exception:
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
    skill = actor.get_skill("split")
    preview = action.get("preview", {}) or {}
    candidate_cells = preview_positions(preview.get("cells"))
    required = int((preview.get("selection") or {}).get("required_cells") or getattr(skill, "clone_count", 3))
    probe = skill._clone_probe(actor)  # type: ignore[attr-defined]
    selected: list[Position] = []
    occupied: set[tuple[int, int]] = set()
    for cell in sorted(candidate_cells, key=lambda item: distance_to_position(battle, actor, item)):
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
        "skill_code": "split",
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
        for group in combinations(candidate_cells[:24], required):
            payload = {"cells": positions_to_payload(group)}
            try:
                skill.selected_growth_cells(battle, actor, payload, required)
            except Exception:
                continue
            selected_cells = list(group)
            break
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
    preview = option.get("preview", {}) or {}
    selection = dict(preview.get("selection") or {})
    mode = str(selection.get("mode") or "")
    if action_code in REACTION_SHIELD_CODES:
        target_ids = shield_targets_for_reaction(battle, reactor, queued_action, preview)
        if target_ids:
            return [{**base_payload, "target_unit_ids": target_ids}]
        return [base_payload]
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
        return [proxy.unit_id] if proxy is not None else []
    threatened.sort(key=lambda unit: (incoming_threat_score(battle, unit, queued_action), unit.current_hp, -unit.level))
    selection = dict(preview.get("selection") or {})
    max_targets = int(selection.get("max_targets") or len(threatened))
    return [unit.unit_id for unit in threatened[:max(1, max_targets)]]


def score_move_destination(
    battle: Battle,
    actor: Unit,
    destination: Position,
    role: str,
    profile: DifficultyProfile,
) -> float:
    enemies = [unit for unit in battle.enemy_units(actor.player_id) if unit.alive and unit.position is not None and not unit.banished]
    allies = [unit for unit in battle.player_units(actor.player_id) if unit.unit_id != actor.unit_id and unit.alive and unit.position is not None and not unit.banished]
    if not enemies:
        return -10.0
    nearest_enemy = min(distance_to_position(battle, enemy, destination) for enemy in enemies)
    current_distance = min(distance_between_units(battle, actor, enemy) for enemy in enemies)
    score = float(current_distance - nearest_enemy) * 6.0
    offensive_gain = offensive_reach_score_at(battle, actor, destination)
    score += offensive_gain * 18.0
    score += great_fire_funeral_alignment_score_at(battle, actor, destination)
    if role == "support":
        nearest_ally = min((distance_to_position(battle, ally, destination) for ally in allies), default=2)
        score += max(0.0, 3.0 - nearest_ally) * 6.0
        score += max(0.0, nearest_enemy - 1.0) * profile.support_bonus
    else:
        score += max(0.0, 4.0 - nearest_enemy) * (4.0 + profile.aggressive_bonus / 6.0)
    return score


def score_attack_payload(
    battle: Battle,
    actor: Unit,
    payload: dict[str, Any],
    profile: DifficultyProfile,
) -> float:
    if not payload.get("target_unit_id"):
        resolved_payload = battle.resolved_basic_attack_payload(actor, payload)
        cells = battle.payload_positions(resolved_payload, "attack_cells")
        if not cells:
            cells = preview_positions(payload.get("cells"))
        attack_power = battle.basic_attack_preview_power(actor, payload)
        score = 0.0
        for target in battle.effect_units_at_cells(cells):
            if target.player_id == actor.player_id:
                score -= friendly_fire_penalty(target)
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
            score += expected_damage * 100.0
            if expected_damage >= target.current_hp - 1e-9:
                score += 95.0
            score += hostile_unit_value(target) * 0.65
        if hero_style(actor) != "support":
            score += profile.aggressive_bonus
        return score
    target = battle.get_unit(str(payload["target_unit_id"]))
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
    score = expected_damage * 100.0
    if expected_damage >= target.current_hp - 1e-9:
        score += 95.0
    score += hostile_unit_value(target) * 0.8
    if hero_style(actor) != "support":
        score += profile.aggressive_bonus
    if str(payload.get("attack_variant") or "") == "triple":
        score += 24.0
    return score


def score_skill_payload(
    battle: Battle,
    actor: Unit,
    action: dict[str, Any],
    payload: dict[str, Any],
    profile: DifficultyProfile,
    *,
    instant_only: bool,
) -> float:
    code = str(action.get("code") or payload.get("skill_code") or "")
    if code == "mimic_skill":
        context = mimic_payload_context(battle, actor, payload)
        if context is None:
            return -10.0
        _, _, copied_payload, copied_action = context
        return score_skill_payload(battle, actor, copied_action, copied_payload, profile, instant_only=instant_only) - 4.0
    if code == "agency_borrowed_skill":
        context = agency_borrowed_payload_context(battle, actor, payload)
        if context is None:
            return -10.0
        _, _, copied_payload, copied_action = context
        return score_skill_payload(battle, actor, copied_action, copied_payload, profile, instant_only=instant_only)
    if code == "agency_contract":
        return agency_contract_score(battle, actor, payload, profile)
    if code == "weapon_copy":
        return weapon_copy_score(battle, actor, payload, profile)
    if code == "deadly_bow":
        return deadly_bow_score(battle, actor, action, payload, profile)
    if code == "migratory_bird_mark":
        return migratory_bird_mark_score(battle, actor, action, payload, profile)
    skill = skill_from_ai_action(actor, action, code)
    targets = skill_effect_units(battle, actor, skill, payload)
    role = hero_style(actor)
    if code in MOVE_SKILL_CODES:
        destination = payload_destination(payload)
        if destination is None:
            return -5.0
        score = score_move_destination(battle, actor, destination, role, profile) + 10.0
        if code == "zero_dash":
            score += zero_dash_score(battle, actor, skill, payload)
        if code == "fuma_pursuit":
            score += fuma_pursuit_score(battle, actor, skill, payload, profile)
        if code == "crazy_sand":
            score += skill_damage_score(battle, actor, skill, payload, profile)
        if code == "true_blade_air_slash":
            score += true_blade_air_slash_score(battle, actor, skill, payload, profile)
        return score
    if code == "judgment_stone":
        return judgment_stone_score(battle, actor, payload, profile)
    if code == "world_seed":
        return world_seed_score(battle, actor, payload, profile)
    if code in SUMMON_SKILL_CODES:
        destination = payload_destination(payload)
        score = 42.0
        if destination is not None:
            score += summon_position_score(battle, actor, destination)
        if code in {"earth_walker", "split"}:
            score += 10.0
        return score
    if code == "nian_dragon_dance":
        missing_hp = max(0.0, actor.max_health - actor.current_hp)
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
        if healed is None:
            healed = actor
        missing = max(0.0, healed.max_health - healed.current_hp)
        score = missing * 120.0
        if code == "mech_enhancement":
            score += 24.0
        return score
    if code == "rainbow_mirror":
        return rainbow_mirror_score(battle, actor, payload, targets, profile)
    if code in ALLY_BUFF_SKILL_CODES:
        target = primary_target_unit(battle, payload, targets)
        if target is None:
            return -2.0
        score = ally_buff_score(battle, actor, code, target, profile)
        return score
    if code in SELF_BUFF_SKILL_CODES:
        return self_buff_score(battle, actor, code, profile)
    if code == "great_funeral":
        return great_fire_funeral_score(battle, actor, skill, payload, profile)
    if code in {"stance", "great_holy_light", "plant_growth", "smoke_spray"}:
        return field_skill_score(battle, actor, code, targets, profile)
    if code in {"drain_mana", "large_drain_mana"}:
        return drain_mana_score(battle, actor, targets, profile)
    if code == "kaiser_fist":
        return kaiser_fist_score(battle, actor, skill, payload, profile)
    if code == "interference":
        return interference_score(battle, actor, targets, profile)
    if code == "noise_wave":
        return noise_wave_score(actor, targets, profile)
    if code == "purify_mana":
        return purify_mana_score(battle, actor, payload, targets, profile)
    if code == "sacred_duel":
        return sacred_duel_score(battle, actor, payload, targets, profile)
    if code == "fuma_trap":
        return fuma_trap_score(battle, actor, payload, profile)
    if code == "fantasy_move":
        return fantasy_move_score(battle, actor, skill, payload, targets, profile)
    if code == "friendly_mirror":
        return friendly_mirror_score(battle, actor, profile)
    if code == "electric_wind":
        return electric_wind_score(actor, targets, profile)
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
    attacker = battle.get_unit(queued_action.actor_id)
    proxy_target = battle.reaction_proxy_target(reactor, queued_action) or reactor
    threat = incoming_threat_score(battle, proxy_target, queued_action)
    if code in REACTION_SHIELD_CODES or code == "block":
        if queued_action.payload.get("ignore_shield"):
            return -20.0
        score = threat + 20.0
        if proxy_target.current_hp <= max(0.25, threat / 100.0):
            score += 55.0
        if queued_action.payload.get("half_ignore_shield"):
            score -= 15.0
        return score
    if code == "counter":
        expected = estimate_damage(battle, attacker, battle.basic_attack_preview_power(reactor), ignore_shield=False, half_ignore_shield=False)
        score = expected * 90.0 + hostile_unit_value(attacker) * 0.3
        if expected >= attacker.current_hp - 1e-9:
            score += 80.0
        return score
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
        if payload.get("target_unit_id"):
            expected = estimate_damage(battle, attacker, battle.basic_attack_preview_power(reactor))
            score += expected * 85.0 + profile.aggressive_bonus
        return score
    if code == "card_transposition":
        destination = payload_destination(payload)
        if destination is None:
            return -5.0
        return threat + score_move_destination(battle, reactor, destination, hero_style(reactor), profile) / 2.0 + 22.0
    if code == "knockback":
        return threat * 0.8 + 18.0
    return 0.0


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
        active_hits = sum(1 for unit in enemies if unit.player_id == battle.active_player and battle.unit_belongs_to_current_turn(unit))
        return active_hits * (80.0 if instant_only else 40.0) + len(enemies) * 20.0
    if code == "paralyzing_glove":
        return sum(hostile_unit_value(unit) * 0.6 for unit in enemies) + 45.0
    if code == "curse":
        return sum(unit.current_hp * 80.0 for unit in enemies)
    if code == "doom_light":
        return len(enemies) * 32.0 + sum(unit.current_hp * 40.0 for unit in enemies)
    if code in {"complete_burn", "blizzard"}:
        return len(enemies) * 24.0
    if code == "morning_holy_light":
        return len(enemies) * 30.0 + sum(40.0 for unit in enemies if unit.attribute == "暗")
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
        return len(enemies) * 52.0
    if code == "sun_slash":
        return sum(
            18.0
            + sum(1 for skill in unit.skills if skill.passive or skill.timing == "passive") * 36.0
            for unit in enemies
        )
    if code == "heaven_punishment":
        selected_code = str(payload.get("disabled_skill_code") or "")
        target = primary_target_unit(battle, payload, targets)
        if target is None or not selected_code:
            return -120.0
        selected = next((skill for skill in target.skills if skill.code == selected_code and skill.timing == "active"), None)
        if selected is None or any(getattr(status, "skill_code", None) == selected_code for status in target.statuses):
            return -120.0
        score = 55.0
        if selected_code in DAMAGING_SKILL_CODES or selected_code in CONTROL_SKILL_CODES:
            score += 35.0
        if selected_code in SUMMON_SKILL_CODES or selected_code in HEAL_SKILL_CODES:
            score += 24.0
        if getattr(selected, "max_uses_per_battle", None) == 1 and getattr(selected, "uses_this_battle", 0) == 0:
            score += 20.0
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
    cells = skill_effect_cells(battle, actor, skill, payload)
    impact = probe_skill_damage_impact(
        battle,
        actor,
        skill,
        payload,
        target,
        actor.stat("attack") + 1,
        cells=cells,
        ignore_shield=False,
        half_ignore_shield=False,
    )
    score = impact.damage * 100.0 + hostile_unit_value(target) * 0.45 + profile.aggressive_bonus
    if impact.changed_target and impact.damage <= 0:
        score += 42.0
    if impact.damage <= 0:
        score += min(2.0, max(0.0, actor.max_mana() - actor.current_mana)) * 28.0
    return score


def interference_score(
    battle: Battle,
    actor: Unit,
    targets: list[Unit],
    profile: DifficultyProfile,
) -> float:
    score = 0.0
    for target in targets:
        if target.is_clone:
            score += (80.0 + hostile_unit_value(target) * 0.45) if target.player_id != actor.player_id else -friendly_fire_penalty(target)
        elif target.is_summon and target.player_id != actor.player_id:
            score += 70.0 + hostile_unit_value(target) * 0.55
    return score + profile.aggressive_bonus if score > 0 else score - 20.0


def noise_wave_score(actor: Unit, targets: list[Unit], profile: DifficultyProfile) -> float:
    score = 0.0
    for target in targets:
        if target.has_status("乱音电波"):
            continue
        if target.player_id != actor.player_id:
            active_skills = sum(1 for skill in target.skills if skill.timing == "active")
            score += 38.0 + min(active_skills, 3) * 8.0 + hostile_unit_value(target) * 0.12
        else:
            score -= 38.0 + ally_unit_value(target) * 0.2
    return score + profile.aggressive_bonus if score > 0 else score - 12.0


def electric_wind_score(actor: Unit, targets: list[Unit], profile: DifficultyProfile) -> float:
    score = 0.0
    for target in targets:
        if target.has_status("电风"):
            continue
        active_skills = sum(1 for skill in target.skills if skill.timing in {"active", "instant"})
        if target.player_id != actor.player_id:
            score += 34.0 + min(active_skills, 4) * 10.0 + max(0.0, target.stat("speed") - 1.0) * 4.0
            score += hostile_unit_value(target) * 0.12
        else:
            score -= 42.0 + active_skills * 8.0 + ally_unit_value(target) * 0.2
    return score + profile.aggressive_bonus if score > 0 else score - 16.0


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
    crossed = battle.path_crossing_units(actor, path)
    score = 0.0
    for target in crossed:
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
            cells=[],
            ignore_shield=False,
            half_ignore_shield=False,
        )
        score += impact.damage * 100.0 + hostile_unit_value(target) * 0.35
        if impact.changed_target and impact.damage <= 0:
            score += 30.0
    mana_room = max(0.0, actor.max_mana() - actor.current_mana)
    score += min(mana_room, len(crossed) * 0.5) * 24.0
    return score


def fuma_pursuit_score(
    battle: Battle,
    actor: Unit,
    skill: Any,
    payload: dict[str, Any],
    profile: DifficultyProfile,
) -> float:
    direction = payload_step_direction(payload)
    if direction is None:
        return -20.0
    try:
        cells = list(skill.line_for_direction(battle, actor, direction))[:4]
    except Exception:
        return -20.0
    return score_damage_cells(battle, actor, skill, payload, cells, profile, ignore_shield=True)


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
    cells = [
        Position(x, y)
        for x in range(max(0, center.x - 1), min(battle.width, center.x + 2))
        for y in range(max(0, center.y - 1), min(battle.height, center.y + 2))
    ]
    score = 0.0
    for target in battle.effect_units_at_cells(cells):
        if target.unit_id == actor.unit_id:
            continue
        if target.player_id == actor.player_id:
            score -= friendly_fire_penalty(target) * 0.8
        else:
            damage = estimate_damage(battle, target, 3.0, ignore_shield=True)
            score += damage * 105.0 + hostile_unit_value(target) * 0.35
    return score + profile.aggressive_bonus if score > 0 else score - 12.0


def fantasy_move_score(
    battle: Battle,
    actor: Unit,
    skill: Any,
    payload: dict[str, Any],
    targets: list[Unit],
    profile: DifficultyProfile,
) -> float:
    target = primary_target_unit(battle, payload, targets)
    destination = payload_destination(payload)
    if target is None or destination is None:
        return -100.0
    score = skill_damage_score(battle, actor, skill, payload, profile)
    score += 45.0 + max(0, distance_between_units(battle, actor, target) - distance_to_position(battle, actor, destination)) * 8.0
    return score


def rainbow_mirror_score(
    battle: Battle,
    actor: Unit,
    payload: dict[str, Any],
    targets: list[Unit],
    profile: DifficultyProfile,
) -> float:
    target = primary_target_unit(battle, payload, targets)
    destination = payload_destination(payload)
    if target is None or destination is None or target.moved_this_turn:
        return -100.0
    score = 28.0 + ally_unit_value(target) * 0.18
    score += score_move_destination(battle, target, destination, hero_style(target), profile) * 0.35
    if target.unit_id == actor.unit_id:
        score -= 24.0
    return score


def friendly_mirror_score(battle: Battle, actor: Unit, profile: DifficultyProfile) -> float:
    threats = [
        unit
        for unit in battle.enemy_units(actor.player_id)
        if unit.alive and unit.position is not None and not unit.banished and unit.stat("attack") >= 3
    ]
    if not threats:
        return -25.0
    return 58.0 + sum(hostile_unit_value(unit) * 0.12 for unit in threats) + profile.support_bonus


def true_blade_air_slash_score(
    battle: Battle,
    actor: Unit,
    skill: Any,
    payload: dict[str, Any],
    profile: DifficultyProfile,
) -> float:
    target_id = str(payload.get("target_unit_id") or "")
    if not target_id:
        return -20.0
    target = battle.get_unit(target_id)
    impact = probe_skill_damage_impact(
        battle,
        actor,
        skill,
        payload,
        target,
        target.stat("defense") + 1,
        cells=[],
        ignore_shield=True,
        half_ignore_shield=False,
    )
    mana_gain = min(target.current_mana, max(0.0, actor.max_mana() - actor.current_mana))
    score = impact.damage * 100.0 + mana_gain * 22.0 + hostile_unit_value(target) * 0.45
    if impact.damage >= target.current_hp - 1e-9:
        score += 90.0
    return score + profile.aggressive_bonus


def undead_boy_devour_score(
    battle: Battle,
    actor: Unit,
    skill: Any,
    payload: dict[str, Any],
    targets: list[Unit],
    profile: DifficultyProfile,
) -> float:
    target = primary_target_unit(battle, payload, targets)
    if target is None or target.player_id == actor.player_id:
        return -20.0
    impact = probe_skill_raw_damage_impact(
        battle,
        actor,
        skill,
        target,
        raw_damage=round(target.current_hp / 2, 4),
        ignore_shield=True,
    )
    missing_hp = max(0.0, actor.max_health - actor.current_hp)
    healing = min(actor.current_hp, missing_hp)
    return impact.damage * 100.0 + healing * 110.0 + hostile_unit_value(target) * 0.4 + profile.aggressive_bonus


def illumination_light_score(
    battle: Battle,
    actor: Unit,
    skill: Any,
    payload: dict[str, Any],
    targets: list[Unit],
    profile: DifficultyProfile,
) -> float:
    enemies = [target for target in targets if target.player_id != actor.player_id]
    if not enemies:
        return -8.0
    score = 0.0
    for target in enemies:
        impact = probe_skill_damage_impact(
            battle,
            actor,
            skill,
            payload,
            target,
            4.0,
            cells=[],
            ignore_shield=target.attribute == "暗",
            half_ignore_shield=False,
        )
        score += impact.damage * 100.0 + hostile_unit_value(target) * 0.4
        if impact.damage >= target.current_hp - 1e-9:
            score += 90.0
    return score + profile.aggressive_bonus


def ally_buff_score(battle: Battle, actor: Unit, code: str, target: Unit, profile: DifficultyProfile) -> float:
    target_value = ally_unit_value(target)
    missing_hp = max(0.0, target.max_health - target.current_hp)
    if code == "experiment":
        return target_value * 0.9 + 48.0 + missing_hp * 50.0
    if code == "defend_twice":
        return target_value * 0.4 + 18.0
    if code == "baptism":
        return target_value * 0.35 + 10.0
    if code == "chant":
        if has_mana_point_skill(target):
            return 55.0 + target_value * 0.25
        return 8.0
    if code == "fried_inspire":
        if target.has_status("鼓舞"):
            return -6.0
        return 48.0 + target_value * 0.2
    return 12.0


def agency_contract_score(battle: Battle, actor: Unit, payload: dict[str, Any], profile: DifficultyProfile) -> float:
    if actor.has_status("代行契约附着"):
        enemies = [
            unit
            for unit in battle.enemy_units(actor.player_id)
            if unit.alive and unit.position is not None and not unit.banished and distance_between_units(battle, actor, unit) <= 1
        ]
        return len(enemies) * 85.0 + 16.0
    try:
        target = battle.get_unit(str(payload.get("target_unit_id") or ""))
    except Exception:
        return -8.0
    copied_code = str(payload.get("copied_skill_code") or "")
    score = 46.0 + ally_unit_value(target) * 0.15
    if copied_code in DAMAGING_SKILL_CODES or copied_code in CONTROL_SKILL_CODES:
        score += profile.aggressive_bonus + 14.0
    if copied_code in HEAL_SKILL_CODES or copied_code in ALLY_BUFF_SKILL_CODES:
        score += profile.support_bonus + 10.0
    stat_name = str(payload.get("stat_name") or "")
    if stat_name in {"attack", "speed", "attack_range"}:
        score += 6.0
    return score


def weapon_copy_score(battle: Battle, actor: Unit, payload: dict[str, Any], profile: DifficultyProfile) -> float:
    try:
        target = battle.get_unit(str(payload.get("target_unit_id") or ""))
    except Exception:
        return -8.0
    if actor.has_status("武装复制"):
        return -6.0
    attack_gain = max(0.0, target.stat("attack") - actor.stat("attack"))
    copied_traits = sum(1 for trait in getattr(target, "traits", []) if trait.name in {"攻击吸血", "攻击吸魔"})
    score = 18.0 + attack_gain * 34.0 + copied_traits * 26.0
    if target.player_id != actor.player_id:
        score += profile.aggressive_bonus
    else:
        score += profile.support_bonus
    return score


def deadly_bow_score(
    battle: Battle,
    actor: Unit,
    action: dict[str, Any],
    payload: dict[str, Any],
    profile: DifficultyProfile,
) -> float:
    try:
        skill = skill_from_ai_action(actor, action, "deadly_bow")
        cells = skill_effect_cells(battle, actor, skill, payload)
    except Exception:
        return -12.0
    damage = max(0.0, float(getattr(actor, "mana_points", 0.0)))
    if damage <= 0:
        return -10.0
    score = profile.aggressive_bonus
    for target in battle.effect_units_at_cells(cells):
        if target.player_id == actor.player_id:
            score -= friendly_fire_penalty(target)
            continue
        score += damage * 100.0 + hostile_unit_value(target) * 0.45
        if damage >= target.current_hp - 1e-9:
            score += 90.0
    return score


def migratory_bird_mark_score(
    battle: Battle,
    actor: Unit,
    action: dict[str, Any],
    payload: dict[str, Any],
    profile: DifficultyProfile,
) -> float:
    try:
        skill = skill_from_ai_action(actor, action, "migratory_bird_mark")
    except Exception:
        return -8.0
    targets = skill_effect_units(battle, actor, skill, payload)
    if not targets:
        return -8.0
    score = skill_damage_score(battle, actor, skill, payload, profile)
    active_mist = any(getattr(effect, "name", "") == "无常之雾" for effect in battle.field_effects)
    if active_mist:
        cells = skill_effect_cells(battle, actor, skill, payload)
        for target in targets:
            if target.player_id == actor.player_id or target.has_status("侯鸟标记"):
                continue
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
            if damage < target.current_hp - 1e-9:
                score -= 180.0
    return score


def self_buff_score(battle: Battle, actor: Unit, code: str, profile: DifficultyProfile) -> float:
    enemies = [unit for unit in battle.enemy_units(actor.player_id) if unit.alive and unit.position is not None and not unit.banished]
    if code == "mountain_awakening":
        counters = sum(1 for status in actor.statuses if status.name == "山神计数点")
        return 1000.0 if counters >= 8 else -8.0
    if code == "nian_spirit_pressure":
        return 84.0 if actor.get_status("灵压") is None and enemies else -8.0
    if code == "black_cat_form":
        active = actor.get_status("化猫") is not None
        skill_threats = sum(1 for enemy in enemies if any(getattr(skill, "timing", None) == "active" for skill in enemy.skills))
        attack_targets = battle.action_snapshot_for(actor).get("attack_targets") or []
        if active:
            return 92.0 if skill_threats == 0 and attack_targets else -8.0
        if not enemies or skill_threats == 0:
            return -8.0
        if actor.current_hp <= 0.5:
            return 128.0
        return 96.0 if not attack_targets else -8.0
    if code == "big_avalanche":
        active_weather = any(getattr(effect, "weather_name", "") == "大雪崩" for effect in battle.field_effects)
        return 72.0 if enemies and not active_weather else -8.0
    if code == "martial_god_seal":
        if actor.has_status("魔界武神之印"):
            return -8.0
        missing_hp = max(0.0, actor.max_health - actor.current_hp)
        missing_mana = max(0.0, actor.max_mana() + 2.0 - actor.current_mana)
        return 74.0 + missing_hp * 90.0 + min(2.0, missing_mana) * 18.0 if enemies else 24.0
    if code == "pandemonium":
        active_weather = any(getattr(effect, "weather_name", "") == "万魔殿" for effect in battle.field_effects)
        return 88.0 + profile.aggressive_bonus if enemies and not active_weather else -8.0
    if code == "sky_sanctuary":
        return 92.0 + profile.aggressive_bonus if enemies and not battle.has_weather("天空的圣域") else -8.0
    if code == "wetland_grassland":
        return 76.0 + profile.support_bonus if enemies and not battle.has_weather("湿地草原") else -8.0
    if code == "floating_cannon_berserk":
        if actor.get_status("浮游炮狂暴化") is not None:
            return -8.0
        cannons = [
            unit
            for unit in battle.player_units(actor.player_id)
            if getattr(unit, "hero_code", "") == "floating_cannon"
            and unit.summoner_id == actor.unit_id
            and unit.alive
            and unit.position is not None
        ]
        # This free toggle must happen before any ordinary action closes Sakura's start phase.
        return 1000.0 + len(cannons) * 12.0 if cannons and enemies else -8.0
    if code == "wuchang_mist":
        if any(getattr(effect, "name", "") == "无常之雾" for effect in battle.field_effects):
            return -8.0
        unmarked = sum(1 for enemy in enemies if not enemy.has_status("侯鸟标记"))
        return 320.0 + unmarked * 50.0 if enemies else -4.0
    if code == "crystal_ball":
        return 62.0 if enemies else -4.0
    if code == "water_wave":
        return 46.0
    if code == "headshot":
        return 60.0 if battle.action_snapshot_for(actor).get("attack_targets") else 18.0
    if code == "six_blade_style":
        return 54.0 if battle.action_snapshot_for(actor).get("attack_targets") else 12.0
    if code == "form_shift":
        return 58.0
    if code == "into_darkness":
        return 52.0
    if code == "stealth":
        return 34.0 if actor.current_hp <= 0.75 else 18.0
    if code == "harden":
        return 28.0 if actor.current_hp <= 0.75 else 12.0
    if code == "mech_enhancement":
        return 38.0
    if code == "n_skill":
        return 26.0 if actor.mana_points >= 1 else -10.0
    if code in {"shensu", "big_shensu"}:
        enemies = [unit for unit in battle.enemy_units(actor.player_id) if unit.alive and unit.position is not None and not unit.banished]
        if not enemies or actor.move_used:
            return -6.0
        nearest = min(distance_between_units(battle, actor, unit) for unit in enemies)
        bonus = 10.0 if code == "big_shensu" else 0.0
        return (30.0 + bonus) if nearest > actor.normal_move_distance() else (8.0 + bonus)
    if code == "nuclear_rush":
        if any(status.name == "核冲" for status in actor.statuses):
            return -8.0
        attack_targets = battle.action_snapshot_for(actor).get("attack_targets") or []
        return 272.0 + len(attack_targets) * 24.0 if enemies else 28.0
    if code == "inner_dimension_sword":
        if any(status.name == "里次元大剑" for status in actor.statuses):
            return -8.0
        attack_targets = battle.action_snapshot_for(actor).get("attack_targets") or []
        return 188.0 + len(attack_targets) * 18.0 if enemies else -4.0
    if code == "kings_insight":
        reactive_enemies = sum(
            1
            for enemy in enemies
            if any(getattr(skill, "timing", None) in {"passive", "reaction"} for skill in enemy.skills)
        )
        return 42.0 + reactive_enemies * 18.0 if enemies else -4.0
    return 10.0


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
        return nearby_allies * 24.0 + nearby_enemies * 10.0
    if code == "great_holy_light":
        nearby_enemies = sum(1 for unit in enemies if distance_between_units(battle, actor, unit) <= 3)
        nearby_allies = sum(1 for unit in allies if distance_between_units(battle, actor, unit) <= 3)
        return nearby_enemies * 16.0 + nearby_allies * 10.0
    if code == "plant_growth":
        return len([unit for unit in targets if unit.player_id != actor.player_id]) * 12.0 + 10.0
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
    battle: Battle,
    actor: Unit,
    destination: Position,
    profile: DifficultyProfile,
) -> float:
    direct_enemies = [
        unit
        for unit in battle.units_at(destination)
        if unit.player_id != actor.player_id and unit.unit_id != actor.unit_id
    ]
    if not direct_enemies:
        return 0.0
    score = 90.0 + profile.aggressive_bonus
    for unit in battle.units_at_cells(square_cells_around_position(battle, destination, radius=2)):
        if unit.unit_id == actor.unit_id:
            continue
        if unit.player_id == actor.player_id:
            score -= friendly_fire_penalty(unit) * 1.1
        else:
            score += min(5.0, unit.current_hp) * 90.0 + hostile_unit_value(unit) * 0.35
    return score


def judgment_stone_score(
    battle: Battle,
    actor: Unit,
    payload: dict[str, Any],
    profile: DifficultyProfile,
) -> float:
    destination = payload_destination(payload)
    if destination is None:
        return -20.0
    enemies = [
        enemy
        for enemy in battle.enemy_units(actor.player_id)
        if enemy.alive and enemy.position is not None and not enemy.banished
    ]
    if not enemies:
        return -20.0
    nearest_enemy = min(distance_to_position(battle, enemy, destination) for enemy in enemies)
    score = 250.0 + profile.aggressive_bonus + max(0.0, 7.0 - nearest_enemy) * 12.0
    has_root_two = any(
        unit.alive
        and getattr(unit, "hero_code", "") == "world_root"
        and getattr(unit, "summoner_id", None) == actor.unit_id
        and int(getattr(unit, "root_number", 0) or 0) == 2
        for unit in battle.all_units()
    )
    if has_root_two:
        score += 35.0 if nearest_enemy <= 6 else 0.0
    return score


def world_seed_score(
    battle: Battle,
    actor: Unit,
    payload: dict[str, Any],
    profile: DifficultyProfile,
) -> float:
    destination = payload_destination(payload)
    if destination is None:
        return -20.0
    enemies = [unit for unit in battle.enemy_units(actor.player_id) if unit.alive and unit.position is not None and not unit.banished]
    nearest_enemy = min((distance_to_position(battle, enemy, destination) for enemy in enemies), default=8)
    center_bonus = max(0.0, 6.0 - abs(destination.x - battle.width / 2.0) - abs(destination.y - battle.height / 2.0))
    return 70.0 + center_bonus * 3.0 + max(0.0, 6.0 - nearest_enemy) * 4.0


def gale_score(
    battle: Battle,
    actor: Unit,
    payload: dict[str, Any],
    profile: DifficultyProfile,
) -> float:
    try:
        skill = actor.get_skill("gale")
        cells = skill.get_target_cells_for_payload(battle, actor, payload)
    except Exception:
        cells = []
    if not cells:
        return -12.0
    score = profile.aggressive_bonus
    for unit in battle.units_at_cells(cells):
        if unit.unit_id == actor.unit_id:
            continue
        if unit.player_id == actor.player_id:
            score -= 55.0 if (unit.is_summon or unit.is_clone) else 18.0
            continue
        if unit.is_summon or unit.is_clone:
            score += 85.0 + hostile_unit_value(unit) * 0.25
        else:
            score += 34.0 + hostile_unit_value(unit) * 0.25
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
    for enemy in enemies:
        occupied = {(cell.x, cell.y) for cell in battle.unit_cells(enemy)}
        if occupied & cell_keys:
            direct_enemy_hits += 1
            score += hostile_unit_value(enemy) * 0.25 + 34.0
        else:
            nearest_to_fire = min((min(abs(cell.x - fire.x) + abs(cell.y - fire.y) for fire in cells) for cell in battle.unit_cells(enemy)), default=99)
            if nearest_to_fire <= max(1, int(enemy.stat("speed"))):
                score += max(0.0, 4.0 - nearest_to_fire) * 10.0
    for ally in allies:
        occupied = {(cell.x, cell.y) for cell in battle.unit_cells(ally)}
        if occupied & cell_keys:
            score -= friendly_fire_penalty(ally) * 0.8
    if direct_enemy_hits == 0 and score < profile.action_threshold:
        return -12.0
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
    enemies = [unit for unit in battle.enemy_units(actor.player_id) if unit.alive and unit.position is not None and not unit.banished]
    if not enemies:
        return 0.0
    nearest = min(distance_to_position(battle, unit, destination) for unit in enemies)
    return max(0.0, 4.0 - nearest) * 8.0


def score_respawn_destination(
    battle: Battle,
    unit: Unit,
    destination: Position,
    role: str,
    profile: DifficultyProfile,
) -> float:
    enemies = [enemy for enemy in battle.enemy_units(unit.player_id) if enemy.alive and enemy.position is not None and not enemy.banished]
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
        _, _, copied_payload, copied_action = context
        return skill_payload_requires_enemy_impact(battle, actor, copied_action, copied_payload)
    if code == "agency_borrowed_skill":
        context = agency_borrowed_payload_context(battle, actor, payload)
        if context is None:
            return False
        _, _, copied_payload, copied_action = context
        return skill_payload_requires_enemy_impact(battle, actor, copied_action, copied_payload)
    if code == "vain_giant_shadow":
        target = battle.units.get(str(payload.get("target_unit_id") or ""))
        return target is not None and target.player_id != actor.player_id
    if code in MOVE_SKILL_CODES or code in SUMMON_SKILL_CODES or code in HEAL_SKILL_CODES:
        return False
    if code == "fuma_trap":
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
    if code == "mimic_skill":
        context = mimic_payload_context(battle, actor, payload)
        if context is None:
            return False
        _, _, copied_payload, copied_action = context
        return skill_payload_has_effective_enemy_impact(battle, actor, copied_action, copied_payload)
    if code == "agency_borrowed_skill":
        context = agency_borrowed_payload_context(battle, actor, payload)
        if context is None:
            return False
        _, _, copied_payload, copied_action = context
        return skill_payload_has_effective_enemy_impact(battle, actor, copied_action, copied_payload)
    try:
        skill = skill_from_ai_action(actor, action, code)
    except Exception:
        return False
    targets = [unit for unit in skill_effect_units(battle, actor, skill, payload) if unit.player_id != actor.player_id]
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
    if code == "heaven_punishment":
        selected_code = str(payload.get("disabled_skill_code") or "")
        return bool(selected_code) and any(
            getattr(target_skill, "timing", None) == "active"
            and target_skill.code == selected_code
            and not any(getattr(status, "skill_code", None) == selected_code for status in target.statuses)
            for target_skill in target.skills
        )
    if code == "purify_mana":
        return target.current_mana > 0 or target.total_shields() > 0
    if code == "interference":
        return target.is_clone or (target.is_summon and target.player_id != actor.player_id)
    if code == "fantasy_move":
        return payload.get("x") is not None and payload.get("y") is not None
    if code == "vain_giant_shadow":
        return not target.cannot_attack and not target.has_status("虚荣巨影")
    if code in {"electric_wind", "snow_avalanche", "sacred_duel", "heaven_lock", "gale"}:
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
        return DamageImpact(damage_ctx.damage, unit_impact_signature(probe_target) != before)


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
    hit_count = max(1, battle.unit_hit_count_for_cells(target, cells) if cells else 1)
    adjusted_attack_power = max(0.0, float(attack_power) - max(0, hit_count - 1))
    resolves_as_attack = str(skill.code) == "whirlwind_attack"
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
            resolve_defenses=False,
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
    random_state = random.getstate()
    battle_state = shallow_object_state(battle)
    units = list(battle.all_units())
    unit_states = [(unit, shallow_object_state(unit)) for unit in units]
    components = []
    for unit in units:
        components.extend(list(unit.iter_components()))
    components.extend(list(getattr(battle, "field_effects", [])))
    component_states = [(component, shallow_object_state(component)) for component in components]
    try:
        yield
    finally:
        for component, state in component_states:
            restore_object_state(component, state)
        for unit, state in unit_states:
            restore_object_state(unit, state)
        restore_object_state(battle, battle_state)
        random.setstate(random_state)


def shallow_object_state(obj: Any) -> dict[str, Any]:
    state: dict[str, Any] = {}
    for key, value in getattr(obj, "__dict__", {}).items():
        if isinstance(value, list):
            state[key] = list(value)
        elif isinstance(value, dict):
            state[key] = dict(value)
        elif isinstance(value, set):
            state[key] = set(value)
        else:
            state[key] = value
    return state


def restore_object_state(obj: Any, state: dict[str, Any]) -> None:
    obj.__dict__.clear()
    obj.__dict__.update(state)


def unit_impact_signature(unit: Unit) -> tuple[Any, ...]:
    return (
        unit.alive,
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
    payload_cells = preview_positions(payload.get("cells"))
    if payload_cells:
        units.extend(battle.units_at_cells(payload_cells))
        return battle.effect_units(units, ignore=None)
    try:
        units.extend(skill.get_target_units_for_payload(battle, actor, payload))
    except Exception:
        pass
    try:
        units.extend(battle.units_at_cells(skill.get_target_cells_for_payload(battle, actor, payload)))
    except Exception:
        pass
    return battle.effect_units(units, ignore=None)


def skill_effect_cells(battle: Battle, actor: Unit, skill: Any, payload: dict[str, Any]) -> list[Position]:
    payload_cells = preview_positions(payload.get("cells"))
    if payload_cells:
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
    if code == "judgment_fire":
        return 6.0
    if code == "great_funeral":
        return 5.0
    if code == "kaiser_fist":
        return actor.stat("attack") + 1
    if code == "illumination_light":
        return 4.0
    if code == "true_blade_air_slash":
        return target.stat("defense") + 1
    if code == "dragon_slash":
        return 5.0
    if code == "lao_wave_bullet":
        return actor.stat("attack") - (1 if payload.get("free_cast") else 0)
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
    return float(battle.damage_rule.calculate_damage(attack_value, target.stat("defense")))


def incoming_threat_score(battle: Battle, target: Unit, queued_action: QueuedAction) -> float:
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
        target_ids = {str(unit_id) for unit_id in preview.get("target_unit_ids", [])}
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
    return any(getattr(skill, "code", "") in {"magnetic_wave", "n_skill"} for skill in unit.skills)


def hostile_unit_value(unit: Unit) -> float:
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
