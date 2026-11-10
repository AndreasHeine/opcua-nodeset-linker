"""The lint pass: OPC 11030 best practices judged on one semantic model (§2 - §15).

Each test builds the smallest model that shows one rule, and asserts the finding *code* rather
than its wording: codes are the stable contract, messages are for humans.
"""

from __future__ import annotations

import json

from click.testing import CliRunner
from tests.conftest import MACHINERY, UA_NS, Machinery_NS, node_id

from nodeset_linker.cli.main import main
from nodeset_linker.diff.rules import Severity
from nodeset_linker.lint import LINT_RULES, LintReport, lint_model
from nodeset_linker.models.identifiers import ExpandedNodeId, QualifiedName
from nodeset_linker.models.namespace import DocumentInfo, Namespace
from nodeset_linker.models.node import (
    Definition,
    EnumValue,
    FieldMetadata,
    Reference,
    StructureField,
    UADataType,
    UAInterface,
    UAMethod,
    UANode,
    UAObjectType,
    UAReferenceType,
    UAVariable,
)
from nodeset_linker.models.node_class import ModellingRule, NodeClass, NodeRole
from nodeset_linker.repository.model import SemanticModel, build_model

VENDOR_NS = "http://vendor.example.com/ACME/"
NAMESPACES = {Machinery_NS: Namespace(uri=Machinery_NS), VENDOR_NS: Namespace(uri=VENDOR_NS)}
UA_STATUS_CODE = node_id(22, UA_NS)


def _id(number: int, namespace: str = Machinery_NS) -> ExpandedNodeId:
    return node_id(number, namespace)


def _ref(name: str, target: ExpandedNodeId) -> Reference:
    return Reference(target=target, type_name=name)


def components(*numbers: int) -> tuple[Reference, ...]:
    return tuple(_ref("HasComponent", _id(number)) for number in numbers)


def properties(*numbers: int) -> tuple[Reference, ...]:
    return tuple(_ref("HasProperty", _id(number)) for number in numbers)


def arguments(*numbers: int) -> tuple[Reference, ...]:
    return tuple(_ref("HasArgument", _id(number)) for number in numbers)


def subtypes(*targets: ExpandedNodeId) -> tuple[Reference, ...]:
    return tuple(_ref("HasSubtype", target) for target in targets)


def typed_as(number: int) -> tuple[Reference, ...]:
    return (_ref("HasTypeDefinition", _id(number)),)


def ua(number: int) -> ExpandedNodeId:
    return node_id(number, UA_NS)


def object_type(browse: str, number: int, refs: tuple[Reference, ...] = ()) -> UAObjectType:
    return UAObjectType(
        node_id=_id(number),
        browse_name=QualifiedName(name=browse),
        node_class=NodeClass.OBJECT_TYPE,
        role=NodeRole.TYPE_DEFINITION,
        references=refs,
    )


def vendor_object_type(browse: str, number: int) -> UAObjectType:
    return UAObjectType(
        node_id=node_id(number, VENDOR_NS),
        browse_name=QualifiedName(name=browse),
        node_class=NodeClass.OBJECT_TYPE,
        role=NodeRole.TYPE_DEFINITION,
    )


def ua_object_type(browse: str, number: int, refs: tuple[Reference, ...] = ()) -> UAObjectType:
    return UAObjectType(
        node_id=ua(number),
        browse_name=QualifiedName(name=browse),
        node_class=NodeClass.OBJECT_TYPE,
        role=NodeRole.TYPE_DEFINITION,
        references=refs,
    )


def ua_reference_type(
    browse: str, number: int, refs: tuple[Reference, ...] = (), inverse: str | None = None
) -> UAReferenceType:
    return UAReferenceType(
        node_id=ua(number),
        browse_name=QualifiedName(name=browse),
        node_class=NodeClass.REFERENCE_TYPE,
        role=NodeRole.BASE_TYPE,
        references=refs,
        inverse_name=inverse,
    )


