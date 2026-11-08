"""工具层的测试：正常、边界、失败，全部离线。

规矩和 test_slice 一样：不联网、不需要 key。
"""

import inspect

import pytest

from group_agent.models import (
    ActionRequest,
    Candidate,
    ChatLog,
    FinalDraft,
    GroupState,
    PlannerDecision,
    Slot,
)
from group_agent.orchestrator import run
from group_agent.tools import (
    TOOLS,
    TOOL_SPECS,
    ToolError,
    describe_tools,
    dispatch,
    get_place,
    load_places,
    search_places,
)
from group_agent.verifier import PRICE_KEY


def ids(places: list[Candidate]) -> list[str]:
    return [place.place_id for place in places]


# --- search_places -------------------------------------------------------


def test_search_by_kind():
    assert ids(search_places("board_game")) == ["p001", "p002"]
    assert ids(search_places("restaurant")) == ["p003", "p004", "p005"]


def test_price_limit_is_inclusive():
    # p001 正好 45，边界应该算通过。
    assert ids(search_places("board_game", max_price=45)) == ["p001", "p002"]
    assert ids(search_places("restaurant", max_price=38)) == ["p003"]


def test_open_window_must_be_covered_whole():
    # 阿婆面馆 21:00 关门，排到 21:30 就不行。
    assert ids(search_places("restaurant", open_from=1260, open_to=1290)) == ["p004", "p005"]
    # 只给 open_from 时按“那一刻”算。
    assert ids(search_places("restaurant", open_from=1300)) == ["p004"]


def test_avoid_tags_excludes_hotpot():
    assert ids(search_places("restaurant", avoid_tags=["hotpot"])) == ["p003", "p005"]


def test_max_distance_and_max_noise():
    assert ids(search_places("board_game", max_distance=20)) == ["p001"]
    assert ids(search_places("restaurant", max_noise="low")) == ["p003"]


def test_empty_result_is_not_an_error():
    observation = dispatch(ActionRequest(tool="search_places", arguments={"kind": "restaurant", "max_price": 1}))
    assert observation.ok is True
    assert observation.data == []


@pytest.mark.parametrize(
    "arguments",
    [
        {"kind": "movie"},
        {"kind": "board_game", "max_price": -1},
        {"kind": "board_game", "open_to": 900},
        {"kind": "board_game", "open_from": 900, "open_to": 600},
        {"kind": "board_game", "max_noise": "quiet"},
    ],
)
def test_bad_search_arguments(arguments):
    with pytest.raises(ToolError) as caught:
        search_places(**arguments)
    assert caught.value.kind == "bad_args"


# --- get_place -----------------------------------------------------------


def test_get_place_returns_zero_or_one():
    hit = get_place("p003")
    assert ids(hit) == ["p003"]
    assert hit[0].name == "阿婆面馆"
    assert get_place("p999") == []


def test_get_place_miss_is_empty_not_an_error():
    observation = dispatch(ActionRequest(tool="get_place", arguments={"place_id": "p999"}))
    assert observation.ok is True
    assert observation.data == []


# --- dispatch / 注册表 ----------------------------------------------------


def test_unknown_tool_is_bad_args():
    observation = dispatch(ActionRequest(tool="no_such_tool"))
    assert observation.ok is False
    assert observation.error.kind == "bad_args"


def test_wrong_argument_name_is_bad_args():
    observation = dispatch(ActionRequest(tool="search_places", arguments={"type": "board_game"}))
    assert observation.ok is False
    assert observation.error.kind == "bad_args"
    assert "参数不对" in observation.error.message


def test_unexpected_exception_is_internal(monkeypatch):
    def boom(**kwargs):
        raise RuntimeError("炸了")

    monkeypatch.setitem(TOOLS, "boom", boom)
    observation = dispatch(ActionRequest(tool="boom"))
    assert observation.ok is False
    assert observation.error.kind == "internal"


def test_os_error_is_retryable(monkeypatch):
    def io_fail(**kwargs):
        raise OSError("读不到")

    monkeypatch.setitem(TOOLS, "io_fail", io_fail)
    observation = dispatch(ActionRequest(tool="io_fail"))
    assert observation.ok is False
    assert observation.error.kind == "upstream"
    assert observation.error.retryable is True


def test_tool_returning_none_is_not_found(monkeypatch):
    monkeypatch.setitem(TOOLS, "nothing", lambda **kwargs: None)
    observation = dispatch(ActionRequest(tool="nothing"))
    assert observation.ok is False
    assert observation.error.kind == "not_found"


def test_every_tool_has_a_spec():
    assert {spec.name for spec in TOOL_SPECS} == set(TOOLS)


def test_describe_tools_matches_real_signatures():
    text = describe_tools()
    for spec in TOOL_SPECS:
        assert spec.name in text
        params = inspect.signature(TOOLS[spec.name]).parameters
        for name, param in params.items():
            assert name in text
            if param.default is not inspect.Parameter.empty:
                assert f"{name}={param.default!r}" in text


# --- 工具结果必须能被初版主循环吞下去 -------------------------------------


def test_loop_survives_get_place():
    """工具只回 list[Candidate]，所以 models/orchestrator 不用动。

    以前 get_place 返回单个 Candidate，塞进 found 之后校验阶段会崩。
    """
    seen: list[GroupState] = []

    def fake_plan(state: GroupState) -> PlannerDecision:
        seen.append(state)
        if "get_place" not in state.found:
            return PlannerDecision(
                kind="act",
                action=ActionRequest(tool="get_place", arguments={"place_id": "p001"}),
            )
        return PlannerDecision(
            kind="final",
            draft=FinalDraft(
                participants=["小明"],
                slots=[
                    Slot(
                        start_min=900,
                        end_min=1050,
                        activity="board_game",
                        place_id="p001",
                        place_name="骰子桌游",
                        attrs={PRICE_KEY: 45},
                    )
                ],
            ),
        )

    plan = run(
        ChatLog(chat_id="t1"),
        understand_fn=lambda chat: GroupState(participants=["小明"]),
        plan_fn=fake_plan,
        express_fn=lambda state, draft, result: "好",
    )

    assert plan.status == "ok"
    assert isinstance(seen[-1].found["get_place"], list)
    assert ids(seen[-1].found["get_place"]) == ["p001"]


def test_every_tool_returns_a_list():
    """形状是这个层的契约：不是列表，初版主循环就会在校验阶段炸。"""
    assert len(load_places()) == 5
    assert isinstance(search_places("board_game"), list)
    assert isinstance(get_place("p001"), list)
