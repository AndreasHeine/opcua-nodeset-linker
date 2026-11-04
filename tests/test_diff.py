"""Semantic diff tests: model comparison, not XML comparison."""

from __future__ import annotations

import dataclasses
import json
import re
from pathlib import Path

import pytest
from click.testing import CliRunner
from tests.conftest import (
    MACHINERY,
    Machinery_NS,
    enum_definition,
    make_data_type,
    make_method,
    make_object_type,
    make_reference_type,
    make_variable,
    node_id,
    structure_definition,
)

from nodeset_linker.cli.main import main
from nodeset_linker.diff import (
    PAPER,
    Severity,
    apply_impact,
    console_text,
    dependents,
    diff_models,
    html_text,
    json_text,
    markdown_text,
    total_impact,
)
from nodeset_linker.diff.model import NamespaceChange, SemanticDiff
from nodeset_linker.models.identifiers import ExpandedNodeId, QualifiedName
from nodeset_linker.models.node import (
    Definition,
    FieldMetadata,
    OptionSetValue,
    Reference,
    StructureField,
    UnionAlternative,
)
from nodeset_linker.models.node_class import ModellingRule, NodeRole
from nodeset_linker.models.standard import STANDARD_DATA_TYPE_IDS
from nodeset_linker.repository.model import SemanticModel, build_model, load_nodesets

BASE = MACHINERY.read_text(encoding="utf-8")


def replace(node, **changes):
    """A copy of a model node with one attribute changed."""
    return dataclasses.replace(node, **changes)


def in_namespace(node, uri: str):
    """A copy of a model node in a project namespace rather than the OPC UA namespace."""
    return replace(node, node_id=node_id(int(node.node_id.identifier.value), uri))


def write(tmp_path: Path, name: str, text: str) -> Path:
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    return path


def diff_texts(tmp_path: Path, old: str, new: str) -> tuple[SemanticModel, SemanticModel, SemanticDiff]:
    """Diff two textual variants of the Machinery model."""
    old_model = load_nodesets([write(tmp_path, "old.xml", old)])
    new_model = load_nodesets([write(tmp_path, "new.xml", new)])
    return old_model, new_model, diff_models(old_model, new_model)


def labels(entries: list) -> set[str]:
    return {entry.label for entry in entries}


def remove_node(text: str, node_id_value: str) -> str:
    """Delete one node element from a nodeset document."""
    pattern = rf"\n\n?  <UA\w+ NodeId=\"{node_id_value}\".*?</UA\w+>\n"
    return re.sub(pattern, "\n", text, count=1, flags=re.S)


def reindex_namespace(text: str) -> str:
    """Move the model namespace from index 1 to index 2 behind a filler namespace."""
    text = text.replace(
        "<Uri>http://opcfoundation.org/UA/Machinery/</Uri>",
        "<Uri>http://example.org/UA/Filler/</Uri>\n    <Uri>http://opcfoundation.org/UA/Machinery/</Uri>",
    )
    return text.replace("ns=1;i=", "ns=2;i=").replace('BrowseName="1:', 'BrowseName="2:')


def reversed_nodes(text: str) -> str:
    """Same nodes, opposite document order."""
    head, _, tail = text.partition("<UADataType NodeId=\"ns=1;i=2001\"")
    body = "<UADataType NodeId=\"ns=1;i=2001\"" + tail[:-len("</UANodeSet>")]
    nodes = re.findall(r"<UA\w+ .*?</UA\w+>", body, flags=re.S)
    return head + "\n  ".join(reversed(nodes)) + "\n</UANodeSet>"


ADDED_TYPE = """
  <UAObjectType NodeId="ns=1;i=2600" BrowseName="1:MachineHealthType" SymbolicName="MachineHealthType">
    <DisplayName>MachineHealthType</DisplayName>
    <References>
      <Reference ReferenceType="HasSubtype" IsForward="false">i=58</Reference>
    </References>
  </UAObjectType>
"""

REMOVED_TYPE = """
  <UAObjectType NodeId="ns=1;i=2700" BrowseName="1:LegacyMachineType" SymbolicName="LegacyMachineType">
    <DisplayName>LegacyMachineType</DisplayName>
    <References>
      <Reference ReferenceType="HasSubtype" IsForward="false">i=58</Reference>
    </References>
  </UAObjectType>
"""


class TestAddedAndRemovedNodes:
    def test_added_node(self, tmp_path) -> None:
        _, _, diff = diff_texts(tmp_path, BASE, BASE.replace("</UANodeSet>", ADDED_TYPE + "</UANodeSet>"))
        assert "MachineHealthType" in labels(diff.added)
        assert not diff.removed
        assert not diff.modified
        assert not diff.breaking

    def test_removed_node(self, tmp_path) -> None:
        _, _, diff = diff_texts(tmp_path, BASE.replace("</UANodeSet>", REMOVED_TYPE + "</UANodeSet>"), BASE)
        assert "LegacyMachineType" in labels(diff.removed)
        assert not diff.added

    def test_removed_type_definition_is_breaking(self, tmp_path) -> None:
        _, _, diff = diff_texts(tmp_path, BASE.replace("</UANodeSet>", REMOVED_TYPE + "</UANodeSet>"), BASE)
        reasons = {change.label: change.reason for change in diff.breaking}
        assert reasons["LegacyMachineType"] == "ObjectType removed"

    def test_removed_data_type_is_breaking(self, tmp_path) -> None:
        _, _, diff = diff_texts(tmp_path, BASE, remove_node(BASE, "ns=1;i=2020"))
        assert labels(diff.removed) == {"MachineStatusStructure"}
        assert [change.reason for change in diff.breaking] == ["DataType removed"]

    def test_namespace_totals(self, tmp_path) -> None:
        _, _, diff = diff_texts(tmp_path, BASE, BASE.replace("</UANodeSet>", ADDED_TYPE + "</UANodeSet>"))
        totals = {entry.alias: entry for entry in diff.namespaces}
        assert totals["Machinery"].nodes[1] == totals["Machinery"].nodes[0] + 1
        assert NamespaceChange.delta(*totals["Machinery"].nodes) == "+1"
        assert totals["Machinery"].references[1] > totals["Machinery"].references[0]


