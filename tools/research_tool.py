#!/usr/bin/env python3
"""
Deep Research Tool — Orchestration layer for multi-source legal research.

Provides code-driven research workflow (not just prompt tricks):
- research_plan: decompose question into subquestions + source strategy
- research_run: create durable research run with status tracking
- research_collect: record a source in the source ledger
- research_synthesize: consolidate findings with confidence levels
- research_report: generate md/html/docx deliverables
- research_update_facts: write high-confidence findings to project facts

Evidence is persisted so research results survive session boundaries.
"""

from __future__ import annotations

import json
import os
import sqlite3
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

# ── Schema ──────────────────────────────────────────────────────────────────

RESEARCH_DB_SCHEMA = """
CREATE TABLE IF NOT EXISTS research_runs (
    id TEXT PRIMARY KEY,
    project_id TEXT,
    session_id TEXT,
    title TEXT NOT NULL,
    status TEXT DEFAULT 'intake',
    question TEXT NOT NULL,
    jurisdiction TEXT,
    date_scope TEXT,
    started_at REAL,
    finished_at REAL,
    report_paths TEXT,
    blocking_reason TEXT
);

CREATE TABLE IF NOT EXISTS research_questions (
    id TEXT PRIMARY KEY,
    research_run_id TEXT NOT NULL,
    parent_question_id TEXT,
    question_text TEXT NOT NULL,
    priority INTEGER DEFAULT 0,
    status TEXT DEFAULT 'planned',
    assignee TEXT,
    source_strategy TEXT,
    coverage_status TEXT,
    FOREIGN KEY (research_run_id) REFERENCES research_runs(id)
);

CREATE TABLE IF NOT EXISTS research_sources (
    id TEXT PRIMARY KEY,
    research_run_id TEXT NOT NULL,
    question_id TEXT,
    source_type TEXT,
    title TEXT,
    url_or_path TEXT,
    authority_tier TEXT DEFAULT 'C',
    retrieved_at REAL,
    read_method TEXT,
    read_coverage REAL,
    summary TEXT,
    verbatim_excerpt TEXT,
    confidence REAL DEFAULT 0.5,
    FOREIGN KEY (research_run_id) REFERENCES research_runs(id)
);

CREATE TABLE IF NOT EXISTS research_findings (
    id TEXT PRIMARY KEY,
    research_run_id TEXT NOT NULL,
    question_id TEXT,
    finding_text TEXT NOT NULL,
    supporting_sources TEXT,
    confidence REAL DEFAULT 0.5,
    status TEXT DEFAULT 'tentative',
    open_issue TEXT,
    fact_candidate INTEGER DEFAULT 0,
    FOREIGN KEY (research_run_id) REFERENCES research_runs(id)
);
"""

# ── DB helpers ──────────────────────────────────────────────────────────────

def _research_db_path(project_dir: str = "") -> Path:
    if project_dir:
        return Path(project_dir) / ".hermes-project" / "research.db"
    from hermes_constants import get_hermes_home
    return get_hermes_home() / "research.db"


def _ensure_db(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    conn.executescript(RESEARCH_DB_SCHEMA)
    return conn


def _uid(prefix: str = "") -> str:
    return f"{prefix}{uuid.uuid4().hex[:12]}"


def _now() -> float:
    return time.time()


# ── research_plan ───────────────────────────────────────────────────────────

RESEARCH_PLAN_SCHEMA = {
    "name": "research_plan",
    "description": (
        "Decompose a research question into subquestions with source strategy. "
        "Use before research_run to plan the research approach."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "question": {"type": "string", "description": "The main research question."},
            "jurisdiction": {"type": "string", "description": "Legal jurisdiction (e.g. PRC, HK, US)."},
            "date_scope": {"type": "string", "description": "Date range for sources (e.g. 'current law only', '2020-present')."},
            "depth": {"type": "string", "enum": ["quick", "standard", "exhaustive"], "description": "Research depth. Default standard."},
            "subquestions": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "question": {"type": "string"},
                        "source_types": {"type": "array", "items": {"type": "string"}},
                        "priority": {"type": "integer"},
                    },
                    "required": ["question"],
                },
                "description": "Pre-defined subquestions (optional, system will decompose if empty).",
            },
        },
        "required": ["question"],
    },
}


