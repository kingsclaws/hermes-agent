"""
lex_docx API — 顶层 Python 库接口。

用法：
    from lex_docx.api import insert, replace, delete, create, scaffold, new_table, stats

    # 直接编辑（无 TC）
    result = insert("doc.docx", para=5, text="新增文字")
    result = replace("doc.docx", para=5, old="旧文", new="新文")
    result = delete("doc.docx", para=5, text="要删的文字")

    # Track Changes 编辑
    result = insert("doc.docx", para=5, text="新增", tc=True, author="JT")
    result = replace("doc.docx", para=5, old="旧文", new="新文", tc=True)
    result = delete("doc.docx", para=5, tc=True)

    # 文档创建
    result = create("out.docx", title="文档标题", meta="案号:xxx")

    # 表格插入
    result = new_table("doc.docx", type="grid", cols=3, rows=4,
                       headers=["列A", "列B", "列C"])
"""
from __future__ import annotations

from . import edit_ops
from . import doc_create
from . import doc_scaffold
from . import new_table_ops
from . import doc_stats
from . import toc_ops

# ── 基础编辑 ──────────────────────────────────────────────────────────────────


def insert(docx_path: str, para: int, text: str, *,
           tc: bool = False, author: str = "agent",
           bold: bool = False, italic: bool = False,
           font: str = "宋体", font_size: float = 11.0,
           output: str | None = None) -> edit_ops.EditResult:
    """在指定段落末尾插入文字。"""
    return edit_ops.insert_text(
        docx_path, para, text,
        tc=tc, author=author, bold=bold, italic=italic,
        font=font, font_size=font_size, output=output,
    )


def replace(docx_path: str, para: int, old: str, new: str, *,
            tc: bool = False, author: str = "agent",
            bold: bool = False, italic: bool = False,
            font: str = "宋体", font_size: float = 11.0,
            output: str | None = None) -> edit_ops.EditResult:
    """替换段内文字。"""
    return edit_ops.replace_text(
        docx_path, para, old, new,
        tc=tc, author=author, bold=bold, italic=italic,
        font=font, font_size=font_size, output=output,
    )


def delete(docx_path: str, para: int, *,
           text: str | None = None,
           tc: bool = False, author: str = "agent",
           output: str | None = None) -> edit_ops.EditResult:
    """删除段内文字。"""
    return edit_ops.delete_text(
        docx_path, para, text=text,
        tc=tc, author=author, output=output,
    )


# ── 文档创建 ──────────────────────────────────────────────────────────────────


def create(output: str, *, title: str = "", meta: str = "",
           font_song: str = "宋体", font_roman: str = "Times New Roman",
           font_size: float = 11.5, line_spacing: str = "single") -> dict:
    """从零创建标准 OPC 骨架 .docx。"""
    return doc_create.create_document(
        output=output, title=title, meta=meta,
        font_song=font_song, font_roman=font_roman,
        font_size=font_size, line_spacing=line_spacing,
    )


def scaffold_preview(template: str, mapping: dict) -> dict:
    """预览模板克隆的映射命中。"""
    return doc_scaffold.scaffold_preview(template, mapping)


def scaffold_apply(template: str, output: str, mapping: dict, *,
                   dry_run: bool = False) -> dict:
    """执行模板克隆 + 主体替换。"""
    return doc_scaffold.scaffold_apply(
        template, output, mapping, dry_run=dry_run,
    )


def new_table(docx_path: str, *,
              type: str = "grid",
              cols: int = 2, rows: int = 3,
              headers: list[str] | None = None,
              data: list[list[str]] | None = None,
              col_widths: list[str] | None = None,
              merged_spec: list | None = None,
              nested_spec: list | None = None,
              diagonal_labels: list | None = None,
              at: int | None = None,
              after: str | None = None,
              font: str = "宋体",
              font_size: float = 11.5) -> dict:
    """在文档中插入新表格。"""
    return new_table_ops.insert_table(
        docx_path=docx_path, type=type, cols=cols, rows=rows,
        headers=headers, data=data, col_widths=col_widths,
        merged_spec=merged_spec, nested_spec=nested_spec,
        diagonal_labels=diagonal_labels,
        at=at, after=after, font=font, font_size=font_size,
    )


def stats(docx_path: str, *, fmt: str = "json") -> dict:
    """快速文档摘要。"""
    return doc_stats.doc_stats(docx_path)


def toc_generate(docx_path: str, *,
                 level_from: int = 1, level_to: int = 3,
                 position: str = "after-title",
                 output: str | None = None) -> dict:
    """生成 TOC 域。"""
    return toc_ops.toc_generate(
        docx_path, level_from=level_from, level_to=level_to,
        position=position, out=output,
    )


def toc_refresh(docx_path: str, output: str | None = None) -> dict:
    """刷新已有 TOC 域。"""
    return toc_ops.toc_refresh(docx_path, out=output)


def replace_in_place(docx_path: str, para: int, old: str, new: str, *,
                     author: str = "agent",
                     output: str | None = None) -> edit_ops.EditResult:
    """原地替换段落文本（保留原 run 格式），用 TC 标记。"""
    return edit_ops.replace_text_in_place(
        docx_path, para, old, new, author=author, output=output,
    )


def delete_paragraph(docx_path: str, para: int, *,
                     author: str = "agent",
                     output: str | None = None) -> edit_ops.EditResult:
    """整段标记为 TC 删除。"""
    return edit_ops.delete_paragraph_tc(
        docx_path, para, author=author, output=output,
    )
