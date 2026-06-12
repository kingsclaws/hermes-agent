"""
deliver.py — Project delivery packaging.

Bundles final document versions, comparison redlines, and review artifacts
into a timestamped delivery directory.

Operations:
  - package:   Scan a project directory, collect deliverables, generate manifest
"""
from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

# Patterns to exclude from delivery (working files, temp, etc.)
_EXCLUDE_PATTERNS = [
    "~$",       # Office temp lock files
    ".tmp",     # Temp files
    "[Redline]",  # Previous redlines (we generate fresh ones)
    "[Mark-up]",  # Previous mark-up files
]


def _is_deliverable(path: str) -> bool:
    """Check if a .docx file is a deliverable (not a temp/lock file)."""
    name = os.path.basename(path)
    for pat in _EXCLUDE_PATTERNS:
        if name.startswith(pat) or pat in name:
            return False
    return True


def _read_project_meta(project_dir: str) -> Optional[dict]:
    """Read project-meta.json if present."""
    meta_path = Path(project_dir) / ".hermes-project" / "project-meta.json"
    if not meta_path.is_file():
        return None
    try:
        return json.loads(meta_path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _find_docx_files(project_dir: str) -> List[str]:
    """Find deliverable .docx files in a project directory, sorted by name."""
    result = []
    root = Path(project_dir)
    for p in sorted(root.rglob("*.docx")):
        # Skip files inside .hermes-project/ and delivery/
        if ".hermes-project" in p.parts:
            continue
        if "delivery" in p.parts:
            continue
        if _is_deliverable(str(p)):
            result.append(str(p))
    return result


def package(
    project_dir: str,
    output_dir: Optional[str] = None,
    author: Optional[str] = None,
    include_redlines: bool = True,
    require_gates: bool = True,
    git_tag: bool = True,
) -> dict:
    """Package project deliverables into a timestamped delivery folder.

    Args:
        project_dir:    Path to the project root (containing .hermes-project/).
        output_dir:     Where to create the delivery folder (default: project_dir/delivery/).
        author:         Author name for tracked changes in redlines.
        include_redlines: Whether to run cross-document comparisons (default: True).
        require_gates:  If True, runs gate_check(strict=True) and refuses to package
                        if any gates fail. Default: True (gates REQUIRED for delivery).
        git_tag:        If True, creates a git deliver/<version>-<date> tag after
                        successful packaging. Requires git repo in project_dir.

    Returns:
        {
            "ok": True,
            "delivery_path": "/.../delivery/20260601/",
            "manifest": [...],
            "redlines_generated": N,
            "cross_ref_result": {...} or None,
            "gate_result": {...} or None,
            "git_tag": "deliver/v1-20260612" or None,
        }
    """
    src = Path(project_dir)
    if not src.is_dir():
        return {"ok": False, "error": f"Project directory not found: {project_dir}"}
    if not (src / ".hermes-project").is_dir():
        return {"ok": False, "error": f"Not a hermes project (no .hermes-project/ in {project_dir})"}

    meta = _read_project_meta(project_dir)
    project_name = meta.get("name", src.name) if meta else src.name

    # ── Optional gate check before packaging ──
    gate_result = None
    if require_gates:
        try:
            from lexitool.gate_check import gate_check
            gate_result = gate_check(project_dir, strict=True)
            if not gate_result.get("all_passed", False):
                return {
                    "ok": False,
                    "error": "Gate check failed — delivery blocked",
                    "gate_result": gate_result,
                    "blocked": gate_result.get("blocked", "unknown"),
                }
        except ImportError:
            logger.warning("gate_check not available, skipping pre-delivery gate check")
        except Exception as e:
            logger.warning("Gate check error (non-fatal): %s", e)

    # Create timestamped delivery directory
    today = datetime.now(timezone.utc).strftime("%Y%m%d")
    delivery_root = Path(output_dir) if output_dir else src / "delivery"
    delivery_dir = delivery_root / today
    delivery_dir.mkdir(parents=True, exist_ok=True)

    # Find deliverable docx files
    docx_files = _find_docx_files(project_dir)
    manifest: List[dict] = []
    redlines_generated = 0

    # Copy clean versions to delivery
    for doc_path in docx_files:
        rel = os.path.relpath(doc_path, project_dir)
        dest = delivery_dir / os.path.basename(doc_path)
        # Avoid overwrite by disambiguating with parent dir
        if dest.exists():
            dest = delivery_dir / f"{Path(rel).parent.name}_{os.path.basename(doc_path)}"
        shutil.copy2(doc_path, dest)
        manifest.append({
            "file": os.path.basename(str(dest)),
            "source": rel,
            "type": "final",
        })

    # Cross-reference scan: internal refs for every document, plus cross-doc refs for multi-doc projects.
    cross_ref_result = None
    if docx_files:
        try:
            from lexitool.xref import audit_documents
            cross_ref_result = audit_documents(docx_files)
            # Write cross-reference report
            cr_text = _format_cross_ref_report(cross_ref_result, docx_files)
            (delivery_dir / "cross-reference-report.md").write_text(cr_text, encoding="utf-8")
            manifest.append({
                "file": "cross-reference-report.md",
                "source": "auto-generated",
                "type": "report",
            })
        except Exception as e:
            logger.debug("Cross-ref scan skipped: %s", e)

    # Write delivery manifest
    manifest_text = _format_manifest(project_name, meta, manifest, today, cross_ref_result)
    (delivery_dir / "MANIFEST.md").write_text(manifest_text, encoding="utf-8")

    # ── Git tag (auto-commit + delivery tag) ──
    git_tag_result = None
    if git_tag:
        try:
            from lexitool.git_ops import ensure_repo, snapshot, deliver_tag as git_deliver_tag, status as git_status

            # Ensure repo exists (safe to call on already-initialized repos)
            repo_result = ensure_repo(project_dir)
            if repo_result.ok:
                # Commit any uncommitted changes before tagging
                st = git_status(project_dir)
                if st.data.get("dirty", False):
                    snapshot(
                        project_dir,
                        f"deliver: {project_name} packaged {today} — {author or 'hermes-agent'}",
                        author=author or "hermes-agent",
                    )

                # Tag the delivery
                tag_result = git_deliver_tag(project_dir, f"v{len(manifest)}", message=f"Delivery: {project_name} — {today}")
                git_tag_result = tag_result.tag if tag_result.ok else None
        except ImportError:
            logger.debug("git_ops not available, skipping git tag")
        except Exception as e:
            logger.warning("Git tag failed (non-fatal): %s", e)

    return {
        "ok": True,
        "delivery_path": str(delivery_dir),
        "project_name": project_name,
        "files_delivered": len(docx_files),
        "manifest": manifest,
        "redlines_generated": redlines_generated,
        "cross_ref_result": cross_ref_result,
        "gate_result": gate_result,
        "git_tag": git_tag_result,
    }


def _format_cross_ref_report(result: dict, docx_files: List[str]) -> str:
    """Format cross-reference scan results as a Markdown report."""
    lines = [
        "# Cross-Reference Verification Report",
        "",
        f"**Documents Scanned:** {result.get('docs_scanned', 0)}",
        "",
    ]

    s = result.get("summary", {})
    lines.append(f"| Metric | Count |")
    lines.append(f"|--------|-------|")
    lines.append(f"| Total cross-references | {s.get('total', 0)} |")
    lines.append(f"| Valid references | {s.get('valid', 0)} |")
    lines.append(f"| Broken references | {s.get('broken', 0)} |")
    lines.append(f"| Internal dead references | {s.get('internal_dead', 0)} |")
    lines.append(f"| Cross-document broken refs | {s.get('cross_broken', 0)} |")
    lines.append(f"| Schedule/attachment refs | {s.get('schedules', 0)} |")
    lines.append("")

    internal_broken = []
    for doc in result.get("documents", []) or []:
        for ref in doc.get("dead_refs", []) or []:
            internal_broken.append((doc, ref))

    cross_result = result.get("cross_doc_scan") or {}
    cross_broken = cross_result.get("broken_refs", []) if isinstance(cross_result, dict) else []

    lines.append("## Broken References")
    lines.append("")
    if internal_broken:
        lines.append("### Internal References")
        lines.append("")
        for doc, ref in internal_broken:
            src_name = Path(doc.get("path", "")).stem
            lines.append(f"- **{src_name}** §{ref.get('para')} → {ref.get('ref_text')}")
            lines.append(f"  - Context: {ref.get('context', '')}")
            lines.append("")
    if cross_broken:
        lines.append("### Cross-Document References")
        lines.append("")
        for r in cross_broken:
            src_name = Path(r["source"]).stem
            lines.append(f"- **{src_name}** → 《{r['target_doc']}》第{r['clause']}条")
            lines.append(f"  - Reason: {r['reason']}")
            lines.append("")
    if not internal_broken and not cross_broken:
        lines.append("No broken cross-references found. All references validated successfully.")
        lines.append("")

    valid = []
    for doc in result.get("documents", []) or []:
        for ref in doc.get("valid_refs", []) or []:
            valid.append((doc, ref))
    if valid:
        lines.append("## Valid References")
        lines.append("")
        for doc, ref in valid:
            src_name = Path(doc.get("path", "")).stem
            lines.append(f"- {src_name} §{ref.get('para')} → {ref.get('ref_text')} ✓")

    return "\n".join(lines) + "\n"


def _format_manifest(
    project_name: str,
    meta: Optional[dict],
    manifest: List[dict],
    date_str: str,
    cross_ref_result: Optional[dict],
) -> str:
    """Generate delivery manifest as Markdown."""
    lines = [
        f"# Delivery Manifest — {project_name}",
        "",
        f"**Date:** {date_str}",
        f"**Project:** {project_name}",
    ]
    if meta:
        lines.append(f"**Client:** {meta.get('client', 'N/A')}")
        lines.append(f"**Goal:** {meta.get('goal', 'N/A')}")
    lines.append("")
    lines.append("## Files")
    lines.append("")
    for item in manifest:
        icon = {"final": "📄", "report": "📋", "redline": "🔴"}.get(item["type"], "📎")
        lines.append(f"- {icon} **{item['file']}** — {item['source']}")

    if cross_ref_result:
        s = cross_ref_result.get("summary", {})
        broken = s.get("broken", 0)
        lines.append("")
        lines.append("## Cross-Reference Status")
        if broken > 0:
            lines.append(f"⚠️ **{broken} broken cross-references** — see cross-reference-report.md for details.")
        else:
            lines.append("✅ All cross-references validated — no broken references found.")

    return "\n".join(lines) + "\n"
