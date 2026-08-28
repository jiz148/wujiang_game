from __future__ import annotations

import copy
from dataclasses import dataclass, field
from typing import Any

from wujiang.strategy.errors import StrategyError
from wujiang.strategy.migrations import CURRENT_STRATEGY_SAVE_VERSION, migrate_world_payload


def _string_list(values: Any) -> list[str]:
    if not isinstance(values, list):
        return []
    return [str(value) for value in values if str(value)]


def _string_dict(raw: Any) -> dict[str, str]:
    if not isinstance(raw, dict):
        return {}
    return {str(key): str(value) for key, value in raw.items()}


def _int_dict(raw: Any) -> dict[str, int]:
    if not isinstance(raw, dict):
        return {}
    result: dict[str, int] = {}
    for key, value in raw.items():
        try:
            result[str(key)] = int(value)
        except (TypeError, ValueError):
            result[str(key)] = 0
    return result


def _plain_dict(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        return {}
    return {str(key): value for key, value in raw.items()}


@dataclass(slots=True)
class ResourceBundle:
    food: int
    money: int
    population: int
    ether: int
    troops: int

    def __post_init__(self) -> None:
        for field_name in ("food", "money", "population", "ether", "troops"):
            value = getattr(self, field_name)
            if int(value) < 0:
                raise StrategyError(f"资源 {field_name} 不能为负数。")
            setattr(self, field_name, int(value))

    def to_dict(self) -> dict[str, int]:
        return {
            "food": self.food,
            "money": self.money,
            "population": self.population,
            "ether": self.ether,
            "troops": self.troops,
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> ResourceBundle:
        return cls(
            food=int(raw.get("food", 0)),
            money=int(raw.get("money", 0)),
            population=int(raw.get("population", 0)),
            ether=int(raw.get("ether", 0)),
            troops=int(raw.get("troops", 0)),
        )


@dataclass(slots=True)
class Faction:
    faction_id: str
    name: str
    controller_user_id: int | None = None
    is_ai: bool = False
    capital_city_id: str | None = None
    resources: ResourceBundle = field(default_factory=lambda: ResourceBundle(0, 0, 0, 0, 0))
    diplomacy: dict[str, str] = field(default_factory=dict)
    relations: dict[str, int] = field(default_factory=dict)
    influence_by_faction: dict[str, int] = field(default_factory=dict)
    diplomatic_reputation: int = 50
    memory_tags: list[str] = field(default_factory=list)
    tactic_techs: list[str] = field(default_factory=list)
    faction_type: str = "major"
    governor_name: str | None = None
    incited_against_faction_id: str | None = None
    incited_by_faction_id: str | None = None

    def __post_init__(self) -> None:
        self.diplomatic_reputation = max(0, min(100, int(self.diplomatic_reputation)))
        self.relations = {
            str(faction_id): max(-100, min(100, int(score)))
            for faction_id, score in self.relations.items()
            if str(faction_id)
        }
        self.influence_by_faction = {
            str(faction_id): max(0, min(100, int(score)))
            for faction_id, score in self.influence_by_faction.items()
            if str(faction_id)
        }

    @property
    def is_neutral_city_state(self) -> bool:
        return self.faction_type == "neutral_city_state"

    @property
    def is_major(self) -> bool:
        return self.faction_type == "major"

    @property
    def is_world_crisis(self) -> bool:
        return self.faction_type == "world_crisis"

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.faction_id,
            "name": self.name,
            "controller_user_id": self.controller_user_id,
            "is_ai": self.is_ai,
            "capital_city_id": self.capital_city_id,
            "resources": self.resources.to_dict(),
            "diplomacy": dict(self.diplomacy),
            "relations": dict(self.relations),
            "influence_by_faction": dict(self.influence_by_faction),
            "diplomatic_reputation": self.diplomatic_reputation,
            "memory_tags": list(self.memory_tags),
            "tactic_techs": list(self.tactic_techs),
            "faction_type": self.faction_type,
            "governor_name": self.governor_name,
            "incited_against_faction_id": self.incited_against_faction_id,
            "incited_by_faction_id": self.incited_by_faction_id,
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> Faction:
        return cls(
            faction_id=str(raw.get("id") or raw.get("faction_id") or ""),
            name=str(raw.get("name") or ""),
            controller_user_id=raw.get("controller_user_id"),
            is_ai=bool(raw.get("is_ai", False)),
            capital_city_id=raw.get("capital_city_id"),
            resources=ResourceBundle.from_dict(raw.get("resources") or {}),
            diplomacy=_string_dict(raw.get("diplomacy")),
            relations=_int_dict(raw.get("relations")),
            influence_by_faction=_int_dict(raw.get("influence_by_faction")),
            diplomatic_reputation=int(raw.get("diplomatic_reputation", 50)),
            memory_tags=_string_list(raw.get("memory_tags")),
            tactic_techs=_string_list(raw.get("tactic_techs")),
            faction_type=str(raw.get("faction_type") or "major"),
            governor_name=(str(raw.get("governor_name")) if raw.get("governor_name") else None),
            incited_against_faction_id=(
                str(raw.get("incited_against_faction_id")) if raw.get("incited_against_faction_id") else None
            ),
            incited_by_faction_id=(
                str(raw.get("incited_by_faction_id")) if raw.get("incited_by_faction_id") else None
            ),
        )


@dataclass(slots=True)
class DiplomaticAgreement:
    agreement_id: str
    agreement_type: str
    major_faction_id: str
    neutral_faction_id: str
    started_month: int
    expires_month: int | None = None
    status: str = "active"
    ended_month: int | None = None
    end_reason: str | None = None
    terms: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.agreement_id,
            "agreement_type": self.agreement_type,
            "major_faction_id": self.major_faction_id,
            "neutral_faction_id": self.neutral_faction_id,
            "started_month": self.started_month,
            "expires_month": self.expires_month,
            "status": self.status,
            "ended_month": self.ended_month,
            "end_reason": self.end_reason,
            "terms": dict(self.terms),
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> DiplomaticAgreement:
        return cls(
            agreement_id=str(raw.get("id") or raw.get("agreement_id") or ""),
            agreement_type=str(raw.get("agreement_type") or ""),
            major_faction_id=str(raw.get("major_faction_id") or ""),
            neutral_faction_id=str(raw.get("neutral_faction_id") or ""),
            started_month=max(1, int(raw.get("started_month", 1))),
            expires_month=(
                int(raw["expires_month"])
                if raw.get("expires_month") is not None
                else max(1, int(raw.get("started_month", 1))) + 3
            ),
            status=str(raw.get("status") or "active"),
            ended_month=(int(raw["ended_month"]) if raw.get("ended_month") is not None else None),
            end_reason=(str(raw["end_reason"]) if raw.get("end_reason") else None),
            terms=_plain_dict(raw.get("terms")),
        )


@dataclass(slots=True)
class MapNode:
    node_id: str
    name: str
    node_type: str
    x: int
    y: int
    connected_node_ids: list[str] = field(default_factory=list)
    traits: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.node_id,
            "name": self.name,
            "type": self.node_type,
            "x": self.x,
            "y": self.y,
            "connected_node_ids": list(self.connected_node_ids),
            "traits": list(self.traits),
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> MapNode:
        return cls(
            node_id=str(raw.get("id") or raw.get("node_id") or ""),
            name=str(raw.get("name") or ""),
            node_type=str(raw.get("type") or raw.get("node_type") or "city"),
            x=int(raw.get("x", 0)),
            y=int(raw.get("y", 0)),
            connected_node_ids=_string_list(raw.get("connected_node_ids")),
            traits=_string_list(raw.get("traits")),
        )


@dataclass(slots=True)
class City:
    city_id: str
    node_id: str
    name: str
    owner_faction_id: str
    level: int
    resources: ResourceBundle
    defense: int
    governor_id: str | None = None
    policy: str = "稳定优先"
    buildings: list[str] = field(default_factory=list)
    building_levels: dict[str, int] = field(default_factory=dict)
    registered_units: dict[str, int] = field(default_factory=dict)
    relics_stored: list[str] = field(default_factory=list)
    altars: list[str] = field(default_factory=list)
    support_by_faction: dict[str, int] = field(default_factory=dict)
    local_factions: list[str] = field(default_factory=list)
    traits: list[str] = field(default_factory=list)
    event_states: list[str] = field(default_factory=list)
    troop_features: list[str] = field(default_factory=list)
    occupation: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.level = int(self.level)
        self.defense = int(self.defense)
        if self.level <= 0:
            raise StrategyError("城市等级必须为正数。")
        if self.defense < 0:
            raise StrategyError("城市防御不能为负数。")
        self.support_by_faction = {
            faction_id: max(0, min(100, int(value)))
            for faction_id, value in self.support_by_faction.items()
        }
        self.building_levels = {
            str(building_id): max(1, int(level))
            for building_id, level in self.building_levels.items()
            if str(building_id) and int(level) > 0
        }
        for building_id in self.buildings:
            if building_id != "政厅":
                self.building_levels.setdefault(building_id, 1)
        self.registered_units = {
            str(unit_type): max(0, int(count))
            for unit_type, count in self.registered_units.items()
            if str(unit_type) and int(count) > 0
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.city_id,
            "node_id": self.node_id,
            "name": self.name,
            "owner_faction_id": self.owner_faction_id,
            "level": self.level,
            "resources": self.resources.to_dict(),
            "defense": self.defense,
            "governor_id": self.governor_id,
            "policy": self.policy,
            "buildings": list(self.buildings),
            "building_levels": dict(self.building_levels),
            "registered_units": dict(self.registered_units),
            "relics_stored": list(self.relics_stored),
            "altars": list(self.altars),
            "support_by_faction": dict(self.support_by_faction),
            "local_factions": list(self.local_factions),
            "traits": list(self.traits),
            "event_states": list(self.event_states),
            "troop_features": list(self.troop_features),
            "occupation": dict(self.occupation),
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> City:
        return cls(
            city_id=str(raw.get("id") or raw.get("city_id") or ""),
            node_id=str(raw.get("node_id") or ""),
            name=str(raw.get("name") or ""),
            owner_faction_id=str(raw.get("owner_faction_id") or ""),
            level=int(raw.get("level", 1)),
            resources=ResourceBundle.from_dict(raw.get("resources") or {}),
            defense=int(raw.get("defense", 0)),
            governor_id=raw.get("governor_id"),
            policy=str(raw.get("policy") or "稳定优先"),
            buildings=_string_list(raw.get("buildings")),
            building_levels=_int_dict(raw.get("building_levels")),
            registered_units=_int_dict(raw.get("registered_units")),
            relics_stored=_string_list(raw.get("relics_stored")),
            altars=_string_list(raw.get("altars")),
            support_by_faction=_int_dict(raw.get("support_by_faction")),
            local_factions=_string_list(raw.get("local_factions")),
            traits=_string_list(raw.get("traits")),
            event_states=_string_list(raw.get("event_states")),
            troop_features=_string_list(raw.get("troop_features")),
            occupation=_plain_dict(raw.get("occupation")),
        )


@dataclass(frozen=True, slots=True)
class CampaignMember:
    user_id: int
    username: str
    role: str
    faction_id: str
    is_initial_player: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "user_id": self.user_id,
            "username": self.username,
            "role": self.role,
            "faction_id": self.faction_id,
            "is_initial_player": self.is_initial_player,
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> CampaignMember:
        return cls(
            user_id=int(raw.get("user_id", 0)),
            username=str(raw.get("username") or ""),
            role=str(raw.get("role") or "lord"),
            faction_id=str(raw.get("faction_id") or ""),
            is_initial_player=bool(raw.get("is_initial_player", True)),
        )


@dataclass(slots=True)
class PendingBattle:
    battle_id: str
    month: int
    attacker_faction_id: str
    defender_faction_id: str
    source_city_id: str
    target_city_id: str
    resolution_mode: str
    attacker_troops: int
    defender_troops: int
    status: str = "pending"
    winner_faction_id: str | None = None
    battle_room_id: str | None = None
    battle_room_invite_path: str | None = None
    attacker_hero_codes: list[str] | None = None
    defender_hero_codes: list[str] | None = None
    attacker_office_id: str | None = None
    attacker_registered_units: dict[str, int] = field(default_factory=dict)
    defender_registered_units: dict[str, int] = field(default_factory=dict)
    report: list[str] = field(default_factory=list)
    battle_result: dict[str, Any] = field(default_factory=dict)
    source_kind: str = "legacy_city_attack"
    source_entity_id: str | None = None
    battle_trigger: str | None = None
    battle_node_id: str | None = None
    attacker_army_ids: list[str] = field(default_factory=list)
    defender_army_ids: list[str] = field(default_factory=list)
    army_snapshots: dict[str, dict[str, Any]] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.month = int(self.month)
        self.attacker_troops = int(self.attacker_troops)
        self.defender_troops = int(self.defender_troops)
        self.attacker_registered_units = {
            str(unit_type): max(0, int(count))
            for unit_type, count in self.attacker_registered_units.items()
            if str(unit_type) and int(count) > 0
        }
        self.defender_registered_units = {
            str(unit_type): max(0, int(count))
            for unit_type, count in self.defender_registered_units.items()
            if str(unit_type) and int(count) > 0
        }
        self.source_kind = str(self.source_kind or "legacy_city_attack")
        self.source_entity_id = str(self.source_entity_id) if self.source_entity_id else None
        self.battle_trigger = str(self.battle_trigger) if self.battle_trigger else None
        self.battle_node_id = str(self.battle_node_id) if self.battle_node_id else None
        self.attacker_army_ids = [str(army_id) for army_id in self.attacker_army_ids if str(army_id)]
        self.defender_army_ids = [str(army_id) for army_id in self.defender_army_ids if str(army_id)]
        self.army_snapshots = {
            str(army_id): dict(snapshot)
            for army_id, snapshot in self.army_snapshots.items()
            if str(army_id) and isinstance(snapshot, dict)
        }
        if self.attacker_troops < 0 or self.defender_troops < 0:
            raise StrategyError("战斗兵力不能为负数。")

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.battle_id,
            "month": self.month,
            "attacker_faction_id": self.attacker_faction_id,
            "defender_faction_id": self.defender_faction_id,
            "source_city_id": self.source_city_id,
            "target_city_id": self.target_city_id,
            "resolution_mode": self.resolution_mode,
            "attacker_troops": self.attacker_troops,
            "defender_troops": self.defender_troops,
            "status": self.status,
            "winner_faction_id": self.winner_faction_id,
            "battle_room_id": self.battle_room_id,
            "battle_room_invite_path": self.battle_room_invite_path,
            "attacker_hero_codes": list(self.attacker_hero_codes) if self.attacker_hero_codes is not None else None,
            "defender_hero_codes": list(self.defender_hero_codes) if self.defender_hero_codes is not None else None,
            "attacker_office_id": self.attacker_office_id,
            "attacker_registered_units": dict(self.attacker_registered_units),
            "defender_registered_units": dict(self.defender_registered_units),
            "report": list(self.report),
            "battle_result": dict(self.battle_result),
            "source_kind": self.source_kind,
            "source_entity_id": self.source_entity_id,
            "battle_trigger": self.battle_trigger,
            "battle_node_id": self.battle_node_id,
            "attacker_army_ids": list(self.attacker_army_ids),
            "defender_army_ids": list(self.defender_army_ids),
            "army_snapshots": {army_id: dict(snapshot) for army_id, snapshot in self.army_snapshots.items()},
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> PendingBattle:
        return cls(
            battle_id=str(raw.get("id") or raw.get("battle_id") or ""),
            month=int(raw.get("month", 1)),
            attacker_faction_id=str(raw.get("attacker_faction_id") or ""),
            defender_faction_id=str(raw.get("defender_faction_id") or ""),
            source_city_id=str(raw.get("source_city_id") or ""),
            target_city_id=str(raw.get("target_city_id") or ""),
            resolution_mode=str(raw.get("resolution_mode") or "quick"),
            attacker_troops=int(raw.get("attacker_troops", 0)),
            defender_troops=int(raw.get("defender_troops", 0)),
            status=str(raw.get("status") or "pending"),
            winner_faction_id=raw.get("winner_faction_id"),
            battle_room_id=raw.get("battle_room_id"),
            battle_room_invite_path=raw.get("battle_room_invite_path"),
            attacker_hero_codes=_string_list(raw.get("attacker_hero_codes")) if "attacker_hero_codes" in raw else None,
            defender_hero_codes=_string_list(raw.get("defender_hero_codes")) if "defender_hero_codes" in raw else None,
            attacker_office_id=(
                str(raw.get("attacker_office_id")) if raw.get("attacker_office_id") is not None else None
            ),
            attacker_registered_units=_int_dict(raw.get("attacker_registered_units")),
            defender_registered_units=_int_dict(raw.get("defender_registered_units")),
            report=_string_list(raw.get("report")),
            battle_result=_plain_dict(raw.get("battle_result") or raw.get("result")),
            source_kind=str(raw.get("source_kind") or "legacy_city_attack"),
            source_entity_id=(str(raw.get("source_entity_id")) if raw.get("source_entity_id") else None),
            battle_trigger=(str(raw.get("battle_trigger")) if raw.get("battle_trigger") else None),
            battle_node_id=(str(raw.get("battle_node_id")) if raw.get("battle_node_id") else None),
            attacker_army_ids=_string_list(raw.get("attacker_army_ids")),
            defender_army_ids=_string_list(raw.get("defender_army_ids")),
            army_snapshots={
                str(army_id): dict(snapshot)
                for army_id, snapshot in (raw.get("army_snapshots") or {}).items()
                if isinstance(snapshot, dict)
            },
        )


@dataclass(slots=True)
class EventLogEntry:
    month: int
    category: str
    message: str
    visibility: str = "player_visible"
    related_ids: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "month": self.month,
            "category": self.category,
            "message": self.message,
            "visibility": self.visibility,
            "related_ids": list(self.related_ids),
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> EventLogEntry:
        return cls(
            month=int(raw.get("month", 1)),
            category=str(raw.get("category") or "system"),
            message=str(raw.get("message") or ""),
            visibility=str(raw.get("visibility") or "player_visible"),
            related_ids=_string_list(raw.get("related_ids")),
        )


@dataclass(slots=True)
class StoryEvent:
    event_id: str
    template_id: str
    faction_id: str
    city_id: str
    opened_month: int
    status: str = "pending"
    choice_id: str | None = None
    resolved_month: int | None = None
    outcome_summary: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.event_id,
            "template_id": self.template_id,
            "faction_id": self.faction_id,
            "city_id": self.city_id,
            "opened_month": self.opened_month,
            "status": self.status,
            "choice_id": self.choice_id,
            "resolved_month": self.resolved_month,
            "outcome_summary": self.outcome_summary,
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> StoryEvent:
        resolved_month = raw.get("resolved_month")
        return cls(
            event_id=str(raw.get("id") or raw.get("event_id") or ""),
            template_id=str(raw.get("template_id") or ""),
            faction_id=str(raw.get("faction_id") or ""),
            city_id=str(raw.get("city_id") or ""),
            opened_month=int(raw.get("opened_month", 1)),
            status=str(raw.get("status") or "pending"),
            choice_id=str(raw.get("choice_id")) if raw.get("choice_id") is not None else None,
            resolved_month=int(resolved_month) if resolved_month is not None else None,
            outcome_summary=str(raw.get("outcome_summary") or ""),
        )


@dataclass(slots=True)
class ScheduledConsequence:
    consequence_id: str
    source_event_id: str
    effect_id: str
    faction_id: str
    city_id: str
    due_month: int
    description: str
    status: str = "pending"

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.consequence_id,
            "source_event_id": self.source_event_id,
            "effect_id": self.effect_id,
            "faction_id": self.faction_id,
            "city_id": self.city_id,
            "due_month": self.due_month,
            "description": self.description,
            "status": self.status,
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> ScheduledConsequence:
        return cls(
            consequence_id=str(raw.get("id") or raw.get("consequence_id") or ""),
            source_event_id=str(raw.get("source_event_id") or ""),
            effect_id=str(raw.get("effect_id") or ""),
            faction_id=str(raw.get("faction_id") or ""),
            city_id=str(raw.get("city_id") or ""),
            due_month=int(raw.get("due_month", 1)),
            description=str(raw.get("description") or ""),
            status=str(raw.get("status") or "pending"),
        )


@dataclass(slots=True)
class StrategicHeroState:
    hero_code: str
    status: str = "roaming"
    faction_id: str | None = None
    city_id: str | None = None
    ritual_city_id: str | None = None
    office_id: str | None = None
    controller_type: str = "ai"
    controller_user_id: int | None = None
    loyalty: int = 50
    sleeping_until_month: int | None = None
    assignment_type: str = "reserve"
    assignment_target_id: str | None = None
    last_personal_action_month: int | None = None
    strategic_specialty: str = ""
    relationships: dict[str, int] = field(default_factory=dict)
    personal_mission_id: str | None = None
    personal_mission_status: str = "none"
    personal_mission_started_month: int | None = None
    personal_mission_due_month: int | None = None
    personal_mission_assignment_type: str | None = None
    personal_mission_progress: int = 0
    personal_mission_required: int = 2
    last_duty_settlement_month: int | None = None
    personal_history: list[dict[str, Any]] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.loyalty = max(0, min(100, int(self.loyalty)))
        self.relationships = {
            str(hero_code): max(-100, min(100, int(score)))
            for hero_code, score in self.relationships.items()
            if str(hero_code)
        }
        self.personal_mission_progress = max(0, int(self.personal_mission_progress))
        self.personal_mission_required = max(1, int(self.personal_mission_required))
        self.personal_history = [_plain_dict(item) for item in self.personal_history if isinstance(item, dict)]

    def to_dict(self) -> dict[str, Any]:
        return {
            "hero_code": self.hero_code,
            "status": self.status,
            "faction_id": self.faction_id,
            "city_id": self.city_id,
            "ritual_city_id": self.ritual_city_id,
            "office_id": self.office_id,
            "controller_type": self.controller_type,
            "controller_user_id": self.controller_user_id,
            "loyalty": self.loyalty,
            "sleeping_until_month": self.sleeping_until_month,
            "assignment_type": self.assignment_type,
            "assignment_target_id": self.assignment_target_id,
            "last_personal_action_month": self.last_personal_action_month,
            "strategic_specialty": self.strategic_specialty,
            "relationships": dict(self.relationships),
            "personal_mission_id": self.personal_mission_id,
            "personal_mission_status": self.personal_mission_status,
            "personal_mission_started_month": self.personal_mission_started_month,
            "personal_mission_due_month": self.personal_mission_due_month,
            "personal_mission_assignment_type": self.personal_mission_assignment_type,
            "personal_mission_progress": self.personal_mission_progress,
            "personal_mission_required": self.personal_mission_required,
            "last_duty_settlement_month": self.last_duty_settlement_month,
            "personal_history": [dict(item) for item in self.personal_history],
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> StrategicHeroState:
        controller_user_id = raw.get("controller_user_id")
        sleeping_until_month = raw.get("sleeping_until_month")
        last_personal_action_month = raw.get("last_personal_action_month")
        personal_mission_started_month = raw.get("personal_mission_started_month")
        personal_mission_due_month = raw.get("personal_mission_due_month")
        last_duty_settlement_month = raw.get("last_duty_settlement_month")
        return cls(
            hero_code=str(raw.get("hero_code") or raw.get("code") or ""),
            status=str(raw.get("status") or "roaming"),
            faction_id=str(raw.get("faction_id")) if raw.get("faction_id") is not None else None,
            city_id=str(raw.get("city_id")) if raw.get("city_id") is not None else None,
            ritual_city_id=(str(raw.get("ritual_city_id")) if raw.get("ritual_city_id") is not None else None),
            office_id=str(raw.get("office_id")) if raw.get("office_id") is not None else None,
            controller_type=str(raw.get("controller_type") or "ai"),
            controller_user_id=int(controller_user_id) if controller_user_id is not None else None,
            loyalty=max(0, min(100, int(raw.get("loyalty", 50)))),
            sleeping_until_month=int(sleeping_until_month) if sleeping_until_month is not None else None,
            assignment_type=str(raw.get("assignment_type") or "reserve"),
            assignment_target_id=(
                str(raw.get("assignment_target_id")) if raw.get("assignment_target_id") is not None else None
            ),
            last_personal_action_month=(
                int(last_personal_action_month) if last_personal_action_month is not None else None
            ),
            strategic_specialty=str(raw.get("strategic_specialty") or ""),
            relationships={
                str(hero_code): int(score)
                for hero_code, score in dict(raw.get("relationships") or {}).items()
            },
            personal_mission_id=(
                str(raw.get("personal_mission_id")) if raw.get("personal_mission_id") is not None else None
            ),
            personal_mission_status=str(raw.get("personal_mission_status") or "none"),
            personal_mission_started_month=(
                int(personal_mission_started_month) if personal_mission_started_month is not None else None
            ),
            personal_mission_due_month=(
                int(personal_mission_due_month) if personal_mission_due_month is not None else None
            ),
            personal_mission_assignment_type=(
                str(raw.get("personal_mission_assignment_type"))
                if raw.get("personal_mission_assignment_type") is not None
                else None
            ),
            personal_mission_progress=max(0, int(raw.get("personal_mission_progress", 0))),
            personal_mission_required=max(1, int(raw.get("personal_mission_required", 2))),
            last_duty_settlement_month=(
                int(last_duty_settlement_month) if last_duty_settlement_month is not None else None
            ),
            personal_history=[
                dict(item) for item in raw.get("personal_history", []) if isinstance(item, dict)
            ],
        )


@dataclass(slots=True)
class RelicState:
    relic_id: str
    hero_code: str
    name: str
    state: str = "scattered"
    condition: str = "intact"
    location_node_id: str | None = None
    location_city_id: str | None = None
    owner_faction_id: str | None = None
    altar_id: str | None = None
    maintenance_ether_cost: int = 10
    discovered_by_faction_ids: list[str] = field(default_factory=list)
    last_changed_month: int = 1
    history: list[dict[str, Any]] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.maintenance_ether_cost = max(0, int(self.maintenance_ether_cost))
        self.last_changed_month = max(1, int(self.last_changed_month))
        self.discovered_by_faction_ids = sorted(set(self.discovered_by_faction_ids))
        self.history = [_plain_dict(item) for item in self.history if isinstance(item, dict)]

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.relic_id,
            "hero_code": self.hero_code,
            "name": self.name,
            "state": self.state,
            "condition": self.condition,
            "location_node_id": self.location_node_id,
            "location_city_id": self.location_city_id,
            "owner_faction_id": self.owner_faction_id,
            "altar_id": self.altar_id,
            "maintenance_ether_cost": self.maintenance_ether_cost,
            "discovered_by_faction_ids": list(self.discovered_by_faction_ids),
            "last_changed_month": self.last_changed_month,
            "history": [dict(item) for item in self.history],
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> RelicState:
        return cls(
            relic_id=str(raw.get("id") or raw.get("relic_id") or ""),
            hero_code=str(raw.get("hero_code") or ""),
            name=str(raw.get("name") or ""),
            state=str(raw.get("state") or "scattered"),
            condition=str(raw.get("condition") or "intact"),
            location_node_id=(
                str(raw.get("location_node_id")) if raw.get("location_node_id") is not None else None
            ),
            location_city_id=(
                str(raw.get("location_city_id")) if raw.get("location_city_id") is not None else None
            ),
            owner_faction_id=(
                str(raw.get("owner_faction_id")) if raw.get("owner_faction_id") is not None else None
            ),
            altar_id=str(raw.get("altar_id")) if raw.get("altar_id") is not None else None,
            maintenance_ether_cost=int(raw.get("maintenance_ether_cost", 10)),
            discovered_by_faction_ids=_string_list(raw.get("discovered_by_faction_ids")),
            last_changed_month=int(raw.get("last_changed_month", 1)),
            history=[
                _plain_dict(item)
                for item in raw.get("history", [])
                if isinstance(item, dict)
            ],
        )


@dataclass(slots=True)
class RelicAltar:
    altar_id: str
    city_id: str
    name: str
    level: int = 1
    state: str = "dormant"
    capacity: int = 1
    bound_relic_ids: list[str] = field(default_factory=list)
    damaged_until_month: int | None = None
    action_month: int | None = None
    actions_used: int = 0
    consecration_faction_id: str | None = None
    consecration_relic_id: str | None = None
    consecration_progress: int = 0
    consecration_required: int = 3
    consecration_started_month: int | None = None
    consecration_last_month: int | None = None
    history: list[dict[str, Any]] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.level = max(1, int(self.level))
        self.capacity = max(1, int(self.capacity))
        self.bound_relic_ids = list(dict.fromkeys(self.bound_relic_ids))
        self.damaged_until_month = (
            max(1, int(self.damaged_until_month))
            if self.damaged_until_month is not None
            else None
        )
        self.action_month = max(1, int(self.action_month)) if self.action_month is not None else None
        self.actions_used = max(0, int(self.actions_used))
        self.consecration_progress = max(0, int(self.consecration_progress))
        self.consecration_required = max(1, int(self.consecration_required))
        self.consecration_started_month = (
            max(1, int(self.consecration_started_month))
            if self.consecration_started_month is not None
            else None
        )
        self.consecration_last_month = (
            max(1, int(self.consecration_last_month))
            if self.consecration_last_month is not None
            else None
        )
        self.history = [_plain_dict(item) for item in self.history if isinstance(item, dict)]

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.altar_id,
            "city_id": self.city_id,
            "name": self.name,
            "level": self.level,
            "state": self.state,
            "capacity": self.capacity,
            "bound_relic_ids": list(self.bound_relic_ids),
            "damaged_until_month": self.damaged_until_month,
            "action_month": self.action_month,
            "actions_used": self.actions_used,
            "consecration_faction_id": self.consecration_faction_id,
            "consecration_relic_id": self.consecration_relic_id,
            "consecration_progress": self.consecration_progress,
            "consecration_required": self.consecration_required,
            "consecration_started_month": self.consecration_started_month,
            "consecration_last_month": self.consecration_last_month,
            "history": [dict(item) for item in self.history],
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> RelicAltar:
        return cls(
            altar_id=str(raw.get("id") or raw.get("altar_id") or ""),
            city_id=str(raw.get("city_id") or ""),
            name=str(raw.get("name") or ""),
            level=int(raw.get("level", 1)),
            state=str(raw.get("state") or "dormant"),
            capacity=int(raw.get("capacity", 1)),
            bound_relic_ids=_string_list(raw.get("bound_relic_ids")),
            damaged_until_month=(
                int(raw["damaged_until_month"])
                if raw.get("damaged_until_month") is not None
                else None
            ),
            action_month=int(raw["action_month"]) if raw.get("action_month") is not None else None,
            actions_used=int(raw.get("actions_used", 0)),
            consecration_faction_id=(
                str(raw.get("consecration_faction_id"))
                if raw.get("consecration_faction_id") is not None
                else None
            ),
            consecration_relic_id=(
                str(raw.get("consecration_relic_id"))
                if raw.get("consecration_relic_id") is not None
                else None
            ),
            consecration_progress=int(raw.get("consecration_progress", 0)),
            consecration_required=int(raw.get("consecration_required", 3)),
            consecration_started_month=(
                int(raw["consecration_started_month"])
                if raw.get("consecration_started_month") is not None
                else None
            ),
            consecration_last_month=(
                int(raw["consecration_last_month"])
                if raw.get("consecration_last_month") is not None
                else None
            ),
            history=[
                _plain_dict(item)
                for item in raw.get("history", [])
                if isinstance(item, dict)
            ],
        )


@dataclass(slots=True)
class HeroRecruitment:
    recruitment_id: str
    faction_id: str
    city_id: str
    issuer_office_id: str
    issued_month: int
    status: str = "open"
    candidate_hero_codes: list[str] = field(default_factory=list)
    accepted_hero_code: str | None = None
    recommended_hero_code: str | None = None
    recommended_by_office_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.recruitment_id,
            "faction_id": self.faction_id,
            "city_id": self.city_id,
            "issuer_office_id": self.issuer_office_id,
            "issued_month": self.issued_month,
            "status": self.status,
            "candidate_hero_codes": list(self.candidate_hero_codes),
            "accepted_hero_code": self.accepted_hero_code,
            "recommended_hero_code": self.recommended_hero_code,
            "recommended_by_office_id": self.recommended_by_office_id,
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> HeroRecruitment:
        return cls(
            recruitment_id=str(raw.get("id") or raw.get("recruitment_id") or ""),
            faction_id=str(raw.get("faction_id") or ""),
            city_id=str(raw.get("city_id") or ""),
            issuer_office_id=str(raw.get("issuer_office_id") or ""),
            issued_month=int(raw.get("issued_month", 1)),
            status=str(raw.get("status") or "open"),
            candidate_hero_codes=_string_list(raw.get("candidate_hero_codes")),
            accepted_hero_code=(
                str(raw.get("accepted_hero_code")) if raw.get("accepted_hero_code") is not None else None
            ),
            recommended_hero_code=(
                str(raw.get("recommended_hero_code")) if raw.get("recommended_hero_code") is not None else None
            ),
            recommended_by_office_id=(
                str(raw.get("recommended_by_office_id"))
                if raw.get("recommended_by_office_id") is not None
                else None
            ),
        )


@dataclass(slots=True)
class StrategicArmy:
    army_id: str
    faction_id: str
    commander_office_id: str
    commander_hero_code: str
    location_node_id: str
    home_city_id: str
    name: str = ""
    army_kind: str = "conventional"
    unit_inventory: dict[str, int] = field(default_factory=dict)
    manpower: int = 0
    supply: int = 0
    supply_capacity: int = 0
    morale: int = 70
    status: str = "garrisoned"
    current_order: str = "hold"
    created_month: int = 1
    march_origin_node_id: str | None = None
    destination_node_id: str | None = None
    route_node_ids: list[str] = field(default_factory=list)
    route_progress_index: int = 0
    departure_month: int | None = None
    estimated_arrival_month: int | None = None
    supply_source_city_id: str | None = None
    supply_line_node_ids: list[str] = field(default_factory=list)
    supply_line_status: str = "unassessed"
    supply_distance: int | None = None
    monthly_supply_need: int = 0
    last_supply_consumed: int = 0
    last_supply_received: int = 0
    starvation_months: int = 0
    target_army_id: str | None = None
    target_encounter_id: str | None = None
    retreat_destination_node_id: str | None = None
    last_cold_route_key: str | None = None
    last_cold_exposure_month: int | None = None
    last_cold_supply_loss: int = 0
    last_cold_morale_loss: int = 0

    def __post_init__(self) -> None:
        self.name = str(self.name or self.army_id)
        self.army_kind = str(self.army_kind or "conventional")
        self.unit_inventory = {
            str(unit_type): max(0, int(count))
            for unit_type, count in self.unit_inventory.items()
            if str(unit_type) and int(count) > 0
        }
        self.manpower = max(0, int(self.manpower))
        self.supply_capacity = max(0, int(self.supply_capacity))
        self.supply = max(0, min(int(self.supply), self.supply_capacity))
        self.morale = max(0, min(100, int(self.morale)))
        self.created_month = max(1, int(self.created_month))
        self.march_origin_node_id = str(self.march_origin_node_id) if self.march_origin_node_id else None
        self.destination_node_id = str(self.destination_node_id) if self.destination_node_id else None
        self.route_node_ids = [str(node_id) for node_id in self.route_node_ids if str(node_id)]
        self.route_progress_index = max(0, int(self.route_progress_index))
        self.departure_month = max(1, int(self.departure_month)) if self.departure_month is not None else None
        self.estimated_arrival_month = (
            max(1, int(self.estimated_arrival_month))
            if self.estimated_arrival_month is not None
            else None
        )
        self.supply_source_city_id = str(self.supply_source_city_id) if self.supply_source_city_id else None
        self.supply_line_node_ids = [str(node_id) for node_id in self.supply_line_node_ids if str(node_id)]
        self.supply_line_status = str(self.supply_line_status or "unassessed")
        self.supply_distance = max(0, int(self.supply_distance)) if self.supply_distance is not None else None
        self.monthly_supply_need = max(0, int(self.monthly_supply_need))
        self.last_supply_consumed = max(0, int(self.last_supply_consumed))
        self.last_supply_received = max(0, int(self.last_supply_received))
        self.starvation_months = max(0, int(self.starvation_months))
        self.target_army_id = str(self.target_army_id) if self.target_army_id else None
        self.target_encounter_id = str(self.target_encounter_id) if self.target_encounter_id else None
        self.retreat_destination_node_id = (
            str(self.retreat_destination_node_id) if self.retreat_destination_node_id else None
        )
        self.last_cold_route_key = str(self.last_cold_route_key) if self.last_cold_route_key else None
        self.last_cold_exposure_month = (
            max(1, int(self.last_cold_exposure_month))
            if self.last_cold_exposure_month is not None
            else None
        )
        self.last_cold_supply_loss = max(0, int(self.last_cold_supply_loss))
        self.last_cold_morale_loss = max(0, int(self.last_cold_morale_loss))

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.army_id,
            "faction_id": self.faction_id,
            "commander_office_id": self.commander_office_id,
            "commander_hero_code": self.commander_hero_code,
            "location_node_id": self.location_node_id,
            "home_city_id": self.home_city_id,
            "name": self.name,
            "army_kind": self.army_kind,
            "unit_inventory": dict(self.unit_inventory),
            "manpower": self.manpower,
            "supply": self.supply,
            "supply_capacity": self.supply_capacity,
            "morale": self.morale,
            "status": self.status,
            "current_order": self.current_order,
            "created_month": self.created_month,
            "march_origin_node_id": self.march_origin_node_id,
            "destination_node_id": self.destination_node_id,
            "route_node_ids": list(self.route_node_ids),
            "route_progress_index": self.route_progress_index,
            "departure_month": self.departure_month,
            "estimated_arrival_month": self.estimated_arrival_month,
            "supply_source_city_id": self.supply_source_city_id,
            "supply_line_node_ids": list(self.supply_line_node_ids),
            "supply_line_status": self.supply_line_status,
            "supply_distance": self.supply_distance,
            "monthly_supply_need": self.monthly_supply_need,
            "last_supply_consumed": self.last_supply_consumed,
            "last_supply_received": self.last_supply_received,
            "starvation_months": self.starvation_months,
            "target_army_id": self.target_army_id,
            "target_encounter_id": self.target_encounter_id,
            "retreat_destination_node_id": self.retreat_destination_node_id,
            "last_cold_route_key": self.last_cold_route_key,
            "last_cold_exposure_month": self.last_cold_exposure_month,
            "last_cold_supply_loss": self.last_cold_supply_loss,
            "last_cold_morale_loss": self.last_cold_morale_loss,
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> StrategicArmy:
        return cls(
            army_id=str(raw.get("id") or raw.get("army_id") or ""),
            faction_id=str(raw.get("faction_id") or ""),
            commander_office_id=str(raw.get("commander_office_id") or ""),
            commander_hero_code=str(raw.get("commander_hero_code") or ""),
            location_node_id=str(raw.get("location_node_id") or ""),
            home_city_id=str(raw.get("home_city_id") or ""),
            name=str(raw.get("name") or raw.get("id") or raw.get("army_id") or ""),
            army_kind=str(raw.get("army_kind") or "conventional"),
            unit_inventory=_int_dict(raw.get("unit_inventory")),
            manpower=int(raw.get("manpower", 0)),
            supply=int(raw.get("supply", 0)),
            supply_capacity=int(raw.get("supply_capacity", 0)),
            morale=int(raw.get("morale", 70)),
            status=str(raw.get("status") or "garrisoned"),
            current_order=str(raw.get("current_order") or "hold"),
            created_month=int(raw.get("created_month", 1)),
            march_origin_node_id=(
                str(raw.get("march_origin_node_id")) if raw.get("march_origin_node_id") else None
            ),
            destination_node_id=(
                str(raw.get("destination_node_id")) if raw.get("destination_node_id") else None
            ),
            route_node_ids=_string_list(raw.get("route_node_ids")),
            route_progress_index=int(raw.get("route_progress_index", 0)),
            departure_month=(int(raw["departure_month"]) if raw.get("departure_month") is not None else None),
            estimated_arrival_month=(
                int(raw["estimated_arrival_month"])
                if raw.get("estimated_arrival_month") is not None
                else None
            ),
            supply_source_city_id=(
                str(raw.get("supply_source_city_id")) if raw.get("supply_source_city_id") else None
            ),
            supply_line_node_ids=_string_list(raw.get("supply_line_node_ids")),
            supply_line_status=str(raw.get("supply_line_status") or "unassessed"),
            supply_distance=(int(raw["supply_distance"]) if raw.get("supply_distance") is not None else None),
            monthly_supply_need=int(raw.get("monthly_supply_need", 0)),
            last_supply_consumed=int(raw.get("last_supply_consumed", 0)),
            last_supply_received=int(raw.get("last_supply_received", 0)),
            starvation_months=int(raw.get("starvation_months", 0)),
            target_army_id=(str(raw.get("target_army_id")) if raw.get("target_army_id") else None),
            target_encounter_id=(
                str(raw.get("target_encounter_id")) if raw.get("target_encounter_id") else None
            ),
            retreat_destination_node_id=(
                str(raw.get("retreat_destination_node_id"))
                if raw.get("retreat_destination_node_id")
                else None
            ),
            last_cold_route_key=(
                str(raw.get("last_cold_route_key"))
                if raw.get("last_cold_route_key")
                else None
            ),
            last_cold_exposure_month=(
                int(raw["last_cold_exposure_month"])
                if raw.get("last_cold_exposure_month") is not None
                else None
            ),
            last_cold_supply_loss=int(raw.get("last_cold_supply_loss", 0)),
            last_cold_morale_loss=int(raw.get("last_cold_morale_loss", 0)),
        )


@dataclass(slots=True)
class StrategicEncounter:
    encounter_id: str
    node_id: str
    faction_army_ids: dict[str, list[str]] = field(default_factory=dict)
    opened_month: int = 1
    status: str = "active"
    ended_month: int | None = None
    outcome: str | None = None

    def __post_init__(self) -> None:
        self.faction_army_ids = {
            str(faction_id): [str(army_id) for army_id in army_ids if str(army_id)]
            for faction_id, army_ids in self.faction_army_ids.items()
            if str(faction_id) and isinstance(army_ids, list) and army_ids
        }
        self.opened_month = max(1, int(self.opened_month))
        self.ended_month = max(1, int(self.ended_month)) if self.ended_month is not None else None
        self.outcome = str(self.outcome) if self.outcome else None

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.encounter_id,
            "node_id": self.node_id,
            "faction_army_ids": {
                faction_id: list(army_ids)
                for faction_id, army_ids in self.faction_army_ids.items()
            },
            "opened_month": self.opened_month,
            "status": self.status,
            "ended_month": self.ended_month,
            "outcome": self.outcome,
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> StrategicEncounter:
        raw_sides = raw.get("faction_army_ids")
        sides = raw_sides if isinstance(raw_sides, dict) else {}
        return cls(
            encounter_id=str(raw.get("id") or raw.get("encounter_id") or ""),
            node_id=str(raw.get("node_id") or ""),
            faction_army_ids={
                str(faction_id): _string_list(army_ids)
                for faction_id, army_ids in sides.items()
            },
            opened_month=int(raw.get("opened_month", 1)),
            status=str(raw.get("status") or "active"),
            ended_month=(int(raw["ended_month"]) if raw.get("ended_month") is not None else None),
            outcome=(str(raw.get("outcome")) if raw.get("outcome") else None),
        )


@dataclass(slots=True)
class StrategicSiege:
    siege_id: str
    city_id: str
    node_id: str
    attacker_faction_id: str
    defender_faction_id: str
    attacker_army_ids: list[str] = field(default_factory=list)
    started_month: int = 1
    status: str = "active"
    fortification_initial: int = 20
    fortification_remaining: int = 20
    attacker_stance: str = "blockade"
    defender_stance: str = "hold"
    last_city_food_consumed: int = 0
    last_garrison_lost: int = 0
    last_fortification_damage: int = 0
    battle_trigger: str | None = None
    ended_month: int | None = None
    outcome: str | None = None

    def __post_init__(self) -> None:
        self.attacker_army_ids = [str(army_id) for army_id in self.attacker_army_ids if str(army_id)]
        self.started_month = max(1, int(self.started_month))
        self.fortification_initial = max(1, int(self.fortification_initial))
        self.fortification_remaining = max(0, min(int(self.fortification_remaining), self.fortification_initial))
        self.last_city_food_consumed = max(0, int(self.last_city_food_consumed))
        self.last_garrison_lost = max(0, int(self.last_garrison_lost))
        self.last_fortification_damage = max(0, int(self.last_fortification_damage))
        self.battle_trigger = str(self.battle_trigger) if self.battle_trigger else None
        self.ended_month = max(1, int(self.ended_month)) if self.ended_month is not None else None
        self.outcome = str(self.outcome) if self.outcome else None

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.siege_id,
            "city_id": self.city_id,
            "node_id": self.node_id,
            "attacker_faction_id": self.attacker_faction_id,
            "defender_faction_id": self.defender_faction_id,
            "attacker_army_ids": list(self.attacker_army_ids),
            "started_month": self.started_month,
            "status": self.status,
            "fortification_initial": self.fortification_initial,
            "fortification_remaining": self.fortification_remaining,
            "attacker_stance": self.attacker_stance,
            "defender_stance": self.defender_stance,
            "last_city_food_consumed": self.last_city_food_consumed,
            "last_garrison_lost": self.last_garrison_lost,
            "last_fortification_damage": self.last_fortification_damage,
            "battle_trigger": self.battle_trigger,
            "ended_month": self.ended_month,
            "outcome": self.outcome,
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> StrategicSiege:
        return cls(
            siege_id=str(raw.get("id") or raw.get("siege_id") or ""),
            city_id=str(raw.get("city_id") or ""),
            node_id=str(raw.get("node_id") or ""),
            attacker_faction_id=str(raw.get("attacker_faction_id") or ""),
            defender_faction_id=str(raw.get("defender_faction_id") or ""),
            attacker_army_ids=_string_list(raw.get("attacker_army_ids")),
            started_month=int(raw.get("started_month", 1)),
            status=str(raw.get("status") or "active"),
            fortification_initial=int(raw.get("fortification_initial", 20)),
            fortification_remaining=int(raw.get("fortification_remaining", 20)),
            attacker_stance=str(raw.get("attacker_stance") or "blockade"),
            defender_stance=str(raw.get("defender_stance") or "hold"),
            last_city_food_consumed=int(raw.get("last_city_food_consumed", 0)),
            last_garrison_lost=int(raw.get("last_garrison_lost", 0)),
            last_fortification_damage=int(raw.get("last_fortification_damage", 0)),
            battle_trigger=(str(raw.get("battle_trigger")) if raw.get("battle_trigger") else None),
            ended_month=(int(raw["ended_month"]) if raw.get("ended_month") is not None else None),
            outcome=(str(raw.get("outcome")) if raw.get("outcome") else None),
        )


@dataclass(slots=True)
class Office:
    office_id: str
    faction_id: str
    office_type: str
    holder_id: str | None = None
    holder_type: str | None = None
    controller_type: str = "ai"
    controller_user_id: int | None = None
    parent_office_id: str | None = None
    subordinate_office_ids: list[str] = field(default_factory=list)
    managed_entity_ids: list[str] = field(default_factory=list)
    permissions: list[str] = field(default_factory=list)
    duties: list[str] = field(default_factory=list)
    unit_inventory: dict[str, int] = field(default_factory=dict)
    status: str = "active"

    def __post_init__(self) -> None:
        self.unit_inventory = {
            str(unit_type): max(0, int(count))
            for unit_type, count in self.unit_inventory.items()
            if str(unit_type) and int(count) > 0
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.office_id,
            "faction_id": self.faction_id,
            "office_type": self.office_type,
            "holder_id": self.holder_id,
            "holder_type": self.holder_type,
            "controller_type": self.controller_type,
            "controller_user_id": self.controller_user_id,
            "parent_office_id": self.parent_office_id,
            "subordinate_office_ids": list(self.subordinate_office_ids),
            "managed_entity_ids": list(self.managed_entity_ids),
            "permissions": list(self.permissions),
            "duties": list(self.duties),
            "unit_inventory": dict(self.unit_inventory),
            "status": self.status,
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> Office:
        controller_user_id = raw.get("controller_user_id")
        return cls(
            office_id=str(raw.get("id") or raw.get("office_id") or ""),
            faction_id=str(raw.get("faction_id") or ""),
            office_type=str(raw.get("office_type") or ""),
            holder_id=str(raw.get("holder_id")) if raw.get("holder_id") is not None else None,
            holder_type=str(raw.get("holder_type")) if raw.get("holder_type") is not None else None,
            controller_type=str(raw.get("controller_type") or "ai"),
            controller_user_id=int(controller_user_id) if controller_user_id is not None else None,
            parent_office_id=str(raw.get("parent_office_id")) if raw.get("parent_office_id") is not None else None,
            subordinate_office_ids=_string_list(raw.get("subordinate_office_ids")),
            managed_entity_ids=_string_list(raw.get("managed_entity_ids")),
            permissions=_string_list(raw.get("permissions")),
            duties=_string_list(raw.get("duties")),
            unit_inventory=_int_dict(raw.get("unit_inventory")),
            status=str(raw.get("status") or "active"),
        )


@dataclass(slots=True)
class OfficeDuty:
    duty_id: str
    office_id: str
    duty_type: str
    related_entity_id: str | None = None
    priority: int = 1
    due_month: int | None = None
    status: str = "pending"

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.duty_id,
            "office_id": self.office_id,
            "duty_type": self.duty_type,
            "related_entity_id": self.related_entity_id,
            "priority": self.priority,
            "due_month": self.due_month,
            "status": self.status,
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> OfficeDuty:
        due_month = raw.get("due_month")
        return cls(
            duty_id=str(raw.get("id") or raw.get("duty_id") or ""),
            office_id=str(raw.get("office_id") or ""),
            duty_type=str(raw.get("duty_type") or ""),
            related_entity_id=str(raw.get("related_entity_id")) if raw.get("related_entity_id") is not None else None,
            priority=int(raw.get("priority", 1)),
            due_month=int(due_month) if due_month is not None else None,
            status=str(raw.get("status") or "pending"),
        )


@dataclass(slots=True)
class OfficeOrder:
    order_id: str
    issuer_office_id: str
    receiver_office_id: str
    order_type: str
    objective: str
    issued_month: int
    target_entity_id: str | None = None
    priority: int = 1
    deadline_month: int | None = None
    status: str = "pending"
    details: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.order_id,
            "issuer_office_id": self.issuer_office_id,
            "receiver_office_id": self.receiver_office_id,
            "order_type": self.order_type,
            "target_entity_id": self.target_entity_id,
            "objective": self.objective,
            "priority": self.priority,
            "issued_month": self.issued_month,
            "deadline_month": self.deadline_month,
            "status": self.status,
            "details": dict(self.details),
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> OfficeOrder:
        deadline_month = raw.get("deadline_month")
        return cls(
            order_id=str(raw.get("id") or raw.get("order_id") or ""),
            issuer_office_id=str(raw.get("issuer_office_id") or ""),
            receiver_office_id=str(raw.get("receiver_office_id") or ""),
            order_type=str(raw.get("order_type") or "order"),
            target_entity_id=str(raw.get("target_entity_id")) if raw.get("target_entity_id") is not None else None,
            objective=str(raw.get("objective") or ""),
            priority=int(raw.get("priority", 1)),
            issued_month=int(raw.get("issued_month", 1)),
            deadline_month=int(deadline_month) if deadline_month is not None else None,
            status=str(raw.get("status") or "pending"),
            details=_plain_dict(raw.get("details")),
        )


@dataclass(slots=True)
class OfficeTakeover:
    superior_office_id: str
    vacant_office_id: str
    start_month: int
    management_penalty: float = 0.25

    def to_dict(self) -> dict[str, Any]:
        return {
            "superior_office_id": self.superior_office_id,
            "vacant_office_id": self.vacant_office_id,
            "start_month": self.start_month,
            "management_penalty": self.management_penalty,
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> OfficeTakeover:
        return cls(
            superior_office_id=str(raw.get("superior_office_id") or ""),
            vacant_office_id=str(raw.get("vacant_office_id") or ""),
            start_month=int(raw.get("start_month", 1)),
            management_penalty=float(raw.get("management_penalty", 0.25)),
        )


@dataclass(slots=True)
class WorldCrisis:
    crisis_id: str
    crisis_type: str
    status: str
    stage: str
    stage_started_month: int
    next_stage_month: int | None
    pressure: int
    origin_node_id: str
    frontier_node_ids: list[str] = field(default_factory=list)
    affected_route_keys: list[str] = field(default_factory=list)
    threatened_city_ids: list[str] = field(default_factory=list)
    spawned_army_ids: list[str] = field(default_factory=list)
    contributions_by_faction: dict[str, int] = field(default_factory=dict)
    cooperation_targets_by_faction: dict[str, str] = field(default_factory=dict)
    cooperation_pairs: list[str] = field(default_factory=list)
    broken_cooperation_pairs: list[str] = field(default_factory=list)
    decisions: list[dict[str, Any]] = field(default_factory=list)
    showdown_branch: str | None = None
    showdown_battle_id: str | None = None
    showdown_leader_faction_id: str | None = None
    showdown_outcome: str | None = None
    mainline_winner_faction_ids: list[str] = field(default_factory=list)
    aftermath: dict[str, Any] = field(default_factory=dict)
    started_month: int | None = None
    history: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.crisis_id,
            "crisis_type": self.crisis_type,
            "status": self.status,
            "stage": self.stage,
            "stage_started_month": self.stage_started_month,
            "next_stage_month": self.next_stage_month,
            "pressure": self.pressure,
            "origin_node_id": self.origin_node_id,
            "frontier_node_ids": list(self.frontier_node_ids),
            "affected_route_keys": list(self.affected_route_keys),
            "threatened_city_ids": list(self.threatened_city_ids),
            "spawned_army_ids": list(self.spawned_army_ids),
            "contributions_by_faction": dict(self.contributions_by_faction),
            "cooperation_targets_by_faction": dict(self.cooperation_targets_by_faction),
            "cooperation_pairs": list(self.cooperation_pairs),
            "broken_cooperation_pairs": list(self.broken_cooperation_pairs),
            "decisions": [dict(item) for item in self.decisions],
            "showdown_branch": self.showdown_branch,
            "showdown_battle_id": self.showdown_battle_id,
            "showdown_leader_faction_id": self.showdown_leader_faction_id,
            "showdown_outcome": self.showdown_outcome,
            "mainline_winner_faction_ids": list(self.mainline_winner_faction_ids),
            "aftermath": dict(self.aftermath),
            "started_month": self.started_month,
            "history": [dict(item) for item in self.history],
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> WorldCrisis:
        return cls(
            crisis_id=str(raw.get("id") or raw.get("crisis_id") or ""),
            crisis_type=str(raw.get("crisis_type") or ""),
            status=str(raw.get("status") or "dormant"),
            stage=str(raw.get("stage") or "dormant"),
            stage_started_month=max(1, int(raw.get("stage_started_month", 1))),
            next_stage_month=(
                int(raw["next_stage_month"])
                if raw.get("next_stage_month") is not None
                else None
            ),
            pressure=max(0, int(raw.get("pressure", 0))),
            origin_node_id=str(raw.get("origin_node_id") or ""),
            frontier_node_ids=_string_list(raw.get("frontier_node_ids")),
            affected_route_keys=_string_list(raw.get("affected_route_keys")),
            threatened_city_ids=_string_list(raw.get("threatened_city_ids")),
            spawned_army_ids=_string_list(raw.get("spawned_army_ids")),
            contributions_by_faction={
                faction_id: max(0, contribution)
                for faction_id, contribution in _int_dict(raw.get("contributions_by_faction")).items()
            },
            cooperation_targets_by_faction=_string_dict(raw.get("cooperation_targets_by_faction")),
            cooperation_pairs=_string_list(raw.get("cooperation_pairs")),
            broken_cooperation_pairs=_string_list(raw.get("broken_cooperation_pairs")),
            decisions=[
                _plain_dict(item)
                for item in raw.get("decisions", [])
                if isinstance(item, dict)
            ],
            showdown_branch=(str(raw.get("showdown_branch")) if raw.get("showdown_branch") else None),
            showdown_battle_id=(
                str(raw.get("showdown_battle_id")) if raw.get("showdown_battle_id") else None
            ),
            showdown_leader_faction_id=(
                str(raw.get("showdown_leader_faction_id"))
                if raw.get("showdown_leader_faction_id")
                else None
            ),
            showdown_outcome=(
                str(raw.get("showdown_outcome")) if raw.get("showdown_outcome") else None
            ),
            mainline_winner_faction_ids=_string_list(raw.get("mainline_winner_faction_ids")),
            aftermath=_plain_dict(raw.get("aftermath")),
            started_month=(
                int(raw["started_month"])
                if raw.get("started_month") is not None
                else None
            ),
            history=[
                _plain_dict(item)
                for item in raw.get("history", [])
                if isinstance(item, dict)
            ],
        )


@dataclass(slots=True)
class WorldState:
    seed: int
    current_month: int
    nodes: list[MapNode]
    cities: list[City]
    factions: list[Faction]
    event_log: list[EventLogEntry] = field(default_factory=list)
    memory_tags: list[str] = field(default_factory=list)
    pending_battles: list[PendingBattle] = field(default_factory=list)
    story_events: list[StoryEvent] = field(default_factory=list)
    scheduled_consequences: list[ScheduledConsequence] = field(default_factory=list)
    strategic_heroes: list[StrategicHeroState] = field(default_factory=list)
    relics: list[RelicState] = field(default_factory=list)
    relic_altars: list[RelicAltar] = field(default_factory=list)
    hero_recruitments: list[HeroRecruitment] = field(default_factory=list)
    offices: list[Office] = field(default_factory=list)
    office_duties: list[OfficeDuty] = field(default_factory=list)
    office_orders: list[OfficeOrder] = field(default_factory=list)
    office_takeovers: list[OfficeTakeover] = field(default_factory=list)
    campaign_contract: dict[str, Any] = field(default_factory=dict)
    campaign_conclusion: dict[str, Any] = field(default_factory=dict)
    monthly_reports: list[dict[str, Any]] = field(default_factory=list)
    campaign_tutorial: dict[str, Any] = field(default_factory=dict)
    ai_strategic_goals: dict[str, Any] = field(default_factory=dict)
    diplomatic_agreements: list[DiplomaticAgreement] = field(default_factory=list)
    diplomatic_cooldowns: dict[str, int] = field(default_factory=dict)
    diplomatic_memory: list[dict[str, Any]] = field(default_factory=list)
    armies: list[StrategicArmy] = field(default_factory=list)
    encounters: list[StrategicEncounter] = field(default_factory=list)
    sieges: list[StrategicSiege] = field(default_factory=list)
    world_crises: list[WorldCrisis] = field(default_factory=list)
    save_format_version: int = CURRENT_STRATEGY_SAVE_VERSION

    def __post_init__(self) -> None:
        self.seed = int(self.seed)
        self.current_month = int(self.current_month)
        self.save_format_version = int(self.save_format_version)
        if self.current_month <= 0:
            raise StrategyError("战略月份必须为正数。")
        if self.save_format_version != CURRENT_STRATEGY_SAVE_VERSION:
            raise StrategyError(
                f"战略存档版本必须为当前版本 {CURRENT_STRATEGY_SAVE_VERSION}。"
            )
        self.validate()

    def validate(self) -> None:
        node_ids = {node.node_id for node in self.nodes}
        city_ids = {city.city_id for city in self.cities}
        faction_ids = {faction.faction_id for faction in self.factions}
        battle_ids = {battle.battle_id for battle in self.pending_battles}
        story_event_ids = {event.event_id for event in self.story_events}
        consequence_ids = {item.consequence_id for item in self.scheduled_consequences}
        office_ids = {office.office_id for office in self.offices}
        duty_ids = {duty.duty_id for duty in self.office_duties}
        order_ids = {order.order_id for order in self.office_orders}
        hero_codes = {hero.hero_code for hero in self.strategic_heroes}
        relic_ids = {relic.relic_id for relic in self.relics}
        relic_hero_codes = {relic.hero_code for relic in self.relics}
        altar_ids = {altar.altar_id for altar in self.relic_altars}
        recruitment_ids = {item.recruitment_id for item in self.hero_recruitments}
        agreement_ids = {item.agreement_id for item in self.diplomatic_agreements}
        army_ids = {item.army_id for item in self.armies}
        encounter_ids = {item.encounter_id for item in self.encounters}
        siege_ids = {item.siege_id for item in self.sieges}
        crisis_ids = {item.crisis_id for item in self.world_crises}
        if len(node_ids) != len(self.nodes):
            raise StrategyError("地图节点 ID 不能重复。")
        if len(city_ids) != len(self.cities):
            raise StrategyError("城市 ID 不能重复。")
        if len(faction_ids) != len(self.factions):
            raise StrategyError("势力 ID 不能重复。")
        if len(battle_ids) != len(self.pending_battles):
            raise StrategyError("战略战斗 ID 不能重复。")
        if len(story_event_ids) != len(self.story_events):
            raise StrategyError("战略事件 ID 不能重复。")
        if len(consequence_ids) != len(self.scheduled_consequences):
            raise StrategyError("延迟后果 ID 不能重复。")
        if len(office_ids) != len(self.offices):
            raise StrategyError("职位 ID 不能重复。")
        if len(duty_ids) != len(self.office_duties):
            raise StrategyError("职位职责 ID 不能重复。")
        if len(order_ids) != len(self.office_orders):
            raise StrategyError("职位命令 ID 不能重复。")
        if len(hero_codes) != len(self.strategic_heroes):
            raise StrategyError("战略武将不能重复。")
        if len(relic_ids) != len(self.relics) or len(relic_hero_codes) != len(self.relics):
            raise StrategyError("圣物 ID 与对应英灵必须唯一。")
        if len(altar_ids) != len(self.relic_altars):
            raise StrategyError("圣物祭坛 ID 不能重复。")
        player_controller_ids = [
            int(hero.controller_user_id)
            for hero in self.strategic_heroes
            if hero.controller_type == "player" and hero.controller_user_id is not None
        ]
        if len(player_controller_ids) != len(set(player_controller_ids)):
            raise StrategyError("同一玩家在一个战役中只能控制一名武将。")
        if len(recruitment_ids) != len(self.hero_recruitments):
            raise StrategyError("武将招募令 ID 不能重复。")
        if len(agreement_ids) != len(self.diplomatic_agreements):
            raise StrategyError("外交协议 ID 不能重复。")
        if len(army_ids) != len(self.armies):
            raise StrategyError("战略军队 ID 不能重复。")
        if len(encounter_ids) != len(self.encounters):
            raise StrategyError("战略遭遇 ID 不能重复。")
        if len(siege_ids) != len(self.sieges):
            raise StrategyError("战略围城 ID 不能重复。")
        if len(crisis_ids) != len(self.world_crises):
            raise StrategyError("世界危机 ID 不能重复。")
        for node in self.nodes:
            unknown = [target_id for target_id in node.connected_node_ids if target_id not in node_ids]
            if unknown:
                raise StrategyError(f"节点 {node.node_id} 连接了不存在的节点。")
        for city in self.cities:
            if city.node_id not in node_ids:
                raise StrategyError(f"城市 {city.city_id} 绑定了不存在的节点。")
            if city.owner_faction_id not in faction_ids:
                raise StrategyError(f"城市 {city.city_id} 归属了不存在的势力。")
            for faction_id in city.support_by_faction:
                if faction_id not in faction_ids and faction_id not in set(city.local_factions):
                    raise StrategyError(f"城市 {city.city_id} 记录了不存在势力的支持度。")
            if any(altar_id not in altar_ids for altar_id in city.altars):
                raise StrategyError(f"城市 {city.city_id} 记录了不存在的圣物祭坛。")
        for faction in self.factions:
            if faction.faction_type not in {"major", "neutral_city_state", "world_crisis"}:
                raise StrategyError(f"势力 {faction.faction_id} 类型无效。")
            if faction.capital_city_id is not None and faction.capital_city_id not in city_ids:
                raise StrategyError(f"势力 {faction.faction_id} 的主城不存在。")
            if faction.incited_against_faction_id is not None and faction.incited_against_faction_id not in faction_ids:
                raise StrategyError(f"势力 {faction.faction_id} 的教唆目标不存在。")
            if faction.incited_by_faction_id is not None and faction.incited_by_faction_id not in faction_ids:
                raise StrategyError(f"势力 {faction.faction_id} 的教唆来源不存在。")
            if any(target_id not in faction_ids for target_id in faction.relations):
                raise StrategyError(f"势力 {faction.faction_id} 记录了不存在势力的关系。")
            if any(target_id not in faction_ids for target_id in faction.influence_by_faction):
                raise StrategyError(f"势力 {faction.faction_id} 记录了不存在势力的影响力。")
        for agreement in self.diplomatic_agreements:
            major = next((item for item in self.factions if item.faction_id == agreement.major_faction_id), None)
            neutral = next((item for item in self.factions if item.faction_id == agreement.neutral_faction_id), None)
            if major is None or not major.is_major or neutral is None or not neutral.is_neutral_city_state:
                raise StrategyError(f"外交协议 {agreement.agreement_id} 的签署势力无效。")
            if agreement.agreement_type not in {"protection", "non_aggression"}:
                raise StrategyError(f"外交协议 {agreement.agreement_id} 类型无效。")
            if agreement.status not in {"active", "ended", "broken"}:
                raise StrategyError(f"外交协议 {agreement.agreement_id} 状态无效。")
            if agreement.expires_month is not None and agreement.expires_month <= agreement.started_month:
                raise StrategyError(f"外交协议 {agreement.agreement_id} 的期限无效。")
        active_commanders: set[str] = set()
        for army in self.armies:
            if army.faction_id not in faction_ids:
                raise StrategyError(f"军队 {army.army_id} 所属势力不存在。")
            if army.commander_office_id not in office_ids:
                raise StrategyError(f"军队 {army.army_id} 指挥职位不存在。")
            commander = next(office for office in self.offices if office.office_id == army.commander_office_id)
            if commander.faction_id != army.faction_id or commander.office_type != "general":
                raise StrategyError(f"军队 {army.army_id} 必须由本势力将军指挥。")
            if army.location_node_id not in node_ids or army.home_city_id not in city_ids:
                raise StrategyError(f"军队 {army.army_id} 的位置或驻地不存在。")
            if army.status not in {"garrisoned", "deployed", "marching", "engaged", "besieging", "retreating", "disbanded", "destroyed"}:
                raise StrategyError(f"军队 {army.army_id} 状态无效。")
            if army.army_kind not in {"conventional", "snow_ghost"}:
                raise StrategyError(f"军队 {army.army_id} 类型无效。")
            if army.current_order not in {"hold", "march", "intercept", "reinforce", "retreat", "besiege"}:
                raise StrategyError(f"军队 {army.army_id} 命令无效。")
            if army.status not in {"disbanded", "destroyed"}:
                if army.commander_office_id in active_commanders:
                    raise StrategyError("同一将军只能指挥一支现役军队。")
                active_commanders.add(army.commander_office_id)
                if not army.unit_inventory or army.manpower <= 0:
                    raise StrategyError(f"现役军队 {army.army_id} 必须拥有兵员。")
            if any(unit_type not in {"infantry", "archer", "cavalry", "snow_ghost"} for unit_type in army.unit_inventory):
                raise StrategyError(f"军队 {army.army_id} 包含不存在的注册兵种。")
            if army.supply_source_city_id is not None and army.supply_source_city_id not in city_ids:
                raise StrategyError(f"军队 {army.army_id} 的补给城市不存在。")
            if army.supply_line_status not in {"unassessed", "local", "open", "strained", "severed", "none"}:
                raise StrategyError(f"军队 {army.army_id} 的补给线状态无效。")
            if any(node_id not in node_ids for node_id in army.supply_line_node_ids):
                raise StrategyError(f"军队 {army.army_id} 的补给线包含不存在的节点。")
            if army.supply_line_node_ids:
                if army.supply_line_node_ids[0] != army.location_node_id:
                    raise StrategyError(f"军队 {army.army_id} 的补给线起点与当前位置不一致。")
                if army.supply_distance != len(army.supply_line_node_ids) - 1:
                    raise StrategyError(f"军队 {army.army_id} 的补给距离与路线不一致。")
                source_city = next(
                    (city for city in self.cities if city.city_id == army.supply_source_city_id),
                    None,
                )
                if source_city is None or source_city.node_id != army.supply_line_node_ids[-1]:
                    raise StrategyError(f"军队 {army.army_id} 的补给来源与路线不一致。")
                for source_node_id, target_node_id in zip(
                    army.supply_line_node_ids,
                    army.supply_line_node_ids[1:],
                ):
                    source_node = next(node for node in self.nodes if node.node_id == source_node_id)
                    if target_node_id not in source_node.connected_node_ids:
                        raise StrategyError(f"军队 {army.army_id} 的补给线包含未连接节点。")
            if any(node_id not in node_ids for node_id in army.route_node_ids):
                raise StrategyError(f"军队 {army.army_id} 的行军路线包含不存在的节点。")
            if army.route_node_ids:
                if army.route_progress_index >= len(army.route_node_ids):
                    raise StrategyError(f"军队 {army.army_id} 的行军进度越界。")
                if army.location_node_id != army.route_node_ids[army.route_progress_index]:
                    raise StrategyError(f"军队 {army.army_id} 的位置与行军进度不一致。")
                for source_node_id, target_node_id in zip(army.route_node_ids, army.route_node_ids[1:]):
                    source_node = next(node for node in self.nodes if node.node_id == source_node_id)
                    if target_node_id not in source_node.connected_node_ids:
                        raise StrategyError(f"军队 {army.army_id} 的行军路线包含未连接节点。")
            if army.status == "marching":
                if (
                    army.current_order != "march"
                    or len(army.route_node_ids) < 2
                    or army.route_progress_index >= len(army.route_node_ids) - 1
                    or army.march_origin_node_id != army.route_node_ids[0]
                    or army.destination_node_id != army.route_node_ids[-1]
                    or army.departure_month is None
                    or army.estimated_arrival_month != army.departure_month + len(army.route_node_ids) - 1
                ):
                    raise StrategyError(f"军队 {army.army_id} 的行军命令不完整。")
            if army.target_army_id is not None and army.target_army_id not in army_ids:
                raise StrategyError(f"军队 {army.army_id} 的拦截目标不存在。")
            if army.target_encounter_id is not None and army.target_encounter_id not in encounter_ids:
                raise StrategyError(f"军队 {army.army_id} 的增援目标不存在。")
            if army.retreat_destination_node_id is not None and army.retreat_destination_node_id not in node_ids:
                raise StrategyError(f"军队 {army.army_id} 的撤退目的地不存在。")
        active_encounter_armies: set[str] = set()
        for encounter in self.encounters:
            if encounter.node_id not in node_ids:
                raise StrategyError(f"战略遭遇 {encounter.encounter_id} 的节点不存在。")
            if encounter.status not in {"active", "ended"}:
                raise StrategyError(f"战略遭遇 {encounter.encounter_id} 状态无效。")
            if any(faction_id not in faction_ids for faction_id in encounter.faction_army_ids):
                raise StrategyError(f"战略遭遇 {encounter.encounter_id} 包含不存在的势力。")
            listed_ids = [army_id for ids in encounter.faction_army_ids.values() for army_id in ids]
            if len(listed_ids) != len(set(listed_ids)) or any(army_id not in army_ids for army_id in listed_ids):
                raise StrategyError(f"战略遭遇 {encounter.encounter_id} 的军队列表无效。")
            for faction_id, listed_armies in encounter.faction_army_ids.items():
                if any(next(army for army in self.armies if army.army_id == army_id).faction_id != faction_id for army_id in listed_armies):
                    raise StrategyError(f"战略遭遇 {encounter.encounter_id} 的参战势力不一致。")
            if encounter.status == "active":
                active_sides = [ids for ids in encounter.faction_army_ids.values() if ids]
                if len(active_sides) < 2:
                    raise StrategyError(f"战略遭遇 {encounter.encounter_id} 至少需要两个参战势力。")
                if active_encounter_armies.intersection(listed_ids):
                    raise StrategyError("同一军队不能同时参加多场战略遭遇。")
                active_encounter_armies.update(listed_ids)
                for army_id in listed_ids:
                    army = next(item for item in self.armies if item.army_id == army_id)
                    if army.location_node_id != encounter.node_id or army.status not in {"engaged", "retreating"}:
                        raise StrategyError(f"战略遭遇 {encounter.encounter_id} 的军队位置或状态不一致。")
        active_siege_armies: set[str] = set()
        for siege in self.sieges:
            if siege.city_id not in city_ids or siege.node_id not in node_ids:
                raise StrategyError(f"战略围城 {siege.siege_id} 的城市或节点不存在。")
            city = next(item for item in self.cities if item.city_id == siege.city_id)
            if city.node_id != siege.node_id:
                raise StrategyError(f"战略围城 {siege.siege_id} 的城市节点不一致。")
            if siege.attacker_faction_id not in faction_ids or siege.defender_faction_id not in faction_ids:
                raise StrategyError(f"战略围城 {siege.siege_id} 的攻守势力不存在。")
            if siege.attacker_faction_id == siege.defender_faction_id:
                raise StrategyError(f"战略围城 {siege.siege_id} 的攻守势力不能相同。")
            if siege.status not in {"active", "contested", "breached", "battle_pending", "ended"}:
                raise StrategyError(f"战略围城 {siege.siege_id} 状态无效。")
            if siege.attacker_stance not in {"blockade", "starve", "assault", "withdraw"}:
                raise StrategyError(f"战略围城 {siege.siege_id} 的攻方方针无效。")
            if siege.defender_stance not in {"hold", "breakout", "await_relief", "surrender"}:
                raise StrategyError(f"战略围城 {siege.siege_id} 的守方方针无效。")
            if len(siege.attacker_army_ids) != len(set(siege.attacker_army_ids)) or any(
                army_id not in army_ids for army_id in siege.attacker_army_ids
            ):
                raise StrategyError(f"战略围城 {siege.siege_id} 的参围军队无效。")
            if siege.status != "ended":
                if not siege.attacker_army_ids:
                    raise StrategyError(f"战略围城 {siege.siege_id} 没有参围军队。")
                if active_siege_armies.intersection(siege.attacker_army_ids):
                    raise StrategyError("同一军队不能同时参加多场战略围城。")
                active_siege_armies.update(siege.attacker_army_ids)
                for army_id in siege.attacker_army_ids:
                    army = next(item for item in self.armies if item.army_id == army_id)
                    if army.faction_id != siege.attacker_faction_id or army.location_node_id != siege.node_id:
                        raise StrategyError(f"战略围城 {siege.siege_id} 的参围军队不一致。")
                    if army.status not in {"besieging", "engaged", "retreating"}:
                        raise StrategyError(f"战略围城 {siege.siege_id} 的参围军队状态无效。")
        for battle in self.pending_battles:
            if battle.attacker_faction_id not in faction_ids or battle.defender_faction_id not in faction_ids:
                raise StrategyError(f"战略战斗 {battle.battle_id} 绑定了不存在的势力。")
            if battle.source_city_id not in city_ids or battle.target_city_id not in city_ids:
                raise StrategyError(f"战略战斗 {battle.battle_id} 绑定了不存在的城市。")
            if battle.attacker_office_id is not None and battle.attacker_office_id not in office_ids:
                raise StrategyError(f"战略战斗 {battle.battle_id} 绑定了不存在的出征职位。")
            if battle.source_kind not in {"legacy_city_attack", "encounter", "siege", "world_crisis"}:
                raise StrategyError(f"战略战斗 {battle.battle_id} 的来源类型无效。")
            if any(army_id not in army_ids for army_id in [*battle.attacker_army_ids, *battle.defender_army_ids]):
                raise StrategyError(f"战略战斗 {battle.battle_id} 绑定了不存在的军队。")
            if battle.battle_node_id is not None and battle.battle_node_id not in node_ids:
                raise StrategyError(f"战略战斗 {battle.battle_id} 绑定了不存在的战斗节点。")
        for event in self.story_events:
            if event.faction_id not in faction_ids or event.city_id not in city_ids:
                raise StrategyError(f"战略事件 {event.event_id} 绑定了不存在的势力或城市。")
        for consequence in self.scheduled_consequences:
            if consequence.faction_id not in faction_ids or consequence.city_id not in city_ids:
                raise StrategyError(f"延迟后果 {consequence.consequence_id} 绑定了不存在的势力或城市。")
        for office in self.offices:
            if office.faction_id not in faction_ids:
                raise StrategyError(f"职位 {office.office_id} 绑定了不存在的势力。")
            if office.parent_office_id is not None and office.parent_office_id not in office_ids:
                raise StrategyError(f"职位 {office.office_id} 的上级职位不存在。")
            if any(subordinate_id not in office_ids for subordinate_id in office.subordinate_office_ids):
                raise StrategyError(f"职位 {office.office_id} 包含不存在的下属职位。")
            if self.strategic_heroes and office.holder_type == "hero":
                holder = next((hero for hero in self.strategic_heroes if hero.hero_code == office.holder_id), None)
                if holder is None or holder.office_id != office.office_id:
                    raise StrategyError(f"职位 {office.office_id} 没有绑定一致的武将持有人。")
        for hero in self.strategic_heroes:
            if hero.status not in {"roaming", "serving", "sleeping"}:
                raise StrategyError(f"战略武将 {hero.hero_code} 状态无效。")
            if hero.faction_id is not None and hero.faction_id not in faction_ids:
                raise StrategyError(f"战略武将 {hero.hero_code} 所属势力不存在。")
            if hero.city_id is not None and hero.city_id not in city_ids:
                raise StrategyError(f"战略武将 {hero.hero_code} 所在城市不存在。")
            if hero.ritual_city_id is not None and hero.ritual_city_id not in city_ids:
                raise StrategyError(f"战略武将 {hero.hero_code} 绑定的祭祀城市不存在。")
            if hero.office_id is not None and hero.office_id not in office_ids:
                raise StrategyError(f"战略武将 {hero.hero_code} 担任的职位不存在。")
            if hero.status == "roaming" and hero.faction_id is not None:
                raise StrategyError(f"在野武将 {hero.hero_code} 不能已有所属势力。")
            if hero.assignment_type not in {"reserve", "administration", "training", "garrison", "campaign"}:
                raise StrategyError(f"战略武将 {hero.hero_code} 的任务类型无效。")
            if hero.last_personal_action_month is not None and hero.last_personal_action_month < 1:
                raise StrategyError(f"战略武将 {hero.hero_code} 的个人行动月份无效。")
            if hero.strategic_specialty and hero.strategic_specialty not in {
                "vanguard",
                "guardian",
                "trainer",
                "aether_scholar",
            }:
                raise StrategyError(f"战略武将 {hero.hero_code} 的战略专长无效。")
            if hero.personal_mission_status not in {"none", "active", "completed", "failed"}:
                raise StrategyError(f"战略武将 {hero.hero_code} 的个人任务状态无效。")
            if hero.personal_mission_assignment_type is not None and hero.personal_mission_assignment_type not in {
                "administration",
                "training",
                "garrison",
                "campaign",
            }:
                raise StrategyError(f"战略武将 {hero.hero_code} 的个人任务职责无效。")
            if any(other_code not in hero_codes or other_code == hero.hero_code for other_code in hero.relationships):
                raise StrategyError(f"战略武将 {hero.hero_code} 记录了无效英灵关系。")
            if any(score < -100 or score > 100 for score in hero.relationships.values()):
                raise StrategyError(f"战略武将 {hero.hero_code} 的英灵关系超出范围。")
            if hero.personal_mission_status != "none" and not hero.personal_mission_id:
                raise StrategyError(f"战略武将 {hero.hero_code} 的个人任务缺少标识。")
            if hero.personal_mission_progress > hero.personal_mission_required:
                raise StrategyError(f"战略武将 {hero.hero_code} 的个人任务进度无效。")
            if hero.last_duty_settlement_month is not None and hero.last_duty_settlement_month < 1:
                raise StrategyError(f"战略武将 {hero.hero_code} 的职责结算月份无效。")
        for altar in self.relic_altars:
            if altar.city_id not in city_ids:
                raise StrategyError(f"圣物祭坛 {altar.altar_id} 所在城市不存在。")
            if altar.state not in {"dormant", "active", "damaged"}:
                raise StrategyError(f"圣物祭坛 {altar.altar_id} 状态无效。")
            if len(altar.bound_relic_ids) > altar.capacity:
                raise StrategyError(f"圣物祭坛 {altar.altar_id} 超出绑定容量。")
            if altar.actions_used > 1:
                raise StrategyError(f"圣物祭坛 {altar.altar_id} 超出当前等级每月行动次数。")
            if altar.actions_used and altar.action_month is None:
                raise StrategyError(f"圣物祭坛 {altar.altar_id} 的行动月份缺失。")
            if any(relic_id not in relic_ids for relic_id in altar.bound_relic_ids):
                raise StrategyError(f"圣物祭坛 {altar.altar_id} 绑定了不存在的圣物。")
            if altar.consecration_progress > altar.consecration_required:
                raise StrategyError(f"圣物祭坛 {altar.altar_id} 的胜利准备进度无效。")
            if altar.consecration_progress > 0:
                if (
                    altar.consecration_faction_id not in faction_ids
                    or altar.consecration_relic_id not in altar.bound_relic_ids
                    or altar.consecration_started_month is None
                    or altar.consecration_last_month is None
                ):
                    raise StrategyError(f"圣物祭坛 {altar.altar_id} 的胜利准备绑定无效。")
                consecration_relic = next(
                    item
                    for item in self.relics
                    if item.relic_id == altar.consecration_relic_id
                )
                if (
                    consecration_relic.owner_faction_id != altar.consecration_faction_id
                    or consecration_relic.condition != "intact"
                    or altar.state != "active"
                ):
                    raise StrategyError(f"圣物祭坛 {altar.altar_id} 的胜利准备状态不一致。")
            city = next(item for item in self.cities if item.city_id == altar.city_id)
            if altar.altar_id not in city.altars:
                raise StrategyError(f"圣物祭坛 {altar.altar_id} 未登记到所在城市。")
            if (
                altar.consecration_progress > 0
                and city.owner_faction_id != altar.consecration_faction_id
            ):
                raise StrategyError(f"圣物祭坛 {altar.altar_id} 的胜利准备控制权不一致。")
        for relic in self.relics:
            if relic.hero_code not in hero_codes:
                raise StrategyError(f"圣物 {relic.relic_id} 对应的英灵不存在。")
            if relic.state not in {"scattered", "stored", "bound_to_altar", "released"}:
                raise StrategyError(f"圣物 {relic.relic_id} 状态无效。")
            if relic.condition not in {"intact", "damaged"}:
                raise StrategyError(f"圣物 {relic.relic_id} 完整度状态无效。")
            if relic.location_node_id is not None and relic.location_node_id not in node_ids:
                raise StrategyError(f"圣物 {relic.relic_id} 所在节点不存在。")
            if relic.location_city_id is not None and relic.location_city_id not in city_ids:
                raise StrategyError(f"圣物 {relic.relic_id} 所在城市不存在。")
            if relic.owner_faction_id is not None and relic.owner_faction_id not in faction_ids:
                raise StrategyError(f"圣物 {relic.relic_id} 所属势力不存在。")
            if relic.altar_id is not None and relic.altar_id not in altar_ids:
                raise StrategyError(f"圣物 {relic.relic_id} 绑定祭坛不存在。")
            if any(faction_id not in faction_ids for faction_id in relic.discovered_by_faction_ids):
                raise StrategyError(f"圣物 {relic.relic_id} 的发现势力不存在。")
            if relic.state in {"scattered", "released"}:
                if relic.location_node_id is None or relic.owner_faction_id is not None or relic.altar_id is not None:
                    raise StrategyError(f"散落或释放的圣物 {relic.relic_id} 状态不一致。")
            if relic.state == "stored":
                if relic.location_city_id is None or relic.owner_faction_id is None or relic.altar_id is not None:
                    raise StrategyError(f"保管中的圣物 {relic.relic_id} 状态不一致。")
                city = next(item for item in self.cities if item.city_id == relic.location_city_id)
                if relic.relic_id not in city.relics_stored:
                    raise StrategyError(f"保管中的圣物 {relic.relic_id} 未登记到所在城市。")
            if relic.state == "bound_to_altar":
                if relic.location_city_id is None or relic.owner_faction_id is None or relic.altar_id is None:
                    raise StrategyError(f"祭坛绑定圣物 {relic.relic_id} 状态不一致。")
                altar = next(item for item in self.relic_altars if item.altar_id == relic.altar_id)
                if relic.relic_id not in altar.bound_relic_ids or altar.city_id != relic.location_city_id:
                    raise StrategyError(f"祭坛绑定圣物 {relic.relic_id} 与祭坛记录不一致。")
        stored_relic_ids = {
            relic.relic_id
            for relic in self.relics
            if relic.state == "stored"
        }
        for city in self.cities:
            if len(city.relics_stored) != len(set(city.relics_stored)):
                raise StrategyError(f"城市 {city.city_id} 的圣物保管清单存在重复。")
            if any(relic_id not in stored_relic_ids for relic_id in city.relics_stored):
                raise StrategyError(f"城市 {city.city_id} 登记了非保管状态圣物。")
        for recruitment in self.hero_recruitments:
            if recruitment.faction_id not in faction_ids or recruitment.city_id not in city_ids:
                raise StrategyError(f"武将招募令 {recruitment.recruitment_id} 势力或城市不存在。")
            if recruitment.issuer_office_id not in office_ids:
                raise StrategyError(f"武将招募令 {recruitment.recruitment_id} 签发职位不存在。")
            if any(code not in hero_codes for code in recruitment.candidate_hero_codes):
                raise StrategyError(f"武将招募令 {recruitment.recruitment_id} 包含不存在的候选武将。")
            if recruitment.recommended_hero_code is not None and recruitment.recommended_hero_code not in hero_codes:
                raise StrategyError(f"武将招募令 {recruitment.recruitment_id} 举荐了不存在的武将。")
            if recruitment.recommended_by_office_id is not None and recruitment.recommended_by_office_id not in office_ids:
                raise StrategyError(f"武将招募令 {recruitment.recruitment_id} 举荐职位不存在。")
        for duty in self.office_duties:
            if duty.office_id not in office_ids:
                raise StrategyError(f"职位职责 {duty.duty_id} 绑定了不存在的职位。")
        for order in self.office_orders:
            if order.issuer_office_id not in office_ids or order.receiver_office_id not in office_ids:
                raise StrategyError(f"职位命令 {order.order_id} 绑定了不存在的职位。")
        for takeover in self.office_takeovers:
            if takeover.superior_office_id not in office_ids or takeover.vacant_office_id not in office_ids:
                raise StrategyError("职位临时接管绑定了不存在的职位。")
        for crisis in self.world_crises:
            if crisis.crisis_type not in {"snow_ghost"}:
                raise StrategyError(f"世界危机 {crisis.crisis_id} 类型无效。")
            if crisis.status not in {"dormant", "active", "resolved"}:
                raise StrategyError(f"世界危机 {crisis.crisis_id} 状态无效。")
            if crisis.stage not in {
                "dormant",
                "omen",
                "border_pressure",
                "spread",
                "mobilization",
                "showdown",
                "aftermath",
                "resolved",
            }:
                raise StrategyError(f"世界危机 {crisis.crisis_id} 阶段无效。")
            if crisis.origin_node_id not in node_ids:
                raise StrategyError(f"世界危机 {crisis.crisis_id} 起源节点不存在。")
            if (
                not crisis.frontier_node_ids
                or crisis.origin_node_id not in crisis.frontier_node_ids
                or any(node_id not in node_ids for node_id in crisis.frontier_node_ids)
            ):
                raise StrategyError(f"世界危机 {crisis.crisis_id} 前线节点无效。")
            for route_key in crisis.affected_route_keys:
                route_nodes = route_key.split("::")
                if len(route_nodes) != 2 or route_nodes[0] not in node_ids or route_nodes[1] not in node_ids:
                    raise StrategyError(f"世界危机 {crisis.crisis_id} 影响路线无效。")
                source = next(node for node in self.nodes if node.node_id == route_nodes[0])
                if route_nodes[1] not in source.connected_node_ids:
                    raise StrategyError(f"世界危机 {crisis.crisis_id} 影响了不存在的地图边。")
            if any(city_id not in city_ids for city_id in crisis.threatened_city_ids):
                raise StrategyError(f"世界危机 {crisis.crisis_id} 受威胁城市无效。")
            if any(army_id not in army_ids for army_id in crisis.spawned_army_ids):
                raise StrategyError(f"世界危机 {crisis.crisis_id} 生成军队无效。")
            major_faction_ids = {faction.faction_id for faction in self.factions if faction.is_major}
            if any(faction_id not in major_faction_ids for faction_id in crisis.contributions_by_faction):
                raise StrategyError(f"世界危机 {crisis.crisis_id} 贡献势力无效。")
            if any(
                faction_id not in major_faction_ids
                or target_id not in major_faction_ids
                or faction_id == target_id
                for faction_id, target_id in crisis.cooperation_targets_by_faction.items()
            ):
                raise StrategyError(f"世界危机 {crisis.crisis_id} 合作承诺无效。")
            for pair_key in [*crisis.cooperation_pairs, *crisis.broken_cooperation_pairs]:
                pair = pair_key.split("::")
                if (
                    len(pair) != 2
                    or pair[0] not in major_faction_ids
                    or pair[1] not in major_faction_ids
                    or pair[0] == pair[1]
                ):
                    raise StrategyError(f"世界危机 {crisis.crisis_id} 合作组合无效。")
            if crisis.showdown_branch is not None and crisis.showdown_branch not in {
                "united_counteroffensive",
                "rival_vanguards",
                "shattered_line",
            }:
                raise StrategyError(f"世界危机 {crisis.crisis_id} 决战分支无效。")
            if (
                crisis.showdown_leader_faction_id is not None
                and crisis.showdown_leader_faction_id not in major_faction_ids
            ):
                raise StrategyError(f"世界危机 {crisis.crisis_id} 决战领袖无效。")
            if any(faction_id not in major_faction_ids for faction_id in crisis.mainline_winner_faction_ids):
                raise StrategyError(f"世界危机 {crisis.crisis_id} 主线胜利势力无效。")
            if (
                crisis.showdown_battle_id is not None
                and crisis.showdown_battle_id not in {battle.battle_id for battle in self.pending_battles}
            ):
                raise StrategyError(f"世界危机 {crisis.crisis_id} 决战记录不存在。")
            if crisis.next_stage_month is not None and crisis.next_stage_month <= crisis.stage_started_month:
                raise StrategyError(f"世界危机 {crisis.crisis_id} 下一阶段月份无效。")
        if self.campaign_contract:
            month_limit = int(self.campaign_contract.get("month_limit", 0))
            if month_limit <= 0:
                raise StrategyError("限时战役必须设置正数月份上限。")
        if self.campaign_conclusion:
            conclusion_state = str(self.campaign_conclusion.get("state") or "")
            if conclusion_state not in {"settled", "sandbox", "archived"}:
                raise StrategyError("战役结算状态无效。")

    def to_dict(self) -> dict[str, Any]:
        return {
            "save_format_version": self.save_format_version,
            "seed": self.seed,
            "current_month": self.current_month,
            "nodes": [node.to_dict() for node in self.nodes],
            "cities": [city.to_dict() for city in self.cities],
            "factions": [faction.to_dict() for faction in self.factions],
            "event_log": [event.to_dict() for event in self.event_log],
            "memory_tags": list(self.memory_tags),
            "pending_battles": [battle.to_dict() for battle in self.pending_battles],
            "story_events": [event.to_dict() for event in self.story_events],
            "scheduled_consequences": [item.to_dict() for item in self.scheduled_consequences],
            "strategic_heroes": [hero.to_dict() for hero in self.strategic_heroes],
            "relics": [relic.to_dict() for relic in self.relics],
            "relic_altars": [altar.to_dict() for altar in self.relic_altars],
            "hero_recruitments": [item.to_dict() for item in self.hero_recruitments],
            "offices": [office.to_dict() for office in self.offices],
            "office_duties": [duty.to_dict() for duty in self.office_duties],
            "office_orders": [order.to_dict() for order in self.office_orders],
            "office_takeovers": [takeover.to_dict() for takeover in self.office_takeovers],
            "campaign_contract": dict(self.campaign_contract),
            "campaign_conclusion": dict(self.campaign_conclusion),
            "monthly_reports": [dict(report) for report in self.monthly_reports],
            "campaign_tutorial": dict(self.campaign_tutorial),
            "ai_strategic_goals": copy.deepcopy(self.ai_strategic_goals),
            "diplomatic_agreements": [item.to_dict() for item in self.diplomatic_agreements],
            "diplomatic_cooldowns": dict(self.diplomatic_cooldowns),
            "diplomatic_memory": [dict(item) for item in self.diplomatic_memory],
            "armies": [item.to_dict() for item in self.armies],
            "encounters": [item.to_dict() for item in self.encounters],
            "sieges": [item.to_dict() for item in self.sieges],
            "world_crises": [item.to_dict() for item in self.world_crises],
        }

    def to_public_dict(self) -> dict[str, Any]:
        from wujiang.strategy.tactics import enrich_world_public_state
        from wujiang.strategy.relics import ensure_relic_system
        from wujiang.strategy.world_crisis import ensure_world_crises

        return enrich_world_public_state(ensure_world_crises(ensure_relic_system(self)))

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> WorldState:
        raw = migrate_world_payload(raw)
        return cls(
            save_format_version=int(raw.get("save_format_version", CURRENT_STRATEGY_SAVE_VERSION)),
            seed=int(raw.get("seed", 0)),
            current_month=int(raw.get("current_month", 1)),
            nodes=[MapNode.from_dict(item) for item in raw.get("nodes", [])],
            cities=[City.from_dict(item) for item in raw.get("cities", [])],
            factions=[Faction.from_dict(item) for item in raw.get("factions", [])],
            event_log=[EventLogEntry.from_dict(item) for item in raw.get("event_log", [])],
            memory_tags=_string_list(raw.get("memory_tags")),
            pending_battles=[PendingBattle.from_dict(item) for item in raw.get("pending_battles", [])],
            story_events=[StoryEvent.from_dict(item) for item in raw.get("story_events", [])],
            scheduled_consequences=[ScheduledConsequence.from_dict(item) for item in raw.get("scheduled_consequences", [])],
            strategic_heroes=[StrategicHeroState.from_dict(item) for item in raw.get("strategic_heroes", [])],
            relics=[
                RelicState.from_dict(item)
                for item in raw.get("relics", [])
                if isinstance(item, dict)
            ],
            relic_altars=[
                RelicAltar.from_dict(item)
                for item in raw.get("relic_altars", [])
                if isinstance(item, dict)
            ],
            hero_recruitments=[HeroRecruitment.from_dict(item) for item in raw.get("hero_recruitments", [])],
            offices=[Office.from_dict(item) for item in raw.get("offices", [])],
            office_duties=[OfficeDuty.from_dict(item) for item in raw.get("office_duties", [])],
            office_orders=[OfficeOrder.from_dict(item) for item in raw.get("office_orders", [])],
            office_takeovers=[OfficeTakeover.from_dict(item) for item in raw.get("office_takeovers", [])],
            campaign_contract=_plain_dict(raw.get("campaign_contract")),
            campaign_conclusion=_plain_dict(raw.get("campaign_conclusion")),
            monthly_reports=[
                _plain_dict(report)
                for report in raw.get("monthly_reports", [])
                if isinstance(report, dict)
            ],
            campaign_tutorial=_plain_dict(raw.get("campaign_tutorial")),
            ai_strategic_goals=_plain_dict(raw.get("ai_strategic_goals")),
            diplomatic_agreements=[
                DiplomaticAgreement.from_dict(item)
                for item in raw.get("diplomatic_agreements", [])
                if isinstance(item, dict)
            ],
            diplomatic_cooldowns=_int_dict(raw.get("diplomatic_cooldowns")),
            diplomatic_memory=[
                _plain_dict(item)
                for item in raw.get("diplomatic_memory", [])
                if isinstance(item, dict)
            ],
            armies=[
                StrategicArmy.from_dict(item)
                for item in raw.get("armies", [])
                if isinstance(item, dict)
            ],
            encounters=[
                StrategicEncounter.from_dict(item)
                for item in raw.get("encounters", [])
                if isinstance(item, dict)
            ],
            sieges=[
                StrategicSiege.from_dict(item)
                for item in raw.get("sieges", [])
                if isinstance(item, dict)
            ],
            world_crises=[
                WorldCrisis.from_dict(item)
                for item in raw.get("world_crises", [])
                if isinstance(item, dict)
            ],
        )
