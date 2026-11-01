"""Element Hunter battlefield objects and skills."""

from __future__ import annotations

from typing import Any
from typing import Callable
from typing import Optional
from wujiang.tactical.engine.core import ActionError
from wujiang.tactical.engine.core import Battle
from wujiang.tactical.engine.core import BattleFieldEffect
from wujiang.tactical.engine.core import DamageContext
from wujiang.tactical.engine.core import HeroUnit
from wujiang.tactical.engine.core import Position
from wujiang.tactical.engine.core import Skill
from wujiang.tactical.engine.core import Stats
from wujiang.tactical.engine.core import StatusEffect
from wujiang.tactical.engine.core import Trait
from wujiang.tactical.engine.core import Unit
from wujiang.tactical.heroes.base import AbstractHero
from wujiang.tactical.heroes.ai_policy import AISkillScoreContext
from wujiang.tactical.rooms.ai_targets import living_hostile_combatants
from wujiang.tactical.heroes.common import FlagStatus
from wujiang.tactical.heroes.common import LightWallSkill
from wujiang.tactical.heroes.common import ShensuSkill
from wujiang.tactical.heroes.common import ensure_distance
from wujiang.tactical.heroes.common import match_payload_pattern
from wujiang.tactical.heroes.common import pattern_selection_preview
from wujiang.tactical.heroes.common import payload_position
from wujiang.tactical.heroes.common import positions_to_dict
from wujiang.tactical.heroes.mechanics.clone_split import SplitSkill
from wujiang.tactical.heroes.mechanics.next_five_effects import damage_followup_effect_applies
from wujiang.tactical.heroes.mechanics.next_five_effects import remote_rectangle_patterns
from wujiang.tactical.heroes.mechanics.status_replacement import replace_status_by_name


class ElementalEffectTrait(Trait):
    def __init__(self) -> None:
        super().__init__(
            "元素破魔",
            "所有技能的伤害以外效果破魔，且不会与同名技能效果叠加。",
        )


class BurningManaStatus(StatusEffect):
    def __init__(self, *, triggers: int = 5) -> None:
        super().__init__("完全燃烧", "每个己方回合开始时魔 -1。", duration=None)
        self.triggers_remaining = triggers

    def on_owner_turn_start(self, battle: Battle) -> None:
        if self.owner is None:
            return
        lost = self.owner.spend_mana(1)
        battle.log(f"{self.owner.name} 的完全燃烧发作，失去 {lost} 点魔。")
        self.triggers_remaining -= 1
        if self.triggers_remaining <= 0:
            self.owner.remove_status(self, battle)

    def to_public_dict(self, battle: Battle) -> dict[str, Any]:
        data = super().to_public_dict(battle)
        data["duration"] = self.triggers_remaining
        return data


class AllStatsPlusStatus(StatusEffect):
    def __init__(self, *, duration: int = 2) -> None:
        super().__init__("水之波动", "攻、守、速、范、魔上限 +1。", duration=duration, tick_scope="owner_turn_start")

    def modify_stat(self, stat_name: str, value: float) -> float:
        if stat_name in {"attack", "defense", "speed", "attack_range", "mana"}:
            return value + 1
        return value

    def on_removed(self, battle: Battle) -> None:
        if self.owner is not None:
            self.owner.clamp_mana()


class SummonLifetimeStatus(StatusEffect):
    def __init__(self, *, rounds: int, expire_log: str) -> None:
        super().__init__("召唤持续", f"持续 {rounds} 轮。", duration=rounds, tick_scope="owner_turn_end")
        self.expire_log = expire_log

    def on_removed(self, battle: Battle) -> None:
        if self.owner is not None and self.owner.alive:
            self.owner.alive = False
            battle.log(self.expire_log.format(unit=self.owner.name))


