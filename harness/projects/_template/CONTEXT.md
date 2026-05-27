# CONTEXT.md — 项目上下文（hp 管理，仅供参考）

> **实际项目上下文由 `hp` 管理。** 运行 `hp init` 后，项目上下文存储在
> `.hermes-project/project-context.md`，由 Coordinator 维护。
> 本文件仅为参考模板，说明上下文的结构和字段含义。

## 项目状态机

由 Coordinator 在 `project-context.md` 中维护：

```
INIT → DRAFTING → REVIEWING → REVISING → FINAL → DELIVERED
```

- **INIT**：项目刚创建，等待第一个任务
- **DRAFTING**：Drafter 正在起草/修改文档
- **REVIEWING**：Reviewer 正在审阅（内容 or 格式）
- **REVISING**：根据审阅意见改稿中
- **FINAL**：定稿，等待最终验收
- **DELIVERED**：已交付

## hp 项目日常命令

| 命令 | 说明 |
|------|------|
| `hp list` | 列出所有项目 |
| `hp context <slug>` | 查看项目上下文 |
| `hp sync <slug>` | 同步上下文到 Hermes 会话 |
| `hp goal <slug> "text"` | 更新项目目标 |
| `hp status <slug>` | 查看 session 状态 |
| `hp open <slug>` | 打开项目 session（通过 hermes --tui） |
| `hp activity <slug>` | 查看项目文件变迁 |

## 上下文字段说明

`project-context.md` 中的标准字段（由 hp init 生成，Coordinator 维护）：

- **Project Facts**：项目名称、目录、客户、目标、摘要
- **Project Background**：项目背景（Coordinator 补充）
- **Key Files**：关键文件路径
- **Active Tasks**：活跃任务清单
- **Constraints / Working Rules**：工作约束与规则
- **Logistics Support**：后勤项目配置
- **Recent Decisions**：关键决策记录
- **Extra Notes**：补充说明

## HPSwarm 调度流程

```
Coordinator（读取 project-context.md）
  └─ delegate_task() ──→ Drafter（lex_docx 起草/修改）
                              │
                              ▼
                     Reviewer-Content（内容审阅）
                              │
                              ▼
                     Reviewer-Format（格式审阅）
                              │
                              ▼
                     Coordinator（汇总 → 用户）
```
