"""R303 Daylight Hunter battlefield components."""

from __future__ import annotations

from typing import Any

from wujiang.tactical.engine.core import Battle, DamageContext, Position, Skill, StatusEffect, Trait, Unit
from wujiang.tactical.heroes.ai_policy import HeroAIPolicy
from wujiang.tactical.heroes.mechanics.next_five_effects import (
    apply_piercing_status_effect,
    damage_followup_effect_applies,
)


class DaylightHunterAIPolicy(HeroAIPolicy):
    """Chase the locked target and value repeated attacks on that target."""

    def focused_move_shortlist(self, battle: Battle, actor: Unit,
                               cells: list[Position]) -> list[Position] | None:
        hunt = next((trait for trait in actor.traits if trait.name == "昼行追猎"), None)
        quarry_id = (getattr(hunt, "current_target_id", None)
                     or getattr(hunt, "previous_target_id", None))
        quarry = battle.units.get(quarry_id) if quarry_id else None
        if quarry is None or not quarry.alive or quarry.position is None or quarry.banished:
            return None
        return sorted(cells, key=lambda cell: battle.unit_distance_to_cell(quarry, cell))[:6]

    def post_forced_move_score_bonus(self, battle: Battle, actor: Unit,
                                    destination: Position) -> float:
        hunt = next((trait for trait in actor.traits if trait.name == "昼行追猎"), None)
        preferred_id = (getattr(hunt, "current_target_id", None)
                        or getattr(hunt, "previous_target_id", None))
        preferred = battle.units.get(preferred_id) if preferred_id else None
        if preferred is None or not preferred.alive or preferred.position is None or preferred.banished:
            return 0.0
        distance = battle.unit_distance_to_cell(preferred, destination)
        return max(0.0, actor.targeting_range() + 1.0 - distance) * (
            11.0 + min(5, actor.attack_actions_per_turn()) * 3.0)

    def early_attack_synergy_bonus(self, battle: Battle, actor: Unit,
                                   target: Unit, damage: float) -> float:
        score = 0.0
        hunt = next((trait for trait in actor.traits if trait.name == "昼行追猎"), None)
        if hunt is not None:
            if not getattr(hunt, "current_target_id", None):
                if target.unit_id == getattr(hunt, "previous_target_id", None):
                    score += min(5, getattr(hunt, "previous_bonus", 0) + 1) * 27.0
                elif getattr(hunt, "previous_target_id", None):
                    score -= min(4, getattr(hunt, "previous_bonus", 0) + 1) * 14.0
            if (target.alive and not target.direct_effects_blocked()
                    and not target.has_status("昼行破魔封技")):
                active_count = sum(skill.timing == "active" for skill in target.skills)
                score += min(4, active_count) * 15.0
        if damage > 0 and not actor.cannot_heal:
            score += min(0.25, max(0.0, actor.max_health - actor.current_hp)) * 140.0
        return score


class DaylightActiveSealStatus(StatusEffect):
    def __init__(self) -> None:
        super().__init__("昼行破魔封技", "下一个本人回合不能使用主动技能。",
                         duration=1, tick_scope="owner_turn_end")

    def blocks_skill_use(self, battle: Battle, actor: Unit, skill: Skill) -> tuple[bool, str]:
        if actor is self.owner and skill.timing == "active":
            return True, "昼行破魔使该单位本回合不能使用主动技能。"
        return False, ""


