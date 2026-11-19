"""R361 Astrasa: dynamic magical exclusion, large breath and defensive wall."""

from __future__ import annotations

from typing import Any

from wujiang.tactical.engine.core import (
    ActionError, Battle, BattleFieldEffect, DamageContext, Position, Skill,
    StatusEffect, TargetContext, Trait, Unit,
)
from wujiang.tactical.heroes.ai_candidate_plan import SkillCandidatePlan
from wujiang.tactical.heroes.common import positions_to_dict
from wujiang.tactical.heroes.mechanics.dragon_breath import RemoteDragonBreathSkill
from wujiang.tactical.heroes.mechanics.next_five_effects import remote_rectangle_patterns


def _present(unit: Unit | None) -> bool:
    return bool(unit is not None and unit.alive and not unit.banished and unit.position is not None)


def _near(battle: Battle, source: Unit, target: Unit, radius: int) -> bool:
    return _present(source) and _present(target) and battle.distance_between_units(source, target) <= radius


class AstrasaLargeBreathSkill(RemoteDragonBreathSkill):
    ai_area_bonus_codes = frozenset({"remote_dragon_breath_large"})
    ai_payload_value_owned = True
    ai_candidate_plan = SkillCandidatePlan(prefilter_limit=24)

    def __init__(self) -> None:
        super().__init__()
        self.code = "remote_dragon_breath_large"
        self.name = "远程龙息（大）"
        self.description = "费2魔，每本人轮最多2次，范内选3×3区域，按当前攻造成普通技能伤。"

    def patterns(self, battle: Battle, actor: Unit) -> list[list[Position]]:
        return remote_rectangle_patterns(battle, actor, 3, 3)

    def _area_value(self, battle: Battle, actor: Unit, cells: list[Position]) -> float:
        score = -12.0
        for target in battle.effect_units_at_cells(cells, ignore=actor):
            if target.magic_immunity:
                continue
            if target.total_shields() > 0:
                impact = 18.0
            else:
                damage = min(target.current_hp, battle.damage_rule.calculate_damage(
                    actor.stat("attack") + max(0, battle.unit_hit_count_for_cells(target, cells) - 1),
                    target.stat("defense")))
                impact = damage * 90.0 + (50.0 if damage >= target.current_hp and damage > 0 else 0.0)
            score += impact if target.player_id != actor.player_id else -impact * 1.5
        return score

    def ai_prefilter_score(self, battle: Battle, actor: Unit,
                           payload: dict[str, Any], shared: Any) -> float:
        return self._area_value(battle, actor, battle.payload_positions(payload, "cells"))

    def ai_payload_value(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> float:
        try:
            cells = self.chosen_cells(battle, actor, payload)
        except (ActionError, ValueError, TypeError, KeyError):
            return -1000.0
        return self._area_value(battle, actor, cells)

    def ai_effective_enemy_impact(self, battle: Battle, actor: Unit, payload: dict[str, Any],
                                  targets: list[Unit] | None = None, shared: Any = None,
                                  action: dict[str, Any] | None = None) -> bool:
        return self.ai_payload_value(battle, actor, payload) > 0

    def ai_priority_score(self, context: Any) -> float:
        return self.ai_payload_value(context.battle, context.actor, context.payload)


class AstrasaDivineBlastSkill(RemoteDragonBreathSkill):
    ai_payload_value_owned = True
    ai_candidate_plan = SkillCandidatePlan(prefilter_limit=24)

    def __init__(self) -> None:
        super().__init__()
        self.code = "astrasa_divine_blast"
        self.name = "神力爆破"
        self.description = "费2.5魔，本人轮一次，范内选5×5区域，对双方实际受体造成当前攻的破魔技能伤。"
        self.mana_cost = 2.5
        self.max_uses_per_turn = 1

    def patterns(self, battle: Battle, actor: Unit) -> list[list[Position]]:
        return remote_rectangle_patterns(battle, actor, 5, 5)

    def queued_payload_metadata(self, battle: Battle, actor: Unit,
                                payload: dict[str, Any]) -> dict[str, Any]:
        return {**super().queued_payload_metadata(battle, actor, payload), "ignore_shield": True}

    def ignores_shield_for_payload(self, battle: Battle, actor: Unit,
                                   payload: dict[str, Any]) -> bool:
        return True

    def execute(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> None:
        cells = (battle.payload_positions(payload, "declared_area_cells")
                 if "declared_area_cells" in payload else self.chosen_cells(battle, actor, payload))
        for unit in battle.effect_units_at_cells(cells, ignore=actor):
            battle.resolve_damage(DamageContext(
                source=actor, target=unit, attack_power=actor.stat("attack"),
                is_skill=True, ignore_shield=True, action_name=self.name,
                area_cell_hits=battle.unit_hit_count_for_cells(unit, cells),
                tags={"skill", self.code},
            ))

    def _area_value(self, battle: Battle, actor: Unit, cells: list[Position]) -> float:
        score = -18.0
        for target in battle.effect_units_at_cells(cells, ignore=actor):
            if target.magic_immunity:
                continue
            damage = min(target.current_hp, battle.damage_rule.calculate_damage(
                actor.stat("attack") + max(0, battle.unit_hit_count_for_cells(target, cells) - 1),
                target.stat("defense")))
            impact = damage * 90.0 + (50.0 if damage >= target.current_hp and damage > 0 else 0.0)
            score += impact if target.player_id != actor.player_id else -impact * 1.5
        return score

    def ai_prefilter_score(self, battle: Battle, actor: Unit,
                           payload: dict[str, Any], shared: Any) -> float:
        return self.ai_payload_value(battle, actor, payload)

    def ai_payload_value(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> float:
        try:
            cells = self.chosen_cells(battle, actor, payload)
        except (ActionError, ValueError, TypeError, KeyError):
            return -1000.0
        return self._area_value(battle, actor, cells)

    def ai_effective_enemy_impact(self, battle: Battle, actor: Unit, payload: dict[str, Any],
                                  targets: list[Unit] | None = None, shared: Any = None,
                                  action: dict[str, Any] | None = None) -> bool:
        return self.ai_payload_value(battle, actor, payload) > 0

    def ai_priority_score(self, context: Any) -> float:
        return self.ai_payload_value(context.battle, context.actor, context.payload)


class AstrasaGreatManaTrait(Trait):
    def __init__(self) -> None:
        super().__init__("自然大回魔", "本人轮开始自然回复2魔，不超过正常上限。")

    def on_owner_turn_start(self, battle: Battle) -> None:
        if _present(self.owner):
            gained = self.owner.gain_mana(2.0)
            if gained:
                battle.log(f"{self.owner.name} 自然大回魔，获得 {gained:g} 魔。")


class AstrasaAreaAttackTrait(Trait):
    def __init__(self) -> None:
        super().__init__("魔导广域普攻", "每次普攻可改选本人周围5×5，命中除本人外双方实际受体。")

    def basic_attack_action_entries(self, battle: Battle, actor: Unit) -> list[dict[str, Any]]:
        return [{"code": "astrasa_area_attack", "name": "广域普攻",
                 "description": "消耗一次普攻，攻击本人周围5×5的双方单位。",
                 "attack_payload": {"attack_variant": "astrasa_area"}, "is_attack_variant": True}]

    def ai_basic_attack_variant_payload(self, battle: Battle, actor: Unit,
                                        snapshot: dict[str, Any]) -> dict[str, Any] | None:
        action = next((item for item in snapshot.get("actions", [])
                       if item.get("code") == "astrasa_area_attack" and item.get("available")), None)
        if action is None:
            return None
        cells = self.basic_attack_area_cells(battle, actor, {"attack_variant": "astrasa_area"}) or []
        affected = battle.effect_units_at_cells(cells, ignore=actor)
        enemies = [unit for unit in affected if unit.player_id != actor.player_id
                   and not unit.physical_immunity and unit.total_shields() <= 0]
        if len(enemies) < 2:
            return None
        def value(unit: Unit) -> float:
            return min(unit.current_hp, battle.damage_rule.calculate_damage(
                actor.stat("attack"), unit.stat("defense")))
        enemy_gain = sum(value(unit) for unit in enemies)
        ally_loss = sum(value(unit) for unit in affected if unit.player_id == actor.player_id)
        if enemy_gain - ally_loss * 1.5 <= max(value(unit) for unit in enemies) + 0.05:
            return None
        return {"type": "attack", "unit_id": actor.unit_id,
                "attack_variant": "astrasa_area", "target_unit_id": enemies[0].unit_id}

    def basic_attack_area_cells(self, battle: Battle, actor: Unit,
                                payload: dict[str, Any] | None = None) -> list[Position] | None:
        if (payload or {}).get("attack_variant") != "astrasa_area":
            return None
        if actor.position is None:
            return []
        body = set(battle.unit_cells(actor))
        return [Position(x, y) for y in range(battle.height) for x in range(battle.width)
                if Position(x, y) not in body and any(
                    max(abs(x - origin.x), abs(y - origin.y)) <= 2 for origin in body)]

    def basic_attack_payload_metadata(self, battle: Battle, actor: Unit,
                                      payload: dict[str, Any] | None = None) -> dict[str, Any]:
        cells = self.basic_attack_area_cells(battle, actor, payload)
        return ({"attack_cells": positions_to_dict(cells), "area_attack": True,
                 "single_hit_per_recipient": True, "attack_name": "广域普攻"} if cells is not None else {})

    def basic_attack_preview(self, battle: Battle, actor: Unit,
                             payload: dict[str, Any] | None = None) -> dict[str, Any] | None:
        cells = self.basic_attack_area_cells(battle, actor, payload)
        if cells is None:
            return None
        targets = battle.effect_units_at_cells(cells, ignore=actor)
        return {"cells": positions_to_dict(cells),
                "target_unit_ids": [target.unit_id for target in targets],
                "secondary_cells": [], "requires_target": True,
                "selection": {"mode": "pattern_cells",
                              "patterns": [[cell.to_dict()] for cell in cells]}}


class AstrasaSacredArrayStatus(StatusEffect):
    def __init__(self) -> None:
        super().__init__("神圣魔导阵", "周围7×7的敌方单位不能使用主动技能，持续四本人轮。",
                         duration=4, tick_scope="owner_turn_end")


class AstrasaExclusionField(BattleFieldEffect):
    ignores_direct_effect_immunity = True

    def __init__(self, source: Unit) -> None:
        super().__init__("大魔导封锁", "随本体移动：近处敌方禁普攻；魔导阵开启时更大范围禁主动技。")
        self.source_unit_id = source.unit_id

    def _source(self, battle: Battle) -> Unit | None:
        return battle.units.get(self.source_unit_id)

    def can_attack_target(self, battle: Battle, actor: Unit, target: Unit) -> tuple[bool, str]:
        source = self._source(battle)
        if source is not None and actor.player_id != source.player_id and _near(battle, source, actor, 2):
            return False, f"{actor.name} 在【大魔导封锁】内不能普攻。"
        return True, ""

    def blocks_skill_use(self, battle: Battle, actor: Unit, skill: Skill) -> tuple[bool, str]:
        source = self._source(battle)
        if (source is not None and actor.player_id != source.player_id
                and skill.timing == "active" and source.has_status("神圣魔导阵")
                and _near(battle, source, actor, 3)):
            return True, f"{actor.name} 在【神圣魔导阵】内不能使用主动技能。"
        return False, ""


class AstrasaExclusionTrait(Trait):
    def __init__(self) -> None:
        super().__init__("近身禁攻", "本人周围5×5的敌方单位不能普攻。")

    def on_enter_battle(self, battle: Battle) -> None:
        if self.owner is not None and not any(
            isinstance(effect, AstrasaExclusionField) and effect.source_unit_id == self.owner.unit_id
            for effect in battle.field_effects
        ):
            battle.add_field_effect(AstrasaExclusionField(self.owner), source=self.owner)

    def on_owner_removed(self, battle: Battle) -> None:
        if self.owner is not None:
            for effect in list(battle.field_effects):
                if isinstance(effect, AstrasaExclusionField) and effect.source_unit_id == self.owner.unit_id:
                    battle.remove_field_effect(effect)


class AstrasaSacredArraySkill(Skill):
    ai_payload_value_owned = True

    def __init__(self) -> None:
        super().__init__("astrasa_sacred_array", "神圣魔导阵",
                         "大招：本场一次；四本人轮内使本人周围7×7的敌方单位不能使用主动技能。",
                         max_uses_per_battle=1, target_mode="self")

    def execute(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> None:
        actor.add_status(AstrasaSacredArrayStatus(), source=actor)
        battle.log(f"{actor.name} 张开【神圣魔导阵】，周围7×7禁用敌方主动技能。")

    def ai_requires_enemy_impact(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> bool:
        return False

    def ai_payload_value(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> float:
        if actor.has_status("神圣魔导阵"):
            return -100.0
        value = sum(32.0 + min(2, sum(skill.timing == "active" for skill in enemy.skills)) * 21.0
                    for enemy in battle.enemy_units(actor.player_id)
                    if _near(battle, actor, enemy, 3) and any(skill.timing == "active" for skill in enemy.skills))
        return value - 35.0

    def ai_payload_candidates(self, battle: Battle, actor: Unit,
                              action: dict[str, Any]) -> list[dict[str, Any]]:
        return ([{"type": "skill", "unit_id": actor.unit_id, "skill_code": self.code}]
                if self.ai_payload_value(battle, actor, {}) > 0 else [])

    def ai_priority_score(self, context: Any) -> float:
        return self.ai_payload_value(context.battle, context.actor, context.payload)


class AstrasaMageWallStatus(StatusEffect):
    def __init__(self) -> None:
        super().__init__("魔法使之墙", "挡一次针对本人的普攻或技能及其效果，破魔不能穿过。")
        self.blocked_token: Any = None

    def _block(self, battle: Battle, ctx: TargetContext | DamageContext) -> None:
        owner = self.owner
        source = ctx.actor if isinstance(ctx, TargetContext) else ctx.source
        if (owner is None or ctx.target is not owner or source is None
                or source.player_id == owner.player_id or ctx.from_field_effect
                or not (ctx.is_skill or "attack" in ctx.tags) or ctx.cancelled):
            return
        token = battle.current_action_resolution_token
        if self.blocked_token is not None and self.blocked_token != token:
            return
        first = self.blocked_token is None
        ctx.cancelled = True
        ctx.reason = f"{owner.name} 的【魔法使之墙】挡住了【{ctx.action_name}】。"
        if token is None:
            owner.remove_status(self, battle)
        else:
            self.blocked_token = token
            self.expires_after_action_token = token
        if first:
            battle.emit_defense_visual_event(source=source, target=owner,
                                            action_name=ctx.action_name, defense_reason="block")

    def on_targeted(self, battle: Battle, ctx: TargetContext) -> None:
        self._block(battle, ctx)

    def on_before_damage(self, battle: Battle, ctx: DamageContext) -> None:
        self._block(battle, ctx)


class AstrasaMageWallSkill(Skill):
    ai_reaction_score_phase = "after_hidden_source"

    def __init__(self) -> None:
        super().__init__("astrasa_mage_wall", "魔法使之墙",
                         "费1魔；被动连锁，每敌方武将回合最多一次，只保护本人且无视破魔。",
                         mana_cost=1, max_uses_per_turn=1, target_mode="self", timing="passive")

    def can_react_to(self, battle: Battle, actor: Unit, queued_action: Any) -> tuple[bool, str]:
        ok, reason = super().can_react_to(battle, actor, queued_action)
        if not ok:
            return ok, reason
        if queued_action.source_player_id == actor.player_id or queued_action.action_type not in {
            "attack", "skill", "skill_effect",
        }:
            return False, "只在敌方普攻或技能将影响本人时使用。"
        threatened = battle.effect_units_at_cells(queued_action.target_cells)
        if not threatened:
            threatened = battle.effect_units([
                battle.units[uid] for uid in queued_action.target_unit_ids if uid in battle.units])
        if actor not in threatened or actor.has_status("魔法使之墙"):
            return False, "当前本人不会受到需要挡住的效果。"
        return True, ""

    def react(self, battle: Battle, actor: Unit,
              payload: dict[str, Any], queued_action: Any) -> None:
        if not _present(actor):
            return
        actor.add_status(AstrasaMageWallStatus(), source=actor)
        battle.log(f"{actor.name} 张开【魔法使之墙】。")

    def execute(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> None:
        raise ActionError("魔法使之墙只能通过连锁使用。")

    def reaction_preview(self, battle: Battle, actor: Unit, queued_action: Any) -> dict[str, Any]:
        return {"cells": positions_to_dict(battle.unit_cells(actor)),
                "target_unit_ids": [actor.unit_id], "secondary_cells": [], "requires_target": False}

    def ai_reaction_score(self, context: Any) -> float:
        if context.actor.unit_id in context.shield_protected_ids():
            return -1000.0
        threat = context.threat(context.actor)
        return threat * 1.2 - 12.0 if threat > 0 else -1000.0