def research_plan_handler(args: dict, **kwargs) -> str:
    question = str(args.get("question") or "").strip()
    if not question:
        return json.dumps({"success": False, "error": "question is required"})

    jurisdiction = str(args.get("jurisdiction") or "").strip()
    date_scope = str(args.get("date_scope") or "").strip()
    depth = str(args.get("depth") or "standard").strip()
    subquestions = args.get("subquestions") or []

    plan = {
        "question": question,
        "jurisdiction": jurisdiction,
        "date_scope": date_scope,
        "depth": depth,
        "subquestions": subquestions if subquestions else _auto_decompose(question, jurisdiction),
        "source_strategy": _source_strategy(depth),
        "stop_conditions": _stop_conditions(depth),
        "deliverable_format": ["md", "html", "docx"],
    }

    return json.dumps({"success": True, "plan": plan}, ensure_ascii=False)


def _auto_decompose(question: str, jurisdiction: str) -> list:
    """Auto-decompose a question into subquestions (heuristic)."""
    sq = []
    sq.append({"question": f"What is the current legal/regulatory framework for: {question}?", "source_types": ["web", "statute"], "priority": 1})
    sq.append({"question": f"What are the key cases or precedents?", "source_types": ["web", "case_law"], "priority": 2})
    if jurisdiction:
        sq.append({"question": f"Are there jurisdiction-specific rules in {jurisdiction}?", "source_types": ["web", "statute"], "priority": 1})
    sq.append({"question": f"What are practical implications and recommendations?", "source_types": ["web", "commentary"], "priority": 3})
    return sq


def _source_strategy(depth: str) -> dict:
    if depth == "quick":
        return {"max_sources": 5, "prefer": ["web"], "require_tier_a": False}
    if depth == "exhaustive":
        return {"max_sources": 50, "prefer": ["statute", "case_law", "commentary", "project_docs"], "require_tier_a": True}
    return {"max_sources": 20, "prefer": ["statute", "web", "commentary"], "require_tier_a": False}


def _stop_conditions(depth: str) -> dict:
    if depth == "quick":
        return {"min_sources": 2, "min_confidence": 0.5}
    if depth == "exhaustive":
        return {"min_sources": 10, "min_confidence": 0.8}
    return {"min_sources": 5, "min_confidence": 0.6}


# ── research_run ────────────────────────────────────────────────────────────

RESEARCH_RUN_SCHEMA = {
    "name": "research_run",
    "description": (
        "Create or update a durable research run. Tracks status, subquestions, "
        "sources, and findings across sessions."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "action": {"type": "string", "enum": ["create", "update", "get", "list"], "description": "Action to perform."},
            "run_id": {"type": "string", "description": "Research run ID (for update/get)."},
            "title": {"type": "string", "description": "Research title."},
            "question": {"type": "string", "description": "Main research question."},
            "jurisdiction": {"type": "string"},
            "date_scope": {"type": "string"},
            "status": {"type": "string", "enum": ["intake", "planned", "collecting", "synthesizing", "reviewing", "delivering", "done", "blocked"]},
            "project_dir": {"type": "string", "description": "Project directory for scoped research."},
        },
        "required": ["action"],
    },
}


def research_run_handler(args: dict, **kwargs) -> str:
    action = str(args.get("action") or "create").strip()
    project_dir = str(args.get("project_dir") or "").strip()
    db_path = _research_db_path(project_dir)
    conn = _ensure_db(db_path)

    try:
        if action == "create":
            return _run_create(conn, args)
        elif action == "update":
            return _run_update(conn, args)
        elif action == "get":
            return _run_get(conn, args)
        elif action == "list":
            return _run_list(conn, args)
        else:
            return json.dumps({"success": False, "error": f"Unknown action: {action}"})
    finally:
        conn.close()


