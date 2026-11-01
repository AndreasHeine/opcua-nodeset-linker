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

    @classmethod
    def from_model(cls, model: SemanticModel) -> Self:
        graph = cls()
        for node in model.iter_nodes():
            graph.add_node(node)
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
    return edges


def _edge_metadata(attrs: dict[str, object]) -> EdgeMetadata:
    return EdgeMetadata(
        reference_type=str(attrs["reference_type"]),
        dependency_kind=attrs["dependency_kind"],  # type: ignore[arg-type]
        is_forward=bool(attrs["is_forward"]),
        mandatory=bool(attrs.get("mandatory", True)),
    )
