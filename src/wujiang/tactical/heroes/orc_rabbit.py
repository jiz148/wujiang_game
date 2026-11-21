"""R375 卯: attack-built terrain burrows, relayed fire, and escape."""

from __future__ import annotations

from typing import Any

from wujiang.tactical.engine.core import (
    ActionError, Battle, Position, QueuedAction, Skill, Stats, Trait, Unit,
)
from wujiang.tactical.heroes.ai_policy import HeroAIPolicy
from wujiang.tactical.heroes.base import AbstractHero
from wujiang.tactical.heroes.common import payload_position, positions_to_dict


def _present(unit: Unit | None) -> bool:
    return unit is not None and unit.alive and not unit.banished and unit.position is not None


def own_burrows(battle: Battle, owner: Unit) -> list[RabbitBurrow]:
    return [unit for unit in battle.all_units()
            if isinstance(unit, RabbitBurrow) and _present(unit)
            and unit.summoner_id == owner.unit_id]


class RabbitBurrowBond(Trait):
    def __init__(self) -> None:
        super().__init__("兔洞同命", "一处同源兔洞被真正破坏时，其余同源兔洞一起破坏。")

    def on_unit_destroyed(self, battle: Battle, destroyed_unit: Unit) -> None:
        hole = self.owner
        if (not isinstance(hole, RabbitBurrow) or not isinstance(destroyed_unit, RabbitBurrow)
                or hole.summoner_id is None or hole.summoner_id != destroyed_unit.summoner_id):
            return
        for other in battle.all_units():
            if (isinstance(other, RabbitBurrow) and other is not destroyed_unit
                    and other.summoner_id == hole.summoner_id and other.alive):
                other.alive = False
                other.current_hp = 0
                battle.log_public_event(f"{destroyed_unit.name} 被破坏，同源兔子洞随之破坏。",
                                        source=destroyed_unit, target=other)


class RabbitBurrow(AbstractHero):
    hero_code = "orc_rabbit_burrow"
    hero_name = "兔子洞"
    role = "地形单位"
    attribute = "暗"
    race = "召唤物"
    level = 1
    base_stats = Stats(attack=0, defense=1, speed=0, attack_range=0, mana=0)
    stat_minimums = {"attack": 0.0, "speed": 0.0, "attack_range": 0.0, "mana": 0.0}
    raw_skill_text = ""
    raw_trait_text = "守1；可站立地形单位；同源兔洞同命；无独立行动"

    def __init__(self, player_id: int) -> None:
        self.standable_terrain = True
        self.is_terrain = True
        super().__init__(player_id, is_summon=True)

    def build_skills(self) -> list[Skill]:
        return []

    def build_traits(self) -> list[Trait]:
        return [RabbitBurrowBond()]

    def can_take_turn_actions(self, battle: Battle) -> bool:
        return False


