# 架构

输入一段群聊，输出一份能照做的安排。本文只讲**为什么这么设计**；怎么跑看 `README.md`。

## 数据怎么流动

```text
群聊 JSON
   │ ChatLog（读入即校验）
   ▼
understand() ──① StateDelta──┐
                              ▼
                   ┌────────────────────┐
    ② rejections ─►│                    │
    ③ found ──────►│     GroupState     │
                   │                    │
                   └────────────────────┘
                              │ 每轮读取
                              ▼
                    plan() ──► PlannerDecision
                              │
         ┌────────────────────┼────────────────────┐
         ▼                    ▼                    ▼
        act                 final            ask / give_up
         │                    │                   │
   ActionRequest         FinalDraft          直接返回
         ▼                    ▼
    dispatch()            verify()
         │                    │
   Observation        VerificationResult
         │                    │
         ├─ ok ────► ③ 写回 found        ├─ 通过 ──► express() ──► FinalPlan
         └─ 失败 ──► ② 写回 rejections   └─ 不通过 ─► ② 写回 rejections
```

三条写回的箭头，各管一件事：

```text
① StateDelta   改约束、参与者、未知项
② rejections   记下"为什么被打回"或"工具为什么失败"
③ found/facts  把查到的候选、查到的事实存进去
```

**三个都写进 `GroupState`，所以下一轮 `plan()` 看得到。** 唯一不存的是 `plan()` 自己给出的决定——它每轮重新产生，不留历史。

循环上限 `MAX_STEPS = 12`；被拒 3 次（`MAX_REJECTIONS`）直接判无解。

## 中间量都是谁给谁

| 从 → 到 | 类型 | 写回状态吗 |
|---|---|---|
| 理解 → 状态 | `StateDelta` | **会**：应用后改变约束、参与者、未知项 |
| 规划 → 执行 | `ActionRequest` | 请求本身不存；它的结果见下一行 |
| 执行 → 状态 | `Observation` | **会**：`list[Candidate]` 进 `found`，其他结果进 `facts`；`error` 进 `rejections` |
| 规划 → 校验 | `FinalDraft` | 不会 |
| 校验 → 规划 | `VerificationResult` | **会一半**：转成一句话进 `rejections`，对象本身不存 |
| 校验 → 表达 | `FinalDraft` + 算好的数 | 不会 |
| 表达 → 用户 | `FinalPlan.message` | 不会 |

## 三条原理

### 1. 为什么必须有循环

因为**下一步查什么，取决于上一步查到了什么**。

e01 里，先要确认有没有合适的桌游店，才能决定晚餐排在哪里、排到几点。一次把它想完，等于在没看到世界之前就把方案定死。

所以 `plan()` 每轮只看当前状态、只给一个动作，`run()` 负责循环。

### 2. 为什么理解和规划分开

因为它们**知道自己错了的方式不一样**。

- 理解的错：可以对着人工标注比——"这句话应该抽出预算约束"，有唯一正确答案
- 规划的错：没有唯一答案，只能看最终方案好不好

分开之后，两者能各自单测、各自归因。合在一起，模型抽错了约束会伪装成"规划不好"，你永远不知道该改哪个。

代码上：`prompts/understanding.py` 只负责产出 `StateDelta`，`prompts/planning.py` 只负责产出 `PlannerDecision`。

### 3. 为什么校验器不是模型

因为**模型自评不可信**。你问它"你的方案对吗"，它答"对的"的概率远高于真实正确率——它出错是因为判断标准里少了东西，用同一个标准自查当然查不出来。

而硬约束判错的代价是"人到现场发现不行"。所以硬约束必须由纯函数逐条判：

```text
verifier.py
├── check_budget        价格加总，比人均/总价上限
├── check_diet          店的 tags 和"不能吃"求交集
├── check_availability  所有安排是否落在时间窗内
└── check_activity      方案里有没有覆盖要求的活动
```

结果有三种，不是两种：**是 / 否 / 无法判定**。查不到数据时只能说"不知道"，不能猜——猜出来的"通过"比"不知道"危险得多。

## 纪要和待办放在哪

纪要不是方案的输入，也不该受主循环那 12 步约束。它是在方案算完之后，
拿**同一份聊天 + 同一份状态**再问模型一次得到的，所以刻意放在循环外面：

```text
understand(chat) ──► GroupState ──┬──► run() ──► FinalPlan ──┐
                                  │                          ├──► 入口拼成一个 JSON
                                  └──► write_minutes() ──► Minutes
```

