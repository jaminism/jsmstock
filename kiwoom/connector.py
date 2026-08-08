"""키움 Open API+ 연결/로그인/TR 조회 래퍼.

**32비트 전용**이다 — KHOpenAPI.ocx가 32비트 COM 컨트롤이라 반드시 32비트 Python으로
실행해야 한다(.venv32\\Scripts\\python.exe). src/rich_stock의 나머지 코드(백테스트 엔진 등)는
64비트 Anaconda 환경에서 돌아가므로, 이 패키지는 의도적으로 rich_stock을 import하지 않고
독립적으로 유지한다 — pyarrow/duckdb 등 64비트 전용 의존성과 섞이지 않게 하기 위함.

로그인은 키움이 띄우는 네이티브 팝업(ID/비밀번호/공동인증서/서버 선택)에서 사용자가 직접
입력해야 한다 — 프로그램적으로 자동 입력할 방법이 없다(키움 정책상 의도된 제약).
`comm_connect()`는 그 팝업이 완료(OnEventConnect 이벤트 발생)될 때까지 로컬 이벤트 루프로
대기한다.

TR(예수금상세현황요청 opw00001, 계좌평가잔고내역요청 opw00018)의 필드명/코드는 키움 Open API+
공식 개발가이드에 문서화된 표준 명칭을 따랐다 — 실제 서버 응답으로 검증되기 전까지는 최종
확정이 아니므로, 필드가 비거나 예상과 다르면 KOA Studio(키움 제공 개발자 도구)로 TR 스펙을
재확인할 것.
"""

from __future__ import annotations

import sys

from PyQt5.QAxContainer import QAxWidget
from PyQt5.QtCore import QEventLoop, QTimer
from PyQt5.QtWidgets import QApplication

TR_TIMEOUT_MS = 15_000
"""TR 요청 후 OnReceiveTrData가 이 시간 안에 안 오면 강제로 이벤트 루프를 빠져나온다.

계좌비밀번호 미등록 등으로 서버가 TR 자체를 거부하면 OnReceiveTrData가 영영 안 올 수 있어
(경고 메시지만 뜨고 스크립트가 무한 대기하는 문제 실제 발생) 무한 행을 막기 위한 안전장치다."""


