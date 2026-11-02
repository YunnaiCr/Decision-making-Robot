"""M0 验收：骨架能不能跑通。"""

from pathlib import Path

from group_agent.models import (
    ActionRequest,
    Availability,
    Budget,
    ChatLog,
    Diet,
    FinalDraft,
    GroupState,
    Slot,
)
from group_agent.orchestrator import run, to_final
from group_agent.tools import dispatch, load_places
from group_agent.verifier import PRICE_KEY, verify

ROOT = Path(__file__).resolve().parent.parent
CHAT_E01 = ROOT / "fixtures" / "chats" / "e01.json"

THREE = ["小明", "小王", "小李"]
PLACES = {p.place_id: p for p in load_places()}


def budget(limit: int, severity: str = "hard") -> Budget:
    return Budget(
        id=f"c{limit}",
        severity=severity,
        evidence="一人一百以内吧",
        per_person_max=limit,
    )


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
    assert result.violations == []


def test_diet_blocks_hotpot():
    draft = FinalDraft(participants=THREE, slots=[
        Slot(start_min=1065, end_min=1140, activity="dinner",
             place_id="p004", place_name="蜀香火锅"),
    ])
    constraint = Diet(id="c-diet", severity="hard",
                      evidence="小李：别吃火锅", avoid=["hotpot"])
    result = verify([constraint], draft, PLACES)
    assert result.passed is False
    assert "hotpot" in result.violations[0].detail


def test_diet_cannot_decide_without_place():
    draft = FinalDraft(participants=THREE, slots=[
        Slot(start_min=1065, end_min=1140, activity="dinner", place_name="不认识的店"),
    ])
    constraint = Diet(id="c-diet", severity="hard",
                      evidence="小李：别吃火锅", avoid=["hotpot"])
    result = verify([constraint], draft, PLACES)
    assert result.passed is True
    assert len(result.undecided) == 1


def test_availability_blocks_early_slot():
    draft = FinalDraft(participants=THREE, slots=[
        Slot(start_min=840, end_min=1020, activity="board_game", place_name="早场"),
    ])
    constraint = Availability(id="c-avail", severity="hard",
                             evidence="小王：三点之前不行", who=["小王"],
                             free_start_min=900, free_end_min=1440)
    result = verify([constraint], draft, PLACES)
    assert result.passed is False


def test_availability_ignores_absent_person():
    draft = FinalDraft(participants=THREE, slots=two_slots())
    constraint = Availability(id="c-avail", severity="hard",
                             evidence="小张：六点前得走", who=["小张"],
                             free_start_min=600, free_end_min=1080)
    result = verify([constraint], draft, PLACES)
    assert result.passed is True


def test_wrong_argument_name_is_bad_args():
    observation = dispatch(
        ActionRequest(tool="search_places", arguments={"type": "board_game"})
    )
    assert observation.ok is False
    assert observation.error.kind == "bad_args"


def test_missing_price_does_not_block_but_is_flagged():
    draft = FinalDraft(
        participants=["小明", "小王"],
        slots=[Slot(start_min=900, end_min=1050, activity="board_game",
                    place_name="某桌游店")],
    )
    result = verify([budget(100)], draft, PLACES)
    assert result.passed is True
    assert len(result.undecided) == 1

    state = GroupState(date="2026-11-07", participants=["小明", "小王"])
    plan = to_final(state, draft, result)
    assert plan.total_cost is None
    assert "还没核实" in plan.message


def test_end_to_end_e01():
    chat = ChatLog.model_validate_json(CHAT_E01.read_text(encoding="utf-8"))
    plan = run(chat)
    assert plan.status == "ok"
    assert plan.total_cost == 249
    assert [s.place_id for s in plan.slots] == ["p001", "p003"]
