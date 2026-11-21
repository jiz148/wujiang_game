"""Object-owned battlefield behavior for the auto chess synergy workbook."""
from __future__ import annotations

from typing import Any

from wujiang.tactical.engine.core import (
    Battle, BattleFieldEffect, DamageContext, HealContext, MoveContext,
    Position, Skill, Stats, StatusEffect, TargetContext, Trait, Unit,
)
from wujiang.tactical.heroes.base import AbstractHero
from wujiang.tactical.heroes.common import DashMoveSkill, PierceSkill
from wujiang.tactical.heroes.common import StatModifierStatus
from wujiang.tactical.prediction import direct_scoring
from wujiang.tactical.component_providers import empty_components_when_none, readonly_component_fields
from wujiang.tactical.stat_queries import stat_dependencies
from wujiang.tactical.query_purity import readonly_query_methods
from wujiang.tactical.settlement_reuse import independent_area_component, independent_area_field


SUPPORTED_TAGS = frozenset({
    *(('role', key) for key in ('刺客', '剑士', '勇者', '弓箭', '术士', '法师', '狂战', '贤者', '骑士')),
    *(('attribute', key) for key in ('光', '冰', '土', '暗', '木', '水', '火', '钢', '雷', '风')),
    *(('race', key) for key in ('人类', '兽人', '古龙', '天使', '恶魔', '机甲', '灵体', '石人', '精灵', '虫人', '鱼人', '鸟人')),
})


def _roll(battle: Battle, probability: float, label: str, side: int) -> bool:
    if direct_scoring() or getattr(battle, "_ai_probe_active", False):
        return probability >= .5
    return battle.roll_chance(probability, action_name=label, beneficiary_player_id=side)


def _chance(tier: int) -> float:
    return .5 if tier == 2 else .25


class SynergyDebuff(StatusEffect):
    """A named, nonstacking one-round attack rider."""

    _STAT = {"冰": "speed", "土": "attack", "暗": "defense", "木": "attack_range", "火": "mana"}

    def __init__(self, attribute: str, source_side: int) -> None:
        super().__init__(f"羁绊·{attribute}·受击", "普攻附加的一轮效果；同名效果不叠加。",
                         duration=1, tick_scope="owner_turn_end")
        self.attribute = attribute
        self.source_side = source_side
        self.applied_turn_number: int | None = None

    def on_owner_turn_end(self, battle: Battle) -> None:
        if self.applied_turn_number == battle.turn_number:
            return
        super().on_owner_turn_end(battle)

    def modify_stat(self, stat_name: str, value: float) -> float:
        if self._STAT.get(self.attribute) == stat_name:
            return min(value, max(1, value - 1))
        return value

    def modify_skill_mana_cost(self, battle: Battle, actor: Unit, skill: Skill,
                               payload: dict[str, Any] | None, cost: float) -> float:
        return cost + 1 if self.attribute == "水" and cost > 0 else cost

    def before_basic_attack_resolution(self, battle: Battle, actor: Unit,
                                       payload: dict[str, Any]) -> bool:
        if self.attribute not in ("光", "风"):
            return True
        probability = .5 if self.attribute == "光" else .25
        failed = _roll(battle, probability, f"{self.attribute}羁绊普攻失效", 3 - actor.player_id)
        if failed:
            payload["basic_attack_failed_reason"] = f"{self.attribute}羁绊"
        return not failed

    def on_before_move(self, battle: Battle, ctx: MoveContext) -> None:
        if (self.attribute == "雷" and ctx.unit is self.owner
                and _roll(battle, .25, "雷羁绊移动失效", self.source_side)):
            ctx.cancelled = True
            ctx.reason = "雷羁绊使移动失败。"

    def on_owner_action_declared(self, battle: Battle, action_type: str,
                                 payload: dict[str, Any]) -> None:
        if self.attribute != "钢" or action_type != "skill" or payload.get("synergy_failure_checked"):
            return
        if getattr(battle, "_ai_probe_active", False):
            return
        actor = self.owner
        if actor is None:
            return
        skill = actor.skill_map().get(str(payload.get("skill_code") or ""))
        if skill is None or skill.timing != "active":
            return
        payload["synergy_failure_checked"] = True
        payload["action_failed_by_synergy"] = _roll(battle, .25, "钢羁绊主动技能失效", self.source_side)


