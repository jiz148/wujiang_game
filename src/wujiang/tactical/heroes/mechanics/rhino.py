"""R356 Rhino legacy and return behavior."""

from __future__ import annotations

from wujiang.tactical.engine.core import Battle, BattleFieldEffect, DamageContext, HeroUnit, Position, Trait
from wujiang.tactical.heroes.common import StatModifierStatus
from wujiang.tactical.heroes.mechanics.next_five_geometry import square_around_cells


class RhinoLegacyStatus(StatModifierStatus):
    def __init__(self) -> None:
        super().__init__("强袭犀牛遗志", attack_delta=1, defense_delta=1, description="攻 +1，守 +1。", duration=3, tick_scope="owner_turn_end")


class RhinoReturnEffect(BattleFieldEffect):
    def __init__(self, rhino: HeroUnit, death_round: int, death_turn_number: int, turn_ring_size: int) -> None:
        super().__init__("强袭犀牛回归", "被破坏满3轮后，若有己方兽人武将则满状态回归。", duration=None)
        self.rhino = rhino
        self.death_round = death_round
        self.death_turn_number = death_turn_number
        self.return_turn_number = death_turn_number + max(1, turn_ring_size) * 3

    def _candidate_destinations(self, battle: Battle) -> list[tuple[HeroUnit, Position]]:
        rhino = self.rhino
        candidates: list[tuple[HeroUnit, Position]] = []
        orc_heroes = [
            unit
            for unit in battle.player_units(rhino.player_id)
            if not unit.is_summon and not unit.is_clone and unit.race == "兽人" and unit.position is not None and unit.alive
        ]
        turn_slots = {unit_id: index for index, unit_id in enumerate(battle.turn_order_unit_ids)}
        for orc in sorted(
            orc_heroes,
            key=lambda unit: (turn_slots.get(unit.unit_id, len(turn_slots)), unit.unit_id),
        ):
            for cell in sorted(square_around_cells(battle, battle.unit_cells(orc), radius=1), key=lambda item: (item.y, item.x)):
                if battle.can_place_unit(rhino, cell, ignore=rhino):
                    candidates.append((orc, cell))
        return candidates

    def on_turn_start(self, battle: Battle, active_unit: HeroUnit | None) -> None:
        if battle.turn_number < self.return_turn_number:
            return
        candidates = self._candidate_destinations(battle)
        if not candidates:
            battle.log(f"{self.rhino.name} 暂无可用的己方兽人武将或合法邻接格，继续等待回归。")
            return
        anchor, destination = candidates[0]
        rhino = self.rhino
        for status in list(rhino.statuses):
            if isinstance(status, StatModifierStatus) and status.duration is None:
                continue
            rhino.remove_status(status, battle)
        rhino.alive = True
        rhino.banished = False
        rhino.banish_return_position = None
        rhino.banish_turns_remaining = 0
        rhino.current_hp = rhino.max_health
        rhino.current_mana = rhino.max_mana()
        rhino.turn_ready = False
        rhino.move_used = False
        rhino.normal_move_actions_used = 0
        rhino.normal_move_steps_used = 0
        rhino.attacks_used = 0
        rhino.performed_active_skill = False
        rhino.moved_this_turn = False
        rhino.actions_taken_this_turn = []
        rhino.clear_end_of_turn_shields()
        for trait in rhino.traits:
            if isinstance(trait, RhinoDeathTrait):
                trait.triggered = False
        battle.add_unit(rhino, destination)
        battle.destroyed_units = [unit for unit in battle.destroyed_units if unit.unit_id != rhino.unit_id]
        battle.remove_field_effect(self)
        battle.notify_destroyed_hero_count_changed()
        battle.log(f"{rhino.name} 在 {anchor.name} 周围满状态回归。")


class RhinoDeathTrait(Trait):
    def __init__(self) -> None:
        super().__init__("强袭犀牛遗志", "被破坏时强化当前盟友3轮，并在3轮后尝试于己方兽人武将周围回归。")
        self.triggered = False

    def on_enter_battle(self, battle: Battle) -> None:
        self.triggered = False

    def trigger_legacy(self, battle: Battle) -> None:
        owner = self.owner
        if owner is None or owner.alive or self.triggered:
            return
        self.triggered = True
        for ally in list(battle.player_units(owner.player_id)):
            if ally.unit_id == owner.unit_id or not ally.alive or ally.position is None:
                continue
            existing = ally.get_status("强袭犀牛遗志")
            if existing is not None:
                ally.remove_status(existing, battle)
            ally.add_status(RhinoLegacyStatus())
        battle.add_field_effect(
            RhinoReturnEffect(
                owner,
                battle.round_number,
                battle.turn_number,
                len(battle.turn_order_unit_ids),
            )
        )
        battle.log(f"{owner.name} 被破坏，当前全部其他己方单位获得攻+1、守+1，持续3轮。")

    def on_after_damage(self, battle: Battle, ctx: DamageContext) -> None:
        owner = self.owner
        if owner is None or ctx.target.unit_id != owner.unit_id:
            return
        self.trigger_legacy(battle)

    def on_owner_removed(self, battle: Battle) -> None:
        # Covers destruction effects that set alive=False without creating a damage context.
        self.trigger_legacy(battle)