class TestDefinitionChanges:
    def test_structure_field_added_is_breaking(self, tmp_path) -> None:
        new = BASE.replace(
            '<Field Name="Progress" DataType="ns=1;i=2003" IsOptional="true" />',
            '<Field Name="Progress" DataType="ns=1;i=2003" IsOptional="true" />\n'
            '      <Field Name="ProgressPercentage" DataType="Int32" IsOptional="true" />',
        )
        _, _, diff = diff_texts(tmp_path, BASE, new)
        modified = {entry.label: entry for entry in diff.modified}
        assert "JobDataType" in modified
        assert any("ProgressPercentage" in change.subject for change in modified["JobDataType"].changes)
        # §3.2.5: a new field changes the encoding of every value of the structure.
        assert diff.breaking
        assert "BP-StructureField-Added" in diff.breaking[0].rule_id

    def test_structure_field_datatype_change_is_breaking(self, tmp_path) -> None:
        new = BASE.replace('<Field Name="JobId" DataType="String" />', '<Field Name="JobId" DataType="Int32" />')
        _, _, diff = diff_texts(tmp_path, BASE, new)
        assert diff.breaking
        assert "JobId" in diff.breaking[0].reason

    def test_enum_value_added_is_breaking(self, tmp_path) -> None:
        new = BASE.replace('<Field Name="Done" Value="2" />', '<Field Name="Done" Value="2" />\n      <Field Name="Paused" Value="3" />')
        _, _, diff = diff_texts(tmp_path, BASE, new)
        assert labels(diff.modified) == {"JobStateEnum"}
        # §3.2.4: a new enum value changes the value range a consumer may receive.
        assert diff.breaking
        assert "BP-EnumValue-Added" in diff.breaking[0].rule_id

    def test_enum_value_removed_is_breaking(self, tmp_path) -> None:
        new = BASE.replace('      <Field Name="Done" Value="2" />\n', "")
        _, _, diff = diff_texts(tmp_path, BASE, new)
        assert labels(diff.modified) == {"JobStateEnum"}
        assert "Done" in diff.breaking[0].reason


class TestReferenceChanges:
    def test_reference_added_and_removed(self, tmp_path) -> None:
        new = BASE.replace(
            '      <Reference ReferenceType="HasInterface">ns=1;i=2100</Reference>\n'
            '      <Reference ReferenceType="HasComponent">ns=1;i=2201</Reference>',
            '      <Reference ReferenceType="HasComponent">ns=1;i=2300</Reference>',
        )
        _, _, diff = diff_texts(tmp_path, BASE, new)
        modified = {entry.label: entry for entry in diff.modified}
        areas = [change.area for change in modified["MSSControllerInstance"].changes]
        # An Interface is judged as a hierarchy change (§3.2.3), components as references.
        assert areas == ["hierarchy", "reference", "reference"]
        kinds = [change.kind for change in modified["MSSControllerInstance"].changes]
        assert sorted(kinds) == ["added", "removed", "removed"]
        assert {change.label for change in diff.breaking} == {"MSSControllerInstance"}

    def test_reference_removal_names_the_target(self, tmp_path) -> None:
        new = BASE.replace('      <Reference ReferenceType="HasInterface">ns=1;i=2100</Reference>\n', "")
        _, _, diff = diff_texts(tmp_path, BASE, new)
        reasons = [change.reason for change in diff.breaking]
        assert any("HasInterface" in reason for reason in reasons)

    def test_organizes_reference_is_not_breaking(self, tmp_path) -> None:
        new = BASE.replace(
            '      <Reference ReferenceType="Organizes">ns=1;i=2200</Reference>',
            '      <Reference ReferenceType="Organizes">ns=1;i=2500</Reference>\n'
            '      <Reference ReferenceType="Organizes">ns=1;i=2200</Reference>',
        )
        _, _, diff = diff_texts(tmp_path, BASE, new)
        assert labels(diff.modified) == {"MachineryView"}
        assert not diff.breaking


class TestModellingRules:
    def test_optional_to_mandatory_is_breaking(self) -> None:
        old = build_model([make_variable("Status", 3001, rule=ModellingRule.OPTIONAL)])
        new = build_model([make_variable("Status", 3001, rule=ModellingRule.MANDATORY)])
        diff = diff_models(old, new)
        assert diff.breaking
        assert "Modelling rule" in diff.breaking[0].reason

    def test_mandatory_to_optional_is_not_breaking(self) -> None:
        old = build_model([make_variable("Status", 3001, rule=ModellingRule.MANDATORY)])
        new = build_model([make_variable("Status", 3001, rule=ModellingRule.OPTIONAL)])
        assert not diff_models(old, new).breaking

    def test_mandatory_placeholder_removal_is_breaking(self) -> None:
        node = in_namespace(
            make_variable("SubMachine", 5001, rule=ModellingRule.MANDATORY_PLACEHOLDER), Machinery_NS
        )
        assert diff_models(build_model([node]), build_model([])).breaking

    def test_optional_placeholder_removal_is_breaking(self) -> None:
        node = in_namespace(
            make_variable("SubMachine", 5001, rule=ModellingRule.OPTIONAL_PLACEHOLDER), Machinery_NS
        )
        # §3.2.16: removing an optional InstanceDeclaration is a breaking change too.
        diff = diff_models(build_model([node]), build_model([]))
        assert diff.breaking
        assert "BP-Declaration-Removed-Optional" in diff.breaking[0].rule_id


