import json

from tools.legal_orchestration_tool import (
    _build_review_subtasks,
    _extract_json_payload,
    _handle_legal_orchestrate,
    _normalize_findings,
    _run_scorecard_gate,
)


def test_extract_json_payload_accepts_plain_json():
    parsed = _extract_json_payload(
        '{"status":"completed","summary":"ok","findings":[{"severity":"major","location":"§3","title":"Missing term","finding":"Undefined term","suggestion":"Add definition"}]}'
    )

    assert parsed is not None
    assert parsed["status"] == "completed"
    assert parsed["findings"][0]["title"] == "Missing term"


def test_normalize_findings_falls_back_when_reviewer_is_unstructured():
    findings = _normalize_findings(
        None,
        review_type="review_content",
        document_path="agreement.docx",
        raw_summary="The indemnity cap is missing.",
    )

    assert len(findings) == 1
    assert findings[0]["review_type"] == "review_content"
    assert findings[0]["document_path"] == "agreement.docx"
    assert "indemnity cap" in findings[0]["finding"]


def test_build_review_subtasks_includes_output_contract(tmp_path):
    roles = tmp_path / ".hermes-project" / "roles"
    roles.mkdir(parents=True)
    (roles / "reviewer-content.md").write_text("reviewer role", encoding="utf-8")
    (tmp_path / ".hermes-project" / "project-context.md").write_text("project context", encoding="utf-8")

    tasks = _build_review_subtasks(
        project_root=tmp_path,
        document_path="agreement.docx",
        instructions="Check definitions.",
        related_paths=[],
        term_sheet_path=None,
        review_types=["review_content"],
        workflow_type="full_review",
        learning_scope="project",
    )

    assert len(tasks) == 1
    assert tasks[0]["toolsets"] == ["lex-docx-coordinator", "file", "project_management"]
    assert "Output Contract" in tasks[0]["context"]


class _StubAgent:
    session_id = "sess-legal"
    _selected_project_cwd = None


def test_build_review_subtasks_includes_xref_preflight_context(tmp_path, monkeypatch):
    roles = tmp_path / ".hermes-project" / "roles"
    roles.mkdir(parents=True)
    (roles / "reviewer-cross-ref.md").write_text("xref reviewer role", encoding="utf-8")
    (tmp_path / ".hermes-project" / "project-context.md").write_text("project context", encoding="utf-8")

    monkeypatch.setattr(
        "tools.legal_orchestration_tool._run_xref_preflight",
        lambda **kwargs: {
            "document_path": "agreement.docx",
            "findings": [{"title": "Dead internal cross-reference", "review_type": "review_xref"}],
            "summary": "1 broken xref",
        },
    )

    tasks = _build_review_subtasks(
        project_root=tmp_path,
        document_path="agreement.docx",
        instructions="Check cross references.",
        related_paths=[],
        term_sheet_path=None,
        review_types=["review_xref"],
        workflow_type="full_review",
        learning_scope="project",
    )

    assert len(tasks) == 1
    assert tasks[0]["review_type"] == "review_xref"
    assert tasks[0]["machine_summary"] == "1 broken xref"
    assert "Cross-Reference Preflight" in tasks[0]["context"]


def test_review_defaults_include_xref_and_merge_machine_findings(tmp_path, monkeypatch):
    roles = tmp_path / ".hermes-project" / "roles"
    roles.mkdir(parents=True)
    (roles / "reviewer-content.md").write_text("content reviewer", encoding="utf-8")
    (roles / "reviewer-format.md").write_text("format reviewer", encoding="utf-8")
    (roles / "reviewer-cross-ref.md").write_text("xref reviewer", encoding="utf-8")
    (tmp_path / ".hermes-project" / "project-context.md").write_text("project context", encoding="utf-8")

    machine_findings = [
        {
            "document_path": "agreement.docx",
            "finding": "第9条 does not resolve to an existing clause.",
            "location": "Paragraph 12",
            "review_type": "review_xref",
            "severity": "major",
            "suggestion": "Fix the target clause number.",
            "title": "Dead internal cross-reference: 第9条",
        }
    ]

    monkeypatch.setattr(
        "tools.legal_orchestration_tool._run_xref_preflight",
        lambda **kwargs: {
            "document_path": "agreement.docx",
            "findings": machine_findings,
            "summary": "1 broken xref",
        },
    )

    captured = {}

    def _fake_delegate_task(**kwargs):
        captured["tasks"] = kwargs.get("tasks")
        results = []
        for task in kwargs.get("tasks") or []:
            review_type = task["review_type"]
            results.append(
                {
                    "status": "completed",
                    "summary": json.dumps(
                        {
                            "status": "completed",
                            "summary": f"{review_type} done",
                            "findings": [],
                        }
                    ),
                }
            )
        return json.dumps({"results": results})

    monkeypatch.setattr("tools.legal_orchestration_tool.delegate_task", _fake_delegate_task)

    raw = _handle_legal_orchestrate(
        {"task_type": "review", "document_path": "agreement.docx", "project_dir": str(tmp_path)},
        parent_agent=_StubAgent(),
    )

    payload = json.loads(raw)
    assert [task["review_type"] for task in captured["tasks"]] == [
        "review_content",
        "review_format",
        "review_xref",
    ]
    assert any(item["title"] == "Dead internal cross-reference: 第9条" for item in payload["findings"])


def test_scorecard_gate_blocks_failed_scorecard(tmp_path, monkeypatch):
    def _fake_scorecard(*args, **kwargs):
        assert kwargs["workflow_id"] == "full_review"
        assert kwargs["run_id"] == "run-1"
        return {
            "ok": False,
            "status": "failed",
            "failures": [{"check": "review_plan", "message": "No review plan found."}],
        }

    monkeypatch.setattr("hermes_cli.project_commands.legal_scorecard", _fake_scorecard)

    payload = _run_scorecard_gate(
        tmp_path,
        document_path="agreement.docx",
        workflow_id="full_review",
        run_id="run-1",
        payload={"status": "completed", "verification_passed": True},
    )

    assert payload["status"] == "failed"
    assert payload["verification_passed"] is False
    assert payload["scorecard"]["ok"] is False
    assert "No review plan found." in payload["verification_report"][0]


def test_scorecard_gate_fails_closed_on_exception(tmp_path, monkeypatch):
    def _raise_scorecard(*args, **kwargs):
        raise RuntimeError("scorecard unavailable")

    monkeypatch.setattr("hermes_cli.project_commands.legal_scorecard", _raise_scorecard)

    payload = _run_scorecard_gate(
        tmp_path,
        document_path="agreement.docx",
        workflow_id="contract_revision",
        run_id="run-2",
        payload={"status": "completed", "verification_passed": True},
    )

    assert payload["status"] == "failed"
    assert payload["verification_passed"] is False
    assert payload["scorecard"]["ok"] is False
    assert payload["scorecard"]["status"] == "failed"
    assert "scorecard unavailable" in payload["verification_report"][0]
