"""把真实世界的数据翻译成模型能读的结构。

这是整条链路上最容易出错的一段：外部 API 给的是人写的分类、可空的字符串、
有时甚至是空数组；模型要的是稳定的 category / tags / attrs 和明确的 None。

三条原则
1. **不猜**：没有的字段就是 None，不填默认值，也不拿今日数据冒充指定日期
2. **可追溯**：原始编号、地址、电话、来源都留在 attrs 里，出问题能回去查
3. **有兜底**：分类映射不上就落到 `other`，标签映射不上就不加，单条数据脏就跳过，
   绝不让一条坏数据把整个查询打挂

高德的几个坑（都是实测形状）：
- 缺字段时常常给空数组 `[]` 而不是空字符串
- `biz_ext.cost` 是字符串，可能是 "55.00"，也可能缺
- `biz_ext.open_time` 有时有（"10:00-22:00"），大多数没有
- `type` 是 "餐饮服务;中餐厅;火锅店" 这种分号串，不是枚举
"""

import re
from typing import Any

from .models import Candidate

# 高德 type 串里的关键词 -> 内部品类。顺序有讲究：先具体后笼统。
_CATEGORY_RULES: tuple[tuple[tuple[str, ...], str], ...] = (
    (("电影院", "影院", "剧场", "剧院"), "movie"),
    (("公园", "景点", "广场", "植物园"), "park"),
    (("桌游", "电玩", "游戏"), "board_game"),
    (("KTV", "ktv", "量贩式"), "ktv"),
    (("餐饮", "餐厅", "美食", "小吃", "咖啡"), "restaurant"),
)

# type 串里的关键词 -> 内部标签。标签要和 verifier 用的词对得上（见 prompts/vocab.py）。
_TAG_RULES: tuple[tuple[tuple[str, ...], str], ...] = (
    (("火锅",), "hotpot"),
    (("川菜", "湘菜", "麻辣", "辣"), "spicy"),
    (("面馆", "面条", "拉面", "面食"), "noodle"),
    (("中餐", "本帮", "家常"), "chinese"),
    (("电影院", "影院"), "movie"),
    (("公园", "景点"), "park"),
    (("桌游",), "board_game"),
    (("KTV", "ktv"), "ktv"),
)

_HOURS = re.compile(r"(\d{1,2}):(\d{2})\s*-\s*(\d{1,2}):(\d{2})")

# 外部文本会进提示词，所以先在边界上收窄：去掉控制字符、压平空白、限长。
_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def text(value: Any) -> str:
    """把高德那种“缺值给 []”的字段压成字符串，并去掉控制字符。"""
    if isinstance(value, str):
        raw = value
    elif isinstance(value, list):
        raw = ";".join(item for item in value if isinstance(item, str))
    else:
        return ""
    return _CONTROL.sub("", raw).strip()


def clean(value: Any, limit: int) -> str:
    """给要进提示词的字段用：压平空白 + 限长，别让一段长文本挤爆上下文。"""
    return " ".join(text(value).split())[:limit].strip()


def category_of(type_text: str) -> str:
    """类型串 -> 内部品类。映射不上就 other，不猜。"""
    for keywords, category in _CATEGORY_RULES:
        if any(keyword in type_text for keyword in keywords):
            return category
    return "other"


def tags_of(type_text: str) -> list[str]:
    """类型串 -> 内部标签。只加认得的，认不得的一个都不加。"""
    found: list[str] = []
    for keywords, tag in _TAG_RULES:
        if any(keyword in type_text for keyword in keywords) and tag not in found:
            found.append(tag)
    return found


def price_of(poi: dict[str, Any]) -> int | None:
    """人均价。解析不了就当没有，不四舍五入到 0。"""
    raw = (poi.get("biz_ext") or {}).get("cost")
    if not isinstance(raw, str) or not raw.strip():
        return None
    try:
        value = float(raw)
    except ValueError:
        return None
    return int(value) if value >= 0 else None


def hours_of(poi: dict[str, Any]) -> tuple[int, int] | None:
    """营业时段。只认 "10:00-22:00"，跨天的（22:00-02:00）表示不了，返回 None。"""
    raw = text((poi.get("biz_ext") or {}).get("open_time"))
    match = _HOURS.search(raw)
    if not match:
        return None

    open_hour, open_minute, close_hour, close_minute = (int(part) for part in match.groups())
    start = open_hour * 60 + open_minute
    end = close_hour * 60 + close_minute
    if not (0 <= start < end <= 1440):
        return None
    return start, end


def option_id(poi_id: str) -> str:
    """高德 id -> 我们的编号。小写、只保留安全字符、限长 27（加前缀正好 32）。"""
    cleaned = re.sub(r"[^a-z0-9]+", "", poi_id.lower())[:27]
    return f"amap-{cleaned}" if cleaned else ""


def amap_poi(poi: dict[str, Any]) -> Candidate | None:
    """一条高德 POI -> Candidate。缺名字或编号就返回 None（跳过这条）。"""
    name = clean(poi.get("name"), 60)
    place_id = option_id(text(poi.get("id")))
    if not name or not place_id:
        return None

    type_text = text(poi.get("type"))
    window = hours_of(poi)

    attrs: dict[str, Any] = {"source": "amap", "source_id": text(poi.get("id"))}
    address = clean(poi.get("address"), 120)
    tel = clean(poi.get("tel"), 40)
    if address:
        attrs["address"] = address
    if tel:
        attrs["tel"] = tel
    location = text(poi.get("location"))
    if location:
        attrs["location"] = location

    return Candidate(
        place_id=place_id,
        name=name,
        category=category_of(type_text),
        tags=tags_of(type_text),
        price_per_person=price_of(poi),
        open_min=window[0] if window else None,
        close_min=window[1] if window else None,
        attrs=attrs,
    )


def _to_int(value: Any) -> int | None:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


def amap_weather_fact(payload: dict[str, Any], date: str | None = None) -> dict[str, Any]:
    """高德天气 -> 统一的事实。

    给了 date 就只认预报里那一天；没给就返回实时天气。
    预报覆盖不到（超过 3 天）就报 not_found，不要拿今天的天气冒充。
    """
    from .sources import SourceError  # 避免循环导入

    if date:
        forecasts = payload.get("forecasts") or []
        casts = (forecasts[0].get("casts") if forecasts else None) or []
        for cast in casts:
            if text(cast.get("date")) != date:
                continue
            return {
                "city": text(forecasts[0].get("city")),
                "date": date,
                "kind": "forecast",
                "weather": text(cast.get("dayweather")),
                "temp_high": _to_int(cast.get("daytemp")),
                "temp_low": _to_int(cast.get("nighttemp")),
                "wind": f"{text(cast.get('daywind'))} {text(cast.get('daypower'))}".strip(),
                "source": "amap",
            }
        covered = [text(cast.get("date")) for cast in casts]
        raise SourceError("not_found", f"预报只覆盖 {covered or '无'}，查不到 {date}")

    lives = payload.get("lives") or []
    if not lives:
        raise SourceError("not_found", "没有实时天气数据")

    live = lives[0]
    return {
        "city": text(live.get("city")),
        "date": None,
        "kind": "live",
        "weather": text(live.get("weather")),
        "temperature": _to_int(live.get("temperature")),
        "wind": f"{text(live.get('winddirection'))} {text(live.get('windpower'))}".strip(),
        "humidity": _to_int(live.get("humidity")),
        "reported_at": text(live.get("reporttime")),
        "source": "amap",
    }
