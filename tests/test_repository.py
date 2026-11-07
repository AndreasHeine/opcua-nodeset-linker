"""Repository tests: merging documents into one semantic model."""

from __future__ import annotations

import pytest
from tests.conftest import UA_NS, make_object, make_object_type

from nodeset_linker.models.identifiers import ExpandedNodeId, NodeId, QualifiedName
from nodeset_linker.models.namespace import Namespace
from nodeset_linker.repository.model import (
    DuplicateNodeError,
    SemanticModel,
    build_model,
    load_nodesets,
)

OTHER = "http://example.com/Other/"


class TestLoading:
    def test_merge_keeps_all_nodes(self, ua_base, machinery, merged_model) -> None:
        # Nothing is dropped when documents are merged: every node of both files survives.
        assert merged_model.node_count() == (
            load_nodesets([ua_base]).node_count() + load_nodesets([machinery]).node_count()
        )
        assert set(merged_model.namespaces) == {UA_NS, "http://opcfoundation.org/UA/Machinery/"}

    def test_identical_redeclaration_is_tolerated(self, ua_base, machinery) -> None:
        model = load_nodesets([ua_base, ua_base])
        assert model.node_count() == load_nodesets([ua_base]).node_count()

    def test_conflicting_declaration_is_reported(self, tmp_path) -> None:
        first = tmp_path / "a.xml"
        second = tmp_path / "b.xml"
        body = '<UANodeSet xmlns="http://opcfoundation.org/UA/"><UAObject NodeId="i=99" BrowseName="A"/>'
        first.write_text(body + "</UANodeSet>", encoding="utf-8")
        second.write_text(
            '<UANodeSet xmlns="http://opcfoundation.org/UA/">'
            '<UAVariable NodeId="i=99" BrowseName="B"/></UANodeSet>',
            encoding="utf-8",
        )
        with pytest.raises(DuplicateNodeError) as excinfo:
            load_nodesets([first, second])
        assert excinfo.value.node_id == ExpandedNodeId.parse("i=99")
        assert excinfo.value.paths == [str(first), str(second)]

    def test_conflict_names_only_the_documents_that_declared_the_identifier(self, tmp_path) -> None:
        # A bundle of namespace files merges many documents; only the two that declared the
        # identifier may be named, or the report blames an innocent file.
        first = tmp_path / "a.xml"
        second = tmp_path / "b.xml"
        innocent = tmp_path / "c.xml"
        first.write_text(
            '<UANodeSet xmlns="http://opcfoundation.org/UA/">'
            '<UAObject NodeId="i=99" BrowseName="A"/></UANodeSet>',
            encoding="utf-8",
        )
        second.write_text(
            '<UANodeSet xmlns="http://opcfoundation.org/UA/">'
            '<UAVariable NodeId="i=99" BrowseName="B"/></UANodeSet>',
            encoding="utf-8",
        )
        innocent.write_text(
            '<UANodeSet xmlns="http://opcfoundation.org/UA/">'
            '<UAObject NodeId="i=100" BrowseName="C"/></UANodeSet>',
            encoding="utf-8",
        )
        model = load_nodesets([first, second, innocent], tolerate_duplicates=True)
        assert len(model.conflicts) == 1
        conflict = model.conflicts[0]
        assert conflict.paths == (str(first), str(second))
        assert str(innocent) not in conflict.describe()

    def test_tolerant_merge_keeps_the_first_declaration_and_records_the_conflict(
        self, tmp_path
    ) -> None:
        # A release review needs the diff, not a crash: the conflict becomes a finding later.
        first = tmp_path / "a.xml"
        second = tmp_path / "b.xml"
        first.write_text(
            '<UANodeSet xmlns="http://opcfoundation.org/UA/">'
            '<UAObject NodeId="i=99" BrowseName="A"/></UANodeSet>',
            encoding="utf-8",
        )
        second.write_text(
            '<UANodeSet xmlns="http://opcfoundation.org/UA/">'
            '<UAVariable NodeId="i=99" BrowseName="B"/></UANodeSet>',
            encoding="utf-8",
        )
        model = load_nodesets([first, second], tolerate_duplicates=True)
        assert model.node_count() == 1
        assert model.nodes[ExpandedNodeId.parse("i=99")].browse_name.name == "A"
        assert len(model.conflicts) == 1
        conflict = model.conflicts[0]
        assert conflict.kept_source == str(first)
        assert conflict.rejected_source == str(second)
        assert "kept" in conflict.describe() and "dropped" in conflict.describe()

    def test_sources_record_which_document_contributed_a_node(self, ua_base, machinery) -> None:
        model = load_nodesets([ua_base, machinery])
        assert model.sources[ExpandedNodeId.parse("i=1002")] == str(ua_base)
        job_type = model.find_by_browse_name("JobDataType")[0]
        assert model.sources[job_type.node_id] == str(machinery)

    def test_unresolved_targets_are_reported(self, tmp_path) -> None:
        doc = tmp_path / "a.xml"
        doc.write_text(
            '<UANodeSet xmlns="http://opcfoundation.org/UA/">'
            '<UAObject NodeId="i=1" BrowseName="A">'
            '<References ReferenceType="HasComponent" TypeId="s=HasComponent" TargetId="i=404"/>'
            "</UAObject></UANodeSet>",
            encoding="utf-8",
        )
        model = load_nodesets([doc])
        assert model.unresolved_targets() == [(ExpandedNodeId.parse("i=1"), ExpandedNodeId.parse("i=404"))]

    def test_referenced_namespaces_detects_cross_namespace_use(self, merged_model) -> None:
        assert "http://opcfoundation.org/UA/Machinery/" in merged_model.referenced_namespaces()

    def test_lookup_helpers(self, merged_model) -> None:
        assert len(merged_model.find_by_browse_name("MachineType")) == 1
        assert len(merged_model.find_by_display_name("BaseDataType")) == 1
        assert merged_model.get(ExpandedNodeId.parse("i=1002")) is not None
        assert merged_model.get(ExpandedNodeId.parse("i=999999")) is None

    def test_nodes_in_namespace(self, merged_model) -> None:
        machinery_nodes = list(merged_model.nodes_in_namespace("http://opcfoundation.org/UA/Machinery/"))
        assert {n.browse_name.name for n in machinery_nodes} >= {"JobDataType", "StartJob"}


class TestBuildModel:
    def test_build_from_domain_objects(self) -> None:
        model = build_model(
            [make_object("A", 1), make_object_type("T", 2)],
            namespaces={UA_NS: Namespace(uri=UA_NS, index_in_document=0)},
        )
        assert isinstance(model, SemanticModel)
        assert model.node_count() == 2

    def test_add_rejects_conflict(self) -> None:
        model = build_model([make_object("A", 1)])
        with pytest.raises(DuplicateNodeError):
            model.add(make_object_type("T", 1), "extra.xml")

    def test_add_tolerates_conflict_when_asked(self) -> None:
        model = build_model([make_object("A", 1)])
        model.add(make_object_type("T", 1), "extra.xml", tolerate=True)
        assert model.node_count() == 1
        assert [conflict.rejected_source for conflict in model.conflicts] == ["extra.xml"]

    def test_identity_never_uses_namespace_index(self) -> None:
        node_a = make_object("A", 5)
        same_id = ExpandedNodeId(identifier=NodeId.numeric_id(5), namespace_uri=UA_NS)
        assert node_a.node_id == same_id
        assert node_a.node_id != ExpandedNodeId(identifier=NodeId.numeric_id(5), namespace_uri=OTHER)
        assert QualifiedName(name="A", namespace_index=3).namespace_index == 3
