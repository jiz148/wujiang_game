"""R290 messenger-crow skills, summon, aura, and attack trait."""

from __future__ import annotations

from typing import Any

from wujiang.tactical.engine.core import (
    ActionError, Battle, BattleFieldEffect, DamageContext, MoveContext,
    Position, Skill, Stats, Trait, Unit,
)
from wujiang.tactical.heroes.base import AbstractHero
from wujiang.tactical.heroes.common import (
    FlyingTrait, match_payload_pattern, pattern_selection_preview,
    payload_position, payload_target_unit, positions_to_dict,
)
from wujiang.tactical.heroes.next_five import DeclaredAreaSkillMixin, square_around_cells

class MessengerCrowAura(BattleFieldEffect):
    def __init__(self, crow_id: str) -> None:
        super().__init__("报信乌鸦威压", "外邻敌方单位速-1至最低1，不能使用本次实际费用0的技能。")
        self.crow_id = crow_id
        self.source_unit_id = crow_id

    def crow(self, battle: Battle) -> Unit | None:
        unit = battle.units.get(self.crow_id)
        return unit if unit is not None and unit.alive and not unit.banished and unit.position is not None else None

    def covers(self, battle: Battle, unit: Unit) -> bool:
        crow = self.crow(battle)
        return bool(crow is not None and unit.alive and unit.position is not None
                    and unit.player_id != crow.player_id and battle.distance_between_units(crow, unit) == 1)

    def affected_cells(self, battle: Battle) -> list[Position]:
        crow = self.crow(battle)
        return square_around_cells(battle, battle.unit_cells(crow), radius=1) if crow is not None else []

    def modify_unit_stat(self, battle: Battle, unit: Unit, stat_name: str, value: float) -> float:
        if stat_name != "speed" or not self.covers(battle, unit):
            return value
        first = next((effect for effect in battle.field_effects
                      if isinstance(effect, MessengerCrowAura) and effect.covers(battle, unit)), None)
        return max(1.0, value - 1.0) if first is self else value

    def blocks_skill_payload(self, battle: Battle, actor: Unit, skill: Skill,
                             payload: dict[str, Any] | None) -> tuple[bool, str]:
        if self.covers(battle, actor) and skill.mana_cost_for_payload(battle, actor, payload) <= 0:
            return True, "报信乌鸦使该单位不能使用本次不费魔的技能。"
        return False, ""


class MessengerCrowAuraTrait(Trait):
    def __init__(self) -> None:
        super().__init__("报信乌鸦威压", "外邻敌方速-1，不能使用本次不费魔的技能。")
        self.field: MessengerCrowAura | None = None

    def on_enter_battle(self, battle: Battle) -> None:
        if self.owner is None:
            return
        self.field = MessengerCrowAura(self.owner.unit_id)
        battle.add_field_effect(self.field, source=self.owner)

    def on_owner_removed(self, battle: Battle) -> None:
        if self.field is not None:
            battle.remove_field_effect(self.field)
            self.field = None


class MessengerCrowSummon(AbstractHero):
    hero_code = "messenger_crow"
    hero_name = "报信乌鸦"
    role = "召唤物"
    attribute = "暗"
    race = "鸟人"
    level = 1
    base_stats = Stats(attack=1, defense=3, speed=5, attack_range=1, mana=0)
    raw_skill_text = ""
    raw_trait_text = "飞行；外邻敌方速-1且不能使用不费魔技能"

    def __init__(self, player_id: int) -> None:
        super().__init__(player_id, is_summon=True)

    def build_skills(self) -> list[Skill]:
        return []

    def build_traits(self) -> list[Trait]:
        return [FlyingTrait(), MessengerCrowAuraTrait()]


def own_messenger_crows(battle: Battle, actor: Unit) -> list[Unit]:
    return [unit for unit in battle.player_units(actor.player_id)
            if unit.alive and unit.position is not None and not unit.banished
            and unit.hero_code == "messenger_crow" and unit.summoner_id == actor.unit_id]


