"""Semantic graph engine — completely independent of XML processing.

Vertices are :class:`~nodeset_linker.models.node.UANode` objects keyed by
:class:`~nodeset_linker.models.identifiers.ExpandedNodeId`; edges are OPC UA
references carried on a ``MultiDiGraph`` because several references may connect the
same pair of nodes (e.g. ``HasComponent`` plus a custom reference).

Every edge carries metadata:

    {"reference_type": "HasComponent", "dependency_kind": STRUCTURAL, "is_forward": True}

Nothing in this module knows about files, XML tags or NamespaceIndexes.
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from typing import TYPE_CHECKING, Self

import networkx as nx

from nodeset_linker.models.dependency import DependencyKind
from nodeset_linker.models.identifiers import ExpandedNodeId
from nodeset_linker.models.node import UANode
from nodeset_linker.models.node_class import NodeClass

if TYPE_CHECKING:  # pragma: no cover
    from nodeset_linker.repository.model import SemanticModel

STANDARD_REFERENCE_TYPES: dict[str, DependencyKind] = {
    "HasSubtype": DependencyKind.INHERITANCE,
    "HasTypeDefinition": DependencyKind.STRUCTURAL,
    "HasComponent": DependencyKind.STRUCTURAL,
    "HasProperty": DependencyKind.STRUCTURAL,
    "HasOrderedComponent": DependencyKind.STRUCTURAL,
    "HasInterface": DependencyKind.INTERFACE,
    "GeneratesEvent": DependencyKind.DATATYPE,
    "HasEncoding": DependencyKind.ENCODING,
    "HasDescription": DependencyKind.OPTIONAL,
    "Organizes": DependencyKind.NAMESPACE,
}

OWNERSHIP_REFERENCES = frozenset(
    {"HasComponent", "HasProperty", "HasOrderedComponent", "HasArgumentDescription",
     "HasOptionalInputArgumentDescription"}
)
FORWARD_DEPENDENCIES = frozenset(
    {"HasTypeDefinition", "HasInterface", "GeneratesEvent", "HasDescription"}
)


def follows_reference(reference_type: str, is_forward: bool) -> bool:
    """Whether a declared reference requires its target, rather than just navigation."""
    if reference_type == "HasSubtype":
        return not is_forward
    if reference_type in OWNERSHIP_REFERENCES | FORWARD_DEPENDENCIES | {"Organizes"}:
        return is_forward
    return True


def reverse_dependency(reference_type: str, is_forward: bool) -> bool:
    """Dependencies recoverable from a reference declared only at its other end."""
    if reference_type == "HasSubtype":
        return is_forward
    if reference_type == "HasEncoding":
        return True
    return reference_type in OWNERSHIP_REFERENCES | {"Organizes"} and not is_forward


def forward_subtype_declarations(
    model: SemanticModel,
) -> dict[ExpandedNodeId, list[ExpandedNodeId]]:
    """Subtypes declared on the *base* side of ``HasSubtype`` (``IsForward="true"``).

    OPC 10000-6 lets the base declare the link too: ``base -> subtype`` with
    ``IsForward="true"`` states the *subtype's* base.  A reader that only walks reverse
    declarations misses these bases; graph and validator share this one builder so both
    sides classify the same hierarchy.
    """
    declared: dict[ExpandedNodeId, list[ExpandedNodeId]] = {}
    for node in model.iter_nodes():
        for ref in node.references:
            if ref.type_name == "HasSubtype" and ref.is_forward:
                declared.setdefault(ref.target, []).append(node.node_id)
    return declared


@dataclass(frozen=True, slots=True)
class EdgeMetadata:
    """Metadata carried on every graph edge."""

    reference_type: str
    dependency_kind: DependencyKind
    is_forward: bool
    mandatory: bool = True

    def as_dict(self) -> dict[str, object]:
        return {
            "reference_type": self.reference_type,
            "dependency_kind": self.dependency_kind,
            "is_forward": self.is_forward,
            "mandatory": self.mandatory,
        }


@dataclass(frozen=True, slots=True)
class _NodeData:
    """Vertex payload; keeps node objects out of the networkx attribute dicts."""

    node: UANode


class SemanticGraph:
    """Directed semantic graph of an information model."""

    def __init__(self) -> None:
        self._graph: nx.MultiDiGraph[ExpandedNodeId, dict[str, object]] = nx.MultiDiGraph()
        self._nodes: dict[ExpandedNodeId, _NodeData] = {}
        self._reference_semantics: dict[str, str] = {}

    @classmethod
    def from_model(cls, model: SemanticModel) -> Self:
        graph = cls()
        for node in model.iter_nodes():
            graph.add_node(node)
        forward_bases = forward_subtype_declarations(model)
        for node in model.iter_nodes():
            if node.node_class is not NodeClass.REFERENCE_TYPE:
                continue
            pending = [node]
            seen: set[ExpandedNodeId] = set()
            while pending:
                reference_type = pending.pop()
                if reference_type.node_id in seen:
                    continue
                seen.add(reference_type.node_id)
                name = reference_type.browse_name.name
                if name in OWNERSHIP_REFERENCES | FORWARD_DEPENDENCIES | {"Organizes"}:
                    graph._reference_semantics[node.browse_name.name] = name
                    break
                if name == "Aggregates":
                    graph._reference_semantics[node.browse_name.name] = "HasComponent"
                    break
                bases = [
                    base
                    for ref in reference_type.references
                    if ref.type_name == "HasSubtype" and not ref.is_forward
                    and (base := model.get(ref.target)) is not None
                ]
                # A base may declare the link from its own side; that base is the
                # source pointing at this reference type, not one of its references.
                bases.extend(
                    base
                    for base in (
                        model.get(source)
                        for source in forward_bases.get(reference_type.node_id, [])
                    )
                    if base is not None
                )
                pending.extend(bases)
        for node in model.iter_nodes():
            for ref in node.references:
                kind = STANDARD_REFERENCE_TYPES.get(ref.type_name, DependencyKind.STRUCTURAL)
                if ref.type_name == "HasSubtype" and node.node_class is NodeClass.DATA_TYPE:
                    # A datatype base is a datatype, not an object type; the subtype
                    # chain must stay traversable even when its bases are abstract.
                    kind = DependencyKind.DATATYPE
                graph.add_edge(
                    node.node_id,
                    ref.target,
                    EdgeMetadata(
                        reference_type=ref.type_name,
                        dependency_kind=kind,
                        is_forward=ref.is_forward,
                    ).as_dict(),
                )
            for source, target, metadata in _implicit_edges(node):
                graph.add_edge(source, target, metadata)
        return graph

    def reference_semantics(self, name: str) -> str:
        """Use a custom reference's declared standard base when one is known."""
        return self._reference_semantics.get(name, name)

    @property
    def raw(self) -> nx.MultiDiGraph[ExpandedNodeId, dict[str, object]]:
        """The underlying networkx graph (read-only use outside this class)."""
        return self._graph

    def add_node(self, node: UANode) -> None:
        if node.node_id in self._nodes:
            raise ValueError(f"duplicate node {node.node_id}")
        self._nodes[node.node_id] = _NodeData(node=node)
        self._graph.add_node(node.node_id)

    def add_edge(
        self, source: ExpandedNodeId, target: ExpandedNodeId, metadata: dict[str, object]
    ) -> None:
        self._graph.add_edge(source, target, **metadata)

    def node(self, node_id: ExpandedNodeId) -> UANode | None:
        data = self._nodes.get(node_id)
        return data.node if data else None

    def nodes(self) -> Iterator[UANode]:
        for data in self._nodes.values():
            yield data.node

    def edges(self) -> Iterator[tuple[ExpandedNodeId, ExpandedNodeId, EdgeMetadata]]:
        for source, target, attrs in self._graph.edges(data=True):
            yield source, target, _edge_metadata(attrs)

    def out_edges(self, node_id: ExpandedNodeId) -> Iterator[tuple[ExpandedNodeId, EdgeMetadata]]:
        for _, target, attrs in self._graph.edges(node_id, data=True):
            yield target, _edge_metadata(attrs)

    def in_edges(self, node_id: ExpandedNodeId) -> Iterator[tuple[ExpandedNodeId, EdgeMetadata]]:
        for source, _, attrs in self._graph.in_edges(node_id, data=True):
            yield source, _edge_metadata(attrs)

    def neighbours(self, node_id: ExpandedNodeId) -> Iterable[ExpandedNodeId]:
        """Direction-independent successors and predecessors — closure-safe."""
        seen: dict[ExpandedNodeId, None] = {}
        for target in self._graph.successors(node_id):
            seen.setdefault(target, None)
        for source in self._graph.predecessors(node_id):
            seen.setdefault(source, None)
        return seen

    def edge_count(self) -> int:
        return self._graph.number_of_edges()

    def node_count(self) -> int:
        return self._graph.number_of_nodes()

    def has_node(self, node_id: ExpandedNodeId) -> bool:
        return node_id in self._graph

    def subgraph(self, keep: Iterable[ExpandedNodeId]) -> Self:
        """Induced subgraph over ``keep``; references to dropped nodes disappear."""
        subset = nx.subgraph(self._graph, set(keep))
        reduced = type(self)()
        reduced._reference_semantics = dict(self._reference_semantics)
        for node_id in subset.nodes():
            data = self._nodes[node_id]
            reduced._nodes[node_id] = data
            reduced._graph.add_node(node_id)
        for source, target, attrs in subset.edges(data=True):
            reduced._graph.add_edge(source, target, **attrs)
        return reduced

    def is_connected_ignoring_direction(self) -> bool:
        return nx.is_weakly_connected(self._graph)