class RabbitEmptyAttackTrait(Trait):
    def __init__(self) -> None:
        super().__init__("兔洞布阵", "普攻可攻击范内直线空格，在该格造一处守1的兔子洞。")

    def available_cells(self, battle: Battle, actor: Unit) -> list[Position]:
        if actor.position is None:
            return []
        probe = RabbitBurrow(actor.player_id)
        return [cell for y in range(battle.height) for x in range(battle.width)
                for cell in (Position(x, y),)
                if not battle.units_at(cell) and battle.can_place_unit(probe, cell, mover=probe)
                and any(0 < origin.distance_to(cell) <= actor.targeting_range()
                        and battle.cells_are_straight_aligned(origin, cell)
                        for origin in battle.unit_cells(actor))]

    def basic_attack_action_entries(self, battle: Battle, actor: Unit) -> list[dict[str, Any]]:
        return [{"code": "rabbit_empty_attack", "name": "空格造兔洞",
                 "description": "消耗一次普攻，在合法空格制造可被攻击的地形兔子洞。",
                 "attack_payload": {"attack_variant": "rabbit_burrow"}}]

    def basic_attack_preview(self, battle: Battle, actor: Unit,
                             payload: dict[str, Any] | None = None) -> dict[str, Any] | None:
        if (payload or {}).get("attack_variant") != "rabbit_burrow":
            return None
        return {"cells": positions_to_dict(self.available_cells(battle, actor)),
                "target_unit_ids": [], "secondary_cells": [],
                "requires_target": True, "cell_targeted_attack": True}

    def basic_attack_area_cells(self, battle: Battle, actor: Unit,
                                payload: dict[str, Any] | None = None) -> list[Position] | None:
        if (payload or {}).get("attack_variant") != "rabbit_burrow":
            return None
        declared = battle.payload_positions(payload or {}, "attack_cells")
        if not declared and ((payload or {}).get("x") is None or (payload or {}).get("y") is None):
            if (payload or {}).get("declaration_id"):
                raise ActionError("空格造兔洞需要指定合法空格。")
            return []
        cell = declared[0] if declared else payload_position(payload or {})
        if cell not in self.available_cells(battle, actor) and not (payload or {}).get("queued_resolution"):
            raise ActionError("所选格不是范内合法空格。")
        return [cell]

    def basic_attack_payload_metadata(self, battle: Battle, actor: Unit,
                                      payload: dict[str, Any] | None = None) -> dict[str, Any]:
        if (payload or {}).get("attack_variant") != "rabbit_burrow":
            return {}
        cells = battle.payload_positions(payload or {}, "attack_cells")
        if not cells and (payload or {}).get("x") is not None and (payload or {}).get("y") is not None:
            cells = self.basic_attack_area_cells(battle, actor, payload) or []
        return {"attack_cells": positions_to_dict(cells), "area_attack": True,
                "allow_empty_attack_cell": True, "attack_name": "空格造兔洞"}

    def resolve_empty_basic_attack(self, battle: Battle, actor: Unit,
                                   payload: dict[str, Any]) -> bool:
        if payload.get("attack_variant") != "rabbit_burrow":
            return False
        cells = battle.payload_positions(payload, "attack_cells")
        if not cells or battle.units_at(cells[0]):
            battle.log_public_event(f"{actor.name} 的兔洞原格已被占用，未能制造。", source=actor)
            return True
        hole = RabbitBurrow(actor.player_id)
        if battle.can_place_unit(hole, cells[0], mover=hole):
            battle.summon_unit(hole, cells[0], summoner=actor)
            battle.log_public_event(f"{actor.name} 在 ({cells[0].x},{cells[0].y}) 制造兔子洞。",
                                    source=actor, target=hole)
        return True

    def ai_basic_attack_variant_payload(self, battle: Battle, actor: Unit,
                                        snapshot: dict[str, Any]) -> dict[str, Any] | None:
        policy = getattr(actor, "ai_policy", None)
        if policy is None or not hasattr(policy, "burrow_position_value"):
            return None
        cells = self.available_cells(battle, actor)
        if not cells:
            return None
        cell = max(cells, key=lambda item: policy.burrow_position_value(battle, actor, item))
        if policy.burrow_position_value(battle, actor, cell) <= 0:
            return None
        return {"type": "attack", "unit_id": actor.unit_id, "attack_variant": "rabbit_burrow",
                "x": cell.x, "y": cell.y}


