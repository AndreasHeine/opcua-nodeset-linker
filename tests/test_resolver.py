"""Resolver tests: root selection and dependency closure."""

from __future__ import annotations

import pytest
from tests.conftest import DATA, UA_NS, make_object, make_object_type, make_variable, node_id

from nodeset_linker.graph.engine import SemanticGraph
from nodeset_linker.models.dependency import DependencyKind
from nodeset_linker.repository.model import build_model, load_nodesets
from nodeset_linker.resolver.closure import DependencyResolver
from nodeset_linker.resolver.roots import RootResolver, RootSelector
from nodeset_linker.utils.errors import ResolutionError


class TestRootSelectors:
    def test_parse_all_documented_kinds(self) -> None:
        for text, kind in (
            ("nodeid=nsu=http://x/;i=1", "nodeid"),
            ("BROWSENAME=MachineType", "browseName"),
            ("expandedNodeId=nsu=http://x/;i=1", "expandednodeid"),
            ("browseName=MachineType", "browseName"),
            ("namespaceUri=http://x/", "namespaceUri"),
            ("typeDefinition=MSSControllerType", "typeDefinition"),
        ):
            assert RootSelector.parse(text).kind == kind

    def test_bare_nodeid_is_accepted(self) -> None:
        assert RootSelector.parse("nsu=http://opcfoundation.org/UA/Machinery/;i=1001").kind == "nodeid"

    def test_unknown_kind_rejected(self) -> None:
        with pytest.raises(ResolutionError):
            RootSelector.parse("weird=Foo")

    def test_unmatched_selector_raises(self, merged_model) -> None:
        with pytest.raises(ResolutionError, match="matched nothing"):
            RootResolver(merged_model).resolve([RootSelector.parse("browseName=DoesNotExist")])

    def test_browse_name_and_wildcard(self, merged_model) -> None:
        roots = RootResolver(merged_model).resolve([RootSelector.parse("browseName=*Type")])
        assert node_id(1001) in roots

    def test_namespace_uri_selector(self, merged_model) -> None:
        roots = RootResolver(merged_model).resolve(
            [RootSelector.parse("namespaceUri=http://opcfoundation.org/UA/Machinery/")]
        )
        assert all(root.namespace_uri == "http://opcfoundation.org/UA/Machinery/" for root in roots)

    def test_type_definition_selector(self, merged_model) -> None:
        roots = RootResolver(merged_model).resolve([RootSelector.parse("typeDefinition=MSSControllerType")])
        assert node_id(1002, UA_NS) in roots  # the type itself
        assert node_id(2200, "http://opcfoundation.org/UA/Machinery/") in roots  # instances of it

    def test_ns_index_marker_resolves_to_uri(self, merged_model) -> None:
        roots = RootResolver(merged_model).resolve([RootSelector.parse("nodeid=ns=1;i=2001")])
        assert roots == [node_id(2001, "http://opcfoundation.org/UA/Machinery/")]

    def test_unknown_index_raises(self, merged_model) -> None:
        with pytest.raises(ResolutionError, match="namespace index 9"):
            RootResolver(merged_model).resolve([RootSelector.parse("nodeid=ns=9;i=1")])


