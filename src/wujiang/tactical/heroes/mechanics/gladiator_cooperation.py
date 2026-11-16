"""Gladiator cooperation, roar, and rotation mechanics."""

from __future__ import annotations

from typing import Any
from wujiang.tactical.engine.core import ActionError
from wujiang.tactical.engine.core import Battle
from wujiang.tactical.engine.core import DamageContext
from wujiang.tactical.engine.core import MoveContext
from wujiang.tactical.engine.core import Skill
from wujiang.tactical.engine.core import StatusEffect
from wujiang.tactical.engine.core import Trait
from wujiang.tactical.engine.core import Unit
from wujiang.tactical.heroes.common import StatModifierStatus
from wujiang.tactical.heroes.ai_policy import AIReactionScoreContext
from wujiang.tactical.heroes.mechanics.gladiator_core import gladiator_swap
from wujiang.tactical.heroes.mechanics.gladiator_core import gladiator_swap_destinations


class GladiatorMovedByAllyTrait(Trait):
    def __init__(self, *, persistent_mana: bool) -> None:
        super().__init__("剑斗兽协同", "被其他己方单位效果位移时获得魔能力值与机动收益。")
        self.persistent_mana = persistent_mana

    def on_unit_moved(self, battle: Battle, ctx: MoveContext) -> None:
        owner = self.owner
        if owner is None or ctx.unit.unit_id != owner.unit_id or ctx.start == ctx.end:
            return
        source_id = next((tag.removeprefix("source:") for tag in ctx.tags if tag.startswith("source:")), None)
        if source_id is None and battle.resolving_action is not None:
            source_id = battle.resolving_action.actor_id
        source = battle.units.get(source_id or "")
        if source is None or source.unit_id == owner.unit_id or source.player_id != owner.player_id:
            return
        if self.persistent_mana:
            owner.base_stats.mana = round(float(owner.base_stats.mana) + 1.0, 2)
            owner.current_mana = round(owner.current_mana + 1.0, 2)
            existing = owner.get_status("剑斗双移")
            if existing is None:
                owner.add_status(GladiatorDoubleMoveStatus())
            else:
                existing.duration = 1
            battle.log_public_event(f"{owner.name} 受友方位移，魔能力值+1，本轮可移动两次。", source=source, target=owner)
        else:
            existing = owner.get_status("剑斗协同增益")
            if existing is None:
                owner.add_status(GladiatorCooperationStatus())
                owner.current_mana = round(owner.current_mana + 1.0, 2)
            else:
                existing.duration = 2
            battle.log_public_event(f"{owner.name} 受友方位移，攻和魔能力值各+1。", source=source, target=owner)


class GladiatorCooperationStatus(StatModifierStatus):
    def __init__(self) -> None:
        super().__init__("剑斗协同增益", attack_delta=1, mana_delta=1,
                         duration=2, tick_scope="owner_turn_end")

    def on_removed(self, battle: Battle) -> None:
        if self.owner is not None:
            self.owner.clamp_mana()


class GladiatorDoubleMoveStatus(StatusEffect):
    def __init__(self) -> None:
        super().__init__("剑斗双移", "本轮可普通移动两次。", duration=1, tick_scope="owner_turn_end")

    def modify_normal_move_actions_per_turn(self, value: int) -> int:
        return max(2, value)


class AndrewMovedByAllyTrait(Trait):
    def __init__(self) -> None:
        super().__init__("安德鲁协同", "因其他己方单位效果位移时，本轮和下本人轮守、魔能力值各+1。")

    def on_unit_moved(self, battle: Battle, ctx: MoveContext) -> None:
        owner = self.owner
        if owner is None or ctx.unit.unit_id != owner.unit_id or ctx.start == ctx.end:
            return
        source_id = next((tag.removeprefix("source:") for tag in ctx.tags if tag.startswith("source:")), None)
        if source_id is None and battle.resolving_action is not None:
            source_id = battle.resolving_action.actor_id
        source = battle.units.get(source_id or "")
        if source is None or source.unit_id == owner.unit_id or source.player_id != owner.player_id:
            return
        existing = owner.get_status("安德鲁协同增益")
        if existing is None:
            owner.add_status(AndrewCooperationStatus())
            owner.current_mana = round(owner.current_mana + 1.0, 2)
        else:
            existing.duration = 2
        battle.log_public_event(f"{owner.name} 受友方位移，守和魔能力值各+1。", source=source, target=owner)


