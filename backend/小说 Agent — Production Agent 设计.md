# 小说 Agent — Production Agent 设计

## 1. 项目目标

构建一个真正具备 Agent 能力的小说创作系统，而不是简单的：

```text
用户 → LLM → 小说
```

目标是让 Agent 能够围绕一部长篇小说持续进行：

```text
规划 → 创作 → 检查 → 修改 → 保存 → 继续
```

并能够长期维护：

- 世界观
- 人物设定
- 剧情
- 章节
- 写作风格
- 作者要求
- 创作进度

最终形成一个具备 **Planning、Tool Use、Memory、Persistence、Context Management、Multi-Agent** 等能力的生产级 Agent。

---

# 2. 整体架构

```text
                         User
                          │
                          ↓
                    ┌───────────┐
                    │ NovelAgent│
                    └─────┬─────┘
                          │
             ┌────────────┼────────────┐
             ↓            ↓            ↓
          Planner       Writer       Reviewer
             │            │            │
             └────────────┼────────────┘
                          ↓
                       Runner
                          │
             ┌────────────┼────────────┐
             ↓            ↓            ↓
          Context        State        Tools
             │            │            │
             └────────────┼────────────┘
                          ↓
                        LLM
                          │
                          ↓
                     Observation
                          │
                          ↓
                        State
                          │
                          ↓
                   下一轮 Agent Loop
```

---

# 3. Agent Core

小说 Agent 的核心不是“一次生成小说”，而是持续循环。

```text
Context
   ↓
LLM
   ↓
Decision
   ↓
Action
   ↓
Observation
   ↓
Context
   ↓
LLM
```

例如：

```text
分析当前剧情
    ↓
决定写下一章
    ↓
调用章节生成能力
    ↓
生成章节
    ↓
检查人物是否符合设定
    ↓
发现问题
    ↓
决定修改
    ↓
重新生成
    ↓
通过检查
    ↓
保存章节
```

Agent Core 负责定义这种持续运行的 Agent 行为。

---

# 4. Context

Context 是当前这一轮真正提供给 LLM 的信息。

小说 Agent 的 Context 可能包括：

```text
System Instructions
User Requirements
当前卷大纲
当前章大纲
当前章节
前几章摘要
人物设定
世界观
剧情状态
写作风格
Reviewer 反馈
Tool Results
```

例如：

```text
System:
你是一名长篇小说创作 Agent。

Novel:
《XXX》

World:
......

Characters:
主角：......

Current Plot:
......

Current Chapter:
第 23 章

Previous Observation:
审核发现主角的行为与人物设定冲突。
```

## Context Builder

不要把所有小说内容永远塞给 LLM。

应该动态构建：

```text
State
  ↓
Context Builder
  ↓
Relevant Context
  ↓
LLM
```

---

# 5. State

State 保存小说 Agent 当前运行状态。

示例：

```ts
type NovelState = {
  status: "running" | "completed" | "failed";

  novel: Novel;

  currentChapter?: Chapter;

  characters: Character[];

  world: World;

  plot: Plot;

  tasks: Task[];

  observations: Observation[];

  variables: Record<string, unknown>;

  metadata: Record<string, unknown>;
};
```

State 主要解决：

> Agent 现在处于什么状态？

例如：

```text
当前卷：第二卷
当前章节：23
章节状态：reviewing

主角：
  已获得能力 X

剧情：
  已完成事件 A
  已完成事件 B
  下一事件 C

Review：
  发现人物行为冲突
```

---

# 6. Tool

Tool 是小说 Agent 与外部世界交互的能力。

可能的 Tool：

```text
read_novel
save_chapter
read_chapter
search_character
update_character
search_world
update_world
search_plot
update_plot
search_web
search_reference
check_consistency
```

例如：

```text
LLM
 ↓
read_character("张三")
 ↓
Tool
 ↓
Character Data
 ↓
Observation
 ↓
LLM
```

## Tool Registry

所有工具统一注册：

```ts
toolRegistry.register(readNovelTool);
toolRegistry.register(saveChapterTool);
toolRegistry.register(searchCharacterTool);
toolRegistry.register(checkConsistencyTool);
```

Agent 不应该：

```ts
if (toolName === "readNovel") {}
if (toolName === "saveChapter") {}
if (toolName === "searchCharacter") {}
```

而应该：

```ts
const tool = toolRegistry.get(toolName);

await tool.execute(args);
```

---

# 7. Runner

Runner 是 Agent 的运行器。

负责驱动整个 Agent Loop：

```text
while (running) {

  1. 构建 Context

  2. 调用 LLM

  3. 判断 LLM Decision

  4. 如果调用 Tool
       ↓
     执行 Tool
       ↓
     Observation
       ↓
     回到 Context

  5. 如果完成
       ↓
     结束 Agent

}
```

小说场景：

```text
Runner
 ↓
LLM：我要查看主角设定
 ↓
Tool：search_character
 ↓
Observation
 ↓
LLM：我要生成这一章
 ↓
Tool：generate/save chapter
 ↓
Observation
 ↓
LLM：我要进行一致性检查
 ↓
Tool：check_consistency
 ↓
Observation
 ↓
LLM：发现问题，需要修改
 ↓
...
```

