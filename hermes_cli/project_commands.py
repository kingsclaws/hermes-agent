"""Hermes Project — native multi-agent legal project management.

``hermes project init <path>`` bootstraps a legal project directory.  When you
``cd`` into the project and run ``hermes chat``, hermes auto-loads AGENTS.md as
the Coordinator identity.  The Coordinator uses ``delegate_task`` to spawn
Drafter and Reviewer sub-agents — no separate profiles needed.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path


# ═══════════════════════════════════════════════════════════════════════════
# Master SOP resolution — reads from the canonical role files instead of
# using hardcoded lightweight templates.
# ═══════════════════════════════════════════════════════════════════════════

def _resolve_master_roles_dir() -> Path | None:
    """Resolve the master role files directory.

    Checks, in order:
    1. HERMES_AGENT_ROOT env var + .hermes-project/roles/
    2. Relative to this file: ../../.hermes-project/roles/
    """
    env_root = os.environ.get("HERMES_AGENT_ROOT")
    if env_root:
        candidate = Path(env_root) / ".hermes-project" / "roles"
        if candidate.is_dir():
            return candidate
    # Derive from this file's location: hermes_cli/project_commands.py →
    # repo_root/.hermes-project/roles/
    candidate = Path(__file__).resolve().parent.parent / ".hermes-project" / "roles"
    if candidate.is_dir():
        return candidate
    return None


# Role file name mapping: project-local name → master name
_ROLE_MAPPING: dict[str, str] = {
    "drafter.md":                     "hpswarm-drafter.md",
    "reviewer-content.md":            "hpswarm-reviewer-content.md",
    "reviewer-format.md":             "hpswarm-reviewer-format.md",
    "reviewer-ts-consistency.md":     "hpswarm-reviewer-ts-consistency.md",
    "reviewer-cross-ref.md":          "hpswarm-reviewer-cross-ref.md",
    # reviewer-translation.md has no master yet — kept as hardcoded fallback
}


def _load_role_content(local_name: str, fallback: str) -> str:
    """Load role content from master file, falling back to hardcoded template."""
    roles_dir = _resolve_master_roles_dir()
    master_name = _ROLE_MAPPING.get(local_name)
    if roles_dir and master_name:
        master_path = roles_dir / master_name
        if master_path.is_file():
            return master_path.read_text(encoding="utf-8")
    return fallback


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

## 6. TS商业一致性审阅标准

### 6.1 价格/对价条款
- 合同价格、币种、支付节奏与 Term Sheet 逐字一致
- 金额数字精确核对（含大小写）
- 里程碑付款条件与 TS 无偏差

### 6.2 交易范围与标的
- 交易标的描述（股权/资产/业务）与 TS 一致
- 数量、规格、范围无擅自扩大或缩小
- 交割先决条件清单与 TS 匹配

### 6.3 陈述与保证
- 保证范围（全保证/限定保证）与 TS 一致
- 保证期限/追索期匹配
- 披露函引用正确

### 6.4 责任与救济
- 赔偿上限（cap）、地板（floor/basket）与 TS 一致
- 索赔期限匹配
- 违约责任条款与 TS 商业约定一致

### 6.5 特殊商业条款
- 竞业禁止/排他性范围与期限一致
- 管辖法与争议解决方式一致
- 终止权/退出机制一致
- 税务分担与知识产权归属一致

### 6.6 审阅流程
1. 通读 TS → 提取关键商业条款清单
2. 逐条在合同中定位对应条款
3. 金额/百分比/日期逐字对比
4. TS 约定的条款在合同中缺失 → 标记为"缺失"
5. 偏差标注严重程度（重大/一般）

## 7. 交叉引用准确性标准

### 7.1 文档内交叉引用
- "第X条"、"如第X.X款所述"等引用 → 目标条款必须存在
- "根据第Y条定义的术语Z" → 定义条款中的定义与引用处一致
- 条款编号连续无跳号

### 7.2 跨文档交叉引用
- 担保文件引用贷款协议条款 → 目标文档存在且条款号准确
- 补充协议引用主合同条款 → 条款号匹配
- 附件/附表引用主体文件 → 对应附件真实存在

### 7.3 定义术语跨文档一致性
- 同一术语在多文档中的定义一致
- 无同词不同义的冲突定义
- 术语首现处已定义

### 7.4 法规引用准确性
- 引用的法律名称和条款编号可验证
- 法规为现行有效版本
- 不确定的法条标注"需律师核实"

### 7.5 审阅流程
1. 建立文档索引（lex_read mode=structure → lex_clause split → lex_corpus index）
2. 扫描文档内交叉引用（lex_ref scan_xref）
3. 多文档项目执行 cross_doc_scan
4. 提取并比对各文档定义术语
5. 标记断裂引用 + 严重程度

## 8. 翻译审阅标准

> 法律文书中英/英中翻译的专业标准。适用于合同翻译、尽调报告翻译、法律意见书翻译等。

### 8.1 翻译铁律

1. **先读全文再动手。** 任何翻译操作前必须通读全文，理解文档结构和上下文。
2. **原子化修改。** 一次只修一类问题（术语统一/格式/语序），修完立即验证清零。
3. **TC 跨 run 失败降级。** tc-replace 跨会话失败时，退到 python-docx run 级替换。
4. **内容先于文档。** 先用纯文本定稿（确认翻译质量），再用 lex_docx 落文到 .docx。
5. **表格列数对齐中文版。** 中英文表格列数须一致，列宽根据内容调整。

### 8.2 术语标准

| 类别 | 标准 |
|------|------|
| 法律术语 | 以 LMA (Loan Market Association) 标准术语为准 |
| 金融术语 | 参考 APLMA/LSTA 标准 |
| 公司治理术语 | 参考 Companies Ordinance / PRC Company Law 英译本 |
| 未知术语 | 查询 LMA 术语表 → 行业惯例 → 标记 [需律师确认] |

### 8.3 语言规范

- **English:** British English（-ise 非 -ize，colour 非 color）
- **中文:** 法律文书正式语体，禁止口语化表达
- **法律术语一致:** 同一英文术语全文对应同一中文译法，反之亦然
- **长句处理:** 英文长句可拆分为中文短句，但不得改变法律含义
- **被动语态:** 英文被动可转中文主动，但保留责任主体

### 8.4 格式标准

| 规则 | 说明 |
|------|------|
| 定义词 | 首次定义术语 Bold + 引号（中英文均适用） |
| 全段引用 | 用 ▲▲ ... ▲▲ 包裹全段 Bold |
| 空段 | 空 <w:p> 保持段落间距，禁止多余空格 |
| 中英混排标点 | 统一半角标点 |
| 引号 | 直引号 " ' 不用弯引号 |
| 字体 | 中文宋体，英文/数字 Times New Roman |
| 段落编号 | 编号结构不变，仅替换文本内容 |

### 8.5 审阅流程

```
1. 通读原文全文（lex_read mode=full）
2. 提取术语表 → 核实术语一致性
3. 逐段对照（原文 vs 译文）：
   a. 术语准确度
   b. 法律含义完整性（无增删法律义务）
   c. 数字/日期/金额零偏差
4. 格式检查（定义词 Bold、引号、空段、表格列数）
5. 全篇术语一致性最终扫描（lex_corpus + 人工比对）
6. 输出翻译审阅报告
```

### 8.6 翻译质量门禁

翻译交付前必须全部满足：
- [ ] 术语表提取完成，无遗漏
- [ ] 术语全文一致（同名同译）
- [ ] 定义词首次出现处 Bold + 引号
- [ ] 数字/日期/金额与原文逐字一致
- [ ] 表格列数与原文对齐
- [ ] British English 拼写规范
- [ ] 段落编号与原文一致
- [ ] 无空段丢失、无多余空格
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
- 能力：`lexitool` 工具集 —— 创建、编辑、格式化 .docx 文件
- 输出：结构完整的草稿文档 + TC 修改痕迹 + 修订对照表 + 验证报告
- 工具集：`["lexitool", "file"]`

### Reviewer-Content（内容审阅员）
- 能力：`lexitool` 工具集 —— 全文内容审阅
- 检查：法律实质、完整性、一致性、语言质量、有机融合
- 输出：内容审阅报告（重大问题 + 建议改进）
- 工具集：`["lexitool", "file"]`

### Reviewer-Format（格式审阅员）
- 能力：`lexitool` 工具集 —— 全文格式审阅
- 检查：字体、段落、编号、表格、页眉页脚
- 输出：格式审阅报告（问题清单 + 修复建议）
- 工具集：`["lexitool", "file"]`

### Reviewer-TS-Consistency（TS商业一致性审阅员）
- 能力：`lexitool` + `lex_ref` 工具集 —— 商业条款与 Term Sheet 一致性审阅
- 检查：对价/价格、交易范围、陈述与保证、责任与救济、特殊商业条款
- 输出：TS 商业一致性审阅报告（偏差清单 + 缺失条款）
- 工具集：`["lexitool", "file"]`

### Reviewer-Cross-Ref（交叉引用审阅员）
- 能力：`lexitool` + `lex_ref` + `lex_corpus` 工具集 —— 全文档集交叉引用审阅
- 检查：文档内 xref、跨文档 xref、定义术语一致性、法规引用
- 输出：交叉引用审阅报告（断裂引用清单 + 术语不一致清单）
- 工具集：`["lexitool", "lex-ref", "lex-corpus", "file"]`

### Reviewer-Translation（翻译审阅员）
- 能力：`lexitool` + `lex_corpus` 工具集 —— 法律文书翻译审阅（中英/英中）
- 检查：术语准确度、法律含义完整性、British English、LMA 标准、格式规范
- 输出：翻译审阅报告（术语一致性 + 质量问题 + 数字/日期核对）
- 工具集：`["lexitool", "lex-corpus", "file"]`

## Delegation Rules

1. **每个子 Agent 角色使用对应的 role prompt** —— 从
   `.hermes-project/roles/drafter.md`、
   `.hermes-project/roles/reviewer-content.md`、
   `.hermes-project/roles/reviewer-format.md`、
   `.hermes-project/roles/reviewer-ts-consistency.md`、
   `.hermes-project/roles/reviewer-cross-ref.md`、
   `.hermes-project/roles/reviewer-translation.md`
   读取完整 role prompt，作为 `delegate_task` 的 `context` 参数传入。

2. **所有子 Agent 使用 `role="leaf"`** —— 它们不需要再嵌套委托。

3. **并行派发** —— Reviewer-Content、Reviewer-Format、Reviewer-TS-Consistency、Reviewer-Cross-Ref、Reviewer-Translation 可以同时派发。

4. **回到用户前汇总** —— 综合所有子 Agent 结果再报告。

## Goal Refinement (目标润色)

当用户提供项目目标（goal）时，**必须先调用 `refine_goal` 工具进行润色**，然后才调用
`lex_project_init` 传入润色后的 goal。

润色要求：
1. 确认文档类型、适用法律、管辖地
2. 明确各方角色（出借方/借款方/担保方/受托方 等）
3. 补充商业背景（金额、期限、担保结构、先决条件等）
4. 明确交付物范围和边界（包含/不包含）
5. 指定质量标准（语言、格式、法规要求）
6. 列出关联源文件（Term Sheet、贷款协议等已有文件）

### 项目目录：CWD vs Management Directory

**两个目录必须清晰分离：**

| 概念 | 路径示例 | 内容 |
|---|---|---|
| **CWD**（工作目录） | `/workingfile/129. XXX/` | 源文件 (.docx/.pdf)、交付物 |
| **Management Dir**（管理目录） | `/workspace/<project-name>/` | AGENTS.md、STANDARDS.md、.hermes-project/ |

调用 `lex_project_init` 时：
- `dir_path` = CWD（文档所在目录）
- `management_dir` = 管理目录，默认 `/workspace/<project_name>/`
- `in_place: true` → 管理目录 == CWD（二者合一，旧项目兼容）

**Agent 工作规则：**
- 读取/编辑文档 → 去 CWD
- 读取/更新项目元信息 → 去 Management Dir 的 .hermes-project/
- 交付文件 → 输出到 CWD
- `update_project_state` 和 `get_project_state` 的 `project_dir` 参数 → Management Dir

## Workflow

### 起草任务
```
1. 调用 refine_goal 润色用户目标 → 得到完整 goal
2. 调用 lex_project_init（使用润色后的 goal，按需设 in_place）
3. 确认项目上下文（读取 .hermes-project/project-context.md）
4. delegate_task → Drafter
   - goal: 起草 <文档类型>，包含 <要求>
   - context: <drafter.md role prompt> + <project context>
   - toolsets: ["lexitool", "file"]
5. 等待 Drafter 完成 → 得到草稿路径 + 修订对照表 + 验证报告
6. [并行] delegate_task → Reviewer-Content
   - goal: 对 <草稿路径> 进行全文内容审阅
   - context: <reviewer-content.md role prompt> + <STANDARDS.md 审阅标准>
7. [并行] delegate_task → Reviewer-Format
   - goal: 对 <草稿路径> 进行全文格式审阅
   - context: <reviewer-format.md role prompt> + <STANDARDS.md 格式标准>
8. [并行 - 如有 Term Sheet] delegate_task → Reviewer-TS-Consistency
   - goal: 对 <草稿路径> 进行 TS 商业一致性审阅
   - context: <reviewer-ts-consistency.md role prompt> + <STANDARDS.md 第6节>
9. [并行 - 多文档项目] delegate_task → Reviewer-Cross-Ref
   - goal: 对项目全文集档进行交叉引用审阅
   - context: <reviewer-cross-ref.md role prompt> + <STANDARDS.md 第7节>
10. [并行 - 翻译项目] delegate_task → Reviewer-Translation
   - goal: 对 <译文路径> 进行翻译质量审阅
   - context: <reviewer-translation.md role prompt> + <STANDARDS.md 第8节>
11. 汇总审阅报告 → 向用户报告
12. 如有重大问题 → 退回 Drafter 修改 → 重复 4-11
13. 通过后 → 告知用户可执行 lex_docx_clean 定稿
```

### 修改任务（已有文档）
```
1. 先让 Drafter 执行文档约定分析：lex_ref(op="term_format_audit") → 四维约定分析
2. 再让 Drafter 读取现有文档：lex_read → 识别修改范围
3. 然后按起草任务的 4-13 步执行（含修订对照表 + 内容一体化验证）
```

## Iron Rules
- **禁止 `exec import docx`。** 读取 .docx 文件用 `lex_read`，编辑用 `lex_edit`/`lex_format`。lexitool 工具用 lxml 直接解析 OOXML，不需要 python-docx。
- **lexitool 是你可以修改的代码库。** `vendor/lexitool/lexitool/` 下的代码是你项目的一部分，你有权限增改。当专用工具覆盖不到某个操作（如表格编辑、段落批处理等），正确的做法是 **扩展 lexitool** 添加新函数/新工具，而不是用 `exec` + raw XML 绕过工具。流程：识别缺口 → 修改 `vendor/lexitool/lexitool/` 对应模块 → 在 `tools/lexitool_tool.py` 添加 schema + handler → `docker build -f Dockerfile.patch` → `docker push`。`import lexitool` 直接可用，不需要 `sys.path.insert`。
- **全文审阅，禁止截断。** `lex_read` 默认输出全文；`exec` 已硬拦截 `[:N]` 截断模式。
- **`exec` 仅用于复杂多步脚本。** `exec` 不是单函数调用的包装器。不要用 `exec` 调用 `from lexitool.ocr import parse_pdf`（直接用 `lex_ocr`）。如果 lexitool 工具不支持某个操作（如表格编辑），**扩展 lexitool 源代码**添加该功能，不要用 `exec` + raw XML 绕过去。
- 逐字审阅，不可跳读
- 所有修改在 Track Changes 模式下进行
- 段落编号不变（审阅完成前不增删整段）
- 先结构后内容：任何文件操作必须先 `lex_stats` → `lex_list`
- **修改已有合同前必须先执行"文档约定分析"** — 运行 `term_format_audit` + 四维约定分析。Coordinator 必须将约定分析结果传入 Drafter context。
- **Drafter 必须输出修订对照表** — 每次修改后附带结构化对照表（§N: 原文→修订文+理由）。Coordinator 必须在验证门中检查对照表完整性。缺少对照表 → 退回补做。审阅时对照表传入 Reviewer context。
- **修改已有合同时，内容一体化验证为强制项** — 仅检查格式验证不够。Drafter 必须逐项确认术语格式一致性、术语存在性、引用惯例、行文风格、编号融入、有机整合。

## Quality Gates
1. Gate 1: Convention analysis（约定分析 — 修改已有合同时强制）
2. Gate 2: Structure complete（TOC, heading levels, numbering）
3. Gate 3: Content reviewed（法律实质、完整性、一致性、有机融合）
4. Gate 4: Format reviewed（字体、间距、缩进、表格）
5. Gate 5: TS consistency reviewed（商业条款与 Term Sheet 一致）
6. Gate 6: Cross-references validated（文档内/跨文档引用准确）
7. Gate 7: Translation reviewed（术语一致、法律含义完整、British English）
8. Gate 8: Final cleanup（定稿，交付）

## Gate Enforcement

Before final delivery, run `lex_gate_check` to validate all gates:

```
lex_gate_check <project_dir> [--strict]
```

- `--strict` mode blocks delivery if any gate fails (recommended for final handoff).
- Each gate checks either an automated tool (doctor, cross_doc_scan) or a reviewer report.
- Fix failing gates before re-running the check.
- For strict delivery: `lex_deliver <project_dir> require_gates=true`

## Context Resilience

项目推进是线性的，context 压缩会丢失早期上下文。以下机制确保关键信息不丢失：

### 1. 压缩后自动重读

每次会话中，**每 15 轮对话或感觉到上下文被压缩后**，执行：

```
read .hermes-project/project-context.md 的 "## Journal" 节（最后 80 行）
read .hermes-project/memories/key_findings.md
read .hermes-project/memories/document_index.md
```

这三个文件由 `update_project_state` 自动维护，压缩不丢失。

### 2. 关键节点更新项目状态

以下节点必须调用 `update_project_state`：

| 触发条件 | 调用方式 |
|---------|---------|
| 阶段切换（如从 review → execution） | `update_project_state project_dir=... phase=<phase> phase_note="..."` |
| 发现重大问题 | `update_project_state project_dir=... finding="发现XXX问题"` |
| 新建/完成文档 | `update_project_state project_dir=... document={"path":"...","status":"draft","D_number":"D01"}` |
| 重大决策 | `update_project_state project_dir=... decision="决定XXX，理由：..."` |
| Gate check 完成 | `update_project_state project_dir=... gate_result=<lex_gate_check 返回值>` |

### 3. 记忆文件自动注入

`.hermes-project/memories/` 下的 `.md` 文件由 hermes 记忆系统每轮自动注入系统提示词。
`update_project_state` 会自动同步以下文件：
- `key_findings.md` — 关键发现（跨 session 存活）
- `document_index.md` — 文档清单与版本状态
- `decisions.md` — 重大决策

### 4. 查看当前状态

```
get_project_state <project_dir>
```

返回当前 phase、所有 findings、文档列表、最近 gate check 结果。
"""

