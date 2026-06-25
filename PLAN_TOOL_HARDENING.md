# PLAN A: Tool Layer Hardening — Delivery Harness

## 目标

将现有的**顾问式**（仅报告不阻断）检查体系改造为**硬性阻断**体系，类似 Claude Code 的 plan→todo→verify 不可跳过的状态机。

## 当前状态

| 组件 | 位置 | 当前行为 | 目标 |
|------|------|---------|------|
| `swarm_task_wait` | `tools/kanban_toolset.py:634` | 阻塞轮询（最多300s） | 废弃，替换为 poll |
| `swarm_task_poll` | **不存在** | — | 🆕 非阻塞多任务状态 |
| `swarm_task_collect` | **不存在** | — | 🆕 收集 worker 产出 |
| `validate_handoff()` | `project_commands.py:2059` | 仅返回 errors 列表 | 嵌入 handoff，硬阻断 |
| `legal_scorecard()` | `project_commands.py:2147` | 仅返回 failures 列表 | 嵌入 approve，硬阻断 |
| `DELIVERY_SPEC.yaml` | **不存在** | — | 🆕 项目级交付标准 |
| `.hermes-project/convention-profiles.json` | 空文件 | 从未使用 | 在 scorecard 中强制要求 |

---

## Step A1: DELIVERY_SPEC.yaml 加载器

### 文件: `hermes_cli/delivery_spec.py` (新)

```python
# 职责:
# 1. 加载 <project>/.hermes-project/DELIVERY_SPEC.yaml
# 2. 逐 gate 验证 entry/exit 条件
# 3. 返回结构化验证结果

def load_delivery_spec(project_path: str) -> dict:
    """加载项目的交付规范文件。不存在则返回默认空 spec。"""

def validate_gate_entry(project_path: str, gate: str) -> list[str]:
    """检查是否满足进入某个阶段的前置条件。返回失败列表（空=通过）。"""

def validate_gate_exit(project_path: str, gate: str) -> list[str]:
    """检查是否满足退出某个阶段的后置条件。返回失败列表（空=通过）。"""
```

### Spec 文件约定

路径: `<project>/.hermes-project/DELIVERY_SPEC.yaml`

```yaml
# 见设计文档中的结构
project: "项目名"
version: 1
deliverables: [...]
gates:
  plan: { entry: [...], exit: [...] }
  draft: { entry: [...], exit: [...] }
  review: { entry: [...], exit: [...] }
  finalize: { entry: [...], exit: [...] }
```

### 验证

- 单元测试：不存在的 spec 返回空 errors
- 单元测试：spec 中缺少某 gate 时跳过该 gate
- 单元测试：YAML 解析错误时返回可读的错误信息

---

## Step A2: swarm_task_poll — 非阻塞多任务状态

### 注册名: `swarm_task_poll`

### 修改: `tools/kanban_toolset.py`

```python
# Schema
KANBAN_TASK_POLL_SCHEMA = {
    "name": "swarm_task_poll",
    "description": (
        "非阻塞查询多个任务的状态。立即返回每个任务的当前状态、"
        "assignee、完成时间等。不会等待——Coordinator 用它了解进度，"
        "然后决定是否需要等待或收集结果。"
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "task_ids": {
                "type": "string",
                "description": "逗号分隔的任务 ID 列表，如 'tsk_abc,tsk_def'",
            },
            "project_path": {
                "type": "string",
                "description": "项目路径。省略则使用当前项目。",
            },
        },
        "required": ["task_ids"],
    },
}
```

### 返回格式

```json
{
  "success": true,
  "tasks": [
    {
      "task_id": "tsk_abc",
      "title": "...",
      "status": "done",
      "assignee": "hpswarm-drafter",
      "completed_at": 1234567890,
      "gate_progress": "2/3"
    }
  ],
  "summary": {
    "total": 3,
    "todo": 0,
    "in_progress": 1,
    "in_review": 0,
    "done": 2,
    "blocked": 0
  }
}
```