class TestIdentityNotXml:
    def test_namespace_index_reassignment_is_no_change(self, tmp_path) -> None:
        _, _, diff = diff_texts(tmp_path, BASE, reindex_namespace(BASE))
        assert diff.is_empty
        assert console_text(diff) == "No semantic differences found."

    def test_document_order_is_no_change(self, tmp_path) -> None:
        _, _, diff = diff_texts(tmp_path, BASE, reversed_nodes(BASE))
        assert diff.is_empty

    def test_whitespace_and_comments_are_no_change(self, tmp_path) -> None:
        noisy = BASE.replace("<UANodeSet", "<!-- edited -->\n  <UANodeSet").replace("\n", "\n\n")
        _, _, diff = diff_texts(tmp_path, BASE, noisy)
        assert diff.is_empty

    def test_renumbered_node_is_reported_as_renumbering(self) -> None:
        old = build_model([make_object_type("MachineType", 4200)])
        new = build_model([make_object_type("MachineType", 4201)])
        diff = diff_models(old, new)
        assert not diff.added
        assert not diff.removed
        assert [entry.label for entry in diff.renumbered] == ["MachineType"]
        # §3.2.17: a published identifier shall not move, so renumbering gates the build.
        assert diff.breaking
        assert diff.breaking[0].rule_id == "BP-NodeId-Immutable"
        assert diff.renumbered[0].severity == Severity.BREAKING

    def test_renumbered_node_with_changed_content(self) -> None:
        old = build_model([make_object_type("MachineType", 4200)])
        new = build_model(
            [make_object_type("MachineType", 4201, refs=(Reference(target=node_id(58), type_name="HasSubtype"),))]
        )
        diff = diff_models(old, new)
        assert labels(diff.renumbered) == {"MachineType"}
        # The base type is a hierarchy change (§3.2.9), not a plain reference change.
        assert any(
            (change.area, change.kind) == ("hierarchy", "added")
            for change in diff.renumbered[0].changes
        )


class TestImpact:
    def test_impact_counts_dependent_nodes(self, tmp_path) -> None:
        old_model, new_model, diff = diff_texts(tmp_path, BASE, remove_node(BASE, "ns=1;i=2100"))
        apply_impact(diff, old_model, new_model)
        assert [entry.label for entry in diff.breaking] == ["IMachineStatus"]
        assert diff.breaking[0].affected == 5  # the interface user and everything under it
        assert total_impact(diff) == diff.breaking[0].affected
        assert "MSSControllerInstance" in console_text(diff, impact=True)

    def test_impact_names_dependents(self, tmp_path) -> None:
        old_model, new_model, diff = diff_texts(
            tmp_path, BASE, BASE.replace("</UANodeSet>", ADDED_TYPE + "</UANodeSet>")
        )
        apply_impact(diff, old_model, new_model)
        assert "Total Impact" in console_text(diff, impact=True)


class TestOutputFormats:
    def test_json_shape(self, tmp_path) -> None:
        _, _, diff = diff_texts(tmp_path, BASE.replace("</UANodeSet>", REMOVED_TYPE + "</UANodeSet>"), BASE)
        payload = json.loads(json_text(diff))
        legacy_keys = {
            "old",
            "new",
            "summary",
            "added_nodes",
            "removed_nodes",
            "modified_nodes",
            "renumbered_nodes",
            "namespace_changes",
            "breaking_changes",
        }
        assert legacy_keys <= set(payload)
        assert payload["removed_nodes"][0]["label"] == "LegacyMachineType"
        assert payload["breaking_changes"][0]["reason"] == "ObjectType removed"
        # The severity model only adds keys; nothing a previous consumer read is gone.
        assert set(payload) - legacy_keys == {"paper", "advisory_changes"}
        assert payload["paper"] == PAPER
        breaking = payload["breaking_changes"][0]
        assert breaking["severity"] == "breaking"
        assert breaking["rule_id"] == "BP-Declaration-Removed"
        assert breaking["rationale"]
        assert breaking["clause"] == "3.2.16"
        assert payload["removed_nodes"][0]["severity"] == "breaking"
        assert payload["removed_nodes"][0]["verdicts"][0]["rule_id"] == "BP-Declaration-Removed"
        assert payload["summary"]["advisory_nodes"] == 0
        assert payload["summary"]["info_nodes"] == 0

    def test_json_verdicts_on_every_entry(self, tmp_path) -> None:
        _, _, diff = diff_texts(
            tmp_path, BASE, BASE.replace("</UANodeSet>", ADDED_TYPE + "</UANodeSet>")
        )
        payload = json.loads(json_text(diff))
        added = payload["added_nodes"][0]
        assert added["severity"] == "info"
        assert added["verdicts"][0]["rule_id"] == "BP-Declaration-Added"
        assert added["verdicts"][0]["paper"] == PAPER

    def test_advisory_section_is_reported_but_never_gates(self) -> None:
        old_model = build_model([make_object_type("MachineType", 7940)])
        new_model = build_model(
            [
                make_object_type(
                    "MachineType", 7940, refs=(Reference(target=node_id(2100), type_name="HasInterface"),)
                )
            ]
        )
        diff = diff_models(old_model, new_model)
        text = console_text(diff)
        # Advisory findings are shown to a reader and never exit the build (§7 checklist).
        assert "Advisory: 1" in text
        assert "Best practices to review" in text
        assert "BP-Interface-Added" in text
        assert "Best practices to review" not in console_text(diff, breaking_only=True)

    def test_markdown(self, tmp_path) -> None:
        _, _, diff = diff_texts(tmp_path, BASE, BASE.replace("</UANodeSet>", ADDED_TYPE + "</UANodeSet>"))
        text = markdown_text(diff)
        assert text.startswith("# Semantic diff")
        assert "## Added nodes" in text

    def test_html(self, tmp_path) -> None:
        _, _, diff = diff_texts(tmp_path, BASE.replace("</UANodeSet>", REMOVED_TYPE + "</UANodeSet>"), BASE)
        text = html_text(diff)
        assert "<h2>Breaking changes</h2>" in text
        assert '<strong class="breaking">' in text

    def test_empty_report(self, tmp_path) -> None:
        _, _, diff = diff_texts(tmp_path, BASE, BASE)
        assert console_text(diff) == "No semantic differences found."
        assert markdown_text(diff) == "No semantic differences found."
        assert "<p>No semantic differences found.</p>" in html_text(diff)

    def test_breaking_only_report(self, tmp_path) -> None:
        _, _, diff = diff_texts(tmp_path, BASE.replace("</UANodeSet>", REMOVED_TYPE + "</UANodeSet>"), BASE)
        text = console_text(diff, breaking_only=True)
        assert "Added nodes" not in text
        assert "LegacyMachineType" in text


