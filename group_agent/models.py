"""所有跨节点的数据类型。只放定义，不放逻辑。"""

from enum import Enum
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class Severity(str, Enum):
    HARD = "hard"
    SOFT = "soft"


class ConstraintBase(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
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


Constraint = Annotated[Budget, Field(discriminator="kind")]


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


class GroupState(BaseModel):
    """主循环里共享的状态。目前只有预算约束，后面会变厚。"""
    goal: str = ""
    date: str | None = None
    participants: list[str] = Field(default_factory=list)
    constraints: list[Constraint] = Field(default_factory=list)
    found: dict[str, list[dict[str, Any]]] = Field(default_factory=dict)
    rejections: list[str] = Field(default_factory=list)


class FinalPlan(BaseModel):
    """最终输出。status 只能由主循环决定，规划不能自封。"""
    status: Literal["ok", "needs_input", "infeasible"]
    date: str | None = None
    participants: list[str] = Field(default_factory=list)
    slots: list[Slot] = Field(default_factory=list)
    total_cost: int | None = None
    message: str = ""
