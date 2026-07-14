"""Persistent legal workflow plans for Lex Hermes."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional
from datetime import datetime, timezone

from hermes_state import SessionDB
from .project_management_tool import resolve_selected_project
from tools.registry import registry, tool_error


def _ok(payload: Dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False)


def _resolve_project(args: dict, parent_agent=None) -> Dict[str, Optional[str]]:
    project_id = None
    project_dir = str(args.get("project_dir") or "").strip() or None
    selected = resolve_selected_project(parent_agent, session_id=getattr(parent_agent, "session_id", None))
    if selected:
        project_id = selected.get("id")
        project_dir = project_dir or selected.get("cwd") or selected.get("path")
    if project_dir:
        project_dir = str(Path(project_dir).expanduser().resolve())
    return {"project_id": project_id, "project_dir": project_dir}


def _existing_session_id(db: SessionDB, parent_agent=None) -> Optional[str]:
    session_id = str(getattr(parent_agent, "session_id", "") or "").strip()
    if not session_id:
        return None
    try:
        return session_id if db.get_session(session_id) else None
    except Exception:
        return None


def _default_contract_workflow_steps(
    *,
    document_path: Optional[str],
    term_sheet_path: Optional[str],
    enable_learning: bool,
    learning_scope: str,
    instructions: Optional[str],
) -> List[Dict[str, Any]]:
    common_input = {
        "document_path": document_path,
        "term_sheet_path": term_sheet_path,
        "enable_learning": enable_learning,
        "learning_scope": learning_scope,
        "instructions": instructions,
    }
    return [
        {
            "id": "read-template",
            "title": "通读合同模板",
            "type": "lex_read",
            "role": "reader",
            "input": {**common_input, "mode": "structure_then_full"},
        },
        {
            "id": "read-ts-and-support",
            "title": "读取 TS 和支持资料",
            "type": "source_read",
            "role": "reader",
            "depends_on": ["read-template"],
            "input": common_input,
        },
        {
            "id": "build-clause-map",
            "title": "建立条款地图和定义表",
            "type": "analysis",
            "role": "planner",
            "depends_on": ["read-template", "read-ts-and-support"],
            "input": common_input,
        },
        {
            "id": "build-ts-matrix",
            "title": "生成 TS 到合同条款矩阵",
            "type": "analysis",
            "role": "planner",
            "depends_on": ["build-clause-map"],
            "input": common_input,
        },
        {
            "id": "draft-revision-plan",
            "title": "生成可执行修订计划",
            "type": "approval_gate",
            "role": "planner",
            "depends_on": ["build-ts-matrix"],
            "requires_approval": True,
            "input": {
                **common_input,
                "output_contract": "structured patch plan; no document mutation before approval",
            },
        },
        {
            "id": "execute-approved-patches",
            "title": "执行已确认的合同修订",
            "type": "lex_edit",
            "role": "drafter",
            "depends_on": ["draft-revision-plan"],
            "input": common_input,
        },
        {
            "id": "xref-audit-and-fix",
            "title": "交叉引用审计和修复",
            "type": "lex_ref",
            "role": "xref",
            "depends_on": ["execute-approved-patches"],
            "input": common_input,
        },
        {
            "id": "format-review",
            "title": "格式、编号、定义一致性复核",
            "type": "review",
            "role": "format_reviewer",
            "depends_on": ["execute-approved-patches"],
            "input": common_input,
        },
        {
            "id": "ts-consistency-review",
            "title": "TS 一致性复核",
            "type": "review",
            "role": "ts_reviewer",
            "depends_on": ["execute-approved-patches"],
            "input": common_input,
        },
        {
            "id": "gate-check-and-deliver",
            "title": "交付检查和交付包",
            "type": "delivery",
            "role": "coordinator",
            "depends_on": ["xref-audit-and-fix", "format-review", "ts-consistency-review"],
            "input": common_input,
        },
    ]


def _default_document_drafting_workflow_steps(
    *,
    document_path: Optional[str],
    term_sheet_path: Optional[str],
    chunk_size: int,
    enable_learning: bool,
    learning_scope: str,
    instructions: Optional[str],
) -> List[Dict[str, Any]]:
    common_input = {
        "document_path": document_path,
        "term_sheet_path": term_sheet_path,
        "chunk_size": max(int(chunk_size or 12), 1),
        "enable_learning": enable_learning,
        "learning_scope": learning_scope,
        "instructions": instructions,
    }
    return [
        {
            "id": "read-template-structure",
            "title": "通读模板结构和段落统计",
            "type": "lex_read",
            "role": "coordinator",
            "input": {**common_input, "mode": "structure_and_stats"},
        },
        {
            "id": "read-project-sources",
            "title": "读取 TS 和项目基础资料",
            "type": "source_read",
            "role": "coordinator",
            "depends_on": ["read-template-structure"],
            "input": common_input,
        },
        {
            "id": "build-paragraph-production-map",
            "title": "建立逐段制作地图",
            "type": "analysis",
            "role": "coordinator",
            "depends_on": ["read-template-structure", "read-project-sources"],
            "input": {
                **common_input,
                "output_contract": "paragraph chunks with required source checks; every paragraph assigned exactly once",
            },
        },
        {
            "id": "iterative-drafting",
            "title": "逐段制作并逐段读回核对",
            "type": "kanban_iterative_drafting",
            "role": "drafter",
            "depends_on": ["build-paragraph-production-map"],
            "input": {
                **common_input,
                "task_type": "draft_iterative",
                "output_contract": "for each chunk: lex_read before, lex_edit if needed, lex_read after, verification log",
            },
        },
        {
            "id": "targeted-proofread-after-drafting",
            "title": "制作后逐段校对",
            "type": "lex_proofread",
            "role": "proofread_reviewer_pool",
            "depends_on": ["iterative-drafting"],
            "input": {
                **common_input,
                "path": document_path,
                "review_types": ["content", "format", "xref"],
                "target": "full_document_after_drafting",
            },
        },
        {
            "id": "final-gate-before-delivery",
            "title": "交付前门禁",
            "type": "lex_gate_check",
            "role": "coordinator",
            "depends_on": ["targeted-proofread-after-drafting"],
            "input": {**common_input, "gate": "drafting_delivery"},
        },
    ]


def _default_translation_workflow_steps(
    *,
    bilingual_path: Optional[str],
    source_path: Optional[str],
    translation_path: Optional[str],
    glossary_path: Optional[str],
    sop_path: Optional[str],
    sop_overrides: Optional[str],
    domain_terms: Optional[Dict[str, Any]],
    enable_learning: bool,
    learning_scope: str,
    instructions: Optional[str],
) -> List[Dict[str, Any]]:
    common_input = {
        "bilingual_path": bilingual_path,
        "source_path": source_path,
        "translation_path": translation_path,
        "glossary_path": glossary_path,
        "sop_path": sop_path,
        "sop_overrides": sop_overrides,
        "domain_terms": domain_terms or {},
        "enable_learning": enable_learning,
        "learning_scope": learning_scope,
        "instructions": instructions,
        "source_language": "Chinese",
        "target_language": "English",
    }
    return [
        {
            "id": "read-and-map-bilingual-text",
            "title": "读取文件并建立中英对照地图",
            "type": "lex_read",
            "role": "translation_coordinator",
            "input": {**common_input, "mode": "structure_then_targeted_ranges"},
        },
        {
            "id": "extract-defined-terms",
            "title": "抽取定义词、主体名称和术语表",
            "type": "analysis",
            "role": "translation_coordinator",
            "depends_on": ["read-and-map-bilingual-text"],
            "input": common_input,
        },
        {
            "id": "parallel-translation-review",
            "title": "分段中英文翻译质量核对",
            "type": "lex_translation_review",
            "role": "lex-reviewer-translation",
            "depends_on": ["extract-defined-terms"],
            "input": {**common_input, "chunk_size": 120},
        },
        {
            "id": "legal-effect-review",
            "title": "法律效果偏差复核",
            "type": "review",
            "role": "senior_legal_reviewer",
            "depends_on": ["parallel-translation-review"],
            "input": {
                **common_input,
                "focus": "obligations, conditions, discretion, negation, amounts, dates, notice periods",
            },
        },
        {
            "id": "aggregate-findings",
            "title": "汇总问题清单并去重分级",
            "type": "analysis",
            "role": "translation_coordinator",
            "depends_on": ["parallel-translation-review", "legal-effect-review"],
            "input": common_input,
        },
        {
            "id": "approval-before-edits",
            "title": "修改前人工确认",
            "type": "approval_gate",
            "role": "translation_coordinator",
            "depends_on": ["aggregate-findings"],
            "requires_approval": True,
            "input": {
                **common_input,
                "output_contract": "issue list only; no lex_edit before approval",
            },
        },
        {
            "id": "apply-approved-translation-fixes",
            "title": "执行已确认的翻译修订",
            "type": "lex_edit",
            "role": "drafter",
            "depends_on": ["approval-before-edits"],
            "input": common_input,
        },
        {
            "id": "final-translation-gate",
            "title": "最终翻译质量门禁",
            "type": "lex_gate_check",
            "role": "coordinator",
            "depends_on": ["apply-approved-translation-fixes"],
            "input": {**common_input, "gate": "translation"},
        },
    ]


def _default_proofread_workflow_steps(
    *,
    document_path: Optional[str],
    term_sheet_path: Optional[str],
    review_types: List[str],
    chunk_size: int,
    enable_learning: bool,
    learning_scope: str,
    instructions: Optional[str],
) -> List[Dict[str, Any]]:
    normalized_review_types = review_types or ["content", "format", "xref"]
    common_input = {
        "document_path": document_path,
        "term_sheet_path": term_sheet_path,
        "review_types": normalized_review_types,
        "chunk_size": max(int(chunk_size or 180), 50),
        "enable_learning": enable_learning,
        "learning_scope": learning_scope,
        "instructions": instructions,
    }
    return [
        {
            "id": "read-document-map",
            "title": "读取全文结构和段落统计",
            "type": "lex_read",
            "role": "proofread_coordinator",
            "input": {**common_input, "mode": "structure_and_stats"},
        },
        {
            "id": "build-paragraph-chunks",
            "title": "按标题和段落建立校对分块",
            "type": "analysis",
            "role": "proofread_coordinator",
            "depends_on": ["read-document-map"],
            "input": {
                **common_input,
                "output_contract": "chunk map with paragraph ranges; every paragraph assigned exactly once",
            },
        },
        {
            "id": "parallel-proofread-review",
            "title": "分段并行校对审阅",
            "type": "lex_proofread",
            "role": "proofread_reviewer_pool",
            "depends_on": ["build-paragraph-chunks"],
            "input": {
                **common_input,
                "path": document_path,
                "review_types": normalized_review_types,
            },
        },
        {
            "id": "normalize-findings",
            "title": "按段落汇总、去重和分级问题",
            "type": "analysis",
            "role": "proofread_coordinator",
            "depends_on": ["parallel-proofread-review"],
            "input": {
                **common_input,
                "output_contract": "structured findings grouped by paragraph range, severity, issue type, and proposed fix",
            },
        },
        {
            "id": "approval-before-proofread-edits",
            "title": "修改前人工确认",
            "type": "approval_gate",
            "role": "proofread_coordinator",
            "depends_on": ["normalize-findings"],
            "requires_approval": True,
            "input": {
                **common_input,
                "output_contract": "issue list only; no lex_edit before approval",
            },
        },
        {
            "id": "apply-approved-proofread-fixes",
            "title": "执行已确认的校对修订",
            "type": "lex_edit",
            "role": "drafter",
            "depends_on": ["approval-before-proofread-edits"],
            "input": common_input,
        },
        {
            "id": "targeted-recheck-edited-paragraphs",
            "title": "复核已修改段落",
            "type": "lex_proofread",
            "role": "proofread_reviewer_pool",
            "depends_on": ["apply-approved-proofread-fixes"],
            "input": {
                **common_input,
                "path": document_path,
                "review_types": normalized_review_types,
                "target": "edited_paragraphs_only",
            },
        },
        {
            "id": "final-proofread-gate",
            "title": "最终校对交付门禁",
            "type": "lex_gate_check",
            "role": "coordinator",
            "depends_on": ["targeted-recheck-edited-paragraphs"],
            "input": {**common_input, "gate": "proofread"},
        },
    ]


def _normalize_custom_steps(raw_steps: Any) -> List[Dict[str, Any]]:
    if not isinstance(raw_steps, list):
        return []
    normalized: List[Dict[str, Any]] = []
    previous_id: Optional[str] = None
    for index, item in enumerate(raw_steps):
        if not isinstance(item, dict):
            continue
        step_id = str(item.get("id") or f"step-{index + 1}").strip()
        title = str(item.get("title") or step_id).strip()
        step_type = str(item.get("type") or "manual").strip()
        role = str(item.get("role") or "coordinator").strip()
        depends_on = item.get("depends_on")
        if not isinstance(depends_on, list):
            depends_on = [previous_id] if previous_id else []
        input_payload = item.get("input") if isinstance(item.get("input"), dict) else {}
        instructions = str(item.get("instructions") or "").strip()
        if instructions:
            input_payload = {**input_payload, "instructions": instructions}
        normalized.append(
            {
                "id": step_id,
                "step_index": index,
                "title": title,
                "type": step_type,
                "role": role,
                "depends_on": [str(v) for v in depends_on if str(v).strip()],
                "input": input_payload,
                "requires_approval": bool(item.get("requires_approval")),
            }
        )
        previous_id = step_id
    return normalized


def _collect_findings_from_workflow(workflow: Dict[str, Any]) -> List[Dict[str, Any]]:
    findings: List[Dict[str, Any]] = []
    for step in workflow.get("steps") or []:
        result = step.get("result")
        if isinstance(result, str):
            try:
                result = json.loads(result)
            except Exception:
                result = None
        if not isinstance(result, dict):
            continue
        raw = result.get("findings")
        if isinstance(raw, list):
            findings.extend(item for item in raw if isinstance(item, dict))
        learning = result.get("learning")
        if isinstance(learning, dict) and isinstance(learning.get("findings"), list):
            findings.extend(item for item in learning["findings"] if isinstance(item, dict))
        gate_learning = result.get("gate_learning")
        if isinstance(gate_learning, dict) and isinstance(gate_learning.get("findings"), list):
            findings.extend(item for item in gate_learning["findings"] if isinstance(item, dict))
    return findings


def _infer_workflow_type(workflow: Dict[str, Any]) -> str:
    name = str(workflow.get("name") or "").lower()
    instructions = str(workflow.get("instructions") or "").lower()
    step_types = " ".join(str(step.get("type") or "") for step in workflow.get("steps") or [])
    haystack = " ".join([name, instructions, step_types])
    if any(marker in haystack for marker in ("project init", "project_init", "source_digest", "init.synthesis")):
        return "project_init"
    if any(marker in haystack for marker in ("delivery gate", "delivery_gate", "lex_gate_check")):
        return "delivery_gate"
    if any(marker in haystack for marker in ("translation", "翻译", "bilingual", "lex_translation_review")):
        return "translation_quality_review"
    if any(marker in haystack for marker in ("proofread", "校对", "审校", "lex_proofread")):
        return "proofread_review"
    if any(marker in haystack for marker in ("document_drafting", "逐段制作", "draft_iterative", "iterative-drafting")):
        return "document_drafting"
    return "contract_revision"


_ROLE_TO_PROFILE = {
    "coordinator": "lex-coordinator",
    "reader": "lex-coordinator",
    "planner": "lex-coordinator",
    "proofread_coordinator": "lex-coordinator",
    "translation_coordinator": "lex-coordinator",
    "drafter": "lex-drafter",
    "xref": "lex-reviewer-xref",
    "format_reviewer": "lex-reviewer-format",
    "ts_reviewer": "lex-reviewer-ts",
    "senior_legal_reviewer": "lex-reviewer-content",
    "proofread_reviewer_pool": "lex-reviewer-content",
}


def _profile_for_step(step: Dict[str, Any]) -> str:
    role = str(step.get("role") or "").strip()
    if role in _ROLE_TO_PROFILE:
        return _ROLE_TO_PROFILE[role]
    if role.startswith("lex-"):
        return role
    step_type = str(step.get("type") or "").strip()
    if step_type in {"lex_edit", "lex_template_fill"}:
        return "lex-drafter"
    if step_type in {"lex_proofread", "review"}:
        return "lex-reviewer-content"
    if step_type in {"lex_ref", "lex_xref_audit"}:
        return "lex-reviewer-xref"
    return "lex-coordinator"


def _workflow_steps_to_kanban_spec(
    *,
    workflow_id: str,
    steps: List[Dict[str, Any]],
) -> Dict[str, Any]:
    nodes = []
    for step in steps:
        step_id = str(step.get("id") or step.get("step_key") or "").strip()
        if not step_id:
            continue
        kind = "gate" if str(step.get("type") or "") in {"approval_gate", "delivery", "lex_gate_check"} else "worker"
        nodes.append({
            "id": step_id,
            "kind": kind,
            "title": str(step.get("title") or step_id),
            "profile": _profile_for_step(step),
            "requires": [str(item) for item in (step.get("depends_on") or []) if str(item).strip()],
            "output_required": ["status", "evidence", "verification"],
            "input_schema": sorted((step.get("input") or {}).keys()) if isinstance(step.get("input"), dict) else [],
            "timeout_minutes": 30,
        })
    return {
        "id": workflow_id,
        "version": 1,
        "description": "Kanban-backed legal workflow generated from legal_workflow steps.",
        "nodes": nodes,
        "scorecard": {
            "required": ["edit_verification"],
            "block_on_failed_verification": True,
            "block_on_invalid_handoff": True,
        },
    }


def _compile_kanban_execution(
    *,
    workflow_run_id: str,
    workflow_type: str,
    project_dir: str,
    steps: List[Dict[str, Any]],
    params: Dict[str, Any],
) -> Dict[str, Any]:
    if not project_dir:
        return {"ok": False, "error": "project_dir is required for kanban execution."}

    from ..kanban_legal_swarm import compile_workflow

    workflow_id = f"lwf_{workflow_run_id}"
    spec = _workflow_steps_to_kanban_spec(workflow_id=workflow_id, steps=steps)
    workflows_dir = Path(project_dir) / ".hermes-project" / "workflows"
    workflows_dir.mkdir(parents=True, exist_ok=True)
    spec_path = workflows_dir / f"{workflow_id}.yaml"

    try:
        import yaml
    except Exception as exc:
        return {"ok": False, "error": f"PyYAML unavailable: {exc}"}

    spec_path.write_text(yaml.safe_dump(spec, allow_unicode=True, sort_keys=False), encoding="utf-8")
    run = compile_workflow(
        project_dir=project_dir,
        workflow_id=workflow_id,
        run_id=workflow_run_id,
        params={
            **params,
            "workflow_run_id": workflow_run_id,
            "workflow_type": workflow_type,
        },
    )
    return {
        "ok": True,
        "workflow_id": workflow_id,
        "workflow_file": str(spec_path),
        "board": run.board,
        "kanban_run_id": run.run_id,
        "root_task_id": run.root_task_id,
        "node_mappings": {
            key: {
                "kind": mapping.kind,
                "task_ids": mapping.task_ids,
                "profile": mapping.profile,
                "output_required": mapping.output_required,
            }
            for key, mapping in run.node_mappings.items()
        },
    }


def _find_open_workflow(db: SessionDB, *, project: Dict[str, Optional[str]], document_path: Optional[str]) -> Optional[Dict[str, Any]]:
    candidates = db.list_legal_workflows(
        project_id=project.get("project_id"),
        project_dir=project.get("project_dir"),
        limit=20,
    )
    for workflow in candidates:
        if workflow.get("status") in {"completed", "cancelled", "archived"}:
            continue
        if document_path and workflow.get("document_path") and str(workflow.get("document_path")) != document_path:
            continue
        return db.get_legal_workflow(workflow["id"])
    return None


def _workflow_kanban_binding(workflow: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    for step in workflow.get("steps") or []:
        result = step.get("result")
        if isinstance(result, dict) and isinstance(result.get("kanban"), dict):
            return result["kanban"]
    return None


def _read_json_file(path: Path, default: Dict[str, Any]) -> Dict[str, Any]:
    if not path.is_file():
        return dict(default)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else dict(default)
    except Exception:
        return dict(default)


def _write_json_file(path: Path, data: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _as_list(value: Any) -> List[Any]:
    return value if isinstance(value, list) else []


def _table_escape(value: Any) -> str:
    return str(value if value is not None else "").replace("\n", " ").replace("|", "\\|").strip()


def _table_rows(items: List[Any], columns: List[str]) -> List[str]:
    rows = []
    for item in items:
        if not isinstance(item, dict):
            continue
        rows.append("| " + " | ".join(_table_escape(item.get(col, "")) for col in columns) + " |")
    if not rows:
        rows.append("|  | " * len(columns) + "|")
    return rows


_TASK_BRIEF_REQUIRED_KEYS = (
    "task_goal",
    "source_materials",
    "document_scope",
    "format_requirements",
    "revision_trace_policy",
    "review_granularity",
    "delivery_outputs",
    "interaction_policy",
)


_TASK_BRIEF_QUESTIONS = {
    "task_goal": (
        "这次任务的具体交付目标是什么？请说明是起草、修订、proofread、翻译校对、交叉引用修复，"
        "还是其他任务，以及最终要交付给谁。"
    ),
    "source_materials": (
        "本次任务需要依据哪些材料？请列出 TS、批复、清单、原稿、对方反馈、权威来源或业务资料；"
        "如果有优先级，也请说明。"
    ),
    "document_scope": (
        "处理范围是什么？请说明文件路径、章节/页码/段落范围，是否必须全文通读，"
        "以及哪些部分只读不改或暂不处理。"
    ),
    "format_requirements": (
        "格式要求请具体说明：字体/字号、编号层级、表格样式、页眉页脚、中文/英文标点、"
        "占位符、定义术语格式、是否保留原模板风格。"
    ),
    "revision_trace_policy": (
        "修订痕迹怎么处理？请说明是否必须 track changes、是否允许 comment、是否可 highlight/mark、"
        "新增内容是否用特定颜色、删除是否先标记再决定、以及是否禁止整段替换。"
    ),
    "review_granularity": (
        "审阅/制作颗粒度是什么？请说明是一条一条/逐段/逐页/逐文件处理，"
        "每一步是否需要读回核对，以及是否需要先输出修订计划再动手。"
    ),
    "delivery_outputs": (
        "交付物格式是什么？请说明是否需要保留 docx、html、md、修订清单、问题清单、"
        "证据覆盖表、compare/diff、备份文件，以及文件命名规则。"
    ),
    "interaction_policy": (
        "哪些事项需要先问你再做？哪些可以 agent 自主决定？例如商业条件不明、格式冲突、"
        "无法读取文件、批量替换、删除内容、绕过 gate、OCR 失败等。"
    ),
}


def _as_dict(value: Any) -> Dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _task_brief_missing(task_brief: Dict[str, Any]) -> List[str]:
    missing = []
    for key in _TASK_BRIEF_REQUIRED_KEYS:
        value = task_brief.get(key)
        if value in (None, "", [], {}):
            missing.append(key)
    return missing


def _render_transaction_structure_doc(payload: Dict[str, Any]) -> str:
    tier = str(payload.get("structure_tier") or "minimal").strip() or "minimal"
    confirmed_by = str(payload.get("confirmed_by") or "").strip()
    confirmed_at = str(payload.get("confirmed_at") or "").strip()
    terms = _as_list(payload.get("terms"))
    parties = _as_list(payload.get("parties"))
    amounts = _as_list(payload.get("amounts"))
    transaction_files = _as_list(payload.get("transaction_files"))
    format_conventions = payload.get("format_conventions") if isinstance(payload.get("format_conventions"), dict) else {}
    task_brief = _as_dict(payload.get("task_brief"))
    notes = str(payload.get("notes") or "").strip()

    lines = [
        "# 交易结构与术语表",
        "",
        f"<!-- tier: {tier} | confirmed: {confirmed_by or 'unconfirmed'} / {confirmed_at or '-'} -->",
        "",
        "## ① 术语表",
        "",
        "| 术语 | 定义 | 使用场景 | 排除/替代 |",
        "|------|------|----------|----------|",
        *_table_rows(terms, ["term", "definition", "usage", "exclusion"]),
        "",
        "## ② 签署主体",
        "",
        "| 合同 | 角色 | 主体全称 |",
        "|------|------|---------|",
        *_table_rows(parties, ["contract", "role", "entity"]),
        "",
        "## ③ 本次任务执行约定",
        "",
        f"- 任务目标：{_table_escape(task_brief.get('task_goal', ''))}",
        f"- 依据材料：{_table_escape(task_brief.get('source_materials', ''))}",
        f"- 处理范围：{_table_escape(task_brief.get('document_scope', ''))}",
        f"- 格式要求：{_table_escape(task_brief.get('format_requirements', ''))}",
        f"- 修订痕迹策略：{_table_escape(task_brief.get('revision_trace_policy', ''))}",
        f"- 审阅颗粒度：{_table_escape(task_brief.get('review_granularity', ''))}",
        f"- 交付物：{_table_escape(task_brief.get('delivery_outputs', ''))}",
        f"- 需先询问事项：{_table_escape(task_brief.get('interaction_policy', ''))}",
        "",
    ]

    if tier == "full":
        lines.extend([
            "## ④ 关键金额与费率表",
            "",
            "| 项目 | 数值 | 出处/依据 | 备注 |",
            "|------|------|----------|------|",
            *_table_rows(amounts, ["item", "value", "source", "note"]),
            "",
            "## ⑤ 编号/格式约定",
            "",
            f"- 占位符格式：{_table_escape(format_conventions.get('placeholder_format', ''))}",
            f"- 其他格式约定：{_table_escape(format_conventions.get('other', ''))}",
            f"- 编号样式：{_table_escape(format_conventions.get('numbering', ''))}",
            f"- 字体/字号约定：{_table_escape(format_conventions.get('font', ''))}",
            "",
            "## ⑥ 交易文件清单与跨文件引用",
            "",
            "| 文件名 | 角色（主合同/配套/担保/监管等） | 被引用方 |",
            "|--------|------------------------------|---------|",
            *_table_rows(transaction_files, ["file", "role", "referenced_by"]),
            "",
        ])

    if notes:
        lines.extend(["## 备注", "", notes, ""])
    return "\n".join(lines).rstrip() + "\n"


def _append_planning_log(project_dir: str, args: dict, payload: Dict[str, Any]) -> str:
    root = Path(project_dir)
    planning_dir = root / ".hermes-project" / "planning"
    planning_dir.mkdir(parents=True, exist_ok=True)
    log_path = planning_dir / "grill-log.md"
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    question = str(args.get("question") or args.get("clarify_question") or "").strip()
    answer = args.get("answer", args.get("user_response"))
    rationale = str(args.get("rationale") or "").strip()
    changed_keys = [
        key for key in (
            "structure_tier",
            "parties",
            "terms",
            "amounts",
            "format_conventions",
            "task_brief",
            "transaction_files",
            "notes",
        )
        if key in args and args.get(key) not in (None, "")
    ]

    if not log_path.is_file():
        log_path.write_text(
            "# Planning Grill Log\n\n"
            "> Native legal planning intake log. Each entry should correspond to a visible `clarify` question and the persisted answer.\n\n",
            encoding="utf-8",
        )

    lines = [
        f"## {now}",
        "",
        f"- Question: {question or '-'}",
        f"- Answer: {_table_escape(answer) if answer is not None else '-'}",
        f"- Changed fields: {', '.join(changed_keys) if changed_keys else '-'}",
        f"- Confirmed: {bool(args.get('confirmed'))}",
    ]
    if rationale:
        lines.append(f"- Rationale: {rationale}")
    lines.extend([
        "",
        "```json",
        json.dumps({key: payload.get(key) for key in changed_keys}, ensure_ascii=False, indent=2),
        "```",
        "",
    ])
    with log_path.open("a", encoding="utf-8") as handle:
        handle.write("\n".join(lines))
    return str(log_path)


def _write_planning_decision(project_dir: str, payload: Dict[str, Any]) -> Optional[str]:
    if not payload.get("confirmed_by") or not payload.get("confirmed_at"):
        return None
    root = Path(project_dir)
    decisions_dir = root / ".hermes-project" / "decisions"
    decisions_dir.mkdir(parents=True, exist_ok=True)
    decision_path = decisions_dir / "transaction-structure.md"
    tier = str(payload.get("structure_tier") or "minimal").strip() or "minimal"
    confirmed_by = str(payload.get("confirmed_by") or "").strip()
    confirmed_at = str(payload.get("confirmed_at") or "").strip()
    lines = [
        "# Decision: Transaction Structure And Terminology",
        "",
        f"- Status: accepted",
        f"- Tier: {tier}",
        f"- Confirmed by: {confirmed_by}",
        f"- Confirmed at: {confirmed_at}",
        "",
        "## Decision",
        "",
        "Use `交易结构与术语表.md` as the project-level authoritative reference for party names, roles, terminology, key values, formatting conventions, and cross-document relationships.",
        "",
        "## Consequences",
        "",
        "- Drafter and Reviewer agents must consult this artifact before document drafting, review, or delivery.",
        "- Later corrections must update `交易结构与术语表.md`, `.hermes-project/project-facts.json`, and this decision trail.",
        "- Delivery gates may block handoff when the artifact is missing or unconfirmed.",
        "",
    ]
    decision_path.write_text("\n".join(lines), encoding="utf-8")
    return str(decision_path)


def _record_planning_decision_in_state(project_dir: str, payload: Dict[str, Any], decision_path: Optional[str]) -> Dict[str, Any]:
    state_path = Path(project_dir) / ".hermes-project" / "project-state.json"
    if not state_path.is_file() or not payload.get("confirmed_by"):
        return {"ok": False, "skipped": True, "reason": "project-state unavailable or planning not confirmed"}
    try:
        from hermes_cli.project_commands import update_project_state

        return update_project_state(
            project_dir,
            decision=(
                "交易结构与术语表 confirmed as authoritative "
                f"(tier={payload.get('structure_tier') or 'minimal'}, "
                f"confirmed_by={payload.get('confirmed_by')}, "
                f"artifact=交易结构与术语表.md"
                + (f", decision={decision_path}" if decision_path else "")
                + ")."
            ),
        )
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


def _write_planning_bypass_log(project_dir: str, *, reason: str, action: str, session_id: str = "") -> str:
    root = Path(project_dir)
    log_path = root / ".hermes-project" / "planning" / "grill-bypass-log.json"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        data = json.loads(log_path.read_text(encoding="utf-8")) if log_path.is_file() else {}
    except Exception:
        data = {}
    entries = data.setdefault("bypasses", [])
    entries.append({
        "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "action": action,
        "reason": reason,
        "session_id": session_id,
    })
    _write_json_file(log_path, data)
    return str(log_path)


def _planning_grill_gate(project_dir: str, args: dict, *, action: str, parent_agent=None) -> Optional[Dict[str, Any]]:
    """Hard gate before execution: every legal task needs a persisted grill brief."""
    bypass = bool(args.get("bypass_planning_grill_gate"))
    reason = str(args.get("bypass_reason") or args.get("planning_bypass_reason") or "").strip()
    status = _transaction_structure_status(project_dir)
    if status.get("confirmed") and not status.get("missing"):
        return None
    if bypass and reason:
        session_id = str(getattr(parent_agent, "session_id", "") or os.environ.get("HERMES_SESSION_ID", ""))
        return {
            "ok": True,
            "bypassed": True,
            "bypass_log_path": _write_planning_bypass_log(
                project_dir,
                reason=reason,
                action=action,
                session_id=session_id,
            ),
            "planning_status": status,
        }
    return {
        "ok": False,
        "blocked": True,
        "error": "planning_grill_gate blocked legal workflow execution.",
        "missing": status.get("missing", []),
        "next_question": status.get("next_question", ""),
        "planning_status": status,
        "interaction_contract": {
            "ask_with": "clarify",
            "persist_with": "legal_workflow(action='planning_intake')",
            "rule": "Ask one question at a time and persist each answer. Start execution only after confirmed=true.",
            "bypass": "Only if the user explicitly approves; pass bypass_planning_grill_gate=true and bypass_reason.",
        },
    }


def _transaction_structure_status(project_dir: str) -> Dict[str, Any]:
    root = Path(project_dir)
    meta = _read_json_file(root / ".hermes-project" / "project-meta.json", {})
    draft = _read_json_file(root / ".hermes-project" / "planning" / "transaction-structure-draft.json", {})
    md_path = root / "交易结构与术语表.md"
    tier = str(meta.get("structure_tier") or draft.get("structure_tier") or "").strip()
    confirmed = bool(meta.get("structure_confirmed_by") and meta.get("structure_confirmed_at"))
    task_brief = _as_dict(draft.get("task_brief"))
    missing_task_brief = _task_brief_missing(task_brief)

    missing: List[str] = []
    next_question = ""
    if not tier:
        missing.append("structure_tier")
        next_question = "本项目涉及几份合同/法律文件需要起草或修改？1份建议 minimal，2份以上建议 full。"
    elif not _as_list(draft.get("parties")):
        missing.append("parties")
        next_question = "每份合同的签署方分别是谁？请给出正式法定全称和各方角色。"
    elif not _as_list(draft.get("terms")):
        missing.append("terms")
        next_question = "本项目有哪些必须统一或避免使用的核心术语？请说明定义、使用场景和替代规则。"
    elif missing_task_brief:
        key = missing_task_brief[0]
        missing.append(f"task_brief.{key}")
        next_question = _TASK_BRIEF_QUESTIONS.get(key, "请补充本次任务执行约定。")
    elif tier == "full" and not _as_list(draft.get("transaction_files")):
        missing.append("transaction_files")
        next_question = "这些合同之间的引用关系是什么？哪份是主合同，哪些是配套/担保/监管文件？"
    elif tier == "full" and not _as_list(draft.get("amounts")):
        missing.append("amounts")
        next_question = "本项目有哪些关键金额、费率、百分比或日期需要跨文件保持一致？"
    elif tier == "full" and not isinstance(draft.get("format_conventions"), dict):
        missing.append("format_conventions")
        next_question = "文件占位符、编号、字体字号和其他格式约定是什么？"
    elif not confirmed:
        missing.append("user_confirmation")
        next_question = "交易结构与术语表草稿已具备基础内容。请确认是否作为项目级权威参考生效。"

    return {
        "ok": True,
        "project_dir": str(root),
        "artifact_path": str(md_path),
        "draft_path": str(root / ".hermes-project" / "planning" / "transaction-structure-draft.json"),
        "meta_path": str(root / ".hermes-project" / "project-meta.json"),
        "exists": md_path.is_file(),
        "confirmed": confirmed,
        "structure_tier": tier or None,
        "missing": missing,
        "next_question": next_question,
        "task_brief_required_keys": list(_TASK_BRIEF_REQUIRED_KEYS),
        "task_brief_missing": missing_task_brief,
        "task_brief_questions": _TASK_BRIEF_QUESTIONS,
        "draft": draft,
        "meta": {
            "structure_tier": meta.get("structure_tier"),
            "structure_confirmed_by": meta.get("structure_confirmed_by"),
            "structure_confirmed_at": meta.get("structure_confirmed_at"),
        },
    }


def _sync_transaction_structure_facts(project_dir: str, payload: Dict[str, Any]) -> List[Dict[str, Any]]:
    from hermes_cli.project_commands import project_facts

    updates: List[Dict[str, Any]] = []
    for party in _as_list(payload.get("parties")):
        if not isinstance(party, dict):
            continue
        key = ".".join(
            part for part in [
                str(party.get("contract") or "document").strip(),
                str(party.get("role") or "party").strip(),
            ] if part
        )
        result = project_facts(
            project_dir,
            "upsert",
            category="transaction_parties",
            key=key,
            value=party.get("entity"),
            source="legal_workflow.planning_intake",
            confidence="high",
            status="confirmed",
            tags=["transaction_structure"],
        )
        updates.append(result)
    for term in _as_list(payload.get("terms")):
        if not isinstance(term, dict) or not term.get("term"):
            continue
        result = project_facts(
            project_dir,
            "upsert",
            category="transaction_terms",
            key=str(term.get("term")).strip(),
            value={
                "definition": term.get("definition", ""),
                "usage": term.get("usage", ""),
                "exclusion": term.get("exclusion", ""),
            },
            source="legal_workflow.planning_intake",
            confidence="high",
            status="confirmed",
            tags=["transaction_structure"],
        )
        updates.append(result)
    for amount in _as_list(payload.get("amounts")):
        if not isinstance(amount, dict) or not amount.get("item"):
            continue
        result = project_facts(
            project_dir,
            "upsert",
            category="transaction_amounts",
            key=str(amount.get("item")).strip(),
            value={
                "value": amount.get("value", ""),
                "source": amount.get("source", ""),
                "note": amount.get("note", ""),
            },
            source="legal_workflow.planning_intake",
            confidence="high",
            status="confirmed",
            tags=["transaction_structure"],
        )
        updates.append(result)
    task_brief = _as_dict(payload.get("task_brief"))
    for key in _TASK_BRIEF_REQUIRED_KEYS:
        value = task_brief.get(key)
        if value in (None, "", [], {}):
            continue
        result = project_facts(
            project_dir,
            "upsert",
            category="task_brief",
            key=key,
            value=value,
            source="legal_workflow.planning_intake",
            confidence="high",
            status="confirmed" if payload.get("confirmed_by") else "draft",
            tags=["planning_grill", "task_execution"],
        )
        updates.append(result)
    return updates


def _record_planning_intake(args: dict, project_dir: str) -> Dict[str, Any]:
    root = Path(project_dir)
    planning_dir = root / ".hermes-project" / "planning"
    draft_path = planning_dir / "transaction-structure-draft.json"
    payload = _read_json_file(draft_path, {})
    for key in (
        "structure_tier",
        "parties",
        "terms",
        "amounts",
        "format_conventions",
        "task_brief",
        "transaction_files",
        "notes",
    ):
        if key in args and args.get(key) not in (None, ""):
            payload[key] = args[key]
    payload["updated_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    confirmed = bool(args.get("confirmed"))
    confirmed_by = str(args.get("confirmed_by") or "").strip()
    if confirmed and not confirmed_by:
        return {"ok": False, "error": "confirmed_by is required when confirmed=true."}
    if confirmed:
        payload["confirmed_by"] = confirmed_by
        payload["confirmed_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    _write_json_file(draft_path, payload)
    md_path = root / "交易结构与术语表.md"
    md_path.write_text(_render_transaction_structure_doc(payload), encoding="utf-8")
    log_path = _append_planning_log(project_dir, args, payload)

    meta_path = root / ".hermes-project" / "project-meta.json"
    meta = _read_json_file(meta_path, {})
    decision_path = None
    state_update = {"ok": False, "skipped": True}
    if confirmed:
        meta["structure_tier"] = payload.get("structure_tier") or "minimal"
        meta["structure_confirmed_by"] = payload["confirmed_by"]
        meta["structure_confirmed_at"] = payload["confirmed_at"]
        _write_json_file(meta_path, meta)
        decision_path = _write_planning_decision(project_dir, payload)
        state_update = _record_planning_decision_in_state(project_dir, payload, decision_path)

    fact_updates = _sync_transaction_structure_facts(project_dir, payload)
    status = _transaction_structure_status(project_dir)
    return {
        "ok": True,
        "status": "confirmed" if confirmed else "drafted",
        "artifact_path": str(md_path),
        "planning_log_path": log_path,
        "decision_path": decision_path,
        "draft_path": str(draft_path),
        "meta_path": str(meta_path),
        "fact_updates": fact_updates,
        "state_update": state_update,
        "planning_status": status,
    }


LEGAL_WORKFLOW_SCHEMA = {
    "name": "legal_workflow",
    "description": (
        "Create, inspect, and update persistent legal-document workflow plans. "
        "Use before editing contracts so reading, TS mapping, human approval, "
        "drafting, xref review, and delivery are visible and stateful."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": [
                    "start",
                    "create_plan",
                    "get",
                    "list",
                    "status",
                    "next",
                    "planning_status",
                    "planning_intake",
                    "update_run",
                    "update_step",
                    "finish",
                    "learn_from_run",
                    "list_learning_rules",
                    "approve_learning_rule",
                    "reject_learning_rule",
                    "export_learning_sop",
                ],
            },
            "run_id": {"type": "string"},
            "step_id": {"type": "string"},
            "rule_id": {"type": "string"},
            "name": {"type": "string"},
            "workflow_type": {
                "type": "string",
                "enum": ["contract_revision", "document_drafting", "translation_quality_review", "proofread_review", "project_init", "delivery_gate"],
                "description": "Default workflow template to create when custom steps are not supplied.",
            },
            "learning_scope": {
                "type": "string",
                "enum": ["global"],
                "description": "Learning rule scope. Default: global.",
            },
            "project_dir": {"type": "string"},
            "document_path": {"type": "string"},
            "term_sheet_path": {"type": "string"},
            "bilingual_path": {"type": "string"},
            "source_path": {"type": "string"},
            "translation_path": {"type": "string"},
            "glossary_path": {"type": "string"},
            "sop_path": {
                "type": "string",
                "description": "Optional project/task-specific translation QA SOP file.",
            },
            "sop_overrides": {
                "type": "string",
                "description": "Optional task-specific SOP additions, exceptions, or priority changes.",
            },
            "domain_terms": {
                "type": "object",
                "description": "Optional task-specific term map for translation QA.",
            },
            "enable_learning": {
                "type": "boolean",
                "description": "Whether workflow learning is enabled. Default: true.",
            },
            "bypass_planning_grill_gate": {
                "type": "boolean",
                "description": "Emergency override for legal_workflow(action='start') planning grill gate. Use only after explicit user approval.",
            },
            "planning_bypass_reason": {
                "type": "string",
                "description": "Audit reason required when bypass_planning_grill_gate=true.",
            },
            "review_types": {
                "type": "array",
                "items": {
                    "type": "string",
                    "enum": [
                        "content",
                        "format",
                        "ts",
                        "xref",
                        "translation",
                        "review_content",
                        "review_format",
                        "review_ts",
                        "review_xref",
                        "review_translation",
                    ],
                },
                "description": "Proofread review lanes to run. Default: content, format, xref.",
            },
            "chunk_size": {
                "type": "integer",
                "description": "Max paragraphs per proofread chunk. Default: 180.",
            },
            "instructions": {"type": "string"},
            "status": {"type": "string"},
            "steps": {
                "type": "array",
                "description": "Optional UI-edited workflow atoms/steps to persist instead of the default template.",
                "items": {
                    "type": "object",
                    "properties": {
                        "id": {"type": "string"},
                        "title": {"type": "string"},
                        "type": {"type": "string"},
                        "role": {"type": "string"},
                        "depends_on": {"type": "array", "items": {"type": "string"}},
                        "instructions": {"type": "string"},
                        "requires_approval": {"type": "boolean"},
                        "input": {"type": "object"},
                    },
                },
            },
            "step": {"type": "object"},
            "reuse_existing": {
                "type": "boolean",
                "description": "When action=start, resume an open workflow for the same project/document. Default: true.",
            },
            "confirmed": {
                "type": "boolean",
                "description": "For planning_intake: whether the user confirmed the transaction structure artifact as authoritative.",
            },
            "confirmed_by": {
                "type": "string",
                "description": "For planning_intake: user/person who confirmed the planning artifact.",
            },
            "structure_tier": {
                "type": "string",
                "enum": ["minimal", "full"],
                "description": "Transaction structure planning tier. minimal=single document; full=multi-document/cross-reference matter.",
            },
            "parties": {
                "type": "array",
                "description": "Signing party rows: {contract, role, entity}.",
                "items": {
                    "type": "object",
                    "properties": {
                        "contract": {"type": "string"},
                        "role": {"type": "string"},
                        "entity": {"type": "string"},
                    },
                },
            },
            "terms": {
                "type": "array",
                "description": "Terminology rows: {term, definition, usage, exclusion}.",
                "items": {
                    "type": "object",
                    "properties": {
                        "term": {"type": "string"},
                        "definition": {"type": "string"},
                        "usage": {"type": "string"},
                        "exclusion": {"type": "string"},
                    },
                },
            },
            "amounts": {
                "type": "array",
                "description": "Key amount/rate/date rows: {item, value, source, note}. Required for full tier.",
                "items": {
                    "type": "object",
                    "properties": {
                        "item": {"type": "string"},
                        "value": {"type": "string"},
                        "source": {"type": "string"},
                        "note": {"type": "string"},
                    },
                },
            },
            "format_conventions": {
                "type": "object",
                "description": "Format conventions: placeholder_format, other, numbering, font.",
            },
            "task_brief": {
                "type": "object",
                "description": (
                    "Per-task grill/intake answers. Required keys before execution: "
                    "task_goal, source_materials, document_scope, format_requirements, "
                    "revision_trace_policy, review_granularity, delivery_outputs, interaction_policy."
                ),
                "properties": {
                    "task_goal": {"type": "string"},
                    "source_materials": {"type": "string"},
                    "document_scope": {"type": "string"},
                    "format_requirements": {"type": "string"},
                    "revision_trace_policy": {"type": "string"},
                    "review_granularity": {"type": "string"},
                    "delivery_outputs": {"type": "string"},
                    "interaction_policy": {"type": "string"},
                },
            },
            "transaction_files": {
                "type": "array",
                "description": "Transaction document map rows: {file, role, referenced_by}. Required for full tier.",
                "items": {
                    "type": "object",
                    "properties": {
                        "file": {"type": "string"},
                        "role": {"type": "string"},
                        "referenced_by": {"type": "string"},
                    },
                },
            },
            "notes": {"type": "string"},
            "question": {
                "type": "string",
                "description": "For planning_intake: the clarify question that produced this intake update.",
            },
            "answer": {
                "type": "string",
                "description": "For planning_intake: the user's clarify answer, preserved in the planning grill log.",
            },
            "rationale": {
                "type": "string",
                "description": "For planning_intake: why this answer changes the plan or artifact.",
            },
            "limit": {"type": "integer"},
        },
        "required": ["action"],
    },
}


def _handle_legal_workflow(args: dict, **kwargs) -> str:
    action = str(args.get("action") or "").strip()
    if not action:
        return tool_error("legal_workflow action is required.")

    db = SessionDB()
    parent_agent = kwargs.get("parent_agent")

    if action in {"start", "create_plan"}:
        project = _resolve_project(args, parent_agent=parent_agent)
        if action == "start" and project.get("project_dir"):
            gate = _planning_grill_gate(
                project["project_dir"] or "",
                args,
                action=action,
                parent_agent=parent_agent,
            )
            if gate is not None and not gate.get("bypassed"):
                return json.dumps({"ok": False, **gate}, ensure_ascii=False)
        document_path = str(args.get("document_path") or "").strip() or None
        term_sheet_path = str(args.get("term_sheet_path") or "").strip() or None
        bilingual_path = str(args.get("bilingual_path") or "").strip() or None
        source_path = str(args.get("source_path") or "").strip() or None
        translation_path = str(args.get("translation_path") or "").strip() or None
        glossary_path = str(args.get("glossary_path") or "").strip() or None
        sop_path = str(args.get("sop_path") or "").strip() or None
        sop_overrides = str(args.get("sop_overrides") or "").strip() or None
        domain_terms = args.get("domain_terms") if isinstance(args.get("domain_terms"), dict) else {}
        enable_learning = bool(args.get("enable_learning", True))
        learning_scope = str(args.get("learning_scope") or "global").strip()
        review_types = [
            str(v).strip().replace("review_", "")
            for v in (args.get("review_types") or [])
            if str(v).strip()
        ]
        chunk_size = int(args.get("chunk_size") or 180)
        instructions = str(args.get("instructions") or "").strip() or None
        workflow_type = str(args.get("workflow_type") or "contract_revision").strip()
        if action == "start" and bool(args.get("reuse_existing", True)):
            existing = _find_open_workflow(db, project=project, document_path=document_path)
            if existing:
                return _ok({
                    "status": "resumed",
                    "workflow": existing,
                    "kanban": _workflow_kanban_binding(existing),
                })
        default_name_by_type = {
            "contract_revision": "法律文书修订 workflow",
            "document_drafting": "法律文书逐段制作 workflow",
            "delivery_gate": "法律交付门禁 workflow",
            "project_init": "项目初始化 workflow",
            "proofread_review": "法律文书逐段校对 workflow",
            "translation_quality_review": "中英文翻译质量核对 workflow",
        }
        default_name = default_name_by_type.get(workflow_type, "法律文书 workflow")
        name = str(args.get("name") or "").strip() or default_name
        steps = _normalize_custom_steps(args.get("steps"))
        if not steps:
            if workflow_type == "translation_quality_review":
                steps = _default_translation_workflow_steps(
                    bilingual_path=bilingual_path or document_path,
                    source_path=source_path,
                    translation_path=translation_path,
                    glossary_path=glossary_path or term_sheet_path,
                    sop_path=sop_path,
                    sop_overrides=sop_overrides,
                    domain_terms=domain_terms,
                    enable_learning=enable_learning,
                    learning_scope=learning_scope,
                    instructions=instructions,
                )
            elif workflow_type == "document_drafting":
                steps = _default_document_drafting_workflow_steps(
                    document_path=document_path,
                    term_sheet_path=term_sheet_path,
                    chunk_size=chunk_size,
                    enable_learning=enable_learning,
                    learning_scope=learning_scope,
                    instructions=instructions,
                )
            elif workflow_type == "proofread_review":
                steps = _default_proofread_workflow_steps(
                    document_path=document_path,
                    term_sheet_path=term_sheet_path,
                    review_types=review_types,
                    chunk_size=chunk_size,
                    enable_learning=enable_learning,
                    learning_scope=learning_scope,
                    instructions=instructions,
                )
            else:
                steps = _default_contract_workflow_steps(
                    document_path=document_path,
                    term_sheet_path=term_sheet_path,
                    enable_learning=enable_learning,
                    learning_scope=learning_scope,
                    instructions=instructions,
                )
        run_id = db.create_legal_workflow(
            name=name,
            project_id=project["project_id"],
            project_dir=project["project_dir"],
            document_path=document_path,
            term_sheet_path=term_sheet_path,
            instructions=instructions,
            created_by_session_id=_existing_session_id(db, parent_agent),
            steps=steps,
        )
        workflow = db.get_legal_workflow(run_id)
        kanban = None
        if action == "start":
            planning_status = (
                _transaction_structure_status(project["project_dir"])
                if project.get("project_dir") else {}
            )
            task_brief = _as_dict(_as_dict(planning_status.get("draft")).get("task_brief"))
            kanban = _compile_kanban_execution(
                workflow_run_id=run_id,
                workflow_type=workflow_type,
                project_dir=project["project_dir"] or "",
                steps=steps,
                params={
                    "document_path": document_path,
                    "term_sheet_path": term_sheet_path,
                    "bilingual_path": bilingual_path,
                    "source_path": source_path,
                    "translation_path": translation_path,
                    "glossary_path": glossary_path,
                    "sop_path": sop_path,
                    "instructions": instructions,
                    "chunk_size": chunk_size,
                    "task_brief": task_brief,
                    "planning_artifact_path": planning_status.get("artifact_path"),
                },
            )
            first_step = (workflow.get("steps") or [None])[0]
            if first_step and kanban:
                db.update_legal_workflow_step(
                    first_step["id"],
                    result={"kanban": kanban},
                )
                workflow = db.get_legal_workflow(run_id)
            db.update_legal_workflow(run_id, status="running" if kanban and kanban.get("ok") else "blocked")
            workflow = db.get_legal_workflow(run_id)
        return _ok({
            "status": "started" if action == "start" else "created",
            "workflow": workflow,
            "kanban": kanban,
        })

    if action == "get":
        run_id = str(args.get("run_id") or "").strip()
        if not run_id:
            return tool_error("run_id is required for legal_workflow get.")
        workflow = db.get_legal_workflow(run_id)
        if not workflow:
            return tool_error(f"legal workflow not found: {run_id}")
        return _ok({"status": "ok", "workflow": workflow})

    if action == "list":
        project = _resolve_project(args, parent_agent=parent_agent)
        workflows = db.list_legal_workflows(
            project_id=project["project_id"],
            project_dir=project["project_dir"],
            limit=int(args.get("limit") or 20),
        )
        return _ok({"status": "ok", "workflows": workflows})

    if action == "status":
        run_id = str(args.get("run_id") or "").strip()
        if not run_id:
            return tool_error("run_id is required for legal_workflow status.")
        workflow = db.get_legal_workflow(run_id)
        if not workflow:
            return tool_error(f"legal workflow not found: {run_id}")
        kanban = _workflow_kanban_binding(workflow)
        kanban_status = None
        if kanban and kanban.get("board") and kanban.get("root_task_id"):
            try:
                from hermes_cli import kanban_db as kb
                from ..kanban_legal_swarm import run_status

                conn = kb.connect(board=str(kanban["board"]))
                try:
                    kanban_status = run_status(conn, str(kanban["root_task_id"]))
                finally:
                    conn.close()
            except Exception as exc:
                kanban_status = {"ok": False, "error": str(exc)}
        return _ok({"status": "ok", "workflow": workflow, "kanban": kanban, "kanban_status": kanban_status})

    if action == "next":
        run_id = str(args.get("run_id") or "").strip()
        if not run_id:
            return tool_error("run_id is required for legal_workflow next.")
        workflow = db.get_legal_workflow(run_id)
        if not workflow:
            return tool_error(f"legal workflow not found: {run_id}")
        pending = [
            step for step in workflow.get("steps") or []
            if step.get("status") in {"pending", "running", "blocked"}
        ]
        next_step = pending[0] if pending else None
        return _ok({
            "status": "ok",
            "workflow_id": run_id,
            "next_step": next_step,
            "kanban": _workflow_kanban_binding(workflow),
        })

    if action == "planning_status":
        project = _resolve_project(args, parent_agent=parent_agent)
        if not project["project_dir"]:
            return tool_error("project_dir or selected project is required for legal_workflow planning_status.")
        status = _transaction_structure_status(project["project_dir"])
        status["interaction_contract"] = {
            "ask_with": "clarify",
            "persist_with": "legal_workflow(action='planning_intake')",
            "rule": "Ask one question at a time. Do not start kanban execution until confirmed=true.",
        }
        return _ok(status)

    if action == "planning_intake":
        project = _resolve_project(args, parent_agent=parent_agent)
        if not project["project_dir"]:
            return tool_error("project_dir or selected project is required for legal_workflow planning_intake.")
        result = _record_planning_intake(args, project["project_dir"])
        if not result.get("ok"):
            return tool_error(str(result.get("error") or "planning_intake failed."))
        return _ok(result)

    if action == "update_run":
        run_id = str(args.get("run_id") or "").strip()
        if not run_id:
            return tool_error("run_id is required for legal_workflow update_run.")
        fields = {
            key: args[key]
            for key in ("name", "status", "document_path", "term_sheet_path", "instructions")
            if key in args
        }
        db.update_legal_workflow(run_id, **fields)
        return _ok({"status": "updated", "workflow": db.get_legal_workflow(run_id)})

    if action == "update_step":
        step_id = str(args.get("step_id") or "").strip()
        if not step_id:
            return tool_error("step_id is required for legal_workflow update_step.")
        step = args.get("step") or {}
        if not isinstance(step, dict):
            return tool_error("step must be an object.")
        db.update_legal_workflow_step(step_id, **step)
        return _ok({"status": "updated", "step_id": step_id})

    if action == "finish":
        run_id = str(args.get("run_id") or "").strip()
        if not run_id:
            return tool_error("run_id is required for legal_workflow finish.")
        workflow = db.get_legal_workflow(run_id)
        if not workflow:
            return tool_error(f"legal workflow not found: {run_id}")
        try:
            from hermes_cli.project_commands import legal_scorecard

            scorecard = legal_scorecard(
                workflow.get("project_dir") or "",
                document_path=workflow.get("document_path"),
                workflow_id=_infer_workflow_type(workflow),
                run_id=run_id,
                strict=True,
            )
        except Exception as exc:
            scorecard = {"ok": False, "status": "failed", "error": str(exc)}
        final_status = "completed" if scorecard.get("ok") else "blocked"
        db.update_legal_workflow(run_id, status=final_status)
        return _ok({
            "status": final_status,
            "workflow": db.get_legal_workflow(run_id),
            "scorecard": scorecard,
        })

    if action == "learn_from_run":
        run_id = str(args.get("run_id") or "").strip()
        if not run_id:
            return tool_error("run_id is required for legal_workflow learn_from_run.")
        workflow_type = str(args.get("workflow_type") or "").strip()
        learning_scope = str(args.get("learning_scope") or "global").strip()
        workflow = db.get_legal_workflow(run_id)
        if not workflow:
            return tool_error(f"legal workflow not found: {run_id}")
        if not workflow_type:
            workflow_type = _infer_workflow_type(workflow)
        findings = _collect_findings_from_workflow(workflow)
        from .legal_workflow_learning import learn_from_findings

        learning = learn_from_findings(
            findings=findings,
            workflow_type=workflow_type,
            scope=learning_scope,
            run_id=run_id,
            document_path=workflow.get("document_path"),
            enabled=True,
        )
        return _ok({"status": "learned", "learning": learning, "finding_count": len(findings)})

    if action == "list_learning_rules":
        workflow_type = str(args.get("workflow_type") or "contract_revision").strip()
        learning_scope = str(args.get("learning_scope") or "global").strip()
        status = str(args.get("status") or "").strip() or None
        rules = db.list_workflow_learning_rules(
            workflow_type=workflow_type,
            scope=learning_scope,
            status=status,
            limit=int(args.get("limit") or 100),
        )
        return _ok({"status": "ok", "rules": rules})

    if action in {"approve_learning_rule", "reject_learning_rule"}:
        rule_id = str(args.get("rule_id") or "").strip()
        if not rule_id:
            return tool_error(f"rule_id is required for legal_workflow {action}.")
        next_status = "active" if action == "approve_learning_rule" else "rejected"
        updated = db.update_workflow_learning_rule_status(rule_id, next_status)
        if not updated:
            return tool_error(f"workflow learning rule not found: {rule_id}")
        return _ok({"status": "updated", "rule_id": rule_id, "rule_status": next_status})

    if action == "export_learning_sop":
        workflow_type = str(args.get("workflow_type") or "contract_revision").strip()
        learning_scope = str(args.get("learning_scope") or "global").strip()
        from .legal_workflow_learning import export_learning_sop

        path = export_learning_sop(workflow_type=workflow_type, scope=learning_scope, db=db)
        return _ok({"status": "exported" if path else "error", "path": path})

    return tool_error(f"unknown legal_workflow action: {action}")


registry.register(
    name="legal_workflow",
    toolset="legal_orchestration",
    schema=LEGAL_WORKFLOW_SCHEMA,
    handler=_handle_legal_workflow,
    description=LEGAL_WORKFLOW_SCHEMA["description"],
)
