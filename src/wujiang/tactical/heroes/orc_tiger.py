"""R374 寅: turn-scoped star attacks and a bonded tiger pack."""

from __future__ import annotations

from typing import Any

from wujiang.tactical.engine.core import ActionError, Battle, Position, Skill, Stats, StatusEffect, Trait, Unit
from wujiang.tactical.heroes.base import AbstractHero
from wujiang.tactical.heroes.common import positions_to_dict


def _present(unit: Unit | None) -> bool:
    return unit is not None and unit.alive and not unit.banished and unit.position is not None


class StarAppraisalStatus(StatusEffect):
    """Aggregate same-name dice on one recipient; base and other bonuses stay independent."""

    def __init__(self, turn_number: int, extra_attacks: int) -> None:
        super().__init__("星象鉴", "本实际回合的同名额外普攻次数，上限12。", duration=None)
        self.turn_number = turn_number
        self.extra_attacks = min(12, extra_attacks)

    def modify_attack_actions_per_turn(self, value: int) -> int:
        return value + self.extra_attacks

    def on_any_turn_end(self, battle: Battle, ended_player_id: int) -> None:
        if self.owner is not None and battle.turn_number == self.turn_number:
            self.owner.remove_status(self, battle)

    def to_public_dict(self, battle: Battle) -> dict[str, Any]:
        data = super().to_public_dict(battle)
        data["extra_attacks"] = self.extra_attacks
        return data


