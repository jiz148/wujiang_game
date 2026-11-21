"""R104 E Vanguard: isolation bonus and momentum-funded spin."""

from __future__ import annotations

from typing import Any

from wujiang.tactical.engine.core import Battle, DamageContext, MoveContext, Position, Skill, Trait, Unit
from wujiang.tactical.heroes.mechanics.foundation import LargePierceSkill
from wujiang.tactical.heroes.mechanics.zero_pass_through import ZeroDashSkill


def _spin_cells(battle: Battle, actor: Unit) -> list[Position]:
    occupied = set(battle.unit_cells(actor))
    return sorted({cell.offset(dx, dy)
                   for cell in occupied for dx in (-1, 0, 1) for dy in (-1, 0, 1)
                   if (dx or dy) and cell.offset(dx, dy) not in occupied
                   and battle.in_bounds(cell.offset(dx, dy))}, key=lambda p: (p.y, p.x))


class VanguardIsolationTrait(Trait):
    def __init__(self) -> None:
        super().__init__("孤军前锋", "周围7×7无友军且有敌军时，攻守各+1。")

    def modify_stat(self, stat_name: str, value: float) -> float:
        owner = self.owner
        ref = getattr(owner, "_battle_ref", None) if owner is not None else None
        battle = ref() if ref is not None else None
        if stat_name not in {"attack", "defense"} or owner is None or battle is None or not owner.alive:
            return value
        own_cells = battle.unit_cells(owner)
        if not own_cells:
            return value
        nearby = [unit for unit in battle.all_units()
                  if unit is not owner and unit.alive and unit.position is not None
                  and any(a.distance_to(b) <= 3 for a in own_cells for b in battle.unit_cells(unit))]
        return value + 1 if (any(unit.player_id != owner.player_id for unit in nearby)
                             and not any(unit.player_id == owner.player_id for unit in nearby)) else value


class VanguardSpinMomentumTrait(Trait):
    def __init__(self) -> None:
        super().__init__("旋转动能", "每次实际位移后获得一次本轮额外免费旋转。")
        self.free_spins = 0

    def on_unit_moved(self, battle: Battle, ctx: MoveContext) -> None:
        if self.owner is ctx.unit and len(ctx.path) > 1:
            self.free_spins += 1
            battle.log_public_event(f"{ctx.unit.name} 位移后获得一次免费旋转（现有{self.free_spins}次）。",
                                    source=ctx.unit)

    def on_any_turn_end(self, battle: Battle, ended_player_id: int) -> None:
        self.free_spins = 0

    def modify_skill_mana_cost(self, battle: Battle, actor: Unit, skill: Skill,
                               payload: dict[str, Any] | None, cost: float) -> float:
        return 0.0 if skill.code == "vanguard_spin" and self.free_spins > 0 else cost


def _momentum(actor: Unit) -> VanguardSpinMomentumTrait | None:
    return next((trait for trait in actor.traits if isinstance(trait, VanguardSpinMomentumTrait)), None)


class VanguardWindSkill(LargePierceSkill):
    def __init__(self) -> None:
        super().__init__(line_length=3, code="large_pierce", name="风")


class VanguardChargeSkill(ZeroDashSkill):
    def __init__(self) -> None:
        super().__init__()
        self.name = "冲锋"


class VanguardSpinSkill(Skill):
    ai_payload_value_owned = True

    def __init__(self) -> None:
        super().__init__("vanguard_spin", "旋转", "费1.5魔；攻击身体外一周。每次位移后额外获得一次本轮免费施放。",
                         mana_cost=1.5, target_mode="none")

    def _cells(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> list[Position]:
        return battle.payload_positions(payload, "declared_spin_cells") or _spin_cells(battle, actor)

    def queued_payload_metadata(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> dict[str, Any]:
        return {"declared_spin_cells": [cell.to_dict() for cell in _spin_cells(battle, actor)]}

    def get_target_cells_for_payload(self, battle: Battle, actor: Unit,
                                     payload: dict[str, Any]) -> list[Position]:
        return self._cells(battle, actor, payload)

    def get_target_units_for_payload(self, battle: Battle, actor: Unit,
                                     payload: dict[str, Any]) -> list[Unit]:
        return list(battle.effect_units_at_cells(self._cells(battle, actor, payload), ignore=actor))

    def preview(self, battle: Battle, actor: Unit) -> dict[str, Any]:
        cells = _spin_cells(battle, actor)
        return {"cells": [cell.to_dict() for cell in cells],
                "target_unit_ids": [unit.unit_id for unit in battle.effect_units_at_cells(cells, ignore=actor)],
                "secondary_cells": [], "requires_target": False}

    def ai_payload_value(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> float:
        from wujiang.tactical.direct_expectation import damage_result
        from wujiang.tactical.rooms.ai_probe import ai_probe_rollback

        cells = self._cells(battle, actor, payload)
        score = 0.0
        for target in battle.effect_units_at_cells(cells, ignore=actor):
            with ai_probe_rollback(battle):
                source = battle.get_unit(actor.unit_id)
                recipient = battle.get_unit(target.unit_id)
                hp_before = recipient.current_hp
                ctx = battle.resolve_damage(DamageContext(
                    source=source, target=recipient, attack_power=source.stat("attack"),
                    is_skill=True, area_cell_hits=battle.unit_hit_count_for_cells(recipient, cells),
                    action_name=self.name, tags={"skill", self.code}))
                value = min(hp_before, max(0.0, damage_result(ctx, ctx.actual_damage))) * 100.0
            score += value if target.player_id != actor.player_id else -1.5 * value
        return score - (0.0 if _momentum(actor) and _momentum(actor).free_spins else 15.0) if score > 0 else -1000.0

    def ai_priority_score(self, context: Any) -> float:
        return self.ai_payload_value(context.battle, context.actor, context.payload)

    def ai_effective_enemy_impact(self, battle: Battle, actor: Unit,
                                  payload: dict[str, Any], targets: list[Unit] | None = None,
                                  shared: Any = None, action: dict[str, Any] | None = None) -> bool:
        return any(unit.player_id != actor.player_id
                   for unit in self.get_target_units_for_payload(battle, actor, payload))

    def execute(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> None:
        momentum = _momentum(actor)
        if momentum is not None and momentum.free_spins > 0:
            momentum.free_spins -= 1
        cells = self._cells(battle, actor, payload)
        for target in battle.effect_units_at_cells(cells, ignore=actor):
            battle.resolve_damage(DamageContext(
                source=actor, target=target, attack_power=actor.stat("attack"), is_skill=True,
                area_cell_hits=battle.unit_hit_count_for_cells(target, cells),
                action_name=self.name, tags={"skill", self.code}))

