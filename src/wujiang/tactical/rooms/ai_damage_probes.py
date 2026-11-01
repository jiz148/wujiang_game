"""Rollback-safe damage and effect probes used by tactical AI."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Optional

from wujiang.tactical.engine.core import Battle, DamageContext, Position, Unit
from wujiang.tactical.prediction import expected_numeric
from wujiang.tactical.rooms.ai_probe import ai_probe_rollback


@dataclass(slots=True)
class DamageImpact:
    damage: float
    changed_target: bool


class DamageProbeService:
    """Stateless, source-reviewed probes with one rollback boundary."""

    @staticmethod
    @expected_numeric
    def estimate_attack_damage(
        battle: Battle,
        actor: Unit,
        target: Unit,
        payload: dict[str, Any],
        *,
        attack_power: float,
        area_cell_hits: int = 1,
    ) -> float:
        return DamageProbeService.probe_attack_damage_impact(
            battle,
            actor,
            target,
            payload,
            attack_power=attack_power,
            area_cell_hits=area_cell_hits,
        ).damage

    @staticmethod
    @expected_numeric
    def estimate_skill_damage(
        battle: Battle,
        actor: Unit,
        skill: Any,
        payload: dict[str, Any],
        target: Unit,
        attack_power: float,
        *,
        cells: list[Position],
        ignore_shield: bool,
        half_ignore_shield: bool,
    ) -> float:
        return DamageProbeService.probe_skill_damage_impact(
            battle,
            actor,
            skill,
            payload,
            target,
            attack_power,
            cells=cells,
            ignore_shield=ignore_shield,
            half_ignore_shield=half_ignore_shield,
        ).damage

    @staticmethod
    def probe_attack_damage_impact(
        battle: Battle,
        actor: Unit,
        target: Unit,
        payload: dict[str, Any],
        *,
        attack_power: float,
        area_cell_hits: int = 1,
    ) -> DamageImpact:
        resolved_payload = battle.resolved_basic_attack_payload(actor, payload)
        attack_tags = {"attack"}
        attack_tags.update(set(resolved_payload.get("attack_tags", [])))
        with ai_probe_rollback(battle):
            probe_actor = battle.get_unit(actor.unit_id)
            probe_target = battle.get_unit(target.unit_id)
            before = DamageProbeService.unit_impact_signature(probe_target)
            target_ctx = battle.validate_target(
                probe_actor,
                probe_target,
                action_name=str(resolved_payload.get("attack_name") or "普攻"),
                is_skill=False,
                is_hostile=True,
                resolve_defenses=False,
                tags=set(attack_tags),
            )
            if target_ctx.cancelled:
                return DamageImpact(0.0, DamageProbeService.unit_impact_signature(probe_target) != before)
            damage_ctx = DamageContext(
                source=probe_actor,
                target=probe_target,
                attack_power=attack_power,
                is_skill=False,
                action_name=str(resolved_payload.get("attack_name") or "普攻"),
                ignore_shield=bool(target_ctx.ignore_shield or resolved_payload.get("ignore_shield")),
                half_ignore_shield=bool(target_ctx.half_ignore_shield or resolved_payload.get("half_ignore_shield")),
                ignore_magic_immunity=target_ctx.ignore_magic_immunity,
                cannot_evade=target_ctx.cannot_evade,
                tags=set(target_ctx.tags),
                area_cell_hits=max(1, int(area_cell_hits)),
            )
            battle.resolve_damage(damage_ctx)
            from wujiang.tactical.direct_expectation import damage_result
            return DamageImpact(damage_result(damage_ctx, damage_ctx.actual_damage), DamageProbeService.unit_impact_signature(probe_target) != before)

    @staticmethod
    def probe_skill_damage_impact(
        battle: Battle,
        actor: Unit,
        skill: Any,
        payload: dict[str, Any],
        target: Unit,
        attack_power: float,
        *,
        cells: list[Position],
        ignore_shield: bool,
        half_ignore_shield: bool,
    ) -> DamageImpact:
        hit_count = 1 if skill.code == "deadly_bow" else max(1, battle.unit_hit_count_for_cells(target, cells) if cells else 1)
        adjusted_attack_power = max(0.0, float(attack_power) - max(0, hit_count - 1))
        resolves_as_attack = str(skill.code) == "whirlwind_attack"
        if str(skill.code) in {"complete_burn", "blizzard"}:
            # Only the attached control pierces; the damage still stops at shields.
            ignore_shield = False
        target_tags = {"attack", "whirlwind"} if resolves_as_attack else {"skill", str(skill.code)}
        with ai_probe_rollback(battle):
            probe_actor = battle.get_unit(actor.unit_id)
            probe_target = battle.get_unit(target.unit_id)
            before = DamageProbeService.unit_impact_signature(probe_target)
            target_ctx = battle.validate_target(
                probe_actor,
                probe_target,
                action_name=str(skill.name),
                is_skill=not resolves_as_attack,
                is_hostile=True,
                ignore_shield=ignore_shield,
                half_ignore_shield=half_ignore_shield,
                ignore_magic_immunity=skill.ignores_magic_immunity_for_payload(battle, probe_actor, payload),
                cannot_evade=skill.cannot_evade_for_payload(battle, probe_actor, payload),
                ignore_targeting_restrictions=str(skill.code) in {"great_funeral", "judgment_fire", "wind_sand", "crazy_sand", "dragon_breath", "remote_dragon_breath", "hell_orb", "hell_orb_free", "hell_dragon_fire", "rending", "complete_burn", "blizzard", "machine_gun", "missile", "laser", "magnetic_wave", "frey_quick_flash", "frey_god_stab", "frey_lion_spear", "demon_blade", "nuclear_mutation", "gravity_field"},
                resolve_defenses=False,
                damage_target=True,
                tags=set(target_tags),
            )
            if target_ctx.cancelled:
                return DamageImpact(0.0, DamageProbeService.unit_impact_signature(probe_target) != before)
            damage_ctx = DamageContext(
                source=probe_actor,
                target=probe_target,
                attack_power=adjusted_attack_power,
                is_skill=not resolves_as_attack,
                action_name=str(skill.name),
                ignore_shield=bool(target_ctx.ignore_shield or ignore_shield),
                half_ignore_shield=bool(target_ctx.half_ignore_shield or half_ignore_shield),
                ignore_magic_immunity=target_ctx.ignore_magic_immunity,
                cannot_evade=target_ctx.cannot_evade,
                tags=set(target_ctx.tags),
                area_cell_hits=hit_count,
            )
            battle.resolve_damage(damage_ctx)
            if str(skill.code) in {"complete_burn", "blizzard"}:
                skill.apply_followup_effect(battle, probe_target, damage_ctx)
            from wujiang.tactical.direct_expectation import damage_result
            return DamageImpact(damage_result(damage_ctx, damage_ctx.damage), DamageProbeService.unit_impact_signature(probe_target) != before)

    @staticmethod
    def probe_skill_raw_damage_impact(
        battle: Battle,
        actor: Unit,
        skill: Any,
        target: Unit,
        *,
        raw_damage: float,
        ignore_shield: bool,
    ) -> DamageImpact:
        with ai_probe_rollback(battle):
            probe_actor = battle.get_unit(actor.unit_id)
            probe_target = battle.get_unit(target.unit_id)
            before = DamageProbeService.unit_impact_signature(probe_target)
            target_ctx = battle.validate_target(
                probe_actor,
                probe_target,
                action_name=str(skill.name),
                is_skill=True,
                is_hostile=True,
                ignore_shield=ignore_shield,
                resolve_defenses=False,
                damage_target=True,
                tags={"skill", str(skill.code)},
            )
            if target_ctx.cancelled:
                return DamageImpact(0.0, DamageProbeService.unit_impact_signature(probe_target) != before)
            damage_ctx = DamageContext(
                source=probe_actor,
                target=probe_target,
                attack_power=0,
                raw_damage=raw_damage,
                is_skill=True,
                action_name=str(skill.name),
                ignore_shield=bool(target_ctx.ignore_shield or ignore_shield),
                ignore_magic_immunity=target_ctx.ignore_magic_immunity,
                cannot_evade=target_ctx.cannot_evade,
                tags=set(target_ctx.tags),
            )
            battle.resolve_damage(damage_ctx)
            from wujiang.tactical.direct_expectation import damage_result
            return DamageImpact(damage_result(damage_ctx, damage_ctx.damage), DamageProbeService.unit_impact_signature(probe_target) != before)

    @staticmethod
    @expected_numeric(independent=True, boolean=True)
    def probe_target_effect_impact(
        battle: Battle,
        actor: Unit,
        target: Unit,
        *,
        action_name: str,
        is_skill: bool,
        ignore_shield: bool = False,
        half_ignore_shield: bool = False,
        extra_effect_applies: Optional[Callable[[Unit], bool]] = None,
    ) -> bool:
        with ai_probe_rollback(battle):
            probe_actor = battle.get_unit(actor.unit_id)
            probe_target = battle.get_unit(target.unit_id)
            before = DamageProbeService.unit_impact_signature(probe_target)
            ctx = battle.validate_target(
                probe_actor,
                probe_target,
                action_name=action_name,
                is_skill=is_skill,
                is_hostile=True,
                ignore_shield=ignore_shield,
                half_ignore_shield=half_ignore_shield,
                resolve_defenses=True,
                tags={"skill"} if is_skill else {"attack"},
            )
            changed = DamageProbeService.unit_impact_signature(probe_target) != before
            if changed:
                return True
            if ctx.cancelled:
                return False
            return extra_effect_applies(probe_target) if extra_effect_applies is not None else True

    @staticmethod
    def unit_impact_signature(unit: Unit) -> tuple[Any, ...]:
        return (
            unit.alive,
            unit.turn_ready,
            unit.position,
            round(float(unit.current_hp), 6),
            round(float(unit.current_mana), 6),
            round(float(unit.mana_points), 6),
            unit.shields,
            unit.temporary_shields,
            unit.dodge_charges,
            unit.magic_immunity,
            unit.cannot_be_targeted,
            unit.cannot_move,
            unit.cannot_normal_move,
            unit.cannot_heal,
            unit.cannot_attack,
            unit.cannot_use_skills,
            tuple(DamageProbeService.status_impact_signature(status) for status in unit.statuses),
        )

    @staticmethod
    def status_impact_signature(status: Any) -> tuple[Any, ...]:
        data = []
        for key, value in sorted(getattr(status, "__dict__", {}).items(), key=lambda item: str(item[0])):
            if key == "owner":
                continue
            if isinstance(value, (str, int, float, bool, type(None))):
                data.append((key, value))
            elif isinstance(value, set):
                data.append((key, tuple(sorted(value))))
            elif isinstance(value, list):
                data.append((key, tuple(repr(item) for item in value)))
            elif isinstance(value, dict):
                data.append((key, tuple(sorted((str(k), repr(v)) for k, v in value.items()))))
            else:
                data.append((key, repr(value)))
        return (type(status).__name__, tuple(data))


# Keep function-shaped entry points for callers and reviewed code identities.
estimate_attack_damage = DamageProbeService.estimate_attack_damage
estimate_skill_damage = DamageProbeService.estimate_skill_damage
probe_attack_damage_impact = DamageProbeService.probe_attack_damage_impact
probe_skill_damage_impact = DamageProbeService.probe_skill_damage_impact
probe_skill_raw_damage_impact = DamageProbeService.probe_skill_raw_damage_impact
probe_target_effect_impact = DamageProbeService.probe_target_effect_impact
unit_impact_signature = DamageProbeService.unit_impact_signature
status_impact_signature = DamageProbeService.status_impact_signature