class TestCli:
    def test_diff_console(self, tmp_path) -> None:
        old = write(tmp_path, "old.xml", BASE.replace("</UANodeSet>", REMOVED_TYPE + "</UANodeSet>"))
        new = write(tmp_path, "new.xml", BASE)
        result = CliRunner().invoke(main, ["diff", str(old), str(new)])
        assert result.exit_code == 0, result.output
        assert "LegacyMachineType" in result.output
        assert "Nodes:" in result.output

    def test_diff_json(self, tmp_path) -> None:
        old = write(tmp_path, "old.xml", BASE)
        new = write(tmp_path, "new.xml", BASE.replace("</UANodeSet>", ADDED_TYPE + "</UANodeSet>"))
        result = CliRunner().invoke(main, ["diff", str(old), str(new), "--json"])
        assert result.exit_code == 0, result.output
        assert json.loads(result.output)["added_nodes"][0]["label"] == "MachineHealthType"

    def test_diff_no_change(self, tmp_path) -> None:
        old = write(tmp_path, "old.xml", BASE)
        new = write(tmp_path, "new.xml", reindex_namespace(BASE))
        result = CliRunner().invoke(main, ["diff", str(old), str(new)])
        assert result.exit_code == 0
        assert "No semantic differences found." in result.output

    def test_fail_on_breaking_exits_1(self, tmp_path) -> None:
        old = write(tmp_path, "old.xml", BASE)
        new = write(
            tmp_path,
            "new.xml",
            BASE.replace('<Field Name="JobId" DataType="String" />', '<Field Name="JobId" DataType="Int32" />'),
        )
        result = CliRunner().invoke(main, ["diff", str(old), str(new), "--fail-on-breaking"])
        assert result.exit_code == 1
        assert "JobId" in result.output

    def test_fail_on_breaking_passes_without_breaking_changes(self, tmp_path) -> None:
        old = write(tmp_path, "old.xml", BASE)
        new = write(
            tmp_path,
            "new.xml",
            BASE.replace("<DisplayName>JobDataType</DisplayName>", "<DisplayName>Job Data Type</DisplayName>"),
        )
        # §3.2.17: DisplayName text may change, so an information-only edit never gates.
        result = CliRunner().invoke(main, ["diff", str(old), str(new), "--fail-on-breaking"])
        assert result.exit_code == 0, result.output

    def test_fail_on_breaking_gates_an_enum_value_addition(self, tmp_path) -> None:
        old = write(tmp_path, "old.xml", BASE)
        new = write(
            tmp_path,
            "new.xml",
            BASE.replace('<Field Name="Done" Value="2" />', '<Field Name="Done" Value="2" />\n      <Field Name="Paused" Value="3" />'),
        )
        # §3.2.4: the value range changed, which gates the build (§7 checklist).
        result = CliRunner().invoke(main, ["diff", str(old), str(new), "--fail-on-breaking"])
        assert result.exit_code == 1, result.output
        assert "BP-EnumValue-Added" in result.output

    def test_impact_and_output_file(self, tmp_path) -> None:
        old = write(tmp_path, "old.xml", BASE)
        new = write(tmp_path, "new.xml", remove_node(BASE, "ns=1;i=2100"))
        out = tmp_path / "diff.md"
        result = CliRunner().invoke(
            main, ["diff", str(old), str(new), "--markdown", "--impact", "-o", str(out)]
        )
        assert result.exit_code == 0, result.output
        assert out.read_text(encoding="utf-8").startswith("# Semantic diff")
        assert "Total Impact" in out.read_text(encoding="utf-8")

    def test_missing_file_reports_error(self, tmp_path) -> None:
        result = CliRunner().invoke(main, ["diff", str(tmp_path / "nope.xml"), str(MACHINERY)])
        assert result.exit_code == 1
        assert "nope.xml" in result.output


def test_synthetic_structure_field_removal_is_breaking() -> None:
    fields = (("JobId", node_id(12)), ("State", node_id(29)))
    old_model = build_model([make_data_type("JobInfo", 6001, definition=structure_definition(fields))])
    new_model = build_model(
        [make_data_type("JobInfo", 6001, definition=structure_definition((("JobId", node_id(12)),)))]
    )
    diff = diff_models(old_model, new_model)
    assert diff.breaking
    assert "State" in diff.breaking[0].reason


def test_display_name_change_is_not_breaking() -> None:
    old = make_object_type("MachineType", 7000)
    new = replace(old, display_name="Machine Type")
    diff = diff_models(build_model([old]), build_model([new]))
    assert labels(diff.modified) == {"MachineType"}
    assert not diff.breaking


def test_enum_definition_value_change_is_breaking() -> None:
    old_model = build_model([make_data_type("LevelEnum", 8000, definition=enum_definition(("Low", "High")))])
    new_model = build_model([make_data_type("LevelEnum", 8000, definition=enum_definition(("Low", "VeryHigh")))])
    diff = diff_models(old_model, new_model)
    assert labels(diff.modified) == {"LevelEnum"}
    assert diff.breaking


def union_definition(alternatives: tuple[tuple[str, ExpandedNodeId], ...]) -> Definition:
    return Definition(
        kind="Union",
        union_alternatives=tuple(
            UnionAlternative(name=name, data_type=data_type)
            for name, data_type in alternatives
        ),
    )


def option_set_definition(values: tuple[tuple[str, int], ...]) -> Definition:
    return Definition(
        kind="OptionSet",
        option_set_values=tuple(
            OptionSetValue(name=name, value=value) for name, value in values
        ),
    )


def structure_of(*fields: StructureField) -> Definition:
    return Definition(kind="Structure", fields=fields)


