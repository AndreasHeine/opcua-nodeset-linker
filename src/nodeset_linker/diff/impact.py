"""Impact analysis: which nodes depend on a changed node.

Impact reuses the semantic dependency graph, so a change is counted where it actually
reaches consumers: a component, a subtype, an encoding, an interface implementation.
``Organizes`` and ``ParentNodeId`` edges are excluded — folders are browsing structure,
not a dependency that breaks when a node changes.

The walk reports *identifiers*, not BrowseNames: the same BrowseName in two namespaces is
two different nodes, and a JSON consumer cannot resolve a label.  Labels stay available for
humans, and only the identifier list is authoritative.
"""

from __future__ import annotations

from collections import deque
from collections.abc import Sequence

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
from nodeset_linker.models.node import UANode
from nodeset_linker.repository.model import SemanticModel

#: Browsing structure, not a dependency: excluded from impact walks.
STRUCTURAL_ONLY = frozenset({DependencyKind.NAMESPACE})

#: Reverse inheritance edges are followed at most this many hops.  Removing a base type
#: would otherwise report its entire subtype forest, which is correct but unreadable.
INHERITANCE_DEPTH_LIMIT = 3


def impacted(
    model: SemanticModel,
    node_id: ExpandedNodeId,
    *,
    inheritance_depth: int = INHERITANCE_DEPTH_LIMIT,
) -> tuple[ExpandedNodeId, ...]:
    """Identifiers of the nodes that depend on ``node_id`` in ``model``."""
    return _dependents(
        SemanticGraph.from_model(model), node_id, inheritance_depth=inheritance_depth
    )


def dependents(
    model: SemanticModel,
    node_id: ExpandedNodeId,
    *,
    inheritance_depth: int = INHERITANCE_DEPTH_LIMIT,
) -> tuple[str, ...]:
    """Labels of the nodes that depend on ``node_id`` — the human-readable form."""
    graph = SemanticGraph.from_model(model)
    return _labels(
        _dependents(graph, node_id, inheritance_depth=inheritance_depth), [graph]
    )


def apply_impact(
    diff: SemanticDiff,
    old_model: SemanticModel,
    new_model: SemanticModel,
    *,
    context: Sequence[SemanticModel] = (),
    inheritance_depth: int = INHERITANCE_DEPTH_LIMIT,
) -> SemanticDiff:
    """Fill the impact fields of a diff from the dependency graphs of both models.

    Removed nodes are measured in the old model, where their consumers still exist;
    modified and renumbered nodes are measured in the new model, which is the contract
    consumers now have to live with.  ``context`` adds consumer models — the providers of
    externalized specs or a sibling companion spec — so consumers that live outside the two
    compared files are counted instead of silently undercounted.
    """
    old_graph = SemanticGraph.from_model(old_model)
    new_graph = SemanticGraph.from_model(new_model)
    context_graphs = [SemanticGraph.from_model(model) for model in context]
    old_graphs = [old_graph, *context_graphs]
    new_graphs = [new_graph, *context_graphs]
    for entry in diff.removed:
        _fill(entry, old_graphs, entry.node_id, inheritance_depth)
    for modification in diff.modified:
        _fill(modification, new_graphs, modification.node_id, inheritance_depth)
    for renumbered in diff.renumbered:
        ids = _walk(new_graphs, renumbered.new_node_id, inheritance_depth)
        ids.update(_walk(old_graphs, renumbered.old_node_id, inheritance_depth))
        _set(renumbered, ids, new_graphs + old_graphs)
    for breaking in diff.breaking:
        graphs = new_graphs if new_graphs[0].has_node(breaking.node_id) else old_graphs
        breaking.affected = len(_walk(graphs, breaking.node_id, inheritance_depth))
    return diff


def total_impact(diff: SemanticDiff) -> int:
    """Distinct nodes touched by any change in the diff, counted by identifier."""
    touched: set[ExpandedNodeId] = set()
    entries: list[NodeEntry | NodeModification | RenumberedNode] = [
        *diff.removed,
        *diff.modified,
        *diff.renumbered,
    ]
    for entry in entries:
        touched.update(entry.impact_ids)
    return len(touched)


def _fill(
    entry: NodeEntry | NodeModification,
    graphs: Sequence[SemanticGraph],
    node_id: ExpandedNodeId,
    inheritance_depth: int,
) -> None:
    _set(entry, _walk(graphs, node_id, inheritance_depth), graphs)


def _set(
    entry: NodeEntry | NodeModification | RenumberedNode,
    ids: set[ExpandedNodeId],
    graphs: Sequence[SemanticGraph],
) -> None:
    ordered = tuple(sorted(ids, key=ExpandedNodeId.as_xml))
    entry.impact_ids = ordered
    entry.impact = _labels(ordered, graphs)


def _walk(
    graphs: Sequence[SemanticGraph],
    node_id: ExpandedNodeId,
    inheritance_depth: int,
) -> set[ExpandedNodeId]:
    """Impacted identifiers over every given graph, so external consumers are counted."""
    found: set[ExpandedNodeId] = set()
    for graph in graphs:
        if graph.has_node(node_id):
            found.update(_dependents(graph, node_id, inheritance_depth=inheritance_depth))
    return found


def _labels(ids: Sequence[ExpandedNodeId], graphs: Sequence[SemanticGraph]) -> tuple[str, ...]:
    """Human labels for the impacted identifiers, from whichever graph knows the node."""
    labels: set[str] = set()
    for node_id in ids:
        node = _node(graphs, node_id)
        if node is not None:
            labels.add(node_label(node))
    return tuple(sorted(labels))


def _node(graphs: Sequence[SemanticGraph], node_id: ExpandedNodeId) -> UANode | None:
    for graph in graphs:
        node = graph.node(node_id)
        if node is not None:
            return node
    return None


def _dependents(
    graph: SemanticGraph,
    node_id: ExpandedNodeId,
    *,
    inheritance_depth: int = INHERITANCE_DEPTH_LIMIT,
) -> tuple[ExpandedNodeId, ...]:
    """Reverse transitive closure over real dependency edges, by identifier."""
    seen: set[ExpandedNodeId] = set()
    found: list[ExpandedNodeId] = []
    queue: deque[tuple[ExpandedNodeId, int]] = deque([(node_id, 0)])
    while queue:
        current, depth = queue.popleft()
        for source, metadata in graph.in_edges(current):
            if metadata.dependency_kind in STRUCTURAL_ONLY or source in seen or source == node_id:
                continue
            inherited = metadata.dependency_kind is DependencyKind.INHERITANCE
            if inherited and depth >= inheritance_depth:
                # A base type is inherited through a deep forest; report the near generations.
                continue
            if graph.node(source) is None:
                # A phantom vertex is a reference whose target is not in the model.
                continue
            seen.add(source)
            found.append(source)
            queue.append((source, depth + 1 if inherited else depth))
    return tuple(sorted(found, key=ExpandedNodeId.as_xml))
