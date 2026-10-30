"""R022 mimic skill wrapper and selection behavior."""

from __future__ import annotations

from contextlib import contextmanager
from typing import Any

from wujiang.tactical.engine.core import ActionError, Battle, HeroUnit, Position, Skill
from wujiang.tactical.heroes.common import (
    ensure_ally, ensure_enemy, match_payload_pattern, payload_position,
    positions_to_dict,
)

class MimicSkill(Skill):
    excluded_codes = {"mimic_skill", "agency_borrowed_skill", "agency_contract"}

    def __init__(self) -> None:
        super().__init__("mimic_skill", "模仿", "普通技能：消耗1魔力点，模仿11×11内可见武将主动/瞬发技能；实际魔费减3，保留其他成本及自身限制。", target_mode="unit")
        self.requires_direct_unit_target_line = False
        self.borrowed: dict[str, Skill] = {}

    def runtime_children(self):
        return self.borrowed.values()

    def copyable(self, skill: Skill) -> bool:
        return skill.timing in {"active", "instant"} and skill.code not in self.excluded_codes

    def source_skills(self, source: HeroUnit) -> list[Skill]:
        # An installed borrowed skill is an execution aid, not a new public provider.
        return getattr(source, "_mimic_source_skills", source.skills)

    def targets(self, battle: Battle, actor: HeroUnit) -> list[HeroUnit]:
        return [unit for unit in battle.all_units()
                if not unit.is_summon and not unit.is_clone and battle.unit_can_be_selected(unit, actor=actor)[0]
                and battle.distance_between_units(actor, unit) <= 5
                and any(self.copyable(skill) for skill in self.source_skills(unit))]

    def borrowed_skill(self, actor: HeroUnit, source: HeroUnit, code: str) -> Skill:
        definition = next((skill for skill in self.source_skills(source) if skill.code == code), None)
        if definition is None:
            raise ActionError("该武将没有可提供的模仿技能。")
        if not self.copyable(definition):
            raise ActionError("不能模仿被动技能或借技包装技能。")
        own = next((skill for skill in actor.skills if skill.code == code), None)
        if own is not None:
            return own
        if code not in self.borrowed:
            fresh = next((skill for skill in source.build_skills() if skill.code == code), None)
            if fresh is None:
                raise ActionError("该技能没有可模仿的公开定义。")
            fresh.bind(actor)
            legacy = getattr(actor, "_mimic_uses", {}).get(code, {})
            fresh.uses_this_battle = int(legacy.get("battle_uses", 0))
            fresh.uses_this_turn = int(legacy.get("turn_uses", 0))
            fresh._uses_turn_number = legacy.get("turn")
            self.borrowed[code] = fresh
        return self.borrowed[code]

    @contextmanager
    def copying(self, actor: HeroUnit, copied: Skill, *, reserve_point: bool = False):
        # Quote the discounted mana cost while preserving all custom payment behavior.
        skills, points = actor.skills, actor.mana_points
        source_skills = getattr(actor, "_mimic_source_skills", None)
        actor._mimic_source_skills = source_skills if source_skills is not None else skills
        point_reserved = getattr(actor, "_mimic_point_reserved", False)
        reserve_here = reserve_point and not point_reserved
        original_cost = copied.mana_cost_for_payload
        nested = getattr(copied, "_mimic_discount_active", False)
        had_override = "mana_cost_for_payload" in copied.__dict__
        old_override = copied.__dict__.get("mana_cost_for_payload")
        actor.skills = [*skills] if copied in skills else [*skills, copied]
        if not nested:
            copied.mana_cost_for_payload = lambda battle, caster, payload=None: max(0.0, original_cost(battle, caster, payload) - 3.0)
            copied._mimic_discount_active = True
        if reserve_here:
            actor.mana_points -= 1
            actor._mimic_point_reserved = True
        try:
            yield
        finally:
            actor.skills = skills
            if source_skills is None:
                actor.__dict__.pop("_mimic_source_skills", None)
            else:
                actor._mimic_source_skills = source_skills
            if not nested:
                copied.__dict__.pop("_mimic_discount_active", None)
            if reserve_here:
                actor.mana_points = points
                actor.__dict__.pop("_mimic_point_reserved", None)
            if had_override:
                copied.mana_cost_for_payload = old_override
            else:
                copied.__dict__.pop("mana_cost_for_payload", None)

    def _target_skill(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> tuple[HeroUnit, Skill, dict[str, Any]]:
        source = battle.get_unit(str(payload.get("mimic_source_id") or payload.get("target_unit_id") or ""))
        if source not in self.targets(battle, actor):
            raise ActionError("模仿只能选择11×11内可见武将本体。")
        code = str(payload.get("mimic_skill_code") or payload.get("copied_skill_code") or "")
        copied = self.borrowed_skill(actor, source, code)
        inner = dict(payload.get("mimic_payload") or payload.get("copied_payload") or {})
        for key in list(inner):
            if key.startswith(("declared_", "_")) or key in {"resources_prepaid", "queued_resolution", "resolved_target_unit_id", "declaration_id"}:
                inner.pop(key)
        inner.update(type="skill", unit_id=actor.unit_id, skill_code=code)
        return source, copied, inner

    def can_use(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any] | None = None) -> tuple[bool, str]:
        ok, reason = super().can_use(battle, actor, payload)
        if not ok:
            return ok, reason
        if actor.mana_points < 1:
            return False, "魔力点不足。"
        if not payload:
            return (True, "") if self.targets(battle, actor) else (False, "周围没有可模仿的武将。")
        try:
            _, copied, inner = self._target_skill(battle, actor, payload)
            with self.copying(actor, copied, reserve_point=True):
                return copied.can_use(battle, actor, inner)
        except (ActionError, AttributeError, KeyError, TypeError, ValueError) as exc:
            return False, str(exc) or "当前不满足被模仿技能的施放条件。"

    def validate_inner_selection(self, battle: Battle, actor: HeroUnit, copied: Skill, inner: dict[str, Any]) -> None:
        preview = copied.preview(battle, actor)
        if copied.target_mode in {"ally", "enemy", "unit"} and inner.get("target_unit_id"):
            target = battle.get_unit(inner["target_unit_id"])
            if copied.target_mode == "ally":
                ensure_ally(actor, target)
            elif copied.target_mode == "enemy":
                ensure_enemy(actor, target)
            if preview.get("requires_target") and target.unit_id not in preview.get("target_unit_ids", []):
                raise ActionError("请选择被模仿技能允许的目标。")
        elif copied.target_mode in {"ally", "enemy", "unit"} and preview.get("requires_target") and not inner.get("target_unit_ids") and not inner.get("cells"):
            raise ActionError("请为被模仿技能选择实际目标。")
        selection = preview.get("selection") or {}
        patterns = selection.get("patterns")
        if selection.get("mode") == "choice_pattern":
            choice = next((item for item in selection.get("choices", []) if item.get("code") == inner.get("choice_code")), None)
            if choice is None:
                raise ActionError("请先选择被模仿技能的模式。")
            patterns = choice.get("patterns", [])
        if selection.get("mode") in {"pattern_cells", "choice_pattern"}:
            match_payload_pattern(inner, [[Position(int(cell["x"]), int(cell["y"])) for cell in pattern] for pattern in patterns or []])
        elif copied.target_mode == "cell" and preview.get("requires_target") and inner.get("x") is not None:
            if payload_position(inner).to_dict() not in preview.get("cells", []):
                raise ActionError("被模仿技能的落点不合法。")

    def build_wrapped_action(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]):
        source, copied, inner = self._target_skill(battle, actor, payload)
        with self.copying(actor, copied, reserve_point=True):
            self.validate_inner_selection(battle, actor, copied, inner)
            declared = battle.build_queued_action(inner)
        inner = dict(declared.payload)
        declared.payload.update(skill_code=self.code, mimic_source_id=source.unit_id,
                                mimic_source_name=source.name, mimic_skill_code=copied.code, copied_payload=inner)
        declared.display_name = f"模仿·{copied.name}"
        return declared

    def declared_skill(self, actor: HeroUnit, payload: dict[str, Any]) -> Skill:
        code = str(payload.get("mimic_skill_code") or "")
        return next((skill for skill in actor.skills if skill.code == code), None) or self.borrowed[code]

    def effective_skill_for_payload(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> Skill:
        if payload.get("mimic_skill_code"):
            return self.declared_skill(actor, payload)
        return self._target_skill(battle, actor, payload)[1]

    def prepay_resources(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> None:
        if not payload.get("mimic_source_id") or not payload.get("queued_resolution"):
            ok, reason = self.can_use(battle, actor, payload)
            if not ok:
                raise ActionError(reason)
            payload.update(self.build_wrapped_action(battle, actor, payload).payload)
        copied = self.declared_skill(actor, payload)
        actor.spend_mana_points(1)
        with self.copying(actor, copied):
            copied.prepay_resources(battle, actor, payload["copied_payload"])
        self.sync_turn_scope(battle)
        self.uses_this_turn += 1
        self.uses_this_battle += 1

    def on_owner_action_declared(self, battle: Battle, action_type: str, payload: dict[str, Any]) -> None:
        if self.owner is not None and action_type == "skill" and payload.get("skill_code") == self.code:
            copied = self.declared_skill(self.owner, payload)
            copied.on_owner_action_declared(battle, action_type, payload["copied_payload"])

    def execute(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> None:
        copied = self.declared_skill(actor, payload)
        inner = dict(payload["copied_payload"])
        inner.update(resources_prepaid=True, queued_resolution=True)
        target = battle.resolve_declared_target_unit(actor, inner, ignore_stealth=bool(inner.get("ignore_stealth")))
        inner["resolved_target_unit_id"] = target.unit_id if target is not None else None
        battle.log(f"{actor.name} 模仿 {payload.get('mimic_source_name', '武将')} 的【{copied.name}】。")
        try:
            with self.copying(actor, copied):
                battle.use_skill(actor, copied.code, inner)
        finally:
            actor.gain_mana_points(0)

    def reaction_window_timing(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]):
        copied = self.declared_skill(actor, payload)
        return copied.reaction_window_timing(battle, actor, payload["copied_payload"])

    def on_owner_turn_start(self, battle: Battle) -> None:
        super().on_owner_turn_start(battle)
        for skill in [item for item in self.borrowed.values() if item not in self.owner.skills]:
            skill.on_owner_turn_start(battle)

    def on_owner_turn_end(self, battle: Battle) -> None:
        for skill in [item for item in self.borrowed.values() if item not in self.owner.skills]:
            skill.on_owner_turn_end(battle)

    def on_any_turn_end(self, battle: Battle, ended_player_id: int) -> None:
        super().on_any_turn_end(battle, ended_player_id)
        for skill in [item for item in self.borrowed.values() if item not in self.owner.skills]:
            skill.on_any_turn_end(battle, ended_player_id)

    def preview(self, battle: Battle, actor: HeroUnit) -> dict[str, Any]:
        entries = []
        for source in self.targets(battle, actor):
            skills = []
            for definition in self.source_skills(source):
                if not self.copyable(definition):
                    continue
                copied = self.borrowed_skill(actor, source, definition.code)
                try:
                    with self.copying(actor, copied, reserve_point=True):
                        ok, reason = copied.can_use(battle, actor, {})
                        data = copied.to_public_dict(battle)
                        preview = battle.filter_preview_targets(
                            actor, copied.preview(battle, actor), ignore_stealth=copied.ignores_stealth_for_payload(battle, actor, {}),
                            replace_cells=copied.target_mode in {"ally", "enemy", "unit"},
                            require_line_targeting=copied.target_mode in {"ally", "enemy", "unit"} and copied.requires_direct_unit_target_line,
                            line_target_range=copied.direct_unit_target_range(battle, actor, {}))
                    data.update(kind="skill", available=ok and actor.mana_points >= 1, reason=reason, preview=preview)
                    skills.append({"code": copied.code, "name": copied.name, "action": data})
                except (ActionError, AttributeError, KeyError, TypeError, ValueError):
                    continue
            entries.append({"unit_id": source.unit_id, "name": source.name, "skills": skills})
        return {"cells": positions_to_dict([cell for source in self.targets(battle, actor) for cell in battle.unit_cells(source)]),
                "target_unit_ids": [entry["unit_id"] for entry in entries], "requires_target": True,
                "selection": {"mode": "mimic_skill", "targets": entries}}

    def get_target_units_for_payload(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> list[HeroUnit]:
        _, copied, inner = self._target_skill(battle, actor, payload)
        with self.copying(actor, copied):
            return copied.get_target_units_for_payload(battle, actor, inner)

    def get_target_cells_for_payload(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> list[Position]:
        _, copied, inner = self._target_skill(battle, actor, payload)
        with self.copying(actor, copied):
            return copied.get_target_cells_for_payload(battle, actor, inner)


