"""R160 Dead Soul Echo: arc strike, permanent stat cost and enemy-end rewind."""

from __future__ import annotations

from typing import Any

from wujiang.tactical.engine.core import ActionError, Battle, DamageContext, Position, Skill, StatusEffect, Unit
from wujiang.tactical.heroes.common import positions_to_dict
from wujiang.tactical.heroes.mechanics.masamune import ARC_DIRECTION_CHOICES, arc_direction_cells


_STATS = {"attack": "攻", "defense": "守", "speed": "速", "attack_range": "范"}
_STAT_COST = {"attack": 36.0, "defense": 38.0, "speed": 27.0, "attack_range": 31.0}


class SpiritAxeSkill(Skill):
    ai_payload_value_owned = True
    target_cells_are_geometry_only = True

    def __init__(self) -> None:
        super().__init__("spirit_axe", "精神斧", "每本人轮一次；选弧形方向，立即对该方向敌方单位造成半破魔伤害。",
                         max_uses_per_turn=1, target_mode="self")

    def cells(self, battle: Battle, actor: Unit, code: str) -> list[Position]:
        if actor.position is None:
            return []
        return [cell for cell in arc_direction_cells(actor.position, code) if battle.in_bounds(cell)]

    def targets(self, battle: Battle, actor: Unit, code: str) -> list[Unit]:
        return [unit for unit in battle.effect_units_at_cells(self.cells(battle, actor, code), ignore=actor)
                if unit.player_id != actor.player_id]

    def options(self, battle: Battle, actor: Unit) -> list[dict[str, str]]:
        return [{"code": code, "label": f"朝{label}弧形攻击"}
                for code, _, label in ARC_DIRECTION_CHOICES if self.targets(battle, actor, code)]

    def _chosen_code(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> str:
        code = str(payload.get("option_code") or "")
        if code not in {entry["code"] for entry in self.options(battle, actor)}:
            raise ActionError("精神斧需要选择能命中敌方单位的弧形方向。")
        return code

    def can_use(self, battle: Battle, actor: Unit,
                payload: dict[str, Any] | None = None) -> tuple[bool, str]:
        ok, reason = super().can_use(battle, actor, payload)
        if not ok:
            return ok, reason
        if not self.options(battle, actor):
            return False, "当前没有可命中的弧形方向。"
        if payload and "option_code" in payload:
            try:
                self._chosen_code(battle, actor, payload)
            except ActionError as exc:
                return False, str(exc)
        return True, ""

    def preview(self, battle: Battle, actor: Unit) -> dict[str, Any]:
        options = self.options(battle, actor)
        cells = list(dict.fromkeys(cell for entry in options for cell in self.cells(battle, actor, entry["code"])))
        targets = list(dict.fromkeys(unit.unit_id for entry in options
                                     for unit in self.targets(battle, actor, entry["code"])))
        return {"cells": positions_to_dict(cells), "target_unit_ids": targets,
                "secondary_cells": [], "requires_target": True,
                "selection": {"mode": "option", "requires_unit": False,
                              "prompt": "选择精神斧的弧形攻击方向。", "options": options}}

    def get_target_cells_for_payload(self, battle: Battle, actor: Unit,
                                     payload: dict[str, Any]) -> list[Position]:
        frozen = battle.payload_positions(payload, "declared_spirit_axe_cells")
        return frozen or self.cells(battle, actor, self._chosen_code(battle, actor, payload))

    def get_target_units_for_payload(self, battle: Battle, actor: Unit,
                                     payload: dict[str, Any]) -> list[Unit]:
        cells = self.get_target_cells_for_payload(battle, actor, payload)
        return [unit for unit in battle.effect_units_at_cells(cells, ignore=actor)
                if unit.player_id != actor.player_id]

    def queued_payload_metadata(self, battle: Battle, actor: Unit,
                                payload: dict[str, Any]) -> dict[str, Any]:
        return {"declared_spirit_axe_cells": positions_to_dict(
            self.cells(battle, actor, self._chosen_code(battle, actor, payload)))}

    def half_ignores_shield_for_payload(self, battle: Battle, actor: Unit,
                                        payload: dict[str, Any]) -> bool:
        return True

    def ai_payload_candidates(self, battle: Battle, actor: Unit,
                              action: dict[str, Any]) -> list[dict[str, Any]]:
        return [{"type": "skill", "unit_id": actor.unit_id, "skill_code": self.code,
                 "option_code": entry["code"]} for entry in self.options(battle, actor)]

    def ai_candidate_payloads(self, context: Any) -> list[dict[str, Any]]:
        return self.ai_payload_candidates(context.battle, context.actor, context.action)

    def ai_effective_enemy_impact(self, battle: Battle, actor: Unit,
                                  payload: dict[str, Any], targets: list[Unit] | None = None,
                                  shared: Any = None, action: dict[str, Any] | None = None) -> bool:
        return bool(self.get_target_units_for_payload(battle, actor, payload))

    def ai_payload_value(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> float:
        from wujiang.tactical.rooms.ai_probe import ai_probe_rollback

        try:
            targets = self.get_target_units_for_payload(battle, actor, payload)
            cells = self.get_target_cells_for_payload(battle, actor, payload)
        except ActionError:
            return -1000.0
        if not targets:
            return -1000.0
        score = 0.0
        for target in targets:
            with ai_probe_rollback(battle):
                source, recipient = battle.get_unit(actor.unit_id), battle.get_unit(target.unit_id)
                ctx = battle.resolve_damage(DamageContext(
                    source=source, target=recipient, attack_power=source.stat("attack"),
                    is_skill=True, action_name=self.name, half_ignore_shield=True,
                    area_cell_hits=battle.unit_hit_count_for_cells(recipient, cells),
                    tags={"skill", self.code}))
                score += ctx.actual_damage * 100.0 + (16.0 if ctx.shield_consumed else 0.0)
        return score - 14.0 if score > 0 else -1000.0

    def ai_priority_score(self, context: Any) -> float:
        return self.ai_payload_value(context.battle, context.actor, context.payload)

    def execute(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> None:
        cells = self.get_target_cells_for_payload(battle, actor, payload)
        for target in self.get_target_units_for_payload(battle, actor, payload):
            battle.resolve_damage(DamageContext(
                source=actor, target=target, attack_power=actor.stat("attack"),
                is_skill=True, action_name=self.name, half_ignore_shield=True,
                area_cell_hits=battle.unit_hit_count_for_cells(target, cells),
                tags={"skill", self.code}))


class SacrificedAbility(StatusEffect):
    def __init__(self, stat_name: str) -> None:
        super().__init__("禁忌牺牲", f"永久{_STATS[stat_name]}-1；最低1。")
        self.stat_name = stat_name

    def modify_stat(self, stat_name: str, value: float) -> float:
        return max(1.0, value - 1) if stat_name == self.stat_name else value


class RewoundAbilities(StatusEffect):
    def __init__(self) -> None:
        super().__init__("饿魂回溯", "此前攻守速范变化归源；之后获得的变化照常生效。")

    def modify_stat(self, stat_name: str, value: float) -> float:
        if self.owner is not None and stat_name in _STATS:
            return float(getattr(self.owner.base_stats, stat_name))
        return value


class ForbiddenMagusSinSkill(Skill):
    ai_payload_value_owned = True
    ai_enemy_impact_requirement = False

    def __init__(self) -> None:
        super().__init__("forbidden_magus_sin", "禁忌。魔法使之罪",
                         "牺牲攻守速范之一永久-1（最低1），重置本轮普攻或普通移动次数。",
                         target_mode="self")

    def options(self, actor: Unit) -> list[dict[str, str]]:
        resets = []
        if actor.attacks_used > 0:
            resets.append(("attack", "普攻"))
        if actor.move_used or actor.normal_move_actions_used or actor.normal_move_steps_used:
            resets.append(("move", "普通移动"))
        return [{"code": f"{stat}|{reset}", "label": f"{_STATS[stat]}-1，重置{label}"}
                for stat in _STATS if actor.stat(stat) > 1
                for reset, label in resets]

    def can_use(self, battle: Battle, actor: Unit,
                payload: dict[str, Any] | None = None) -> tuple[bool, str]:
        ok, reason = super().can_use(battle, actor, payload)
        if not ok:
            return ok, reason
        options = {entry["code"] for entry in self.options(actor)}
        if not options:
            return False, "须先消耗一次普攻或普通移动，且有可牺牲的非魔能力。"
        if payload and "option_code" in payload and payload["option_code"] not in options:
            return False, "请选择当前合法的能力牺牲和行动重置。"
        return True, ""

    def preview(self, battle: Battle, actor: Unit) -> dict[str, Any]:
        return {"cells": [], "target_unit_ids": [], "secondary_cells": [], "requires_target": True,
                "selection": {"mode": "option", "requires_unit": False,
                              "prompt": "先选择要牺牲的能力和要重置的行动。", "options": self.options(actor)}}

    def ai_payload_candidates(self, battle: Battle, actor: Unit,
                              action: dict[str, Any]) -> list[dict[str, Any]]:
        return [{"type": "skill", "unit_id": actor.unit_id, "skill_code": self.code,
                 "option_code": entry["code"]} for entry in self.options(actor)]

    def ai_candidate_payloads(self, context: Any) -> list[dict[str, Any]]:
        return self.ai_payload_candidates(context.battle, context.actor, context.action)

    def ai_payload_value(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> float:
        code = str(payload.get("option_code") or "")
        if code not in {entry["code"] for entry in self.options(actor)}:
            return -1000.0
        stat, reset = code.split("|", 1)
        if reset == "attack":
            benefit = 75.0 if any(battle.attack_target_allowed(
                actor, unit, payload={"target_unit_id": unit.unit_id})[0]
                for unit in battle.enemy_units(actor.player_id)) else 0.0
        else:
            enemies = [unit for unit in battle.enemy_units(actor.player_id) if unit.position is not None]
            if actor.position is None or not enemies:
                benefit = 0.0
            else:
                before = min(battle.distance_between_units(actor, unit) for unit in enemies)
                distance = max(1, actor.normal_move_distance())
                legal = (Position(actor.position.x + dx, actor.position.y + dy)
                         for dx in range(-distance, distance + 1)
                         for dy in range(-distance, distance + 1)
                         if abs(dx) + abs(dy) <= distance and (dx or dy))
                best = min((min(cell.distance_to(unit.position) for unit in enemies)
                            for cell in legal if battle.in_bounds(cell)
                            and battle.can_place_unit(actor, cell, ignore=actor, mover=actor)),
                           default=before)
                benefit = 20.0 + 15.0 * min(3, before - best) if best < before else 0.0
        return benefit - _STAT_COST[stat]

    def ai_priority_score(self, context: Any) -> float:
        return self.ai_payload_value(context.battle, context.actor, context.payload)

    def execute(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> None:
        code = str(payload.get("option_code") or "")
        if code not in {entry["code"] for entry in self.options(actor)}:
            raise ActionError("禁忌牺牲选项已失效。")
        stat, reset = code.split("|", 1)
        actor.add_status(SacrificedAbility(stat), source=actor)
        if reset == "attack":
            actor.attacks_used = 0
        else:
            actor.move_used = False
            actor.normal_move_actions_used = 0
            actor.normal_move_steps_used = 0
        battle.log_public_event(f"{actor.name} 牺牲1点{_STATS[stat]}，重置了本轮{'普攻' if reset == 'attack' else '普通移动'}次数。",
                                source=actor)


class HungrySoulRewindSkill(Skill):
    requires_damage_choice = True
    ai_optional_choice_owned = True

    def __init__(self) -> None:
        super().__init__("hungry_soul_rewind", "饿魂回溯",
                         "每敌方进攻轮结束时可随时使用一次；攻守速范重置为源基础数值，魔不变。",
                         max_uses_per_turn=1, target_mode="self", timing="instant")
        self._enemy_turn_end_open = False

    def can_use(self, battle: Battle, actor: Unit,
                payload: dict[str, Any] | None = None) -> tuple[bool, str]:
        ok, reason = super().can_use(battle, actor, payload)
        if not ok:
            return ok, reason
        ending = battle.current_turn_unit()
        if (not self._enemy_turn_end_open or ending is None or ending.player_id == actor.player_id
                or not actor.alive or actor.position is None):
            return False, "饿魂回溯仅能在对方武将进攻回合结束时使用。"
        return True, ""

    def on_before_any_turn_end(self, battle: Battle) -> bool:
        actor = self.owner
        if actor is None or not actor.alive or actor.position is None:
            return False
        self._enemy_turn_end_open = True
        try:
            if not self.can_use(battle, actor)[0]:
                return False
            choice = battle.optional_position_choice(
                actor, ["use"], action_name=self.name, kind="optional_skill_use",
                choice_context={"skill_code": self.code})
            if choice != "use":
                return False
            payload = {"type": "skill", "unit_id": actor.unit_id, "skill_code": self.code}
            battle.prepay_skill_resources(self, actor, payload)
            if not actor.alive:
                battle.cleanup_dead_units()
                return False
            actor.notify_action_declared(battle, "skill", payload)
            self.finalize_use(battle, actor)
            self.execute(battle, actor, payload)
            return True
        finally:
            self._enemy_turn_end_open = False

    def ai_optional_choice_score(self, battle: Battle, actor: Unit,
                                 prompt: dict[str, Any], choice: str) -> float | None:
        if (actor is not self.owner or prompt.get("kind") != "optional_skill_use"
                or (prompt.get("choice_context") or {}).get("skill_code") != self.code
                or choice != "use"):
            return None
        weights = {"attack": 27.0, "defense": 30.0, "speed": 18.0, "attack_range": 19.0}
        return sum((float(getattr(actor.base_stats, stat)) - actor.stat(stat)) * weight
                   for stat, weight in weights.items()) - 5.0

    def execute(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> None:
        for status in list(actor.statuses):
            if isinstance(status, RewoundAbilities):
                actor.remove_status(status, battle)
        actor.add_status(RewoundAbilities(), source=actor)
        battle.log_public_event(f"{actor.name} 在敌方轮末回溯攻守速范；当前魔保持 {actor.current_mana:g}。",
                                source=actor)
