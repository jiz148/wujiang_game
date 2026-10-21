"""Skill-random rooms: each side spends a point budget on one custom hero.

Implemented heroes supply the skill and trait parts. A part is copied onto a
blank body, so the existing full scorer can play the result.
"""
from __future__ import annotations

import copy
import random
from typing import Any, Callable

from wujiang.tactical.engine.core import Skill, Stats, Trait
from wujiang.tactical.heroes.base import AbstractHero
from wujiang.tactical.heroes.excel_roster import IMPLEMENTED_EXCEL_HERO_CODES
from wujiang.tactical.heroes.registry import HERO_REGISTRY, create_hero

STAT_KEYS = ("attack", "defense", "speed", "attack_range", "mana")
STAT_LABELS = {"attack": "攻", "defense": "守", "speed": "速", "attack_range": "范", "mana": "魔"}
DEFAULT_STAT = 3
DEFAULT_STAT_CAP = 6
DEFAULT_POINT_CAP = 15
STAT_POINT_COST = 3


class ForgeError(ValueError):
    pass


def blank_build() -> dict[str, Any]:
    build = {
        "name": "未命名",
        "race": "人类",
        "attribute": "土",
        "role": "",
        "intro": "",
        "skills": [],
        "traits": [],
    }
    for key in STAT_KEYS:
        build[key] = DEFAULT_STAT
    return build


def _clone_component(component: Skill | Trait) -> Skill | Trait:
    fresh = object.__new__(type(component))
    for key, value in component.__dict__.items():
        if key == "owner":
            continue
        try:
            fresh.__dict__[key] = copy.deepcopy(value)
        except Exception:
            fresh.__dict__[key] = value
    fresh.owner = None
    if isinstance(fresh, Skill):
        fresh.uses_this_turn = 0
        fresh.uses_this_battle = 0
        fresh.cooldown_remaining = 0
    return fresh


def _part_text(component: Skill | Trait, *, skill: bool) -> str:
    text = str(getattr(component, "description", "") or "").strip()
    if text:
        return text
    if not skill:
        return ""
    parts: list[str] = []
    timing = str(getattr(component, "timing", "") or "")
    if timing == "passive" or getattr(component, "passive", False):
        parts.append("被动")
    elif timing == "instant":
        parts.append("即时")
    elif timing == "reaction":
        parts.append("连锁")
    try:
        mana_value = float(getattr(component, "mana_cost", 0) or 0)
    except (TypeError, ValueError):
        mana_value = 0
    if mana_value:
        shown = int(mana_value) if mana_value == int(mana_value) else mana_value
        parts.append(f"{shown}魔")
    per_turn = getattr(component, "max_uses_per_turn", None)
    if per_turn:
        parts.append(f"每回合{int(per_turn)}次")
    per_battle = getattr(component, "max_uses_per_battle", None)
    if per_battle == 1:
        parts.append("一场一次")
    elif per_battle:
        parts.append(f"一场{int(per_battle)}次")
    return " · ".join(parts)


def _skill_search(skill: Skill, ultimate: bool) -> str:
    timing = str(getattr(skill, "timing", "") or "")
    passive = bool(getattr(skill, "passive", False)) or timing == "passive"
    if passive:
        words = ["被动", "被动技能"]
    elif timing == "instant":
        words = ["即时", "即时技能", "主动", "主动技能"]
    elif timing == "reaction":
        words = ["连锁", "连锁技能", "反应"]
    else:
        words = ["主动", "主动技能"]
    try:
        mana = float(getattr(skill, "mana_cost", 0) or 0)
    except (TypeError, ValueError):
        mana = 0
    words.extend(["费魔", "耗魔"] if mana > 0 else ["不费魔", "免费"])
    if ultimate:
        words.append("大招")
    return " ".join(words)


