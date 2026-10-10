"""Server-authoritative auto chess economy and tournament state.

Only the published hero catalog and the player supplied equipment workbook are used.
"""
from __future__ import annotations

import random
import secrets
import time
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Any

from wujiang.tactical.engine.core import Battle, Position
from wujiang.tactical.heroes.registry import (
    create_hero, entry_footprint_offsets, interleaved_classic_turn_order,
    list_heroes, sort_units_for_classic,
)
from wujiang.tactical.rooms.equipment import load_equipment_catalog
from wujiang.tactical.rooms.equipment_effects import EquipmentStatus
from wujiang.tactical.rooms.synergy import CATEGORY_LABELS, SynergyStatus, synergy_counts, synergy_description


@dataclass(frozen=True)
class AutoChessConfig:
    initial_health: int = 30
    initial_gold: int = 10
    base_income: int = 5
    interest_step: int = 10
    interest_cap: int = 5
    preparation_seconds: int = 90
    bench_capacity: int = 8
    shop_slots: int = 5
    reroll_cost: int = 2
    equipment_draw_cost: int = 5
    equipment_slots: int = 3
    board_width: int = 10
    board_height: int = 10
    deployment_width: int = 4
    battle_round_limit: int = 30
    level_budgets: tuple[int, ...] = (6, 9, 12, 15, 18, 21, 24, 27)
    level_costs: tuple[int, ...] = (4, 6, 8, 10, 12, 14, 16)
    shop_weights: tuple[tuple[int, ...], ...] = (
        (70, 25, 5, 0, 0), (55, 30, 13, 2, 0),
        (40, 32, 20, 7, 1), (28, 30, 25, 14, 3),
        (18, 26, 28, 21, 7), (10, 21, 28, 28, 13),
        (5, 15, 25, 32, 23), (2, 9, 19, 35, 35),
    )


CONFIG = AutoChessConfig()
STATS = ("attack", "defense", "speed", "attack_range", "mana")
STAT_LABELS = {"attack": "攻击", "defense": "防御", "speed": "速度", "attack_range": "范围", "mana": "魔法"}
EQUIPMENT_CATALOG: dict[str, dict[str, Any]] = load_equipment_catalog()


def _error(message: str) -> None:
    from wujiang.tactical.rooms.multiplayer import RoomError
    raise RoomError(message)


@lru_cache(maxsize=1)
def hero_catalog() -> dict[str, dict[str, Any]]:
    return {str(hero["code"]): hero for hero in list_heroes()}


@lru_cache(maxsize=None)
def _footprint(code: str) -> tuple[tuple[int, int], ...]:
    return tuple(entry_footprint_offsets(create_hero(code, 1)))


@lru_cache(maxsize=None)
def _entry_companion(code: str) -> str:
    unit = create_hero(code, 1)
    return next((trait.description for trait in unit.traits
                 if trait.name == "骑士开场坐骑"), "")


def _hero_preview(hero: dict[str, Any]) -> dict[str, Any]:
    preview = dict(hero)
    preview["synergies"] = [
        {"category": category, "category_name": label,
         "name": str(hero[category]),
         "first": synergy_description(category, str(hero[category]), 1),
         "second": synergy_description(category, str(hero[category]), 2)}
        for category, label in CATEGORY_LABELS.items()
    ]
    preview["entry_companion"] = _entry_companion(str(hero["code"]))
    return preview


