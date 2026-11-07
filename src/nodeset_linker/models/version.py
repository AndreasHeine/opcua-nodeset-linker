"""Version strings of a namespace release (OPC 10000-6 Annex F.2, OPC 10000-5 §12.41).

`Version` is a *display* string: Annex F.2 states that it is "not intended for programmatic
comparisons", and OPC 10000-5 §6.3.13 calls ``NamespaceVersion`` "intended for display purposes".
`ModelVersion` is the comparable one — a ``SemanticVersionString`` whose precedence follows the
SemVer 2.0.0 rules (F.2 and §6.3.13 both define the same comparison: compare the ``ModelVersion``
of both sides, fall back to the publication date when they are equal).

Published companion specs therefore state one release twice, spelled differently:
``Version="1.05.03"`` next to ``ModelVersion="1.5.3"``.  Comparing those as text makes every
namespace look inconsistent, so equality is judged on the numeric core and only a genuine numeric
disagreement is reported.  A value that is not numeric-dot form at all stays compared as the
string it is written as.
"""

from __future__ import annotations

import re

#: The numeric core of a version: major, minor, patch, plus the SemVer pre-release tail.
VersionCore = tuple[int, int, int, str]

#: Identifier characters allowed in a version tail, and the digits-only form of one identifier.
_IDENTIFIER = re.compile("[0-9A-Za-z-]+")
_DIGITS = re.compile("[0-9]+")


def _text(value: str | None) -> str | None:
    """The value as written, trimmed; an empty string counts as *not stated*."""
    trimmed = value.strip() if value else ""
    return trimmed or None


def _tail_ok(tail: str, *, allow_leading_zeros: bool) -> bool:
    """SemVer identifier list: every segment non-empty, digits-only where numeric.

    Pre-release identifiers must not carry leading zeros (§9 of the SemVer specification);
    build metadata identifiers may, because the BNF of OPC 10000-5 Table 303 allows ``<digits>``.
    """
    identifiers = tail.split(".")
    if any(not _IDENTIFIER.fullmatch(identifier) for identifier in identifiers):
        return False
    if allow_leading_zeros:
        return True
    return not any(
        _DIGITS.fullmatch(identifier) and len(identifier) > 1 and identifier.startswith("0")
        for identifier in identifiers
    )


def _core_and_prerelease(body: str) -> tuple[str, str]:
    """Split a version core from its pre-release tail at the first ``-``."""
    core, _, prerelease = body.partition("-")
    return core, prerelease


def _parse(value: str | None, *, strict: bool) -> VersionCore | None:
    """Read the numeric core, or ``None`` when the value is not numeric-dot form.

    ``strict`` demands the ``SemanticVersionString`` spelling: exactly three segments without
    leading zeros (OPC 10000-5 §12.41, Table 303).  A tolerant read folds the display spelling in
    — ``1.04.0`` and ``1.4`` both mean ``1.4.0`` — because a display ``Version`` is allowed to
    pad its segments.
    """
    text = _text(value)
    if text is None:
        return None
    if text.endswith("+"):
        return None
    body, _, build = text.partition("+")
    if build and not _tail_ok(build, allow_leading_zeros=True):
        return None
    if body.endswith("-"):
        return None
    core, prerelease = _core_and_prerelease(body)
    if prerelease and not _tail_ok(prerelease, allow_leading_zeros=not strict):
        return None
    segments = core.split(".")
    if len(segments) > 3 or any(not _DIGITS.fullmatch(segment) for segment in segments):
        return None
    if strict and len(segments) != 3:
        return None
    if strict and any(segment != str(int(segment)) for segment in segments):
        return None
    segments += ["0"] * (3 - len(segments))
    major, minor, patch = (int(segment) for segment in segments)
    return (major, minor, patch, prerelease)


def normalized(value: str | None) -> VersionCore | None:
    """The comparable reading of a version: ``(major, minor, patch, prerelease)``.

    Leading zeros and missing segments are folded in, so the display spelling ``1.04.0`` and the
    SemVer spelling ``1.4.0`` normalize to the same core.  Build metadata is ignored, exactly as
    SemVer precedence ignores it.
    """
    return _parse(value, strict=False)


def is_semantic(value: str | None) -> bool:
    """True when the value is a valid ``SemanticVersionString`` (OPC 10000-5 §12.41)."""
    return _parse(value, strict=True) is not None


def canonical(value: str | None) -> str | None:
    """The spelling a comparison sees: ``1.04.0`` reads as ``1.4.0``, anything else stays as is."""
    core = normalized(value)
    if core is None:
        return _text(value)
    major, minor, patch, prerelease = core
    return f"{major}.{minor}.{patch}-{prerelease}" if prerelease else f"{major}.{minor}.{patch}"


def versions_equal(left: str | None, right: str | None) -> bool:
    """Whether two version claims describe the same release.

    Two values that both read as numeric-dot form are compared on their core; anything else is
    compared as the string it is, so a vendor spelling nobody can parse is never silently equal.
    """
    left_core, right_core = normalized(left), normalized(right)
    if left_core is not None and right_core is not None:
        return left_core == right_core
    return _text(left) == _text(right)


def _prerelease_precedes(left: str, right: str) -> bool:
    """SemVer §11: a pre-release is older than the released version of the same core."""
    if left == right:
        return False
    if not left:
        return False
    if not right:
        return True
    mine = left.split(".")
    theirs = right.split(".")
    for mine_part, their_part in zip(mine, theirs, strict=False):
        if _DIGITS.fullmatch(mine_part) and _DIGITS.fullmatch(their_part):
            if int(mine_part) != int(their_part):
                return int(mine_part) < int(their_part)
            continue
        if _DIGITS.fullmatch(mine_part):
            return True
        if _DIGITS.fullmatch(their_part):
            return False
        if mine_part != their_part:
            return mine_part < their_part
    return len(mine) < len(theirs)


def version_precedes(left: str | None, right: str | None) -> bool | None:
    """SemVer precedence of two claims; ``None`` when either side is not comparable."""
    left_core, right_core = normalized(left), normalized(right)
    if left_core is None or right_core is None:
        return None
    if left_core[:3] != right_core[:3]:
        return left_core[:3] < right_core[:3]
    return _prerelease_precedes(left_core[3], right_core[3])
