from __future__ import annotations

import random
from typing import Any

from wujiang.engine.core import (
    ActionError,
    Battle,
    BattleFieldEffect,
    DamageContext,
    HeroUnit,
    Position,
    QueuedAction,
    Skill,
    Stats,
)
from wujiang.heroes.base import AbstractHero
from wujiang.heroes.common import (
    AttackCountTrait,
    BackstepShotSkill,
    BaptismSkill,
    BlockCounterTrait,
    ChantSkill,
    CrystalBallStatus,
    CurseStatus,
    DashMoveSkill,
    DefendTwiceSkill,
    DelayedDarknessStatus,
    DrainManaSkill,
    EllieWardTrait,
    ExperimentCountdownStatus,
    FlagStatus,
    FlyingTrait,
    GreatHolyLightSkill,
    HardenSkill,
    HeadshotSkill,
    HealSkill,
    KnockbackSkill,
    MachineGunSkill,
    MagicImmuneWhenAttackOneTrait,
    MagicWallSkill,
    InvincibleUntilActionStatus,
    PassiveEvasionSkill,
    PassiveProtectionSkill,
    PierceSkill,
    PrecisionTrainingTrait,
    StatModifierStatus,
    StationaryRecoveryTrait,
    StealthSkill,
    payload_position,
    payload_target_unit,
    ensure_ally,
    ensure_distance,
    ensure_enemy,
    straight_direction,
)


