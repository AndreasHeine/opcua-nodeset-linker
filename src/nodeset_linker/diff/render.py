"""Rendering of a semantic diff: console text, Markdown and HTML.

All three formats come from the same report structure, so a human-readable report, a
Markdown release note and an HTML artifact can never disagree with each other or with the
JSON.  ``Change.describe()`` writes the actual +/- line; renderers only add structure.
"""

from __future__ import annotations

import html
from dataclasses import dataclass
from typing import TypeVar

from nodeset_linker.diff.impact import total_impact
from nodeset_linker.diff.model import (
    BreakingChange,
    NamespaceChange,
    NodeEntry,
    NodeModification,
    RenumberedNode,
    SemanticDiff,
)
from nodeset_linker.diff.release import ReleaseVerdict, release_text
from nodeset_linker.diff.rules import RuleVerdict, Severity

#: A report entry, so the severity filter keeps its concrete type.
EntryT = TypeVar("EntryT", NodeEntry, NodeModification, RenumberedNode)

ARROW = "→"


@dataclass(frozen=True, slots=True)
class Entry:
    """One node (or namespace) with its detail lines."""

    header: str
    details: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class Section:
    """A titled block: summary lines and/or per-node entries."""

    title: str
    entries: tuple[Entry, ...] = ()
    plain: tuple[str, ...] = ()


def console_text(
    diff: SemanticDiff,
    *,
    impact: bool = False,
    breaking_only: bool = False,
    include_advisory: bool = True,
    release: ReleaseVerdict | None = None,
) -> str:
    """Plain-text report for a terminal."""
    sections = _sections(
        diff,
        impact=impact,
        breaking_only=breaking_only,
        include_advisory=include_advisory,
        release=release,
    )
    if not sections:
        return "No semantic differences found."
    lines: list[str] = []
    for section in sections:
        lines.append(section.title)
        lines.extend(section.plain)
        for entry in section.entries:
            lines.append(f"  {entry.header}")
            lines.extend(f"    {detail}" for detail in entry.details)
        lines.append("")
    return "\n".join(lines).rstrip("\n")


def markdown_text(
    diff: SemanticDiff,
    *,
    impact: bool = False,
    breaking_only: bool = False,
    include_advisory: bool = True,
    release: ReleaseVerdict | None = None,
) -> str:
    """Markdown report, usable as a release note or PR comment."""
    sections = _sections(
        diff,
        impact=impact,
        breaking_only=breaking_only,
        include_advisory=include_advisory,
        release=release,
    )
    if not sections:
        return "No semantic differences found."
    lines = ["# Semantic diff", ""]
    if diff.old_source or diff.new_source:
        lines.append(f"- Old: `{diff.old_source}`")
        lines.append(f"- New: `{diff.new_source}`")
        lines.append("")
    for section in sections:
        lines.append(f"## {section.title}")
        for line in section.plain:
            lines.append(f"- {line}")
        for entry in section.entries:
            lines.append(f"- **{entry.header}**")
            lines.extend(f"  - {detail}" for detail in entry.details)
        lines.append("")
    return "\n".join(lines).rstrip("\n")


def html_text(
    diff: SemanticDiff,
    *,
    impact: bool = False,
    breaking_only: bool = False,
    include_advisory: bool = True,
    release: ReleaseVerdict | None = None,
) -> str:
    """Self-contained HTML report."""
    sections = _sections(
        diff,
        impact=impact,
        breaking_only=breaking_only,
        include_advisory=include_advisory,
        release=release,
    )
    title = "Semantic diff"
    if diff.old_source:
        title += f" of {html.escape(diff.old_source)}"
    parts = [
        "<!DOCTYPE html>",
        '<html lang="en"><head><meta charset="utf-8">',
        f"<title>{title}</title>",
        "<style>body{font-family:system-ui,sans-serif;max-width:60rem;margin:2rem auto}"
        "h2{margin:1.5rem 0 .5rem}ul{margin:.25rem 0}li{margin:.15rem 0}"
        ".detail{color:#555;font-family:ui-monospace,monospace;font-size:.9em}"
        ".breaking{color:#b3261e}.advisory{color:#8a6d3b}</style></head><body>",
        "<h1>Semantic diff</h1>",
    ]
    if not sections:
        parts.append("<p>No semantic differences found.</p>")
    elif diff.old_source or diff.new_source:
        parts.append(
            f"<p>Old: <code>{html.escape(diff.old_source)}</code>"
            f" &nbsp; New: <code>{html.escape(diff.new_source)}</code></p>"
        )
    for section in sections:
        parts.append(f"<h2>{html.escape(section.title)}</h2>")
        if section.plain:
            parts.append("<ul>")
            parts.extend(f"<li>{html.escape(line)}</li>" for line in section.plain)
            parts.append("</ul>")
        if section.entries:
            strong = _strong_for(section.title)
            parts.append("<ul>")
            for entry in section.entries:
                parts.append(f"<li>{strong}>{html.escape(entry.header)}</strong>")
                if entry.details:
                    parts.append('<ul class="detail">')
                    parts.extend(f"<li>{html.escape(detail)}</li>" for detail in entry.details)
                    parts.append("</ul>")
                parts.append("</li>")
            parts.append("</ul>")
    parts.append("</body></html>")
    return "\n".join(parts)


