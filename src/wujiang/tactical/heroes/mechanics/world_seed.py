"""R020 world seed, roots, and judgment stone mechanics."""

from __future__ import annotations

from wujiang.tactical.heroes.ai_candidate_plan import RANKED_CANDIDATES

from itertools import permutations
from typing import Any
from wujiang.tactical.engine.core import ActionError
from wujiang.tactical.engine.core import Battle
from wujiang.tactical.engine.core import DamageContext
from wujiang.tactical.engine.core import HealContext
from wujiang.tactical.engine.core import HeroUnit
from wujiang.tactical.engine.core import Position
from wujiang.tactical.engine.core import Skill
from wujiang.tactical.engine.core import Stats
from wujiang.tactical.engine.core import StatusEffect
from wujiang.tactical.engine.core import TargetContext
from wujiang.tactical.engine.core import Trait
from wujiang.tactical.engine.core import Unit
from wujiang.tactical.heroes.ai_policy import AISkillScoreContext
from wujiang.tactical.heroes.base import AbstractHero
from wujiang.tactical.heroes.common import FlyingTrait
from wujiang.tactical.heroes.common import payload_position
from wujiang.tactical.heroes.common import positions_to_dict
from wujiang.tactical.heroes.mechanics.next_five_geometry import square_around_cells


class WorldSeedSkillEffectGuardTrait(Trait):
    def __init__(self) -> None:
        super().__init__("世界之种技能效果免疫", "不受技能的非伤害效果影响，技能伤害仍然结算。")

    def accepts_status(self, status: StatusEffect) -> bool:
        return not getattr(status, "is_skill_effect", True)

    def on_targeted(self, battle: Battle, ctx: TargetContext) -> None:
        if self.owner is ctx.target and ctx.is_skill and not ctx.damage_target:
            ctx.cancelled = True
            ctx.reason = f"{self.owner.name} 不受技能非伤害效果影响。"

    def on_before_heal(self, battle: Battle, ctx: HealContext) -> None:
        action = battle.resolving_action
        if self.owner is ctx.target and ("skill" in ctx.tags or (action is not None and action.action_type in {"skill", "reaction_skill", "skill_effect"})):
            ctx.cancelled = True
            ctx.reason = f"{self.owner.name} 不受技能回复效果影响。"

    def blocks_skill_use(self, battle: Battle, actor: HeroUnit, skill: Skill) -> tuple[bool, str]:
        owner = self.owner
        if owner is not None and actor.unit_id == owner.unit_id and getattr(owner, "world_seed_terrain", False):
            return True, f"{owner.name} 被视为地形，不能使用技能。"
        return False, ""


class WorldSeedProtectionTrait(Trait):
    def __init__(self) -> None:
        super().__init__("树根守护", "自己的 1/2/3 号树根都存在时，世界之种不受伤害。")

    def on_before_damage(self, battle: Battle, ctx: DamageContext) -> None:
        owner = self.owner
        if owner is None or ctx.target.unit_id != owner.unit_id:
            return
        if {1, 2, 3}.issubset(alive_world_root_numbers(battle, owner)):
            ctx.cancelled = True
            ctx.reason = f"{owner.name} 的 1/2/3 号树根都在场，不受伤害。"


class WorldSeedRootCleanupTrait(Trait):
    def __init__(self) -> None:
        super().__init__("世界之种连根破坏", "世界之种被破坏时，与其同时召唤的树根也一并破坏。")

    def on_owner_banished(self, battle: Battle) -> None:
        self.on_owner_removed(battle)

    def on_owner_removed(self, battle: Battle) -> None:
        owner = self.owner
        if owner is None:
            return
        for unit in list(battle.all_units()):
            if unit.alive and getattr(unit, "hero_code", "") == "world_root" and getattr(unit, "seed_id", None) == owner.unit_id:
                unit.alive = False
                battle.log(f"{unit.name} 因 {owner.name} 被破坏而一并破坏。")
                if unit.position is not None:
                    unit.position = None
                if unit.unit_id not in {destroyed.unit_id for destroyed in battle.destroyed_units}:
                    battle.destroyed_units.append(unit)
                battle.remove_unit(unit)


