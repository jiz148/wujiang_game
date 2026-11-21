"""R363 吸血鬼王子：巢穴乘骑、积点血咒与条件影袭。"""

from __future__ import annotations

from typing import Any

from wujiang.tactical.engine.core import (
    ActionError, Battle, DamageContext, HealContext, Position, QueuedAction, Skill,
    Stats, StatusEffect, Trait, Unit,
)
from wujiang.tactical.heroes.ai_candidate_plan import SkillCandidatePlan
from wujiang.tactical.heroes.ai_policy import AIReactionScoreContext, AISkillScoreContext
from wujiang.tactical.heroes.base import AbstractHero
from wujiang.tactical.heroes.common import positions_to_dict
from wujiang.tactical.heroes.mechanics.dragon_breath import RemoteDragonBreathSkill
from wujiang.tactical.heroes.mechanics.next_five_effects import remote_rectangle_patterns


def _present(unit: Unit | None) -> bool:
    return bool(unit is not None and unit.alive and not unit.banished and unit.position is not None)


class VampireNestRideableTrait(Trait):
    def __init__(self) -> None:
        super().__init__("可乘骑", "王子可乘骑自己召出的巢穴；乘骑受体按通用坐骑规则映射。")


class VampireNestSummon(AbstractHero):
    hero_code = "vampire_nest"
    hero_name = "吸血鬼巢穴"
    role = "巢穴"
    attribute = "暗"
    race = "召唤物"
    level = 1
    base_stats = Stats(attack=0, defense=5, speed=0, attack_range=1, mana=0)
    stat_minimums = {"attack": 0.0, "speed": 0.0}
    raw_skill_text = ""
    raw_trait_text = "可乘骑；固定巢穴"

    def __init__(self, player_id: int) -> None:
        super().__init__(player_id, is_summon=True)
        self.cannot_move = True
        self.cannot_attack = True

    def build_skills(self) -> list[Skill]:
        return []

    def build_traits(self) -> list[Trait]:
        return [VampireNestRideableTrait()]


def owned_nest(battle: Battle, actor: Unit) -> VampireNestSummon | None:
    return next((unit for unit in battle.all_units()
                 if isinstance(unit, VampireNestSummon) and _present(unit)
                 and unit.summoner_id == actor.unit_id), None)


def friendly_nest(battle: Battle, actor: Unit) -> VampireNestSummon | None:
    return next((unit for unit in battle.all_units()
                 if isinstance(unit, VampireNestSummon) and _present(unit)
                 and unit.player_id == actor.player_id), None)


class VampireNestRecoveryTrait(Trait):
    def __init__(self) -> None:
        super().__init__("巢穴自然回复", "乘骑自己的吸血鬼巢穴时，本人轮初自然回血并回魔。")

    def on_owner_turn_start(self, battle: Battle) -> None:
        owner = self.owner
        if not _present(owner) or battle.mounted_unit_for(owner) is not owned_nest(battle, owner):
            return
        gained = owner.gain_mana(1)
        if gained:
            battle.log_public_event(f"{owner.name} 在巢穴自然回魔 {gained:g}。", source=owner, target=owner)
        battle.heal(HealContext(source=owner, target=owner, amount=0.25,
                                action_name="巢穴自然回复", direct_target=True))


