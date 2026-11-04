"""Result objects of a semantic diff — the shape humans and CI both read.

Every entry names a *semantic* entity (a node, a definition member, a reference), never
an XML location.  ``Change.describe()`` is the single place where the +/- lines users
read are produced, so console, Markdown and HTML stay consistent with the JSON.
"""

from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass, field
from typing import Any

from nodeset_linker.diff.rules import PAPER, RuleVerdict, Severity
from nodeset_linker.models.identifiers import ExpandedNodeId

#: Machine kind of a change → the label used in human-readable output.
CHANGE_LABELS: dict[str, str] = {
    "node_class": "NodeClass",
    "browse_name": "BrowseName",
    "display_name": "DisplayName",
    "description": "Description",
    "symbolic_name": "SymbolicName",
    "inverse_name": "InverseName",
    "categories": "Categories",
    "attributes": "Attributes",
    "documentation": "Documentation",
    "modelling_rule": "Modelling rule",
    "is_abstract": "IsAbstract",
    "data_type": "DataType",
    "value_rank": "ValueRank",
    "array_dimensions": "ArrayDimensions",
    "value": "Value",
    "definition_kind": "Definition kind",
    "field_order": "Field order",
    "alternative_order": "Alternative order",
}


def _spell(value: str | None) -> str:
    """Render a missing attribute as an explicit marker instead of an empty string."""
    return value if value is not None else "(none)"


@dataclass(frozen=True, slots=True)
class Change:
    """One semantic difference of one node.

    ``area`` groups it (node attribute, definition member, reference); ``kind`` says what
    happened (``attribute``, ``added``, ``removed``, ``changed``) and ``verdict`` carries the
    severity, rule id and rationale of the best-practices rule, so a report never re-derives
    it.  ``breaking`` is the verdict's severity read as a boolean.
    """

    area: str
    kind: str
    subject: str | None = None
    old: str | None = None
    new: str | None = None
    verdict: RuleVerdict | None = None

    @property
    def severity(self) -> Severity:
        return self.verdict.severity if self.verdict is not None else Severity.INFO

    @property
    def rule_id(self) -> str | None:
        return self.verdict.rule_id if self.verdict is not None else None

    @property
    def breaking(self) -> bool:
        return self.severity is Severity.BREAKING

    def describe(self) -> str:
        if self.kind == "added":
            return f"+ {self.subject}{f' : {self.new}' if self.new else ''}"
        if self.kind == "removed":
            return f"- {self.subject}{f' : {self.old}' if self.old else ''}"
        if self.kind == "changed":
            return f"{self.subject}: {_spell(self.old)} → {_spell(self.new)}"
        label = CHANGE_LABELS.get(self.kind, self.kind)
        if self.old is None:
            return f"{label}: + {_spell(self.new)}"
        if self.new is None:
            return f"{label}: - {_spell(self.old)}"
        return f"{label}: {_spell(self.old)} → {_spell(self.new)}"

    def as_json(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "area": self.area,
            "kind": self.kind,
            "subject": self.subject,
            "old": self.old,
            "new": self.new,
            "breaking": self.breaking,
            "text": self.describe(),
        }
        if self.verdict is not None:
            payload["severity"] = self.verdict.severity.value
            payload["rule_id"] = self.verdict.rule_id
            payload["rationale"] = self.verdict.rationale
            payload["clause"] = self.verdict.clause
        return payload


@dataclass(slots=True)
class NodeEntry:
    """A node that appeared (``added``) or disappeared (``removed``)."""

    label: str
    node_id: ExpandedNodeId
    node_class: str
    namespace: str
    verdicts: tuple[RuleVerdict, ...] = ()
    impact: tuple[str, ...] = ()

    @property
    def breaking(self) -> bool:
        return any(verdict.breaking for verdict in self.verdicts)

    @property
    def severity(self) -> Severity:
        """The strongest severity this entry carries."""
        order = (Severity.BREAKING, Severity.ADVISORY, Severity.INFO)
        severities = {verdict.severity for verdict in self.verdicts}
        return next((severity for severity in order if severity in severities), Severity.INFO)

    @property
    def rule_id(self) -> str | None:
        """The rule id of the strongest verdict this entry carries."""
        for severity in (Severity.BREAKING, Severity.ADVISORY, Severity.INFO):
            for entry_verdict in self.verdicts:
                if entry_verdict.severity is severity:
                    return entry_verdict.rule_id
        return None

    def as_json(self) -> dict[str, Any]:
        return {
            "label": self.label,
            "node_id": self.node_id.as_xml(),
            "namespace": self.namespace,
            "node_class": self.node_class,
            "breaking": self.breaking,
            "severity": self.severity.value,
            "verdicts": [verdict.as_json() for verdict in self.verdicts],
            "impacted_nodes": list(self.impact),
            "impact_count": len(self.impact),
        }


