"""R032 Aaron great unicorn and morning light mechanics."""

from __future__ import annotations

from wujiang.tactical.heroes.ai_candidate_plan import RANKED_CANDIDATES

from typing import Any
from wujiang.tactical.engine.core import ActionError
from wujiang.tactical.engine.core import Battle
from wujiang.tactical.engine.core import DamageContext
from wujiang.tactical.engine.core import HealContext
from wujiang.tactical.engine.core import HeroUnit
from wujiang.tactical.engine.core import Position
from wujiang.tactical.engine.core import Skill
from wujiang.tactical.engine.core import Stats
from wujiang.tactical.engine.core import StatusEffect
from wujiang.tactical.engine.core import Trait
from wujiang.tactical.heroes.base import AbstractHero
from wujiang.tactical.heroes.ai_policy import AISkillScoreContext
from wujiang.tactical.heroes.common import StatModifierStatus
from wujiang.tactical.heroes.common import match_payload_pattern
from wujiang.tactical.heroes.common import pattern_selection_preview
from wujiang.tactical.heroes.common import pattern_signature
from wujiang.tactical.heroes.mechanics.foundation import BasicAttackPierceTrait
from wujiang.tactical.heroes.mechanics.kiku import PassiveSkillLockStatus
from wujiang.tactical.heroes.mechanics.next_five_effects import remote_rectangle_patterns
from wujiang.tactical.heroes.mechanics.next_five_geometry import square_around_cells


class GreatUnicornSummon(AbstractHero):
    hero_code = "great_unicorn"
    hero_name = "大独角兽"
    role = "坐骑"
    attribute = "光"
    race = "兽"
    level = 1
    base_stats = Stats(attack=4, defense=6, speed=5, attack_range=1, mana=0)
    footprint_width = 1
    footprint_height = 2
    raw_skill_text = ""
    raw_trait_text = "可乘骑；普攻破魔"

    def __init__(self, player_id: int) -> None:
        super().__init__(player_id, is_summon=True)

    def build_skills(self) -> list[Skill]:
        return []

    def build_traits(self) -> list[Trait]:
        return [GreatUnicornRideableTrait(), BasicAttackPierceTrait()]


def alive_owned_great_unicorn(battle: Battle, rider: HeroUnit) -> GreatUnicornSummon | None:
    for unit in battle.all_units():
        if (
            isinstance(unit, GreatUnicornSummon)
            and unit.mount_owner_id == rider.unit_id
            and unit.alive
            and not unit.banished
            and unit.position is not None
        ):
            return unit
    return None


class GreatUnicornCooldownStatus(StatusEffect):
    def __init__(self, duration: int) -> None:
        super().__init__(
            "大独角兽召回冷却",
            "坐骑被破坏后，需要再等待 1 个自己的回合才能重新召唤。",
            duration=duration,
            tick_scope="owner_turn_end",
        )


class GreatUnicornRideableTrait(Trait):
    def __init__(self) -> None:
        super().__init__("可乘骑", "只有被乘骑单位会承受伤害和技能效果；乘骑者仍可替其连锁。")

    def on_owner_removed(self, battle: Battle) -> None:
        owner = self.owner
        if owner is None or owner.alive or not getattr(owner, "destruction_count", 0) or not owner.mount_owner_id:
            return
        rider = battle.units.get(owner.mount_owner_id)
        if not isinstance(rider, HeroUnit) or not rider.alive:
            return
        duration = 2 if battle.active_player == rider.player_id else 1
        existing = rider.get_status("大独角兽召回冷却")
        if existing is not None:
            rider.remove_status(existing, battle)
        rider.add_status(GreatUnicornCooldownStatus(duration))


class AaronMountedStartTrait(Trait):
    def __init__(self) -> None:
        super().__init__("骑士开场坐骑", "出场时已经召唤出自己的大独角兽，并且已经处于乘骑状态。")

    def on_enter_battle(self, battle: Battle) -> None:
        owner = self.owner
        if not isinstance(owner, HeroUnit) or owner.position is None:
            return
        if alive_owned_great_unicorn(battle, owner) is not None:
            return
        mount = GreatUnicornSummon(owner.player_id)
        mount.summoner_id = owner.unit_id
        mount.mount_owner_id = owner.unit_id
        mount.is_mount = True
        mount.can_act_on_entry_turn = True
        mount.turn_ready = True
        battle.add_unit(mount, owner.position)
        battle.set_mounted_state(owner, mount)


