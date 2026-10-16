"""Rank the unit's legal actions with a saved network, then take one of them.

The network scores tokens such as move, attack, and skill:pierce. It does not
invent a cell. The concrete step still comes from that one legal entry, and an
area skill that hits nobody is refused. Weights stay under data/nn-lab. If the
ranker file is missing, the older four-way kind picker is used.
"""
from __future__ import annotations

from typing import Any, Iterable, Optional

from wujiang.tactical.engine.core import Battle, Unit
from wujiang.tactical.rooms.imitation.dataset import LABELS, situation_vector
from wujiang.tactical.rooms.imitation.decisions import action_verb
from wujiang.tactical.rooms.imitation.ranker import LegalRanker
from wujiang.tactical.rooms.imitation.hero_models import hero_weight_path, uses_soldier_logic
from wujiang.tactical.rooms.imitation.service import lab_weights_path, load_picker
from wujiang.tactical.rooms.imitation.visible import merge_candidates, visible_situation
from wujiang.tactical.rooms.tendency.decide import (
    _attack,
    _first_skill,
    _move,
    _skill_payload,
)

_RANKER: tuple[str, float, LegalRanker] | None = None


def _load_ranker(actor: Unit) -> Optional[LegalRanker]:
    global _RANKER
    path = None
    seen: set[str] = set()
    for raw in (getattr(actor, "hero_code", ""), getattr(actor, "name", "")):
        identity = str(raw or "").strip()
        if not identity or identity in seen or uses_soldier_logic(identity):
            continue
        seen.add(identity)
        candidate = hero_weight_path(identity)
        if candidate.is_file():
            path = candidate
            break
    if path is None:
        return None
    try:
        modified = path.stat().st_mtime
    except OSError:
        return None
    key = str(path)
    if _RANKER is not None and _RANKER[0] == key and _RANKER[1] == modified:
        return _RANKER[2]
    try:
        model = LegalRanker.load(path)
    except (OSError, ValueError, KeyError):
        return None
    _RANKER = (key, modified, model)
    return model


def _is_plain_soldier(unit: Unit) -> bool:
    code = str(getattr(unit, "hero_code", "") or "")
    name = str(getattr(unit, "name", "") or "")
    return uses_soldier_logic(code) or uses_soldier_logic(name)


__all__ = [
    "choose_chain_reaction",
    "choose_damage_choice_action",
    "choose_instant_action",
    "choose_respawn_action",
    "choose_turn_bundle_action",
]


def choose_turn_bundle_action(
    battle: Battle,
    units: Iterable[Unit],
    difficulty: str,
) -> tuple[dict[str, Any], Optional[Unit]]:
    ready = [unit for unit in units if unit.can_take_turn_actions(battle)]
    if not ready:
        return {"type": "end_turn"}, None
    slot = battle.current_turn_slot_unit_id()
    actor = next((unit for unit in ready if unit.unit_id == slot), ready[0])
    if _is_plain_soldier(actor):
        from wujiang.tactical.rooms import ai as detailed
        return detailed.choose_turn_bundle_action(battle, units, difficulty)
    snapshot = battle.action_snapshot_for(actor)
    payload = _payload_from_ranker(battle, actor, snapshot)
    if payload is None:
        from wujiang.tactical.rooms import ai as detailed
        return detailed.choose_turn_bundle_action(battle, units, difficulty)
    if payload.get("type") == "end_turn" and any(unit.unit_id != actor.unit_id for unit in ready):
        payload = {"type": "pass_unit", "unit_id": actor.unit_id}
    return payload, actor



def choose_instant_action(battle: Battle, units: Iterable[Unit], difficulty: str) -> Optional[dict[str, Any]]:
    from wujiang.tactical.rooms import ai as detailed
    return detailed.choose_instant_action(battle, units, difficulty)


def choose_chain_reaction(battle: Battle, reactor: Unit, options: list[dict[str, Any]], difficulty: str):
    from wujiang.tactical.rooms import ai as detailed
    return detailed.choose_chain_reaction(battle, reactor, options, difficulty)


def choose_respawn_action(battle: Battle, unit: Unit, options: list, difficulty: str):
    from wujiang.tactical.rooms import ai as detailed
    return detailed.choose_respawn_action(battle, unit, options, difficulty)


def choose_damage_choice_action(battle: Battle) -> dict[str, Any]:
    from wujiang.tactical.rooms import ai as detailed
    return detailed.choose_damage_choice_action(battle)


