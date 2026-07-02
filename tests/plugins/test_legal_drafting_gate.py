from __future__ import annotations

import importlib.util
import json
from pathlib import Path


def _load_plugin():
    plugin_path = (
        Path(__file__).resolve().parents[2]
        / "plugins"
        / "legal-drafting-gate"
        / "__init__.py"
    )
    spec = importlib.util.spec_from_file_location("legal_drafting_gate", plugin_path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


def test_blocks_lex_edit_without_style_memo_and_revision_plan(tmp_path):
    plugin = _load_plugin()
    project = tmp_path / "workingfile" / "140. Test"
    (project / ".hermes-project").mkdir(parents=True)
    doc = project / "draft.docx"
    doc.write_bytes(b"")

    result = plugin._on_pre_tool_call("lex_edit", {"path": str(doc), "op": "replace"})

    assert result["action"] == "block"
    assert "style-memo.md" in result["message"]
    assert "revision-plan.md" in result["message"]


def test_allows_lex_edit_when_prereqs_exist(tmp_path):
    plugin = _load_plugin()
    project = tmp_path / "workingfile" / "140. Test"
    drafting = project / ".hermes-project" / "drafting"
    drafting.mkdir(parents=True)
    (drafting / "style-memo.md").write_text("style", encoding="utf-8")
    (drafting / "revision-plan.md").write_text("plan", encoding="utf-8")
    doc = project / "draft.docx"
    doc.write_bytes(b"")

    assert plugin._on_pre_tool_call("lex_edit", {"path": str(doc), "op": "replace"}) is None


def test_bypass_requires_reason_and_writes_audit_log(tmp_path):
    plugin = _load_plugin()
    project = tmp_path / "workingfile" / "140. Test"
    (project / ".hermes-project").mkdir(parents=True)
    doc = project / "draft.docx"
    doc.write_bytes(b"")

    blocked = plugin._on_pre_tool_call(
        "lex_edit",
        {"path": str(doc), "op": "replace", "bypass_legal_drafting_gate": True},
    )
    assert blocked["action"] == "block"

    allowed = plugin._on_pre_tool_call(
        "lex_edit",
        {
            "path": str(doc),
            "op": "replace",
            "bypass_legal_drafting_gate": True,
            "bypass_reason": "User explicitly approved this one edit.",
        },
        session_id="sess",
        task_id="task",
    )
    assert allowed is None
    log = json.loads((project / ".hermes-project" / "drafting" / "gate-bypass-log.json").read_text(encoding="utf-8"))
    assert log["bypasses"][0]["reason"] == "User explicitly approved this one edit."


def test_read_only_ref_ops_are_not_blocked(tmp_path):
    plugin = _load_plugin()
    project = tmp_path / "workingfile" / "140. Test"
    (project / ".hermes-project").mkdir(parents=True)
    doc = project / "draft.docx"
    doc.write_bytes(b"")

    assert plugin._on_pre_tool_call("lex_ref", {"path": str(doc), "op": "audit"}) is None


def test_lex_master_blocks_direct_project_execution(monkeypatch, tmp_path):
    plugin = _load_plugin()
    monkeypatch.setenv("HERMES_PROFILE", "lex-master")
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "lex-master"))

    result = plugin._on_pre_tool_call("lex_edit", {"path": "/workingfile/140. Test/a.docx"})

    assert result["action"] == "block"
    assert "lex_master_route" in result["message"]


def test_lex_master_allows_route_tool(monkeypatch):
    plugin = _load_plugin()
    monkeypatch.setenv("HERMES_PROFILE", "lex-master")

    assert plugin._on_pre_tool_call("lex_master_route", {"action": "dispatch"}) is None


def test_lex_master_allows_direct_ocr_for_project_identification(monkeypatch):
    plugin = _load_plugin()
    monkeypatch.setenv("HERMES_PROFILE", "lex-master")

    assert plugin._on_pre_tool_call("lex_ocr", {"path": "/workingfile/140. Test/a.pdf"}) is None


