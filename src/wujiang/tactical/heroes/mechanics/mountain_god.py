"""R035 mountain god attacks and counter mechanics."""

from __future__ import annotations

from typing import Any
from wujiang.tactical.engine.core import ActionError
from wujiang.tactical.engine.core import Battle
from wujiang.tactical.engine.core import DamageContext
from wujiang.tactical.engine.core import HealContext
from wujiang.tactical.engine.core import HeroUnit
from wujiang.tactical.engine.core import Position
from wujiang.tactical.engine.core import Skill
from wujiang.tactical.engine.core import StatusEffect
from wujiang.tactical.engine.core import Trait
from wujiang.tactical.heroes.common import DrainManaSkill
from wujiang.tactical.heroes.common import StatModifierStatus
from wujiang.tactical.heroes.common import ensure_distance
from wujiang.tactical.heroes.common import ensure_enemy
from wujiang.tactical.heroes.common import is_mana_drain_immune
from wujiang.tactical.heroes.common import pattern_selection_preview
from wujiang.tactical.heroes.common import pattern_signature
from wujiang.tactical.heroes.common import payload_position
from wujiang.tactical.heroes.common import payload_target_unit
from wujiang.tactical.heroes.common import positions_to_dict
from wujiang.tactical.heroes.next_five import ALL_DIRECTIONS


class MountainGodCounterStatus(StatusEffect):
    def __init__(self) -> None:
        super().__init__("山神计数点", "牛鬼使用山神术。觉醒需要 8 个。", duration=None)


def mountain_counter_count(unit: HeroUnit) -> int:
    return sum(1 for status in unit.statuses if isinstance(status, MountainGodCounterStatus))


