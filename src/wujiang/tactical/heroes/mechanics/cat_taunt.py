"""R142 Cat taunt, required attack, and retaliation lifecycle."""

from __future__ import annotations

from typing import Any

from wujiang.tactical.engine.core import (
    ActionError, Battle, DamageContext, HeroUnit, Position, Skill,
    StatusEffect, TargetContext, Trait,
)
from wujiang.tactical.heroes.ai_policy import AISkillScoreContext
from wujiang.tactical.heroes.common import FlagStatus, positions_to_dict
from wujiang.tactical.heroes.mechanics.next_five_geometry import (
    position_key,
    square_around_cells,
)

class RequiredAttackStatus(StatusEffect):
    """One-turn taunt contract shared by heroes that require a specific basic-attack target."""

    def __init__(self, name: str, description: str, required_target_id: str) -> None:
        super().__init__(name, description, duration=1, tick_scope="owner_turn_end")
        self.required_attack_target_id = required_target_id
        self.requirement_active = False
        self.requirement_satisfied = False

    def _required_target(self, battle: Battle) -> HeroUnit | None:
        target = battle.units.get(self.required_attack_target_id)
        if target is None or not target.alive or target.position is None or target.banished:
            return None
        return target  # type: ignore[return-value]

    def forces_attack_target(self, battle: Battle) -> bool:
        return self.requirement_active and not self.requirement_satisfied and self._required_target(battle) is not None

    def on_owner_turn_start(self, battle: Battle) -> None:
        self.requirement_active = True
        if self._required_target(battle) is None:
            self.requirement_satisfied = True

    def can_attack_target(self, battle: Battle, actor: HeroUnit, target: HeroUnit) -> tuple[bool, str]:
        if not self.forces_attack_target(battle):
            return True, ""
        if target.unit_id != self.required_attack_target_id:
            required = self._required_target(battle)
            return False, f"{actor.name} 必须先攻击 {required.name if required is not None else '嘲讽来源'}。"
        return True, ""

    def blocks_skill_use(self, battle: Battle, actor: HeroUnit, skill: Skill) -> tuple[bool, str]:
        if not self.forces_attack_target(battle):
            return False, ""
        if actor.player_id != battle.active_player or not battle.unit_belongs_to_current_turn(actor):
            return False, ""
        return True, f"{actor.name} 必须先攻击嘲讽来源，本回合目前只能移动。"

    def on_owner_action_declared(self, battle: Battle, action_type: str, payload: dict[str, Any]) -> None:
        owner = self.owner
        if owner is None or action_type != "attack" or not self.forces_attack_target(battle):
            return
        target_ids = [str(payload.get("target_unit_id") or "")]
        attack_cells = {position_key(cell) for cell in battle.payload_positions(payload, "attack_cells")}
        required = self._required_target(battle)
        if required is not None and any(position_key(cell) in attack_cells for cell in battle.unit_cells(required)):
            target_ids.append(required.unit_id)
        if self.required_attack_target_id not in target_ids:
            return
        self.requirement_satisfied = True
        battle.log(f"{owner.name} 已按【{self.name}】要求攻击 {required.name if required is not None else '嘲讽来源'}。")


def replace_required_attack_status(battle: Battle, target: HeroUnit, status: RequiredAttackStatus) -> None:
    """Keep one executable forced-target contract; the newest taunt replaces older targets."""
    existing_statuses = [existing for existing in target.statuses if isinstance(existing, RequiredAttackStatus)]
    target.add_status(status, source=battle.units.get(status.required_attack_target_id))
    if status not in target.statuses:
        return
    for existing in existing_statuses:
        target.remove_status(existing, battle)


class CatTauntStatus(RequiredAttackStatus):
    def __init__(self, cat_unit_id: str) -> None:
        super().__init__(
            "嘲讽之吼",
            "下个自己的回合中，攻击猫叔前只能移动。",
            cat_unit_id,
        )


