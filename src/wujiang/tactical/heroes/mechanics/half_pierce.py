"""Reusable half-piercing attack trait."""

from __future__ import annotations

from wujiang.tactical.engine.core import Battle
from wujiang.tactical.engine.core import DamageContext
from wujiang.tactical.engine.core import TargetContext
from wujiang.tactical.engine.core import Trait


class HalfPierceAttackTrait(Trait):
    def __init__(self) -> None:
        super().__init__("攻击半破魔", "普攻命中护盾时打掉护盾，并以原本攻 -1 继续结算伤害。")

    def _is_owner_attack(self, ctx: TargetContext | DamageContext) -> bool:
        owner = self.owner
        source = ctx.actor if isinstance(ctx, TargetContext) else ctx.source
        return owner is not None and source is not None and source.unit_id == owner.unit_id and not ctx.is_skill and "attack" in ctx.tags

    def on_targeted(self, battle: Battle, ctx: TargetContext) -> None:
        if self._is_owner_attack(ctx):
            ctx.half_ignore_shield = True

    def on_before_damage(self, battle: Battle, ctx: DamageContext) -> None:
        if self._is_owner_attack(ctx):
            ctx.half_ignore_shield = True


