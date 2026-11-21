"""R068 狼王: wolf summon, lingering bite and two public coins."""

from __future__ import annotations

from typing import Any

from wujiang.tactical.engine.core import (
    ActionError, Battle, DamageContext, Position, Skill, Stats, StatusEffect, Trait, Unit,
)
from wujiang.tactical.heroes.base import AbstractHero
from wujiang.tactical.heroes.ai_policy import HeroAIPolicy
from wujiang.tactical.heroes.common import payload_position, positions_to_dict
from wujiang.tactical.heroes.mechanics.foundation import PermanentMagicImmunityTrait
from wujiang.tactical.prediction import direct_scoring


def _present(unit: Unit | None) -> bool:
    return unit is not None and unit.alive and not unit.banished and unit.position is not None


class WolfBiteMark(StatusEffect):
    def __init__(self, wolf_id: str, player_id: int) -> None:
        super().__init__("召狼咬伤", "每个召唤方队伍回合结束时血-1/8。")
        self.wolf_id = wolf_id
        self.player_id = player_id

    def on_any_turn_end(self, battle: Battle, ended_player_id: int) -> None:
        target = self.owner
        if ended_player_id != self.player_id or not _present(target):
            return
        wolf = battle.units.get(self.wolf_id)
        battle.resolve_damage(DamageContext(
            source=wolf if _present(wolf) else None, target=target,
            attack_power=0, raw_damage=0.125, is_skill=False,
            from_field_effect=True, action_name="召狼咬伤", tags={"periodic", "wolf_bite"},
        ))

    def to_public_dict(self, battle: Battle) -> dict[str, Any]:
        result = super().to_public_dict(battle)
        result["wolf_unit_id"] = self.wolf_id
        return result


class WolfBiteTrait(Trait):
    def __init__(self) -> None:
        super().__init__("狼咬", "普攻造成实际生命伤后留下逐己方队伍轮失血标记。")

    def on_after_damage(self, battle: Battle, ctx: DamageContext) -> None:
        wolf = self.owner
        if (ctx.source is not wolf or ctx.is_skill or "attack" not in ctx.tags
                or ctx.actual_damage <= 0 or not _present(ctx.target)
                or ctx.target.player_id == wolf.player_id):
            return
        for status in list(ctx.target.statuses):
            if isinstance(status, WolfBiteMark) and status.wolf_id == wolf.unit_id:
                ctx.target.remove_status(status, battle)
        ctx.target.add_status(WolfBiteMark(wolf.unit_id, wolf.player_id), source=wolf)
        battle.log_public_event(f"{ctx.target.name} 被 {wolf.name} 咬伤。", source=wolf, target=ctx.target)


class MountedWolfCoinTrait(Trait):
    def __init__(self) -> None:
        super().__init__("骑狼防伤硬币", "狼王乘骑时，狼每次受正数生命伤有1/2概率无效。")

    def on_before_hp_loss(self, battle: Battle, ctx: DamageContext) -> None:
        wolf = self.owner
        if (ctx.target is not wolf or ctx.raw_damage is None or ctx.raw_damage <= 0
                or wolf is None or not wolf.mount_owner_id):
            return
        rider = battle.rider_for(wolf)
        if (rider is None or rider.unit_id != wolf.mount_owner_id
                or not any(isinstance(trait, WolfRiderGuardTrait) for trait in rider.traits)):
            return
        ctx.complete_block_chance = 1.0 - (1.0 - ctx.complete_block_chance) * 0.5
        ctx.complete_block_label = "骑狼防伤硬币"


class WolfRiderGuardTrait(Trait):
    def __init__(self) -> None:
        super().__init__("狼王骑狼防伤", "本人骑乘同源召狼时，该狼有1/2概率使本次生命伤无效。")


class SummonedWolf(AbstractHero):
    hero_code = "summoned_wolf_r068"
    hero_name = "召狼"
    role = "坐骑"
    attribute = "钢"
    race = "兽"
    level = 1
    base_stats = Stats(attack=3, defense=3, speed=5, attack_range=1, mana=0)
    raw_skill_text = ""
    raw_trait_text = "魔免；狼咬；可被狼王乘骑"

    def __init__(self, player_id: int) -> None:
        super().__init__(player_id, is_summon=True)

    def build_skills(self) -> list[Skill]:
        return []

    def build_traits(self) -> list[Trait]:
        return [PermanentMagicImmunityTrait(), WolfBiteTrait(), MountedWolfCoinTrait()]