class DemonBladeSkill(Skill):
    def __init__(self) -> None:
        super().__init__("demon_blade", "妖刀。魔鬼", "普通技能：费 1 魔，每回合最多 1 次；声明 3 格直线，三格伤害分别为 5、4、3。", mana_cost=1, max_uses_per_turn=1, target_mode="cell")

    def patterns(self, battle: Battle, actor: HeroUnit) -> list[list[Position]]:
        patterns: list[list[Position]] = []
        seen: set[tuple[tuple[int, int], ...]] = set()
        for origin in battle.unit_cells(actor):
            for dx, dy in ALL_DIRECTIONS:
                cells: list[Position] = []
                for step in range(1, 4):
                    cell = Position(origin.x + dx * step, origin.y + dy * step)
                    if not battle.in_bounds(cell):
                        break
                    cells.append(cell)
                if not cells:
                    continue
                key = pattern_signature(cells)
                if key in seen:
                    continue
                seen.add(key)
                patterns.append(cells)
        return patterns

    def chosen_cells(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> list[Position]:
        cells = [payload_position(item) for item in payload.get("cells", [])]
        signature = pattern_signature(cells)
        legal = {pattern_signature(pattern): pattern for pattern in self.patterns(battle, actor)}
        if signature not in legal:
            raise ActionError("请选择合法的妖刀直线。")
        return legal[signature]

    def execute(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> None:
        cells = self.chosen_cells(battle, actor, payload)
        damage_by_key = {(cell.x, cell.y): damage for cell, damage in zip(cells, [5, 4, 3])}
        for target in battle.effect_units_at_cells(cells):
            if target.player_id == actor.player_id:
                continue
            hit_damages = [damage_by_key[(cell.x, cell.y)] for cell in battle.unit_cells(target) if (cell.x, cell.y) in damage_by_key]
            if not hit_damages:
                continue
            battle.resolve_damage(
                DamageContext(
                    source=actor,
                    target=target,
                    attack_power=max(hit_damages),
                    is_skill=True,
                    action_name="妖刀。魔鬼",
                    tags={"skill", "attack", "demon_blade"},
                )
            )

    def preview(self, battle: Battle, actor: HeroUnit) -> dict[str, Any]:
        patterns = self.patterns(battle, actor)
        preview = pattern_selection_preview(patterns, ordered=True)
        cell_keys = {(cell.x, cell.y) for pattern in patterns for cell in pattern}
        targets = [unit.unit_id for unit in battle.enemy_units(actor.player_id) if any((cell.x, cell.y) in cell_keys for cell in battle.unit_cells(unit))]
        preview.update({"target_unit_ids": targets, "secondary_cells": [], "requires_target": True})
        return preview


class LargeDrainManaSkill(DrainManaSkill):
    def __init__(self) -> None:
        super().__init__()
        self.code = "large_drain_mana"
        self.name = "吸魔（大）"
        self.description = "普通技能：吸魔的扩大版，范 +1。"

    def direct_unit_target_range(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any] | None = None) -> int:
        return actor.targeting_range() + 1

    def execute(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> None:
        target = payload_target_unit(battle, payload)
        ensure_enemy(actor, target)
        if not battle.is_declared_resolution(actor, payload, {"skill"}):
            ensure_distance(actor, target, actor.targeting_range() + 1)
        target_ctx = battle.validate_target(actor, target, action_name="吸魔（大）", is_skill=True, is_hostile=True)
        if target_ctx.cancelled:
            battle.log(target_ctx.reason)
            return
        if is_mana_drain_immune(target):
            battle.log(f"{target.name} 无法被吸魔。")
            return
        lost = target_ctx.target.drain_mana(1.0)
        actor.gain_mana(lost)
        battle.log(f"{actor.name} 吸取了 {target.name} 的 {lost} 点魔力。")

    def preview(self, battle: Battle, actor: HeroUnit) -> dict[str, Any]:
        targets = [
            unit
            for unit in battle.enemy_units(actor.player_id)
            if unit.position and actor.position and actor.position.distance_to(unit.position) <= actor.targeting_range() + 1
        ]
        return {"cells": positions_to_dict([unit.position for unit in targets if unit.position]), "target_unit_ids": [unit.unit_id for unit in targets], "secondary_cells": [], "requires_target": True}


class UnlimitedManaTemporaryStatus(StatusEffect):
    prevents_mana_drain = True
    prevents_mana_loss = True

    def __init__(self, duration: int = 4) -> None:
        super().__init__("山神术。室王", "魔无限：施放技能不消耗魔，当前魔无上限。", duration=duration, tick_scope="owner_turn_end")

    def bind(self, owner: HeroUnit) -> "UnlimitedManaTemporaryStatus":
        super().bind(owner)
        owner.allow_unbounded_mana = True
        owner.current_mana = max(owner.current_mana, owner.max_mana())
        return self

    def modify_skill_mana_cost(self, battle: Battle, actor: HeroUnit, skill: Skill,
                               payload: dict[str, Any] | None, cost: float) -> float:
        return 0.0 if self.owner is not None and actor.unit_id == self.owner.unit_id else cost

    def on_removed(self, battle: Battle) -> None:
        owner = self.owner
        if owner is None:
            return
        owner.allow_unbounded_mana = any(status is not self and isinstance(status, UnlimitedManaTemporaryStatus) for status in owner.statuses)
        owner.clamp_mana()


class MountainGodMuroSkill(Skill):
    def __init__(self) -> None:
        super().__init__("mountain_god_muro", "山神术。室王", "大招：一场战斗一次；魔无限，持续 4 轮。", max_uses_per_battle=1, target_mode="self")

    def execute(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> None:
        existing = actor.get_status("山神术。室王")
        if existing is not None:
            actor.remove_status(existing, battle)
        actor.add_status(UnlimitedManaTemporaryStatus())
        battle.log(f"{actor.name} 发动【山神术。室王】，4 轮内魔无限。")


class MountainEscapeStatus(StatModifierStatus):
    def __init__(self) -> None:
        super().__init__("遁术。神山", defense_delta=2, description="守 +2，不能移动，每回合魔 +1。", duration=6, tick_scope="owner_turn_end")
        self.flag_name = "cannot_move"
        self.locked_flags = ("cannot_move",)

    def bind(self, owner: HeroUnit) -> "MountainEscapeStatus":
        super().bind(owner)
        owner.cannot_move = True
        return self

    def on_owner_turn_start(self, battle: Battle) -> None:
        owner = self.owner
        if owner is None:
            return
        gained = owner.gain_mana(1)
        if gained:
            battle.log(f"{owner.name} 的【遁术。神山】使魔 +{gained}。")

    def on_removed(self, battle: Battle) -> None:
        owner = self.owner
        if owner is None:
            return
        owner.cannot_move = any(getattr(status, "flag_name", "") in {"cannot_move", "cannot_act"}
                               or "cannot_move" in getattr(status, "locked_flags", ())
                               or "cannot_move" in getattr(status, "provided_flags", ())
                               for status in owner.statuses)


class MountainEscapeSkill(Skill):
    def __init__(self) -> None:
        super().__init__("mountain_escape", "遁术。神山", "大招：一场战斗一次；守 +2，血满，不能移动，每回合魔 +1，持续 6 轮。", max_uses_per_battle=1, target_mode="self")

    def execute(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> None:
        battle.heal(HealContext(source=actor, target=actor, amount=max(0.0, actor.max_health - actor.current_hp),
                               action_name="遁术。神山"))
        existing = actor.get_status("遁术。神山")
        if existing is not None:
            actor.remove_status(existing, battle)
        actor.add_status(MountainEscapeStatus())
        battle.log(f"{actor.name} 发动【遁术。神山】，生命回满并进入神山状态。")


class MountainAwakeningSkill(Skill):
    def __init__(self) -> None:
        super().__init__("mountain_awakening", "山神术。觉醒", "清空 8 个山神计数点，重置所有大招。", target_mode="self")

    def can_use(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any] | None = None) -> tuple[bool, str]:
        ok, reason = super().can_use(battle, actor, payload)
        if not ok:
            return ok, reason
        if mountain_counter_count(actor) < 8:
            return False, "需要 8 个山神计数点。"
        return True, ""

    def execute(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> None:
        for status in list(actor.statuses):
            if isinstance(status, MountainGodCounterStatus):
                actor.remove_status(status, battle)
        for skill in actor.skills:
            if skill.max_uses_per_battle is not None:
                skill.uses_this_battle = 0
        battle.log(f"{actor.name} 发动【山神术。觉醒】，重置所有大招。")


class MountainGodCounterTrait(Trait):
    def __init__(self) -> None:
        super().__init__("山神计数", "整次普攻被对方连锁或自己真实破坏单位时获得山神计数点。")

    def add_counter(self, battle: Battle) -> None:
        owner = self.owner
        if owner is None:
            return
        owner.add_status(MountainGodCounterStatus())
        battle.log(f"{owner.name} 获得 1 个【山神计数点】。")

    def on_basic_attack_finished(
        self,
        battle: Battle,
        actor: HeroUnit,
        payload: dict[str, Any],
        damage_contexts: list[DamageContext],
        missed: bool,
    ) -> None:
        owner = self.owner
        if owner is None or actor.unit_id != owner.unit_id or not owner.alive or owner.banished or owner.is_clone:
            return
        if not bool(payload.get("enemy_reacted")):
            return
        self.add_counter(battle)

    def on_confirmed_damage_destruction(self, battle: Battle, ctx: DamageContext) -> None:
        owner = self.owner
        if owner is None or not owner.alive or owner.banished or owner.is_clone or ctx.source is None or ctx.source.unit_id != owner.unit_id:
            return
        if ctx.target.alive or ctx.target.unit_id == owner.unit_id:
            return
        self.add_counter(battle)


