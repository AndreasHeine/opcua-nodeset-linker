"""Semantic-preservation regressions against the OPC UA NodeSet specification.

Each test covers one class of information that must survive parse -> link -> export
unchanged: qualified browse names, datatype-definition field metadata, server-qualified
identifiers, and namespace indexes that the document never declared.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from lxml.etree import XMLSchema, parse
from tests.conftest import DATA, NODESET_SCHEMA, UA_NS, Machinery_NS

from nodeset_linker.exporter.nodeset import ExportError, NodeSetExporter, export
from nodeset_linker.models.identifiers import ExpandedNodeId, NodeId
from nodeset_linker.models.node import FieldMetadata
from nodeset_linker.parser.nodeset import ParseError
from nodeset_linker.repository.model import load_nodesets

ALPHA_NS = "http://opcfoundation.org/UA/Alpha/"
BETA_NS = "http://opcfoundation.org/UA/Beta/"
HEADER = '<?xml version="1.0" encoding="utf-8"?>\n<UANodeSet xmlns="http://opcfoundation.org/UA/2011/03/UANodeSet.xsd">'

BROWSE_FIXTURE = f"""{HEADER}
  <NamespaceUris>
    <Uri>{ALPHA_NS}</Uri>
    <Uri>{BETA_NS}</Uri>
  </NamespaceUris>
  <Aliases>
    <Alias Alias="HasSubtype">i=45</Alias>
  </Aliases>
  <UADataType NodeId="ns=1;i=100" BrowseName="2:BetaName" SymbolicName="BetaName">
    <DisplayName>BetaName</DisplayName>
    <References>
      <Reference ReferenceType="HasSubtype" IsForward="false">i=24</Reference>
    </References>
  </UADataType>
</UANodeSet>
"""

FIELD_FIXTURE = f"""{HEADER}
  <NamespaceUris>
    <Uri>{Machinery_NS}</Uri>
  </NamespaceUris>
  <Aliases>
    <Alias Alias="HasSubtype">i=45</Alias>
  </Aliases>
  <UADataType NodeId="ns=1;i=300" BrowseName="1:SensorReading" SymbolicName="SensorReading">
    <DisplayName>SensorReading</DisplayName>
    <References>
      <Reference ReferenceType="HasSubtype" IsForward="false">i=22</Reference>
    </References>
    <Definition Name="SensorReading">
      <Field Name="Timestamp" DataType="i=21" SymbolicName="Timestamp"/>
      <Field Name="Samples" DataType="i=26" ValueRank="1" ArrayDimensions="3,2"/>
      <Field Name="Label" DataType="i=21" MaxStringLength="64"/>
      <Field Name="Derived" DataType="i=22" AllowSubTypes="true" IsOptional="true">
        <DisplayName>Derived</DisplayName>
        <Description>a sub-structure an implementer may leave out</Description>
      </Field>
    </Definition>
  </UADataType>
  <UADataType NodeId="ns=1;i=310" BrowseName="1:LevelUnion" SymbolicName="LevelUnion">
    <DisplayName>LevelUnion</DisplayName>
    <References>
      <Reference ReferenceType="HasSubtype" IsForward="false">i=22</Reference>
    </References>
    <Definition Name="LevelUnion" IsUnion="true">
      <Field Name="Low" DataType="i=26">
        <DisplayName>Low</DisplayName>
        <Description>low level</Description>
      </Field>
      <Field Name="High" DataType="i=21"/>
    </Definition>
  </UADataType>
  <UADataType NodeId="ns=1;i=320" BrowseName="1:AlarmState" SymbolicName="AlarmState">
    <DisplayName>AlarmState</DisplayName>
    <References>
      <Reference ReferenceType="HasSubtype" IsForward="false">i=29</Reference>
    </References>
    <Definition Name="AlarmState">
      <Field Name="Idle" Value="0"/>
      <Field Name="Running" Value="1" SymbolicName="Running">
        <DisplayName>Running</DisplayName>
        <Description>the equipment runs</Description>
      </Field>
      <Field Name="Fault" Value="2" MaxStringLength="10"/>
    </Definition>
  </UADataType>
  <UADataType NodeId="ns=1;i=330" BrowseName="1:EquipmentMode" SymbolicName="EquipmentMode">
    <DisplayName>EquipmentMode</DisplayName>
    <References>
      <Reference ReferenceType="HasSubtype" IsForward="false">i=29</Reference>
    </References>
    <Definition Name="EquipmentMode" IsOptionSet="true">
      <Field Name="None" Value="0"/>
      <Field Name="Heating" Value="1">
        <DisplayName>Heating</DisplayName>
        <Description>heating is enabled</Description>
      </Field>
      <Field Name="Cooling" Value="2" SymbolicName="Cooling"/>
    </Definition>
  </UADataType>
