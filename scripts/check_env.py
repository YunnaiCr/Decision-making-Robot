"""安全地看一眼 `.env`：只报「有没有值」，绝不打印值。

为什么要单独写一个：之前用 `sed 's/=.*/=***/'` 打码，只处理了 `KEY=值`，
被注释掉的 `# AMAP_KEY=xxxx` 会原样漏出来。这个脚本连注释行也只看键名。

    python scripts/check_env.py
"""

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

ENV = Path(__file__).resolve().parent.parent / ".env"
EXPECTED = ("LLM_API_KEY", "LLM_BASE_URL", "LLM_MODEL", "AMAP_KEY")

# 这些键的值可能是密钥，只报长度，不报内容
SECRET_HINTS = ("KEY", "TOKEN", "SECRET", "PASSWORD")


def parse(path: Path) -> tuple[dict[str, str], set[str]]:
    """返回 (生效的键值, 被注释掉的键)。值只在内存里用于量长度，不打印。"""
    values: dict[str, str] = {}
    commented: set[str] = set()

    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line:
            continue
        if line.startswith("#"):
            match = re.match(r"#\s*([A-Za-z_]+)=", line)
            if match:
                commented.add(match.group(1))
            continue
        if "=" in line:
            name, value = line.split("=", 1)
            values[name.strip()] = value.strip()

    return values, commented


def describe(name: str, values: dict[str, str], commented: set[str]) -> str:
    if name in values:
        value = values[name]
        if not value:
            return "未设置（等号后面是空的）"
        if any(hint in name.upper() for hint in SECRET_HINTS):
            return f"已设置（{len(value)} 个字符）"
        return f"已设置（{value}）"  # 非密钥的项（base_url、model）可以直接看
    if name in commented:
        return "被注释掉了（等于没设，把行首的 # 去掉）"
    return "文件里没有这一行"


def main() -> None:
    if not ENV.exists():
        print(f"没有 {ENV}")
        raise SystemExit(1)

    values, commented = parse(ENV)

    print(f"文件: {ENV}")
    print()
    for name in EXPECTED:
        print(f"  {name:<14} {describe(name, values, commented)}")

    missing = [name for name in EXPECTED if not values.get(name)]
    print()
    print("都填好了" if not missing else "还没填: " + ", ".join(missing))


if __name__ == "__main__":
    main()
