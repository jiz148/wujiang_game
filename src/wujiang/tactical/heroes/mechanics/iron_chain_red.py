"""R291 red iron chain, drain, and piercing objects."""

from __future__ import annotations

from typing import Any
from wujiang.tactical.engine.core import ActionError
from wujiang.tactical.engine.core import Battle
from wujiang.tactical.engine.core import DamageContext
from wujiang.tactical.engine.core import HeroUnit
from wujiang.tactical.engine.core import Position
from wujiang.tactical.engine.core import StatusEffect
from wujiang.tactical.engine.core import Trait
from wujiang.tactical.heroes.common import DrainManaSkill
from wujiang.tactical.heroes.common import ensure_distance
from wujiang.tactical.heroes.common import ensure_enemy
from wujiang.tactical.heroes.common import is_mana_drain_immune
from wujiang.tactical.heroes.common import payload_target_unit
from wujiang.tactical.heroes.common import positions_to_dict
from wujiang.tactical.heroes.mechanics.h1_line_damage import H1LineDamageSkill
from wujiang.tactical.heroes.mechanics.terrain_helpers import _terrain_unit
from wujiang.tactical.prediction import direct_scoring
from wujiang.tactical.prediction import random


class RedNextDamageStatus(StatusEffect):
    def __init__(self, target_unit_id: str) -> None:
        super().__init__("铁锁追击", "本回合对铁锁路径锚点造成的下一次直接伤害，伤害值 +1。", duration=1, tick_scope="any_turn_end")
        self.target_unit_id = target_unit_id

    def on_before_damage(self, battle: Battle, ctx: DamageContext) -> None:
        owner = self.owner
        if (
            owner is None
            or ctx.source is None
            or ctx.source.unit_id != owner.unit_id
            or ctx.target.unit_id != self.target_unit_id
            or ctx.from_field_effect
        ):
            return
        if ctx.raw_damage is None:
            ctx.attack_power += 1
        else:
            ctx.raw_damage = round(ctx.raw_damage + 1, 4)
        owner.remove_status(self, battle)
        battle.log(f"{owner.name} 的【铁锁追击】使对 {ctx.target.name} 的这次伤害值 +1。")