def object_node(
    browse: str,
    number: int,
    refs: tuple[Reference, ...] = (),
    rule: ModellingRule = ModellingRule.NO_RULE,
) -> UANode:
    return UANode(
        node_id=_id(number),
        browse_name=QualifiedName(name=browse),
        node_class=NodeClass.OBJECT,
        role=NodeRole.INSTANCE,
        references=refs,
        modelling_rule=rule,
    )


def variable(
    browse: str,
    number: int,
    refs: tuple[Reference, ...] = (),
    rule: ModellingRule = ModellingRule.NO_RULE,
    description: str | None = None,
) -> UAVariable:
    return UAVariable(
        node_id=_id(number),
        browse_name=QualifiedName(name=browse),
        node_class=NodeClass.VARIABLE,
        role=NodeRole.INSTANCE,
        references=refs,
        modelling_rule=rule,
        description=description,
    )


def argument(browse: str, number: int, refs: tuple[Reference, ...] = ()) -> UAVariable:
    return UAVariable(
        node_id=_id(number),
        browse_name=QualifiedName(name=browse),
        node_class=NodeClass.VARIABLE,
        role=NodeRole.ARGUMENT,
        references=refs,
    )


def method(browse: str, number: int, refs: tuple[Reference, ...] = ()) -> UAMethod:
    return UAMethod(
        node_id=_id(number),
        browse_name=QualifiedName(name=browse),
        node_class=NodeClass.METHOD,
        role=NodeRole.METHOD,
        references=refs,
    )


def interface(browse: str, number: int) -> UAInterface:
    return UAInterface(
        node_id=_id(number),
        browse_name=QualifiedName(name=browse),
        node_class=NodeClass.INTERFACE,
    )


def data_type(
    browse: str,
    number: int,
    refs: tuple[Reference, ...] = (),
    definition: Definition | None = None,
    role: NodeRole = NodeRole.STRUCTURE,
    namespace: str = Machinery_NS,
) -> UADataType:
    return UADataType(
        node_id=node_id(number, namespace),
        browse_name=QualifiedName(name=browse),
        node_class=NodeClass.DATA_TYPE,
        role=role,
        references=refs,
        definition=definition,
    )


def base_type(
    browse: str, number: int, refs: tuple[Reference, ...] = (), namespace: str = Machinery_NS
) -> UADataType:
    return data_type(browse, number, refs=refs, role=NodeRole.BASE_TYPE, namespace=namespace)


def enum_type(
    browse: str,
    number: int,
    values: tuple[str, ...] = ("Idle",),
    namespace: str = Machinery_NS,
) -> UADataType:
    definition = enum_definition(values)
    return data_type(browse, number, definition=definition, role=NodeRole.ENUM, namespace=namespace)


def structure_type(
    browse: str, number: int, fields: tuple[tuple[str, ExpandedNodeId], ...]
) -> UADataType:
    return data_type(browse, number, definition=structure_definition(fields))


def structure_of(
    number: int, fields: tuple[StructureField, ...], browse: str = "JobDataType"
) -> UADataType:
    return data_type(browse, number, definition=Definition(kind="Structure", fields=fields))


def reference_type(
    browse: str, number: int, refs: tuple[Reference, ...] = (), inverse: str | None = None
) -> UAReferenceType:
    return UAReferenceType(
        node_id=_id(number),
        browse_name=QualifiedName(name=browse),
        node_class=NodeClass.REFERENCE_TYPE,
        role=NodeRole.BASE_TYPE,
        references=refs,
        inverse_name=inverse,
    )


def enum_definition(values: tuple[str, ...]) -> Definition:
    return Definition(
        kind="Enum",
        enum_values=tuple(EnumValue(name=name, value=index) for index, name in enumerate(values)),
    )


