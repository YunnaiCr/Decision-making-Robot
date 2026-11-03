"""骨架 + 契约的测试，全部不联网。"""

from pathlib import Path

from group_agent.models import (
    ActionRequest,
    Activity,
    Availability,
    Budget,
    ChatLog,
    Diet,
    FinalDraft,
    GroupState,
    PlannerDecision,
    Slot,
    StateDelta,
)
from group_agent.orchestrator import apply_delta, run, template_message, to_final
from group_agent.tools import dispatch, load_places
from group_agent.verifier import PRICE_KEY, verify

ROOT = Path(__file__).resolve().parent.parent
CHAT_E01 = ROOT / "fixtures" / "chats" / "e01.json"

THREE = ["小明", "小王", "小李"]
PLACES = {p.place_id: p for p in load_places()}


def budget(limit: int, severity: str = "hard") -> Budget:
    return Budget(id=f"c{limit}", severity=severity, evidence="一人一百以内吧", per_person_max=limit)


def two_slots() -> list[Slot]:
    return [
        Slot(start_min=900, end_min=1050, activity="board_game", place_id="p001",
             place_name="骰子桌游", attrs={PRICE_KEY: 45}),
        Slot(start_min=1065, end_min=1140, activity="dinner", place_id="p003",
             place_name="阿婆面馆", attrs={PRICE_KEY: 38}),
    ]


def test_budget_blocks_over_limit():
    draft = FinalDraft(participants=THREE, slots=two_slots())
    result = verify([budget(50)], draft, PLACES)
    assert result.passed is False
    assert len(result.violations) == 1
    assert "83" in result.violations[0].detail


def test_budget_passes_within_limit():
    draft = FinalDraft(participants=THREE, slots=two_slots())
    result = verify([budget(100)], draft, PLACES)
    assert result.passed is True
    assert result.violations == []


def test_soft_constraint_is_ignored():
    draft = FinalDraft(participants=THREE, slots=two_slots())
    result = verify([budget(50, severity="soft")], draft, PLACES)
    assert result.passed is True


def test_diet_blocks_hotpot():
    draft = FinalDraft(participants=THREE, slots=[
        Slot(start_min=1065, end_min=1140, activity="dinner", place_id="p004", place_name="蜀香火锅"),
    ])
    constraint = Diet(id="c-diet", severity="hard", evidence="小李：别吃火锅", avoid=["hotpot"])
    result = verify([constraint], draft, PLACES)
    assert result.passed is False
    assert "hotpot" in result.violations[0].detail


def test_diet_cannot_decide_without_place():
    draft = FinalDraft(participants=THREE, slots=[
        Slot(start_min=1065, end_min=1140, activity="dinner", place_name="不认识的店"),
    ])
    constraint = Diet(id="c-diet", severity="hard", evidence="小李：别吃火锅", avoid=["hotpot"])
    result = verify([constraint], draft, PLACES)
    assert result.passed is True
    assert len(result.undecided) == 1


def test_availability_blocks_early_slot():
    draft = FinalDraft(participants=THREE, slots=[
        Slot(start_min=840, end_min=1020, activity="board_game", place_name="早场"),
    ])
    constraint = Availability(id="c-avail", severity="hard", evidence="小王：三点之前不行",
                             who=["小王"], free_start_min=900, free_end_min=1440)
    assert verify([constraint], draft, PLACES).passed is False


def test_availability_ignores_absent_person():
    draft = FinalDraft(participants=THREE, slots=two_slots())
    constraint = Availability(id="c-avail", severity="hard", evidence="小张：六点前得走",
                             who=["小张"], free_start_min=600, free_end_min=1080)
    assert verify([constraint], draft, PLACES).passed is True


def test_wrong_argument_name_is_bad_args():
    observation = dispatch(ActionRequest(tool="search_places", arguments={"type": "board_game"}))
    assert observation.ok is False
    assert observation.error.kind == "bad_args"


def test_missing_price_does_not_block_but_is_flagged():
    draft = FinalDraft(participants=["小明", "小王"], slots=[
        Slot(start_min=900, end_min=1050, activity="board_game", place_name="某桌游店"),
    ])
    result = verify([budget(100)], draft, PLACES)
    assert result.passed is True
    assert len(result.undecided) == 1

    state = GroupState(date="2026-11-07", participants=["小明", "小王"])
    plan = to_final(state, draft, result, express_fn=fake_express)
    assert plan.total_cost is None
    assert "还没核实" in template_message(state, draft, result)


