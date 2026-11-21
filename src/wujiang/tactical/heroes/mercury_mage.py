"""R133: mercury summons, local displacement denial and one-use fixation."""

from __future__ import annotations

from typing import Any

from wujiang.tactical.engine.core import (
    ActionError, Battle, BattleFieldEffect, DamageContext, HealContext, Position,
    Skill, Stats, Trait, Unit,
)
from wujiang.tactical.heroes.base import AbstractHero
from wujiang.tactical.heroes.common import (
    FlagStatus, ShensuSkill, payload_position, payload_target_unit,
    positions_to_dict,
)
from wujiang.tactical.heroes.mechanics.next_five_geometry import square_around_cells
from wujiang.tactical.heroes.mechanics.piercing_control import piercing_nondamage_recipient


def _present(unit: Unit | None) -> bool:
    return unit is not None and unit.alive and not unit.banished and unit.position is not None


class MercuryShensuSkill(ShensuSkill):
    def __init__(self) -> None:
        super().__init__()
        self.mana_cost = 0
        self.description = "每所属回合一次；水银无魔，免魔使下次普通移动格数+3。"


class MercurySkillDamageGuard(Trait):
    def __init__(self) -> None:
        super().__init__("水银技能伤害免疫", "受到技能伤害时防止该伤害，随后回满血。")

    def on_final_hp_loss(self, battle: Battle, ctx: DamageContext) -> None:
        owner = self.owner
        if (owner is None or ctx.target is not owner or not ctx.is_skill or ctx.cancelled
                or ctx.raw_damage is None or ctx.raw_damage <= 0):
            return
        ctx.raw_damage = 0.0
        ctx.preserve_followup_effects = True
        ctx.reason = f"{owner.name} 防止技能伤害。"
        battle.heal(HealContext(source=owner, target=owner,
                                amount=max(0.0, owner.max_health - owner.current_hp),
                                action_name="水银技能伤害后回满"))
        battle.log_public_event(ctx.reason, source=ctx.source, target=owner)


class MercuryMobilityField(BattleFieldEffect):
    def __init__(self, mercury_id: str) -> None:
        super().__init__("水银禁位移", "水银外邻单位不能使用会移动使用者本人的位移技能。")
        self.mercury_id = mercury_id

    def mercury(self, battle: Battle) -> Unit | None:
        unit = battle.units.get(self.mercury_id)
        return unit if _present(unit) else None

    def affected_cells(self, battle: Battle) -> list[Position]:
        mercury = self.mercury(battle)
        if mercury is None:
            return []
        body = set(battle.unit_cells(mercury))
        return sorted(set(square_around_cells(battle, body, 1)) - body,
                      key=lambda cell: (cell.y, cell.x))

    def blocks_skill_payload(self, battle: Battle, actor: Unit, skill: Skill,
                             payload: dict[str, Any] | None) -> tuple[bool, str]:
        if (self.mercury(battle) is not None and actor.position is not None
                and any(cell in self.affected_cells(battle) for cell in battle.unit_cells(actor))
                and skill.moves_owner_on_use(battle, actor, payload)):
            return True, "水银周围不能使用位移技能。"
        return False, ""


class MercuryMobilityTrait(Trait):
    def __init__(self) -> None:
        super().__init__("水银周围禁位移", "外邻双方单位不能使用会移动自身的位移技能。")
        self.field: MercuryMobilityField | None = None

    def on_enter_battle(self, battle: Battle) -> None:
        if self.owner is None or self.field in battle.field_effects:
            return
        self.field = MercuryMobilityField(self.owner.unit_id)
        battle.add_field_effect(self.field, source=self.owner)

    def on_owner_removed(self, battle: Battle) -> None:
        if self.field in battle.field_effects:
            battle.remove_field_effect(self.field)
        self.field = None


class MercuryUnit(AbstractHero):
    hero_code = "mercury_r133"
    hero_name = "水银"
    role = "召唤物"
    attribute = "钢"
    race = "人类"
    level = 1
    base_stats = Stats(attack=3, defense=5, speed=2, attack_range=1, mana=0)
    raw_skill_text = "每回合可使用一次神速"
    raw_trait_text = "不受到技能伤害，受到技能伤害时血全满；周围单位不能使用位移技能"

    def __init__(self, player_id: int) -> None:
        super().__init__(player_id, is_summon=True)

    def build_skills(self) -> list[Skill]:
        return [MercuryShensuSkill()]

    def build_traits(self) -> list[Trait]:
        return [MercurySkillDamageGuard(), MercuryMobilityTrait()]


