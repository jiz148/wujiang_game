"""Hell rebirth traits for the first Hell heroes."""

from __future__ import annotations

from wujiang.tactical.engine.core import Battle
from wujiang.tactical.engine.core import DamageContext
from wujiang.tactical.engine.core import Position
from wujiang.tactical.engine.core import StatusEffect
from wujiang.tactical.engine.core import Trait
from wujiang.tactical.heroes.common import InvincibleUntilActionStatus
from wujiang.tactical.heroes.mechanics.hell_core import HellHorseSummon
from wujiang.tactical.heroes.mechanics.hell_core import HellRebirthGuardStatus
from wujiang.tactical.heroes.mechanics.hell_core import HellRebirthPierceStatus
from wujiang.tactical.heroes.mechanics.hell_core import resolve_hell_death_strike


class HellRebirthTrait(Trait):
    def __init__(self, code: str) -> None:
        self.hero_code = code
        super().__init__("地狱再临", "真正被技能破坏后，在下个敌方武将回合末以0魔合法回场。")

    def on_after_damage(self, battle: Battle, ctx: DamageContext) -> None:
        owner = self.owner
        if (owner is None or ctx.target.unit_id != owner.unit_id or owner.alive
                or not ctx.is_skill or ctx.from_field_effect):
            return
        battle.schedule_hell_rebirth(owner)

    def on_hell_forced_skill_death(self, battle: Battle) -> None:
        if self.owner is not None:
            battle.schedule_hell_rebirth(self.owner)

    def on_hell_rebirth(self, battle: Battle) -> None:
        owner = self.owner
        if owner is None:
            return
        status: StatusEffect | None = None
        if self.hero_code == "excel_r238":
            status = HellRebirthPierceStatus()
        elif self.hero_code == "excel_r240":
            status = HellRebirthGuardStatus()
        elif self.hero_code == "excel_r239":
            shuffle = next((skill for skill in owner.skills if skill.code == "hell_shuffle"), None)
            if shuffle is not None:
                shuffle.uses_this_battle = 0
                battle.log(f"{owner.name} 的地狱洗牌次数已重置。")
        if status is not None:
            status.is_skill_effect = False
            owner.add_status(status, source=owner)
        battle.log(f"{owner.name} 的地狱再临强化开始。")


class HellKnightRebirthTrait(HellRebirthTrait):
    def __init__(self) -> None:
        super().__init__("excel_r243")
        self.name = "地狱骑士再临"

    def hell_rebirth_destination_legal(self, battle: Battle, destination: Position) -> bool:
        owner = self.owner
        if owner is None:
            return False
        horse = HellHorseSummon(owner.player_id)
        horse.mount_owner_id = owner.unit_id
        horse.is_mount = True
        return battle.can_place_unit(horse, destination, mover=horse)

    def on_hell_rebirth(self, battle: Battle) -> None:
        owner = self.owner
        if owner is None or owner.position is None:
            return
        horse = HellHorseSummon(owner.player_id)
        horse.mount_owner_id = owner.unit_id
        horse.is_mount = True
        battle.summon_unit(horse, owner.position, summoner=owner)
        battle.set_mounted_state(owner, horse)
        battle.log_public_event(f"{owner.name} 骑上地狱马再临。", source=owner, target=horse)


class HellAssassinRebirthTrait(HellRebirthTrait):
    requires_damage_choice = True

    def __init__(self) -> None:
        super().__init__("excel_r244")
        self.name = "地狱刺客再临"

    def on_after_damage(self, battle: Battle, ctx: DamageContext) -> None:
        owner = self.owner
        if (owner is None or ctx.target.unit_id != owner.unit_id or owner.alive
                or not ctx.is_skill or ctx.from_field_effect):
            return
        super().on_after_damage(battle, ctx)
        self.offer_death_strike(battle)

    def on_hell_forced_skill_death(self, battle: Battle) -> None:
        super().on_hell_forced_skill_death(battle)
        self.offer_death_strike(battle)

    def offer_death_strike(self, battle: Battle) -> None:
        owner = self.owner
        if owner is None:
            return
        if getattr(owner, "_hell_assassin_strike_scheduled", False):
            return
        owner._hell_assassin_strike_scheduled = True
        origin = owner.position or owner.last_position
        if origin is None:
            return
        surrounding = set(battle.neighbors(origin))
        options = [unit.unit_id for unit in battle.all_units()
                   if unit.alive and not unit.banished and unit.position is not None
                   and any(cell in surrounding for cell in battle.unit_cells(unit))]
        if not options:
            battle.log(f"{owner.name} 身边没有死亡反击目标。")
            return
        selected_id = battle.optional_position_choice(owner, options, action_name="地狱刺客死亡反击",
                                                      kind="hell_death_strike")
        selected = battle.units.get(selected_id or "")
        if selected is None or selected.position is None or not selected.alive:
            battle.log(f"{owner.name} 放弃了死亡反击。")
            return
        cell = next((cell for cell in battle.unit_cells(selected) if cell in surrounding), None)
        if cell is None:
            return
        battle.log_public_event(f"{owner.name} 临死对{selected.name}连续反击两次。",
                                source=owner, target=selected)
        for index in range(2):
            battle.queue_skill_effect_action(
                actor=owner, display_name=f"地狱刺客死亡反击·{index + 1}",
                effect_code="hell_death_strike",
                payload={"after_death_effect": True, "cells": [cell.to_dict()],
                         "attack_power": owner.stat("attack"), "refresh_cell_targets": True},
                target_cells=[cell], speed=1, hostile=selected.player_id != owner.player_id,
                segment_index=index + 1, segment_count=2,
                effect_resolver=resolve_hell_death_strike,
            )

    def on_hell_rebirth(self, battle: Battle) -> None:
        owner = self.owner
        if owner is None:
            return
        owner._hell_assassin_strike_scheduled = False
        owner.add_status(InvincibleUntilActionStatus(), source=owner)
        battle.enforce_sandstorm_stealth_rules()
        battle.clear_all_stealth_if_all_heroes_stealthed()
        battle.log(f"{owner.name} 隐身地狱再临。")

    def on_hell_rescue(self, battle: Battle) -> None:
        if self.owner is not None:
            self.owner._hell_assassin_strike_scheduled = False