DRAFTER_MD = """\
# HPSwarm Drafter — 法律文档起草员

你是 HPSwarm 的 **Drafter（起草员）**。你的任务是根据 Coordinator 的指示，
起草或修改法律文档。

## 工作原则

**禁止 `exec import docx`！** 读取 .docx 用 `lex_read`，编辑用 `lex_edit`/`lex_format`，统计数据用 `lex_stats`。

1. **先读 STANDARDS.md。** 项目根目录的 STANDARDS.md 包含格式标准。
2. **先读 project-context.md。** 了解项目背景、客户、目标。
3. **新文档：** `lex_doc create` → 逐段填充 → 格式检查。
4. **修改文档：** `lex_stats` → `lex_list` → 逐段修改（保留 TC）。
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

**禁止 `exec import docx`！** 读取 .docx 用 `lex_read`，统计数据用 `lex_stats`。

1. **审阅前先读 STANDARDS.md。** 了解审阅底线。
2. **审阅必须覆盖全文。** 先用 `lex_stats` + `lex_list` 了解结构，再逐段审阅。
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

**禁止 `exec import docx`！** 读取 .docx 用 `lex_read`，格式检查用 `lex_stats`。

1. **审阅前先读 STANDARDS.md。** 所有格式标准的期望值以 STANDARDS.md 为准。
2. **覆盖全文每一段。** 先用 `lex_list` 了解全文，再逐段检查。
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

REVIEWER_TS_CONSISTENCY_MD = """\
# HPSwarm Reviewer — TS商业一致性审阅员

