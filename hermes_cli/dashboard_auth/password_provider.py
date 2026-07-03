"""Local username/password dashboard auth provider.

This is intentionally process-local and opt-in. It exists for self-hosted
LAN deployments where the official desktop remote flow should still use the
dashboard auth gate (/login cookies + ws tickets), but an operator wants a
simple static username/password instead of an external OAuth provider.
"""
from __future__ import annotations

import hmac
import secrets
import time
from dataclasses import dataclass
from typing import Optional

from hermes_cli.dashboard_auth.base import (
    DashboardAuthProvider,
    InvalidCodeError,
    LoginStart,
    ProviderError,
    RefreshExpiredError,
    Session,
)


@dataclass
class _TokenRecord:
    session: Session


class StaticPasswordDashboardAuthProvider(DashboardAuthProvider):
    name = "password"
    display_name = "Password"
    supports_password = True

    def __init__(self, *, username: str, password: str, ttl_seconds: int = 12 * 60 * 60) -> None:
        self._username = username
        self._password = password
        self._ttl_seconds = max(300, int(ttl_seconds or 0))
        self._tokens: dict[str, _TokenRecord] = {}

    def start_login(self, *, redirect_uri: str) -> LoginStart:
        # Password login is handled by POST /auth/password from the login form.
        raise ProviderError("password provider does not support OAuth redirects")

    def complete_login(
        self,
        *,
        code: str,
        state: str,
        code_verifier: str,
        redirect_uri: str,
    ) -> Session:
        raise InvalidCodeError("password provider does not support OAuth callbacks")

    def complete_password_login(self, *, username: str, password: str) -> Session:
        if not hmac.compare_digest(username.encode(), self._username.encode()):
            raise InvalidCodeError("invalid username or password")
        if not hmac.compare_digest(password.encode(), self._password.encode()):
            raise InvalidCodeError("invalid username or password")

        now = int(time.time())
        access_token = secrets.token_urlsafe(32)
        refresh_token = secrets.token_urlsafe(32)
        session = Session(
            user_id=self._username,
            email=f"{self._username}@local",
            display_name=self._username,
            org_id="local",
            provider=self.name,
            expires_at=now + self._ttl_seconds,
            access_token=access_token,
            refresh_token=refresh_token,
        )
        self._tokens[access_token] = _TokenRecord(session=session)
        self._tokens[refresh_token] = _TokenRecord(session=session)
        return session

    def verify_session(self, *, access_token: str) -> Optional[Session]:
        record = self._tokens.get(access_token)
        if record is None:
            return None
        if record.session.expires_at <= int(time.time()):
            self._tokens.pop(access_token, None)
            return None
        return record.session

    def refresh_session(self, *, refresh_token: str) -> Session:
        record = self._tokens.get(refresh_token)
        if record is None or record.session.expires_at <= int(time.time()):
            raise RefreshExpiredError("password session expired")
        return self.complete_password_login(username=self._username, password=self._password)

    def revoke_session(self, *, refresh_token: str) -> None:
        record = self._tokens.pop(refresh_token, None)
        if record is not None:
            self._tokens.pop(record.session.access_token, None)
