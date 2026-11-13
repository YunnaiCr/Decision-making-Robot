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

在 `.env` 里填 `LLM_API_KEY`，其余项可以留空。要连外部数据源（高德）再填 `AMAP_KEY`。

## 运行

```bash
.venv/bin/python -m group_agent fixtures/chats/e01.json
```

也支持直接喂 QQ 导出的 txt：

```bash
.venv/bin/python -m group_agent ~/Downloads/群聊记录.txt
```

## 从 QQ 导入聊天记录

QQ **没有公开的聊天记录接口**，现实里只有两条路：

1. **导出文件**（推荐）：QQ 消息管理器 → 右键群 → 导出消息记录 → 选 `.txt`。
   本项目直接解析这个格式，离线、不登录、不碰账号。
2. **机器人框架**：NapCat / LLOneBot 这类，走 OneBot 协议拿实时消息。
   要常驻进程、要账号，还有封号风险，本项目不做。

导出文件常见是 GBK，导入时会自动尝试 UTF-8 和 GB18030。跨天的记录只保留消息最多的那一天，
因为 `Message.min` 只有 0–1440 分钟。

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

## 外部数据源（可选）

配了 `AMAP_KEY` 之后多两个工具：

- `search_remote(keyword, city)`：去高德查真实地点。返回的候选**没有营业时间**，价格也常缺，
  和 fixtures 里的本地数据不是一回事
- `weather(city)`：城市实时天气

没配 key 时它们返回 `unavailable`，**不会静默降级成假数据**。测试全部用假 transport，不联网。

接别的平台就照着 `group_agent/sources.py` 里的函数写一个，解析成 `Candidate` 或 dict，
再到 `tools.py` 注册。注意大多数平台没有公开 API，接之前先看服务条款。

## 检查模型连通性

换 key、换模型之后跑一次：

```bash
.venv/bin/python scripts/check_llm.py
```

## 文档

- [架构说明](ARCHITECTURE.md)：为什么这么设计
- [协作方式](CONTRIBUTING.md)：分支、PR、评审
