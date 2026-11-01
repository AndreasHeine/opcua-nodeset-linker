"""Reports: dependency, statistics and namespace output."""

from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from nodeset_linker.models.dependency import MandatoryLevel
from nodeset_linker.models.identifiers import ExpandedNodeId
from nodeset_linker.models.namespace_metadata import marked_namespaces
from nodeset_linker.models.node import UANode, mandatory_level_of, referenced_identifiers
from nodeset_linker.repository.model import SemanticModel
from nodeset_linker.resolver.closure import Closure

if TYPE_CHECKING:  # pragma: no cover
    from nodeset_linker.validator.semantic import ValidationReport


@dataclass(slots=True)
class Statistics:
    """Reduction statistics — the shape shown by ``nodeset-linker stats``.

    ``external_nodes`` are declarations of a model the output deliberately does not ship:
    they are *not* dropped dependencies, they are a dependency the consumer has to provide.
    ``removed_nodes`` excludes those declarations; ``reduction_percent`` uses only
    non-external input nodes as its baseline.
    """

    original_nodes: int
    retained_nodes: int
    removed_nodes: int
    reduction_percent: float
    external_nodes: int = 0

    def as_json(self) -> str:
        payload = {
            "original_nodes": self.original_nodes,
            "retained_nodes": self.retained_nodes,
            "removed_nodes": self.removed_nodes,
            "reduction_percent": round(self.reduction_percent, 1),
        }
        if self.external_nodes:
            payload["external_nodes"] = self.external_nodes
        return json.dumps(payload, indent=2)


@dataclass(slots=True)
class DependencyReportData:
    """Dependency tree, origins, retention reasons and category counts."""

    roots: list[str] = field(default_factory=list)
    origins: dict[str, list[str]] = field(default_factory=dict)
    reasons: dict[str, str] = field(default_factory=dict)
    categories: dict[str, int] = field(default_factory=dict)
    mandatory: list[str] = field(default_factory=list)
    optional: list[str] = field(default_factory=list)

    def as_json(self) -> str:
        return json.dumps(
            {
                "roots": self.roots,
                "dependency_origins": self.origins,
                "retention_reasons": self.reasons,
                "dependency_categories": self.categories,
                "mandatory_nodes": self.mandatory,
                "optional_nodes": self.optional,
            },
            indent=2,
        )


@dataclass(slots=True)
class NamespaceReportData:
    """Retained node counts per namespace plus what the export states about them.

    ``declared`` is how many nodes the loaded documents contributed for a namespace — the
    strongest claim the file made before the reduction.  ``subsets`` says whether the
    export kept fewer than that, and ``marked_in_export`` whether the namespace metadata
    object survived, which is the only place ``IsNamespaceSubset`` can be written.
    """

    counts: dict[str, int] = field(default_factory=dict)
    declared: dict[str, int] = field(default_factory=dict)
    subsets: dict[str, bool] = field(default_factory=dict)
    marked_in_export: dict[str, bool] = field(default_factory=dict)
    cross_namespace_edges: list[tuple[str, str]] = field(default_factory=list)
    external_models: list[str] = field(default_factory=list)
    external_dependencies: dict[str, int] = field(default_factory=dict)

    def as_json(self) -> str:
        payload = {
            "nodes_per_namespace": self.counts,
            "declared_per_namespace": self.declared,
            "namespace_subsets": self.subsets,
            "subset_markers_written": self.marked_in_export,
            "cross_namespace_dependencies": [
                {"from": source, "to": target} for source, target in self.cross_namespace_edges
            ],
        }
        if self.external_models:
            payload["external_models"] = self.external_models
            payload["external_references"] = self.external_dependencies
        return json.dumps(payload, indent=2)


