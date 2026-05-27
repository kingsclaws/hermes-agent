"""
cli.py — lex_docx 命令行入口

用法：
    python -m lex_docx <command> [options]
    # 或软链后：
    lex_docx <command> [options]

Commands:
  ── 检查 ──────────────────────────────────────────────────────────────────────
    lint                检查 DOCX 格式（输出 JSON 或文本；支持 Profile+Selector 模式）

  ── 数据填充 ───────────────────────────────────────────────────────────────────
    extract             提取表格数据（输出 JSON）
    fill-table          按列映射填充表格
    fill-kv             填充 KV 表（基本信息类）
    fill-footer         批量替换所有 footer parts 中的文本（含 textbox；legacy flat command）

  ── 表格操作 ───────────────────────────────────────────────────────────────────
    format-table        统一表格格式（底色/边框/列宽/对齐）
    copy-table          跨文档表格复制（含格式）
    table-inspect       读取表格完整格式信息（底色/边框/列宽/字体/风格检测）
    table-format-brush  表格格式刷（从参考表格复制格式到目标表格）

  ── 段落/Track Changes ─────────────────────────────────────────────────────────
    tc-insert           段落级 TC INS（在指定段落插入文字）
    tc-delete           段落级 TC DEL（将指定段落标记为删除）
    tc-list             列出文档中所有 Track Changes（w:ins / w:del）
    tc-accept           接受所有（或指定作者的）Track Changes
    tc-reject           拒绝所有（或指定作者的）Track Changes
    highlight           批量高亮段落范围
    format-brush        格式刷（从参考段落复制格式到目标段落）
    set-outline-level   设置段落大纲级别（w:outlineLvl，独立于 Heading 样式）
    para-query          全文格式检索（按字体/样式/大纲级别/字号/粗斜体/对齐等过滤段落）

  ── 文档维护 ───────────────────────────────────────────────────────────────────
    cleanup             清理空段落 / 孤儿编号
    bold-terms          加粗定义术语
    comment-clean       删除所有批注（commentRange* + commentReference runs + comments.xml）
    header-clean        清除所有 header 内容（可选：移除 headerReference 引用）
    footer-audit        审查所有 footer OPC parts（legacy flat command）
    numbering inspect   检查段落编号状态（own/effective numPr）
    numbering restart   仅重置编号计数，不调整缩进/样式等其他格式
    section-restart-numbering  按章节范围重置子级编号计数
    doctor check        格式诊断（字体/编号/大纲/样式引用/TOC/footer，只读）
    doctor fix          自动修复（D01/D02/D04/D05/D07/D08，支持 --dry-run）
    inject              读取 JSON 计划文件一键执行注入
    clean               执行版一键清理（accept/reject TC + 批注 + header，支持 --dry-run）

  ── 文档生成 ──────────────────────────────────────────────────────────────────
    create              从零创建标准 OPC 骨架 .docx（全程无 python-docx）
    scaffold            模板克隆 + 主体替换（含 mapping 预览 + 残留扫描）
    new-table           在文档中插入新表格（grid / kv / merged / nested / diagonal）
    toc                 目录生成/刷新（基于 Heading1-3）
    stats               快速文档摘要（段落数/表格数/字体分布/TCC 数）

  ── 基础编辑 ──────────────────────────────────────────────────────────────────
    insert              在段落末尾插入文本（--tc 启用 Track Changes）
    replace             替换段内文字（--tc 启用 Track Changes）
    delete              删除段内文字（--tc 启用 Track Changes）

  ── Canonical family aliases ─────────────────────────────────────────────────
    footer audit        审查所有 footer OPC parts（推荐入口）
    footer fill         批量替换所有 footer parts 中的文本（推荐入口）
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
import zipfile
from pathlib import Path


# =========================================================================== #
# helpers                                                                      #
# =========================================================================== #

def _load_doc(path: str, backend: str = "python-docx"):
    if backend == "openxml":
        from lex_docx.openxml_package import OpenXmlDocument
        return OpenXmlDocument(path)
    from docx import Document
    return Document(path)


def _save_doc(doc, path: str, *, validate_zip: bool = False):
    if not validate_zip:
        doc.save(path)
        print(f"saved → {path}", file=sys.stderr)
        return

    target_path = Path(path)
    suffix = target_path.suffix or ".docx"
    fd, tmp_path = tempfile.mkstemp(prefix=target_path.stem + ".", suffix=suffix, dir=str(target_path.parent))
    os.close(fd)
    tmp_path_obj = Path(tmp_path)

    try:
        doc.save(str(tmp_path_obj))
        with zipfile.ZipFile(tmp_path_obj, "r") as zf:
            bad_member = zf.testzip()
            if bad_member is not None:
                raise zipfile.BadZipFile(f"corrupt member: {bad_member}")
        os.replace(tmp_path_obj, target_path)
        print(f"saved → {path}", file=sys.stderr)
    except Exception:
        if tmp_path_obj.exists():
            print(f"save failed, temp kept → {tmp_path_obj}", file=sys.stderr)
        raise


def _load_cfg(cfg_path: str | None):
    from lex_docx import DocConfig
    if not cfg_path:
        return DocConfig()
    data = json.loads(Path(cfg_path).read_text(encoding="utf-8"))
    return DocConfig(**data)


def _load_json(path: str):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _out(data, fmt: str = "json"):
    if fmt == "json":
        print(json.dumps(data, ensure_ascii=False, indent=2))
    else:
        if isinstance(data, list):
            for item in data:
                print(item)
        else:
            print(data)


# =========================================================================== #
# commands                                                                     #
# =========================================================================== #

def _resolve_backend(name: str | None) -> str:
    from lex_docx.backends.common import normalize_backend
    return normalize_backend(name)


def cmd_lint(args):
    """
    lex_docx lint report.docx [--cfg config.json] [--rules rule1,rule2] [--fmt text|json]
    lex_docx lint report.docx --lint-cfg lint-config.json [--profile dd_report_draft]
    """
    from lex_docx import lint

    cfg        = _load_cfg(args.cfg) if args.cfg else None
    rules      = args.rules.split(",") if args.rules else None
    lint_cfg   = args.lint_cfg or None
    profile    = args.profile or None

    results = lint.check(
        args.docx,
        config=cfg,
        rules=rules,
        lint_cfg=lint_cfg,
        profile=profile,
    )

    # ── lint_cfg 模式：gate 判定 + 增强输出 ──────────────────────────────── #
    if lint_cfg:
        from lex_docx import lint_config as lc
        raw_cfg = lc.load_file(lint_cfg) if not isinstance(lint_cfg, dict) else lint_cfg
        resolved = lc.resolve(raw_cfg, profile_name=profile, doc_path=args.docx)
        gate_result = lc.gate_check(results, resolved.gate)

        if args.fmt == "json":
            _out({
                "profile":  resolved.name,
                "gate":     gate_result["gate"],
                "summary":  gate_result["summary"],
                "fail_reasons": gate_result["fail_reasons"],
                "results": [{
                    "rule":      r.rule,
                    "severity":  r.severity,
                    "passed":    r.passed,
                    "detail":    r.detail,
                    "locations": r.locations,
                } for r in results],
            })
        else:
            gate_icon = "✅ PASS" if gate_result["gate"] == "PASS" else "❌ FAIL"
            print(f"Profile: {resolved.name}  Gate: {gate_icon}")
            s = gate_result["summary"]
            print(f"Summary: error={s['error']} warn={s['warn']} info={s.get('info',0)}")
            if gate_result["fail_reasons"]:
                for reason in gate_result["fail_reasons"]:
                    print(f"  ⛔ {reason}")
            print()
            for r in results:
                sev_tag = {"error": "🔴", "warn": "🟡", "info": "🔵"}.get(r.severity, "⚪")
                icon = "✅" if r.passed else f"❌{sev_tag}"
                print(f"{icon} {r.rule}: {r.detail}")
                for loc in r.locations[:5]:
                    print(f"    → {loc.get('context', loc)}")
                if len(r.locations) > 5:
                    print(f"    … 共 {len(r.locations)} 处")
            sys.exit(0 if gate_result["gate"] == "PASS" else 1)
        return

    # ── 经典模式（向后兼容）──────────────────────────────────────────────── #
    if args.fmt == "json":
        _out([{
            "rule":      r.rule,
            "passed":    r.passed,
            "detail":    r.detail,
            "locations": r.locations,
        } for r in results])
    else:
        any_fail = False
        for r in results:
            icon = "✅" if r.passed else "❌"
            print(f"{icon} {r.rule}: {r.detail}")
            for loc in r.locations:
                print(f"    → {loc}")
            if not r.passed:
                any_fail = True
        sys.exit(1 if any_fail else 0)


def cmd_extract(args):
    """
    lex_docx extract source.docx --table 3 [--near "股东情况"] [--fmt json|csv]
    """
    from lex_docx import table_ops
    kwargs = {}
    if args.near:
        kwargs["near_text"] = args.near
    elif args.table is not None:
        kwargs["table_index"] = args.table
    else:
        print("error: --table or --near required", file=sys.stderr)
        sys.exit(1)

    data = table_ops.extract_table(args.docx, output="list_of_dicts", **kwargs)

    if args.fmt == "csv":
        import csv, io
        buf = io.StringIO()
        if data:
            w = csv.DictWriter(buf, fieldnames=data[0].keys())
            w.writeheader()
            w.writerows(data)
        print(buf.getvalue(), end="")
    else:
        _out(data)


def cmd_fill_table(args):
    """
    lex_docx fill-table report.docx --table 12 --data data.json
                [--map map.json] [--cfg config.json] [--out output.docx]
    """
    from lex_docx import table_ops
    doc = _load_doc(args.docx, backend="openxml")
    data = _load_json(args.data)
    column_mapping = _load_json(args.map) if args.map else None
    cfg = _load_cfg(args.cfg)

    if args.auto_del:
        # auto delete extra rows first
        table_ops.adjust_rows(doc, args.table,
                               target_data_rows=len(data), cfg=cfg)

    result = table_ops.fill_table(doc, args.table, data,
                                   column_mapping=column_mapping, cfg=cfg)
    _save_doc(doc, args.out or args.docx)
    _out({"filled_rows": result["filled"], "format_warnings": result.get("format_warnings", [])})


def cmd_fill_kv(args):
    """
    lex_docx fill-kv report.docx --table 8 --data data.json
               [--key-cols 0,2] [--cfg config.json] [--out output.docx]
    """
    from lex_docx import table_ops
    doc = _load_doc(args.docx, backend="openxml")
    data = _load_json(args.data)
    cfg = _load_cfg(args.cfg)

    key_columns = None
    if args.key_cols:
        key_columns = [int(x) for x in args.key_cols.split(",")]

    result = table_ops.fill_kv_table(
        doc, args.table, data,
        key_columns=key_columns,
        cfg=cfg,
    )
    _save_doc(doc, args.out or args.docx)
    _out({"filled_cells": result["filled"], "format_warnings": result.get("format_warnings", [])})


def cmd_format_table(args):
    """
    lex_docx format-table report.docx --table 12
               [--shading D9E2F3] [--borders single] [--cfg config.json] [--out output.docx]
    """
    from lex_docx import table_ops
    doc = _load_doc(args.docx, backend="openxml")
    cfg = _load_cfg(args.cfg)

    kwargs = {}
    if args.shading:
        kwargs["header_shading"] = args.shading
    if args.borders:
        kwargs["borders"] = args.borders
    if args.widths:
        kwargs["column_widths"] = [int(x) for x in args.widths.split(",")]
    if args.align:
        kwargs["column_alignments"] = args.align.split(",")

    table_ops.format_table(doc, args.table, cfg=cfg, **kwargs)
    _save_doc(doc, args.out or args.docx)
    _out({"status": "ok"})


def cmd_cleanup(args):
    """
    lex_docx cleanup report.docx [--range 0,200] [--mode report|fix]
               [--keep-styles "Heading 1,Heading 2"] [--cfg config.json] [--out output.docx]
    """
    from lex_docx import cleanup
    cfg = _load_cfg(args.cfg)
    para_range = None
    if args.range:
        a, b = args.range.split(",")
        para_range = (int(a), int(b))
    keep_styles = args.keep_styles.split(",") if args.keep_styles else None

    doc = _load_doc(args.docx)
    as_tc_del = (args.mode != "delete")   # default: TC DEL; "delete" = hard remove

    result = cleanup.cleanup_all(
        doc,
        as_tc_del=as_tc_del,
        para_range=para_range,
        cfg=cfg,
        keep_styles=keep_styles,
    )

    if args.mode != "report":
        _save_doc(doc, args.out or args.docx)

    _out(result)


def cmd_bold_terms(args):
    """
    lex_docx bold-terms report.docx --para 39 [--out output.docx]
    lex_docx bold-terms report.docx --scan [--range 0,100]
    """
    from lex_docx import defined_terms
    doc = _load_doc(args.docx)

    if args.scan:
        para_range = None
        if args.range:
            a, b = args.range.split(",")
            para_range = (int(a), int(b))
        results = defined_terms.scan_terms(doc, para_range=para_range)
        _out(results)
        return

    if args.para is None:
        print("error: --para required (or use --scan)", file=sys.stderr)
        sys.exit(1)

    terms = defined_terms.auto_bold(doc, paragraph_index=args.para)
    _save_doc(doc, args.out or args.docx)
    _out({"bolded": terms})


def cmd_copy_table(args):
    """
    lex_docx copy-table src.docx [--src-table N | --src-near TEXT] dst.docx
               --dst-pos after_para:N|replace_table:N [--out out.docx]
    """
    from lex_docx import table_ops

    cfg = _load_cfg(args.cfg) if hasattr(args, "cfg") and args.cfg else None
    dst_doc = _load_doc(args.dst_docx, backend="openxml")

    transform = {}
    if hasattr(args, "cols") and args.cols:
        transform["columns"] = [int(c) for c in args.cols.split(",")]
    if hasattr(args, "max_rows") and args.max_rows:
        transform["max_rows"] = args.max_rows
    if hasattr(args, "rename") and args.rename:
        import json
        transform["rename_headers"] = json.loads(args.rename)

    kwargs: dict = {}
    if hasattr(args, "src_table") and args.src_table is not None:
        kwargs["src_table_index"] = args.src_table
    if hasattr(args, "src_near") and args.src_near:
        kwargs["src_near_text"] = args.src_near

    table_ops.copy_table(
        src_doc=args.src_docx,
        dst_doc=dst_doc,
        dst_position=args.dst_pos,
        transform=transform or None,
        cfg=cfg,
        **kwargs,
    )
    _save_doc(dst_doc, args.out or args.dst_docx)
    print(f"ok: table copied → {args.out or args.dst_docx}")


def cmd_table_inspect(args):
    """
    lex_docx table-inspect report.docx --table 5 [--fmt json|text]
    """
    from lex_docx import table_ops
    kwargs = {}
    if args.near:
        kwargs["near_text"] = args.near
    else:
        kwargs["table_index"] = args.table
    result = table_ops.inspect_table(args.docx, preview_len=args.preview_len, preview_rows=args.preview_rows, **kwargs)
    if args.fmt == "text":
        t = result
        print(f"Table {t['table_index']}: {t['rows']}行 × {t['cols']}列  [{t['detected_style']}]")
        print(f"  列宽(dxa): {t['col_widths_dxa']}")
        print(f"  列对齐:    {t['col_aligns']}")
        print(f"  边框: {t['borders']}")
        print(f"  标题行: {t['header_row']}")
        print(f"  数据行: {t['data_rows']}")
        if t.get('sample_rows'):
            print("  样例行:")
            for row in t['sample_rows']:
                print(f"    R{row['row']}: {row['cells']}")
    else:
        _out(result)


def cmd_table_replace(args):
    """
    lex_docx table-replace report.docx --table 0 --row 1 --col 2 --old "旧文" --new "新文" [--para 0] [--out out.docx]
    """
    from lex_docx import table_ops
    cfg = _load_cfg(args.cfg)
    doc = _load_doc(args.docx, backend="openxml")
    result = table_ops.replace_text_in_table_cell(
        doc,
        table_index=args.table,
        row_index=args.row,
        col_index=args.col,
        old_text=args.old,
        new_text=args.new,
        paragraph_index=args.para,
        cfg=cfg,
        occurrence=args.occurrence,
        after_text=args.after_text,
        before_text=args.before_text,
        replace_all=args.replace_all,
    )
    if not result.get("ok"):
        _out({"ok": False, **result})
        sys.exit(1)
    _save_doc(doc, args.out or args.docx)
    _out({"ok": True, **result})


def cmd_table_format_brush(args):
    """
    lex_docx table-format-brush report.docx --ref-table 5 --target-table 12 --out out.docx
    lex_docx table-format-brush template.docx --ref-table 3 report.docx --target-table 12 --out out.docx
    """
    from lex_docx import table_ops

    # 单文档 vs 跨文档
    if args.target_docx:
        ref_src  = args.docx
        dst_doc  = _load_doc(args.target_docx, backend="openxml")
        out_path = args.out or args.target_docx
    else:
        dst_doc  = _load_doc(args.docx, backend="openxml")
        ref_src  = dst_doc
        out_path = args.out or args.docx

    copy = args.copy.split(",") if args.copy else None
    result = table_ops.table_format_brush(
        ref_src, args.ref_table, dst_doc, args.target_table, copy=copy
    )
    _save_doc(dst_doc, out_path)
    _out({"ok": True, **result})


def cmd_tc_insert(args):
    """
    lex_docx tc-insert report.docx --para 180 --text "新增文字"
               [--pos end|start|N] [--bold] [--italic] [--highlight yellow]
               [--inherit-rpr true|style|auto] [--cfg config.json] [--out out.docx]
    """
    from lex_docx.tc_utils import tc_ins_text, next_tc_id
    from lex_docx.markup_codec import parse_markup
    cfg = _load_cfg(args.cfg)
    doc = _load_doc(args.docx, backend="openxml")

    author = cfg.author if cfg else "JT"
    tc_id  = next_tc_id(doc)
    para   = doc.paragraphs[args.para]

    inherit = args.inherit_rpr
    if inherit == "true":
        inherit = True

    style_rPr_map = None
    try:
        from lex_docx.format_brush import extract_style_rPr_map
        style_rPr_map = extract_style_rPr_map(doc)
    except Exception:
        style_rPr_map = None

    text_input = args.text_markup if getattr(args, "text_markup", "") else args.text
    if not text_input:
        print("error: either --text or --text-markup is required", file=sys.stderr)
        sys.exit(1)
    segments = parse_markup(text_input) if getattr(args, "text_markup", "") else None
    if segments is None:
        ins_el = tc_ins_text(
            para._element,
            text=args.text,
            tc_id=tc_id,
            author=author,
            position=args.pos,
            bold=args.bold,
            italic=args.italic,
            highlight=args.highlight or None,
            inherit_rPr=inherit,
            style_rPr_map=style_rPr_map,
        )
    else:
        # 插入第一段后，其余段落按 end 追加，确保不破坏原有接口
        first_text, first_fmt = (segments[0] if segments else ("", {}))
        ins_el = tc_ins_text(
            para._element,
            text=first_text,
            tc_id=tc_id,
            author=author,
            position=args.pos,
            bold=bool(first_fmt.get("bold")),
            italic=bool(first_fmt.get("italic")),
            highlight=("yellow" if first_fmt.get("highlight") else None),
            inherit_rPr=inherit,
            style_rPr_map=style_rPr_map,
        )
        for seg_text, seg_fmt in segments[1:]:
            tc_id += 1
            tc_ins_text(
                para._element,
                text=seg_text,
                tc_id=tc_id,
                author=author,
                position="end",
                bold=bool(seg_fmt.get("bold")),
                italic=bool(seg_fmt.get("italic")),
                highlight=("yellow" if seg_fmt.get("highlight") else None),
                inherit_rPr=inherit,
                style_rPr_map=style_rPr_map,
            )
    fmt_check = None
    try:
        from lex_docx.tc_utils import assess_para_format_context
        from docx.oxml.ns import qn
        run_el = ins_el.find(qn("w:r")) if ins_el is not None else None
        run_rPr = run_el.find(qn("w:rPr")) if run_el is not None else None
        fmt_check = assess_para_format_context(para._element, run_rPr, style_rPr_map=style_rPr_map)
    except Exception:
        fmt_check = None
    _save_doc(doc, args.out or args.docx)
    _out({"ok": True, "para": args.para, "text": text_input, "format_context": fmt_check, "markup_mode": bool(getattr(args, "text_markup", ""))})


def cmd_tc_replace(args):
    """
    lex_docx tc-replace report.docx --para 180 --old "旧文" --new "新文" [--cfg config.json] [--out out.docx]
    """
    from lex_docx.tc_utils import tc_replace_first_in_para, next_tc_id
    cfg = _load_cfg(args.cfg)
    doc = _load_doc(args.docx, backend="openxml")
    author = cfg.author if cfg else "JT"

    if args.para >= len(doc.paragraphs):
        print("error: --para out of range", file=sys.stderr)
        sys.exit(1)

    style_rPr_map = None
    try:
        from lex_docx.format_brush import extract_style_rPr_map
        style_rPr_map = extract_style_rPr_map(doc)
    except Exception:
        style_rPr_map = None
    result = tc_replace_first_in_para(
        doc.paragraphs[args.para]._element,
        old_text=args.old,
        new_text=args.new,
        tc_id=next_tc_id(doc),
        author=author,
        inherit_rPr=True,
        style_rPr_map=style_rPr_map,
        occurrence=args.occurrence,
        after_text=args.after_text,
        before_text=args.before_text,
    )
    if not result.get("ok"):
        _out({"ok": False, **result})
        sys.exit(1)
    fmt_check = None
    try:
        from lex_docx.tc_utils import assess_para_format_context
        from lxml import etree
        ins_nodes = doc.paragraphs[args.para]._element.findall(qn("w:ins"))
        target_ins = None
        for node in ins_nodes:
            if node.get(qn("w:id")) == result.get("inserted_id"):
                target_ins = node
                break
        run_el = target_ins.find(qn("w:r")) if target_ins is not None else None
        run_rPr = run_el.find(qn("w:rPr")) if run_el is not None else None
        fmt_check = assess_para_format_context(doc.paragraphs[args.para]._element, run_rPr, style_rPr_map=style_rPr_map)
    except Exception:
        fmt_check = None
    _save_doc(doc, args.out or args.docx)
    _out({"ok": True, "para": args.para, **result, "format_context": fmt_check})


def cmd_tc_delete(args):
    """
    lex_docx tc-delete report.docx --para 180
    lex_docx tc-delete report.docx --range 180,195
               [--cfg config.json] [--out out.docx]
    """
    from lex_docx.tc_utils import tc_del_paragraph, next_tc_id
    cfg    = _load_cfg(args.cfg)
    doc    = _load_doc(args.docx, backend="openxml")
    author = cfg.author if cfg else "JT"
    tc_id  = next_tc_id(doc)

    if args.range:
        a, b = args.range.split(",")
        indices = list(range(int(a), int(b) + 1))
    elif args.para is not None:
        indices = [args.para]
    else:
        print("error: --para or --range required", file=sys.stderr)
        sys.exit(1)

    deleted = []
    for idx in indices:
        if idx >= len(doc.paragraphs):
            continue
        tc_del_paragraph(doc.paragraphs[idx]._element, tc_id, author)
        deleted.append(idx)
        tc_id += 1

    _save_doc(doc, args.out or args.docx)
    _out({"ok": True, "deleted": deleted})


def cmd_highlight(args):
    """
    lex_docx highlight report.docx --range 180,195
    lex_docx highlight report.docx --para 180
               [--color yellow] [--out out.docx]
    """
    from lxml import etree
    from docx.oxml.ns import qn
    from docx.oxml import OxmlElement

    doc = _load_doc(args.docx, backend="openxml")
    color = args.color or "yellow"

    if args.range:
        a, b = args.range.split(",")
        indices = list(range(int(a), int(b) + 1))
    elif args.para is not None:
        indices = [args.para]
    else:
        print("error: --para or --range required", file=sys.stderr)
        sys.exit(1)

    marked = []
    for idx in indices:
        if idx >= len(doc.paragraphs):
            continue
        para_el = doc.paragraphs[idx]._element
        runs = para_el.findall(qn("w:r"))
        if not runs:
            # 段落无 run — 创建空 run 承载 highlight
            r = OxmlElement("w:r")
            rPr = OxmlElement("w:rPr")
            hl = OxmlElement("w:highlight")
            hl.set(qn("w:val"), color)
            rPr.append(hl)
            r.insert(0, rPr)
            para_el.append(r)
        else:
            for run_el in runs:
                rPr = run_el.find(qn("w:rPr"))
                if rPr is None:
                    rPr = OxmlElement("w:rPr")
                    run_el.insert(0, rPr)
                existing = rPr.find(qn("w:highlight"))
                if existing is not None:
                    rPr.remove(existing)
                hl = OxmlElement("w:highlight")
                hl.set(qn("w:val"), color)
                rPr.append(hl)
        marked.append(idx)

    _save_doc(doc, args.out or args.docx)
    _out({"ok": True, "highlighted": marked, "color": color})


def cmd_format_brush(args):
    """
    lex_docx format-brush report.docx --ref 171 --target 177,178,180
    lex_docx format-brush report.docx --ref 171 --range 175,185
               [--copy indent,spacing,style] [--out out.docx]
    """
    from lex_docx.backends import format_backend

    backend = _resolve_backend(getattr(args, "backend", None) or "ooxml")
    doc = _load_doc(args.docx, backend="openxml" if backend == "ooxml" else "python-docx")

    if args.target:
        indices = [int(x) for x in args.target.split(",")]
    elif args.range:
        a, b = args.range.split(",")
        indices = list(range(int(a), int(b) + 1))
    else:
        print("error: --target or --range required", file=sys.stderr)
        sys.exit(1)

    copy = args.copy.split(",") if args.copy else None

    modified = format_backend.apply_format_brush(
        backend=backend,
        doc=doc,
        target_indices=indices,
        reference_index=args.ref,
        copy=copy,
        skip_if_jc=args.skip_if_jc or None,
        safe=args.safe,
        validate_each=args.validate_each,
    )

    if modified["failed_at"] is not None:
        _out({
            "ok": False,
            "modified": modified["modified"],
            "details": modified["details"],
            "failed_at": modified["failed_at"],
            "error": modified["error"],
            "safe": args.safe,
            "validate_each": args.validate_each,
            "effective_copy": modified["effective_copy"],
            "backend": backend,
        })
        sys.exit(1)

    _save_doc(doc, args.out or args.docx, validate_zip=True)
    _out({
        "ok": True,
        "modified": modified["modified"],
        "details": modified["details"],
        "safe": args.safe,
        "validate_each": args.validate_each,
        "effective_copy": modified["effective_copy"],
        "backend": backend,
    })


def cmd_set_outline_level(args):
    """
    lex_docx set-outline-level report.docx --para 5 --level 2
    lex_docx set-outline-level report.docx --range 10,20 --level 2
    lex_docx set-outline-level report.docx --style "自定义标题" --level 1
    lex_docx set-outline-level report.docx --range 0,200 --style "自定义标题" --level 1
    lex_docx set-outline-level report.docx --para 5 --level none  # 清除大纲级别
    """
    from lex_docx import format_brush

    doc = _load_doc(args.docx)
    paras = doc.paragraphs

    # 确定目标段落索引
    if args.para is not None:
        indices: list[int] = [args.para]
    elif args.range:
        lo, hi = [int(x.strip()) for x in args.range.split(",", 1)]
        indices = list(range(lo, hi + 1))
    else:
        indices = list(range(len(paras)))

    # 按 style 过滤
    if args.style:
        indices = [i for i in indices
                   if i < len(paras) and paras[i].style and paras[i].style.name == args.style]

    # 解析 level：整数 1-9 或 "none"/0 表示清除
    if args.level.lower() == "none":
        level = None
    else:
        level = int(args.level)

    modified = format_brush.set_outline_level(doc, indices, level)
    _save_doc(doc, args.out or args.docx)
    _out({"modified": len(modified), "indices": modified})


def cmd_doctor(args):
    """
    lex_docx doctor check report.docx --font 楷体 --font-size 12
    lex_docx doctor fix   report.docx --font 楷体 --rules D01,D02,D04 --dry-run
    """
    from lex_docx import doctor as dr

    footer_blacklist = None
    if getattr(args, "footer_blacklist", None):
        footer_blacklist = [kw.strip() for kw in args.footer_blacklist.split(",") if kw.strip()]

    standards = dr.Standards(
        font=args.font or None,
        ascii_font=args.ascii_font or None,
        font_size=float(args.font_size) if args.font_size else None,
        toc_levels=tuple(int(x) for x in args.toc_levels.split("-")) if args.toc_levels else (1, 3),
        footer_blacklist=footer_blacklist,
    )
    rules = [r.strip().upper() for r in args.rules.split(",")] if args.rules else None

    para_range = None
    if args.range:
        lo, hi = [int(x.strip()) for x in args.range.split(",", 1)]
        para_range = (lo, hi + 1)

    if args.doctor_cmd == "check":
        doc = _load_doc(args.docx, backend="openxml")
        result = dr.check(doc, standards, rules=rules, para_range=para_range)
        _out(result.to_dict())

    elif args.doctor_cmd == "fix":
        doc = _load_doc(args.docx, backend="openxml")
        # check 先跑一遍
        check_result = dr.check(doc, standards, rules=rules, para_range=para_range)

        exclude_range = None
        if args.exclude_range:
            lo, hi = [int(x.strip()) for x in args.exclude_range.split(",", 1)]
            exclude_range = (lo, hi)

        if args.backup and not args.dry_run:
            import shutil
            shutil.copy2(args.docx, args.docx + ".bak")

        fix_result = dr.fix(
            doc, check_result, standards,
            rules=rules,
            exclude_range=exclude_range,
            dry_run=args.dry_run,
        )

        if not args.dry_run:
            _save_doc(doc, args.out or args.docx)

        _out({
            "dry_run":   fix_result.dry_run,
            "fixed":     fix_result.fixed,
            "skipped":   fix_result.skipped,
            "log":       fix_result.log,
            "check_summary": check_result.summary(),
        })



def cmd_para_runs(args):
    """
    lex_docx para-runs report.docx --para 10 [--fmt json|text]
    """
    from lex_docx import openxml_package
    from lex_docx.markup_codec import runs_to_markup
    from docx.oxml.ns import qn
    doc = _load_doc(args.docx, backend="openxml")
    if args.para < 0 or args.para >= len(doc.paragraphs):
        print("error: --para out of range", file=sys.stderr)
        sys.exit(1)
    para = doc.paragraphs[args.para]
    items = []
    for i, run_el in enumerate(para._element.iter(qn("w:r"))):
        text_val = ''.join(t.text or '' for t in run_el.findall(qn("w:t")))
        rPr = run_el.find(qn("w:rPr"))
        bold = bool(rPr is not None and (rPr.find(qn("w:b")) is not None or rPr.find(qn("w:bCs")) is not None))
        italic = bool(rPr is not None and (rPr.find(qn("w:i")) is not None or rPr.find(qn("w:iCs")) is not None))
        font = {}
        size = None
        if rPr is not None:
            rFonts = rPr.find(qn("w:rFonts"))
            if rFonts is not None:
                for k, q in (("eastAsia", qn("w:eastAsia")), ("ascii", qn("w:ascii")), ("hAnsi", qn("w:hAnsi"))):
                    v = rFonts.get(q)
                    if v: font[k] = v
            sz = rPr.find(qn("w:sz"))
            if sz is not None and sz.get(qn("w:val")):
                try: size = int(sz.get(qn("w:val"))) / 2
                except: pass
        items.append({"run": i, "text": text_val, "bold": bold, "italic": italic, "font": font, "size": size})
    payload = {"runs": items}
    if getattr(args, "with_markup", False):
        payload["markup"] = runs_to_markup(para._element)
    if args.fmt == "text":
        for item in items:
            print(item)
        if "markup" in payload:
            print("markup:", payload["markup"])
    else:
        _out(payload if getattr(args, "with_markup", False) else items)


def cmd_para_query(args):
    """
    lex_docx para-query report.docx --font "仿宋"
    lex_docx para-query report.docx --outline-level 1,2
    lex_docx para-query report.docx --style "Heading 1" "Heading 2"
    lex_docx para-query report.docx --font "仿宋" --font-size 12
    lex_docx para-query report.docx --bold
    lex_docx para-query report.docx --range 0,200 --font "仿宋"
    """
    from lex_docx import para_query

    doc = _load_doc(args.docx)

    outline_level = None
    if args.outline_level:
        outline_level = [int(x.strip()) for x in args.outline_level.split(",")]

    para_range = None
    if args.range:
        lo, hi = [int(x.strip()) for x in args.range.split(",", 1)]
        para_range = (lo, hi + 1)

    bold = None
    if args.bold:
        bold = True
    elif args.no_bold:
        bold = False

    italic = None
    if args.italic:
        italic = True
    elif args.no_italic:
        italic = False

    results = para_query.query(
        doc,
        style=args.style or None,
        font=args.font,
        font_size=float(args.font_size) if args.font_size else None,
        outline_level=outline_level,
        bold=bold,
        italic=italic,
        color=args.color,
        jc=args.jc or None,
        para_range=para_range,
        text_preview_len=args.preview_len,
    )

    if args.fmt == "text":
        for r in results:
            ol = f"大纲{r['outline_level']}级" if r["outline_level"] else ""
            font_str = "/".join(r["font_eastasia"] or r["font_ascii"] or [])
            sz_str = "/".join(str(s) for s in r["font_size"]) + "pt" if r["font_size"] else ""
            flags = " ".join(f for f, v in [("粗", r["bold"]), ("斜", r["italic"])] if v)
            meta = "  ".join(x for x in [r["style"], ol, font_str, sz_str, flags] if x)
            print(f"[{r['index']:>4}] {meta}")
            print(f"       {r['text']}")
    else:
        _out(results)


def cmd_inject(args):
    """
    lex_docx inject plan.json [--cfg config.json] [--out out.docx]

    plan.json 结构：
    {
      "doc_path": "report.docx",
      "out_path": "report_out.docx",    // 可被 --out 覆盖
      "target_range": [200, 300],       // 可选
      "tables": [
        {"table_index": 8, "data": {...}, "mode": "kv", "key_columns": [0, 2]},
        {"table_index": 12, "data": [...], "mode": "rows", "auto_adjust": true}
      ],
      "jt_notes": {"180": "待确认", "治理结构": "待核实"},
      "jt_cell_notes": [
        {"table_index": 8, "row_index": 2, "col_index": 1, "note_text": "待核实营业执照扫描件"}
      ],
      "auto_cleanup": true,
      "run_lint": true
    }
    """
    from lex_docx import inject_engine

    raw = _load_json(args.plan)
    cfg = _load_cfg(args.cfg)

    # out path: CLI --out 优先于 JSON 中的 out_path
    if args.out:
        raw["out_path"] = args.out

    # jt_notes key 从 JSON string 转回 int（JSON key 只能是 str）
    jt_notes_raw = raw.pop("jt_notes", {})
    jt_notes: dict = {}
    for k, v in jt_notes_raw.items():
        try:
            jt_notes[int(k)] = v
        except ValueError:
            jt_notes[k] = v

    tables = [inject_engine.TableFill(**t) for t in raw.pop("tables", [])]
    jt_cell_notes = [
        inject_engine.TableCellNote(**item) for item in raw.pop("jt_cell_notes", [])
    ]
    footer_replace = [
        inject_engine.FooterReplace(**fr) for fr in raw.pop("footer_replace", [])
    ]

    plan = inject_engine.InjectPlan(
        tables=tables,
        jt_notes=jt_notes,
        jt_cell_notes=jt_cell_notes,
        footer_replace=footer_replace,
        **raw,
    )
    result = inject_engine.execute(plan, cfg)
    _out({
        "summary":        result.summary(),
        "tables":         result.tables,
        "notes":          result.notes,
        "footer_replace": result.footer_replace,
        "cleanup":        result.cleanup,
    })


def cmd_numbering_inspect(args):
    """
    lex_docx numbering inspect report.docx [--range 0,200] [--style Header2] [--outline-level 2,3]
    """
    from lex_docx.backends import numbering_backend

    backend = _resolve_backend(getattr(args, "backend", None) or "ooxml")
    doc = _load_doc(args.docx, backend="openxml" if backend == "ooxml" else "python-docx")

    para_range = None
    if args.range:
        lo, hi = [int(x.strip()) for x in args.range.split(",", 1)]
        para_range = (lo, hi + 1)

    outline_levels = None
    if args.outline_level:
        outline_levels = [int(x.strip()) for x in args.outline_level.split(",")]

    styles = args.style or None
    results = numbering_backend.inspect_numbering(
        backend=backend,
        doc=doc,
        para_range=para_range,
        styles=styles,
        outline_levels=outline_levels,
        preview_len=args.preview_len,
    )

    if args.fmt == "text":
        for item in results:
            print(
                f"[{item['index']:>4}] style={item['style']} outline={item['outline_level']} "
                f"numId={item['numId']} ilvl={item['ilvl']}"
            )
            print(f"       {item['text']}")
    else:
        _out({"backend": backend, "items": results})


def cmd_numbering_restart(args):
    """
    lex_docx numbering restart report.docx --start 120 --style Header2 --start-at 1 [--dry-run]
    """
    from lex_docx.backends import numbering_backend

    backend = _resolve_backend(getattr(args, "backend", None) or "ooxml")
    doc = _load_doc(args.docx, backend="openxml" if backend == "ooxml" else "python-docx")

    outline_levels = None
    if args.outline_level:
        outline_levels = [int(x.strip()) for x in args.outline_level.split(",")]

    linked_levels = None
    if args.levels:
        linked_levels = [int(x.strip()) for x in args.levels.split(",") if x.strip()]

    result = numbering_backend.restart_numbering(
        backend=backend,
        doc=doc,
        start_para=args.start,
        styles=args.style or None,
        outline_levels=outline_levels,
        start_at=args.start_at,
        dry_run=args.dry_run,
        multilevel_link=args.multilevel_link,
        linked_levels=linked_levels,
    )
    result["backend"] = backend

    if not args.dry_run:
        _save_doc(doc, args.out or args.docx, validate_zip=True)
    _out(result)


def cmd_section_restart_numbering(args):
    """
    lex_docx section-restart-numbering report.docx --heading 80 --style Header2 --start-at 1 [--dry-run]
    """
    from lex_docx.backends import numbering_backend

    backend = _resolve_backend(getattr(args, "backend", None) or "ooxml")
    doc = _load_doc(args.docx, backend="openxml" if backend == "ooxml" else "python-docx")
    start, end = numbering_backend.find_section_scope(backend=backend, doc=doc, heading_para=args.heading)

    outline_levels = None
    if args.outline_level:
        outline_levels = [int(x.strip()) for x in args.outline_level.split(",")]

    linked_levels = None
    if args.levels:
        linked_levels = [int(x.strip()) for x in args.levels.split(",") if x.strip()]

    all_items = numbering_backend.inspect_numbering(
        backend=backend,
        doc=doc,
        para_range=(start, end),
        styles=args.style or None,
        outline_levels=outline_levels,
    )
    items = all_items["items"] if isinstance(all_items, dict) else all_items
    if not items:
        _out({"changed": [], "section_range": [start, end], "dry_run": args.dry_run, "backend": backend})
        return

    result = numbering_backend.restart_numbering(
        backend=backend,
        doc=doc,
        start_para=items[0]["index"],
        styles=args.style or None,
        outline_levels=outline_levels,
        start_at=args.start_at,
        dry_run=args.dry_run,
        multilevel_link=args.multilevel_link,
        linked_levels=linked_levels,
    )
    result["section_range"] = [start, end]
    result["backend"] = backend

    if not args.dry_run:
        _save_doc(doc, args.out or args.docx, validate_zip=True)
    _out(result)


def cmd_footer_audit(args):
    """
    lex_docx footer-audit report.docx [--fmt json|text]
    """
    from lex_docx import footer_ops
    doc = _load_doc(args.docx, backend="openxml")
    results = footer_ops.audit_footers(doc)

    if args.fmt == "text":
        limit = args.preview_len
        for r in results:
            tb = "含textbox" if r["has_textbox"] else "无textbox"
            txt = r['text'] if limit == 0 else r['text'][:limit]
            print(f"[{r['rId']}] {r['footer_type']:8s}  {tb}  part={r['part_name']}")
            print(f"       text={txt!r}")
    else:
        _out(results)


def cmd_fill_footer(args):
    """
    lex_docx fill-footer report.docx --replace "Auspicious Linkage" --with "Rokid HK Ltd"
                [--out output.docx]
    """
    from lex_docx import footer_ops
    doc = _load_doc(args.docx, backend="openxml")
    count = footer_ops.fill_footer(doc, find=args.replace, replace=args.with_text)
    _save_doc(doc, args.out or args.docx)
    _out({"ok": True, "replaced": count, "find": args.replace, "replace": args.with_text})


def cmd_footer_family(args):
    """footer family alias: lex_docx footer audit|fill ..."""
    if args.footer_cmd == "audit":
        return cmd_footer_audit(args)
    if args.footer_cmd == "fill":
        return cmd_fill_footer(args)
    print("error: footer subcommand required (audit|fill)", file=sys.stderr)
    sys.exit(1)


def cmd_numbering_family(args):
    """numbering family alias: lex_docx numbering inspect|restart ..."""
    if args.numbering_cmd == "inspect":
        return cmd_numbering_inspect(args)
    if args.numbering_cmd == "restart":
        return cmd_numbering_restart(args)
    print("error: numbering subcommand required (inspect|restart)", file=sys.stderr)
    sys.exit(1)


# =========================================================================== #
# tc-list / tc-accept / tc-reject                                              #
# =========================================================================== #

def cmd_tc_list(args):
    """
    lex_docx tc-list report.docx [--author "JT"] [--type ins|del] [--range 100,200] [--fmt json|text]
    """
    from lex_docx import tc_ops
    doc = _load_doc(args.docx, backend="openxml")

    para_range = None
    if args.range:
        lo, hi = [int(x.strip()) for x in args.range.split(",", 1)]
        para_range = (lo, hi + 1)

    items = tc_ops.list_tc(
        doc,
        author_filter=args.author or None,
        para_range=para_range,
        type_filter=args.type or None,
    )

    if args.fmt == "text":
        limit = args.preview_len
        for item in items:
            lvl = f"[{item['level']}]"
            raw = repr(item["text"]) if item["text"] else ""
            txt = raw if limit == 0 else raw[:limit]
            print(f"id={item['id']:>4} {item['type']:3s} {lvl:11s} author={item['author']!r:16s} {txt}")
        print(f"Total: {len(items)}")
    else:
        _out({"total": len(items), "items": items})


def cmd_tc_accept(args):
    """
    lex_docx tc-accept report.docx [--author "JT"] [--type ins|del] [--range 100,200] [--out out.docx] [--dry-run]
    """
    from lex_docx import tc_ops
    doc = _load_doc(args.docx, backend="openxml")

    para_range = None
    if args.range:
        lo, hi = [int(x.strip()) for x in args.range.split(",", 1)]
        para_range = (lo, hi + 1)

    if args.dry_run:
        items = tc_ops.list_tc(
            doc,
            author_filter=args.author or None,
            para_range=para_range,
            type_filter=args.type or None,
        )
        _out({"dry_run": True, "would_accept": len(items), "items": items})
        return

    stats = tc_ops.accept_all(
        doc,
        author_filter=args.author or None,
        para_range=para_range,
        type_filter=args.type or None,
    )
    _save_doc(doc, args.out or args.docx)
    _out({"ok": True, **stats})


def cmd_tc_reject(args):
    """
    lex_docx tc-reject report.docx [--author "JT"] [--type ins|del] [--range 100,200] [--out out.docx] [--dry-run]
    """
    from lex_docx import tc_ops
    doc = _load_doc(args.docx, backend="openxml")

    para_range = None
    if args.range:
        lo, hi = [int(x.strip()) for x in args.range.split(",", 1)]
        para_range = (lo, hi + 1)

    if args.dry_run:
        items = tc_ops.list_tc(
            doc,
            author_filter=args.author or None,
            para_range=para_range,
            type_filter=args.type or None,
        )
        _out({"dry_run": True, "would_reject": len(items), "items": items})
        return

    stats = tc_ops.reject_all(
        doc,
        author_filter=args.author or None,
        para_range=para_range,
        type_filter=args.type or None,
    )
    _save_doc(doc, args.out or args.docx)
    _out({"ok": True, **stats})


# =========================================================================== #
# comment-clean / header-clean                                                 #
# =========================================================================== #

def cmd_comment_list(args):
    """
    lex_docx comment-list report.docx [--author "JH"] [--range 100,200] [--fmt json|text]
    """
    from lex_docx import tc_ops
    doc = _load_doc(args.docx, backend="openxml")

    para_range = None
    if args.range:
        lo, hi = [int(x.strip()) for x in args.range.split(",", 1)]
        para_range = (lo, hi + 1)

    items = tc_ops.list_comments(
        doc,
        author_filter=args.author or None,
        para_range=para_range,
    )

    if args.fmt == "text":
        limit = args.preview_len
        for item in items:
            para = f"P{item['para']}" if item["para"] is not None else "P?"
            author = item["author"] or "?"
            raw_text = repr(item["text"])
            text = raw_text if limit == 0 else raw_text[:limit]
            quoted = f" quoted={item['quoted_text']!r}" if item.get("quoted_text") else ""
            para_text = f" para_text={item['para_text']!r}" if item.get("para_text") else ""
            print(f"id={item['id']:>4} {para:6s} author={author!r:16s} {text}{quoted}{para_text}")
        print(f"Total: {len(items)}")
    else:
        _out({"total": len(items), "items": items})


def cmd_comment_clean(args):
    """
    lex_docx comment-clean report.docx [--author "JH"] [--range 100,200] [--out out.docx] [--dry-run]
    """
    from lex_docx import tc_ops
    doc = _load_doc(args.docx, backend="openxml")

    para_range = None
    if args.range:
        lo, hi = [int(x.strip()) for x in args.range.split(",", 1)]
        para_range = (lo, hi + 1)

    if args.dry_run:
        items = tc_ops.list_comments(
            doc,
            author_filter=args.author or None,
            para_range=para_range,
        )
        _out({"dry_run": True, "total": len(items), "items": items})
        return

    stats = tc_ops.clean_comments_filtered(
        doc,
        author_filter=args.author or None,
        para_range=para_range,
    )
    _save_doc(doc, args.out or args.docx)
    _out({"ok": True, **stats})


def cmd_comment_remove(args):
    """
    lex_docx comment-remove report.docx --id 12 [--out out.docx] [--dry-run]
    """
    from lex_docx import tc_ops
    doc = _load_doc(args.docx)

    target_ids = [x.strip() for x in args.id.split(",") if x.strip()]
    items = [x for x in tc_ops.list_comments(doc) if x.get("id") in target_ids]

    if args.dry_run:
        _out({"dry_run": True, "total": len(items), "items": items, "comment_ids": target_ids})
        return

    stats = tc_ops.clean_comments_filtered(doc, comment_ids=target_ids)
    _save_doc(doc, args.out or args.docx)
    _out({"ok": True, **stats})


def cmd_header_clean(args):
    """
    lex_docx header-clean report.docx [--remove-refs] [--out out.docx] [--dry-run]
    """
    from lex_docx import tc_ops
    doc = _load_doc(args.docx, backend="openxml")

    if args.dry_run:
        count = sum(
            1 for rel in doc.part.rels.values()
            if tc_ops._HEADERS_REL in rel.reltype
        )
        _out({"dry_run": True, "header_parts_found": count})
        return

    stats = tc_ops.clean_headers(
        doc,
        clear_text=True,
        remove_refs=args.remove_refs,
    )
    _save_doc(doc, args.out or args.docx)
    _out({"ok": True, **stats})


# =========================================================================== #
# clean  (issue #28 — execution-version workflow)                              #
# =========================================================================== #

def cmd_review_stats(args):
    """
    lex_docx review-stats report.docx [--author "JH"] [--footer-blacklist "Auspicious,Template"] [--fmt json|text]
    """
    from lex_docx import tc_ops, doctor as dr, footer_ops
    ox_doc = _load_doc(args.docx, backend="openxml")
    doc = _load_doc(args.docx, backend="python-docx")

    para_range = None
    if getattr(args, "range", None):
        lo, hi = [int(x.strip()) for x in args.range.split(",", 1)]
        para_range = (lo, hi + 1)

    tc_items = tc_ops.list_tc(
        ox_doc,
        author_filter=args.author or None,
        para_range=para_range,
    )
    comments = tc_ops.list_comments(
        ox_doc,
        author_filter=args.author or None,
        para_range=para_range,
    )
    footers = footer_ops.audit_footers(ox_doc)

    blacklist = [x.strip() for x in args.footer_blacklist.split(",") if x.strip()] if args.footer_blacklist else None
    standards = dr.Standards(footer_blacklist=blacklist)
    local_check = dr.check(doc, standards, para_range=para_range)
    d09 = [i for i in local_check.issues if i.rule == "D09"]
    local_doctor_issues = [i for i in local_check.issues if i.rule != "D09"]

    by_author: dict[str, dict] = {}
    for item in tc_items:
        author = item.get("author") or ""
        by_author.setdefault(author, {"ins": 0, "del": 0})
        by_author[author][item["type"]] += 1
    comment_by_author: dict[str, int] = {}
    for c in comments:
        author = c.get("author") or ""
        comment_by_author[author] = comment_by_author.get(author, 0) + 1

    payload = {
        "range": [lo, hi] if para_range is not None else None,
        "tc_total": len(tc_items),
        "tc_ins": sum(1 for x in tc_items if x["type"] == "ins"),
        "tc_del": sum(1 for x in tc_items if x["type"] == "del"),
        "comments_total": len(comments),
        "footer_parts": len(footers),
        "footer_non_empty": sum(1 for x in footers if x["text"].strip()),
        "footer_has_textbox": sum(1 for x in footers if x["has_textbox"]),
        "doctor_local_issue_count": len(local_doctor_issues),
        "doctor_local_issues": [
            {"rule": i.rule, "para": i.para, "detail": i.detail, "severity": i.severity}
            for i in local_doctor_issues[:50]
        ],
        "doctor_d09_warnings": [i.detail for i in d09],
        "tc_by_author": by_author,
        "comments_by_author": comment_by_author,
    }

    if args.fmt == "text":
        if payload["range"] is not None:
            print(f"Range: {payload['range'][0]}-{payload['range'][1]}")
        print(f"TC: total={payload['tc_total']} ins={payload['tc_ins']} del={payload['tc_del']}")
        print(f"Comments: total={payload['comments_total']}")
        print(f"Footers: total={payload['footer_parts']} non_empty={payload['footer_non_empty']} textbox={payload['footer_has_textbox']}")
        if payload["tc_by_author"]:
            print("TC by author:")
            for author, stats in payload["tc_by_author"].items():
                print(f"  {author or '?'}: ins={stats['ins']} del={stats['del']}")
        if payload["comments_by_author"]:
            print("Comments by author:")
            for author, count in payload["comments_by_author"].items():
                print(f"  {author or '?'}: {count}")
        if payload["doctor_d09_warnings"]:
            print("Footer warnings:")
            for msg in payload["doctor_d09_warnings"]:
                print(f"  - {msg}")
        if payload["doctor_local_issue_count"]:
            print(f"Local doctor issues: {payload['doctor_local_issue_count']}")
            for item in payload["doctor_local_issues"][:20]:
                para = f" P{item['para']}" if item["para"] is not None and item["para"] >= 0 else ""
                print(f"  - {item['rule']}{para} [{item['severity']}] {item['detail']}")
    else:
        _out(payload)


def cmd_review_inspect(args):
    """
    lex_docx review-inspect report.docx --range 100,150 [--author "JH"] [--fmt json|text] [--rich]
    """
    from lex_docx import tc_ops, doctor as dr, openxml_core as ox

    # ── Rich mode: 带格式标记的全文预览 ───────────────────────────────────── #
    if getattr(args, "rich", False):
        try:
            from lex_docx.review_detailed import rich_inspect, format_rich_output
            lo, hi = [int(x.strip()) for x in args.range.split(",", 1)]
            results = rich_inspect(args.docx, para_range=(lo, hi))
            if args.fmt == "text":
                print(f"Range: {lo}-{hi}")
                print(format_rich_output(results))
            else:
                _out({"range": [lo, hi], "paragraphs": results})
            return 0
        except Exception as e:
            print(f"error: rich inspect failed: {e}", file=sys.stderr)
            sys.exit(1)
    from lex_docx import tc_ops, doctor as dr, openxml_core as ox
    ox_doc = _load_doc(args.docx, backend="openxml")
    doc = _load_doc(args.docx, backend="python-docx")

    if not args.range:
        print("error: --range required", file=sys.stderr)
        sys.exit(1)

    lo, hi = [int(x.strip()) for x in args.range.split(",", 1)]
    para_range = (lo, hi + 1)
    preview_len = args.preview_len if getattr(args, "preview_len", None) is not None else 120
    para_preview_map = ox.paragraph_preview_map(ox_doc, max_len=preview_len if preview_len > 0 else 1000000)

    tc_items = tc_ops.list_tc(
        ox_doc,
        author_filter=args.author or None,
        para_range=para_range,
    )
    comments = tc_ops.list_comments(
        ox_doc,
        author_filter=args.author or None,
        para_range=para_range,
    )
    local_check = dr.check(doc, dr.Standards(), para_range=para_range)
    local_doctor_issues = [
        {"rule": i.rule, "para": i.para, "detail": i.detail, "severity": i.severity}
        for i in local_check.issues if i.rule != "D09"
    ]

    paras = doc.paragraphs
    para_slice = []
    for idx in range(lo, min(hi + 1, len(paras))):
        fallback_text = paras[idx].text
        if preview_len and preview_len > 0:
            fallback_text = fallback_text[:preview_len]
        para_slice.append({"index": idx, "text": para_preview_map.get(idx, fallback_text)})

    grouped: dict[int, dict] = {}
    for p in para_slice:
        grouped[p["index"]] = {
            "index": p["index"],
            "text": p["text"],
            "tc_items": [],
            "comments": [],
            "summary": {
                "tc_total": 0,
                "tc_ins": 0,
                "tc_del": 0,
                "comments_total": 0,
                "authors": [],
            },
        }
    for item in tc_items:
        para_idx = item.get("para")
        if para_idx in grouped:
            grouped[para_idx]["tc_items"].append(item)
            grouped[para_idx]["summary"]["tc_total"] += 1
            grouped[para_idx]["summary"][f"tc_{item['type']}"] += 1
            author = item.get("author") or ""
            if author not in grouped[para_idx]["summary"]["authors"]:
                grouped[para_idx]["summary"]["authors"].append(author)
    for c in comments:
        para_idx = c.get("para")
        if para_idx in grouped:
            grouped[para_idx]["comments"].append(c)
            grouped[para_idx]["summary"]["comments_total"] += 1
            author = c.get("author") or ""
            if author not in grouped[para_idx]["summary"]["authors"]:
                grouped[para_idx]["summary"]["authors"].append(author)

    next_actions = []
    if comments:
        next_actions.append({
            "why": "Inspect comments in this same range",
            "command": f"lex_docx review comments {args.docx} --range {lo},{hi} --fmt text" + (f" --author \"{args.author}\"" if args.author else ""),
        })
        next_actions.append({
            "why": "Preview cleaning comments in this same range",
            "command": f"lex_docx review clean-comments {args.docx} --range {lo},{hi} --dry-run" + (f" --author \"{args.author}\"" if args.author else ""),
        })
    if any(x["type"] == "ins" for x in tc_items):
        next_actions.append({
            "why": "Preview accepting insertions in this same range",
            "command": f"lex_docx tc-accept {args.docx} --type ins --range {lo},{hi} --dry-run" + (f" --author \"{args.author}\"" if args.author else ""),
        })
    if any(x["type"] == "del" for x in tc_items):
        next_actions.append({
            "why": "Preview rejecting deletions in this same range",
            "command": f"lex_docx tc-reject {args.docx} --type del --range {lo},{hi} --dry-run" + (f" --author \"{args.author}\"" if args.author else ""),
        })
    if local_doctor_issues:
        next_actions.append({
            "why": "Check structural/format issues in this same range",
            "command": f"lex_docx review stats {args.docx} --range {lo},{hi} --fmt text",
        })
        next_actions.append({
            "why": "Run focused doctor check with footer blacklist if needed",
            "command": f"lex_docx doctor check {args.docx} --range {lo},{hi}",
        })

    payload = {
        "range": [lo, hi],
        "paragraphs": para_slice,
        "tc_items": tc_items,
        "comments": comments,
        "doctor_local_issues": local_doctor_issues,
        "next_actions": next_actions,
        "summary": {
            "paragraphs_total": len(para_slice),
            "paragraphs_with_tc": sum(1 for g in grouped.values() if g["summary"]["tc_total"] > 0),
            "paragraphs_with_comments": sum(1 for g in grouped.values() if g["summary"]["comments_total"] > 0),
            "paragraphs_with_any_review": sum(1 for g in grouped.values() if g["summary"]["tc_total"] > 0 or g["summary"]["comments_total"] > 0),
            "tc_total": len(tc_items),
            "comments_total": len(comments),
            "doctor_local_issue_count": len(local_doctor_issues),
        },
        "grouped_by_paragraph": list(grouped.values()),
    }

    if args.fmt == "text":
        print(f"Range: {lo}-{hi}")
        s = payload["summary"]
        print(f"Summary: paragraphs={s['paragraphs_total']} with_tc={s['paragraphs_with_tc']} with_comments={s['paragraphs_with_comments']} with_any_review={s['paragraphs_with_any_review']} tc_total={s['tc_total']} comments_total={s['comments_total']} doctor_issues={s['doctor_local_issue_count']}")
        print("Paragraph groups:")
        for g in payload["grouped_by_paragraph"][:20]:
            gs = g["summary"]
            authors = ",".join(a for a in gs["authors"] if a) or "-"
            print(f"  P{g['index']}: {g['text']!r}")
            print(f"    summary: tc={gs['tc_total']} (ins={gs['tc_ins']} del={gs['tc_del']}) comments={gs['comments_total']} authors={authors}")
            if g["tc_items"]:
                for item in g["tc_items"][:10]:
                    print(f"    TC id={item['id']} {item['type']} author={item['author']!r} level={item['level']} text={item['text']!r}")
            if g["comments"]:
                for c in g["comments"][:10]:
                    quoted = f" quoted={c['quoted_text']!r}" if c.get("quoted_text") else ""
                    para_text = f" para_text={c['para_text']!r}" if c.get("para_text") else ""
                    print(f"    Comment id={c['id']} author={c['author']!r} text={c['text']!r}{quoted}{para_text}")
        print(f"TC items: {len(tc_items)}")
        print(f"Comments: {len(comments)}")
        if local_doctor_issues:
            print("Local doctor issues:")
            for item in local_doctor_issues[:20]:
                para = f" P{item['para']}" if item["para"] is not None and item["para"] >= 0 else ""
                print(f"  - {item['rule']}{para} [{item['severity']}] {item['detail']}")
        if next_actions:
            print("Suggested next actions:")
            for item in next_actions[:10]:
                print(f"  - {item['why']}")
                print(f"    {item['command']}")
    else:
        _out(payload)


def cmd_review_family(args):
    """review family alias: lex_docx review stats|inspect|comments|remove-comment|clean-comments ..."""
    if args.review_cmd == "stats":
        return cmd_review_stats(args)
    if args.review_cmd == "inspect":
        return cmd_review_inspect(args)
    if args.review_cmd == "comments":
        return cmd_comment_list(args)
    if args.review_cmd == "remove-comment":
        return cmd_comment_remove(args)
    if args.review_cmd == "clean-comments":
        return cmd_comment_clean(args)
    print("error: review subcommand required (stats|inspect|comments|remove-comment|clean-comments)", file=sys.stderr)
    sys.exit(1)


def cmd_clean(args):
    """
    lex_docx clean report.docx [--accept|--reject] [--author "JT"]
                [--keep-comments] [--keep-headers]
                [--dry-run] [--backup] [--yes] [--out out.docx]

    Orchestrated execution-version cleanup:
      1. Accept or reject all Track Changes  (default: accept)
      2. Remove all comments
      3. Clear all header content

    Use --dry-run to preview without modifying the file.
    Use --backup to create a .bak copy before modifying.
    Use --yes to skip interactive confirmation.
    """
    import shutil
    from lex_docx import tc_ops

    docx_path = args.docx
    out_path = args.out or docx_path

    tc_mode = "reject" if args.reject else "accept"
    author_filter = args.author or None
    do_comments = not args.keep_comments
    do_headers = not args.keep_headers

    # ── Dry-run: report and exit ──────────────────────────────────────── #
    if args.dry_run:
        doc = _load_doc(docx_path)
        tc_items = tc_ops.list_tc(doc, author_filter=author_filter)
        comments = tc_ops.list_comments(doc, author_filter=author_filter)
        hdr_count = sum(
            1 for rel in doc.part.rels.values()
            if tc_ops._HEADERS_REL in rel.reltype
        )
        plan = {
            "dry_run": True,
            "tc_mode": tc_mode,
            "author_filter": author_filter,
            "tc_changes_found": len(tc_items),
            "tc_changes_preview": tc_items[:20],
            "comments_found": len(comments),
            "comments_preview": comments[:20],
            "header_parts_found": hdr_count,
            "do_comments": do_comments,
            "do_headers": do_headers,
        }
        _out(plan)
        return

    # ── Interactive confirmation ──────────────────────────────────────── #
    if not args.yes:
        actions = [f"  • {tc_mode.upper()} all Track Changes"]
        if do_comments:
            actions.append("  • Remove all comments")
        if do_headers:
            actions.append("  • Clear all headers")
        print(f"Document: {docx_path}", file=sys.stderr)
        print("Actions to perform:", file=sys.stderr)
        for a in actions:
            print(a, file=sys.stderr)
        print(f"Output  : {out_path}", file=sys.stderr)
        answer = input("Proceed? [y/N] ").strip().lower()
        if answer not in ("y", "yes"):
            print("Aborted.", file=sys.stderr)
            sys.exit(0)

    # ── Backup ────────────────────────────────────────────────────────── #
    if args.backup:
        bak_path = docx_path + ".bak"
        shutil.copy2(docx_path, bak_path)
        print(f"backup  → {bak_path}", file=sys.stderr)

    # ── Execute ───────────────────────────────────────────────────────── #
    doc = _load_doc(docx_path)
    result: dict = {"ok": True, "tc_mode": tc_mode}

    if tc_mode == "accept":
        result["tc"] = tc_ops.accept_all(doc, author_filter=author_filter)
    else:
        result["tc"] = tc_ops.reject_all(doc, author_filter=author_filter)

    if do_comments:
        result["comments"] = tc_ops.clean_comments(doc)

    if do_headers:
        result["headers"] = tc_ops.clean_headers(doc)

    _save_doc(doc, out_path)
    _out(result)


# =========================================================================== #
# new document commands (P0)                                                   #
# =========================================================================== #

def cmd_create(args):
    """
    lex_docx create out.docx [--title "标题"] [--meta "案号:xxx\n日期:yyy"]
               [--font-song 宋体] [--font-roman TNR]
               [--font-size 11.0] [--line-spacing single]
    """
    from lex_docx import doc_create
    result = doc_create.create_document(
        output=args.output,
        title=args.title or "",
        meta=args.meta or "",
        font_song=args.font_song or "宋体",
        font_roman=args.font_roman or "Times New Roman",
        font_size=float(args.font_size) if args.font_size else 11.0,
        line_spacing=args.line_spacing or "single",
    )
    _out(result)


def cmd_scaffold(args):
    """
    lex_docx scaffold template.docx out.docx --mapping '{...}'
               [--preview] [--dry-run]
    """
    from lex_docx import doc_scaffold
    mapping = _load_json(args.mapping)

    if args.preview:
        result = doc_scaffold.scaffold_preview(args.template, mapping)
    else:
        result = doc_scaffold.scaffold_apply(
            args.template, args.output, mapping,
            dry_run=args.dry_run,
        )
    _out(result)


def cmd_new_table(args):
    """
    lex_docx new-table doc.docx --type grid --cols 3 --rows 5
               --header "列1" "列2" "列3"
               [--data '<json>'] [--at 5] [--after "关键字"]
               [--font-song 宋体] [--font-size 11.0]
    """
    from lex_docx import new_table_ops
    data = _load_json(args.data) if args.data else None
    merged_spec = _load_json(args.merged_spec) if args.merged_spec else None
    nested_spec = _load_json(args.nested_spec) if args.nested_spec else None
    result = new_table_ops.insert_table(
        docx_path=args.docx,
        at=args.at,
        after=args.after,
        type=args.type or "grid",
        cols=args.cols or 2,
        rows=args.rows or 3,
        headers=args.header,
        data=data,
        col_widths=args.col_widths,
        merged_spec=merged_spec,
        nested_spec=nested_spec,
        diagonal_labels=args.diagonal_labels,
        font=args.font_song or "宋体",
        font_size=float(args.font_size) if args.font_size else 11.0,
    )
    _out(result)


def cmd_stats(args):
    """
    lex_docx stats doc.docx [--fmt json|text]
    """
    from lex_docx import doc_stats
    result = doc_stats.doc_stats(args.docx)
    if args.fmt == "text":
        s = result
        print(f"paragraphs : {s['paragraphs']}")
        print(f"tables     : {s['tables']}")
        print(f"images     : {s['images']}")
        print(f"TC total   : {s['tc_total']} (ins={s['tc_ins']} del={s['tc_del']})")
        print(f"fonts      : {s['font_distribution']}")
        for t in s.get("table_summaries", []):
            print(f"  tbl[{t['table_index']}] rows={t['rows']}: {t['preview'][:80]}")
    else:
        _out(result)


def cmd_toc(args):
    """
    lex_docx toc doc.docx generate [--levels 1-3] [--position after-title]
    lex_docx toc doc.docx refresh
    """
    from lex_docx import toc_ops
    toc_cmd = getattr(args, "toc_cmd", None)
    if toc_cmd == "refresh":
        result = toc_ops.toc_refresh(args.docx, out=getattr(args, "out", None))
    else:
        level_from, level_to = 1, 3
        levels_str = getattr(args, "levels", "1-3") or "1-3"
        if levels_str:
            parts = levels_str.split("-")
            level_from = int(parts[0])
            level_to = int(parts[-1])
        result = toc_ops.toc_generate(
            args.docx,
            level_from=level_from,
            level_to=level_to,
            position=getattr(args, "position", "after-title") or "after-title",
            out=getattr(args, "out", None),
        )
    _out(result)



def cmd_structure(args):
    from lex_docx import iter_ops
    result = iter_ops.export_structure(args.docx, preview_len=args.preview_len, tree=args.tree, include_table_cells=args.include_table_cells, table_cell_preview_len=args.table_cell_preview_len)
    _out(result, args.fmt)
    return 0


def cmd_para_insert(args):
    from lex_docx import iter_ops
    result = iter_ops.insert_blank_paragraph(args.docx, para=args.para, position=args.position, count=args.count, out=args.out, dry_run=args.dry_run)
    _out(result, 'json')
    return 0


def cmd_page_break(args):
    from lex_docx import iter_ops
    result = iter_ops.insert_page_break(args.docx, para=args.para, out=args.out, dry_run=args.dry_run)
    _out(result, 'json')
    return 0


def cmd_section_break(args):
    from lex_docx import iter_ops
    result = iter_ops.insert_section_break(args.docx, para=args.para, out=args.out, dry_run=args.dry_run)
    _out(result, 'json')
    return 0


def cmd_page_setup(args):
    from lex_docx import iter_ops
    result = iter_ops.apply_page_setup(
        args.docx,
        scope=args.scope,
        section=args.section,
        section_start=args.section_start,
        section_end=args.section_end,
        paper=args.paper,
        orientation=args.orientation,
        margin_top_mm=args.margin_top_mm,
        margin_bottom_mm=args.margin_bottom_mm,
        margin_left_mm=args.margin_left_mm,
        margin_right_mm=args.margin_right_mm,
        out=args.out,
        dry_run=args.dry_run,
    )
    _out(result, 'json')
    return 0


# ── 基础编辑命令（insert / replace / delete） ──────────────────────────────── #

def cmd_insert(args):
    """
    lex_docx insert doc.docx --para 180 --text "新增文字"
               [--tc] [--author JT] [--bold] [--italic]
               [--font-song 宋体] [--font-size 11.0]
               [--out out.docx]
    """
    from lex_docx.edit_ops import insert_text
    result = insert_text(
        docx_path=args.docx,
        para=args.para,
        text=args.text,
        tc=args.tc,
        author=args.author or "agent",
        bold=args.bold,
        italic=args.italic,
        font=args.font_song or "宋体",
        font_size=float(args.font_size) if args.font_size else 11.0,
        output=args.out,
    )
    _out(result.__dict__)


def cmd_replace(args):
    """
    lex_docx replace doc.docx --para 180 --old "旧文" --new "新文"
               [--tc] [--author JT]
               [--out out.docx]
    """
    from lex_docx.edit_ops import replace_text
    result = replace_text(
        docx_path=args.docx,
        para=args.para,
        old=args.old,
        new=args.new,
        tc=args.tc,
        author=args.author or "agent",
        bold=args.bold,
        italic=args.italic,
        font=args.font_song or "宋体",
        font_size=float(args.font_size) if args.font_size else 11.0,
        output=args.out,
    )
    _out(result.__dict__)


def cmd_delete(args):
    """
    lex_docx delete doc.docx --para 180
               [--text "指定文字"] [--tc] [--author JT]
               [--out out.docx]
    """
    from lex_docx.edit_ops import delete_text
    result = delete_text(
        docx_path=args.docx,
        para=args.para,
        text=args.text,
        tc=args.tc,
        author=args.author or "agent",
        output=args.out,
    )
    _out(result.__dict__)


# =========================================================================== #
# main                                                                         #
# =========================================================================== #

def main():
    parser = argparse.ArgumentParser(
        prog="lex_docx",
        usage="lex_docx <command> [options]  (lex_docx --help 查看全部命令)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description="""\
