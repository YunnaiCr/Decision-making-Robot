"""语言层共用的受控词表。

为什么单独放一个文件
- 模型写出来的标签必须和下游对得上：`avoid` 要跟 `Candidate.tags` 求交集，
  `activity` 要和 `Slot.activity` 精确相等。词表散在提示词里，两边迟早对不上。
- 所以提示词里的词表是从这里拼进去的（f-string），测试也拿它去对 fixtures。
  改词表只有一处，提示词和测试自动跟着变。

词的来源是 `fixtures/places.json`，不是想象。加一个标签的顺序是：
先有店的标签（B 那边）→ 再进这里 → 提示词自动带上。

约定
- 活动和店铺类型是两套词：方案里写 dinner，查店用 restaurant。
- 校验器目前只认这几个；写别的，一定对不上，等于没查。
"""

# 工具能查的店铺类型，对应 Candidate.kind 和 search_places(kind=...)
PLACE_KINDS = ("board_game", "restaurant")

# 方案里的活动名，对应 Slot.activity 和 Activity.want
ACTIVITIES = ("board_game", "dinner", "movie", "ktv")

# 活动 -> 用哪个 kind 去查店。None 表示现在没有对应店铺，查也查不到。
ACTIVITY_PLACE_KIND: dict[str, str | None] = {
    "board_game": "board_game",
    "dinner": "restaurant",
    "movie": None,
    "ktv": None,
}

# 查不到对应店铺的活动。规划层看到这些只能 ask 或 give_up。
UNSUPPORTED_ACTIVITIES = tuple(a for a, k in ACTIVITY_PLACE_KIND.items() if k is None)

# 忌口标签，对应 Candidate.tags 里能吃出来的部分。
# 只有这几个！「海鲜」这类不在表里的，模型要照样记约束，但要写进 unknowns。
DIET_TAGS = ("hotpot", "spicy", "noodle", "chinese")

# 店的 tags 能出现的全部标签，测试拿它卡 fixtures/places.json。
PLACE_TAGS = DIET_TAGS + ("board_game",)

# 硬度：出现这些词判 hard
HARD_WORDS = ("必须", "只能", "不行", "别", "不要", "一定", "不许", "不可以")

# 出现这些词、且没有上面那些，判 soft
SOFT_WORDS = ("最好", "尽量", "想", "希望", "愿意", "可以")

# 这些词等于没提要求：不生成约束
VAGUE_WORDS = ("都行", "随便", "看你们", "无所谓", "听你的")


def words(values: tuple[str, ...]) -> str:
    """把词表拼成提示词里的一句话：a、b、c。"""
    return "、".join(values)
