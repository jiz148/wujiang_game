from __future__ import annotations

import copy
import hashlib
from typing import Any

from wujiang.heroes.registry import list_heroes
from wujiang.strategy.models import EventLogEntry, StrategicHeroState, StrategyError, WorldState


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


def hero_specialty_for_code(hero_code: str) -> str:
    row = _hero_catalog().get(str(hero_code), {})
    stats = dict(row.get("stats") or {})
    attack = float(stats.get("attack", 1) or 1)
    defense = float(stats.get("defense", 1) or 1)
    speed = float(stats.get("speed", 1) or 1)
    mana = float(stats.get("mana", 0) or 0)
    if mana >= max(attack, defense, speed) and mana >= 3:
        return "aether_scholar"
    if defense >= max(attack, speed):
        return "guardian"
    if speed > attack:
        return "trainer"
    return "vanguard"


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
        if not hero.strategic_specialty:
            hero.strategic_specialty = hero_specialty_for_code(hero.hero_code)
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
        matches = hero.assignment_type == specialty["assignment_type"]
        if matches:
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
            summary="战败沉睡，忠诚下降。",
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
