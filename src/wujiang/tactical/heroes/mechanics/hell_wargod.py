"""Hell war god and sacrifice mechanics."""

from __future__ import annotations

from typing import Any
from wujiang.tactical.engine.core import Battle
from wujiang.tactical.engine.core import DamageContext
from wujiang.tactical.engine.core import Skill
from wujiang.tactical.engine.core import StatusEffect
from wujiang.tactical.engine.core import Unit
from wujiang.tactical.heroes.common import positions_to_dict
from wujiang.tactical.heroes.ai_policy import AISkillScoreContext
from wujiang.tactical.heroes.mechanics.hell_rebirth import HellRebirthTrait


class HellWarGodStatus(StatusEffect):
    def __init__(self) -> None:
        super().__init__("武神", "攻守各+1；再经历2个敌方武将回合末后由武神技能破坏本人。")
        self.remaining_enemy_ends = 2

    def modify_stat(self, stat_name: str, value: float) -> float:
        return value + 1 if stat_name in {"attack", "defense"} else value

    def on_any_turn_end(self, battle: Battle, ended_player_id: int) -> None:
        owner = self.owner
        if (owner is None or not owner.alive or owner.player_id == ended_player_id
                or battle.is_army_turn()):
            return
        self.remaining_enemy_ends -= 1
        if self.remaining_enemy_ends > 0:
            battle.log(f"{owner.name} 的武神还剩 {self.remaining_enemy_ends} 个敌方武将回合末。")
            return
        owner.current_hp = 0
        owner.alive = False
        owner._hell_rebirth_skip_turn_number = battle.turn_number
        battle.log_public_event(f"{owner.name} 的武神到期，技能破坏了本人。", source=owner, target=owner)
        for component in list(owner.iter_components()):
            callback = getattr(component, "on_hell_forced_skill_death", None)
            if callable(callback):
                callback(battle)
        owner.remove_status(self, battle)

    def to_public_dict(self, battle: Battle) -> dict[str, Any]:
        data = super().to_public_dict(battle)
        data["remaining_enemy_ends"] = self.remaining_enemy_ends
        return data


