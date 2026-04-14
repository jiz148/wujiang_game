from __future__ import annotations

import random
from typing import Any, Callable, Iterable

from wujiang.engine.core import (
    ActionError,
    Battle,
    BattleFieldEffect,
    DamageContext,
    HealContext,
    HeroUnit,
    Position,
    Skill,
    Stats,
    StatusEffect,
    TargetContext,
    Trait,
)
from wujiang.heroes.base import AbstractHero
from wujiang.heroes.common import (
    AttackCountTrait,
    FlagStatus,
    HardenSkill,
    KnockbackSkill,
    LightWallSkill,
    ShensuSkill,
    StealthSkill,
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
    def __init__(self, *, duration: int = 4) -> None:
        super().__init__("水之波动", "攻、守、速、范、魔上限 +1。", duration=duration, tick_scope="any_turn_end")

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
            duration=2,
        )
        self.owner_unit_id = owner_unit_id
        self.cells = {(cell.x, cell.y) for cell in cells}

    def affected_cells(self, battle: Battle) -> list[Position]:
        return [Position(x, y) for x, y in sorted(self.cells)]

    def board_marker(self, battle: Battle) -> str:
        return "植"

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
            if unit.alive and (not damage_ctx.cancelled or damage_ctx.shield_consumed):
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
        replace_status_by_name(battle, actor, AllStatsPlusStatus(duration=4))
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
                if weather_name is not None:
                    battle.remove_field_effect(effect)
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
            battle.add_field_effect(SandstormWeatherEffect(duration=2))

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
        gained = owner.gain_mana(0.25)
        if gained:
            battle.log(f"{owner.name} 因沙尘自然回魔，获得 {gained} 点魔。")
        battle.heal(HealContext(source=owner, target=owner, amount=0.25, action_name="自然回复", tags={"natural_recovery"}))


class NaturalManaRecoveryTrait(Trait):
    def __init__(self) -> None:
        super().__init__("自然回魔", "每个自己的己方回合开始时魔 +1/4，最多到当前魔上限。")

    def on_owner_turn_start(self, battle: Battle) -> None:
        if self.owner is None:
            return
        gained = self.owner.gain_mana(0.25)
        if gained:
            battle.log(f"{self.owner.name} 自然回魔，获得 {gained} 点魔。")


class RockGodSandstormAura(BattleFieldEffect):
    weather_name = "沙尘"

    def __init__(self, owner_unit_id: str) -> None:
        super().__init__("岩神沙尘", "岩神每个占用格周围 9*9 的局部沙尘天气。", duration=None)
        self.owner_unit_id = owner_unit_id

    def get_owner_unit(self, battle: Battle) -> HeroUnit | None:
        unit = battle.units.get(self.owner_unit_id)
        if unit is None or not unit.alive or unit.position is None or unit.banished:
            return None
        return unit  # type: ignore[return-value]

    def affected_cells(self, battle: Battle) -> list[Position]:
        owner = self.get_owner_unit(battle)
        if owner is None:
            return []
        return square_around_cells(battle, battle.unit_cells(owner), radius=4)

    def board_marker(self, battle: Battle) -> str:
        return "沙"

    def merge_into_existing(self, battle: Battle, existing_effects: list[BattleFieldEffect]) -> bool:
        for effect in existing_effects:
            if isinstance(effect, RockGodSandstormAura) and effect.owner_unit_id == self.owner_unit_id:
                return True
        return False

    def on_any_turn_end(self, battle: Battle, ended_player_id: int) -> None:
        owner = self.get_owner_unit(battle)
        if owner is None:
            battle.remove_field_effect(self)
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
                    source=owner,
                    target=unit,
                    attack_power=0,
                    is_skill=False,
                    action_name="局部沙尘",
                    from_field_effect=True,
                    cannot_evade=True,
                    raw_damage=damage,
                    tags={"weather", "sandstorm", "rock_god_aura"},
                )
            )


class RockGodSandstormTrait(Trait):
    def __init__(self) -> None:
        super().__init__("局部沙尘", "周围 9*9 天气变为沙尘；多格身体按每个占用格周围 9*9 的并集计算。")

    def _ensure_aura(self, battle: Battle) -> None:
        owner = self.owner
        if owner is None:
            return
        if any(isinstance(effect, RockGodSandstormAura) and effect.owner_unit_id == owner.unit_id for effect in battle.field_effects):
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
            if isinstance(effect, RockGodSandstormAura) and effect.owner_unit_id == owner.unit_id:
                battle.remove_field_effect(effect)