class WorldSeedRootSyncTrait(Trait):
    def __init__(self, root_number: int) -> None:
        self.root_number = root_number
        super().__init__(f"{root_number}号树根", "世界之种的树根，被视为地形。")

    def to_public_dict(self, battle: Battle) -> dict[str, Any]:
        data = super().to_public_dict(battle)
        data["root_number"] = self.root_number
        return data


class WorldSeedTrait(Trait):
    def __init__(self) -> None:
        super().__init__("世界之种连根", "根据场上自己的树根编号赋予世界之种效果。")

    def on_owner_turn_start(self, battle: Battle) -> None:
        owner = self.owner
        if owner is None:
            return
        seed = alive_world_seed(battle, owner)
        if seed is None:
            return
        numbers = alive_world_root_numbers(battle, seed)
        if 1 in numbers:
            gained = seed.gain_mana(1)
            if gained:
                battle.log(f"{seed.name} 因 1 号树根自然回魔 {gained}。")


class WorldSeedSummon(AbstractHero):
    hero_code = "world_seed"
    hero_name = "世界之种"
    role = "地形单位"
    attribute = "木"
    race = "召唤物"
    level = 1
    base_stats = Stats(attack=5, defense=6, speed=0, attack_range=2, mana=1)
    footprint_width = 5
    footprint_height = 5
    stat_minimums = {"speed": 0.0, "mana": 0.0}
    raw_skill_text = ""
    raw_trait_text = "地形；不受技能非伤害效果；树根守护"

    def __init__(self, player_id: int, summoner_id: str) -> None:
        self.summoner_id = summoner_id
        self.world_seed_terrain = True
        self.standable_terrain = True
        super().__init__(player_id, is_summon=True)
        self.summoner_id = summoner_id

    @property
    def magic_immunity(self) -> bool:
        ref = getattr(self, "_battle_ref", None)
        battle = ref() if ref is not None else None
        return bool(Unit.magic_immunity.fget(self) or (
            battle is not None and 3 in alive_world_root_numbers(battle, self)
        ))

    @magic_immunity.setter
    def magic_immunity(self, value: bool) -> None:
        self._base_magic_immunity = value

    def build_skills(self) -> list[Skill]:
        return []

    def build_traits(self) -> list[Trait]:
        return [WorldSeedSkillEffectGuardTrait(), WorldSeedProtectionTrait(), WorldSeedRootCleanupTrait()]


class WorldRootSummon(AbstractHero):
    hero_code = "world_root"
    hero_name = "树根"
    role = "地形单位"
    attribute = "木"
    race = "召唤物"
    level = 1
    base_stats = Stats(attack=5, defense=6, speed=0, attack_range=2, mana=1)
    stat_minimums = {"speed": 0.0, "mana": 0.0}
    raw_skill_text = ""
    raw_trait_text = "地形；不受技能非伤害效果"

    def __init__(self, player_id: int, summoner_id: str, seed_id: str, root_number: int, direction: tuple[int, int]) -> None:
        self.summoner_id = summoner_id
        self.seed_id = seed_id
        self.root_number = root_number
        self.world_seed_terrain = True
        self.standable_terrain = True
        if direction[0] != 0:
            self.footprint_width = 2
            self.footprint_height = 1
        else:
            self.footprint_width = 1
            self.footprint_height = 2
        super().__init__(player_id, is_summon=True)
        self.summoner_id = summoner_id

    def stat(self, stat_name: str) -> float:
        ref = getattr(self, "_battle_ref", None)
        battle = ref() if ref is not None else None
        seed = battle.units.get(self.seed_id) if battle is not None else None
        if seed is not None and seed.alive and not seed.banished:
            return seed.stat(stat_name)
        return super().stat(stat_name)

    def build_skills(self) -> list[Skill]:
        return []

    def build_traits(self) -> list[Trait]:
        return [WorldSeedSkillEffectGuardTrait(), WorldSeedRootSyncTrait(self.root_number)]


