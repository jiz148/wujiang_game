"""R071 avalanche weather and skills."""

from __future__ import annotations
from wujiang.tactical.heroes.ai_impact_rule import SNOW_AVALANCHE_IMPACT

from typing import Any
from wujiang.tactical.engine.core import Battle
from wujiang.tactical.engine.core import BattleFieldEffect
from wujiang.tactical.engine.core import DamageContext
from wujiang.tactical.engine.core import HeroUnit
from wujiang.tactical.engine.core import Skill
from wujiang.tactical.heroes.ai_policy import AISkillScoreContext
from wujiang.tactical.heroes.common import match_payload_pattern
from wujiang.tactical.heroes.common import pattern_selection_preview
from wujiang.tactical.heroes.mechanics.kaiser import CannotActNextTurnStatus
from wujiang.tactical.heroes.mechanics.next_five_effects import damage_followup_effect_applies
from wujiang.tactical.heroes.mechanics.next_five_effects import remote_rectangle_patterns


class BigAvalancheWeatherEffect(BattleFieldEffect):
    weather_name = "大雪崩"
    global_weather = True

    def __init__(self) -> None:
        super().__init__("大雪崩", "非冰武将及其召唤物不能移动或使用主动技能；冰武将及其召唤物速+2；雪崩伤害+1且破魔。", duration=5)

    @staticmethod
    def ice_eligible(battle: Battle, unit: HeroUnit) -> bool:
        root_id = battle.controlling_hero_id(unit)
        root = battle.units.get(root_id) if root_id else None
        return getattr(root or unit, "attribute", None) == "冰"

    def blocks_unit_movement(self, battle: Battle, unit: HeroUnit) -> bool:
        return not unit.direct_effects_blocked() and not self.ice_eligible(battle, unit)

    def blocks_skill_use(self, battle: Battle, actor: HeroUnit, skill: Skill) -> tuple[bool, str]:
        if skill.timing == "active" and not self.ice_eligible(battle, actor):
            return True, "大雪崩天气中非冰单位不能使用主动技能。"
        return False, ""

    def modify_unit_stat(self, battle: Battle, unit: HeroUnit, stat_name: str, value: float) -> float:
        if stat_name == "speed" and battle.weather_effect_applies(self, unit) and self.ice_eligible(battle, unit) and not unit.direct_effects_blocked():
            return value + 2
        return value

    def on_before_damage(self, battle: Battle, ctx: DamageContext) -> None:
        if "snow_avalanche" not in ctx.tags or not battle.weather_effect_applies(self, ctx.target):
            return
        ctx.attack_power += 1
        ctx.ignore_shield = True

    def merge_into_existing(self, battle: Battle, existing_effects: list[BattleFieldEffect]) -> bool:
        for effect in existing_effects:
            if getattr(effect, "weather_name", None) != self.weather_name:
                continue
            effect.duration = max(int(effect.duration or 0), int(self.duration or 0))
            battle.log("天气【大雪崩】刷新。")
            return True
        return False

    def board_marker(self, battle: Battle) -> str:
        return "雪"


