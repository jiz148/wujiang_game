"""Ancient-mecha battlefield abilities and summons (R249-R253)."""

from __future__ import annotations

from copy import copy
from typing import Any

from wujiang.tactical.prediction import random, direct_scoring
from wujiang.tactical.engine.army import is_army_soldier
from wujiang.tactical.engine.core import (
    ActionError, ActionMiss, Battle, BattleComponent, DamageContext,
    HealContext, HeroUnit, Position, Skill, Stats, StatusEffect, Trait, Unit,
)
from wujiang.tactical.heroes.base import AbstractHero
from wujiang.tactical.heroes.ai_policy import AISkillScoreContext
from wujiang.tactical.heroes.common import (
    StatModifierStatus, match_payload_pattern, pattern_selection_preview,
    payload_position, positions_to_dict,
)
from wujiang.tactical.heroes.mechanics.dragon_breath import RemoteDragonBreathSkill
from wujiang.tactical.heroes.mechanics.next_five_effects import (
    damage_followup_effect_applies,
    remote_rectangle_patterns,
)
from wujiang.tactical.heroes.mechanics.next_five_geometry import ALL_DIRECTIONS

class AncientRepairBonusStatus(StatModifierStatus):
    def __init__(self, stat_name: str, applied_turn_number: int) -> None:
        deltas = {"attack": "attack_delta", "defense": "defense_delta", "speed": "speed_delta",
                  "attack_range": "range_delta", "mana": "mana_delta"}
        super().__init__("万古的修整", description=f"{stat_name} +1，直到下个本人回合结束。",
                         duration=1, tick_scope="owner_turn_end", **{deltas[stat_name]: 1})
        self.applied_turn_number = applied_turn_number

    def on_owner_turn_end(self, battle: Battle) -> None:
        if battle.turn_number != self.applied_turn_number:
            super().on_owner_turn_end(battle)

    def on_removed(self, battle: Battle) -> None:
        if self.owner is not None:
            self.owner.clamp_mana()


