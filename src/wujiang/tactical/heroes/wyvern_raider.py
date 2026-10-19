from __future__ import annotations

from dataclasses import replace
from typing import Any

from wujiang.tactical.engine.core import (
    ActionError, Battle, DamageContext, HeroUnit, MoveContext, Position, QueuedAction,
    Skill, Stats, StatusEffect, Trait, Unit,
)
from wujiang.tactical.heroes.base import AbstractHero
from wujiang.tactical.heroes.common import FlyingTrait, StatModifierStatus
from wujiang.tactical.heroes.next_five import AttackManaDrainTrait, PassThroughMovementTrait, apply_piercing_status_effect


class WyvernSlowStatus(StatModifierStatus):
    def __init__(self, created_turn: int, layers: int = 1) -> None:
        super().__init__("翼龙穿人减速", speed_delta=-layers,
                         description="破魔；速-1最低1，至下一个本单位所属武将回合结束。")
        self.created_turn = created_turn

    def modify_stat(self, stat_name: str, value: float) -> float:
        updated = super().modify_stat(stat_name, value)
        return max(1.0, updated) if stat_name == "speed" else updated

    def on_owner_turn_end(self, battle: Battle) -> None:
        if self.owner is not None and battle.turn_number > self.created_turn:
            self.owner.remove_status(self, battle)


def resolve_wyvern_crossing(battle: Battle, mount: Unit, queued: QueuedAction) -> None:
    for target in battle.effect_units_at_cells(queued.target_cells, ignore=mount):
        if target.unit_id == mount.unit_id:
            continue
        old = target.get_status("翼龙穿人减速")
        layers = max(1, int(-getattr(old, "speed_delta", 0))) + 1 if old is not None else 1
        apply_piercing_status_effect(
            battle, mount, target, action_name="翼龙穿人", status=WyvernSlowStatus(battle.turn_number, layers),
            is_skill=False, tags={"wyvern_crossing", "pass_through"},
        )


class WyvernPassThroughTrait(PassThroughMovementTrait):
    def __init__(self) -> None:
        super().__init__()
        self.name = "翼龙穿人"
        self.description = "穿过单位时破魔令其速-1最低1，至其下个回合结束。"

    def on_unit_moved(self, battle: Battle, ctx: MoveContext) -> None:
        mount = self.owner
        if mount is None or ctx.unit is not mount or len(ctx.path) < 2:
            return
        crossings = battle.path_crossing_events(mount, ctx.path)
        for index, (target, cell) in enumerate(crossings):
            battle.queue_skill_effect_action(
                actor=mount, display_name="翼龙穿人", effect_code="wyvern_crossing",
                payload={"cells": [cell.to_dict()], "refresh_cell_targets": True},
                target_cells=[cell], target_units=[target], effect_resolver=resolve_wyvern_crossing,
                description="位移已完成；对经过原格上的实际单位施加破魔速-1，结算前可连锁。",
                segment_index=index, segment_count=len(crossings),
            )
        rider = battle.rider_for(mount)
        if rider is None:
            return
        combat = next((trait for trait in rider.traits if isinstance(trait, WyvernRiderCombatTrait)), None)
        if combat is not None:
            rider_start = rider.position.offset(ctx.start.x - ctx.end.x, ctx.start.y - ctx.end.y)
            rider_path = [rider_start.offset(step.x - ctx.start.x, step.y - ctx.start.y) for step in ctx.path]
            combat.enqueue_movement_attacks(battle, rider_path)


class WyvernRideableTrait(Trait):
    def __init__(self) -> None:
        super().__init__("可乘骑", "骑乘时由翼龙承受伤害与技能效果，骑手可替其连锁。")

    def on_owner_removed(self, battle: Battle) -> None:
        mount = self.owner
        if mount is None or mount.alive or not mount.mount_owner_id or not getattr(mount, "destruction_count", 0):
            return
        rider = battle.units.get(mount.mount_owner_id)
        if rider is None or not rider.alive:
            return
        old = rider.get_status("翼龙重召冷却")
        if old is not None:
            rider.remove_status(old, battle)
        rider.add_status(WyvernCooldownStatus(2 if battle.active_player == rider.player_id else 1))


