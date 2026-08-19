from datetime import datetime, timedelta
from unittest.mock import MagicMock

import pytest

from rich_stock.broker.kiwoom_rest import (
    ACCOUNT_TR_URL_PATH,
    LIVE_BASE_URL,
    MARKET_COND_TR_URL_PATH,
    ORDER_TR_URL_PATH,
    US_ACCOUNT_TR_URL_PATH,
    US_MARKET_COND_TR_URL_PATH,
    US_ORDER_TR_URL_PATH,
    AccessToken,
    KiwoomCredentials,
    KiwoomRestClient,
    LiveTradingBlockedError,
    _parse_amount,
    _parse_decimal_us,
    cancel_order,
    cancel_order_us,
    place_buy_order,
    place_buy_order_us,
    place_sell_order,
    place_sell_order_us,
    query_current_price,
    query_current_price_us,
    query_deposit,
    query_deposit_us,
    query_holdings,
    query_holdings_us,
    query_order_status,
    query_order_status_us,
)


def test_parse_amount_handles_zero_padded_positive():
    assert _parse_amount("000000000017534") == 17534


def test_parse_amount_handles_zero_padded_negative():
    assert _parse_amount("-00000000004900") == -4900


def test_parse_amount_handles_empty_and_none():
    assert _parse_amount("") == 0
    assert _parse_amount(None) == 0


def _make_client_with_mock_tr(response: dict) -> KiwoomRestClient:
    client = KiwoomRestClient(KiwoomCredentials(appkey="x", secretkey="y"))
    client.request_tr = MagicMock(return_value=response)
    return client


def test_query_deposit_parses_kt00001_response():
    # 스펙 문서(kt00001)의 responseExample을 그대로 축약해 사용
    response = {
        "entr": "000000000017534",
        "pymn_alow_amt": "000000000085341",
        "ord_alow_amt": "000000000085341",
        "d2_entra": "000000000012550",
        "return_code": 0,
        "return_msg": "조회가 완료되었습니다.",
    }
    client = _make_client_with_mock_tr(response)
    result = query_deposit(client)

    assert result["예수금"] == 17534
    assert result["출금가능금액"] == 85341
    assert result["주문가능금액"] == 85341
    assert result["d2추정예수금"] == 12550
    client.request_tr.assert_called_once_with("kt00001", {"qry_tp": "3"})


def test_query_holdings_parses_kt00018_response():
    # 스펙 문서(kt00018)의 responseExample 중 종목 1건을 축약해 사용
    response = {
        "tot_pur_amt": "000000017598258",
        "acnt_evlt_remn_indv_tot": [
            {
                "stk_cd": "A005930",
                "stk_nm": "삼성전자",
                "evltv_prft": "-00000000196888",
                "prft_rt": "-52.71",
                "pur_pric": "000000000124500",
                "rmnd_qty": "000000000000003",
                "cur_prc": "000000059000",
            }
        ],
        "return_code": 0,
        "return_msg": "조회가 완료되었습니다",
    }
    client = _make_client_with_mock_tr(response)
    holdings = query_holdings(client)

    assert len(holdings) == 1
    h = holdings[0]
    assert h["종목코드"] == "A005930"
    assert h["종목명"] == "삼성전자"
    assert h["보유수량"] == 3
    assert h["매입가"] == 124500
    assert h["현재가"] == 59000
    assert h["평가손익"] == -196888
    client.request_tr.assert_called_once_with("kt00018", {"qry_tp": "1", "dmst_stex_tp": "KRX"})


def test_query_holdings_empty_list_when_no_positions():
    response = {"acnt_evlt_remn_indv_tot": [], "return_code": 0, "return_msg": "조회완료"}
    client = _make_client_with_mock_tr(response)
    assert query_holdings(client) == []


