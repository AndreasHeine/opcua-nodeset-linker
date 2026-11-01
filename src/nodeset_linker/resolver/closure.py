"""Dependency closure over the semantic graph.

The traversal is deterministic, cycle-safe and explainable: a breadth-first walk with
an explicit ``deque`` and a visited set records, for every reached node, the *first*
path that reached it — which is what ``nodeset-linker explain`` prints.

References are directional: types do not pull their users, datatypes do not pull
variables that use them, and bases do not pull unrelated subtypes. Inverse ownership
references still let a parent discover its children when only the child declares the link.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from nodeset_linker.graph.engine import (
    OWNERSHIP_REFERENCES,
    SemanticGraph,
    follows_reference,
    reverse_dependency,
)
from nodeset_linker.models.dependency import DependencyKind
from nodeset_linker.models.identifiers import UA_NAMESPACE_URI, ExpandedNodeId
from nodeset_linker.models.node_class import ModellingRule, NodeRole

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
    navigation_only: set[ExpandedNodeId] = field(default_factory=set)

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

    def __init__(
        self,
        graph: SemanticGraph,
        *,
        external: frozenset[str] = frozenset(),
        include_optional: bool = True,
    ) -> None:
        self._graph = graph
        self._external = external
        self._include_optional = include_optional

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
            self._pull_namespace_metadata(closure, queue)
            self._pull_reference_types(closure, queue)
            if not queue:
                break
            self._drain(closure, queue)
        return closure

    def _drain(self, closure: Closure, queue: deque[QueueEntry]) -> None:
        """Follow every dependency of the queued nodes until the queue is empty."""
        while queue:
            current, path, origin = queue.popleft()
            if current.namespace in self._external:
                continue
            for target, metadata in self._graph.out_edges(current):
                semantics = self._graph.reference_semantics(metadata.reference_type)
                if (
                    semantics in OWNERSHIP_REFERENCES | {"Organizes"}
                    and not metadata.is_forward
                ) or semantics == "ParentNodeId":
                    step = ClosureStep(
                        target, self._label(target), metadata.reference_type,
                        metadata.dependency_kind, metadata.is_forward,
                    )
                    self._visit(target, origin, (*path, step), closure, queue, navigation_only=True)
                    continue
                if current in closure.navigation_only and semantics in (
                    OWNERSHIP_REFERENCES | {"Organizes"}
                ):
                    continue
                if not follows_reference(semantics, metadata.is_forward):
                    continue
                if self._omit_optional_child(target, semantics):
                    continue
                step = ClosureStep(
                    node_id=target,
                    browse_name=self._label(target),
                    reference_type=metadata.reference_type,
                    dependency_kind=metadata.dependency_kind,
                    is_forward=metadata.is_forward,
                )
                self._visit(target, origin, (*path, step), closure, queue)

            for source, metadata in self._graph.in_edges(current):
                semantics = self._graph.reference_semantics(metadata.reference_type)
                if semantics in OWNERSHIP_REFERENCES | {"Organizes"} and metadata.is_forward:
                    step = ClosureStep(
                        source, self._label(source), metadata.reference_type,
                        metadata.dependency_kind, False,
                    )
                    self._visit(source, origin, (*path, step), closure, queue, navigation_only=True)
                    continue
                if current in closure.navigation_only and semantics in (
                    OWNERSHIP_REFERENCES | {"Organizes"}
                ):
                    continue
                if not reverse_dependency(semantics, metadata.is_forward):
                    continue
                if self._omit_optional_child(source, semantics):
                    continue
                step = ClosureStep(
                    node_id=source,
                    browse_name=self._label(source),
                    reference_type=metadata.reference_type,
                    dependency_kind=metadata.dependency_kind,
                    is_forward=False,
                )
                self._visit(source, origin, (*path, step), closure, queue)

    def _pull_namespace_metadata(self, closure: Closure, queue: deque[QueueEntry]) -> None:
        namespaces = {
            node_id.namespace: reason
            for node_id, reason in closure.retained.items()
            if node_id.namespace not in self._external
        }
        for node in self._graph.nodes():
            if node.role is not NodeRole.NAMESPACE_METADATA or node.node_id in closure.retained:
                continue
            reason = namespaces.get(node.node_id.namespace)
            if reason is None:
                continue
            step = ClosureStep(
                node.node_id, node.browse_name.name, "NamespaceMetadata",
                DependencyKind.NAMESPACE, True,
            )
            self._visit(node.node_id, reason.origin, (*reason.path, step), closure, queue)

    def _omit_optional_child(self, node_id: ExpandedNodeId, reference_type: str) -> bool:
        node = self._graph.node(node_id)
        return (
            not self._include_optional
            and reference_type in OWNERSHIP_REFERENCES | {"Organizes"}
            and node is not None
            and node.modelling_rule
            in {ModellingRule.OPTIONAL, ModellingRule.OPTIONAL_PLACEHOLDER}
        )

    def _pull_reference_types(self, closure: Closure, queue: deque[QueueEntry]) -> None:
        """Declare every reference type a retained node actually uses.

        A ``<Reference ReferenceType="ConnectsTo">`` only resolves if the type is part of
        the document, so a model's own reference types join the closure.  Types of the OPC
        UA namespace do not: they come with the base namespace every importer loads.
        """
        for node_id in list(closure.retained):
            reason = closure.retained[node_id]
            node: UANode | None = self._graph.node(node_id)
            if node is None or node_id.namespace in self._external:
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
        *,
        navigation_only: bool = False,
    ) -> None:
        if target in closure.retained:
            if not navigation_only and target in closure.navigation_only:
                closure.navigation_only.remove(target)
                queue.append((target, path, origin))
            return  # cycle-safe: first path wins, traversal stays deterministic
        label = closure.retained[origin].origin_label
        closure.retained[target] = RetentionReason(
            node_id=target,
            origin=origin,
            origin_label=label,
            path=path,
            kind=path[-1].dependency_kind,
        )
        if navigation_only:
            closure.navigation_only.add(target)
        queue.append((target, path, origin))

    def _label(self, node_id: ExpandedNodeId) -> str:
        node: UANode | None = self._graph.node(node_id)
        if node is None:
            return str(node_id)
        return node.browse_name.name or str(node_id)
