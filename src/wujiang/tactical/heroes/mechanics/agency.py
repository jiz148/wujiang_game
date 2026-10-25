"""R025 agency contract, borrowed skill, and passive punishment."""

from __future__ import annotations

from contextlib import contextmanager
from typing import Any

from wujiang.tactical.engine.core import (
    ActionError, ActionMiss, Battle, DamageContext, HealContext, HeroUnit,
    Position, Skill, StatusEffect, TargetContext, Trait,
)
from wujiang.tactical.heroes.common import (
    StatModifierStatus, is_mana_drain_immune, positions_to_dict,
)
from wujiang.tactical.heroes.mechanics.mimic import MimicSkill
from wujiang.tactical.heroes.next_five import (
    damage_followup_effect_applies, square_around_cells,
)

class AgencyDefenseStatus(StatModifierStatus):
    def __init__(self) -> None:
        super().__init__("代行解除守备", defense_delta=4, duration=2, tick_scope="owner_turn_end", description="直到下回合结束前守 +4。")


class AgencyAttachedStatus(StatusEffect):
    provided_flags = ("cannot_move", "cannot_be_targeted")

    def __init__(self, carrier_id: str, stat_name: str, copied_skill_code: str) -> None:
        super().__init__("代行契约附着", "随载体移动，免伤免效果；借用一项当前属性及一个技能，保留自身攻击和施法。", duration=None)
        self.carrier_id, self.stat_name, self.copied_skill_code = carrier_id, stat_name, copied_skill_code

    def carrier(self, battle: Battle) -> HeroUnit | None:
        unit = battle.units.get(self.carrier_id)
        return unit if unit is not None and unit.alive else None

    def bind(self, owner: HeroUnit) -> "AgencyAttachedStatus":
        super().bind(owner)
        owner.attached_to_unit_id = self.carrier_id
        owner.cannot_move = owner.cannot_be_targeted = True
        return self

    def sync_linked_state(self, battle: Battle) -> None:
        owner, carrier = self.owner, self.carrier(battle)
        if owner is None or not owner.alive:
            return
        if carrier is None:
            self.detach(battle)
            return
        carrier_status = carrier.get_status("代行契约附着")
        if isinstance(carrier_status, AgencyAttachedStatus):
            carrier_status.sync_linked_state(battle)
        self.last_carrier_position = getattr(carrier, "_resolution_actual_position", carrier.position) or getattr(self, "last_carrier_position", owner.position)
        if not hasattr(owner, "_resolution_actual_position"):
            owner.position = self.last_carrier_position
        owner.banished = carrier.banished or carrier.position is None
        owner.banish_turns_remaining = carrier.banish_turns_remaining
        owner.banish_return_position = self.last_carrier_position

    def on_owner_turn_start(self, battle: Battle) -> None:
        self.sync_linked_state(battle)

    def on_unit_moved(self, battle: Battle, ctx: Any) -> None:
        self.sync_linked_state(battle)

    def detach(self, battle: Battle) -> None:
        owner = self.owner
        if owner is None:
            return
        carrier = self.carrier(battle)
        destroyed = next((unit for unit in battle.destroyed_units if unit.unit_id == self.carrier_id), None)
        origin = ((getattr(carrier, "_resolution_actual_position", carrier.position) if carrier else None)
                  or (getattr(destroyed, "last_position", None) if destroyed else None)
                  or getattr(self, "last_carrier_position", owner.position))
        owner.remove_status(self, battle)
        owner.banish_return_position = origin
        owner.banish_turns_remaining = 0
        choices = battle.respawn_options_for(owner)
        if choices:
            owner.position, owner.banished = choices[0], False
        else:
            owner.position, owner.banished = origin, True
            battle.log(f"{owner.name} 解除附着后没有合法落点，暂时等待重新出现。")

    def modify_stat(self, stat_name: str, value: float) -> float:
        ref = getattr(self.owner, "_battle_ref", None)
        battle = ref() if ref else None
        carrier = self.carrier(battle) if battle else None
        return carrier.stat(stat_name) if carrier is not None and stat_name == self.stat_name else value

    def blocks_direct_effects(self) -> bool:
        return True

    def limit_hp_loss(self, amount: float) -> float:
        return amount if getattr(self.owner, "_paying_skill_cost", False) else 0.0

    def accepts_status(self, status: StatusEffect) -> bool:
        return False

    def on_targeted(self, battle: Battle, ctx: TargetContext) -> None:
        if self.owner is ctx.target and not getattr(self.owner, "_paying_skill_cost", False):
            ctx.cancelled, ctx.reason = True, "附着期间不受伤害和效果影响。"

    def on_before_damage(self, battle: Battle, ctx: DamageContext) -> None:
        if self.owner is ctx.target and not getattr(self.owner, "_paying_skill_cost", False):
            ctx.cancelled, ctx.reason = True, "附着期间不受伤害和效果影响。"

    def on_before_heal(self, battle: Battle, ctx: HealContext) -> None:
        if self.owner is ctx.target:
            ctx.cancelled = True

    def on_removed(self, battle: Battle) -> None:
        owner = self.owner
        if owner is None:
            return
        owner.__dict__.pop("attached_to_unit_id", None)
        for flag in self.provided_flags:
            setattr(owner, flag, any(flag in getattr(status, "provided_flags", ()) or
                                    (getattr(status, "flag_name", "") in {flag, "cannot_act"} and getattr(status, "value", True))
                                    for status in owner.statuses))
        wrapper = next((skill for skill in owner.skills if skill.code == "agency_borrowed_skill"), None)
        if wrapper is not None:
            wrapper.timing = "active"

    def to_public_dict(self, battle: Battle) -> dict[str, Any]:
        data = super().to_public_dict(battle)
        data.update(carrier_id=self.carrier_id, stat_name=self.stat_name, copied_skill_code=self.copied_skill_code)
        return data


