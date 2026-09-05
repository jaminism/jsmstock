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


def load_webhook_url(path: Path | str = DEFAULT_CREDENTIALS_PATH, key: str = "webhook_url") -> str:
    """로컬 JSON 파일에서 webhook_url을 읽는다.

    파일 형식: {"webhook_url": "https://hooks.slack.com/services/..."}

    Slack Incoming Webhook은 채널 하나당 URL이 하나라, 다른 채널로 보내려면 별도 웹훅이
    필요하다(예: 요약 리포트를 다른 채널로 보낼 때) — `key`로 같은 파일 안의 다른 항목
    (예: "summary_webhook_url")을 지정할 수 있다.
    """
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(
            f"{path} 이 없다. {{'webhook_url': 'https://hooks.slack.com/services/...'}} "
            "형식으로 직접 만들어야 한다 (git에 커밋 금지)."
        )
    data = json.loads(path.read_text(encoding="utf-8"))
    if key not in data:
        raise KeyError(f"{path} 에 '{key}' 항목이 없다.")
    return data[key]


def resolve_webhook_url(
    key: str, fallback_key: str = "webhook_url", path: Path | str = DEFAULT_CREDENTIALS_PATH
) -> str:
    """`key` 웹훅을 읽되, 아직 등록 전이면 `fallback_key`로 폴백한다.

    채널을 새로 나눌 때(2026-09-05 #jamstock_monitoring) 코드를 먼저 배포하고 웹훅 URL은
    나중에 넣게 되는데, 그 사이에 알림이 **조용히 사라지는 게 가장 나쁘다** — 이 프로젝트가
    반복해서 당한 실패 모드가 정확히 "실패가 조용한 것"이다. 그래서 없으면 예외를 던지는
    대신 기존 채널로 계속 보낸다(시끄러운 쪽이 안전한 쪽).
    """
    try:
        return load_webhook_url(path, key)
    except KeyError:
        return load_webhook_url(path, fallback_key)


def load_credential(key: str, path: Path | str = DEFAULT_CREDENTIALS_PATH):
    """자격증명 파일에서 항목 하나를 읽되, 파일이나 키가 없으면 None을 돌려준다.
    (봇 토큰/채널 ID처럼 "있으면 쓰고 없으면 다른 경로로 폴백"하는 설정용)

    값을 원형 그대로 준다 — 문자열만 받게 좁혀뒀더니 `decisions_channel_enabled` 같은 불리언
    설정을 읽을 수 없었다(2026-09-05). 빈 문자열은 "없음"과 같게 None으로 눌러준다."""
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    value = data.get(key)
    return None if value == "" else value


def _bot_call(method: str, token: str, payload: dict) -> dict:
    """Slack Web API 호출. **`ok: false`를 반드시 예외로 올린다** — HTTP 200에 실패를 담아
    돌려주는 API라, 그냥 두면 `not_in_channel`(봇이 채널에서 빠짐)이나 만료된 토큰이 조용한
    무동작이 된다. 이 프로젝트가 반복해서 당한 게 정확히 "실패가 조용한 것"이다."""
    resp = requests.post(
        f"https://slack.com/api/{method}",
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json; charset=utf-8"},
        json=payload,
        timeout=10,
    )
    resp.raise_for_status()
    body = resp.json()
    if not body.get("ok"):
        raise RuntimeError(f"Slack {method} 실패: {body.get('error')}")
    return body


def _bot_get(method: str, token: str, params: dict) -> dict:
    """읽기 계열 Web API(GET). _bot_call과 같은 이유로 `ok: false`를 예외로 올린다."""
    resp = requests.get(
        f"https://slack.com/api/{method}",
        headers={"Authorization": f"Bearer {token}"},
        params=params,
        timeout=10,
    )
    resp.raise_for_status()
    body = resp.json()
    if not body.get("ok"):
        raise RuntimeError(f"Slack {method} 실패: {body.get('error')}")
    return body


