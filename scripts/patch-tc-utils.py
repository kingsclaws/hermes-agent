#!/usr/bin/env python3
"""Append _normalize_fullwidth to tc_utils.py if missing."""
import pathlib, sys

FUNC = '''

def _normalize_fullwidth(text: str) -> str:
    result = []
    for ch in text:
        code = ord(ch)
        if 0xFF10 <= code <= 0xFF19: result.append(chr(code - 0xFF10 + 0x30))
        elif 0xFF21 <= code <= 0xFF3A: result.append(chr(code - 0xFF21 + 0x41))
        elif 0xFF41 <= code <= 0xFF5A: result.append(chr(code - 0xFF41 + 0x61))
        elif code == 0xFF0C: result.append(',')
        elif code == 0x3002: result.append('.')
        elif code in (0x2014, 0x2013): result.append('-')
        elif code == 0xFF0D: result.append('-')
        else: result.append(ch)
    return ''.join(result)
'''

targets = [
    pathlib.Path("/opt/lexitool/lexitool/tc_utils.py"),
    pathlib.Path("/opt/hermes/vendor/lexitool/lexitool/tc_utils.py"),
]
try:
    import lexitool
    targets.append(pathlib.Path(lexitool.__file__).parent / "tc_utils.py")
except Exception:
    pass

for p in targets:
    if p.exists():
        content = p.read_text()
        if "_normalize_fullwidth" not in content:
            p.write_text(content + FUNC)
            print(f"Patched {p}")
        else:
            print(f"Already has _normalize_fullwidth: {p}")
