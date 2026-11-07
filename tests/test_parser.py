"""Parser tests: XML is read once and converted to the semantic model."""

from __future__ import annotations

import pathlib

import pytest

from nodeset_linker.models.identifiers import ExpandedNodeId
from nodeset_linker.models.node_class import ModellingRule, NodeClass, NodeRole
from nodeset_linker.parser.nodeset import NodeSetParser, parse_nodeset
from nodeset_linker.utils.errors import ParseError


class TestParsing:
    def test_parses_nodes_and_metadata(self, ua_base) -> None:
        parser = NodeSetParser.parse_file(ua_base)
        assert parser.nodes
        info = parser.document_info()
        assert info.namespaces[0].uri == "http://opcfoundation.org/UA/"

    def test_namespace_indexes_resolve_to_uris(self, machinery) -> None:
        nodes, _ = parse_nodeset(machinery)
        job = next(n for n in nodes if n.browse_name.name == "JobDataType")
        assert job.node_id.namespace_uri == "http://opcfoundation.org/UA/Machinery/"
        base = job.reference_of("HasSubtype")[0]
        assert base.namespace_uri == "http://opcfoundation.org/UA/"

    def test_ua_namespace_is_index_zero_without_being_listed(self, machinery) -> None:
        _, info = parse_nodeset(machinery)
        # A model document lists only its own namespace; index 0 is the OPC UA namespace.
        assert [ns.index_in_document for ns in info.namespaces] == [0, 1]
        assert info.namespaces[1].uri == "http://opcfoundation.org/UA/Machinery/"

    def test_alias_and_metadata_are_captured(self, machinery) -> None:
        _, info = parse_nodeset(machinery)
        namespace = next(ns for ns in info.namespaces if ns.alias == "Machinery")
        assert namespace.version == "1.0.1"
        assert namespace.publication_date == "2024-01-01T00:00:00Z"
        assert namespace.xml_schema_uri == "http://opcfoundation.org/UA/Machinery/Types.xsd"

    def test_models_and_required_models(self, machinery) -> None:
        _, info = parse_nodeset(machinery)
        assert [model.namespace_uri for model in info.models] == [
            "http://opcfoundation.org/UA/Machinery/"
        ]
        assert info.models[0].required_models == ("http://opcfoundation.org/UA/",)
        assert info.models[0].model_version == "1.0.1"

    def test_aliases_resolve_datatype_names(self, machinery) -> None:
        parser = NodeSetParser.parse_file(machinery)
        arguments = next(n for n in parser.nodes if n.browse_name.name == "InputArguments")
        assert arguments.data_type == ExpandedNodeId.parse("i=24136")

    def test_inverted_reference_keeps_direction_flag(self, ua_base) -> None:
        nodes, _ = parse_nodeset(ua_base)
        variable = next(n for n in nodes if n.browse_name.name == "MachineName")
        ref = next(ref for ref in variable.references if ref.type_name == "HasProperty")
        assert ref.is_forward is False
        assert ref.target == ExpandedNodeId.parse("i=1001")

    def test_references_carry_both_type_name_and_id(self, machinery) -> None:
        nodes, _ = parse_nodeset(machinery)
        job = next(n for n in nodes if n.browse_name.name == "JobDataType")
        encoding = next(ref for ref in job.references if ref.type_name == "HasEncoding")
        assert encoding.reference_type == ExpandedNodeId.parse("i=38")

    def test_modelling_rules(self, ua_base) -> None:
        nodes, _ = parse_nodeset(ua_base)
        optional = next(n for n in nodes if n.browse_name.name == "Diagnostics")
        mandatory = next(n for n in nodes if n.browse_name.name == "CurrentJob")
        assert optional.modelling_rule is ModellingRule.OPTIONAL
        assert mandatory.modelling_rule is ModellingRule.MANDATORY

    def test_variable_datatype_attribute(self, ua_base) -> None:
        nodes, _ = parse_nodeset(ua_base)
        variable = next(n for n in nodes if n.browse_name.name == "CurrentJob")
        assert variable.data_type == ExpandedNodeId.parse(
            "nsu=http://opcfoundation.org/UA/Machinery/;i=2001"
        )

    def test_structure_definition_fields(self, machinery) -> None:
        nodes, _ = parse_nodeset(machinery)
        structure = next(n for n in nodes if n.browse_name.name == "JobDataType")
        assert structure.role is NodeRole.STRUCTURE
        assert structure.definition is not None
        assert structure.definition.name == "JobDataType"
        assert [field.name for field in structure.definition.fields] == ["JobId", "State", "Progress"]
        assert structure.definition.fields[2].is_optional

    def test_enum_definition(self, machinery) -> None:
        nodes, _ = parse_nodeset(machinery)
        enum = next(n for n in nodes if n.browse_name.name == "JobStateEnum")
        assert enum.role is NodeRole.ENUM
        assert enum.definition is not None
        assert [value.name for value in enum.definition.enum_values] == ["Idle", "Running", "Done"]

    def test_encoding_nodes_flagged(self, machinery) -> None:
        nodes, _ = parse_nodeset(machinery)
        encodings = [n for n in nodes if n.role is NodeRole.ENCODING]
        assert {n.browse_name.name for n in encodings} == {"JobDataTypeBinary", "JobDataTypeXml"}

    def test_interface_is_an_object_type_in_schema_documents(self, machinery) -> None:
        # The UANodeSet schema has no UAInterface element; interfaces are object types
        # marked abstract, referenced through HasInterface.
        nodes, _ = parse_nodeset(machinery)
        interface = next(n for n in nodes if n.browse_name.name == "IMachineStatus")
        assert interface.node_class is NodeClass.OBJECT_TYPE
        controller = next(n for n in nodes if n.browse_name.name == "MSSControllerInstance")
        assert controller.reference_of("HasInterface") == (interface.node_id,)

    def test_namespace_metadata_object_is_recognized(self, machinery) -> None:
        nodes, _ = parse_nodeset(machinery)
        metadata = next(n for n in nodes if n.role is NodeRole.NAMESPACE_METADATA)
        assert metadata.symbolic_name == "OPCUAMachineryNamespaceMetadata"
        subset = next(n for n in nodes if n.browse_name.name == "IsNamespaceSubset")
        assert subset.value_xml is not None and "false" in subset.value_xml

    def test_argument_role(self, machinery) -> None:
        nodes, _ = parse_nodeset(machinery)
        args = [n for n in nodes if n.role is NodeRole.ARGUMENT]
        assert {n.browse_name.name for n in args} == {"InputArguments", "OutputArguments"}

    def test_rejects_non_nodeset_root(self, tmp_path) -> None:
        bad = tmp_path / "bad.xml"
        bad.write_text("<Other/>", encoding="utf-8")
        with pytest.raises(ParseError, match="root element"):
            parse_nodeset(bad)

    def test_rejects_malformed_xml(self, tmp_path) -> None:
        bad = tmp_path / "bad.xml"
        bad.write_text("<UANodeSet><broken", encoding="utf-8")
        with pytest.raises(ParseError):
            parse_nodeset(bad)

    def test_reference_without_typeid_is_rejected(self, tmp_path) -> None:
        bad = tmp_path / "bad.xml"
        bad.write_text(
            '<UANodeSet xmlns="http://opcfoundation.org/UA/">'
            '<UAObject NodeId="i=1" BrowseName="A">'
            '<References TargetId="i=2"/></UAObject></UANodeSet>',
            encoding="utf-8",
        )
        with pytest.raises(ParseError, match="TypeId"):
            parse_nodeset(bad)

    def test_model_without_uri_is_rejected(self, tmp_path) -> None:
        bad = tmp_path / "bad.xml"
        bad.write_text(
            '<UANodeSet xmlns="http://opcfoundation.org/UA/"><Models><Model ModelName="M"/></Models></UANodeSet>',
            encoding="utf-8",
        )
        with pytest.raises(ParseError, match="NamespaceUri"):
            parse_nodeset(bad)


