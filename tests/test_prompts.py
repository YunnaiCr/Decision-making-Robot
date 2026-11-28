"""语言示例的离线契约回归；不把这些检查当作真实模型的行为评测。"""

import copy
import json
import re

import pytest

from group_agent import llm, orchestrator, tools
from group_agent.minutes import Minutes, build_facts
from group_agent.models import (
    ActionRequest, ChatLog, FinalDraft, FinalPlan, GroupState, MessageOut,
    PlannerDecision, Slot, StateDelta,
)
from group_agent.prompts import express, minutes as minutes_prompt, planning, understanding, vocab
from group_agent.verifier import PRICE_KEY, verify


def _chat(example):
    messages = []
    for index, line in enumerate(example["chat"], 1):
        head, text = line.split("：", 1)
        messages.append({"i": index, "who": head.split(" ", 1)[1], "min": index, "text": text})
    return ChatLog(chat_id="synthetic", messages=messages)


def _scenario(case):
    return next(example for example in planning.EXAMPLES if example["case"] == case)


@pytest.mark.parametrize("example", understanding.EXAMPLES, ids=lambda example: example["note"])
def test_understanding_examples_have_grounded_constraints(example):
    chat = _chat(example)
    delta = StateDelta.model_validate(example["delta"])
    assert delta.date is None or re.fullmatch(r"\d{4}-\d{2}-\d{2}", delta.date)
    assert len(delta.participants) == len(set(delta.participants))
    assert set(delta.participants) <= {message.who for message in chat.messages}
    for constraint in delta.add_constraints:
        assert constraint.id == ""
        assert set(constraint.who) <= set(delta.participants)
        assert any(constraint.evidence in message.text for message in chat.messages)
    assert delta.drop_constraints == []  # these examples contain no historical ids


def test_withdrawn_person_leaves_no_personal_requirements():
    delta = StateDelta.model_validate(understanding.EXAMPLES[1]["delta"])
    assert delta.participants == ["小可"]
    assert not any("阿黄" in item or "包间" in item for item in delta.unknowns)
    assert all("阿黄" not in constraint.who for constraint in delta.add_constraints)


def test_new_activity_and_numeric_preference_are_preserved():
    delta = StateDelta.model_validate(understanding.EXAMPLES[2]["delta"])
    activity, budget = delta.add_constraints
    assert activity.want == ["pottery"]
    assert activity.severity == budget.severity == "soft"
    assert budget.per_person_max == 100
    assert any("硬要求" in item and "必须有包间" in item for item in delta.unknowns)
    assert all("10" not in item for item in delta.unknowns)  # no invented distance threshold


def test_activity_alternatives_are_not_conjunctive_constraints():
    delta = StateDelta.model_validate(understanding.EXAMPLES[3]["delta"])
    assert delta.add_constraints == []
    assert any("二选一" in item for item in delta.unknowns)


def test_allergy_label_preserves_the_food_not_a_similar_food():
    delta = StateDelta.model_validate(understanding.EXAMPLES[0]["delta"])
    diet = next(constraint for constraint in delta.add_constraints if constraint.kind == "diet")
    assert diet.avoid == ["seafood"]
    assert diet.severity == "hard"
    assert vocab.DIET_ALIASES["花生"] != vocab.DIET_ALIASES["海鲜"]


@pytest.mark.parametrize("example", planning.EXAMPLES, ids=lambda example: example["case"])
def test_planning_examples_follow_runtime_schema(example):
    state = GroupState.model_validate(example["state"])
    decision = PlannerDecision.model_validate(example["decision"])
    if decision.kind == "act":
        assert decision.draft is None and decision.questions == []
        assert decision.action.why
    elif decision.kind == "final":
        assert decision.action is None and decision.questions == []
        assert decision.draft.participants == state.participants
        assert decision.draft.date == state.date
    else:
        assert decision.action is None and decision.draft is None
    assert decision.reason


def test_examples_cover_four_exits():
    assert {example["decision"]["kind"] for example in planning.EXAMPLES} == {
        "act", "final", "ask", "give_up"
    }


@pytest.mark.parametrize("example", [example for example in planning.EXAMPLES
                                   if example["decision"]["kind"] == "act"],
                         ids=lambda example: example["case"])