class VampireNestSkill(Skill):
    ai_payload_value_owned = True

    def __init__(self) -> None:
        super().__init__("vampire_nest", "吸血鬼巢穴",
                         "每3本人轮可在脚下召一座守5巢穴并骑上；己方场上至多一座。",
                         cooldown_turns=3, max_uses_per_turn=1, target_mode="self")

    def can_use(self, battle: Battle, actor: Unit,
                payload: dict[str, Any] | None = None) -> tuple[bool, str]:
        ok, reason = super().can_use(battle, actor, payload)
        if not ok:
            return ok, reason
        if actor.position is None or friendly_nest(battle, actor) is not None:
            return False, "当前无法在原地召唤第二座己方吸血鬼巢穴。"
        probe = VampireNestSummon(actor.player_id)
        probe.mount_owner_id = actor.unit_id
        probe.is_mount = True
        if not battle.can_place_unit(probe, actor.position, ignore=probe, mover=probe):
            return False, "当前占格不能合法放置吸血鬼巢穴。"
        return True, ""

    def execute(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> None:
        assert actor.position is not None
        if friendly_nest(battle, actor) is not None:
            raise ActionError("己方场上已有吸血鬼巢穴。")
        nest = VampireNestSummon(actor.player_id)
        nest.summoner_id = actor.unit_id
        nest.mount_owner_id = actor.unit_id
        nest.is_mount = True
        if not battle.can_place_unit(nest, actor.position, ignore=nest, mover=nest):
            raise ActionError("原地已不能合法放置吸血鬼巢穴。")
        battle.summon_unit(nest, actor.position, summoner=actor)
        battle.set_mounted_state(actor, nest)
        battle.log_public_event(f"{actor.name} 在原地筑起吸血鬼巢穴并骑上。", source=actor, target=nest)

    def preview(self, battle: Battle, actor: Unit) -> dict[str, Any]:
        return {"cells": [actor.position.to_dict()] if actor.position else [],
                "target_unit_ids": [actor.unit_id], "secondary_cells": [],
                "requires_target": False}

    def ai_payload_candidates(self, battle: Battle, actor: Unit,
                              action: dict[str, Any]) -> list[dict[str, Any]]:
        return ([{"type": "skill", "unit_id": actor.unit_id, "skill_code": self.code}]
                if self.ai_payload_value(battle, actor, {}) > 0 else [])

    def ai_requires_enemy_impact(self, battle: Battle, actor: Unit,
                                 payload: dict[str, Any]) -> bool:
        return False

    def ai_priority_score(self, context: AISkillScoreContext) -> float:
        return self.ai_payload_value(context.battle, context.actor, context.payload)

    def ai_payload_value(self, battle: Battle, actor: Unit,
                         payload: dict[str, Any]) -> float:
        if not self.can_use(battle, actor, {})[0]:
            return -1000.0
        return (15.0 + (actor.max_health - actor.current_hp) * 85.0
                + (actor.max_mana() - actor.current_mana) * 12.0)


class VampireReturnSkill(Skill):
    is_displacement_skill = True
    moves_owner_on_use = True
    ai_payload_value_owned = True

    def __init__(self) -> None:
        super().__init__("vampire_return", "回巢", "随时使用，每武将轮一次，瞬移回自己召出的巢穴并骑上。",
                         max_uses_per_turn=1, target_mode="self", timing="instant")

    def can_use(self, battle: Battle, actor: Unit,
                payload: dict[str, Any] | None = None) -> tuple[bool, str]:
        ok, reason = super().can_use(battle, actor, payload)
        if not ok:
            return ok, reason
        nest = owned_nest(battle, actor)
        if nest is None or nest.position is None or actor.position is None:
            return False, "本人召唤的吸血鬼巢穴不在场。"
        if actor.position == nest.position and battle.mounted_unit_for(actor) is nest:
            return False, "已经乘骑在自己的巢穴上。"
        if actor.cannot_move or not battle.can_place_unit(actor, nest.position, ignore=actor, mover=actor):
            return False, "当前不能合法回到自己的巢穴。"
        return True, ""

    def execute(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> None:
        nest = owned_nest(battle, actor)
        if nest is None or nest.position is None:
            raise ActionError("本人巢穴不在场。")
        battle.move_unit(actor, nest.position, via_skill=True, allow_anywhere=True,
                         max_distance=max(battle.width, battle.height), tags={self.code})
        if _present(actor) and _present(nest):
            battle.set_mounted_state(actor, nest)
            battle.log_public_event(f"{actor.name} 瞬移回自己的吸血鬼巢穴。", source=actor, target=nest)

    def preview(self, battle: Battle, actor: Unit) -> dict[str, Any]:
        nest = owned_nest(battle, actor)
        return {"cells": [nest.position.to_dict()] if nest and nest.position else [],
                "target_unit_ids": [actor.unit_id] if nest else [],
                "secondary_cells": [], "requires_target": False}

    def ai_requires_enemy_impact(self, battle: Battle, actor: Unit,
                                 payload: dict[str, Any]) -> bool:
        return False

    def ai_payload_candidates(self, battle: Battle, actor: Unit,
                              action: dict[str, Any]) -> list[dict[str, Any]]:
        return ([{"type": "skill", "unit_id": actor.unit_id, "skill_code": self.code}]
                if self.ai_payload_value(battle, actor, {}) > 0 else [])

    def ai_payload_value(self, battle: Battle, actor: Unit,
                         payload: dict[str, Any]) -> float:
        nest = owned_nest(battle, actor)
        if nest is None or nest.position is None or actor.position is None or not self.can_use(battle, actor, {})[0]:
            return -1000.0
        current_danger = min((battle.distance_between_units(actor, enemy)
                              for enemy in battle.enemy_units(actor.player_id)), default=99)
        nest_danger = min((nest.position.distance_to(cell)
                           for enemy in battle.enemy_units(actor.player_id)
                           for cell in battle.unit_cells(enemy)), default=99)
        return (25.0 if actor.current_hp / max(actor.max_health, 0.01) < 0.5
                and nest_danger > current_danger else -1000.0)

    def ai_priority_score(self, context: AISkillScoreContext) -> float:
        safety_value = self.ai_payload_value(context.battle, context.actor, context.payload)
        if safety_value <= 0:
            return -1000.0
        nest = owned_nest(context.battle, context.actor)
        if nest is None or nest.position is None or context.actor.position is None:
            return -1000.0
        return max(safety_value,
                   context.move_score(nest.position, context.hero_style())
                   - context.move_score(context.actor.position, context.hero_style()) - 12.0)

    def ai_reaction_score(self, context: AIReactionScoreContext) -> float:
        nest = owned_nest(context.battle, context.actor)
        if nest is None or nest.position is None or not self.can_use(context.battle, context.actor, {})[0]:
            return -1000.0
        threat = context.threat(context.actor)
        if threat <= 0 or context.destination_still_targeted(nest.position):
            return -1000.0
        return threat + 20.0


class VampireBasicPierceTrait(Trait):
    def __init__(self) -> None:
        super().__init__("吸血鬼破魔普攻", "普攻破魔，不能被回避。")

    def basic_attack_payload_metadata(self, battle: Battle, actor: Unit,
                                      payload: dict[str, Any] | None = None) -> dict[str, Any]:
        return {"ignore_shield": True, "cannot_evade": True}

    def on_targeted(self, battle: Battle, ctx: Any) -> None:
        if ctx.actor is self.owner and not ctx.is_skill and "attack" in ctx.tags:
            ctx.ignore_shield = True
            ctx.cannot_evade = True

    def on_before_damage(self, battle: Battle, ctx: DamageContext) -> None:
        if ctx.source is self.owner and not ctx.is_skill and "attack" in ctx.tags:
            ctx.ignore_shield = True
            ctx.cannot_evade = True


class VampireCurseStatus(StatusEffect):
    def __init__(self, source_unit_id: str, *, permanent: bool) -> None:
        super().__init__("吸血鬼血咒", "本人轮末血-1/4；前期两次，三点后新层永久可叠。",
                         duration=None)
        self.source_unit_id = source_unit_id
        self.triggers_remaining: int | None = None if permanent else 2

    def on_owner_turn_end(self, battle: Battle) -> None:
        target = self.owner
        if not _present(target):
            return
        source = battle.units.get(self.source_unit_id)
        battle.resolve_damage(DamageContext(
            source=source, target=target, attack_power=0, raw_damage=target.max_health / 4,
            is_skill=False, action_name="吸血鬼血咒", tags={"vampire_curse"},
        ))
        if self.triggers_remaining is not None:
            self.triggers_remaining -= 1
            if self.triggers_remaining <= 0:
                target.remove_status(self, battle)

    def to_public_dict(self, battle: Battle) -> dict[str, Any]:
        data = super().to_public_dict(battle)
        data.update({"source_unit_id": self.source_unit_id,
                     "triggers_remaining": self.triggers_remaining,
                     "permanent": self.triggers_remaining is None})
        return data


def vampire_curses(target: Unit, source_unit_id: str) -> list[VampireCurseStatus]:
    return [status for status in target.statuses
            if isinstance(status, VampireCurseStatus) and status.source_unit_id == source_unit_id]


class VampireCurseTrait(Trait):
    def __init__(self) -> None:
        super().__init__("吸血鬼血咒普攻", "普攻实伤施加轮末失血；3点后新层永久可叠。")

    def on_after_damage(self, battle: Battle, ctx: DamageContext) -> None:
        owner = self.owner
        if (owner is None or ctx.source is not owner or ctx.is_skill or "attack" not in ctx.tags
                or ctx.actual_damage <= 0 or not _present(ctx.target)):
            return
        threshold = next((trait for trait in owner.iter_components()
                          if isinstance(trait, VampireCurseThresholdTrait)), None)
        if threshold is not None and threshold.permanent_curse_ready():
            ctx.target.add_status(VampireCurseStatus(owner.unit_id, permanent=True), source=owner)
        else:
            existing = next((status for status in vampire_curses(ctx.target, owner.unit_id)
                             if status.triggers_remaining is not None), None)
            if existing is not None:
                existing.triggers_remaining = 2
            else:
                ctx.target.add_status(VampireCurseStatus(owner.unit_id, permanent=False), source=owner)
        battle.log_public_event(f"{ctx.target.name} 被 {owner.name} 的普攻附上吸血鬼血咒。",
                                source=owner, target=ctx.target)


class VampirePointTrait(Trait):
    def __init__(self) -> None:
        super().__init__("吸血鬼魔力点", "每次正式普攻获得1魔力点，第三点起新血咒永久且可叠。")

    def on_owner_action_declared(self, battle: Battle, action_type: str,
                                 payload: dict[str, Any]) -> None:
        if action_type == "attack" and _present(self.owner):
            gained = self.owner.gain_mana_points(1)
            if gained:
                battle.log_public_event(f"{self.owner.name} 普攻获得 {gained:g} 魔力点。",
                                        source=self.owner, target=self.owner)


class VampireCurseThresholdTrait(Trait):
    def __init__(self) -> None:
        super().__init__("血咒永久化", "本人已有3魔力点后，新挂血咒没有期限且逐次叠加。")

    def permanent_curse_ready(self) -> bool:
        return self.owner is not None and self.owner.mana_points >= 3


class VampireShadowSkill(RemoteDragonBreathSkill):
    target_cells_are_geometry_only = True
    ai_payload_value_owned = True
    ai_candidate_plan = SkillCandidatePlan(prefilter_limit=24)

    def __init__(self) -> None:
        super().__init__()
        self.code = "vampire_shadow"
        self.name = "吸血鬼之影"
        self.description = "每本人轮一次，范内选4×4，仅攻击本人血咒受体；命中后另排当前血减半。"
        self.mana_cost = 0
        self.max_uses_per_turn = 1

    def patterns(self, battle: Battle, actor: Unit) -> list[list[Position]]:
        return remote_rectangle_patterns(battle, actor, 4, 4)

    def targets(self, battle: Battle, actor: Unit, cells: list[Position]) -> list[Unit]:
        return [target for target in battle.effect_units_at_cells(cells, ignore=actor)
                if target.player_id != actor.player_id and vampire_curses(target, actor.unit_id)]

    def get_target_units_for_payload(self, battle: Battle, actor: Unit,
                                     payload: dict[str, Any]) -> list[Unit]:
        return self.targets(battle, actor, self.chosen_cells(battle, actor, payload))

    def ai_effect_units(self, battle: Battle, actor: Unit,
                        payload: dict[str, Any]) -> list[Unit]:
        return self.get_target_units_for_payload(battle, actor, payload)

    def preview(self, battle: Battle, actor: Unit) -> dict[str, Any]:
        preview = super().preview(battle, actor)
        all_cells = [cell for pattern in self.patterns(battle, actor) for cell in pattern]
        preview["target_unit_ids"] = [target.unit_id for target in self.targets(battle, actor, all_cells)]
        return preview

    def execute(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> None:
        cells = (battle.payload_positions(payload, "declared_area_cells")
                 if "declared_area_cells" in payload else self.chosen_cells(battle, actor, payload))
        for target in self.targets(battle, actor, cells):
            if not _present(actor) or not _present(target):
                continue
            ctx = battle.resolve_damage(DamageContext(
                source=actor, target=target, attack_power=actor.stat("attack"),
                is_skill=True, action_name=self.name,
                area_cell_hits=battle.unit_hit_count_for_cells(target, cells),
                tags={"skill", self.code},
            ))
            if ctx.actual_damage > 0 and _present(target):
                battle.queue_skill_effect_action(
                    actor=actor, display_name="吸血鬼之影半血", effect_code="vampire_shadow_half",
                    target_units=[target], include_cell_units=False,
                    payload={"target_unit_id": target.unit_id},
                    effect_resolver=self.resolve_half_effect,
                )

    def resolve_half_effect(self, battle: Battle, actor: Unit,
                            queued: QueuedAction) -> None:
        target = battle.units.get(str(queued.payload.get("target_unit_id")))
        if not _present(target):
            return
        battle.resolve_damage(DamageContext(
            source=actor, target=target, attack_power=0,
            raw_damage=target.current_hp / 2, is_skill=True,
            action_name="吸血鬼之影半血", tags={"skill", "vampire_shadow_half"},
        ))

    def _area_value(self, battle: Battle, actor: Unit, cells: list[Position]) -> float:
        score = -12.0
        for target in self.targets(battle, actor, cells):
            if target.magic_immunity or target.direct_effects_blocked():
                continue
            damage = 0.0 if target.total_shields() > 0 else min(
                target.current_hp, battle.damage_rule.calculate_damage(
                    actor.stat("attack") + max(0, battle.unit_hit_count_for_cells(target, cells) - 1),
                    target.stat("defense")))
            if damage > 0:
                score += damage * 100.0 + max(0.0, target.current_hp - damage) * 45.0
        return score

    def ai_prefilter_score(self, battle: Battle, actor: Unit,
                           payload: dict[str, Any], shared: Any) -> float:
        return self._area_value(battle, actor, battle.payload_positions(payload, "cells"))

    def ai_payload_candidates(self, battle: Battle, actor: Unit,
                              action: dict[str, Any]) -> list[dict[str, Any]]:
        ranked = sorted(((self._area_value(battle, actor, cells), cells)
                         for cells in self.patterns(battle, actor)),
                        key=lambda item: item[0], reverse=True)
        return [{"type": "skill", "unit_id": actor.unit_id, "skill_code": self.code,
                 "cells": positions_to_dict(cells)}
                for value, cells in ranked[:24] if value > 0]

    def ai_payload_value(self, battle: Battle, actor: Unit,
                         payload: dict[str, Any]) -> float:
        try:
            return self._area_value(battle, actor, self.chosen_cells(battle, actor, payload))
        except (ActionError, TypeError, ValueError, KeyError):
            return -1000.0

    def ai_effective_enemy_impact(self, battle: Battle, actor: Unit, payload: dict[str, Any],
                                  targets: list[Unit] | None = None, shared: Any = None,
                                  action: dict[str, Any] | None = None) -> bool:
        return self.ai_payload_value(battle, actor, payload) > 0

    def ai_priority_score(self, context: AISkillScoreContext) -> float:
        return self.ai_payload_value(context.battle, context.actor, context.payload)
