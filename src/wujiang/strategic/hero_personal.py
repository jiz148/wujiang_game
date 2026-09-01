from __future__ import annotations

import copy
import hashlib
from typing import Any

from wujiang.tactical.heroes.registry import list_heroes
from wujiang.strategic.models import EventLogEntry, StrategicHeroState, StrategyError, WorldState


STRATEGIC_SKILLS: dict[str, dict[str, Any]] = {
    "raid": {
        "name": "奔袭",
        "assignment_type": "campaign",
        "effect": "随军出征时，公式战力 +40。",
        "battle_power": 40,
    },
    "inspire": {
        "name": "鼓号",
        "assignment_type": "campaign",
        "effect": "随军出征时，额外提供 +30 武将战力。",
        "hero_power_bonus": 30,
    },
    "hold_line": {
        "name": "坚守",
        "assignment_type": "garrison",
        "effect": "驻守时，该城兵力 +20；守城公式战力 +40。",
        "troops": 20,
        "battle_power": 40,
    },
    "fortify": {
        "name": "增垒",
        "assignment_type": "garrison",
        "effect": "驻守时，该城城防 +1。",
        "defense": 1,
    },
    "drill": {
        "name": "校阅",
        "assignment_type": "training",
        "effect": "训练时，该城兵力 +30。",
        "troops": 30,
    },
    "conscript": {
        "name": "征募",
        "assignment_type": "training",
        "effect": "训练时，该城兵力 +18。",
        "troops": 18,
    },
    "taxmaster": {
        "name": "度支",
        "assignment_type": "administration",
        "effect": "辅政时，该城金钱 +35。",
        "money": 35,
    },
    "logistics": {
        "name": "粮秣",
        "assignment_type": "administration",
        "effect": "辅政时，该城粮食 +50。",
        "food": 50,
    },
    "aether_rite": {
        "name": "祭仪",
        "assignment_type": "administration",
        "effect": "辅政时，该城以太 +10。",
        "ether": 10,
    },
    "envoy": {
        "name": "抚民",
        "assignment_type": "administration",
        "effect": "辅政时，该城统治支持 +2。",
        "support": 2,
    },
}

SKILL_POOLS = {
    "attack": ("raid", "inspire", "drill"),
    "defense": ("hold_line", "fortify", "envoy"),
    "speed": ("raid", "conscript", "logistics"),
    "mana": ("aether_rite", "taxmaster", "envoy"),
}

ASSIGNMENT_TO_SPECIALTY = {
    "campaign": "vanguard",
    "garrison": "guardian",
    "training": "trainer",
    "administration": "aether_scholar",
}

HERO_SPECIALTIES: dict[str, dict[str, Any]] = {
    "vanguard": {
        "name": "先锋统御",
        "assignment_type": "campaign",
        "mission_name": "证明统军能力",
        "effect": "随军出征时，现役军队士气 +3；无现役军队时势力后备兵力 +15。",
    },
    "guardian": {
        "name": "坚守卫士",
        "assignment_type": "garrison",
        "mission_name": "守护一座城邦",
        "effect": "驻守己方城市时，该城兵力 +25、统治支持 +1。",
    },
    "trainer": {
        "name": "军务教习",
        "assignment_type": "training",
        "mission_name": "整训城邦军务",
        "effect": "训练己方城市时，该城兵力 +35。",
    },
    "aether_scholar": {
        "name": "以太学者",
        "assignment_type": "administration",
        "mission_name": "梳理以太脉络",
        "effect": "辅佐内政时，所在己方城市以太 +10。",
    },
}

MISSION_REQUIRED_PROGRESS = 2
MISSION_DURATION_MONTHS = 3


def _clone_world(world: WorldState) -> WorldState:
    return WorldState.from_dict(copy.deepcopy(world.to_dict()))


