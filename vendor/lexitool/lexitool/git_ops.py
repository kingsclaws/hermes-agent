"""
git_ops.py — Git version control helpers for legal projects.

Each legal project directory is an independent git repo. This module
provides the programmatic interface for HPSwarm agents to commit, tag,
branch, and restore documents during the drafting/revision lifecycle.

Operations:
  - ensure_repo:      Initialize a git repo in a project directory
  - snapshot:         Commit current state with structured message
  - deliver_tag:      Tag a delivery milestone
  - revision_branch:  Create/switch branch for a revision round
  - status:           Quick dirty/clean check
  - log:              Recent commit history for audit trail
"""
from __future__ import annotations

import logging
import os
import subprocess
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# Default .gitignore content for legal project repos
GITIGNORE_CONTENT = """\
# Office temp files
~$*.docx
~$*.doc
~$*.xlsx
~$*.pptx
*.tmp
*.temp

# Office lock files
.~lock.*#

# Word autorecovery
AutoRecovery save of *.asd

# macOS
.DS_Store
.AppleDouble
.LSOverride
._*

# Thumbnails
Thumbs.db
ehthumbs.db

# Redline/Markup working files (generated during delivery)
[Redline]*.docx
[Mark-up]*.docx
[Page-pull]*.pdf

# Git worktrees (parallel revision branches)
.worktrees/
"""


@dataclass
class GitResult:
    ok: bool
    message: str = ""
    data: dict = field(default_factory=dict)
    commit_hash: str = ""
    tag: str = ""
    branch: str = ""


