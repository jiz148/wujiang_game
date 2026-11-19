"""R359 Dios's four independent floating cannons."""

from __future__ import annotations

from itertools import combinations
from typing import Any

from wujiang.tactical.engine.core import (
    ActionError, Battle, BattleFieldEffect, DamageContext, Position, Skill, Stats, Trait, Unit,
)
from wujiang.tactical.heroes.base import AbstractHero
from wujiang.tactical.heroes.common import (
    ensure_enemy, is_mana_drain_immune, payload_cells, payload_target_unit,
)
from wujiang.tactical.heroes.mechanics.next_five_geometry import square_around_cells


def _present(unit: Unit | None) -> bool:
    return unit is not None and unit.alive and not unit.banished and unit.position is not None


class DiosCannonDrainSkill(Skill):
    """Drain the target's actual mana straight into the living summoner."""

    ai_payload_value_owned = True

    def __init__(self) -> None:
        super().__init__("dios_cannon_drain", "吸魔", "本人轮一次，夺取敌方至多1魔并交给迪奥斯。",
                         max_uses_per_turn=1, target_mode="enemy")

    def eligible_targets(self, battle: Battle, actor: Unit) -> list[Unit]:
        return [target for target in battle.enemy_units(actor.player_id)
                if _present(target) and battle.unit_target_in_range_and_line(actor, target, actor.targeting_range())]

    def preview(self, battle: Battle, actor: Unit) -> dict[str, Any]:
        targets = self.eligible_targets(battle, actor)
        return {"cells": [cell.to_dict() for target in targets for cell in battle.unit_cells(target)],
                "target_unit_ids": [target.unit_id for target in targets], "secondary_cells": [],
                "requires_target": True}

    def execute(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> None:
        target = payload_target_unit(battle, payload)
        ensure_enemy(actor, target)
        if not battle.is_declared_resolution(actor, payload, {"skill"}) and target not in self.eligible_targets(battle, actor):
            raise ActionError("吸魔目标不在合法范围或直线上。")
        target_ctx = battle.validate_target(actor, target, action_name=self.name, is_skill=True,
                                            is_hostile=True, record_effect_on_validation=False)
        if target_ctx.cancelled:
            battle.log(target_ctx.reason)
            return
        if is_mana_drain_immune(target):
            battle.log(f"{target.name} 无法被吸魔。")
            return
        lost = target.drain_mana(1.0)
        owner = battle.units.get(actor.summoner_id or "")
        if _present(owner):
            owner.gain_mana(lost)
        if lost > 0:
            battle.record_skill_effect(target)
        battle.log(f"{actor.name} 从 {target.name} 吸取 {lost:g} 魔，交给 {owner.name if _present(owner) else '已离场的召唤者'}。")

    def ai_payload_candidates(self, battle: Battle, actor: Unit,
                              action: dict[str, Any]) -> list[dict[str, Any]]:
        return [{"type": "skill", "unit_id": actor.unit_id, "skill_code": self.code,
                 "target_unit_id": target.unit_id} for target in self.eligible_targets(battle, actor)
                if self.ai_payload_value(battle, actor, {"target_unit_id": target.unit_id}) > 0]

    def ai_candidate_payloads(self, context: Any) -> list[dict[str, Any]]:
        return [{**context.base_payload, "target_unit_id": item["target_unit_id"]}
                for item in self.ai_payload_candidates(context.battle, context.actor, context.action)]

    def ai_payload_value(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> float:
        target = battle.units.get(str(payload.get("target_unit_id") or ""))
        owner = battle.units.get(actor.summoner_id or "")
        if target is None or target not in self.eligible_targets(battle, actor) or is_mana_drain_immune(target):
            return -50.0
        stolen = min(1.0, target.current_mana)
        received = min(stolen, max(0.0, owner.max_mana() - owner.current_mana)) if _present(owner) else 0.0
        return stolen * 38.0 + received * 43.0 - 8.0

    def ai_effective_enemy_impact(self, battle: Battle, actor: Unit, payload: dict[str, Any],
                                  targets: list[Unit] | None = None, shared: Any = None,
                                  action: dict[str, Any] | None = None) -> bool:
        target = battle.units.get(str(payload.get("target_unit_id") or ""))
        return bool(target is not None and target in self.eligible_targets(battle, actor)
                    and target.current_mana > 0 and not is_mana_drain_immune(target))

    def ai_priority_score(self, context: Any) -> float:
        return self.ai_payload_value(context.battle, context.actor, context.payload)


class DiosFloatingCannon(AbstractHero):
    hero_code = "dios_floating_cannon"
    hero_name = "暗。浮游炮"
    role = "召唤物"
    attribute = "暗"
    race = "恶魔"
    level = 1
    base_stats = Stats(attack=4, defense=1, speed=2, attack_range=3, mana=0)
    raw_skill_text = "吸魔"
    raw_trait_text = "7×7内被动技能费魔+1；被破坏后下回合重召；击破者血减半"

    def __init__(self, player_id: int) -> None:
        super().__init__(player_id, is_summon=True)

    def build_skills(self) -> list[Skill]:
        return [DiosCannonDrainSkill()]

    def build_traits(self) -> list[Trait]:
        return [DiosCannonLifetimeTrait()]


class DiosCannonAura(BattleFieldEffect):
    def __init__(self, cannon: DiosFloatingCannon) -> None:
        super().__init__("浮游炮干扰", "该炮周围7×7的双方单位使用被动技能时费魔+1。")
        self.source_unit_id = cannon.unit_id

    def affected_cells(self, battle: Battle) -> list[Position]:
        cannon = battle.units.get(self.source_unit_id)
        if not _present(cannon):
            return []
        origin = cannon.position
        return [Position(x, y) for y in range(max(0, origin.y - 3), min(battle.height, origin.y + 4))
                for x in range(max(0, origin.x - 3), min(battle.width, origin.x + 4))]

    def modify_skill_mana_cost(self, battle: Battle, actor: Unit, skill: Skill,
                               payload: dict[str, Any] | None, cost: float) -> float:
        cannon = battle.units.get(self.source_unit_id)
        if (skill.timing in {"passive", "reaction"} and _present(cannon) and _present(actor)
                and battle.distance_between_units(cannon, actor) <= 3):
            return cost + 1.0
        return cost


class DiosCannonLifetimeTrait(Trait):
    """The cannon owns its aura even when its summoner dies first."""

    def __init__(self) -> None:
        super().__init__("浮游炮干扰源", "离场时移除本炮的被动技能费魔干扰。")

    def on_owner_removed(self, battle: Battle) -> None:
        owner = self.owner
        if owner is None:
            return
        for effect in list(battle.field_effects):
            if isinstance(effect, DiosCannonAura) and effect.source_unit_id == owner.unit_id:
                battle.remove_field_effect(effect)


class DiosCannonTrackerTrait(Trait):
    """Own original cannon identities, the live auras, and delayed returns."""

    def __init__(self) -> None:
        super().__init__("浮游炮编队", "四门暗浮游炮独立存在，击破后在迪奥斯轮初回补。")
        self.cannon_ids: set[str] = set()
        self.pending: dict[str, tuple[DiosFloatingCannon, Position]] = {}

    def register(self, battle: Battle, cannon: DiosFloatingCannon) -> None:
        self.cannon_ids.add(cannon.unit_id)
        battle.add_field_effect(DiosCannonAura(cannon), source=cannon)

    def on_unit_destroyed(self, battle: Battle, destroyed_unit: Unit) -> None:
        if destroyed_unit.unit_id not in self.cannon_ids or destroyed_unit.unit_id in self.pending:
            return
        owner = self.owner
        if not _present(owner) or destroyed_unit.summoner_id != owner.unit_id:
            return
        if destroyed_unit.last_position is not None:
            self.pending[destroyed_unit.unit_id] = (destroyed_unit, destroyed_unit.last_position)
        killer = battle.units.get(str(getattr(destroyed_unit, "_destroying_source_unit_id", "")))
        if not _present(killer) or killer.unit_id == destroyed_unit.unit_id:
            return
        battle.queue_skill_effect_action(
            actor=owner, display_name="浮游炮击破反噬", effect_code="dios_cannon_retribution",
            payload={"target_unit_id": killer.unit_id}, target_units=[killer],
            effect_resolver=self.resolve_retribution,
        )

    @staticmethod
    def resolve_retribution(battle: Battle, owner: Unit, queued_action: Any) -> None:
        target = battle.units.get(str(queued_action.payload.get("target_unit_id") or ""))
        if not _present(target):
            return
        battle.resolve_damage(DamageContext(
            source=owner, target=target, attack_power=0, raw_damage=round(target.current_hp / 2.0, 4),
            ignore_shield=True,
            is_skill=True, action_name="浮游炮击破反噬", tags={"skill", "dios_cannon_retribution"},
        ))

    def on_owner_turn_start(self, battle: Battle) -> None:
        owner = self.owner
        if not _present(owner):
            return
        for unit_id, (cannon, original) in list(self.pending.items()):
            cells = [cell for cell in square_around_cells(battle, battle.unit_cells(owner), 1)
                     if battle.can_place_unit(cannon, cell)]
            if battle.can_place_unit(cannon, original):
                cell = original
            elif cells:
                cell = min(cells, key=lambda item: (item.distance_to(original), item.y, item.x))
            else:
                continue
            cannon.alive = True
            cannon.current_hp = cannon.max_health
            cannon.current_mana = cannon.max_mana()
            cannon.banished = False
            cannon.attacks_used = 0
            cannon.actions_taken_this_turn = []
            cannon.moved_this_turn = False
            battle.summon_unit(cannon, cell, summoner=owner)
            if cannon in battle.destroyed_units:
                battle.destroyed_units.remove(cannon)
            self.register(battle, cannon)
            del self.pending[unit_id]
            battle.log(f"{owner.name} 回补 1 门【暗。浮游炮】。")


class DiosFloatingCannonsSkill(Skill):
    ai_payload_value_owned = True

    def __init__(self) -> None:
        super().__init__("dios_floating_cannons", "暗。浮游炮*4",
                         "大招：在范内选择最多四个不同空格召唤暗。浮游炮。",
                         max_uses_per_battle=1, target_mode="cell")

    def available_cells(self, battle: Battle, actor: Unit) -> list[Position]:
        if actor.position is None:
            return []
        return [cell for cell in square_around_cells(battle, battle.unit_cells(actor), 1)
                if battle.in_bounds(cell) and (cell.x, cell.y) not in battle.blocked_cells
                and not battle.is_occupied(cell)]

    def selected_cells(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> list[Position]:
        selected = payload_cells(payload)
        legal = {(cell.x, cell.y) for cell in self.available_cells(battle, actor)}
        required = min(4, len(legal))
        if len(selected) != required or any((cell.x, cell.y) not in legal for cell in selected):
            raise ActionError(f"请选择 {required} 个不同的合法浮游炮落格。")
        return selected

    def can_use(self, battle: Battle, actor: Unit,
                payload: dict[str, Any] | None = None) -> tuple[bool, str]:
        ok, reason = super().can_use(battle, actor, payload)
        if not ok:
            return ok, reason
        if not self.available_cells(battle, actor):
            return False, "周围没有可召唤浮游炮的空格。"
        if payload and "cells" in payload:
            try:
                self.selected_cells(battle, actor, payload)
            except ActionError as exc:
                return False, str(exc)
        return True, ""

    def execute(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> None:
        cells = self.selected_cells(battle, actor, payload)
        tracker = next((trait for trait in actor.traits if isinstance(trait, DiosCannonTrackerTrait)), None)
        if tracker is None:
            raise ActionError("浮游炮编队未准备好。")
        for cell in cells:
            cannon = DiosFloatingCannon(actor.player_id)
            battle.summon_unit(cannon, cell, summoner=actor)
            tracker.register(battle, cannon)
        battle.log(f"{actor.name} 展开 {len(cells)} 门【暗。浮游炮】。")

    def preview(self, battle: Battle, actor: Unit) -> dict[str, Any]:
        cells = self.available_cells(battle, actor)
        return {"cells": [cell.to_dict() for cell in cells], "target_unit_ids": [],
                "secondary_cells": [], "requires_target": True,
                "selection": {"mode": "pattern_cells", "patterns": [], "ordered": False,
                              "required_cells": min(4, len(cells))}}

    def ai_requires_enemy_impact(self, battle: Battle, actor: Unit,
                                 payload: dict[str, Any]) -> bool:
        return False

    def ai_payload_value(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> float:
        try:
            cells = self.selected_cells(battle, actor, payload)
        except ActionError:
            return -1000.0
        value = len(cells) * 35.0
        for cell in cells:
            for unit in battle.all_units():
                if not _present(unit):
                    continue
                distance = battle.unit_distance_to_cell(unit, cell)
                if unit.player_id != actor.player_id:
                    value += max(0, 5 - distance) * 5.0
                    if distance <= 3 and any(skill.timing in {"passive", "reaction"} for skill in unit.skills):
                        value += 18.0
                elif distance <= 3 and any(skill.timing in {"passive", "reaction"} for skill in unit.skills):
                    value -= 12.0
        return value

    def _ai_selections(self, battle: Battle, actor: Unit) -> list[list[Position]]:
        cells = self.available_cells(battle, actor)
        required = min(4, len(cells))
        if not required:
            return []
        ranked = sorted(combinations(cells, required),
                        key=lambda group: self.ai_payload_value(
                            battle, actor, {"cells": [cell.to_dict() for cell in group]}), reverse=True)
        return [list(group) for group in ranked[:4]]

    def ai_payload_candidates(self, battle: Battle, actor: Unit,
                              action: dict[str, Any]) -> list[dict[str, Any]]:
        return [{"type": "skill", "unit_id": actor.unit_id, "skill_code": self.code,
                 "cells": [cell.to_dict() for cell in group]} for group in self._ai_selections(battle, actor)]

    def ai_candidate_payloads(self, context: Any) -> list[dict[str, Any]]:
        return [{**context.base_payload, "cells": [cell.to_dict() for cell in group]}
                for group in self._ai_selections(context.battle, context.actor)]

    def ai_priority_score(self, context: Any) -> float:
        return self.ai_payload_value(context.battle, context.actor, context.payload)
