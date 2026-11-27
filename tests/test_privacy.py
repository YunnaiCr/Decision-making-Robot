"""隐私回归测试：PII 打码覆盖三条出口，仓库里不许出现个人信息。"""

import json
import re
import subprocess
from pathlib import Path

import pytest

from group_agent import privacy
from group_agent.minutes import build_facts
from group_agent.models import Budget, ChatLog, FinalPlan, GroupState, Slot
from group_agent.orchestrator import format_chat, format_state

ROOT = Path(__file__).resolve().parent.parent

PHONE = "13800138000"
ID_CARD = "110101199003071234"
EMAIL = "someone@example.com"
CARD = "6222021234567890123"


# --- 规则本身 ------------------------------------------------------------


@pytest.mark.parametrize(
    "raw,expected",
    [
        (f"我电话 {PHONE}", "我电话 138****8000"),
        (f"身份证 {ID_CARD}", "身份证 110101********1234"),
        (f"邮箱 {EMAIL}", "邮箱 <email>"),
        (f"卡号 {CARD}", "卡号 6222********0123"),
    ],
)
def test_redacts_common_pii(raw, expected):
    assert privacy.redact(raw) == expected


def test_does_not_touch_numbers_we_need():
    """时间和价格不能被误伤，否则规划就废了。"""
    for text in ("15:00 到 17:30，人均 45 元", "2026-11-21 周六", "一共 249 元"):
        assert privacy.redact(text) == text


def test_can_be_disabled(monkeypatch):
    monkeypatch.setenv("PRIVACY_REDACT", "0")
    assert privacy.redact(f"电话 {PHONE}") == f"电话 {PHONE}"


# --- 三条出口 ------------------------------------------------------------


def make_chat() -> ChatLog:
    return ChatLog.model_validate(
        {
            "chat_id": "t1",
            "messages": [
                {"i": 1, "who": "小明", "min": 0, "text": f"我的号 {PHONE}，周六聚"},
                {"i": 2, "who": "小王", "min": 1, "text": "行"},
            ],
        }
    )


def test_format_chat_masks_pii():
    text = format_chat(make_chat())
    assert PHONE not in text
    assert "138****8000" in text


def test_format_state_masks_constraint_evidence():
    state = GroupState(
        participants=["小明"],
        constraints=[
            Budget(
                id="c1",
                severity="hard",
                evidence=f"小明：预算按我电话 {PHONE} 说的来",
                per_person_max=100,
            )
        ],
    )
    text = format_state(state)
    assert PHONE not in text
    assert "138****8000" in text


def test_minutes_facts_mask_pii():
    plan = FinalPlan(status="ok", participants=["小明"], slots=[])
    state = GroupState(unknowns=[f"再问一下 {EMAIL}"])
    facts = build_facts(make_chat(), plan, state)

    assert PHONE not in json.dumps(facts, ensure_ascii=False)
    assert EMAIL not in json.dumps(facts, ensure_ascii=False)


# --- 仓库里不许有真实个人信息 --------------------------------------------


# 扫描时排除 tests/：测试文件本身就带着这些样例号码
PII_PATTERNS = (
    re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)"),          # 手机号
    re.compile(r"(?<!\d)\d{17}[\dXx](?!\d)"),          # 身份证
    re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}"),  # 邮箱
)


def tracked_files() -> list[Path]:
    result = subprocess.run(["git", "ls-files"], cwd=ROOT, capture_output=True, text=True, check=True)
    return [ROOT / name for name in result.stdout.splitlines() if not name.startswith("tests/")]


@pytest.mark.parametrize("pattern", PII_PATTERNS, ids=["phone", "id-card", "email"])
def test_repo_has_no_personal_data(pattern):
    hits = []
    for path in tracked_files():
        if not path.exists() or path.suffix not in {".py", ".md", ".json", ".txt", ".yml", ".yaml"}:
            continue
        for number, line in enumerate(path.read_text(encoding="utf-8", errors="ignore").splitlines(), 1):
            if pattern.search(line):
                hits.append(f"{path.relative_to(ROOT)}:{number}")
    assert not hits, "仓库里出现疑似个人信息: " + ", ".join(hits)


def test_gitignore_covers_private_chats():
    text = (ROOT / ".gitignore").read_text(encoding="utf-8")
    assert "private/" in text
    assert "*.chat.txt" in text
