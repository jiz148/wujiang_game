"""R338 Hermopolis: dynamic mirror body, timed specimens and name seal."""

from __future__ import annotations

from copy import copy
from typing import Any

from wujiang.tactical.engine.core import (
    ActionError, Battle, BattleComponent, BattleFieldEffect, Position, Skill,
    Stats, StatusEffect, Trait, Unit,
)
from wujiang.tactical.heroes.base import build_unit_id
from wujiang.tactical.heroes.common import DashMoveSkill, positions_to_dict


def _on_board(unit: Unit) -> bool:
    return unit.alive and not unit.banished and unit.position is not None


def _specimens(battle: Battle, player_id: int) -> list[Unit]:
    return [unit for unit in battle.all_units()
            if isinstance(unit, HermopolisSpecimen) and unit.player_id == player_id and _on_board(unit)]


def _plain_stat(unit: Unit, stat_name: str) -> float:
    """Cut a mutually recursive mirror group at its unmirrored current value."""
    value = float(getattr(unit.base_stats, stat_name))
    for component in unit.iter_components():
        if not isinstance(component, HermopolisMirrorTrait):
            value = component.modify_stat(stat_name, value)
    battle_ref = getattr(unit, "_battle_ref", None)
    battle = battle_ref() if battle_ref is not None else None
    if battle is not None:
        for effect in battle.field_effects:
            value = effect.modify_unit_stat(battle, unit, stat_name, value)
    return max(unit.stat_minimums.get(stat_name, float("-inf")), value)


class HermopolisMirrorTrait(Trait):
    def __init__(self) -> None:
        super().__init__("邪神镜像能力", "攻=其他在场单位最高守+1；守=其他在场单位最高攻+1，实时变化。")

    def modify_stat(self, stat_name: str, value: float) -> float:
        if stat_name not in {"attack", "defense"} or self.owner is None:
            return value
        battle_ref = getattr(self.owner, "_battle_ref", None)
        battle = battle_ref() if battle_ref is not None else None
        if battle is None:
            return value
        opposite = "defense" if stat_name == "attack" else "attack"
        values = []
        for other in battle.all_units():
            if other.unit_id == self.owner.unit_id or not _on_board(other):
                continue
            # Copies of this trait form a positive feedback cycle. All members
            # use each other's unmirrored current value as the cycle boundary.
            mirrors = any(isinstance(trait, HermopolisMirrorTrait) for trait in other.traits)
            values.append(_plain_stat(other, opposite) if mirrors else other.stat(opposite))
        return max(values, default=0.0) + 1.0


class HermopolisFullHealTrait(Trait):
    def __init__(self) -> None:
        super().__init__("邪神再生", "每位武将回合结束后，若仍在场，生命变满。")

    def on_any_turn_end(self, battle: Battle, ended_player_id: int) -> None:
        owner = self.owner
        if (owner is None or battle.is_army_turn() or not _on_board(owner)
                or owner.current_hp >= owner.max_health):
            return
        before = owner.current_hp
        owner.current_hp = owner.max_health
        if owner.current_hp > before:
            battle.log_public_event(f"{owner.name} 的生命变满。", source=owner, target=owner)


