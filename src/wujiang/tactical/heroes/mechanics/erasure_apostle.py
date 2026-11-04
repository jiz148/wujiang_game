"""Erasure Apostle battlefield objects."""

from __future__ import annotations

from wujiang.tactical.heroes.ai_candidate_plan import RANKED_CANDIDATES
from wujiang.tactical.heroes.ai_candidate_plan import TargetDestinationCandidates

from typing import Any
from typing import Optional
from wujiang.tactical.engine.army import is_army_soldier
from wujiang.tactical.engine.core import ActionError
from wujiang.tactical.engine.core import Battle
from wujiang.tactical.engine.core import DamageContext
from wujiang.tactical.engine.core import HeroUnit
from wujiang.tactical.engine.core import Position
from wujiang.tactical.engine.core import QueuedAction
from wujiang.tactical.engine.core import Skill
from wujiang.tactical.engine.core import Stats
from wujiang.tactical.engine.core import StatusEffect
from wujiang.tactical.engine.core import Trait
from wujiang.tactical.engine.core import Unit
from wujiang.tactical.heroes.ai_policy import AIReactionScoreContext, AISkillScoreContext, HeroAIPolicy
from wujiang.tactical.heroes.mechanics.simulated_effect_ai import SimulatedEffectDeltaScoring
from wujiang.tactical.rooms.ai_probe import ai_probe_rollback
from wujiang.tactical.heroes.base import AbstractHero
from wujiang.tactical.heroes.common import DrainManaSkill
from wujiang.tactical.heroes.common import InvincibleUntilActionStatus
from wujiang.tactical.heroes.common import StealthSkill
from wujiang.tactical.heroes.common import dedupe_positions
from wujiang.tactical.heroes.common import payload_position
from wujiang.tactical.heroes.common import payload_target_unit
from wujiang.tactical.heroes.common import positions_to_dict
from wujiang.tactical.heroes.mechanics.clone_split import SplitSkill


class ErasureAttackPolicy(HeroAIPolicy):
    def single_target_attack_bonus(self, battle: Battle, actor: Unit, target: Unit,
                                   payload: dict[str, Any], expected_damage: float) -> float:
        if expected_damage < target.current_hp - 1e-9:
            return 0.0
        score = min(target.current_mana, max(0.0, actor.max_mana() - actor.current_mana)) * 26.0
        if not target.is_clone and not target.is_summon and actor.get_skill("erasure").uses_this_battle > 0:
            score += 55.0
        return score


class ErasureCounterStatus(StatusEffect):
    def on_owner_removed(self, battle: Battle) -> None:
        if self.owner is not None:
            self.owner.remove_status(self, battle)

    def on_owner_banished(self, battle: Battle) -> None:
        self.on_owner_removed(battle)

    def __init__(self, source_unit_id: str) -> None:
        super().__init__("抹杀计数点", "可被放置者的【抹杀】移除并结算破魔扣血。")
        self.source_unit_id = source_unit_id

    def to_public_dict(self, battle: Battle) -> dict[str, Any]:
        data = super().to_public_dict(battle)
        data["source_unit_id"] = self.source_unit_id
        return data


def erasure_counter_count(unit: HeroUnit, source_unit_id: str | None = None) -> int:
    return sum(
        1
        for status in unit.statuses
        if isinstance(status, ErasureCounterStatus)
        and (source_unit_id is None or status.source_unit_id == source_unit_id)
    )


