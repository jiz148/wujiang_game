"""Source-locked rules for R320 Legendary Fisher and R321 Garden Demon."""

from __future__ import annotations

import random
from typing import Any

from wujiang.tactical.engine.army import is_army_soldier
from wujiang.tactical.engine.core import (
    ActionError, Battle, BattleFieldEffect, DamageContext, HealContext,
    HeroUnit, Position, Skill, Trait, Unit,
)
from wujiang.tactical.heroes.common import InvincibleUntilActionStatus
from wujiang.tactical.heroes.next_five import square_around_cells


def _outer_ring(battle: Battle, actor: Unit) -> list[Position]:
    body = set(battle.unit_cells(actor))
    return [cell for cell in square_around_cells(battle, body, radius=1) if cell not in body]


class FisherGrowthTrait(Trait):
    def __init__(self) -> None:
        super().__init__("渔人连击", "基础两次普攻；攻击后公开硬币追加本轮攻次；本人真击破增加以后每轮攻次。")
        self.hero_kills = 0
        self.weather_kills = 0
        self.coin_bonus = 0

    def on_owner_turn_start(self, battle: Battle) -> None:
        self.coin_bonus = 0

    def modify_attack_actions_per_turn(self, value: int) -> int:
        return value + self.hero_kills + self.weather_kills + self.coin_bonus

    def on_basic_attack_finished(
        self, battle: Battle, actor: Unit, payload: dict[str, Any],
        damage_contexts: list[DamageContext], missed: bool,
    ) -> None:
        if actor is not self.owner or not actor.alive:
            return
        # An AI probe must not reveal or consume the next live coin toss.
        if getattr(battle, "_ai_probe_active", False):
            return
        heads = random.random() < 0.5
        if heads:
            self.coin_bonus += 1
        battle.log_public_event(
            f"{actor.name} 普攻后掷币：{'正面，本轮普攻次数+1' if heads else '反面，本轮次数不变'}；"
            f"当前额度 {actor.attack_actions_per_turn()}。",
            source=actor,
        )

    def on_unit_destroyed(self, battle: Battle, destroyed_unit: Unit) -> None:
        owner = self.owner
        if owner is None or not owner.alive or owner.banished or owner.position is None:
            return
        if getattr(destroyed_unit, "_destroying_source_unit_id", None) != owner.unit_id:
            return
        growth = 0
        if (isinstance(destroyed_unit, HeroUnit) and not destroyed_unit.is_summon
                and not destroyed_unit.is_clone and not is_army_soldier(destroyed_unit)):
            self.hero_kills += 1
            growth += 1
        if (destroyed_unit.player_id != owner.player_id
                and (battle.unit_in_weather("暴雨", owner) or battle.unit_in_weather("大海", owner))):
            self.weather_kills += 1
            growth += 1
        if growth:
            battle.log_public_event(
                f"{owner.name} 的永久普攻额度+{growth}；今后每本人轮基础额度 "
                f"{owner.attack_actions_per_turn() - self.coin_bonus}。",
                source=owner, target=destroyed_unit,
            )

    def modify_skill_mana_cost(
        self, battle: Battle, actor: Unit, skill: Skill,
        payload: dict[str, Any] | None, cost: float,
    ) -> float:
        if (actor is self.owner and skill.code in {"fly_leap", "evasion"}
                and battle.unit_in_weather("大海", actor)):
            return 0.0
        return cost

    def to_public_dict(self, battle: Battle) -> dict[str, Any]:
        data = super().to_public_dict(battle)
        data.update(hero_kills=self.hero_kills, weather_kills=self.weather_kills,
                    current_turn_coin_bonus=self.coin_bonus)
        return data


