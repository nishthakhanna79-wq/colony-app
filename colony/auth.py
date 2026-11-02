"""The login gate. Nothing about the colony loads until this passes.

Everyone in the lab shares one password, kept in the [login] section of the app's Secrets.
There is deliberately no [auth] section: on Community Cloud an app with [auth] counts as your
one private app.
"""

from __future__ import annotations

import hmac
import os
import threading
import time
from collections.abc import Callable, Mapping

import streamlit as st

APP_NAME = "Colony"
SHARED_USER = "lab"  # Name on commits and records, since a shared login can't tell people apart.
MIN_PASSWORD_LENGTH = 16
SESSION_HOURS = 12
FAILURE_LIMIT = 20
FAILURE_WINDOW_SECONDS = 15 * 60
BAD_LOGIN_MESSAGE = "That password isn't right."
LOCKED_MESSAGE = "Too many failed sign-ins. Wait 15 minutes and try again."
_SIGNED_IN_AT_KEY = "colony_signed_in_at"


def usable_password(section: Mapping | None) -> str | None:
    """The shared password, or None if it's missing or too short to trust."""
    password = str((section or {}).get("password", ""))
    return password if len(password) >= MIN_PASSWORD_LENGTH else None


def password_matches(attempt: str, expected: str) -> bool:
    return hmac.compare_digest(attempt.encode(), expected.encode())


class LoginThrottle:
    """Limits password guessing. The app's URL is public, so anyone can try."""

    def __init__(self, clock: Callable[[], float] = time.monotonic):
        self.clock = clock
        self.lock = threading.Lock()
        self.failures: list[float] = []

    def _recent(self) -> list[float]:
        cutoff = self.clock() - FAILURE_WINDOW_SECONDS
        self.failures = [moment for moment in self.failures if moment > cutoff]
        return self.failures

    def is_locked(self) -> bool:
        with self.lock:
            return len(self._recent()) >= FAILURE_LIMIT

    def record_failure(self) -> None:
        with self.lock:
            self._recent().append(self.clock())


@st.cache_resource(show_spinner=False)
def _throttle() -> LoginThrottle:
    return LoginThrottle()


def secret_section(name: str) -> dict | None:
    try:
        return dict(st.secrets[name]) if name in st.secrets else None
    except Exception:  # Streamlit raises when no secrets file exists at all.
        return None


def login_configured() -> bool:
    return secret_section("login") is not None


def _dev_user() -> str | None:
    """Local development only. Ignored as soon as a real login or real data is configured."""
    if login_configured() or secret_section("github") is not None:
        return None
    return os.environ.get("COLONY_DEV_USER") or None


def log_out() -> None:
    for key in list(st.session_state.keys()):
        del st.session_state[key]


def _signed_in() -> bool:
    signed_in_at = st.session_state.get(_SIGNED_IN_AT_KEY)
    if signed_in_at is None:
        return False
    if time.time() - signed_in_at > SESSION_HOURS * 3600:
        log_out()
        return False
    return True


def _show_login_form(expected: str) -> None:
    _, middle, _ = st.columns([1, 2, 1])
    with middle:
        st.title(APP_NAME)
        with st.form("login"):
            attempt = st.text_input("Password", type="password", autocomplete="current-password")
            submitted = st.form_submit_button("Sign in", type="primary", width="stretch")
        if not submitted:
            return
        throttle = _throttle()
        if throttle.is_locked():
            st.error(LOCKED_MESSAGE)
            return
        if not password_matches(attempt, expected):
            throttle.record_failure()
            time.sleep(1)
            st.error(BAD_LOGIN_MESSAGE)
            return
        st.session_state[_SIGNED_IN_AT_KEY] = time.time()
        st.rerun()


def require_user() -> str:
    dev_user = _dev_user()
    if dev_user:
        return dev_user

    expected = usable_password(secret_section("login"))
    if expected is None:
        st.title(APP_NAME)
        st.error("Sign-in isn't set up yet, so this app is closed.")
        st.stop()

    if not _signed_in():
        _show_login_form(expected)
        st.stop()
    return SHARED_USER
