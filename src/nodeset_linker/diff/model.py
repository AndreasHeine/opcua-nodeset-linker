"""Result objects of a semantic diff — the shape humans and CI both read.

Every entry names a *semantic* entity (a node, a definition member, a reference), never
an XML location.  ``Change.describe()`` is the single place where the +/- lines users
read are produced, so console, Markdown and HTML stay consistent with the JSON.
"""

from __future__ import annotations

import json
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from nodeset_linker.diff.rules import PAPER, RuleVerdict, Severity
from nodeset_linker.models.identifiers import ExpandedNodeId

#: How many impacted nodes a report and the JSON payload list; the count stays exact.
DEFAULT_IMPACT_LIMIT = 50

#: Version of the JSON payload this module writes.  Keys are additive only, so a consumer
#: must ignore keys it does not know; the version tells it when the shape grew.
SCHEMA_VERSION = "1.0"


def _impacted(ids: Sequence[ExpandedNodeId], limit: int | None) -> list[str]:
    """Impacted identifiers as XML, truncated to what a report shows."""
    return [identifier.as_xml() for identifier in ids[:limit]]


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
    "renamed": "Renamed",
    "duplicate_member": "Duplicate members",
    "moved": "Moved",
    "inserted": "Inserted",
    "supertype_incomplete": "Supertype gap",
    "enum_conflict": "Enumeration conflict",
    "interface_gap": "Interface declarations",
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
            text = f"+ {self.subject}{f' : {self.new}' if self.new else ''}"
        elif self.kind == "removed":
            text = f"- {self.subject}{f' : {self.old}' if self.old else ''}"
        elif self.kind == "changed":
            text = f"{self.subject}: {_spell(self.old)} → {_spell(self.new)}"
        else:
            label = CHANGE_LABELS.get(self.kind, self.kind)
            if self.old is None:
                text = f"{label}: + {_spell(self.new)}"
            elif self.new is None:
                text = f"{label}: - {_spell(self.old)}"
            else:
                text = f"{label}: {_spell(self.old)} → {_spell(self.new)}"
        if self.verdict is not None and self.verdict.waived:
            text = f"{text} [waived]"
        return text

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
            if self.verdict.waived:
                payload["waived"] = True
                payload["waived_by"] = self.verdict.waived_by
        return payload


@dataclass(slots=True)
class NodeEntry:
    """A node that appeared (``added``) or disappeared (``removed``)."""

    label: str
    node_id: ExpandedNodeId
    node_class: str
    namespace: str
    #: The NamespaceUri the node lives in; ``namespace`` is the document-local alias (§11.1).
    namespace_uri: str = ""
    verdicts: tuple[RuleVerdict, ...] = ()
    #: Human labels of the impacted nodes, for reports.
    impact: tuple[str, ...] = ()
    #: Identifiers of the impacted nodes — the authoritative list (§WP 13: a label alone
    #: cannot be resolved, and the same BrowseName in two namespaces is two nodes).
    impact_ids: tuple[ExpandedNodeId, ...] = ()

    @property
    def impact_total(self) -> int:
        """Exact number of impacted nodes, independent of what a report shows."""
        return len(self.impact_ids)

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

    def as_json(self, *, impact_limit: int | None = DEFAULT_IMPACT_LIMIT) -> dict[str, Any]:
        return {
            "label": self.label,
            "node_id": self.node_id.as_xml(),
            "namespace": self.namespace,
            "namespace_uri": self.namespace_uri,
            "node_class": self.node_class,
            "breaking": self.breaking,
            "severity": self.severity.value,
            "verdicts": [verdict.as_json() for verdict in self.verdicts],
            "impacted_nodes": _impacted(self.impact_ids, impact_limit),
            "impacted_labels": list(self.impact[:impact_limit]),
            "impact_count": self.impact_total,
        }


