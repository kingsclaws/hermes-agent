# Lex Master 与项目 Coordinator 路由计划

Date: 2026-07-03

## 目标

把 `lex-master` 明确成“总入口 / 总协调员”，而不是项目执行闭环的最终收件人。

期望行为：

1. 用户只通过微信联系 `lex-master`。
2. `lex-master` 负责识别项目、选择项目 coordinator、下发任务。
3. 项目内的 `drafter / reviewer / worker` 只向该项目自己的 coordinator session 汇报。
4. 项目 coordinator 只有在需要向上汇总时，才把结果回传给 `lex-master`。

## 问题背景

当前实现已经具备 kanban 任务流转、session wake、自动通知等能力，但语义边界混在了一起：

- `lex-master` 会收到项目任务的自动唤醒。
- reviewer 完成后，通知链有时会落到 `lex-master` 的 synthetic wake session。
- 这会造成两个误解：
  - 以为 `lex-master` 是所有项目任务的默认回报对象。
  - 以为项目内 reviewer 应该直接面向 `lex-master` 汇报。

实际上，`lex-master` 应该只做路由，不参与项目内闭环。

## 设计原则

1. `lex-master` 只做总路由，不直接做项目工作。
2. 项目 coordinator 是项目闭环的唯一上游收件人。
3. reviewer、drafter、worker 只对本项目 coordinator 负责。
4. 通知必须按“归属 coordinator session”分发，而不是按“lex-master”统一上送。
5. 跨项目汇总是显式动作，不是默认动作。

## 需要落地的行为

### 1. lex-master 只负责入口路由

- 微信、API、CLI 进入 `lex-master` 后，只允许：
  - 识别项目
  - 查找或创建项目 coordinator
  - 把请求路由给该 coordinator
- `lex-master` 不应直接执行项目内 `swarm_task_collect`、`lex_edit`、`lex_ocr` 等项目工作。

### 2. 项目 coordinator 才是任务归属者

- 每个项目必须有自己的 coordinator session。
- kanban 任务创建时的 `session_id` 应该绑定到项目 coordinator session。
- reviewer 完成后，默认只唤醒该 coordinator session。
- 该 coordinator 再决定是否向 `lex-master` 汇总。

### 3. 自动通知按 coordinator 归属投递

- `task_completed`、`task_blocked`、`task_crashed` 等终态通知，默认发送给项目 coordinator。
- `lex-master` 只有在自己作为上游协调者收到显式汇总时才介入。
- 任何 synthetic wake 必须保留“项目 coordinator 语义”，不能默认变成 lex-master 语义。

### 4. 兼容旧 session

- 旧 session 里已经存在的 kanban 订阅和 coordinator 绑定，必须尽量继续可用。
- 对旧任务，如果已绑定到 `lex-master`，需要提供迁移策略：
  - 能识别真实项目 coordinator 的，切到项目 coordinator。
  - 不能识别的，保留旧行为，但在日志里标记为 legacy。

## 实施计划

### Phase 1: 明确路由模型

- 梳理 `lex-master`、项目 coordinator、worker/reviewer 的角色定义。
- 统一“谁是 coordinator session”的判定来源。
- 规范 kanban 任务创建时的 `session_id` 绑定逻辑。

### Phase 2: 调整通知落点

- 修改 kanban 完成事件的 session wake 逻辑。
- 默认把通知投递给项目 coordinator session。
- 仅在项目 coordinator 明确上抛时，才路由到 `lex-master`。

### Phase 3: 调整 lex-master 的工具约束

- 给 `lex-master` 增加强约束：
  - 不直接处理项目执行工具
  - 只能调用路由工具和项目识别工具
- 对违反约束的工具调用，继续保持门禁拦截。

### Phase 4: 回归验证

- 验证 `lex-master` 能从微信收到用户请求并路由到项目 coordinator。
- 验证项目内 reviewer 完成后，只唤醒项目 coordinator。
- 验证项目 coordinator 汇总后，才上报给 `lex-master`。
- 验证旧 session 在升级后不丢失任务通知。

## 验收标准

1. `lex-master` 不再是项目 reviewer 的默认收件人。
2. 项目 coordinator 能完整收到 reviewer / drafter 的回报。
3. `lex-master` 只接收它自己作为总入口时应接收的汇总。
4. kanban 的自动通知日志能清楚区分：
   - 项目内通知
   - lex-master 汇总通知
5. 旧 session 不需要手工重建就能继续工作。

## 相关文件

- `gateway/kanban_runner.py`
- `gateway/run.py`
- `gateway/slash_commands.py`
- `hermes_cli/kanban_db.py`
- `tools/project_management_tool.py`
- `tools/legal_orchestration_tool.py`
- `hermes_cli/web_server.py`

## 风险

- 老 session 中可能已经有错误绑定，需要兼容迁移。
- `session wake` 和 `notifier_profile` 机制同时存在，容易出现重复唤醒。
- 如果路由约束过严，可能误伤需要上抛给 `lex-master` 的汇总场景。

