"""
lexitool — Atomic Word Document Manipulation for AI Agents

Provides a unified inline markup language that bridges the gap between
"agent sees plain text" and "Word documents have rich binary formatting."

Core modules:
  markup         — bidirectional OOXML ↔ annotated text bridge
  bookmarks      — w:bookmarkStart/End CRUD
  fields         — w:fldChar + instrText (REF/PAGEREF/PAGE/NUMPAGES/TOC)
  lists          — abstract numbering definition + numbering instance CRUD
  edit_ops       — insert/replace/delete (with TC)
  tc_utils       — TC XML element construction
  openxml_package — pure-lxml OPC read/write

Markup format (exported by lex_read, consumed by lex_edit):
  Format:   [b]bold[/b]  [i]italic[/i]  [u]underline[/u]  [s]strikethrough[/s]
            [font:name,size]text[/font]  [color:#FF0000]red[/color]
  TC:       [ins]added[/ins]  [del]deleted[/del]
  Lists:    [bullet:level]  [num:level,start]
  Refs:     [bookmark:name]text[/bookmark]  [ref:name]  [page-ref:name]
"""

from . import markup          # noqa: F401
from . import bookmarks       # noqa: F401
from . import fields          # noqa: F401
from . import lists           # noqa: F401
from . import edit_ops         # noqa: F401
from . import tc_utils         # noqa: F401
from . import openxml_package  # noqa: F401
from . import numbering_ops    # noqa: F401
from . import format_brush     # noqa: F401
from . import doc_create       # noqa: F401
from . import header_footer_ops  # noqa: F401
from . import toc_ops          # noqa: F401
from . import table_ops        # noqa: F401
from . import doctor           # noqa: F401
from . import doc_stats        # noqa: F401
from . import new_table_ops    # noqa: F401
from . import doc_scaffold     # noqa: F401
from . import constants        # noqa: F401
from . import config           # noqa: F401
from . import cleanup          # noqa: F401
from . import inject_engine    # noqa: F401
from . import footer_ops       # noqa: F401
from . import openxml_core     # noqa: F401
from . import iter_ops         # noqa: F401
from . import jt_note          # noqa: F401
from . import lint             # noqa: F401
from . import lint_config      # noqa: F401
from . import para_query       # noqa: F401
from . import tc_ops           # noqa: F401
from . import review_detailed  # noqa: F401
from . import defined_terms    # noqa: F401

from .config import DocConfig, PRESET_JT  # noqa: F401

__version__ = "0.5.0"
__all__ = [
    "markup", "bookmarks", "fields", "lists",
    "edit_ops", "tc_utils", "openxml_package", "numbering_ops",
    "format_brush", "doc_create", "header_footer_ops", "toc_ops",
    "table_ops", "doctor", "doc_stats", "new_table_ops",
    "doc_scaffold", "constants", "config", "cleanup",
    "inject_engine", "footer_ops", "openxml_core", "iter_ops",
    "jt_note", "lint", "lint_config", "para_query", "tc_ops",
    "review_detailed", "defined_terms",
    "DocConfig", "PRESET_JT",
]
