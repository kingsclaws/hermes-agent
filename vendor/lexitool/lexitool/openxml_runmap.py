"""Rendered paragraph/run mapping helpers for WordprocessingML.

This module is intentionally small and lxml-native.  It borrows the useful
shape of dolanmiu/docx's run-renderer/patcher design: render a paragraph into
plain text plus positional spans, then edit only the spans touched by a match.
"""
from __future__ import annotations

from dataclasses import dataclass

from lxml import etree


W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
W = f"{{{W_NS}}}"
XML_SPACE = "{http://www.w3.org/XML/1998/namespace}space"


@dataclass(frozen=True)
class TextSpan:
    el: etree._Element
    start: int
    end: int
    text: str
    editable: bool

    @property
    def run(self) -> etree._Element | None:
        cur = self.el
        while cur is not None:
            if cur.tag == f"{W}r":
                return cur
            cur = cur.getparent()
        return None


@dataclass(frozen=True)
class RenderedParagraph:
    paragraph: etree._Element
    text: str
    spans: list[TextSpan]

    @property
    def text_nodes(self) -> list[etree._Element]:
        return [span.el for span in self.spans if span.editable]


def _is_line_break(el: etree._Element) -> bool:
    if el.tag != f"{W}br":
        return False
    br_type = el.get(f"{W}type")
    return br_type in (None, "", "textWrapping")


def render_paragraph(para: etree._Element, *, include_deleted: bool = False) -> RenderedParagraph:
    """Render a paragraph to visible text and XML position spans.

    Editable spans are currently normal ``w:t`` nodes. Tabs and line breaks are
    rendered for accurate offsets but are not editable by span replacement.
    Deleted text is excluded by default because lexitool edits final-view text.
    """
    spans: list[TextSpan] = []
    parts: list[str] = []
    cursor = 0

    for el in para.iter():
        text = ""
        editable = False
        if el.tag == f"{W}t":
            text = el.text or ""
            editable = True
        elif include_deleted and el.tag == f"{W}delText":
            text = el.text or ""
        elif el.tag == f"{W}tab":
            # Exclude tab-stop definitions inside w:tabs (paragraph formatting)
            parent = el.getparent()
            if parent is not None and parent.tag == f"{W}tabs":
                continue
            text = "\t"
        elif _is_line_break(el):
            text = "\n"

        if not text:
            continue
        start = cursor
        cursor += len(text)
        spans.append(TextSpan(el=el, start=start, end=cursor, text=text, editable=editable))
        parts.append(text)

    return RenderedParagraph(paragraph=para, text="".join(parts), spans=spans)


def patch_space_attribute(t_el: etree._Element) -> None:
    """Keep Word whitespace semantics valid after changing a w:t node."""
    text = t_el.text or ""
    if text.startswith((" ", "\t", "\n")) or text.endswith((" ", "\t", "\n")):
        t_el.set(XML_SPACE, "preserve")
    else:
        t_el.attrib.pop(XML_SPACE, None)


def replace_span(rendered: RenderedParagraph, start: int, end: int, new_text: str) -> bool:
    """Replace a rendered text span without collapsing unrelated runs.

    Returns False when the span crosses non-editable content such as tabs or
    line breaks. Callers can then report a precise boundary error instead of
    silently corrupting document structure.
    """
    touched = [span for span in rendered.spans if span.end > start and span.start < end]
    if not touched or any(not span.editable for span in touched):
        return False

    first = touched[0]
    last = touched[-1]
    prefix = first.text[: max(0, start - first.start)]
    suffix = last.text[max(0, end - last.start):]

    if first.el is last.el:
        first.el.text = prefix + new_text + suffix
        patch_space_attribute(first.el)
        return True

    first.el.text = prefix + new_text
    patch_space_attribute(first.el)
    for span in touched[1:-1]:
        span.el.text = ""
        patch_space_attribute(span.el)
    last.el.text = suffix
    patch_space_attribute(last.el)
    return True


def editable_touched_spans(rendered: RenderedParagraph, start: int, end: int) -> list[TextSpan]:
    touched = [span for span in rendered.spans if span.end > start and span.start < end]
    if not touched or any(not span.editable for span in touched):
        return []
    return touched


def text_in_span(span: TextSpan, start: int, end: int) -> str:
    local_start = max(0, start - span.start)
    local_end = min(len(span.text), end - span.start)
    return span.text[local_start:local_end]
