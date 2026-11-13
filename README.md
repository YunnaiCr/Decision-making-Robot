# 群聊活动规划 Agent

输入一段群聊记录，输出一份活动安排和一句能发回群里的话。

## 安装

```bash
cd ~/group-agent
uv venv --python python3 .venv
uv pip install --python .venv/bin/python pydantic pytest
```

## 配置

```bash
cp .env.example .env
```

在 `.env` 里填 `LLM_API_KEY`，其余项可以留空。

## 运行

```bash
.venv/bin/python -m group_agent fixtures/chats/e01.json
```

输出是一段 JSON，两块：

**`plan`** —— 活动方案

- `status`：`ok` 有方案 / `needs_input` 需要补充信息 / `infeasible` 做不到
- `slots`：时间、活动、地点、每人费用
- `total_cost`：总花费，查不到价格时为 `null`
- `message`：可以直接发到群里的话
- `questions`：`needs_input` 时具体要问什么

**`minutes`** —— 纪要和待办

- `participants`：实际参加的人
- `decisions`：已经拍板的事
- `open_questions`：还没定、要有人回答的事
- `todos`：谁、做什么、截止（没说就 `null`）、原话依据

`minutes` 生成失败时是 `null`，`plan` 照常输出，错误信息走 stderr。

## 测试

```bash
.venv/bin/python -m pytest -q
```

测试不联网，不需要 key。注意要用 `python -m pytest`，不是直接 `pytest`。

## 检查工具

工具层同样不联网、不需要 key。改完 `group_agent/tools.py` 跑一次：

```bash
.venv/bin/python scripts/check_tools.py
```

每个工具都会真调一遍，正常和失败的路径都过，最后一行是 `25/25 通过`。

## 检查模型连通性

换 key、换模型之后跑一次：

```bash
.venv/bin/python scripts/check_llm.py
```

## 文档

- [架构说明](ARCHITECTURE.md)：为什么这么设计
- [协作方式](CONTRIBUTING.md)：分支、PR、评审
