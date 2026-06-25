# PLAN B: Gateway Event Bridge — Kanban Notification to Chat Sessions

## 目标

将 kanban 任务事件从 gateway 投递到**聊天会话**（WebUI + CLI），使得 coordinator agent 和用户能够实时看到 worker 的进度和结果，无需轮询。

## 当前状态

```
Gateway 进程                           Dashboard 进程
┌──────────────────────┐              ┌──────────────────────────┐
│ _kanban_notifier     │              │ /api/pub    (WS server)  │
│   → adapter.send()   │              │   ↑ PTY sidecar 写入     │
│   → Telegram/Discord │              │   ↓ _broadcast_event()  │
│                      │              │ /api/events (WS server)  │
│ _kanban_dispatcher   │  ??? 桥接?   │   ↑ 浏览器订阅           │
│   → spawn worker     │──────??──────│   ↓ 接收 tool.* 事件     │
│   → hermes chat -q   │              │                          │
└──────────────────────┘              └──────────────────────────┘

❌ 缺口:
1. Notifier 不投递到 /api/events 通道
2. Notifier 不投递到 CLI session 的消息流
3. WebUI 右侧面板不处理 kanban 事件类型
4. 没有 Session → Kanban Task 的归属追踪
```

## 架构决策

Gateway 和 Dashboard 可能在同一进程或不同进程中。
使用 **内部桥接函数** + **REST fallback** 模式：
- 同进程时直接调用 `_broadcast_event()`
- 跨进程时通过 `POST /api/kanban/broadcast` HTTP 端点

---

## Step B1: Kanban 事件广播端点

### 文件: `hermes_cli/web_server.py`

### 新增 HTTP 端点: `POST /api/kanban/broadcast`

```python
@app.post("/api/kanban/broadcast")
async def kanban_broadcast(request: Request):
    """
    Gateway kanban notifier 调用此端点广播 kanban 事件。
    
    Body:
    {
        "channel": "<session_channel_id>",   # 目标 session 的 channel
        "event_type": "kanban.task.completed", # 事件类型
        "payload": {
            "task_id": "tsk_abc",
            "title": "起草第三条",
            "status": "done",
            "worker": "hpswarm-drafter",
            "summary": "...",
            "board": "brd_xyz",
            "run_id": "run_123"
        }
    }
    """
```

### 安全

- 仅接受来自 localhost 的请求（`request.client.host == "127.0.0.1"`）
- 或使用内部共享 token

### 新增 Kanban 事件类型定义

```python
KANBAN_EVENT_TYPES = {
    "kanban.task.created",     # 任务被创建
    "kanban.task.claimed",     # Worker 认领
    "kanban.task.started",     # Worker 开始工作
    "kanban.task.progress",    # Worker 进度更新
    "kanban.task.handoff",     # Drafter 移交
    "kanban.task.completed",   # Worker 完成
    "kanban.task.approved",    # Reviewer 批准
    "kanban.task.rejected",    # Reviewer 拒绝
    "kanban.task.blocked",     # 阻塞
    "kanban.task.crashed",     # Worker 崩溃
    "kanban.task.timed_out",   # 超时
    "kanban.batch.completed",  # 一批任务全部完成
}
```

### 验证

- curl POST → 200 + 事件被广播到 `/api/events` 订阅者
- 无效 channel → 404
- 非 localhost → 403

---

## Step B2: Notifier 扩展 — 投递到 Chat Session

### 文件: `gateway/kanban_runner.py`

### 修改: `_kanban_notifier_watcher()` 

在现有投递逻辑（platform adapter.send）之后，增加 chat session 投递:

```python
async def _deliver_to_chat_session(self, event: dict, task_row: dict):
    """
    将 kanban 事件投递到创建该 task 的 session。
    
    通过 task_row['session_id'] 找到创建者 session，
    然后:
    1. 如果 session 在 gateway 上活跃 → 注入 system 消息
    2. 同时调用 /api/kanban/broadcast → WebUI 更新
    """
    session_id = task_row.get("session_id")
    if not session_id:
        return  # 非 session 创建的任务，跳过
    
    # 路径 1: Gateway session 消息注入
    await self._inject_kanban_system_message(session_id, event)
    
    # 路径 2: Dashboard broadcast (WebUI right panel)
    await self._broadcast_kanban_event(session_id, event)
```