lex_docx — DOCX 自动化工具库 CLI

Commands:
  ── 检查 / 诊断 ──────────────────────────────────────────────────────────────
  lint                检查 DOCX 内容格式（拼写/表格/标注，支持 Profile+Selector）
  doctor check        格式结构诊断（字体/大纲/编号/样式引用/TOC/footer，只读）
  doctor fix          自动修复诊断结果（支持 --dry-run / --backup）
  review-stats        统计 review 文件中的 TC / comments / footer 风险（legacy flat command）

  ── 数据填充 ────────────────────────────────────────────────────────────────
  extract             提取表格数据（输出 JSON 或 CSV）
  fill-table          按列映射批量填充表格行
  fill-kv             填充 KV 表（基本信息类二列/四列布局）
  fill-footer         批量替换所有 footer parts 中的文本（legacy flat command）

  ── 表格操作 ────────────────────────────────────────────────────────────────
  format-table        统一表格格式（底色/边框/列宽/对齐）
  copy-table          跨文档表格复制（含完整格式）
  table-inspect       读取表格完整格式信息（底色/边框/列宽/字体/风格检测）
  table-replace       在指定表格单元格内做细颗粒度 TC 替换
  table-format-brush  表格格式刷（从参考表格复制格式到目标表格）

  ── 段落 / Track Changes ────────────────────────────────────────────────────
  tc-insert           段落级 TC INS（在指定段落插入带标记文字）
  tc-replace          段内细颗粒度 TC 替换（只替换命中的旧文本）
  tc-delete           段落级 TC DEL（将指定段落标记为删除）
  tc-list             列出文档所有 Track Changes（支持按作者/范围/类型过滤）
  tc-accept           接受 Track Changes（支持按作者/范围/类型过滤）
  tc-reject           拒绝 Track Changes（支持按作者/范围/类型过滤）
  comment-list        列出文档 comments（可按作者/范围）
  comment-remove      按 id 删除指定 comments
  review-inspect      检查某段范围内的 paragraphs / TC / comments（legacy flat command）
  highlight           批量高亮段落范围
  format-brush        段落格式刷（复制缩进/间距/样式/大纲级别等）
  set-outline-level   设置段落大纲级别（w:outlineLvl，独立于 Heading 样式）
  para-runs           输出指定段落的 run-level 文本与格式信息
  para-query          全文格式检索（按字体/样式/大纲级别/字号/粗斜体/对齐等过滤）

  ── 文档维护 ────────────────────────────────────────────────────────────────
  cleanup             清理空段落 / 孤儿编号
  bold-terms          加粗定义术语首次出现位置
  comment-clean       删除所有批注（commentRange + 引用 run + comments.xml）
  header-clean        清除所有 header 内容（可选：移除 headerReference 引用）
  footer-audit        审查所有 footer OPC parts（legacy flat command）
  numbering inspect   检查段落编号状态（own/effective numPr）
  numbering restart   仅重置编号计数，不调整缩进/样式等其他格式
  section-restart-numbering  按章节范围重置子级编号计数
  structure           导出文档结构（段落/表格/编号/样式）
  para-insert         在指定段落前后插入空白段落
  page-break          在指定段落后插入分页符
  section-break       在指定段落后插入分节符（下一页）
  page-setup          设置页面格式（纸张/方向/边距）
  inject              读取 JSON 计划文件一键批量注入
  clean               执行版一键清理（accept/reject TC + 批注 + header）

  ── 文档生成 ──────────────────────────────────────────────────────────────────
  create              从零创建标准 OPC 骨架 .docx（全程无 python-docx）
  scaffold            模板克隆 + 主体替换（含 mapping 预览 + 残留扫描）
  new-table           在文档中插入新表格（grid / kv / merged / nested / diagonal）
  toc                 目录生成/刷新（基于 Heading1-3）
  stats               快速文档摘要（段落数/表格数/字体分布/TCC 数）

  ── 基础编辑 ──────────────────────────────────────────────────────────────────
  insert              在段落末尾插入文本（--tc 启用 Track Changes）
  replace             替换段内文字（--tc 启用 Track Changes）
  delete              删除段内文字（--tc 启用 Track Changes）

  ── Canonical family aliases ───────────────────────────────────────────────
  footer audit        审查所有 footer OPC parts（推荐入口）
  footer fill         批量替换所有 footer parts 中的文本（推荐入口）
  review stats        统计 review 文件中的 TC / comments / footer 风险（推荐入口）
  review inspect      检查某段范围内的 paragraphs / TC / comments（推荐入口）
  review comments     列出文档 comments（推荐入口）
  review remove-comment  按 id 删除指定 comments（推荐入口）
  review clean-comments  删除 comments（支持全量或按作者/范围，推荐入口）

