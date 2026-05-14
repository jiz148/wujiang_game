from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Optional

from wujiang.engine.core import ActionError, Battle, Position, QueuedAction, Unit


AI_DIFFICULTIES = {"easy", "standard", "aggressive"}

SUPPORT_HERO_CODES = {"ellie", "bard", "element_hunter"}
SUMMON_SKILL_CODES = {"medusa", "thunder_god", "earth_walker", "split", "motor_horse"}
HEAL_SKILL_CODES = {"heal", "heal_mount", "mech_enhancement"}
ALLY_BUFF_SKILL_CODES = {"defend_twice", "baptism", "chant", "experiment"}
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
}
MOVE_SKILL_CODES = {"fly_leap", "fate_kick", "crazy_sand", "plasma_thruster", "mounted_leap"}
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
}
REACTION_SHIELD_CODES = {"magic_wall", "light_wall", "stone_wall", "ion_shield", "quantum_shield", "protection"}


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
    best_non_move = best_candidate(candidates)
    best_move = best_candidate(move_candidates)
    if best_non_move is not None and best_non_move.score >= profile.action_threshold:
        return best_non_move.payload
    if best_move is not None and best_move.score >= 0:
        return best_move.payload
    if best_non_move is not None and best_non_move.score > 0:
        return best_non_move.payload
    return {"type": "end_turn"}


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
    role = hero_style(actor)
    candidates: list[AICandidate] = []
    for cell in preview_positions(action.get("preview", {}).get("cells")):
        payload = {"type": "move", "unit_id": actor.unit_id, "x": cell.x, "y": cell.y}
        if not payload_is_legal(battle, payload):
            continue
        score = score_move_destination(battle, actor, cell, role, profile)
        candidates.append(AICandidate(payload=payload, score=score, summary=f"move:{cell.x},{cell.y}"))
    return candidates


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
    candidates: list[AICandidate] = []
    for payload in payloads:
        if not payload_is_legal(battle, payload):
            continue
        score = score_skill_payload(battle, actor, action, payload, profile, instant_only=instant_only)
        candidates.append(AICandidate(payload=payload, score=score, summary=f"skill:{action.get('code')}"))
    return candidates


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
    else:
        for target_id in preview.get("target_unit_ids", []):
            target = battle.get_unit(str(target_id))
            if not target.alive or target.position is None or target.banished:
                continue
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
    base_payload = {"type": "skill", "unit_id": actor.unit_id, "skill_code": code}
    selection = dict(preview.get("selection") or {})
    mode = str(selection.get("mode") or "")
    if code == "rock_absorb":
        return rock_absorb_payloads(battle, actor, action)
    if code == "rock_cannon":
        return rock_cannon_payloads(battle, actor)
    if target_mode in {"none", "self"}:
        return [base_payload]
    if target_mode in {"ally", "enemy", "unit"}:
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
        if mode == "pattern_cells":
            return [
                {
                    "type": "skill",
                    "unit_id": actor.unit_id,
                    "skill_code": code,
                    "cells": positions_to_payload(preview_positions(pattern)),
                }
                for pattern in selection.get("patterns", [])
            ]
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
        payloads = []
        for cell in preview_positions(preview.get("cells")):
            payloads.append({"type": "skill", "unit_id": actor.unit_id, "skill_code": code, "x": cell.x, "y": cell.y})
        return dedupe_payloads(payloads)
    return []


