from __future__ import annotations

import copy
from typing import Any, Iterable

from wujiang.strategy.models import (
    EventLogEntry,
    Office,
    OfficeDuty,
    OfficeOrder,
    StrategyError,
    WorldState,
)


OFFICE_TYPE_LABELS = {
    "lord": "主公",
    "grand_general": "大将军",
    "general": "将军",
    "governor": "城主",
}

OFFICE_WORKSPACES = {
    "lord": "LordWorkspace",
    "grand_general": "GrandGeneralWorkspace",
    "general": "GeneralWorkspace",
    "governor": "GovernorWorkspace",
}

OFFICE_PERMISSIONS = {
    "lord": (
        "advance_month",
        "manage_offices",
        "national_technology",
        "national_policy",
        "national_budget",
        "diplomacy",
        "perform_ritual",
        "unbind_hero",
        "relic_decision",
        "issue_order",
        "resolve_faction_event",
        "exile_action",
        "temporary_takeover",
        "lead_army",
        "declare_attack",
    ),
    "grand_general": (
        "manage_theater",
        "assign_general",
        "allocate_military_supply",
        "issue_order",
        "set_defense_plan",
        "send_request",
        "temporary_takeover",
        "transfer_registered_units",
        "approve_unit_request",
    ),
    "general": (
        "command_army",
        "declare_attack",
        "battle_control",
        "set_battle_roster",
        "send_request",
        "submit_report",
        "request_registered_units",
    ),
    "governor": (
        "manage_city",
        "set_city_policy",
        "handle_rebellion",
        "manage_local_defense",
        "resolve_city_event",
        "send_request",
        "submit_report",
        "perform_ritual",
        "increase_city_troops",
        "register_city_soldiers",
        "manage_buildings",
    ),
}

OFFICE_DUTY_TYPES = {
    "lord": ("review_national_strategy", "review_office_vacancies", "review_subordinate_requests"),
    "grand_general": ("review_theater_security", "coordinate_generals", "report_major_threats"),
    "general": ("maintain_army_readiness", "execute_military_orders", "submit_battle_reports"),
    "governor": ("maintain_food_supply", "maintain_city_support", "manage_local_defense"),
}

ACTION_PERMISSION = {
    "advance_month": "advance_month",
    "unlock_tactic_tech": "national_technology",
    "perform_hero_ritual": "perform_ritual",
    "unbind_strategic_hero": "unbind_hero",
    "appoint_strategic_hero": "manage_offices",
    "assign_strategic_hero_duty": "manage_offices",
    "increase_city_troops": "increase_city_troops",
    "register_city_soldiers": "register_city_soldiers",
    "transfer_registered_units": "transfer_registered_units",
    "request_registered_units": "request_registered_units",
    "approve_registered_unit_request": "approve_unit_request",
    "construct_city_building": "manage_buildings",
    "set_city_policy": "set_city_policy",
    "rebellion_action": "handle_rebellion",
    "rebellion_battle": "handle_rebellion",
    "declare_attack": "declare_attack",
    "exile_action": "exile_action",
    "issue_office_order": "issue_order",
    "send_office_request": "send_request",
    "set_strategic_defender_hero": "set_defense_plan",
    "set_battle_defender_hero": "battle_control",
}


def _clone_world(world: WorldState) -> WorldState:
    return WorldState.from_dict(copy.deepcopy(world.to_dict()))


def office_id(faction_id: str, office_type: str, suffix: str = "") -> str:
    return ":".join(part for part in ("office", str(faction_id), str(office_type), str(suffix)) if part)


def grand_general_capacity(world: WorldState, faction_id: str) -> int:
    faction = next((item for item in world.factions if item.faction_id == str(faction_id)), None)
    if faction is None:
        raise StrategyError("势力不存在。")
    capacity = 1
    from wujiang.strategy.tactics import TACTIC_TECHS_BY_ID

    for tech_id in faction.tactic_techs:
        tech = TACTIC_TECHS_BY_ID.get(tech_id)
        if tech is not None:
            capacity += int(tech.office_capacity_effects.get("grand_general", 0))
    return max(0, capacity)