class TestClosure:
    def closure_for(self, nodes: list, roots: list):  # type: ignore[no-untyped-def]
        graph = SemanticGraph.from_model(build_model(nodes))
        return DependencyResolver(graph).resolve(roots)

    def test_inheritance_chain_is_complete(self) -> None:
        a = make_object_type("A", 1)
        b = make_object_type("B", 2, refs=_ref("HasSubtype", 1))
        c = make_object_type("C", 3, refs=_ref("HasSubtype", 2))
        closure = self.closure_for([a, b, c], [node_id(3)])
        assert {node_id(1), node_id(2), node_id(3)} <= closure.ids()

    def test_base_does_not_pull_subtypes(self) -> None:
        a = make_object_type("A", 1)
        b = make_object_type("B", 2, refs=_ref("HasSubtype", 1))
        closure = self.closure_for([a, b], [node_id(1)])
        assert node_id(2) not in closure.ids()

    def test_structural_dependency_both_directions(self) -> None:
        parent = make_object_type("P", 1, refs=_ref("HasComponent", 2))
        child = make_object_type("C", 2)
        assert node_id(1) in self.closure_for([parent, child], [node_id(2)]).ids()
        assert node_id(2) in self.closure_for([parent, child], [node_id(1)]).ids()

    def test_cycles_are_safe(self) -> None:
        a = make_object("A", 1, refs=_ref("HasComponent", 2))
        b = make_object("B", 2, refs=_ref("HasComponent", 1))
        closure = self.closure_for([a, b], [node_id(1)])
        assert closure.ids() == {node_id(1), node_id(2)}

    def test_traversal_is_deterministic(self) -> None:
        nodes = _chain()
        first = self.closure_for(nodes, [node_id(1)])
        second = self.closure_for(nodes, [node_id(1)])
        assert list(first.retained.keys()) == list(second.retained.keys())

    def test_full_transitive_closure(self) -> None:
        closure = self.closure_for(_chain(), [node_id(1)])
        assert len(closure.ids()) == 5

    def test_reason_path_records_hops(self) -> None:
        nodes = _chain()
        closure = self.closure_for(nodes, [node_id(1)])
        reason = closure.reason_for(node_id(5))
        assert reason is not None
        assert [step.browse_name for step in reason.path][-1] == "E"
        assert reason.origin == node_id(1)

    def test_root_reason_has_empty_path(self) -> None:
        nodes = _chain()
        closure = self.closure_for(nodes, [node_id(1)])
        assert closure.reason_for(node_id(1)).path == ()

    def test_datatype_chain_followed(self) -> None:
        from tests.conftest import make_data_type, structure_definition

        enum = make_data_type("JobStateEnum", 2002)
        structure = make_data_type(
            "JobDataType", 2001, definition=structure_definition((("State", node_id(2002)),))
        )
        variable = make_variable("CurrentJob", 1013, data_type=node_id(2001))
        closure = self.closure_for([enum, structure, variable], [node_id(1013)])
        assert node_id(2002) in closure.ids()

    def test_encoding_dependency_followed(self) -> None:
        from tests.conftest import make_data_type

        encoding = make_data_type("JobDataTypeBinaryEncoding", 2010)
        structure = make_data_type("JobDataType", 2001, refs=_ref("HasEncoding", 2010))
        closure = self.closure_for([encoding, structure], [node_id(2001)])
        assert node_id(2010) in closure.ids()

    def test_interface_dependency_followed(self) -> None:
        interface = make_object("IMachineStatus", 2100)
        instance = make_object("Instance", 2200, refs=_ref("HasInterface", 2100))
        closure = self.closure_for([interface, instance], [node_id(2200)])
        assert node_id(2100) in closure.ids()

    def test_missing_root_raises(self) -> None:
        graph = SemanticGraph.from_model(build_model([make_object("A", 1)]))
        with pytest.raises(ValueError, match="not part of the model"):
            DependencyResolver(graph).resolve([node_id(99)])

    def test_closure_on_real_fixture_data(self) -> None:
        model = load_nodesets([DATA / "ua_base.xml", DATA / "machinery.xml"])
        graph = SemanticGraph.from_model(model)
        closure = DependencyResolver(graph).resolve([node_id(1002, UA_NS)])
        assert node_id(2002, "http://opcfoundation.org/UA/Machinery/") in closure.ids()
        assert node_id(2500, "http://opcfoundation.org/UA/Machinery/") not in closure.ids()

    def test_dependency_kind_recorded(self) -> None:
        nodes = _chain()
        closure = self.closure_for(nodes, [node_id(1)])
        assert closure.reason_for(node_id(2)).kind is DependencyKind.STRUCTURAL


def _chain():  # type: ignore[no-untyped-def]
    a = make_object("A", 1, refs=_ref("HasComponent", 2))
    b = make_object("B", 2, refs=_ref("HasComponent", 3))
    c = make_object("C", 3, refs=_ref("HasProperty", 4))
    d = make_object("D", 4, refs=_ref("HasComponent", 5))
    e = make_object("E", 5)
    return [a, b, c, d, e]


def _ref(name: str, target: int):  # type: ignore[no-untyped-def]
    from nodeset_linker.models.node import Reference

    return (Reference(reference_type=node_id(0, UA_NS), type_name=name, target=node_id(target)),)
