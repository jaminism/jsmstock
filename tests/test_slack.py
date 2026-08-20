import json

import pytest

from rich_stock.notify import slack


def test_load_webhook_url_reads_default_key(tmp_path):
    path = tmp_path / "slack_credentials.json"
    path.write_text(json.dumps({"webhook_url": "https://hooks.slack.com/services/AAA"}), encoding="utf-8")

    assert slack.load_webhook_url(path) == "https://hooks.slack.com/services/AAA"


def test_load_webhook_url_reads_alternate_key(tmp_path):
    # 다른 채널(예: 요약 리포트)로 보내려면 같은 파일 안에 별도 웹훅 URL을 둘 수 있다.
    path = tmp_path / "slack_credentials.json"
    path.write_text(
        json.dumps({"webhook_url": "https://hooks.slack.com/services/AAA", "summary_webhook_url": "https://hooks.slack.com/services/BBB"}),
        encoding="utf-8",
    )

    assert slack.load_webhook_url(path, key="summary_webhook_url") == "https://hooks.slack.com/services/BBB"


def test_load_webhook_url_raises_for_missing_key(tmp_path):
    path = tmp_path / "slack_credentials.json"
    path.write_text(json.dumps({"webhook_url": "https://hooks.slack.com/services/AAA"}), encoding="utf-8")

    with pytest.raises(KeyError, match="summary_webhook_url"):
        slack.load_webhook_url(path, key="summary_webhook_url")


def test_load_webhook_url_raises_for_missing_file(tmp_path):
    with pytest.raises(FileNotFoundError):
        slack.load_webhook_url(tmp_path / "does_not_exist.json")
