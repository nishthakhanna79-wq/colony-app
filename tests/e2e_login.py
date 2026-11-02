"""End-to-end login check in a real browser, with a stand-in for GitHub.

Run: python tests/e2e_login.py
"""

import os
import socket
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

import requests
from flask import request
from playwright.sync_api import expect, sync_playwright
from werkzeug.serving import make_server

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tests"))
from fake_github import FakeGitHub  # noqa: E402

PASSWORD = "correct horse battery staple"


def free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def settle(page):
    page.wait_for_timeout(700)
    page.wait_for_function("!document.querySelector('[data-testid=\"stStatusWidget\"]')", timeout=20000)
    page.wait_for_timeout(400)


def sign_in(page, password):
    page.get_by_label("Password", exact=True).fill(password)
    page.get_by_role("button", name="Sign in").click()
    page.wait_for_timeout(1300)
    settle(page)


def main():
    work = Path(tempfile.mkdtemp())
    github = FakeGitHub()
    github_calls = []
    github.app.before_request(lambda: github_calls.append(request.path) and None)
    github_port = free_port()
    github_server = make_server("127.0.0.1", github_port, github.app, threaded=True)
    threading.Thread(target=github_server.serve_forever, daemon=True).start()

    app_port = free_port()
    app_url = f"http://localhost:{app_port}"
    secrets = work / "secrets.toml"
    secrets.write_text(
        f"""
[login]
password = "{PASSWORD}"

[github]
repo = "{github.repo}"
token = "{github.token}"
branch = "main"
api_url = "http://127.0.0.1:{github_port}"
"""
    )
    env = {k: v for k, v in os.environ.items() if not k.startswith("COLONY_")}
    log = (work / "app.log").open("w")
    app = subprocess.Popen(
        [sys.executable, "-m", "streamlit", "run", "streamlit_app.py", "--server.port", str(app_port), "--server.headless", "true", "--secrets.files", str(secrets)],
        cwd=ROOT,
        env=env,
        stdout=log,
        stderr=log,
    )
    try:
        for _ in range(120):
            try:
                if requests.get(f"http://127.0.0.1:{app_port}/_stcore/health", timeout=2).ok:
                    break
            except requests.RequestException:
                time.sleep(0.3)

        with sync_playwright() as p:
            browser = p.chromium.launch()
            context = browser.new_context()
            page = context.new_page()

            # 1. Logged out: only the sign-in form, on every URL, with no data read at all.
            for path in ("", "/mice", "/gels", "/settings"):
                page.goto(app_url + path)
                settle(page)
                expect(page.get_by_role("button", name="Sign in")).to_be_visible()
                body = page.locator('[data-testid="stApp"]').inner_text()
                assert "Dashboard" not in body and "Mice" not in body and "Genotype" not in body, body
                assert page.locator('[data-testid="stMetric"]').count() == 0
            assert github_calls == [], f"GitHub was read before login: {github_calls}"
            assert page.title() == "Colony", page.title()
            print("ok: logged out sees only the sign-in form, and GitHub was never contacted")

            # 2. Wrong and empty passwords are refused, with no data read.
            for attempt in ("wrong-password-entirely", "", PASSWORD.upper()):
                sign_in(page, attempt)
                expect(page.get_by_text("isn't right")).to_be_visible()
                assert page.locator('[data-testid="stMetric"]').count() == 0
            assert github_calls == [], f"GitHub was read after failed logins: {github_calls}"
            print("ok: wrong passwords are refused, and GitHub was never contacted")

            # 3. The right password opens the app.
            sign_in(page, PASSWORD)
            expect(page.get_by_role("heading", name="Dashboard")).to_be_visible()
            print("ok: the right password opens the app")

            # 4. A save reaches GitHub as one commit.
            page.get_by_role("link", name="Mice").click()
            settle(page)
            page.locator('[data-testid="stButtonGroup"]').get_by_text("Add", exact=True).first.click()
            settle(page)
            page.get_by_label("Ear tag", exact=True).fill("9001")
            page.get_by_role("button", name="Save mouse").click()
            settle(page)
            expect(page.get_by_text("Saved mouse 9001")).to_be_visible()
            assert b"9001" in github.files_at_head()["mice.csv"]
            assert github.commit_messages[-1] == "Add mouse 9001 [lab]", github.commit_messages
            print("ok: save reached GitHub:", github.commit_messages[-1])

            # 5. Another browser isn't signed in. Refreshing means signing in again.
            other = browser.new_context().new_page()
            other.goto(app_url)
            settle(other)
            expect(other.get_by_role("button", name="Sign in")).to_be_visible()
            page.reload()
            settle(page)
            expect(page.get_by_role("button", name="Sign in")).to_be_visible()
            assert page.locator('[data-testid="stMetric"]').count() == 0
            print("ok: another browser isn't signed in, and a refresh asks to sign in again")

            # 6. Logging out ends access.
            sign_in(page, PASSWORD)
            expect(page.get_by_role("heading", name="Dashboard")).to_be_visible()
            page.get_by_role("button", name="Log out").click()
            settle(page)
            expect(page.get_by_role("button", name="Sign in")).to_be_visible()
            assert page.locator('[data-testid="stMetric"]').count() == 0
            print("ok: log out ends access")

            # 7. Repeated wrong guesses pause sign-in, even for the right password.
            for attempt in range(20):
                sign_in(page, f"wrong-guess-number-{attempt}")
            sign_in(page, PASSWORD)
            expect(page.get_by_text("Too many failed sign-ins")).to_be_visible()
            assert page.locator('[data-testid="stMetric"]').count() == 0
            print("ok: 20 wrong guesses pause sign-in, even for the right password")
            browser.close()
    finally:
        app.terminate()
        github_server.shutdown()
        log.close()
        problems = [line for line in (work / "app.log").read_text().splitlines() if "Traceback" in line or "Error" in line]
        print("app log problems:", problems[:10])


if __name__ == "__main__":
    main()