class TestUnionAndOptionSet:
    def test_union_alternative_added_is_breaking(self) -> None:
        old_model = build_model(
            [make_data_type("ValueUnion", 9000, definition=union_definition((("Int", node_id(6)),)))]
        )
        new_model = build_model(
            [
                make_data_type(
                    "ValueUnion",
                    9000,
                    definition=union_definition(
                        (("Int", node_id(6)), ("Double", node_id(11)))
                    ),
                )
            ]
        )
        diff = diff_models(old_model, new_model)
        # §3.2.6: a new alternative gains an encoding position a consumer cannot decode.
        assert diff.breaking
        assert "BP-UnionAlternative-Added" in diff.breaking[0].rule_id
        assert any(change.kind == "added" for change in diff.modified[0].changes)

    def test_union_alternative_removed_is_breaking(self) -> None:
        old_model = build_model(
            [
                make_data_type(
                    "ValueUnion",
                    9000,
                    definition=union_definition(
                        (("Int", node_id(6)), ("Double", node_id(11)))
                    ),
                )
            ]
        )
        new_model = build_model(
            [make_data_type("ValueUnion", 9000, definition=union_definition((("Int", node_id(6)),)))]
        )
        diff = diff_models(old_model, new_model)
        assert diff.breaking
        assert "Double" in diff.breaking[0].reason

    def test_union_alternative_retyped_is_breaking(self) -> None:
        old_model = build_model(
            [make_data_type("ValueUnion", 9000, definition=union_definition((("Int", node_id(6)),)))]
        )
        new_model = build_model(
            [make_data_type("ValueUnion", 9000, definition=union_definition((("Int", node_id(9)),)))]
        )
        diff = diff_models(old_model, new_model)
        assert diff.breaking

    def test_option_set_value_added_is_breaking(self) -> None:
        old_model = build_model(
            [make_data_type("Flags", 9100, definition=option_set_definition((("Idle", 0),)))]
        )
        new_model = build_model(
            [
                make_data_type(
                    "Flags", 9100, definition=option_set_definition((("Idle", 0), ("Busy", 1)))
                )
            ]
        )
        diff = diff_models(old_model, new_model)
        # §3.2.7 and §3.2.8: a new bit changes what a consumer may receive and what it knows.
        assert diff.breaking
        assert "BP-OptionSetBit-Added" in diff.breaking[0].rule_id
        assert labels(diff.modified) == {"Flags"}

    def test_option_set_value_removed_is_breaking(self) -> None:
        old_model = build_model(
            [
                make_data_type(
                    "Flags", 9100, definition=option_set_definition((("Idle", 0), ("Busy", 1)))
                )
            ]
        )
        new_model = build_model(
            [make_data_type("Flags", 9100, definition=option_set_definition((("Idle", 0),)))]
        )
        diff = diff_models(old_model, new_model)
        assert diff.breaking
        assert "Busy" in diff.breaking[0].reason

    def test_definition_kind_change_is_breaking(self) -> None:
        old_model = build_model(
            [
                make_data_type(
                    "Shape",
                    9200,
                    definition=structure_definition((("Width", node_id(7)),)),
                )
            ]
        )
        new_model = build_model(
            [make_data_type("Shape", 9200, definition=enum_definition(("Small", "Large")))]
        )
        diff = diff_models(old_model, new_model)
        assert diff.breaking
        assert any(change.kind == "definition_kind" for change in diff.modified[0].changes)


class TestFieldMetadataAndText:
    def test_field_array_metadata_change_is_breaking(self) -> None:
        field = StructureField(name="Tags", data_type=node_id(12), metadata=FieldMetadata(value_rank=-1))
        scalar = replace(field, metadata=FieldMetadata(value_rank=1))
        diff = diff_models(
            build_model([make_data_type("TagList", 9300, definition=structure_of(field))]),
            build_model([make_data_type("TagList", 9300, definition=structure_of(scalar))]),
        )
        assert diff.breaking
        assert "ValueRank" in diff.breaking[0].reason

    def test_field_display_name_change_is_not_breaking(self) -> None:
        field = StructureField(name="JobId", data_type=node_id(12), display_name="Job Id")
        other = replace(field, display_name="Identifier")
        diff = diff_models(
            build_model([make_data_type("JobInfo", 9400, definition=structure_of(field))]),
            build_model([make_data_type("JobInfo", 9400, definition=structure_of(other))]),
        )
        assert not diff.breaking
        assert any("JobId" in change.describe() for change in diff.modified[0].changes)

    def test_standard_field_datatype_is_named_without_loaded_core(self) -> None:
        old = StructureField(name="JobId", data_type=node_id(12))
        new = StructureField(name="JobId", data_type=node_id(9))
        diff = diff_models(
            build_model([make_data_type("JobInfo", 9450, definition=structure_of(old))]),
            build_model([make_data_type("JobInfo", 9450, definition=structure_of(new))]),
        )
        assert diff.breaking
        assert "String (i=12) → UInt64 (i=9)" in diff.breaking[0].reason

    def test_field_order_change_is_advisory(self) -> None:
        first = StructureField(name="First", data_type=node_id(12))
        second = StructureField(name="Second", data_type=node_id(12))
        diff = diff_models(
            build_model([make_data_type("Pair", 9500, definition=structure_of(first, second))]),
            build_model([make_data_type("Pair", 9500, definition=structure_of(second, first))]),
        )
        # §2.2: names are the contract; only a positional encoder reads the order.
        assert not diff.breaking
        assert {change.label for change in diff.advisory} == {"Pair"}
        assert any(change.kind == "field_order" for change in diff.modified[0].changes)
        assert diff.modified[0].severity == Severity.ADVISORY


