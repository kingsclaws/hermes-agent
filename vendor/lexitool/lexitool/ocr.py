"""Compatibility shim for the runtime-installed lexitool OCR module.

The live container imports lexitool from /root/.hermes/tools/lexitool, but the
authoritative OCR implementation is maintained in the repo vendor copy:
/root/.hermes/hermes-agent/vendor/lexitool/lexitool/ocr.py

This shim loads that implementation directly so the runtime sees the same
local-MinerU-first behavior as the repo version without duplicating code.
"""

from __future__ import annotations

from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
import sys

_VENDOR_OCR = Path("/root/.hermes/hermes-agent/vendor/lexitool/lexitool/ocr.py")

if not _VENDOR_OCR.exists():
    raise ImportError(f"Vendored OCR implementation not found: {_VENDOR_OCR}")

_spec = spec_from_file_location("_lexitool_vendor_ocr", _VENDOR_OCR)
if _spec is None or _spec.loader is None:
    raise ImportError(f"Could not load vendored OCR implementation: {_VENDOR_OCR}")

_module = module_from_spec(_spec)
_spec.loader.exec_module(_module)

for _name, _value in _module.__dict__.items():
    if _name in {"__name__", "__loader__", "__package__", "__spec__"}:
        continue
    globals()[_name] = _value

__all__ = getattr(_module, "__all__", [name for name in globals() if not name.startswith("_")])
sys.modules.setdefault(__name__, sys.modules[__name__])

