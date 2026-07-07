# Lexitool 交叉引用系统重构计划

Date: 2026-07-07

## 问题

当前 lexitool 的交叉引用处理是**文本匹配**（regex `第X条`），不是**Word field code 解析**。

Word 的交叉引用实际结构：

```
<w:r><w:fldChar w:fldCharType="begin"/></w:r>
<w:r><w:instrText> REF _TocClause3_2 \h \p </w:instrText></w:r>
<w:r><w:fldChar w:fldCharType="separate"/></w:r>
<w:r><w:t>第3.2条（贷款利率）</w:t></w:r>   ← 显示文本（格式可变）
<w:r><w:fldChar w:fldCharType="end"/></w:r>
```

当前 lexitool 只看到 `第3.2条（贷款利率）` 这段文本，完全丢失了：
- 引用目标（bookmark 名 `_TocClause3_2`）
- 引用类型（REF/PAGEREF/NOTEREF/STYLEREF）
- 显示格式（\p 段落编号 / \n 无上下文 / \w 完整上下文 / \t 段落文字）
- 超链接（\h）
- 见上/见下（\p）
- 页码引用（PAGEREF）
- 脚注/尾注引用

## Word 引用系统规格

### 引用类型（Reference Types）

| 类型 | Word 内部 | 说明 |
|------|-----------|------|
| 编号项 | `Numbered item` | 带编号的段落（§3.1、1.2.3 等） |
| 标题 | `Heading` | 标题样式段落 |
| 书签 | `Bookmark` | 手动或自动添加的书签 |
| 脚注 | `Footnote` | 脚注引用 |
| 尾注 | `Endnote` | 尾注引用 |

### 显示格式（Insert As）

| 格式 | Word 内部 | field code 参数 | 示例输出 |
|------|-----------|----------------|---------|
| 编号段落 | `Paragraph number` | `\p` | `第3.2条` |
| 编号段落(无上下文) | `Paragraph number (no context)` | `\n` | `3.2` |
| 编号段落(完整上下文) | `Paragraph number (full context)` | `\w` | `第三章 第3.2条` |
| 段落文字 | `Paragraph text` | `\t` | `第3.2条 贷款利率` |

### 选项

| 选项 | field code 参数 | 说明 |
|------|----------------|------|
| 插入为超链接 | `\h` | 点击跳转到引用目标 |
| 包含见上/见下 | `\p` (with `\d` or `\u`) | 显示"见上方"或"见下方" |

### Field Code 语法

```
REF <bookmark> \h \p         ← 超链接 + 段落编号
REF <bookmark> \h \n         ← 超链接 + 无上下文编号
REF <bookmark> \h \w         ← 超链接 + 完整上下文编号
REF <bookmark> \h \t         ← 超链接 + 段落文字
PAGEREF <bookmark> \h        ← 页码引用（超链接）
NOTEREF <bookmark> \h \f \p  ← 脚注引用
STYLEREF <style> \p          ← 样式引用
```

## 重构方案

### 核心改动：lexitool/xref.py

将 `_XREF_PATTERN`（regex 文本匹配）替换为 XML field code 解析。

#### 新增函数

```python
def parse_field_codes(doc_path: str) -> list[dict]:
    """Parse all Word field codes from docx XML.
    
    Returns list of:
    {
        "type": "REF" | "PAGEREF" | "NOTEREF" | "STYLEREF" | "TOC",
        "bookmark": "_TocClause3_2",
        "flags": ["h", "p"],           # \h, \p, \n, \w, \t etc.
        "display_text": "第3.2条（贷款利率）",
        "para_index": 145,             # 1-indexed paragraph containing this field
        "run_indices": [3,4,5,6,7],    # XML run indices for begin/separate/text/end
        "is_hyperlink": True,          # \h present
        "display_format": "paragraph_number",  # \p → paragraph_number, \n → number_no_context, etc.
        "has_above_below": False,      # \p with \d or \u
        "target_exists": True,         # bookmark exists in document
        "target_para": 132,            # paragraph index of target bookmark
        "target_heading": "第3.2条 贷款利率",
    }
    """
```

#### 新增函数

```python
def classify_field_code(instr_text: str) -> dict:
    """Parse a single field code instruction string.
    
    Examples:
        "REF _TocClause3_2 \\h \\p" → {type: "REF", bookmark: "_TocClause3_2", flags: ["h","p"]}
        "PAGEREF _Toc88583195 \\h"  → {type: "PAGEREF", bookmark: "_Toc88583195", flags: ["h"]}
    """
```