class RabbitRelayTrait(Trait):
    requires_damage_choice = True
    ai_optional_choice_owned = True

    def __init__(self) -> None:
        super().__init__("兔洞转发", "普攻己方兔洞不伤洞，改由兔洞向本人范内敌人发起同威力与效果的攻击。")

    @staticmethod
    def targets(battle: Battle, actor: Unit, hole: RabbitBurrow) -> list[Unit]:
        if hole.position is None:
            return []
        return [unit for unit in battle.enemy_units(actor.player_id)
                if _present(unit) and battle.unit_can_be_selected(unit, actor=actor)[0]
                and any(origin.distance_to(cell) <= actor.targeting_range()
                        and battle.cells_are_straight_aligned(origin, cell)
                        for origin in battle.unit_cells(hole) for cell in battle.unit_cells(unit))]

    def basic_attack_payload_metadata(self, battle: Battle, actor: Unit,
                                      payload: dict[str, Any] | None = None) -> dict[str, Any]:
        target = battle.units.get(str((payload or {}).get("target_unit_id") or ""))
        if self.owner is actor and isinstance(target, RabbitBurrow) and target in own_burrows(battle, actor):
            return {"allow_allied_attack_target": True,
                    "attack_effect_summary": "兔子洞不受伤，由兔洞选择范内对方目标发起同效果攻击。"}
        return {}

    def resolve_allied_basic_support(self, battle: Battle, actor: Unit, target: Unit) -> bool:
        if self.owner is not actor or not isinstance(target, RabbitBurrow) or target not in own_burrows(battle, actor):
            return False
        targets = self.targets(battle, actor, target)
        if not targets:
            battle.log_public_event(f"{target.name} 周围没有合法转发目标。", source=actor, target=target)
            return True
        ids = [unit.unit_id for unit in targets]
        choice = battle.optional_position_choice(actor, ids,
            action_name="兔洞转发：选择敌方受体", kind="required_unit_selection",
            choice_context={"rabbit_relay": True, "burrow_id": target.unit_id})
        if choice not in ids:
            return True
        chosen = battle.units[choice]
        battle.queue_skill_effect_action(
            actor=actor, display_name="兔子洞攻击", effect_code="rabbit_proxy_attack",
            target_units=[chosen], target_cells=battle.unit_cells(chosen), include_cell_units=False,
            payload={"burrow_id": target.unit_id, "target_unit_id": choice},
            effect_resolver=self.resolve_relay,
        )
        return True

    @staticmethod
    def resolve_relay(battle: Battle, actor: Unit, queued: QueuedAction) -> None:
        hole = battle.units.get(str(queued.payload.get("burrow_id") or ""))
        target = battle.units.get(str(queued.payload.get("target_unit_id") or ""))
        if not isinstance(hole, RabbitBurrow) or not _present(hole) or not _present(actor) or not _present(target):
            return
        if target not in RabbitRelayTrait.targets(battle, actor, hole):
            return
        original = actor.position
        actor.position = hole.position
        try:
            battle.resolve_attack_damage(actor, target, action_name="兔子洞攻击",
                                         tags={"rabbit_proxy"}, payload={"rabbit_proxy": True})
        finally:
            if actor.alive and actor.position == hole.position:
                actor.position = original

    def ai_optional_choice_score(self, battle: Battle, actor: Unit,
                                 prompt: dict[str, Any], choice: str) -> float | None:
        if actor is not self.owner or not (prompt.get("choice_context") or {}).get("rabbit_relay"):
            return None
        target = battle.units.get(choice)
        return 80.0 + 30.0 * (1 - target.current_hp) if _present(target) else -1000.0


