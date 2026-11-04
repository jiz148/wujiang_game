"""R324: messenger-crow economy, formations, and their independent effects."""

from __future__ import annotations

from typing import Any, Mapping

from wujiang.tactical.prediction import direct_scoring, expected_numeric, random
from wujiang.tactical.engine.core import (
    ActionError, Battle, BattleFieldEffect, DamageContext, Position, Skill, Stats,
    StatusEffect, Trait, Unit,
)
from wujiang.tactical.heroes.base import AbstractHero
from wujiang.tactical.heroes.ai_policy import AISkillCandidateContext, AISkillScoreContext, HeroAIPolicy
from wujiang.tactical.heroes.common import FlyingTrait, payload_position, payload_target_unit, positions_to_dict
from wujiang.tactical.heroes.hero_kit import HeroKit, register_hero_kit
from wujiang.tactical.heroes.mechanics.next_five_geometry import square_around_cells
from wujiang.tactical.rooms.ai_targets import living_hostile_combatants


FORMATION_RULES = {
    "triangle_crow_array": ("三角飞鸦阵", 3, 15, 15),
    "square_crow_array": ("四方飞鸦阵", 4, 15, 25),
    "hexagram_crow_array": ("六芒星杀阵", 6, 15, 15),
    "eight_dragon_crow_array": ("八龙封印阵", 8, 15, 15),
}


class SuperCrowAttackPolicy(HeroAIPolicy):
    def priority_skill_score(self, battle: Battle, actor: Unit, code: str,
                             payload: dict) -> float | None:
        if code not in FORMATION_RULES:
            return None
        already = sum(item.startswith("skill:") and item.removeprefix("skill:") in FORMATION_RULES
                      for item in actor.actions_taken_this_turn)
        return -1000.0 if already >= 3 else None

    def attack_followup_bonus(self, battle: Battle, actor: Unit, target: Unit,
                              payload: dict, expected_damage: float) -> float:
        if actor.innate_traits_suppressed():
            return 0.0
        capacity = len(available_super_crow_cells(battle, actor))
        return sum(min(face, capacity) for face in range(1, 7)) / 6.0 * 5.0


@expected_numeric(independent=True)
def super_crow_sacrifice_value(battle: Battle, actor: Unit, crow: Unit | None) -> float:
    if crow is None or crow.position is None:
        return 0.0
    enemies = living_hostile_combatants(battle, actor.player_id)
    adjacent = sum(battle.distance_between_units(crow, enemy) == 1 for enemy in enemies if enemy.position is not None)
    nearby = sum(battle.distance_between_units(crow, enemy) <= 2 for enemy in enemies if enemy.position is not None)
    return 7.0 + adjacent * 13.0 + nearby * 3.0 + min(6.0, crow.current_hp * 6.0)


def _adjacent_cells(battle: Battle, unit: Unit) -> list[Position]:
    body = set(battle.unit_cells(unit))
    return sorted((cell for cell in square_around_cells(battle, body, radius=1) if cell not in body),
                  key=lambda cell: (cell.y, cell.x))


class SuperCrowAura(BattleFieldEffect):
    def __init__(self, owner_id: str) -> None:
        super().__init__("超级信鸦威压", "本体或报信乌鸦外邻敌速-1；多只报信乌鸦效果不叠加；禁止本次实付0魔的技能。")
        self.owner_id = owner_id
        self.source_unit_id = owner_id

    def source(self, battle: Battle) -> Unit | None:
        unit = battle.units.get(self.owner_id)
        return (unit if unit is not None and unit.alive and not unit.banished
                and unit.position is not None and not unit.innate_traits_suppressed() else None)

    def covers(self, battle: Battle, unit: Unit) -> bool:
        source = self.source(battle)
        return bool(source is not None and source.player_id != unit.player_id
                    and unit.alive and not unit.banished and unit.position is not None
                    and battle.distance_between_units(source, unit) == 1)

    def affected_cells(self, battle: Battle) -> list[Position]:
        source = self.source(battle)
        return _adjacent_cells(battle, source) if source is not None else []

    def on_after_dead_cleanup(self, battle: Battle) -> None:
        if self.owner_id not in battle.units:
            battle.remove_field_effect(self)

    def modify_unit_stat(self, battle: Battle, unit: Unit, stat_name: str, value: float) -> float:
        if stat_name == "speed" and self.covers(battle, unit):
            source = self.source(battle)
            if source is not None and source.hero_code == "super_messenger_crow":
                first = next((effect for effect in battle.field_effects
                              if isinstance(effect, SuperCrowAura)
                              and (crow := effect.source(battle)) is not None
                              and crow.hero_code == "super_messenger_crow"
                              and effect.covers(battle, unit)), None)
                if first is not self:
                    return value
            return max(1.0, value - 1.0)
        return value

    def blocks_skill_payload(self, battle: Battle, actor: Unit, skill: Skill,
                             payload: dict[str, Any] | None) -> tuple[bool, str]:
        if self.covers(battle, actor) and skill.mana_cost_for_payload(battle, actor, payload) <= 0:
            return True, "超级信鸦威压禁止本次实付0魔的技能。"
        return False, ""


