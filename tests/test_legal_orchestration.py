from tools.legal_orchestration_tool import (
    _build_review_subtasks,
    _extract_json_payload,
    _normalize_findings,
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
    )

    assert len(tasks) == 1
    assert tasks[0]["toolsets"] == ["lex-docx", "file", "project_management"]
    assert "Output Contract" in tasks[0]["context"]