def compute_statistics(
    model: SemanticModel,
    closure: Closure,
    *,
    retained: set[ExpandedNodeId] | None = None,
    external: frozenset[str] = frozenset(),
) -> Statistics:
    """Count the final retained set, or the closure for a closure-only analysis."""
    original = model.node_count()
    external_ids = {node_id for node_id in model.nodes if node_id.namespace in external}
    retained_ids = (closure.ids() if retained is None else retained) - external_ids
    eligible = original - len(external_ids)
    removed = eligible - len(retained_ids)
    return Statistics(
        original_nodes=original,
        retained_nodes=len(retained_ids),
        removed_nodes=removed,
        reduction_percent=(removed / eligible * 100.0) if eligible else 0.0,
        external_nodes=len(external_ids),
    )


def build_dependency_report(model: SemanticModel, closure: Closure) -> DependencyReportData:
    labels = {node_id: _label(model, node_id) for node_id in closure.ids()}
    categories: Counter[str] = Counter()
    origins: dict[str, list[str]] = {}
    reasons: dict[str, str] = {}
    mandatory: list[str] = []
    optional: list[str] = []

    for node_id, reason in closure.retained.items():
        label = labels[node_id]
        reasons[label] = reason.describe()
        origin_label = labels.get(reason.origin, str(reason.origin))
        origins.setdefault(origin_label, []).append(label)
        if reason.kind is not None:
            categories[reason.kind.value] += 1
        node = model.get(node_id)
        if node is not None:
            if mandatory_level_of(node) is MandatoryLevel.MANDATORY:
                mandatory.append(label)
            else:
                optional.append(label)

    return DependencyReportData(
        roots=[labels[root] for root in closure.roots],
        origins={origin: sorted(nodes) for origin, nodes in sorted(origins.items())},
        reasons=dict(sorted(reasons.items())),
        categories=dict(sorted(categories.items())),
        mandatory=sorted(mandatory),
        optional=sorted(optional),
    )


def build_namespace_report(
    model: SemanticModel, closure: Closure, *, external: frozenset[str] = frozenset()
) -> NamespaceReportData:
    retained = closure.ids()
    counts: Counter[str] = Counter()
    for node_id in retained:
        counts[_alias_of(model, node_id.namespace)] += 1
    declared: Counter[str] = Counter()
    for node_id in model.nodes:
        declared[_alias_of(model, node_id.namespace)] += 1

    pairs: set[tuple[str, str]] = set()
    external_refs: Counter[str] = Counter()
    for node_id in retained:
        node = model.get(node_id)
        if node is None:
            continue
        source_ns = _alias_of(model, node_id.namespace)
        for ref in node.references:
            if ref.target in retained and ref.target.namespace != node_id.namespace:
                pairs.add((source_ns, _alias_of(model, ref.target.namespace)))
        for identifier in referenced_identifiers(node):
            if identifier.namespace in external:
                external_refs[_alias_of(model, identifier.namespace)] += 1

    subsets = marked_namespaces(model, retained)
    return NamespaceReportData(
        counts=dict(sorted(counts.items())),
        declared=dict(sorted(declared.items())),
        subsets={
            _alias_of(model, uri): subset for uri, (subset, _present) in sorted(subsets.items())
        },
        marked_in_export={
            _alias_of(model, uri): present for uri, (_subset, present) in sorted(subsets.items())
        },
        cross_namespace_edges=sorted(pairs),
        external_models=sorted(external),
        external_dependencies=dict(sorted(external_refs.items())),
    )


def build_validation_report_text(report: ValidationReport) -> str:
    lines = [str(finding) for finding in report.errors + report.warnings]
    return "\n".join(lines) if lines else "no findings"


def _label(model: SemanticModel, node_id: ExpandedNodeId) -> str:
    node: UANode | None = model.get(node_id)
    if node is None:
        return str(node_id)
    return node.browse_name.name or str(node_id)


def _alias_of(model: SemanticModel, uri: str) -> str:
    namespace = model.namespaces.get(uri)
    return namespace.alias if namespace and namespace.alias else uri
