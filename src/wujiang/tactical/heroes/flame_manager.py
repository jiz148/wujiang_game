from __future__ import annotations

from typing import Any

from wujiang.tactical.engine.core import (
    ActionError, Battle, BattleFieldEffect, DamageContext, HeroUnit, Position,
    Skill, Stats, StatusEffect, TargetContext, Trait, Unit,
)
from wujiang.tactical.heroes.base import AbstractHero
from wujiang.tactical.heroes.common import LightWallSkill, payload_position, payload_target_unit, positions_to_dict
from wujiang.tactical.heroes.next_five import square_around_cells


def elemental_hero_family(battle: Battle, unit: Unit, attribute: str) -> bool:
    """A hero and its summons share the hero's elemental family, not nearby allies'."""
    current = unit
    seen: set[str] = set()
    while current.is_summon and current.summoner_id and current.unit_id not in seen:
        seen.add(current.unit_id)
        parent = battle.units.get(current.summoner_id)
        if parent is None:
            return False
        current = parent
    return not current.is_summon and not current.is_clone and current.attribute == attribute


class FireWallSkill(LightWallSkill):
    def __init__(self) -> None:
        super().__init__()
        self.code = "fire_wall"
        self.name = "火墙"
        self.description = "连锁防护；按未写专属参数的墙技能规则，1魔为受影响己方单位放置1层仅本次连锁有效的护盾。"


class ChimeraAura(BattleFieldEffect):
    def __init__(self, source_id: str) -> None:
        super().__init__("奇美拉元素光环", "外邻炎属性武将及其召唤物攻+1，水属性武将及其召唤物攻-1，最低1。")
        self.source_unit_id = source_id

    def source(self, battle: Battle) -> Unit | None:
        unit = battle.units.get(self.source_unit_id)
        return unit if unit is not None and unit.alive and not unit.banished and unit.position is not None else None

    def affected_cells(self, battle: Battle) -> list[Position]:
        source = self.source(battle)
        return square_around_cells(battle, battle.unit_cells(source), radius=1) if source is not None else []

    def modify_unit_stat(self, battle: Battle, unit: Unit, stat_name: str, value: float) -> float:
        source = self.source(battle)
        if (stat_name != "attack" or source is None or unit is source or not unit.alive
                or unit.banished or unit.position is None or battle.distance_between_units(source, unit) != 1):
            return value
        if elemental_hero_family(battle, unit, "炎"):
            return value + 1
        if elemental_hero_family(battle, unit, "水"):
            return max(1, value - 1)
        return value


class ChimeraAuraTrait(Trait):
    def __init__(self) -> None:
        super().__init__("奇美拉元素光环", "外邻炎攻+1、水攻-1到1；多只奇美拉分别生效。")
        self.field: ChimeraAura | None = None

    def on_enter_battle(self, battle: Battle) -> None:
        if self.owner is not None:
            self.field = ChimeraAura(self.owner.unit_id)
            battle.add_field_effect(self.field, source=self.owner)

    def on_owner_removed(self, battle: Battle) -> None:
        if self.field is not None:
            battle.remove_field_effect(self.field)
            self.field = None


class ChimeraSummon(AbstractHero):
    hero_code = "flame_chimera"
    hero_name = "奇美拉"
    role = "召唤物"
    attribute = "炎"
    race = "兽"
    level = 1
    base_stats = Stats(attack=1, defense=1, speed=3, attack_range=2, mana=0)
    raw_skill_text = ""
    raw_trait_text = "奇美拉元素光环"

    def __init__(self, player_id: int) -> None:
        super().__init__(player_id, is_summon=True)

    def build_skills(self) -> list[Skill]:
        return []

    def build_traits(self) -> list[Trait]:
        return [ChimeraAuraTrait()]


def own_chimeras(battle: Battle, actor: Unit) -> list[Unit]:
    return [unit for unit in battle.player_units(actor.player_id)
            if unit.hero_code == "flame_chimera" and unit.summoner_id == actor.unit_id
            and unit.alive and unit.position is not None and not unit.banished]


