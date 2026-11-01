"""R056 Remi bat and chaos mechanics."""

from __future__ import annotations
from wujiang.tactical.heroes.ai_impact_rule import R13_IMPACT

from typing import Any
from wujiang.tactical.engine.core import ActionError
from wujiang.tactical.engine.core import ActionMiss
from wujiang.tactical.engine.core import Battle
from wujiang.tactical.engine.core import DamageContext
from wujiang.tactical.engine.core import HeroUnit
from wujiang.tactical.engine.core import Position
from wujiang.tactical.engine.core import Skill
from wujiang.tactical.engine.core import Stats
from wujiang.tactical.engine.core import Trait
from wujiang.tactical.heroes.base import AbstractHero
from wujiang.tactical.heroes.ai_policy import AISkillScoreContext
from wujiang.tactical.heroes.common import FlyingTrait
from wujiang.tactical.heroes.common import payload_position
from wujiang.tactical.heroes.mechanics.next_five_geometry import ALL_DIRECTIONS
from wujiang.tactical.heroes.mechanics.next_five_geometry import square_around_cells


class RemiBatSummon(AbstractHero):
    hero_code = "remi_bat"
    hero_name = "蝙蝠"
    role = "召唤物"
    attribute = ""
    race = ""
    level = 1
    base_stats = Stats(attack=3, defense=1, speed=3, attack_range=1, mana=0)
    raw_skill_text = ""
    raw_trait_text = "飞行"

    def __init__(self, player_id: int) -> None:
        super().__init__(player_id, is_summon=True)

    def build_skills(self) -> list[Skill]:
        return []

    def build_traits(self) -> list[Trait]:
        return [FlyingTrait()]


