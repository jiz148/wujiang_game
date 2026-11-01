"""Dragon Rider battlefield objects and mount."""

from __future__ import annotations

from wujiang.tactical.heroes.ai_candidate_plan import RANKED_CANDIDATES

from typing import Any
from typing import Optional
from wujiang.tactical.engine.core import ActionError
from wujiang.tactical.engine.core import Battle
from wujiang.tactical.engine.core import BattleFieldEffect
from wujiang.tactical.engine.core import DamageContext
from wujiang.tactical.engine.core import HeroUnit
from wujiang.tactical.engine.core import Position
from wujiang.tactical.engine.core import Skill
from wujiang.tactical.engine.core import Stats
from wujiang.tactical.engine.core import StatusEffect
from wujiang.tactical.engine.core import Trait
from wujiang.tactical.engine.core import Unit
from wujiang.tactical.heroes.base import AbstractHero
from wujiang.tactical.heroes.ai_policy import AISkillScoreContext
from wujiang.tactical.heroes.mechanics.simulated_effect_ai import SimulatedEffectDeltaScoring
from wujiang.tactical.heroes.common import FlagStatus
from wujiang.tactical.heroes.common import FlyingTrait
from wujiang.tactical.heroes.common import PassiveProtectionSkill
from wujiang.tactical.heroes.common import dedupe_positions
from wujiang.tactical.heroes.common import match_payload_pattern
from wujiang.tactical.heroes.common import pattern_selection_preview
from wujiang.tactical.heroes.common import pattern_signature
from wujiang.tactical.heroes.common import positions_to_dict
from wujiang.tactical.heroes.mechanics.area_helpers import area_patterns_preview
from wujiang.tactical.heroes.mechanics.area_helpers import combined_remote_rectangle_patterns
from wujiang.tactical.heroes.mechanics.declared_area import DeclaredAreaSkillMixin
from wujiang.tactical.heroes.mechanics.dragon_breath import DragonBreathSkill
from wujiang.tactical.heroes.mechanics.li import ChainPullSkill
from wujiang.tactical.heroes.mechanics.masamune import RideableMountTrait
from wujiang.tactical.heroes.mechanics.next_five_effects import damage_followup_effect_applies
from wujiang.tactical.heroes.mechanics.next_five_effects import remote_rectangle_patterns
from wujiang.tactical.heroes.mechanics.next_five_geometry import position_key
from wujiang.tactical.heroes.mechanics.status_replacement import replace_status_by_name


def alive_owned_dragon_mount(battle: Battle, rider: HeroUnit) -> Optional["DragonMountSummon"]:
    for unit in battle.all_units():
        if (
            isinstance(unit, DragonMountSummon)
            and unit.mount_owner_id == rider.unit_id
            and unit.alive
            and not unit.banished
            and unit.position is not None
        ):
            return unit
    return None


def replace_source_status(battle: Battle, target: HeroUnit, status: StatusEffect, source_unit_id: str) -> None:
    for existing in list(target.statuses):
        if type(existing) is type(status) and getattr(existing, "source_unit_id", None) == source_unit_id:
            target.remove_status(existing, battle)
    target.add_status(status)


def apply_dragon_piercing_status(
    battle: Battle,
    source: HeroUnit,
    target: HeroUnit,
    *,
    action_name: str,
    status: StatusEffect,
    source_unit_id: str,
    tags: set[str],
) -> bool:
    if not target.alive or target.position is None or target.banished:
        return False
    is_hostile = target.player_id != source.player_id
    target_ctx = battle.validate_target(
        source,
        target,
        action_name=action_name,
        is_skill="dragon_attack_debuff" not in tags,
        is_hostile=is_hostile,
        ignore_shield=True,
        cannot_evade=True,
        ignore_targeting_restrictions=True,
        tags=tags,
    )
    if target_ctx.cancelled:
        if target_ctx.reason:
            battle.log_public_event(target_ctx.reason, source=source, target=target)
        return False
    if is_hostile and target.total_shields() > 0:
        target.consume_one_shield()
        battle.log_public_event(
            f"{target.name} 的 1 层护盾被【{action_name}】贯穿并打碎。",
            source=source,
            target=target,
        )
    replace_source_status(battle, target, status, source_unit_id)
    return True


