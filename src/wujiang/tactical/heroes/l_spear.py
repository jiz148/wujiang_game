"""R151 L: action mobility, damage-fed mana, and two distinct spear hits."""

from __future__ import annotations

from typing import Any
from wujiang.tactical.stat_queries import stat_dependencies
from wujiang.tactical.query_purity import readonly_query_methods

from wujiang.tactical.engine.core import Battle, DamageContext, HeroUnit, Skill, Trait, Unit, is_army_soldier
from wujiang.tactical.heroes.common import FlagStatus, payload_target_unit, positions_to_dict


@readonly_query_methods('modify_normal_move_actions_per_turn')
class LAttackMobility(Trait):
    def __init__(self) -> None:
        super().__init__("连击机动", "每次完成普通攻击，本轮额外获得一次普通移动机会。")
        self.extra_moves = 0

    def modify_normal_move_actions_per_turn(self, value: int) -> int:
        return value + self.extra_moves

    def on_basic_attack_finished(self, battle: Battle, actor: Unit,
                                 payload: dict[str, Any], damage_contexts: list[DamageContext],
                                 missed: bool) -> None:
        if actor is self.owner:
            self.extra_moves += 1

    def on_any_turn_end(self, battle: Battle, ended_player_id: int) -> None:
        if self.owner is not None and ended_player_id == self.owner.player_id:
            self.extra_moves = 0


class LKillGrowth(Trait):
    def __init__(self) -> None:
        super().__init__("猎杀成长", "每真正破坏一名敌方武将，永久速+1、魔+1。")
        self.kills = 0

    @stat_dependencies('kills', query_only=True)
    def modify_stat(self, stat_name: str, value: float) -> float:
        return value + self.kills if stat_name == "speed" else value

    def on_unit_destruction_finalized(self, battle: Battle, destroyed_unit: Unit,
                                      source_unit_id: str | None) -> None:
        owner = self.owner
        if (owner is None or source_unit_id != owner.unit_id
                or destroyed_unit.player_id == owner.player_id
                or not isinstance(destroyed_unit, HeroUnit)
                or destroyed_unit.is_summon or is_army_soldier(destroyed_unit)):
            return
        self.kills += 1
        owner.gain_mana(1)
        battle.log_public_event(f"{owner.name} 击毁敌将，永久速+1并回魔1。", source=owner,
                                target=destroyed_unit)


class LDamageMana(Trait):
    def __init__(self) -> None:
        super().__init__("血战回魔", "每次造成或受到实际生命伤害，魔+1。")

    def on_after_damage(self, battle: Battle, ctx: DamageContext) -> None:
        owner = self.owner
        if (owner is None or ctx.cancelled or ctx.actual_damage <= 0
                or owner not in (ctx.source, ctx.target)):
            return
        owner.gain_mana(1)
        battle.log_public_event(f"{owner.name} 因造成或承受伤害回魔1。", source=owner,
                                target=owner)


class LSpearSkill(Skill):
    ai_payload_value_owned = True

    def __init__(self, code: str, name: str, description: str, *,
                 cooldown_turns: int = 0, max_uses_per_battle: int | None = None) -> None:
        super().__init__(code, name, description, target_mode="enemy",
                         cooldown_turns=cooldown_turns,
                         max_uses_per_battle=max_uses_per_battle)

    def ignores_magic_immunity_for_payload(self, battle: Battle, actor: Unit,
                                           payload: dict[str, Any]) -> bool:
        return True

    def preview(self, battle: Battle, actor: Unit) -> dict[str, Any]:
        targets = [unit for unit in battle.enemy_units(actor.player_id)
                   if unit.position is not None and battle.unit_target_in_range_and_line(
                       actor, unit, actor.targeting_range())]
        return {"cells": positions_to_dict([cell for unit in targets for cell in battle.unit_cells(unit)]),
                "target_unit_ids": [unit.unit_id for unit in targets],
                "secondary_cells": [], "requires_target": True}

    def ai_payload_value(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> float:
        from wujiang.tactical.rooms.ai_probe import ai_probe_rollback

        target_id = payload.get("target_unit_id")
        target = battle.units.get(str(target_id))
        if target is None or not target.alive or target.player_id == actor.player_id:
            return -1000.0
        with ai_probe_rollback(battle):
            source, recipient = battle.get_unit(actor.unit_id), battle.get_unit(target.unit_id)
            ctx = battle.resolve_damage(DamageContext(
                source=source, target=recipient, attack_power=source.stat("attack"),
                is_skill=True, action_name=self.name,
                ignore_shield=self.code == "red_spear",
                cannot_evade=self.code == "cursed_gold_spear",
                ignore_magic_immunity=True, tags={"skill", self.code},
            ))
            damage = max(0.0, ctx.actual_damage)
            mana = recipient.current_mana if self.code == "red_spear" else 0.0
            denied_heal = (not recipient.cannot_heal and recipient.current_hp < recipient.max_health
                           and self.code == "cursed_gold_spear")
        if self.code == "cursed_gold_spear" and target.total_shields() > 0:
            return 22.0
        return damage * 100.0 + mana * 24.0 + (38.0 if denied_heal else 0.0) - 12.0

    def ai_priority_score(self, context: Any) -> float:
        return self.ai_payload_value(context.battle, context.actor, context.payload)

    def ai_effective_enemy_impact(self, battle: Battle, actor: Unit,
                                  payload: dict[str, Any], targets: list[Unit] | None = None,
                                  shared: Any = None, action: dict[str, Any] | None = None) -> bool:
        target = battle.units.get(str(payload.get("target_unit_id")))
        return target is not None and target.alive and target.player_id != actor.player_id

    def execute(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> None:
        target = payload_target_unit(battle, payload)
        if target.player_id == actor.player_id:
            return
        ctx = battle.resolve_damage(DamageContext(
            source=actor, target=target, attack_power=actor.stat("attack"),
            is_skill=True, action_name=self.name,
            ignore_shield=self.code == "red_spear",
            cannot_evade=self.code == "cursed_gold_spear",
            ignore_magic_immunity=True, tags={"skill", self.code},
        ))
        if ctx.cancelled:
            return
        recipient = ctx.target
        if self.code == "red_spear":
            lost = recipient.drain_mana(recipient.current_mana)
            battle.log_public_event(f"{recipient.name} 被红枪命中，失去 {lost:g} 魔。",
                                    source=actor, target=recipient)
        elif not recipient.cannot_heal:
            recipient.add_status(FlagStatus("金枪禁疗", "cannot_heal",
                                            description="被金枪命中后不能回血。"), source=actor)
            battle.log_public_event(f"{recipient.name} 被金枪命中，不能回血。",
                                    source=actor, target=recipient)


class RedSpearSkill(LSpearSkill):
    def __init__(self) -> None:
        super().__init__("red_spear", "破魔的红枪",
                         "每2本人轮一次；伤害破魔、无视魔免，命中目标魔变为0。",
                         cooldown_turns=2)

    def ignores_shield_for_payload(self, battle: Battle, actor: Unit,
                                   payload: dict[str, Any]) -> bool:
        return True


class CursedGoldSpearSkill(LSpearSkill):
    def __init__(self) -> None:
        super().__init__("cursed_gold_spear", "诅咒的金枪",
                         "本场一次；伤害不能回避、无视魔免，命中后不能回血。",
                         max_uses_per_battle=1)

    def cannot_evade_for_payload(self, battle: Battle, actor: Unit,
                                 payload: dict[str, Any]) -> bool:
        return True