</UANodeSet>
"""

SERVER_FIXTURE = f"""{HEADER}
  <NamespaceUris>
    <Uri>{Machinery_NS}</Uri>
  </NamespaceUris>
  <ServerUris>
    <Uri>http://example.com/svc-a/</Uri>
    <Uri>http://example.com/svc-b/</Uri>
  </ServerUris>
  <Aliases>
    <Alias Alias="HasComponent">i=47</Alias>
  </Aliases>
  <UAVariable NodeId="srv=2;ns=1;i=200" BrowseName="1:RemoteTemp" DataType="i=26">
    <DisplayName>RemoteTemp</DisplayName>
  </UAVariable>
  <UAVariable NodeId="ns=1;i=201" BrowseName="1:LocalTemp" DataType="i=26">
    <DisplayName>LocalTemp</DisplayName>
    <References>
      <Reference ReferenceType="HasComponent">srv=2;ns=1;i=200</Reference>
    </References>
  </UAVariable>
  <UAVariable NodeId="srv=1;ns=1;i=202" BrowseName="1:OtherTemp" DataType="i=26">
    <DisplayName>OtherTemp</DisplayName>
  </UAVariable>
</UANodeSet>
"""

BAD_NS_FIXTURE = f"""{HEADER}
  <NamespaceUris>
    <Uri>{Machinery_NS}</Uri>
  </NamespaceUris>
  <UAVariable NodeId="ns=4;i=200" BrowseName="1:Temp" DataType="i=26">
    <DisplayName>Temp</DisplayName>
  </UAVariable>
</UANodeSet>
"""

BAD_BROWSE_FIXTURE = f"""{HEADER}
  <NamespaceUris>
    <Uri>{Machinery_NS}</Uri>
  </NamespaceUris>
  <UAVariable NodeId="ns=1;i=200" BrowseName="3:Temp" DataType="i=26">
    <DisplayName>Temp</DisplayName>
  </UAVariable>
</UANodeSet>
"""

BAD_SERVER_FIXTURE = f"""{HEADER}
  <NamespaceUris>
    <Uri>{Machinery_NS}</Uri>
  </NamespaceUris>
  <ServerUris>
    <Uri>http://example.com/svc-a/</Uri>
  </ServerUris>
  <UAVariable NodeId="srv=3;ns=1;i=200" BrowseName="1:Temp" DataType="i=26">
    <DisplayName>Temp</DisplayName>
  </UAVariable>
</UANodeSet>
"""

BAD_INDEX_FIXTURE = f"""{HEADER}
  <NamespaceUris>
    <Uri>{Machinery_NS}</Uri>
  </NamespaceUris>
  <UAVariable NodeId="ns=loud;i=200" BrowseName="1:Temp" DataType="i=26">
    <DisplayName>Temp</DisplayName>
  </UAVariable>
</UANodeSet>
"""


BROWSE_ONLY_FIXTURE = f"""{HEADER}
  <NamespaceUris>
    <Uri>{ALPHA_NS}</Uri>
    <Uri>{BETA_NS}</Uri>
  </NamespaceUris>
  <UAVariable NodeId="ns=1;i=400" BrowseName="2:DetachedName">
    <DisplayName>DetachedName</DisplayName>
  </UAVariable>
