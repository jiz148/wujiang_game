"""Doomlight Dragon battlefield objects and skills."""

from __future__ import annotations

from typing import Any
from wujiang.tactical.engine.core import ActionError
from wujiang.tactical.engine.core import Battle
from wujiang.tactical.engine.core import DamageContext
from wujiang.tactical.engine.core import HealContext
from wujiang.tactical.engine.core import HeroUnit
from wujiang.tactical.engine.core import Position
from wujiang.tactical.engine.core import Skill
from wujiang.tactical.engine.core import Stats
from wujiang.tactical.engine.core import Trait
from wujiang.tactical.heroes.base import AbstractHero
from wujiang.tactical.heroes.ai_policy import AISkillScoreContext, HeroAIPolicy
from wujiang.tactical.heroes.mechanics.resource_effect_ai import ResourceEffectScoring
from wujiang.tactical.heroes.common import FlagStatus
from wujiang.tactical.heroes.common import FlyingTrait
from wujiang.tactical.heroes.common import HardenSkill
from wujiang.tactical.heroes.common import OverhealTrait
from wujiang.tactical.heroes.common import ShensuSkill
from wujiang.tactical.heroes.common import StoneWallSkill
from wujiang.tactical.heroes.common import dedupe_positions
from wujiang.tactical.heroes.common import match_payload_pattern
from wujiang.tactical.heroes.common import pattern_selection_preview
from wujiang.tactical.heroes.common import positions_to_dict
from wujiang.tactical.heroes.mechanics.dragon_breath import RemoteDragonBreathSkill
from wujiang.tactical.heroes.mechanics.next_five_effects import apply_piercing_status_effect
from wujiang.tactical.heroes.mechanics.next_five_effects import remote_rectangle_patterns
import math


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
            super().on_owner_turn_start(battle)
            return
        source = battle.units.get(self.source_unit_id)
        source_unit = source if isinstance(source, HeroUnit) and source.alive and not source.banished else None
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
            and damage_ctx.actual_damage > 0
            and not damage_ctx.cancelled
        ):
            battle.heal(
                HealContext(
                    source=source_unit,
                    target=source_unit,
                    amount=damage_ctx.actual_damage,
                    action_name="末日光吸收",
                )
            )
        super().on_owner_turn_start(battle)

    def to_public_dict(self, battle: Battle) -> dict[str, Any]:
        data = super().to_public_dict(battle)
        data["triggers_remaining"] = self.triggers_remaining
        return data


def doomlight_ai_healing_value(context: AISkillScoreContext, score: float,
                               new_doom_damage: float) -> float:
    battle, actor = context.battle, context.actor
    if actor.hero_code == "doomlight_dragon" and new_doom_damage > 0:
        hp = actor.current_hp
        battle.heal(HealContext(source=actor, target=actor, amount=0.25, action_name="AI吸收估值"))
        if actor.current_hp > hp:
            score += new_doom_damage * 35.0
        actor.current_hp = hp
    return score


class DoomLightSkill(ResourceEffectScoring, Skill):
    def ai_control_bonus(self, context: AISkillScoreContext, targets: list[Unit]) -> float:
        enemies = [unit for unit in targets if unit.player_id != context.actor.player_id]
        return len(enemies) * 32.0 + sum(unit.current_hp * 40.0 for unit in enemies)

    def ai_after_effect_score(self, context: AISkillScoreContext, score: float,
                              new_doom_damage: float) -> float | None:
        if new_doom_damage == 0:
            return None
        return doomlight_ai_healing_value(context, score, new_doom_damage)

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
        cells = battle.payload_positions(payload, "declared_area_cells") if "declared_area_cells" in payload else self.chosen_cells(battle, actor, payload)
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
                refresh_existing=False,
            )

    def queued_payload_metadata(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> dict[str, Any]:
        return {"declared_area_cells": positions_to_dict(self.chosen_cells(battle, actor, payload))}

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


class ApocalypseSkill(ResourceEffectScoring, Skill):
    def ai_attack_power(self, battle, actor, payload, target, cells) -> float:
        try:
            n = int(payload.get("choice_code", payload.get("n", 0)))
        except (TypeError, ValueError):
            n = 0
        return actor.stat("attack") + n

    def ai_probe_prepare(self, context: AISkillScoreContext, probe: dict[str, Any]) -> None:
        self.prepay_resources(context.battle, context.actor, probe)

    def ai_after_effect_score(self, context: AISkillScoreContext, score: float,
                              new_doom_damage: float) -> float:
        score = doomlight_ai_healing_value(context, score, new_doom_damage)
        if any(context.hostile_units()) and context.actor.current_hp <= 0.5:
            score -= 85.0
        return score

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
        actor.take_damage_fraction(n)
        self.uses_this_turn += 1
        self.uses_this_battle += 1

    def execute(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> None:
        if "declared_area_cells" in payload:
            n = int(payload["resolved_n"])
            cells = battle.payload_positions(payload, "declared_area_cells")
        else:
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

    def queued_payload_metadata(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> dict[str, Any]:
        n, cells = self.chosen_cells(battle, actor, payload)
        return {"resolved_n": n, "declared_area_cells": positions_to_dict(cells)}

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
            refresh_existing=False,
        )

    def on_target_action_declared(self, battle: Battle, actor: HeroUnit, action_type: str, payload: dict[str, Any]) -> None:
        if action_type == "attack":
            self.apply_doom_light(battle, actor)

    def on_after_damage(self, battle: Battle, ctx: DamageContext) -> None:
        owner = self.owner
        if owner is None or "doom_light" in ctx.tags or not ctx.raw_damage or ctx.raw_damage <= 0:
            return
        if ctx.source is not None and ctx.target.unit_id == owner.unit_id and ctx.source.unit_id != owner.unit_id:
            self.apply_doom_light(battle, ctx.source)  # type: ignore[arg-type]
        if ctx.source is not None and ctx.source.unit_id == owner.unit_id and ctx.target.unit_id != owner.unit_id:
            self.apply_doom_light(battle, ctx.target)  # type: ignore[arg-type]

    def on_damage_cancelled(self, battle: Battle, ctx: DamageContext) -> None:
        owner = self.owner
        if owner is None or ctx.is_skill or "attack" not in ctx.tags or ctx.source is None:
            return
        if ctx.target.unit_id == owner.unit_id and ctx.source.unit_id != owner.unit_id:
            self.apply_doom_light(battle, ctx.source)  # type: ignore[arg-type]


class DoomlightAIPolicy(HeroAIPolicy):
    """Value a new infection and the cost of striking this dragon."""

    def late_attack_synergy_bonus(self, battle: Battle, actor: HeroUnit,
                                  target: HeroUnit, damage: float) -> float:
        if damage > 0 and not target.has_status("末日光"):
            return min(target.current_hp, 1.0) * 32.0
        return 0.0

    def target_attack_synergy_bonus(self, battle: Battle, target: HeroUnit,
                                    attacker: HeroUnit, damage: float) -> float:
        if not attacker.has_status("末日光"):
            return -min(attacker.current_hp, 1.0) * 55.0
        return 0.0


class DoomlightDragon(AbstractHero):
    hero_code = "doomlight_dragon"
    ai_policy = DoomlightAIPolicy()
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


