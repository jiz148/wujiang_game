"""Rock God battlefield objects and skills."""

from __future__ import annotations

from collections import deque
from typing import Any
from wujiang.tactical.engine.core import ActionError
from wujiang.tactical.engine.core import Battle
from wujiang.tactical.engine.core import HeroUnit
from wujiang.tactical.engine.core import Position
from wujiang.tactical.engine.core import QueuedAction
from wujiang.tactical.engine.core import Skill
from wujiang.tactical.engine.core import Stats
from wujiang.tactical.engine.core import StatusEffect
from wujiang.tactical.engine.core import Trait
from wujiang.tactical.heroes.base import AbstractHero
from wujiang.tactical.heroes.ai_policy import AISkillScoreContext
from wujiang.tactical.heroes.common import HardenSkill
from wujiang.tactical.heroes.common import KnockbackSkill
from wujiang.tactical.heroes.common import payload_cells
from wujiang.tactical.heroes.common import positions_to_dict
from wujiang.tactical.heroes.mechanics.dragon_breath import DragonBreathSkill
from wujiang.tactical.heroes.mechanics.mana_recovery import NaturalManaRecoveryTrait
from wujiang.tactical.heroes.mechanics.next_five_geometry import ALL_DIRECTIONS
from wujiang.tactical.heroes.mechanics.next_five_geometry import ORTHOGONAL_DIRECTIONS
from wujiang.tactical.heroes.mechanics.next_five_geometry import impact_area
from wujiang.tactical.heroes.mechanics.next_five_geometry import position_key
from wujiang.tactical.heroes.mechanics.next_five_geometry import positions_connected
from wujiang.tactical.heroes.mechanics.next_five_geometry import square_around_cells
from wujiang.tactical.heroes.mechanics.sandstorm import RockGodSandstormAura
from wujiang.tactical.heroes.mechanics.resource_effect_ai import ResourceEffectScoring


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
    def __init__(self, stat_name: str, delta: int, *, duration: int = 1, source_unit_id: str | None = None) -> None:
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
        self.source_unit_id = source_unit_id

    def modify_stat(self, stat_name: str, value: float) -> float:
        if stat_name == self.stat_name:
            return max(0 if stat_name == "mana" else 1, value + self.delta)
        return value

    def on_owner_turn_start(self, battle: Battle) -> None:
        if self.source_unit_id is None:
            super().on_owner_turn_start(battle)

    def on_any_turn_end(self, battle: Battle, ended_player_id: int) -> None:
        if self.source_unit_id and self.source_unit_id not in battle.units and self.owner is not None:
            self.owner.remove_status(self, battle)

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



class RockAbsorbLinkStatus(StatusEffect):
    def __init__(self) -> None:
        super().__init__("岩吸连结", "同次岩吸的能力交换在岩神下次己方回合开始时一起结束。", duration=1, tick_scope="owner_turn_start")

    def on_removed(self, battle: Battle) -> None:
        if self.owner is None:
            return
        for unit in list(battle.all_units()):
            for status in list(unit.statuses):
                if isinstance(status, RockAbsorbStatStatus) and status.source_unit_id == self.owner.unit_id:
                    unit.remove_status(status, battle)

    def on_owner_removed(self, battle: Battle) -> None:
        self.on_removed(battle)