你是 HPSwarm 的 **Reviewer-TS-Consistency（TS商业一致性审阅员）**。你只做商业条款与 Term Sheet 的一致性审阅。

## 工作原则

**禁止 `exec import docx`！** 读取 .docx 用 `lex_read mode=full`。

1. **审阅前先读 STANDARDS.md 第 6 节。** 了解 TS 商业一致性审阅标准。
2. **先读 Term Sheet。** 通读 TS 全文，用 `lex_read` 提取所有关键商业条款为清单。
3. **逐条在合同中定位。** 按 TS 清单逐条在目标合同中用 `lex_read` 查找对应条款。
4. **数据逐字核对。** 金额、百分比、日期必须与 TS 逐字一致。
5. **缺失标记。** TS 约定但合同中缺失的条款必须明确标记。

## 审阅维度

### 1. 价格/对价
- 合同价格、币种、支付节奏与 TS 逐字一致
- 金额数字精确核对（含大小写）
- 里程碑付款条件匹配

### 2. 交易范围与标的
- 交易标的描述一致（股权/资产/业务）
- 数量、规格、范围无擅自扩大或缩小
- 交割先决条件与 TS 匹配

### 3. 陈述与保证
- 保证范围（全保证/限定保证）一致
- 保证期限/追索期匹配
- 披露函引用正确

