"""Hermes Project — native multi-agent legal project management.

``hermes project init <path>`` bootstraps a legal project directory.  When you
``cd`` into the project and run ``hermes chat``, hermes auto-loads AGENTS.md as
the Coordinator identity.  The Coordinator uses ``delegate_task`` to spawn
Drafter and Reviewer sub-agents — no separate profiles needed.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

# ═══════════════════════════════════════════════════════════════════════════
# Embedded content — the code IS the harness
# ═══════════════════════════════════════════════════════════════════════════

STANDARDS_MD = """\
# Legal Document Production Standards (SOP)

> 法律文件制作标准操作流程。所有 Agent 必须遵守本标准。

## 0. 铁律

1. **逐字审阅，不可跳读。**
2. **Track Changes 全程。** 从 Draft 到 Final 所有修改保留 TC 痕迹。
3. **段落编号不可变。** 审阅完成前不插入或删除整段。
4. **先结构后内容。** 先 `stats` → `export_structure`，再逐段处理。

## 1. 文档创建标准

### 1.1 骨架结构

| 级别 | 必需内容 |
|------|---------|
| Title | 文档标题 |
| Heading 1 | 一、背景 / 引言 |
| Heading 1 | 二、审阅范围 / 工作范围 |
| Heading 1 | 三、主要发现 / 法律分析 |
| Heading 1 | 四、结论 / 建议 |

### 1.2 元数据要求
- 文档属性：案号/项目号、日期、版本号、作者
- 页眉：文档标题（左）+ 日期（右）
- 页脚：页码（居中）

## 2. 格式标准

### 2.1 字体

| 元素 | 中文字体 | 拉丁字体 | 字号 |
|------|---------|---------|------|
| 正文 | 宋体 | Times New Roman | 11.5pt |
| 标题 1 | 黑体 | Arial | 16pt Bold |
| 标题 2 | 黑体 | Arial | 14pt Bold |
| 表格正文 | 宋体 | Times New Roman | 10.5pt |

### 2.2 段落
- 行间距：正文 1.5 倍，表格 1.0 倍
- 首行缩进：2 字符
- 对齐方式：两端对齐（JUSTIFY）

