"""Semantic validation tests."""

from __future__ import annotations

import pytest
from tests.conftest import (
    DATA,
    UA_NS,
    make_data_type,
    make_object,
    make_object_type,
    make_variable,
    node_id,
)

from nodeset_linker.models.identifiers import ExpandedNodeId, QualifiedName
from nodeset_linker.models.node import Reference, UADataType, UANode
from nodeset_linker.models.node_class import NodeClass, NodeRole
from nodeset_linker.repository.model import build_model
from nodeset_linker.utils.errors import ValidationError
from nodeset_linker.validator.semantic import SemanticValidator, validate_model


def ref(name: str, target: int) -> tuple[Reference, ...]:
    return (Reference(reference_type=node_id(0, UA_NS), type_name=name, target=node_id(target)),)


class TestValidation:
    def test_clean_model_passes(self) -> None:
        model = build_model([make_object_type("BaseObjectType", 1000)])
        report = validate_model(model)
        assert report.ok
        assert report.errors == []

    def test_missing_reference_target_is_an_error(self) -> None:
        model = build_model([make_object("A", 1, refs=ref("HasComponent", 999))])
        report = validate_model(model)
        assert not report.ok
        assert any(f.code == "REF_TARGET_MISSING" for f in report.errors)

    def test_type_definition_class_mismatch(self) -> None:
        variable = make_variable("V", 1, refs=ref("HasTypeDefinition", 2))
        wrong_type = make_object_type("WrongType", 2)
        report = validate_model(build_model([variable, wrong_type]))
        assert any(f.code == "TYPEDEF_CLASS_MISMATCH" for f in report.errors)

    def test_datatype_must_be_datatype(self) -> None:
        variable = make_variable("V", 1, data_type=node_id(2))
        report = validate_model(build_model([variable, make_object("NotAType", 2)]))
        assert any(f.code == "DATATYPE_CLASS_MISMATCH" for f in report.errors)

    def test_missing_datatype(self) -> None:
        variable = make_variable("V", 1, data_type=node_id(999))
        report = validate_model(build_model([variable]))
        assert any(f.code == "DATATYPE_MISSING" for f in report.errors)

    def test_structure_field_datatype_missing(self) -> None:
        from tests.conftest import structure_definition

        structure = make_data_type("S", 1, definition=structure_definition((("f", node_id(999)),)))
        report = validate_model(build_model([structure]))
        assert any(f.code == "STRUCT_FIELD_DATATYPE_MISSING" for f in report.errors)

    def test_subtype_base_missing(self) -> None:
        derived = make_object_type("Derived", 2, refs=ref("HasSubtype", 1))
        report = validate_model(build_model([derived]))
        assert any(f.code == "SUBTYPE_BASE_MISSING" for f in report.errors)

    def test_subtype_class_mismatch(self) -> None:
        base = make_data_type("BaseDataType", 49)
        derived = make_object_type("Derived", 2, refs=ref("HasSubtype", 49))
        report = validate_model(build_model([base, derived]))
        assert any(f.code == "SUBTYPE_CLASS_MISMATCH" for f in report.errors)

    def test_subtype_cycle_detected(self) -> None:
        a = make_object_type("A", 1, refs=ref("HasSubtype", 2))
        b = make_object_type("B", 2, refs=ref("HasSubtype", 1))
        report = validate_model(build_model([a, b]))
        assert any(f.code == "SUBTYPE_CYCLE" for f in report.errors)

    def test_reference_type_needs_base(self) -> None:
        custom = UANode(
            node_id=node_id(30),
            browse_name=QualifiedName(name="HasCustomThing"),
            node_class=NodeClass.REFERENCE_TYPE,
            role=NodeRole.BASE_TYPE,
        )
        report = validate_model(build_model([custom]))
        assert any(f.code == "REFTYPE_NO_BASE" for f in report.errors)

    def test_encoding_must_be_present(self) -> None:
        structure = make_data_type("JobDataType", 2001, refs=ref("HasEncoding", 2010))
        report = validate_model(build_model([structure]))
        assert any(f.code == "ENCODING_MISSING" for f in report.errors)

    def test_encoding_class_mismatch(self) -> None:
        structure = make_data_type("JobDataType", 2001, refs=ref("HasEncoding", 2010))
        not_encoding = make_object("Blob", 2010)
        report = validate_model(build_model([structure, not_encoding]))
        assert any(f.code == "ENCODING_CLASS_MISMATCH" for f in report.errors)

    def test_namespace_not_declared_is_warning(self) -> None:
        foreign = UANode(
            node_id=ExpandedNodeId.parse("nsu=http://example.com/Other/;i=5"),
            browse_name=QualifiedName(name="Foreign"),
            node_class=NodeClass.OBJECT,
            role=NodeRole.INSTANCE,
        )
        report = validate_model(build_model([foreign]))
        assert report.ok  # warnings only
        assert any(f.code == "NAMESPACE_NOT_DECLARED" for f in report.warnings)

    def test_assert_valid_raises(self) -> None:
        model = build_model([make_object("A", 1, refs=ref("HasComponent", 999))])
        with pytest.raises(ValidationError) as excinfo:
            SemanticValidator(model).assert_valid()
        assert any(f.code == "REF_TARGET_MISSING" for f in excinfo.value.findings.errors)

    def test_fixture_model_is_valid(self) -> None:
        from nodeset_linker.repository.model import load_nodesets

        report = validate_model(load_nodesets([DATA / "ua_base.xml", DATA / "machinery.xml"]))
        assert report.ok


class TestReducedModelValidation:
    def test_dropping_a_base_type_blocks_export(self) -> None:
        from nodeset_linker.exporter.nodeset import export
        from nodeset_linker.utils.errors import ExportError

        base = make_object_type("BaseObjectType", 1000)
        derived = make_object_type("MachineType", 1001, refs=ref("HasSubtype", 1000))
        model = build_model([base, derived])
        with pytest.raises(ExportError, match="export blocked"):
            export(model, {derived.node_id}, "out.xml")

    def test_dropping_an_encoding_blocks_export(self) -> None:
        from nodeset_linker.exporter.nodeset import export
        from nodeset_linker.utils.errors import ExportError

        encoding = UADataType(
            node_id=node_id(2010),
            browse_name=QualifiedName(name="JobDataTypeBinaryEncoding"),
            node_class=NodeClass.DATA_TYPE,
            role=NodeRole.ENCODING,
            is_encoding=True,
        )
        structure = make_data_type("JobDataType", 2001, refs=ref("HasEncoding", 2010))
        model = build_model([encoding, structure])
        with pytest.raises(ExportError):
            export(model, {structure.node_id}, "out.xml")
