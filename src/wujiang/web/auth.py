from __future__ import annotations

import hashlib
import hmac
import os
import secrets
import sqlite3
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass
from http import HTTPStatus
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_AUTH_DB_PATH = PROJECT_ROOT / "var" / "wujiang.sqlite3"
PASSWORD_HASH_ITERATIONS = 200_000
SESSION_TTL_SECONDS = 60 * 60 * 24 * 30


class AuthError(Exception):
    def __init__(self, message: str, *, status: HTTPStatus = HTTPStatus.BAD_REQUEST) -> None:
        super().__init__(message)
        self.status = status


@dataclass(frozen=True, slots=True)
class AuthUser:
    user_id: int
    username: str
    created_at: float

    def to_public_dict(self) -> dict[str, Any]:
        return {
            "id": self.user_id,
            "username": self.username,
            "created_at": self.created_at,
        }


def auth_database_path(raw_path: str | None = None) -> Path:
    configured = str(raw_path or os.environ.get("WUJIANG_AUTH_DB") or "").strip()
    return Path(configured).expanduser() if configured else DEFAULT_AUTH_DB_PATH


def normalize_username(username: str) -> str:
    return " ".join(str(username or "").strip().split())


def normalized_username_key(username: str) -> str:
    return normalize_username(username).casefold()


def validate_username(username: str) -> str:
    normalized = normalize_username(username)
    if len(normalized) < 2:
        raise AuthError("用户名至少需要 2 个字符。")
    if len(normalized) > 32:
        raise AuthError("用户名最多 32 个字符。")
    if any(ord(char) < 32 for char in normalized):
        raise AuthError("用户名不能包含控制字符。")
    return normalized


def validate_password(password: str) -> str:
    raw = str(password or "")
    if len(raw) < 6:
        raise AuthError("密码至少需要 6 个字符。")
    if len(raw) > 128:
        raise AuthError("密码最多 128 个字符。")
    return raw


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        salt,
        PASSWORD_HASH_ITERATIONS,
    )
    return f"pbkdf2_sha256${PASSWORD_HASH_ITERATIONS}${salt.hex()}${digest.hex()}"


def verify_password(password: str, encoded_hash: str) -> bool:
    try:
        algorithm, iterations_raw, salt_hex, digest_hex = encoded_hash.split("$", 3)
        if algorithm != "pbkdf2_sha256":
            return False
        iterations = int(iterations_raw)
        salt = bytes.fromhex(salt_hex)
        expected = bytes.fromhex(digest_hex)
    except (TypeError, ValueError):
        return False
    actual = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    return hmac.compare_digest(actual, expected)


class UserStore:
    def __init__(self, db_path: str | Path | None = None) -> None:
        self.db_path = auth_database_path(str(db_path) if db_path is not None else None)
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
                    CREATE TABLE IF NOT EXISTS users (
                      id INTEGER PRIMARY KEY AUTOINCREMENT,
                      username TEXT NOT NULL,
                      normalized_username TEXT NOT NULL UNIQUE,
                      password_hash TEXT NOT NULL,
                      created_at REAL NOT NULL,
                      updated_at REAL NOT NULL
                    );

                    CREATE TABLE IF NOT EXISTS sessions (
                      token TEXT PRIMARY KEY,
                      user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                      created_at REAL NOT NULL,
                      last_seen_at REAL NOT NULL,
                      expires_at REAL NOT NULL
                    );

                    CREATE INDEX IF NOT EXISTS idx_sessions_user_id ON sessions(user_id);
                    CREATE INDEX IF NOT EXISTS idx_sessions_expires_at ON sessions(expires_at);
                    """
                )
            self._schema_ready = True

    def _user_from_row(self, row: sqlite3.Row | None) -> AuthUser | None:
        if row is None:
            return None
        return AuthUser(
            user_id=int(row["id"]),
            username=str(row["username"]),
            created_at=float(row["created_at"]),
        )

    def register(self, username: str, password: str) -> tuple[AuthUser, str]:
        normalized = validate_username(username)
        normalized_key = normalized_username_key(normalized)
        valid_password = validate_password(password)
        now = time.time()
        with self._lock:
            self._ensure_schema()
            with self._connection() as connection:
                try:
                    cursor = connection.execute(
                        """
                        INSERT INTO users (username, normalized_username, password_hash, created_at, updated_at)
                        VALUES (?, ?, ?, ?, ?)
                        """,
                        (normalized, normalized_key, hash_password(valid_password), now, now),
                    )
                except sqlite3.IntegrityError as exc:
                    raise AuthError("这个用户名已经被注册。") from exc
                user = AuthUser(user_id=int(cursor.lastrowid), username=normalized, created_at=now)
                token = self._create_session(connection, user.user_id, now)
                return user, token

    def authenticate(self, username: str, password: str) -> tuple[AuthUser, str]:
        normalized_key = normalized_username_key(username)
        valid_password = validate_password(password)
        now = time.time()
        with self._lock:
            self._ensure_schema()
            with self._connection() as connection:
                row = connection.execute(
                    "SELECT * FROM users WHERE normalized_username = ?",
                    (normalized_key,),
                ).fetchone()
                if row is None or not verify_password(valid_password, str(row["password_hash"])):
                    raise AuthError("用户名或密码不正确。", status=HTTPStatus.UNAUTHORIZED)
                user = self._user_from_row(row)
                if user is None:
                    raise AuthError("用户不存在。", status=HTTPStatus.UNAUTHORIZED)
                token = self._create_session(connection, user.user_id, now)
                return user, token

    def user_for_session(self, token: str) -> AuthUser:
        normalized_token = str(token or "").strip()
        if not normalized_token:
            raise AuthError("缺少登录令牌。", status=HTTPStatus.UNAUTHORIZED)
        now = time.time()
        with self._lock:
            self._ensure_schema()
            with self._connection() as connection:
                row = connection.execute(
                    """
                    SELECT users.*
                    FROM sessions
                    JOIN users ON users.id = sessions.user_id
                    WHERE sessions.token = ? AND sessions.expires_at > ?
                    """,
                    (normalized_token, now),
                ).fetchone()
                if row is None:
                    raise AuthError("登录状态已失效，请重新登录。", status=HTTPStatus.UNAUTHORIZED)
                connection.execute(
                    "UPDATE sessions SET last_seen_at = ? WHERE token = ?",
                    (now, normalized_token),
                )
                user = self._user_from_row(row)
                if user is None:
                    raise AuthError("登录状态已失效，请重新登录。", status=HTTPStatus.UNAUTHORIZED)
                return user

    def logout(self, token: str) -> None:
        normalized_token = str(token or "").strip()
        if not normalized_token:
            return
        with self._lock:
            self._ensure_schema()
            with self._connection() as connection:
                connection.execute("DELETE FROM sessions WHERE token = ?", (normalized_token,))

    def _create_session(self, connection: sqlite3.Connection, user_id: int, now: float) -> str:
        token = secrets.token_urlsafe(32)
        connection.execute(
            """
            INSERT INTO sessions (token, user_id, created_at, last_seen_at, expires_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (token, user_id, now, now, now + SESSION_TTL_SECONDS),
        )
        return token
