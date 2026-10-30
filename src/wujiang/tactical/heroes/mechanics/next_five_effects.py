"""Shared piercing effects and remote area patterns."""

from __future__ import annotations

from wujiang.tactical.engine.core import Battle
from wujiang.tactical.engine.core import DamageContext
from wujiang.tactical.engine.core import HeroUnit
from wujiang.tactical.engine.core import Position
from wujiang.tactical.engine.core import StatusEffect
from wujiang.tactical.engine.geometry import rectangle_pattern_coordinates
from wujiang.tactical.heroes.common import dedupe_positions
from wujiang.tactical.heroes.common import pattern_signature
from wujiang.tactical.heroes.mechanics.status_replacement import replace_status_by_name


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
    refresh_existing: bool = True,
    consume_shield: bool = True,
    cannot_evade: bool = True,
) -> bool:
    target = battle.effect_recipient(target)
    if not target.alive or target.position is None or target.banished:
        return False
    if target.direct_effects_blocked():
        return False
    if not refresh_existing and target.has_status(status.name):
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
        cannot_evade=cannot_evade,
        resolve_defenses=False,
        ignore_targeting_restrictions=ignore_targeting_restrictions,
        tags=set(tags or set()),
    )
    if ctx.cancelled:
        if ctx.reason:
            battle.log_public_event(ctx.reason, source=source, target=target)
        return False
    if consume_shield and is_hostile and target.total_shields() > 0:
        target.consume_one_shield()
        battle.log_public_event(
            f"{target.name} 的 1 层护盾被【{action_name}】贯穿并打碎。",
            source=source,
            target=target,
        )
    if is_hostile and target.dodge_charges > 0 and not ctx.cannot_evade:
        target.dodge_charges -= 1
        battle.log_public_event(f"{target.name} 闪避了【{action_name}】。", source=source, target=target)
        return False
    if is_skill and battle.destroy_clone_for_skill_effect(target, source=source, action_name=action_name):
        return True
    status.is_skill_effect = is_skill
    replace_status_by_name(battle, target, status)
    return status in target.statuses


def remote_rectangle_patterns(battle: Battle, actor: HeroUnit, width: int, height: int,
                              *, attack_range: int | None = None) -> list[list[Position]]:
    if actor.position is None:
        return []
    patterns: list[list[Position]] = []
    seen: set[tuple[tuple[int, int], ...]] = set()
    range_limit = attack_range if attack_range is not None else actor.targeting_range()
    in_range = {(cell.x, cell.y) for cell in battle.cells_within_distance(actor, range_limit)}
    coordinates = rectangle_pattern_coordinates(battle.width, battle.height, width, height, in_range)
    if coordinates is not None:
        return [[Position(x, y) for x, y in cells] for cells in coordinates]
    for start_x in range(1 - width, battle.width):
        for start_y in range(1 - height, battle.height):
            cells: list[Position] = []
            contains_range_cell = False
            for x in range(max(0, start_x), min(battle.width, start_x + width)):
                for y in range(max(0, start_y), min(battle.height, start_y + height)):
                    cell = Position(x, y)
                    cells.append(cell)
                    if (x, y) in in_range:
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