def _run_create(conn, args):
    run_id = _uid("res_")
    session_id = os.environ.get("HERMES_SESSION_ID", "")
    now = _now()
    conn.execute(
        "INSERT INTO research_runs (id, project_id, session_id, title, status, question, jurisdiction, date_scope, started_at) "
        "VALUES (?, ?, ?, ?, 'intake', ?, ?, ?, ?)",
        (run_id, args.get("project_id", ""), session_id,
         args.get("title") or args.get("question", "")[:80],
         args.get("question", ""), args.get("jurisdiction", ""),
         args.get("date_scope", ""), now),
    )
    conn.commit()
    return json.dumps({"success": True, "run_id": run_id, "status": "intake"}, ensure_ascii=False)


def _run_update(conn, args):
    run_id = args.get("run_id", "")
    if not run_id:
        return json.dumps({"success": False, "error": "run_id required"})
    updates = []
    params = []
    for field in ["title", "status", "jurisdiction", "date_scope", "blocking_reason", "report_paths"]:
        val = args.get(field)
        if val is not None:
            updates.append(f"{field} = ?")
            params.append(str(val) if field != "report_paths" else json.dumps(val))
    if args.get("status") == "done":
        updates.append("finished_at = ?")
        params.append(_now())
    if not updates:
        return json.dumps({"success": False, "error": "no fields to update"})
    params.append(run_id)
    conn.execute(f"UPDATE research_runs SET {', '.join(updates)} WHERE id = ?", params)
    conn.commit()
    return json.dumps({"success": True, "run_id": run_id}, ensure_ascii=False)


def _run_get(conn, args):
    run_id = args.get("run_id", "")
    row = conn.execute("SELECT * FROM research_runs WHERE id = ?", (run_id,)).fetchone()
    if not row:
        return json.dumps({"success": False, "error": f"Run not found: {run_id}"})
    cols = [d[0] for d in conn.execute("PRAGMA table_info(research_runs)").fetchall()]
    run = dict(zip(cols, row))
    # Get subquestions
    qs = conn.execute("SELECT * FROM research_questions WHERE research_run_id = ?", (run_id,)).fetchall()
    q_cols = [d[0] for d in conn.execute("PRAGMA table_info(research_questions)").fetchall()]
    run["questions"] = [dict(zip(q_cols, q)) for q in qs]
    # Get source count
    sc = conn.execute("SELECT COUNT(*) FROM research_sources WHERE research_run_id = ?", (run_id,)).fetchone()
    run["source_count"] = sc[0] if sc else 0
    # Get findings
    fs = conn.execute("SELECT * FROM research_findings WHERE research_run_id = ?", (run_id,)).fetchall()
    f_cols = [d[0] for d in conn.execute("PRAGMA table_info(research_findings)").fetchall()]
    run["findings"] = [dict(zip(f_cols, f)) for f in fs]
    return json.dumps({"success": True, "run": run}, ensure_ascii=False)


def _run_list(conn, args):
    rows = conn.execute("SELECT id, title, status, question, started_at FROM research_runs ORDER BY started_at DESC LIMIT 20").fetchall()
    runs = [{"id": r[0], "title": r[1], "status": r[2], "question": r[3], "started_at": r[4]} for r in rows]
    return json.dumps({"success": True, "runs": runs, "count": len(runs)}, ensure_ascii=False)


# ── research_collect ────────────────────────────────────────────────────────

RESEARCH_COLLECT_SCHEMA = {
    "name": "research_collect",
    "description": (
        "Record a source in the research source ledger. Every source used in "
        "a conclusion must be recorded here first."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "run_id": {"type": "string", "description": "Research run ID."},
            "question_id": {"type": "string", "description": "Question this source addresses."},
            "source_type": {"type": "string", "enum": ["web", "statute", "case_law", "commentary", "project_doc", "ocr", "prior_fact"], "description": "Type of source."},
            "title": {"type": "string", "description": "Source title."},
            "url_or_path": {"type": "string", "description": "URL or file path."},
            "authority_tier": {"type": "string", "enum": ["A", "B", "C", "D"], "description": "Authority tier. A=statutes/official, B=project docs, C=reputable analysis, D=low-trust."},
            "summary": {"type": "string", "description": "Brief summary of relevant content."},
            "verbatim_excerpt": {"type": "string", "description": "Direct quote from source."},
            "confidence": {"type": "number", "description": "Confidence in this source (0-1). Default 0.5."},
            "project_dir": {"type": "string"},
        },
        "required": ["run_id", "source_type", "title"],
    },
}