class AncientRepairSkill(Skill):
    def ai_priority_score(self, context: AISkillScoreContext) -> float:
        battle, actor = context.battle, context.actor
        target = battle.units.get(str(context.payload.get("target_unit_id") or ""))
        if target is None or target.player_id != actor.player_id or target.skill_non_damage_effects_blocked():
            return -1000.0
        heal = 0.0 if target.cannot_heal else min(0.25, max(0.0, target.max_health - target.current_hp))
        mana = min(1.0, max(0.0, target.max_mana() - target.current_mana))
        score = heal * 155.0 + mana * 26.0 + (19.0 if target.alive else 0.0)
        return score if score > 21.0 else -1000.0

    def __init__(self) -> None:
        super().__init__("ancient_repair", "万古的修整",
                         "每回合一次；范4，机甲武将或其召唤物血+1/4、魔+1，随机一项能力值+1至其下回合结束。",
                         max_uses_per_turn=1, target_mode="unit")

    def targets(self, battle: Battle, actor: Unit) -> list[Unit]:
        return [unit for unit in battle.all_units()
                if unit.alive and not unit.banished and unit.position is not None
                and ((isinstance(unit, HeroUnit) and not unit.is_summon and unit.race == "机甲")
                     or (unit.is_summon and (unit.race == "机甲"
                         or getattr(battle.units.get(unit.summoner_id or ""), "race", None) == "机甲")))
                and battle.unit_can_be_selected(unit, actor=actor)[0]
                and battle.unit_target_in_range_and_line(actor, unit, actor.targeting_range())]

    def can_use(self, battle: Battle, actor: Unit, payload: dict[str, Any] | None = None) -> tuple[bool, str]:
        ok, reason = super().can_use(battle, actor, payload)
        if not ok:
            return ok, reason
        eligible = {unit.unit_id for unit in self.targets(battle, actor)}
        if payload and payload.get("target_unit_id"):
            return (True, "") if str(payload["target_unit_id"]) in eligible else (False, "只能修整射程内机甲武将或其召唤物。")
        return (True, "") if eligible else (False, "没有可修整的机甲单位。")

    def execute(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> None:
        target = battle.get_unit(str(payload.get("resolved_target_unit_id") or payload.get("target_unit_id") or ""))
        if target.unit_id not in {unit.unit_id for unit in self.targets(battle, actor)}:
            raise ActionMiss("万古的修整目标已不在合法范围。")
        result = battle.validate_target(actor, target, action_name=self.name, is_skill=True,
                                        is_hostile=target.player_id != actor.player_id,
                                        record_effect_on_validation=False)
        recipient = result.target
        if (result.cancelled or recipient.unit_id not in {unit.unit_id for unit in self.targets(battle, actor)}
                or recipient.skill_non_damage_effects_blocked()):
            battle.log_public_event(result.reason or f"{recipient.name} 未受到万古的修整。", source=actor, target=recipient)
            return
        battle.heal(HealContext(source=actor, target=recipient, amount=0.25, action_name=self.name, tags={"skill"}))
        recipient.gain_mana(1)
        if direct_scoring():
            status = AncientRepairBonusStatus("attack", battle.turn_number)
            for attribute in ("attack_delta", "defense_delta", "speed_delta", "range_delta", "mana_delta"):
                setattr(status, attribute, .2)
            recipient.add_status(status, source=actor)
            return
        stat_name = random.choice(("attack", "defense", "speed", "attack_range", "mana"))
        recipient.add_status(AncientRepairBonusStatus(stat_name, battle.turn_number), source=actor)
        battle.log_public_event(f"{actor.name} 修整了{recipient.name}，其{stat_name}暂时+1。", source=actor, target=recipient)

    def preview(self, battle: Battle, actor: Unit) -> dict[str, Any]:
        targets = self.targets(battle, actor)
        return {"cells": positions_to_dict([cell for target in targets for cell in battle.unit_cells(target)]),
                "target_unit_ids": [target.unit_id for target in targets], "secondary_cells": [], "requires_target": True}


class AncientFusionSkill(Skill):
    requires_direct_unit_target_line = False

    def ai_priority_score(self, context: AISkillScoreContext) -> float:
        battle, actor, payload = context.battle, context.actor, context.payload
        selected = battle.units.get(str(payload.get("target_unit_id") or ""))
        target = battle.effect_recipient(selected) if selected is not None else None
        selected_code = str(payload.get("copied_skill_code") or "")
        if (selected is None or "古代" not in selected.name or target is None
                or target.player_id != actor.player_id or not target.alive
                or target.skill_non_damage_effects_blocked() or target.summoner_id == actor.unit_id):
            return -1000.0
        skill = actor.skill_map().get(selected_code)
        if skill is None:
            return -1000.0
        ability = {"shensu": 37.0, "pierce": 32.0, "protection": 34.0,
                   "ancient_repair": 46.0, "ancient_rust": 38.0,
                   "remote_dragon_breath": 42.0, "ancient_jaw": 47.0,
                   "ancient_arm": 44.0, "ancient_vigor": 32.0,
                   "ancient_cursed_rust": 51.0, "mechanical_horse": 42.0,
                   "dragon_breath": 40.0, "ancient_fusion": 8.0}.get(selected_code, 22.0)
        if selected_code in target.skill_map():
            ability = 0.0
        trait_gain = sum(24.0 for trait in actor.traits
                         if trait.name != "机械马开局乘骑"
                         and not any(t.name == trait.name for t in target.traits))
        target_value = (36.0 + target.stat("attack") * 4.0 + target.stat("speed") * 3.0
                        + (16.0 if target.turn_ready else 0.0))
        donor_loss = actor.current_hp * 155.0 + (70.0 if actor.turn_ready and actor.attacks_used == 0 else 0.0)
        if any(trait.name == "古代炮台4×4普攻" for trait in actor.traits):
            donor_loss += 42.0
        if actor.hero_code == "excel_r250":
            donor_loss += 9.0 * sum(status.name == "溶解之颌弱化" and getattr(status, "source_unit_id", "") == actor.unit_id
                                      for unit in battle.all_units() for status in unit.statuses)
        if actor.hero_code == "excel_r252":
            donor_loss += 32.0
        if actor.hero_code == "excel_r253" and battle.mounted_unit_for(actor) is not None:
            donor_loss += 36.0
        if target.is_summon or target.is_clone:
            target_value *= 0.6
        return target_value + ability + trait_gain - donor_loss - 23.0

    def __init__(self) -> None:
        super().__init__("ancient_fusion", "古代融合",
                         "破坏自己，使一名己方名字含古代的单位永久攻守速+1，并继承自身特性及所选一个技能。",
                         target_mode="ally")

    def direct_unit_target_range(self, battle: Battle, actor: Unit,
                                 payload: dict[str, Any] | None = None) -> int:
        return max(battle.width, battle.height)

    def targets(self, battle: Battle, actor: Unit) -> list[Unit]:
        return [unit for unit in battle.all_units()
                if unit.unit_id != actor.unit_id and unit.player_id == actor.player_id
                and unit.alive and not unit.banished and unit.position is not None
                and "古代" in unit.name and battle.unit_can_be_selected(unit, actor=actor)[0]]

    def source_skills(self, actor: Unit) -> list[Skill]:
        return [skill for skill in actor.skills if skill.code
                and getattr(skill, "_ancient_stolen_charges", 1) > 0]

    def can_use(self, battle: Battle, actor: Unit, payload: dict[str, Any] | None = None) -> tuple[bool, str]:
        ok, reason = super().can_use(battle, actor, payload)
        if not ok:
            return ok, reason
        targets = {unit.unit_id for unit in self.targets(battle, actor)}
        if not targets or not self.source_skills(actor):
            return False, "场上没有可融合的古代友军或可传承技能。"
        if payload and payload.get("target_unit_id"):
            if str(payload["target_unit_id"]) not in targets:
                return False, "只能选择场上己方名字含古代的其他单位。"
            code = str(payload.get("copied_skill_code") or "")
            if code not in {skill.code for skill in self.source_skills(actor)}:
                return False, "请选择本单位一项可见技能传承。"
        return True, ""

    def execute(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> None:
        target = battle.get_unit(str(payload.get("resolved_target_unit_id") or payload.get("target_unit_id") or ""))
        if target.unit_id not in {unit.unit_id for unit in self.targets(battle, actor)}:
            raise ActionMiss("古代融合的受体已离场。")
        selected = next((skill for skill in self.source_skills(actor)
                         if skill.code == str(payload.get("copied_skill_code") or "")), None)
        if selected is None:
            raise ActionError("没有所选传承技能。")
        result = battle.validate_target(actor, target, action_name=self.name, is_skill=True,
                                        is_hostile=False, record_effect_on_validation=False)
        recipient = result.target
        if (result.cancelled or not recipient.alive or recipient.player_id != actor.player_id
                or recipient.skill_non_damage_effects_blocked()):
            battle.log_public_event(result.reason or f"{target.name} 未接受古代融合。", source=actor, target=target)
            return
        actor.current_hp = 0
        actor.alive = False
        for stat_name in ("attack", "defense", "speed"):
            setattr(recipient.base_stats, stat_name, getattr(recipient.base_stats, stat_name) + 1)
        for trait in actor.traits:
            if isinstance(trait, AncientMechanicalHorseStartTrait):
                # This is the rider's deployment rule, not a transferable printed trait.
                continue
            if any(existing.name == trait.name for existing in recipient.traits):
                continue
            inherited = copy(trait)
            BattleComponent.__init__(inherited, inherited.name, inherited.description)
            recipient.traits.append(inherited.bind(recipient))
        if selected.code not in recipient.skill_map():
            inherited_skill = copy(selected)
            BattleComponent.__init__(inherited_skill, inherited_skill.name, inherited_skill.description)
            inherited_skill.uses_this_turn = 0
            inherited_skill.uses_this_battle = 0
            inherited_skill.cooldown_remaining = 0
            inherited_skill._uses_turn_number = None
            recipient.skills.append(inherited_skill.bind(recipient))
        battle.log_public_event(f"{actor.name} 融合到{recipient.name}，传承【{selected.name}】。", source=actor, target=recipient)
        battle.cleanup_dead_units()

    def preview(self, battle: Battle, actor: Unit) -> dict[str, Any]:
        targets = self.targets(battle, actor)
        return {"cells": positions_to_dict([cell for target in targets for cell in battle.unit_cells(target)]),
                "target_unit_ids": [target.unit_id for target in targets], "secondary_cells": [], "requires_target": True,
                "selection": {"mode": "ancient_fusion", "targets": [
                    {"unit_id": target.unit_id, "name": target.name,
                     "recipient_name": battle.effect_recipient(target).name} for target in targets],
                    "skills": [{"code": skill.code, "name": skill.name} for skill in self.source_skills(actor)]}}


class AncientEngineerReactionLockTrait(Trait):
    def __init__(self) -> None:
        super().__init__("古代工兵伤害封锁", "本人攻技的伤害计算阶段，非受击对象不能使用技能。")
        self.blocks_bystander_reactions = True


class AncientTurretAreaTrait(Trait):
    def __init__(self) -> None:
        super().__init__("古代炮台4×4普攻", "范内远程选择4×4区域，敌我均受击。")

    def patterns(self, battle: Battle, actor: Unit) -> list[list[Position]]:
        return remote_rectangle_patterns(battle, actor, 4, 4)

    def basic_attack_area_cells(self, battle: Battle, actor: Unit,
                                payload: dict[str, Any] | None = None) -> list[Position] | None:
        if payload is None or not (payload.get("attack_cells") or payload.get("cells")):
            return None
        return match_payload_pattern({"cells": payload.get("attack_cells") or payload.get("cells")},
                                     self.patterns(battle, actor))

    def basic_attack_preview(self, battle: Battle, actor: Unit,
                             payload: dict[str, Any] | None = None) -> dict[str, Any]:
        patterns = self.patterns(battle, actor)
        preview = pattern_selection_preview(patterns)
        covered_cells = {spot for pattern in patterns for spot in pattern}
        preview.update({"target_unit_ids": [unit.unit_id for unit in battle.all_units()
                                            if any(cell in covered_cells for cell in battle.unit_cells(unit))],
                        "secondary_cells": [], "requires_target": True})
        return preview


class AncientRustNoHealStatus(StatusEffect):
    remove_when_source_destroyed = True

    def __init__(self, source_unit_id: str) -> None:
        super().__init__("古代锈蚀禁疗", "原炮台破坏前不能回复。")
        self.source_unit_id = source_unit_id
        self.flag_name = "cannot_heal"

    def bind(self, owner: Unit) -> "AncientRustNoHealStatus":
        super().bind(owner)
        owner.cannot_heal = True
        return self

    def on_removed(self, battle: Battle) -> None:
        if self.owner is not None:
            self.owner.cannot_heal = any(getattr(status, "flag_name", "") == "cannot_heal"
                                         for status in self.owner.statuses)


class AncientRustReadyStatus(StatusEffect):
    def __init__(self) -> None:
        super().__init__("生锈的气味预备", "下一次普攻破魔；实际命中者禁疗至本人破坏。")

    def basic_attack_payload_metadata(self, battle: Battle, actor: Unit,
                                      payload: dict[str, Any] | None = None) -> dict[str, Any]:
        return {"ignore_shield": True, "ancient_rust_attack": True}

    def on_after_damage(self, battle: Battle, ctx: DamageContext) -> None:
        owner = self.owner
        if owner is None or ctx.source is not owner or ctx.is_skill or "attack" not in ctx.tags:
            return
        if not damage_followup_effect_applies(ctx) or ctx.target.skill_non_damage_effects_blocked():
            return
        if not any(isinstance(status, AncientRustNoHealStatus) and status.source_unit_id == owner.unit_id
                   for status in ctx.target.statuses):
            ctx.target.add_status(AncientRustNoHealStatus(owner.unit_id), source=owner)

    def on_basic_attack_finished(self, battle: Battle, actor: Unit, payload: dict[str, Any],
                                 damage_contexts: list[DamageContext], missed: bool) -> None:
        if self.owner is actor:
            actor.remove_status(self, battle)


class AncientRustSkill(Skill):
    def ai_priority_score(self, context: AISkillScoreContext) -> float:
        battle, actor = context.battle, context.actor
        if actor.attacks_used >= actor.attack_actions_per_turn() or actor.cannot_attack:
            return -14.0
        enemies = [unit for unit in context.hostile_units()
                   if unit.position is not None and not unit.cannot_heal
                   and not unit.physical_immunity and battle.distance_between_units(actor, unit) <= actor.targeting_range()]
        if not enemies:
            return -12.0
        return max(33.0 + min(30.0, max(0.0, unit.max_health - unit.current_hp) * 45.0)
                   + (24.0 if any(context.is_heal_skill(skill.code) for skill in unit.skills) else 0.0)
                   for unit in enemies)
    def __init__(self) -> None:
        super().__init__("ancient_rust", "生锈的气味", "每回合一次；下一次普攻破魔并令实际命中者禁疗至本人破坏。",
                         max_uses_per_turn=1, target_mode="self")

    def can_use(self, battle: Battle, actor: Unit, payload: dict[str, Any] | None = None) -> tuple[bool, str]:
        ok, reason = super().can_use(battle, actor, payload)
        if ok and actor.has_status("生锈的气味预备"):
            return False, "生锈的气味已经预备，先完成下一次攻击。"
        return ok, reason

    def execute(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> None:
        actor.add_status(AncientRustReadyStatus(), source=actor)


class AncientMechanicalHorseCooldown(StatusEffect):
    def __init__(self, duration: int) -> None:
        super().__init__("机械马召回冷却", "坐骑被毁后隔一本人回合才能再召。",
                         duration=duration, tick_scope="owner_turn_end")


class AncientMechanicalHorseRideableTrait(Trait):
    def __init__(self) -> None:
        super().__init__("可乘骑", "骑乘时由机械马承受伤害与技能，骑士仍可替马连锁。")

    def on_owner_removed(self, battle: Battle) -> None:
        horse = self.owner
        if horse is None or horse.alive or not horse.mount_owner_id:
            return
        rider = battle.units.get(horse.mount_owner_id)
        if rider is None or not rider.alive:
            return
        duration = 2 if battle.current_turn_slot_unit_id() == rider.unit_id else 1
        previous = rider.get_status("机械马召回冷却")
        if previous is not None:
            rider.remove_status(previous, battle)
        rider.add_status(AncientMechanicalHorseCooldown(duration))


class AncientMechanicalHorse(AbstractHero):
    hero_code = "ancient_mechanical_horse"
    hero_name = "机械马"
    role = "坐骑"
    attribute = "土"
    race = "机甲"
    level = 1
    base_stats = Stats(attack=4, defense=5, speed=4, attack_range=1, mana=0)
    footprint_width = 1
    footprint_height = 2
    raw_skill_text = ""
    raw_trait_text = "可乘骑"

    def __init__(self, player_id: int) -> None:
        super().__init__(player_id, is_summon=True)

    def build_skills(self) -> list[Skill]:
        return []

    def build_traits(self) -> list[Trait]:
        return [AncientMechanicalHorseRideableTrait()]


def ancient_mechanical_horse(battle: Battle, rider: Unit) -> Unit | None:
    return next((unit for unit in battle.all_units()
                 if isinstance(unit, AncientMechanicalHorse) and unit.mount_owner_id == rider.unit_id
                 and unit.alive and not unit.banished and unit.position is not None), None)


class AncientMechanicalHorseStartTrait(Trait):
    def __init__(self) -> None:
        super().__init__("机械马开局乘骑", "骑士出场时已召唤纵向1×2机械马并乘骑。")

    def on_enter_battle(self, battle: Battle) -> None:
        rider = self.owner
        if rider is None or rider.position is None or ancient_mechanical_horse(battle, rider) is not None:
            return
        horse = AncientMechanicalHorse(rider.player_id)
        horse.summoner_id = rider.unit_id
        horse.mount_owner_id = rider.unit_id
        horse.is_mount = True
        horse.can_act_on_entry_turn = True
        horse.turn_ready = True
        battle.add_unit(horse, rider.position)
        battle.set_mounted_state(rider, horse)


class AncientMechanicalHorseSkill(Skill):
    def ai_priority_score(self, context: AISkillScoreContext) -> float:
        battle, actor = context.battle, context.actor
        if battle.mounted_unit_for(actor) is not None:
            return -1000.0
        enemies = list(context.hostile_units())
        danger = min((battle.distance_between_units(actor, enemy) for enemy in enemies
                      if enemy.position is not None), default=99)
        return 76.0 + (24.0 if danger <= 3 else 0.0) + (18.0 if actor.current_hp <= 0.5 else 0.0)
    def __init__(self) -> None:
        super().__init__("mechanical_horse", "机械马",
                         "召唤并乘骑攻4守5速4、纵向1×2的机械马；被毁后隔一本人回合可再召。",
                         target_mode="self")

    def can_use(self, battle: Battle, actor: Unit, payload: dict[str, Any] | None = None) -> tuple[bool, str]:
        ok, reason = super().can_use(battle, actor, payload)
        if not ok:
            return ok, reason
        if actor.position is None or ancient_mechanical_horse(battle, actor) is not None:
            return False, "自己的机械马仍在场。"
        if battle.mounted_unit_for(actor) is not None:
            return False, "当前已乘骑其他坐骑，不能再召机械马。"
        if actor.has_status("机械马召回冷却"):
            return False, "机械马需要隔一本人回合才能再召。"
        probe = AncientMechanicalHorse(actor.player_id)
        probe.mount_owner_id = actor.unit_id
        probe.is_mount = True
        if not battle.can_place_unit(probe, actor.position, ignore=probe, mover=probe):
            return False, "当前位置容不下完整机械马。"
        return True, ""

    def execute(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> None:
        if actor.position is None:
            raise ActionMiss("机械马召唤位置失效。")
        horse = AncientMechanicalHorse(actor.player_id)
        horse.mount_owner_id = actor.unit_id
        horse.is_mount = True
        battle.summon_unit(horse, actor.position, summoner=actor)
        battle.set_mounted_state(actor, horse)


class AncientDamageModeStatus(StatusEffect):
    def __init__(self, mode: str) -> None:
        name = "古代伤害适应·普攻" if mode == "attack" else "古代伤害适应·技能"
        super().__init__(name, "本回合只继续承受同类生命伤害。",
                         duration=1, tick_scope="any_turn_end")
        self.mode = mode

    def on_final_damage(self, battle: Battle, ctx: DamageContext) -> None:
        if ctx.target is not self.owner or ctx.from_field_effect:
            return
        incoming = "skill" if ctx.is_skill else "attack" if "attack" in ctx.tags else None
        if incoming is not None and incoming != self.mode:
            ctx.raw_damage = 0.0
            ctx.modifier_receipts.add("ancient_damage_mode")
            battle.log_public_event(f"{ctx.target.name} 的古代伤害适应挡下异类生命伤害。",
                                    source=ctx.source, target=ctx.target)


class AncientDamageModeTrait(Trait):
    def __init__(self) -> None:
        super().__init__("古代伤害适应", "本人及其召唤物受普攻或技能实际伤害后，本回合只再受同类生命伤害。")

    def on_battle_damage(self, battle: Battle, ctx: DamageContext) -> None:
        owner = self.owner
        target = ctx.target
        if (owner is None or ctx.actual_damage <= 0 or ctx.from_field_effect
                or (target is not owner and target.summoner_id != owner.unit_id
                    and target.mount_owner_id != owner.unit_id)):
            return
        mode = "skill" if ctx.is_skill else "attack" if "attack" in ctx.tags else None
        if mode is None or any(isinstance(status, AncientDamageModeStatus) for status in target.statuses):
            return
        target.add_status(AncientDamageModeStatus(mode), source=owner)
        battle.log_public_event(f"{target.name} 本回合进入{('普攻' if mode == 'attack' else '技能')}伤害适应。",
                                source=owner, target=target)


def ancient_cursed_rust_targets(battle: Battle, cells: list[Position]) -> list[Unit]:
    area = set(cells)
    targets: list[Unit] = []
    seen: set[str] = set()
    for hero in battle.all_units():
        if (not isinstance(hero, HeroUnit) or hero.is_summon or hero.is_clone or is_army_soldier(hero)
                or not hero.alive or hero.banished or hero.position is None or hero.race == "机甲"
                or not any(cell in area for cell in battle.unit_cells(hero))):
            continue
        recipient = battle.effect_recipient(hero)
        if recipient.unit_id not in seen:
            seen.add(recipient.unit_id)
            targets.append(recipient)
    return targets


def resolve_ancient_cursed_rust(battle: Battle, actor: Unit, queued_action: Any) -> None:
    for target in ancient_cursed_rust_targets(battle, queued_action.target_cells):
        battle.resolve_damage(DamageContext(
            source=actor, target=target, attack_power=0, is_skill=True,
            action_name="诅咒的锈迹", raw_damage=round(target.current_hp / 2, 4),
            ignore_shield=True, tags={"skill", "ancient_cursed_rust"},
        ))


class AncientCursedRustSkill(Skill):
    def ai_priority_score(self, context: AISkillScoreContext) -> float:
        battle, actor = context.battle, context.actor
        score = -10.0
        enemies = 0
        for target in ancient_cursed_rust_targets(battle, self.cells(battle, actor)):
            if target.magic_immunity:
                continue
            if target.total_shields() > 0:
                impact = 18.0
            elif target.dodge_charges > 0:
                impact = 8.0
            elif target.has_status("古代伤害适应·普攻"):
                impact = 0.0
            else:
                damage = min(target.current_hp, target.damage_fraction_after_limits(target.current_hp / 2))
                impact = damage * 145.0
            if target.player_id == actor.player_id:
                score -= impact * 1.6
            else:
                score += impact
                enemies += int(impact > 0)
        return score if enemies and score > 0 else -1000.0
    target_cells_are_geometry_only = True

    def __init__(self) -> None:
        super().__init__("ancient_cursed_rust", "诅咒的锈迹",
                         "每本人轮一次；周围7×7双方非机甲武将当前血减半，破魔。",
                         max_uses_per_turn=1, target_mode="self")

    def ignores_shield_for_payload(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> bool:
        return True

    def cells(self, battle: Battle, actor: Unit) -> list[Position]:
        if actor.position is None:
            return []
        return [Position(x, y) for y in range(max(0, actor.position.y - 3),
                                                min(battle.height, actor.position.y + 4))
                for x in range(max(0, actor.position.x - 3),
                               min(battle.width, actor.position.x + 4))]

    def chosen_cells(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> list[Position]:
        if payload.get("queued_resolution") and payload.get("cursed_rust_cells"):
            return [Position(**cell) for cell in payload["cursed_rust_cells"]]
        return self.cells(battle, actor)

    def queued_payload_metadata(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> dict[str, Any]:
        return {"cursed_rust_cells": positions_to_dict(self.cells(battle, actor))}

    def get_target_cells_for_payload(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> list[Position]:
        return self.chosen_cells(battle, actor, payload)

    def get_target_units_for_payload(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> list[Unit]:
        return ancient_cursed_rust_targets(battle, self.chosen_cells(battle, actor, payload))

    def execute(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> None:
        cells = self.chosen_cells(battle, actor, payload)
        targets = ancient_cursed_rust_targets(battle, cells)
        battle.queue_skill_effect_action(actor=actor, display_name=self.name,
                                         effect_code=self.code, target_units=targets,
                                         payload={"ignore_shield": True},
                                         target_cells=cells, include_cell_units=False,
                                         effect_resolver=resolve_ancient_cursed_rust)

    def preview(self, battle: Battle, actor: Unit) -> dict[str, Any]:
        cells = self.cells(battle, actor)
        return {"cells": positions_to_dict(cells),
                "target_unit_ids": [unit.unit_id for unit in ancient_cursed_rust_targets(battle, cells)],
                "secondary_cells": [], "requires_target": False}


class AncientJawWeaknessStatus(StatModifierStatus):
    remove_when_source_destroyed = True

    def __init__(self, source_unit_id: str, stat_name: str) -> None:
        deltas = {"attack": "attack_delta", "defense": "defense_delta", "speed": "speed_delta",
                  "attack_range": "range_delta", "mana": "mana_delta"}
        super().__init__("溶解之颌弱化", description=f"{stat_name}-1，原机械兽破坏时解除。",
                         **{deltas[stat_name]: -1})
        self.source_unit_id = source_unit_id
        self.stat_name = stat_name

    def on_removed(self, battle: Battle) -> None:
        if self.owner is not None:
            self.owner.clamp_mana()


class AncientJawSkill(Skill):
    def ai_priority_score(self, context: AISkillScoreContext) -> float:
        battle, actor = context.battle, context.actor
        target = battle.units.get(str(context.payload.get("target_unit_id") or ""))
        target = battle.effect_recipient(target) if target is not None else None
        if target is None or target.player_id == actor.player_id or target.magic_immunity:
            return -1000.0
        score = context.r18_effect_score(self)
        if score <= -1000.0:
            return score
        if not target.skill_non_damage_effects_blocked() and target.dodge_charges <= 0:
            layers = sum(status.name == "溶解之颌弱化" for status in target.statuses)
            score += max(9.0, 42.0 - layers * 9.0) + target.stat("attack") * 3.0
        last = target.skill_map().get(str(getattr(target, "_last_active_skill_code", "") or ""))
        owned = actor.skill_map().get(last.code) if last is not None else None
        if (last is not None and last.timing == "active"
                and (owned is None or getattr(owned, "_ancient_stolen_charges", 1) <= 0)):
            if actor.current_mana >= last.mana_cost:
                score += 22.0 + (12.0 if context.is_damaging_skill(last.code) else 0.0)
        return score
    def __init__(self) -> None:
        super().__init__("ancient_jaw", "溶解之颌", "每本人回合一次；范2，当前攻单体技能伤害破魔，真实命中随机一项能力-1至本人破坏。",
                         max_uses_per_turn=1, target_mode="unit")

    def direct_unit_target_range(self, battle: Battle, actor: Unit,
                                 payload: dict[str, Any] | None = None) -> int:
        return 2

    def ignores_shield_for_payload(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> bool:
        return True

    def execute(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> None:
        target = battle.get_unit(str(payload.get("resolved_target_unit_id") or payload.get("target_unit_id") or ""))
        ctx = battle.resolve_damage(DamageContext(
            source=actor, target=target, attack_power=actor.stat("attack"), is_skill=True,
            action_name=self.name, ignore_shield=True, tags={"skill", self.code},
        ))
        recipient = ctx.target
        if recipient.alive and damage_followup_effect_applies(ctx) and not recipient.skill_non_damage_effects_blocked():
            if direct_scoring():
                weakness = AncientJawWeaknessStatus(actor.unit_id, "attack")
                weakness.stat_name = "expected"
                for attribute in ("attack_delta", "defense_delta", "speed_delta", "range_delta", "mana_delta"):
                    setattr(weakness, attribute, -.2 * ctx.forecast_hit_probability)
                recipient.add_status(weakness, source=actor)
                recipient.clamp_mana()
                return
            stat_name = random.choice(("attack", "defense", "speed", "attack_range", "mana"))
            weakness = AncientJawWeaknessStatus(actor.unit_id, stat_name)
            recipient.add_status(weakness, source=actor)
            if weakness in recipient.statuses:
                recipient.clamp_mana()
                battle.log_public_event(f"{recipient.name} 被溶解之颌削弱了{stat_name}。", source=actor, target=recipient)

    def preview(self, battle: Battle, actor: Unit) -> dict[str, Any]:
        targets = [unit for unit in battle.all_units() if unit.alive and unit.position is not None
                   and battle.unit_can_be_selected(unit, actor=actor)[0]
                   and battle.unit_target_in_range_and_line(actor, unit, 2)]
        return {"cells": positions_to_dict([cell for unit in targets for cell in battle.unit_cells(unit)]),
                "target_unit_ids": [unit.unit_id for unit in targets], "secondary_cells": [], "requires_target": True}


class AncientBeastTheftTrait(Trait):
    def __init__(self) -> None:
        super().__init__("古代机械兽夺技", "造成生命伤害后获得受体最近公开主动技能的一次使用权；真正击毁单位时该单位失去特性。")

    def on_after_damage(self, battle: Battle, ctx: DamageContext) -> None:
        owner = self.owner
        if owner is None or ctx.source is not owner:
            return
        target = ctx.target
        if not target.alive:
            removed = [trait.name for trait in target.traits]
            target.traits.clear()
            if removed:
                battle.log_public_event(f"{owner.name} 击毁{target.name}，其特性消失：{'、'.join(removed)}。",
                                        source=owner, target=target)
        if (ctx.cancelled or ctx.actual_damage <= 0
                or (target.alive and not battle.unit_can_be_selected(target, actor=owner)[0])
                or target.is_stealthed()):
            return
        code = str(getattr(target, "_last_active_skill_code", "") or "")
        definition = target.skill_map().get(code)
        if definition is None or definition.timing != "active":
            return
        own = owner.skill_map().get(code)
        if own is not None and not hasattr(own, "_ancient_stolen_charges"):
            battle.log_public_event(f"{owner.name} 已掌握【{definition.name}】，没有新增独立额度。", source=owner)
            return
        if own is None:
            fresh = copy(definition)
            BattleComponent.__init__(fresh, fresh.name, fresh.description)
            fresh.uses_this_turn = fresh.uses_this_battle = fresh.cooldown_remaining = 0
            fresh._uses_turn_number = None
            if hasattr(fresh, "borrowed"):
                fresh.borrowed = {}
            fresh._ancient_stolen_charges = 0
            owner.skills.append(fresh.bind(owner))
            own = fresh
        own._ancient_stolen_charges += 1
        battle.log_public_event(f"{owner.name} 从{target.name}取得【{definition.name}】一次使用权。", source=owner, target=target)


class AncientArmSkill(RemoteDragonBreathSkill):
    def ai_priority_score(self, context: AISkillScoreContext) -> float:
        return context.r18_effect_score(self)
    excludes_caster_from_effect = False

    def __init__(self) -> None:
        super().__init__()
        self.code = "ancient_arm"
        self.name = "机械臂"
        self.description = "每本人回合一次；范5的2×2区域按当前攻伤双方，命中护盾时半破魔。"
        self.mana_cost = 0
        self.max_uses_per_turn = 1

    def patterns(self, battle: Battle, actor: Unit) -> list[list[Position]]:
        return remote_rectangle_patterns(battle, actor, 2, 2, attack_range=5)

    def half_ignores_shield_for_payload(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> bool:
        return True

    def execute(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> None:
        cells = battle.payload_positions(payload, "declared_area_cells") if "declared_area_cells" in payload else self.chosen_cells(battle, actor, payload)
        for target in battle.effect_units_at_cells(cells):
            battle.resolve_damage(DamageContext(
                source=actor, target=target, attack_power=actor.stat("attack"), is_skill=True,
                action_name=self.name, half_ignore_shield=True,
                area_cell_hits=battle.unit_hit_count_for_cells(target, cells), tags={"skill", self.code},
            ))

    def preview(self, battle: Battle, actor: Unit) -> dict[str, Any]:
        preview = pattern_selection_preview(self.patterns(battle, actor))
        covered = {cell for pattern in self.patterns(battle, actor) for cell in pattern}
        preview.update({"target_unit_ids": [unit.unit_id for unit in battle.effect_units_at_cells(list(covered))
                                             if battle.unit_can_be_selected(unit, actor=actor)[0]],
                        "secondary_cells": [], "requires_target": True})
        return preview

    def get_target_units_for_payload(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> list[Unit]:
        return battle.effect_units_at_cells(self.chosen_cells(battle, actor, payload))


class AncientVigorSkill(Skill):
    def ai_priority_score(self, context: AISkillScoreContext) -> float:
        battle, actor, payload = context.battle, context.actor, context.payload
        missing = 0.0 if actor.cannot_heal else max(0.0, actor.max_health - actor.current_hp)
        restored = min(0.25, missing)
        if actor.attribute == "暗" or restored <= 0:
            return -1000.0
        destination = Position(int(payload["x"]), int(payload["y"])) if payload.get("x") is not None else actor.position
        if destination is None:
            return -1000.0
        movement = 0.0
        if destination != actor.position:
            style = context.hero_style()
            movement = context.move_score(destination, style) - context.move_score(actor.position, style)
        return restored * (260.0 if actor.current_hp <= 0.5 else 175.0) + movement - 8.0
    def __init__(self) -> None:
        super().__init__("ancient_vigor", "活力锈迹", "每本人回合一次；先回血1/4，实得回血后可沿直线恰好移动4格。",
                         max_uses_per_turn=1, target_mode="cell")

    def destinations(self, battle: Battle, actor: Unit) -> list[Position]:
        if actor.position is None or actor.cannot_move:
            return []
        spots = []
        for dx, dy in ALL_DIRECTIONS:
            destination = actor.position.offset(dx * 4, dy * 4)
            if not battle.in_bounds(destination) or not battle.can_place_unit(actor, destination, ignore=actor, mover=actor):
                continue
            try:
                battle.find_path(actor, destination, max_distance=4, exact_distance=4, straight_only=True)
            except ActionError:
                continue
            spots.append(destination)
        return spots

    def can_use(self, battle: Battle, actor: Unit, payload: dict[str, Any] | None = None) -> tuple[bool, str]:
        ok, reason = super().can_use(battle, actor, payload)
        if not ok:
            return ok, reason
        if payload and payload.get("x") is not None:
            destination = payload_position(payload)
            if destination != actor.position and destination not in self.destinations(battle, actor):
                return False, "活力锈迹只可选择合法的直线四格落点或原地放弃。"
        return True, ""

    def execute(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> None:
        healed = battle.heal(HealContext(source=actor, target=actor, amount=0.25, action_name=self.name, tags={"skill"}))
        if healed.actual_heal <= 0 or payload.get("x") is None or actor.position is None:
            return
        destination = payload_position(payload)
        if destination == actor.position:
            return
        try:
            battle.move_unit(actor, destination, via_skill=True, max_distance=4, exact_distance=4, straight_only=True)
        except ActionError:
            battle.log_public_event(f"{actor.name} 已回血，但四格落点不再合法，位移落空。", source=actor)

    def preview(self, battle: Battle, actor: Unit) -> dict[str, Any]:
        cells = ([actor.position] if actor.position is not None else []) + self.destinations(battle, actor)
        return {"cells": positions_to_dict(cells), "target_unit_ids": [], "secondary_cells": [],
                "requires_target": True}

    def get_target_cells_for_payload(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> list[Position]:
        return [payload_position(payload)] if payload.get("x") is not None else battle.unit_cells(actor)


