"""R034 floating cannon summon and protection mechanics."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any
if TYPE_CHECKING:
    from wujiang.tactical.heroes.ai_policy import AIReactionScoreContext
    from wujiang.tactical.heroes.ai_policy import AISkillScoreContext
from wujiang.tactical.prediction import expected_local
from wujiang.tactical.engine.core import ActionError
from wujiang.tactical.engine.core import Battle
from wujiang.tactical.engine.core import DamageContext
from wujiang.tactical.engine.core import HeroUnit
from wujiang.tactical.engine.core import Position
from wujiang.tactical.engine.core import Skill
from wujiang.tactical.engine.core import Stats
from wujiang.tactical.engine.core import StatusEffect
from wujiang.tactical.engine.core import TargetContext
from wujiang.tactical.engine.core import Trait
from wujiang.tactical.heroes.base import AbstractHero
from wujiang.tactical.heroes.common import StatModifierStatus
from wujiang.tactical.heroes.common import payload_position
from wujiang.tactical.heroes.mechanics.next_five_geometry import position_key
from wujiang.tactical.heroes.mechanics.next_five_geometry import square_around_cells


class FloatingCannonBerserkStatus(StatusEffect):
    def __init__(self) -> None:
        super().__init__("浮游炮狂暴化", "浮游炮攻 +2，速 +2，范 -2，攻 2 次，且只能攻击最近敌方单位。", duration=None)


class FloatingCannonsActiveStatus(StatusEffect):
    def __init__(self, cannon_ids: list[str]) -> None:
        super().__init__("浮游炮展开", "樱火已展开四炮；真正被破坏的原炮会在樱火下回合开始时按原身份补回。", duration=None)
        self.cannon_ids = list(cannon_ids)

    def to_public_dict(self, battle: Battle) -> dict[str, Any]:
        data = super().to_public_dict(battle)
        owner = self.owner
        trait = next((item for item in owner.traits if isinstance(item, SakuraFloatingCannonTrait)), None) if owner is not None else None
        data["pending_cannon_count"] = len(trait.pending_cannons) if trait is not None else 0
        return data


class FloatingCannonBuffStatus(StatModifierStatus):
    def __init__(self) -> None:
        super().__init__("浮游炮狂暴", attack_delta=2, speed_delta=2, range_delta=-2, description="攻 +2，速 +2，范 -2，攻击次数变 2。")

    def modify_attack_actions_per_turn(self, value: int) -> int:
        return max(value, 2)


class FloatingCannonStatTrait(Trait):
    def __init__(self) -> None:
        super().__init__("浮游炮狂暴属性", "狂暴化时攻 +2，速 +2，范 -2，攻击次数变 2。")

    def bind(self, owner: HeroUnit) -> "FloatingCannonStatTrait":
        super().bind(owner)
        owner.magic_immunity = True
        return self

    def _berserk(self, battle: Battle) -> bool:
        owner = self.owner
        if owner is None or owner.summoner_id is None:
            return False
        summoner = battle.units.get(owner.summoner_id)
        return bool(summoner is not None and summoner.get_status("浮游炮狂暴化") is not None)

    def modify_stat(self, stat_name: str, value: float) -> float:
        return value

    def modify_attack_actions_per_turn(self, value: int) -> int:
        return value

    def nearest_targets(self, battle: Battle, actor: HeroUnit) -> list[HeroUnit]:
        visible = [unit for unit in battle.enemy_units(actor.player_id)
                   if unit.alive and unit.position is not None and not unit.banished
                   and battle.unit_can_be_selected(unit, actor=actor)[0]]
        if not visible:
            return []
        nearest = min(battle.distance_between_units(actor, unit) for unit in visible)
        return [unit for unit in visible if battle.distance_between_units(actor, unit) == nearest]

    def can_fire_at(self, battle: Battle, actor: HeroUnit, target: HeroUnit, origin: Position) -> bool:
        return any(battle.cells_are_straight_aligned(cell, enemy_cell)
                   and cell.distance_to(enemy_cell) <= actor.targeting_range()
                   for cell in battle.unit_cells_at(actor, origin)
                   for enemy_cell in battle.unit_cells(target))

    def route_to(self, battle: Battle, actor: HeroUnit, target: HeroUnit) -> list[Position]:
        if actor.position is None:
            return []
        if self.can_fire_at(battle, actor, target, actor.position):
            return [actor.position]
        max_distance = battle.width * battle.height * 5
        reachable = battle.reachable_positions(actor, max_distance=max_distance, use_movement_cost=True)
        firing = [position for position in reachable if self.can_fire_at(battle, actor, target, position)]
        if firing:
            farthest = max(min(cell.distance_to(enemy_cell) for cell in battle.unit_cells_at(actor, position)
                                for enemy_cell in battle.unit_cells(target)) for position in firing)
            destinations = [position for position in firing
                            if min(cell.distance_to(enemy_cell) for cell in battle.unit_cells_at(actor, position)
                                   for enemy_cell in battle.unit_cells(target)) == farthest]
        else:
            if not reachable:
                return []
            closest = min(min(cell.distance_to(enemy_cell) for cell in battle.unit_cells_at(actor, position)
                              for enemy_cell in battle.unit_cells(target)) for position in reachable)
            if closest >= battle.distance_between_units(actor, target):
                return []
            destinations = [position for position in reachable
                            if min(cell.distance_to(enemy_cell) for cell in battle.unit_cells_at(actor, position)
                                   for enemy_cell in battle.unit_cells(target)) == closest]
        routes = [battle.find_path(actor, position, max_distance=max_distance, use_movement_cost=True)
                  for position in destinations]
        return min(routes, key=lambda route: (sum(battle.normal_movement_step_cost(actor, start, end)
                                                   for start, end in zip(route, route[1:])),
                                               len(route), route[-1].y, route[-1].x))

    def action_route(self, battle: Battle, actor: HeroUnit, target: HeroUnit) -> tuple[list[Position], bool]:
        route = self.route_to(battle, actor, target)
        if not route:
            return [], False
        if len(route) == 1:
            return route, True
        if (actor.cannot_move or actor.cannot_normal_move
                or actor.normal_move_actions_used >= actor.normal_move_actions_per_turn()):
            return [], False
        budget = actor.remaining_normal_move_distance(battle)
        selected = [route[0]]
        spent = 0
        for start, end in zip(route, route[1:]):
            cost = battle.normal_movement_step_cost(actor, start, end)
            if spent + cost > budget:
                break
            spent += cost
            selected.append(end)
        if len(selected) == 1:
            return [], False
        return selected, len(selected) == len(route) and self.can_fire_at(battle, actor, target, route[-1])

    def forced_normal_move_paths(self, battle: Battle, actor: HeroUnit) -> list[list[Position]] | None:
        if not self._berserk(battle):
            return None
        routes = [route for target in self.nearest_targets(battle, actor)
                  for route, can_attack in [self.action_route(battle, actor, target)]
                  if len(route) > 1 and not can_attack]
        return list({tuple(route): route for route in routes}.values())

    def basic_attack_preview(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any] | None = None) -> dict[str, Any] | None:
        if not self._berserk(battle):
            return None
        targets: list[HeroUnit] = []
        for target in self.nearest_targets(battle, actor):
            attack_payload = {**(payload or {}), "target_unit_id": target.unit_id}
            if battle.attack_target_allowed(actor, target, payload=attack_payload)[0]:
                targets.append(target)
        return {"cells": [cell.to_dict() for target in targets for cell in battle.unit_cells(target)],
                "target_unit_ids": [target.unit_id for target in targets], "requires_target": True}

    def basic_attack_origins(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any] | None) -> list[Position] | None:
        if not self._berserk(battle) or (payload or {}).get("_attack_pre_move_done"):
            return None
        target = battle.units.get(str((payload or {}).get("target_unit_id") or ""))
        if target not in self.nearest_targets(battle, actor):
            return []
        route, can_attack = self.action_route(battle, actor, target)
        return battle.unit_cells_at(actor, route[-1]) if route and can_attack else []

    def can_attack_target_with_payload(self, battle: Battle, actor: HeroUnit, target: HeroUnit, payload: dict[str, Any] | None) -> tuple[bool, str]:
        if not self._berserk(battle) or (payload or {}).get("_attack_pre_move_done"):
            return True, ""
        if target not in self.nearest_targets(battle, actor):
            return False, "浮游炮狂暴化时只能攻击最近的可见敌方单位。"
        if payload is None or str(payload.get("target_unit_id") or "") != target.unit_id:
            return False, "请选择最近的攻击目标。"
        return True, ""

    def before_basic_attack_resolution(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> bool:
        if not self._berserk(battle) or payload.get("_attack_pre_move_done"):
            return True
        target = battle.units.get(str(payload.get("target_unit_id") or ""))
        if target not in self.nearest_targets(battle, actor):
            return False
        route, can_attack = self.action_route(battle, actor, target)
        if not route or not can_attack:
            return False
        if len(route) > 1:
            try:
                battle.move_unit(actor, route[-1], path=route[1:], max_distance=actor.remaining_normal_move_distance(battle))
            except ActionError:
                return False
            payload["_attack_pre_move_done"] = True
        return actor.alive and not actor.banished


class FloatingCannonSummon(AbstractHero):
    hero_code = "floating_cannon"
    hero_name = "浮游炮"
    role = "召唤物"
    attribute = "光"
    race = "机械"
    level = 1
    base_stats = Stats(attack=3, defense=2, speed=4, attack_range=4, mana=0)
    raw_skill_text = ""
    raw_trait_text = "魔免"

    def __init__(self, player_id: int) -> None:
        super().__init__(player_id, is_summon=True)

    def build_skills(self) -> list[Skill]:
        return []

    def build_traits(self) -> list[Trait]:
        return [FloatingCannonStatTrait()]


def floating_cannons_for(battle: Battle, owner: HeroUnit) -> list[HeroUnit]:
    return [
        unit
        for unit in battle.player_units(owner.player_id)
        if getattr(unit, "hero_code", "") == "floating_cannon"
        and unit.summoner_id == owner.unit_id
        and unit.alive
        and unit.position is not None
    ]  # type: ignore[return-value]


class FloatingCannonsSkill(Skill):
    def __init__(self) -> None:
        super().__init__("floating_cannons", "浮游炮*4", "大招：召唤 4 个浮游炮到周围合法格。", max_uses_per_battle=1, target_mode="cell")

    def available_cells(self, battle: Battle, actor: HeroUnit) -> list[Position]:
        return [cell for cell in square_around_cells(battle, battle.unit_cells(actor), radius=1) if battle.can_place_unit(FloatingCannonSummon(actor.player_id), cell)]

    def execute(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> None:
        cells = self.available_cells(battle, actor)
        if len(cells) < 4:
            raise ActionError("周围没有足够空间召唤 4 个浮游炮。")
        first = payload_position(payload)
        ordered = sorted(cells, key=lambda cell: (cell != first, cell.y, cell.x))
        if first not in cells:
            raise ActionError("请选择周围合法格作为浮游炮召唤起点。")
        entries = [(cell, FloatingCannonSummon(actor.player_id)) for cell in ordered[:4]]
        cannon_ids = [cannon.unit_id for _, cannon in entries]
        tracker = next((trait for trait in actor.traits if isinstance(trait, SakuraFloatingCannonTrait)), None)
        if tracker is not None:
            tracker.cannon_ids = set(cannon_ids)
            tracker.expanded = True
        if actor.get_status("浮游炮展开") is None:
            actor.add_status(FloatingCannonsActiveStatus(cannon_ids))
        for cell, cannon in entries:
            battle.summon_unit(cannon, cell, summoner=actor)
            if actor.get_status("浮游炮狂暴化") is not None:
                cannon.add_status(FloatingCannonBuffStatus())
        battle.log(f"{actor.name} 展开 4 个【浮游炮】。")

    def preview(self, battle: Battle, actor: HeroUnit) -> dict[str, Any]:
        cells = self.available_cells(battle, actor)
        return {"cells": [cell.to_dict() for cell in cells], "target_unit_ids": [], "secondary_cells": [], "requires_target": True}


class FloatingCannonBerserkSkill(Skill):
    def ai_self_buff_score(self, context: AISkillScoreContext) -> float:
        battle, actor = context.battle, context.actor
        enemies = [unit for unit in battle.enemy_units(actor.player_id)
                   if unit.alive and unit.position is not None and not unit.banished]
        cannons = floating_cannons_for(battle, actor)
        if not cannons or not enemies:
            return -8.0
        protected = {}
        for ally in battle.player_units(actor.player_id):
            recipient = battle.effect_recipient(ally)
            if not recipient.alive or recipient.position is None or recipient.banished:
                continue
            if any(any(abs(cannon.position.x - cell.x) <= 3 and abs(cannon.position.y - cell.y) <= 3
                       for cell in battle.unit_cells(recipient)) for cannon in cannons):
                protected[recipient.unit_id] = recipient
        cover_value = max((min(160.0, context.shared.incoming_position_risk(battle, recipient)) * 0.5
                           for recipient in protected.values()), default=0.0)

        @expected_local(battle)
        def array_value() -> float:
            total = 0.0
            berserk = actor.get_status("浮游炮狂暴化") is not None
            for cannon in cannons:
                if not cannon.turn_ready or cannon.attacks_used >= cannon.attack_actions_per_turn():
                    continue
                trait = next((item for item in cannon.traits if isinstance(item, FloatingCannonStatTrait)), None)
                if trait is None:
                    continue
                if berserk:
                    targets = [target for target in trait.nearest_targets(battle, cannon)
                               if trait.action_route(battle, cannon, target)[1]]
                else:
                    targets = [target for target in enemies
                               if battle.attack_target_allowed(cannon, target, payload={"target_unit_id": target.unit_id})[0]]
                damage = max((context.attack_damage(cannon, target) for target in targets), default=0.0)
                total += damage * max(0, cannon.attack_actions_per_turn() - cannon.attacks_used) * 45.0
                if berserk and not targets and any(trait.action_route(battle, cannon, target)[0]
                                                for target in trait.nearest_targets(battle, cannon)):
                    total += 12.0
            if not berserk:
                total += cover_value
            return total

        before = array_value()
        with context.probe_rollback():
            self.execute(battle, actor, {})
            after = array_value()
        gain = after - before
        return 140.0 + gain if gain > 15.0 else -8.0

    def __init__(self) -> None:
        super().__init__("floating_cannon_berserk", "浮游炮狂暴化", "开关技能：仅可在回合开始时使用；切换浮游炮狂暴化。", max_uses_per_turn=1, target_mode="self")

    def can_use(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any] | None = None) -> tuple[bool, str]:
        ok, reason = super().can_use(battle, actor, payload)
        if not ok:
            return ok, reason
        if actor.actions_taken_this_turn or actor.moved_this_turn or actor.attacks_used:
            return False, "浮游炮狂暴化只能在回合开始时使用。"
        return True, ""

    def execute(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> None:
        status = actor.get_status("浮游炮狂暴化")
        if status is None:
            actor.add_status(FloatingCannonBerserkStatus())
            for cannon in floating_cannons_for(battle, actor):
                if cannon.get_status("浮游炮狂暴") is None:
                    cannon.add_status(FloatingCannonBuffStatus())
            battle.log(f"{actor.name} 开启【浮游炮狂暴化】。")
        else:
            actor.remove_status(status, battle)
            for cannon in floating_cannons_for(battle, actor):
                buff = cannon.get_status("浮游炮狂暴")
                if buff is not None:
                    cannon.remove_status(buff, battle)
            battle.log(f"{actor.name} 关闭【浮游炮狂暴化】。")


class FloatingCannonCoverStatus(StatusEffect):
    def __init__(self, declaration_id: str) -> None:
        super().__init__("浮游炮掩护", "仅抵消已声明动作对这个受体的伤害与附效。", duration=None)
        self.declaration_id = declaration_id
        self.expire_with_chain = True

    def block(self, battle: Battle, ctx: Any) -> None:
        owner = self.owner
        action = battle.resolving_action
        if (owner is None or ctx.target is not owner or action is None
                or action.payload.get("declaration_id") != self.declaration_id
                or ctx.from_field_effect or ctx.cancelled):
            return
        source = ctx.actor if isinstance(ctx, TargetContext) else ctx.source
        if source is None or not (ctx.is_skill or "attack" in ctx.tags):
            return
        ctx.cancelled = True
        ctx.reason = f"{owner.name} 的【浮游炮掩护】挡住了【{ctx.action_name}】。"
        battle.emit_defense_visual_event(source=source, target=owner,
                                         action_name=ctx.action_name, defense_reason="block")

    def on_targeted(self, battle: Battle, ctx: TargetContext) -> None:
        self.block(battle, ctx)

    def on_before_damage(self, battle: Battle, ctx: DamageContext) -> None:
        self.block(battle, ctx)


class FloatingCannonCoverSkill(Skill):
    ai_reaction_score_phase = "after_hidden_source"

    def __init__(self) -> None:
        super().__init__("floating_cannon_cover", "浮游炮掩护", "被动技能：狂暴化关闭时，指定受保护者和其周围 7*7 内一座自己的浮游炮；真实破坏该炮，仅保护该目标免受当前动作的伤害和附效。", timing="passive", target_mode="unit")

    def _cannons_near(self, battle: Battle, actor: HeroUnit, target: HeroUnit) -> list[HeroUnit]:
        cells = {position_key(cell) for cell in square_around_cells(battle, battle.unit_cells(target), radius=3)}
        return [
            unit
            for unit in battle.player_units(actor.player_id)
            if getattr(unit, "hero_code", "") == "floating_cannon"
            and unit.summoner_id == actor.unit_id
            and unit.alive
            and unit.position is not None
            and any(position_key(cell) in cells for cell in battle.unit_cells(unit))
        ]  # type: ignore[return-value]

    def _threatened_units(self, battle: Battle, actor: HeroUnit, queued_action: Any) -> list[HeroUnit]:
        result: list[HeroUnit] = []
        for unit_id in queued_action.target_unit_ids:
            unit = battle.units.get(unit_id)
            if unit is not None:
                recipient = battle.effect_recipient(unit)
                if (recipient.alive and recipient.position is not None and not recipient.banished
                        and battle.unit_can_be_selected(recipient, actor=actor)[0]
                        and not recipient.direct_effects_blocked()
                        and recipient.get_status("风壁") is None
                        and recipient.get_status("浮游炮掩护") is None
                        and not battle.shield_auto_blocks_chain(recipient, queued_action)
                        and recipient not in result):
                    result.append(recipient)  # type: ignore[arg-type]
        return result

    def can_react_to(self, battle: Battle, actor: HeroUnit, queued_action: Any) -> tuple[bool, str]:
        ok, reason = super().can_react_to(battle, actor, queued_action)
        if not ok:
            return ok, reason
        if actor.get_status("浮游炮狂暴化") is not None:
            return False, "浮游炮狂暴化开启时不能使用掩护。"
        if not any(self._cannons_near(battle, actor, target) for target in self._threatened_units(battle, actor, queued_action)):
            return False, "没有可用于掩护的浮游炮。"
        return True, ""

    def can_react_with_payload(self, battle: Battle, actor: HeroUnit, queued_action: Any, payload: dict[str, Any] | None = None) -> tuple[bool, str]:
        ok, reason = super().can_react_with_payload(battle, actor, queued_action, payload)
        if not ok:
            return ok, reason
        reaction_payload = dict(payload or {})
        ids = reaction_payload.get("target_unit_ids") or []
        target_id = str(reaction_payload.get("target_unit_id") or (ids[0] if ids else ""))
        cannon_id = str(reaction_payload.get("cannon_unit_id") or (ids[1] if len(ids) > 1 else ""))
        target = battle.units.get(target_id)
        target = battle.effect_recipient(target) if target is not None else None
        if target is None or target not in self._threatened_units(battle, actor, queued_action):
            return False, "请选择当前动作实际影响的保护目标。"
        if target.unit_id == cannon_id:
            return False, "不能牺牲正在保护的同一座浮游炮。"
        if cannon_id not in {unit.unit_id for unit in self._cannons_near(battle, actor, target)}:
            return False, "请选择目标周围 7*7 内一座自己的浮游炮。"
        return True, ""

    def react(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any], queued_action: Any) -> None:
        ok, reason = self.can_react_with_payload(battle, actor, queued_action, payload)
        if not ok:
            battle.log(f"{actor.name} 的【浮游炮掩护】因之前的连锁使掩护条件失效而落空。")
            return
        ids = payload.get("target_unit_ids") or []
        target = battle.effect_recipient(battle.get_unit(str(payload.get("target_unit_id") or ids[0])))
        cannon_id = str(payload.get("cannon_unit_id") or (ids[1] if len(ids) > 1 else ""))
        cannon = battle.get_unit(cannon_id)
        cannon.alive = False
        battle.cleanup_dead_units()
        target.add_status(FloatingCannonCoverStatus(str(queued_action.payload.get("declaration_id") or "")), source=actor)
        battle.log(f"{actor.name} 破坏 {cannon.name}，为 {target.name} 发动【浮游炮掩护】。")

    def execute(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> None:
        raise ActionError("浮游炮掩护只能通过连锁使用。")

    def reaction_preview(self, battle: Battle, actor: HeroUnit, queued_action: Any) -> dict[str, Any]:
        targets = [target for target in self._threatened_units(battle, actor, queued_action) if self._cannons_near(battle, actor, target)]
        cannons: list[HeroUnit] = []
        for target in targets:
            for cannon in self._cannons_near(battle, actor, target):
                if cannon is not target and cannon not in cannons:
                    cannons.append(cannon)
        return {
            "cells": [cell.to_dict() for unit in [*targets, *cannons] for cell in battle.unit_cells(unit)],
            "target_unit_ids": [unit.unit_id for unit in [*targets, *cannons]],
            "secondary_cells": [cell.to_dict() for cannon in cannons for cell in battle.unit_cells(cannon)],
            "requires_target": True,
            "selection": {"mode": "multi_unit", "min_targets": 2, "max_targets": 2,
                          "prompt": "先点受保护单位，再点要牺牲的浮游炮，最后完成选择。"},
        }

    def ai_reaction_score(self, context: AIReactionScoreContext) -> float:
        battle, actor, queued, payload = (context.battle, context.actor,
                                          context.queued_action, context.payload)
        if not self.can_react_with_payload(battle, actor, queued, payload)[0]:
            return -1000.0
        target = battle.get_unit(str(payload["target_unit_id"]))
        cannon = battle.get_unit(str(payload["cannon_unit_id"]))
        threat = context.threat(target)
        if target.player_id != actor.player_id:
            threat = -threat
        cost = 42.0 + (18.0 if cannon.attacks_used < cannon.attack_actions_per_turn() else 0.0)
        if target.player_id == actor.player_id and target.current_hp <= threat / 100.0:
            threat += 100.0
        return threat - cost


class SakuraFloatingCannonTrait(Trait):
    def __init__(self) -> None:
        super().__init__("浮游炮回补", "已展开浮游炮后，每个樱火回合开始时补回被破坏的浮游炮到周围合法格。")
        self.expanded = False
        self.cannon_ids: set[str] = set()
        self.pending_cannons: list[HeroUnit] = []

    def on_owned_summon_destroyed(self, battle: Battle, summon: HeroUnit) -> None:
        owner = self.owner
        if (owner is not None and self.expanded
                and getattr(summon, "hero_code", "") == "floating_cannon"
                and summon.summoner_id == owner.unit_id
                and summon.unit_id in self.cannon_ids
                and summon not in self.pending_cannons):
            self.pending_cannons.append(summon)

    def on_owner_turn_start(self, battle: Battle) -> None:
        owner = self.owner
        if owner is None or owner.position is None or not self.expanded:
            return
        for cannon in list(self.pending_cannons):
            cells = [cell for cell in square_around_cells(battle, battle.unit_cells(owner), radius=1)
                     if battle.can_place_unit(cannon, cell)]
            if not cells:
                continue
            cell = min(cells, key=lambda item: (item.y, item.x))
            cannon.alive = True
            cannon.current_hp = cannon.max_health
            cannon.current_mana = cannon.max_mana()
            cannon.banished = False
            cannon.attacks_used = 0
            cannon.actions_taken_this_turn = []
            cannon.moved_this_turn = False
            battle.summon_unit(cannon, cell, summoner=owner)
            if cannon in battle.destroyed_units:
                battle.destroyed_units.remove(cannon)
            if owner.get_status("浮游炮狂暴化") is not None:
                if cannon.get_status("浮游炮狂暴") is None:
                    cannon.add_status(FloatingCannonBuffStatus())
            else:
                buff = cannon.get_status("浮游炮狂暴")
                if buff is not None:
                    cannon.remove_status(buff, battle)
            self.pending_cannons.remove(cannon)
            battle.log(f"{owner.name} 在回合开始时补回 1 个【浮游炮】。")


