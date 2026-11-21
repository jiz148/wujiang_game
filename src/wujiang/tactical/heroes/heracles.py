"""R038 Heracles: a four-cell body, counter-chain fissure, and eleven trials."""

from __future__ import annotations

from typing import Any

from wujiang.tactical.engine.core import ActionError, Battle, DamageContext, HeroUnit, Position, QueuedAction, Skill, StatusEffect, Trait, Unit, direct_scoring
from wujiang.tactical.heroes.ai_candidate_plan import SkillCandidatePlan
from wujiang.tactical.heroes.common import DashMoveSkill, match_payload_pattern, payload_position, positions_to_dict
from wujiang.tactical.heroes.mechanics.area_helpers import area_patterns_preview, combined_remote_rectangle_patterns, resolve_area_damage


_STATS = ("attack", "defense", "speed", "attack_range", "mana")
_LABELS = {"attack": "攻", "defense": "守", "speed": "速", "attack_range": "范", "mana": "魔"}


class EarthFissureSlow(StatusEffect):
    def __init__(self) -> None:
        super().__init__("地裂迟缓", "速-2，最低1，至下次本人轮末。")
        self.armed = False

    def modify_stat(self, stat_name: str, value: float) -> float:
        return max(1.0, value - 2.0) if stat_name == "speed" else value

    def on_owner_turn_start(self, battle: Battle) -> None:
        self.armed = True

    def on_owner_turn_end(self, battle: Battle) -> None:
        if self.armed and self.owner is not None:
            self.owner.remove_status(self, battle)


