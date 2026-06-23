#!/usr/bin/env python3
"""
Memory Tool Module - Persistent Curated Memory

Provides bounded, file-backed memory that persists across sessions. Two stores:
  - MEMORY.md: agent's personal notes and observations (environment facts, project
    conventions, tool quirks, things learned)
  - USER.md: what the agent knows about the user (preferences, communication style,
    expectations, workflow habits)

Both are injected into the system prompt as a frozen snapshot at session start.
Mid-session writes update files on disk immediately (durable) but do NOT change
the system prompt -- this preserves the prefix cache for the entire session.
The snapshot refreshes on the next session start.

Entry delimiter: § (section sign). Entries can be multiline.
Character limits (not tokens) because char counts are model-independent.

Two scopes supported:
  - global: stored in ~/.hermes/memories/, shared across all projects
  - project: stored in .hermes-project/memories/, portable with the project

When a project is active, the system prompt includes both global and project
memories, labelled separately.

Design:
- Single `memory` tool with action parameter: add, replace, remove, read
- replace/remove use short unique substring matching (not full text or IDs)
- Behavioral guidance lives in the tool schema description
- Frozen snapshot pattern: system prompt is stable, tool responses show live state
"""

import json
import logging
import os
import re
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path
from hermes_constants import get_hermes_home
from typing import Dict, Any, List, Optional

from utils import atomic_replace

# fcntl is Unix-only; on Windows use msvcrt for file locking
msvcrt = None
try:
    import fcntl
except ImportError:
    fcntl = None
    try:
        import msvcrt
    except ImportError:
        pass

logger = logging.getLogger(__name__)

# Where memory files live — resolved dynamically so profile overrides
# (HERMES_HOME env var changes) are always respected.  The old module-level
# constant was cached at import time and could go stale if a profile switch
# happened after the first import.
def get_memory_dir() -> Path:
    """Return the profile-scoped memories directory."""
    return get_hermes_home() / "memories"


def _get_project_mem_dir(project_dir: Optional[str]) -> Optional[Path]:
    """Return the project-scoped memories directory, or None if no project."""
    if not project_dir:
        return None
    project = Path(project_dir).resolve()
    p = project / ".hermes-project" / "memories"
    # Ensure the memories dir actually lives within the project directory
    # to prevent path traversal via a crafted project_dir value.
    try:
        p.resolve().relative_to(project)
    except ValueError:
        logger.warning("Project memory path escapes project directory: %s", p)
        return None
    return p if p.is_dir() else None

ENTRY_DELIMITER = "\n§\n"


# ---------------------------------------------------------------------------
# Memory content scanning — lightweight check for injection/exfiltration
# in content that gets injected into the system prompt.
#
# Patterns live in ``tools/threat_patterns.py`` — the single source of truth
# shared with the context-file scanner and the tool-result delimiter system.
# Memory uses the "strict" scope (broadest pattern set) because:
#  - memory entries are user-curated; the user can rewrite a flagged entry
#  - memory enters the system prompt as a FROZEN snapshot, so a poisoned
#    entry persists for the entire session and across sessions until
#    explicitly removed.
# ---------------------------------------------------------------------------

from tools.threat_patterns import first_threat_message as _first_threat_message


def _scan_memory_content(content: str) -> Optional[str]:
    """Scan memory content for injection/exfil patterns. Returns error string if blocked."""
    return _first_threat_message(content, scope="strict")


def _drift_error(path: "Path", bak_path: str) -> Dict[str, Any]:
    """Build the error dict returned when external drift is detected.

    The on-disk memory file contains content that wouldn't round-trip
    through the tool's parser/serializer — flushing would discard the
    appended/edited content from a patch tool, shell append, manual edit,
    or sister-session write. We refuse the mutation, point the operator at
    the .bak.<ts> snapshot we took, and tell them what to do next.
    """
    return {
        "success": False,
        "error": (
            f"Refusing to write {path.name}: file on disk has content that "
            f"wouldn't round-trip through the memory tool (likely added by "
            f"the patch tool, a shell append, a manual edit, or a "
            f"concurrent session). A snapshot was saved to {bak_path}. "
            f"Resolve the drift first — either rewrite the file as a clean "
            f"§-delimited list of entries, or move the extra content out — "
            f"then retry. This guard exists to prevent silent data loss "
            f"(issue #26045)."
        ),
        "drift_backup": bak_path,
        "remediation": (
            "Open the .bak file, integrate the missing entries into the "
            "memory tool one at a time via memory(action=add, content=...), "
            "then remove or rewrite the original file to a clean state."
        ),
    }


