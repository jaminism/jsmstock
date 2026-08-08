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
from PyQt5.QtCore import QEventLoop
from PyQt5.QtWidgets import QApplication


class KiwoomAPI:
    def __init__(self) -> None:
        self.app = QApplication.instance() or QApplication(sys.argv)
        self.ocx = QAxWidget("KHOPENAPI.KHOpenAPICtrl.1")

        self._login_loop: QEventLoop | None = None
        self._login_err_code: int | None = None
        self._tr_loop: QEventLoop | None = None
        self._tr_data: dict = {}

        self.ocx.OnEventConnect.connect(self._on_event_connect)
        self.ocx.OnReceiveTrData.connect(self._on_receive_tr_data)

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

    def comm_rq_data(self, rq_name: str, tr_code: str, next_flag: int, screen_no: str) -> None:
        self.ocx.dynamicCall(
            "CommRqData(QString, QString, int, QString)", rq_name, tr_code, next_flag, screen_no
        )
        self._tr_loop = QEventLoop()
        self._tr_loop.exec_()

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

    def query_deposit(self, account_no: str, password: str = "") -> dict:
        """예수금상세현황요청(opw00001) — 계좌 예수금/출금가능금액/주문가능금액.

        모의투자는 보통 비밀번호 입력이 필요 없어 빈 문자열로 둔다(로그인 시 이미 인증 완료).
        """
        self.set_input_value("계좌번호", account_no)
        self.set_input_value("비밀번호", password)
        self.set_input_value("비밀번호입력매체구분", "00")
        self.set_input_value("조회구분", "2")
        self.comm_rq_data("예수금상세현황요청", "opw00001", 0, "2000")
        return {
            "예수금": self.get_comm_data("opw00001", "예수금상세현황요청", 0, "예수금"),
            "출금가능금액": self.get_comm_data("opw00001", "예수금상세현황요청", 0, "출금가능금액"),
            "주문가능금액": self.get_comm_data("opw00001", "예수금상세현황요청", 0, "주문가능금액"),
        }

    def query_holdings(self, account_no: str, password: str = "") -> list[dict]:
        """계좌평가잔고내역요청(opw00018) — 보유종목 리스트(다건, GetRepeatCnt로 행 수 조회)."""
        self.set_input_value("계좌번호", account_no)
        self.set_input_value("비밀번호", password)
        self.set_input_value("비밀번호입력매체구분", "00")
        self.set_input_value("조회구분", "2")
        self.comm_rq_data("계좌평가잔고내역요청", "opw00018", 0, "2001")

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
