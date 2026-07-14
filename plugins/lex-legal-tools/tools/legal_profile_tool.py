"""
Native legal profile orchestration helpers.

Hermes profiles are independent HERMES_HOME directories.  This tool exposes a
lex-focused bootstrap so legal workflows can route work to real worker profiles
instead of pretending through prompts.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import yaml

from tools.registry import registry, tool_error


LEGAL_PROFILE_ROLES: dict[str, dict[str, Any]] = {
    "coordinator": {
        "suffix": "coordinator",
        "description": "Legal workflow coordinator. Plans work, delegates, gates edits, and does not directly mutate documents.",
        "toolsets": ["project_management", "legal_orchestration", "lex-docx-coordinator", "delegation", "skills", "session_search", "file"],
    },
    "drafter": {
        "suffix": "drafter",
        "description": "Legal document drafter. Applies approved revisions with Track Changes and verifies edited passages.",
        "toolsets": ["project_management", "lex-docx-worker", "skills", "file", "terminal", "session_search", "kanban"],
    },
    "content_reviewer": {
        "suffix": "review-content",
        "description": "Substantive legal reviewer. Checks legal logic, defined terms, obligations, conditions, and internal consistency.",
        "toolsets": ["project_management", "lex-docx-coordinator", "skills", "file", "session_search", "kanban"],
    },
    "format_reviewer": {
        "suffix": "review-format",
        "description": "Formatting reviewer. Checks styles, numbering, tables, Track Changes hygiene, headers, and footers.",
        "toolsets": ["project_management", "lex-docx-coordinator", "skills", "file", "session_search", "kanban"],
    },
    "xref_reviewer": {
        "suffix": "review-xref",
        "description": "Cross-reference reviewer. Audits bookmarks, REF fields, hardcoded clause references, and broken links.",
        "toolsets": ["project_management", "lex-docx-coordinator", "skills", "file", "session_search", "kanban"],
    },
    "translation_reviewer": {
        "suffix": "review-translation",
        "description": "Bilingual legal translation reviewer. Compares source and translation paragraph by paragraph.",
        "toolsets": ["project_management", "lex-docx-coordinator", "skills", "file", "session_search", "kanban"],
    },
}


LEGAL_PROFILES_SCHEMA = {
    "name": "legal_profiles",
    "description": (
        "Create or inspect lex legal-worker Hermes profiles for native multi-profile "
        "orchestration. Use this before multi-agent legal workflows that need real "
        "coordinator/drafter/reviewer worker identities."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["templates", "list", "bootstrap"],
                "description": "templates=show recommended roles, list=show installed profiles, bootstrap=create missing legal profiles.",
            },
            "prefix": {
                "type": "string",
                "description": "Profile name prefix for bootstrap. Default: lex.",
            },
            "roles": {
                "type": "array",
                "items": {"type": "string", "enum": list(LEGAL_PROFILE_ROLES.keys())},
                "description": "Roles to create. Default: all legal roles.",
            },
            "clone_config": {
                "type": "boolean",
                "description": "Clone current profile config/.env/SOUL.md/skills into new profiles. Default: true.",
            },
        },
        "required": ["action"],
    },
}


def _profile_name(prefix: str, role: str) -> str:
    suffix = LEGAL_PROFILE_ROLES[role]["suffix"]
    return f"{prefix}-{suffix}".lower().replace("_", "-")


def _write_profile_toolsets(profile_dir: Path, toolsets: list[str]) -> None:
    config_path = profile_dir / "config.yaml"
    try:
        data = yaml.safe_load(config_path.read_text(encoding="utf-8")) if config_path.exists() else {}
    except Exception:
        data = {}
    if not isinstance(data, dict):
        data = {}
    data.setdefault("platform_toolsets", {})
    data["platform_toolsets"]["cli"] = sorted(set(toolsets))
    data.setdefault("skills", {})
    data["skills"]["bundled_profile"] = "lex"
    data.setdefault("delegation", {})
    data["delegation"]["orchestrator_enabled"] = role_orchestrator_enabled(toolsets)
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8")


def role_orchestrator_enabled(toolsets: list[str]) -> bool:
    return "delegation" in set(toolsets) or "legal_orchestration" in set(toolsets)


def _handle_legal_profiles(args: dict, **kwargs) -> str:
    action = str(args.get("action") or "").strip()
    prefix = str(args.get("prefix") or "lex").strip().lower().replace("_", "-")
    roles = [str(v).strip() for v in (args.get("roles") or []) if str(v).strip()] or list(LEGAL_PROFILE_ROLES)
    invalid = [role for role in roles if role not in LEGAL_PROFILE_ROLES]
    if invalid:
        return tool_error(f"Unknown legal profile roles: {', '.join(invalid)}")

    if action == "templates":
        return json.dumps({"success": True, "templates": LEGAL_PROFILE_ROLES}, ensure_ascii=False)

    try:
        from hermes_cli.profiles import create_profile, get_profile_dir, list_profiles, seed_profile_skills
    except Exception as exc:
        return tool_error(f"Profile support unavailable: {exc}")

    if action == "list":
        profiles = []
        installed = {p.name: p for p in list_profiles()}
        for role in roles:
            name = _profile_name(prefix, role)
            p = installed.get(name)
            profiles.append({
                "role": role,
                "profile": name,
                "exists": bool(p),
                "path": str(get_profile_dir(name)),
                "description": LEGAL_PROFILE_ROLES[role]["description"],
                "toolsets": LEGAL_PROFILE_ROLES[role]["toolsets"],
            })
        return json.dumps({"success": True, "profiles": profiles}, ensure_ascii=False)

    if action != "bootstrap":
        return tool_error("action must be templates, list, or bootstrap.")

    clone_config = bool(args.get("clone_config", True))
    created: list[dict[str, Any]] = []
    existing: list[dict[str, Any]] = []
    for role in roles:
        spec = LEGAL_PROFILE_ROLES[role]
        name = _profile_name(prefix, role)
        profile_dir = get_profile_dir(name)
        if profile_dir.exists():
            existing.append({"role": role, "profile": name, "path": str(profile_dir)})
        else:
            profile_dir = create_profile(
                name=name,
                clone_config=clone_config,
                no_alias=True,
                no_skills=False,
                description=spec["description"],
            )
            created.append({"role": role, "profile": name, "path": str(profile_dir)})
        seed_profile_skills(profile_dir, quiet=True)
        _write_profile_toolsets(profile_dir, spec["toolsets"])

    return json.dumps({
        "success": True,
        "prefix": prefix,
        "created": created,
        "existing": existing,
        "message": "Legal profiles are ready for Kanban/delegate orchestration.",
    }, ensure_ascii=False)


registry.register(
    name="legal_profiles",
    toolset="legal_orchestration",
    schema=LEGAL_PROFILES_SCHEMA,
    handler=_handle_legal_profiles,
    description=LEGAL_PROFILES_SCHEMA["description"],
)
