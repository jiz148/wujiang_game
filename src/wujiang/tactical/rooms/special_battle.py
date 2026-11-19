"""Battle implementation for BP rules in data/特殊规则.xlsx."""

from __future__ import annotations

from wujiang.tactical.prediction import secrets
from typing import Sequence

from wujiang.tactical.engine.core import ActionError, Battle, BattleFieldEffect, DamageContext, HealContext, HeroUnit, Position, Skill, Unit
from wujiang.tactical.rooms.special_modes import SPECIAL_MODE_BY_CODE


def real_hero(unit: Unit) -> bool:
    return isinstance(unit, HeroUnit) and not unit.is_summon and not unit.is_clone


def prepare_bp_special_rosters(player1: list[Unit], player2: list[Unit], codes: Sequence[str]) -> tuple[list[Unit], list[Unit]]:
    """Apply modes that change who enters the battle before spawn placement."""
    selected = (list(player1), list(player2))
    if "duel" in codes:
        if "general" in codes:
            remaining = []
            for team in selected:
                if len(team) <= 1:
                    remaining.append(team)
                    continue
                highest = max(unit.level for unit in team)
                general = secrets.choice([unit for unit in team if unit.level == highest])
                remaining.append([unit for unit in team if unit is not general])
            selected = tuple(remaining)
        pairs = [(left, right) for left in selected[0] for right in selected[1]]
        smallest_gap = min(abs(left.level - right.level) for left, right in pairs)
        chosen = secrets.choice([(left, right) for left, right in pairs
                                 if abs(left.level - right.level) == smallest_gap])
        selected = ([chosen[0]], [chosen[1]])
    return selected


