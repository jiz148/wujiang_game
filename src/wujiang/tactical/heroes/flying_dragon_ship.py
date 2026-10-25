from __future__ import annotations

from copy import copy
from typing import Any

from wujiang.tactical.engine.core import (
    ActionError, Battle, DamageContext, HealContext, Position, QueuedAction,
    Skill, Stats, StatusEffect, Trait, Unit,
)
from wujiang.tactical.heroes.base import AbstractHero
from wujiang.tactical.heroes.common import (
    FlyingTrait, HealSkill, MagicImmunityStatus, StatModifierStatus,
    ensure_distance, payload_target_unit, positions_to_dict,
)
from wujiang.tactical.heroes.next_five import RecoverManaSkill, damage_followup_effect_applies


SHIP_CODE = "flying_dragon_ship_atlanta"
CREW_NAME = "骷髅飞盗团"


def allied_ship(battle: Battle, player_id: int) -> Unit | None:
    return next((unit for unit in battle.all_units()
                 if unit.hero_code == SHIP_CODE and unit.player_id == player_id
                 and unit.alive and not unit.banished and unit.position is not None), None)


class ShipCrewAttackTrait(Trait):
    def __init__(self) -> None:
        super().__init__("飞龙船员攻次", "参谋在船上时，该船每回合增加一次普攻。")

    def modify_attack_actions_per_turn(self, value: int) -> int:
        ship = self.owner
        battle_ref = getattr(ship, "_battle_ref", None)
        battle = battle_ref() if battle_ref is not None else None
        if battle is None or ship is None:
            return value
        return value + sum(rider.hero_code == "excel_r329" for rider in battle.riders_for(ship))


class FlyingDragonShip(AbstractHero):
    hero_code = SHIP_CODE
    hero_name = "飞龙船。亚特兰大"
    role = "召唤物"
    attribute = ""
    race = ""
    level = 1
    base_stats = Stats(attack=3, defense=5, speed=4, attack_range=3, mana=0)
    footprint_width = 2
    footprint_height = 3
    raw_trait_text = "飞行；2×3；可被多名己方单位乘骑；每回合仅一名船上单位可对船使用被动技能。"

    def __init__(self, player_id: int) -> None:
        super().__init__(player_id, is_summon=True)
        self.is_mount = True
        self.allows_multiple_riders = True
        self.ship_passive_claim_turn = -1
        self.ship_passive_claim_rider_id: str | None = None
        self.borrowed_turn_by_rider: dict[str, int] = {}

    def build_skills(self) -> list[Skill]:
        return []

    def build_traits(self) -> list[Trait]:
        return [FlyingTrait(), ShipCrewAttackTrait()]

    def action_skills(self) -> list[Skill]:
        battle_ref = getattr(self, "_battle_ref", None)
        battle = battle_ref() if battle_ref is not None else None
        if battle is None:
            return super().action_skills()
        aboard = {rider.unit_id for rider in battle.riders_for(self)}
        return [skill for skill in super().action_skills()
                if not isinstance(skill, ShipBorrowedSkill) or skill.rider_id in aboard]

    def on_rider_changed(self, battle: Battle) -> None:
        existing = {skill.code for skill in self.skills}
        for rider in battle.riders_for(self):
            if not any(trait.name == "飞龙船代施" for trait in rider.traits):
                continue
            for source_skill in rider.skills:
                if source_skill.mana_cost <= 0 or source_skill.code == "flying_dragon_ship":
                    continue
                code = f"ship_borrow__{rider.unit_id}__{source_skill.code}"
                if code not in existing:
                    self.skills.append(ShipBorrowedSkill(rider.unit_id, source_skill).bind(self))
                    existing.add(code)

    def to_public_dict(self, battle: Battle) -> dict[str, Any]:
        data = super().to_public_dict(battle)
        data["ship_passive_rider_id"] = (
            self.ship_passive_claim_rider_id if self.ship_passive_claim_turn == battle.turn_number else None
        )
        data["ship_borrowed_rider_ids"] = [
            rider_id for rider_id, turn in self.borrowed_turn_by_rider.items()
            if turn == battle.turn_number
        ]
        return data


_SHIP_PLACEMENT_PROBES = {player_id: FlyingDragonShip(player_id) for player_id in (1, 2)}


