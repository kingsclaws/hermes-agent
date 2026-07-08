"""Lex Preview Tool — Convert .docx to styled HTML for agent visualization.

Solves the fundamental problem: agents can't "see" documents.
lex_read returns structured text, lex_preview returns rendered HTML.

Usage:
  lex_preview(path="/path/to/doc") → styled HTML with Track Changes, tables, images

Dependencies: pandoc (for docx→html conversion)
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import tempfile
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# ── CSS for styled preview ──────────────────────────────────────────────────

_PREVIEW_CSS = """\
<style>
  body {
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", "Microsoft YaHei", sans-serif;
    max-width: 900px; margin: 0 auto; padding: 20px;
    line-height: 1.7; color: #333; font-size: 14px;
  }
  .insertion {
    background: #d4edda; border-bottom: 2px solid #28a745;
    padding: 1px 3px; border-radius: 2px;
  }
  .deletion {
    background: #f8d7da; text-decoration: line-through;
    border-bottom: 2px solid #dc3545; padding: 1px 3px; border-radius: 2px;
  }
  .insertion::after { content: " ✚"; font-size: 0.7em; color: #28a745; }
  .deletion::after { content: " ✖"; font-size: 0.7em; color: #dc3545; }
  table { border-collapse: collapse; width: 100%; margin: 1em 0; font-size: 0.85em; }
  td, th { border: 1px solid #ddd; padding: 6px 8px; text-align: left; }
  tr:nth-child(even) { background: #f9f9f9; }
  th { background: #f0f0f0; font-weight: 600; }
  h1 { font-size: 1.4em; border-bottom: 2px solid #333; padding-bottom: 8px; }
  h2 { font-size: 1.2em; color: #1a1a1a; border-bottom: 1px solid #ddd; padding-bottom: 4px; }
  h3 { font-size: 1.05em; color: #333; }
  blockquote { border-left: 3px solid #666; margin-left: 0; padding-left: 1em; color: #555; background: #fafafa; padding: 8px 12px; }
  .comment-start { background: #fff3cd; padding: 2px 4px; border-radius: 3px; font-size: 0.85em; }
  .comment-end { background: #fff3cd; padding: 2px 4px; border-radius: 3px; }
  img { max-width: 100%; height: auto; }
  .preview-meta { color: #888; font-size: 0.8em; margin-bottom: 1em; border-bottom: 1px solid #eee; padding-bottom: 8px; }
</style>
"""

# ── Tool schema ─────────────────────────────────────────────────────────────

LEX_PREVIEW_SCHEMA = {
    "name": "lex_preview",
    "description": (
        "Convert a .docx document to styled HTML for visual inspection. "
        "Shows Track Changes (insertions/deletions with author+date), tables, "
        "images, comments, headers/footers, and formatting — exactly as a human "
        "would see the document. Use this AFTER lex_read to visually verify "
        "your edits, or BEFORE editing to understand the document layout. "
        "Returns self-contained HTML that can be saved to a file."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "Path to the .docx file.",
            },
            "track_changes": {
                "type": "string",
                "enum": ["all", "accept", "reject", "none"],
                "description": "How to render Track Changes. 'all' shows both insertions and deletions (default). 'accept' shows only insertions. 'reject' shows only deletions. 'none' hides all TC.",
            },
            "output": {
                "type": "string",
                "description": "Optional path to save the HTML file. If omitted, returns HTML content directly.",
            },
        },
        "required": ["path"],
    },
}

# ── Handler ─────────────────────────────────────────────────────────────────

def _check_pandoc() -> bool:
    """Check if pandoc is available."""
    try:
        result = subprocess.run(["pandoc", "--version"], capture_output=True, text=True, timeout=5)
        return result.returncode == 0
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False


def _extract_format_map(docx_path: str) -> dict:
    """Extract per-paragraph formatting from docx XML.

    Returns {para_index: {"font": str, "size_pt": float, "bold": bool,
    "italic": bool, "line_spacing": str, "alignment": str}} for paragraphs
    that have explicit formatting.
    """
    import zipfile
    from lxml import etree

    W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
    fmt_map = {}

    try:
        with zipfile.ZipFile(docx_path) as zf:
            doc_xml = zf.read("word/document.xml")
            root = etree.fromstring(doc_xml)
            body = root.find(f"{W}body")
            paras = [c for c in body if c.tag == f"{W}p"]

            # Also read styles.xml for default font
            try:
                styles_xml = zf.read("word/styles.xml")
                sroot = etree.fromstring(styles_xml)
                defaults = sroot.find(f"{W}docDefaults")
                default_font = ""
                default_size = 0
                if defaults is not None:
                    rprd = defaults.find(f".//{W}rPr")
                    if rprd is not None:
                        rf = rprd.find(f"{W}rFonts")
                        if rf is not None:
                            default_font = rf.get(f"{W}eastAsia", "") or rf.get(f"{W}ascii", "")
                        sz = rprd.find(f"{W}sz")
                        if sz is not None:
                            default_size = int(sz.get(f"{W}val", 0)) / 2  # half-points to points
            except Exception:
                default_font = ""
                default_size = 0

            for pi, p in enumerate(paras):
                # Get paragraph-level formatting
                ppr = p.find(f"{W}pPr")
                alignment = ""
                line_spacing = ""
                if ppr is not None:
                    jc = ppr.find(f"{W}jc")
                    if jc is not None:
                        alignment = jc.get(f"{W}val", "")
                    spacing = ppr.find(f"{W}spacing")
                    if spacing is not None:
                        line_val = spacing.get(f"{W}line", "")
                        if line_val:
                            try:
                                # twips to px (1 twip = 1/20 pt, 1pt = 1.333px)
                                line_spacing = f"{int(line_val) / 20 * 1.333:.1f}px"
                            except ValueError:
                                pass

                # Get run-level formatting from first run with text
                font = ""
                size_pt = 0
                bold = False
                italic = False
                for r in p.findall(f"{W}r"):
                    t = r.find(f"{W}t")
                    if t is not None and t.text:
                        rpr = r.find(f"{W}rPr")
                        if rpr is not None:
                            rf = rpr.find(f"{W}rFonts")
                            if rf is not None:
                                font = rf.get(f"{W}eastAsia", "") or rf.get(f"{W}ascii", "")
                            sz = rpr.find(f"{W}sz")
                            if sz is not None:
                                size_pt = int(sz.get(f"{W}val", 0)) / 2
                            b = rpr.find(f"{W}b")
                            bold = b is not None
                            i = rpr.find(f"{W}i")
                            italic = i is not None
                        break

                if font or size_pt or bold or italic or line_spacing or alignment:
                    fmt_map[pi] = {
                        "font": font or default_font,
                        "size_pt": size_pt or default_size,
                        "bold": bold,
                        "italic": italic,
                        "line_spacing": line_spacing,
                        "alignment": alignment,
                    }
    except Exception:
        pass

    return fmt_map


def _inject_format_styles(html: str, fmt_map: dict) -> str:
    """Inject inline CSS styles into HTML paragraphs based on format map."""
    import re

    if not fmt_map:
        return html

    # Match <p> tags and inject styles
    para_idx = 0
    def _replace_para(match):
        nonlocal para_idx
        tag = match.group(0)
        idx = para_idx
        para_idx += 1

        fmt = fmt_map.get(idx)
        if not fmt:
            return tag

        styles = []
        if fmt.get("font"):
            styles.append(f"font-family: '{fmt['font']}', serif")
        if fmt.get("size_pt") and fmt["size_pt"] > 0:
            styles.append(f"font-size: {fmt['size_pt']:.1f}pt")
        if fmt.get("bold"):
            styles.append("font-weight: bold")
        if fmt.get("italic"):
            styles.append("font-style: italic")
        if fmt.get("line_spacing"):
            styles.append(f"line-height: {fmt['line_spacing']}")
        if fmt.get("alignment"):
            align_map = {"center": "center", "right": "right", "both": "justify"}
            if fmt["alignment"] in align_map:
                styles.append(f"text-align: {align_map[fmt['alignment']]}")

        if not styles:
            return tag

        style_str = "; ".join(styles)
        # Insert style attribute into <p> tag
        if 'style="' in tag:
            return tag.replace('style="', f'style="{style_str}; ')
        else:
            return tag.replace("<p", f'<p style="{style_str}"', 1)

    html = re.sub(r"<p[^>]*>", _replace_para, html)
    return html


def _handle_preview(args: dict, **kwargs) -> str:
    path = args.get("path", "").strip()
    if not path:
        return json.dumps({"success": False, "error": "path is required"})

    # Expand ~ and make absolute
    path = str(Path(path).expanduser().resolve())

    if not Path(path).exists():
        return json.dumps({"success": False, "error": f"File not found: {path}"})
    if not path.endswith(".docx"):
        return json.dumps({"success": False, "error": "Only .docx files are supported"})

    if not _check_pandoc():
        return json.dumps({
            "success": False,
            "error": "pandoc is not installed. Install with: apt-get install pandoc"
        })

    tc_mode = args.get("track_changes", "all")
    output_path = args.get("output", "").strip()

    # Extract formatting from docx XML
    fmt_map = _extract_format_map(path)

    # Build pandoc command
    cmd = [
        "pandoc", path,
        "-t", "html",
        "--track-changes=" + tc_mode,
        "--self-contained",
        "--metadata", "title=Document Preview",
    ]

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=60,
        )
        if result.returncode != 0:
            return json.dumps({
                "success": False,
                "error": f"pandoc failed: {result.stderr[:500]}"
            })

        html = result.stdout

        # Inject format styles from docx XML
        html = _inject_format_styles(html, fmt_map)

        # Inject our CSS (pandoc's --self-contained includes its own style,
        # but we want our TC styling to take precedence)
        html = html.replace("</head>", _PREVIEW_CSS + "\n</head>")

        # Fix the garbled title from --self-contained with CJK filenames
        import re
        html = re.sub(
            r'<title>[^<]*</title>',
            f'<title>{Path(path).stem}</title>',
            html
        )

        # Count TC markers for the response
        ins_count = html.count('class="insertion"')
        del_count = html.count('class="deletion"')
        table_count = html.count('<table')
        img_count = html.count('<img')

        # Save to file if requested
        if output_path:
            output_path = str(Path(output_path).expanduser().resolve())
            Path(output_path).parent.mkdir(parents=True, exist_ok=True)
            Path(output_path).write_text(html, encoding="utf-8")
            return json.dumps({
                "success": True,
                "output": output_path,
                "size_bytes": len(html),
                "stats": {
                    "insertions": ins_count,
                    "deletions": del_count,
                    "tables": table_count,
                    "images": img_count,
                },
                "message": f"HTML preview saved to {output_path}",
            }, ensure_ascii=False)

        # Return HTML directly (truncated if too large)
        if len(html) > 100_000:
            # Save to temp file and return path
            with tempfile.NamedTemporaryFile(suffix=".html", delete=False, dir="/tmp") as f:
                f.write(html.encode("utf-8"))
                tmp_path = f.name
            return json.dumps({
                "success": True,
                "output": tmp_path,
                "size_bytes": len(html),
                "stats": {
                    "insertions": ins_count,
                    "deletions": del_count,
                    "tables": table_count,
                    "images": img_count,
                },
                "message": f"HTML preview saved to {tmp_path} (too large to return inline: {len(html)} bytes)",
            }, ensure_ascii=False)

        return json.dumps({
            "success": True,
            "html": html,
            "size_bytes": len(html),
            "stats": {
                "insertions": ins_count,
                "deletions": del_count,
                "tables": table_count,
                "images": img_count,
            },
        }, ensure_ascii=False)

    except subprocess.TimeoutExpired:
        return json.dumps({"success": False, "error": "pandoc timed out (60s). File may be too large."})
    except Exception as exc:
        return json.dumps({"success": False, "error": str(exc)})


# ── Registry ────────────────────────────────────────────────────────────────

from tools.registry import registry  # noqa: E402

registry.register(
    name="lex_preview",
    toolset="lex-docx",
    schema=LEX_PREVIEW_SCHEMA,
    handler=_handle_preview,
    check_fn=_check_pandoc,
    description=LEX_PREVIEW_SCHEMA["description"],
    emoji="👁️",
)
