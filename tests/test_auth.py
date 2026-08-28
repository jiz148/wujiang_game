from __future__ import annotations

import tempfile
import sys
import sqlite3
import time
from contextlib import closing
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from wujiang.web.auth import (
    AuthError,
    PASSWORD_HASH_ITERATIONS,
    UserStore,
    hash_password,
    session_token_hash,
)


class UserStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmpdir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmpdir.name) / "auth.sqlite3"
        self.store = UserStore(self.db_path)

    def tearDown(self) -> None:
        self.tmpdir.cleanup()

    def test_register_login_and_session_lookup(self) -> None:
        user, token = self.store.register("Alice", "secret123")

        self.assertEqual(user.username, "Alice")
        self.assertTrue(self.db_path.exists())
        self.assertEqual(self.store.user_for_session(token).username, "Alice")

        login_user, login_token = self.store.authenticate(" alice ", "secret123")
        self.assertEqual(login_user.user_id, user.user_id)
        self.assertNotEqual(login_token, token)
        self.assertEqual(self.store.user_for_session(login_token).username, "Alice")

    def test_duplicate_username_is_case_insensitive(self) -> None:
        self.store.register("Alice", "secret123")

        with self.assertRaises(AuthError):
            self.store.register("alice", "secret456")

    def test_logout_invalidates_session(self) -> None:
        _user, token = self.store.register("Alice", "secret123")

        self.store.logout(token)

        with self.assertRaises(AuthError):
            self.store.user_for_session(token)

    def test_session_token_is_hashed_at_rest(self) -> None:
        _user, token = self.store.register("Alice", "secret123")

        with closing(sqlite3.connect(self.db_path)) as connection:
            stored_token, stored_hash = connection.execute(
                "SELECT token, token_hash FROM sessions"
            ).fetchone()
        self.assertNotEqual(stored_token, token)
        self.assertNotIn(token, stored_token)
        self.assertEqual(stored_hash, session_token_hash(token))
        self.assertEqual(self.store.user_for_session(token).username, "Alice")

    def test_legacy_plaintext_session_is_migrated_without_invalidating_client(self) -> None:
        user, _token = self.store.register("Alice", "secret123")
        legacy_token = "legacy-client-secret"
        with closing(sqlite3.connect(self.db_path)) as connection:
            connection.execute("DROP INDEX idx_sessions_token_hash")
            connection.execute("DELETE FROM sessions")
            connection.execute("ALTER TABLE sessions RENAME TO sessions_hardened")
            connection.execute(
                """
                CREATE TABLE sessions (
                  token TEXT PRIMARY KEY, user_id INTEGER NOT NULL, created_at REAL NOT NULL,
                  last_seen_at REAL NOT NULL, expires_at REAL NOT NULL
                )
                """
            )
            now = time.time()
            connection.execute(
                "INSERT INTO sessions VALUES (?, ?, ?, ?, ?)",
                (legacy_token, user.user_id, now, now, now + 3600),
            )
            connection.execute("DROP TABLE sessions_hardened")
            connection.commit()
        migrated = UserStore(self.db_path)

        self.assertEqual(migrated.user_for_session(legacy_token).user_id, user.user_id)
        with closing(sqlite3.connect(self.db_path)) as connection:
            stored_token, stored_hash = connection.execute(
                "SELECT token, token_hash FROM sessions"
            ).fetchone()
        self.assertNotEqual(stored_token, legacy_token)
        self.assertEqual(stored_hash, session_token_hash(legacy_token))

    def test_idle_expiry_and_active_session_cap(self) -> None:
        now = [1_000.0]
        store = UserStore(
            self.db_path, clock=lambda: now[0], session_idle_ttl_seconds=60,
            max_active_sessions_per_user=2,
        )
        _user, first = store.register("Alice", "secret123")
        now[0] += 1
        _user, second = store.authenticate("Alice", "secret123")
        now[0] += 1
        _user, third = store.authenticate("Alice", "secret123")
        with self.assertRaises(AuthError):
            store.user_for_session(first)
        self.assertEqual(store.user_for_session(second).username, "Alice")
        self.assertEqual(store.user_for_session(third).username, "Alice")
        now[0] += 61
        with self.assertRaises(AuthError):
            store.user_for_session(third)

    def test_successful_login_upgrades_old_password_hash(self) -> None:
        user, _token = self.store.register("Alice", "secret123")
        old_hash = hash_password("secret123", iterations=100_000)
        with closing(sqlite3.connect(self.db_path)) as connection:
            connection.execute("UPDATE users SET password_hash = ? WHERE id = ?", (old_hash, user.user_id))
            connection.commit()

        self.store.authenticate("Alice", "secret123")

        with closing(sqlite3.connect(self.db_path)) as connection:
            upgraded = connection.execute("SELECT password_hash FROM users WHERE id = ?", (user.user_id,)).fetchone()[0]
        self.assertEqual(int(upgraded.split("$")[1]), PASSWORD_HASH_ITERATIONS)

    def test_new_password_floor_does_not_lock_out_legacy_six_character_account(self) -> None:
        with self.assertRaises(AuthError):
            self.store.register("ShortNew", "123456")
        user, _token = self.store.register("Legacy", "secret123")
        with closing(sqlite3.connect(self.db_path)) as connection:
            connection.execute(
                "UPDATE users SET password_hash = ? WHERE id = ?",
                (hash_password("123456", iterations=200_000), user.user_id),
            )
            connection.commit()

        logged_in, _token = self.store.authenticate("Legacy", "123456")
        self.assertEqual(logged_in.user_id, user.user_id)


if __name__ == "__main__":
    unittest.main()