def structure_definition(
    fields: tuple[tuple[str, ExpandedNodeId], ...], optional: tuple[str, ...] = ()
) -> Definition:
    return Definition(
        kind="Structure",
        fields=tuple(
            StructureField(name=name, data_type=kind, is_optional=name in optional)
            for name, kind in fields
        ),
    )


def optional_fields(count: int) -> tuple[StructureField, ...]:
    return tuple(
        StructureField(name=f"Field{index}", data_type=UA_STATUS_CODE, is_optional=True)
        for index in range(count)
    )


def model_of(
    nodes: list[UANode],
    namespaces: dict[str, Namespace] | None = None,
    documents: tuple[DocumentInfo, ...] = (),
) -> SemanticModel:
    known = namespaces if namespaces is not None else {Machinery_NS: Namespace(uri=Machinery_NS)}
    model = build_model(nodes, known)
    model.documents.extend(documents)
    return model


def lint(
    nodes: list[UANode],
    namespaces: dict[str, Namespace] | None = None,
    documents: tuple[DocumentInfo, ...] = (),
) -> LintReport:
    return lint_model(model_of(nodes, namespaces, documents))


def codes(report: LintReport) -> set[str]:
    return {item.code for item in report.findings}


def clean_model() -> list[UANode]:
    """A companion model that satisfies every rule the checks judge."""
    pump_type = object_type(
        "PumpType",
        9000,
        refs=components(9001, 9002, 9003) + properties(9010),
    )
    pump_status = data_type(
        "PumpStatusDataType",
        9010,
        refs=subtypes(ua(24)),
        definition=structure_definition((("State", _id(9011)), ("ErrorCode", ua(23)))),
    )
    pump_state = data_type(
        "PumpStateEnum",
        9011,
        refs=subtypes(ua(29)),
        definition=enum_definition(("Idle", "Running", "Faulted")),
        role=NodeRole.ENUM,
    )
    return [
        pump_type,
        variable("Status", 9001, rule=ModellingRule.OPTIONAL),
        variable("JobId", 9002, rule=ModellingRule.OPTIONAL),
        method("StartPump", 9003, refs=arguments(9004, 9005)),
        argument("PortId", 9004),
        argument("RecipeId", 9005),
        pump_status,
        pump_state,
        reference_type("HasPumpStatus", 9020, inverse="PumpStatusOf"),
    ]