def _sections(
    diff: SemanticDiff,
    *,
    impact: bool,
    breaking_only: bool,
    include_advisory: bool,
    release: ReleaseVerdict | None = None,
) -> list[Section]:
    """Build the report structure shared by every format.

    ``breaking_only`` keeps the entries whose own severity is breaking — an identifier move
    stays visible even though it is reported apart from the other node sections.
    ``include_advisory`` is off when a caller asked for ``--no-advisory``.
    ``release`` puts the paper's release question at the top of the report (§3.3, §11.3).
    """
    if diff.is_empty and not diff.namespaces:
        if release is None:
            return []
        return [Section(title="Release verdict", plain=_release_lines(release))]

    def kept(entries: list[EntryT]) -> list[EntryT]:
        return [
            entry
            for entry in entries
            if not breaking_only or entry.severity is Severity.BREAKING
        ]

    limit = diff.impact_limit
    sections = [
        Section(
            title="Summary",
            plain=_summary_lines(
                diff, impact=impact, include_advisory=include_advisory, release=release
            ),
        )
    ]
    if release is not None:
        sections.append(Section(title="Release verdict", plain=_release_lines(release)))

    if not breaking_only and diff.added:
        sections.append(
            Section(title="Added nodes", entries=tuple(_node_line(e, limit) for e in diff.added))
        )

    removed = kept(diff.removed)
    if removed:
        sections.append(
            Section(title="Removed nodes", entries=tuple(_node_line(e, limit) for e in removed))
        )

    modified = kept(diff.modified)
    if modified:
        sections.append(
            Section(
                title="Modified nodes",
                entries=tuple(_modification_line(e, limit) for e in modified),
            )
        )

    renumbered = kept(diff.renumbered)
    if renumbered:
        sections.append(
            Section(
                title="Renumbered nodes",
                entries=tuple(_renumbered_line(entry, limit) for entry in renumbered),
            )
        )

    namespace_entries = [
        entry for entry in diff.namespaces if not breaking_only or entry.breaking
    ]
    if namespace_entries:
        sections.append(
            Section(
                title="Namespace changes",
                entries=tuple(_namespace_line(entry) for entry in namespace_entries),
            )
        )

    if impact and not breaking_only:
        total = total_impact(diff)
        if total:
            sections.append(Section(title="Impact", plain=(_node_count(total),)))

    if diff.breaking:
        sections.append(
            Section(
                title="Breaking changes",
                entries=tuple(_breaking_line(entry) for entry in diff.breaking),
            )
        )
    elif breaking_only:
        sections.append(Section(title="Breaking changes", plain=("None",)))

    if diff.advisory and include_advisory and not breaking_only:
        sections.append(
            Section(
                title="Advisory",
                plain=("Best practices to review; they do not block a release.",),
                entries=tuple(_breaking_line(entry) for entry in diff.advisory),
            )
        )
    return sections


def _summary_lines(
    diff: SemanticDiff,
    *,
    impact: bool,
    include_advisory: bool,
    release: ReleaseVerdict | None = None,
) -> tuple[str, ...]:
    lines = [
        _count_line("Nodes", diff.old_nodes, diff.new_nodes),
        f"Added: {len(diff.added)}   Removed: {len(diff.removed)}   Modified: {len(diff.modified)}",
    ]
    if diff.renumbered:
        lines.append(f"Renumbered: {len(diff.renumbered)}")
    lines.append(f"Breaking changes: {len(diff.breaking) + len(diff.breaking_namespaces)}")
    advisory_namespaces = [
        entry for entry in diff.namespaces if entry.severity is Severity.ADVISORY
    ]
    if (diff.advisory or advisory_namespaces) and include_advisory:
        lines.append(f"Advisory: {len(diff.advisory) + len(advisory_namespaces)}")
    if release is not None:
        lines.append(f"Release: {release.recommendation}")
    if impact:
        lines.append(_node_count(total_impact(diff)))
    return tuple(lines)


