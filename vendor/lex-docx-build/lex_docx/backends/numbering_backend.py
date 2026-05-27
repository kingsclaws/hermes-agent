from __future__ import annotations

from .common import require_implemented


def inspect_numbering(*, backend: str, **kwargs):
    if backend in ("legacy", "ooxml"):
        from lex_docx import numbering_ops
        return numbering_ops.inspect_numbering(**kwargs)
    require_implemented("numbering inspect", backend)


def restart_numbering(*, backend: str, **kwargs):
    if backend in ("legacy", "ooxml"):
        from lex_docx import numbering_ops
        return numbering_ops.restart_numbering(**kwargs)
    require_implemented("numbering restart", backend)


def find_section_scope(*, backend: str, **kwargs):
    if backend in ("legacy", "ooxml"):
        from lex_docx import numbering_ops
        return numbering_ops.find_section_scope(**kwargs)
    require_implemented("section scope", backend)
