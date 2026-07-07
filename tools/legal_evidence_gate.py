"""Legal evidence gates shared by Kanban worker completion surfaces."""

from __future__ import annotations

import json
from typing import Any


MISSING_CONCLUSION_TERMS = (
    "未提供",
    "未回复",
    "未见",
    "未提交",
    "zip中未见",
    "rar中未见",
    "附件未见",
    "not provided",
    "not found",
    "missing",
)

EVIDENCE_LEDGER_TERMS = (
    "file evidence ledger",
    "文件证据台账",
    "证据覆盖表",
    "证据台账",
    "file -> question",
    "文件-问题",
    "文件名 | 工具",
)

VERIFICATION_STATUS_TERMS = (
    "已核实未提供",
    "未核实",
    "已提供但不完整",
    "verified_not_provided",
    "unverified",
    "provided_but_incomplete",
)


def render_completion_text(*parts: Any) -> str:
    """Render summary/result/metadata into one searchable validation string."""
    rendered: list[str] = []
    for part in parts:
        if part is None:
            continue
        if isinstance(part, str):
            rendered.append(part)
            continue
        try:
            rendered.append(json.dumps(part, ensure_ascii=False, sort_keys=True))
        except TypeError:
            rendered.append(str(part))
    return "\n\n".join(rendered)


def read_before_conclude_errors(text: str) -> list[str]:
    """Return hard-gate failures for filename-only legal evidence conclusions.

    Conservative by design: this only triggers when a worker makes a missing /
    not-found conclusion. In that case, the report must contain an evidence
    coverage ledger and the verified/unverified/provided-incomplete status
    taxonomy. This prevents "未提供" findings based on filenames or unread
    archives without blocking unrelated drafting tasks.
    """
    raw = text or ""
    lowered = raw.lower()
    has_missing_conclusion = any(
        term.lower() in lowered for term in MISSING_CONCLUSION_TERMS
    )
    if not has_missing_conclusion:
        return []

    errors: list[str] = []
    if not any(term.lower() in lowered for term in EVIDENCE_LEDGER_TERMS):
        errors.append(
            "缺少 File Evidence Ledger / 文件证据台账。作出“未提供/未见/缺失”结论前，必须列明每个文件的读取工具、实际内容摘要、对应问题编号和结论。"
        )
    if not any(term.lower() in lowered for term in VERIFICATION_STATUS_TERMS):
        errors.append(
            "缺口结论未使用三态标签：已核实未提供 / 未核实 / 已提供但不完整。不能把未读取、OCR失败、压缩包未解开表述为“未提供”。"
        )
    return errors
