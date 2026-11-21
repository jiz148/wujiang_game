"""R380 Penitent: race declarations, a race rewrite, thorns, and delayed penance."""

from __future__ import annotations

from typing import Any

from wujiang.tactical.engine.core import (
    ActionError, Battle, DamageContext, HealContext, Position, QueuedAction,
    Skill, StatusEffect, TargetContext, Trait, Unit,
)
from wujiang.tactical.heroes.common import positions_to_dict
from wujiang.tactical.heroes.mechanics.piercing_control import piercing_nondamage_recipient


STAT_LABELS = {"attack": "攻", "defense": "守", "speed": "速", "attack_range": "范"}


def _present(unit: Unit | None) -> bool:
    return unit is not None and unit.alive and not unit.banished and unit.position is not None


def field_races(battle: Battle) -> list[str]:
    return sorted({unit.race for unit in battle.all_units() if _present(unit) and unit.race})


def selectable_units(battle: Battle, actor: Unit) -> list[Unit]:
    if actor.position is None:
        return []
    return [unit for unit in battle.all_units()
            if _present(unit) and battle.unit_can_be_selected(unit, actor=actor)[0]
            and any(origin.distance_to(cell) <= actor.targeting_range()
                    and battle.cells_are_straight_aligned(origin, cell)
                    for origin in battle.unit_cells(actor) for cell in battle.unit_cells(unit))]


def payload_target(battle: Battle, actor: Unit, payload: dict[str, Any]) -> Unit:
    target = battle.units.get(str(payload.get("target_unit_id") or ""))
    if target not in selectable_units(battle, actor):
        raise ActionError("请选择当前范内直线可选中的单位。")
    return target


class DeclarationStatus(StatusEffect):
    def __init__(self, stat_name: str, delta: int) -> None:
        label = STAT_LABELS[stat_name]
        super().__init__(f"神的宣言·{label}{delta:+d}", f"{label}{delta:+d}，最低1，持续一轮。",
                         duration=1, tick_scope="owner_turn_start")
        self.stat_name = stat_name
        self.delta = delta

    def modify_stat(self, stat_name: str, value: float) -> float:
        return max(1.0, value + self.delta) if stat_name == self.stat_name else value