def test_query_current_price_takes_absolute_value_of_signed_fields():
    # 실측(2026-08-12, 005930): cur_prc/upl_pric/lst_pric은 회계상 부호가 아니라 등락 방향을
    # 나타낸다 — 하한가(lst_pric)도 항상 "-"로 온다. abs() 없이 그대로 쓰면 하한가가 음수가 된다.
    response = {
        "stk_cd": "005930", "stk_nm": "삼성전자", "cur_prc": "+255500", "upl_pric": "+311000", "lst_pric": "-168000",
        "return_code": 0, "return_msg": "조회가 완료되었습니다",
    }
    client = _make_client_with_mock_tr(response)
    quote = query_current_price(client, "005930")

    assert quote["종목명"] == "삼성전자"
    assert quote["현재가"] == 255500
    assert quote["상한가"] == 311000
    assert quote["하한가"] == 168000
    client.request_tr.assert_called_once_with("ka10007", {"stk_cd": "005930"}, url_path=MARKET_COND_TR_URL_PATH)


def test_request_tr_raises_on_nonzero_return_code():
    client = KiwoomRestClient(KiwoomCredentials(appkey="x", secretkey="y"))
    client._token = MagicMock(token_type="Bearer", token="dummy")

    import requests

    class FakeResponse:
        def raise_for_status(self):
            pass

        def json(self):
            return {"return_code": 1, "return_msg": "계좌비밀번호 오류"}

    original_post = requests.post
    requests.post = MagicMock(return_value=FakeResponse())
    try:
        try:
            client.request_tr("kt00001", {"qry_tp": "3"})
            assert False, "예외가 발생해야 함"
        except RuntimeError as e:
            assert "계좌비밀번호 오류" in str(e)
    finally:
        requests.post = original_post


# --- issue_token(au10001) ---------------------------------------------------


def test_issue_token_parses_response():
    # 스펙(au10001)의 responseExample을 그대로 축약
    import requests

    class FakeResponse:
        def raise_for_status(self):
            pass

        def json(self):
            return {
                "expires_dt": "20241107083713", "token_type": "bearer", "token": "WQJCwyqInphKnR3bSRtB9NE1lv",
                "return_code": 0, "return_msg": "정상적으로 처리되었습니다",
            }

    client = KiwoomRestClient(KiwoomCredentials(appkey="x", secretkey="y"))
    original_post = requests.post
    requests.post = MagicMock(return_value=FakeResponse())
    try:
        token = client.issue_token()
    finally:
        requests.post = original_post

    assert token.token == "WQJCwyqInphKnR3bSRtB9NE1lv"
    assert token.token_type == "bearer"
    assert token.expires_dt == "20241107083713"


def test_token_reissues_when_cached_token_expired():
    # 2026-08-18: 상시 데몬이 며칠씩 재시작 없이 켜져 있으면 캐시된 토큰이 만료된 채 계속
    # 재사용돼 모든 TR이 8005로 실패했다 — expires_dt를 확인해서 만료 전에 재발급해야 한다.
    import requests

    client = KiwoomRestClient(KiwoomCredentials(appkey="x", secretkey="y"))
    expired_dt = (datetime.now() - timedelta(days=1)).strftime("%Y%m%d%H%M%S")
    client._token = AccessToken(token="stale", token_type="Bearer", expires_dt=expired_dt)

    class FakeResponse:
        def raise_for_status(self):
            pass

        def json(self):
            return {
                "expires_dt": (datetime.now() + timedelta(hours=1)).strftime("%Y%m%d%H%M%S"),
                "token_type": "Bearer", "token": "fresh", "return_code": 0, "return_msg": "정상",
            }

    original_post = requests.post
    requests.post = MagicMock(return_value=FakeResponse())
    try:
        token = client.token
    finally:
        requests.post = original_post

    assert token.token == "fresh"


def test_token_reused_when_still_valid():
    client = KiwoomRestClient(KiwoomCredentials(appkey="x", secretkey="y"))
    valid_dt = (datetime.now() + timedelta(hours=1)).strftime("%Y%m%d%H%M%S")
    client._token = AccessToken(token="cached", token_type="Bearer", expires_dt=valid_dt)
    client.issue_token = MagicMock(side_effect=AssertionError("재발급이 호출되면 안 됨"))

    assert client.token.token == "cached"


