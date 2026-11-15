"""主循环：编排理解、规划、表达三个节点。

约定
- 状态怎么变只发生在这里；节点函数不直接改 state。
- 被打回的理由必须回灌给规划层，否则会重复同一方案。
"""

import json
from typing import Any, Callable

from pydantic import ValidationError

from . import llm
from .models import (
    ActionRequest,
    Candidate,
    ChatLog,
    Constraint,
    FinalDraft,
    FinalPlan,
    GroupState,
    MessageOut,
    PlannerDecision,
    StateDelta,
    VerificationResult,
)
from .prompts import express as express_prompt
from .prompts import planning, understanding
from .tools import dispatch
from .verifier import PRICE_KEY, verify

# 超过这么多步还没给出方案，就放弃。
MAX_STEPS = 12
# 方案被连续打回这么多次，判定为无解。
MAX_REJECTIONS = 3


def format_chat(chat: ChatLog) -> str:
    """编号 + 说话人 + 原文，一行一条。"""
    lines = [f"{m.i:02d} {m.who}：{m.text}" for m in chat.messages]
    return "聊天记录：" + chr(10) + chr(10).join(lines)


def describe_candidate(option: Candidate) -> str:
    """一个候选摊成一行。规划层能看到的候选信息就是这里印出来的这些。"""
    parts = [option.place_id, option.name, f"[{option.category}]"]
    if option.price_per_person is not None:
        parts.append(f"人均{option.price_per_person}")
    if option.open_min is not None and option.close_min is not None:
        parts.append(f"可用{option.open_min}-{option.close_min}")
    if option.distance_min is not None:
        parts.append(f"距离{option.distance_min}分")
    if option.score is not None:
        parts.append(f"评分{option.score}")
    if option.tags:
        parts.append("tags=" + json.dumps(option.tags, ensure_ascii=False))
    if option.attrs:
        parts.append("attrs=" + json.dumps(option.attrs, ensure_ascii=False))
    return " ".join(parts)


def format_state(state: GroupState) -> str:
    """把状态摊成文字。这是规划层能看到的全部信息。"""
    lines: list[str] = []
    date_text = state.date or "还没定"
    who_text = "、".join(state.participants) or "还不知道"
    lines.append(f"目标：{state.goal}")
    lines.append(f"日期：{date_text}")
    lines.append(f"参与者：{who_text}")

    lines.append("约束：")
    if state.constraints:
        for c in state.constraints:
            data = c.model_dump(exclude_none=True)
            lines.append("  - " + json.dumps(data, ensure_ascii=False))
    else:
        lines.append("  （无）")

    lines.append("已经查到的候选：")
    if state.found:
        for group in state.found.values():
            for option in group:
                lines.append("  " + describe_candidate(option))
    else:
        lines.append("  （还没查）")

    if state.facts:
        lines.append("查到的其他信息（不是候选，但排方案时要用）：")
        for key, value in state.facts.items():
            lines.append(f"  - {key} -> {json.dumps(value, ensure_ascii=False)}")

    if state.rejections:
        lines.append("被打回过的原因（不要重复同样的方案）：")
        for r in state.rejections:
            lines.append("  - " + r)

    if state.unknowns:
        lines.append("还不清楚的事：")
        for u in state.unknowns:
            lines.append("  - " + u)

    return chr(10).join(lines)


def extract(chat: ChatLog) -> StateDelta:
    """调模型把聊天记录变成 StateDelta。格式不对重试一次。"""
    user = understanding.RULES + chr(10) + chr(10) + format_chat(chat)
    schema = StateDelta.model_json_schema()
    last_error = ""

    for _ in range(2):
        result = llm.complete_json(system=understanding.SYSTEM, user=user, schema=schema)
        try:
            return StateDelta.model_validate(result.data)
        except ValidationError as exc:
            last_error = str(exc)

    raise RuntimeError("理解层输出不合格式：" + last_error[:300])


def apply_delta(state: GroupState, delta: StateDelta) -> GroupState:
    """把增量应用到状态上。id 由这里统一分配。"""
    if delta.date:
        state.date = delta.date
    if delta.participants:
        state.participants = list(delta.participants)

    dropped = set(delta.drop_constraints)
    kept = [c for c in state.constraints if c.id not in dropped]

    added: list[Constraint] = []
    for c in delta.add_constraints:
        if not c.id:
            c = c.model_copy(update={"id": f"c{state.next_id}"})
            state.next_id += 1
        added.append(c)

    state.constraints = kept + added
    state.unknowns = list(state.unknowns) + list(delta.unknowns)
    return state


def understand(chat: ChatLog) -> GroupState:
    """真理解：抽成增量，应用到空状态上。"""
    delta = extract(chat)
    return apply_delta(GroupState(goal=chat.title, date=chat.date), delta)


def plan(state: GroupState) -> PlannerDecision:
    """调模型决定下一步。格式不对重试一次。"""
    user = planning.RULES + chr(10) + chr(10) + format_state(state)
    schema = PlannerDecision.model_json_schema()
    last_error = ""

    for _ in range(2):
        result = llm.complete_json(system=planning.SYSTEM, user=user, schema=schema)
        try:
            return PlannerDecision.model_validate(result.data)
        except ValidationError as exc:
            last_error = str(exc)

    raise RuntimeError("规划层输出不合格式：" + last_error[:300])


def render_rejection(result: VerificationResult) -> str:
    """把校验结果拼成一句话，回灌给规划层。"""
    parts = [v.detail for v in result.violations]
    parts += [v.detail for v in result.undecided]
    return "；".join(parts)


