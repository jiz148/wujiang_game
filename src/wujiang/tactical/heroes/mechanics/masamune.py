"""Masamune battlefield objects and mounted combat."""

from __future__ import annotations

from typing import Any
from typing import Optional
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
from wujiang.tactical.engine.core import Unit
from wujiang.tactical.heroes.base import AbstractHero
from wujiang.tactical.heroes.ai_policy import AISkillScoreContext
from wujiang.tactical.heroes.common import DashMoveSkill
from wujiang.tactical.heroes.common import PassiveProtectionSkill
from wujiang.tactical.heroes.common import ShensuSkill
from wujiang.tactical.heroes.common import positions_to_dict
from wujiang.tactical.heroes.mechanics.status_replacement import replace_status_by_name


ARC_DIRECTION_CHOICES: list[tuple[str, tuple[int, int], str]] = [
    ("up", (0, -1), "上"),
    ("up_right", (1, -1), "右上"),
    ("right", (1, 0), "右"),
    ("down_right", (1, 1), "右下"),
    ("down", (0, 1), "下"),
    ("down_left", (-1, 1), "左下"),
    ("left", (-1, 0), "左"),
    ("up_left", (-1, -1), "左上"),
]


def arc_direction_cells(center: Position, code: str) -> list[Position]:
    if code == "up":
        return [Position(center.x - 1, center.y - 1), Position(center.x, center.y - 1), Position(center.x + 1, center.y - 1)]
    if code == "down":
        return [Position(center.x - 1, center.y + 1), Position(center.x, center.y + 1), Position(center.x + 1, center.y + 1)]
    if code == "left":
        return [Position(center.x - 1, center.y - 1), Position(center.x - 1, center.y), Position(center.x - 1, center.y + 1)]
    if code == "right":
        return [Position(center.x + 1, center.y - 1), Position(center.x + 1, center.y), Position(center.x + 1, center.y + 1)]
    if code == "up_right":
        return [Position(center.x, center.y - 1), Position(center.x + 1, center.y - 1), Position(center.x + 1, center.y)]
    if code == "down_right":
        return [Position(center.x + 1, center.y), Position(center.x + 1, center.y + 1), Position(center.x, center.y + 1)]
    if code == "down_left":
        return [Position(center.x - 1, center.y), Position(center.x - 1, center.y + 1), Position(center.x, center.y + 1)]
    if code == "up_left":
        return [Position(center.x - 1, center.y), Position(center.x - 1, center.y - 1), Position(center.x, center.y - 1)]
    return []


def alive_owned_motor_horse(battle: Battle, rider: HeroUnit) -> Optional["MotorHorseSummon"]:
    for unit in battle.all_units():
        if (
            isinstance(unit, MotorHorseSummon)
            and unit.mount_owner_id == rider.unit_id
            and unit.alive
            and not unit.banished
            and unit.position is not None
        ):
            return unit
    return None


class MotorHorseCooldownStatus(StatusEffect):
    def __init__(self, duration: int) -> None:
        super().__init__(
            "摩托马召回冷却",
            "坐骑被破坏后，需要再等待 1 个自己的回合才能重新召唤。",
            duration=duration,
            tick_scope="owner_turn_end",
        )


class RideableMountTrait(Trait):
    def __init__(self) -> None:
        super().__init__("可乘骑", "只有被乘骑单位会承受伤害和技能效果；乘骑者仍可替其连锁。")

    def on_owner_removed(self, battle: Battle) -> None:
        owner = self.owner
        if owner is None or not owner.mount_owner_id:
            return
        rider = battle.units.get(owner.mount_owner_id)
        if not isinstance(rider, HeroUnit) or not rider.alive:
            return
        duration = 2 if battle.unit_belongs_to_current_turn(rider) else 1
        replace_status_by_name(battle, rider, MotorHorseCooldownStatus(duration))


class FreeShensuSkill(ShensuSkill):
    def __init__(self) -> None:
        super().__init__()
        self.code = "free_shensu"
        self.description = "普通技能：每回合最多 1 次，不费魔，本回合内下一次普通移动的格数 +3。"
        self.mana_cost = 0