def test_request_tr_reissues_token_and_retries_on_8005():
    # 만료시각 계산이 어긋나는 경우(시계 오차 등)의 안전망 — 8005 응답을 받으면 토큰을
    # 재발급하고 한 번 더 시도한다.
    import requests

    client = KiwoomRestClient(KiwoomCredentials(appkey="x", secretkey="y"))
    client._token = AccessToken(
        token="stale", token_type="Bearer", expires_dt=(datetime.now() + timedelta(hours=1)).strftime("%Y%m%d%H%M%S")
    )

    responses = [
        {"return_code": 3, "return_msg": "인증에 실패했습니다[8005:Token이 유효하지 않습니다]"},
        {"entr": "000000000017534", "pymn_alow_amt": "0", "ord_alow_amt": "0", "d2_entra": "0", "return_code": 0, "return_msg": "조회완료"},
    ]
    token_responses = [
        {"expires_dt": (datetime.now() + timedelta(hours=1)).strftime("%Y%m%d%H%M%S"), "token_type": "Bearer", "token": "fresh", "return_code": 0, "return_msg": "정상"},
    ]

    call_log = []

    def fake_post(url, **kwargs):
        class FakeResponse:
            def raise_for_status(self):
                pass

            def json(self):
                if "/oauth2/token" in url:
                    call_log.append("token")
                    return token_responses.pop(0)
                call_log.append("tr")
                return responses.pop(0)

        return FakeResponse()

    original_post = requests.post
    requests.post = fake_post
    try:
        data = client.request_tr("kt00001", {"qry_tp": "3"})
    finally:
        requests.post = original_post

    assert call_log == ["tr", "token", "tr"]
    assert data["return_code"] == 0
    assert client._token.token == "fresh"


def test_issue_token_raises_on_nonzero_return_code():
    # HTTP 200이지만 return_code!=0인 경우(예: appkey/secretkey 오류) — 예전에는 이 체크가
    # 없어서 data["token"]에서 알아보기 힘든 KeyError만 났다.
    import requests

    class FakeResponse:
        def raise_for_status(self):
            pass

        def json(self):
            return {"return_code": 1, "return_msg": "AppKey 또는 SecretKey 정보가 틀립니다"}

    client = KiwoomRestClient(KiwoomCredentials(appkey="x", secretkey="y"))
    original_post = requests.post
    requests.post = MagicMock(return_value=FakeResponse())
    try:
        with pytest.raises(RuntimeError, match="AppKey 또는 SecretKey"):
            client.issue_token()
    finally:
        requests.post = original_post


# --- 주문(kt10000/kt10001/kt10003) -----------------------------------------


def test_place_buy_order_limit_sends_correct_body_and_url():
    # 스펙(kt10000)의 responseExample을 그대로 축약
    response = {"ord_no": "00024", "return_code": 0, "return_msg": "정상적으로 처리되었습니다"}
    client = _make_client_with_mock_tr(response)

    result = place_buy_order(client, "005930", quantity=1, price=70000)

    assert result["주문번호"] == "00024"
    client.request_tr.assert_called_once_with(
        "kt10000",
        {"dmst_stex_tp": "KRX", "stk_cd": "005930", "ord_qty": "1", "ord_uv": "70000", "trde_tp": "0", "cond_uv": ""},
        url_path=ORDER_TR_URL_PATH,
    )


def test_place_buy_order_without_price_sends_market_order():
    response = {"ord_no": "00025", "return_code": 0, "return_msg": "정상적으로 처리되었습니다"}
    client = _make_client_with_mock_tr(response)

    place_buy_order(client, "005930", quantity=1)

    body = client.request_tr.call_args[0][1]
    assert body["ord_uv"] == ""
    assert body["trde_tp"] == "3"  # 시장가


def test_place_sell_order_sends_correct_body_and_url():
    # 스펙(kt10001)의 responseExample을 그대로 축약
    response = {"ord_no": "0000138", "dmst_stex_tp": "KRX", "return_code": 0, "return_msg": "매도주문이 완료되었습니다."}
    client = _make_client_with_mock_tr(response)

    result = place_sell_order(client, "005930", quantity=2, price=71000)

    assert result["주문번호"] == "0000138"
    client.request_tr.assert_called_once_with(
        "kt10001",
        {"dmst_stex_tp": "KRX", "stk_cd": "005930", "ord_qty": "2", "ord_uv": "71000", "trde_tp": "0", "cond_uv": ""},
        url_path=ORDER_TR_URL_PATH,
    )