class TestNodeAttributes:
    @pytest.mark.parametrize(("name", "number"), STANDARD_DATA_TYPE_IDS.items())
    def test_standard_datatype_labels_without_a_core_provider(self, name, number) -> None:
        old = make_variable("Value", 6002, data_type=node_id(999999))
        diff = diff_models(
            build_model([old]),
            build_model([replace(old, data_type=node_id(number))]),
        )
        assert diff.modified[0].changes[0].new == f"{name} (i={number})"

    @pytest.mark.parametrize("with_core", [False, True])
    def test_datatype_change_has_names_and_ids_in_every_report(self, with_core) -> None:
        old = make_variable("ProductInstanceUri", 6002, data_type=node_id(12))
        new = replace(old, data_type=node_id(11))
        core = [make_data_type("String", 12), make_data_type("Double", 11)] if with_core else []
        diff = diff_models(build_model([*core, old]), build_model([*core, new]))
        change = diff.modified[0].changes[0]
        assert (change.kind, change.old, change.new) == (
            "data_type", "String (i=12)", "Double (i=11)",
        )
        expected = "String (i=12) → Double (i=11)"
        assert expected in console_text(diff)
        assert expected in markdown_text(diff)
        assert expected in html_text(diff)
        payload = json.loads(json_text(diff))
        assert payload["modified_nodes"][0]["changes"][0]["old"] == "String (i=12)"
        assert expected in payload["breaking_changes"][0]["reason"]

    def test_custom_datatype_names_keep_uri_identity(self) -> None:
        first = in_namespace(make_data_type("State", 11), "urn:vendor:first")
        second = in_namespace(make_data_type("State", 11), "urn:vendor:second")
        variable = make_variable("StateValue", 6002, data_type=first.node_id)
        diff = diff_models(
            build_model([first, second, variable]),
            build_model([first, second, replace(variable, data_type=second.node_id)]),
        )
        change = diff.modified[0].changes[0]
        assert change.old == "State (nsu=urn:vendor:first;i=11)"
        assert change.new == "State (nsu=urn:vendor:second;i=11)"

    @pytest.mark.parametrize("identifier", ["i=999999", "nsu=urn:custom;i=12", "s=String"])
    def test_unknown_datatype_does_not_get_a_guessed_name(self, identifier) -> None:
        target = ExpandedNodeId.parse(identifier)
        variable = make_variable("Value", 6002, data_type=node_id(11))
        diff = diff_models(
            build_model([variable]),
            build_model([replace(variable, data_type=target)]),
        )
        change = diff.modified[0].changes[0]
        assert change.new == (
            target.identifier.as_xml() if target.namespace == node_id(11).namespace
            else target.as_xml()
        )

    def test_datatype_attribute_is_not_reported_twice(self, tmp_path) -> None:
        old_xml = BASE.replace('DataType="String"', 'DataType="i=12"')
        new_xml = BASE.replace('DataType="String"', 'DataType="i=11"')
        _, _, diff = diff_texts(tmp_path, old_xml, new_xml)
        assert diff.breaking
        assert all(
            change.kind != "attributes"
            for entry in diff.modified for change in entry.changes
        )

    def test_equivalent_datatype_alias_spelling_is_not_a_change(self, tmp_path) -> None:
        _, _, diff = diff_texts(
            tmp_path, BASE, BASE.replace('DataType="String"', 'DataType="i=12"')
        )
        assert diff.is_empty

    def test_loaded_datatype_name_takes_precedence_over_standard_fallback(self) -> None:
        datatype = make_data_type("LoadedStringName", 12)
        variable = make_variable("Value", 6002, data_type=node_id(11))
        diff = diff_models(
            build_model([datatype, variable]),
            build_model([datatype, replace(variable, data_type=datatype.node_id)]),
        )
        assert diff.modified[0].changes[0].new == "LoadedStringName (i=12)"

    def test_browse_name_change_is_breaking(self) -> None:
        old = make_object_type("MachineType", 7100)
        new = replace(old, browse_name=QualifiedName(name="Machine"))
        diff = diff_models(build_model([old]), build_model([new]))
        assert diff.breaking
        assert "BrowseName" in diff.breaking[0].reason

    def test_symbolic_name_change_is_advisory(self) -> None:
        old = make_object_type("Level", 7200)
        new = replace(old, symbolic_name="LevelType")
        diff = diff_models(build_model([old]), build_model([new]))
        # §2.1.2: BrowseName stays the identity; the symbol in generated code needs a redo.
        assert not diff.breaking
        assert "SymbolicName" in diff.advisory[0].reason
        assert diff.modified[0].rule_id == "BP-SymbolicName-Identity"

    def test_inverse_name_change_is_advisory(self) -> None:
        old = make_reference_type("HasOwner", 7300)
        new = replace(old, inverse_name="Owns")
        diff = diff_models(build_model([old]), build_model([new]))
        # §3.2.17: InverseName may change as long as its meaning stays the same.
        assert not diff.breaking
        assert "InverseName" in diff.advisory[0].reason
        assert diff.modified[0].severity == Severity.ADVISORY

    def test_abstract_change_is_breaking(self) -> None:
        old = make_object_type("MachineType", 7400)
        new = replace(old, is_abstract=True)
        diff = diff_models(build_model([old]), build_model([new]))
        assert diff.breaking
        assert "IsAbstract" in diff.breaking[0].reason

    def test_categories_and_documentation_are_not_breaking(self) -> None:
        old = replace(make_object_type("MachineType", 7500), categories=("Machinery",))
        new = replace(
            old,
            categories=("Machinery", "Diagnostics"),
            documentation="https://example.org/doc",
        )
        diff = diff_models(build_model([old]), build_model([new]))
        assert not diff.breaking
        kinds = {change.kind for change in diff.modified[0].changes}
        assert {"categories", "documentation"} <= kinds

    def test_variable_rank_and_dimensions_are_breaking(self) -> None:
        old = make_variable("Count", 7600, data_type=node_id(6))
        new = replace(old, value_rank=1, array_dimensions=("2",))
        diff = diff_models(build_model([old]), build_model([new]))
        assert len(diff.breaking) == 2

    def test_value_change_is_reported(self) -> None:
        old = make_variable("Count", 7700, data_type=node_id(6))
        diff = diff_models(build_model([old]), build_model([old.with_value("<Int32>7</Int32>")]))
        assert labels(diff.modified) == {"Count"}
        assert not diff.breaking