class DragonMountCooldownStatus(StatusEffect):
    def __init__(self, duration: int) -> None:
        super().__init__(
            "召龙冷却",
            "龙被破坏后，需要再等待 1 个自己的回合才能重新召唤。",
            duration=duration,
            tick_scope="owner_turn_end",
        )


class DragonAttackDebuffStatus(StatusEffect):
    def __init__(self, source_unit_id: str) -> None:
        super().__init__("龙击破魔", "速 -1，守 -1，直到龙骑的下个回合开始前。")
        self.source_unit_id = source_unit_id

    def modify_stat(self, stat_name: str, value: float) -> float:
        if stat_name in {"speed", "defense"}:
            return value - 1
        return value

    def to_public_dict(self, battle: Battle) -> dict[str, Any]:
        data = super().to_public_dict(battle)
        data["source_unit_id"] = self.source_unit_id
        return data


class DragonSlashSlowStatus(StatusEffect):
    def __init__(self, source_unit_id: str) -> None:
        super().__init__("龙斩链条", "速 -2，直到龙骑的下个回合开始前。")
        self.source_unit_id = source_unit_id

    def modify_stat(self, stat_name: str, value: float) -> float:
        if stat_name == "speed":
            return value - 2
        return value

    def to_public_dict(self, battle: Battle) -> dict[str, Any]:
        data = super().to_public_dict(battle)
        data["source_unit_id"] = self.source_unit_id
        return data


class DragonSmokeRestrictionStatus(FlagStatus):
    def __init__(self, field_id: str, source_unit_id: str) -> None:
        super().__init__(
            "喷烟",
            "cannot_attack",
            description="处于喷烟区域内时不能攻击或使用主动技能。",
        )
        self.field_id = field_id
        self.source_unit_id = source_unit_id

    def blocks_skill_use(self, battle: Battle, actor: HeroUnit, skill: Skill) -> tuple[bool, str]:
        if skill.timing == "active":
            return True, f"{actor.name} 处于喷烟中，不能使用主动技能。"
        return False, ""

    def to_public_dict(self, battle: Battle) -> dict[str, Any]:
        data = super().to_public_dict(battle)
        data["field_id"] = self.field_id
        data["source_unit_id"] = self.source_unit_id
        return data


class DragonSmokeFieldEffect(BattleFieldEffect):
    def __init__(self, source_unit_id: str, cells: list[Position]) -> None:
        super().__init__("喷烟", "区域内所有单位不能攻击或使用主动技能；持续到龙骑下个回合开始前。")
        self.source_unit_id = source_unit_id
        self.cells = dedupe_positions(cells)

    def affected_cells(self, battle: Battle) -> list[Position]:
        return list(self.cells)

    def board_marker(self, battle: Battle) -> str:
        return "烟"

    def unit_in_area(self, battle: Battle, unit: HeroUnit) -> bool:
        affected = {position_key(cell) for cell in self.cells}
        return any(position_key(cell) in affected for cell in battle.unit_cells(battle.effect_recipient(unit)))

    def sync_restrictions(self, battle: Battle) -> None:
        for unit in battle.all_units():
            existing = [
                status
                for status in unit.statuses
                if isinstance(status, DragonSmokeRestrictionStatus)
                and status.field_id == self.component_id
            ]
            should_apply = (
                unit.alive
                and unit.position is not None
                and not unit.banished
                and self.unit_in_area(battle, unit)  # type: ignore[arg-type]
            )
            if should_apply and not existing:
                unit.add_status(DragonSmokeRestrictionStatus(self.component_id, self.source_unit_id))
            if not should_apply:
                for status in existing:
                    unit.remove_status(status, battle)

    def cleanup_restrictions(self, battle: Battle) -> None:
        for unit in battle.all_units():
            for status in list(unit.statuses):
                if isinstance(status, DragonSmokeRestrictionStatus) and status.field_id == self.component_id:
                    unit.remove_status(status, battle)

    def on_unit_entered(self, battle: Battle, unit: Unit) -> None:
        self.sync_restrictions(battle)

    def on_unit_moved(self, battle: Battle, ctx: Any) -> None:
        self.sync_restrictions(battle)

    def on_turn_start(self, battle: Battle, active_unit: Optional[HeroUnit]) -> None:
        if active_unit is not None and active_unit.unit_id == self.source_unit_id:
            self.cleanup_restrictions(battle)
            battle.remove_field_effect(self)
            return
        self.sync_restrictions(battle)

    def can_attack_target(self, battle: Battle, actor: HeroUnit, target: HeroUnit) -> tuple[bool, str]:
        if self.unit_in_area(battle, actor):
            return False, f"{actor.name} 处于喷烟中，不能攻击。"
        return True, ""

    def blocks_skill_use(self, battle: Battle, actor: HeroUnit, skill: Skill) -> tuple[bool, str]:
        if skill.timing == "active" and self.unit_in_area(battle, actor):
            return True, f"{actor.name} 处于喷烟中，不能使用主动技能。"
        return False, ""


