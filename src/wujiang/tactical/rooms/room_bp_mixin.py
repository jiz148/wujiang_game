"""GameRoom RoomBPMixin methods, retained under the public API."""

from __future__ import annotations

from typing import Any, Optional

class RoomBPMixin:
    """A cohesive Battle responsibility with no independent state."""

    def set_bp_team_size(self, token: str, team_size: Any) -> None:
        from wujiang.tactical.rooms.multiplayer import (
            RoomError,
        )
        with self._lock:
            self.require_host(token)
            if self.status != "lobby" or self.mode != "bp":
                raise RoomError("只能在BP模式大厅设置武将数量。")
            if str(team_size) not in {"3", "5"}:
                raise RoomError("BP模式只支持3v3或5v5。")
            team_size = int(team_size)
            if self.bp_team_size != team_size:
                self.bp_team_size = team_size
                self.invalidate_readiness()
                self.touch()


    def set_bp_settings(self, token: str, *, team_size: Any, level_cap: Any, show_win_count: Any,
                        bp_format: Any = None, allow_same_hero: Any = None, tier_pool: Any = None,
                        tier_rounds: Any = None, rule_mode: Any = None,
                        fixed_rule: Any = None, rule_pool: Any = None) -> None:
        from wujiang.tactical.rooms.multiplayer import (
            RoomError,
            normalize_bp_format,
            normalize_bp_rule_code,
            normalize_bp_rule_mode,
            normalize_bp_rule_pool,
            normalize_bp_tier_pool,
            normalize_bp_tier_rounds,
        )
        with self._lock:
            self.require_host(token)
            if self.status != "lobby" or self.mode != "bp":
                raise RoomError("只能在BP模式大厅修改赛前设置。")
            if str(team_size) not in {"3", "5"}:
                raise RoomError("BP模式只支持3v3或5v5。")
            size = int(team_size)
            if isinstance(level_cap, bool) or not str(level_cap).isdigit() or not 1 <= int(level_cap) <= 50:
                raise RoomError("每队总等级上限必须为1到50的整数。")
            if not isinstance(show_win_count, bool):
                raise RoomError("胜场显示设置无效。")
            next_format = self.bp_format if bp_format is None else normalize_bp_format(bp_format)
            if allow_same_hero is None:
                next_same = bool(self.bp_allow_same_hero)
            elif not isinstance(allow_same_hero, bool):
                raise RoomError("是否允许选择同一武将的设置无效。")
            else:
                next_same = allow_same_hero
            next_pool = self.bp_tier_pool if tier_pool is None else normalize_bp_tier_pool(tier_pool)
            next_rounds = self.bp_tier_rounds if tier_rounds is None else normalize_bp_tier_rounds(tier_rounds)
            try:
                next_rule_mode = self.bp_rule_mode if rule_mode is None else normalize_bp_rule_mode(rule_mode)
                next_fixed_rule = self.bp_fixed_rule if fixed_rule is None else normalize_bp_rule_code(fixed_rule)
                next_rule_pool = self.bp_rule_pool if rule_pool is None else normalize_bp_rule_pool(rule_pool)
            except ValueError as exc:
                raise RoomError(str(exc)) from exc
            cap = int(level_cap)
            if (size == self.bp_team_size and cap == self.bp_level_caps[size]
                    and show_win_count == self.bp_show_win_count
                    and next_format == self.bp_format
                    and next_same == bool(self.bp_allow_same_hero)
                    and tuple(next_pool) == tuple(self.bp_tier_pool)
                    and next_rounds == int(self.bp_tier_rounds)
                    and next_rule_mode == self.bp_rule_mode
                    and next_fixed_rule == self.bp_fixed_rule
                    and tuple(next_rule_pool) == tuple(self.bp_rule_pool)):
                return
            self.bp_team_size = size
            self.bp_level_caps[size] = cap
            self.bp_show_win_count = show_win_count
            self.bp_format = next_format
            self.bp_allow_same_hero = next_same
            self.bp_tier_pool = tuple(next_pool)
            self.bp_tier_rounds = next_rounds
            self.bp_rule_mode = next_rule_mode
            self.bp_fixed_rule = next_fixed_rule
            self.bp_rule_pool = tuple(next_rule_pool)
            self._bp_pair_cache = None
            self._bp_legal_cache = None
            self.invalidate_readiness()
            self.touch()


    def set_bp_captain(self, token: str, team_id: Any, seat_id: Any) -> None:
        from wujiang.tactical.rooms.multiplayer import (
            RoomError,
            normalize_team_id,
        )
        with self._lock:
            self.require_host(token)
            if self.status != "lobby" or self.mode != "bp":
                raise RoomError("只能在BP模式大厅指定队长。")
            team = normalize_team_id(team_id)
            seat = self._seat(seat_id)
            if not seat.is_human or seat.team_id != team:
                raise RoomError("队长必须是该队的真人玩家。")
            if self.bp_captains.get(team) != seat.player_id:
                self.bp_captains[team] = seat.player_id
                self.invalidate_readiness()
                self.touch()


    def _sync_default_captains(self) -> None:
        from wujiang.tactical.rooms.multiplayer import (
            TEAM_IDS,
        )
        if self.mode != "bp" or self.status != "lobby":
            return
        for team_id in TEAM_IDS:
            current = self.seats.get(self.bp_captains.get(team_id, -1))
            if current is not None and current.is_human and current.team_id == team_id:
                continue
            humans = [seat for seat in self._team_seats(team_id) if seat.is_human]
            if len(humans) == 1:
                self.bp_captains[team_id] = humans[0].player_id


    def _tier_round_steps(self, round_plan: dict[str, Any]) -> list[tuple[str, int]]:
        from wujiang.tactical.rooms.multiplayer import (
            tier_round_steps,
        )
        steps = round_plan.get("steps")
        if steps is None:
            return tier_round_steps(int(round_plan["starter"]), int(round_plan["bans"]), int(round_plan["picks"]))
        return [(str(kind), int(team)) for kind, team in steps]


    def _bp_sequence(self) -> list[tuple[str, int]]:
        from wujiang.tactical.rooms.multiplayer import (
            BP_FORMAT_TIER,
            TEAM_IDS,
        )
        if self.bp_format == BP_FORMAT_TIER:
            sequence: list[tuple[str, int]] = []
            for round_plan in self.bp_tier_plan:
                sequence += self._tier_round_steps(round_plan)
            return sequence
        first = self.bp_first_pick_team
        if first not in TEAM_IDS:
            return []
        other = 3 - first
        sequence = ([("ban", first)] * 2 + [("ban", other)] * 2
                    + [("pick", first)] * 2 + [("pick", other)] * 2)
        if self.bp_team_size == 5:
            sequence += ([("ban", other)] * 2 + [("ban", first)] * 2
                         + [("pick", other)] * 2 + [("pick", first)] * 2
                         + [("ban", first), ("ban", other),
                            ("pick", first), ("pick", other)])
        else:
            sequence += [("ban", other), ("ban", first),
                         ("pick", other), ("pick", first)]
        return sequence


    def _bp_picks(self, team_id: int) -> list[str]:
        return [action["hero_code"] for action in self.bp_actions if action["kind"] == "pick" and action["team_id"] == team_id]


    def _bp_level_cap(self) -> int:
        return int(self.bp_level_caps.get(self.bp_team_size, self.bp_team_size * 5))


    def _bp_total_level(self, team_id: int, actions: Optional[list[dict[str, Any]]] = None) -> int:
        from wujiang.tactical.rooms.multiplayer import (
            hero_lookup,
        )
        known = hero_lookup()
        return sum(int(action.get("level", known.get(action["hero_code"], {}).get("level", 0)))
                   for action in (self.bp_actions if actions is None else actions)
                   if action["kind"] == "pick" and action["team_id"] == team_id)


    def _bp_draft_feasible(self, actions: list[dict[str, Any]],
                           *, banned_levels: Optional[tuple[int, ...]] = None) -> bool:
        from wujiang.tactical.rooms.multiplayer import (
            BP_FORMAT_TIER,
            Counter,
            TEAM_IDS,
            hero_lookup,
            lru_cache,
        )
        if self.bp_format == BP_FORMAT_TIER:
            return True
        if self.bp_allow_same_hero:
            return self._bp_shared_draft_feasible(actions, banned_levels=banned_levels)
        known = hero_lookup()
        used = {action["hero_code"] for action in actions}
        excluded = set(self.bp_auto_banned_levels if banned_levels is None else banned_levels)
        pool = set(self.bp_hero_pool) if self.bp_hero_pool else set(known)
        available_levels = sorted(int(hero["level"]) for code, hero in known.items()
                                  if code in pool and code not in used and int(hero["level"]) not in excluded)
        needs = tuple(self.bp_team_size - sum(action["kind"] == "pick" and action["team_id"] == team
                                                for action in actions) for team in TEAM_IDS)
        budgets = tuple(self._bp_level_cap() - self._bp_total_level(team, actions) for team in TEAM_IDS)
        if any(need < 0 or budget < 0 for need, budget in zip(needs, budgets)):
            return False
        required_steps = 2 * self.bp_team_size + (6 if self.bp_team_size == 3 else 10)
        if len(available_levels) < max(sum(needs), required_steps - len(actions)):
            return False
        if any(sum(available_levels[:need]) > budget for need, budget in zip(needs, budgets)):
            return False
        if sum(available_levels[:sum(needs)]) > sum(budgets):
            return False
        counts = Counter(available_levels)
        levels = tuple(sorted(counts))

        @lru_cache(maxsize=None)
        def can_fill(index: int, red_needed: int, blue_needed: int, red_budget: int, blue_budget: int) -> bool:
            if red_needed == blue_needed == 0:
                return True
            if index == len(levels):
                return False
            level = levels[index]
            count = counts[level]
            for red_count in range(min(count, red_needed, red_budget // level), -1, -1):
                for blue_count in range(min(count - red_count, blue_needed, blue_budget // level), -1, -1):
                    if can_fill(index + 1, red_needed - red_count, blue_needed - blue_count,
                                red_budget - level * red_count, blue_budget - level * blue_count):
                        return True
            return False

        return can_fill(0, needs[0], needs[1], budgets[0], budgets[1])


    def _bp_shared_draft_feasible(self, actions: list[dict[str, Any]],
                                  *, banned_levels: Optional[tuple[int, ...]] = None) -> bool:
        from wujiang.tactical.rooms.multiplayer import (
            TEAM_IDS,
            hero_lookup,
        )
        known = hero_lookup()
        pool = set(self.bp_hero_pool) if self.bp_hero_pool else set(known)
        excluded = set(self.bp_auto_banned_levels if banned_levels is None else banned_levels)
        banned_codes = {action["hero_code"] for action in actions if action["kind"] == "ban"}
        sequence_bans = 6 if self.bp_team_size == 3 else 10
        ban_steps_left = sequence_bans - sum(action["kind"] == "ban" for action in actions)
        needs = []
        for team in TEAM_IDS:
            need = self.bp_team_size - sum(action["kind"] == "pick" and action["team_id"] == team for action in actions)
            budget = self._bp_level_cap() - self._bp_total_level(team, actions)
            if need < 0 or budget < 0:
                return False
            own_picks = {action["hero_code"] for action in actions if action["kind"] == "pick" and action["team_id"] == team}
            levels = sorted(
                int(hero["level"])
                for code, hero in known.items()
                if code in pool and code not in banned_codes and code not in own_picks and int(hero["level"]) not in excluded
            )
            if len(levels) < need or sum(levels[:need]) > budget:
                return False
            needs.append(need)
        open_heroes = sum(
            1 for code, hero in known.items()
            if code in pool and code not in banned_codes and int(hero["level"]) not in excluded
        )
        return open_heroes - ban_steps_left >= max(needs or [0])


    def _catalog_level_counts(self) -> Counter[int]:
        from wujiang.tactical.rooms.multiplayer import (
            Counter,
            hero_lookup,
        )
        return Counter(int(hero["level"]) for hero in hero_lookup().values())


    def _tier_pool_can_start(self) -> bool:
        from wujiang.tactical.rooms.multiplayer import (
            bp_tier_round_shape,
        )
        counts = self._catalog_level_counts()
        return any(
            bp_tier_round_shape(counts[level], allow_same_hero=self.bp_allow_same_hero) is not None
            for level in self.bp_tier_pool
        )


    def _plan_tier_rounds(self) -> list[dict[str, Any]]:
        from wujiang.tactical.rooms.multiplayer import (
            TEAM_IDS,
            bp_tier_round_shape,
            secrets,
            tier_round_steps,
        )
        counts = self._catalog_level_counts()
        pool = [
            level for level in self.bp_tier_pool
            if bp_tier_round_shape(counts[level], allow_same_hero=self.bp_allow_same_hero) is not None
        ]
        if not pool:
            return []
        first = self.bp_first_pick_team if self.bp_first_pick_team in TEAM_IDS else secrets.choice(list(TEAM_IDS))
        plan: list[dict[str, Any]] = []
        for index in range(int(self.bp_tier_rounds)):
            level = secrets.choice(pool)
            bans, picks = bp_tier_round_shape(counts[level], allow_same_hero=self.bp_allow_same_hero)
            starter = first if index % 2 == 0 else 3 - first
            plan.append({
                "level": level,
                "bans": bans,
                "picks": picks,
                "starter": starter,
                "steps": tier_round_steps(starter, bans, picks),
            })
        return plan


    def _bp_tier_round_at(self, step_index: int) -> tuple[Optional[dict[str, Any]], Optional[int]]:
        cursor = 0
        number = 0
        for round_plan in self.bp_tier_plan:
            steps = self._tier_round_steps(round_plan)
            if not steps:
                continue
            number += 1
            if step_index < cursor + len(steps):
                return round_plan, number
            cursor += len(steps)
        return None, None


    def _tier_step_has_hero(self, kind: str, team: int, level: int) -> bool:
        from wujiang.tactical.rooms.multiplayer import (
            hero_lookup,
        )
        for code, hero in hero_lookup().items():
            if int(hero["level"]) != int(level):
                continue
            if self._bp_hero_taken_error(code, team, kind) is None:
                return True
        return False


    def _drop_stuck_tier_steps(self) -> None:
        """当前这一步已经没有武将可选时，把这一步拿掉，避免整局卡死。"""
        from wujiang.tactical.rooms.multiplayer import (
            BP_FORMAT_TIER,
        )
        if self.bp_format != BP_FORMAT_TIER or self.status != "bp":
            return
        changed = False
        for _ in range(64):
            index = len(self.bp_actions)
            sequence = self._bp_sequence()
            if index >= len(sequence):
                break
            kind, team = sequence[index]
            round_plan, _number = self._bp_tier_round_at(index)
            if round_plan is None:
                break
            if self._tier_step_has_hero(kind, team, int(round_plan["level"])):
                break
            if "steps" not in round_plan:
                round_plan["steps"] = self._tier_round_steps(round_plan)
            cursor = 0
            for earlier in self.bp_tier_plan:
                if earlier is round_plan:
                    break
                cursor += len(self._tier_round_steps(earlier))
            offset = index - cursor
            steps = round_plan.get("steps") or []
            if offset < 0 or offset >= len(steps):
                break
            del steps[offset]
            changed = True
        if changed:
            self._bp_legal_cache = None
            self.touch()


    def _bp_hero_taken_error(self, hero_code: str, team: int, kind: str) -> Optional[str]:
        actions = [action for action in self.bp_actions if action["hero_code"] == hero_code]
        if any(action["kind"] == "ban" for action in actions):
            return "该武将已经被禁用。"
        if any(action["kind"] == "pick" and action["team_id"] == team for action in actions):
            return "本队已经选过这名武将。"
        if kind == "ban" and any(action["kind"] == "pick" for action in actions):
            return "该武将已经被选走，不能禁用。"
        if any(action["kind"] == "pick" for action in actions) and not self.bp_allow_same_hero:
            return "该武将已经被选走。"
        return None


    def _bp_feasible_level_pairs(self) -> list[tuple[int, int]]:
        from wujiang.tactical.rooms.multiplayer import (
            Counter,
            combinations,
            hero_lookup,
        )
        known = hero_lookup()
        pool = self.bp_hero_pool or tuple(known)
        counts = Counter(int(known[code]["level"]) for code in pool)
        key = (self.bp_team_size, self._bp_level_cap(), tuple(sorted(counts.items())))
        cached = getattr(self, "_bp_pair_cache", None)
        if cached and cached[0] == key:
            return cached[1]
        pairs = [pair for pair in combinations(sorted(counts), 2)
                 if self._bp_draft_feasible([], banned_levels=pair)]
        self._bp_pair_cache = (key, pairs)
        return pairs


    def _bp_choice_error(self, kind: str, team: int, hero_code: str) -> Optional[str]:
        from wujiang.tactical.rooms.multiplayer import (
            BP_FORMAT_TIER,
            TEAM_IDS,
            hero_lookup,
            team_name,
        )
        known = hero_lookup()
        hero = known.get(hero_code)
        if hero is None:
            return "所选武将不存在。"
        if self.bp_hero_pool and hero_code not in self.bp_hero_pool:
            return "该武将不在本局武将池中。"
        level = int(hero["level"])
        if self.bp_format == BP_FORMAT_TIER:
            round_plan, _round_number = self._bp_tier_round_at(len(self.bp_actions))
            if round_plan is None:
                return "BP 已经结束。"
            if level != int(round_plan["level"]):
                return f"这一轮只能选择 {int(round_plan['level'])} 点的武将。"
            return self._bp_hero_taken_error(hero_code, team, kind)
        if level in self.bp_auto_banned_levels:
            return "该等级已被系统禁用。"
        if self.bp_allow_same_hero:
            taken = self._bp_hero_taken_error(hero_code, team, kind)
            if taken is not None:
                return taken
        elif any(action["hero_code"] == hero_code for action in self.bp_actions):
            return "该武将已经被禁用或选走。"
        if kind == "pick" and self._bp_total_level(team) + level > self._bp_level_cap():
            return f"{team_name(team)}选将总等级不能超过{self._bp_level_cap()}。"
        if kind == "ban" and not any(
            len(self._bp_picks(candidate_team)) < self.bp_team_size
            and self._bp_draft_feasible([*self.bp_actions, {"kind": "pick", "team_id": candidate_team,
                                                            "hero_code": hero_code, "level": level}])
            for candidate_team in TEAM_IDS
        ):
            return "该武将的等级选后无法补齐阵容，不能禁用。"
        candidate = {"kind": kind, "team_id": team, "hero_code": hero_code, "level": level}
        if not self._bp_draft_feasible([*self.bp_actions, candidate]):
            return "这次禁选会使双方无法在等级上限内补齐阵容，请换一名武将。"
        return None


    def _bp_legal_levels(self) -> list[int]:
        from wujiang.tactical.rooms.multiplayer import (
            hero_lookup,
        )
        sequence = self._bp_sequence()
        if self.status != "bp" or len(self.bp_actions) >= len(sequence):
            return []
        kind, team = sequence[len(self.bp_actions)]
        key = (self.bp_team_size, self._bp_level_cap(), self.bp_hero_pool, self.bp_auto_banned_levels,
               self.bp_first_pick_team, self.bp_format, bool(self.bp_allow_same_hero), kind, team,
               tuple((action["kind"], action["team_id"], action["hero_code"])
                     for action in self.bp_actions))
        cached = getattr(self, "_bp_legal_cache", None)
        if cached and cached[0] == key:
            return cached[1]
        used = {action["hero_code"] for action in self.bp_actions
                if not self.bp_allow_same_hero or action["kind"] == "ban" or action["team_id"] == team}
        representative = {}
        for code, hero in hero_lookup().items():
            level = int(hero["level"])
            if (not self.bp_hero_pool or code in self.bp_hero_pool) and code not in used and level not in self.bp_auto_banned_levels:
                representative.setdefault(level, code)
        legal = [level for level, code in sorted(representative.items())
                 if self._bp_choice_error(kind, team, code) is None]
        self._bp_legal_cache = (key, legal)
        return legal


    def _bp_phase(self) -> str:
        if self.status != "bp":
            return "closed"
        if len(self.bp_actions) < len(self._bp_sequence()):
            return "draft"
        if not self.bp_assign_opened:
            return "review"
        return "assign"


    def _roll_bp_special_modes(self) -> tuple[str, ...]:
        from wujiang.tactical.rooms.multiplayer import (
            RoomError,
            SPECIAL_MODE_BY_CODE,
            choose_bp_special_modes,
        )
        if self.bp_rule_mode == "fixed":
            code = self.bp_fixed_rule if self.bp_fixed_rule in SPECIAL_MODE_BY_CODE else "none"
            return choose_bp_special_modes((code,))
        pool = tuple(self.bp_rule_pool)
        if not pool:
            raise RoomError("请至少勾选一条可随机的规则。")
        try:
            return choose_bp_special_modes(pool)
        except ValueError as exc:
            raise RoomError("请至少勾选一条可随机的规则。") from exc


    def _begin_bp(self) -> None:
        from wujiang.tactical.rooms.multiplayer import (
            BP_FORMAT_TIER,
            RoomError,
            TEAM_IDS,
            draw_bp_hero_pool,
            hero_lookup,
            secrets,
        )
        if self.bp_format == BP_FORMAT_TIER:
            self._begin_tier_bp()
            return
        known = hero_lookup()
        self.bp_hero_pool = draw_bp_hero_pool(known)
        pairs = self._bp_feasible_level_pairs()
        if not pairs:
            self.bp_hero_pool = ()
            raise RoomError("当前可用武将无法组成满足等级限制的66人武将池。")
        self.bp_auto_banned_levels = secrets.choice(pairs)
        self.bp_special_modes = self._roll_bp_special_modes()
        self.bp_first_pick_team = secrets.choice(TEAM_IDS)
        self.bp_actions = []
        self.bp_assignments = {}
        self.bp_assign_opened = False
        self.bp_team_ai = {1: True, 2: True}
        self.bp_tier_plan = []
        self.status = "bp"


    def _begin_tier_bp(self) -> None:
        from wujiang.tactical.rooms.multiplayer import (
            RoomError,
            TEAM_IDS,
            secrets,
        )
        if not self._tier_pool_can_start():
            raise RoomError("勾选的等级里没有足够的武将，双方无法各选到一名。")
        self.bp_first_pick_team = secrets.choice(list(TEAM_IDS))
        plan = self._plan_tier_rounds()
        if not plan:
            self.bp_first_pick_team = None
            raise RoomError("勾选的等级里没有足够的武将，双方无法各选到一名。")
        self.bp_tier_plan = plan
        self.bp_auto_banned_levels = ()
        self.bp_hero_pool = ()
        self.bp_special_modes = self._roll_bp_special_modes()
        self.bp_actions = []
        self.bp_assignments = {}
        self.bp_assign_opened = False
        self.bp_team_ai = {1: True, 2: True}
        self.status = "bp"


    def open_bp_assign(self, token: str) -> None:
        from wujiang.tactical.rooms.multiplayer import (
            RoomError,
        )
        with self._lock:
            self.require_host(token)
            if self.mode != "bp" or self._bp_phase() != "review":
                raise RoomError("禁选结束后，才能进入分配。")
            self.bp_assign_opened = True
            self.touch()


    def set_bp_team_ai(self, token: str, team_id: Any, enabled: Any) -> None:
        from wujiang.tactical.rooms.multiplayer import (
            RoomError,
            normalize_team_id,
        )
        with self._lock:
            seat = self.require_seat(token)
            if self.mode != "bp" or self._bp_phase() != "assign":
                raise RoomError("进入分配后才能选择是否交给 AI。")
            team = normalize_team_id(team_id)
            if not isinstance(enabled, bool):
                raise RoomError("AI 控制设置无效。")
            if seat.team_id != team and seat.player_id != self.host_player_id:
                raise RoomError("只能设置自己这一侧。")
            if self.bp_team_ai.get(team) == enabled:
                return
            self.bp_team_ai[team] = enabled
            self.touch()


    def bp_choose(self, token: str, hero_code: str) -> None:
        from wujiang.tactical.rooms.multiplayer import (
            RoomError,
            hero_lookup,
        )
        with self._lock:
            seat = self.require_seat(token)
            if self.mode != "bp" or self.status != "bp":
                raise RoomError("当前不在BP禁选阶段。")
            self._drop_stuck_tier_steps()
            if self._bp_phase() != "draft":
                raise RoomError("这一步已经没有可选武将。")
            kind, team = self._bp_sequence()[len(self.bp_actions)]
            if self.bp_captains.get(team) != seat.player_id:
                raise RoomError("当前轮到另一队队长操作。")
            error = self._bp_choice_error(kind, team, hero_code)
            if error is not None:
                raise RoomError(error)
            level = int(hero_lookup()[hero_code]["level"])
            candidate = {"kind": kind, "team_id": team, "hero_code": hero_code, "level": level}
            self.bp_actions.append(candidate)
            self.touch()


    def bp_assign(self, token: str, hero_code: str, controller: Any) -> None:
        from wujiang.tactical.rooms.multiplayer import (
            RoomError,
        )
        with self._lock:
            seat = self.require_seat(token)
            if self.mode != "bp" or self._bp_phase() != "assign":
                raise RoomError("禁选结束后才能分配武将。")
            if not seat.is_human or hero_code not in self._bp_picks(seat.team_id):
                raise RoomError("只能分配本队选中的武将。")
            previous = self.bp_assignments.get(hero_code)
            captain = self.bp_captains.get(seat.team_id) == seat.player_id
            if previous is not None and previous not in (seat.player_id, "ai") and not captain:
                raise RoomError("该武将已被分配；只有原控制者或队长可以调整。")
            if controller == "ai":
                self.bp_assignments[hero_code] = "ai"
            else:
                try:
                    target = self._seat(int(controller))
                except (TypeError, ValueError) as exc:
                    raise RoomError("控制席位无效。") from exc
                if not target.is_human or target.team_id != seat.team_id:
                    raise RoomError("只能交给本队真人玩家，或指定AI。")
                if target.player_id != seat.player_id and not captain:
                    raise RoomError("只有队长可以代其他队员分配武将。")
                self.bp_assignments[hero_code] = target.player_id
            self.touch()


    def _bp_assignment_blocker(self) -> Optional[str]:
        phase = self._bp_phase()
        if phase == "review":
            return "请房主确认后再进入分配。"
        if phase != "assign":
            return "请先完成BP禁选。"
        return None


    def _bp_ai_seat(self, team: int, ai_seats: dict[int, Any]) -> Any:
        from wujiang.tactical.rooms.multiplayer import (
            PlayerSeat,
        )
        if team in ai_seats:
            return ai_seats[team]
        existing = next((seat for seat in self._team_seats(team) if seat.is_ai), None)
        if existing is not None:
            ai_seats[team] = existing
            return existing
        seat_id = max(self.seats) + 1
        virtual = PlayerSeat(player_id=seat_id, team_id=team)
        virtual.set_ai()
        self.seats[seat_id] = virtual
        self.bp_virtual_seat_ids.append(seat_id)
        ai_seats[team] = virtual
        return virtual


    def _materialize_bp_assignments(self) -> None:
        from wujiang.tactical.rooms.multiplayer import (
            TEAM_IDS,
        )
        for seat in self.seats.values():
            seat.clear_roster()
        ai_seats: dict[int, Any] = {}
        for team in TEAM_IDS:
            human = next((seat for seat in self._team_seats(team) if seat.is_human), None)
            use_ai = bool(self.bp_team_ai.get(team)) or human is None
            target = self._bp_ai_seat(team, ai_seats) if use_ai else human
            for code in self._bp_picks(team):
                target.adjust_hero_count(code, 1)


    def _bp_public_state(self) -> dict[str, Any]:
        from wujiang.tactical.rooms.multiplayer import (
            BP_FORMAT_NAMES,
            BP_FORMAT_TIER,
            Counter,
            TEAM_IDS,
            hero_lookup,
            public_special_modes,
            special_mode_catalog,
        )
        self._drop_stuck_tier_steps()
        sequence = self._bp_sequence()
        index = len(self.bp_actions)
        step = sequence[index] if index < len(sequence) else None
        known = hero_lookup()
        round_plan, round_number = self._bp_tier_round_at(index) if self.bp_format == BP_FORMAT_TIER else (None, None)
        return {
            "team_size": self.bp_team_size,
            "level_cap": self._bp_level_cap(),
            "level_caps": dict(self.bp_level_caps),
            "show_win_count": self.bp_show_win_count,
            "wins": dict(self.bp_wins) if self.bp_show_win_count else None,
            "total_levels": {team: self._bp_total_level(team) for team in TEAM_IDS},
            "captains": dict(self.bp_captains),
            "format": self.bp_format,
            "format_name": BP_FORMAT_NAMES[self.bp_format],
            "allow_same_hero": bool(self.bp_allow_same_hero),
            "tier_pool": list(self.bp_tier_pool),
            "tier_rounds": int(self.bp_tier_rounds),
            "round_index": round_number,
            "round_count": sum(1 for item in self.bp_tier_plan if self._tier_round_steps(item)),
            "current_level": None if round_plan is None else int(round_plan["level"]),
            "round_bans": None if round_plan is None else int(round_plan["bans"]),
            "round_picks": None if round_plan is None else int(round_plan["picks"]),
            "phase": self._bp_phase(),
            "first_pick_team": self.bp_first_pick_team,
            "auto_banned_levels": list(self.bp_auto_banned_levels),
            "hero_pool_codes": list(self.bp_hero_pool),
            "hero_pool_levels": dict(sorted(Counter(int(known[code]["level"]) for code in self.bp_hero_pool).items())),
            "special_modes": public_special_modes(self.bp_special_modes),
            "rule_mode": self.bp_rule_mode,
            "fixed_rule": self.bp_fixed_rule,
            "rule_pool": list(self.bp_rule_pool),
            "rule_catalog": special_mode_catalog(),
            "legal_levels": self._bp_legal_levels(),
            "step_index": index,
            "step_count": len(sequence),
            "current_kind": step[0] if step else None,
            "current_team_id": step[1] if step else None,
            "actions": [{**action,
                         "hero_name": known.get(action["hero_code"], {}).get("name", action["hero_code"]),
                         "hero_level": action.get("level", known.get(action["hero_code"], {}).get("level", 0))}
                        for action in self.bp_actions],
            "picks": {team: self._bp_picks(team) for team in TEAM_IDS},
            "assignments": dict(self.bp_assignments),
            "team_ai": {team: bool(self.bp_team_ai.get(team)) for team in TEAM_IDS},
        }


