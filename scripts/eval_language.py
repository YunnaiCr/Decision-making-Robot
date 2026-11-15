"""对着真模型跑一遍语言层：抽取 → 主循环 → 措辞检查。

用法（Windows）：
    .venv\\Scripts\\python.exe scripts\\eval_language.py
    .venv\\Scripts\\python.exe scripts\\eval_language.py fixtures\\chats\\e01.json

它不判分，只把三个节点的真实输出摊开给人看：
- 抽取出来的 StateDelta 和它落成的状态
- 每一轮规划层的决定（含被打回的原因）
- 最终那句话，以及 lint_message 查出来的问题

没有 key 就退出码 2。CI 不跑这个脚本，tests/ 里也不许联网。
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from group_agent import llm
from group_agent.models import ChatLog, GroupState
from group_agent.orchestrator import (
    apply_delta,
    express as express_call,
    extract,
    format_state,
    plan,
    plan_facts,
    run,
)
from group_agent.prompts.express import lint_message


def main() -> None:
    path = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("fixtures/chats/e01.json")
    chat = ChatLog.model_validate_json(path.read_text(encoding="utf-8"))

    try:
        _, _, model = llm.settings()
    except llm.LLMError as exc:
        print("跳过：", exc)
        raise SystemExit(2)

    print("聊天：", path, "| 模型：", model)

    # 1. 抽取（只调一次，后面主循环复用这份状态）
    delta = extract(chat)
    print("\n===== 理解层输出 =====")
    print(json.dumps(delta.model_dump(exclude_none=True), ensure_ascii=False, indent=2))

    state = apply_delta(GroupState(goal=chat.title), delta)
    print("\n===== 落成状态（规划层看到的就是这个）=====")
    print(format_state(state))

    # 2. 主循环：每一轮的决定照原样打出来
    captured: dict[str, dict] = {}

    def spy_plan(current: GroupState):
        decision = plan(current)
        print("\n--- 决定 ---")
        print(json.dumps(decision.model_dump(exclude_none=True), ensure_ascii=False, indent=2))
        return decision

    def spy_express(current: GroupState, draft, result):
        captured["facts"] = plan_facts(current, draft)
        captured["unverified"] = [v.detail for v in result.undecided]
        return express_call(current, draft, result)

    def reuse(_chat: ChatLog) -> GroupState:
        return state

    print("\n===== 主循环 =====")
    try:
        plan_out = run(chat, understand_fn=reuse, plan_fn=spy_plan, express_fn=spy_express)
    except Exception as exc:  # 超步、格式错都会走到这里
        print("\n主循环抛异常：", type(exc).__name__, exc)
        raise SystemExit(1)

    # 3. 收尾
    print("\n===== 最终输出 =====")
    print(plan_out.model_dump_json(indent=2, exclude_none=True))

    if captured:
        facts = {**captured["facts"], "unverified": captured["unverified"]}
        print("\n===== 交给人看的事实 =====")
        print(json.dumps(facts, ensure_ascii=False, indent=2))
        problems = lint_message(facts, plan_out.message)
        print("\n===== 措辞检查 =====")
        print("通过" if not problems else "\n".join(f"- {p}" for p in problems))


if __name__ == "__main__":
    main()
