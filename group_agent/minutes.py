"""纪要层：把整理好的群聊 + 最终方案，压成一份纪要和待办。

约定
- 只读输入（聊天、状态、方案），不写回 `GroupState`，所以不碰主循环。
- 输出的类型定义在这里，不动 `models.py` 的契约：方案是 `FinalPlan`，
  纪要是 `Minutes`，两者并排交给入口，而不是塞进同一个对象。
- 失败就抛错，由入口决定要不要降级成“只输出方案”。
"""

import json
from typing import Any, Callable

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from . import llm
from .models import ChatLog, FinalPlan, GroupState
from .prompts import minutes as minutes_prompt
from .verifier import PRICE_KEY

# 和 llm.complete_json 同签名，测试塞假实现进来就不联网。
Complete = Callable[..., llm.LLMResult]


class Todo(BaseModel):
    """一件要有人去做的事。who 不确定就留空，由调用方决定怎么显示。"""

    model_config = ConfigDict(extra="forbid")

    who: str = ""
    what: str = Field(min_length=1)
    due: str | None = None
    evidence: str = ""


class Minutes(BaseModel):
    """一次群聊的纪要：定了什么、还差什么、谁要做什么。"""

    model_config = ConfigDict(extra="forbid")

    participants: list[str] = Field(default_factory=list)
    decisions: list[str] = Field(default_factory=list)
    open_questions: list[str] = Field(default_factory=list)
    todos: list[Todo] = Field(default_factory=list)


def hhmm(minute: int) -> str:
    """分钟数转 HH:MM，提示词和输出都用这个格式。"""
    return f"{minute // 60:02d}:{minute % 60:02d}"


def build_facts(chat: ChatLog, plan: FinalPlan, state: GroupState | None = None) -> dict[str, Any]:
    """纪要层能看到的全部事实。方案里的数字和它对齐，避免两处对不上。"""
    facts: dict[str, Any] = {
        "chat": [{"i": m.i, "who": m.who, "text": m.text} for m in chat.messages],
        "plan": {
            "status": plan.status,
            "date": plan.date,
            "participants": plan.participants,
            "slots": [
                {
                    "activity": slot.activity,
                    "place": slot.place_name,
                    "start": hhmm(slot.start_min),
                    "end": hhmm(slot.end_min),
                    "cost_per_person": slot.attrs.get(PRICE_KEY),
                }
                for slot in plan.slots
            ],
            "total_cost": plan.total_cost,
            "message": plan.message,
        },
    }
    if state is not None:
        facts["constraints"] = [c.model_dump(exclude_none=True) for c in state.constraints]
        facts["unknowns"] = list(state.unknowns)
    return facts


def write_minutes(
    chat: ChatLog,
    plan: FinalPlan,
    state: GroupState | None = None,
    complete: Complete | None = None,
) -> Minutes:
    """调一次模型，产出纪要。格式不对重试一次，两次都不对就抛错。"""
    send = complete or llm.complete_json
    user = minutes_prompt.RULES + chr(10) + chr(10) + json.dumps(
        build_facts(chat, plan, state), ensure_ascii=False, indent=2
    )
    schema = Minutes.model_json_schema()
    last_error = ""

    for _ in range(2):
        result = send(system=minutes_prompt.SYSTEM, user=user, schema=schema)
        try:
            return Minutes.model_validate(result.data)
        except ValidationError as exc:
            last_error = str(exc)

    raise RuntimeError("纪要层输出不合格式：" + last_error[:300])