每个子命令均支持 -h / --help 查看详细参数。
""",
    )
    sub = parser.add_subparsers(dest="command", required=True,
                                metavar="<command>",
                                help=argparse.SUPPRESS)

    # ── lint ──────────────────────────────────────────────────────────────── #
    p = sub.add_parser("lint", help="检查 DOCX 内容格式（支持 Profile+Selector 模式）")
    p.add_argument("docx")
    p.add_argument("--cfg", help="DocConfig JSON 文件路径（经典模式）")
    p.add_argument("--rules", help="逗号分隔的规则名，默认全部")
    p.add_argument("--fmt", choices=["text", "json"], default="text")
    p.add_argument("--lint-cfg", dest="lint_cfg",
                   help="Lint Config JSON 路径（Profile + Selector 模式）")
    p.add_argument("--profile", help="指定 profile 名；不指定则按 selectors 自动匹配")

    # ── extract ───────────────────────────────────────────────────────────── #
    p = sub.add_parser("extract", help="提取表格数据")
    p.add_argument("docx")
    p.add_argument("--table", type=int, help="表格索引")
    p.add_argument("--near", help="临近文字定位")
    p.add_argument("--fmt", choices=["json", "csv"], default="json")

    # ── fill-table ────────────────────────────────────────────────────────── #
    p = sub.add_parser("fill-table", help="按列映射填充表格")
    p.add_argument("docx")
    p.add_argument("--table", type=int, required=True)
    p.add_argument("--data", required=True, help="List[Dict] JSON 文件")
    p.add_argument("--map", help="列映射 Dict JSON 文件")
    p.add_argument("--auto-del", action="store_true", help="自动 TC DEL 多余行")
    p.add_argument("--cfg")
    p.add_argument("--out", help="输出路径，默认覆盖原文件")

    # ── fill-kv ───────────────────────────────────────────────────────────── #
    p = sub.add_parser("fill-kv", help="填充 KV 表")
    p.add_argument("docx")
    p.add_argument("--table", type=int, required=True)
    p.add_argument("--data", required=True, help="Dict JSON 文件")
    p.add_argument("--key-cols", help="多列 key 索引，如 '0,2'（四列布局）")
    p.add_argument("--cfg")
    p.add_argument("--out")

    # ── format-table ──────────────────────────────────────────────────────── #
    p = sub.add_parser("format-table", help="统一表格格式")
    p.add_argument("docx")
    p.add_argument("--table", type=int, required=True)
    p.add_argument("--shading", help="标题行底色十六进制，如 D9E2F3")
    p.add_argument("--borders", choices=["single", "none"])
    p.add_argument("--widths", help="列宽 dxa，逗号分隔，如 '800,4000,2000'")
    p.add_argument("--align", help="列对齐，逗号分隔，如 'center,left,right'")
    p.add_argument("--cfg")
    p.add_argument("--out")

    # ── cleanup ───────────────────────────────────────────────────────────── #
    p = sub.add_parser("cleanup", help="清理空段落 / 孤儿编号")
    p.add_argument("docx")
    p.add_argument("--range", help="段落范围，如 '0,200'")
    p.add_argument("--mode", choices=["report", "tc-del", "delete"],
                   default="tc-del",
                   help="report=只报告 | tc-del=TC DEL标记（默认）| delete=直接删除")
    p.add_argument("--keep-styles", help="保留这些 style 的空段落，逗号分隔")
    p.add_argument("--cfg")
    p.add_argument("--out")

    # ── bold-terms ────────────────────────────────────────────────────────── #
    p = sub.add_parser("bold-terms", help="加粗定义术语")
    p.add_argument("docx")
    p.add_argument("--para", type=int, help="目标段落索引")
    p.add_argument("--scan", action="store_true", help="扫描全文，只查不改")
    p.add_argument("--range", help="scan 范围，如 '0,100'")
    p.add_argument("--out")

    # ── copy-table ────────────────────────────────────────────────────────── #
    p = sub.add_parser("copy-table", help="跨文档表格复制（含格式）")
    p.add_argument("src_docx", help="源文档路径")
    p.add_argument("dst_docx", help="目标文档路径")
    p.add_argument("--dst-pos", required=True,
                   help="插入位置：after_para:N 或 replace_table:N")
    p.add_argument("--src-table", type=int, help="源表格序号（0-based）")
    p.add_argument("--src-near", help="按临近文字定位源表格")
    p.add_argument("--cols", help="保留列号，逗号分隔，如 '0,1,2,3'")
    p.add_argument("--max-rows", type=int, help="最多保留数据行数")
    p.add_argument("--rename", help='重命名表头 JSON，如 \'{"变更时间":"日期"}\'')
    p.add_argument("--cfg")
    p.add_argument("--out", help="输出路径，默认覆盖 dst_docx")

    # ── table-inspect ─────────────────────────────────────────────────────── #
    p = sub.add_parser("table-inspect", help="读取表格完整格式信息（底色/边框/列宽/字体）")
    p.add_argument("docx")
    p.add_argument("--table", type=int, help="表格序号（0-based）")
    p.add_argument("--near",  help="按临近文字定位表格")
    p.add_argument("--preview-len", dest="preview_len", type=int, default=80,
                   help="样例行单元格文本预览长度；0 表示不截断")
    p.add_argument("--preview-rows", dest="preview_rows", type=int, default=3,
                   help="输出多少行样例，默认 3")
    p.add_argument("--fmt", choices=["json", "text"], default="json")
    p.add_argument("--tree", action="store_true", help="输出简化层级树")

    # ── table-replace ─────────────────────────────────────────────────────── #
    p = sub.add_parser("table-replace", help="在指定表格单元格内做细颗粒度 TC 替换")
    p.add_argument("docx")
    p.add_argument("--table", type=int, required=True)
    p.add_argument("--row", type=int, required=True)
    p.add_argument("--col", type=int, required=True)
    p.add_argument("--para", type=int, default=0, help="单元格内段落索引，默认 0")
    p.add_argument("--old", required=True)
    p.add_argument("--new", dest="new", required=True)
    p.add_argument("--occurrence", type=int, default=1, help="替换第几次命中，默认 1")
    p.add_argument("--after-text", dest="after_text", help="只在此锚点之后搜索 old 文本")
    p.add_argument("--before-text", dest="before_text", help="要求命中后方仍能看到此锚点")
    p.add_argument("--replace-all", dest="replace_all", action="store_true", help="替换该单元格内所有命中")
    p.add_argument("--cfg")
    p.add_argument("--out")

    # ── table-format-brush ────────────────────────────────────────────────── #
    p = sub.add_parser("table-format-brush", help="表格格式刷（从参考表格复制格式到目标表格）")
    p.add_argument("docx", help="参考文档（单文档模式时也是目标文档）")
    p.add_argument("target_docx", nargs="?", help="目标文档（跨文档模式时指定）")
    p.add_argument("--ref-table",    dest="ref_table",    type=int, required=True,
                   help="参考表格序号")
    p.add_argument("--target-table", dest="target_table", type=int, required=True,
                   help="目标表格序号")
    p.add_argument("--copy", help="复制项，逗号分隔：shading,borders,col_widths,col_aligns,font,row_height")
    p.add_argument("--out")

    # ── tc-insert ─────────────────────────────────────────────────────────── #
    p = sub.add_parser("tc-insert", help="段落级 TC INS（在指定段落插入文字）")
    p.add_argument("docx")
    p.add_argument("--para", type=int, required=True, help="目标段落索引")
    p.add_argument("--text", required=False, default="", help="插入的文字内容")
    p.add_argument("--text-markup", dest="text_markup", default="", help="轻量标记文本（**粗体** *斜体* __下划线__ ==高亮==）")
    p.add_argument("--pos", default="end", help="插入位置：end（默认）| start | N（整数）")
    p.add_argument("--bold", action="store_true")
    p.add_argument("--italic", action="store_true")
    p.add_argument("--highlight", help="高亮颜色，如 yellow")
    p.add_argument("--inherit-rpr", dest="inherit_rpr", default="true",
                   choices=["true", "style", "auto"],
                   help="rPr 继承策略：true=继承首 run（默认）| style=跟 pStyle | auto=按 style_rPr_map")
    p.add_argument("--cfg")
    p.add_argument("--out")

    # ── tc-replace ────────────────────────────────────────────────────────── #
    p = sub.add_parser("tc-replace", help="段内细颗粒度 TC 替换（只替换命中的旧文本）")
    p.add_argument("docx")
    p.add_argument("--para", type=int, required=True, help="目标段落索引")
    p.add_argument("--old", required=True, help="段内要替换的旧文本")
    p.add_argument("--new", dest="new", required=True, help="替换后的新文本")
    p.add_argument("--occurrence", type=int, default=1, help="替换第几次命中，默认 1")
    p.add_argument("--after-text", dest="after_text", help="只在此锚点之后搜索 old 文本")
    p.add_argument("--before-text", dest="before_text", help="要求命中后方仍能看到此锚点")
    p.add_argument("--replace-all", dest="replace_all", action="store_true", help="替换该单元格内所有命中")
    p.add_argument("--cfg")
    p.add_argument("--out")

    # ── tc-delete ─────────────────────────────────────────────────────────── #
    p = sub.add_parser("tc-delete", help="段落级 TC DEL（将指定段落标记为删除）")
    p.add_argument("docx")
    p.add_argument("--para", type=int, help="单个段落索引")
    p.add_argument("--range", help="段落范围（含两端），如 '180,195'")
    p.add_argument("--cfg")
    p.add_argument("--out")

    # ── highlight ─────────────────────────────────────────────────────────── #
    p = sub.add_parser("highlight", help="批量标黄段落范围")
    p.add_argument("docx")
    p.add_argument("--para", type=int, help="单个段落索引")
    p.add_argument("--range", help="段落范围（含两端），如 '180,195'")
    p.add_argument("--color", default="yellow", help="高亮颜色（默认 yellow）")
    p.add_argument("--out")

    # ── format-brush ──────────────────────────────────────────────────────── #
    p = sub.add_parser("format-brush", help="格式刷（从参考段落复制格式到目标段落）")
    p.add_argument("docx")
    p.add_argument("--ref", type=int, required=True, help="参考段落索引")
    p.add_argument("--target", help="目标段落索引，逗号分隔，如 '177,178,180'")
    p.add_argument("--range", help="目标段落范围（含两端），如 '175,185'")
    p.add_argument("--copy", help="复制项，逗号分隔，如 'indent,spacing,style'；也支持 font,font-size,run-props")
    p.add_argument("--skip-if-jc", dest="skip_if_jc",
                   help="跳过当前对齐方式等于此值的段落，如 'center'")
    p.add_argument("--safe", action="store_true",
                   help="安全模式：仅复制低风险段落级属性，并在保存前做 zip 完整性校验")
    p.add_argument("--validate-each", dest="validate_each", action="store_true",
                   help="逐段做内存 zip 校验；失败时报告触发的 target 段落索引")
    p.add_argument("--backend", choices=["legacy", "ooxml"], default="ooxml",
                   help="选择后端引擎，默认 ooxml")
    p.add_argument("--out")

    # ── set-outline-level ─────────────────────────────────────────────────── #
    p = sub.add_parser("set-outline-level",
                       help="设置段落大纲级别（w:outlineLvl，独立于 Heading 样式）")
    p.add_argument("docx")
    p.add_argument("--level", required=True,
                   help="大纲级别 1-9，或 none/0 表示清除（变为正文）")
    p.add_argument("--para",  type=int, help="单个段落索引")
    p.add_argument("--range", help="段落范围（含两端），如 '10,20'")
    p.add_argument("--style", help="按样式名过滤，如 '自定义标题'")
    p.add_argument("--out",   help="输出路径，默认覆盖原文件")

    p.add_argument("--backend", choices=["legacy", "ooxml"], default="ooxml",
                   help="选择后端引擎，默认 ooxml")

    # ── para-runs ─────────────────────────────────────────────────────────── #
    p = sub.add_parser("para-runs", help="输出指定段落的 run-level 文本与格式信息")
    p.add_argument("docx")
    p.add_argument("--para", type=int, required=True)
    p.add_argument("--with-markup", dest="with_markup", action="store_true", help="同时输出可逆轻量 markup")
    p.add_argument("--fmt", choices=["json", "text"], default="json")

    # ── para-query ────────────────────────────────────────────────────────── #
    p = sub.add_parser("para-query", help="全文格式检索（按字体/样式/大纲级别/字号/粗斜体/对齐等过滤）")
    p.add_argument("docx")
    p.add_argument("--style", nargs="+")
    p.add_argument("--font")
    p.add_argument("--font-size", dest="font_size")
    p.add_argument("--outline-level", dest="outline_level")
    p.add_argument("--bold", action="store_true")
    p.add_argument("--no-bold", dest="no_bold", action="store_true")
    p.add_argument("--italic", action="store_true")
    p.add_argument("--no-italic", dest="no_italic", action="store_true")
    p.add_argument("--color")
    p.add_argument("--jc")
    p.add_argument("--range")
    p.add_argument("--preview-len", dest="preview_len", type=int, default=80)
    p.add_argument("--fmt", choices=["json", "text"], default="json")

    # ── structure / layout iteration ─────────────────────────────────────── #
    p = sub.add_parser("structure", help="导出文档结构（段落/表格/编号/样式）")
    p.add_argument("docx")
    p.add_argument("--preview-len", dest="preview_len", type=int, default=120)
    p.add_argument("--fmt", choices=["json", "text"], default="json")
    p.add_argument("--tree", action="store_true", help="输出简化层级树")
    p.add_argument("--include-table-cells", dest="include_table_cells", action="store_true", help="输出表格单元格路径与预览")
    p.add_argument("--table-cell-preview-len", dest="table_cell_preview_len", type=int, default=60)

    p = sub.add_parser("para-insert", help="在指定段落前后插入空白段落")
    p.add_argument("docx")
    p.add_argument("--para", type=int, required=True)
    p.add_argument("--position", choices=["before", "after"], default="after")
    p.add_argument("--count", type=int, default=1)
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--out")

    p = sub.add_parser("page-break", help="在指定段落后插入分页符")
    p.add_argument("docx")
    p.add_argument("--para", type=int, required=True)
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--out")

    p = sub.add_parser("section-break", help="在指定段落后插入分节符（下一页）")
    p.add_argument("docx")
    p.add_argument("--para", type=int, required=True)
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--out")

    p = sub.add_parser("page-setup", help="设置页面格式（纸张/方向/边距）")
    p.add_argument("docx")
    p.add_argument("--scope", choices=["document", "section", "section-range"], default="document")
    p.add_argument("--section", type=int, help="当 scope=section 时指定 section 索引")
    p.add_argument("--section-start", dest="section_start", type=int, help="当 scope=section-range 时指定起始 section")
    p.add_argument("--section-end", dest="section_end", type=int, help="当 scope=section-range 时指定结束 section")
    p.add_argument("--paper", default="A4", choices=["A4", "A3", "LETTER"])
    p.add_argument("--orientation", choices=["portrait", "landscape"], default="portrait")
    p.add_argument("--margin-top-mm", dest="margin_top_mm", type=float)
    p.add_argument("--margin-bottom-mm", dest="margin_bottom_mm", type=float)
    p.add_argument("--margin-left-mm", dest="margin_left_mm", type=float)
    p.add_argument("--margin-right-mm", dest="margin_right_mm", type=float)
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--out")

    # ── doctor family ─────────────────────────────────────────────────────── #
    p = sub.add_parser("doctor", help="格式结构诊断与修复（check/fix）")
    doctor_sub = p.add_subparsers(dest="doctor_cmd", required=True)
    for name in ("check", "fix"):
        sp = doctor_sub.add_parser(name, help=f"doctor {name}")
        sp.add_argument("docx")
        sp.add_argument("--font")
        sp.add_argument("--ascii-font", dest="ascii_font")
        sp.add_argument("--font-size", dest="font_size")
        sp.add_argument("--toc-levels", dest="toc_levels", help="目录级别范围，如 1-3")
        sp.add_argument("--footer-blacklist", dest="footer_blacklist")
        sp.add_argument("--rules", help="逗号分隔规则，如 D01,D02")
        sp.add_argument("--range", help="段落范围，如 0,200")
        if name == "fix":
            sp.add_argument("--exclude-range", dest="exclude_range")
            sp.add_argument("--dry-run", action="store_true")
            sp.add_argument("--backup", action="store_true")
            sp.add_argument("--out")
        sp.set_defaults(func=cmd_doctor)

    # ── inject ────────────────────────────────────────────────────────────── #
    p = sub.add_parser("inject", help="读取 JSON 计划文件一键批量注入")
    p.add_argument("plan")
    p.add_argument("--cfg")
    p.add_argument("--out")

    # ── numbering family ──────────────────────────────────────────────────── #
    p = sub.add_parser("numbering", help="编号检查与重置（inspect/restart）")
    num_sub = p.add_subparsers(dest="numbering_cmd", required=True)
    sp = num_sub.add_parser("inspect", help="检查段落编号状态")
    sp.add_argument("docx")
    sp.add_argument("--range")
    sp.add_argument("--style", nargs="+")
    sp.add_argument("--outline-level", dest="outline_level")
    sp.add_argument("--preview-len", dest="preview_len", type=int, default=80)
    sp.add_argument("--fmt", choices=["json", "text"], default="json")
    sp.add_argument("--backend", choices=["legacy", "ooxml"], default="ooxml")
    sp.set_defaults(func=cmd_numbering_family)

    sp = num_sub.add_parser("restart", help="重置编号计数")
    sp.add_argument("docx")
    sp.add_argument("--start", type=int, required=True)
    sp.add_argument("--style", nargs="+")
    sp.add_argument("--outline-level", dest="outline_level")
    sp.add_argument("--start-at", dest="start_at", type=int, default=1)
    sp.add_argument("--multilevel-link", dest="multilevel_link", action="store_true")
    sp.add_argument("--levels", help="联动级别，逗号分隔")
    sp.add_argument("--dry-run", action="store_true")
    sp.add_argument("--backend", choices=["legacy", "ooxml"], default="ooxml")
    sp.add_argument("--out")
    sp.set_defaults(func=cmd_numbering_family)

    # ── section-restart-numbering ─────────────────────────────────────────── #
    p = sub.add_parser("section-restart-numbering", help="按章节范围重置子级编号计数")
    p.add_argument("docx")
    p.add_argument("--heading", type=int, required=True)
    p.add_argument("--style", nargs="+")
    p.add_argument("--outline-level", dest="outline_level")
    p.add_argument("--start-at", dest="start_at", type=int, default=1)
    p.add_argument("--multilevel-link", dest="multilevel_link", action="store_true")
    p.add_argument("--levels")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--backend", choices=["legacy", "ooxml"], default="ooxml")
    p.add_argument("--out")

    # ── footer family ─────────────────────────────────────────────────────── #
    p = sub.add_parser("footer", help="页脚审查与替换（audit/fill）")
    footer_sub = p.add_subparsers(dest="footer_cmd", required=True)
    sp = footer_sub.add_parser("audit", help="审查所有 footer OPC parts")
    sp.add_argument("docx")
    sp.add_argument("--preview-len", dest="preview_len", type=int, default=80)
    sp.add_argument("--fmt", choices=["json", "text"], default="json")
    sp.set_defaults(func=cmd_footer_family)
    sp = footer_sub.add_parser("fill", help="批量替换所有 footer parts 中的文本")
    sp.add_argument("docx")
    sp.add_argument("--replace", required=True)
    sp.add_argument("--with", dest="with_text", required=True)
    sp.add_argument("--out")
    sp.set_defaults(func=cmd_footer_family)

    # ── create ──────────────────────────────────────────────────────────────── #
    p = sub.add_parser("create", help="从零创建标准 OPC 骨架 .docx（全程无 python-docx）")
    p.add_argument("output", help="输出文件路径")
    p.add_argument("--title", dest="title")
    p.add_argument("--meta", dest="meta",
                   help="元数据行，多行用 \\\\n 分隔，如 '案号:xxx\\\\n日期:yyy'")
    p.add_argument("--font-song", dest="font_song", default="宋体",
                   help="中文字体（默认 宋体）")
    p.add_argument("--font-roman", dest="font_roman", default="Times New Roman",
                   help="西文字体（默认 Times New Roman）")
    p.add_argument("--font-size", dest="font_size",
                   help="正文字号 pt（默认 11.0）")
    p.add_argument("--line-spacing", dest="line_spacing",
                   choices=["single", "1.5", "double"], default="single",
                   help="行距类型（默认 single）")
    p.set_defaults(func=cmd_create)

    # ── scaffold ────────────────────────────────────────────────────────────── #
    p = sub.add_parser("scaffold", help="模板克隆 + 主体替换（含 mapping 预览 + 残留扫描）")
    p.add_argument("template", help="模板文件路径")
    p.add_argument("output", help="输出文件路径")
    p.add_argument("--mapping", dest="mapping", required=True,
                   help="mapping JSON 文件路径")
    p.add_argument("--preview", action="store_true",
                   help="只预览 mapping 命中，不写入")
    p.add_argument("--dry-run", dest="dry_run", action="store_true")
    p.set_defaults(func=cmd_scaffold)

    # ── new-table ────────────────────────────────────────────────────────────── #
    p = sub.add_parser("new-table", help="在文档中插入新表格（grid / kv / merged / nested / diagonal）")
    p.add_argument("docx", help="目标文档路径")
    p.add_argument("--type", dest="type",
                   choices=["grid", "kv", "merged", "nested", "diagonal"],
                   default="grid", help="表格类型（默认 grid）")
    p.add_argument("--cols", dest="cols", type=int, default=2,
                   help="列数（默认 2）")
    p.add_argument("--rows", dest="rows", type=int, default=3,
                   help="数据行数（不含表头，默认 3）")
    p.add_argument("--header", dest="header", nargs="+",
                   help="表头文本，如：--header '列1' '列2' '列3'")
    p.add_argument("--data", dest="data",
                   help="数据 JSON 文件路径")
    p.add_argument("--at", dest="at", type=int,
                   help="插入到指定段落索引之后")
    p.add_argument("--after", dest="after",
                   help="插入到第一个包含此文字的段落之后")
    p.add_argument("--col-widths", dest="col_widths", nargs="+",
                   help="每列宽度 dxa（1/20pt），如：--col-widths 2000 4000")
    p.add_argument("--font-song", dest="font_song", default="宋体")
    p.add_argument("--font-size", dest="font_size",
                   help="字号 pt（默认 11.0）")
    # merged / nested / diagonal 专用参数
    p.add_argument("--merged-spec", dest="merged_spec",
                   help="合并单元格规格 JSON 文件。"
                        "格式：[{\"row\":r,\"col\":c,\"rowspan\":n,\"colspan\":m},...]。"
                        "row/col 从 0 起，表头行=0。"
                        "例如 --merged-spec spec.json")
    p.add_argument("--nested-spec", dest="nested_spec",
                   help="嵌套子表规格 JSON 文件。"
                        "格式：[{\"row\":r,\"col\":c,\"nested_data\":[[...]]},...]")
    p.add_argument("--diagonal-labels", dest="diagonal_labels", nargs="+",
                   help="斜线表头左下角标签（与 --header 对应），"
                        "如：--diagonal-labels '项目' '分类'")
    p.set_defaults(func=cmd_new_table)

    # ── toc ─────────────────────────────────────────────────────────────────── #
    p = sub.add_parser("toc", help="目录生成/刷新（基于 Heading1-3）")
    toc_sub = p.add_subparsers(dest="toc_cmd", required=True)
    sp = toc_sub.add_parser("generate", help="生成 TOC 域")
    sp.add_argument("docx")
    sp.add_argument("--levels", dest="levels", default="1-3",
                    help="级别范围，如 1-3（默认 1-3）")
    sp.add_argument("--position", dest="position",
                    choices=["after-title", "end-of-body"],
                    default="after-title",
                    help="插入位置（默认 after-title）")
    sp.add_argument("--out")
    sp.set_defaults(func=cmd_toc)
    sp = toc_sub.add_parser("refresh", help="刷新已有 TOC 域")
    sp.add_argument("docx")
    sp.add_argument("--out")
    sp.set_defaults(func=cmd_toc)

    # ── stats ───────────────────────────────────────────────────────────────── #
    p = sub.add_parser("stats", help="快速文档摘要（段落数/表格数/字体分布/TCC 数）")
    p.add_argument("docx")
    p.add_argument("--fmt", choices=["json", "text"], default="json")

    # ── insert ──────────────────────────────────────────────────────────────── #
    p = sub.add_parser("insert", help="在段落末尾插入文本（--tc 启用 Track Changes）")
    p.add_argument("docx", help="目标文档路径")
    p.add_argument("--para", dest="para", type=int, required=True, help="段落索引（从 0 起）")
    p.add_argument("--text", dest="text", required=True, help="要插入的文本")
    p.add_argument("--tc", dest="tc", action="store_true", help="启用 Track Changes 模式")
    p.add_argument("--author", dest="author", default="agent", help="修订作者名称")
    p.add_argument("--bold", dest="bold", action="store_true")
    p.add_argument("--italic", dest="italic", action="store_true")
    p.add_argument("--font-song", dest="font_song", default="宋体")
    p.add_argument("--font-size", dest="font_size")
    p.add_argument("--out", dest="out", help="输出路径（默认就地覆盖）")
    p.set_defaults(func=cmd_insert)

    # ── replace ─────────────────────────────────────────────────────────────── #
    p = sub.add_parser("replace", help="替换段内文字（--tc 启用 Track Changes）")
    p.add_argument("docx", help="目标文档路径")
    p.add_argument("--para", dest="para", type=int, required=True, help="段落索引（从 0 起）")
    p.add_argument("--old", dest="old", required=True, help="旧文本")
    p.add_argument("--new", dest="new", required=True, help="新文本")
    p.add_argument("--tc", dest="tc", action="store_true", help="启用 Track Changes 模式")
    p.add_argument("--author", dest="author", default="agent", help="修订作者名称")
    p.add_argument("--bold", dest="bold", action="store_true")
    p.add_argument("--italic", dest="italic", action="store_true")
    p.add_argument("--font-song", dest="font_song", default="宋体")
    p.add_argument("--font-size", dest="font_size")
    p.add_argument("--out", dest="out", help="输出路径（默认就地覆盖）")
    p.set_defaults(func=cmd_replace)

    # ── delete ──────────────────────────────────────────────────────────────── #
    p = sub.add_parser("delete", help="删除段内文字（--tc 启用 Track Changes）")
    p.add_argument("docx", help="目标文档路径")
    p.add_argument("--para", dest="para", type=int, required=True, help="段落索引（从 0 起）")
    p.add_argument("--text", dest="text", help="要删除的文字（不指定则删除整段文本）")
    p.add_argument("--tc", dest="tc", action="store_true", help="启用 Track Changes 模式")
    p.add_argument("--author", dest="author", default="agent", help="修订作者名称")
    p.add_argument("--out", dest="out", help="输出路径（默认就地覆盖）")
    p.set_defaults(func=cmd_delete)

    # ── legacy flat aliases ───────────────────────────────────────────────── #
    p = sub.add_parser("footer-audit", help="审查所有 footer OPC parts（legacy flat command）")
    p.add_argument("docx")
    p.add_argument("--preview-len", dest="preview_len", type=int, default=80)
    p.add_argument("--fmt", choices=["json", "text"], default="json")

    p = sub.add_parser("fill-footer", help="批量替换所有 footer parts 中的文本（legacy flat command）")
    p.add_argument("docx")
    p.add_argument("--replace", required=True)
    p.add_argument("--with", dest="with_text", required=True)
    p.add_argument("--out")

    # ── tc-list / accept / reject ─────────────────────────────────────────── #
    p = sub.add_parser("tc-list", help="列出文档所有 Track Changes（支持按作者/范围/类型过滤）")
    p.add_argument("docx")
    p.add_argument("--author")
    p.add_argument("--type", choices=["ins", "del"])
    p.add_argument("--range")
    p.add_argument("--preview-len", dest="preview_len", type=int, default=80)
    p.add_argument("--fmt", choices=["json", "text"], default="json")

    for name in ("tc-accept", "tc-reject"):
        p = sub.add_parser(name, help=f"{name} Track Changes（支持按作者/范围/类型过滤）")
        p.add_argument("docx")
        p.add_argument("--author")
        p.add_argument("--type", choices=["ins", "del"])
        p.add_argument("--range")
        p.add_argument("--dry-run", action="store_true")
        p.add_argument("--out")

    # ── comments / headers ────────────────────────────────────────────────── #
    p = sub.add_parser("comment-list", help="列出文档 comments（可按作者/范围）")
    p.add_argument("docx")
    p.add_argument("--author")
    p.add_argument("--range")
    p.add_argument("--preview-len", dest="preview_len", type=int, default=80)
    p.add_argument("--fmt", choices=["json", "text"], default="json")

    p = sub.add_parser("comment-clean", help="删除 comments（支持全量或按作者/范围）")
    p.add_argument("docx")
    p.add_argument("--author")
    p.add_argument("--range")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--out")

    p = sub.add_parser("comment-remove", help="按 id 删除指定 comments")
    p.add_argument("docx")
    p.add_argument("--id", required=True)
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--out")

    p = sub.add_parser("header-clean", help="清除所有 header 内容（可选：移除 headerReference 引用）")
    p.add_argument("docx")
    p.add_argument("--remove-refs", dest="remove_refs", action="store_true")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--out")

    # ── review family ─────────────────────────────────────────────────────── #
    p = sub.add_parser("review", help="review 族命令（stats/inspect/comments/remove-comment/clean-comments）")
    review_sub = p.add_subparsers(dest="review_cmd", required=True)

    sp = review_sub.add_parser("stats", help="统计 review 文件中的 TC / comments / footer 风险")
    sp.add_argument("docx")
    sp.add_argument("--author")
    sp.add_argument("--range")
    sp.add_argument("--footer-blacklist", dest="footer_blacklist")
    sp.add_argument("--fmt", choices=["json", "text"], default="json")
    sp.set_defaults(func=cmd_review_family)

    sp = review_sub.add_parser("inspect", help="检查某段范围内的 paragraphs / TC / comments")
    sp.add_argument("docx")
    sp.add_argument("--range", required=True)
    sp.add_argument("--author")
    sp.add_argument("--preview-len", dest="preview_len", type=int, default=120)
    sp.add_argument("--fmt", choices=["json", "text"], default="json")
    sp.add_argument("--rich", dest="rich", action="store_true",
                    help="输出带格式标记的详细预览（加粗/字号/字体/行距/分页）")
    sp.set_defaults(func=cmd_review_family)

    sp = review_sub.add_parser("comments", help="列出文档 comments")
    sp.add_argument("docx")
    sp.add_argument("--author")
    sp.add_argument("--range")
    sp.add_argument("--preview-len", dest="preview_len", type=int, default=80)
    sp.add_argument("--fmt", choices=["json", "text"], default="json")
    sp.set_defaults(func=cmd_review_family)

    sp = review_sub.add_parser("remove-comment", help="按 id 删除指定 comments")
    sp.add_argument("docx")
    sp.add_argument("--id", required=True)
    sp.add_argument("--dry-run", action="store_true")
    sp.add_argument("--out")
    sp.set_defaults(func=cmd_review_family)

    sp = review_sub.add_parser("clean-comments", help="删除 comments（支持全量或按作者/范围）")
    sp.add_argument("docx")
    sp.add_argument("--author")
    sp.add_argument("--range")
    sp.add_argument("--dry-run", action="store_true")
    sp.add_argument("--out")
    sp.set_defaults(func=cmd_review_family)

    # ── legacy flat review aliases ────────────────────────────────────────── #
    p = sub.add_parser("review-stats", help="统计 review 文件中的 TC / comments / footer 风险（legacy flat command）")
    p.add_argument("docx")
    p.add_argument("--author")
    p.add_argument("--range")
    p.add_argument("--footer-blacklist", dest="footer_blacklist")
    p.add_argument("--fmt", choices=["json", "text"], default="json")

    p = sub.add_parser("review-inspect", help="检查某段范围内的 paragraphs / TC / comments（legacy flat command）")
    p.add_argument("docx")
    p.add_argument("--range", required=True)
    p.add_argument("--author")
    p.add_argument("--preview-len", dest="preview_len", type=int, default=120)
    p.add_argument("--fmt", choices=["json", "text"], default="json")

    # ── clean ─────────────────────────────────────────────────────────────── #
    p = sub.add_parser("clean", help="执行版一键清理（accept/reject TC + 批注 + header）")
    p.add_argument("docx")
    p.add_argument("--accept", action="store_true")
    p.add_argument("--reject", action="store_true")
    p.add_argument("--author")
    p.add_argument("--keep-comments", dest="keep_comments", action="store_true")
    p.add_argument("--keep-headers", dest="keep_headers", action="store_true")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--backup", action="store_true")
    p.add_argument("--yes", action="store_true")
    p.add_argument("--out")

    args = parser.parse_args()

    dispatch = {
        "lint":               cmd_lint,
        "extract":            cmd_extract,
        "fill-table":         cmd_fill_table,
        "fill-kv":            cmd_fill_kv,
        "format-table":       cmd_format_table,
        "cleanup":            cmd_cleanup,
        "bold-terms":         cmd_bold_terms,
        "copy-table":         cmd_copy_table,
        "table-inspect":      cmd_table_inspect,
        "table-replace":      cmd_table_replace,
        "table-format-brush": cmd_table_format_brush,
        "tc-insert":          cmd_tc_insert,
        "tc-replace":         cmd_tc_replace,
        "tc-delete":          cmd_tc_delete,
        "highlight":          cmd_highlight,
        "format-brush":       cmd_format_brush,
        "set-outline-level":  cmd_set_outline_level,
        "para-runs":          cmd_para_runs,
        "para-query":         cmd_para_query,
        "structure":          cmd_structure,
        "para-insert":        cmd_para_insert,
        "page-break":         cmd_page_break,
        "section-break":      cmd_section_break,
        "page-setup":         cmd_page_setup,
        "doctor":             getattr(args, "func", cmd_doctor),
        "inject":             cmd_inject,
        "numbering":          getattr(args, "func", cmd_numbering_family),
        "section-restart-numbering": cmd_section_restart_numbering,
        "footer":             getattr(args, "func", cmd_footer_family),
        "footer-audit":       cmd_footer_audit,
        "fill-footer":        cmd_fill_footer,
        "tc-list":            cmd_tc_list,
        "tc-accept":          cmd_tc_accept,
        "tc-reject":          cmd_tc_reject,
        "comment-list":       cmd_comment_list,
        "comment-clean":      cmd_comment_clean,
        "comment-remove":     cmd_comment_remove,
        "header-clean":       cmd_header_clean,
        "review":             getattr(args, "func", cmd_review_family),
        "review-stats":       cmd_review_stats,
        "review-inspect":     cmd_review_inspect,
        "clean":              cmd_clean,
        "create":             cmd_create,
        "scaffold":           cmd_scaffold,
        "new-table":          cmd_new_table,
        "toc":                cmd_toc,
        "stats":              cmd_stats,
        "insert":             cmd_insert,
        "replace":            cmd_replace,
        "delete":             cmd_delete,
    }

    func = dispatch.get(args.command)
    if func is None:
        parser.print_help()
        return 1
    return func(args)