class CatTauntRoarSkill(Skill):
    def ai_taunt_control_score(self, battle, actor, payload, targets, services, profile) -> float:
        enemies = [unit for unit in targets if unit.player_id != actor.player_id]
        allies = [unit for unit in targets if unit.player_id == actor.player_id]
        if not enemies:
            return -20.0
        score = 0.0
        services.skill_effect_cells(battle, actor, self, payload)
        for unit in enemies:
            if unit.magic_immunity or unit.direct_effects_blocked() or unit.skill_non_damage_effects_blocked():
                continue
            if unit.total_shields() > 0:
                score += 16.0
            if unit.dodge_charges > 0:
                score += 10.0
                continue
            if unit.is_clone:
                score += services.hostile_unit_value(unit) * 0.45 + 70.0
                continue
            score += services.hostile_unit_value(unit) * 0.25
            already_taunted = any(
                str(getattr(status, "required_attack_target_id", "") or "") == actor.unit_id
                and not bool(getattr(status, "requirement_satisfied", False))
                for status in unit.statuses)
            if already_taunted:
                score -= 18.0
                continue
            active_skills = sum(1 for skill in unit.skills if getattr(skill, "timing", None) in {"active", "instant"})
            incoming = services.estimate_damage(battle, actor, unit.stat("attack"))
            score += 42.0 + unit.attack_actions_per_turn() * 16.0 + active_skills * 9.0
            score += min(1.0, unit.current_mana) * 18.0 - incoming * 55.0
        for unit in allies:
            if unit.magic_immunity or unit.direct_effects_blocked() or unit.skill_non_damage_effects_blocked():
                continue
            if unit.total_shields() > 0:
                score -= 16.0
            if unit.dodge_charges > 0:
                score -= 10.0
                continue
            if unit.is_clone:
                score -= services.friendly_fire_penalty(unit)
                continue
            already_taunted = any(
                str(getattr(status, "required_attack_target_id", "") or "") == actor.unit_id
                and not bool(getattr(status, "requirement_satisfied", False))
                for status in unit.statuses)
            if not already_taunted:
                active_skills = sum(1 for ally_skill in unit.skills
                                    if getattr(ally_skill, "timing", None) in {"active", "instant"})
                incoming = services.estimate_damage(battle, actor, unit.stat("attack"))
                score -= 42.0 + unit.attack_actions_per_turn() * 16.0 + active_skills * 9.0
                score -= incoming * 55.0
        return score + profile.aggressive_bonus

    def ai_hostile_target_impact(self, battle, actor, payload, target):
        if target.magic_immunity or target.direct_effects_blocked() or target.skill_non_damage_effects_blocked():
            return False
        if target.total_shields() > 0 or target.dodge_charges > 0 or target.is_clone:
            return True
        return not any(
            str(getattr(status, "required_attack_target_id", "") or "") == actor.unit_id
            and not bool(getattr(status, "requirement_satisfied", False))
            for status in target.statuses
        )

    def ai_priority_score(self, context: AISkillScoreContext) -> float:
        return context.shared.taunt_control_score(context)

    excludes_caster_from_effect = True

    def __init__(self) -> None:
        super().__init__(
            "cat_taunt_roar",
            "嘲讽之吼",
            "普通技能：每回合一次；周围7*7；没有伤害；破魔；被击中单位下个自己的回合在攻击猫叔前只能移动。",
            max_uses_per_turn=1,
            target_mode="self",
        )

    def affected_cells(self, battle: Battle, actor: HeroUnit) -> list[Position]:
        return square_around_cells(battle, battle.unit_cells(actor), radius=3)

    def affected_units(self, battle: Battle, actor: HeroUnit) -> list[HeroUnit]:
        return battle.effect_units_at_cells(self.affected_cells(battle, actor), ignore=actor)  # type: ignore[return-value]

    def chosen_cells(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> list[Position]:
        return battle.payload_positions(payload, "declared_area_cells") or self.affected_cells(battle, actor)

    def queued_payload_metadata(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> dict[str, Any]:
        return {"declared_area_cells": positions_to_dict(self.affected_cells(battle, actor))}

    def execute(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> None:
        cells = self.chosen_cells(battle, actor, payload)
        for target in list(battle.effect_units_at_cells(cells, ignore=actor)):
            ctx = battle.validate_target(
                actor, target, action_name=self.name, is_skill=True,
                is_hostile=target.player_id != actor.player_id,
                ignore_shield=True, ignore_targeting_restrictions=True,
                resolve_defenses=False, tags={"skill", "area", "control", "pierce", self.code},
            )
            target = ctx.target
            if ctx.cancelled:
                if ctx.reason:
                    battle.log_public_event(ctx.reason, source=actor, target=target)
                continue
            if target.magic_immunity:
                battle.emit_defense_visual_event(source=actor, target=target, action_name=self.name,
                                                 defense_reason="magic_immunity")
                battle.log_public_event(f"{target.name} 处于魔免状态。", source=actor, target=target)
                continue
            if target.direct_effects_blocked() or target.skill_non_damage_effects_blocked():
                continue
            if target.total_shields() > 0:
                target.consume_one_shield()
                battle.record_shield_break_summary(actor, target, self.name)
                battle.emit_defense_visual_event(source=actor, target=target, action_name=self.name,
                                                 defense_reason="shield_break")
                battle.log_public_event(f"{target.name} 的 1 层护盾被【{self.name}】贯穿并打碎。",
                                        source=actor, target=target)
            if target.dodge_charges > 0 and not ctx.cannot_evade:
                target.dodge_charges -= 1
                battle.emit_defense_visual_event(source=actor, target=target, action_name=self.name,
                                                 defense_reason="dodge")
                battle.log_public_event(f"{target.name} 闪避了【{self.name}】。", source=actor, target=target)
                continue
            if battle.destroy_clone_for_skill_effect(target, source=actor, action_name=self.name):
                continue
            status = CatTauntStatus(actor.unit_id)
            status.is_skill_effect = True
            replace_required_attack_status(battle, target, status)
            if status in target.statuses:
                battle.record_skill_effect(target)
                battle.log_public_event(f"{target.name} 被 {actor.name} 的【嘲讽之吼】锁定。",
                                        source=actor, target=target)

    def preview(self, battle: Battle, actor: HeroUnit) -> dict[str, Any]:
        targets = self.affected_units(battle, actor)
        return {
            "cells": positions_to_dict(self.affected_cells(battle, actor)),
            "target_unit_ids": [unit.unit_id for unit in targets],
            "secondary_cells": positions_to_dict(battle.unit_cells(actor)),
            "requires_target": False,
        }

    def get_target_units_for_payload(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> list[HeroUnit]:
        return battle.effect_units_at_cells(self.chosen_cells(battle, actor, payload), ignore=actor)  # type: ignore[return-value]

    def get_target_cells_for_payload(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> list[Position]:
        return self.chosen_cells(battle, actor, payload)

    def ignores_shield_for_payload(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> bool:
        return True


class CatRetaliationLockStatus(StatusEffect):
    locked_flags = ("cannot_move",)

    def __init__(self) -> None:
        super().__init__("猫叔反制", "当前回合不能移动或使用主动技能。", duration=1, tick_scope="any_turn_end")

    def bind(self, owner: HeroUnit) -> "CatRetaliationLockStatus":
        super().bind(owner)
        owner.cannot_move = True
        return self

    def blocks_skill_use(self, battle: Battle, actor: HeroUnit, skill: Skill) -> tuple[bool, str]:
        if skill.timing == "active":
            return True, f"{actor.name} 受到猫叔反制，当前回合不能使用主动技能。"
        return False, ""

    def on_removed(self, battle: Battle) -> None:
        owner = self.owner
        if owner is None:
            return
        owner.cannot_move = any(
            "cannot_move" in getattr(status, "locked_flags", ())
            or (isinstance(status, FlagStatus) and status.flag_name == "cannot_move" and status.value)
            for status in owner.statuses
        )


def _straight_destination_around(battle: Battle, mover: HeroUnit, anchor: HeroUnit) -> Position | None:
    if mover.position is None or anchor.position is None:
        return None
    start = mover.position
    reachable = battle.reachable_positions(
        mover,
        max_distance=battle.width + battle.height,
        straight_only=True,
    )
    ordered = sorted(
        [start, *reachable],
        key=lambda cell: (
            min((cell.distance_to(anchor_cell) for anchor_cell in battle.unit_cells(anchor)), default=10**9),
            start.distance_to(cell),
            cell.y,
            cell.x,
        ),
    )
    destination = ordered[0] if ordered else start
    if destination == start:
        return start
    return destination


class CatRetaliationTrait(Trait):
    def __init__(self) -> None:
        super().__init__("猫叔反制", "任何单位攻击或技能影响猫叔时魔-1，并沿直线尽量被拉近，本回合不能移动或使用主动技能。")
        self.last_trigger_key: tuple[Any, ...] | None = None

    def _trigger(
        self,
        battle: Battle,
        *,
        actor: HeroUnit,
        target: HeroUnit,
        action_name: str,
        is_skill: bool,
        tags: set[str],
        from_field_effect: bool,
    ) -> None:
        owner = self.owner
        if (
            owner is None
            or target.unit_id != owner.unit_id
            or from_field_effect
            or actor.unit_id == owner.unit_id
            or (not is_skill and "attack" not in tags)
        ):
            return
        resolution_token = battle.current_action_resolution_token
        key = (
            ("queued_action", resolution_token)
            if resolution_token is not None
            else ("direct_effect", battle.turn_number, actor.unit_id, action_name, len(actor.actions_taken_this_turn))
        )
        if key == self.last_trigger_key:
            return
        self.last_trigger_key = key
        lost = actor.spend_mana(min(1.0, actor.current_mana))
        start = actor.position
        destination = _straight_destination_around(battle, actor, owner)
        moved = False
        if destination is not None and start is not None and destination != start:
            try:
                battle.move_unit(
                    actor,
                    destination,
                    via_skill=True,
                    forced=True,
                    straight_only=True,
                    max_distance=battle.width + battle.height,
                    tags={"cat_retaliation"},
                )
                moved = True
            except ActionError:
                destination = None
        existing = actor.get_status("猫叔反制")
        if existing is not None:
            actor.remove_status(existing, battle)
        actor.add_status(CatRetaliationLockStatus())
        if moved:
            movement_text = "并被沿直线尽量拉近猫叔"
        elif destination == start and start is not None:
            movement_text = "且已经处于可达的最近直线位置"
        else:
            movement_text = "，但没有合法直线路径"
        battle.log(f"{actor.name} 因攻击或技能猫叔失去 {lost:g} 魔{movement_text}，本回合不能移动或使用主动技能。")
        battle.record_rule_trigger_summary("cat_retaliation", actor=actor, target=owner)

    def on_targeted(self, battle: Battle, ctx: TargetContext) -> None:
        self._trigger(
            battle,
            actor=ctx.actor,
            target=ctx.target,
            action_name=ctx.action_name,
            is_skill=ctx.is_skill,
            tags=ctx.tags,
            from_field_effect=ctx.from_field_effect,
        )

    def on_before_damage(self, battle: Battle, ctx: DamageContext) -> None:
        if ctx.source is None:
            return
        self._trigger(
            battle,
            actor=ctx.source,
            target=ctx.target,
            action_name=ctx.action_name,
            is_skill=ctx.is_skill,
            tags=ctx.tags,
            from_field_effect=ctx.from_field_effect,
        )


