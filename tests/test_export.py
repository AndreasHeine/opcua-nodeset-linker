"""Export tests: the reduced NodeSet is schema-valid, self-contained and deterministic."""

from __future__ import annotations

import pytest
from lxml.etree import parse as xml_parse
from tests.conftest import DATA, UA_NS, Machinery_NS, node_id

from nodeset_linker.exporter.nodeset import (
    NODESET_XML_NAMESPACE,
    NamespaceIndexAssigner,
    NodeSetExporter,
    export,
    export_closure,
)
from nodeset_linker.models.identifiers import QualifiedName
from nodeset_linker.models.namespace import Namespace
from nodeset_linker.models.namespace_metadata import TYPES_XML_NAMESPACE
from nodeset_linker.models.node import Reference, UANode, UAObject
from nodeset_linker.models.node_class import NodeClass, NodeRole
from nodeset_linker.repository.model import build_model, load_nodesets
from nodeset_linker.utils.errors import ExportError

FULL = {n.node_id for n in load_nodesets([DATA / "ua_base.xml", DATA / "machinery.xml"]).iter_nodes()}
# Dropping one machinery node reduces the namespace while its metadata object survives.
REDUCED = FULL - {node_id(2500, Machinery_NS)}


def root_of(path) -> object:  # type: ignore[no-untyped-def]
    return xml_parse(str(path)).getroot()


def node_ids(path) -> list[str]:  # type: ignore[no-untyped-def]
    root = root_of(path)
    return [element.get("NodeId") for element in root if element.tag.endswith("UAObject") or element.tag.endswith("UAVariable")]


def subset_value(path, node_id: str = "ns=1;i=15005") -> str | None:  # type: ignore[no-untyped-def]
    """The ``IsNamespaceSubset`` value of one namespace metadata property."""
    root = root_of(path)
    for element in root:
        if (
            not element.tag.endswith("UAVariable")
            or element.get("BrowseName") != "IsNamespaceSubset"
            or element.get("NodeId") != node_id
        ):
            continue
        for child in element:
            if child.tag.endswith("Value"):
                for inner in child:
                    if inner.tag == f"{{{TYPES_XML_NAMESPACE}}}Boolean":
                        return (inner.text or "").strip()
    return None


