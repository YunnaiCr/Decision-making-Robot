"""外部数据源的测试：全部用假 transport，不联网。"""

import pytest

from group_agent.models import ActionRequest
from group_agent.sources import SourceError, amap_places, amap_weather
from group_agent.tools import dispatch

WEATHER = {
    "status": "1",
    "info": "OK",
    "lives": [
        {
            "city": "北京市",
            "weather": "晴",
            "temperature": "12",
            "winddirection": "北",
            "windpower": "≤3",
            "humidity": "40",
            "reporttime": "2026-11-13 10:00:00",
        }
    ],
}

PLACES = {
    "status": "1",
    "info": "OK",
    "pois": [
        {
            "id": "B0FFH123456",
            "name": "星光影城",
            "type": "购物服务;电影院",
            "address": "中山路 1 号",
            "tel": "010-1234",
            "biz_ext": {"cost": "55.0"},
        },
        {
            "id": "B0FFH999999",
            "name": "无价影院",
            "type": "购物服务;电影院",
            "address": "",
            "biz_ext": {},
        },
    ],
}


def test_weather_parses_and_sends_key(monkeypatch):
    monkeypatch.setenv("AMAP_KEY", "test-key")
    seen = {}

    def transport(url):
        seen["url"] = url
        return WEATHER

    data = amap_weather("北京", transport=transport)
    assert data["weather"] == "晴"
    assert data["temperature"] == "12"
    assert data["wind"] == "北≤3"
    assert "key=test-key" in seen["url"]


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


def test_places_are_mapped_to_candidates(monkeypatch):
    monkeypatch.setenv("AMAP_KEY", "k")
    found = amap_places("电影院", "北京", transport=lambda url: PLACES)

    assert [option.place_id for option in found] == ["amap-b0ffh123456", "amap-b0ffh999999"]
    assert found[0].name == "星光影城"
    assert found[0].category == "购物服务"
    assert found[0].price_per_person == 55
    # 高德没给价格就不猜
    assert found[1].price_per_person is None
    # 高德也不给营业时间
    assert found[0].open_min is None


def test_empty_pois_is_not_found(monkeypatch):
    monkeypatch.setenv("AMAP_KEY", "k")
    with pytest.raises(SourceError) as caught:
        amap_places("不存在的地方", "北京", transport=lambda url: {"status": "1", "pois": []})
    assert caught.value.kind == "not_found"


def test_weather_tool_reports_unavailable(monkeypatch):
    """没配 key 时，工具应该明确说“不可用”，而不是悄悄返回假数据。"""
    monkeypatch.delenv("AMAP_KEY", raising=False)
    observation = dispatch(ActionRequest(tool="weather", arguments={"city": "北京"}))
    assert observation.ok is False
    assert observation.error.kind == "unavailable"
    assert "AMAP_KEY" in observation.error.message
