"""导入聊天记录：QQ 导出的 txt，或者本项目自己的 JSON。

QQ 没有公开的聊天记录接口，现实里只有两条路：

1. 导出文件（消息管理器 → 导出为 .txt）——本模块直接解析，离线、不需要登录
2. 挂机器人框架（NapCat / LLOneBot 这类），走 OneBot 协议拿实时消息
   —— 要常驻进程、要账号、有封号风险，不在本模块范围内

所以这里只做第 1 条，并把结果转成 `ChatLog` 交给后面的流程。
"""

import re
from collections import Counter
from pathlib import Path

from .models import ChatLog, Message

# QQ 导出的一行消息头：2023-11-04 15:23:45 小明(10001)
_HEADER = re.compile(
    r"^(?P<date>\d{4}-\d{2}-\d{2}) (?P<hour>\d{2}):(?P<minute>\d{2}):(?P<second>\d{2}) "
    r"(?P<who>.+?)(?:\((?P<qq>\d+)\))?$"
)


def _read_text(path: Path) -> str:
    """QQ 导出的文件常见是 GBK，先试 UTF-8，再退到 GB18030。"""
    data = path.read_bytes()
    for encoding in ("utf-8-sig", "gb18030"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")


def parse_qq_export(text: str, chat_id: str = "qq", title: str = "") -> ChatLog:
    """解析 QQ 导出的文本。

    同一条消息可以有多个段落，所以一直读到下一个消息头为止。
    跨天的记录只保留消息最多的那一天（Message.min 只有 0-1440）。
    """
    raw: list[dict] = []
    current: dict | None = None

    for line in text.splitlines():
        match = _HEADER.match(line.rstrip())
        if match:
            if current is not None:
                raw.append(current)
            current = {
                "date": match["date"],
                "min": int(match["hour"]) * 60 + int(match["minute"]),
                "who": match["who"].strip(),
                "lines": [],
            }
            continue

        if current is None:
            continue  # 文件头、分隔线之前的内容一律跳过
        stripped = line.strip()
        if not stripped or stripped.startswith("===="):
            continue
        current["lines"].append(stripped)

    if current is not None:
        raw.append(current)

    if not raw:
        raise ValueError("这个文件里没有解析出任何消息，确认是 QQ 导出的 txt")

    # 只留消息最多的那一天
    day = Counter(item["date"] for item in raw).most_common(1)[0][0]
    kept = [item for item in raw if item["date"] == day]

    messages = []
    for item in kept:
        body = " ".join(item["lines"]).strip()
        if not body:
            continue
        messages.append(
            Message(i=len(messages) + 1, who=item["who"], min=min(item["min"], 1440), text=body)
        )

    if not messages:
        raise ValueError("这个文件里只有时间戳，没有消息内容")

    return ChatLog(chat_id=chat_id, title=title or chat_id, date=day, messages=messages)


def load_chat(path: Path, title: str | None = None) -> ChatLog:
    """按后缀分派：.json 走自己的格式，其他一律当 QQ 导出的 txt。"""
    if not path.exists():
        raise FileNotFoundError(f"找不到聊天记录文件: {path}")

    if path.suffix.lower() == ".json":
        return ChatLog.model_validate_json(_read_text(path))

    return parse_qq_export(_read_text(path), chat_id=path.stem, title=title or path.stem)
