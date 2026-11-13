"""外部数据源：从别的平台取数据。

现在只接高德（Amap）：天气和 POI（地点）搜索。
都是 HTTP + 一个 key，所以：

- key 从环境变量 `AMAP_KEY` 读，没配就抛 `SourceError("unavailable")`，
  不会静默降级成假数据
- 网络请求可以注入 `transport`，测试离线跑，不碰网
- 这里只负责“取回来并翻译成我们的类型”，不做缓存、不做重试

想接别的平台（和风天气、豆瓣、大众点评…）：照着 `amap_weather` 写一个函数，
解析成同样的 dict / list[Candidate]，再在 `tools.py` 注册一个工具就行。
注意大多数平台没有公开 API，接之前先确认服务条款。
"""

import json
import os
import urllib.parse
import urllib.request
from typing import Any, Callable

from .models import Candidate

AMAP_BASE = "https://restapi.amap.com"
TIMEOUT_S = 10

# url -> 解析好的 JSON
Transport = Callable[[str], dict[str, Any]]


class SourceError(Exception):
    """外部数据源失败。kind 和 ErrorInfo 的取值保持一致。"""

    def __init__(self, kind: str, message: str) -> None:
        super().__init__(message)
        self.kind = kind
        self.message = message


def _key(explicit: str | None = None) -> str:
    key = (explicit or os.environ.get("AMAP_KEY", "")).strip()
    if not key:
        raise SourceError("unavailable", "没配 AMAP_KEY，外部数据源不可用")
    return key


def _fetch(url: str, transport: Transport | None = None) -> dict[str, Any]:
    if transport is not None:
        return transport(url)
    try:
        with urllib.request.urlopen(url, timeout=TIMEOUT_S) as response:
            return json.loads(response.read().decode("utf-8"))
    except OSError as exc:
        raise SourceError("upstream", f"请求高德失败: {exc}") from exc


def _check(data: dict[str, Any]) -> None:
    """高德用 status=1 表示成功，其余都是错误。"""
    if str(data.get("status")) != "1":
        info = data.get("info") or "未知错误"
        code = data.get("infocode") or ""
        raise SourceError("upstream", f"高德返回错误: {info} {code}".strip())


def amap_weather(city: str, key: str | None = None, transport: Transport | None = None) -> dict[str, Any]:
    """查某城市实时天气。city 可以是城市名或 adcode。"""
    if not city.strip():
        raise SourceError("bad_args", "city 不能是空的")

    query = urllib.parse.urlencode({"city": city, "key": _key(key), "extensions": "base"})
    data = _fetch(f"{AMAP_BASE}/v3/weather/weatherInfo?{query}", transport)
    _check(data)

    lives = data.get("lives") or []
    if not lives:
        raise SourceError("not_found", f"查不到 {city} 的天气")

    live = lives[0]
    return {
        "city": live.get("city") or city,
        "weather": live.get("weather"),
        "temperature": live.get("temperature"),
        "wind": f"{live.get('winddirection', '')}{live.get('windpower', '')}",
        "humidity": live.get("humidity"),
        "reported_at": live.get("reporttime"),
        "source": "amap",
    }


def amap_places(
    keyword: str,
    city: str,
    key: str | None = None,
    transport: Transport | None = None,
    limit: int = 5,
) -> list[Candidate]:
    """按关键词查地点，翻译成 Candidate。

    高德不提供营业时间，价格也常缺，所以这两项留 None —— 不猜。
    """
    if not keyword.strip():
        raise SourceError("bad_args", "keyword 不能是空的")

    query = urllib.parse.urlencode(
        {"keywords": keyword, "city": city, "key": _key(key), "offset": limit, "page": 1}
    )
    data = _fetch(f"{AMAP_BASE}/v3/place/text?{query}", transport)
    _check(data)

    pois = data.get("pois") or []
    if not pois:
        raise SourceError("not_found", f"在 {city} 查不到「{keyword}」")

    found: list[Candidate] = []
    for poi in pois[:limit]:
        name = poi.get("name") or ""
        poi_id = str(poi.get("id") or "").lower()
        if not name or not poi_id:
            continue

        biz = poi.get("biz_ext") or {}
        price = None
        raw_cost = biz.get("cost")
        if isinstance(raw_cost, str) and raw_cost.strip():
            try:
                price = int(float(raw_cost))
            except ValueError:
                price = None

        found.append(
            Candidate(
                place_id=f"amap-{poi_id[:27]}",
                name=name,
                category=(poi.get("type") or "place").split(";")[0],
                tags=[tag for tag in (poi.get("type") or "").split(";") if tag],
                price_per_person=price,
                attrs={
                    "address": poi.get("address"),
                    "tel": poi.get("tel"),
                    "source": "amap",
                },
            )
        )

    return found
