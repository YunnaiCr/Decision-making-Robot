"""理解层提示词。

改措辞只动这个文件，不碰 orchestrator。
词表从 vocab 拼进来，别在这里手写标签——两边会漂。
"""

import json
from typing import Any

from . import vocab as v

SYSTEM = """你是抽取器：只做一件事，把群里说清楚的要求变成结构化约束。
宁可少抽，不要编。拿不准的写进 unknowns，不要替人补全。只输出 JSON。"""

RULES = f"""
任务：读一段群聊，输出 StateDelta —— 这份聊天让状态改变了什么。没提到的字段用默认值。

抽取规则
1. 说过话的人都是参与者（participants），按第一次说话的先后排。
   中途说了去不了、不参加的，不放进 participants，也不为他抽任何约束。
2. 只抽说清楚的要求。「{v.words(v.VAGUE_WORDS)}」这类等于没提，不要生成约束。
3. 每条约束都要有 evidence：直接抄原话，不要改写、不要概括。
4. 硬度（severity）：
   - 出现 {v.words(v.HARD_WORDS)}，或者带明确数字（八十以内、七点以后）→ hard
   - 出现 {v.words(v.SOFT_WORDS)}，没有上面那些 → soft
   - 都没有 → soft（拿不准的不要判 hard，hard 会直接否掉方案）
5. 时间（availability）：用分钟，从当天 0 点算起。15:00 是 900，18:00 是 1080。
   - 「七点以后才有空」→ free_start_min=1140、free_end_min=1440
   - 「六点前得走」→ free_start_min=0、free_end_min=1080
   - 只给一端时，另一端取 0 或 1440，不要留空。
   - who 填具体的人。个人时间限制漏了 who，等于把所有人都限住了。
6. 预算（budget）：人均上限填 per_person_max，总价上限填 total_max，至少填一个。
7. 忌口（diet）：avoid 用英文小写标签，能用的只有 {v.words(v.DIET_TAGS)}。
   说不出对应标签的忌口（比如「别点海鲜」），照样生成这条约束，avoid 填最接近的词
   （"seafood"），同时在 unknowns 写一句「海鲜这条忌口现有店铺标签核实不了」。
8. 活动（activity）：want 用英文小写，能用的只有 {v.words(v.ACTIVITIES)}。
   明确说要吃饭 / 玩桌游 / 看电影 / 唱歌才生成，只是暗示的不要编。
   这是这次要干的事（不是顺手一提）就判 hard。
9. 日期（date）：只有明确说了日期才填，格式 YYYY-MM-DD（例如「11 月 7 日」）。
   只说了「周六」「明天」这种相对日期，填不了就留 null——不要换算，不要猜是几号。
   没说日期当然也是 null。
10. 明确说了、但四种约束都装不下的要求（安静点、别太远、别太吵、别有烟味），
    不要硬塞进 diet 或 budget。原话写进 unknowns，让后面的人知道这条没被丢掉。
11. 同一件事后面被推翻的，以最后一条消息为准，不要为作废的说法生成约束。
12. 约束的 id 一律留空字符串，由程序统一分配。
13. 如果输入里列出了历史的约束（带 id），要作废的把 id 写进 drop_constraints；
    没有列就一律留空，不要自己编编号。
14. unknowns 只写两类东西：明确说了但装不下的要求；真的缺、会影响方案的信息。
    不要写你已经做过的判断（「所以没有生成活动约束」「他退出了所以不计入」这种），
    那是在解释自己，不是在记缺口。
"""


def _render(examples: list[dict[str, Any]]) -> str:
    """把示例拼成提示词尾巴：聊天记录 -> 输出。"""
    blocks: list[str] = []
    for i, ex in enumerate(examples, 1):
        lines = [f"例子 {i}（{ex['note']}）", "聊天记录："]
        lines += [f"  {line}" for line in ex["chat"]]
        lines.append("输出：")
        lines.append(json.dumps(ex["delta"], ensure_ascii=False, indent=2))
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks)


EXAMPLES: list[dict[str, Any]] = [
    {
        "note": "有人说没空、有人随口说都行、有装不下的要求",
        "chat": [
            "01 阿哲：这周找个晚上一起吃饭吧",
            "02 小雨：我七点以后才有空",
            "03 阿哲：那就七点半，人均八十以内",
            "04 小雨：别点海鲜，我过敏",
            "05 大刘：我都行",
            "06 阿哲：最好安静点，能聊得开",
        ],
        "delta": {
            "date": None,
            "participants": ["阿哲", "小雨", "大刘"],
            "add_constraints": [
                {
                    "kind": "activity",
                    "severity": "hard",
                    "who": [],
                    "evidence": "这周找个晚上一起吃饭吧",
                    "want": ["dinner"],
                    "id": "",
                },
                {
                    "kind": "availability",
                    "severity": "hard",
                    "who": ["小雨"],
                    "evidence": "我七点以后才有空",
                    "free_start_min": 1140,
                    "free_end_min": 1440,
                    "id": "",
                },
                {
                    "kind": "budget",
                    "severity": "hard",
                    "who": [],
                    "evidence": "人均八十以内",
                    "per_person_max": 80,
                    "total_max": None,
                    "currency": "CNY",
                    "id": "",
                },
                {
                    "kind": "diet",
                    "severity": "hard",
                    "who": ["小雨"],
                    "evidence": "别点海鲜，我过敏",
                    "avoid": ["seafood"],
                    "id": "",
                },
            ],
            "drop_constraints": [],
            "unknowns": [
                "安静、能聊得开这条要求，四种约束都装不下，需要人工判断",
                "海鲜这条忌口现有店铺标签核实不了",
            ],
        },
    },
    {
        "note": "先说的被后面推翻、有人说了去不了",
        "chat": [
            "01 小可：周六下午唱歌或者看电影都行",
            "02 阿黄：我都行，听你的",
            "03 小可：那就看电影，人均六十以内",
            "04 阿黄：啊不对，周六我要值班，你们去吧",
        ],
        "delta": {
            "date": None,
            "participants": ["小可"],
            "add_constraints": [
                {
                    "kind": "activity",
                    "severity": "hard",
                    "who": [],
                    "evidence": "那就看电影",
                    "want": ["movie"],
                    "id": "",
                },
                {
                    "kind": "budget",
                    "severity": "hard",
                    "who": [],
                    "evidence": "人均六十以内",
                    "per_person_max": 60,
                    "total_max": None,
                    "currency": "CNY",
                    "id": "",
                },
            ],
            "drop_constraints": [],
            "unknowns": [],
        },
    },
]

RULES = RULES + "\n" + _render(EXAMPLES) + "\n"