class HeraclesAreaSkill(Skill):
    ai_candidate_plan = SkillCandidatePlan(prefilter_limit=16)
    ai_payload_value_owned = True
    sizes: tuple[tuple[int, int], ...] = ()

    def patterns(self, battle: Battle, actor: HeroUnit) -> list[list[Position]]:
        return combined_remote_rectangle_patterns(battle, actor, list(self.sizes))

    def chosen_cells(self, battle: Battle, actor: HeroUnit,
                     payload: dict[str, Any]) -> list[Position]:
        return match_payload_pattern(payload, self.patterns(battle, actor))

    def preview(self, battle: Battle, actor: HeroUnit) -> dict[str, Any]:
        return area_patterns_preview(battle, actor, self.patterns(battle, actor))

    def get_target_cells_for_payload(self, battle: Battle, actor: HeroUnit,
                                     payload: dict[str, Any]) -> list[Position]:
        return self.chosen_cells(battle, actor, payload)

    def get_target_units_for_payload(self, battle: Battle, actor: HeroUnit,
                                     payload: dict[str, Any]) -> list[Unit]:
        return list(battle.effect_units_at_cells(self.chosen_cells(battle, actor, payload)))

    def ignores_magic_immunity_for_payload(self, battle: Battle, actor: Unit,
                                           payload: dict[str, Any]) -> bool:
        return True

    def ai_payload_candidates(self, battle: Battle, actor: HeroUnit,
                              action: dict[str, Any]) -> list[dict[str, Any]]:
        return [{"type": "skill", "unit_id": actor.unit_id, "skill_code": self.code,
                 "cells": positions_to_dict(cells)} for cells in self.patterns(battle, actor)]

    def ai_candidate_payloads(self, context: Any) -> list[dict[str, Any]]:
        return self.ai_payload_candidates(context.battle, context.actor, context.action)

    def ai_prefilter_score(self, battle: Battle, actor: Unit, payload: dict[str, Any],
                           shared: Any) -> float:
        cells = [Position(cell["x"], cell["y"]) for cell in payload.get("cells", [])]
        score = 0.0
        for unit in battle.effect_units_at_cells(cells):
            hits = battle.unit_hit_count_for_cells(unit, cells)
            damage = max(0.0, actor.stat("attack") + hits - 1 - unit.stat("defense"))
            value = 22.0 + 45.0 * damage
            score += value if unit.player_id != actor.player_id else -value * 1.5
        return score

    def ai_effective_enemy_impact(self, battle: Battle, actor: Unit,
                                  payload: dict[str, Any], targets: list[Unit] | None = None,
                                  shared: Any = None, action: dict[str, Any] | None = None) -> bool:
        return any(unit.player_id != actor.player_id for unit in self.get_target_units_for_payload(battle, actor, payload))

    def ai_payload_value(self, battle: Battle, actor: HeroUnit,
                         payload: dict[str, Any]) -> float:
        try:
            cells = self.chosen_cells(battle, actor, payload)
        except ActionError:
            return -1000.0
        score = 0.0
        for unit in battle.effect_units_at_cells(cells):
            damage = max(0.0, actor.stat("attack") + battle.unit_hit_count_for_cells(unit, cells) - 1
                         - unit.stat("defense"))
            value = 20.0 + damage * 55.0
            score += value if unit.player_id != actor.player_id else -value * 1.5
        return score - 10.0

    def ai_priority_score(self, context: Any) -> float:
        return self.ai_payload_value(context.battle, context.actor, context.payload)

    def ai_candidate_filtered_reason(self, battle: Battle, actor: Unit,
                                     payloads: list[dict[str, Any]]) -> str | None:
        if payloads and all(self.ai_payload_value(battle, actor, payload) <= 0 for payload in payloads):
            return "no_positive_net_area_impact"
        return None

    def execute(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> None:
        cells = self.chosen_cells(battle, actor, payload)
        resolve_area_damage(battle, actor, action_name=self.name, cells=cells,
                            attack_power=actor.stat("attack"),
                            ignore_magic_immunity=True, tags={"skill", self.code})


class EarthFissureSkill(HeraclesAreaSkill):
    sizes = ((2, 6), (6, 2))

    def __init__(self) -> None:
        super().__init__("earth_fissure", "地裂", "费1.5魔；2×6伤害无视魔免；连锁者速-2至其下次本人轮末。",
                         mana_cost=1.5, target_mode="cell")

    def on_action_chained(self, battle: Battle, declared_action: QueuedAction,
                          reaction: QueuedAction) -> None:
        if (self.owner is None or declared_action.actor_id != self.owner.unit_id
                or declared_action.payload.get("skill_code") != self.code):
            return
        chainer = battle.units.get(reaction.actor_id)
        if chainer is None or not chainer.alive or chainer.banished:
            return
        chainer.add_status(EarthFissureSlow(), source=self.owner)
        battle.log_public_event(f"{chainer.name} 连锁地裂，速-2至下次本人轮末。",
                                source=self.owner, target=chainer)


class WrathSlashSkill(HeraclesAreaSkill):
    sizes = ((3, 4), (4, 3))

    def __init__(self) -> None:
        super().__init__("wrath_slash", "怒斩", "本场一次；3×4范围伤害无视魔免，普攻重置。",
                         max_uses_per_battle=1, target_mode="cell")


class HeavyChargeSkill(DashMoveSkill):
    ai_payload_value_owned = True
    target_cells_are_geometry_only = True

    def __init__(self) -> None:
        super().__init__("heavy_charge", "重磅冲撞", "每本人轮一次；直线尽量移动2格，周围受当前守值伤害，无视魔免。",
                         max_distance=2, mana_cost=0, max_uses_per_turn=1, straight_only=True)

    @staticmethod
    def landing_ring(battle: Battle, actor: Unit, destination: Position) -> list[Position]:
        footprint = set(actor.footprint_cells_at(destination))
        return sorted({cell for origin in footprint for cell in battle.neighbors(origin)
                       if cell not in footprint}, key=lambda cell: (cell.y, cell.x))

    def get_target_cells_for_payload(self, battle: Battle, actor: Unit,
                                     payload: dict[str, Any]) -> list[Position]:
        landing = payload_position(payload)
        return self.landing_ring(battle, actor, landing)

    def get_target_units_for_payload(self, battle: Battle, actor: Unit,
                                     payload: dict[str, Any]) -> list[Unit]:
        # The area is declared by the follow-up after the movement actually lands.
        return []

    def ignores_magic_immunity_for_payload(self, battle: Battle, actor: Unit,
                                           payload: dict[str, Any]) -> bool:
        return True

    def preview(self, battle: Battle, actor: HeroUnit) -> dict[str, Any]:
        result = super().preview(battle, actor)
        cells = [Position(cell["x"], cell["y"]) for cell in result["cells"]]
        result["secondary_cells"] = positions_to_dict([cell for landing in cells
                                                       for cell in self.landing_ring(battle, actor, landing)])
        return result

    def ai_payload_candidates(self, battle: Battle, actor: Unit,
                              action: dict[str, Any]) -> list[dict[str, Any]]:
        return [{"type": "skill", "unit_id": actor.unit_id, "skill_code": self.code,
                 "x": cell["x"], "y": cell["y"]} for cell in self.preview(battle, actor)["cells"]]

    def ai_candidate_payloads(self, context: Any) -> list[dict[str, Any]]:
        return self.ai_payload_candidates(context.battle, context.actor, context.action)

    def ai_effective_enemy_impact(self, battle: Battle, actor: Unit,
                                  payload: dict[str, Any], targets: list[Unit] | None = None,
                                  shared: Any = None, action: dict[str, Any] | None = None) -> bool:
        return any(unit.player_id != actor.player_id for unit in battle.effect_units_at_cells(
            self.get_target_cells_for_payload(battle, actor, payload), ignore=actor))

    def ai_payload_value(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> float:
        try:
            landing = payload_position(payload)
        except (KeyError, TypeError, ValueError):
            return -1000.0
        if landing.to_dict() not in self.preview(battle, actor)["cells"]:
            return -1000.0
        value = 0.0
        for unit in battle.effect_units_at_cells(self.landing_ring(battle, actor, landing), ignore=actor):
            damage = min(unit.current_hp, actor.stat("defense"))
            value += (25.0 + damage * 45.0) * (1.0 if unit.player_id != actor.player_id else -1.5)
        return value - 10.0

    def ai_priority_score(self, context: Any) -> float:
        return self.ai_payload_value(context.battle, context.actor, context.payload)

    def execute(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> None:
        super().execute(battle, actor, payload)
        if not actor.alive or actor.position is None:
            return
        cells = self.landing_ring(battle, actor, actor.position)
        targets = list(battle.effect_units_at_cells(cells, ignore=actor))
        if not targets:
            return
        battle.queue_skill_effect_action(
            actor=actor, display_name="重磅冲撞·震击", effect_code="heavy_charge_impact",
            target_units=targets, target_cells=cells, include_cell_units=False,
            payload={"cells": positions_to_dict(cells), "power": actor.stat("defense"),
                     "ignore_magic_immunity": True},
            effect_resolver=self.resolve_impact,
        )

    @staticmethod
    def resolve_impact(battle: Battle, actor: Unit, queued: QueuedAction) -> None:
        cells = battle.payload_positions(queued.payload, "cells")
        for target in battle.effect_units_at_cells(cells, ignore=actor):
            battle.resolve_damage(DamageContext(
                source=actor, target=target, attack_power=0, raw_damage=float(queued.payload["power"]),
                is_skill=True, action_name="重磅冲撞", ignore_magic_immunity=True,
                area_cell_hits=1, tags={"skill", "heavy_charge"},
            ))


class TwelveTrialsTrait(Trait):
    def __init__(self) -> None:
        super().__init__("十二试炼", "被击破时原地满状态复活，最多11次；每次随机分配3点能力。")
        self.revival_count = 0
        self.original_stats: dict[str, float] = {}

    def bind(self, owner: Unit) -> "TwelveTrialsTrait":
        super().bind(owner)
        self.original_stats = owner.base_stats.to_dict()
        return self

    def on_unit_destroyed(self, battle: Battle, destroyed_unit: Unit) -> None:
        owner = self.owner
        if owner is not destroyed_unit or self.revival_count >= 11 or owner.last_position is None:
            return
        destination = owner.last_position
        if not battle.unit_entry_allowed(owner)[0] or not battle.can_place_unit(
                owner, destination, ignore=owner, mover=owner):
            battle.log_public_event(f"{owner.name} 的十二试炼无法在原地复活。", target=owner)
            return
        self.revival_count += 1
        for status in list(owner.statuses):
            owner.remove_status(status, battle)
        for stat, value in self.original_stats.items():
            setattr(owner.base_stats, stat, value)
        if direct_scoring():
            for stat in _STATS:
                setattr(owner.base_stats, stat, getattr(owner.base_stats, stat) + 0.6)
            gains = "攻守速范魔各+0.6期望"
        else:
            drawn = [_STATS[battle.roll_die(5, action_name="十二试炼") - 1] for _ in range(3)]
            for stat in drawn:
                setattr(owner.base_stats, stat, getattr(owner.base_stats, stat) + 1)
            gains = "、".join(f"{_LABELS[stat]}+1" for stat in drawn)
        for skill in owner.skills:
            if skill.code != "twelve_trials":
                skill.uses_this_turn = 0
                skill.uses_this_battle = 0
                skill.cooldown_remaining = 0
        owner.alive = True
        owner.banished = False
        owner.position = destination
        owner.current_hp = owner.max_health
        owner.current_mana = owner.max_mana()
        owner.turn_ready = False
        owner.move_used = False
        owner.normal_move_actions_used = 0
        owner.normal_move_steps_used = 0
        owner.attacks_used = 0
        owner.performed_active_skill = False
        owner.actions_taken_this_turn = []
        owner.clear_end_of_turn_shields()
        battle.log_public_event(f"{owner.name} 十二试炼第{self.revival_count}次原地满状态复活：{gains}。",
                                target=owner)

    def to_public_dict(self, battle: Battle) -> dict[str, Any]:
        result = super().to_public_dict(battle)
        result["revivals"] = self.revival_count
        result["revivals_remaining"] = 11 - self.revival_count
        return result


class TwelveTrialsSkill(Skill):
    ai_payload_value_owned = True
    ai_enemy_impact_requirement = False

    def __init__(self) -> None:
        super().__init__("twelve_trials", "十二试炼", "本场一次；取得最多11次原地满状态复活。",
                         max_uses_per_battle=1, target_mode="self")

    def preview(self, battle: Battle, actor: Unit) -> dict[str, Any]:
        return {"cells": positions_to_dict(battle.unit_cells(actor)), "target_unit_ids": [actor.unit_id],
                "secondary_cells": [], "requires_target": False}

    def ai_payload_candidates(self, battle: Battle, actor: Unit,
                              action: dict[str, Any]) -> list[dict[str, Any]]:
        return [{"type": "skill", "unit_id": actor.unit_id, "skill_code": self.code}]

    def ai_candidate_payloads(self, context: Any) -> list[dict[str, Any]]:
        return self.ai_payload_candidates(context.battle, context.actor, context.action)

    def ai_payload_value(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> float:
        if any(isinstance(trait, TwelveTrialsTrait) for trait in actor.traits):
            return -1000.0
        enemies = [unit for unit in battle.enemy_units(actor.player_id) if unit.alive and unit.position is not None]
        return 120.0 + 40.0 * (1 - actor.current_hp / max(actor.max_health, 0.01)) if enemies else -10.0

    def ai_priority_score(self, context: Any) -> float:
        return self.ai_payload_value(context.battle, context.actor, context.payload)

    def execute(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> None:
        actor.traits.append(TwelveTrialsTrait().bind(actor))
        battle.log_public_event(f"{actor.name} 开启十二试炼。", source=actor, target=actor)


class HeraclesRangedGuard(Trait):
    def __init__(self) -> None:
        super().__init__("远程伤害减免", "受到距离大于1的伤害时伤害-1。")

    def on_before_damage(self, battle: Battle, ctx: DamageContext) -> None:
        owner = self.owner
        if owner is not ctx.target or ctx.source is None or ctx.source.position is None:
            return
        if battle.distance_between_units(ctx.source, owner) <= 1:
            return
        if ctx.raw_damage is not None:
            ctx.raw_damage = max(0.0, ctx.raw_damage - 1.0)
        else:
            ctx.attack_power = max(0.0, ctx.attack_power - 1.0)


class HeraclesWrathReset(Trait):
    def __init__(self) -> None:
        super().__init__("怒斩再燃", "每次正式普攻完成后重置怒斩。")

    def on_basic_attack_finished(self, battle: Battle, actor: Unit, payload: dict[str, Any],
                                 damage_contexts: list[DamageContext], missed: bool) -> None:
        if actor is not self.owner:
            return
        wrath = actor.get_skill("wrath_slash")
        wrath.uses_this_battle = 0
        wrath.cooldown_remaining = 0
        battle.log_public_event(f"{actor.name} 普攻后重置怒斩。", source=actor)
