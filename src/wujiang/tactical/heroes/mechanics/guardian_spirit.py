"""Shared guardian/summoner lifecycle for the star guardian family."""

from __future__ import annotations

from typing import Any

from wujiang.tactical.engine.core import ActionError, Battle, Position, Skill, StatusEffect, Trait, Unit
from wujiang.tactical.heroes.common import payload_position
from wujiang.tactical.heroes.mechanics.next_five_geometry import square_around_cells


def present(unit: Unit | None) -> bool:
    return unit is not None and unit.alive and not unit.banished and unit.position is not None


def named_guardians(battle: Battle, player_id: int) -> list[Unit]:
    """The saved R344-Q001 answer matches displayed names, including clones."""
    return [unit for unit in battle.all_units()
            if present(unit) and unit.player_id == player_id and "守护灵" in unit.name]


class GuardianLegacyStatus(StatusEffect):
    def __init__(self) -> None:
        super().__init__("星宿继承", "攻、守、速各+1；可与其他星宿继承叠加。", duration=None)

    def modify_stat(self, stat_name: str, value: float) -> float:
        return value + 1 if stat_name in {"attack", "defense", "speed"} else value


class GuardianSummonerTrait(Trait):
    """One specific guardian's destruction kills its summoner; true death grants legacy."""

    requires_damage_choice = True
    ai_optional_choice_owned = True

    def __init__(self, guardian_code: str) -> None:
        super().__init__("守护灵牵系", "守护灵破坏时召唤者破坏；召唤者破坏时指定一名己方守护灵攻守速+1。")
        self.guardian_code = guardian_code

    def on_owned_summon_destroyed(self, battle: Battle, summon: Unit) -> None:
        owner = self.owner
        if owner is None or not owner.alive or summon.hero_code != self.guardian_code:
            return
        owner.alive = False
        battle.log(f"{summon.name} 破坏，牵系的 {owner.name} 同时破坏。")

    def on_unit_destroyed(self, battle: Battle, destroyed_unit: Unit) -> None:
        owner = self.owner
        if owner is None or destroyed_unit.unit_id != owner.unit_id:
            return
        options = named_guardians(battle, owner.player_id)
        if not options:
            return
        ids = [unit.unit_id for unit in options]
        choice = battle.optional_position_choice(
            owner, ids, action_name="星宿继承：选择一名己方守护灵", kind="guardian_legacy")
        target = battle.units.get(choice) if choice in ids else options[0]
        if not present(target):
            return
        target.add_status(GuardianLegacyStatus(), source=owner)
        battle.log(f"{target.name} 继承了 {owner.name} 的星宿之力，攻守速各+1。")

    def ai_optional_choice_score(self, battle: Battle, actor: Unit,
                                 prompt: dict[str, Any], choice: str) -> float | None:
        if prompt.get("kind") != "guardian_legacy" or actor is not self.owner:
            return None
        target = battle.units.get(choice)
        return (100 + target.stat("attack") * 4 + target.current_hp * 8) if present(target) else -100


class GuardianTetherTrait(Trait):
    """A 13×13 square follows the summoner's current body position."""

    def __init__(self) -> None:
        super().__init__("守护灵范围", "离开召唤者周围13×13时立即破坏。")

    def on_unit_moved(self, battle: Battle, ctx: Any) -> None:
        guardian = self.owner
        if guardian is None or not present(guardian):
            return
        if ctx.unit.unit_id not in {guardian.unit_id, guardian.summoner_id}:
            return
        summoner = battle.units.get(guardian.summoner_id or "")
        if not present(summoner):
            return
        if guardian.position.distance_to(summoner.position) <= 6:
            return
        guardian.alive = False
        battle.log(f"{guardian.name} 离开召唤者周围13×13，立即破坏。")


class GuardianSummonerManaTrait(Trait):
    """The guardian casts its own skills while its living summoner funds them."""

    def __init__(self) -> None:
        super().__init__("召唤者供魔", "守护灵施放有魔耗的技能时由召唤者支付魔力。")

    def skill_mana_payer(self, battle: Battle, actor: Unit, skill: Skill) -> Unit | None:
        if actor is not self.owner or not actor.summoner_id:
            return None
        summoner = battle.units.get(actor.summoner_id)
        return summoner if present(summoner) else None


def adjacent_legal_cells(battle: Battle, unit: Unit, anchor: Unit) -> list[Position]:
    if not present(anchor):
        return []
    cells = {cell for body in battle.unit_cells(anchor) for cell in battle.neighbors(body)}
    return sorted((cell for cell in cells if battle.can_place_unit(unit, cell, mover=unit)),
                  key=lambda cell: (cell.y, cell.x))


class SummonGuardianSkill(Skill):
    """The shared once-per-battle entry rule; each summoner supplies its guardian."""

    ai_payload_value_owned = True
    ai_enemy_impact_requirement = False

    def __init__(self, code: str, name: str, guardian_type: type[Unit]) -> None:
        super().__init__(code, name, "每场一次；在本人周围合法空格召唤专属守护灵。",
                         max_uses_per_battle=1, target_mode="cell")
        self.guardian_type = guardian_type

    def available_cells(self, battle: Battle, actor: Unit) -> list[Position]:
        if any(unit.summoner_id == actor.unit_id and unit.hero_code == self.guardian_type.hero_code
               and unit.alive for unit in battle.all_units()):
            return []
        return [cell for cell in square_around_cells(battle, battle.unit_cells(actor), radius=1)
                if cell not in battle.unit_cells(actor)
                and battle.can_place_unit(actor, cell, mover=actor)]

    def preview(self, battle: Battle, actor: Unit) -> dict[str, Any]:
        return {"cells": [cell.to_dict() for cell in self.available_cells(battle, actor)],
                "target_unit_ids": [], "secondary_cells": [cell.to_dict() for cell in battle.unit_cells(actor)],
                "requires_target": True}

    def get_target_cells_for_payload(self, battle: Battle, actor: Unit,
                                     payload: dict[str, Any]) -> list[Position]:
        return [payload_position(payload)]

    def execute(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> None:
        cell = payload_position(payload)
        if cell not in self.available_cells(battle, actor):
            raise ActionError("请选择本人周围合法的守护灵落格。")
        battle.summon_unit(self.guardian_type(actor.player_id), cell, summoner=actor)

    def ai_payload_value(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> float | None:
        try:
            cell = payload_position(payload)
        except (KeyError, TypeError, ValueError):
            return -100
        if cell not in self.available_cells(battle, actor):
            return -100
        enemies = [unit for unit in battle.all_units() if present(unit) and unit.player_id != actor.player_id]
        distance = min((cell.distance_to(unit.position) for unit in enemies), default=4)
        return 75 - min(40, distance * 6)

    def ai_priority_score(self, context: Any) -> float:
        return self.ai_payload_value(context.battle, context.actor, context.payload) or -100