def research_collect_handler(args: dict, **kwargs) -> str:
    run_id = args.get("run_id", "")
    if not run_id:
        return json.dumps({"success": False, "error": "run_id required"})

    project_dir = str(args.get("project_dir") or "").strip()
    db_path = _research_db_path(project_dir)
    conn = _ensure_db(db_path)

    try:
        source_id = _uid("src_")
        conn.execute(
            "INSERT INTO research_sources "
            "(id, research_run_id, question_id, source_type, title, url_or_path, "
            "authority_tier, retrieved_at, summary, verbatim_excerpt, confidence) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (source_id, run_id, args.get("question_id", ""),
             args["source_type"], args.get("title", ""),
             args.get("url_or_path", ""), args.get("authority_tier", "C"),
             _now(), args.get("summary", ""), args.get("verbatim_excerpt", ""),
             args.get("confidence", 0.5)),
        )
        # Auto-update run status to collecting
        conn.execute("UPDATE research_runs SET status = 'collecting' WHERE id = ? AND status IN ('intake', 'planned')", (run_id,))
        conn.commit()
        return json.dumps({"success": True, "source_id": source_id}, ensure_ascii=False)
    finally:
        conn.close()


# ── research_synthesize ─────────────────────────────────────────────────────

RESEARCH_SYNTHESIZE_SCHEMA = {
    "name": "research_synthesize",
    "description": (
        "Record a research finding with supporting sources and confidence level. "
        "Use after collecting sources to consolidate findings."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "run_id": {"type": "string", "description": "Research run ID."},
            "question_id": {"type": "string", "description": "Question this finding addresses."},
            "finding_text": {"type": "string", "description": "The finding/conclusion."},
            "supporting_sources": {"type": "array", "items": {"type": "string"}, "description": "Source IDs that support this finding."},
            "confidence": {"type": "number", "description": "Confidence level (0-1)."},
            "status": {"type": "string", "enum": ["confirmed", "tentative", "disputed"], "description": "Finding status."},
            "open_issue": {"type": "string", "description": "Unresolved issue or gap."},
            "fact_candidate": {"type": "boolean", "description": "If true, eligible for project facts update."},
            "project_dir": {"type": "string"},
        },
        "required": ["run_id", "finding_text"],
    },
}


def research_synthesize_handler(args: dict, **kwargs) -> str:
    run_id = args.get("run_id", "")
    if not run_id:
        return json.dumps({"success": False, "error": "run_id required"})

    project_dir = str(args.get("project_dir") or "").strip()
    db_path = _research_db_path(project_dir)
    conn = _ensure_db(db_path)

    try:
        finding_id = _uid("find_")
        conn.execute(
            "INSERT INTO research_findings "
            "(id, research_run_id, question_id, finding_text, supporting_sources, "
            "confidence, status, open_issue, fact_candidate) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (finding_id, run_id, args.get("question_id", ""),
             args["finding_text"], json.dumps(args.get("supporting_sources", [])),
             args.get("confidence", 0.5), args.get("status", "tentative"),
             args.get("open_issue", ""), 1 if args.get("fact_candidate") else 0),
        )
        conn.execute("UPDATE research_runs SET status = 'synthesizing' WHERE id = ? AND status IN ('collecting', 'planned')", (run_id,))
        conn.commit()
        return json.dumps({"success": True, "finding_id": finding_id}, ensure_ascii=False)
    finally:
        conn.close()


# ── research_report ─────────────────────────────────────────────────────────

