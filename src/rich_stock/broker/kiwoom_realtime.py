"""키움 REST API 실시간 WebSocket 시세 연동 — 폴링(query_current_price) 대체용.

**배경**: auto_trader.py의 fast_check_pending_entries/sync_open_positions가 5초마다 감시
종목 전부를 순차로 REST 폴링(ka10007)하다가 종목 수가 늘면(2026-08-13 18종목) 키움 모의투자
API의 요청 한도를 넘겨 거의 전부 429로 실패하는 사고가 있었다([[project_auto_trader_429_reconcile_bug]]
와 동일 세션). REST 폴링을 WebSocket 실시간 구독(REG 방식 "0B" 주식체결)으로 대체하면 폴링
자체가 없어져 요청 한도 문제가 원천 해결된다 — kiwoom-rest-api-spec.json에 "0B" TR이 문서화돼
있는 걸 확인했다(응답 필드 10=현재가, 다른 실시간 가격류 필드와 동일하게 "부호가 등락방향"이라
ka10007과 마찬가지로 abs() 필요).

**LOGIN/PING 프로토콜은 로컬 spec JSON에 없어서 별도 확인**(2026-08-13, 공식 문서가 SPA라
WebFetch로 못 읽어서 실제 사용자 후기 코드로 교차 확인): 연결 직후 `{"trnm":"LOGIN","token":...}`을
보내고, 서버가 주기적으로 보내는 `{"trnm":"PING",...}`은 받은 값 그대로 되돌려 보내야 연결이
유지된다. REG(등록)/REMOVE(해지)는 `{"trnm":"REG","grp_no":"1","refresh":"1",
"data":[{"item":[...],"type":["0B"]}]}` 형식.

**설계**: 나머지 코드베이스가 전부 동기(sync)라 이 모듈만 별도 스레드에서 자체 asyncio 이벤트
루프를 돌린다. 호출 측(auto_trader.py)은 `subscribe`/`unsubscribe`/`get_quote` 같은 평범한 동기
메서드만 쓰면 되고, 스레드/asyncio는 내부에 숨겨져 있다. 연결이 끊기면 자동 재연결하고, 재연결
시점에 그동안 구독했던 종목을 자동으로 다시 등록한다(무상태 REST 폴링과 달리 이 모듈은 프로세스
안에 구독 상태를 들고 있으므로).

**안전장치**: REST 폴링을 완전히 대체하지 않고 auto_trader.py 쪽에서 "WS 캐시가 최근
N초 이내로 신선하면 그걸 쓰고, 없거나 오래됐으면 기존 REST 폴링으로 폴백"하는 하이브리드로
연결한다(get_quote의 max_age_sec) — WS 연결이 끊기거나 이 모듈에 버그가 있어도 기존 REST
경로가 안전망 역할을 하도록.
"""

from __future__ import annotations

import asyncio
import json
import logging
import threading
from typing import TYPE_CHECKING, Callable

import pandas as pd
import websockets

if TYPE_CHECKING:
    from rich_stock.broker.kiwoom_rest import KiwoomRestClient

logger = logging.getLogger(__name__)

REALTIME_PRICE_TYPE = "0B"
"""실시간 항목 코드 — 주식체결(현재가/체결시간 등)."""

MOCK_WS_URL = "wss://mockapi.kiwoom.com:10000/api/dostk/websocket"
LIVE_WS_URL = "wss://api.kiwoom.com:10000/api/dostk/websocket"


def _abs_signed_amount(raw: str | None) -> int:
    """REST(ka10007)와 동일한 "부호=등락방향" 컨벤션 — 부호를 무시하고 절대값만 취한다."""
    raw = (raw or "").strip()
    if not raw:
        return 0
    sign = -1 if raw.startswith("-") else 1
    digits = raw.lstrip("+-") or "0"
    return abs(sign * int(digits))


def parse_stock_execution(values: dict) -> dict:
    """"0B"(주식체결) REAL 메시지의 values를 우리 표준 필드명으로 변환."""
    return {
        "현재가": _abs_signed_amount(values.get("10")),
        "체결시간": values.get("20"),
    }


