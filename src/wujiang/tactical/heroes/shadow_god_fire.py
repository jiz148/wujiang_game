"""R366 Shadow God fire king: hound, four-turn whip, and summon aftermath."""

from __future__ import annotations

from typing import Any

from wujiang.tactical.engine.core import (
    ActionError, Battle, BattleFieldEffect, DamageContext, Position, QueuedAction,
    Skill, Stats, StatusEffect, Trait, Unit,
)
from wujiang.tactical.heroes.base import AbstractHero
from wujiang.tactical.heroes.common import positions_to_dict


def _present(unit: Unit | None) -> bool:
    return bool(unit is not None and unit.alive and not unit.banished and unit.position is not None)


class ShadowHoundPierceTrait(Trait):
    def __init__(self) -> None:
        super().__init__("影恶犬破魔普攻", "普攻破魔，但受体仍可合法连锁。")

    def basic_attack_payload_metadata(self, battle: Battle, actor: Unit,
                                      payload: dict[str, Any] | None = None) -> dict[str, Any]:
        return {"ignore_shield": True}

    def on_targeted(self, battle: Battle, ctx: Any) -> None:
        if ctx.actor is self.owner and not ctx.is_skill and "attack" in ctx.tags:
            ctx.ignore_shield = True

    def on_before_damage(self, battle: Battle, ctx: DamageContext) -> None:
        if ctx.source is self.owner and not ctx.is_skill and "attack" in ctx.tags:
            ctx.ignore_shield = True


class ShadowHound(AbstractHero):
    hero_code = "shadow_hound"
    hero_name = "影恶犬"
    role = "召唤物"
    attribute = "火"
    race = "灵体"
    level = 1
    base_stats = Stats(attack=4, defense=4, speed=4, attack_range=1, mana=0)
    raw_skill_text = ""
    raw_trait_text = "普攻破魔"

    def __init__(self, player_id: int) -> None:
        super().__init__(player_id, is_summon=True)

    def build_skills(self) -> list[Skill]:
        return []

    def build_traits(self) -> list[Trait]:
        return [ShadowHoundPierceTrait()]