class RockAbsorbSkill(ResourceEffectScoring, Skill):
    def ai_unit_stat_bonus(self, context: AISkillScoreContext, unit: HeroUnit,
                           stats: dict[str, float], mana: float, sign: float) -> float:
        return (sum((unit.stat(stat) - stats[stat]) * weight * sign
                    for stat, weight in self.stat_weights.items())
                + (unit.current_mana - mana) * sign * 14.0)

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
        declaration = payload if "rock_absorb_area" in payload else self.queued_payload_metadata(battle, actor, payload)
        targets = [unit for unit in battle.units_at_cells(battle.payload_positions(declaration, "rock_absorb_area")) if unit is not actor]
        selected_cells = battle.payload_positions(declaration, "rock_absorb_growth")
        previous_link = actor.get_status("岩吸连结")
        if previous_link is not None:
            actor.remove_status(previous_link, battle)
        applied_count = 0
        for target in targets:
            ctx = battle.validate_target(
                actor,
                target,
                action_name="岩吸",
                is_skill=True,
                is_hostile=target.player_id != actor.player_id,
                ignore_targeting_restrictions=True,
                tags={"skill", "rock_absorb"},
            )
            if ctx.cancelled:
                if ctx.reason:
                    battle.log_public_event(ctx.reason, source=actor, target=target)
                continue
            applied_count += 1
            target.add_status(RockAbsorbStatStatus(stat_name, -1, source_unit_id=actor.unit_id))
            if stat_name == "mana":
                target.current_mana = round(max(0.0, target.current_mana - 1), 2)
                target.clamp_mana()
            battle.log(f"{target.name} 受到岩吸影响，{self.stat_labels()[stat_name]} -1。")
        if applied_count:
            actor.add_status(RockAbsorbStatStatus(stat_name, applied_count, source_unit_id=actor.unit_id))
            if stat_name == "mana":
                actor.current_mana = round(actor.current_mana + applied_count, 2)
                actor.clamp_mana()
        gained_cells: list[Position] = []
        connected = {position_key(cell) for cell in battle.unit_cells(actor)}
        remaining = list(selected_cells)
        while remaining and len(gained_cells) < applied_count:
            candidate = next((cell for cell in remaining if battle.in_bounds(cell)
                              and not battle.is_occupied(cell, ignore=actor, mover=actor)
                              and position_key(cell) not in connected
                              and any((cell.x + dx, cell.y + dy) in connected for dx, dy in ORTHOGONAL_DIRECTIONS)), None)
            if candidate is None:
                break
            remaining.remove(candidate)
            gained_cells.append(candidate)
            connected.add(position_key(candidate))
        if gained_cells:
            actor.set_footprint_cells([*battle.unit_cells(actor), *gained_cells])
            footprint_status = actor.get_status("岩吸占格")
            if footprint_status is None:
                actor.add_status(RockAbsorbFootprintStatus())
            else:
                footprint_status.duration = 1
            battle.log(f"{actor.name} 因岩吸增加了 {len(gained_cells)} 个占格。")

        if applied_count:
            actor.add_status(RockAbsorbLinkStatus())

    def queued_payload_metadata(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> dict[str, Any]:
        self.selected_stat(payload)
        targets = self.affected_units(battle, actor)
        required = min(len(targets), len(self.growth_candidates(battle, actor, len(targets))))
        growth = self.selected_growth_cells(battle, actor, payload, required)
        return {"rock_absorb_area": positions_to_dict(self.aura_cells(battle, actor)), "rock_absorb_growth": positions_to_dict(growth)}

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


class RockCannonSkill(ResourceEffectScoring, Skill):
    def ai_effect_preflight(self, context: AISkillScoreContext) -> bool:
        battle, actor = context.battle, context.actor
        try:
            selected, direction, _ = self.validate_selection(battle, actor, context.payload)
            impact_cells = [cell for center in self.impact_positions(battle, actor, selected, direction)
                            for cell in (Position(center.x + dx, center.y + dy)
                                         for dx in (-1, 0, 1) for dy in (-1, 0, 1))]
            return any(unit.player_id != actor.player_id for unit in battle.units_at_cells(impact_cells))
        except ActionError:
            return False

    def ai_probe_prepare(self, context: AISkillScoreContext, probe: dict[str, Any]) -> None:
        context.battle.pending_followup_actions = deque()

    def ai_probe_finish(self, context: AISkillScoreContext) -> None:
        battle, actor = context.battle, context.actor
        while battle.pending_followup_actions:
            battle.resolve_skill_effect(actor, battle.pending_followup_actions.popleft())

    def ai_after_effect_score(self, context: AISkillScoreContext, score: float,
                              new_doom_damage: float) -> float:
        score -= len(context.payload.get("cells", [])) * 12.0
        if len(context.battle.unit_cells(context.actor)) == 1:
            score -= 12.0
        return score

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
        self.queue_projectile(battle, actor, selected, direction, 0)
        battle.log(f"{actor.name} 发射了 {len(selected)} 个身体格。")

    def queue_projectile(self, battle: Battle, actor: HeroUnit, selected: list[Position], direction: tuple[int, int], index: int) -> None:
        if index >= len(selected) or not actor.alive:
            return
        impact = self.impact_positions(battle, actor, [selected[index]], direction)[0]
        cells = impact_area(battle, impact)
        battle.queue_skill_effect_action(
            actor=actor, display_name=self.name, effect_code="area_damage", target_cells=cells,
            payload={"cells": positions_to_dict(cells), "attack_power": 3 + len(selected),
                     "tags": ["skill", "rock_cannon"], "projectiles": positions_to_dict(selected),
                     "direction": {"dx": direction[0], "dy": direction[1]}},
            speed=self.chain_speed, segment_index=index + 1, segment_count=len(selected),
            effect_resolver=self.resolve_projectile,
        )

    def resolve_projectile(self, battle: Battle, actor: HeroUnit, queued_action: QueuedAction) -> None:
        queued_action.effect_resolver = None
        battle.resolve_skill_effect(actor, queued_action)
        selected = battle.payload_positions(queued_action.payload, "projectiles")
        direction = self.selected_direction(queued_action.payload)
        self.queue_projectile(battle, actor, selected, direction, int(queued_action.payload["segment_index"]))

    def queued_payload_metadata(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> dict[str, Any]:
        self.validate_selection(battle, actor, payload)
        return {}

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
    raw_skill_text = "变硬 震开 龙息 岩吸（每回合一次；选择一种能力；自身局部沙尘内除自己外单位该能力-1，自己按未被盾挡的目标数增加同项能力并尽量连通增生；交换与增生在自己下次回合开始时结束） 岩石炮（选择身体格及方向发射，至少留1格且身体连通；每弹碰撞或到边界产生独立3*3攻值3+本次弹数的非破魔敌我伤害，每弹可分别连锁）"
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


