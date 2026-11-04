"""R123 Fuma pursuit, traps, and shuriken."""

from __future__ import annotations

from wujiang.tactical.heroes.ai_candidate_plan import (
    DAMAGE_DEDUPED_RANKED_CANDIDATES,
    RANKED_CANDIDATES,
)

from typing import Any
from wujiang.tactical.engine.core import ActionError
from wujiang.tactical.engine.core import Battle
from wujiang.tactical.engine.core import BattleFieldEffect
from wujiang.tactical.engine.core import DamageContext
from wujiang.tactical.engine.core import HeroUnit
from wujiang.tactical.engine.core import Position
from wujiang.tactical.engine.core import Skill
from wujiang.tactical.engine.core import Trait
from wujiang.tactical.engine.core import Unit
from wujiang.tactical.heroes.ai_policy import AISkillScoreContext, HeroAIPolicy
from wujiang.tactical.heroes.common import match_payload_pattern
from wujiang.tactical.heroes.common import pattern_selection_preview
from wujiang.tactical.heroes.common import pattern_signature
from wujiang.tactical.heroes.common import payload_position
from wujiang.tactical.heroes.common import positions_to_dict
from wujiang.tactical.heroes.mechanics.remi_bat import _payload_direction
from wujiang.tactical.heroes.mechanics.next_five_geometry import ALL_DIRECTIONS
from wujiang.tactical.heroes.mechanics.next_five_geometry import square_around_cells
from wujiang.tactical.prediction import random


class FumaAIPolicy(HeroAIPolicy):
    def skill_score_bonus(self, battle: Battle, actor: Unit, action: dict,
                          payload: dict, score: float) -> float:
        if score <= 0:
            return 0.0
        skill = action.get("_skill_object") or actor.get_skill(str(action.get("code") or payload.get("skill_code") or ""))
        if skill.timing != "active":
            return 0.0
        after_payment = actor.current_mana - skill.mana_cost_for_payload(battle, actor, payload)
        return 0.5 * min(1.0, max(0.0, actor.max_mana() - after_payment)) * 24.0


