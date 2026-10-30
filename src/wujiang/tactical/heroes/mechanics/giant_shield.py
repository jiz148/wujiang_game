"""Giant-shield family battlefield skills, traits, and statuses (R254-R257)."""

from __future__ import annotations

from typing import Any

from wujiang.tactical.engine.core import (
    ActionError, ActionMiss, Battle, DamageContext, MoveContext, Position,
    Skill, StatusEffect, TargetContext, Trait, Unit,
)
from wujiang.tactical.heroes.common import (
    StatModifierStatus, pattern_selection_preview, pattern_signature,
    payload_target_unit, positions_to_dict,
)
from wujiang.tactical.heroes.ai_policy import (AIReactionScoreContext,
    AISkillCandidateContext, AISkillScoreContext, HeroAIPolicy)
from wujiang.tactical.heroes.mechanics.declared_area import DeclaredAreaSkillMixin
from wujiang.tactical.heroes.mechanics.next_five_effects import remote_rectangle_patterns
from wujiang.tactical.heroes.mechanics.next_five_geometry import square_around_cells


class GiantShieldMovePolicy(HeroAIPolicy):
    """Keep shield partners adjacent, especially while a wall is active."""

    def late_move_score_bonus(self, battle: Battle, actor: Unit,
                              destination: Position, allies: list[Unit]) -> float:
        shield_allies = [unit for unit in allies if "巨盾" in unit.name]
        if not shield_allies:
            return 0.0
        near_now = min(battle.distance_between_units(actor, unit) for unit in shield_allies) <= 1
        near_after = min(battle.unit_distance_to_cell(unit, destination) for unit in shield_allies) <= 1
        score = (13.0 if near_after else 0.0) - (13.0 if near_now else 0.0)
        if actor.has_status("盾墙") and near_now and not near_after:
            score -= 34.0
        return score


def shield_named_neighbor(battle: Battle, owner: Unit) -> bool:
    return any(unit is not owner and unit.alive and not unit.banished and unit.position is not None
               and "巨盾" in unit.name and battle.distance_between_units(owner, unit) <= 1
               for unit in battle.all_units())


class GiantShieldNeighborTrait(Trait):
    def __init__(self) -> None:
        super().__init__("巨盾相邻减伤", "周围有显示名含巨盾的单位时，本人的每项数值伤害减免额+1。")


def giant_shield_reduction_bonus(battle: Battle, target: Unit) -> float:
    return float(any(isinstance(trait, GiantShieldNeighborTrait) for trait in target.traits)
                 and shield_named_neighbor(battle, target))


class GiantShieldRaiseStatus(StatusEffect):
    def __init__(self) -> None:
        super().__init__("举盾", "一轮内近身伤害-1、其他伤害-2；同名不叠加。",
                         duration=1, tick_scope="owner_turn_end")

    def on_before_damage(self, battle: Battle, ctx: DamageContext) -> None:
        target = self.owner
        if target is None or target is not ctx.target or "giant_shield_raise" in ctx.modifier_receipts:
            return
        ctx.modifier_receipts.add("giant_shield_raise")
        close = (ctx.source is not None and not ctx.from_field_effect
                 and ctx.source.position is not None and battle.distance_between_units(ctx.source, target) <= 1)
        reduction = (1.0 if close else 2.0) + giant_shield_reduction_bonus(battle, target)
        if ctx.raw_damage is None:
            ctx.attack_power = max(0.0, ctx.attack_power - reduction)
        else:
            ctx.raw_damage = max(0.0, ctx.raw_damage - reduction)