class MemoryStore:
    """
    Bounded curated memory with file persistence. One instance per AIAgent.

    Maintains two parallel states:
      - _system_prompt_snapshot: frozen at load time, used for system prompt injection.
        Never mutated mid-session. Keeps prefix cache stable.
      - memory_entries / user_entries: live state, mutated by tool calls, persisted to disk.
        Tool responses always reflect this live state.
    """

    def __init__(self, memory_char_limit: int = 2200, user_char_limit: int = 1375, project_dir: Optional[str] = None):
        self.memory_entries: List[str] = []
        self.user_entries: List[str] = []
        self.project_memory_entries: List[str] = []
        self.project_user_entries: List[str] = []
        self.memory_char_limit = memory_char_limit
        self.user_char_limit = user_char_limit
        # Project-scoped memory directory (read-only after init — only load_from_disk uses it)
        self._project_mem_dir: Optional[Path] = _get_project_mem_dir(project_dir) if project_dir else None
        # Frozen snapshot for system prompt -- set once at load_from_disk()
        self._system_prompt_snapshot: Dict[str, str] = {
            "memory": "", "user": "", "project_memory": "", "project_user": "",
        }

    def load_from_disk(self):
        """Load entries from global and project MEMORY.md / USER.md files.

        The frozen snapshot is what enters the system prompt. We scan each
        entry for injection/promptware patterns at snapshot-build time —
        ANY hit replaces the entry text in the snapshot with a placeholder
        like ``[BLOCKED: …]``, so a poisoned-on-disk memory file (supply
        chain, compromised tool, sister-session write) cannot inject into
        the system prompt.

        The live ``memory_entries`` / ``user_entries`` lists keep the
        original text so the user can still SEE poisoned entries via
        ``memory(action=read)`` and remove them — silently dropping them
        would hide the attack from the user.

        Scanning is deterministic from disk bytes, so the snapshot remains
        stable for the entire session (prefix-cache invariant holds).
        """
        mem_dir = get_memory_dir()
        mem_dir.mkdir(parents=True, exist_ok=True)

        self.memory_entries = self._read_file(mem_dir / "MEMORY.md")
        self.user_entries = self._read_file(mem_dir / "USER.md")

        # Load project-scoped memories if available
        if self._project_mem_dir and self._project_mem_dir.exists():
            self.project_memory_entries = self._read_file(self._project_mem_dir / "MEMORY.md")
            self.project_user_entries = self._read_file(self._project_mem_dir / "USER.md")
        else:
            self.project_memory_entries = []
            self.project_user_entries = []

        # Deduplicate entries (preserves order, keeps first occurrence)
        self.memory_entries = list(dict.fromkeys(self.memory_entries))
        self.user_entries = list(dict.fromkeys(self.user_entries))
        self.project_memory_entries = list(dict.fromkeys(self.project_memory_entries))
        self.project_user_entries = list(dict.fromkeys(self.project_user_entries))

        # Sanitize entries for the system-prompt snapshot only.  Live state
        # (memory_entries / user_entries) keeps the raw text so the user
        # can see + remove poisoned entries via the memory tool.
        sanitized_memory = self._sanitize_entries_for_snapshot(self.memory_entries, "MEMORY.md")
        sanitized_user = self._sanitize_entries_for_snapshot(self.user_entries, "USER.md")
        sanitized_proj_mem = self._sanitize_entries_for_snapshot(self.project_memory_entries, "project/MEMORY.md")
        sanitized_proj_usr = self._sanitize_entries_for_snapshot(self.project_user_entries, "project/USER.md")

        # Capture frozen snapshot for system prompt injection
        self._system_prompt_snapshot = {
            "memory": self._render_block("memory", sanitized_memory),
            "user": self._render_block("user", sanitized_user),
            "project_memory": self._render_project_block("memory", sanitized_proj_mem),
            "project_user": self._render_project_block("user", sanitized_proj_usr),
        }

    @staticmethod
    def _sanitize_entries_for_snapshot(entries: List[str], filename: str) -> List[str]:
        """Return ``entries`` with any threat-matching entry replaced by a placeholder.

        Each entry is scanned with the shared threat-pattern library at the
        ``"strict"`` scope (same as memory writes).  On match, the entry is
        replaced in the returned list with ``"[BLOCKED: <filename> entry
        contained threat pattern: <ids>. Removed from system prompt.]"`` —
        the placeholder enters the snapshot, the original entry stays in
        live state for the user to inspect and delete.

        Empty or already-block-marker entries pass through unchanged.
        """
        from tools.threat_patterns import scan_for_threats

        sanitized: List[str] = []
        for entry in entries:
            if not entry or entry.startswith("[BLOCKED:"):
                sanitized.append(entry)
                continue
            findings = scan_for_threats(entry, scope="strict")
            if findings:
                logger.warning(
                    "Memory entry from %s blocked at load time: %s",
                    filename, ", ".join(findings),
                )
                sanitized.append(
                    f"[BLOCKED: {filename} entry contained threat pattern(s): "
                    f"{', '.join(findings)}. Removed from system prompt; "
                    f"use memory(action=read) to inspect and memory(action=remove) "
                    f"to delete the original.]"
                )
            else:
                sanitized.append(entry)
        return sanitized

    @staticmethod
    @contextmanager
    def _file_lock(path: Path):
        """Acquire an exclusive file lock for read-modify-write safety.

        Uses a separate .lock file so the memory file itself can still be
        atomically replaced via os.replace().
        """
        lock_path = path.with_suffix(path.suffix + ".lock")
        lock_path.parent.mkdir(parents=True, exist_ok=True)

        if fcntl is None and msvcrt is None:
            yield
            return

        fd = open(lock_path, "a+", encoding="utf-8")
        try:
            if fcntl:
                fcntl.flock(fd, fcntl.LOCK_EX)
            else:
                fd.seek(0)
                msvcrt.locking(fd.fileno(), msvcrt.LK_LOCK, 1)
            yield
        finally:
            if fcntl:
                try:
                    fcntl.flock(fd, fcntl.LOCK_UN)
                except (OSError, IOError):
                    pass
            elif msvcrt:
                try:
                    fd.seek(0)
                    msvcrt.locking(fd.fileno(), msvcrt.LK_UNLCK, 1)
                except (OSError, IOError):
                    pass
            fd.close()

    @staticmethod
    def _path_for(target: str, scope: str = "global") -> Path:
        if scope == "project":
            # Project path must be resolved at call time via the instance
            return None  # caller must use _resolve_path_for
        mem_dir = get_memory_dir()
        if target == "user":
            return mem_dir / "USER.md"
        return mem_dir / "MEMORY.md"

    def _resolve_path_for(self, target: str, scope: str = "global") -> Path:
        """Resolve the file path for the given target and scope."""
        if scope == "project":
            if not self._project_mem_dir:
                raise ValueError("No project memory directory configured")
            pdir = self._project_mem_dir
            pdir.mkdir(parents=True, exist_ok=True)
            if target == "user":
                return pdir / "USER.md"
            return pdir / "MEMORY.md"
        return self._path_for(target, "global")

    def _reload_target(self, target: str, scope: str = "global") -> Optional[str]:
        """Re-read entries from disk into in-memory state.

        Called under file lock to get the latest state before mutating.
        Returns the backup path if external drift was detected (the on-disk
        file contains content that wouldn't round-trip through our
        parser/serializer, OR an entry larger than the store's char limit).
        When drift is detected the caller must abort the mutation —
        flushing would discard the un-roundtrippable content.
        Returns None on clean reload.
        """
        path = self._resolve_path_for(target, scope)
        bak = self._detect_external_drift(target, scope)
        fresh = self._read_file(path)
        fresh = list(dict.fromkeys(fresh))  # deduplicate
        self._set_entries(target, scope, fresh)
        return bak

    def save_to_disk(self, target: str, scope: str = "global"):
        """Persist entries to the appropriate file. Called after every mutation."""
        path = self._resolve_path_for(target, scope)
        path.parent.mkdir(parents=True, exist_ok=True)
        self._write_file(path, self._entries_for(target, scope))

    def _entries_for(self, target: str, scope: str = "global") -> List[str]:
        if scope == "project":
            if target == "user":
                return self.project_user_entries
            return self.project_memory_entries
        if target == "user":
            return self.user_entries
        return self.memory_entries

    def _set_entries(self, target: str, scope: str, entries: List[str]):
        if scope == "project":
            if target == "user":
                self.project_user_entries = entries
            else:
                self.project_memory_entries = entries
        elif target == "user":
            self.user_entries = entries
        else:
            self.memory_entries = entries

    def _char_count(self, target: str, scope: str = "global") -> int:
        entries = self._entries_for(target, scope)
        if not entries:
            return 0
        return len(ENTRY_DELIMITER.join(entries))

    def _char_limit(self, target: str, scope: str = "global") -> int:
        if target == "user":
            return self.user_char_limit
        return self.memory_char_limit

    def add(self, target: str, content: str, scope: str = "global") -> Dict[str, Any]:
        """Append a new entry. Returns error if it would exceed the char limit."""
        content = content.strip()
        if not content:
            return {"success": False, "error": "Content cannot be empty."}

        # Scan for injection/exfiltration before accepting
        scan_error = _scan_memory_content(content)
        if scan_error:
            return {"success": False, "error": scan_error}

        path = self._resolve_path_for(target, scope)
        with self._file_lock(path):
            # Re-read from disk under lock to pick up writes from other sessions.
            # If external drift was detected, the file was backed up to .bak.<ts>
            # — refuse the mutation so we don't clobber the un-roundtrippable
            # content the patch tool / shell append / sister session wrote.
            bak = self._reload_target(target, scope)
            if bak:
                return _drift_error(path, bak)

            entries = self._entries_for(target, scope)
            limit = self._char_limit(target, scope)

            # Reject exact duplicates
            if content in entries:
                return self._success_response(target, "Entry already exists (no duplicate added).", scope)

            # Calculate what the new total would be
            new_entries = entries + [content]
            new_total = len(ENTRY_DELIMITER.join(new_entries))

            if new_total > limit:
                current = self._char_count(target, scope)
                return {
                    "success": False,
                    "error": (
                        f"Memory at {current:,}/{limit:,} chars. "
                        f"Adding this entry ({len(content)} chars) would exceed the limit. "
                        f"Replace or remove existing entries first."
                    ),
                    "current_entries": entries,
                    "usage": f"{current:,}/{limit:,}",
                }

            entries.append(content)
            self._set_entries(target, scope, entries)
            self.save_to_disk(target, scope)

        return self._success_response(target, "Entry added.", scope)

    def replace(self, target: str, old_text: str, new_content: str, scope: str = "global") -> Dict[str, Any]:
        """Find entry containing old_text substring, replace it with new_content."""
        old_text = old_text.strip()
        new_content = new_content.strip()
        if not old_text:
            return {"success": False, "error": "old_text cannot be empty."}
        if not new_content:
            return {"success": False, "error": "new_content cannot be empty. Use 'remove' to delete entries."}

        # Scan replacement content for injection/exfiltration
        scan_error = _scan_memory_content(new_content)
        if scan_error:
            return {"success": False, "error": scan_error}

        path = self._resolve_path_for(target, scope)
        with self._file_lock(path):
            bak = self._reload_target(target, scope)
            if bak:
                return _drift_error(path, bak)

            entries = self._entries_for(target, scope)
            matches = [(i, e) for i, e in enumerate(entries) if old_text in e]

            if not matches:
                return {"success": False, "error": f"No entry matched '{old_text}'."}

            if len(matches) > 1:
                # If all matches are identical (exact duplicates), operate on the first one
                unique_texts = {e for _, e in matches}
                if len(unique_texts) > 1:
                    previews = [e[:80] + ("..." if len(e) > 80 else "") for _, e in matches]
                    return {
                        "success": False,
                        "error": f"Multiple entries matched '{old_text}'. Be more specific.",
                        "matches": previews,
                    }
                # All identical -- safe to replace just the first

            idx = matches[0][0]
            limit = self._char_limit(target, scope)

            # Check that replacement doesn't blow the budget
            test_entries = entries.copy()
            test_entries[idx] = new_content
            new_total = len(ENTRY_DELIMITER.join(test_entries))

            if new_total > limit:
                return {
                    "success": False,
                    "error": (
                        f"Replacement would put memory at {new_total:,}/{limit:,} chars. "
                        f"Shorten the new content or remove other entries first."
                    ),
                }

            entries[idx] = new_content
            self._set_entries(target, scope, entries)
            self.save_to_disk(target, scope)

        return self._success_response(target, "Entry replaced.", scope)

    def remove(self, target: str, old_text: str, scope: str = "global") -> Dict[str, Any]:
        """Remove the entry containing old_text substring."""
        old_text = old_text.strip()
        if not old_text:
            return {"success": False, "error": "old_text cannot be empty."}

        path = self._resolve_path_for(target, scope)
        with self._file_lock(path):
            bak = self._reload_target(target, scope)
            if bak:
                return _drift_error(path, bak)

            entries = self._entries_for(target, scope)
            matches = [(i, e) for i, e in enumerate(entries) if old_text in e]

            if not matches:
                return {"success": False, "error": f"No entry matched '{old_text}'."}

            if len(matches) > 1:
                # If all matches are identical (exact duplicates), remove the first one
                unique_texts = {e for _, e in matches}
                if len(unique_texts) > 1:
                    previews = [e[:80] + ("..." if len(e) > 80 else "") for _, e in matches]
                    return {
                        "success": False,
                        "error": f"Multiple entries matched '{old_text}'. Be more specific.",
                        "matches": previews,
                    }
                # All identical -- safe to remove just the first

            idx = matches[0][0]
            entries.pop(idx)
            self._set_entries(target, scope, entries)
            self.save_to_disk(target, scope)

        return self._success_response(target, "Entry removed.", scope)

    def format_for_system_prompt(self, target: str) -> Optional[str]:
        """
        Return the frozen snapshot for system prompt injection.

        This returns the state captured at load_from_disk() time, NOT the live
        state. Mid-session writes do not affect this. This keeps the system
        prompt stable across all turns, preserving the prefix cache.

        For 'memory' and 'user', returns both global and project blocks
        concatenated (if project blocks exist).

        Returns None if the snapshot is empty (no entries at load time).
        """
        if target == "memory":
            global_block = self._system_prompt_snapshot.get("memory", "")
            proj_block = self._system_prompt_snapshot.get("project_memory", "")
            combined = "\n".join(p for p in (global_block, proj_block) if p)
            return combined if combined else None
        elif target == "user":
            global_block = self._system_prompt_snapshot.get("user", "")
            proj_block = self._system_prompt_snapshot.get("project_user", "")
            combined = "\n".join(p for p in (global_block, proj_block) if p)
            return combined if combined else None
        return None

    # -- Internal helpers --

    def _success_response(self, target: str, message: str = None, scope: str = "global") -> Dict[str, Any]:
        entries = self._entries_for(target, scope)
        current = self._char_count(target, scope)
        limit = self._char_limit(target, scope)
        pct = min(100, int((current / limit) * 100)) if limit > 0 else 0

        resp = {
            "success": True,
            "target": target,
            "scope": scope,
            "entries": entries,
            "usage": f"{pct}% — {current:,}/{limit:,} chars",
            "entry_count": len(entries),
        }
        if message:
            resp["message"] = message
        return resp

    def _render_block(self, target: str, entries: List[str]) -> str:
        """Render a system prompt block with header and usage indicator."""
        if not entries:
            return ""

        limit = self._char_limit(target)
        content = ENTRY_DELIMITER.join(entries)
        current = len(content)
        pct = min(100, int((current / limit) * 100)) if limit > 0 else 0

        if target == "user":
            header = f"USER PROFILE (who the user is) [{pct}% — {current:,}/{limit:,} chars]"
        else:
            header = f"MEMORY (your personal notes) [{pct}% — {current:,}/{limit:,} chars]"

        separator = "═" * 46
        return f"{separator}\n{header}\n{separator}\n{content}"

    def _render_project_block(self, target: str, entries: List[str]) -> str:
        """Render a project-scoped memory block for the system prompt."""
        if not entries:
            return ""

        limit = self._char_limit(target)
        content = ENTRY_DELIMITER.join(entries)
        current = len(content)
        pct = min(100, int((current / limit) * 100)) if limit > 0 else 0

        if target == "user":
            header = f"PROJECT USER PROFILE (project-scoped) [{pct}% — {current:,}/{limit:,} chars]"
        else:
            header = f"PROJECT MEMORY (project-scoped) [{pct}% — {current:,}/{limit:,} chars]"

        separator = "─" * 46
        return f"{separator}\n{header}\n{separator}\n{content}"

    @staticmethod
    def _read_file(path: Path) -> List[str]:
        """Read a memory file and split into entries.

        No file locking needed: _write_file uses atomic rename, so readers
        always see either the previous complete file or the new complete file.
        """
        if not path.exists():
            return []
        try:
            raw = path.read_text(encoding="utf-8")
        except (OSError, IOError):
            return []

        if not raw.strip():
            return []

        # Use ENTRY_DELIMITER for consistency with _write_file. Splitting by "§"
        # alone would incorrectly split entries that contain "§" in their content.
        entries = [e.strip() for e in raw.split(ENTRY_DELIMITER)]
        return [e for e in entries if e]

    def _detect_external_drift(self, target: str, scope: str = "global") -> Optional[str]:
        """Return a backup-path string if on-disk content shows external drift.

        The memory file is supposed to be a list of small entries the tool
        wrote, joined by §. Detect drift via two signals:

        1. Round-trip mismatch — re-parsing and re-serializing the file
           doesn't produce identical bytes (rare; would catch oddly-encoded
           delimiters).
        2. Entry-size overflow — any single parsed entry exceeds the
           store's whole-file char limit. The tool budgets the ENTIRE store
           against that limit; no single tool-written entry can exceed it.
           When we see one entry larger than the limit, an external writer
           (patch tool, shell append, manual edit, sister session) appended
           free-form content into what the tool will treat as one entry.
           Flushing would then truncate that entry to the model's new
           content, discarding the appended bytes — issue #26045.

        Returns the absolute path of the .bak file when drift was found and
        backed up; returns None when the file looks tool-shaped.

        Note: this is an INSTANCE method (not static) because we need the
        per-target char_limit for signal #2.
        """
        path = self._resolve_path_for(target, scope)
        if not path.exists():
            return None
        try:
            raw = path.read_text(encoding="utf-8")
        except (OSError, IOError):
            return None
        if not raw.strip():
            return None

        parsed = [e.strip() for e in raw.split(ENTRY_DELIMITER) if e.strip()]
        roundtrip = ENTRY_DELIMITER.join(parsed)

        char_limit = self._char_limit(target)
        max_entry_len = max((len(e) for e in parsed), default=0)

        drift_detected = (raw.strip() != roundtrip) or (max_entry_len > char_limit)
        if not drift_detected:
            return None

        # Drift confirmed — snapshot the file so the operator can recover
        # whatever the external writer added, then return the .bak path so
        # the caller can refuse the mutation.
        ts = int(time.time())
        bak_path = path.with_suffix(path.suffix + f".bak.{ts}")
        try:
            bak_path.write_text(raw, encoding="utf-8")
        except (OSError, IOError):
            return str(bak_path) + " (BACKUP FAILED — file unchanged on disk)"
        return str(bak_path)

    @staticmethod
    def _write_file(path: Path, entries: List[str]):
        """Write entries to a memory file using atomic temp-file + rename.

        Previous implementation used open("w") + flock, but "w" truncates the
        file *before* the lock is acquired, creating a race window where
        concurrent readers see an empty file. Atomic rename avoids this:
        readers always see either the old complete file or the new one.
        """
        content = ENTRY_DELIMITER.join(entries) if entries else ""
        try:
            # Write to temp file in same directory (same filesystem for atomic rename)
            fd, tmp_path = tempfile.mkstemp(
                dir=str(path.parent), suffix=".tmp", prefix=".mem_"
            )
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as f:
                    f.write(content)
                    f.flush()
                    os.fsync(f.fileno())
                atomic_replace(tmp_path, path)
            except BaseException:
                # Clean up temp file on any failure
                try:
                    os.unlink(tmp_path)
                except OSError:
                    pass
                raise
        except (OSError, IOError) as e:
            raise RuntimeError(f"Failed to write memory file {path}: {e}")


