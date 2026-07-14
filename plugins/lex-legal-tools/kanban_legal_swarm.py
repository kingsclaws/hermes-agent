"""Kanban Legal Swarm — YAML-driven workflow orchestration on Kanban.

Reads workflow YAML definitions from ``.hermes-project/workflows/`` and compiles
them into a kanban board task graph. The existing kanban kernel handles
dispatching, claiming, dependency unlocking (``recompute_ready``), and crash
recovery — this module is purely the compiler bridge between YAML and kanban.

Workflow node kinds
-------------------
- ``analysis`` — single coordinator task (profile)
- ``worker``   — single specialist task (profile)
- ``fanout``   — one task per profile in ``profiles[]``, all parallel siblings
- ``tool``     — single tool-invocation task (profile)
- ``gate``     — scorecard-gate task that runs ``legal_scorecard()`` (profile)

Usage::

    from hermes_plugins.lex_legal_tools.kanban_legal_swarm import compile_workflow

    run = compile_workflow(
        project_dir="/workingfile/my-project",
        workflow_id="contract_revision",
        params={"document_path": "/workingfile/my-project/source.docx",
                "instructions": "Update §3 interest rate"},
    )
    # Daemon picks up ready tasks automatically. Monitor via:
    #   kanban list board run.board
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from hermes_cli import kanban_db as kb
from hermes_cli.kanban_swarm import (
    BLACKBOARD_PREFIX,
    latest_blackboard,
    post_blackboard_update,
)

# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class NodeTaskMapping:
    """One YAML node compiled to one or more kanban task IDs."""

    node_id: str
    kind: str
    task_ids: list[str]  # fanout nodes produce >1 task
    profile: str | None
    output_required: list[str]


@dataclass(frozen=True)
class WorkflowRun:
    """Result of compiling a workflow YAML into a kanban board."""

    workflow_id: str
    run_id: str
    board: str
    root_task_id: str
    node_mappings: dict[str, NodeTaskMapping]  # node_id -> mapping
    params: dict
    project_dir: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "workflow_id": self.workflow_id,
            "run_id": self.run_id,
            "board": self.board,
            "root_task_id": self.root_task_id,
            "project_dir": self.project_dir,
            "node_mappings": {
                nid: {
                    "node_id": m.node_id,
                    "kind": m.kind,
                    "task_ids": list(m.task_ids),
                    "profile": m.profile,
                    "output_required": list(m.output_required),
                }
                for nid, m in self.node_mappings.items()
            },
            "params": self.params,
        }


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _require_text(value: str, field_name: str) -> str:
    text = (value or "").strip()
    if not text:
        raise ValueError(f"{field_name} is required")
    return text


def _new_run_id() -> str:
    import secrets

    return "run_" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ_") + secrets.token_hex(4)


def _resolve_workflow_spec(project_dir: str, workflow_id: str) -> dict:
    """Load a workflow YAML, falling back to the embedded template dict."""
    from hermes_cli.project_commands import _load_legal_workflow

    spec = _load_legal_workflow(project_dir, workflow_id)
    if not spec:
        raise ValueError(
            f"Unknown workflow '{workflow_id}'. "
            f"Available workflows are in .hermes-project/workflows/."
        )
    if not isinstance(spec.get("nodes"), list) or not spec["nodes"]:
        raise ValueError(f"Workflow '{workflow_id}' has no nodes defined.")
    return spec


def _build_task_body(
    node: dict,
    *,
    workflow_id: str,
    run_id: str,
    root_task_id: str,
    params: dict,
) -> str:
    """Construct the task body a worker sees when dispatched."""
    node_id = node.get("id", "?")
    kind = node.get("kind", "worker")
    profile = node.get("profile", "")
    checks = node.get("checks") or []
    output_required = node.get("output_required") or []
    input_schema = node.get("input_schema") or []
    timeout_mins = node.get("timeout_minutes")
    node_retry = node.get("retry") or {}

    parts: list[str] = [
        f"## Legal Swarm Task — {workflow_id} / {run_id}",
        "",
        f"**Node:** `{node_id}`  ",
        f"**Kind:** `{kind}`  ",
        f"**Profile:** `{profile}`  ",
        f"**Root / Blackboard:** `{root_task_id}`  ",
    ]
    if timeout_mins:
        parts.append(f"**Timeout:** {timeout_mins} min  ")
    if node_retry:
        max_att = node_retry.get("max_attempts", "?")
        parts.append(f"**Max Retries:** {max_att}  ")

    parts += [
        "",
        "## Swarm Protocol",
        f"- Swarm root (shared blackboard): `{root_task_id}`",
        "- Read sibling/parent handoffs from kanban context before working.",
        "- Put machine-readable facts in completion metadata.",
        "- Put cross-worker notes on the root task via structured comments.",
    ]

    if params:
        parts.append("")
        parts.append("## Parameters")
        for k, v in params.items():
            parts.append(f"- **{k}:** {v}")

    if input_schema:
        parts.append("")
        parts.append("## Expected Inputs")
        for field in input_schema:
            parts.append(f"- `{field}`")

    if output_required:
        parts.append("")
        parts.append("## Required Handoff Fields")
        for field in output_required:
            parts.append(f"- `{field}`")

    if checks:
        parts.append("")
        parts.append("## Scorecard Checks (gate nodes only)")
        for check in checks:
            parts.append(f"- `{check}`")

    if kind == "gate":
        parts.append("")
        parts.append("## Scorecard Gate Instructions")
        parts.append("All upstream nodes for this workflow run have completed.")
        parts.append("Your job:")
        parts.append(f"1. Run `legal_scorecard()` with project_dir, workflow_id='{workflow_id}', run_id='{run_id}', strict=True")
        parts.append("2. Review the scorecard results.")
        parts.append('3. If all checks pass → complete this task with metadata: {"gate": "pass"}')
        parts.append('4. If checks fail → report failures, metadata: {"gate": "fail", "failures": [...]}')

    return "\n".join(parts)


def _node_idempotency_key(workflow_id: str, run_id: str, node_id: str, suffix: str = "") -> str:
    """Stable idempotency key so re-compiling the same run is a no-op."""
    base = f"legal-swarm:{workflow_id}:{run_id}:{node_id}"
    return f"{base}:{suffix}" if suffix else base


# ---------------------------------------------------------------------------
# Compile entry point
# ---------------------------------------------------------------------------


def compile_workflow(
    project_dir: str,
    workflow_id: str,
    *,
    board: str | None = None,
    run_id: str | None = None,
    params: dict | None = None,
    tenant: str | None = None,
    created_by: str = "legal-swarm",
    workspace_kind: str = "dir",
    workspace_path: str | None = None,
) -> WorkflowRun:
    """Compile a workflow YAML into kanban tasks.

    Reads ``.hermes-project/workflows/{workflow_id}.yaml``, creates a kanban
    board, and writes a task graph with correct dependencies, assignees, and
    metadata.  Returns a ``WorkflowRun`` that maps YAML node ids to kanban
    task ids.

    Idempotent: calling this twice with the same ``run_id`` returns the
    already-compiled run without creating duplicate tasks.
    """
    project_dir = str(Path(project_dir).expanduser().resolve())
    _require_text(workflow_id, "workflow_id")

    run_id = run_id or _new_run_id()
    params = dict(params or {})

    # Resolve board: explicit arg > env var > derive from workflow+run
    if board:
        board_slug = board
    else:
        board_slug = kb.get_current_board()
        # If no board is configured (falling back to "default"), pin to a
        # workflow-specific board so legal swarm runs don't pollute the
        # general-purpose board.
        if board_slug == "default":
            board_slug = f"legal-{workflow_id}"

    # Resolve workspace_path from project_dir when not specified
    if workspace_path is None:
        workspace_path = project_dir

    spec = _resolve_workflow_spec(project_dir, workflow_id)
    workflow_nodes: list[dict] = spec["nodes"]
    scorecard_cfg: dict = spec.get("scorecard", {})

    conn = kb.connect(board=board_slug)

    # --- idempotency: check for existing run ---
    existing = _find_existing_run(conn, workflow_id, run_id)
    if existing:
        conn.close()
        return existing

    # --- create root / blackboard anchor ---
    root_id = kb.create_task(
        conn,
        title=f"Legal Swarm: {workflow_id} — {run_id}",
        body=(
            "Legal Swarm root / blackboard anchor. This task is completed "
            "immediately so worker nodes can start while it remains the "
            "shared blackboard and audit anchor.\n\n"
            f"Workflow: {workflow_id}\n"
            f"Run: {run_id}\n"
            f"Project: {project_dir}\n"
        ),
        assignee=created_by,
        created_by=created_by,
        tenant=tenant,
        idempotency_key=_node_idempotency_key(workflow_id, run_id, "__root__"),
        workspace_kind=workspace_kind,
        workspace_path=workspace_path,
        skills=["kanban-orchestrator"],
    )

    kb.complete_task(
        conn,
        root_id,
        summary="Legal swarm topology planned; root remains the shared blackboard.",
        metadata={
            "kind": "legal_swarm_v1",
            "workflow_id": workflow_id,
            "run_id": run_id,
            "project_dir": project_dir,
            "node_count": len(workflow_nodes),
        },
    )

    # --- Phase 1: create all tasks (no parent links yet, so we can resolve node→task mappings) ---
    node_task_map: dict[str, NodeTaskMapping] = {}

    for node in workflow_nodes:
        node_id = _require_text(node.get("id"), f"node.id in {workflow_id}")
        kind = node.get("kind", "worker")
        profile = node.get("profile") or None
        output_required: list[str] = node.get("output_required") or []
        profiles: list[str] = node.get("profiles") or []
        checks: list[str] = node.get("checks") or []
        title = node.get("title") or f"[{workflow_id}] {node_id}"

        # Optional node-level runtime config
        timeout_mins = node.get("timeout_minutes")
        max_runtime = int(timeout_mins * 60) if isinstance(timeout_mins, (int, float)) else None
        node_retry = node.get("retry") or {}
        max_retries = int(node_retry.get("max_attempts")) if isinstance(node_retry, dict) and "max_attempts" in node_retry else None

        if kind == "fanout":
            if not profiles:
                raise ValueError(
                    f"Fanout node '{node_id}' in {workflow_id} requires a 'profiles' list."
                )
            task_ids: list[str] = []
            for i, prof in enumerate(profiles):
                prof = prof.strip()
                fanout_suffix = f"fanout{i}"
                tid = kb.create_task(
                    conn,
                    title=f"{title} ({prof})",
                    body=_build_task_body(
                        node,
                        workflow_id=workflow_id,
                        run_id=run_id,
                        root_task_id=root_id,
                        params=params,
                    ),
                    assignee=prof,
                    created_by=created_by,
                    parents=[root_id],
                    tenant=tenant,
                    idempotency_key=_node_idempotency_key(workflow_id, run_id, node_id, fanout_suffix),
                    workspace_kind=workspace_kind,
                    workspace_path=workspace_path,
                    max_runtime_seconds=max_runtime,
                    max_retries=max_retries,
                )
                task_ids.append(tid)
            node_task_map[node_id] = NodeTaskMapping(
                node_id=node_id,
                kind=kind,
                task_ids=task_ids,
                profile=None,  # fanout has multiple profiles
                output_required=output_required,
            )
        else:
            tid = kb.create_task(
                conn,
                title=title,
                body=_build_task_body(
                    node,
                    workflow_id=workflow_id,
                    run_id=run_id,
                    root_task_id=root_id,
                    params=params,
                ),
                assignee=profile,
                created_by=created_by,
                parents=[root_id],
                tenant=tenant,
                idempotency_key=_node_idempotency_key(workflow_id, run_id, node_id),
                workspace_kind=workspace_kind,
                workspace_path=workspace_path,
                max_runtime_seconds=max_runtime,
                max_retries=max_retries,
            )
            node_task_map[node_id] = NodeTaskMapping(
                node_id=node_id,
                kind=kind,
                task_ids=[tid],
                profile=profile,
                output_required=output_required,
            )

    # --- Phase 2: replace parent links with actual node dependencies ---
    for node in workflow_nodes:
        node_id = node.get("id")
        requires: list[str] = node.get("requires") or []
        if not requires:
            continue
        child_mapping = node_task_map[node_id]
        # Collect all parent task IDs from the required nodes
        parent_task_ids: list[str] = []
        for req_node_id in requires:
            parent_mapping = node_task_map.get(req_node_id)
            if parent_mapping is None:
                raise ValueError(
                    f"Node '{node_id}' requires '{req_node_id}' which is not "
                    f"in the workflow node list."
                )
            parent_task_ids.extend(parent_mapping.task_ids)

        # Unlink from root, link to actual parents
        for child_tid in child_mapping.task_ids:
            kb.unlink_tasks(conn, root_id, child_tid)
            for parent_tid in parent_task_ids:
                kb.link_tasks(conn, parent_tid, child_tid)

    # --- Phase 3: store topology on blackboard ---
    run = WorkflowRun(
        workflow_id=workflow_id,
        run_id=run_id,
        board=board_slug,
        root_task_id=root_id,
        node_mappings=node_task_map,
        params=params,
        project_dir=project_dir,
    )

    post_blackboard_update(
        conn,
        root_id,
        author=created_by,
        key="topology",
        value=run.as_dict(),
    )

    # --- Phase 4: create harness run directory ---
    from hermes_cli.project_commands import _append_harness_event

    _append_harness_event(
        project_dir,
        run_id,
        "workflow_compiled",
        {
            "workflow_id": workflow_id,
            "board": board_slug,
            "root_task_id": root_id,
            "node_count": len(workflow_nodes),
            "node_mappings": {
                nid: m.task_ids for nid, m in node_task_map.items()
            },
        },
    )

    conn.close()
    return run


def _find_existing_run(
    conn: sqlite3.Connection,
    workflow_id: str,
    run_id: str,
) -> WorkflowRun | None:
    """Check whether *run_id* was already compiled on this board.

    Scans all root-level tasks for a matching idempotency key, then reads
    the blackboard topology to reconstruct the ``WorkflowRun``.
    """
    root_key = _node_idempotency_key(workflow_id, run_id, "__root__")
    row = conn.execute(
        "SELECT id FROM tasks WHERE idempotency_key = ? "
        "AND status != 'archived' ORDER BY created_at DESC LIMIT 1",
        (root_key,),
    ).fetchone()
    if row is None:
        return None

    root_id = row["id"]
    bb = latest_blackboard(conn, root_id)
    topo = bb.get("topology")
    if not isinstance(topo, dict):
        return None

    # Reconstruct node mappings from topology dict
    node_mappings: dict[str, NodeTaskMapping] = {}
    for nid, ndict in topo.get("node_mappings", {}).items():
        if isinstance(ndict, dict):
            node_mappings[nid] = NodeTaskMapping(
                node_id=ndict.get("node_id", nid),
                kind=ndict.get("kind", "worker"),
                task_ids=list(ndict.get("task_ids", [])),
                profile=ndict.get("profile"),
                output_required=list(ndict.get("output_required", [])),
            )

    return WorkflowRun(
        workflow_id=topo.get("workflow_id", workflow_id),
        run_id=topo.get("run_id", run_id),
        board=topo.get("board", ""),
        root_task_id=root_id,
        node_mappings=node_mappings,
        params=topo.get("params", {}),
        project_dir=topo.get("project_dir", ""),
    )


# ---------------------------------------------------------------------------
# Run-level queries
# ---------------------------------------------------------------------------


def get_run(
    conn: sqlite3.Connection,
    workflow_id: str,
    run_id: str,
) -> WorkflowRun | None:
    """Look up a previously-compiled workflow run by id."""
    return _find_existing_run(conn, workflow_id, run_id)


def list_runs(
    conn: sqlite3.Connection,
) -> list[dict[str, Any]]:
    """List all legal-swarm runs on this board (brief summaries)."""
    rows = conn.execute(
        "SELECT id, title, status, created_at, completed_at "
        "FROM tasks "
        "WHERE idempotency_key LIKE 'legal-swarm:%__root__' "
        "AND status != 'archived' "
        "ORDER BY created_at DESC"
    ).fetchall()

    runs: list[dict[str, Any]] = []
    for row in rows:
        bb = latest_blackboard(conn, row["id"])
        topo = bb.get("topology") or {}
        runs.append({
            "root_task_id": row["id"],
            "title": row["title"],
            "status": row["status"],
            "created_at": row["created_at"],
            "completed_at": row["completed_at"],
            "workflow_id": topo.get("workflow_id", ""),
            "run_id": topo.get("run_id", ""),
            "board": topo.get("board", ""),
            "node_count": len(topo.get("node_mappings", {})),
        })
    return runs


def run_status(
    conn: sqlite3.Connection,
    root_task_id: str,
) -> dict[str, Any]:
    """Return per-node status for a compiled workflow run."""
    bb = latest_blackboard(conn, root_task_id)
    topo = bb.get("topology")
    if not isinstance(topo, dict):
        return {"ok": False, "error": "Not a legal swarm root task"}

    nodes_status: dict[str, dict[str, Any]] = {}
    for nid, ndict in topo.get("node_mappings", {}).items():
        if not isinstance(ndict, dict):
            continue
        task_ids = ndict.get("task_ids", [])
        task_statuses: list[dict[str, Any]] = []
        for tid in task_ids:
            row = conn.execute(
                "SELECT id, title, status, assignee, created_at, completed_at "
                "FROM tasks WHERE id = ?",
                (tid,),
            ).fetchone()
            if row:
                task_statuses.append({
                    "task_id": row["id"],
                    "title": row["title"],
                    "status": row["status"],
                    "assignee": row["assignee"],
                })
        all_done = all(t["status"] == "done" for t in task_statuses)
        any_running = any(t["status"] == "running" for t in task_statuses)
        nodes_status[nid] = {
            "node_id": nid,
            "kind": ndict.get("kind", "?"),
            "task_ids": task_ids,
            "tasks": task_statuses,
            "status": "done" if all_done else ("running" if any_running else "pending"),
        }

    root_row = conn.execute(
        "SELECT status FROM tasks WHERE id = ?", (root_task_id,)
    ).fetchone()

    return {
        "ok": True,
        "root_task_id": root_task_id,
        "root_status": root_row["status"] if root_row else "?",
        "workflow_id": topo.get("workflow_id", ""),
        "run_id": topo.get("run_id", ""),
        "nodes": nodes_status,
    }


def get_run_task_ids(conn: sqlite3.Connection, root_task_id: str) -> list[str]:
    """Return every task_id in a compiled workflow run (including the root).

    Reads the blackboard topology stored on the root task. Returns an
    empty list if *root_task_id* doesn't carry a legal-swarm topology.
    """
    bb = latest_blackboard(conn, root_task_id)
    topo = bb.get("topology")
    if not isinstance(topo, dict):
        return []
    ids: list[str] = [root_task_id]
    for ndict in topo.get("node_mappings", {}).values():
        if isinstance(ndict, dict):
            ids.extend(ndict.get("task_ids", []))
    return ids


# ---------------------------------------------------------------------------
# Handoff validation (called at kanban_complete time)
# ---------------------------------------------------------------------------


def find_swarm_root(conn: sqlite3.Connection, task_id: str) -> str | None:
    """Walk parent links upward to find a legal-swarm root task.

    Returns the root task_id, or ``None`` if *task_id* is not part of
    a legal swarm workflow.
    """
    seen: set[str] = set()
    current = task_id
    while current and current not in seen:
        seen.add(current)
        # Check if this task is a legal swarm root
        row = conn.execute(
            "SELECT idempotency_key FROM tasks WHERE id = ?",
            (current,),
        ).fetchone()
        if row and (row["idempotency_key"] or "").startswith("legal-swarm:") and (row["idempotency_key"] or "").endswith(":__root__"):
            return current
        # Walk up to parents
        parent_row = conn.execute(
            "SELECT parent_id FROM task_links WHERE child_id = ? ORDER BY parent_id LIMIT 1",
            (current,),
        ).fetchone()
        current = parent_row["parent_id"] if parent_row else None
    return None


def validate_handoff(
    conn: sqlite3.Connection,
    task_id: str,
    metadata: dict | None,
) -> dict:
    """Validate completion metadata against the node's output_required schema.

    Called by ``kanban_complete`` before allowing task completion.
    Returns ``{"ok": True}`` if the handoff passes, or
    ``{"ok": False, "errors": [...], "expected": [...]}`` if rejected.
    """
    root_id = find_swarm_root(conn, task_id)
    if root_id is None:
        return {"ok": True}  # Not a legal swarm task — skip validation

    bb = latest_blackboard(conn, root_id)
    topo = bb.get("topology")
    if not isinstance(topo, dict):
        return {"ok": True}  # Topology not readable — skip

    # Find which node maps to this task_id
    node_mappings = topo.get("node_mappings", {})
    matched_node_id: str | None = None
    matched_node: dict | None = None
    for nid, ndict in node_mappings.items():
        if not isinstance(ndict, dict):
            continue
        if task_id in (ndict.get("task_ids") or []):
            matched_node_id = nid
            matched_node = ndict
            break

    if matched_node is None:
        return {"ok": True}  # Task not found in topology — skip

    output_required: list[str] = matched_node.get("output_required") or []
    if not output_required:
        return {"ok": True}  # No handoff requirements — skip

    # Validate the metadata envelope
    handoff = metadata or {}
    from hermes_cli.project_commands import _validate_handoff_envelope

    errors = _validate_handoff_envelope(handoff, output_required)
    if errors:
        return {
            "ok": False,
            "errors": errors,
            "expected": ["status", "evidence", *output_required],
            "node_id": matched_node_id,
            "hint": "Add the missing fields to your completion metadata and retry kanban_complete.",
        }

    return {"ok": True, "node_id": matched_node_id}


def persist_handoff_for_task(
    conn: sqlite3.Connection,
    task_id: str,
    metadata: dict,
) -> dict | None:
    """Persist a validated handoff envelope after successful kanban_complete.

    Reads the project directory and workflow topology from the swarm root
    blackboard. Returns the handoff record, or ``None`` if the task is not
    part of a legal swarm workflow.
    """
    root_id = find_swarm_root(conn, task_id)
    if root_id is None:
        return None

    bb = latest_blackboard(conn, root_id)
    topo = bb.get("topology")
    if not isinstance(topo, dict):
        return None

    project_dir = topo.get("project_dir", "")
    if not project_dir:
        return None

    workflow_id = topo.get("workflow_id", "")
    run_id = topo.get("run_id", "")

    # Find node_id for this task
    node_mappings = topo.get("node_mappings", {})
    node_id: str | None = None
    for nid, ndict in node_mappings.items():
        if not isinstance(ndict, dict):
            continue
        if task_id in (ndict.get("task_ids") or []):
            node_id = nid
            break

    if node_id is None:
        return None

    from hermes_cli.project_commands import legal_handoff_record

    return legal_handoff_record(
        project_dir,
        workflow_id=workflow_id,
        node_id=node_id,
        handoff=metadata,
        run_id=run_id,
        action="add",
    )