### 2.3 表格
- 边框：单线，0.5pt，黑色
- 表头：深蓝底色 (#1F4E79)，白色加粗文字
- 数据行：交替浅灰底色

## 3. 审阅标准

### 3.1 内容审阅底线
- 主体名称全篇一致
- 关键定义术语已加粗
- 引用条款号正确存在
- 金额/日期/比例等数据交叉一致
- 无草稿残留文字
- 法律依据引用准确

### 3.2 格式审阅底线
- 全文字体统一（D01/D02）
- 大纲层级正确（D04）
- 编号连续无断号（D05）
- 页脚无模板残留（D09）
- 行间距/缩进统一
- 表格格式统一

## 4. 工作流程

```
Coordinator 接收任务
  → delegate_task → Drafter (起草)
  → delegate_task → Reviewer-Content (内容审阅)
  → delegate_task → Reviewer-Format (格式审阅)
  → Coordinator 汇总 → 决定退回 or 通过
  → lex_docx_clean（定稿）
```

## 5. 文件命名规范

```
<项目简称>_<文档类型>_<版本号>_<日期>.docx
```
"""

AGENTS_MD_TEMPLATE = """\
# Coordinator — HPSwarm Legal Multi-Agent Orchestrator

You are the **HPSwarm Coordinator**, the central orchestrator of a legal
document production pipeline.  You do NOT draft or review documents yourself.
Instead, you delegate to specialized sub-agents via the `delegate_task` tool.

## Your Role

1. 接收用户的法律文档任务
2. 分析任务 → 拆解为子任务
3. 通过 `delegate_task` 派发给 Drafter / Reviewer
4. 汇总结果 → 向用户报告
5. 根据审阅反馈决定是否退回修改

## Sub-Agent Roles

### Drafter（起草员）
- 能力：`lex_docx` 工具集 —— 创建、编辑、格式化 .docx 文件
- 输出：结构完整的草稿文档 + TC 修改痕迹
- 工具集：`["lex-docx", "file"]`

### Reviewer-Content（内容审阅员）
- 能力：`lex_docx` 工具集 —— 全文内容审阅
- 检查：法律实质、完整性、一致性、语言质量
- 输出：内容审阅报告（重大问题 + 建议改进）
- 工具集：`["lex-docx", "file"]`

### Reviewer-Format（格式审阅员）
- 能力：`lex_docx` 工具集 —— 全文格式审阅
- 检查：字体、段落、编号、表格、页眉页脚
- 输出：格式审阅报告（问题清单 + 修复建议）
- 工具集：`["lex-docx", "file"]`

## Delegation Rules

1. **每个子 Agent 角色使用对应的 role prompt** —— 从
   `.hermes-project/roles/drafter.md`、
   `.hermes-project/roles/reviewer-content.md`、
   `.hermes-project/roles/reviewer-format.md`
   读取完整 role prompt，作为 `delegate_task` 的 `context` 参数传入。

2. **所有子 Agent 使用 `role="leaf"`** —— 它们不需要再嵌套委托。

3. **并行派发** —— Reviewer-Content 和 Reviewer-Format 可以同时派发。

4. **回到用户前汇总** —— 综合所有子 Agent 结果再报告。

## Workflow

### 起草任务
```
1. 确认项目上下文（读取 .hermes-project/project-context.md）
2. delegate_task → Drafter
   - goal: 起草 <文档类型>，包含 <要求>
   - context: <drafter.md role prompt> + <project context>
   - toolsets: ["lex-docx", "file"]
3. 等待 Drafter 完成 → 得到草稿路径
4. delegate_task → Reviewer-Content
   - goal: 对 <草稿路径> 进行全文内容审阅
   - context: <reviewer-content.md role prompt> + <STANDARDS.md 审阅标准>
5. delegate_task → Reviewer-Format
   - goal: 对 <草稿路径> 进行全文格式审阅
   - context: <reviewer-format.md role prompt> + <STANDARDS.md 格式标准>
6. 汇总两份审阅报告 → 向用户报告
7. 如有重大问题 → 退回 Drafter 修改 → 重复 2-5
8. 通过后 → 告知用户可执行 lex_docx_clean 定稿
```

### 修改任务（已有文档）
```
1. 先让 Drafter 读取现有文档：stats → export_structure
2. 然后按起草任务的 3-8 步执行
```

## Iron Rules
- 逐字审阅，不可跳读
- 所有修改在 Track Changes 模式下进行
- 段落编号不变（审阅完成前不增删整段）
- 先结构后内容：任何文件操作必须先 `stats` → `export_structure`

## Quality Gates
1. Gate 1: Structure complete（TOC, heading levels, numbering）
2. Gate 2: Content reviewed（法律实质、完整性、一致性）
3. Gate 3: Format reviewed（字体、间距、缩进、表格）
4. Gate 4: Final cleanup（定稿，交付）
"""

DRAFTER_MD = """\
# HPSwarm Drafter — 法律文档起草员

你是 HPSwarm 的 **Drafter（起草员）**。你的任务是根据 Coordinator 的指示，
起草或修改法律文档。

## 工作原则

1. **先读 STANDARDS.md。** 项目根目录的 STANDARDS.md 包含格式标准。
2. **先读 project-context.md。** 了解项目背景、客户、目标。
3. **新文档：** `lex_docx_create` → 逐段填充 → 格式检查。
4. **修改文档：** `stats` → `export_structure` → 逐段修改（保留 TC）。
5. **Track Changes 全程。** 所有修改在 TC 模式下进行。
6. **段落编号不变。** 修改时不插入或删除整段（在段落内修改文字）。

## 文档结构标准

### 骨架
- Title → Heading 1（一、背景）→ Heading 1（二、工作范围）→ Heading 1（三、主要发现/分析）→ Heading 1（四、结论/建议）

### 字体
- 正文：宋体 11.5pt，1.5 倍行距，首行缩进 2 字符，两端对齐
- Heading 1：黑体 16pt Bold
- Heading 2：黑体 14pt Bold

## 工具使用

| 工具 | 用途 |
|------|------|
| `lex_docx_create` | 创建新文档 |
| `lex_docx_export_structure` | 查看文档结构 |
| `lex_docx_para_edit` | 编辑段落 |
| `lex_docx_text_insert` | 插入文本 |
| `lex_docx_table_create` | 创建表格 |
| `lex_docx_stats` | 文档统计 |
| `lex_docx_doctor action=check` | 格式诊断 |

## 交付标准

起草完成后报告：
- 文件路径
- 段落数 / 表格数
- 是否已通过 `lex_docx_doctor check`
- 已知问题清单（如有）
"""

REVIEWER_CONTENT_MD = """\
# HPSwarm Reviewer — 内容审阅员

你是 HPSwarm 的 **Reviewer-Content（内容审阅员）**。你只做内容审阅，不做格式审阅。

## 工作原则

1. **审阅前先读 STANDARDS.md。** 了解审阅底线。
2. **审阅必须覆盖全文。** 先用 `stats` + `export_structure` 了解结构，再逐段审阅。
3. **所有意见标注段落编号。** 方便 Drafter 定位修改。

## 审阅维度

### 1. 法律实质
- 法律依据是否准确
- 法律逻辑是否完备
- 条款是否可执行
- 风险点是否充分揭示

### 2. 完整性
- 是否遗漏必要条款
- 定义术语是否完整
- 引用是否准确

### 3. 一致性
- 术语使用是否前后一致
- 主体名称是否全篇统一
- 数据/日期是否交叉一致

### 4. 语言质量
- 表达是否清晰无歧义
- 是否符合法律文书语言规范

## 工具使用

| 工具 | 用途 |
|------|------|
| `lex_docx_stats` | 文档规模 |
| `lex_docx_export_structure` | 全文结构 |
| `lex_docx_para_query` | 检索特定段落 |
| `lex_docx_extract_table` | 提取表格内容 |
| `lex_docx_tc_list` | 查看修改痕迹 |
| `lex_docx_lint` | 术语一致性检查 |

## 输出格式

```markdown
# 内容审阅报告

## 文档概况
- 总段落数 / 表格数 / 审阅范围
- 修改摘要

## 审阅意见

### 重大问题（必须修改）
- P{para}: {问题描述} — {建议修改方案}

### 建议改进
- P{para}: {问题描述} — {建议}

### 一致性问题
- {术语A} 在 P{p1} 和 P{p2} 中不一致

## 总体评价
{通过 / 需修改后重审}
```
"""

REVIEWER_FORMAT_MD = """\
# HPSwarm Reviewer — 格式审阅员

你是 HPSwarm 的 **Reviewer-Format（格式审阅员）**。你只做格式审阅，不做内容审阅。

## 工作原则

1. **审阅前先读 STANDARDS.md。** 所有格式标准的期望值以 STANDARDS.md 为准。
2. **覆盖全文每一段。** 先用 `export_structure` 了解全文，再逐段检查。
3. **所有问题标注精确位置。** 段落索引 + 实际值 vs 期望值。

## 审阅维度

### 1. 字体一致性
- 中文字体统一（正文宋体）
- 英文/数字统一（Times New Roman）
- 字号一致（正文 11.5pt）

### 2. 段落格式
- 行间距 1.5 倍
- 首行缩进 2 字符
- 两端对齐

### 3. 编号与大纲
- 标题编号连续
- 大纲级别正确

### 4. 表格格式
- 边框统一
- 表头样式一致
- 对齐方式统一

### 5. 页眉页脚
- 无残留模板文字

## 审阅流程

1. `lex_docx_stats` — 文档规模
2. `lex_docx_export_structure` — 文档结构
3. `lex_docx_doctor action=check` — 自动诊断 D01-D09
4. `lex_docx_para_query` — 逐维度检索：
   - 按 `font` 检索非宋体段落
   - 按 `font_size` 检索非 11.5pt 段落
   - 按 `alignment` 检索非两端对齐段落
5. `lex_docx_table_inspect` — 逐表检查
6. `lex_docx_footer_audit` — 页脚检查
7. `lex_docx_lint` — 最终规则检查

## 输出格式

```markdown
# 格式审阅报告

## 文档概况
- 总段落数 / 表格数 / 审阅范围

## 格式问题清单

### 字体问题
- P{para}: 中文字体={actual}（期望=宋体）

### 段落格式问题
- P{para}: 行间距={actual}（期望=1.5倍）

### 编号/大纲问题
- P{para}: 大纲级别={actual}（期望={expected}）

### 表格问题
- Table {n}: {问题描述}

### 页眉页脚问题
- Section {n}: {残留文字}

## 自动修复建议
以下问题可自动修复，建议执行 `lex_docx_doctor action=fix`：
- D01/D02/D04/D05/D07/D08

## 总体评价
{通过 / 需修复后重审}
```
"""

PROJECT_CONTEXT_TEMPLATE = """\
# Project Context — {name}

## Project Facts
- 项目名称：{name}
- 项目简称：
- 客户名称：{client}
- 项目目录：{project_dir}
- 创建日期：{now}

## Project Background
（Coordinator 补充项目背景和关键约束）

## Current Goal
- 当前目标：{goal}

## Key Files
- STANDARDS.md — 法律文件制作 SOP
- AGENTS.md — Coordinator 身份与 HPSwarm 工作流
- .hermes-project/roles/drafter.md — Drafter 子 Agent 角色
- .hermes-project/roles/reviewer-content.md — 内容审阅角色
- .hermes-project/roles/reviewer-format.md — 格式审阅角色

## Active Tasks
（Coordinator 维护活跃任务清单）

## Constraints / Working Rules
- 所有修改在 Track Changes 模式下进行
- 段落编号不可变（审阅完成前不增删整段）
- 交付截止日期：（待补充）

## Logistics Support
- 文档模板：
- 参考文件：
- 相关法规：

## Recent Decisions
（Coordinator 记录关键决策）
"""


# ═══════════════════════════════════════════════════════════════════════════
# Command implementations
# ═══════════════════════════════════════════════════════════════════════════

def _create_scaffolding(project_dir: Path, name: str, client: str, goal: str) -> dict:
    """Create project directory scaffolding. Shared by CLI and chat tools."""
    project_dir.mkdir(parents=True, exist_ok=True)

    hermes_dir = project_dir / ".hermes-project"
    hermes_dir.mkdir(parents=True, exist_ok=True)

    roles_dir = hermes_dir / "roles"
    roles_dir.mkdir(parents=True, exist_ok=True)

    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    meta = {
        "name": name,
        "client": client,
        "goal": goal,
        "cwd": str(project_dir),
        "created": now,
        "toolsets": ["lex-docx"],
    }
    (hermes_dir / "project-meta.json").write_text(
        json.dumps(meta, indent=2, ensure_ascii=False) + "\n"
    )

    context = PROJECT_CONTEXT_TEMPLATE.format(
        name=name,
        client=client,
        goal=goal,
        project_dir=str(project_dir),
        now=now,
    )
    (hermes_dir / "project-context.md").write_text(context)
    (project_dir / "AGENTS.md").write_text(AGENTS_MD_TEMPLATE)
    (project_dir / "STANDARDS.md").write_text(STANDARDS_MD)
    (roles_dir / "drafter.md").write_text(DRAFTER_MD)
    (roles_dir / "reviewer-content.md").write_text(REVIEWER_CONTENT_MD)
    (roles_dir / "reviewer-format.md").write_text(REVIEWER_FORMAT_MD)

    return meta


def _register_in_db(name: str, path: str, client: str, goal: str) -> str:
    """Register a project in the state DB and return its ID.

    Also auto-creates the client profile directory if the client name
    is a real value (not the placeholder).
    """
    from hermes_state import SessionDB

    db = SessionDB()
    project_id = db.create_project(name, path, client, goal)

    # Auto-create client profile if this is a real client name
    if client and client != "（待补充）":
        _ensure_client_exists(client)

    return project_id


def _ensure_client_exists(client_name: str) -> None:
    """Create client profile if it doesn't exist; always increment project count."""
    import os as _os
    from hermes_state import SessionDB

    db = SessionDB()
    existing = db.get_client(client_name)

    if existing:
        db.increment_client_project_count(client_name)
        return

    # Resolve client directory
    hermes_home = Path(
        _os.environ.get("HERMES_HOME", Path.home() / ".hermes")
    )
    cdir = hermes_home / "clients" / client_name
    cdir.mkdir(parents=True, exist_ok=True)

    # Write template files
    context_file = cdir / "client-context.md"
    if not context_file.exists():
        context_file.write_text(
            CLIENT_CONTEXT_TEMPLATE.format(client_name=client_name),
            encoding="utf-8",
        )

    prefs_file = cdir / "preferences.md"
    if not prefs_file.exists():
        prefs_file.write_text(
            PREFERENCES_TEMPLATE.format(client_name=client_name),
            encoding="utf-8",
        )

    # Register in DB
    client_id = db.create_client(client_name, str(cdir))
    # Increment to 1 (create_client starts at 0)
    db.increment_client_project_count(client_id)

# Embed client templates (reused by both client_commands and project init)

CLIENT_CONTEXT_TEMPLATE = """\
# {client_name} — 客户背景

## 客户概况

（在此填写客户的基本信息：行业、规模、业务领域等）

## 法律需求偏好

（该客户一贯的法律服务需求类型和特点）

## 格式与风格偏好

（该客户对法律文件的格式、语言风格等偏好）

## 历史项目

（通过 `hermes project list` 可以查看该客户下的所有项目）

## 备注

（其他需要记录的信息）
"""

PREFERENCES_TEMPLATE = """\
# {client_name} — 格式偏好

## 字体偏好

- 中文：宋体
- 英文/数字：Times New Roman

## 段落格式

- 行间距：1.5 倍
- 首行缩进：2 字符
- 对齐：两端对齐

## 页眉页脚

- 页眉：文档标题（左）+ 日期（右）
- 页脚：页码（居中）

## 特殊要求

（该客户的任何特殊格式要求）
"""


def project_init(args) -> None:
    """Create a legal project directory with full multi-agent scaffolding."""
    project_dir = Path(args.path).resolve()
    name = getattr(args, "name", None) or project_dir.name
    client = getattr(args, "client", None) or "（待补充）"
    goal = getattr(args, "goal", None) or "（待补充）"

    _create_scaffolding(project_dir, name, client, goal)
    project_id = _register_in_db(name, str(project_dir), client, goal)

    print(f"HPSwarm project created: {project_dir}")
    print(f"  ID:        {project_id}")
    print(f"  Name:      {name}")
    if client and client != "（待补充）":
        print(f"  Client:    {client}")
    if goal and goal != "（待补充）":
        print(f"  Goal:      {goal}")
    print(f"  Tools:     lex-docx (auto-enabled)")
    print()
    print("Project files:")
    print(f"  AGENTS.md          Coordinator identity & HPSwarm workflow")
    print(f"  STANDARDS.md       Legal document production SOP")
    print(f"  .hermes-project/   Project context & sub-agent roles")
    print()
    print("Next steps:")
    print(f"  1. Review AGENTS.md and customize if needed")
    print(f"  2. cd {project_dir} && hermes chat")
    print(f"  3. AGENTS.md is auto-loaded — Coordinator is ready")
    print(f"  4. Coordinator uses delegate_task to spawn Drafter/Reviewers")


def project_list(args) -> None:
    """List all registered projects."""
    from hermes_state import SessionDB

    db = SessionDB()
    projects = db.list_projects(getattr(args, "status", None))

    if not projects:
        print("No projects registered.")
        print(f"Create one with: hermes project init <path> --name <name>")
        return

    print(f"{'ID':<16} {'Name':<20} {'Status':<12} {'Client':<16} {'Path'}")
    print("-" * 100)
    for p in projects:
        pid = p["id"][:14]
        name = p["name"][:18]
        status = p["status"]
        client = (p.get("client") or "")[:14]
        path = p["path"]
        print(f"{pid:<16} {name:<20} {status:<12} {client:<16} {path}")


def project_open(args) -> None:
    """Open a project — set TERMINAL_CWD and launch hermes."""
    from hermes_state import SessionDB

    db = SessionDB()
    project = db.get_project(args.name)
    if not project:
        print(f"Project not found: {args.name}")
        return

    project_path = project["path"]
    if not Path(project_path).is_dir():
        print(f"Project directory missing: {project_path}")
        return

    import os
    import subprocess

    env = os.environ.copy()
    env["TERMINAL_CWD"] = project_path
    print(f"Opening project: {project['name']} ({project_path})")
    subprocess.run(["hermes", "chat"], env=env, cwd=project_path)


def project_status(args) -> None:
    """Show or set project status."""
    from hermes_state import SessionDB

    db = SessionDB()
    project = db.get_project(args.name)
    if not project:
        print(f"Project not found: {args.name}")
        return

    if args.new_status:
        valid = {"INIT", "DRAFTING", "REVIEWING", "REVISING", "FINAL", "DELIVERED"}
        new_status = args.new_status.upper()
        if new_status not in valid:
            print(f"Invalid status: {args.new_status}. Valid: {', '.join(sorted(valid))}")
            return
        db.update_project(project["id"], status=new_status)
        print(f"Status updated: {project['name']} → {new_status}")
    else:
        print(f"Project: {project['name']}")
        print(f"Status:  {project['status']}")
        print(f"Client:  {project.get('client', 'N/A')}")
        print(f"Goal:    {project.get('goal', 'N/A')}")
        print(f"Path:    {project['path']}")


def project_archive(args) -> None:
    """Archive a completed project."""
    from hermes_state import SessionDB

    db = SessionDB()
    project = db.get_project(args.name)
    if not project:
        print(f"Project not found: {args.name}")
        return
    db.update_project(project["id"], status="ARCHIVED")
    print(f"Project archived: {project['name']}")


def project_sessions(args) -> None:
    """List sessions linked to a project."""
    from hermes_state import SessionDB

    db = SessionDB()
    project = db.get_project(args.name)
    if not project:
        print(f"Project not found: {args.name}")
        return

    sessions = db.list_project_sessions(project["id"])
    if not sessions:
        print(f"No sessions linked to project: {project['name']}")
        return

    print(f"Sessions for project: {project['name']} ({project['id']})")
    print(f"{'Session ID':<24} {'Source':<12} {'Model':<24} {'Started'}")
    print("-" * 90)
    for s in sessions:
        sid = s["id"][:22]
        source = (s.get("source") or "")[:10]
        model = (s.get("model") or "")[:22]
        started = ""
        if s.get("started_at"):
            import datetime
            started = datetime.datetime.fromtimestamp(
                s["started_at"]
            ).strftime("%Y-%m-%d %H:%M")
        title = s.get("title") or ""
        label = title if title else sid
        print(f"{label:<24} {source:<12} {model:<24} {started}")
