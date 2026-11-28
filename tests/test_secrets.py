"""防止密钥再进仓库的护栏。

三件事：
1. 工作区里不许出现像密钥的字符串（sk-…、32 位十六进制）
2. `.env` 不许被 git 跟踪
3. 错误信息里的 URL 必须被打码（key 通常藏在 query 里）
"""

import re
import subprocess
from pathlib import Path

import pytest

from group_agent.sources import _redact

ROOT = Path(__file__).resolve().parent.parent

# 只扫源码文本，不扫 .venv / .git / .env
TEXT_SUFFIXES = {".py", ".md", ".json", ".txt", ".yml", ".yaml", ".toml", ".cfg", ".ini"}
SKIP_DIRS = {".git", ".venv", "__pycache__", ".pytest_cache", "node_modules"}

# sk- 开头的密钥、32 位十六进制（高德 key 的形状）
SECRET_PATTERNS = (
    re.compile(r"\bsk-[A-Za-z0-9]{16,}"),
    re.compile(r"\b[0-9a-f]{32}\b"),
)


def tracked_text_files() -> list[Path]:
    files: list[Path] = []
    for path in ROOT.rglob("*"):
        if not path.is_file() or path.suffix not in TEXT_SUFFIXES:
            continue
        if any(part in SKIP_DIRS for part in path.parts):
            continue
        if path.name == ".env":
            continue
        files.append(path)
    return files


@pytest.mark.parametrize("pattern", SECRET_PATTERNS, ids=["sk-key", "hex32"])
def test_no_secret_looking_strings_in_the_tree(pattern):
    hits = []
    for path in tracked_text_files():
        for number, line in enumerate(path.read_text(encoding="utf-8", errors="ignore").splitlines(), 1):
            if pattern.search(line):
                hits.append(f"{path.relative_to(ROOT)}:{number}")
    assert not hits, "疑似密钥出现在这些位置: " + ", ".join(hits)


def test_env_is_not_tracked():
    result = subprocess.run(
        ["git", "ls-files", "--error-unmatch", ".env"],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    assert result.returncode != 0, ".env 被 git 跟踪了，赶紧把它从索引里删掉"


def test_url_key_is_redacted():
    url = "https://restapi.amap.com/v3/place/text?keywords=x&key=abcdef123456&page=1"
    cleaned = _redact(f"HTTP Error 401: {url}")
    assert "abcdef123456" not in cleaned
    assert "key=***" in cleaned