def general_capacity_per_grand_general(world: WorldState, faction_id: str) -> int:
    faction = next((item for item in world.factions if item.faction_id == str(faction_id)), None)
    if faction is None:
        raise StrategyError("势力不存在。")
    capacity = 1
    from wujiang.strategy.tactics import TACTIC_TECHS_BY_ID

    for tech_id in faction.tactic_techs:
        tech = TACTIC_TECHS_BY_ID.get(tech_id)
        if tech is not None:
            capacity += int(tech.office_capacity_effects.get("general_per_grand_general", 0))
    return max(0, capacity)


def _member_controller(members: Iterable[Any] | None, faction_id: str) -> tuple[str, int | None, str | None, str | None]:
    candidates = [member for member in (members or ()) if str(getattr(member, "faction_id", "")) == str(faction_id)]
    human = next(
        (
            member
            for member in candidates
            if str(getattr(member, "role", "")).lower() != "ai" and int(getattr(member, "user_id", 0)) > 0
        ),
        None,
    )
    if human is not None:
        user_id = int(getattr(human, "user_id"))
        return "player", user_id, f"player:{user_id}", "player_character"
    ai_member = next((member for member in candidates if str(getattr(member, "role", "")).lower() == "ai"), None)
    ai_user_id = int(getattr(ai_member, "user_id", 0)) if ai_member is not None else None
    return "ai", ai_user_id, f"ai:{faction_id}", "officer"


def _upsert_office(offices: dict[str, Office], office: Office) -> Office:
    existing = offices.get(office.office_id)
    if existing is None:
        offices[office.office_id] = office
        return office
    existing.faction_id = office.faction_id
    existing.office_type = office.office_type
    existing.parent_office_id = office.parent_office_id
    existing.managed_entity_ids = list(office.managed_entity_ids)
    existing.permissions = list(office.permissions)
    existing.duties = list(office.duties)
    existing.status = office.status
    return existing