class SuperCrowAuraTrait(Trait):
    def __init__(self) -> None:
        super().__init__("超级信鸦威压", "外邻敌速-1并禁实付0魔技能；多只报信乌鸦效果不叠加。")
        self.field: SuperCrowAura | None = None

    def on_enter_battle(self, battle: Battle) -> None:
        if self.owner is not None:
            self.field = SuperCrowAura(self.owner.unit_id)
            battle.add_field_effect(self.field, source=self.owner)

    def on_owner_removed(self, battle: Battle) -> None:
        if self.field is not None:
            battle.remove_field_effect(self.field)
            self.field = None


class SuperMessengerCrow(AbstractHero):
    hero_code = "super_messenger_crow"
    hero_name = "报信乌鸦"
    role = "召唤物"
    attribute = "风"
    race = "鸟人"
    level = 1
    base_stats = Stats(attack=1, defense=3, speed=5, attack_range=1, mana=0)
    raw_skill_text = ""
    raw_trait_text = "飞行；外邻敌速-1，效果不叠加，不能使用实付0魔技能"

    def __init__(self, player_id: int) -> None:
        super().__init__(player_id, is_summon=True)

    def build_skills(self) -> list[Skill]:
        return []

    def build_traits(self) -> list[Trait]:
        return [FlyingTrait(), SuperCrowAuraTrait()]


def own_super_crows(battle: Battle, actor: Unit) -> list[Unit]:
    return [unit for unit in battle.player_units(actor.player_id)
            if unit.hero_code == "super_messenger_crow" and unit.summoner_id == actor.unit_id
            and unit.alive and not unit.banished and unit.position is not None]


def available_super_crow_cells(battle: Battle, actor: Unit) -> list[Position]:
    probe = SuperMessengerCrow(actor.player_id)
    return [cell for cell in _adjacent_cells(battle, actor) if battle.can_place_unit(probe, cell)]


def _automatic_crow_cells(battle: Battle, actor: Unit, count: int) -> list[Position]:
    """Automatic trait summons spread toward enemy adjacency; never reroll for AI scoring."""
    cells = available_super_crow_cells(battle, actor)
    enemies = [unit for unit in battle.all_units() if unit.player_id != actor.player_id
               and unit.alive and unit.position is not None and not unit.banished]
    cells.sort(key=lambda cell: (
        -sum(1 for enemy in enemies if any(cell.distance_to(body) == 1 for body in battle.unit_cells(enemy))),
        min((cell.distance_to(body) for enemy in enemies for body in battle.unit_cells(enemy)), default=99),
        cell.y, cell.x,
    ))
    return cells[:count]


