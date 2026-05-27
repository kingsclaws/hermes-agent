"""Hermes Client — client profile management.

Each client gets a directory under ~/.hermes/clients/<name>/ with:
  - client-context.md  — freeform context, auto-loaded into agent system prompt
  - preferences.md     — formatting / style preferences

The DB stores structured metadata (name, slug, project count) for CLI queries.
"""

from __future__ import annotations

import os
import re
import unicodedata
from pathlib import Path


def _get_hermes_home() -> Path:
    """Resolve HERMES_HOME directory."""
    return Path(os.environ.get("HERMES_HOME", Path.home() / ".hermes"))


def _slugify(name: str) -> str:
    """Generate a filesystem-safe slug from Chinese/Unicode name.

    For names with no ASCII content (pure CJK etc.), falls back to a
    short hash so the slug is always unique and non-empty.
    """
    nfkd = unicodedata.normalize("NFKD", name.lower())
    ascii_name = "".join(c for c in nfkd if not unicodedata.combining(c))
    # Keep ASCII letters/digits, replace everything else with hyphens
    slug = re.sub(r"[^a-z0-9]+", "-", ascii_name).strip("-")
    # If the name is entirely non-ASCII (e.g. pure Chinese), generate a
    # stable short hash so we still get a unique, usable slug
    if not slug:
        import hashlib
        slug = "cl-" + hashlib.blake2b(
            name.encode("utf-8"), digest_size=5
        ).hexdigest()
    return slug


def _client_dir(name: str) -> Path:
    """Return ~/.hermes/clients/<name>/."""
    return _get_hermes_home() / "clients" / name


def _ensure_client_directory(name: str) -> Path:
    """Ensure the client profile directory exists. Returns the Path."""
    cdir = _client_dir(name)
    cdir.mkdir(parents=True, exist_ok=True)
    return cdir


from hermes_cli.project_commands import CLIENT_CONTEXT_TEMPLATE, PREFERENCES_TEMPLATE


# ── CLI handlers ──────────────────────────────────────────────────────────────

def client_create(args) -> None:
    """Create a client profile."""
    from hermes_state import SessionDB

    name = args.name.strip()
    if not name:
        print("Client name is required.")
        return

    db = SessionDB()
    existing = db.get_client(name)
    if existing:
        print(f"Client already exists: {existing['name']}")
        print(f"  Path: {existing['path']}")
        print(f"  Projects: {existing['project_count']}")
        return

    cdir = _ensure_client_directory(name)
    slug = _slugify(name)
    path = str(cdir)

    # Write template files only if they don't exist
    context_file = cdir / "client-context.md"
    if not context_file.exists():
        context_file.write_text(
            CLIENT_CONTEXT_TEMPLATE.format(client_name=name), encoding="utf-8"
        )

    prefs_file = cdir / "preferences.md"
    if not prefs_file.exists():
        prefs_file.write_text(
            PREFERENCES_TEMPLATE.format(client_name=name), encoding="utf-8"
        )

    client_id = db.create_client(name, path)
    print(f"Client profile created: {name}")
    print(f"  ID:        {client_id}")
    print(f"  Path:      {path}")
    print(f"  Files:     client-context.md, preferences.md")
    print()
    print(f"Next: edit {cdir / 'client-context.md'} and")
    print(f"  {cdir / 'preferences.md'} to customize.")


def client_list(args) -> None:
    """List all registered clients."""
    from hermes_state import SessionDB

    db = SessionDB()
    clients = db.list_clients()

    if not clients:
        print("No clients registered.")
        print(f"Create one with: hermes client create <name>")
        return

    print(f"{'ID':<16} {'Name':<20} {'Projects':<10} {'Path'}")
    print("-" * 90)
    for c in clients:
        cid = c["id"][:14]
        name = c["name"][:18]
        count = str(c.get("project_count", 0))
        path = c["path"]
        print(f"{cid:<16} {name:<20} {count:<10} {path}")


def client_show(args) -> None:
    """Show client details — metadata, linked projects, and file contents."""
    from hermes_state import SessionDB

    db = SessionDB()
    client = db.get_client(args.name)
    if not client:
        print(f"Client not found: {args.name}")
        return

    print(f"Client: {client['name']}")
    print(f"  ID:       {client['id']}")
    print(f"  Slug:     {client['slug']}")
    print(f"  Path:     {client['path']}")
    print(f"  Projects: {client['project_count']}")
    print(f"  Created:  {client['created_at']}")

    # Show linked projects
    projects = db.list_projects()
    client_projects = [
        p for p in projects if p.get("client", "") == client["name"]
    ]
    if client_projects:
        print()
        print("Linked projects:")
        for p in client_projects:
            status = p["status"]
            print(f"  [{status:<12}] {p['name']} — {p.get('goal', '')}")

    # Show file contents
    cdir = Path(client["path"])
    for fname in ["client-context.md", "preferences.md"]:
        fpath = cdir / fname
        if fpath.is_file():
            print()
            print(f"─── {fname} ───")
            content = fpath.read_text(encoding="utf-8").strip()
            # Cap at 3000 chars for CLI display
            if len(content) > 3000:
                content = content[:3000] + "\n\n... (truncated)"
            print(content)