def candidates_by_id(state: GroupState) -> dict[str, Candidate]:
    """place_id 到候选的索引，校验器查标签用。"""
    return {
        option.place_id: option
        for group in state.found.values()
        if isinstance(group, list)
        for option in group
    }


def remember(state: GroupState, action: ActionRequest, data: Any) -> None:
    """工具结果写回状态：候选进 found，其他事实进 facts。

    规划层下一轮只看得到状态，不写回就等于白查。
    """
    if isinstance(data, Candidate):
        data = [data]
    if isinstance(data, list) and all(isinstance(item, Candidate) for item in data):
        key = action.arguments.get("category") or action.tool
        state.found[key] = data
        return

    args = json.dumps(action.arguments, ensure_ascii=False, sort_keys=True)
    state.facts[f"{action.tool} {args}".strip()] = data


def plan_facts(state: GroupState, draft: FinalDraft) -> dict:
    """expression 节点能看到的全部事实。多一项都不给。"""
    slots = []
    for s in draft.slots:
        slots.append({
            "activity": s.activity,
            "place": s.place_name,
            "start": f"{s.start_min // 60:02d}:{s.start_min % 60:02d}",
            "end": f"{s.end_min // 60:02d}:{s.end_min % 60:02d}",
            "cost_per_person": s.attrs.get(PRICE_KEY),
        })
    prices = [s.attrs.get(PRICE_KEY) for s in draft.slots]
    per_person = sum(prices) if all(p is not None for p in prices) else None
    total = per_person * len(draft.participants) if per_person is not None else None
    return {
        "date": state.date,
        "participants": draft.participants,
        "slots": slots,
        "per_person": per_person,
        "total_cost": total,
    }


def express(state: GroupState, draft: FinalDraft, result: VerificationResult) -> str:
    """调模型把事实改写成一句话。只改措辞，不许新增。"""
    facts = plan_facts(state, draft)
    facts["unverified"] = [v.detail for v in result.undecided]
    user = express_prompt.RULES + chr(10) + chr(10) + json.dumps(facts, ensure_ascii=False, indent=2)
    result_llm = llm.complete_json(
        system=express_prompt.SYSTEM, user=user, schema=MessageOut.model_json_schema()
    )
    return MessageOut.model_validate(result_llm.data).message


def template_message(state: GroupState, draft: FinalDraft, result: VerificationResult) -> str:
    """兜底：模型挂了就用这个，保证方案能出得去。"""
    when = []
    for s in draft.slots:
        when.append(f"{s.start_min // 60:02d}:{s.start_min % 60:02d} 去 {s.place_name}")
    if state.date:
        head = f"那就定在 {state.date}："
    else:
        head = "具体哪天还没定，暂定这样："

    prices = [s.attrs.get(PRICE_KEY) for s in draft.slots]
    if all(p is not None for p in prices):
        per_person = sum(prices)
        total = per_person * len(draft.participants)
        tail = f"。{len(draft.participants)} 个人，人均约 {per_person} 元，总共 {total} 元左右。"
    else:
        tail = f"。{len(draft.participants)} 个人。"

    message = head + "，然后 ".join(when) + tail
    if result.undecided:
        notes = "；".join(v.detail for v in result.undecided)
        message += f"（还没核实：{notes}）"
    return message


def to_final(
    state: GroupState,
    draft: FinalDraft,
    result: VerificationResult,
    express_fn: Callable[[GroupState, FinalDraft, VerificationResult], str] | None = None,
) -> FinalPlan:
    """措辞交给表达节点；它失败就用模板兜底，绝不让方案因为措辞出不去。"""
    fallback = template_message(state, draft, result)
    writer = express_fn or express
    try:
        message = writer(state, draft, result)
    except (llm.LLMError, RuntimeError, ValidationError, KeyError):
        message = fallback

    facts = plan_facts(state, draft)
    return FinalPlan(
        status="ok",
        date=state.date,
        participants=draft.participants,
        slots=draft.slots,
        total_cost=facts["total_cost"],
        message=message,
    )


def run(
    chat: ChatLog,
    understand_fn: Callable[[ChatLog], GroupState] | None = None,
    plan_fn: Callable[[GroupState], PlannerDecision] | None = None,
    express_fn: Callable[[GroupState, FinalDraft, VerificationResult], str] | None = None,
) -> FinalPlan:
    """三个节点默认都用真模型；测试可以各塞一个假的进来。"""
    state = (understand_fn or understand)(chat)
    decide = plan_fn or plan

    for _ in range(MAX_STEPS):
        if len(state.rejections) >= MAX_REJECTIONS:
            return FinalPlan(status="infeasible", message="试了几次都不满足条件")

        decision = decide(state)

        if decision.kind == "give_up":
            return FinalPlan(status="infeasible", message=decision.reason or "没有可行方案")

        if decision.kind == "ask":
            return FinalPlan(
                status="needs_input",
                message=decision.reason or "还差一点信息",
                questions=decision.questions,
            )

        if decision.kind == "final":
            result = verify(state.constraints, decision.draft, candidates_by_id(state))
            if result.passed:
                return to_final(state, decision.draft, result, express_fn)
            state.rejections.append(render_rejection(result))
            continue

        observation = dispatch(decision.action)
        if not observation.ok:
            state.rejections.append(
                f"{observation.tool} 失败({observation.error.kind})：{observation.error.message}"
            )
            continue
        remember(state, decision.action, observation.data)

    raise RuntimeError(f"超过 {MAX_STEPS} 步还没出方案")
