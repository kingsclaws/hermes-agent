"""Unit tests for hermes_cli.kanban_legal_swarm — YAML-driven workflow orchestration."""

import json
import secrets
from pathlib import Path

import pytest

from hermes_cli import kanban_db as kb
from hermes_cli.kanban_legal_swarm import (
    NodeTaskMapping,
    WorkflowRun,
    compile_workflow,
    find_swarm_root,
    get_run,
    list_runs,
    persist_handoff_for_task,
    run_status,
    validate_handoff,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _unique_board(test_name: str) -> str:
    """Generate a board slug that won't collide across parallel test runs."""
    return f"test-ls-{test_name}-{secrets.token_hex(3)}"


def _write_workflow(project_dir: str, nodes=None):
    """Write a minimal workflow YAML into *project_dir*."""
    import yaml

    wf_dir = Path(project_dir) / ".hermes-project" / "workflows"
    wf_dir.mkdir(parents=True, exist_ok=True)
    spec = {
        "id": "test_wf",
        "version": 1,
        "nodes": nodes or [
            {"id": "step1", "kind": "analysis", "profile": "lex-coordinator", "requires": [],
             "output_required": ["status", "evidence"]},
            {"id": "step2", "kind": "worker", "profile": "lex-drafter", "requires": ["step1"],
             "output_required": ["status", "modified_files", "evidence"]},
        ],
        "scorecard": {"required": [], "block_on_failed_verification": False},
    }
    (wf_dir / "test_wf.yaml").write_text(
        yaml.safe_dump(spec, allow_unicode=True, sort_keys=False), encoding="utf-8"
    )


def _new_proj(tmp_path):
    """Create a minimal project directory with a fake docx."""
    proj = tmp_path / "proj"
    proj.mkdir()
    (proj / "test.docx").write_text("fake")
    return proj


# ---------------------------------------------------------------------------
# compile_workflow tests
# ---------------------------------------------------------------------------


class TestCompileWorkflow:

    def test_basic_compilation(self, tmp_path):
        proj = _new_proj(tmp_path)
        _write_workflow(str(proj))
        board = _unique_board("basic")

        run = compile_workflow(project_dir=str(proj), workflow_id="test_wf", board=board)

        assert run.workflow_id == "test_wf"
        assert run.board == board
        assert run.root_task_id.startswith("t_")
        assert set(run.node_mappings.keys()) == {"step1", "step2"}

        conn = kb.connect(board=board)
        try:
            root = kb.get_task(conn, run.root_task_id)
            assert root.status == "done"

            step1_tid = run.node_mappings["step1"].task_ids[0]
            step1 = kb.get_task(conn, step1_tid)
            assert step1.status == "ready"
            assert step1.assignee == "lex-coordinator"

            step2_tid = run.node_mappings["step2"].task_ids[0]
            step2 = kb.get_task(conn, step2_tid)
            assert step2.status == "todo"
            assert step2.assignee == "lex-drafter"

            assert kb.parent_ids(conn, step2_tid) == [step1_tid]
        finally:
            conn.close()

    def test_fanout_creates_parallel_tasks(self, tmp_path):
        proj = _new_proj(tmp_path)
        _write_workflow(str(proj), nodes=[
            {"id": "plan", "kind": "analysis", "profile": "lex-coordinator", "requires": [],
             "output_required": ["status"]},
            {"id": "review", "kind": "fanout",
             "profiles": ["lex-reviewer-content", "lex-reviewer-format", "lex-reviewer-xref"],
             "requires": ["plan"], "output_required": ["status", "findings"]},
        ])
        board = _unique_board("fanout")

        run = compile_workflow(project_dir=str(proj), workflow_id="test_wf", board=board)

        review_mapping = run.node_mappings["review"]
        assert review_mapping.kind == "fanout"
        assert len(review_mapping.task_ids) == 3

        conn = kb.connect(board=board)
        try:
            plan_tid = run.node_mappings["plan"].task_ids[0]
            for tid in review_mapping.task_ids:
                assert kb.parent_ids(conn, tid) == [plan_tid]
                assert kb.get_task(conn, tid).status == "todo"
        finally:
            conn.close()

    def test_gate_node_has_all_parents(self, tmp_path):
        proj = _new_proj(tmp_path)
        _write_workflow(str(proj), nodes=[
            {"id": "draft", "kind": "worker", "profile": "lex-drafter", "requires": [],
             "output_required": ["status"]},
            {"id": "review", "kind": "fanout",
             "profiles": ["lex-reviewer-content", "lex-reviewer-format"],
             "requires": ["draft"], "output_required": ["status"]},
            {"id": "gate", "kind": "gate", "profile": "lex-coordinator",
             "requires": ["review"], "checks": ["edit_verification"]},
        ])
        board = _unique_board("gate")

        run = compile_workflow(project_dir=str(proj), workflow_id="test_wf", board=board)

        conn = kb.connect(board=board)
        try:
            gate_tid = run.node_mappings["gate"].task_ids[0]
            review_ids = run.node_mappings["review"].task_ids
            assert set(kb.parent_ids(conn, gate_tid)) == set(review_ids)
            assert kb.get_task(conn, gate_tid).status == "todo"
        finally:
            conn.close()

    def test_parent_completion_unlocks_child(self, tmp_path):
        proj = _new_proj(tmp_path)
        _write_workflow(str(proj))
        board = _unique_board("unlock")

        run = compile_workflow(project_dir=str(proj), workflow_id="test_wf", board=board)

        conn = kb.connect(board=board)
        try:
            step1_tid = run.node_mappings["step1"].task_ids[0]
            step2_tid = run.node_mappings["step2"].task_ids[0]

            assert kb.get_task(conn, step2_tid).status == "todo"

            kb.complete_task(conn, step1_tid, summary="Done",
                             metadata={"status": "completed", "evidence": {"ok": True}})
            assert kb.get_task(conn, step2_tid).status == "ready"
        finally:
            conn.close()

    def test_idempotency_same_run_id(self, tmp_path):
        proj = _new_proj(tmp_path)
        _write_workflow(str(proj))
        board = _unique_board("idem")

        run1 = compile_workflow(project_dir=str(proj), workflow_id="test_wf",
                                board=board, run_id="run_fixed_001")
        run2 = compile_workflow(project_dir=str(proj), workflow_id="test_wf",
                                board=board, run_id="run_fixed_001")

        assert run2.root_task_id == run1.root_task_id

        conn = kb.connect(board=board)
        try:
            count = conn.execute(
                "SELECT COUNT(*) as n FROM tasks WHERE idempotency_key LIKE 'legal-swarm:%' "
                "AND status != 'archived'"
            ).fetchone()["n"]
            assert count == 3  # root + step1 + step2
        finally:
            conn.close()

    def test_timeout_and_retry_passed_to_create_task(self, tmp_path):
        proj = _new_proj(tmp_path)
        _write_workflow(str(proj), nodes=[
            {"id": "step1", "kind": "analysis", "profile": "lex-coordinator",
             "requires": [], "output_required": ["status"],
             "timeout_minutes": 15, "retry": {"max_attempts": 5}},
        ])
        board = _unique_board("timeout")

        run = compile_workflow(project_dir=str(proj), workflow_id="test_wf", board=board)

        conn = kb.connect(board=board)
        try:
            step1_tid = run.node_mappings["step1"].task_ids[0]
            task = kb.get_task(conn, step1_tid)
            assert task.max_runtime_seconds == 900
            assert task.max_retries == 5
        finally:
            conn.close()


# ---------------------------------------------------------------------------
# validate_handoff tests
# ---------------------------------------------------------------------------


class TestValidateHandoff:

    def test_rejects_missing_required_fields(self, tmp_path):
        proj = _new_proj(tmp_path)
        _write_workflow(str(proj))
        board = _unique_board("valrej")

        run = compile_workflow(project_dir=str(proj), workflow_id="test_wf", board=board)

        conn = kb.connect(board=board)
        try:
            step2_tid = run.node_mappings["step2"].task_ids[0]
            result = validate_handoff(conn, step2_tid, {"status": "completed"})
            assert result["ok"] is False
            assert len(result["errors"]) >= 2
            assert "modified_files" in str(result["errors"])
            assert "evidence" in str(result["errors"])
        finally:
            conn.close()

    def test_accepts_valid_handoff(self, tmp_path):
        proj = _new_proj(tmp_path)
        _write_workflow(str(proj))
        board = _unique_board("valok")

        run = compile_workflow(project_dir=str(proj), workflow_id="test_wf", board=board)

        conn = kb.connect(board=board)
        try:
            step2_tid = run.node_mappings["step2"].task_ids[0]
            valid = {"status": "completed", "evidence": {"rb": True}, "modified_files": ["t.docx"]}
            assert validate_handoff(conn, step2_tid, valid)["ok"] is True
        finally:
            conn.close()

    def test_skips_non_swarm_tasks(self, tmp_path):
        conn = kb.connect(tmp_path / "kanban.db")
        try:
            tid = kb.create_task(conn, title="Plain", assignee="test", created_by="test")
            assert validate_handoff(conn, tid, {"foo": "bar"})["ok"] is True
        finally:
            conn.close()

    def test_rejects_invalid_status_value(self, tmp_path):
        proj = _new_proj(tmp_path)
        _write_workflow(str(proj))
        board = _unique_board("valstatus")

        run = compile_workflow(project_dir=str(proj), workflow_id="test_wf", board=board)

        conn = kb.connect(board=board)
        try:
            step2_tid = run.node_mappings["step2"].task_ids[0]
            result = validate_handoff(conn, step2_tid, {
                "status": "gibberish", "evidence": {}, "modified_files": [],
            })
            assert result["ok"] is False
            assert "status" in str(result["errors"]).lower()
        finally:
            conn.close()


# ---------------------------------------------------------------------------
# find_swarm_root tests
# ---------------------------------------------------------------------------


class TestFindSwarmRoot:

    def test_finds_root_from_leaf_task(self, tmp_path):
        proj = _new_proj(tmp_path)
        _write_workflow(str(proj))
        board = _unique_board("root")

        run = compile_workflow(project_dir=str(proj), workflow_id="test_wf", board=board)

        conn = kb.connect(board=board)
        try:
            step2_tid = run.node_mappings["step2"].task_ids[0]
            assert find_swarm_root(conn, step2_tid) == run.root_task_id
        finally:
            conn.close()

    def test_returns_none_for_non_swarm_task(self, tmp_path):
        conn = kb.connect(tmp_path / "kanban.db")
        try:
            tid = kb.create_task(conn, title="Plain", assignee="test", created_by="test")
            assert find_swarm_root(conn, tid) is None
        finally:
            conn.close()


# ---------------------------------------------------------------------------
# persist_handoff_for_task tests
# ---------------------------------------------------------------------------


class TestPersistHandoff:

    def test_persists_handoff_envelope(self, tmp_path):
        proj = _new_proj(tmp_path)
        _write_workflow(str(proj))
        board = _unique_board("persist")

        run = compile_workflow(project_dir=str(proj), workflow_id="test_wf", board=board)

        conn = kb.connect(board=board)
        try:
            step2_tid = run.node_mappings["step2"].task_ids[0]
            conn.execute("UPDATE tasks SET status='ready' WHERE id=?", (step2_tid,))
            conn.commit()
            metadata = {"status": "completed", "evidence": {"ok": True}, "modified_files": ["t.docx"]}
            kb.complete_task(conn, step2_tid, summary="Done", metadata=metadata)

            record = persist_handoff_for_task(conn, step2_tid, metadata)
            assert record is not None
            assert record.get("ok") is True

            hb_path = proj / ".hermes-project" / "harness-runs" / run.run_id / "nodes" / "step2.json"
            assert hb_path.exists()
        finally:
            conn.close()

    def test_returns_none_for_non_swarm_task(self, tmp_path):
        conn = kb.connect(tmp_path / "kanban.db")
        try:
            tid = kb.create_task(conn, title="Plain", assignee="test", created_by="test")
            assert persist_handoff_for_task(conn, tid, {"status": "ok"}) is None
        finally:
            conn.close()


# ---------------------------------------------------------------------------
# run_status / list_runs / get_run tests
# ---------------------------------------------------------------------------


class TestRunQueries:

    def test_run_status_shows_per_node_state(self, tmp_path):
        proj = _new_proj(tmp_path)
        _write_workflow(str(proj))
        board = _unique_board("runstatus")

        run = compile_workflow(project_dir=str(proj), workflow_id="test_wf", board=board)

        conn = kb.connect(board=board)
        try:
            status = run_status(conn, run.root_task_id)
            assert status["ok"] is True
            assert status["workflow_id"] == "test_wf"
            assert "step1" in status["nodes"]
            assert "step2" in status["nodes"]
            assert status["nodes"]["step1"]["status"] == "pending"
        finally:
            conn.close()

    def test_list_runs_finds_compiled_runs(self, tmp_path):
        proj = _new_proj(tmp_path)
        _write_workflow(str(proj))
        board = _unique_board("listruns")

        compile_workflow(project_dir=str(proj), workflow_id="test_wf", board=board)

        conn = kb.connect(board=board)
        try:
            runs = list_runs(conn)
            assert len(runs) >= 1
            assert runs[0]["workflow_id"] == "test_wf"
        finally:
            conn.close()

    def test_get_run_returns_existing(self, tmp_path):
        proj = _new_proj(tmp_path)
        _write_workflow(str(proj))
        board = _unique_board("getrun")

        run = compile_workflow(project_dir=str(proj), workflow_id="test_wf", board=board)

        conn = kb.connect(board=board)
        try:
            found = get_run(conn, "test_wf", run.run_id)
            assert found is not None
            assert found.root_task_id == run.root_task_id
        finally:
            conn.close()

    def test_get_run_returns_none_for_unknown(self, tmp_path):
        conn = kb.connect(tmp_path / "kanban.db")
        try:
            assert get_run(conn, "nonexistent", "run_000") is None
        finally:
            conn.close()


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


class TestEdgeCases:

    def test_unknown_workflow_raises(self, tmp_path):
        proj = _new_proj(tmp_path)
        with pytest.raises(ValueError, match="Unknown workflow"):
            compile_workflow(project_dir=str(proj), workflow_id="does_not_exist",
                             board=_unique_board("unknown"))

    def test_fanout_without_profiles_raises(self, tmp_path):
        proj = _new_proj(tmp_path)
        _write_workflow(str(proj), nodes=[
            {"id": "bad_fanout", "kind": "fanout", "profiles": [], "requires": [],
             "output_required": []},
        ])
        with pytest.raises(ValueError, match="profiles"):
            compile_workflow(project_dir=str(proj), workflow_id="test_wf",
                             board=_unique_board("badfan"))

    def test_node_with_unknown_parent_raises(self, tmp_path):
        proj = _new_proj(tmp_path)
        _write_workflow(str(proj), nodes=[
            {"id": "orphan", "kind": "worker", "profile": "lex-drafter",
             "requires": ["ghost_node"], "output_required": []},
        ])
        with pytest.raises(ValueError, match="ghost_node"):
            compile_workflow(project_dir=str(proj), workflow_id="test_wf",
                             board=_unique_board("orphan"))

    def test_task_body_includes_node_metadata(self, tmp_path):
        proj = _new_proj(tmp_path)
        _write_workflow(str(proj))
        board = _unique_board("body")

        run = compile_workflow(project_dir=str(proj), workflow_id="test_wf", board=board,
                               params={"document_path": "test.docx", "instructions": "Fix §3"})

        conn = kb.connect(board=board)
        try:
            step1_tid = run.node_mappings["step1"].task_ids[0]
            task = kb.get_task(conn, step1_tid)
            body = task.body or ""
            assert "Swarm Protocol" in body
            assert "test_wf" in body
            assert "Parameters" in body
            assert "test.docx" in body
            assert "Required Handoff Fields" in body
        finally:
            conn.close()