class JudgmentStoneSummon(AbstractHero):
    def ai_normal_move_candidate_score(self, battle: Battle, destination: Position,
                                       profile: Any, score: float, shared: Any) -> float | None:
        if not any(unit.player_id != self.player_id for unit in battle.units_at(destination)):
            return score
        collision = shared.judgment_stone_collision_move_score(battle, self, destination, profile)
        return collision if collision > 0 else None

    hero_code = "judgment_stone"
    hero_name = "审判之石"
    role = "召唤物"
    attribute = "木"
    race = "召唤物"
    level = 1
    base_stats = Stats(attack=0, defense=999, speed=6, attack_range=0, mana=0)
    stat_minimums = {"attack": 0.0, "attack_range": 0.0, "mana": 0.0}
    raw_skill_text = ""
    raw_trait_text = "飞行；与敌方单位重合时爆裂"

    def __init__(self, player_id: int, summoner_id: str) -> None:
        self.summoner_id = summoner_id
        super().__init__(player_id, is_summon=True)
        self.summoner_id = summoner_id
        self.allow_enemy_destination_overlap = True

    def build_skills(self) -> list[Skill]:
        return []

    def build_traits(self) -> list[Trait]:
        return [FlyingTrait(), JudgmentStoneImpactTrait()]

class JudgmentStoneImpactTrait(Trait):
    def __init__(self) -> None:
        super().__init__("审判之石爆裂", "与敌方单位重合时，对该单位和周围 5*5 造成伤害 5，然后破坏。")

    def on_enter_battle(self, battle: Battle) -> None:
        self._explode_if_overlapping(battle)

    def on_unit_moved(self, battle: Battle, ctx: Any) -> None:
        self._explode_if_overlapping(battle)

    def _explode_if_overlapping(self, battle: Battle) -> None:
        owner = self.owner
        if owner is None or not owner.alive or owner.position is None or getattr(self, "exploding", False):
            return
        overlapping = [unit for unit in battle.units_at_cells(battle.unit_cells(owner)) if unit.player_id != owner.player_id and unit.unit_id != owner.unit_id]
        if not overlapping:
            return
        self.exploding = True
        cells = square_around_cells(battle, battle.unit_cells(owner), radius=2)
        battle.queue_skill_effect_action(
            actor=owner,
            display_name="审判之石爆裂",
            effect_code="judgment_stone_explosion",
            payload={"attack_power": 5, "cells": [cell.to_dict() for cell in cells]},
            target_cells=cells,
            description="撞击格周围 5×5 内各受到一次伤害数值 5 的伤害。",
            effect_resolver=resolve_judgment_stone_explosion,
        )


def resolve_judgment_stone_explosion(battle: Battle, owner: HeroUnit, queued_action: Any) -> None:
    cells = battle.payload_positions(queued_action.payload, "cells")
    apply_judgment_stone_explosion(battle, owner, cells)


def apply_judgment_stone_explosion(battle: Battle, owner: HeroUnit, cells: list[Position]) -> None:
    for target in battle.effect_units_at_cells(cells):
        if target.unit_id == owner.unit_id:
            continue
        battle.resolve_damage(DamageContext(
            source=owner,
            target=target,
            attack_power=5,
            is_skill=True,
            action_name="审判之石爆裂",
            from_field_effect=True,
            tags={"skill", "judgment_stone"},
        ))
    owner.alive = False
    battle.log(f"{owner.name} 爆裂并破坏。")
    battle.cleanup_dead_units()


def alive_world_seeds(battle: Battle, summoner: HeroUnit) -> list[HeroUnit]:
    return [
        unit
        for unit in battle.player_units(summoner.player_id)
        if unit.alive and not unit.banished and unit.position is not None and getattr(unit, "hero_code", "") == "world_seed" and getattr(unit, "summoner_id", None) == summoner.unit_id
    ]


def alive_world_seed(battle: Battle, summoner: HeroUnit) -> HeroUnit | None:
    seeds = alive_world_seeds(battle, summoner)
    return seeds[0] if seeds else None


def alive_world_root_numbers(battle: Battle, seed_or_summoner: HeroUnit) -> set[int]:
    if getattr(seed_or_summoner, "hero_code", "") == "world_seed":
        seed_id = seed_or_summoner.unit_id
        summoner_id = getattr(seed_or_summoner, "summoner_id", None)
    else:
        seed = alive_world_seed(battle, seed_or_summoner)
        seed_id = seed.unit_id if seed is not None else None
        summoner_id = seed_or_summoner.unit_id
    result: set[int] = set()
    for unit in battle.all_units():
        if not unit.alive or unit.banished or unit.position is None or getattr(unit, "hero_code", "") != "world_root":
            continue
        if seed_id is not None and getattr(unit, "seed_id", None) != seed_id:
            continue
        if seed_id is None and getattr(unit, "summoner_id", None) != summoner_id:
            continue
        result.add(int(getattr(unit, "root_number", 0) or 0))
    return result


