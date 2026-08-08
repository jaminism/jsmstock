"""키움 Open API+ 연결/로그인/잔고조회 테스트 스크립트.

**반드시 32비트 Python으로 프로젝트 루트에서 모듈로 실행할 것**:
    .venv32\\Scripts\\python.exe -m kiwoom.login_test

실행하면 키움 로그인 팝업이 뜬다 — ID/비밀번호/공동인증서 비밀번호를 직접 입력하고,
서버 선택 화면에서 반드시 **"모의투자"**를 선택해야 한다. 실서버로 접속되면 이 스크립트가
자동으로 감지해 중단한다(안전장치).
"""

from __future__ import annotations

from kiwoom.connector import KiwoomAPI


def main() -> None:
    api = KiwoomAPI()

    print("로그인 창이 뜹니다 — 직접 로그인하고 반드시 '모의투자'로 접속해주세요...")
    err = api.comm_connect()
    if err != 0:
        print(f"로그인 실패 (에러코드: {err})")
        return
    print("로그인 성공")

    if not api.is_mock_server():
        print("⚠️ 경고: 모의투자 서버가 아니라 실서버로 접속됐습니다! 안전을 위해 중단합니다.")
        return
    print("모의투자 서버 접속 확인됨\n")

    accounts = api.get_account_list()
    user_name = api.get_login_info("USER_NAME")
    print(f"사용자명: {user_name}")
    print(f"계좌 목록: {accounts}")

    if not accounts:
        print("계좌가 없습니다.")
        return

    account_no = accounts[0]

    print(f"\n[{account_no}] 예수금 조회 중...")
    deposit = api.query_deposit(account_no)
    for k, v in deposit.items():
        print(f"  {k}: {v}")

    print(f"\n[{account_no}] 보유종목 조회 중...")
    holdings = api.query_holdings(account_no)
    if holdings:
        for h in holdings:
            print(f"  {h}")
    else:
        print("  보유 종목 없음")


if __name__ == "__main__":
    main()