class HermopolisBlinkSkill(DashMoveSkill):
    ai_payload_value_owned = True

    def __init__(self) -> None:
        super().__init__("indeterminate_state", "不定态", "每本人回合一次，瞬移到任意完整合法落点。",
                         max_distance=99, mana_cost=0, max_uses_per_turn=1, allow_anywhere=True)

    def ai_payload_value(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> float | None:
        if actor.position is None:
            return -1000.0
        try:
            destination = Position(int(payload["x"]), int(payload["y"]))
        except (KeyError, TypeError, ValueError):
            return -1000.0
        if not battle.can_place_unit(actor, destination, ignore=actor, mover=actor):
            return -1000.0
        enemies = [enemy for enemy in battle.enemy_units(actor.player_id) if _on_board(enemy)]
        if not enemies:
            return -1000.0
        old_nearest = min(battle.distance_between_units(actor, enemy) for enemy in enemies)
        new_nearest = min(min(cell.distance_to(enemy_cell)
                              for cell in actor.footprint_cells_at(destination)
                              for enemy_cell in battle.unit_cells(enemy)) for enemy in enemies)
        can_attack = new_nearest <= actor.targeting_range()
        return (24.0 if can_attack and old_nearest > actor.targeting_range() else 0.0) \
            + max(0, old_nearest - new_nearest) * 3.0 - (8.0 if not can_attack else 0.0)

    def ai_priority_score(self, context: Any) -> float:
        return self.ai_payload_value(context.battle, context.actor, context.payload) or -1000.0


class HermopolisNameSeal(BattleFieldEffect):
    ignores_direct_effect_immunity = True

    def __init__(self, source_player_id: int, forbidden_name: str) -> None:
        super().__init__("地狱设计者封名", f"{forbidden_name}：下一对方武将回合末前不可召唤、普攻或用主动技能。")
        self.source_player_id = source_player_id
        self.forbidden_name = forbidden_name
        self.expired_after_enemy_end = False
        self.expiry_turn_number: int | None = None

    def active_for(self, battle: Battle) -> bool:
        return self.expiry_turn_number is None or battle.turn_number <= self.expiry_turn_number

    def matches(self, unit: Unit) -> bool:
        return (unit.player_id != self.source_player_id and unit.name == self.forbidden_name
                and not unit.is_summon and not unit.is_clone)

    def allows_unit_entry(self, battle: Battle, unit: Unit) -> tuple[bool, str]:
        if self.active_for(battle) and self.matches(unit):
            return False, f"【地狱设计者】封锁了{unit.name}的召唤。"
        return True, ""

    def blocks_skill_use(self, battle: Battle, actor: Unit, skill: Skill) -> tuple[bool, str]:
        if self.active_for(battle) and self.matches(actor) and skill.timing == "active":
            return True, f"{actor.name} 的主动技能被【地狱设计者】封锁。"
        return False, ""

    def on_any_turn_end(self, battle: Battle, ended_player_id: int) -> None:
        if ended_player_id != self.source_player_id and not battle.is_army_turn():
            # The end phase may still contain resurrection or follow-up actions.
            # Keep the seal through those actions, then clear before the next turn.
            self.expired_after_enemy_end = True
            self.expiry_turn_number = battle.turn_number

    def on_turn_start(self, battle: Battle, active_unit: Unit | None) -> None:
        if not self.expired_after_enemy_end:
            return
        for unit in battle.all_units():
            for status in list(unit.statuses):
                if isinstance(status, HermopolisNameSealStatus) and status.seal_id == self.component_id:
                    unit.remove_status(status, battle)
        battle.remove_field_effect(self)
        battle.log(f"{self.forbidden_name} 的【地狱设计者】封锁结束。")


class HermopolisNameSealStatus(StatusEffect):
    """Actor-local basic-attack gate; the field effect owns the matching name."""

    def __init__(self, seal: HermopolisNameSeal) -> None:
        super().__init__("地狱设计者普攻封锁", "该名字的敌方武将不能普攻。")
        self.seal_id = seal.component_id

    def can_use_basic_attack(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> tuple[bool, str]:
        if any(isinstance(effect, HermopolisNameSeal)
               and effect.component_id == self.seal_id and effect.active_for(battle)
               and effect.matches(actor)
               for effect in battle.field_effects):
            return False, f"{actor.name} 的普攻被【地狱设计者】封锁。"
        return True, ""


class HermopolisDesignerSkill(Skill):
    ai_payload_value_owned = True
    ai_enemy_impact_requirement = False
    target_cells_are_geometry_only = True

    def __init__(self) -> None:
        super().__init__("hell_designer", "地狱设计者", "选一名真正被破坏的武将，封其敌方同名武将至下一敌方武将回合末。",
                         max_uses_per_turn=1, target_mode="cell")

    @staticmethod
    def dead_heroes(battle: Battle) -> list[Unit]:
        return [unit for unit in battle.destroyed_units if not unit.alive and not unit.is_summon
                and not unit.is_clone]

    def chosen(self, battle: Battle, payload: dict[str, Any]) -> Unit:
        selected = str(payload.get("dead_unit_id") or "")
        found = next((unit for unit in self.dead_heroes(battle) if unit.unit_id == selected), None)
        if found is None:
            raise ActionError("请选择一名真正被破坏的武将。")
        return found

    def can_use(self, battle: Battle, actor: Unit, payload: dict[str, Any] | None = None) -> tuple[bool, str]:
        ok, reason = super().can_use(battle, actor, payload)
        if not ok:
            return ok, reason
        if not self.dead_heroes(battle):
            return False, "场上还没有真正被破坏的武将。"
        if payload:
            try:
                self.chosen(battle, payload)
            except ActionError as exc:
                return False, str(exc)
        return True, ""

    def execute(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> None:
        name = str(payload.get("sealed_name") or "") or self.chosen(battle, payload).name
        seal = HermopolisNameSeal(actor.player_id, name)
        battle.add_field_effect(seal, source=actor)
        # Actor-local gates cover every existing same-name body. Newly restored
        # bodies cannot enter while the effect remains active.
        for unit in battle.all_units():
            if seal.matches(unit):
                unit.statuses.append(HermopolisNameSealStatus(seal).bind(unit))
        battle.log_public_event(f"{actor.name} 封锁了敌方同名武将【{name}】。", source=actor)

    def queued_payload_metadata(self, battle: Battle, actor: Unit,
                                payload: dict[str, Any]) -> dict[str, Any]:
        return {"sealed_name": self.chosen(battle, payload).name}

    def preview(self, battle: Battle, actor: Unit) -> dict[str, Any]:
        cells = positions_to_dict(battle.unit_cells(actor))[:1]
        candidates = [{"id": dead.unit_id, "name": dead.name, "cells": cells}
                      for dead in self.dead_heroes(battle)]
        return {"cells": cells if candidates else [], "target_unit_ids": [],
                "secondary_cells": [], "requires_target": True,
                "selection": {"mode": "unit_cell", "candidates": candidates,
                              "target_key": "dead_unit_id",
                              "subject_label": "被破坏武将", "instruction": "先选择一名被破坏的武将，再点击高亮格确认封名。"}}

    def get_target_cells_for_payload(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> list[Position]:
        try:
            name = self.chosen(battle, payload).name
        except ActionError:
            return []
        return [cell for unit in battle.all_units() if unit.player_id != actor.player_id
                and unit.name == name for cell in battle.unit_cells(unit)]

    def get_target_units_for_payload(self, battle: Battle, actor: Unit,
                                     payload: dict[str, Any]) -> list[Unit]:
        try:
            name = self.chosen(battle, payload).name
        except ActionError:
            return []
        return [unit for unit in battle.all_units()
                if unit.player_id != actor.player_id and unit.name == name and _on_board(unit)]

    def ai_payload_value(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> float | None:
        try:
            name = self.chosen(battle, payload).name
        except ActionError:
            return -1000.0
        living = [unit for unit in battle.enemy_units(actor.player_id)
                  if unit.name == name and _on_board(unit) and not unit.is_summon]
        pending = any(unit.name == name and unit.player_id != actor.player_id
                      and unit.unit_id in battle.hell_rebirth_unit_ids for unit in battle.destroyed_units)
        return sum(24.0 + unit.stat("attack") * 5 for unit in living) + (24.0 if pending else 0.0) - 1.0

    def ai_priority_score(self, context: Any) -> float:
        return self.ai_payload_value(context.battle, context.actor, context.payload) or -1000.0


class HermopolisSpecimenLifeTrait(Trait):
    def __init__(self, birth_round: int) -> None:
        super().__init__("实验体期限", "完成三个完整战场轮后破坏。")
        self.birth_round = birth_round

    def on_any_turn_start(self, battle: Battle, active_unit: Unit) -> None:
        owner = self.owner
        if owner is None or not _on_board(owner) or battle.round_number < self.birth_round + 4:
            return
        battle.log(f"{owner.name} 的三轮实验期限结束。")
        owner.current_hp = 0
        owner.alive = False
        battle.cleanup_dead_units()

    def _clear_empty_specialization(self, battle: Battle) -> None:
        owner = self.owner
        if owner is None:
            return
        for effect in list(battle.field_effects):
            if (isinstance(effect, HermopolisSpecializationField)
                    and effect.source_player_id == owner.player_id
                    and not _specimens(battle, owner.player_id)):
                battle.remove_field_effect(effect)

    def on_owner_removed(self, battle: Battle) -> None:
        self._clear_empty_specialization(battle)

    def on_owner_banished(self, battle: Battle) -> None:
        self._clear_empty_specialization(battle)


class HermopolisSpecimen(Unit):
    hero_code = "hermopolis_specimen"

    def __init__(self, player_id: int, source: Unit, birth_round: int) -> None:
        self.footprint_width = source.footprint_width
        self.footprint_height = source.footprint_height
        self.base_footprint_offsets = list(source.base_footprint_offsets)
        self._source_traits = [trait for trait in source.traits
                               if not isinstance(trait, HermopolisSpecimenLifeTrait)]
        self._birth_round = birth_round
        super().__init__(unit_id=build_unit_id("hermopolis_specimen"),
                         player_id=player_id, name=f"{source.name}（实验体）", title="实验体",
                         role=source.role, attribute=source.attribute, race=source.race,
                         level=source.level, base_stats=Stats(
                             attack=source.base_stats.attack, defense=source.base_stats.defense,
                             speed=source.base_stats.speed, attack_range=source.base_stats.attack_range,
                             mana=source.base_stats.mana),
                         raw_skill_text="", raw_trait_text=source.raw_trait_text,
                         max_health=source.max_health, is_summon=True)
        self.cannot_use_skills = True

    def build_skills(self) -> list[Skill]:
        return []

    def build_traits(self) -> list[Trait]:
        inherited: list[Trait] = []
        for trait in self._source_traits:
            replicated = copy(trait)
            BattleComponent.__init__(replicated, replicated.name, replicated.description)
            inherited.append(replicated)
        inherited.append(HermopolisSpecimenLifeTrait(self._birth_round))
        return inherited


class HermopolisExperimentSkill(Skill):
    ai_payload_value_owned = True
    ai_enemy_impact_requirement = False

    def __init__(self) -> None:
        super().__init__("experiment", "实验体", "每2本人轮一次，复制一名单位的原始能力与特性，在其周围召唤无技能实验体，三轮后破坏。",
                         cooldown_turns=2, target_mode="cell")

    def legal_cells(self, battle: Battle, actor: Unit, target: Unit) -> list[Position]:
        if not _on_board(target):
            return []
        specimen = copy(target)
        specimen.unit_id = "hermopolis-specimen-placement-probe"
        specimen.player_id = actor.player_id
        specimen.is_summon = True
        specimen.is_clone = False
        specimen.is_mount = False
        specimen.mount_owner_id = None
        specimen.mounted_on_unit_id = None
        specimen.ridden_by_unit_id = None
        specimen.allows_multiple_riders = False
        specimen.allow_enemy_destination_overlap = False
        specimen.statuses = []
        specimen.footprint_offsets = list(target.base_footprint_offsets)
        neighbors = {neighbor for body in battle.unit_cells(target) for neighbor in battle.neighbors(body)}
        return [cell for cell in sorted(neighbors, key=lambda pos: (pos.y, pos.x))
                if battle.can_place_unit(specimen, cell, mover=specimen)]

    def target(self, battle: Battle, payload: dict[str, Any]) -> Unit:
        target = battle.units.get(str(payload.get("target_unit_id") or ""))
        if target is None or not _on_board(target):
            raise ActionError("请选择场上的一个单位作为实验体原型。")
        return target

    def can_use(self, battle: Battle, actor: Unit, payload: dict[str, Any] | None = None) -> tuple[bool, str]:
        ok, reason = super().can_use(battle, actor, payload)
        if not ok:
            return ok, reason
        if payload:
            try:
                target = self.target(battle, payload)
                cell = Position(int(payload["x"]), int(payload["y"]))
            except (ActionError, KeyError, ValueError, TypeError) as exc:
                return False, str(exc)
            if cell not in self.legal_cells(battle, actor, target):
                return False, "所选实验体落格不合法。"
        elif not any(self.legal_cells(battle, actor, target) for target in battle.all_units()
                     if _on_board(target) and battle.unit_can_be_selected(target, actor=actor)[0]):
            return False, "没有可召唤实验体的外邻落点。"
        return True, ""

    def execute(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> None:
        try:
            target = self.target(battle, payload)
            cell = Position(int(payload["x"]), int(payload["y"]))
        except (ActionError, KeyError, TypeError, ValueError):
            battle.log(f"{actor.name} 的实验体原型已离场，召唤落空。")
            return
        if cell not in self.legal_cells(battle, actor, target):
            battle.log(f"{actor.name} 的实验体落点已失效，召唤落空。")
            return
        specimen = HermopolisSpecimen(actor.player_id, target, battle.round_number)
        battle.summon_unit(specimen, cell, summoner=actor)
        battle.log_public_event(f"{actor.name} 以{target.name}为原型制造实验体。", source=actor, target=specimen)

    def preview(self, battle: Battle, actor: Unit) -> dict[str, Any]:
        candidates = [{"id": target.unit_id, "name": target.name,
                       "cells": positions_to_dict(self.legal_cells(battle, actor, target))}
                      for target in battle.all_units()
                      if _on_board(target) and battle.unit_can_be_selected(target, actor=actor)[0]]
        candidates = [entry for entry in candidates if entry["cells"]]
        cells = list(dict.fromkeys((cell["x"], cell["y"])
                                    for entry in candidates for cell in entry["cells"]))
        return {"cells": [{"x": x, "y": y} for x, y in cells],
                "target_unit_ids": [], "secondary_cells": [], "requires_target": True,
                "selection": {"mode": "unit_cell", "candidates": candidates,
                              "subject_label": "实验体原型", "instruction": "先选一个原型单位，再点它周围高亮格生成实验体。"}}

    def get_target_cells_for_payload(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> list[Position]:
        return []

    def ai_payload_value(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> float | None:
        try:
            target = self.target(battle, payload)
            destination = Position(int(payload["x"]), int(payload["y"]))
        except (ActionError, KeyError, ValueError, TypeError):
            return -1000.0
        if destination not in self.legal_cells(battle, actor, target):
            return -1000.0
        strength = target.base_stats.attack + target.base_stats.defense + target.base_stats.speed * 0.5
        trait_value = min(4, len(target.traits)) * 6.0
        new_field = not _specimens(battle, actor.player_id)
        field_bonus = 20.0 if new_field and any(_on_board(enemy) for enemy in battle.enemy_units(actor.player_id)) else 0.0
        enemy_penalty = 8.0 if target.player_id != actor.player_id else 0.0
        return 18.0 + strength * 3.0 + trait_value + field_bonus - enemy_penalty

    def ai_priority_score(self, context: Any) -> float:
        return self.ai_payload_value(context.battle, context.actor, context.payload) or -1000.0


class HermopolisSpecializationField(BattleFieldEffect):
    def __init__(self, source_player_id: int) -> None:
        super().__init__("特化起点", "己方实验体仍在场时，对方所有单位攻守各-1，到1，不叠加。")
        self.source_player_id = source_player_id

    def modify_unit_stat(self, battle: Battle, unit: Unit, stat_name: str, value: float) -> float:
        if (unit.player_id != self.source_player_id and stat_name in {"attack", "defense"}
                and not unit.magic_immunity and not unit.skill_non_damage_effects_blocked()
                and _specimens(battle, self.source_player_id)):
            return max(1.0, value - 1.0)
        return value

    def on_any_turn_end(self, battle: Battle, ended_player_id: int) -> None:
        if not _specimens(battle, self.source_player_id):
            battle.remove_field_effect(self)


class HermopolisSpecializationSkill(Skill):
    ai_payload_value_owned = True
    ai_enemy_impact_requirement = False

    def __init__(self) -> None:
        super().__init__("specialization_start", "特化起点", "每3本人轮一次；有己方实验体时对方全体攻守-1到1，最后一只实验体消失即结束。",
                         cooldown_turns=3, target_mode="none")

    def can_use(self, battle: Battle, actor: Unit, payload: dict[str, Any] | None = None) -> tuple[bool, str]:
        ok, reason = super().can_use(battle, actor, payload)
        if not ok:
            return ok, reason
        if not _specimens(battle, actor.player_id):
            return False, "场上没有己方实验体。"
        if any(isinstance(effect, HermopolisSpecializationField)
               and effect.source_player_id == actor.player_id for effect in battle.field_effects):
            return False, "己方特化起点已生效，效果不叠加。"
        return True, ""

    def execute(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> None:
        if not _specimens(battle, actor.player_id):
            battle.log(f"{actor.name} 的实验体已离场，特化起点未生效。")
            return
        battle.add_field_effect(HermopolisSpecializationField(actor.player_id), source=actor)
        battle.log_public_event(f"{actor.name} 启动特化起点，敌方单位攻守下降。", source=actor)

    def preview(self, battle: Battle, actor: Unit) -> dict[str, Any]:
        return {"cells": [], "target_unit_ids": [], "secondary_cells": [], "requires_target": False}

    def ai_payload_value(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> float | None:
        if not _specimens(battle, actor.player_id):
            return -1000.0
        value = sum((1 if enemy.stat("attack") > 1 else 0)
                    + (1 if enemy.stat("defense") > 1 else 0)
                    for enemy in battle.enemy_units(actor.player_id) if _on_board(enemy))
        return value * 17.0 - 4.0

    def ai_priority_score(self, context: Any) -> float:
        return self.ai_payload_value(context.battle, context.actor, context.payload) or -1000.0
