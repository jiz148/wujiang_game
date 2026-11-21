"""R365 Shadow God king: guarded damage, triple raid, and life reversal."""

from __future__ import annotations

from typing import Any

from wujiang.tactical.engine.core import (
    ActionError, Battle, BattleFieldEffect, DamageContext, HealContext, Position,
    QueuedAction, Skill, StatusEffect, Trait, Unit,
)
from wujiang.tactical.heroes.common import payload_direction, payload_position, positions_to_dict
from wujiang.tactical.heroes.mechanics.clone_split import StandardCloneSummon, maybe_swap_new_clones


_DIRECTIONS = ((-1, -1), (-1, 0), (-1, 1), (0, -1), (0, 1),
               (1, -1), (1, 0), (1, 1))


def _present(unit: Unit | None) -> bool:
    return bool(unit is not None and unit.alive and not unit.banished and unit.position is not None)


class ShadowGuardReduction(StatusEffect):
    def __init__(self, declaration_id: str | None) -> None:
        super().__init__("影的加护减伤", "此声明的下一次实际伤害-1。", duration=None)
        self.declaration_id = declaration_id

    def _matches(self, battle: Battle) -> bool:
        action = battle.resolving_action
        return action is not None and action.payload.get("declaration_id") == self.declaration_id

    def on_before_hp_loss(self, battle: Battle, ctx: DamageContext) -> None:
        if ctx.target is self.owner and self._matches(battle):
            ctx.raw_damage = max(0.0, ctx.raw_damage - 1.0)
            self.owner.remove_status(self, battle)

    def on_damage_cancelled(self, battle: Battle, ctx: DamageContext) -> None:
        if ctx.target is self.owner and self._matches(battle):
            self.owner.remove_status(self, battle)

    def on_any_turn_end(self, battle: Battle, ended_player_id: int) -> None:
        if self.owner is not None:
            self.owner.remove_status(self, battle)