class SynergyPierceSkill(PierceSkill):
    ai_area_bonus_codes = PierceSkill.ai_area_bonus_codes | frozenset({"chess_synergy_pierce"})

    def __init__(self) -> None:
        super().__init__()
        self.code = "chess_synergy_pierce"
        self.name = "穿刺（剑士羁绊）"
        self.description = "剑士羁绊：每回合一次，不费魔使用穿刺。"
        self.mana_cost = 0
        self.max_uses_per_turn = 1


class SynergyLeapSkill(DashMoveSkill):
    def __init__(self) -> None:
        super().__init__("chess_synergy_leap", "飞跃（鱼人羁绊）",
                         "鱼人羁绊：本回合一次不费魔飞跃，直线飞行三格。",
                         max_distance=3, exact_distance=3, mana_cost=0,
                         max_uses_per_turn=1, straight_only=True, ignore_units=True)


class SynergyLeapGrant(StatusEffect):
    def __init__(self) -> None:
        super().__init__("鱼人羁绊·飞跃", "本回合可以不费魔使用一次飞跃。",
                         duration=1, tick_scope="owner_turn_end")
        self.skill = SynergyLeapSkill()

    def bind(self, owner: Unit) -> "SynergyLeapGrant":
        super().bind(owner)
        self.skill.bind(owner)
        return self

    def provided_components(self) -> tuple[Skill, ...]:
        return (self.skill,)


class SynergyTurnGuard(StatusEffect):
    def __init__(self) -> None:
        super().__init__("羁绊·临时守护", "本回合守+1。", duration=1, tick_scope="any_turn_end")

    def modify_stat(self, stat_name: str, value: float) -> float:
        return value + 1 if stat_name == "defense" else value


class LittleDemonArmorBreak(Trait):
    def __init__(self) -> None:
        super().__init__("小魔破守", "被此单位攻击到的单位守-1，到1。")

    def on_basic_attack_finished(self, battle: Battle, actor: Unit,
                                 payload: dict[str, Any], damage_contexts: list[DamageContext], missed: bool) -> None:
        if actor is not self.owner or missed:
            return
        for ctx in damage_contexts:
            target = ctx.target
            if ctx.cancelled or target is actor or not target.alive or target.stat("defense") <= 1:
                continue
            target.add_status(StatModifierStatus("小魔破守", defense_delta=-1,
                                                  description="被小魔攻击，守-1，到1。"), source=actor)


class SynergyLittleDemon(AbstractHero):
    hero_code = "chess_little_demon"
    hero_name = "小魔"
    role = "召唤物"
    attribute = "暗"
    race = "恶魔"
    base_stats = Stats(attack=3, defense=3, speed=3, attack_range=3, mana=0)
    raw_trait_text = "被此单位攻击到的单位守-1，到1。"

    def __init__(self, player_id: int) -> None:
        super().__init__(player_id, is_summon=True)

    def build_skills(self) -> list[Skill]:
        return []

    def build_traits(self) -> list[Trait]:
        return [LittleDemonArmorBreak()]


class SynergyTurretLine(Trait):
    def __init__(self) -> None:
        super().__init__("机甲炮塔瞄准", "可普攻选中范内任意格，不要求对准。")

    def ignores_direct_unit_target_line(self, battle: Battle, actor: Unit) -> bool:
        return actor is self.owner


