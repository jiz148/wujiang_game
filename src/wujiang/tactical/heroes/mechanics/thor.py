"""Blind evasion and Thor line, impact, and lightning objects."""

from __future__ import annotations
from wujiang.tactical.heroes.ai_impact_rule import R18_IMPACT

from typing import Any
from wujiang.tactical.engine.core import ActionError
from wujiang.tactical.engine.core import Battle
from wujiang.tactical.engine.core import DamageContext
from wujiang.tactical.engine.core import HeroUnit
from wujiang.tactical.engine.core import Position
from wujiang.tactical.engine.core import StatusEffect
from wujiang.tactical.engine.core import Trait
from wujiang.tactical.heroes.ai_policy import AISkillScoreContext
from wujiang.tactical.heroes.common import FlagStatus
from wujiang.tactical.heroes.common import match_payload_pattern
from wujiang.tactical.heroes.common import pattern_selection_preview
from wujiang.tactical.heroes.common import positions_to_dict
from wujiang.tactical.heroes.mechanics.fusion_rush import FusionCircleAttackTrait
from wujiang.tactical.heroes.mechanics.h1_line_damage import H1LineDamageSkill
from wujiang.tactical.heroes.mechanics.next_five_effects import damage_followup_effect_applies


class BlindEvasionAttackResetTrait(Trait):
    def __init__(self) -> None:
        super().__init__("回避后重置普攻", "普攻被实际回避完整躲开后，本回合已用攻击次数归零。")

    def on_basic_attack_finished(
        self,
        battle: Battle,
        actor: HeroUnit,
        payload: dict[str, Any],
        damage_contexts: list[DamageContext],
        missed: bool,
    ) -> None:
        owner = self.owner
        if owner is None or actor.unit_id != owner.unit_id or not owner.alive or payload.get("basic_attack_cancelled") or not payload.get("completed_evasion_unit_ids"):
            return
        cells = set(battle.payload_positions(payload, "attack_cells"))
        hit_ids = {ctx.target.unit_id for ctx in damage_contexts if not ctx.cancelled}
        for unit_id in payload["completed_evasion_unit_ids"]:
            unit = battle.units.get(str(unit_id))
            if unit is None or not unit.alive or unit.banished or unit.unit_id in hit_ids:
                continue
            if not cells or any(cell in cells for cell in battle.unit_cells(unit)):
                continue
            owner.attacks_used = 0
            battle.log(f"{owner.name} 的普攻被回避，本回合攻击次数重置。")
            return


