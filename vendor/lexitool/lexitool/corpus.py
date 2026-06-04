"""
corpus.py — Multi-document corpus indexing and search for lexitool.

Index all .docx files in a project directory, split each into clauses,
and provide search across the corpus. Pure Python, no external dependencies
beyond the standard library + lxml.
"""
from __future__ import annotations

import json
import os
import re
import time
from dataclasses import dataclass, field
from pathlib import Path

from .clause_ops import split_clauses, Clause


# ── Data structures ────────────────────────────────────────────────────────────


@dataclass
class ClauseRef:
    """Reference to a specific clause in the corpus."""
    doc_path: str
    doc_name: str
    clause_id: str
    title: str
    para_start: int
    para_end: int
    clause_type: str
    key_terms: list[str]
    detection: str


@dataclass
class CorpusMeta:
    """Corpus-level metadata."""
    dir_path: str
    indexed_at: float          # unix timestamp
    doc_count: int
    clause_count: int
    documents: dict[str, int]  # doc_name → clause_count


# ── Internal helpers ───────────────────────────────────────────────────────────


def _corpus_index_path(dir_path: str) -> Path:
    return Path(dir_path) / ".lex_corpus" / "index.json"


def _load_index(dir_path: str) -> dict | None:
    """Load corpus index from disk, or None if not indexed."""
    path = _corpus_index_path(dir_path)
    if not path.exists():
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return None


def _save_index(dir_path: str, index_data: dict) -> str:
    """Save corpus index to disk."""
    index_dir = Path(dir_path) / ".lex_corpus"
    index_dir.mkdir(parents=True, exist_ok=True)
    path = index_dir / "index.json"
    with open(path, "w", encoding="utf-8") as f:
        json.dump(index_data, f, ensure_ascii=False, indent=2)
    return str(path)


# ── Index ──────────────────────────────────────────────────────────────────────


def index_dir(dir_path: str, pattern: str = "*.docx") -> dict:
    """Index all .docx files in a directory.

    Walks the directory recursively, splits each .docx into clauses,
    and builds an inverted index for fast search.

    Args:
        dir_path: Root directory to scan for .docx files.
        pattern: Glob pattern for document files (default: "*.docx").

    Returns:
        Dict with corpus metadata (doc_count, clause_count, index_path).
    """
    base = Path(dir_path)
    if not base.exists():
        raise FileNotFoundError(f"Directory not found: {dir_path}")

    doc_paths = sorted(base.rglob(pattern))
    # Filter out hidden dirs and temp files
    doc_paths = [p for p in doc_paths
                 if not any(part.startswith(".") for part in p.parts[:-1])
                 and not p.name.startswith("~")]
    # Filter out files in .lex_corpus
    doc_paths = [p for p in doc_paths
                 if ".lex_corpus" not in p.parts]

    documents: dict[str, list[dict]] = {}
    inverted_index: dict[str, list[dict]] = {}
    total_clauses = 0

    for doc_path in doc_paths:
        doc_str = str(doc_path.relative_to(base))
        doc_name = doc_path.name
        try:
            clauses = split_clauses(str(doc_path))
        except Exception as e:
            documents[doc_str] = [{"error": str(e)}]
            continue

        clause_dicts = []
        for c in clauses:
            clause_dicts.append({
                "id": c.id, "title": c.title,
                "para_start": c.para_start, "para_end": c.para_end,
                "level": c.level, "type": c.clause_type,
                "key_terms": c.key_terms, "detection": c.detection,
            })
            total_clauses += 1

            # Update inverted index
            for term in c.key_terms:
                term_lower = term.lower()
                if term_lower not in inverted_index:
                    inverted_index[term_lower] = []
                inverted_index[term_lower].append({
                    "doc": doc_str, "doc_name": doc_name,
                    "clause_id": c.id, "title": c.title,
                    "para_start": c.para_start, "para_end": c.para_end,
                    "type": c.clause_type,
                })

            # Also index significant words from the title (for free-text search)
            title_words = re.findall(r'\w+', c.title.lower())
            for word in title_words:
                if len(word) >= 2 and word not in inverted_index:
                    inverted_index[word] = []
                if len(word) >= 2:
                    # Only add if not already added via key_terms for this clause
                    existing = inverted_index.get(word, [])
                    if not any(e.get("clause_id") == c.id and e.get("doc") == doc_str
                              for e in existing):
                        inverted_index.setdefault(word, []).append({
                            "doc": doc_str, "doc_name": doc_name,
                            "clause_id": c.id, "title": c.title,
                            "para_start": c.para_start, "para_end": c.para_end,
                            "type": c.clause_type,
                        })

        documents[doc_str] = clause_dicts

    index_data = {
        "dir_path": str(base),
        "indexed_at": time.time(),
        "doc_count": len(doc_paths),
        "clause_count": total_clauses,
        "documents": {k: len(v) for k, v in documents.items()},
        "clauses": documents,
        "inverted_index": inverted_index,
    }

    index_path = _save_index(dir_path, index_data)
    return {
        "ok": True,
        "dir_path": str(base),
        "doc_count": len(doc_paths),
        "clause_count": total_clauses,
        "index_path": index_path,
        "documents": {k: len(v) for k, v in documents.items()},
    }


# ── Search ─────────────────────────────────────────────────────────────────────