class WyvernMount(AbstractHero):
    hero_code = "raider_wyvern"
    hero_name = "翼龙"
    role = "坐骑"
    attribute = "风"
    race = "兽"
    level = 1
    base_stats = Stats(attack=1, defense=4, speed=8, attack_range=1, mana=0)
    footprint_width = 1
    footprint_height = 2
    raw_skill_text = ""
    raw_trait_text = "飞行；可穿人；穿人破魔速-1；可乘骑"

    def __init__(self, player_id: int) -> None:
        super().__init__(player_id, is_summon=True)

    def build_skills(self) -> list[Skill]:
        return []

    def build_traits(self) -> list[Trait]:
        return [FlyingTrait(), WyvernPassThroughTrait(), WyvernRideableTrait()]


def own_wyvern(battle: Battle, rider: Unit) -> Unit | None:
    return next((unit for unit in battle.all_units() if unit.hero_code == "raider_wyvern"
                 and unit.mount_owner_id == rider.unit_id and unit.alive and not unit.banished
                 and unit.position is not None), None)


class WyvernCooldownStatus(StatusEffect):
    def __init__(self, duration: int) -> None:
        super().__init__("翼龙重召冷却", "翼龙被破坏后隔一本人轮才能重召。",
                         duration=duration, tick_scope="owner_turn_end")


class WyvernMountedStartTrait(Trait):
    def __init__(self) -> None:
        super().__init__("翼龙开局乘骑", "开局已召出并乘骑第一只翼龙。")

    def on_enter_battle(self, battle: Battle) -> None:
        rider = self.owner
        if rider is None or rider.position is None or own_wyvern(battle, rider) is not None:
            return
        mount = WyvernMount(rider.player_id)
        mount.summoner_id = rider.unit_id
        mount.mount_owner_id = rider.unit_id
        mount.is_mount = True
        mount.can_act_on_entry_turn = True
        mount.turn_ready = True
        battle.add_unit(mount, rider.position)
        battle.set_mounted_state(rider, mount)


