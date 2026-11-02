"""llm.py 的逻辑测试，不联网。"""

import json

import pytest

from group_agent import llm


@pytest.fixture(autouse=True)
def _isolate_env(monkeypatch):
    """测试不依赖本机 .env。"""
    for name in ("LLM_EXTRA_JSON", "LLM_MAX_TOKENS", "LLM_TEMPERATURE"):
        monkeypatch.delenv(name, raising=False)



def fake_ok(url, payload, key):
    return {
        "choices": [{"message": {"content": json.dumps({"ok": 1})}}],
        "usage": {"total_tokens": 42},
    }


def test_returns_parsed_dict(monkeypatch):
    monkeypatch.setenv("LLM_API_KEY", "test-key")
    result = llm.complete_json("系统", "用户", {"type": "object"}, transport=fake_ok)
    assert result.data == {"ok": 1}
    assert result.tokens == 42


def test_missing_key_raises(monkeypatch):
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    with pytest.raises(llm.LLMError):
        llm.complete_json("s", "u", {}, transport=fake_ok)


def test_retries_then_gives_up(monkeypatch):
    monkeypatch.setenv("LLM_API_KEY", "test-key")
    calls = []

    def not_json(url, payload, key):
        calls.append(1)
        return {"choices": [{"message": {"content": "这不是 json"}}]}

    with pytest.raises(llm.LLMError):
        llm.complete_json("s", "u", {}, transport=not_json)
    assert len(calls) == 2


def test_schema_is_sent_to_the_model(monkeypatch):
    monkeypatch.setenv("LLM_API_KEY", "test-key")
    seen = {}

    def capture(url, payload, key):
        seen["payload"] = payload
        return {"choices": [{"message": {"content": "{}"}}]}

    llm.complete_json("s", "u", {"required": ["a"]}, transport=capture)
    prompt = seen["payload"]["messages"][1]["content"]
    assert "required" in prompt
    assert "temperature" not in seen["payload"]

def test_temperature_only_when_set(monkeypatch):
    monkeypatch.setenv("LLM_API_KEY", "test-key")
    monkeypatch.setenv("LLM_TEMPERATURE", "0.2")
    seen = {}

    def capture(url, payload, key):
        seen["payload"] = payload
        return {"choices": [{"message": {"content": "{}"}}]}

    llm.complete_json("s", "u", {}, transport=capture)
    assert seen["payload"]["temperature"] == 0.2