class TestStrictIdentifiers:
    """An identifier a document states explicitly has to resolve — nothing is guessed."""

    def write(self, tmp_path, body: str) -> pathlib.Path:  # type: ignore[no-untyped-def]
        path = tmp_path / "strict.xml"
        path.write_text(
            '<UANodeSet xmlns="http://opcfoundation.org/UA/2011/03/UANodeSet.xsd">'
            f"{body}</UANodeSet>",
            encoding="utf-8",
        )
        return path

    def test_unknown_variable_datatype_name_is_rejected(self, tmp_path) -> None:
        # A fallback to BaseDataType would retype the variable and make two different
        # unknown datatypes compare as identical.
        path = self.write(tmp_path, '<UAVariable NodeId="i=1" BrowseName="Tag" DataType="Temperature"/>')
        with pytest.raises(ParseError, match="Temperature"):
            parse_nodeset(path)

    def test_unknown_field_datatype_is_rejected(self, tmp_path) -> None:
        path = self.write(
            tmp_path,
            '<UADataType NodeId="i=2" BrowseName="Pair"><Definition Name="Pair">'
            '<Field Name="Value" DataType="Temperature"/></Definition></UADataType>',
        )
        with pytest.raises(ParseError, match="field 'Value'"):
            parse_nodeset(path)

    def test_unknown_union_alternative_datatype_is_rejected(self, tmp_path) -> None:
        path = self.write(
            tmp_path,
            '<UADataType NodeId="i=2" BrowseName="Choice"><Definition Name="Choice" IsUnion="true">'
            '<Field Name="Number" DataType="Int32"/><Field Name="Text" DataType="Temperature"/>'
            "</Definition></UADataType>",
        )
        with pytest.raises(ParseError, match="alternative 'Text'"):
            parse_nodeset(path)

    def test_field_without_datatype_is_the_schema_default(self, tmp_path) -> None:
        # Declaring no DataType is not a typo: the schema default is BaseDataType (i=24).
        path = self.write(
            tmp_path,
            '<UADataType NodeId="i=2" BrowseName="Pair"><Definition Name="Pair">'
            '<Field Name="Value"/></Definition></UADataType>',
        )
        nodes, _ = parse_nodeset(path)
        assert nodes[0].definition is not None
        assert nodes[0].definition.fields[0].data_type == ExpandedNodeId.parse("i=24")

    def test_unknown_parent_node_id_is_rejected(self, tmp_path) -> None:
        path = self.write(
            tmp_path, '<UAVariable NodeId="i=1" BrowseName="Tag" ParentNodeId="Folder"/>'
        )
        with pytest.raises(ParseError, match="ParentNodeId"):
            parse_nodeset(path)

    def test_unknown_value_identifier_is_rejected(self, tmp_path) -> None:
        # The value names the datatype it is encoded with; skipping it would drop the
        # dependency and let a reduction remove the datatype the value still names.
        path = self.write(
            tmp_path,
            '<UAVariable NodeId="i=1" BrowseName="Tag"><Value><Any><TypeId><Identifier>'
            "Temperature</Identifier></TypeId></Any></Value></UAVariable>",
        )
        with pytest.raises(ParseError, match="Temperature"):
            parse_nodeset(path)

    def test_ua_namespace_keeps_index_zero_when_listed_later(self, tmp_path) -> None:
        path = self.write(
            tmp_path,
            "<NamespaceUris>"
            '<Uri>http://example.org/UA/X/</Uri>'
            '<Uri>http://opcfoundation.org/UA/</Uri>'
            "</NamespaceUris>"
            '<UAVariable NodeId="ns=1;i=1" BrowseName="Tag"/>',
        )
        _, info = parse_nodeset(path)
        indexes = {ns.uri: ns.index_in_document for ns in info.namespaces}
        assert indexes == {"http://example.org/UA/X/": 1, "http://opcfoundation.org/UA/": 0}

    def test_standard_encoding_objects_are_encodings(self, tmp_path) -> None:
        path = self.write(
            tmp_path,
            '<UADataType NodeId="i=2" BrowseName="Pair"/><UAObject NodeId="i=3" BrowseName="Default Binary"'
            ' SymbolicName="DefaultBinary"/><UADataType NodeId="i=4" BrowseName="Default XML"/>'
            '<UAObject NodeId="i=5" BrowseName="Default JSON"/>',
        )
        nodes, _ = parse_nodeset(path)
        assert {n.node_id.identifier.value for n in nodes if n.role is NodeRole.ENCODING} == {
            "3",
            "4",
            "5",
        }

    def test_an_object_naming_a_format_is_not_an_encoding(self, tmp_path) -> None:
        # "OPC Binary" is the schema dictionary of the base specification, not the encoding
        # of a datatype; a suffix rule would make it an undroppable hard dependency.
        path = self.write(
            tmp_path,
            '<UAObject NodeId="i=3" BrowseName="OPC Binary" SymbolicName="OPCBinarySchema_TypeSystem"/>'
            '<UAObject NodeId="i=4" BrowseName="PayloadXml"/>',
        )
        nodes, _ = parse_nodeset(path)
        assert {n.role for n in nodes} == {NodeRole.INSTANCE}


