"""Jade battlefield objects and skills."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any
from typing import Optional
if TYPE_CHECKING:
    from wujiang.tactical.heroes.ai_policy import AIReactionScoreContext, AISkillScoreContext
from wujiang.tactical.engine.core import ActionError
from wujiang.tactical.engine.core import Battle
from wujiang.tactical.engine.core import BattleFieldEffect
from wujiang.tactical.engine.core import DamageContext
from wujiang.tactical.engine.core import HealContext
from wujiang.tactical.engine.core import HeroUnit
from wujiang.tactical.engine.core import Position
from wujiang.tactical.engine.core import QueuedAction
from wujiang.tactical.engine.core import Skill
from wujiang.tactical.engine.core import Stats
from wujiang.tactical.engine.core import Trait
from wujiang.tactical.heroes.base import AbstractHero
from wujiang.tactical.heroes.common import FlyingTrait
from wujiang.tactical.heroes.common import MachineGunSkill
from wujiang.tactical.heroes.common import MultiTargetChainShieldSkill
from wujiang.tactical.heroes.common import StatModifierStatus
from wujiang.tactical.heroes.common import WindowChargeSkill
from wujiang.tactical.heroes.common import payload_position
from wujiang.tactical.heroes.common import positions_to_dict
from wujiang.tactical.heroes.mechanics.area_helpers import area_patterns_preview
from wujiang.tactical.heroes.mechanics.area_helpers import combined_remote_rectangle_patterns
from wujiang.tactical.heroes.mechanics.area_helpers import resolve_area_damage
from wujiang.tactical.heroes.mechanics.declared_area import DeclaredAreaSkillMixin
from wujiang.tactical.heroes.mechanics.next_five_effects import remote_rectangle_patterns
from wujiang.tactical.heroes.mechanics.next_five_geometry import ALL_DIRECTIONS
from wujiang.tactical.heroes.mechanics.next_five_geometry import position_key
from wujiang.tactical.heroes.mechanics.next_five_geometry import square_around_cells
from wujiang.tactical.heroes.mechanics.status_replacement import replace_status_by_name


def maybe_queue_jade_reactive_bonus(
    battle: Battle,
    actor: HeroUnit,
    *,
    skill_code: str,
    payload: dict[str, Any],
    original_enemy_ids: set[str],
    damaged_enemy_ids: set[str],
) -> None:
    if not payload.get("enemy_reacted"):
        return
    original_enemy_ids = set(payload.get("declared_enemy_target_ids", original_enemy_ids))
    if not original_enemy_ids or original_enemy_ids.issubset(damaged_enemy_ids):
        return
    for trait in actor.traits:
        if isinstance(trait, JadeReactiveOverclockTrait):
            trait.queue_skill_bonus(battle, skill_code)
            return


class JadeReactiveOverclockTrait(Trait):
    def __init__(self) -> None:
        super().__init__(
            "受阻超频",
            "带有伤害的技能被连锁后，只要有任意原目标最终没受到伤害，则从下个己方回合开始该技能的使用次数永久 +1；每回合每个技能最多触发一次。",
        )
        self.pending_skill_bonuses: dict[str, int] = {}
        self.triggered_skill_codes: set[str] = set()

    def queue_skill_bonus(self, battle: Battle, skill_code: str) -> None:
        owner = self.owner
        if owner is None or skill_code in self.triggered_skill_codes:
            return
        self.triggered_skill_codes.add(skill_code)
        self.pending_skill_bonuses[skill_code] = self.pending_skill_bonuses.get(skill_code, 0) + 1
        skill = owner.get_skill(skill_code)
        battle.log(f"{owner.name} 的【{skill.name}】将在下个己方回合开始时永久增加 1 次使用次数。")

    def apply_bonus_to_skill(self, battle: Battle, skill_code: str, amount: int) -> None:
        owner = self.owner
        if owner is None or amount <= 0:
            return
        skill = owner.get_skill(skill_code)
        if isinstance(skill, WindowChargeSkill):
            skill.increase_window_uses(amount, apply_to_active_window=True)
        elif skill.max_uses_per_turn is not None:
            skill.max_uses_per_turn += amount
        else:
            return
        battle.log(f"{owner.name} 的【{skill.name}】使用次数永久 +{amount}。")

    def on_owner_turn_start(self, battle: Battle) -> None:
        pending = dict(self.pending_skill_bonuses)
        self.pending_skill_bonuses.clear()
        self.triggered_skill_codes.clear()
        for skill_code, amount in pending.items():
            self.apply_bonus_to_skill(battle, skill_code, amount)


class JadeMachineGunSkill(DeclaredAreaSkillMixin, MachineGunSkill):
    def chosen_line(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> list[Position]:
        return self.chosen_cells(battle, actor, payload)

    def execute(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> None:
        cells = self.chosen_line(battle, actor, payload)
        original_enemy_ids, damaged_enemy_ids = resolve_area_damage(
            battle,
            actor,
            action_name="机枪",
            cells=cells,
            attack_power=actor.stat("attack"),
            tags={"skill", "machine_gun"},
        )
        maybe_queue_jade_reactive_bonus(
            battle,
            actor,
            skill_code=self.code,
            payload=payload,
            original_enemy_ids=original_enemy_ids,
            damaged_enemy_ids=damaged_enemy_ids,
        )


class MissileAreaMixin(DeclaredAreaSkillMixin):
    def patterns(self, battle: Battle, actor: HeroUnit) -> list[list[Position]]:
        return remote_rectangle_patterns(battle, actor, 2, 2)

    def execute(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> None:
        cells = self.chosen_cells(battle, actor, payload)
        original_enemy_ids, damaged_enemy_ids = resolve_area_damage(
            battle,
            actor,
            action_name="导弹",
            cells=cells,
            attack_power=actor.stat("attack"),
            tags={"skill", "missile"},
        )
        maybe_queue_jade_reactive_bonus(
            battle,
            actor,
            skill_code=self.code,
            payload=payload,
            original_enemy_ids=original_enemy_ids,
            damaged_enemy_ids=damaged_enemy_ids,
        )

    def preview(self, battle: Battle, actor: HeroUnit) -> dict[str, Any]:
        return area_patterns_preview(battle, actor, self.patterns(battle, actor))

    def get_target_cells_for_payload(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> list[Position]:
        return self.chosen_cells(battle, actor, payload)

    def get_target_units_for_payload(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> list[HeroUnit]:
        return battle.effect_units_at_cells(self.chosen_cells(battle, actor, payload))  # type: ignore[return-value]


class MissileSkill(MissileAreaMixin, WindowChargeSkill):
    """Jade's unique three-use window."""

    def ai_priority_score(self, context: AISkillScoreContext) -> float:
        return context.r18_effect_score(self)

    def __init__(self) -> None:
        super().__init__(
            "missile",
            "导弹",
            "普通技能：每 2 轮最多 3 次，远程选择 2*2 区域；按当前攻造成伤害。",
            window_rounds=2,
            window_uses=3,
            target_mode="cell",
        )

    def on_owner_turn_start(self, battle: Battle) -> None:
        Skill.on_owner_turn_start(self, battle)

    def on_any_turn_end(self, battle: Battle, ended_player_id: int) -> None:
        Skill.on_any_turn_end(self, battle, ended_player_id)
        if self.owner is not None and battle.unit_belongs_to_current_turn(self.owner) and self.window_is_active():
            self.window_remaining_turns = max(0, self.window_remaining_turns - 1)
            if self.window_remaining_turns == 0:
                self.window_remaining_uses = 0