@dataclass(slots=True)
class NodeModification:
    """A node present in both models whose semantics differ."""

    label: str
    node_id: ExpandedNodeId
    node_class: str
    namespace: str
    changes: list[Change] = field(default_factory=list)
    verdicts: tuple[RuleVerdict, ...] = ()
    impact: tuple[str, ...] = ()

    @property
    def breaking(self) -> bool:
        return any(change.breaking for change in self.changes) or any(
            verdict.breaking for verdict in self.verdicts
        )

    @property
    def severity(self) -> Severity:
        """The strongest severity this entry carries."""
        severities = {change.severity for change in self.changes} | {
            verdict.severity for verdict in self.verdicts
        }
        for severity in (Severity.BREAKING, Severity.ADVISORY):
            if severity in severities:
                return severity
        return Severity.INFO

    def breaking_changes(self) -> list[Change]:
        return [change for change in self.changes if change.breaking]

    @property
    def rule_id(self) -> str | None:
        """The rule id of the strongest verdict that judged this entry."""
        for severity in (Severity.BREAKING, Severity.ADVISORY, Severity.INFO):
            for entry_verdict in self.all_verdicts():
                if entry_verdict.severity is severity:
                    return entry_verdict.rule_id
        return None

    def all_verdicts(self) -> list[RuleVerdict]:
        """Every rule that judged this entry, entry-level verdicts first."""
        return [*self.verdicts, *[change.verdict for change in self.changes if change.verdict]]

    def as_json(self) -> dict[str, Any]:
        return {
            "label": self.label,
            "node_id": self.node_id.as_xml(),
            "namespace": self.namespace,
            "node_class": self.node_class,
            "breaking": self.breaking,
            "severity": self.severity.value,
            "verdicts": [verdict.as_json() for verdict in self.all_verdicts()],
            "changes": [change.as_json() for change in self.changes],
            "impacted_nodes": list(self.impact),
            "impact_count": len(self.impact),
        }


@dataclass(slots=True)
class RenumberedNode:
    """Same namespace, same BrowseName, different identifier.

    This is an identity move, not a modelling change: it is reported separately so a
    renumbered nodeset does not look like a big addition plus a big removal.
    """

    label: str
    old_node_id: ExpandedNodeId
    new_node_id: ExpandedNodeId
    node_class: str
    namespace: str
    changes: list[Change] = field(default_factory=list)
    verdicts: tuple[RuleVerdict, ...] = ()
    impact: tuple[str, ...] = ()

    @property
    def node_id(self) -> ExpandedNodeId:
        """The identifier the node has now — a renumbering reports the new identity."""
        return self.new_node_id

    @property
    def breaking(self) -> bool:
        return any(change.breaking for change in self.changes) or any(
            verdict.breaking for verdict in self.verdicts
        )

    @property
    def severity(self) -> Severity:
        """The strongest severity this entry carries."""
        severities = {change.severity for change in self.changes} | {
            verdict.severity for verdict in self.verdicts
        }
        for severity in (Severity.BREAKING, Severity.ADVISORY):
            if severity in severities:
                return severity
        return Severity.INFO

    @property
    def rule_id(self) -> str | None:
        """The rule id of the strongest verdict that judged this entry."""
        for severity in (Severity.BREAKING, Severity.ADVISORY, Severity.INFO):
            for entry_verdict in self.all_verdicts():
                if entry_verdict.severity is severity:
                    return entry_verdict.rule_id
        return None

    def all_verdicts(self) -> list[RuleVerdict]:
        return [*self.verdicts, *[change.verdict for change in self.changes if change.verdict]]

    def as_json(self) -> dict[str, Any]:
        return {
            "label": self.label,
            "namespace": self.namespace,
            "node_class": self.node_class,
            "old_node_id": self.old_node_id.as_xml(),
            "new_node_id": self.new_node_id.as_xml(),
            "breaking": self.breaking,
            "severity": self.severity.value,
            "verdicts": [verdict.as_json() for verdict in self.all_verdicts()],
            "changes": [change.as_json() for change in self.changes],
            "impacted_nodes": list(self.impact),
            "impact_count": len(self.impact),
        }


