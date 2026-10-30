"""R194 Alexander movement and hurricane mechanics."""

from __future__ import annotations

from typing import Any
from wujiang.tactical.engine.core import Battle
from wujiang.tactical.engine.core import DamageContext
from wujiang.tactical.engine.core import MoveContext
from wujiang.tactical.engine.core import Position
from wujiang.tactical.engine.core import Skill
from wujiang.tactical.engine.core import Trait
from wujiang.tactical.engine.core import Unit
from wujiang.tactical.heroes.common import StatModifierStatus
from wujiang.tactical.heroes.ai_policy import AISkillScoreContext
from wujiang.tactical.heroes.common import pattern_selection_preview
from wujiang.tactical.heroes.common import pattern_signature
from wujiang.tactical.heroes.mechanics.gladiator_core import gladiator_swap
from wujiang.tactical.heroes.mechanics.gladiator_core import gladiator_swap_destinations
from wujiang.tactical.heroes.mechanics.world_seed import WorldSeedSkillEffectGuardTrait
from wujiang.tactical.heroes.mechanics.declared_area import DeclaredAreaSkillMixin
from wujiang.tactical.heroes.mechanics.next_five_effects import remote_rectangle_patterns
from wujiang.tactical.heroes.mechanics.next_five_geometry import square_around_cells