class TestMethodSignature:
    """§3.2.11: the arguments a method was published with are a contract."""

    @staticmethod
    def signature(diff: SemanticDiff, method: str = "StartJob") -> list[tuple[str, str | None]]:
        """The method-level findings for one method, in report order."""
        entries = [item for item in diff.modified if item.label == method]
        return [
            (change.kind, change.subject)
            for entry in entries
            for change in entry.changes
            if change.area == "method"
        ]

    def test_argument_removal_is_breaking(self, tmp_path) -> None:
        new = BASE.replace(
            """  <UAVariable NodeId="ns=1;i=2202" BrowseName="InputArguments" ParentNodeId="ns=1;i=2201" DataType="Argument" ValueRank="1" ArrayDimensions="0">
    <DisplayName>InputArguments</DisplayName>
    <References>
      <Reference ReferenceType="HasProperty" IsForward="false">ns=1;i=2201</Reference>
    </References>
  </UAVariable>
""",
            "",
        )
        _, _, diff = diff_texts(tmp_path, BASE, new)
        assert diff.breaking
        # §3.2.11 names the method, §3.2.16 names the declaration that disappeared.
        assert {"BP-MethodSignature-Immutable", "BP-Declaration-Removed-Optional"} <= {
            change.rule_id for change in diff.breaking
        }
        assert self.signature(diff) == [("removed", "InputArguments")]

    def test_argument_addition_is_breaking(self, tmp_path) -> None:
        new = BASE.replace(
            '      <Reference ReferenceType="HasProperty" IsForward="false">ns=1;i=2201</Reference>\n    </References>\n  </UAVariable>\n  <UAVariable NodeId="ns=1;i=2203"',
            '      <Reference ReferenceType="HasProperty" IsForward="false">ns=1;i=2201</Reference>\n    </References>\n  </UAVariable>\n  <UAVariable NodeId="ns=1;i=2204" BrowseName="Timeout" ParentNodeId="ns=1;i=2201" DataType="Int32">\n    <DisplayName>Timeout</DisplayName>\n    <References>\n      <Reference ReferenceType="HasProperty" IsForward="false">ns=1;i=2201</Reference>\n    </References>\n  </UAVariable>\n  <UAVariable NodeId="ns=1;i=2203"',
        )
        _, _, diff = diff_texts(tmp_path, BASE, new)
        assert self.signature(diff) == [("added", "Timeout")]

    def test_argument_data_type_change_is_breaking(self, tmp_path) -> None:
        new = BASE.replace(
            'NodeId="ns=1;i=2202" BrowseName="InputArguments" ParentNodeId="ns=1;i=2201" DataType="Argument"',
            'NodeId="ns=1;i=2202" BrowseName="InputArguments" ParentNodeId="ns=1;i=2201" DataType="String"',
        )
        _, _, diff = diff_texts(tmp_path, BASE, new)
        assert diff.breaking
        # §3.2.11 names the method and §3.2.12 names the argument variable that changed.
        assert {change.rule_id for change in diff.breaking} == {
            "BP-MethodSignature-Immutable",
            "BP-DataType-Immutable",
        }
        assert self.signature(diff) == [("changed", "InputArguments")]

    def test_argument_rank_and_dimensions_changes_are_breaking(self, tmp_path) -> None:
        new = BASE.replace(
            'BrowseName="OutputArguments" ParentNodeId="ns=1;i=2201" DataType="Argument" ValueRank="1" ArrayDimensions="0"',
            'BrowseName="OutputArguments" ParentNodeId="ns=1;i=2201" DataType="Argument" ValueRank="-1" ArrayDimensions="2"',
        )
        _, _, diff = diff_texts(tmp_path, BASE, new)
        assert diff.breaking
        assert self.signature(diff) == [("changed", "OutputArguments")]

    def test_argument_list_value_change_is_breaking(self) -> None:
        old_arg = replace(
            make_variable("InputArguments", 7960, data_type=node_id(24136)),
            role=NodeRole.ARGUMENT,
            parent_node_id=node_id(7961),
            value_xml="<ArgumentList><Argument><Name>JobId</Name></Argument></ArgumentList>",
        )
        new_arg = replace(
            old_arg,
            value_xml=(
                "<ArgumentList><Argument><Name>JobId</Name></Argument>"
                "<Argument><Name>Timeout</Name></Argument></ArgumentList>"
            ),
        )
        method = make_method("StartJob", 7961)
        diff = diff_models(build_model([method, old_arg]), build_model([method, new_arg]))
        assert diff.breaking
        assert diff.breaking[0].rule_id == "BP-MethodSignature-Immutable"
        entry = next(item for item in diff.modified if item.label == "StartJob")
        assert self.signature(diff, "StartJob") == [("changed", "InputArguments")]
        change = next(change for change in entry.changes if change.area == "method")
        assert "JobId" in str(change.old) and "Timeout" in str(change.new)

    def test_method_declaration_change_is_breaking(self) -> None:
        old = replace(make_method("StartJob", 7962), method_declaration_id=node_id(2100))
        new = replace(old, method_declaration_id=node_id(2101))
        diff = diff_models(build_model([old]), build_model([new]))
        # The instance now points at a different declared method.
        assert diff.breaking
        assert self.signature(diff, "StartJob") == [("changed", "MethodDeclarationId")]

    def test_argument_display_name_edit_is_not_breaking(self, tmp_path) -> None:
        new = BASE.replace(
            "<DisplayName>InputArguments</DisplayName>", "<DisplayName>Input Arguments</DisplayName>"
        )
        _, _, diff = diff_texts(tmp_path, BASE, new)
        assert not diff.breaking
        assert self.signature(diff) == []


