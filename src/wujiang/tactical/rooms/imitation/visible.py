"""Encode the board an acting hero can see, plus one legal action.

Enemy stealth is omitted. Allies stay, including their own stealth. Traits,
statuses, skill cards, and the mana cost of the candidate are part of the
vector. Training records and the live battle both pass through these functions.
"""
from __future__ import annotations

from typing import Any

import numpy as np

HASH = 16
ACTOR_SCALARS = 15
ACTOR_DIM = HASH * 4 + ACTOR_SCALARS
ENEMY_SLOTS = 4
ENEMY_WIDTH = 6
ALLY_SLOTS = 3
ALLY_WIDTH = 4
SUMMARY_DIM = 6
SITUATION_DIM = ACTOR_DIM + ENEMY_SLOTS * ENEMY_WIDTH + ALLY_SLOTS * ALLY_WIDTH + SUMMARY_DIM
CANDIDATE_DIM = 4 + HASH + 4
INPUT_DIM = SITUATION_DIM + CANDIDATE_DIM


def visible_situation(
    battle: dict[str, Any],
    actor: dict[str, Any],
    *,
    round_number: int | None = None,
) -> dict[str, Any]:
    units = [
        unit for unit in (battle.get("units") or [])
        if isinstance(unit, dict) and unit.get("alive") and not unit.get("banished") and isinstance(unit.get("position"), dict)
    ]
    actor_id = str(actor.get("id") or "")
    player = actor.get("player_id")
    position = actor.get("position") if isinstance(actor.get("position"), dict) else {}
    enemies: list[dict[str, Any]] = []
    allies: list[dict[str, Any]] = []
    for unit in units:
        if str(unit.get("id") or "") == actor_id:
            continue
        if unit.get("player_id") != player and _named(unit.get("statuses"), "隐身"):
            continue
        entry = _unit_brief(position, unit)
        if unit.get("player_id") == player:
            allies.append(entry)
        else:
            enemies.append(entry)
    enemies.sort(key=lambda item: item["distance"])
    allies.sort(key=lambda item: item["distance"])
    skills = _skill_briefs(actor)
    ready = [skill["code"] for skill in skills if skill["ready"]]
    attack_range = float(((actor.get("stats") or {}).get("attack_range") or 0))
    nearest = enemies[0]["distance"] if enemies else None
    attacks_per = float(actor.get("attacks_per_turn") or 0)
    attacks_used = float(actor.get("attacks_used") or 0)
    hero_code = str(actor.get("hero_code") or "")
    hero_name = str(actor.get("name") or "")
    return {
        "round": int(round_number if round_number is not None else battle.get("round_number") or 1),
        "ready_skills": ready,
        "enemy_count": len(enemies),
        "ally_count": len(allies),
        "nearest_enemy": nearest,
        "enemy_in_attack_range": bool(nearest is not None and nearest <= attack_range),
        "weakest_enemy_hp": min((item["hp_ratio"] for item in enemies), default=0.0),
        "actor": {
            "hero_code": hero_code or hero_name,
            "name": hero_name,
            "hp_ratio": round(_ratio(actor.get("hp"), actor.get("max_hp")), 3),
            "mana_ratio": round(_ratio(actor.get("mana"), actor.get("max_mana")), 3),
            "attack": float(((actor.get("stats") or {}).get("attack") or 0)),
            "defense": float(((actor.get("stats") or {}).get("defense") or 0)),
            "speed": float(((actor.get("stats") or {}).get("speed") or 0)),
            "attack_range": attack_range,
            "attacks_left": max(0.0, attacks_per - attacks_used),
            "shields": float(actor.get("total_shields") or actor.get("shields") or 0),
            "move_mana_per_cell": _move_mana_per_cell(actor),
            "cannot_move": bool(actor.get("cannot_move") or actor.get("cannot_normal_move")),
            "cannot_attack": bool(actor.get("cannot_attack")),
            "cannot_use_skills": bool(actor.get("cannot_use_skills")),
            "performed_active_skill": bool(actor.get("performed_active_skill")),
            "stealthed": _named(actor.get("statuses"), "隐身"),
            "trait_names": _names(actor.get("traits")),
            "status_names": _names(actor.get("statuses")),
            "ready_skills": ready,
            "skill_count": len(skills),
        },
        "enemies": enemies,
        "allies": allies,
    }


