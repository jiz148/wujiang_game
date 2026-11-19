"""R350: Delongkula's ancient-dragon hunt, spear and constrained reflections."""

from __future__ import annotations

from typing import Any

from wujiang.tactical.engine.core import (
    ActionError, Battle, DamageContext, Position, QueuedAction, Skill, Stats,
    StatusEffect, TargetContext, Trait, Unit, is_army_soldier,
)
from wujiang.tactical.heroes.ai_policy import HeroAIPolicy
from wujiang.tactical.heroes.base import AbstractHero
from wujiang.tactical.heroes.common import FlyingTrait, pattern_signature
from wujiang.tactical.heroes.mechanics.area_helpers import area_patterns_preview, combined_remote_rectangle_patterns
from wujiang.tactical.heroes.mechanics.declared_area import DeclaredAreaSkillMixin
from wujiang.tactical.heroes.mechanics.next_five_effects import (
    apply_piercing_status_effect, damage_followup_effect_applies,
)
from wujiang.tactical.heroes.mechanics.next_five_geometry import square_around_cells


def _present(unit: Unit | None) -> bool:
    return unit is not None and unit.alive and not unit.banished and unit.position is not None


def _true_hero(unit: Unit) -> bool:
    return not unit.is_summon and not unit.is_clone and not is_army_soldier(unit)


def _dragon_hunt_target(battle: Battle, unit: Unit) -> bool:
    seen: set[str] = set()
    current = unit
    while current.unit_id not in seen:
        if "龙" in current.name or (_true_hero(current) and current.race in {"飞龙", "古龙"}):
            return True
        if not current.is_summon or not current.summoner_id:
            break
        seen.add(current.unit_id)
        parent = battle.units.get(current.summoner_id)
        if parent is None:
            return False
        current = parent
    return False


def _dragon_growth_target(unit: Unit) -> bool:
    return "龙" in unit.name or (_true_hero(unit) and unit.race in {"飞龙", "古龙"})


class AncientDragonHuntTrait(Trait):
    def __init__(self) -> None:
        super().__init__("古龙狩猎", "普攻和技能对飞龙、古龙武将或名字带龙的单位及其召唤物破魔。")

    def on_targeted(self, battle: Battle, ctx: TargetContext) -> None:
        if (ctx.actor is self.owner and not ctx.from_field_effect
                and (ctx.is_skill or "attack" in ctx.tags)
                and _dragon_hunt_target(battle, ctx.target)):
            ctx.ignore_shield = True

    def on_before_damage(self, battle: Battle, ctx: DamageContext) -> None:
        if (ctx.source is self.owner and not ctx.from_field_effect
                and (ctx.is_skill or "attack" in ctx.tags) and _dragon_hunt_target(battle, ctx.target)):
            ctx.ignore_shield = True


class AncientDragonGrowth(StatusEffect):
    def __init__(self) -> None:
        super().__init__("古龙战果", "每次亲自真正击杀飞龙、古龙武将或名字带龙的单位后攻、守各+1。")

    def modify_stat(self, stat_name: str, value: float) -> float:
        return value + 1 if stat_name in {"attack", "defense"} else value


class AncientDragonKillTrait(Trait):
    def __init__(self) -> None:
        super().__init__("古龙战果", "亲自真正破坏飞龙、古龙武将或名字带龙的单位后，永久攻守各+1。")

    def on_unit_destroyed(self, battle: Battle, destroyed_unit: Unit) -> None:
        owner = self.owner
        if (not _present(owner) or not _dragon_growth_target(destroyed_unit)
                or getattr(destroyed_unit, "_destroying_source_unit_id", None) != owner.unit_id):
            return
        owner.add_status(AncientDragonGrowth(), source=owner)
        battle.log_public_event(f"{owner.name} 击破龙类目标，攻、守永久各+1。",
                                source=owner, target=destroyed_unit)


