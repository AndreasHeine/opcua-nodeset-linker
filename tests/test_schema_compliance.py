"""Schema compliance: exported documents must validate against the official UANodeSet.xsd.

The vendored schema is the OPC Foundation one (MIT), copied into ``tests/data/schema``.
"Spec compliant" is only meaningful if the file an importer checks against accepts our
output, so this test is the contract for the exporter.
"""

from __future__ import annotations

from pathlib import Path

from lxml.etree import XMLSchema
from lxml.etree import parse as xml_parse
from tests.conftest import DATA, Machinery_NS, node_id

from nodeset_linker.exporter.nodeset import export
from nodeset_linker.repository.model import load_nodesets

FULL = {n.node_id for n in load_nodesets([DATA / "ua_base.xml", DATA / "machinery.xml"]).iter_nodes()}
REDUCED = FULL - {node_id(2500, Machinery_NS)}


def schema(path: Path) -> XMLSchema:
    return XMLSchema(xml_parse(str(path)))


def validate(path: Path, xsd: XMLSchema) -> str | None:
    """Return the validation error text, or ``None`` when the document is valid."""
    document = xml_parse(str(path))
    if xsd.validate(document):
        return None
    return str(xsd.error_log)


class TestSchemaCompliance:
    def test_full_export_is_schema_valid(self, tmp_path, nodeset_schema) -> None:
        model = load_nodesets([DATA / "ua_base.xml", DATA / "machinery.xml"])
        out = export(model, set(FULL), tmp_path / "full.xml")
        assert validate(out, schema(nodeset_schema)) is None

    def test_reduced_export_is_schema_valid(self, tmp_path, nodeset_schema) -> None:
        model = load_nodesets([DATA / "ua_base.xml", DATA / "machinery.xml"])
        out = export(model, set(REDUCED), tmp_path / "reduced.xml")
        assert validate(out, schema(nodeset_schema)) is None

    def test_source_fixtures_are_schema_valid(self, nodeset_schema) -> None:
        """The fixtures imitate real NodeSet files, so they must pass too."""
        xsd = schema(nodeset_schema)
        assert validate(DATA / "ua_base.xml", xsd) is None
        assert validate(DATA / "machinery.xml", xsd) is None

    def test_legacy_spelling_is_rejected_by_the_schema(self, legacy, nodeset_schema) -> None:
        """The pre-schema spelling is readable but not valid — that is the whole point."""
        assert validate(legacy, schema(nodeset_schema)) is not None

    def test_custom_uri_attributes_are_rejected(self, tmp_path, nodeset_schema) -> None:
        bad = tmp_path / "bad.xml"
        bad.write_text(
            '<UANodeSet xmlns="http://opcfoundation.org/UA/2011/03/UANodeSet.xsd">'
            "<NamespaceUris>"
            '<Uri UriIndex="1" Alias="M">http://example.org/UA/M/</Uri>'
            "</NamespaceUris>"
            '<UADataType NodeId="ns=1;i=1" BrowseName="1:M"/>'
            "</UANodeSet>",
            encoding="utf-8",
        )
        assert validate(bad, schema(nodeset_schema)) is not None

    def test_attribute_form_references_are_rejected(self, tmp_path, nodeset_schema) -> None:
        bad = tmp_path / "bad.xml"
        bad.write_text(
            '<UANodeSet xmlns="http://opcfoundation.org/UA/2011/03/UANodeSet.xsd">'
            '<UAObjectType NodeId="i=1" BrowseName="A">'
            '<References ReferenceType="HasComponent" TargetId="i=2"/></UAObjectType>'
            "</UANodeSet>",
            encoding="utf-8",
        )
        assert validate(bad, schema(nodeset_schema)) is not None

    def test_definition_type_attribute_is_rejected(self, tmp_path, nodeset_schema) -> None:
        bad = tmp_path / "bad.xml"
        bad.write_text(
            '<UANodeSet xmlns="http://opcfoundation.org/UA/2011/03/UANodeSet.xsd">'
            '<UADataType NodeId="i=1" BrowseName="A">'
            '<Definition DefinitionType="Structure"/></UADataType>'
            "</UANodeSet>",
            encoding="utf-8",
        )
        assert validate(bad, schema(nodeset_schema)) is not None
