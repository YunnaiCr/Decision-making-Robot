# 协作方式

三个人一个仓库。先讲四条原理，再讲每天怎么做。

## 原理：git 到底在管什么

### ① 有两份仓库，不是一份

```text
你的电脑                        GitHub
~/group-agent                  YunnaiCr/Decision-making-Robot
  └── .git（完整历史）           └── 完整历史
```

两份是**平的**，不是"服务器是主、本地是副本"。你在本地能提交、能切分支、能看全部历史，全都不需要联网。

```text
git push   把本地的提交复制到远端
git pull   把远端的提交复制到本地
```

所以 `push` 只动远端那**一个分支**，碰不到别人的东西——这就是它不需要任何人同意的原因。

### ② 分支是一个指针，不是一份拷贝

```text
main      → 提交 fd67bca
feat/x    → 提交 8a2c1e0
```

`git checkout -b feat/x` 只做一件事：**新建一个指针，指到你当前站的位置**。没有复制任何文件。

（从 C 过来容易以为"分支 = 把目录复制一份"，不是。）

你在 `feat/x` 上提交，只是让这个指针往前走；`main` 纹丝不动。所以两人各开一个分支，互不干扰。

### ③ commit 是一张快照

每次 commit 记下三样：**此刻所有文件的样子**、谁在什么时候改的、上一张快照是谁。

git 靠"上一张是谁"把快照串成链，这就是历史。所以回滚不是"撤销操作"，而是"把指针挪回某张快照"。

### ④ PR 不是 git 的功能

git 只有 `merge`，没有 PR。PR 是 GitHub 加的**网页功能**，干四件事：

```text
展示两个分支的差异 → 跑自动检查 → 收集别人的同意 → 再执行 merge
```

所以 PR 只存在于 GitHub 上。同样的两个分支，纯 git 里也能 merge，只是没人拦你。

## 所以规则为什么长这样

```text
main       大家共用     → 改动要审批（这就是「分支保护」）
feat/xxx   你自己用     → 随便推，force push 也行
```

**保护只加在 main 上，因为共用的是它。** 其他分支是你的草稿区。

## 三个动作，各改变什么

```text
push    把本地提交送到远端的一个分支        不需要同意
PR      一张「申请把 A 合进 main」的单子    只是请求，不改代码
merge   真正合进 main                     需要 CI 绿 + 一个人同意
```

**只有最后一步需要许可。** 前面的 push 都是自由的。

## 一天的样子

```text
早上   git pull                              先同步
       git checkout -b feat/b-tools          开自己的分支
       改代码
       .venv/bin/python -m pytest -q         自己先跑绿
       git add -A && git commit -m "..."
       git push origin feat/b-tools          推到远端（还不开 PR）

做完了 开 PR → 队友 Approve → CI 绿 → Create a merge commit → 删分支

第二天 git checkout main && git pull          回到最新
```

## 开分支

`main` 永远可运行，**谁都不许直接 push main**，直接推会被拒。

```bash
git checkout main
git pull
git checkout -b feat/c-plan-prompt
```

命名：`feat/<你的名字>-<做什么>`（实际上名字自由发挥即可）。一个分支只做一件事，做完了就发 PR，别攒。

## 提交

英文，一句话说清改了什么：

```text
feat: add failure injection to search_places
fix: stop the planner from repeating a rejected draft
docs: explain why the verifier is pure code
```

## 什么时候才该合并

**合并 = 宣布「这件事做完了，别人可以直接用」。**

所以不是每天固定合一次，而是**一个分支上的活儿真做完了**才合。三条全中才算完：

```text
□ 能跑      手动跑一遍 e01，输出正常
□ 测试绿    .venv/bin/python -m pytest -q 全过
□ 不打算再改 这个分支上的目标已经达成
```

三条没全中，就继续在分支上改、接着 push。**push 不需要理由，合并需要。**

不该合的情况：

```text
只是想存档         → push 上去就行，不用开 PR
还在试，成不成不知道 → 分支留着，实验失败直接删掉，不影响 main
一堆不相关的改动    → 拆成几个分支、几个 PR，别塞一起
```

**不要等"全部做完"才合。** 一个人做完 A 就合 A，另一个人做完 B 再合 B，main 一直往前滚。攒着不合，最后三个人一起合，冲突会很难收。

## 开 PR

push 完，仓库首页顶上会出现一条黄条 `Compare & pull request`，点它：

```text
base     = main          ← 目标
compare  = 你的分支       ← 来源
标题      写清做什么
```

点 Create，PR 就开好了。

**PR 挂在分支上，不是挂在某个提交上。** 后面再改、再 push，PR 会自动更新——diff 变、CI 重跑。**不用重开 PR**，除非你把分支删了。

## CI 会自己跑

每次 push 到有 PR 的分支、或者合并进 main，GitHub 会在一台干净机器上跑一遍测试。

脚本在 `.github/workflows/test.yml`，跑的就是：

```bash
python -m pytest -q
```

结果在 PR 页面底部：绿勾或红叉。**红了不用等人说，自己先修。**

## 别人怎么审你的 PR

```text
1. 点 "Files changed" 标签，看红绿 diff
2. 右上角 "Review changes" → 选 Approve → Submit review
3. 回到 "Conversation" 标签
```

你那边会看到 `1 approval` 打勾、CI 绿勾，然后 Merge 按钮才亮。

**自己不能批自己的 PR。** 你的找 B 或 C 看，他们的你来看。

## 怎么合并

点绿色的 **Create a merge commit**。

```text
一个 PR → main 上多出「你分支的全部提交 + 一个合并提交」
```

历史里能看见这条分支从哪里分出去、在哪里合回来，`git log --graph` 很直观，各人回看自己的分支也方便。

按钮旁边的下拉里还有 **Squash and merge**，会把整个 PR 压成一个提交——**那是例外，不是默认**，只有一堆小修小补、不想留过程时才用。

> 推了新提交之后，之前那个 Approve 会自动作废——因为人家批的是旧代码。改完在 PR 里 @ 一下对方。

## 合并之后

GitHub 合完会问一句 `Delete branch`，点掉。

分支是临时通道，合完就该消失，不然仓库里堆几十条没人记得的分支。

本地也删：

```bash
git checkout main
git pull
git branch -d feat/b-tools
```

**然后立刻开下一个分支做下一件事**，不要在 main 上直接改。

## 评审时看什么

四条，别只回一句「看着行」：

```text
□ pytest 全绿了吗（红了就没得商量）
□ 动了 models.py 吗？契约变了必须全员知会
□ 有没有把 key、真实人名、真实聊天记录写进代码
□ 新加的文件，别人看得懂它是干什么的吗
```

## 谁能改什么

分工不是领地，但改别人的部分**先说一声**。

| 文件 | 主要谁管 |
|---|---|
| `models.py` / `verifier.py` / `orchestrator.py` | Yunnai |
| `tools.py` / `fixtures/` | B |
| `prompts/` | C |
| `tests/` | 谁改功能谁补测试 |

## 红线

```text
✗ 把 .env 提交上去
✗ 在代码或提示词里写真实人名、手机号、住址
✗ 直接 push main
✗ 自己合并自己的 PR
```

`.env` 已在 `.gitignore` 里。万一真提交了，**先去平台吊销密钥，再清理仓库**——只删文件没用，git 历史里还在。

## 测试为什么能在 CI 里跑

因为 `tests/` 里的用例**全部离线**：模型节点和表达节点都通过参数塞假实现，不联网、不需要 key。

所以 CI 里不用配任何密钥。也正因为这样，**不许在测试里调真模型**——一有网络，CI 就不稳，保护规则就形同虚设。