def memory_tool(
    action: str,
    target: str = "memory",
    scope: str = "global",
    content: str = None,
    old_text: str = None,
    operations: list = None,
    store: Optional[MemoryStore] = None,
) -> str:
    """
    Single entry point for the memory tool. Dispatches to MemoryStore methods.

    ``scope`` controls where memory is persisted:
      - "global" → ~/.hermes/memories/ (shared across all projects)
      - "project" → .hermes-project/memories/ (portable with the project)

    Returns JSON string with results.
    """
    if store is None:
        return tool_error("Memory is not available. It may be disabled in config or this environment.", success=False)

    if action == "batch":
        return _handle_batch(operations or [], store)

    if target not in {"memory", "user"}:
        return tool_error(f"Invalid target '{target}'. Use 'memory' or 'user'.", success=False)

    if scope not in {"global", "project"}:
        return tool_error(f"Invalid scope '{scope}'. Use 'global' or 'project'.", success=False)

    if scope == "project" and not store._project_mem_dir:
        return tool_error("Project-scoped memory is not available (no .hermes-project/ detected in cwd).", success=False)

    if action == "add":
        if not content:
            return tool_error("Content is required for 'add' action.", success=False)
        result = store.add(target, content, scope=scope)

    elif action == "replace":
        if not old_text:
            return tool_error("old_text is required for 'replace' action.", success=False)
        if not content:
            return tool_error("content is required for 'replace' action.", success=False)
        result = store.replace(target, old_text, content, scope=scope)

    elif action == "remove":
        if not old_text:
            return tool_error("old_text is required for 'remove' action.", success=False)
        result = store.remove(target, old_text, scope=scope)

    else:
        return tool_error(f"Unknown action '{action}'. Use: add, replace, remove, batch", success=False)

    return json.dumps(result, ensure_ascii=False)


