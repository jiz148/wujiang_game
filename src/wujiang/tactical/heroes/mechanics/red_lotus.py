"""R327 Red Lotus battlefield traits shared with R252."""

from __future__ import annotations

from typing import Any
from wujiang.tactical.engine.core import Battle
from wujiang.tactical.engine.core import DamageContext
from wujiang.tactical.engine.core import HeroUnit
from wujiang.tactical.engine.core import TargetContext
from wujiang.tactical.engine.core import Trait


class RedLotusDestroyedHeroesTrait(Trait):
    def __init__(self) -> None:
        super().__init__("破坏武将攻守成长", "双方每有一个被破坏的武将本体，自身攻、守随时 +1。")
        self.destroyed_hero_count = 0

    def on_destroyed_hero_count_changed(self, battle: Battle, count: int) -> None:
        self.destroyed_hero_count = max(0, int(count))

    def modify_stat(self, stat_name: str, value: float) -> float:
        if stat_name not in {"attack", "defense"}:
            return value
        reference = getattr(self.owner, "_battle_ref", None)
        battle = reference() if reference is not None else None
        count = battle.destroyed_hero_count() if battle is not None else self.destroyed_hero_count
        return value + count


class RedLotusSkillDamageImmunityTrait(Trait):
    def __init__(self) -> None:
        super().__init__("技能伤害免疫", "不受到技能直接造成的伤害，但仍会受到技能的非伤害后续效果。")

    def on_before_damage(self, battle: Battle, ctx: DamageContext) -> None:
        owner = self.owner
        if owner is None or ctx.target.unit_id != owner.unit_id:
            return
        if not ctx.is_skill or ctx.from_field_effect or ctx.cancelled:
            return
        ctx.cancelled = True
        ctx.preserve_followup_effects = True
        ctx.reason = f"{owner.name} 免疫了技能伤害。"
        battle.emit_defense_visual_event(
            source=ctx.source,
            target=owner,
            action_name=ctx.action_name,
            defense_reason="skill_damage_immunity",
        )


class RedLotusUnevadableAttackTrait(Trait):
    def __init__(self) -> None:
        super().__init__("普攻无法被回避", "自身普攻无法被回避，且不会消耗目标的回避次数。")

    def basic_attack_payload_metadata(
        self,
        battle: Battle,
        actor: HeroUnit,
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return {"cannot_evade": True}

    def on_targeted(self, battle: Battle, ctx: TargetContext) -> None:
        owner = self.owner
        if owner is not None and ctx.actor.unit_id == owner.unit_id and not ctx.is_skill and "attack" in ctx.tags:
            ctx.cannot_evade = True


class RedLotusOnceDamagePerTurnTrait(Trait):
    def __init__(self) -> None:
        super().__init__("每回合一次伤害", "每个全局武将回合只会实际受到一次伤害。")
        self._turn_number: int | None = None
        self._damage_taken = False

    def _sync_turn(self, battle: Battle) -> None:
        turn_number = int(getattr(battle, "turn_number", 1) or 1)
        if self._turn_number == turn_number:
            return
        self._turn_number = turn_number
        self._damage_taken = False

    def on_before_damage(self, battle: Battle, ctx: DamageContext) -> None:
        owner = self.owner
        if owner is None or ctx.target.unit_id != owner.unit_id or ctx.cancelled:
            return
        self._sync_turn(battle)
        if not self._damage_taken:
            return
        ctx.cancelled = True
        ctx.preserve_followup_effects = True
        ctx.reason = f"{owner.name} 本回合已经受到过一次伤害。"
        battle.emit_defense_visual_event(
            source=ctx.source,
            target=owner,
            action_name=ctx.action_name,
            defense_reason="once_damage_per_turn",
        )

    def on_after_damage(self, battle: Battle, ctx: DamageContext) -> None:
        owner = self.owner
        if owner is None or ctx.target.unit_id != owner.unit_id or ctx.cancelled:
            return
        self._sync_turn(battle)
        if ctx.actual_damage > 0:
            self._damage_taken = True