class ExtraStealthSkill(Skill):
    def ai_priority_score(self, context: AISkillScoreContext) -> float:
        return context.owned_effect_score(self)

    def ai_effect_score(self, context: AISkillScoreContext) -> float:
        battle, actor = context.battle, context.actor
        if actor.is_stealthed() or context.best_attack_score() > 0:
            return -1000.0
        return (42.0 if any(battle.distance_between_units(actor, enemy)
                            <= enemy.normal_move_distance() + enemy.targeting_range()
                            for enemy in context.hostile_units()) else -20.0)

    def __init__(self) -> None:
        super().__init__(
            "extra_stealth",
            "额外隐身",
            "大招：不费魔，进入隐身状态。",
            max_uses_per_battle=1,
            target_mode="self",
        )

    def can_use(
        self,
        battle: Battle,
        actor: HeroUnit,
        payload: Optional[dict[str, Any]] = None,
    ) -> tuple[bool, str]:
        ok, reason = super().can_use(battle, actor, payload)
        if not ok:
            return ok, reason
        if battle.unit_in_weather("沙尘", actor):
            return False, "沙尘天气中不能使用隐身。"
        return True, ""

    def execute(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> None:
        existing = actor.get_status("隐身")
        if existing is not None:
            actor.remove_status(existing, battle)
        actor.add_status(InvincibleUntilActionStatus())
        battle.log(f"{actor.name} 使用额外隐身进入隐身状态。")
        battle.clear_all_stealth_if_all_heroes_stealthed()

    def preview(self, battle: Battle, actor: HeroUnit) -> dict[str, Any]:
        return {
            "cells": [actor.position.to_dict()] if actor.position else [],
            "target_unit_ids": [actor.unit_id],
            "secondary_cells": [],
            "requires_target": False,
        }


class PrematureBurialSkill(Skill):
    ai_probe_hostile_effect = True
    def ai_priority_score(self, context: AISkillScoreContext) -> float:
        return context.owned_effect_score(self)

    def ai_effect_score(self, context: AISkillScoreContext) -> float:
        battle, actor, payload = context.battle, context.actor, context.payload
        target = battle.units.get(str(payload.get("target_unit_id") or ""))
        if target is None or target.player_id == actor.player_id:
            return -1000.0
        target = battle.effect_recipient(target)
        before = erasure_counter_count(target, actor.unit_id)
        with context.probe_rollback():
            self.execute(battle, actor, payload)
            if erasure_counter_count(target, actor.unit_id) <= before:
                return -1000.0
        finish = 35.0 if (before + 1) * 0.25 >= target.current_hp else 0.0
        anchor = 20.0 if erasure_counter_count(target) == 0 and actor.current_mana >= 1 else 0.0
        return 30.0 + finish + anchor

    def __init__(self) -> None:
        super().__init__(
            "premature_burial",
            "过早的埋葬",
            "普通技能：每回合最多 1 次，范 5 选择任意单位；命中后破魔放置 1 个抹杀计数点，可叠加。",
            max_uses_per_turn=1,
            target_mode="unit",
        )

    def direct_unit_target_range(
        self,
        battle: Battle,
        actor: HeroUnit,
        payload: Optional[dict[str, Any]] = None,
    ) -> int:
        return 5

    def targets(self, battle: Battle, actor: HeroUnit) -> list[HeroUnit]:
        if actor.position is None:
            return []
        return [
            unit
            for unit in battle.all_units()
            if unit.position is not None
            and not unit.banished
            and unit.alive
            and battle.unit_target_in_range_and_line(actor, unit, 5)
            and battle.unit_can_be_selected(unit, actor=actor)[0]
        ]  # type: ignore[list-item]

    def can_use(self, battle: Battle, actor: HeroUnit, payload: Optional[dict[str, Any]] = None) -> tuple[bool, str]:
        ok, reason = super().can_use(battle, actor, payload)
        if not ok:
            return ok, reason
        if payload:
            try:
                if payload_target_unit(battle, payload) not in self.targets(battle, actor):
                    return False, "目标不在埋葬的合法范5直线范围内。"
            except ActionError as exc:
                return False, str(exc)
        return True, ""

    def execute(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> None:
        target = payload_target_unit(battle, payload)
        if target not in self.targets(battle, actor):
            raise ActionError("目标超出过早的埋葬范围。")
        target = battle.effect_recipient(target)
        is_hostile = target.player_id != actor.player_id
        target_ctx = battle.validate_target(
            actor,
            target,
            action_name="过早的埋葬",
            is_skill=True,
            is_hostile=is_hostile,
            ignore_shield=True,
            tags={"erasure_counter"},
        )
        if target_ctx.cancelled:
            battle.log(target_ctx.reason)
            return
        if is_hostile and target.total_shields() > 0:
            target.consume_one_shield()
            battle.log_public_event(
                f"{target.name} 的 1 层护盾被【过早的埋葬】贯穿并打碎。",
                source=actor,
                target=target,
            )
        target.add_status(ErasureCounterStatus(actor.unit_id))
        battle.log(f"{actor.name} 在 {target.name} 身上放置了 1 个抹杀计数点。")

    def preview(self, battle: Battle, actor: HeroUnit) -> dict[str, Any]:
        targets = self.targets(battle, actor)
        return {
            "cells": positions_to_dict([cell for unit in targets for cell in battle.unit_cells(unit)]),
            "target_unit_ids": [unit.unit_id for unit in targets],
            "secondary_cells": [],
            "requires_target": True,
        }

    def ignores_shield_for_payload(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> bool:
        return True


class ErasureSkill(SimulatedEffectDeltaScoring, Skill):
    def ai_extra_effect_applies(self, target):
        return any(status.name == "抹杀计数点" for status in target.statuses)

    ai_probe_hostile_effect = True
    ai_effect_base_score = -12.0

    def ai_effect_targets(self, context: AISkillScoreContext) -> list[Unit]:
        return self.targets(context.battle, context.actor)

    def __init__(self) -> None:
        super().__init__(
            "erasure",
            "抹杀",
            "大招：移除场上所有由自己放置的抹杀计数点，并对对应单位造成每个计数点 1/4 血的破魔扣血。",
            max_uses_per_battle=1,
            target_mode="none",
        )

    def targets(self, battle: Battle, actor: HeroUnit) -> list[HeroUnit]:
        return [
            unit
            for unit in battle.all_units()
            if unit.position is not None
            and not unit.banished
            and erasure_counter_count(unit, actor.unit_id) > 0
        ]  # type: ignore[list-item]

    def can_use(
        self,
        battle: Battle,
        actor: HeroUnit,
        payload: Optional[dict[str, Any]] = None,
    ) -> tuple[bool, str]:
        ok, reason = super().can_use(battle, actor, payload)
        if not ok:
            return ok, reason
        if not self.targets(battle, actor):
            return False, "场上没有自己放置的抹杀计数点。"
        return True, ""

    def execute(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> None:
        targets = [(unit, erasure_counter_count(unit, actor.unit_id)) for unit in self.targets(battle, actor)]
        if not targets:
            battle.log(f"{actor.name} 没有可抹杀的计数点。")
            return
        for target, count in targets:
            for status in list(target.statuses):
                if isinstance(status, ErasureCounterStatus) and status.source_unit_id == actor.unit_id:
                    target.remove_status(status, battle)
            for _ in range(count):
                if not target.alive or target.position is None or target.banished:
                    break
                battle.resolve_damage(
                    DamageContext(
                        source=actor, target=target, attack_power=0, raw_damage=0.25,
                        is_skill=True, action_name="抹杀", ignore_shield=True,
                        tags={"skill", "erasure", "special_health_loss"},
                    )
                )

    def preview(self, battle: Battle, actor: HeroUnit) -> dict[str, Any]:
        targets = self.targets(battle, actor)
        return {
            "cells": positions_to_dict([cell for unit in targets for cell in battle.unit_cells(unit)]),
            "target_unit_ids": [unit.unit_id for unit in targets],
            "secondary_cells": [],
            "requires_target": False,
        }

    def get_target_units_for_payload(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> list[HeroUnit]:
        return self.targets(battle, actor)

    def get_target_cells_for_payload(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> list[Position]:
        return [Position(x, y) for y in range(battle.height) for x in range(battle.width)]

    def ignores_shield_for_payload(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> bool:
        return True


class DescentMomentAttackStatus(StatusEffect):
    def __init__(self, target_unit_id: str, target_name: str, normal_attack_cap: int) -> None:
        super().__init__("降临时刻", f"本回合额外2次普攻优先用于 {target_name}，普通普攻可攻击其他目标。",
                         duration=1, tick_scope="owner_turn_end")
        self.target_unit_id = target_unit_id
        self.normal_attack_cap = normal_attack_cap
        self.extra_attacks_used = 0

    def modify_attack_actions_per_turn(self, value: int) -> int:
        return value + 2

    def can_attack_target_with_payload(self, battle: Battle, actor: HeroUnit, target: HeroUnit,
                                       payload: Optional[dict[str, Any]] = None) -> tuple[bool, str]:
        if self.owner is None or actor.unit_id != self.owner.unit_id:
            return True, ""
        if payload and payload.get("descent_extra_declared"):
            return True, ""
        if target.unit_id == self.target_unit_id and self.extra_attacks_used < 2:
            return True, ""
        if actor.attacks_used - self.extra_attacks_used < self.normal_attack_cap:
            return True, ""
        return False, "普通普攻已用完；降临额外普攻只能攻击指定目标。"

    def on_owner_action_declared(self, battle: Battle, action_type: str, payload: dict[str, Any]) -> None:
        if action_type == "attack" and payload.get("target_unit_id") == self.target_unit_id and self.extra_attacks_used < 2:
            self.extra_attacks_used += 1
            payload["descent_extra_declared"] = True

    def on_basic_attack_finished(self, battle: Battle, actor: Unit, payload: dict[str, Any],
                                 damage_contexts: list[DamageContext], missed: bool) -> None:
        if payload.get("queued_resolution") or payload.get("reaction_attack"):
            return
        if not payload.get("descent_extra_declared") and self.extra_attacks_used < 2:
            if any(ctx.target.unit_id == self.target_unit_id for ctx in damage_contexts):
                self.extra_attacks_used += 1

    def to_public_dict(self, battle: Battle) -> dict[str, Any]:
        data = super().to_public_dict(battle)
        data.update(target_unit_id=self.target_unit_id, normal_attack_cap=self.normal_attack_cap,
                    extra_attacks_left=max(0, 2 - self.extra_attacks_used))
        return data


class DescentMomentSkill(TargetDestinationCandidates, Skill):
    ai_enemy_impact_requirement = False
    ai_candidate_plan = RANKED_CANDIDATES
    ai_destination_fields = ("dest_x", "dest_y")
    def ai_priority_score(self, context: AISkillScoreContext) -> float:
        return context.owned_effect_score(self)

    def ai_effect_score(self, context: AISkillScoreContext) -> float:
        battle, actor, payload = context.battle, context.actor, context.payload
        target = battle.units.get(str(payload.get("target_unit_id") or ""))
        if target is None or target.player_id == actor.player_id:
            return -1000.0
        recipient = battle.effect_recipient(target)
        hp, mana, alive = recipient.current_hp, actor.current_mana, recipient.alive
        with context.probe_rollback():
            try:
                self.execute(battle, actor, payload)
                for _ in range(2):
                    if not target.alive or not recipient.alive:
                        break
                    attack = {"target_unit_id": target.unit_id}
                    if not battle.attack_target_allowed(actor, target, payload=attack)[0]:
                        break
                    battle.basic_attack_with_payload(actor, target, attack)
            except ActionError:
                return -1000.0
            damage = hp - recipient.current_hp
            if damage <= 0:
                return -1000.0
            danger = sum(18.0 for enemy in context.hostile_units()
                         if battle.distance_between_units(actor, enemy) <= enemy.targeting_range()
                         and enemy.unit_id != target.unit_id)
            return (damage * 135.0 + (110.0 if alive and not recipient.alive else 0.0)
                    + max(0.0, actor.current_mana - mana) * 24.0 - 25.0 - danger
                    - (30.0 if mana < 1.5 else 0.0))

    requires_direct_unit_target_line = False

    def __init__(self) -> None:
        super().__init__(
            "descent_moment",
            "降临时刻",
            "普通技能：费 1 魔，每回合最多 1 次；瞬移到一个带有抹杀计数点的对方单位周围，本回合额外获得 2 次只能攻击该目标的普攻。",
            mana_cost=1,
            max_uses_per_turn=1,
            target_mode="unit",
        )

    def targets(self, battle: Battle, actor: HeroUnit) -> list[HeroUnit]:
        return [
            unit
            for unit in battle.enemy_units(actor.player_id)
            if unit.position is not None
            and not unit.banished
            and erasure_counter_count(unit) > 0
            and battle.unit_can_be_selected(unit, actor=actor)[0]
        ]  # type: ignore[list-item]

    def legal_destinations(self, battle: Battle, actor: HeroUnit, target: HeroUnit) -> list[Position]:
        if actor.position is None or actor.cannot_move or battle.mounted_unit_for(actor) is not None:
            return []
        cells = dedupe_positions(
            [
                neighbor
                for cell in battle.unit_cells(target)
                for neighbor in battle.neighbors(cell)
            ]
        )
        return [cell for cell in cells if cell != actor.position and battle.can_place_unit(actor, cell, ignore=actor, mover=actor)]

    def target_from_payload(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> HeroUnit:
        if not payload.get("target_unit_id"):
            raise ActionError("降临时刻需要选择目标。")
        target = payload_target_unit(battle, payload)
        if target not in self.targets(battle, actor):
            raise ActionError("降临时刻只能选择带有抹杀计数点的对方单位。")
        return target

    def destination_from_payload(
        self,
        battle: Battle,
        actor: HeroUnit,
        target: HeroUnit,
        payload: dict[str, Any],
    ) -> Position:
        legal = self.legal_destinations(battle, actor, target)
        if not legal:
            raise ActionError("目标周围没有合法落点。")
        if payload.get("dest_x") is not None and payload.get("dest_y") is not None:
            destination = payload_position(payload, "dest_x", "dest_y")
            if destination not in legal:
                raise ActionError("该位置不能作为降临时刻的落点。")
            return destination
        actor_cells = battle.unit_cells(actor)
        return min(
            legal,
            key=lambda cell: (
                min((origin.distance_to(cell) for origin in actor_cells), default=0),
                cell.y,
                cell.x,
            ),
        )

    def can_use(self, battle: Battle, actor: HeroUnit, payload: Optional[dict[str, Any]] = None) -> tuple[bool, str]:
        ok, reason = super().can_use(battle, actor, payload)
        if not ok:
            return ok, reason
        if not any(self.legal_destinations(battle, actor, unit) for unit in self.targets(battle, actor)):
            return False, "没有可降临的目标与合法落点。"
        if payload:
            try:
                target = self.target_from_payload(battle, actor, payload)
                self.destination_from_payload(battle, actor, target, payload)
            except ActionError as exc:
                return False, str(exc)
        return True, ""

    def queued_payload_metadata(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> dict[str, Any]:
        target = self.target_from_payload(battle, actor, payload)
        destination = self.destination_from_payload(battle, actor, target, payload)
        return {"dest_x": destination.x, "dest_y": destination.y}

    def execute(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> None:
        target = self.target_from_payload(battle, actor, payload)
        destination = self.destination_from_payload(battle, actor, target, payload)
        existing = actor.get_status("降临时刻")
        if existing is not None:
            actor.remove_status(existing, battle)
        normal_attack_cap = actor.attack_actions_per_turn()
        battle.move_unit(
            actor,
            destination,
            via_skill=True,
            allow_anywhere=True,
            max_distance=99,
            tags={"descent_moment"},
        )
        actor.add_status(DescentMomentAttackStatus(target.unit_id, target.name, normal_attack_cap))
        battle.log(f"{actor.name} 降临到 {target.name} 周围，本回合获得 2 次针对该目标的额外普攻。")

    def preview(self, battle: Battle, actor: HeroUnit) -> dict[str, Any]:
        targets = self.targets(battle, actor)
        destinations_by_target = {
            unit.unit_id: positions_to_dict(self.legal_destinations(battle, actor, unit))
            for unit in targets
        }
        return {
            "cells": positions_to_dict([cell for unit in targets for cell in battle.unit_cells(unit)]),
            "target_unit_ids": [unit.unit_id for unit in targets if destinations_by_target.get(unit.unit_id)],
            "secondary_cells": [],
            "requires_target": True,
            "destinations_by_target": destinations_by_target,
        }

    def get_target_units_for_payload(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> list[HeroUnit]:
        return [self.target_from_payload(battle, actor, payload)]

    def get_target_cells_for_payload(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> list[Position]:
        if payload.get("dest_x") is None or payload.get("dest_y") is None:
            return []
        return [payload_position(payload, "dest_x", "dest_y")]


class ShadowCounterSkill(Skill):
    ai_reaction_score_phase = "after_hidden_source"

    def __init__(self) -> None:
        super().__init__(
            "shadow_counter",
            "暗影反击",
            "被动技能：费 0.5 魔，连锁速度 2；自己被敌方普攻或技能影响时普通移动 2 格，然后对原位置周围 5*5 不含原位置的对方单位放置抹杀计数点。",
            mana_cost=0.5,
            timing="passive",
            target_mode="cell",
        )

    def execute(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> None:
        raise ActionError("暗影反击只能通过连锁使用。")

    def retreat_paths(self, battle: Battle, actor: HeroUnit) -> dict[Position, list[Position]]:
        if actor.position is None or actor.cannot_move or actor.cannot_normal_move:
            return {}
        paths: dict[Position, list[Position]] = {}
        for middle in battle.neighbors(actor.position):
            if not battle.terrain_step_allowed(actor, actor.position, middle) or not battle.can_place_unit(actor, middle, ignore=actor, mover=actor):
                continue
            for end in battle.neighbors(middle):
                if (end != actor.position and battle.terrain_step_allowed(actor, middle, end)
                        and battle.can_place_unit(actor, end, ignore=actor, mover=actor)):
                    paths.setdefault(end, [middle, end])
        return paths

    def retreat_cells(self, battle: Battle, actor: HeroUnit) -> list[Position]:
        return sorted(self.retreat_paths(battle, actor), key=lambda cell: (cell.y, cell.x))

    def can_react_with_payload(self, battle: Battle, actor: HeroUnit, queued_action: QueuedAction,
                               payload: Optional[dict[str, Any]] = None) -> tuple[bool, str]:
        ok, reason = super().can_react_with_payload(battle, actor, queued_action, payload)
        if not ok:
            return ok, reason
        if payload and payload_position(payload) not in self.retreat_paths(battle, actor):
            return False, "暗影反击需要恰好两步且不穿过单位的合法路径。"
        return True, ""

    def queued_reaction_payload_metadata(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> dict[str, Any]:
        destination = payload_position(payload)
        paths = self.retreat_paths(battle, actor)
        if destination not in paths or actor.position is None:
            raise ActionError("没有合法暗影反击路径。")
        return {"shadow_origin": actor.position.to_dict(),
                "shadow_path": positions_to_dict(paths[destination]),
                "reaction_effect_cells": positions_to_dict(self.affected_cells(battle, actor.position))}

    def affected_cells(self, battle: Battle, origin: Position) -> list[Position]:
        cells = [
            Position(origin.x + dx, origin.y + dy)
            for dx in range(-2, 3)
            for dy in range(-2, 3)
            if not (dx == 0 and dy == 0)
        ]
        return [cell for cell in cells if battle.in_bounds(cell)]

    def can_react_to(self, battle: Battle, actor: HeroUnit, queued_action: QueuedAction) -> tuple[bool, str]:
        ok, reason = super().can_react_to(battle, actor, queued_action)
        if not ok:
            return ok, reason
        if queued_action.source_player_id == actor.player_id:
            return False, "暗影反击只能响应敌方动作。"
        if queued_action.action_type not in {"attack", "skill", "skill_effect"}:
            return False, "暗影反击只能响应敌方普攻或技能。"
        if battle.reaction_proxy_target(actor, queued_action) is None:
            return False, "当前动作没有影响到自己。"
        if not self.retreat_cells(battle, actor):
            return False, "没有可用于暗影反击的落点。"
        return True, ""

    def react(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any], queued_action: QueuedAction) -> None:
        locked = payload if "shadow_origin" in payload else self.queued_reaction_payload_metadata(battle, actor, payload)
        destination = payload_position(payload)
        path = battle.payload_positions(locked, "shadow_path")
        origin_data = locked["shadow_origin"]
        origin = Position(int(origin_data["x"]), int(origin_data["y"]))
        if actor.position == origin and not actor.cannot_move and not actor.cannot_normal_move and all(
            battle.terrain_step_allowed(actor, previous, step)
            and battle.can_place_unit(actor, step, ignore=actor, mover=actor)
            for previous, step in zip([origin, *path[:-1]], path)
        ):
            battle.move_unit(actor, destination, path=path, via_skill=True, exact_distance=2,
                             triggered_by_reaction=True, max_distance=2, tags={"shadow_counter"})
        else:
            battle.log(f"{actor.name} 的暗影反击位移受阻，仍结算原位置的计数点。")
        affected = battle.payload_positions(locked, "reaction_effect_cells")
        targets = [
            unit
            for unit in battle.effect_units_at_cells(affected)
            if unit.player_id != actor.player_id
        ]
        for target in targets:
            target_ctx = battle.validate_target(
                actor,
                target,
                action_name="暗影反击",
                is_skill=True,
                is_hostile=True,
                ignore_targeting_restrictions=True,
                tags={"erasure_counter", "shadow_counter"},
            )
            if target_ctx.cancelled:
                battle.log(target_ctx.reason)
                continue
            target.add_status(ErasureCounterStatus(actor.unit_id))
            battle.log(f"{actor.name} 的暗影反击在 {target.name} 身上放置了 1 个抹杀计数点。")

    def reaction_preview(self, battle: Battle, actor: HeroUnit, queued_action: QueuedAction) -> dict[str, Any]:
        return {
            "cells": positions_to_dict(self.retreat_cells(battle, actor)),
            "target_unit_ids": [],
            "secondary_cells": [actor.position.to_dict()] if actor.position else [],
            "requires_target": True,
        }

    def ai_reaction_score(self, context: AIReactionScoreContext) -> float:
        battle, reactor, queued_action = (context.battle, context.actor,
                                          context.queued_action)
        threat = context.threat(context.proxy_target())
        destination = context.destination()
        if destination is None:
            return -1000.0
        cells = self.affected_cells(battle, reactor.position) if reactor.position else []
        marks = 0
        with ai_probe_rollback(battle):
            for unit in battle.effect_units_at_cells(cells):
                if unit.player_id == reactor.player_id:
                    continue
                ctx = battle.validate_target(reactor, unit, action_name=self.name, is_skill=True,
                                             is_hostile=True, ignore_targeting_restrictions=True)
                if not ctx.cancelled:
                    marks += 1
        escape = not context.destination_still_targeted(destination)
        skill_context = context.skill_context(self.code)
        return ((threat + 25.0 if escape else -35.0) + marks * 24.0
                + skill_context.move_score(destination, skill_context.hero_style()) * 0.25 - 12.0)


class ErasureApostleDestroyRewardTrait(Trait):
    def __init__(self) -> None:
        super().__init__("抹杀回收", "自己用攻击或技能破坏单位后获得该单位剩余魔；破坏武将后重置【抹杀】。")

    def on_after_damage(self, battle: Battle, ctx: DamageContext) -> None:
        owner = self.owner
        if owner is None or ctx.source is None or ctx.source.unit_id != owner.unit_id:
            return
        if ctx.cancelled or ctx.target.alive or ctx.from_field_effect or not (ctx.is_skill or "attack" in ctx.tags):
            return
        if "erasure_rewarded" in ctx.tags:
            return
        ctx.tags.add("erasure_rewarded")
        remaining_mana = max(0.0, float(ctx.target.current_mana))
        if remaining_mana > 0:
            gained = owner.gain_mana(remaining_mana)
            if gained > 0:
                battle.log(f"{owner.name} 破坏单位后回收了 {gained} 点魔。")
        if isinstance(ctx.target, HeroUnit) and not ctx.target.is_summon and not ctx.target.is_clone and not is_army_soldier(ctx.target):
            skill = owner.skill_map().get("erasure")
            if skill is not None:
                skill.uses_this_battle = 0
                skill.cooldown_remaining = 0
                battle.log(f"{owner.name} 破坏武将，重置了【抹杀】。")


class ErasureApostle(AbstractHero):
    hero_code = "erasure_apostle"
    ai_policy = ErasureAttackPolicy()
    hero_name = "抹杀的使徒"
    role = "刺客"
    attribute = "暗"
    race = "人类"
    level = 5
    base_stats = Stats(attack=4, defense=1, speed=4, attack_range=1, mana=4)
    raw_skill_text = "隐身 分身 ￥额外隐身 吸魔 过早的埋葬（范5；回合一次；将一个抹杀计数点放置在一个单位上；破魔） ¥抹杀（将场上所有单位上的此单位放置的所有抹杀计数点去掉，并对被去掉计数点单位造成计数点*1/4的破魔血量伤害） 【1降临时刻（一回合一次；瞬移到一个对方被放置抹杀计数点的单位周围；此回合可以额外对那个单位攻击2次） 【0.5 暗影反击（被动技能；移动两格；对原本位置周围5*5的所有对方单位放置一个抹杀计数点）"
    raw_trait_text = "破坏一个单位后魔+那个单位剩余的魔；破坏一个武将后重置“抹杀”"

    def build_skills(self) -> list[Skill]:
        return [
            StealthSkill(),
            SplitSkill(),
            ExtraStealthSkill(),
            DrainManaSkill(),
            PrematureBurialSkill(),
            ErasureSkill(),
            DescentMomentSkill(),
            ShadowCounterSkill(),
        ]

    def build_traits(self) -> list[Trait]:
        return [ErasureApostleDestroyRewardTrait()]