def test_example_tool_requests_run_offline(example, monkeypatch):
    # External examples use injected fake sources, never credentials or a live network.
    from group_agent.models import Candidate

    monkeypatch.setattr(tools.sources, "amap_places", lambda keyword, city, limit=5: [
        Candidate(place_id="amap-synthetic", name="虚构陶艺地点", category="生活服务",
                  attrs={"source": "amap"})])
    monkeypatch.setattr(tools.sources, "amap_weather", lambda city, date=None: {"city": city, "weather": "晴"})
    action = ActionRequest.model_validate(example["decision"]["action"])
    observation = tools.dispatch(action)
    assert observation.ok, observation.error
    assert observation.data


@pytest.mark.parametrize("example", planning.EXAMPLES, ids=lambda example: example["case"])
def test_planning_example_data_matches_local_tool_evidence(example):
    state = GroupState.model_validate(example["state"])
    actual_options = tools.places_by_id()
    for group in state.found.values():
        for option in group:
            assert option == actual_options[option.place_id]
    for key, expected in state.facts.items():
        tool, arguments = key.split(" ", 1)
        observation = tools.dispatch(ActionRequest(tool=tool, arguments=json.loads(arguments)))
        assert observation.ok, observation.error
        assert observation.data == expected


def test_final_example_is_grounded_and_has_valid_schedule():
    example = _scenario("final")
    state = GroupState.model_validate(example["state"])
    draft = FinalDraft.model_validate(example["decision"]["draft"])
    options = orchestrator.candidates_by_id(state)
    result = verify(state.constraints, draft, options)
    assert result.passed and result.undecided == []
    for index, slot in enumerate(draft.slots):
        option = options[slot.place_id]
        assert slot.place_name == option.name
        assert slot.attrs == {PRICE_KEY: option.price_per_person}
        assert option.open_min <= slot.start_min < slot.end_min <= option.close_min
        if "max_people" in option.attrs:
            assert len(draft.participants) <= option.attrs["max_people"]
        if index:
            previous = draft.slots[index - 1]
            travel = tools.travel_time(previous.place_id, slot.place_id)["minutes"]
            assert slot.start_min - previous.end_min >= travel
    facts = orchestrator.plan_facts(state, draft)
    assert facts["per_person"] == 83 and facts["total_cost"] == 249


def test_give_up_example_has_a_provable_time_conflict():
    state = GroupState.model_validate(_scenario("give_up")["state"])
    windows = [constraint for constraint in state.constraints if constraint.kind == "availability"]
    assert all(constraint.severity == "hard" for constraint in windows)
    assert max(constraint.free_start_min for constraint in windows) >= min(
        constraint.free_end_min for constraint in windows)
    assert {who for constraint in windows for who in constraint.who} == set(state.participants)


def test_ask_example_needs_headcount_for_total_budget():
    state = GroupState.model_validate(_scenario("ask")["state"])
    decision = PlannerDecision.model_validate(_scenario("ask")["decision"])
    assert state.participants == []
    assert state.constraints[0].total_max == 300
    assert len(decision.questions) == 1


def test_new_category_and_boolean_attribute_need_no_fixture_changes(monkeypatch):
    # Synthetic data lives only in this test, never in shared fixtures or tool code.
    from group_agent.models import Candidate

    option = Candidate(place_id="synthetic-pottery", name="虚构陶艺候选", category="pottery_studio",
                       attrs={"private_room": True, "wheel_type": "electric"})
    monkeypatch.setattr(tools, "_places", [option])
    assert tools.search_options(category="pottery_studio", attrs={"private_room": True}) == [option]
    assert tools.search_options(category="pottery_studio", attrs={"private_room": "true"}) == []
    assert tools.search_options(attrs={"wheel_type": "electric"}) == [option]


def test_runtime_plan_receives_new_rules_and_real_state(monkeypatch):
    example = _scenario("attributes")
    state = GroupState.model_validate(example["state"])
    before = state.model_dump()
    seen = {}

    def capture(system, user, schema):
        seen.update(system=system, user=user, schema=schema)
        return llm.LLMResult(data=copy.deepcopy(example["decision"]), tokens=0, ms=0)

    monkeypatch.setattr(llm, "complete_json", capture)
    decision = orchestrator.plan(state)
    assert decision.action.arguments["attrs"]["private_room"] is True
    assert planning.RULES in seen["user"]
    assert orchestrator.format_state(state) in seen["user"]
    assert seen["schema"] == PlannerDecision.model_json_schema()
    assert state.model_dump() == before