class MountedLeapSkill(DashMoveSkill):
    def ai_move_score(self, context: AISkillScoreContext, base_score: float,
                      destination: Position) -> float:
        battle, actor = context.battle, context.actor
        if actor.position is None:
            return base_score
        before = context.shared.masamune_attack_plan_value(context)
        with context.shared.probe_rollback(battle):
            battle.clear_mounted_state(actor)
            actor.position = destination
            followup = next((skill for skill in actor.skills if skill.code == "six_blade_style"), None)
            if followup is not None and followup.can_use(battle, actor, {})[0]:
                followup.execute(battle, actor, {})
            after = context.shared.masamune_attack_plan_value(context)
        return base_score + (min(100.0, after - before) - 40.0)

    def __init__(self) -> None:
        super().__init__(
            "mounted_leap",
            "飞跃",
            "普通技能：仅能在乘骑状态时使用；不费魔，每回合最多 1 次，自己直线飞行移动恰好 3 格；若离开坐骑占格则下马。",
            max_distance=3,
            mana_cost=0,
            max_uses_per_turn=1,
            straight_only=True,
            ignore_units=True,
            exact_distance=3,
        )

    def can_use(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any] | None = None) -> tuple[bool, str]:
        ok, reason = super().can_use(battle, actor, payload)
        if not ok:
            return ok, reason
        mount = battle.mounted_unit_for(actor)
        if mount is None:
            return False, "只有乘骑状态时才能使用飞跃。"
        if actor.cannot_move:
            return False, f"{actor.name} 当前无法移动。"
        return True, ""

    def execute(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> None:
        if battle.mounted_unit_for(actor) is None:
            raise ActionError("只有乘骑状态时才能使用飞跃。")
        super().execute(battle, actor, payload)

    def preview(self, battle: Battle, actor: HeroUnit) -> dict[str, Any]:
        if battle.mounted_unit_for(actor) is None or actor.cannot_move:
            return {"cells": [], "target_unit_ids": [], "secondary_cells": [], "requires_target": True}
        return super().preview(battle, actor)


class SixBladeStyleStatus(StatusEffect):
    def __init__(self) -> None:
        super().__init__(
            "六刀流",
            "本回合内攻 -1，普攻上限变为 6 次。",
            duration=1,
            tick_scope="owner_turn_end",
        )

    def modify_stat(self, stat_name: str, value: float) -> float:
        if stat_name == "attack":
            return value - 1
        return value

    def modify_attack_actions_per_turn(self, value: int) -> int:
        return 6


class SixBladeStyleSkill(Skill):
    def ai_self_buff_score(self, context: AISkillScoreContext) -> float:
        actor, battle = context.actor, context.battle
        before = context.shared.masamune_attack_plan_value(context)
        before_score = context.best_attack_score()
        with context.probe_rollback():
            self.execute(battle, actor, {})
            after = context.shared.masamune_attack_plan_value(context)
            after_score = context.best_attack_score()
        return max(before_score, after_score) + 25.0 if after > before + 1.0 else -1000.0

    def __init__(self) -> None:
        super().__init__(
            "six_blade_style",
            "六刀流",
            "普通技能：每回合最多 1 次；仅能在非乘骑状态且本回合未攻击时使用；本回合内攻 -1，普攻上限变为 6 次。",
            max_uses_per_turn=1,
        )

    def can_use(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any] | None = None) -> tuple[bool, str]:
        ok, reason = super().can_use(battle, actor, payload)
        if not ok:
            return ok, reason
        if battle.mounted_unit_for(actor) is not None:
            return False, "乘骑状态时不能使用六刀流。"
        if actor.attacks_used > 0 or "attack" in actor.actions_taken_this_turn:
            return False, "必须在本回合尚未普攻时使用六刀流。"
        return True, ""

    def execute(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> None:
        replace_status_by_name(battle, actor, SixBladeStyleStatus())
        battle.log(f"{actor.name} 进入了六刀流状态。")


class HealMountSkill(Skill):
    def ai_heal_score(self, context: AISkillScoreContext, healed: Unit) -> float:
        mount = context.battle.mounted_unit_for(context.actor)
        return context.shared.probed_heal_score(context, self, mount or context.actor)

    def __init__(self) -> None:
        super().__init__(
            "heal_mount",
            "治愈良驹",
            "普通技能：每回合最多 1 次；仅能在乘骑状态时使用；自己当前乘骑的摩托马回复 1/2 生命。",
            max_uses_per_turn=1,
        )

    def can_use(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any] | None = None) -> tuple[bool, str]:
        ok, reason = super().can_use(battle, actor, payload)
        if not ok:
            return ok, reason
        mount = battle.mounted_unit_for(actor)
        if mount is None or mount.hero_code != "motor_horse":
            return False, "只有乘骑状态时才能治疗良驹。"
        return True, ""

    def execute(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> None:
        mount = battle.mounted_unit_for(actor)
        if mount is None:
            raise ActionError("当前没有可治疗的摩托马。")
        battle.heal(HealContext(source=actor, target=mount, amount=0.5, action_name="治愈良驹"))


class TripleStrikeAttackTrait(Trait):
    def __init__(self) -> None:
        super().__init__("三连重斩", "可以选择一次普攻占用 3 次普攻次数；那次普攻伤害 +3 并半破魔。")

    def basic_attack_action_entries(self, battle: Battle, actor: HeroUnit) -> list[dict[str, Any]]:
        return [
            {
                "code": "attack_triple",
                "name": "三连重斩",
                "description": "普攻变体：占用 3 次普攻次数；这次普攻伤害 +3 并半破魔。",
                "attack_payload": {"attack_variant": "triple"},
                "is_attack_variant": True,
            }
        ]

    def basic_attack_payload_metadata(self, battle: Battle, actor: Unit, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        if str((payload or {}).get("attack_variant") or "default") != "triple":
            return {}
        return {
            "attack_name": "三连重斩",
            "attack_cost": 3,
            "attack_bonus": 3,
            "half_ignore_shield": True,
            "attack_note": "这次普攻伤害 +3，并且半破魔。",
        }


class ArcAttackTrait(Trait):
    def __init__(self) -> None:
        super().__init__("弧形攻击", "普攻前先声明八码方向；正交方向打外侧整排 3 格，斜向打对应角上的 3 格弧面。")

    def basic_attack_area_affects_allies(
        self, battle: Battle, actor: Unit, payload: dict[str, Any] | None = None,
    ) -> bool:
        return False

    def selected_direction_code(self, payload: dict[str, Any] | None = None) -> str:
        return str((payload or {}).get("choice_code") or "").strip()

    def direction_cells(self, battle: Battle, actor: Unit, code: str) -> list[Position]:
        if actor.position is None:
            return []
        return [cell for cell in arc_direction_cells(actor.position, code) if battle.in_bounds(cell)]

    def valid_target_cells(self, battle: Battle, actor: Unit, target: Unit, code: str) -> list[Position]:
        allowed = {(cell.x, cell.y) for cell in self.direction_cells(battle, actor, code)}
        return [cell for cell in battle.unit_cells(target) if (cell.x, cell.y) in allowed]

    def basic_attack_payload_metadata(
        self,
        battle: Battle,
        actor: Unit,
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        code = self.selected_direction_code(payload)
        if not code:
            return {}
        return {"attack_cells": positions_to_dict(self.direction_cells(battle, actor, code)), "attack_tags": ["arc_attack"]}

    def basic_attack_area_cells(
        self,
        battle: Battle,
        actor: Unit,
        payload: dict[str, Any] | None = None,
    ) -> Optional[list[Position]]:
        if payload is None:
            return None
        code = self.selected_direction_code(payload)
        if not code:
            return None
        if code not in [choice_code for choice_code, _, _ in ARC_DIRECTION_CHOICES]:
            raise ActionError("所选普攻方向无效。")
        cells = self.direction_cells(battle, actor, code)
        if not battle.effect_units_at_cells(cells):
            raise ActionError("攻击区域内没有有效目标。")
        return cells

    def basic_attack_preview(self, battle: Battle, actor: Unit, payload: dict[str, Any] | None = None) -> Optional[dict[str, Any]]:
        preview_cells: list[Position] = []
        preview_choices: list[dict[str, Any]] = []
        target_ids: list[str] = []
        seen_target_ids: set[str] = set()
        seen_cells: set[tuple[int, int]] = set()
        for code, _, label in ARC_DIRECTION_CHOICES:
            direction_patterns: list[list[dict[str, int]]] = []
            for enemy in battle.all_units():
                valid_cells = self.valid_target_cells(battle, actor, enemy, code)
                if not valid_cells:
                    continue
                if not battle.attack_target_allowed(actor, enemy, payload={"choice_code": code})[0]:
                    continue
                if enemy.unit_id not in seen_target_ids:
                    seen_target_ids.add(enemy.unit_id)
                    target_ids.append(enemy.unit_id)
                for cell in valid_cells:
                    if (cell.x, cell.y) not in seen_cells:
                        seen_cells.add((cell.x, cell.y))
                        preview_cells.append(cell)
                    direction_patterns.append([cell.to_dict()])
            preview_choices.append({"code": code, "label": label, "patterns": direction_patterns})
        return {
            "cells": positions_to_dict(preview_cells),
            "target_unit_ids": target_ids,
            "secondary_cells": [],
            "requires_target": True,
            "selection": {
                "mode": "choice_pattern",
                "choices": preview_choices,
                "ordered": False,
            },
        }

    def can_attack_target_with_payload(
        self,
        battle: Battle,
        actor: Unit,
        target: Unit,
        payload: dict[str, Any] | None = None,
    ) -> tuple[bool, str]:
        codes = [code for code, _, _ in ARC_DIRECTION_CHOICES]
        if payload is None:
            if any(self.valid_target_cells(battle, actor, target, code) for code in codes):
                return True, ""
            return False, "目标不在弧形普攻范围内。"
        code = self.selected_direction_code(payload)
        if not code:
            return False, "弧形攻击普攻前需要先声明方向。"
        if code not in codes:
            return False, "所选普攻方向无效。"
        valid_cells = self.valid_target_cells(battle, actor, target, code)
        if not valid_cells:
            return False, "目标不在该方向的弧形普攻范围内。"
        if payload.get("x") is not None and payload.get("y") is not None:
            clicked = Position(int(payload["x"]), int(payload["y"]))
            target_cells = battle.unit_cells(target)
            if clicked in target_cells and clicked not in valid_cells:
                return False, "所点目标格不在该方向的弧形普攻范围内。"
        return True, ""


class MasamuneArcAttackTrait(ArcAttackTrait):
    """Masamune selects one cell of the arc; other arc heroes retain area attacks."""

    def basic_attack_payload_metadata(self, battle: Battle, actor: Unit, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        return {"attack_cells": [], "attack_tags": ["arc_attack"]}

    def basic_attack_area_cells(self, battle: Battle, actor: Unit, payload: dict[str, Any] | None = None) -> Optional[list[Position]]:
        return None


class MountedFreeLeapTrait(Trait):
    def __init__(self) -> None:
        super().__init__("乘骑飞跃", "乘骑状态时，每回合可以不用魔使用 1 次飞跃。")


class UnmountedCombatTrait(Trait):
    def __init__(self) -> None:
        super().__init__("下马战技", "下马后可格挡、反击，并且普攻造成伤害后回复 1/4 生命。")

    def bind(self, owner: HeroUnit) -> "UnmountedCombatTrait":
        super().bind(owner)
        owner.has_block_counter = True
        return self

    def allows_block_counter(self, battle: Battle, actor: Unit) -> bool:
        return battle.mounted_unit_for(actor) is None

    def on_after_damage(self, battle: Battle, ctx: DamageContext) -> None:
        owner = self.owner
        if owner is None or ctx.source is None or ctx.source.unit_id != owner.unit_id:
            return
        if ctx.is_skill or "attack" not in ctx.tags or ctx.cancelled or ctx.actual_damage <= 0:
            return
        if battle.mounted_unit_for(owner) is not None:
            return
        battle.heal(HealContext(source=owner, target=owner, amount=0.25, action_name="攻击吸血"))


class MasamuneMountedStartTrait(Trait):
    def __init__(self) -> None:
        super().__init__("骑士开场坐骑", "出场时已经召唤出自己的摩托马，并且已经处于乘骑状态。")

    def on_enter_battle(self, battle: Battle) -> None:
        owner = self.owner
        if not isinstance(owner, HeroUnit) or owner.position is None:
            return
        if alive_owned_motor_horse(battle, owner) is not None:
            return
        mount = MotorHorseSummon(owner.player_id)
        mount.summoner_id = owner.unit_id
        mount.mount_owner_id = owner.unit_id
        mount.is_mount = True
        mount.can_act_on_entry_turn = True
        mount.turn_ready = True
        battle.add_unit(mount, owner.position)
        battle.set_mounted_state(owner, mount)


class MotorHorseSummon(AbstractHero):
    hero_code = "motor_horse"
    hero_name = "摩托马"
    role = "坐骑"
    attribute = "土"
    race = "召唤物"
    level = 1
    base_stats = Stats(attack=0, defense=5, speed=5, attack_range=1, mana=0)
    footprint_width = 1
    footprint_height = 2
    stat_minimums = {"attack": 0.0}
    raw_skill_text = "神速（每个己方回合可以免费使用一次）"
    raw_trait_text = "可乘骑"

    def __init__(self, player_id: int) -> None:
        super().__init__(player_id, is_summon=True)

    def build_skills(self) -> list[Skill]:
        return [FreeShensuSkill()]

    def build_traits(self) -> list[Trait]:
        return [RideableMountTrait()]


class MotorHorseSkill(Skill):
    def __init__(self) -> None:
        super().__init__(
            "motor_horse",
            "摩托马",
            "普通技能：召唤自己的坐骑摩托马；若已存在自己的摩托马，或仍在召回冷却中，则不能使用。",
            target_mode="self",
        )

    def can_use(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any] | None = None) -> tuple[bool, str]:
        ok, reason = super().can_use(battle, actor, payload)
        if not ok:
            return ok, reason
        if alive_owned_motor_horse(battle, actor) is not None:
            return False, "场上已经有自己的摩托马。"
        if actor.has_status("摩托马召回冷却"):
            return False, "摩托马仍在召回冷却中。"
        if actor.position is None:
            return False, "当前不在战场上。"
        test_mount = MotorHorseSummon(actor.player_id)
        test_mount.mount_owner_id = actor.unit_id
        test_mount.is_mount = True
        if not battle.can_place_unit(test_mount, actor.position, ignore=test_mount, mover=test_mount):
            return False, "当前位置无法召唤摩托马。"
        return True, ""

    def execute(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> None:
        if actor.position is None:
            raise ActionError("当前不在战场上。")
        mount = MotorHorseSummon(actor.player_id)
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


class Masamune(AbstractHero):
    hero_code = "masamune"
    hero_name = "天位骑士。政宗"
    role = "骑士"
    attribute = "土"
    race = "人类"
    level = 4
    base_stats = Stats(attack=4, defense=3, speed=3, attack_range=1, mana=3)
    entry_footprint_width = 1
    entry_footprint_height = 2
    raw_skill_text = "摩托马（攻0守5速5；每个己方回合可以使用一次神速） 保护 六刀流（一回合一次；仅能在非乘骑状态并且当回合未攻击时使用；本回合内攻-1，攻击六次） 治愈良驹（一回合一次；仅能在乘骑时使用此；单位召唤的“摩托马”血+1/2）"
    raw_trait_text = "弧形攻击；乘骑状态时每回合可以不用魔使用一次飞跃；可选择一次攻击占用3次普攻次数，那次攻击伤害+3并且半破魔；此单位在下马后可以格挡，反击，并且攻击吸血"

    def build_skills(self) -> list[Skill]:
        return [
            MotorHorseSkill(),
            PassiveProtectionSkill(),
            SixBladeStyleSkill(),
            HealMountSkill(),
            MountedLeapSkill(),
        ]

    def build_traits(self) -> list[Trait]:
        return [
            MasamuneArcAttackTrait(),
            MountedFreeLeapTrait(),
            TripleStrikeAttackTrait(),
            UnmountedCombatTrait(),
            MasamuneMountedStartTrait(),
        ]