class AlexanderHurricaneSkill(DeclaredAreaSkillMixin, Skill):
    target_cells_are_geometry_only = True

    def ai_priority_score(self, context: AISkillScoreContext) -> float:
        if not self.nearby_allies(context.battle, context.actor):
            return -1000.0
        value = context.paid_skill_value()
        return value if value > 0 else -1000.0

    def __init__(self) -> None:
        super().__init__("battle_hurricane", "战斗飓风",
                         "每回合一次；远程横竖2×5，按声明时周围7×7内其他己方单位数逐次造成双方技能伤害。",
                         max_uses_per_turn=1, target_mode="cell")

    def patterns(self, battle: Battle, actor: Unit) -> list[list[Position]]:
        patterns = remote_rectangle_patterns(battle, actor, 2, 5) + remote_rectangle_patterns(battle, actor, 5, 2)
        return list({pattern_signature(pattern): pattern for pattern in patterns}.values())

    def nearby_allies(self, battle: Battle, actor: Unit) -> list[Unit]:
        area = set(square_around_cells(battle, battle.unit_cells(actor), radius=3)) - set(battle.unit_cells(actor))
        return [unit for unit in battle.player_units(actor.player_id)
                if unit.unit_id != actor.unit_id and unit.alive and not unit.banished
                and any(cell in area for cell in battle.unit_cells(unit))]

    def queued_payload_metadata(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> dict[str, Any]:
        return {**super().queued_payload_metadata(battle, actor, payload),
                "hurricane_hits": len(self.nearby_allies(battle, actor)),
                "hurricane_attack_power": actor.stat("attack")}

    def execute(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> None:
        cells = self.chosen_cells(battle, actor, payload)
        hits = int(payload.get("hurricane_hits", len(self.nearby_allies(battle, actor))))
        power = float(payload.get("hurricane_attack_power", actor.stat("attack")))
        battle.log_public_event(f"{actor.name} 的战斗飓风锁定 {hits} 次伤害。", source=actor)
        for index in range(hits):
            battle.queue_area_damage_effect(
                actor=actor, display_name="战斗飓风", cells=cells, attack_power=power,
                tags={"skill", self.code}, segment_index=index + 1, segment_count=hits,
            )

    def preview(self, battle: Battle, actor: Unit) -> dict[str, Any]:
        patterns = self.patterns(battle, actor)
        preview = pattern_selection_preview(patterns)
        affected = battle.effect_units_at_cells([cell for pattern in patterns for cell in pattern])
        preview.update({"target_unit_ids": [unit.unit_id for unit in affected
                                            if battle.unit_can_be_selected(unit, actor=actor)[0]],
                        "secondary_cells": [], "requires_target": True,
                        "hit_count": len(self.nearby_allies(battle, actor))})
        return preview

    def get_target_cells_for_payload(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> list[Position]:
        return self.chosen_cells(battle, actor, payload)

    def get_target_units_for_payload(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> list[Unit]:
        return battle.effect_units_at_cells(self.chosen_cells(battle, actor, payload))


class AlexanderSkillEffectGuardTrait(WorldSeedSkillEffectGuardTrait):
    def __init__(self) -> None:
        Trait.__init__(self, "亚历山大技能效果免疫", "不受技能非伤害效果影响，技能伤害仍结算。")

    def blocks_skill_non_damage_effects(self) -> bool:
        return True

    def blocks_skill_use(self, battle: Battle, actor: Unit, skill: Skill) -> tuple[bool, str]:
        if self.owner is actor and skill.code in {"harden", "protection"}:
            return True, f"{actor.name} 不受非伤害技能效果，无法获得{skill.name}效果。"
        return False, ""


class AlexanderAfterAttackSwapTrait(Trait):
    requires_damage_choice = True

    def __init__(self) -> None:
        super().__init__("亚历山大攻击换位", "普攻实际完成后与一名有合法落点的己方单位交换位置。")

    def on_basic_attack_finished(self, battle: Battle, actor: Unit, payload: dict[str, Any],
                                 damage_contexts: list[DamageContext], missed: bool) -> None:
        owner = self.owner
        if (owner is None or actor.unit_id != owner.unit_id or not actor.alive or actor.banished
                or actor.position is None or payload.get("counter") or payload.get("reaction_attack")
                or payload.get("basic_attack_cancelled")):
            return
        options = [unit.unit_id for unit in battle.player_units(actor.player_id)
                   if unit.unit_id != actor.unit_id and unit.alive and not unit.banished
                   and gladiator_swap_destinations(battle, actor, unit, via_skill=False) is not None]
        if not options:
            battle.log_public_event(f"{actor.name} 普攻后没有可交换的己方单位。", source=actor)
            return
        choice = battle.attack_swap_choice(actor, options)
        ally = battle.units.get(choice or "")
        if ally is not None and choice in options:
            gladiator_swap(battle, actor, ally, skill_code="alexander_attack_swap", via_skill=False)


class AlexanderMovedByAllyTrait(Trait):
    def __init__(self) -> None:
        super().__init__("亚历山大协同", "因其他己方单位非技能效果位移时，本回合和下个自己的回合攻守+1并重置战斗飓风。")

    def on_unit_moved(self, battle: Battle, ctx: MoveContext) -> None:
        owner = self.owner
        if owner is None or ctx.unit.unit_id != owner.unit_id or ctx.start == ctx.end or ctx.via_skill:
            return
        source_id = next((tag.removeprefix("source:") for tag in ctx.tags if tag.startswith("source:")), None)
        if source_id is None and battle.resolving_action is not None:
            source_id = battle.resolving_action.actor_id
        source = battle.units.get(source_id or "")
        if source is None or source.unit_id == owner.unit_id or source.player_id != owner.player_id:
            return
        existing = owner.get_status("亚历山大协同增益")
        if existing is None:
            status = StatModifierStatus("亚历山大协同增益", attack_delta=1, defense_delta=1,
                                        duration=2, tick_scope="owner_turn_end")
            status.is_skill_effect = False
            owner.add_status(status, source=source)
        else:
            existing.duration = 2
        hurricane = owner.get_skill("battle_hurricane")
        hurricane.sync_turn_scope(battle)
        hurricane.uses_this_turn = 0
        battle.log_public_event(f"{owner.name} 受友方位移，攻守+1并重置战斗飓风。", source=source, target=owner)


class AlexanderMagicAuraTrait(Trait):
    def __init__(self) -> None:
        super().__init__("亚历山大魔免光环", "身体周围5×5内其他己方单位获得魔免。")

    def grants_magic_immunity_to(self, battle: Battle, unit: Unit) -> bool:
        owner = self.owner
        return bool(owner is not None and owner.alive and not owner.banished and owner.position is not None
                    and unit.unit_id != owner.unit_id and unit.player_id == owner.player_id
                    and unit.alive and not unit.banished and unit.position is not None
                    and battle.distance_between_units(owner, unit) <= 2)