def search_corpus(dir_path: str, query: str | None = None,
                  clause_type: str | None = None,
                  terms: list[str] | None = None,
                  limit: int = 20) -> dict:
    """Search the indexed corpus for matching clauses.

    Args:
        dir_path: Root directory of the indexed corpus.
        query: Free-text search string (searches clause titles and terms).
        clause_type: Filter by clause type (definitions, representations, etc.).
        terms: Filter by specific defined terms (exact match against index).
        limit: Maximum results to return.

    Returns:
        Dict with matches list and search metadata.
    """
    index = _load_index(dir_path)
    if index is None:
        return {"error": f"Corpus not indexed. Run lex_corpus op=index first.",
                "dir_path": dir_path}

    clauses = index.get("clauses", {})
    inverted_index = index.get("inverted_index", {})

    # Collect candidate matches
    candidates: dict[tuple[str, str], dict] = {}  # (doc, clause_id) → match

    # Search by terms in inverted index
    if terms:
        for term in terms:
            term_lower = term.lower()
            for entry in inverted_index.get(term_lower, []):
                key = (entry["doc"], entry["clause_id"])
                if key not in candidates:
                    candidates[key] = {
                        **entry,
                        "score": 0,
                        "matched_terms": [],
                    }
                candidates[key]["score"] += 10
                candidates[key]["matched_terms"].append(term)

    # Search by clause_type
    if clause_type:
        for doc_name, clause_list in clauses.items():
            if isinstance(clause_list, list):
                for c in clause_list:
                    if isinstance(c, dict) and c.get("type") == clause_type:
                        key = (doc_name, c["id"])
                        if key not in candidates:
                            candidates[key] = {
                                "doc": doc_name,
                                "doc_name": Path(doc_name).name,
                                "clause_id": c["id"],
                                "title": c["title"],
                                "para_start": c["para_start"],
                                "para_end": c["para_end"],
                                "type": c["type"],
                                "score": 0,
                                "matched_terms": [],
                            }
                        candidates[key]["score"] += 5

    # Free-text query: search title and inverted index
    if query:
        query_lower = query.lower()
        query_words = re.findall(r'\w+', query_lower)
        for doc_name, clause_list in clauses.items():
            if isinstance(clause_list, list):
                for c in clause_list:
                    if not isinstance(c, dict):
                        continue
                    score = 0
                    # Title match
                    title_lo = c.get("title", "").lower()
                    if query_lower in title_lo:
                        score += 8
                    for word in query_words:
                        if word in title_lo:
                            score += 3
                    # Key terms match
                    for term in c.get("key_terms", []):
                        if query_lower in term.lower():
                            score += 5
                        elif any(w in term.lower() for w in query_words):
                            score += 2
                    if score > 0:
                        key = (doc_name, c["id"])
                        if key not in candidates:
                            candidates[key] = {
                                "doc": doc_name,
                                "doc_name": Path(doc_name).name,
                                "clause_id": c["id"],
                                "title": c["title"],
                                "para_start": c["para_start"],
                                "para_end": c["para_end"],
                                "type": c.get("type", "general"),
                                "score": 0,
                                "matched_terms": [],
                            }
                        candidates[key]["score"] += score

    # If no criteria given, return all clauses (capped)
    if not query and not clause_type and not terms:
        for doc_name, clause_list in clauses.items():
            if isinstance(clause_list, list):
                for c in clause_list:
                    if isinstance(c, dict):
                        key = (doc_name, c["id"])
                        candidates[key] = {
                            "doc": doc_name,
                            "doc_name": Path(doc_name).name,
                            "clause_id": c["id"],
                            "title": c["title"],
                            "para_start": c["para_start"],
                            "para_end": c["para_end"],
                            "type": c.get("type", "general"),
                            "score": 0,
                            "matched_terms": [],
                        }

    # Sort by score descending, limit
    sorted_matches = sorted(candidates.values(),
                            key=lambda m: m["score"], reverse=True)[:limit]

    return {
        "ok": True,
        "query": query,
        "clause_type": clause_type,
        "terms": terms,
        "total_matches": len(sorted_matches),
        "matches": sorted_matches,
    }


# ── Status ─────────────────────────────────────────────────────────────────────


def corpus_status(dir_path: str) -> dict:
    """Get corpus status and metadata.

    Args:
        dir_path: Root directory of the corpus.

    Returns:
        Dict with corpus stats or error if not indexed.
    """
    index = _load_index(dir_path)
    if index is None:
        # Count .docx files even without index
        base = Path(dir_path)
        doc_count = 0
        if base.exists():
            doc_count = len([p for p in base.rglob("*.docx")
                            if not any(part.startswith(".") for part in p.parts[:-1])
                            and ".lex_corpus" not in p.parts])
        return {
            "ok": True,
            "indexed": False,
            "dir_path": str(base),
            "doc_count": doc_count,
            "message": "Corpus not yet indexed. Run lex_corpus op=index.",
        }

    return {
        "ok": True,
        "indexed": True,
        "dir_path": index.get("dir_path", dir_path),
        "indexed_at": index.get("indexed_at"),
        "indexed_time": time.strftime(
            "%Y-%m-%d %H:%M:%S",
            time.localtime(index["indexed_at"])
        ) if index.get("indexed_at") else "unknown",
        "doc_count": index.get("doc_count", 0),
        "clause_count": index.get("clause_count", 0),
        "documents": index.get("documents", {}),
    }
