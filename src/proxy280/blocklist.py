import logging
import threading
import time
import urllib.error
import urllib.request
from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from datetime import date

logger = logging.getLogger(__name__)


@dataclass(frozen=True, order=True)
class YearMonth:
    year: int
    month: int

    @classmethod
    def of(cls, d: date) -> YearMonth:
        return cls(d.year, d.month)

    def previous(self) -> YearMonth:
        if self.month == 1:
            return YearMonth(self.year - 1, 12)
        return YearMonth(self.year, self.month - 1)

    def __str__(self) -> str:
        return f"{self.year}-{self.month:02d}"


@dataclass(frozen=True)
class Blocklist:
    month: YearMonth
    content: str
    etag: str | None


@dataclass(frozen=True)
class Response:
    status: int
    etag: str | None
    body: bytes


# URL とリクエストヘッダを受け取り、上流のレスポンスを返す。
# 通信エラーは OSError を送出する
Fetch = Callable[[str, Mapping[str, str]], Response]


def urllib_fetch(timeout: float, user_agent: str) -> Fetch:
    def fetch(url: str, headers: Mapping[str, str]) -> Response:
        request = urllib.request.Request(
            url, headers={"User-Agent": user_agent, **headers}
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                etag = response.headers["ETag"]
                return Response(response.status, etag, response.read())
        except urllib.error.HTTPError as e:
            # 304 や 4xx/5xx は例外になるため、ステータスとして返す
            with e:
                return Response(e.code, e.headers["ETag"], b"")

    return fetch


class BlocklistUnavailable(Exception):
    pass


class InvalidBlocklist(Exception):
    pass


class BlocklistService:
    """280blocker の月次リストを取得・保持する。

    - 上流は月ごとの URL のまま中身を随時更新するため、refresh_interval ごとに
      ETag による条件付きリクエストで更新を確認する
      （未更新なら 304 が返り、本体を受け取らない）
    - 月が変わったら refresh_interval を待たずに今月分を取りに行く
    - 取得に失敗したら直前に保持していたリストを返し続け、retry_interval 後に再試行する
    - 何も保持していない状態で今月分が取れなければ前月分を試す（月初の公開前対策）
    """

    def __init__(
        self,
        fetch: Fetch,
        url_template: str,
        refresh_interval: float,
        retry_interval: float,
        today: Callable[[], date],
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._fetch_response = fetch
        self._url_template = url_template
        self._refresh_interval = refresh_interval
        self._retry_interval = retry_interval
        self._today = today
        self._clock = clock
        self._lock = threading.Lock()
        self._current: Blocklist | None = None
        # 最後に上流と内容を確認できた時刻 (200 / 304)
        self._checked_at: float | None = None
        # 直近の取得失敗の時刻。成功したら None に戻す
        self._failed_at: float | None = None

    def get(self) -> Blocklist:
        target = YearMonth.of(self._today())

        if not self._should_refresh(target):
            assert self._current is not None
            return self._current

        with self._lock:
            # 待っている間に他のリクエストが更新しているかもしれない
            if not self._should_refresh(target):
                assert self._current is not None
                return self._current
            return self._refresh(target)

    def _should_refresh(self, target: YearMonth) -> bool:
        if self._current is None:
            return True
        now = self._clock()
        if self._failed_at is not None and now - self._failed_at < self._retry_interval:
            return False
        if self._current.month < target:
            return True
        assert self._checked_at is not None
        return now - self._checked_at >= self._refresh_interval

    def _refresh(self, target: YearMonth) -> Blocklist:
        candidates = [target] if self._current else [target, target.previous()]

        for month in candidates:
            try:
                self._current = self._fetch(month)
            except (OSError, InvalidBlocklist) as e:
                logger.warning("failed to fetch blocklist for %s: %r", month, e)
                continue
            now = self._clock()
            self._checked_at = now
            # 前月分で代用したときは、今月分の再試行もバックオフさせる
            self._failed_at = now if month < target else None
            return self._current

        self._failed_at = self._clock()
        if self._current is None:
            raise BlocklistUnavailable("no blocklist is available")
        logger.warning("serving stale blocklist for %s", self._current.month)
        return self._current

    def _fetch(self, month: YearMonth) -> Blocklist:
        url = self._url_template.format(year=month.year, month=month.month)
        headers = {}
        if self._current is not None and self._current.etag is not None:
            headers["If-None-Match"] = self._current.etag
        response = self._fetch_response(url, headers)

        if response.status == 304 and self._current is not None:
            logger.info("blocklist for %s is not modified", month)
            return replace(self._current, month=month)
        if response.status != 200:
            raise InvalidBlocklist(f"unexpected status {response.status} from {url}")

        content = response.body.decode("utf-8-sig")  # 上流は BOM 付き
        _validate(content)
        logger.info("fetched blocklist for %s", month)
        return Blocklist(month, content, response.etag)


def _validate(content: str) -> None:
    # エラーページ等を誤って配らないよう、`||domain^` 形式のルールを含むか確認する
    if not any(line.startswith("||") for line in content.splitlines()):
        raise InvalidBlocklist("response does not contain any domain rules")
