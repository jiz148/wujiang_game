"""R346: Li Kui's guardian spends his mana and commands the nearby formation."""

from __future__ import annotations

from typing import Any

from wujiang.tactical.engine.core import ActionError, Battle, Skill, Stats, StatusEffect, Trait, Unit
from wujiang.tactical.heroes.base import AbstractHero
from wujiang.tactical.heroes.common import AttackCountTrait, HardenSkill
from wujiang.tactical.heroes.mechanics.dragon_breath import DragonBreathSkill
from wujiang.tactical.heroes.mechanics.guardian_spirit import (
    GuardianSummonerManaTrait, GuardianTetherTrait, SummonGuardianSkill, present,
)


def _adjacent(battle: Battle, actor: Unit, unit: Unit) -> bool:
    return present(unit) and battle.distance_between_units(actor, unit) <= 1


class TurnAttackBonus(StatusEffect):
    def __init__(self) -> None:
        super().__init__("天河吼", "本实际回合攻击次数+1。", duration=None)

    def modify_attack_actions_per_turn(self, value: int) -> int:
        return value + 1

    def on_any_turn_end(self, battle: Battle, ended_player_id: int) -> None:
        if self.owner is not None:
            self.owner.remove_status(self, battle)


class NextEnemyTurnDefense(StatusEffect):
    def __init__(self, friendly_player_id: int) -> None:
        super().__init__("生魂吼", "守+1，直到下个对方武将回合结束。", duration=None)
        self.friendly_player_id = friendly_player_id

    def modify_stat(self, stat_name: str, value: float) -> float:
        return value + 1 if stat_name == "defense" else value

    def on_any_turn_end(self, battle: Battle, ended_player_id: int) -> None:
        if self.owner is not None and ended_player_id != self.friendly_player_id and not battle.is_army_turn():
            self.owner.remove_status(self, battle)


class TurnMoveBonus(StatusEffect):
    def __init__(self) -> None:
        super().__init__("占星术。离", "本实际回合普通移动次数+1。", duration=None)

    def modify_normal_move_actions_per_turn(self, value: int) -> int:
        return value + 1

    def on_any_turn_end(self, battle: Battle, ended_player_id: int) -> None:
        if self.owner is not None:
            self.owner.remove_status(self, battle)


