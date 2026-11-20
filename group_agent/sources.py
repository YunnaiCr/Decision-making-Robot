"""外部数据源：从别的平台取数据。

现在只接高德（Amap）：天气和 POI（地点）搜索。
这一层只负责「发请求 + 判错」，数据怎么翻译成我们的类型交给 `adapters.py`——
真实世界的字段形状放在一处，测试也只需要对着抓下来的原始报文跑。

三条规矩
- key 从环境变量 `AMAP_KEY` 读，没配就抛 `SourceError("unavailable")`，
  不静默降级成假数据
- 网络请求可以注入 `transport`，测试离线跑，不碰网
- 不做缓存、不做重试：那是调用方（tools）该决定的事

想接别的平台（和风天气、豆瓣、大众点评…）：照着 `amap_weather` 写一个函数，
把响应交给 `adapters` 里对应的翻译函数，再在 `tools.py` 注册一个工具。
注意大多数平台没有公开 API，接之前先确认服务条款。
"""

import json
import os
import urllib.parse
import urllib.request
from typing import Any, Callable

from . import adapters
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


def amap_weather(
    city: str,
    date: str | None = None,
    key: str | None = None,
    transport: Transport | None = None,
) -> dict[str, Any]:
    """查天气。不给 date 返回实时；给了就只认预报里那一天（覆盖不到会说查不到）。

    city 可以是城市名或 adcode。
    """
    if not city.strip():
        raise SourceError("bad_args", "city 不能是空的")

    # 高德的分水岭：base 只给实时，all 只给预报。要哪个就请求哪个，别指望一次拿全。
    extensions = "all" if date else "base"
    query = urllib.parse.urlencode({"city": city, "key": _key(key), "extensions": extensions})
    data = _fetch(f"{AMAP_BASE}/v3/weather/weatherInfo?{query}", transport)
    _check(data)
    return adapters.amap_weather_fact(data, date)


def amap_places(
    keyword: str,
    city: str,
    key: str | None = None,
    transport: Transport | None = None,
    limit: int = 5,
) -> list[Candidate]:
    """按关键词查地点，翻译成 Candidate。

    高德不保证给营业时间和价格，缺了就留 None —— 筛选时会被当作「不满足」。
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

    options = [option for poi in pois if (option := adapters.amap_poi(poi)) is not None]
    if not options:
        raise SourceError("not_found", f"在 {city} 查到的「{keyword}」都缺名字或编号")
    return options[:limit]