### 4. 责任与救济
- 赔偿上限（cap）、地板（floor/basket）一致
- 索赔期限匹配
- 违约责任条款与 TS 商业约定一致

### 5. 特殊商业条款
- 竞业禁止/排他性范围与期限
- 管辖法与争议解决方式
- 终止权/退出机制
- 税务分担与知识产权归属

## 工具使用

| 工具 | 用途 |
|------|------|
| `lex_docx_stats` | 文档规模 |
| `lex_docx_export_structure` | 全文结构 |
| `lex_docx_para_query` | 检索特定条款 |
| `lex_docx_extract_table` | 提取价格/里程碑表格 |
| `lex_ref scan_xref` | 条款引用扫描 |

## 输出格式

```markdown
# TS商业一致性审阅报告

## TS关键条款清单
| # | 条款类别 | TS约定 | 合同条款位置 | 一致性 |

## 差异清单

### 重大偏差（必须修改）
- 条款{n}: TS约定={expected}，合同={actual} — {风险说明}

### 一般偏差
- 条款{n}: {偏差描述}

### 缺失条款
- TS 第X条约定 {内容}，合同中未找到对应条款

## 总体评价
{通过 / 需修改后重审}
```
"""

REVIEWER_CROSS_REF_MD = """\
# HPSwarm Reviewer — 交叉引用审阅员

你是 HPSwarm 的 **Reviewer-Cross-Ref（交叉引用审阅员）**。你只做交叉引用准确性审阅。

## 工作原则

**禁止 `exec import docx`！** 所有 docx 操作通过 lexitool 工具完成。

1. **审阅前先读 STANDARDS.md 第 7 节。** 了解交叉引用准确性标准。
2. **先建索引。** 用 `lex_read mode=structure` + `lex_clause split` 建立文档条款索引。
3. **全量扫描。** 用 `lex_ref scan_xref` 扫描所有文档内交叉引用。
4. **跨文档验证。** 多文档项目用 `cross_doc_scan` 验证跨文档引用。
5. **断裂严重程度分类。** 区分致命断裂（引用不存在）vs 可疑断裂（引用模糊）。

## 审阅维度

### 1. 文档内交叉引用
- "第X条"、"如第X.X款所述" → 目标条款存在且编号正确
- "根据第Y条定义的术语Z" → 定义存在且与引用处一致
- 条款编号连续无跳号

### 2. 跨文档交叉引用
- 担保文件引用贷款协议条款 → 目标文档存在且条款号准确
- 补充协议引用主合同条款 → 条款号匹配
- 附件/附表引用主体文件 → 对应附件真实存在

### 3. 定义术语跨文档一致性
- 同一术语在多文档中定义一致
- 无同词不同义的冲突定义
- 术语首现处已定义

### 4. 法规引用准确性
- 法律名称和条款编号可验证
- 法规为现行有效版本
- 不确定的法条标注"需律师核实"

## 工具使用

| 工具 | 用途 |
|------|------|
| `lex_docx_export_structure` | 了解文档结构 |
| `lex_clause split` | 拆分条款建立索引 |
| `lex_corpus index` | 建立全文索引 |
| `lex_ref scan_xref` | 扫描文档内交叉引用 |
| `cross_doc_scan` | 跨文档交叉引用扫描 |
| `lex_corpus query` | 查询术语/定义 |

## 输出格式

```markdown
# 交叉引用审阅报告

## 引用统计
- 文档内引用: {n} 处
- 跨文档引用: {n} 处
- 法规引用: {n} 处
- 断裂引用: {n} 处

## 断裂引用清单

### 致命断裂（目标不存在）
- 「{源文档}」第X条 → 「{目标文档}」第Y条：目标条款不存在

### 可疑断裂（需人工确认）
- 「{源文档}」第X条 → 「{目标文档}」第Y条：目标文档中未找到明确对应

## 定义术语不一致
- 「{术语}」: 文档A={定义A}，文档B={定义B}

## 总体评价
{通过 / 需修复后重审}
```
"""

REVIEWER_TRANSLATION_MD = """\
# HPSwarm Reviewer — 翻译审阅员