### 验证

- 3 个任务混合状态（todo/running/done），poll 立即返回
- 不存在的 task_id 在结果中标记 `{"error": "not found"}` 而非整体失败
- 并发安全：poll 不持有锁，仅读取

---

## Step A3: swarm_task_collect — 收集 Worker 产出

### 注册名: `swarm_task_collect`

### 修改: `tools/kanban_toolset.py`

```python
KANBAN_TASK_COLLECT_SCHEMA = {
    "name": "swarm_task_collect",
    "description": (
        "收集已完成任务的 Worker 产出。从任务的 event log、blackboard 评论、"
        "和 handoff_history 中提取 Worker 的工作成果。"
        "\n\n"
        "只对终态任务有效（done/approved/rejected）。"
        "进行中的任务返回其当前 event 摘要。"
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "task_id": {
                "type": "string",
                "description": "要收集的任务 ID。",
            },
            "include_events": {
                "type": "boolean",
                "description": "是否包含完整 event log（默认 false，仅摘要）。",
            },
            "project_path": {
                "type": "string",
                "description": "项目路径。省略则使用当前项目。",
            },
        },
        "required": ["task_id"],
    },
}
```

### 产出提取逻辑

1. 读 `task_events`：提取所有 `completed` / `handoff` / `approved` 事件的 payload
2. 读 `handoff_history`：提取每个 handoff 的 note（Worker 的修改说明）
3. 读 `comments`：提取 `[swarm:blackboard]` 前缀的评论内容
4. 合成结构化摘要返回

### 返回格式

```json
{
  "success": true,
  "task_id": "tsk_abc",
  "status": "done",
  "worker": "hpswarm-drafter",
  "summary": "完成第三条担保条款起草，新增 3 段，修改 2 处格式...",
  "events_summary": [
    {"kind": "claimed", "at": 1234567890},
    {"kind": "handoff", "note": "起草完成，请审核", "at": 1234568000},
    {"kind": "approved", "note": "内容合格", "at": 1234569000}
  ],
  "output": {
    "blackboard": [...],
    "files_touched": [...],
    "handoff_notes": [...]
  }
}
```

### 验证

- done 任务返回完整产出
- running 任务返回 `{"warning": "task not yet terminal", "current_status": "in_progress"}`
- 无 blackboard 的任务 output.blackboard 为空数组

---

## Step A4: swarm_task_handoff 硬性化

### 修改: `tools/kanban_toolset.py` — `kanban_task_handoff_handler()`

### 在 CAS handoff 验证通过后，执行 write 操作前，插入以下检查:

```python
# 1. 加载 DELIVERY_SPEC.yaml
from hermes_cli.delivery_spec import load_delivery_spec, validate_gate_exit

spec = load_delivery_spec(project_path)

# 2. 确定当前阶段（从 task metadata 或 handoff 计数推断）
current_gate_name = _infer_current_gate(task_row, spec)

# 3. 检查 exit criteria
exit_errors = validate_gate_exit(project_path, current_gate_name)
if exit_errors:
    return json.dumps({
        "success": False,
        "error": "交付规范检查未通过，禁止移交。",
        "gate": current_gate_name,
        "failed_checks": exit_errors,
        "fix_hint": "请先满足上述条件后再调用 handoff。"
    })

# 4. 检查 output_required 字段
# 从 workflow YAML 或 gate 配置中读取 output_required
# 验证 handoff note 中是否包含必要信息
output_errors = _validate_output_required(task_row, spec, current_gate_name)
if output_errors:
    return json.dumps({
        "success": False,
        "error": "缺少必要的交付物。",
        "missing_outputs": output_errors,
    })

# 5. 通过 → 执行原有 handoff 逻辑
```

### 错误返回格式（关键：必须可操作）

