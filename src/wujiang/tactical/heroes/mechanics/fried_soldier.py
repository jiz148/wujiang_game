"""R024 Fried's inspiring aura, allied healing, and royal soldier."""

from __future__ import annotations

from wujiang.tactical.heroes.ai_candidate_plan import RANKED_CANDIDATES

import re
from typing import Any

from wujiang.tactical.engine.core import (
    ActionError, ActionMiss, Battle, DamageContext, HealContext, HeroUnit,
    Position, Skill, Stats, StatusEffect, Trait,
)
from wujiang.tactical.heroes.base import AbstractHero
from wujiang.tactical.heroes.ai_policy import AISkillCandidateContext, AISkillScoreContext
from wujiang.tactical.heroes.common import (
    ensure_ally, payload_position, payload_target_unit, positions_to_dict,
)
from wujiang.tactical.heroes.mechanics.next_five_geometry import square_around_cells

class FriedInspireStatus(StatusEffect):
    def __init__(self) -> None:
        super().__init__("鼓舞", "本回合移动次数 +1，速度 *2。", duration=1, tick_scope="any_turn_end")

    def modify_stat(self, stat_name: str, value: float) -> float:
        return value * 2 if stat_name == "speed" else value

    def modify_normal_move_actions_per_turn(self, value: int) -> int:
        return value + 1


