"""M0 验收：骨架能不能跑通。"""

import json
from pathlib import Path

from group_agent.models import ActionRequest, Budget, FinalDraft, GroupState, Slot
from group_agent.orchestrator import run, to_final
from group_agent.tools import dispatch
from group_agent.verifier import PRICE_KEY, verify

ROOT = Path(__file__).resolve().parent.parent
CHAT_E01 = ROOT / "fixtures" / "chats" / "e01.json"

THREE = ["小明", "小王", "小李"]


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
    result = verify([budget(50)], draft)
    assert result.passed is False
    assert len(result.violations) == 1
    assert "83" in result.violations[0].detail


def test_budget_passes_within_limit():
    draft = FinalDraft(participants=THREE, slots=two_slots())
    result = verify([budget(100)], draft)
    assert result.passed is True
    assert result.violations == []


def test_soft_constraint_is_ignored():
    draft = FinalDraft(participants=THREE, slots=two_slots())
    result = verify([budget(50, severity="soft")], draft)
    assert result.passed is True
    assert result.violations == []


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
    result = verify([budget(100)], draft)
    assert result.passed is True
    assert len(result.undecided) == 1

    state = GroupState(date="2026-11-07", participants=["小明", "小王"])
    plan = to_final(state, draft, result)
    assert plan.total_cost is None
    assert "还没核实" in plan.message


def test_end_to_end_e01():
    chat = json.loads(CHAT_E01.read_text(encoding="utf-8"))
    plan = run(chat)
    assert plan.status == "ok"
    assert plan.total_cost == 249
    assert [s.place_id for s in plan.slots] == ["p001", "p003"]
