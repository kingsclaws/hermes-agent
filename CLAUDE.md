# CLAUDE.md — Hermes Agent (Lex-Hermes Legal Fork)

> 本文件是 Claude Code 与 Hermes Agent 的桥接上下文。
> 当 lexitool 或 lex_docx 工具出现问题，或需要修改/新增工具时，
> 将此文件内容粘贴给 Claude，Claude 即可获得完整的工具参数上下文来协助修复。

## 项目概述

这是 [Hermes Agent](https://github.com/NousResearch/hermes-agent) 的 fork，
增加了法律文档自动化能力：
- **lexitool**（8 个工具，`tools/lexitool_tool.py`）— 新一代 Word 文档原子操作工具集
- **lex_docx 原生工具集**（30 个工具，`tools/lex_docx_tool.py`）— 旧版兼容
- **lex-editor 角色**（`.hermes-project/roles/lex-editor.md`）— 法律文档编辑 SOP
- **HPSwarm 多 Agent 协作**（Coordinator / Drafter / Reviewer-Content / Reviewer-Format）
- **项目级 Harness**（`harness/` 目录，含法律 SOP 和项目模板）

## 关键文件索引

| 文件 | 用途 |
|------|------|
| `tools/lexitool_tool.py` | 8 个 lexitool 工具注册（新一代） |
| `tools/lex_docx_tool.py` | 30 个 lex_docx 原生工具注册（旧版兼容） |
| `vendor/lexitool/lexitool/` | lexitool Python 包（35+ 模块） |
| `.hermes-project/roles/lex-editor.md` | 法律文档编辑 SOP + 自演化机制 |
| `toolsets.py` | 含 `lexitool` + `lex-docx` toolset 定义 |
| `harness/legal/STANDARDS.md` | 法律文件格式标准 |
| `harness/legal/profiles/` | HPSwarm Agent profile 定义 |
| `harness/projects/_template/` | 新项目模板 |
| `tools/registry.py` | 工具注册表（现有基础设施，不修改） |

## 工具注册模式

在 `tools/lex_docx_tool.py` 中，每个工具按以下模式注册：

```python
from tools.registry import registry, tool_error, tool_result

SCHEMA = {
    "name": "lex_docx_xxx",
    "description": "...",
    "parameters": {
        "type": "object",
        "properties": { ... },
        "required": [...],
    },
}

def _handle_xxx(args: dict, **kwargs) -> str:
    # 懒导入 lex_docx 模块
    from lex_docx.some_module import some_function
    # 调用并返回 JSON
    return tool_result(some_function(...))

registry.register(
    name="lex_docx_xxx",
    toolset="lex-docx",
    schema=SCHEMA,
    handler=_handle_xxx,
    check_fn=_check_lex_docx,
    description=SCHEMA.get("description", ""),
    emoji="",
)
```

**关键点：**
- AST 扫描器（`registry._module_registers_tools`）只检测**模块顶层**的 `registry.register(...)` 调用
- 所以文件中第一个工具是独立注册（不在循环内），其余 29 个在 `for` 循环中注册
- `check_fn=_check_lex_docx` 检测 lex_docx 是否可导入，不可用时工具自动隐藏
- 所有 handler 在调用时才 `import` lex_docx 模块（懒导入），避免启动时加载失败
- 每个 handler 接收 `args: dict`（工具参数）+ `**kwargs`，返回 JSON 字符串

## 全部 30 个 lex_docx 工具参数

### Inspect 类（只读）

**lex_docx_stats** — 文档摘要
```
docx (required): str — .docx 文件路径
```
返回：段落数、表格数、字体分布 top-5、TC 数量、批注数量

**lex_docx_export_structure** — 文档结构导出
```
docx (required): str
preview_len: int (default 120) — 每段预览最大字符数
tree: bool (default true) — 输出缩进树形结构
```

**lex_docx_para_query** — 段落格式检索
```
docx (required): str
style: str — 按样式名过滤 ("Heading 1", "Normal")
font: str — 按字体过滤
font_size: number — 按字号过滤 (pt)
bold: bool — 首 run 是否粗体
italic: bool — 首 run 是否斜体
outline_level: int — 大纲级别 0-8
alignment: str — LEFT/CENTER/RIGHT/JUSTIFY
text_regex: str — 正则匹配段落文本
para_range: str — "lo,hi" 段落范围
```

**lex_docx_extract_table** — 提取表格数据
```
docx (required): str
table_index: int — 表格索引 (0-based)，省略则提取全部
near_text: str — 找包含此文本最近的表格
```

**lex_docx_table_inspect** — 表格格式详情
```
docx (required): str
table_index: int
near_text: str
```

**lex_docx_tc_list** — Track Changes 列表
```
docx (required): str
author: str — 按作者过滤
tc_type: str — "ins" 或 "del"
para_range: str — "lo,hi"
```

**lex_docx_comment_list** — 批注列表
```
docx (required): str
author: str
para_range: str
```

**lex_docx_footer_audit** — 页脚审查
```
docx (required): str
```

**lex_docx_numbering_inspect** — 编号检查
```
docx (required): str
para_range: str
```

### Edit 类（写操作，默认 TC 模式）

**lex_docx_insert** — 段落插入
```
docx (required): str
para (required): int — 段落索引 (0-based)
text (required): str — 插入文本
tc: bool (default true) — Track Changes 模式
author: str (default "agent")
output: str — 输出路径（默认覆盖原文件）
```

**lex_docx_replace** — 段内替换
```
docx (required): str
para (required): int
old (required): str — 要替换的原文
new (required): str — 替换文本
tc: bool (default true)
author: str (default "agent")
output: str
```

**lex_docx_delete** — 段内删除
```
docx (required): str
para (required): int
text: str — 要删除的文本（省略则删整段）
tc: bool (default true)
author: str (default "agent")
output: str
```

### Review 类

**lex_docx_lint** — 格式规则检查
```
docx (required): str
rules: str — 逗号分隔的规则名（默认全跑）
para_range: str
```
返回：每条规则的 pass/fail + 位置

**lex_docx_doctor** — 格式诊断/修复
```
docx (required): str
action: str — "check" (default) 或 "fix"
para_range: str
output: str
dry_run: bool
```
D01-D09 规则：font_missing, font_mismatch, double_numbering, outline_leak,
sibling_numpr_gap, invalid_style_id, toc_u_switch, heading_font_inconsistent,
footer_stale_entities

**lex_docx_review_stats** — 定稿前审查摘要
```
docx (required): str
author: str — 按作者过滤 TC/批注
para_range: str
```
返回：TC 数量（按作者分组）、批注数、页脚问题、D09 陈旧实体数

### Finalize 类

**lex_docx_tc_accept** — 接受修订
```
docx (required): str
author: str
tc_type: str — "ins" 或 "del"
para_range: str
output: str
dry_run: bool — 预览模式
```

**lex_docx_tc_reject** — 拒绝修订
```
docx (required): str
author: str
tc_type: str
para_range: str
output: str
dry_run: bool
```

**lex_docx_comment_clean** — 清除批注
```
docx (required): str
author: str
para_range: str
output: str
dry_run: bool
```

**lex_docx_header_clean** — 清除页眉
```
docx (required): str
remove_refs: bool — 同时移除 section 的 headerReference
output: str
dry_run: bool
```

**lex_docx_clean** — 一键定稿
```
docx (required): str
tc_mode: str — "accept" (default) 或 "reject"
author: str
keep_comments: bool — 跳过批注清理
keep_headers: bool — 跳过页眉清理
output: str
dry_run: bool
```

### Format 类

**lex_docx_highlight** — 段落高亮
```
docx (required): str
para: int — 单个段落
para_range: str — 段落范围
color: str (default "yellow") — yellow/cyan/magenta/green/red
output: str
```

**lex_docx_set_outline_level** — 设置大纲级别
```
docx (required): str
level (required): int — 大纲级别 (0=正文, 1=Heading1, ...)
para: int
para_range: str
style: str — 按样式名批量设置
output: str
```

**lex_docx_bold_terms** — 定义术语加粗
```
docx (required): str
para: int — 处理的段落索引
scan: bool — 扫描模式（找候选段落）
para_range: str
output: str
```

**lex_docx_format_brush** — 格式刷
```
docx (required): str
ref (required): int — 参考段落索引
target: str — 逗号分隔的目标段落索引
target_range: str — 目标段落范围 "lo,hi"
copy: str — 要复制的属性: indent,spacing,font,size,bold,italic,alignment,style
output: str
```

**lex_docx_cleanup** — 清理空段落
```
docx (required): str
para_range: str
mode: str — "report" 或 "fix" (default)
output: str
```

**lex_docx_format_table** — 统一表格格式
```
docx (required): str
table_index: int
near_text: str
header_bg: str — 表头底色 hex (e.g. "1F4E79")
border_style: str — single/double/none/dashed
col_widths: str — 逗号分隔的列宽 (twips)
output: str
```

### Generate 类

**lex_docx_create** — 创建文档骨架
```
output (required): str — 输出 .docx 路径
title: str — 标题
meta: str — 元数据（换行分隔）
font_size: number (default 11.5)
```

**lex_docx_new_table** — 插入表格
```
docx (required): str
table_type: str (default "grid") — grid/kv/merged/nested/diagonal
cols: int
rows: int
headers: str — JSON 数组 '["列A","列B"]'
data: str — JSON 二维数组 '[["a1","b1"],["a2","b2"]]'
after_para: int — 插入到该段落之后
output: str
```

**lex_docx_fill_table** — 按列映射填充表格
```
docx (required): str
mapping (required): str — JSON 对象 '{"列名":"值",...}'
table_index: int
near_text: str
output: str
```

**lex_docx_toc** — 目录生成/刷新
```
docx (required): str
action: str (default "generate") — "generate" 或 "refresh"
levels: str (default "1-3") — 标题级别范围
output: str
```

## 热重载

修改 `tools/lex_docx_tool.py` 后，通过以下方式让 Hermes 重新加载工具：

```bash
# 方法 1：通过 Hermes CLI
hermes tools reload

# 方法 2：发送 /reload-tools 指令给 agent（如果运行中）
# 方法 3：重启 hermes 进程
```

工具注册表（`registry.py`）的 `deregister()` 方法支持按名称卸载单个工具，然后重新 import 模块触发 `register()`。

## HPSwarm 多 Agent 架构

```
Coordinator (hpswarm-coordinator)
  |— 读取 CONTEXT.md 了解项目状态
  |— 拆解任务 → delegate_task() 分派
  |
  ├─→ Drafter (hpswarm-drafter)
  |     |— 使用 lex_docx_* 工具起草/修改文档
  |     |— 全程 TC 模式
  |     └─→ 输出：修改后的 .docx + 修改摘要
  |
  ├─→ Reviewer-Content (hpswarm-reviewer-content)
  |     |— 逐段内容审阅（法律实质、完整性、一致性）
  |     |— 只读操作（stats, structure, para_query, lint）
  |     └─→ 输出：内容审阅报告 (Markdown)
  |
  └─→ Reviewer-Format (hpswarm-reviewer-format)
        |— 逐段格式审阅（字体、字号、间距、缩进、空行、表格、页眉页脚）
        |— doctor check → para_query → table_inspect → footer_audit
        └─→ 输出：格式审阅报告 (Markdown) + 自动修复
```

## 项目 Harness 结构

```
<项目目录>/
  BOOTSTRAP.md     # 项目初始化（从 harness/projects/_template/ 复制）
  CONTEXT.md       # 项目实时上下文（Coordinator 维护）
  config.yaml      # Hermes profile 配置
  workspace/       # 工作文件（.docx 等）
```

初始化新项目：
```bash
cp -r harness/projects/_template/ <新项目目录>/
# 编辑 BOOTSTRAP.md 填写项目信息
# Coordinator 首次启动时会读取 BOOTSTRAP.md 并生成 CONTEXT.md
```

## 修改工具时注意事项

1. **不要修改 `tools/registry.py`** — 这是上游基础设施，修改会导致合并冲突
2. **工具 schema 的 `name` 字段必须唯一** — 与 toolset 无关
3. **handler 返回值必须是 JSON 字符串** — 使用 `tool_result()` / `tool_error()` 辅助函数
4. **懒导入** — handler 内部 `from lex_docx.xxx import yyy`，不要放在文件头部
5. **check_fn 缓存** — `_check_lex_docx` 结果被 TTL 缓存 30 秒，修改工具代码后可能需要等 30 秒或重启
6. **AST 扫描** — 至少保留一个顶层 `registry.register(...)` 调用（不在循环/函数内），否则工具不会被自动发现
