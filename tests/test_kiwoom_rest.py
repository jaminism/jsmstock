from unittest.mock import MagicMock

import pytest

from rich_stock.broker.kiwoom_rest import (
    ACCOUNT_TR_URL_PATH,
    LIVE_BASE_URL,
    MARKET_COND_TR_URL_PATH,
    ORDER_TR_URL_PATH,
    KiwoomCredentials,
    KiwoomRestClient,
    LiveTradingBlockedError,
    _parse_amount,
    cancel_order,
    place_buy_order,
    place_sell_order,
    query_current_price,
    query_deposit,
    query_holdings,
    query_order_status,
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
        "stk_cd": "005930", "cur_prc": "+255500", "upl_pric": "+311000", "lst_pric": "-168000",
        "return_code": 0, "return_msg": "조회가 완료되었습니다",
    }
    client = _make_client_with_mock_tr(response)
    quote = query_current_price(client, "005930")

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