Runner 不应该负责小说业务逻辑。

它负责：

> **让 Agent 持续运行。**

---

# 8. LLM Layer

LLM 层负责模型抽象。

```text
NovelAgent
    ↓
LLM Interface
    ↓
┌──────────┬──────────┬──────────┐
OpenAI    Anthropic   Gemini
```

需要支持：

- Tool Calling
- Streaming
- Structured Output
- Token Usage
- Retry
- Timeout
- Model Fallback
- Model Selection

未来可以针对不同任务选择不同模型：

```text
Planner       → 强推理模型
Writer        → 创作模型
Reviewer      → 推理模型
Summarizer    → 快速模型
```

---

# 9. Planner

Planner 负责长篇小说的任务分解。

小说天然适合层级规划：

```text
Novel
 ↓
Volume
 ↓
Arc
 ↓
Chapter
 ↓
Scene
```

例如：

```text
小说
│
├── 第一卷
│   ├── 第 1 章
│   ├── 第 2 章
│   └── 第 3 章
│
├── 第二卷
│   ├── 第 20 章
│   ├── 第 21 章
│   └── 第 22 章
│
└── 第三卷
```

Planner 负责：

- 故事总体规划
- 卷规划
- 章节规划
- 场景规划
- 任务拆分
- Replanning

如果创作过程中剧情发生变化：

```text
原 Plan
   ↓
发现剧情变化
   ↓
Replanner
   ↓
新 Plan
```

---

# 10. Memory

Memory 解决：

> Agent 如何记住长期小说信息？

小说 Memory 非常重要。

可以记忆：

### 人物 Memory

```text
张三：

性格：
谨慎、冷静

关系：
李四 → 师徒

经历：
第 10 章获得 XXX
```

### 世界观 Memory

```text
修炼体系
势力
地理
历史
规则
```

### 剧情 Memory

```text
已经发生：
事件 A
事件 B
事件 C

未解决：
伏笔 X
伏笔 Y
```

### 作者 Memory

```text
作者喜欢：
慢节奏
细节描写
第一人称
```

Memory 的基本流程：

```text
Write
 ↓
Memory Store
 ↓
Retrieve
 ↓
Context
 ↓
LLM
```

---

# 11. Persistence / Checkpoint

Memory 解决“记住什么”。

Persistence 解决：

> Agent 的运行状态如何保存？

例如 Agent 写到第 50 章时程序崩溃。

应该能够：

```text
Checkpoint

第 47 章 ✓
第 48 章 ✓
第 49 章 ✓
第 50 章处理中
```

重新启动：

```text
Restore State
      ↓
第 50 章继续
```

需要支持：

- State Persistence
- Checkpoint
- Resume
- Recovery
- Run History
- Versioning

---

# 12. Context Compression

长篇小说最大的实际问题之一：

> Context 会越来越大。

不能把前 500 章全文全部塞给 LLM。

应该：

```text
完整历史
   ↓
Summarization
   ↓
Chapter Summary
   ↓
Arc Summary
   ↓
Volume Summary
```

最终 Context：

```text
当前章节
+
相关人物
+
相关世界观
+
当前剧情
+
历史摘要
+
必要原文
```

形成：

```text
Raw Content
     ↓
Compression
     ↓
Summary
     ↓
Retrieval
     ↓
Context
```

---

# 13. Guardrails / Security

小说 Agent 也需要安全边界。

主要包括：

- 输入检查
- 输出检查
- Tool 权限
- Tool 参数验证
- Prompt Injection 防护
- 外部内容安全检查
- 敏感操作审批

例如：

```text
LLM
 ↓
save_novel
 ↓
Permission Check
 ↓
执行
```

对于高风险 Tool：

```text
LLM
 ↓
Tool
 ↓
Human Approval
 ↓
Execute
```

---

# 14. Consistency

小说 Agent 特别需要“一致性检查”。

这是小说领域非常重要的一层业务能力。

检查：

### 人物一致性

```text
第 3 章：
张三害怕火

第 50 章：
张三主动冲进火海

→ 可能存在冲突
```

### 世界观一致性

```text
世界观：
魔法无法在水中使用

第 80 章：
主角在水中释放魔法

→ 冲突
```

### 剧情一致性

```text
第 20 章：
角色 A 已死亡

第 50 章：
角色 A 再次出现

→ 冲突
```

可以作为 Tool：

```text
check_character_consistency
check_world_consistency
check_plot_consistency
```

也可以由 Reviewer Agent 完成。

---

# 15. Observability

生产 Agent 必须知道：

> Agent 到底做了什么？

例如：

```text
Run #1024

Step 1
LLM → Planner

Step 2
Tool → search_character

Step 3
LLM → Writer

Step 4
Tool → check_consistency

Step 5
LLM → Reviewer

Step 6
Tool → save_chapter
```

需要记录：

- Run
- Step
- LLM Call
- Tool Call
- Token Usage
- Latency
- Error
- State Change
- Final Result

