"""连通性自检：发一次最小请求，确认 key、模型、参数都正常。"""

import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from group_agent import llm


def main() -> None:
    _, base, model = llm.settings()
    key = os.environ["LLM_API_KEY"]
    print("base_url :", base)
    print("model    :", model)
    print("extra    :", os.environ.get("LLM_EXTRA_JSON", "(无)"))
    print()

    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": "你只输出 JSON，不要解释。"},
            {"role": "user", "content": "输出一个 JSON 对象，内容为 ok 等于 true"},
        ],
        "response_format": {"type": "json_object"},
    }
    payload.update(llm.optional_params())

    started = time.monotonic()
    try:
        response = llm.post(base + "/chat/completions", payload, key)
    except Exception as exc:
        print("失败:", type(exc).__name__)
        print(str(exc)[:400])
        raise SystemExit(1)

    ms = int((time.monotonic() - started) * 1000)
    usage = response.get("usage", {})
    think = usage.get("completion_tokens_details", {}).get("reasoning_tokens", 0)
    print("成功 |", ms, "ms | 输出", usage.get("completion_tokens", 0), "| 思考", think)
    print("内容:", str(response["choices"][0]["message"].get("content"))[:120])


if __name__ == "__main__":
    main()
