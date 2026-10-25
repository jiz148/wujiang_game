"""R352 Water Ninja clone behavior."""

from __future__ import annotations

from typing import Any

from wujiang.tactical.prediction import random, direct_scoring
from wujiang.tactical.engine.core import Battle, DamageContext, HeroUnit, Trait
from wujiang.tactical.heroes.next_five import StandardCloneSummon, square_around_cells


class WaterNinjaCloneAfterAttackTrait(Trait):
    def __init__(self) -> None:
        super().__init__("水忍分身", "每次普攻伤害结算后，在自身周围召唤 1 个分身，再随机与场上的一个自身分身交换位置。")

    def on_basic_attack_finished(
        self,
        battle: Battle,
        actor: HeroUnit,
        payload: dict[str, Any],
        damage_contexts: list[DamageContext],
        missed: bool,
    ) -> None:
        owner = self.owner
        if (owner is None or actor.unit_id != owner.unit_id or not owner.alive or owner.banished
                or owner.is_clone or owner.position is None or payload.get("basic_attack_cancelled")):
            return
        real_position = getattr(owner, "_resolution_actual_position", None) or owner.position
        body = owner.footprint_cells_at(real_position)
        own_keys = {(cell.x, cell.y) for cell in body}
        cells = [
            cell
            for cell in square_around_cells(battle, body, radius=1)
            if (cell.x, cell.y) not in own_keys
        ]
        summoned = False
        for cell in sorted(cells, key=lambda item: (item.y, item.x)):
            clone = StandardCloneSummon(owner.player_id, owner)
            if not battle.can_place_unit(clone, cell):
                continue
            battle.summon_unit(clone, cell, summoner=owner)
            battle.log(f"{owner.name} 攻击后在周围召唤了一个分身。")
            summoned = True
            break
        if not summoned:
            battle.log(f"{owner.name} 周围没有合法位置，无法召唤分身。")
        clones = [
            unit for unit in battle.all_units()
            if unit.is_clone and unit.summoner_id == owner.unit_id
            and unit.alive and not unit.banished and unit.position is not None
        ]
        if not clones:
            return
        if direct_scoring():
            owner._ai_expected_positions = tuple((clone.position, 1 / len(clones)) for clone in clones)
            return
        swap_clone = random.choice(sorted(clones, key=lambda unit: unit.unit_id))
        swap_position = swap_clone.position
        swap_clone.position = real_position
        owner.position = swap_position
        if hasattr(owner, "_resolution_actual_position"):
            owner._resolution_actual_position = swap_position
        battle.log(f"{owner.name} 攻击后与自身分身随机交换了位置。")


