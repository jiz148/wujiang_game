from __future__ import annotations

import tempfile
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from wujiang.web.auth import AuthError, UserStore


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


if __name__ == "__main__":
    unittest.main()
