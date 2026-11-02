"""Where the data files live.

GitHubBackend is the live store. LocalBackend exists for development and tests.
Both expose the same three operations and report a lost race with Conflict.
"""

from __future__ import annotations

import base64
import json
import os
import threading
import uuid
from pathlib import Path
from typing import Protocol
from urllib.parse import quote

import requests


class Conflict(Exception):
    """Another writer moved the branch after we read it."""


class StoreError(Exception):
    """The data store could not complete a request. Safe to show to a user."""


class Backend(Protocol):
    def head(self) -> str | None: ...

    def read(self, path: str, ref: str) -> bytes | None: ...

    def commit(self, parent: str | None, files: dict[str, bytes], message: str) -> str: ...


class LocalBackend:
    def __init__(self, root: str | Path):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self._head_file = self.root / ".head"
        self._lock = threading.Lock()

    def head(self) -> str | None:
        if not self._head_file.exists():
            return None
        return self._head_file.read_text().strip() or None

    def read(self, path: str, ref: str) -> bytes | None:
        target = self.root / path
        return target.read_bytes() if target.exists() else None

    def commit(self, parent: str | None, files: dict[str, bytes], message: str) -> str:
        with self._lock:
            if self.head() != parent:
                raise Conflict
            for path, content in files.items():
                target = self.root / path
                target.parent.mkdir(parents=True, exist_ok=True)
                temp = target.with_name(f".{target.name}.{uuid.uuid4().hex}")
                temp.write_bytes(content)
                os.replace(temp, target)
            new_head = uuid.uuid4().hex
            self._head_file.write_text(new_head)
            with (self.root / ".history").open("a") as history:
                history.write(json.dumps({"head": new_head, "message": message}) + "\n")
            return new_head


class GitHubBackend:
    """Stores files in one branch of a private repository.

    Writes use the Git Data API so one save is one commit, even when it touches
    several files. The final ref update refuses anything but a fast-forward, which
    is what turns a lost race into a Conflict instead of overwritten data.
    """

    def __init__(
        self,
        repo: str,
        token: str,
        branch: str = "main",
        api_url: str = "https://api.github.com",
        timeout: float = 20,
        session: requests.Session | None = None,
    ):
        self.repo = repo
        self.branch = branch
        self.api_url = api_url.rstrip("/")
        self.timeout = timeout
        self.session = session or requests.Session()
        self.session.headers.update(
            {
                "Authorization": f"Bearer {token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
            }
        )

    def _request(self, method: str, path: str, **kwargs) -> requests.Response:
        url = f"{self.api_url}/repos/{self.repo}{path}"
        try:
            return self.session.request(method, url, timeout=self.timeout, **kwargs)
        except requests.RequestException as exc:
            # The exception text is dropped on purpose so nothing about the request leaks.
            raise StoreError(
                "GitHub did not respond. Refresh and check whether your change saved "
                "before trying again."
            ) from exc

    def _fail(self, response: requests.Response, action: str) -> StoreError:
        if response.status_code == 401:
            detail = "The GitHub token is invalid or has expired."
        elif response.status_code == 403:
            detail = "GitHub refused the request. Check the token's permissions and rate limit."
        elif response.status_code == 404:
            detail = "The data repo or branch was not found. Check the repo name and token."
        else:
            detail = f"GitHub returned status {response.status_code}."
        return StoreError(f"Could not {action}. {detail}")

    def _json(self, response: requests.Response, action: str) -> dict:
        if not response.ok:
            raise self._fail(response, action)
        return response.json()

    def head(self) -> str | None:
        response = self._request("GET", f"/git/ref/heads/{self.branch}")
        if response.status_code == 200:
            return response.json()["object"]["sha"]
        if response.status_code == 409:  # GitHub's answer for a repo with no commits.
            return None
        raise self._fail(response, "read the data repo")

    def read(self, path: str, ref: str) -> bytes | None:
        response = self._request(
            "GET",
            f"/contents/{quote(path)}",
            params={"ref": ref},
            headers={"Accept": "application/vnd.github.raw+json"},
        )
        if response.status_code == 404:
            return None
        if not response.ok:
            raise self._fail(response, f"read {path}")
        return response.content

    def _initialize(self) -> str:
        """Blob and tree calls fail on an empty repo, so make the first commit by hand."""
        body = {
            "message": "Start colony data",
            "content": base64.b64encode(b"# Colony data\n").decode(),
            "branch": self.branch,
        }
        response = self._request("PUT", "/contents/README.md", json=body)
        self._json(response, "set up the data repo")
        head = self.head()
        if head is None:
            raise StoreError("Could not set up the data repo.")
        return head

    def commit(self, parent: str | None, files: dict[str, bytes], message: str) -> str:
        if parent is None:
            current = self.head()
            if current is not None:
                raise Conflict
            parent = self._initialize()

        action = "save your change"
        parent_commit = self._json(self._request("GET", f"/git/commits/{parent}"), action)
        entries = []
        for path, content in files.items():
            blob = self._json(
                self._request(
                    "POST",
                    "/git/blobs",
                    json={"content": base64.b64encode(content).decode(), "encoding": "base64"},
                ),
                action,
            )
            entries.append({"path": path, "mode": "100644", "type": "blob", "sha": blob["sha"]})

        tree = self._json(
            self._request(
                "POST",
                "/git/trees",
                json={"base_tree": parent_commit["tree"]["sha"], "tree": entries},
            ),
            action,
        )
        new_commit = self._json(
            self._request(
                "POST",
                "/git/commits",
                json={"message": message, "tree": tree["sha"], "parents": [parent]},
            ),
            action,
        )
        response = self._request(
            "PATCH",
            f"/git/refs/heads/{self.branch}",
            json={"sha": new_commit["sha"], "force": False},
        )
        if response.status_code == 422 and "fast forward" in response.text.lower():
            raise Conflict
        self._json(response, action)
        return new_commit["sha"]
