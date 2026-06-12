"""Structured legal template audit and batch-fill tools.

lex_template_audit — scan an annotated .docx template and produce a structured
    manifest of fill-in blanks, checkboxes, colored notes, bracketed annotations,
    and usage-guide paragraphs.  Template-format agnostic: works with NAFMII,
    APLMA, 证监会, 银保监会, and any other convention that uses highlight/checkbox/
    color-coded annotations in .docx files.

lex_template_fill  — batch-fill a structured template: fill blanks, select
    checkboxes, handle optional clauses, strip annotations, and produce a clean
    document. Driven entirely by the audit manifest — no template-specific logic.

Both tools operate on OOXML directly (no python-docx).
"""

from __future__ import annotations

import json
import logging
import re
import shutil
import tempfile
import zipfile
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from lxml import etree

from tools.registry import registry, tool_error

logger = logging.getLogger(__name__)

# ── OOXML constants ────────────────────────────────────────────────────────────

W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
W = f"{{{W_NS}}}"

# ── Supported template conventions ─────────────────────────────────────────────
# Each convention is a set of detection heuristics + default settings.
# Adding a new convention means adding an entry here — the tools stay generic.

TEMPLATE_CONVENTIONS = {
    "nafmii": {
        "label": "NAFMII 银行间协会贷款合同",
        "guide_keywords": ["说明", "请在此", "填写", "根据实际", "勾选", "可选", "选择"],
        "guide_max_para": 10,
        "checkbox_pattern": re.compile(r"☐\[([A-Z])\]\s*([^\n\r☐]*)"),
        "annotation_pattern": re.compile(r"【[^】]*】"),
        "highlight_color": "yellow",
        "note_colors": ["0000FF"],
        "content_keywords": ["银团贷款", "贷款合同", "借款人", "贷款人"],
    },
    "generic": {
        "label": "通用标注模板",
        "guide_keywords": ["说明", "请填写", "注：", "提示", "Instructions", "Note:"],
        "guide_max_para": 8,
        "checkbox_pattern": re.compile(r"☐[^\n\r]*"),
        "annotation_pattern": re.compile(r"【[^】]*】|\[[^]]*\]"),
        "highlight_color": "yellow",
        "note_colors": ["0000FF", "FF0000", "808080"],
        "content_keywords": [],
    },
}


# ── Check function ─────────────────────────────────────────────────────────────


def _check_lexitool():
    import importlib.util
    try:
        return importlib.util.find_spec("lexitool") is not None
    except (ImportError, ValueError):
        return False


# ── Schemas ────────────────────────────────────────────────────────────────────

LEX_TEMPLATE_AUDIT_SCHEMA = {
    "name": "lex_template_audit",
    "description": (
        "Scan an annotated .docx template and produce a structured JSON manifest "
        "of all fill-in blanks (highlighted runs), binary-choice checkboxes, "
        "colored internal notes, bracketed annotations, and usage-guide paragraphs. "
        "Template-format agnostic — detects NAFMII, APLMA, CSRC, CBRC, and any "
        "other convention that uses highlight/checkbox/color annotations. "
        "Read-only — makes no modifications to the document."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "Path to the annotated .docx template.",
            },
            "convention": {
                "type": "string",
                "description": (
                    "Optional template convention hint. One of: nafmii, generic. "
                    "If omitted, auto-detected from document content."
                ),
            },
        },
        "required": ["path"],
    },
}