class BPSpecialModeEffect(BattleFieldEffect):
    """Permanent room rule; no hero owns it and weather cannot replace it."""

    ignores_direct_effect_immunity = True

    def __init__(self, codes: Sequence[str]) -> None:
        self.codes = frozenset(codes)
        names = " + ".join(SPECIAL_MODE_BY_CODE[code]["name"] for code in codes)
        super().__init__(name=f"BP特殊模式：{names}", description="本局BP特殊规则")
        self._tether_guard = False
        self._initial_distance: dict[tuple[str, str], int] = {}
        self._sympathy_unit_ids: set[str] = set()
        self._general_ids: dict[int, str] = {}
        self._arena_order: dict[int, list[str]] = {}
        self._mages_original_mana: dict[str, float] = {}
        self._pending_match_end: tuple[str, str] | None = None

    def initialize(self, battle: Battle) -> None:
        heroes = [unit for unit in battle.all_units() if real_hero(unit)]
        if "mages" in self.codes:
            for hero in heroes:
                original = float(hero.base_stats.mana)
                self._mages_original_mana[hero.unit_id] = original
                hero.current_mana = original
        if "sympathy" in self.codes and heroes:
            lowest = min(unit.level for unit in heroes)
            self._sympathy_unit_ids = {unit.unit_id for unit in heroes if unit.level == lowest}
        if "general" in self.codes and "duel" not in self.codes:
            for team in (1, 2):
                members = [unit for unit in heroes if unit.player_id == team]
                if len(members) <= 1:
                    continue
                highest = max(unit.level for unit in members)
                general = secrets.choice([unit for unit in members if unit.level == highest])
                self._general_ids[team] = general.unit_id
                self._set_general_bench(battle, general, benched=True)
                battle.log(f"大将模式：{general.name} 候场，其他己方武将全部被破坏后参战。")
        if "big_rock" in self.codes:
            left, top = (battle.width - 5) // 2, (battle.height - 5) // 2
            battle.special_terrain_cells = {(x, y) for x in range(left, left + 5)
                                            for y in range(top, top + 5)
                                            if 0 <= x < battle.width and 0 <= y < battle.height}
        if "arena" in self.codes:
            first_team = battle.units[battle.turn_order_unit_ids[0]].player_id
            if "fast" in self.codes:
                first_team = 3 - first_team
            for team in (1, 2):
                ordered = [unit.unit_id for unit in heroes if unit.player_id == team]
                secrets.SystemRandom().shuffle(ordered)
                if "fast" in self.codes:
                    ordered.reverse()
                if team in self._general_ids:
                    ordered.remove(self._general_ids[team])
                    ordered.append(self._general_ids[team])
                self._arena_order[team] = ordered
                for unit_id in ordered[1:]:
                    self._set_arena_bench(battle, battle.units[unit_id], benched=True)
            arena_turn_order = [unit_id for index in range(max(map(len, self._arena_order.values())))
                                for team in (first_team, 3 - first_team)
                                for unit_id in self._arena_order[team][index:index + 1]]
            battle.configure_turn_order(arena_turn_order, starting_index=0)
            battle.log("擂台赛：双方仅当前擂主可行动，其余武将候场。")
        if "chain" in self.codes:
            for team in (1, 2):
                ordered = [battle.units[unit_id] for unit_id in battle.turn_order_unit_ids
                           if unit_id in battle.units and battle.units[unit_id].player_id == team]
                for index, unit in enumerate(ordered):
                    nxt = ordered[(index + 1) % len(ordered)]
                    if unit is not nxt and unit.position and nxt.position:
                        self._initial_distance[(unit.unit_id, nxt.unit_id)] = battle.distance_between_units(unit, nxt)

    def modify_unit_stat(self, battle: Battle, unit: Unit, stat_name: str, value: float) -> float:
        if unit.unit_id in self._sympathy_unit_ids and stat_name in {"attack", "defense", "speed"}:
            value += 1.0
        if "fast" in self.codes and stat_name == "speed":
            return 10.0
        if "true_turn" in self.codes and stat_name == "attack_range":
            return float(max(battle.width, battle.height))
        return value + (1.0 if "attack_plus_one" in self.codes and stat_name == "attack" else 0.0)

    def adjust_unit_targeting_range(self, battle: Battle, unit: Unit, value: int) -> int:
        return max(battle.width, battle.height) if "true_turn" in self.codes else value

    def ignores_direct_unit_target_line(self, battle: Battle, actor: Unit) -> bool:
        return "true_turn" in self.codes

    def blocks_unit_movement(self, battle: Battle, unit: Unit) -> bool:
        return "true_turn" in self.codes

    def blocks_skill_use(self, battle: Battle, actor: Unit, skill: Skill) -> tuple[bool, str]:
        if "true_turn" in self.codes and self._causes_movement(battle, actor, skill):
            return True, "真回合制不能使用位移技能。"
        if "real_men" in self.codes and skill.timing != "active":
            return True, "真男人模式只能使用主动技能。"
        if "slow" in self.codes and real_hero(actor):
            for owned_skill in actor.action_skills():
                owned_skill.sync_turn_scope(battle)
            if sum(owned_skill.uses_this_turn for owned_skill in actor.action_skills()) >= 1:
                return True, "慢速模式每回合只能使用一个技能。"
        return False, ""

    @staticmethod
    def _causes_movement(battle: Battle, actor: Unit, skill: Skill,
                         payload: dict | None = None) -> bool:
        return skill.moves_owner_on_use(battle, actor, payload)

    def blocks_skill_payload(self, battle: Battle, actor: Unit, skill: Skill,
                             payload: dict | None) -> tuple[bool, str]:
        if "true_turn" in self.codes and self._causes_movement(battle, actor, skill, payload):
            return True, "真回合制不能使用位移技能。"
        return False, ""

    def on_before_heal(self, battle: Battle, ctx: HealContext) -> None:
        if "no_recovery" in self.codes:
            ctx.cancelled = True
            ctx.reason = "老一套对我没用：本局禁止回复。"

    def on_before_damage(self, battle: Battle, ctx: DamageContext) -> None:
        if "face_tank" in self.codes:
            enemy_heroes = [hero for hero in battle.on_field_hero_units()
                            if hero.player_id == ctx.target.player_id]
            if (ctx.source is not None and ctx.source.player_id == ctx.target.player_id
                    or not enemy_heroes or not real_hero(ctx.target) or
                    ctx.target.stat("defense") < max(hero.stat("defense") for hero in enemy_heroes)):
                ctx.cancelled = True
                ctx.reason = "刚正面：只能伤害对方守最高的武将。"

    def on_after_source_damage(self, battle: Battle, ctx: DamageContext) -> None:
        if ("soft_persimmon" in self.codes and real_hero(ctx.target) and not ctx.target.alive
                and "bp_soft_persimmon" not in ctx.tags):
            if self._replace_soft_persimmon(battle, ctx.target, source=ctx.source, ctx=ctx):
                ctx.tags.add("bp_soft_persimmon")
        if "arena" in self.codes and real_hero(ctx.target) and not ctx.target.alive:
            self._advance_arena(battle, ctx.target)
        if ("sudden_death" in self.codes and real_hero(ctx.target) and not ctx.target.alive
                and battle.winner is None):
            self._award_sudden_death(battle, ctx.target)
        if "duel" in self.codes and real_hero(ctx.target) and not ctx.target.alive and battle.winner is None:
            self._award_duel_win(battle, ctx.target)

    def on_before_dead_cleanup(self, battle: Battle) -> None:
        """Catch destruction caused by effects that do not use DamageContext."""
        for unit in list(battle.all_units()):
            if unit.alive:
                continue
            if ("arena" in self.codes and unit.banished and getattr(unit, "_bp_arena_benched", False)
                    and not getattr(unit, "_bp_soft_replacement", False)):
                controller = battle.units.get(battle.controlling_hero_id(unit) or "")
                if controller is unit or controller is not None and controller.alive:
                    unit.alive = True
                    object.__setattr__(unit, "current_hp", max(unit.current_hp, 0.25))
                    continue
            if not real_hero(unit):
                continue
            if "soft_persimmon" in self.codes and not getattr(unit, "_bp_soft_replacement", False):
                self._replace_soft_persimmon(battle, unit)
        self._activate_waiting_generals(battle)
        for unit in list(battle.all_units()):
            if not real_hero(unit) or unit.alive:
                continue
            self._advance_arena(battle, unit)
            if "sudden_death" in self.codes and battle.winner is None:
                self._award_sudden_death(battle, unit)
            if "duel" in self.codes and battle.winner is None:
                self._award_duel_win(battle, unit)

    def _replace_soft_persimmon(self, battle: Battle, original: Unit, *,
                                source: Unit | None = None,
                                ctx: DamageContext | None = None) -> bool:
        order = battle.turn_order_unit_ids
        if original.unit_id not in order or getattr(original, "_bp_soft_replacement", False):
            return False
        index = order.index(original.unit_id)
        successor = next((battle.units[unit_id] for offset in range(1, len(order))
                          if (unit_id := order[(index + offset) % len(order)]) in battle.units
                          and battle.units[unit_id].player_id == original.player_id
                          and real_hero(battle.units[unit_id]) and battle.units[unit_id].alive), None)
        if successor is None:
            return False
        from wujiang.tactical.heroes.registry import create_hero
        import weakref

        original_id = original.unit_id
        position = (getattr(original, "_resolution_actual_position", None)
                    or original.position or original.last_position)
        owner_seat_id = getattr(original, "owner_seat_id", None)
        if ctx is not None and source is not None:
            for component in list(source.iter_components()):
                hook = getattr(component, "on_confirmed_damage_destruction", None)
                if callable(hook):
                    hook(battle, ctx)
        for summon in list(battle.all_units()):
            if (summon.is_summon or summon.is_clone) and original_id in {
                summon.summoner_id, getattr(summon, "seed_id", None),
                getattr(summon, "controller_hero_id", None), getattr(summon, "creator_unit_id", None),
            }:
                summon.alive = False
        for status in list(original.statuses):
            original.remove_status(status, battle)
        for field in list(battle.field_effects):
            if field is self:
                continue
            owners = getattr(field, "owner_unit_ids", None)
            shared_owner_removed = isinstance(owners, set) and original_id in owners
            if shared_owner_removed:
                owners.discard(original_id)
                if owners:
                    continue
            if (original_id in {getattr(field, key, None) for key in
                                ("source_unit_id", "owner_unit_id", "caster_unit_id", "creator_unit_id")}
                    or shared_owner_removed):
                battle.remove_field_effect(field)
        for other in battle.all_units():
            for status in list(other.statuses):
                if original_id in {getattr(status, key, None) for key in
                                   ("source_unit_id", "owner_unit_id", "caster_unit_id", "creator_unit_id")}:
                    other.remove_status(status, battle)
        fresh = create_hero(original.hero_code, original.player_id)
        original.__dict__.clear()
        original.__dict__.update(fresh.__dict__)
        original.unit_id = original_id
        original.position = position
        original.owner_seat_id = owner_seat_id
        original._battle_ref = weakref.ref(battle)
        for component in original.iter_components():
            component.bind(original)
        # A benched arena unit is otherwise immune to HP changes; replacement
        # is the explicit exception required by Soft Persimmon.
        object.__setattr__(successor, "current_hp", 0.0)
        successor.alive = False
        successor._bp_soft_replacement = True
        battle.record_defeat_summary(source, successor, "软柿子替死")
        battle.log(f"软柿子：{original.name} 在原地完全重置，{successor.name} 代替被击破。")
        self._advance_arena(battle, successor)
        if "sudden_death" in self.codes and battle.winner is None:
            self._award_sudden_death(battle, successor, replaced=True)
        if "duel" in self.codes and battle.winner is None:
            self._award_duel_win(battle, successor)
        return True

    def _award_sudden_death(self, battle: Battle, victim: Unit, *, replaced: bool = False) -> None:
        cause = "代替被击破" if replaced else "被击破"
        self._award_mode_win(battle, victim, "bp_sudden_death", "骤死模式", cause)

    def _award_duel_win(self, battle: Battle, victim: Unit) -> None:
        self._award_mode_win(battle, victim, "bp_duel", "有种来单挑", "被击破")

    def _award_mode_win(self, battle: Battle, victim: Unit, code: str, mode_name: str, cause: str) -> None:
        battle.winner = 3 - victim.player_id
        battle.win_reason_code = code
        battle.win_reason_text = f"{mode_name}：{victim.name} {cause}，玩家 {battle.winner} 获胜。"
        battle.log(battle.win_reason_text)
        self._pending_match_end = (victim.name, mode_name)

    def on_after_dead_cleanup(self, battle: Battle) -> None:
        if self._pending_match_end is None:
            return
        victim_name, mode_name = self._pending_match_end
        self._pending_match_end = None
        battle._append_summary_event("match_end", actor_unit_id=None, actor_name="系统",
                                     actor_player_id=battle.winner, target_name=victim_name,
                                     action_name=mode_name, amount=0)
        battle._emit_replay_checkpoint("match_end")

    def _advance_arena(self, battle: Battle, fallen: Unit) -> None:
        if ("arena" not in self.codes or fallen.banished
                or getattr(fallen, "_bp_arena_advanced", False)):
            return
        order = self._arena_order.get(fallen.player_id, [])
        if fallen.unit_id in order:
            fallen._bp_arena_advanced = True
            for unit_id in order[order.index(fallen.unit_id) + 1:]:
                contender = battle.units.get(unit_id)
                if contender is not None and contender.alive:
                    self._set_arena_bench(battle, contender, benched=False)
                    battle.log(f"擂台赛：{contender.name} 接替上场。")
                    break

    @staticmethod
    def _set_arena_bench(battle: Battle, hero: Unit, *, benched: bool) -> None:
        for unit in list(battle.all_units()):
            if unit.unit_id != hero.unit_id and battle.controlling_hero_id(unit) != hero.unit_id:
                continue
            if benched:
                unit._bp_arena_benched = True
                unit.banished = True
            elif getattr(unit, "_bp_arena_benched", False):
                del unit._bp_arena_benched
                if not getattr(unit, "_bp_general_benched", False):
                    unit.banished = False

    @staticmethod
    def _set_general_bench(battle: Battle, hero: Unit, *, benched: bool) -> None:
        for unit in list(battle.all_units()):
            if unit.unit_id != hero.unit_id and battle.controlling_hero_id(unit) != hero.unit_id:
                continue
            if benched:
                unit._bp_general_benched = True
                unit.banished = True
            elif getattr(unit, "_bp_general_benched", False):
                del unit._bp_general_benched
                if not getattr(unit, "_bp_arena_benched", False):
                    unit.banished = False

    def _activate_waiting_generals(self, battle: Battle) -> None:
        for team, general_id in tuple(self._general_ids.items()):
            general = battle.units.get(general_id)
            if general is None or not general.alive or not getattr(general, "_bp_general_benched", False):
                continue
            if any(real_hero(unit) and unit.alive and unit.player_id == team
                   and unit.unit_id != general_id for unit in battle.all_units()):
                continue
            self._set_general_bench(battle, general, benched=False)
            battle.log(f"大将模式：{general.name} 解除候场并参战。")

    def on_unit_entered(self, battle: Battle, unit: Unit) -> None:
        controller = battle.units.get(battle.controlling_hero_id(unit) or "")
        if controller is None or controller is unit:
            return
        if "arena" in self.codes and getattr(controller, "_bp_arena_benched", False):
            unit._bp_arena_benched = True
            unit.banished = True
        if "general" in self.codes and getattr(controller, "_bp_general_benched", False):
            unit._bp_general_benched = True
            unit.banished = True

    def on_after_any_turn_end(self, battle: Battle, ended_player_id: int) -> None:
        if "mages" in self.codes:
            hero = battle.current_turn_unit()
            if (hero is not None and hero.alive and not hero.banished
                    and not hero.is_stealthed()
                    and not any(getattr(status, "grants_stealth", False) for status in hero.statuses)):
                hero.current_mana = self._mages_original_mana.get(hero.unit_id, float(hero.base_stats.mana))

    def on_unit_moved(self, battle: Battle, ctx) -> None:
        if "chain" not in self.codes or self._tether_guard or ctx.unit.position is None:
            return
        self._follow_tether(battle, ctx.unit, {ctx.unit.unit_id})

    def _follow_tether(self, battle: Battle, leader: Unit, visited: set[str]) -> None:
        followers = [(follower_id, distance) for (leader_id, follower_id), distance in self._initial_distance.items()
                     if leader_id == leader.unit_id and follower_id not in visited]
        if not followers:
            return
        follower_id, maximum = followers[0]
        follower = battle.units.get(follower_id)
        if follower is None or not follower.alive or follower.banished or follower.position is None:
            return
        if battle.distance_between_units(leader, follower) <= maximum:
            return
        def candidate_distance(cell: Position) -> int:
            return min(left.distance_to(right)
                       for left in battle.unit_cells(leader)
                       for right in battle.unit_cells_at(follower, cell))

        old_distance = battle.distance_between_units(leader, follower)
        candidates = [Position(x, y) for x in range(battle.width) for y in range(battle.height)
                      if candidate_distance(Position(x, y)) < old_distance
                      and battle.can_place_unit(follower, Position(x, y), ignore=follower, mover=follower)]
        candidates.sort(key=lambda cell: (candidate_distance(cell) > maximum,
                                          0 if candidate_distance(cell) <= maximum else candidate_distance(cell),
                                          follower.position.distance_to(cell), cell.x, cell.y))
        self._tether_guard = True
        followed = False
        try:
            for cell in candidates:
                try:
                    battle.move_unit(follower, cell, forced=True, allow_anywhere=True,
                                     max_distance=max(battle.width, battle.height))
                    followed = True
                    break
                except ActionError:
                    continue
        finally:
            self._tether_guard = False
        if followed:
            self._follow_tether(battle, follower, visited | {follower_id})


def apply_bp_special_modes(battle: Battle, codes: Sequence[str]) -> None:
    battle.special_mode_codes = tuple(codes)
    if not codes or codes == ("none",):
        return
    effect = BPSpecialModeEffect(codes)
    battle.add_field_effect(effect)
    effect.initialize(battle)