@dataclass(slots=True)
class BreakingChange:
    """One change a consumer can break on, or an advisory that should be read before release.

    ``reason`` is the human line; ``severity``, ``rule_id`` and ``rationale`` are the
    machine-readable verdict of the best-practices rule.
    """

    label: str
    node_id: ExpandedNodeId
    reason: str
    affected: int | None = None
    severity: Severity = Severity.BREAKING
    rule_id: str | None = None
    rationale: str | None = None
    clause: str | None = None

    @property
    def breaking(self) -> bool:
        return self.severity is Severity.BREAKING

    def as_json(self) -> dict[str, Any]:
        return {
            "label": self.label,
            "node_id": self.node_id.as_xml(),
            "reason": self.reason,
            "affected_nodes": self.affected,
            "breaking": self.breaking,
            "severity": self.severity.value,
            "rule_id": self.rule_id,
            "rationale": self.rationale,
            "clause": self.clause,
        }


@dataclass(frozen=True, slots=True)
class NamespaceChange:
    """Totals per namespace: nodes, references and data types, old → new."""

    alias: str
    uri: str
    nodes: tuple[int, int]
    references: tuple[int, int]
    datatypes: tuple[int, int]

    @staticmethod
    def delta(old: int, new: int) -> str:
        difference = new - old
        if difference == 0:
            return "no change"
        return f"{difference:+d}"

    def as_json(self) -> dict[str, Any]:
        return {
            "namespace": self.alias,
            "uri": self.uri,
            "nodes": {
                "old": self.nodes[0],
                "new": self.nodes[1],
                "delta": self.nodes[1] - self.nodes[0],
            },
            "references": {
                "old": self.references[0],
                "new": self.references[1],
                "delta": self.references[1] - self.references[0],
            },
            "datatypes": {
                "old": self.datatypes[0],
                "new": self.datatypes[1],
                "delta": self.datatypes[1] - self.datatypes[0],
            },
        }


@dataclass(slots=True)
class SemanticDiff:
    """The whole model-level comparison of one old model against one new model."""

    old_source: str = ""
    new_source: str = ""
    old_nodes: int = 0
    new_nodes: int = 0
    added: list[NodeEntry] = field(default_factory=list)
    removed: list[NodeEntry] = field(default_factory=list)
    modified: list[NodeModification] = field(default_factory=list)
    renumbered: list[RenumberedNode] = field(default_factory=list)
    namespaces: list[NamespaceChange] = field(default_factory=list)
    breaking: list[BreakingChange] = field(default_factory=list)
    advisory: list[BreakingChange] = field(default_factory=list)
    """Best-practices findings that never gate a release but should be read before one."""

    @property
    def is_empty(self) -> bool:
        return not (self.added or self.removed or self.modified or self.renumbered)

    @property
    def changed_nodes(self) -> list[ExpandedNodeId]:
        """Every node the diff touched — what impact analysis is computed for."""
        entries: list[NodeEntry | NodeModification | RenumberedNode] = [
            *self.removed,
            *self.modified,
            *self.renumbered,
        ]
        return [entry.node_id for entry in entries]

    def _all_entries(self) -> list[NodeEntry | NodeModification | RenumberedNode]:
        """Every entry the diff produced, in report order."""
        return [*self.added, *self.removed, *self.modified, *self.renumbered]

    def summary(self) -> dict[str, Any]:
        severities = Counter(entry.severity for entry in self._all_entries())
        return {
            "old_nodes": self.old_nodes,
            "new_nodes": self.new_nodes,
            "added_nodes": len(self.added),
            "removed_nodes": len(self.removed),
            "modified_nodes": len(self.modified),
            "renumbered_nodes": len(self.renumbered),
            "breaking_changes": len(self.breaking),
            "advisory_changes": len(self.advisory),
            "advisory_nodes": severities.get(Severity.ADVISORY, 0),
            "info_nodes": severities.get(Severity.INFO, 0),
            "namespace_changes": len(self.namespaces),
            "identical": self.is_empty,
        }

    def as_json(self) -> str:
        payload = {
            "old": self.old_source,
            "new": self.new_source,
            "paper": PAPER,
            "summary": self.summary(),
            "added_nodes": [entry.as_json() for entry in self.added],
            "removed_nodes": [entry.as_json() for entry in self.removed],
            "modified_nodes": [entry.as_json() for entry in self.modified],
            "renumbered_nodes": [entry.as_json() for entry in self.renumbered],
            "namespace_changes": [entry.as_json() for entry in self.namespaces],
            "breaking_changes": [entry.as_json() for entry in self.breaking],
            "advisory_changes": [entry.as_json() for entry in self.advisory],
        }
        return json.dumps(payload, indent=2)


def json_text(diff: SemanticDiff) -> str:
    """Machine-readable report for CI/CD."""
    return diff.as_json()