class CommonMissileSkill(MissileAreaMixin, Skill):
    def ai_priority_score(self, context: AISkillScoreContext) -> float:
        return context.r18_effect_score(self)

    def __init__(self) -> None:
        super().__init__(
            "missile",
            "导弹",
            "通用普通技能：0 魔，每 2 轮使用 1 次；远程选择 2*2 区域，按当前攻造成伤害。",
            cooldown_turns=2,
            target_mode="cell",
        )


class IonShieldSkill(MultiTargetChainShieldSkill):
    shield_amount = 1

    def __init__(self) -> None:
        super().__init__(
            "ion_shield",
            "离子盾",
            "被动技能：连锁速度 2，不费魔，每回合最多 2 次；效果与墙相同，可选择多个当前受影响的己方目标，各获得 1 层临时护盾，只持续到这次连锁结束。",
            mana_cost=0,
            max_uses_per_turn=2,
            target_mode="ally",
            timing="passive",
        )

    def execute(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> None:
        raise ActionError("离子盾只能通过连锁使用。")

    def can_react_to(self, battle: Battle, actor: HeroUnit, queued_action: QueuedAction) -> tuple[bool, str]:
        ok, reason = super().can_react_to(battle, actor, queued_action)
        if not ok:
            return ok, reason
        if queued_action.source_player_id == actor.player_id:
            return False, "只能对敌方动作连锁。"
        if not self.selectable_targets(battle, actor, queued_action):
            return False, "当前动作没有影响到可施放离子盾的己方目标。"
        return True, ""


class LaserSkill(DeclaredAreaSkillMixin, Skill):
    def __init__(self) -> None:
        super().__init__(
            "laser",
            "激光",
            "普通技能：冷却 3 轮，远程选择 2*10 区域；按当前攻造成伤害。",
            cooldown_turns=3,
            max_uses_per_turn=1,
            target_mode="cell",
        )
        self.burst_turn_number = -1

    def allows_additional_use_during_cooldown(self, battle: Battle, actor: HeroUnit) -> bool:
        return self.burst_turn_number == battle.turn_number

    def prepay_resources(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any] | None = None) -> None:
        super().prepay_resources(battle, actor, payload)
        self.burst_turn_number = battle.turn_number

    def patterns(self, battle: Battle, actor: HeroUnit) -> list[list[Position]]:
        return combined_remote_rectangle_patterns(battle, actor, [(2, 10), (10, 2)])

    def chosen_cells(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> list[Position]:
        return super().chosen_cells(battle, actor, payload)

    def execute(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> None:
        cells = self.chosen_cells(battle, actor, payload)
        original_enemy_ids, damaged_enemy_ids = resolve_area_damage(
            battle,
            actor,
            action_name="激光",
            cells=cells,
            attack_power=actor.stat("attack"),
            tags={"skill", "laser"},
        )
        maybe_queue_jade_reactive_bonus(
            battle,
            actor,
            skill_code=self.code,
            payload=payload,
            original_enemy_ids=original_enemy_ids,
            damaged_enemy_ids=damaged_enemy_ids,
        )

    def preview(self, battle: Battle, actor: HeroUnit) -> dict[str, Any]:
        return area_patterns_preview(battle, actor, self.patterns(battle, actor))

    def get_target_cells_for_payload(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> list[Position]:
        return self.chosen_cells(battle, actor, payload)

    def get_target_units_for_payload(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> list[HeroUnit]:
        return battle.units_at_cells(self.chosen_cells(battle, actor, payload))  # type: ignore[return-value]


class DelayedCooldownMultiTargetWallSkill(MultiTargetChainShieldSkill):
    def __init__(
        self,
        code: str,
        name: str,
        description: str,
        *,
        mana_cost: float,
        max_uses_per_turn: int,
        cooldown_turns: int,
    ) -> None:
        super().__init__(
            code,
            name,
            description,
            mana_cost=mana_cost,
            max_uses_per_turn=max_uses_per_turn,
            cooldown_turns=cooldown_turns,
            target_mode="ally",
            timing="passive",
        )
        self.cooldown_pending = False

    def prepay_resources(
        self,
        battle: Battle,
        actor: HeroUnit,
        payload: dict[str, Any] | None = None,
    ) -> None:
        self.sync_turn_scope(battle)
        actor.spend_mana(self.mana_cost_for_payload(battle, actor, payload))
        self.uses_this_turn += 1
        self.uses_this_battle += 1
        if self.cooldown_turns > 0:
            self.cooldown_pending = True

    def on_owner_turn_start(self, battle: Battle) -> None:
        super().on_owner_turn_start(battle)
        if self.cooldown_pending and self.cooldown_turns > 0 and self.cooldown_remaining <= 0:
            self.cooldown_remaining = max(self.cooldown_turns - 1, 0)
            self.cooldown_pending = False

    def to_public_dict(self, battle: Battle) -> dict[str, Any]:
        data = super().to_public_dict(battle)
        data["cooldown_pending"] = self.cooldown_pending
        return data


class QuantumShieldSkill(DelayedCooldownMultiTargetWallSkill):
    shield_amount = 1

    def __init__(self) -> None:
        super().__init__(
            "quantum_shield",
            "量子盾",
            "被动技能：连锁速度 2，不费魔，每回合最多 3 次；效果与墙相同。只要本轮使用过，下一轮整轮不能使用，再下一轮恢复可用。",
            mana_cost=0,
            max_uses_per_turn=3,
            cooldown_turns=2,
        )

    def ai_shield_reservation_penalty(self, context: AIReactionScoreContext) -> float:
        ion = next((skill for skill in context.actor.skills if skill.code == "ion_shield"), None)
        if ion is not None and ion.can_react_with_payload(
                context.battle, context.actor, context.queued_action, context.payload)[0]:
            return 30.0
        return 0.0

    def on_owner_turn_start(self, battle: Battle) -> None:
        Skill.on_owner_turn_start(self, battle)
        self.cooldown_remaining = 1 if self.cooldown_pending else 0
        self.cooldown_pending = False

    def on_any_turn_end(self, battle: Battle, ended_player_id: int) -> None:
        # The disabled round includes all opponents after Jade's own turn end.
        pass

    def execute(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> None:
        raise ActionError("量子盾只能通过连锁使用。")

    def can_react_to(self, battle: Battle, actor: HeroUnit, queued_action: QueuedAction) -> tuple[bool, str]:
        ok, reason = super().can_react_to(battle, actor, queued_action)
        if not ok:
            return ok, reason
        if queued_action.source_player_id == actor.player_id:
            return False, "只能对敌方动作连锁。"
        if not self.selectable_targets(battle, actor, queued_action):
            return False, "当前动作没有影响到可施放量子盾的己方目标。"
        return True, ""


class MechEnhancementSkill(Skill):
    def __init__(self) -> None:
        super().__init__(
            "mech_enhancement",
            "机甲强化",
            "普通技能：冷却 3 轮；自己守 +1，持续 2 轮，并回复 1/2 生命。",
            cooldown_turns=3,
            target_mode="self",
        )

    def execute(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> None:
        replace_status_by_name(
            battle,
            actor,
            StatModifierStatus(
                "机甲强化",
                defense_delta=1,
                duration=2,
                tick_scope="owner_turn_start",
                description="守 +1。",
            ),
        )
        battle.heal(HealContext(source=actor, target=actor, amount=0.5, action_name="机甲强化"))
        battle.log(f"{actor.name} 进入机甲强化状态。")

    def preview(self, battle: Battle, actor: HeroUnit) -> dict[str, Any]:
        return {
            "cells": [actor.position.to_dict()] if actor.position else [],
            "target_unit_ids": [actor.unit_id],
            "secondary_cells": [],
            "requires_target": False,
        }


class PlasmaThrusterSkill(Skill):
    def __init__(self) -> None:
        super().__init__(
            "plasma_thruster",
            "等离子喷射系统",
            "普通技能：每回合最多 1 次，直线飞行移动 5 格；若该方向会撞到边界，则可以停在边界上的最后一格，但不能停在有单位的位置。",
            max_uses_per_turn=1,
            target_mode="cell",
        )

    def selectable_destinations(self, battle: Battle, actor: HeroUnit) -> list[Position]:
        if actor.position is None or actor.cannot_move:
            return []
        destinations: list[Position] = []
        seen: set[tuple[int, int]] = set()
        for dx, dy in ALL_DIRECTIONS:
            last_in_bounds: Position | None = None
            for step in range(1, 6):
                candidate = actor.position.offset(dx * step, dy * step)
                if not battle.in_bounds(candidate):
                    break
                last_in_bounds = candidate
            if last_in_bounds is None or last_in_bounds == actor.position:
                continue
            if not battle.can_place_unit(actor, last_in_bounds, ignore=actor, mover=actor):
                continue
            key = position_key(last_in_bounds)
            if key in seen:
                continue
            seen.add(key)
            destinations.append(last_in_bounds)
        destinations.sort(key=lambda cell: (cell.y, cell.x))
        return destinations

    def can_use(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any] | None = None) -> tuple[bool, str]:
        ok, reason = super().can_use(battle, actor, payload)
        if not ok:
            return ok, reason
        if actor.cannot_move:
            return False, f"{actor.name} 当前无法移动。"
        return True, ""

    def execute(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> None:
        destination = payload_position(payload)
        if destination not in self.selectable_destinations(battle, actor):
            raise ActionError("该落点不是等离子喷射系统的合法目标。")
        battle.move_unit(
            actor,
            destination,
            via_skill=True,
            straight_only=True,
            ignore_units=True,
            max_distance=5,
            tags={self.code},
        )

    def preview(self, battle: Battle, actor: HeroUnit) -> dict[str, Any]:
        return {
            "cells": positions_to_dict(self.selectable_destinations(battle, actor)),
            "target_unit_ids": [],
            "secondary_cells": [],
            "requires_target": True,
        }


class StanceFieldEffect(BattleFieldEffect):
    def __init__(self, owner_unit_id: str, owner_player_id: int) -> None:
        super().__init__("立场", "翡翠周围 7*7 内的己方单位到翡翠下个回合开始前不受到伤害。", duration=None)
        self.owner_unit_id = owner_unit_id
        self.owner_player_id = owner_player_id
        self.armed = False

    def owner_unit(self, battle: Battle) -> HeroUnit | None:
        owner = battle.units.get(self.owner_unit_id)
        if owner is None or not isinstance(owner, HeroUnit) or not owner.alive or owner.position is None or owner.banished:
            return None
        return owner

    def affected_cells(self, battle: Battle) -> list[Position]:
        owner = self.owner_unit(battle)
        if owner is None:
            return []
        return square_around_cells(battle, battle.unit_cells(owner), radius=3)

    def board_marker(self, battle: Battle) -> str:
        return "立"

    def on_before_damage(self, battle: Battle, ctx: DamageContext) -> None:
        if not self.armed:
            return
        owner = self.owner_unit(battle)
        if owner is None or ctx.target.player_id != self.owner_player_id or ctx.target.unit_id == owner.unit_id:
            return
        protected_keys = {position_key(cell) for cell in self.affected_cells(battle)}
        if not any(position_key(cell) in protected_keys for cell in battle.unit_cells(ctx.target)):
            return
        ctx.cancelled = True
        ctx.preserve_followup_effects = True
        ctx.reason = f"{ctx.target.name} 受到立场保护，这次伤害无效。"

    def on_turn_start(self, battle: Battle, active_unit: Optional[HeroUnit]) -> None:
        if not self.armed:
            return
        if active_unit is not None and active_unit.unit_id == self.owner_unit_id:
            battle.remove_field_effect(self)

    def on_any_turn_end(self, battle: Battle, ended_player_id: int) -> None:
        owner = self.owner_unit(battle)
        if owner is None:
            battle.remove_field_effect(self)
            return
        if not self.armed and battle.unit_belongs_to_current_turn(owner):
            self.armed = True


class StanceSkill(Skill):
    def __init__(self) -> None:
        super().__init__(
            "stance",
            "立场",
            "普通技能：冷却 2 轮；从这个回合结束后的第一个对方回合开始，到自己下个回合开始前，周围 7*7 内的其他己方单位不受到伤害。",
            cooldown_turns=2,
            target_mode="self",
        )

    def execute(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> None:
        for effect in list(battle.field_effects):
            if isinstance(effect, StanceFieldEffect) and effect.owner_unit_id == actor.unit_id:
                battle.remove_field_effect(effect)
        battle.add_field_effect(StanceFieldEffect(actor.unit_id, actor.player_id))
        battle.log(f"{actor.name} 展开了立场。")

    def preview(self, battle: Battle, actor: HeroUnit) -> dict[str, Any]:
        return {
            "cells": [actor.position.to_dict()] if actor.position else [],
            "target_unit_ids": [actor.unit_id],
            "secondary_cells": [],
            "requires_target": False,
        }


class Jade(AbstractHero):
    hero_code = "jade"
    hero_name = "翡翠"
    role = "勇者"
    attribute = "钢"
    race = "机甲"
    level = 8
    base_stats = Stats(attack=4, defense=4, speed=3, attack_range=3, mana=0)
    raw_skill_text = "机枪（一回合一次） 导弹（每2轮可以使用3次） 离子盾（一回合可以使用两次，可以对队友使用） 激光（3轮一次，2*10） 量子盾（一回合可以使用3次，可以对队友使用；若本轮使用过则下一轮不能使用，再下一轮恢复） 机甲强化（3轮一次；守＋1，2轮；血＋1/2） 等离子喷射系统（1回合一次；移动直线5格） 立场（2轮一次；使用以后周围7*7内己方单位到翡翠下个回合开始前不受到伤害）"
    raw_trait_text = "飞行；在带有伤害的技能被对方的技能连锁，未能造成后，从下个己方回合开始那个技能的回合使用次数+1，每回合每个技能此效果只能触发一次"

    def build_skills(self) -> list[Skill]:
        return [
            JadeMachineGunSkill(),
            MissileSkill(),
            IonShieldSkill(),
            LaserSkill(),
            QuantumShieldSkill(),
            MechEnhancementSkill(),
            PlasmaThrusterSkill(),
            StanceSkill(),
        ]

    def build_traits(self) -> list[Trait]:
        return [FlyingTrait(), JadeReactiveOverclockTrait()]