def candidate_from_action(action: dict[str, Any], situation: dict[str, Any], verb: str) -> dict[str, Any]:
    preview = action.get("preview") if isinstance(action.get("preview"), dict) else {}
    kind = str(action.get("kind") or "")
    if kind == "skill":
        mana_cost = float(action.get("mana_cost") or 0.0)
    elif kind == "move":
        mana_cost = float((situation.get("actor") or {}).get("move_mana_per_cell") or 0.0)
    else:
        mana_cost = 0.0
    return {
        "verb": verb,
        "mana_cost": mana_cost,
        "enemy_targets": len(preview.get("target_unit_ids") or []),
        "cells": len(preview.get("cells") or []),
    }


def merge_candidates(actions: list[dict[str, Any]], situation: dict[str, Any], verb_of) -> list[dict[str, Any]]:
    merged: dict[str, dict[str, Any]] = {}
    order: list[str] = []
    for action in actions:
        if not isinstance(action, dict) or not action.get("available", True):
            continue
        verb = str(verb_of(action) or "")
        if not verb:
            continue
        item = candidate_from_action(action, situation, verb)
        current = merged.get(verb)
        if current is None:
            order.append(verb)
            merged[verb] = item
            continue
        current["enemy_targets"] = max(int(current["enemy_targets"]), int(item["enemy_targets"]))
        current["cells"] = max(int(current["cells"]), int(item["cells"]))
        current["mana_cost"] = max(float(current["mana_cost"]), float(item["mana_cost"]))
    return [merged[verb] for verb in order]


def situation_features(situation: dict[str, Any]) -> np.ndarray:
    actor = situation.get("actor") if isinstance(situation.get("actor"), dict) else {}
    ready = actor.get("ready_skills") or situation.get("ready_skills") or []
    actor_vector = np.concatenate([
        _hash(str(actor.get("hero_code") or "")),
        _hash("|".join(sorted(str(name) for name in (actor.get("trait_names") or [])))),
        _hash("|".join(sorted(str(name) for name in (actor.get("status_names") or [])))),
        _hash("|".join(sorted(str(code) for code in ready))),
        np.asarray([
            float(actor.get("hp_ratio") or 0.0),
            float(actor.get("mana_ratio") or 0.0),
            float(actor.get("attack") or 0.0) / 10.0,
            float(actor.get("defense") or 0.0) / 10.0,
            float(actor.get("speed") or 0.0) / 10.0,
            float(actor.get("attack_range") or 0.0) / 10.0,
            min(float(actor.get("attacks_left") or 0.0), 4.0) / 4.0,
            min(float(actor.get("shields") or 0.0), 5.0) / 5.0,
            1.0 if float(actor.get("move_mana_per_cell") or 0.0) > 0 else 0.0,
            1.0 if actor.get("cannot_move") else 0.0,
            1.0 if actor.get("cannot_attack") else 0.0,
            1.0 if actor.get("cannot_use_skills") else 0.0,
            1.0 if actor.get("performed_active_skill") else 0.0,
            1.0 if actor.get("stealthed") else 0.0,
            min(len(ready), 6) / 6.0,
        ], dtype=np.float32),
    ])
    enemies = _slots(situation.get("enemies"), ENEMY_SLOTS, ENEMY_WIDTH, _enemy_slot)
    allies = _slots(situation.get("allies"), ALLY_SLOTS, ALLY_WIDTH, _ally_slot)
    nearest = situation.get("nearest_enemy")
    summary = np.asarray([
        min(float(situation.get("enemy_count") or 0.0), 8.0) / 8.0,
        min(float(situation.get("ally_count") or 0.0), 8.0) / 8.0,
        1.0 if nearest is None else min(float(nearest), 20.0) / 20.0,
        1.0 if situation.get("enemy_in_attack_range") else 0.0,
        float(situation.get("weakest_enemy_hp") or 0.0),
        min(float(situation.get("round") or 1.0), 30.0) / 30.0,
    ], dtype=np.float32)
    return np.concatenate([actor_vector, enemies, allies, summary]).astype(np.float32)


def candidate_features(situation: dict[str, Any], candidate: dict[str, Any]) -> np.ndarray:
    verb = str(candidate.get("verb") or "")
    kind = np.zeros(4, dtype=np.float32)
    kind[_kind_index(verb)] = 1.0
    paid_move = 1.0 if verb == "move" and float((situation.get("actor") or {}).get("move_mana_per_cell") or 0.0) > 0 else 0.0
    extra = np.asarray([
        min(float(candidate.get("mana_cost") or 0.0), 5.0) / 5.0,
        min(float(candidate.get("enemy_targets") or 0.0), 4.0) / 4.0,
        min(float(candidate.get("cells") or 0.0), 16.0) / 16.0,
        paid_move,
    ], dtype=np.float32)
    return np.concatenate([kind, _hash(verb), extra])


