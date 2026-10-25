"""Hell dog, snow, and hunter mechanics."""

from __future__ import annotations

from typing import Any
from wujiang.tactical.engine.core import ActionError
from wujiang.tactical.engine.core import Battle
from wujiang.tactical.engine.core import DamageContext
from wujiang.tactical.engine.core import Position
from wujiang.tactical.engine.core import Skill
from wujiang.tactical.engine.core import Stats
from wujiang.tactical.engine.core import Trait
from wujiang.tactical.engine.core import Unit
from wujiang.tactical.heroes.base import AbstractHero
from wujiang.tactical.heroes.common import payload_position
from wujiang.tactical.heroes.common import positions_to_dict
from wujiang.tactical.heroes.mechanics.foundation import PermanentMagicImmunityTrait
from wujiang.tactical.heroes.mechanics.hell_core import HellSnowManaStatus
from wujiang.tactical.heroes.mechanics.hell_rebirth import HellRebirthTrait
from wujiang.tactical.heroes.next_five import square_around_cells


class HellDogSummon(AbstractHero):
    hero_code = "hell_dog_summon"
    hero_name = "地狱犬"
    role = "召唤物"
    attribute = "暗"
    race = "恶魔"
    level = 1
    base_stats = Stats(attack=3, defense=3, speed=4, attack_range=1, mana=0)

    def __init__(self, player_id: int) -> None:
        super().__init__(player_id, is_summon=True)

    def build_skills(self) -> list[Skill]:
        return []

    def build_traits(self) -> list[Trait]:
        return [PermanentMagicImmunityTrait()]