class SnowAvalancheSkill(Skill):
    def ai_control_bonus(self, context: AISkillScoreContext, targets: list[Unit]) -> float:
        return sum(52.0 + 9.0 * sum(skill.timing == "active" for skill in unit.skills)
                   for unit in targets if unit.player_id != context.actor.player_id
                   and not unit.has_status("雪崩") and unit.alive)

    ai_always_hostile_effect = True
    ai_effective_impact_rule = SNOW_AVALANCHE_IMPACT
    def ai_attack_power(self, battle, actor, payload, target, cells) -> float:
        return actor.stat("attack") + (1.0 if battle.has_weather("大雪崩") else 0.0)

    def ai_priority_score(self, context: AISkillScoreContext) -> float:
        return context.snow_avalanche_score(self)

    def __init__(self) -> None:
        super().__init__(
            "snow_avalanche",
            "雪崩",
            "普通技能：2 轮一次，远程选择 2*6 或 6*2 区域，按当前攻造成伤害；被击中单位下个自己的回合不能行动。",
            cooldown_turns=2,
            target_mode="cell",
        )

    def patterns(self, battle: Battle, actor: HeroUnit) -> list[list[Any]]:
        return remote_rectangle_patterns(battle, actor, 2, 6) + remote_rectangle_patterns(battle, actor, 6, 2)

    def chosen_cells(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> list[Any]:
        return match_payload_pattern(payload, self.patterns(battle, actor))

    def ignores_shield_for_payload(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> bool:
        return battle.has_weather("大雪崩")

    def execute(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> None:
        cells = self.chosen_cells(battle, actor, payload)
        for unit in battle.units_at_cells(cells):
            ctx = battle.resolve_damage(
                DamageContext(
                    source=actor,
                    target=unit,
                    attack_power=actor.stat("attack"),
                    is_skill=True,
                    action_name="雪崩",
                    area_cell_hits=battle.unit_hit_count_for_cells(unit, cells),
                    tags={"skill", "snow_avalanche"},
                )
            )
            if unit.alive and damage_followup_effect_applies(ctx):
                existing = unit.get_status("雪崩")
                if existing is not None:
                    unit.remove_status(existing, battle)
                unit.add_status(CannotActNextTurnStatus("雪崩", battle.turn_number))
                battle.log(f"{unit.name} 被【雪崩】压制，下个回合不能行动。")

    def preview(self, battle: Battle, actor: HeroUnit) -> dict[str, Any]:
        preview = pattern_selection_preview(self.patterns(battle, actor))
        cell_keys = {(cell["x"], cell["y"]) for cell in preview["cells"]}
        targets = [
            unit.unit_id
            for unit in battle.all_units()
            if any((cell.x, cell.y) in cell_keys for cell in battle.unit_cells(unit))
        ]
        preview.update({"target_unit_ids": targets, "secondary_cells": [], "requires_target": True})
        return preview

    def get_target_cells_for_payload(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> list[Any]:
        return self.chosen_cells(battle, actor, payload)

    def get_target_units_for_payload(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> list[HeroUnit]:
        return battle.units_at_cells(self.chosen_cells(battle, actor, payload))  # type: ignore[return-value]


class BigAvalancheSkill(Skill):
    def ai_self_buff_score(self, context: AISkillScoreContext) -> float:
        battle, actor = context.battle, context.actor
        enemies = [unit for unit in battle.enemy_units(actor.player_id)
                   if unit.alive and unit.position is not None and not unit.banished]
        if battle.has_weather("大雪崩") or not enemies:
            return -8.0
        net_value = 0.0
        for unit in battle.all_units():
            if not unit.alive or unit.banished or unit.position is None or unit.direct_effects_blocked():
                continue
            ally = unit.player_id == actor.player_id
            if BigAvalancheWeatherEffect.ice_eligible(battle, unit):
                value = 20.0 + min(3.0, unit.stat("speed")) * 4.0 if not unit.cannot_move and not unit.cannot_normal_move else 0.0
                net_value += value if ally else -value
                continue
            usable_active = sum(
                skill.timing == "active"
                and not unit.cannot_use_skills
                and not getattr(skill, "locked", False)
                and skill.cooldown_remaining == 0
                and (skill.max_uses_per_battle is None or skill.uses_this_battle < skill.max_uses_per_battle)
                and skill.mana_cost <= unit.current_mana
                and not any(component.blocks_skill_use(battle, unit, skill)[0] for component in unit.iter_components())
                for skill in unit.skills
            )
            value = (0.0 if unit.cannot_move else min(4.0, unit.stat("speed")) * 9.0) + min(3, usable_active) * 15.0
            net_value += -value if ally else value
        avalanche = next((skill for skill in actor.skills if skill.code == "snow_avalanche"), None)
        if avalanche is not None and not actor.cannot_use_skills and avalanche.cooldown_remaining <= 2:
            net_value += 24.0
        return 24.0 + net_value if net_value > 8.0 else -8.0

    def __init__(self) -> None:
        super().__init__(
            "big_avalanche",
            "大雪崩",
            "大招：一场战斗一次，将全场天气变为“大雪崩”，持续 5 个全局天气倒计时。双方非冰武将及其召唤物不能移动或使用主动技能；冰武将及其召唤物速+2；雪崩伤害值+1且破魔。",
            max_uses_per_battle=1,
            target_mode="self",
        )

    def execute(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> None:
        battle.add_field_effect(BigAvalancheWeatherEffect(), source=actor)


