"""Dependency closure over the semantic graph.

The traversal is deterministic, cycle-safe and explainable: a breadth-first walk with
an explicit ``deque`` and a visited set records, for every reached node, the *first*
path that reached it — which is what ``nodeset-linker explain`` prints.

Direction matters only in one place: inheritance must be followed upward.  A retained
node whose subtype lives elsewhere still needs its own base chain, so ``HasSubtype``
edges are traversed forward (to bases) and never backward (a base does not pull its
subtypes).  All other reference kinds are traversed in both directions, because OPC UA
reference types are symmetric by definition: an inverted ``HasComponent`` still means
the component belongs to the parent.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from nodeset_linker.graph.engine import SemanticGraph
from nodeset_linker.models.dependency import DependencyKind
from nodeset_linker.models.identifiers import UA_NAMESPACE_URI, ExpandedNodeId

if TYPE_CHECKING:  # pragma: no cover
    from nodeset_linker.models.node import UANode

INHERITANCE_REFERENCE = "HasSubtype"


@dataclass(frozen=True, slots=True)
class ClosureStep:
    """One hop of a dependency path."""

    node_id: ExpandedNodeId
    browse_name: str
    reference_type: str
    dependency_kind: DependencyKind
    is_forward: bool


@dataclass(slots=True)
class RetentionReason:
    """Why a node was retained — first-class traceability.

    ``origin`` is the user-selected root whose closure reached this node; ``path`` is
    the concrete hop chain from that root to it.  Roots have an empty path and are
    explained by their selector instead.
    """

    node_id: ExpandedNodeId
    origin: ExpandedNodeId
    origin_label: str
    path: tuple[ClosureStep, ...] = ()
    kind: DependencyKind | None = None

    def describe(self) -> str:
        if not self.path:
            return f"selected root ({self.origin_label})"
        hops = " -> ".join(
            f"{step.browse_name} [{step.reference_type}]"
            for step in (self.origin_step(), *self.path)
        )
        return f"required by {self.origin_label}: {hops}"

    def origin_step(self) -> ClosureStep:
        first = self.path[0]
        return ClosureStep(
            node_id=self.origin,
            browse_name=self.origin_label,
            reference_type=first.reference_type,
            dependency_kind=first.dependency_kind,
            is_forward=True,
        )


@dataclass(slots=True)
class Closure:
    """Result of a dependency closure computation."""

    retained: dict[ExpandedNodeId, RetentionReason] = field(default_factory=dict)
    roots: tuple[ExpandedNodeId, ...] = ()

    def contains(self, node_id: ExpandedNodeId) -> bool:
        return node_id in self.retained

    def reason_for(self, node_id: ExpandedNodeId) -> RetentionReason | None:
        return self.retained.get(node_id)

    def ids(self) -> set[ExpandedNodeId]:
        return set(self.retained)


#: A queued node with the path that reached it and the root it came from.
QueueEntry = tuple[ExpandedNodeId, tuple[ClosureStep, ...], ExpandedNodeId]


class DependencyResolver:
    """Computes the transitive dependency closure of selected roots."""

    def __init__(self, graph: SemanticGraph) -> None:
        self._graph = graph

    def resolve(
        self, roots: list[ExpandedNodeId], root_labels: dict[ExpandedNodeId, str] | None = None
    ) -> Closure:
        labels = root_labels or {}
        closure = Closure(roots=tuple(roots))
        queue: deque[QueueEntry] = deque()
        for root in roots:
            if not self._graph.has_node(root):
                raise ValueError(f"root node is not part of the model: {root}")
            label = labels.get(root) or self._label(root)
            closure.retained[root] = RetentionReason(
                node_id=root, origin=root, origin_label=label, path=()
            )
            queue.append((root, (), root))

        self._drain(closure, queue)
        # A reference type a retained node uses has to be declared in the document, and it
        # brings its own bases with it — so the pull and the traversal repeat until stable.
        while True:
            self._pull_reference_types(closure, queue)
            if not queue:
                break
            self._drain(closure, queue)
        return closure

    def _drain(self, closure: Closure, queue: deque[QueueEntry]) -> None:
        """Follow every dependency of the queued nodes until the queue is empty."""
        while queue:
            current, path, origin = queue.popleft()
            for target, metadata in self._graph.out_edges(current):
                step = ClosureStep(
                    node_id=target,
                    browse_name=self._label(target),
                    reference_type=metadata.reference_type,
                    dependency_kind=metadata.dependency_kind,
                    is_forward=metadata.is_forward,
                )
                self._visit(target, origin, (*path, step), closure, queue)

            for source, metadata in self._graph.in_edges(current):
                if metadata.dependency_kind is DependencyKind.INHERITANCE:
                    continue  # never pull subtypes of a retained base
                step = ClosureStep(
                    node_id=source,
                    browse_name=self._label(source),
                    reference_type=metadata.reference_type,
                    dependency_kind=metadata.dependency_kind,
                    is_forward=False,
                )
                self._visit(source, origin, (*path, step), closure, queue)

    def _pull_reference_types(self, closure: Closure, queue: deque[QueueEntry]) -> None:
        """Declare every reference type a retained node actually uses.

        A ``<Reference ReferenceType="ConnectsTo">`` only resolves if the type is part of
        the document, so a model's own reference types join the closure.  Types of the OPC
        UA namespace do not: they come with the base namespace every importer loads.
        """
        for node_id in list(closure.retained):
            reason = closure.retained[node_id]
            node: UANode | None = self._graph.node(node_id)
            if node is None:
                continue
            for ref in node.references:
                type_id = ref.reference_type
                if type_id is None or type_id.namespace == UA_NAMESPACE_URI:
                    continue
                if type_id in closure.retained or not self._graph.has_node(type_id):
                    continue
                step = ClosureStep(
                    node_id=type_id,
                    browse_name=self._label(type_id),
                    reference_type=ref.type_name or str(type_id),
                    dependency_kind=DependencyKind.REFERENCETYPE,
                    is_forward=True,
                )
                self._visit(type_id, reason.origin, (*reason.path, step), closure, queue)

    def _visit(
        self,
        target: ExpandedNodeId,
        origin: ExpandedNodeId,
        path: tuple[ClosureStep, ...],
        closure: Closure,
        queue: deque[QueueEntry],
    ) -> None:
        if target in closure.retained:
            return  # cycle-safe: first path wins, traversal stays deterministic
        label = closure.retained[origin].origin_label
        closure.retained[target] = RetentionReason(
            node_id=target,
            origin=origin,
            origin_label=label,
            path=path,
            kind=path[-1].dependency_kind,
        )
        queue.append((target, path, origin))

    def _label(self, node_id: ExpandedNodeId) -> str:
        node: UANode | None = self._graph.node(node_id)
        if node is None:
            return str(node_id)
        return node.browse_name.name or str(node_id)