RESEARCH_REPORT_SCHEMA = {
    "name": "research_report",
    "description": (
        "Generate research report in md/html/docx formats. "
        "Reads all sources and findings from the run and produces deliverables."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "run_id": {"type": "string", "description": "Research run ID."},
            "output_dir": {"type": "string", "description": "Directory for output files. Defaults to project dir."},
            "project_dir": {"type": "string"},
        },
        "required": ["run_id"],
    },
}


def research_report_handler(args: dict, **kwargs) -> str:
    run_id = args.get("run_id", "")
    if not run_id:
        return json.dumps({"success": False, "error": "run_id required"})

    project_dir = str(args.get("project_dir") or "").strip()
    output_dir = str(args.get("output_dir") or project_dir or ".").strip()
    db_path = _research_db_path(project_dir)
    conn = _ensure_db(db_path)

    try:
        # Get run
        row = conn.execute("SELECT * FROM research_runs WHERE id = ?", (run_id,)).fetchone()
        if not row:
            return json.dumps({"success": False, "error": f"Run not found: {run_id}"})
        cols = [d[0] for d in conn.execute("PRAGMA table_info(research_runs)").fetchall()]
        run = dict(zip(cols, row))

        # Get findings
        fs = conn.execute("SELECT * FROM research_findings WHERE research_run_id = ? ORDER BY confidence DESC", (run_id,)).fetchall()
        f_cols = [d[0] for d in conn.execute("PRAGMA table_info(research_findings)").fetchall()]
        findings = [dict(zip(f_cols, f)) for f in fs]

        # Get sources
        ss = conn.execute("SELECT * FROM research_sources WHERE research_run_id = ? ORDER BY authority_tier, confidence DESC", (run_id,)).fetchall()
        s_cols = [d[0] for d in conn.execute("PRAGMA table_info(research_sources)").fetchall()]
        sources = [dict(zip(s_cols, s)) for s in ss]

        # Generate MD
        md = _render_report_md(run, findings, sources)
        out_path = Path(output_dir)
        out_path.mkdir(parents=True, exist_ok=True)
        slug = run.get("title", "research")[:40].replace(" ", "-").replace("/", "-")
        md_path = out_path / f"research-{slug}.md"
        md_path.write_text(md, encoding="utf-8")

        # Update run
        conn.execute(
            "UPDATE research_runs SET status = 'done', finished_at = ?, report_paths = ? WHERE id = ?",
            (_now(), json.dumps([str(md_path)]), run_id),
        )
        conn.commit()

        return json.dumps({
            "success": True,
            "report_paths": [str(md_path)],
            "finding_count": len(findings),
            "source_count": len(sources),
        }, ensure_ascii=False)
    finally:
        conn.close()


def _render_report_md(run: dict, findings: list, sources: list) -> str:
    lines = [
        f"# Research: {run.get('title', '')}",
        "",
        f"**Question:** {run.get('question', '')}",
        f"**Jurisdiction:** {run.get('jurisdiction', '-')}",
        f"**Date scope:** {run.get('date_scope', '-')}",
        f"**Status:** {run.get('status', '-')}",
        "",
        "## Findings",
        "",
    ]

    confirmed = [f for f in findings if f.get("status") == "confirmed"]
    tentative = [f for f in findings if f.get("status") == "tentative"]
    disputed = [f for f in findings if f.get("status") == "disputed"]

    if confirmed:
        lines.append("### Confirmed")
        for f in confirmed:
            lines.append(f"- {f['finding_text']} (confidence: {f.get('confidence', 0):.0%})")
        lines.append("")

    if tentative:
        lines.append("### Tentative")
        for f in tentative:
            lines.append(f"- {f['finding_text']} (confidence: {f.get('confidence', 0):.0%})")
        lines.append("")

    if disputed:
        lines.append("### Disputed")
        for f in disputed:
            lines.append(f"- {f['finding_text']}")
            if f.get("open_issue"):
                lines.append(f"  - Open: {f['open_issue']}")
        lines.append("")

    lines.extend([
        "## Source Ledger",
        "",
        "| # | Source | Type | Authority | Confidence | Summary |",
        "|---|--------|------|-----------|------------|---------|",
    ])
    for i, s in enumerate(sources, 1):
        lines.append(
            f"| {i} | {s.get('title', '')[:40]} | {s.get('source_type', '')} | "
            f"{s.get('authority_tier', '')} | {s.get('confidence', 0):.0%} | "
            f"{s.get('summary', '')[:60]} |"
        )

    lines.append("")
    return "\n".join(lines)


