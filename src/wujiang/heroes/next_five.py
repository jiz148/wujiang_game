from __future__ import annotations

import math
import random
from typing import Any, Callable, Iterable, Optional

from wujiang.engine.core import (
    ActionError,
    Battle,
    BattleFieldEffect,
    DamageContext,
    HealContext,
    HeroUnit,
    Position,
    QueuedAction,
    Skill,
    Stats,
    StatusEffect,
    TargetContext,
    Trait,
)
from wujiang.heroes.base import AbstractHero
from wujiang.heroes.common import (
    AttackCountTrait,
    DashMoveSkill,
    DrainManaSkill,
    FlagStatus,
    FlyingTrait,
    HardenSkill,
    KnockbackSkill,
    LightWallSkill,
    MachineGunSkill,
    MultiTargetChainShieldSkill,
    PassiveProtectionSkill,
    OverhealTrait,
    PierceSkill,
    ShensuSkill,
    StatModifierStatus,
    StoneWallSkill,
    StealthSkill,
    WindowChargeSkill,
    dedupe_positions,
    ensure_distance,
    match_payload_pattern,
    pattern_selection_preview,
    pattern_signature,
    payload_cells,
    payload_position,
    positions_to_dict,
)


def replace_status_by_name(battle: Battle, target: HeroUnit, status: StatusEffect) -> None:
    existing = target.get_status(status.name)
    if existing is not None:
        target.remove_status(existing, battle)
    target.add_status(status)


def damage_followup_effect_applies(ctx: DamageContext, *, allow_on_shield_break: bool = False) -> bool:
    if not ctx.cancelled:
        return True
    if ctx.preserve_followup_effects:
        return True
    return allow_on_shield_break and ctx.shield_consumed


def apply_piercing_status_effect(
    battle: Battle,
    source: HeroUnit,
    target: HeroUnit,
    *,
    action_name: str,
    status: StatusEffect,
    is_skill: bool,
    tags: set[str] | None = None,
    ignore_magic_immunity: bool = False,
    ignore_targeting_restrictions: bool = False,
) -> bool:
    if not target.alive or target.position is None or target.banished:
        return False
    is_hostile = target.player_id != source.player_id
    ctx = battle.validate_target(
        source,
        target,
        action_name=action_name,
        is_skill=is_skill,
        is_hostile=is_hostile,
        ignore_shield=True,
        ignore_magic_immunity=ignore_magic_immunity,
        cannot_evade=True,
        ignore_targeting_restrictions=ignore_targeting_restrictions,
        tags=set(tags or set()),
    )
    if ctx.cancelled:
        if ctx.reason:
            battle.log_public_event(ctx.reason, source=source, target=target)
        return False
    if is_hostile and target.total_shields() > 0:
        target.consume_one_shield()
        battle.log_public_event(
            f"{target.name} 的 1 层护盾被【{action_name}】贯穿并打碎。",
            source=source,
            target=target,
        )
    replace_status_by_name(battle, target, status)
    return True


def remote_rectangle_patterns(battle: Battle, actor: HeroUnit, width: int, height: int) -> list[list[Position]]:
    if actor.position is None:
        return []
    patterns: list[list[Position]] = []
    seen: set[tuple[tuple[int, int], ...]] = set()
    for start_x in range(1 - width, battle.width):
        for start_y in range(1 - height, battle.height):
            cells: list[Position] = []
            contains_range_cell = False
            for dx in range(width):
                for dy in range(height):
                    cell = Position(start_x + dx, start_y + dy)
                    if not battle.in_bounds(cell):
                        continue
                    cells.append(cell)
                    if battle.unit_distance_to_cell(actor, cell) <= actor.targeting_range():
                        contains_range_cell = True
            if not cells or not contains_range_cell:
                continue
            deduped = dedupe_positions(cells)
            key = pattern_signature(deduped)
            if key in seen:
                continue
            seen.add(key)
            patterns.append(deduped)
    patterns.sort(key=pattern_signature)
    return patterns


ALL_DIRECTIONS: list[tuple[int, int]] = [
    (-1, -1),
    (-1, 0),
    (-1, 1),
    (0, -1),
    (0, 1),
    (1, -1),
    (1, 0),
    (1, 1),
]

ORTHOGONAL_DIRECTIONS: list[tuple[int, int]] = [(-1, 0), (1, 0), (0, -1), (0, 1)]


def position_key(cell: Position) -> tuple[int, int]:
    return (cell.x, cell.y)


def positions_connected(cells: Iterable[Position]) -> bool:
    keys = {position_key(cell) for cell in cells}
    if not keys:
        return False
    queue = [next(iter(keys))]
    seen = {queue[0]}
    while queue:
        x, y = queue.pop(0)
        for dx, dy in ORTHOGONAL_DIRECTIONS:
            nxt = (x + dx, y + dy)
            if nxt not in keys or nxt in seen:
                continue
            seen.add(nxt)
            queue.append(nxt)
    return seen == keys


def nearby_rectangle_patterns(
    battle: Battle,
    actor: HeroUnit,
    width: int,
    height: int,
) -> list[list[Position]]:
    actor_cells = battle.unit_cells(actor)
    actor_keys = {position_key(cell) for cell in actor_cells}
    patterns: list[list[Position]] = []
    seen: set[tuple[tuple[int, int], ...]] = set()
    for start_x in range(1 - width, battle.width):
        for start_y in range(1 - height, battle.height):
            cells: list[Position] = []
            touches_actor = False
            for dx in range(width):
                for dy in range(height):
                    cell = Position(start_x + dx, start_y + dy)
                    if not battle.in_bounds(cell):
                        continue
                    if position_key(cell) in actor_keys:
                        continue
                    cells.append(cell)
                    if actor_cells and battle.unit_distance_to_cell(actor, cell) <= 1:
                        touches_actor = True
            if not cells or not touches_actor:
                continue
            deduped = dedupe_positions(cells)
            key = pattern_signature(deduped)
            if key in seen:
                continue
            seen.add(key)
            patterns.append(deduped)
    patterns.sort(key=pattern_signature)
    return patterns


def square_around_cells(battle: Battle, cells: Iterable[Position], radius: int) -> list[Position]:
    result: list[Position] = []
    seen: set[tuple[int, int]] = set()
    for origin in cells:
        for x in range(origin.x - radius, origin.x + radius + 1):
            for y in range(origin.y - radius, origin.y + radius + 1):
                cell = Position(x, y)
                key = position_key(cell)
                if key in seen or not battle.in_bounds(cell):
                    continue
                seen.add(key)
                result.append(cell)
    result.sort(key=lambda cell: (cell.y, cell.x))
    return result