class GodDeclarationSkill(Skill):
    ai_payload_value_owned = True
    ai_enemy_impact_requirement = False

    def __init__(self) -> None:
        super().__init__("god_declaration", "神的宣言",
                         "每本人轮一次；选在场种族、攻守速范及增减2，影响该族在场单位一轮。",
                         max_uses_per_turn=1, target_mode="self")

    def options(self, battle: Battle) -> list[dict[str, str]]:
        return [{"code": f"{race}|{stat}|{direction}",
                 "label": f"{race} · {label}{'+2' if direction == 'plus' else '-2'}"}
                for race in field_races(battle)
                for stat, label in STAT_LABELS.items()
                for direction in ("plus", "minus")]

    def choice(self, battle: Battle, payload: dict[str, Any]) -> tuple[str, str, int]:
        code = str(payload.get("option_code") or "")
        if code:
            parts = code.split("|")
            if len(parts) != 3:
                raise ActionError("神的宣言需要完整的种族、能力及增减选项。")
            race, stat, direction = parts
            delta = 2 if direction == "plus" else -2 if direction == "minus" else 0
        else:
            race, stat = str(payload.get("race") or ""), str(payload.get("stat_name") or "")
            delta = int(payload.get("delta") or 0)
        if race not in field_races(battle) or stat not in STAT_LABELS or delta not in {-2, 2}:
            raise ActionError("神的宣言选项不合法。")
        return race, stat, delta

    def preview(self, battle: Battle, actor: Unit) -> dict[str, Any]:
        return {"cells": [], "target_unit_ids": [], "secondary_cells": [], "requires_target": True,
                "selection": {"mode": "option", "requires_unit": False,
                              "prompt": "选择当前场上的种族、能力和增减幅度，再点击完成选择。",
                              "options": self.options(battle)}}

    def can_use(self, battle: Battle, actor: Unit,
                payload: dict[str, Any] | None = None) -> tuple[bool, str]:
        ok, reason = super().can_use(battle, actor, payload)
        if not ok:
            return ok, reason
        if payload and any(key in payload for key in ("option_code", "race", "stat_name", "delta")):
            try:
                self.choice(battle, payload)
            except (ActionError, ValueError) as exc:
                return False, str(exc)
        return True, ""

    def declared_targets(self, battle: Battle, payload: dict[str, Any]) -> list[Unit]:
        race, _, _ = self.choice(battle, payload)
        return [unit for unit in battle.all_units()
                if _present(unit) and unit.qualifies_as_race(race)]

    def get_target_units_for_payload(self, battle: Battle, actor: Unit,
                                     payload: dict[str, Any]) -> list[Unit]:
        return self.declared_targets(battle, payload)

    def get_target_cells_for_payload(self, battle: Battle, actor: Unit,
                                     payload: dict[str, Any]) -> list[Position]:
        return [cell for unit in self.declared_targets(battle, payload)
                for cell in battle.unit_cells(unit)]

    def execute(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> None:
        race, stat, delta = self.choice(battle, payload)
        affected = self.declared_targets(battle, payload)
        landed = 0
        for unit in affected:
            checked = battle.validate_target(actor, unit, action_name=self.name, is_skill=True,
                                             is_hostile=unit.player_id != actor.player_id)
            if checked.cancelled:
                continue
            recipient = checked.target
            recipient.add_status(DeclarationStatus(stat, delta), source=actor)
            landed += int(recipient.has_status(f"神的宣言·{STAT_LABELS[stat]}{delta:+d}"))
        battle.log_public_event(
            f"{actor.name} 宣告【{race}】的{STAT_LABELS[stat]}{delta:+d}一轮；当前命中 {landed} 个单位。",
            source=actor,
        )

    def ai_payload_candidates(self, battle: Battle, actor: Unit,
                              action: dict[str, Any]) -> list[dict[str, Any]]:
        return [{"type": "skill", "unit_id": actor.unit_id, "skill_code": self.code,
                 "option_code": entry["code"]} for entry in self.options(battle)]

    def ai_candidate_payloads(self, context: Any) -> list[dict[str, Any]]:
        return self.ai_payload_candidates(context.battle, context.actor, context.action)

    def ai_payload_value(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> float:
        try:
            race, stat, delta = self.choice(battle, payload)
        except (ActionError, ValueError):
            return -1000.0
        weight = {"attack": 23.0, "defense": 20.0, "speed": 16.0, "attack_range": 14.0}[stat]
        value = 0.0
        for unit in battle.all_units():
            if not _present(unit) or not unit.qualifies_as_race(race):
                continue
            if (unit.player_id != actor.player_id and
                    (unit.magic_immunity or unit.total_shields() > 0 or
                     unit.dodge_charges > 0 or unit.cannot_be_targeted)):
                continue
            change = max(1.0, unit.stat(stat) + delta) - unit.stat(stat)
            value += change * weight * (1 if unit.player_id == actor.player_id else -1)
        return value - 12.0

    def ai_priority_score(self, context: Any) -> float:
        return self.ai_payload_value(context.battle, context.actor, context.payload)


class MoonDarkSideSkill(Skill):
    ai_payload_value_owned = True
    ai_enemy_impact_requirement = False

    def __init__(self) -> None:
        super().__init__("moon_dark_side", "月之暗面",
                         "本场一次；破魔；命中单位后改成施术者所选的当前在场种族。",
                         max_uses_per_battle=1, target_mode="unit")

    def choices(self, battle: Battle, actor: Unit) -> list[str]:
        return field_races(battle)

    def selection(self, battle: Battle, actor: Unit,
                  payload: dict[str, Any]) -> tuple[Unit, str]:
        target = payload_target(battle, actor, payload)
        race = str(payload.get("option_code") or payload.get("race") or "")
        if race not in self.choices(battle, actor) or race == target.race:
            raise ActionError("月之暗面需要选与受体当前不同的在场种族。")
        return target, race

    def preview(self, battle: Battle, actor: Unit) -> dict[str, Any]:
        races = self.choices(battle, actor)
        targets = [unit for unit in selectable_units(battle, actor)
                   if any(race != unit.race for race in races)]
        return {"cells": positions_to_dict([cell for unit in targets for cell in battle.unit_cells(unit)]),
                "target_unit_ids": [unit.unit_id for unit in targets],
                "secondary_cells": [], "requires_target": True,
                "selection": {"mode": "option", "requires_unit": True,
                              "prompt": "先选择要改成的在场种族，再点击当前范内目标，最后完成选择。",
                              "options": [{"code": race, "label": race} for race in races],
                              "options_by_target": {unit.unit_id: [race for race in races if race != unit.race]
                                                    for unit in targets}}}

    def can_use(self, battle: Battle, actor: Unit,
                payload: dict[str, Any] | None = None) -> tuple[bool, str]:
        ok, reason = super().can_use(battle, actor, payload)
        if not ok:
            return ok, reason
        if not self.preview(battle, actor)["target_unit_ids"]:
            return False, "场上没有可改为另一在场种族的目标。"
        if payload and "target_unit_id" in payload:
            try:
                self.selection(battle, actor, payload)
            except ActionError as exc:
                return False, str(exc)
        return True, ""

    def ignores_shield_for_payload(self, battle: Battle, actor: Unit,
                                   payload: dict[str, Any]) -> bool:
        return True

    def queued_payload_metadata(self, battle: Battle, actor: Unit,
                                payload: dict[str, Any]) -> dict[str, Any]:
        return {**super().queued_payload_metadata(battle, actor, payload), "ignore_shield": True}

    def get_target_cells_for_payload(self, battle: Battle, actor: Unit,
                                     payload: dict[str, Any]) -> list[Position]:
        return battle.unit_cells(self.selection(battle, actor, payload)[0])

    def get_target_units_for_payload(self, battle: Battle, actor: Unit,
                                     payload: dict[str, Any]) -> list[Unit]:
        return [self.selection(battle, actor, payload)[0]]

    def execute(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> None:
        target, race = self.selection(battle, actor, payload)
        recipient = piercing_nondamage_recipient(
            battle, actor, target, action_name=self.name, code=self.code)
        if recipient is None:
            return
        before = recipient.race
        recipient.change_race_permanently(race)
        battle.log_public_event(f"{actor.name} 的月之暗面将 {recipient.name} 的种族由{before}改为{race}。",
                                source=actor, target=recipient)

    def ai_payload_candidates(self, battle: Battle, actor: Unit,
                              action: dict[str, Any]) -> list[dict[str, Any]]:
        return [{"type": "skill", "unit_id": actor.unit_id, "skill_code": self.code,
                 "target_unit_id": unit.unit_id, "option_code": race,
                 "x": unit.position.x, "y": unit.position.y}
                for unit in selectable_units(battle, actor)
                for race in self.choices(battle, actor) if race != unit.race]

    def ai_candidate_payloads(self, context: Any) -> list[dict[str, Any]]:
        return self.ai_payload_candidates(context.battle, context.actor, context.action)

    def ai_payload_value(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> float:
        try:
            target, race = self.selection(battle, actor, payload)
        except ActionError:
            return -1000.0
        if (target.player_id != actor.player_id and
                (target.magic_immunity or target.dodge_charges > 0)):
            return -1000.0
        declaration = actor.skill_map().get("god_declaration")
        if declaration is not None and not target.qualifies_as_race(race):
            options = [{"race": race, "stat_name": stat, "delta": delta}
                       for stat in STAT_LABELS for delta in (-2, 2)]
            baseline = max(declaration.ai_payload_value(battle, actor, option) for option in options)
            projected = max(
                declaration.ai_payload_value(battle, actor, option)
                + (max(1.0, target.stat(option["stat_name"]) + option["delta"])
                   - target.stat(option["stat_name"]))
                * {"attack": 23.0, "defense": 20.0, "speed": 16.0, "attack_range": 14.0}[option["stat_name"]]
                * (1 if target.player_id == actor.player_id else -1)
                for option in options
            )
            return min(38.0, (projected - baseline) * 0.35) - 4.0
        return -20.0

    def ai_priority_score(self, context: Any) -> float:
        return self.ai_payload_value(context.battle, context.actor, context.payload)


class ThornCurseSkill(Skill):
    ai_payload_value_owned = True
    ai_enemy_impact_requirement = True

    def __init__(self) -> None:
        super().__init__("thorn_curse", "荆棘诅咒",
                         "每本人轮一次；周围八格伤3半破魔；每击中一格单位，血+1/4、魔+1。",
                         max_uses_per_turn=1, target_mode="self")

    def cells(self, battle: Battle, actor: Unit) -> list[Position]:
        return sorted({origin.offset(dx, dy)
                       for origin in battle.unit_cells(actor)
                       for dx in (-1, 0, 1) for dy in (-1, 0, 1)
                       if (dx or dy) and battle.in_bounds(origin.offset(dx, dy))},
                      key=lambda cell: (cell.y, cell.x))

    def preview(self, battle: Battle, actor: Unit) -> dict[str, Any]:
        cells = self.cells(battle, actor)
        return {"cells": positions_to_dict(cells),
                "target_unit_ids": [unit.unit_id for unit in battle.effect_units_at_cells(cells)],
                "secondary_cells": [], "requires_target": False}

    def execute(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> None:
        cells = self.cells(battle, actor)
        battle.queue_skill_effect_action(
            actor=actor, display_name="荆棘诅咒", effect_code="thorn_curse",
            target_units=battle.effect_units_at_cells(cells), target_cells=cells,
            payload={"cells": [cell.to_dict() for cell in cells], "half_ignore_shield": True},
            effect_resolver=self.resolve_thorns,
        )

    @staticmethod
    def resolve_thorns(battle: Battle, actor: Unit, queued: QueuedAction) -> None:
        cells = battle.payload_positions(queued.payload, "cells")
        hit_cells: set[Position] = set()
        for unit in battle.effect_units_at_cells(cells):
            if unit is actor or not _present(unit):
                continue
            covered_cells = {cell for cell in battle.unit_cells(unit) if cell in cells}
            hit = battle.resolve_damage(DamageContext(
                source=actor, target=unit, attack_power=3, is_skill=True,
                action_name="荆棘诅咒", half_ignore_shield=True,
                area_cell_hits=battle.unit_hit_count_for_cells(unit, cells),
                tags={"skill", "area", "thorn_curse"},
            ))
            if not hit.cancelled:
                hit_cells.update(covered_cells)
        for _ in hit_cells:
            if not actor.alive:
                break
            battle.heal(HealContext(source=actor, target=actor, amount=0.25,
                                    action_name="荆棘诅咒回馈"))
            actor.gain_mana(1)
        if hit_cells:
            battle.log_public_event(f"{actor.name} 的荆棘诅咒命中 {len(hit_cells)} 格，逐格回复生命与魔。",
                                    source=actor)

    def ai_payload_candidates(self, battle: Battle, actor: Unit,
                              action: dict[str, Any]) -> list[dict[str, Any]]:
        return [{"type": "skill", "unit_id": actor.unit_id, "skill_code": self.code}]

    def ai_candidate_payloads(self, context: Any) -> list[dict[str, Any]]:
        return self.ai_payload_candidates(context.battle, context.actor, context.action)

    def ai_effective_enemy_impact(self, battle: Battle, actor: Unit,
                                  payload: dict[str, Any], targets: list[Unit] | None = None,
                                  shared: Any = None, action: dict[str, Any] | None = None) -> bool:
        return any(unit.player_id != actor.player_id for unit in battle.effect_units_at_cells(self.cells(battle, actor)))

    def ai_payload_value(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> float:
        cells = self.cells(battle, actor)
        value = 0.0
        for unit in battle.effect_units_at_cells(cells):
            if unit is actor or not _present(unit):
                continue
            hits = battle.unit_hit_count_for_cells(unit, cells)
            impact = max(0.0, 3 - unit.stat("defense") + hits - 1)
            value += (40.0 + 25.0 * impact) * (1 if unit.player_id != actor.player_id else -1.5)
            value += 10.0 * hits
        return value - 15.0

    def ai_priority_score(self, context: Any) -> float:
        return self.ai_payload_value(context.battle, context.actor, context.payload)


class ThornGrantStatus(StatusEffect):
    def __init__(self) -> None:
        super().__init__("枷锁荆棘", "速-2，最低1；获得每本人轮一次的荆棘诅咒。")
        self.thorns = ThornCurseSkill()

    def bind(self, owner: Unit) -> ThornGrantStatus:
        super().bind(owner)
        self.thorns.bind(owner)
        return self

    def provided_components(self) -> tuple[Skill, ...]:
        return (self.thorns,)

    def modify_stat(self, stat_name: str, value: float) -> float:
        return max(1.0, value - 2.0) if stat_name == "speed" else value


class PenitentShackleSkill(Skill):
    ai_payload_value_owned = True
    ai_enemy_impact_requirement = False

    def __init__(self) -> None:
        super().__init__("penitent_shackle", "枷锁",
                         "每本人轮一次；命中单位永久速-2最低1，并获得荆棘诅咒。",
                         max_uses_per_turn=1, target_mode="unit")

    def preview(self, battle: Battle, actor: Unit) -> dict[str, Any]:
        targets = selectable_units(battle, actor)
        return {"cells": positions_to_dict([cell for unit in targets for cell in battle.unit_cells(unit)]),
                "target_unit_ids": [unit.unit_id for unit in targets],
                "secondary_cells": [], "requires_target": True}

    def get_target_cells_for_payload(self, battle: Battle, actor: Unit,
                                     payload: dict[str, Any]) -> list[Position]:
        return battle.unit_cells(payload_target(battle, actor, payload))

    def get_target_units_for_payload(self, battle: Battle, actor: Unit,
                                     payload: dict[str, Any]) -> list[Unit]:
        return [payload_target(battle, actor, payload)]

    def execute(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> None:
        target = payload_target(battle, actor, payload)
        checked = battle.validate_target(actor, target, action_name=self.name, is_skill=True,
                                         is_hostile=target.player_id != actor.player_id)
        if checked.cancelled:
            battle.log_public_event(checked.reason or "枷锁未命中。", source=actor, target=target)
            return
        recipient = checked.target
        if not recipient.has_status("枷锁荆棘"):
            recipient.add_status(ThornGrantStatus(), source=actor)
        battle.log_public_event(f"{actor.name} 的枷锁命中 {recipient.name}，速-2并授予荆棘诅咒。",
                                source=actor, target=recipient)

    def ai_payload_candidates(self, battle: Battle, actor: Unit,
                              action: dict[str, Any]) -> list[dict[str, Any]]:
        return [{"type": "skill", "unit_id": actor.unit_id, "skill_code": self.code,
                 "target_unit_id": unit.unit_id, "x": unit.position.x, "y": unit.position.y}
                for unit in selectable_units(battle, actor)]

    def ai_candidate_payloads(self, context: Any) -> list[dict[str, Any]]:
        return self.ai_payload_candidates(context.battle, context.actor, context.action)

    def ai_payload_value(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> float:
        try:
            target = payload_target(battle, actor, payload)
        except ActionError:
            return -1000.0
        if (target.has_status("枷锁荆棘") or
                (target.player_id != actor.player_id and
                 (target.magic_immunity or target.total_shields() > 0 or
                  target.dodge_charges > 0))):
            return -1000.0
        neighbors = [unit for unit in battle.effect_units_at_cells(ThornCurseSkill().cells(battle, target))
                     if unit is not target and _present(unit)]
        thorn_net = sum((1 if unit.player_id != target.player_id else -1.5) * 55
                        for unit in neighbors)
        speed_cost = min(2.0, max(0.0, target.stat("speed") - 1)) * 16.0
        return (thorn_net - speed_cost if target.player_id == actor.player_id
                else speed_cost - thorn_net) - 8.0

    def ai_priority_score(self, context: Any) -> float:
        return self.ai_payload_value(context.battle, context.actor, context.payload)


class PenanceDueStatus(StatusEffect):
    def __init__(self, source_player_id: int) -> None:
        super().__init__("忏悔待决", "下次本人轮不对忏悔者一方造成实际生命伤害，则轮末血-1/2。")
        self.penitent_player_id = source_player_id
        self.armed = False
        self.dealt_damage = False

    def on_owner_turn_start(self, battle: Battle) -> None:
        self.armed = True
        self.dealt_damage = False

    def on_battle_damage(self, battle: Battle, ctx: DamageContext) -> None:
        if (self.armed and ctx.source is self.owner and ctx.target.player_id == self.penitent_player_id
                and ctx.actual_damage > 0 and battle.unit_belongs_to_current_turn(self.owner)):
            self.dealt_damage = True

    def on_owner_turn_end(self, battle: Battle) -> None:
        owner = self.owner
        if owner is None or not self.armed:
            return
        if not self.dealt_damage and owner.alive:
            before = owner.current_hp
            owner.take_damage_fraction(0.5)
            battle.log_public_event(f"{owner.name} 未在本轮伤害忏悔者一方，忏悔减血 {before - owner.current_hp:g}。",
                                    target=owner)
        owner.remove_status(self, battle)


class PenanceAttackTrait(Trait):
    def __init__(self) -> None:
        super().__init__("忏悔普攻", "普攻破魔；命中单位下次本人轮不伤害己方则血-1/2。")

    def basic_attack_payload_metadata(self, battle: Battle, actor: Unit,
                                      payload: dict[str, Any] | None = None) -> dict[str, Any]:
        return {"ignore_shield": True} if actor is self.owner else {}

    def on_targeted(self, battle: Battle, ctx: TargetContext) -> None:
        if ctx.actor is self.owner and not ctx.is_skill and "attack" in ctx.tags:
            ctx.ignore_shield = True

    def on_before_damage(self, battle: Battle, ctx: DamageContext) -> None:
        if ctx.source is self.owner and not ctx.is_skill and "attack" in ctx.tags:
            ctx.ignore_shield = True

    def on_after_damage(self, battle: Battle, ctx: DamageContext) -> None:
        if (ctx.source is not self.owner or ctx.is_skill or "attack" not in ctx.tags
                or ctx.cancelled or not _present(ctx.target)):
            return
        ctx.target.add_status(PenanceDueStatus(self.owner.player_id), source=self.owner)
        battle.log_public_event(f"{ctx.target.name} 受到忏悔普攻附效，下次本人轮须伤害忏悔者一方。",
                                source=self.owner, target=ctx.target)