def hero_cost(level: int) -> int:
    return max(1, min(5, (int(level) + 1) // 2))


def live_units(battle: Battle, side: int) -> list[Any]:
    battle.cleanup_dead_units()
    return [unit for unit in battle.all_units()
            if unit.player_id == side and unit.alive and not unit.banished and unit.position is not None]


def battle_score(battle: Battle, side: int) -> tuple[int, float, float]:
    units = live_units(battle, side)
    damage = sum(float(event.get("amount") or 0) for event in battle.summary_events
                 if event.get("kind") == "damage" and event.get("actor_player_id") == side
                 and event.get("target_player_id") == 3 - side)
    return len(units), round(sum(max(0, unit.current_hp) / max(unit.max_health, 0.001)
                                  for unit in units), 6), round(damage, 6)


def chess_timeout_result(battle: Battle) -> dict[str, Any]:
    """Compare only living, fielded bodies after turn-end cleanup."""
    left, right = battle_score(battle, 1), battle_score(battle, 2)
    if left[0] != right[0]:
        criterion = "units"
    elif left[1] != right[1]:
        criterion = "health_ratio"
    elif left[2] != right[2]:
        criterion = "enemy_damage"
    else:
        criterion = "seeded_coin"
    winner = (1 if left > right else 2 if right > left else
              int(getattr(battle, "chess_tie_winner", 1)))
    damage = abs(left[0] - right[0]) if criterion == "units" else 1
    return {"winner": winner, "damage": max(1, damage), "criterion": criterion,
            "scores": {1: left, 2: right}}


def battle_observer_snapshot(battle: Battle) -> dict[str, Any]:
    """Only the fields used by the read-only chess board; avoid action previews."""
    units = []
    for unit in battle.all_units():
        if not unit.alive or unit.position is None or unit.banished:
            continue
        units.append({"id": unit.unit_id, "name": unit.name, "player_id": unit.player_id,
                      "position": unit.position.to_dict(), "alive": True, "banished": False,
                      "occupied_cells": [cell.to_dict() for cell in battle.unit_cells(unit)],
                      "hp": unit.current_hp, "max_hp": unit.max_health,
                      "is_mount": unit.is_mount})
    return {"board": {"width": battle.width, "height": battle.height},
            "round_number": battle.round_number, "winner": battle.winner,
            "units": units, "logs": battle.logs[-12:]}


@dataclass
class ChessPiece:
    id: str
    code: str
    star: int = 1
    upgrades: dict[str, int] = field(default_factory=dict)
    pending_upgrades: int = 0
    x: int | None = None
    y: int | None = None
    equipment: list[str] = field(default_factory=list)

    def public(self, catalog: dict[str, dict[str, Any]]) -> dict[str, Any]:
        hero = catalog[self.code]
        return {"id": self.id, "code": self.code, "name": hero["name"],
                "level": int(hero["level"]), "cost": hero_cost(hero["level"]),
                "star": self.star, "upgrades": dict(self.upgrades),
                "pending_upgrades": self.pending_upgrades, "x": self.x, "y": self.y,
                "equipment": list(self.equipment),
                "footprint": _footprint(self.code),
                "entry_companion": _entry_companion(self.code)}


@dataclass
class ChessPlayer:
    seat_id: int
    health: int = CONFIG.initial_health
    gold: int = CONFIG.initial_gold
    level: int = 1
    wins: int = 0
    losses: int = 0
    win_streak: int = 0
    loss_streak: int = 0
    ready: bool = False
    eliminated: bool = False
    pieces: list[ChessPiece] = field(default_factory=list)
    shop: list[str | None] = field(default_factory=list)
    equipment_inventory: list[str] = field(default_factory=list)
    equipment_drawn_round: int = 0

    def fielded(self) -> list[ChessPiece]:
        return [piece for piece in self.pieces if piece.x is not None and piece.y is not None]


@dataclass
class ChessMatch:
    left: int
    right: int
    mirror: bool = False
    runner: Any = None
    result: dict[str, Any] | None = None


class AutoChessState:
    def __init__(self, seat_count: int, *, seed: int | None = None) -> None:
        if seat_count not in (4, 8):
            _error("自走棋只支持 4 人或 8 人。")
        self.config = CONFIG
        self.rng = random.Random(seed if seed is not None else secrets.randbits(64))
        self.seat_count = seat_count
        self.players = {seat_id: ChessPlayer(seat_id) for seat_id in range(1, seat_count + 1)}
        self.round_number = 0
        self.phase = "lobby"
        self.deadline_at: float | None = None
        self.matches: list[ChessMatch] = []
        self.history: list[dict[str, Any]] = []
        self.champion: int | None = None
        self.last_mirror: int | None = None
        self.next_match_cursor = 0
        self.version = 0

    def _change(self) -> None:
        self.version += 1

    def _living(self) -> list[int]:
        return [seat_id for seat_id, player in self.players.items() if not player.eliminated]

    def _player(self, seat_id: int) -> ChessPlayer:
        player = self.players.get(seat_id)
        if player is None or player.eliminated:
            _error("该玩家已淘汰或不存在。")
        return player

    def _preparing(self, seat_id: int) -> ChessPlayer:
        if self.phase != "preparation":
            _error("当前不在备战阶段。")
        player = self._player(seat_id)
        if player.ready:
            _error("本轮已确认，不能继续更改。")
        return player

    def _draw_shop(self, level: int) -> list[str]:
        catalog = hero_catalog()
        by_tier: list[list[str]] = [[] for _ in range(5)]
        for code, hero in catalog.items():
            by_tier[hero_cost(int(hero["level"])) - 1].append(code)
        weights = [weight if by_tier[index] else 0
                   for index, weight in enumerate(self.config.shop_weights[level - 1])]
        if not any(weights):
            _error("当前没有可出售的公开武将。")
        tiers = self.rng.choices(range(5), weights=weights, k=self.config.shop_slots)
        return [self.rng.choice(by_tier[tier]) for tier in tiers]

    def start(self) -> None:
        if self.phase != "lobby":
            _error("自走棋已经开局。")
        self._begin_round(initial=True)

    def _begin_round(self, *, initial: bool = False) -> None:
        living = self._living()
        if len(living) <= 1:
            self.phase = "finished"
            self.champion = living[0] if living else None
            self._change()
            return
        self.round_number += 1
        self.rng.shuffle(living)
        self.matches = [ChessMatch(living[index], living[index + 1])
                        for index in range(0, len(living) - 1, 2)]
        if len(living) % 2:
            candidate = [seat_id for seat_id in living[:-1] if seat_id != self.last_mirror]
            mirror_id = self.rng.choice(candidate or living[:-1])
            self.last_mirror = mirror_id
            self.matches.append(ChessMatch(living[-1], mirror_id, mirror=True))
        for seat_id in living:
            player = self.players[seat_id]
            if not initial:
                player.gold += self.config.base_income + min(
                    self.config.interest_cap, player.gold // self.config.interest_step)
            player.ready = False
            player.shop = self._draw_shop(player.level)
        self.phase = "preparation"
        self.deadline_at = time.time() + self.config.preparation_seconds
        self._change()

    def _piece(self, player: ChessPlayer, piece_id: str) -> ChessPiece:
        piece = next((piece for piece in player.pieces if piece.id == piece_id), None)
        if piece is None:
            _error("该武将不在你的阵容中。")
        return piece

    def _budget_used(self, player: ChessPlayer) -> int:
        catalog = hero_catalog()
        return sum(int(catalog[piece.code]["level"]) for piece in player.fielded())

    def _roster_capacity(self, player: ChessPlayer, budget: int) -> int:
        levels = sorted(int(hero_catalog()[piece.code]["level"]) for piece in player.pieces)
        used = count = 0
        for level in levels:
            if used + level > budget:
                break
            used += level
            count += 1
        return count

    def _can_place(self, player: ChessPlayer, piece: ChessPiece, x: int, y: int) -> bool:
        catalog = hero_catalog()
        used = self._budget_used(player) - (int(catalog[piece.code]["level"]) if piece.x is not None else 0)
        if used + int(catalog[piece.code]["level"]) > self.config.level_budgets[player.level - 1]:
            return False
        occupied: set[tuple[int, int]] = set()
        for other in player.fielded():
            if other.id == piece.id:
                continue
            for dx, dy in _footprint(other.code):
                occupied.add((int(other.x) + dx, int(other.y) + dy))
        for dx, dy in _footprint(piece.code):
            cell = x + dx, y + dy
            if not (0 <= cell[0] < self.config.deployment_width and
                    0 <= cell[1] < self.config.board_height) or cell in occupied:
                return False
        return True

    def buy(self, seat_id: int, slot: int, target_piece_id: str | None = None) -> None:
        player = self._preparing(seat_id)
        if not 0 <= slot < len(player.shop) or player.shop[slot] is None:
            _error("商店格无可购买武将。")
        code = str(player.shop[slot])
        price = hero_cost(hero_catalog()[code]["level"])
        if player.gold < price:
            _error("金币不足。")
        owned = [piece for piece in player.pieces if piece.code == code]
        if target_piece_id:
            target = self._piece(player, target_piece_id)
            if target.code != code:
                _error("所选强化目标与商店武将不同。")
        else:
            target = min(owned, key=lambda piece: (piece.x is None, -piece.star)) if owned else None
        bench_full = len([piece for piece in player.pieces if piece.x is None]) >= self.config.bench_capacity
        if bench_full and target is None:
            _error("后备席已满，请先上场或出售武将。")
        player.gold -= price
        player.shop[slot] = None
        if target is None:
            player.pieces.append(ChessPiece(secrets.token_hex(5), code))
        else:
            target.star = min(3, target.star + 1)
            target.pending_upgrades += 1
        self._change()

    def reroll(self, seat_id: int) -> None:
        player = self._preparing(seat_id)
        if player.gold < self.config.reroll_cost:
            _error("金币不足。")
        player.gold -= self.config.reroll_cost
        player.shop = self._draw_shop(player.level)
        self._change()

    def level_up(self, seat_id: int) -> None:
        player = self._preparing(seat_id)
        if player.level >= len(self.config.level_budgets):
            _error("玩家等级已达上限。")
        cost = self.config.level_costs[player.level - 1]
        if player.gold < cost:
            _error("金币不足。")
        player.gold -= cost
        player.level += 1
        self._change()

    def choose_upgrade(self, seat_id: int, piece_id: str, stat: str) -> None:
        player = self._preparing(seat_id)
        piece = self._piece(player, piece_id)
        if piece.pending_upgrades <= 0 or stat not in STATS:
            _error("该能力值升级选择无效。")
        piece.pending_upgrades -= 1
        piece.upgrades[stat] = piece.upgrades.get(stat, 0) + 1
        self._change()

    def place(self, seat_id: int, piece_id: str, x: int | None, y: int | None) -> None:
        player = self._preparing(seat_id)
        piece = self._piece(player, piece_id)
        if x is None and y is None:
            if piece.x is not None and len([item for item in player.pieces if item.x is None]) >= self.config.bench_capacity:
                _error("后备席已满，不能撤回。")
            piece.x = piece.y = None
        elif x is not None and y is not None and self._can_place(player, piece, int(x), int(y)):
            piece.x, piece.y = int(x), int(y)
        else:
            _error("放置位置超出己侧、与其他武将重叠或超出总等级预算。")
        self._change()

    def sell(self, seat_id: int, piece_id: str) -> None:
        player = self._preparing(seat_id)
        piece = self._piece(player, piece_id)
        player.gold += (hero_cost(hero_catalog()[piece.code]["level"]) + 1) // 2
        player.equipment_inventory.extend(piece.equipment)
        player.pieces.remove(piece)
        self._change()

    def draw_equipment(self, seat_id: int) -> None:
        player = self._preparing(seat_id)
        if not EQUIPMENT_CATALOG:
            _error("装备表无法读取，暂不能抽取装备；不会扣除金币。")
        if player.equipment_drawn_round == self.round_number:
            _error("每轮只能抽取一件装备。")
        if player.gold < self.config.equipment_draw_cost:
            _error("金币不足。")
        codes = list(EQUIPMENT_CATALOG)
        weights = [max(0, int(EQUIPMENT_CATALOG[code].get("weight", 1))) for code in codes]
        if not any(weights):
            _error("装备表没有可抽取项目。")
        code = self.rng.choices(codes, weights=weights, k=1)[0]
        player.gold -= self.config.equipment_draw_cost
        player.equipment_inventory.append(code)
        player.equipment_drawn_round = self.round_number
        self._change()

    def equip(self, seat_id: int, piece_id: str, code: str) -> None:
        player = self._preparing(seat_id)
        piece = self._piece(player, piece_id)
        if code not in EQUIPMENT_CATALOG or code not in player.equipment_inventory:
            _error("该装备不在背包中。")
        if len(piece.equipment) >= self.config.equipment_slots:
            _error("该武将的装备位已满。")
        allowed = EQUIPMENT_CATALOG[code].get("allowed_roles")
        if allowed and hero_catalog()[piece.code]["role"] not in allowed:
            _error("该装备不适用于此武将。")
        player.equipment_inventory.remove(code)
        piece.equipment.append(code)
        self._change()

    def unequip(self, seat_id: int, piece_id: str, code: str) -> None:
        player = self._preparing(seat_id)
        piece = self._piece(player, piece_id)
        if code not in piece.equipment:
            _error("该武将没有这件装备。")
        piece.equipment.remove(code)
        player.equipment_inventory.append(code)
        self._change()

    def ready(self, seat_id: int) -> None:
        player = self._preparing(seat_id)
        if any(piece.pending_upgrades for piece in player.pieces):
            _error("请先为强化武将选择能力值。")
        player.ready = True
        self._change()

    def _ai_prepare(self, seat_id: int) -> None:
        player = self._player(seat_id)
        if player.ready:
            return
        for piece in player.pieces:
            while piece.pending_upgrades:
                hero = hero_catalog()[piece.code]
                stat = max(("attack", "defense", "speed", "attack_range", "mana"),
                           key=lambda name: (float(hero["stats"][name]) / (1 + piece.upgrades.get(name, 0)),
                                             name == "attack"))
                self.choose_upgrade(seat_id, piece.id, stat)
        if player.level < 8 and player.gold >= self.config.level_costs[player.level - 1] + 12:
            self.level_up(seat_id)
        for slot, code in list(enumerate(player.shop)):
            if code is None:
                continue
            price = hero_cost(hero_catalog()[code]["level"])
            if player.gold - price < 5 and player.fielded():
                continue
            try:
                self.buy(seat_id, slot)
            except Exception:
                continue
        if player.gold >= 20 and len(player.pieces) < self.config.bench_capacity:
            self.reroll(seat_id)
            for slot, code in list(enumerate(player.shop)):
                if code and player.gold - hero_cost(hero_catalog()[code]["level"]) >= 10:
                    try:
                        self.buy(seat_id, slot)
                    except Exception:
                        continue
        if (EQUIPMENT_CATALOG and player.gold >= 18 and
                player.equipment_drawn_round != self.round_number):
            self.draw_equipment(seat_id)
        for piece in player.pieces:
            while piece.pending_upgrades:
                self.choose_upgrade(seat_id, piece.id, "attack")
        for piece in sorted(player.pieces, key=lambda item: (-item.star, hero_catalog()[item.code]["level"])):
            if piece.x is not None:
                continue
            for x in range(self.config.deployment_width - 1, -1, -1):
                for y in range(self.config.board_height):
                    if self._can_place(player, piece, x, y):
                        self.place(seat_id, piece.id, x, y)
                        break
                if piece.x is not None:
                    break
        for code in list(player.equipment_inventory):
            candidate = next((piece for piece in player.fielded()
                              if len(piece.equipment) < self.config.equipment_slots), None)
            if candidate is None:
                break
            self.equip(seat_id, candidate.id, code)
        self.ready(seat_id)

    def _create_match_battle(self, match: ChessMatch) -> Any:
        from wujiang.tactical.rooms.multiplayer import GameRoom
        left = self.players[match.left]
        right = self.players[match.right]
        battle = Battle(width=self.config.board_width, height=self.config.board_height)
        battle.chess_rng = random.Random(self.rng.randrange(2**64))
        units_by_side: dict[int, list[Any]] = {1: [], 2: []}
        catalog = hero_catalog()
        for side, player in ((1, left), (2, right)):
            active_synergies = [item for item in synergy_counts(player.fielded(), catalog) if item["tier"]]
            for piece in player.fielded():
                unit = create_hero(piece.code, side)
                unit.owner_seat_id = side
                for stat, amount in piece.upgrades.items():
                    setattr(unit.base_stats, stat, getattr(unit.base_stats, stat) + amount)
                # Printed stats come from the workbook; named effects are battle components.
                for code in piece.equipment:
                    item = EQUIPMENT_CATALOG.get(code)
                    if item is None:
                        continue
                    for stat, amount in item["stats"].items():
                        if stat in STATS:
                            setattr(unit.base_stats, stat, getattr(unit.base_stats, stat) + float(amount))
                        elif stat == "max_health":
                            unit.max_health += float(amount)
                            unit.current_hp += float(amount)
                    unit.add_status(EquipmentStatus(code, item["attributes"], item["effect"]))
                for synergy in active_synergies:
                    if str(catalog[piece.code][synergy["category"]]) == synergy["name"]:
                        unit.add_status(SynergyStatus(synergy["category"], synergy["name"], synergy["tier"]))
                unit.current_mana = unit.max_mana()
                x = int(piece.x) + (0 if side == 1 else self.config.board_width - self.config.deployment_width)
                battle.add_unit(unit, Position(x, int(piece.y)))
                units_by_side[side].append(unit)
        if not units_by_side[1] or not units_by_side[2]:
            return None
        sorted_left = sort_units_for_classic(units_by_side[1])
        sorted_right = sort_units_for_classic(units_by_side[2])
        turn_order = interleaved_classic_turn_order(sorted_left, sorted_right)
        battle.configure_turn_order([unit.unit_id for unit in turn_order])
        battle.turn_timeout_limit = 100000
        battle.chess_round_cap = self.config.battle_round_limit
        battle.chess_tie_winner = self.rng.choice((1, 2))
        battle.start_battle()
        runner = GameRoom(f"{match.left}{match.right}{self.round_number:04d}", seat_count=2)
        for seat in runner.seats.values():
            seat.set_ai()
        runner.battle = battle
        runner.status = "battle"
        runner.fast_ai_simulation = False
        return runner

    def _begin_battles(self) -> None:
        self.phase = "battle"
        self.deadline_at = None
        self.next_match_cursor = 0
        self._change()

    def _advance_match(self, match: ChessMatch) -> None:
        if match.runner is None:
            match.runner = self._create_match_battle(match)
            if match.runner is None:
                left_count = len(self.players[match.left].fielded())
                right_count = len(self.players[match.right].fielded())
                side = (1 if left_count > right_count else 2 if right_count > left_count
                        else self.rng.choice((1, 2)))
                match.result = {"winner": side, "damage": max(1, left_count if side == 1 else right_count),
                                "criterion": "empty_board", "scores": {}}
            self._change()
            return
        steps = match.runner.resolve_ai_until_human_input(max_steps=1)
        if steps:
            self._change()
        if match.runner.battle.winner is not None:
            self._settle_match(match)
            self._change()

    def _settle_match(self, match: ChessMatch) -> None:
        battle = match.runner.battle if match.runner else None
        if battle is None:
            return
        battle.cleanup_dead_units()
        timeout = getattr(battle, "chess_timeout_result", None)
        if timeout:
            match.result = timeout
        else:
            winner = int(battle.winner)
            match.result = {"winner": winner, "damage": max(1, len(live_units(battle, winner))),
                            "criterion": battle.win_reason_code or "normal",
                            "scores": {1: battle_score(battle, 1), 2: battle_score(battle, 2)}}
        defeated_heroes = sum(1 for event in battle.summary_events
                              if event.get("kind") == "defeat" and
                              event.get("target_player_id") == 3 - match.result["winner"])
        match.result["hero_kills"] = defeated_heroes

    def _finish_round(self) -> None:
        outcomes: dict[int, list[dict[str, Any]]] = {seat_id: [] for seat_id in self._living()}
        for match in self.matches:
            result = match.result or {}
            winner_side = int(result["winner"])
            winner_id = match.left if winner_side == 1 else match.right
            loser_id = match.right if winner_side == 1 else match.left
            if not match.mirror or winner_id == match.left:
                outcomes[winner_id].append({"win": True, "kills": int(result.get("hero_kills", 0)), "damage": 0})
            if not match.mirror or loser_id == match.left:
                outcomes[loser_id].append({"win": False, "kills": 0, "damage": int(result["damage"])})
        round_record = {"round": self.round_number, "matches": [], "players": {}}
        for match in self.matches:
            round_record["matches"].append({"left": match.left, "right": match.right,
                                            "mirror": match.mirror, **(match.result or {})})
        before_hp = {seat_id: self.players[seat_id].health for seat_id in outcomes}
        for seat_id, player_outcomes in outcomes.items():
            player = self.players[seat_id]
            primary = player_outcomes[0] if player_outcomes else {"win": True, "kills": 0, "damage": 0}
            if primary["win"]:
                player.wins += 1
                player.win_streak += 1
                player.loss_streak = 0
                gold_gain = 1 + primary["kills"] + min(3, player.win_streak)
            else:
                player.losses += 1
                player.loss_streak += 1
                player.win_streak = 0
                gold_gain = min(3, player.loss_streak)
            # The workbook uses 100 base coins per shop gold.  Point Gold's
            # die therefore awards 1..player-count auto chess gold per copy.
            item_gold = sum(self.rng.randint(1, self.seat_count)
                            for piece in player.fielded()
                            for code in piece.equipment if code == "点金手")
            gold_gain += item_gold
            player.gold += gold_gain
            health_loss = sum(item["damage"] for item in player_outcomes)
            player.health -= health_loss
            round_record["players"][seat_id] = {"gold_gain": gold_gain, "item_gold": item_gold,
                                                "health_loss": health_loss,
                                                "health": max(0, player.health)}
        survivors = [seat_id for seat_id in outcomes if self.players[seat_id].health > 0]
        if not survivors:
            survivors = [max(outcomes, key=lambda seat_id: (before_hp[seat_id],
                         self.players[seat_id].wins, self.rng.random()))]
            self.players[survivors[0]].health = 1
        for seat_id in outcomes:
            self.players[seat_id].eliminated = seat_id not in survivors
        self.history.append(round_record)
        if len(survivors) == 1:
            self.champion = survivors[0]
            self.phase = "finished"
        else:
            self._begin_round()
        self._change()

    def tick(self, seats: dict[int, Any], *, now: float | None = None,
             action_budget: int = 2, prepare_budget: int = 1) -> bool:
        before = self.version
        if self.phase == "preparation":
            due = (now if now is not None else time.time()) >= (self.deadline_at or float("inf"))
            prepared = 0
            for seat_id in self._living():
                if prepared >= prepare_budget:
                    break
                if not self.players[seat_id].ready and (due or seats[seat_id].is_ai):
                    self._ai_prepare(seat_id)
                    prepared += 1
            if all(self.players[seat_id].ready for seat_id in self._living()):
                self._begin_battles()
        if self.phase == "battle" and action_budget > 0:
            for _ in range(action_budget):
                unfinished = [match for match in self.matches if match.result is None]
                if not unfinished:
                    break
                index = getattr(self, "next_match_cursor", 0) % len(self.matches)
                for offset in range(len(self.matches)):
                    candidate = self.matches[(index + offset) % len(self.matches)]
                    if candidate.result is None:
                        match = candidate
                        self.next_match_cursor = (index + offset + 1) % len(self.matches)
                        break
                self._advance_match(match)
            if all(match.result is not None for match in self.matches):
                self._finish_round()
        return self.version != before

    def public(self, viewer_id: int | None, seats: dict[int, Any]) -> dict[str, Any]:
        catalog = hero_catalog()
        viewer = self.players.get(viewer_id) if viewer_id is not None else None
        visible_codes = ({piece.code for piece in viewer.pieces} |
                         {code for code in viewer.shop if code}) if viewer else set()
        players = []
        for seat_id, player in self.players.items():
            seat = seats[seat_id]
            public = {"seat_id": seat_id, "name": seat.name, "is_ai": seat.is_ai,
                      "health": max(0, player.health), "gold": player.gold if viewer_id == seat_id else None,
                      "level": player.level, "wins": player.wins, "losses": player.losses,
                      "win_streak": player.win_streak, "loss_streak": player.loss_streak,
                      "ready": player.ready, "eliminated": player.eliminated,
                      "fielded": [piece.public(catalog) for piece in player.fielded()],
                      "synergies": synergy_counts(player.fielded(), catalog)}
            if viewer_id == seat_id:
                public.update({"pieces": [piece.public(catalog) for piece in player.pieces],
                               "shop": [{"slot": index, **({"code": code, "name": catalog[code]["name"],
                                          "level": catalog[code]["level"], "cost": hero_cost(catalog[code]["level"])}
                                          if code else {})} for index, code in enumerate(player.shop)],
                               "equipment_inventory": list(player.equipment_inventory),
                               "equipment_drawn_this_round": player.equipment_drawn_round == self.round_number,
                               "budget": self.config.level_budgets[player.level - 1],
                               "budget_used": self._budget_used(player),
                               "roster_capacity": self._roster_capacity(player, self.config.level_budgets[player.level - 1]),
                               "next_roster_capacity": (self._roster_capacity(player, self.config.level_budgets[player.level])
                                                        if player.level < len(self.config.level_budgets) else None)})
            players.append(public)
        battles = []
        for index, match in enumerate(self.matches):
            battle = match.runner.battle if match.runner else None
            battles.append({"index": index, "left": match.left, "right": match.right,
                            "mirror": match.mirror, "result": match.result,
                            "battle": battle_observer_snapshot(battle) if battle is not None else None})
        return {"phase": self.phase, "round": self.round_number, "deadline_at": self.deadline_at,
                "server_time": time.time(),
                "seat_count": self.seat_count, "players": players, "matches": battles,
                "hero_previews": {code: _hero_preview(catalog[code]) for code in visible_codes},
                "history": self.history[-8:], "champion": self.champion,
                "viewer_id": viewer_id, "version": self.version,
                "equipment_available": bool(EQUIPMENT_CATALOG),
                "equipment_catalog": [{"code": code, "name": item["name"], "attributes": item["attributes"],
                                       "effect": item["effect"]} for code, item in EQUIPMENT_CATALOG.items()],
                "config": {"preparation_seconds": self.config.preparation_seconds,
                           "level_costs": self.config.level_costs,
                           "level_budgets": self.config.level_budgets,
                           "reroll_cost": self.config.reroll_cost,
                           "equipment_draw_cost": self.config.equipment_draw_cost,
                           "equipment_slots": self.config.equipment_slots,
                           "bench_capacity": self.config.bench_capacity,
                           "battle_round_limit": self.config.battle_round_limit}}
