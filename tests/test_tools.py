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
    alternatives,
    describe_tools,
    dispatch,
    filter_places,
    get_place,
    load_places,
    load_travel,
    nearby_places,
    rank_places,
    search_places,
)
from group_agent.verifier import PRICE_KEY


def ids(places: list[Candidate]) -> list[str]:
    return [place.place_id for place in places]


# 每个工具一份能跑通的参数，下面几个“全量”测试都用它。
CALLS: dict[str, dict] = {
    "search_places": {"kind": "board_game"},
    "get_place": {"place_id": "p001"},
    "filter_places": {"place_ids": ["p001", "p002"], "max_price": 40},
    "rank_places": {"place_ids": ["p001", "p003"], "by": "price"},
    "alternatives": {"place_id": "p001"},
    "nearby_places": {"from_place_id": "p001", "max_travel_min": 10},
}


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


def test_min_score_and_private_room():
    assert ids(search_places("board_game", min_score=4.5)) == ["p001"]
    # p003 的 has_private_room 是 None，缺数据不算满足。
    assert ids(search_places("restaurant", need_private_room=True)) == ["p004", "p005"]


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
        {"kind": "board_game", "min_score": 9},
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


# --- filter_places -------------------------------------------------------


def test_filter_places_keeps_input_order():
    assert ids(filter_places(["p003", "p001"], max_price=50)) == ["p003", "p001"]
    assert ids(filter_places(["p003", "p001"], max_price=40)) == ["p003"]


def test_filter_places_can_answer_is_it_open():
    # 空列表本身就是答案：这个时段不营业。
    assert ids(filter_places(["p003"], open_from=1260, open_to=1290)) == []
    assert ids(filter_places(["p003"], open_from=1200, open_to=1260)) == ["p003"]


def test_filter_places_rejects_unknown_id():
    observation = dispatch(
        ActionRequest(tool="filter_places", arguments={"place_ids": ["p001", "p999"]})
    )
    assert observation.ok is False
    assert observation.error.kind == "not_found"


def test_filter_places_dedupes():
    assert ids(filter_places(["p001", "p001"])) == ["p001"]


# --- rank_places ---------------------------------------------------------


def test_rank_places_by_price():
    assert ids(rank_places(["p001", "p002", "p003"], by="price")) == ["p002", "p003", "p001"]


def test_rank_places_by_score_is_descending():
    assert ids(rank_places(["p002", "p001", "p003"], by="score")) == ["p001", "p003", "p002"]


def test_rank_places_by_noise():
    # low(0) 在前，mid(1)，high(2)
    assert ids(rank_places(["p004", "p003", "p005"], by="noise")) == ["p003", "p005", "p004"]


def test_rank_places_bad_key():
    with pytest.raises(ToolError) as caught:
        rank_places(["p001"], by="vibe")
    assert caught.value.kind == "bad_args"


# --- alternatives --------------------------------------------------------


def test_alternatives_excludes_origin_and_stays_in_kind():
    assert ids(alternatives("p001")) == ["p002"]
    assert ids(alternatives("p003")) == ["p004", "p005"]


def test_alternatives_applies_filters():
    assert ids(alternatives("p003", avoid_tags=["hotpot"])) == ["p005"]


def test_alternatives_unknown_origin():
    observation = dispatch(ActionRequest(tool="alternatives", arguments={"place_id": "p999"}))
    assert observation.ok is False
    assert observation.error.kind == "not_found"


# --- nearby_places -------------------------------------------------------


def test_nearby_places_sorts_by_travel_time():
    # p001 出发：p003 8、p004 13、p005 15、p002 30
    assert ids(nearby_places("p001", max_travel_min=15)) == ["p003", "p004", "p005"]


def test_nearby_places_can_filter_by_kind():
    assert ids(nearby_places("p001", kind="board_game", max_travel_min=30)) == ["p002"]


def test_nearby_places_unknown_origin():
    observation = dispatch(
        ActionRequest(tool="nearby_places", arguments={"from_place_id": "p999"})
    )
    assert observation.ok is False
    assert observation.error.kind == "not_found"


def test_travel_fixture_covers_every_pair():
    places = load_places()
    table = load_travel()
    for i, here in enumerate(places):
        for there in places[i + 1:]:
            assert (here.place_id, there.place_id) in table


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


# --- 形状契约：初版主循环只吞 list[Candidate] ----------------------------


@pytest.mark.parametrize("tool", sorted(CALLS))
def test_every_tool_returns_a_candidate_list(tool):
    observation = dispatch(ActionRequest(tool=tool, arguments=CALLS[tool]))
    assert observation.ok is True, observation.error
    assert isinstance(observation.data, list)
    assert all(isinstance(place, Candidate) for place in observation.data)


@pytest.mark.parametrize("tool", sorted(CALLS))
def test_loop_survives_every_tool(tool):
    """不改 models/orchestrator 的前提：任何工具的结果都要能进 found 并走完校验。"""
    steps: list[int] = []

    def fake_plan(state: GroupState) -> PlannerDecision:
        steps.append(1)
        if len(steps) == 1:
            return PlannerDecision(
                kind="act",
                action=ActionRequest(tool=tool, arguments=CALLS[tool]),
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
    assert len(steps) == 2