class ChimeraSkill(Skill):
    def __init__(self) -> None:
        super().__init__("chimera", "奇美拉", "1魔；每本人轮首只免费，选择外邻合法空格召唤奇美拉。",
                         mana_cost=1, target_mode="cell")

    def mana_cost_for_payload(self, battle: Battle, actor: Unit,
                              payload: dict[str, Any] | None = None) -> float:
        self.sync_turn_scope(battle)
        return 0.0 if self.uses_this_turn == 0 else super().mana_cost_for_payload(battle, actor, payload)

    def available_cells(self, battle: Battle, actor: Unit) -> list[Position]:
        if actor.position is None:
            return []
        probe = ChimeraSummon(actor.player_id)
        return [cell for cell in square_around_cells(battle, battle.unit_cells(actor), radius=1)
                if battle.can_place_unit(probe, cell)]

    def can_use(self, battle: Battle, actor: Unit,
                payload: dict[str, Any] | None = None) -> tuple[bool, str]:
        ok, reason = super().can_use(battle, actor, payload)
        if not ok:
            return ok, reason
        cells = self.available_cells(battle, actor)
        if not cells:
            return False, "周围没有合法空格召唤奇美拉。"
        if payload and payload.get("x") is not None and payload_position(payload) not in cells:
            return False, "奇美拉落点不合法。"
        return True, ""

    def execute(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> None:
        cell = payload_position(payload)
        if cell not in self.available_cells(battle, actor):
            raise ActionError("请选择外邻合法空格召唤奇美拉。")
        battle.summon_unit(ChimeraSummon(actor.player_id), cell, summoner=actor)

    def preview(self, battle: Battle, actor: Unit) -> dict[str, Any]:
        return {"cells": positions_to_dict(self.available_cells(battle, actor)),
                "target_unit_ids": [], "secondary_cells": [], "requires_target": True}

    def get_target_cells_for_payload(self, battle: Battle, actor: Unit,
                                     payload: dict[str, Any]) -> list[Position]:
        return [payload_position(payload)]


class FlameManagerAttackCountTrait(Trait):
    def __init__(self) -> None:
        super().__init__("火阵连击", "本人进攻回合开始时按周围7×7己方炎将及其召唤物数增加当轮普攻次数。")
        self.bonus = 0

    def on_owner_turn_start(self, battle: Battle) -> None:
        owner = self.owner
        if owner is None or owner.position is None:
            self.bonus = 0
            return
        self.bonus = sum(1 for unit in battle.player_units(owner.player_id)
                         if unit.unit_id != owner.unit_id and unit.alive and unit.position is not None
                         and not unit.banished and battle.distance_between_units(owner, unit) <= 3
                         and elemental_hero_family(battle, unit, "炎"))
        battle.log_public_event(f"{owner.name} 火阵连击：本轮额外普攻 {self.bonus} 次。", source=owner, target=owner)

    def modify_attack_actions_per_turn(self, value: int) -> int:
        return value + self.bonus

    def on_owner_turn_end(self, battle: Battle) -> None:
        self.bonus = 0

    def to_public_dict(self, battle: Battle) -> dict[str, Any]:
        data = super().to_public_dict(battle)
        data["bonus_attacks_this_turn"] = self.bonus
        return data


class FlamePasswordPierce(StatusEffect):
    def __init__(self, created_turn: int) -> None:
        super().__init__("火焰密码·普攻破魔", "本轮普攻破魔。")
        self.created_turn = created_turn
        self.is_skill_effect = True

    def _matches(self, ctx: TargetContext | DamageContext) -> bool:
        owner = self.owner
        source = ctx.actor if isinstance(ctx, TargetContext) else ctx.source
        return bool(owner is not None and source is not None and owner.unit_id == source.unit_id
                    and not ctx.is_skill and "attack" in ctx.tags)

    def on_targeted(self, battle: Battle, ctx: TargetContext) -> None:
        if self._matches(ctx):
            ctx.ignore_shield = True

    def on_before_damage(self, battle: Battle, ctx: DamageContext) -> None:
        if self._matches(ctx):
            ctx.ignore_shield = True

    def on_any_turn_end(self, battle: Battle, ended_player_id: int) -> None:
        if self.owner is not None and battle.turn_number >= self.created_turn:
            self.owner.remove_status(self, battle)


class FlamePasswordSkill(Skill):
    requires_direct_unit_target_line = False

    def __init__(self) -> None:
        super().__init__("flame_password", "火焰密码", "破坏一只己方奇美拉，本轮本人与周围7×7炎属性友方普攻破魔。",
                         target_mode="unit")

    def can_use(self, battle: Battle, actor: Unit,
                payload: dict[str, Any] | None = None) -> tuple[bool, str]:
        ok, reason = super().can_use(battle, actor, payload)
        if not ok:
            return ok, reason
        targets = own_chimeras(battle, actor)
        if not targets:
            return False, "没有可破坏的己方奇美拉。"
        if payload and payload.get("target_unit_id") and str(payload["target_unit_id"]) not in {unit.unit_id for unit in targets}:
            return False, "只能选择自己召唤的奇美拉。"
        return True, ""

    def execute(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> None:
        target = payload_target_unit(battle, payload)
        if target not in own_chimeras(battle, actor):
            raise ActionError("请选择自己召唤的在场奇美拉。")
        target.current_hp = 0
        target.alive = False
        battle.log_public_event(f"{actor.name} 用火焰密码破坏了己方{target.name}。", source=actor, target=target)
        battle.cleanup_dead_units()
        if target.alive:
            return
        for unit in list(battle.player_units(actor.player_id)):
            if (unit.alive and unit.position is not None and not unit.banished
                    and (unit is actor or (actor.position is not None
                                           and battle.distance_between_units(actor, unit) <= 3
                                           and elemental_hero_family(battle, unit, "炎")))):
                for old in list(unit.statuses):
                    if isinstance(old, FlamePasswordPierce):
                        unit.remove_status(old, battle)
                unit.add_status(FlamePasswordPierce(battle.turn_number), source=actor)
        battle.log_public_event(f"{actor.name} 牺牲奇美拉，炎属性队伍本轮普攻破魔。", source=actor, target=actor)

    def preview(self, battle: Battle, actor: Unit) -> dict[str, Any]:
        targets = own_chimeras(battle, actor)
        return {"cells": positions_to_dict([unit.position for unit in targets if unit.position is not None]),
                "target_unit_ids": [unit.unit_id for unit in targets], "secondary_cells": [],
                "requires_target": True}

    def get_target_units_for_payload(self, battle: Battle, actor: Unit,
                                     payload: dict[str, Any]) -> list[Unit]:
        return [payload_target_unit(battle, payload)]
