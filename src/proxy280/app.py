import logging
from collections.abc import Callable
from datetime import date, datetime
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

from proxy280 import __version__
from proxy280.blocklist import (
    BlocklistService,
    BlocklistUnavailable,
    Fetch,
    urllib_fetch,
)
from proxy280.config import Settings

logger = logging.getLogger(__name__)


def create_server(
    settings: Settings,
    *,
    fetch: Fetch | None = None,
    today: Callable[[], date] | None = None,
) -> ThreadingHTTPServer:
    tz = ZoneInfo(settings.timezone)
    service = BlocklistService(
        fetch or urllib_fetch(settings.fetch_timeout, f"proxy280/{__version__}"),
        settings.blocklist_url_template,
        settings.refresh_interval,
        settings.retry_interval,
        today or (lambda: datetime.now(tz).date()),
    )

    class Handler(BaseHTTPRequestHandler):
        server_version = f"proxy280/{__version__}"

        def do_GET(self) -> None:
            match urlsplit(self.path).path:
                case "/blocklist":
                    self._blocklist()
                case "/allowlist":
                    self._allowlist()
                case "/healthz":
                    self._send(HTTPStatus.OK, "ok\n")
                case _:
                    self._send(HTTPStatus.NOT_FOUND, "not found\n")

        def _blocklist(self) -> None:
            try:
                blocklist = service.get()
            except BlocklistUnavailable as e:
                self._send(HTTPStatus.SERVICE_UNAVAILABLE, f"{e}\n")
                return
            self._send(
                HTTPStatus.OK,
                blocklist.content,
                {"X-Blocklist-Month": str(blocklist.month)},
            )

        def _allowlist(self) -> None:
            # マウントしたファイルの変更を再起動なしで反映するため毎回読む
            try:
                content = settings.allowlist_path.read_text(encoding="utf-8")
            except FileNotFoundError:
                self._send(HTTPStatus.NOT_FOUND, "allowlist not found\n")
                return
            self._send(HTTPStatus.OK, content)

        def _send(
            self,
            status: HTTPStatus,
            text: str,
            headers: dict[str, str] | None = None,
        ) -> None:
            body = text.encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            for name, value in (headers or {}).items():
                self.send_header(name, value)
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format: str, *args: object) -> None:
            # ヘルスチェックは 30 秒ごとに来るのでアクセスログに残さない
            if urlsplit(self.path).path == "/healthz":
                return
            logger.info("%s %s", self.address_string(), format % args)

    server = ThreadingHTTPServer((settings.host, settings.port), Handler)
    server.daemon_threads = True
    return server
