"""Lex Legal Tools Plugin

Registers all Lex-specific tools via the plugin system instead of modifying
toolsets.py directly. This keeps changes in Layer 3 (plugin) instead of
Layer 2 (modified upstream files).

Tools registered:
- lexitool (lex_read, lex_edit, lex_tc, lex_comment, etc.)
- legal workflow (legal_workflow, legal_orchestrate, legal_profiles)
- legal project management (legal_project_create, legal_project_list, etc.)
- kanban swarm (swarm_task_create, swarm_task_poll, etc.)
- research (research_plan, research_run, etc.)
- legal evidence gate, OCR guard, proofread, template, translation
"""

from __future__ import annotations

import importlib
import logging
logger = logging.getLogger(__name__)

# Map of toolset name → list of tool module paths
# Each module must have a `register(registry)` function or use
# the standard registry.register() pattern at module level.
_TOOL_MODULES = [
    # Lexitool core
    "lex_heal_tool",
    "lex_proofread_tool",
    "lex_template_tool",
    "lex_translation_review_tool",
    "lexitool_tool",
    # Legal workflow
    "legal_evidence_gate",
    "legal_ocr_guard",
    "legal_orchestration_tool",
    "legal_profile_tool",
    "legal_workflow_learning",
    "legal_workflow_tool",
    # Project management
    "project_management_tool",
    # Kanban swarm
    "kanban_toolset",
    # Research
    "research_tool",
    # Preview
    "lex_preview_tool",
]

# Toolsets to register via auto_register_toolset
_TOOLSET_DEFINITIONS = {
    "lex-docx": {
        "description": "Lex DOCX tools for legal document operations",
        "tools": [
            "lex_read", "lex_scan", "lex_revision_guard", "lex_stats",
            "lex_table_list", "lex_edit", "lex_tc", "lex_format",
            "lex_list", "lex_ref", "lex_section", "lex_doc", "lex_clause",
            "lex_corpus", "lex_ocr", "lex_project_init", "lex_diff",
            "lex_xref_audit", "lex_deliver", "lex_gate_check", "lex_git",
            "project_facts", "lex_convention_profile", "legal_review_plan",
            "edit_verification_record", "lex_comment",
        ],
        "includes": [],
    },
    "lex-docx-worker": {
        "description": "Full read-write legal document tools for worker sub-agents (Drafter, Reviewer). Includes lex_edit, lex_format, execute_code.",
        "tools": [
            "lex_read", "lex_scan", "lex_revision_guard", "lex_stats", "lex_table_list", "lex_edit", "lex_tc", "lex_format",
            "lex_comment", "lex_list", "lex_ref", "lex_section", "lex_doc", "lex_clause",
            "lex_corpus", "lex_ocr", "lex_project_init", "lex_diff", "lex_xref_audit",
            "lex_deliver", "lex_gate_check", "lex_git", "project_facts",
            "lex_convention_profile", "legal_review_plan", "edit_verification_record",
            "lex_proofread", "lex_template_audit", "lex_template_fill",
            "lex_translation_review", "lex_review_workflow", "lex_verify_edits",
            "legal_harness_migrate", "legal_harness_workflow", "legal_handoff_record",
            "legal_scorecard", "update_project_state", "get_project_state", "refine_goal",
            "project_add_task", "project_list_tasks", "project_update_task",
            "project_delete_task", "lex_heal", "execute_code",
        ],
        "includes": ["kanban_swarm"],
    },
    "lex-docx-coordinator": {
        "description": "Lex DOCX tools plus coordination tools for project coordinators",
        "tools": [
            "lex_read", "lex_scan", "lex_revision_guard", "lex_stats",
            "lex_table_list", "lex_edit", "lex_tc", "lex_comment",
            "lex_format", "lex_list", "lex_ref", "lex_section", "lex_doc",
            "lex_clause", "lex_corpus", "lex_ocr", "lex_project_init",
            "lex_diff", "lex_xref_audit", "lex_deliver", "lex_gate_check",
            "lex_git", "project_facts", "lex_convention_profile",
            "legal_review_plan", "edit_verification_record",
            "lex_proofread", "lex_template_audit", "lex_template_fill",
            "lex_translation_review", "lex_review_workflow",
            "lex_verify_edits", "legal_harness_migrate",
            "legal_harness_workflow", "legal_handoff_record",
            "legal_scorecard", "update_project_state", "get_project_state",
            "refine_goal", "project_add_task", "project_list_tasks",
            "project_update_task", "project_delete_task", "lex_heal",
            "execute_code", "swarm_task_create", "swarm_task_poll",
            "swarm_task_collect", "swarm_task_progress",
            "swarm_workflow_compile",
        ],
        "includes": [],
    },
    "research": {
        "description": "Deep research orchestration with source ledger and evidence tracking",
        "tools": [
            "research_plan", "research_run", "research_collect",
            "research_synthesize", "research_report", "research_update_facts",
        ],
        "includes": [],
    },
}


def register(ctx) -> None:
    """Plugin entry point — called by the plugin loader."""
    # Import all tool modules so their registry.register() calls execute
    for mod_path in _TOOL_MODULES:
        try:
            importlib.import_module(f"{__package__}.tools.{mod_path}")
        except Exception as exc:
            logger.warning("lex-legal-tools: failed to import %s: %s", mod_path, exc)

    # Register toolset definitions so they appear in `hermes tools`
    from toolsets import create_custom_toolset
    for name, defn in _TOOLSET_DEFINITIONS.items():
        create_custom_toolset(
            name=name,
            description=defn["description"],
            tools=defn["tools"],
            includes=defn.get("includes", []),
        )

    logger.info("lex-legal-tools: registered %d tool modules, %d toolsets",
                len(_TOOL_MODULES), len(_TOOLSET_DEFINITIONS))