def _payload_from_ranker(battle: Battle, actor: Unit, snapshot: dict[str, Any]) -> Optional[dict[str, Any]]:
    ranker = _load_ranker(actor)
    if ranker is None:
        return None
    actions = [action for action in (snapshot.get("actions") or []) if isinstance(action, dict) and action.get("available")]
    if not actions:
        return None
    board = _spectator(battle, actor)
    recorded = next((unit for unit in board["units"] if unit.get("id") == actor.unit_id), None)
    if recorded is None:
        return None
    situation = visible_situation(board, recorded)
    candidates = merge_candidates(actions, situation, action_verb)
    if not candidates:
        return None
    scores = ranker.score_candidates(situation, candidates)
    order = sorted(range(len(candidates)), key=lambda index: (-float(scores[index]), str(candidates[index].get("verb") or "")))
    for index in order:
        verb = str(candidates[index].get("verb") or "")
        for action in actions:
            if action_verb(action) != verb:
                continue
            payload = _payload_for_action(battle, actor, snapshot, action)
            if payload is not None:
                return payload
    return None


def _payload_for_action(
    battle: Battle,
    actor: Unit,
    snapshot: dict[str, Any],
    action: dict[str, Any],
) -> Optional[dict[str, Any]]:
    kind = str(action.get("kind") or "")
    if kind == "move":
        return _move(battle, actor, snapshot, away=False)
    if kind == "attack":
        return _attack(battle, actor, snapshot)
    if kind == "skill":
        return _skill_payload(battle, actor, action)
    return None


def _predicted_kind(battle: Battle, actor: Unit) -> str:
    path = lab_weights_path()
    if not path.is_file():
        return "attack"
    features = situation_vector(_spectator(battle, actor), actor.unit_id)
    if features is None:
        return "end"
    try:
        picker = load_picker(path)
    except (OSError, ValueError, KeyError):
        return "attack"
    import numpy as np
    label_index = int(picker.predict(np.asarray([features], dtype=np.float32))[0])
    if label_index < 0 or label_index >= len(LABELS):
        return "end"
    return LABELS[label_index]


def _spectator(battle: Battle, viewer: Unit) -> dict[str, Any]:
    units = []
    for unit in battle.all_units():
        if not unit.alive or unit.banished or unit.position is None:
            continue
        if unit.player_id != viewer.player_id and unit.has_status("隐身"):
            continue
        units.append({
            "id": unit.unit_id,
            "alive": True,
            "banished": False,
            "player_id": unit.player_id,
            "hero_code": str(getattr(unit, "hero_code", "") or ""),
            "name": unit.name,
            "position": {"x": unit.position.x, "y": unit.position.y},
            "hp": unit.current_hp,
            "max_hp": unit.max_health,
            "mana": unit.current_mana,
            "max_mana": unit.max_mana(),
            "stats": {
                "attack": unit.stat("attack"),
                "defense": unit.stat("defense"),
                "speed": unit.stat("speed"),
                "attack_range": unit.targeting_range(),
            },
            "statuses": [{"name": getattr(status, "name", "")} for status in (unit.statuses or [])],
            "skills": [skill.to_public_dict(battle) for skill in unit.action_skills()],
            "traits": [trait.to_public_dict(battle) for trait in [*unit.traits, *unit.projected_traits()]],
            "attacks_per_turn": unit.attack_actions_per_turn(),
            "attacks_used": unit.attacks_used,
            "shields": unit.shields,
            "total_shields": unit.total_shields(),
            "performed_active_skill": unit.performed_active_skill,
            "cannot_move": unit.cannot_move,
            "cannot_normal_move": unit.cannot_normal_move,
            "cannot_attack": unit.cannot_attack,
            "cannot_use_skills": unit.cannot_use_skills,
        })
    return {"units": units, "round_number": getattr(battle, "round_number", 1)}


def _payload_for_kind(battle: Battle, actor: Unit, snapshot: dict[str, Any], kind: str) -> Optional[dict[str, Any]]:
    order = {
        "move": ("move", "attack", "skill"),
        "attack": ("attack", "skill", "move"),
        "skill": ("skill", "attack", "move"),
        "end": (),
    }.get(kind, ("attack", "skill", "move"))
    for name in order:
        payload = _one(battle, actor, snapshot, name)
        if payload is not None:
            return payload
    return None


def _one(battle: Battle, actor: Unit, snapshot: dict[str, Any], kind: str) -> Optional[dict[str, Any]]:
    if kind == "attack":
        return _attack(battle, actor, snapshot)
    if kind == "move":
        return _move(battle, actor, snapshot, away=False)
    if kind == "skill":
        return _first_skill(battle, actor, snapshot, (), timing="active")
    return None
