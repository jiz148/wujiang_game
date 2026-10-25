"""Solar and sphinx cannon battlefield objects."""

from __future__ import annotations

from typing import Any
from wujiang.tactical.engine.core import ActionError
from wujiang.tactical.engine.core import Battle
from wujiang.tactical.engine.core import DamageContext
from wujiang.tactical.engine.core import HeroUnit
from wujiang.tactical.engine.core import Position
from wujiang.tactical.engine.core import Skill
from wujiang.tactical.engine.core import Stats
from wujiang.tactical.engine.core import Trait
from wujiang.tactical.heroes.base import AbstractHero
from wujiang.tactical.heroes.common import match_payload_pattern
from wujiang.tactical.heroes.common import pattern_selection_preview
from wujiang.tactical.heroes.common import payload_cells
from wujiang.tactical.heroes.common import payload_position
from wujiang.tactical.heroes.common import positions_to_dict
from wujiang.tactical.heroes.mechanics.terrain_helpers import _destroy_terrain_in_cells
from wujiang.tactical.heroes.next_five import RemoteDragonBreathSkill
from wujiang.tactical.heroes.next_five import position_key
from wujiang.tactical.heroes.next_five import remote_rectangle_patterns
from wujiang.tactical.heroes.next_five import square_around_cells


class SolarFlameSkill(RemoteDragonBreathSkill):
    def __init__(self) -> None:
        super().__init__()
        self.code = "solar_flame"
        self.name = "爆炎"
        self.description = "普通技能：规则同远程龙息；费 2 魔，每回合最多 2 次，远程 2*2 当前攻击伤害。"


