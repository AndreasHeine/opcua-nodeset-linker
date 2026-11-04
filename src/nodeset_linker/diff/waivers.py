"""Waivers: findings a team decided to accept without deleting them (WP 15).

A waiver never removes a finding.  The report still shows it, marked ``waived``, so a
release note keeps the fact and the reason next to it, and a CI dashboard can group by
``waived_by``.  Only ``ADVISORY`` and ``INFO`` findings can be waived; a ``BREAKING``
finding is acknowledged by ``--accept-breaking``, which marks every breaking finding and
prints the acknowledgement in the report header, so a reviewer sees that the gate was
opened on purpose.

The waiver file is a JSON array of objects: ``{"rule": "...", "namespace": "...",
"node": "...", "reason": "..."}``.  ``rule`` is required; ``namespace`` and ``node`` narrow
a waiver to one namespace or one node, so a waiver cannot silently cover the whole model.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

from nodeset_linker.diff.model import (
    BreakingChange,
    Change,
    MetadataDelta,
    NamespaceChange,
    NodeEntry,
    NodeModification,
    RenumberedNode,
    SemanticDiff,
)
from nodeset_linker.diff.rules import RuleVerdict, Severity


class WaiverFileError(ValueError):
    """A waiver file that cannot be read or does not hold the documented shape."""


@dataclass(frozen=True, slots=True)
class Waiver:
    """One accepted finding: a rule id, optionally narrowed to a namespace and a node."""

    rule: str
    namespace: str | None = None
    node: str | None = None
    reason: str = ""

    @staticmethod
    def from_json(raw: Any, *, where: str) -> Waiver:
        """Parse one waiver entry, naming the file and index in every error."""
        if not isinstance(raw, dict):
            raise WaiverFileError(f"{where}: expected an object, got {type(raw).__name__}")
        rule = raw.get("rule")
        if not isinstance(rule, str) or not rule.strip():
            raise WaiverFileError(f"{where}: 'rule' is required and must be a non-empty string")
        unknown = sorted(set(raw) - {"rule", "namespace", "node", "reason"})
        if unknown:
            raise WaiverFileError(f"{where}: unknown keys {', '.join(unknown)}")
        namespace = raw.get("namespace")
        node = raw.get("node")
        reason = raw.get("reason", "")
        for key, value in (("namespace", namespace), ("node", node), ("reason", reason)):
            if value is not None and not isinstance(value, str):
                raise WaiverFileError(f"{where}: '{key}' must be a string or null")
        return Waiver(
            rule=rule.strip(),
            namespace=None if namespace is None else namespace.strip(),
            node=None if node is None else node.strip(),
            reason=str(reason or ""),
        )

    def matches_rule(self, rule_id: str | None) -> bool:
        return rule_id is not None and rule_id.casefold() == self.rule.casefold()

    def matches_namespace(self, uri: str, alias: str) -> bool:
        if self.namespace is None:
            return True
        wanted = self.namespace.casefold()
        return wanted in {uri.casefold(), alias.casefold()}

    def matches_node(self, label: str, node_xml: str, identifier_xml: str) -> bool:
        if self.node is None:
            return True
        wanted = self.node.casefold()
        return wanted in {label.casefold(), node_xml.casefold(), identifier_xml.casefold()}


@dataclass(frozen=True, slots=True)
class WaiverSet:
    """The waivers a run applies, from ``--waive`` and ``--waiver-file``."""

    entries: tuple[Waiver, ...] = ()

    @staticmethod
    def from_cli(
        rule_ids: list[str] | tuple[str, ...], path: Path | None
    ) -> WaiverSet:
        """Merge the command-line waivers with the ones in a file."""
        entries = [Waiver(rule=rule_id, reason=f"--waive {rule_id}") for rule_id in rule_ids]
        if path is not None:
            entries.extend(load_waivers(path))
        return WaiverSet(entries=tuple(entries))

    def find(
        self,
        rule_id: str | None,
        *,
        uri: str = "",
        alias: str = "",
        label: str = "",
        node_xml: str = "",
        identifier_xml: str = "",
    ) -> Waiver | None:
        """The first waiver that covers this finding, or ``None``.

        A waiver without a ``node`` still has to match the namespace; a waiver with a
        ``node`` matches the BrowseName label, the full identifier or the local part.
        """
        for entry in self.entries:
            if not entry.matches_rule(rule_id):
                continue
            if not entry.matches_namespace(uri, alias):
                continue
            if entry.node is not None and not entry.matches_node(
                label, node_xml, identifier_xml
            ):
                continue
            return entry
        return None

    def rules(self) -> tuple[str, ...]:
        """The rule ids this set names, in the order they were given."""
        return tuple(entry.rule for entry in self.entries)


def load_waivers(path: Path) -> list[Waiver]:
    """Read a waiver file: a JSON array of ``{rule, namespace?, node?, reason}`` objects."""
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise WaiverFileError(f"cannot read waiver file {path}: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise WaiverFileError(f"waiver file {path} is not valid JSON: {exc}") from exc
    if isinstance(raw, dict) and isinstance(raw.get("waivers"), list):
        raw = raw["waivers"]
    if not isinstance(raw, list):
        raise WaiverFileError(
            f"waiver file {path} must hold a JSON array of objects, or an object with a "
            f"'waivers' array"
        )
    return [Waiver.from_json(entry, where=f"{path}[{index}]") for index, entry in enumerate(raw)]


@dataclass(frozen=True, slots=True)
class WaiverOutcome:
    """What the waiver pass did, so the report can say it out loud."""

    waived: int = 0
    accepted_breaking: bool = False
    ignored: tuple[str, ...] = ()

    def header_lines(self) -> tuple[str, ...]:
        """Report-header lines: the acknowledgement and any waiver that was not honoured."""
        lines: list[str] = []
        if self.accepted_breaking:
            lines.append(
                "BREAKING changes acknowledged with --accept-breaking: the gate does not fail, "
                "but every breaking finding stays in this report."
            )
        if self.waived:
            lines.append(f"{self.waived} finding(s) waived; they stay visible, marked 'waived'.")
        lines.extend(self.ignored)
        return tuple(lines)

    def as_json(self) -> dict[str, Any]:
        """The ``waivers`` key of the JSON report: what was accepted, and what was refused."""
        return {
            "accepted_breaking": self.accepted_breaking,
            "waived_changes": self.waived,
            "ignored": list(self.ignored),
        }


@dataclass(slots=True)
class _Runner:
    """Applies one waiver set to a diff, collecting what it did."""

    waivers: WaiverSet
    accept_breaking: bool = False
    waived: int = 0
    ignored: list[str] = field(default_factory=list)
    seen_ignored: set[str] = field(default_factory=set)

    def decide(
        self,
        verdict: RuleVerdict,
        *,
        uri: str = "",
        alias: str = "",
        label: str = "",
        node_xml: str = "",
        identifier_xml: str = "",
        count: bool = True,
    ) -> RuleVerdict:
        """Mark one verdict, or record why its waiver was refused.

        ``count=False`` marks a finding that mirrors another one (the flat breaking list
        repeats an entry-level verdict), so the summary counts a finding once.
        """
        waiver = self.waivers.find(
            verdict.rule_id,
            uri=uri,
            alias=alias,
            label=label,
            node_xml=node_xml,
            identifier_xml=identifier_xml,
        )
        if verdict.severity is Severity.BREAKING:
            if self.accept_breaking:
                if count:
                    self.waived += 1
                return replace(verdict, waived=True, waived_by="--accept-breaking")
            if waiver is None:
                return verdict
            message = (
                f"waiver ignored: {waiver.rule} is breaking and cannot be waived; "
                f"use --accept-breaking to acknowledge it."
            )
            if message not in self.seen_ignored:
                self.seen_ignored.add(message)
                self.ignored.append(message)
            return verdict
        if waiver is None:
            return verdict
        if count:
            self.waived += 1
        return replace(
            verdict, waived=True, waived_by=waiver.reason or f"waiver rule={waiver.rule}"
        )

    def changes(
        self,
        changes: list[Change],
        *,
        uri: str = "",
        alias: str = "",
        label: str,
        node_xml: str,
        identifier_xml: str,
    ) -> list[Change]:
        """Rebuild the per-change verdicts with the waiver markers."""
        rebuilt: list[Change] = []
        for change in changes:
            if change.verdict is None:
                rebuilt.append(change)
                continue
            rebuilt.append(
                replace(
                    change,
                    verdict=self.decide(
                        change.verdict,
                        uri=uri,
                        alias=alias,
                        label=label,
                        node_xml=node_xml,
                        identifier_xml=identifier_xml,
                    ),
                )
            )
        return rebuilt

    def entries(self, diff: SemanticDiff) -> None:
        """Mark the node-level findings of every entry the diff produced."""
        node_entries: list[NodeEntry | NodeModification | RenumberedNode] = [
            *diff.added,
            *diff.removed,
            *diff.modified,
            *diff.renumbered,
        ]
        for entry in node_entries:
            node_xml = entry.node_id.as_xml()
            identifier_xml = entry.node_id.identifier.as_xml()
            scoped = {
                "uri": entry.namespace_uri,
                "alias": entry.namespace,
                "label": entry.label,
                "node_xml": node_xml,
                "identifier_xml": identifier_xml,
            }
            entry.verdicts = tuple(
                self.decide(
                    verdict,
                    count=not isinstance(entry, NodeModification | RenumberedNode),
                    **scoped,
                )
                for verdict in entry.verdicts
            )
            if isinstance(entry, NodeEntry):
                continue
            entry.changes = self.changes(entry.changes, **scoped)

    def flat(self, items: list[BreakingChange]) -> list[BreakingChange]:
        """Mark the flat breaking/advisory list, which mirrors an entry-level verdict."""
        rebuilt: list[BreakingChange] = []
        for item in items:
            if item.rule_id is None:
                rebuilt.append(item)
                continue
            mirrored = RuleVerdict(
                severity=item.severity,
                rationale=item.rationale or "",
                rule_id=item.rule_id,
                clause=item.clause,
            )
            marked = self.decide(
                mirrored,
                uri=item.node_id.namespace,
                label=item.label,
                node_xml=item.node_id.as_xml(),
                identifier_xml=item.node_id.identifier.as_xml(),
                count=False,
            )
            rebuilt.append(replace(item, waived=marked.waived, waived_by=marked.waived_by))
        return rebuilt

    def namespaces(self, entries: list[NamespaceChange]) -> list[NamespaceChange]:
        """Mark namespace metadata and namespace-level verdicts by URI and alias."""
        rebuilt: list[NamespaceChange] = []
        for entry in entries:
            metadata: list[MetadataDelta] = []
            for delta in entry.metadata:
                marked = self.decide(delta.verdict, uri=entry.uri, alias=entry.alias)
                metadata.append(
                    replace(
                        delta,
                        verdict=marked,
                        waived=marked.waived,
                        waived_by=marked.waived_by,
                    )
                )
            verdicts = tuple(
                self.decide(verdict, uri=entry.uri, alias=entry.alias)
                for verdict in entry.verdicts
            )
            rebuilt.append(replace(entry, metadata=tuple(metadata), verdicts=verdicts))
        return rebuilt


def apply_waivers(
    diff: SemanticDiff, waivers: WaiverSet, *, accept_breaking: bool = False
) -> WaiverOutcome:
    """Mark the findings a waiver covers, and report the waivers that could not apply.

    Severities are never re-derived here: a waived finding keeps its severity and rule id,
    so a report still says *breaking* next to *waived*, and only the release gate consults
    the markers.
    """
    runner = _Runner(waivers=waivers, accept_breaking=accept_breaking)
    runner.entries(diff)
    diff.breaking = runner.flat(diff.breaking)
    diff.advisory = runner.flat(diff.advisory)
    diff.namespaces = runner.namespaces(diff.namespaces)
    return WaiverOutcome(
        waived=runner.waived,
        accepted_breaking=accept_breaking,
        ignored=tuple(runner.ignored),
    )