class TestLegacySpelling:
    """Documents written before the schema namespace are still read, never written."""

    def test_legacy_namespace_and_alias(self, legacy) -> None:
        _, info = parse_nodeset(legacy)
        namespace = next(ns for ns in info.namespaces if ns.uri == "http://example.org/UA/Legacy/")
        assert namespace.alias == "Legacy"
        assert namespace.index_in_document == 1

    def test_legacy_version_from_namespace_metadata_element(self, legacy) -> None:
        _, info = parse_nodeset(legacy)
        namespace = next(ns for ns in info.namespaces if ns.uri == "http://example.org/UA/Legacy/")
        assert namespace.version == "0.9.0"

    def test_legacy_attribute_form_references(self, legacy) -> None:
        nodes, _ = parse_nodeset(legacy)
        obj = next(n for n in nodes if n.browse_name.name == "LegacyObject")
        ref = next(ref for ref in obj.references if ref.type_name == "HasProperty")
        assert ref.reference_type == ExpandedNodeId.parse("i=46")

    def test_legacy_modelling_rule_element(self, legacy) -> None:
        nodes, _ = parse_nodeset(legacy)
        obj = next(n for n in nodes if n.browse_name.name == "LegacyObject")
        assert obj.modelling_rule is ModellingRule.OPTIONAL

    def test_legacy_definition_type_and_interface_element(self, legacy) -> None:
        nodes, _ = parse_nodeset(legacy)
        enum = next(n for n in nodes if n.browse_name.name == "LegacyEnum")
        assert enum.role is NodeRole.ENUM
        assert [value.name for value in enum.definition.enum_values] == ["Off", "On"]
        interface = next(n for n in nodes if n.browse_name.name == "ILegacy")
        assert interface.node_class is NodeClass.INTERFACE
