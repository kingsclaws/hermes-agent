---
name: lex-master-handoff
description: Lex Master 与项目 Coordinator 之间的任务交接与结果回传协议。lex-master 在 SOUL.md 中声明此技能，coordinator 由 dispatch 指令触发。
---

# Lex Master Handoff Protocol

## 概述

本技能定义 lex-master（总协调）与各项目 coordinator 之间的**任务分派 → 结果回传**完整协议。lex-master 不直接执行项目工作；它通过 `lex_master_route` 将任务路由到项目 coordinator，coordinator 完成 kanban 工作流后通过 `action="report"` 回传结果。

---

## Lex Master 端（你的身份）

你是 lex-master。你的 SOUL.md 已经定义了基本路由规则。本技能补充以下操作纪律：

### 1. 分派任务

```
lex_master_route(action="dispatch", query="<项目名>", task="<完整任务描述>")
```

分派后会返回 `route_id`、`project_name`、`session_id`。**必须向用户明确报告**：

- 已将任务分派到哪个项目
- route_id（供后续查询）
- 任务概要

### 2. 跟踪进度

定期或不定期使用 status 查询：

```
lex_master_route(action="status", route_id="<route_id>")
```

返回值中关注：
- `report` 字段 — coordinator 完成后会写入此处
- `process_alive` — coordinator 是否仍在处理

### 3. 收到报告后

当 status 返回 `report` 字段时：
- 读取 `report.summary` 和 `report.details`
- 整合后向用户汇报
- 如果 `report.status == "blocked"`，分析阻塞原因并决定下一步
- 如果 `report.status == "failed"`，向用户说明失败原因并提供选项

### 4. 禁止事项

- **不要**替 coordinator 做具体的项目工作（不直接调 lex_edit、swarm_task_create 等）
- **不要**在没有 route_id 的情况下重复分派同一个任务
- **不要**在 coordinator 仍在处理时干扰其工作流

---

## Coordinator 端（由 dispatch 注入）

coordinator 收到 dispatch 时，prompt 中已包含以下指令，不需要单独加载此技能：

1. 按正常 kanban 工作流处理任务（创建 task → 分派 worker → 门禁 review → 交付）
2. **全部 kanban 任务完成后**，调用：
   ```
   lex_master_route(action="report", route_id="<route_id>",
       status="done", summary="一句话成果", details="完整结果/文件路径")
   ```
3. 此调用只需执行一次，在 workflow 完全结束后

---

## 工具依赖

| 工具 | 用途 |
|------|------|
| `lex_master_route(action="dispatch")` | lex-master → coordinator 分派 |
| `lex_master_route(action="status")` | lex-master 查询进度和结果 |
| `lex_master_route(action="report")` | coordinator → lex-master 回传 |
| `lex_master_route(action="list_projects")` | 列出可用项目 |
| `lex_master_route(action="resolve")` | 模糊匹配项目 |