def queue_crow_replenishment(battle: Battle, actor: Unit, *, trigger: str) -> None:
    if not actor.alive or actor.position is None or actor.banished or actor.innate_traits_suppressed() or direct_scoring():
        return

    def replenish(current: Battle, source: Unit, _queued: Any) -> None:
        if not source.alive or source.position is None or source.banished or source.innate_traits_suppressed():
            return
        rolled = random.randint(1, 6)
        cells = _automatic_crow_cells(current, source, rolled)
        current.log_public_event(f"{source.name} 的{trigger}补鸦掷出 {rolled} 点，实际召唤 {len(cells)} 只。", source=source)
        for cell in cells:
            if current.can_place_unit(SuperMessengerCrow(source.player_id), cell):
                current.summon_unit(SuperMessengerCrow(source.player_id), cell, summoner=source)

    battle.queue_skill_effect_action(actor=actor, display_name="掷骰补鸦", effect_code="super_crow_replenish",
        target_cells=[], target_units=[], include_cell_units=False, hostile=False,
        description="普攻或非手动召鸦主动技能结束后，公开掷d6补召报信乌鸦。", effect_resolver=replenish)


class SuperCrowReplenishmentTrait(Trait):
    def __init__(self) -> None:
        super().__init__("掷骰补鸦", "普攻或非手动召鸦主动技能后公开掷d6并尽量在外邻补鸦。")

    def on_basic_attack_finished(self, battle: Battle, actor: Unit, payload: dict[str, Any],
                                 damage_contexts: list[DamageContext], missed: bool) -> None:
        if actor is self.owner:
            queue_crow_replenishment(battle, actor, trigger="普攻")


class SuperCrowFreeSkillTrait(Trait):
    free_skills_ignore_magic_immunity = True

    def __init__(self) -> None:
        super().__init__("免费技能穿魔吸魔", "本体实付0魔的技能无视魔免，有独立破魔吸魔效果。")