# ── research_update_facts ───────────────────────────────────────────────────

RESEARCH_UPDATE_FACTS_SCHEMA = {
    "name": "research_update_facts",
    "description": (
        "Write high-confidence research findings into project facts. "
        "Only findings marked as fact_candidate with confidence >= 0.7 are eligible."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "run_id": {"type": "string", "description": "Research run ID."},
            "finding_id": {"type": "string", "description": "Specific finding to promote (optional, promotes all eligible if empty)."},
            "project_dir": {"type": "string"},
        },
        "required": ["run_id"],
    },
}


def research_update_facts_handler(args: dict, **kwargs) -> str:
    run_id = args.get("run_id", "")
    if not run_id:
        return json.dumps({"success": False, "error": "run_id required"})

    project_dir = str(args.get("project_dir") or "").strip()
    db_path = _research_db_path(project_dir)
    conn = _ensure_db(db_path)

    try:
        finding_id = args.get("finding_id", "")
        if finding_id:
            rows = conn.execute(
                "SELECT * FROM research_findings WHERE id = ? AND research_run_id = ? AND fact_candidate = 1 AND confidence >= 0.7",
                (finding_id, run_id),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM research_findings WHERE research_run_id = ? AND fact_candidate = 1 AND confidence >= 0.7 AND status = 'confirmed'",
                (run_id,),
            ).fetchall()

        if not rows:
            return json.dumps({"success": True, "promoted": 0, "message": "No eligible findings."})

        f_cols = [d[0] for d in conn.execute("PRAGMA table_info(research_findings)").fetchall()]
        findings = [dict(zip(f_cols, r)) for r in rows]

        # Write to project facts via memory tool
        from tools.memory_tool import MemoryStore
        store = MemoryStore()
        promoted = 0
        for f in findings:
            fact_text = f"[Research {run_id[:12]}] {f['finding_text']}"
            result = store.add("memory", "project", fact_text)
            if result.get("success"):
                promoted += 1

        return json.dumps({"success": True, "promoted": promoted}, ensure_ascii=False)
    finally:
        conn.close()


# ── Registry ────────────────────────────────────────────────────────────────

from tools.registry import registry  # noqa: E402

registry.register(
    name="research_plan",
    toolset="research",
    schema=RESEARCH_PLAN_SCHEMA,
    handler=lambda args, **kw: research_plan_handler(args, **kw),
    emoji="📋",
)

registry.register(
    name="research_run",
    toolset="research",
    schema=RESEARCH_RUN_SCHEMA,
    handler=lambda args, **kw: research_run_handler(args, **kw),
    emoji="🔬",
)

registry.register(
    name="research_collect",
    toolset="research",
    schema=RESEARCH_COLLECT_SCHEMA,
    handler=lambda args, **kw: research_collect_handler(args, **kw),
    emoji="📥",
)

registry.register(
    name="research_synthesize",
    toolset="research",
    schema=RESEARCH_SYNTHESIZE_SCHEMA,
    handler=lambda args, **kw: research_synthesize_handler(args, **kw),
    emoji="🧪",
)

registry.register(
    name="research_report",
    toolset="research",
    schema=RESEARCH_REPORT_SCHEMA,
    handler=lambda args, **kw: research_report_handler(args, **kw),
    emoji="📊",
)

registry.register(
    name="research_update_facts",
    toolset="research",
    schema=RESEARCH_UPDATE_FACTS_SCHEMA,
    handler=lambda args, **kw: research_update_facts_handler(args, **kw),
    emoji="📝",
)