def test_place_sell_order_with_float_quantity_sends_integer_ord_qty():
    # DB의 fill_quantity 컬럼이 DOUBLE이라 청산 시 30.0 같은 float로 넘어올 수 있는데,
    # 키움 kt10001은 "30.0"을 "정수만 입력가능합니다" 오류로 거부한다(2026-08-19 실거래 확인).
    response = {"ord_no": "0000138", "dmst_stex_tp": "KRX", "return_code": 0, "return_msg": "매도주문이 완료되었습니다."}
    client = _make_client_with_mock_tr(response)

    place_sell_order(client, "005930", quantity=30.0, price=None)

    body = client.request_tr.call_args[0][1]
    assert body["ord_qty"] == "30"


def test_cancel_order_sends_correct_body_and_url():
    # 스펙(kt10003)의 responseExample을 그대로 축약
    response = {"ord_no": "0000141", "base_orig_ord_no": "0000139", "cncl_qty": "000000000001", "return_code": 0, "return_msg": "매수취소 주문입력이 완료되었습니다"}
    client = _make_client_with_mock_tr(response)

    result = cancel_order(client, "005930", orig_order_no="0000139", quantity=1)

    assert result["주문번호"] == "0000141"
    assert result["원주문번호"] == "0000139"
    client.request_tr.assert_called_once_with(
        "kt10003",
        {"dmst_stex_tp": "KRX", "orig_ord_no": "0000139", "stk_cd": "005930", "cncl_qty": "1"},
        url_path=ORDER_TR_URL_PATH,
    )


def test_orders_blocked_on_live_domain():
    client = KiwoomRestClient(KiwoomCredentials(appkey="x", secretkey="y"), base_url=LIVE_BASE_URL)
    client.request_tr = MagicMock()

    with pytest.raises(LiveTradingBlockedError):
        place_buy_order(client, "005930", quantity=1, price=70000)
    with pytest.raises(LiveTradingBlockedError):
        place_sell_order(client, "005930", quantity=1, price=70000)
    with pytest.raises(LiveTradingBlockedError):
        cancel_order(client, "005930", orig_order_no="0000139")

    client.request_tr.assert_not_called()  # 안전장치가 실제 TR 호출 전에 막아야 함


def test_account_query_url_path_unchanged():
    assert ACCOUNT_TR_URL_PATH == "/api/dostk/acnt"


# --- 해외(미국)주식(ust21160/ust21070/usa20100/ust21510/ust20000/ust20001/ust20003) --------
# 2026-08-15 사용자 요청("해외 종목으로 테스트할 수 있게 기능 추가")으로 신규 구현. 국내(dostk)
# TR과 URL 경로가 다르고(/api/us/...), 금액이 정수 0-padding이 아니라 부호+소수점 문자열이라
# _parse_amount 대신 _parse_decimal_us를 쓴다.


def test_parse_decimal_us_handles_signed_and_plain_decimals():
    assert _parse_decimal_us("+201.4700") == pytest.approx(201.47)
    assert _parse_decimal_us("-14.82") == pytest.approx(-14.82)
    assert _parse_decimal_us("200.0000") == pytest.approx(200.0)
    assert _parse_decimal_us("") == 0.0
    assert _parse_decimal_us(None) == 0.0


def test_query_deposit_us_parses_ust21160_response():
    response = {
        "won_entr": "000000000017534",
        "d0_usd_fx_entr": "18042538.7700",
        "usd_exch_rate": "1,520.80",
        "return_code": 0,
        "return_msg": "정상적으로 처리되었습니다",
    }
    client = _make_client_with_mock_tr(response)
    result = query_deposit_us(client)

    assert result["원화예수금"] == 17534
    assert result["D0외화예수금(USD)"] == pytest.approx(18042538.77)
    client.request_tr.assert_called_once_with("ust21160", {}, url_path=US_ACCOUNT_TR_URL_PATH)


