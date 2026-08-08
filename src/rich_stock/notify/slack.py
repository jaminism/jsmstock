"""Slack Incoming Webhook 발송.

웹훅 URL은 코드나 대화창에 직접 넣지 않고 로컬 파일(`slack_credentials.json`,
git 추적 제외)에서 읽는다 — kiwoom_credentials.json과 동일한 패턴(load_webhook_url() 참고).
"""

from __future__ import annotations

import json
from pathlib import Path

import requests

DEFAULT_CREDENTIALS_PATH = Path(__file__).resolve().parents[3] / "slack_credentials.json"
"""프로젝트 루트(D:\\dev\\rich_stock)/slack_credentials.json — .gitignore에 등록되어 있어야 한다."""


def load_webhook_url(path: Path | str = DEFAULT_CREDENTIALS_PATH) -> str:
    """로컬 JSON 파일에서 webhook_url을 읽는다.

    파일 형식: {"webhook_url": "https://hooks.slack.com/services/..."}
    """
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(
            f"{path} 이 없다. {{'webhook_url': 'https://hooks.slack.com/services/...'}} "
            "형식으로 직접 만들어야 한다 (git에 커밋 금지)."
        )
    data = json.loads(path.read_text(encoding="utf-8"))
    return data["webhook_url"]


def send_message(text: str, webhook_url: str | None = None) -> None:
    """Slack 채널에 텍스트 메시지 한 건을 보낸다.

    webhook_url을 안 넘기면 load_webhook_url()로 로컬 파일에서 읽는다.
    """
    url = webhook_url or load_webhook_url()
    resp = requests.post(url, json={"text": text}, timeout=10)
    resp.raise_for_status()
