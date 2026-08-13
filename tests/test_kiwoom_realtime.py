"""kiwoom_realtime.py 회귀 테스트.

REST 폴링(query_current_price) 429 사고([[project_auto_trader_429_reconcile_bug]])를 계기로
WebSocket 실시간 구독으로 대체하는 KiwoomRealtimeFeed를 검증한다. 순수 파싱/메시지 빌더 함수는
일반 단위테스트로, LOGIN/PING/REG/REAL 프로토콜 왕복은 로컬 in-process 페이크 WebSocket
서버(`websockets.serve`)를 띄워 실제 스레드+asyncio 이벤트루프로 굴러가는 KiwoomRealtimeFeed와
end-to-end로 주고받아 검증한다(pytest-asyncio 없이 asyncio.run으로 직접 구동).
"""

from __future__ import annotations

import asyncio
import json

import websockets

from rich_stock.broker.kiwoom_realtime import (
    KiwoomRealtimeFeed,
    build_reg_message,
    build_remove_message,
    parse_stock_execution,
)


def test_parse_stock_execution_takes_absolute_value_of_signed_price():
    # 실측(REST ka10007)과 동일하게 "10"(현재가)이 회계상 부호가 아니라 등락방향이라 abs() 필요
    assert parse_stock_execution({"10": "-20800", "20": "165208"}) == {
        "현재가": 20800,
        "체결시간": "165208",
    }


def test_parse_stock_execution_handles_empty_values():
    assert parse_stock_execution({}) == {"현재가": 0, "체결시간": None}


def test_build_reg_message():
    assert build_reg_message(["005930", "000660"]) == {
        "trnm": "REG",
        "grp_no": "1",
        "refresh": "1",
        "data": [{"item": ["005930", "000660"], "type": ["0B"]}],
    }


def test_build_remove_message():
    assert build_remove_message(["005930"]) == {
        "trnm": "REMOVE",
        "grp_no": "1",
        "data": [{"item": ["005930"], "type": ["0B"]}],
    }


def test_sync_subscriptions_adds_and_removes_delta():
    # 연결 전(disconnected)에도 내부 구독 상태(_subscribed)만 정확히 갱신되면 됨 —
    # 실제 REG/REMOVE 전송은 다음 연결 시 재구독 로직이 알아서 반영한다.
    feed = KiwoomRealtimeFeed(token_provider=lambda: "dummy-token")
    feed.sync_subscriptions(["005930", "000660"])
    assert feed._subscribed == {"005930", "000660"}

    feed.sync_subscriptions(["000660", "035420"])  # 005930 빠지고 035420 추가
    assert feed._subscribed == {"000660", "035420"}

    feed.sync_subscriptions([])
    assert feed._subscribed == set()


# --- 통합 테스트: 로컬 페이크 WebSocket 서버로 실제 프로토콜 왕복 검증 -----------


async def _wait_until(predicate, timeout=3.0, interval=0.05):
    elapsed = 0.0
    while elapsed < timeout:
        if predicate():
            return True
        await asyncio.sleep(interval)
        elapsed += interval
    return False


def test_realtime_feed_echoes_ping_to_stay_alive():
    results: dict = {}

    async def handler(ws):
        await ws.recv()  # LOGIN
        await ws.send(json.dumps({"trnm": "LOGIN", "return_code": 0, "return_msg": ""}))
        await ws.send(json.dumps({"trnm": "PING", "tick": "abc"}))
        results["echoed"] = json.loads(await ws.recv())

    async def scenario():
        async with websockets.serve(handler, "localhost", 0) as server:
            port = server.sockets[0].getsockname()[1]
            feed = KiwoomRealtimeFeed(token_provider=lambda: "dummy-token", ws_url=f"ws://localhost:{port}")
            feed.start()
            try:
                assert await _wait_until(lambda: "echoed" in results)
            finally:
                await asyncio.to_thread(feed.stop)

        assert results["echoed"] == {"trnm": "PING", "tick": "abc"}

    asyncio.run(scenario())


