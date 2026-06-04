from __future__ import annotations

from .common import require_implemented


def apply_format_brush(*, backend: str, **kwargs):
    if backend in ("legacy", "ooxml"):
        from lex_docx import format_brush
        return format_brush.apply(**kwargs)
    require_implemented("format-brush", backend)
