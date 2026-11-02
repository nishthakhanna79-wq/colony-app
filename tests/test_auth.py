from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from colony.auth import (
    BAD_LOGIN_MESSAGE,
    FAILURE_LIMIT,
    FAILURE_WINDOW_SECONDS,
    LoginThrottle,
    password_matches,
    usable_password,
)

GOOD = "correct horse battery staple"
APP = "../streamlit_app.py"


def test_only_the_exact_password_matches():
    assert password_matches(GOOD, GOOD)


@pytest.mark.parametrize("attempt", ["", "wrong", GOOD.upper(), GOOD + " ", " " + GOOD])
def test_anything_else_is_refused(attempt):
    assert not password_matches(attempt, GOOD)


def test_missing_or_short_passwords_are_never_usable():
    assert usable_password({"password": GOOD}) == GOOD
    assert usable_password({"password": "short"}) is None
    assert usable_password({"password": ""}) is None
    assert usable_password({}) is None
    assert usable_password(None) is None


class Clock:
    now = 1000.0

    def __call__(self):
        return self.now


def test_repeated_failures_pause_sign_in_then_it_recovers():
    clock = Clock()
    throttle = LoginThrottle(clock)
    for _ in range(FAILURE_LIMIT):
        assert not throttle.is_locked()
        throttle.record_failure()
    assert throttle.is_locked()
    clock.now += FAILURE_WINDOW_SECONDS + 1
    assert not throttle.is_locked()


def test_the_example_secrets_never_use_the_auth_section():
    # [auth] would make Streamlit count this as the one private app.
    example = (Path(__file__).parent.parent / ".streamlit" / "secrets.toml.example").read_text()
    assert "\n[auth" not in example


def fresh_app(monkeypatch, tmp_path, login=None):
    monkeypatch.delenv("COLONY_DEV_USER", raising=False)
    monkeypatch.setenv("COLONY_LOCAL_DATA", str(tmp_path))
    app = AppTest.from_file(APP, default_timeout=30)
    if login is not None:
        app.secrets["login"] = login
    return app


def test_app_is_closed_when_no_password_is_configured(monkeypatch, tmp_path):
    app = fresh_app(monkeypatch, tmp_path).run()
    assert not app.exception
    assert [e.value for e in app.error] == ["Sign-in isn't set up yet, so this app is closed."]
    assert not app.metric


def test_app_is_closed_when_the_password_is_too_short(monkeypatch, tmp_path):
    app = fresh_app(monkeypatch, tmp_path, {"password": "short"}).run()
    assert not app.exception
    assert not app.text_input
    assert [e.value for e in app.error] == ["Sign-in isn't set up yet, so this app is closed."]


def test_dev_user_is_ignored_once_a_real_login_is_configured(monkeypatch, tmp_path):
    monkeypatch.setenv("COLONY_LOCAL_DATA", str(tmp_path))
    monkeypatch.setenv("COLONY_DEV_USER", "dev")
    app = AppTest.from_file(APP, default_timeout=30)
    app.secrets["login"] = {"password": GOOD}
    app.run()
    assert not app.exception
    assert not app.metric
    assert [t.label for t in app.text_input] == ["Password"]


def sign_in(app, password):
    app.text_input[0].set_value(password)
    app.button[0].click().run()
    return app


def test_wrong_password_shows_no_data_and_right_password_opens_the_app(monkeypatch, tmp_path):
    app = fresh_app(monkeypatch, tmp_path, {"password": GOOD}).run()
    assert not app.metric
    sign_in(app, "wrong-password-here")
    assert not app.exception
    assert [e.value for e in app.error] == [BAD_LOGIN_MESSAGE]
    assert not app.metric
    sign_in(app, GOOD)
    assert not app.exception
    assert app.metric, "dashboard should load after signing in"
    assert not app.error
