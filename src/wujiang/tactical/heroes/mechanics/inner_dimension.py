"""Inner dimension, king insight, and transfer objects."""

from __future__ import annotations

from typing import Any
from wujiang.tactical.engine.core import Battle
from wujiang.tactical.engine.core import BattleFieldEffect
from wujiang.tactical.engine.core import DamageContext
from wujiang.tactical.engine.core import HeroUnit
from wujiang.tactical.engine.core import Position
from wujiang.tactical.engine.core import Skill
from wujiang.tactical.engine.core import StatusEffect
from wujiang.tactical.engine.core import Trait
from wujiang.tactical.heroes.common import StatModifierStatus
from wujiang.tactical.heroes.next_five import position_key
from wujiang.tactical.heroes.next_five import square_around_cells


class InnerDimensionSwordStatus(StatusEffect):
    separate_basic_attack_windows = True

    def __init__(self) -> None:
        super().__init__("里次元大剑", "攻 +2，速 -2；普攻扩散到原目标周围敌方单位，每个实际受体独立连锁且仅命中一次。", duration=None)

    def modify_stat(self, stat_name: str, value: float) -> float:
        if stat_name == "attack":
            return value + 2
        if stat_name == "speed":
            return max(1.0, value - 2)
        return value

    def basic_attack_area_cells(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> list[Position] | None:
        target_id = payload.get("target_unit_id")
        if not target_id:
            return None
        target = battle.units.get(str(target_id))
        if target is None or target.position is None:
            return None
        cells = list(battle.unit_cells(target))
        keys = {position_key(cell) for cell in cells}
        for cell in square_around_cells(battle, battle.unit_cells(target), radius=1):
            if position_key(cell) in keys:
                continue
            if any(unit.player_id != actor.player_id and unit.unit_id != target.unit_id for unit in battle.units_at(cell)):
                keys.add(position_key(cell))
                cells.append(cell)
        return cells


class InnerDimensionSwordSkill(Skill):
    def __init__(self) -> None:
        super().__init__("inner_dimension_sword", "里次元大剑", "开关技能：每回合一次，仅回合开始使用；攻 +2，速 -2，普攻扩散。", max_uses_per_turn=1, target_mode="self")

    def can_use(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any] | None = None) -> tuple[bool, str]:
        ok, reason = super().can_use(battle, actor, payload)
        if not ok:
            return ok, reason
        if actor.actions_taken_this_turn or actor.move_used or actor.attacks_used > 0 or actor.performed_active_skill:
            return False, "里次元大剑只能在回合开始阶段使用。"
        return True, ""

    def execute(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> None:
        existing = actor.get_status("里次元大剑")
        if existing is not None:
            actor.remove_status(existing, battle)
            battle.log(f"{actor.name} 关闭【里次元大剑】。")
        else:
            actor.add_status(InnerDimensionSwordStatus())
            battle.log(f"{actor.name} 开启【里次元大剑】。")


class KingsInsightField(BattleFieldEffect):
    observes_immune_actions = True
    weather_name = "王者的看破"
    global_weather = True

    def __init__(self, source_player_id: int) -> None:
        self.source_player_id = source_player_id
        super().__init__("王者的看破", "本回合内，非己方飞王使用被动/反应技能时血-3/4，然后在场己方飞王本体魔+2。", duration=1)

    def merge_into_existing(self, battle: Battle, existing_effects: list[BattleFieldEffect]) -> bool:
        for effect in existing_effects:
            if isinstance(effect, KingsInsightField):
                effect.source_player_id = self.source_player_id
                effect.duration = 1
                return True
        return False

    def on_owner_action_declared(self, battle: Battle, action_type: str, payload: dict[str, Any]) -> None:
        if action_type != "skill" or not payload.get("queued_resolution") or payload.get("kings_insight_checked"):
            return
        actor = battle.units.get(str(payload.get("unit_id") or ""))
        if actor is None:
            return
        skill = actor.skill_map().get(str(payload.get("skill_code") or ""))
        if skill is None or skill.timing not in {"passive", "reaction"}:
            return
        payload["kings_insight_checked"] = True
        if actor.player_id == self.source_player_id and getattr(actor, "hero_code", "") == "excel_r028":
            return
        if not battle.effect_recipient(actor).direct_effects_blocked():
            battle.resolve_damage(DamageContext(source=None, target=actor, attack_power=0, raw_damage=0.75,
                is_skill=False, from_field_effect=True, action_name="王者的看破", tags={"weather", "kings_insight"}))
        for unit in battle.hero_units(self.source_player_id):
            if (unit.alive and unit.position is not None and not unit.banished and not unit.is_summon
                    and not unit.is_clone and getattr(unit, "hero_code", "") == "excel_r028"):
                gained = unit.gain_mana(2)
                if gained:
                    battle.log_public_event(f"{unit.name} 通过【王者的看破】回复 {gained:g} 魔。", target=unit)


class KingsInsightSkill(Skill):
    def __init__(self) -> None:
        super().__init__("kings_insight", "王者的看破", "普通技能：2 轮一次；本回合天气，非己方飞王使用被动技能血 -3/4，己方飞王魔 +2。", cooldown_turns=2, target_mode="self")

    def execute(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> None:
        battle.add_field_effect(KingsInsightField(actor.player_id))


class MagicPointCapTrait(Trait):
    def __init__(self, cap: float) -> None:
        self.cap = cap
        super().__init__(f"魔力点上限 {int(cap)}", f"最多持有 {cap:g} 魔力点。")

    def bind(self, owner: HeroUnit) -> "MagicPointCapTrait":
        super().bind(owner)
        owner.max_mana_points = min(getattr(owner, "max_mana_points", float("inf")), self.cap)
        if owner.mana_points <= 0:
            owner.mana_points = min(float(owner.base_stats.mana), self.cap)
        else:
            owner.mana_points = min(owner.mana_points, self.cap)
        return self

    def on_owner_turn_start(self, battle: Battle) -> None:
        owner = self.owner
        if owner is not None:
            owner.mana_points = min(owner.mana_points, self.cap)


class WeaponTransferStatus(StatModifierStatus):
    def __init__(self) -> None:
        super().__init__("武器传送", attack_delta=5, duration=1, tick_scope="any_turn_end", description="直到当前回合结束前攻 +5。")


class WeaponTransferSkill(Skill):
    def __init__(self) -> None:
        super().__init__("weapon_transfer", "武器传送", "普通技能：2 轮一次；直到回合结束前攻 +5。", cooldown_turns=2, target_mode="self")

    def execute(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> None:
        actor.add_status(WeaponTransferStatus())


class RedChargeSkill(Skill):
    def __init__(self) -> None:
        super().__init__("red_charge", "蓄力", "普通技能：每回合一次；魔力点 +1。", max_uses_per_turn=1, target_mode="self")

    def execute(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> None:
        actor.gain_mana_points(1)