最终可以做到：

```text
Novel Run
 ├── Step 1
 ├── Step 2
 ├── Step 3
 ├── Step 4
 └── Step 5
```

可以重新查看 Agent 为什么做出某个决定。

---

# 16. Multi-Agent

当单 Agent 复杂到一定程度，可以拆成多个 Agent。

```text
                    Novel Supervisor
                           │
          ┌────────────────┼────────────────┐
          ↓                ↓                ↓
      Planner Agent     Writer Agent    Reviewer Agent
          │                │                │
          ↓                ↓                ↓
       剧情规划           小说创作          一致性检查
```

还可以增加：

```text
Character Agent
World Agent
Research Agent
Editor Agent
Style Agent
```

例如：

```text
Supervisor
    ↓
Planner Agent
    ↓
Writer Agent
    ↓
Reviewer Agent
    ↓
发现问题
    ↓
Writer Agent
    ↓
通过
    ↓
Save
```

---

# 17. 小说 Agent 最终能力

最终希望做到：

```text
用户：
帮我写一部长篇玄幻小说。
要求：
- 100 万字
- 三个阶段
- 主角成长路线明确
- 有伏笔
- 人物不能崩
```

Agent：

```text
1. 建立世界观
2. 建立人物
3. 制定总体大纲
4. 拆分卷
5. 拆分章节
6. 创建当前任务
7. 检索相关 Context
8. 生成章节
9. 检查人物一致性
10. 检查世界观一致性
11. 检查剧情一致性
12. 修改
13. 保存
14. 更新 Memory
15. 创建 Checkpoint
16. 进入下一章
```

整个过程由 Runner 驱动。

---

# 18. 12 个 Agent Framework 模块与小说 Agent 的映射

| Agent Framework | Novel Agent |
|---|---|
| Agent Core | 小说创作 Agent |
| Context | 当前章节 + 剧情 + 人物 + 世界观 |
| State | 小说当前运行状态 |
| Tool | 读写小说、查询人物、检查剧情 |
| Runner | 驱动创作循环 |
| LLM | Planner / Writer / Reviewer 的模型 |
| Planner | 小说 → 卷 → 章 → 场景 |
| Memory | 人物、世界观、剧情、作者偏好 |
| Persistence | 保存创作进度 |
| Guardrails | 安全与 Tool 权限 |
| Observability | Agent 执行轨迹 |
| Multi-Agent | 编剧 / 写作 / 审核 / 角色等 Agent |

---

# 19. 项目学习路线

## Phase 1：Agent 基础

```text
Agent Core
Tool
Tool Registry
State
Executor
Runner
LLM
```

目标：

> 能够运行一个最基本的小说 Agent。

---

## Phase 2：Context

```text
Context
Context Builder
Context Management
Context Compression
```

目标：

> Agent 能够正确获得当前小说所需要的信息。

---

## Phase 3：Planning

```text
Planner
Task
Task Dependency
Replanning
```

目标：

> Agent 能够真正规划长篇小说。

---

## Phase 4：Memory

```text
Short-term Memory
Long-term Memory
Retrieval
Memory Write
```

目标：

> Agent 能够长期记住小说内容。

---

## Phase 5：Persistence

```text
Checkpoint
State Persistence
Resume
Recovery
```

目标：

> Agent 可以随时暂停并继续创作。

---

## Phase 6：Production

```text
Guardrails
Security
Observability
Tracing
Metrics
```

目标：

> 从 Demo Agent 变成生产级 Agent。

---

## Phase 7：Multi-Agent

```text
Supervisor
Planner Agent
Writer Agent
Reviewer Agent
Character Agent
World Agent
```

目标：

> 构建完整的小说 Agent 系统。

---

# 20. 最终项目架构

最终可以演化为：

```text
Novel Agent
│
├── Agent Core
│
├── Runner
│
├── LLM
│
├── Context
│   ├── Context Builder
│   └── Context Compression
│
├── State
│
├── Planner
│
├── Memory
│
├── Persistence
│
├── Tools
│   ├── Novel Tools
│   ├── Character Tools
│   ├── World Tools
│   ├── Plot Tools
│   └── Research Tools
│
├── Guardrails
│
├── Observability
│
└── Multi-Agent
    ├── Supervisor
    ├── Planner Agent
    ├── Writer Agent
    ├── Reviewer Agent
    ├── Character Agent
    └── World Agent
```

---

# 21. 核心目标

这个项目最终不是为了做一个“AI 写小说网站”。

真正的目标是：

> **以小说 Agent 为真实业务场景，从零实现并理解一个生产级 Agent Framework。**

小说只是业务场景。

真正要掌握的是：

```text
Agent
 ↓
Context
 ↓
State
 ↓
Tool
 ↓
Runner
 ↓
Planner
 ↓
Memory
 ↓
Persistence
 ↓
Guardrails
 ↓
Observability
 ↓
Multi-Agent
```

最终能够做到：

> **不仅会使用 Agent Framework，还能理解为什么 Framework 必须设计成这样。**