class FriedInspireSkill(Skill):
    def ai_priority_score(self, context: AISkillScoreContext) -> float:
        return context.owned_effect_score(self)

    def ai_effect_score(self, context: AISkillScoreContext) -> float:
        battle, actor, payload = context.battle, context.actor, context.payload
        try:
            target = self.validate_target(battle, actor, payload)
        except ActionError:
            return -1000.0
        if target.has_status("鼓舞") or not target.can_take_turn_actions(battle) or target.cannot_move or target.cannot_normal_move:
            return -1000.0
        before_cells = context.reachable_positions(target)
        before_value = context.best_move_score(target, before_cells)
        with context.probe_rollback():
            target.add_status(FriedInspireStatus(), source=actor)
            after_cells = context.reachable_positions(target)
            gain = context.best_move_score(target, after_cells) - before_value
        return gain + 10.0 - self.mana_cost_for_payload(battle, actor, payload) * 12.0 if gain > 1 else -1000.0

    def __init__(self) -> None:
        super().__init__("fried_inspire", "鼓舞", "普通技能：周围3*3己方单位本回合移动次数+1，速度*2，同名不叠加；普通不限次，另有每回合一次免费使用。", target_mode="ally")

    def direct_unit_target_range(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any] | None = None) -> int:
        return 1

    def free_available(self, battle: Battle) -> bool:
        return getattr(self, "_free_turn_number", None) != battle.turn_number

    def mana_cost_for_payload(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any] | None = None) -> float:
        return 0.0 if self.free_available(battle) else super().mana_cost_for_payload(battle, actor, payload)

    def validate_target(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> HeroUnit:
        target = payload_target_unit(battle, payload)
        ensure_ally(actor, target)
        battle.require_selectable_unit(target, actor=actor, action_name=self.name)
        battle.require_unit_target_in_range_and_line(actor, target, 1, action_name=self.name)
        return target

    def can_use(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any] | None = None) -> tuple[bool, str]:
        ok, reason = super().can_use(battle, actor, payload)
        if not ok:
            return ok, reason
        if payload and payload.get("target_unit_id"):
            try:
                target = self.validate_target(battle, actor, payload)
            except ActionError as exc:
                return False, str(exc)
            if target.has_status("鼓舞"):
                return False, f"{target.name} 本回合已受鼓舞。"
        return True, ""

    def preview(self, battle: Battle, actor: HeroUnit) -> dict[str, Any]:
        targets = [unit for unit in battle.player_units(actor.player_id)
                   if not unit.has_status("鼓舞") and battle.unit_can_be_selected(unit, actor=actor)[0]
                   and battle.unit_target_in_range_and_line(actor, unit, 1)]
        return {"cells": positions_to_dict([cell for target in targets for cell in battle.unit_cells(target)]),
                "target_unit_ids": [target.unit_id for target in targets], "requires_target": True,
                "free_use_available": self.free_available(battle)}

    def prepay_resources(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> None:
        self.validate_target(battle, actor, payload)
        self.sync_turn_scope(battle)
        if self.free_available(battle):
            self._free_turn_number = battle.turn_number
            self.uses_this_battle += 1
        else:
            super().prepay_resources(battle, actor, payload)

    def execute(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> None:
        target = self.validate_target(battle, actor, payload)
        if target.has_status("鼓舞"):
            battle.log(f"{target.name} 本回合已受鼓舞，同名效果不叠加。")
            return
        target.add_status(FriedInspireStatus(), source=actor)
        battle.log(f"{target.name} 受到【鼓舞】，本回合速度翻倍且移动次数 +1。")


class RoyalSoldierAuraBorrowTrait(Trait):
    """State-free projected traits shared by every allied non-hero in the aura."""
    def __init__(self, summoner_id: str | None = None) -> None:
        super().__init__("弗里德共享", "周围统帅共享：攻击己方加血、格挡反击、自然回魔与当前正数能力加成；多源不叠加。")

    def sources(self) -> list[HeroUnit]:
        owner = self.owner
        ref = getattr(owner, "_battle_ref", None)
        battle = ref() if ref else None
        if battle is None or owner is None:
            return []
        return [unit for unit in battle.all_units()
                if any(isinstance(trait, FriedAuraTrait) and trait.covers(battle, owner) for trait in unit.traits)]

    def grants_block_counter(self, battle: Battle, unit: HeroUnit) -> bool:
        return bool(self.sources())

    def allied_basic_heal_amount(self, battle: Battle, actor: HeroUnit, target: HeroUnit) -> float:
        return 0.25 if actor.player_id == target.player_id and self.sources() else 0.0

    def on_owner_turn_start(self, battle: Battle) -> None:
        owner = self.owner
        if owner is not None and self.sources() and not any(trait.name == "自然回魔" for trait in owner.traits):
            gained = owner.gain_mana(1)
            if gained:
                battle.log(f"{owner.name} 借用弗里德的自然回魔，魔 +{gained}。")

    def modify_stat(self, stat_name: str, value: float) -> float:
        delta = max((max(0.0, source.stat(stat_name) - float(getattr(source.base_stats, stat_name)))
                     for source in self.sources()), default=0.0)
        return value + delta


class RoyalSoldierSummon(AbstractHero):
    hero_code = "royal_soldier"
    hero_name = "皇家士兵"
    role = "召唤物"
    attribute = "土"
    race = "召唤物"
    level = 1
    raw_skill_text = ""
    raw_trait_text = "弗里德召唤物"

    def __init__(self, player_id: int, summoner_id: str, attack: int, defense: int, attack_range: int) -> None:
        self.summoner_id = summoner_id
        self.base_stats = Stats(attack=attack, defense=defense, speed=2, attack_range=attack_range, mana=0)
        super().__init__(player_id, is_summon=True)
        self.summoner_id = summoner_id

    def build_skills(self) -> list[Skill]:
        return []

    def build_traits(self) -> list[Trait]:
        return []


class RoyalSoldierSkill(Skill):
    ai_candidate_plan = RANKED_CANDIDATES
    selection_cells_are_effect_cells = False

    def ai_candidate_payloads(self, context: AISkillCandidateContext) -> list[dict[str, Any]]:
        selection = (context.action.get("preview") or {}).get("selection") or {}
        return [{**context.base_payload, "choice_code": choice["code"], "cells": pattern}
                for choice in selection.get("choices", []) for pattern in choice.get("patterns", [])]

    def ai_priority_score(self, context: AISkillScoreContext) -> float:
        return context.owned_effect_score(self)

    def ai_effect_score(self, context: AISkillScoreContext) -> float:
        battle, actor, payload = context.battle, context.actor, context.payload
        try:
            (attack, defense, reach), destination = self.validate_selection(battle, actor, payload)
        except (ActionError, ValueError, TypeError):
            return -1000.0
        enemies = list(context.hostile_units())
        if not enemies:
            return -1000.0
        with context.probe_rollback():
            probe = RoyalSoldierSummon(actor.player_id, actor.unit_id, attack, defense, reach)
            probe.position, probe._battle_ref = destination, actor._battle_ref
            best = max(context.estimate_damage(enemy, probe.stat("attack")) * 100.0
                       + (24.0 if battle.distance_between_units(probe, enemy) <= probe.targeting_range() + probe.normal_move_distance() else 0.0)
                       - max(0.0, battle.distance_between_units(probe, enemy) - probe.targeting_range() - probe.normal_move_distance()) * 8.0
                       for enemy in enemies)
            danger = max((context.estimate_damage(probe, enemy.stat("attack")) for enemy in enemies
                          if battle.distance_between_units(probe, enemy) <= enemy.targeting_range() + enemy.normal_move_distance()), default=0.0)
            support = 12.0 if battle.unit_can_use_block_counter(probe) else 0.0
            return 30.0 + best + support - danger * 35.0 - self.mana_cost_for_payload(battle, actor, payload) * 12.0

    def __init__(self) -> None:
        super().__init__("royal_soldier", "皇家士兵", "普通技能：每回合2次；攻/守/范分配10点，每项1~5，速2魔0；场上己方同名最多4个。", target_mode="cell", max_uses_per_turn=2)

    def allocation(self, payload: dict[str, Any]) -> tuple[int, int, int]:
        if payload.get("choice_code"):
            raw = str(payload["choice_code"]).split(",")
        else:
            raw = [payload.get("attack", payload.get("soldier_attack")),
                   payload.get("defense", payload.get("soldier_defense")),
                   payload.get("range", payload.get("attack_range", payload.get("soldier_range")))]
        if len(raw) != 3 or any(isinstance(value, bool) or not re.fullmatch(r"[1-5]", str(value)) for value in raw):
            raise ActionError("皇家士兵的攻/守/范必须是1~5的整数。")
        values = tuple(int(value) for value in raw)
        if sum(values) != 10:
            raise ActionError("皇家士兵的攻/守/范总和必须为10。")
        return values

    def destination(self, payload: dict[str, Any]) -> Position:
        if "cells" in payload:
            if not isinstance(payload["cells"], list) or len(payload["cells"]) != 1:
                raise ActionError("皇家士兵需要一个空落点。")
            return payload_position(payload["cells"][0])
        return payload_position(payload)

    def present_count(self, battle: Battle, actor: HeroUnit) -> int:
        return sum(unit.name == RoyalSoldierSummon.hero_name and unit.position is not None and not unit.banished
                   for unit in battle.player_units(actor.player_id))

    def legal_cells(self, battle: Battle, actor: HeroUnit) -> list[Position]:
        if actor.position is None or self.present_count(battle, actor) >= 4:
            return []
        return [Position(x, y) for y in range(battle.height) for x in range(battle.width)
                if battle.unit_distance_to_cell(actor, Position(x, y)) <= actor.targeting_range()
                and (x, y) not in battle.blocked_cells and not battle.units_at(Position(x, y))]

    def validate_selection(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> tuple[tuple[int, int, int], Position]:
        allocation, destination = self.allocation(payload), self.destination(payload)
        if destination not in self.legal_cells(battle, actor):
            raise ActionError("皇家士兵需要范围内空格，且场上己方同名少于4个。")
        return allocation, destination

    def can_use(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any] | None = None) -> tuple[bool, str]:
        ok, reason = super().can_use(battle, actor, payload)
        if not ok:
            return ok, reason
        if self.present_count(battle, actor) >= 4:
            return False, "场上己方皇家士兵已达4个。"
        if payload and any(key in payload for key in ("choice_code", "attack", "soldier_attack", "cells", "x")):
            try:
                self.validate_selection(battle, actor, payload)
            except (ActionError, TypeError, ValueError) as exc:
                return False, str(exc)
        return True, ""

    def prepay_resources(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> None:
        self.validate_selection(battle, actor, payload)
        super().prepay_resources(battle, actor, payload)

    def preview(self, battle: Battle, actor: HeroUnit) -> dict[str, Any]:
        cells = self.legal_cells(battle, actor)
        choices = [{"code": f"{attack},{defense},{attack_range}", "label": f"攻{attack} / 守{defense} / 范{attack_range}",
                    "patterns": [[cell.to_dict()] for cell in cells]}
                   for attack in range(1, 6) for defense in range(1, 6) for attack_range in range(1, 6)
                   if attack + defense + attack_range == 10]
        return {"cells": positions_to_dict(cells), "target_unit_ids": [], "requires_target": True,
                "selection": {"mode": "choice_pattern", "choices": choices, "ordered": False,
                              "choice_prompt": "先分配皇家士兵的10点属性，再选择空落点。",
                              "cell_prompt": "选择范围内一个蓝色空格，再完成召唤。"}}

    def get_target_cells_for_payload(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> list[Position]:
        return []

    def execute(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> None:
        try:
            (attack, defense, attack_range), pos = self.validate_selection(battle, actor, payload)
        except ActionError as exc:
            if payload.get("queued_resolution"):
                raise ActionMiss("皇家士兵原召唤位置或名额已失效，本次召唤落空。") from exc
            raise
        soldier = RoyalSoldierSummon(actor.player_id, actor.unit_id, attack, defense, attack_range)
        soldier.turn_ready = False
        soldier.can_act_on_entry_turn = True  # First future owner-turn refresh enables it.
        battle.add_unit(soldier, pos)


class FriedAllyAttackHealTrait(Trait):
    def __init__(self) -> None:
        super().__init__("攻击己方加血", "可以普攻己方单位；命中己方时不造成伤害，改为治疗 1/4。")

    def allied_basic_heal_amount(self, battle: Battle, actor: HeroUnit, target: HeroUnit) -> float:
        return 0.25 if actor.player_id == target.player_id else 0.0

    def on_before_damage(self, battle: Battle, ctx: DamageContext) -> None:
        owner = self.owner
        if owner is None or ctx.source is None or ctx.source.unit_id != owner.unit_id:
            return
        if ctx.is_skill or "attack" not in ctx.tags or ctx.target.player_id != owner.player_id:
            return
        ctx.cancelled = True
        ctx.reason = f"{owner.name} 攻击己方，改为治疗。"
        battle.heal(HealContext(source=owner, target=ctx.target, amount=0.25, action_name="攻击己方加血"))


class FriedAuraTrait(Trait):
    def __init__(self) -> None:
        super().__init__("弗里德统帅", "周围非武将己方单位动态获得弗里德常驻特性和当前数值加成。")

    def covers(self, battle: Battle, unit: HeroUnit) -> bool:
        owner = self.owner
        if owner is None or owner.is_summon or owner.is_clone or not owner.alive or owner.banished or owner.position is None:
            return False
        if not unit.alive or unit.banished or unit.position is None or not (unit.is_summon or unit.is_clone) or unit.player_id != owner.player_id:
            return False
        body = set(battle.unit_cells_at(owner, getattr(owner, "_resolution_actual_position", owner.position)))
        ring = set(square_around_cells(battle, list(body), radius=1)) - body
        return bool(ring.intersection(battle.unit_cells_at(unit, getattr(unit, "_resolution_actual_position", unit.position))))

    def projected_trait_for(self, battle: Battle, unit: HeroUnit) -> Trait | None:
        if not self.covers(battle, unit):
            return None
        return RoyalSoldierAuraBorrowTrait().bind(unit)