class TestNaming:
    def test_clean_companion_model_is_green(self) -> None:
        assert lint(clean_model()).findings == []

    def test_lowercase_browse_name_is_advisory(self) -> None:
        report = lint([object_type("pumpType", 9000)])
        assert codes(report) == {"BROWSE_NAME_NOT_PASCAL_CASE"}
        assert report.by_severity(Severity.ADVISORY)

    def test_acronym_browse_name_is_advisory(self) -> None:
        report = lint([variable("MACAddress", 9000)])
        assert codes(report) == {"BROWSE_NAME_NOT_PASCAL_CASE"}

    def test_special_characters_need_a_symbolic_name(self) -> None:
        report = lint([variable("Machine-Status", 9000)])
        assert codes(report) == {"BROWSE_NAME_SPECIAL_CHARS_WITHOUT_SYMBOLIC_NAME"}

    def test_special_characters_are_fine_with_a_symbolic_name(self) -> None:
        node = UANode(
            node_id=_id(9000),
            browse_name=QualifiedName(name="Machine-Status"),
            node_class=NodeClass.VARIABLE,
            role=NodeRole.INSTANCE,
            symbolic_name="MachineStatus",
        )
        assert codes(lint([node])) == set()

    def test_object_type_needs_a_type_suffix(self) -> None:
        assert codes(lint([object_type("Pump", 9000)])) == {"TYPE_SUFFIX_MISSING"}

    def test_structure_needs_a_datatype_suffix(self) -> None:
        report = lint([structure_type("JobInfo", 9000, (("State", UA_STATUS_CODE),))])
        assert codes(report) == {"TYPE_SUFFIX_MISSING"}

    def test_enum_must_not_carry_a_datatype_suffix(self) -> None:
        assert codes(lint([enum_type("JobStateDataType", 9000)])) == {"TYPE_SUFFIX_FORBIDDEN"}

    def test_enum_may_carry_the_enum_suffix(self) -> None:
        assert codes(lint([enum_type("JobStateEnum", 9000)])) == set()

    def test_simple_datatype_must_not_carry_a_suffix(self) -> None:
        assert codes(lint([base_type("TemperatureType", 9000)])) == {"TYPE_SUFFIX_FORBIDDEN"}

    def test_interface_name_is_not_read_as_an_acronym(self) -> None:
        assert codes(lint([interface("IMachineStatus", 9000)])) == set()

    def test_base_nodes_of_the_ua_namespace_are_not_judged(self) -> None:
        assert codes(lint([enum_type("jobState", 9000, namespace=UA_NS)])) == set()

    def test_placeholder_names_are_not_judged_as_naming(self) -> None:
        assert codes(lint([variable("<MacAddress>", 9000)])) == set()

    def test_reference_type_name_should_be_a_verb(self) -> None:
        assert codes(lint([reference_type("MachineAssociation", 9000)])) == {
            "REFERENCE_TYPE_NAME_NOT_VERB"
        }

    def test_inverse_name_should_be_the_inverse(self) -> None:
        report = lint([reference_type("HasComponent", 9000, inverse="HasPartOf")])
        assert codes(report) == {"REFERENCE_INVERSE_NAME_SPELLING"}

    def test_inverse_name_on_a_symmetric_base(self) -> None:
        base = ua_reference_type("HasChild", 40)
        subtype = reference_type("HasMachineGroup", 9000, refs=subtypes(ua(40)), inverse="GroupingOf")
        assert codes(lint([base, subtype])) == {"REFERENCE_INVERSE_ON_SYMMETRIC_BASE"}

    def test_inverse_name_under_an_asymmetric_base_is_fine(self) -> None:
        base = ua_reference_type("HasComponent", 40, inverse="ComponentOf")
        subtype = reference_type("HasPumpStatus", 9000, refs=subtypes(ua(40)), inverse="PumpStatusOf")
        assert codes(lint([base, subtype])) == set()


class TestDefinitionMembers:
    def test_field_names_are_pascal_case(self) -> None:
        report = lint([structure_type("JobDataType", 9000, (("jobId", UA_STATUS_CODE),))])
        assert codes(report) == {"FIELD_NAME_NOT_PASCAL_CASE"}

    def test_enum_value_names_are_pascal_case(self) -> None:
        assert codes(lint([enum_type("JobStateEnum", 9000, ("idle",))])) == {
            "ENUM_VALUE_NOT_PASCAL_CASE"
        }

    def test_field_names_must_be_unique(self) -> None:
        fields = (("State", UA_STATUS_CODE), ("State", UA_STATUS_CODE))
        report = lint([structure_type("JobDataType", 9000, fields)])
        assert codes(report) == {"FIELD_NAME_DUPLICATE"}
        assert "State" in report.findings[0].message

    def test_enum_value_names_must_be_unique(self) -> None:
        assert codes(lint([enum_type("JobStateEnum", 9000, ("Idle", "Idle"))])) == {
            "ENUM_VALUE_NAME_DUPLICATE"
        }

    def test_optional_fields_are_limited_to_thirty_two(self) -> None:
        report = lint([structure_of(9000, optional_fields(33))])
        assert codes(report) == {"STRUCTURE_OPTIONAL_FIELDS_LIMIT"}
        assert "32" in report.findings[0].message

    def test_thirty_two_optional_fields_are_allowed(self) -> None:
        report = lint([structure_of(9000, optional_fields(32))])
        assert codes(report) == set()

    def test_subtypes_and_optional_fields_cannot_be_combined(self) -> None:
        fields = (
            StructureField(name="State", data_type=UA_STATUS_CODE, is_optional=True),
            StructureField(
                name="Payload",
                data_type=UA_STATUS_CODE,
                metadata=FieldMetadata(allow_sub_types=True),
            ),
        )
        report = lint([structure_of(9000, fields)])
        assert codes(report) == {"STRUCTURE_SUBTYPES_WITH_OPTIONAL_FIELDS"}