class RabbitEscapeSkill(Skill):
    is_displacement_skill = True
    ai_reaction_score_phase = "after_hidden_source"

    def __init__(self) -> None:
        super().__init__("rabbit_escape", "脱兔",
                         "每实际回合最多2次；受敌动作影响时可连锁瞬移到同源兔洞旁。",
                         max_uses_per_turn=2, timing="passive", target_mode="cell")

    def destinations(self, battle: Battle, actor: Unit) -> list[Position]:
        if not _present(actor) or actor.cannot_move:
            return []
        cells = {cell for hole in own_burrows(battle, actor)
                 for origin in battle.unit_cells(hole) for cell in battle.neighbors(origin)}
        return [cell for cell in sorted(cells, key=lambda item: (item.y, item.x))
                if cell != actor.position and battle.can_place_unit(actor, cell, ignore=actor, mover=actor)]

    def execute(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> None:
        raise ActionError("脱兔只能在连锁窗口使用。")

    def can_react_to(self, battle: Battle, actor: Unit,
                     queued_action: QueuedAction) -> tuple[bool, str]:
        ok, reason = super().can_react_to(battle, actor, queued_action)
        if not ok:
            return ok, reason
        if queued_action.source_player_id == actor.player_id or actor.unit_id not in queued_action.target_unit_ids:
            return False, "只有本人受敌方动作影响时可脱兔。"
        if not self.destinations(battle, actor):
            return False, "没有可用兔洞旁落格。"
        return True, ""

    def can_react_with_payload(self, battle: Battle, actor: Unit, queued_action: QueuedAction,
                               payload: dict[str, Any] | None = None) -> tuple[bool, str]:
        ok, reason = super().can_react_with_payload(battle, actor, queued_action, payload)
        if not ok:
            return ok, reason
        try:
            cell = payload_position(payload or {})
        except (KeyError, TypeError, ValueError):
            return False, "脱兔需要选合法落格。"
        return (True, "") if cell in self.destinations(battle, actor) else (False, "不是兔洞旁合法落格。")

    def declared_reaction_landing_valid(self, battle: Battle, actor: Unit,
                                        payload: dict[str, Any]) -> bool:
        return payload_position(payload) in self.destinations(battle, actor)

    def react(self, battle: Battle, actor: Unit, payload: dict[str, Any],
              queued_action: QueuedAction) -> None:
        cell = payload_position(payload)
        if cell not in self.destinations(battle, actor):
            raise ActionError("脱兔落格已失效。")
        battle.move_unit(actor, cell, via_skill=True, allow_anywhere=True,
                         triggered_by_reaction=True, tags={"rabbit_escape"})
        if queued_action.action_type == "attack":
            evaded = queued_action.payload.setdefault("evaded_unit_ids", [])
            if actor.unit_id not in evaded:
                evaded.append(actor.unit_id)
        battle.log_public_event(f"{actor.name} 脱兔至 ({cell.x},{cell.y})。", source=actor)

    def reaction_preview(self, battle: Battle, actor: Unit,
                         queued_action: QueuedAction) -> dict[str, Any]:
        return {"cells": positions_to_dict(self.destinations(battle, actor)),
                "target_unit_ids": [], "secondary_cells": positions_to_dict(battle.unit_cells(actor)),
                "requires_target": True}

    def preview(self, battle: Battle, actor: Unit) -> dict[str, Any]:
        return {"cells": [], "target_unit_ids": [], "secondary_cells": [], "requires_target": False}

    def ai_reaction_score(self, context: Any) -> float:
        cell = context.destination()
        if cell is None:
            return -100.0
        threat = context.threat(context.proxy_target())
        position = context.skill_context(self.code).move_score(cell, context.skill_context(self.code).hero_style())
        return threat + position / 2 + 45 - (threat + 65 if context.destination_still_targeted(cell) else 0)


class RabbitBurrowAIPolicy(HeroAIPolicy):
    def burrow_position_value(self, battle: Battle, actor: Unit, cell: Position) -> float:
        if not battle.enemy_units(actor.player_id):
            return -100.0
        nearby = sum(1 for enemy in battle.enemy_units(actor.player_id) if _present(enemy)
                     and cell.distance_to(enemy.position) <= actor.targeting_range())
        own = sum(1 for hole in own_burrows(battle, actor)
                  if hole.position == cell or hole.position.distance_to(cell) <= 1)
        return 24.0 + 15.0 * nearby - 14.0 * own

    def attack_payload_has_hero_benefit(self, battle: Battle, actor: Unit,
                                        payload: dict) -> bool:
        if payload.get("attack_variant") == "rabbit_burrow":
            try:
                cell = payload_position(payload)
            except (KeyError, TypeError, ValueError):
                return False
            return self.burrow_position_value(battle, actor, cell) > 0
        hole = battle.units.get(str(payload.get("target_unit_id") or ""))
        return (isinstance(hole, RabbitBurrow) and hole in own_burrows(battle, actor)
                and bool(RabbitRelayTrait.targets(battle, actor, hole)))

    def early_attack_score(self, battle: Battle, actor: Unit, target: Unit | None,
                           payload: dict, profile: Any, shared: Any) -> float | None:
        if payload.get("attack_variant") == "rabbit_burrow":
            try:
                return self.burrow_position_value(battle, actor, payload_position(payload))
            except (KeyError, TypeError, ValueError):
                return -1000.0
        if isinstance(target, RabbitBurrow) and target in own_burrows(battle, actor):
            options = RabbitRelayTrait.targets(battle, actor, target)
            return 72.0 + 25.0 * max((1 - unit.current_hp for unit in options), default=-4.0)
        return None