class DragonRideableMountTrait(RideableMountTrait):
    def on_owner_removed(self, battle: Battle) -> None:
        owner = self.owner
        if owner is None or not owner.mount_owner_id:
            return
        rider = battle.units.get(owner.mount_owner_id)
        if not isinstance(rider, HeroUnit) or not rider.alive:
            return
        duration = 2 if battle.unit_belongs_to_current_turn(rider) else 1
        replace_status_by_name(battle, rider, DragonMountCooldownStatus(duration))


class DragonDamageResistanceTrait(Trait):
    def __init__(self) -> None:
        super().__init__("龙鳞", "受到技能公式伤害时攻击值 -1，到 1；多格命中最多按 1 格计算。")

    def on_before_damage(self, battle: Battle, ctx: DamageContext) -> None:
        owner = self.owner
        if owner is None or ctx.target.unit_id != owner.unit_id:
            return
        if not ctx.is_skill or ctx.raw_damage is not None:
            return
        ctx.area_cell_hits = 1
        ctx.attack_power = max(1.0, ctx.attack_power - 1.0)


class DragonAreaAttackTrait(Trait):
    def __init__(self) -> None:
        super().__init__("龙爪", "普攻选择远程 3*3 区域；攻击命中后的速 -1、守 -1 效果破魔。")

    def patterns(self, battle: Battle, actor: HeroUnit) -> list[list[Position]]:
        patterns: list[list[Position]] = []
        for pattern in remote_rectangle_patterns(battle, actor, 3, 3):
            if battle.effect_units_at_cells(pattern):
                patterns.append(pattern)
        return patterns

    def selected_cells(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> list[Position]:
        declared_cells = battle.payload_positions(payload, "attack_cells")
        if declared_cells:
            return declared_cells
        return match_payload_pattern(payload, self.patterns(battle, actor))

    def basic_attack_preview(
        self,
        battle: Battle,
        actor: HeroUnit,
        payload: Optional[dict[str, Any]] = None,
    ) -> Optional[dict[str, Any]]:
        patterns = self.patterns(battle, actor)
        preview = pattern_selection_preview(patterns)
        targets: list[str] = []
        for pattern in patterns:
            for unit in battle.effect_units_at_cells(pattern):
                if unit.unit_id in targets:
                    continue
                targets.append(unit.unit_id)
        preview.update({"target_unit_ids": targets, "secondary_cells": [], "requires_target": True})
        return preview

    def basic_attack_payload_metadata(
        self,
        battle: Battle,
        actor: HeroUnit,
        payload: Optional[dict[str, Any]] = None,
    ) -> dict[str, Any]:
        resolved: dict[str, Any] = {
            "attack_name": "龙爪",
            "attack_tags": ["dragon_area_attack"],
            "area_attack": True,
        }
        if payload:
            declared_cells = battle.payload_positions(payload, "attack_cells")
            if declared_cells:
                resolved["attack_cells"] = positions_to_dict(declared_cells)
        return resolved

    def basic_attack_area_cells(
        self,
        battle: Battle,
        actor: HeroUnit,
        payload: Optional[dict[str, Any]] = None,
    ) -> Optional[list[Position]]:
        if payload is None:
            return None
        cells = self.selected_cells(battle, actor, payload)
        if not battle.payload_positions(payload, "attack_cells") and not battle.effect_units_at_cells(cells):
            raise ActionError("攻击区域内没有有效目标。")
        return cells

    def on_after_damage(self, battle: Battle, ctx: DamageContext) -> None:
        self.apply_debuff_after_hit(battle, ctx)

    def on_damage_cancelled(self, battle: Battle, ctx: DamageContext) -> None:
        self.apply_debuff_after_hit(battle, ctx)

    def apply_debuff_after_hit(self, battle: Battle, ctx: DamageContext) -> None:
        owner = self.owner
        if owner is None or ctx.source is None or ctx.source.unit_id != owner.unit_id:
            return
        if "dragon_area_attack" not in ctx.tags or ctx.target.player_id == owner.player_id:
            return
        if not damage_followup_effect_applies(ctx, allow_on_shield_break=True):
            return
        source_unit_id = owner.mount_owner_id or owner.unit_id
        apply_dragon_piercing_status(
            battle,
            owner,  # type: ignore[arg-type]
            ctx.target,  # type: ignore[arg-type]
            action_name="龙爪破魔",
            status=DragonAttackDebuffStatus(source_unit_id),
            source_unit_id=source_unit_id,
            tags={"attack", "dragon_area_attack", "dragon_attack_debuff"},
        )


class DragonMountSummon(AbstractHero):
    hero_code = "dragon_mount"
    hero_name = "龙"
    role = "坐骑"
    attribute = "火"
    race = "召唤物"
    level = 1
    base_stats = Stats(attack=3, defense=5, speed=5, attack_range=4, mana=0)
    footprint_width = 2
    footprint_height = 2
    stat_minimums = {"mana": 0.0}
    raw_skill_text = "飞行；普攻选择 3*3 区域。"
    raw_trait_text = "可乘骑；受到技能伤害 -1 到 1；最多受到一格伤害；攻击带有破魔的速 -1、守 -1 效果。"

    def __init__(self, player_id: int) -> None:
        super().__init__(player_id, is_summon=True)

    def build_skills(self) -> list[Skill]:
        return []

    def build_traits(self) -> list[Trait]:
        return [
            FlyingTrait(),
            DragonRideableMountTrait(),
            DragonDamageResistanceTrait(),
            DragonAreaAttackTrait(),
        ]


class DragonMountedStartTrait(Trait):
    def __init__(self) -> None:
        super().__init__("骑士开场坐骑", "出场时已经召唤出自己的龙，并且已经处于乘骑状态。")

    def on_enter_battle(self, battle: Battle) -> None:
        owner = self.owner
        if not isinstance(owner, HeroUnit) or owner.position is None:
            return
        if alive_owned_dragon_mount(battle, owner) is not None:
            return
        mount = DragonMountSummon(owner.player_id)
        mount.summoner_id = owner.unit_id
        mount.mount_owner_id = owner.unit_id
        mount.is_mount = True
        mount.can_act_on_entry_turn = True
        mount.turn_ready = True
        battle.add_unit(mount, owner.position)
        battle.set_mounted_state(owner, mount)


class DragonRiderTurnTrait(Trait):
    def __init__(self) -> None:
        super().__init__("龙骑统御", "龙骑下个回合开始前的临时效果在回合开始时结束；己方法师武将每个给自己 +1 魔。")

    def cleanup_source_effects(self, battle: Battle) -> None:
        owner = self.owner
        if owner is None:
            return
        for unit in battle.all_units():
            for status in list(unit.statuses):
                if isinstance(status, (DragonAttackDebuffStatus, DragonSlashSlowStatus)) and status.source_unit_id == owner.unit_id:
                    unit.remove_status(status, battle)
        for effect in list(battle.field_effects):
            if isinstance(effect, DragonSmokeFieldEffect) and effect.source_unit_id == owner.unit_id:
                effect.cleanup_restrictions(battle)
                battle.remove_field_effect(effect)

    def on_owner_turn_start(self, battle: Battle) -> None:
        owner = self.owner
        if not isinstance(owner, HeroUnit):
            return
        self.cleanup_source_effects(battle)
        mage_count = sum(
            1
            for unit in battle.player_units(owner.player_id)
            if unit.unit_id != owner.unit_id
            and not unit.is_summon
            and not unit.is_clone
            and unit.role == "法师"
            and unit.alive
            and unit.position is not None
            and not unit.banished
        )
        if mage_count <= 0:
            return
        gained = owner.gain_mana(mage_count)
        if gained > 0:
            battle.log(f"{owner.name} 因己方法师武将获得 {gained} 点魔。")

    def on_owner_removed(self, battle: Battle) -> None:
        self.cleanup_source_effects(battle)

    def on_owner_banished(self, battle: Battle) -> None:
        self.cleanup_source_effects(battle)


class DragonSummonSkill(Skill):
    def __init__(self) -> None:
        super().__init__(
            "summon_dragon",
            "召龙",
            "普通技能：召唤自己的 2*2 龙并乘骑；场上至多存在 1 条自己的龙，被破坏后需等待 1 个自己的回合。",
            target_mode="self",
        )

    def can_use(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any] | None = None) -> tuple[bool, str]:
        ok, reason = super().can_use(battle, actor, payload)
        if not ok:
            return ok, reason
        if alive_owned_dragon_mount(battle, actor) is not None:
            return False, "场上已经有自己的龙。"
        if actor.has_status("召龙冷却"):
            return False, "召龙仍在冷却中。"
        if actor.position is None:
            return False, "当前不在战场上。"
        test_mount = DragonMountSummon(actor.player_id)
        test_mount.mount_owner_id = actor.unit_id
        test_mount.is_mount = True
        if not battle.can_place_unit(test_mount, actor.position, ignore=test_mount, mover=test_mount):
            return False, "当前位置无法召唤龙。"
        return True, ""

    def execute(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> None:
        if actor.position is None:
            raise ActionError("当前不在战场上。")
        mount = DragonMountSummon(actor.player_id)
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


class DragonSlashSkill(SimulatedEffectDeltaScoring, DeclaredAreaSkillMixin, Skill):
    def ai_attack_power(self, battle, actor, payload, target, cells) -> float:
        return 5.0

    ai_candidate_plan = RANKED_CANDIDATES
    def ai_unit_delta_bonus(self, context: AISkillScoreContext, unit: Unit,
                            speed: float, position: Position | None) -> float:
        if not unit.alive:
            return 0.0
        value = max(0.0, speed - unit.stat("speed")) * 15.0
        if unit.position != position:
            value += 25.0 + (15.0 if context.actor.attacks_used < context.actor.attack_actions_per_turn() else 0.0)
        return value

    directions = ChainPullSkill.directions

    def __init__(self) -> None:
        super().__init__(
            "dragon_slash",
            "龙斩",
            "普通技能：每回合最多 1 次；选择身前直线 5 格，按攻击值 5 造成技能伤害；带有破魔的链条效果，收到链条效果的单位速 -2，直到龙骑下个回合开始前。",
            max_uses_per_turn=1,
            target_mode="cell",
        )

    def source_body(self, battle: Battle, actor: HeroUnit) -> HeroUnit:
        return battle.effect_recipient(actor)  # type: ignore[return-value]

    def ignored_units(self, battle: Battle, actor: HeroUnit) -> set[str]:
        ignored = {actor.unit_id}
        mount = battle.mounted_unit_for(actor)
        if mount is not None:
            ignored.add(mount.unit_id)
        return ignored

    def patterns(self, battle: Battle, actor: HeroUnit) -> list[list[Position]]:
        source = self.source_body(battle, actor)
        source_cells = battle.unit_cells(source)
        source_keys = {position_key(cell) for cell in source_cells}
        patterns: list[list[Position]] = []
        seen: set[tuple[tuple[int, int], ...]] = set()
        for origin in source_cells or ([actor.position] if actor.position else []):
            if origin is None:
                continue
            for direction in self.directions:
                pattern: list[Position] = []
                for cell in battle.line_positions(origin, direction, 5 + len(source_cells)):
                    if position_key(cell) in source_keys:
                        continue
                    pattern.append(cell)
                    if len(pattern) >= 5:
                        break
                if not pattern:
                    continue
                key = pattern_signature(pattern)
                if key in seen:
                    continue
                seen.add(key)
                patterns.append(pattern)
        return patterns

    def chosen_line(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> list[Position]:
        return self.chosen_cells(battle, actor, payload)

    def first_hit(self, battle: Battle, actor: HeroUnit, cells: list[Position]) -> HeroUnit | None:
        ignored = self.ignored_units(battle, actor)
        for cell in cells:
            for unit in battle.effect_units_at_cells([cell]):
                if unit.unit_id not in ignored:
                    return unit  # type: ignore[return-value]
        return None

    def apply_chain_effect(self, battle: Battle, actor: HeroUnit, target: HeroUnit, cells: list[Position]) -> None:
        if not target.alive or target.position is None or target.banished:
            return
        source = self.source_body(battle, actor)
        target_ctx = battle.validate_target(
            actor,
            target,
            action_name="龙斩链条",
            is_skill=True,
            is_hostile=target.player_id != actor.player_id,
            ignore_shield=True,
            cannot_evade=True,
            ignore_targeting_restrictions=True,
            tags={"skill", "dragon_slash", "chain_pull"},
        )
        if target_ctx.cancelled:
            battle.log_public_event(target_ctx.reason, source=actor, target=target)
            return
        if target.player_id != actor.player_id and target.total_shields() > 0:
            target.consume_one_shield()
            battle.log_public_event(
                f"{target.name} 的 1 层护盾被【龙斩链条】贯穿并打碎。",
                source=actor,
                target=target,
            )
        replace_source_status(battle, target, DragonSlashSlowStatus(actor.unit_id), actor.unit_id)
        target_cells = battle.unit_cells(target)
        source_cells = battle.unit_cells(source)
        try_cells = [cell for cell in cells if any(cell.distance_to(source_cell) <= 1 for source_cell in source_cells)]
        for destination in try_cells:
            if any(destination == occupied for occupied in target_cells):
                battle.log(f"{target.name} 已经在 {source.name} 周围。")
                return
            if battle.can_place_unit(target, destination, ignore=target):
                battle.move_unit(
                    target,
                    destination,
                    via_skill=True,
                    forced=True,
                    max_distance=battle.width + battle.height,
                    tags={"dragon_slash", "chain_pull"},
                )
                battle.log(f"{actor.name} 用龙斩链条将 {target.name} 拉到身边。")
                return
        battle.log(f"{actor.name} 的龙斩链条击中了 {target.name}，但没有合法落点。")

    def execute(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> None:
        cells = self.chosen_line(battle, actor, payload)
        ignored = self.ignored_units(battle, actor)
        targets = [
            unit
            for unit in battle.effect_units_at_cells(cells)
            if unit.unit_id not in ignored
        ]
        first = self.first_hit(battle, actor, cells)
        first_context = None
        for unit in targets:
            ctx = battle.resolve_damage(
                DamageContext(
                    source=actor,
                    target=unit,
                    attack_power=5,
                    is_skill=True,
                    action_name="龙斩",
                    area_cell_hits=battle.unit_hit_count_for_cells(unit, cells),
                    tags={"skill", "attack", "dragon_slash"},
                )
            )
            if first is not None and unit.unit_id == first.unit_id:
                first_context = ctx
        if first is not None and first_context is not None and damage_followup_effect_applies(first_context, allow_on_shield_break=True):
            self.apply_chain_effect(battle, actor, first, cells)

    def preview(self, battle: Battle, actor: HeroUnit) -> dict[str, Any]:
        preview = pattern_selection_preview(self.patterns(battle, actor))
        targets: list[str] = []
        for pattern in self.patterns(battle, actor):
            for unit in battle.effect_units_at_cells(pattern):
                if unit.unit_id in self.ignored_units(battle, actor) or unit.unit_id in targets:
                    continue
                targets.append(unit.unit_id)
        preview.update({"target_unit_ids": targets, "secondary_cells": positions_to_dict(battle.unit_cells(self.source_body(battle, actor))), "requires_target": True})
        return preview

    def get_target_cells_for_payload(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> list[Position]:
        return self.chosen_line(battle, actor, payload)

    def get_target_units_for_payload(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> list[HeroUnit]:
        cells = self.chosen_line(battle, actor, payload)
        ignored = self.ignored_units(battle, actor)
        return [unit for unit in battle.effect_units_at_cells(cells) if unit.unit_id not in ignored]  # type: ignore[list-item]


class DragonSmokeSkill(DeclaredAreaSkillMixin, Skill):
    ai_candidate_plan = RANKED_CANDIDATES
    def ai_priority_score(self, context: AISkillScoreContext) -> float:
        return context.owned_effect_score(self)

    def ai_effect_score(self, context: AISkillScoreContext) -> float:
        battle, actor, payload = context.battle, context.actor, context.payload
        cells = self.chosen_cells(battle, actor, payload)
        keys = {(cell.x, cell.y) for cell in cells}
        score = -20.0
        for unit in battle.all_units():
            if not unit.alive or unit.banished or unit.position is None:
                continue
            body = battle.effect_recipient(unit)
            if not any((cell.x, cell.y) in keys for cell in battle.unit_cells(body)):
                continue
            if any(effect.name == "喷烟" and effect.unit_in_area(battle, unit) for effect in battle.field_effects):
                continue
            allied = unit.player_id == actor.player_id
            current = battle.unit_belongs_to_current_turn(unit)
            attacks = max(0, unit.attack_actions_per_turn() - (unit.attacks_used if current else 0))
            active = sum(candidate.timing == "active" and candidate.code != self.code
                         and candidate.cooldown_remaining == 0
                         and (not current or candidate.max_uses_per_turn is None or candidate.uses_this_turn < candidate.max_uses_per_turn)
                         and unit.current_mana >= candidate.mana_cost for candidate in unit.skills)
            denied = (0 if unit.cannot_attack else attacks * 24.0) + min(3, active) * 15.0
            if denied <= 0:
                continue
            escape = False
            if not unit.cannot_move and not unit.cannot_normal_move:
                distance = unit.remaining_normal_move_distance(battle) if current else unit.normal_move_distance()
                escape = any(not any((cell.x, cell.y) in keys for cell in battle.unit_cells_at(body, dest))
                             for dest in context.reachable_positions_for(body, distance))
            value = denied * (0.55 if escape else 1.0) + 8.0
            score += -value * 1.25 if allied else value
        return score

    def __init__(self) -> None:
        super().__init__(
            "smoke_spray",
            "喷烟",
            "普通技能：费 1 魔，每回合最多 1 次；远程选择 3*6 或 6*3 区域；没有伤害，该区域内所有单位直到龙骑下个回合开始前不能使用主动技能或攻击，后进入的单位也会受到影响。",
            mana_cost=1,
            max_uses_per_turn=1,
            target_mode="cell",
        )

    def patterns(self, battle: Battle, actor: HeroUnit) -> list[list[Position]]:
        return combined_remote_rectangle_patterns(battle, actor, [(3, 6), (6, 3)])

    def execute(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> None:
        cells = self.chosen_cells(battle, actor, payload)
        effect = DragonSmokeFieldEffect(actor.unit_id, cells)
        battle.add_field_effect(effect)
        effect.sync_restrictions(battle)

    def preview(self, battle: Battle, actor: HeroUnit) -> dict[str, Any]:
        return area_patterns_preview(battle, actor, self.patterns(battle, actor))

    def get_target_cells_for_payload(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> list[Position]:
        return self.chosen_cells(battle, actor, payload)

    def get_target_units_for_payload(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> list[HeroUnit]:
        return battle.units_at_cells(self.chosen_cells(battle, actor, payload))  # type: ignore[return-value]


class DragonRider(AbstractHero):
    hero_code = "dragon_rider"
    hero_name = "龙骑"
    role = "骑士"
    attribute = "火"
    race = "兽人"
    level = 5
    base_stats = Stats(attack=4, defense=4, speed=3, attack_range=1, mana=5)
    entry_footprint_width = 2
    entry_footprint_height = 2
    raw_skill_text = "龙息 召龙（攻3守5速5范4；飞行，占2*2；受到公式技能攻击值-1，到1；固定扣血不减免；最多受到一格伤害；攻击范围3*3；攻击带有以下破魔效果：被击中后直到龙骑下个自己的回合开始前速-1，守-1） 保护 龙斩（一回合一次；伤5格；带有破魔的链条效果；收到链条效果的单位直到龙骑下个自己的回合开始前速-2，到1） 链条 【1喷烟（一回合最多使用一次；3*6；没有伤害；被击中区域直到龙骑下个自己的回合开始前所有除单位无法使用主动技能，攻击）"
    raw_trait_text = "场上每有一个己方“法师”武将，每回合开始时魔+1"

    def build_skills(self) -> list[Skill]:
        return [
            DragonBreathSkill(),
            DragonSummonSkill(),
            PassiveProtectionSkill(),
            DragonSlashSkill(),
            ChainPullSkill(),
            DragonSmokeSkill(),
        ]

    def build_traits(self) -> list[Trait]:
        return [
            DragonMountedStartTrait(),
            DragonRiderTurnTrait(),
        ]