class TestMethods:
    def test_argument_names_are_pascal_case(self) -> None:
        start = method("StartPump", 9003, refs=arguments(9004))
        assert codes(lint([start, argument("jobId", 9004)])) == {"ARGUMENT_NAME_NOT_PASCAL_CASE"}

    def test_argument_names_must_be_unique(self) -> None:
        start = method("StartPump", 9003, refs=arguments(9004, 9005))
        report = lint([start, argument("JobId", 9004), argument("JobId", 9005)])
        assert codes(report) == {"ARGUMENT_NAME_DUPLICATE"}


class TestNamesAndBrowsePaths:
    def test_browse_paths_must_be_unique_without_case(self) -> None:
        pump_type = object_type("PumpType", 9000, refs=components(9001, 9002))
        report = lint([pump_type, variable("JobId", 9001), variable("JobID", 9002)])
        assert codes(report) == {"BROWSE_PATH_CASE_COLLISION", "BROWSE_NAME_NOT_PASCAL_CASE"}
        text = " ".join(item.message for item in report.findings)
        assert "JobId" in text
        assert "JobID" in text

    def test_paths_of_different_depth_do_not_collide(self) -> None:
        pump_type = object_type("PumpType", 9000, refs=components(9001, 9003))
        assembly = object_node("Assembly", 9001, refs=components(9002))
        report = lint([pump_type, assembly, variable("Assembly", 9002), variable("JobId", 9003)])
        assert codes(report) == set()

    def test_type_names_must_be_unique_inside_a_namespace(self) -> None:
        report = lint([object_type("PumpType", 9000), object_type("PumpType", 9001)])
        assert codes(report) == {"TYPE_NAME_DUPLICATE_IN_NAMESPACE"}

    def test_a_base_type_sharing_a_name_with_a_companion_is_not_a_finding(self) -> None:
        report = lint([object_type("PumpType", 9000), ua_object_type("PumpType", 2000)], NAMESPACES)
        assert codes(report) == set()

    def test_a_name_used_in_two_namespaces_is_ambiguous(self) -> None:
        report = lint([object_type("PumpType", 9000), vendor_object_type("PumpType", 9000)], NAMESPACES)
        assert codes(report) == {"BROWSE_NAME_USED_IN_TWO_NAMESPACES"}

    def test_names_of_two_namespaces_do_not_collide_when_they_differ(self) -> None:
        report = lint([object_type("PumpType", 9000), vendor_object_type("ValveType", 9000)], NAMESPACES)
        assert codes(report) == set()