class MessengerCrowSkill(Skill):
    def __init__(self) -> None:
        super().__init__("messenger_crow", "报信乌鸦", "费0.5魔；选外邻合法空格召唤飞行报信乌鸦。",
                         mana_cost=0.5, target_mode="cell")

    def available_cells(self, battle: Battle, actor: Unit) -> list[Position]:
        probe = MessengerCrowSummon(actor.player_id)
        return [cell for cell in square_around_cells(battle, battle.unit_cells(actor), radius=1)
                if battle.can_place_unit(probe, cell)]

    def can_use(self, battle: Battle, actor: Unit, payload: dict[str, Any] | None = None) -> tuple[bool, str]:
        ok, reason = super().can_use(battle, actor, payload)
        if not ok:
            return ok, reason
        if not self.available_cells(battle, actor):
            return False, "周围没有合法空格召唤报信乌鸦。"
        return True, ""

    def execute(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> None:
        cell = payload_position(payload)
        if cell not in self.available_cells(battle, actor):
            raise ActionError("请选择雀皇外邻合法空格召唤报信乌鸦。")
        battle.summon_unit(MessengerCrowSummon(actor.player_id), cell, summoner=actor)

    def preview(self, battle: Battle, actor: Unit) -> dict[str, Any]:
        return {"cells": positions_to_dict(self.available_cells(battle, actor)), "target_unit_ids": [],
                "secondary_cells": positions_to_dict(battle.unit_cells(actor)), "requires_target": True}

    def get_target_cells_for_payload(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> list[Position]:
        return [payload_position(payload)]


class CrowSwapSkill(Skill):
    requires_direct_unit_target_line = False
    target_cells_are_geometry_only = True
    resolves_from_current_position = True

    def __init__(self) -> None:
        super().__init__("crow_swap", "牵引式传送", "每本人轮一次；与自己召出的在场报信乌鸦交换合法位置。",
                         max_uses_per_turn=1, target_mode="unit")

    @staticmethod
    def swap_legal(battle: Battle, actor: Unit, crow: Unit) -> bool:
        if (actor.position is None or crow.position is None or actor.cannot_move or crow.cannot_move
                or actor.skill_non_damage_effects_blocked()
                or crow.direct_effects_blocked() or crow.skill_non_damage_effects_blocked()):
            return False
        actor_start, crow_start = actor.position, crow.position
        try:
            actor.position = None
            crow.position = None
            return (battle.can_place_unit(actor, crow_start, ignore=actor, mover=actor)
                    and battle.can_place_unit(crow, actor_start, ignore=crow, mover=crow))
        finally:
            actor.position, crow.position = actor_start, crow_start

    def available_crows(self, battle: Battle, actor: Unit) -> list[Unit]:
        return [crow for crow in own_messenger_crows(battle, actor)
                if self.swap_legal(battle, actor, crow)]

    def can_use(self, battle: Battle, actor: Unit, payload: dict[str, Any] | None = None) -> tuple[bool, str]:
        ok, reason = super().can_use(battle, actor, payload)
        if not ok:
            return ok, reason
        if actor.cannot_move:
            return False, "当前不能位移。"
        return (True, "") if self.available_crows(battle, actor) else (False, "没有可合法换位的报信乌鸦。")

    def execute(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> None:
        crow = payload_target_unit(battle, payload)
        if crow not in self.available_crows(battle, actor):
            raise ActionError("请选择自己召出的、可合法换位的报信乌鸦。")
        actor_start, crow_start = actor.position, crow.position
        assert actor_start is not None and crow_start is not None
        actor.position, crow.position = crow_start, actor_start
        if hasattr(actor, "_resolution_actual_position"):
            actor._resolution_actual_position = crow_start
        if hasattr(crow, "_resolution_actual_position"):
            crow._resolution_actual_position = actor_start
        mount = battle.mounted_unit_for(actor)
        if mount is not None and actor.position not in battle.unit_cells(mount):
            battle.clear_mounted_state(actor)
        actor.moved_this_turn = crow.moved_this_turn = True
        battle.log_public_event(f"{actor.name} 与 {crow.name} 使用【牵引式传送】交换位置。", source=actor, target=crow)
        for moved, start, end in ((actor, actor_start, crow_start), (crow, crow_start, actor_start)):
            ctx = MoveContext(unit=moved, start=start, end=end, path=[start, end],
                              via_skill=True, tags={self.code})
            for effect in list(battle.field_effects):
                effect.on_unit_moved(battle, ctx)
            for other in battle.all_units():
                for component in list(other.iter_components()):
                    component.on_unit_moved(battle, ctx)
        battle.enforce_sandstorm_stealth_rules()
        battle.cleanup_dead_units()

    def preview(self, battle: Battle, actor: Unit) -> dict[str, Any]:
        crows = self.available_crows(battle, actor)
        return {"cells": positions_to_dict([cell for crow in crows for cell in battle.unit_cells(crow)]),
                "target_unit_ids": [crow.unit_id for crow in crows],
                "secondary_cells": positions_to_dict(battle.unit_cells(actor)), "requires_target": True}

    def get_target_units_for_payload(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> list[Unit]:
        return [payload_target_unit(battle, payload)]


class GreatCrowFlockSkill(DeclaredAreaSkillMixin, Skill):
    target_cells_are_geometry_only = True

    def __init__(self) -> None:
        super().__init__("great_crow_flock", "超大群乌鸦", "3本人轮一次；选身前6×6先伤敌方武将，后围绕存活目标尽量召鸦。",
                         cooldown_turns=3, target_mode="cell")

    def patterns(self, battle: Battle, actor: Unit) -> list[list[Position]]:
        if actor.position is None:
            return []
        patterns: list[list[Position]] = []
        for dx, dy in ((0, -1), (1, 0), (0, 1), (-1, 0)):
            cells = [actor.position.offset(dx * forward - dy * lateral, dy * forward + dx * lateral)
                     for forward in range(1, 7) for lateral in range(-2, 4)]
            pattern = [cell for cell in cells if battle.in_bounds(cell)]
            if pattern:
                patterns.append(pattern)
        return patterns

    def chosen_cells(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> list[Position]:
        return battle.payload_positions(payload, "declared_area_cells") or match_payload_pattern(payload, self.patterns(battle, actor))

    def enemy_heroes(self, battle: Battle, actor: Unit, cells: list[Position]) -> list[Unit]:
        return [unit for unit in battle.effect_units_at_cells(cells)
                if unit.player_id != actor.player_id and not unit.is_summon and not unit.is_clone]

    def execute(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> None:
        cells = self.chosen_cells(battle, actor, payload)
        targets = list(self.enemy_heroes(battle, actor, cells))
        for target in targets:
            if target.alive:
                battle.resolve_damage(DamageContext(source=actor, target=target,
                    attack_power=actor.stat("attack"), is_skill=True, action_name=self.name,
                    area_cell_hits=battle.unit_hit_count_for_cells(target, cells),
                    tags={"skill", "attack", self.code}))
        for target in targets:
            if not target.alive or target.position is None:
                continue
            for cell in square_around_cells(battle, battle.unit_cells(target), radius=1):
                crow = MessengerCrowSummon(actor.player_id)
                if battle.can_place_unit(crow, cell):
                    battle.summon_unit(crow, cell, summoner=actor)

    def preview(self, battle: Battle, actor: Unit) -> dict[str, Any]:
        preview = pattern_selection_preview(self.patterns(battle, actor))
        preview.update({"target_unit_ids": [], "secondary_cells": positions_to_dict(battle.unit_cells(actor)),
                        "requires_target": True})
        return preview

    def get_target_cells_for_payload(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> list[Position]:
        return self.chosen_cells(battle, actor, payload)

    def get_target_units_for_payload(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> list[Unit]:
        return self.enemy_heroes(battle, actor, self.chosen_cells(battle, actor, payload))


class CrowEmptyAttackTrait(Trait):
    def __init__(self) -> None:
        super().__init__("空格召鸦", "普攻可选范内直线合法空格，消耗一次普攻而免费召唤报信乌鸦。")

    def available_cells(self, battle: Battle, actor: Unit) -> list[Position]:
        if actor.position is None:
            return []
        probe = MessengerCrowSummon(actor.player_id)
        cells: list[Position] = []
        for y in range(battle.height):
            for x in range(battle.width):
                cell = Position(x, y)
                if not battle.can_place_unit(probe, cell):
                    continue
                if any(0 < source.distance_to(cell) <= actor.targeting_range()
                       and (source.x == cell.x or source.y == cell.y
                            or abs(source.x - cell.x) == abs(source.y - cell.y))
                       for source in battle.unit_cells(actor)):
                    cells.append(cell)
        return cells

    def basic_attack_action_entries(self, battle: Battle, actor: Unit) -> list[dict[str, Any]]:
        return [{"code": "crow_empty_attack", "name": "空格召鸦", "description": "消耗一次普攻，在合法空格免费召一只报信乌鸦。",
                 "attack_payload": {"attack_variant": "crow_spawn"}}]

    def basic_attack_preview(self, battle: Battle, actor: Unit,
                             payload: dict[str, Any] | None = None) -> dict[str, Any] | None:
        if (payload or {}).get("attack_variant") != "crow_spawn":
            return None
        return {"cells": positions_to_dict(self.available_cells(battle, actor)), "target_unit_ids": [],
                "secondary_cells": [], "requires_target": True, "cell_targeted_attack": True}

    def basic_attack_area_cells(self, battle: Battle, actor: Unit,
                                payload: dict[str, Any] | None = None) -> list[Position] | None:
        if (payload or {}).get("attack_variant") != "crow_spawn":
            return None
        declared = battle.payload_positions(payload or {}, "attack_cells")
        if not declared and ((payload or {}).get("x") is None or (payload or {}).get("y") is None):
            if (payload or {}).get("declaration_id"):
                raise ActionError("空格召鸦需要选择一个合法空格。")
            return []
        cell = declared[0] if declared else payload_position(payload or {})
        if cell not in self.available_cells(battle, actor) and not (payload or {}).get("queued_resolution"):
            raise ActionError("空格召鸦必须选择范内直线的合法空格。")
        return [cell]

    def basic_attack_payload_metadata(self, battle: Battle, actor: Unit,
                                      payload: dict[str, Any] | None = None) -> dict[str, Any]:
        if (payload or {}).get("attack_variant") != "crow_spawn":
            return {}
        cells = battle.payload_positions(payload or {}, "attack_cells")
        if not cells and (payload or {}).get("x") is not None and (payload or {}).get("y") is not None:
            cells = self.basic_attack_area_cells(battle, actor, payload) or []
        return {"attack_cells": positions_to_dict(cells), "area_attack": True,
                "allow_empty_attack_cell": True, "attack_name": "空格召鸦"}

    def resolve_empty_basic_attack(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> bool:
        if payload.get("attack_variant") != "crow_spawn":
            return False
        cells = battle.payload_positions(payload, "attack_cells")
        if cells and battle.can_place_unit(MessengerCrowSummon(actor.player_id), cells[0]):
            battle.summon_unit(MessengerCrowSummon(actor.player_id), cells[0], summoner=actor)
        else:
            battle.log_public_event(f"{actor.name} 的【空格召鸦】原格已被占，未能召唤。", source=actor)
        return True


