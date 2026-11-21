"""R150 魔使：选择性免疫、普攻供魔与付魔防御。"""

from __future__ import annotations

from typing import Any

from wujiang.tactical.engine.core import (
    Battle, DamageContext, HealContext, Skill, StatusEffect, TargetContext, Trait, Unit,
)
from wujiang.tactical.heroes.ai_policy import HeroAIPolicy
from wujiang.tactical.heroes.common import SelfBuffSkill, StatModifierStatus


class SuperHardenStatus(StatModifierStatus):
    def __init__(self) -> None:
        super().__init__("超硬化", defense_delta=3, duration=3,
                         tick_scope="owner_turn_end", description="守+3；本人轮初自然回血。")

    def on_owner_turn_start(self, battle: Battle) -> None:
        if self.owner is not None and self.owner.alive:
            battle.heal(HealContext(source=self.owner, target=self.owner, amount=0.25,
                                    action_name="超硬化自然回血", effect_source=self))


class SuperHardenSkill(SelfBuffSkill):
    ai_enemy_impact_requirement = False

    def __init__(self) -> None:
        super().__init__("super_harden", "超硬化",
                         "每5本人轮一次；持续3轮，守+3，每本人轮初自然回血1/4。",
                         cooldown_turns=5)

    def can_use(self, battle: Battle, actor: Unit,
                payload: dict[str, Any] | None = None) -> tuple[bool, str]:
        ok, reason = super().can_use(battle, actor, payload)
        if ok and actor.has_status("超硬化"):
            return False, "超硬化尚未结束。"
        return ok, reason

    def apply_to_self(self, battle: Battle, actor: Unit) -> None:
        actor.add_status(SuperHardenStatus(), source=actor)
        battle.log_public_event(f"{actor.name} 超硬化，守+3并开始自然回血。", source=actor, target=actor)

    def ai_self_buff_score(self, context: Any) -> float:
        actor = context.actor
        nearest = min((context.battle.distance_between_units(actor, unit)
                       for unit in context.battle.enemy_units(actor.player_id)
                       if unit.alive and unit.position is not None), default=99)
        return 42.0 if nearest <= 3 or actor.current_hp <= 0.5 else -15.0


class MessengerBasicManaTrait(Trait):
    def __init__(self) -> None:
        super().__init__("魔使普攻供魔", "攻击敌人自己回1魔；攻击友军不伤害，友军回1魔。")

    def basic_attack_payload_metadata(self, battle: Battle, actor: Unit,
                                      payload: dict[str, Any] | None = None) -> dict[str, Any]:
        if actor is not self.owner:
            return {}
        target = battle.units.get(str((payload or {}).get("target_unit_id") or ""))
        result: dict[str, Any] = {"allow_allied_attack_target": True}
        if target is not None and target.player_id == actor.player_id:
            result["attack_effect_summary"] = "友方受体魔+1；不造成伤害。"
        return result

    def resolve_allied_basic_support(self, battle: Battle, actor: Unit, target: Unit) -> bool:
        if actor is not self.owner or target is actor or target.player_id != actor.player_id:
            return False
        gained = target.gain_mana(1)
        battle.log_public_event(f"{actor.name} 普攻支援 {target.name}，目标回复 {gained:g} 魔。",
                                source=actor, target=target)
        return True

    def can_attack_target(self, battle: Battle, actor: Unit, target: Unit) -> tuple[bool, str]:
        if actor is self.owner and target is actor:
            return False, "魔使只能普攻其他友军或敌人。"
        return True, ""

    def ai_basic_attack_variant_payload(self, battle: Battle, actor: Unit,
                                        snapshot: dict[str, Any]) -> dict[str, Any] | None:
        if actor is not self.owner:
            return None
        action = next((item for item in snapshot.get("actions", [])
                       if item.get("kind") == "attack" and item.get("available")), None)
        if action is None:
            return None
        # An immediately useful hostile strike takes priority over mana support.
        for enemy in battle.enemy_units(actor.player_id):
            if (enemy.alive and enemy.position is not None
                    and battle.attack_target_allowed(actor, enemy)[0]
                    and battle.damage_rule.calculate_damage(
                        actor.stat("attack"), enemy.stat("defense")) >= 0.25):
                return None
        allies = sorted((unit for unit in battle.player_units(actor.player_id)
                         if unit is not actor and unit.alive and unit.position is not None
                         and unit.current_mana < unit.max_mana()
                         and battle.attack_target_allowed(actor, unit)[0]),
                        key=lambda unit: (unit.current_mana - unit.max_mana(), unit.unit_id))
        if not allies:
            return None
        target = allies[0]
        return {"type": "attack", "unit_id": actor.unit_id,
                **dict(action.get("attack_payload") or {}),
                "target_unit_id": target.unit_id,
                "x": target.position.x, "y": target.position.y}

    def on_basic_attack_finished(self, battle: Battle, actor: Unit,
                                 payload: dict[str, Any], damage_contexts: list[DamageContext],
                                 missed: bool) -> None:
        if actor is not self.owner:
            return
        target = battle.units.get(str(payload.get("target_unit_id") or ""))
        if target is None or target.player_id == actor.player_id:
            return
        gained = actor.gain_mana(1)
        battle.log_public_event(f"{actor.name} 攻击敌人后回复 {gained:g} 魔。", source=actor, target=target)