class StarAppraisalSkill(Skill):
    ai_payload_value_owned = True
    ai_enemy_impact_requirement = False
    requires_direct_unit_target_line = False

    def __init__(self) -> None:
        super().__init__("orc_star_appraisal", "星象鉴",
                         "每本人轮一次，任意己方单位本实际回合攻击次数增加公开骰点；同名额外次数合计最多12。",
                         max_uses_per_turn=1, target_mode="unit")

    def eligible(self, battle: Battle, actor: Unit) -> list[Unit]:
        return [unit for unit in battle.player_units(actor.player_id) if _present(unit)]

    def can_use(self, battle: Battle, actor: Unit,
                payload: dict[str, Any] | None = None) -> tuple[bool, str]:
        ok, reason = super().can_use(battle, actor, payload)
        if not ok:
            return ok, reason
        if payload and payload.get("target_unit_id"):
            if str(payload["target_unit_id"]) not in {unit.unit_id for unit in self.eligible(battle, actor)}:
                return False, "星象鉴只能指定场上己方单位。"
        return True, ""

    def execute(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> None:
        target = battle.units.get(str(payload.get("target_unit_id") or ""))
        if target not in self.eligible(battle, actor):
            raise ActionError("星象鉴目标已失效。")
        roll = battle.roll_die(6, action_name="星象鉴掷骰", beneficiary_player_id=actor.player_id)
        status = next((item for item in target.statuses if isinstance(item, StarAppraisalStatus)
                       and item.turn_number == battle.turn_number), None)
        if status is None:
            status = StarAppraisalStatus(battle.turn_number, roll)
            target.add_status(status, source=actor)
        else:
            status.extra_attacks = min(12, status.extra_attacks + roll)
        battle.log_public_event(
            f"{actor.name} 的星象鉴掷出 {roll}；{target.name} 本回合同名额外攻击次数现为 {status.extra_attacks}/12。",
            source=actor, target=target,
        )

    def preview(self, battle: Battle, actor: Unit) -> dict[str, Any]:
        targets = self.eligible(battle, actor)
        return {"cells": positions_to_dict([unit.position for unit in targets if unit.position]),
                "target_unit_ids": [unit.unit_id for unit in targets],
                "secondary_cells": [], "requires_target": True, "dice": "1d6"}

    def ai_payload_candidates(self, battle: Battle, actor: Unit,
                              action: dict[str, Any]) -> list[dict[str, Any]]:
        return [{"type": "skill", "unit_id": actor.unit_id, "skill_code": self.code,
                 "target_unit_id": target.unit_id} for target in self.eligible(battle, actor)]

    def ai_payload_value(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> float:
        target = battle.units.get(str(payload.get("target_unit_id") or ""))
        if target not in self.eligible(battle, actor) or not target.can_take_turn_actions(battle):
            return -100.0
        if not battle.basic_attack_target_ids_for_payload(target):
            return -30.0
        current = next((item.extra_attacks for item in target.statuses
                        if isinstance(item, StarAppraisalStatus)
                        and item.turn_number == battle.turn_number), 0)
        if current >= 12:
            return -100.0
        # A die is worth more when its recipient has a legal attack now.
        return 65.0 + min(12 - current, 3.5) * 9.0

    def ai_priority_score(self, context: Any) -> float:
        return self.ai_payload_value(context.battle, context.actor, context.payload)


class TigerPackBond(Trait):
    def __init__(self) -> None:
        super().__init__("虎灵同命", "任一同源虎灵真正破坏时，所有同源虎灵一同破坏。")

    def on_unit_destroyed(self, battle: Battle, destroyed_unit: Unit) -> None:
        tiger = self.owner
        if (not isinstance(tiger, TigerSpirit) or not isinstance(destroyed_unit, TigerSpirit)
                or tiger.summoner_id is None or tiger.summoner_id != destroyed_unit.summoner_id):
            return
        siblings = [unit for unit in battle.all_units()
                    if isinstance(unit, TigerSpirit) and unit.summoner_id == tiger.summoner_id
                    and unit is not destroyed_unit and unit.alive]
        for sibling in siblings:
            sibling.alive = False
            sibling.current_hp = 0
            battle.log_public_event(f"{destroyed_unit.name} 破坏，同源 {sibling.name} 随之破坏。",
                                    source=destroyed_unit, target=sibling)


class TigerSpirit(AbstractHero):
    hero_code = "orc_tiger_spirit"
    hero_name = "虎灵"
    role = "召唤物"
    attribute = "土"
    race = "灵体"
    level = 1
    base_stats = Stats(attack=3, defense=2, speed=3, attack_range=1, mana=0)
    raw_skill_text = ""
    raw_trait_text = "同源虎灵同命"

    def __init__(self, player_id: int) -> None:
        super().__init__(player_id, is_summon=True)

    def build_skills(self) -> list[Skill]:
        return []

    def build_traits(self) -> list[Trait]:
        return [TigerPackBond()]


class TigerCallTrait(Trait):
    requires_damage_choice = True
    ai_optional_choice_owned = True

    def __init__(self) -> None:
        super().__init__("虎灵召唤", "本人对敌方单位普攻后，周围可召唤当回合能行动的虎灵。")

    def legal_destinations(self, battle: Battle) -> list[Position]:
        owner = self.owner
        if not _present(owner):
            return []
        probe = TigerSpirit(owner.player_id)
        cells = {cell for origin in battle.unit_cells(owner) for cell in battle.neighbors(origin)}
        return [cell for cell in sorted(cells, key=lambda item: (item.y, item.x))
                if battle.can_place_unit(probe, cell, mover=probe)]

    def on_basic_attack_finished(self, battle: Battle, actor: Unit,
                                 payload: dict[str, Any], damage_contexts: list[Any],
                                 missed: bool) -> None:
        if actor is not self.owner or not _present(actor) or payload.get("basic_attack_cancelled"):
            return
        target_id = str(payload.get("target_unit_id") or "")
        target = battle.units.get(target_id) or next(
            (ctx.target for ctx in damage_contexts if ctx.target.unit_id == target_id), None)
        if target is None or target.player_id == actor.player_id:
            return
        options = self.legal_destinations(battle)
        if not options:
            return
        choices = [f"{cell.x},{cell.y}" for cell in options]
        choice = battle.optional_position_choice(
            actor, choices, action_name="虎灵召唤：选择周围落格", kind="required_placement",
            choice_context={"placement_cells": [cell.to_dict() for cell in options]},
        )
        if choice not in choices:
            return
        cell = options[choices.index(choice)]
        tiger = TigerSpirit(actor.player_id)
        battle.summon_unit(tiger, cell, summoner=actor)
        tiger.turn_ready = True
        battle.log_public_event(f"{actor.name} 普攻后在 ({cell.x},{cell.y}) 召唤虎灵；虎灵本回合可行动。",
                                source=actor, target=tiger)

    def ai_optional_choice_score(self, battle: Battle, actor: Unit,
                                 prompt: dict[str, Any], choice: str) -> float | None:
        if actor is not self.owner or prompt.get("action_name") != "虎灵召唤：选择周围落格":
            return None
        try:
            x, y = (int(part) for part in choice.split(",", 1))
        except (ValueError, TypeError):
            return -1000.0
        cell = Position(x, y)
        if cell not in self.legal_destinations(battle):
            return -1000.0
        enemy_distance = min((cell.distance_to(enemy.position) for enemy in battle.enemy_units(actor.player_id)
                              if enemy.position is not None), default=9)
        return 80.0 - enemy_distance * 6.0


class TigerDefenseTrait(Trait):
    def __init__(self) -> None:
        super().__init__("虎群护体", "本人守增加场上仍存活的本人虎灵数量。")

    def modify_stat(self, stat_name: str, value: float) -> float:
        owner = self.owner
        ref = getattr(owner, "_battle_ref", None) if owner is not None else None
        battle = ref() if ref is not None else None
        if stat_name != "defense" or owner is None or battle is None:
            return value
        return value + sum(isinstance(unit, TigerSpirit) and _present(unit)
                           and unit.summoner_id == owner.unit_id for unit in battle.all_units())
