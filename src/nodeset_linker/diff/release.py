"""The release question the paper actually asks: may a NamespaceUri be reused?

A semantic diff lists findings; a release manager needs one answer per namespace (§3.3, §11.3):
either the extension is compatible and the identifiers stay as they are, or the release has to
publish a new versioned URI and leave the published one untouched.  The answer needs to know what
was already published, so a baseline registry is optional input — without it the verdict says
"baseline unknown" instead of inventing a judgement (§11.3).
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from nodeset_linker.diff.model import (
    MetadataDelta,
    NamespaceChange,
    NodeModification,
    RenumberedNode,
    SemanticDiff,
)
from nodeset_linker.diff.rules import RuleVerdict, verdict

#: The major-version tail §11.3 puts in a NamespaceUri: ``http://…/<spec>/V2/``.
_VERSION_TAIL = re.compile(r"/V(\d+)/?$")


@dataclass(frozen=True, slots=True)
class Baseline:
    """NamespaceUris a previous release already published.

    Read from a split-export ``manifest.json`` or a hand-written registry listing
    ``{"uri", "version"}`` per namespace; the version is optional context, not a judgement.
    """

    path: str
    published: Mapping[str, str | None]


def load_baseline(path: Path | str) -> Baseline:
    """Read the published NamespaceUris from a bundle directory, manifest or registry file."""
    target = Path(path)
    if target.is_dir():
        target = target / "manifest.json"
    if not target.is_file():
        raise ValueError(f"baseline not found: {target}")
    try:
        payload = json.loads(target.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"baseline is not valid JSON: {target}") from exc
    entries = payload.get("namespaces", payload) if isinstance(payload, dict) else payload
    if not isinstance(entries, list):
        raise ValueError(f"baseline must list namespaces: {target}")
    published: dict[str, str | None] = {}
    for entry in entries:
        if isinstance(entry, str):
            published[entry] = None
        elif isinstance(entry, dict) and isinstance(entry.get("uri"), str):
            version = entry.get("version")
            published[str(entry["uri"])] = None if version is None else str(version)
    return Baseline(path=str(target), published=published)


@dataclass(frozen=True, slots=True)
class NamespaceVerdict:
    """Whether one NamespaceUri may carry the changes of this release."""

    uri: str
    #: ``False`` means the release must publish a new namespace instead (§11.3).
    reusable: bool
    #: Breaking findings the namespace carries, from its nodes and its release metadata.
    breaking: tuple[RuleVerdict, ...] = ()
    #: Release metadata that moved (§11.1, §11.2).
    metadata: tuple[MetadataDelta, ...] = ()
    #: Judgements of the release gate itself: the blocking verdict, or a version bump.
    verdicts: tuple[RuleVerdict, ...] = ()
    #: ``True`` when the URI carries the ``V<major>`` tail (§11.3).
    bumped: bool = False
    #: The URI this namespace moved away from, when the release renamed it (§11.3).
    moved_from: str | None = None
    #: ``None`` when no baseline states whether this URI was published.
    published: bool | None = None
    recommendation: str = ""

    def as_json(self) -> dict[str, object]:
        return {
            "uri": self.uri,
            "reusable": self.reusable,
            "bumped": self.bumped,
            "moved_from": self.moved_from,
            "published": self.published,
            "recommendation": self.recommendation,
            "breaking": [found.as_json() for found in self.breaking],
            "verdicts": [entry.as_json() for entry in self.verdicts],
            "metadata": [delta.as_json() for delta in self.metadata],
        }


@dataclass(frozen=True, slots=True)
class ReleaseVerdict:
    """The release answer: reusable namespaces, or "publish a new NamespaceUri"."""

    reusable: bool
    namespaces: tuple[NamespaceVerdict, ...]
    recommendation: str
    #: ``None`` when no baseline states what was published.
    baseline: str | None = None

    @property
    def blocked(self) -> tuple[NamespaceVerdict, ...]:
        """Namespaces that may not carry this release."""
        return tuple(entry for entry in self.namespaces if not entry.reusable)

    def as_json(self) -> dict[str, object]:
        return {
            "reusable": self.reusable,
            "recommendation": self.recommendation,
            "baseline": self.baseline if self.baseline is not None else "baseline unknown",
            "namespaces": [entry.as_json() for entry in self.namespaces],
        }


def release_verdict(diff: SemanticDiff, baseline: Baseline | None = None) -> ReleaseVerdict:
    """Judge every namespace this release touched against the published ones."""
    node_findings = _node_findings(diff)
    reported = {change.uri for change in diff.namespaces}
    entries = [
        _namespace_verdict(
            uri=change.uri,
            breaking=[
                *node_findings.get(change.uri, []),
                *[found for found in change.all_verdicts() if found.breaking],
            ],
            metadata=change.metadata,
            baseline=baseline,
            moved_from=_moved_from(change),
        )
        for change in diff.namespaces
    ]
    entries += [
        _namespace_verdict(
            uri=uri, breaking=node_findings[uri], metadata=(), baseline=baseline, moved_from=None
        )
        for uri in sorted(set(node_findings) - reported)
    ]
    blocked = tuple(entry for entry in entries if not entry.reusable)
    return ReleaseVerdict(
        reusable=not blocked,
        namespaces=tuple(entries),
        recommendation=_overall_recommendation(blocked, baseline),
        baseline=None if baseline is None else baseline.path,
    )


def _node_findings(diff: SemanticDiff) -> dict[str, list[RuleVerdict]]:
    """Breaking findings grouped by the NamespaceUri they live in (§11.3 judges per URI)."""
    findings: dict[str, list[RuleVerdict]] = {}
    for entry in [*diff.removed, *diff.added]:
        carried = [each for each in entry.verdicts if each.breaking]
        _add(findings, entry.namespace_uri, carried)
    for changed in _changed_entries(diff):
        carried = [each for each in changed.all_verdicts() if each.breaking]
        _add(findings, changed.namespace_uri, carried)
    return findings


def _changed_entries(diff: SemanticDiff) -> list[RenumberedNode | NodeModification]:
    """The nodes that were renumbered or modified: both kinds judge themselves per change."""
    return [*diff.renumbered, *diff.modified]


def _add(findings: dict[str, list[RuleVerdict]], uri: str, found: list[RuleVerdict]) -> None:
    if found:
        findings.setdefault(uri, []).extend(found)


def _moved_from(change: NamespaceChange) -> str | None:
    """The URI this namespace moved away from, when the release renamed it (§11.3)."""
    return next((delta.old for delta in change.metadata if delta.attribute == "uri"), None)


def _namespace_verdict(
    *,
    uri: str,
    breaking: list[RuleVerdict],
    metadata: tuple[MetadataDelta, ...],
    baseline: Baseline | None,
    moved_from: str | None,
) -> NamespaceVerdict:
    """Say what this namespace may carry, given what was published under it.

    A moved URI counts as published when the baseline knows either side of the move: the
    consumers were built on the old URI, so that is the publication that blocks the release.
    """
    bumped = _VERSION_TAIL.search(uri) is not None
    if baseline is None:
        published = None
    else:
        published = uri in baseline.published or (
            moved_from is not None and moved_from in baseline.published
        )
    reusable, advice, judgements = _judge(uri, breaking, published, bumped, moved_from)
    return NamespaceVerdict(
        uri=uri,
        reusable=reusable,
        breaking=tuple(breaking),
        metadata=metadata,
        verdicts=judgements,
        bumped=bumped,
        published=published,
        moved_from=moved_from,
        recommendation=advice,
    )


def _judge(
    uri: str,
    breaking: list[RuleVerdict],
    published: bool | None,
    bumped: bool,
    moved_from: str | None,
) -> tuple[bool, str, tuple[RuleVerdict, ...]]:
    """Reusable, the advice a release manager acts on, and the rule that judged it."""
    if not breaking:
        return True, _compatible_recommendation(published), ()
    if published is False:
        judgement = verdict("namespace_bump") if bumped else verdict("namespace_added")
        return (
            True,
            f"New NamespaceUri — the breaking changes belong here ({_count(breaking)}).",
            (judgement,),
        )
    if bumped and published is None:
        return (
            True,
            "Versioned NamespaceUri — breaking changes are allowed in a namespace nobody "
            "published yet; pass --baseline to confirm (§11.3).",
            (verdict("namespace_bump"),),
        )
    if published is None:
        # Nothing states whether this URI was published, so the verdict stays a fact without a
        # judgement: --fail-on-breaking is what gates the build here (§11.3).
        return (
            True,
            f"Breaking changes ({_count(breaking)}) on {_current_uri(uri)} — baseline unknown, "
            "so reuse cannot be confirmed; pass --baseline (§11.3).",
            (),
        )
    blocking = (verdict("breaking_in_released_namespace"),)
    if bumped:
        return (
            False,
            f"{uri} is already published with breaking changes — publish {_next_uri(uri)} "
            "instead (§11.3).",
            blocking,
        )
    if moved_from is not None:
        return (
            False,
            f"{_current_uri(moved_from)} is published; a moved NamespaceUri is not a new "
            "release — keep it and publish the changes under a versioned URI (§11.3).",
            blocking,
        )
    return (
        False,
        f"{_current_uri(uri)} is already published — publish {_next_uri(uri)} (§11.3).",
        blocking,
    )


def _compatible_recommendation(published: bool | None) -> str:
    if published is False:
        return "Reusable — a NamespaceUri nobody has published yet."
    if published is None:
        return "Reusable — compatible extension; baseline unknown."
    return "Reusable — compatible extension of a published NamespaceUri."


def _count(findings: list[RuleVerdict]) -> str:
    """How many breaking findings, in the words a report needs."""
    plural = "breaking change" if len(findings) == 1 else "breaking changes"
    return f"{len(findings)} {plural}"


def _current_uri(uri: str) -> str:
    """The URI a namespace carries in this release, shortened for a report line."""
    return uri.removeprefix("http://opcfoundation.org/UA/") or uri


def _next_uri(uri: str) -> str:
    """The versioned URI §11.3 asks for: the major version moves, the rest stays."""
    match = _VERSION_TAIL.search(uri)
    if match is None:
        return f"{uri.rstrip('/')}/V2/"
    major = int(match.group(1)) + 1
    return f"{uri[: match.start()]}/V{major}/"


def _overall_recommendation(
    blocked: tuple[NamespaceVerdict, ...], baseline: Baseline | None
) -> str:
    """One line a CI gate can print: reusable, or which namespace has to move."""
    if not blocked:
        if baseline is None:
            return "Reusable — baseline unknown, so reuse is reported per namespace."
        return "Reusable — no breaking finding lands on a published NamespaceUri."
    first = blocked[0]
    # The versioned URI is derived from the URI consumers actually resolve, not from the one
    # this release moved to (§11.3).
    origin = first.moved_from or first.uri
    names = ", ".join(entry.uri for entry in blocked[:3])
    if len(blocked) > 3:
        names += f", … ({len(blocked)} namespaces)"
    return f"Not reusable — publish {_next_uri(origin)} for {names} (§11.3)."


def release_text(release: ReleaseVerdict) -> str:
    """The release verdict as report lines: the answer first, then the namespaces behind it."""
    lines = [release.recommendation]
    if release.baseline is None:
        lines.append("No baseline given: reuse of a NamespaceUri cannot be confirmed (§11.3).")
    for entry in release.namespaces:
        if entry.reusable and not entry.breaking:
            continue
        blocking = next((found for found in entry.verdicts if found.breaking), None)
        detail = entry.recommendation
        if blocking is not None:
            detail = f"{blocking.rule_id}: {blocking.rationale}"
        lines.append(f"{entry.uri}: {detail}")
    return "\n".join(lines)