class TestBestPracticeRules:
    """The release checklist of the plan: what gates a release and what only advises."""

    def test_symmetric_change_is_breaking(self) -> None:
        old = replace(make_reference_type("HasOwner", 7800), attributes={"Symmetric": "true"})
        new = replace(old, attributes={"Symmetric": "false"})
        diff = diff_models(build_model([old]), build_model([new]))
        # §3.2.14: Symmetric shall not change; the meaning of the reference flips.
        assert diff.breaking
        assert diff.breaking[0].rule_id == "BP-ReferenceType-Symmetric-Immutable"
        assert [(change.subject, change.kind) for change in diff.modified[0].changes] == [
            ("Symmetric", "attributes")
        ]

    def test_access_level_change_is_information_only(self) -> None:
        old = replace(
            make_variable("Count", 7900, data_type=node_id(6)), attributes={"AccessLevel": "None|Input"}
        )
        new = replace(old, attributes={"AccessLevel": "None|Input|Output"})
        diff = diff_models(build_model([old]), build_model([new]))
        assert not diff.breaking
        assert not diff.advisory
        assert diff.modified[0].severity == Severity.INFO

    def test_hierarchy_move_is_breaking(self) -> None:
        base = Reference(target=node_id(58), type_name="HasSubtype", is_forward=False)
        other = Reference(target=node_id(22), type_name="HasSubtype", is_forward=False)
        diff = diff_models(
            build_model([make_object_type("MachineType", 7910, refs=(base,))]),
            build_model([make_object_type("MachineType", 7910, refs=(other,))]),
        )
        # §3.2.9 and §3.2.10: a type that moved inherits something else.
        assert diff.breaking
        assert diff.breaking[0].rule_id == "BP-Hierarchy-Move"
        assert [change.area for change in diff.modified[0].changes] == ["hierarchy"]

    def test_interface_addition_advises_without_gating(self) -> None:
        old = make_object_type("MachineType", 7920)
        new = make_object_type(
            "MachineType", 7920, refs=(Reference(target=node_id(2100), type_name="HasInterface"),)
        )
        diff = diff_models(build_model([old]), build_model([new]))
        # §3.2.3: the type now carries the interface's declarations; nothing disappeared.
        assert not diff.breaking
        assert {change.label for change in diff.advisory} == {"MachineType"}
        assert diff.modified[0].rule_id == "BP-Interface-Added"

    def test_interface_removal_is_breaking(self) -> None:
        old = make_object_type(
            "MachineType", 7930, refs=(Reference(target=node_id(2100), type_name="HasInterface"),)
        )
        new = make_object_type("MachineType", 7930)
        diff = diff_models(build_model([old]), build_model([new]))
        assert diff.breaking
        assert diff.breaking[0].rule_id == "BP-Interface-Removed"

    def test_optional_declaration_addition_is_information(self) -> None:
        added = in_namespace(
            replace(make_variable("Notes", 5100, rule=ModellingRule.OPTIONAL), parent_node_id=node_id(2200)),
            Machinery_NS,
        )
        diff = diff_models(build_model([]), build_model([added]))
        # §3.2.2: an optional addition widens what consumers may browse.
        assert not diff.breaking
        assert diff.added[0].severity == Severity.INFO
        assert diff.added[0].rule_id == "BP-Declaration-Added"

    def test_mandatory_declaration_addition_advises(self) -> None:
        added = in_namespace(
            replace(
                make_variable("Status", 5101, rule=ModellingRule.MANDATORY), parent_node_id=node_id(2200)
            ),
            Machinery_NS,
        )
        diff = diff_models(build_model([]), build_model([added]))
        # §3.2.1: every instance must now carry the declaration, so consumers see more.
        assert not diff.breaking
        assert diff.added[0].severity == Severity.ADVISORY
        assert diff.added[0].rule_id == "BP-Declaration-Added-Mandatory"

    def test_subtype_insertion_advises(self) -> None:
        parent = in_namespace(make_object_type("MachineType", 2200), Machinery_NS)
        child = in_namespace(
            make_object_type(
                "MachineHealthType",
                2600,
                refs=(
                    Reference(target=parent.node_id, type_name="HasSubtype", is_forward=False),
                ),
            ),
            Machinery_NS,
        )
        diff = diff_models(build_model([parent]), build_model([parent, child]))
        # §3.2.9: a subtype appeared inside the published hierarchy.
        assert not diff.breaking
        assert diff.added[0].severity == Severity.ADVISORY
        assert diff.added[0].rule_id == "BP-Subtype-Inserted"

    def test_metadata_only_version_bump_is_green(self, tmp_path) -> None:
        new = BASE.replace(
            '<String xmlns="http://opcfoundation.org/UA/2008/02/Types.xsd">1.0.1</String>',
            '<String xmlns="http://opcfoundation.org/UA/2008/02/Types.xsd">1.0.2</String>',
        )
        _, _, diff = diff_texts(tmp_path, BASE, new)
        assert not diff.breaking
        assert not diff.advisory
        assert {entry.severity for entry in diff.modified} == {Severity.INFO}

    def test_option_set_text_fix_is_information(self) -> None:
        old = Definition(
            kind="OptionSet",
            option_set_values=(OptionSetValue(name="Idle", value=0, description="not running"),),
        )
        new = Definition(
            kind="OptionSet",
            option_set_values=(OptionSetValue(name="Idle", value=0, description="idle, nothing running"),),
        )
        diff = diff_models(
            build_model([make_data_type("Flags", 9150, definition=old)]),
            build_model([make_data_type("Flags", 9150, definition=new)]),
        )
        assert not diff.breaking
        assert diff.modified[0].rule_id == "BP-Member-Text"

    def test_optional_component_detach_is_breaking(self, tmp_path) -> None:
        new = BASE.replace('      <Reference ReferenceType="HasComponent">ns=1;i=2201</Reference>\n', "")
        _, _, diff = diff_texts(tmp_path, BASE, new)
        # §3.2.16: detaching an optional declaration still breaks a consumer that browsed it.
        assert diff.breaking
        assert "BP-Reference-OptionalChild-Removed" in diff.breaking[0].rule_id


class TestImpactHelper:
    def test_dependents_lists_the_consumers(self) -> None:
        model = build_model(
            [make_data_type("JobInfo", 6001), make_variable("Job", 6002, data_type=node_id(6001))]
        )
        assert dependents(model, node_id(6001)) == ("Job",)

    def test_renumbered_impact_is_measured_in_both_models(self) -> None:
        old_model = build_model(
            [make_data_type("JobInfo", 6001), make_variable("Job", 6002, data_type=node_id(6001))]
        )
        new_model = build_model(
            [make_data_type("JobInfo", 6005), make_variable("Job", 6002, data_type=node_id(6005))]
        )
        diff = diff_models(old_model, new_model)
        apply_impact(diff, old_model, new_model)
        assert labels(diff.renumbered) == {"JobInfo"}
        assert diff.renumbered[0].impact == ("Job",)
        assert total_impact(diff) == 1


class TestRenumberedRendering:
    def test_renumbered_node_renders_in_every_format(self) -> None:
        old_model = build_model([make_object_type("MachineType", 8000)])
        new_model = build_model([make_object_type("MachineType", 8001)])
        diff = diff_models(old_model, new_model, old_source="old.xml", new_source="new.xml")
        assert labels(diff.renumbered) == {"MachineType"}
        # §3.2.17: the identifier moved, so a published consumer loses the node.
        assert diff.breaking
        assert diff.breaking[0].rule_id == "BP-NodeId-Immutable"
        assert "Renumbered: 1" in console_text(diff)
        assert "## Renumbered nodes" in markdown_text(diff)
        assert "<h2>Renumbered nodes</h2>" in html_text(diff)
        assert "old.xml" in html_text(diff)

    def test_breaking_only_report_without_breaking_changes(self) -> None:
        old_model = build_model([make_object_type("MachineType", 8100)])
        new_model = build_model(
            [replace(make_object_type("MachineType", 8100), display_name="Machine Type")]
        )
        diff = diff_models(old_model, new_model)
        text = console_text(diff, breaking_only=True, impact=True)
        assert "Breaking changes" in text
        assert "None" in text