class TestNamespaceRules:
    def test_a_nodeset_file_is_named_nodeset2(self) -> None:
        document = DocumentInfo(source_path="Machinery.xml", namespaces=(Namespace(uri=Machinery_NS),))
        report = lint([], documents=(document,))
        assert codes(report) == {"FILENAME_CONVENTION"}
        assert "NodeSet2.xml" in report.findings[0].message

    def test_the_companion_filename_spelling_is_accepted(self) -> None:
        document = DocumentInfo(
            source_path="Opc.Ua.Machinery.NodeSet2.xml",
            namespaces=(Namespace(uri=Machinery_NS),),
        )
        assert codes(lint([], documents=(document,))) == set()

    def test_official_companion_case_variants_are_accepted(self) -> None:
        """The official DI file is ``Opc.Ua.Di.NodeSet2.xml`` although the alias is ``DI``."""
        di_uri = "http://opcfoundation.org/UA/DI/"
        for filename in ("Opc.Ua.Di.NodeSet2.xml", "Opc.Ua.DI.NodeSet2.xml"):
            document = DocumentInfo(source_path=filename, namespaces=(Namespace(uri=di_uri),))
            assert codes(lint([], documents=(document,))) == set()

    def test_a_vendor_only_needs_the_nodeset2_suffix(self) -> None:
        document = DocumentInfo(source_path="ACME.NodeSet2.xml", namespaces=(Namespace(uri=VENDOR_NS),))
        assert codes(lint([], documents=(document,))) == set()

    def test_a_wrong_companion_short_name_is_reported(self) -> None:
        document = DocumentInfo(
            source_path="Opc.Ua.Pump.NodeSet2.xml",
            namespaces=(Namespace(uri=Machinery_NS),),
        )
        report = lint([], documents=(document,))
        assert codes(report) == {"FILENAME_CONVENTION"}
        assert "Opc.Ua.Machinery.NodeSet2.xml" in report.findings[0].message

    def test_a_namespace_uri_without_trailing_slash_is_information(self) -> None:
        report = lint([], namespaces={VENDOR_NS: Namespace(uri="http://vendor.example.com/acme")})
        assert codes(report) == {"NAMESPACE_URI_MISSING_TRAILING_SLASH"}
        assert report.by_severity(Severity.INFO)

    def test_a_namespace_uri_must_be_a_plain_http_uri(self) -> None:
        report = lint([], namespaces={"urn:vendor:acme": Namespace(uri="urn:vendor:acme")})
        assert codes(report) == {"NAMESPACE_URI_TEMPLATE"}

    def test_percent_escaped_namespace_uris_are_reported(self) -> None:
        uri = "http://opcfoundation.org/UA/Mach%20inery/"
        report = lint([], namespaces={uri: Namespace(uri=uri)})
        assert codes(report) == {"NAMESPACE_URI_TEMPLATE"}

    def test_the_base_namespace_is_not_judged(self) -> None:
        assert codes(lint([], namespaces={UA_NS: Namespace(uri=UA_NS)})) == set()


class TestStatusCodes:
    def test_status_code_datatypes_are_not_allowed(self) -> None:
        report = lint([base_type("MachineryStatusCode", 9000)])
        assert codes(report) == {"STATUS_CODE_DEFINED_OUTSIDE_BASE"}

    def test_a_status_code_subtype_is_reported_too(self) -> None:
        base = base_type("StatusCode", 22, namespace=UA_NS)
        derived = base_type("PumpErrors", 9000, refs=subtypes(UA_STATUS_CODE))
        assert codes(lint([base, derived])) == {"STATUS_CODE_DEFINED_OUTSIDE_BASE"}

    def test_an_unrelated_datatype_is_fine(self) -> None:
        assert codes(lint([base_type("PumpDiagnostics", 9000)])) == set()


class TestRecursion:
    def test_a_mandatory_self_containing_declaration_is_advisory(self) -> None:
        pump_type = object_type("PumpType", 9000, refs=components(9001))
        child = object_node("ChildPump", 9001, refs=typed_as(9000), rule=ModellingRule.MANDATORY)
        assert codes(lint([pump_type, child])) == {"MANDATORY_SELF_CONTAINED_DECLARATION"}

    def test_a_mandatory_nested_instance_is_advisory(self) -> None:
        pump_type = object_type("PumpType", 9000, refs=components(9001))
        assembly = object_node("Assembly", 9001, refs=components(9002))
        nested = object_node("InnerPump", 9002, refs=typed_as(9000), rule=ModellingRule.MANDATORY)
        assert codes(lint([pump_type, assembly, nested])) == {"MANDATORY_SELF_CONTAINED_DECLARATION"}

    def test_an_optional_recursive_declaration_is_allowed(self) -> None:
        pump_type = object_type("PumpType", 9000, refs=components(9001))
        child = object_node("ChildPump", 9001, refs=typed_as(9000), rule=ModellingRule.OPTIONAL)
        assert codes(lint([pump_type, child])) == set()