你是 HPSwarm 的 **Reviewer-Translation（翻译审阅员）**。你只做法律文书翻译审阅（中英/英中）。

## 翻译铁律（必须逐条遵守）

**禁止 `exec import docx`！** 读取 .docx 用 `lex_read`，编辑用 `lex_edit`/`lex_format`。

1. **先读全文再动手。** 任何操作前通读全文，理解结构和上下文。
2. **原子化修改。** 一次只修一类问题（术语/格式/语序），修完立即 verify 清零。
3. **TC 跨 run 失败降级。** tc-replace 跨会话失败时，退到 python-docx run 级替换。
4. **表格列数对齐中文版。** 中英文表格列数须一致，列宽根据内容调整。
5. **内容先于文档。** 先用纯文本定稿（确认翻译质量），再用 lex_docx 落文到 .docx。

## 术语标准

| 类别 | 标准 |
|------|------|
| 法律术语 | LMA (Loan Market Association) 标准术语 |
| 金融术语 | APLMA / LSTA 标准 |
| 公司治理 | Companies Ordinance / PRC Company Law 英译本 |
| 未知术语 | 查 LMA 术语表 → 行业惯例 → 标记 [需律师确认] |

## 语言规范

- **English:** British English（-ise 非 -ize，colour 非 color）
- **中文:** 法律文书正式语体，禁止口语化
- **术语一致:** 同一英文术语全篇对应同一中文译法，反之亦然
- **长句处理:** 英文长句可拆分为中文短句，但不得改变法律含义
- **被动语态:** 英文被动可转中文主动，但保留责任主体

## 格式标准

| 规则 | 说明 |
|------|------|
| 定义词 | 首次定义术语 Bold + 引号（中英文均适用） |
| 全段引用 | ▲▲ ... ▲▲ 包裹全段 Bold |
| 空段 | 空 <w:p> 保持段落间距，禁止多余空格 |
| 中英混排标点 | 统一半角标点 |
| 引号 | 直引号 " ' 不用弯引号 |
| 字体 | 中文宋体，英文/数字 Times New Roman |
| 段落编号 | 编号结构不变，仅替换文本内容 |

## 审阅流程

1. 通读原文全文（`lex_docx_export_structure` + `lex_docx_para_query`）
2. 提取术语表 → 核实术语一致性（`lex_corpus index` + 人工比对）
3. 逐段对照（原文 vs 译文）：
   a. 术语准确度
   b. 法律含义完整性（无增删法律义务）
   c. 数字/日期/金额零偏差
4. 格式检查（定义词 Bold、引号、空段、表格列数）
5. 全篇术语一致性最终扫描
6. 输出翻译审阅报告

## 工具使用

| 工具 | 用途 |
|------|------|
| `lex_docx_stats` | 文档规模 |
| `lex_docx_export_structure` | 全文结构 |
| `lex_docx_para_query` | 检索特定段落 |
| `lex_docx_extract_table` | 提取表格对比 |
| `lex_corpus index` | 建立术语索引 |
| `lex_corpus query` | 查询术语一致性 |
| `lex_docx_lint` | 格式规则检查 |

## 输出格式

```markdown
# 翻译审阅报告

## 文档概况
- 源语言 / 目标语言
- 总段落数 / 表格数
- 审阅范围

## 术语一致性检查
| 原文术语 | 期望译文 | 实际译文 | P# | 状态 |

## 翻译质量问题

### 术语错误（必须修改）
- P{para}: 「{原文}」译为「{actual}」，应为「{expected}」（参考：LMA标准）

### 法律含义偏差（必须修改）
- P{para}: {偏差描述} — 原文含义 vs 译文表达

### 格式问题
- P{para}: {格式问题描述}

### 数字/日期核对
- 已验证 {n} 处数字/日期/金额，{n} 处偏差

## 总体评价
{通过 / 需修改后重审}
```
"""

PROJECT_CONTEXT_TEMPLATE = """\
# Project Context — {name}

> **Living document.** Agent 在项目推进中通过 `update_project_state` 持续更新。
> 上下文压缩后，重读本文底部的 `## Journal` 节即可恢复关键上下文。
> 记忆文件 `memories/*.md` 每轮自动注入，不会因压缩丢失。

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
- .hermes-project/roles/reviewer-ts-consistency.md — TS商业一致性审阅角色
- .hermes-project/roles/reviewer-cross-ref.md — 交叉引用审阅角色
- .hermes-project/roles/reviewer-translation.md — 翻译审阅角色

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

