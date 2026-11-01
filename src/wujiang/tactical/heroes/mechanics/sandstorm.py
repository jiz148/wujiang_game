"""Shared sandstorm battlefield objects."""

from __future__ import annotations

from typing import Any
from wujiang.tactical.engine.core import ActionError
from wujiang.tactical.engine.core import Battle
from wujiang.tactical.engine.core import BattleFieldEffect
from wujiang.tactical.engine.core import DamageContext
from wujiang.tactical.engine.core import HeroUnit
from wujiang.tactical.engine.core import Position
from wujiang.tactical.engine.core import QueuedAction
from wujiang.tactical.engine.core import Skill
from wujiang.tactical.heroes.ai_policy import AISkillCandidateContext, AISkillScoreContext
from wujiang.tactical.heroes.common import match_payload_pattern
from wujiang.tactical.heroes.common import pattern_selection_preview
from wujiang.tactical.heroes.common import pattern_signature
from wujiang.tactical.heroes.common import positions_to_dict
from wujiang.tactical.heroes.mechanics.next_five_effects import remote_rectangle_patterns
from wujiang.tactical.heroes.mechanics.next_five_geometry import position_key
from wujiang.tactical.heroes.mechanics.next_five_geometry import square_around_cells


class SandstormWeatherEffect(BattleFieldEffect):
    weather_name = "沙尘"
    global_weather = True

    def __init__(self, *, duration: int = 2, source_unit_id: str | None = None) -> None:
        super().__init__(
            "沙尘",
            "全场天气：回合结束时非土单位受天气伤害；飞行 1/8，其他 1/16；沙尘中不能隐身，回避距离 -1。",
            duration=duration,
        )
        self.source_unit_id = source_unit_id

    def on_turn_start(self, battle: Battle, active_unit: HeroUnit | None) -> None:
        if self.source_unit_id and active_unit is not None and active_unit.unit_id == self.source_unit_id:
            battle.remove_field_effect(self)

    def merge_into_existing(self, battle: Battle, existing_effects: list[BattleFieldEffect]) -> bool:
        for effect in list(existing_effects):
            weather_name = getattr(effect, "weather_name", None)
            if weather_name != self.weather_name:
                continue
            if isinstance(effect, RockGodSandstormAura):
                continue
            if self.duration is not None:
                effect.duration = max(int(effect.duration or 0), self.duration)
            if isinstance(effect, SandstormWeatherEffect):
                effect.source_unit_id = self.source_unit_id
            battle.log("天气【沙尘】刷新。")
            return True
        return False

    def board_marker(self, battle: Battle) -> str:
        return "沙"

    def on_any_turn_end(self, battle: Battle, ended_player_id: int) -> None:
        for unit in list(battle.all_units()):
            if not unit.alive or unit.banished or unit.position is None:
                continue
            if not battle.unit_belongs_to_current_turn(unit):
                continue
            if unit.attribute == "土":
                continue
            if getattr(unit, "_sandstorm_damage_turn", None) == battle.turn_number:
                continue
            unit._sandstorm_damage_turn = battle.turn_number
            damage = 0.125 if unit.has_flying else 0.0625
            battle.resolve_damage(
                DamageContext(
                    source=None,
                    target=unit,
                    attack_power=0,
                    is_skill=False,
                    action_name="沙尘",
                    from_field_effect=True,
                    cannot_evade=True,
                    raw_damage=damage,
                    tags={"weather", "sandstorm"},
                )
            )
        if self.source_unit_id is None:
            super().on_any_turn_end(battle, ended_player_id)
        elif self.source_unit_id not in battle.units:
            battle.remove_field_effect(self)


