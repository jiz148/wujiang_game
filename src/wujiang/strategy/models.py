from __future__ import annotations

import copy
from dataclasses import dataclass, field
from http import HTTPStatus
from typing import Any


class StrategyError(Exception):
    def __init__(self, message: str, *, status: HTTPStatus = HTTPStatus.BAD_REQUEST) -> None:
        super().__init__(message)
        self.status = status


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
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> StrategicHeroState:
        controller_user_id = raw.get("controller_user_id")
        sleeping_until_month = raw.get("sleeping_until_month")
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

    def __post_init__(self) -> None:
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

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.army_id,
            "faction_id": self.faction_id,
            "commander_office_id": self.commander_office_id,
            "commander_hero_code": self.commander_hero_code,
            "location_node_id": self.location_node_id,
            "home_city_id": self.home_city_id,
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

    def __post_init__(self) -> None:
        self.seed = int(self.seed)
        self.current_month = int(self.current_month)
        if self.current_month <= 0:
            raise StrategyError("战略月份必须为正数。")
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
        recruitment_ids = {item.recruitment_id for item in self.hero_recruitments}
        agreement_ids = {item.agreement_id for item in self.diplomatic_agreements}
        army_ids = {item.army_id for item in self.armies}
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
        for faction in self.factions:
            if faction.faction_type not in {"major", "neutral_city_state"}:
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
            if major is None or major.is_neutral_city_state or neutral is None or not neutral.is_neutral_city_state:
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
            if army.current_order not in {"hold", "march", "intercept", "reinforce", "retreat", "besiege"}:
                raise StrategyError(f"军队 {army.army_id} 命令无效。")
            if army.status not in {"disbanded", "destroyed"}:
                if army.commander_office_id in active_commanders:
                    raise StrategyError("同一将军只能指挥一支现役军队。")
                active_commanders.add(army.commander_office_id)
                if not army.unit_inventory or army.manpower <= 0:
                    raise StrategyError(f"现役军队 {army.army_id} 必须拥有兵员。")
            if any(unit_type not in {"infantry", "archer", "cavalry"} for unit_type in army.unit_inventory):
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
        for battle in self.pending_battles:
            if battle.attacker_faction_id not in faction_ids or battle.defender_faction_id not in faction_ids:
                raise StrategyError(f"战略战斗 {battle.battle_id} 绑定了不存在的势力。")
            if battle.source_city_id not in city_ids or battle.target_city_id not in city_ids:
                raise StrategyError(f"战略战斗 {battle.battle_id} 绑定了不存在的城市。")
            if battle.attacker_office_id is not None and battle.attacker_office_id not in office_ids:
                raise StrategyError(f"战略战斗 {battle.battle_id} 绑定了不存在的出征职位。")
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
        }

    def to_public_dict(self) -> dict[str, Any]:
        from wujiang.strategy.tactics import enrich_world_public_state

        return enrich_world_public_state(self)

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> WorldState:
        return cls(
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
        )
