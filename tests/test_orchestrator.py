"""主循环写回状态的测试：候选要合并、事实要分流、格式化要看得见。"""

from group_agent.models import ActionRequest, Candidate, GroupState
from group_agent.orchestrator import describe_candidate, format_state, remember


def option(place_id: str, name: str, category: str = "board_game", price: int | None = 10) -> Candidate:
    return Candidate(place_id=place_id, name=name, category=category, price_per_person=price)


def test_found_merges_instead_of_overwriting():
    """同一个 key 查两次，第二次不能把第一次的结果冲掉。"""
    state = GroupState()
    action = ActionRequest(tool="search_options", arguments={"category": "board_game"})

    remember(state, action, [option("p1", "A")])
    remember(state, action, [option("p2", "B")])

    assert [item.place_id for item in state.found["board_game"]] == ["p1", "p2"]


def test_found_dedupes_the_same_candidate():
    state = GroupState()
    action = ActionRequest(tool="search_options", arguments={"category": "board_game"})

    remember(state, action, [option("p1", "A")])
    remember(state, action, [option("p1", "A")])

    assert [item.place_id for item in state.found["board_game"]] == ["p1"]


def test_single_candidate_is_wrapped_into_a_list():
    state = GroupState()
    remember(state, ActionRequest(tool="get_option", arguments={"option_id": "p1"}), option("p1", "A"))
    assert [item.place_id for item in state.found["get_option"]] == ["p1"]


def test_facts_go_to_facts_not_found():
    state = GroupState()
    remember(state, ActionRequest(tool="weather", arguments={"city": "北京"}), {"weather": "晴"})
    assert state.facts
    assert state.found == {}


def test_fact_key_keeps_the_arguments_apart():
    state = GroupState()
    remember(state, ActionRequest(tool="weather", arguments={"city": "北京"}), {"weather": "晴"})
    remember(state, ActionRequest(tool="weather", arguments={"city": "上海"}), {"weather": "雨"})
    assert len(state.facts) == 2


def test_format_state_shows_category_and_attrs():
    state = GroupState(participants=["小明"])
    state.found["board_game"] = [
        Candidate(
            place_id="p001",
            name="骰子桌游",
            category="board_game",
            price_per_person=45,
            open_min=600,
            close_min=1380,
            attrs={"noise": "low"},
        )
    ]
    text = format_state(state)
    assert "[board_game]" in text
    assert "attrs=" in text
    assert "noise" in text


def test_format_state_shows_facts():
    state = GroupState()
    state.facts['travel_time {"a": "p001"}'] = {"minutes": 8}
    assert "查到的其他信息" in format_state(state)
    assert "minutes" in format_state(state)


def test_describe_candidate_survives_missing_fields():
    """缺价格/时间/评分的候选也要能打印出来，不能 KeyError。"""
    line = describe_candidate(Candidate(place_id="x1", name="没数据", category="other"))
    assert "x1" in line
    assert "[other]" in line
    assert "None" not in line
