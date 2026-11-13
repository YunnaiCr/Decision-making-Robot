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
    catalog,
    check_open,
    describe_tools,
    dispatch,
    estimate_cost,
    filter_options,
    get_option,
    load_places,
    load_travel,
    nearby_options,
    rank_options,
    search_options,
    travel_time,
)
from group_agent.verifier import PRICE_KEY


def ids(options: list[Candidate]) -> list[str]:
    return [option.place_id for option in options]


# 每个工具一份能跑通的参数，下面几个“全量”测试都用它。
CALLS: dict[str, dict] = {
    "catalog": {},
    "search_options": {"category": "board_game"},
    "get_option": {"option_id": "p001"},
    "filter_options": {"option_ids": ["p001", "p002"], "max_price": 40},
    "rank_options": {"option_ids": ["p001", "p003"], "by": "price"},
    "alternatives": {"option_id": "p001"},
    "nearby_options": {"from_option_id": "p001", "max_travel_min": 10},
    "check_open": {"option_id": "p001", "start_min": 900, "end_min": 1000},
    "travel_time": {"from_option_id": "p001", "to_option_id": "p003"},
    "estimate_cost": {"option_ids": ["p001", "p003"], "headcount": 3},
}


# --- catalog：先看看世界有什么 --------------------------------------------


def test_catalog_lists_categories_tags_and_attrs():
    data = catalog()
    assert data["categories"] == {"board_game": 2, "restaurant": 3, "movie": 1, "park": 1}
    assert "movie" in data["tags"]
    assert "outdoor" in data["tags"]
    assert data["attrs"]["noise"] == ["high", "low", "mid"]
    assert data["attrs"]["private_room"] == ["false", "true"]
    assert data["attrs"]["screen"] == ["IMAX"]


# --- search_options ------------------------------------------------------


def test_search_without_category_looks_at_everything():
    assert len(search_options()) == 7


def test_search_by_open_category():
    assert ids(search_options("board_game")) == ["p001", "p002"]
    assert ids(search_options("movie")) == ["p006"]
    assert ids(search_options("park")) == ["p007"]
    # 品类是自由字符串，不认识的返回空表而不是报错
    assert ids(search_options("coding")) == []


def test_search_by_positive_and_avoid_tags():
    assert ids(search_options(tags=["hotpot"])) == ["p004"]
    assert ids(search_options(avoid_tags=["hotpot"])) == ["p001", "p002", "p003", "p005", "p006", "p007"]
    assert ids(search_options(tags=["noodle", "chinese"])) == ["p003", "p005"]


def test_search_by_attrs():
    assert ids(search_options(attrs={"noise": "low"})) == ["p001", "p003"]
    assert ids(search_options(attrs={"private_room": True})) == ["p001", "p004", "p005"]
    assert ids(search_options(attrs={"free": True})) == ["p007"]
    # 没有这个属性键的候选不算命中
    assert ids(search_options(attrs={"unknown_key": "x"})) == []


def test_price_limit_excludes_unknown_price():
    # p007 没有价格，价格上限下不能算它便宜
    assert ids(search_options(max_price=45)) == ["p001", "p002", "p003"]


def test_open_window_must_be_covered_whole():
    # 阿婆面馆 21:00 关门，排到 21:30 就不行；别的都能覆盖这一段
    assert ids(search_options(open_from=1260, open_to=1290)) == [
        "p001", "p002", "p004", "p005", "p006", "p007"
    ]


def test_min_score():
    assert ids(search_options(min_score=4.5)) == ["p001", "p004", "p005", "p006"]


def test_exclude_ids():
    assert ids(search_options("board_game", exclude_ids=["p001"])) == ["p002"]


def test_empty_result_is_not_an_error():
    observation = dispatch(ActionRequest(tool="search_options", arguments={"category": "coding"}))
    assert observation.ok is True
    assert observation.data == []


@pytest.mark.parametrize(
    "arguments",
    [
        {"max_price": -1},
        {"max_distance": -1},
        {"min_score": 9},
        {"open_to": 900},
        {"open_from": 900, "open_to": 600},
    ],
)
def test_bad_search_arguments(arguments):
    with pytest.raises(ToolError) as caught:
        search_options(**arguments)
    assert caught.value.kind == "bad_args"


# --- get_option / filter_options -----------------------------------------


def test_get_option_returns_zero_or_one():
    hit = get_option("p006")
    assert ids(hit) == ["p006"]
    assert hit[0].name == "星光影城"
    assert get_option("p999") == []


def test_filter_options_keeps_order_and_dedupes():
    assert ids(filter_options(["p003", "p001"], max_price=50)) == ["p003", "p001"]
    assert ids(filter_options(["p003", "p001"], max_price=40)) == ["p003"]
    assert ids(filter_options(["p001", "p001"])) == ["p001"]


def test_filter_options_by_attrs():
    assert ids(filter_options(["p001", "p004"], attrs={"noise": "low"})) == ["p001"]