### 新增辅助方法

```python
async def _inject_kanban_system_message(self, session_id: str, event: dict):
    """
    向 session 的消息队列注入一条 system 角色消息。
    
    消息格式取决于平台:
    - WebUI/Gateway: 通过 session 的消息通道注入
    - CLI/PTY: 通过 PTY stdin 注入 ANSI 格式的状态行
    """
    # 查找 session 对应的 transport
    # 注入格式化后的消息
    
async def _broadcast_kanban_event(self, session_id: str, event: dict):
    """
    调用 Dashboard 的 POST /api/kanban/broadcast 端点。
    如果 dashboard 与 gateway 在同一进程，直接调用内部函数。
    """
```

### 验证

- worker 完成 → coordinator 的 chat 中收到 system 消息
- system 消息格式: `[Kanban] 任务 "起草第三条" (hpswarm-drafter) 已完成`
- WebUI 右侧面板 Kanban tab 实时更新

---

## Step B3: Session ↔ Kanban Task 归属追踪

### 文件: `hermes_cli/kanban_db.py`

### 现有支持

`tasks` 表已有 `session_id` 字段（在 `kanban_toolset.py:573` 写入 `os.environ.get("HERMES_SESSION_ID", "")`）。

### 修改: `dispatch_once()` 或 `_default_spawn()`

Worker 被 spawn 时，将 coordinator 的 `session_id` 传递给 worker 环境:

```python
# 在 _default_spawn() 中:
if task.session_id:
    env["HERMES_KANBAN_PARENT_SESSION"] = task.session_id
```

这样 worker 在完成时可以将事件关联回 coordinator session。

### 新增: `subscribe_session_to_task(session_id, task_id)`

当 coordinator 创建 kanban task 时，自动在 `kanban_notify_subs` 表中创建一条订阅记录:

```sql
INSERT INTO kanban_notify_subs (task_id, platform, chat_id, thread_id)
VALUES (?, 'session', ?, NULL)
```

其中 `chat_id = session_id`，`platform = 'session'`。

Notifier 在处理 `platform='session'` 的订阅时，走 session 消息注入路径而非外部平台 adapter。

---

## Step B4: WebUI 前端 — Kanban 事件处理

### 文件: `web/src/components/CollapsibleRightPanel.tsx`

### 修改: `/api/events` WebSocket 消息处理（lines 169-222）

在现有的 `tool.start` / `tool.progress` / `tool.complete` 处理之外，增加:

```typescript
// 新增 kanban 事件处理
if (type === "kanban.task.created") {
  // 在 Kanban tab 中显示新任务
  setKanbanEvents((prev) => [...prev, {
    kind: "kanban",
    eventType: "created",
    taskId: payload.task_id,
    title: payload.title,
    status: payload.status,
    timestamp: Date.now(),
  }].slice(-50));
}
// ... 其他 kanban.task.* 事件类型
```

### 新增状态

```typescript
interface KanbanEvent {
  kind: "kanban";
  eventType: string;
  taskId: string;
  title: string;
  status: string;
  worker?: string;
  summary?: string;
  timestamp: number;
}

const [kanbanEvents, setKanbanEvents] = useState<KanbanEvent[]>([]);
```

### Kanban tab 增强

当 `activeTab === "kanban"` 时，除现有的 `CompactKanbanPanel`（active swarm）外，增加 kanban 事件时间线:

```tsx
{kanbanEvents.length > 0 && (
  <div className="mt-3 space-y-1 px-3">
    <div className="text-display text-xs tracking-wider text-text-tertiary mb-2">
      live events
    </div>
    {kanbanEvents.slice().reverse().map((ev) => (
      <KanbanEventCard key={`${ev.taskId}-${ev.timestamp}`} event={ev} />
    ))}
  </div>
)}
```

### 文件: `web/src/components/ChatMessageList.tsx`

### 修改: 渲染 kanban 完成事件为内联卡片

当 coordinator 的聊天流收到 `kanban.batch.completed` 系统消息时，
渲染为可折叠的汇总卡片（类似 SwarmInlineView 的折叠态）。

已在 `CollapsibleRightPanel.tsx` 的 `/api/events` 中处理，此处的修改是可选的增强。

### 验证

