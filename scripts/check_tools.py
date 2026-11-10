"""工具可用性自检：每个工具都真跑一遍，打印结果。

改完 tools.py 就跑一次，不联网、不需要 key：

    python scripts/check_tools.py

全过退出 0，有失败退出 1。
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from group_agent.models import ActionRequest, Candidate
from group_agent.tools import TOOLS, describe_tools, dispatch, load_places, load_travel


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
    # 查：品类可以省略，也可以叠加各种条件
    ("跨品类查（不给 kind）", ActionRequest(tool="search_options", arguments={}), True),
    ("按类型查", ActionRequest(tool="search_options", arguments={"kind": "board_game"}), True),
    ("按正向标签查", ActionRequest(tool="search_options", arguments={"tags": ["hotpot"]}), True),
    ("叠加价格上限", ActionRequest(
        tool="search_options", arguments={"kind": "restaurant", "max_price": 66}), True),
    ("叠加营业时段", ActionRequest(
        tool="search_options", arguments={"kind": "restaurant", "open_from": 1260, "open_to": 1290}), True),
    ("避开忌口标签", ActionRequest(
        tool="search_options", arguments={"kind": "restaurant", "avoid_tags": ["hotpot"]}), True),
    ("只要好评", ActionRequest(
        tool="search_options", arguments={"kind": "board_game", "min_score": 4.5}), True),
    ("只要包间", ActionRequest(
        tool="search_options", arguments={"kind": "restaurant", "need_private_room": True}), True),
    ("排除已否决的", ActionRequest(
        tool="search_options", arguments={"kind": "restaurant", "exclude_ids": ["p004"]}), True),
    ("查不到返回空表", ActionRequest(
        tool="search_options", arguments={"kind": "restaurant", "max_price": 1}), True),
    # 取
    ("取一个选项", ActionRequest(tool="get_option", arguments={"option_id": "p003"}), True),
    ("编号不存在返回空表", ActionRequest(tool="get_option", arguments={"option_id": "p999"}), True),
    # 筛
    ("在名单里再筛", ActionRequest(
        tool="filter_options", arguments={"option_ids": ["p003", "p001"], "max_price": 40}), True),
    ("判断某时段是否合适", ActionRequest(
        tool="filter_options", arguments={"option_ids": ["p003"], "open_from": 1260, "open_to": 1290}), True),
    # 排
    ("按价格排序", ActionRequest(
        tool="rank_options", arguments={"option_ids": ["p001", "p002", "p003"], "by": "price"}), True),
    ("按评分排序", ActionRequest(
        tool="rank_options", arguments={"option_ids": ["p002", "p001"], "by": "score"}), True),
    # 换 / 附近
    ("同品类换一个", ActionRequest(tool="alternatives", arguments={"option_id": "p001"}), True),
    ("附近 15 分钟内", ActionRequest(
        tool="nearby_options", arguments={"from_option_id": "p001", "max_travel_min": 15}), True),
    # 下面这些要的是“被挡回来”，不是抛异常
    ("未知工具 -> bad_args", ActionRequest(tool="no_such_tool"), False),
    ("参数名写错 -> bad_args", ActionRequest(
        tool="search_options", arguments={"type": "board_game"}), False),
    ("kind 不合法 -> bad_args", ActionRequest(
        tool="search_options", arguments={"kind": "movie"}), False),
    ("名单里有不存在的编号 -> not_found", ActionRequest(
        tool="filter_options", arguments={"option_ids": ["p001", "p999"]}), False),
]


def main() -> None:
    places = load_places()
    pairs = len(load_travel()) // 2
    print(f"fixtures：{len(places)} 个选项；路上时间 {pairs} 对；工具 {len(TOOLS)} 个")
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
