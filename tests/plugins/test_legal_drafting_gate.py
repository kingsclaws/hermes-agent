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
