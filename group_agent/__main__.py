"""入口：python -m group_agent fixtures/chats/e01.json

输出一份 JSON：plan 是活动方案，minutes 是纪要和待办。
"""

import json
import sys
from pathlib import Path

from .minutes import write_minutes
from .models import ChatLog
from .orchestrator import run, understand


def main() -> None:
    if len(sys.argv) < 2:
        print("用法: python -m group_agent <聊天记录.json>")
        raise SystemExit(2)

    text = Path(sys.argv[1]).read_text(encoding="utf-8")
    chat = ChatLog.model_validate_json(text)

    # 理解只跑一次：先拿到整理好的状态，再把它交给主循环复用。
    state = understand(chat)
    plan = run(chat, understand_fn=lambda _: state)

    payload = {"plan": plan.model_dump(mode="json")}
    try:
        payload["minutes"] = write_minutes(chat, plan, state).model_dump(mode="json")
    except Exception as exc:  # 纪要挂了不该让已经算出来的方案出不去
        print(f"纪要生成失败，只输出方案：{exc}", file=sys.stderr)
        payload["minutes"] = None

    print(json.dumps(payload, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