class AgencyBorrowedSkill(MimicSkill):
    MOVEMENT_SKILL_CODES = {"fly_leap", "fate_kick", "crazy_sand", "plasma_thruster", "zero_dash", "fuma_pursuit",
                           "fantasy_move", "true_blade_air_slash", "mounted_leap", "frey_quick_flash", "teleport",
                           "descent_moment", "earth_walker", "split", "leap", "jirobo_follow_step", "iron_chain_path", "ghost_step"}

    def __init__(self) -> None:
        Skill.__init__(self, "agency_borrowed_skill", "代行技能", "使用已选技能；完整费用与限制按暮别自身计算，跨契约保留。", target_mode="none")
        self.requires_direct_unit_target_line = False
        self.target_unit_is_anchor = True
        self.borrowed: dict[str, Skill] = {}

    @contextmanager
    def copying(self, actor: HeroUnit, copied: Skill, **kwargs):
        previous = actor.skills
        actor.skills = previous if copied in previous else [*previous, copied]
        try:
            yield
        finally:
            actor.skills = previous

    def attached_status(self, actor: HeroUnit) -> AgencyAttachedStatus:
        status = actor.get_status("代行契约附着")
        if not isinstance(status, AgencyAttachedStatus):
            raise ActionError("未处于代行契约附着状态。")
        return status

    def target_skill(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> tuple[HeroUnit, Skill, dict[str, Any]]:
        status = self.attached_status(actor)
        carrier = status.carrier(battle)
        if carrier is None or carrier.banished or carrier.position is None:
            raise ActionError("载体不在战场上。")
        code = status.copied_skill_code
        copied = next((item for item in actor.skills if item.code == code), None) or self.borrowed.get(code)
        if copied is None:
            copied = self.borrowed_skill(actor, carrier, code)
        inner = dict(payload.get("contract_payload") or payload.get("copied_payload") or {})
        for key in list(inner):
            if key.startswith(("declared_", "_")) or key in {"resources_prepaid", "queued_resolution", "resolved_target_unit_id", "declaration_id"}:
                inner.pop(key)
        inner.update(type="skill", unit_id=actor.unit_id, skill_code=code)
        return carrier, copied, inner

    _target_skill = target_skill

    def movement_blocked(self, copied: Skill) -> bool:
        return copied.code in self.MOVEMENT_SKILL_CODES

    def can_use(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any] | None = None) -> tuple[bool, str]:
        try:
            _, copied, inner = self.target_skill(battle, actor, payload or {})
            if self.movement_blocked(copied):
                return False, "附着期间不能使用独立位移技能。"
            with self.copying(actor, copied):
                return copied.can_use(battle, actor, inner)
        except (ActionError, KeyError, TypeError, ValueError) as exc:
            return False, str(exc)

    def mana_cost_for_payload(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any] | None = None) -> float:
        try:
            _, copied, inner = self.target_skill(battle, actor, payload or {})
            with self.copying(actor, copied):
                return copied.mana_cost_for_payload(battle, actor, inner)
        except ActionError:
            return 0.0

    def build_wrapped_action(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]):
        carrier, copied, inner = self.target_skill(battle, actor, payload)
        if self.movement_blocked(copied):
            raise ActionError("附着期间不能使用独立位移技能。")
        with self.copying(actor, copied):
            self.validate_inner_selection(battle, actor, copied, inner)
            reaction_to = battle.pending_chain.queued_action if battle.pending_chain is not None and copied.timing == "instant" else None
            declared = battle.build_queued_action(inner, reaction_to=reaction_to)
        inner = dict(declared.payload)
        declared.payload.update(skill_code=self.code, agency_skill_code=copied.code,
                                agency_carrier_id=carrier.unit_id, copied_payload=inner)
        declared.display_name = f"代行·{copied.name}"
        return declared

    def declared_skill(self, actor: HeroUnit, payload: dict[str, Any]) -> Skill:
        code = str(payload.get("agency_skill_code") or "")
        return next((item for item in actor.skills if item.code == code), None) or self.borrowed[code]

    def prepay_resources(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> None:
        if not payload.get("agency_skill_code"):
            payload.update(self.build_wrapped_action(battle, actor, payload).payload)
        copied = self.declared_skill(actor, payload)
        with self.copying(actor, copied):
            battle.prepay_skill_resources(copied, actor, payload["copied_payload"])
        self.sync_turn_scope(battle)
        self.uses_this_turn += 1
        self.uses_this_battle += 1

    def execute(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> None:
        copied = self.declared_skill(actor, payload)
        inner = dict(payload["copied_payload"])
        inner.update(resources_prepaid=True, queued_resolution=True)
        target = battle.resolve_declared_target_unit(actor, inner, ignore_stealth=bool(inner.get("ignore_stealth")))
        inner["resolved_target_unit_id"] = target.unit_id if target is not None else None
        with self.copying(actor, copied):
            battle.resolve_from_declared_origin(actor, inner, lambda: battle.use_skill(actor, copied.code, inner))

    def react(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any], queued_action: Any) -> None:
        copied = self.declared_skill(actor, payload)
        inner = dict(payload["copied_payload"], resources_prepaid=True, queued_resolution=True)
        target = battle.resolve_declared_target_unit(actor, inner, ignore_stealth=bool(inner.get("ignore_stealth")))
        inner["resolved_target_unit_id"] = target.unit_id if target is not None else None
        try:
            with self.copying(actor, copied):
                battle.resolve_from_declared_origin(actor, inner, lambda: copied.react(battle, actor, inner, queued_action))
                copied.finalize_use(battle, actor)
        except ActionMiss as exc:
            battle.log(str(exc) or "代行技能落在原定位置，未命中目标。")

    def can_react_to(self, battle: Battle, actor: HeroUnit, queued_action: Any) -> tuple[bool, str]:
        try:
            _, copied, _ = self.target_skill(battle, actor, {})
            if self.movement_blocked(copied):
                return False, "附着期间不能使用独立位移技能。"
            with self.copying(actor, copied):
                return copied.can_react_to(battle, actor, queued_action)
        except ActionError as exc:
            return False, str(exc)

    def can_react_with_payload(self, battle: Battle, actor: HeroUnit, queued_action: Any, payload=None):
        ok, reason = self.can_react_to(battle, actor, queued_action)
        if not ok:
            return ok, reason
        _, copied, inner = self.target_skill(battle, actor, payload or {})
        with self.copying(actor, copied):
            return copied.can_react_with_payload(battle, actor, queued_action, inner)

    def queued_reaction_payload_metadata(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]):
        return self.build_wrapped_action(battle, actor, payload).payload

    def preview(self, battle: Battle, actor: HeroUnit) -> dict[str, Any]:
        try:
            carrier, copied, _ = self.target_skill(battle, actor, {})
            with self.copying(actor, copied):
                ok, reason = self.can_use(battle, actor, {})
                data = copied.to_public_dict(battle)
                preview = battle.filter_preview_targets(actor, copied.preview(battle, actor),
                    replace_cells=copied.target_mode in {"ally", "enemy", "unit"},
                    require_line_targeting=copied.requires_direct_unit_target_line and copied.target_mode in {"ally", "enemy", "unit"},
                    line_target_range=copied.direct_unit_target_range(battle, actor, {}))
            data.update(kind="skill", preview=preview, available=ok, reason=reason)
            return {"cells": [], "target_unit_ids": [], "requires_target": True,
                    "selection": {"mode": "mimic_skill", "wrapper_code": self.code, "targets": [
                        {"unit_id": carrier.unit_id, "name": carrier.name, "skills": [
                            {"code": copied.code, "name": copied.name, "action": data}]}]}}
        except ActionError:
            return {"cells": [], "target_unit_ids": [], "requires_target": False}

    def reaction_preview(self, battle: Battle, actor: HeroUnit, queued_action: Any):
        preview = self.preview(battle, actor)
        ok, reason = self.can_react_to(battle, actor, queued_action)
        for target in preview.get("selection", {}).get("targets", []):
            for entry in target["skills"]:
                entry["action"].update(available=ok, reason=reason)
        return preview