class MeleeBloodDecay(StatusEffect):
    def __init__(self) -> None:
        super().__init__("龙血鬼的衰竭", "每个本人回合结束时当前血减半。")

    def on_owner_turn_end(self, battle: Battle) -> None:
        owner = self.owner
        if _present(owner) and owner.current_hp > 0:
            battle.resolve_damage(DamageContext(
                source=owner, target=owner, attack_power=0,
                raw_damage=round(owner.current_hp / 2, 4), is_skill=True,
                action_name=self.name, tags={"special_health_loss", "dragon_blood_decay"},
            ))


class MeleeBloodCurseTrait(Trait):
    def __init__(self) -> None:
        super().__init__("龙血反噬", "对本人造成近战生命伤害者，每个本人回合末当前血减半。")

    def on_after_damage(self, battle: Battle, ctx: DamageContext) -> None:
        owner = self.owner
        source = ctx.source
        if (owner is None or ctx.target is not owner or source is None or source is owner
                or not _present(source) or ctx.actual_damage <= 0 or ctx.from_field_effect
                or owner.position is None
                or min((left.distance_to(right)
                        for left in source.footprint_cells_at(source.position)
                        for right in owner.footprint_cells_at(owner.position)),
                       default=10**9) > 1):
            return
        if not source.has_status("龙血鬼的衰竭"):
            source.add_status(MeleeBloodDecay(), source=owner)
            battle.log_public_event(f"{source.name} 近战伤及 {owner.name}，受到龙血衰竭。",
                                    source=owner, target=source)


class SpearSuppression(StatusEffect):
    """The saved R350-Q001 answer suppresses intrinsic flying only."""
    suppresses_native_flying = True

    def __init__(self, applied_turn: int) -> None:
        super().__init__("击龙枪压制", "速-2（最低1）；原生飞行特性无效2本人轮。")
        self.applied_turn = applied_turn
        self.own_ends = 0

    def modify_stat(self, stat_name: str, value: float) -> float:
        return max(1, value - 2) if stat_name == "speed" else value

    def _refresh_flight(self, owner: Unit, *, applying: bool) -> None:
        FlyingTrait.refresh_flight_state(owner, suppressing=applying)

    def bind(self, owner: Unit) -> "SpearSuppression":
        super().bind(owner)
        self._refresh_flight(owner, applying=True)
        return self

    def on_removed(self, battle: Battle) -> None:
        if self.owner is not None:
            self._refresh_flight(self.owner, applying=False)

    def on_owner_turn_end(self, battle: Battle) -> None:
        if battle.turn_number <= self.applied_turn:
            return
        self.own_ends += 1
        if self.own_ends >= 2 and self.owner is not None:
            self.owner.remove_status(self, battle)


def _spear_suppression(battle: Battle, actor: Unit, queued: QueuedAction) -> None:
    target = battle.units.get(str(queued.payload.get("recipient_id") or ""))
    if not _present(actor) or not _present(target):
        return
    status = SpearSuppression(battle.turn_number)
    applied = apply_piercing_status_effect(
        battle, actor, target, action_name="击龙枪压制", status=status,
        is_skill=True, tags={"skill", "dragon_spear", "control"},
    )
    if applied:
        battle.log_public_event(f"{target.name} 被击龙枪压制两轮，速-2且原生飞行失效。",
                                source=actor, target=target)