class SynergyMechaTurret(AbstractHero):
    hero_code = "chess_mecha_turret"
    hero_name = "机甲炮塔"
    role = "召唤物"
    attribute = "钢"
    race = "机甲"
    base_stats = Stats(attack=3, defense=4, speed=0, attack_range=99, mana=0)
    raw_trait_text = "普攻可选中范内任意格。"

    def __init__(self, player_id: int) -> None:
        super().__init__(player_id, is_summon=True)

    def build_skills(self) -> list[Skill]:
        return []

    def build_traits(self) -> list[Trait]:
        return [SynergyTurretLine()]


@independent_area_component
@readonly_query_methods('modify_attack_actions_per_turn', 'modify_normal_move_actions_per_turn',
    'grants_block_counter', 'grants_counter')
class SynergyStatus(StatusEffect):
    """One active tag applied to every body in its stated effect scope."""

    GLOBAL = {("role", "勇者"), ("role", "贤者"), ("attribute", "光")}

    def ai_area_independent(self, source: Unit) -> bool:
        # Stone can install a new stat modifier; use the complete probe there.
        return not (self.category == 'race' and self.key == '石人')

    def __init__(self, category: str, key: str, tier: int, sponsor_side: int = 0) -> None:
        from wujiang.tactical.rooms.synergy import CATEGORY_LABELS, synergy_description
        super().__init__(f"自走棋羁绊·{CATEGORY_LABELS[category]}·{key}",
                         synergy_description(category, key, tier))
        self.category, self.key, self.tier, self.sponsor_side = category, key, tier, sponsor_side
        self.pierce = SynergyPierceSkill() if (category, key) == ("role", "剑士") else None
        self.extra_attacks = 0
        self.extra_moves = 0

    def bind(self, owner: Unit) -> "SynergyStatus":
        super().bind(owner)
        if not self.sponsor_side:
            self.sponsor_side = owner.player_id
        if self.pierce is not None:
            self.pierce.bind(owner)
        return self

    @empty_components_when_none('pierce')
    @readonly_component_fields('pierce', optional=True)
    def provided_components(self) -> tuple[Skill, ...]:
        return (self.pierce,) if self.pierce is not None and self.owner is not None and not (self.owner.is_summon or self.owner.is_clone) else ()

    @stat_dependencies('category', 'key', 'tier', 'owner')
    def modify_stat(self, stat_name: str, value: float) -> float:
        owner = self.owner
        if self.category == "role" and self.key == "刺客" and stat_name == "speed":
            return value + 1
        if (self.category, self.key) == ("role", "弓箭") and stat_name == "attack_range" and value > 1:
            return value + 1
        if (self.category, self.key) == ("role", "骑士") and self.tier == 2 and stat_name == "defense":
            return value + 1
        return value

    def modify_normal_move_actions_per_turn(self, value: int) -> int:
        return value + self.extra_moves + (self.tier == 2 and self.category == "role" and self.key == "剑士")

    def modify_attack_actions_per_turn(self, value: int) -> int:
        return value + self.extra_attacks

    def grants_block_counter(self, battle: Battle, unit: Unit) -> bool:
        return (self.category == "role" and self.key == "勇者" and self.tier == 2
                and unit is self.owner and not (unit.is_summon or unit.is_clone))

    def grants_counter(self, battle: Battle, unit: Unit) -> bool:
        return self.grants_block_counter(battle, unit)

    def on_targeted(self, battle: Battle, ctx: TargetContext) -> None:
        if self.owner is ctx.actor and not ctx.is_skill and (self.category, self.key) == ("role", "刺客") and self.tier == 2:
            ctx.half_ignore_shield = True

    def on_before_damage(self, battle: Battle, ctx: DamageContext) -> None:
        if self.owner is ctx.source and not ctx.is_skill:
            if (self.category, self.key) == ("role", "刺客") and self.tier == 2:
                ctx.half_ignore_shield = True

    def on_before_hp_loss(self, battle: Battle, ctx: DamageContext) -> None:
        if ctx.target is not self.owner or ctx.raw_damage is None:
            return
        if (self.category, self.key) == ("role", "勇者") and ctx.is_skill:
            if direct_scoring():
                ctx.raw_damage = (ctx.raw_damage + max(0, ctx.raw_damage - 1)) / 2
            elif _roll(battle, .5, "勇者羁绊减伤", ctx.target.player_id):
                ctx.raw_damage = max(0, ctx.raw_damage - 1)
        elif (self.category, self.key) == ("role", "狂战") and ctx.source is not None:
            if ctx.source.position is not None and ctx.target.position is not None and ctx.source.position.distance_to(ctx.target.position) > 1:
                if direct_scoring():
                    ctx.raw_damage = (ctx.raw_damage + max(0, ctx.raw_damage - 1)) / 2
                elif _roll(battle, .5, "狂战羁绊远程减伤", self.sponsor_side):
                    ctx.raw_damage = max(0, ctx.raw_damage - 1)
        elif ((self.category, self.key) == ("role", "贤者")
              and not (self.owner.is_summon or self.owner.is_clone)
              and self.owner.current_hp < self.owner.max_health):
            ctx.raw_damage = min(ctx.raw_damage, max(1, ctx.raw_damage - 1))
        elif ((self.category, self.key) == ("role", "骑士")
              and self.owner.player_id == self.sponsor_side and not ctx.is_skill):
            if direct_scoring():
                ctx.raw_damage = (ctx.raw_damage + max(0, ctx.raw_damage - 1)) / 2
            elif _roll(battle, .5, "骑士羁绊普攻减伤", self.sponsor_side):
                ctx.raw_damage = max(0, ctx.raw_damage - 1)

    def on_final_hp_loss(self, battle: Battle, ctx: DamageContext) -> None:
        if ctx.source is not self.owner or ctx.raw_damage is None:
            return
        marker = f"synergy_outgoing_{self.sponsor_side}_{self.category}_{self.key}"
        if marker in ctx.tags:
            return
        ctx.tags.add(marker)
        if (self.category, self.key) == ("role", "弓箭") and self.tier == 2 and self.owner.stat("attack_range") > 1:
            if direct_scoring():
                ctx.raw_damage += .5
            elif _roll(battle, .5, "弓箭羁绊伤害增加", self.sponsor_side):
                ctx.raw_damage += 1
        elif (self.category, self.key) == ("role", "法师") and ctx.is_skill:
            if direct_scoring():
                ctx.raw_damage += .5
            elif _roll(battle, .5, "法师羁绊伤害增加", self.sponsor_side):
                ctx.raw_damage += 1

    def on_before_heal(self, battle: Battle, ctx: HealContext) -> None:
        marker = f"synergy_sage_heal_{self.sponsor_side}"
        if ((self.category, self.key) == ("role", "贤者") and self.tier == 2
                and ctx.target is self.owner and self.owner.player_id == self.sponsor_side
                and marker not in ctx.tags):
            ctx.amount *= 2
            ctx.tags.add(marker)

    def modify_direct_heal_amount(self, amount: float) -> float:
        if ((self.category, self.key) == ("role", "贤者") and self.tier == 2
                and self.owner is not None and self.owner.player_id == self.sponsor_side):
            return amount * 2
        return amount

    def modify_committed_skill_mana_cost(self, battle: Battle, actor: Unit,
                                         skill: Skill, cost: float) -> float:
        if ((self.category, self.key) == ("race", "人类") and actor is self.owner
                and cost > 0 and _roll(battle, _chance(self.tier), "人类羁绊免魔", self.sponsor_side)):
            return 0.0
        return cost

    def on_owner_turn_start(self, battle: Battle) -> None:
        owner = self.owner
        if owner is None:
            return
        self.extra_attacks = self.extra_moves = 0
        if (self.category, self.key) == ("role", "法师") and self.tier == 2:
            owner.gain_mana(1)
        elif (self.category, self.key) == ("role", "狂战") and self.tier == 2:
            owner.heal_fraction(.25)
        elif self.category == "race":
            if self.key == "天使" and _roll(battle, _chance(self.tier), "天使羁绊回复", self.sponsor_side):
                owner.heal_fraction(.25)
                owner.gain_mana(1)
            elif self.key == "恶魔" and _roll(battle, _chance(self.tier), "恶魔羁绊行动次数", self.sponsor_side):
                self.extra_attacks += 1
                self.extra_moves += 1
            elif self.key == "鱼人" and _roll(battle, _chance(self.tier), "鱼人羁绊飞跃", self.sponsor_side):
                if not any(isinstance(status, SynergyLeapGrant) for status in owner.statuses):
                    owner.add_status(SynergyLeapGrant())

    def on_basic_attack_finished(self, battle: Battle, actor: Unit,
                                 payload: dict[str, Any], damage_contexts: list[DamageContext], missed: bool) -> None:
        if actor is not self.owner or self.category != "race" or missed:
            return
        if self.key == "兽人" and _roll(battle, _chance(self.tier), "兽人羁绊额外攻击", self.sponsor_side):
            self.extra_attacks += 1
        elif self.key == "古龙" and _roll(battle, _chance(self.tier), "古龙羁绊回魔", self.sponsor_side):
            actor.gain_mana(1)
        elif self.key == "精灵" and _roll(battle, _chance(self.tier), "精灵羁绊额外移动", self.sponsor_side):
            self.extra_moves += 1

    def on_own_skill_prepaid(self, battle: Battle, actor: Unit, skill: Skill,
                             *, used_extra_turn_use: bool) -> None:
        if self.category != "race" or actor is not self.owner:
            return
        if self.key == "灵体" and skill.timing == "active" and _roll(battle, _chance(self.tier), "灵体羁绊额外攻击", self.sponsor_side):
            self.extra_attacks += 1
        elif self.key == "虫人" and skill.timing != "active" and _roll(battle, _chance(self.tier), "虫人羁绊守护", self.sponsor_side):
            actor.add_status(SynergyTurnGuard())

    def on_unit_moved(self, battle: Battle, ctx: MoveContext) -> None:
        if (self.category, self.key) == ("race", "鸟人") and ctx.unit is self.owner:
            if _roll(battle, _chance(self.tier), "鸟人羁绊行动次数", self.sponsor_side):
                self.extra_attacks += 1
                self.extra_moves += 1

    def on_after_damage(self, battle: Battle, ctx: DamageContext) -> None:
        stone_marker = f"synergy_stone_{self.sponsor_side}"
        if (self.owner is ctx.target and ctx.actual_damage > 0 and (self.category, self.key) == ("race", "石人")
                and stone_marker not in ctx.tags):
            ctx.tags.add(stone_marker)
            if _roll(battle, _chance(self.tier), "石人羁绊守护", self.sponsor_side):
                self.owner.add_status(SynergyTurnGuard())
        if (self.owner is not ctx.source or ctx.is_skill or ctx.cancelled
                or self.category != "attribute" or ctx.target is None):
            return
        self._apply_attack_rider(battle, ctx)

    def on_damage_cancelled(self, battle: Battle, ctx: DamageContext) -> None:
        if (self.owner is ctx.source and self.category == "attribute" and self.tier == 2
                and not ctx.is_skill and ctx.shield_consumed and "shield_blocked_damage" in ctx.modifier_receipts):
            self._apply_attack_rider(battle, ctx)

    def _apply_attack_rider(self, battle: Battle, ctx: DamageContext) -> None:
        marker = f"synergy_attack_rider_{self.sponsor_side}_{self.key}"
        if marker in ctx.tags or ctx.target is None or not ctx.target.alive:
            return
        ctx.tags.add(marker)
        existing = next((status for status in ctx.target.statuses
                         if isinstance(status, SynergyDebuff) and status.attribute == self.key), None)
        if existing is not None:
            existing.duration = 1
            existing.applied_turn_number = battle.turn_number
        else:
            status = SynergyDebuff(self.key, self.sponsor_side)
            status.applied_turn_number = battle.turn_number
            ctx.target.add_status(status, source=self.owner)
        if self.key == "火":
            ctx.target.clamp_mana()

