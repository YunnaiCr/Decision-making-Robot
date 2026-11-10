"""工具层：查选项、筛名单、排顺序、换一家、看附近。

“选项”是这里的通用词：今天它落在店铺上（`Candidate`），因为数据模型只有
`board_game` / `restaurant` 两类。工具接口不写死“吃饭”，品类、正向标签、
排除名单都交给调用方，换一类数据时不用改函数签名。

约定
- 外部世界的访问只走这里，别的模块不许直接读 fixtures。
- 参数不合法抛 ToolError，由 dispatch 统一转成 Observation。
- 查不到返回空列表，不猜也不造。
- 每个工具都返回 list[Candidate]：主循环只把这种列表写进 found，
  返回别的形状会在校验阶段炸，所以形状由这一层保证。
  需要“是/否”类答案时用空列表和非空列表表达：filter_options([id], open_from=...)
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

# 数据模型目前只认这两类。kind 可以不给（跨品类查），给了就必须是其中之一。
KINDS = ("board_game", "restaurant")

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
    """一组选项过滤条件。四个查询类工具共用同一份判定，行为才不会各说各话。"""

    max_price: int | None = None
    open_from: int | None = None
    open_to: int | None = None
    tags: tuple[str, ...] = ()
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
    """编号到选项的索引。"""
    return {place.place_id: place for place in load_places()}


def load_travel() -> dict[tuple[str, str], int]:
    """读选项之间的路上时间，双向对称。文件里只写一对，两向都建索引。"""
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
    return tuple(sorted({tag.strip().lower() for tag in (tags or []) if tag.strip()}))


def _collect(
    max_price: int | None = None,
    open_from: int | None = None,
    open_to: int | None = None,
    tags: list[str] | None = None,
    avoid_tags: list[str] | None = None,
    max_distance: int | None = None,
    max_noise: str | None = None,
    min_score: float | None = None,
    need_private_room: bool = False,
) -> Filters:
    """校验筛选参数并收成一个 Filters。所有查询工具都从这里进。"""
    if max_price is not None and max_price < 0:
        raise ToolError("bad_args", "max_price 不能是负数")
    if max_noise is not None and max_noise not in _NOISE_RANK:
        raise ToolError("bad_args", f"max_noise 只能是 {' / '.join(_NOISE_RANK)}，收到 {max_noise!r}")
    if min_score is not None and not 0 <= min_score <= 5:
        raise ToolError("bad_args", "min_score 必须在 0-5 之间")

    start, end = _window(open_from, open_to)
    return Filters(
        max_price=max_price,
        open_from=start,
        open_to=end,
        tags=_clean_tags(tags),
        avoid_tags=_clean_tags(avoid_tags),
        max_distance=max_distance,
        max_noise=max_noise,
        min_score=min_score,
        need_private_room=bool(need_private_room),
    )


def _match(option: Candidate, filters: Filters) -> bool:
    """一个选项是否满足这组条件。缺数据一律算不满足，不猜。"""
    if filters.max_price is not None and option.price_per_person > filters.max_price:
        return False
    if filters.open_from is not None and (
        option.open_min > filters.open_from or option.close_min < filters.open_to
    ):
        return False

    option_tags = {tag.lower() for tag in option.tags}
    # 正向标签：命中任意一个就算；没写就是不限。
    if filters.tags and not set(filters.tags) & option_tags:
        return False
    if filters.avoid_tags and set(filters.avoid_tags) & option_tags:
        return False

    if filters.max_distance is not None:
        if option.distance_min is None or option.distance_min > filters.max_distance:
            return False
    if filters.max_noise is not None:
        if option.noise is None or _NOISE_RANK[option.noise] > _NOISE_RANK[filters.max_noise]:
            return False
    if filters.min_score is not None:
        if option.score is None or option.score < filters.min_score:
            return False
    if filters.need_private_room and option.has_private_room is not True:
        return False
    return True


def _pick(option_ids: list[str]) -> list[Candidate]:
    """按编号取选项，保持传入顺序，重复的只留一次。有不认识的编号就报错。"""
    index = places_by_id()
    missing = [option_id for option_id in option_ids if option_id not in index]
    if missing:
        raise ToolError("not_found", f"没有这些选项: {missing}")
    return [index[option_id] for option_id in dict.fromkeys(option_ids)]


def _check_kind(kind: str | None) -> None:
    """品类可以不给（跨品类查），给了就必须是认识的。"""
    if kind is not None and kind not in KINDS:
        raise ToolError("bad_args", f"kind 只能是 {' / '.join(KINDS)} 或省略，收到 {kind!r}")


def search_options(
    kind: str | None = None,
    tags: list[str] | None = None,
    exclude_ids: list[str] | None = None,
    max_price: int | None = None,
    open_from: int | None = None,
    open_to: int | None = None,
    avoid_tags: list[str] | None = None,
    max_distance: int | None = None,
    max_noise: str | None = None,
    min_score: float | None = None,
    need_private_room: bool = False,
) -> list[Candidate]:
    """按条件找选项。不给 kind 就是跨品类查，返回空列表表示没有符合条件的。"""
    _check_kind(kind)
    filters = _collect(
        max_price=max_price,
        open_from=open_from,
        open_to=open_to,
        tags=tags,
        avoid_tags=avoid_tags,
        max_distance=max_distance,
        max_noise=max_noise,
        min_score=min_score,
        need_private_room=need_private_room,
    )
    # 排除名单里不认识的编号直接跳过：它可能来自更早一轮的状态。
    skip = set(exclude_ids or [])
    return [
        option
        for option in load_places()
        if (kind is None or option.kind == kind)
        and option.place_id not in skip
        and _match(option, filters)
    ]


def get_option(option_id: str) -> list[Candidate]:
    """按编号取一个选项的完整资料，返回 0 或 1 项。"""
    option = places_by_id().get(option_id)
    return [option] if option is not None else []


def filter_options(
    option_ids: list[str],
    tags: list[str] | None = None,
    max_price: int | None = None,
    open_from: int | None = None,
    open_to: int | None = None,
    avoid_tags: list[str] | None = None,
    max_distance: int | None = None,
    max_noise: str | None = None,
    min_score: float | None = None,
    need_private_room: bool = False,
) -> list[Candidate]:
    """在给定名单里再筛一遍，顺序不变。用来判断“这个选项那个点还合适吗”。"""
    filters = _collect(
        max_price=max_price,
        open_from=open_from,
        open_to=open_to,
        tags=tags,
        avoid_tags=avoid_tags,
        max_distance=max_distance,
        max_noise=max_noise,
        min_score=min_score,
        need_private_room=need_private_room,
    )
    return [option for option in _pick(option_ids) if _match(option, filters)]


_RANKERS: dict[str, tuple[Callable[[Candidate], Any], bool]] = {
    "price": (lambda option: option.price_per_person, False),
    "score": (lambda option: option.score, True),
    "distance": (lambda option: option.distance_min, False),
    "noise": (lambda option: None if option.noise is None else _NOISE_RANK[option.noise], False),
}


def rank_options(option_ids: list[str], by: str = "price") -> list[Candidate]:
    """给一批选项排序：price 便宜在前、score 好在前、distance 近在前、noise 安静在前。缺数据排最后。"""
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
    max_price: int | None = None,
    open_from: int | None = None,
    open_to: int | None = None,
    avoid_tags: list[str] | None = None,
    max_distance: int | None = None,
    max_noise: str | None = None,
    min_score: float | None = None,
    need_private_room: bool = False,
) -> list[Candidate]:
    """同品类的其他选项，排除这一个。被否掉之后用它换一个。"""
    origin = places_by_id().get(option_id)
    if origin is None:
        raise ToolError("not_found", f"没有这个选项: {option_id}")
    filters = _collect(
        max_price=max_price,
        open_from=open_from,
        open_to=open_to,
        tags=tags,
        avoid_tags=avoid_tags,
        max_distance=max_distance,
        max_noise=max_noise,
        min_score=min_score,
        need_private_room=need_private_room,
    )
    return [
        option
        for option in load_places()
        if option.kind == origin.kind and option.place_id != option_id and _match(option, filters)
    ]


def nearby_options(
    from_option_id: str,
    kind: str | None = None,
    max_travel_min: int = 30,
    tags: list[str] | None = None,
    max_price: int | None = None,
    open_from: int | None = None,
    open_to: int | None = None,
    avoid_tags: list[str] | None = None,
    max_noise: str | None = None,
    min_score: float | None = None,
    need_private_room: bool = False,
) -> list[Candidate]:
    """从某个选项出发、路上不超过 max_travel_min 分钟的选项，按远近排序。"""
    if from_option_id not in places_by_id():
        raise ToolError("not_found", f"没有这个选项: {from_option_id}")
    _check_kind(kind)
    if max_travel_min < 0:
        raise ToolError("bad_args", "max_travel_min 不能是负数")

    filters = _collect(
        max_price=max_price,
        open_from=open_from,
        open_to=open_to,
        tags=tags,
        avoid_tags=avoid_tags,
        max_distance=None,
        max_noise=max_noise,
        min_score=min_score,
        need_private_room=need_private_room,
    )
    table = load_travel()
    reachable: list[tuple[int, Candidate]] = []
    for option in load_places():
        if option.place_id == from_option_id:
            continue
        if kind is not None and option.kind != kind:
            continue
        minutes = table.get((from_option_id, option.place_id))
        if minutes is None or minutes > max_travel_min:
            continue
        if not _match(option, filters):
            continue
        reachable.append((minutes, option))

    reachable.sort(key=lambda pair: pair[0])
    return [option for minutes, option in reachable]


# 工具名 -> 函数。加一个工具就在这里注册。
TOOLS = {
    "search_options": search_options,
    "get_option": get_option,
    "filter_options": filter_options,
    "rank_options": rank_options,
    "alternatives": alternatives,
    "nearby_options": nearby_options,
}

# 工具的对外说明，顺序就是提示词里的顺序。
TOOL_SPECS = [
    ToolSpec(
        "search_options",
        "按条件找选项：品类可省略，可给正向标签、忌口标签、排除名单，再叠加价格/营业/距离/噪音/评分/包间",
        "list[Candidate]",
    ),
    ToolSpec("get_option", "按编号取一个选项的完整资料，查不到给空列表", "list[Candidate]（0 或 1 项）"),
    ToolSpec("filter_options", "在已查到的名单里再筛一遍，返回空列表就是都不满足", "list[Candidate]"),
    ToolSpec("rank_options", "给一批选项排序：price 便宜、score 好、distance 近、noise 安静在前", "list[Candidate]"),
    ToolSpec("alternatives", "同品类的其他选项，排除这一个，被否掉时换一个", "list[Candidate]"),
    ToolSpec("nearby_options", "从某个选项出发、路上不超过 max_travel_min 分钟的选项，按远近排序", "list[Candidate]"),
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
