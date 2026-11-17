"""外部数据源的测试：用抓下来的报文或假 transport，不联网。"""

import json
from pathlib import Path

import pytest

from group_agent import sources
from group_agent.models import ActionRequest
from group_agent.sources import SourceError, amap_places, amap_weather
from group_agent.tools import dispatch

REMOTE = Path(__file__).resolve().parent.parent / "fixtures" / "remote"


def load(name: str) -> dict:
    return json.loads((REMOTE / name).read_text(encoding="utf-8"))


PLACES = load("amap_places.json")
WEATHER = load("amap_weather.json")


def test_weather_sends_key_and_asks_for_forecast(monkeypatch):
    monkeypatch.setenv("AMAP_KEY", "test-key")
    seen = {}

    def transport(url):
        seen["url"] = url
        return WEATHER

    fact = amap_weather("北京", transport=transport)
    assert fact["weather"] == "晴"
    assert "key=test-key" in seen["url"]
    # 要预报就得请求 all，只要实时是 base
    assert "extensions=all" in seen["url"]


def test_weather_with_date_uses_forecast(monkeypatch):
    monkeypatch.setenv("AMAP_KEY", "k")
    fact = amap_weather("北京", date="2026-11-17", transport=lambda url: WEATHER)
    assert fact["kind"] == "forecast"
    assert fact["date"] == "2026-11-17"
    assert fact["temp_high"] == 14


def test_missing_key_is_unavailable(monkeypatch):
    monkeypatch.delenv("AMAP_KEY", raising=False)
    with pytest.raises(SourceError) as caught:
        amap_weather("北京")
    assert caught.value.kind == "unavailable"


def test_error_status_is_upstream(monkeypatch):
    monkeypatch.setenv("AMAP_KEY", "k")
    with pytest.raises(SourceError) as caught:
        amap_weather("北京", transport=lambda url: {"status": "0", "info": "INVALID_USER_KEY"})
    assert caught.value.kind == "upstream"


def test_places_are_translated(monkeypatch):
    monkeypatch.setenv("AMAP_KEY", "k")
    options = amap_places("桌游", "北京", transport=lambda url: PLACES)

    assert [option.place_id for option in options] == [
        "amap-b0ffh1x2y3",
        "amap-b0ffh4a5b6",
        "amap-b0ffh7c8d9",
    ]
    assert options[0].category == "board_game"
    assert options[1].price_per_person == 88
    # 没有营业时间的就留 None，不猜
    assert options[1].open_min is None


def test_empty_pois_is_not_found(monkeypatch):
    monkeypatch.setenv("AMAP_KEY", "k")
    with pytest.raises(SourceError) as caught:
        amap_places("不存在的地方", "北京", transport=lambda url: {"status": "1", "pois": []})
    assert caught.value.kind == "not_found"


def test_weather_tool_passes_the_date(monkeypatch):
    calls = {}

    def fake(city, date=None):
        calls["city"] = city
        calls["date"] = date
        return {"weather": "小雨"}

    monkeypatch.setattr(sources, "amap_weather", fake)
    observation = dispatch(
        ActionRequest(tool="weather", arguments={"city": "北京", "date": "2026-11-18"})
    )
    assert observation.ok is True
    assert calls == {"city": "北京", "date": "2026-11-18"}


def test_weather_tool_reports_unavailable(monkeypatch):
    """没配 key 时，工具应该明确说“不可用”，而不是悄悄返回假数据。"""
    monkeypatch.delenv("AMAP_KEY", raising=False)
    observation = dispatch(ActionRequest(tool="weather", arguments={"city": "北京"}))
    assert observation.ok is False
    assert observation.error.kind == "unavailable"
    assert "AMAP_KEY" in observation.error.message
