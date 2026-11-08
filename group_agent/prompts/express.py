"""表达层提示词：把结构化方案改写成群里能发的话。

除了提示词，这里还有一个纯函数 `lint_message`：
模型写的话对不对，不该靠再问一遍模型（见 ARCHITECTURE 第 3 条），能用代码查的就用代码查。
它现在只给测试和 scripts/eval_language.py 用；接进 orchestrator.express() 需要动主循环。
"""

import json
import re
from typing import Any

SYSTEM = """你把结构化方案改写成一句群里能发的话。
只能表述给定的事实，不许新增，不许承诺。只输出 JSON。"""

RULES = """
写作规则
1. 只能用输入里给出的事实：日期、参与者、活动、地点、起止时间、人均和总价、未核实项。
2. 不许新增输入里没有的信息。不要承诺、不要猜：不用排队、老板人很好、环境安静，都算编。
3. 数字必须照抄输入：人均、总价、人数、日期。时间可以写成「下午三点」这种说法，
   但必须和输入里的时刻一致；拿不准就照抄 15:00。
4. 费用是 null 就不要提钱；unverified 非空时用一句话提醒，不要展开。
5. 像群里说话：一句话为主，最多三句；确有未核实项时，可以再加一句提醒（总共不超过 4 句）。
   不分点、不换行、不加标题和表情。
6. date 为 null 时不要说具体哪天。
7. 不要自称「我」做承诺；用「我们」指代参加的人。
"""

EXAMPLE = {
    "facts": {
        "date": None,
        "participants": ["小明", "小王", "小李"],
        "slots": [
            {
                "activity": "board_game",
                "place": "骰子桌游（中山路店）",
                "start": "15:00",
                "end": "17:00",
                "cost_per_person": 45,
            },
            {
                "activity": "dinner",
                "place": "阿婆面馆",
                "start": "17:30",
                "end": "19:30",
                "cost_per_person": 38,
            },
        ],
        "per_person": 83,
        "total_cost": 249,
        "unverified": [],
    },
    "message": "那就这么定：下午三点先去骰子桌游，五点半到阿婆面馆，三个人人均 83，一共 249。",
}

_RENDERED = (
    "例子（输入 -> 输出）\n"
    "事实：\n"
    + json.dumps(EXAMPLE["facts"], ensure_ascii=False, indent=2)
    + "\n输出：\n"
    + json.dumps({"message": EXAMPLE["message"]}, ensure_ascii=False, indent=2)
    + "\n\n反例（这样写会被打回）\n"
    "-「不用排队」「老板人很好」「环境安静」：输入里没有这些事实。\n"
    "-「人均一百左右」：输入是 83，数字要照抄。\n"
    "-「我们肯定准时到」：这是承诺，不是方案里的事实。\n"
    "- 分点、换行、emoji、小标题。\n"
)

RULES = RULES + "\n" + _RENDERED


# ---- 措辞检查：纯函数，不调模型 ----

_DIGITS = re.compile(r"\d+(?:\.\d+)?")
_SENTENCE = re.compile(r"[。！？!?]")
_EMPTY_TOKENS = ("None", "null", "NaN", "undefined")

# 正文最多三句，有未核实项时再加一句提醒。和 RULES 第 5 条是同一个数。
MAX_SENTENCES = 4


def facts_digits(facts: dict[str, Any]) -> set[str]:
    """事实里出现过的所有数字。人数和人均这类「算得出来但没写进字段」的也要算上。"""
    known = set(_DIGITS.findall(json.dumps(facts, ensure_ascii=False)))
    headcount = len(facts.get("participants") or [])
    if headcount:
        known.add(str(headcount))
    return known


def lint_message(
    facts: dict[str, Any], message: str, max_sentences: int = MAX_SENTENCES
) -> list[str]:
    """查一句话有没有编造。返回问题列表，空列表表示没查出问题。

    能查的只有这几样，查不出「语气像不像人」：
    - 数字（含日期、时间、金额）必须是事实里出现过的
    - 不许出现 None / null 这类空值
    - 不许换行、不许超过 max_sentences 句
    """
    problems: list[str] = []
    text = message.strip()
    if not text:
        return ["消息是空的"]
    for token in _EMPTY_TOKENS:
        if token in text:
            problems.append(f"出现了空值 {token}")
    known = facts_digits(facts)
    for token in _DIGITS.findall(text):
        if token not in known:
            problems.append(f"数字 {token} 不在给定事实里")
    if "\n" in text:
        problems.append("不要换行")
    if len(_SENTENCE.findall(text)) > max_sentences:
        problems.append(f"超过 {max_sentences} 句")
    return problems