</UANodeSet>
"""


def write(tmp_path: Path, name: str, xml: str) -> Path:
    path = tmp_path / name
    path.write_text(xml, encoding="utf-8")
    return path


def write_subset(model, keep: set[ExpandedNodeId], tmp_path: Path, *, subsets: bool = False) -> Path:
    """Write the retained subset without running the semantic checks (checks are tested elsewhere)."""
    path = tmp_path / "out.xml"
    path.write_bytes(NodeSetExporter(model, keep, mark_namespace_subsets=subsets).build_document())
    return path


def xsd_valid(path: Path) -> bool:
    return XMLSchema(parse(str(NODESET_SCHEMA))).validate(parse(str(path)))


def node_by_name(model, name: str):
    for node in model.iter_nodes():
        if node.browse_name.name == name:
            return node
    raise AssertionError(f"no node named {name}")


class TestQualifiedBrowseNames:
    def test_parse_resolves_the_name_namespace_from_the_document(self, tmp_path) -> None:
        model = load_nodesets([str(DATA / "ua_base.xml"), str(write(tmp_path, "browse.xml", BROWSE_FIXTURE))])
        node = node_by_name(model, "BetaName")
        assert node.node_id.namespace == ALPHA_NS
        assert node.browse_name.name == "BetaName"
        assert node.browse_name.namespace_uri == BETA_NS

    def test_export_reindexes_the_name_not_the_node_id(self, tmp_path) -> None:
        model = load_nodesets([str(DATA / "ua_base.xml"), str(write(tmp_path, "browse.xml", BROWSE_FIXTURE))])
        keep = {node_by_name(model, "BetaName").node_id, ExpandedNodeId(identifier=NodeId.numeric_id(24), namespace_uri=UA_NS)}
        path = write_subset(model, keep, tmp_path)
        assert xsd_valid(path)

        xml = path.read_text(encoding="utf-8")
        element = next(el for el in parse(str(path)).getroot() if el.get("SymbolicName") == "BetaName")
        assert element.get("NodeId") == "ns=1;i=100"  # Alpha got index 1
        assert element.get("BrowseName") == "2:BetaName"  # Beta got index 2, not the node's 1
        assert "<Uri>" + BETA_NS + "</Uri>" in xml

    def test_round_trip_keeps_the_name_namespace_after_reindexing(self, tmp_path) -> None:
        model = load_nodesets([str(DATA / "ua_base.xml"), str(write(tmp_path, "browse.xml", BROWSE_FIXTURE))])
        keep = {node_by_name(model, "BetaName").node_id, ExpandedNodeId(identifier=NodeId.numeric_id(24), namespace_uri=UA_NS)}
        reloaded = load_nodesets([str(write_subset(model, keep, tmp_path))])
        node = node_by_name(reloaded, "BetaName")
        assert node.node_id.namespace == ALPHA_NS
        assert node.browse_name.namespace_uri == BETA_NS
        assert node.browse_name.is_qualified

    def test_export_indexes_a_namespace_only_used_by_a_browse_name(self, tmp_path) -> None:
        """A name qualified into a namespace none of the NodeIds uses must still get an index."""
        model = load_nodesets([str(write(tmp_path, "only.xml", BROWSE_ONLY_FIXTURE))])
        path = write_subset(model, {node_by_name(model, "DetachedName").node_id}, tmp_path)
        assert xsd_valid(path)
        text = path.read_text(encoding="utf-8")
        assert "<Uri>" + ALPHA_NS + "</Uri>" in text
        assert "<Uri>" + BETA_NS + "</Uri>" in text
        assert 'NodeId="ns=1;i=400"' in text  # index 0 stays the OPC UA namespace even when unused
        assert 'BrowseName="2:DetachedName"' in text  # Beta keeps its own index, Alpha is 1
        assert "<NamespaceUris>" in text


class TestDataTypeFieldMetadata:
    @pytest.fixture
    def field_model(self, tmp_path):
        return load_nodesets([str(DATA / "ua_base.xml"), str(write(tmp_path, "fields.xml", FIELD_FIXTURE))])

    def test_structure_field_metadata_survives(self, field_model) -> None:
        definition = node_by_name(field_model, "SensorReading").definition
        assert definition is not None
        fields = {field.name: field for field in definition.fields}
        assert fields["Timestamp"].metadata == FieldMetadata(symbolic_name="Timestamp")
        assert fields["Samples"].metadata.value_rank == 1
        assert fields["Samples"].metadata.array_dimensions == ("3", "2")
        assert fields["Label"].metadata.max_string_length == 64
        assert fields["Derived"].metadata.allow_sub_types
        assert fields["Derived"].is_optional
        assert fields["Derived"].description == "a sub-structure an implementer may leave out"

    def test_enum_optionset_and_union_metadata_survives(self, field_model) -> None:
        enum = node_by_name(field_model, "AlarmState").definition
        values = {value.name: value for value in enum.enum_values}
        assert values["Running"].metadata.symbolic_name == "Running"
        assert values["Running"].display_name == "Running"
        assert values["Running"].description == "the equipment runs"
        assert values["Fault"].metadata.max_string_length == 10
        options = {value.name: value for value in node_by_name(field_model, "EquipmentMode").definition.option_set_values}
        assert options["Cooling"].metadata.symbolic_name == "Cooling"
        assert options["Heating"].display_name == "Heating"
        assert options["Heating"].description == "heating is enabled"
        alternatives = {a.name: a for a in node_by_name(field_model, "LevelUnion").definition.union_alternatives}
        assert alternatives["Low"].description == "low level"
        assert alternatives["Low"].display_name == "Low"

    def test_export_writes_the_attributes_back(self, field_model, tmp_path) -> None:
        keep = {node.node_id for node in field_model.iter_nodes() if node.node_id.namespace == Machinery_NS}
        keep.update(
            ExpandedNodeId(identifier=NodeId.numeric_id(number), namespace_uri=UA_NS)
            for number in (21, 22, 26, 29)
        )
        path = write_subset(field_model, keep, tmp_path)
        assert xsd_valid(path)
        xml = path.read_text(encoding="utf-8")
        assert 'ValueRank="1"' in xml
        assert 'ArrayDimensions="3,2"' in xml
        assert 'MaxStringLength="64"' in xml
        assert 'AllowSubTypes="true"' in xml
        assert 'IsOptional="true"' in xml
        assert 'SymbolicName="Timestamp"' in xml
        assert 'IsUnion="true"' in xml
        assert 'IsOptionSet="true"' in xml

    def test_round_trip_of_definition_fields(self, field_model, tmp_path) -> None:
        keep = {node.node_id for node in field_model.iter_nodes() if node.node_id.namespace == Machinery_NS}
        keep.update(
            ExpandedNodeId(identifier=NodeId.numeric_id(number), namespace_uri=UA_NS)
            for number in (21, 22, 26, 29)
        )
        reloaded = load_nodesets([str(write_subset(field_model, keep, tmp_path))])
        before = {
            name: (field.metadata, field.is_optional, field.description, field.display_name)
            for name, field in ((f.name, f) for f in node_by_name(field_model, "SensorReading").definition.fields)
        }
        after = {
            name: (field.metadata, field.is_optional, field.description, field.display_name)
            for name, field in ((f.name, f) for f in node_by_name(reloaded, "SensorReading").definition.fields)
        }
        assert before == after

    def test_defaults_are_not_written_out_again(self, field_model, tmp_path) -> None:
        keep = {node.node_id for node in field_model.iter_nodes() if node.node_id.namespace == Machinery_NS}
        keep.update(
            ExpandedNodeId(identifier=NodeId.numeric_id(number), namespace_uri=UA_NS)
            for number in (21, 22, 26, 29)
        )
        xml = write_subset(field_model, keep, tmp_path).read_text(encoding="utf-8")
        assert 'ValueRank="-1"' not in xml
        assert 'MaxStringLength="0"' not in xml
        assert 'AllowSubTypes="false"' not in xml


class TestServerQualifiedIdentifiers:
    def test_parse_resolves_srv_index_to_the_server_uri(self, tmp_path) -> None:
        model = load_nodesets([str(DATA / "ua_base.xml"), str(write(tmp_path, "srv.xml", SERVER_FIXTURE))])
        assert node_by_name(model, "RemoteTemp").node_id.server_uri == "http://example.com/svc-b/"
        assert node_by_name(model, "OtherTemp").node_id.server_uri == "http://example.com/svc-a/"
        assert node_by_name(model, "LocalTemp").references[0].target.server_uri == "http://example.com/svc-b/"

    def test_export_reindexes_the_server_table(self, tmp_path) -> None:
        """Dropping the first server must renumber the one that survived, not drop the qualification."""
        model = load_nodesets([str(DATA / "ua_base.xml"), str(write(tmp_path, "srv.xml", SERVER_FIXTURE))])
        keep = {
            node_by_name(model, "RemoteTemp").node_id,
            ExpandedNodeId(identifier=NodeId.numeric_id(26), namespace_uri=UA_NS),
        }
        path = write_subset(model, keep, tmp_path)
        assert xsd_valid(path)
        xml = path.read_text(encoding="utf-8")
        assert "<ServerUris>" in xml
        assert xml.count("<Uri>http://example.com/") == 1
        assert '<Uri>http://example.com/svc-b/</Uri>' in xml
        assert 'NodeId="srv=1;ns=1;i=200"' in xml

    def test_round_trip_keeps_the_server_uri(self, tmp_path) -> None:
        model = load_nodesets([str(DATA / "ua_base.xml"), str(write(tmp_path, "srv.xml", SERVER_FIXTURE))])
        keep = {
            node_by_name(model, "RemoteTemp").node_id,
            ExpandedNodeId(identifier=NodeId.numeric_id(26), namespace_uri=UA_NS),
        }
        reloaded = load_nodesets([str(write_subset(model, keep, tmp_path))])
        assert node_by_name(reloaded, "RemoteTemp").node_id.server_uri == "http://example.com/svc-b/"

    def test_both_servers_are_written_when_both_survive(self, tmp_path) -> None:
        model = load_nodesets([str(DATA / "ua_base.xml"), str(write(tmp_path, "srv.xml", SERVER_FIXTURE))])
        keep = {node.node_id for node in model.iter_nodes() if node.node_id.namespace == Machinery_NS}
        path = write_subset(model, keep, tmp_path)
        assert xsd_valid(path)
        xml = path.read_text(encoding="utf-8")
        assert "<Uri>http://example.com/svc-a/</Uri>" in xml
        assert "<Uri>http://example.com/svc-b/</Uri>" in xml
        assert 'NodeId="srv=1;ns=1;i=202"' in xml
        assert 'NodeId="srv=2;ns=1;i=200"' in xml
        assert ">srv=2;ns=1;i=200<" in xml

    def test_a_subset_that_drops_a_server_qualified_target_is_not_written(self, tmp_path) -> None:
        """The qualification is never quietly lost: validation blocks the export instead."""
        model = load_nodesets([str(DATA / "ua_base.xml"), str(write(tmp_path, "srv.xml", SERVER_FIXTURE))])
        keep = {
            node_by_name(model, "LocalTemp").node_id,
            ExpandedNodeId(identifier=NodeId.numeric_id(26), namespace_uri=UA_NS),
            ExpandedNodeId(identifier=NodeId.numeric_id(47), namespace_uri=UA_NS),
        }
        with pytest.raises(ExportError, match="export blocked"):
            export(model, keep, tmp_path / "out.xml")


class TestUndeclaredIndexes:
    def test_undeclared_namespace_index_is_rejected(self, tmp_path) -> None:
        with pytest.raises(ParseError) as caught:
            load_nodesets([str(write(tmp_path, "bad.xml", BAD_NS_FIXTURE))])
        assert "NamespaceUris" in str(caught.value)
        assert "ns=4" in str(caught.value)

    def test_undeclared_browse_name_index_is_rejected(self, tmp_path) -> None:
        with pytest.raises(ParseError) as caught:
            load_nodesets([str(write(tmp_path, "bad.xml", BAD_BROWSE_FIXTURE))])
        assert "BrowseName" in str(caught.value)
        assert "NamespaceUris" in str(caught.value)

    def test_undeclared_server_index_is_rejected(self, tmp_path) -> None:
        with pytest.raises(ParseError) as caught:
            load_nodesets([str(write(tmp_path, "bad.xml", BAD_SERVER_FIXTURE))])
        assert "ServerUris" in str(caught.value)

    def test_non_numeric_namespace_index_is_rejected(self, tmp_path) -> None:
        with pytest.raises(ParseError) as caught:
            load_nodesets([str(write(tmp_path, "bad.xml", BAD_INDEX_FIXTURE))])
        assert "non-numeric" in str(caught.value)

    def test_declared_indexes_still_work(self, tmp_path) -> None:
        model = load_nodesets([str(write(tmp_path, "ok.xml", SERVER_FIXTURE))])
        assert node_by_name(model, "LocalTemp").node_id.namespace == Machinery_NS
