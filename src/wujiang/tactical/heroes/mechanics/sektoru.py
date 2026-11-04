"""R193 Sektoru cooperation mechanics."""

from __future__ import annotations

from wujiang.tactical.engine.core import ActionError
from wujiang.tactical.engine.core import Battle
from wujiang.tactical.engine.core import MoveContext
from wujiang.tactical.engine.core import Position
from wujiang.tactical.engine.core import Trait
from wujiang.tactical.engine.core import Unit
from wujiang.tactical.heroes.common import StatModifierStatus
from wujiang.tactical.heroes.mechanics.next_five_geometry import square_around_cells


class SektoruCooperationStatus(StatModifierStatus):
    def __init__(self) -> None:
        super().__init__("塞克托鲁协同增益", attack_delta=1, mana_delta=1,
                         duration=2, tick_scope="owner_turn_end")
        self.is_skill_effect = False

    def on_removed(self, battle: Battle) -> None:
        if self.owner is not None:
            self.owner.clamp_mana()


class SektoruMovedByAllyTrait(Trait):
    requires_damage_choice = True

    def __init__(self) -> None:
        super().__init__("塞克托鲁协同布阵", "其他友方效果搬动本人后攻魔+1、重置剑斗之魂并重排至多两名单位。")

    def placement_options(self, battle: Battle, owner: Unit, chosen: set[str]) -> list[str]:
        if owner.position is None:
            return []
        surrounding = sorted((cell for cell in square_around_cells(battle, battle.unit_cells(owner), radius=1)
                              if cell not in set(battle.unit_cells(owner))), key=lambda cell: (cell.y, cell.x))
        options: list[str] = []
        for unit in battle.all_units():
            if (unit.unit_id == owner.unit_id or unit.unit_id in chosen or not unit.alive
                    or unit.banished or unit.position is None or getattr(unit, "attached_to_unit_id", None)
                    or getattr(unit, "standable_terrain", False) or unit.direct_effects_blocked()
                    or not battle.unit_can_be_selected(unit, actor=owner)[0]):
                continue
            for cell in surrounding:
                if cell == unit.position or not battle.can_place_unit(unit, cell, ignore=unit, mover=unit):
                    continue
                rider = battle.rider_for(unit)
                if rider is not None and rider.position is not None:
                    end = rider.position.offset(cell.x - unit.position.x, cell.y - unit.position.y)
                    if not battle.can_place_unit(rider, end, ignore=rider, mover=rider):
                        continue
                options.append(f"{unit.unit_id}@{cell.x},{cell.y}")
        return options

    def on_unit_moved(self, battle: Battle, ctx: MoveContext) -> None:
        owner = self.owner
        if (owner is None or ctx.unit.unit_id != owner.unit_id or ctx.start == ctx.end
                or "sektoru_formation" in ctx.tags):
            return
        source_id = next((tag.removeprefix("source:") for tag in ctx.tags if tag.startswith("source:")), None)
        if source_id is None and battle.resolving_action is not None:
            source_id = battle.resolving_action.actor_id
        source = battle.units.get(source_id or "")
        if source is None or source.unit_id == owner.unit_id or source.player_id != owner.player_id:
            return
        existing = owner.get_status("塞克托鲁协同增益")
        if existing is None:
            owner.add_status(SektoruCooperationStatus(), source=source)
            owner.current_mana = round(min(owner.max_mana(), owner.current_mana + 1), 2)
        else:
            existing.duration = 2
        soul = next((skill for skill in owner.skills if skill.code == "gladiator_soul"), None)
        if soul is not None:
            soul.cooldown_remaining = 0
            soul.uses_this_battle = 0
        battle.log_public_event(f"{owner.name} 受友方位移，攻魔各+1并重置剑斗之魂。",
                                source=source, target=owner)
        chosen: set[str] = set()
        for index in range(2):
            options = self.placement_options(battle, owner, chosen)
            if not options:
                break
            selection = battle.formation_placement_choice(owner, options, index + 1)
            if selection not in options:
                continue
            unit_id, position = selection.rsplit("@", 1)
            x, y = (int(value) for value in position.split(",", 1))
            target = battle.units.get(unit_id)
            if target is None:
                continue
            chosen.add(unit_id)
            try:
                battle.move_unit(target, Position(x, y), via_skill=False, forced=True,
                                 allow_anywhere=True, tags={"sektoru_formation", f"source:{owner.unit_id}"})
                battle.log_public_event(f"{owner.name} 将 {target.name} 布置到 ({x},{y})。",
                                        source=owner, target=target)
            except ActionError:
                battle.log_public_event(f"{owner.name} 的剑斗布阵落点已不可用。", source=owner)


