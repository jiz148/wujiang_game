from __future__ import annotations

import json
import os
import secrets
import sqlite3
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass
from http import HTTPStatus
from pathlib import Path
from typing import Any, Iterable

from wujiang.strategy.battles import resolve_battle_room_result
from wujiang.strategy.command import faction_command_points, strategy_action_command_cost
from wujiang.strategy.generation import generate_random_world
from wujiang.strategy.models import CampaignMember, StrategyError, WorldState
from wujiang.web.auth import AuthUser, DEFAULT_AUTH_DB_PATH


DEFAULT_STRATEGY_DB_PATH = DEFAULT_AUTH_DB_PATH
PRESENCE_TTL_SECONDS = 60 * 5
JOIN_CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
JOIN_CODE_LENGTH = 6
AI_MEMBER_ROLE = "ai"


def strategy_database_path(raw_path: str | None = None) -> Path:
    configured = str(raw_path or os.environ.get("WUJIANG_STRATEGY_DB") or "").strip()
    return Path(configured).expanduser() if configured else DEFAULT_STRATEGY_DB_PATH


def _faction_index(faction_id: str) -> int:
    try:
        parsed = int(str(faction_id).rsplit("_", 1)[-1])
    except (TypeError, ValueError):
        parsed = 0
    return max(1, parsed)


@dataclass(frozen=True, slots=True)
class ResumeStatus:
    can_resume: bool
    online_initial_user_ids: tuple[int, ...]
    missing_initial_user_ids: tuple[int, ...]
    initial_user_ids: tuple[int, ...]
    campaign_status: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "can_resume": self.can_resume,
            "online_initial_user_ids": list(self.online_initial_user_ids),
            "missing_initial_user_ids": list(self.missing_initial_user_ids),
            "initial_user_ids": list(self.initial_user_ids),
            "campaign_status": self.campaign_status,
        }


@dataclass(frozen=True, slots=True)
class QueuedStrategyAction:
    action_id: int
    campaign_id: int
    user_id: int
    username: str
    faction_id: str
    month: int
    action_type: str
    action_key: str
    payload: dict[str, Any]
    status: str
    submitted_at: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.action_id,
            "campaign_id": self.campaign_id,
            "user_id": self.user_id,
            "username": self.username,
            "faction_id": self.faction_id,
            "month": self.month,
            "action_type": self.action_type,
            "action_key": self.action_key,
            "payload": dict(self.payload),
            "status": self.status,
            "submitted_at": self.submitted_at,
            "command_cost": strategy_action_command_cost(self.action_type, self.payload),
            "issuer_office_id": str(self.payload.get("issuer_office_id") or ""),
        }


@dataclass(frozen=True, slots=True)
class CampaignRecord:
    campaign_id: int
    join_code: str
    name: str
    owner_user_id: int
    status: str
    current_month: int
    created_at: float
    updated_at: float
    world: WorldState
    members: tuple[CampaignMember, ...]
    queued_actions: tuple[QueuedStrategyAction, ...] = ()

    def to_public_dict(self, *, resume_status: ResumeStatus | None = None) -> dict[str, Any]:
        from wujiang.strategy.campaign_tutorial import campaign_tutorial_public
        from wujiang.strategy.campaign_retrospective import campaign_retrospective_public
        from wujiang.strategy.ai_goals import ai_strategic_goals_public
        from wujiang.strategy.monthly_cycle import monthly_cycle_public
        from wujiang.strategy.office_automation import office_coordination_public

        command_points_by_faction = {
            faction.faction_id: faction_command_points(faction.faction_id, self.queued_actions)
            for faction in self.world.factions
        }
        payload = {
            "id": self.campaign_id,
            "join_code": self.join_code,
            "name": self.name,
            "owner_user_id": self.owner_user_id,
            "status": self.status,
            "current_month": self.current_month,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "world": self.world.to_public_dict(),
            "members": [member.to_dict() for member in self.members],
            "queued_actions": [action.to_dict() for action in self.queued_actions],
            "command_points_by_faction": command_points_by_faction,
        }
        payload["world"]["monthly_cycle"] = monthly_cycle_public(self.world, self.queued_actions)
        payload["world"]["campaign_tutorial"] = campaign_tutorial_public(self.world, self.queued_actions)
        payload["world"]["campaign_retrospective"] = campaign_retrospective_public(self.world)
        payload["world"]["ai_strategic_goals"] = ai_strategic_goals_public(self.world)
        payload["world"]["office_coordination"] = office_coordination_public(self.world, self.queued_actions)
        if resume_status is not None:
            payload["resume"] = resume_status.to_dict()
        return payload