class ShipBorrowedSkill(Skill):
    def __init__(self, rider_id: str, source_skill: Skill) -> None:
        super().__init__(
            f"ship_borrow__{rider_id}__{source_skill.code}",
            f"船代施·{source_skill.name}",
            f"由船上乘员每回合额外免费代施一次：{source_skill.description}",
            mana_cost=0, target_mode=source_skill.target_mode,
            timing=source_skill.timing, direction_mode=source_skill.direction_mode,
            passive=source_skill.passive, max_uses_per_turn=1,
        )
        self.rider_id = rider_id
        self.source_skill = source_skill
        self.inner = copy(source_skill)
        self.inner.mana_cost = 0
        self.inner.max_uses_per_turn = None
        self.inner.max_uses_per_battle = None
        self.inner.cooldown_turns = 0
        self.inner.cooldown_remaining = 0
        self.requires_direct_unit_target_line = source_skill.requires_direct_unit_target_line
        self.target_unit_is_anchor = source_skill.target_unit_is_anchor
        self.target_cells_are_geometry_only = source_skill.target_cells_are_geometry_only
        self.selection_cells_are_effect_cells = source_skill.selection_cells_are_effect_cells

    def bind(self, owner: Unit) -> "ShipBorrowedSkill":
        super().bind(owner)
        self.inner.bind(owner)
        return self

    def to_public_dict(self, battle: Battle) -> dict[str, Any]:
        data = super().to_public_dict(battle)
        data["borrowed_from_unit_id"] = self.rider_id
        data["source_skill_code"] = self.source_skill.code
        data["cooldown_remaining"] = self.source_skill.cooldown_remaining
        return data

    def _check_source(self, battle: Battle, actor: Unit) -> tuple[bool, str]:
        rider = battle.units.get(self.rider_id)
        if (rider is None or not rider.alive or rider.banished or rider.position is None
                or rider.mounted_on_unit_id != actor.unit_id):
            return False, "提供技能的乘员已离船。"
        if self.source_skill.cooldown_remaining > 0:
            return False, "乘员原技能仍在冷却。"
        if actor.borrowed_turn_by_rider.get(self.rider_id) == battle.turn_number:
            return False, "该乘员本回合的船代施机会已用。"
        return True, ""

    def can_use(self, battle: Battle, actor: Unit, payload: dict[str, Any] | None = None) -> tuple[bool, str]:
        ok, reason = super().can_use(battle, actor, payload)
        if not ok:
            return ok, reason
        ok, reason = self._check_source(battle, actor)
        return (ok, reason) if not ok else self.inner.can_use(battle, actor, payload)

    def can_react_to(self, battle: Battle, actor: Unit, queued_action: QueuedAction) -> tuple[bool, str]:
        ok, reason = super().can_react_to(battle, actor, queued_action)
        if not ok:
            return ok, reason
        ok, reason = self._check_source(battle, actor)
        return (ok, reason) if not ok else self.inner.can_react_to(battle, actor, queued_action)

    def can_react_with_payload(self, battle: Battle, actor: Unit, queued_action: QueuedAction,
                               payload: dict[str, Any] | None = None) -> tuple[bool, str]:
        ok, reason = self.can_react_to(battle, actor, queued_action)
        return (ok, reason) if not ok else self.inner.can_react_with_payload(battle, actor, queued_action, payload)

    def prepay_resources(self, battle: Battle, actor: Unit, payload: dict[str, Any] | None = None) -> None:
        super().prepay_resources(battle, actor, payload)
        actor.borrowed_turn_by_rider[self.rider_id] = battle.turn_number

    def execute(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> None:
        self.inner.execute(battle, actor, payload)

    def react(self, battle: Battle, actor: Unit, payload: dict[str, Any], queued_action: QueuedAction) -> None:
        self.inner.react(battle, actor, payload, queued_action)

    def preview(self, battle: Battle, actor: Unit) -> dict[str, Any]:
        return self.inner.preview(battle, actor)

    def reaction_preview(self, battle: Battle, actor: Unit, queued_action: QueuedAction) -> dict[str, Any]:
        return self.inner.reaction_preview(battle, actor, queued_action)

    def queued_payload_metadata(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> dict[str, Any]:
        return self.inner.queued_payload_metadata(battle, actor, payload)

    def queued_reaction_payload_metadata(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> dict[str, Any]:
        return self.inner.queued_reaction_payload_metadata(battle, actor, payload)

    def get_target_units_for_payload(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> list[Unit]:
        return self.inner.get_target_units_for_payload(battle, actor, payload)

    def get_target_cells_for_payload(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> list[Position]:
        return self.inner.get_target_cells_for_payload(battle, actor, payload)


class FlyingDragonShipSkill(Skill):
    def __init__(self) -> None:
        super().__init__("flying_dragon_ship", "飞龙船。亚特兰大",
                         "0魔大招、每场一次；在身边完整合法的2×3区域召唤己方唯一飞行船。",
                         max_uses_per_battle=1, target_mode="cell")

    def destinations(self, battle: Battle, actor: Unit) -> list[Position]:
        if actor.position is None or allied_ship(battle, actor.player_id) is not None:
            return []
        probe = _SHIP_PLACEMENT_PROBES[actor.player_id]
        source_cells = battle.unit_cells(actor)
        result: list[Position] = []
        for y in range(battle.height):
            for x in range(battle.width):
                anchor = Position(x, y)
                if (any(source.distance_to(body) == 1 for source in source_cells
                        for body in battle.unit_cells_at(probe, anchor))
                        and battle.can_place_unit(probe, anchor, mover=probe)):
                    result.append(anchor)
        return result

    def can_use(self, battle: Battle, actor: Unit, payload: dict[str, Any] | None = None) -> tuple[bool, str]:
        ok, reason = super().can_use(battle, actor, payload)
        if not ok:
            return ok, reason
        if allied_ship(battle, actor.player_id) is not None:
            return False, "己方已有飞龙船。"
        if not self.destinations(battle, actor):
            return False, "身边没有可放下完整船身的位置。"
        if payload and "x" in payload and "y" in payload:
            if Position(int(payload["x"]), int(payload["y"])) not in self.destinations(battle, actor):
                return False, "无法在该位置放置完整船身。"
        return True, ""

    def execute(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> None:
        anchor = Position(int(payload["x"]), int(payload["y"]))
        if anchor not in self.destinations(battle, actor):
            raise ActionError("飞龙船的召唤位置已失效。")
        battle.summon_unit(FlyingDragonShip(actor.player_id), anchor, summoner=actor)

    def preview(self, battle: Battle, actor: Unit) -> dict[str, Any]:
        anchors = self.destinations(battle, actor)
        return {"cells": positions_to_dict(anchors), "target_unit_ids": [],
                "secondary_cells": [], "requires_target": True,
                "ship_footprint": {"width": 2, "height": 3}}


class DepartureMagicImmunity(MagicImmunityStatus):
    def __init__(self, captain_id: str) -> None:
        super().__init__(source_name="启航号", duration=2)
        self.captain_id = captain_id
        self.captain_ends = 0

    def on_owner_turn_start(self, battle: Battle) -> None:
        return None

    def on_any_turn_end(self, battle: Battle, ended_player_id: int) -> None:
        if battle.current_turn_slot_unit_id() == self.captain_id:
            self.captain_ends += 1
            if self.captain_ends >= 2 and self.owner is not None:
                self.owner.remove_status(self, battle)


class DepartureSkill(Skill):
    directions = ((-1, -1), (-1, 0), (-1, 1), (0, -1), (0, 1), (1, -1), (1, 0), (1, 1))

    def __init__(self) -> None:
        super().__init__("departure_signal", "启航号", "3轮一次；己方飞龙船直线恰移5格，魔免至团长下一本人轮末。",
                         cooldown_turns=3, target_mode="cell")

    def routes(self, battle: Battle, actor: Unit) -> list[list[Position]]:
        ship = allied_ship(battle, actor.player_id)
        if ship is None or ship.position is None:
            return []
        routes: list[list[Position]] = []
        for dx, dy in self.directions:
            steps = [ship.position.offset(dx * distance, dy * distance) for distance in range(1, 6)]
            try:
                battle.explicit_path(ship, steps, max_distance=5, exact_distance=5,
                                     straight_only=True, ignore_units=True)
            except ActionError:
                continue
            routes.append(steps)
        return routes

    def can_use(self, battle: Battle, actor: Unit, payload: dict[str, Any] | None = None) -> tuple[bool, str]:
        ok, reason = super().can_use(battle, actor, payload)
        if not ok:
            return ok, reason
        if not self.routes(battle, actor):
            return False, "己方飞龙船没有完整合法的五格直线路线。"
        if payload and "x" in payload and "y" in payload:
            destination = Position(int(payload["x"]), int(payload["y"]))
            if all(route[-1] != destination for route in self.routes(battle, actor)):
                return False, "启航号落点必须是完整五格直线的合法船身。"
        return True, ""

    def execute(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> None:
        ship = allied_ship(battle, actor.player_id)
        destination = Position(int(payload["x"]), int(payload["y"]))
        route = next((route for route in self.routes(battle, actor) if route[-1] == destination), None)
        if ship is None or route is None:
            raise ActionError("启航号的船或五格路线已失效。")
        battle.move_unit(ship, destination, path=route, max_distance=5, exact_distance=5,
                         straight_only=True, ignore_units=True, via_skill=True)
        ship.add_status(DepartureMagicImmunity(actor.unit_id), source=actor)
        battle.log_public_event(f"{actor.name} 发出启航号，{ship.name} 载人直行五格并获得魔免。",
                                source=actor, target=ship)

    def preview(self, battle: Battle, actor: Unit) -> dict[str, Any]:
        routes = self.routes(battle, actor)
        return {"cells": positions_to_dict([route[-1] for route in routes]),
                "target_unit_ids": [ship.unit_id] if (ship := allied_ship(battle, actor.player_id)) else [],
                "secondary_cells": positions_to_dict([cell for route in routes for cell in route]),
                "requires_target": True, "routes": [positions_to_dict(route) for route in routes]}

    def get_target_units_for_payload(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> list[Unit]:
        ship = allied_ship(battle, actor.player_id)
        return [ship] if ship is not None else []


class AddManaSkill(RecoverManaSkill):
    def __init__(self) -> None:
        super().__init__()
        self.code = "add_mana"
        self.name = "加魔"
        self.description = "按回魔通用值：费1魔，范内直线单体魔+1。"


class AddHealthSkill(HealSkill):
    def __init__(self) -> None:
        super().__init__()
        self.code = "add_health"
        self.name = "加血"
        self.description = "按回血通用值：费1魔，每本人轮一次，己方单体回复1/4生命；暗、灵体、恶魔目标按通用反转。"


class DefenseOneStatus(StatModifierStatus):
    def __init__(self, source_id: str, created_turn: int) -> None:
        super().__init__("守+1", defense_delta=1, description="守+1，持续被使用单位两个本人回合。")
        self.source_id = source_id
        self.created_turn = created_turn
        self.owner_ends = 0

    def on_owner_turn_end(self, battle: Battle) -> None:
        if battle.turn_number == self.created_turn:
            return
        self.owner_ends += 1
        if self.owner_ends >= 2 and self.owner is not None:
            self.owner.remove_status(self, battle)


class DefenseOneSkill(Skill):
    def __init__(self) -> None:
        super().__init__("defense_one", "守+1", "0魔；范内直线单体守+1，持续该单位两个本人回合。", target_mode="unit")

    def execute(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> None:
        target = battle.effect_recipient(payload_target_unit(battle, payload))
        battle.require_unit_target_in_range_and_line(actor, target, actor.targeting_range())
        ctx = battle.validate_target(actor, target, action_name=self.name, is_skill=True,
                                     is_hostile=target.player_id != actor.player_id)
        if ctx.cancelled:
            battle.log(ctx.reason)
            return
        old = next((status for status in target.statuses
                    if isinstance(status, DefenseOneStatus) and status.source_id == actor.unit_id), None)
        if old is not None:
            target.remove_status(old, battle)
        target.add_status(DefenseOneStatus(actor.unit_id, battle.turn_number), source=actor)
        battle.log_public_event(f"{target.name} 守+1，持续其两个本人回合。", source=actor, target=target)

    def preview(self, battle: Battle, actor: Unit) -> dict[str, Any]:
        targets = [target for target in battle.all_units()
                   if target.alive and target.position is not None and actor.position is not None
                   and battle.unit_can_be_selected(target, actor=actor)[0]
                   and battle.unit_target_in_range_and_line(actor, target, actor.targeting_range())
                   and not any(isinstance(status, DefenseOneStatus) and status.source_id == actor.unit_id
                               for status in target.statuses)]
        return {"cells": positions_to_dict([cell for target in targets for cell in battle.unit_cells(target)]),
                "target_unit_ids": [target.unit_id for target in targets],
                "secondary_cells": [], "requires_target": True}


class SilverSignalFlareSkill(Skill):
    def __init__(self) -> None:
        super().__init__("silver_signal_flare", "银制的信号弹",
                         "费0.5魔，每本人轮一次；本人5×5内同队名字含骷髅飞盗团者本回合攻+1，不叠加。",
                         mana_cost=0.5, max_uses_per_turn=1, target_mode="self")

    def beneficiaries(self, battle: Battle, actor: Unit) -> list[Unit]:
        return [unit for unit in battle.player_units(actor.player_id)
                if unit.position is not None and CREW_NAME in unit.name
                and battle.distance_between_units(actor, unit) <= 2]

    def execute(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> None:
        for target in self.beneficiaries(battle, actor):
            if target.get_status("银制信号弹攻势") is not None:
                continue
            target.add_status(StatModifierStatus("银制信号弹攻势", attack_delta=1,
                                                duration=1, tick_scope="any_turn_end",
                                                description="当前实际武将回合攻+1，不叠加。"), source=actor)
            if target.get_status("银制信号弹攻势") is not None:
                battle.log_public_event(f"{target.name} 收到银制的信号弹，攻+1。", source=actor, target=target)

    def preview(self, battle: Battle, actor: Unit) -> dict[str, Any]:
        targets = self.beneficiaries(battle, actor)
        return {"cells": positions_to_dict([cell for target in targets for cell in battle.unit_cells(target)]),
                "target_unit_ids": [target.unit_id for target in targets],
                "secondary_cells": [], "requires_target": False}


class RutoMovementLock(StatusEffect):
    def __init__(self, created_turn: int) -> None:
        super().__init__("鲁托封位移", "下一个本人回合不能使用位移技能。")
        self.created_turn = created_turn
        self.owner_ends = 0

    def blocks_skill_use(self, battle: Battle, actor: Unit, skill: Skill) -> tuple[bool, str]:
        from wujiang.tactical.heroes.excel_roster import skill_has_movement_effect
        if actor is self.owner and skill_has_movement_effect(skill):
            return True, "被鲁托击中，下一个本人回合不能使用位移技能。"
        return False, ""

    def on_owner_turn_end(self, battle: Battle) -> None:
        if battle.turn_number != self.created_turn:
            self.owner_ends += 1
            if self.owner_ends >= 1 and self.owner is not None:
                self.owner.remove_status(self, battle)


class RutoAttackLockTrait(Trait):
    def __init__(self) -> None:
        super().__init__("鲁托普攻禁位移", "本人普攻真实命中后，目标下一本人回合不能使用位移技能。")

    def on_basic_attack_finished(self, battle: Battle, actor: Unit, payload: dict[str, Any],
                                 damage_contexts: list[DamageContext], missed: bool) -> None:
        if actor is not self.owner or missed:
            return
        for ctx in damage_contexts:
            if not damage_followup_effect_applies(ctx) or not ctx.target.alive:
                continue
            old = ctx.target.get_status("鲁托封位移")
            if old is not None:
                ctx.target.remove_status(old, battle)
            ctx.target.add_status(RutoMovementLock(battle.turn_number), source=actor)
            battle.log_public_event(f"{ctx.target.name} 被鲁托普攻命中，下本人回合不能使用位移技能。",
                                    source=actor, target=ctx.target)


class ShipBorrowPermissionTrait(Trait):
    def __init__(self) -> None:
        super().__init__("飞龙船代施", "乘骑飞龙船时，船每实际武将回合可额外免费代施本人一项费魔技能。")


class RutoShipAttackTrait(Trait):
    def __init__(self) -> None:
        super().__init__("参谋增援船攻次", "参谋在飞龙船上时，船动态多一次普攻。")