class DaylightHunterTrait(Trait):
    def __init__(self) -> None:
        super().__init__("昼行追猎", "每本人轮只普攻一个目标；连续追猎增加普攻次数，低半血免技能伤害，普攻破魔封主动。")
        self.previous_target_id: str | None = None
        self.previous_bonus = 0
        self.current_target_id: str | None = None
        self.current_bonus = 0

    def on_owner_turn_start(self, battle: Battle) -> None:
        self.current_target_id = None
        self.current_bonus = 0
        previous = battle.units.get(self.previous_target_id) if self.previous_target_id else None
        if previous is None or not previous.alive or previous.banished:
            self.previous_target_id = None
            self.previous_bonus = 0

    def on_owner_turn_end(self, battle: Battle) -> None:
        self.previous_target_id = self.current_target_id
        self.previous_bonus = self.current_bonus if self.current_target_id else 0

    def on_owner_removed(self, battle: Battle) -> None:
        self.previous_target_id = None
        self.previous_bonus = 0
        self.current_target_id = None
        self.current_bonus = 0

    def modify_attack_actions_per_turn(self, value: int) -> int:
        bonus = (self.previous_bonus + 1 if self.previous_target_id else 0) if self.current_target_id is None else self.current_bonus
        return value + bonus

    def can_use_basic_attack(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> tuple[bool, str]:
        if self.owner is not actor or not actor.can_take_turn_actions(battle):
            return True, ""
        cells = (battle.payload_positions(payload, "attack_cells")
                 or battle.basic_attack_area_cells_for_payload(actor, payload) or [])
        if not cells:
            return True, ""
        recipients = battle.effect_units_at_cells(cells, ignore=actor)
        if len(recipients) > 1:
            return False, "昼行武者本回合只能普攻一个单位。"
        if (recipients and self.current_target_id is not None
                and recipients[0].unit_id != self.current_target_id):
            return False, "昼行武者本回合只能普攻已经锁定的单位。"
        return True, ""

    def can_attack_target(self, battle: Battle, actor: Unit, target: Unit) -> tuple[bool, str]:
        recipient = battle.effect_recipient(target)
        if (self.owner is actor and actor.can_take_turn_actions(battle)
                and self.current_target_id is not None and recipient.unit_id != self.current_target_id):
            return False, "昼行武者本回合只能普攻已经锁定的单位。"
        return True, ""

    def on_before_damage(self, battle: Battle, ctx: DamageContext) -> None:
        owner = self.owner
        if (owner is not None and ctx.target is owner and ctx.is_skill and not ctx.cancelled
                and owner.current_hp < owner.max_health / 2):
            ctx.cancelled = True
            ctx.preserve_followup_effects = True
            ctx.reason = f"{owner.name} 低于半血，免疫了技能伤害。"
            battle.emit_defense_visual_event(source=ctx.source, target=owner,
                                             action_name=ctx.action_name,
                                             defense_reason="skill_damage_immunity")

    def on_basic_attack_finished(self, battle: Battle, actor: Unit, payload: dict[str, Any],
                                 damage_contexts: list[DamageContext], missed: bool) -> None:
        if actor is not self.owner:
            return
        target_id = str(payload.get("target_unit_id") or "")
        if damage_contexts:
            target_id = damage_contexts[0].target.unit_id
        elif target_id and target_id in battle.units:
            target_id = battle.effect_recipient(battle.units[target_id]).unit_id
        if (self.current_target_id is None and target_id and not payload.get("reaction_attack")
                and actor.can_take_turn_actions(battle)):
            self.current_target_id = target_id
            self.current_bonus = self.previous_bonus + 1 if target_id == self.previous_target_id else 0
            battle.log_public_event(
                f"{actor.name} 锁定昼行追猎目标；本回合可普攻 {actor.attack_actions_per_turn()} 次。",
                source=actor, target=battle.units.get(target_id))
        for ctx in damage_contexts:
            if (ctx.source is not actor or ctx.is_skill or not ctx.target.alive
                    or not damage_followup_effect_applies(ctx, allow_on_shield_break=True)
                    or (ctx.cancelled and not ctx.shield_consumed)):
                continue
            apply_piercing_status_effect(
                battle, actor, ctx.target, action_name="昼行破魔", status=DaylightActiveSealStatus(),
                is_skill=False, tags={"attack", "daylight_seal"}, consume_shield=False,
                cannot_evade=False)

    def to_public_dict(self, battle: Battle) -> dict[str, Any]:
        data = super().to_public_dict(battle)
        data.update(locked_target_id=self.current_target_id,
                    previous_target_id=self.previous_target_id,
                    consecutive_bonus=self.current_bonus,
                    potential_attacks=self.owner.attack_actions_per_turn() if self.owner is not None else 1)
        return data