class JudgmentStoneSkill(Skill):
    ai_nonhostile_throttle_exempt = True

    def ai_priority_score(self, context: AISkillScoreContext) -> float:
        return context.shared.judgment_stone_score(context)

    def __init__(self) -> None:
        super().__init__("judgment_stone", "审判之石", "普通技能：每回合第一次免费，之后费 0.5 魔；召唤飞行审判之石，与敌方单位重合时爆裂。", mana_cost=0.5, target_mode="cell")

    def mana_cost_for_payload(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any] | None = None) -> float:
        self.sync_turn_scope(battle)
        return 0.0 if self.uses_this_turn == 0 else super().mana_cost_for_payload(battle, actor, payload)

    def summon_cells(self, battle: Battle, actor: HeroUnit) -> list[Position]:
        body = set(battle.unit_cells(actor))
        return sorted({pos for cell in body for pos in battle.neighbors(cell)
                       if pos not in body and (pos.x, pos.y) not in battle.blocked_cells and not battle.units_at(pos)},
                      key=lambda pos: (pos.y, pos.x))

    def can_use(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any] | None = None) -> tuple[bool, str]:
        ok, reason = super().can_use(battle, actor, payload)
        if not ok:
            return ok, reason
        cells = self.summon_cells(battle, actor)
        if not cells:
            return False, "周围没有召唤审判之石的空格。"
        if payload is not None and payload.get("x") is not None and payload_position(payload) not in cells:
            return False, "审判之石只能召唤在周围空格。"
        return True, ""

    def preview(self, battle: Battle, actor: HeroUnit) -> dict[str, Any]:
        return {"cells": positions_to_dict(self.summon_cells(battle, actor)), "target_unit_ids": [], "secondary_cells": [], "requires_target": True}

    def execute(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> None:
        pos = payload_position(payload)
        if pos not in self.summon_cells(battle, actor):
            raise ActionError("审判之石只能召唤在周围空格。")
        stone = JudgmentStoneSummon(actor.player_id, actor.unit_id)
        stone.can_act_on_entry_turn = 2 in alive_world_root_numbers(battle, actor)
        stone.turn_ready = stone.can_act_on_entry_turn
        battle.add_unit(stone, pos)


class WorldSeedSkill(Skill):
    def ai_priority_score(self, context: AISkillScoreContext) -> float:
        return context.shared.world_seed_score(context)

    ai_candidate_plan = RANKED_CANDIDATES
    EDGE_DEFS = {"north": ((2, 0), (0, -1)), "east": ((4, 2), (1, 0)),
                 "south": ((2, 4), (0, 1)), "west": ((0, 2), (-1, 0))}
    EDGE_NAMES = {"north": "上", "east": "右", "south": "下", "west": "左"}

    def __init__(self) -> None:
        super().__init__("world_seed", "世界之种", "大招：一场一次；选择三边树根编号，再选种子左上格，召唤完整世界之种和三根。", max_uses_per_battle=1, target_mode="cell")

    def _edges(self, payload: dict[str, Any]) -> list[str]:
        raw = str(payload["choice_code"]).split(",") if payload.get("choice_code") else payload.get("root_edges", payload.get("edges", ["north", "east", "south"]))
        if not isinstance(raw, (list, tuple)):
            raise ActionError("世界之种需要三条不同的边。")
        edges = [str(edge).lower() for edge in raw]
        if len(edges) != 3 or len(set(edges)) != 3 or any(edge not in self.EDGE_DEFS for edge in edges):
            raise ActionError("世界之种需要三条不同的边。")
        return edges

    def _numbers(self, payload: dict[str, Any], edges: list[str]) -> list[int]:
        raw = payload.get("root_numbers")
        try:
            nums = [1, 2, 3] if raw is None else [int(raw[edge]) for edge in edges] if isinstance(raw, dict) else [int(value) for value in raw]
        except (KeyError, TypeError, ValueError):
            raise ActionError("树根编号必须为 1、2、3。")
        if sorted(nums) != [1, 2, 3]:
            raise ActionError("树根编号必须为 1、2、3。")
        return nums

    def anchor(self, payload: dict[str, Any]) -> Position:
        if payload.get("cells") is not None:
            cells = payload.get("cells")
            if not isinstance(cells, list) or len(cells) != 1:
                raise ActionError("请选择世界之种的一个左上格。")
            return payload_position(cells[0])
        return payload_position(payload)

    def root_placements(self, anchor: Position, edges: list[str], numbers: list[int]) -> list[tuple[int, Position, tuple[int, int], list[Position]]]:
        specs = []
        for edge, number in zip(edges, numbers):
            midpoint, direction = self.EDGE_DEFS[edge]
            first = anchor.offset(midpoint[0] + direction[0], midpoint[1] + direction[1])
            second = first.offset(*direction)
            start = Position(min(first.x, second.x), min(first.y, second.y))
            specs.append((number, start, direction, [first, second]))
        return specs

    def layout_cells(self, anchor: Position, edges: list[str], numbers: list[int]) -> list[Position]:
        cells = [anchor.offset(dx, dy) for dy in range(5) for dx in range(5)]
        return cells + [cell for _, _, _, root in self.root_placements(anchor, edges, numbers) for cell in root]

    def legal_layout(self, battle: Battle, anchor: Position, edges: list[str], numbers: list[int]) -> bool:
        return all(battle.in_bounds(cell) and (cell.x, cell.y) not in battle.blocked_cells and not battle.units_at(cell)
                   for cell in self.layout_cells(anchor, edges, numbers))

    def can_use(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any] | None = None) -> tuple[bool, str]:
        ok, reason = super().can_use(battle, actor, payload)
        if not ok:
            return ok, reason
        if payload is None or not any(key in payload for key in ("cells", "x", "root_edges", "choice_code")):
            return True, ""
        try:
            edges = self._edges(payload)
            if not self.legal_layout(battle, self.anchor(payload), edges, self._numbers(payload, edges)):
                return False, "世界之种及三条树根需要完整、未占用的落点。"
        except (ActionError, TypeError, ValueError, KeyError) as exc:
            return False, str(exc)
        return True, ""

    def preview(self, battle: Battle, actor: HeroUnit) -> dict[str, Any]:
        choices, destinations = [], []
        anchors: set[Position] = set()
        layouts: dict[frozenset[str], list[Position]] = {}
        for edges_tuple in permutations(self.EDGE_DEFS, 3):
            edges = list(edges_tuple)
            key = frozenset(edges)
            if key not in layouts:
                layouts[key] = [Position(x, y) for y in range(max(0, battle.height - 4)) for x in range(max(0, battle.width - 4))
                                if self.legal_layout(battle, Position(x, y), edges, [1, 2, 3])]
            legal = layouts[key]
            if not legal:
                continue
            choice = ",".join(edges)
            choices.append({"code": choice, "label": " / ".join(f"{i}号{self.EDGE_NAMES[edge]}" for i, edge in enumerate(edges, 1)),
                            "patterns": [[pos.to_dict()] for pos in legal]})
            anchors.update(legal)
            for pos in legal:
                destinations.append({"choice_code": choice, "pattern": [pos.to_dict()],
                                     "destination_cells": positions_to_dict(self.layout_cells(pos, edges, [1, 2, 3]))})
        return {"cells": positions_to_dict(sorted(anchors, key=lambda pos: (pos.y, pos.x))), "target_unit_ids": [],
                "secondary_cells": [], "requires_target": True, "pattern_destinations": destinations,
                "selection": {"mode": "choice_pattern", "choices": choices, "ordered": False}}

    def get_target_cells_for_payload(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> list[Position]:
        edges = self._edges(payload)
        return self.layout_cells(self.anchor(payload), edges, self._numbers(payload, edges))

    def execute(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> None:
        anchor = self.anchor(payload)
        edges = self._edges(payload)
        numbers = self._numbers(payload, edges)
        if not self.legal_layout(battle, anchor, edges, numbers):
            raise ActionError("世界之种及三条树根需要完整、未占用的落点。")
        seed = WorldSeedSummon(actor.player_id, actor.unit_id)
        battle.add_unit(seed, anchor)
        for number, start, direction, _ in self.root_placements(anchor, edges, numbers):
            root = WorldRootSummon(actor.player_id, actor.unit_id, seed.unit_id, number, direction)
            battle.add_unit(root, start)
            battle.log(f"{root.name} 编号为 {root.root_number}。")


