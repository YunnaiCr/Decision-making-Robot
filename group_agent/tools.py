"""工具层：查店铺、筛名单、排顺序、换一家、看附近。

约定
- 外部世界的访问只走这里，别的模块不许直接读 fixtures。
- 参数不合法抛 ToolError，由 dispatch 统一转成 Observation。
- 查不到返回空列表，不猜也不造。
- 每个工具都返回 list[Candidate]：主循环只把店铺列表写进 found，
  返回别的形状会在校验阶段炸，所以形状由这一层保证。
  需要“是/否”类答案时用空列表和非空列表表达：filter_places([id], open_from=...)
  返回空，就说明那个时段不营业。
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

# 噪音从低到高，max_noise 和 by="noise" 都用它比较。
_NOISE_RANK = {"low": 0, "mid": 1, "high": 2}

# 世界里目前只有这两类店。加类型要同时改 models.Candidate。
PLACE_KINDS = ("board_game", "restaurant")

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
    """一组店铺过滤条件。四个查询类工具共用同一份判定，行为才不会各说各话。"""

    max_price: int | None = None
    open_from: int | None = None
    open_to: int | None = None
    avoid_tags: tuple[str, ...] = ()
    max_distance: int | None = None
    max_noise: str | None = None
    min_score: float | None = None
    need_private_room: bool = False


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


def load_travel() -> dict[tuple[str, str], int]:
    """读店与店之间的路上时间，双向对称。文件里只写一对，两向都建索引。"""
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


def _collect(
    max_price: int | None = None,
    open_from: int | None = None,
    open_to: int | None = None,
    avoid_tags: list[str] | None = None,
    max_distance: int | None = None,
    max_noise: str | None = None,
    min_score: float | None = None,
    need_private_room: bool = False,
) -> Filters:
    """校验筛选参数并收成一个 Filters。四个查询工具都从这里进。"""
    if max_price is not None and max_price < 0:
        raise ToolError("bad_args", "max_price 不能是负数")
    if max_noise is not None and max_noise not in _NOISE_RANK:
        raise ToolError("bad_args", f"max_noise 只能是 {' / '.join(_NOISE_RANK)}，收到 {max_noise!r}")
    if min_score is not None and not 0 <= min_score <= 5:
        raise ToolError("bad_args", "min_score 必须在 0-5 之间")

    start, end = _window(open_from, open_to)
    avoid = tuple(sorted({tag.strip().lower() for tag in (avoid_tags or []) if tag.strip()}))
    return Filters(
        max_price=max_price,
        open_from=start,
        open_to=end,
        avoid_tags=avoid,
        max_distance=max_distance,
        max_noise=max_noise,
        min_score=min_score,
        need_private_room=bool(need_private_room),
    )


def _match(place: Candidate, filters: Filters) -> bool:
    """一家店是否满足这组条件。缺数据一律算不满足，不猜。"""
    if filters.max_price is not None and place.price_per_person > filters.max_price:
        return False
    if filters.open_from is not None and (
        place.open_min > filters.open_from or place.close_min < filters.open_to
    ):
        return False
    if filters.avoid_tags and set(filters.avoid_tags) & {tag.lower() for tag in place.tags}:
        return False
    if filters.max_distance is not None:
        if place.distance_min is None or place.distance_min > filters.max_distance:
            return False
    if filters.max_noise is not None:
        if place.noise is None or _NOISE_RANK[place.noise] > _NOISE_RANK[filters.max_noise]:
            return False
    if filters.min_score is not None:
        if place.score is None or place.score < filters.min_score:
            return False
    if filters.need_private_room and place.has_private_room is not True:
        return False
    return True


def _pick(place_ids: list[str]) -> list[Candidate]:
    """按 id 取店，保持传入顺序，重复的只留一次。有不认识的 id 就报错。"""
    index = places_by_id()
    missing = [place_id for place_id in place_ids if place_id not in index]
    if missing:
        raise ToolError("not_found", f"没有这些店: {missing}")
    return [index[place_id] for place_id in dict.fromkeys(place_ids)]


def search_places(
    kind: str,
    max_price: int | None = None,
    open_from: int | None = None,
    open_to: int | None = None,
    avoid_tags: list[str] | None = None,
    max_distance: int | None = None,
    max_noise: str | None = None,
    min_score: float | None = None,
    need_private_room: bool = False,
) -> list[Candidate]:
    """按类型查店，再按价格/营业时段/忌口/距离/噪音/评分/包间过滤。查不到返回空列表。"""
    if kind not in PLACE_KINDS:
        raise ToolError("bad_args", f"kind 只能是 {' / '.join(PLACE_KINDS)}，收到 {kind!r}")
    filters = _collect(
        max_price, open_from, open_to, avoid_tags, max_distance, max_noise, min_score, need_private_room
    )
    return [place for place in load_places() if place.kind == kind and _match(place, filters)]


def get_place(place_id: str) -> list[Candidate]:
    """按编号取一家店的完整资料，返回 0 或 1 项。"""
    place = places_by_id().get(place_id)
    return [place] if place is not None else []


def filter_places(
    place_ids: list[str],
    max_price: int | None = None,
    open_from: int | None = None,
    open_to: int | None = None,
    avoid_tags: list[str] | None = None,
    max_distance: int | None = None,
    max_noise: str | None = None,
    min_score: float | None = None,
    need_private_room: bool = False,
) -> list[Candidate]:
    """在给定名单里再筛一遍，顺序不变。用来判断“这家那个点还开着吗”。"""
    filters = _collect(
        max_price, open_from, open_to, avoid_tags, max_distance, max_noise, min_score, need_private_room
    )
    return [place for place in _pick(place_ids) if _match(place, filters)]


_RANKERS: dict[str, tuple[Callable[[Candidate], Any], bool]] = {
    "price": (lambda place: place.price_per_person, False),
    "score": (lambda place: place.score, True),
    "distance": (lambda place: place.distance_min, False),
    "noise": (lambda place: None if place.noise is None else _NOISE_RANK[place.noise], False),
}


def rank_places(place_ids: list[str], by: str = "price") -> list[Candidate]:
    """给一批店排序：price 便宜在前、score 高在前、distance 近在前、noise 安静在前。缺数据排最后。"""
    if by not in _RANKERS:
        raise ToolError("bad_args", f"by 只能是 {' / '.join(_RANKERS)}，收到 {by!r}")

    key, reverse = _RANKERS[by]
    places = _pick(place_ids)
    known = [place for place in places if key(place) is not None]
    unknown = [place for place in places if key(place) is None]
    known.sort(key=key, reverse=reverse)
    return known + unknown


def alternatives(
    place_id: str,
    max_price: int | None = None,
    open_from: int | None = None,
    open_to: int | None = None,
    avoid_tags: list[str] | None = None,
    max_distance: int | None = None,
    max_noise: str | None = None,
    min_score: float | None = None,
    need_private_room: bool = False,
) -> list[Candidate]:
    """同类型的其他店，排除这一家。被否掉之后用它换店。"""
    origin = places_by_id().get(place_id)
    if origin is None:
        raise ToolError("not_found", f"没有这个店: {place_id}")
    filters = _collect(
        max_price, open_from, open_to, avoid_tags, max_distance, max_noise, min_score, need_private_room
    )
    return [
        place
        for place in load_places()
        if place.kind == origin.kind and place.place_id != place_id and _match(place, filters)
    ]


def nearby_places(
    from_place_id: str,
    kind: str | None = None,
    max_travel_min: int = 30,
    max_price: int | None = None,
    open_from: int | None = None,
    open_to: int | None = None,
    avoid_tags: list[str] | None = None,
    max_noise: str | None = None,
    min_score: float | None = None,
    need_private_room: bool = False,
) -> list[Candidate]:
    """从某家店出发、路上不超过 max_travel_min 分钟的店，按远近排序。"""
    if from_place_id not in places_by_id():
        raise ToolError("not_found", f"没有这个店: {from_place_id}")
    if kind is not None and kind not in PLACE_KINDS:
        raise ToolError("bad_args", f"kind 只能是 {' / '.join(PLACE_KINDS)}，收到 {kind!r}")
    if max_travel_min < 0:
        raise ToolError("bad_args", "max_travel_min 不能是负数")

    filters = _collect(
        max_price, open_from, open_to, avoid_tags, None, max_noise, min_score, need_private_room
    )
    table = load_travel()
    reachable: list[tuple[int, Candidate]] = []
    for place in load_places():
        if place.place_id == from_place_id:
            continue
        if kind is not None and place.kind != kind:
            continue
        minutes = table.get((from_place_id, place.place_id))
        if minutes is None or minutes > max_travel_min:
            continue
        if not _match(place, filters):
            continue
        reachable.append((minutes, place))

    reachable.sort(key=lambda pair: pair[0])
    return [place for minutes, place in reachable]


# 工具名 -> 函数。加一个工具就在这里注册。
TOOLS = {
    "search_places": search_places,
    "get_place": get_place,
    "filter_places": filter_places,
    "rank_places": rank_places,
    "alternatives": alternatives,
    "nearby_places": nearby_places,
}

# 工具的对外说明，顺序就是提示词里的顺序。
TOOL_SPECS = [
    ToolSpec(
        "search_places",
        "按类型查店，再按价格/营业/忌口/距离/噪音/评分/包间过滤，查不到给空列表",
        "list[Candidate]",
    ),
    ToolSpec("get_place", "按编号取一家店的完整资料，查不到给空列表", "list[Candidate]（0 或 1 项）"),
    ToolSpec("filter_places", "在已查到的名单里再筛一遍，返回空列表就是都不满足", "list[Candidate]"),
    ToolSpec("rank_places", "给一批店排序：price 便宜、score 好评、distance 近、noise 安静在前", "list[Candidate]"),
    ToolSpec("alternatives", "同类型的其他店，排除这一家，被否掉时换店", "list[Candidate]"),
    ToolSpec("nearby_places", "从某家店出发、路上不超过 max_travel_min 分钟的店，按远近排序", "list[Candidate]"),
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