class MessengerEnemyEffectGuard(Trait):
    def __init__(self) -> None:
        super().__init__("敌方效果抗性", "只受敌方技能伤害；敌方普通伤害与非伤害效果无效。")

    def on_targeted(self, battle: Battle, ctx: TargetContext) -> None:
        owner = self.owner
        if (owner is None or ctx.target is not owner or ctx.actor.player_id == owner.player_id):
            return
        if ctx.is_skill and (ctx.damage_target or "damage" in ctx.tags):
            return
        ctx.cancelled = True
        ctx.reason = f"{owner.name} 不受敌方技能伤害以外的效果。"

    def on_before_damage(self, battle: Battle, ctx: DamageContext) -> None:
        owner = self.owner
        if (owner is None or ctx.target is not owner or ctx.source is None
                or ctx.source.player_id == owner.player_id or ctx.is_skill):
            return
        ctx.cancelled = True
        ctx.reason = f"{owner.name} 不受敌方非技能伤害。"

    def accepts_status(self, status: StatusEffect) -> bool:
        owner = self.owner
        source_player = getattr(status, "source_player_id", None)
        return owner is None or source_player is None or source_player == owner.player_id


class MessengerSpellDefenseStatus(StatModifierStatus):
    def __init__(self, created_turn: int) -> None:
        super().__init__("近魔防御", defense_delta=1,
                         description="外邻单位付魔，守+1至下一敌方英雄回合结束前。")
        self.created_turn = created_turn

    def on_any_turn_end(self, battle: Battle, ended_player_id: int) -> None:
        if (self.owner is not None and ended_player_id != self.owner.player_id
                and battle.turn_number > self.created_turn):
            self.owner.remove_status(self, battle)


class MessengerSpellDefenseTrait(Trait):
    def __init__(self) -> None:
        super().__init__("近魔防御", "周围单位每次真正费魔用技能，守+1至下一敌方回合结束前。")

    def on_skill_mana_paid(self, battle: Battle, actor: Unit, skill: Skill,
                           amount: float) -> None:
        owner = self.owner
        if (owner is None or not owner.alive or owner.banished or owner.position is None
                or actor is owner or actor.position is None or amount <= 0
                or battle.distance_between_units(owner, actor) != 1):
            return
        owner.add_status(MessengerSpellDefenseStatus(battle.turn_number), source=owner)
        battle.log_public_event(f"{actor.name} 在 {owner.name} 周围付魔施技，{owner.name} 守+1。",
                                source=actor, target=owner)


class ManaMessengerAIPolicy(HeroAIPolicy):
    def attack_payload_has_hero_benefit(self, battle: Battle, actor: Unit,
                                        payload: dict) -> bool:
        target = battle.units.get(str(payload.get("target_unit_id") or ""))
        return (target is not None and target is not actor and target.player_id == actor.player_id
                and target.alive and target.current_mana < target.max_mana())

    def early_attack_score(self, battle: Battle, actor: Unit, target: Unit | None,
                           payload: dict, profile: Any, shared: Any) -> float | None:
        if target is None or target is actor or target.player_id != actor.player_id:
            return None
        return 55.0 if target.current_mana < target.max_mana() else -100.0
