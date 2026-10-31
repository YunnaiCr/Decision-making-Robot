"""工具：目前全部读 fixtures，不联网。以后接真 API 只换这个文件内部。"""

import json
from pathlib import Path
from typing import Any

from .models import ActionRequest, ErrorInfo, Observation

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures"

_places: list[dict[str, Any]] | None = None


def load_places() -> list[dict[str, Any]]:
    """读一次，之后用缓存。"""
    global _places
    if _places is None:
        text = (FIXTURES / "places.json").read_text(encoding="utf-8")
        _places = json.loads(text)
    return _places


def search_places(kind: str, max_price: int | None = None) -> list[dict[str, Any]]:
    """按类型查店，可按人均价上限过滤。"""
    found = []
    for place in load_places():
        if place["kind"] != kind:
            continue
        if max_price is not None and place["price_per_person"] > max_price:
            continue
        found.append(place)
    return found


def get_place(place_id: str) -> dict[str, Any] | None:
    """按编号查一家店，查不到返回 None。"""
    for place in load_places():
        if place["place_id"] == place_id:
            return place
    return None


TOOLS = {
    "search_places": search_places,
    "get_place": get_place,
}


def dispatch(request: ActionRequest) -> Observation:
    """把 Planner 给的动作执行掉，返回统一格式的结果。"""
    fn = TOOLS.get(request.tool)
    if fn is None:
        return Observation(
            ok=False,
            tool=request.tool,
            error=ErrorInfo(kind="bad_args", message=f"没有这个工具: {request.tool}"),
        )

    try:
        data = fn(**request.arguments)
    except TypeError as exc:
        return Observation(
            ok=False,
            tool=request.tool,
            error=ErrorInfo(kind="bad_args", message=str(exc)),
        )

    if data is None:
        return Observation(
            ok=False,
            tool=request.tool,
            error=ErrorInfo(kind="not_found", message="查不到"),
        )

    return Observation(ok=True, tool=request.tool, data=data)
