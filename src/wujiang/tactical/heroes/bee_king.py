"""R124 蜂王：免费自动产蜂、主动产蜂、蜂巢与蜂反。"""

from __future__ import annotations

from typing import Any

from wujiang.tactical.engine.core import Battle, Position, Skill, Stats, Trait, Unit
from wujiang.tactical.heroes.base import AbstractHero
from wujiang.tactical.heroes.common import FlyingTrait, SelfBuffSkill, StatModifierStatus
from wujiang.tactical.heroes.kanzaki import SummonFollowerSkill
from wujiang.tactical.heroes.mechanics.foundation import BasicAttackPierceTrait
from wujiang.tactical.heroes.mechanics.next_five_geometry import square_around_cells
from wujiang.tactical.heroes.common import payload_position


class PurpleBeeHiveBond(Trait):
    def __init__(self) -> None:
        super().__init__("蜂巢同源", "蜂王的蜂巢生效期间，同源紫蜂攻+1。")

    def modify_stat(self, stat_name: str, value: float) -> float:
        bee = self.owner
        if bee is None or stat_name != "attack" or not bee.summoner_id:
            return value
        ref = getattr(bee, "_battle_ref", None)
        battle = ref() if ref is not None else None
        king = battle.units.get(bee.summoner_id) if battle is not None else None
        if (king is not None and king.alive and not king.banished
                and king.position is not None and king.has_status("蜂巢")):
            return value + 1
        return value


class PurpleBee(AbstractHero):
    hero_code = "bee_king_purple_bee"
    hero_name = "紫蜂"
    role = "召唤物"
    attribute = "雷"
    race = "虫人"
    level = 1
    base_stats = Stats(attack=2, defense=1, speed=4, attack_range=1, mana=0)
    raw_skill_text = ""
    raw_trait_text = "普攻破魔；飞行；同源蜂巢攻+1"

    def __init__(self, player_id: int) -> None:
        super().__init__(player_id, is_summon=True)

    def build_skills(self) -> list[Skill]:
        return []

    def build_traits(self) -> list[Trait]:
        return [BasicAttackPierceTrait(), FlyingTrait(), PurpleBeeHiveBond()]


def bee_destinations(battle: Battle, king: Unit) -> list[Position]:
    body = set(battle.unit_cells(king))
    probe = PurpleBee(king.player_id)
    return sorted((cell for cell in set(square_around_cells(battle, body, 1)) - body
                   if battle.can_place_unit(probe, cell)), key=lambda cell: (cell.y, cell.x))


class PurpleBeeSkill(SummonFollowerSkill):
    def __init__(self) -> None:
        super().__init__()
        self.code = "purple_bee"
        self.name = "紫蜂"
        self.description = "主动费0.5魔，在合法外邻格召一只紫蜂；自动生成免费。"
        self.mana_cost = 0.5
        self.max_uses_per_turn = None

    def destinations(self, battle: Battle, actor: Unit) -> list[Position]:
        return bee_destinations(battle, actor)

    def execute(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> None:
        cell = payload_position(payload)
        if cell not in self.destinations(battle, actor):
            battle.log_public_event(f"{actor.name} 的紫蜂落格已失效。", source=actor)
            return
        battle.summon_unit(PurpleBee(actor.player_id), cell, summoner=actor)


class BeeAutoSummonTrait(Trait):
    def __init__(self) -> None:
        super().__init__("紫蜂自动生成", "每个己方英雄回合开始免费生成两只；蜂王本人轮再生成一只。")

    def on_any_turn_start(self, battle: Battle, active_unit: Unit) -> None:
        king = self.owner
        if (king is None or not king.alive or king.banished or king.position is None
                or active_unit.player_id != king.player_id or active_unit.is_summon
                or active_unit.is_clone):
            return
        for _ in range(3 if active_unit is king else 2):
            cells = bee_destinations(battle, king)
            if not cells:
                break
            bee = PurpleBee(king.player_id)
            battle.summon_unit(bee, cells[0], summoner=king)
            battle.log_public_event(f"{king.name} 免费自动召唤 {bee.name}。", source=king, target=bee)


class HiveStatus(StatModifierStatus):
    def __init__(self) -> None:
        super().__init__("蜂巢", attack_delta=1, duration=3,
                         tick_scope="owner_turn_end", description="蜂王和同源在场紫蜂攻+1。")


class HiveSkill(SelfBuffSkill):
    ai_enemy_impact_requirement = False

    def __init__(self) -> None:
        super().__init__("hive", "蜂巢", "每场一次，持续3个本人轮；蜂王及同源紫蜂攻+1。",
                         max_uses_per_battle=1)

    def apply_to_self(self, battle: Battle, actor: Unit) -> None:
        prior = actor.get_status("蜂巢")
        if prior is not None:
            actor.remove_status(prior, battle)
        actor.add_status(HiveStatus(), source=actor)
        battle.log_public_event(f"{actor.name} 开启蜂巢，同源紫蜂与自身攻+1。", source=actor)

    def ai_self_buff_score(self, context: Any) -> float:
        actor, battle = context.actor, context.battle
        bees = sum(unit.hero_code == PurpleBee.hero_code and unit.summoner_id == actor.unit_id
                   and unit.alive and unit.position is not None for unit in battle.player_units(actor.player_id))
        return 22.0 + 15.0 * bees if not actor.has_status("蜂巢") else -100.0


class BeeReversalSkill(SelfBuffSkill):
    ai_enemy_impact_requirement = False

    def __init__(self) -> None:
        super().__init__("bee_reversal", "蜂反", "费1.5魔，重置蜂巢已用额度。",
                         mana_cost=1.5)

    def _hive(self, actor: Unit) -> Skill | None:
        return next((skill for skill in actor.skills if skill.code == "hive"), None)

    def can_use(self, battle: Battle, actor: Unit,
                payload: dict[str, Any] | None = None) -> tuple[bool, str]:
        ok, reason = super().can_use(battle, actor, payload)
        if not ok:
            return ok, reason
        hive = self._hive(actor)
        if hive is None or hive.uses_this_battle == 0:
            return False, "蜂巢尚未使用，无法重置。"
        return True, ""

    def apply_to_self(self, battle: Battle, actor: Unit) -> None:
        hive = self._hive(actor)
        if hive is not None:
            hive.uses_this_battle = 0
            hive.cooldown_remaining = 0
            battle.log_public_event(f"{actor.name} 的蜂巢额度已由蜂反重置。", source=actor)

    def ai_self_buff_score(self, context: Any) -> float:
        return 32.0 if not context.actor.has_status("蜂巢") else -12.0
