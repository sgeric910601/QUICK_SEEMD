"""QUICK_SEEMD Linux 版。"""

from __future__ import annotations

from pathlib import Path


def _read_version() -> str:
    here = Path(__file__).resolve().parent
    for candidate in (here / "VERSION", here.parent.parent / "VERSION"):
        try:
            return candidate.read_text("utf-8").strip()
        except OSError:
            continue
    return "0.0.0"


__version__ = _read_version()