def ensure_office_system(world: WorldState, members: Iterable[Any] | None = None) -> WorldState:
    next_world = _clone_world(world)
    offices = {office.office_id: office for office in next_world.offices}
    active_ids: set[str] = set()

    for faction in sorted(next_world.factions, key=lambda item: item.faction_id):
        lord_id = office_id(faction.faction_id, "lord")
        existing_lord = offices.get(lord_id)
        if existing_lord is not None and existing_lord.holder_type == "hero":
            if members is None:
                controller_type = existing_lord.controller_type
                controller_user_id = existing_lord.controller_user_id
            else:
                controller_type, controller_user_id, _, _ = _member_controller(members, faction.faction_id)
            holder_id = existing_lord.holder_id
            holder_type = existing_lord.holder_type
        else:
            controller_type, controller_user_id, holder_id, holder_type = _member_controller(members, faction.faction_id)
        lord = _upsert_office(
            offices,
            Office(
                office_id=lord_id,
                faction_id=faction.faction_id,
                office_type="lord",
                holder_id=holder_id,
                holder_type=holder_type,
                controller_type=controller_type,
                controller_user_id=controller_user_id,
                managed_entity_ids=[faction.faction_id],
                permissions=list(OFFICE_PERMISSIONS["lord"]),
                duties=list(OFFICE_DUTY_TYPES["lord"]),
            ),
        )
        lord.controller_type = controller_type
        lord.controller_user_id = controller_user_id
        lord.holder_id = holder_id
        lord.holder_type = holder_type
        active_ids.add(lord_id)

        owned_cities = sorted(
            (city for city in next_world.cities if city.owner_faction_id == faction.faction_id),
            key=lambda city: city.city_id,
        )
        general_offices: list[Office] = []
        general_capacity = general_capacity_per_grand_general(next_world, faction.faction_id)
        total_generals = max(1, grand_general_capacity(next_world, faction.faction_id) * general_capacity)
        general_ordinal = 0
        for index in range(1, grand_general_capacity(next_world, faction.faction_id) + 1):
            grand_id = office_id(faction.faction_id, "grand_general", str(index))
            existing_grand = offices.get(grand_id)
            grand = _upsert_office(
                offices,
                Office(
                    office_id=grand_id,
                    faction_id=faction.faction_id,
                    office_type="grand_general",
                    parent_office_id=lord_id,
                    managed_entity_ids=[f"theater:{faction.faction_id}:{index}"],
                    permissions=list(OFFICE_PERMISSIONS["grand_general"]),
                    duties=list(OFFICE_DUTY_TYPES["grand_general"]),
                ),
            )
            if existing_grand is not None and existing_grand.holder_type == "hero":
                grand.controller_type = existing_grand.controller_type
                grand.controller_user_id = existing_grand.controller_user_id
                grand.holder_id = existing_grand.holder_id
                grand.holder_type = "hero"
            elif not next_world.strategic_heroes:
                grand.controller_type = controller_type
                grand.controller_user_id = controller_user_id
                grand.holder_id = f"officer:{faction.faction_id}:grand_general:{index}"
                grand.holder_type = "officer"
            else:
                grand.controller_type = "ai"
                grand.controller_user_id = None
                grand.holder_id = None
                grand.holder_type = None
            active_ids.add(grand_id)

            for slot in range(1, general_capacity + 1):
                general_ordinal += 1
                suffix = str(index) if slot == 1 else f"{index}:{slot}"
                general_id = office_id(faction.faction_id, "general", suffix)
                existing_general = offices.get(general_id)
                assigned_cities = [
                    city.city_id
                    for city_index, city in enumerate(owned_cities)
                    if city_index % total_generals == general_ordinal - 1
                ]
                general = _upsert_office(
                    offices,
                    Office(
                        office_id=general_id,
                        faction_id=faction.faction_id,
                        office_type="general",
                        parent_office_id=grand_id,
                        managed_entity_ids=[f"army:{faction.faction_id}:{suffix}", *assigned_cities],
                        permissions=list(OFFICE_PERMISSIONS["general"]),
                        duties=list(OFFICE_DUTY_TYPES["general"]),
                    ),
                )
                if existing_general is not None and existing_general.holder_type == "hero":
                    general.controller_type = existing_general.controller_type
                    general.controller_user_id = existing_general.controller_user_id
                    general.holder_id = existing_general.holder_id
                    general.holder_type = "hero"
                elif not next_world.strategic_heroes:
                    general.controller_type = controller_type
                    general.controller_user_id = controller_user_id
                    general.holder_id = f"officer:{faction.faction_id}:general:{suffix}"
                    general.holder_type = "officer"
                else:
                    general.controller_type = "ai"
                    general.controller_user_id = None
                    general.holder_id = None
                    general.holder_type = None
                active_ids.add(general_id)
                general_offices.append(general)

        for city in owned_cities:
            governor_id = office_id(faction.faction_id, "governor", city.city_id)
            existing_governor = offices.get(governor_id)
            governor = _upsert_office(
                offices,
                Office(
                    office_id=governor_id,
                    faction_id=faction.faction_id,
                    office_type="governor",
                    parent_office_id=lord_id,
                    managed_entity_ids=[city.city_id],
                    permissions=list(OFFICE_PERMISSIONS["governor"]),
                    duties=list(OFFICE_DUTY_TYPES["governor"]),
                ),
            )
            if existing_governor is not None and existing_governor.holder_type == "hero":
                governor.controller_type = existing_governor.controller_type
                governor.controller_user_id = existing_governor.controller_user_id
                governor.holder_id = existing_governor.holder_id
                governor.holder_type = "hero"
            elif not next_world.strategic_heroes:
                governor.controller_type = controller_type
                governor.controller_user_id = controller_user_id
                governor.holder_id = f"officer:{faction.faction_id}:governor:{city.city_id}"
                governor.holder_type = "officer"
            else:
                governor.controller_type = "ai"
                governor.controller_user_id = None
                governor.holder_id = None
                governor.holder_type = None
            active_ids.add(governor_id)

    for office in offices.values():
        office.subordinate_office_ids = []
        if office.office_id not in active_ids:
            office.status = "disabled"
            office.managed_entity_ids = []
        elif office.holder_id is None:
            office.status = "vacant"
        else:
            office.status = "active"
    for office in offices.values():
        if office.status != "disabled" and office.parent_office_id in offices:
            offices[office.parent_office_id].subordinate_office_ids.append(office.office_id)
    for office in offices.values():
        office.subordinate_office_ids.sort()

    next_world.offices = sorted(offices.values(), key=lambda office: office.office_id)
    _refresh_office_duties(next_world)
    next_world.validate()
    return next_world