class RemiChaosSkill(Skill):
    ai_effective_impact_rule = R13_IMPACT
    def ai_priority_score(self, context: AISkillScoreContext) -> float:
        return context.r13_effect_score(self)

    def __init__(self) -> None:
        super().__init__(
            "remi_chaos",
            "混沌",
            "大招：一场战斗一次；移动恰好 3 格后，对周围 8 格按当前攻击造成技能伤害。",
            max_uses_per_battle=1,
            target_mode="cell",
        )

    def can_use(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any] | None = None) -> tuple[bool, str]:
        ok, reason = super().can_use(battle, actor, payload)
        if not ok:
            return ok, reason
        if actor.cannot_move:
            return False, "当前无法移动，不能使用混沌。"
        if payload is not None and payload.get("x") is not None and payload.get("y") is not None:
            try:
                destination = payload_position(payload)
            except (KeyError, TypeError, ValueError):
                return False, "请选择合法的混沌落点。"
            if destination not in battle.reachable_positions(actor, max_distance=3, exact_distance=3):
                return False, "混沌必须移动恰好 3 格到合法落点。"
        return True, ""

    def execute(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> None:
        destination = payload_position(payload)
        path = battle.payload_positions(payload, "path")
        if battle.is_declared_resolution(actor, payload, {"skill"}) and (
            actor.cannot_move or destination not in battle.reachable_positions(actor, max_distance=3, exact_distance=3)
        ):
            raise ActionMiss("【混沌】原定落点已失效，本次位移和环伤落空。")
        battle.move_unit(actor, destination, via_skill=True, max_distance=3, exact_distance=3, path=path or None, tags={"remi_chaos"})
        cells = square_around_cells(battle, battle.unit_cells(actor), radius=1)
        for target in battle.units_at_cells(cells):
            if target.unit_id == actor.unit_id:
                continue
            battle.resolve_damage(
                DamageContext(
                    source=actor,
                    target=target,
                    attack_power=actor.stat("attack"),
                    is_skill=True,
                    action_name="混沌",
                    area_cell_hits=battle.unit_hit_count_for_cells(target, cells),
                    tags={"skill", "remi_chaos"},
                )
            )

    def preview(self, battle: Battle, actor: HeroUnit) -> dict[str, Any]:
        cells = battle.reachable_positions(actor, max_distance=3, exact_distance=3)
        secondary: list[dict[str, int]] = []
        seen: set[tuple[int, int]] = set()
        for destination in cells:
            for cell in square_around_cells(battle, actor.footprint_cells_at(destination), radius=1):
                key = (cell.x, cell.y)
                if key in seen:
                    continue
                seen.add(key)
                secondary.append(cell.to_dict())
        return {"cells": [cell.to_dict() for cell in cells], "target_unit_ids": [], "secondary_cells": secondary, "requires_target": True}

    def get_target_cells_for_payload(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> list[Position]:
        destination = payload_position(payload)
        return square_around_cells(battle, actor.footprint_cells_at(destination), radius=1)

    def get_target_units_for_payload(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> list[HeroUnit]:
        cells = self.get_target_cells_for_payload(battle, actor, payload)
        return [unit for unit in battle.units_at_cells(cells) if unit.unit_id != actor.unit_id]  # type: ignore[list-item]


class RemiBatSkill(Skill):
    def ai_priority_score(self, context: AISkillScoreContext) -> float:
        return context.r13_bat_score(self)

    def __init__(self) -> None:
        super().__init__(
            "summon_remi_bat",
            "蝙蝠",
            "普通技能：每回合一次；在周围合法格召唤一只蝙蝠（攻3守1速3范1，飞行），召唤回合可以行动。",
            max_uses_per_turn=1,
            target_mode="cell",
        )

    def legal_destinations(self, battle: Battle, actor: HeroUnit) -> list[Position]:
        probe = RemiBatSummon(actor.player_id)
        result: list[Position] = []
        for cell in square_around_cells(battle, battle.unit_cells(actor), radius=1):
            if any(cell == occupied for occupied in battle.unit_cells(actor)):
                continue
            if battle.can_place_unit(probe, cell):
                result.append(cell)
        return result

    def execute(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> None:
        destination = payload_position(payload)
        if destination not in self.legal_destinations(battle, actor):
            raise ActionError("蝙蝠只能召唤在自身周围的合法空格。")
        summon = RemiBatSummon(actor.player_id)
        battle.summon_unit(summon, destination, summoner=actor)
        summon.turn_ready = True
        summon.can_act_on_entry_turn = True

    def preview(self, battle: Battle, actor: HeroUnit) -> dict[str, Any]:
        cells = self.legal_destinations(battle, actor)
        return {"cells": [cell.to_dict() for cell in cells], "target_unit_ids": [], "secondary_cells": [], "requires_target": True}


class RemiUndyingTrait(Trait):
    def __init__(self) -> None:
        super().__init__("蕾米不灭", "血量归 0 时不破坏，而是血变为 1/4、魔 -1；若因此魔变为 0 则破坏。")

    def on_after_damage(self, battle: Battle, ctx: DamageContext) -> None:
        owner = self.owner
        if owner is None or ctx.target.unit_id != owner.unit_id or owner.alive or owner.current_hp > 0:
            return
        if owner.current_mana <= 0:
            return
        paying = getattr(owner, "_paying_skill_cost", False)
        owner._paying_skill_cost = True
        try:
            spent = owner.spend_mana(1)
        finally:
            owner._paying_skill_cost = paying
        if spent < 1 or owner.current_mana <= 0:
            battle.log(f"{owner.name} 的【蕾米不灭】扣除了 {spent} 点魔，魔为 0，仍被破坏。")
            return
        owner.current_hp = 0.25
        owner.alive = True
        battle.log(f"{owner.name} 的【蕾米不灭】使其保留 1/4 生命，并扣除了 {spent} 点魔。")


def _payload_direction(payload: dict[str, Any]) -> tuple[int, int]:
    direction = payload.get("direction")
    if isinstance(direction, dict):
        dx = int(direction.get("dx", 0))
        dy = int(direction.get("dy", 0))
    else:
        dx = int(payload.get("dx", 0))
        dy = int(payload.get("dy", 0))
    if (dx, dy) not in ALL_DIRECTIONS:
        raise ActionError("需要选择一个合法方向。")
    return dx, dy


