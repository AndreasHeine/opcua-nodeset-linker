"""Unit tests for identifiers and domain models."""

from __future__ import annotations

import pytest

from nodeset_linker.models.dependency import DependencyKind, MandatoryLevel
from nodeset_linker.models.identifiers import ExpandedNodeId, IdentifierKind, NodeId, QualifiedName
from nodeset_linker.models.node import (
    Definition,
    EnumValue,
    Reference,
    StructureField,
    UADataType,
    UANode,
    is_placeholder,
    mandatory_level_of,
)
from nodeset_linker.models.node_class import ModellingRule, NodeClass, NodeRole

UA = "http://opcfoundation.org/UA/"


class TestNodeId:
    def test_parse_numeric(self) -> None:
        assert NodeId.parse("i=1234") == NodeId(IdentifierKind.NUMERIC, "1234")
        assert NodeId.numeric_id(7).numeric == 7

    def test_parse_string_and_guid(self) -> None:
        assert NodeId.parse("s=Foo Bar").value == "Foo Bar"
        assert NodeId.parse("g=08FFCC6A-0124-5DB6-096D-B66B7BB5961C").kind is IdentifierKind.GUID

    def test_parse_rejects_garbage(self) -> None:
        with pytest.raises(ValueError):
            NodeId.parse("nope")

    def test_roundtrip_xml(self) -> None:
        for text in ("i=42", "s=Name", "b=xyz"):
            assert NodeId.parse(text).as_xml() == text


class TestExpandedNodeId:
    def test_parse_nsu(self) -> None:
        parsed = ExpandedNodeId.parse("nsu=http://opcfoundation.org/UA/Machinery/;i=1001")
        assert parsed.namespace == "http://opcfoundation.org/UA/Machinery/"
        assert parsed.identifier.value == "1001"

    def test_parse_ns_index_is_a_marker_only(self) -> None:
        parsed = ExpandedNodeId.parse("ns=2;i=5")
        assert parsed.namespace_uri == "index:2"  # never identity; repository resolves it

    def test_bare_local_part_defaults_to_ua_namespace(self) -> None:
        parsed = ExpandedNodeId.parse("i=9")
        assert parsed.namespace == UA
        assert parsed.namespace_uri == UA

    def test_is_hashable_and_immutable(self) -> None:
        ids = {ExpandedNodeId.parse("nsu=http://x/;i=1"), ExpandedNodeId.parse("nsu=http://x/;i=1")}
        assert len(ids) == 1
        with pytest.raises(Exception):
            ExpandedNodeId.parse("i=1").identifier.value = "2"


class TestQualifiedName:
    def test_indexed_form(self) -> None:
        assert QualifiedName(name="Foo", namespace_index=3).__str__() == "3:Foo"
        assert QualifiedName(name="Foo").__str__() == "Foo"


class TestNodeModel:
    def make(self, **kwargs: object) -> UANode:
        defaults = {
            "node_id": ExpandedNodeId.parse("i=1"),
            "browse_name": QualifiedName(name="N"),
            "node_class": NodeClass.OBJECT,
            "role": NodeRole.INSTANCE,
        }
        defaults.update(kwargs)
        return UANode(**defaults)  # type: ignore[arg-type]

    def test_nodes_are_immutable(self) -> None:
        node = self.make()
        with pytest.raises(Exception):
            node.browse_name = QualifiedName(name="Other")

    def test_type_definition_lookup(self) -> None:
        ref = Reference(
            reference_type=ExpandedNodeId.parse("s=HasTypeDefinition"),
            type_name="HasTypeDefinition",
            target=ExpandedNodeId.parse("i=1002"),
        )
        assert self.make(references=(ref,)).type_definition == ExpandedNodeId.parse("i=1002")

    def test_with_references_returns_new_object(self) -> None:
        node = self.make()
        ref = Reference(
            reference_type=ExpandedNodeId.parse("s=Organizes"), type_name="Organizes", target=ExpandedNodeId.parse("i=5")
        )
        updated = node.with_references((ref,))
        assert updated.references == (ref,)
        assert node.references == ()

    def test_data_type_carries_definition(self) -> None:
        data_type = UADataType(
            node_id=ExpandedNodeId.parse("nsu=http://x/;i=1"),
            browse_name=QualifiedName(name="Job", namespace_index=1),
            node_class=NodeClass.DATA_TYPE,
            role=NodeRole.ENUM,
            definition=Definition(kind="Enum", enum_values=(EnumValue(name="Idle", value=0),)),
        )
        assert data_definition_kind(data_type) == "Enum"

    def test_placeholder_detection(self) -> None:
        assert is_placeholder("MachineType_Placeholder")
        assert not is_placeholder("MachineType")

    def test_mandatory_level(self) -> None:
        assert mandatory_level_of(self.make(modelling_rule=ModellingRule.MANDATORY)) is MandatoryLevel.MANDATORY
        assert mandatory_level_of(self.make(modelling_rule=ModellingRule.OPTIONAL)) is MandatoryLevel.OPTIONAL
        # Nodes without a modelling rule are not declarations of optionality.
        assert mandatory_level_of(self.make()) is MandatoryLevel.MANDATORY

    def test_node_class_helpers(self) -> None:
        assert NodeClass.DATA_TYPE.is_type and not NodeClass.DATA_TYPE.is_instance
        assert NodeClass.VARIABLE.is_instance and not NodeClass.VARIABLE.is_type

    def test_dependency_kind_values(self) -> None:
        assert {kind.value for kind in DependencyKind} == {
            "inheritance",
            "structural",
            "datatype",
            "interface",
            "method",
            "encoding",
            "referencetype",
            "namespace",
            "optional",
        }


def data_definition_kind(node: UADataType) -> str:
    assert node.definition is not None
    return node.definition.kind


class TestStructureField:
    def test_field_defaults(self) -> None:
        field = StructureField(name="x", data_type=ExpandedNodeId.parse("i=24"))
        assert field.is_optional is False
        assert field.description is None