class ThunderResetWatcherStatus(StatusEffect):
    def __init__(self) -> None:
        super().__init__("雷神重置监听", "被敌方普攻或技能伤害破坏时重置雷神。", duration=None)

    def on_after_damage(self, battle: Battle, ctx: DamageContext) -> None:
        if self.owner is None or ctx.target.unit_id != self.owner.unit_id:
            return
        if self.owner.alive or ctx.source is None:
            return
        if ctx.source.player_id == self.owner.player_id or ctx.from_field_effect:
            return
        if not ctx.is_skill and "attack" not in ctx.tags:
            return
        summoner_id = self.owner.summoner_id
        summoner = battle.units.get(summoner_id or "")
        if summoner is None:
            return
        skill = summoner.skill_map().get("thunder_god")
        if skill is None:
            return
        skill.uses_this_battle = 0
        skill.cooldown_remaining = 0
        battle.log(f"{self.owner.name} 被敌方伤害破坏，{summoner.name} 的【雷神】已重置。")


class PlantGrowthFieldEffect(BattleFieldEffect):
    def __init__(self, owner_unit_id: str, cells: list[Position]) -> None:
        super().__init__(
            "植物生长",
            "区域内普通移动每走 1 格时，若起点在区域内则需要 2 点移动点数；技能位移不受影响。",
            duration=None,
        )
        self.owner_unit_id = owner_unit_id
        self.cells = {(cell.x, cell.y) for cell in cells}

    def affected_cells(self, battle: Battle) -> list[Position]:
        return [Position(x, y) for x, y in sorted(self.cells)]

    def board_marker(self, battle: Battle) -> str:
        return "植"

    def on_turn_start(self, battle: Battle, active_unit: Optional[HeroUnit]) -> None:
        if active_unit is None or active_unit.unit_id != self.owner_unit_id:
            return
        battle.remove_field_effect(self)

    def normal_movement_step_cost(
        self,
        battle: Battle,
        unit: HeroUnit,
        start: Position,
        end: Position,
        current_cost: int,
    ) -> int:
        if any((cell.x, cell.y) in self.cells for cell in battle.unit_cells_at(unit, start)):
            return max(current_cost, 2)
        return current_cost