def test_apply_delta_assigns_ids():
    delta = StateDelta(add_constraints=[
        Budget(severity="hard", evidence="一人一百", per_person_max=100),
        Diet(severity="hard", evidence="别吃火锅", avoid=["hotpot"]),
    ])
    state = apply_delta(GroupState(), delta)
    assert [c.id for c in state.constraints] == ["c1", "c2"]
    assert state.next_id == 3


def fake_understanding(chat: ChatLog) -> GroupState:
    return GroupState(
        goal=chat.title,
        date="2026-11-07",
        participants=list(THREE),
        constraints=[
            budget(100),
            Diet(id="c-diet", severity="hard", evidence="别吃火锅", avoid=["hotpot"]),
            Availability(id="c-avail", severity="hard", evidence="三点之前不行",
                         who=["小王"], free_start_min=900, free_end_min=1440),
        ],
    )


def fake_plan(state: GroupState) -> PlannerDecision:
    if "board_game" not in state.found:
        return PlannerDecision(kind="act", action=ActionRequest(
            tool="search_places", arguments={"kind": "board_game"}))
    if "restaurant" not in state.found:
        return PlannerDecision(kind="act", action=ActionRequest(
            tool="search_places", arguments={"kind": "restaurant"}))

    board = state.found["board_game"][0]
    dinner = state.found["restaurant"][0]
    draft = FinalDraft(
        date=state.date,
        participants=state.participants,
        slots=[
            Slot(start_min=900, end_min=1050, activity="board_game",
                 place_id=board.place_id, place_name=board.name,
                 attrs={PRICE_KEY: board.price_per_person}),
            Slot(start_min=1065, end_min=1140, activity="dinner",
                 place_id=dinner.place_id, place_name=dinner.name,
                 attrs={PRICE_KEY: dinner.price_per_person}),
        ],
    )
    return PlannerDecision(kind="final", draft=draft)


def fake_express(state, draft, result) -> str:
    return "测试措辞"

def load_e01() -> ChatLog:
    return ChatLog.model_validate_json(CHAT_E01.read_text(encoding="utf-8"))


def test_end_to_end_e01():
    plan = run(load_e01(), understand_fn=fake_understanding, plan_fn=fake_plan, express_fn=fake_express)
    assert plan.status == "ok"
    assert plan.total_cost == 249
    assert [s.place_id for s in plan.slots] == ["p001", "p003"]


def test_needs_input_when_planner_asks():
    def ask_plan(state: GroupState) -> PlannerDecision:
        return PlannerDecision(kind="ask", questions=["哪天去？"])

    plan = run(load_e01(), understand_fn=fake_understanding, plan_fn=ask_plan, express_fn=fake_express)
    assert plan.status == "needs_input"
    assert plan.questions == ["哪天去？"]


def test_give_up_returns_infeasible():
    def give_up_plan(state: GroupState) -> PlannerDecision:
        return PlannerDecision(kind="give_up", reason="预算不够")

    plan = run(load_e01(), understand_fn=fake_understanding, plan_fn=give_up_plan, express_fn=fake_express)
    assert plan.status == "infeasible"
    assert "预算" in plan.message


def test_activity_requires_dinner():
    draft = FinalDraft(participants=THREE, slots=[
        Slot(start_min=900, end_min=1050, activity="board_game", place_name="桌游"),
    ])
    constraint = Activity(id="c-act", severity="hard",
                        evidence="别吃火锅", want=["dinner"])
    result = verify([constraint], draft, PLACES)
    assert result.passed is False
    assert "dinner" in result.violations[0].detail


def test_activity_passes_when_present():
    draft = FinalDraft(participants=THREE, slots=two_slots())
    constraint = Activity(id="c-act", severity="hard",
                        evidence="要吃饭", want=["dinner"])
    assert verify([constraint], draft, PLACES).passed is True


def test_message_has_no_none_when_date_missing():
    draft = FinalDraft(participants=THREE, slots=two_slots())
    result = verify([], draft, PLACES)
    state = GroupState(date=None, participants=list(THREE))
    plan = to_final(state, draft, result, express_fn=fake_express)
    assert plan.date is None
    assert "None" not in plan.message


def test_express_failure_falls_back_to_template():
    def boom(state, draft, result):
        raise RuntimeError("表达节点挂了")

    plan = run(load_e01(), understand_fn=fake_understanding, plan_fn=fake_plan, express_fn=boom)
    assert plan.status == "ok"
    assert "None" not in plan.message
    assert plan.total_cost == 249