def _handle_batch(operations: list, store: "MemoryStore") -> str:
    """Execute multiple memory operations atomically."""
    if not operations:
        return tool_error("operations array is required for 'batch' action.", success=False)
    if len(operations) > 20:
        return tool_error("Batch limited to 20 operations.", success=False)

    results = []
    for i, op in enumerate(operations):
        if not isinstance(op, dict):
            results.append({"index": i, "success": False, "error": "Operation must be an object"})
            break
        act = op.get("action", "")
        tgt = op.get("target", "memory")
        scp = op.get("scope", "global")
        cnt = op.get("content")
        old = op.get("old_text")

        if tgt not in {"memory", "user"}:
            results.append({"index": i, "success": False, "error": f"Invalid target '{tgt}'"})
            break
        if scp not in {"global", "project"}:
            results.append({"index": i, "success": False, "error": f"Invalid scope '{scp}'"})
            break
        if scp == "project" and not store._project_mem_dir:
            results.append({"index": i, "success": False, "error": "Project scope not available"})
            break

        try:
            if act == "add":
                if not cnt:
                    results.append({"index": i, "success": False, "error": "content required"})
                    break
                r = store.add(tgt, cnt, scope=scp)
            elif act == "replace":
                if not old or not cnt:
                    results.append({"index": i, "success": False, "error": "old_text and content required"})
                    break
                r = store.replace(tgt, old, cnt, scope=scp)
            elif act == "remove":
                if not old:
                    results.append({"index": i, "success": False, "error": "old_text required"})
                    break
                r = store.remove(tgt, old, scope=scp)
            else:
                results.append({"index": i, "success": False, "error": f"Unknown action '{act}'"})
                break
            results.append({"index": i, "success": r.get("success", False), "result": r})
        except Exception as e:
            results.append({"index": i, "success": False, "error": str(e)})
            break

    return json.dumps({
        "batch": True,
        "completed": len(results),
        "total": len(operations),
        "results": results,
    }, ensure_ascii=False)