class WindSandSkill(Skill):
    def ai_priority_score(self, context: AISkillScoreContext) -> float:
        return context.shared.wind_sand_score(context)

    def __init__(self) -> None:
        super().__init__(
            "wind_sand",
            "风沙",
            "普通技能：每回合最多 1 次，远程选择 2*4 或 4*2 区域；按当前攻造成伤害，若范围内有单位则全场沙尘持续到自己下个回合开始。沙尘使土属性单位免受天气伤害，飞行单位每个自己的回合末失去1/8生命，其他单位失去1/16生命；范围内不能隐身且回避距离-1。",
            max_uses_per_turn=1,
            target_mode="cell",
        )

    def patterns(self, battle: Battle, actor: HeroUnit) -> list[list[Position]]:
        patterns = [*remote_rectangle_patterns(battle, actor, 2, 4), *remote_rectangle_patterns(battle, actor, 4, 2)]
        unique: list[list[Position]] = []
        seen: set[tuple[tuple[int, int], ...]] = set()
        for pattern in patterns:
            key = pattern_signature(pattern)
            if key in seen:
                continue
            seen.add(key)
            unique.append(pattern)
        return unique

    def chosen_cells(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> list[Position]:
        return match_payload_pattern(payload, self.patterns(battle, actor))

    def resolve_weather_effect(self, battle: Battle, actor: HeroUnit, queued_action: QueuedAction) -> None:
        battle.add_field_effect(SandstormWeatherEffect(duration=1, source_unit_id=actor.unit_id))

    def queued_payload_metadata(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> dict[str, Any]:
        return {"declared_area_cells": positions_to_dict(self.chosen_cells(battle, actor, payload))}

    def execute(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> None:
        cells = battle.payload_positions(payload, "declared_area_cells") if "declared_area_cells" in payload else self.chosen_cells(battle, actor, payload)
        targets = battle.units_at_cells(cells)
        for unit in targets:
            battle.resolve_damage(
                DamageContext(
                    source=actor,
                    target=unit,
                    attack_power=actor.stat("attack"),
                    is_skill=True,
                    action_name="风沙",
                    area_cell_hits=battle.unit_hit_count_for_cells(unit, cells),
                    tags={"skill", "wind_sand"},
                )
            )
        if targets:
            weather_effect = battle.build_skill_effect_action(
                actor=actor,
                display_name="风沙",
                effect_code="sandstorm_weather",
                payload={"duration": 2},
                include_cell_units=False,
                hostile=False,
                effect_resolver=self.resolve_weather_effect,
            )
            battle.resolve_skill_effect(actor, weather_effect)

    def preview(self, battle: Battle, actor: HeroUnit) -> dict[str, Any]:
        preview = pattern_selection_preview(self.patterns(battle, actor))
        cell_keys = {(cell["x"], cell["y"]) for cell in preview["cells"]}
        targets = [
            unit.unit_id
            for unit in battle.all_units()
            if any((cell.x, cell.y) in cell_keys for cell in battle.unit_cells(unit))
        ]
        preview.update({"target_unit_ids": targets, "secondary_cells": [], "requires_target": True})
        return preview

    def get_target_cells_for_payload(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> list[Position]:
        return self.chosen_cells(battle, actor, payload)

    def get_target_units_for_payload(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> list[HeroUnit]:
        return [unit for unit in battle.units_at_cells(self.chosen_cells(battle, actor, payload))]  # type: ignore[list-item]


class CrazySandSkill(Skill):
    excludes_caster_from_effect = True

    def ai_candidate_payloads(self, context: AISkillCandidateContext) -> list[dict[str, Any]]:
        preview = context.action.get("preview", {}) or {}
        return [{**context.base_payload, "cells": entry["pattern"],
                 "x": entry["destination"]["x"], "y": entry["destination"]["y"]}
                for entry in preview.get("pattern_destinations", [])]

    def __init__(self) -> None:
        super().__init__(
            "crazy_sand",
            "狂沙",
            "普通技能：冷却 2轮，选择有效方向；直线 5 格造成当前攻伤害，并瞬移到第 6 格，第 6 格越界或被占用则不能选择。",
            cooldown_turns=2,
            target_mode="cell",
        )

    def directions(self) -> list[tuple[int, int]]:
        return [(-1, -1), (-1, 0), (-1, 1), (0, -1), (0, 1), (1, -1), (1, 0), (1, 1)]

    def can_use(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any] | None = None) -> tuple[bool, str]:
        ok, reason = super().can_use(battle, actor, payload)
        if not ok:
            return ok, reason
        if actor.cannot_move:
            return False, "当前不能使用狂沙位移。"
        return (True, "") if self.patterns(battle, actor) else (False, "没有可放置完整身体的狂沙落点。")

    def _patterns_with_destinations(self, battle: Battle, actor: HeroUnit) -> list[tuple[list[Position], Position]]:
        if actor.position is None:
            return []
        result: list[tuple[list[Position], Position]] = []
        seen: set[tuple[tuple[int, int], ...]] = set()
        actor_cells = battle.unit_cells(actor)
        actor_cell_keys = {(cell.x, cell.y) for cell in actor_cells}
        for dx, dy in self.directions():
            destination = actor.position.offset(dx * 6, dy * 6)
            if not battle.can_place_unit(actor, destination, ignore=actor, mover=actor):
                continue
            front_origins = [
                cell
                for cell in actor_cells
                if (cell.x + dx, cell.y + dy) not in actor_cell_keys
            ]
            for origin in front_origins:
                line = battle.line_positions(origin, (dx, dy), 5)
                if len(line) != 5:
                    continue
                if any((cell.x, cell.y) in actor_cell_keys for cell in line):
                    continue
                key = pattern_signature(line)
                if key in seen:
                    continue
                seen.add(key)
                result.append((line, destination))
        return result

    def patterns(self, battle: Battle, actor: HeroUnit) -> list[list[Position]]:
        return [line for line, _ in self._patterns_with_destinations(battle, actor)]

    def chosen_line_and_destination(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> tuple[list[Position], Position]:
        chosen = match_payload_pattern(payload, self.patterns(battle, actor))
        signature = pattern_signature(chosen)
        for line, destination in self._patterns_with_destinations(battle, actor):
            if pattern_signature(line) == signature:
                return chosen, destination
        raise ActionError("该狂沙方向当前不可用。")

    def execute(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> None:
        if "declared_area_cells" in payload:
            line = battle.payload_positions(payload, "declared_area_cells")
            destination = Position(int(payload["crazy_sand_dest_x"]), int(payload["crazy_sand_dest_y"]))
        else:
            line, destination = self.chosen_line_and_destination(battle, actor, payload)
        for unit in battle.units_at_cells(line):
            if unit.unit_id == actor.unit_id:
                continue
            battle.resolve_damage(
                DamageContext(
                    source=actor,
                    target=unit,
                    attack_power=actor.stat("attack"),
                    is_skill=True,
                    action_name="狂沙",
                    area_cell_hits=battle.unit_hit_count_for_cells(unit, line),
                    tags={"skill", "crazy_sand"},
                )
            )
        if actor.alive and actor.position is not None:
            if actor.cannot_move or not battle.can_place_unit(actor, destination, ignore=actor, mover=actor):
                battle.log(f"{actor.name} 的狂沙落点已受阻，保留原位。")
                return
            battle.move_unit(
                actor,
                destination,
                via_skill=True,
                allow_anywhere=True,
                max_distance=6,
                tags={"crazy_sand"},
            )

    def queued_payload_metadata(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> dict[str, Any]:
        line, destination = self.chosen_line_and_destination(battle, actor, payload)
        return {"declared_area_cells": positions_to_dict(line), "crazy_sand_dest_x": destination.x, "crazy_sand_dest_y": destination.y}

    def preview(self, battle: Battle, actor: HeroUnit) -> dict[str, Any]:
        preview = pattern_selection_preview(self.patterns(battle, actor))
        cell_keys = {(cell["x"], cell["y"]) for cell in preview["cells"]}
        targets = [
            unit.unit_id
            for unit in battle.all_units()
            if unit.unit_id != actor.unit_id and any((cell.x, cell.y) in cell_keys for cell in battle.unit_cells(unit))
        ]
        preview["pattern_destinations"] = [
            {"pattern": positions_to_dict(line), "destination": destination.to_dict(),
             "destination_cells": positions_to_dict(battle.unit_cells_at(actor, destination))}
            for line, destination in self._patterns_with_destinations(battle, actor)
        ]
        preview.update({"target_unit_ids": targets, "secondary_cells": [], "requires_target": True})
        return preview

    def get_target_cells_for_payload(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> list[Position]:
        line, _ = self.chosen_line_and_destination(battle, actor, payload)
        return line

    def get_target_units_for_payload(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> list[HeroUnit]:
        line, _ = self.chosen_line_and_destination(battle, actor, payload)
        return [unit for unit in battle.units_at_cells(line) if unit.unit_id != actor.unit_id]  # type: ignore[list-item]



class RockGodSandstormAura(BattleFieldEffect):
    weather_name = "沙尘"

    def __init__(self, owner_unit_id: str) -> None:
        super().__init__("岩神沙尘", "岩神每个占用格周围 9*9 的局部沙尘：土属性单位免受天气伤害，飞行单位每个自己的回合末失去1/8生命，其他单位失去1/16生命；范围内不能隐身且回避距离-1。", duration=None)
        self.owner_unit_id = owner_unit_id
        self.owner_unit_ids = {owner_unit_id}

    def get_owner_unit(self, battle: Battle) -> HeroUnit | None:
        owners = self.get_owner_units(battle)
        return owners[0] if owners else None

    def get_owner_units(self, battle: Battle) -> list[HeroUnit]:
        owners: list[HeroUnit] = []
        for owner_id in sorted(self.owner_unit_ids):
            unit = battle.units.get(owner_id)
            if unit is None or not unit.alive or unit.position is None or unit.banished:
                continue
            owners.append(unit)  # type: ignore[arg-type]
        return owners

    def affected_cells(self, battle: Battle) -> list[Position]:
        owners = self.get_owner_units(battle)
        if not owners:
            return []
        cells: list[Position] = []
        for owner in owners:
            cells.extend(battle.unit_cells(owner))
        return square_around_cells(battle, cells, radius=4)

    def board_marker(self, battle: Battle) -> str:
        return "沙"

    def merge_into_existing(self, battle: Battle, existing_effects: list[BattleFieldEffect]) -> bool:
        for effect in existing_effects:
            if isinstance(effect, RockGodSandstormAura):
                effect.owner_unit_ids.add(self.owner_unit_id)
                return True
        return False

    def on_any_turn_end(self, battle: Battle, ended_player_id: int) -> None:
        owners = self.get_owner_units(battle)
        self.owner_unit_ids = {unit_id for unit_id in self.owner_unit_ids if unit_id in battle.units and battle.units[unit_id].alive}
        if not self.owner_unit_ids:
            battle.remove_field_effect(self)
            return
        if any(isinstance(effect, SandstormWeatherEffect) for effect in battle.field_effects):
            return
        area_keys = {position_key(cell) for cell in self.affected_cells(battle)}
        for unit in list(battle.all_units()):
            if not unit.alive or unit.banished or unit.position is None:
                continue
            if not battle.unit_belongs_to_current_turn(unit):
                continue
            if not any(position_key(cell) in area_keys for cell in battle.unit_cells(unit)):
                continue
            if unit.attribute == "土":
                continue
            if getattr(unit, "_sandstorm_damage_turn", None) == battle.turn_number:
                continue
            unit._sandstorm_damage_turn = battle.turn_number
            damage = 0.125 if unit.has_flying else 0.0625
            battle.resolve_damage(
                DamageContext(
                    source=None,
                    target=unit,
                    attack_power=0,
                    is_skill=False,
                    action_name="沙尘",
                    from_field_effect=True,
                    cannot_evade=True,
                    raw_damage=damage,
                    tags={"weather", "sandstorm"},
                )
            )