def test_query_holdings_us_parses_ust21070_response():
    # 스펙(ust21070)의 responseExample 중 종목 1건을 축약해 사용
    response = {
        "result_list": [
            {
                "stk_cd": "NVDA",
                "frgn_stk_nm": "엔비디아",
                "poss_qty": "000000000028",
                "frgn_stk_book_uv": "195.7772",
                "now_pric": "201.4700",
                "pl_amt": "158.3184",
                "pl_rt": "2.86",
            }
        ],
        "return_code": 0,
        "return_msg": "정상적으로 처리되었습니다",
    }
    client = _make_client_with_mock_tr(response)
    holdings = query_holdings_us(client)

    assert len(holdings) == 1
    h = holdings[0]
    assert h["종목코드"] == "NVDA"
    assert h["종목명"] == "엔비디아"
    assert h["보유수량"] == 28
    assert h["매입단가"] == pytest.approx(195.7772)
    assert h["현재가"] == pytest.approx(201.47)
    client.request_tr.assert_called_once_with(
        "ust21070", {"stex_tp": "", "stk_cd": ""}, url_path=US_ACCOUNT_TR_URL_PATH
    )


def test_query_holdings_us_empty_list_when_no_positions():
    response = {"result_list": [], "return_code": 0, "return_msg": "정상적으로 처리되었습니다"}
    client = _make_client_with_mock_tr(response)
    assert query_holdings_us(client) == []


def test_query_current_price_us_takes_absolute_value_of_signed_field():
    # 스펙(usa20100)의 responseExample(NVDA)을 축약해 사용
    response = {
        "stk_nm": "엔비디아", "cur_prc": "+201.4700", "base_close_pric": "200.0400", "curr_unit": "USD",
        "return_code": 0, "return_msg": "정상적으로 처리되었습니다",
    }
    client = _make_client_with_mock_tr(response)
    quote = query_current_price_us(client, "NVDA")

    assert quote["종목명"] == "엔비디아"
    assert quote["현재가"] == pytest.approx(201.47)
    assert quote["전일종가"] == pytest.approx(200.04)
    assert quote["통화"] == "USD"
    client.request_tr.assert_called_once_with(
        "usa20100", {"stex_tp": "ND", "stk_cd": "NVDA"}, url_path=US_MARKET_COND_TR_URL_PATH
    )


def test_query_order_status_us_parses_ust21510_response():
    response = {
        "result_list": [
            {
                "ord_no": "000000282", "stk_cd": "NVDA", "frgn_stk_nm": "엔비디아",
                "ord_qty": "000000000010", "cntr_qty": "000000000010", "cntr_uv": "201.3147",
            }
        ],
        "return_code": 0, "return_msg": "정상적으로 처리되었습니다",
    }
    client = _make_client_with_mock_tr(response)
    results = query_order_status_us(client, ticker="NVDA")

    assert len(results) == 1
    r = results[0]
    assert r["주문번호"] == "000000282"
    assert r["주문수량"] == 10
    assert r["체결수량"] == 10
    assert r["체결단가"] == pytest.approx(201.3147)
    assert r["전량체결"] is True
    client.request_tr.assert_called_once_with(
        "ust21510", {"slby_tp": "0", "stex_tp": "", "stk_cd": "NVDA"}, url_path=US_ACCOUNT_TR_URL_PATH
    )


def test_place_buy_order_us_limit_sends_correct_body_and_url():
    # 스펙(ust20000)의 responseExample을 그대로 축약
    response = {"ord_no": "000000282", "return_code": 0, "return_msg": "미국 매수주문입력 완료되었습니다"}
    client = _make_client_with_mock_tr(response)

    result = place_buy_order_us(client, "NVDA", quantity=10, price=213.04)

    assert result["주문번호"] == "000000282"
    client.request_tr.assert_called_once_with(
        "ust20000",
        {"stex_tp": "ND", "stk_cd": "NVDA", "ord_qty": "10", "ord_uv": "213.04", "trde_tp": "00"},
        url_path=US_ORDER_TR_URL_PATH,
    )


def test_place_buy_order_us_without_price_sends_market_order():
    response = {"ord_no": "000000283", "return_code": 0, "return_msg": "미국 매수주문입력 완료되었습니다"}
    client = _make_client_with_mock_tr(response)

    place_buy_order_us(client, "NVDA", quantity=10)

    body = client.request_tr.call_args[0][1]
    assert body["ord_uv"] == ""
    assert body["trde_tp"] == "03"  # 시장가


