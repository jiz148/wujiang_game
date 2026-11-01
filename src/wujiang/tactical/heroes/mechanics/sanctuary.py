"""Sky sanctuary, healing, banishment, and judgment objects."""

from __future__ import annotations
from wujiang.tactical.heroes.ai_impact_rule import R12_IMPACT

from typing import Any
from wujiang.tactical.engine.core import Battle
from wujiang.tactical.engine.core import BattleFieldEffect
from wujiang.tactical.engine.core import DamageContext
from wujiang.tactical.engine.core import HealContext
from wujiang.tactical.engine.core import HeroUnit
from wujiang.tactical.engine.core import Position
from wujiang.tactical.engine.core import Skill
from wujiang.tactical.engine.core import StatusEffect
from wujiang.tactical.engine.core import Trait
from wujiang.tactical.heroes.ai_policy import AISkillScoreContext
from wujiang.tactical.heroes.common import ensure_ally
from wujiang.tactical.heroes.common import ensure_distance
from wujiang.tactical.heroes.common import payload_target_unit
from wujiang.tactical.heroes.mechanics.hell_slash import HellSlashSkill
from wujiang.tactical.heroes.mechanics.racial_weather import RacialWeatherEffect
from wujiang.tactical.heroes.mechanics.next_five_effects import apply_piercing_status_effect
from wujiang.tactical.heroes.mechanics.next_five_geometry import square_around_cells