class TianshaHardenSkill(HardenSkill):
    """A source-required option that the AI should not spend Li Kui's mana on."""

    ai_payload_value_owned = True
    ai_enemy_impact_requirement = False

    def ai_payload_value(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> float | None:
        return -100.0 if actor.stat("defense") >= 999 else 10.0

    def ai_priority_score(self, context: Any) -> float:
        return -1000.0 if context.actor.stat("defense") >= 999 else self.ai_self_buff_score(context)


class TianshaGuardian(AbstractHero):
    hero_code = "guardian_tiansha"
    hero_name = "守护灵。天杀"
    role = "召唤物"
    attribute = "土"
    race = "守护灵"
    level = 1
    base_stats = Stats(attack=4, defense=999, speed=5, attack_range=1, mana=0)

    def __init__(self, player_id: int) -> None:
        super().__init__(player_id, is_summon=True)

    def build_skills(self) -> list[Skill]:
        return [DragonBreathSkill(), TianshaHardenSkill(), HeavenlyRiverRoarSkill(),
                LivingSoulRoarSkill(), StarDepartureSkill()]

    def build_traits(self) -> list[Trait]:
        return [GuardianTetherTrait(), GuardianSummonerManaTrait(), AttackCountTrait(2)]


class SummonTianshaSkill(SummonGuardianSkill):
    def __init__(self) -> None:
        super().__init__("summon_guardian_tiansha", "守护灵。天杀", TianshaGuardian)
        self.description = "每场一次；在本人周围召唤攻4、守无限、速5、范1、攻2次的天杀守护灵。"


class HeavenlyRiverRoarSkill(Skill):
    ai_payload_value_owned = True
    ai_enemy_impact_requirement = False

    def __init__(self) -> None:
        super().__init__("heavenly_river_roar", "天河吼",
                         "每回合一次；自身、周围己方守护灵及相邻召唤者本回合攻击次数+1。",
                         max_uses_per_turn=1, target_mode="self")

    def recipients(self, battle: Battle, actor: Unit) -> list[Unit]:
        result = [actor]
        for unit in battle.all_units():
            if unit is actor or unit.player_id != actor.player_id or not _adjacent(battle, actor, unit):
                continue
            if "守护灵" in unit.name or unit.unit_id == actor.summoner_id:
                result.append(unit)
        return result

    def preview(self, battle: Battle, actor: Unit) -> dict[str, Any]:
        units = self.recipients(battle, actor)
        return {"cells": [cell.to_dict() for unit in units for cell in battle.unit_cells(unit)],
                "target_unit_ids": [unit.unit_id for unit in units], "secondary_cells": [],
                "requires_target": False}

    def get_target_units_for_payload(self, battle: Battle, actor: Unit,
                                     payload: dict[str, Any]) -> list[Unit]:
        return self.recipients(battle, actor)

    def execute(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> None:
        for unit in self.recipients(battle, actor):
            unit.add_status(TurnAttackBonus(), source=actor)
            battle.log(f"{unit.name} 受到天河吼鼓舞，本回合可多普攻一次。")

    def ai_payload_value(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> float | None:
        value = 0.0
        enemies = [unit for unit in battle.all_units() if present(unit) and unit.player_id != actor.player_id]
        for unit in self.recipients(battle, actor):
            if unit.attacks_used >= unit.attack_actions_per_turn() and not any(
                    battle.unit_target_in_range_and_line(unit, enemy, unit.targeting_range()) for enemy in enemies):
                continue
            reachable = any(battle.unit_target_in_range_and_line(unit, enemy, unit.targeting_range()) for enemy in enemies)
            value += 75.0 if reachable else 15.0
        return value - 10.0

    def ai_priority_score(self, context: Any) -> float:
        return self.ai_payload_value(context.battle, context.actor, context.payload) or -100


class LivingSoulRoarSkill(Skill):
    ai_payload_value_owned = True
    ai_enemy_impact_requirement = False

    def __init__(self) -> None:
        super().__init__("living_soul_roar", "生魂吼",
                         "每回合一次；周围己方单位守+1，直到下个对方武将回合结束。",
                         max_uses_per_turn=1, target_mode="self")

    def recipients(self, battle: Battle, actor: Unit) -> list[Unit]:
        return [unit for unit in battle.all_units()
                if unit is not actor and unit.player_id == actor.player_id and _adjacent(battle, actor, unit)]

    def preview(self, battle: Battle, actor: Unit) -> dict[str, Any]:
        units = self.recipients(battle, actor)
        return {"cells": [cell.to_dict() for unit in units for cell in battle.unit_cells(unit)],
                "target_unit_ids": [unit.unit_id for unit in units], "secondary_cells": [],
                "requires_target": False}

    def get_target_units_for_payload(self, battle: Battle, actor: Unit,
                                     payload: dict[str, Any]) -> list[Unit]:
        return self.recipients(battle, actor)

    def can_use(self, battle: Battle, actor: Unit,
                payload: dict[str, Any] | None = None) -> tuple[bool, str]:
        ok, reason = super().can_use(battle, actor, payload)
        if not ok:
            return ok, reason
        return (True, "") if self.recipients(battle, actor) else (False, "周围没有己方单位。")

    def execute(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> None:
        for unit in self.recipients(battle, actor):
            unit.add_status(NextEnemyTurnDefense(actor.player_id), source=actor)
            battle.log(f"{unit.name} 因生魂吼守+1，直到下个对方回合结束。")

    def ai_payload_value(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> float | None:
        return sum(34.0 if unit.current_hp <= 1 else 18.0
                   for unit in self.recipients(battle, actor) if not unit.has_status("生魂吼")) - 8.0

    def ai_priority_score(self, context: Any) -> float:
        return self.ai_payload_value(context.battle, context.actor, context.payload) or -100


class StarDepartureSkill(Skill):
    ai_payload_value_owned = True
    ai_enemy_impact_requirement = False

    def __init__(self) -> None:
        super().__init__("star_departure", "占星术。离",
                         "每场一次；选择周围名字带守护灵的单位或召唤者，本回合普通移动次数+1。",
                         max_uses_per_battle=1, target_mode="unit")

    def targets(self, battle: Battle, actor: Unit) -> list[Unit]:
        return [unit for unit in battle.all_units() if unit is not actor and _adjacent(battle, actor, unit)
                and ("守护灵" in unit.name or unit.unit_id == actor.summoner_id)]

    def preview(self, battle: Battle, actor: Unit) -> dict[str, Any]:
        targets = self.targets(battle, actor)
        return {"cells": [cell.to_dict() for unit in targets for cell in battle.unit_cells(unit)],
                "target_unit_ids": [unit.unit_id for unit in targets], "secondary_cells": [],
                "requires_target": True}

    def get_target_units_for_payload(self, battle: Battle, actor: Unit,
                                     payload: dict[str, Any]) -> list[Unit]:
        target = battle.units.get(str(payload.get("target_unit_id") or ""))
        return [target] if target in self.targets(battle, actor) else []

    def can_use(self, battle: Battle, actor: Unit,
                payload: dict[str, Any] | None = None) -> tuple[bool, str]:
        ok, reason = super().can_use(battle, actor, payload)
        if not ok:
            return ok, reason
        if payload and payload.get("target_unit_id") and not self.get_target_units_for_payload(battle, actor, payload):
            return False, "目标不在周围，或不是守护灵或召唤者。"
        return (True, "") if self.targets(battle, actor) else (False, "没有合法的占星术目标。")

    def execute(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> None:
        targets = self.get_target_units_for_payload(battle, actor, payload)
        if not targets:
            raise ActionError("请选择周围合法的占星术目标。")
        target = targets[0]
        target.add_status(TurnMoveBonus(), source=actor)
        battle.log(f"{target.name} 因占星术。离本回合可多普通移动一次。")

    def ai_payload_value(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> float | None:
        targets = self.get_target_units_for_payload(battle, actor, payload)
        if not targets or targets[0].player_id != actor.player_id:
            return -100.0
        target = targets[0]
        if target.normal_move_actions_used < target.normal_move_actions_per_turn() or target.cannot_move:
            return -80.0
        enemies = [unit for unit in battle.all_units() if present(unit) and unit.player_id != actor.player_id]
        distance = min((battle.distance_between_units(target, enemy) for enemy in enemies), default=8)
        return 70.0 if distance <= target.normal_move_distance() + target.targeting_range() else 20.0

    def ai_priority_score(self, context: Any) -> float:
        return self.ai_payload_value(context.battle, context.actor, context.payload) or -100
