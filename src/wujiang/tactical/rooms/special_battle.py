"""Battle implementation for BP rules in data/特殊规则.xlsx."""

from __future__ import annotations

import secrets
from typing import Sequence

from wujiang.tactical.engine.core import ActionError, Battle, BattleFieldEffect, DamageContext, HealContext, HeroUnit, Position, Skill, Unit
from wujiang.tactical.rooms.special_modes import SPECIAL_MODE_BY_CODE


def real_hero(unit: Unit) -> bool:
    return isinstance(unit, HeroUnit) and not unit.is_summon and not unit.is_clone


def prepare_bp_special_rosters(player1: list[Unit], player2: list[Unit], codes: Sequence[str]) -> tuple[list[Unit], list[Unit]]:
    """Apply modes that change who enters the battle before spawn placement."""
    selected = (list(player1), list(player2))
    if "general" in codes:
        remaining = []
        for team in selected:
            if len(team) <= 1:
                remaining.append(team)
                continue
            highest = max(unit.level for unit in team)
            excluded = secrets.choice([unit for unit in team if unit.level == highest])
            remaining.append([unit for unit in team if unit is not excluded])
        selected = tuple(remaining)
    if "duel" in codes:
        selected = tuple([secrets.choice(team)] for team in selected)
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
        self._stat_bonus: dict[str, dict[str, int]] = {}
        self._sympathy: dict[str, set[str]] = {}
        self._arena_order: dict[int, list[str]] = {}
        self._pending_match_end: tuple[str, str] | None = None

    def initialize(self, battle: Battle) -> None:
        heroes = [unit for unit in battle.all_units() if real_hero(unit)]
        if "sympathy" in self.codes and heroes:
            for stat in ("attack", "defense", "speed", "attack_range"):
                maximum = max(unit.stat(stat) for unit in heroes)
                self._sympathy[stat] = {unit.unit_id for unit in heroes if unit.stat(stat) == maximum}
        if "arena" in self.codes:
            first_team = battle.units[battle.turn_order_unit_ids[0]].player_id
            if "fast" in self.codes:
                first_team = 3 - first_team
            for team in (1, 2):
                ordered = [unit.unit_id for unit in heroes if unit.player_id == team]
                secrets.SystemRandom().shuffle(ordered)
                if "fast" in self.codes:
                    ordered.reverse()
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
        if "fast" in self.codes and stat_name == "speed":
            return 10.0
        if "true_turn" in self.codes and stat_name == "attack_range":
            return float(max(battle.width, battle.height))
        return value + self._stat_bonus.get(unit.unit_id, {}).get(stat_name, 0)

    def blocks_skill_use(self, battle: Battle, actor: Unit, skill: Skill) -> tuple[bool, str]:
        if "real_men" in self.codes and skill.timing != "active":
            return True, "真男人模式只能使用主动技能。"
        if "slow" in self.codes and real_hero(actor):
            for owned_skill in actor.action_skills():
                owned_skill.sync_turn_scope(battle)
            if sum(owned_skill.uses_this_turn for owned_skill in actor.action_skills()) >= 1:
                return True, "慢速模式每回合只能使用一个技能。"
        if "sympathy" in self.codes:
            if (actor.unit_id in self._sympathy.get("attack", set()) and skill.timing == "active"
                    and self._deals_direct_damage(skill)):
                return True, "同情弱者：攻最高的武将不能使用主动直接伤害技能。"
            if actor.unit_id in self._sympathy.get("speed", set()) and self._causes_movement(skill):
                return True, "同情弱者：速最高的武将不能使用位移技能。"
        return False, ""

    @staticmethod
    def _deals_direct_damage(skill: Skill) -> bool:
        # Classify the implemented skill operation, not prose that may describe
        # a later trigger, a defense against damage, or explicitly no damage.
        execute_code = getattr(type(skill).execute, "__code__", None)
        names = set(execute_code.co_names) if execute_code is not None else set()
        return bool(names & {"DamageContext", "resolve_damage", "queue_damage", "damage_at_cells",
                             "resolve_area_damage", "resolve_hit", "resolve_attack_damage",
                             "queue_area_damage_effect",
                             "queue_projectile", "apply_to_units"}) or bool(
                                 {"super", "execute"} <= names and "伤害" in skill.description)

    @staticmethod
    def _causes_movement(skill: Skill) -> bool:
        execute_code = getattr(type(skill).execute, "__code__", None)
        names = set(execute_code.co_names) if execute_code is not None else set()
        return bool(names & {"move_unit", "gladiator_swap", "swap_units", "swap_positions",
                             "teleport_unit", "force_move_unit", "push_unit", "pull_unit"}) or any(
                                 phrase in skill.description for phrase in
                                 ("位移", "瞬移", "传送", "移动到", "移动恰好", "飞行移动",
                                  "普攻先移动", "推开", "拉近", "换位", "突进", "冲刺", "跳跃"))

    def blocks_skill_payload(self, battle: Battle, actor: Unit, skill: Skill,
                             payload: dict | None) -> tuple[bool, str]:
        if ("sympathy" not in self.codes or actor.unit_id not in self._sympathy.get("attack_range", set())
                or actor.position is None or not payload):
            return False, ""
        cells: list[Position] = []
        target_units: list[Unit] = []
        for key in ("target_unit_id", "resolved_target_unit_id"):
            target = battle.units.get(str(payload.get(key) or ""))
            if target is not None and target.position is not None:
                target_units.append(target)
        for unit_id in payload.get("target_unit_ids", ()) or ():
            target = battle.units.get(str(unit_id))
            if target is not None and target.position is not None:
                target_units.append(target)
        for x_key, y_key in (("x", "y"), ("target_x", "target_y"),
                             ("declared_target_x", "declared_target_y"),
                             ("center_x", "center_y"), ("dest_x", "dest_y"),
                             ("move_x", "move_y"), ("landing_x", "landing_y")):
            if payload.get(x_key) is not None and payload.get(y_key) is not None:
                cells.append(Position(int(payload[x_key]), int(payload[y_key])))
        for key in ("cells", "target_cells", "target_cell", "destination"):
            raw = payload.get(key, ()) or ()
            for cell in (raw if isinstance(raw, (list, tuple)) else [raw]):
                if isinstance(cell, Position):
                    cells.append(cell)
                elif isinstance(cell, dict) and cell.get("x") is not None and cell.get("y") is not None:
                    cells.append(Position(int(cell["x"]), int(cell["y"])))
        if (any(battle.unit_distance_to_cell(actor, cell) > 1 for cell in cells)
                or any(battle.distance_between_units(actor, target) > 1 for target in target_units)):
            return True, "同情弱者：范最高的武将只能在1格范围内发动技能。"
        return False, ""

    def on_before_heal(self, battle: Battle, ctx: HealContext) -> None:
        if ("sympathy" in self.codes and ctx.target.unit_id in self._sympathy.get("defense", set())
                and self._is_passive_effect(battle, ctx.source, ctx.action_name)):
            ctx.cancelled = True
            ctx.reason = "同情弱者：守最高的武将不受被动技能影响。"
            return
        if "no_recovery" in self.codes:
            ctx.cancelled = True
            ctx.reason = "老一套对我没用：本局禁止回复。"

    def on_after_heal(self, battle: Battle, ctx: HealContext) -> None:
        gained = max(0.0, ctx.actual_heal)
        if gained and "healer_attack" in self.codes:
            self.on_direct_heal(battle, ctx.target, gained, source=ctx.source)

    def on_direct_heal(self, battle: Battle, target: Unit, amount: float,
                       *, source: Unit | None = None) -> None:
        if "healer_attack" not in self.codes:
            return
        healer_team = source.player_id if source is not None else target.player_id
        enemies = [unit for unit in battle.on_field_hero_units() if unit.player_id != healer_team]
        if enemies:
            enemy = secrets.choice(enemies)
            battle.resolve_damage(DamageContext(source=source or target, target=enemy,
                                               action_name="回复反噬", attack_power=0, is_skill=False,
                                               raw_damage=amount, from_field_effect=True))

    def on_mana_gained(self, battle: Battle, unit: Unit, amount: float) -> None:
        if amount <= 0 or "healer_attack" not in self.codes:
            return
        action = battle.resolving_action
        origin = battle.units.get(action.actor_id) if action is not None else None
        healer_team = origin.player_id if origin is not None else unit.player_id
        enemies = [hero for hero in battle.on_field_hero_units() if hero.player_id != healer_team]
        if enemies:
            secrets.choice(enemies).drain_mana(amount)

    def on_before_damage(self, battle: Battle, ctx: DamageContext) -> None:
        action = battle.resolving_action
        acting_unit = battle.units.get(action.actor_id) if action is not None else None
        action_skill_code = str(action.payload.get("skill_code") or "") if action is not None else ""
        if ("sympathy" in self.codes and action is not None
                and action.action_type in {"skill", "skill_effect"}
                and action.actor_id in self._sympathy.get("attack", set())
                and acting_unit is not None and any(
                    skill.code == action_skill_code and skill.timing == "active"
                    for skill in acting_unit.action_skills())
                and not ctx.from_field_effect):
            ctx.cancelled = True
            ctx.reason = "同情弱者：攻最高的武将不能以主动技能直接造成伤害。"
        if "face_tank" in self.codes:
            enemy_heroes = [hero for hero in battle.on_field_hero_units()
                            if hero.player_id == ctx.target.player_id]
            if (ctx.source is not None and ctx.source.player_id == ctx.target.player_id
                    or not enemy_heroes or not real_hero(ctx.target) or
                    ctx.target.stat("defense") < max(hero.stat("defense") for hero in enemy_heroes)):
                ctx.cancelled = True
                ctx.reason = "刚正面：只能伤害对方守最高的武将。"
        if ("sympathy" in self.codes and ctx.target.unit_id in self._sympathy.get("defense", set())
                and self._is_passive_effect(battle, ctx.source, ctx.action_name)):
            ctx.cancelled = True
            ctx.reason = "同情弱者：守最高的武将不受被动技能影响。"

    def on_targeted(self, battle: Battle, ctx) -> None:
        if ("sympathy" in self.codes and ctx.target.unit_id in self._sympathy.get("defense", set())
                and self._is_passive_effect(battle, ctx.actor, ctx.action_name)):
            ctx.cancelled = True
            ctx.reason = "同情弱者：守最高的武将不受被动技能影响。"

    def blocks_status_application(self, battle: Battle, source: Unit | None, target: Unit, status) -> bool:
        return ("sympathy" in self.codes and target.unit_id in self._sympathy.get("defense", set())
                and self._is_passive_effect(battle, source, status.name))

    @staticmethod
    def _is_passive_effect(battle: Battle, source: Unit | None, action_name: str) -> bool:
        action = battle.resolving_action
        if action is not None and (source is None or action.actor_id == source.unit_id):
            if action.action_type == "reaction_skill":
                return True
            if source is not None:
                code = str(action.payload.get("skill_code") or "")
                if any(skill.code == code and skill.timing in {"passive", "reaction"}
                       for skill in source.action_skills()):
                    return True
        return bool(source is not None and any(
            skill.name == action_name and skill.timing in {"passive", "reaction"}
            for skill in source.action_skills()))

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
        self._stat_bonus.pop(original_id, None)
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
                unit.banished = False
                del unit._bp_arena_benched

    def on_unit_entered(self, battle: Battle, unit: Unit) -> None:
        if "arena" not in self.codes:
            return
        controller = battle.units.get(battle.controlling_hero_id(unit) or "")
        if controller is not None and controller is not unit and getattr(controller, "_bp_arena_benched", False):
            unit._bp_arena_benched = True
            unit.banished = True

    def on_after_any_turn_end(self, battle: Battle, ended_player_id: int) -> None:
        if "mages" in self.codes:
            hero = battle.current_turn_unit()
            if (hero is not None and hero.alive and not hero.banished
                    and not hero.is_stealthed()
                    and not any(getattr(status, "grants_stealth", False) for status in hero.statuses)):
                hero.current_mana = hero.max_mana()

    def on_round_complete(self, battle: Battle) -> None:
        if "eternal" not in self.codes:
            return
        left = (battle.width - 5) // 2
        top = (battle.height - 5) // 2
        present = {unit.player_id for unit in battle.on_field_hero_units() if unit.position is not None
                   and left <= unit.position.x < left + 5 and top <= unit.position.y < top + 5}
        if len(present) == 1:
            winner = next(iter(present))
            stat = secrets.choice(("attack", "defense", "speed", "attack_range"))
            for unit in battle.all_units():
                if unit.alive and not unit.banished and unit.player_id == winner:
                    self._stat_bonus.setdefault(unit.unit_id, {})[stat] = self._stat_bonus.get(unit.unit_id, {}).get(stat, 0) + 1
            battle.log(f"永恒轮回战场：玩家 {winner} 全员{stat} +1。")

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
