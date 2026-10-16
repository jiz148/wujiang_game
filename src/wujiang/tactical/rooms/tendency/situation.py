"""Cheap description of a moment. No path search."""
from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

from wujiang.tactical.engine.core import Battle, Unit


@dataclass(frozen=True)
class Situation:
    hero_code: str
    hp_ratio: float
    mana: float
    attack: float
    attack_range: int
    enemies_alive: int
    allies_alive: int
    nearest_enemy_distance: int | None
    weakest_enemy_hp_ratio: float | None
    lowest_ally_hp_ratio: float | None
    outnumbered: bool
    stealthed: bool
    can_attack_enemy: bool
    enemy_skill_codes: tuple[str, ...]
    ally_skill_codes: tuple[str, ...]
    self_skill_codes: tuple[str, ...]

    def as_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["enemy_skill_codes"] = list(self.enemy_skill_codes)
        payload["ally_skill_codes"] = list(self.ally_skill_codes)
        payload["self_skill_codes"] = list(self.self_skill_codes)
        return payload


def _ratio(current: float, maximum: float) -> float:
    return float(current) / max(float(maximum), 0.001)


def build_situation(battle: Battle, actor: Unit, snapshot: dict[str, Any]) -> Situation:
    enemies = [
        unit
        for unit in battle.units.values()
        if unit.alive and not unit.banished and unit.position is not None and unit.player_id != actor.player_id
    ]
    allies = [
        unit
        for unit in battle.units.values()
        if unit.alive and not unit.banished and unit.position is not None and unit.player_id == actor.player_id
    ]
    nearest = None
    weakest = None
    if actor.position is not None and enemies:
        nearest = min(actor.position.distance_to(unit.position) for unit in enemies if unit.position is not None)
        weakest = min(_ratio(unit.current_hp, unit.max_health) for unit in enemies)
    lowest_ally = min(_ratio(unit.current_hp, unit.max_health) for unit in allies) if allies else None
    enemy_skills: list[str] = []
    ally_skills: list[str] = []
    self_skills: list[str] = []
    can_attack = False
    for action in snapshot.get("actions", []):
        if not action.get("available"):
            continue
        kind = str(action.get("kind") or "")
        if kind == "attack":
            ids = action.get("preview", {}).get("target_unit_ids") or []
            if any(_is_enemy(battle, actor, item) for item in ids):
                can_attack = True
            continue
        if kind != "skill" or str(action.get("timing") or "") != "active":
            continue
        code = str(action.get("code") or "")
        mode = str(action.get("target_mode") or "none")
        if mode in {"enemy", "unit"}:
            enemy_skills.append(code)
        elif mode == "ally":
            ally_skills.append(code)
        elif mode in {"none", "self"}:
            self_skills.append(code)
        elif mode == "cell":
            enemy_skills.append(code)
    return Situation(
        hero_code=str(getattr(actor, "hero_code", "") or ""),
        hp_ratio=_ratio(actor.current_hp, actor.max_health),
        mana=float(getattr(actor, "current_mana", 0) or 0),
        attack=float(actor.stat("attack")),
        attack_range=max(1, int(actor.targeting_range())),
        enemies_alive=len(enemies),
        allies_alive=len(allies),
        nearest_enemy_distance=None if nearest is None else int(nearest),
        weakest_enemy_hp_ratio=weakest,
        lowest_ally_hp_ratio=lowest_ally,
        outnumbered=len(enemies) > len(allies),
        stealthed=any(getattr(status, "name", "") == "隐身" for status in getattr(actor, "statuses", []) or []),
        can_attack_enemy=can_attack,
        enemy_skill_codes=tuple(enemy_skills),
        ally_skill_codes=tuple(ally_skills),
        self_skill_codes=tuple(self_skills),
    )


def _is_enemy(battle: Battle, actor: Unit, raw: Any) -> bool:
    unit = battle.units.get(str(raw))
    return bool(unit and unit.alive and not unit.banished and unit.player_id != actor.player_id)