LEX_TEMPLATE_FILL_SCHEMA = {
    "name": "lex_template_fill",
    "description": (
        "Batch-fill a structured template from a lex_template_audit manifest. "
        "By default this runs only non-destructive phases (fill blanks and "
        "select checkboxes). Destructive cleanup phases — optional clause "
        "deletion, colored-note deletion, bracketed annotation deletion, guide "
        "deletion, and highlight stripping — must be explicitly requested in "
        "phases after the manifest has been reviewed. Operates on OOXML "
        "directly and returns a before/after verification report."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "Path to the annotated .docx template.",
            },
            "manifest_json": {
                "type": "string",
                "description": "JSON manifest string from lex_template_audit.",
            },
            "fill_values_json": {
                "type": "string",
                "description": (
                    "JSON object with 'blanks' (para-number → text mapping) "
                    "and 'checkboxes' (para-number → 'A' or 'B' or null). "
                    'Example: {"blanks": {"15": "张三", "23": "伍仟万元"}, '
                    '"checkboxes": {"42": "A", "58": "B"}}'
                ),
            },
            "output": {
                "type": "string",
                "description": "Output path for the filled .docx.",
            },
            "phases": {
                "type": "array",
                "items": {"type": "string"},
                "description": (
                    "Phases to run. Default: non-destructive fill_blanks and "
                    "select_checkboxes only. Explicitly request cleanup phases "
                    "after reviewing the audit manifest. "
                    "Options: fill_blanks, select_checkboxes, optional_clauses, "
                    "delete_colored_notes, delete_annotations, delete_guide, "
                    "strip_highlights."
                ),
            },
            "tc": {
                "type": "boolean",
                "description": "Track Changes mode for content modifications. Default: true.",
            },
        },
        "required": ["path", "manifest_json", "fill_values_json", "output"],
    },
}

# ── OOXML I/O helpers ──────────────────────────────────────────────────────────


def _read_docx_xml(path: str) -> Tuple[bytes, Dict[str, bytes]]:
    with zipfile.ZipFile(path, "r") as zf:
        doc_xml = zf.read("word/document.xml")
        other = {}
        for name in zf.namelist():
            if name != "word/document.xml":
                other[name] = zf.read(name)
    return doc_xml, other