class GreatUnicornSkill(Skill):
    def __init__(self) -> None:
        super().__init__(
            "summon_great_unicorn",
            "大独角兽",
            "普通技能：召唤并乘骑自己的大独角兽（攻4守6速5范1；1*2；普攻破魔）。",
            target_mode="self",
        )

    def can_use(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any] | None = None) -> tuple[bool, str]:
        ok, reason = super().can_use(battle, actor, payload)
        if not ok:
            return ok, reason
        if actor.position is None:
            return False, "当前不在战场上。"
        if alive_owned_great_unicorn(battle, actor) is not None:
            return False, "场上已经有自己的大独角兽。"
        if actor.has_status("大独角兽召回冷却"):
            return False, "大独角兽仍在召回冷却中。"
        probe = GreatUnicornSummon(actor.player_id)
        probe.mount_owner_id = actor.unit_id
        probe.is_mount = True
        if not battle.can_place_unit(probe, actor.position, ignore=probe, mover=probe):
            return False, "当前位置无法召唤大独角兽。"
        return True, ""

    def execute(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> None:
        if actor.position is None:
            raise ActionError("当前不在战场上。")
        mount = GreatUnicornSummon(actor.player_id)
        mount.summoner_id = actor.unit_id
        mount.mount_owner_id = actor.unit_id
        mount.is_mount = True
        battle.summon_unit(mount, actor.position, summoner=actor)
        battle.set_mounted_state(actor, mount)

    def preview(self, battle: Battle, actor: HeroUnit) -> dict[str, Any]:
        return {
            "cells": [actor.position.to_dict()] if actor.position else [],
            "target_unit_ids": [actor.unit_id],
            "secondary_cells": [],
            "requires_target": False,
        }


class AaronDestroyedSummonBoostStatus(StatModifierStatus):
    def __init__(self) -> None:
        super().__init__(
            "独角兽遗辉",
            attack_delta=2,
            defense_delta=2,
            speed_delta=2,
            description="召唤物被破坏后的下个回合：攻守速 +2，血魔已补满，主动技能不费魔。",
            duration=1,
            tick_scope="owner_turn_end",
        )

    def modify_skill_mana_cost(self, battle: Battle, actor: HeroUnit, skill: Skill, payload: dict[str, Any] | None, cost: float) -> float:
        owner = self.owner
        if owner is not None and actor.unit_id == owner.unit_id:
            return 0.0
        return cost


class AaronDestroyedSummonPendingStatus(StatusEffect):
    def __init__(self) -> None:
        super().__init__("独角兽遗辉待生效", "自己的召唤物被破坏，下个本人回合获得独角兽遗辉。", duration=None)


class MorningHolyLightSkill(Skill):
    ai_candidate_plan = RANKED_CANDIDATES
    def ai_priority_score(self, context: AISkillScoreContext) -> float:
        return context.morning_holy_light_score(self)

    def __init__(self) -> None:
        super().__init__(
            "morning_holy_light",
            "晨曦圣光",
            "普通技能：费 1.5 魔，每回合最多 1 次；远程 5*10 或 10*5；破魔；无伤害；命中单位 2 轮不能使用被动技能，暗属性单位额外受到 5 点伤害。",
            mana_cost=1.5,
            max_uses_per_turn=1,
            target_mode="cell",
        )

    def patterns(self, battle: Battle, actor: HeroUnit) -> list[list[Position]]:
        patterns = remote_rectangle_patterns(battle, actor, 5, 10) + remote_rectangle_patterns(battle, actor, 10, 5)
        seen: set[tuple[tuple[int, int], ...]] = set()
        result: list[list[Position]] = []
        for pattern in patterns:
            key = pattern_signature(pattern)
            if key in seen:
                continue
            seen.add(key)
            result.append(pattern)
        return result

    def chosen_cells(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> list[Position]:
        return match_payload_pattern(payload, self.patterns(battle, actor))

    def execute(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> None:
        cells = self.chosen_cells(battle, actor, payload)
        for target in battle.effect_units_at_cells(cells):
            is_hostile = target.player_id != actor.player_id
            target_ctx = battle.validate_target(
                actor,
                target,
                action_name=self.name,
                is_skill=True,
                is_hostile=is_hostile,
                ignore_shield=True,
                cannot_evade=True,
                tags={"skill", "morning_holy_light"},
                damage_target=True,
            )
            if target_ctx.cancelled:
                if target_ctx.reason:
                    battle.log_public_event(target_ctx.reason, source=actor, target=target)
                continue
            existing = target.get_status("被动封锁")
            if existing is None:
                target.add_status(PassiveSkillLockStatus(duration=2))
                battle.log(f"{target.name} 被【晨曦圣光】封锁被动技能。")
            else:
                existing.duration = 2
            if target.attribute == "暗":
                damage_ctx = battle.resolve_damage(
                    DamageContext(
                        source=actor,
                        target=target,
                        attack_power=5,
                        is_skill=True,
                        action_name=self.name,
                        ignore_shield=True,
                        cannot_evade=True,
                        tags={"skill", "morning_holy_light"},
                    )
                )
                if is_hostile and not damage_ctx.shield_consumed and target.total_shields() > 0:
                    target.consume_one_shield()
                    battle.record_shield_break_summary(actor, target, self.name)
                    battle.log_public_event(f"{target.name} 的 1 层护盾被【{self.name}】贯穿并打碎。",
                                            source=actor, target=target)
            elif is_hostile and target.total_shields() > 0:
                target.consume_one_shield()
                battle.record_shield_break_summary(actor, target, self.name)
                battle.log_public_event(f"{target.name} 的 1 层护盾被【{self.name}】贯穿并打碎。",
                                        source=actor, target=target)

    def preview(self, battle: Battle, actor: HeroUnit) -> dict[str, Any]:
        preview = pattern_selection_preview(self.patterns(battle, actor))
        preview.update({"target_unit_ids": [], "secondary_cells": [], "requires_target": True})
        return preview

    def get_target_cells_for_payload(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> list[Position]:
        return self.chosen_cells(battle, actor, payload)

    def get_target_units_for_payload(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> list[HeroUnit]:
        return battle.effect_units_at_cells(self.chosen_cells(battle, actor, payload))  # type: ignore[return-value]

    def ignores_shield_for_payload(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> bool:
        return True


class AaronLightAuraTrait(Trait):
    def __init__(self) -> None:
        super().__init__("晨曦光环", "自身周围 7*7 单位在亚伦己方回合开始时血 +1/4；亚伦及其召唤物对暗属性单位伤害 +1。")

    def on_owner_turn_start(self, battle: Battle) -> None:
        owner = self.owner
        if owner is None or owner.position is None:
            return
        pending = owner.get_status("独角兽遗辉待生效")
        if pending is not None:
            owner.remove_status(pending, battle)
            recipient = battle.effect_recipient(owner)
            battle.heal(HealContext(source=owner, target=owner,
                                    amount=max(0.0, recipient.max_health - recipient.current_hp),
                                    action_name="独角兽遗辉"))
            owner.gain_mana(max(0.0, owner.max_mana() - owner.current_mana))
            old_boost = owner.get_status("独角兽遗辉")
            if old_boost is not None:
                owner.remove_status(old_boost, battle)
            owner.add_status(AaronDestroyedSummonBoostStatus())
            battle.log(f"{owner.name} 在本回合获得【独角兽遗辉】：攻守速 +2、血魔补满、使用技能不费魔。")
        cells = square_around_cells(battle, battle.unit_cells(owner), radius=3)
        for unit in battle.effect_units_at_cells(cells):
            battle.heal(HealContext(source=owner, target=unit, amount=0.25, action_name="晨曦光环"))

    def _source_is_aaron_or_summon(self, battle: Battle, source: HeroUnit) -> bool:
        owner = self.owner
        if owner is None:
            return False
        return source.unit_id == owner.unit_id or source.summoner_id == owner.unit_id

    def on_before_damage(self, battle: Battle, ctx: DamageContext) -> None:
        if ctx.source is None or ctx.target.attribute != "暗":
            return
        if not self._source_is_aaron_or_summon(battle, ctx.source):
            return
        if ctx.raw_damage is None:
            ctx.attack_power += 1

    def on_owned_summon_destroyed(self, battle: Battle, summon: HeroUnit) -> None:
        owner = self.owner
        if owner is None or not owner.alive or summon.summoner_id != owner.unit_id:
            return
        if owner.get_status("独角兽遗辉待生效") is None:
            owner.add_status(AaronDestroyedSummonPendingStatus())
            battle.log(f"{owner.name} 的召唤物被破坏，下个本人回合将获得【独角兽遗辉】。")