@pytest.mark.parametrize("example", minutes_prompt.EXAMPLES, ids=lambda example: example["note"])
def test_minutes_examples_match_runtime_input_shape_and_task_evidence(example):
    facts = example["facts"]
    minutes = Minutes.model_validate(example["minutes"])
    chat = ChatLog(chat_id="synthetic", messages=[{**message, "min": index}
                   for index, message in enumerate(facts["chat"])])
    slots = [Slot(start_min=int(slot["start"][:2]) * 60 + int(slot["start"][3:]),
                  end_min=int(slot["end"][:2]) * 60 + int(slot["end"][3:]),
                  activity=slot["activity"], place_name=slot["place"],
                  attrs={PRICE_KEY: slot["cost_per_person"]}) for slot in facts["plan"]["slots"]]
    plan = FinalPlan(**{**facts["plan"], "slots": slots})
    state = GroupState(constraints=facts["constraints"], unknowns=facts["unknowns"])
    assert build_facts(chat, plan, state) == facts
    for todo in minutes.todos:
        matching = [message for message in chat.messages if todo.evidence in message.text]
        assert matching, todo.evidence
        if todo.who:
            assert any(message.who == todo.who for message in matching)
        assert todo.due is None  # neither example explicitly assigns a deadline
    assert all(question.endswith("？") for question in minutes.open_questions)


def test_minutes_does_not_turn_plan_into_booking_commitment():
    minutes = Minutes.model_validate(minutes_prompt.EXAMPLES[0]["minutes"])
    assert minutes.todos[0].who == "小王"
    assert minutes.todos[1].who == ""
    assert all(todo.who != "小明" for todo in minutes.todos)
    pending = Minutes.model_validate(minutes_prompt.EXAMPLES[1]["minutes"])
    assert pending.decisions == [] and pending.todos == [] and pending.open_questions


@pytest.mark.parametrize("example", express.EXAMPLES)
def test_expression_examples_match_runtime_facts_and_pass_existing_lint(example):
    facts = example["facts"]
    draft = FinalDraft(participants=facts["participants"], slots=[
        Slot(start_min=int(slot["start"][:2]) * 60 + int(slot["start"][3:]),
             end_min=int(slot["end"][:2]) * 60 + int(slot["end"][3:]), activity=slot["activity"],
             place_name=slot["place"], attrs={PRICE_KEY: slot["cost_per_person"]})
        for slot in facts["slots"]])
    generated = orchestrator.plan_facts(GroupState(date=facts["date"]), draft)
    assert {**generated, "unverified": facts["unverified"]} == facts
    MessageOut(message=example["message"])
    assert express.lint_message(facts, example["message"]) == []
    if facts["date"] is None:
        assert "日期还没定" in example["message"]
    for detail in facts["unverified"]:
        assert detail in example["message"]


def test_lint_catches_invented_number():
    assert express.lint_message({"participants": ["a", "b"], "total_cost": 249},
                                "人均 120，一共 249") == ["数字 120 不在给定事实里"]


def test_lint_catches_empty_value():
    assert any("None" in problem for problem in express.lint_message({"date": None}, "定在 None 那天"))


def test_lint_allows_colloquial_time():
    facts = {"participants": ["a"], "slots": [{"start": "15:00", "end": "17:00"}]}
    assert express.lint_message(facts, "下午三点开始，五点结束") == []


def test_lint_counts_headcount_as_known():
    assert express.lint_message({"participants": ["a", "b", "c"]}, "3 个人一起去") == []


def test_lint_catches_newline_and_too_many_sentences():
    assert "不要换行" in express.lint_message({}, "第一句。\n第二句。")
    assert any("超过" in problem for problem in express.lint_message({}, "一。二。三。四。五。"))


def test_lint_allows_four_sentences():
    assert express.lint_message({}, "一。二。三。还有一条没核实。") == []
    assert express.MAX_SENTENCES == 4
