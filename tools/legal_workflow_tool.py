"""Persistent legal workflow plans for Lex Hermes."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional

from hermes_state import SessionDB
from tools.project_management_tool import resolve_selected_project
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
    return findings


def _infer_workflow_type(workflow: Dict[str, Any]) -> str:
    name = str(workflow.get("name") or "").lower()
    instructions = str(workflow.get("instructions") or "").lower()
    step_types = " ".join(str(step.get("type") or "") for step in workflow.get("steps") or [])
    haystack = " ".join([name, instructions, step_types])
    if any(marker in haystack for marker in ("translation", "翻译", "bilingual", "lex_translation_review")):
        return "translation_quality_review"
    return "contract_revision"


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
                    "create_plan",
                    "get",
                    "list",
                    "update_run",
                    "update_step",
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
                "enum": ["contract_revision", "translation_quality_review"],
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
                "description": "Whether workflow learning is enabled for translation QA. Default: true.",
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

    if action == "create_plan":
        project = _resolve_project(args, parent_agent=parent_agent)
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
        instructions = str(args.get("instructions") or "").strip() or None
        workflow_type = str(args.get("workflow_type") or "contract_revision").strip()
        default_name = "中英文翻译质量核对 workflow" if workflow_type == "translation_quality_review" else "法律文书修订 workflow"
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
            created_by_session_id=getattr(parent_agent, "session_id", None),
            steps=steps,
        )
        return _ok({"status": "created", "workflow": db.get_legal_workflow(run_id)})

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
        from tools.lex_translation_review_tool import _learn_from_findings

        learning = _learn_from_findings(
            findings=findings,
            workflow_type=workflow_type,
            scope=learning_scope,
            run_id=run_id,
            document_path=workflow.get("document_path"),
        )
        return _ok({"status": "learned", "learning": learning, "finding_count": len(findings)})

    if action == "list_learning_rules":
        workflow_type = str(args.get("workflow_type") or "translation_quality_review").strip()
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
        workflow_type = str(args.get("workflow_type") or "translation_quality_review").strip()
        learning_scope = str(args.get("learning_scope") or "global").strip()
        from tools.lex_translation_review_tool import _export_learning_sop

        path = _export_learning_sop(workflow_type=workflow_type, scope=learning_scope, db=db)
        return _ok({"status": "exported" if path else "error", "path": path})

    return tool_error(f"unknown legal_workflow action: {action}")


registry.register(
    name="legal_workflow",
    toolset="legal_orchestration",
    schema=LEGAL_WORKFLOW_SCHEMA,
    handler=_handle_legal_workflow,
    description=LEGAL_WORKFLOW_SCHEMA["description"],
)