class TestExport:
    def test_export_writes_schema_namespaced_document(self, tmp_path) -> None:
        model = load_nodesets([DATA / "ua_base.xml", DATA / "machinery.xml"])
        keep = {
            n.node_id
            for n in model.iter_nodes()
            if n.browse_name.name in {"UnrelatedMachineType", "BaseMachineType", "BaseObjectType"}
        }
        out = export(model, keep, tmp_path / "reduced.xml")
        root = root_of(out)
        assert root.tag == f"{{{NODESET_XML_NAMESPACE}}}UANodeSet"
        names = {el.get("BrowseName") for el in root if el.tag.startswith(f"{{{NODESET_XML_NAMESPACE}}}UA")}
        assert {"BaseObjectType", "BaseMachineType", "UnrelatedMachineType"} <= names
        # Only namespace 0 is used, so no Uri table is written.
        assert root.find(f"{{{NODESET_XML_NAMESPACE}}}NamespaceUris") is None

    def test_namespace_indexes_assigned_deterministically(self, tmp_path) -> None:
        model = load_nodesets([DATA / "ua_base.xml", DATA / "machinery.xml"])
        keep = set(FULL)
        first = export(model, keep, tmp_path / "a.xml").read_bytes()
        second = export(model, keep, tmp_path / "b.xml").read_bytes()
        assert first == second

    def test_ua_namespace_keeps_index_zero(self) -> None:
        assigner = NamespaceIndexAssigner(
            {UA_NS: Namespace(uri=UA_NS), Machinery_NS: Namespace(uri=Machinery_NS, alias="Machinery")}
        )
        assert assigner.index_of(UA_NS) == 0
        assert assigner.index_of(Machinery_NS) == 1

    def test_ua_namespace_is_not_listed_in_uri_table(self, tmp_path) -> None:
        model = load_nodesets([DATA / "ua_base.xml", DATA / "machinery.xml"])
        out = export(model, set(FULL), tmp_path / "full.xml")
        uris = [el.text for el in root_of(out).find(f"{{{NODESET_XML_NAMESPACE}}}NamespaceUris")]
        assert uris == ["http://opcfoundation.org/UA/Machinery/"]

    def test_model_table_survives(self, tmp_path) -> None:
        model = load_nodesets([DATA / "ua_base.xml", DATA / "machinery.xml"])
        out = export(model, set(FULL), tmp_path / "full.xml")
        text = out.read_text(encoding="utf-8")
        assert 'ModelUri="http://opcfoundation.org/UA/Machinery/"' in text
        assert "PublicationDate" in text
        assert '<RequiredModel ModelUri="http://opcfoundation.org/UA/"' in text
        assert "ModelName" not in text

    def test_used_reference_types_are_aliased(self, tmp_path) -> None:
        model = load_nodesets([DATA / "ua_base.xml", DATA / "machinery.xml"])
        out = export(model, set(FULL), tmp_path / "full.xml")
        text = out.read_text(encoding="utf-8")
        assert '<Alias Alias="HasSubtype">i=45</Alias>' in text
        assert '<Alias Alias="HasModellingRule">i=37</Alias>' in text

    def test_exported_document_parses_again(self, tmp_path) -> None:
        """Self-containedness: the reduced NodeSet is loadable on its own."""
        model = load_nodesets([DATA / "ua_base.xml", DATA / "machinery.xml"])
        keep = {n.node_id for n in model.iter_nodes() if n.browse_name.name != "UnrelatedType"}
        out = export(model, keep, tmp_path / "reduced.xml")
        reloaded = load_nodesets([out])
        assert reloaded.unresolved_targets() == []
        assert reloaded.node_count() == len(keep)

    def test_datatype_and_encoding_chain_survives_export(self, tmp_path) -> None:
        model = load_nodesets([DATA / "ua_base.xml", DATA / "machinery.xml"])
        out = export(model, set(FULL), tmp_path / "full.xml")
        reloaded = load_nodesets([out])
        job = reloaded.find_by_browse_name("JobDataType")[0]
        assert job.definition is not None
        assert [field.name for field in job.definition.fields] == ["JobId", "State", "Progress"]

    def test_modelling_rule_survives_as_a_reference(self, tmp_path) -> None:
        model = load_nodesets([DATA / "ua_base.xml", DATA / "machinery.xml"])
        out = export(model, set(FULL), tmp_path / "full.xml")
        root = root_of(out)
        rules = [
            ref.text
            for element in root
            if (element.get("BrowseName") or "").endswith("MachineDiagnostics")
            for ref in element.find(f"{{{NODESET_XML_NAMESPACE}}}References")
            if ref.get("ReferenceType") == "HasModellingRule"
        ]
        assert rules == ["i=80"]

    def test_export_closure_helper(self, tmp_path) -> None:
        from nodeset_linker.graph.engine import SemanticGraph
        from nodeset_linker.resolver.closure import DependencyResolver

        model = load_nodesets([DATA / "ua_base.xml", DATA / "machinery.xml"])
        graph = SemanticGraph.from_model(model)
        closure = DependencyResolver(graph).resolve(
            [n.node_id for n in model.find_by_browse_name("JobDataType")]
        )
        out = export_closure(model, closure, tmp_path / "closure.xml")
        assert out.exists()

    def test_export_refuses_incomplete_subset(self, tmp_path) -> None:
        """A hand-picked subset that breaks an inheritance chain is never written."""
        model = load_nodesets([DATA / "ua_base.xml", DATA / "machinery.xml"])
        keep = {n.node_id for n in model.iter_nodes() if n.browse_name.name == "UnrelatedMachineType"}
        with pytest.raises(ExportError, match="export blocked"):
            export(model, keep - {node_id(1000)}, tmp_path / "broken.xml")

    def test_reference_without_type_is_not_invented_as_has_component(self, tmp_path) -> None:
        """A reference that names no type is refused, not written as `ReferenceType="i=47"`."""
        target = UAObject(
            node_id=node_id(2200, Machinery_NS),
            browse_name=QualifiedName(name="Controller"),
            node_class=NodeClass.OBJECT,
            role=NodeRole.INSTANCE,
        )
        source = UAObject(
            node_id=node_id(2500, Machinery_NS),
            browse_name=QualifiedName(name="TypelessThing"),
            node_class=NodeClass.OBJECT,
            role=NodeRole.INSTANCE,
            references=[Reference(target=target.node_id)],
        )
        model = build_model([source, target])
        out = tmp_path / "typeless.xml"
        with pytest.raises(ExportError, match="no reference type"):
            export(model, {source.node_id, target.node_id}, out, validate=False)
        assert not out.exists()

    def test_schema_forbidden_value_and_inverse_name_are_not_written(self, tmp_path) -> None:
        """``Value`` is only legal on variables and ``InverseName`` only on reference types."""
        stray = UAObject(
            node_id=node_id(31000, Machinery_NS),
            browse_name=QualifiedName(name="StrayValueObject"),
            node_class=NodeClass.OBJECT,
            role=NodeRole.INSTANCE,
            value_xml="<Value>oops</Value>",
            inverse_name="No",
        )
        reftype = UANode(
            node_id=node_id(31001, Machinery_NS),
            browse_name=QualifiedName(name="HasWorkDone"),
            node_class=NodeClass.REFERENCE_TYPE,
            role=NodeRole.BASE_TYPE,
            inverse_name="HasWorkDone_Inverse",
        )
        model = build_model([stray, reftype])
        out = export(model, {stray.node_id, reftype.node_id}, tmp_path / "gated.xml", validate=False)
        xml = out.read_text(encoding="utf-8")
        assert "<Value" not in xml
        # The legal one still survives.
        assert "InverseName" in xml

    def test_export_refuses_an_unwritable_reference_even_without_validation(self, tmp_path) -> None:
        """validate=False may skip the semantic pass, but it never writes a silently pruned file."""
        target = UAObject(
            node_id=node_id(31002, Machinery_NS),
            browse_name=QualifiedName(name="Gone"),
            node_class=NodeClass.OBJECT,
            role=NodeRole.INSTANCE,
        )
        source = UAObject(
            node_id=node_id(31003, Machinery_NS),
            browse_name=QualifiedName(name="Holder"),
            node_class=NodeClass.OBJECT,
            role=NodeRole.INSTANCE,
            references=[Reference(target=target.node_id, type_name="HasComponent")],
        )
        model = build_model([source, target])
        out = tmp_path / "dangling.xml"
        with pytest.raises(ExportError, match="cannot be written"):
            export(model, {source.node_id}, out, validate=False)
        assert not out.exists()

    def test_split_document_without_a_declared_model_has_no_models_element(self, tmp_path) -> None:
        """A namespace whose source declared no ``Model`` gets no invented entry (§OPC 10000-6)."""
        vendor_ns = "http://vendor.example.org/"
        node = UAObject(
            node_id=node_id(4200, vendor_ns),
            browse_name=QualifiedName(name="PumpType"),
            node_class=NodeClass.OBJECT,
            role=NodeRole.INSTANCE,
        )
        model = build_model([node])  # domain objects declare no models
        exporter = NodeSetExporter(model, {node.node_id}, document_namespace=vendor_ns)
        out = exporter.write(tmp_path / "vendor.xml")
        xml = out.read_text(encoding="utf-8")
        assert "<Models" not in xml
        assert "<Model " not in xml
        assert vendor_ns in xml  # the namespace itself is still declared


    def test_raw_exporter_records_the_references_it_omits(self, tmp_path) -> None:
        """The low-level exporter may prune boundary links, but it says which ones."""
        target = UAObject(
            node_id=node_id(31004, Machinery_NS),
            browse_name=QualifiedName(name="Sibling"),
            node_class=NodeClass.OBJECT,
            role=NodeRole.INSTANCE,
        )
        source = UAObject(
            node_id=node_id(31005, Machinery_NS),
            browse_name=QualifiedName(name="Holder2"),
            node_class=NodeClass.OBJECT,
            role=NodeRole.INSTANCE,
            references=[Reference(target=target.node_id, type_name="HasComponent")],
        )
        model = build_model([source, target])
        exporter = NodeSetExporter(model, {source.node_id})
        exporter.write(tmp_path / "pruned.xml")
        assert len(exporter.dropped) == 1
        assert "31004" in exporter.dropped[0]

    def test_exported_nodeids_use_new_indexes(self, tmp_path) -> None:
        model = load_nodesets([DATA / "ua_base.xml", DATA / "machinery.xml"])
        out = export(model, set(FULL), tmp_path / "full.xml")
        ids = node_ids(out)
        assert any(id_.startswith("ns=1;") for id_ in ids)
        assert any(not id_.startswith("ns=") for id_ in ids)  # namespace 0 needs no prefix

    def test_reduced_namespace_is_marked_as_subset(self, tmp_path) -> None:
        model = load_nodesets([DATA / "ua_base.xml", DATA / "machinery.xml"])
        out = export(model, set(REDUCED), tmp_path / "reduced.xml")
        assert subset_value(out) == "true"
        # Namespace 0 was not reduced, so its own flag stays false.
        assert subset_value(out, "i=15961") == "false"

    def test_unreduced_namespace_keeps_its_own_value(self, tmp_path) -> None:
        model = load_nodesets([DATA / "ua_base.xml", DATA / "machinery.xml"])
        out = export(model, set(FULL), tmp_path / "full.xml")
        assert subset_value(out) == "false"

    def test_subset_flag_is_not_invented_in_the_header(self, tmp_path) -> None:
        """Nothing outside the schema may appear: no custom element, no custom attribute."""
        model = load_nodesets([DATA / "ua_base.xml", DATA / "machinery.xml"])
        text = export(model, set(REDUCED), tmp_path / "reduced.xml").read_text(encoding="utf-8")
        assert "NamespaceMetadata Uri=" not in text
        assert "IsNamespaceSubset=" not in text
        assert "UriIndex" not in text
        assert "DefinitionType" not in text

    def test_export_can_leave_subset_marking_off(self, tmp_path) -> None:
        model = load_nodesets([DATA / "ua_base.xml", DATA / "machinery.xml"])
        out = export(model, set(REDUCED), tmp_path / "unmarked.xml", mark_namespace_subsets=False)
        assert subset_value(out) == "false"
