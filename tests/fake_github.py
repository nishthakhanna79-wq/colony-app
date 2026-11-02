"""A stand-in for the parts of the GitHub API the app uses. In memory, for tests only."""

from __future__ import annotations

import base64
import hashlib
import threading
import uuid

from flask import Flask, Response, jsonify, request


def _sha() -> str:
    return hashlib.sha1(uuid.uuid4().bytes).hexdigest()


class FakeGitHub:
    def __init__(self, repo: str = "lab/colony-data", token: str = "good-token", branch: str = "main"):
        self.repo, self.token, self.branch = repo, token, branch
        self.blobs: dict[str, bytes] = {}
        self.trees: dict[str, dict[str, str]] = {}
        self.commits: dict[str, dict] = {}
        self.ref: str | None = None
        self.lock = threading.Lock()
        self.update_delay_hook = None  # Called just before a ref update, to simulate a race.
        self.commit_messages: list[str] = []
        self.app = self._build()

    def files_at_head(self) -> dict[str, bytes]:
        tree = self.trees[self.commits[self.ref]["tree"]]
        return {path: self.blobs[sha] for path, sha in tree.items()}

    def outside_commit(self, files: dict[str, bytes], message: str = "edit outside the app") -> None:
        with self.lock:
            tree = dict(self.trees[self.commits[self.ref]["tree"]])
            for path, content in files.items():
                sha = _sha()
                self.blobs[sha] = content
                tree[path] = sha
            tree_sha = _sha()
            self.trees[tree_sha] = tree
            commit_sha = _sha()
            self.commits[commit_sha] = {"tree": tree_sha, "parents": [self.ref], "message": message}
            self.ref = commit_sha
            self.commit_messages.append(message)

    def _build(self) -> Flask:
        app = Flask(__name__)
        prefix = f"/repos/{self.repo}"

        @app.before_request
        def check_token():
            if request.headers.get("Authorization") != f"Bearer {self.token}":
                return jsonify(message="Bad credentials"), 401
            if not request.path.startswith(prefix):
                return jsonify(message="Not Found"), 404
            return None

        @app.get(f"{prefix}/git/ref/heads/<branch>")
        def get_ref(branch):
            if self.ref is None:
                return jsonify(message="Git Repository is empty."), 409
            if branch != self.branch:
                return jsonify(message="Not Found"), 404
            return jsonify(object={"sha": self.ref})

        @app.get(f"{prefix}/contents/<path:path>")
        def get_contents(path):
            ref = request.args.get("ref", self.ref)
            commit = self.commits.get(ref)
            if commit is None:
                return jsonify(message="No commit found"), 404
            sha = self.trees[commit["tree"]].get(path)
            if sha is None:
                return jsonify(message="Not Found"), 404
            return Response(self.blobs[sha], mimetype="application/octet-stream")

        @app.put(f"{prefix}/contents/<path:path>")
        def put_contents(path):
            body = request.get_json()
            with self.lock:
                if self.ref is not None:
                    return jsonify(message="sha wasn't supplied"), 422
                blob = _sha()
                self.blobs[blob] = base64.b64decode(body["content"])
                tree = _sha()
                self.trees[tree] = {path: blob}
                commit = _sha()
                self.commits[commit] = {"tree": tree, "parents": [], "message": body["message"]}
                self.ref = commit
                self.commit_messages.append(body["message"])
            return jsonify(commit={"sha": commit}), 201

        @app.get(f"{prefix}/git/commits/<sha>")
        def get_commit(sha):
            commit = self.commits.get(sha)
            if commit is None:
                return jsonify(message="Not Found"), 404
            return jsonify(sha=sha, tree={"sha": commit["tree"]})

        @app.get(f"{prefix}/commits")
        def list_commits():
            items = []
            sha = self.ref
            while sha and len(items) < int(request.args.get("per_page", 30)):
                commit = self.commits[sha]
                items.append({"sha": sha, "commit": {"message": commit["message"], "committer": {"date": "2026-11-02T22:05:00Z"}}})
                sha = commit["parents"][0] if commit["parents"] else None
            return jsonify(items)

        @app.post(f"{prefix}/git/blobs")
        def post_blob():
            if self.ref is None:
                return jsonify(message="Git Repository is empty."), 409
            body = request.get_json()
            sha = _sha()
            self.blobs[sha] = base64.b64decode(body["content"])
            return jsonify(sha=sha), 201

        @app.post(f"{prefix}/git/trees")
        def post_tree():
            body = request.get_json()
            tree = dict(self.trees[body["base_tree"]])
            for entry in body["tree"]:
                tree[entry["path"]] = entry["sha"]
            sha = _sha()
            self.trees[sha] = tree
            return jsonify(sha=sha), 201

        @app.post(f"{prefix}/git/commits")
        def post_commit():
            body = request.get_json()
            sha = _sha()
            self.commits[sha] = {"tree": body["tree"], "parents": body["parents"], "message": body["message"]}
            return jsonify(sha=sha), 201

        @app.patch(f"{prefix}/git/refs/heads/<branch>")
        def patch_ref(branch):
            body = request.get_json()
            if self.update_delay_hook:
                hook, self.update_delay_hook = self.update_delay_hook, None
                hook()
            with self.lock:
                new = self.commits[body["sha"]]
                if self.ref not in new["parents"] and not body.get("force"):
                    return jsonify(message="Update is not a fast forward"), 422
                self.ref = body["sha"]
                self.commit_messages.append(new["message"])
            return jsonify(object={"sha": self.ref})

        return app