class SuperCrowSummonSkill(Skill):
    tendency_priority_rank = 4
    simple_repeatable_limit = 3

    def ai_priority_score(self, context: AISkillScoreContext) -> float | None:
        if context.actor.hero_code != "excel_r324":
            return None
        battle, actor, payload = context.battle, context.actor, context.payload
        cell = Position(int(payload.get("x", -1)), int(payload.get("y", -1)))
        enemies = list(context.hostile_units())
        if not enemies:
            return -1000.0
        adjacent = [enemy for enemy in enemies if enemy.position is not None and
                    any(cell.distance_to(body) == 1 for body in battle.unit_cells(enemy))]
        near = min((context.distance_to_position(enemy, cell) for enemy in enemies), default=99)
        if near > 4:
            return -1000.0
        setup = 0.0
        for enemy in adjacent:
            existing = sum(crow.hero_code == "super_messenger_crow" and crow.summoner_id == actor.unit_id
                           and crow.alive and crow.position is not None
                           and battle.distance_between_units(crow, enemy) == 1
                           for crow in battle.player_units(actor.player_id))
            setup = max(setup, 22.0 + existing * 13.0 + min(3, existing + 1) * 6.0)
        pressure = len(adjacent) * 16.0 + max(0.0, 4.0 - near) * 6.0
        cost = self.mana_cost_for_payload(battle, actor, payload)
        return setup + pressure - cost * 34.0 - (20.0 if not adjacent else 0.0)

    def __init__(self) -> None:
        super().__init__("super_messenger_crow", "报信乌鸦", "每本人轮前三次免费，之后每次费0.5魔；外邻召一只飞行乌鸦，威压效果不叠加。",
                         mana_cost=0.5, target_mode="cell")

    def mana_cost_for_payload(self, battle: Battle, actor: Unit,
                              payload: dict[str, Any] | None = None) -> float:
        self.sync_turn_scope(battle)
        return 0.0 if self.uses_this_turn < 3 else super().mana_cost_for_payload(battle, actor, payload)

    def mana_cost_text(self) -> str:
        return "本轮前三次0，之后0.5"

    def can_use(self, battle: Battle, actor: Unit, payload: dict[str, Any] | None = None) -> tuple[bool, str]:
        ok, reason = super().can_use(battle, actor, payload)
        if not ok:
            return ok, reason
        cells = available_super_crow_cells(battle, actor)
        if not cells:
            return False, "本体外邻没有合法召鸦空格。"
        if payload and payload.get("x") is not None and payload_position(payload) not in cells:
            return False, "请选择本体外邻合法空格。"
        return True, ""

    def execute(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> None:
        cell = payload_position(payload)
        if cell not in available_super_crow_cells(battle, actor):
            raise ActionError("报信乌鸦的声明格已不合法。")
        battle.summon_unit(SuperMessengerCrow(actor.player_id), cell, summoner=actor)

    def preview(self, battle: Battle, actor: Unit) -> dict[str, Any]:
        return {"cells": positions_to_dict(available_super_crow_cells(battle, actor)),
                "target_unit_ids": [], "secondary_cells": positions_to_dict(battle.unit_cells(actor)),
                "requires_target": True, "free_uses_remaining": max(0, 3 - self.uses_this_turn)}


class CrowFormationStatus(StatusEffect):
    def __init__(self, code: str, duration: int) -> None:
        names = {"triangle_crow_array": "三角飞鸦阵减速禁位移", "square_crow_array": "四方飞鸦阵禁攻施技",
                 "eight_dragon_crow_array": "八龙封印"}
        super().__init__(names[code], names[code], duration=duration, tick_scope="owner_turn_end")
        self.formation_code = code
        self.suppresses_innate_traits = code == "eight_dragon_crow_array"
        self.suppresses_projected_traits = code == "eight_dragon_crow_array"
        self.prevents_movement = code == "eight_dragon_crow_array"

    def modify_stat(self, stat_name: str, value: float) -> float:
        return max(1.0, value - 3.0) if self.formation_code == "triangle_crow_array" and stat_name == "speed" else value

    def bind(self, owner: Unit) -> "CrowFormationStatus":
        super().bind(owner)
        if self.formation_code in {"square_crow_array", "eight_dragon_crow_array"}:
            owner.cannot_attack = True
        if self.formation_code == "eight_dragon_crow_array":
            owner.cannot_use_skills = True
        return self

    def on_removed(self, battle: Battle) -> None:
        owner = self.owner
        if owner is None:
            return
        for flag in ("cannot_attack", "cannot_use_skills"):
            if flag == "cannot_use_skills" and self.formation_code != "eight_dragon_crow_array":
                continue
            still_locked = any(status is not self and (
                flag in getattr(status, "locked_flags", ())
                or flag in getattr(status, "provided_flags", ())
                or (getattr(status, "flag_name", None) == flag and getattr(status, "value", False))
                or (isinstance(status, CrowFormationStatus) and status.formation_code in
                    ({"square_crow_array", "eight_dragon_crow_array"} if flag == "cannot_attack" else {"eight_dragon_crow_array"}))
            ) for status in owner.statuses)
            setattr(owner, flag, still_locked or (flag == "cannot_attack" and owner.is_clone))

    def blocks_skill_use(self, battle: Battle, actor: Unit, skill: Skill) -> tuple[bool, str]:
        if actor is not self.owner:
            return False, ""
        if self.formation_code == "square_crow_array" and skill.timing == "active":
            return True, "四方飞鸦阵使该单位不能使用主动技能。"
        if self.formation_code == "triangle_crow_array":
            from wujiang.tactical.heroes.mechanics.true_blade_bird import skill_has_movement_effect
            if skill_has_movement_effect(skill):
                return True, "三角飞鸦阵使该单位不能使用位移技能。"
        return False, ""


class CrowFormationSkill(Skill):
    tendency_cast_group = frozenset({
        "eight_dragon_crow_array", "square_crow_array",
        "hexagram_crow_array", "triangle_crow_array",
    })
    tendency_cast_limit = 3
    simple_repeatable_limit = 3

    @property
    def tendency_priority_rank(self) -> int:
        return {"eight_dragon_crow_array": 0, "square_crow_array": 1,
                "hexagram_crow_array": 2, "triangle_crow_array": 3}.get(self.code, 5)

    requires_direct_unit_target_line = False

    def ai_priority_score(self, context: AISkillScoreContext) -> float | None:
        if context.actor.hero_code != "excel_r324":
            return None
        battle, actor, payload = context.battle, context.actor, context.payload
        target = battle.units.get(str(payload.get("target_unit_id") or ""))
        if target is None or not target.alive or target.position is None:
            return -1000.0
        count = self.crow_count
        shields = target.total_shields() + target.dodge_charges
        hits = max(0, count - shields)
        per_hit = context.estimate_damage(target, 3, ignore_shield=True)
        life = min(target.current_hp, hits * max(0.0, per_hit))
        control = 0.0
        if self.code == "triangle_crow_array":
            control = max(0.0, min(3.0, target.stat("speed") - 1.0)) * 12.0 + 22.0
        elif self.code == "square_crow_array":
            control = 48.0 + target.stat("attack") * 9.0
        elif self.code == "eight_dragon_crow_array":
            control = 170.0 + target.stat("attack") * 13.0
        if target.skill_non_damage_effects_blocked():
            control = 0.0
        status_names = {"triangle_crow_array": "三角飞鸦阵减速禁位移",
                        "square_crow_array": "四方飞鸦阵禁攻施技",
                        "eight_dragon_crow_array": "八龙封印"}
        if target.has_status(status_names.get(self.code, "")):
            control *= 0.1
        drain = min(1.0, target.current_mana) * 19.0 if target.player_id != actor.player_id else 0.0
        sacrificed = sum(super_crow_sacrifice_value(battle, actor, battle.units.get(str(item)))
                          for item in payload.get("raven_ids", []))
        benefit = life * 108.0 + control + drain
        if life >= target.current_hp - 1e-9 and life > 0:
            benefit += 45.0
        if target.player_id == actor.player_id:
            benefit = -life * 170.0 - control * 1.4
        free_space = len(available_super_crow_cells(battle, actor))
        replenishment = sum(min(face, free_space) for face in range(1, 7)) / 6.0 * 5.0
        if life <= 0 and control <= 0 and drain <= 0:
            return -1000.0
        return benefit - sacrificed * 0.9 + replenishment - 12.0

    def ai_candidate_payloads(self, context: AISkillCandidateContext) -> list[dict]:
        preview = context.action.get("preview") or {}
        selection = preview.get("selection") or {}
        required = int(selection.get("count") or 0)
        payloads: list[dict[str, Any]] = []
        for target_id, raven_ids in (preview.get("raven_ids_by_target") or {}).items():
            target = context.battle.units.get(str(target_id))
            if target is None or target.position is None or len(raven_ids) < required:
                continue
            ordered_ravens = sorted((str(item) for item in raven_ids),
                key=lambda item: super_crow_sacrifice_value(context.battle, context.actor,
                                                            context.battle.units.get(item)))
            subsets = [ordered_ravens[:required]]
            # A bounded alternative keeps construction linear on large crow bodies.
            for replacement in ordered_ravens[required:required + 2]:
                subsets.append([*ordered_ravens[:required - 1], replacement])
            for ids in subsets:
                payloads.append({**context.base_payload, "target_unit_id": str(target_id),
                                 "x": target.position.x, "y": target.position.y,
                                 "raven_ids": list(ids)})
        return payloads

    def directly_damages_on_use(self) -> bool:
        return True

    def __init__(self, code: str) -> None:
        name, count, width, height = FORMATION_RULES[code]
        super().__init__(code, name, f"牺牲目标外邻{count}只本将报信乌鸦，对目标逐段造成{count}次普通威力3伤害；后续破魔附效可连锁。",
                         target_mode="unit")
        self.crow_count = count
        self.width = width
        self.height = height

    def _in_area(self, battle: Battle, actor: Unit, target: Unit) -> bool:
        if actor.position is None or target.position is None:
            return False
        return any((abs(a.x - b.x) <= (self.width - 1) // 2 and abs(a.y - b.y) <= (self.height - 1) // 2)
                   or (abs(a.x - b.x) <= (self.height - 1) // 2 and abs(a.y - b.y) <= (self.width - 1) // 2)
                   for a in battle.unit_cells(actor) for b in battle.unit_cells(target))

    def adjacent_crows(self, battle: Battle, actor: Unit, target: Unit) -> list[Unit]:
        return sorted((crow for crow in own_super_crows(battle, actor)
                       if battle.distance_between_units(crow, target) == 1), key=lambda crow: crow.unit_id)

    def eligible_targets(self, battle: Battle, actor: Unit) -> list[Unit]:
        return [unit for unit in battle.all_units() if unit.alive and not unit.banished
                and battle.unit_can_be_selected(unit, actor=actor)[0] and self._in_area(battle, actor, unit)
                and len(self.adjacent_crows(battle, actor, unit)) >= self.crow_count]

    def _selection(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> tuple[Unit, list[Unit]]:
        target = payload_target_unit(battle, payload)
        if target not in self.eligible_targets(battle, actor):
            raise ActionError(f"{self.name}须在所示范围内选择外邻至少{self.crow_count}只己方报信乌鸦的单位。")
        candidates = {crow.unit_id: crow for crow in self.adjacent_crows(battle, actor, target)}
        ids = [str(item) for item in payload.get("raven_ids", [])]
        if not ids and len(candidates) == self.crow_count:
            ids = list(candidates)
        if len(ids) != self.crow_count or len(set(ids)) != len(ids) or any(item not in candidates for item in ids):
            raise ActionError(f"须指定目标外邻恰{self.crow_count}只本将报信乌鸦作为阵法代价。")
        return target, [candidates[item] for item in ids]

    def can_use(self, battle: Battle, actor: Unit, payload: dict[str, Any] | None = None) -> tuple[bool, str]:
        ok, reason = super().can_use(battle, actor, payload)
        if not ok:
            return ok, reason
        if not self.eligible_targets(battle, actor):
            return False, f"没有外邻至少{self.crow_count}只己方报信乌鸦的合法目标。"
        if payload and payload.get("target_unit_id"):
            try:
                self._selection(battle, actor, payload)
            except ActionError as exc:
                return False, str(exc)
        return True, ""

    def ignores_magic_immunity_for_payload(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> bool:
        return super().ignores_magic_immunity_for_payload(battle, actor, payload)

    def queued_payload_metadata(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> dict[str, Any]:
        # Advertise only the later piercing effect; AI damage probes must still see ordinary damage.
        return {"piercing_followup": True}

    def execute(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> None:
        target, chosen = self._selection(battle, actor, payload)
        declared_cell = Position(int(payload.get("x", target.position.x)), int(payload.get("y", target.position.y)))
        if declared_cell not in battle.unit_cells(target):
            declared_cell = battle.unit_cells(target)[0]
        for crow in chosen:
            crow.alive = False
        battle.log_public_event(f"{actor.name} 以{len(chosen)}只报信乌鸦发动【{self.name}】；牺牲：{', '.join(crow.unit_id for crow in chosen)}。",
                                source=actor, target=target)
        battle.cleanup_dead_units()
        target_id = target.unit_id
        pierce_immunity = self.ignores_magic_immunity_for_payload(battle, actor, payload)

        def current_target(current: Battle) -> Unit | None:
            unit = current.units.get(target_id)
            return unit if unit is not None and unit.alive and not unit.banished and declared_cell in current.unit_cells(unit) else None

        def damage(current: Battle, source: Unit, _queued: Any) -> None:
            unit = current_target(current)
            if unit is not None:
                current.resolve_damage(DamageContext(source=source, target=unit, attack_power=3,
                    is_skill=True, action_name=self.name, ignore_magic_immunity=pierce_immunity,
                    tags={"skill", self.code, "crow_formation"}))

        def effect(current: Battle, source: Unit, _queued: Any) -> None:
            unit = current_target(current)
            if unit is None:
                return
            if self.code != "hexagram_crow_array":
                result = current.validate_target(source, unit, action_name=self.name, is_skill=True,
                    is_hostile=unit.player_id != source.player_id, ignore_shield=True,
                    ignore_magic_immunity=pierce_immunity, resolve_defenses=False,
                    record_effect_on_validation=False, tags={"skill", self.code, "control"})
                unit = result.target
                if not result.cancelled and not unit.skill_non_damage_effects_blocked():
                    if unit.player_id != source.player_id and unit.total_shields() > 0:
                        unit.consume_one_shield()
                    if unit.player_id != source.player_id and unit.dodge_charges > 0:
                        unit.dodge_charges -= 1
                    else:
                        if current.destroy_clone_for_skill_effect(unit, source=source, action_name=self.name):
                            return
                        turns = 4 if self.code == "eight_dragon_crow_array" else 1
                        if current.unit_belongs_to_current_turn(unit):
                            turns += 1
                        unit.add_status(CrowFormationStatus(self.code, turns), source=source)
                        current.record_skill_effect(unit)
                        current.log_public_event(f"{unit.name} 受到【{self.name}】破魔控制，持续{turns}个本人轮末。",
                                                 source=source, target=unit)
            if pierce_immunity and unit.player_id != source.player_id and unit.alive:
                if unit.total_shields() > 0:
                    unit.consume_one_shield()
                drained = unit.drain_mana(1)
                if drained > 0:
                    source.gain_mana(drained)
                    current.log_public_event(f"{source.name} 破魔吸取 {unit.name} {drained:g} 魔。", source=source, target=unit)

        for index in range(self.crow_count):
            battle.queue_skill_effect_action(actor=actor, display_name=f"{self.name}第{index+1}段",
                effect_code="crow_formation_damage", target_units=[target], target_cells=[declared_cell],
                payload={"ignore_magic_immunity": pierce_immunity, "target_must_occupy_declared_cell": True},
                include_cell_units=False, effect_resolver=damage, segment_index=index+1,
                segment_count=self.crow_count+1, description="固定威力3；本段伤害不破魔。")
        battle.queue_skill_effect_action(actor=actor, display_name=f"{self.name}附效",
            effect_code="crow_formation_control", target_units=[target], target_cells=[declared_cell],
            payload={"ignore_shield": True, "ignore_magic_immunity": pierce_immunity,
                     "target_must_occupy_declared_cell": True},
            include_cell_units=False, effect_resolver=effect, segment_index=self.crow_count+1,
            segment_count=self.crow_count+1, description="控制与吸魔独立破魔；伤害不破魔。")
        queue_crow_replenishment(battle, actor, trigger=self.name)

    def preview(self, battle: Battle, actor: Unit) -> dict[str, Any]:
        targets = self.eligible_targets(battle, actor)
        return {"cells": positions_to_dict([cell for target in targets for cell in battle.unit_cells(target)]),
                "target_unit_ids": [target.unit_id for target in targets], "requires_target": True,
                "raven_ids_by_target": {target.unit_id: [crow.unit_id for crow in self.adjacent_crows(battle, actor, target)]
                                        for target in targets},
                "raven_count": self.crow_count, "damage_segments": self.crow_count,
                "selection": {"mode": "crow_formation", "count": self.crow_count,
                              "target_prompt": "先选阵法目标，再选恰好需要的相邻报信乌鸦。"}}


def formation_skill_for_name(name: str) -> CrowFormationSkill | None:
    return next((CrowFormationSkill(code) for code, (label, _, _, _) in FORMATION_RULES.items()
                 if label == name), None)


class SuperCrowKit(HeroKit):
    """Own R324's crow and formation assembly."""

    code = "excel_r324"
    ai_policy = SuperCrowAttackPolicy()
    _aura_fragment = "此单位周围的对方单位速-1，不能使用不费魔的技能"
    _free_skill_fragment = "所有不费魔的技能无视魔免，带有破魔的吸魔效果"
    _replenishment_fragment = "攻击或使用除“报信乌鸦”以外的主动技能后在周围召唤投骰子数量的“报信乌鸦”"

    def special_skill(self, fragment: Mapping[str, Any]) -> Skill | None:
        name = fragment.get("name")
        if name == "报信乌鸦":
            return SuperCrowSummonSkill()
        return formation_skill_for_name(str(name))

    def special_trait(self, fragment: Mapping[str, Any]) -> Trait | None:
        source = fragment.get("fragment")
        if source == self._aura_fragment:
            return SuperCrowAuraTrait()
        if source == self._free_skill_fragment:
            return SuperCrowFreeSkillTrait()
        if source == self._replenishment_fragment:
            return SuperCrowReplenishmentTrait()
        return None


SUPER_CROW_KIT = register_hero_kit(SuperCrowKit())