class AndrewCooperationStatus(StatModifierStatus):
    def __init__(self) -> None:
        super().__init__("安德鲁协同增益", defense_delta=1, mana_delta=1,
                         duration=2, tick_scope="owner_turn_end")

    def on_removed(self, battle: Battle) -> None:
        if self.owner is not None:
            self.owner.clamp_mana()


class GladiatorRoarStatus(StatModifierStatus):
    def __init__(self) -> None:
        super().__init__("咆哮守护", defense_delta=1, duration=1, tick_scope="any_turn_end")

    def redirect_queued_action(self, battle: Battle, queued_action: Any) -> None:
        owner = self.owner
        source = battle.units.get(queued_action.actor_id)
        payload = queued_action.payload
        if (owner is None or source is None or owner.position is None or owner.banished or not owner.alive
                or source.player_id == owner.player_id or payload.get("redirected_by_roar")
                or queued_action.action_type not in {"attack", "skill"}):
            return
        original_id = str(payload.get("target_unit_id") or "")
        original = battle.units.get(original_id)
        if (original is None or original.player_id != owner.player_id or original.position is None
                or original.banished or not original.alive
                or original.unit_id not in queued_action.target_unit_ids
                or min(cell.distance_to(other) for cell in battle.unit_cells(owner)
                       for other in battle.unit_cells(original)) > 5):
            return
        if queued_action.action_type == "attack":
            if battle.basic_attack_area_cells_for_payload(source, payload) is not None:
                return
            allowed_range = source.targeting_range()
        else:
            try:
                skill = source.get_skill(str(payload.get("skill_code") or ""))
            except ActionError:
                return
            if (skill.target_mode not in {"enemy", "unit"} or len(queued_action.target_unit_ids) != 1
                    or payload.get("cells") or skill.target_cells_are_geometry_only):
                return
            allowed_range = skill.direct_unit_target_range(battle, source, payload)
        declared = battle.declared_cell_for_target(source, owner, {})
        if declared is None:
            return
        payload["roar_original_target_unit_id"] = original_id
        payload["redirected_by_roar"] = owner.unit_id
        payload["target_unit_id"] = owner.unit_id
        payload["target_unit_ids"] = [owner.unit_id]
        payload["declared_target_x"], payload["declared_target_y"] = declared.x, declared.y
        if queued_action.action_type == "attack":
            payload["x"], payload["y"] = declared.x, declared.y
        queued_action.target_unit_ids = [owner.unit_id]
        queued_action.target_cells = battle.unit_cells(owner)
        queued_action.suppress_logs = queued_action.suppress_logs or owner.is_stealthed()
        origin = battle.declared_source_position(payload) or source.position
        source_cells = battle.unit_cells_at(source, origin) if origin is not None else []
        distance = min((cell.distance_to(other) for cell in source_cells
                        for other in battle.unit_cells(owner)), default=10_000)
        if distance > allowed_range:
            payload["action_failed_by_target_redirect"] = True
        battle.log_public_event(f"{owner.name} 的咆哮将{source.name}的【{queued_action.display_name}】改向自己。",
                                source=owner, target=owner)