def rock_absorb_payloads(battle: Battle, actor: Unit, action: dict[str, Any]) -> list[dict[str, Any]]:
    skill = actor.get_skill("rock_absorb")
    preview = action.get("preview", {}) or {}
    selection = dict(preview.get("selection") or {})
    required = int(selection.get("required_cells") or 0)
    candidate_cells = preview_positions(preview.get("cells"))
    selected_cells = candidate_cells[:required]
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
    if action_code in {"block", "counter", "protection", "knockback"}:
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
    return [base_payload]


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
    threatened = [battle.get_unit(str(unit_id)) for unit_id in preview.get("target_unit_ids", [])]
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
    target = battle.get_unit(str(payload["target_unit_id"]))
    attack_power = battle.basic_attack_preview_power(actor, payload)
    cells = battle.payload_positions(battle.resolved_basic_attack_payload(actor, payload), "attack_cells")
    hit_count = max(1, battle.unit_hit_count_for_cells(target, cells) if cells else 1)
    ignore_shield = bool(payload.get("ignore_shield"))
    half_ignore_shield = bool(payload.get("half_ignore_shield"))
    expected_damage = estimate_damage(battle, target, attack_power + max(0, hit_count - 1), ignore_shield=ignore_shield, half_ignore_shield=half_ignore_shield)
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
    skill = actor.get_skill(code)
    targets = skill_effect_units(battle, actor, skill, payload)
    role = hero_style(actor)
    if code in MOVE_SKILL_CODES:
        destination = payload_destination(payload)
        if destination is None:
            return -5.0
        score = score_move_destination(battle, actor, destination, role, profile) + 10.0
        if code == "crazy_sand":
            score += skill_damage_score(battle, actor, skill, payload, profile)
        return score
    if code in SUMMON_SKILL_CODES:
        destination = payload_destination(payload)
        score = 42.0
        if destination is not None:
            score += summon_position_score(battle, actor, destination)
        if code in {"earth_walker", "split"}:
            score += 10.0
        return score
    if code in HEAL_SKILL_CODES:
        healed = primary_target_unit(battle, payload, targets)
        if healed is None:
            healed = actor
        missing = max(0.0, healed.max_health - healed.current_hp)
        score = missing * 120.0
        if code == "mech_enhancement":
            score += 24.0
        return score
    if code in ALLY_BUFF_SKILL_CODES:
        target = primary_target_unit(battle, payload, targets)
        if target is None:
            return -2.0
        score = ally_buff_score(battle, actor, code, target, profile)
        return score
    if code in SELF_BUFF_SKILL_CODES:
        return self_buff_score(battle, actor, code, profile)
    if code in {"stance", "great_holy_light", "plant_growth"}:
        return field_skill_score(battle, actor, code, targets, profile)
    if code == "drain_mana":
        return drain_mana_score(battle, actor, targets, profile)
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
    if code == "knockback":
        return threat * 0.8 + 18.0
    return 0.0


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
        attack_power = skill_attack_power(battle, actor, skill, payload, unit, cells)
        ignore_shield = bool(skill.ignores_shield_for_payload(battle, actor, payload))
        half_ignore_shield = bool(skill.half_ignores_shield_for_payload(battle, actor, payload))
        damage = estimate_damage(
            battle,
            unit,
            attack_power,
            ignore_shield=ignore_shield,
            half_ignore_shield=half_ignore_shield,
        )
        score += damage * 100.0
        score += hostile_unit_value(unit) * 0.5
        if damage >= unit.current_hp - 1e-9:
            score += 90.0
    if code in {"judgment_fire", "great_funeral", "laser", "missile", "machine_gun", "pierce", "remote_dragon_breath", "dragon_breath", "magnetic_wave"}:
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
    if code == "drain_mana":
        return sum(min(unit.current_mana, 1.0) * 45.0 for unit in enemies)
    if code == "mana_pull":
        return 20.0 if enemies else 6.0
    if code == "stance":
        return field_skill_score(battle, actor, code, targets, profile)
    if code == "plant_growth":
        return field_skill_score(battle, actor, code, targets, profile)
    return 0.0


def drain_mana_score(battle: Battle, actor: Unit, targets: list[Unit], profile: DifficultyProfile) -> float:
    enemies = [unit for unit in targets if unit.player_id != actor.player_id]
    if not enemies:
        return -4.0
    return sum(min(unit.current_mana, 1.0) * 55.0 + hostile_unit_value(unit) * 0.2 for unit in enemies)


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
    return 12.0


def self_buff_score(battle: Battle, actor: Unit, code: str, profile: DifficultyProfile) -> float:
    enemies = [unit for unit in battle.enemy_units(actor.player_id) if unit.alive and unit.position is not None and not unit.banished]
    if code == "crystal_ball":
        return 62.0 if enemies else -4.0
    if code == "water_wave":
        return 46.0
    if code == "headshot":
        return 60.0 if battle.action_snapshot_for(actor).get("attack_targets") else 18.0
    if code == "six_blade_style":
        return 54.0 if battle.action_snapshot_for(actor).get("attack_targets") else 12.0
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
    if code == "shensu":
        enemies = [unit for unit in battle.enemy_units(actor.player_id) if unit.alive and unit.position is not None and not unit.banished]
        if not enemies or actor.move_used:
            return -6.0
        nearest = min(distance_between_units(battle, actor, unit) for unit in enemies)
        return 30.0 if nearest > actor.normal_move_distance() else 8.0
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
    return 6.0


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
    if payload.get("action_code") in {"block", "counter"}:
        return True
    try:
        skill = reactor.get_skill(str(payload["action_code"]))
        stripped = {key: value for key, value in payload.items() if key not in {"type", "unit_id", "action_code"}}
        ok, _ = skill.can_react_with_payload(battle, reactor, queued_action, stripped)
        return ok
    except Exception:
        return False


def skill_effect_units(battle: Battle, actor: Unit, skill: Any, payload: dict[str, Any]) -> list[Unit]:
    units: list[Unit] = []
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