class GiantShieldRaiseSkill(Skill):
    def ai_priority_score(self, context: AISkillScoreContext) -> float:
        battle, actor = context.battle, context.actor
        allies = [unit for unit in self.beneficiaries(battle, actor)
                  if not unit.has_status("举盾") and not unit.skill_non_damage_effects_blocked()]
        enemies = [unit for unit in context.hostile_units()
                   if unit.position is not None and not unit.banished]
        score = 0.0
        for ally in allies:
            danger = max((enemy.stat("attack") * 6 + 18
                          if battle.distance_between_units(enemy, ally) <= enemy.targeting_range() + min(2, enemy.stat("speed"))
                          else 0.0 for enemy in enemies), default=0.0)
            score += danger * (0.55 if ally.current_hp <= 0.5 else 0.4)
        return score - (15.0 if actor.current_mana <= 1 else 9.0) if score >= 18.0 else -1000.0

    def __init__(self) -> None:
        super().__init__("shield_raise", "举盾", "0.5魔；施放时5×5内己方单位一轮近身伤害-1、其他-2，同名不叠加。",
                         mana_cost=0.5, target_mode="self")

    def beneficiaries(self, battle: Battle, actor: Unit) -> list[Unit]:
        cells = square_around_cells(battle, battle.unit_cells(actor), radius=2)
        return [unit for unit in battle.effect_units_at_cells(cells)
                if unit.player_id == actor.player_id and unit.alive and not unit.banished]

    def preview(self, battle: Battle, actor: Unit) -> dict[str, Any]:
        units = self.beneficiaries(battle, actor)
        return {"cells": positions_to_dict(square_around_cells(battle, battle.unit_cells(actor), radius=2)),
                "target_unit_ids": [unit.unit_id for unit in units], "secondary_cells": [],
                "requires_target": False}

    def get_target_units_for_payload(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> list[Unit]:
        return self.beneficiaries(battle, actor)

    def execute(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> None:
        for target in self.beneficiaries(battle, actor):
            if target.skill_non_damage_effects_blocked() or target.direct_effects_blocked():
                continue
            existing = target.get_status("举盾")
            if existing is not None:
                existing.duration = max(int(existing.duration or 0), 1)
            else:
                target.add_status(GiantShieldRaiseStatus(), source=actor)
            battle.log_public_event(f"{target.name} 获得一轮【举盾】减伤。", source=actor, target=target)


class GiantShieldFlatStatus(StatModifierStatus):
    def __init__(self) -> None:
        super().__init__("平射降守", defense_delta=-1, description="四轮守-1；同名刷新而不叠加。",
                         duration=4, tick_scope="owner_turn_end")


class GiantShieldFlatShotSkill(Skill):
    def ai_priority_score(self, context: AISkillScoreContext) -> float:
        battle, actor = context.battle, context.actor
        target = battle.units.get(str(context.payload.get("target_unit_id") or ""))
        target = battle.effect_recipient(target) if target is not None else None
        if target is None or target.player_id == actor.player_id or target.magic_immunity:
            return -1000.0
        score = context.r18_effect_score(self)
        if score > -1000 and not target.has_status("平射降守") and not target.skill_non_damage_effects_blocked():
            score += 19.0 + max(0.0, target.current_hp) * 8.0
        return score

    def __init__(self) -> None:
        super().__init__("shield_flat_shot", "平射", "每本人轮一次；范内单体按当前攻伤害，命中后守-1四轮，同名不叠加。",
                         max_uses_per_turn=1, target_mode="unit")

    def targets(self, battle: Battle, actor: Unit) -> list[Unit]:
        if actor.has_status("巨盾拔刀"):
            return []
        return [unit for unit in battle.all_units() if unit.alive and not unit.banished and unit.position is not None
                and battle.unit_can_be_selected(unit, actor=actor)[0]
                and battle.unit_target_in_range_and_line(actor, unit, actor.targeting_range())]

    def can_use(self, battle: Battle, actor: Unit, payload: dict[str, Any] | None = None) -> tuple[bool, str]:
        ok, reason = super().can_use(battle, actor, payload)
        if ok and actor.has_status("巨盾拔刀"):
            return False, "拔刀后不能使用火枪技能。"
        if ok and payload and payload.get("target_unit_id"):
            if str(payload["target_unit_id"]) not in {unit.unit_id for unit in self.targets(battle, actor)}:
                return False, "平射需选择范内可见单位。"
        return ok, reason

    def declared_cell(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> Position:
        if payload.get("shield_flat_cell"):
            return Position(**payload["shield_flat_cell"])
        target = payload_target_unit(battle, payload)
        if target.unit_id not in {unit.unit_id for unit in self.targets(battle, actor)}:
            raise ActionError("平射目标已不合法。")
        cell = battle.declared_cell_for_target(actor, target, payload)
        if cell is None:
            raise ActionError("平射需要声明目标身体格。")
        return cell

    def queued_payload_metadata(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> dict[str, Any]:
        return {"shield_flat_cell": self.declared_cell(battle, actor, payload).to_dict()}

    def get_target_cells_for_payload(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> list[Position]:
        return [self.declared_cell(battle, actor, payload)]

    def get_target_units_for_payload(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> list[Unit]:
        return battle.effect_units_at_cells(self.get_target_cells_for_payload(battle, actor, payload))

    def preview(self, battle: Battle, actor: Unit) -> dict[str, Any]:
        units = self.targets(battle, actor)
        return {"cells": positions_to_dict([cell for unit in units for cell in battle.unit_cells(unit)]),
                "target_unit_ids": [unit.unit_id for unit in units], "secondary_cells": [],
                "requires_target": True}

    def execute(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> None:
        cells = self.get_target_cells_for_payload(battle, actor, payload)
        for target in battle.effect_units_at_cells(cells):
            ctx = battle.resolve_damage(DamageContext(source=actor, target=target,
                attack_power=actor.stat("attack"), is_skill=True, action_name=self.name,
                tags={"skill", self.code}))
            if (target.alive and not ctx.cancelled
                    and not target.skill_non_damage_effects_blocked()):
                old = target.get_status("平射降守")
                if old is not None:
                    target.remove_status(old, battle)
                target.add_status(GiantShieldFlatStatus(), source=actor)
                battle.log_public_event(f"{target.name} 被平射命中，守-1持续四轮。", source=actor, target=target)


class GiantShieldLobSkill(DeclaredAreaSkillMixin, Skill):
    def ai_priority_score(self, context: AISkillScoreContext) -> float:
        return context.r18_effect_score(self)

    def __init__(self) -> None:
        super().__init__("shield_lob", "抛射", "每本人轮一次；按范选择3×3区域，对双方按当前攻造成技能伤害。",
                         max_uses_per_turn=1, target_mode="cell")

    def patterns(self, battle: Battle, actor: Unit) -> list[list[Position]]:
        return remote_rectangle_patterns(battle, actor, 3, 3)

    def queued_payload_metadata(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> dict[str, Any]:
        return {"declared_area_cells": positions_to_dict(self.chosen_cells(battle, actor, payload))}

    def execute(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> None:
        cells = battle.payload_positions(payload, "declared_area_cells") if "declared_area_cells" in payload else self.chosen_cells(battle, actor, payload)
        for target in battle.effect_units_at_cells(cells):
            battle.resolve_damage(DamageContext(source=actor, target=target,
                attack_power=actor.stat("attack"), is_skill=True, action_name=self.name,
                area_cell_hits=battle.unit_hit_count_for_cells(target, cells), tags={"skill", self.code}))

    def preview(self, battle: Battle, actor: Unit) -> dict[str, Any]:
        preview = pattern_selection_preview(self.patterns(battle, actor))
        covered = {cell for pattern in self.patterns(battle, actor) for cell in pattern}
        preview.update({"target_unit_ids": [unit.unit_id for unit in battle.effect_units_at_cells(list(covered))
                                            if battle.unit_can_be_selected(unit, actor=actor)[0]],
                        "secondary_cells": [], "requires_target": True})
        return preview

    def get_target_units_for_payload(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> list[Unit]:
        return battle.effect_units_at_cells(self.chosen_cells(battle, actor, payload))

    def get_target_cells_for_payload(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> list[Position]:
        return self.chosen_cells(battle, actor, payload)


class GiantShieldAllPierceTrait(Trait):
    def __init__(self) -> None:
        super().__init__("所有伤害破魔", "本人造成的普攻和技能伤害破一层盾，不能无视魔免。")

    def basic_attack_payload_metadata(self, battle: Battle, actor: Unit,
                                      payload: dict[str, Any] | None = None) -> dict[str, Any]:
        return {"ignore_shield": True}

    def on_targeted(self, battle: Battle, ctx: TargetContext) -> None:
        if self.owner is ctx.actor and ctx.damage_target:
            ctx.ignore_shield = True

    def on_before_damage(self, battle: Battle, ctx: DamageContext) -> None:
        if self.owner is ctx.source:
            ctx.ignore_shield = True


class GiantShieldBlockTrait(Trait):
    def __init__(self) -> None:
        super().__init__("巨盾完全格挡", "每次可能受到生命伤害时有1/3几率完全格挡，结果公开。")

    def on_final_damage(self, battle: Battle, ctx: DamageContext) -> None:
        if self.owner is ctx.target:
            ctx.complete_block_chance = max(ctx.complete_block_chance, 1 / 3)
            ctx.complete_block_label = "巨盾完全格挡"


class GiantShieldWallStatus(StatusEffect):
    def __init__(self, partner_id: str, spear_id: str, wall_defense: float = 4.0,
                 block_chance: float = 1 / 3, sword_guard: bool = False) -> None:
        super().__init__("盾墙", f"与指定巨盾友军相邻时，两人按守{wall_defense:g}并共享格挡。")
        self.partner_id = partner_id
        self.spear_id = spear_id
        self.wall_defense = wall_defense
        self.block_chance = block_chance
        self.sword_guard = sword_guard

    def on_before_damage(self, battle: Battle, ctx: DamageContext) -> None:
        if (not self.sword_guard or self.owner is not ctx.target or not self.active_pair(battle)
                or ctx.ignore_shield or ctx.half_ignore_shield
                or "giant_shield_sword_guard" in ctx.modifier_receipts):
            return
        ctx.modifier_receipts.add("giant_shield_sword_guard")
        close = bool(ctx.source is not None and ctx.source.position is not None
                     and not ctx.from_field_effect and battle.distance_between_units(ctx.source, ctx.target) <= 1)
        reduction = (1.0 if close else 2.0) + float(shield_named_neighbor(battle, ctx.target))
        if ctx.raw_damage is None:
            ctx.attack_power = max(0.0, ctx.attack_power - reduction)
        else:
            ctx.raw_damage = max(0.0, ctx.raw_damage - reduction)

    def active_pair(self, battle: Battle) -> bool:
        owner = self.owner
        partner = battle.units.get(self.partner_id)
        spear = battle.units.get(self.spear_id)
        return bool(owner is not None and owner.alive and owner.position is not None and not owner.banished
                    and partner is not None and partner.alive and partner.position is not None and not partner.banished
                    and spear is not None and spear.alive and not spear.banished
                    and owner.player_id == partner.player_id and battle.distance_between_units(owner, partner) <= 1)

    def on_final_damage(self, battle: Battle, ctx: DamageContext) -> None:
        if self.owner is ctx.target and self.active_pair(battle):
            ctx.defense_override = self.wall_defense
            ctx.complete_block_chance = max(ctx.complete_block_chance, self.block_chance)
            ctx.complete_block_label = "盾墙格挡"

    def on_unit_moved(self, battle: Battle, ctx: MoveContext) -> None:
        if self.owner is not None and not self.active_pair(battle):
            self.owner.remove_status(self, battle)

    def on_battle_damage(self, battle: Battle, ctx: DamageContext) -> None:
        if self.owner is not None and not self.active_pair(battle):
            self.owner.remove_status(self, battle)

    def on_owner_banished(self, battle: Battle) -> None:
        if self.owner is not None:
            self.owner.remove_status(self, battle)

    def on_owner_removed(self, battle: Battle) -> None:
        if self.owner is not None:
            self.owner.remove_status(self, battle)

    def on_removed(self, battle: Battle) -> None:
        partner = battle.units.get(self.partner_id)
        if partner is not None and self.owner is not None:
            for status in list(partner.statuses):
                if (isinstance(status, GiantShieldWallStatus) and status.partner_id == self.owner.unit_id
                        and status.spear_id == self.spear_id):
                    partner.remove_status(status, battle)


class GiantShieldWallSkill(Skill):
    def ai_priority_score(self, context: AISkillScoreContext) -> float:
        battle, actor = context.battle, context.actor
        partner = battle.units.get(str(context.payload.get("target_unit_id") or ""))
        if partner is None or partner.player_id != actor.player_id or "巨盾" not in partner.name:
            return -1000.0
        enemies = [unit for unit in context.hostile_units()
                   if unit.position is not None and not unit.banished]
        pressure = sum(1 for unit in (actor, partner)
                       if any(battle.distance_between_units(unit, enemy) <= enemy.targeting_range() + min(2, enemy.stat("speed"))
                              for enemy in enemies))
        if pressure == 0:
            return -1000.0
        return 17.0 + pressure * 18.0 + max(0.0, 4 - partner.stat("defense")) * 7.0 - (10.0 if actor.current_mana <= 1 else 5.0)

    def __init__(self, *, sword: bool = False) -> None:
        self.sword_guard = sword
        self.wall_defense = 5.0 if sword else 4.0
        self.block_chance = 0.5 if sword else 1 / 3
        super().__init__("shield_wall", "盾墙", f"0.5魔；选相邻己方巨盾组成守{self.wall_defense:g}双人盾墙，分开即解除。",
                         mana_cost=0.5, target_mode="ally")

    def partners(self, battle: Battle, actor: Unit) -> list[Unit]:
        defender = battle.effect_recipient(actor)
        return [unit for unit in battle.all_units() if unit is not actor and unit.player_id == actor.player_id
                and unit.alive and not unit.banished and unit.position is not None and "巨盾" in unit.name
                and battle.effect_recipient(unit) is not defender
                and battle.distance_between_units(defender, battle.effect_recipient(unit)) <= 1
                and not battle.effect_recipient(unit).skill_non_damage_effects_blocked()
                and not any(isinstance(status, GiantShieldWallStatus) and status.active_pair(battle)
                            for status in battle.effect_recipient(unit).statuses)]

    def can_use(self, battle: Battle, actor: Unit, payload: dict[str, Any] | None = None) -> tuple[bool, str]:
        ok, reason = super().can_use(battle, actor, payload)
        if not ok:
            return ok, reason
        if any(isinstance(status, GiantShieldWallStatus) and status.active_pair(battle)
               for status in battle.effect_recipient(actor).statuses):
            return False, "当前盾墙仍有效。"
        if not self.partners(battle, actor):
            return False, "需相邻己方巨盾单位。"
        if payload and payload.get("target_unit_id"):
            if str(payload["target_unit_id"]) not in {unit.unit_id for unit in self.partners(battle, actor)}:
                return False, "盾墙伙伴已不合法。"
        return True, ""

    def preview(self, battle: Battle, actor: Unit) -> dict[str, Any]:
        partners = self.partners(battle, actor)
        return {"cells": positions_to_dict([cell for unit in partners for cell in battle.unit_cells(unit)]),
                "target_unit_ids": [unit.unit_id for unit in partners], "secondary_cells": [],
                "requires_target": True}

    def get_target_units_for_payload(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> list[Unit]:
        return [battle.effect_recipient(payload_target_unit(battle, payload))]

    def execute(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> None:
        selected = payload_target_unit(battle, payload)
        if selected.unit_id not in {unit.unit_id for unit in self.partners(battle, actor)}:
            raise ActionMiss("盾墙伙伴已不相邻，结墙落空。")
        defender = battle.effect_recipient(actor)
        partner = battle.effect_recipient(selected)
        defender.add_status(GiantShieldWallStatus(partner.unit_id, actor.unit_id,
                                                   self.wall_defense, self.block_chance, self.sword_guard), source=actor)
        partner.add_status(GiantShieldWallStatus(defender.unit_id, actor.unit_id,
                                                  self.wall_defense, self.block_chance, self.sword_guard), source=actor)
        battle.log_public_event(f"{actor.name} 与{partner.name}共同举起盾墙，受伤按守{self.wall_defense:g}。", source=actor, target=partner)


class GiantShieldSwordTrait(Trait):
    def __init__(self) -> None:
        super().__init__("巨盾剑士守势", "可能损血时50%完全格挡；非破魔近身/远程伤害减1/2；可反击两次。")

    def grants_counter(self, battle: Battle, unit: Unit) -> bool:
        return self.owner is unit

    def ai_counter_bonus(self, context: AIReactionScoreContext) -> float:
        actor, battle = context.actor, context.battle
        if self.owner is not actor or actor.position is None:
            return 0.0
        score = 0.0
        for unit in battle.effect_units_at_cells(battle.neighbors(actor.position), ignore=actor):
            expected_second = context.damage(unit, actor.stat("attack")) * 85.0
            score += expected_second if unit.player_id != actor.player_id else -expected_second * 1.45
        return score

    def on_before_damage(self, battle: Battle, ctx: DamageContext) -> None:
        owner = self.owner
        if (owner is not ctx.target or ctx.ignore_shield or ctx.half_ignore_shield
                or "giant_shield_sword_guard" in ctx.modifier_receipts):
            return
        ctx.modifier_receipts.add("giant_shield_sword_guard")
        close = bool(ctx.source is not None and ctx.source.position is not None
                     and not ctx.from_field_effect and battle.distance_between_units(ctx.source, owner) <= 1)
        reduction = (1.0 if close else 2.0) + giant_shield_reduction_bonus(battle, owner)
        if ctx.raw_damage is None:
            ctx.attack_power = max(0.0, ctx.attack_power - reduction)
        else:
            ctx.raw_damage = max(0.0, ctx.raw_damage - reduction)

    def on_final_damage(self, battle: Battle, ctx: DamageContext) -> None:
        if self.owner is ctx.target:
            ctx.complete_block_chance = max(ctx.complete_block_chance, 0.5)
            ctx.complete_block_label = "巨盾剑士完全格挡"

    def on_basic_attack_finished(self, battle: Battle, actor: Unit, payload: dict[str, Any],
                                 damage_contexts: list[DamageContext], missed: bool) -> None:
        if self.owner is not actor or not payload.get("counter") or actor.position is None:
            return
        cells = [cell for cell in battle.neighbors(actor.position) if battle.in_bounds(cell)]
        for target in battle.effect_units_at_cells(cells, ignore=actor):
            if not actor.alive:
                break
            battle.resolve_attack_damage(actor, target, action_name="反击第二击",
                                         tags={"counter", "area_attack", "giant_shield_sword_second"},
                                         payload={"area_attack": True})


class GiantShieldSwordDanceStatus(StatusEffect):
    def __init__(self) -> None:
        super().__init__("剑舞连击", "每回合额外两次普通攻击，不叠加。")

    def modify_attack_actions_per_turn(self, value: int) -> int:
        return value + 2


class GiantShieldDanceGuardStatus(StatusEffect):
    def __init__(self) -> None:
        super().__init__("剑舞下回合近战免伤", "施放后下一个实际武将回合内，近身攻击不造成生命伤害。")
        self.active = False

    def on_any_turn_start(self, battle: Battle, active_unit: Unit) -> None:
        self.active = True

    def on_any_turn_end(self, battle: Battle, ended_player_id: int) -> None:
        if self.active and battle.current_turn_unit() is not None and self.owner is not None:
            self.owner.remove_status(self, battle)

    def on_before_hp_loss(self, battle: Battle, ctx: DamageContext) -> None:
        if (self.active and self.owner is ctx.target and ctx.source is not None
                and ctx.source.position is not None and not ctx.from_field_effect
                and battle.distance_between_units(ctx.source, ctx.target) <= 1):
            ctx.raw_damage = 0.0
            ctx.modifier_receipts.add("giant_shield_dance_guard")


class GiantShieldSwordDanceSkill(Skill):
    def ai_priority_score(self, context: AISkillScoreContext) -> float:
        battle, actor = context.battle, context.actor
        enemies = list(context.hostile_units())
        reachable = any(enemy.position is not None and battle.distance_between_units(actor, enemy)
                        <= actor.normal_move_distance() + 1 for enemy in enemies)
        return (92.0 if reachable else 54.0) - (16.0 if actor.current_mana <= 1 else 0.0) if enemies else -1000.0

    def __init__(self) -> None:
        super().__init__("shield_sword_dance", "剑舞", "0.5魔；下一个武将回合近战免伤；此后每回合多攻击两次。",
                         mana_cost=0.5, target_mode="self")

    def can_use(self, battle: Battle, actor: Unit, payload: dict[str, Any] | None = None) -> tuple[bool, str]:
        ok, reason = super().can_use(battle, actor, payload)
        if ok and actor.has_status("剑舞连击"):
            return False, "剑舞连击已生效。"
        return ok, reason

    def execute(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> None:
        if not actor.has_status("剑舞连击"):
            actor.add_status(GiantShieldSwordDanceStatus(), source=actor)
            actor.add_status(GiantShieldDanceGuardStatus(), source=actor)
            battle.log_public_event(f"{actor.name} 发动剑舞，下个武将回合近战免伤且此后每回合多攻击两次。", source=actor)

    def preview(self, battle: Battle, actor: Unit) -> dict[str, Any]:
        return {"cells": positions_to_dict(battle.unit_cells(actor)), "target_unit_ids": [actor.unit_id],
                "secondary_cells": [], "requires_target": False}


class GiantShieldBashChargeStatus(StatusEffect):
    def __init__(self) -> None:
        super().__init__("盾击护身", "下一次可能损血的独立伤害无效。")

    def on_before_hp_loss(self, battle: Battle, ctx: DamageContext) -> None:
        if self.owner is ctx.target and ctx.raw_damage is not None and ctx.raw_damage > 0:
            ctx.raw_damage = 0.0
            ctx.modifier_receipts.add("giant_shield_bash")
            battle.log_public_event(f"{ctx.target.name} 的盾击挡下下一次伤害。", source=ctx.target, target=ctx.target)
            ctx.target.remove_status(self, battle)


class GiantShieldBashSkill(Skill):
    ai_reaction_score_phase = "after_hidden_source"

    def __init__(self) -> None:
        super().__init__("shield_bash", "盾击", "0.5魔；每个武将回合一次，被影响时连锁；挡下一次可能损血的伤害。",
                         mana_cost=0.5, max_uses_per_turn=1, timing="passive", target_mode="self")

    def can_react_to(self, battle: Battle, actor: Unit, queued_action: Any) -> tuple[bool, str]:
        ok, reason = super().can_react_to(battle, actor, queued_action)
        if not ok:
            return ok, reason
        if queued_action.source_player_id == actor.player_id or battle.reaction_proxy_target(actor, queued_action) is None:
            return False, "盾击只能对影响自己的敌方动作连锁。"
        if actor.has_status("盾击护身"):
            return False, "盾击护身已生效。"
        return True, ""

    def reaction_preview(self, battle: Battle, actor: Unit, queued_action: Any) -> dict[str, Any]:
        return {"cells": positions_to_dict(battle.unit_cells(actor)), "target_unit_ids": [actor.unit_id],
                "secondary_cells": [], "requires_target": False}

    def execute(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> None:
        raise ActionError("盾击只能通过连锁使用。")

    def react(self, battle: Battle, actor: Unit, payload: dict[str, Any], queued_action: Any) -> None:
        actor.add_status(GiantShieldBashChargeStatus(), source=actor)
        battle.log_public_event(f"{actor.name} 使用盾击，下一次可能损血的伤害无效。", source=actor)

    def ai_reaction_score(self, context: AIReactionScoreContext) -> float:
        if context.actor.has_status("盾击护身"):
            return -1000.0
        threat = context.threat(context.proxy_target())
        return threat + (75.0 if threat >= context.actor.current_hp * 100 else 0.0) - 35.0


class GiantShieldGunAreaSkill(GiantShieldLobSkill):
    def ai_priority_score(self, context: AISkillScoreContext) -> float:
        battle, actor = context.battle, context.actor
        cells = context.positions(context.payload.get("cells"))
        if not cells:
            return -1000.0
        score, enemy_hits = 0.0, 0
        for target in battle.effect_units_at_cells(cells):
            if target.magic_immunity or target.dodge_charges > 0:
                continue
            power = actor.stat("attack") + max(0, battle.unit_hit_count_for_cells(target, cells) - 1)
            damage = context.estimate_damage(target, power, ignore_shield=True)
            value = min(target.current_hp, damage) * 100.0 + (20.0 if target.total_shields() > 0 else 0.0)
            if target.player_id == actor.player_id:
                score -= value * 1.45
            else:
                score += value
                enemy_hits += 1
        return score - (13.0 if self.code == "shield_volley" else 7.0) if enemy_hits else -1000.0

    def ai_candidate_patterns(self, context: AISkillCandidateContext,
                              patterns: list) -> list:
        ranked: list[tuple[float, Any]] = []
        for pattern in patterns:
            cells = context.positions(pattern)
            score = sum((1.0 if unit.player_id != context.actor.player_id else -1.45)
                        * (2.0 if unit.current_hp <= 0.5 else 1.0)
                        * max(1, context.battle.unit_hit_count_for_cells(unit, cells))
                        for unit in context.battle.effect_units_at_cells(cells)
                        if not unit.magic_immunity)
            if score > 0:
                ranked.append((score, pattern))
        ranked.sort(key=lambda entry: entry[0], reverse=True)
        return [pattern for _, pattern in ranked[:24]]

    def __init__(self, *, volley: bool = False) -> None:
        Skill.__init__(self, "shield_volley" if volley else "shield_sweep", "排射" if volley else "扫射",
                       "按范选择1×5横纵区域，冷却2轮，对双方造成当前攻伤害。" if volley
                       else "每本人轮一次；按范选择4×4区域，对双方造成当前攻伤害。",
                       cooldown_turns=2 if volley else 0,
                       max_uses_per_turn=None if volley else 1, target_mode="cell")
        self.volley = volley

    def patterns(self, battle: Battle, actor: Unit) -> list[list[Position]]:
        if self.volley:
            patterns = remote_rectangle_patterns(battle, actor, 1, 5)
            patterns += remote_rectangle_patterns(battle, actor, 5, 1)
            return list({pattern_signature(pattern): pattern for pattern in patterns}.values())
        return remote_rectangle_patterns(battle, actor, 4, 4)

    def can_use(self, battle: Battle, actor: Unit, payload: dict[str, Any] | None = None) -> tuple[bool, str]:
        ok, reason = super().can_use(battle, actor, payload)
        if ok and actor.has_status("巨盾拔刀"):
            return False, "拔刀后不能使用火枪技能。"
        return ok, reason


class GiantShieldBladeStatus(StatusEffect):
    def __init__(self) -> None:
        super().__init__("巨盾拔刀", "攻击改为近战，范1，守+1，可格挡反击，可能损血时50%完全格挡。")

    def modify_stat(self, stat_name: str, value: float) -> float:
        return value + 1 if stat_name == "defense" else value

    def modify_targeting_range(self, value: int) -> int:
        return 1

    def grants_block_counter(self, battle: Battle, unit: Unit) -> bool:
        return self.owner is unit

    def on_final_damage(self, battle: Battle, ctx: DamageContext) -> None:
        if self.owner is ctx.target:
            ctx.complete_block_chance = max(ctx.complete_block_chance, 0.5)
            ctx.complete_block_label = "拔刀完全格挡"


class GiantShieldDrawBladeSkill(Skill):
    def ai_priority_score(self, context: AISkillScoreContext) -> float:
        battle, actor = context.battle, context.actor
        enemies = list(context.hostile_units())
        close = any(enemy.position is not None and battle.distance_between_units(actor, enemy) <= 2 for enemy in enemies)
        gun_ready = any(skill.code in {"shield_sweep", "shield_volley", "shield_flat_shot"}
                        and skill.can_use(battle, actor, {})[0] for skill in actor.skills)
        return (48.0 if close else -1000.0) - (22.0 if gun_ready else 0.0)

    def __init__(self) -> None:
        super().__init__("shield_draw_blade", "拔刀", "0.5魔；永久改为近战，范1、守+1、可格挡反击，50%完全格挡。",
                         mana_cost=0.5, target_mode="self")

    def can_use(self, battle: Battle, actor: Unit, payload: dict[str, Any] | None = None) -> tuple[bool, str]:
        ok, reason = super().can_use(battle, actor, payload)
        if ok and actor.has_status("巨盾拔刀"):
            return False, "已经拔刀。"
        return ok, reason

    def execute(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> None:
        if not actor.has_status("巨盾拔刀"):
            actor.add_status(GiantShieldBladeStatus(), source=actor)
            battle.log_public_event(f"{actor.name} 拔刀，转为近战守势。", source=actor)

    def preview(self, battle: Battle, actor: Unit) -> dict[str, Any]:
        return {"cells": positions_to_dict(battle.unit_cells(actor)), "target_unit_ids": [actor.unit_id],
                "secondary_cells": [], "requires_target": False}


class GiantShieldProtectionBreakStatus(StatModifierStatus):
    def __init__(self) -> None:
        super().__init__("火枪破保护降守", defense_delta=-1,
                         description="保护类技能最后一层护盾被火枪普攻打破后，守-1至最低1，持续四轮。",
                         duration=4, tick_scope="owner_turn_end")

    def modify_stat(self, stat_name: str, value: float) -> float:
        return max(1.0, value - 1) if stat_name == "defense" else value


class GiantShieldGunTrait(GiantShieldAllPierceTrait):
    def __init__(self) -> None:
        Trait.__init__(self, "巨盾火枪", "所有伤害破魔；本体守-1至最低1；普攻打破保护后降守四轮。")

    def modify_stat(self, stat_name: str, value: float) -> float:
        return max(1.0, value - 1) if stat_name == "defense" else value

    def on_after_damage(self, battle: Battle, ctx: DamageContext) -> None:
        if (self.owner is not ctx.source or ctx.is_skill or not ctx.protection_broken
                or not ctx.target.alive or ctx.target.skill_non_damage_effects_blocked()):
            return
        old = ctx.target.get_status("火枪破保护降守")
        if old is not None:
            ctx.target.remove_status(old, battle)
        ctx.target.add_status(GiantShieldProtectionBreakStatus(), source=self.owner)
        battle.log_public_event(f"{ctx.target.name} 的保护被火枪打破，守-1持续四轮。",
                                source=self.owner, target=ctx.target)