def _implicit_edges(node: UANode) -> list[tuple[ExpandedNodeId, ExpandedNodeId, dict[str, object]]]:
    """Dependencies OPC UA semantics imply but XML does not express as references.

    A variable's ``DataType`` attribute, a structure field and a union alternative are
    real semantic dependencies; they become DATATYPE edges so the closure follows them.
    """
    edges: list[tuple[ExpandedNodeId, ExpandedNodeId, dict[str, object]]] = []
    data_type = getattr(node, "data_type", None)
    if data_type is not None:
        edges.append(
            (
                node.node_id,
                data_type,
                EdgeMetadata(
                    reference_type="DataType",
                    dependency_kind=DependencyKind.DATATYPE,
                    is_forward=True,
                ).as_dict(),
            )
        )
    definition = getattr(node, "definition", None)
    if definition is not None:
        for field in definition.fields:
            edges.append(
                (
                    node.node_id,
                    field.data_type,
                    EdgeMetadata(
                        reference_type=f"Field:{field.name}",
                        dependency_kind=DependencyKind.DATATYPE,
                        is_forward=True,
                    ).as_dict(),
                )
            )
        for alternative in definition.union_alternatives:
            edges.append(
                (
                    node.node_id,
                    alternative.data_type,
                    EdgeMetadata(
                        reference_type=f"Alternative:{alternative.name}",
                        dependency_kind=DependencyKind.DATATYPE,
                        is_forward=True,
                    ).as_dict(),
                )
            )
    if node.method_declaration_id is not None:
        edges.append(
            (
                node.node_id,
                node.method_declaration_id,
                EdgeMetadata("MethodDeclarationId", DependencyKind.STRUCTURAL, True).as_dict(),
            )
        )
    if node.parent_node_id is not None:
        edges.append(
            (
                node.node_id,
                node.parent_node_id,
                EdgeMetadata("ParentNodeId", DependencyKind.NAMESPACE, True).as_dict(),
            )
        )
    for _, identifier in node.value_dependencies:
        edges.append(
            (
                node.node_id,
                identifier,
                EdgeMetadata("ValueDataType", DependencyKind.DATATYPE, True).as_dict(),
            )
        )
    return edges


def _edge_metadata(attrs: dict[str, object]) -> EdgeMetadata:
    return EdgeMetadata(
        reference_type=str(attrs["reference_type"]),
        dependency_kind=attrs["dependency_kind"],  # type: ignore[arg-type]
        is_forward=bool(attrs["is_forward"]),
        mandatory=bool(attrs.get("mandatory", True)),
    )
