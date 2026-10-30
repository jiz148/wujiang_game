"""Chanter battlefield objects and cards."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any
from typing import Iterable
if TYPE_CHECKING:
    from wujiang.tactical.heroes.ai_policy import AIReactionScoreContext
from wujiang.tactical.engine.core import ActionError
from wujiang.tactical.engine.core import Battle
from wujiang.tactical.engine.core import BattleFieldEffect
from wujiang.tactical.engine.core import DamageContext
from wujiang.tactical.engine.core import HeroUnit
from wujiang.tactical.engine.core import Position
from wujiang.tactical.engine.core import QueuedAction
from wujiang.tactical.engine.core import Skill
from wujiang.tactical.engine.core import Stats
from wujiang.tactical.engine.core import StatusEffect
from wujiang.tactical.engine.core import Trait
from wujiang.tactical.heroes.ai_policy import AISkillScoreContext
from wujiang.tactical.heroes.base import AbstractHero
from wujiang.tactical.heroes.common import FlagStatus
from wujiang.tactical.heroes.common import LightWallSkill
from wujiang.tactical.heroes.common import dedupe_positions
from wujiang.tactical.heroes.common import is_mana_drain_immune
from wujiang.tactical.heroes.common import payload_position
from wujiang.tactical.heroes.common import positions_to_dict
from wujiang.tactical.heroes.mechanics.mana_recovery import NaturalManaRecoveryTrait
from wujiang.tactical.heroes.mechanics.next_five_geometry import position_key
from wujiang.tactical.heroes.mechanics.next_five_geometry import square_around_cells
from wujiang.tactical.heroes.mechanics.status_replacement import replace_status_by_name


def card_effect_area(battle: Battle, center: Position, *, include_center: bool = True) -> list[Position]:
    cells = square_around_cells(battle, [center], radius=1)
    if include_center:
        return cells
    return [cell for cell in cells if cell != center]


def cell_in_cells(cell: Position, cells: Iterable[Position]) -> bool:
    key = position_key(cell)
    return any(position_key(candidate) == key for candidate in cells)


def unit_in_cells(battle: Battle, unit: HeroUnit, cells: Iterable[Position]) -> bool:
    keys = {position_key(cell) for cell in cells}
    return any(position_key(cell) in keys for cell in battle.unit_cells(unit))


def consume_pierced_shield_for_effect(battle: Battle, source: HeroUnit, target: HeroUnit, action_name: str) -> None:
    if target.total_shields() <= 0:
        return
    target.consume_one_shield()
    battle.emit_defense_visual_event(source=source, target=target, action_name=action_name, defense_reason="shield_break")
    battle.log_public_event(
        f"{target.name} 的 1 层护盾被【{action_name}】贯穿并打碎。",
        source=source,
        target=target,
    )


class ChanterCardFieldEffect(BattleFieldEffect):
    def __init__(self, owner_unit_id: str, owner_player_id: int, card_type: str, center: Position) -> None:
        names = {
            "paralysis": "麻痹牌",
            "poison": "毒牌",
            "drain": "吸魔牌",
        }
        descriptions = {
            "paralysis": "击中格及周围 3*3 内对方单位不能使用技能。",
            "poison": "击中格及周围 3*3 内对方单位在自己的回合结束时受到伤害 2。",
            "drain": "击中格及周围 3*3 内对方单位在自己的回合结束时被咏唱者吸魔。",
        }
        super().__init__(names[card_type], descriptions[card_type], duration=None)
        self.owner_unit_id = owner_unit_id
        self.owner_player_id = owner_player_id
        self.card_type = card_type
        self.center = center

    def owner_unit(self, battle: Battle) -> HeroUnit | None:
        owner = battle.units.get(self.owner_unit_id)
        if owner is None or not isinstance(owner, HeroUnit) or not owner.alive or owner.banished or owner.position is None:
            return None
        return owner

    def affected_cells(self, battle: Battle) -> list[Position]:
        return card_effect_area(battle, self.center, include_center=True)

    def claw_cells(self, battle: Battle) -> list[Position]:
        return card_effect_area(battle, self.center, include_center=False)

    def board_marker(self, battle: Battle) -> str:
        return {"paralysis": "麻", "poison": "毒", "drain": "吸"}[self.card_type]

    def to_public_dict(self, battle: Battle) -> dict[str, Any]:
        data = super().to_public_dict(battle)
        data["center"] = self.center.to_dict()
        data["card_type"] = self.card_type
        return data

    def blocks_skill_use(self, battle: Battle, actor: HeroUnit, skill: Skill) -> tuple[bool, str]:
        if self.owner_unit(battle) is None or actor.magic_immunity or self.card_type != "paralysis" or actor.player_id == self.owner_player_id:
            return False, ""
        if actor.position is None or actor.banished or not actor.alive:
            return False, ""
        if unit_in_cells(battle, actor, self.affected_cells(battle)):
            return True, f"{actor.name} 处于麻痹牌范围内，不能使用技能。"
        return False, ""

    def on_any_turn_end(self, battle: Battle, ended_player_id: int) -> None:
        owner = self.owner_unit(battle)
        if owner is None:
            battle.remove_field_effect(self)
            return
        if self.card_type not in {"poison", "drain"}:
            return
        area = self.affected_cells(battle)
        recipients = {battle.effect_recipient(unit).unit_id: battle.effect_recipient(unit) for unit in battle.current_turn_bundle_units(include_banished=False)}
        for unit in list(recipients.values()):
            if unit.player_id == self.owner_player_id or unit.position is None or not unit.alive:
                continue
            if unit.magic_immunity or not unit_in_cells(battle, unit, area):
                continue
            target = unit  # type: ignore[assignment]
            if self.card_type == "poison":
                battle.resolve_damage(
                    DamageContext(
                        source=owner,
                        target=target,
                        attack_power=2,
                        is_skill=True,
                        action_name="毒牌",
                        ignore_shield=True,
                        from_field_effect=True,
                        tags={"chanter_card", "poison_card"},
                    )
                )
                continue
            if is_mana_drain_immune(target) or target.current_mana <= 0:
                continue
            target_ctx = battle.validate_target(
                owner,
                target,
                action_name="吸魔牌",
                is_skill=True,
                is_hostile=True,
                ignore_shield=True,
                from_field_effect=True,
                ignore_targeting_restrictions=True,
                tags={"chanter_card", "drain_card"},
            )
            if target_ctx.cancelled:
                battle.log(target_ctx.reason)
                continue
            consume_pierced_shield_for_effect(battle, owner, target, "吸魔牌")
            lost = min(target.current_mana, 1.0)
            target.spend_mana(lost)
            owner.gain_mana(lost)
            battle.log(f"{owner.name} 的吸魔牌吸取了 {target.name} 的 {lost} 点魔力。")


class ChanterCardSkill(Skill):
    ai_early_effect_score = True
    def ai_target_value(self, context: AISkillScoreContext, target: HeroUnit) -> float:
        return _CARD_AI_VALUES[self.card_type].score(context, target)

    def ai_effect_score(self, context: AISkillScoreContext) -> float:
        battle, actor = context.battle, context.actor
        targets = [unit for unit in context.effect_units(self) if unit.player_id != actor.player_id]
        score = -20.0
        for target in targets:
            if not target.magic_immunity:
                score += self.ai_target_value(context, target)
        destination = context.destination()
        enemies = list(context.hostile_units())
        own_cards = [effect for effect in battle.field_effects
                     if getattr(effect, "owner_unit_id", None) == actor.unit_id and hasattr(effect, "card_type")]
        if (destination is not None and actor.position is not None and enemies
                and actor.current_mana >= 1.5
                and battle.can_place_unit(actor, destination, ignore=actor, mover=actor)):
            current_distance = min(battle.distance_between_units(actor, enemy) for enemy in enemies)
            safe_distance = min(context.distance_to_position(enemy, destination) for enemy in enemies)
            has_escape = any(min(context.distance_to_position(enemy, card.center) for enemy in enemies)
                             >= current_distance + 2
                             and battle.can_place_unit(actor, card.center, ignore=actor, mover=actor)
                             for card in own_cards)
            if current_distance <= 3 and safe_distance >= current_distance + 2 and not has_escape:
                score += 65.0
        if actor.current_mana < 1.5:
            score -= 25.0
        return score

    def __init__(self, code: str, name: str, card_type: str) -> None:
        super().__init__(
            code,
            name,
            f"普通技能：费 1 魔，在范内放置一张不占格的{name}。",
            mana_cost=1,
            target_mode="cell",
        )
        self.card_type = card_type

    def can_use(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any] | None = None) -> tuple[bool, str]:
        ok, reason = super().can_use(battle, actor, payload)
        if not ok:
            return ok, reason
        if actor.has_status("形态转换"):
            return False, "形态转换后不能使用三种牌。"
        if payload:
            try:
                self.selected_center(battle, actor, payload)
            except (ActionError, KeyError, TypeError, ValueError) as exc:
                return False, str(exc)
        return True, ""

    def selectable_centers(self, battle: Battle, actor: HeroUnit) -> list[Position]:
        if actor.position is None:
            return []
        return battle.cells_within_distance(actor, actor.targeting_range())

    def queued_payload_metadata(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> dict[str, Any]:
        return {"declared_card_center": positions_to_dict([self.selected_center(battle, actor, payload)])}

    def selected_center(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> Position:
        center = payload_position(payload)
        if center not in self.selectable_centers(battle, actor):
            raise ActionError(f"该格不在{self.name}的放置范围内。")
        return center

    def execute(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> None:
        center = battle.payload_positions(payload, "declared_card_center")[0] if "declared_card_center" in payload else self.selected_center(battle, actor, payload)
        battle.add_field_effect(ChanterCardFieldEffect(actor.unit_id, actor.player_id, self.card_type, center))
        battle.log(f"{actor.name} 在 ({center.x}, {center.y}) 放置了{self.name}。")

    def preview(self, battle: Battle, actor: HeroUnit) -> dict[str, Any]:
        return {
            "cells": positions_to_dict(self.selectable_centers(battle, actor)),
            "target_unit_ids": [],
            "secondary_cells": [],
            "requires_target": True,
        }

    def get_target_cells_for_payload(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> list[Position]:
        return card_effect_area(battle, self.selected_center(battle, actor, payload), include_center=True)

    def ignores_shield_for_payload(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> bool:
        return True


class ParalysisCardAIValue:
    def score(self, context: AISkillScoreContext, target: HeroUnit) -> float:
        battle = context.battle
        covered = any(getattr(effect, "card_type", None) == "paralysis"
                      and getattr(effect, "owner_player_id", None) != target.player_id
                      and set(battle.unit_cells(target)).intersection(effect.affected_cells(battle))
                      for effect in battle.field_effects)
        return 30.0 + min(4, len(target.skills)) * 10.0 if not covered and not target.cannot_use_skills else 0.0


class DrainCardAIValue:
    def score(self, context: AISkillScoreContext, target: HeroUnit) -> float:
        if is_mana_drain_immune(target):
            return 0.0
        actor = context.actor
        return min(1.0, target.current_mana) * (38.0 + (16.0 if actor.current_mana < actor.max_mana() else 0.0))


class PoisonCardAIValue:
    def score(self, context: AISkillScoreContext, target: HeroUnit) -> float:
        with context.probe_rollback():
            ctx = context.battle.resolve_damage(DamageContext(
                source=context.actor, target=target, attack_power=2, is_skill=True,
                action_name="毒牌", ignore_shield=True, from_field_effect=True))
            return ctx.actual_damage * (150.0 if target.cannot_move else 100.0) + (45.0 if ctx.destroyed_as_clone else 0.0)


_CARD_AI_VALUES = {
    "paralysis": ParalysisCardAIValue(),
    "drain": DrainCardAIValue(),
    "poison": PoisonCardAIValue(),
}


class CardTranspositionSkill(Skill):
    ai_reaction_score_phase = "after_hidden_source"

    def __init__(self) -> None:
        super().__init__(
            "card_transposition",
            "移形换位",
            "被动技能：费 0.5 魔，每回合最多 2 次；被敌方动作影响时，可与自己放置的一张牌交换位置。",
            mana_cost=0.5,
            max_uses_per_turn=2,
            target_mode="cell",
            timing="passive",
        )

    def own_cards(self, battle: Battle, actor: HeroUnit) -> list[ChanterCardFieldEffect]:
        return [
            effect
            for effect in battle.field_effects
            if isinstance(effect, ChanterCardFieldEffect) and effect.owner_unit_id == actor.unit_id
        ]

    def selectable_centers(self, battle: Battle, actor: HeroUnit) -> list[Position]:
        if actor.position is None or actor.cannot_move:
            return []
        centers = dedupe_positions([card.center for card in self.own_cards(battle, actor)
                                    if card.center not in battle.unit_cells(actor)
                                    and battle.can_place_unit(actor, card.center, ignore=actor, mover=actor)])
        centers.sort(key=lambda cell: (cell.y, cell.x))
        return centers

    def selected_card(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> ChanterCardFieldEffect:
        center = payload_position(payload)
        for card in self.own_cards(battle, actor):
            if card.center == center:
                return card
        raise ActionError("需要选择一张自己放置的牌。")

    def execute(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> None:
        raise ActionError("移形换位只能通过连锁使用。")

    def ai_reaction_score(self, context: AIReactionScoreContext) -> float:
        destination = context.destination()
        if destination is None:
            return -5.0
        if context.destination_still_targeted(destination):
            return -1000.0
        skill_context = context.skill_context(self.code)
        return (context.threat(context.proxy_target())
                + skill_context.move_score(destination, skill_context.hero_style()) / 2.0 + 22.0)

    def can_react_to(self, battle: Battle, actor: HeroUnit, queued_action: QueuedAction) -> tuple[bool, str]:
        ok, reason = super().can_react_to(battle, actor, queued_action)
        if not ok:
            return ok, reason
        if queued_action.source_player_id == actor.player_id:
            return False, "只能对敌方动作连锁。"
        if battle.reaction_proxy_target(actor, queued_action) is None:
            return False, "当前动作没有影响到自己。"
        if not self.selectable_centers(battle, actor):
            return False, "场上没有自己放置的牌。"
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
        try:
            card = self.selected_card(battle, actor, dict(payload or {}))
        except (ActionError, KeyError, TypeError, ValueError) as exc:
            return False, str(exc)
        if actor.position is None:
            return False, "单位不在战场上。"
        if card.center not in self.selectable_centers(battle, actor):
            return False, "当前不能位移，或该牌不是合法交换落点。"
        return True, ""

    def react(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any], queued_action: QueuedAction) -> None:
        card = self.selected_card(battle, actor, payload)
        if actor.position is None:
            raise ActionError("单位不在战场上。")
        original = actor.position
        battle.move_unit(
            actor,
            card.center,
            via_skill=True,
            allow_anywhere=True,
            max_distance=99,
            triggered_by_reaction=True,
            tags={"card_transposition"},
        )
        card.center = original
        battle.log(f"{actor.name} 与{card.name}交换了位置。")

    def reaction_preview(self, battle: Battle, actor: HeroUnit, queued_action: QueuedAction) -> dict[str, Any]:
        centers = self.selectable_centers(battle, actor)
        return {
            "cells": positions_to_dict(centers),
            "target_unit_ids": [],
            "secondary_cells": [],
            "requires_target": True,
        }


class MagicClawLockStatus(FlagStatus):
    def __init__(self, source_unit_id: str) -> None:
        super().__init__(
            "魔爪",
            "cannot_move",
            description="不能移动或使用位移技能，直到咏唱者下个回合开始前。",
        )
        self.source_unit_id = source_unit_id


class MagicClawSkill(Skill):
    ai_early_effect_score = True
    def ai_effect_score(self, context: AISkillScoreContext) -> float:
        battle, actor = context.battle, context.actor
        targets = [unit for unit in context.effect_units(self) if unit.player_id != actor.player_id]
        before = {unit.unit_id: unit.cannot_move for unit in targets}
        with context.probe_rollback():
            probe = dict(context.payload)
            probe.update(self.queued_payload_metadata(battle, actor, probe))
            self.execute(battle, actor, probe)
            return sum(55.0 for unit in targets if unit.cannot_move and not before[unit.unit_id]) - 24.0

    def __init__(self) -> None:
        super().__init__(
            "magic_claw",
            "魔爪",
            "普通技能：费 1.5 魔，每回合最多 1 次；选择自己的一张牌，使其周围不含中心的对方单位不能移动或使用位移技能，直到咏唱者下个回合开始前。",
            mana_cost=1.5,
            max_uses_per_turn=1,
            target_mode="cell",
        )

    def own_cards(self, battle: Battle, actor: HeroUnit) -> list[ChanterCardFieldEffect]:
        return [
            effect
            for effect in battle.field_effects
            if isinstance(effect, ChanterCardFieldEffect) and effect.owner_unit_id == actor.unit_id
        ]

    def selected_card(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> ChanterCardFieldEffect:
        center = payload_position(payload)
        for card in self.own_cards(battle, actor):
            if card.center == center:
                return card
        raise ActionError("魔爪需要选择自己放置的一张牌。")

    def can_use(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any] | None = None) -> tuple[bool, str]:
        ok, reason = super().can_use(battle, actor, payload)
        if not ok or not payload:
            return ok, reason
        try:
            self.selected_card(battle, actor, payload)
        except (ActionError, KeyError, TypeError, ValueError) as exc:
            return False, str(exc)
        return True, ""

    def queued_payload_metadata(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> dict[str, Any]:
        return {"declared_area_cells": positions_to_dict(self.selected_card(battle, actor, payload).claw_cells(battle))}

    def execute(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> None:
        cells = self.get_target_cells_for_payload(battle, actor, payload)
        for target in battle.effect_units_at_cells(cells):
            if target.player_id == actor.player_id or target.position is None or not target.alive:
                continue
            target_ctx = battle.validate_target(
                actor,
                target,
                action_name="魔爪",
                is_skill=True,
                is_hostile=True,
                ignore_shield=True,
                ignore_targeting_restrictions=True,
                tags={"magic_claw"},
            )
            if target_ctx.cancelled:
                battle.log(target_ctx.reason)
                continue
            lock = MagicClawLockStatus(actor.unit_id)
            lock.source_player_id = actor.player_id
            if any(not trait.accepts_status(lock) for trait in target.traits):
                continue
            consume_pierced_shield_for_effect(battle, actor, target, "魔爪")
            for status in list(target.statuses):
                if isinstance(status, MagicClawLockStatus) and status.source_unit_id == actor.unit_id:
                    target.remove_status(status, battle)
            target.add_status(lock, source=actor)
            battle.log(f"{target.name} 被魔爪束缚，暂时不能位移。")

    def preview(self, battle: Battle, actor: HeroUnit) -> dict[str, Any]:
        cards = self.own_cards(battle, actor)
        centers = [card.center for card in cards]
        affected = dedupe_positions([cell for card in cards for cell in card.claw_cells(battle)])
        return {
            "cells": positions_to_dict(centers),
            "target_unit_ids": [unit.unit_id for unit in battle.effect_units_at_cells(affected) if unit.player_id != actor.player_id],
            "secondary_cells": positions_to_dict(affected),
            "requires_target": True,
        }

    def get_target_cells_for_payload(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> list[Position]:
        return battle.payload_positions(payload, "declared_area_cells") if "declared_area_cells" in payload else self.selected_card(battle, actor, payload).claw_cells(battle)

    def ignores_shield_for_payload(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> bool:
        return True


class FormShiftStatus(StatusEffect):
    def __init__(self) -> None:
        super().__init__("形态转换", "攻 4 / 守 5 / 速 4 / 范 1。")

    def modify_stat(self, stat_name: str, value: float) -> float:
        if stat_name == "attack":
            return value + 3
        if stat_name == "defense":
            return value + 3
        if stat_name == "speed":
            return value + 1
        if stat_name == "attack_range":
            return value - 3
        return value


class FormShiftSkill(Skill):
    def __init__(self) -> None:
        super().__init__(
            "form_shift",
            "形态转换",
            "大招：永久变为攻4守5速4范1，不能再使用三种牌，已放置的牌继续生效。",
            max_uses_per_battle=1,
            target_mode="self",
        )

    def execute(self, battle: Battle, actor: HeroUnit, payload: dict[str, Any]) -> None:
        replace_status_by_name(battle, actor, FormShiftStatus())
        battle.log(f"{actor.name} 进行了形态转换。")

    def preview(self, battle: Battle, actor: HeroUnit) -> dict[str, Any]:
        return {
            "cells": [actor.position.to_dict()] if actor.position else [],
            "target_unit_ids": [actor.unit_id],
            "secondary_cells": [],
            "requires_target": False,
        }


class ChanterCardCleanupTrait(Trait):
    def __init__(self) -> None:
        super().__init__("牌维持", "咏唱者离场时移除自己放置的牌；自己回合开始时清理魔爪束缚。")

    def on_owner_turn_start(self, battle: Battle) -> None:
        owner = self.owner
        if owner is None:
            return
        for unit in battle.all_units():
            for status in list(unit.statuses):
                if isinstance(status, MagicClawLockStatus) and status.source_unit_id == owner.unit_id:
                    unit.remove_status(status, battle)

    def on_owner_banished(self, battle: Battle) -> None:
        self.on_owner_removed(battle)

    def on_owner_removed(self, battle: Battle) -> None:
        owner = self.owner
        if owner is None:
            return
        for effect in list(battle.field_effects):
            if isinstance(effect, ChanterCardFieldEffect) and effect.owner_unit_id == owner.unit_id:
                battle.remove_field_effect(effect)
        for unit in battle.all_units():
            for status in list(unit.statuses):
                if isinstance(status, MagicClawLockStatus) and status.source_unit_id == owner.unit_id:
                    unit.remove_status(status, battle)


class Chanter(AbstractHero):
    hero_code = "chanter"
    hero_name = "咏唱者"
    role = "法师"
    attribute = "暗"
    race = "精灵"
    level = 3
    base_stats = Stats(attack=1, defense=2, speed=3, attack_range=4, mana=5)
    raw_skill_text = "【1麻痹牌(被击中格子及周围3*3对方单位不能使用技能）【1 毒牌（被击中格子及周围3*3对方单位每回合结束时受到2的伤害）【1吸魔牌（被击中格子及周围3*3对方单位每回合结束时被此单位吸魔）【1 光墙 【0.5移形换位（被动技能；一回合2次；与场上的自己放置的“麻痹牌”，“毒牌”或者“吸魔牌”交换位置）【1.5魔爪（一回合最多使用一次；选择一个自己放置的“麻痹牌”，“毒牌”或者“吸魔牌”，那周围3*3所有的对方单位受到以下破魔效果：直到咏唱者下个回合开始前不能移动或使用位移） ￥形态转换（攻4守5速4范1，不能使用“麻痹牌”，“毒牌”，“吸魔牌”）"
    raw_trait_text = "自然回魔"

    def build_skills(self) -> list[Skill]:
        return [
            ChanterCardSkill("paralysis_card", "麻痹牌", "paralysis"),
            ChanterCardSkill("poison_card", "毒牌", "poison"),
            ChanterCardSkill("drain_card", "吸魔牌", "drain"),
            LightWallSkill(),
            CardTranspositionSkill(),
            MagicClawSkill(),
            FormShiftSkill(),
        ]

    def build_traits(self) -> list[Trait]:
        return [NaturalManaRecoveryTrait(), ChanterCardCleanupTrait()]


