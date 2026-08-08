"""키움 REST API 연동 — OAuth2 접근토큰 발급.

레거시 Open API+(OCX/COM, `kiwoom/` 패키지, 32비트 전용)와는 완전히 별개의 신버전 API다.
HTTP 기반이라 `requests`만으로 이 프로젝트의 나머지 코드와 동일한 64비트 환경에서 동작하고,
32비트 파이썬/PyQt5/네이티브 로그인 팝업이 필요 없다.

**도메인 구분이 중요하다**: 모의투자는 `mockapi.kiwoom.com`, 실서버는 `api.kiwoom.com` — 반드시
모의투자 도메인으로 시작할 것(`KiwoomRestClient`의 기본값이 모의투자).

**현재 상태(WIP)**: 토큰 발급(POST /oauth2/token)만 문서를 확보해 구현했다. 계좌잔고/보유종목
조회 TR 엔드포인트는 아직 문서를 확보하지 못해 미구현 — 확보되는 대로 이어서 구현할 것.

인증키(appkey/secretkey)는 코드나 대화창에 직접 넣지 않고 로컬 파일(`kiwoom_credentials.json`,
git 추적 제외)에서 읽는다 — load_credentials() 참고.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import requests

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
        self._token = AccessToken(
            token=data["token"], token_type=data["token_type"], expires_dt=data["expires_dt"]
        )
        return self._token

    @property
    def token(self) -> AccessToken:
        if self._token is None:
            self.issue_token()
        return self._token

    def auth_headers(self) -> dict[str, str]:
        return {
            "Content-Type": "application/json;charset=UTF-8",
            "Authorization": f"{self.token.token_type} {self.token.token}",
        }
