"""语言层的常见叫法映射；不是活动、品类或标签的允许清单。"""

VERSION = "2.0"

# 映射只是查询提示，不证明数据中有或没有某个品类。
# activity 用于 Activity.want / Slot.activity；category 以实际候选和 catalog 为准。
ACTIVITY_CATEGORY_HINTS = {
    "board_game": "board_game", "dinner": "restaurant", "movie": "movie", "ktv": "ktv",
}
ACTIVITY_ALIASES = {
    "吃饭": "dinner", "晚饭": "dinner", "桌游": "board_game",
    "看电影": "movie", "唱歌": "ktv", "逛公园": "park",
}

# 保留相同语义，不代表候选已经被证明不含某种成分。
DIET_ALIASES = {"火锅": "hotpot", "辣": "spicy", "海鲜": "seafood", "花生": "peanut"}


def render_aliases(aliases: dict[str, str]) -> str:
    """把常见叫法作为示例展示，不依赖本地 fixtures。"""
    return "、".join(f"{source} -> {target}" for source, target in aliases.items())