class GladiatorRoarSkill(Skill):
    ai_reaction_simulation_bonus = 8.0

    ai_reaction_score_phase = "after_hidden_source"

    def ai_reaction_score(self, context: AIReactionScoreContext) -> float:
        return context.simulated_reaction(self, 8.0)

    def __init__(self) -> None:
        super().__init__("gladiator_roar", "咆哮", "敌方回合一次连锁；没有伤害；守+1至该敌英雄回合末，11×11内友方单体受体改向本人。",
                         max_uses_per_turn=1, timing="passive", target_mode="self")

    def can_react_to(self, battle: Battle, actor: Unit, queued_action: Any) -> tuple[bool, str]:
        ok, reason = super().can_react_to(battle, actor, queued_action)
        if not ok:
            return ok, reason
        if (battle.active_player == actor.player_id or queued_action.source_player_id == actor.player_id
                or queued_action.action_type not in {"attack", "skill"}):
            return False, "咆哮只能在敌方单体攻技的连锁中使用。"
        candidate = battle.units.get(str(queued_action.payload.get("target_unit_id") or ""))
        if (candidate is None or candidate.player_id != actor.player_id or candidate.position is None
                or candidate.banished or not candidate.alive
                or candidate.unit_id not in queued_action.target_unit_ids):
            return False, "咆哮只保护明确选择的己方单位。"
        if (not battle.unit_cells(actor) or min(cell.distance_to(other) for cell in battle.unit_cells(actor)
                                             for other in battle.unit_cells(candidate)) > 5):
            return False, "目标不在咆哮11×11范围。"
        if queued_action.action_type == "attack":
            source = battle.units.get(queued_action.actor_id)
            if source is None or battle.basic_attack_area_cells_for_payload(source, queued_action.payload) is not None:
                return False, "已选区域不能改向。"
        else:
            source = battle.units.get(queued_action.actor_id)
            skill = source.get_skill(str(queued_action.payload.get("skill_code") or "")) if source is not None else None
            if (skill is None or skill.target_mode not in {"enemy", "unit"}
                    or len(queued_action.target_unit_ids) != 1 or queued_action.payload.get("cells")
                    or skill.target_cells_are_geometry_only):
                return False, "已选区域不能改向。"
        return True, ""

    def reaction_preview(self, battle: Battle, actor: Unit, queued_action: Any) -> dict[str, Any]:
        return {"cells": [actor.position.to_dict()] if actor.position else [],
                "target_unit_ids": [actor.unit_id], "secondary_cells": [], "requires_target": False}

    def react(self, battle: Battle, actor: Unit, payload: dict[str, Any], queued_action: Any) -> None:
        existing = actor.get_status("咆哮守护")
        if existing is not None:
            actor.remove_status(existing, battle)
        actor.add_status(GladiatorRoarStatus())
        battle.log_public_event(f"{actor.name} 使用咆哮，本敌方回合守+1并保护附近友军。", source=actor)

    def execute(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> None:
        raise ActionError("咆哮只能在敌方连锁中使用。")


class BattleRotationSkill(Skill):
    requires_damage_choice = True

    def __init__(self) -> None:
        super().__init__("battle_rotation", "战斗轮转", "每次实际损血后可与一名在场己方单位交换位置。",
                         timing="passive", target_mode="ally")

    def allies(self, battle: Battle, actor: Unit) -> list[Unit]:
        return [unit for unit in battle.player_units(actor.player_id)
                if unit.unit_id != actor.unit_id and unit.position is not None and not unit.banished
                and gladiator_swap_destinations(battle, actor, unit) is not None]

    def can_react_to(self, battle: Battle, actor: Unit, queued_action: Any) -> tuple[bool, str]:
        return False, "战斗轮转在实际损血后选择。"

    def on_after_damage(self, battle: Battle, ctx: DamageContext) -> None:
        owner = self.owner
        if (owner is None or ctx.target.unit_id != owner.unit_id or ctx.actual_damage <= 0
                or not owner.alive or owner.position is None or owner.banished or owner.cannot_use_skills):
            return
        for effect in ([] if owner.direct_effects_blocked() else list(battle.field_effects)):
            if effect.blocks_skill_use(battle, owner, self)[0]:
                return
        for component in list(owner.iter_components()):
            if component.blocks_skill_use(battle, owner, self)[0]:
                return
        options = [unit.unit_id for unit in self.allies(battle, owner)]
        if not options:
            return
        choice = battle.damage_rotation_choice(ctx, options)
        ally = battle.units.get(choice or "")
        if ally is not None and ally.unit_id in options:
            if gladiator_swap(battle, owner, ally, skill_code="battle_rotation"):
                battle.log_public_event(f"{owner.name} 使用战斗轮转，转入新阵位。", source=owner, target=ally)

    def execute(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> None:
        raise ActionError("战斗轮转只能在实际损血后使用。")


class BlindManaMovementTrait(Trait):
    def __init__(self) -> None:
        super().__init__("以魔代步", "同回合可多次普通移动；每格消耗 1 魔，每次最远为当前魔的整数部分。")

    def modify_normal_move_distance(self, value: int) -> int:
        owner = self.owner
        return int(owner.current_mana) if owner is not None else 0

    def modify_normal_move_actions_per_turn(self, value: int) -> int:
        return 1_000_000

    def on_before_normal_move(self, battle: Battle, ctx: Any) -> None:
        owner = self.owner
        if owner is None or ctx.unit.unit_id != owner.unit_id:
            return
        cost = max(0, len(ctx.path) - 1)
        if owner.current_mana + 1e-9 < cost:
            raise ActionError("以魔代步的魔不足。")
        spent = owner.spend_mana(cost)
        if spent:
            battle.log(f"{owner.name} 以魔代步，消耗 {spent:g} 点魔。")


