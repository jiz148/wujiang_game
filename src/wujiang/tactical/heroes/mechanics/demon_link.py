"""R308 demon-link status pair and death backlash."""

from __future__ import annotations

from typing import Any

from wujiang.tactical.engine.core import Battle, DamageContext, Skill, Trait, Unit
from wujiang.tactical.heroes.ai_policy import AISkillScoreContext
from wujiang.tactical.heroes.common import (
    StatModifierStatus, payload_target_unit, positions_to_dict,
)

class DemonLinkTargetStatus(StatModifierStatus):
    def __init__(self, source_unit_id: str, link_id: str) -> None:
        super().__init__("恶魔链接·受体", description="被链接吸走攻与速；此链接允许降至0。")
        self.source_unit_id = source_unit_id
        self.link_id = link_id
        self.stacks = 0

    def bind(self, owner: Unit) -> "DemonLinkTargetStatus":
        super().bind(owner)
        if not hasattr(owner, "_demon_link_original_minimums"):
            owner._demon_link_original_minimums = dict(owner.stat_minimums)
        owner.stat_minimums = {**owner.stat_minimums, "attack": 0.0, "speed": 0.0}
        return self

    def on_removed(self, battle: Battle) -> None:
        owner = self.owner
        if owner is not None and not any(isinstance(s, DemonLinkTargetStatus) for s in owner.statuses):
            owner.stat_minimums = getattr(owner, "_demon_link_original_minimums", owner.stat_minimums)
            if hasattr(owner, "_demon_link_original_minimums"):
                delattr(owner, "_demon_link_original_minimums")
        source = battle.units.get(self.source_unit_id)
        if source is not None:
            for status in list(source.statuses):
                if isinstance(status, DemonLinkSourceStatus) and status.link_id == self.link_id:
                    source.remove_status(status, battle)

    def on_owner_removed(self, battle: Battle) -> None:
        if self.owner is not None:
            self.owner.remove_status(self, battle)

    def to_public_dict(self, battle: Battle) -> dict[str, Any]:
        data = super().to_public_dict(battle)
        data.update(source_unit_id=self.source_unit_id, stacks=self.stacks)
        return data


class DemonLinkSourceStatus(StatModifierStatus):
    def __init__(self, target_unit_id: str, link_id: str) -> None:
        super().__init__("恶魔链接·来源", description="每个本人轮开始吸取目标攻速；离距或降至0后一轮结束。")
        self.target_unit_id = target_unit_id
        self.link_id = link_id
        self.stacks = 0
        self.ending_turn_number: int | None = None

    def target_status(self, battle: Battle) -> DemonLinkTargetStatus | None:
        target = battle.units.get(self.target_unit_id)
        if target is None:
            return None
        return next((s for s in target.statuses if isinstance(s, DemonLinkTargetStatus)
                     and s.link_id == self.link_id), None)

    def add_layer(self, battle: Battle) -> None:
        target_status = self.target_status(battle)
        if self.owner is None or target_status is None or self.ending_turn_number is not None:
            return
        self.stacks += 1
        target_status.stacks += 1
        self.attack_delta = self.speed_delta = float(self.stacks)
        target_status.attack_delta = target_status.speed_delta = -float(target_status.stacks)
        battle.log_public_event(f"{self.owner.name} 的恶魔链接叠至 {self.stacks} 层。", source=self.owner,
                                target=target_status.owner)
        if target_status.owner is not None and (target_status.owner.stat("attack") <= 0
                                                or target_status.owner.stat("speed") <= 0):
            self.ending_turn_number = battle.turn_number + max(1, len(battle.turn_order_unit_ids))

    def on_owner_turn_start(self, battle: Battle) -> None:
        if self.ending_turn_number is None and self.owner is not None and self.owner.alive:
            if self.target_status(battle) is None:
                self.owner.remove_status(self, battle)
            else:
                self.add_layer(battle)

    def on_any_turn_end(self, battle: Battle, ended_player_id: int) -> None:
        owner = self.owner
        target_status = self.target_status(battle)
        target = target_status.owner if target_status is not None else None
        if owner is None or not owner.alive or target is None or not target.alive:
            if owner is not None:
                owner.remove_status(self, battle)
            return
        if self.ending_turn_number is None and battle.distance_between_units(owner, target) > 4:
            self.ending_turn_number = battle.turn_number + max(1, len(battle.turn_order_unit_ids))
            battle.log_public_event(f"{target.name} 离开 {owner.name} 的9×9恶魔链接范围。", source=owner, target=target)
        if self.ending_turn_number is not None and battle.turn_number >= self.ending_turn_number:
            owner.remove_status(self, battle)

    def on_removed(self, battle: Battle) -> None:
        target_status = self.target_status(battle)
        if target_status is not None and target_status.owner is not None:
            target_status.owner.remove_status(target_status, battle)

    def on_owner_removed(self, battle: Battle) -> None:
        if self.owner is not None:
            self.owner.remove_status(self, battle)

    def to_public_dict(self, battle: Battle) -> dict[str, Any]:
        data = super().to_public_dict(battle)
        data.update(target_unit_id=self.target_unit_id, stacks=self.stacks,
                    ending_turn_number=self.ending_turn_number)
        return data


