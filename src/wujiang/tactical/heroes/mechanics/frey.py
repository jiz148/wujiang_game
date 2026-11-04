"""R023 Frey quick flash and spear mechanics."""

from __future__ import annotations

from wujiang.tactical.heroes.ai_candidate_plan import RANKED_CANDIDATES

from typing import Any
from wujiang.tactical.engine.core import ActionError
from wujiang.tactical.engine.core import ActionMiss
from wujiang.tactical.engine.core import Battle
from wujiang.tactical.engine.core import DamageContext
from wujiang.tactical.engine.core import HeroUnit
from wujiang.tactical.engine.core import Position
from wujiang.tactical.engine.core import Skill
from wujiang.tactical.engine.core import TargetContext
from wujiang.tactical.engine.core import Trait
from wujiang.tactical.heroes.ai_policy import AISkillScoreContext, HeroAIPolicy
from wujiang.tactical.heroes.common import line_patterns
from wujiang.tactical.heroes.common import match_payload_pattern
from wujiang.tactical.heroes.common import pattern_selection_preview
from wujiang.tactical.heroes.common import pattern_signature
from wujiang.tactical.heroes.common import payload_position
from wujiang.tactical.heroes.common import positions_to_dict
from wujiang.tactical.heroes.mechanics.next_five_geometry import ALL_DIRECTIONS
from wujiang.tactical.heroes.mechanics.next_five_geometry import square_around_cells
from wujiang.tactical.prediction import expected_local


class FreyAreaAIScoring:
    """Shared area and landing valuation for Frey's three skill objects."""

    def ai_priority_score(self, context: AISkillScoreContext) -> float:
        return context.owned_effect_score(self)

    def ai_score_destination(self, context: AISkillScoreContext) -> Position | None:
        return None

    def ai_effect_score(self, context: AISkillScoreContext) -> float:
        battle, actor = context.battle, context.actor
        try:
            cells = context.effect_cells(self)
            destination = self.ai_score_destination(context)
        except (ActionError, ValueError, TypeError, KeyError):
            return -1000.0
        score = context.area_damage_value(self, cells)
        if destination is not None and actor.position is not None:
            origin = actor.position
            role = context.hero_style()
            score += context.move_score(destination, role) - context.move_score(origin, role)

            @expected_local(battle)
            def pressure(position: Position) -> float:
                previous = actor.position
                actor.position = position
                try:
                    total = sum(
                        context.estimate_damage(actor, enemy.stat("attack")) * max(1, enemy.attack_actions_per_turn())
                        for enemy in context.hostile_units()
                        if not enemy.cannot_attack and battle.distance_between_units(actor, enemy) <= enemy.targeting_range()
                    )
                    return total * 100.0 + (100.0 if total >= actor.current_hp - 1e-9 and total > 0 else 0.0)
                finally:
                    actor.position = previous
            score += pressure(origin) - pressure(destination)
            return score - 12.0
        if score <= 0:
            return -1000.0
        return score - (28.0 if self.max_uses_per_battle is not None else 6.0)


class FreyAttackPolicy(HeroAIPolicy):
    """Value only the recoverable HP from a damaging basic attack."""

    def late_attack_synergy_bonus(self, battle: Battle, actor: HeroUnit,
                                  target: HeroUnit, damage: float) -> float:
        if damage > 0 and not actor.cannot_heal:
            return min(0.25, max(0.0, actor.max_health - actor.current_hp)) * 140.0
        return 0.0


class FreySkillPierceTrait(Trait):
    all_skills_pierce_shields = True

    def __init__(self) -> None:
        super().__init__("所有技能破魔", "芙蕾的所有技能伤害和技能附带效果破魔。")

    def _is_owner_skill(self, ctx: TargetContext | DamageContext) -> bool:
        owner = self.owner
        source = ctx.actor if isinstance(ctx, TargetContext) else ctx.source
        return owner is not None and source is not None and source.unit_id == owner.unit_id and ctx.is_skill

    def on_targeted(self, battle: Battle, ctx: TargetContext) -> None:
        if self._is_owner_skill(ctx):
            ctx.ignore_shield = True

    def on_before_damage(self, battle: Battle, ctx: DamageContext) -> None:
        if self._is_owner_skill(ctx):
            ctx.ignore_shield = True


class FreyDamageCapTrait(Trait):
    def __init__(self) -> None:
        super().__init__("伤害封顶", "每个伤害实例及固定失血最多失去 1/4 生命。")

    def limit_hp_loss(self, amount: float) -> float:
        return min(0.25, amount)


