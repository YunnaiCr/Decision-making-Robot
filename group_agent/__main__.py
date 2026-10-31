"""入口：python -m group_agent fixtures/chats/e01.json"""

import json
import sys
from pathlib import Path

from .orchestrator import run


def main() -> None:
    if len(sys.argv) < 2:
        print("用法: python -m group_agent <聊天记录.json>")
        raise SystemExit(2)

    chat = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    plan = run(chat)
    print(json.dumps(plan.model_dump(), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