class RemoteAreaDamageSkill(Skill):
    def __init__(
        self,
        code: str,
        name: str,
        description: str,
        *,
        width: int,
        height: int,
        status_factory: Callable[[], StatusEffect],
    ) -> None:
        super().__init__(
            code,
            name,
            description,
            max_uses_per_turn=1,
            target_mode="cell",
        )
        self.width = width
        self.height = height
        self.status_factory = status_factory

    def patterns(self, battle: Battle, actor: HeroUnit) -> list[list[Position]]:
        return remote_rectangle_patterns(battle, actor, self.width, self.height)

    def chosen_cells(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> list[Position]:
        return match_payload_pattern(payload, self.patterns(battle, actor))

    def queued_payload_metadata(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> dict[str, Any]:
        return {"declared_area_cells": positions_to_dict(self.chosen_cells(battle, actor, payload))}

    def execute(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> None:
        declared = payload.get("declared_area_cells")
        cells = [Position(int(cell["x"]), int(cell["y"])) for cell in declared] if declared is not None else self.chosen_cells(battle, actor, payload)
        for unit in battle.units_at_cells(cells):
            damage_ctx = battle.resolve_damage(
                DamageContext(
                    source=actor,
                    target=unit,
                    attack_power=actor.stat("attack"),
                    is_skill=True,
                    action_name=self.name,
                    area_cell_hits=battle.unit_hit_count_for_cells(unit, cells),
                    tags={"skill", self.code},
                )
            )
            self.apply_followup_effect(battle, unit, damage_ctx)

    def apply_followup_effect(self, battle: Battle, unit: HeroUnit, damage_ctx: DamageContext) -> None:
        if not unit.alive or not damage_followup_effect_applies(damage_ctx, allow_on_shield_break=True):
            return
        if not damage_ctx.shield_consumed and unit.total_shields() > 0:
            unit.consume_one_shield()
            battle.log(f"{unit.name} 的1层护盾被【{self.name}】的附加效果贯穿。")
        replace_status_by_name(battle, unit, self.status_factory())
        battle.log(f"{unit.name} 获得了【{self.name}】的附加效果。")

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

    def get_target_cells_for_payload(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> list[Position]:
        return self.chosen_cells(battle, actor, payload)

    def get_target_units_for_payload(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> list[HeroUnit]:
        return [unit for unit in battle.units_at_cells(self.chosen_cells(battle, actor, payload))]  # type: ignore[list-item]

    def ignores_shield_for_payload(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> bool:
        return True


def complete_burn_status_factory() -> StatusEffect:
    return BurningManaStatus(triggers=5)


def blizzard_status_factory() -> StatusEffect:
    return FlagStatus(
        "暴风雪",
        "cannot_normal_move",
        description="不能进行普通移动。",
        duration=3,
        tick_scope="owner_turn_end",
    )


class CompleteBurnSkill(RemoteAreaDamageSkill):
    def __init__(self) -> None:
        super().__init__(
            "complete_burn",
            "完全燃烧",
            "普通技能：每回合最多 1 次，远程选择完整 4*4 区域；按当前攻造成伤害，附加每个己方回合开始时魔 -1（附加效果破魔，5轮，不叠加）。",
            width=4,
            height=4,
            status_factory=complete_burn_status_factory,
        )


class BlizzardSkill(RemoteAreaDamageSkill):
    def __init__(self) -> None:
        super().__init__(
            "blizzard",
            "暴风雪",
            "普通技能：每回合最多 1 次，远程选择完整 3*3 区域；按当前攻造成伤害，附加 3轮不能普通移动（附加效果破魔，不叠加）。",
            width=3,
            height=3,
            status_factory=blizzard_status_factory,
        )


class ThunderGodSummon(AbstractHero):
    hero_code = "thunder_god_summon"
    hero_name = "雷神"
    role = "召唤物"
    attribute = "雷"
    race = "召唤物"
    level = 1
    base_stats = Stats(attack=4, defense=5, speed=4, attack_range=3, mana=0)
    raw_skill_text = ""
    raw_trait_text = ""

    def __init__(self, player_id: int) -> None:
        super().__init__(player_id, is_summon=True)

    def build_skills(self) -> list[Skill]:
        return []

    def build_traits(self) -> list[Trait]:
        return []


class ThunderGodSkill(Skill):
    def __init__(self) -> None:
        super().__init__("thunder_god", "雷神", "大招：在范内召唤雷神，持续 5轮；若被敌方普攻或技能伤害破坏则重置此技能。", max_uses_per_battle=1, target_mode="cell")

    def execute(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> None:
        destination = payload_position(payload)
        ensure_distance(actor, destination, actor.targeting_range())
        if battle.is_occupied(destination):
            raise ActionError("召唤位置已被占用。")
        summon = ThunderGodSummon(actor.player_id)
        battle.summon_unit(summon, destination, summoner=actor)
        summon.add_status(SummonLifetimeStatus(rounds=5, expire_log="{unit} 的召唤持续时间结束。"))
        summon.add_status(ThunderResetWatcherStatus())

    def preview(self, battle: Battle, actor: HeroUnit) -> dict[str, Any]:
        if actor.position is None:
            return {"cells": [], "target_unit_ids": [], "secondary_cells": [], "requires_target": True}
        cells = [
            Position(x, y)
            for x in range(battle.width)
            for y in range(battle.height)
            if actor.position.distance_to(Position(x, y)) <= actor.targeting_range()
            and not battle.is_occupied(Position(x, y))
        ]
        return {"cells": positions_to_dict(cells), "target_unit_ids": [], "secondary_cells": [], "requires_target": True}

class WaterWaveSkill(Skill):
    def __init__(self) -> None:
        super().__init__("water_wave", "水之波动", "普通技能：冷却 4轮，只能对自己使用；全能力 +1，持续 2轮，不回复当前魔。", cooldown_turns=4, target_mode="self")

    def execute(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> None:
        replace_status_by_name(battle, actor, AllStatsPlusStatus(duration=2))
        actor.clamp_mana()
        battle.log(f"{actor.name} 的全能力因水之波动提升。")

    def preview(self, battle: Battle, actor: HeroUnit) -> dict[str, Any]:
        return {"cells": [actor.position.to_dict()] if actor.position else [], "target_unit_ids": [actor.unit_id], "secondary_cells": [], "requires_target": False}


class ElementHunterClone(AbstractHero):
    hero_code = "element_hunter_clone"
    hero_name = "元素猎人"
    role = "法师"
    attribute = "木"
    race = "精灵"
    level = 7
    raw_skill_text = "分身"
    raw_trait_text = ""

    def __init__(self, player_id: int, source: HeroUnit) -> None:
        self.base_stats = Stats(
            attack=int(source.stat("attack")),
            defense=int(source.stat("defense")),
            speed=int(source.stat("speed")),
            attack_range=int(source.targeting_range()),
            mana=source.max_mana(),
        )
        super().__init__(player_id, is_summon=True, is_clone=True)
        self.max_health = source.max_health
        self.current_hp = min(source.current_hp, self.max_health)
        self.current_mana = min(source.current_mana, self.max_mana())
        self.mana_points = source.mana_points
        self.cannot_attack = True
        self.cannot_use_skills = True

    def build_skills(self) -> list[Skill]:
        return []

    def build_traits(self) -> list[Trait]:
        return []


class EarthWalkerCleanupStatus(StatusEffect):
    def __init__(self, clone_ids: list[str]) -> None:
        super().__init__("土行者", "下个己方回合开始前破坏由土行者制造的分身。", duration=None)
        self.clone_ids = clone_ids

    def on_owner_turn_start(self, battle: Battle) -> None:
        if self.owner is None:
            return
        destroyed = 0
        for clone_id in self.clone_ids:
            clone = battle.units.get(clone_id)
            if clone is None or not clone.alive or not clone.is_clone:
                continue
            clone.alive = False
            destroyed += 1
        if destroyed:
            battle.log(f"{self.owner.name} 的土行者持续结束，破坏了 {destroyed} 个分身。")
            battle.cleanup_dead_units()
        self.owner.remove_status(self, battle)


class PlantGrowthSkill(Skill):
    def ai_priority_score(self, context: AISkillScoreContext) -> float:
        return context.shared.field_skill_score(context)

    def __init__(self) -> None:
        super().__init__("plant_growth", "植物生长", "普通技能：每回合最多 1 次，远程选择完整 5*5 区域；持续 1轮，普通移动每步若起点在区域内则消耗 2 点移动点数，飞行单位也会受到影响。", max_uses_per_turn=1, target_mode="cell")

    def patterns(self, battle: Battle, actor: HeroUnit) -> list[list[Position]]:
        return remote_rectangle_patterns(battle, actor, 5, 5)

    def chosen_cells(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> list[Position]:
        return match_payload_pattern(payload, self.patterns(battle, actor))

    def queued_payload_metadata(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> dict[str, Any]:
        return {"declared_area_cells": positions_to_dict(self.chosen_cells(battle, actor, payload))}

    def execute(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> None:
        declared = payload.get("declared_area_cells")
        cells = [Position(int(cell["x"]), int(cell["y"])) for cell in declared] if declared is not None else self.chosen_cells(battle, actor, payload)
        battle.add_field_effect(PlantGrowthFieldEffect(actor.unit_id, cells))

    def preview(self, battle: Battle, actor: HeroUnit) -> dict[str, Any]:
        preview = pattern_selection_preview(self.patterns(battle, actor))
        preview.update({"target_unit_ids": [], "secondary_cells": [], "requires_target": True})
        return preview

    def get_target_cells_for_payload(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> list[Position]:
        return self.chosen_cells(battle, actor, payload)

    def ignores_shield_for_payload(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> bool:
        return True


class RendingSkill(Skill):
    excludes_caster_from_effect = True

    def __init__(self) -> None:
        super().__init__(
            "rending",
            "撕裂",
            "大招：按范点选 1 格，造成等同当前攻的伤害，伤害破魔。",
            max_uses_per_battle=1,
            target_mode="cell",
        )

    def _target_cell(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> Position:
        cell = payload_position(payload)
        if battle.unit_distance_to_cell(actor, cell) > actor.targeting_range():
            raise ActionError("目标超出技能范围。")
        return cell

    def execute(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> None:
        cell = battle.payload_positions(payload, "declared_area_cells")[0] if "declared_area_cells" in payload else self._target_cell(battle, actor, payload)
        targets = [unit for unit in battle.units_at(cell) if unit.unit_id != actor.unit_id]
        if not targets:
            battle.log("【撕裂】没有命中有效目标。")
            return
        for unit in targets:
            battle.resolve_damage(
                DamageContext(
                    source=actor,
                    target=unit,
                    attack_power=actor.stat("attack"),
                    is_skill=True,
                    action_name="撕裂",
                    ignore_shield=True,
                    area_cell_hits=battle.unit_hit_count_for_cells(unit, [cell]),
                    tags={"skill", "rending"},
                )
            )

    def queued_payload_metadata(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> dict[str, Any]:
        return {"declared_area_cells": positions_to_dict([self._target_cell(battle, actor, payload)])}

    def preview(self, battle: Battle, actor: HeroUnit) -> dict[str, Any]:
        cells = [
            Position(x, y)
            for x in range(battle.width)
            for y in range(battle.height)
            if battle.unit_distance_to_cell(actor, Position(x, y)) <= actor.targeting_range()
        ]
        targets = [unit.unit_id for unit in battle.units_at_cells(cells) if unit.unit_id != actor.unit_id]
        return {"cells": positions_to_dict(cells), "target_unit_ids": targets, "secondary_cells": [], "requires_target": True}

    def get_target_cells_for_payload(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> list[Position]:
        return [self._target_cell(battle, actor, payload)]

    def get_target_units_for_payload(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> list[HeroUnit]:
        cell = self._target_cell(battle, actor, payload)
        return [unit for unit in battle.units_at(cell) if unit.unit_id != actor.unit_id]  # type: ignore[list-item]

    def ignores_shield_for_payload(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> bool:
        return True



class ElementHunter(AbstractHero):
    hero_code = "element_hunter"
    hero_name = "元素猎人"
    role = "法师"
    attribute = "木"
    race = "精灵"
    level = 7
    base_stats = Stats(attack=3, defense=3, speed=2, attack_range=2, mana=5)
    raw_skill_text = "光墙 神速 完全燃烧（一回合一次；4*4；当前攻伤害；目标5次己方回合开始时魔-1） 暴风雪（一回合一次；3*3；当前攻伤害；3轮不能普通移动） ￥雷神（攻4守5速4范3魔0；5轮；登场不能行动；被敌方普攻或技能直接伤害破坏后重置） 水之波动（4己方轮一次；全能力及魔上限+1，2轮；不补当前魔） 土行者（每回合一次；不费魔；制造3个分身并随机换位，本体结束行动；分身当回合只能移动，下次己方回合开始前全部破坏） 植物生长（每回合一次；5*5；直到自己下次回合开始前，敌我普通移动每步起点在区域内时消耗2点移动点数，飞行也受影响）"
    raw_trait_text = "所有技能的伤害以外效果破魔并且不会与同名技能的效果叠加"

    def build_skills(self) -> list[Skill]:
        return [
            LightWallSkill(),
            ShensuSkill(),
            CompleteBurnSkill(),
            BlizzardSkill(),
            ThunderGodSkill(),
            WaterWaveSkill(),
            EarthWalkerSkill(),
            PlantGrowthSkill(),
        ]

    def build_traits(self) -> list[Trait]:
        return [ElementalEffectTrait()]



class EarthWalkerSkill(SplitSkill):
    def ai_clone_placement_enemies(self, battle: Battle, actor: HeroUnit) -> list[Unit]:
        return living_hostile_combatants(battle, actor.player_id)

    def __init__(self) -> None:
        super().__init__()
        self.code = "earth_walker"
        self.name = "土行者"
        self.mana_cost = 0
        self.description = "普通技能：不费魔，每回合一次，在范内制造3个分身并随机换位；本体结束行动，分身当回合只能移动，下次己方回合开始前破坏。"

    def _clone_probe(self, actor: HeroUnit) -> ElementHunterClone:
        return ElementHunterClone(actor.player_id, actor)

    def execute(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> None:
        previous_ids = set(battle.units)
        super().execute(battle, actor, payload)
        clones = [unit for unit in battle.all_units() if unit.unit_id not in previous_ids and unit.is_clone and unit.summoner_id == actor.unit_id]
        for clone in clones:
            clone.turn_ready = True
            clone.can_act_on_entry_turn = True
        actor.add_status(EarthWalkerCleanupStatus([clone.unit_id for clone in clones]))