class GaleHalberdSkill(Skill):
    def __init__(self) -> None:
        super().__init__("gale_halberd", "神风之戟", "每本人轮一次；伤一格后重置本轮已用普攻次数。",
                         max_uses_per_turn=1, target_mode="cell")

    def available_cells(self, battle: Battle, actor: Unit) -> list[Position]:
        if actor.position is None:
            return []
        result: list[Position] = []
        for origin in battle.unit_cells(actor):
            for dx, dy in ((-1, -1), (-1, 0), (-1, 1), (0, -1), (0, 1), (1, -1), (1, 0), (1, 1)):
                for step in range(1, actor.targeting_range() + 1):
                    cell = origin.offset(dx * step, dy * step)
                    if battle.in_bounds(cell) and cell not in result:
                        result.append(cell)
        return result

    def can_use(self, battle: Battle, actor: Unit, payload: dict[str, Any] | None = None) -> tuple[bool, str]:
        ok, reason = super().can_use(battle, actor, payload)
        if not ok:
            return ok, reason
        if payload and payload.get("x") is not None and payload.get("y") is not None:
            if Position(int(payload["x"]), int(payload["y"])) not in self.available_cells(battle, actor):
                return False, "请选择范内直线的一格。"
        return True, ""

    def execute(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> None:
        cell = Position(int(payload["x"]), int(payload["y"]))
        for target in battle.effect_units_at_cells([cell], ignore=actor):
            if target.alive:
                battle.resolve_damage(DamageContext(
                    source=actor, target=target, attack_power=actor.stat("attack"),
                    is_skill=True, action_name=self.name, tags={"skill", self.code},
                ))
        actor.attacks_used = 0
        battle.log_public_event(f"{actor.name} 使用【神风之戟】后，本轮已用普攻次数清零。", source=actor)

    def preview(self, battle: Battle, actor: Unit) -> dict[str, Any]:
        cells = self.available_cells(battle, actor)
        return {"cells": [cell.to_dict() for cell in cells],
                "target_unit_ids": [unit.unit_id for unit in battle.effect_units_at_cells(cells, ignore=actor)],
                "secondary_cells": [], "requires_target": True}

    def get_target_cells_for_payload(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> list[Position]:
        if payload.get("x") is None or payload.get("y") is None:
            return []
        return [Position(int(payload["x"]), int(payload["y"]))]

    def get_target_units_for_payload(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> list[Unit]:
        return list(battle.effect_units_at_cells(self.get_target_cells_for_payload(battle, actor, payload), ignore=actor))


class FisherStealthSkill(Skill):
    def __init__(self) -> None:
        super().__init__("fisher_stealth", "潜行", "三本人轮一次；本轮末隐身至下本人轮末。",
                         cooldown_turns=3, target_mode="self")
        self.pending_stealth = False

    def execute(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> None:
        self.pending_stealth = True
        battle.log_public_event(f"{actor.name} 已准备潜行，待本轮结束时隐身。", source=actor)

    def on_owner_turn_end(self, battle: Battle) -> None:
        if not self.pending_stealth or self.owner is None:
            return
        self.pending_stealth = False
        actor = self.owner
        if not actor.alive or actor.banished or actor.position is None:
            return
        existing = actor.get_status("隐身")
        if existing is not None:
            actor.remove_status(existing, battle)
        # Unit.finish_turn snapshots components before this skill adds the status;
        # it therefore gets its first owner-end tick at the next owner turn.
        actor.add_status(InvincibleUntilActionStatus(duration=1, tick_scope="owner_turn_end"), source=actor)
        battle.log_public_event(f"{actor.name} 在回合末进入隐身，至下本人轮末。", source=actor)
        battle.clear_all_stealth_if_all_heroes_stealthed()

    def preview(self, battle: Battle, actor: Unit) -> dict[str, Any]:
        return {"cells": [cell.to_dict() for cell in battle.unit_cells(actor)],
                "target_unit_ids": [actor.unit_id], "secondary_cells": [], "requires_target": False}


class GardenSkillFeastTrait(Trait):
    def __init__(self) -> None:
        super().__init__("苑魔食技", "敌方在7×7内正式使用技能时，本体血+1/4、魔+2、魔力点+1。")

    def bind(self, owner: Unit) -> "GardenSkillFeastTrait":
        super().bind(owner)
        owner.mana_points = float(owner.base_stats.mana)
        return self

    def on_any_skill_declared(self, battle: Battle, actor: Unit, skill: Skill) -> None:
        owner = self.owner
        if (owner is None or not owner.alive or owner.banished or owner.position is None
                or actor.player_id == owner.player_id or actor.position is None
                or battle.distance_between_units(owner, actor) > 3):
            return
        battle.heal(HealContext(source=owner, target=owner, amount=0.25, action_name="苑魔食技"))
        mana = owner.gain_mana(2)
        points = owner.gain_mana_points(1)
        battle.log_public_event(
            f"{actor.name} 在7×7内使用【{skill.name}】，{owner.name} 回复 {mana:g} 魔并获得 {points:g} 魔力点。",
            source=actor, target=owner,
        )

    def on_owner_removed(self, battle: Battle) -> None:
        if self.owner is None:
            return
        for effect in list(battle.field_effects):
            if isinstance(effect, BlackTransformationField) and effect.source_unit_id == self.owner.unit_id:
                battle.remove_field_effect(effect)


class PointCostSkill(Skill):
    def __init__(self, code: str, name: str, description: str, *, point_cost: int, **kwargs: Any) -> None:
        super().__init__(code, name, description, **kwargs)
        self.point_cost = point_cost
        self.mana_point_cost = point_cost

    def can_use(self, battle: Battle, actor: Unit, payload: dict[str, Any] | None = None) -> tuple[bool, str]:
        ok, reason = super().can_use(battle, actor, payload)
        if not ok:
            return ok, reason
        if actor.mana_points < self.point_cost:
            return False, "魔力点不足。"
        return True, ""

    def prepay_resources(self, battle: Battle, actor: Unit, payload: dict[str, Any] | None = None) -> None:
        if actor.mana_points < self.point_cost:
            raise ActionError("魔力点不足。")
        super().prepay_resources(battle, actor, payload)
        actor.spend_mana_points(self.point_cost)

    def to_public_dict(self, battle: Battle) -> dict[str, Any]:
        data = super().to_public_dict(battle)
        data["mana_point_cost"] = self.point_cost
        return data


class BlackTransformationField(BattleFieldEffect):
    def __init__(self, caster_id: str, speed_penalty: float) -> None:
        super().__init__("黑魔化", "本体速+3；随身7×7内敌方速降低施放时剩余魔，最低1。")
        self.source_unit_id = caster_id
        self.speed_penalty = speed_penalty
        self.remaining_owner_ends = 3

    def caster(self, battle: Battle) -> Unit | None:
        caster = battle.units.get(self.source_unit_id)
        return caster if caster is not None and caster.alive and not caster.banished and caster.position is not None else None

    def affected_cells(self, battle: Battle) -> list[Position]:
        caster = self.caster(battle)
        return square_around_cells(battle, battle.unit_cells(caster), radius=3) if caster is not None else []

    def modify_unit_stat(self, battle: Battle, unit: Unit, stat_name: str, value: float) -> float:
        caster = self.caster(battle)
        if stat_name != "speed" or caster is None:
            return value
        if unit is caster:
            return value + 3
        if (unit.player_id == caster.player_id or unit.position is None
                or battle.distance_between_units(caster, unit) > 3
                or unit.magic_immunity or unit.skill_non_damage_effects_blocked()):
            return value
        return max(1.0, value - self.speed_penalty)

    def on_any_turn_end(self, battle: Battle, ended_player_id: int) -> None:
        caster = self.caster(battle)
        if caster is None:
            battle.remove_field_effect(self)
            return
        if battle.unit_belongs_to_current_turn(caster):
            self.remaining_owner_ends -= 1
            if self.remaining_owner_ends <= 0:
                battle.remove_field_effect(self)

    def to_public_dict(self, battle: Battle) -> dict[str, Any]:
        data = super().to_public_dict(battle)
        data.update(duration=self.remaining_owner_ends, speed_penalty=self.speed_penalty)
        return data


class BlackTransformationSkill(PointCostSkill):
    def __init__(self) -> None:
        super().__init__("black_transformation", "黑魔化", "消耗3魔力点；三轮速+3，7×7敌方按施放时剩余魔降速。",
                         point_cost=3, target_mode="self")

    def execute(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> None:
        for effect in list(battle.field_effects):
            if isinstance(effect, BlackTransformationField) and effect.source_unit_id == actor.unit_id:
                battle.remove_field_effect(effect)
        penalty = actor.current_mana
        battle.add_field_effect(BlackTransformationField(actor.unit_id, penalty), source=actor)
        battle.log_public_event(f"{actor.name} 黑魔化，速+3；周围敌方速最多-{penalty:g}，持续3本人轮。", source=actor)

    def preview(self, battle: Battle, actor: Unit) -> dict[str, Any]:
        cells = square_around_cells(battle, battle.unit_cells(actor), radius=3)
        return {"cells": [cell.to_dict() for cell in cells],
                "target_unit_ids": [unit.unit_id for unit in battle.effect_units_at_cells(cells)
                                    if unit.player_id != actor.player_id],
                "secondary_cells": [], "requires_target": False}

    def get_target_cells_for_payload(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> list[Position]:
        return square_around_cells(battle, battle.unit_cells(actor), radius=3)

    def get_target_units_for_payload(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> list[Unit]:
        return [unit for unit in battle.effect_units_at_cells(self.get_target_cells_for_payload(battle, actor, payload))
                if unit.player_id != actor.player_id]


class BlackNightShadowSkill(PointCostSkill):
    def __init__(self) -> None:
        super().__init__("black_night_shadow", "黑夜魔影", "消耗1魔力点；公开掷d6，身体外邻双方每段受攻2技能伤。",
                         point_cost=1, target_mode="self")

    def queued_payload_metadata(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> dict[str, Any]:
        return {"declared_shadow_cells": [cell.to_dict() for cell in _outer_ring(battle, actor)]}

    def cells(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> list[Position]:
        declared = battle.payload_positions(payload, "declared_shadow_cells")
        return declared if declared else _outer_ring(battle, actor)

    def execute(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> None:
        rolled = 3 if getattr(battle, "_ai_probe_active", False) else random.randint(1, 6)
        cells = self.cells(battle, actor, payload)
        battle.log_public_event(f"{actor.name} 的【黑夜魔影】掷出 {rolled} 点，结算 {rolled} 段。", source=actor)
        for segment in range(1, rolled + 1):
            battle.queue_area_damage_effect(
                actor=actor, display_name=f"黑夜魔影第{segment}段", cells=cells,
                attack_power=2, tags={"skill", self.code}, exclude_actor=True,
                refresh_cell_targets=True, segment_index=segment, segment_count=rolled,
            )

    def preview(self, battle: Battle, actor: Unit) -> dict[str, Any]:
        cells = _outer_ring(battle, actor)
        return {"cells": [cell.to_dict() for cell in cells],
                "target_unit_ids": [unit.unit_id for unit in battle.effect_units_at_cells(cells, ignore=actor)],
                "secondary_cells": [], "requires_target": False,
                "roll_range": [1, 6], "damage_segments": "后端公开骰面次数，每段独立结算"}

    def get_target_cells_for_payload(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> list[Position]:
        return self.cells(battle, actor, payload)

    def get_target_units_for_payload(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> list[Unit]:
        return list(battle.effect_units_at_cells(self.cells(battle, actor, payload), ignore=actor))


class EnterDarknessSkill(Skill):
    def __init__(self) -> None:
        super().__init__("enter_darkness", "入魔", "0魔；魔力点+1，源未写每轮限次。", target_mode="self")

    def execute(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> None:
        gained = actor.gain_mana_points(1)
        battle.log_public_event(f"{actor.name} 使用【入魔】，魔力点+{gained:g}。", source=actor)

    def preview(self, battle: Battle, actor: Unit) -> dict[str, Any]:
        return {"cells": [cell.to_dict() for cell in battle.unit_cells(actor)],
                "target_unit_ids": [actor.unit_id], "secondary_cells": [], "requires_target": False}
