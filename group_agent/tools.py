"""工具层：查店铺。

约定
- 外部世界的访问只走这里，别的模块不许直接读 fixtures。
- 参数不合法抛 ToolError，由 dispatch 统一转成 Observation。
- 查不到返回空列表，不猜也不造。
- 每个工具都返回 list[Candidate]：主循环只把店铺列表写进 found，
  返回别的形状会在校验阶段炸，所以形状由这一层保证。
- 加一个工具：写函数 -> 加进 TOOLS -> 加一条 ToolSpec。提示词会自动跟着变。
"""

import inspect
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from pydantic import ValidationError

from .models import ActionRequest, Candidate, ErrorInfo, Observation

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures"

# 噪音从低到高，max_noise 用它比较。
_NOISE_RANK = {"low": 0, "mid": 1, "high": 2}

# 世界里目前只有这两类店。加类型要同时改 models.Candidate。
PLACE_KINDS = ("board_game", "restaurant")

_places: list[Candidate] | None = None


class ToolError(Exception):
    """工具自己判定的失败：参数不对、数据缺失。dispatch 负责转成 ErrorInfo。"""

    def __init__(self, kind: str, message: str, retryable: bool = False) -> None:
        super().__init__(message)
        self.kind = kind
        self.message = message
        self.retryable = retryable


@dataclass(frozen=True)
class ToolSpec:
    """工具对外的说明。签名不写在这里，从函数现取，避免两处不同步。"""

    name: str
    summary: str
    returns: str


def load_places() -> list[Candidate]:
    """读一次并校验，之后用缓存。字段写错会在这里当场报错。"""
    global _places
    if _places is None:
        text = (FIXTURES / "places.json").read_text(encoding="utf-8")
        _places = [Candidate.model_validate(item) for item in json.loads(text)]
    return _places


def places_by_id() -> dict[str, Candidate]:
    """place_id 到店的索引。"""
    return {place.place_id: place for place in load_places()}


def _window(open_from: int | None, open_to: int | None) -> tuple[int | None, int | None]:
    """把两个可选参数收成一个时段。只给 open_from 时，当作那一刻。"""
    if open_from is None and open_to is None:
        return None, None
    if open_from is None:
        raise ToolError("bad_args", "open_to 要和 open_from 一起给")
    end = open_from if open_to is None else open_to
    if not 0 <= open_from <= 1440 or not 0 <= end <= 1440:
        raise ToolError("bad_args", "open_from / open_to 必须在 0-1440 分钟之间")
    if end < open_from:
        raise ToolError("bad_args", f"open_to({end}) 不能早于 open_from({open_from})")
    return open_from, end


def search_places(
    kind: str,
    max_price: int | None = None,
    open_from: int | None = None,
    open_to: int | None = None,
    avoid_tags: list[str] | None = None,
    max_distance: int | None = None,
    max_noise: str | None = None,
) -> list[Candidate]:
    """按类型查店，可再按价格、营业时段、忌口标签、距离、噪音过滤。查不到返回空列表。"""
    if kind not in PLACE_KINDS:
        raise ToolError("bad_args", f"kind 只能是 {' / '.join(PLACE_KINDS)}，收到 {kind!r}")
    if max_price is not None and max_price < 0:
        raise ToolError("bad_args", "max_price 不能是负数")
    if max_noise is not None and max_noise not in _NOISE_RANK:
        raise ToolError("bad_args", f"max_noise 只能是 {' / '.join(_NOISE_RANK)}，收到 {max_noise!r}")

    start, end = _window(open_from, open_to)
    avoid = {tag.strip().lower() for tag in (avoid_tags or []) if tag.strip()}

    found: list[Candidate] = []
    for place in load_places():
        if place.kind != kind:
            continue
        if max_price is not None and place.price_per_person > max_price:
            continue
        if start is not None and (place.open_min > start or place.close_min < end):
            continue
        if avoid & {tag.lower() for tag in place.tags}:
            continue
        if max_distance is not None:
            # 距离未知就不算它近，宁可漏掉也不要猜。
            if place.distance_min is None or place.distance_min > max_distance:
                continue
        if max_noise is not None:
            if place.noise is None or _NOISE_RANK[place.noise] > _NOISE_RANK[max_noise]:
                continue
        found.append(place)
    return found


def get_place(place_id: str) -> list[Candidate]:
    """按编号取一家店的完整资料，返回 0 或 1 项。"""
    place = places_by_id().get(place_id)
    return [place] if place is not None else []


# 工具名 -> 函数。加一个工具就在这里注册。
TOOLS = {
    "search_places": search_places,
    "get_place": get_place,
}

# 工具的对外说明，顺序就是提示词里的顺序。
TOOL_SPECS = [
    ToolSpec(
        "search_places",
        "按类型查店；再按人均价、营业时段、忌口标签、距离、噪音过滤。查不到返回空列表",
        "list[Candidate]",
    ),
    ToolSpec("get_place", "按编号取一家店的完整资料，查不到返回空列表", "list[Candidate]（0 或 1 项）"),
]


def _signature_text(fn: Callable[..., Any]) -> str:
    """把函数签名摊成一行，参数名和默认值都从函数现取。"""
    parts = []
    for param in inspect.signature(fn).parameters.values():
        if param.default is inspect.Parameter.empty:
            parts.append(param.name)
        else:
            parts.append(f"{param.name}={param.default!r}")
    return ", ".join(parts)


def describe_tools() -> str:
    """给规划层看的工具清单。"""
    lines = []
    for spec in TOOL_SPECS:
        lines.append(f"   - {spec.name}({_signature_text(TOOLS[spec.name])}) -> {spec.returns}：{spec.summary}")
    return "\n".join(lines)


def _fail(tool: str, kind: str, message: str, retryable: bool = False) -> Observation:
    return Observation(
        ok=False,
        tool=tool,
        error=ErrorInfo(kind=kind, message=message, retryable=retryable),
    )


def dispatch(request: ActionRequest) -> Observation:
    """把 Planner 给的动作执行掉，返回统一格式的结果。

    错误分四类：参数不对（bad_args）、查不到（not_found）、上游读失败
    （upstream，可重试）、工具内部出问题（internal）。参数先 bind 再调用，
    这样工具内部的 TypeError 不会被误判成参数错。
    """
    fn = TOOLS.get(request.tool)
    if fn is None:
        return _fail(request.tool, "bad_args", f"没有这个工具: {request.tool}")

    try:
        inspect.signature(fn).bind(**request.arguments)
    except TypeError as exc:
        return _fail(request.tool, "bad_args", f"参数不对: {exc}")

    try:
        data = fn(**request.arguments)
    except ToolError as exc:
        return _fail(request.tool, exc.kind, exc.message, exc.retryable)
    except ValidationError as exc:
        return _fail(request.tool, "bad_args", str(exc))
    except (OSError, TimeoutError) as exc:
        # 读不到 fixtures / 超时这类，换一次可能就好了。
        return _fail(request.tool, "upstream", f"{type(exc).__name__}: {exc}", retryable=True)
    except Exception as exc:
        return _fail(request.tool, "internal", f"{type(exc).__name__}: {exc}")

    if data is None:
        args = json.dumps(request.arguments, ensure_ascii=False)
        return _fail(request.tool, "not_found", f"{request.tool}({args}) 查不到")

    return Observation(ok=True, tool=request.tool, data=data)
