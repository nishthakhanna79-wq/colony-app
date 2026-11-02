import socket
import sys
import threading
from pathlib import Path

import pytest
from werkzeug.serving import make_server

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from fake_github import FakeGitHub  # noqa: E402


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


@pytest.fixture
def github():
    fake = FakeGitHub()
    port = free_port()
    server = make_server("127.0.0.1", port, fake.app, threaded=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    fake.url = f"http://127.0.0.1:{port}"
    yield fake
    server.shutdown()
    thread.join()