def _refresh_office_duties(world: WorldState) -> None:
    existing = {duty.duty_id: duty for duty in world.office_duties}
    for office in world.offices:
        if office.status == "disabled":
            continue
        for duty_type in office.duties:
            duty_id = f"duty:{world.current_month}:{office.office_id}:{duty_type}"
            if duty_id not in existing:
                existing[duty_id] = OfficeDuty(
                    duty_id=duty_id,
                    office_id=office.office_id,
                    duty_type=duty_type,
                    priority=2 if office.status == "vacant" else 1,
                    due_month=world.current_month,
                )
    world.office_duties = sorted(existing.values(), key=lambda duty: duty.duty_id)


def office_action_entity_id(action_type: str, payload: dict[str, Any]) -> str:
    if action_type in {
        "set_city_policy",
        "rebellion_action",
        "rebellion_battle",
        "perform_hero_ritual",
        "increase_city_troops",
        "register_city_soldiers",
        "transfer_registered_units",
        "request_registered_units",
        "construct_city_building",
    }:
        return str(payload.get("city_id") or payload.get("target_city_id") or "")
    if action_type == "declare_attack":
        return str(payload.get("source_city_id") or "")
    # Battle ids are not managed entities. Battle ownership is validated by the
    # battle service, while this layer verifies that a general signed the order.
    if action_type in {"set_battle_defender_hero"}:
        return ""
    return ""


def permission_for_action(world: WorldState, action_type: str, payload: dict[str, Any]) -> str:
    if action_type == "resolve_story_event":
        event_id = str(payload.get("event_id") or "")
        event = next((item for item in world.story_events if item.event_id == event_id), None)
        return "resolve_city_event" if event is not None and event.city_id else "resolve_faction_event"
    return ACTION_PERMISSION.get(action_type, "")


def resolve_action_office(
    world: WorldState,
    *,
    user_id: int,
    faction_id: str,
    action_type: str,
    payload: dict[str, Any],
    requested_office_id: str = "",
) -> Office:
    permission = permission_for_action(world, action_type, payload)
    if not permission:
        raise StrategyError("该战略行动尚未配置职位权限。")
    entity_id = office_action_entity_id(action_type, payload)
    candidates = [
        office
        for office in world.offices
        if office.faction_id == str(faction_id)
        and office.controller_type == "player"
        and int(office.controller_user_id or 0) == int(user_id)
        and office.status == "active"
        and permission in office.permissions
        and (
            not entity_id
            or (
                action_type in {"declare_attack", "perform_hero_ritual", "transfer_registered_units", "request_registered_units"}
                and office.office_type in {"lord", "grand_general", "general"}
            )
            or not office.managed_entity_ids
            or entity_id in office.managed_entity_ids
        )
    ]
    if requested_office_id:
        selected = next((office for office in candidates if office.office_id == str(requested_office_id)), None)
        if selected is None:
            raise StrategyError("当前职位无权执行该行动或不管理目标对象。")
        return selected
    if not candidates:
        raise StrategyError("你控制的职位中没有可执行该行动的职位。")
    return sorted(candidates, key=lambda office: office.office_id)[0]


