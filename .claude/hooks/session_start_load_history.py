import json
import os

HISTORY_FILE = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "history.md")

MAX_CHARS = 60000


def main():
    output = {}
    if os.path.isfile(HISTORY_FILE) and os.path.getsize(HISTORY_FILE) > 0:
        with open(HISTORY_FILE, "r", encoding="utf-8") as f:
            content = f.read()
        if len(content) > MAX_CHARS:
            content = "...(앞부분 생략, 최근 내용만 표시)...\n\n" + content[-MAX_CHARS:]
        context = (
            "이 프로젝트(rich_stock 자동매매 프로그램)의 이전 대화·작업 기록이 "
            "history.md 파일에 누적 저장되어 있습니다. 아래 내용을 이전 세션의 맥락으로 참고하세요:\n\n"
            + content
        )
        output = {
            "hookSpecificOutput": {
                "hookEventName": "SessionStart",
                "additionalContext": context,
            }
        }
    print(json.dumps(output))


if __name__ == "__main__":
    main()
