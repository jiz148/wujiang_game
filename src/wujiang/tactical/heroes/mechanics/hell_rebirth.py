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


class HellRespawnScoreMixin:
    """Own the destination tradeoff for a returning Hell body."""

    respawn_pressure = 13.0
    respawn_ai_enabled = True
    respawn_safety = 0.8
    respawn_defense: float | None = None
    respawn_dog_space = False

    def ai_respawn_destination_score(self, battle: Battle, unit, destination: Position,
                                     enemies, distance_to_position) -> float:
        pressure, safety = self.respawn_pressure, self.respawn_safety
        if type(self) is HellRebirthTrait:
            # Parameterized traits in saved battles predate the specific classes.
            code = self.hero_code
            pressure = 22.0 if code == "excel_r238" else pressure
            safety = 1.3 if code == "excel_r239" else safety
        nearest_enemy = min((distance_to_position(battle, enemy, destination)
                             for enemy in enemies), default=8)
        effective_defense = self.respawn_defense if self.respawn_defense is not None else unit.stat("defense")
        danger = sum(max(0.0, enemy.stat("attack") - effective_defense + 1.0) * 12.0
                     for enemy in enemies if distance_to_position(battle, enemy, destination)
                     <= enemy.normal_move_distance() + enemy.targeting_range())
        dog_space = (sum(battle.in_bounds(destination.offset(dx, dy))
                         and battle.can_place_unit(unit, destination.offset(dx, dy), ignore=unit, mover=unit)
                         for dx in (-1, 0, 1) for dy in (-1, 0, 1) if dx or dy)
                     if self.respawn_dog_space else 0)
        return -nearest_enemy * pressure - danger * safety + dog_space * 3.0


class HellRebirthTrait(HellRespawnScoreMixin, Trait):
    ai_sacrifice_future_gain: float | None = None
    ai_rescue_bonus = False
    ai_shuffle_value: float | None = None

    def ai_shuffle_rebirth_value(self, battle: Battle, actor) -> float | None:
        if type(self) is HellRebirthTrait:
            return {"excel_r238": 92.0, "excel_r239": 112.0,
                    "excel_r240": 93.0}.get(self.hero_code)
        return self.ai_shuffle_value

    def ai_hell_death_effect_value(self, battle: Battle) -> float:
        return 35.0

    def ai_sacrifice_gain(self) -> float | None:
        if type(self) is HellRebirthTrait:
            return {"excel_r238": 115.0, "excel_r240": 105.0,
                    "excel_r242": 0.0}.get(self.hero_code)
        return self.ai_sacrifice_future_gain

    def ai_hell_rescue_bonus(self) -> float:
        if type(self) is HellRebirthTrait:
            return 30.0 if self.hero_code in {
                "excel_r238", "excel_r240", "excel_r242", "excel_r243",
                "excel_r244", "excel_r246"} else 0.0
        return 30.0 if self.ai_rescue_bonus else 0.0

    @property
    def rebirth_enemy_anchors_only(self) -> bool:
        # Existing saved battles may still contain the old parameterized trait.
        return type(self) is HellRebirthTrait and self.hero_code == "excel_r239"

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
        status = self.rebirth_bonus_status()
        if status is not None:
            status.is_skill_effect = False
            owner.add_status(status, source=owner)
        self.on_rebirth_bonus(battle)
        battle.log(f"{owner.name} 的地狱再临强化开始。")

    def rebirth_bonus_status(self) -> StatusEffect | None:
        if type(self) is HellRebirthTrait:
            legacy = {"excel_r238": HellRebirthPierceStatus,
                      "excel_r240": HellRebirthGuardStatus}.get(self.hero_code)
            return legacy() if legacy is not None else None
        return None

    def on_rebirth_bonus(self, battle: Battle) -> None:
        if type(self) is HellRebirthTrait and self.hero_code == "excel_r239":
            HellShuffleRebirthTrait.on_rebirth_bonus(self, battle)


class HellPierceRebirthTrait(HellRebirthTrait):
    respawn_pressure = 22.0
    ai_sacrifice_future_gain = 115.0
    ai_rescue_bonus = True
    ai_shuffle_value = 92.0
    def __init__(self) -> None:
        super().__init__("excel_r238")

    def rebirth_bonus_status(self) -> StatusEffect:
        return HellRebirthPierceStatus()


class HellGuardRebirthTrait(HellRebirthTrait):
    ai_sacrifice_future_gain = 105.0
    ai_rescue_bonus = True
    ai_shuffle_value = 93.0
    def __init__(self) -> None:
        super().__init__("excel_r240")

    def rebirth_bonus_status(self) -> StatusEffect:
        return HellRebirthGuardStatus()


class HellShuffleRebirthTrait(HellRebirthTrait):
    rebirth_enemy_anchors_only = True
    respawn_safety = 1.3
    ai_shuffle_value = 112.0

    def __init__(self) -> None:
        super().__init__("excel_r239")

    def on_rebirth_bonus(self, battle: Battle) -> None:
        owner = self.owner
        if owner is None:
            return
        shuffle = next((skill for skill in owner.skills if skill.code == "hell_shuffle"), None)
        if shuffle is not None:
            shuffle.uses_this_battle = 0
            battle.log(f"{owner.name} 的地狱洗牌次数已重置。")


class HellKnightRebirthTrait(HellRebirthTrait):
    respawn_pressure = 22.0
    respawn_defense = 4.0
    ai_rescue_bonus = True
    ai_shuffle_value = 115.0
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
    rebirth_enemy_anchors_only = True
    respawn_pressure = 22.0
    ai_rescue_bonus = True
    ai_shuffle_value = 102.0

    def ai_hell_death_effect_value(self, battle: Battle) -> float:
        owner = self.owner
        if owner is None or owner.position is None:
            return 35.0
        nearby = battle.units_at_cells(battle.neighbors(owner.position))
        return (35.0 + 22.0 * sum(body.alive and body.player_id != owner.player_id for body in nearby)
                - 30.0 * sum(body.alive and body.player_id == owner.player_id
                             and body.unit_id != owner.unit_id for body in nearby))

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


