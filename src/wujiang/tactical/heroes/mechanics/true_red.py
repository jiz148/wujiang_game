"""True red free movement and blast."""

from __future__ import annotations

from typing import Any
from wujiang.tactical.engine.core import ActionError
from wujiang.tactical.engine.core import Battle
from wujiang.tactical.engine.core import HeroUnit
from wujiang.tactical.engine.core import MoveContext
from wujiang.tactical.engine.core import Position
from wujiang.tactical.engine.core import Trait
from wujiang.tactical.heroes.ai_policy import HeroAIPolicy
from wujiang.tactical.heroes.common import DashMoveSkill
from wujiang.tactical.heroes.common import positions_to_dict
from wujiang.tactical.heroes.mechanics.tianyuan import resolve_true_red_move_blast
from wujiang.tactical.heroes.mechanics.zero_pass_through import ZeroDashSkill
from wujiang.tactical.heroes.mechanics.next_five_geometry import square_around_cells
from wujiang.tactical.prediction import expected_numeric


@expected_numeric(independent=True)
def true_red_blast_score(battle: Battle, actor: HeroUnit, destination: Position) -> float:
    """Value the real landing halo, including allied damage and shield/dodge loss."""
    body = set(battle.unit_cells_at(actor, destination))
    total = 0.0
    for target in battle.all_units():
        if target.unit_id == actor.unit_id or not target.alive or target.position is None or target.banished:
            continue
        if not any(any(max(abs(cell.x - own.x), abs(cell.y - own.y)) == 1 for own in body)
                   for cell in battle.unit_cells(target)):
            continue
        raw = max(0.0, battle.damage_rule.calculate_damage(5, target.stat("defense")))
        if target.total_shields() > 0 or target.dodge_charges > 0:
            raw = 0.0
        impact = min(target.current_hp, raw) * 105.0 + (22.0 if raw >= target.current_hp > 0 else 0.0)
        if raw <= 0 and (target.total_shields() > 0 or target.dodge_charges > 0):
            impact = 11.0
        total += impact if target.player_id != actor.player_id else -impact * 1.6
    return total


class TrueRedAIPolicy(HeroAIPolicy):
    """Value the blast caused by a real landing position."""

    def move_candidate_score_bonus(self, battle: Battle, actor: HeroUnit,
                                   destination: Position) -> float:
        return true_red_blast_score(battle, actor, destination)


class TrueRedMovementTrait(Trait):
    def __init__(self) -> None:
        super().__init__("真红位移", "本人轮可额外免费使用一次位移技能；每次实际位移结束后周围双方受威力5特性伤害；不位移的本人轮末魔+1。")
        self.free_displacement_used = False

    def on_owner_turn_start(self, battle: Battle) -> None:
        self.free_displacement_used = False

    def on_unit_moved(self, battle: Battle, ctx: MoveContext) -> None:
        owner = self.owner
        if owner is None or not owner.alive or owner.position is None or ctx.start == ctx.end:
            return
        if ctx.unit is not owner and battle.mounted_unit_for(owner) is not ctx.unit:
            return
        owner.moved_this_turn = True
        body = set(battle.unit_cells(owner))
        cells = [cell for cell in square_around_cells(battle, body, radius=1) if cell not in body]
        if cells:
            battle.queue_skill_effect_action(actor=owner, display_name="真红位移爆破",
                                             effect_code="true_red_move_blast",
                                             payload={"cells": positions_to_dict(cells), "refresh_cell_targets": True,
                                                      "after_death_effect": True},
                                             target_cells=cells, effect_resolver=resolve_true_red_move_blast)

    def on_owner_turn_end(self, battle: Battle) -> None:
        owner = self.owner
        if owner is not None and owner.alive and not owner.moved_this_turn:
            gained = owner.gain_mana(1)
            if gained:
                battle.log_public_event(f"{owner.name} 本轮未位移，魔 +{gained}。", source=owner)

    def to_public_dict(self, battle: Battle) -> dict[str, Any]:
        data = super().to_public_dict(battle)
        data["free_displacement_used"] = self.free_displacement_used
        return data


class TrueRedFreeMoveMixin:
    def can_use(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any] | None = None) -> tuple[bool, str]:
        ok, reason = super().can_use(battle, actor, payload)
        if not ok:
            return ok, reason
        trait = next((item for item in actor.traits if isinstance(item, TrueRedMovementTrait)), None)
        if trait is None or trait.free_displacement_used:
            return False, "本回合免费位移额度已使用。"
        return True, ""

    def prepay_resources(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any] | None = None) -> None:
        trait = next(item for item in actor.traits if isinstance(item, TrueRedMovementTrait))
        if trait.free_displacement_used:
            raise ActionError("本回合免费位移额度已使用。")
        super().prepay_resources(battle, actor, payload)
        trait.free_displacement_used = True


class TrueRedFreeLeapSkill(TrueRedFreeMoveMixin, DashMoveSkill):
    def __init__(self) -> None:
        super().__init__("true_red_free_leap", "免费飞跃", "本人轮额外一次免费飞跃；与普通飞跃次数独立，但与免费冲刺共用额度。",
                         max_distance=3, exact_distance=3, mana_cost=0, max_uses_per_turn=1,
                         straight_only=True, ignore_units=True)


class TrueRedFreeDashSkill(TrueRedFreeMoveMixin, ZeroDashSkill):
    def __init__(self) -> None:
        super().__init__()
        self.code = "true_red_free_dash"
        self.name = "免费冲刺"
        self.description = "本人轮额外一次免费冲刺；与普通冲刺次数独立，但与免费飞跃共用额度。"


