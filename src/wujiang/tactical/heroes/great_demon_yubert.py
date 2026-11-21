"""R362 尤贝尔特：魔力点、合法换位、恶魔形态与相位普攻。"""

from __future__ import annotations

from typing import Any

from wujiang.tactical.engine.core import (
    ActionError, Battle, DamageContext, HealContext, Position, Skill, StatusEffect,
    Trait, Unit,
)
from wujiang.tactical.heroes.ai_policy import AISkillScoreContext
from wujiang.tactical.heroes.common import (
    DashMoveSkill, StatModifierStatus, payload_target_unit,
    positions_to_dict,
)
from wujiang.tactical.heroes.mechanics.gladiator_core import (
    gladiator_swap, gladiator_swap_destinations,
)
from wujiang.tactical.heroes.mechanics.mana_point_cost import ManaPointCostSkill
from wujiang.tactical.heroes.mechanics.next_five_geometry import square_around_cells


def _present(unit: Unit | None) -> bool:
    return bool(unit is not None and unit.alive and not unit.banished and unit.position is not None)


class YubertBloodPointsTrait(Trait):
    def __init__(self) -> None:
        super().__init__("恶魔之血", "正式普攻+1魔力点；普攻实伤吸血。")

    def on_owner_action_declared(self, battle: Battle, action_type: str,
                                 payload: dict[str, Any]) -> None:
        if action_type == "attack" and _present(self.owner):
            gained = self.owner.gain_mana_points(1)
            if gained:
                battle.log_public_event(f"{self.owner.name} 普攻获得 {gained:g} 魔力点。",
                                        source=self.owner, target=self.owner)

    def on_after_damage(self, battle: Battle, ctx: DamageContext) -> None:
        owner = self.owner
        if (owner is None or ctx.source is not owner or ctx.is_skill or "attack" not in ctx.tags
                or ctx.actual_damage <= 0 or not _present(owner)):
            return
        battle.heal(HealContext(source=owner, target=owner, amount=ctx.actual_damage,
                                action_name="普攻吸血"))


class YubertKillAttacksTrait(Trait):
    def __init__(self) -> None:
        super().__init__("恶魔连猎", "本人真实击破一个其他单位后，永久增加每轮普攻次数1次。")
        self.extra_attacks = 0

    def modify_attack_actions_per_turn(self, value: int) -> int:
        return value + self.extra_attacks

    def on_unit_destroyed(self, battle: Battle, destroyed_unit: Unit) -> None:
        owner = self.owner
        if (owner is None or destroyed_unit is owner
                or str(getattr(destroyed_unit, "_destroying_source_unit_id", "")) != owner.unit_id):
            return
        self.extra_attacks += 1
        battle.log_public_event(f"{owner.name} 击破 {destroyed_unit.name}，每轮普攻次数增加1。",
                                source=owner, target=destroyed_unit)