def pair_features(situation: dict[str, Any], candidate: dict[str, Any]) -> np.ndarray:
    return np.concatenate([situation_features(situation), candidate_features(situation, candidate)])


def _unit_brief(origin: dict[str, Any], unit: dict[str, Any]) -> dict[str, Any]:
    position = unit.get("position") if isinstance(unit.get("position"), dict) else {}
    stats = unit.get("stats") or {}
    return {
        "distance": _distance(origin, position) if origin and position else 20,
        "hp_ratio": round(_ratio(unit.get("hp"), unit.get("max_hp")), 3),
        "mana_ratio": round(_ratio(unit.get("mana"), unit.get("max_mana")), 3),
        "attack": float(stats.get("attack") or 0.0),
        "defense": float(stats.get("defense") or 0.0),
        "attack_range": float(stats.get("attack_range") or 0.0),
    }


def _enemy_slot(item: dict[str, Any]) -> list[float]:
    return [
        min(float(item.get("distance") or 0.0), 20.0) / 20.0,
        float(item.get("hp_ratio") or 0.0),
        float(item.get("mana_ratio") or 0.0),
        float(item.get("attack") or 0.0) / 10.0,
        float(item.get("defense") or 0.0) / 10.0,
        float(item.get("attack_range") or 0.0) / 10.0,
    ]


def _ally_slot(item: dict[str, Any]) -> list[float]:
    return [
        min(float(item.get("distance") or 0.0), 20.0) / 20.0,
        float(item.get("hp_ratio") or 0.0),
        float(item.get("attack") or 0.0) / 10.0,
        float(item.get("defense") or 0.0) / 10.0,
    ]


def _slots(raw: Any, count: int, width: int, encode) -> np.ndarray:
    rows = np.zeros((count, width), dtype=np.float32)
    items = [item for item in (raw or []) if isinstance(item, dict)]
    for index, item in enumerate(items[:count]):
        values = encode(item)
        rows[index, :len(values)] = np.asarray(values, dtype=np.float32)
    return rows.reshape(-1)


def _skill_briefs(actor: dict[str, Any]) -> list[dict[str, Any]]:
    mana = float(actor.get("mana") or 0.0)
    briefs: list[dict[str, Any]] = []
    for skill in actor.get("skills") or []:
        if not isinstance(skill, dict) or skill.get("passive"):
            continue
        if str(skill.get("timing") or "") not in {"active", "instant"}:
            continue
        code = str(skill.get("code") or "")
        ready = int(skill.get("cooldown_remaining") or 0) <= 0 and mana + 0.001 >= float(skill.get("mana_cost") or 0.0)
        briefs.append({"code": code, "ready": ready})
    return briefs


def _move_mana_per_cell(actor: dict[str, Any]) -> float:
    for trait in actor.get("traits") or []:
        if not isinstance(trait, dict):
            continue
        name = str(trait.get("name") or "")
        description = str(trait.get("description") or "")
        if name == "以魔代步" or "每格消耗" in description:
            return 1.0
    return 0.0


def _names(raw: Any) -> list[str]:
    names: list[str] = []
    for item in raw or []:
        if isinstance(item, dict) and item.get("name"):
            names.append(str(item["name"]))
        elif isinstance(item, str) and item:
            names.append(item)
    return names


def _named(raw: Any, expected: str) -> bool:
    return expected in _names(raw)


def _hash(text: str) -> np.ndarray:
    vector = np.zeros(HASH, dtype=np.float32)
    for index, character in enumerate(text):
        vector[(ord(character) + index * 17) % HASH] += 1.0
    norm = float(np.linalg.norm(vector))
    if norm > 0:
        vector /= norm
    return vector


def _kind_index(verb: str) -> int:
    if verb == "move":
        return 0
    if verb == "attack":
        return 1
    if verb.startswith("skill:"):
        return 2
    return 3


def _ratio(current: Any, maximum: Any) -> float:
    return float(current or 0) / max(float(maximum or 0), 0.001)


def _distance(left: dict[str, Any], right: dict[str, Any]) -> int:
    return max(abs(int(left.get("x") or 0) - int(right.get("x") or 0)), abs(int(left.get("y") or 0) - int(right.get("y") or 0)))