@independent_area_field
class SynergyField(BattleFieldEffect):
    """Installs side-wide buffs on entrants and creates battle-start summons."""

    def ai_area_independent(self) -> bool:
        # Its destruction callback is outside the certified nonlethal path.
        return True

    def __init__(self, by_side: dict[int, list[dict[str, Any]]]) -> None:
        super().__init__("自走棋羁绊", "双方已激活的羁绊效果。")
        self.by_side = by_side

    def on_unit_entered(self, battle: Battle, unit: Unit) -> None:
        for side, entries in self.by_side.items():
            for entry in entries:
                category, key = entry["category"], entry["name"]
                global_effect = (category, key) in SynergyStatus.GLOBAL or (category, key) == ("role", "骑士") and entry["tier"] == 2
                if side != unit.player_id and not global_effect:
                    continue
                if any(isinstance(status, SynergyStatus) and status.sponsor_side == side
                       and (status.category, status.key) == (category, key) for status in unit.statuses):
                    continue
                status = SynergyStatus(category, key, entry["tier"], side)
                status.is_skill_effect = False
                unit.statuses.append(status.bind(unit))

    def apply_initial(self, battle: Battle) -> None:
        for unit in list(battle.all_units()):
            self.on_unit_entered(battle, unit)
        rng = getattr(battle, "chess_rng", None)
        for side, entries in self.by_side.items():
            for entry in entries:
                category, key, tier = entry["category"], entry["name"], entry["tier"]
                if (category, key) not in {("role", "术士"), ("race", "机甲")}:
                    continue
                anchors = [unit for unit in battle.all_units()
                           if unit.player_id == side and not unit.is_summon and not unit.is_clone
                           and unit.alive and unit.position is not None
                           and (unit.role if category == "role" else unit.race) == key]
                if not anchors:
                    continue
                anchor = rng.choice(anchors) if rng is not None else anchors[0]
                summon_type = SynergyLittleDemon if category == "role" else SynergyMechaTurret
                count = 2 if category == "role" else 3 if tier == 2 else 1
                cells = [Position(x, y) for x in range(battle.width) for y in range(battle.height)
                         if anchor.position is not None and max(abs(x - anchor.position.x), abs(y - anchor.position.y)) == 1]
                if rng is not None:
                    rng.shuffle(cells)
                for _ in range(count):
                    summon = summon_type(side)
                    landing = next((cell for cell in cells if battle.can_place_unit(summon, cell)), None)
                    if landing is None:
                        break
                    battle.summon_unit(summon, landing, summoner=anchor)
                    cells.remove(landing)

    def on_unit_destruction_finalized(self, battle: Battle, destroyed_unit: Unit,
                                      source_unit_id: str | None) -> None:
        if (not destroyed_unit.is_summon or destroyed_unit.player_id not in self.by_side
                or not any(item["category"] == "role" and item["name"] == "术士" and item["tier"] == 2
                           for item in self.by_side[destroyed_unit.player_id])):
            return
        center = getattr(destroyed_unit, "last_position", None)
        if center is None:
            return
        for target in list(battle.all_units()):
            if (target.player_id == destroyed_unit.player_id or not target.alive
                    or target.banished or target.position is None):
                continue
            if any(abs(cell.x - center.x) <= 1 and abs(cell.y - center.y) <= 1
                   for cell in battle.unit_cells(target)):
                battle.resolve_damage(DamageContext(source=destroyed_unit, target=target, attack_power=0,
                                                    raw_damage=3, is_skill=True, from_field_effect=True,
                                                    action_name="术士羁绊·召唤物爆炸"))