def test_place_sell_order_us_sends_correct_body_and_url():
    # 스펙(ust20001)의 responseExample을 그대로 축약
    response = {"ord_no": "000000283", "return_code": 0, "return_msg": "미국 매도주문 입력이 완료되었습니다."}
    client = _make_client_with_mock_tr(response)

    result = place_sell_order_us(client, "NVDA", quantity=10, price=210.05)

    assert result["주문번호"] == "000000283"
    client.request_tr.assert_called_once_with(
        "ust20001",
        {"stk_cd": "NVDA", "stex_tp": "ND", "ord_qty": "10", "ord_uv": "210.05", "stop_pric": "", "trde_tp": "00"},
        url_path=US_ORDER_TR_URL_PATH,
    )


def test_cancel_order_us_sends_correct_body_and_url():
    # 스펙(ust20003)의 responseExample을 그대로 축약
    response = {"ord_no": "000000285", "cncl_ord_qty": "000000000001", "return_code": 0, "return_msg": "미국 매도취소주문 입력이 완료되었습니다."}
    client = _make_client_with_mock_tr(response)

    result = cancel_order_us(client, "NVDA", orig_order_no="000000047")

    assert result["주문번호"] == "000000285"
    assert result["취소수량"] == 1
    client.request_tr.assert_called_once_with(
        "ust20003", {"orig_ord_no": "000000047", "stex_tp": "ND", "stk_cd": "NVDA"}, url_path=US_ORDER_TR_URL_PATH
    )


def test_us_orders_blocked_on_live_domain():
    client = KiwoomRestClient(KiwoomCredentials(appkey="x", secretkey="y"), base_url=LIVE_BASE_URL)
    client.request_tr = MagicMock()

    with pytest.raises(LiveTradingBlockedError):
        place_buy_order_us(client, "NVDA", quantity=10, price=213.04)
    with pytest.raises(LiveTradingBlockedError):
        place_sell_order_us(client, "NVDA", quantity=10, price=213.04)
    with pytest.raises(LiveTradingBlockedError):
        cancel_order_us(client, "NVDA", orig_order_no="000000047")

    client.request_tr.assert_not_called()


# --- 주문 체결 확인(kt00009) ------------------------------------------------


def test_query_order_status_parses_fully_filled_order():
    # 스펙(kt00009)의 responseExample을 그대로 축약 — 주문수량==체결수량(전량체결)
    response = {
        "acnt_ord_cntr_prst_array": [
            {
                "ord_no": "0000050", "stk_cd": "A069500", "stk_nm": "KODEX 200",
                "ord_qty": "0000000001", "ord_uv": "0000000000",
                "cntr_qty": "0000000001", "cntr_uv": "0000004900",
            }
        ],
        "return_code": 0, "return_msg": "조회가 완료되었습니다",
    }
    client = _make_client_with_mock_tr(response)

    result = query_order_status(client, ticker="069500")

    assert len(result) == 1
    row = result[0]
    assert row["주문번호"] == "0000050"
    assert row["주문수량"] == 1
    assert row["체결수량"] == 1
    assert row["체결단가"] == 4900
    assert row["미체결수량"] == 0
    assert row["전량체결"] is True
    client.request_tr.assert_called_once_with(
        "kt00009",
        {"ord_dt": "", "stk_bond_tp": "0", "mrkt_tp": "0", "sell_tp": "0", "qry_tp": "0",
         "stk_cd": "069500", "fr_ord_no": "", "dmst_stex_tp": "KRX"},
    )


def test_query_order_status_detects_partial_fill():
    response = {
        "acnt_ord_cntr_prst_array": [
            {"ord_no": "0000051", "stk_cd": "A005930", "stk_nm": "삼성전자",
             "ord_qty": "0000000010", "cntr_qty": "0000000003", "cntr_uv": "0000070000"}
        ],
        "return_code": 0, "return_msg": "조회가 완료되었습니다",
    }
    client = _make_client_with_mock_tr(response)

    row = query_order_status(client)[0]

    assert row["주문수량"] == 10
    assert row["체결수량"] == 3
    assert row["미체결수량"] == 7
    assert row["전량체결"] is False


def test_query_order_status_empty_list_when_no_orders():
    response = {"acnt_ord_cntr_prst_array": [], "return_code": 0, "return_msg": "조회완료"}
    client = _make_client_with_mock_tr(response)
    assert query_order_status(client) == []