class ShadowHoundSkill(Skill):
    ai_payload_value_owned = True
    ai_enemy_impact_requirement = False

    def __init__(self) -> None:
        super().__init__("shadow_hound", "影恶犬",
                         "每本人轮一次；身边召一只攻4守4速4范1、普攻破魔的影恶犬；同源最多一只。",
                         max_uses_per_turn=1, target_mode="cell")

    def current_hound(self, battle: Battle, actor: Unit) -> Unit | None:
        return next((unit for unit in battle.all_units() if isinstance(unit, ShadowHound)
                     and _present(unit) and unit.summoner_id == actor.unit_id), None)

    def legal_destinations(self, battle: Battle, actor: Unit) -> list[Position]:
        if actor.position is None or self.current_hound(battle, actor) is not None:
            return []
        probe = ShadowHound(actor.player_id)
        cells = {cell for origin in battle.unit_cells(actor) for cell in battle.neighbors(origin)}
        return [cell for cell in sorted(cells, key=lambda item: (item.y, item.x))
                if battle.can_place_unit(probe, cell, mover=probe)]

    def can_use(self, battle: Battle, actor: Unit,
                payload: dict[str, Any] | None = None) -> tuple[bool, str]:
        ok, reason = super().can_use(battle, actor, payload)
        if not ok:
            return ok, reason
        if not self.legal_destinations(battle, actor):
            return False, "己方已有本人影恶犬或周围没有合法落格。"
        if payload and payload.get("x") is not None:
            cell = Position(int(payload["x"]), int(payload["y"]))
            if cell not in self.legal_destinations(battle, actor):
                return False, "请选择影恶犬合法落格。"
        return True, ""

    def execute(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> None:
        cell = Position(int(payload["x"]), int(payload["y"]))
        if cell not in self.legal_destinations(battle, actor):
            raise ActionError("影恶犬落格已失效。")
        hound = ShadowHound(actor.player_id)
        battle.summon_unit(hound, cell, summoner=actor)
        battle.log_public_event(f"{actor.name} 召唤影恶犬。", source=actor, target=hound)

    def preview(self, battle: Battle, actor: Unit) -> dict[str, Any]:
        return {"cells": positions_to_dict(self.legal_destinations(battle, actor)),
                "target_unit_ids": [], "secondary_cells": [], "requires_target": True}

    def get_target_cells_for_payload(self, battle: Battle, actor: Unit,
                                     payload: dict[str, Any]) -> list[Position]:
        return [Position(int(payload["x"]), int(payload["y"]))]

    def ai_payload_candidates(self, battle: Battle, actor: Unit,
                              action: dict[str, Any]) -> list[dict[str, Any]]:
        return [{"type": "skill", "unit_id": actor.unit_id, "skill_code": self.code,
                 "x": cell.x, "y": cell.y} for cell in self.legal_destinations(battle, actor)]

    def ai_payload_value(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> float:
        try:
            cell = Position(int(payload["x"]), int(payload["y"]))
        except (KeyError, TypeError, ValueError):
            return -1000.0
        if cell not in self.legal_destinations(battle, actor):
            return -1000.0
        distance = min((cell.distance_to(enemy.position)
                        for enemy in battle.enemy_units(actor.player_id) if enemy.position), default=8)
        return 75.0 - 4.0 * distance

    def ai_priority_score(self, context: Any) -> float:
        return self.ai_payload_value(context.battle, context.actor, context.payload)


class ShadowFireWhipBurn(StatusEffect):
    def __init__(self, source_unit_id: str) -> None:
        super().__init__("影神烈火鞭灼烧", "每本人轮末固定受3点伤害，持续4次。", duration=None)
        self.source_unit_id = source_unit_id
        self.remaining_ticks = 4

    def on_owner_turn_end(self, battle: Battle) -> None:
        target = self.owner
        if not _present(target):
            return
        source = battle.units.get(self.source_unit_id)
        battle.resolve_damage(DamageContext(
            source=source, target=target, attack_power=0, raw_damage=3,
            is_skill=False, action_name="烈火鞭灼烧", tags={"shadow_fire_whip_burn"},
        ))
        self.remaining_ticks -= 1
        if self.remaining_ticks <= 0:
            target.remove_status(self, battle)

    def to_public_dict(self, battle: Battle) -> dict[str, Any]:
        data = super().to_public_dict(battle)
        data["remaining_ticks"] = self.remaining_ticks
        return data


class ShadowFireWhipSkill(Skill):
    ai_payload_value_owned = True

    def __init__(self) -> None:
        super().__init__("shadow_fire_whip", "影神力。烈火鞭",
                         "每本人轮一次，被击中或对此技用被动者受4次本人轮末3伤，不叠加。",
                         max_uses_per_turn=1, target_mode="enemy")

    def on_reaction_resolved(self, battle: Battle, declared_action: QueuedAction,
                             reaction: QueuedAction) -> None:
        if reaction.action_type != "reaction_skill":
            return
        reactor = battle.units.get(reaction.actor_id)
        code = reaction.payload.get("action_code") or reaction.payload.get("skill_code")
        used = reactor.skill_map().get(code) if reactor is not None else None
        if used is not None and used.timing in {"passive", "reaction"}:
            declared_action.payload.setdefault("shadow_whip_passive_ids", []).append(reaction.actor_id)

    def _mark(self, battle: Battle, actor: Unit, target: Unit) -> None:
        if not _present(target) or target.player_id == actor.player_id:
            return
        check = battle.validate_target(actor, target, action_name=self.name,
                                       is_skill=True, is_hostile=True, ignore_shield=True,
                                       resolve_defenses=False, tags={"skill", self.code})
        if check.cancelled:
            return
        old = next((status for status in target.statuses
                    if isinstance(status, ShadowFireWhipBurn) and status.source_unit_id == actor.unit_id), None)
        if old is not None:
            old.remaining_ticks = 4
        else:
            target.add_status(ShadowFireWhipBurn(actor.unit_id), source=actor)
        battle.log_public_event(f"{target.name} 受到烈火鞭四轮灼烧。", source=actor, target=target)

    def _mark_passive_reactors(self, battle: Battle, actor: Unit,
                               payload: dict[str, Any]) -> None:
        for unit_id in dict.fromkeys(payload.get("shadow_whip_passive_ids", [])):
            target = battle.units.get(unit_id)
            if target is not None:
                self._mark(battle, actor, target)

    def execute(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> None:
        target_id = str(payload.get("resolved_target_unit_id") or payload.get("target_unit_id") or "")
        target = battle.units.get(target_id)
        if _present(target):
            ctx = battle.resolve_damage(DamageContext(
                source=actor, target=target, attack_power=actor.stat("attack"),
                is_skill=True, action_name=self.name, tags={"skill", self.code},
            ))
            if ctx.actual_damage > 0 and target.alive:
                self._mark(battle, actor, target)
        self._mark_passive_reactors(battle, actor, payload)

    def on_target_missed(self, battle: Battle, actor: Unit,
                         payload: dict[str, Any]) -> None:
        self._mark_passive_reactors(battle, actor, payload)

    def preview(self, battle: Battle, actor: Unit) -> dict[str, Any]:
        targets = [unit for unit in battle.enemy_units(actor.player_id)
                   if _present(unit) and battle.distance_between_units(actor, unit) <= actor.targeting_range()]
        return {"cells": positions_to_dict([unit.position for unit in targets if unit.position]),
                "target_unit_ids": [unit.unit_id for unit in targets],
                "secondary_cells": [], "requires_target": True}

    def ai_payload_candidates(self, battle: Battle, actor: Unit,
                              action: dict[str, Any]) -> list[dict[str, Any]]:
        return [{"type": "skill", "unit_id": actor.unit_id, "skill_code": self.code,
                 "target_unit_id": unit_id} for unit_id in self.preview(battle, actor)["target_unit_ids"]]

    def ai_payload_value(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> float:
        target = battle.units.get(str(payload.get("target_unit_id") or ""))
        if not _present(target) or target.player_id == actor.player_id:
            return -1000.0
        damage = battle.damage_rule.calculate_damage(actor.stat("attack"), target.stat("defense"))
        return damage * 100.0 + min(target.current_hp, 3.0) * 55.0 - 10.0

    def ai_effect_units(self, battle: Battle, actor: Unit,
                        payload: dict[str, Any]) -> list[Unit]:
        target = battle.units.get(str(payload.get("target_unit_id") or ""))
        return [target] if _present(target) and target.player_id != actor.player_id else []

    def ai_effective_enemy_impact(self, battle: Battle, actor: Unit,
                                  payload: dict[str, Any], targets: list[Unit] | None = None,
                                  shared: Any = None, action: dict[str, Any] | None = None) -> bool:
        return bool(self.ai_effect_units(battle, actor, payload)) and self.ai_payload_value(
            battle, actor, payload) > 0

    def ai_priority_score(self, context: Any) -> float:
        return self.ai_payload_value(context.battle, context.actor, context.payload)


class ShadowPassiveLimitStatus(StatusEffect):
    def __init__(self, source_unit_id: str) -> None:
        super().__init__("影神被动限制", "本实际回合对永火王的攻击最多使用一次被动技能。",
                         duration=None)
        self.source_unit_id = source_unit_id

    def on_any_turn_end(self, battle: Battle, ended_player_id: int) -> None:
        if self.owner is not None:
            self.owner.remove_status(self, battle)

    def blocks_skill_use(self, battle: Battle, actor: Unit,
                         skill: Skill) -> tuple[bool, str]:
        if (skill.timing in {"passive", "reaction"} and
                sum(item.uses_this_turn for item in actor.skills
                    if item.timing in {"passive", "reaction"}) >= 1):
            return True, "本实际回合已经使用过一次被动技能。"
        return False, ""


class ShadowPassiveLimitTrait(Trait):
    def __init__(self) -> None:
        super().__init__("影神被动限制", "被本人攻击的对象本实际回合只能使用一次被动技能。")

    def blocks_target_passive_reaction(self, battle: Battle, target: Unit,
                                       queued_action: QueuedAction, skill: Skill) -> bool:
        owner = self.owner
        return bool(owner is not None and queued_action.actor_id == owner.unit_id
                    and (target.unit_id in queued_action.target_unit_ids
                         or target in battle.effect_units_at_cells(queued_action.target_cells))
                    and sum(item.uses_this_turn for item in target.skills
                            if item.timing in {"passive", "reaction"}) >= 1)

    def on_before_damage(self, battle: Battle, ctx: DamageContext) -> None:
        owner = self.owner
        if (owner is None or ctx.source is not owner or ctx.target is owner
                or ctx.target.player_id == owner.player_id
                or not ("attack" in ctx.tags or ctx.is_skill)):
            return
        if not any(isinstance(status, ShadowPassiveLimitStatus)
                   and status.source_unit_id == owner.unit_id for status in ctx.target.statuses):
            ctx.target.add_status(ShadowPassiveLimitStatus(owner.unit_id), source=owner)


class ShadowSummonedBurnStatus(StatusEffect):
    def __init__(self) -> None:
        super().__init__("永火王召唤余焰", "召唤后的2本人轮，敌方各在其本人轮末血-1/4。",
                         duration=2, tick_scope="owner_turn_end")


class ShadowSummonedBurnField(BattleFieldEffect):
    def __init__(self, source_unit_id: str) -> None:
        super().__init__("永火王召唤余焰", "召唤后的2轮敌方轮末失血。")
        self.source_unit_id = source_unit_id

    def on_any_turn_end(self, battle: Battle, ended_player_id: int) -> None:
        owner = battle.units.get(self.source_unit_id)
        active = battle.current_turn_unit()
        if (not _present(owner) or not owner.has_status("永火王召唤余焰")
                or active is None or active.player_id == owner.player_id or not _present(active)):
            return
        battle.resolve_damage(DamageContext(
            source=owner, target=active, attack_power=0, raw_damage=active.max_health / 4,
            is_skill=False, action_name="永火王召唤余焰", tags={"shadow_summoned_burn"},
        ))


class ShadowSummonedBurnTrait(Trait):
    def __init__(self) -> None:
        super().__init__("召唤后余焰", "本人被召唤后2轮敌方单位本人轮末失血1/4。")
        self.field: ShadowSummonedBurnField | None = None

    def on_restored_by_apocalypse(self, battle: Battle) -> None:
        owner = self.owner
        if owner is None:
            return
        owner.add_status(ShadowSummonedBurnStatus(), source=owner)
        self.field = ShadowSummonedBurnField(owner.unit_id)
        battle.add_field_effect(self.field, source=owner)

    def on_owner_removed(self, battle: Battle) -> None:
        if self.field in battle.field_effects:
            battle.remove_field_effect(self.field)
        self.field = None