class TestAuditEventsAndText:
    def test_audit_event_subtypes_should_not_add_fields(self) -> None:
        audit_base = ua_object_type("AuditEventType", 2000)
        subtype = object_type(
            "AuditPumpEventType", 9000, refs=subtypes(ua(2000)) + properties(9001)
        )
        report = lint([audit_base, subtype, variable("Reason", 9001)])
        assert codes(report) == {"AUDIT_EVENT_SUBTYPE_ADDS_FIELDS"}

    def test_audit_event_methods_are_not_fields(self) -> None:
        audit_base = ua_object_type("AuditEventType", 2000)
        subtype = object_type("AuditPumpEventType", 9000, refs=subtypes(ua(2000)) + components(9001))
        assert codes(lint([audit_base, subtype, method("Acknowledge", 9001)])) == set()

    def test_namespace_index_in_description_text_is_reported(self) -> None:
        report = lint([variable("Status", 9000, description="Read 1:JobId for the current job")])
        assert codes(report) == {"NAMESPACE_INDEX_IN_TEXT"}

    def test_a_browse_name_without_index_is_fine(self) -> None:
        report = lint([variable("Status", 9000, description="Read JobId for the current job")])
        assert codes(report) == set()


class TestSeverityDiscipline:
    def test_no_lint_rule_can_break_a_release(self) -> None:
        assert all(rule.severity is not Severity.BREAKING for rule in LINT_RULES.values())

    def test_a_lint_pass_never_blocks(self) -> None:
        report = lint([object_type("pump", 9000), enum_type("JobStateDataType", 9001, ("idle",))])
        assert report.findings
        assert report.ok
        assert report.by_severity(Severity.BREAKING) == []

    def test_every_emitted_code_is_registered(self) -> None:
        assert codes(lint([object_type("pump", 9000)])) <= set(LINT_RULES)

    def test_findings_carry_the_rule_slug_and_clause(self) -> None:
        item = lint([object_type("pumpType", 9000)]).findings[0]
        assert item.rule_id == "BP-Naming-PascalCase"
        assert item.clause == "2.1.2"
        assert item.node_id == _id(9000)

    def test_slugs_are_shared_with_the_diff_registry(self) -> None:
        slugs = {rule.rule_id for rule in LINT_RULES.values()}
        assert {"BP-Naming-PascalCase", "BP-Filename-Convention"} <= slugs

    def test_summary_counts_the_severity_buckets(self) -> None:
        report = lint([], namespaces={VENDOR_NS: Namespace(uri="http://vendor.example.com/acme")})
        assert report.summary() == "0 advisory finding(s), 1 information finding(s)"

    def test_json_reports_severity_and_rule_of_every_finding(self) -> None:
        payload = json.loads(json.dumps(lint([object_type("pumpType", 9000)]).as_json()))
        assert set(payload) == {"paper", "summary", "findings"}
        assert payload["summary"] == {"findings": 1, "advisory": 1, "info": 0}
        entry = payload["findings"][0]
        assert entry["code"] == "BROWSE_NAME_NOT_PASCAL_CASE"
        assert entry["severity"] == "advisory"
        assert entry["node"] == _id(9000).as_xml()


class TestCli:
    def test_validate_reports_best_practices_without_failing(self) -> None:
        result = CliRunner().invoke(main, ["validate", str(MACHINERY), "--best-practices"])
        assert result.exit_code == 0
        assert "best practices" in result.output
        assert "BP-" in result.output

    def test_validate_without_the_flag_stays_a_validation(self) -> None:
        result = CliRunner().invoke(main, ["validate", str(MACHINERY)])
        assert result.exit_code == 0
        assert "best practices" not in result.output
