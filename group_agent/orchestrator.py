"""主循环。目前两个 LLM 节点都是假的，先把骨架跑通。"""

from .models import (
    ActionRequest,
    Budget,
    Constraint,
    FinalDraft,
    FinalPlan,
    GroupState,
    Slot,
    VerificationResult,
)
from .tools import dispatch
from .verifier import PRICE_KEY, verify

MAX_STEPS = 12
MAX_REJECTIONS = 3


def understand(chat: dict) -> tuple[list[str], list[Constraint]]:
    """假理解：直接返回硬编码结果。以后换成真模型。"""
    participants = ["小明", "小王", "小李"]
    constraints: list[Constraint] = [
        Budget(
            id="c1",
            severity="hard",
            evidence="小明：预算别太高，一人一百以内吧",
            per_person_max=100,
        ),
    ]
    return participants, constraints


def plan(state: GroupState) -> ActionRequest | FinalDraft:
    """假规划：按已有数据决定下一步。以后换成真模型。"""
    if "board_game" not in state.found:
        return ActionRequest(tool="search_places", arguments={"kind": "board_game"})
    if "restaurant" not in state.found:
        return ActionRequest(tool="search_places", arguments={"kind": "restaurant"})
    return build_draft(state)


def build_draft(state: GroupState) -> FinalDraft:
    """挑第一家桌游 + 第一家餐厅。钱放进 attrs，骨架不认它。"""
    board = state.found["board_game"][0]
    dinner = state.found["restaurant"][0]
    return FinalDraft(
        date=state.date,
        participants=state.participants,
        slots=[
            Slot(
                start_min=900,
                end_min=1050,
                activity="board_game",
                place_id=board["place_id"],
                place_name=board["name"],
                attrs={PRICE_KEY: board["price_per_person"]},
            ),
            Slot(
                start_min=1065,
                end_min=1140,
                activity="dinner",
                place_id=dinner["place_id"],
                place_name=dinner["name"],
                attrs={PRICE_KEY: dinner["price_per_person"]},
            ),
        ],
    )


def render_rejection(result: VerificationResult) -> str:
    parts = [v.detail for v in result.violations]
    parts += [v.detail for v in result.undecided]
    return "；".join(parts)


def to_final(state: GroupState, draft: FinalDraft, result: VerificationResult) -> FinalPlan:
    headcount = len(draft.participants)
    when = []
    for s in draft.slots:
        when.append(f"{s.start_min // 60:02d}:{s.start_min % 60:02d} 去 {s.place_name}")

    prices = [s.attrs.get(PRICE_KEY) for s in draft.slots]
    if all(p is not None for p in prices):
        per_person = sum(prices)
        total = per_person * headcount
        cost_part = f"。{headcount} 个人，人均约 {per_person} 元，总共 {total} 元左右。"
    else:
        per_person = None
        total = None
        cost_part = f"。{headcount} 个人。"

    message = f"那就定在 {state.date}：" + "，然后 ".join(when) + cost_part
    if result.undecided:
        notes = "；".join(v.detail for v in result.undecided)
        message += f"（还没核实：{notes}）"

    return FinalPlan(
        status="ok",
        date=state.date,
        participants=draft.participants,
        slots=draft.slots,
        total_cost=total,
        message=message,
    )


def run(chat: dict) -> FinalPlan:
    participants, constraints = understand(chat)
    state = GroupState(
        goal=chat.get("title", ""),
        date="2026-11-07",
        participants=participants,
        constraints=constraints,
    )

    for step in range(MAX_STEPS):
        if len(state.rejections) >= MAX_REJECTIONS:
            return FinalPlan(status="infeasible", message="试了几次都不满足条件")

        decision = plan(state)

        if isinstance(decision, FinalDraft):
            result = verify(state.constraints, decision)
            if result.passed:
                return to_final(state, decision, result)
            state.rejections.append(render_rejection(result))
            continue

        observation = dispatch(decision)
        if not observation.ok:
            state.rejections.append(f"{observation.tool} 失败: {observation.error.kind}")
            continue
        state.found[decision.arguments["kind"]] = observation.data

    raise RuntimeError(f"超过 {MAX_STEPS} 步还没出方案")