@dataclass(slots=True)
class NodeModification:
    """A node present in both models whose semantics differ."""

    label: str
    node_id: ExpandedNodeId
    node_class: str
    namespace: str
    #: The NamespaceUri the node lives in; ``namespace`` is the document-local alias (§11.1).
    namespace_uri: str = ""
    changes: list[Change] = field(default_factory=list)
    verdicts: tuple[RuleVerdict, ...] = ()
    #: Human labels of the impacted nodes, for reports.
    impact: tuple[str, ...] = ()
    #: Identifiers of the impacted nodes — the authoritative list.
    impact_ids: tuple[ExpandedNodeId, ...] = ()

    @property
    def impact_total(self) -> int:
        """Exact number of impacted nodes, independent of what a report shows."""
        return len(self.impact_ids)

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

    def as_json(self, *, impact_limit: int | None = DEFAULT_IMPACT_LIMIT) -> dict[str, Any]:
        return {
            "label": self.label,
            "node_id": self.node_id.as_xml(),
            "namespace": self.namespace,
            "namespace_uri": self.namespace_uri,
            "node_class": self.node_class,
            "breaking": self.breaking,
            "severity": self.severity.value,
            "verdicts": [verdict.as_json() for verdict in self.all_verdicts()],
            "changes": [change.as_json() for change in self.changes],
            "impacted_nodes": _impacted(self.impact_ids, impact_limit),
            "impacted_labels": list(self.impact[:impact_limit]),
            "impact_count": self.impact_total,
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
    #: The NamespaceUri the node lives in; ``namespace`` is the document-local alias (§11.1).
    namespace_uri: str = ""
    #: ``exact`` when namespace, BrowseName and node class paired one removed node with
    #: one added node; ``heuristic`` when a group had more than one candidate and the
    #: engine guessed, which a reader may want to check by hand (§3.2.17).
    confidence: str = "exact"
    changes: list[Change] = field(default_factory=list)
    verdicts: tuple[RuleVerdict, ...] = ()
    #: Human labels of the impacted nodes, for reports.
    impact: tuple[str, ...] = ()
    #: Identifiers of the impacted nodes — the authoritative list.
    impact_ids: tuple[ExpandedNodeId, ...] = ()

    @property
    def impact_total(self) -> int:
        """Exact number of impacted nodes, independent of what a report shows."""
        return len(self.impact_ids)

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

    def as_json(self, *, impact_limit: int | None = DEFAULT_IMPACT_LIMIT) -> dict[str, Any]:
        return {
            "label": self.label,
            "namespace": self.namespace,
            "namespace_uri": self.namespace_uri,
            "node_class": self.node_class,
            "old_node_id": self.old_node_id.as_xml(),
            "new_node_id": self.new_node_id.as_xml(),
            "confidence": self.confidence,
            "breaking": self.breaking,
            "severity": self.severity.value,
            "verdicts": [verdict.as_json() for verdict in self.all_verdicts()],
            "changes": [change.as_json() for change in self.changes],
            "impacted_nodes": _impacted(self.impact_ids, impact_limit),
            "impacted_labels": list(self.impact[:impact_limit]),
            "impact_count": self.impact_total,
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
    #: WP 15: an accepted finding stays listed; only the gate reads these markers.
    waived: bool = False
    waived_by: str | None = None

    @property
    def breaking(self) -> bool:
        return self.severity is Severity.BREAKING

    def as_json(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
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
        if self.waived:
            payload["waived"] = True
            payload["waived_by"] = self.waived_by
        return payload


#: Namespace metadata attribute → the label used in human-readable output.
METADATA_LABELS: dict[str, str] = {
    "uri": "NamespaceUri",
    "alias": "Alias",
    "version": "Version",
    "publication_date": "PublicationDate",
    "xml_schema_uri": "XmlSchemaUri",
    "model_version": "ModelVersion",
    "required_models": "RequiredModel",
    "required_model_version": "RequiredModel version",
    "deprecations": "Deprecations",
}


@dataclass(frozen=True, slots=True)
class MetadataDelta:
    """One namespace metadata value, old → new, with the rule that judged it.

    A metadata conflict between two source documents has no old/new pair: it carries a
    note naming what the documents declared and what the merge kept.
    """

    attribute: str
    verdict: RuleVerdict
    old: str | None = None
    new: str | None = None
    note: str | None = None
    #: WP 15: namespace metadata can be waived, but the delta stays in the report.
    waived: bool = False
    waived_by: str | None = None

    @property
    def label(self) -> str:
        return METADATA_LABELS.get(self.attribute, self.attribute)

    def describe(self) -> str:
        if self.old is None and self.new is None:
            # A note-only delta (version drift, deprecation) *is* the note; never repeat it.
            text = self.note or self.label
            return f"{text} [waived]" if self.waived else text
        if self.old is None:
            text = f"{self.label}: + {self.new}"
        elif self.new is None:
            text = f"{self.label}: - {self.old}"
        else:
            text = f"{self.label}: {self.old} → {self.new}"
        text = f"{text} ({self.note})" if self.note else text
        return f"{text} [waived]" if self.waived else text

    def as_json(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "attribute": self.attribute,
            "old": self.old,
            "new": self.new,
            "severity": self.verdict.severity.value,
            "rule_id": self.verdict.rule_id,
            "rationale": self.verdict.rationale,
            "clause": self.verdict.clause,
            "note": self.note,
        }
        if self.waived:
            payload["waived"] = True
            payload["waived_by"] = self.waived_by
        return payload


@dataclass(frozen=True, slots=True)
class NamespaceChange:
    """Totals and release metadata per namespace: nodes, references and data types."""

    alias: str
    uri: str
    nodes: tuple[int, int]
    references: tuple[int, int]
    datatypes: tuple[int, int]
    metadata: tuple[MetadataDelta, ...] = ()
    verdicts: tuple[RuleVerdict, ...] = ()

    @property
    def breaking(self) -> bool:
        return any(verdict.breaking for verdict in self.all_verdicts())

    def breaking_unwaived(self) -> bool:
        """Whether a breaking namespace finding still blocks the gate.

        WP 15: ``--accept-breaking`` marks namespace findings too, so only an unmarked
        breaking finding keeps a release blocked.
        """
        return any(verdict.breaking and not verdict.waived for verdict in self.all_verdicts())

    @property
    def severity(self) -> Severity:
        """The strongest severity this namespace entry carries."""
        severities = {verdict.severity for verdict in self.all_verdicts()}
        for severity in (Severity.BREAKING, Severity.ADVISORY):
            if severity in severities:
                return severity
        return Severity.INFO

    @property
    def rule_id(self) -> str | None:
        """The rule behind the strongest severity, so a report can cite one id."""
        for severity in (Severity.BREAKING, Severity.ADVISORY, Severity.INFO):
            for verdict in self.all_verdicts():
                if verdict.severity is severity:
                    return verdict.rule_id
        return None

    def all_verdicts(self) -> list[RuleVerdict]:
        return [*self.verdicts, *[delta.verdict for delta in self.metadata]]

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
            "severity": self.severity.value,
            "rule_id": self.rule_id,
            "metadata": [delta.as_json() for delta in self.metadata],
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
    #: How many impacted nodes a report lists per entry; counts stay exact.
    impact_limit: int | None = DEFAULT_IMPACT_LIMIT

    @property
    def is_empty(self) -> bool:
        return not (self.added or self.removed or self.modified or self.renumbered)

    @property
    def breaking_namespaces(self) -> list[NamespaceChange]:
        """Namespaces whose *release* metadata broke (§11.3), most often a moved URI.

        These findings belong to a namespace, not to a node, so they never appear in
        ``breaking``; a release gate has to check both lists.
        """
        return [entry for entry in self.namespaces if entry.breaking]

    @property
    def changed_nodes(self) -> list[ExpandedNodeId]:
        """Every node the diff touched — what impact analysis is computed for."""
        entries: list[NodeEntry | NodeModification | RenumberedNode] = [
            *self.removed,
            *self.modified,
            *self.renumbered,
        ]
        return [entry.node_id for entry in entries]

    def blocking_breaking(self) -> list[BreakingChange]:
        """Breaking findings nothing accepted - what ``--fail-on-breaking`` fails on.

        WP 15: a waiver or ``--accept-breaking`` marks a finding instead of deleting it, so
        the report keeps the fact while the gate stops treating it as a failure.
        """
        return [entry for entry in self.breaking if not entry.waived]

    def blocking_namespaces(self) -> list[NamespaceChange]:
        """Namespace findings that still block the release."""
        return [entry for entry in self.namespaces if entry.breaking_unwaived()]

    def blocking_advisory(self) -> list[BreakingChange]:
        """Advisory findings ``--fail-on-advisory`` fails on: unwaived ones only."""
        return [
            entry
            for entry in self.advisory
            if entry.severity is Severity.ADVISORY and not entry.waived
        ]

    def blocking_advisory_namespaces(self) -> list[NamespaceChange]:
        """Namespace findings that are advisory and not waived."""
        return [
            entry
            for entry in self.namespaces
            if any(
                verdict.severity is Severity.ADVISORY and not verdict.waived
                for verdict in entry.all_verdicts()
            )
        ]

    def waived_findings(self) -> int:
        """How many findings carry a waiver marker (mirrored lists counted once)."""
        count = 0
        for entry in self._all_entries():
            if isinstance(entry, NodeModification | RenumberedNode):
                # A modification's verdicts are its change verdicts; count the changes only.
                count += sum(
                    change.verdict is not None and change.verdict.waived
                    for change in entry.changes
                )
            else:
                count += sum(verdict.waived for verdict in entry.verdicts)
        for namespace in self.namespaces:
            count += sum(verdict.waived for verdict in namespace.verdicts)
            count += sum(delta.waived for delta in namespace.metadata)
        return count

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
            "breaking_changes": len(self.breaking) + len(self.breaking_namespaces),
            "advisory_changes": len(self.advisory),
            "advisory_nodes": severities.get(Severity.ADVISORY, 0),
            "info_nodes": severities.get(Severity.INFO, 0),
            "namespace_changes": len(self.namespaces),
            "identical": self.is_empty,
            #: WP 15: findings a waiver or --accept-breaking marked; the buckets above stay.
            "waived_changes": self.waived_findings(),
        }

    def as_json(self) -> str:
        limit = self.impact_limit
        payload = {
            "schema_version": SCHEMA_VERSION,
            "old": self.old_source,
            "new": self.new_source,
            "paper": PAPER,
            "summary": self.summary(),
            "added_nodes": [entry.as_json(impact_limit=limit) for entry in self.added],
            "removed_nodes": [entry.as_json(impact_limit=limit) for entry in self.removed],
            "modified_nodes": [entry.as_json(impact_limit=limit) for entry in self.modified],
            "renumbered_nodes": [entry.as_json(impact_limit=limit) for entry in self.renumbered],
            "namespace_changes": [entry.as_json() for entry in self.namespaces],
            "breaking_changes": [entry.as_json() for entry in self.breaking],
            "advisory_changes": [entry.as_json() for entry in self.advisory],
        }
        return json.dumps(payload, indent=2)


def json_text(
    diff: SemanticDiff,
    *,
    release: Mapping[str, object] | None = None,
    waivers: Mapping[str, object] | None = None,
) -> str:
    """Machine-readable report for CI/CD.

    ``release`` adds the release verdict as an extra key; ``waivers`` adds what WP 15
    accepted.  Every existing key keeps its shape.
    """
    if release is None and waivers is None:
        return diff.as_json()
    payload = json.loads(diff.as_json())
    if release is not None:
        payload["release"] = release
    if waivers is not None:
        payload["waivers"] = waivers
    return json.dumps(payload, indent=2)