def latest_messages(channel: str, token: str, limit: int = 10) -> list[dict]:
    """채널의 최근 메시지를 최신순으로 돌려준다(conversations.history).

    봇이 그 채널의 멤버여야 하고, 공개 채널은 `channels:history`, 비공개 채널은
    `groups:history` 스코프가 필요하다."""
    return _bot_get("conversations.history", token, {"channel": channel, "limit": limit})["messages"]


def is_bot_message(message: dict) -> bool:
    """봇이 보낸 메시지인가 — 사람이 보낸 것과 구분한다(채널 입장 알림 등 subtype도 사람 취급
    하지 않도록 subtype이 있으면 사람 발화로 보지 않는다)."""
    return bool(message.get("bot_id") or message.get("subtype"))


def post_message(text: str, channel: str, token: str) -> str:
    """봇으로 채널에 새 메시지를 보내고 그 타임스탬프(ts)를 돌려준다 — 나중에
    update_message()로 같은 메시지를 제자리 갱신할 때 이 값이 필요하다."""
    return _bot_call("chat.postMessage", token, {"channel": channel, "text": text})["ts"]


def update_message(text: str, channel: str, ts: str, token: str) -> None:
    """이미 보낸 메시지를 제자리에서 갱신한다(웹훅으로는 불가능).

    **주의**: 수정된 메시지는 채널 맨 아래로 내려오지 않고 안읽음 배지도 안 생긴다. 그래서
    "항상 최신 상태를 한 장으로 보여주는" 용도에는 좋지만, **살아있음을 알리는 용도로는 쓰면
    안 된다** — 갱신이 멈춰도 겉보기가 똑같아서 무음 실패가 된다(생존 핑은 스트림으로 유지)."""
    _bot_call("chat.update", token, {"channel": channel, "ts": ts, "text": text})


class Channel:
    """전송 대상 채널 하나 — 봇 토큰 + 채널 ID가 있으면 봇으로, 없으면 웹훅으로 보낸다.

    **왜 두 경로를 다 남기나(2026-09-05)**: 채널을 늘리거나 봇을 다른 워크스페이스에 붙일 때
    설정이 한 박자 늦게 들어오는데, 그 사이에 알림이 **조용히 사라지는 게 가장 나쁘다**.
    설정이 부족하면 예외를 던지는 대신 기존 웹훅으로 계속 보낸다(시끄러운 쪽이 안전한 쪽).
    봇 경로가 있을 때만 `chat.update`(제자리 갱신)를 쓸 수 있다 — supports_update로 확인."""

    def __init__(self, channel_key: str, webhook_key: str, path: Path | str = DEFAULT_CREDENTIALS_PATH):
        self.path = path
        self.token = load_credential("bot_token", path)
        self.channel_id = load_credential(channel_key, path)
        self.webhook_key = webhook_key

    @property
    def supports_update(self) -> bool:
        return bool(self.token and self.channel_id)

    def send(self, text: str) -> str | None:
        """보내고, 봇 경로였다면 나중에 갱신할 수 있도록 ts를 돌려준다(웹훅이면 None)."""
        if self.supports_update:
            return post_message(text, self.channel_id, self.token)
        send_message(text, webhook_url=resolve_webhook_url(self.webhook_key, path=self.path))
        return None

    def update(self, text: str, ts: str) -> None:
        if not self.supports_update:
            raise RuntimeError("봇 토큰/채널 ID가 없어 메시지를 갱신할 수 없습니다")
        update_message(text, self.channel_id, ts, self.token)


def send_message(text: str, webhook_url: str | None = None) -> None:
    """Slack 채널에 텍스트 메시지 한 건을 보낸다.

    webhook_url을 안 넘기면 load_webhook_url()로 로컬 파일에서 읽는다.
    """
    url = webhook_url or load_webhook_url()
    resp = requests.post(url, json={"text": text}, timeout=10)
    resp.raise_for_status()