```json
{
  "success": false,
  "error": "交付规范检查未通过，禁止移交。",
  "gate": "draft",
  "failed_checks": [
    {"check": "tc_mode", "message": "所有文档修改必须在 Track Changes 模式下进行。请重新编辑并使用 TC 模式。"},
    {"check": "git_branch", "message": "修改必须在独立 git 分支上进行。请先创建 rev/ 分支。"}
  ],
  "fix_hint": "请修复以上 2 个问题后重新调用 swarm_task_handoff。"
}
```

### 验证

- 未满足 TC 模式 → handoff 被拒，error 信息可操作
- 未创建 git 分支 → handoff 被拒
- 所有条件满足 → handoff 正常执行
- DELIVERY_SPEC.yaml 不存在 → 降级为仅检查 output_required（不阻断）

---

## Step A5: swarm_task_approve 硬性化

### 修改: `tools/kanban_toolset.py` — `kanban_task_approve_handler()`

### 在 CAS 验证通过后，执行 approve 前，插入:

```python
# 1. 运行 legal_scorecard
from hermes_cli.project_commands import legal_scorecard

score = legal_scorecard(
    project_path,
    document_path=task_metadata.get("document_path"),
    run_id=task_row.get("current_run_id"),
    strict=True,
)

# 2. 检查最低分数线（从 DELIVERY_SPEC 或默认 80）
min_score = spec.get("gates", {}).get("review", {}).get("exit", {}).get("legal_scorecard_min", 80)
score_pct = _scorecard_pass_rate(score)

if score_pct < min_score:
    return json.dumps({
        "success": False,
        "error": f"legal_scorecard 得分 {score_pct}% < {min_score}%，禁止批准。",
        "scorecard": score,
        "fix_hint": "请先修复 scorecard 中的 failures 后再调用 approve。"
    })

# 3. 检查硬性失败项
if score.get("failures"):
    return json.dumps({
        "success": False,
        "error": f"存在 {len(score['failures'])} 个硬性失败项，禁止批准。",
        "failures": score["failures"],
    })

# 4. 检查 handoff envelope 完整性
# 从 task handoff_history 中验证所有 handoff 都有有效的 note
# 没有空 handoff

# 5. 通过 → 执行原有 approve 逻辑
```

### 验证

- scorecard < 80 → approve 被拒
- convention_profile 缺失 → approve 被拒
- edit_verification 有 failed → approve 被拒
- 全部通过 → approve 正常执行

---

## Step A6: 注册新工具 + 清理

### 注册

在 `tools/kanban_toolset.py` 中注册 `swarm_task_poll` 和 `swarm_task_collect`。
使用顶层 `registry.register()` 保证 AST 扫描发现。

### 废弃

- `swarm_task_wait` 保留但标记 deprecated（在 description 中注明推荐使用 poll + collect）
- 不删除：已有 workflow 可能依赖它

### 工具分组更新

Coordinator 工具集:
- `swarm_board_create` ✅
- `swarm_board_info` ✅
- `swarm_task_create` ✅
- `swarm_task_assign` ✅
- `swarm_task_wait` ⚠️ deprecated (保留兼容)
- `swarm_task_poll` 🆕
- `swarm_task_collect` 🆕
- `swarm_workflow_compile` ✅
- `swarm_board_status` ✅

---

## 文件变更清单

| 文件 | 操作 | 大小 |
|------|------|------|
| `hermes_cli/delivery_spec.py` | 🆕 新建 | ~150 行 |
| `tools/kanban_toolset.py` | ✏️ 修改 | +~300 行（2 新工具 + 2 硬性化） |
| `hermes_cli/project_commands.py` | ✏️ 修改 | +~30 行（暴露 _validate_handoff_envelope） |

---

## Phase 依赖

```
A1 (DELIVERY_SPEC loader)
  └→ A4 (handoff 硬性化)
  └→ A5 (approve 硬性化)

A2 (poll) ─ 独立
A3 (collect) ─ 独立

A6 (注册+清理) 依赖 A2, A3
```

A1, A2, A3 可并行开始。A4, A5 依赖 A1。A6 收尾。