class StrategyStore:
    def __init__(self, db_path: str | Path | None = None) -> None:
        self.db_path = strategy_database_path(str(db_path) if db_path is not None else None)
        self._lock = threading.RLock()
        self._schema_ready = False

    def _connect(self) -> sqlite3.Connection:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.db_path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

    @contextmanager
    def _connection(self) -> sqlite3.Connection:
        connection = self._connect()
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def _ensure_schema(self) -> None:
        if self._schema_ready:
            return
        with self._lock:
            if self._schema_ready:
                return
            with self._connection() as connection:
                connection.executescript(
                    """
                    CREATE TABLE IF NOT EXISTS strategy_campaigns (
                      id INTEGER PRIMARY KEY AUTOINCREMENT,
                      join_code TEXT,
                      name TEXT NOT NULL,
                      owner_user_id INTEGER NOT NULL,
                      status TEXT NOT NULL,
                      current_month INTEGER NOT NULL,
                      world_json TEXT NOT NULL,
                      created_at REAL NOT NULL,
                      updated_at REAL NOT NULL
                    );

                    CREATE TABLE IF NOT EXISTS strategy_members (
                      campaign_id INTEGER NOT NULL REFERENCES strategy_campaigns(id) ON DELETE CASCADE,
                      user_id INTEGER NOT NULL,
                      username TEXT NOT NULL,
                      role TEXT NOT NULL,
                      faction_id TEXT NOT NULL,
                      is_initial_player INTEGER NOT NULL,
                      joined_at REAL NOT NULL,
                      PRIMARY KEY (campaign_id, user_id)
                    );

                    CREATE TABLE IF NOT EXISTS strategy_presence (
                      campaign_id INTEGER NOT NULL REFERENCES strategy_campaigns(id) ON DELETE CASCADE,
                      user_id INTEGER NOT NULL,
                      last_seen_at REAL NOT NULL,
                      is_online INTEGER NOT NULL,
                      PRIMARY KEY (campaign_id, user_id)
                    );

                    CREATE TABLE IF NOT EXISTS strategy_actions (
                      id INTEGER PRIMARY KEY AUTOINCREMENT,
                      campaign_id INTEGER NOT NULL REFERENCES strategy_campaigns(id) ON DELETE CASCADE,
                      user_id INTEGER NOT NULL,
                      username TEXT NOT NULL,
                      faction_id TEXT NOT NULL,
                      month INTEGER NOT NULL,
                      action_type TEXT NOT NULL,
                      action_key TEXT NOT NULL,
                      payload_json TEXT NOT NULL,
                      status TEXT NOT NULL,
                      submitted_at REAL NOT NULL,
                      UNIQUE (campaign_id, user_id, month, action_type, action_key)
                    );

                    CREATE INDEX IF NOT EXISTS idx_strategy_members_user_id ON strategy_members(user_id);
                    CREATE INDEX IF NOT EXISTS idx_strategy_presence_campaign ON strategy_presence(campaign_id);
                    CREATE INDEX IF NOT EXISTS idx_strategy_actions_campaign_month
                      ON strategy_actions(campaign_id, month, status, submitted_at);
                    """
                )
                self._migrate_schema(connection)
            self._schema_ready = True

    def _migrate_schema(self, connection: sqlite3.Connection) -> None:
        campaign_columns = {
            str(row["name"])
            for row in connection.execute("PRAGMA table_info(strategy_campaigns)").fetchall()
        }
        if "join_code" not in campaign_columns:
            connection.execute("ALTER TABLE strategy_campaigns ADD COLUMN join_code TEXT")
        rows = connection.execute(
            """
            SELECT id
            FROM strategy_campaigns
            WHERE join_code IS NULL OR TRIM(join_code) = ''
            """
        ).fetchall()
        for row in rows:
            connection.execute(
                "UPDATE strategy_campaigns SET join_code = ? WHERE id = ?",
                (self._unique_join_code(connection), int(row["id"])),
            )
        connection.execute(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS idx_strategy_campaigns_join_code
              ON strategy_campaigns(join_code)
            """
        )

    def _unique_join_code(self, connection: sqlite3.Connection) -> str:
        while True:
            code = "".join(secrets.choice(JOIN_CODE_ALPHABET) for _ in range(JOIN_CODE_LENGTH))
            existing = connection.execute(
                "SELECT 1 FROM strategy_campaigns WHERE join_code = ?",
                (code,),
            ).fetchone()
            if existing is None:
                return code

    def create_campaign(
        self,
        *,
        owner: AuthUser,
        name: str,
        initial_players: Iterable[AuthUser] | None = None,
        seed: int = 1,
        city_count: int = 8,
        faction_count: int = 2,
        neutral_city_states: bool = False,
        campaign_contract: dict[str, Any] | None = None,
    ) -> CampaignRecord:
        normalized_name = " ".join(str(name or "").strip().split())
        if len(normalized_name) < 2:
            raise StrategyError("战役名称至少需要 2 个字符。")
        if len(normalized_name) > 40:
            raise StrategyError("战役名称最多 40 个字符。")

        players_by_id: dict[int, AuthUser] = {owner.user_id: owner}
        for player in initial_players or ():
            players_by_id[int(player.user_id)] = player
        contract = dict(campaign_contract or {})
        if contract:
            city_count = int(contract.get("city_count", city_count))
            faction_count = int(contract.get("major_faction_count", faction_count))
            neutral_city_states = int(contract.get("neutral_city_state_count", 0)) > 0
        if len(players_by_id) > faction_count:
            if contract:
                raise StrategyError("首个产品战役固定为 2 个主要势力，初始真人不能超过 2 名。")
            faction_count = len(players_by_id)
        world = generate_random_world(
            seed=seed,
            city_count=city_count,
            faction_count=faction_count,
            neutral_city_states=neutral_city_states,
            campaign_contract=contract,
        )
        now = time.time()

        with self._lock:
            self._ensure_schema()
            with self._connection() as connection:
                cursor = connection.execute(
                    """
                    INSERT INTO strategy_campaigns
                      (join_code, name, owner_user_id, status, current_month, world_json, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        self._unique_join_code(connection),
                        normalized_name,
                        owner.user_id,
                        "lobby",
                        world.current_month,
                        json.dumps(world.to_dict(), ensure_ascii=False, sort_keys=True),
                        now,
                        now,
                    ),
                )
                campaign_id = int(cursor.lastrowid)
                for index, player in enumerate(players_by_id.values(), start=1):
                    connection.execute(
                        """
                        INSERT INTO strategy_members
                          (campaign_id, user_id, username, role, faction_id, is_initial_player, joined_at)
                        VALUES (?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            campaign_id,
                            int(player.user_id),
                            player.username,
                            "host" if int(player.user_id) == int(owner.user_id) else "lord",
                            f"faction_{index}",
                            1,
                            now,
                        ),
                    )
                return self._campaign_from_connection(connection, campaign_id)

    def join_campaign_by_code(self, join_code: str, user: AuthUser) -> CampaignRecord:
        normalized_code = "".join(str(join_code or "").strip().upper().split())
        if len(normalized_code) != JOIN_CODE_LENGTH:
            raise StrategyError("战役加入码必须是 6 位。")
        now = time.time()
        with self._lock:
            self._ensure_schema()
            with self._connection() as connection:
                campaign = self._campaign_from_join_code(connection, normalized_code)
                existing = connection.execute(
                    """
                    SELECT 1
                    FROM strategy_members
                    WHERE campaign_id = ? AND user_id = ?
                    """,
                    (campaign.campaign_id, int(user.user_id)),
                ).fetchone()
                if existing is not None:
                    return campaign
                if campaign.status != "lobby":
                    raise StrategyError("战役已经锁定，只有初始玩家可以恢复。", status=HTTPStatus.CONFLICT)
                used_faction_ids = {member.faction_id for member in campaign.members}
                free_faction = next(
                    (faction for faction in campaign.world.factions if faction.faction_id not in used_faction_ids),
                    None,
                )
                if free_faction is None:
                    raise StrategyError("战役初始玩家席位已满。", status=HTTPStatus.CONFLICT)
                connection.execute(
                    """
                    INSERT INTO strategy_members
                      (campaign_id, user_id, username, role, faction_id, is_initial_player, joined_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        campaign.campaign_id,
                        int(user.user_id),
                        user.username,
                        "lord",
                        free_faction.faction_id,
                        1,
                        now,
                    ),
                )
                return self._campaign_from_connection(connection, campaign.campaign_id)

    def lock_initial_players(self, campaign_id: int, user_id: int) -> CampaignRecord:
        now = time.time()
        with self._lock:
            self._ensure_schema()
            with self._connection() as connection:
                campaign = self._campaign_from_connection(connection, int(campaign_id))
                if int(campaign.owner_user_id) != int(user_id):
                    raise StrategyError("只有战役房主可以锁定初始玩家。", status=HTTPStatus.FORBIDDEN)
                if campaign.status == "active":
                    return campaign
                if campaign.status != "lobby":
                    raise StrategyError("当前战役状态不能锁定初始玩家。", status=HTTPStatus.CONFLICT)
                if not campaign.members:
                    raise StrategyError("战役缺少初始玩家。")
                used_faction_ids = {member.faction_id for member in campaign.members}
                for faction in campaign.world.factions:
                    if faction.faction_id in used_faction_ids:
                        continue
                    faction_index = _faction_index(faction.faction_id)
                    connection.execute(
                        """
                        INSERT INTO strategy_members
                          (campaign_id, user_id, username, role, faction_id, is_initial_player, joined_at)
                        VALUES (?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            int(campaign_id),
                            -faction_index,
                            f"{faction.name} AI",
                            AI_MEMBER_ROLE,
                            faction.faction_id,
                            1,
                            now + faction_index / 1000,
                        ),
                    )
                connection.execute(
                    """
                    UPDATE strategy_campaigns
                    SET status = 'active', updated_at = ?
                    WHERE id = ?
                    """,
                    (now, int(campaign_id)),
                )
                return self._campaign_from_connection(connection, int(campaign_id))

    def rotate_join_code(self, campaign_id: int, user_id: int) -> CampaignRecord:
        now = time.time()
        with self._lock:
            self._ensure_schema()
            with self._connection() as connection:
                campaign = self._campaign_from_connection(connection, int(campaign_id))
                if int(campaign.owner_user_id) != int(user_id):
                    raise StrategyError("只有战役房主可以重新生成加入码。", status=HTTPStatus.FORBIDDEN)
                connection.execute(
                    """
                    UPDATE strategy_campaigns
                    SET join_code = ?, updated_at = ?
                    WHERE id = ?
                    """,
                    (self._unique_join_code(connection), now, int(campaign_id)),
                )
                return self._campaign_from_connection(connection, int(campaign_id))

    def list_campaigns_for_user(self, user_id: int) -> list[CampaignRecord]:
        with self._lock:
            self._ensure_schema()
            with self._connection() as connection:
                rows = connection.execute(
                    """
                    SELECT campaign_id
                    FROM strategy_members
                    WHERE user_id = ?
                    ORDER BY joined_at DESC
                    """,
                    (int(user_id),),
                ).fetchall()
                return [self._campaign_from_connection(connection, int(row["campaign_id"])) for row in rows]

    def get_campaign_for_user(self, campaign_id: int, user_id: int) -> CampaignRecord:
        with self._lock:
            self._ensure_schema()
            with self._connection() as connection:
                self._require_member(connection, int(campaign_id), int(user_id))
                return self._campaign_from_connection(connection, int(campaign_id))

    def mark_online(self, campaign_id: int, user: AuthUser) -> ResumeStatus:
        now = time.time()
        with self._lock:
            self._ensure_schema()
            with self._connection() as connection:
                self._require_member(connection, int(campaign_id), int(user.user_id))
                connection.execute(
                    """
                    INSERT INTO strategy_presence (campaign_id, user_id, last_seen_at, is_online)
                    VALUES (?, ?, ?, 1)
                    ON CONFLICT(campaign_id, user_id)
                    DO UPDATE SET last_seen_at = excluded.last_seen_at, is_online = 1
                    """,
                    (int(campaign_id), int(user.user_id), now),
                )
                return self.resume_status_from_connection(connection, int(campaign_id), now=now)

    def mark_offline(self, campaign_id: int, user_id: int) -> ResumeStatus:
        now = time.time()
        with self._lock:
            self._ensure_schema()
            with self._connection() as connection:
                self._require_member(connection, int(campaign_id), int(user_id))
                connection.execute(
                    """
                    INSERT INTO strategy_presence (campaign_id, user_id, last_seen_at, is_online)
                    VALUES (?, ?, ?, 0)
                    ON CONFLICT(campaign_id, user_id)
                    DO UPDATE SET last_seen_at = excluded.last_seen_at, is_online = 0
                    """,
                    (int(campaign_id), int(user_id), now),
                )
                return self.resume_status_from_connection(connection, int(campaign_id), now=now)

    def resume_status(self, campaign_id: int) -> ResumeStatus:
        with self._lock:
            self._ensure_schema()
            with self._connection() as connection:
                return self.resume_status_from_connection(connection, int(campaign_id), now=time.time())

    def require_can_resume(self, campaign_id: int, user_id: int) -> ResumeStatus:
        with self._lock:
            self._ensure_schema()
            with self._connection() as connection:
                self._require_member(connection, int(campaign_id), int(user_id))
                status = self.resume_status_from_connection(connection, int(campaign_id), now=time.time())
                if status.campaign_status != "active":
                    raise StrategyError("战役需要房主锁定初始玩家后才能继续。", status=HTTPStatus.CONFLICT)
                if not status.can_resume:
                    raise StrategyError("战役需要所有初始玩家在线后才能继续。", status=HTTPStatus.CONFLICT)
                return status

    def update_world(self, campaign_id: int, user_id: int, world: WorldState) -> CampaignRecord:
        now = time.time()
        with self._lock:
            self._ensure_schema()
            with self._connection() as connection:
                self._require_member(connection, int(campaign_id), int(user_id))
                connection.execute(
                    """
                    UPDATE strategy_campaigns
                    SET current_month = ?, world_json = ?, updated_at = ?
                    WHERE id = ?
                    """,
                    (
                        int(world.current_month),
                        json.dumps(world.to_dict(), ensure_ascii=False, sort_keys=True),
                        now,
                        int(campaign_id),
                    ),
                )
                for hero in world.strategic_heroes:
                    if hero.controller_type != "player" or hero.controller_user_id is None:
                        continue
                    faction_id = hero.faction_id if hero.status == "serving" and hero.faction_id else ""
                    connection.execute(
                        """
                        UPDATE strategy_members
                        SET faction_id = ?
                        WHERE campaign_id = ? AND user_id = ? AND role != ?
                        """,
                        (faction_id, int(campaign_id), int(hero.controller_user_id), AI_MEMBER_ROLE),
                    )
                return self._campaign_from_connection(connection, int(campaign_id))

    def queue_action(
        self,
        *,
        campaign_id: int,
        user: AuthUser,
        action_type: str,
        action_key: str,
        payload: dict[str, Any],
    ) -> CampaignRecord:
        normalized_type = str(action_type or "").strip()
        normalized_key = str(action_key or "").strip()
        if not normalized_type or not normalized_key:
            raise StrategyError("Strategy action type and key are required.")
        if not isinstance(payload, dict):
            raise StrategyError("Strategy action payload must be an object.")
        now = time.time()
        with self._lock:
            self._ensure_schema()
            with self._connection() as connection:
                self._require_member(connection, int(campaign_id), int(user.user_id))
                campaign = self._campaign_from_connection(connection, int(campaign_id))
                member = next(
                    (item for item in campaign.members if int(item.user_id) == int(user.user_id)),
                    None,
                )
                if member is None:
                    raise StrategyError("Campaign member not found.", status=HTTPStatus.FORBIDDEN)
                connection.execute(
                    """
                    INSERT INTO strategy_actions
                      (campaign_id, user_id, username, faction_id, month, action_type,
                       action_key, payload_json, status, submitted_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?)
                    ON CONFLICT(campaign_id, user_id, month, action_type, action_key)
                    DO UPDATE SET
                      username = excluded.username,
                      faction_id = excluded.faction_id,
                      payload_json = excluded.payload_json,
                      status = 'pending',
                      submitted_at = excluded.submitted_at
                    """,
                    (
                        int(campaign_id),
                        int(user.user_id),
                        user.username,
                        member.faction_id,
                        int(campaign.world.current_month),
                        normalized_type,
                        normalized_key,
                        json.dumps(payload, ensure_ascii=False, sort_keys=True),
                        now,
                    ),
                )
                return self._campaign_from_connection(connection, int(campaign_id))

    def mark_queued_actions_resolved(self, campaign_id: int, user_id: int, month: int) -> None:
        now = time.time()
        with self._lock:
            self._ensure_schema()
            with self._connection() as connection:
                self._require_member(connection, int(campaign_id), int(user_id))
                connection.execute(
                    """
                    UPDATE strategy_actions
                    SET status = 'resolved', submitted_at = ?
                    WHERE campaign_id = ? AND month = ? AND status = 'pending'
                    """,
                    (now, int(campaign_id), int(month)),
                )

    def resolve_battle_room_result(
        self,
        *,
        battle_room_id: str,
        winner_team_id: int,
        battle_summary: str = "",
        surviving_grid_units_by_team: dict[int, int] | None = None,
        surviving_hero_codes_by_team: dict[int, set[str] | list[str] | tuple[str, ...]] | None = None,
    ) -> CampaignRecord | None:
        room_id = str(battle_room_id or "").strip().upper()
        if not room_id:
            return None
        now = time.time()
        with self._lock:
            self._ensure_schema()
            with self._connection() as connection:
                rows = connection.execute(
                    """
                    SELECT id, world_json
                    FROM strategy_campaigns
                    WHERE world_json LIKE ?
                    ORDER BY updated_at DESC, id DESC
                    """,
                    (f"%{room_id}%",),
                ).fetchall()
                for row in rows:
                    world = WorldState.from_dict(json.loads(str(row["world_json"])))
                    battle = next(
                        (
                            item
                            for item in world.pending_battles
                            if str(item.battle_room_id or "").strip().upper() == room_id
                        ),
                        None,
                    )
                    if battle is None:
                        continue
                    if battle.status != "pending":
                        return self._campaign_from_connection(connection, int(row["id"]))
                    next_world = resolve_battle_room_result(
                        world,
                        battle_room_id=room_id,
                        winner_team_id=winner_team_id,
                        battle_summary=battle_summary,
                        surviving_grid_units_by_team=surviving_grid_units_by_team,
                        surviving_hero_codes_by_team=surviving_hero_codes_by_team,
                    )
                    connection.execute(
                        """
                        UPDATE strategy_campaigns
                        SET current_month = ?, world_json = ?, updated_at = ?
                        WHERE id = ?
                        """,
                        (
                            int(next_world.current_month),
                            json.dumps(next_world.to_dict(), ensure_ascii=False, sort_keys=True),
                            now,
                            int(row["id"]),
                        ),
                    )
                    return self._campaign_from_connection(connection, int(row["id"]))
        return None

    def resume_status_from_connection(
        self,
        connection: sqlite3.Connection,
        campaign_id: int,
        *,
        now: float,
    ) -> ResumeStatus:
        member_rows = connection.execute(
            """
            SELECT user_id
            FROM strategy_members
            WHERE campaign_id = ? AND is_initial_player = 1 AND role != ?
            ORDER BY user_id
            """,
            (int(campaign_id), AI_MEMBER_ROLE),
        ).fetchall()
        initial_user_ids = tuple(int(row["user_id"]) for row in member_rows)
        if not initial_user_ids:
            raise StrategyError("战役缺少初始玩家。")
        campaign_row = connection.execute(
            "SELECT status FROM strategy_campaigns WHERE id = ?",
            (int(campaign_id),),
        ).fetchone()
        if campaign_row is None:
            raise StrategyError("战役不存在。", status=HTTPStatus.NOT_FOUND)
        campaign_status = str(campaign_row["status"])
        presence_rows = connection.execute(
            """
            SELECT user_id, last_seen_at, is_online
            FROM strategy_presence
            WHERE campaign_id = ?
            """,
            (int(campaign_id),),
        ).fetchall()
        online_ids = {
            int(row["user_id"])
            for row in presence_rows
            if int(row["is_online"]) == 1 and now - float(row["last_seen_at"]) <= PRESENCE_TTL_SECONDS
        }
        online_initial_user_ids = tuple(user_id for user_id in initial_user_ids if user_id in online_ids)
        missing_initial_user_ids = tuple(user_id for user_id in initial_user_ids if user_id not in online_ids)
        return ResumeStatus(
            can_resume=campaign_status == "active" and not missing_initial_user_ids,
            online_initial_user_ids=online_initial_user_ids,
            missing_initial_user_ids=missing_initial_user_ids,
            initial_user_ids=initial_user_ids,
            campaign_status=campaign_status,
        )

    def _require_member(self, connection: sqlite3.Connection, campaign_id: int, user_id: int) -> None:
        row = connection.execute(
            """
            SELECT 1
            FROM strategy_members
            WHERE campaign_id = ? AND user_id = ?
            """,
            (int(campaign_id), int(user_id)),
        ).fetchone()
        if row is None:
            raise StrategyError("你不是这个战役的成员，不能恢复或操作该战役。", status=HTTPStatus.FORBIDDEN)

    def _campaign_from_connection(self, connection: sqlite3.Connection, campaign_id: int) -> CampaignRecord:
        row = connection.execute(
            "SELECT * FROM strategy_campaigns WHERE id = ?",
            (int(campaign_id),),
        ).fetchone()
        if row is None:
            raise StrategyError("战役不存在。", status=HTTPStatus.NOT_FOUND)
        member_rows = connection.execute(
            """
            SELECT *
            FROM strategy_members
            WHERE campaign_id = ?
            ORDER BY joined_at, user_id
            """,
            (int(campaign_id),),
        ).fetchall()
        world = WorldState.from_dict(json.loads(str(row["world_json"])))
        members = tuple(
            CampaignMember(
                user_id=int(member["user_id"]),
                username=str(member["username"]),
                role=str(member["role"]),
                faction_id=str(member["faction_id"]),
                is_initial_player=bool(member["is_initial_player"]),
            )
            for member in member_rows
        )
        from wujiang.strategy.heroes import ensure_strategic_hero_system
        from wujiang.strategy.offices import ensure_office_system

        world = ensure_office_system(world, members)
        world = ensure_strategic_hero_system(world, members)
        action_rows = connection.execute(
            """
            SELECT *
            FROM strategy_actions
            WHERE campaign_id = ? AND month = ? AND status = 'pending'
            ORDER BY submitted_at, id
            """,
            (int(campaign_id), int(world.current_month)),
        ).fetchall()
        queued_actions = tuple(
            QueuedStrategyAction(
                action_id=int(action["id"]),
                campaign_id=int(action["campaign_id"]),
                user_id=int(action["user_id"]),
                username=str(action["username"]),
                faction_id=str(action["faction_id"]),
                month=int(action["month"]),
                action_type=str(action["action_type"]),
                action_key=str(action["action_key"]),
                payload=json.loads(str(action["payload_json"])),
                status=str(action["status"]),
                submitted_at=float(action["submitted_at"]),
            )
            for action in action_rows
        )
        return CampaignRecord(
            campaign_id=int(row["id"]),
            join_code=str(row["join_code"] or ""),
            name=str(row["name"]),
            owner_user_id=int(row["owner_user_id"]),
            status=str(row["status"]),
            current_month=int(row["current_month"]),
            created_at=float(row["created_at"]),
            updated_at=float(row["updated_at"]),
            world=world,
            members=members,
            queued_actions=queued_actions,
        )

    def _campaign_from_join_code(self, connection: sqlite3.Connection, join_code: str) -> CampaignRecord:
        row = connection.execute(
            "SELECT id FROM strategy_campaigns WHERE join_code = ?",
            (str(join_code),),
        ).fetchone()
        if row is None:
            raise StrategyError("战役加入码不存在。", status=HTTPStatus.NOT_FOUND)
        return self._campaign_from_connection(connection, int(row["id"]))