class FreyQuickFlashSkill(FreyAreaAIScoring, Skill):
    ai_candidate_plan = RANKED_CANDIDATES

    def ai_unique_candidate_payloads(self, battle: Battle, actor: HeroUnit,
                                     payloads: list[dict[str, Any]]) -> list[dict[str, Any]]:
        unique: dict[tuple[Position, bool], dict[str, Any]] = {}
        for candidate in payloads:
            try:
                _, strike, destination = self.validate_selection(battle, actor, candidate)
                unique.setdefault((destination, strike), candidate)
            except (ActionError, ValueError, TypeError, KeyError):
                continue
        return list(unique.values())
    excludes_caster_from_effect = True
    target_unit_is_anchor = True
    selection_cells_are_effect_cells = False
    resolves_from_current_position = True

    def ai_score_destination(self, context: AISkillScoreContext) -> Position | None:
        return self.validate_selection(context.battle, context.actor, context.payload)[2]

    def __init__(self) -> None:
        super().__init__(
            "frey_quick_flash", "快闪",
            "普通技能：每回合最多2次，范5；选一个单位及其周围空格，瞬移后可选择对周围造成技能伤害。",
            max_uses_per_turn=2, target_mode="cell",
        )

    def direct_unit_target_range(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any] | None = None) -> int:
        return 5

    def selection(self, payload: dict[str, Any]) -> tuple[str, bool, Position]:
        choice = str(payload.get("choice_code") or "")
        if choice:
            parts = choice.split(":", 2)
            if len(parts) != 3 or parts[0] != "flash" or parts[1] not in {"strike", "move"}:
                raise ActionError("需要选择快闪锚单位和是否攻击。")
            target_id, strike = parts[2], parts[1] == "strike"
        else:
            target_id = str(payload.get("target_unit_id") or "")
            strike = payload.get("deal_damage", True)
            if not isinstance(strike, bool):
                raise ActionError("快闪伤害选择必须为是或否。")
        if not target_id:
            raise ActionError("需要选择快闪锚单位。")
        if payload.get("cells") is not None:
            cells = payload["cells"]
            if not isinstance(cells, list) or len(cells) != 1:
                raise ActionError("快闪只能选择一个落点。")
            destination = payload_position(cells[0])
        else:
            destination = payload_position(payload)
        return target_id, strike, destination

    def landing_is_empty(self, battle: Battle, actor: HeroUnit, destination: Position) -> bool:
        return destination != actor.position and all(
            battle.in_bounds(cell) and (cell.x, cell.y) not in battle.blocked_cells
            and not any(unit.unit_id != actor.unit_id for unit in battle.units_at(cell))
            for cell in battle.unit_cells_at(actor, destination)
        )

    def legal_destinations(self, battle: Battle, actor: HeroUnit, target: HeroUnit) -> list[Position]:
        target_cells = set(battle.unit_cells(target))
        return [cell for cell in square_around_cells(battle, list(target_cells), radius=1)
                if cell not in target_cells and self.landing_is_empty(battle, actor, cell)]

    def validate_selection(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> tuple[str, bool, Position]:
        if actor.cannot_move or actor.position is None or getattr(actor, "standable_terrain", False):
            raise ActionError("当前不能快闪移动。")
        target_id, strike, destination = self.selection(payload)
        target = battle.get_unit(target_id)
        battle.require_selectable_unit(target, actor=actor, action_name=self.name)
        battle.require_unit_target_in_range_and_line(actor, target, 5, action_name=self.name)
        if destination not in self.legal_destinations(battle, actor, target):
            raise ActionError("快闪需要目标周围的完整空落点。")
        return target_id, strike, destination

    def can_use(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any] | None = None) -> tuple[bool, str]:
        ok, reason = super().can_use(battle, actor, payload)
        if not ok:
            return ok, reason
        if actor.cannot_move or actor.position is None or getattr(actor, "standable_terrain", False):
            return False, "当前不能快闪移动。"
        if payload and any(key in payload for key in ("target_unit_id", "choice_code", "cells", "x", "y")):
            try:
                self.validate_selection(battle, actor, payload)
            except (ActionError, TypeError, ValueError, KeyError) as exc:
                return False, str(exc)
        return True, ""

    def prepay_resources(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any] | None = None) -> None:
        self.validate_selection(battle, actor, payload or {})
        super().prepay_resources(battle, actor, payload)

    def ring(self, battle: Battle, actor: HeroUnit, destination: Position) -> list[Position]:
        body = set(battle.unit_cells_at(actor, destination))
        return [cell for cell in square_around_cells(battle, list(body), radius=1) if cell not in body]

    def get_target_cells_for_payload(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> list[Position]:
        _, strike, destination = self.selection(payload)
        return self.ring(battle, actor, destination) if strike else []

    def get_target_units_for_payload(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> list[HeroUnit]:
        return battle.effect_units_at_cells(self.get_target_cells_for_payload(battle, actor, payload), ignore=actor)

    def queued_payload_metadata(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> dict[str, Any]:
        target_id, strike, destination = self.validate_selection(battle, actor, payload)
        return {"frey_flash_anchor": target_id, "frey_flash_destination": destination.to_dict(),
                "frey_flash_cells": positions_to_dict(self.ring(battle, actor, destination) if strike else []),
                "frey_flash_strike": strike}

    def execute(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> None:
        declaration = payload if payload.get("queued_resolution") and "frey_flash_destination" in payload else self.queued_payload_metadata(battle, actor, payload)
        destination = payload_position(declaration["frey_flash_destination"])
        if actor.cannot_move or not self.landing_is_empty(battle, actor, destination):
            raise ActionMiss("快闪原落点已失效，本次瞬移和环伤落空。")
        battle.move_unit(actor, destination, via_skill=True, allow_anywhere=True,
                         max_distance=max(battle.width, battle.height), tags={"frey_quick_flash"})
        if not actor.alive or actor.banished:
            return
        cells = battle.payload_positions(declaration, "frey_flash_cells")
        for unit in battle.effect_units_at_cells(cells, ignore=actor):
            battle.resolve_damage(DamageContext(
                source=actor, target=unit, attack_power=actor.stat("attack"),
                is_skill=True, action_name=self.name,
                area_cell_hits=battle.unit_hit_count_for_cells(unit, cells),
                tags={"skill", self.code},
            ))

    def preview(self, battle: Battle, actor: HeroUnit) -> dict[str, Any]:
        choices, destinations = [], []
        legal_cells: set[Position] = set()
        if not actor.cannot_move and actor.position is not None:
            for target in battle.all_units():
                try:
                    battle.require_selectable_unit(target, actor=actor, action_name=self.name)
                    battle.require_unit_target_in_range_and_line(actor, target, 5, action_name=self.name)
                except ActionError:
                    continue
                cells = self.legal_destinations(battle, actor, target)
                if not cells:
                    continue
                legal_cells.update(cells)
                for strike in (False, True):
                    code = f"flash:{'strike' if strike else 'move'}:{target.unit_id}"
                    choices.append({"code": code, "label": f"{target.name}（{target.position.x},{target.position.y}）旁·{'瞬移并攻击' if strike else '仅瞬移'}",
                                    "patterns": [[cell.to_dict()] for cell in cells]})
                    for cell in cells:
                        destinations.append({"choice_code": code, "pattern": [cell.to_dict()],
                                             "destination_cells": positions_to_dict(self.ring(battle, actor, cell) if strike else [])})
        return {"cells": positions_to_dict(sorted(legal_cells, key=lambda cell: (cell.y, cell.x))),
                "target_unit_ids": [], "secondary_cells": [], "requires_target": True,
                "pattern_destinations": destinations,
                "selection": {"mode": "choice_pattern", "choices": choices, "ordered": False,
                              "choice_prompt": "先选择锚单位及是否攻击，再选择一个蓝色空落点。",
                              "cell_prompt": "选择一个蓝色空落点；环形高亮显示本次攻击范围。"}}


class FreyGodStabSkill(FreyAreaAIScoring, Skill):
    ai_candidate_plan = RANKED_CANDIDATES
    excludes_caster_from_effect = True

    def __init__(self) -> None:
        super().__init__(
            "frey_god_stab",
            "神刺",
            "大招：一场战斗一次；选择一条最多 4 格直线，按当前攻击造成技能伤害。",
            max_uses_per_battle=1,
            target_mode="cell",
        )

    def patterns(self, battle: Battle, actor: HeroUnit) -> list[list[Position]]:
        patterns: list[list[Position]] = []
        seen: set[tuple[tuple[int, int], ...]] = set()
        for origin in battle.unit_cells(actor) or ([actor.position] if actor.position else []):
            if origin is None:
                continue
            for ray in line_patterns(battle, origin, ALL_DIRECTIONS, 4):
                # Start at the outward edge of a multi-cell body, never hit that body.
                if any(cell in battle.unit_cells(actor) for cell in ray):
                    continue
                for length in range(1, len(ray) + 1):
                    pattern = ray[:length]
                    key = pattern_signature(pattern)
                    if key not in seen:
                        seen.add(key)
                        patterns.append(pattern)
        return patterns

    def chosen_cells(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> list[Position]:
        return match_payload_pattern(payload, self.patterns(battle, actor))

    def queued_payload_metadata(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> dict[str, Any]:
        return {"frey_stab_cells": positions_to_dict(match_payload_pattern(payload, self.patterns(battle, actor)))}

    def prepay_resources(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any] | None = None) -> None:
        match_payload_pattern(payload or {}, self.patterns(battle, actor))
        super().prepay_resources(battle, actor, payload)

    def execute(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> None:
        cells = battle.payload_positions(payload, "frey_stab_cells") if payload.get("queued_resolution") and "frey_stab_cells" in payload else self.chosen_cells(battle, actor, payload)
        for unit in battle.effect_units_at_cells(cells, ignore=actor):
            if unit.unit_id == actor.unit_id:
                continue
            battle.resolve_damage(
                DamageContext(
                    source=actor,
                    target=unit,
                    attack_power=actor.stat("attack"),
                    is_skill=True,
                    action_name="神刺",
                    area_cell_hits=battle.unit_hit_count_for_cells(unit, cells),
                    tags={"skill", "frey_god_stab"},
                )
            )

    def preview(self, battle: Battle, actor: HeroUnit) -> dict[str, Any]:
        preview = pattern_selection_preview(self.patterns(battle, actor))
        cell_keys = {(cell["x"], cell["y"]) for cell in preview["cells"]}
        targets = [
            unit.unit_id
            for unit in battle.all_units()
            if unit.unit_id != actor.unit_id and any((cell.x, cell.y) in cell_keys for cell in battle.unit_cells(unit))
        ]
        preview.update({"target_unit_ids": targets, "secondary_cells": [], "requires_target": True})
        return preview

    def get_target_cells_for_payload(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> list[Position]:
        return self.chosen_cells(battle, actor, payload)

    def get_target_units_for_payload(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> list[HeroUnit]:
        cells = self.chosen_cells(battle, actor, payload)
        return battle.effect_units_at_cells(cells, ignore=actor)


class FreyLionSpearSkill(FreyAreaAIScoring, Skill):
    ai_candidate_plan = RANKED_CANDIDATES
    excludes_caster_from_effect = True

    def __init__(self) -> None:
        super().__init__(
            "frey_lion_spear",
            "狮子神枪",
            "普通技能：每回合最多 1 次；对所有斜线方向最多 4 格造成技能伤害。",
            max_uses_per_turn=1,
            target_mode="self",
        )

    def affected_cells(self, battle: Battle, actor: HeroUnit) -> list[Position]:
        result: list[Position] = []
        seen: set[tuple[int, int]] = set()
        for origin in battle.unit_cells(actor) or ([actor.position] if actor.position else []):
            if origin is None:
                continue
            for direction in [(-1, -1), (-1, 1), (1, -1), (1, 1)]:
                for cell in battle.line_positions(origin, direction, 4):
                    key = (cell.x, cell.y)
                    if key in seen or cell in battle.unit_cells(actor):
                        continue
                    seen.add(key)
                    result.append(cell)
        return result

    def get_target_cells_for_payload(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> list[Position]:
        return self.affected_cells(battle, actor)

    def get_target_units_for_payload(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> list[HeroUnit]:
        return battle.effect_units_at_cells(self.get_target_cells_for_payload(battle, actor, payload), ignore=actor)

    def queued_payload_metadata(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> dict[str, Any]:
        return {"frey_lion_cells": positions_to_dict(self.affected_cells(battle, actor))}

    def execute(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> None:
        cells = battle.payload_positions(payload, "frey_lion_cells") if payload.get("queued_resolution") and "frey_lion_cells" in payload else self.affected_cells(battle, actor)
        for unit in battle.effect_units_at_cells(cells, ignore=actor):
            if unit.unit_id == actor.unit_id:
                continue
            battle.resolve_damage(
                DamageContext(
                    source=actor,
                    target=unit,
                    attack_power=actor.stat("attack"),
                    is_skill=True,
                    action_name="狮子神枪",
                    area_cell_hits=battle.unit_hit_count_for_cells(unit, cells),
                    tags={"skill", "frey_lion_spear"},
                )
            )

    def preview(self, battle: Battle, actor: HeroUnit) -> dict[str, Any]:
        cells = self.affected_cells(battle, actor)
        cell_keys = {(cell.x, cell.y) for cell in cells}
        targets = [
            unit.unit_id
            for unit in battle.all_units()
            if unit.unit_id != actor.unit_id and any((cell.x, cell.y) in cell_keys for cell in battle.unit_cells(unit))
        ]
        return {"cells": [cell.to_dict() for cell in cells], "target_unit_ids": targets, "secondary_cells": [], "requires_target": False}