class AgencyContractSkill(Skill):
    VALID_STATS = ("attack", "defense", "speed", "attack_range", "mana")
    excludes_allies_from_effect = True
    resolves_from_current_position = True
    selection_cells_are_effect_cells = False

    def __init__(self) -> None:
        super().__init__("agency_contract", "代行契约", "每自己回合1次，绑定可见友方英雄或主动解除。借一项当前属性和一个技能；解除守+4并对声明外圈敌人伤害吸魔。", target_mode="none", max_uses_per_turn=1)
        self.requires_direct_unit_target_line = False
        self.target_unit_is_anchor = True

    def wrapper(self, actor: HeroUnit) -> AgencyBorrowedSkill:
        return actor.get_skill("agency_borrowed_skill")

    def targets(self, battle: Battle, actor: HeroUnit) -> list[HeroUnit]:
        return [unit for unit in battle.player_units(actor.player_id)
                if unit is not actor and not unit.is_clone and not unit.is_summon
                and not getattr(unit, "attached_to_unit_id", None)
                and battle.unit_can_be_selected(unit, actor=actor)[0]
                and any(self.wrapper(actor).copyable(skill) for skill in unit.skills)]

    def validate_binding(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]):
        target = battle.units.get(str(payload.get("target_unit_id") or ""))
        if target not in self.targets(battle, actor):
            raise ActionError("请选择可见的其他友方英雄本体作为载体。")
        stat = str(payload.get("stat_name") or "")
        code = str(payload.get("copied_skill_code") or payload.get("contract_skill_code") or "")
        if stat not in self.VALID_STATS:
            raise ActionError("请选择要借用的一项属性。")
        copied = self.wrapper(actor).borrowed_skill(actor, target, code)
        return target, stat, copied

    def can_use(self, battle: Battle, actor: HeroUnit, payload=None):
        ok, reason = super().can_use(battle, actor, payload)
        if not ok or actor.has_status("代行契约附着"):
            return ok, reason
        if not payload:
            return (True, "") if self.targets(battle, actor) else (False, "没有可用载体。")
        try:
            self.validate_binding(battle, actor, payload)
            return True, ""
        except (ActionError, KeyError, ValueError, TypeError) as exc:
            return False, str(exc)

    def ring(self, battle: Battle, actor: HeroUnit) -> list[Position]:
        status = actor.get_status("代行契约附着")
        carrier = status.carrier(battle) if isinstance(status, AgencyAttachedStatus) else None
        body = battle.unit_cells(carrier) if carrier is not None else battle.unit_cells(actor)
        return [cell for cell in square_around_cells(battle, body, radius=1) if cell not in body]

    def queued_payload_metadata(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]):
        if actor.has_status("代行契约附着"):
            return {"agency_cancel": True, "agency_cells": positions_to_dict(self.ring(battle, actor))}
        target, stat, copied = self.validate_binding(battle, actor, payload)
        return {"agency_cancel": False, "agency_carrier_id": target.unit_id,
                "stat_name": stat, "copied_skill_code": copied.code}

    def get_target_cells_for_payload(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]):
        if payload.get("agency_cancel") or actor.has_status("代行契约附着"):
            return battle.payload_positions(payload, "agency_cells") if "agency_cells" in payload else self.ring(battle, actor)
        return []

    def get_target_units_for_payload(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]):
        cells = self.get_target_cells_for_payload(battle, actor, payload)
        return [unit for unit in battle.effect_units_at_cells(cells) if unit.player_id != actor.player_id]

    def prepay_resources(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> None:
        if "agency_cancel" not in payload:
            payload.update(self.queued_payload_metadata(battle, actor, payload))
        if not payload["agency_cancel"]:
            self.validate_binding(battle, actor, payload)
        super().prepay_resources(battle, actor, payload)

    def execute(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> None:
        if payload.get("agency_cancel"):
            attached = actor.get_status("代行契约附着")
            if not isinstance(attached, AgencyAttachedStatus):
                raise ActionMiss("载体已经被破坏，主动解除落空。")
            cells = self.get_target_cells_for_payload(battle, actor, payload)
            attached.detach(battle)
            actor.add_status(AgencyDefenseStatus(), source=actor)
            for target in battle.effect_units_at_cells(cells):
                if target.player_id == actor.player_id:
                    continue
                ctx = battle.resolve_damage(DamageContext(source=actor, target=target, attack_power=actor.stat("attack"),
                    is_skill=True, action_name="代行契约解除", area_cell_hits=battle.unit_hit_count_for_cells(target, cells),
                    tags={"skill", "agency_contract"}))
                if damage_followup_effect_applies(ctx) and not is_mana_drain_immune(target):
                    actor.gain_mana(target.spend_mana(min(1.0, target.current_mana)))
            battle.log(f"{actor.name} 主动解除【代行契约】。")
            return
        target, stat, copied = self.validate_binding(battle, actor, payload)
        status = AgencyAttachedStatus(target.unit_id, stat, copied.code)
        actor.add_status(status, source=actor)
        if status not in actor.statuses:
            raise ActionMiss("契约附着未能生效。")
        self.wrapper(actor).timing = copied.timing
        status.sync_linked_state(battle)
        battle.log(f"{actor.name} 附着在 {target.name}，借用 {stat} 和【{copied.name}】。")

    def preview(self, battle: Battle, actor: HeroUnit) -> dict[str, Any]:
        if actor.has_status("代行契约附着"):
            return {"cells": positions_to_dict(self.ring(battle, actor)), "target_unit_ids": [], "requires_target": False,
                    "selection": {"mode": "agency_contract", "attached": True}}
        targets = self.targets(battle, actor)
        return {"cells": positions_to_dict([cell for target in targets for cell in battle.unit_cells(target)]),
                "target_unit_ids": [target.unit_id for target in targets], "requires_target": True,
                "selection": {"mode": "agency_contract", "stats": list(self.VALID_STATS), "targets": [
                    {"unit_id": target.unit_id, "name": target.name, "skills": [
                        {"code": skill.code, "name": skill.name} for skill in target.skills if self.wrapper(actor).copyable(skill)]}
                    for target in targets]}}


class PassiveNoRetryStatus(StatusEffect):
    def __init__(self) -> None:
        super().__init__("被动失败封锁", "本回合不能再次使用被动技能。", duration=1, tick_scope="any_turn_end")

    def blocks_skill_use(self, battle: Battle, actor: HeroUnit, skill: Skill) -> tuple[bool, str]:
        return (True, "本回合不能再次使用被动技能。") if skill.timing in {"passive", "reaction"} else (False, "")


class AgencyPassivePunishTrait(Trait):
    track_attack_reaction_prevention = True
    reaction_prevention_requires_hp = True

    def __init__(self) -> None:
        super().__init__("被动失败封锁", "敌方被动真实阻止普攻伤害后，封锁实际使用者至当前回合末。")

    def on_basic_attack_finished(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any], damage_contexts: list[DamageContext], missed: bool) -> None:
        if any(ctx.actual_damage > 0 for ctx in damage_contexts) or getattr(battle, "_measuring_attack_success", False):
            return
        for unit_id in set(payload.get("passive_attack_preventer_ids", [])):
            target = battle.units.get(unit_id)
            if target is not None and target.alive and target.player_id != actor.player_id and not target.has_status("被动失败封锁"):
                target.add_status(PassiveNoRetryStatus(), source=actor)
                battle.log(f"{target.name} 的被动使 {actor.name} 普攻无伤，本回合被动被封锁。")