def impact_area(battle: Battle, center: Position, radius: int = 1) -> list[Position]:
    return [
        Position(x, y)
        for x in range(center.x - radius, center.x + radius + 1)
        for y in range(center.y - radius, center.y + radius + 1)
        if battle.in_bounds(Position(x, y))
    ]


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
        if (start.x, start.y) in self.cells:
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

    def execute(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> None:
        cells = self.chosen_cells(battle, actor, payload)
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
            if unit.alive and damage_followup_effect_applies(damage_ctx, allow_on_shield_break=True):
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


class CompleteBurnSkill(RemoteAreaDamageSkill):
    def __init__(self) -> None:
        super().__init__(
            "complete_burn",
            "完全燃烧",
            "普通技能：每回合最多 1 次，远程选择完整 4*4 区域；按当前攻造成伤害，附加每个己方回合开始时魔 -1（附加效果破魔，5轮，不叠加）。",
            width=4,
            height=4,
            status_factory=lambda: BurningManaStatus(triggers=5),
        )


class BlizzardSkill(RemoteAreaDamageSkill):
    def __init__(self) -> None:
        super().__init__(
            "blizzard",
            "暴风雪",
            "普通技能：每回合最多 1 次，远程选择完整 3*3 区域；按当前攻造成伤害，附加 3轮不能普通移动（附加效果破魔，不叠加）。",
            width=3,
            height=3,
            status_factory=lambda: FlagStatus(
                "暴风雪",
                "cannot_normal_move",
                description="不能进行普通移动。",
                duration=3,
                tick_scope="owner_turn_end",
            ),
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
        super().__init__("water_wave", "水之波动", "普通技能：冷却 4轮，只能对自己使用；全能力 +1，持续 2轮，不回复当前魔。", cooldown_turns=8, target_mode="self")

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


class EarthWalkerSkill(Skill):
    def __init__(self) -> None:
        super().__init__(
            "earth_walker",
            "土行者",
            "普通技能：不费魔，每回合最多 1 次，在范内制造 1 个分身；本体本回合不能继续行动，分身本回合可以行动但不能普攻或使用技能，并随机与新分身换位。",
            max_uses_per_turn=1,
            target_mode="cell",
        )

    def execute(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> None:
        destination = payload_position(payload)
        ensure_distance(actor, destination, actor.targeting_range())
        if actor.position is None:
            raise ActionError("单位不在战场上。")
        if battle.is_occupied(destination):
            raise ActionError("分身位置已被占用。")
        original_position = actor.position
        clone = ElementHunterClone(actor.player_id, actor)
        battle.summon_unit(clone, destination, summoner=actor)
        clone.turn_ready = True
        clone.can_act_on_entry_turn = True
        chosen_clone = random.choice([clone])
        actor.position, chosen_clone.position = chosen_clone.position, original_position
        actor.turn_ready = False
        actor.add_status(EarthWalkerCleanupStatus([clone.unit_id]))
        battle.log(f"{actor.name} 使用土行者制造了分身，并与分身交换了位置。")

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


class PlantGrowthSkill(Skill):
    def __init__(self) -> None:
        super().__init__("plant_growth", "植物生长", "普通技能：每回合最多 1 次，远程选择完整 5*5 区域；持续 1轮，普通移动每步若起点在区域内则消耗 2 点移动点数，飞行单位也会受到影响。", max_uses_per_turn=1, target_mode="cell")

    def patterns(self, battle: Battle, actor: HeroUnit) -> list[list[Position]]:
        return remote_rectangle_patterns(battle, actor, 5, 5)

    def chosen_cells(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> list[Position]:
        return match_payload_pattern(payload, self.patterns(battle, actor))

    def execute(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> None:
        cells = self.chosen_cells(battle, actor, payload)
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
        cell = self._target_cell(battle, actor, payload)
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


class SandstormWeatherEffect(BattleFieldEffect):
    weather_name = "沙尘"
    global_weather = True

    def __init__(self, *, duration: int = 2) -> None:
        super().__init__(
            "沙尘",
            "全场天气：回合结束时非土单位受天气伤害；飞行 1/8，其他 1/16；沙尘中不能隐身，回避距离 -1。",
            duration=duration,
        )

    def merge_into_existing(self, battle: Battle, existing_effects: list[BattleFieldEffect]) -> bool:
        for effect in list(existing_effects):
            weather_name = getattr(effect, "weather_name", None)
            if weather_name != self.weather_name:
                continue
            if isinstance(effect, RockGodSandstormAura):
                continue
            if self.duration is not None:
                effect.duration = max(int(effect.duration or 0), self.duration)
            battle.log("天气【沙尘】刷新。")
            return True
        return False

    def board_marker(self, battle: Battle) -> str:
        return "沙"

    def on_any_turn_end(self, battle: Battle, ended_player_id: int) -> None:
        for unit in list(battle.all_units()):
            if not unit.alive or unit.banished or unit.position is None:
                continue
            if unit.attribute == "土":
                continue
            damage = 0.125 if unit.has_flying else 0.0625
            battle.resolve_damage(
                DamageContext(
                    source=None,
                    target=unit,
                    attack_power=0,
                    is_skill=False,
                    action_name="沙尘",
                    from_field_effect=True,
                    cannot_evade=True,
                    raw_damage=damage,
                    tags={"weather", "sandstorm"},
                )
            )
        super().on_any_turn_end(battle, ended_player_id)


class WindSandSkill(Skill):
    def __init__(self) -> None:
        super().__init__(
            "wind_sand",
            "风沙",
            "普通技能：每回合最多 1 次，远程选择 2*4 或 4*2 区域；按当前攻造成伤害，若范围内有单位则天气变为沙尘一轮。",
            max_uses_per_turn=1,
            target_mode="cell",
        )

    def patterns(self, battle: Battle, actor: HeroUnit) -> list[list[Position]]:
        patterns = [*remote_rectangle_patterns(battle, actor, 2, 4), *remote_rectangle_patterns(battle, actor, 4, 2)]
        unique: list[list[Position]] = []
        seen: set[tuple[tuple[int, int], ...]] = set()
        for pattern in patterns:
            key = pattern_signature(pattern)
            if key in seen:
                continue
            seen.add(key)
            unique.append(pattern)
        return unique

    def chosen_cells(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> list[Position]:
        return match_payload_pattern(payload, self.patterns(battle, actor))

    def resolve_weather_effect(self, battle: Battle, actor: HeroUnit, queued_action: QueuedAction) -> None:
        duration = int(queued_action.payload.get("duration", 2))
        battle.add_field_effect(SandstormWeatherEffect(duration=duration))

    def execute(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> None:
        cells = self.chosen_cells(battle, actor, payload)
        targets = battle.units_at_cells(cells)
        for unit in targets:
            battle.resolve_damage(
                DamageContext(
                    source=actor,
                    target=unit,
                    attack_power=actor.stat("attack"),
                    is_skill=True,
                    action_name="风沙",
                    area_cell_hits=battle.unit_hit_count_for_cells(unit, cells),
                    tags={"skill", "wind_sand"},
                )
            )
        if targets:
            weather_effect = battle.build_skill_effect_action(
                actor=actor,
                display_name="风沙",
                effect_code="sandstorm_weather",
                payload={"duration": 2},
                include_cell_units=False,
                hostile=False,
                effect_resolver=self.resolve_weather_effect,
            )
            battle.resolve_skill_effect(actor, weather_effect)

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


class CrazySandSkill(Skill):
    def __init__(self) -> None:
        super().__init__(
            "crazy_sand",
            "狂沙",
            "普通技能：冷却 2轮，选择有效方向；直线 5 格造成当前攻伤害，并瞬移到第 6 格，第 6 格越界或被占用则不能选择。",
            cooldown_turns=4,
            target_mode="cell",
        )

    def directions(self) -> list[tuple[int, int]]:
        return [(-1, -1), (-1, 0), (-1, 1), (0, -1), (0, 1), (1, -1), (1, 0), (1, 1)]

    def _patterns_with_destinations(self, battle: Battle, actor: HeroUnit) -> list[tuple[list[Position], Position]]:
        if actor.position is None:
            return []
        result: list[tuple[list[Position], Position]] = []
        seen: set[tuple[tuple[int, int], ...]] = set()
        actor_cells = battle.unit_cells(actor)
        actor_cell_keys = {(cell.x, cell.y) for cell in actor_cells}
        for dx, dy in self.directions():
            destination = actor.position.offset(dx * 6, dy * 6)
            if not battle.can_place_unit(actor, destination, ignore=actor, mover=actor):
                continue
            front_origins = [
                cell
                for cell in actor_cells
                if (cell.x + dx, cell.y + dy) not in actor_cell_keys
            ]
            for origin in front_origins:
                line = battle.line_positions(origin, (dx, dy), 5)
                if len(line) != 5:
                    continue
                if any((cell.x, cell.y) in actor_cell_keys for cell in line):
                    continue
                key = pattern_signature(line)
                if key in seen:
                    continue
                seen.add(key)
                result.append((line, destination))
        return result

    def patterns(self, battle: Battle, actor: HeroUnit) -> list[list[Position]]:
        return [line for line, _ in self._patterns_with_destinations(battle, actor)]

    def chosen_line_and_destination(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> tuple[list[Position], Position]:
        chosen = match_payload_pattern(payload, self.patterns(battle, actor))
        signature = pattern_signature(chosen)
        for line, destination in self._patterns_with_destinations(battle, actor):
            if pattern_signature(line) == signature:
                return chosen, destination
        raise ActionError("该狂沙方向当前不可用。")

    def execute(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> None:
        line, destination = self.chosen_line_and_destination(battle, actor, payload)
        for unit in battle.units_at_cells(line):
            if unit.unit_id == actor.unit_id:
                continue
            battle.resolve_damage(
                DamageContext(
                    source=actor,
                    target=unit,
                    attack_power=actor.stat("attack"),
                    is_skill=True,
                    action_name="狂沙",
                    area_cell_hits=battle.unit_hit_count_for_cells(unit, line),
                    tags={"skill", "crazy_sand"},
                )
            )
        if actor.alive and actor.position is not None:
            battle.move_unit(
                actor,
                destination,
                via_skill=True,
                allow_anywhere=True,
                max_distance=6,
                tags={"crazy_sand"},
            )

    def preview(self, battle: Battle, actor: HeroUnit) -> dict[str, Any]:
        preview = pattern_selection_preview(self.patterns(battle, actor))
        cell_keys = {(cell["x"], cell["y"]) for cell in preview["cells"]}
        targets = [
            unit.unit_id
            for unit in battle.all_units()
            if unit.unit_id != actor.unit_id and any((cell.x, cell.y) in cell_keys for cell in battle.unit_cells(unit))
        ]
        preview.update({"target_unit_ids": targets, "secondary_cells": [], "requires_target": True})
        return preview

    def get_target_cells_for_payload(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> list[Position]:
        line, _ = self.chosen_line_and_destination(battle, actor, payload)
        return line

    def get_target_units_for_payload(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> list[HeroUnit]:
        line, _ = self.chosen_line_and_destination(battle, actor, payload)
        return [unit for unit in battle.units_at_cells(line) if unit.unit_id != actor.unit_id]  # type: ignore[list-item]


class HalfPierceAttackTrait(Trait):
    def __init__(self) -> None:
        super().__init__("攻击半破魔", "普攻命中护盾时打掉护盾，并以原本攻 -1 继续结算伤害。")

    def _is_owner_attack(self, ctx: TargetContext | DamageContext) -> bool:
        owner = self.owner
        source = ctx.actor if isinstance(ctx, TargetContext) else ctx.source
        return owner is not None and source is not None and source.unit_id == owner.unit_id and not ctx.is_skill and "attack" in ctx.tags

    def on_targeted(self, battle: Battle, ctx: TargetContext) -> None:
        if self._is_owner_attack(ctx):
            ctx.half_ignore_shield = True

    def on_before_damage(self, battle: Battle, ctx: DamageContext) -> None:
        if self._is_owner_attack(ctx):
            ctx.half_ignore_shield = True


class AttackLockTrait(Trait):
    def __init__(self) -> None:
        super().__init__("执念目标", "普攻对象被破坏前，不能普攻其他单位。")
        self.locked_target_id: str | None = None

    def _current_locked_target(self, battle: Battle) -> HeroUnit | None:
        if not self.locked_target_id:
            return None
        target = battle.units.get(self.locked_target_id)
        if target is None or not target.alive or target.position is None:
            self.locked_target_id = None
            return None
        return target  # type: ignore[return-value]

    def can_attack_target(self, battle: Battle, actor: HeroUnit, target: HeroUnit) -> tuple[bool, str]:
        if self.owner is None or actor.unit_id != self.owner.unit_id:
            return True, ""
        locked = self._current_locked_target(battle)
        if locked is not None and locked.unit_id != target.unit_id:
            return False, f"{actor.name} 必须先攻击 {locked.name}，直到其被破坏。"
        return True, ""

    def on_owner_action_declared(self, battle: Battle, action_type: str, payload: dict[str, Any]) -> None:
        if self.owner is None or action_type != "attack":
            return
        target_id = str(payload.get("target_unit_id") or "").strip()
        if target_id:
            self.locked_target_id = target_id

    def on_after_damage(self, battle: Battle, ctx: DamageContext) -> None:
        if self.owner is None or ctx.source is None or ctx.source.unit_id != self.owner.unit_id:
            return
        if ctx.is_skill or "attack" not in ctx.tags:
            return
        if ctx.target.alive:
            self.locked_target_id = ctx.target.unit_id
        elif self.locked_target_id == ctx.target.unit_id:
            self.locked_target_id = None


class LinaDestroyRewardTrait(Trait):
    def __init__(self) -> None:
        super().__init__("击破重置", "每回合最多 1 次；自己的普攻或技能破坏武将或守 4 以上单位时，移动和攻击重置，并获得目标剩余魔。")
        self.used_this_turn = False

    def on_owner_turn_start(self, battle: Battle) -> None:
        self.used_this_turn = False

    def _eligible_target(self, target: HeroUnit) -> bool:
        return (not target.is_summon) or target.stat("defense") >= 4

    def on_after_damage(self, battle: Battle, ctx: DamageContext) -> None:
        owner = self.owner
        if owner is None or self.used_this_turn or ctx.source is None or ctx.source.unit_id != owner.unit_id:
            return
        if ctx.target.alive or not (ctx.is_skill or "attack" in ctx.tags):
            return
        if not self._eligible_target(ctx.target):  # type: ignore[arg-type]
            return
        gained = owner.gain_mana(ctx.target.current_mana)
        owner.move_used = False
        owner.attacks_used = 0
        self.used_this_turn = True
        battle.log(f"{owner.name} 击破目标，移动和攻击已重置，并获得 {gained} 点魔。")


class NoEnemyHealAuraTrait(Trait):
    def __init__(self) -> None:
        super().__init__("压制回复", "周围 7*7 内的敌方单位不能回复。")

    def on_before_heal(self, battle: Battle, ctx: HealContext) -> None:
        owner = self.owner
        if owner is None or not owner.alive or owner.position is None or ctx.target.player_id == owner.player_id:
            return
        if battle.distance_between_units(owner, ctx.target) <= 3:
            ctx.cancelled = True
            ctx.reason = f"{ctx.target.name} 处于 {owner.name} 的压制回复范围内，不能回复。"


class LinaSandstormRecoveryTrait(Trait):
    def __init__(self) -> None:
        super().__init__("沙尘自然回复", "沙尘天气且非隐身时，在自己的己方回合开始时自然回血并自然回魔。")

    def on_owner_turn_start(self, battle: Battle) -> None:
        owner = self.owner
        if owner is None or not battle.unit_in_weather("沙尘", owner) or owner.is_stealthed():
            return
        gained = owner.gain_mana(1)
        if gained:
            battle.log(f"{owner.name} 因沙尘自然回魔，获得 {gained} 点魔。")
        battle.heal(HealContext(source=owner, target=owner, amount=0.25, action_name="自然回复", tags={"natural_recovery"}))


class NaturalManaRecoveryTrait(Trait):
    def __init__(self) -> None:
        super().__init__("自然回魔", "每个自己的己方回合开始时魔 +1，最多到当前魔上限。")

    def on_owner_turn_start(self, battle: Battle) -> None:
        if self.owner is None:
            return
        gained = self.owner.gain_mana(1)
        if gained:
            battle.log(f"{self.owner.name} 自然回魔，获得 {gained} 点魔。")


class RockGodSandstormAura(BattleFieldEffect):
    weather_name = "沙尘"

    def __init__(self, owner_unit_id: str) -> None:
        super().__init__("岩神沙尘", "岩神每个占用格周围 9*9 的局部沙尘天气。", duration=None)
        self.owner_unit_id = owner_unit_id
        self.owner_unit_ids = {owner_unit_id}

    def get_owner_unit(self, battle: Battle) -> HeroUnit | None:
        owners = self.get_owner_units(battle)
        return owners[0] if owners else None

    def get_owner_units(self, battle: Battle) -> list[HeroUnit]:
        owners: list[HeroUnit] = []
        for owner_id in sorted(self.owner_unit_ids):
            unit = battle.units.get(owner_id)
            if unit is None or not unit.alive or unit.position is None or unit.banished:
                continue
            owners.append(unit)  # type: ignore[arg-type]
        return owners

    def affected_cells(self, battle: Battle) -> list[Position]:
        owners = self.get_owner_units(battle)
        if not owners:
            return []
        cells: list[Position] = []
        for owner in owners:
            cells.extend(battle.unit_cells(owner))
        return square_around_cells(battle, cells, radius=4)

    def board_marker(self, battle: Battle) -> str:
        return "沙"

    def merge_into_existing(self, battle: Battle, existing_effects: list[BattleFieldEffect]) -> bool:
        for effect in existing_effects:
            if isinstance(effect, RockGodSandstormAura):
                effect.owner_unit_ids.add(self.owner_unit_id)
                return True
        return False

    def on_any_turn_end(self, battle: Battle, ended_player_id: int) -> None:
        owners = self.get_owner_units(battle)
        self.owner_unit_ids = {owner.unit_id for owner in owners}
        if not owners:
            battle.remove_field_effect(self)
            return
        if any(isinstance(effect, SandstormWeatherEffect) for effect in battle.field_effects):
            return
        area_keys = {position_key(cell) for cell in self.affected_cells(battle)}
        for unit in list(battle.all_units()):
            if not unit.alive or unit.banished or unit.position is None:
                continue
            if not any(position_key(cell) in area_keys for cell in battle.unit_cells(unit)):
                continue
            if unit.attribute == "土":
                continue
            damage = 0.125 if unit.has_flying else 0.0625
            battle.resolve_damage(
                DamageContext(
                    source=None,
                    target=unit,
                    attack_power=0,
                    is_skill=False,
                    action_name="沙尘",
                    from_field_effect=True,
                    cannot_evade=True,
                    raw_damage=damage,
                    tags={"weather", "sandstorm"},
                )
            )


class RockGodSandstormTrait(Trait):
    def __init__(self) -> None:
        super().__init__("局部沙尘", "周围 9*9 天气变为沙尘；多格身体按每个占用格周围 9*9 的并集计算。")

    def _ensure_aura(self, battle: Battle) -> None:
        owner = self.owner
        if owner is None:
            return
        if any(isinstance(effect, RockGodSandstormAura) and owner.unit_id in effect.owner_unit_ids for effect in battle.field_effects):
            return
        battle.add_field_effect(RockGodSandstormAura(owner.unit_id))

    def on_enter_battle(self, battle: Battle) -> None:
        self._ensure_aura(battle)

    def on_owner_turn_start(self, battle: Battle) -> None:
        self._ensure_aura(battle)

    def on_owner_removed(self, battle: Battle) -> None:
        owner = self.owner
        if owner is None:
            return
        for effect in list(battle.field_effects):
            if isinstance(effect, RockGodSandstormAura) and owner.unit_id in effect.owner_unit_ids:
                effect.owner_unit_ids.discard(owner.unit_id)
                if not effect.owner_unit_ids:
                    battle.remove_field_effect(effect)


class RockAbsorbStatStatus(StatusEffect):
    def __init__(self, stat_name: str, delta: int, *, duration: int = 1) -> None:
        label = RockAbsorbSkill.stat_labels()[stat_name]
        sign = "+" if delta > 0 else ""
        super().__init__(
            "岩吸",
            f"{label} {sign}{delta}。",
            duration=duration,
            tick_scope="owner_turn_start",
        )
        self.stat_name = stat_name
        self.delta = delta

    def modify_stat(self, stat_name: str, value: float) -> float:
        if stat_name == self.stat_name:
            return value + self.delta
        return value

    def on_removed(self, battle: Battle) -> None:
        if self.owner is not None and self.stat_name == "mana":
            self.owner.clamp_mana()


class RockAbsorbFootprintStatus(StatusEffect):
    def __init__(self) -> None:
        super().__init__("岩吸占格", "岩吸临时增加占格；持续结束后恢复 2*2。", duration=1, tick_scope="owner_turn_start")

    def on_removed(self, battle: Battle) -> None:
        owner = self.owner
        if owner is None or owner.position is None:
            return
        base_offsets = list(owner.base_footprint_offsets)
        current_cells = battle.unit_cells(owner)
        restored: list[Position] = []
        skipped = 0
        for dx, dy in base_offsets:
            cell = owner.position.offset(dx, dy)
            if not battle.in_bounds(cell):
                skipped += 1
                continue
            if battle.is_occupied(cell, ignore=owner, mover=owner):
                skipped += 1
                continue
            restored.append(cell)
        if not restored and current_cells:
            restored = [current_cells[0]]
        owner.set_footprint_cells(restored)
        if skipped:
            battle.log(f"{owner.name} 的岩吸占格结束，{skipped} 个基础格因被占用或越界未恢复。")
        else:
            battle.log(f"{owner.name} 的岩吸占格恢复为 2*2。")


class DragonBreathSkill(Skill):
    def __init__(self) -> None:
        super().__init__(
            "dragon_breath",
            "龙息",
            "普通技能：费 2 魔，每回合最多 2 次，近身选择 2*2 区域；按当前攻造成伤害。",
            mana_cost=2,
            max_uses_per_turn=2,
            target_mode="cell",
        )

    def patterns(self, battle: Battle, actor: HeroUnit) -> list[list[Position]]:
        return nearby_rectangle_patterns(battle, actor, 2, 2)

    def chosen_cells(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> list[Position]:
        return match_payload_pattern(payload, self.patterns(battle, actor))

    def execute(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> None:
        cells = self.chosen_cells(battle, actor, payload)
        for unit in battle.units_at_cells(cells):
            if unit.unit_id == actor.unit_id:
                continue
            battle.resolve_damage(
                DamageContext(
                    source=actor,
                    target=unit,
                    attack_power=actor.stat("attack"),
                    is_skill=True,
                    action_name="龙息",
                    area_cell_hits=battle.unit_hit_count_for_cells(unit, cells),
                    tags={"skill", "dragon_breath"},
                )
            )

    def preview(self, battle: Battle, actor: HeroUnit) -> dict[str, Any]:
        preview = pattern_selection_preview(self.patterns(battle, actor))
        cell_keys = {(cell["x"], cell["y"]) for cell in preview["cells"]}
        targets = [
            unit.unit_id
            for unit in battle.all_units()
            if unit.unit_id != actor.unit_id and any((cell.x, cell.y) in cell_keys for cell in battle.unit_cells(unit))
        ]
        preview.update({"target_unit_ids": targets, "secondary_cells": [], "requires_target": True})
        return preview

    def get_target_cells_for_payload(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> list[Position]:
        return self.chosen_cells(battle, actor, payload)

    def get_target_units_for_payload(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> list[HeroUnit]:
        return [unit for unit in battle.units_at_cells(self.chosen_cells(battle, actor, payload)) if unit.unit_id != actor.unit_id]  # type: ignore[list-item]


class RemoteDragonBreathSkill(DragonBreathSkill):
    def __init__(self) -> None:
        super().__init__()
        self.code = "remote_dragon_breath"
        self.name = "远程龙息"
        self.description = "普通技能：费 2 魔，每回合最多 2 次，远程选择 2*2 区域；按当前攻造成伤害。"

    def patterns(self, battle: Battle, actor: HeroUnit) -> list[list[Position]]:
        return remote_rectangle_patterns(battle, actor, 2, 2)


class DoomLightStatus(FlagStatus):
    def __init__(
        self,
        source_unit_id: str,
        *,
        from_skill: bool,
        triggers: int = 4,
        duration: int = 4,
    ) -> None:
        super().__init__(
            "末日光",
            "cannot_heal",
            description="4轮内不能回复；每个自己的己方回合开始时血量减半。",
            duration=duration,
            tick_scope="owner_turn_start",
        )
        self.source_unit_id = source_unit_id
        self.from_skill = from_skill
        self.triggers_remaining = triggers

    def on_owner_turn_start(self, battle: Battle) -> None:
        owner = self.owner
        if owner is None or not owner.alive or self.triggers_remaining <= 0:
            return
        damage = round(owner.current_hp / 2, 4)
        self.triggers_remaining -= 1
        if damage <= 0:
            return
        source = battle.units.get(self.source_unit_id)
        source_unit = source if isinstance(source, HeroUnit) and source.alive else None
        battle.log(f"{owner.name} 的末日光发作。")
        damage_ctx = battle.resolve_damage(
            DamageContext(
                source=source_unit,
                target=owner,
                attack_power=0,
                is_skill=self.from_skill,
                action_name="末日光",
                raw_damage=damage,
                ignore_shield=True,
                cannot_evade=True,
                tags={"doom_light"},
            )
        )
        if (
            source_unit is not None
            and damage_ctx.raw_damage is not None
            and damage_ctx.raw_damage > 0
            and not damage_ctx.cancelled
        ):
            battle.heal(
                HealContext(
                    source=source_unit,
                    target=source_unit,
                    amount=damage_ctx.raw_damage,
                    action_name="末日光吸收",
                )
            )
        super().on_owner_turn_start(battle)

    def to_public_dict(self, battle: Battle) -> dict[str, Any]:
        data = super().to_public_dict(battle)
        data["triggers_remaining"] = self.triggers_remaining
        return data


class DoomLightSkill(Skill):
    def __init__(self) -> None:
        super().__init__(
            "doom_light",
            "末日光",
            "大招：远程选择 7*7 区域；其中单位获得持续 4轮的末日光效果：不能回复，并在每个自己的己方回合开始时血量减半；该效果破魔且不叠加。",
            max_uses_per_battle=1,
            target_mode="cell",
        )

    def patterns(self, battle: Battle, actor: HeroUnit) -> list[list[Position]]:
        return remote_rectangle_patterns(battle, actor, 7, 7)

    def chosen_cells(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> list[Position]:
        return match_payload_pattern(payload, self.patterns(battle, actor))

    def execute(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> None:
        cells = self.chosen_cells(battle, actor, payload)
        for unit in battle.units_at_cells(cells):
            apply_piercing_status_effect(
                battle,
                actor,
                unit,
                action_name="末日光",
                status=DoomLightStatus(actor.unit_id, from_skill=True),
                is_skill=True,
                tags={"skill", "doom_light"},
                ignore_targeting_restrictions=True,
            )

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


class ApocalypseSkill(Skill):
    def __init__(self) -> None:
        super().__init__(
            "apocalypse",
            "末日",
            "普通技能：每回合最多 1 次；选择 n（需小于当前生命），耗费 n 点生命，远程对 n*n 区域造成当前攻+n的破魔伤害。",
            max_uses_per_turn=1,
            target_mode="cell",
        )

    def max_n(self, actor: HeroUnit) -> int:
        # Apocalypse chooses the largest positive integer strictly below current hp.
        # Example: hp 1.25 -> n can be 1; hp 2.0 -> n can be 1.
        return max(0, int(math.ceil(actor.current_hp)) - 1)

    def pattern_choices(self, battle: Battle, actor: HeroUnit) -> list[dict[str, Any]]:
        choices: list[dict[str, Any]] = []
        for n in range(1, self.max_n(actor) + 1):
            patterns = remote_rectangle_patterns(battle, actor, n, n)
            if not patterns:
                continue
            choices.append(
                {
                    "code": str(n),
                    "label": f"n={n}",
                    "value": n,
                    "patterns": patterns,
                }
            )
        return choices

    def selected_n(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> int:
        if payload.get("resolved_n") is not None:
            return int(payload["resolved_n"])
        raw = payload.get("choice_code", payload.get("n"))
        try:
            selected = int(raw)
        except (TypeError, ValueError):
            raise ActionError("末日需要先选择 n。")
        valid = {int(choice["value"]) for choice in self.pattern_choices(battle, actor)}
        if selected not in valid:
            raise ActionError("当前生命下不能选择这个 n。")
        if selected >= actor.current_hp:
            raise ActionError("末日要求 n 严格小于当前生命。")
        return selected

    def patterns_for_n(self, battle: Battle, actor: HeroUnit, n: int) -> list[list[Position]]:
        if n < 1:
            return []
        return remote_rectangle_patterns(battle, actor, n, n)

    def chosen_cells(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> tuple[int, list[Position]]:
        n = self.selected_n(battle, actor, payload)
        cells = match_payload_pattern(payload, self.patterns_for_n(battle, actor, n))
        return n, cells

    def can_use(
        self,
        battle: Battle,
        actor: HeroUnit,
        payload: dict[str, Any] | None = None,
    ) -> tuple[bool, str]:
        ok, reason = super().can_use(battle, actor, payload)
        if not ok:
            return ok, reason
        if self.max_n(actor) < 1:
            return False, "当前生命不足以施放末日。"
        if payload is None:
            return True, ""
        try:
            n = self.selected_n(battle, actor, payload)
        except ActionError as exc:
            return False, str(exc)
        if n >= actor.current_hp:
            return False, "末日要求 n 严格小于当前生命。"
        if not self.patterns_for_n(battle, actor, n):
            return False, "当前没有可选的末日范围。"
        return True, ""

    def prepay_resources(
        self,
        battle: Battle,
        actor: HeroUnit,
        payload: dict[str, Any] | None = None,
    ) -> None:
        if payload is None:
            raise ActionError("末日需要先选择范围。")
        n = self.selected_n(battle, actor, payload)
        payload["resolved_n"] = n
        actor.current_hp = round(actor.current_hp - n, 4)
        self.uses_this_turn += 1
        self.uses_this_battle += 1

    def execute(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> None:
        n, cells = self.chosen_cells(battle, actor, payload)
        for unit in battle.units_at_cells(cells):
            battle.resolve_damage(
                DamageContext(
                    source=actor,
                    target=unit,
                    attack_power=actor.stat("attack") + n,
                    is_skill=True,
                    action_name="末日",
                    ignore_shield=True,
                    area_cell_hits=battle.unit_hit_count_for_cells(unit, cells),
                    tags={"skill", "apocalypse"},
                )
            )

    def preview(self, battle: Battle, actor: HeroUnit) -> dict[str, Any]:
        choices = self.pattern_choices(battle, actor)
        preview_cells: list[Position] = []
        seen_cells: set[tuple[int, int]] = set()
        preview_choices: list[dict[str, Any]] = []
        all_targets: set[str] = set()
        for choice in choices:
            preview_choices.append(
                {
                    "code": str(choice["code"]),
                    "label": str(choice["label"]),
                    "patterns": [positions_to_dict(dedupe_positions(pattern)) for pattern in choice["patterns"]],
                }
            )
            for pattern in choice["patterns"]:
                for cell in dedupe_positions(pattern):
                    key = (cell.x, cell.y)
                    if key not in seen_cells:
                        seen_cells.add(key)
                        preview_cells.append(cell)
            for unit in battle.units_at_cells([cell for pattern in choice["patterns"] for cell in pattern]):
                all_targets.add(unit.unit_id)
        return {
            "cells": positions_to_dict(preview_cells),
            "target_unit_ids": list(all_targets),
            "secondary_cells": [],
            "requires_target": True,
            "selection": {
                "mode": "choice_pattern",
                "choices": preview_choices,
                "ordered": False,
            },
        }

    def get_target_cells_for_payload(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> list[Position]:
        _, cells = self.chosen_cells(battle, actor, payload)
        return cells

    def get_target_units_for_payload(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> list[HeroUnit]:
        _, cells = self.chosen_cells(battle, actor, payload)
        return [unit for unit in battle.units_at_cells(cells)]  # type: ignore[list-item]

    def ignores_shield_for_payload(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> bool:
        return True


class DoomLightRetaliationTrait(Trait):
    def __init__(self) -> None:
        super().__init__("反噬末日光", "攻击、伤害神龙。末日光的单位，以及被其伤害的单位，都会获得末日光效果。")

    def apply_doom_light(self, battle: Battle, target: HeroUnit) -> None:
        owner = self.owner
        if owner is None or target.unit_id == owner.unit_id:
            return
        apply_piercing_status_effect(
            battle,
            owner,
            target,
            action_name="末日光",
            status=DoomLightStatus(owner.unit_id, from_skill=False),
            is_skill=False,
            tags={"doom_light_trait"},
            ignore_targeting_restrictions=True,
        )

    def on_after_damage(self, battle: Battle, ctx: DamageContext) -> None:
        owner = self.owner
        if owner is None or "doom_light" in ctx.tags:
            return
        if ctx.source is not None and ctx.target.unit_id == owner.unit_id and ctx.source.unit_id != owner.unit_id:
            self.apply_doom_light(battle, ctx.source)  # type: ignore[arg-type]
        if ctx.source is not None and ctx.source.unit_id == owner.unit_id and ctx.target.unit_id != owner.unit_id:
            self.apply_doom_light(battle, ctx.target)  # type: ignore[arg-type]

    def on_damage_cancelled(self, battle: Battle, ctx: DamageContext) -> None:
        owner = self.owner
        if owner is None or "attack" not in ctx.tags or ctx.source is None:
            return
        if ctx.target.unit_id == owner.unit_id and ctx.source.unit_id != owner.unit_id:
            self.apply_doom_light(battle, ctx.source)  # type: ignore[arg-type]


class RockAbsorbSkill(Skill):
    def __init__(self) -> None:
        super().__init__(
            "rock_absorb",
            "岩吸",
            "普通技能：每回合最多 1 次；选择一种能力值，吸取局部沙尘中除自己外所有单位的该能力值；护盾类效果可以挡住岩吸。",
            max_uses_per_turn=1,
            target_mode="cell",
        )

    @staticmethod
    def stat_labels() -> dict[str, str]:
        return {
            "attack": "攻",
            "defense": "守",
            "speed": "速",
            "attack_range": "范",
            "mana": "魔",
        }

    def aura_cells(self, battle: Battle, actor: HeroUnit) -> list[Position]:
        return square_around_cells(battle, battle.unit_cells(actor), radius=4)

    def affected_units(self, battle: Battle, actor: HeroUnit) -> list[HeroUnit]:
        area = self.aura_cells(battle, actor)
        return [unit for unit in battle.units_at_cells(area) if unit.unit_id != actor.unit_id]  # type: ignore[list-item]

    def growth_candidates(self, battle: Battle, actor: HeroUnit, max_cells: int) -> list[Position]:
        if max_cells <= 0:
            return []
        body_keys = {position_key(cell) for cell in battle.unit_cells(actor)}
        seen = set(body_keys)
        frontier: list[tuple[tuple[int, int], int]] = [(key, 0) for key in body_keys]
        result: list[Position] = []
        while frontier:
            (x, y), depth = frontier.pop(0)
            if depth >= max_cells:
                continue
            for dx, dy in ORTHOGONAL_DIRECTIONS:
                candidate = Position(x + dx, y + dy)
                key = position_key(candidate)
                if key in seen:
                    continue
                seen.add(key)
                if not battle.in_bounds(candidate):
                    continue
                if battle.is_occupied(candidate, ignore=actor, mover=actor):
                    continue
                result.append(candidate)
                frontier.append((key, depth + 1))
        result.sort(key=lambda cell: (cell.y, cell.x))
        return result

    def selected_growth_cells(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any], required: int) -> list[Position]:
        raw_cells = payload.get("cells")
        selected = [] if required == 0 and raw_cells in (None, []) else payload_cells(payload)
        if len(selected) != required:
            raise ActionError(f"岩吸需要选择 {required} 个新增占格。")
        current_cells = battle.unit_cells(actor)
        current_keys = {position_key(cell) for cell in current_cells}
        selected_keys = {position_key(cell) for cell in selected}
        if len(selected_keys) != len(selected):
            raise ActionError("不能重复选择新增占格。")
        candidates = {position_key(cell) for cell in self.growth_candidates(battle, actor, max(required, len(selected)))}
        for cell in selected:
            key = position_key(cell)
            if key in current_keys:
                raise ActionError("新增占格不能选择当前身体。")
            if key not in candidates:
                raise ActionError("该格不能作为岩吸新增占格。")
            if battle.is_occupied(cell, ignore=actor, mover=actor):
                raise ActionError("新增占格已被占用。")
        if selected and not positions_connected([*current_cells, *selected]):
            raise ActionError("新增后的身体必须正交相连。")
        return selected

    def selected_stat(self, payload: dict[str, Any]) -> str:
        stat_name = str(payload.get("stat_name") or payload.get("stat") or "").strip()
        if stat_name not in self.stat_labels():
            raise ActionError("岩吸需要选择吸取的能力值。")
        return stat_name

    def execute(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> None:
        stat_name = self.selected_stat(payload)
        targets = self.affected_units(battle, actor)
        gain = len(targets)
        candidates = self.growth_candidates(battle, actor, gain)
        required_cells = min(gain, len(candidates))
        selected_cells = self.selected_growth_cells(battle, actor, payload, required_cells)
        applied_count = 0
        for target in targets:
            ctx = battle.validate_target(
                actor,
                target,
                action_name="岩吸",
                is_skill=True,
                is_hostile=target.player_id != actor.player_id,
                tags={"skill", "rock_absorb"},
            )
            if ctx.cancelled:
                if ctx.reason:
                    battle.log_public_event(ctx.reason, source=actor, target=target)
                continue
            applied_count += 1
            replace_status_by_name(battle, target, RockAbsorbStatStatus(stat_name, -1))
            if stat_name == "mana":
                target.current_mana = round(max(0.0, target.current_mana - 1), 2)
                target.clamp_mana()
            battle.log(f"{target.name} 受到岩吸影响，{self.stat_labels()[stat_name]} -1。")
        if applied_count:
            replace_status_by_name(battle, actor, RockAbsorbStatStatus(stat_name, applied_count))
            if stat_name == "mana":
                actor.current_mana = round(actor.current_mana + applied_count, 2)
                actor.clamp_mana()
        gained_cells = selected_cells[:applied_count]
        if gained_cells:
            actor.set_footprint_cells([*battle.unit_cells(actor), *gained_cells])
            replace_status_by_name(battle, actor, RockAbsorbFootprintStatus())
            battle.log(f"{actor.name} 因岩吸增加了 {len(gained_cells)} 个占格。")

    def preview(self, battle: Battle, actor: HeroUnit) -> dict[str, Any]:
        targets = self.affected_units(battle, actor)
        candidates = self.growth_candidates(battle, actor, len(targets))
        required = min(len(targets), len(candidates))
        return {
            "cells": positions_to_dict(candidates),
            "target_unit_ids": [unit.unit_id for unit in targets],
            "secondary_cells": positions_to_dict(self.aura_cells(battle, actor)),
            "requires_target": True,
            "selection": {
                "mode": "stat_cells",
                "stats": [
                    {"code": code, "label": label}
                    for code, label in self.stat_labels().items()
                ],
                "required_cells": required,
            },
        }

    def get_target_cells_for_payload(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> list[Position]:
        self.selected_stat(payload)
        return self.aura_cells(battle, actor)

    def get_target_units_for_payload(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> list[HeroUnit]:
        self.selected_stat(payload)
        return self.affected_units(battle, actor)


class RockCannonSkill(Skill):
    def __init__(self) -> None:
        super().__init__(
            "rock_cannon",
            "岩石炮",
            "普通技能：选择身体任意数量格子和方向发射；每个格子碰撞或到边界消失时，对周围 3*3 造成 3+发射格数的伤害。",
            target_mode="cell",
        )

    def selected_body_cells(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> list[Position]:
        selected = payload_cells(payload)
        body_keys = {position_key(cell) for cell in battle.unit_cells(actor)}
        selected_keys = {position_key(cell) for cell in selected}
        if not selected or len(selected_keys) != len(selected):
            raise ActionError("岩石炮需要选择至少 1 个不同身体格。")
        if not selected_keys.issubset(body_keys):
            raise ActionError("岩石炮只能选择岩神当前身体格。")
        remaining = [cell for cell in battle.unit_cells(actor) if position_key(cell) not in selected_keys]
        if not remaining:
            raise ActionError("岩石炮发射后本体至少要剩下 1 格。")
        if not positions_connected(remaining):
            raise ActionError("岩石炮发射后剩余身体必须正交相连。")
        return selected

    def selected_direction(self, payload: dict[str, Any]) -> tuple[int, int]:
        direction = payload.get("direction")
        if isinstance(direction, dict):
            dx = int(direction.get("dx", 0))
            dy = int(direction.get("dy", 0))
        else:
            dx = int(payload.get("dx", 0))
            dy = int(payload.get("dy", 0))
        if (dx, dy) not in ALL_DIRECTIONS:
            raise ActionError("岩石炮需要选择一个有效方向。")
        return dx, dy

    def validate_selection(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> tuple[list[Position], tuple[int, int], list[Position]]:
        selected = self.selected_body_cells(battle, actor, payload)
        direction = self.selected_direction(payload)
        selected_keys = {position_key(cell) for cell in selected}
        remaining = [cell for cell in battle.unit_cells(actor) if position_key(cell) not in selected_keys]
        remaining_keys = {position_key(cell) for cell in remaining}
        dx, dy = direction
        for start in selected:
            current = start
            while True:
                current = current.offset(dx, dy)
                if not battle.in_bounds(current):
                    break
                if position_key(current) in remaining_keys:
                    raise ActionError("所选方向会被岩神未发射的身体挡住。")
                occupants = [
                    unit
                    for unit in battle.units_at(current, ignore=actor)
                    if unit.position is not None and unit.alive and not unit.banished
                ]
                if occupants:
                    break
        return selected, direction, remaining

    def impact_positions(self, battle: Battle, actor: HeroUnit, selected: list[Position], direction: tuple[int, int]) -> list[Position]:
        dx, dy = direction
        impacts: list[Position] = []
        for start in selected:
            current = start
            last_in_bounds = start
            while True:
                current = current.offset(dx, dy)
                if not battle.in_bounds(current):
                    impacts.append(last_in_bounds)
                    break
                last_in_bounds = current
                occupants = [
                    unit
                    for unit in battle.units_at(current)
                    if unit.unit_id != actor.unit_id and unit.position is not None and unit.alive and not unit.banished
                ]
                if occupants:
                    impacts.append(current)
                    break
        return impacts

    def execute(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> None:
        selected, direction, remaining = self.validate_selection(battle, actor, payload)
        actor.set_footprint_cells(remaining)
        impacts = self.impact_positions(battle, actor, selected, direction)
        attack_power = 3 + len(selected)
        for index, impact in enumerate(impacts, start=1):
            cells = impact_area(battle, impact)
            battle.queue_area_damage_effect(
                actor=actor,
                display_name="岩石炮",
                cells=cells,
                attack_power=attack_power,
                speed=self.chain_speed,
                tags={"skill", "rock_cannon"},
                segment_index=index,
                segment_count=len(impacts),
            )
        battle.log(f"{actor.name} 发射了 {len(selected)} 个身体格。")

    def preview(self, battle: Battle, actor: HeroUnit) -> dict[str, Any]:
        body_cells = battle.unit_cells(actor)
        return {
            "cells": positions_to_dict(body_cells),
            "target_unit_ids": [],
            "secondary_cells": positions_to_dict(body_cells),
            "requires_target": True,
            "selection": {
                "mode": "body_direction",
                "directions": [
                    {"dx": dx, "dy": dy, "label": label}
                    for (dx, dy), label in [
                        ((0, -1), "上"),
                        ((1, -1), "右上"),
                        ((1, 0), "右"),
                        ((1, 1), "右下"),
                        ((0, 1), "下"),
                        ((-1, 1), "左下"),
                        ((-1, 0), "左"),
                        ((-1, -1), "左上"),
                    ]
                ],
            },
        }

    def get_target_cells_for_payload(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> list[Position]:
        return []

    def get_target_units_for_payload(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> list[HeroUnit]:
        return []


class ElementHunter(AbstractHero):
    hero_code = "element_hunter"
    hero_name = "元素猎人"
    role = "法师"
    attribute = "木"
    race = "精灵"
    level = 7
    base_stats = Stats(attack=3, defense=3, speed=2, attack_range=2, mana=5)
    raw_skill_text = "光墙 神速 完全燃烧（一回合一次；4*4，被击中后每回合魔-1；5轮）暴风雪（一回合一次；3*3，被击中后3轮不能移动）￥雷神（攻4守5速4范3，5轮；召唤的单位被对方的伤害破坏后此技能重置） 水之波动（4轮一次；全能力+1；2轮）土行者（一回合一次；制造一个分身，当回合可以行动；在下个回合结束时如果场上有分身则破坏所有分身） 植物生长（一回合一次；选择5*5的范围；那个范围直到下个回合结束时移动一格需要两个移动点数）"
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


class UndeadKingLina(AbstractHero):
    hero_code = "undead_king_lina"
    hero_name = "不死王利娜"
    role = "刺客"
    attribute = "土"
    race = "灵体"
    level = 8
    base_stats = Stats(attack=4, defense=4, speed=4, attack_range=3, mana=5)
    footprint_width = 2
    footprint_height = 2
    raw_skill_text = "隐身 变硬 ￥撕裂 风沙（一回合一次；2*4；对有单位的格子使用后直到下个回合结束前天气变为沙尘）震开 狂沙（2轮一次；直线5格，移动到第6格，对经过的单位造成伤害）"
    raw_trait_text = "攻击两次；攻击半破魔；在攻击对象死之前无法攻击其他单位；占4格；每破坏一个武将或守在4以上的单位移动，攻击重置，并且魔+那个单位剩余的魔，此效果每回合最多发动一次；此单位周围7*7的对方单位不能回复；在沙尘天气并且非隐身时自然回复"

    def build_skills(self) -> list[Skill]:
        return [
            StealthSkill(),
            HardenSkill(),
            RendingSkill(),
            WindSandSkill(),
            KnockbackSkill(),
            CrazySandSkill(),
        ]

    def build_traits(self) -> list[Trait]:
        return [
            AttackCountTrait(2),
            HalfPierceAttackTrait(),
            AttackLockTrait(),
            LinaDestroyRewardTrait(),
            NoEnemyHealAuraTrait(),
            LinaSandstormRecoveryTrait(),
        ]


class RockGod(AbstractHero):
    hero_code = "rock_god"
    hero_name = "岩神"
    role = "狂战"
    attribute = "土"
    race = "石人"
    level = 4
    base_stats = Stats(attack=3, defense=5, speed=2, attack_range=1, mana=3)
    footprint_width = 2
    footprint_height = 2
    raw_skill_text = "变硬 震开 龙息 岩吸（一回合一次；可以对‘沙尘’中所有单位生效；指定一个能力值，那些单位直到下回合结束，那个能力值-1；护盾类效果可以挡住岩吸；此单位可以任意增加等于因为此效果减少的能力值；此效果生效的时间内每增加一点能力值，此单位格子尽量增加一格；效果结束后此单位格子恢复到2*2） 岩石炮（直线移动此单位的任意数量格子直到触碰到单位；那些格子消失并对周围造3+格子数量的伤害）"
    raw_trait_text = "自然回魔；此单位周围9*9天气变为“沙尘”；占2*2"

    def build_skills(self) -> list[Skill]:
        return [
            HardenSkill(),
            KnockbackSkill(),
            DragonBreathSkill(),
            RockAbsorbSkill(),
            RockCannonSkill(),
        ]

    def build_traits(self) -> list[Trait]:
        return [
            NaturalManaRecoveryTrait(),
            RockGodSandstormTrait(),
        ]


class DoomlightDragon(AbstractHero):
    hero_code = "doomlight_dragon"
    hero_name = "神龙。末日光"
    role = "法师"
    attribute = "光"
    race = "古龙"
    level = 4
    base_stats = Stats(attack=3, defense=4, speed=3, attack_range=3, mana=5)
    footprint_width = 2
    footprint_height = 2
    raw_skill_text = "石墙 神速 变硬 远程龙息 ￥末日光（7*7内4轮不能回复，每个己方回合开始时血*1/2，4轮，破魔；此效果不叠加） 末日（一回合一次；耗费n的血量；对n*n造成攻击力+n的破魔伤害）"
    raw_trait_text = "占2*2；飞行；对此单位攻击或造成伤害的单位受到“末日光”的效果；被此单位造成伤害的单位受到“末日光”的效果；“末日光”效果所造成的伤害等量回复此单位；此单位的血量可以超过1"

    def build_skills(self) -> list[Skill]:
        return [
            StoneWallSkill(),
            ShensuSkill(),
            HardenSkill(),
            RemoteDragonBreathSkill(),
            DoomLightSkill(),
            ApocalypseSkill(),
        ]

    def build_traits(self) -> list[Trait]:
        return [
            FlyingTrait(),
            DoomLightRetaliationTrait(),
            OverhealTrait(),
        ]


ARC_DIRECTION_CHOICES: list[tuple[str, tuple[int, int], str]] = [
    ("up", (0, -1), "上"),
    ("up_right", (1, -1), "右上"),
    ("right", (1, 0), "右"),
    ("down_right", (1, 1), "右下"),
    ("down", (0, 1), "下"),
    ("down_left", (-1, 1), "左下"),
    ("left", (-1, 0), "左"),
    ("up_left", (-1, -1), "左上"),
]


def arc_direction_cells(center: Position, code: str) -> list[Position]:
    if code == "up":
        return [Position(center.x - 1, center.y - 1), Position(center.x, center.y - 1), Position(center.x + 1, center.y - 1)]
    if code == "down":
        return [Position(center.x - 1, center.y + 1), Position(center.x, center.y + 1), Position(center.x + 1, center.y + 1)]
    if code == "left":
        return [Position(center.x - 1, center.y - 1), Position(center.x - 1, center.y), Position(center.x - 1, center.y + 1)]
    if code == "right":
        return [Position(center.x + 1, center.y - 1), Position(center.x + 1, center.y), Position(center.x + 1, center.y + 1)]
    if code == "up_right":
        return [Position(center.x, center.y - 1), Position(center.x + 1, center.y - 1), Position(center.x + 1, center.y)]
    if code == "down_right":
        return [Position(center.x + 1, center.y), Position(center.x + 1, center.y + 1), Position(center.x, center.y + 1)]
    if code == "down_left":
        return [Position(center.x - 1, center.y), Position(center.x - 1, center.y + 1), Position(center.x, center.y + 1)]
    if code == "up_left":
        return [Position(center.x - 1, center.y), Position(center.x - 1, center.y - 1), Position(center.x, center.y - 1)]
    return []


def alive_owned_motor_horse(battle: Battle, rider: HeroUnit) -> Optional["MotorHorseSummon"]:
    for unit in battle.all_units():
        if (
            isinstance(unit, MotorHorseSummon)
            and unit.mount_owner_id == rider.unit_id
            and unit.alive
            and not unit.banished
            and unit.position is not None
        ):
            return unit
    return None


class MotorHorseCooldownStatus(StatusEffect):
    def __init__(self, duration: int) -> None:
        super().__init__(
            "摩托马召回冷却",
            "坐骑被破坏后，需要再等待 1 个自己的回合才能重新召唤。",
            duration=duration,
            tick_scope="owner_turn_end",
        )


class RideableMountTrait(Trait):
    def __init__(self) -> None:
        super().__init__("可乘骑", "只有被乘骑单位会承受伤害和技能效果；乘骑者仍可替其连锁。")

    def on_owner_removed(self, battle: Battle) -> None:
        owner = self.owner
        if owner is None or not owner.mount_owner_id:
            return
        rider = battle.units.get(owner.mount_owner_id)
        if not isinstance(rider, HeroUnit) or not rider.alive:
            return
        duration = 2 if battle.active_player == rider.player_id else 1
        replace_status_by_name(battle, rider, MotorHorseCooldownStatus(duration))


class FreeShensuSkill(ShensuSkill):
    def __init__(self) -> None:
        super().__init__()
        self.code = "free_shensu"
        self.description = "普通技能：每回合最多 1 次，不费魔，本回合内下一次普通移动的格数 +3。"
        self.mana_cost = 0


class MountedLeapSkill(DashMoveSkill):
    def __init__(self) -> None:
        super().__init__(
            "mounted_leap",
            "飞跃",
            "普通技能：仅能在乘骑状态时使用；不费魔，每回合最多 1 次，直线飞行移动恰好 3 格。",
            max_distance=3,
            mana_cost=0,
            max_uses_per_turn=1,
            straight_only=True,
            ignore_units=True,
            exact_distance=3,
        )

    def can_use(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any] | None = None) -> tuple[bool, str]:
        ok, reason = super().can_use(battle, actor, payload)
        if not ok:
            return ok, reason
        if battle.mounted_unit_for(actor) is None:
            return False, "只有乘骑状态时才能使用飞跃。"
        return True, ""


class SixBladeStyleStatus(StatusEffect):
    def __init__(self) -> None:
        super().__init__(
            "六刀流",
            "本回合内攻 -1，普攻上限变为 6 次。",
            duration=1,
            tick_scope="owner_turn_end",
        )

    def modify_stat(self, stat_name: str, value: float) -> float:
        if stat_name == "attack":
            return value - 1
        return value

    def modify_attack_actions_per_turn(self, value: int) -> int:
        return max(value, 6)


class SixBladeStyleSkill(Skill):
    def __init__(self) -> None:
        super().__init__(
            "six_blade_style",
            "六刀流",
            "普通技能：每回合最多 1 次；仅能在非乘骑状态且本回合未攻击时使用；本回合内攻 -1，普攻上限变为 6 次。",
            max_uses_per_turn=1,
        )

    def can_use(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any] | None = None) -> tuple[bool, str]:
        ok, reason = super().can_use(battle, actor, payload)
        if not ok:
            return ok, reason
        if battle.mounted_unit_for(actor) is not None:
            return False, "乘骑状态时不能使用六刀流。"
        if actor.attacks_used > 0 or "attack" in actor.actions_taken_this_turn:
            return False, "必须在本回合尚未普攻时使用六刀流。"
        return True, ""

    def execute(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> None:
        replace_status_by_name(battle, actor, SixBladeStyleStatus())
        battle.log(f"{actor.name} 进入了六刀流状态。")


class HealMountSkill(Skill):
    def __init__(self) -> None:
        super().__init__(
            "heal_mount",
            "治愈良驹",
            "普通技能：每回合最多 1 次；仅能在乘骑状态时使用；自己当前乘骑的摩托马回复 1/2 生命。",
            max_uses_per_turn=1,
        )

    def can_use(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any] | None = None) -> tuple[bool, str]:
        ok, reason = super().can_use(battle, actor, payload)
        if not ok:
            return ok, reason
        if battle.mounted_unit_for(actor) is None:
            return False, "只有乘骑状态时才能治疗良驹。"
        return True, ""

    def execute(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> None:
        mount = battle.mounted_unit_for(actor)
        if mount is None:
            raise ActionError("当前没有可治疗的摩托马。")
        battle.heal(HealContext(source=actor, target=mount, amount=0.5, action_name="治愈良驹"))


class TripleStrikeAttackTrait(Trait):
    def __init__(self) -> None:
        super().__init__("三连重斩", "可以选择一次普攻占用 3 次普攻次数；那次普攻伤害 +3 并半破魔。")

    def basic_attack_action_entries(self, battle: Battle, actor: HeroUnit) -> list[dict[str, Any]]:
        return [
            {
                "code": "attack_triple",
                "name": "三连重斩",
                "description": "普攻变体：占用 3 次普攻次数；这次普攻伤害 +3 并半破魔。",
                "attack_payload": {"attack_variant": "triple"},
            }
        ]

    def basic_attack_payload_metadata(self, battle: Battle, actor: Unit, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        if str((payload or {}).get("attack_variant") or "default") != "triple":
            return {}
        return {
            "attack_name": "三连重斩",
            "attack_cost": 3,
            "attack_bonus": 3,
            "half_ignore_shield": True,
            "attack_note": "这次普攻伤害 +3，并且半破魔。",
        }


class ArcAttackTrait(Trait):
    def __init__(self) -> None:
        super().__init__("弧形攻击", "普攻前先声明八码方向；正交方向打外侧整排 3 格，斜向打对应角上的 3 格弧面。")

    def selected_direction_code(self, payload: dict[str, Any] | None = None) -> str:
        return str((payload or {}).get("choice_code") or "").strip()

    def direction_cells(self, battle: Battle, actor: Unit, code: str) -> list[Position]:
        if actor.position is None:
            return []
        return [cell for cell in arc_direction_cells(actor.position, code) if battle.in_bounds(cell)]

    def valid_target_cells(self, battle: Battle, actor: Unit, target: Unit, code: str) -> list[Position]:
        allowed = {(cell.x, cell.y) for cell in self.direction_cells(battle, actor, code)}
        return [cell for cell in battle.unit_cells(target) if (cell.x, cell.y) in allowed]

    def basic_attack_payload_metadata(
        self,
        battle: Battle,
        actor: Unit,
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        code = self.selected_direction_code(payload)
        if not code:
            return {}
        return {"attack_cells": positions_to_dict(self.direction_cells(battle, actor, code))}

    def basic_attack_preview(self, battle: Battle, actor: Unit, payload: dict[str, Any] | None = None) -> Optional[dict[str, Any]]:
        preview_cells: list[Position] = []
        preview_choices: list[dict[str, Any]] = []
        target_ids: list[str] = []
        seen_target_ids: set[str] = set()
        seen_cells: set[tuple[int, int]] = set()
        for code, _, label in ARC_DIRECTION_CHOICES:
            direction_patterns: list[list[dict[str, int]]] = []
            for enemy in battle.enemy_units(actor.player_id):
                valid_cells = self.valid_target_cells(battle, actor, enemy, code)
                if not valid_cells:
                    continue
                if not battle.attack_target_allowed(actor, enemy, payload={"choice_code": code})[0]:
                    continue
                if enemy.unit_id not in seen_target_ids:
                    seen_target_ids.add(enemy.unit_id)
                    target_ids.append(enemy.unit_id)
                for cell in valid_cells:
                    if (cell.x, cell.y) not in seen_cells:
                        seen_cells.add((cell.x, cell.y))
                        preview_cells.append(cell)
                    direction_patterns.append([cell.to_dict()])
            preview_choices.append({"code": code, "label": label, "patterns": direction_patterns})
        return {
            "cells": positions_to_dict(preview_cells),
            "target_unit_ids": target_ids,
            "secondary_cells": [],
            "requires_target": True,
            "selection": {
                "mode": "choice_pattern",
                "choices": preview_choices,
                "ordered": False,
            },
        }

    def can_attack_target_with_payload(
        self,
        battle: Battle,
        actor: Unit,
        target: Unit,
        payload: dict[str, Any] | None = None,
    ) -> tuple[bool, str]:
        codes = [code for code, _, _ in ARC_DIRECTION_CHOICES]
        if payload is None:
            if any(self.valid_target_cells(battle, actor, target, code) for code in codes):
                return True, ""
            return False, "目标不在弧形普攻范围内。"
        code = self.selected_direction_code(payload)
        if not code:
            return False, "政宗普攻前需要先声明方向。"
        if code not in codes:
            return False, "所选普攻方向无效。"
        valid_cells = self.valid_target_cells(battle, actor, target, code)
        if not valid_cells:
            return False, "目标不在该方向的弧形普攻范围内。"
        if payload.get("x") is not None and payload.get("y") is not None:
            clicked = Position(int(payload["x"]), int(payload["y"]))
            target_cells = battle.unit_cells(target)
            if clicked in target_cells and clicked not in valid_cells:
                return False, "所点目标格不在该方向的弧形普攻范围内。"
        return True, ""


class MountedFreeLeapTrait(Trait):
    def __init__(self) -> None:
        super().__init__("乘骑飞跃", "乘骑状态时，每回合可以不用魔使用 1 次飞跃。")


class UnmountedCombatTrait(Trait):
    def __init__(self) -> None:
        super().__init__("下马战技", "下马后可格挡、反击，并且普攻造成伤害后回复 1/4 生命。")

    def bind(self, owner: HeroUnit) -> "UnmountedCombatTrait":
        super().bind(owner)
        owner.has_block_counter = True
        return self

    def allows_block_counter(self, battle: Battle, actor: Unit) -> bool:
        return battle.mounted_unit_for(actor) is None

    def on_after_damage(self, battle: Battle, ctx: DamageContext) -> None:
        owner = self.owner
        if owner is None or ctx.source is None or ctx.source.unit_id != owner.unit_id:
            return
        if ctx.is_skill or "attack" not in ctx.tags or ctx.cancelled or (ctx.raw_damage or 0) <= 0:
            return
        if battle.mounted_unit_for(owner) is not None:
            return
        battle.heal(HealContext(source=owner, target=owner, amount=0.25, action_name="攻击吸血"))


class MasamuneMountedStartTrait(Trait):
    def __init__(self) -> None:
        super().__init__("骑士开场坐骑", "出场时已经召唤出自己的摩托马，并且已经处于乘骑状态。")

    def on_enter_battle(self, battle: Battle) -> None:
        owner = self.owner
        if not isinstance(owner, HeroUnit) or owner.position is None:
            return
        if alive_owned_motor_horse(battle, owner) is not None:
            return
        mount = MotorHorseSummon(owner.player_id)
        mount.summoner_id = owner.unit_id
        mount.mount_owner_id = owner.unit_id
        mount.is_mount = True
        mount.can_act_on_entry_turn = True
        mount.turn_ready = True
        battle.add_unit(mount, owner.position)
        battle.set_mounted_state(owner, mount)


class MotorHorseSummon(AbstractHero):
    hero_code = "motor_horse"
    hero_name = "摩托马"
    role = "坐骑"
    attribute = "土"
    race = "召唤物"
    level = 1
    base_stats = Stats(attack=0, defense=5, speed=5, attack_range=1, mana=0)
    footprint_width = 1
    footprint_height = 2
    stat_minimums = {"attack": 0.0}
    raw_skill_text = "神速（每个己方回合可以免费使用一次）"
    raw_trait_text = "可乘骑"

    def __init__(self, player_id: int) -> None:
        super().__init__(player_id, is_summon=True)

    def build_skills(self) -> list[Skill]:
        return [FreeShensuSkill()]

    def build_traits(self) -> list[Trait]:
        return [RideableMountTrait()]


class MotorHorseSkill(Skill):
    def __init__(self) -> None:
        super().__init__(
            "motor_horse",
            "摩托马",
            "普通技能：召唤自己的坐骑摩托马；若已存在自己的摩托马，或仍在召回冷却中，则不能使用。",
            target_mode="self",
        )

    def can_use(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any] | None = None) -> tuple[bool, str]:
        ok, reason = super().can_use(battle, actor, payload)
        if not ok:
            return ok, reason
        if alive_owned_motor_horse(battle, actor) is not None:
            return False, "场上已经有自己的摩托马。"
        if actor.has_status("摩托马召回冷却"):
            return False, "摩托马仍在召回冷却中。"
        if actor.position is None:
            return False, "当前不在战场上。"
        test_mount = MotorHorseSummon(actor.player_id)
        test_mount.mount_owner_id = actor.unit_id
        test_mount.is_mount = True
        if not battle.can_place_unit(test_mount, actor.position, ignore=test_mount, mover=test_mount):
            return False, "当前位置无法召唤摩托马。"
        return True, ""

    def execute(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> None:
        if actor.position is None:
            raise ActionError("当前不在战场上。")
        mount = MotorHorseSummon(actor.player_id)
        mount.summoner_id = actor.unit_id
        mount.mount_owner_id = actor.unit_id
        mount.is_mount = True
        battle.summon_unit(mount, actor.position, summoner=actor)
        battle.set_mounted_state(actor, mount)

    def preview(self, battle: Battle, actor: HeroUnit) -> dict[str, Any]:
        return {
            "cells": [actor.position.to_dict()] if actor.position else [],
            "target_unit_ids": [actor.unit_id],
            "secondary_cells": [],
            "requires_target": False,
        }


class Masamune(AbstractHero):
    hero_code = "masamune"
    hero_name = "天位骑士。政宗"
    role = "骑士"
    attribute = "土"
    race = "人类"
    level = 4
    base_stats = Stats(attack=4, defense=3, speed=3, attack_range=1, mana=3)
    entry_footprint_width = 1
    entry_footprint_height = 2
    raw_skill_text = "摩托马（攻0守5速5；每个己方回合可以使用一次神速） 保护 六刀流（一回合一次；仅能在非乘骑状态并且当回合未攻击时使用；本回合内攻-1，攻击六次） 治愈良驹（一回合一次；仅能在乘骑时使用此；单位召唤的“摩托马”血+1/2）"
    raw_trait_text = "弧形攻击；乘骑状态时每回合可以不用魔使用一次飞跃；可选择一次攻击占用3次普攻次数，那次攻击伤害+3并且半破魔；此单位在下马后可以格挡，反击，并且攻击吸血"

    def build_skills(self) -> list[Skill]:
        return [
            MotorHorseSkill(),
            PassiveProtectionSkill(),
            SixBladeStyleSkill(),
            HealMountSkill(),
            MountedLeapSkill(),
        ]

    def build_traits(self) -> list[Trait]:
        return [
            ArcAttackTrait(),
            MountedFreeLeapTrait(),
            TripleStrikeAttackTrait(),
            UnmountedCombatTrait(),
            MasamuneMountedStartTrait(),
        ]


def combined_remote_rectangle_patterns(
    battle: Battle,
    actor: HeroUnit,
    sizes: list[tuple[int, int]],
) -> list[list[Position]]:
    patterns: list[list[Position]] = []
    seen: set[tuple[tuple[int, int], ...]] = set()
    for width, height in sizes:
        for pattern in remote_rectangle_patterns(battle, actor, width, height):
            key = pattern_signature(pattern)
            if key in seen:
                continue
            seen.add(key)
            patterns.append(pattern)
    patterns.sort(key=pattern_signature)
    return patterns


def area_patterns_preview(
    battle: Battle,
    actor: HeroUnit,
    patterns: list[list[Position]],
) -> dict[str, Any]:
    preview = pattern_selection_preview(patterns)
    cell_keys = {(cell["x"], cell["y"]) for cell in preview["cells"]}
    targets = [
        unit.unit_id
        for unit in battle.all_units()
        if any((cell.x, cell.y) in cell_keys for cell in battle.unit_cells(unit))
    ]
    preview.update({"target_unit_ids": targets, "secondary_cells": [], "requires_target": True})
    return preview


def resolve_area_damage(
    battle: Battle,
    actor: HeroUnit,
    *,
    action_name: str,
    cells: list[Position],
    attack_power: float,
    tags: set[str],
    enemy_only: bool = False,
    ignore_shield: bool = False,
    half_ignore_shield: bool = False,
    ignore_magic_immunity: bool = False,
    cannot_evade: bool = False,
    raw_damage: float | None = None,
) -> tuple[set[str], set[str]]:
    original_enemy_ids: set[str] = set()
    damaged_enemy_ids: set[str] = set()
    for unit in battle.units_at_cells(cells):
        if enemy_only and unit.player_id == actor.player_id:
            continue
        if unit.player_id != actor.player_id:
            original_enemy_ids.add(unit.unit_id)
        ctx = battle.resolve_damage(
            DamageContext(
                source=actor,
                target=unit,
                attack_power=attack_power,
                is_skill=True,
                action_name=action_name,
                ignore_shield=ignore_shield,
                half_ignore_shield=half_ignore_shield,
                ignore_magic_immunity=ignore_magic_immunity,
                cannot_evade=cannot_evade,
                area_cell_hits=battle.unit_hit_count_for_cells(unit, cells),
                raw_damage=raw_damage,
                tags=set(tags),
            )
        )
        if unit.player_id != actor.player_id and not ctx.cancelled and (ctx.raw_damage or 0) > 0:
            damaged_enemy_ids.add(unit.unit_id)
    return original_enemy_ids, damaged_enemy_ids


def maybe_queue_jade_reactive_bonus(
    battle: Battle,
    actor: HeroUnit,
    *,
    skill_code: str,
    payload: dict[str, Any],
    original_enemy_ids: set[str],
    damaged_enemy_ids: set[str],
) -> None:
    if not payload.get("enemy_reacted"):
        return
    if not original_enemy_ids or original_enemy_ids.issubset(damaged_enemy_ids):
        return
    for trait in actor.traits:
        if isinstance(trait, JadeReactiveOverclockTrait):
            trait.queue_skill_bonus(battle, skill_code)
            return


class JadeReactiveOverclockTrait(Trait):
    def __init__(self) -> None:
        super().__init__(
            "受阻超频",
            "带有伤害的技能被连锁后，只要有任意原目标最终没受到伤害，则从下个己方回合开始该技能的使用次数永久 +1；每回合每个技能最多触发一次。",
        )
        self.pending_skill_bonuses: dict[str, int] = {}
        self.triggered_skill_codes: set[str] = set()

    def queue_skill_bonus(self, battle: Battle, skill_code: str) -> None:
        owner = self.owner
        if owner is None or skill_code in self.triggered_skill_codes:
            return
        self.triggered_skill_codes.add(skill_code)
        self.pending_skill_bonuses[skill_code] = self.pending_skill_bonuses.get(skill_code, 0) + 1
        skill = owner.get_skill(skill_code)
        battle.log(f"{owner.name} 的【{skill.name}】将在下个己方回合开始时永久增加 1 次使用次数。")

    def apply_bonus_to_skill(self, battle: Battle, skill_code: str, amount: int) -> None:
        owner = self.owner
        if owner is None or amount <= 0:
            return
        skill = owner.get_skill(skill_code)
        if isinstance(skill, WindowChargeSkill):
            skill.increase_window_uses(amount, apply_to_active_window=True)
        elif skill.max_uses_per_turn is not None:
            skill.max_uses_per_turn += amount
        else:
            return
        battle.log(f"{owner.name} 的【{skill.name}】使用次数永久 +{amount}。")

    def on_owner_turn_start(self, battle: Battle) -> None:
        pending = dict(self.pending_skill_bonuses)
        self.pending_skill_bonuses.clear()
        self.triggered_skill_codes.clear()
        for skill_code, amount in pending.items():
            self.apply_bonus_to_skill(battle, skill_code, amount)


class JadeMachineGunSkill(MachineGunSkill):
    def execute(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> None:
        cells = self.chosen_line(battle, actor, payload)
        original_enemy_ids, damaged_enemy_ids = resolve_area_damage(
            battle,
            actor,
            action_name="机枪",
            cells=cells,
            attack_power=actor.stat("attack"),
            tags={"skill", "attack", "machine_gun"},
            enemy_only=True,
        )
        maybe_queue_jade_reactive_bonus(
            battle,
            actor,
            skill_code=self.code,
            payload=payload,
            original_enemy_ids=original_enemy_ids,
            damaged_enemy_ids=damaged_enemy_ids,
        )


class MissileSkill(WindowChargeSkill):
    def __init__(self) -> None:
        super().__init__(
            "missile",
            "导弹",
            "普通技能：每 2 轮最多 3 次，远程选择 2*2 区域；按当前攻造成伤害。",
            window_rounds=2,
            window_uses=3,
            target_mode="cell",
        )

    def patterns(self, battle: Battle, actor: HeroUnit) -> list[list[Position]]:
        return remote_rectangle_patterns(battle, actor, 2, 2)

    def chosen_cells(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> list[Position]:
        return match_payload_pattern(payload, self.patterns(battle, actor))

    def execute(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> None:
        cells = self.chosen_cells(battle, actor, payload)
        original_enemy_ids, damaged_enemy_ids = resolve_area_damage(
            battle,
            actor,
            action_name="导弹",
            cells=cells,
            attack_power=actor.stat("attack"),
            tags={"skill", "missile"},
        )
        maybe_queue_jade_reactive_bonus(
            battle,
            actor,
            skill_code=self.code,
            payload=payload,
            original_enemy_ids=original_enemy_ids,
            damaged_enemy_ids=damaged_enemy_ids,
        )

    def preview(self, battle: Battle, actor: HeroUnit) -> dict[str, Any]:
        return area_patterns_preview(battle, actor, self.patterns(battle, actor))

    def get_target_cells_for_payload(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> list[Position]:
        return self.chosen_cells(battle, actor, payload)

    def get_target_units_for_payload(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> list[HeroUnit]:
        return battle.units_at_cells(self.chosen_cells(battle, actor, payload))  # type: ignore[return-value]


class IonShieldSkill(MultiTargetChainShieldSkill):
    shield_amount = 1

    def __init__(self) -> None:
        super().__init__(
            "ion_shield",
            "离子盾",
            "被动技能：连锁速度 2，不费魔，每回合最多 2 次；效果与墙相同，可选择多个当前受影响的己方目标，各获得 1 层临时护盾，只持续到这次连锁结束。",
            mana_cost=0,
            max_uses_per_turn=2,
            target_mode="ally",
            timing="passive",
        )

    def execute(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> None:
        raise ActionError("离子盾只能通过连锁使用。")

    def can_react_to(self, battle: Battle, actor: HeroUnit, queued_action: QueuedAction) -> tuple[bool, str]:
        ok, reason = super().can_react_to(battle, actor, queued_action)
        if not ok:
            return ok, reason
        if queued_action.source_player_id == actor.player_id:
            return False, "只能对敌方动作连锁。"
        if not self.selectable_targets(battle, actor, queued_action):
            return False, "当前动作没有影响到可施放离子盾的己方目标。"
        return True, ""


class LaserSkill(Skill):
    def __init__(self) -> None:
        super().__init__(
            "laser",
            "激光",
            "普通技能：冷却 3 轮，远程选择 2*10 区域；按当前攻造成伤害。",
            cooldown_turns=6,
            target_mode="cell",
        )

    def patterns(self, battle: Battle, actor: HeroUnit) -> list[list[Position]]:
        return combined_remote_rectangle_patterns(battle, actor, [(2, 10), (10, 2)])

    def chosen_cells(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> list[Position]:
        return match_payload_pattern(payload, self.patterns(battle, actor))

    def execute(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> None:
        cells = self.chosen_cells(battle, actor, payload)
        original_enemy_ids, damaged_enemy_ids = resolve_area_damage(
            battle,
            actor,
            action_name="激光",
            cells=cells,
            attack_power=actor.stat("attack"),
            tags={"skill", "laser"},
        )
        maybe_queue_jade_reactive_bonus(
            battle,
            actor,
            skill_code=self.code,
            payload=payload,
            original_enemy_ids=original_enemy_ids,
            damaged_enemy_ids=damaged_enemy_ids,
        )

    def preview(self, battle: Battle, actor: HeroUnit) -> dict[str, Any]:
        return area_patterns_preview(battle, actor, self.patterns(battle, actor))

    def get_target_cells_for_payload(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> list[Position]:
        return self.chosen_cells(battle, actor, payload)

    def get_target_units_for_payload(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> list[HeroUnit]:
        return battle.units_at_cells(self.chosen_cells(battle, actor, payload))  # type: ignore[return-value]


class DelayedCooldownMultiTargetWallSkill(MultiTargetChainShieldSkill):
    def __init__(
        self,
        code: str,
        name: str,
        description: str,
        *,
        mana_cost: float,
        max_uses_per_turn: int,
        cooldown_turns: int,
    ) -> None:
        super().__init__(
            code,
            name,
            description,
            mana_cost=mana_cost,
            max_uses_per_turn=max_uses_per_turn,
            cooldown_turns=cooldown_turns,
            target_mode="ally",
            timing="passive",
        )
        self.cooldown_pending = False

    def prepay_resources(
        self,
        battle: Battle,
        actor: HeroUnit,
        payload: dict[str, Any] | None = None,
    ) -> None:
        actor.spend_mana(self.mana_cost_for_payload(battle, actor, payload))
        self.uses_this_turn += 1
        self.uses_this_battle += 1
        if self.cooldown_turns > 0:
            self.cooldown_pending = True

    def on_owner_turn_start(self, battle: Battle) -> None:
        super().on_owner_turn_start(battle)
        if self.cooldown_pending and self.cooldown_turns > 0 and self.cooldown_remaining <= 0:
            self.cooldown_remaining = max(self.cooldown_turns - 1, 0)
            self.cooldown_pending = False

    def to_public_dict(self, battle: Battle) -> dict[str, Any]:
        data = super().to_public_dict(battle)
        data["cooldown_pending"] = self.cooldown_pending
        return data


class QuantumShieldSkill(DelayedCooldownMultiTargetWallSkill):
    shield_amount = 1

    def __init__(self) -> None:
        super().__init__(
            "quantum_shield",
            "量子盾",
            "被动技能：连锁速度 2，不费魔，每回合最多 3 次；效果与墙相同。只要本轮使用过，下一轮整轮不能使用，再下一轮恢复可用。",
            mana_cost=0,
            max_uses_per_turn=3,
            cooldown_turns=4,
        )

    def execute(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> None:
        raise ActionError("量子盾只能通过连锁使用。")

    def can_react_to(self, battle: Battle, actor: HeroUnit, queued_action: QueuedAction) -> tuple[bool, str]:
        ok, reason = super().can_react_to(battle, actor, queued_action)
        if not ok:
            return ok, reason
        if queued_action.source_player_id == actor.player_id:
            return False, "只能对敌方动作连锁。"
        if not self.selectable_targets(battle, actor, queued_action):
            return False, "当前动作没有影响到可施放量子盾的己方目标。"
        return True, ""


class MechEnhancementSkill(Skill):
    def __init__(self) -> None:
        super().__init__(
            "mech_enhancement",
            "机甲强化",
            "普通技能：冷却 3 轮；自己守 +1，持续 2 轮，并回复 1/2 生命。",
            cooldown_turns=6,
            target_mode="self",
        )

    def execute(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> None:
        replace_status_by_name(
            battle,
            actor,
            StatModifierStatus(
                "机甲强化",
                defense_delta=1,
                duration=2,
                tick_scope="owner_turn_start",
                description="守 +1。",
            ),
        )
        battle.heal(HealContext(source=actor, target=actor, amount=0.5, action_name="机甲强化"))
        battle.log(f"{actor.name} 进入机甲强化状态。")

    def preview(self, battle: Battle, actor: HeroUnit) -> dict[str, Any]:
        return {
            "cells": [actor.position.to_dict()] if actor.position else [],
            "target_unit_ids": [actor.unit_id],
            "secondary_cells": [],
            "requires_target": False,
        }


class PlasmaThrusterSkill(Skill):
    def __init__(self) -> None:
        super().__init__(
            "plasma_thruster",
            "等离子喷射系统",
            "普通技能：每回合最多 1 次，直线飞行移动 5 格；若该方向会撞到边界，则可以停在边界上的最后一格，但不能停在有单位的位置。",
            max_uses_per_turn=1,
            target_mode="cell",
        )

    def selectable_destinations(self, battle: Battle, actor: HeroUnit) -> list[Position]:
        if actor.position is None:
            return []
        destinations: list[Position] = []
        seen: set[tuple[int, int]] = set()
        for dx, dy in ALL_DIRECTIONS:
            last_in_bounds: Position | None = None
            for step in range(1, 6):
                candidate = actor.position.offset(dx * step, dy * step)
                if not battle.in_bounds(candidate):
                    break
                last_in_bounds = candidate
            if last_in_bounds is None or last_in_bounds == actor.position:
                continue
            if not battle.can_place_unit(actor, last_in_bounds, ignore=actor, mover=actor):
                continue
            key = position_key(last_in_bounds)
            if key in seen:
                continue
            seen.add(key)
            destinations.append(last_in_bounds)
        destinations.sort(key=lambda cell: (cell.y, cell.x))
        return destinations

    def execute(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> None:
        destination = payload_position(payload)
        if destination not in self.selectable_destinations(battle, actor):
            raise ActionError("该落点不是等离子喷射系统的合法目标。")
        battle.move_unit(
            actor,
            destination,
            via_skill=True,
            straight_only=True,
            ignore_units=True,
            max_distance=5,
            tags={self.code},
        )

    def preview(self, battle: Battle, actor: HeroUnit) -> dict[str, Any]:
        return {
            "cells": positions_to_dict(self.selectable_destinations(battle, actor)),
            "target_unit_ids": [],
            "secondary_cells": [],
            "requires_target": True,
        }


class StanceFieldEffect(BattleFieldEffect):
    def __init__(self, owner_unit_id: str, owner_player_id: int) -> None:
        super().__init__("立场", "翡翠周围 7*7 内的己方单位在下个对方回合中不受到伤害。", duration=None)
        self.owner_unit_id = owner_unit_id
        self.owner_player_id = owner_player_id
        self.armed = False

    def owner_unit(self, battle: Battle) -> HeroUnit | None:
        owner = battle.units.get(self.owner_unit_id)
        if owner is None or not isinstance(owner, HeroUnit) or not owner.alive or owner.position is None or owner.banished:
            return None
        return owner

    def affected_cells(self, battle: Battle) -> list[Position]:
        owner = self.owner_unit(battle)
        if owner is None:
            return []
        return square_around_cells(battle, battle.unit_cells(owner), radius=3)

    def board_marker(self, battle: Battle) -> str:
        return "立"

    def on_before_damage(self, battle: Battle, ctx: DamageContext) -> None:
        if not self.armed:
            return
        owner = self.owner_unit(battle)
        if owner is None or ctx.target.player_id != self.owner_player_id or ctx.target.unit_id == owner.unit_id:
            return
        protected_keys = {position_key(cell) for cell in self.affected_cells(battle)}
        if not any(position_key(cell) in protected_keys for cell in battle.unit_cells(ctx.target)):
            return
        ctx.cancelled = True
        ctx.reason = f"{ctx.target.name} 受到立场保护，这次伤害无效。"

    def on_any_turn_end(self, battle: Battle, ended_player_id: int) -> None:
        owner = self.owner_unit(battle)
        if owner is None:
            battle.remove_field_effect(self)
            return
        if not self.armed and ended_player_id == self.owner_player_id:
            self.armed = True
            return
        if self.armed and ended_player_id != self.owner_player_id:
            battle.remove_field_effect(self)


class StanceSkill(Skill):
    def __init__(self) -> None:
        super().__init__(
            "stance",
            "立场",
            "普通技能：冷却 2 轮；从这个回合结束后的第一个对方回合开始，到自己下个回合开始前，周围 7*7 内的其他己方单位不受到伤害。",
            cooldown_turns=4,
            target_mode="self",
        )

    def execute(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> None:
        for effect in list(battle.field_effects):
            if isinstance(effect, StanceFieldEffect) and effect.owner_unit_id == actor.unit_id:
                battle.remove_field_effect(effect)
        battle.add_field_effect(StanceFieldEffect(actor.unit_id, actor.player_id))
        battle.log(f"{actor.name} 展开了立场。")

    def preview(self, battle: Battle, actor: HeroUnit) -> dict[str, Any]:
        return {
            "cells": [actor.position.to_dict()] if actor.position else [],
            "target_unit_ids": [actor.unit_id],
            "secondary_cells": [],
            "requires_target": False,
        }


class Jade(AbstractHero):
    hero_code = "jade"
    hero_name = "翡翠"
    role = "勇者"
    attribute = "钢"
    race = "机甲"
    level = 8
    base_stats = Stats(attack=4, defense=4, speed=3, attack_range=3, mana=0)
    raw_skill_text = "机枪（一回合一次） 导弹（每2轮可以使用3次） 离子盾（一回合可以使用两次，可以对队友使用） 激光（3轮一次，2*10） 量子盾（一回合可以使用3次，可以对队友使用；若本轮使用过则下一轮不能使用，再下一轮恢复） 机甲强化（3轮一次；守＋1，2轮；血＋1/2） 等离子喷射系统（1回合一次；移动直线5格） 立场（2轮一次；使用以后周围7*7内己方单位下回合内不受到伤害）"
    raw_trait_text = "飞行；在带有伤害的技能被对方的技能连锁，未能造成后，从下个己方回合开始那个技能的回合使用次数+1，每回合每个技能此效果只能触发一次"

    def build_skills(self) -> list[Skill]:
        return [
            JadeMachineGunSkill(),
            MissileSkill(),
            IonShieldSkill(),
            LaserSkill(),
            QuantumShieldSkill(),
            MechEnhancementSkill(),
            PlasmaThrusterSkill(),
            StanceSkill(),
        ]

    def build_traits(self) -> list[Trait]:
        return [FlyingTrait(), JadeReactiveOverclockTrait()]


class ManaPointCostSkill(Skill):
    def __init__(
        self,
        code: str,
        name: str,
        description: str,
        *,
        mana_point_cost: float,
        cooldown_turns: int = 0,
        max_uses_per_turn: int | None = None,
        max_uses_per_battle: int | None = None,
        target_mode: str = "none",
        timing: str = "active",
    ) -> None:
        super().__init__(
            code,
            name,
            description,
            mana_cost=0,
            cooldown_turns=cooldown_turns,
            max_uses_per_turn=max_uses_per_turn,
            max_uses_per_battle=max_uses_per_battle,
            target_mode=target_mode,  # type: ignore[arg-type]
            timing=timing,  # type: ignore[arg-type]
        )
        self.mana_point_cost = mana_point_cost

    def mana_point_cost_for_payload(
        self,
        battle: Battle,
        actor: HeroUnit,
        payload: dict[str, Any] | None = None,
    ) -> float:
        return self.mana_point_cost

    def can_use(
        self,
        battle: Battle,
        actor: HeroUnit,
        payload: dict[str, Any] | None = None,
    ) -> tuple[bool, str]:
        ok, reason = super().can_use(battle, actor, payload)
        if not ok:
            return ok, reason
        if actor.mana_points + 1e-9 < self.mana_point_cost_for_payload(battle, actor, payload):
            return False, "魔力点不足。"
        return True, ""

    def can_react_with_payload(
        self,
        battle: Battle,
        actor: HeroUnit,
        queued_action: QueuedAction,
        payload: dict[str, Any] | None = None,
    ) -> tuple[bool, str]:
        ok, reason = super().can_react_with_payload(battle, actor, queued_action, payload)
        if not ok:
            return ok, reason
        if actor.mana_points + 1e-9 < self.mana_point_cost_for_payload(battle, actor, payload):
            return False, "魔力点不足。"
        return True, ""

    def prepay_resources(
        self,
        battle: Battle,
        actor: HeroUnit,
        payload: dict[str, Any] | None = None,
    ) -> None:
        self.sync_turn_scope(battle)
        actor.spend_mana_points(self.mana_point_cost_for_payload(battle, actor, payload))
        self.uses_this_turn += 1
        self.uses_this_battle += 1
        if self.cooldown_turns:
            self.cooldown_remaining = self.cooldown_turns

    def mana_cost_text(self) -> str | None:
        value = int(self.mana_point_cost) if float(self.mana_point_cost).is_integer() else self.mana_point_cost
        return f"费 {value} 魔力点"

    def to_public_dict(self, battle: Battle) -> dict[str, Any]:
        data = super().to_public_dict(battle)
        data["mana_point_cost"] = self.mana_point_cost
        return data


class StandardCloneSummon(AbstractHero):
    hero_code = "standard_clone"
    hero_name = "分身"
    role = "分身"
    attribute = ""
    race = ""
    level = 1
    base_stats = Stats(attack=1, defense=1, speed=1, attack_range=1, mana=0)
    raw_skill_text = "分身"
    raw_trait_text = ""

    def __init__(self, player_id: int, source: HeroUnit) -> None:
        self.hero_name = source.name
        self.hero_title = source.title
        self.role = source.role
        self.attribute = source.attribute
        self.race = source.race
        self.level = source.level
        self.base_stats = Stats(
            attack=int(source.stat("attack")),
            defense=int(source.stat("defense")),
            speed=int(source.stat("speed")),
            attack_range=int(source.targeting_range()),
            mana=source.max_mana(),
        )
        self.base_footprint_offsets = list(source.base_footprint_offsets)
        self.footprint_offsets = list(source.footprint_offsets)
        super().__init__(player_id, is_summon=True, is_clone=True)
        self.max_health = source.max_health
        self.current_hp = min(source.current_hp, self.max_health)
        self.current_mana = source.current_mana
        self.allow_unbounded_mana = source.allow_unbounded_mana
        self.clamp_mana()
        self.mana_points = source.mana_points
        self.cannot_attack = True
        self.cannot_use_skills = True

    def build_skills(self) -> list[Skill]:
        return []

    def build_traits(self) -> list[Trait]:
        return []


class SplitSkill(Skill):
    def __init__(self) -> None:
        super().__init__(
            "split",
            "分身",
            "普通技能：费 1.5 魔，每回合最多 1 次，在范内制造 1 个分身；本体本回合不能继续行动，分身登场当回合不能行动，并与新分身交换位置。",
            mana_cost=1.5,
            max_uses_per_turn=1,
            target_mode="cell",
        )

    def _clone_probe(self, actor: HeroUnit) -> StandardCloneSummon:
        return StandardCloneSummon(actor.player_id, actor)

    def execute(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> None:
        destination = payload_position(payload)
        ensure_distance(actor, destination, actor.targeting_range())
        if actor.position is None:
            raise ActionError("单位不在战场上。")
        clone = self._clone_probe(actor)
        if not battle.can_place_unit(clone, destination):
            raise ActionError("分身位置已被占用。")
        original_position = actor.position
        battle.summon_unit(clone, destination, summoner=actor)
        actor.position, clone.position = clone.position, original_position
        actor.turn_ready = False
        battle.log(f"{actor.name} 使用分身制造了分身，并与新分身交换了位置。")

    def preview(self, battle: Battle, actor: HeroUnit) -> dict[str, Any]:
        if actor.position is None:
            return {"cells": [], "target_unit_ids": [], "secondary_cells": [], "requires_target": True}
        probe = self._clone_probe(actor)
        cells = [
            Position(x, y)
            for x in range(battle.width)
            for y in range(battle.height)
            if battle.unit_distance_to_cell(actor, Position(x, y)) <= actor.targeting_range()
            and battle.can_place_unit(probe, Position(x, y))
        ]
        return {"cells": positions_to_dict(cells), "target_unit_ids": [], "secondary_cells": [], "requires_target": True}


class MagneticWaveSkill(ManaPointCostSkill):
    def __init__(self) -> None:
        super().__init__(
            "magnetic_wave",
            "磁力波",
            "随时使用技能：费 2 魔力点，每回合最多 1 次，远程选择完整 3*3 区域；按当前攻造成伤害，被命中的单位本回合不能行动。",
            mana_point_cost=2,
            max_uses_per_turn=1,
            target_mode="cell",
            timing="instant",
        )

    def patterns(self, battle: Battle, actor: HeroUnit) -> list[list[Position]]:
        return remote_rectangle_patterns(battle, actor, 3, 3)

    def chosen_cells(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> list[Position]:
        return match_payload_pattern(payload, self.patterns(battle, actor))

    def execute(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> None:
        cells = self.chosen_cells(battle, actor, payload)
        for unit in battle.units_at_cells(cells):
            damage_ctx = battle.resolve_damage(
                DamageContext(
                    source=actor,
                    target=unit,
                    attack_power=actor.stat("attack"),
                    is_skill=True,
                    action_name="磁力波",
                    area_cell_hits=battle.unit_hit_count_for_cells(unit, cells),
                    tags={"skill", "magnetic_wave"},
                )
            )
            if (
                unit.alive
                and damage_followup_effect_applies(damage_ctx)
                and unit.player_id == battle.active_player
                and battle.unit_belongs_to_current_turn(unit)
                and unit.turn_ready
            ):
                unit.turn_ready = False
                battle.log(f"{unit.name} 受到磁力波影响，本回合不能行动。")

    def preview(self, battle: Battle, actor: HeroUnit) -> dict[str, Any]:
        return area_patterns_preview(battle, actor, self.patterns(battle, actor))

    def get_target_cells_for_payload(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> list[Position]:
        return self.chosen_cells(battle, actor, payload)

    def get_target_units_for_payload(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> list[HeroUnit]:
        return battle.units_at_cells(self.chosen_cells(battle, actor, payload))  # type: ignore[return-value]


class NSkill(ManaPointCostSkill):
    def __init__(self) -> None:
        super().__init__(
            "n_skill",
            "N",
            "主动技能：费 1 魔力点，自己魔 +1。",
            mana_point_cost=1,
            target_mode="self",
        )

    def execute(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> None:
        gained = actor.gain_mana(1)
        battle.log(f"{actor.name} 使用【N】，魔力 +{gained}。")

    def preview(self, battle: Battle, actor: HeroUnit) -> dict[str, Any]:
        return {
            "cells": [actor.position.to_dict()] if actor.position else [],
            "target_unit_ids": [actor.unit_id],
            "secondary_cells": [],
            "requires_target": False,
        }


class NAttackManaPointTrait(Trait):
    def __init__(self) -> None:
        super().__init__("攻击魔力点+1", "每次声明普攻时，魔力点 +1。")

    def on_owner_action_declared(self, battle: Battle, action_type: str, payload: dict[str, Any]) -> None:
        if self.owner is None or action_type != "attack":
            return
        gained = self.owner.gain_mana_points(1)
        if gained > 0:
            battle.log(f"{self.owner.name} 因声明普攻获得了 {gained} 点魔力点。")


class NAttackCountTrait(Trait):
    def __init__(self) -> None:
        super().__init__("攻击数=魔+1", "自己的回合开始时，按当前魔决定本回合攻击次数。")
        self.snapshot_attack_count = 1

    def on_owner_turn_start(self, battle: Battle) -> None:
        if self.owner is None:
            return
        self.snapshot_attack_count = max(1, int(math.floor(self.owner.current_mana + 1e-9)) + 1)

    def modify_attack_actions_per_turn(self, value: int) -> int:
        return max(value, self.snapshot_attack_count)


class UnlimitedManaTrait(Trait):
    def __init__(self) -> None:
        super().__init__("魔无上限", "当前魔和魔上限都不受基础魔值封顶。")

    def bind(self, owner: HeroUnit) -> "UnlimitedManaTrait":
        super().bind(owner)
        owner.allow_unbounded_mana = True
        return self


class NManaGuardTrait(Trait):
    def __init__(self) -> None:
        super().__init__("魔护体", "当前魔大于 0 时，伤害无效并改为失去 1 点魔。")

    def on_before_damage(self, battle: Battle, ctx: DamageContext) -> None:
        if self.owner is None or ctx.target.unit_id != self.owner.unit_id:
            return
        if self.owner.current_mana <= 0:
            return
        self.owner.spend_mana(1)
        ctx.cancelled = True
        ctx.preserve_followup_effects = True
        ctx.reason = f"{self.owner.name} 的魔抵消了这次伤害，并失去 1 点魔。"
        battle.emit_defense_visual_event(
            source=ctx.source,
            target=self.owner,
            action_name=ctx.action_name,
            defense_reason="mana_guard",
        )


class N(AbstractHero):
    hero_code = "n"
    hero_name = "N"
    role = "勇者"
    attribute = "光"
    race = "人类"
    level = 4
    base_stats = Stats(attack=2, defense=3, speed=3, attack_range=1, mana=2)
    raw_skill_text = "保护 穿刺 分身 吸魔 磁力波（一回合一次；随时使用；3*3；魔力点-2；被击中单位本回合不能行动） N（魔力点-1；魔+1）"
    raw_trait_text = "攻击魔力点+1；每回合开始时决定攻击数=魔+1；魔无上限；魔大于0时不受到伤害，受到伤害时魔-1"

    def build_skills(self) -> list[Skill]:
        return [
            PassiveProtectionSkill(),
            PierceSkill(),
            SplitSkill(),
            DrainManaSkill(),
            MagneticWaveSkill(),
            NSkill(),
        ]

    def build_traits(self) -> list[Trait]:
        return [
            NAttackManaPointTrait(),
            NAttackCountTrait(),
            UnlimitedManaTrait(),
            NManaGuardTrait(),
        ]
