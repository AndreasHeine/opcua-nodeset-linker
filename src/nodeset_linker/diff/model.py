"""Result objects of a semantic diff — the shape humans and CI both read.

Every entry names a *semantic* entity (a node, a definition member, a reference), never
an XML location.  ``Change.describe()`` is the single place where the +/- lines users
read are produced, so console, Markdown and HTML stay consistent with the JSON.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

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
    happened (``attribute``, ``added``, ``removed``, ``changed``) and ``breaking`` carries
    the verdict of the breaking-change rules, so a report never re-derives it.
    """

    area: str
    kind: str
    subject: str | None = None
    old: str | None = None
    new: str | None = None
    breaking: bool = False

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
        return {
            "area": self.area,
            "kind": self.kind,
            "subject": self.subject,
            "old": self.old,
            "new": self.new,
            "breaking": self.breaking,
            "text": self.describe(),
        }


@dataclass(slots=True)
class NodeEntry:
    """A node that appeared (``added``) or disappeared (``removed``)."""

    label: str
    node_id: ExpandedNodeId
    node_class: str
    namespace: str
    breaking: bool = False
    impact: tuple[str, ...] = ()

    def as_json(self) -> dict[str, Any]:
        return {
            "label": self.label,
            "node_id": self.node_id.as_xml(),
            "namespace": self.namespace,
            "node_class": self.node_class,
            "breaking": self.breaking,
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
    impact: tuple[str, ...] = ()

    @property
    def breaking(self) -> bool:
        return any(change.breaking for change in self.changes)

    def breaking_changes(self) -> list[Change]:
        return [change for change in self.changes if change.breaking]

    def as_json(self) -> dict[str, Any]:
        return {
            "label": self.label,
            "node_id": self.node_id.as_xml(),
            "namespace": self.namespace,
            "node_class": self.node_class,
            "breaking": self.breaking,
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
    impact: tuple[str, ...] = ()

    @property
    def node_id(self) -> ExpandedNodeId:
        """The identifier the node has now — a renumbering reports the new identity."""
        return self.new_node_id

    @property
    def breaking(self) -> bool:
        return any(change.breaking for change in self.changes)

    def as_json(self) -> dict[str, Any]:
        return {
            "label": self.label,
            "namespace": self.namespace,
            "node_class": self.node_class,
            "old_node_id": self.old_node_id.as_xml(),
            "new_node_id": self.new_node_id.as_xml(),
            "breaking": self.breaking,
            "changes": [change.as_json() for change in self.changes],
            "impacted_nodes": list(self.impact),
            "impact_count": len(self.impact),
        }


@dataclass(slots=True)
class BreakingChange:
    """One change that may break consumers of the model."""

    label: str
    node_id: ExpandedNodeId
    reason: str
    affected: int | None = None

    def as_json(self) -> dict[str, Any]:
        return {
            "label": self.label,
            "node_id": self.node_id.as_xml(),
            "reason": self.reason,
            "affected_nodes": self.affected,
            "breaking": True,
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

    def summary(self) -> dict[str, Any]:
        return {
            "old_nodes": self.old_nodes,
            "new_nodes": self.new_nodes,
            "added_nodes": len(self.added),
            "removed_nodes": len(self.removed),
            "modified_nodes": len(self.modified),
            "renumbered_nodes": len(self.renumbered),
            "breaking_changes": len(self.breaking),
            "namespace_changes": len(self.namespaces),
            "identical": self.is_empty,
        }

    def as_json(self) -> str:
        payload = {
            "old": self.old_source,
            "new": self.new_source,
            "summary": self.summary(),
            "added_nodes": [entry.as_json() for entry in self.added],
            "removed_nodes": [entry.as_json() for entry in self.removed],
            "modified_nodes": [entry.as_json() for entry in self.modified],
            "renumbered_nodes": [entry.as_json() for entry in self.renumbered],
            "namespace_changes": [entry.as_json() for entry in self.namespaces],
            "breaking_changes": [entry.as_json() for entry in self.breaking],
        }
        return json.dumps(payload, indent=2)


def json_text(diff: SemanticDiff) -> str:
    """Machine-readable report for CI/CD."""
    return diff.as_json()