#### 新增函数

```python
def resolve_display_format(flags: list[str]) -> str:
    """Convert field code flags to human-readable display format.
    
    ["h", "p"] → "超链接 + 段落编号"
    ["h", "n"] → "超链接 + 无上下文编号"
    ["h", "w"] → "超链接 + 完整上下文编号"
    ["h", "t"] → "超链接 + 段落文字"
    ["h"]      → "超链接（默认显示格式）"
    []         - 无特殊选项
    """
```

#### 修改函数

`scan_xrefs()` — 替换 regex 匹配为 field code 解析：

```python
def scan_xrefs(doc_path: str) -> dict:
    """Scan all cross-references by parsing field codes."""
    field_codes = parse_field_codes(doc_path)
    clause_index = _build_clause_index(...)  # 保留，用于 target 解析
    
    xrefs = []
    for fc in field_codes:
        if fc["type"] in ("REF", "PAGEREF", "NOTEREF", "STYLEREF"):
            target_para = clause_index.get(fc["bookmark"])
            xrefs.append({
                "para": fc["para_index"],
                "type": fc["type"],
                "bookmark": fc["bookmark"],
                "display_format": fc["display_format"],
                "is_hyperlink": fc["is_hyperlink"],
                "display_text": fc["display_text"],
                "target_para": target_para,
                "target_exists": target_para is not None,
            })
    
    return {
        "ok": True,
        "xrefs": xrefs,
        "xref_count": len(xrefs),
        "field_types": {t: sum(1 for f in field_codes if f["type"] == t) for t in set(f["type"] for f in field_codes)},
        "display_formats": {d: sum(1 for f in xrefs if f.get("display_format") == d) for d in set(f.get("display_format","") for f in xrefs)},
    }
```

### lex_ref 工具更新

更新 `_handle_ref` 中的 `xref_audit` 操作，返回 field code 信息：

```
现有输出：
  §109 references §106: 第4.1条(初始先决条件)

新输出：
  §109 → §106 | REF | 第4.1条(初始先决条件) | 超链接+段落编号 | OK
  §110 → §132 | PAGEREF | page 5 | 超链接 | OK
  §200 → ???  | REF | 第99条 | 超链接+段落文字 | DEAD (bookmark missing)
```

### 新增操作

`lex_ref` 新增 `parse_fields` 操作：

```python
"parse_fields": "Parse all field codes and return structured JSON with type, bookmark, display format, flags, and target resolution."
```

### 修改现有操作

| 操作 | 当前行为 | 新行为 |
|------|---------|--------|
| `xref_audit` | regex 匹配 `第X条` | field code 解析，显示引用类型和格式 |
| `auto_xref` | 包裹文本为 hyperlink | 解析已有 field codes，只补缺失的 |
| `convert_static_refs` | regex → field code | 同上，但先检查是否已有 field code |
| `list_fields` | 简单列出 field codes | 结构化输出（类型、bookmark、格式、目标） |

### 不改动的部分

- `_build_clause_index()` — 保留，用于目标解析
- `_add_bookmarks_to_headings()` — 保留，auto_xref 需要
- `cross_doc_scan` — 保留，跨文档引用仍需文本匹配（不同 docx 文件）
- `term_format_audit` — 保留，跟 xref 无关

## 实现步骤

### Step 1: 新增 `parse_field_codes()` (~100 行)

在 `xref.py` 中新增，解析 `word/document.xml` 的 field code 结构。

### Step 2: 新增 `classify_field_code()` (~30 行)

解析 `instrText` 字符串为结构化数据。

### Step 3: 新增 `resolve_display_format()` (~20 行)

flags → 人类可读格式。

### Step 4: 修改 `scan_xrefs()` (~50 行改动)

替换 regex 匹配为 field code 解析。

### Step 5: 更新 `lex_ref` tool 的 `xref_audit` 操作

返回 field code 信息（类型、格式、超链接状态）。

### Step 6: 新增 `parse_fields` 操作

给 Agent 提供原始 field code 数据。

### Step 7: 测试

- 用 D001_UOB-Starley 测试（125 个 field codes）
- 用颐保银团测试（149 个插入）
- 用漕河泾尽调测试（917 个插入）

## 风险

- **field code 嵌套**：REF 内部可能有嵌套 field code（如 TOC 内的 REF）
- **broken field codes**：`begin`/`separate`/`end` 不匹配
- **性能**：解析 XML 比 regex 慢，但 1000 个 field code 仍在毫秒级
