"""语言层的离线测试：提示词自洽、词表和数据对得上、示例能过校验器。

这里不联网、不需要 key。测的不是「模型答得好不好」，而是
「我们写给模型的规则和例子，本身是不是对的」——这两件事以前一件都没测。
"""

import re

from group_agent.models import (
    ActionRequest,
    Activity,
    Availability,
    Budget,
    ChatLog,
    Diet,
    FinalDraft,
    GroupState,
    MessageOut,
    PlannerDecision,
    Slot,
    StateDelta,
)
from group_agent.orchestrator import plan_facts
from group_agent.prompts import express, planning, understanding, vocab
from group_agent.tools import TOOLS, dispatch, load_places
from group_agent.verifier import PRICE_KEY, verify

PLACES = {place.place_id: place for place in load_places()}


def _minutes(text: str) -> int:
    hour, minute = text.split(":")
    return int(hour) * 60 + int(minute)


def _slot(fact: dict) -> Slot:
    return Slot(
        start_min=_minutes(fact["start"]),
        end_min=_minutes(fact["end"]),
        activity=fact["activity"],
        place_name=fact["place"],
        attrs={PRICE_KEY: fact["cost_per_person"]},
    )


def _chat(ex: dict) -> ChatLog:
    """把示例里的 "01 名字：原话" 还原成 ChatLog，顺便验证示例本身合格式。"""
    messages = []
    for i, line in enumerate(ex["chat"], 1):
        head, text = line.split("：", 1)
        messages.append({"i": i, "who": head.split(" ", 1)[1], "min": i, "text": text})
    return ChatLog.model_validate({"chat_id": "example", "messages": messages})


def test_every_fixture_tag_is_in_the_vocabulary():
    """店的标签必须是词表里的。新加一家店写了新标签，这里会拦下来。"""
    for place in PLACES.values():
        for tag in place.tags:
            assert tag in vocab.PLACE_TAGS, f"{place.place_id} 的标签 {tag} 不在词表里"


def test_every_fixture_kind_is_in_the_vocabulary():
    for place in PLACES.values():
        assert place.kind in vocab.PLACE_KINDS


def test_activity_mapping_only_points_at_real_kinds():
    for activity, kind in vocab.ACTIVITY_PLACE_KIND.items():
        assert activity in vocab.ACTIVITIES
        assert kind is None or kind in vocab.PLACE_KINDS


def test_understanding_examples_are_valid():
    for ex in understanding.EXAMPLES:
        assert _chat(ex).messages  # 示例聊天本身能被 ChatLog 读进来
        StateDelta.model_validate(ex["delta"])


def test_understanding_examples_leave_ids_to_the_program():
    """示例里必须留空 id：模型不该自己起编号（规则 12）。"""
    for ex in understanding.EXAMPLES:
        for constraint in ex["delta"]["add_constraints"]:
            assert constraint["id"] == ""


def test_example_dates_are_iso_or_null():
    """date 只收 YYYY-MM-DD 或 null。「周六」这种相对日期填不了就留 null。"""
    for ex in understanding.EXAMPLES:
        date = ex["delta"]["date"]
        assert date is None or re.fullmatch(r"\d{4}-\d{2}-\d{2}", date), date


def test_understanding_prompt_carries_the_vocabulary():
    """提示词里的词表是从 vocab 拼进去的，不是手写的。"""
    assert "hotpot" in understanding.RULES
    assert "seafood" not in vocab.DIET_TAGS  # 词表里没有，规则要求写进 unknowns
    assert "dinner" in understanding.RULES
    assert "都行" in understanding.RULES


def test_planning_examples_are_valid_decisions():
    for ex in planning.EXAMPLES:
        PlannerDecision.model_validate(ex["decision"])


def test_planning_example_tools_exist_and_run():
    """示例教模型调的工具必须真存在，参数也必须真跑得通。"""
    for ex in planning.EXAMPLES:
        decision = PlannerDecision.model_validate(ex["decision"])
        if decision.kind != "act":
            continue
        assert decision.action.tool in TOOLS
        observation = dispatch(ActionRequest(**decision.action.model_dump()))
        assert observation.ok is True, observation.error
        assert observation.data


def test_planning_example_draft_passes_the_verifier():
    """例子里的方案，拿例子里的约束去跑校验器，必须过得去。"""
    decision = PlannerDecision.model_validate(planning.EXAMPLES[1]["decision"])
    constraints = [
        Budget(id="c1", severity="hard", evidence="一人一百以内", per_person_max=100),
        Activity(id="c2", severity="hard", evidence="要桌游也要吃饭",
                 want=["board_game", "dinner"]),
        Diet(id="c3", severity="hard", evidence="别吃火锅", avoid=["hotpot"]),
        Availability(id="c4", severity="hard", evidence="三点之前不行", who=["小王"],
                     free_start_min=900, free_end_min=1440),
    ]
    result = verify(constraints, decision.draft, PLACES)
    assert result.passed is True, result.violations
    assert result.undecided == []


def test_planning_prompt_maps_activity_to_tool():
    """活动名和工具 kind 是两套词，映射必须写在提示词里。"""
    assert "dinner 用 restaurant 查" in planning.RULES
    assert "board_game 用 board_game 查" in planning.RULES


def test_express_example_is_lint_clean():
    MessageOut(message=express.EXAMPLE["message"])
    assert express.lint_message(express.EXAMPLE["facts"], express.EXAMPLE["message"]) == []


def test_express_example_facts_match_plan_facts():
    """示例事实的字段，必须和 orchestrator 真给模型的那份一模一样。"""
    draft = FinalDraft(
        participants=express.EXAMPLE["facts"]["participants"],
        slots=[_slot(slot) for slot in express.EXAMPLE["facts"]["slots"]],
    )
    facts = plan_facts(GroupState(date=express.EXAMPLE["facts"]["date"]), draft)
    assert set(facts) | {"unverified"} == set(express.EXAMPLE["facts"])


def test_lint_catches_invented_number():
    facts = {"date": None, "participants": ["a", "b"], "total_cost": 249}
    assert express.lint_message(facts, "人均 120，一共 249") == ["数字 120 不在给定事实里"]


def test_lint_catches_empty_value():
    facts = {"date": None, "participants": ["a"]}
    assert any("None" in problem for problem in express.lint_message(facts, "定在 None 那天"))


def test_lint_allows_colloquial_time():
    facts = {"date": None, "participants": ["a"], "slots": [{"start": "15:00", "end": "17:00"}]}
    assert express.lint_message(facts, "下午三点开始，五点结束") == []


def test_lint_counts_headcount_as_known():
    facts = {"date": None, "participants": ["a", "b", "c"]}
    assert express.lint_message(facts, "3 个人一起去") == []


def test_lint_catches_newline_and_too_many_sentences():
    facts = {"date": None, "participants": ["a"]}
    assert "不要换行" in express.lint_message(facts, "第一句。\n第二句。")
    assert any("超过" in p for p in express.lint_message(facts, "一。二。三。四。五。"))


def test_lint_allows_four_sentences():
    """正文三句 + 未核实提醒一句。提示词第 5 条和 MAX_SENTENCES 是同一个数。"""
    facts = {"date": None, "participants": ["a"]}
    assert express.lint_message(facts, "一。二。三。还有一条没核实。") == []
    assert express.MAX_SENTENCES == 4


def test_planning_rules_do_not_call_unknowns_soft():
    """unknowns 不是软约束，这句必须在提示词里，否则模型会假装"照顾到了"。"""
    assert "不是软约束" in planning.RULES