def _catalog() -> dict[str, Any]:
    cached = getattr(_catalog, "value", None)
    if cached is not None:
        return cached
    named = [code for code in HERO_REGISTRY if not str(code).startswith(("excel_", "strategy_"))]
    codes = named + sorted(IMPLEMENTED_EXCEL_HERO_CODES)
    skills: dict[str, dict[str, Any]] = {}
    traits: dict[str, dict[str, Any]] = {}
    races: set[str] = set()
    attributes: set[str] = set()
    roles: set[str] = set()
    heroes: list[dict[str, Any]] = []
    for code in codes:
        try:
            hero = create_hero(code, 1)
        except Exception:
            continue
        hero_name = str(getattr(hero, "name", "") or code)
        heroes.append({
            "code": code,
            "name": hero_name,
            "level": int(getattr(hero, "level", 1) or 1),
            "attribute": str(getattr(hero, "attribute", "") or ""),
            "race": str(getattr(hero, "race", "") or ""),
            "role": str(getattr(hero, "role", "") or ""),
            "stats": {
                "attack": int(getattr(getattr(hero, "base_stats", None), "attack", 0) or 0),
                "defense": int(getattr(getattr(hero, "base_stats", None), "defense", 0) or 0),
                "speed": int(getattr(getattr(hero, "base_stats", None), "speed", 0) or 0),
                "attack_range": int(getattr(getattr(hero, "base_stats", None), "attack_range", 0) or 0),
                "mana": int(getattr(getattr(hero, "base_stats", None), "mana", 0) or 0),
            },
            "skills": [
                {"name": str(skill.name), "description": _part_text(skill, skill=True)}
                for skill in hero.skills
            ],
            "traits": [
                {"name": str(trait.name), "description": _part_text(trait, skill=False)}
                for trait in hero.traits
            ],
        })
        if getattr(hero, "race", ""):
            races.add(str(hero.race))
        if getattr(hero, "attribute", ""):
            attributes.add(str(hero.attribute))
        if getattr(hero, "role", ""):
            roles.add(str(hero.role))
        for skill in hero.skills:
            entry = skills.get(skill.code)
            ultimate = int(getattr(skill, "max_uses_per_battle", 0) or 0) == 1
            if entry is None:
                skills[skill.code] = {
                    "id": f"s:{skill.code}",
                    "kind": "skill",
                    "code": skill.code,
                    "name": skill.name,
                    "heroes": [hero_name],
                    "ultimate": ultimate,
                    "cost": 3 if ultimate else 2,
                    "source": code,
                    "description": _part_text(skill, skill=True),
                    "search": _skill_search(skill, ultimate),
                }
            elif hero_name not in entry["heroes"]:
                entry["heroes"].append(hero_name)
        for index, trait in enumerate(hero.traits):
            trait_id = f"t:{code}:{index}"
            traits[trait_id] = {
                "id": trait_id,
                "kind": "trait",
                "name": trait.name,
                "heroes": [hero_name],
                "ultimate": False,
                "cost": 2,
                "source": code,
                "index": index,
                "description": _part_text(trait, skill=False),
            }
    factories: dict[str, Callable[[], Skill | Trait]] = {}
    for entry in skills.values():
        source = entry["source"]
        skill_code = entry["code"]

        def make_skill(source: str = source, skill_code: str = skill_code) -> Skill:
            donor = create_hero(source, 1)
            found = next(skill for skill in donor.skills if skill.code == skill_code)
            return _clone_component(found)

        factories[entry["id"]] = make_skill
    for entry in traits.values():
        source = entry["source"]
        index = entry["index"]

        def make_trait(source: str = source, index: int = index) -> Trait:
            donor = create_hero(source, 1)
            return _clone_component(donor.traits[index])

        factories[entry["id"]] = make_trait
    payload = {
        "skills": sorted(skills.values(), key=lambda item: (item["ultimate"], item["name"])),
        "traits": sorted(traits.values(), key=lambda item: item["name"]),
        "races": sorted(races),
        "attributes": sorted(attributes),
        "roles": sorted(roles),
        "heroes": heroes,
        "factories": factories,
    }
    _catalog.value = payload
    return payload


