"""纪要层的测试，不联网。"""

import json
import sys

import pytest

from group_agent import llm
from group_agent.minutes import Minutes, build_facts, hhmm, write_minutes
from group_agent.models import ChatLog, FinalPlan, GroupState, Slot


def make_chat() -> ChatLog:
    return ChatLog.model_validate(
        {
            "chat_id": "t1",
            "messages": [
                {"i": 1, "who": "小明", "min": 0, "text": "周六下午聚一下"},
                {"i": 2, "who": "小王", "min": 1, "text": "我三点前不行"},
            ],
        }
    )


def make_plan() -> FinalPlan:
    return FinalPlan(
        status="ok",
        date="2026-11-07",
        participants=["小明"],
        slots=[
            Slot(
                start_min=900,
                end_min=1050,
                activity="board_game",
                place_id="p001",
                place_name="骰子桌游",
                attrs={"cost_per_person": 45},
            )
        ],
        total_cost=45,
        message="周六 15:00 去骰子桌游",
    )


GOOD = {
    "participants": ["小明", "小王"],
    "decisions": ["周六下午聚一次"],
    "open_questions": ["晚饭吃什么？"],
    "todos": [
        {"who": "小明", "what": "订桌游店", "due": "周六 15:00", "evidence": "小明：周六下午聚一下"},
    ],
}


def fake_complete(data):
    def _complete(system, user, schema, **kwargs):
        return llm.LLMResult(data=data, tokens=10, ms=1)

    return _complete


def test_hhmm():
    assert hhmm(900) == "15:00"
    assert hhmm(0) == "00:00"
    assert hhmm(65) == "01:05"


def test_build_facts_matches_the_plan():
    facts = build_facts(make_chat(), make_plan())
    slot = facts["plan"]["slots"][0]
    assert slot["start"] == "15:00"
    assert slot["activity"] == "board_game"
    assert slot["cost_per_person"] == 45
    assert facts["plan"]["message"] == "周六 15:00 去骰子桌游"
    assert len(facts["chat"]) == 2
    # 不传状态就不该有约束和未决项
    assert "constraints" not in facts


def test_build_facts_includes_state():
    state = GroupState(participants=["小明"], unknowns=["晚饭在哪吃"])
    facts = build_facts(make_chat(), make_plan(), state)
    assert facts["unknowns"] == ["晚饭在哪吃"]
    assert facts["constraints"] == []


def test_parses_model_output():
    minutes = write_minutes(make_chat(), make_plan(), complete=fake_complete(GOOD))
    assert isinstance(minutes, Minutes)
    assert minutes.participants == ["小明", "小王"]
    assert minutes.todos[0].what == "订桌游店"
    assert minutes.open_questions == ["晚饭吃什么？"]


def test_schema_and_facts_are_sent():
    seen = {}

    def capture(system, user, schema, **kwargs):
        seen["schema"] = schema
        seen["user"] = user
        return llm.LLMResult(data=GOOD, tokens=1, ms=1)

    write_minutes(make_chat(), make_plan(), complete=capture)
    assert "todos" in json.dumps(seen["schema"])
    assert "骰子桌游" in seen["user"]
    assert "周六下午聚一下" in seen["user"]


def test_retries_then_raises_on_bad_shape():
    calls = []

    def bad(system, user, schema, **kwargs):
        calls.append(1)
        return llm.LLMResult(data={"participants": "不是列表"}, tokens=1, ms=1)

    with pytest.raises(RuntimeError):
        write_minutes(make_chat(), make_plan(), complete=bad)
    assert len(calls) == 2


def test_cli_outputs_plan_and_minutes(tmp_path, monkeypatch, capsys):
    chat_file = tmp_path / "chat.json"
    chat_file.write_text(
        json.dumps({"chat_id": "t1", "messages": [{"i": 1, "who": "小明", "min": 0, "text": "周六聚"}]}),
        encoding="utf-8",
    )

    from group_agent import __main__ as cli

    monkeypatch.setattr(cli, "understand", lambda chat: GroupState(participants=["小明"]))
    monkeypatch.setattr(cli, "run", lambda chat, understand_fn=None: make_plan())
    monkeypatch.setattr(
        cli, "write_minutes", lambda chat, plan, state=None: Minutes.model_validate(GOOD)
    )
    monkeypatch.setattr(sys, "argv", ["group_agent", str(chat_file)])

    cli.main()
    payload = json.loads(capsys.readouterr().out)
    assert payload["plan"]["status"] == "ok"
    assert payload["minutes"]["todos"][0]["what"] == "订桌游店"


def test_cli_still_prints_plan_when_minutes_fail(tmp_path, monkeypatch, capsys):
    chat_file = tmp_path / "chat.json"
    chat_file.write_text(
        json.dumps({"chat_id": "t1", "messages": [{"i": 1, "who": "小明", "min": 0, "text": "周六聚"}]}),
        encoding="utf-8",
    )

    from group_agent import __main__ as cli

    def boom(chat, plan, state=None):
        raise RuntimeError("模型挂了")

    monkeypatch.setattr(cli, "understand", lambda chat: GroupState(participants=["小明"]))
    monkeypatch.setattr(cli, "run", lambda chat, understand_fn=None: make_plan())
    monkeypatch.setattr(cli, "write_minutes", boom)
    monkeypatch.setattr(sys, "argv", ["group_agent", str(chat_file)])

    cli.main()
    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert payload["plan"]["status"] == "ok"
    assert payload["minutes"] is None
    assert "纪要生成失败" in captured.err