class DemonLinkSkill(Skill):
    def ai_effective_enemy_impact(self, battle: Battle, actor: Unit, payload: dict[str, Any],
                                  targets: list[Unit] | None = None, shared: Any = None,
                                  action: dict[str, Any] | None = None) -> bool:
        target = battle.units.get(str(payload.get("target_unit_id") or ""))
        return bool(target is not None and target.player_id != actor.player_id
                    and not target.magic_immunity and not target.skill_non_damage_effects_blocked()
                    and target.dodge_charges <= 0)

    def ai_priority_score(self, context: AISkillScoreContext) -> float:
        battle, actor, payload = context.battle, context.actor, context.payload
        target = battle.units.get(str(payload.get("target_unit_id") or ""))
        if target is None or target.player_id == actor.player_id or target.magic_immunity or target.dodge_charges > 0:
            return -1000.0
        if target.skill_non_damage_effects_blocked():
            return -1000.0
        if any(status.name == "恶魔链接·来源" and getattr(status, "target_unit_id", None) == target.unit_id
               and getattr(status, "ending_turn_number", None) is None for status in actor.statuses):
            return -1000.0
        distance = battle.distance_between_units(actor, target)
        duration = max(0, 4 - distance)
        return (22.0 + min(3.0, target.stat("attack")) * 8.0
                + min(3.0, target.stat("speed")) * 8.0
                + duration * 4.0 + (12.0 if not target.is_summon and not target.is_clone else 0.0)
                - (18.0 if target.stat("attack") <= 1 or target.stat("speed") <= 1 else 0.0))

    def __init__(self) -> None:
        super().__init__("demon_link", "恶魔链接", "2轮冷却、破魔；命中后逐本人轮吸取目标攻速，离距或降至0后一轮消失。",
                         cooldown_turns=2, target_mode="unit")

    def ignores_shield_for_payload(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> bool:
        return True

    def targets(self, battle: Battle, actor: Unit) -> list[Unit]:
        return [unit for unit in battle.all_units() if unit.unit_id != actor.unit_id
                and unit.alive and not unit.banished and unit.position is not None
                and battle.unit_can_be_selected(unit, actor=actor)[0]
                and battle.unit_target_in_range_and_line(actor, unit, actor.targeting_range())]

    def can_use(self, battle: Battle, actor: Unit, payload: dict[str, Any] | None = None) -> tuple[bool, str]:
        ok, reason = super().can_use(battle, actor, payload)
        if not ok:
            return ok, reason
        targets = self.targets(battle, actor)
        if payload and payload.get("target_unit_id"):
            return ((True, "") if str(payload["target_unit_id"]) in {unit.unit_id for unit in targets}
                    else (False, "恶魔链接需要选择射程内直线上的其他单位。"))
        return ((True, "") if targets else (False, "没有可链接的单位。"))

    def execute(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> None:
        target = payload_target_unit(battle, payload)
        if target not in self.targets(battle, actor):
            return
        hostile = target.player_id != actor.player_id
        result = battle.validate_target(actor, target, action_name=self.name, is_skill=True,
                                        is_hostile=hostile, ignore_shield=True,
                                        resolve_defenses=False, record_effect_on_validation=False)
        if result.cancelled or target.skill_non_damage_effects_blocked():
            battle.log_public_event(result.reason or f"{target.name} 未受恶魔链接影响。", source=actor, target=target)
            return
        if hostile and target.total_shields() > 0:
            target.consume_one_shield()
            battle.log_public_event(f"{target.name} 的护盾被恶魔链接贯穿。", source=actor, target=target)
        if hostile and target.dodge_charges > 0:
            target.dodge_charges -= 1
            battle.log_public_event(f"{target.name} 回避了恶魔链接。", source=actor, target=target)
            return
        if battle.destroy_clone_for_skill_effect(target, source=actor, action_name=self.name):
            return
        for status in list(actor.statuses):
            if isinstance(status, DemonLinkSourceStatus) and status.target_unit_id == target.unit_id:
                actor.remove_status(status, battle)
        link_id = f"{actor.unit_id}:{target.unit_id}:{battle.turn_number}"
        source_status = DemonLinkSourceStatus(target.unit_id, link_id)
        target_status = DemonLinkTargetStatus(actor.unit_id, link_id)
        target.add_status(target_status, source=actor)
        if target_status not in target.statuses:
            return
        actor.add_status(source_status, source=actor)
        if source_status not in actor.statuses:
            target.remove_status(target_status, battle)
            return
        source_status.add_layer(battle)
        battle.record_skill_effect(target)

    def preview(self, battle: Battle, actor: Unit) -> dict[str, Any]:
        targets = self.targets(battle, actor)
        return {"cells": positions_to_dict([cell for unit in targets for cell in battle.unit_cells(unit)]),
                "target_unit_ids": [unit.unit_id for unit in targets], "requires_target": True}


def resolve_demon_death_backlash(battle: Battle, actor: Unit, queued_action: Any) -> None:
    if actor.alive or actor not in battle.destroyed_units:
        return
    killer_id = str(queued_action.payload.get("killer_unit_id", ""))
    killer = battle.units.get(killer_id) or next((unit for unit in battle.destroyed_units
                                                   if unit.unit_id == killer_id), None)
    if killer is None:
        return
    killer.add_status(StatModifierStatus("希尔法死亡反噬", attack_delta=-1,
                                       description="因真正击破希尔法而永久攻-1。"), source=actor)
    battle.log_public_event(f"{killer.name} 因击破 {actor.name} 攻 -1。", source=actor, target=killer)


class DemonDeathBacklashTrait(Trait):
    def __init__(self) -> None:
        super().__init__("希尔法死亡反噬", "被攻击或技能伤害真正击破且保持死态时，责任单位攻-1。")
        self.queued_destruction_count = 0

    def on_after_damage(self, battle: Battle, ctx: DamageContext) -> None:
        owner = self.owner
        if (owner is None or ctx.target.unit_id != owner.unit_id or owner.alive
                or ctx.source is None
                or ctx.cancelled or ctx.actual_damage <= 0
                or not (ctx.is_skill or "attack" in ctx.tags)):
            return
        destruction_count = getattr(owner, "destruction_count", 0) + 1
        if self.queued_destruction_count >= destruction_count:
            return
        self.queued_destruction_count = destruction_count
        battle.queue_skill_effect_action(actor=owner, display_name="希尔法死亡反噬",
                                         effect_code="demon_death_backlash",
                                         payload={"killer_unit_id": ctx.source.unit_id, "after_death_effect": True},
                                         target_units=[ctx.source], hostile=False,
                                         effect_resolver=resolve_demon_death_backlash)