- coordinator 创建 kanban 任务 → 右侧面板 Kanban tab 出现 live event
- worker 完成 → 事件状态更新
- 切换 tab 不影响事件接收（WebSocket 持续连接）

---

## Step B5: Dispatcher 健康检查 + 日志

### 文件: `gateway/kanban_runner.py`

### 新增: Dispatcher 启动时日志

```python
logger.info(
    "kanban dispatcher: started (interval=%.1fs, boards=%d)",
    interval, len(boards),
)
```

### 新增: Dispatcher 心跳事件

每个 tick 向 `/api/pub` 发送一条 heartbeat 事件（如果 dashboard 可用）：

```python
# 在 dispatcher tick 结束时:
await _broadcast_kanban_heartbeat({
    "type": "kanban.dispatcher.heartbeat",
    "tick_id": tick_counter,
    "boards_scanned": len(boards),
    "tasks_spawned": spawned_this_tick,
    "tasks_in_progress": in_progress_count,
})
```

### 验证

- gateway 启动日志包含 "kanban dispatcher: started"
- WebUI Info tab 可以显示 dispatcher 状态（后续增强）

---

## Step B6: CLI/TUI — Kanban 事件展示

### 文件: `tui_gateway/` 相关文件

### 现状

PTY 通过 sidecar URL (`HERMES_TUI_SIDECAR_URL`) 连接到 `/api/pub`，但它作为**发布者**连接，不是订阅者。

### 方案: TUI 内联状态行

当 gateway notifier 检测到 `platform='session'` 的订阅事件时，通过 PTY stdin 注入 ANSI 格式的状态行:

```
\x1b[36m[Kanban]\x1b[0m 起草第三条 (drafter) · ✅ 已完成
```

TUI 不需要改代码——stdin 注入的文本会自动渲染在终端输出中。

### 实现位置: `gateway/kanban_runner.py`

```python
async def _inject_kanban_to_pty(self, session_id: str, message: str):
    """通过 PTY stdin 注入 kanban 状态消息。"""
    # 查找 session 对应的 PTY process
    # 向 PTY master fd 写入 ANSI 格式的消息
```

### 验证

- CLI 中 coordinator 创建 kanban 任务
- Worker 完成 → CLI 终端出现彩色的 [Kanban] 状态行

---

## Step B7: 端到端集成测试

### 测试场景

1. **Coordinator 创建任务 → 在 chat 中看到进度**
   - 启动 gateway
   - 在 WebUI 中发起 "审查这份合同"
   - Coordinator 创建 3 个 kanban 任务
   - ✅ 右侧面板 Kanban tab 显示 3 个 live events
   - ✅ Worker 完成后 chat 流收到 system 消息
   - ✅ 所有完成后收到 "3/3 tasks completed" 汇总

2. **CLI 端到端**
   - 在 CLI 中发起相同请求
   - ✅ 终端出现 [Kanban] 状态行
   - ✅ swarm_task_poll 返回正确状态
   - ✅ swarm_task_collect 返回 worker 产出

3. **错误路径**
   - Worker 崩溃 → ✅ chat 收到 "task crashed" 通知
   - Handoff 被拒 → ✅ coordinator 收到具体失败原因
   - Dispatch 未运行 → ✅ health check 显示 warning

---

## 文件变更清单

| 文件 | 操作 | 大小 |
|------|------|------|
| `hermes_cli/web_server.py` | ✏️ 修改 | +120 行（POST 端点 + 事件类型） |
| `gateway/kanban_runner.py` | ✏️ 修改 | +200 行（session 投递 + PTY 注入 + heartbeat） |
| `hermes_cli/kanban_db.py` | ✏️ 修改 | +50 行（session 订阅 + parent_session 传递） |
| `web/src/components/CollapsibleRightPanel.tsx` | ✏️ 修改 | +80 行（kanban 事件处理 + KanbanEventCard） |
| `web/src/components/ChatMessageList.tsx` | ✏️ 修改 | +40 行（kanban 完成卡片渲染） |

---

## Phase 依赖

```
B1 (广播端点)
  └→ B2 (notifier 扩展)
       └→ B3 (session 追踪)
            └→ B4 (WebUI 处理) + B6 (CLI 展示)
       
B5 (health check) ─ 独立

B7 (集成测试) 依赖 B1-B6
```

B1 和 B5 可并行开始。B2 依赖 B1。B3 和 B4 依赖 B2。
