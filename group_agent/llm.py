"""唯一的模型调用出口。换厂商、换模型只改这个文件。"""

import json
import os
import time
import urllib.error
import urllib.request
from pathlib import Path
from dataclasses import dataclass
from typing import Any, Callable

DEFAULT_BASE_URL = "https://api.deepseek.com"
DEFAULT_MODEL = "deepseek-chat"
TIMEOUT_S = 60

Transport = Callable[[str, dict, str], dict]


class LLMError(RuntimeError):
    """模型调用失败。"""


@dataclass
class LLMResult:
    data: dict[str, Any]
    tokens: int
    ms: int
    cached: int = 0


def settings() -> tuple[str, str, str]:
    """读环境变量。没配 key 直接报错，不要静默。"""
    key = os.environ.get("LLM_API_KEY", "").strip()
    base = os.environ.get("LLM_BASE_URL", DEFAULT_BASE_URL).rstrip("/")
    model = os.environ.get("LLM_MODEL", DEFAULT_MODEL)
    if not key:
        raise LLMError("没配 LLM_API_KEY：把 .env.example 复制成 .env 并填上")
    return key, base, model


def build_prompt(user: str, schema: dict) -> str:
    """把 schema 拼进提示词，让模型按格式填。"""
    schema_text = json.dumps(schema, ensure_ascii=False, indent=2)
    return (
        "只输出一个 JSON 对象，不要解释、不要代码块。必须符合下面的 schema：\n"
        f"{schema_text}\n\n"
        "以下是输入：\n"
        f"{user}"
    )


def post(url: str, payload: dict, key: str) -> dict:
    """真的发 HTTP 请求。测试时会换成假的。"""
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {key}",
        },
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=TIMEOUT_S) as response:
        return json.loads(response.read().decode("utf-8"))


def optional_params() -> dict[str, Any]:
    """可选请求参数：输出上限 + 透传字段（试参数用，不写死）。"""
    extra: dict[str, Any] = {}

    temperature = os.environ.get("LLM_TEMPERATURE", "").strip()
    if temperature:
        extra["temperature"] = float(temperature)

    max_tokens = os.environ.get("LLM_MAX_TOKENS", "").strip()
    if max_tokens:
        extra["max_tokens"] = int(max_tokens)

    raw = os.environ.get("LLM_EXTRA_JSON", "").strip()
    if raw:
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise LLMError(f"LLM_EXTRA_JSON 不是合法 JSON: {exc}") from exc
        if not isinstance(parsed, dict):
            raise LLMError("LLM_EXTRA_JSON 必须是一个 JSON 对象")
        extra.update(parsed)

    return extra


def complete_json(
    system: str,
    user: str,
    schema: dict,
    model: str | None = None,
    transport: Transport | None = None,
    retries: int = 1,
) -> LLMResult:
    """发一次请求，拿回一个 dict。不负责校验类型，那是调用方的事。"""
    key, base, default_model = settings()
    send = transport or post
    last_error = ""

    for _ in range(retries + 1):
        payload = {
            "model": model or default_model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": build_prompt(user, schema)},
            ],
            "response_format": {"type": "json_object"},
        }
        payload.update(optional_params())
        started = time.monotonic()
        try:
            response = send(base + "/chat/completions", payload, key)
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            last_error = f"网络错误: {exc}"
            continue

        ms = int((time.monotonic() - started) * 1000)
        try:
            content = response["choices"][0]["message"]["content"]
            data = json.loads(content)
        except (KeyError, IndexError, TypeError, json.JSONDecodeError) as exc:
            last_error = f"模型返回的不是 JSON: {exc}"
            continue

        if not isinstance(data, dict):
            last_error = "模型返回的不是对象"
            continue

        usage = response.get("usage", {})
        tokens = int(usage.get("total_tokens", 0))
        cached = int(usage.get("prompt_cache_hit_tokens", 0))
        if not cached:
            cached = int(usage.get("prompt_tokens_details", {}).get("cached_tokens", 0))
        return LLMResult(data=data, tokens=tokens, ms=ms, cached=cached)

    raise LLMError(f"调了 {retries + 1} 次都失败：{last_error}")


def load_dotenv(path: Path | None = None) -> None:
    """读仓库根的 .env，只补没设过的变量。不引第三方依赖。"""
    env_path = path or Path(__file__).resolve().parent.parent / ".env"
    if not env_path.exists():
        return
    for raw in env_path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, value = line.split("=", 1)
        name = name.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in chr(34) + chr(39):
            value = value[1:-1]
        if name and name not in os.environ:
            os.environ[name] = value


load_dotenv()

