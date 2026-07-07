# Lex-Hermes Plugin Migration Plan

Date: 2026-07-03

## 目标

把 `lex-hermes` fork 的所有自定义功能逐步迁移到基于官方 `upstream/main` 的 `lex-hermes-plugin` 分支，通过 plugin 机制实现，最小化对上游代码的修改。

## 当前状态

- `lex-hermes` 分支：468 个文件改动（309 新增 + 158 修改上游）
- `lex-hermes-plugin` 分支：干净的 upstream/main + lex-legal-tools plugin（2 文件）

## 迁移原则

1. **能做 plugin 的绝不改源码**
2. **必须改源码的，用最小 diff + 清晰注释标记 `# LEX-HERMES`**
3. **每迁移一个功能，验证一次**
4. **保持 `lex-hermes` 分支作为生产分支，`lex-hermes-plugin` 作为开发分支**

## 迁移阶段

### Phase 1: Plugin 骨架 ✅

- [x] `plugins/lex-legal-tools/plugin.yaml`
- [x] `plugins/lex-legal-tools/__init__.py` — 注册 15 个工具模块 + 3 个 toolset

### Phase 2: 纯新增文件迁移（Layer 3，零冲突）

从 `lex-hermes` 分支复制到 `lex-hermes-plugin`，不需要改任何上游文件：

| 组件 | 文件 | 说明 |
|---|---|---|
| lexitool 库 | `vendor/lexitool/` | pip 包，独立于 hermes |
| Lex 工具 | `tools/lex_*.py` (8 个) | 通过 plugin 注册 |
| Legal 工具 | `tools/legal_*.py` (6 个) | 通过 plugin 注册 |
| Research 工具 | `tools/research_tool.py` | 通过 plugin 注册 |
| Skills | `skills/deep-research/`, `skills/lex-master-handoff/` | 纯 SKILL.md |
| Profiles | `profiles/lex-*.soul.md` (7 个) | 纯配置 |
| Docker 配置 | `docker/s6-rc.d/dashboard/run` | 容器启动脚本 |
| 测试 | `tests/test_*.py` (新增的) | 纯新增 |
| 文档 | `docs/plans/`, `CONTEXT.md` | 纯新增 |

**预估：~100 个文件，零冲突**

### Phase 3: 最小源码改动（Layer 2，需要改上游）

这些文件**必须**修改上游代码，无法通过 plugin 规避：

| 文件 | 改动 | 行数 | 说明 |
|---|---|---|---|
| `hermes_state.py` | +`coordinator_for` 列 | ~5 行 | DB schema，plugin 可做 migration 但查询逻辑在上游 |
| `agent/conversation_compression.py` | +传播 `coordinator_for` | ~15 行 | 无 compression hook |
| `gateway/kanban_runner.py` | +session wake 逻辑 | ~100 行 | 无 wake hook |
| `hermes_cli/kanban_db.py` | +kanban DB 扩展 | ~50 行 | kanban 核心逻辑 |
| `run_agent.py` | +delivery gate | ~20 行 | 输出拦截 |
| `toolsets.py` | 无改动（plugin 已注册） | 0 行 | ✅ 已消除 |

**预估：~190 行改动，6 个文件**

### Phase 4: 测试验证

每个阶段迁移后：
1. `py_compile` 全部 Python 文件
2. `npm run build` WebUI
3. 容器内 smoke test：gateway 启动、工具注册、API 响应
4. 实际 dispatch 测试：lex-master → coordinator → kanban worker

### Phase 5: 生产切换

1. `lex-hermes-plugin` 分支验证通过后
2. 重命名 `lex-hermes` → `lex-hermes-legacy`
3. 重命名 `lex-hermes-plugin` → `lex-hermes`
4. 更新容器镜像

## 风险

- Phase 2 的 `vendor/lexitool/` 需要确保 pip install 在新容器里正常工作
- Phase 3 的 kanban_runner.py 改动最大，需要仔细测试
- 上游频繁更新时，Phase 3 的 6 个文件需要定期 merge

## 维护成本对比

| 方案 | 上游更新时需要处理的文件数 |
|---|---|
| 当前 `lex-hermes` | 158 个修改文件 |
| 迁移后 `lex-hermes-plugin` | 6 个修改文件 |