class VitalityBlastSkill(HellSlashSkill):
    length = 5

    def ai_priority_score(self, context: AISkillScoreContext) -> float:
        return context.r18_effect_score(self)

    def __init__(self) -> None:
        Skill.__init__(self, "vitality_blast", "元气爆破",
                       "大招：0魔、2轮一次；本人实际处于天空圣域时使用，身前5格双方当前攻普通技能伤害。",
                       cooldown_turns=2, target_mode="cell")

    def can_use(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any] | None = None) -> tuple[bool, str]:
        ok, reason = super().can_use(battle, actor, payload)
        if not ok:
            return ok, reason
        if not battle.unit_in_weather("天空圣域", actor):
            return False, "需要处于“天空的圣域”天气中。"
        return True, ""

    def patterns(self, battle: Battle, actor: HeroUnit) -> list[list[Position]]:
        body = set(battle.unit_cells(actor))
        return [cells for cells in super().patterns(battle, actor) if not body.intersection(cells)]

    def execute(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> None:
        if not battle.unit_in_weather("天空圣域", actor):
            battle.log("【元气爆破】结算时施法者已不在天空圣域中，动作落空。")
            return
        super().execute(battle, actor, payload)


class SkySanctuaryAuraEffect(RacialWeatherEffect):
    weather_name = "天空圣域"

    def __init__(self, source_unit_id: str) -> None:
        self.source_unit_id = source_unit_id
        super().__init__("天空圣域", "制裁者周围11*11具有完整天空圣域效果：双方天使及原召唤链魔上限+1、各单位自己的行动槽开始血+1/4和当前魔+1、免破魔/半破魔攻击或技能的对应段。", duration=None)

    def merge_into_existing(self, battle: Battle, existing_effects: list[BattleFieldEffect]) -> bool:
        for effect in existing_effects:
            if isinstance(effect, SkySanctuaryAuraEffect) and effect.source_unit_id == self.source_unit_id:
                return True
        return False

    def _source(self, battle: Battle) -> HeroUnit | None:
        source = battle.units.get(self.source_unit_id)
        if source is None or not source.alive or source.position is None or source.banished:
            return None
        return source  # type: ignore[return-value]

    def affected_cells(self, battle: Battle) -> list[Position]:
        source = self._source(battle)
        if source is None:
            return []
        return square_around_cells(battle, battle.unit_cells(source), radius=5)

    def board_marker(self, battle: Battle) -> str:
        return "圣"

    def on_turn_start(self, battle: Battle, active_unit: HeroUnit | None) -> None:
        source = battle.units.get(self.source_unit_id)
        if source is None or not source.alive:
            battle.remove_field_effect(self)
            return
        super().on_turn_start(battle, active_unit)


class SkySanctuaryAuraTrait(Trait):
    def __init__(self) -> None:
        super().__init__("天空圣域光环", "周围 11*11 天气变为“天空圣域”：双方天使武将及原召唤链魔上限+1，每个自己的行动槽开始血+1/4、当前魔+1，免破魔及半破魔攻击或技能对应段。")

    def _ensure_aura(self, battle: Battle) -> None:
        owner = self.owner
        if owner is not None:
            battle.add_field_effect(SkySanctuaryAuraEffect(owner.unit_id))

    def on_enter_battle(self, battle: Battle) -> None:
        self._ensure_aura(battle)

    def on_owner_turn_start(self, battle: Battle) -> None:
        self._ensure_aura(battle)

    def on_owner_removed(self, battle: Battle) -> None:
        owner = self.owner
        if owner is None:
            return
        for effect in list(battle.field_effects):
            if isinstance(effect, SkySanctuaryAuraEffect) and effect.source_unit_id == owner.unit_id:
                battle.remove_field_effect(effect)


class PunisherHealSkill(Skill):
    def ai_priority_score(self, context: AISkillScoreContext) -> float:
        return context.r12_effect_score(self)

    def __init__(self) -> None:
        super().__init__(
            "punisher_heal",
            "治疗",
            "普通技能：费 1 魔，每回合最多 1 次，可对包括自己在内的己方单位使用；目标血 +1/4，魔 +1。",
            mana_cost=1,
            max_uses_per_turn=1,
            target_mode="ally",
        )

    def execute(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> None:
        target = payload_target_unit(battle, payload)
        ensure_ally(actor, target)
        if not battle.is_declared_resolution(actor, payload, {"skill"}):
            ensure_distance(actor, target, actor.targeting_range())
        target = battle.effect_recipient(target)
        battle.heal(HealContext(source=actor, target=target, amount=0.25, action_name="治疗"))
        gained = target.gain_mana(1) if target.alive and target.unit_id in battle.units else 0
        if gained:
            battle.log(f"{target.name} 因【治疗】获得 {gained} 点魔。")

    def preview(self, battle: Battle, actor: HeroUnit) -> dict[str, Any]:
        targets = [
            unit
            for unit in battle.player_units(actor.player_id)
            if unit.position is not None
            and actor.position is not None
            and battle.distance_between_units(actor, unit) <= actor.targeting_range()
        ]
        cells = [unit.position.to_dict() for unit in targets if unit.position is not None]
        return {"cells": cells, "target_unit_ids": [unit.unit_id for unit in targets], "secondary_cells": [], "requires_target": True}


class SanctuaryBanishStatus(StatusEffect):
    def __init__(self) -> None:
        super().__init__("圣殿放逐", "无法攻击，不能使用主动技能。", duration=1, tick_scope="owner_turn_end")
        self.flag_name = "cannot_attack"
        self.locked_flags = ("cannot_attack",)

    def bind(self, owner: HeroUnit) -> "SanctuaryBanishStatus":
        super().bind(owner)
        owner.cannot_attack = True
        return self

    def blocks_skill_use(self, battle: Battle, actor: HeroUnit, skill: Skill) -> tuple[bool, str]:
        owner = self.owner
        if owner is not None and actor.unit_id == owner.unit_id and skill.timing == "active":
            return True, "圣殿放逐状态下不能使用主动技能。"
        return False, ""

    def on_removed(self, battle: Battle) -> None:
        owner = self.owner
        if owner is None:
            return
        owner.cannot_attack = owner.is_clone or any(
            getattr(status, "flag_name", "") in {"cannot_attack", "cannot_act"}
            or "cannot_attack" in getattr(status, "locked_flags", ())
            or "cannot_attack" in getattr(status, "provided_flags", ())
            for status in owner.statuses
        )


class SanctuaryBanishSkill(Skill):
    ai_effective_impact_rule = R12_IMPACT
    def ai_priority_score(self, context: AISkillScoreContext) -> float:
        return context.r12_effect_score(self)

    def __init__(self) -> None:
        super().__init__(
            "sanctuary_banish",
            "圣殿放逐",
            "普通技能：3 轮一次；对所有处于“天空圣域”中的敌方单位施加破魔效果，直到下回合结束前无法攻击且不能使用主动技能。",
            cooldown_turns=3,
            target_mode="self",
        )

    def targets(self, battle: Battle, actor: HeroUnit) -> list[HeroUnit]:
        return [
            unit  # type: ignore[list-item]
            for unit in battle.effect_units(battle.enemy_units(actor.player_id))
            if battle.unit_in_weather("天空圣域", unit)
        ]

    def execute(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> None:
        for target in self.targets(battle, actor):
            apply_piercing_status_effect(
                battle,
                actor,
                target,
                action_name="圣殿放逐",
                status=SanctuaryBanishStatus(),
                is_skill=True,
                tags={"skill", "sanctuary_banish"},
            )

    def preview(self, battle: Battle, actor: HeroUnit) -> dict[str, Any]:
        targets = self.targets(battle, actor)
        cells = [cell.to_dict() for target in targets for cell in battle.unit_cells(target)]
        return {"cells": cells, "target_unit_ids": [unit.unit_id for unit in targets], "secondary_cells": [], "requires_target": False}

    def ignores_shield_for_payload(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> bool:
        return True


class SanctuaryJudgmentSkill(Skill):
    ai_effective_impact_rule = R12_IMPACT
    def ai_priority_score(self, context: AISkillScoreContext) -> float:
        return context.r12_effect_score(self)

    def __init__(self) -> None:
        super().__init__(
            "sanctuary_judgment",
            "制裁",
            "大招：一场战斗一次；所有处于“天空圣域”中的敌方单位受到 5 次技能伤害，每次伤害值等于该单位当前攻击。",
            max_uses_per_battle=1,
            target_mode="self",
        )

    def targets(self, battle: Battle, actor: HeroUnit) -> list[HeroUnit]:
        return [
            unit  # type: ignore[list-item]
            for unit in battle.effect_units(battle.enemy_units(actor.player_id))
            if battle.unit_in_weather("天空圣域", unit)
        ]

    def execute(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> None:
        for target in self.targets(battle, actor):
            for _ in range(5):
                if not target.alive or not battle.unit_in_weather("天空圣域", target):
                    break
                battle.resolve_damage(
                    DamageContext(
                        source=actor,
                        target=target,
                        attack_power=target.stat("attack"),
                        is_skill=True,
                        action_name="制裁",
                        tags={"skill", "sanctuary_judgment"},
                    )
                )

    def preview(self, battle: Battle, actor: HeroUnit) -> dict[str, Any]:
        targets = self.targets(battle, actor)
        cells = [cell.to_dict() for target in targets for cell in battle.unit_cells(target)]
        return {"cells": cells, "target_unit_ids": [unit.unit_id for unit in targets], "secondary_cells": [], "requires_target": False}


