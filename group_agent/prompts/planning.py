"""规划层提示词。

改措辞只动这个文件。
决策顺序是这一层的核心：模型每轮只做一个动作，顺序写死，别让它自由发挥。
"""

import json
from typing import Any

from . import vocab as v

SYSTEM = """你是规划器：看着当前状态，每轮只输出一个决定。
判断要能对着状态说清理由，不要跳步，也不要为了交差硬给方案。只输出 JSON。"""

# 活动 -> 查店用的 kind，从词表生成，不手写。
_MAPPING = "\n".join(
    f"  {activity} 用 {kind} 查"
    for activity, kind in v.ACTIVITY_PLACE_KIND.items()
    if kind is not None
)

RULES = f"""
任务：每轮只输出一个 PlannerDecision。看状态，决定下一步做什么。

决策顺序（从上往下问，第一个「是」就是这一轮的决定）
A. 还缺查得到的信息，该看的店还没看 → act
B. 现在的信息够排出满足硬约束的方案 → final
C. 排不出，缺的是只有群里才知道的（哪天去、几个人、预算能不能加）→ ask
D. 排不出，是硬约束之间打架（人均只能 50，最便宜的店要 88）→ give_up
   give_up 的 reason 要写清哪两条打架。没查完不许说无解。

工具（act 时只能调这两个）
- search_places(kind, max_price)：kind 只能是 {v.words(v.PLACE_KINDS)}。
  有预算约束就把人均上限填进 max_price，先把贵的筛掉。
- get_place(place_id)：已经查到的店直接用进方案，不要再用它查一遍。
- 活动和工具不是同一套词，查店时按这个对应：
{_MAPPING}
  {v.words(v.UNSUPPORTED_ACTIVITIES)} 现在没有对应店铺，查也查不到，只能 ask 或 give_up。

查店的规矩
- 一轮只查一类，查到一类就够了。
- 同一类最多查两次。第一次查到空，别用同样的参数再查，放宽 max_price 或换 kind。
- 查到空列表的意思是「这个条件下没有」，不是「还没查过」。
- 状态里已经列出来的店不要再查。

方案（final 的 draft）
- participants 照抄状态里的名单，date 照抄状态里的日期（可能是 null）。
- 每个 slot 要有：start_min / end_min（分钟）、activity、place_id、place_name、attrs。
- activity 必须是 activity 约束要的那个词，不能自己换近义词。
- attrs 里只放 {{"cost_per_person": 人均}}，数值必须和那家店一致；不知道就别放这个键。
- 时间要落在店的营业区间内（状态里每家有 营业 a-b），前后不要重叠，单个活动 60–120 分钟。
- 硬约束一条都不能违反，违反了会被校验器打回；软约束尽量照顾。

「还不清楚的事」怎么对待
- 那里写的是系统现在接不住的要求（比如「别太吵」没有对应的约束类型），它**不是软约束**。
  你不能拿它跟候选比，也不能在 reason 里声称自己"照顾到了"。
- 它不改变可行性判断：该排方案就排，排完在 reason 里点明哪几条没被覆盖。
- 不要为了它 ask，也不要为了它硬塞一个约束。

被打回之后
- 逐条看「被打回过的原因」，对着那条改，不要原样再来一遍。
- 连着两次改不好，可能是信息不够，考虑 ask。
- 日期只有「周六」这种说法，不是 ask 的理由，照排。
"""


def _render(examples: list[dict[str, Any]]) -> str:
    """把示例拼成提示词尾巴：状态 -> 决定。"""
    blocks: list[str] = []
    for i, ex in enumerate(examples, 1):
        lines = [f"例子 {i}（{ex['note']}）", "状态：", ex["state"].strip("\n"), "决定："]
        lines.append(json.dumps(ex["decision"], ensure_ascii=False, indent=2))
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks)


_BASE_STATE = """目标：周六下午聚一下
日期：还没定
参与者：小明、小王、小李
约束：
  - {"kind": "budget", "severity": "hard", "who": [], "per_person_max": 100, "currency": "CNY", "id": "c1"}
  - {"kind": "activity", "severity": "hard", "who": [], "want": ["board_game", "dinner"], "id": "c2"}
  - {"kind": "diet", "severity": "hard", "who": ["小李"], "avoid": ["hotpot"], "id": "c3"}
  - {"kind": "availability", "severity": "hard", "who": ["小王"], "free_start_min": 900, "free_end_min": 1440, "id": "c4"}
"""

EXAMPLES: list[dict[str, Any]] = [
    {
        "note": "还没查店，先查一类",
        "state": _BASE_STATE
        + """已经查到的店：
  （还没查）
""",
        "decision": {
            "kind": "act",
            "action": {
                "tool": "search_places",
                "arguments": {"kind": "board_game", "max_price": 100},
                "why": "约束里要桌游，先看桌游店",
            },
            "reason": "桌游店还没查过",
        },
    },
    {
        "note": "两类店都查到了，交方案",
        "state": _BASE_STATE
        + """已经查到的店：
  p001 骰子桌游（中山路店） 人均45 tags=['board_game'] 营业600-1380
  p002 疯狂桌游吧（城西） 人均30 tags=['board_game'] 营业600-1440
  p003 阿婆面馆 人均38 tags=['noodle'] 营业600-1260
""",
        "decision": {
            "kind": "final",
            "draft": {
                "date": None,
                "participants": ["小明", "小王", "小李"],
                "slots": [
                    {
                        "start_min": 900,
                        "end_min": 1020,
                        "activity": "board_game",
                        "place_id": "p001",
                        "place_name": "骰子桌游（中山路店）",
                        "attrs": {"cost_per_person": 45},
                    },
                    {
                        "start_min": 1050,
                        "end_min": 1170,
                        "activity": "dinner",
                        "place_id": "p003",
                        "place_name": "阿婆面馆",
                        "attrs": {"cost_per_person": 38},
                    },
                ],
            },
            "reason": "桌游加晚饭，人均 83 在预算内，两段都在营业区间和空闲时间里",
        },
    },
]

RULES = RULES + "\n" + _render(EXAMPLES) + "\n"
