from __future__ import annotations

import importlib.util
import json
from pathlib import Path


def _load_plugin():
    plugin_path = (
        Path(__file__).resolve().parents[2]
        / "plugins"
        / "backoffice-issue-relay"
        / "__init__.py"
    )
    spec = importlib.util.spec_from_file_location("backoffice_issue_relay", plugin_path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


def test_lex_ocr_401_creates_redacted_backoffice_issue(tmp_path, monkeypatch):
    plugin = _load_plugin()
    issue_dir = tmp_path / "issues"
    monkeypatch.setattr(plugin, "_FALLBACK_ISSUE_DIR", issue_dir)
    monkeypatch.setattr(plugin, "_project_root_from_args", lambda _args: None)

    result = json.dumps({"error": "401 Client Error: Unauthorized for url: https://mineru.net/api/v4/file-urls/batch"})
    transformed = plugin._on_transform_tool_result(
        tool_name="lex_ocr",
        args={
            "path": "/workingfile/140. 颐保银团/上海颐保综合授信额度核定.pdf",
            "prompt": "读取这份文件的全部内容",
        },
        result=result,
        session_id="sess_1",
    )

    files = list(issue_dir.glob("*.json"))
    assert len(files) == 1
    issue = json.loads(files[0].read_text(encoding="utf-8"))
    assert issue["category"] == "ocr"
    assert issue["severity"] == "high"
    assert issue["issue_path"] == str(files[0])
    assert issue["report_path"] == str(files[0].with_suffix(".REPORT.md"))
    assert "Read this Lex-Hermes backoffice report" in issue["maintainer_prompt"]
    assert any(item["relative"] == "tools/lexitool_tool.py" for item in issue["source_candidates"])
    assert "python_executable" in issue["diagnostic_context"]
    assert issue["tool_args_redacted"]["prompt"]["redacted"] is True
    assert issue["artifact_paths"] == ["/workingfile/140. 颐保银团/上海颐保综合授信额度核定.pdf"]
    report = files[0].with_suffix(".REPORT.md")
    assert report.exists()
    report_text = report.read_text(encoding="utf-8")
    assert "## Source Candidates" in report_text
    assert "## Maintainer Prompt" in report_text
    assert "读取这份文件的全部内容" not in report_text
    note = json.loads(transformed)["backoffice_issue"]
    assert note["report_path"] == str(report)


def test_repeated_issue_deduplicates_by_fingerprint(tmp_path, monkeypatch):
    plugin = _load_plugin()
    issue_dir = tmp_path / "issues"
    monkeypatch.setattr(plugin, "_FALLBACK_ISSUE_DIR", issue_dir)
    monkeypatch.setattr(plugin, "_project_root_from_args", lambda _args: None)

    result = {"error": "insert_paragraph_block() got an unexpected keyword argument 'after_para'"}
    for _ in range(2):
        plugin._on_transform_tool_result(
            tool_name="lex_edit",
            args={"path": "/workingfile/141. 特雷通变更/a.docx", "new_text": "sensitive legal text"},
            result=result,
            session_id="sess_1",
        )

    files = list(issue_dir.glob("*.json"))
    assert len(files) == 1
    issue = json.loads(files[0].read_text(encoding="utf-8"))
    assert issue["occurrences"] == 2
    assert issue["tool_args_redacted"]["new_text"]["redacted"] is True
    assert files[0].with_suffix(".REPORT.md").exists()


def test_ordinary_user_validation_error_is_not_recorded(tmp_path, monkeypatch):
    plugin = _load_plugin()
    issue_dir = tmp_path / "issues"
    monkeypatch.setattr(plugin, "_FALLBACK_ISSUE_DIR", issue_dir)

    transformed = plugin._on_transform_tool_result(
        tool_name="lex_edit",
        args={"path": "/workingfile/example.docx"},
        result={"error": "targets is required for replace_all"},
    )

    assert transformed is None
    assert not issue_dir.exists()


def test_kanban_terminal_workspace_failure_creates_backoffice_issue(tmp_path, monkeypatch):
    plugin = _load_plugin()
    issue_dir = tmp_path / "issues"
    monkeypatch.setattr(plugin, "_FALLBACK_ISSUE_DIR", issue_dir)
    monkeypatch.setattr(plugin, "_project_root_from_args", lambda _args: None)
    monkeypatch.setenv("HERMES_KANBAN_TASK", "t_workspace")

    transformed = plugin._on_transform_tool_result(
        tool_name="terminal",
        args={
            "cmd": (
                "find '/workingfile/106. 国金资管 - 欢乐颂' -type f "
                "|| echo Workspace empty"
            )
        },
        result={"error": "Workspace empty — Source files not found under /workingfile/106. 国金资管 - 欢乐颂"},
        session_id="coord-session",
    )

    files = list(issue_dir.glob("*.json"))
    assert len(files) == 1
    issue = json.loads(files[0].read_text(encoding="utf-8"))
    assert issue["category"] == "workflow"
    assert issue["severity"] == "high"
    assert issue["tool_name"] == "terminal"
    assert "/workingfile/106. 国金资管 - 欢乐颂" in issue["artifact_paths"]
    assert "backoffice_issue" in json.loads(transformed)