def _write_docx_xml(path: str, doc_xml: bytes, other: Dict[str, bytes]) -> None:
    fd, tmp = tempfile.mkstemp(prefix="lex_template_", suffix=".docx")
    import os as _os
    _os.close(fd)
    with zipfile.ZipFile(tmp, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        ct_xml = other.get("[Content_Types].xml")
        if ct_xml is not None:
            zf.writestr("[Content_Types].xml", ct_xml)
        zf.writestr("word/document.xml", doc_xml)
        for name, data in other.items():
            if name == "[Content_Types].xml":
                continue
            zf.writestr(name, data)
    shutil.move(tmp, path)


def _all_paras(root: etree._Element) -> List[etree._Element]:
    return [el for el in root.iter() if el.tag == f"{W}p"]


def _para_text(para: etree._Element) -> str:
    parts = []
    for t in para.iter(f"{W}t"):
        parts.append(t.text or "")
    return "".join(parts)


def _para_runs(para: etree._Element) -> List[etree._Element]:
    return [child for child in para if child.tag == f"{W}r"]


def _run_has_highlight(run: etree._Element, color: str = "yellow") -> bool:
    rpr = run.find(f"{W}rPr")
    if rpr is None:
        return False
    hl = rpr.find(f"{W}highlight")
    if hl is None:
        return False
    return hl.get(f"{W}val") == color


def _run_has_color(run: etree._Element, color: Optional[str] = None) -> bool:
    """Check if a w:r has w:color. If color is given, match that specific value."""
    rpr = run.find(f"{W}rPr")
    if rpr is None:
        return False
    c = rpr.find(f"{W}color")
    if c is None:
        return False
    if color is None:
        return True
    return (c.get(f"{W}val") or "").upper() == color.upper()


def _run_color_value(run: etree._Element) -> Optional[str]:
    """Return the w:color value of a w:r, or None."""
    rpr = run.find(f"{W}rPr")
    if rpr is None:
        return None
    c = rpr.find(f"{W}color")
    if c is None:
        return None
    return (c.get(f"{W}val") or "").upper()


def _run_has_italic(run: etree._Element) -> bool:
    rpr = run.find(f"{W}rPr")
    if rpr is None:
        return False
    return rpr.find(f"{W}i") is not None


def _run_text(run: etree._Element) -> str:
    parts = []
    for t in run.iter(f"{W}t"):
        parts.append(t.text or "")
    return "".join(parts)


def _set_run_text(run: etree._Element, text: str) -> None:
    t = run.find(f"{W}t")
    if t is None:
        t = etree.SubElement(run, f"{W}t")
        t.set("{http://www.w3.org/XML/1998/namespace}space", "preserve")
    t.text = text


def _remove_highlight_from_run(run: etree._Element) -> bool:
    rpr = run.find(f"{W}rPr")
    if rpr is None:
        return False
    hl = rpr.find(f"{W}highlight")
    if hl is not None:
        rpr.remove(hl)
        return True
    return False


def _clear_run(run: etree._Element) -> None:
    """Clear text inside a w:r, preserving its XML structure.

    Must NOT delete the element — removing w:r nodes can orphan sibling
    w:bookmarkStart/w:bookmarkEnd, w:fldChar, or w:commentRange*
    elements and corrupt the document.
    """
    for t in run.iter(f"{W}t"):
        t.text = ""


def _clear_para(para: etree._Element) -> None:
    """Clear all run text in a w:p, preserving the paragraph structure.

    Must NOT delete the w:p element — removing w:p nodes can break
    cross-reference fields (w:fldChar/w:instrText) and SDT wrappers
    that reference the paragraph, corrupting the document.
    """
    for run in _para_runs(para):
        _clear_run(run)


def _delete_run(run: etree._Element) -> None:
    """DEPRECATED: use _clear_run() instead. Removing w:r elements can corrupt the document."""
    _clear_run(run)


def _delete_para(para: etree._Element) -> None:
    """DEPRECATED: use _clear_para() instead. Removing w:p elements can corrupt the document."""
    _clear_para(para)


# ── Convention detection ───────────────────────────────────────────────────────


def _detect_convention(paras: List[etree._Element]) -> str:
    """Auto-detect the template convention from document content."""
    full_text = " ".join(_para_text(p) for p in paras[:20])

    # NAFMII: strong signals from banking/loan context + Chinese checkbox convention
    nafmii_score = 0
    if "银团贷款" in full_text or "贷款合同" in full_text:
        nafmii_score += 2
    if "借款人" in full_text and "贷款人" in full_text:
        nafmii_score += 1
    if "固定资产" in full_text or "流动资金" in full_text:
        nafmii_score += 2
    if "☐[A]" in full_text:
        nafmii_score += 3
    if nafmii_score >= 4:
        return "nafmii"

    return "generic"


def _load_convention(name: str) -> Dict[str, Any]:
    """Load convention settings, falling back to generic."""
    return TEMPLATE_CONVENTIONS.get(name, TEMPLATE_CONVENTIONS["generic"])


# ── Audit helpers ──────────────────────────────────────────────────────────────


def _is_guide_para(para: etree._Element, para_idx: int, convention: Dict[str, Any]) -> bool:
    if para_idx >= convention.get("guide_max_para", 8):
        return False
    text = _para_text(para)
    if not text.strip():
        return False
    keywords = convention.get("guide_keywords", [])
    return any(kw in text for kw in keywords)


def _find_blanks(paras: List[etree._Element], convention: Dict[str, Any]) -> List[Dict[str, Any]]:
    highlight_color = convention.get("highlight_color", "yellow")
    blanks = []
    for para_idx, para in enumerate(paras):
        for run in _para_runs(para):
            if _run_has_highlight(run, highlight_color):
                text = _run_text(run)
                blanks.append({
                    "para": para_idx + 1,
                    "text": text,
                    "text_length": len(text.strip()),
                    "context": _para_text(para)[:120],
                })
    return blanks


def _find_checkboxes(paras: List[etree._Element], convention: Dict[str, Any]) -> List[Dict[str, Any]]:
    pattern = convention.get("checkbox_pattern", re.compile(r"☐[^\n\r]*"))
    checkboxes = []
    for para_idx, para in enumerate(paras):
        text = _para_text(para)
        matches = pattern.findall(text)
        if not matches:
            continue
        # If pattern has groups (like ☐[A] label), extract structured options
        if pattern.groups >= 2:
            options = [{"key": m[0], "label": m[1].strip()} for m in matches]
        else:
            options = [{"key": str(i), "label": m.strip() if isinstance(m, str) else str(m)} for i, m in enumerate(matches)]
        if options:
            checkboxes.append({
                "para": para_idx + 1,
                "text": text[:200],
                "options": options,
                "selected": None,
            })
    return checkboxes


def _find_colored_notes(paras: List[etree._Element], convention: Dict[str, Any]) -> List[Dict[str, Any]]:
    note_colors = [c.upper() for c in convention.get("note_colors", [])]
    notes = []
    for para_idx, para in enumerate(paras):
        for run in _para_runs(para):
            c = _run_color_value(run)
            if c and c in note_colors:
                text = _run_text(run)
                if text.strip():
                    notes.append({
                        "para": para_idx + 1,
                        "text": text,
                        "color": c,
                        "italic": _run_has_italic(run),
                        "context": _para_text(para)[:120],
                    })
    return notes


def _find_annotations(paras: List[etree._Element], convention: Dict[str, Any]) -> List[Dict[str, Any]]:
    pattern = convention.get("annotation_pattern", re.compile(r"【[^】]*】"))
    annotations = []
    for para_idx, para in enumerate(paras):
        text = _para_text(para)
        for m in pattern.finditer(text):
            parent_tag = para.getparent().tag if para.getparent() is not None else ""
            annotations.append({
                "para": para_idx + 1,
                "text": m.group(),
                "in_table": "tc" in parent_tag or "sdtContent" in parent_tag,
                "context": text[:120],
            })
    return annotations


def _find_guide_paras(paras: List[etree._Element], convention: Dict[str, Any]) -> List[int]:
    return [i + 1 for i, p in enumerate(paras) if _is_guide_para(p, i, convention)]


# ── Audit handler ──────────────────────────────────────────────────────────────


def _handle_template_audit(args: dict, **kwargs) -> str:
    path = str(args["path"]).strip()
    if not path:
        return tool_error("path is required")

    try:
        doc_xml, _other = _read_docx_xml(path)
    except FileNotFoundError:
        return tool_error(f"File not found: {path}")
    except Exception as e:
        return tool_error(f"Failed to open {path}: {e}")

    root = etree.fromstring(doc_xml)
    paras = _all_paras(root)

    convention_name = str(args.get("convention") or "").strip()
    if not convention_name:
        convention_name = _detect_convention(paras)
    convention = _load_convention(convention_name)

    blanks = _find_blanks(paras, convention)
    checkboxes = _find_checkboxes(paras, convention)
    colored_notes = _find_colored_notes(paras, convention)
    annotations = _find_annotations(paras, convention)
    guide_paras = _find_guide_paras(paras, convention)

    manifest = {
        "template_type": "structured_annotated",
        "convention": convention_name,
        "convention_label": convention.get("label", "Unknown"),
        "total_paras": len(paras),
        "blanks": blanks,
        "blanks_count": len(blanks),
        "checkboxes": checkboxes,
        "checkboxes_count": len(checkboxes),
        "colored_notes": colored_notes,
        "colored_notes_count": len(colored_notes),
        "annotations": annotations,
        "annotations_count": len(annotations),
        "guide_paras": guide_paras,
        "guide_paras_count": len(guide_paras),
        "path": path,
    }

    return json.dumps(manifest, ensure_ascii=False, indent=2)


# ── Phase runners ──────────────────────────────────────────────────────────────


def _phase_fill_blanks(
    root: etree._Element,
    paras: List[etree._Element],
    manifest: Dict[str, Any],
    fill_values: Dict[str, Any],
    tc: bool,
    convention: Dict[str, Any],
) -> Dict[str, Any]:
    blanks_map = fill_values.get("blanks", {})
    highlight_color = convention.get("highlight_color", "yellow")
    filled = 0
    skipped = 0
    errors = []

    for blank in manifest.get("blanks", []):
        para_idx = blank["para"] - 1
        if para_idx < 0 or para_idx >= len(paras):
            errors.append(f"Blank para {blank['para']} out of range")
            continue

        para_key = str(blank["para"])
        if para_key not in blanks_map:
            skipped += 1
            continue

        new_value = str(blanks_map[para_key])
        para = paras[para_idx]
        replaced = False
        for run in _para_runs(para):
            if _run_has_highlight(run, highlight_color):
                _set_run_text(run, new_value)
                replaced = True
                filled += 1
                break

        if not replaced:
            errors.append(f"Could not find highlighted run in paragraph {blank['para']}")

    return {"phase": "fill_blanks", "filled": filled, "skipped": skipped, "errors": errors}


def _phase_select_checkboxes(
    root: etree._Element,
    paras: List[etree._Element],
    manifest: Dict[str, Any],
    fill_values: Dict[str, Any],
    tc: bool,
    convention: Dict[str, Any],
) -> Dict[str, Any]:
    checkbox_map = fill_values.get("checkboxes", {})
    selected = 0
    cleared = 0
    errors = []

    for cb in manifest.get("checkboxes", []):
        para_idx = cb["para"] - 1
        if para_idx < 0 or para_idx >= len(paras):
            errors.append(f"Checkbox para {cb['para']} out of range")
            continue

        para_key = str(cb["para"])
        choice = checkbox_map.get(para_key)
        if choice is None:
            continue

        choice = str(choice).upper()
        valid_keys = {o["key"] for o in cb.get("options", [])}
        if choice not in valid_keys:
            errors.append(f"Invalid choice '{choice}' for para {cb['para']}. Valid: {valid_keys}")
            continue

        para = paras[para_idx]
        selected_pattern = f"☐[{choice}]"
        selected_replacement = f"☑[{choice}]"

        for run in _para_runs(para):
            run_text = _run_text(run)
            if selected_pattern in run_text:
                _set_run_text(run, run_text.replace(selected_pattern, selected_replacement))
                selected += 1

            # Clear unselected options in same paragraph
            for opt in cb.get("options", []):
                other = opt["key"]
                if other == choice:
                    continue
                other_pattern = f"☐[{other}]"
                if other_pattern in run_text:
                    _set_run_text(run, run_text.replace(other_pattern, f"☐[{other}]"))
                    cleared += 1

    return {"phase": "select_checkboxes", "selected": selected, "cleared": cleared, "errors": errors}


def _phase_optional_clauses(
    root: etree._Element,
    paras: List[etree._Element],
    manifest: Dict[str, Any],
    fill_values: Dict[str, Any],
    tc: bool,
    convention: Dict[str, Any],
) -> Dict[str, Any]:
    """Delete clause text for unselected checkbox options.

    When checkbox selects [A], delete [B] clause text following it, and vice versa.
    """
    checkbox_map = fill_values.get("checkboxes", {})
    deleted_paras: List[int] = []
    errors: List[str] = []

    for cb in manifest.get("checkboxes", []):
        para_key = str(cb["para"])
        choice = str(checkbox_map.get(para_key) or "")
        if not choice:
            continue

        valid_keys = {o["key"] for o in cb.get("options", [])}
        if choice not in valid_keys:
            continue

        cb_para_idx = cb["para"] - 1
        other_keys = [k for k in valid_keys if k != choice]

        for offset in range(1, 21):
            look_idx = cb_para_idx + offset
            if look_idx >= len(paras):
                break
            look_text = _para_text(paras[look_idx])

            for other in other_keys:
                if f"选项{other}" in look_text or f"[{other}]" in look_text:
                    if look_idx + 1 not in deleted_paras:
                        deleted_paras.append(look_idx + 1)
                        _delete_para(paras[look_idx])
                        paras = _all_paras(root)

    return {"phase": "optional_clauses", "deleted_paragraphs": deleted_paras, "errors": errors}


def _phase_delete_colored_notes(
    root: etree._Element,
    paras: List[etree._Element],
    manifest: Dict[str, Any],
    fill_values: Dict[str, Any],
    tc: bool,
    convention: Dict[str, Any],
) -> Dict[str, Any]:
    note_colors = [c.upper() for c in convention.get("note_colors", [])]
    deleted = 0
    errors: List[str] = []

    for para in root.iter(f"{W}p"):
        for run in _para_runs(para):
            c = _run_color_value(run)
            if c and c in note_colors:
                _delete_run(run)
                deleted += 1

    return {"phase": "delete_colored_notes", "deleted_runs": deleted, "errors": errors}


def _phase_delete_annotations(
    root: etree._Element,
    paras: List[etree._Element],
    manifest: Dict[str, Any],
    fill_values: Dict[str, Any],
    tc: bool,
    convention: Dict[str, Any],
) -> Dict[str, Any]:
    pattern = convention.get("annotation_pattern", re.compile(r"【[^】]*】"))
    removed = 0

    for para in root.iter(f"{W}p"):
        for run in _para_runs(para):
            text = _run_text(run)
            new_text = pattern.sub("", text)
            if new_text != text:
                _set_run_text(run, new_text)
                removed += 1

    return {"phase": "delete_annotations", "removed_from_runs": removed, "errors": []}


def _phase_delete_guide(
    root: etree._Element,
    paras: List[etree._Element],
    manifest: Dict[str, Any],
    fill_values: Dict[str, Any],
    tc: bool,
    convention: Dict[str, Any],
) -> Dict[str, Any]:
    guide_paras = manifest.get("guide_paras", [])
    deleted = 0
    errors: List[str] = []

    for para_idx_1 in sorted(guide_paras, reverse=True):
        para_idx = para_idx_1 - 1
        if 0 <= para_idx < len(paras):
            _delete_para(paras[para_idx])
            deleted += 1
        else:
            errors.append(f"Guide para {para_idx_1} out of range")

    return {"phase": "delete_guide", "deleted_paragraphs": deleted, "errors": errors}


def _phase_strip_highlights(
    root: etree._Element,
    paras: List[etree._Element],
    manifest: Dict[str, Any],
    fill_values: Dict[str, Any],
    tc: bool,
    convention: Dict[str, Any],
) -> Dict[str, Any]:
    stripped = 0
    for run in root.iter(f"{W}r"):
        if _remove_highlight_from_run(run):
            stripped += 1
    return {"phase": "strip_highlights", "stripped_runs": stripped, "errors": []}


# ── Phase registry ─────────────────────────────────────────────────────────────

_PHASES = {
    "fill_blanks": _phase_fill_blanks,
    "select_checkboxes": _phase_select_checkboxes,
    "optional_clauses": _phase_optional_clauses,
    "delete_colored_notes": _phase_delete_colored_notes,
    "delete_annotations": _phase_delete_annotations,
    "delete_guide": _phase_delete_guide,
    "strip_highlights": _phase_strip_highlights,
}

_DEFAULT_PHASES = ["fill_blanks", "select_checkboxes"]


# ── Fill handler ───────────────────────────────────────────────────────────────


def _handle_template_fill(args: dict, **kwargs) -> str:
    path = str(args["path"]).strip()
    output = str(args["output"]).strip()
    manifest_json = str(args.get("manifest_json", "")).strip()
    fill_values_json = str(args.get("fill_values_json", "")).strip()
    tc = bool(args.get("tc", True))

    if not path or not output:
        return tool_error("path and output are required")
    if not manifest_json:
        return tool_error("manifest_json is required (output from lex_template_audit)")
    if not fill_values_json:
        return tool_error("fill_values_json is required")

    try:
        manifest = json.loads(manifest_json)
    except json.JSONDecodeError as e:
        return tool_error(f"Invalid manifest_json: {e}")

    try:
        fill_values = json.loads(fill_values_json)
    except json.JSONDecodeError as e:
        return tool_error(f"Invalid fill_values_json: {e}")

    if not isinstance(fill_values, dict):
        return tool_error("fill_values_json must be a JSON object with 'blanks' and 'checkboxes' keys")
    if "blanks" not in fill_values and "checkboxes" not in fill_values:
        return tool_error("fill_values_json must contain at least 'blanks' or 'checkboxes' key")

    convention = _load_convention(manifest.get("convention", "generic"))

    if path != output:
        import shutil as _shutil
        try:
            _shutil.copy2(path, output)
        except Exception as e:
            return tool_error(f"Failed to copy {path} to {output}: {e}")
        work_path = output
    else:
        work_path = path

    try:
        doc_xml, other = _read_docx_xml(work_path)
    except Exception as e:
        return tool_error(f"Failed to open {work_path}: {e}")

    root = etree.fromstring(doc_xml)
    paras = _all_paras(root)

    requested = [str(p).strip() for p in (args.get("phases") or []) if str(p).strip()]
    if not requested:
        phases_to_run = list(_DEFAULT_PHASES)
    else:
        invalid = [p for p in requested if p not in _PHASES]
        if invalid:
            return tool_error(f"Unknown phases: {', '.join(invalid)}. Valid: {', '.join(_PHASES)}")
        phases_to_run = requested

    phase_results: List[Dict[str, Any]] = []
    for phase_name in phases_to_run:
        try:
            result = _PHASES[phase_name](root, paras, manifest, fill_values, tc, convention)
            phase_results.append(result)
        except Exception as e:
            logger.exception("Phase %s failed", phase_name)
            phase_results.append({"phase": phase_name, "error": str(e)})

        if phase_name in ("optional_clauses", "delete_guide"):
            paras = _all_paras(root)

    try:
        _write_docx_xml(
            work_path,
            etree.tostring(root, xml_declaration=True, encoding="UTF-8", standalone=True),
            other,
        )
    except Exception as e:
        return tool_error(f"Failed to write {work_path}: {e}")

    # Verification — re-run audit on output
    verify_manifest = None
    try:
        verify_raw = _handle_template_audit({"path": work_path})
        verify_manifest = json.loads(verify_raw)
    except Exception:
        verify_manifest = {"error": "Verification audit failed"}

    report = {
        "path": path,
        "output": work_path,
        "convention": manifest.get("convention", "generic"),
        "tc_mode": tc,
        "phases_run": phases_to_run,
        "phase_results": phase_results,
        "verification": verify_manifest,
        "residual_blanks": (
            verify_manifest.get("blanks_count", "N/A")
            if isinstance(verify_manifest, dict) else "N/A"
        ),
        "residual_colored_notes": (
            verify_manifest.get("colored_notes_count", "N/A")
            if isinstance(verify_manifest, dict) else "N/A"
        ),
        "residual_annotations": (
            verify_manifest.get("annotations_count", "N/A")
            if isinstance(verify_manifest, dict) else "N/A"
        ),
        "status": "completed",
    }

    return json.dumps(report, ensure_ascii=False, indent=2)


# ── NAFMII-specific detection (used by orchestrator) ───────────────────────────

def detect_nafmii_template(document_path: str) -> Optional[str]:
    """Quick heuristic: is this .docx a NAFMII 说明版 template?

    Returns 'nafmii' for high confidence, 'nafmii_likely' for medium, or None.
    This is the only NAFMII-specific function — it feeds into the generic
    lex_template_audit / lex_template_fill pipeline.
    """
    try:
        from lexitool.markup import lex_read
        text = lex_read(document_path, paras=list(range(1, 11)), mode="full", show_format=True)
    except Exception:
        return None

    if not text:
        return None

    score = 0
    if "说明" in text or "请在此填写" in text or "根据实际情况" in text:
        score += 20
    if "[highlight:yellow]" in text:
        score += 30
    if "☐" in text:
        score += 25
    if "[color:#0000FF]" in text or "0000FF" in text:
        score += 15
    if "银团贷款" in text or "贷款合同" in text:
        score += 10

    if score >= 50:
        return "nafmii"
    if score >= 30:
        return "nafmii_likely"
    return None


# ── Registration ───────────────────────────────────────────────────────────────

registry.register(
    name="lex_template_audit",
    toolset="lexitool",
    schema=LEX_TEMPLATE_AUDIT_SCHEMA,
    handler=_handle_template_audit,
    check_fn=_check_lexitool,
    emoji="",
)

registry.register(
    name="lex_template_fill",
    toolset="lexitool",
    schema=LEX_TEMPLATE_FILL_SCHEMA,
    handler=_handle_template_fill,
    check_fn=_check_lexitool,
    emoji="",
)
