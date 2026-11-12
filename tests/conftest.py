from __future__ import annotations

import threading
from collections.abc import Iterator
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

pytest_plugins = ["pytester"]


@dataclass(frozen=True)
class Services:
    baseline: str
    candidate: str

    def write_config(self, directory: Path, *cases: str, rules: str = "") -> Path:
        """A config and one GET case per path. `/same` matches; `/diff` differs in `total`."""
        corpus = directory / "cases"
        corpus.mkdir(exist_ok=True)
        for path in cases:
            name = path.strip("/")
            (corpus / f"{name}.yaml").write_text(
                f"name: {name}\nrequest: {{method: GET, path: {path}}}\n"
            )
        config = directory / "migrate-guard.yaml"
        config.write_text(
            "version: 1\n"
            f"baseline: {{target: http, url: '{self.baseline}'}}\n"
            f"candidate: {{target: http, url: '{self.candidate}'}}\n"
            "corpus: cases/\n" + rules
        )
        return config


def _handler(total: str) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            # Raw text, so the numbers reach the tool exactly as written: 2.675, not a float.
            value = total if self.path == "/diff" else "1.00"
            data = f'{{"total": {value}, "password": "hunter2"}}'.encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, *args: object) -> None:
            pass

    return Handler


@pytest.fixture(scope="session")
def services() -> Iterator[Services]:
    """Two tiny JSON services on loopback: the same except for `total` on `/diff`."""
    servers = [ThreadingHTTPServer(("127.0.0.1", 0), _handler(t)) for t in ("2.675", "2.67")]
    for server in servers:
        threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        b, c = (f"http://127.0.0.1:{s.server_address[1]}" for s in servers)
        yield Services(b, c)
    finally:
        for server in servers:
            server.shutdown()
            server.server_close()