class SphinxCannonAreaAttackTrait(Trait):
    def __init__(self) -> None:
        super().__init__("斯芬克斯炮击", "普攻按范远程选择 3*3 区域，结算后破坏范围内地形。")

    def patterns(self, battle: Battle, actor: HeroUnit) -> list[list[Position]]:
        return remote_rectangle_patterns(battle, actor, 3, 3)

    def basic_attack_area_cells(
        self,
        battle: Battle,
        actor: HeroUnit,
        payload: dict[str, Any] | None = None,
    ) -> list[Position] | None:
        if payload is None:
            return None
        cells = [payload_position(item) for item in payload.get("attack_cells", payload.get("cells", []))]
        if not cells:
            return None
        return match_payload_pattern({"cells": positions_to_dict(cells)}, self.patterns(battle, actor))

    def basic_attack_area_affects_allies(
        self,
        battle: Battle,
        actor: HeroUnit,
        payload: dict[str, Any] | None = None,
    ) -> bool:
        return True

    def basic_attack_preview(
        self,
        battle: Battle,
        actor: HeroUnit,
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        patterns = self.patterns(battle, actor)
        preview = pattern_selection_preview(patterns)
        all_pattern_cells = {cell for pattern in patterns for cell in pattern}
        target_ids = [
            unit.unit_id
            for unit in battle.all_units()
            if unit.unit_id != actor.unit_id
            if any(cell in all_pattern_cells for cell in battle.unit_cells(unit))
        ]
        preview.update({"target_unit_ids": target_ids, "secondary_cells": [], "requires_target": True})
        return preview

    def on_basic_attack_finished(
        self,
        battle: Battle,
        actor: HeroUnit,
        payload: dict[str, Any],
        damage_contexts: list[DamageContext],
        missed: bool,
    ) -> None:
        owner = self.owner
        if owner is None or actor.unit_id != owner.unit_id:
            return
        cells = battle.payload_positions(payload, "attack_cells")
        if cells:
            _destroy_terrain_in_cells(battle, actor, cells, "斯芬克斯炮击")


class SphinxCannonSummon(AbstractHero):
    hero_code = "sphinx_cannon"
    hero_name = "斯芬克斯炮"
    role = "召唤物"
    attribute = "火"
    race = "机械"
    level = 1
    base_stats = Stats(attack=4, defense=5, speed=1, attack_range=7, mana=0)
    raw_skill_text = ""
    raw_trait_text = "攻3*3；破坏地形"

    def __init__(self, player_id: int) -> None:
        super().__init__(player_id, is_summon=True)

    def build_skills(self) -> list[Skill]:
        return []

    def build_traits(self) -> list[Trait]:
        return [SphinxCannonAreaAttackTrait()]


class SphinxCannonSkill(Skill):
    def __init__(self) -> None:
        super().__init__(
            "sphinx_cannon",
            "斯芬克斯炮",
            "普通技能：2 轮一次；在周围召唤攻4守5速1范7、普攻3*3并破坏地形的炮台。",
            cooldown_turns=2,
            target_mode="cell",
        )

    def available_cells(self, battle: Battle, actor: HeroUnit) -> list[Position]:
        probe = SphinxCannonSummon(actor.player_id)
        return [
            cell
            for cell in square_around_cells(battle, battle.unit_cells(actor), radius=1)
            if battle.can_place_unit(probe, cell)
        ]

    def execute(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> None:
        destination = payload_position(payload)
        if destination not in self.available_cells(battle, actor):
            raise ActionError("请选择太阳神周围的合法格召唤斯芬克斯炮。")
        battle.summon_unit(SphinxCannonSummon(actor.player_id), destination, summoner=actor)

    def preview(self, battle: Battle, actor: HeroUnit) -> dict[str, Any]:
        return {
            "cells": positions_to_dict(self.available_cells(battle, actor)),
            "target_unit_ids": [],
            "secondary_cells": positions_to_dict(battle.unit_cells(actor)),
            "requires_target": True,
        }

    def get_target_cells_for_payload(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> list[Position]:
        return [payload_position(payload)]


class SolarJudgmentSkill(Skill):
    def __init__(self) -> None:
        super().__init__(
            "solar_judgment",
            "审判",
            "大招：远程 10*10，伤害值 6，半破魔、无视魔免，并破坏地形。",
            max_uses_per_battle=1,
            target_mode="cell",
        )

    def patterns(self, battle: Battle, actor: HeroUnit) -> list[list[Position]]:
        return remote_rectangle_patterns(battle, actor, 10, 10)

    def chosen_cells(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> list[Position]:
        declared = battle.payload_positions(payload, "declared_area_cells")
        if declared:
            return declared
        chosen = payload_cells(payload)
        xs = {cell.x for cell in chosen}
        ys = {cell.y for cell in chosen}
        invalid = ActionError("所选格子不符合该技能的形状要求。")
        if (actor.position is None or any(not battle.in_bounds(cell) for cell in chosen)
                or len(chosen) != len(xs) * len(ys)
                or max(xs) - min(xs) + 1 != len(xs)
                or max(ys) - min(ys) + 1 != len(ys)):
            raise invalid
        x_bounds = (min(xs), max(xs))
        y_bounds = (min(ys), max(ys))
        if not any((max(0, start), min(battle.width - 1, start + 9)) == x_bounds
                   for start in range(-9, battle.width)):
            raise invalid
        if not any((max(0, start), min(battle.height - 1, start + 9)) == y_bounds
                   for start in range(-9, battle.height)):
            raise invalid
        range_limit = actor.targeting_range()
        if not any(battle.unit_distance_to_cell(actor, cell) <= range_limit for cell in chosen):
            raise invalid
        return sorted(chosen, key=lambda cell: (cell.x, cell.y))

    def execute(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> None:
        cells = self.chosen_cells(battle, actor, payload)
        for target in list(battle.units_at_cells(cells)):
            battle.resolve_damage(
                DamageContext(
                    source=actor,
                    target=target,
                    attack_power=6,
                    is_skill=True,
                    action_name=self.name,
                    half_ignore_shield=True,
                    ignore_magic_immunity=True,
                    area_cell_hits=battle.unit_hit_count_for_cells(target, cells),
                    tags={"skill", "attack", self.code},
                )
            )
        _destroy_terrain_in_cells(battle, actor, cells, self.name)

    def preview(self, battle: Battle, actor: HeroUnit) -> dict[str, Any]:
        patterns = self.patterns(battle, actor)
        preview = pattern_selection_preview(patterns)
        all_cells = {position_key(cell) for pattern in patterns for cell in pattern}
        preview.update({
            "target_unit_ids": [
                unit.unit_id
                for unit in battle.all_units()
                if any(position_key(cell) in all_cells for cell in battle.unit_cells(unit))
            ],
            "secondary_cells": [],
            "requires_target": True,
        })
        return preview

    def get_target_cells_for_payload(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> list[Position]:
        return self.chosen_cells(battle, actor, payload)

    def get_target_units_for_payload(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> list[HeroUnit]:
        return list(battle.units_at_cells(self.chosen_cells(battle, actor, payload)))  # type: ignore[return-value]

    def half_ignores_shield_for_payload(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> bool:
        return True

    def queued_payload_metadata(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> dict[str, Any]:
        return {
            "ignore_magic_immunity": True,
            # Declaration metadata must be derived from the actual selected cells,
            # never from an untrusted caller-supplied frozen-area field.
            "declared_area_cells": positions_to_dict(self.chosen_cells(battle, actor, {"cells": payload.get("cells")})),
        }