def _release_lines(release: ReleaseVerdict) -> tuple[str, ...]:
    """The release answer and the namespaces behind it, as report lines."""
    return tuple(release_text(release).splitlines())


def _strong_for(section_title: str) -> str:
    """The emphasis a section deserves: red for breaking, amber for advisory."""
    if section_title == "Breaking changes":
        return '<strong class="breaking">'
    if section_title == "Advisory":
        return '<strong class="advisory">'
    return "<strong>"


def _count_line(label: str, old: int, new: int) -> str:
    difference = new - old
    suffix = f" ({difference:+d})" if difference else ""
    return f"{label}: {old} {ARROW} {new}{suffix}"


def _node_header(entry: NodeEntry | NodeModification | RenumberedNode) -> str:
    """Where the change lives: name, node class, namespace alias and identifier."""
    return (
        f"{entry.label} [{entry.node_class}, ns={entry.namespace}, "
        f"{entry.node_id.identifier.as_xml()}]"
    )


def _node_count(count: int, *, prefix: str = "Total Impact: ") -> str:
    """An impact count in one line, singular for one node."""
    return f"{prefix}{count} node" if count == 1 else f"{prefix}{count} nodes"


def _impact_details(
    impact: tuple[str, ...], *, count_only: bool = False, limit: int | None = None
) -> tuple[str, ...]:
    """Dependent nodes of a change; a long list is truncated, while the count stays exact."""
    if not impact:
        return ()
    if count_only:
        return (_node_count(len(impact), prefix="Impact: "),)
    shown = [f"Impact: {name}" for name in impact[:limit]]
    if limit is not None and len(impact) > limit:
        shown.append(f"... and {len(impact) - limit} more")
    return tuple(shown)


def _node_line(entry: NodeEntry, limit: int | None) -> Entry:
    return Entry(header=_node_header(entry), details=_impact_details(entry.impact, limit=limit))


def _modification_line(entry: NodeModification, limit: int | None) -> Entry:
    details = tuple(change.describe() for change in entry.changes)
    return Entry(
        header=_node_header(entry),
        details=details + _impact_details(entry.impact, count_only=True, limit=limit),
    )


def _renumbered_line(entry: RenumberedNode, limit: int | None) -> Entry:
    old = entry.old_node_id.identifier.as_xml()
    new = entry.new_node_id.identifier.as_xml()
    header = f"{entry.label} [{entry.node_class}, ns={entry.namespace}]: {old} {ARROW} {new}"
    details = tuple(change.describe() for change in entry.changes)
    if entry.confidence != "exact":
        details += (
            "Pairing inferred from namespace, BrowseName and node class; check it by hand.",
        )
    return Entry(
        header=header,
        details=details + _impact_details(entry.impact, count_only=True, limit=limit),
    )


def _namespace_line(entry: NamespaceChange) -> Entry:
    details = [
        _count_line("Nodes", *entry.nodes),
        _count_line("References", *entry.references),
        _count_line("DataTypes", *entry.datatypes),
    ]
    details.extend(delta.describe() for delta in entry.metadata)
    strongest = _strongest(entry.all_verdicts())
    if strongest is not None and strongest.severity is not Severity.INFO:
        details.append(f"{strongest.rule_id}: {strongest.rationale}")
    return Entry(header=f"{entry.alias} ({entry.uri})", details=tuple(details))


def _strongest(verdicts: list[RuleVerdict]) -> RuleVerdict | None:
    """The verdict a report cites: breaking first, then advisory, then information."""
    for severity in (Severity.BREAKING, Severity.ADVISORY, Severity.INFO):
        for candidate in verdicts:
            if candidate.severity is severity:
                return candidate
    return None


def _breaking_line(entry: BreakingChange) -> Entry:
    header = f"{entry.label} [{entry.node_id.identifier.as_xml()}]: {entry.reason}"
    details: list[str] = []
    if entry.rule_id is not None:
        details.append(f"{entry.rule_id}: {entry.rationale or ''}")
    if entry.affected is not None:
        details.append(_node_count(entry.affected, prefix="Affects "))
    return Entry(header=header, details=tuple(details))
