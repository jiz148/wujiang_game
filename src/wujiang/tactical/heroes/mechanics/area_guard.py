"""Reusable multi-cell area damage guard."""

from __future__ import annotations

from wujiang.tactical.engine.core import Battle
from wujiang.tactical.engine.core import DamageContext
from wujiang.tactical.engine.core import Trait


class MultiCellAreaDamageGuardTrait(Trait):
    def __init__(self) -> None:
        super().__init__("多格范围伤害保护", "同一范围伤害命中本体多个占格时，只按 1 格命中结算。")

    def on_before_damage(self, battle: Battle, ctx: DamageContext) -> None:
        owner = self.owner
        if owner is None or ctx.target.unit_id != owner.unit_id:
            return
        if ctx.area_cell_hits <= 1:
            return
        ctx.area_cell_hits = 1
        battle.log(f"{owner.name} 不同时受到范围一格以上的伤害，只按 1 格命中结算。")


