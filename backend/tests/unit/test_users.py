"""User repository tests with mocked SQLAlchemy session.

Real DB integration is deferred to tests/integration/test_users_db.py
once `alembic upgrade head` has been run.
"""
from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

os.environ.setdefault("JWT_SECRET_KEY", "test-secret-please-replace-with-32-random-bytes-x")
os.environ.setdefault("RUNTIME_MODE", "paper")

try:
    import sqlalchemy  # noqa: F401
    import passlib  # noqa: F401

    _HAVE_DEPS = True
except ImportError:
    _HAVE_DEPS = False


if _HAVE_DEPS:
    from core.auth import AuthError  # noqa: E402
    from core.config import reset_settings_cache  # noqa: E402
    from core.models.auth import User  # noqa: E402
    from core.users import (  # noqa: E402
        authenticate,
        bootstrap_if_empty,
        create_user,
        get_user,
        set_live_enabled,
        set_password,
    )

    reset_settings_cache()


@unittest.skipUnless(_HAVE_DEPS, "sqlalchemy/passlib not installed (run `uv sync`)")
class CreateUserTest(unittest.TestCase):
    def setUp(self):
        self.session = MagicMock()
        # get_user returns None (no existing user)
        self.session.scalars.return_value.first.return_value = None

    def test_create_user_hashes_password(self):
        user = create_user(self.session, username="alice", password="hunter2")
        self.session.add.assert_called_once()
        added = self.session.add.call_args[0][0]
        self.assertNotEqual(added.password_hash, "hunter2")
        self.assertTrue(added.password_hash.startswith("$2"))  # bcrypt prefix
        self.assertTrue(added.is_active)
        self.assertFalse(added.is_live_enabled)

    def test_create_user_with_live_enabled(self):
        user = create_user(
            self.session, username="alice", password="hunter2", is_live_enabled=True
        )
        added = self.session.add.call_args[0][0]
        self.assertTrue(added.is_live_enabled)

    def test_create_user_duplicate_raises(self):
        existing = User(username="alice", password_hash="x", is_active=True, is_live_enabled=False)
        self.session.scalars.return_value.first.return_value = existing
        with self.assertRaises(AuthError):
            create_user(self.session, username="alice", password="hunter2")


@unittest.skipUnless(_HAVE_DEPS, "deps not installed")
class AuthenticateTest(unittest.TestCase):
    def setUp(self):
        from core.auth import hash_password

        self.real_hash = hash_password("hunter2")
        self.user = User(
            username="alice", password_hash=self.real_hash,
            is_active=True, is_live_enabled=False,
        )
        self.session = MagicMock()

    def _set_user(self, user):
        self.session.scalars.return_value.first.return_value = user

    def test_correct_password_returns_user(self):
        self._set_user(self.user)
        u = authenticate(self.session, "alice", "hunter2")
        self.assertIsNotNone(u)
        self.assertEqual(u.username, "alice")

    def test_wrong_password_returns_none(self):
        self._set_user(self.user)
        u = authenticate(self.session, "alice", "wrong")
        self.assertIsNone(u)

    def test_unknown_user_returns_none(self):
        self._set_user(None)
        u = authenticate(self.session, "bob", "hunter2")
        self.assertIsNone(u)

    def test_inactive_user_returns_none(self):
        self.user.is_active = False
        self._set_user(self.user)
        u = authenticate(self.session, "alice", "hunter2")
        self.assertIsNone(u)


@unittest.skipUnless(_HAVE_DEPS, "deps not installed")
class SetPasswordAndLiveTest(unittest.TestCase):
    def setUp(self):
        from core.auth import hash_password

        self.user = User(
            username="alice", password_hash=hash_password("hunter2"),
            is_active=True, is_live_enabled=False,
        )
        self.session = MagicMock()

    def test_set_password_changes_hash(self):
        old_hash = self.user.password_hash
        set_password(self.session, self.user, "newpw")
        self.assertNotEqual(self.user.password_hash, old_hash)

    def test_set_live_enabled_flag(self):
        set_live_enabled(self.session, self.user, True)
        self.assertTrue(self.user.is_live_enabled)
        set_live_enabled(self.session, self.user, False)
        self.assertFalse(self.user.is_live_enabled)


@unittest.skipUnless(_HAVE_DEPS, "deps not installed")
class BootstrapTest(unittest.TestCase):
    def test_bootstrap_creates_when_empty(self):
        os.environ["BOOTSTRAP_USERNAME"] = "woonam"
        os.environ["BOOTSTRAP_PASSWORD"] = "init-strong-pw"
        reset_settings_cache()
        try:
            session = MagicMock()
            session.scalars.return_value.__iter__.return_value = iter([])  # list_users() → empty
            session.scalars.return_value.first.return_value = None          # create_user's get_user() → no dup
            user = bootstrap_if_empty(session)
            self.assertIsNotNone(user)
            session.add.assert_called_once()
            added = session.add.call_args[0][0]
            self.assertEqual(added.username, "woonam")
        finally:
            os.environ.pop("BOOTSTRAP_PASSWORD", None)
            reset_settings_cache()

    def test_bootstrap_is_noop_when_users_exist(self):
        session = MagicMock()
        existing = User(username="someone", password_hash="x", is_active=True, is_live_enabled=False)
        session.scalars.return_value.__iter__.return_value = iter([existing])
        user = bootstrap_if_empty(session)
        self.assertIsNone(user)
        session.add.assert_not_called()

    def test_bootstrap_skipped_without_password(self):
        # Force an empty password even though .env may define one: an
        # explicit env var outranks the dotenv file in pydantic-settings.
        os.environ["BOOTSTRAP_PASSWORD"] = ""
        reset_settings_cache()
        try:
            session = MagicMock()
            session.scalars.return_value.__iter__.return_value = iter([])
            user = bootstrap_if_empty(session)
            self.assertIsNone(user)
            session.add.assert_not_called()
        finally:
            os.environ.pop("BOOTSTRAP_PASSWORD", None)
            reset_settings_cache()


if __name__ == "__main__":
    unittest.main()