def ai_office_for_action(
    world: WorldState,
    *,
    faction_id: str,
    action_type: str,
    payload: dict[str, Any],
) -> Office | None:
    permission = permission_for_action(world, action_type, payload)
    entity_id = office_action_entity_id(action_type, payload)
    candidates = [
        office
        for office in world.offices
        if office.faction_id == str(faction_id)
        and office.controller_type == "ai"
        and office.status == "active"
        and permission in office.permissions
        and (
            not entity_id
            or (
                action_type in {"declare_attack", "perform_hero_ritual", "transfer_registered_units", "request_registered_units"}
                and office.office_type in {"lord", "grand_general", "general"}
            )
            or not office.managed_entity_ids
            or entity_id in office.managed_entity_ids
        )
    ]
    return sorted(candidates, key=lambda office: office.office_id)[0] if candidates else None


def apply_office_order(
    world: WorldState,
    *,
    issuer_office_id: str,
    receiver_office_id: str,
    order_type: str,
    objective: str,
    target_entity_id: str = "",
    priority: int = 1,
    deadline_month: int | None = None,
) -> WorldState:
    next_world = _clone_world(world)
    issuer = next((office for office in next_world.offices if office.office_id == str(issuer_office_id)), None)
    receiver = next((office for office in next_world.offices if office.office_id == str(receiver_office_id)), None)
    if issuer is None or receiver is None or issuer.faction_id != receiver.faction_id:
        raise StrategyError("职位命令的发出者或接收者无效。")
    normalized_type = str(order_type or "order")
    if normalized_type == "request":
        if issuer.parent_office_id != receiver.office_id:
            raise StrategyError("职位请求只能提交给直属上级。")
    elif receiver.parent_office_id != issuer.office_id:
        raise StrategyError("职位命令只能下达给直属下属。")
    if normalized_type in {"attack_city", "defend_city"}:
        if (issuer.office_type, receiver.office_type) not in {
            ("lord", "grand_general"),
            ("grand_general", "general"),
        }:
            raise StrategyError("攻防命令只能由主公下达给大将军，或由大将军分派给直属将军。")
        target_city = next((city for city in next_world.cities if city.city_id == str(target_entity_id)), None)
        if target_city is None:
            raise StrategyError("攻防命令必须指定有效目标城市。")
    objective_text = str(objective or "").strip()
    if not objective_text:
        raise StrategyError("职位命令必须填写目标。")
    order_id_value = f"order:{next_world.current_month}:{len(next_world.office_orders) + 1}:{issuer.office_id}"
    next_world.office_orders.append(
        OfficeOrder(
            order_id=order_id_value,
            issuer_office_id=issuer.office_id,
            receiver_office_id=receiver.office_id,
            order_type=normalized_type,
            target_entity_id=str(target_entity_id or "") or None,
            objective=objective_text,
            priority=max(1, min(3, int(priority))),
            issued_month=next_world.current_month,
            deadline_month=int(deadline_month) if deadline_month is not None else None,
        )
    )
    next_world.event_log.append(
        EventLogEntry(
            month=next_world.current_month,
            category="office_request" if normalized_type == "request" else "office_order",
            message=f"{OFFICE_TYPE_LABELS.get(issuer.office_type, issuer.office_type)}向{OFFICE_TYPE_LABELS.get(receiver.office_type, receiver.office_type)}提交：{objective_text}",
            related_ids=[order_id_value, issuer.office_id, receiver.office_id],
        )
    )
    next_world.validate()
    return next_world


def office_system_public(world: WorldState) -> dict[str, Any]:
    capacities = {
        faction.faction_id: {
            "grand_general": grand_general_capacity(world, faction.faction_id),
            "general_per_grand_general": general_capacity_per_grand_general(world, faction.faction_id),
        }
        for faction in world.factions
    }
    return {
        "office_types": [
            {
                "id": office_type,
                "name": OFFICE_TYPE_LABELS[office_type],
                "workspace": OFFICE_WORKSPACES[office_type],
                "permissions": list(OFFICE_PERMISSIONS[office_type]),
                "duties": list(OFFICE_DUTY_TYPES[office_type]),
            }
            for office_type in ("lord", "grand_general", "general", "governor")
        ],
        "capacities_by_faction": capacities,
    }