def check_memory_requirements() -> bool:
    """Memory tool has no external requirements -- always available."""
    return True


# =============================================================================
# OpenAI Function-Calling Schema
# =============================================================================

MEMORY_SCHEMA = {
    "name": "memory",
    "description": (
        "Save durable information to persistent memory that survives across sessions. "
        "Memory is injected into future turns, so keep it compact and focused on facts "
        "that will still matter later.\n\n"
        "WHEN TO SAVE (do this proactively, don't wait to be asked):\n"
        "- User corrects you or says 'remember this' / 'don't do that again'\n"
        "- User shares a preference, habit, or personal detail (name, role, timezone, coding style)\n"
        "- You discover something about the environment (OS, installed tools, project structure)\n"
        "- You learn a convention, API quirk, or workflow specific to this user's setup\n"
        "- You identify a stable fact that will be useful again in future sessions\n\n"
        "PRIORITY: User preferences and corrections > environment facts > procedural knowledge. "
        "The most valuable memory prevents the user from having to repeat themselves.\n\n"
        "Do NOT save task progress, session outcomes, completed-work logs, or temporary TODO "
        "state to memory; use session_search to recall those from past transcripts.\n"
        "If you've discovered a new way to do something, solved a problem that could be "
        "necessary later, save it as a skill with the skill tool.\n\n"
        "TWO TARGETS:\n"
        "- 'user': who the user is -- name, role, preferences, communication style, pet peeves\n"
        "- 'memory': your notes -- environment facts, project conventions, tool quirks, lessons learned\n\n"
        "TWO SCOPES:\n"
        "- 'global': cross-project, always loaded (user preferences, coding style, environment facts)\n"
        "- 'project': current project only, stored in .hermes-project/memories/ (client context, "
        "document conventions, project-specific standards). Only available when cwd contains "
        ".hermes-project/.\n\n"
        "SCOPE GUIDANCE:\n"
        "- User preferences/feedback → scope=global\n"
        "- Client/project-specific conventions, document templates, naming rules → scope=project\n"
        "- Tool/CLI quirks, environment facts, coding patterns → scope=global\n"
        "- Legal review SOPs, contract clause standards tied to one client → scope=project\n\n"
        "ACTIONS: add (new entry), replace (update existing -- old_text identifies it), "
        "remove (delete -- old_text identifies it), batch (multiple ops in one call).\n\n"
        "BATCH: Use action='batch' with an 'operations' array. Each element has: "
        "{action, target, scope, content, old_text}. Operations execute in order; "
        "if one fails, remaining operations are skipped.\n\n"
        "SKIP: trivial/obvious info, things easily re-discovered, raw data dumps, and temporary task state."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["add", "replace", "remove", "batch"],
                "description": "The action to perform."
            },
            "target": {
                "type": "string",
                "enum": ["memory", "user"],
                "description": "Which memory store: 'memory' for personal notes, 'user' for user profile."
            },
            "scope": {
                "type": "string",
                "enum": ["global", "project"],
                "description": "Which memory scope: 'global' for cross-project, 'project' for current project only."
            },
            "content": {
                "type": "string",
                "description": "The entry content. Required for 'add' and 'replace'."
            },
            "old_text": {
                "type": "string",
                "description": "Short unique substring identifying the entry to replace or remove."
            },
            "operations": {
                "type": "array",
                "description": "For batch action: array of operations. Each has {action, target, scope, content, old_text}.",
                "items": {
                    "type": "object",
                    "properties": {
                        "action": {"type": "string", "enum": ["add", "replace", "remove"]},
                        "target": {"type": "string", "enum": ["memory", "user"]},
                        "scope": {"type": "string", "enum": ["global", "project"]},
                        "content": {"type": "string"},
                        "old_text": {"type": "string"},
                    },
                    "required": ["action", "target"],
                },
            },
        },
        "required": ["action", "target"],
    },
}


# --- Registry ---
from tools.registry import registry, tool_error

registry.register(
    name="memory",
    toolset="memory",
    schema=MEMORY_SCHEMA,
    handler=lambda args, **kw: memory_tool(
        action=args.get("action", ""),
        target=args.get("target", "memory"),
        scope=args.get("scope", "global"),
        content=args.get("content"),
        old_text=args.get("old_text"),
        operations=args.get("operations"),
        store=kw.get("store")),
    check_fn=check_memory_requirements,
    emoji="🧠",
)




