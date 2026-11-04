"""Reusable racial and global weather objects."""

from __future__ import annotations

from typing import Any
from wujiang.tactical.heroes.ai_policy import AISkillScoreContext
from wujiang.tactical.engine.army import is_army_soldier
from wujiang.tactical.engine.core import Battle
from wujiang.tactical.engine.core import BattleFieldEffect
from wujiang.tactical.engine.core import DamageContext
from wujiang.tactical.engine.core import HealContext
from wujiang.tactical.engine.core import HeroUnit
from wujiang.tactical.engine.core import Skill
from wujiang.tactical.engine.core import StatusEffect
from wujiang.tactical.engine.core import TargetContext
from wujiang.tactical.engine.core import Trait
from wujiang.tactical.prediction import random


def weather_race_eligible(unit: HeroUnit, race: str) -> bool:
    return getattr(unit, "original_weather_race", unit.race) == race


class WeatherExtraAttackStatus(StatusEffect):
    def __init__(self) -> None:
        super().__init__("万魔殿追加攻击", "本全局回合内增加的普攻次数；回合末清除。", duration=1, tick_scope="any_turn_end")
        self.extra_attacks = 1

    def modify_attack_actions_per_turn(self, value: int) -> int:
        return value + self.extra_attacks


class RacialWeatherEffect(BattleFieldEffect):
    def eligible(self, battle: Battle, unit: HeroUnit, name: str) -> bool:
        return (battle.canonical_weather_name(getattr(self, "weather_name", "")) == name
                and unit.alive and unit.position is not None and not unit.banished
                and weather_race_eligible(unit, "恶魔" if name == "万魔殿" else "天使")
                and battle.weather_effect_applies(self, unit))

    def modify_unit_stat(self, battle: Battle, unit: HeroUnit, stat_name: str, value: float) -> float:
        if unit.direct_effects_blocked():
            return value
        if stat_name == "attack" and self.eligible(battle, unit, "万魔殿"):
            return value + 1
        if stat_name == "mana" and self.eligible(battle, unit, "天空圣域"):
            return value + 1
        return value

    def on_basic_attack_finished(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any], damage_contexts: list[DamageContext], missed: bool) -> None:
        if not self.eligible(battle, actor, "万魔殿") or actor.direct_effects_blocked():
            return
        if getattr(battle, "_forecasting_action", False):
            return
        gained = random.random() < 0.5
        if gained:
            status = actor.get_status("万魔殿追加攻击")
            if status is None:
                actor.add_status(WeatherExtraAttackStatus())
            else:
                status.extra_attacks += 1
        battle.log_public_event(f"{actor.name} 的万魔殿判定：{'追加1次普攻' if gained else '不追加普攻'}。", source=actor)

    def on_turn_start(self, battle: Battle, active_unit: HeroUnit | None) -> None:
        if active_unit is None:
            return
        for unit in battle.effect_units(battle.turn_bundle_units(active_unit, include_banished=False)):
            if not self.eligible(battle, unit, "天空圣域"):
                continue
            if getattr(unit, "_sky_weather_opening_turn", None) == battle.turn_number:
                continue
            unit._sky_weather_opening_turn = battle.turn_number
            battle.heal(HealContext(source=None, target=unit, amount=0.25, action_name="天空圣域补给", effect_source=self))
            gained = unit.gain_mana(1)
            if gained > 0:
                battle.log_public_event(f"{unit.name} 受到天空圣域补给，魔 +{gained}。", target=unit)

    def protects_piercing(self, battle: Battle, target: HeroUnit, *, is_skill: bool, tags: set[str], ignore_shield: bool, half_ignore_shield: bool, from_field_effect: bool) -> bool:
        return (not from_field_effect and (is_skill or bool(tags & {"attack", "counter"}))
                and (ignore_shield or half_ignore_shield) and self.eligible(battle, target, "天空圣域"))

    def on_after_damage_modifiers(self, battle: Battle, ctx: DamageContext) -> None:
        if self.protects_piercing(battle, ctx.target, is_skill=ctx.is_skill, tags=ctx.tags,
                                 ignore_shield=ctx.ignore_shield, half_ignore_shield=ctx.half_ignore_shield, from_field_effect=ctx.from_field_effect):
            ctx.cancelled = True
            ctx.preserve_followup_effects = False
            ctx.reason = "天空圣域免疫破魔攻击或技能。"

    def on_after_target_modifiers(self, battle: Battle, ctx: TargetContext) -> None:
        if self.protects_piercing(battle, ctx.target, is_skill=ctx.is_skill, tags=ctx.tags,
                                 ignore_shield=ctx.ignore_shield, half_ignore_shield=ctx.half_ignore_shield, from_field_effect=ctx.from_field_effect):
            ctx.cancelled = True
            ctx.reason = "天空圣域免疫破魔攻击或技能。"

    def blocks_action_effect(self, battle: Battle, unit: HeroUnit, queued_action: Any) -> bool:
        payload = queued_action.payload
        return self.protects_piercing(battle, unit, is_skill=queued_action.action_type in {"skill", "skill_effect"},
                                     tags={"attack"} if queued_action.action_type == "attack" else set(),
                                     ignore_shield=bool(payload.get("ignore_shield")), half_ignore_shield=bool(payload.get("half_ignore_shield")),
                                     from_field_effect=bool(payload.get("from_field_effect")))


