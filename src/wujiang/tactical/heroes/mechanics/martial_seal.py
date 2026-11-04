"""R158 martial god seal mechanics."""

from __future__ import annotations

from typing import Any
from wujiang.tactical.heroes.ai_policy import AISkillScoreContext
from wujiang.tactical.engine.core import Battle
from wujiang.tactical.engine.core import HealContext
from wujiang.tactical.engine.core import HeroUnit
from wujiang.tactical.engine.core import Skill
from wujiang.tactical.engine.core import StatusEffect
from wujiang.tactical.heroes.common import positions_to_dict


class MartialGodSealStatus(StatusEffect):
    def __init__(self) -> None:
        super().__init__("魔界武神之印", "全能力 +2，直到下一个敌方英雄回合结束。", duration=None)

    def modify_stat(self, stat_name: str, value: float) -> float:
        if stat_name in {"attack", "defense", "speed", "attack_range", "mana"}:
            return value + 2
        return value

    def on_any_turn_end(self, battle: Battle, ended_player_id: int) -> None:
        owner = self.owner
        if owner is not None and ended_player_id != owner.player_id and not battle.is_army_turn():
            owner.remove_status(self, battle)

    def on_removed(self, battle: Battle) -> None:
        if self.owner is not None:
            self.owner.clamp_mana()


class MartialGodSealSkill(Skill):
    def ai_self_buff_score(self, context: AISkillScoreContext) -> float:
        if context.battle.effect_recipient(context.actor).has_status("魔界武神之印"):
            return -8.0
        return context.shared.r19_preparation_score(context)

    def __init__(self) -> None:
        super().__init__(
            "martial_god_seal",
            "魔界武神之印",
            "普通技能：2 轮一次；全能力 +2，血 +1/2，持续到下一个敌方英雄回合结束。",
            cooldown_turns=2,
            target_mode="self",
        )

    def execute(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> None:
        target = battle.effect_recipient(actor)
        existing = target.get_status("魔界武神之印")
        if existing is not None:
            target.remove_status(existing, battle)
        target.add_status(MartialGodSealStatus())
        target.gain_mana(2)
        battle.heal(HealContext(source=actor, target=target, amount=0.5, action_name="魔界武神之印"))
        battle.log(f"{target.name} 获得【魔界武神之印】，全能力 +2。")

    def preview(self, battle: Battle, actor: HeroUnit) -> dict[str, Any]:
        target = battle.effect_recipient(actor)
        return {"cells": positions_to_dict(battle.unit_cells(target)), "target_unit_ids": [target.unit_id],
                "secondary_cells": [], "requires_target": False}