def _score_reachable(total: int, costs: set[int]) -> bool:
    if total == 0:
        return True
    usable = [cost for cost in costs if cost > 0]
    if total < 0 or not usable:
        return False
    reach = {0}
    for _ in range(total):
        reach |= {have + cost for have in reach for cost in usable if have + cost <= total}
        if total in reach:
            return True
    return total in reach


def coop_rival_pair(stage: int) -> list[dict[str, Any]]:
    """Draw a squad worth exactly 6 points. Duplicates are allowed."""
    rules = {
        1: {1: 1, 2: 2, 3: 3},
        2: {4: 2, 5: 3},
        3: {6: 2, 7: 3},
        4: {8: 2, 9: 2, 10: 3},
    }
    level = max(1, min(4, int(stage or 1)))
    weights = rules[level]
    pool: dict[int, list[dict[str, Any]]] = {}
    for hero in _catalog()["heroes"]:
        hero_level = int(hero["level"])
        if hero_level in weights:
            pool.setdefault(hero_level, []).append(hero)
    costs = {weights[hero_level] for hero_level in pool}
    if not _score_reachable(6, costs):
        raise ForgeError("这一级没有足够的对手。")
    picked: list[dict[str, Any]] = []
    left = 6
    while left > 0:
        choices: list[tuple[dict[str, Any], int]] = []
        for hero_level, heroes in pool.items():
            cost = weights[hero_level]
            if cost <= left and _score_reachable(left - cost, costs):
                choices.extend((hero, cost) for hero in heroes)
        if not choices:
            raise ForgeError("这一级没有凑齐对手。")
        hero, cost = random.choice(choices)
        picked.append(dict(hero))
        left -= cost
    return picked


def public_catalog() -> dict[str, Any]:
    from wujiang.tactical.trait_tiers import trait_category
    data = _catalog()
    def brief(entry: dict[str, Any]) -> dict[str, Any]:
        payload = {key: entry[key] for key in ("id", "kind", "name", "heroes", "ultimate", "cost", "description", "search") if key in entry}
        if entry.get("kind") == "trait":
            category = trait_category(str(entry.get("name") or ""))
            payload["category"] = category
            payload["cost"] = 4 if category == "strong" else 2
        return payload
    return {
        "skills": [brief(item) for item in data["skills"]],
        "traits": [brief(item) for item in data["traits"]],
        "races": data["races"],
        "attributes": data["attributes"],
        "roles": data["roles"],
    }


FORGE_POOL_FRACTIONS = {
    "fifth": (1, 5),
    "third": (1, 3),
    "half": (1, 2),
    "all": (1, 1),
}
FORGE_POOL_FIXED = {
    "twenty": 20,
}


def forge_pool_fraction(value: Any) -> str:
    text = str(value or "third")
    if text in FORGE_POOL_FRACTIONS or text in FORGE_POOL_FIXED:
        return text
    return "third"