class KiwoomAPI:
    def __init__(self) -> None:
        self.app = QApplication.instance() or QApplication(sys.argv)
        self.ocx = QAxWidget("KHOPENAPI.KHOpenAPICtrl.1")

        self._login_loop: QEventLoop | None = None
        self._login_err_code: int | None = None
        self._tr_loop: QEventLoop | None = None
        self._tr_data: dict = {}
        self._tr_timed_out = False
        self.last_server_message: str | None = None

        self.ocx.OnEventConnect.connect(self._on_event_connect)
        self.ocx.OnReceiveTrData.connect(self._on_receive_tr_data)
        self.ocx.OnReceiveMsg.connect(self._on_receive_msg)

    def _on_receive_msg(self, screen_no, rq_name, tr_code, msg) -> None:
        """서버가 보내는 안내/에러 메시지(계좌비밀번호 미등록 경고 등)를 콘솔에도 남긴다.

        키움 OCX가 일부 메시지는 자체적으로 네이티브 팝업도 띄우는데, 그 팝업은 사용자가 직접
        닫아야 하며 그동안 TR 응답도 오지 않아 comm_rq_data가 멈춘 것처럼 보일 수 있다."""
        self.last_server_message = msg
        print(f"[키움 서버 메시지] {msg}")

    # --- 계좌 비밀번호 등록 -------------------------------------------------

    def show_account_password_window(self) -> None:
        """키움이 제공하는 '계좌비밀번호 등록' 창을 띄운다.

        SetInputValue("비밀번호", "")로 빈 문자열을 보내면 서버가 "계좌비밀번호 입력창을 통해
        ... 입력하십시오 (44)" 경고를 보내고 TR을 거부한다 — 계좌 비밀번호는 이 전용 창에서
        한 번 등록해야 이후 세션 동안 빈 문자열로 보내도 TR이 정상 처리된다. query_deposit/
        query_holdings 호출 전에 반드시 이 창에서 등록을 완료해야 한다.
        """
        self.ocx.dynamicCall("KOA_Functions(QString, QString)", "ShowAccountWindow", "")

    # --- 로그인 ---------------------------------------------------------

    def comm_connect(self) -> int:
        """로그인 팝업을 띄우고 사용자가 로그인을 완료할 때까지 대기한다.

        Returns: 로그인 에러코드(0=성공). 팝업에서 서버 선택(모의투자/실서버)도 사용자가 직접 한다.
        """
        self.ocx.dynamicCall("CommConnect()")
        self._login_loop = QEventLoop()
        self._login_loop.exec_()
        return self._login_err_code

    def _on_event_connect(self, err_code: int) -> None:
        self._login_err_code = err_code
        if self._login_loop is not None:
            self._login_loop.exit()

    # --- 로그인 정보 조회 -------------------------------------------------

    def get_login_info(self, tag: str) -> str:
        """tag 예: "ACCLIST"(세미콜론 구분 계좌목록), "USER_ID", "USER_NAME",
        "GetServerGubun"(모의투자 서버 여부, "1"=모의투자)."""
        return self.ocx.dynamicCall("GetLoginInfo(QString)", tag)

    def get_account_list(self) -> list[str]:
        raw = self.get_login_info("ACCLIST")
        return [a for a in raw.split(";") if a]

    def is_mock_server(self) -> bool:
        """모의투자 서버 접속 여부. "1"이면 모의투자, 그 외(보통 "0")면 실서버."""
        return self.get_login_info("GetServerGubun") == "1"

    # --- TR 조회 공용 -----------------------------------------------------

    def set_input_value(self, key: str, value: str) -> None:
        self.ocx.dynamicCall("SetInputValue(QString, QString)", key, value)

    def comm_rq_data(self, rq_name: str, tr_code: str, next_flag: int, screen_no: str) -> bool:
        """TR 요청 후 응답(OnReceiveTrData)을 대기한다.

        Returns: 정상 응답을 받았으면 True, TR_TIMEOUT_MS 안에 응답이 없어 강제 종료했으면 False
        (계좌비밀번호 미등록 등으로 서버가 거부한 경우가 흔한 원인 — last_server_message 확인할 것).
        """
        self.ocx.dynamicCall(
            "CommRqData(QString, QString, int, QString)", rq_name, tr_code, next_flag, screen_no
        )
        self._tr_timed_out = False
        self._tr_loop = QEventLoop()
        timer = QTimer()
        timer.setSingleShot(True)
        timer.timeout.connect(self._on_tr_timeout)
        timer.start(TR_TIMEOUT_MS)
        self._tr_loop.exec_()
        timer.stop()
        return not self._tr_timed_out

    def _on_tr_timeout(self) -> None:
        self._tr_timed_out = True
        if self._tr_loop is not None:
            self._tr_loop.exit()

    def get_comm_data(self, tr_code: str, rq_name: str, index: int, item_name: str) -> str:
        return self.ocx.dynamicCall(
            "GetCommData(QString, QString, int, QString)", tr_code, rq_name, index, item_name
        ).strip()

    def get_repeat_cnt(self, tr_code: str, rq_name: str) -> int:
        return int(self.ocx.dynamicCall("GetRepeatCnt(QString, QString)", tr_code, rq_name) or 0)

    def _on_receive_tr_data(self, screen_no, rq_name, tr_code, record_name, next_flag, *args) -> None:
        self._tr_data = {"screen_no": screen_no, "rq_name": rq_name, "tr_code": tr_code, "next": next_flag}
        if self._tr_loop is not None:
            self._tr_loop.exit()

    # --- 잔고 조회 --------------------------------------------------------

    def query_deposit(self, account_no: str, password: str = "") -> dict | None:
        """예수금상세현황요청(opw00001) — 계좌 예수금/출금가능금액/주문가능금액.

        비밀번호는 빈 문자열로 두되, 그 전에 show_account_password_window()로 계좌 비밀번호를
        미리 등록해둬야 한다 — 안 하면 서버가 TR을 거부하고 이 메서드는 None을 반환한다.
        """
        self.set_input_value("계좌번호", account_no)
        self.set_input_value("비밀번호", password)
        self.set_input_value("비밀번호입력매체구분", "00")
        self.set_input_value("조회구분", "2")
        ok = self.comm_rq_data("예수금상세현황요청", "opw00001", 0, "2000")
        if not ok:
            print(f"[query_deposit] TR 응답 타임아웃 — 서버 메시지: {self.last_server_message}")
            return None
        return {
            "예수금": self.get_comm_data("opw00001", "예수금상세현황요청", 0, "예수금"),
            "출금가능금액": self.get_comm_data("opw00001", "예수금상세현황요청", 0, "출금가능금액"),
            "주문가능금액": self.get_comm_data("opw00001", "예수금상세현황요청", 0, "주문가능금액"),
        }

    # opw00001 응답에 흔히 포함되는 필드 전체 목록 — query_deposit의 3개 필드가 전부 빈 값으로
    # 나오는 문제를 진단하기 위한 용도. 실제 계좌엔 잔고가 있는데(HTS 확인됨) 코드로는 빈 값이
    # 나온 상황 — 필드명이 틀렸는지, TR 자체가 비어있는지 구분하려고 넓게 찍어본다.
    _DEPOSIT_DEBUG_FIELDS = [
        "예수금", "출금가능금액", "주문가능금액", "당일투자원금", "추정예탁자산",
        "가수도정산금", "d+2추정예수금", "D+2추정예수금", "유가잔고평가액", "예탁자산평가액",
        "총대출금", "총평가금액", "총평가손익금", "증거금율", "매도담보대출금",
    ]

    def query_deposit_debug(self, account_no: str, password: str = "") -> dict:
        """opw00001의 알려진 필드를 전부 찍어 실제로 뭐가 채워지는지 확인하는 진단용 메서드."""
        self.set_input_value("계좌번호", account_no)
        self.set_input_value("비밀번호", password)
        self.set_input_value("비밀번호입력매체구분", "00")
        self.set_input_value("조회구분", "2")
        ok = self.comm_rq_data("예수금상세현황요청", "opw00001", 0, "2000")
        if not ok:
            print(f"[query_deposit_debug] TR 응답 타임아웃 — 서버 메시지: {self.last_server_message}")
            return {}
        return {
            field: self.get_comm_data("opw00001", "예수금상세현황요청", 0, field)
            for field in self._DEPOSIT_DEBUG_FIELDS
        }

    def query_holdings(self, account_no: str, password: str = "") -> list[dict] | None:
        """계좌평가잔고내역요청(opw00018) — 보유종목 리스트(다건, GetRepeatCnt로 행 수 조회).

        query_deposit과 동일하게 show_account_password_window() 선행 등록이 필요하다.
        """
        self.set_input_value("계좌번호", account_no)
        self.set_input_value("비밀번호", password)
        self.set_input_value("비밀번호입력매체구분", "00")
        self.set_input_value("조회구분", "2")
        ok = self.comm_rq_data("계좌평가잔고내역요청", "opw00018", 0, "2001")
        if not ok:
            print(f"[query_holdings] TR 응답 타임아웃 — 서버 메시지: {self.last_server_message}")
            return None

        count = self.get_repeat_cnt("opw00018", "계좌평가잔고내역요청")
        holdings = []
        for i in range(count):
            holdings.append(
                {
                    "종목명": self.get_comm_data("opw00018", "계좌평가잔고내역요청", i, "종목명"),
                    "보유수량": self.get_comm_data("opw00018", "계좌평가잔고내역요청", i, "보유수량"),
                    "매입가": self.get_comm_data("opw00018", "계좌평가잔고내역요청", i, "매입가"),
                    "현재가": self.get_comm_data("opw00018", "계좌평가잔고내역요청", i, "현재가"),
                    "평가손익": self.get_comm_data("opw00018", "계좌평가잔고내역요청", i, "평가손익"),
                }
            )
        return holdings