class ManaPullSkill(Skill):
    def __init__(self) -> None:
        super().__init__(
            "mana_pull",
            "魔力牵引",
            "将目标沿指定方向移动 1-3 格；若是敌方目标，则其下次行动时不能移动。",
            mana_cost=1,
            max_uses_per_turn=1,
            target_mode="unit",
        )

    def execute(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> None:
        target = payload_target_unit(battle, payload)
        destination = payload_position(payload, "dest_x", "dest_y")
        ensure_distance(actor, target, actor.targeting_range())
        if target.position is None:
            raise ActionError("目标不在战场上。")
        direction = straight_direction(target.position, destination)
        steps = target.position.distance_to(destination)
        if steps < 1 or steps > 3:
            raise ActionError("魔力牵引必须移动 1 到 3 格。")
        if actor.player_id != target.player_id:
            target_ctx = battle.validate_target(actor, target, action_name="魔力牵引", is_skill=True, is_hostile=True)
            if target_ctx.cancelled:
                battle.log(target_ctx.reason)
                return
        battle.move_unit(
            target,
            destination,
            via_skill=True,
            straight_only=True,
            ignore_units=True,
            max_distance=steps,
            triggered_by_reaction=True,
            tags={"mana_pull", f"dir:{direction[0]},{direction[1]}"},
        )
        if actor.player_id != target.player_id:
            target.add_status(
                FlagStatus(
                    "牵引迟滞",
                    "cannot_move",
                    description="下次行动时不能移动。",
                    duration=1,
                    tick_scope="owner_turn_end",
                )
            )
            battle.log(f"{target.name} 被魔力牵引束缚，下次行动时无法移动。")

    def preview(self, battle: Battle, actor: HeroUnit) -> dict[str, Any]:
        targets = [unit for unit in battle.all_units() if unit.position and actor.position and actor.position.distance_to(unit.position) <= actor.targeting_range() and unit.unit_id != actor.unit_id]
        return {"cells": [unit.position.to_dict() for unit in targets if unit.position], "target_unit_ids": [unit.unit_id for unit in targets], "secondary_cells": [], "requires_target": True}

    def get_target_units_for_payload(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> list[HeroUnit]:
        if payload.get("target_unit_id"):
            return [payload_target_unit(battle, payload)]
        return []


class CurseSkill(Skill):
    def __init__(self) -> None:
        super().__init__("curse", "诅咒", "自己失去 1/2 生命，并让目标每轮生命减半。", max_uses_per_battle=1, target_mode="enemy")

    def execute(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> None:
        target = payload_target_unit(battle, payload)
        ensure_enemy(actor, target)
        ensure_distance(actor, target, actor.targeting_range())
        target_ctx = battle.validate_target(actor, target, action_name="诅咒", is_skill=True, is_hostile=True)
        if target_ctx.cancelled:
            battle.log(target_ctx.reason)
            return
        actor.take_damage_fraction(0.5)
        target.add_status(CurseStatus())
        battle.log(f"{actor.name} 施加了诅咒。")
        battle.cleanup_dead_units()

    def preview(self, battle: Battle, actor: HeroUnit) -> dict[str, Any]:
        targets = [unit for unit in battle.enemy_units(actor.player_id) if unit.position and actor.position and actor.position.distance_to(unit.position) <= actor.targeting_range()]
        return {"cells": [unit.position.to_dict() for unit in targets if unit.position], "target_unit_ids": [unit.unit_id for unit in targets], "secondary_cells": [], "requires_target": True}


class ExperimentSkill(Skill):
    def __init__(self) -> None:
        super().__init__("experiment", "实验", "令一名己方单位全能力 +2，3 轮后死亡。", max_uses_per_battle=1, target_mode="ally")

    def execute(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> None:
        target = payload_target_unit(battle, payload)
        ensure_ally(actor, target)
        ensure_distance(actor, target, actor.targeting_range())
        target.add_status(
            StatModifierStatus(
                "实验",
                attack_delta=2,
                defense_delta=2,
                speed_delta=2,
                range_delta=2,
                duration=3,
                tick_scope="any_turn_end",
                description="全能力 +2。",
            )
        )
        target.current_mana = round(target.current_mana + 2, 2)
        target.add_status(ExperimentCountdownStatus(duration=3))
        battle.log(f"{target.name} 接受了实验强化。")

    def preview(self, battle: Battle, actor: HeroUnit) -> dict[str, Any]:
        targets = [unit for unit in battle.player_units(actor.player_id) if unit.position and actor.position and actor.position.distance_to(unit.position) <= actor.targeting_range()]
        return {"cells": [unit.position.to_dict() for unit in targets if unit.position], "target_unit_ids": [unit.unit_id for unit in targets], "secondary_cells": [], "requires_target": True}


class CrystalBallSkill(Skill):
    def __init__(self) -> None:
        super().__init__("crystal_ball", "水晶球", "接下来 2轮目标范围变为全图。", max_uses_per_battle=1, target_mode="self")

    def execute(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> None:
        actor.add_status(CrystalBallStatus(duration=4))
        battle.log(f"{actor.name} 展开了水晶球。")

    def preview(self, battle: Battle, actor: HeroUnit) -> dict[str, Any]:
        return {"cells": [actor.position.to_dict()] if actor.position else [], "target_unit_ids": [actor.unit_id], "secondary_cells": [], "requires_target": False}


class MedusaSummon(AbstractHero):
    hero_code = "medusa"
    hero_name = "美杜莎"
    role = "召唤物"
    attribute = "暗"
    race = "魔像"
    level = 1
    base_stats = Stats(attack=3, defense=99, speed=2, attack_range=1, mana=0)
    raw_skill_text = "瞬移"
    raw_trait_text = "攻击四次"

    def __init__(self, player_id: int) -> None:
        super().__init__(player_id, is_summon=True)

    def build_skills(self) -> list[Skill]:
        return [
            DashMoveSkill(
                "medusa_blink",
                "瞬移",
                "瞬移到任意空格。",
                max_distance=99,
                mana_cost=0,
                max_uses_per_turn=1,
                allow_anywhere=True,
                ignore_units=True,
            )
        ]

    def build_traits(self) -> list:
        return [AttackCountTrait(4)]


class MedusaSkill(Skill):
    def __init__(self) -> None:
        super().__init__("medusa", "美杜莎", "在范围内召唤美杜莎。", max_uses_per_battle=1, target_mode="cell")

    def execute(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> None:
        destination = payload_position(payload)
        ensure_distance(actor, destination, actor.targeting_range())
        if battle.is_occupied(destination):
            raise ActionError("召唤位置已被占用。")
        battle.summon_unit(MedusaSummon(actor.player_id), destination, summoner=actor)

    def preview(self, battle: Battle, actor: HeroUnit) -> dict[str, Any]:
        if actor.position is None:
            return {"cells": [], "target_unit_ids": [], "secondary_cells": [], "requires_target": True}
        cells = [
            Position(x, y).to_dict()
            for x in range(battle.width)
            for y in range(battle.height)
            if actor.position.distance_to(Position(x, y)) <= actor.targeting_range() and not battle.is_occupied(Position(x, y))
        ]
        return {"cells": cells, "target_unit_ids": [], "secondary_cells": [], "requires_target": True}


class ParalyzingGloveSkill(Skill):
    def __init__(self) -> None:
        super().__init__(
            "paralyzing_glove",
            "麻痹手套",
            "破魔，造成 4 点伤害，并使目标 1.5轮不能移动。",
            max_uses_per_battle=1,
            target_mode="enemy",
        )

    def execute(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> None:
        target = payload_target_unit(battle, payload)
        ensure_enemy(actor, target)
        ensure_distance(actor, target, actor.targeting_range())
        target_ctx = battle.validate_target(
            actor,
            target,
            action_name="麻痹手套",
            is_skill=True,
            is_hostile=True,
            ignore_shield=True,
        )
        if target_ctx.cancelled:
            battle.log(target_ctx.reason)
            return
        battle.resolve_damage(
            DamageContext(
                source=actor,
                target=target,
                attack_power=4,
                is_skill=True,
                action_name="麻痹手套",
                ignore_shield=True,
                tags={"skill"},
            )
        )
        target.add_status(
            FlagStatus(
                "麻痹",
                "cannot_move",
                description="无法移动。",
                duration=3,
                tick_scope="owner_turn_end",
            )
        )
        battle.log(f"{target.name} 被麻痹手套束缚。")

    def preview(self, battle: Battle, actor: HeroUnit) -> dict[str, Any]:
        targets = [unit for unit in battle.enemy_units(actor.player_id) if unit.position and actor.position and actor.position.distance_to(unit.position) <= actor.targeting_range()]
        return {"cells": [unit.position.to_dict() for unit in targets if unit.position], "target_unit_ids": [unit.unit_id for unit in targets], "secondary_cells": [], "requires_target": True}


    def ignores_shield_for_payload(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> bool:
        return True


class FateKickSkill(Skill):
    def __init__(self) -> None:
        super().__init__("fate_kick", "命运飞踢", "直线冲刺至多 4 格，再判定前方单位；硬币正面则自己消失 1轮，反面则目标消失 1轮。", cooldown_turns=2, target_mode="cell")

    def reaction_window_timing(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> str:
        return "after"

    def execute(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> None:
        if actor.position is None:
            raise ActionError("单位不在战场上。")
        destination = payload_position(payload)
        direction = straight_direction(actor.position, destination)
        if actor.position.distance_to(destination) > 4:
            raise ActionError("命运飞踢最多位移 4 格。")
        battle.move_unit(
            actor,
            destination,
            via_skill=True,
            straight_only=True,
            ignore_units=True,
            max_distance=4,
            tags={"fate_kick"},
        )
        impact = destination.offset(*direction)
        if not battle.in_bounds(impact):
            return
        target = battle.unit_at(impact)
        if target is None or target.player_id == actor.player_id:
            return
        if random.random() < 0.5:
            battle.banish_unit(actor, 2)
            battle.log(f"{actor.name} 的命运飞踢判定为正面，自身消失 1轮。")
        else:
            battle.log(f"{actor.name} 的命运飞踢判定为反面，{target.name} 将在此效果结算时消失 1轮。")
            battle.present_reaction_window_or_resolve(
                QueuedAction(
                    action_type="skill_effect",
                    actor_id=actor.unit_id,
                    display_name="命运飞踢",
                    speed=self.chain_speed,
                    payload={
                        "effect_code": "banish",
                        "banish_turns": 2,
                        "declared_target_x": impact.x,
                        "declared_target_y": impact.y,
                        "success_log": "{actor} 的命运飞踢判定为反面，{target} 消失 1轮。",
                    },
                    target_unit_ids=[target.unit_id],
                    target_cells=[impact],
                    source_player_id=actor.player_id,
                    hostile=True,
                )
            )

    def preview(self, battle: Battle, actor: HeroUnit) -> dict[str, Any]:
        if actor.position is None:
            return {"cells": [], "target_unit_ids": [], "secondary_cells": [], "requires_target": True}
        cells = battle.reachable_positions(actor, max_distance=4, straight_only=True, ignore_units=True)
        return {"cells": [cell.to_dict() for cell in cells], "target_unit_ids": [], "secondary_cells": [], "requires_target": True}

    def get_target_cells_for_payload(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> list[Position]:
        if actor.position is None:
            return []
        destination = payload_position(payload)
        direction = straight_direction(actor.position, destination)
        impact = destination.offset(*direction)
        if not battle.in_bounds(impact):
            return []
        return [impact]


class IntoDarknessSkill(Skill):
    def __init__(self) -> None:
        super().__init__(
            "into_darkness",
            "遁入黑暗",
            "持续 1轮：进入隐身且无法回复；若以普攻解除隐身，则那次普攻伤害 +1 且破魔。",
            cooldown_turns=4,
            target_mode="self",
        )

    def execute(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> None:
        existing_darkness = actor.get_status("遁入黑暗")
        if existing_darkness is not None:
            actor.remove_status(existing_darkness, battle)
        existing_stealth = actor.get_status("隐身")
        if existing_stealth is not None:
            actor.remove_status(existing_stealth, battle)
        actor.add_status(DelayedDarknessStatus(duration=2))
        actor.add_status(
            InvincibleUntilActionStatus(
                duration=2,
                tick_scope="any_turn_end",
                bonus_attack_on_attack_break=1,
                ignore_shield_on_attack_break=True,
                attack_break_buff_name="黑暗突袭",
                attack_break_buff_description="因遁入黑暗现身时，那次普攻伤害 +1 且破魔。",
            )
        )
        battle.log(f"{actor.name} 遁入了黑暗。")

    def preview(self, battle: Battle, actor: HeroUnit) -> dict[str, Any]:
        return {"cells": [actor.position.to_dict()] if actor.position else [], "target_unit_ids": [actor.unit_id], "secondary_cells": [], "requires_target": False}


class GreatFireFuneralField(BattleFieldEffect):
    def __init__(self, owner_unit_id: str, cells: set[tuple[int, int]]) -> None:
        super().__init__("大火葬余烬", "烈焰区域会以攻 5 灼伤停留单位，并灼烧穿行单位。", duration=None)
        self.owner_unit_id = owner_unit_id
        self.cells = cells

    def in_area(self, position: Position) -> bool:
        return (position.x, position.y) in self.cells

    def get_owner_unit(self, battle: Battle) -> HeroUnit | None:
        unit = battle.units.get(self.owner_unit_id)
        if unit is None:
            return None
        return unit  # type: ignore[return-value]

    def blocks_forced_movement(self, battle: Battle, position: Position) -> bool:
        return self.in_area(position)

    def affected_cells(self, battle: Battle) -> list[Position]:
        return [Position(x, y) for x, y in sorted(self.cells)]

    def board_marker(self, battle: Battle) -> str:
        return "火"

    def on_unit_moved(self, battle: Battle, ctx: Any) -> None:
        owner = self.get_owner_unit(battle)
        if owner is None or ctx.unit.unit_id == self.owner_unit_id:
            return
        if any(self.in_area(step) for step in ctx.path[1:]):
            damage = round(ctx.unit.current_hp / 2, 4)
            if damage <= 0:
                return
            battle.log(f"{ctx.unit.name} 穿过了大火葬区域。")
            battle.resolve_damage(
                DamageContext(
                    source=owner,
                    target=ctx.unit,
                    attack_power=0,
                    is_skill=True,
                    action_name="大火葬余烬",
                    raw_damage=damage,
                    ignore_magic_immunity=True,
                    cannot_evade=True,
                    tags={"fire_zone"},
                )
            )

    def on_any_turn_end(self, battle: Battle, ended_player_id: int) -> None:
        owner = self.get_owner_unit(battle)
        if owner is None:
            return
        for unit in battle.all_units():
            if unit.unit_id == self.owner_unit_id or unit.position is None or unit.banished:
                continue
            if unit.player_id != ended_player_id:
                continue
            if self.in_area(unit.position):
                battle.resolve_damage(
                    DamageContext(
                        source=owner,
                        target=unit,
                        attack_power=5,
                        is_skill=True,
                        action_name="大火葬余烬",
                        ignore_magic_immunity=True,
                        tags={"fire_zone"},
                    )
                )


class GreatFireFuneralSkill(Skill):
    def __init__(self) -> None:
        super().__init__("great_funeral", "大火葬", "命中自身所在横竖列，以攻 5 结算伤害并留下烈焰区域，使用后攻击 -1。", cooldown_turns=2, target_mode="self")

    def execute(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> None:
        if actor.position is None:
            raise ActionError("单位不在战场上。")
        affected: set[tuple[int, int]] = set()
        for x in range(battle.width):
            affected.add((x, actor.position.y))
        for y in range(battle.height):
            affected.add((actor.position.x, y))
        for unit in battle.all_units():
            if unit.unit_id == actor.unit_id or unit.position is None or unit.banished:
                continue
            if (unit.position.x, unit.position.y) in affected:
                battle.resolve_damage(
                    DamageContext(
                        source=actor,
                        target=unit,
                        attack_power=5,
                        is_skill=True,
                        action_name="大火葬",
                        ignore_magic_immunity=True,
                        tags={"fire", "skill"},
                    )
                )
        actor.base_stats.attack = max(actor.base_stats.attack - 1, 1)
        battle.add_field_effect(GreatFireFuneralField(actor.unit_id, affected))
        battle.log(f"{actor.name} 施放了大火葬，攻击降为 {actor.base_stats.attack}。")

    def preview(self, battle: Battle, actor: HeroUnit) -> dict[str, Any]:
        if actor.position is None:
            return {"cells": [], "target_unit_ids": [], "secondary_cells": [], "requires_target": False}
        cells = [Position(x, actor.position.y).to_dict() for x in range(battle.width)]
        cells.extend(Position(actor.position.x, y).to_dict() for y in range(battle.height))
        targets = [
            unit.unit_id
            for unit in battle.enemy_units(actor.player_id)
            if unit.position and (unit.position.x == actor.position.x or unit.position.y == actor.position.y)
        ]
        return {"cells": cells, "target_unit_ids": targets, "secondary_cells": [], "requires_target": False}

    def get_target_cells_for_payload(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> list[Position]:
        if actor.position is None:
            return []
        cells = [Position(x, actor.position.y) for x in range(battle.width)]
        cells.extend(Position(actor.position.x, y) for y in range(battle.height))
        return cells

    def get_target_units_for_payload(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> list[HeroUnit]:
        if actor.position is None:
            return []
        return [
            unit
            for unit in battle.all_units()
            if unit.position is not None
            and unit.unit_id != actor.unit_id
            and (unit.position.x == actor.position.x or unit.position.y == actor.position.y)
        ]


class JudgmentFireSkill(Skill):
    def __init__(self) -> None:
        super().__init__("judgment_fire", "审判日之火", "仅在攻击为 1 时才能使用；对全场除最低能力单位外造成 6 点伤害并禁疗。", max_uses_per_battle=1, target_mode="self")

    def can_use(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any] | None = None) -> tuple[bool, str]:
        ok, reason = super().can_use(battle, actor, payload)
        if not ok:
            return ok, reason
        if abs(actor.stat("attack") - 1) > 1e-9:
            return False, "只有攻击为 1 时才能使用审判日之火。"
        return True, ""

    def execute(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> None:
        if abs(actor.stat("attack") - 1) > 1e-9:
            raise ActionError("只有攻击为 1 时才能使用审判日之火。")
        units = [unit for unit in battle.all_units() if unit.position is not None]
        scores = {
            unit.unit_id: unit.stat("attack") + unit.stat("defense") + unit.stat("speed") + unit.targeting_range()
            for unit in units
        }
        minimum = min(scores.values())
        for unit in units:
            if scores[unit.unit_id] == minimum:
                continue
            battle.resolve_damage(
                DamageContext(
                    source=actor,
                    target=unit,
                    attack_power=0,
                    is_skill=True,
                    action_name="审判日之火",
                    raw_damage=6,
                    ignore_magic_immunity=True,
                    cannot_evade=True,
                    tags={"judgment_fire"},
                )
            )
            unit.add_status(
                FlagStatus(
                    "禁疗",
                    "cannot_heal",
                    description="无法回复生命。",
                    duration=3,
                    tick_scope="any_turn_end",
                )
            )
        battle.log(f"{actor.name} 施放了审判日之火。")

    def preview(self, battle: Battle, actor: HeroUnit) -> dict[str, Any]:
        cells = [unit.position.to_dict() for unit in battle.all_units() if unit.position]
        targets = [unit.unit_id for unit in battle.enemy_units(actor.player_id) if unit.position]
        return {"cells": cells, "target_unit_ids": targets, "secondary_cells": [], "requires_target": False}

    def get_target_cells_for_payload(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> list[Position]:
        return [unit.position for unit in self.get_target_units_for_payload(battle, actor, payload) if unit.position is not None]

    def get_target_units_for_payload(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> list[HeroUnit]:
        units = [unit for unit in battle.all_units() if unit.position is not None]
        if not units:
            return []
        scores = {
            unit.unit_id: unit.stat("attack") + unit.stat("defense") + unit.stat("speed") + unit.targeting_range()
            for unit in units
        }
        minimum = min(scores.values())
        return [unit for unit in units if scores[unit.unit_id] != minimum]

class Ellie(AbstractHero):
    hero_code = "ellie"
    hero_name = "艾莉"
    role = "法师"
    attribute = "暗"
    race = "人类"
    level = 8
    base_stats = Stats(attack=2, defense=2, speed=1, attack_range=1, mana=5)
    raw_skill_text = "魔墙 吸魔【1魔力牵引（每回合最多使用一次；可以对对方或者己方使用，被击中单位向指定方向移动1-3格，若是对方单位，则其下次行动时不能移动）￥诅咒（将自己的血下降0.5，场上一单位每1轮血*1/2）￥美杜莎（攻3守无限范1，每回合一次瞬移，攻4次）￥实验（己方一单位全能力+2，3轮后死亡） ￥水晶球（4轮内可攻击和技能场上任何单位）"
    raw_trait_text = "使用结束过主动技能单位当回合无法对此单位造成伤害"

    raw_skill_text = "\u9b54\u5899\uff08\u88ab\u52a8\u6280\u80fd\uff1b1\u9b54\uff1b\u53ef\u5bf9\u5df1\u65b9\u4f7f\u7528\uff1b\u52a0 1 \u76fe\uff09 \u5438\u9b54 \u30101\u9b54\u529b\u7275\u5f15\uff08\u6bcf\u56de\u5408\u6700\u591a\u4f7f\u7528\u4e00\u6b21\uff1b\u53ef\u4ee5\u5bf9\u654c\u65b9\u6216\u8005\u5df1\u65b9\u4f7f\u7528\uff0c\u88ab\u51fb\u4e2d\u5355\u4f4d\u5411\u6307\u5b9a\u65b9\u5411\u79fb\u52a81-3\u683c\uff0c\u82e5\u662f\u654c\u65b9\u5355\u4f4d\uff0c\u5219\u5176\u4e0b\u6b21\u884c\u52a8\u65f6\u4e0d\u80fd\u79fb\u52a8\uff09\uffe5\u8bc5\u5492\uff08\u5c06\u81ea\u5df1\u7684\u8840\u4e0b\u964d0.5\uff0c\u573a\u4e0a\u4e00\u5355\u4f4d\u5728\u6bcf\u4e2a\u5df1\u65b9\u56de\u5408\u5f00\u59cb\u65f6\u8840*1/2\uff09\uffe5\u7f8e\u675c\u838e\uff08\u653b3\u5b88\u65e0\u9650\u83031\uff0c\u6bcf\u56de\u5408\u4e00\u6b21\u77ac\u79fb\uff0c\u653b4\u6b21\uff0c\u767b\u573a\u5f53\u56de\u5408\u4e0d\u80fd\u884c\u52a8\uff09\uffe5\u5b9e\u9a8c\uff08\u5df1\u65b9\u4e00\u5355\u4f4d\u5168\u80fd\u529b+2\uff0c3\u8f6e\u540e\u6b7b\u4ea1\uff09 \uffe5\u6c34\u6676\u7403\uff084\u8f6e\u5185\u53ef\u653b\u51fb\u548c\u6280\u80fd\u573a\u4e0a\u4efb\u4f55\u5355\u4f4d\uff09"
    raw_trait_text = "\u4f7f\u7528\u7ed3\u675f\u8fc7\u4e3b\u52a8\u6280\u80fd\u5355\u4f4d\u5f53\u56de\u5408\u65e0\u6cd5\u5bf9\u6b64\u5355\u4f4d\u9020\u6210\u4f24\u5bb3"

    def build_skills(self) -> list[Skill]:
        return [MagicWallSkill(), DrainManaSkill(), ManaPullSkill(), CurseSkill(), MedusaSkill(), ExperimentSkill(), CrystalBallSkill()]

    def build_traits(self) -> list:
        return [EllieWardTrait()]


class DarkHuman(AbstractHero):
    hero_code = "dark_human"
    hero_name = "E。暗人"
    role = "刺客"
    attribute = "雷"
    race = "人类"
    level = 5
    base_stats = Stats(attack=3, defense=4, speed=4, attack_range=1, mana=4)
    raw_skill_text = "飞跃 保护 回避（被动技能；每回合最多2次；移动2格） 【1.5隐身（直到自己下次普攻或使用技能前进入无敌状态） ￥麻痹手套（破魔；伤4；被击中以后3轮不能移动）命运飞踢（2轮一次；直线移动至多4个以后向移动的方向造成以下效果：被击中的单位投硬币，如果正面，则自己消失1轮；若是反面则被击中单位消失1轮） 遁入黑暗（4轮一次；持续2轮；无法被选中且无法回复，此效果结束后的第一次攻击伤害+1并且破魔）"
    raw_trait_text = "攻击三次；飞行；使用隐身后重置“麻痹手套”;当回合没有移动的武将的主动技能对此单位无效"

    raw_skill_text = "\u98de\u8dc3 \u4fdd\u62a4 \u56de\u907f\uff08\u88ab\u52a8\u6280\u80fd\uff1b\u6bcf\u56de\u5408\u6700\u591a2\u6b21\uff1b\u79fb\u52a82\u683c\uff09 \u30101.5\u9690\u8eab\uff08\u76f4\u5230\u81ea\u5df1\u4e0b\u6b21\u666e\u653b\u6216\u4f7f\u7528\u6280\u80fd\u524d\u8fdb\u5165\u65e0\u654c\u72b6\u6001\uff09 \uffe5\u9ebb\u75f9\u624b\u5957\uff08\u7834\u9b54\uff1b\u4f244\uff1b\u88ab\u51fb\u4e2d\u4ee5\u540e3\u8f6e\u4e0d\u80fd\u79fb\u52a8\uff09\u547d\u8fd0\u98de\u8e22\uff082\u8f6e\u4e00\u6b21\uff1b\u76f4\u7ebf\u79fb\u52a8\u81f3\u591a4\u4e2a\u4ee5\u540e\u5411\u79fb\u52a8\u7684\u65b9\u5411\u9020\u6210\u4ee5\u4e0b\u6548\u679c\uff1a\u88ab\u51fb\u4e2d\u7684\u5355\u4f4d\u6295\u786c\u5e01\uff0c\u5982\u679c\u6b63\u9762\uff0c\u5219\u81ea\u5df1\u6d88\u59311\u8f6e\uff1b\u82e5\u662f\u53cd\u9762\u5219\u88ab\u51fb\u4e2d\u5355\u4f4d\u6d88\u59311\u8f6e\uff09 \u9041\u5165\u9ed1\u6697\uff084\u8f6e\u4e00\u6b21\uff1b\u6301\u7eed2\u8f6e\uff1b\u65e0\u6cd5\u88ab\u9009\u4e2d\u4e14\u65e0\u6cd5\u56de\u590d\uff0c\u6b64\u6548\u679c\u7ed3\u675f\u540e\u7684\u7b2c\u4e00\u6b21\u653b\u51fb\u4f24\u5bb3+1\u5e76\u4e14\u7834\u9b54\uff09"
    raw_trait_text = "\u653b\u51fb\u4e09\u6b21\uff1b\u98de\u884c\uff1b\u4f7f\u7528\u9690\u8eab\u540e\u91cd\u7f6e\u201c\u9ebb\u75f9\u624b\u5957\u201d"

    def build_skills(self) -> list[Skill]:
        return [
            DashMoveSkill("fly_leap", "飞跃", "直线飞行移动 4 格。", max_distance=4, mana_cost=1, max_uses_per_turn=1, straight_only=True, ignore_units=True),
            PassiveProtectionSkill(),
            PassiveEvasionSkill(),
            StealthSkill(),
            ParalyzingGloveSkill(),
            FateKickSkill(),
            IntoDarknessSkill(),
        ]

    def build_traits(self) -> list:
        return [AttackCountTrait(3), FlyingTrait()]


class FireFuneral(AbstractHero):
    hero_code = "fire_funeral"
    hero_name = "火葬者"
    role = "勇者"
    attribute = "火"
    race = "恶魔"
    level = 5
    base_stats = Stats(attack=4, defense=3, speed=2, attack_range=2, mana=4)
    raw_skill_text = "神速 变硬 穿刺 震开 大火葬（2轮一次；横竖全中，伤5，使用以后攻-1；之后被攻击的区域每个玩家的己方回合结束时都会受到5的伤害；且穿过被此技能击中的区域的单位血*1/2；此技能的效果无视魔免；此技能不会对此单位造成伤害） ￥审判日之火（仅在攻击为1时才能使用；给与全场除了能力值最低的单位以外6的伤害；无视魔免；无法回避；3轮不能回血）"
    raw_trait_text = "此单为攻击为1时魔免；可格挡，反击"

    def build_skills(self) -> list[Skill]:
        return [
            DashMoveSkill("shensu", "神速", "移动 4 格。", max_distance=4, mana_cost=1, max_uses_per_turn=1),
            HardenSkill(),
            PierceSkill(),
            KnockbackSkill(),
            GreatFireFuneralSkill(),
            JudgmentFireSkill(),
        ]

    def build_traits(self) -> list:
        return [MagicImmuneWhenAttackOneTrait(), BlockCounterTrait()]


class EliteSoldier(AbstractHero):
    hero_code = "elite_soldier"
    hero_name = "精兵"
    role = "弓箭"
    attribute = "土"
    race = "人类"
    level = 4
    base_stats = Stats(attack=3, defense=2, speed=2, attack_range=14, mana=3)
    raw_skill_text = "机枪 神速 爆头（一回合一次；此回合此单位的第一个效果无效；下一次攻击伤害+2并破魔） 【0.5 撤步射击（被动技能；向一个方向直线位移两格，之后进行一次普攻；一回合最多使用2次）"
    raw_trait_text = "普攻范围是周围（范*2+1）*（范*2+1）；普攻带有以下破魔效果：1/3几率使被攻击单位下次行动时速-2，最低到1"

    def build_skills(self) -> list[Skill]:
        return [
            MachineGunSkill(),
            DashMoveSkill("shensu", "神速", "移动 4 格。", max_distance=4, mana_cost=1, max_uses_per_turn=1),
            HeadshotSkill(),
            BackstepShotSkill(),
        ]

    def build_traits(self) -> list:
        return [PrecisionTrainingTrait()]


class Bard(AbstractHero):
    hero_code = "bard"
    hero_name = "吟游诗人"
    role = "贤者"
    attribute = "木"
    race = "人类"
    level = 3
    base_stats = Stats(attack=2, defense=4, speed=2, attack_range=4, mana=5)
    raw_skill_text = "守*2【1回血（每回合一次；被击中的单位血+1/4；如果是属性为暗或者种族为灵体或恶魔，则效果变为血-1/4） 保护 【2洗礼（仅可对‘人类’使用；被使用的单位魔免；2轮） ￥大圣光（5轮；对方单位移动后在此单位周围11*11内则受到4的伤害；己方单位在己方回合结束时如果在此单位周围11*11内则直到下次己方回合开始前守+1） 吟唱（每回合一次；被击中单位任意种类魔力点+2）"
    raw_trait_text = "原地回魔 原地回血"

    def build_skills(self) -> list[Skill]:
        return [DefendTwiceSkill(), HealSkill(), PassiveProtectionSkill(), BaptismSkill(), GreatHolyLightSkill(), ChantSkill()]

    def build_traits(self) -> list:
        return [StationaryRecoveryTrait()]
