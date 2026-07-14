#!/usr/bin/env python3
"""
Delivery Spec Loader — project-level DELIVERY_SPEC.yaml gate validator.

Loads and validates delivery gates for legal document projects.
Used by kanban_toolset handoff/approve handlers to enforce non-bypassable
delivery quality gates before phase transitions.

Gate names: plan, draft, review, finalize
Each gate has entry criteria (must be met before entering) and exit criteria
(must be met before leaving / handing off).
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

# ── Default delivery spec ──────────────────────────────────────────────────────

DEFAULT_SPEC: dict[str, Any] = {
    "project": "",
    "version": 1,
    "deliverables": [],
    "gates": {
        "plan": {
            "entry": [],
            "exit": ["review_plan", "convention_profile", "transaction_structure_artifact"],
        },
        "draft": {
            "entry": ["review_plan"],
            "exit": ["tc_mode", "git_branch"],
        },
        "review": {
            "entry": ["tc_mode"],
            "exit": [
                "legal_scorecard_min_80",
                "format_lint_pass",
                "no_unresolved_comments",
            ],
        },
        "finalize": {
            "entry": ["legal_scorecard_min_90"],
            "exit": ["edit_verification", "handoff_envelope", "git_tag"],
        },
    },
}


# ── YAML loader ────────────────────────────────────────────────────────────────

def _try_load_yaml(file_path: str) -> dict | None:
    """Load a YAML file. Returns None if YAML unavailable or file invalid."""
    try:
        import yaml as _yaml
    except ImportError:
        return None
    path = Path(file_path)
    if not path.is_file():
        return None
    try:
        raw = path.read_text(encoding="utf-8")
        data = _yaml.safe_load(raw)
        return data if isinstance(data, dict) else None
    except Exception:
        return None


def load_delivery_spec(project_path: str) -> dict:
    """Load DELIVERY_SPEC.yaml from a project. Returns default spec if not found.

    Args:
        project_path: Absolute path to the project root.

    Returns:
        A dict with at least 'version', 'deliverables', and 'gates' keys.
        Falls back to DEFAULT_SPEC when no file exists.
    """
    spec_path = Path(project_path) / ".hermes-project" / "DELIVERY_SPEC.yaml"
    loaded = _try_load_yaml(str(spec_path))
    if loaded:
        # Merge with defaults for any missing gates
        merged = dict(DEFAULT_SPEC)
        merged.update(loaded)
        merged.setdefault("gates", {})
        # Merge per-gate exit/entry
        for gate_name, gate_def in DEFAULT_SPEC.get("gates", {}).items():
            if gate_name not in merged["gates"]:
                merged["gates"][gate_name] = dict(gate_def)
            else:
                for key in ("entry", "exit"):
                    merged["gates"][gate_name].setdefault(key, gate_def.get(key, []))
        return merged
    return dict(DEFAULT_SPEC)


# ── JSON helpers ───────────────────────────────────────────────────────────────

def _read_hermes_json(project_path: str, filename: str, default: dict | None = None) -> dict:
    """Read a JSON file from .hermes-project/ inside project_path."""
    path = Path(project_path) / ".hermes-project" / filename
    if not path.is_file():
        return (default or {}).copy() if default else {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else (default or {}).copy()
    except Exception:
        return (default or {}).copy() if default else {}


# ── Individual check runners ───────────────────────────────────────────────────

def _check_review_plan(project_path: str) -> tuple[bool, str]:
    """Check for non-archived entries in legal-review-plans.json."""
    data = _read_hermes_json(project_path, "legal-review-plans.json", {"plans": []})
    plans = data.get("plans", [])
    active = [p for p in plans if p.get("status") not in ("completed", "archived")]
    if active:
        return True, f"Found {len(active)} active review plan(s)."
    return False, "No active review plan found. Please create one with `legal_review_plan`."


def _check_convention_profile(project_path: str) -> tuple[bool, str]:
    """Check for convention profiles in convention-profiles.json."""
    data = _read_hermes_json(project_path, "convention-profiles.json", {"profiles": []})
    profiles = data.get("profiles", [])
    if profiles:
        return True, f"Found {len(profiles)} convention profile(s)."
    return False, "No convention profile found. Please run `lex_convention_profile` before proceeding."


def _check_tc_mode(project_path: str) -> tuple[bool, str]:
    """Check if Track Changes mode has been tracked in edit-verification-records."""
    data = _read_hermes_json(project_path, "edit-verification-records.json", {"records": []})
    records = data.get("records", [])
    tc_records = [r for r in records if r.get("tc_mode") or r.get("guards", {}).get("tc_mode")]
    if tc_records:
        return True, f"Track Changes mode confirmed in {len(tc_records)} record(s)."
    # Also check if any record has tc_mode in its structure
    for r in records:
        guards = r.get("guards", {})
        if isinstance(guards, dict) and guards.get("tc_mode"):
            return True, "TC mode confirmed."
    return False, (
        "All document edits must be in Track Changes mode. "
        "Please re-edit with TC mode enabled (use lex_edit with tc=true)."
    )


def _check_git_branch(project_path: str) -> tuple[bool, str]:
    """Check if .git exists and current branch starts with 'rev/'."""
    git_dir = Path(project_path) / ".git"
    if not git_dir.exists():
        return False, "Project is not a git repository. Please run `git init` first."

    try:
        # Read HEAD to determine current branch
        head_path = git_dir / "HEAD"
        if head_path.is_file():
            head_content = head_path.read_text(encoding="utf-8").strip()
            # Format: ref: refs/heads/branch-name
            if head_content.startswith("ref: refs/heads/"):
                branch = head_content[len("ref: refs/heads/"):]
                if branch.startswith("rev/"):
                    return True, f"Current branch '{branch}' starts with rev/."
                else:
                    return False, (
                        f"Current branch '{branch}' does not start with 'rev/'. "
                        f"Please create a revision branch: git checkout -b rev/<name>"
                    )
            else:
                # Detached HEAD
                return False, "Detached HEAD. Please create a rev/ branch: git checkout -b rev/<name>"
    except Exception as exc:
        return False, f"Unable to check git branch: {exc}"

    return False, "Unable to determine current git branch."


def _check_legal_scorecard_min(
    project_path: str, min_score: int
) -> tuple[bool, str]:
    """Run legal_scorecard() and check pass rate against minimum."""
    try:
        from hermes_cli.project_commands import legal_scorecard

        result = legal_scorecard(project_path, strict=True)
        failures = result.get("failures", [])
        checks = result.get("checks", [])
        total_checks = len(checks)
        passed_checks = sum(1 for c in checks if c.get("passed"))
        score_pct = int((passed_checks / total_checks) * 100) if total_checks > 0 else 0

        if score_pct >= min_score and not failures:
            return True, f"Scorecard passed ({score_pct}% >= {min_score}%)."
        else:
            failure_details = "; ".join(
                f.get("check", "?") + ": " + f.get("message", "")
                for f in failures[:5]
            )
            return False, (
                f"Scorecard {score_pct}% < {min_score}%. "
                f"Failures: {failure_details}"
            )
    except ImportError:
        return False, "Unable to import legal_scorecard from project_commands."
    except Exception as exc:
        return False, f"Scorecard check failed: {exc}"


def _check_format_lint_pass(project_path: str) -> tuple[bool, str]:
    """Check if format lint rules have passed (via doctor check)."""
    records = _read_hermes_json(project_path, "edit-verification-records.json", {"records": []}).get(
        "records", []
    )
    lint_records = [r for r in records if r.get("lint") or r.get("doctor")]
    passed = [r for r in lint_records if r.get("status") in ("passed", "clear")]
    if passed:
        return True, f"Format lint passed ({len(passed)} record(s))."
    # Fallback: check if doctor ran with no failures
    for r in records:
        if r.get("doctor_result") and r["doctor_result"].get("all_passed"):
            return True, "Format doctor check passed."
    return False, (
        "Format lint not passed. "
        "Please run `lex_docx_doctor action=check` and fix all format issues."
    )


def _check_no_unresolved_comments(project_path: str) -> tuple[bool, str]:
    """Check that there are no unresolved review comments."""
    state = _read_hermes_json(project_path, "project-state.json", {})
    # Check state for any unresolved items
    findings = state.get("key_findings", [])
    unresolved = [
        f for f in findings
        if isinstance(f, dict) and f.get("status") not in ("resolved", "closed")
    ]
    if not unresolved:
        return True, "No unresolved comments or findings."
    return False, (
        f"There are {len(unresolved)} unresolved finding(s). "
        f"Please resolve or close them before proceeding."
    )


def _check_edit_verification(project_path: str) -> tuple[bool, str]:
    """Check that passed edit verification records exist."""
    data = _read_hermes_json(project_path, "edit-verification-records.json", {"records": []})
    records = data.get("records", [])
    passed = [r for r in records if r.get("status") in ("passed", "not_applicable")]
    failed = [r for r in records if r.get("status") in ("failed", "partial")]
    if passed:
        msg = f"Found {len(passed)} passed verification record(s)."
        if failed:
            msg += f" Warning: {len(failed)} failed record(s) also exist."
        return True, msg
    if failed:
        return False, f"{len(failed)} verification record(s) failed. Please fix and re-verify."
    return False, "No edit verification records found. Please run edit_verification_record after edits."


def _check_handoff_envelope(project_path: str) -> tuple[bool, str]:
    """Check for valid handoff records in harness-runs."""
    harness_dir = Path(project_path) / ".hermes-project" / "harness-runs"
    if not harness_dir.is_dir():
        return False, "No harness-runs directory found. Please record handoffs via legal_handoff_record."

    # Check for any valid handoff envelope
    valid_count = 0
    invalid_count = 0
    for run_dir in sorted(harness_dir.iterdir()):
        if not run_dir.is_dir() or not run_dir.name.startswith("run_"):
            continue
        nodes_dir = run_dir / "nodes"
        if not nodes_dir.is_dir():
            continue
        for node_file in nodes_dir.glob("*.json"):
            try:
                node = json.loads(node_file.read_text(encoding="utf-8"))
                if node.get("valid"):
                    valid_count += 1
                else:
                    invalid_count += 1
            except Exception:
                invalid_count += 1

    if valid_count > 0:
        msg = f"Found {valid_count} valid handoff envelope(s)."
        if invalid_count > 0:
            msg += f" ({invalid_count} invalid)."
        return True, msg
    if invalid_count > 0:
        return False, f"{invalid_count} handoff envelope(s) are invalid. Please fix and re-record."
    return False, "No handoff envelopes found. Please record via legal_handoff_record."


def _check_git_tag(project_path: str) -> tuple[bool, str]:
    """Check if git tags exist on the project."""
    git_dir = Path(project_path) / ".git"
    if not git_dir.exists():
        return False, "Project is not a git repository. Cannot create git tags."

    try:
        import subprocess
        result = subprocess.run(
            ["git", "tag", "-l"],
            cwd=project_path,
            capture_output=True,
            text=True,
            timeout=10,
        )
        tags = [t.strip() for t in result.stdout.strip().split("\n") if t.strip()]
        if tags:
            return True, f"Found {len(tags)} git tag(s): {', '.join(tags[:5])}."
        else:
            return False, (
                "No git tags found. "
                "Please tag the delivery: git tag -a deliver/<version>-<date> -m '...'"
            )
    except Exception as exc:
        return False, f"Unable to check git tags: {exc}"


def _check_transaction_structure(project_path: str) -> tuple[bool, str]:
    """Verify 交易结构与术语表.md exists with tier-appropriate sections + user confirm."""
    meta = _read_hermes_json(project_path, "project-meta.json", {})
    tier = (meta.get("structure_tier") or "").strip()
    confirmed_by = (meta.get("structure_confirmed_by") or "").strip()
    confirmed_at = (meta.get("structure_confirmed_at") or "").strip()

    if not tier:
        return False, (
            "未找到交易结构 tier 声明。"
            "请与 Coordinator 完成 交易结构与术语表 访谈，Coordinator 将创建此文件。"
        )
    if tier not in ("minimal", "full"):
        return False, (
            f"未知的 structure_tier '{tier}'，必须为 'minimal' 或 'full'。"
        )
    if not confirmed_by:
        return False, (
            "交易结构与术语表 尚未经用户确认（缺少 structure_confirmed_by）。"
            "请与 Coordinator 确认后由 Coordinator 记录确认信息。"
        )

    # Required H2 headings per tier
    _CORE = ["① 术语表", "② 签署主体"]
    _FULL_EXTRA = ["③ 关键金额与费率表", "④ 编号/格式约定", "⑤ 交易文件清单与跨文件引用"]
    required = _CORE + _FULL_EXTRA if tier == "full" else _CORE

    md_path = Path(project_path) / "交易结构与术语表.md"
    if not md_path.is_file():
        return False, (
            f"缺少 交易结构与术语表.md。"
            f"请与 Coordinator 完成交易结构访谈后由 Coordinator 创建。"
        )

    try:
        md_text = md_path.read_text(encoding="utf-8")
    except Exception as exc:
        return False, f"无法读取 交易结构与术语表.md: {exc}"

    # Parse H2 headings (## ① 术语表)
    found_headings: set[str] = set()
    for line in md_text.splitlines():
        stripped = line.strip()
        if stripped.startswith("## "):
            found_headings.add(stripped[3:].strip())

    missing = [h for h in required if h not in found_headings]
    if missing:
        return False, (
            f"交易结构与术语表 缺少以下必需章节（tier={tier}）: {', '.join(missing)}。"
            f"请让 Coordinator 补充缺失章节。"
        )

    return True, (
        f"交易结构与术语表 已确认（tier={tier}, "
        f"confirmed by {confirmed_by} at {confirmed_at}）。"
    )


# ── Check registry ─────────────────────────────────────────────────────────────

# Maps check names to (function, is_min_score_check)
CHECK_REGISTRY: dict[str, Any] = {
    "review_plan":           (_check_review_plan, False),
    "convention_profile":    (_check_convention_profile, False),
    "tc_mode":               (_check_tc_mode, False),
    "git_branch":            (_check_git_branch, False),
    "format_lint_pass":      (_check_format_lint_pass, False),
    "no_unresolved_comments": (_check_no_unresolved_comments, False),
    "edit_verification":     (_check_edit_verification, False),
    "handoff_envelope":      (_check_handoff_envelope, False),
    "git_tag":               (_check_git_tag, False),
    # Scorecard checks are special: they include the threshold in the name
    "transaction_structure_artifact": (_check_transaction_structure, False),
    "legal_scorecard_min_80": (_check_legal_scorecard_min, True),
    "legal_scorecard_min_90": (_check_legal_scorecard_min, True),
}

# Fix hints for common failures
FIX_HINTS: dict[str, str] = {
    "review_plan": (
        "请先调用 `legal_review_plan` 创建审阅计划，然后再移交。"
    ),
    "convention_profile": (
        "请先调用 `lex_convention_profile` 分析文档约定，然后再移交。"
    ),
    "tc_mode": (
        "所有文档修改必须在 Track Changes 模式下进行。"
        "请使用 lex_edit 的 TC 模式重新编辑。"
    ),
    "git_branch": (
        "修改必须在独立 git 分支上进行。"
        "请先创建 rev/ 分支: git checkout -b rev/<文档名>/<轮次>"
    ),
    "legal_scorecard_min_80": (
        "合法审查得分未达到 80%。请先修复 scorecard 中的 failures。"
        "运行 legal_scorecard() 查看详情。"
    ),
    "legal_scorecard_min_90": (
        "定稿前合法审查得分未达到 90%。请先修复 scorecard 中的 failures。"
        "运行 legal_scorecard() 查看详情。"
    ),
    "format_lint_pass": (
        "格式 lint 未通过。请运行 `lex_docx_doctor action=fix` 修复格式问题。"
    ),
    "no_unresolved_comments": (
        "存在未解决的审阅意见。请先解决或关闭所有意见。"
    ),
    "edit_verification": (
        "缺少编辑验证记录。请调用 `edit_verification_record` 记录验证结果。"
    ),
    "handoff_envelope": (
        "缺少有效的 handoff envelope。请调用 `legal_handoff_record` 记录移交信息。"
    ),
    "git_tag": (
        "缺少 git tag。请为交付打标签: "
        "git tag -a deliver/<version>-<date> -m '交付说明'"
    ),
    "transaction_structure_artifact": (
        "缺少 交易结构与术语表 或尚未经用户确认。"
        "请与 Coordinator 完成项目交易结构访谈，Coordinator 将创建此文件并记录确认。"
    ),
}


# ── Core validation APIs ───────────────────────────────────────────────────────

def _run_checks(
    project_path: str, criteria: list[str]
) -> list[dict[str, str]]:
    """Run a list of criteria checks. Returns list of failure dicts (empty = all pass)."""
    failures: list[dict[str, str]] = []
    for criterion in criteria:
        # Handle scorecard min checks
        if criterion.startswith("legal_scorecard_min_"):
            try:
                min_val = int(criterion.split("_")[-1])
            except ValueError:
                min_val = 80
            check_fn = _check_legal_scorecard_min
            passed, message = check_fn(project_path, min_val)
        else:
            entry = CHECK_REGISTRY.get(criterion)
            if entry is None:
                # Unknown criterion — skip with warning
                failures.append({
                    "check": criterion,
                    "message": f"Unknown criterion '{criterion}' — skipped.",
                    "fix": "Please check the DELIVERY_SPEC.yaml configuration.",
                })
                continue
            check_fn, _ = entry
            passed, message = check_fn(project_path)

        if not passed:
            fix_hint = FIX_HINTS.get(criterion, "请满足此条件后重试。")
            failures.append({
                "check": criterion,
                "message": message,
                "fix": fix_hint,
            })

    return failures


def validate_gate_exit(project_path: str, gate: str) -> list[dict[str, str]]:
    """Check exit criteria for a delivery phase. Returns list of failure dicts.

    An empty list means all exit criteria passed.

    Args:
        project_path: Absolute path to the project root.
        gate: Gate name (plan, draft, review, finalize).

    Returns:
        List of failure dicts with keys: check, message, fix.
    """
    spec = load_delivery_spec(project_path)
    gates = spec.get("gates", {})
    gate_def = gates.get(gate, {})
    exit_criteria: list[str] = gate_def.get("exit", [])
    if not exit_criteria:
        return []
    return _run_checks(project_path, exit_criteria)


def validate_gate_entry(project_path: str, gate: str) -> list[dict[str, str]]:
    """Check entry criteria for a delivery phase. Returns list of failure dicts.

    An empty list means all entry criteria passed.

    Args:
        project_path: Absolute path to the project root.
        gate: Gate name (plan, draft, review, finalize).

    Returns:
        List of failure dicts with keys: check, message, fix.
    """
    spec = load_delivery_spec(project_path)
    gates = spec.get("gates", {})
    gate_def = gates.get(gate, {})
    entry_criteria: list[str] = gate_def.get("entry", [])
    if not entry_criteria:
        return []
    return _run_checks(project_path, entry_criteria)
