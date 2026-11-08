"""工具可用性自检：每个工具都真跑一遍，打印结果。

改完 tools.py 就跑一次，不联网、不需要 key：

    python scripts/check_tools.py

全过退出 0，有失败退出 1。
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from group_agent.models import ActionRequest, Candidate
from group_agent.tools import TOOLS, describe_tools, dispatch, load_places


def brief(data) -> str:
    """把结果压成一行，够看就行。"""
    if isinstance(data, list):
        names = "、".join(getattr(item, "place_id", str(item)) for item in data) or "空"
        return f"{len(data)} 条 [{names}]"
    if isinstance(data, Candidate):
        return f"{data.place_id} {data.name} 人均{data.price_per_person}"
    return str(data)


# (说明, 请求, 这次调用应该成功吗)
CASES = [
    ("按类型查店", ActionRequest(tool="search_places", arguments={"kind": "board_game"}), True),
    ("叠加价格上限", ActionRequest(tool="search_places", arguments={"kind": "restaurant", "max_price": 66}), True),
    ("叠加营业时段", ActionRequest(
        tool="search_places", arguments={"kind": "restaurant", "open_from": 1260, "open_to": 1290}), True),
    ("避开忌口标签", ActionRequest(
        tool="search_places", arguments={"kind": "restaurant", "avoid_tags": ["hotpot"]}), True),
    ("查不到返回空表", ActionRequest(
        tool="search_places", arguments={"kind": "restaurant", "max_price": 1}), True),
    ("取一家店", ActionRequest(tool="get_place", arguments={"place_id": "p003"}), True),
    ("店不存在返回空表", ActionRequest(tool="get_place", arguments={"place_id": "p999"}), True),
    # 下面这些要的是“被挡回来”，不是抛异常。
    ("未知工具 -> bad_args", ActionRequest(tool="no_such_tool"), False),
    ("参数名写错 -> bad_args", ActionRequest(
        tool="search_places", arguments={"type": "board_game"}), False),
    ("kind 不合法 -> bad_args", ActionRequest(
        tool="search_places", arguments={"kind": "movie"}), False),
]


def main() -> None:
    places = load_places()
    print(f"fixtures：{len(places)} 家店；工具 {len(TOOLS)} 个")
    print("工具清单：")
    print(describe_tools())
    print()

    failed = 0
    for label, request, want_ok in CASES:
        observation = dispatch(request)
        if observation.ok is want_ok:
            detail = brief(observation.data) if observation.ok else (
                f"{observation.error.kind}：{observation.error.message}"
            )
            print(f"[PASS] {label}：{detail}")
            continue
        failed += 1
        print(f"[FAIL] {label}：期望 ok={want_ok}，实际 {observation.ok} {observation.error or observation.data}")

    print()
    print(f"{len(CASES) - failed}/{len(CASES)} 通过")
    raise SystemExit(1 if failed else 0)


if __name__ == "__main__":
    main()