def test_realtime_feed_subscribe_sends_reg_and_caches_real_price():
    results: dict = {}

    async def handler(ws):
        login_msg = json.loads(await ws.recv())
        results["login"] = login_msg
        await ws.send(json.dumps({"trnm": "LOGIN", "return_code": 0, "return_msg": ""}))
        reg_msg = json.loads(await ws.recv())
        results["reg"] = reg_msg
        await ws.send(
            json.dumps(
                {
                    "trnm": "REAL",
                    "data": [
                        {"type": "0B", "name": "주식체결", "item": "005930", "values": {"10": "-71000", "20": "101500"}}
                    ],
                }
            )
        )
        await asyncio.sleep(0.3)  # 테스트가 캐시를 읽을 때까지 연결 유지

    async def scenario():
        async with websockets.serve(handler, "localhost", 0) as server:
            port = server.sockets[0].getsockname()[1]
            feed = KiwoomRealtimeFeed(token_provider=lambda: "dummy-token", ws_url=f"ws://localhost:{port}")
            feed.start()
            try:
                assert await _wait_until(feed.is_connected)
                feed.subscribe(["005930"])
                assert await _wait_until(lambda: feed.get_quote("005930") is not None)
                quote = feed.get_quote("005930")
            finally:
                await asyncio.to_thread(feed.stop)

        assert results["login"] == {"trnm": "LOGIN", "token": "dummy-token"}
        assert results["reg"] == {
            "trnm": "REG", "grp_no": "1", "refresh": "1",
            "data": [{"item": ["005930"], "type": ["0B"]}],
        }
        assert quote["현재가"] == 71000

    asyncio.run(scenario())


def test_realtime_feed_get_quote_returns_none_when_stale():
    async def handler(ws):
        await ws.recv()
        await ws.send(json.dumps({"trnm": "LOGIN", "return_code": 0, "return_msg": ""}))
        await ws.send(
            json.dumps(
                {"trnm": "REAL", "data": [{"type": "0B", "item": "005930", "values": {"10": "71000", "20": "101500"}}]}
            )
        )
        await asyncio.sleep(0.3)

    async def scenario():
        async with websockets.serve(handler, "localhost", 0) as server:
            port = server.sockets[0].getsockname()[1]
            feed = KiwoomRealtimeFeed(token_provider=lambda: "dummy-token", ws_url=f"ws://localhost:{port}")
            feed.start()
            try:
                assert await _wait_until(lambda: feed.get_quote("005930") is not None)
                # max_age_sec=0이면 방금 받은 데이터도 이미 "오래됨"으로 취급돼야 함
                assert feed.get_quote("005930", max_age_sec=0) is None
                assert feed.get_quote("999999") is None  # 구독 안 한(캐시에 없는) 종목
            finally:
                await asyncio.to_thread(feed.stop)

    asyncio.run(scenario())


def test_realtime_feed_resubscribes_after_reconnect():
    connection_count = {"n": 0}
    results: dict = {"regs": []}

    async def handler(ws):
        connection_count["n"] += 1
        conn_no = connection_count["n"]
        await ws.recv()  # LOGIN
        await ws.send(json.dumps({"trnm": "LOGIN", "return_code": 0, "return_msg": ""}))
        if conn_no == 1:
            reg_msg = json.loads(await ws.recv())
            results["regs"].append(reg_msg)
            await ws.close()  # 강제로 끊어서 재연결을 유도
            return
        # 두 번째(재연결) 접속 — 재구독을 자동으로 보내는지 확인
        reg_msg = json.loads(await ws.recv())
        results["regs"].append(reg_msg)
        await ws.send(
            json.dumps(
                {"trnm": "REAL", "data": [{"type": "0B", "item": "005930", "values": {"10": "72500", "20": "101600"}}]}
            )
        )
        await asyncio.sleep(0.3)

    async def scenario():
        async with websockets.serve(handler, "localhost", 0) as server:
            port = server.sockets[0].getsockname()[1]
            feed = KiwoomRealtimeFeed(
                token_provider=lambda: "dummy-token", ws_url=f"ws://localhost:{port}", reconnect_delay_sec=0.1
            )
            feed.start()
            try:
                assert await _wait_until(feed.is_connected)
                feed.subscribe(["005930"])
                assert await _wait_until(lambda: len(results["regs"]) >= 1)
                # 서버가 첫 연결을 끊었으니 재연결 후 자동 재구독이 다시 REG로 와야 함
                assert await _wait_until(lambda: feed.get_quote("005930") is not None, timeout=5.0)
            finally:
                await asyncio.to_thread(feed.stop)

        assert len(results["regs"]) == 2
        assert results["regs"][0]["data"][0]["item"] == ["005930"]
        assert results["regs"][1]["data"][0]["item"] == ["005930"]  # 재연결 후 재구독

    asyncio.run(scenario())