class BlindCircleAttackTrait(FusionCircleAttackTrait):
    def __init__(self) -> None:
        Trait.__init__(self, "攻击一周", "普攻同时命中自身周围 8 格内的全部敌方单位。")

    def basic_attack_preview(
        self,
        battle: Battle,
        actor: HeroUnit,
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        cells = self.basic_attack_area_cells(battle, actor, payload or {}) or []
        target_ids = [
            unit.unit_id
            for unit in battle.effect_units_at_cells(cells)
            if unit.player_id != actor.player_id and battle.unit_can_be_selected(unit, actor=actor)[0]
        ]
        return {
            "cells": positions_to_dict(cells),
            "target_unit_ids": target_ids,
            "secondary_cells": positions_to_dict(battle.unit_cells(actor)),
            "requires_target": True,
            "selection": {"mode": "pattern_cells", "patterns": [positions_to_dict(cells)]},
        }


class ThorLineDamageSkill(H1LineDamageSkill):
    ai_effective_impact_rule = R18_IMPACT
    excludes_allies_from_effect = True
    target_cells_are_geometry_only = True

    def ai_priority_score(self, context: AISkillScoreContext) -> float:
        return context.r18_effect_score(self)

    def affected_units(self, battle: Battle, actor: HeroUnit, cells: list[Position]) -> list[HeroUnit]:
        return [unit for unit in battle.effect_units_at_cells(cells) if unit.player_id != actor.player_id]

    def resolve_hit(self, battle: Battle, actor: HeroUnit, target: HeroUnit, cells: list[Position]) -> DamageContext:
        return battle.resolve_damage(DamageContext(
            source=actor, target=target, attack_power=actor.stat("attack"), is_skill=True,
            action_name=self.name, area_cell_hits=battle.unit_hit_count_for_cells(target, cells),
            tags={"skill", self.code},
        ))

    def preview(self, battle: Battle, actor: HeroUnit) -> dict[str, Any]:
        preview = pattern_selection_preview(self.patterns(battle, actor))
        cells = [cell for pattern in self.patterns(battle, actor) for cell in pattern]
        preview.update({"target_unit_ids": [unit.unit_id for unit in self.affected_units(battle, actor, cells)
                                           if battle.unit_can_be_selected(unit, actor=actor)[0]],
                        "secondary_cells": [], "requires_target": True})
        return preview


class ThorHeavyHammerSkill(ThorLineDamageSkill):
    def ai_reaction_mana_pressure(self, battle: Battle, queued: Any) -> float:
        pressure = 0.0
        for unit_id in queued.target_unit_ids:
            target = battle.get_unit(unit_id)
            paid = [target.get_skill(option.action_code).mana_cost_for_payload(battle, target, {})
                    for option in battle.available_reaction_options(target, queued) if option.action_type == "skill"]
            pressure += max((min(25.0, cost * 16.0) for cost in paid if cost > 0), default=0.0)
        return pressure

    def __init__(self) -> None:
        super().__init__(
            "thor_heavy_hammer",
            "重锤",
            "普通技能：费 1 魔，每回合一次；攻击相邻 1 格，敌方连锁技能魔耗翻倍。",
            length=1,
            mana_cost=1,
            max_uses_per_turn=1,
        )

    def queued_payload_metadata(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> dict[str, Any]:
        return {"reaction_mana_multiplier": 2.0}


class ThorRageImpactSkill(ThorLineDamageSkill):
    def __init__(self) -> None:
        super().__init__(
            "thor_rage_impact",
            "暴怒冲击",
            "普通技能：费1魔每回合1次；声明前4敌方伤害格与合法第5落点，伤后尝试固定落点，后来受阻仍完成伤害。",
            length=4,
            mana_cost=1,
            max_uses_per_turn=1,
        )

    def patterns(self, battle: Battle, actor: HeroUnit) -> list[list[Position]]:
        if actor.cannot_move:
            return []
        return [cells for cells in super().patterns(battle, actor) if len(cells) == 4
                and battle.can_place_unit(actor, self.landing(cells), ignore=actor, mover=actor)]

    @staticmethod
    def landing(cells: list[Position]) -> Position:
        return cells[-1].offset(cells[1].x - cells[0].x, cells[1].y - cells[0].y)

    def queued_payload_metadata(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> dict[str, Any]:
        cells = match_payload_pattern(payload, self.patterns(battle, actor))
        return {"thor_rage_cells": positions_to_dict(cells), "thor_rage_landing": self.landing(cells).to_dict()}

    def chosen_cells(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> list[Position]:
        if payload.get("queued_resolution") and "thor_rage_cells" in payload:
            return [Position(**cell) for cell in payload["thor_rage_cells"]]
        return super().chosen_cells(battle, actor, payload)

    def execute(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> None:
        cells = self.chosen_cells(battle, actor, payload)
        landing = Position(**payload["thor_rage_landing"]) if payload.get("queued_resolution") and "thor_rage_landing" in payload else self.landing(cells)
        for target in self.affected_units(battle, actor, cells):
            if not actor.alive or actor.banished:
                break
            self.resolve_hit(battle, actor, target, cells)
        if actor.position is None or not actor.alive or actor.banished:
            return
        actor.position = getattr(actor, "_resolution_actual_position", None) or actor.position
        try:
            battle.move_unit(actor, landing, via_skill=True, ignore_units=True, allow_anywhere=True,
                             max_distance=5, tags={self.code})
        except ActionError:
            battle.log(f"{actor.name} 已完成【暴怒冲击】伤害，但无法到达声明第5格。")

    def preview(self, battle: Battle, actor: HeroUnit) -> dict[str, Any]:
        preview = super().preview(battle, actor)
        preview["secondary_cells"] = positions_to_dict([self.landing(cells) for cells in self.patterns(battle, actor)])
        return preview


class H1AllActionLockStatus(StatusEffect):
    locked_flags = ("cannot_move", "cannot_attack", "cannot_use_skills")

    def bind(self, owner: HeroUnit) -> "H1AllActionLockStatus":
        super().bind(owner)
        for flag in self.locked_flags:
            setattr(owner, flag, True)
        return self

    def on_removed(self, battle: Battle) -> None:
        owner = self.owner
        if owner is None:
            return
        for flag in self.locked_flags:
            still_locked = any(
                flag in getattr(status, "locked_flags", ())
                or (isinstance(status, FlagStatus) and status.flag_name == flag and status.value)
                for status in owner.statuses
            )
            if flag in {"cannot_attack", "cannot_use_skills"} and owner.is_clone:
                still_locked = True
            setattr(owner, flag, still_locked)


class ThorDestroyedLightningStatus(H1AllActionLockStatus):
    def __init__(self) -> None:
        super().__init__("毁灭电击", "待下个自己的行动回合开始，期间不能任何行动。", duration=None, tick_scope="owner_turn_end")
        self.active = False
        self.pending = True

    def bind(self, owner: HeroUnit) -> "ThorDestroyedLightningStatus":
        StatusEffect.bind(self, owner)
        return self

    @property
    def locked_flags(self) -> tuple[str, ...]:
        return ("cannot_move", "cannot_attack", "cannot_use_skills") if self.active else ()

    def on_owner_turn_start(self, battle: Battle) -> None:
        self.active, self.pending = True, False
        self.description = "本回合不能普通移动、普攻、主动技能或任何被动反应。"
        for flag in self.locked_flags:
            setattr(self.owner, flag, True)

    def on_owner_turn_end(self, battle: Battle) -> None:
        if not self.active:
            return
        queued_next = self.pending
        self.owner.remove_status(self, battle)
        if queued_next:
            self.owner.add_status(ThorDestroyedLightningStatus())

    def allows_block_counter(self, battle: Battle, actor: HeroUnit) -> bool:
        return not self.active


class ThorDestroyLightningSkill(ThorLineDamageSkill):
    def __init__(self) -> None:
        super().__init__(
            "thor_destroy_lightning",
            "毁灭电击",
            "普通技能：费 1 魔，每回合一次；攻击前 5 格，雷属性无效，其他有效命中者下回合不能行动。",
            length=5,
            mana_cost=1,
            max_uses_per_turn=1,
        )

    def affected_units(self, battle: Battle, actor: HeroUnit, cells: list[Position]) -> list[HeroUnit]:
        return [unit for unit in super().affected_units(battle, actor, cells) if unit.attribute != "雷"]

    def target_can_react_to_effect(self, battle: Battle, actor: HeroUnit, target: HeroUnit, payload: dict[str, Any]) -> bool:
        return target.attribute != "雷" and target.player_id != actor.player_id

    def resolve_hit(self, battle: Battle, actor: HeroUnit, target: HeroUnit, cells: list[Position]) -> DamageContext:
        ctx = super().resolve_hit(battle, actor, target, cells)
        if damage_followup_effect_applies(ctx) and target.alive:
            effect = battle.validate_target(actor, target, action_name="毁灭电击封锁", is_skill=True,
                                            is_hostile=True, resolve_defenses=ctx.cancelled and not ctx.shield_consumed,
                                            ignore_targeting_restrictions=True)
            if effect.cancelled:
                return ctx
            existing = target.get_status("毁灭电击")
            if existing is not None:
                existing.pending = True
            else:
                target.add_status(ThorDestroyedLightningStatus())
        return ctx