def _run(project_dir: str, args: list[str], timeout: int = 30) -> tuple[int, str, str]:
    """Run a git command in project_dir. Returns (returncode, stdout, stderr)."""
    try:
        p = subprocess.run(
            ["git", "-C", project_dir] + args,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        return p.returncode, p.stdout.strip(), p.stderr.strip()
    except subprocess.TimeoutExpired:
        return -1, "", "Git command timed out"
    except FileNotFoundError:
        return -1, "", "git not found on PATH"


def _is_git_repo(project_dir: str) -> bool:
    """Check if project_dir is already a git repository."""
    rc, _, _ = _run(project_dir, ["rev-parse", "--git-dir"])
    return rc == 0


def _current_branch(project_dir: str) -> str:
    _, out, _ = _run(project_dir, ["rev-parse", "--abbrev-ref", "HEAD"])
    return out or "unknown"


def _head_hash(project_dir: str) -> str:
    _, out, _ = _run(project_dir, ["rev-parse", "--short", "HEAD"])
    return out or ""


def ensure_repo(project_dir: str, *, initial_branch: str = "master") -> GitResult:
    """Ensure a project directory is a git repository.

    If not already a repo, initializes one and makes a baseline commit
    of all existing files. Safe to call on already-initialized repos.

    Returns GitResult with branch and commit_hash populated.
    """
    proj = Path(project_dir)
    if not proj.is_dir():
        return GitResult(ok=False, message=f"Directory not found: {project_dir}")

    if _is_git_repo(project_dir):
        return GitResult(
            ok=True,
            message="Repository already exists",
            branch=_current_branch(project_dir),
            commit_hash=_head_hash(project_dir),
            data={"already_initialized": True},
        )

    # Write .gitignore
    gitignore_path = proj / ".gitignore"
    if not gitignore_path.exists():
        gitignore_path.write_text(GITIGNORE_CONTENT, encoding="utf-8")

    # git init
    rc, _, err = _run(project_dir, ["init", "-b", initial_branch])
    if rc != 0:
        return GitResult(ok=False, message=f"git init failed: {err}")

    # Stage all files and baseline commit
    rc, _, err = _run(project_dir, ["add", "-A"], timeout=120)
    if rc != 0:
        return GitResult(ok=False, message=f"git add failed: {err}")

    rc, _, err = _run(project_dir, [
        "commit", "-m", f"baseline: initial commit — {proj.name}",
    ], timeout=120)
    if rc != 0:
        return GitResult(ok=False, message=f"git commit failed: {err}")

    return GitResult(
        ok=True,
        message=f"Repository initialized with baseline commit",
        branch=initial_branch,
        commit_hash=_head_hash(project_dir),
    )


def snapshot(
    project_dir: str,
    message: str,
    *,
    author: str = "hermes-agent",
    tag: Optional[str] = None,
    allow_empty: bool = False,
) -> GitResult:
    """Commit the current state of the project.

    Args:
        project_dir: Path to the git repo (project root).
        message:     Commit message. Structured format recommended:
                     "round: <what changed> — <who>"
        author:      Author name for the commit.
        tag:         Optional tag name to apply after commit.
        allow_empty: If True, creates commit even with no changes.
    """
    if not _is_git_repo(project_dir):
        return GitResult(ok=False, message="Not a git repository — run ensure_repo first")

    # Stage all changes
    rc, _, err = _run(project_dir, ["add", "-A"], timeout=120)
    if rc != 0:
        return GitResult(ok=False, message=f"git add failed: {err}")

    # Check if there are staged changes
    rc, stdout, _ = _run(project_dir, ["diff", "--cached", "--quiet"])
    if rc == 0 and not allow_empty:
        return GitResult(
            ok=True,
            message="No changes to commit",
            branch=_current_branch(project_dir),
            commit_hash=_head_hash(project_dir),
            data={"no_changes": True},
        )

    args = ["commit", "-m", message, f"--author={author} <{author}@hermes-agent.local>"]
    if allow_empty:
        args.append("--allow-empty")

    rc, _, err = _run(project_dir, args, timeout=120)
    if rc != 0:
        return GitResult(ok=False, message=f"git commit failed: {err}")

    result = GitResult(
        ok=True,
        message=f"Committed: {message}",
        branch=_current_branch(project_dir),
        commit_hash=_head_hash(project_dir),
    )

    # Apply tag if requested
    if tag:
        rc, _, err = _run(project_dir, ["tag", "-a", tag, "-m", message])
        if rc != 0:
            result.message += f" (tag failed: {err})"
        else:
            result.tag = tag

    return result


def deliver_tag(
    project_dir: str,
    version: str,
    *,
    message: Optional[str] = None,
    push: bool = False,
) -> GitResult:
    """Tag the current HEAD as a delivery point.

    Creates annotated tags like 'deliver/v1-20260612'.

    Args:
        project_dir: Path to the git repo.
        version:     Version identifier, e.g. "v1", "v2-final", "draft-for-comment".
        message:     Tag message (defaults to auto-generated).
        push:        If True, also pushes the tag (requires remote configured).
    """
    if not _is_git_repo(project_dir):
        return GitResult(ok=False, message="Not a git repository")

    today = datetime.now(timezone.utc).strftime("%Y%m%d")
    tag_name = f"deliver/{version}-{today}"
    tag_msg = message or f"Delivery: {version} — {today}"

    hash_before = _head_hash(project_dir)

    rc, _, err = _run(project_dir, ["tag", "-a", tag_name, "-m", tag_msg])
    if rc != 0:
        return GitResult(ok=False, message=f"Tag creation failed: {err}")

    result = GitResult(
        ok=True,
        message=f"Tagged delivery: {tag_name}",
        tag=tag_name,
        branch=_current_branch(project_dir),
        commit_hash=hash_before,
    )

    if push:
        rc, _, err = _run(project_dir, ["push", "origin", tag_name], timeout=60)
        if rc != 0:
            result.message += f" (push failed: {err})"
        else:
            result.data["pushed"] = True

    return result


def revision_branch(
    project_dir: str,
    name: str,
    *,
    create: bool = True,
    switch: bool = True,
) -> GitResult:
    """Create and/or switch to a revision branch.

    Standard branch naming: 'rev/<document>/<round>'
    Example: 'rev/support-letter/v2-citic-review'

    Args:
        project_dir: Path to the git repo.
        name:        Branch name.
        create:      If True, creates the branch (from current HEAD).
        switch:      If True, switches to the branch.
    """
    if not _is_git_repo(project_dir):
        return GitResult(ok=False, message="Not a git repository")

    if create:
        rc, _, err = _run(project_dir, ["branch", name])
        if rc != 0:
            return GitResult(ok=False, message=f"Branch creation failed: {err}")

    if switch:
        rc, _, err = _run(project_dir, ["checkout", name])
        if rc != 0:
            return GitResult(ok=False, message=f"Checkout failed: {err}")

    return GitResult(
        ok=True,
        message=f"On branch: {name}",
        branch=name,
        commit_hash=_head_hash(project_dir),
    )


def status(project_dir: str) -> GitResult:
    """Quick dirty/clean check + summary of changes."""
    if not _is_git_repo(project_dir):
        return GitResult(ok=False, message="Not a git repository")

    _, out, _ = _run(project_dir, ["status", "--short"])
    files = [line[3:] for line in out.split("\n") if line.strip()] if out else []

    return GitResult(
        ok=True,
        message=f"{len(files)} files with changes" if files else "Working tree clean",
        branch=_current_branch(project_dir),
        commit_hash=_head_hash(project_dir),
        data={
            "dirty": len(files) > 0,
            "changed_files": files[:50],  # cap at 50
            "total_changed": len(files),
        },
    )


def log(project_dir: str, n: int = 10) -> GitResult:
    """Return recent commit history for audit trail."""
    if not _is_git_repo(project_dir):
        return GitResult(ok=False, message="Not a git repository")

    rc, out, _ = _run(project_dir, [
        "log", f"-{n}", "--oneline", "--decorate",
        "--format=%h %ad %s",
        "--date=short",
    ])
    if rc != 0:
        return GitResult(ok=False, message="git log failed")

    commits = out.split("\n") if out else []
    return GitResult(
        ok=True,
        message=f"{len(commits)} commits in history",
        data={"commits": commits},
        branch=_current_branch(project_dir),
        commit_hash=_head_hash(project_dir),
    )


# ═══════════════════════════════════════════════════════════════════════════════
# Worktree operations — parallel revision workflows
# ═══════════════════════════════════════════════════════════════════════════════

def create_worktree(
    project_dir: str,
    name: str,
    *,
    base_branch: Optional[str] = None,
) -> GitResult:
    """Create a git worktree for parallel work on the same project.

    Worktrees enable simultaneous preparation of alternative negotiation
    positions without interfering with the main working copy. Each worktree
    is an independent checkout on its own branch, stored under
    <project_dir>/.worktrees/<name>/.

    Use cases:
    - Preparing "aggressive" and "conservative" versions simultaneously
    - Reviewer inspects v2 in worktree while Drafter builds v3 in main tree
    - Side-by-side comparison of two negotiation rounds

    Important limitations:
    - .docx files are binary — NEVER edit the same file in two worktrees
    - Merging worktree branches will always conflict on binary files
    - Worktrees are for exploration/alternatives, NOT for merge-based collaboration

    Args:
        project_dir: Path to the main git repo (project root).
        name:        Worktree name (becomes branch name + directory name).
        base_branch: Branch or commit to base the worktree on (default: current HEAD).
    """
    if not _is_git_repo(project_dir):
        return GitResult(ok=False, message="Not a git repository")

    base = base_branch or _current_branch(project_dir)

    # Create branch for the worktree if it doesn't exist
    rc, _, err = _run(project_dir, ["rev-parse", "--verify", name])
    if rc != 0:
        rc, _, err = _run(project_dir, ["branch", name, base])
        if rc != 0:
            return GitResult(ok=False, message=f"Failed to create branch '{name}': {err}")

    # Create the worktree directory under .worktrees/
    wt_path = os.path.join(project_dir, ".worktrees", name)

    if os.path.exists(wt_path):
        return GitResult(ok=False, message=f"Worktree path already exists: {wt_path}")

    rc, stdout, err = _run(project_dir, [
        "worktree", "add", wt_path, name,
    ], timeout=60)

    if rc != 0:
        return GitResult(ok=False, message=f"git worktree add failed: {err}")

    return GitResult(
        ok=True,
        message=f"Worktree created: {wt_path}",
        branch=name,
        data={
            "worktree_path": wt_path,
            "base_branch": base,
        },
    )


def list_worktrees(project_dir: str) -> GitResult:
    """List all worktrees for a project repo."""
    if not _is_git_repo(project_dir):
        return GitResult(ok=False, message="Not a git repository")

    rc, out, err = _run(project_dir, ["worktree", "list"])
    if rc != 0:
        return GitResult(ok=False, message=f"worktree list failed: {err}")

    worktrees = []
    for line in out.split("\n"):
        line = line.strip()
        if not line:
            continue
        # git worktree list format: <path> <hash> [<branch>]
        # Path may contain spaces, so we parse right-to-left from the end
        # Find the last space-separated token (the branch in brackets)
        brace_idx = line.rfind("[")
        if brace_idx >= 0:
            branch = line[brace_idx:].strip("[]")
            rest = line[:brace_idx].strip()
        else:
            branch = "detached"
            rest = line.strip()
        # rest = <path> ... <hash>
        # Hash is the last word; path is everything before it
        rest_parts = rest.rsplit(None, 1)
        if len(rest_parts) == 2:
            path = rest_parts[0]
            hash_val = rest_parts[1]
        elif rest_parts:
            path = rest_parts[0]
            hash_val = ""
        else:
            continue
        worktrees.append({
            "path": path,
            "branch": branch,
            "hash": hash_val,
        })

    return GitResult(
        ok=True,
        message=f"{len(worktrees)} worktrees",
        data={"worktrees": worktrees},
        branch=_current_branch(project_dir),
    )


def remove_worktree(
    project_dir: str,
    name: str,
    *,
    force: bool = False,
) -> GitResult:
    """Remove a worktree and its branch.

    Args:
        project_dir: Path to the main git repo.
        name:        Worktree name.
        force:       If True, force removal even with uncommitted changes.
    """
    if not _is_git_repo(project_dir):
        return GitResult(ok=False, message="Not a git repository")

    wt_path = os.path.join(project_dir, ".worktrees", name)

    args = ["worktree", "remove"]
    if force:
        args.append("--force")
    args.append(wt_path)

    rc, _, err = _run(project_dir, args, timeout=30)
    if rc != 0:
        return GitResult(ok=False, message=f"worktree remove failed: {err}")

    # Also delete the branch
    _run(project_dir, ["branch", "-d", name])

    return GitResult(
        ok=True,
        message=f"Worktree removed: {name}",
        branch=_current_branch(project_dir),
    )


def prune_worktrees(project_dir: str) -> GitResult:
    """Prune stale worktree metadata (run after manual worktree deletions)."""
    if not _is_git_repo(project_dir):
        return GitResult(ok=False, message="Not a git repository")

    rc, _, err = _run(project_dir, ["worktree", "prune"])
    if rc != 0:
        return GitResult(ok=False, message=f"worktree prune failed: {err}")

    return GitResult(
        ok=True,
        message="Stale worktree metadata pruned",
        branch=_current_branch(project_dir),
    )
