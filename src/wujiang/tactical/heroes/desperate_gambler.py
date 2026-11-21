"""R357 绝境赌徒: fixed life, wagered defense and public chance rerolls."""

from __future__ import annotations

from typing import Any

from wujiang.tactical.engine.core import (
    ActionError, Battle, DamageContext, HealContext, Skill, TargetContext,
    Trait, Unit, direct_scoring,
)


class GamblerUnavoidableBasicTrait(Trait):
    def __init__(self) -> None:
        super().__init__("赌徒必中", "本人的普攻不能被回避。")

    def on_targeted(self, battle: Battle, ctx: TargetContext) -> None:
        if ctx.actor is self.owner and not ctx.is_skill and "attack" in ctx.tags:
            ctx.cannot_evade = True


class GamblerFixedHealthTrait(Trait):
    def __init__(self) -> None:
        super().__init__("赌徒不失血", "经过全部防护后本应损血时，血不变而记一次受伤。")

    def on_final_hp_loss(self, battle: Battle, ctx: DamageContext) -> None:
        if ctx.target is self.owner and ctx.raw_damage is not None and ctx.raw_damage > 0:
            ctx.modifier_receipts.add("gambler_wounded")
            ctx.raw_damage = 0.0

    def on_before_heal(self, battle: Battle, ctx: HealContext) -> None:
        if ctx.target is self.owner:
            ctx.cancelled = True


class GamblerWoundDefenseTrait(Trait):
    def __init__(self) -> None:
        super().__init__("伤换守", "每次有效受伤永久守-1，最低1；筹码强化也记于此。")
        self.defense_loss = 0
        self.attack_bonus = 0

    def modify_stat(self, stat_name: str, value: float) -> float:
        if stat_name == "defense":
            return value - self.defense_loss
        if stat_name == "attack":
            return value + self.attack_bonus
        return value

    def on_final_hp_loss(self, battle: Battle, ctx: DamageContext) -> None:
        if ctx.target is self.owner and "gambler_wounded" in ctx.modifier_receipts:
            if self.owner.stat("defense") > 1:
                self.defense_loss += 1
                ctx.modifier_receipts.add("gambler_defense_lost")
            battle.log_public_event(
                f"{self.owner.name} 血量不变，受伤使守降至{self.owner.stat('defense'):g}。",
                source=ctx.source, target=self.owner,
            )

    def ai_damage_impact_without_hp_loss(self, ctx: DamageContext) -> bool:
        return ctx.target is self.owner and "gambler_defense_lost" in ctx.modifier_receipts

    def wager(self, battle: Battle) -> None:
        if self.owner is None or self.owner.stat("defense") <= 1:
            raise ActionError("守已到最低1，不能再筹码强化。")
        self.defense_loss += 1
        self.attack_bonus += 1
        battle.log_public_event(
            f"{self.owner.name} 筹码强化：攻升至{self.owner.stat('attack'):g}，守降至{self.owner.stat('defense'):g}。",
            source=self.owner,
        )

    def to_public_dict(self, battle: Battle) -> dict[str, Any]:
        data = super().to_public_dict(battle)
        data.update(defense_lost=self.defense_loss, attack_gained=self.attack_bonus)
        return data


class GamblerEndAtOneTrait(Trait):
    def __init__(self) -> None:
        super().__init__("守1破坏", "每个英雄行动回合末若守为1，本体破坏。")

    def on_any_turn_end(self, battle: Battle, ended_player_id: int) -> None:
        owner = self.owner
        if owner is None or not owner.alive or owner.banished or owner.stat("defense") > 1:
            return
        battle.log_public_event(f"{owner.name} 守已到1，在回合结束时破坏。", source=owner)
        owner.current_hp = 0.0
        owner.alive = False


class GamblerBasicOddsTrait(Trait):
    def __init__(self) -> None:
        super().__init__("赌徒普攻", "普攻造成伤害的概率为(6-当前守)/6，最低0。")

    def on_final_damage(self, battle: Battle, ctx: DamageContext) -> None:
        owner = self.owner
        if owner is None or ctx.source is not owner or ctx.is_skill or "attack" not in ctx.tags or ctx.cancelled:
            return
        probability = max(0.0, min(1.0, (6.0 - owner.stat("defense")) / 6.0))
        if direct_scoring():
            from wujiang.tactical.direct_expectation import damage_chance
            damage_chance(ctx, probability)
            return
        if getattr(battle, "_ai_probe_active", False):
            ctx.raw_damage = battle.damage_rule.calculate_damage(
                ctx.attack_power, ctx.target.stat("defense")) * probability
            return
        if probability <= 0:
            succeeds = False
        elif probability >= 1:
            succeeds = True
        else:
            succeeds = battle.roll_chance(
                probability, action_name=f"{owner.name}普攻伤害",
                beneficiary_player_id=owner.player_id,
            )
        battle.log_public_event(
            f"{owner.name} 的普攻伤害概率为{probability:.3g}，判定{'成功' if succeeds else '失败'}。",
            source=owner, target=ctx.target,
        )
        if not succeeds:
            ctx.raw_damage = 0.0
            ctx.modifier_receipts.add("gambler_basic_failed")


