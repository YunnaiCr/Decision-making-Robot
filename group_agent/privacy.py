"""把用户输入里的个人身份信息（PII）打码，避免它们离开这台机器。

聊天记录里最常见的是手机号、身份证号、邮箱、银行卡号。这些对「排一次活动」
没有用，但它们会跟着三条路出去：

1. `format_chat` —— 聊天原文发给理解层
2. `format_state` —— 约束里的 evidence 是原话引用，发给规划层
3. `minutes.build_facts` —— 纪要的事实里带聊天原文

所以统一在**离开进程之前**打码：`redact()` 在纯字符串上做替换，
不改任何类型，也不影响校验器（校验器只看结构化的时间和价格）。

默认开启。调试时可以 `PRIVACY_REDACT=0` 关掉，但别在生产里关。
"""

import os
import re

# (正则, 替换)。顺序有讲究：先长后短，别让身份证被手机号规则切开。
_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    # 18 位身份证：保留前 6 位和后 4 位（地区码和校验位，便于核对）
    (re.compile(r"(?<!\d)(\d{6})\d{8}(\d{3}[\dXx])(?!\d)"), r"\1********\2"),
    # 手机号：保留前 3 位和后 4 位
    (re.compile(r"(?<!\d)(1[3-9]\d)\d{4}(\d{4})(?!\d)"), r"\1****\2"),
    # 银行卡：16-19 位，保留前 4 后 4
    (re.compile(r"(?<!\d)(\d{4})\d{8,11}(\d{4})(?!\d)"), r"\1********\2"),
    # 邮箱：整个换掉，长度信息都不留
    (re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}"), "<email>"),
)

_OFF = {"0", "false", "no", "off"}


def enabled() -> bool:
    """默认开启；只有显式写 PRIVACY_REDACT=0/false/no/off 才关。"""
    return os.environ.get("PRIVACY_REDACT", "1").strip().lower() not in _OFF


def redact(text: str) -> str:
    """把 PII 打码。空字符串和非字符串原样返回。"""
    if not text or not enabled():
        return text
    for pattern, replacement in _PATTERNS:
        text = pattern.sub(replacement, text)
    return text
