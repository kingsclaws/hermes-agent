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
    instructions: Optional[str],
) -> List[Dict[str, Any]]:
    common_input = {
        "document_path": document_path,
        "term_sheet_path": term_sheet_path,
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
                "enum": ["create_plan", "get", "list", "update_run", "update_step"],
            },
            "run_id": {"type": "string"},
            "step_id": {"type": "string"},
            "name": {"type": "string"},
            "project_dir": {"type": "string"},
            "document_path": {"type": "string"},
            "term_sheet_path": {"type": "string"},
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
        instructions = str(args.get("instructions") or "").strip() or None
        name = str(args.get("name") or "").strip() or "法律文书修订 workflow"
        steps = _normalize_custom_steps(args.get("steps"))
        if not steps:
            steps = _default_contract_workflow_steps(
                document_path=document_path,
                term_sheet_path=term_sheet_path,
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

    return tool_error(f"unknown legal_workflow action: {action}")


registry.register(
    name="legal_workflow",
    toolset="legal_orchestration",
    schema=LEGAL_WORKFLOW_SCHEMA,
    handler=_handle_legal_workflow,
    description=LEGAL_WORKFLOW_SCHEMA["description"],
)
