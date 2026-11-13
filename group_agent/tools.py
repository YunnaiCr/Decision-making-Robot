"""工具层：在世界里找候选、查事实。

两类工具，形状不同：
- 候选类：返回 list[Candidate]，进 GroupState.found
- 事实类：返回 dict，进 GroupState.facts

“候选”是通用词：一家店、一张票、一个场地，任何能排进方案的东西。
category 不做枚举，属性差异放 Candidate.attrs，所以加一类新东西只需要加数据。

约定
- 外部世界的访问只走这里，别的模块不许直接读 fixtures。
- 参数不合法抛 ToolError，由 dispatch 统一转成 Observation。
- 查不到返回空列表或空，不猜也不造。
- 属性过滤用 attrs（键值匹配），不要为每个品类加一个专用参数。
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

_places: list[Candidate] | None = None
_travel: dict[tuple[str, str], int] | None = None


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


@dataclass(frozen=True)
class Filters:
    """一组候选过滤条件。所有查询类工具共用这份判定，行为才不会各说各话。"""

    tags: tuple[str, ...] = ()
    avoid_tags: tuple[str, ...] = ()
    attrs: tuple[tuple[str, Any], ...] = ()
    max_price: int | None = None
    open_from: int | None = None
    open_to: int | None = None
    max_distance: int | None = None
    min_score: float | None = None


def load_places() -> list[Candidate]:
    """读一次并校验，之后用缓存。字段写错会在这里当场报错。"""
    global _places
    if _places is None:
        text = (FIXTURES / "places.json").read_text(encoding="utf-8")
        _places = [Candidate.model_validate(item) for item in json.loads(text)]
    return _places


def places_by_id() -> dict[str, Candidate]:
    """编号到候选的索引。"""
    return {option.place_id: option for option in load_places()}


def load_travel() -> dict[tuple[str, str], int]:
    """读候选之间的路上时间，双向对称。文件里只写一对，两向都建索引。"""
    global _travel
    if _travel is None:
        raw = json.loads((FIXTURES / "travel.json").read_text(encoding="utf-8"))
        table: dict[tuple[str, str], int] = {}
        for here, others in raw.items():
            for there, minutes in others.items():
                table[(here, there)] = int(minutes)
                table[(there, here)] = int(minutes)
        _travel = table
    return _travel


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


def _clean_tags(tags: list[str] | None) -> tuple[str, ...]:
    """标签统一小写去空白，顺序稳定，方便比较。"""
    return tuple(tag.strip().lower() for tag in (tags or []) if tag.strip())


def _collect(
    tags: list[str] | None = None,
    avoid_tags: list[str] | None = None,
    attrs: dict[str, Any] | None = None,
    max_price: int | None = None,
    open_from: int | None = None,
    open_to: int | None = None,
    max_distance: int | None = None,
    min_score: float | None = None,
) -> Filters:
    """校验过滤参数并收成一个 Filters。所有查询工具都从这里进。"""
    if max_price is not None and max_price < 0:
        raise ToolError("bad_args", "max_price 不能是负数")
    if min_score is not None and not 0 <= min_score <= 5:
        raise ToolError("bad_args", "min_score 必须在 0-5 之间")
    if max_distance is not None and max_distance < 0:
        raise ToolError("bad_args", "max_distance 不能是负数")

    start, end = _window(open_from, open_to)
    return Filters(
        tags=_clean_tags(tags),
        avoid_tags=_clean_tags(avoid_tags),
        attrs=tuple((attrs or {}).items()),
        max_price=max_price,
        open_from=start,
        open_to=end,
        max_distance=max_distance,
        min_score=min_score,
    )


def _match(option: Candidate, filters: Filters) -> bool:
    """一个候选是否满足这组条件。缺数据一律算不满足，不猜。"""
    option_tags = {tag.lower() for tag in option.tags}
    if filters.tags and not set(filters.tags) & option_tags:
        return False
    if filters.avoid_tags and set(filters.avoid_tags) & option_tags:
        return False

    for key, want in filters.attrs:
        if key not in option.attrs:
            return False
        got = option.attrs[key]
        if isinstance(got, str) and isinstance(want, str):
            if got.strip().lower() != want.strip().lower():
                return False
        elif got != want:
            return False

    if filters.max_price is not None:
        if option.price_per_person is None or option.price_per_person > filters.max_price:
            return False
    if filters.open_from is not None:
        if option.open_min is None or option.close_min is None:
            return False
        if option.open_min > filters.open_from or option.close_min < filters.open_to:
            return False
    if filters.max_distance is not None:
        if option.distance_min is None or option.distance_min > filters.max_distance:
            return False
    if filters.min_score is not None:
        if option.score is None or option.score < filters.min_score:
            return False
    return True


def _pick(option_ids: list[str]) -> list[Candidate]:
    """按编号取候选，保持传入顺序，重复的只留一次。有不认识的编号就报错。"""
    index = places_by_id()
    missing = [option_id for option_id in option_ids if option_id not in index]
    if missing:
        raise ToolError("not_found", f"没有这些候选: {missing}")
    return [index[option_id] for option_id in dict.fromkeys(option_ids)]


def search_options(
    category: str | None = None,
    tags: list[str] | None = None,
    avoid_tags: list[str] | None = None,
    attrs: dict[str, Any] | None = None,
    max_price: int | None = None,
    open_from: int | None = None,
    open_to: int | None = None,
    max_distance: int | None = None,
    min_score: float | None = None,
    exclude_ids: list[str] | None = None,
) -> list[Candidate]:
    """按条件找候选。category 自由填，不认识的品类返回空列表。"""
    filters = _collect(tags, avoid_tags, attrs, max_price, open_from, open_to, max_distance, min_score)
    skip = set(exclude_ids or [])
    return [
        option
        for option in load_places()
        if (category is None or option.category == category)
        and option.place_id not in skip
        and _match(option, filters)
    ]


def get_option(option_id: str) -> list[Candidate]:
    """按编号取一个候选的完整资料，返回 0 或 1 项。"""
    option = places_by_id().get(option_id)
    return [option] if option is not None else []


def filter_options(
    option_ids: list[str],
    tags: list[str] | None = None,
    avoid_tags: list[str] | None = None,
    attrs: dict[str, Any] | None = None,
    max_price: int | None = None,
    open_from: int | None = None,
    open_to: int | None = None,
    max_distance: int | None = None,
    min_score: float | None = None,
) -> list[Candidate]:
    """在给定名单里再筛一遍，顺序不变。返回空列表就是都不满足。"""
    filters = _collect(tags, avoid_tags, attrs, max_price, open_from, open_to, max_distance, min_score)
    return [option for option in _pick(option_ids) if _match(option, filters)]


_RANKERS: dict[str, tuple[Callable[[Candidate], Any], bool]] = {
    "price": (lambda option: option.price_per_person, False),
    "score": (lambda option: option.score, True),
    "distance": (lambda option: option.distance_min, False),
}


def rank_options(option_ids: list[str], by: str = "price") -> list[Candidate]:
    """给一批候选排序：price 便宜在前、score 好在前、distance 近在前。缺数据排最后。"""
    if by not in _RANKERS:
        raise ToolError("bad_args", f"by 只能是 {' / '.join(_RANKERS)}，收到 {by!r}")

    key, reverse = _RANKERS[by]
    options = _pick(option_ids)
    known = [option for option in options if key(option) is not None]
    unknown = [option for option in options if key(option) is None]
    known.sort(key=key, reverse=reverse)
    return known + unknown


def alternatives(
    option_id: str,
    tags: list[str] | None = None,
    avoid_tags: list[str] | None = None,
    attrs: dict[str, Any] | None = None,
    max_price: int | None = None,
    open_from: int | None = None,
    open_to: int | None = None,
    max_distance: int | None = None,
    min_score: float | None = None,
) -> list[Candidate]:
    """同品类的其他候选，排除这一个。被否掉之后用它换一个。"""
    origin = places_by_id().get(option_id)
    if origin is None:
        raise ToolError("not_found", f"没有这个候选: {option_id}")
    filters = _collect(tags, avoid_tags, attrs, max_price, open_from, open_to, max_distance, min_score)
    return [
        option
        for option in load_places()
        if option.category == origin.category
        and option.place_id != option_id
        and _match(option, filters)
    ]


def nearby_options(
    from_option_id: str,
    category: str | None = None,
    max_travel_min: int = 30,
    tags: list[str] | None = None,
    avoid_tags: list[str] | None = None,
    attrs: dict[str, Any] | None = None,
    max_price: int | None = None,
    open_from: int | None = None,
    open_to: int | None = None,
    min_score: float | None = None,
) -> list[Candidate]:
    """从某个候选出发、路上不超过 max_travel_min 分钟的候选，按远近排序。"""
    if from_option_id not in places_by_id():
        raise ToolError("not_found", f"没有这个候选: {from_option_id}")
    if max_travel_min < 0:
        raise ToolError("bad_args", "max_travel_min 不能是负数")

    filters = _collect(tags, avoid_tags, attrs, max_price, open_from, open_to, None, min_score)
    table = load_travel()
    reachable: list[tuple[int, Candidate]] = []
    for option in load_places():
        if option.place_id == from_option_id:
            continue
        if category is not None and option.category != category:
            continue
        minutes = table.get((from_option_id, option.place_id))
        if minutes is None or minutes > max_travel_min:
            continue
        if not _match(option, filters):
            continue
        reachable.append((minutes, option))

    reachable.sort(key=lambda pair: pair[0])
    return [option for minutes, option in reachable]


# --- 事实类：返回 dict，进 GroupState.facts -------------------------------


def check_open(option_id: str, start_min: int, end_min: int) -> dict[str, Any]:
    """这个候选在 [start_min, end_min) 这段是否可用。没有时间数据时 open 为 null。"""
    if end_min <= start_min:
        raise ToolError("bad_args", f"end_min({end_min}) 必须大于 start_min({start_min})")

    option = places_by_id().get(option_id)
    if option is None:
        raise ToolError("not_found", f"没有这个候选: {option_id}")

    if option.open_min is None or option.close_min is None:
        return {
            "place_id": option.place_id,
            "name": option.name,
            "open": None,
            "window": None,
            "reason": "没有可用的时间数据，无法判定",
        }

    inside = option.open_min <= start_min and end_min <= option.close_min
    return {
        "place_id": option.place_id,
        "name": option.name,
        "open": inside,
        "window": [option.open_min, option.close_min],
        "reason": (
            f"{option.name} 可用 {option.open_min}-{option.close_min}，"
            f"问的是 {start_min}-{end_min}，{'在范围内' if inside else '超出范围'}"
        ),
    }


def travel_time(from_option_id: str, to_option_id: str) -> dict[str, Any]:
    """两者之间的路上时间（分钟）。同一个候选是 0；没测过的组合报 not_found。"""
    known = places_by_id()
    for option_id in (from_option_id, to_option_id):
        if option_id not in known:
            raise ToolError("not_found", f"没有这个候选: {option_id}")

    if from_option_id == to_option_id:
        minutes = 0
    else:
        minutes = load_travel().get((from_option_id, to_option_id))
    if minutes is None:
        raise ToolError("not_found", f"没测过 {from_option_id} → {to_option_id} 的路程")

    return {"from": from_option_id, "to": to_option_id, "minutes": minutes}


def estimate_cost(option_ids: list[str], headcount: int = 1) -> dict[str, Any]:
    """把几个候选的人均价加起来，按人数算总价。有未知价时给出 missing。"""
    if headcount < 1:
        raise ToolError("bad_args", "headcount 至少是 1")

    options = _pick(option_ids)
    missing = [option.place_id for option in options if option.price_per_person is None]
    if missing:
        return {"per_person": None, "total": None, "missing": missing, "headcount": headcount}

    per_person = sum(option.price_per_person or 0 for option in options)
    return {
        "per_person": per_person,
        "total": per_person * headcount,
        "missing": [],
        "headcount": headcount,
    }


def catalog() -> dict[str, Any]:
    """这个世界里都有什么：品类计数、全部标签、属性的键和取值。"""
    categories: dict[str, int] = {}
    tags: set[str] = set()
    attrs: dict[str, set[str]] = {}

    def label(value: Any) -> str:
        # 属性值可能是 bool / 数字，统一转成能直接读的字符串。
        return value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)

    for option in load_places():
        categories[option.category] = categories.get(option.category, 0) + 1
        tags.update(tag.strip().lower() for tag in option.tags)
        for key, value in option.attrs.items():
            attrs.setdefault(key, set()).add(label(value))

    return {
        "categories": categories,
        "tags": sorted(tags),
        "attrs": {key: sorted(values) for key, values in attrs.items()},
    }


# 工具名 -> 函数。加一个工具就在这里注册。
TOOLS = {
    "catalog": catalog,
    "search_options": search_options,
    "get_option": get_option,
    "filter_options": filter_options,
    "rank_options": rank_options,
    "alternatives": alternatives,
    "nearby_options": nearby_options,
    "check_open": check_open,
    "travel_time": travel_time,
    "estimate_cost": estimate_cost,
}

# 工具的对外说明，顺序就是提示词里的顺序。
TOOL_SPECS = [
    ToolSpec(
        "catalog",
        "这个世界里有什么：品类计数、全部标签、属性的键和取值。不知道能查什么时先看它",
        "{categories, tags, attrs}",
    ),
    ToolSpec(
        "search_options",
        "按条件找候选：品类可省略，再叠加正向标签/忌口/属性/价格/时间窗/距离/评分",
        "list[Candidate]",
    ),
    ToolSpec("get_option", "按编号取一个候选的完整资料，查不到给空列表", "list[Candidate]（0 或 1 项）"),
    ToolSpec("filter_options", "在已查到的名单里再筛一遍，返回空列表就是都不满足", "list[Candidate]"),
    ToolSpec("rank_options", "给一批候选排序：price 便宜、score 好、distance 近在前", "list[Candidate]"),
    ToolSpec("alternatives", "同品类的其他候选，排除这一个，被否掉时换一个", "list[Candidate]"),
    ToolSpec(
        "nearby_options",
        "从某个候选出发、路上不超过 max_travel_min 分钟的候选，按远近排序",
        "list[Candidate]",
    ),
    ToolSpec("check_open", "某个候选在指定时段是否可用；没有时间数据时 open 为 null", "{open, window, reason}"),
    ToolSpec("travel_time", "两个候选之间的路上分钟数", "{minutes}"),
    ToolSpec(
        "estimate_cost",
        "几个候选的人均价合计与按人数算的总价，缺价的进 missing",
        "{per_person, total, missing}",
    ),
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