def roll_part_pool(kind: str, fraction: str, keep: list[str] | None = None) -> list[str]:
    """Pick a fraction of implemented parts. `keep` is merged in afterwards."""
    rows = public_catalog()["skills" if kind == "skill" else "traits"]
    ids = [str(item["id"]) for item in rows if item.get("id")]
    known = set(ids)
    mode = forge_pool_fraction(fraction)
    if mode == "all" or not ids:
        chosen = set(ids)
    elif mode in FORGE_POOL_FIXED:
        count = min(len(ids), FORGE_POOL_FIXED[mode])
        chosen = set(random.sample(ids, count))
    else:
        num, den = FORGE_POOL_FRACTIONS[mode]
        count = max(1, min(len(ids), (len(ids) * num + den // 2) // den))
        chosen = set(random.sample(ids, count))
    for part_id in keep or []:
        if part_id in known:
            chosen.add(part_id)
    return [part_id for part_id in ids if part_id in chosen]


def _entry(part_id: str) -> dict[str, Any] | None:
    data = _catalog()
    for bucket in ("skills", "traits"):
        for entry in data[bucket]:
            if entry["id"] == part_id:
                return entry
    return None


def normalize_build(raw: Any, *, stat_cap: int, point_cap: int, skill_pool: set[str] | None = None, trait_pool: set[str] | None = None) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise ForgeError("武将配置无效。")
    cap = max(0, min(12, int(stat_cap)))
    budget = max(0, min(999, int(point_cap)))
    name = str(raw.get("name") or "未命名").strip()[:12] or "未命名"
    catalog = _catalog()
    race = str(raw.get("race") or "")
    attribute = str(raw.get("attribute") or "")
    role = str(raw.get("role") or "")
    if race and race not in catalog["races"]:
        race = ""
    if attribute and attribute not in catalog["attributes"]:
        attribute = ""
    if role and role not in catalog.get("roles", []):
        role = ""
    if not role and catalog.get("roles"):
        role = catalog["roles"][0]
    build = {
        "name": name,
        "race": race,
        "attribute": attribute,
        "role": role,
        "intro": str(raw.get("intro") or "").strip()[:240],
        "skills": [],
        "traits": [],
    }
    spent = 0
    for key in STAT_KEYS:
        try:
            value = int(raw.get(key))
        except (TypeError, ValueError):
            raise ForgeError("属性必须是整数。") from None
        if value < 0 or value > cap:
            raise ForgeError(f"{STAT_LABELS[key]}要在 0 到 {cap} 之间。")
        build[key] = value
        spent += (value - DEFAULT_STAT) * STAT_POINT_COST
    for part_id in list(dict.fromkeys(str(item) for item in (raw.get("skills") or []))):
        entry = _entry(part_id)
        if entry is None or entry["kind"] != "skill":
            raise ForgeError("有技能不在可选列表里。")
        if skill_pool is not None and part_id not in skill_pool:
            raise ForgeError("这个技能不在这一轮的池子里。")
        spent += int(entry["cost"])
        build["skills"].append(part_id)
    for part_id in list(dict.fromkeys(str(item) for item in (raw.get("traits") or []))):
        entry = _entry(part_id)
        if entry is None or entry["kind"] != "trait":
            raise ForgeError("有特性不在可选列表里。")
        if trait_pool is not None and part_id not in trait_pool:
            raise ForgeError("这个特性不在这一轮的池子里。")
        from wujiang.tactical.trait_tiers import trait_point_cost
        spent += trait_point_cost(str(entry.get("name") or ""))
        build["traits"].append(part_id)
    if spent > budget:
        raise ForgeError("点数不够。")
    build["spent"] = spent
    build["budget"] = budget
    return build


def install_forge_hero(team: int, build: dict[str, Any]) -> str:
    code = f"forge_{int(team)}"
    factories = _catalog()["factories"]
    skill_ids = list(build.get("skills") or [])
    trait_ids = list(build.get("traits") or [])

    preview_skills = [factories[part_id]() for part_id in skill_ids]
    preview_traits = [factories[part_id]() for part_id in trait_ids]

    class ForgeHero(AbstractHero):
        hero_code = code
        hero_name = str(build.get("name") or "未命名")
        attribute = str(build.get("attribute") or "")
        race = str(build.get("race") or "")
        role = str(build.get("role") or "")
        level = 1
        base_stats = Stats(
            attack=int(build["attack"]),
            defense=int(build["defense"]),
            speed=int(build["speed"]),
            attack_range=int(build["attack_range"]),
            mana=int(build["mana"]),
        )
        raw_skill_text = " ".join(skill.name for skill in preview_skills)
        raw_trait_text = " ".join(trait.name for trait in preview_traits)
        max_health = 1.0

        def build_skills(self) -> list[Skill]:
            return [factories[part_id]() for part_id in skill_ids]

        def build_traits(self) -> list[Trait]:
            return [factories[part_id]() for part_id in trait_ids]

    HERO_REGISTRY[code] = ForgeHero
    return code