class FumaPursuitSkill(Skill):
    def ai_move_score(self, context: AISkillScoreContext, base_score: float,
                      destination: Position) -> float:
        return base_score + context.shared.fuma_pursuit_score(context, self)

    def __init__(self) -> None:
        super().__init__(
            "fuma_pursuit",
            "追身",
            "普通技能：3 轮一次；向指定方向攻击前 4 格并移动到第 5 格，伤害破魔。",
            cooldown_turns=3,
            target_mode="cell",
            direction_mode="required",
        )

    def line_for_direction(self, battle: Battle, actor: HeroUnit, direction: tuple[int, int]) -> list[Position]:
        if actor.position is None:
            raise ActionError("单位不在战场上。")
        line = battle.line_positions(actor.position, direction, 5)
        if len(line) < 5:
            raise ActionError("追身需要完整的 5 格直线路径。")
        if not battle.can_place_unit(actor, line[4], ignore=actor, mover=actor):
            raise ActionError("追身第 5 格必须是合法落点。")
        return line

    def queued_payload_metadata(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> dict[str, Any]:
        line = self.line_for_direction(battle, actor, _payload_direction(payload))
        if payload_position(payload) != line[4]:
            raise ActionError("追身必须选择该方向的第5格。")
        return {"fuma_pursuit_cells": positions_to_dict(line[:4]), "fuma_pursuit_landing": line[4].to_dict()}

    def damage_cells(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> list[Position]:
        if payload.get("queued_resolution") and "fuma_pursuit_cells" in payload:
            return battle.payload_positions(payload, "fuma_pursuit_cells")
        return self.line_for_direction(battle, actor, _payload_direction(payload))[:4]

    def get_target_cells_for_payload(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> list[Position]:
        return self.damage_cells(battle, actor, payload)

    def get_target_units_for_payload(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> list[HeroUnit]:
        return battle.effect_units_at_cells(self.damage_cells(battle, actor, payload))

    def execute(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> None:
        damage_cells = self.damage_cells(battle, actor, payload)
        landing = (Position(**payload["fuma_pursuit_landing"]) if payload.get("queued_resolution") and "fuma_pursuit_landing" in payload
                   else self.line_for_direction(battle, actor, _payload_direction(payload))[4])
        for unit in battle.effect_units_at_cells(damage_cells):
            battle.resolve_damage(
                DamageContext(
                    source=actor,
                    target=unit,
                    attack_power=actor.stat("attack"),
                    is_skill=True,
                    action_name="追身",
                    ignore_shield=True,
                    area_cell_hits=battle.unit_hit_count_for_cells(unit, damage_cells),
                    tags={"skill", "fuma_pursuit"},
                )
            )
        if actor.alive and actor.position is not None and not actor.banished and not actor.cannot_move:
            actor.position = getattr(actor, "_resolution_actual_position", None) or actor.position
            if battle.can_place_unit(actor, landing, ignore=actor, mover=actor):
                try:
                    if landing != actor.position:
                        battle.move_unit(actor, landing, via_skill=True, allow_anywhere=True, tags={"fuma_pursuit"})
                except ActionError:
                    battle.log(f"{actor.name} 的追身完成攻击，但无法到达声明落点。")
            else:
                battle.log(f"{actor.name} 的追身完成攻击，但声明落点已被阻挡。")

    def preview(self, battle: Battle, actor: HeroUnit) -> dict[str, Any]:
        cells: list[Position] = []
        secondary: list[Position] = []
        for direction in ALL_DIRECTIONS:
            try:
                line = self.line_for_direction(battle, actor, direction)
            except ActionError:
                continue
            secondary.extend(line[:4])
            cells.append(line[4])
        return {"cells": [cell.to_dict() for cell in cells],
                "target_unit_ids": [unit.unit_id for unit in battle.effect_units_at_cells(secondary)
                                    if battle.unit_can_be_selected(unit, actor=actor)[0]],
                "secondary_cells": positions_to_dict(secondary), "requires_target": True}

    def ignores_shield_for_payload(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> bool:
        return True


class FumaTrapEffect(BattleFieldEffect):
    def __init__(self, source_unit_id: str, player_id: int, center: Position) -> None:
        self.source_unit_id = source_unit_id
        self.player_id = player_id
        self.center = center
        super().__init__("陷阱", "敌方回合结束时，对陷阱格和周围造成伤害值 3 的破魔伤害。", duration=None)

    def affected_cells(self, battle: Battle) -> list[Position]:
        return square_around_cells(battle, [self.center], radius=1)

    def board_marker(self, battle: Battle) -> str:
        return "陷"

    def merge_into_existing(self, battle: Battle, existing_effects: list[BattleFieldEffect]) -> bool:
        for effect in list(existing_effects):
            if isinstance(effect, FumaTrapEffect) and effect.source_unit_id == self.source_unit_id and effect.center == self.center:
                battle.remove_field_effect(effect)
        return False

    def on_any_turn_end(self, battle: Battle, ended_player_id: int) -> None:
        source = battle.units.get(self.source_unit_id)
        if source is None or not source.alive or source.banished or source.position is None:
            battle.remove_field_effect(self)
            return
        if ended_player_id == self.player_id:
            return
        cells = self.affected_cells(battle)
        for unit in battle.effect_units_at_cells(cells):
            battle.resolve_damage(
                DamageContext(
                    source=source,
                    target=unit,
                    attack_power=3,
                    is_skill=True,
                    action_name="陷阱",
                    ignore_shield=True,
                    from_field_effect=True,
                    area_cell_hits=battle.unit_hit_count_for_cells(unit, cells),
                    tags={"field", "trap"},
                )
            )


class FumaTrapSkill(Skill):
    ai_enemy_impact_requirement = False
    def ai_priority_score(self, context: AISkillScoreContext) -> float:
        return context.shared.fuma_trap_score(context)

    ai_candidate_plan = RANKED_CANDIDATES
    def __init__(self) -> None:
        super().__init__(
            "fuma_trap",
            "陷阱",
            "普通技能：费 0.5 魔，每回合最多 1 次；对范内一格设置陷阱，敌方回合结束时对该格和周围造成伤害值 3 的破魔伤害。",
            mana_cost=0.5,
            max_uses_per_turn=1,
            target_mode="cell",
        )

    def queued_payload_metadata(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> dict[str, Any]:
        center = payload_position(payload)
        if not battle.in_bounds(center) or battle.unit_distance_to_cell(actor, center) > actor.targeting_range():
            raise ActionError("陷阱目标格超出范围。")
        return {}

    def execute(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> None:
        self.queued_payload_metadata(battle, actor, payload)
        center = payload_position(payload)
        battle.add_field_effect(FumaTrapEffect(actor.unit_id, actor.player_id, center))

    def preview(self, battle: Battle, actor: HeroUnit) -> dict[str, Any]:
        cells = [
            Position(x, y)
            for x in range(battle.width)
            for y in range(battle.height)
            if battle.unit_distance_to_cell(actor, Position(x, y)) <= actor.targeting_range()
        ]
        return {"cells": [cell.to_dict() for cell in cells], "target_unit_ids": [], "secondary_cells": [], "requires_target": True}


class FumaShurikenSkill(Skill):
    def ai_effective_enemy_impact(self, battle, actor, payload, targets=None, shared=None, action=None):
        if targets is None:
            return None
        return shared.reviewed_r16_damage_result(battle, actor,
                                                   lambda: self.execute(battle, actor, payload)) > 8.0

    def ai_priority_score(self, context: AISkillScoreContext) -> float:
        return context.shared.fuma_shuriken_score(context)

    ai_candidate_plan = DAMAGE_DEDUPED_RANKED_CANDIDATES
    def __init__(self) -> None:
        super().__init__(
            "fuma_shuriken",
            "风魔手里剑",
            "普通技能：每回合最多 1 次，范 3；选择连续 3 格直线，按当前攻击造成技能伤害。",
            max_uses_per_turn=1,
            target_mode="cell",
        )

    def patterns(self, battle: Battle, actor: HeroUnit) -> list[list[Position]]:
        if actor.position is None:
            return []
        patterns: list[list[Position]] = []
        seen: set[tuple[tuple[int, int], ...]] = set()
        for x in range(-2, battle.width):
            for y in range(-2, battle.height):
                start = Position(x, y)
                for direction in ALL_DIRECTIONS:
                    cells = [start.offset(direction[0] * i, direction[1] * i) for i in range(3)]
                    cells = [cell for cell in cells if battle.in_bounds(cell)]
                    if not cells:
                        continue
                    if not any(battle.unit_distance_to_cell(actor, cell) <= 3 for cell in cells):
                        continue
                    key = pattern_signature(cells)
                    if key in seen:
                        continue
                    seen.add(key)
                    patterns.append(cells)
        return patterns

    def chosen_cells(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> list[Position]:
        return match_payload_pattern(payload, self.patterns(battle, actor))

    def execute(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> None:
        cells = self.chosen_cells(battle, actor, payload)
        for unit in battle.effect_units_at_cells(cells):
            battle.resolve_damage(
                DamageContext(
                    source=actor,
                    target=unit,
                    attack_power=actor.stat("attack"),
                    is_skill=True,
                    action_name="风魔手里剑",
                    area_cell_hits=battle.unit_hit_count_for_cells(unit, cells),
                    tags={"skill", "fuma_shuriken"},
                )
            )

    def preview(self, battle: Battle, actor: HeroUnit) -> dict[str, Any]:
        preview = pattern_selection_preview(self.patterns(battle, actor))
        cell_keys = {(cell["x"], cell["y"]) for cell in preview["cells"]}
        targets = [
            unit.unit_id
            for unit in battle.all_units()
            if battle.unit_can_be_selected(unit, actor=actor)[0] and any((cell.x, cell.y) in cell_keys for cell in battle.unit_cells(unit))
        ]
        preview.update({"target_unit_ids": targets, "secondary_cells": [], "requires_target": True})
        return preview

    def get_target_cells_for_payload(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> list[Position]:
        return self.chosen_cells(battle, actor, payload)

    def get_target_units_for_payload(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> list[HeroUnit]:
        cells = self.chosen_cells(battle, actor, payload)
        return battle.effect_units_at_cells(cells)


class FumaSkillManaTrait(Trait):
    def __init__(self) -> None:
        super().__init__("风魔随机回魔", "每次使用主动技能时，后端公开随机；1/2 几率魔 +1。")

    def on_owner_removed(self, battle: Battle) -> None:
        if self.owner is not None:
            for effect in list(battle.field_effects):
                if isinstance(effect, FumaTrapEffect) and effect.source_unit_id == self.owner.unit_id:
                    battle.remove_field_effect(effect)

    def on_owner_banished(self, battle: Battle) -> None:
        self.on_owner_removed(battle)

    def on_owner_action_declared(self, battle: Battle, action_type: str, payload: dict[str, Any]) -> None:
        owner = self.owner
        if owner is None or action_type != "skill":
            return
        skill_code = str(payload.get("skill_code") or "")
        try:
            skill = owner.get_skill(skill_code)
        except ActionError:
            return
        if skill.timing != "active":
            return
        if getattr(battle, "_forecasting_action", False):
            return
        if random.random() < 0.5:
            gained = owner.gain_mana(1)
            battle.log_public_event(f"{owner.name} 的【风魔随机回魔】成功，获得 {gained} 点魔。", source=owner)
        else:
            battle.log_public_event(f"{owner.name} 的【风魔随机回魔】未触发。", source=owner)


