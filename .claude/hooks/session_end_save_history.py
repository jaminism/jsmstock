import json
import os
import sys
import datetime

HISTORY_FILE = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "history.md")


def extract_text(content):
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for block in content:
            if not isinstance(block, dict):
                continue
            btype = block.get("type")
            if btype == "text":
                parts.append(block.get("text", ""))
            elif btype == "tool_use":
                parts.append("*[tool 호출: %s]*" % block.get("name", ""))
            # tool_result, thinking, image 등은 기록에서 제외
        return "\n".join(p for p in parts if p)
    return ""


def main():
    try:
        raw = sys.stdin.read()
        data = json.loads(raw) if raw.strip() else {}
    except Exception:
        data = {}

    session_id = data.get("session_id", "unknown")
    transcript_path = data.get("transcript_path", "")
    reason = data.get("reason", "unknown")
    timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    lines = ["", "---", "## Session %s — %s (종료 사유: %s)" % (session_id, timestamp, reason), ""]

    if transcript_path and os.path.isfile(transcript_path):
        try:
            with open(transcript_path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        entry = json.loads(line)
                    except Exception:
                        continue
                    etype = entry.get("type")
                    if etype not in ("user", "assistant"):
                        continue
                    message = entry.get("message", {})
                    role = message.get("role", etype)
                    text = extract_text(message.get("content", ""))
                    if not text.strip():
                        continue
                    lines.append("**%s**" % role.upper())
                    lines.append(text)
                    lines.append("")
        except Exception as e:
            lines.append("_(transcript 읽기 실패: %s)_" % e)
    else:
        lines.append("_(transcript 파일을 찾을 수 없음 — 기록 생략)_")

    with open(HISTORY_FILE, "a", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")

    print(json.dumps({}))


if __name__ == "__main__":
    main()