class DragonSpearSkill(DeclaredAreaSkillMixin, Skill):
    ai_payload_value_owned = True

    def __init__(self) -> None:
        super().__init__("dragon_spear", "击龙枪",
                         "每回合一次；远程2×3双方技能伤，附加破魔速-2和原生飞行无效2轮。",
                         max_uses_per_turn=1, target_mode="cell")

    def patterns(self, battle: Battle, actor: Unit) -> list[list[Position]]:
        return combined_remote_rectangle_patterns(battle, actor, [(2, 3), (3, 2)])

    def preview(self, battle: Battle, actor: Unit) -> dict[str, Any]:
        return area_patterns_preview(battle, actor, self.patterns(battle, actor))

    def get_target_cells_for_payload(self, battle: Battle, actor: Unit,
                                     payload: dict[str, Any]) -> list[Position]:
        return self.chosen_cells(battle, actor, payload)

    def get_target_units_for_payload(self, battle: Battle, actor: Unit,
                                     payload: dict[str, Any]) -> list[Unit]:
        return battle.effect_units_at_cells(self.chosen_cells(battle, actor, payload))

    def can_use(self, battle: Battle, actor: Unit,
                payload: dict[str, Any] | None = None) -> tuple[bool, str]:
        ok, reason = super().can_use(battle, actor, payload)
        if not ok:
            return ok, reason
        if payload and (payload.get("cells") or payload.get("declared_area_cells")):
            try:
                self.chosen_cells(battle, actor, payload)
            except (ActionError, KeyError, TypeError, ValueError):
                return False, "请选择合法的击龙枪2×3范围。"
        return True, ""

    def execute(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> None:
        cells = self.chosen_cells(battle, actor, payload)
        recipients: list[Unit] = []
        for target in battle.effect_units_at_cells(cells):
            if not _present(target):
                continue
            ctx = battle.resolve_damage(DamageContext(
                source=actor, target=target, attack_power=actor.stat("attack"), is_skill=True,
                action_name=self.name, area_cell_hits=battle.unit_hit_count_for_cells(target, cells),
                tags={"skill", self.code},
            ))
            if (damage_followup_effect_applies(ctx, allow_on_shield_break=True)
                    and _present(ctx.target) and ctx.target not in recipients):
                recipients.append(ctx.target)
        for target in recipients:
            battle.queue_skill_effect_action(
                actor=actor, display_name="击龙枪压制", effect_code="dragon_spear_suppression",
                effect_resolver=_spear_suppression,
                payload={"recipient_id": target.unit_id, "ignore_shield": True},
                target_units=[target], target_cells=battle.unit_cells(target),
                include_cell_units=False,
            )

    def ai_payload_candidates(self, battle: Battle, actor: Unit,
                              action: dict[str, Any]) -> list[dict[str, Any]] | None:
        visible = [unit for unit in battle.all_units() if _present(unit)
                   and battle.unit_can_be_selected(unit, actor=actor)[0]]
        ranked: list[tuple[float, list[Position]]] = []
        for cells in self.patterns(battle, actor):
            impacted = [unit for unit in visible if any(cell in cells for cell in battle.unit_cells(unit))]
            if not any(unit.player_id != actor.player_id for unit in impacted):
                continue
            value = sum((1.0 if unit.player_id != actor.player_id else -1.6)
                        * (1.0 + (0.4 if any(isinstance(trait, FlyingTrait) for trait in unit.traits) else 0.0))
                        for unit in impacted)
            ranked.append((value, cells))
        ranked.sort(key=lambda item: (-item[0], pattern_signature(item[1])))
        return [{"type": "skill", "unit_id": actor.unit_id, "skill_code": self.code,
                 "cells": [cell.to_dict() for cell in cells]} for _, cells in ranked[:16]]

    def ai_candidate_payloads(self, context: Any) -> list[dict[str, Any]]:
        return self.ai_payload_candidates(context.battle, context.actor, context.action) or []

    def ai_empty_payload_filter_reason(self, context: Any, patterns: list) -> str | None:
        """An empty or ally-only area is legal for a player, but not useful to AI."""
        battle, actor = context.battle, context.actor
        visible_enemies = [unit for unit in battle.all_units()
                           if _present(unit) and unit.player_id != actor.player_id
                           and battle.unit_can_be_selected(unit, actor=actor)[0]]
        for cells in self.patterns(battle, actor):
            if any(any(cell in cells for cell in battle.unit_cells(unit))
                   for unit in visible_enemies):
                return None
        return "no_visible_enemy_in_legal_area"

    def ai_effective_enemy_impact(self, battle: Battle, actor: Unit,
                                  payload: dict[str, Any], targets: list[Unit] | None = None,
                                  shared: Any = None, action: dict[str, Any] | None = None) -> bool:
        try:
            recipients = self.get_target_units_for_payload(battle, actor, payload)
        except (ActionError, KeyError, TypeError, ValueError):
            return False
        return any(target.player_id != actor.player_id and not target.magic_immunity
                   and battle.unit_can_be_selected(target, actor=actor)[0]
                   for target in recipients)

    def ai_payload_value(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> float | None:
        try:
            cells = self.chosen_cells(battle, actor, payload)
        except (ActionError, KeyError, TypeError, ValueError):
            return -100.0
        value = -25.0
        for target in battle.effect_units_at_cells(cells):
            if target.magic_immunity or not battle.unit_can_be_selected(target, actor=actor)[0]:
                continue
            damage = 0.0 if target.total_shields() and not _dragon_hunt_target(battle, target) else min(
                target.current_hp, battle.damage_rule.calculate_damage(
                    actor.stat("attack") + max(0, battle.unit_hit_count_for_cells(target, cells) - 1),
                    target.stat("defense")))
            benefit = damage * 100 + 24 + (20 if any(isinstance(trait, FlyingTrait)
                                                       for trait in target.traits) else 0)
            value += benefit if target.player_id != actor.player_id else -1.6 * benefit
        return value

    def ai_priority_score(self, context: Any) -> float:
        value = self.ai_payload_value(context.battle, context.actor, context.payload)
        return value if value is not None else -100.0


class ReflectionAIPolicy(HeroAIPolicy):
    def _targets(self, battle: Battle, actor: Unit) -> list[Unit]:
        return [unit for unit in battle.all_units() if _present(unit)
                and unit.player_id != actor.player_id and _true_hero(unit)
                and unit.name == actor.name and battle.unit_can_be_selected(unit, actor=actor)[0]]

    def normal_move_candidate_score(self, battle: Battle, actor: Unit, destination: Position,
                                    profile: Any, score: float, shared: Any) -> float | None:
        targets = self._targets(battle, actor)
        return score - min((destination.distance_to(target.position) for target in targets), default=20) * 35

    def tendency_boarding_destination(self, battle: Battle, actor: Unit,
                                      cells: list[Position]) -> Position | None:
        targets = self._targets(battle, actor)
        if not targets or not cells:
            return None
        return min(cells, key=lambda cell: (min(cell.distance_to(target.position)
                                                for target in targets), cell.y, cell.x))


class ReflectionLifetime(StatusEffect):
    def __init__(self, created_turn: int) -> None:
        super().__init__("恐惧倒影期限", "施放后四个完整英雄行动回合结束时破坏。")
        self.created_turn = created_turn
        self.completed_turns = 0

    def can_attack_target(self, battle: Battle, actor: Unit, target: Unit) -> tuple[bool, str]:
        if (actor is self.owner and (target.player_id == actor.player_id
                or not _true_hero(target) or target.name != actor.name)):
            return False, "恐惧倒影只能攻击同名敌方武将。"
        return True, ""

    def on_any_turn_end(self, battle: Battle, ended_player_id: int) -> None:
        owner = self.owner
        if owner is None or not owner.alive or battle.turn_number <= self.created_turn or battle.is_army_turn():
            return
        self.completed_turns += 1
        if self.completed_turns >= 4:
            owner.alive = False
            battle.log_public_event(f"{owner.name} 的恐惧倒影期限结束。", target=owner)
            battle.remove_unit(owner)


class FearReflection(AbstractHero):
    hero_code = "fear_reflection"
    hero_name = "恐惧倒影"
    role = "召唤物"
    ai_policy = ReflectionAIPolicy()
    base_stats = Stats(attack=1, defense=1, speed=1, attack_range=1, mana=0)

    def __init__(self, player_id: int, original: Unit) -> None:
        super().__init__(player_id, is_summon=True)
        self.name = original.name
        self.title = original.name
        self.attribute = original.attribute
        self.race = original.race
        self.level = original.level
        self.base_stats = Stats(
            attack=original.stat("attack"), defense=original.stat("defense"),
            speed=original.stat("speed"), attack_range=original.targeting_range(),
            mana=float(original.stat("mana")),
        )
        self.current_mana = original.current_mana

    def build_skills(self) -> list[Skill]:
        return []

    def build_traits(self) -> list[Trait]:
        return []


class FearReflectionSkill(Skill):
    ai_payload_value_owned = True
    ai_enemy_impact_requirement = False
    ai_optional_choice_owned = True
    requires_choice_replay_when_used = True

    def __init__(self) -> None:
        super().__init__("fear_reflection", "恐惧倒影",
                         "每2轮一次；对周围9×9内每名敌方武将召一名无技能同名限时倒影。",
                         cooldown_turns=2, target_mode="self")

    def originals(self, battle: Battle, actor: Unit, *, visible_only: bool = False) -> list[Unit]:
        return [unit for unit in battle.all_units() if _present(unit)
                and unit.player_id != actor.player_id and _true_hero(unit)
                and battle.distance_between_units(actor, unit) <= 4
                and (not visible_only or battle.unit_can_be_selected(unit, actor=actor)[0])]

    def landings(self, battle: Battle, actor: Unit, original: Unit) -> list[Position]:
        cells = set(square_around_cells(battle, battle.unit_cells(original), radius=1))
        cells.difference_update(battle.unit_cells(original))
        return sorted((cell for cell in cells if battle.can_place_unit(actor, cell, mover=actor)),
                      key=lambda cell: (cell.y, cell.x))

    def candidates(self, battle: Battle, actor: Unit, *, visible_only: bool = False) -> list[tuple[Unit, list[Position]]]:
        return [(unit, cells) for unit in self.originals(battle, actor, visible_only=visible_only)
                if (cells := self.landings(battle, actor, unit))]

    def preview(self, battle: Battle, actor: Unit) -> dict[str, Any]:
        candidates = self.candidates(battle, actor, visible_only=True)
        return {"cells": [cell.to_dict() for _, cells in candidates for cell in cells],
                "target_unit_ids": [unit.unit_id for unit, _ in candidates],
                "secondary_cells": [], "requires_target": False,
                "choice_after_cast": True}

    def can_use(self, battle: Battle, actor: Unit,
                payload: dict[str, Any] | None = None) -> tuple[bool, str]:
        ok, reason = super().can_use(battle, actor, payload)
        return (ok, reason) if not ok or self.candidates(battle, actor) else (False, "周围没有可召倒影的敌方武将。")

    def execute(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> None:
        for original, _ in self.candidates(battle, actor):
            cells = self.landings(battle, actor, original)
            if not cells:
                continue
            options = [f"{cell.x},{cell.y}" for cell in cells]
            choice = battle.optional_position_choice(
                actor, options, action_name=f"恐惧倒影：选择 {original.name} 周围落格",
                kind="required_placement")
            if choice not in options:
                choice = options[0]
            x, y = (int(part) for part in choice.split(",", 1))
            mirror = FearReflection(actor.player_id, original)
            battle.summon_unit(mirror, Position(x, y), summoner=actor)
            mirror.add_status(ReflectionLifetime(battle.turn_number), source=actor)
            battle.log_public_event(f"{actor.name} 在 {original.name} 周围召出同名恐惧倒影。",
                                    source=actor, target=mirror)

    def ai_optional_choice_score(self, battle: Battle, actor: Unit,
                                 prompt: dict[str, Any], choice: str) -> float | None:
        if prompt.get("kind") != "required_placement" or not str(
                prompt.get("action_name") or "").startswith("恐惧倒影"):
            return None
        try:
            x, y = (int(part) for part in choice.split(",", 1))
        except (TypeError, ValueError):
            return -1000
        cell = Position(x, y)
        targets = self.originals(battle, actor, visible_only=True)
        return 20.0 - min((cell.distance_to(target.position) for target in targets), default=20)

    def ai_payload_value(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> float | None:
        candidates = self.candidates(battle, actor, visible_only=True)
        return sum(38.0 + min(20.0, unit.stat("attack") * 4) for unit, _ in candidates) - 18.0 if candidates else -100.0

    def ai_priority_score(self, context: Any) -> float:
        value = self.ai_payload_value(context.battle, context.actor, context.payload)
        return value if value is not None else -100.0