def test_filter_options_rejects_unknown_id():
    observation = dispatch(
        ActionRequest(tool="filter_options", arguments={"option_ids": ["p001", "p999"]})
    )
    assert observation.ok is False
    assert observation.error.kind == "not_found"


# --- rank_options --------------------------------------------------------


def test_rank_options():
    assert ids(rank_options(["p001", "p002", "p003"], by="price")) == ["p002", "p003", "p001"]
    assert ids(rank_options(["p002", "p001", "p003"], by="score")) == ["p001", "p003", "p002"]
    assert ids(rank_options(["p007", "p001", "p003"], by="distance")) == ["p003", "p001", "p007"]
    # 缺价格的排最后
    assert ids(rank_options(["p007", "p001"], by="price")) == ["p001", "p007"]


def test_rank_options_bad_key():
    with pytest.raises(ToolError) as caught:
        rank_options(["p001"], by="vibe")
    assert caught.value.kind == "bad_args"


# --- alternatives / nearby_options ---------------------------------------


def test_alternatives_stays_in_category():
    assert ids(alternatives("p001")) == ["p002"]
    assert ids(alternatives("p003")) == ["p004", "p005"]
    # 电影只有一家，换不出别的
    assert ids(alternatives("p006")) == []


def test_alternatives_unknown_origin():
    observation = dispatch(ActionRequest(tool="alternatives", arguments={"option_id": "p999"}))
    assert observation.ok is False
    assert observation.error.kind == "not_found"


def test_nearby_options_sorts_by_travel_time():
    # p001 出发：p003 8、p004 13、p005 15、p006 18、p007 20、p002 30
    assert ids(nearby_options("p001", max_travel_min=15)) == ["p003", "p004", "p005"]
    assert ids(nearby_options("p001", category="movie", max_travel_min=30)) == ["p006"]


def test_nearby_options_unknown_origin():
    observation = dispatch(
        ActionRequest(tool="nearby_options", arguments={"from_option_id": "p999"})
    )
    assert observation.ok is False
    assert observation.error.kind == "not_found"


def test_travel_fixture_covers_every_pair():
    places = load_places()
    table = load_travel()
    for i, here in enumerate(places):
        for there in places[i + 1:]:
            assert (here.place_id, there.place_id) in table


# --- 事实类工具 -----------------------------------------------------------


def test_check_open_answers_three_states():
    inside = check_open("p003", 1200, 1260)
    assert inside["open"] is True
    assert inside["window"] == [600, 1260]

    outside = check_open("p003", 1260, 1290)
    assert outside["open"] is False
    assert "超出" in outside["reason"]

    # 整天可用的地方永远开着
    assert check_open("p007", 0, 1440)["open"] is True


def test_check_open_errors():
    with pytest.raises(ToolError) as caught:
        check_open("p001", 1000, 900)
    assert caught.value.kind == "bad_args"

    observation = dispatch(
        ActionRequest(tool="check_open", arguments={"option_id": "p999", "start_min": 1, "end_min": 2})
    )
    assert observation.error.kind == "not_found"


def test_travel_time_is_mirrored():
    there = travel_time("p001", "p003")
    back = travel_time("p003", "p001")
    assert there["minutes"] == back["minutes"] == 8
    assert travel_time("p001", "p001")["minutes"] == 0


def test_estimate_cost():
    data = estimate_cost(["p001", "p003"], headcount=3)
    assert data["per_person"] == 83
    assert data["total"] == 249
    assert data["missing"] == []

    # 有未知价就不给总数，把缺的列出来
    missing = estimate_cost(["p001", "p007"], headcount=3)
    assert missing["total"] is None
    assert missing["missing"] == ["p007"]


def test_estimate_cost_rejects_bad_headcount():
    with pytest.raises(ToolError) as caught:
        estimate_cost(["p001"], headcount=0)
    assert caught.value.kind == "bad_args"


# --- dispatch / 注册表 ----------------------------------------------------


def test_unknown_tool_is_bad_args():
    observation = dispatch(ActionRequest(tool="no_such_tool"))
    assert observation.ok is False
    assert observation.error.kind == "bad_args"


def test_wrong_argument_name_is_bad_args():
    observation = dispatch(ActionRequest(tool="search_options", arguments={"type": "board_game"}))
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


# --- 形状契约 + 主循环兼容 ------------------------------------------------


@pytest.mark.parametrize("tool", sorted(CALLS))
def test_every_tool_returns_the_right_shape(tool):
    observation = dispatch(ActionRequest(tool=tool, arguments=CALLS[tool]))
    assert observation.ok is True, observation.error
    if tool in {"check_open", "travel_time", "estimate_cost", "catalog"}:
        assert isinstance(observation.data, dict)
    else:
        assert isinstance(observation.data, list)
        assert all(isinstance(option, Candidate) for option in observation.data)


@pytest.mark.parametrize("tool", sorted(CALLS))
def test_loop_survives_every_tool(tool):
    """候选进 found、事实进 facts，主循环都要能走完。"""
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