def test_lex_master_bypass_requires_reason_and_writes_audit_log(monkeypatch, tmp_path):
    plugin = _load_plugin()
    home = tmp_path / "lex-master"
    monkeypatch.setenv("HERMES_PROFILE", "lex-master")
    monkeypatch.setenv("HERMES_HOME", str(home))

    blocked = plugin._on_pre_tool_call(
        "lex_edit",
        {"path": "/workingfile/140. Test/a.docx", "bypass_lex_master_route_gate": True},
    )
    assert blocked["action"] == "block"

    allowed = plugin._on_pre_tool_call(
        "lex_edit",
        {
            "path": "/workingfile/140. Test/a.docx",
            "bypass_lex_master_route_gate": True,
            "bypass_reason": "User approved direct diagnostics.",
        },
        session_id="sess",
        task_id="task",
    )
    assert allowed is None
    log = json.loads((home / "master-route-bypass-log.json").read_text(encoding="utf-8"))
    assert log["bypasses"][0]["reason"] == "User approved direct diagnostics."


def test_delivery_gate_blocks_bare_legal_completion_claim():
    plugin = _load_plugin()

    transformed = plugin._on_transform_llm_output(
        "合同修改完成，文件已经更新。"
    )

    assert transformed is not None
    assert "Legal delivery gate blocked" in transformed
    assert "evidence coverage" in transformed


def test_delivery_gate_allows_simple_tooling_completion_status():
    plugin = _load_plugin()

    transformed = plugin._on_transform_llm_output(
        "已完成：lex-master 已开启 lex_ocr，gateway 已重启。"
    )

    assert transformed is None


def test_delivery_gate_allows_future_completion_status():
    plugin = _load_plugin()

    transformed = plugin._on_transform_llm_output(
        "任务已分派。审阅完成后我会整合结果并汇报。"
    )

    assert transformed is None


def test_delivery_gate_allows_completion_with_evidence_coverage():
    plugin = _load_plugin()

    transformed = plugin._on_transform_llm_output(
        "合同修改完成。\n\n"
        "报告路径：/workingfile/report.html；/workingfile/report.docx；/workingfile/report.md\n\n"
        "Evidence coverage:\n- 文件: a.docx\n- 段落: §12-§18\n- 验证: lex_read 读回确认。"
    )

    assert transformed is None


def test_delivery_gate_requires_html_and_docx_report_paths():
    plugin = _load_plugin()

    transformed = plugin._on_transform_llm_output(
        "合同修改完成。\n\nEvidence coverage:\n- 文件: a.docx\n- 段落: §12-§18\n- 验证: lex_read 读回确认。"
    )

    assert transformed is not None
    assert ".html" in transformed
    assert ".docx" in transformed


def test_plugin_runtime_signature_changes_when_plugin_file_changes(tmp_path, monkeypatch):
    import hermes_cli.plugins as plugins

    root = tmp_path / "plugins"
    plugin_dir = root / "legal-drafting-gate"
    plugin_dir.mkdir(parents=True)
    (plugin_dir / "plugin.yaml").write_text(
        "\n".join(
            [
                "name: legal-drafting-gate",
                "version: 0.1.0",
                "description: test",
                "author: test",
            ]
        ),
        encoding="utf-8",
    )
    init_path = plugin_dir / "__init__.py"
    init_path.write_text("def register(ctx):\n    pass\n", encoding="utf-8")

    monkeypatch.setenv("HERMES_BUNDLED_PLUGINS", str(root))
    monkeypatch.setattr(plugins, "_plugin_manager", None)

    before = plugins.plugin_runtime_signature()
    init_path.write_text("def register(ctx):\n    pass\n# changed\n", encoding="utf-8")
    plugins.discover_plugins(force=True)
    after = plugins.plugin_runtime_signature()

    assert before != after