class WetlandGrasslandEffect(RacialWeatherEffect):
    global_weather = True
    weather_name = "湿地草原"

    def __init__(self) -> None:
        super().__init__("湿地草原", "双方当前等级1的武将本体普攻和反击破魔；技能与召唤物不因此破魔。")

    @staticmethod
    def eligible_hero(actor: HeroUnit) -> bool:
        return (isinstance(actor, HeroUnit) and not actor.is_summon and not actor.is_clone
                and not is_army_soldier(actor) and actor.level == 1)

    def basic_attack_payload_metadata(self, battle: Battle, actor: HeroUnit,
                                      payload: dict[str, Any] | None = None) -> dict[str, Any]:
        return {"ignore_shield": True} if self.eligible_hero(actor) else {}

    def on_targeted(self, battle: Battle, ctx: TargetContext) -> None:
        if not ctx.is_skill and not ctx.from_field_effect and "attack" in ctx.tags and self.eligible_hero(ctx.actor):
            ctx.ignore_shield = True

    def on_before_damage(self, battle: Battle, ctx: DamageContext) -> None:
        if ctx.source is not None and not ctx.is_skill and not ctx.from_field_effect and "attack" in ctx.tags and self.eligible_hero(ctx.source):
            ctx.ignore_shield = True

    def merge_into_existing(self, battle: Battle, existing_effects: list[BattleFieldEffect]) -> bool:
        return any(battle.canonical_weather_name(getattr(effect, "weather_name", "")) == self.weather_name
                   for effect in existing_effects)

    def board_marker(self, battle: Battle) -> str:
        return "湿"


class SimpleGlobalWeatherEffect(RacialWeatherEffect):
    global_weather = True

    def __init__(self, weather_name: str, *, duration: int | None = None, marker: str | None = None) -> None:
        self.weather_name = weather_name
        self._marker = marker or weather_name[:1]
        rules = {"万魔殿": "双方恶魔武将及原召唤链攻+1；每次普攻完成后1/2几率增加本回合1次普攻。",
                 "天空圣域": "双方天使武将及原召唤链魔上限+1；本人回合开始血+1/4、当前魔+1；免破魔及半破魔攻击/技能对应段。"}
        super().__init__(weather_name, f"全场天气：{weather_name}。" + rules.get(Battle.canonical_weather_name(weather_name), ""), duration=duration)

    def merge_into_existing(self, battle: Battle, existing_effects: list[BattleFieldEffect]) -> bool:
        for effect in existing_effects:
            if battle.canonical_weather_name(getattr(effect, "weather_name", "")) != battle.canonical_weather_name(self.weather_name):
                continue
            if self.duration is not None:
                effect.duration = max(int(effect.duration or 0), self.duration)
            battle.log(f"天气【{self.weather_name}】刷新。")
            return True
        return False

    def board_marker(self, battle: Battle) -> str:
        return self._marker


class WeatherUltimateSkill(Skill):
    def ai_self_buff_score(self, context: AISkillScoreContext) -> float:
        return context.shared.weather_cast_score(context)

    def __init__(
        self,
        code: str,
        name: str,
        *,
        weather_name: str | None = None,
        duration: int | None = None,
        marker: str | None = None,
    ) -> None:
        self.weather_name = weather_name or name
        duration_text = "永久" if duration is None else f"持续 {duration} 个全局天气倒计时"
        super().__init__(
            code,
            name,
            f"大招：一场战斗一次，将全场天气变为“{self.weather_name}”，{duration_text}。"
            + ({"万魔殿": "双方恶魔武将及原召唤链攻+1；每次完成普攻有1/2概率增加本全局回合1次普攻。",
                "天空圣域": "双方天使武将及原召唤链魔上限+1，每个自己的行动槽开始血+1/4、当前魔+1，免破魔及半破魔攻击或技能对应段。",
                "湿地草原": "双方当前等级1的真正武将本体普攻及反击破魔；技能和召唤物不因此破魔。"}
               .get(Battle.canonical_weather_name(self.weather_name), "")),
            max_uses_per_battle=1,
            target_mode="self",
        )
        self.weather_duration = duration
        self.weather_marker = marker

    def execute(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> None:
        effect = (WetlandGrasslandEffect() if self.weather_name == "湿地草原"
                  else SimpleGlobalWeatherEffect(self.weather_name, duration=self.weather_duration, marker=self.weather_marker))
        battle.add_field_effect(effect, source=actor)


class PandemoniumSpeedTrait(Trait):
    def __init__(self) -> None:
        super().__init__("万魔殿加速", "在“万魔殿”天气中速 +3。")

    def sync(self, battle: Battle) -> None:
        owner = self.owner
        if owner is None:
            return
        existing = owner.get_status("万魔殿加速")
        if existing is not None:
            owner.remove_status(existing, battle)

    def modify_stat(self, stat_name: str, value: float) -> float:
        owner = self.owner
        reference = getattr(owner, "_battle_ref", None)
        battle = reference() if reference is not None else None
        if (stat_name == "speed" and owner is not None and battle is not None
                and not owner.direct_effects_blocked() and battle.unit_in_weather("万魔殿", owner)):
            return value + 3
        return value

    def on_owner_turn_start(self, battle: Battle) -> None:
        self.sync(battle)

    def on_any_turn_end(self, battle: Battle, ended_player_id: int) -> None:
        self.sync(battle)


class PandemoniumSkill(WeatherUltimateSkill):
    def __init__(self) -> None:
        super().__init__("pandemonium", "万魔殿", marker="魔")

    def execute(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> None:
        super().execute(battle, actor, payload)
        for unit in battle.all_units():
            for component in unit.iter_components():
                if isinstance(component, PandemoniumSpeedTrait):
                    component.sync(battle)


