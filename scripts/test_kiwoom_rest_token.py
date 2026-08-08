"""키움 REST API 토큰 발급 테스트.

사전 준비: 프로젝트 루트에 kiwoom_credentials.json 파일을 만들고 아래 내용을 채워넣을 것
(이 파일은 .gitignore에 등록되어 있어 git에 올라가지 않는다):
    {"appkey": "발급받은 appkey", "secretkey": "발급받은 secretkey"}

실행 (64비트 환경, 이 프로젝트의 기본 Anaconda 환경이면 됨):
    python scripts/test_kiwoom_rest_token.py
"""

from __future__ import annotations

from rich_stock.broker.kiwoom_rest import KiwoomRestClient, load_credentials, query_deposit, query_holdings


def main() -> None:
    creds = load_credentials()
    client = KiwoomRestClient(creds)  # 기본값 = 모의투자 도메인(mockapi.kiwoom.com)

    print(f"토큰 발급 요청 중... ({client.base_url}/oauth2/token)")
    token = client.issue_token()

    print("발급 성공")
    print(f"  token_type: {token.token_type}")
    print(f"  expires_dt: {token.expires_dt}")
    print(f"  token(앞 10자리만): {token.token[:10]}...")

    print("\n예수금 조회 중... (kt00001)")
    deposit = query_deposit(client)
    for k, v in deposit.items():
        if k == "_raw":
            continue
        print(f"  {k}: {v:,}" if isinstance(v, int) else f"  {k}: {v}")

    print("\n보유종목 조회 중... (kt00018)")
    holdings = query_holdings(client)
    if holdings:
        for h in holdings:
            print(f"  {h}")
    else:
        print("  보유 종목 없음")


if __name__ == "__main__":
    main()
