import threading
import urllib.error
import urllib.request
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from datetime import date
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from proxy280.app import create_server
from proxy280.blocklist import Fetch, Response, urllib_fetch
from proxy280.config import Settings


def upstream_error(url: str, headers: Mapping[str, str]) -> Response:
    return Response(500, None, b"")


@contextmanager
def running_server(
    tmp_path: Path,
    fetch: Fetch = upstream_error,
    allowlist: str | None = "@@||ok.example^\n",
    url_template: str = "https://example.test/{year}{month:02d}.txt",
) -> Iterator[str]:
    """テスト用にサーバを空きポートで起動し、ベース URL を返す。"""
    allowlist_path = tmp_path / "allowlist.txt"
    if allowlist is not None:
        allowlist_path.write_text(allowlist, encoding="utf-8")
    settings = Settings(
        blocklist_url_template=url_template,
        allowlist_path=allowlist_path,
        host="127.0.0.1",
        port=0,
    )
    with create_server(settings, fetch=fetch, today=lambda: date(2026, 10, 1)) as srv:
        thread = threading.Thread(target=srv.serve_forever, daemon=True)
        thread.start()
        try:
            yield f"http://127.0.0.1:{srv.server_address[1]}"
        finally:
            srv.shutdown()
            thread.join()


def request(url: str) -> tuple[int, Mapping[str, str], str]:
    try:
        with urllib.request.urlopen(url, timeout=5) as r:
            return r.status, r.headers, r.read().decode("utf-8")
    except urllib.error.HTTPError as e:
        with e:
            return e.code, e.headers, e.read().decode("utf-8")


def test_blocklist_returns_blocklist(tmp_path: Path) -> None:
    def fetch(url: str, headers: Mapping[str, str]) -> Response:
        return Response(200, None, b"||ad.example^\n")

    with running_server(tmp_path, fetch) as base:
        status, headers, body = request(base + "/blocklist")

    assert status == 200
    assert body == "||ad.example^\n"
    assert headers["X-Blocklist-Month"] == "2026-10"
    assert headers["Content-Type"] == "text/plain; charset=utf-8"


def test_blocklist_returns_503_when_upstream_unavailable(tmp_path: Path) -> None:
    with running_server(tmp_path) as base:
        assert request(base + "/blocklist")[0] == 503


def test_allowlist_reflects_file_changes(tmp_path: Path) -> None:
    with running_server(tmp_path) as base:
        assert request(base + "/allowlist")[2] == "@@||ok.example^\n"
        (tmp_path / "allowlist.txt").write_text("@@||new.example^\n")
        assert request(base + "/allowlist")[2] == "@@||new.example^\n"


def test_allowlist_returns_404_when_missing(tmp_path: Path) -> None:
    with running_server(tmp_path, allowlist=None) as base:
        assert request(base + "/allowlist")[0] == 404


def test_healthz(tmp_path: Path) -> None:
    with running_server(tmp_path) as base:
        assert request(base + "/healthz")[:3:2] == (200, "ok\n")


def test_unknown_path_returns_404(tmp_path: Path) -> None:
    with running_server(tmp_path) as base:
        assert request(base + "/nope")[0] == 404


class EtagHandler(BaseHTTPRequestHandler):
    """/list は ETag 付きで返し、If-None-Match が一致すれば 304。他は 404。"""

    def do_GET(self) -> None:
        if self.path != "/list":
            self.send_error(404)
            return
        if self.headers["If-None-Match"] == '"v1"':
            self.send_response(304)
            self.send_header("ETag", '"v1"')
            self.end_headers()
            return
        body = "\ufeff||ad.example^\n".encode()
        self.send_response(200)
        self.send_header("ETag", '"v1"')
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: object) -> None:
        pass


@pytest.fixture
def etag_server() -> Iterator[str]:
    with ThreadingHTTPServer(("127.0.0.1", 0), EtagHandler) as srv:
        thread = threading.Thread(target=srv.serve_forever, daemon=True)
        thread.start()
        yield f"http://127.0.0.1:{srv.server_address[1]}"
        srv.shutdown()
        thread.join()


def test_urllib_fetch_handles_200_304_404(etag_server: str) -> None:
    fetch = urllib_fetch(timeout=5, user_agent="test")

    ok = fetch(etag_server + "/list", {})
    assert (ok.status, ok.etag) == (200, '"v1"')
    assert ok.body == "\ufeff||ad.example^\n".encode()

    # urllib が例外にするステータスも Response として返る
    assert fetch(etag_server + "/list", {"If-None-Match": '"v1"'}).status == 304
    assert fetch(etag_server + "/missing", {}).status == 404
