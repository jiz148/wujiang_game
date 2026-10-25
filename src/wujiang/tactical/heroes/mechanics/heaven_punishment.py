"""R070 heaven punishment and skill disable."""

from __future__ import annotations

from typing import Any
from wujiang.tactical.engine.core import ActionError
from wujiang.tactical.engine.core import ActionMiss
from wujiang.tactical.engine.core import Battle
from wujiang.tactical.engine.core import DamageContext
from wujiang.tactical.engine.core import HeroUnit
from wujiang.tactical.engine.core import Position
from wujiang.tactical.engine.core import Skill
from wujiang.tactical.engine.core import StatusEffect
from wujiang.tactical.heroes.common import match_payload_pattern
from wujiang.tactical.heroes.common import pattern_selection_preview
from wujiang.tactical.heroes.common import payload_target_unit
from wujiang.tactical.heroes.next_five import apply_piercing_status_effect
from wujiang.tactical.heroes.next_five import damage_followup_effect_applies
from wujiang.tactical.heroes.next_five import remote_rectangle_patterns


class SkillDisabledStatus(StatusEffect):
    def __init__(self, skill_code: str, skill_name: str) -> None:
        self.skill_code = skill_code
        super().__init__(f"技能封印：{skill_name}", f"不能使用技能【{skill_name}】。", duration=None)

    def blocks_skill_use(self, battle: Battle, actor: HeroUnit, skill: Skill) -> tuple[bool, str]:
        owner = self.owner
        if owner is not None and actor.unit_id == owner.unit_id and skill.code == self.skill_code:
            return True, f"技能【{skill.name}】已被封印，不能使用。"
        return False, ""