def _stable_roll(world: WorldState, key: str, maximum: int = 100) -> int:
    if maximum <= 0:
        return 0
    digest = hashlib.sha256(f"{world.seed}:{key}".encode("utf-8")).digest()
    return int.from_bytes(digest[:4], "big") % maximum


def _hero_catalog() -> dict[str, dict[str, Any]]:
    return {
        str(item.get("code") or ""): item
        for item in list_heroes()
        if str(item.get("code") or "")
    }


def _hero_name(hero_code: str) -> str:
    row = _hero_catalog().get(str(hero_code))
    return str(row.get("name") or hero_code) if row is not None else str(hero_code)


def hero_skills_for_code(hero_code: str) -> list[str]:
    row = _hero_catalog().get(str(hero_code), {})
    stats = dict(row.get("stats") or {})
    role = str(row.get("role") or "")
    attack = float(stats.get("attack", 1) or 1)
    defense = float(stats.get("defense", 1) or 1)
    speed = float(stats.get("speed", 1) or 1)
    mana = float(stats.get("mana", 0) or 0)
    if any(token in role for token in ("法", "辅", "祭")):
        mana += 2
    if any(token in role for token in ("坦", "守", "卫")):
        defense += 2
    if any(token in role for token in ("骑", "刺", "游")):
        speed += 2
    ranked = sorted(
        (("attack", attack), ("defense", defense), ("speed", speed), ("mana", mana)),
        key=lambda item: (-item[1], item[0]),
    )
    digest = int.from_bytes(hashlib.sha256(str(hero_code).encode("utf-8")).digest()[:4], "big")
    primary_pool = SKILL_POOLS[ranked[0][0]]
    secondary_pool = SKILL_POOLS[ranked[1][0]]
    primary = primary_pool[digest % len(primary_pool)]
    secondary_choices = [skill_id for skill_id in secondary_pool if skill_id != primary] or list(secondary_pool)
    secondary = secondary_choices[(digest // 11) % len(secondary_choices)]
    return [primary, secondary]


def hero_specialty_for_code(hero_code: str) -> str:
    skills = hero_skills_for_code(hero_code)
    assignment = str(STRATEGIC_SKILLS[skills[0]]["assignment_type"])
    return ASSIGNMENT_TO_SPECIALTY.get(assignment, "vanguard")


def normalize_hero_strategic_skills(hero: StrategicHeroState) -> list[str]:
    raw = [str(skill_id) for skill_id in (hero.strategic_skills or []) if str(skill_id) in STRATEGIC_SKILLS]
    if len(raw) >= 2:
        return raw[:2]
    generated = hero_skills_for_code(hero.hero_code)
    merged = raw + [skill_id for skill_id in generated if skill_id not in raw]
    return merged[:2]


def strategic_skills_public(skill_ids: list[str] | tuple[str, ...] | None) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for skill_id in skill_ids or []:
        skill = STRATEGIC_SKILLS.get(str(skill_id))
        if skill is None:
            continue
        rows.append(
            {
                "id": skill_id,
                "name": skill["name"],
                "assignment_type": skill["assignment_type"],
                "effect": skill["effect"],
            }
        )
    return rows


def hero_skill_battle_bonus(hero: StrategicHeroState, *, assignment_type: str) -> int:
    bonus = 0
    for skill_id in normalize_hero_strategic_skills(hero):
        skill = STRATEGIC_SKILLS[skill_id]
        if str(skill["assignment_type"]) != assignment_type:
            continue
        bonus += int(skill.get("battle_power") or 0)
        bonus += int(skill.get("hero_power_bonus") or 0)
    return bonus


def loyalty_band(loyalty: int) -> dict[str, str]:
    value = max(0, min(100, int(loyalty)))
    if value >= 80:
        return {"id": "trusted", "label": "信任"}
    if value >= 60:
        return {"id": "stable", "label": "稳定"}
    if value >= 40:
        return {"id": "estranged", "label": "疏离"}
    if value >= 20:
        return {"id": "resistant", "label": "抗拒"}
    return {"id": "broken", "label": "决裂"}


def _lord_hero_code(world: WorldState, faction_id: str | None) -> str:
    if not faction_id:
        return ""
    office = next(
        (
            item
            for item in world.offices
            if item.faction_id == faction_id
            and item.office_type == "lord"
            and item.status != "disabled"
            and item.holder_type == "hero"
            and item.holder_id
        ),
        None,
    )
    return str(office.holder_id or "") if office is not None else ""


def _history(hero: StrategicHeroState, world: WorldState, event: str, summary: str, **details: Any) -> None:
    hero.personal_history.append(
        {
            "month": world.current_month,
            "event": event,
            "summary": summary,
            **details,
        }
    )
    hero.personal_history = hero.personal_history[-24:]


def adjust_hero_loyalty(
    world: WorldState,
    hero: StrategicHeroState,
    delta: int,
    *,
    event: str,
    summary: str,
) -> int:
    before = hero.loyalty
    hero.loyalty = max(0, min(100, before + int(delta)))
    actual = hero.loyalty - before
    _history(hero, world, event, summary, loyalty_delta=actual, loyalty=hero.loyalty)
    return actual


def adjust_hero_relationship(
    world: WorldState,
    hero: StrategicHeroState,
    other_hero_code: str,
    delta: int,
    *,
    event: str,
    summary: str,
) -> int:
    other_code = str(other_hero_code or "")
    if not other_code or other_code == hero.hero_code:
        return 0
    before = int(hero.relationships.get(other_code, 0))
    after = max(-100, min(100, before + int(delta)))
    hero.relationships[other_code] = after
    actual = after - before
    _history(
        hero,
        world,
        event,
        summary,
        relationship_hero_code=other_code,
        relationship_delta=actual,
        relationship=after,
    )
    return actual


def _start_personal_mission(world: WorldState, hero: StrategicHeroState) -> None:
    if hero.personal_mission_status != "none" or hero.status != "serving" or not hero.faction_id:
        return
    lord_code = _lord_hero_code(world, hero.faction_id)
    if not lord_code or lord_code == hero.hero_code:
        return
    specialty = HERO_SPECIALTIES[hero.strategic_specialty]
    hero.personal_mission_id = f"hero-mission:{hero.hero_code}:1"
    hero.personal_mission_status = "active"
    hero.personal_mission_started_month = world.current_month
    hero.personal_mission_due_month = world.current_month + MISSION_DURATION_MONTHS
    hero.personal_mission_assignment_type = str(specialty["assignment_type"])
    hero.personal_mission_progress = 0
    hero.personal_mission_required = MISSION_REQUIRED_PROGRESS
    hero.relationships.setdefault(lord_code, 0)
    _history(
        hero,
        world,
        "personal_mission_started",
        f"{_hero_name(hero.hero_code)}提出个人任务：{specialty['mission_name']}。",
        mission_id=hero.personal_mission_id,
        due_month=hero.personal_mission_due_month,
    )


def initialize_hero_personal_state(world: WorldState) -> WorldState:
    for hero in world.strategic_heroes:
        hero.strategic_skills = normalize_hero_strategic_skills(hero)
        if not hero.strategic_specialty:
            assignment = str(STRATEGIC_SKILLS[hero.strategic_skills[0]]["assignment_type"])
            hero.strategic_specialty = ASSIGNMENT_TO_SPECIALTY.get(assignment, hero_specialty_for_code(hero.hero_code))
        _start_personal_mission(world, hero)
    return world


def hero_command_accepts(
    world: WorldState,
    hero: StrategicHeroState,
    command_type: str,
) -> bool:
    normalized = str(command_type or "").strip()
    if normalized in {"reserve", "assignment:reserve"}:
        return True
    loyalty = max(0, min(100, int(hero.loyalty)))
    if loyalty >= 40:
        return True
    if loyalty < 20:
        return False
    threshold = loyalty
    roll = _stable_roll(world, f"hero-command:{world.current_month}:{hero.hero_code}:{normalized}")
    return roll < threshold


def require_hero_command_acceptance(
    world: WorldState,
    hero: StrategicHeroState,
    command_type: str,
) -> None:
    if hero_command_accepts(world, hero, command_type):
        return
    band = loyalty_band(hero.loyalty)["label"]
    raise StrategyError(
        f"{_hero_name(hero.hero_code)}当前忠诚为“{band}”({hero.loyalty})，本月拒绝这项命令。"
    )


def hero_personal_public(world: WorldState, hero: StrategicHeroState) -> dict[str, Any]:
    specialty_id = hero.strategic_specialty or hero_specialty_for_code(hero.hero_code)
    specialty = HERO_SPECIALTIES[specialty_id]
    lord_code = _lord_hero_code(world, hero.faction_id)
    mission = None
    if hero.personal_mission_status != "none":
        mission = {
            "id": hero.personal_mission_id,
            "name": specialty["mission_name"],
            "status": hero.personal_mission_status,
            "assignment_type": hero.personal_mission_assignment_type,
            "progress": hero.personal_mission_progress,
            "required": hero.personal_mission_required,
            "started_month": hero.personal_mission_started_month,
            "due_month": hero.personal_mission_due_month,
            "reward": "完成：忠诚 +10、对主公关系 +8；逾期：忠诚 -10、关系 -8。",
        }
    return {
        "loyalty_band": loyalty_band(hero.loyalty),
        "specialty": {
            "id": specialty_id,
            "name": specialty["name"],
            "assignment_type": specialty["assignment_type"],
            "effect": specialty["effect"],
        },
        "strategic_skills": strategic_skills_public(normalize_hero_strategic_skills(hero)),
        "lord_hero_code": lord_code,
        "lord_relationship": int(hero.relationships.get(lord_code, 0)) if lord_code else None,
        "relationships": dict(hero.relationships),
        "personal_mission": mission,
        "command_acceptance": {
            "reserve": True,
            "administration": hero_command_accepts(world, hero, "assignment:administration"),
            "training": hero_command_accepts(world, hero, "assignment:training"),
            "garrison": hero_command_accepts(world, hero, "assignment:garrison"),
            "campaign": hero_command_accepts(world, hero, "assignment:campaign"),
            "battle": hero_command_accepts(world, hero, "battle"),
            "relic_search": hero_command_accepts(world, hero, "relic_search"),
        },
        "recent_personal_history": [dict(item) for item in hero.personal_history[-5:]],
    }


def _assignment_city(world: WorldState, hero: StrategicHeroState):
    if hero.assignment_type in {"training", "garrison"}:
        city_id = hero.assignment_target_id
    else:
        city_id = hero.city_id or hero.ritual_city_id
    city = next(
        (
            item
            for item in world.cities
            if item.city_id == city_id and item.owner_faction_id == hero.faction_id
        ),
        None,
    )
    if city is not None:
        return city
    faction = next((item for item in world.factions if item.faction_id == hero.faction_id), None)
    return next(
        (
            item
            for item in world.cities
            if faction is not None
            and item.city_id == faction.capital_city_id
            and item.owner_faction_id == hero.faction_id
        ),
        None,
    )


def _apply_skill_effect(world: WorldState, hero: StrategicHeroState, skill_id: str) -> str:
    skill = STRATEGIC_SKILLS[skill_id]
    if skill["assignment_type"] == "campaign":
        army = next(
            (
                item
                for item in world.armies
                if item.commander_hero_code == hero.hero_code and item.status not in {"disbanded", "destroyed"}
            ),
            None,
        )
        if army is not None:
            before = army.morale
            army.morale = min(100, army.morale + 2)
            return f"{army.name}士气 +{army.morale - before}"
        faction = next(item for item in world.factions if item.faction_id == hero.faction_id)
        faction.resources.troops += 8
        return "势力后备兵力 +8"
    city = _assignment_city(world, hero)
    if city is None:
        return ""
    parts: list[str] = []
    troops = int(skill.get("troops") or 0)
    money = int(skill.get("money") or 0)
    food = int(skill.get("food") or 0)
    ether = int(skill.get("ether") or 0)
    defense = int(skill.get("defense") or 0)
    support = int(skill.get("support") or 0)
    if troops:
        city.resources.troops += troops
        parts.append(f"兵力 +{troops}")
    if money:
        city.resources.money += money
        parts.append(f"金钱 +{money}")
    if food:
        city.resources.food += food
        parts.append(f"粮食 +{food}")
    if ether:
        city.resources.ether += ether
        parts.append(f"以太 +{ether}")
    if defense:
        city.defense += defense
        parts.append(f"城防 +{defense}")
    if support:
        owner = city.owner_faction_id
        city.support_by_faction[owner] = min(100, int(city.support_by_faction.get(owner, 50)) + support)
        parts.append(f"统治支持 +{support}")
    return f"{city.name}{'、'.join(parts)}" if parts else ""


def _apply_specialty_effect(world: WorldState, hero: StrategicHeroState) -> str:
    if hero.strategic_specialty == "vanguard":
        army = next(
            (
                item
                for item in world.armies
                if item.commander_hero_code == hero.hero_code and item.status not in {"disbanded", "destroyed"}
            ),
            None,
        )
        if army is not None:
            before = army.morale
            army.morale = min(100, army.morale + 3)
            return f"{army.name}士气 +{army.morale - before}"
        faction = next(item for item in world.factions if item.faction_id == hero.faction_id)
        faction.resources.troops += 15
        return "势力后备兵力 +15"
    city = _assignment_city(world, hero)
    if city is None:
        return ""
    if hero.strategic_specialty == "guardian":
        city.resources.troops += 25
        city.support_by_faction[city.owner_faction_id] = min(
            100,
            int(city.support_by_faction.get(city.owner_faction_id, 50)) + 1,
        )
        return f"{city.name}兵力 +25、统治支持 +1"
    if hero.strategic_specialty == "trainer":
        city.resources.troops += 35
        return f"{city.name}兵力 +35"
    city.resources.ether += 10
    return f"{city.name}以太 +10"


def _apply_generic_duty_effect(world: WorldState, hero: StrategicHeroState) -> str:
    city = _assignment_city(world, hero)
    if city is None:
        return ""
    if hero.assignment_type == "training":
        city.resources.troops += 15
        return f"{city.name}兵力 +15"
    if hero.assignment_type == "garrison":
        city.resources.troops += 10
        return f"{city.name}兵力 +10"
    if hero.assignment_type == "administration":
        city.resources.money += 15
        city.resources.food += 10
        return f"{city.name}钱 +15、粮 +10"
    return ""


def advance_hero_personal_states(world: WorldState) -> WorldState:
    next_world = _clone_world(world)
    initialize_hero_personal_state(next_world)
    for hero in next_world.strategic_heroes:
        if (
            hero.status != "serving"
            or not hero.faction_id
            or hero.last_duty_settlement_month == next_world.current_month
        ):
            continue
        hero.last_duty_settlement_month = next_world.current_month
        specialty = HERO_SPECIALTIES[hero.strategic_specialty]
        matching_skills = [
            skill_id
            for skill_id in normalize_hero_strategic_skills(hero)
            if STRATEGIC_SKILLS[skill_id]["assignment_type"] == hero.assignment_type
        ]
        if matching_skills:
            effects = [part for skill_id in matching_skills if (part := _apply_skill_effect(next_world, hero, skill_id))]
            if effects:
                summary = "；".join(effects)
                skill_names = "、".join(STRATEGIC_SKILLS[skill_id]["name"] for skill_id in matching_skills)
                _history(
                    hero,
                    next_world,
                    "strategic_skills_applied",
                    f"{skill_names}生效：{summary}。",
                    skills=matching_skills,
                )
                next_world.event_log.append(
                    EventLogEntry(
                        month=next_world.current_month,
                        category="strategic_hero_skills",
                        message=f"{_hero_name(hero.hero_code)}的{skill_names}生效：{summary}。",
                        related_ids=[hero.hero_code, hero.faction_id, *matching_skills],
                    )
                )
        elif hero.assignment_type == specialty["assignment_type"]:
            effect = _apply_specialty_effect(next_world, hero)
            if effect:
                _history(
                    hero,
                    next_world,
                    "strategic_specialty_applied",
                    f"{specialty['name']}生效：{effect}。",
                    specialty=hero.strategic_specialty,
                )
                next_world.event_log.append(
                    EventLogEntry(
                        month=next_world.current_month,
                        category="strategic_hero_specialty",
                        message=f"{_hero_name(hero.hero_code)}的{specialty['name']}生效：{effect}。",
                        related_ids=[hero.hero_code, hero.faction_id],
                    )
                )
        elif hero.assignment_type in {"training", "garrison", "administration"}:
            effect = _apply_generic_duty_effect(next_world, hero)
            if effect:
                _history(
                    hero,
                    next_world,
                    "generic_duty_applied",
                    f"驻城职责生效：{effect}。",
                    assignment_type=hero.assignment_type,
                )
                next_world.event_log.append(
                    EventLogEntry(
                        month=next_world.current_month,
                        category="strategic_hero_duty",
                        message=f"{_hero_name(hero.hero_code)}驻城生效：{effect}。",
                        related_ids=[hero.hero_code, hero.faction_id],
                    )
                )
        if hero.personal_mission_status != "active":
            continue
        if hero.assignment_type == hero.personal_mission_assignment_type:
            hero.personal_mission_progress = min(
                hero.personal_mission_required,
                hero.personal_mission_progress + 1,
            )
            _history(
                hero,
                next_world,
                "personal_mission_progress",
                f"个人任务进度 {hero.personal_mission_progress}/{hero.personal_mission_required}。",
                mission_id=hero.personal_mission_id,
            )
        lord_code = _lord_hero_code(next_world, hero.faction_id)
        if hero.personal_mission_progress >= hero.personal_mission_required:
            hero.personal_mission_status = "completed"
            loyalty_delta = adjust_hero_loyalty(
                next_world,
                hero,
                10,
                event="personal_mission_completed",
                summary="个人任务完成，忠诚提升。",
            )
            relation_delta = adjust_hero_relationship(
                next_world,
                hero,
                lord_code,
                8,
                event="personal_mission_completed",
                summary="主公兑现了个人任务安排。",
            )
            next_world.event_log.append(
                EventLogEntry(
                    month=next_world.current_month,
                    category="strategic_hero_mission_completed",
                    message=(
                        f"{_hero_name(hero.hero_code)}完成个人任务；"
                        f"忠诚 {loyalty_delta:+d}，对主公关系 {relation_delta:+d}。"
                    ),
                    related_ids=[hero.hero_code, hero.faction_id, lord_code, str(hero.personal_mission_id)],
                )
            )
        elif next_world.current_month >= int(hero.personal_mission_due_month or 0):
            hero.personal_mission_status = "failed"
            loyalty_delta = adjust_hero_loyalty(
                next_world,
                hero,
                -10,
                event="personal_mission_failed",
                summary="个人任务逾期，忠诚下降。",
            )
            relation_delta = adjust_hero_relationship(
                next_world,
                hero,
                lord_code,
                -8,
                event="personal_mission_failed",
                summary="个人任务长期未获支持。",
            )
            next_world.event_log.append(
                EventLogEntry(
                    month=next_world.current_month,
                    category="strategic_hero_mission_failed",
                    message=(
                        f"{_hero_name(hero.hero_code)}的个人任务逾期；"
                        f"忠诚 {loyalty_delta:+d}，对主公关系 {relation_delta:+d}。"
                    ),
                    related_ids=[hero.hero_code, hero.faction_id, lord_code, str(hero.personal_mission_id)],
                )
            )
    next_world.validate()
    return next_world


def record_hero_appointment(
    world: WorldState,
    *,
    faction_id: str,
    hero_code: str,
    lord_hero_code: str,
) -> None:
    hero = next((item for item in world.strategic_heroes if item.hero_code == hero_code), None)
    if hero is None:
        return
    adjust_hero_loyalty(
        world,
        hero,
        5,
        event="appointed",
        summary="获得正式任命，忠诚提升。",
    )
    adjust_hero_relationship(
        world,
        hero,
        lord_hero_code,
        8,
        event="appointed",
        summary="主公授予正式职位。",
    )
    initialize_hero_personal_state(world)


def record_hero_battle_outcome(
    world: WorldState,
    *,
    faction_id: str,
    committed: list[str],
    surviving: list[str],
) -> None:
    lord_code = _lord_hero_code(world, faction_id)
    survivors = [
        hero
        for hero in world.strategic_heroes
        if hero.hero_code in surviving and hero.faction_id == faction_id
    ]
    for hero in survivors:
        adjust_hero_loyalty(
            world,
            hero,
            3,
            event="battle_survived",
            summary="完成出战并生还，忠诚提升。",
        )
        for other in survivors:
            if other.hero_code != hero.hero_code:
                adjust_hero_relationship(
                    world,
                    hero,
                    other.hero_code,
                    3,
                    event="battle_survived_together",
                    summary=f"与{_hero_name(other.hero_code)}共同经历战斗。",
                )
    for code in committed:
        if code in surviving:
            continue
        hero = next((item for item in world.strategic_heroes if item.hero_code == code), None)
        if hero is None:
            continue
        adjust_hero_loyalty(
            world,
            hero,
            -5,
            event="battle_defeat",
            summary="战败负伤，忠诚下降。",
        )
        adjust_hero_relationship(
            world,
            hero,
            lord_code,
            -3,
            event="battle_defeat",
            summary="因主公的出战决定而战败。",
        )


def record_hero_city_loss(
    world: WorldState,
    *,
    hero: StrategicHeroState,
    previous_faction_id: str,
) -> None:
    lord_code = _lord_hero_code(world, previous_faction_id)
    adjust_hero_loyalty(
        world,
        hero,
        -10,
        event="ritual_city_lost",
        summary="祭祀绑定城市失守，忠诚下降。",
    )
    adjust_hero_relationship(
        world,
        hero,
        lord_code,
        -10,
        event="ritual_city_lost",
        summary="主公未能守住祭祀绑定城市。",
    )


def record_hero_crisis_choice(
    world: WorldState,
    *,
    faction_id: str,
    choice_id: str,
    decision_id: str,
) -> None:
    deltas = {
        "contribute": (3, 2),
        "cooperate": (2, 2),
        "betray": (-6, -5),
    }
    if choice_id not in deltas:
        return
    loyalty_delta, relationship_delta = deltas[choice_id]
    lord_code = _lord_hero_code(world, faction_id)
    for hero in world.strategic_heroes:
        if hero.faction_id != faction_id or hero.status not in {"serving", "sleeping"} or hero.hero_code == lord_code:
            continue
        if any(
            item.get("event") == "world_crisis_reaction" and item.get("decision_id") == decision_id
            for item in hero.personal_history
        ):
            continue
        adjust_hero_loyalty(
            world,
            hero,
            loyalty_delta,
            event="world_crisis_reaction",
            summary=f"对雪鬼动员选择“{choice_id}”作出反应。",
        )
        adjust_hero_relationship(
            world,
            hero,
            lord_code,
            relationship_delta,
            event="world_crisis_reaction",
            summary="对主公的雪鬼动员选择作出反应。",
        )
        if hero.personal_history:
            hero.personal_history[-1]["decision_id"] = decision_id
