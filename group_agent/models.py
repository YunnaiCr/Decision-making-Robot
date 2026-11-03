"""所有跨节点的数据类型。只放定义，不放逻辑。"""

from enum import Enum
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class Severity(str, Enum):
    HARD = "hard"
    SOFT = "soft"


class ConstraintBase(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = ""
    severity: Severity
    who: list[str] = Field(default_factory=list)
    evidence: str


class Budget(ConstraintBase):
    kind: Literal["budget"] = "budget"
    total_max: int | None = Field(default=None, ge=0)
    per_person_max: int | None = Field(default=None, ge=0)
    currency: str = Field(default="CNY", min_length=1)

    @model_validator(mode="after")
    def _need_one_limit(self):
        if self.total_max is None and self.per_person_max is None:
            raise ValueError("budget 至少要有一个上限")
        return self


class Availability(ConstraintBase):
    """who 只在 [free_start_min, free_end_min] 内可用。"""
    kind: Literal["availability"] = "availability"
    free_start_min: int = Field(ge=0, le=1440)
    free_end_min: int = Field(ge=0, le=1440)


class Diet(ConstraintBase):
    """不能碰的东西，匹配 Candidate.tags。"""
    kind: Literal["diet"] = "diet"
    avoid: list[str] = Field(min_length=1)


class Activity(ConstraintBase):
    """方案里必须出现的活动，匹配 Slot.activity。"""
    kind: Literal["activity"] = "activity"
    want: list[str] = Field(min_length=1)


Constraint = Annotated[
    Budget | Diet | Availability | Activity,
    Field(discriminator="kind"),
]


class ErrorInfo(BaseModel):
    kind: Literal["bad_args", "not_found", "upstream", "timeout", "rate_limit", "internal"]
    message: str
    retryable: bool = False


class ActionRequest(BaseModel):
    tool: str
    arguments: dict[str, Any] = Field(default_factory=dict)
    why: str = ""


class Observation(BaseModel):
    ok: bool
    tool: str
    data: Any | None = None
    error: ErrorInfo | None = None


class Slot(BaseModel):
    """一个活动。attrs 装这个活动自己的属性（钱、游戏名等），骨架不认识它们。"""
    start_min: int = Field(ge=0, le=1440)
    end_min: int = Field(ge=0, le=1440)
    activity: str
    place_id: str | None = None
    place_name: str = ""
    attrs: dict[str, Any] = Field(default_factory=dict)


class FinalDraft(BaseModel):
    """规划给出的方案提案。注意没有 status：能不能通过由校验器说。"""
    date: str | None = None
    participants: list[str] = Field(default_factory=list)
    slots: list[Slot] = Field(default_factory=list)


class Verdict(BaseModel):
    """单条约束的判定。ok=None 表示无法判定。"""
    constraint_id: str
    ok: bool | None
    detail: str = ""


class VerificationResult(BaseModel):
    passed: bool
    violations: list[Verdict] = Field(default_factory=list)
    undecided: list[Verdict] = Field(default_factory=list)


class Candidate(BaseModel):
    """工具查回来的一家店。字段为 None 表示不知道，不是没有。"""
    model_config = ConfigDict(extra="forbid")

    place_id: str = Field(pattern=r"^p\d{3}$")
    name: str = Field(min_length=1)
    kind: Literal["board_game", "restaurant"]
    price_per_person: int = Field(ge=0)
    open_min: int = Field(ge=0, le=1440)
    close_min: int = Field(ge=0, le=1440)
    tags: list[str] = Field(default_factory=list)
    noise: Literal["low", "mid", "high"] | None = None
    has_private_room: bool | None = None
    score: float | None = Field(default=None, ge=0, le=5)
    distance_min: int | None = Field(default=None, ge=0)


class GroupState(BaseModel):
    """主循环里共享的状态。目前只有预算约束，后面会变厚。"""
    goal: str = ""
    date: str | None = None
    participants: list[str] = Field(default_factory=list)
    constraints: list[Constraint] = Field(default_factory=list)
    found: dict[str, list[Candidate]] = Field(default_factory=dict)
    unknowns: list[str] = Field(default_factory=list)
    next_id: int = 1
    rejections: list[str] = Field(default_factory=list)


class FinalPlan(BaseModel):
    """最终输出。status 只能由主循环决定，规划不能自封。"""
    status: Literal["ok", "needs_input", "infeasible"]
    date: str | None = None
    participants: list[str] = Field(default_factory=list)
    slots: list[Slot] = Field(default_factory=list)
    total_cost: int | None = None
    message: str = ""
    questions: list[str] = Field(default_factory=list)

class Message(BaseModel):
    model_config = ConfigDict(extra="forbid")

    i: int = Field(ge=1)
    who: str = Field(min_length=1)
    min: int = Field(ge=0)
    text: str


class ChatLog(BaseModel):
    model_config = ConfigDict(extra="forbid")

    chat_id: str
    title: str = ""
    messages: list[Message] = Field(default_factory=list)


class StateDelta(BaseModel):
    """理解层的输出：这次消息让状态改变了什么。"""
    model_config = ConfigDict(extra="forbid")

    date: str | None = None
    participants: list[str] = Field(default_factory=list)
    add_constraints: list[Constraint] = Field(default_factory=list)
    drop_constraints: list[str] = Field(default_factory=list)
    unknowns: list[str] = Field(default_factory=list)


class PlannerDecision(BaseModel):
    """规划层的输出：下一步做什么。没理由的一律拒绝。"""
    model_config = ConfigDict(extra="forbid")

    kind: Literal["act", "final", "ask", "give_up"]
    action: ActionRequest | None = None
    draft: FinalDraft | None = None
    questions: list[str] = Field(default_factory=list)
    reason: str = ""

    @model_validator(mode="after")
    def _check_shape(self):
        if self.kind == "act" and self.action is None:
            raise ValueError("kind=act 必须给 action")
        if self.kind == "final" and self.draft is None:
            raise ValueError("kind=final 必须给 draft")
        if self.kind == "ask" and not self.questions:
            raise ValueError("kind=ask 必须给 questions")
        return self



class MessageOut(BaseModel):
    """表达节点的输出：一句话，只允许改写，不许新增事实。"""
    message: str = Field(min_length=1)
