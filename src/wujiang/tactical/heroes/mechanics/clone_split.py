"""Reusable split and clone battlefield objects."""

from __future__ import annotations

from typing import Any
from wujiang.tactical.engine.core import ActionError
from wujiang.tactical.engine.core import Battle
from wujiang.tactical.engine.core import HeroUnit
from wujiang.tactical.engine.core import Position
from wujiang.tactical.engine.core import Skill
from wujiang.tactical.engine.core import Stats
from wujiang.tactical.engine.core import Trait
from wujiang.tactical.engine.core import Unit
from wujiang.tactical.heroes.base import AbstractHero
from wujiang.tactical.heroes.ai_policy import AISkillCandidateContext
from wujiang.tactical.heroes.common import positions_to_dict
from wujiang.tactical.prediction import direct_scoring
from wujiang.tactical.prediction import expected_local
from wujiang.tactical.prediction import random


def maybe_swap_new_clones(actor: HeroUnit, clones: list["StandardCloneSummon"]) -> bool:
    """Each summon has a chance to leave its source in place or exchange it."""
    if not clones or actor.position is None:
        return False
    original = actor.position
    if direct_scoring():
        actor._ai_expected_positions = ((original, 0.5), *(
            (clone.position, 0.5 / len(clones)) for clone in clones))
        return False
    if not random.choice((False, True)):
        return False
    chosen = random.choice(clones)
    actor.position, chosen.position = chosen.position, original
    return True


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
        self.raw_skill_text = source.raw_skill_text
        self.raw_trait_text = source.raw_trait_text
        self.base_stats = Stats(
            attack=source.stat("attack"),
            defense=source.stat("defense"),
            speed=source.stat("speed"),
            attack_range=source.targeting_range(),
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
    clone_count = 3
    ai_summon_base_bonus = 10.0

    def ai_clone_placement_enemies(self, battle: Battle, actor: HeroUnit) -> list[Unit]:
        return []

    def ai_candidate_payloads(self, context: AISkillCandidateContext) -> list[dict[str, Any]]:
        battle, actor = context.battle, context.actor
        preview = context.action.get("preview", {}) or {}
        candidate_cells = context.positions(preview.get("cells"))
        required = int((preview.get("selection") or {}).get("required_cells") or self.clone_count)
        probe = self._clone_probe(actor)
        selected: list[Position] = []
        occupied: set[tuple[int, int]] = set()
        enemies = self.ai_clone_placement_enemies(battle, actor)

        @expected_local(battle)
        def placement_key(cell: Position) -> float:
            return (-min(battle.unit_distance_to_cell(enemy, cell) for enemy in enemies)
                    if enemies else battle.unit_distance_to_cell(actor, cell))

        for cell in sorted(candidate_cells, key=placement_key):
            footprint_keys = {(footprint.x, footprint.y) for footprint in battle.unit_cells_at(probe, cell)}
            if occupied & footprint_keys:
                continue
            occupied.update(footprint_keys)
            selected.append(cell)
            if len(selected) >= required:
                break
        if len(selected) != required:
            return []
        payload = {**context.base_payload,
                   "cells": context.geometry.positions_to_payload(selected)}
        try:
            self.selected_destinations(battle, actor, payload)
        except Exception:
            return []
        return [payload]

    def __init__(self) -> None:
        super().__init__(
            "split",
            "分身",
            "普通技能：费 1.5 魔，每回合最多 1 次，在范内制造 3 个分身；本体本回合不能继续行动，分身登场当回合不能行动，随机决定是否与一个新分身交换位置。",
            mana_cost=1.5,
            max_uses_per_turn=1,
            target_mode="cell",
        )

    def _clone_probe(self, actor: HeroUnit) -> StandardCloneSummon:
        return StandardCloneSummon(actor.player_id, actor)

    def legal_destinations(self, battle: Battle, actor: HeroUnit) -> list[Position]:
        if actor.position is None:
            return []
        probe = self._clone_probe(actor)
        return [
            Position(x, y)
            for x in range(battle.width)
            for y in range(battle.height)
            if battle.unit_distance_to_cell(actor, Position(x, y)) <= actor.targeting_range()
            and battle.can_place_unit(probe, Position(x, y))
        ]

    def selected_destinations(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> list[Position]:
        raw_cells = payload.get("cells")
        if not isinstance(raw_cells, list):
            raise ActionError(f"需要选择 {self.clone_count} 个合法的分身位置。")
        selected: list[Position] = []
        seen: set[tuple[int, int]] = set()
        legal_keys = {(cell.x, cell.y) for cell in self.legal_destinations(battle, actor)}
        probe = self._clone_probe(actor)
        occupied: set[tuple[int, int]] = set()
        for raw in raw_cells:
            if not isinstance(raw, dict) or raw.get("x") is None or raw.get("y") is None:
                raise ActionError("分身位置不合法。")
            cell = Position(int(raw["x"]), int(raw["y"]))
            key = (cell.x, cell.y)
            if key in seen:
                raise ActionError("分身位置不能重复。")
            if key not in legal_keys:
                raise ActionError("分身位置不合法。")
            footprint_keys = {(footprint.x, footprint.y) for footprint in battle.unit_cells_at(probe, cell)}
            if occupied & footprint_keys:
                raise ActionError("分身位置不能互相重叠。")
            occupied.update(footprint_keys)
            seen.add(key)
            selected.append(cell)
        if len(selected) != self.clone_count:
            raise ActionError(f"需要选择 {self.clone_count} 个合法的分身位置。")
        return selected

    def queued_payload_metadata(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> dict[str, Any]:
        return {"declared_clone_cells": positions_to_dict(self.selected_destinations(battle, actor, payload))}

    def execute(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> None:
        if actor.position is None:
            raise ActionError("单位不在战场上。")
        destinations = battle.payload_positions(payload, "declared_clone_cells") or self.selected_destinations(battle, actor, payload)
        if any(not battle.can_place_unit(self._clone_probe(actor), cell) for cell in destinations):
            battle.log(f"{actor.name} 的分身落点被占用，未能召唤。")
            return
        clones: list[StandardCloneSummon] = []
        for destination in destinations:
            clone = self._clone_probe(actor)
            if not battle.can_place_unit(clone, destination):
                raise ActionError("分身位置已被占用。")
            battle.summon_unit(clone, destination, summoner=actor)
            clones.append(clone)
        exchanged = maybe_swap_new_clones(actor, clones)
        actor.turn_ready = False
        battle.log(f"{actor.name} 使用分身制造了 {self.clone_count} 个分身，"
                   + ("并与其中一个新分身交换了位置。" if exchanged else "本体留在原位。"))

    def preview(self, battle: Battle, actor: HeroUnit) -> dict[str, Any]:
        return {
            "cells": positions_to_dict(self.legal_destinations(battle, actor)),
            "target_unit_ids": [],
            "secondary_cells": [],
            "requires_target": True,
            "selection": {
                "mode": "pattern_cells",
                "patterns": [],
                "ordered": False,
                "required_cells": self.clone_count,
            },
        }


