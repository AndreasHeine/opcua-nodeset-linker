"""Small shared helpers."""

from __future__ import annotations

import fnmatch
from pathlib import Path


def matches_glob(pattern: str, value: str) -> bool:
    """Glob-style match used by root selectors and custom retention rules.

    ``*Diagnostics*`` matches anywhere in the value, not only at the anchors.
    """
    return fnmatch.fnmatchcase(value, pattern)


def read_text(path: Path | str) -> str:
    """Read a UTF-8 file (XML files may declare an encoding; lxml handles them)."""
    return Path(path).read_text(encoding="utf-8")