class ShadowGuardSkill(Skill):
    def __init__(self) -> None:
        super().__init__("shadow_guard", "影的加护", "受伤时随时连锁，本实际回合首用免费，当前伤害-1。",
                         mana_cost=1, timing="instant", target_mode="self")

    def mana_cost_for_payload(self, battle: Battle, actor: Unit,
                              payload: dict[str, Any] | None = None) -> float:
        return 0.0 if self.uses_this_turn == 0 else super().mana_cost_for_payload(battle, actor, payload)

    def can_use(self, battle: Battle, actor: Unit,
                payload: dict[str, Any] | None = None) -> tuple[bool, str]:
        return False, "影的加护仅能在本人将受伤时连锁使用。"

    def can_react_to(self, battle: Battle, actor: Unit,
                     queued_action: QueuedAction) -> tuple[bool, str]:
        ok, reason = super().can_react_to(battle, actor, queued_action)
        if not ok:
            return ok, reason
        if (queued_action.action_type not in {"attack", "skill", "skill_effect"}
                or (actor.unit_id not in queued_action.target_unit_ids
                    and actor not in battle.effect_units_at_cells(queued_action.target_cells))):
            return False, "仅能在本人将受该动作伤害时使用。"
        if not battle.attack_would_deal_damage(queued_action):
            return False, "当前动作不会造成实际伤害。"
        return True, ""

    def react(self, battle: Battle, actor: Unit, payload: dict[str, Any],
              queued_action: QueuedAction) -> None:
        actor.add_status(ShadowGuardReduction(queued_action.payload.get("declaration_id")), source=actor)
        battle.log_public_event(f"{actor.name} 用影的加护抵消本次1点伤害。", source=actor, target=actor)

    def execute(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> None:
        raise ActionError("影的加护只能连锁使用。")

    def reaction_preview(self, battle: Battle, actor: Unit,
                         queued_action: QueuedAction) -> dict[str, Any]:
        return {"cells": positions_to_dict(battle.unit_cells(actor)),
                "target_unit_ids": [actor.unit_id], "secondary_cells": [], "requires_target": False}

    def ai_reaction_score(self, context: Any) -> float:
        return min(100.0, context.threat(context.actor) + 20.0)


class ShadowFriendlyLossField(BattleFieldEffect):
    def __init__(self, source_unit_id: str) -> None:
        super().__init__("影神血逆转", "源周围9×9内己方效果扣血变为治疗。")
        self.source_unit_id = source_unit_id

    def source(self, battle: Battle) -> Unit | None:
        unit = battle.units.get(self.source_unit_id)
        return unit if _present(unit) else None

    def on_before_hp_loss(self, battle: Battle, ctx: DamageContext) -> None:
        owner = self.source(battle)
        if (owner is None or ctx.source is None or ctx.source.player_id != owner.player_id
                or ctx.target.player_id != owner.player_id or ctx.target.position is None
                or battle.distance_between_units(owner, ctx.target) > 4
                or ctx.raw_damage <= 0):
            return
        amount = ctx.raw_damage
        ctx.raw_damage = 0.0
        battle.heal(HealContext(source=ctx.source, target=ctx.target, amount=amount,
                                action_name="影神血逆转", direct_target=True, effect_source=self))

    def on_before_heal(self, battle: Battle, ctx: HealContext) -> None:
        owner = self.source(battle)
        if (owner is not None and owner.has_status("影神召唤禁疗")
                and ctx.target.player_id != owner.player_id):
            ctx.amount = 0.0


class ShadowSummonedNoHealStatus(StatusEffect):
    def __init__(self) -> None:
        super().__init__("影神召唤禁疗", "被召唤后2本人轮，敌方所有单位不能回复。",
                         duration=2, tick_scope="owner_turn_end")


class ShadowFriendlyLossTrait(Trait):
    converts_friendly_hp_loss = True

    def __init__(self) -> None:
        super().__init__("影神血逆转", "本人周围9×9中因己方效果的扣血全部改为等量治疗。")
        self.field: ShadowFriendlyLossField | None = None

    def on_enter_battle(self, battle: Battle) -> None:
        self._install(battle)

    def on_restored_by_apocalypse(self, battle: Battle) -> None:
        self._install(battle)

    def _install(self, battle: Battle) -> None:
        if self.owner is None or self.field in battle.field_effects:
            return
        self.field = ShadowFriendlyLossField(self.owner.unit_id)
        battle.add_field_effect(self.field, source=self.owner)

    def on_owner_removed(self, battle: Battle) -> None:
        if self.field in battle.field_effects:
            battle.remove_field_effect(self.field)
        self.field = None


class ShadowFullHealthSurvivalTrait(Trait):
    def __init__(self) -> None:
        super().__init__("满血影神不灭", "满血受致命伤时留1/4最大生命。")

    def lethal_damage_remaining_hp(self, battle: Battle, ctx: DamageContext) -> float | None:
        owner = self.owner
        if (owner is not None and ctx.target is owner
                and owner.current_hp >= owner.max_health - 1e-9):
            return owner.max_health / 4
        return None


class ShadowSummonedNoHealTrait(Trait):
    def __init__(self) -> None:
        super().__init__("召唤后禁疗", "本人被真正召回后的2轮对方所有单位不能回复。")

    def on_restored_by_apocalypse(self, battle: Battle) -> None:
        owner = self.owner
        if owner is not None:
            owner.add_status(ShadowSummonedNoHealStatus(), source=owner)


class ShadowRaidSkill(Skill):
    is_displacement_skill = True
    moves_owner_on_use = True
    target_cells_are_geometry_only = True
    ai_payload_value_owned = True
    ai_enemy_impact_requirement = False
    requires_damage_choice = True
    ai_optional_choice_owned = True

    def __init__(self) -> None:
        super().__init__("shadow_raid", "影神力。影袭",
                         "每本人轮一次，直线移动4格，再向一个方向独立攻击3次；挡住或格挡可造分身。",
                         max_uses_per_turn=1, target_mode="cell")

    def available_cells(self, battle: Battle, actor: Unit) -> list[Position]:
        if actor.position is None or actor.cannot_move:
            return []
        return [cell for cell in battle.reachable_positions(
            actor, max_distance=4, exact_distance=4, straight_only=True)
            if (abs(cell.x - actor.position.x), abs(cell.y - actor.position.y)) in {(4, 0), (0, 4)}]

    def _aim(self, battle: Battle, actor: Unit,
             payload: dict[str, Any]) -> tuple[Position, tuple[int, int]]:
        cell = payload_position(payload)
        direction = payload_direction(payload)
        if cell not in self.available_cells(battle, actor) or direction not in _DIRECTIONS:
            raise ActionError("影袭需要合法四格直线落点与攻击方向。")
        return cell, direction

    def get_target_units_for_payload(self, battle: Battle, actor: Unit,
                                     payload: dict[str, Any]) -> list[Unit]:
        return []

    def execute(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> None:
        cell, direction = self._aim(battle, actor, payload)
        battle.move_unit(actor, cell, via_skill=True, straight_only=True,
                         max_distance=4, exact_distance=4, tags={self.code})
        self._queue_hit(battle, actor, direction, 1)

    def _queue_hit(self, battle: Battle, actor: Unit,
                   direction: tuple[int, int], index: int) -> None:
        if not _present(actor):
            return
        cells = battle.line_positions(actor.position, direction, actor.targeting_range())
        targets = battle.effect_units_at_cells(cells, ignore=actor)
        battle.queue_skill_effect_action(
            actor=actor, display_name=f"影袭第{index}击", effect_code="shadow_raid_hit",
            target_units=targets, target_cells=cells, include_cell_units=False,
            payload={"direction": {"dx": direction[0], "dy": direction[1]},
                     "shadow_raid_reactors": []},
            segment_index=index, segment_count=3, effect_resolver=self.resolve_hit,
        )

    def on_action_chained(self, battle: Battle, declared_action: QueuedAction,
                          reaction: QueuedAction) -> None:
        if declared_action.payload.get("effect_code") != "shadow_raid_hit":
            return
        reactor = battle.units.get(reaction.actor_id)
        code = reaction.payload.get("action_code") or reaction.payload.get("skill_code")
        used = reactor.skill_map().get(code) if reactor is not None else None
        if code == "block" or (used is not None and used.timing in {"passive", "reaction"}):
            declared_action.payload.setdefault("shadow_raid_reactors", []).append(reaction.actor_id)

    def resolve_hit(self, battle: Battle, actor: Unit, queued: QueuedAction) -> None:
        direction = payload_direction(queued.payload)
        targets = [unit for unit in battle.effect_units_at_cells(queued.target_cells, ignore=actor)
                   if _present(unit) and unit.player_id != actor.player_id]
        target = targets[0] if targets else None
        if target is not None:
            ctx = battle.resolve_damage(DamageContext(
                source=actor, target=target, attack_power=actor.stat("attack"),
                is_skill=True, action_name=queued.display_name,
                tags={"skill", self.code, "attack"},
            ))
            if (ctx.actual_damage <= 0 and _present(target)
                    and (target.unit_id in queued.payload.get("shadow_raid_reactors", [])
                         or "complete_block" in ctx.modifier_receipts)):
                self._spawn_clone(battle, actor, target)
        if int(queued.payload.get("segment_index", 0)) < 3:
            self._queue_hit(battle, actor, direction, int(queued.payload["segment_index"]) + 1)

    def _spawn_clone(self, battle: Battle, actor: Unit, target: Unit) -> None:
        if target.position is None:
            return
        probe = StandardCloneSummon(actor.player_id, actor)
        cells = [cell for cell in battle.neighbors(target.position)
                 if battle.can_place_unit(probe, cell, mover=probe)]
        if not cells:
            return
        options = [f"{cell.x},{cell.y}" for cell in cells]
        choice = battle.optional_position_choice(
            actor, options, action_name=f"影袭：在{target.name}周围放置分身",
            kind="required_placement", choice_context={"target_unit_id": target.unit_id})
        if choice not in options:
            return
        x, y = (int(part) for part in choice.split(",", 1))
        clone = StandardCloneSummon(actor.player_id, actor)
        battle.summon_unit(clone, Position(x, y), summoner=actor)
        maybe_swap_new_clones(battle, actor, [clone])

    def ai_optional_choice_score(self, battle: Battle, actor: Unit,
                                 prompt: dict[str, Any], choice: str) -> float | None:
        if prompt.get("kind") != "required_placement" or not str(
                prompt.get("action_name") or "").startswith("影袭："):
            return None
        try:
            x, y = (int(part) for part in choice.split(",", 1))
        except (ValueError, AttributeError):
            return -1000.0
        return 35.0 - min((Position(x, y).distance_to(enemy.position)
                           for enemy in battle.enemy_units(actor.player_id) if enemy.position), default=8) * 3.0

    def preview(self, battle: Battle, actor: Unit) -> dict[str, Any]:
        cells = self.available_cells(battle, actor)
        return {"cells": positions_to_dict(cells), "target_unit_ids": [],
                "secondary_cells": positions_to_dict(battle.unit_cells(actor)),
                "requires_target": True,
                "selection": {"mode": "cell_direction", "directions": [
                    {"dx": dx, "dy": dy, "label": f"{dx:+d},{dy:+d}"} for dx, dy in _DIRECTIONS]}}

    def ai_payload_candidates(self, battle: Battle, actor: Unit,
                              action: dict[str, Any]) -> list[dict[str, Any]]:
        return [{"type": "skill", "unit_id": actor.unit_id, "skill_code": self.code,
                 "x": cell.x, "y": cell.y, "direction": {"dx": dx, "dy": dy}}
                for cell in self.available_cells(battle, actor) for dx, dy in _DIRECTIONS]

    def ai_payload_value(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> float:
        try:
            cell, direction = self._aim(battle, actor, payload)
        except (ActionError, TypeError, ValueError):
            return -1000.0
        cells = battle.line_positions(cell, direction, actor.targeting_range())
        enemies = [unit for unit in battle.effect_units_at_cells(cells)
                   if unit.player_id != actor.player_id]
        return sum(min(1.0, unit.current_hp) * 120.0 for unit in enemies) - 28.0

    def ai_priority_score(self, context: Any) -> float:
        return self.ai_payload_value(context.battle, context.actor, context.payload)