class IronChainPathSkill(H1LineDamageSkill):
    def __init__(self) -> None:
        super().__init__(
            "iron_chain_path",
            "铁锁路径",
            "普通技能：每回合第一次免费，之后费0.5魔；破魔伤害前5格敌方，并移动到最近单位或地形周围；下次伤害+1。",
            length=5,
            mana_cost=0.5,
        )

    def mana_cost_for_payload(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any] | None = None) -> float:
        self.sync_turn_scope(battle)
        return 0.0 if self.uses_this_turn == 0 else super().mana_cost_for_payload(battle, actor, payload)

    def chosen_cells(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> list[Position]:
        return battle.payload_positions(payload, "declared_area_cells") or super().chosen_cells(battle, actor, payload)

    def queued_payload_metadata(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> dict[str, Any]:
        return {"declared_area_cells": positions_to_dict(super().chosen_cells(battle, actor, payload))}

    def _stable_unit_key(self, battle: Battle, unit: HeroUnit) -> tuple[int, str]:
        try:
            turn_slot = battle.turn_order_unit_ids.index(unit.unit_id)
        except ValueError:
            turn_slot = len(battle.turn_order_unit_ids)
        return turn_slot, unit.unit_id

    def anchor_for_cells(
        self,
        battle: Battle,
        actor: HeroUnit,
        cells: list[Position],
    ) -> tuple[str, HeroUnit | None, list[Position], str] | None:
        """Return the mandatory nearest anchor in declared near-to-far line order.

        The first tuple item is ``unit`` or ``terrain``.  Terrain-shaped units
        remain terrain anchors, so they provide movement but never the +1
        follow-up intended for a unit anchor.
        """
        for cell in cells:
            units = sorted(
                [unit for unit in battle.units_at(cell) if unit.unit_id != actor.unit_id and unit.alive],
                key=lambda unit: self._stable_unit_key(battle, unit),
            )
            if units:
                unit = units[0]
                anchor_kind = "terrain" if _terrain_unit(unit) else "unit"
                return anchor_kind, unit, list(battle.unit_cells(unit)), unit.name  # type: ignore[arg-type]
            if (cell.x, cell.y) in battle.blocked_cells:
                return "terrain", None, [cell], f"墙体({cell.x},{cell.y})"
            terrain_effects = sorted(
                [
                    effect
                    for effect in battle.field_effects
                    if getattr(effect, "is_terrain", False) and cell in effect.affected_cells(battle)
                ],
                key=lambda effect: effect.component_id,
            )
            if terrain_effects:
                effect = terrain_effects[0]
                return "terrain", None, list(effect.affected_cells(battle)), effect.name
        return None

    def destination_for_anchor(
        self,
        battle: Battle,
        actor: HeroUnit,
        anchor_cells: list[Position],
    ) -> Position | None:
        if actor.position is None or not anchor_cells:
            return None
        start = actor.position
        candidates = [
            start,
            *battle.reachable_positions(actor, max_distance=5, straight_only=True),
        ]
        adjacent = [
            cell
            for cell in candidates
            if min((cell.distance_to(anchor_cell) for anchor_cell in anchor_cells), default=10**9) == 1
        ]
        if not adjacent:
            return None
        return min(adjacent, key=lambda cell: (start.distance_to(cell), cell.y, cell.x))

    def affected_units(self, battle: Battle, actor: HeroUnit, cells: list[Position]) -> list[HeroUnit]:
        return [unit for unit in battle.units_at_cells(cells) if unit.player_id != actor.player_id]  # type: ignore[return-value]

    def resolve_hit(self, battle: Battle, actor: HeroUnit, target: HeroUnit, cells: list[Position]) -> DamageContext:
        return battle.resolve_damage(
            DamageContext(
                source=actor,
                target=target,
                attack_power=actor.stat("attack"),
                is_skill=True,
                action_name=self.name,
                ignore_shield=True,
                area_cell_hits=battle.unit_hit_count_for_cells(target, cells),
                tags={"skill", "attack", self.code},
            )
        )

    def execute(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> None:
        cells = self.chosen_cells(battle, actor, payload)
        anchor_info = self.anchor_for_cells(battle, actor, cells)
        for target in list(self.affected_units(battle, actor, cells)):
            self.resolve_hit(battle, actor, target, cells)
        if anchor_info is None or actor.position is None:
            return
        anchor_kind, anchor_unit, anchor_cells, anchor_name = anchor_info
        destination = self.destination_for_anchor(battle, actor, anchor_cells)
        if destination is not None and destination != actor.position:
            try:
                battle.move_unit(
                    actor,
                    destination,
                    via_skill=True,
                    forced=False,
                    straight_only=True,
                    max_distance=5,
                    tags={self.code},
                )
            except ActionError:
                destination = None
        movement_text = "并移动到其周围" if destination is not None else "但没有合法直线落点"
        if anchor_kind == "unit" and anchor_unit is not None:
            actor.add_status(RedNextDamageStatus(anchor_unit.unit_id))
            battle.log(
                f"{actor.name} 以 {anchor_name} 为铁锁锚点{movement_text}，"
                "本回合对其下一次直接伤害值 +1。"
            )
        else:
            battle.log(f"{actor.name} 以地形【{anchor_name}】为铁锁锚点{movement_text}；地形锚点不获得追击加伤。")

    def ignores_shield_for_payload(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> bool:
        return True


class RedDrainManaSkill(DrainManaSkill):
    def __init__(self) -> None:
        super().__init__()

    def execute(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> None:
        target = payload_target_unit(battle, payload)
        ensure_enemy(actor, target)
        if not battle.is_declared_resolution(actor, payload, {"skill"}):
            ensure_distance(actor, target, actor.targeting_range())
        ctx = battle.validate_target(
            actor,
            target,
            action_name=self.name,
            is_skill=True,
            is_hostile=True,
            ignore_shield=bool(payload.get("red_random_pierce")),
            tags={"skill", "drain_mana"},
        )
        if ctx.cancelled:
            if ctx.reason:
                battle.log_public_event(ctx.reason, source=actor, target=target)
            return
        if is_mana_drain_immune(target):
            battle.log(f"{target.name} 无法被吸魔。")
            return
        lost = target.drain_mana(1.0)
        gained = actor.gain_mana(lost)
        battle.log(f"{actor.name} 吸取了 {target.name} 的 {gained:g} 点魔力。")


class RedRandomPierceTrait(Trait):
    def __init__(self) -> None:
        super().__init__("赤之随机破魔", "每次声明普攻或技能时后端公开随机；1/2几率该整次动作破魔。")

    def roll_pierce(self) -> bool:
        return random.random() < 0.5

    def on_before_damage(self, battle: Battle, ctx: DamageContext) -> None:
        if not direct_scoring() or ctx.source is not self.owner or ctx.from_field_effect:
            return
        action = getattr(battle, "resolving_action", None)
        payload = action.payload if action is not None and action.actor_id == self.owner.unit_id else {}
        ctx.forecast_shield_pierce_probability = (float(bool(payload["red_random_pierce"]))
                                                if "red_random_pierce" in payload else .5)

    def on_owner_action_declared(self, battle: Battle, action_type: str, payload: dict[str, Any]) -> None:
        owner = self.owner
        if owner is None or action_type not in {"attack", "skill"}:
            return
        if direct_scoring():
            payload["red_random_pierce_probability"] = .5
            return
        success = self.roll_pierce()
        payload["red_random_pierce"] = success
        if success:
            payload["ignore_shield"] = True
        result = "成功，本次动作破魔" if success else "未触发"
        # This result is explicitly public even when the declared area contains
        # a stealthed target and ordinary action logs are suppressed.
        battle.log_public_event(f"{owner.name} 的【赤之随机破魔】{result}。", source=owner)


