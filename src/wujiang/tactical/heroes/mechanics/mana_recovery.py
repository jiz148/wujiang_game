"""Reusable natural mana recovery trait."""

from __future__ import annotations

from wujiang.tactical.engine.core import Battle
from wujiang.tactical.engine.core import Trait


class NaturalManaRecoveryTrait(Trait):
    def __init__(self) -> None:
        super().__init__("自然回魔", "每个自己的己方回合开始时魔 +1，最多到当前魔上限。")

    def on_owner_turn_start(self, battle: Battle) -> None:
        if self.owner is None:
            return
        gained = self.owner.gain_mana(1)
        if gained:
            battle.log(f"{self.owner.name} 自然回魔，获得 {gained} 点魔。")