class SummonMercurySkill(Skill):
    ai_payload_value_owned = True
    ai_enemy_impact_requirement = False

    def __init__(self) -> None:
        super().__init__("summon_mercury", "水银",
                         "费1魔，每2本人轮召一只攻3守5速2范1的水银。",
                         mana_cost=1, cooldown_turns=2, target_mode="cell")

    def destinations(self, battle: Battle, actor: Unit) -> list[Position]:
        if actor.position is None:
            return []
        body = set(battle.unit_cells(actor))
        probe = MercuryUnit(actor.player_id)
        return sorted((cell for cell in set(square_around_cells(battle, body, 1)) - body
                       if battle.can_place_unit(probe, cell)),
                      key=lambda cell: (cell.y, cell.x))

    def can_use(self, battle: Battle, actor: Unit,
                payload: dict[str, Any] | None = None) -> tuple[bool, str]:
        ok, reason = super().can_use(battle, actor, payload)
        if not ok:
            return ok, reason
        cells = self.destinations(battle, actor)
        if not cells:
            return False, "水银没有合法外邻落格。"
        if payload is not None and "x" in payload:
            try:
                if payload_position(payload) not in cells:
                    return False, "水银落格不合法。"
            except (ActionError, TypeError, ValueError):
                return False, "水银落格不合法。"
        return True, ""

    def preview(self, battle: Battle, actor: Unit) -> dict[str, Any]:
        return {"cells": positions_to_dict(self.destinations(battle, actor)),
                "target_unit_ids": [], "secondary_cells": [], "requires_target": True}

    def get_target_cells_for_payload(self, battle: Battle, actor: Unit,
                                     payload: dict[str, Any]) -> list[Position]:
        cell = payload_position(payload)
        if cell not in self.destinations(battle, actor):
            raise ActionError("水银落格不合法。")
        return [cell]

    def ai_payload_candidates(self, battle: Battle, actor: Unit,
                              action: dict[str, Any]) -> list[dict[str, Any]]:
        return [{"type": "skill", "unit_id": actor.unit_id, "skill_code": self.code,
                 "x": cell.x, "y": cell.y} for cell in self.destinations(battle, actor)]

    def ai_candidate_payloads(self, context: Any) -> list[dict[str, Any]]:
        return self.ai_payload_candidates(context.battle, context.actor, context.action)

    def ai_payload_value(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> float:
        try:
            cell = self.get_target_cells_for_payload(battle, actor, payload)[0]
        except (ActionError, TypeError, ValueError):
            return -1000.0
        enemies = [unit for unit in battle.enemy_units(actor.player_id) if _present(unit)]
        nearest = min((battle.unit_distance_to_cell(unit, cell) for unit in enemies), default=8)
        protected = min((cell.distance_to(body) for body in battle.unit_cells(actor)), default=8)
        return 36.0 + max(0.0, 5.0 - nearest) * 11.0 + max(0.0, 3.0 - protected) * 4.0

    def ai_priority_score(self, context: Any) -> float:
        return self.ai_payload_value(context.battle, context.actor, context.payload)

    def execute(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> None:
        try:
            cell = self.get_target_cells_for_payload(battle, actor, payload)[0]
            battle.summon_unit(MercuryUnit(actor.player_id), cell, summoner=actor)
        except ActionError:
            battle.log_public_event(f"{actor.name} 的水银落格已失效。", source=actor)


class FixedStatus(FlagStatus):
    def __init__(self) -> None:
        super().__init__("固定", "cannot_attack", duration=2,
                         description="两个本人进攻回合不能普通移动、普攻或使用主动技能。")

    def can_use_normal_move(self, battle: Battle, actor: Unit) -> tuple[bool, str]:
        return (False, "固定期间不能普通移动。") if actor is self.owner else (True, "")

    def blocks_skill_use(self, battle: Battle, actor: Unit, skill: Skill) -> tuple[bool, str]:
        return (True, "固定期间不能使用主动技能。") if actor is self.owner and skill.is_active else (False, "")


class FixateSkill(Skill):
    ai_payload_value_owned = True
    requires_direct_unit_target_line = False

    def __init__(self) -> None:
        super().__init__("fixate", "固定",
                         "本场一次；十格内直线单体破魔，命中后两本人轮不能移动、普攻和使用主动技能。",
                         max_uses_per_battle=1, target_mode="unit")

    def ignores_shield_for_payload(self, battle: Battle, actor: Unit,
                                   payload: dict[str, Any]) -> bool:
        return True

    def targets(self, battle: Battle, actor: Unit) -> list[Unit]:
        return [unit for unit in battle.all_units() if unit is not actor and _present(unit)
                and battle.unit_target_in_range_and_line(actor, unit, 10)]

    def can_use(self, battle: Battle, actor: Unit,
                payload: dict[str, Any] | None = None) -> tuple[bool, str]:
        ok, reason = super().can_use(battle, actor, payload)
        if not ok:
            return ok, reason
        if payload is not None and "target_unit_id" in payload:
            target = battle.units.get(str(payload["target_unit_id"]))
            if target not in self.targets(battle, actor):
                return False, "固定目标不在直线十格内。"
        return True, ""

    def preview(self, battle: Battle, actor: Unit) -> dict[str, Any]:
        targets = self.targets(battle, actor)
        return {"cells": positions_to_dict([cell for unit in targets for cell in battle.unit_cells(unit)]),
                "target_unit_ids": [unit.unit_id for unit in targets],
                "secondary_cells": [], "requires_target": True}

    def ai_payload_candidates(self, battle: Battle, actor: Unit,
                              action: dict[str, Any]) -> list[dict[str, Any]]:
        return [{"type": "skill", "unit_id": actor.unit_id, "skill_code": self.code,
                 "target_unit_id": unit.unit_id} for unit in self.targets(battle, actor)]

    def ai_candidate_payloads(self, context: Any) -> list[dict[str, Any]]:
        return self.ai_payload_candidates(context.battle, context.actor, context.action)

    def ai_effective_enemy_impact(self, battle: Battle, actor: Unit,
                                  payload: dict[str, Any], targets: list[Unit] | None = None,
                                  shared: Any = None, action: dict[str, Any] | None = None) -> bool:
        target = battle.units.get(str(payload.get("target_unit_id") or ""))
        return _present(target) and target.player_id != actor.player_id and not target.has_status("固定")

    def ai_payload_value(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> float:
        target = battle.units.get(str(payload.get("target_unit_id") or ""))
        if target not in self.targets(battle, actor) or target.player_id == actor.player_id:
            return -1000.0
        if target.magic_immunity:
            return -20.0
        if target.has_status("固定"):
            return -30.0
        offense = (0.0 if target.cannot_attack else target.stat("attack") * 12.0)
        skills = 0.0 if target.cannot_use_skills else sum(
            10.0 for skill in target.action_skills() if skill.is_active and skill.cooldown_remaining == 0)
        movement = 0.0 if target.cannot_move else target.stat("speed") * 6.0
        return offense + skills + movement - 20.0

    def ai_priority_score(self, context: Any) -> float:
        return self.ai_payload_value(context.battle, context.actor, context.payload)

    def execute(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> None:
        target = payload_target_unit(battle, payload)
        if target not in self.targets(battle, actor):
            raise ActionError("固定目标不在直线十格内。")
        recipient = piercing_nondamage_recipient(
            battle, actor, target, action_name=self.name, code=self.code,
        )
        if recipient is None:
            return
        recipient.add_status(FixedStatus(), source=actor)
        battle.record_skill_effect(recipient)
        battle.log_public_event(f"{recipient.name} 被固定，两个本人进攻回合不能普通移动、普攻或主动施技。",
                                source=actor, target=recipient)


class MercuryMageRecovery(Trait):
    def __init__(self) -> None:
        super().__init__("水银回魔", "敌方本人回合结束，按其外邻己方水银数量回魔。")

    def on_any_turn_end(self, battle: Battle, ended_player_id: int) -> None:
        owner = self.owner
        ending = battle.current_turn_unit()
        if not _present(owner) or not _present(ending) or ending.player_id == owner.player_id:
            return
        count = sum(1 for mercury in battle.all_units()
                    if isinstance(mercury, MercuryUnit) and _present(mercury)
                    and mercury.player_id == owner.player_id
                    and mercury.summoner_id == owner.unit_id
                    and any(cell in set(square_around_cells(battle, battle.unit_cells(mercury), 1))
                            for cell in battle.unit_cells(ending)))
        if count:
            gained = owner.gain_mana(count)
            battle.log_public_event(f"{owner.name} 因敌方周围 {count} 只水银回魔 {gained:g}。",
                                    source=owner, target=ending)