class YubertSwapSkill(Skill):
    is_displacement_skill = True
    moves_owner_on_use = True
    requires_direct_unit_target_line = False
    resolves_from_current_position = True

    def __init__(self) -> None:
        super().__init__("yubert_swap", "移形换位",
                         "本人轮一次；21×21内与敌方单位寻找合法落格交换位置，破魔且不可回避；成功后+1魔力点。",
                         max_uses_per_turn=1, target_mode="unit")

    def candidates(self, battle: Battle, actor: Unit) -> list[Unit]:
        if not _present(actor) or actor.cannot_move:
            return []
        origins = battle.unit_cells(actor)
        result = []
        for target in battle.enemy_units(actor.player_id):
            if (not _present(target) or target.cannot_move
                    or not battle.unit_can_be_selected(target, actor=actor)[0]):
                continue
            if min(a.distance_to(b) for a in origins for b in battle.unit_cells(target)) > 10:
                continue
            if gladiator_swap_destinations(battle, actor, target) is not None:
                result.append(target)
        return result

    def can_use(self, battle: Battle, actor: Unit,
                payload: dict[str, Any] | None = None) -> tuple[bool, str]:
        ok, reason = super().can_use(battle, actor, payload)
        if not ok:
            return ok, reason
        candidates = self.candidates(battle, actor)
        if not candidates:
            return False, "21×21内没有可合法交换位置的对方单位。"
        if payload and payload.get("target_unit_id"):
            target = battle.units.get(str(payload["target_unit_id"]))
            if target not in candidates:
                return False, "换位目标或双方落格不合法。"
        return True, ""

    def ignores_shield_for_payload(self, battle: Battle, actor: Unit,
                                   payload: dict[str, Any]) -> bool:
        return True

    def queued_payload_metadata(self, battle: Battle, actor: Unit,
                                payload: dict[str, Any]) -> dict[str, Any]:
        return {"ignore_shield": True, "cannot_evade": True}

    def get_target_units_for_payload(self, battle: Battle, actor: Unit,
                                     payload: dict[str, Any]) -> list[Unit]:
        return [payload_target_unit(battle, payload)]

    def preview(self, battle: Battle, actor: Unit) -> dict[str, Any]:
        targets = self.candidates(battle, actor)
        landing_by_target = {}
        landing_cells: set[Position] = set()
        for target in targets:
            pair = gladiator_swap_destinations(battle, actor, target)
            if pair is None:
                continue
            actor_cells = battle.unit_cells_at(actor, pair[0])
            target_cells = battle.unit_cells_at(target, pair[1])
            landing_cells.update(actor_cells)
            landing_cells.update(target_cells)
            landing_by_target[target.unit_id] = {
                "actor": positions_to_dict(actor_cells), "target": positions_to_dict(target_cells),
            }
        return {"cells": positions_to_dict([cell for unit in targets for cell in battle.unit_cells(unit)]),
                "target_unit_ids": [unit.unit_id for unit in targets],
                "secondary_cells": positions_to_dict(sorted(landing_cells, key=lambda cell: (cell.y, cell.x))),
                "landing_cells_by_target": landing_by_target, "requires_target": True}

    def execute(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> None:
        target = payload_target_unit(battle, payload)
        if target not in self.candidates(battle, actor):
            raise ActionError("换位目标或双方落格不合法。")
        check = battle.validate_target(actor, target, action_name=self.name, is_skill=True,
                                       is_hostile=True, ignore_shield=True, cannot_evade=True,
                                       resolve_defenses=False, tags={"skill", self.code})
        if check.cancelled:
            battle.log_public_event(check.reason or f"{target.name} 挡住了移形换位。",
                                    source=actor, target=target)
            return
        if gladiator_swap(battle, actor, target, skill_code=self.code):
            if target.total_shields() > 0:
                target.consume_one_shield()
                battle.log_public_event(f"{target.name} 的一层护盾被移形换位贯穿并打碎。",
                                        source=actor, target=target)
            actor.gain_mana_points(1)
            battle.log_public_event(f"{actor.name} 换位后获得1魔力点。", source=actor, target=actor)

    def ai_effective_enemy_impact(self, battle: Battle, actor: Unit,
                                  payload: dict[str, Any], targets: list[Unit] | None = None,
                                  shared: Any = None,
                                  action: dict[str, Any] | None = None) -> bool:
        return battle.units.get(str(payload.get("target_unit_id"))) in self.candidates(battle, actor)

    def ai_priority_score(self, context: AISkillScoreContext) -> float:
        actor, battle = context.actor, context.battle
        target = battle.units.get(str(context.payload.get("target_unit_id") or ""))
        if target is None or actor.position is None or target.position is None:
            return -1000.0
        pair = gladiator_swap_destinations(battle, actor, target)
        if pair is None:
            return -1000.0
        gain = context.move_score(pair[0], context.hero_style()) - context.move_score(actor.position, context.hero_style())
        point_value = 36.0 if actor.mana_points == 2 else 15.0
        return gain + point_value - 12.0


class YubertTrueDemonStatus(StatusEffect):
    def __init__(self) -> None:
        super().__init__("真恶魔形态", "永久攻守速+2、范变为7、增加飞行。")
        self.prior_flying = False

    def bind(self, owner: Unit) -> "YubertTrueDemonStatus":
        super().bind(owner)
        self.prior_flying = owner.has_flying
        owner.has_flying = True
        return self

    def modify_stat(self, stat_name: str, value: float) -> float:
        if stat_name in {"attack", "defense", "speed"}:
            return value + 2
        if stat_name == "attack_range":
            return 7.0
        return value

    def on_removed(self, battle: Battle) -> None:
        if self.owner is not None:
            self.owner.has_flying = self.prior_flying


class YubertTrueDemonSkill(ManaPointCostSkill):
    def __init__(self) -> None:
        super().__init__("yubert_true_demon", "真恶魔形态",
                         "本场一次，消耗3魔力点，永久攻守速+2、范7并飞行。",
                         mana_point_cost=3, max_uses_per_battle=1, target_mode="self")

    def execute(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> None:
        if not actor.has_status("真恶魔形态"):
            actor.add_status(YubertTrueDemonStatus())
            battle.log_public_event(f"{actor.name} 进入真恶魔形态。", source=actor, target=actor)

    def ai_priority_score(self, context: AISkillScoreContext) -> float:
        return 95.0 if context.actor.mana_points >= 3 else -1000.0


class YubertSighDebuff(StatusEffect):
    def __init__(self) -> None:
        super().__init__("叹息降守", "守-1，最低为1，至本人下回合结束。", duration=1)

    def modify_stat(self, stat_name: str, value: float) -> float:
        return max(1.0, value - 1) if stat_name == "defense" else value


class YubertSighWallSkill(Skill):
    ai_payload_value_owned = True

    def __init__(self) -> None:
        super().__init__("yubert_sighing_wall", "叹息之壁",
                         "本人轮一次；周围11×11破魔技能伤，命中敌方守-1、本人按命中数守+1至下回合结束。",
                         max_uses_per_turn=1, target_mode="self")

    def cells(self, battle: Battle, actor: Unit) -> list[Position]:
        return square_around_cells(battle, battle.unit_cells(actor), radius=5)

    def targets(self, battle: Battle, actor: Unit) -> list[Unit]:
        return [unit for unit in battle.effect_units_at_cells(self.cells(battle, actor), ignore=actor)
                if unit.player_id != actor.player_id]

    def effective_targets(self, battle: Battle, actor: Unit) -> list[Unit]:
        return [unit for unit in self.targets(battle, actor)
                if not unit.magic_immunity and not unit.direct_effects_blocked()]

    def preview(self, battle: Battle, actor: Unit) -> dict[str, Any]:
        return {"cells": positions_to_dict(self.cells(battle, actor)),
                "target_unit_ids": [unit.unit_id for unit in self.targets(battle, actor)],
                "secondary_cells": [], "requires_target": False}

    def get_target_units_for_payload(self, battle: Battle, actor: Unit,
                                     payload: dict[str, Any]) -> list[Unit]:
        return self.targets(battle, actor)

    def ignores_shield_for_payload(self, battle: Battle, actor: Unit,
                                   payload: dict[str, Any]) -> bool:
        return True

    def queued_payload_metadata(self, battle: Battle, actor: Unit,
                                payload: dict[str, Any]) -> dict[str, Any]:
        return {"ignore_shield": True}

    def execute(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> None:
        cells = self.cells(battle, actor)
        hit = 0
        for target in self.targets(battle, actor):
            if not _present(actor) or not _present(target):
                continue
            ctx = battle.resolve_damage(DamageContext(
                source=actor, target=target, attack_power=actor.stat("attack"), is_skill=True,
                action_name=self.name, ignore_shield=True,
                area_cell_hits=battle.unit_hit_count_for_cells(target, cells),
                tags={"skill", self.code},
            ))
            if ctx.cancelled:
                continue
            hit += 1
            if _present(target):
                target.add_status(YubertSighDebuff())
        if hit and _present(actor):
            actor.add_status(StatModifierStatus("叹息增守", defense_delta=hit, duration=2,
                                                description="每命中一敌人守+1至本人下回合结束。"))
        battle.log_public_event(f"{actor.name} 的叹息之壁命中 {hit} 个单位。", source=actor)

    def ai_effective_enemy_impact(self, battle: Battle, actor: Unit,
                                  payload: dict[str, Any], targets: list[Unit] | None = None,
                                  shared: Any = None,
                                  action: dict[str, Any] | None = None) -> bool:
        return bool(self.effective_targets(battle, actor))

    def ai_payload_value(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> float:
        return len(self.effective_targets(battle, actor)) * 45.0 - 10.0

    def ai_payload_candidates(self, battle: Battle, actor: Unit,
                              action: dict[str, Any]) -> list[dict[str, Any]]:
        return ([{"type": "skill", "unit_id": actor.unit_id, "skill_code": self.code}]
                if self.ai_payload_value(battle, actor, {}) > 0 else [])

    def ai_priority_score(self, context: AISkillScoreContext) -> float:
        return self.ai_payload_value(context.battle, context.actor, context.payload)


class YubertPhaseSlowStatus(StatusEffect):
    def __init__(self) -> None:
        super().__init__("相位减速", "速-2，最低1，至本人下回合结束。", duration=1)

    def modify_stat(self, stat_name: str, value: float) -> float:
        return max(1.0, value - 2) if stat_name == "speed" else value


class YubertPhaseAttackStatus(StatusEffect):
    def __init__(self) -> None:
        super().__init__("相位待攻", "下一次普攻先选原目标，再前移一格并半破魔攻击。")

    @staticmethod
    def destination(payload: dict[str, Any] | None) -> Position | None:
        payload = payload or {}
        try:
            choice = str(payload.get("choice_code") or "")
            if choice.startswith("yubert_phase:"):
                x, y = choice[len("yubert_phase:"):].split(",")
                return Position(int(x), int(y))
            if payload.get("move_x") is not None and payload.get("move_y") is not None:
                return Position(int(payload["move_x"]), int(payload["move_y"]))
        except (ValueError, TypeError):
            pass
        return None

    def destinations(self, battle: Battle, actor: Unit) -> list[Position]:
        if actor.cannot_move or actor.position is None or battle.mounted_unit_for(actor) is not None:
            return []
        return battle.reachable_positions(actor, max_distance=1, exact_distance=1)

    def basic_attack_origins(self, battle: Battle, actor: Unit,
                             payload: dict[str, Any] | None) -> list[Position] | None:
        if (payload or {}).get("_attack_pre_move_done"):
            return None
        destinations = self.destinations(battle, actor)
        dest = self.destination(payload)
        if payload is not None:
            return battle.unit_cells_at(actor, dest) if dest in destinations else []
        return [cell for pos in destinations for cell in battle.unit_cells_at(actor, pos)]

    @staticmethod
    def valid_target(battle: Battle, actor: Unit, target: Unit, dest: Position,
                     payload: dict[str, Any]) -> bool:
        targets = battle.unit_cells(target)
        if payload.get("x") is not None and payload.get("y") is not None:
            clicked = Position(int(payload["x"]), int(payload["y"]))
            targets = [clicked] if clicked in targets else []
        return any(origin.distance_to(cell) <= actor.targeting_range()
                   for origin in battle.unit_cells_at(actor, dest) for cell in targets)

    def can_attack_target_with_payload(self, battle: Battle, actor: Unit, target: Unit,
                                       payload: dict[str, Any] | None) -> tuple[bool, str]:
        if (payload or {}).get("_attack_pre_move_done"):
            return True, ""
        destinations = self.destinations(battle, actor)
        dest = self.destination(payload)
        if payload is None:
            return ((True, "") if any(self.valid_target(battle, actor, target, pos, {})
                                    for pos in destinations)
                    else (False, "没有可命中原目标的相位落格。"))
        if dest not in destinations or not self.valid_target(battle, actor, target, dest, payload):
            return False, "相位攻击须选可合法前移一格且仍能命中原目标的落格。"
        return True, ""

    def basic_attack_payload_metadata(self, battle: Battle, actor: Unit,
                                      payload: dict[str, Any] | None = None) -> dict[str, Any]:
        dest = self.destination(payload)
        data: dict[str, Any] = {"half_ignore_shield": True}
        if dest is not None:
            data.update({"move_x": dest.x, "move_y": dest.y})
        return data

    def basic_attack_preview(self, battle: Battle, actor: Unit,
                             payload: dict[str, Any] | None = None) -> dict[str, Any]:
        choices, cells, ids, pattern_destinations = [], [], set(), []
        for dest in self.destinations(battle, actor):
            patterns = []
            choice = f"yubert_phase:{dest.x},{dest.y}"
            for target in battle.enemy_units(actor.player_id):
                if not battle.unit_can_be_selected(target, actor=actor)[0]:
                    continue
                for cell in battle.unit_cells(target):
                    candidate = {"choice_code": choice, "x": cell.x, "y": cell.y}
                    if not battle.attack_target_allowed(actor, target, payload=candidate)[0]:
                        continue
                    patterns.append([cell.to_dict()])
                    cells.append(cell)
                    ids.add(target.unit_id)
                    pattern_destinations.append({"choice_code": choice, "pattern": [cell.to_dict()],
                                                 "destination_cells": positions_to_dict(battle.unit_cells_at(actor, dest))})
            if patterns:
                choices.append({"code": choice, "label": f"前移至 ({dest.x}, {dest.y})",
                                "patterns": patterns})
        return {"cells": positions_to_dict(cells), "target_unit_ids": sorted(ids),
                "secondary_cells": [], "requires_target": True,
                "pattern_destinations": pattern_destinations,
                "selection": {"mode": "choice_pattern", "choices": choices, "ordered": False}}

    def ai_basic_attack_variant_payload(self, battle: Battle, actor: Unit,
                                        snapshot: dict[str, Any]) -> dict[str, Any] | None:
        for dest in self.destinations(battle, actor):
            for target in battle.enemy_units(actor.player_id):
                if not battle.unit_can_be_selected(target, actor=actor)[0]:
                    continue
                for cell in battle.unit_cells(target):
                    payload = {"type": "attack", "unit_id": actor.unit_id,
                               "target_unit_id": target.unit_id, "x": cell.x, "y": cell.y,
                               "choice_code": f"yubert_phase:{dest.x},{dest.y}"}
                    if battle.attack_target_allowed(actor, target, payload=payload)[0]:
                        return payload
        return None

    def before_basic_attack_resolution(self, battle: Battle, actor: Unit,
                                       payload: dict[str, Any]) -> bool:
        dest = self.destination(payload)
        origin = battle.declared_source_position(payload) if payload.get("queued_resolution") else actor.position
        if actor.position != origin or dest not in self.destinations(battle, actor):
            return False
        try:
            battle.move_unit(actor, dest, via_skill=True, max_distance=1, exact_distance=1,
                             tags={"yubert_phase_attack"})
        except ActionError:
            return False
        payload["_attack_pre_move_done"] = True
        return _present(actor)

    def on_after_damage(self, battle: Battle, ctx: DamageContext) -> None:
        if (ctx.source is not self.owner or ctx.is_skill or "attack" not in ctx.tags
                or ctx.actual_damage <= 0 or not ctx.target.alive):
            return
        ctx.target.add_status(YubertPhaseSlowStatus())

    def on_basic_attack_finished(self, battle: Battle, actor: Unit,
                                 payload: dict[str, Any], damage_contexts: list[DamageContext],
                                 missed: bool) -> None:
        if actor is self.owner:
            actor.remove_status(self, battle)


class YubertPhaseSkill(DashMoveSkill):
    def __init__(self) -> None:
        super().__init__("yubert_phase", "相位", "本人轮一次；移动恰三格，下次普攻先前移一格并半破魔，实伤使目标速-2。",
                         max_distance=3, exact_distance=3, mana_cost=0, max_uses_per_turn=1)

    def execute(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> None:
        super().execute(battle, actor, payload)
        if _present(actor) and not actor.has_status("相位待攻"):
            actor.add_status(YubertPhaseAttackStatus())

    def ai_requires_enemy_impact(self, battle: Battle, actor: Unit,
                                 payload: dict[str, Any]) -> bool:
        return False

    def ai_priority_score(self, context: AISkillScoreContext) -> float:
        dest = context.destination()
        actor = context.actor
        if dest is None or actor.position is None:
            return -1000.0
        gain = context.move_score(dest, context.hero_style()) - context.move_score(actor.position, context.hero_style())
        future_attack = any(
            battle_cell.distance_to(target_cell) <= actor.targeting_range()
            for dx in (-1, 0, 1) for dy in (-1, 0, 1) if dx or dy
            for landing in [dest.offset(dx, dy)]
            if context.battle.in_bounds(landing)
            and context.battle.can_place_unit(actor, landing, ignore=actor, mover=actor)
            for battle_cell in context.battle.unit_cells_at(actor, landing)
            for target in context.battle.enemy_units(actor.player_id)
            if context.battle.unit_can_be_selected(target, actor=actor)[0]
            for target_cell in context.battle.unit_cells(target)
        )
        return gain + (28.0 if future_attack and not actor.has_status("相位待攻") else 0.0) - 12.0
