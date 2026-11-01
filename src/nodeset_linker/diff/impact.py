"""Impact analysis: which nodes depend on a changed node.

Impact reuses the semantic dependency graph, so a change is counted where it actually
reaches consumers: a component, a subtype, an encoding, an interface implementation.
``Organizes`` and ``ParentNodeId`` edges are excluded — folders are browsing structure,
not a dependency that breaks when a node changes.
"""

from __future__ import annotations

from collections import deque

from nodeset_linker.diff.engine import node_label
from nodeset_linker.diff.model import (
    NodeEntry,
    NodeModification,
    RenumberedNode,
    SemanticDiff,
)
from nodeset_linker.graph.engine import SemanticGraph
from nodeset_linker.models.dependency import DependencyKind
from nodeset_linker.models.identifiers import ExpandedNodeId
from nodeset_linker.repository.model import SemanticModel

#: Browsing structure, not a dependency: excluded from impact walks.
STRUCTURAL_ONLY = frozenset({DependencyKind.NAMESPACE})


def dependents(model: SemanticModel, node_id: ExpandedNodeId) -> tuple[str, ...]:
    """Labels of the nodes that depend on ``node_id`` in ``model``."""
    return _dependents(SemanticGraph.from_model(model), node_id)


def apply_impact(
    diff: SemanticDiff, old_model: SemanticModel, new_model: SemanticModel
) -> SemanticDiff:
    """Fill the impact fields of a diff from the dependency graphs of both models.

    Removed nodes are measured in the old model, where their consumers still exist;
    modified and renumbered nodes are measured in the new model, which is the contract
    consumers now have to live with.
    """
    old_graph = SemanticGraph.from_model(old_model)
    new_graph = SemanticGraph.from_model(new_model)
    for entry in diff.removed:
        entry.impact = _dependents(old_graph, entry.node_id)
    for modification in diff.modified:
        modification.impact = _dependents(new_graph, modification.node_id)
    for renumbered in diff.renumbered:
        moved = set(_dependents(old_graph, renumbered.old_node_id))
        moved.update(_dependents(new_graph, renumbered.new_node_id))
        renumbered.impact = tuple(sorted(moved))
    for breaking in diff.breaking:
        graph = new_graph if new_graph.has_node(breaking.node_id) else old_graph
        breaking.affected = len(_dependents(graph, breaking.node_id))
    return diff


def total_impact(diff: SemanticDiff) -> int:
    """Distinct nodes touched by any change in the diff."""
    touched: set[str] = set()
    entries: list[NodeEntry | NodeModification | RenumberedNode] = [
        *diff.removed,
        *diff.modified,
        *diff.renumbered,
    ]
    for entry in entries:
        touched.update(entry.impact)
    return len(touched)


def _dependents(graph: SemanticGraph, node_id: ExpandedNodeId) -> tuple[str, ...]:
    """Reverse transitive closure over real dependency edges."""
    seen: set[ExpandedNodeId] = set()
    labels: set[str] = set()
    queue: deque[ExpandedNodeId] = deque([node_id])
    while queue:
        current = queue.popleft()
        for source, metadata in graph.in_edges(current):
            if metadata.dependency_kind in STRUCTURAL_ONLY or source in seen or source == node_id:
                continue
            dependent = graph.node(source)
            if dependent is None:
                # A phantom vertex is a reference whose target is not in the model.
                continue
            seen.add(source)
            labels.add(node_label(dependent))
            queue.append(source)
    return tuple(sorted(labels))
