"""真实报文 -> 内部结构的翻译测试。

用的是抓下来的高德响应（fixtures/remote/），不是手搓的假数据——
这样字段形状一变，测试就会先红，而不是上线才发现。
"""

import json
from pathlib import Path

import pytest

from group_agent import adapters
from group_agent.sources import SourceError

REMOTE = Path(__file__).resolve().parent.parent / "fixtures" / "remote"


def load(name: str) -> dict:
    return json.loads((REMOTE / name).read_text(encoding="utf-8"))


PLACES = load("amap_places.json")
WEATHER = load("amap_weather.json")


def test_text_handles_gaode_empty_arrays():
    # 高德缺值的时候给 []，不是空字符串
    assert adapters.text([]) == ""
    assert adapters.text(None) == ""
    assert adapters.text("  中山路 1 号 ") == "中山路 1 号"
    assert adapters.text(["a", "b"]) == "a;b"


@pytest.mark.parametrize(
    "type_text,expected",
    [
        ("餐饮服务;中餐厅;火锅店", "restaurant"),
        ("购物服务;电影院", "movie"),
        ("体育休闲服务;娱乐场所;桌游", "board_game"),
        ("风景名胜;公园", "park"),
        ("公司企业;写字楼", "other"),
        ("", "other"),
    ],
)
def test_category_mapping(type_text, expected):
    assert adapters.category_of(type_text) == expected


def test_tag_mapping_only_adds_known_tags():
    assert adapters.tags_of("餐饮服务;中餐厅;火锅店") == ["hotpot", "chinese"]
    assert adapters.tags_of("购物服务;电影院") == ["movie"]
    assert adapters.tags_of("公司企业;写字楼") == []


def test_price_parsing():
    assert adapters.price_of({"biz_ext": {"cost": "45.00"}}) == 45
    assert adapters.price_of({"biz_ext": {"cost": "88"}}) == 88
    # 空数组、空串、非数字都当“没有价格”，不四舍五入成 0
    assert adapters.price_of({"biz_ext": {"cost": []}}) is None
    assert adapters.price_of({"biz_ext": {"cost": ""}}) is None
    assert adapters.price_of({"biz_ext": {"cost": "免费"}}) is None
    assert adapters.price_of({}) is None


def test_hours_parsing():
    assert adapters.hours_of({"biz_ext": {"open_time": "10:00-23:00"}}) == (600, 1380)
    assert adapters.hours_of({"biz_ext": {"open_time": "09:30-24:00"}}) == (570, 1440)
    # 跨天表示不了，宁可没有
    assert adapters.hours_of({"biz_ext": {"open_time": "22:00-02:00"}}) is None
    assert adapters.hours_of({"biz_ext": {}}) is None


def test_option_id_is_safe_and_short():
    assert adapters.option_id("B0FFH1X2Y3") == "amap-b0ffh1x2y3"
    assert adapters.option_id("A/B C!") == "amap-abc"
    assert adapters.option_id("") == ""
    assert len(adapters.option_id("X" * 80)) <= 32


def test_poi_mapping_from_captured_payload():
    board = adapters.amap_poi(PLACES["pois"][0])
    assert board is not None
    assert board.place_id == "amap-b0ffh1x2y3"
    assert board.name == "骰子桌游（中关村店）"
    assert board.category == "board_game"
    assert board.price_per_person == 45
    assert (board.open_min, board.close_min) == (600, 1380)
    assert board.attrs["source"] == "amap"
    assert board.attrs["address"] == "中关村大街 27 号"

    hotpot = adapters.amap_poi(PLACES["pois"][1])
    assert hotpot.category == "restaurant"
    assert "hotpot" in hotpot.tags
    # 地址是 []，就不该塞一个空字符串进去
    assert "address" not in hotpot.attrs

    cinema = adapters.amap_poi(PLACES["pois"][2])
    assert cinema.category == "movie"
    assert cinema.price_per_person is None
    assert (cinema.open_min, cinema.close_min) == (570, 1440)


def test_poi_without_name_is_skipped():
    assert adapters.amap_poi({"id": "B0FFH", "name": []}) is None
    assert adapters.amap_poi({"name": "有名字没编号"}) is None


def test_weather_live_when_no_date():
    fact = adapters.amap_weather_fact(WEATHER)
    assert fact["kind"] == "live"
    assert fact["weather"] == "晴"
    assert fact["temperature"] == 12  # 字符串转成数字
    assert fact["humidity"] == 40


def test_weather_forecast_by_date():
    fact = adapters.amap_weather_fact(WEATHER, "2026-11-18")
    assert fact["kind"] == "forecast"
    assert fact["weather"] == "小雨"
    assert fact["temp_high"] == 9
    assert fact["temp_low"] == 2
    assert fact["city"] == "北京市"


def test_weather_out_of_range_says_not_found():
    """预报只覆盖三天，问下周就必须说查不到，不能拿今天的糊弄。"""
    with pytest.raises(SourceError) as caught:
        adapters.amap_weather_fact(WEATHER, "2026-12-01")
    assert caught.value.kind == "not_found"
    assert "2026-12-01" in caught.value.message