def build_reg_message(tickers: list[str], grp_no: str = "1", refresh: str = "1") -> dict:
    return {
        "trnm": "REG",
        "grp_no": grp_no,
        "refresh": refresh,
        "data": [{"item": list(tickers), "type": [REALTIME_PRICE_TYPE]}],
    }


def build_remove_message(tickers: list[str], grp_no: str = "1") -> dict:
    return {
        "trnm": "REMOVE",
        "grp_no": grp_no,
        "data": [{"item": list(tickers), "type": [REALTIME_PRICE_TYPE]}],
    }


class KiwoomRealtimeFeed:
    """"0B"(주식체결) WebSocket 구독으로 현재가 캐시를 유지하는 백그라운드 클라이언트.

    `token_provider()`는 매 연결(최초/재연결)마다 다시 호출된다 — KiwoomRestClient.token은
    만료 시 자동 재발급하므로 `lambda: client.token.token`을 넘기면 재연결 때마다 최신 토큰을
    쓰게 된다.
    """

    def __init__(
        self,
        token_provider: Callable[[], str],
        ws_url: str = MOCK_WS_URL,
        reconnect_delay_sec: float = 3.0,
    ) -> None:
        self._token_provider = token_provider
        self.ws_url = ws_url
        self._reconnect_delay_sec = reconnect_delay_sec

        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._ws = None

        self._lock = threading.Lock()
        self._prices: dict[str, dict] = {}
        self._subscribed: set[str] = set()

        self._connected = threading.Event()
        self._stop = threading.Event()

    # --- 스레드/연결 수명주기 ------------------------------------------------

    def start(self) -> None:
        if self._thread is not None:
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run_loop, daemon=True, name="kiwoom-realtime-feed")
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        loop = self._loop
        if loop is not None and self._ws is not None:
            fut = asyncio.run_coroutine_threadsafe(self._close_ws(), loop)
            try:
                fut.result(timeout=2)
            except Exception:  # noqa: BLE001
                pass
        if self._thread is not None:
            self._thread.join(timeout=3)
            self._thread = None

    async def _close_ws(self) -> None:
        if self._ws is not None:
            try:
                await asyncio.wait_for(self._ws.close(), timeout=1.0)
            except (TimeoutError, Exception):  # noqa: BLE001
                pass  # 정상 종료 핸드셰이크가 안 되더라도 stop()은 빠르게 끝나야 함

    def is_connected(self) -> bool:
        return self._connected.is_set()

    def _run_loop(self) -> None:
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        try:
            self._loop.run_until_complete(self._connect_forever())
        finally:
            self._loop.close()
            self._loop = None

    async def _connect_forever(self) -> None:
        while not self._stop.is_set():
            try:
                await self._connect_once()
            except Exception:  # noqa: BLE001
                logger.exception("[kiwoom_realtime] 연결 실패/끊김 — 재연결 시도")
            self._connected.clear()
            self._ws = None
            if self._stop.is_set():
                break
            # 재연결 대기 중에도 stop()에 빠르게 반응하도록 짧은 간격으로 나눠서 잔다
            # (통짜로 sleep(reconnect_delay_sec)이면 그 사이 stop()이 와도 최대 그만큼 종료가 늦어짐).
            remaining = self._reconnect_delay_sec
            while remaining > 0 and not self._stop.is_set():
                step = min(0.2, remaining)
                await asyncio.sleep(step)
                remaining -= step

    async def _connect_once(self) -> None:
        # close_timeout 기본값(10초)이면 stop()/재연결이 그만큼 느려질 수 있어 짧게 잡는다 —
        # 상시 프로세스에서 재시작/종료 지연으로 이어지지 않도록.
        async with websockets.connect(self.ws_url, close_timeout=3) as ws:
            self._ws = ws
            await ws.send(json.dumps({"trnm": "LOGIN", "token": self._token_provider()}))
            login_resp = json.loads(await ws.recv())
            if login_resp.get("trnm") == "LOGIN" and login_resp.get("return_code", 0) != 0:
                raise RuntimeError(f"실시간 WebSocket 로그인 실패: {login_resp.get('return_msg')}")

            self._connected.set()
            logger.info("[kiwoom_realtime] 연결/로그인 완료: %s", self.ws_url)

            with self._lock:
                tickers = list(self._subscribed)
            if tickers:
                await ws.send(json.dumps(build_reg_message(tickers)))

            async for raw in ws:
                await self._handle_message(json.loads(raw))

    async def _handle_message(self, msg: dict) -> None:
        trnm = msg.get("trnm")
        if trnm == "PING":
            # 명세 확인 결과 받은 값 그대로 되돌려 보내야 연결 유지됨(별도 PONG 형식 없음).
            if self._ws is not None:
                await self._ws.send(json.dumps(msg))
            return
        if trnm == "REAL":
            self._apply_real_data(msg.get("data", []))
            return
        logger.debug("[kiwoom_realtime] 메시지: %s", msg)

    def _apply_real_data(self, rows: list[dict]) -> None:
        now = pd.Timestamp.now()  # clock-ok: 수신시각 기록 — get_quote가 같은 시계로만 나이를 잰다
        with self._lock:
            for row in rows:
                if row.get("type") != REALTIME_PRICE_TYPE:
                    continue
                ticker = row.get("item")
                if not ticker:
                    continue
                parsed = parse_stock_execution(row.get("values", {}))
                self._prices[ticker] = {**parsed, "수신시각": now}

    # --- 구독 관리(호출 측은 이 아래만 씀) -----------------------------------

    def subscribe(self, tickers: list[str]) -> None:
        new = {t for t in tickers if t}
        with self._lock:
            to_add = new - self._subscribed
            self._subscribed |= new
        if to_add:
            self._send_threadsafe(build_reg_message(list(to_add)))

    def unsubscribe(self, tickers: list[str]) -> None:
        remove = {t for t in tickers if t}
        with self._lock:
            to_remove = remove & self._subscribed
            self._subscribed -= remove
            for t in to_remove:
                self._prices.pop(t, None)
        if to_remove:
            self._send_threadsafe(build_remove_message(list(to_remove)))

    def sync_subscriptions(self, tickers) -> None:
        """"지금 감시가 필요한 종목 전체 목록"을 넘기면 내부 구독 상태와 비교해 늘어난 건
        REG로, 빠진 건 REMOVE로 자동 반영한다. 호출 측(auto_trader.py)이 매 tick마다 매번
        전체 목록을 넘기기만 하면 되고, REG/REMOVE 델타 계산은 이 메서드가 담당한다."""
        desired = {t for t in tickers if t}
        with self._lock:
            current = set(self._subscribed)
        to_add = desired - current
        to_remove = current - desired
        if to_add:
            self.subscribe(list(to_add))
        if to_remove:
            self.unsubscribe(list(to_remove))

    def _send_threadsafe(self, msg: dict) -> None:
        loop = self._loop
        if loop is None or self._ws is None:
            return  # 아직 연결 전 — 다음 연결 시 _subscribed 전체가 재등록됨
        asyncio.run_coroutine_threadsafe(self._send(msg), loop)

    async def _send(self, msg: dict) -> None:
        if self._ws is not None:
            await self._ws.send(json.dumps(msg))

    def get_quote(self, ticker: str, max_age_sec: float = 10.0) -> dict | None:
        """캐시된 실시간 현재가. 없거나 max_age_sec보다 오래됐으면 None(호출 측이 REST로 폴백)."""
        with self._lock:
            row = self._prices.get(ticker)
        if row is None:
            return None
        age = (pd.Timestamp.now() - row["수신시각"]).total_seconds()  # clock-ok: _apply_real_data가 같은 시계로 찍은 값과의 차이
        if age > max_age_sec:
            return None
        return row


def make_realtime_feed(client: "KiwoomRestClient") -> KiwoomRealtimeFeed:
    """KiwoomRestClient의 base_url(모의/실서버)에 맞춰 WebSocket URL을 고르고 토큰을 위임한다."""
    from rich_stock.broker.kiwoom_rest import MOCK_BASE_URL

    ws_url = MOCK_WS_URL if client.base_url == MOCK_BASE_URL else LIVE_WS_URL
    return KiwoomRealtimeFeed(token_provider=lambda: client.token.token, ws_url=ws_url)