class SummonWolfSkill(Skill):
    ai_payload_value_owned = True
    ai_enemy_impact_requirement = False

    def __init__(self) -> None:
        super().__init__("summon_wolf", "召狼", "每本人轮一次；在外邻合法格召攻3守3速5范1、魔免的战狼。",
                         max_uses_per_turn=1, target_mode="cell")

    def destinations(self, battle: Battle, actor: Unit) -> list[Position]:
        if not _present(actor):
            return []
        body = set(battle.unit_cells(actor))
        wolf = SummonedWolf(actor.player_id)
        return sorted({cell for origin in body for cell in battle.neighbors(origin)
                       if cell not in body and battle.can_place_unit(wolf, cell, mover=wolf)},
                      key=lambda cell: (cell.y, cell.x))

    def can_use(self, battle: Battle, actor: Unit,
                payload: dict[str, Any] | None = None) -> tuple[bool, str]:
        ok, reason = super().can_use(battle, actor, payload)
        if not ok:
            return ok, reason
        cells = self.destinations(battle, actor)
        if not cells:
            return False, "周围没有召狼的合法空格。"
        if payload is not None and "x" in payload:
            try:
                if payload_position(payload) not in cells:
                    return False, "召狼落格不合法。"
            except (ActionError, TypeError, ValueError):
                return False, "召狼落格不合法。"
        return True, ""

    def preview(self, battle: Battle, actor: Unit) -> dict[str, Any]:
        return {"cells": positions_to_dict(self.destinations(battle, actor)),
                "target_unit_ids": [], "secondary_cells": [], "requires_target": True}

    def get_target_cells_for_payload(self, battle: Battle, actor: Unit,
                                     payload: dict[str, Any]) -> list[Position]:
        cell = payload_position(payload)
        if cell not in self.destinations(battle, actor):
            raise ActionError("召狼落格不合法。")
        return [cell]

    def ai_payload_candidates(self, battle: Battle, actor: Unit,
                              action: dict[str, Any]) -> list[dict[str, Any]]:
        return [{"type": "skill", "unit_id": actor.unit_id, "skill_code": self.code,
                 "x": cell.x, "y": cell.y} for cell in self.destinations(battle, actor)]

    def ai_candidate_payloads(self, context: Any) -> list[dict[str, Any]]:
        return self.ai_payload_candidates(context.battle, context.actor, context.action)

    def ai_payload_value(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> float:
        try:
            cell = self.get_target_cells_for_payload(battle, actor, payload)[0]
        except (ActionError, TypeError, ValueError):
            return -1000.0
        enemies = [unit for unit in battle.enemy_units(actor.player_id) if _present(unit)]
        reach = min((cell.distance_to(occupied) for unit in enemies
                     for occupied in battle.unit_cells(unit)), default=9)
        return 42.0 + max(0, 5 - reach) * 5.0

    def ai_priority_score(self, context: Any) -> float:
        return self.ai_payload_value(context.battle, context.actor, context.payload)

    def execute(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> None:
        try:
            cell = self.get_target_cells_for_payload(battle, actor, payload)[0]
        except ActionError:
            battle.log_public_event(f"{actor.name} 的召狼落格已失效。", source=actor)
            return
        wolf = SummonedWolf(actor.player_id)
        wolf.mount_owner_id = actor.unit_id
        wolf.is_mount = True
        try:
            battle.summon_unit(wolf, cell, summoner=actor)
        except ActionError:
            battle.log_public_event(f"{actor.name} 的召狼落格已被占用。", source=actor)
            return
        battle.log_public_event(f"{actor.name} 在 ({cell.x},{cell.y}) 召狼。", source=actor, target=wolf)


class WolfKingAttackCoin(Trait):
    def __init__(self) -> None:
        super().__init__("狼王攻击硬币", "本人普攻最终生命伤有1/2概率+1。")

    def on_final_hp_loss(self, battle: Battle, ctx: DamageContext) -> None:
        if (ctx.source is not self.owner or ctx.is_skill or "attack" not in ctx.tags
                or ctx.raw_damage is None or ctx.raw_damage <= 0):
            return
        if direct_scoring():
            rows = ctx.forecast_damage_rows or ((ctx.raw_damage, 1.0),)
            ctx.forecast_damage_rows = tuple(
                outcome for amount, weight in rows
                for outcome in ((amount, weight * 0.5), (amount + 1.0, weight * 0.5))
            )
            ctx.raw_damage += 0.5
            return
        if getattr(battle, "_ai_probe_active", False):
            ctx.raw_damage += 0.5
            return
        bonus = battle.roll_chance(0.5, action_name="狼王攻击加伤硬币",
                                   beneficiary_player_id=self.owner.player_id)
        if bonus:
            ctx.raw_damage += 1.0
        battle.log_public_event(f"{self.owner.name} 攻击加伤硬币：{'加1' if bonus else '不加'}。",
                                source=self.owner, target=ctx.target)


class WolfKingAIPolicy(HeroAIPolicy):
    def normal_move_candidate_score(self, battle: Battle, actor: Unit,
                                    destination: Position, profile: Any,
                                    score: float, shared: Any) -> float:
        if battle.mounted_unit_for(actor) is not None:
            return score
        if any(isinstance(unit, SummonedWolf) and unit.mount_owner_id == actor.unit_id
               and _present(unit) and unit.position == destination
               for unit in battle.player_units(actor.player_id)):
            return score + 22.0
        return score
