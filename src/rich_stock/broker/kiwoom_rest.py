"""키움 REST API 연동 — OAuth2 접근토큰 발급.

레거시 Open API+(OCX/COM, `kiwoom/` 패키지, 32비트 전용)와는 완전히 별개의 신버전 API다.
HTTP 기반이라 `requests`만으로 이 프로젝트의 나머지 코드와 동일한 64비트 환경에서 동작하고,
32비트 파이썬/PyQt5/네이티브 로그인 팝업이 필요 없다.

**도메인 구분이 중요하다**: 모의투자는 `mockapi.kiwoom.com`, 실서버는 `api.kiwoom.com` — 반드시
모의투자 도메인으로 시작할 것(`KiwoomRestClient`의 기본값이 모의투자).

인증키(appkey/secretkey)는 코드나 대화창에 직접 넣지 않고 로컬 파일(`kiwoom_credentials.json`,
git 추적 제외)에서 읽는다 — load_credentials() 참고.

**TR 요청 공통 규약** (사용자가 확보한 kiwoom-rest-api-spec.json으로 확인):
계좌/시세 등 대부분의 TR은 전부 `POST {base_url}/api/dostk/acnt` 한 엔드포인트를 공유하고,
TR 코드는 body가 아니라 **`api-id` 헤더**로 구분한다. `cont-yn`/`next-key` 헤더는 페이지네이션용
(응답 헤더에 돌아온 값을 다음 요청에 그대로 실어 보내면 이어서 조회). 응답 필드는 전부 문자열이고
금액류는 부호+15자리 0-padding(예: "-00000000004900" = -4,900원) — `_parse_amount()`로 정수 변환.
계좌번호는 body에 넣지 않는다 — 발급받은 appkey/secretkey가 이미 계좌 하나에 연결되어 있다.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

import requests

TOKEN_EXPIRY_BUFFER = timedelta(minutes=5)
"""만료시각(expires_dt) 이 안전마진 전이면 이미 만료된 것으로 취급해 미리 재발급한다."""

ACCOUNT_TR_URL_PATH = "/api/dostk/acnt"
ORDER_TR_URL_PATH = "/api/dostk/ordr"
"""주문(매수/매도/정정/취소) TR은 계좌조회(acnt)와 별개 엔드포인트를 쓴다(kiwoom-rest-api-spec.json
확인) — request_tr()의 url_path 인자로 넘긴다."""

MARKET_COND_TR_URL_PATH = "/api/dostk/mrkcond"
"""시세(현재가/호가) TR도 계좌조회(acnt)와 별개 엔드포인트를 쓴다."""

MOCK_BASE_URL = "https://mockapi.kiwoom.com"
LIVE_BASE_URL = "https://api.kiwoom.com"

DEFAULT_CREDENTIALS_PATH = Path(__file__).resolve().parents[3] / "kiwoom_credentials.json"
"""프로젝트 루트(D:\\dev\\rich_stock)/kiwoom_credentials.json — .gitignore에 등록되어 있어야 한다."""


@dataclass
class KiwoomCredentials:
    appkey: str
    secretkey: str


def load_credentials(path: Path | str = DEFAULT_CREDENTIALS_PATH) -> KiwoomCredentials:
    """로컬 JSON 파일에서 appkey/secretkey를 읽는다.

    파일 형식: {"appkey": "...", "secretkey": "..."}
    이 파일은 git에 절대 커밋하지 않는다(.gitignore에 등록됨) — 인증키가 대화 로그나 원격
    저장소에 남지 않도록, 사용자가 로컬에서 직접 파일을 만들어 채워넣는 방식을 쓴다.
    """
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(
            f"{path} 가 없습니다. 다음 내용으로 파일을 만들어주세요:\n"
            '{"appkey": "발급받은 appkey", "secretkey": "발급받은 secretkey"}'
        )
    data = json.loads(path.read_text(encoding="utf-8"))
    return KiwoomCredentials(appkey=data["appkey"], secretkey=data["secretkey"])


@dataclass
class AccessToken:
    token: str
    token_type: str
    expires_dt: str


class KiwoomRestClient:
    def __init__(self, credentials: KiwoomCredentials, base_url: str = MOCK_BASE_URL) -> None:
        self.credentials = credentials
        self.base_url = base_url
        self._token: AccessToken | None = None

    def issue_token(self) -> AccessToken:
        """POST /oauth2/token — client_credentials 방식으로 접근토큰을 발급받는다."""
        resp = requests.post(
            f"{self.base_url}/oauth2/token",
            headers={"Content-Type": "application/json;charset=UTF-8"},
            json={
                "grant_type": "client_credentials",
                "appkey": self.credentials.appkey,
                "secretkey": self.credentials.secretkey,
            },
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()
        if data.get("return_code", 0) != 0:
            raise RuntimeError(f"[au10001] 토큰 발급 실패: {data.get('return_msg')} (전체 응답: {data})")
        self._token = AccessToken(
            token=data["token"], token_type=data["token_type"], expires_dt=data["expires_dt"]
        )
        return self._token

    def _token_expired(self) -> bool:
        """expires_dt("YYYYMMDDHHMMSS")가 안전마진 안쪽이거나 파싱 불가하면 만료로 취급한다.

        상시 실행되는 데몬(auto-trader-daemon)이 며칠씩 재시작 없이 켜져 있으면 캐시된 토큰이
        만료된 채로 계속 재사용되어 모든 TR이 8005(Token이 유효하지 않습니다)로 실패하는 문제가
        있었다(2026-08-18) — 발급 시점에 None 체크만 하고 만료시각을 아예 확인하지 않던 게 원인.
        """
        try:
            expires_at = datetime.strptime(self._token.expires_dt, "%Y%m%d%H%M%S")
        except (ValueError, TypeError):
            return True
        return datetime.now() >= expires_at - TOKEN_EXPIRY_BUFFER

    @property
    def token(self) -> AccessToken:
        if self._token is None or self._token_expired():
            self.issue_token()
        return self._token

    def auth_headers(self) -> dict[str, str]:
        return {
            "Content-Type": "application/json;charset=UTF-8",
            "Authorization": f"{self.token.token_type} {self.token.token}",
        }

    # --- TR 요청 공용 -----------------------------------------------------

    def request_tr(
        self, api_id: str, body: dict, cont_yn: str = "N", next_key: str = "", url_path: str = ACCOUNT_TR_URL_PATH
    ) -> dict:
        """TR 공용 호출. 응답 JSON을 그대로 반환한다(return_code!=0이면 예외 발생).

        cont_yn/next_key는 연속조회(페이지네이션)용 — 응답 헤더의 값을 다음 호출에 그대로 넘기면
        이어서 조회된다. 첫 호출은 기본값(N, 빈 문자열)이면 된다. url_path는 계좌조회(기본값,
        ACCOUNT_TR_URL_PATH)와 주문(ORDER_TR_URL_PATH)이 서로 다른 엔드포인트를 쓰기 때문에 뒀다.
        """
        headers = {
            **self.auth_headers(),
            "api-id": api_id,
            "cont-yn": cont_yn,
            "next-key": next_key,
        }
        resp = requests.post(f"{self.base_url}{url_path}", headers=headers, json=body, timeout=10)
        resp.raise_for_status()
        data = resp.json()
        if data.get("return_code", 0) != 0:
            if "8005" in str(data.get("return_msg", "")):
                # 만료시각 기반 선제 재발급(_token_expired)을 뚫고 들어온 경우의 안전망 —
                # 서버 측 조기 무효화/시계 오차 등으로 만료시각 계산이 어긋날 수 있다.
                self.issue_token()
                headers = {**self.auth_headers(), "api-id": api_id, "cont-yn": cont_yn, "next-key": next_key}
                resp = requests.post(f"{self.base_url}{url_path}", headers=headers, json=body, timeout=10)
                resp.raise_for_status()
                data = resp.json()
                if data.get("return_code", 0) != 0:
                    raise RuntimeError(f"[{api_id}] TR 실패(토큰 재발급 후 재시도까지): {data.get('return_msg')} (전체 응답: {data})")
                return data
            raise RuntimeError(f"[{api_id}] TR 실패: {data.get('return_msg')} (전체 응답: {data})")
        return data


def _parse_amount(raw: str) -> int:
    """키움 REST 응답의 부호+0-padding 숫자 문자열을 정수로 변환. 빈 문자열은 0으로 취급."""
    raw = (raw or "").strip()
    if not raw:
        return 0
    sign = -1 if raw.startswith("-") else 1
    digits = raw.lstrip("+-") or "0"
    return sign * int(digits)


def query_deposit(client: KiwoomRestClient) -> dict:
    """kt00001(예수금상세현황요청) — 예수금/출금가능금액/주문가능금액 등 핵심 필드만 정리해 반환.

    qry_tp="3"(추정조회) 사용 — 스펙 예시와 동일. 전체 원본 응답은 반환값에 "_raw"로 포함한다.
    """
    data = client.request_tr("kt00001", {"qry_tp": "3"})
    return {
        "예수금": _parse_amount(data.get("entr")),
        "출금가능금액": _parse_amount(data.get("pymn_alow_amt")),
        "주문가능금액": _parse_amount(data.get("ord_alow_amt")),
        "d2추정예수금": _parse_amount(data.get("d2_entra")),
        "_raw": data,
    }


def query_holdings(client: KiwoomRestClient) -> list[dict]:
    """kt00018(계좌평가잔고내역요청) — 보유종목 리스트. qry_tp="1"(합산)/dmst_stex_tp="KRX" 고정."""
    data = client.request_tr("kt00018", {"qry_tp": "1", "dmst_stex_tp": "KRX"})
    holdings = []
    for row in data.get("acnt_evlt_remn_indv_tot", []):
        holdings.append(
            {
                "종목코드": row.get("stk_cd"),
                "종목명": row.get("stk_nm"),
                "보유수량": _parse_amount(row.get("rmnd_qty")),
                "매입가": _parse_amount(row.get("pur_pric")),
                "현재가": _parse_amount(row.get("cur_prc")),
                "평가손익": _parse_amount(row.get("evltv_prft")),
                "수익률(%)": row.get("prft_rt"),
            }
        )
    return holdings


def query_order_status(
    client: KiwoomRestClient, order_date: str | None = None, ticker: str | None = None, from_order_no: str | None = None
) -> list[dict]:
    """kt00009(계좌별주문체결현황요청) — 당일 주문/체결 현황. 주문 제출 응답(ord_no)만으로는
    실제 체결 여부/수량/가격을 알 수 없어서(place_buy_order 등은 접수 응답만 반환), 자동매매가
    "진짜 체결됐는지"를 확인하려면 이 함수로 재조회해야 한다.

    order_date를 생략하면 당일 전체, ticker/from_order_no로 좁혀서 조회할 수 있다(from_order_no는
    "그 번호부터 이후"라 정확히 그 주문 하나만 보려면 반환된 리스트에서 "주문번호"로 다시 필터링
    할 것). 종목당 여러 주문이 있으면 여러 행이 반환된다.
    """
    body = {
        "ord_dt": order_date or "",
        "stk_bond_tp": "0",
        "mrkt_tp": "0",
        "sell_tp": "0",
        "qry_tp": "0",
        "stk_cd": ticker or "",
        "fr_ord_no": from_order_no or "",
        "dmst_stex_tp": "KRX",
    }
    data = client.request_tr("kt00009", body)
    results = []
    for row in data.get("acnt_ord_cntr_prst_array", []):
        ord_qty = _parse_amount(row.get("ord_qty"))
        cntr_qty = _parse_amount(row.get("cntr_qty"))
        results.append(
            {
                "주문번호": row.get("ord_no"),
                "종목코드": row.get("stk_cd"),
                "종목명": row.get("stk_nm"),
                "주문수량": ord_qty,
                "체결수량": cntr_qty,
                "체결단가": _parse_amount(row.get("cntr_uv")),
                "미체결수량": max(ord_qty - cntr_qty, 0),
                "전량체결": ord_qty > 0 and cntr_qty >= ord_qty,
                "_raw": row,
            }
        )
    return results


def query_current_price(client: KiwoomRestClient, ticker: str) -> dict:
    """ka10007(시세표성정보요청) — 실시간 현재가/상하한가 조회. 준실시간(폴링) 진입/청산 감시용.

    cur_prc/upl_pric(상한가)/lst_pric(하한가)는 다른 TR의 금액 필드와 달리 부호가 회계상 음수가
    아니라 "전일 대비 등락 방향"을 나타낸다(실측: 하한가도 "-168000"처럼 항상 마이너스로 옴) —
    그대로 _parse_amount()를 쓰면 하한가가 음수로 잘못 해석되므로 abs()로 감싼다.
    """
    data = client.request_tr("ka10007", {"stk_cd": ticker}, url_path=MARKET_COND_TR_URL_PATH)
    return {
        "종목명": data.get("stk_nm"),
        "현재가": abs(_parse_amount(data.get("cur_prc"))),
        "상한가": abs(_parse_amount(data.get("upl_pric"))),
        "하한가": abs(_parse_amount(data.get("lst_pric"))),
        "_raw": data,
    }


# --- 주문(매수/매도/취소) -------------------------------------------------
#
# 2026-08-09 사용자와 합의한 범위: 모의투자 계좌에서만, 사람이 종목/수량/가격을 직접 지정해
# CLI로 주문 1건씩 보낸다(daily_watchlist 신호를 자동으로 주문까지 연결하는 자동매매는 이번
# 범위 밖). kiwoom-rest-api-spec.json의 kt10000(매수)/kt10001(매도)/kt10003(취소) 스펙을 그대로
# 따른다 — 정정(kt10002)은 이번 범위에서 빠짐(필요해지면 같은 패턴으로 추가).

ORDER_TYPE_LIMIT = "0"
"""매매구분 코드 "0" = 보통(지정가). price를 지정하면 이 방식으로 주문한다."""

ORDER_TYPE_MARKET = "3"
"""매매구분 코드 "3" = 시장가. price를 안 넘기면(None) 이 방식으로 주문한다."""


class LiveTradingBlockedError(RuntimeError):
    """실서버(모의투자 아님) 도메인으로 주문을 보내려는 시도를 막는 안전장치가 발동했을 때."""


def _require_mock_account(client: KiwoomRestClient) -> None:
    if client.base_url != MOCK_BASE_URL:
        raise LiveTradingBlockedError(
            f"실서버({client.base_url})로는 주문을 보낼 수 없습니다 — 현재는 모의투자만 허용됩니다"
            f"(2026-08-09 사용자 합의 범위). 실거래가 필요해지면 이 안전장치를 의도적으로 풀어야 함."
        )


def place_buy_order(client: KiwoomRestClient, ticker: str, quantity: int, price: int | None = None) -> dict:
    """kt10000(주식 매수주문). price를 안 넘기면 시장가, 넘기면 그 가격의 지정가로 주문한다.

    모의투자 계좌(client.base_url == MOCK_BASE_URL)에서만 허용 — 아니면 LiveTradingBlockedError.
    """
    _require_mock_account(client)
    body = {
        "dmst_stex_tp": "KRX",
        "stk_cd": ticker,
        "ord_qty": str(int(quantity)),
        "ord_uv": "" if price is None else str(price),
        "trde_tp": ORDER_TYPE_MARKET if price is None else ORDER_TYPE_LIMIT,
        "cond_uv": "",
    }
    data = client.request_tr("kt10000", body, url_path=ORDER_TR_URL_PATH)
    return {"주문번호": data.get("ord_no"), "메시지": data.get("return_msg"), "_raw": data}


def place_sell_order(client: KiwoomRestClient, ticker: str, quantity: int, price: int | None = None) -> dict:
    """kt10001(주식 매도주문). place_buy_order와 동일 규칙(가격 미지정 시 시장가)."""
    _require_mock_account(client)
    body = {
        "dmst_stex_tp": "KRX",
        "stk_cd": ticker,
        "ord_qty": str(int(quantity)),
        "ord_uv": "" if price is None else str(price),
        "trde_tp": ORDER_TYPE_MARKET if price is None else ORDER_TYPE_LIMIT,
        "cond_uv": "",
    }
    data = client.request_tr("kt10001", body, url_path=ORDER_TR_URL_PATH)
    return {"주문번호": data.get("ord_no"), "메시지": data.get("return_msg"), "_raw": data}


def cancel_order(client: KiwoomRestClient, ticker: str, orig_order_no: str, quantity: int = 0) -> dict:
    """kt10003(주식 취소주문). quantity=0(기본값)이면 해당 주문의 잔량 전부를 취소한다."""
    _require_mock_account(client)
    body = {
        "dmst_stex_tp": "KRX",
        "orig_ord_no": orig_order_no,
        "stk_cd": ticker,
        "cncl_qty": str(int(quantity)),
    }
    data = client.request_tr("kt10003", body, url_path=ORDER_TR_URL_PATH)
    return {"주문번호": data.get("ord_no"), "원주문번호": data.get("base_orig_ord_no"), "메시지": data.get("return_msg"), "_raw": data}


# --- 해외(미국)주식 조회/주문 -------------------------------------------------
#
# 2026-08-15 사용자 요청("해외 종목으로 테스트할 수 있게 기능 추가") — 그 전까지는 국내(KRX)
# TR만 구현돼 있었다(위 함수들 전부 dostk/*). kiwoom-rest-api-spec.json 확인 결과 모의투자
# 서버(mockapi.kiwoom.com) 자체는 미국주식 주문(ust20000/20001/20003)을 지원하고, URL 경로만
# 국내(/api/dostk/...)와 다르다(/api/us/...) — 계좌번호는 국내와 동일한 계좌(appkey/secretkey)에
# 연결된 해외증권 파트를 그대로 쓴다. 이 프로젝트의 6개 매매기법(S1~S6)은 전부 KRX 기반이라
# 자동매매(auto_trader.py)에서는 쓰지 않고, 수동 CLI 테스트 용도로만 추가한다
# (scripts/local/kiwoom_order.py의 *-us 서브커맨드 참고).

US_ACCOUNT_TR_URL_PATH = "/api/us/acnt"
US_ORDER_TR_URL_PATH = "/api/us/ordr"
US_MARKET_COND_TR_URL_PATH = "/api/us/mrkcond"

US_ORDER_TYPE_LIMIT = "00"
"""미국주식 해외매매구분 코드 "00" = 지정가. 국내(ORDER_TYPE_LIMIT="0")와 자릿수가 다르므로
섞어 쓰지 않도록 별도 상수로 둔다."""

US_ORDER_TYPE_MARKET = "03"
"""미국주식 해외매매구분 코드 "03" = 시장가."""

DEFAULT_US_EXCHANGE = "ND"
"""거래소구분(stex_tp) 기본값 — NASDAQ. 다른 값: NY(NYSE), NA(AMEX)."""


def _parse_decimal_us(raw: str | None) -> float:
    """미국주식 TR 응답의 숫자 문자열을 float로 변환. 국내(정수, 15자리 0-padding)와 달리
    미국은 소수점(센트) 단위이고, 부호가 붙은 필드(+/-)도 파이썬 float()가 그대로 처리한다.
    다만 환율(usd_exch_rate) 등 일부 필드는 스펙상 "세자릿수 콤마" 포맷("1,520.80")이라
    콤마를 먼저 제거해야 한다."""
    raw = (raw or "").strip().replace(",", "")
    return float(raw) if raw else 0.0


def query_deposit_us(client: KiwoomRestClient) -> dict:
    """ust21160(미국주식 예수금 상세) — 해외증권 파트의 예수금 현황. 국내 query_deposit()과
    별개 TR(계좌 자체는 같음, 원화/외화 예수금이 구분 관리됨)."""
    data = client.request_tr("ust21160", {}, url_path=US_ACCOUNT_TR_URL_PATH)
    return {
        "원화예수금": _parse_amount(data.get("won_entr")),
        "D0외화예수금(USD)": _parse_decimal_us(data.get("d0_usd_fx_entr")),
        "매도환율(USD)": _parse_decimal_us(data.get("usd_exch_rate")),
        "_raw": data,
    }


def query_holdings_us(client: KiwoomRestClient) -> list[dict]:
    """ust21070(미국주식 원장잔고확인) — 해외 보유종목 리스트. stex_tp/stk_cd를 비워두면 전체 조회."""
    data = client.request_tr("ust21070", {"stex_tp": "", "stk_cd": ""}, url_path=US_ACCOUNT_TR_URL_PATH)
    holdings = []
    for row in data.get("result_list", []):
        holdings.append(
            {
                "종목코드": row.get("stk_cd"),
                "종목명": row.get("frgn_stk_nm"),
                "보유수량": _parse_amount(row.get("poss_qty")),
                "매입단가": _parse_decimal_us(row.get("frgn_stk_book_uv")),
                "현재가": _parse_decimal_us(row.get("now_pric")),
                "손익금액": _parse_decimal_us(row.get("pl_amt")),
                "손익율(%)": row.get("pl_rt"),
            }
        )
    return holdings


def query_current_price_us(client: KiwoomRestClient, ticker: str, exchange: str = DEFAULT_US_EXCHANGE) -> dict:
    """usa20100(미국주식 현재가 종목정보) — 현재가/상하한가/전일종가 조회.

    cur_prc는 스펙 예시상 "+201.4700"처럼 부호가 붙어 오는데, 국내 ka10007에서 실측으로 확인된
    "부호=등락방향"(회계상 음수가 아님) 관례와 동일해 보이지만, 이 TR 자체로는 아직 실제 모의투자
    호출로 검증하지 않았다(2026-08-15 스펙 문서만으로 구현) — abs()를 적용해두되, 실사용 중 하한가
    근처 등에서 부호가 진짜 등락방향인지 실측 확인이 필요하다."""
    data = client.request_tr(
        "usa20100", {"stex_tp": exchange, "stk_cd": ticker}, url_path=US_MARKET_COND_TR_URL_PATH
    )
    return {
        "종목명": data.get("stk_nm"),
        "현재가": abs(_parse_decimal_us(data.get("cur_prc"))),
        "전일종가": _parse_decimal_us(data.get("base_close_pric")),
        "통화": data.get("curr_unit"),
        "_raw": data,
    }


def query_order_status_us(
    client: KiwoomRestClient, ticker: str | None = None, exchange: str | None = None
) -> list[dict]:
    """ust21510(미국주식 당일 주문체결 확인) — 국내 query_order_status()의 해외 버전. 주문 제출
    응답만으로는 실제 체결 여부를 알 수 없어 체결 확인은 이 함수로 재조회해야 한다."""
    body = {"slby_tp": "0", "stex_tp": exchange or "", "stk_cd": ticker or ""}
    data = client.request_tr("ust21510", body, url_path=US_ACCOUNT_TR_URL_PATH)
    results = []
    for row in data.get("result_list", []):
        ord_qty = _parse_amount(row.get("ord_qty"))
        cntr_qty = _parse_amount(row.get("cntr_qty"))
        results.append(
            {
                "주문번호": row.get("ord_no"),
                "종목코드": row.get("stk_cd"),
                "종목명": row.get("frgn_stk_nm"),
                "주문수량": ord_qty,
                "체결수량": cntr_qty,
                "체결단가": _parse_decimal_us(row.get("cntr_uv")),
                "미체결수량": max(ord_qty - cntr_qty, 0),
                "전량체결": ord_qty > 0 and cntr_qty >= ord_qty,
                "_raw": row,
            }
        )
    return results


def place_buy_order_us(
    client: KiwoomRestClient, ticker: str, quantity: int, price: float | None = None, exchange: str = DEFAULT_US_EXCHANGE
) -> dict:
    """ust20000(미국주식 매수 주문). price를 안 넘기면 시장가, 넘기면 그 가격의 지정가로 주문한다
    (price는 달러 소수점 포함, 예: 213.04). place_buy_order()와 동일하게 모의투자 계좌에서만 허용."""
    _require_mock_account(client)
    body = {
        "stex_tp": exchange,
        "stk_cd": ticker,
        "ord_qty": str(int(quantity)),
        "ord_uv": "" if price is None else str(price),
        "trde_tp": US_ORDER_TYPE_MARKET if price is None else US_ORDER_TYPE_LIMIT,
    }
    data = client.request_tr("ust20000", body, url_path=US_ORDER_TR_URL_PATH)
    return {"주문번호": data.get("ord_no"), "메시지": data.get("return_msg"), "_raw": data}


def place_sell_order_us(
    client: KiwoomRestClient, ticker: str, quantity: int, price: float | None = None, exchange: str = DEFAULT_US_EXCHANGE
) -> dict:
    """ust20001(미국주식 매도 주문). place_buy_order_us와 동일 규칙(가격 미지정 시 시장가)."""
    _require_mock_account(client)
    body = {
        "stk_cd": ticker,
        "stex_tp": exchange,
        "ord_qty": str(int(quantity)),
        "ord_uv": "" if price is None else str(price),
        "stop_pric": "",
        "trde_tp": US_ORDER_TYPE_MARKET if price is None else US_ORDER_TYPE_LIMIT,
    }
    data = client.request_tr("ust20001", body, url_path=US_ORDER_TR_URL_PATH)
    return {"주문번호": data.get("ord_no"), "메시지": data.get("return_msg"), "_raw": data}


def cancel_order_us(client: KiwoomRestClient, ticker: str, orig_order_no: str, exchange: str = DEFAULT_US_EXCHANGE) -> dict:
    """ust20003(미국주식 취소 주문). 국내 kt10003과 달리 부분취소 수량 지정이 스펙에 없어
    (요청 필드에 취소수량 항목 자체가 없음) 해당 주문의 잔량 전부를 취소하는 것만 가능하다."""
    _require_mock_account(client)
    body = {"orig_ord_no": orig_order_no, "stex_tp": exchange, "stk_cd": ticker}
    data = client.request_tr("ust20003", body, url_path=US_ORDER_TR_URL_PATH)
    return {"주문번호": data.get("ord_no"), "취소수량": _parse_amount(data.get("cncl_ord_qty")), "메시지": data.get("return_msg"), "_raw": data}
