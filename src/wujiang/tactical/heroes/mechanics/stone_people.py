"""R197 stone summons and response to enemy attacks."""

from __future__ import annotations

from typing import Any

from wujiang.tactical.engine.core import (
    ActionError, Battle, DamageContext, Position, Skill, Stats, Trait, Unit,
)
from wujiang.tactical.heroes.base import AbstractHero
from wujiang.tactical.heroes.ai_policy import AISkillScoreContext, HeroAIPolicy
from wujiang.tactical.heroes.common import payload_position, positions_to_dict
from wujiang.tactical.heroes.mechanics.next_five_geometry import square_around_cells

def stone_summon_anchors(battle: Battle, actor: Unit, stone: Unit) -> list[Position]:
    """Every whole-body placement touching the actor's exterior ring."""
    if actor.position is None:
        return []
    ring = set(square_around_cells(battle, battle.unit_cells(actor), radius=1)) - set(battle.unit_cells(actor))
    anchors: list[Position] = []
    for y in range(battle.height):
        for x in range(battle.width):
            anchor = Position(x, y)
            if battle.can_place_unit(stone, anchor) and any(
                cell in ring for cell in battle.unit_cells_at(stone, anchor)
            ):
                anchors.append(anchor)
    return anchors


class SmallStoneAIPolicy(HeroAIPolicy):
    """Advance a slow summon toward an enemy when it cannot attack yet."""

    def normal_move_candidate_score(self, battle: Battle, actor: Unit,
                                    destination: Position, profile: Any,
                                    score: float, shared: Any) -> float:
        enemies = [unit for unit in battle.enemy_units(actor.player_id)
                   if unit.alive and not unit.banished and unit.position is not None]
        if not enemies or actor.position is None:
            return score
        before = min(battle.distance_between_units(actor, enemy) for enemy in enemies)
        after = min(battle.unit_distance_to_cell(enemy, destination) for enemy in enemies)
        return max(score, 2.0 + (before - after) * 12.0) if after < before else score


class SmallStoneSummon(AbstractHero):
    hero_code = "small_stone"
    hero_name = "小石块人"
    role = "召唤物"
    attribute = "土"
    race = "石人"
    level = 1
    base_stats = Stats(attack=2, defense=4, speed=1, attack_range=1, mana=0)
    ai_policy = SmallStoneAIPolicy()

    def __init__(self, player_id: int) -> None:
        super().__init__(player_id, is_summon=True)

    def build_skills(self) -> list[Skill]:
        return []

    def build_traits(self) -> list[Trait]:
        return []


class StoneOnEnemyAttackTrait(Trait):
    def ai_spawn_penalty(self, battle: Battle, attacker: Unit) -> float:
        owner = self.owner
        if owner is None or not owner.alive:
            return 0.0
        child = self.summon_class(owner.player_id)
        if not stone_summon_anchors(battle, owner, child):
            return 0.0
        likely_damage = battle.damage_rule.calculate_damage(attacker.stat("attack"), owner.stat("defense"))
        if owner.total_shields() == 0 and likely_damage >= owner.current_hp:
            return 0.0
        return 28.0 if self.summon_class is MediumStoneSummon else 14.0

    def __init__(self, summon_class: type[AbstractHero]) -> None:
        super().__init__("石块受击裂变", "敌方普攻实际命中后尽可能在周围召唤下一层石块人。")
        self.summon_class = summon_class

    def on_any_basic_attack_finished(
        self, battle: Battle, actor: Unit, payload: dict[str, Any],
        damage_contexts: list[DamageContext], missed: bool,
    ) -> None:
        owner = self.owner
        if (owner is None or not owner.alive or owner.banished or owner.position is None
                or not actor.alive or actor.banished
                or actor.player_id == owner.player_id or payload.get("basic_attack_cancelled")):
            return
        if not any(ctx.target.unit_id == owner.unit_id and ctx.source is actor
                   and not ctx.is_skill and (not ctx.cancelled or ctx.shield_consumed)
                   for ctx in damage_contexts):
            return
        child = self.summon_class(owner.player_id)
        anchors = stone_summon_anchors(battle, owner, child)
        if not anchors:
            battle.log_public_event(f"{owner.name} 受敌方普攻后无合法空位，未能召唤{child.name}。",
                                    source=actor, target=owner)
            return
        # Passive generation has no player choice; favor space towards the attacker.
        anchors.sort(key=lambda cell: (min(cell.distance_to(hit) for hit in battle.unit_cells(actor))
                                       if actor.position is not None else 999, cell.y, cell.x))
        battle.summon_unit(child, anchors[0], summoner=owner)
        battle.log_public_event(f"{owner.name} 受击裂变出{child.name}。", source=actor, target=child)


class MediumStoneSummon(AbstractHero):
    hero_code = "medium_stone"
    hero_name = "中石块人"
    role = "召唤物"
    attribute = "土"
    race = "石人"
    level = 1
    base_stats = Stats(attack=3, defense=5, speed=1, attack_range=1, mana=0)
    footprint_width = 2
    footprint_height = 2

    def __init__(self, player_id: int) -> None:
        super().__init__(player_id, is_summon=True)

    def build_skills(self) -> list[Skill]:
        return [StoneSummonSkill(small=True)]

    def build_traits(self) -> list[Trait]:
        return [StoneOnEnemyAttackTrait(SmallStoneSummon)]


class StoneSummonSkill(Skill):
    def ai_priority_score(self, context: AISkillScoreContext) -> float:
        return context.stone_summon_score(self.code)

    def __init__(self, *, small: bool = False) -> None:
        self.small = small
        super().__init__("summon_small_stone" if small else "summon_medium_stone",
                         "小石块人" if small else "中石块人",
                         "0魔；在身体周围选择完整合法空位召唤小石块人，每本人轮一次。" if small else
                         "0魔；每2本人轮一次，在身体周围选择完整合法2×2空位召唤中石块人。",
                         max_uses_per_turn=1 if small else None,
                         cooldown_turns=0 if small else 2, target_mode="cell")

    def child(self, actor: Unit) -> Unit:
        return SmallStoneSummon(actor.player_id) if self.small else MediumStoneSummon(actor.player_id)

    def available_cells(self, battle: Battle, actor: Unit) -> list[Position]:
        return stone_summon_anchors(battle, actor, self.child(actor))

    def can_use(self, battle: Battle, actor: Unit, payload: dict[str, Any] | None = None) -> tuple[bool, str]:
        ok, reason = super().can_use(battle, actor, payload)
        if not ok:
            return ok, reason
        available = self.available_cells(battle, actor)
        if not available:
            return False, "周围没有能完整放下石块人的合法空位。"
        if payload is not None and payload.get("x") is not None and payload.get("y") is not None:
            if payload_position(payload) not in available:
                return False, "请选择身体周围的合法石块落点。"
        return True, ""

    def execute(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> None:
        destination = payload_position(payload)
        if destination not in self.available_cells(battle, actor):
            raise ActionError("请选择身体周围的合法石块落点。")
        battle.summon_unit(self.child(actor), destination, summoner=actor)

    def preview(self, battle: Battle, actor: Unit) -> dict[str, Any]:
        anchors = self.available_cells(battle, actor)
        return {"cells": positions_to_dict(anchors), "target_unit_ids": [],
                "secondary_cells": positions_to_dict(battle.unit_cells(actor)),
                "requires_target": True, "placement_size": 1 if self.small else 2}

    def get_target_cells_for_payload(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> list[Position]:
        return [payload_position(payload)]