class GamblerChipBoostSkill(Skill):
    ai_payload_value_owned = True
    ai_enemy_impact_requirement = False

    def __init__(self) -> None:
        super().__init__("gambler_chip_boost", "筹码强化", "本人轮1次：永久守-1、攻+1。",
                         max_uses_per_turn=1, target_mode="self")

    def _risk(self, actor: Unit) -> GamblerWoundDefenseTrait:
        return next(item for item in actor.traits if isinstance(item, GamblerWoundDefenseTrait))

    def can_use(self, battle: Battle, actor: Unit,
                payload: dict[str, Any] | None = None) -> tuple[bool, str]:
        ok, reason = super().can_use(battle, actor, payload)
        if not ok:
            return ok, reason
        return (True, "") if actor.stat("defense") > 1 else (False, "守已到最低1。")

    def execute(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> None:
        self._risk(actor).wager(battle)

    def ai_payload_candidates(self, battle: Battle, actor: Unit,
                              action: dict[str, Any]) -> list[dict[str, Any]]:
        return [{"type": "skill", "unit_id": actor.unit_id, "skill_code": self.code}]

    def ai_candidate_payloads(self, context: Any) -> list[dict[str, Any]]:
        return self.ai_payload_candidates(context.battle, context.actor, context.action)

    def ai_payload_value(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> float:
        defense = actor.stat("defense")
        if defense <= 2:
            return -1000.0
        attacks = max(0, actor.attack_actions_per_turn() - actor.attacks_used)
        enemies = [unit for unit in battle.enemy_units(actor.player_id) if unit.alive and unit.position is not None]
        if not enemies:
            return -1000.0
        # Guard 6 makes the basic attack's damage chance exactly zero. Wagering
        # before contact is essential; a purely immediate target score traps the
        # hero at guard 6 throughout an otherwise viable match.
        preparation = (108.0 if defense >= 6 else 78.0 if defense >= 5
                       else 48.0 if defense >= 4 else 12.0)
        targets = [unit for unit in battle.enemy_units(actor.player_id) if unit.alive and unit.position is not None
                   and battle.unit_target_in_range_and_line(actor, unit, actor.targeting_range())]
        if not targets or not attacks:
            return preparation
        before = max(0.0, min(1.0, (6 - defense) / 6))
        after = max(0.0, min(1.0, (7 - defense) / 6))
        gain = max(after * battle.damage_rule.calculate_damage(actor.stat("attack") + 1, unit.stat("defense"))
                   - before * battle.damage_rule.calculate_damage(actor.stat("attack"), unit.stat("defense"))
                   for unit in targets)
        return max(preparation, attacks * gain * 130.0 - 24.0 - (7 - defense) * 6.0)

    def ai_priority_score(self, context: Any) -> float:
        return self.ai_payload_value(context.battle, context.actor, context.payload)


class GamblerDesperateRerollSkill(Skill):
    requires_damage_choice = True
    ai_enemy_impact_requirement = False

    def __init__(self) -> None:
        super().__init__("gambler_desperate_reroll", "绝境无赖",
                         "每当前英雄回合最多3次，公开概率结果生效前可作废并独立重取。",
                         timing="instant", target_mode="self")
        self._event_turn = -1
        self._event_uses = 0

    def remaining_probability_rerolls(self, battle: Battle) -> int:
        used = self._event_uses if self._event_turn == battle.turn_number else 0
        return max(0, 3 - used)

    def can_reroll_probability(self, battle: Battle, actor: Unit) -> bool:
        return actor is self.owner and actor.alive and actor.position is not None and self.remaining_probability_rerolls(battle) > 0

    def consume_probability_reroll(self, battle: Battle, actor: Unit) -> None:
        if not self.can_reroll_probability(battle, actor):
            raise ActionError("绝境无赖本回合次数已用完。")
        battle.prepay_skill_resources(self, actor, {})
        if self._event_turn != battle.turn_number:
            self._event_turn = battle.turn_number
            self._event_uses = 0
        self._event_uses += 1

    def can_use(self, battle: Battle, actor: Unit,
                payload: dict[str, Any] | None = None) -> tuple[bool, str]:
        return False, "等待公开的概率结果后选择是否重判。"

    def execute(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> None:
        raise ActionError("绝境无赖只能在概率结果窗口使用。")

    def to_public_dict(self, battle: Battle) -> dict[str, Any]:
        data = super().to_public_dict(battle)
        data["rerolls_remaining"] = self.remaining_probability_rerolls(battle)
        return data