class HeavenPunishmentSkill(Skill):
    target_unit_is_anchor = True

    def __init__(self) -> None:
        super().__init__(
            "heaven_punishment",
            "天罚",
            "普通技能：2轮一次；5*5双方普通伤害，另选其中一名敌人的一项公开主动技能施加破魔封印。",
            cooldown_turns=2,
            target_mode="cell",
        )

    def patterns(self, battle: Battle, actor: HeroUnit) -> list[list[Position]]:
        return remote_rectangle_patterns(battle, actor, 5, 5)

    def chosen_cells(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> list[Position]:
        return match_payload_pattern(payload, self.patterns(battle, actor))

    @staticmethod
    def public_active_skills(battle: Battle, unit: HeroUnit) -> list[Skill]:
        source = unit
        if unit.is_clone:
            root_id = battle.controlling_hero_id(unit)
            source = battle.units.get(root_id, unit)
        return [skill for skill in source.skills if skill.timing == "active"]

    def has_public_active_skill(self, battle: Battle, unit: HeroUnit) -> bool:
        return bool(self.public_active_skills(battle, unit))

    def can_use(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any] | None = None) -> tuple[bool, str]:
        ok, reason = super().can_use(battle, actor, payload)
        if not ok or not payload:
            return ok, reason
        try:
            cells = self.chosen_cells(battle, actor, payload)
            target = self.choose_target(battle, actor, payload, cells)
            self.choose_skill(battle, target, payload)
        except ActionError as exc:
            return False, str(exc)
        return True, ""

    def affected_enemies(self, battle: Battle, actor: HeroUnit, cells: list[Position]) -> list[HeroUnit]:
        return [
            unit
            for unit in battle.units_at_cells(cells)
            if unit.player_id != actor.player_id and self.has_public_active_skill(battle, unit)
            and battle.unit_can_be_selected(unit, actor=actor)[0]
        ]  # type: ignore[list-item]

    def choose_target(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any], cells: list[Position]) -> HeroUnit:
        enemies = self.affected_enemies(battle, actor, cells)
        if not enemies:
            if payload.get("queued_resolution"):
                raise ActionMiss("【天罚】落在原定区域，没有可封印公开主动技能的敌方单位。")
            raise ActionError("天罚区域内没有可封印公开主动技能的敌方单位。")
        if not payload.get("target_unit_id"):
            raise ActionError("天罚需要选择区域内的一名敌方单位。")
        target = payload_target_unit(battle, payload)
        if target.unit_id not in {unit.unit_id for unit in enemies}:
            raise ActionError("指定目标不在天罚区域内，或没有可封印的公开主动技能。")
        return target

    def choose_skill(self, battle: Battle, target: HeroUnit, payload: dict[str, Any]) -> Skill:
        active_skills = self.public_active_skills(battle, target)
        if not active_skills:
            raise ActionError("目标没有可封印的公开主动技能。")
        selected = str(payload.get("disabled_skill_code") or "").strip()
        if not selected:
            raise ActionError("天罚需要选择目标当前公开的一项主动技能。")
        for skill in active_skills:
            if skill.code == selected:
                return skill
        raise ActionError("只能封印目标当前公开的主动技能。")

    def execute(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> None:
        cells = self.chosen_cells(battle, actor, payload)
        selected_recipient = None
        selected_skill = None
        try:
            target = self.choose_target(battle, actor, payload, cells)
            selected_skill = self.choose_skill(battle, target, payload)
            selected_recipient = battle.effect_recipient(target)
        except ActionError:
            if not payload.get("queued_resolution"):
                raise
            battle.log("【天罚】原定封印目标失效，没有可封印的公开主动技能；范围伤害仍在原定区域结算。")
        for recipient in battle.effect_units(battle.units_at_cells(cells)):
            ctx = battle.resolve_damage(DamageContext(
                source=actor, target=recipient, attack_power=actor.stat("attack"), is_skill=True,
                action_name="天罚", area_cell_hits=battle.unit_hit_count_for_cells(recipient, cells),
                tags={"skill", "heaven_punishment"},
            ))
            if selected_recipient is not None and selected_skill is not None and recipient.unit_id == selected_recipient.unit_id and recipient.alive and damage_followup_effect_applies(ctx, allow_on_shield_break=True):
                apply_piercing_status_effect(
                    battle, actor, recipient, action_name="天罚封印",
                    status=SkillDisabledStatus(selected_skill.code, selected_skill.name), is_skill=True,
                    consume_shield=not ctx.shield_consumed,
                    tags={"skill", "heaven_punishment"},
                )

    def ignores_shield_for_payload(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> bool:
        return True

    def target_can_react_to_effect(self, battle: Battle, actor: HeroUnit, target: HeroUnit, payload: dict[str, Any]) -> bool:
        selected = battle.units.get(str(payload.get("target_unit_id") or ""))
        selected_id = battle.effect_recipient(selected).unit_id if selected is not None else None
        return target.unit_id == selected_id or target.total_shields() == 0

    def preview(self, battle: Battle, actor: HeroUnit) -> dict[str, Any]:
        preview = pattern_selection_preview(self.patterns(battle, actor))
        cell_keys = {(cell["x"], cell["y"]) for cell in preview["cells"]}
        targets = [
            unit.unit_id
            for unit in battle.enemy_units(actor.player_id)
            if not unit.is_stealthed() and self.has_public_active_skill(battle, unit)
            and any((cell.x, cell.y) in cell_keys for cell in battle.unit_cells(unit))
        ]
        options = [
            {"unit_id": unit.unit_id, "name": unit.name, "skills": [
                {"code": skill.code, "name": skill.name}
                for skill in self.public_active_skills(battle, unit)
            ]}
            for unit in battle.enemy_units(actor.player_id)
            if unit.unit_id in targets and not unit.is_stealthed()
        ]
        preview.update({"target_unit_ids": targets, "secondary_cells": [], "requires_target": True,
                        "heaven_punishment_targets": options})
        return preview

    def get_target_cells_for_payload(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> list[Position]:
        return self.chosen_cells(battle, actor, payload)

    def get_target_units_for_payload(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> list[HeroUnit]:
        cells = self.chosen_cells(battle, actor, payload)
        if payload.get("target_unit_id"):
            return [self.choose_target(battle, actor, payload, cells)]
        return self.affected_enemies(battle, actor, cells)