class WyvernSkill(Skill):
    def __init__(self) -> None:
        super().__init__("wyvern", "翼龙", "重召并骑上自己的翼龙；真毁后隔一本人轮才可重召。", target_mode="self")

    def can_use(self, battle: Battle, actor: Unit,
                payload: dict[str, Any] | None = None) -> tuple[bool, str]:
        ok, reason = super().can_use(battle, actor, payload)
        if not ok:
            return ok, reason
        if actor.position is None or own_wyvern(battle, actor) is not None:
            return False, "当前已有翼龙或不在战场。"
        if actor.has_status("翼龙重召冷却"):
            return False, "翼龙仍在重召冷却。"
        probe = WyvernMount(actor.player_id)
        probe.mount_owner_id = actor.unit_id
        probe.is_mount = True
        return (True, "") if battle.can_place_unit(probe, actor.position, mover=probe) else (False, "当前位置无法放置翼龙。")

    def execute(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> None:
        if actor.position is None or own_wyvern(battle, actor) is not None:
            raise ActionError("翼龙重召位置已失效。")
        mount = WyvernMount(actor.player_id)
        mount.mount_owner_id = actor.unit_id
        mount.is_mount = True
        battle.summon_unit(mount, actor.position, summoner=actor)
        battle.set_mounted_state(actor, mount)

    def preview(self, battle: Battle, actor: Unit) -> dict[str, Any]:
        return {"cells": [actor.position.to_dict()] if actor.position else [], "target_unit_ids": [actor.unit_id],
                "secondary_cells": [], "requires_target": False}


class NightSkyPartyStatus(StatusEffect):
    def __init__(self) -> None:
        super().__init__("夜空派对", "至本将下个回合结束前暂停‘每回合同一目标最多普攻一次’。")
        self.end_events = 0

    def on_owner_turn_end(self, battle: Battle) -> None:
        self.end_events += 1
        if self.end_events >= 2 and self.owner is not None:
            self.owner.remove_status(self, battle)


class NightSkyPartySkill(Skill):
    def __init__(self) -> None:
        super().__init__("night_sky_party", "夜空派对", "三本人轮一次，至下个本人轮结束前取消同目标每轮一次限制。",
                         cooldown_turns=3, target_mode="self")

    def execute(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> None:
        old = actor.get_status("夜空派对")
        if old is not None:
            actor.remove_status(old, battle)
        actor.add_status(NightSkyPartyStatus(), source=actor)
        battle.log_public_event(f"{actor.name} 发动夜空派对，可重复普攻同一目标至下个本人轮结束。", source=actor, target=actor)


def resolve_wyvern_rider_attack(battle: Battle, rider: Unit, queued: QueuedAction) -> None:
    cell = queued.target_cells[0] if queued.target_cells else None
    if cell is None:
        return
    target = next((unit for unit in battle.effect_units_at_cells([cell])
                   if unit.player_id != rider.player_id and unit.unit_id != rider.unit_id), None)
    if target is None:
        rider.notify_basic_attack_finished(battle, queued.payload, [], missed=True)
        battle.log_public_event(f"{rider.name} 的位移普攻落在空格。", source=rider)
        return
    queued.payload["target_unit_id"] = target.unit_id
    ctx = battle.resolve_from_declared_origin(
        rider, queued.payload,
        lambda: battle.resolve_attack_damage(rider, target, action_name="翼龙骑手位移普攻",
                                             payload={**queued.payload, "reaction_attack": True}),
    )
    rider.notify_basic_attack_finished(battle, queued.payload, [ctx] if ctx is not None else [], missed=ctx is None)


class WyvernRiderCombatTrait(Trait):
    def __init__(self) -> None:
        super().__init__("翼龙骑手追击", "位移时攻击经过和周围敌人；每轮同目标一次；每次普攻本轮攻+1。")
        self.attacked_ids: set[str] = set()
        self.attack_bonus = 0

    def modify_stat(self, stat_name: str, value: float) -> float:
        return value + self.attack_bonus if stat_name == "attack" else value

    def can_attack_target(self, battle: Battle, actor: Unit, target: Unit) -> tuple[bool, str]:
        if actor is self.owner and target.unit_id in self.attacked_ids and not actor.has_status("夜空派对"):
            return False, "本回合已对同一单位普攻。"
        return True, ""

    def on_basic_attack_finished(self, battle: Battle, actor: Unit, payload: dict[str, Any],
                                 damage_contexts: list[DamageContext], missed: bool) -> None:
        if actor is not self.owner:
            return
        target_id = str(payload.get("target_unit_id") or "")
        if target_id:
            self.attacked_ids.add(target_id)
        self.attack_bonus += 1
        battle.log_public_event(f"{actor.name} 普攻后本轮攻+1，当前累计+{self.attack_bonus}。", source=actor, target=actor)

    def on_any_turn_end(self, battle: Battle, ended_player_id: int) -> None:
        self.attacked_ids.clear()
        self.attack_bonus = 0

    def enqueue_movement_attacks(self, battle: Battle, path: list[Position]) -> None:
        rider = self.owner
        if rider is None or not rider.alive or rider.position is None or len(path) < 2:
            return
        seen_in_move: set[str] = set()
        party = rider.has_status("夜空派对")
        for step in path[1:]:
            for enemy in battle.enemy_units(rider.player_id):
                recipient = battle.effect_recipient(enemy)
                if (recipient.unit_id in seen_in_move or not recipient.alive or recipient.position is None
                        or recipient.banished or (not party and recipient.unit_id in self.attacked_ids)):
                    continue
                if min((cell.distance_to(step) for cell in battle.unit_cells(recipient)), default=99) > 1:
                    continue
                seen_in_move.add(recipient.unit_id)
                if not party:
                    self.attacked_ids.add(recipient.unit_id)
                cell = min(battle.unit_cells(recipient), key=lambda pos: pos.distance_to(step))
                queued = battle.build_skill_effect_action(
                    actor=rider, display_name="翼龙骑手位移普攻", effect_code="wyvern_rider_attack",
                    payload={"target_unit_id": recipient.unit_id, "declared_source_x": step.x,
                             "declared_source_y": step.y, "refresh_cell_targets": True,
                             "movement_followup_attack": True,
                             "attack_effect_summary": "位移完成后，对经过或周围的原格给予一次普攻；伤害前可连锁。"},
                    target_cells=[cell], target_units=[recipient], include_cell_units=False,
                    effect_resolver=resolve_wyvern_rider_attack, hostile=True,
                )
                battle.queue_followup_action(replace(queued, action_type="attack"))

    def on_unit_moved(self, battle: Battle, ctx: MoveContext) -> None:
        if self.owner is not None and ctx.unit is self.owner and len(ctx.path) > 1:
            self.enqueue_movement_attacks(battle, ctx.path)

    def to_public_dict(self, battle: Battle) -> dict[str, Any]:
        data = super().to_public_dict(battle)
        data.update({"attack_bonus_this_turn": self.attack_bonus,
                     "attacked_unit_ids_this_turn": sorted(self.attacked_ids)})
        return data