class RockAbsorbStatStatus(StatusEffect):
    def __init__(self, stat_name: str, delta: int, *, duration: int = 2) -> None:
        label = RockAbsorbSkill.stat_labels()[stat_name]
        sign = "+" if delta > 0 else ""
        super().__init__(
            "岩吸",
            f"{label} {sign}{delta}。",
            duration=duration,
            tick_scope="any_turn_end",
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
        super().__init__("岩吸占格", "岩吸临时增加占格；持续结束后恢复 2*2。", duration=2, tick_scope="any_turn_end")

    def on_removed(self, battle: Battle) -> None:
        owner = self.owner
        if owner is None or owner.position is None:
            return
        current_offsets = list(owner.footprint_offsets)
        current_position = owner.position
        base_offsets = list(owner.base_footprint_offsets)
        current_cells = battle.unit_cells(owner)
        candidates: list[Position] = [current_position]
        if current_cells:
            min_x = min(cell.x for cell in current_cells)
            min_y = min(cell.y for cell in current_cells)
            candidates.append(Position(min_x, min_y))
            for cell in current_cells:
                for dx, dy in base_offsets:
                    candidates.append(Position(cell.x - dx, cell.y - dy))
        for x in range(battle.width):
            for y in range(battle.height):
                candidates.append(Position(x, y))
        seen: set[tuple[int, int]] = set()
        unique = []
        for candidate in candidates:
            key = position_key(candidate)
            if key in seen:
                continue
            seen.add(key)
            unique.append(candidate)
        unique.sort(key=lambda cell: current_position.distance_to(cell))
        owner.set_footprint_offsets(base_offsets)
        for candidate in unique:
            if battle.can_place_unit(owner, candidate, ignore=owner, mover=owner):
                owner.position = candidate
                battle.log(f"{owner.name} 的岩吸占格恢复为 2*2。")
                return
        owner.set_footprint_offsets(current_offsets)
        battle.log(f"{owner.name} 的岩吸占格暂时无法恢复为 2*2。")


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


class RockAbsorbSkill(Skill):
    def __init__(self) -> None:
        super().__init__(
            "rock_absorb",
            "岩吸",
            "普通技能：每回合最多 1 次，破魔；选择一种能力值，吸取局部沙尘中除自己外所有单位的该能力值，并按数量增加自身能力值和占格。",
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
        for target in targets:
            ctx = battle.validate_target(
                actor,
                target,
                action_name="岩吸",
                is_skill=True,
                is_hostile=target.player_id != actor.player_id,
                ignore_shield=True,
                tags={"skill", "rock_absorb"},
            )
            if ctx.cancelled:
                if ctx.reason:
                    battle.log_public_event(ctx.reason, source=actor, target=target)
                continue
            replace_status_by_name(battle, target, RockAbsorbStatStatus(stat_name, -1))
            if stat_name == "mana":
                target.current_mana = round(max(0.0, target.current_mana - 1), 2)
                target.clamp_mana()
            battle.log(f"{target.name} 受到岩吸影响，{self.stat_labels()[stat_name]} -1。")
        if gain:
            replace_status_by_name(battle, actor, RockAbsorbStatStatus(stat_name, gain))
            if stat_name == "mana":
                actor.current_mana = round(actor.current_mana + gain, 2)
                actor.clamp_mana()
        if selected_cells:
            actor.set_footprint_cells([*battle.unit_cells(actor), *selected_cells])
            replace_status_by_name(battle, actor, RockAbsorbFootprintStatus())
            battle.log(f"{actor.name} 因岩吸增加了 {len(selected_cells)} 个占格。")

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

    def ignores_shield_for_payload(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> bool:
        return True


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
        for impact in impacts:
            cells = impact_area(battle, impact)
            for unit in battle.units_at_cells(cells):
                battle.resolve_damage(
                    DamageContext(
                        source=actor,
                        target=unit,
                        attack_power=attack_power,
                        is_skill=True,
                        action_name="岩石炮",
                        area_cell_hits=battle.unit_hit_count_for_cells(unit, cells),
                        tags={"skill", "rock_cannon"},
                    )
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
        selected, direction, _ = self.validate_selection(battle, actor, payload)
        cells: list[Position] = []
        for impact in self.impact_positions(battle, actor, selected, direction):
            cells.extend(impact_area(battle, impact))
        return dedupe_positions(cells)

    def get_target_units_for_payload(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> list[HeroUnit]:
        return [unit for unit in battle.units_at_cells(self.get_target_cells_for_payload(battle, actor, payload))]  # type: ignore[list-item]


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
    raw_skill_text = "变硬 震开 龙息 岩吸（一回合一次；破魔；可以对‘沙尘’中所有单位生效；指定一个能力值，那些单位直到下回合结束，那个能力值-1，此单位可以任意增加等于因为此效果减少的能力值；此效果生效的时间内每增加一点能力值，此单位格子尽量增加一格；效果结束后此单位格子恢复到2*2） 岩石炮（直线移动此单位的任意数量格子直到触碰到单位；那些格子消失并对周围造3+格子数量的伤害）"
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
