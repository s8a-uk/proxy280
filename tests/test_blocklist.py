import hashlib
import threading
import time
from collections.abc import Mapping
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from urllib.parse import urlsplit

import pytest

from proxy280.blocklist import (
    BlocklistService,
    BlocklistUnavailable,
    Response,
    YearMonth,
)

URL_TEMPLATE = "https://example.test/{year}{month:02d}.txt"
REFRESH = 3600
RETRY = 60


def etag_of(body: str) -> str:
    return '"' + hashlib.md5(body.encode()).hexdigest() + '"'


class FakeUpstream:
    """URL ごとに返すレスポンスを差し替えられる上流サーバ。

    本物と同じく本体のハッシュを ETag にし、If-None-Match が一致すれば 304 を返す。
    """

    def __init__(self, delay: float = 0) -> None:
        self.bodies: dict[str, str] = {}
        self.requests: list[str] = []
        self.statuses: list[int] = []
        self.delay = delay
        self._lock = threading.Lock()

    def fetch(self, url: str, headers: Mapping[str, str]) -> Response:
        time.sleep(self.delay)
        path = urlsplit(url).path
        response = self._respond(path, headers)
        with self._lock:
            self.requests.append(path)
            self.statuses.append(response.status)
        return response

    def _respond(self, path: str, headers: Mapping[str, str]) -> Response:
        body = self.bodies.get(path)
        if body is None:
            return Response(404, None, b"<html>not found</html>")
        etag = etag_of(body)
        if headers.get("If-None-Match") == etag:
            return Response(304, etag, b"")
        return Response(200, etag, body.encode("utf-8"))


class FakeClock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


def rules(name: str) -> str:
    return f"! {name}\n||{name}.example^\n"


def make_service(
    upstream: FakeUpstream, today: list[date], clock: FakeClock
) -> BlocklistService:
    return BlocklistService(
        upstream.fetch,
        URL_TEMPLATE,
        refresh_interval=REFRESH,
        retry_interval=RETRY,
        today=lambda: today[0],
        clock=clock,
    )


def test_year_month_previous_wraps_year() -> None:
    assert YearMonth(2026, 1).previous() == YearMonth(2025, 12)
    assert YearMonth(2026, 10).previous() == YearMonth(2026, 9)


def test_serves_cache_within_refresh_interval() -> None:
    upstream = FakeUpstream()
    upstream.bodies["/202610.txt"] = rules("oct")
    clock = FakeClock()
    service = make_service(upstream, [date(2026, 10, 1)], clock)

    assert service.get().content == rules("oct")
    clock.now = REFRESH - 1
    assert service.get().content == rules("oct")
    assert upstream.requests == ["/202610.txt"]


def test_revalidates_with_etag_after_refresh_interval() -> None:
    upstream = FakeUpstream()
    upstream.bodies["/202610.txt"] = rules("oct")
    clock = FakeClock()
    service = make_service(upstream, [date(2026, 10, 1)], clock)
    service.get()

    # 未更新なら 304 で、保持中のリストを返す
    clock.now = REFRESH
    assert service.get().content == rules("oct")
    assert upstream.statuses == [200, 304]

    # 304 の後も間隔を空けるまでは再確認しない
    clock.now = REFRESH * 2 - 1
    service.get()
    assert upstream.statuses == [200, 304]


def test_picks_up_mid_month_update() -> None:
    upstream = FakeUpstream()
    upstream.bodies["/202610.txt"] = rules("oct-1")
    clock = FakeClock()
    service = make_service(upstream, [date(2026, 10, 1)], clock)
    service.get()

    upstream.bodies["/202610.txt"] = rules("oct-2")
    clock.now = REFRESH
    blocklist = service.get()
    assert blocklist.content == rules("oct-2")
    assert blocklist.etag == etag_of(rules("oct-2"))
    assert upstream.statuses == [200, 200]


def test_month_change_refreshes_before_interval() -> None:
    upstream = FakeUpstream()
    upstream.bodies["/202609.txt"] = rules("same")
    upstream.bodies["/202610.txt"] = rules("same")
    today = [date(2026, 9, 30)]
    clock = FakeClock()
    service = make_service(upstream, today, clock)
    service.get()

    # 上流は月をまたいでも同じ中身を返すことがある。その場合も 304 で済ませる
    today[0] = date(2026, 10, 1)
    clock.now = 1
    assert service.get().month == YearMonth(2026, 10)
    assert upstream.requests == ["/202609.txt", "/202610.txt"]
    assert upstream.statuses == [200, 304]


def test_falls_back_to_previous_month_on_cold_start() -> None:
    upstream = FakeUpstream()
    upstream.bodies["/202609.txt"] = rules("sep")
    clock = FakeClock()
    service = make_service(upstream, [date(2026, 10, 1)], clock)

    blocklist = service.get()
    assert blocklist.month == YearMonth(2026, 9)

    # バックオフ中は再取得しない
    service.get()
    assert upstream.requests == ["/202610.txt", "/202609.txt"]

    # バックオフ明けに今月分が公開されていれば切り替わる
    upstream.bodies["/202610.txt"] = rules("oct")
    clock.now = RETRY
    assert service.get().month == YearMonth(2026, 10)


def test_keeps_stale_list_when_new_month_fails() -> None:
    upstream = FakeUpstream()
    upstream.bodies["/202609.txt"] = rules("sep")
    today = [date(2026, 9, 30)]
    clock = FakeClock()
    service = make_service(upstream, today, clock)
    service.get()

    today[0] = date(2026, 10, 1)
    clock.now = 1000
    assert service.get().content == rules("sep")
    # 保持済みのリストがあるときは前月分を取り直さない
    assert upstream.requests == ["/202609.txt", "/202610.txt"]


def test_failed_revalidation_keeps_list_and_backs_off() -> None:
    upstream = FakeUpstream()
    upstream.bodies["/202610.txt"] = rules("oct")
    clock = FakeClock()
    service = make_service(upstream, [date(2026, 10, 1)], clock)
    service.get()

    del upstream.bodies["/202610.txt"]
    clock.now = REFRESH
    assert service.get().content == rules("oct")

    # バックオフ中は再試行しない
    clock.now = REFRESH + RETRY - 1
    service.get()
    assert upstream.statuses == [200, 404]

    # バックオフが明けたら再試行する
    upstream.bodies["/202610.txt"] = rules("oct")
    clock.now = REFRESH + RETRY
    service.get()
    assert upstream.statuses == [200, 404, 304]


def test_strips_bom() -> None:
    upstream = FakeUpstream()
    upstream.bodies["/202610.txt"] = "\ufeff" + rules("oct")
    service = make_service(upstream, [date(2026, 10, 1)], FakeClock())

    assert service.get().content == rules("oct")


def test_rejects_non_filter_response() -> None:
    upstream = FakeUpstream()
    upstream.bodies["/202610.txt"] = "<html>maintenance</html>"
    service = make_service(upstream, [date(2026, 10, 1)], FakeClock())

    with pytest.raises(BlocklistUnavailable):
        service.get()


def test_concurrent_requests_fetch_once() -> None:
    upstream = FakeUpstream(delay=0.05)
    upstream.bodies["/202610.txt"] = rules("oct")
    service = make_service(upstream, [date(2026, 10, 1)], FakeClock())

    with ThreadPoolExecutor(5) as pool:
        results = list(pool.map(lambda _: service.get(), range(5)))

    assert {r.content for r in results} == {rules("oct")}
    assert upstream.requests == ["/202610.txt"]