class HellWarGodSkill(Skill):
    def ai_priority_score(self, context: AISkillScoreContext) -> float:
        actor, battle = context.actor, context.battle
        enemies = [unit for unit in context.hostile_units() if unit.position is not None]
        if not enemies:
            return -1000.0
        contact = min(battle.distance_between_units(actor, enemy) for enemy in enemies)
        reach = actor.targeting_range() + actor.remaining_normal_move_distance(battle)
        if contact > reach + 2:
            return -1000.0
        score = 52.0 + (38.0 if contact <= reach else 12.0)
        if actor.attacks_used >= actor.attack_actions_per_turn() and actor.performed_active_skill:
            score -= 45.0
        if actor.current_hp <= 0.25 and contact <= 1:
            score -= 35.0 if actor.attacks_used < actor.attack_actions_per_turn() else 90.0
        return score

    def __init__(self) -> None:
        super().__init__("hell_war_god", "武神", "大招：本场一次，攻守各+1；2个敌方武将回合末后技能破坏本人。",
                         max_uses_per_battle=1)

    def execute(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> None:
        actor.add_status(HellWarGodStatus(), source=actor)
        battle.log_public_event(f"{actor.name} 发动武神，攻守各+1，两次敌方武将回合末后将自毁。", source=actor)

    def preview(self, battle: Battle, actor: Unit) -> dict[str, Any]:
        return {"cells": positions_to_dict(battle.unit_cells(actor)), "target_unit_ids": [actor.unit_id],
                "secondary_cells": [], "requires_target": False}


def resolve_hell_war_god_death_sweep(battle: Battle, actor: Unit, queued_action: Any) -> None:
    cells = battle.payload_positions(queued_action.payload, "cells")
    for recipient in battle.effect_units_at_cells(cells):
        if recipient.player_id == actor.player_id:
            continue
        battle.resolve_damage(DamageContext(
            source=actor, target=recipient, attack_power=float(queued_action.payload["attack_power"]),
            is_skill=True, action_name=queued_action.display_name,
            tags={"hell_war_god_death_sweep", "death_trait"},
        ))


class HellWarGodRebirthTrait(HellRebirthTrait):
    respawn_ai_enabled = False
    ai_rescue_bonus = True
    ai_shuffle_value = 105.0

    def ai_hell_death_effect_value(self, battle: Battle) -> float:
        owner = self.owner
        if owner is None or owner.position is None:
            return 35.0
        nearby = battle.units_at_cells(battle.neighbors(owner.position))
        return (35.0 + 22.0 * sum(body.alive and body.player_id != owner.player_id for body in nearby)
                - 30.0 * sum(body.alive and body.player_id == owner.player_id
                             and body.unit_id != owner.unit_id for body in nearby))

    def __init__(self) -> None:
        super().__init__("excel_r246")
        self.name = "武神再临"
        self.description = "技能死亡时对原身体外邻敌方单位逐一造成技能伤害，再于下个敌方武将末0魔回场；大招不重置。"

    def offer_death_sweep(self, battle: Battle) -> None:
        owner = self.owner
        if owner is None or getattr(owner, "_hell_war_death_sweep_scheduled", False):
            return
        owner._hell_war_death_sweep_scheduled = True
        origin = owner.position or owner.last_position
        if origin is None:
            return
        body = set(battle.unit_cells_at(owner, origin))
        cells = sorted({neighbor for cell in body for neighbor in battle.neighbors(cell)
                        if neighbor not in body}, key=lambda cell: (cell.y, cell.x))
        targets = [unit for unit in battle.effect_units_at_cells(cells) if unit.player_id != owner.player_id]
        for target in targets:
            cell = next((body_cell for body_cell in battle.unit_cells(target) if body_cell in cells), None)
            if cell is None:
                continue
            battle.queue_skill_effect_action(
                actor=owner, display_name="武神死亡扫击", effect_code="hell_war_god_death_sweep",
                payload={"after_death_effect": True, "cells": [cell.to_dict()],
                         "attack_power": owner.stat("attack")},
                target_units=[target], target_cells=[cell], include_cell_units=False, hostile=True,
                effect_resolver=resolve_hell_war_god_death_sweep,
            )
        if not targets:
            return
        battle.log_public_event(f"{owner.name} 临死扫击周围敌方单位。", source=owner)

    def on_after_damage(self, battle: Battle, ctx: DamageContext) -> None:
        owner = self.owner
        if owner is None or ctx.target.unit_id != owner.unit_id or owner.alive:
            return
        if ctx.is_skill and not ctx.from_field_effect:
            super().on_after_damage(battle, ctx)
            self.offer_death_sweep(battle)
        for status in list(owner.statuses):
            if isinstance(status, HellWarGodStatus):
                owner.remove_status(status, battle)

    def on_hell_forced_skill_death(self, battle: Battle) -> None:
        super().on_hell_forced_skill_death(battle)
        self.offer_death_sweep(battle)
        if self.owner is not None:
            for status in list(self.owner.statuses):
                if isinstance(status, HellWarGodStatus):
                    self.owner.remove_status(status, battle)

    def on_hell_rebirth(self, battle: Battle) -> None:
        if self.owner is not None:
            self.owner._hell_war_death_sweep_scheduled = False
            for status in list(self.owner.statuses):
                if isinstance(status, HellWarGodStatus):
                    self.owner.remove_status(status, battle)

    def on_hell_rescue(self, battle: Battle) -> None:
        if self.owner is not None:
            self.owner._hell_war_death_sweep_scheduled = False
            for status in list(self.owner.statuses):
                if isinstance(status, HellWarGodStatus):
                    self.owner.remove_status(status, battle)


class HellSikaSacrificeTrait(HellRebirthTrait):
    respawn_safety = 1.3
    ai_sacrifice_future_gain = 0.0
    ai_rescue_bonus = True
    ai_shuffle_value = 70.0

    requires_damage_choice = True

    def __init__(self) -> None:
        super().__init__("excel_r242")
        self.name = "赤候牺牲"
        self.description = "技能死亡回场后或普攻死亡时，可选一名己方名字含地狱的在场单位作技能破坏。"

    def sacrifice_options(self, battle: Battle) -> list[str]:
        owner = self.owner
        return [unit.unit_id for unit in battle.all_units()
                if owner is not None and unit.alive and not unit.banished and unit.position is not None
                and unit.player_id == owner.player_id and "地狱" in unit.name]

    def offer_sacrifice(self, battle: Battle) -> None:
        owner = self.owner
        if owner is None:
            return
        options = self.sacrifice_options(battle)
        choice = battle.optional_position_choice(owner, options, action_name="赤候牺牲", kind="hell_sacrifice")
        if choice not in options:
            battle.log(f"{owner.name} 放弃了地狱牺牲。")
            return
        target = battle.units.get(choice)
        if target is None or not target.alive:
            return
        result = battle.validate_target(owner, target, action_name="赤候牺牲", is_skill=True,
                                        is_hostile=True, ignore_targeting_restrictions=True,
                                        record_effect_on_validation=False)
        recipient = result.target
        if result.cancelled or recipient.skill_non_damage_effects_blocked():
            battle.log_public_event(result.reason or f"{recipient.name} 未受到赤候牺牲影响。",
                                    source=owner, target=recipient)
            return
        recipient.current_hp = 0
        recipient.alive = False
        battle.log_public_event(f"{owner.name} 选择技能破坏己方的{recipient.name}。", source=owner, target=recipient)
        for component in list(recipient.iter_components()):
            callback = getattr(component, "on_hell_forced_skill_death", None)
            if callable(callback):
                callback(battle)

    def on_after_damage(self, battle: Battle, ctx: DamageContext) -> None:
        owner = self.owner
        if owner is None or ctx.target.unit_id != owner.unit_id or owner.alive:
            return
        if ctx.is_skill and not ctx.from_field_effect:
            super().on_after_damage(battle, ctx)
        elif not ctx.from_field_effect and not ctx.is_skill and "attack" in ctx.tags:
            self.offer_sacrifice(battle)

    def on_hell_rebirth(self, battle: Battle) -> None:
        super().on_hell_rebirth(battle)
        self.offer_sacrifice(battle)
        battle.cleanup_dead_units()


