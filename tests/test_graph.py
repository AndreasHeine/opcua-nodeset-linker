"""Graph engine tests — the graph is XML-independent by construction."""

from __future__ import annotations

import pytest
from tests.conftest import (
    UA_NS,
    make_data_type,
    make_object,
    make_object_type,
    make_variable,
    node_id,
)

from nodeset_linker.graph.engine import STANDARD_REFERENCE_TYPES, EdgeMetadata, SemanticGraph
from nodeset_linker.models.dependency import DependencyKind
from nodeset_linker.repository.model import build_model


def graph_of(nodes: list) -> SemanticGraph:  # type: ignore[type-arg]
    return SemanticGraph.from_model(build_model(nodes))


class TestGraphConstruction:
    def test_nodes_become_vertices(self) -> None:
        graph = graph_of([make_object("A", 1), make_object("B", 2)])
        assert graph.node_count() == 2
        assert graph.node(node_id(1)) is not None

    def test_references_become_edges_with_metadata(self) -> None:
        a = make_object("A", 1, refs=_ref("HasComponent", 2))
        graph = graph_of([a, make_object("B", 2)])
        target, metadata = next(graph.out_edges(node_id(1)))
        assert target == node_id(2)
        assert metadata.reference_type == "HasComponent"
        assert metadata.dependency_kind is DependencyKind.STRUCTURAL
        assert metadata.is_forward is True

    def test_multiple_references_between_same_pair(self) -> None:
        a = make_object("A", 1, refs=_ref("HasComponent", 2) + _ref("HasProperty", 2))
        graph = graph_of([a, make_object("B", 2)])
        assert len(list(graph.out_edges(node_id(1)))) == 2

    def test_custom_reference_type_is_supported(self) -> None:
        a = make_object("A", 1, refs=_ref("HasMachineState", 2))
        graph = graph_of([a, make_object("B", 2)])
        _, metadata = next(graph.out_edges(node_id(1)))
        assert metadata.reference_type == "HasMachineState"

    def test_custom_reference_base_declared_from_the_base_side(self) -> None:
        """``HasSubtype`` declared on the base side must classify the custom reference."""
        from nodeset_linker.models.identifiers import QualifiedName
        from nodeset_linker.models.node import UANode
        from nodeset_linker.models.node_class import NodeClass, NodeRole

        component = UANode(
            node_id=node_id(47, UA_NS),
            browse_name=QualifiedName(name="HasComponent"),
            node_class=NodeClass.REFERENCE_TYPE,
            role=NodeRole.BASE_TYPE,
            references=_ref("HasSubtype", 5000, forward=True),
        )
        custom = UANode(
            node_id=node_id(5000, UA_NS),
            browse_name=QualifiedName(name="HasJobInfo"),
            node_class=NodeClass.REFERENCE_TYPE,
            role=NodeRole.BASE_TYPE,
        )
        graph = graph_of([component, custom])
        assert graph.reference_semantics("HasJobInfo") == "HasComponent"

    def test_datatype_subtype_chain_is_not_inheritance(self) -> None:
        base = make_data_type("BaseDataType", 49)
        derived = make_data_type("JobDataType", 2001, refs=_ref("HasSubtype", 49))
        graph = graph_of([base, derived])
        _, metadata = next(graph.out_edges(derived.node_id))
        assert metadata.dependency_kind is DependencyKind.DATATYPE

    def test_variable_datatype_attribute_becomes_edge(self) -> None:
        variable = make_variable("CurrentJob", 1013, data_type=node_id(2001))
        graph = graph_of([variable, make_data_type("JobDataType", 2001)])
        target, metadata = next(graph.out_edges(node_id(1013)))
        assert metadata.dependency_kind is DependencyKind.DATATYPE
        assert target == node_id(2001)

    def test_structure_fields_become_edges(self) -> None:
        from tests.conftest import structure_definition

        structure = make_data_type("JobDataType", 2001, definition=structure_definition((("State", node_id(2002)),)))
        graph = graph_of([structure, make_data_type("JobStateEnum", 2002)])
        assert node_id(2002) in [t for t, _ in graph.out_edges(node_id(2001))]

    def test_duplicate_node_rejected(self) -> None:
        graph = SemanticGraph()
        graph.add_node(make_object("A", 1))
        with pytest.raises(ValueError, match="duplicate"):
            graph.add_node(make_object("A2", 1))

    def test_standard_reference_types_cover_documented_set(self) -> None:
        for name in (
            "HasSubtype",
            "HasComponent",
            "HasProperty",
            "HasOrderedComponent",
            "HasTypeDefinition",
            "HasInterface",
            "GeneratesEvent",
            "HasEncoding",
            "HasDescription",
            "Organizes",
        ):
            assert name in STANDARD_REFERENCE_TYPES


class TestGraphOperations:
    def test_neighbours_ignore_direction(self) -> None:
        parent = make_object_type("MachineType", 1001, refs=_ref("HasProperty", 1010))
        child = make_variable("MachineName", 1010, refs=_ref("HasProperty", 1001, forward=False))
        graph = graph_of([parent, child])
        assert set(graph.neighbours(node_id(1010))) == {node_id(1001)}

    def test_in_edges(self) -> None:
        parent = make_object_type("MachineType", 1001, refs=_ref("HasComponent", 1011))
        child = make_object_type("JobManagementType", 1011)
        graph = graph_of([parent, child])
        assert [source for source, _ in graph.in_edges(node_id(1011))] == [node_id(1001)]

    def test_subgraph_drops_edges_to_removed_nodes(self) -> None:
        a = make_object("A", 1, refs=_ref("HasComponent", 2))
        graph = graph_of([a, make_object("B", 2)])
        reduced = graph.subgraph({node_id(1)})
        assert reduced.node_count() == 1
        assert reduced.edge_count() == 0

    def test_edge_metadata_defaults_mandatory(self) -> None:
        metadata = EdgeMetadata(reference_type="HasComponent", dependency_kind=DependencyKind.STRUCTURAL, is_forward=True)
        assert metadata.mandatory is True
        assert metadata.as_dict()["dependency_kind"] is DependencyKind.STRUCTURAL


def _ref(name: str, target: int, forward: bool = True):  # type: ignore[no-untyped-def]
    from nodeset_linker.models.node import Reference

    return (Reference(reference_type=node_id(0, UA_NS), type_name=name, target=node_id(target), is_forward=forward),)
