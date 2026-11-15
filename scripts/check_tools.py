"""工具可用性自检：每个工具都真跑一遍，打印结果。

改完 tools.py 就跑一次，不联网、不需要 key：

    python scripts/check_tools.py

全过退出 0，有失败退出 1。
"""

import json
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
        return f"{data.place_id} {data.name}"
    if isinstance(data, dict):
        return json.dumps(data, ensure_ascii=False)[:110]
    return str(data)


# (说明, 请求, 这次调用应该成功吗)
CASES = [
    # 先看世界
    ("世界里有什么", ActionRequest(tool="catalog"), True),
    # 候选类
    ("跨品类查（不给 category）", ActionRequest(tool="search_options", arguments={}), True),
    ("按自由品类查", ActionRequest(tool="search_options", arguments={"category": "movie"}), True),
    ("按标签查", ActionRequest(tool="search_options", arguments={"tags": ["hotpot"]}), True),
    ("按属性查", ActionRequest(tool="search_options", arguments={"attrs": {"noise": "low"}}), True),
    ("价格上限", ActionRequest(tool="search_options", arguments={"max_price": 45}), True),
    ("营业时段", ActionRequest(
        tool="search_options", arguments={"open_from": 1260, "open_to": 1290}), True),
    ("评分下限", ActionRequest(tool="search_options", arguments={"min_score": 4.5}), True),
    ("排除已否决的", ActionRequest(
        tool="search_options", arguments={"category": "board_game", "exclude_ids": ["p001"]}), True),
    ("查不到返回空表", ActionRequest(tool="search_options", arguments={"category": "coding"}), True),
    ("取一个候选", ActionRequest(tool="get_option", arguments={"option_id": "p006"}), True),
    ("编号不存在返回空表", ActionRequest(tool="get_option", arguments={"option_id": "p999"}), True),
    ("在名单里再筛", ActionRequest(
        tool="filter_options", arguments={"option_ids": ["p003", "p001"], "max_price": 40}), True),
    ("判断某时段是否合适", ActionRequest(
        tool="filter_options", arguments={"option_ids": ["p003"], "open_from": 1260, "open_to": 1290}), True),
    ("按价格排序", ActionRequest(
        tool="rank_options", arguments={"option_ids": ["p001", "p002", "p003"], "by": "price"}), True),
    ("按评分排序", ActionRequest(
        tool="rank_options", arguments={"option_ids": ["p002", "p001"], "by": "score"}), True),
    ("同品类换一个", ActionRequest(tool="alternatives", arguments={"option_id": "p001"}), True),
    ("附近 15 分钟内", ActionRequest(
        tool="nearby_options", arguments={"from_option_id": "p001", "max_travel_min": 15}), True),
    # 事实类
    ("营业判断", ActionRequest(
        tool="check_open", arguments={"option_id": "p003", "start_min": 1200, "end_min": 1260}), True),
    ("路上时间", ActionRequest(
        tool="travel_time", arguments={"from_option_id": "p001", "to_option_id": "p003"}), True),
    ("预算试算", ActionRequest(
        tool="estimate_cost", arguments={"option_ids": ["p001", "p003"], "headcount": 3}), True),
    ("预算试算缺价", ActionRequest(
        tool="estimate_cost", arguments={"option_ids": ["p001", "p007"]}), True),
    # 下面这些要的是“被挡回来”，不是抛异常
    ("未知工具 -> bad_args", ActionRequest(tool="no_such_tool"), False),
    ("参数名写错 -> bad_args", ActionRequest(
        tool="search_options", arguments={"type": "board_game"}), False),
    ("名单里有不存在的编号 -> not_found", ActionRequest(
        tool="filter_options", arguments={"option_ids": ["p001", "p999"]}), False),
]


def main() -> None:
    options = load_places()
    pairs = len(load_travel()) // 2
    print(f"fixtures：{len(options)} 个候选；路上时间 {pairs} 对；工具 {len(TOOLS)} 个")
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
