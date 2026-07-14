"""TC (Track Change) utility functions for lexitool.

Provides normalization functions for text comparison in TC operations.
"""

from __future__ import annotations

import unicodedata
from datetime import datetime, timezone


def _utc_now() -> str:
    """Return the current UTC time as an ISO 8601 string."""
    return datetime.now(timezone.utc).isoformat()


def _normalize_fullwidth(text: str) -> str:
    """Normalize full-width characters to their ASCII equivalents.

    Maps:
    - Full-width digits (０-９) → ASCII digits (0-9)
    - Full-width letters (Ａ-Ｚ, ａ-ｚ) → ASCII letters (A-Z, a-z)
    - Full-width punctuation (，、。；：etc.) → ASCII equivalents
    - Em-dash (—) → single hyphen (-)
    - En-dash (–) → single hyphen (-)

    This ensures 1:1 codepoint mapping for index preservation in
    _locate_replacement_span, which slices the original full_text
    using normalized indices.
    """
    result = []
    for ch in text:
        code = ord(ch)
        # Full-width digits: ０(0xFF10) to ９(0xFF19)
        if 0xFF10 <= code <= 0xFF19:
            result.append(chr(code - 0xFF10 + 0x30))
        # Full-width uppercase: Ａ(0xFF21) to Ｚ(0xFF3A)
        elif 0xFF21 <= code <= 0xFF3A:
            result.append(chr(code - 0xFF21 + 0x41))
        # Full-width lowercase: ａ(0xFF41) to ｚ(0xFF5A)
        elif 0xFF41 <= code <= 0xFF5A:
            result.append(chr(code - 0xFF41 + 0x61))
        # Full-width comma: ，(0xFF0C)
        elif code == 0xFF0C:
            result.append(',')
        # Full-width period: 。(0x3002)
        elif code == 0x3002:
            result.append('.')
        # Full-width semicolon: ；(0xFF1B)
        elif code == 0xFF1B:
            result.append(';')
        # Full-width colon: ：(0xFF1A)
        elif code == 0xFF1A:
            result.append(':')
        # Full-width exclamation: ！(0xFF01)
        elif code == 0xFF01:
            result.append('!')
        # Full-width question: ？(0xFF1F)
        elif code == 0xFF1F:
            result.append('?')
        # Full-width left paren: （(0xFF08)
        elif code == 0xFF08:
            result.append('(')
        # Full-width right paren: ）(0xFF09)
        elif code == 0xFF09:
            result.append(')')
        # Full-width left bracket: ［(0xFF3B)
        elif code == 0xFF3B:
            result.append('[')
        # Full-width right bracket: ］(0xFF3D)
        elif code == 0xFF3D:
            result.append(']')
        # Em-dash: —(0x2014) or En-dash: –(0x2013) → single hyphen
        elif code in (0x2014, 0x2013):
            result.append('-')
        # Full-width hyphen-minus: －(0xFF0D)
        elif code == 0xFF0D:
            result.append('-')
        else:
            result.append(ch)
    return ''.join(result)
