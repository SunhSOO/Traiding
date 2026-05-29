"""Auth primitive tests — password hashing + JWT.

The DB-touching paths (bootstrap user creation, FastAPI dependency)
are integration tests deferred until PostgreSQL is reachable.
"""
from __future__ import annotations

import os
import sys
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

# We need a real JWT secret for token tests. Set one BEFORE we import
# config so the settings cache picks it up.
os.environ["JWT_SECRET_KEY"] = "test-secret-please-replace-with-32-random-bytes-x"
os.environ["RUNTIME_MODE"] = "paper"  # ensure safety gate doesn't fire

# Reset any cached settings from earlier test modules.
try:
    from core.config import reset_settings_cache  # noqa: E402

    reset_settings_cache()
except ImportError:
    pass


try:
    import passlib  # noqa: F401
    import jose  # noqa: F401

    _HAVE_DEPS = True
except ImportError:
    _HAVE_DEPS = False


if _HAVE_DEPS:
    from core.auth import (  # noqa: E402
        AuthError,
        TokenPayload,
        create_access_token,
        decode_access_token,
        hash_password,
        is_token_expired,
        verify_password,
    )


@unittest.skipUnless(_HAVE_DEPS, "passlib / python-jose not installed (run `uv sync`)")
class PasswordHashingTest(unittest.TestCase):
    def test_hash_then_verify_roundtrip(self):
        h = hash_password("hunter2")
        self.assertTrue(verify_password("hunter2", h))

    def test_wrong_password_rejected(self):
        h = hash_password("hunter2")
        self.assertFalse(verify_password("hunter3", h))

    def test_empty_password_rejected(self):
        with self.assertRaises(AuthError):
            hash_password("")

    def test_oversize_password_rejected(self):
        # bcrypt 72-byte limit
        with self.assertRaises(AuthError):
            hash_password("x" * 73)

    def test_verify_handles_garbage_hash(self):
        self.assertFalse(verify_password("anything", "not-a-real-hash"))


@unittest.skipUnless(_HAVE_DEPS, "passlib / python-jose not installed (run `uv sync`)")
class JwtTest(unittest.TestCase):
    def test_create_then_decode_roundtrip(self):
        token = create_access_token(subject="woonam")
        payload = decode_access_token(token)
        self.assertEqual(payload.subject, "woonam")
        self.assertFalse(payload.is_live_enabled)
        self.assertGreater(payload.expires_at, payload.issued_at)

    def test_live_flag_carried_through(self):
        token = create_access_token(subject="woonam", is_live_enabled=True)
        payload = decode_access_token(token)
        self.assertTrue(payload.is_live_enabled)

    def test_garbage_token_rejected(self):
        with self.assertRaises(AuthError):
            decode_access_token("this.is.not.a.token")

    def test_tampered_token_rejected(self):
        token = create_access_token(subject="woonam")
        tampered = token[:-2] + ("aa" if not token.endswith("aa") else "bb")
        with self.assertRaises(AuthError):
            decode_access_token(tampered)

    def test_expired_token_detected(self):
        # Issue a token that expires 1 minute ago
        token = create_access_token(subject="woonam", expires_minutes=-1)
        payload = decode_access_token(token)
        self.assertTrue(is_token_expired(payload))

    def test_active_token_not_expired(self):
        token = create_access_token(subject="woonam", expires_minutes=60)
        payload = decode_access_token(token)
        self.assertFalse(is_token_expired(payload))

    def test_token_expired_helper_accepts_explicit_now(self):
        token = create_access_token(subject="woonam", expires_minutes=60)
        payload = decode_access_token(token)
        future = datetime.now(UTC) + timedelta(hours=2)
        self.assertTrue(is_token_expired(payload, now=future))


@unittest.skipUnless(_HAVE_DEPS, "deps not installed")
class JwtMissingSecretTest(unittest.TestCase):
    """The startup gate enforces 'live mode requires secret'. But the
    primitives themselves should also refuse if asked to operate
    without a secret — defence in depth."""

    def test_create_refuses_with_empty_secret(self):
        from core.config import reset_settings_cache

        old = os.environ.get("JWT_SECRET_KEY")
        os.environ["JWT_SECRET_KEY"] = ""
        reset_settings_cache()
        try:
            with self.assertRaises(AuthError):
                create_access_token(subject="woonam")
        finally:
            if old is not None:
                os.environ["JWT_SECRET_KEY"] = old
            reset_settings_cache()


if __name__ == "__main__":
    unittest.main()
