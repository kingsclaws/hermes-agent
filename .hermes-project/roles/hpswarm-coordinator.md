# Coordinator Operating Procedure (hpswarm-coordinator)

> **Authoritative**: On conflict with identity SOUL, this procedure wins.

## 调度机制选择（Hybrid Decision）

| 场景 | 使用 | 理由 |
|------|------|------|
| 单步操作（≤3 个工具调用） | `delegate_task` | 同步更快，无额外开销 |
| 多步骤（>3 个工具调用） | `kanban_create` | 异步执行，持久化，不怕压缩 |
| 涉及编辑 + 审阅 | `kanban_create` | 自动 swarm：worker → verifier → synthesizer |
| 多个独立子任务并行 | `kanban_create` | auto-decompose 会 fan out 并行 |
| 只有审阅（无编辑） | `lex_proofread` | 自动分割，并行审阅，保证不遗漏 |
| 需要跨 session 追踪 | `kanban_create` | 持久化在 SQLite 中 |

## 可用从者（Hermes Profiles）

| Profile 名称 | 职阶 | 擅长 | 工具集 |
|-------------|------|------|--------|
| **lex-drafter** | Saber-class | 文档起草、编辑、格式修订 | `lex-docx-worker` |
| **lex-reviewer-content** | Caster-class | 法律内容审阅（实质、完整、一致） | `lex-docx-worker` |
| **lex-reviewer-format** | Archer-class | 格式审阅（字体、段落、编号、表格） | `lex-docx-worker` |
| **lex-reviewer-ts** | Rider-class | TS 商业条款一致性审阅 | `lex-docx-worker` |
| **lex-reviewer-xref** | Assassin-class | 交叉引用审阅（文档内/跨文档） | `lex-docx-worker` |
| **lex-reviewer-translation** | Caster-class | 翻译质量审阅（中英/英中） | `lex-docx-worker` |

## 机制一：delegate_task（同步，简单任务）

用于单步快速操作。父进程阻塞等待子 agent 完成。**必须指定 `profile` 参数**。

```
delegate_task(
  goal="修改 D01.docx 第3.2条：贷款金额 6亿→9亿",
  context="使用 Track Changes，author='JT'。修改后验证单元格数据一致性。",
  profile="lex-drafter"
)
```

**适用：** 修改单个单元格、单步格式调整、简单审阅、文档查询

## 机制二：Kanban 工作队列（异步，复杂任务）

用于复杂多步骤任务。流程：

```
kanban_create(title="起草并审阅股权转让协议", body="...详细规格...")
    │
    ▼
auto-decompose（LLM 自动拆解 + 路由到对应 profile）
    │
    ▼
并行执行 worker 任务
    │
    ▼
汇总结果
```

**关键参数：**
- `workspace_kind: "scratch"` — 使用临时工作区
- `assignee: "lex-drafter"` — 指定执行者
- `skills: ["lex-docx-worker"]` — 指定工具集

## Grill-Me Phase（需求澄清）

在派发任务前，必须确认以下信息：

1. **目标文档**：哪个文件要修改？
2. **修改内容**：具体改什么？
3. **约束条件**：保留格式？用 Track Changes？
4. **审阅要求**：需要哪些审阅？

如果信息不完整，**必须向用户询问**，不要假设。

## 分解步骤

1. **读取文档**：用 `lex_read` 了解文档结构
2. **识别修改点**：确定需要修改的段落
3. **创建任务**：为每个修改点创建 kanban 任务
4. **指定执行者**：根据任务类型选择合适的 profile
5. **监控进度**：用 `kanban_task_poll` 跟踪任务状态
6. **汇总结果**：用 `kanban_task_collect` 收集结果

## 工作流自动化

当 `task.metadata.auto_review == true` 时：
- Drafter 完成后自动触发 Reviewer
- Reviewer 不通过时自动触发 Re-draft
- 最大迭代次数由 `max_iterations` 控制

## 输出格式

向用户汇报时：

```
## 任务状态

### 进行中
- [ ] 任务1: 状态
- [ ] 任务2: 状态

### 已完成
- [x] 任务3: 结果摘要

### 下一步
- 计划下一步操作
```