class HellDogSkill(Skill):
    def __init__(self) -> None:
        super().__init__("hell_dog", "地狱犬", "花1魔在本体周围合法空格召唤攻3守3速4、魔免的地狱犬。",
                         mana_cost=1, target_mode="cell")

    def available_cells(self, battle: Battle, actor: Unit) -> list[Position]:
        probe = HellDogSummon(actor.player_id)
        return [cell for cell in square_around_cells(battle, battle.unit_cells(actor), radius=1)
                if battle.can_place_unit(probe, cell)]

    def can_use(self, battle: Battle, actor: Unit, payload: dict[str, Any] | None = None) -> tuple[bool, str]:
        ok, reason = super().can_use(battle, actor, payload)
        if not ok:
            return ok, reason
        options = self.available_cells(battle, actor)
        if not options:
            return False, "本体周围没有合法的地狱犬落点。"
        if payload and any(key in payload for key in ("x", "y", "cells")):
            try:
                if payload_position(payload) not in options:
                    return False, "地狱犬只能召唤到本体周围的完整合法空格。"
            except (ActionError, ValueError, TypeError, KeyError):
                return False, "地狱犬需要选择一个合法落点。"
        return True, ""

    def prepay_resources(self, battle: Battle, actor: Unit, payload: dict[str, Any] | None = None) -> None:
        if payload_position(payload or {}) not in self.available_cells(battle, actor):
            raise ActionError("地狱犬落点已失效。")
        super().prepay_resources(battle, actor, payload)

    def execute(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> None:
        destination = payload_position(payload)
        if destination not in self.available_cells(battle, actor):
            raise ActionError("地狱犬只能召唤到本体周围的完整合法空格。")
        dog = HellDogSummon(actor.player_id)
        battle.summon_unit(dog, destination, summoner=actor)
        battle.log_public_event(f"{actor.name} 在 ({destination.x}, {destination.y}) 召唤地狱犬。",
                                source=actor, target=dog)

    def preview(self, battle: Battle, actor: Unit) -> dict[str, Any]:
        return {"cells": positions_to_dict(self.available_cells(battle, actor)),
                "target_unit_ids": [], "secondary_cells": [], "requires_target": True}

    def get_target_cells_for_payload(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> list[Position]:
        return [payload_position(payload)]


class HellTeleportSkill(Skill):
    def __init__(self) -> None:
        super().__init__("hell_teleport", "瞬移", "大招：每场一次，瞬移到全场任意完整合法空格；不造成伤害。",
                         max_uses_per_battle=1, target_mode="cell")

    def available_cells(self, battle: Battle, actor: Unit) -> list[Position]:
        if actor.position is None or actor.cannot_move or getattr(actor, "standable_terrain", False):
            return []
        return [Position(x, y) for y in range(battle.height) for x in range(battle.width)
                if Position(x, y) != actor.position
                and battle.terrain_step_allowed(actor, actor.position, Position(x, y))
                and battle.can_place_unit(actor, Position(x, y), ignore=actor, mover=actor)]

    def can_use(self, battle: Battle, actor: Unit, payload: dict[str, Any] | None = None) -> tuple[bool, str]:
        ok, reason = super().can_use(battle, actor, payload)
        if not ok:
            return ok, reason
        if not self.available_cells(battle, actor):
            return False, "当前没有合法瞬移落点。"
        if payload and any(key in payload for key in ("x", "y", "cells")):
            try:
                if payload_position(payload) not in self.available_cells(battle, actor):
                    return False, "请选择完整合法的瞬移落点。"
            except (ActionError, ValueError, TypeError, KeyError):
                return False, "请选择完整合法的瞬移落点。"
        return True, ""

    def prepay_resources(self, battle: Battle, actor: Unit, payload: dict[str, Any] | None = None) -> None:
        if payload_position(payload or {}) not in self.available_cells(battle, actor):
            raise ActionError("瞬移落点已失效。")
        super().prepay_resources(battle, actor, payload)

    def execute(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> None:
        destination = payload_position(payload)
        if destination not in self.available_cells(battle, actor):
            raise ActionError("瞬移落点已失效。")
        battle.move_unit(actor, destination, via_skill=True, allow_anywhere=True,
                         max_distance=max(battle.width, battle.height), tags={"hell_teleport"})
        battle.log_public_event(f"{actor.name} 瞬移到 ({destination.x}, {destination.y})。",
                                source=actor, target=actor)

    def preview(self, battle: Battle, actor: Unit) -> dict[str, Any]:
        return {"cells": positions_to_dict(self.available_cells(battle, actor)),
                "target_unit_ids": [], "secondary_cells": [], "requires_target": True}

    def get_target_cells_for_payload(self, battle: Battle, actor: Unit, payload: dict[str, Any]) -> list[Position]:
        return []


def resolve_hell_dog_death_burst(battle: Battle, actor: Unit, queued_action: Any) -> None:
    cells = battle.payload_positions(queued_action.payload, "cells")
    for recipient in battle.effect_units_at_cells(cells):
        battle.resolve_damage(DamageContext(
            source=actor, target=recipient, attack_power=3,
            is_skill=False, from_field_effect=True, action_name=queued_action.display_name,
            tags={"hell_dog_death_burst", "death_trait"},
        ))


class HellSnowRebirthTrait(HellRebirthTrait):
    def __init__(self) -> None:
        super().__init__("excel_r247")
        self.name = "地狱犬群再临"
        self.description = "被技能破坏时，本人现存地狱犬各在旧格爆破；下个敌方武将回合末0魔回场，三本人轮自然回魔并重置瞬移。"

    def on_after_damage(self, battle: Battle, ctx: DamageContext) -> None:
        owner = self.owner
        if (owner is None or ctx.target.unit_id != owner.unit_id or owner.alive
                or not ctx.is_skill or ctx.from_field_effect):
            return
        super().on_after_damage(battle, ctx)
        self.burst_dogs(battle)

    def on_hell_forced_skill_death(self, battle: Battle) -> None:
        super().on_hell_forced_skill_death(battle)
        self.burst_dogs(battle)

    def burst_dogs(self, battle: Battle) -> None:
        owner = self.owner
        if owner is None or getattr(owner, "_hell_dog_burst_scheduled", False):
            return
        owner._hell_dog_burst_scheduled = True
        dogs = [unit for unit in battle.all_units() if unit.alive and not unit.banished and unit.position is not None
                and unit.hero_code == "hell_dog_summon" and unit.summoner_id == owner.unit_id]
        for index, dog in enumerate(dogs, start=1):
            body = set(battle.unit_cells(dog))
            ring = sorted((cell for cell in square_around_cells(battle, list(body), radius=1)
                           if cell not in body), key=lambda cell: (cell.y, cell.x))
            dog.alive = False
            battle.log_public_event(f"{dog.name} 随{owner.name}的技能死亡而破坏并爆破。",
                                    source=owner, target=dog)
            if ring:
                battle.queue_skill_effect_action(
                    actor=owner, display_name=f"地狱犬死亡爆破·{index}", effect_code="hell_dog_death_burst",
                    payload={"after_death_effect": True, "cells": positions_to_dict(ring),
                             "refresh_cell_targets": True, "attack_power": 3},
                    target_cells=ring, speed=1, segment_index=index, segment_count=len(dogs),
                    effect_resolver=resolve_hell_dog_death_burst,
                )

    def on_hell_rebirth(self, battle: Battle) -> None:
        owner = self.owner
        if owner is None:
            return
        owner._hell_dog_burst_scheduled = False
        teleport = next((skill for skill in owner.skills if skill.code == "hell_teleport"), None)
        if teleport is not None:
            teleport.uses_this_battle = 0
        status = HellSnowManaStatus()
        status.is_skill_effect = False
        owner.add_status(status, source=owner)
        battle.log_public_event(f"{owner.name} 地狱再临，瞬移已重置，并获得三轮自然回魔。",
                                source=owner, target=owner)


def resolve_hell_hunter_rescue(battle: Battle, actor: Unit, queued_action: Any) -> None:
    target_id = str(queued_action.payload.get("rescued_unit_id") or "")
    target = next((unit for unit in battle.destroyed_units if unit.unit_id == target_id), None)
    cells = battle.payload_positions(queued_action.payload, "cells")
    if target is None or not cells or not battle.restore_destroyed_hell_ally(target, cells[0]):
        battle.log(f"{actor.name} 的死亡救援因原格失效而落空。")


class HellHunterRescueTrait(HellRebirthTrait):
    requires_damage_choice = True

    def __init__(self) -> None:
        super().__init__("excel_r245")
        self.name = "地狱猎人救援"
        self.description = "技能死亡时先在原格救回一名已破坏的其他地狱友军，之后本人于下个敌方武将回合末0魔回场。"

    def rescue_options(self, battle: Battle, origin: Position) -> list[str]:
        owner = self.owner
        if owner is None:
            return []
        return [unit.unit_id for unit in battle.destroyed_units
                if not unit.alive and unit.unit_id != owner.unit_id
                and unit.player_id == owner.player_id and "地狱" in unit.name
                and (not unit.is_summon or not unit.summoner_id
                     or (unit.summoner_id in battle.units and battle.units[unit.summoner_id].alive))
                and battle.can_place_unit(unit, origin, mover=unit)]

    def offer_rescue(self, battle: Battle) -> None:
        owner = self.owner
        if owner is None or getattr(owner, "_hell_hunter_rescue_scheduled", False):
            return
        owner._hell_hunter_rescue_scheduled = True
        origin = owner.position or owner.last_position
        if origin is None:
            return
        options = self.rescue_options(battle, origin)
        choice = battle.optional_position_choice(owner, options, action_name="地狱猎人死亡救援",
                                                 kind="hell_rescue")
        if choice not in options:
            battle.log(f"{owner.name} 没有选择可救回的地狱友军。")
            return
        target = next((unit for unit in battle.destroyed_units if unit.unit_id == choice), None)
        if target is None:
            return
        battle.queue_skill_effect_action(
            actor=owner, display_name="地狱猎人死亡救援", effect_code="hell_hunter_rescue",
            payload={"after_death_effect": True, "rescued_unit_id": choice, "cells": [origin.to_dict()]},
            target_cells=[origin], include_cell_units=False, hostile=False,
            effect_resolver=resolve_hell_hunter_rescue,
        )
        battle.log_public_event(f"{owner.name} 临死选择救回{target.name}。", source=owner, target=target)

    def on_after_damage(self, battle: Battle, ctx: DamageContext) -> None:
        owner = self.owner
        if (owner is None or ctx.target.unit_id != owner.unit_id or owner.alive
                or not ctx.is_skill or ctx.from_field_effect):
            return
        super().on_after_damage(battle, ctx)
        self.offer_rescue(battle)

    def on_hell_forced_skill_death(self, battle: Battle) -> None:
        super().on_hell_forced_skill_death(battle)
        self.offer_rescue(battle)

    def on_hell_rebirth(self, battle: Battle) -> None:
        if self.owner is not None:
            self.owner._hell_hunter_rescue_scheduled = False

    def on_hell_rescue(self, battle: Battle) -> None:
        if self.owner is not None:
            self.owner._hell_hunter_rescue_scheduled = False


