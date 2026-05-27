from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class BackendChoice:
    name: str


LEGACY = BackendChoice("legacy")
OOXML = BackendChoice("ooxml")
SUPPORTED_BACKENDS = {LEGACY.name, OOXML.name}


def normalize_backend(name: str | None) -> str:
    if not name:
        return LEGACY.name
    value = name.strip().lower()
    if value not in SUPPORTED_BACKENDS:
        raise ValueError(f"unsupported backend: {name}")
    return value


def require_implemented(feature: str, backend: str) -> None:
    if backend == OOXML.name:
        raise NotImplementedError(
            f"OpenXML backend not implemented yet for {feature}; use --backend legacy for now"
        )