def _create_scaffolding(project_dir: Path, name: str, client: str, goal: str, cwd: str = "") -> dict:
    """Create project directory scaffolding. Shared by CLI and chat tools.

    Args:
        project_dir: Management/bootstrap directory (AGENTS.md, .hermes-project/).
        name: Project name.
        client: Client name.
        goal: Project goal.
        cwd: Working directory where documents live. Defaults to project_dir.
    """
    project_dir.mkdir(parents=True, exist_ok=True)

    hermes_dir = project_dir / ".hermes-project"
    hermes_dir.mkdir(parents=True, exist_ok=True)

    roles_dir = hermes_dir / "roles"
    roles_dir.mkdir(parents=True, exist_ok=True)

    memories_dir = hermes_dir / "memories"
    memories_dir.mkdir(parents=True, exist_ok=True)

    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    cwd_path = cwd if cwd else str(project_dir)

    meta = {
        "name": name,
        "client": client,
        "goal": goal,
        "cwd": cwd_path,
        "management_dir": str(project_dir),
        "created": now,
        "toolsets": ["lexitool"],
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
    # Load roles from master SOP files, falling back to hardcoded templates
    (roles_dir / "drafter.md").write_text(_load_role_content("drafter.md", DRAFTER_MD))
    (roles_dir / "reviewer-content.md").write_text(_load_role_content("reviewer-content.md", REVIEWER_CONTENT_MD))
    (roles_dir / "reviewer-format.md").write_text(_load_role_content("reviewer-format.md", REVIEWER_FORMAT_MD))
    (roles_dir / "reviewer-ts-consistency.md").write_text(_load_role_content("reviewer-ts-consistency.md", REVIEWER_TS_CONSISTENCY_MD))
    (roles_dir / "reviewer-cross-ref.md").write_text(_load_role_content("reviewer-cross-ref.md", REVIEWER_CROSS_REF_MD))
    (roles_dir / "reviewer-translation.md").write_text(REVIEWER_TRANSLATION_MD)

    # Init project state and seed memory files
    _init_project_state(project_dir, name, now)
    _seed_project_memories(hermes_dir, name, client, goal, now)

    return meta


# ═══════════════════════════════════════════════════════════════════════════
# Project State Evolution — living project context that survives compression
# ═══════════════════════════════════════════════════════════════════════════

# Valid project phases in linear order
_PHASES = ["init", "drafting", "review", "execution", "cp", "closing", "registration"]

_PROJECT_STATE_TEMPLATE = """\
# Project State — {name}

> 此文件由 `update_project_state` 自动维护。Agent 在项目推进过程中持续更新。
> 最后更新: {now}

## Current Phase

- **Phase:** {phase}
- **Since:** {since}
- **Goal:** {goal}

## Key Findings

<!-- agent appends dated findings here -->

## Active Documents

<!-- agent maintains current working set: path, status (draft/review/final/executed), D_number -->

## Gate Status

<!-- set by lex_gate_check; update after each run -->

## Decisions

<!-- dated decisions with rationale -->
"""


def _init_project_state(project_dir: Path, name: str, now: str) -> Path:
    """Create the initial project-state.json file."""
    project_dir = Path(project_dir)
    hermes_dir = project_dir / ".hermes-project"
    state_path = hermes_dir / "project-state.json"

    state = {
        "project_name": name,
        "current_phase": "init",
        "phase_history": [
            {"phase": "init", "since": now, "note": "Project initialised"}
        ],
        "last_gate_check": None,
        "key_findings": [],
        "active_documents": [],
        "decisions": [],
        "updated_at": now,
    }
    state_path.write_text(json.dumps(state, indent=2, ensure_ascii=False) + "\n")
    return state_path


def _seed_project_memories(hermes_dir: Path, name: str, client: str, goal: str, now: str) -> None:
    """Seed .hermes-project/memories/ with initial files that survive compression."""
    memories_dir = hermes_dir / "memories"

    (memories_dir / "key_findings.md").write_text(
        f"# Key Findings — {name}\n\n"
        f"> 关键发现。每轮自动注入上下文，压缩不丢失。\n\n"
        f"**Project:** {name}  \n"
        f"**Client:** {client}  \n"
        f"**Goal:** {goal}  \n"
        f"**Created:** {now}\n\n"
        f"---\n\n"
        f"<!-- agent: add new findings with date stamps -->\n",
        encoding="utf-8",
    )

    (memories_dir / "document_index.md").write_text(
        f"# Document Index — {name}\n\n"
        f"> 项目文档清单与版本状态。每轮自动注入。\n\n"
        f"| D# | Document | Status | Path | Notes |\n"
        f"|----|----------|--------|------|-------|\n"
        f"<!-- agent: add rows as documents are created/reviewed/signed -->\n",
        encoding="utf-8",
    )

    (memories_dir / "decisions.md").write_text(
        f"# Decisions — {name}\n\n"
        f"> 重大决策记录。每轮自动注入。\n\n"
        f"<!-- agent: add dated decisions with rationale -->\n",
        encoding="utf-8",
    )


def _read_project_state(project_dir: str) -> dict:
    """Read project-state.json. Returns empty dict on failure."""
    state_path = Path(project_dir) / ".hermes-project" / "project-state.json"
    if not state_path.is_file():
        return {}
    try:
        return json.loads(state_path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _write_project_state(project_dir: str, state: dict) -> None:
    """Write project-state.json."""
    state_path = Path(project_dir) / ".hermes-project" / "project-state.json"
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(json.dumps(state, indent=2, ensure_ascii=False) + "\n")


def _read_tasks(project_dir: str) -> dict:
    """Read project-tasks.json. Returns {"tasks": []} on failure."""
    tasks_path = Path(project_dir) / ".hermes-project" / "project-tasks.json"
    if not tasks_path.is_file():
        return {"tasks": []}
    try:
        return json.loads(tasks_path.read_text(encoding="utf-8"))
    except Exception:
        return {"tasks": []}


def _write_tasks(project_dir: str, data: dict) -> None:
    """Write project-tasks.json."""
    tasks_path = Path(project_dir) / ".hermes-project" / "project-tasks.json"
    tasks_path.parent.mkdir(parents=True, exist_ok=True)
    tasks_path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n")


def _sync_tasks_memory(project_dir: str, tasks_data: dict) -> None:
    """Sync task board to memories/tasks.md for compression survival."""
    memories_dir = Path(project_dir) / ".hermes-project" / "memories"
    memories_dir.mkdir(parents=True, exist_ok=True)

    tasks = tasks_data.get("tasks", [])
    status_groups: dict[str, list[dict]] = {"pending": [], "in_progress": [], "completed": [], "cancelled": []}
    for t in tasks:
        s = t.get("status", "pending")
        status_groups.setdefault(s, []).append(t)

    lines = [
        "# Task Board",
        "",
        f"**Updated:** {datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')}",
        "",
    ]

    for status, label in [("pending", "Pending"), ("in_progress", "In Progress"), ("completed", "Completed"), ("cancelled", "Cancelled")]:
        group = status_groups.get(status, [])
        lines.append(f"## {label} ({len(group)})")
        lines.append("")
        if group:
            lines.append("| ID | Title | Priority | Tags | Created |")
            lines.append("|----|-------|----------|------|---------|")
            for t in group:
                tid = t.get("id", "-")
                title = t.get("title", t.get("raw_input", "-"))
                priority = t.get("priority", "medium")
                tags = ", ".join(t.get("tags", [])) or "-"
                created = t.get("created_at", "")[:10] if t.get("created_at") else "-"
                lines.append(f"| {tid} | {title} | {priority} | {tags} | {created} |")
        else:
            lines.append("<!-- no tasks -->")
        lines.append("")

    (memories_dir / "tasks.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def _append_journal_entry(project_dir: str, entry: str, category: str = "general") -> None:
    """Append a dated entry to project-context.md journal and update state.

    The journal section at the bottom of project-context.md is what the agent
    re-reads after compression to rehydrate context.
    """
    context_path = Path(project_dir) / ".hermes-project" / "project-context.md"
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    # Append to journal section of project-context.md
    content = ""
    if context_path.is_file():
        content = context_path.read_text(encoding="utf-8")

    if "## Journal" not in content:
        content += f"\n\n## Journal\n\n> 项目进展日志。Agent 每阶段追加。压缩后重读此节即可恢复上下文。\n\n"

    content += f"### {now} — {category}\n\n{entry}\n\n"
    context_path.write_text(content, encoding="utf-8")

    # Also update state's key_findings if it's a finding
    state = _read_project_state(project_dir)
    if state and category in ("finding", "decision"):
        key = "key_findings" if category == "finding" else "decisions"
        if key not in state:
            state[key] = []
        state[key].append({"at": now, category: entry})
        state["updated_at"] = now
        _write_project_state(project_dir, state)


def update_project_state(
    project_dir: str,
    *,
    phase: str | None = None,
    phase_note: str = "",
    finding: str | None = None,
    decision: str | None = None,
    document: dict | None = None,
    gate_result: dict | None = None,
) -> dict:
    """Update the evolving project state.

    Called by the agent at key milestones: phase changes, new findings,
    document creation/completion, gate check results, major decisions.

    All updates are also appended to the project-context.md journal so
    context survives compression.

    Args:
        project_dir: Path to the project root (.hermes-project/ parent).
        phase:        New phase name (init/drafting/review/execution/cp/closing/registration).
        phase_note:   Human-readable note about the phase transition.
        finding:      A key finding to record.
        decision:     A major decision with rationale.
        document:     {"path": "...", "status": "draft|review|final|executed", "D_number": "D01"}.
        gate_result:  The full result dict from lex_gate_check — auto-updates gate status.

    Returns:
        {"ok": True, "state": {...}, "journal_updated": True}
    """
    src = Path(project_dir)
    state_path = src / ".hermes-project" / "project-state.json"

    if not state_path.is_file():
        return {"ok": False, "error": f"Not a hermes project: {project_dir}"}

    state = _read_project_state(project_dir)
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    journal_entries = []

    # ── Phase update ──
    if phase and phase in _PHASES:
        old_phase = state.get("current_phase", "init")
        state["current_phase"] = phase
        state.setdefault("phase_history", []).append({
            "phase": phase, "since": now,
            "note": phase_note or f"Phase transition from {old_phase}",
        })
        journal_entries.append(
            f"**Phase → {phase}** (from {old_phase})\n\n{phase_note}"
            if phase_note else
            f"**Phase → {phase}** (from {old_phase})"
        )

    # ── Finding ──
    if finding:
        state.setdefault("key_findings", []).append({"at": now, "finding": finding})
        journal_entries.append(f"**Finding:** {finding}")

    # ── Decision ──
    if decision:
        state.setdefault("decisions", []).append({"at": now, "decision": decision})
        journal_entries.append(f"**Decision:** {decision}")

    # ── Document tracking ──
    if document:
        state.setdefault("active_documents", []).append({
            **document, "updated_at": now,
        })
        dnum = document.get("D_number", document.get("path", "doc"))
        journal_entries.append(
            f"**Document:** {dnum} → {document.get('status', 'unknown')}"
        )

    # ── Gate check result ──
    if gate_result:
        state["last_gate_check"] = {
            "at": now,
            "all_passed": gate_result.get("all_passed", False),
            "blocked": gate_result.get("blocked", "none"),
            "gates_summary": [
                {"gate": g["gate"], "status": g["status"]}
                for g in gate_result.get("gates", [])
            ],
        }
        journal_entries.append(
            f"**Gate Check:** {'PASS' if gate_result.get('all_passed') else 'FAIL'} "
            f"— blocked: {gate_result.get('blocked', 'none')}"
        )

    state["updated_at"] = now
    _write_project_state(project_dir, state)

    # ── Journal entries ──
    for entry in journal_entries:
        _append_journal_entry(project_dir, entry, category="state_update")

    # ── Sync to memory files ──
    _sync_memories_from_state(project_dir, state)

    return {
        "ok": True,
        "state": state,
        "journal_updated": bool(journal_entries),
        "phase": state.get("current_phase"),
        "finding_count": len(state.get("key_findings", [])),
        "document_count": len(state.get("active_documents", [])),
    }


def _sync_memories_from_state(project_dir: str, state: dict) -> None:
    """Sync project state to memory files that survive compression."""
    memories_dir = Path(project_dir) / ".hermes-project" / "memories"
    memories_dir.mkdir(parents=True, exist_ok=True)

    name = state.get("project_name", "Project")

    # key_findings.md
    finding_lines = [
        f"# Key Findings — {name}",
        "",
        f"**Phase:** {state.get('current_phase', 'unknown')}",
        f"**Updated:** {state.get('updated_at', 'N/A')}",
        "",
        "---",
        "",
    ]
    for f in state.get("key_findings", []):
        finding_lines.append(f"- [{f['at']}] {f['finding']}")
    if len(state.get("key_findings", [])) == 0:
        finding_lines.append("<!-- no findings yet -->")
    (memories_dir / "key_findings.md").write_text(
        "\n".join(finding_lines) + "\n", encoding="utf-8"
    )

    # document_index.md
    doc_lines = [
        f"# Document Index — {name}",
        "",
        f"**Phase:** {state.get('current_phase', 'unknown')}",
        "",
        "| D# | Document | Status | Notes |",
        "|----|----------|--------|-------|",
    ]
    for d in state.get("active_documents", []):
        dnum = d.get("D_number", "-")
        path = d.get("path", "-")
        status = d.get("status", "-")
        notes = d.get("notes", d.get("updated_at", ""))
        doc_lines.append(f"| {dnum} | {path} | {status} | {notes} |")
    (memories_dir / "document_index.md").write_text(
        "\n".join(doc_lines) + "\n", encoding="utf-8"
    )

    # decisions.md
    dec_lines = [
        f"# Decisions — {name}",
        "",
        f"**Phase:** {state.get('current_phase', 'unknown')}",
        "",
    ]
    for d in state.get("decisions", []):
        dec_lines.append(f"### {d['at']}")
        dec_lines.append(f"{d['decision']}")
        dec_lines.append("")
    (memories_dir / "decisions.md").write_text(
        "\n".join(dec_lines) + "\n", encoding="utf-8"
    )


def get_project_state(project_dir: str) -> dict:
    """Read current project state (read-only, no side effects).

    Returns the full state dict with a convenience summary field.
    """
    state = _read_project_state(project_dir)
    if not state:
        return {"ok": False, "error": f"No project-state.json in {project_dir}"}

    return {
        "ok": True,
        "project_name": state.get("project_name"),
        "phase": state.get("current_phase"),
        "phase_history": state.get("phase_history", []),
        "last_gate_check": state.get("last_gate_check"),
        "key_findings": state.get("key_findings", []),
        "active_documents": state.get("active_documents", []),
        "decisions": state.get("decisions", []),
        "updated_at": state.get("updated_at"),
    }


# ── Project Task Board ──────────────────────────────────────────────────


def _generate_task_id() -> str:
    """Generate a short unique task ID: t_ + 8 hex chars."""
    return "t_" + __import__("secrets").token_hex(4)


def add_project_task(
    project_dir: str,
    raw_task: str,
    *,
    priority: str = "medium",
) -> dict:
    """Capture a raw task and return a refinement framework.

    The task is stored immediately with raw_task as the title (status=pending).
    A refinement framework is returned so the agent can refine and then call
    update_project_task to fill in title, description, and tags.

    Args:
        project_dir: Path to the project root (.hermes-project/ parent).
        raw_task:    The raw task description as entered by the user.
        priority:    Initial priority hint (high/medium/low), default medium.

    Returns:
        {"ok": true, "task": {...}, "refinement_framework": "..."}
    """
    src = Path(project_dir)
    meta_path = src / ".hermes-project" / "project-meta.json"
    if not meta_path.is_file():
        return {"ok": False, "error": f"Not a hermes project: {project_dir}"}

    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    task = {
        "id": _generate_task_id(),
        "title": raw_task,
        "description": "",
        "raw_input": raw_task,
        "status": "pending",
        "priority": priority if priority in ("high", "medium", "low") else "medium",
        "tags": [],
        "created_at": now,
        "updated_at": now,
        "completed_at": None,
    }

    data = _read_tasks(project_dir)
    data.setdefault("tasks", []).append(task)
    _write_tasks(project_dir, data)
    _sync_tasks_memory(project_dir, data)

    framework = f"""## Task Refinement Framework

### Captured Task (Raw)
{raw_task}

### Refinement Checklist

1. **Specific Deliverable**: What exactly should be produced? (document, review report, translation, etc.)
2. **Scope Boundaries**: What is explicitly included? What is explicitly NOT included?
3. **Quality Criteria**: How will we know this task is "done"? What standards apply?
4. **Dependencies**: Does it depend on other tasks, documents, or external inputs?
5. **Priority Justification**: Why high/medium/low? What is the urgency driver?

### Output Format

**Title:** [One clear, actionable sentence]
**Description:** [Scope + deliverables + quality criteria]
**Tags:** [comma-separated: drafting, review, translation, urgent, etc.]
**Priority:** [high | medium | low]

Now use project_update_task to save the refined task with task_id = `{task['id']}`."""

    return {
        "ok": True,
        "task": task,
        "refinement_framework": framework,
    }


def list_project_tasks(
    project_dir: str,
    *,
    status: str | None = None,
    priority: str | None = None,
) -> dict:
    """List project tasks with optional filtering.

    Args:
        project_dir: Path to the project root.
        status:      Filter by status (pending/in_progress/completed/cancelled).
        priority:    Filter by priority (high/medium/low).

    Returns:
        {"ok": true, "tasks": [...], "count": N}
    """
    src = Path(project_dir)
    meta_path = src / ".hermes-project" / "project-meta.json"
    if not meta_path.is_file():
        return {"ok": False, "error": f"Not a hermes project: {project_dir}"}

    data = _read_tasks(project_dir)
    tasks = data.get("tasks", [])

    if status:
        tasks = [t for t in tasks if t.get("status") == status]
    if priority:
        tasks = [t for t in tasks if t.get("priority") == priority]

    return {"ok": True, "tasks": tasks, "count": len(tasks)}


def update_project_task(
    project_dir: str,
    task_id: str,
    *,
    status: str | None = None,
    priority: str | None = None,
    title: str | None = None,
    description: str | None = None,
    tags: list[str] | None = None,
) -> dict:
    """Update a task's fields.

    Only provided (non-None) fields are updated. Omitted fields are left unchanged.

    Args:
        project_dir: Path to the project root.
        task_id:     The task ID to update.
        status:      New status (pending/in_progress/completed/cancelled).
        priority:    New priority (high/medium/low).
        title:       Refined task title.
        description: Expanded task description.
        tags:        List of category tags.

    Returns:
        {"ok": true, "task": {...}}
    """
    src = Path(project_dir)
    meta_path = src / ".hermes-project" / "project-meta.json"
    if not meta_path.is_file():
        return {"ok": False, "error": f"Not a hermes project: {project_dir}"}

    data = _read_tasks(project_dir)
    tasks = data.get("tasks", [])
    task = None
    for t in tasks:
        if t.get("id") == task_id:
            task = t
            break

    if task is None:
        return {"ok": False, "error": f"Task not found: {task_id}"}

    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    task["updated_at"] = now

    if status is not None:
        valid_statuses = {"pending", "in_progress", "completed", "cancelled"}
        if status not in valid_statuses:
            return {"ok": False, "error": f"Invalid status: {status}. Must be one of: {', '.join(sorted(valid_statuses))}"}
        task["status"] = status
        if status == "completed":
            task["completed_at"] = now

    if priority is not None:
        valid_priorities = {"high", "medium", "low"}
        if priority not in valid_priorities:
            return {"ok": False, "error": f"Invalid priority: {priority}. Must be one of: {', '.join(sorted(valid_priorities))}"}
        task["priority"] = priority

    if title is not None:
        task["title"] = title

    if description is not None:
        task["description"] = description

    if tags is not None:
        task["tags"] = tags

    _write_tasks(project_dir, data)
    _sync_tasks_memory(project_dir, data)

    return {"ok": True, "task": task}


def delete_project_task(project_dir: str, task_id: str) -> dict:
    """Delete a task from the project task board.

    Args:
        project_dir: Path to the project root.
        task_id:     The task ID to delete.

    Returns:
        {"ok": True} or {"ok": False, "error": "..."}
    """
    src = Path(project_dir)
    meta_path = src / ".hermes-project" / "project-meta.json"
    if not meta_path.is_file():
        return {"ok": False, "error": f"Not a hermes project: {project_dir}"}

    data = _read_tasks(project_dir)
    tasks = data.get("tasks", [])
    before = len(tasks)
    data["tasks"] = [t for t in tasks if t.get("id") != task_id]

    if len(data["tasks"]) == before:
        return {"ok": False, "error": f"Task not found: {task_id}"}

    _write_tasks(project_dir, data)
    _sync_tasks_memory(project_dir, data)

    return {"ok": True}


def _register_in_db(name: str, path: str, client: str, goal: str, cwd: str = "") -> str:
    """Register a project in the state DB and return its ID.

    Also auto-creates the client profile directory if the client name
    is a real value (not the placeholder).
    """
    from hermes_state import SessionDB

    db = SessionDB()
    project_id = db.create_project(name, path, client, goal, cwd)

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
    print(f"  Tools:     lexitool, lex-docx (auto-enabled when available)")
    print()
    print("Project files:")
    print(f"  AGENTS.md          Coordinator identity & HPSwarm workflow")
    print(f"  STANDARDS.md       Legal document production SOP")
    print(f"  .hermes-project/   Project context, sub-agent roles & memories")
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