三条边界：它不写回 `GroupState`、不参与校验、失败不影响方案（入口会把 `minutes`
降级成 `null` 并往 stderr 打一行）。类型定义在 `minutes.py`，`models.py` 的契约不变。

## 工具

工具分两类，形状不同：

- **候选类** → `list[Candidate]` → 进 `GroupState.found`
- **事实类** → `dict` → 进 `GroupState.facts`

“候选”是通用词：一家店、一张票、一个场地，任何能排进方案的东西。
`Candidate.category` 是自由字符串，不做枚举——世界上的事没法穷举；
品类独有的属性放 `Candidate.attrs`，所以加一类新东西只需要加数据。

| 工具 | 回答什么 |
|---|---|
| `catalog` | 这个世界里有什么：品类计数、标签、属性的键和取值 |
| `search_options` | 符合条件的候选（品类可省略 + 标签/忌口/属性/价格/时间窗/距离/评分/排除名单） |
| `get_option` | 这个候选的完整资料 |
| `filter_options` | 已查到的名单里，谁还满足条件 |
| `rank_options` | 这批候选按便宜 / 好评 / 近怎么排 |
| `alternatives` | 这个不行，同品类还有谁 |
| `nearby_options` | 从这里出发，路上 N 分钟内能到谁 |
| `check_open` | 某个候选在指定时段能不能用（不知道就说 `null`，不猜） |
| `travel_time` | 两者之间路上几分钟 |
| `estimate_cost` | 几个人、几个候选，人均和总价各是多少 |

属性过滤走 `attrs` 键值，别为每个品类加专用参数：`attrs={"noise": "low"}`、
`attrs={"private_room": true}`。加一个新品类不需要改任何工具代码。

## 契约

这几样东西跨模块流动，改动要全员知会。

**`GroupState`** —— 主循环唯一共享的状态

```text
goal / date / participants     目标、日期、参与者
constraints                    约束列表（四种 kind）
found                          key -> 查到的候选
facts                          工具查到的其他事实（路程、营业判断、预算试算）
rejections                     被打回的原因，会回灌给规划层
unknowns                       还没搞清的事
next_id                        下一个约束编号
```

**`StateDelta`** —— 理解层的输出，只写"改变了什么"

```text
date / participants            整体替换
add_constraints                新增约束
drop_constraints               作废哪些（按 id）
unknowns                       新增的未知项
```

**id 规则**：模型给空字符串，由 `apply_delta()` 统一分配 `c1 / c2 / …`。模型只负责引用旧 id，不负责起名。

**三种出口**

| status | 什么时候 | 谁决定 |
|---|---|---|
| `ok` | 方案通过全部硬约束 | 校验器 |
| `needs_input` | 信息不足，要问用户 | 规划层 |
| `infeasible` | 硬约束无法同时满足 | 规划层 / 循环超限 |

**注意：规划层不能自封"完成"**。`FinalDraft` 里根本没有 `status` 字段，能不能通过由 `verify()` 说了算。

## 文件

```text
group_agent/
├── models.py        所有类型，唯一的契约来源
├── llm.py           唯一的模型出口：拼提示词、重试、记 token
├── tools.py         工具：候选类返列表、事实类返字典，四类错误分开
├── verifier.py      校验：纯函数，不调模型
├── orchestrator.py  主循环：编排三个节点，并决定结果进 found 还是 facts
├── minutes.py       纪要：策划完之后再问一次模型，不进循环
├── prompts/         四份提示词
│   ├── understanding.py
│   ├── planning.py
│   ├── express.py
│   └── minutes.py
└── __main__.py      入口

fixtures/            假数据：7 个候选（桌游/餐厅/影院/公园）、路上时间、e01 聊天记录
tests/               测试，全部离线
scripts/check_llm.py 换 key / 换模型后自检
scripts/check_tools.py 改完工具后自检
```

## 加东西的时候

| 想加什么 | 动哪里 |
|---|---|
| 一种新约束 | `models.py` 加类 → 联合类型加一项 → `verifier.py` 写检查器并注册 |
| 一个新工具 | `tools.py` 写函数 → 注册进 `TOOLS` → 加一条 `ToolSpec`（提示词自动带上）；候选类返回 `list[Candidate]`，事实类返回 `dict` |
| 一个新品类 | 只加 `fixtures/` 里的一条数据（`category` 自由填，差异放 `attrs`），代码不用动 |
| 改提示词 | 只动 `prompts/`，不碰循环 |
| 改循环的出口 | `orchestrator.run()` |

加一种约束或一个工具都是**增量**：不用改已有代码，只要加一处注册。如果发现要改循环主流程才能加，说明设计跑偏了，先说一声。
