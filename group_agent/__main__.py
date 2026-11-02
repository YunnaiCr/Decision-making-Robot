"""入口：python -m group_agent fixtures/chats/e01.json"""

import sys
from pathlib import Path

from .models import ChatLog
from .orchestrator import run


def main() -> None:
    if len(sys.argv) < 2:
        print("用法: python -m group_agent <聊天记录.json>")
        raise SystemExit(2)

    text = Path(sys.argv[1]).read_text(encoding="utf-8")
    chat = ChatLog.model_validate_json(text)
    plan = run(chat)
    print(plan.model_dump_json(indent=2))


if __name__ == "__main__":
    main()
