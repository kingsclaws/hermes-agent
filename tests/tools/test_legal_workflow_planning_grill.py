import json

from tools.legal_workflow_tool import (
    _record_planning_intake,
    _transaction_structure_status,
    _handle_legal_workflow,
)


def _project(tmp_path):
    root = tmp_path / "legal-project"
    (root / ".hermes-project").mkdir(parents=True)
    return root


def _complete_task_brief():
    return {
        "task_goal": "修订贷款合同并交付给客户审阅。",
        "source_materials": "TS、批复、客户反馈清单、原合同模板。",
        "document_scope": "全文逐段处理，附件只读不改。",
        "format_requirements": "保留模板字体字号、编号、页眉页脚和定义术语格式。",
        "revision_trace_policy": "必须 track changes，禁止整段替换；删除先 comment 标记。",
        "review_granularity": "逐段修订，完成每节后 lex_read 读回核对。",
        "delivery_outputs": "docx、html 报告、md 工作稿、修订清单和证据覆盖表。",
        "interaction_policy": "商业条件不明、批量替换、删除正文、OCR失败时先问用户。",
    }


def test_planning_status_requires_detailed_task_brief_before_confirmation(tmp_path):
    root = _project(tmp_path)
    result = _record_planning_intake(
        {
            "structure_tier": "minimal",
            "parties": [{"contract": "贷款合同", "role": "借款人", "entity": "目标公司"}],
            "terms": [{"term": "贷款", "definition": "本项目融资", "usage": "全文", "exclusion": ""}],
            "question": "基础交易结构是什么？",
            "answer": "先按 minimal 建立。",
        },
        str(root),
    )
    assert result["ok"] is True

    status = _transaction_structure_status(str(root))

    assert status["missing"] == ["task_brief.task_goal"]
    assert "交付目标" in status["next_question"]
    assert "format_requirements" in status["task_brief_missing"]
    assert "修订痕迹" in status["task_brief_questions"]["revision_trace_policy"]


def test_planning_intake_persists_task_brief_into_artifact_and_status(tmp_path):
    root = _project(tmp_path)
    result = _record_planning_intake(
        {
            "structure_tier": "minimal",
            "parties": [{"contract": "贷款合同", "role": "借款人", "entity": "目标公司"}],
            "terms": [{"term": "贷款", "definition": "本项目融资", "usage": "全文", "exclusion": ""}],
            "task_brief": _complete_task_brief(),
            "confirmed": True,
            "confirmed_by": "user",
        },
        str(root),
    )

    assert result["ok"] is True
    status = result["planning_status"]
    assert status["confirmed"] is True
    assert status["missing"] == []

    artifact = (root / "交易结构与术语表.md").read_text(encoding="utf-8")
    assert "本次任务执行约定" in artifact
    assert "必须 track changes" in artifact
    assert "页眉页脚" in artifact


def test_legal_workflow_start_blocks_until_planning_grill_is_confirmed(tmp_path, monkeypatch):
    home = tmp_path / ".hermes"
    home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    root = _project(tmp_path)

    out = _handle_legal_workflow({
        "action": "start",
        "project_dir": str(root),
        "workflow_type": "document_drafting",
    })
    data = json.loads(out)

    assert data["ok"] is False
    assert data["blocked"] is True
    assert data["error"] == "planning_grill_gate blocked legal workflow execution."
    assert data["interaction_contract"]["ask_with"] == "clarify"
    assert data["next_question"]
