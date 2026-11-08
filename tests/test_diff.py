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
    make_object,
    make_object_type,
    make_reference_type,
    make_variable,
    node_id,
    spaced,
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
    impacted,
    json_text,
    markdown_text,
    total_impact,
)
from nodeset_linker.diff.model import NamespaceChange, SemanticDiff
from nodeset_linker.models.identifiers import ExpandedNodeId, NodeId, QualifiedName
from nodeset_linker.models.node import (
    Definition,
    EnumValue,
    FieldMetadata,
    OptionSetValue,
    Reference,
    StructureField,
    UAMethod,
    UANode,
    UAVariable,
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


def qualified(node, server_uri: str):
    """A copy of a model node whose identifier points into another server's address space."""
    return replace(
        node,
        node_id=ExpandedNodeId(
            identifier=node.node_id.identifier,
            namespace_uri=node.node_id.namespace_uri,
            server_uri=server_uri,
        ),
    )


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

    def test_attaching_a_mandatory_declaration_is_breaking(self) -> None:
        holder = in_namespace(make_object_type("MachineType", 7904), Machinery_NS)
        level = in_namespace(
            make_variable("Level", 7905, data_type=node_id(6), rule=ModellingRule.MANDATORY),
            Machinery_NS,
        )
        attached = replace(
            holder, references=(Reference(target=level.node_id, type_name="HasComponent"),)
        )
        diff = diff_models(build_model([holder, level]), build_model([attached, level]))
        # §3.2.1: every instance now has to carry the component, so consumers break.
        assert labels(diff.breaking) == {"MachineType"}
        assert diff.breaking[0].rule_id == "BP-Reference-MandatoryChild-Added"

    def test_attaching_an_optional_declaration_is_information(self) -> None:
        holder = in_namespace(make_object_type("MachineType", 7906), Machinery_NS)
        diagnostics = in_namespace(
            make_variable("Diagnostics", 7907, data_type=node_id(6), rule=ModellingRule.OPTIONAL),
            Machinery_NS,
        )
        attached = replace(
            holder, references=(Reference(target=diagnostics.node_id, type_name="HasComponent"),)
        )
        diff = diff_models(build_model([holder, diagnostics]), build_model([attached, diagnostics]))
        assert not diff.breaking
        assert not diff.advisory
        assert [change.rule_id for change in diff.modified[0].changes] == [
            "BP-Reference-OptionalChild-Added"
        ]

    def test_deprecation_reference_added_is_information(self) -> None:
        holder = in_namespace(make_object_type("MachineType", 7908), Machinery_NS)
        marker = in_namespace(make_object_type("MachineryVersion1_02", 7909), Machinery_NS)
        marked = replace(
            holder, references=(Reference(target=marker.node_id, type_name="IsDeprecated"),)
        )
        diff = diff_models(build_model([holder, marker]), build_model([marked, marker]))
        # §14.2: the marker tells consumers which release deprecated the node; nothing breaks.
        assert not diff.breaking
        assert not diff.advisory
        assert [change.rule_id for change in diff.modified[0].changes] == ["BP-Deprecation-Added"]

    def test_deprecation_reference_removed_is_advisory(self) -> None:
        holder = in_namespace(make_object_type("MachineType", 7910), Machinery_NS)
        marker = in_namespace(make_object_type("MachineryVersion1_02", 7911), Machinery_NS)
        marked = replace(
            holder, references=(Reference(target=marker.node_id, type_name="IsDeprecated"),)
        )
        diff = diff_models(build_model([marked, marker]), build_model([holder, marker]))
        assert not diff.breaking
        assert diff.modified[0].severity == Severity.ADVISORY
        assert diff.modified[0].rule_id == "BP-Deprecation-Removed"

    def test_identifier_spelling_of_a_reference_type_is_no_change(self, tmp_path) -> None:
        # The reference type is an identifier, not a name: `HasEncoding` and `i=38` are the
        # same reference, so a document that spells it differently changed nothing.
        new = BASE.replace('ReferenceType="HasEncoding"', 'ReferenceType="i=38"')
        _, _, diff = diff_texts(tmp_path, BASE, new)
        assert diff.is_empty

    def test_reference_type_rename_is_a_change_on_the_declaration(self) -> None:
        old_tag = make_reference_type(
            "HasTag", 3000, refs=(Reference(target=node_id(44), type_name="HasChild", is_forward=False),)
        )
        new_tag = make_reference_type(
            "HasTagged", 3000, refs=(Reference(target=node_id(44), type_name="HasChild", is_forward=False),)
        )
        member = in_namespace(make_object_type("TaggedMember", 7912), Machinery_NS)
        holder = in_namespace(make_object_type("MachineType", 7913), Machinery_NS)
        attached = replace(
            holder,
            references=(
                Reference(target=member.node_id, reference_type=node_id(3000), type_name="HasTag"),
            ),
        )
        retagged = replace(
            holder,
            references=(
                Reference(target=member.node_id, reference_type=node_id(3000), type_name="HasTagged"),
            ),
        )
        diff = diff_models(build_model([old_tag, member, attached]), build_model([new_tag, member, retagged]))
        # The reference still points at the same type, so the holder changed nothing; the
        # renamed declaration is the finding (§3.2.17).
        assert labels(diff.modified) == {"HasTagged"}
        assert diff.breaking[0].rule_id == "BP-BrowseName-Immutable"

    def test_duplicated_reference_that_disappears_is_reported(self) -> None:
        member = in_namespace(make_object_type("TaggedMember", 7914), Machinery_NS)
        holder = in_namespace(make_object_type("MachineType", 7915), Machinery_NS)
        twice = replace(
            holder,
            references=(
                Reference(target=member.node_id, type_name="HasComponent"),
                Reference(target=member.node_id, type_name="HasComponent"),
            ),
        )
        once = replace(holder, references=(Reference(target=member.node_id, type_name="HasComponent"),))
        diff = diff_models(build_model([twice, member]), build_model([once, member]))
        # A `set` used to hide this: one declaration fewer is a change a consumer can see.
        assert labels(diff.modified) == {"MachineType"}
        subjects = [change.subject for change in diff.modified[0].changes]
        assert any(subject.endswith("(2 -> 1)") for subject in subjects)


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

    def test_unambiguous_renumbering_is_reported_as_exact(self) -> None:
        old = build_model([make_object_type("MachineType", 4200)])
        new = build_model([make_object_type("MachineType", 4201)])
        diff = diff_models(old, new)
        assert diff.renumbered[0].confidence == "exact"
        assert json.loads(json_text(diff))["renumbered_nodes"][0]["confidence"] == "exact"
        assert "check it by hand" not in console_text(diff)

    def test_ambiguous_renumbering_group_is_reported_as_a_guess(self) -> None:
        old = build_model(
            [make_object_type("MachineType", 4200), make_object_type("MachineType", 4201)]
        )
        new = build_model(
            [make_object_type("MachineType", 4202), make_object_type("MachineType", 4203)]
        )
        diff = diff_models(old, new)
        # More than one candidate per BrowseName: the pairing is a guess the reader checks.
        assert len(diff.renumbered) == 2
        assert {entry.confidence for entry in diff.renumbered} == {"heuristic"}
        assert "check it by hand" in console_text(diff)
        payload = json.loads(json_text(diff))
        assert {entry["confidence"] for entry in payload["renumbered_nodes"]} == {"heuristic"}

    def test_a_server_qualified_node_is_not_the_unqualified_one(self) -> None:
        # OPC 10000-6: a `ServerUri` on the identifier points into another server's address
        # space, so re-qualifying a node is a different node, not a spelling of the same one.
        old = build_model([make_object_type("MachineType", 4200)])
        new = build_model([qualified(make_object_type("MachineType", 4200), "http://example.org/UA/srv")])
        diff = diff_models(old, new)
        assert labels(diff.removed) == {"MachineType"}
        assert labels(diff.added) == {"MachineType"}
        assert not diff.renumbered
        assert not diff.is_empty

    def test_renumbering_does_not_pair_across_servers(self) -> None:
        # A move that also changes the server qualification is not a renumbering: the node
        # that moved is gone from this address space and a foreign one appeared.
        old = build_model([make_object_type("MachineType", 4200)])
        new = build_model([qualified(make_object_type("MachineType", 4201), "http://example.org/UA/srv")])
        diff = diff_models(old, new)
        assert not diff.renumbered
        assert labels(diff.added) == {"MachineType"}
        assert labels(diff.removed) == {"MachineType"}

    def test_server_qualification_that_stays_is_no_change(self) -> None:
        server = "http://example.org/UA/srv"
        old = build_model([qualified(make_object_type("MachineType", 4200), server)])
        new = build_model([qualified(make_object_type("MachineType", 4200), server)])
        assert diff_models(old, new).is_empty


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
        assert set(payload) - legacy_keys == {"paper", "advisory_changes", "schema_version"}
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

    def test_html_collapses_node_sections_per_namespace(self) -> None:
        # A release spanning several namespaces is folded namespace by namespace (§WP 16).
        other = "http://example.org/Other/"
        third = "http://example.org/Third/"
        old = build_model([in_namespace(make_object_type("MachineType", 4200), Machinery_NS)])
        new = build_model(
            [
                in_namespace(make_object_type("MachineType", 4200), Machinery_NS),
                in_namespace(make_object_type("OtherType", 4201), other),
                in_namespace(make_object_type("ThirdType", 4202), third),
            ]
        )
        text = html_text(diff_models(old, new))
        assert "<details>" in text and "open" not in text
        assert "Other (http://example.org/Other/)" in text
        assert "Third (http://example.org/Third/)" in text
        assert text.count("<h2>Added nodes</h2>") == 1
        # The flat formats stay flat: a terminal reader has no fold to open.
        assert "<details>" not in markdown_text(diff_models(old, new))
        lines = console_text(diff_models(old, new)).splitlines()
        assert any(line.startswith("  OtherType") for line in lines)
        assert any(line.startswith("  ThirdType") for line in lines)

    def test_html_stays_flat_for_one_namespace(self, tmp_path) -> None:
        _, _, diff = diff_texts(tmp_path, BASE, BASE.replace("</UANodeSet>", ADDED_TYPE + "</UANodeSet>"))
        text = html_text(diff)
        assert "<details>" not in text
        assert "MachineHealthType" in text

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


def published_manifest(tmp_path: Path, uris: list[str]) -> Path:
    """A bundle manifest stating which NamespaceUris an earlier release already published (§11.1)."""
    (tmp_path / "manifest.json").write_text(
        json.dumps({"namespaces": [{"uri": uri, "version": "1.0.1"} for uri in uris]}),
        encoding="utf-8",
    )
    return tmp_path


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
        assert "Returning exit code 1: blocking breaking changes found." in result.stderr

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

    def test_advisory_findings_never_gate_by_default(self, tmp_path) -> None:
        old = write(tmp_path, "old.xml", BASE)
        new = write(
            tmp_path,
            "new.xml",
            BASE.replace(
                "    <DisplayName>UnrelatedType</DisplayName>\n    <References>",
                "    <DisplayName>UnrelatedType</DisplayName>\n    <References>\n"
                '      <Reference ReferenceType="HasInterface">ns=1;i=2100</Reference>',
            ),
        )
        # §3.2.3: an added Interface is an advisory, so a release gate stays green on it.
        result = CliRunner().invoke(main, ["diff", str(old), str(new), "--fail-on-breaking"])
        assert result.exit_code == 0, result.output
        assert "BP-Interface-Added" in result.output

        result = CliRunner().invoke(main, ["diff", str(old), str(new), "--fail-on-advisory"])
        assert result.exit_code == 1, result.output
        assert "BP-Interface-Added" in result.output

    def test_no_advisory_hides_the_advisory_findings(self, tmp_path) -> None:
        old = write(tmp_path, "old.xml", BASE)
        new = write(
            tmp_path,
            "new.xml",
            BASE.replace(
                "    <DisplayName>UnrelatedType</DisplayName>\n    <References>",
                "    <DisplayName>UnrelatedType</DisplayName>\n    <References>\n"
                '      <Reference ReferenceType="HasInterface">ns=1;i=2100</Reference>',
            ),
        )
        result = CliRunner().invoke(main, ["diff", str(old), str(new), "--no-advisory"])
        assert result.exit_code == 0, result.output
        assert "BP-Interface-Added" not in result.output
        assert "Advisory:" not in result.output

    def test_no_advisory_keeps_namespace_facts_and_hides_the_namespace_rule(self, tmp_path) -> None:
        # A namespace whose only finding is advisory: totals and the metadata delta are facts a
        # release note states, the rule behind them is a finding and --no-advisory hides it.
        old = write(tmp_path, "old.xml", BASE)
        new = write(
            tmp_path,
            "new.xml",
            BASE.replace(
                'Version="1.0.1" PublicationDate="2024-01-01T00:00:00Z" ModelVersion="1.0.1"',
                'Version="1.0.2" PublicationDate="2024-06-01T00:00:00Z" ModelVersion="1.0.1"',
            ),
        )
        shown = CliRunner().invoke(main, ["diff", str(old), str(new)])
        assert shown.exit_code == 0, shown.output
        assert "BP-Namespace-VersionDrift" in shown.output

        hidden = CliRunner().invoke(main, ["diff", str(old), str(new), "--no-advisory"])
        assert hidden.exit_code == 0, hidden.output
        assert "BP-Namespace-VersionDrift" not in hidden.output
        assert "Advisory:" not in hidden.output
        assert "Namespace changes" in hidden.output
        assert "Nodes: 18 → 18" in hidden.output

    def test_version_drift_never_gates_a_release(self, tmp_path) -> None:
        # Version is display-only (Annex F.2), so a drift is a manual-review note: it can gate
        # with --fail-on-advisory, but never turn into a breaking verdict.
        old = write(tmp_path, "old.xml", BASE)
        new = write(
            tmp_path,
            "new.xml",
            BASE.replace(
                'Version="1.0.1" PublicationDate="2024-01-01T00:00:00Z" ModelVersion="1.0.1"',
                'Version="1.0.2" PublicationDate="2024-01-01T00:00:00Z" ModelVersion="1.0.1"',
            ),
        )
        breaking_gate = CliRunner().invoke(main, ["diff", str(old), str(new), "--fail-on-breaking"])
        assert breaking_gate.exit_code == 0, breaking_gate.output
        assert "BP-Namespace-VersionDrift" in breaking_gate.output

        advisory_gate = CliRunner().invoke(main, ["diff", str(old), str(new), "--fail-on-advisory"])
        assert advisory_gate.exit_code == 1, advisory_gate.output

    def test_a_padded_version_beside_a_semantic_model_version_reports_nothing(
        self, tmp_path
    ) -> None:
        # OPC spells one release twice: the display Version pads its segments, the ModelVersion
        # is SemVer (Annex F.2, §12.41).  1.00.1 and 1.0.1 are one release, so nothing is reported.
        old = write(tmp_path, "old.xml", BASE)
        new = write(
            tmp_path,
            "new.xml",
            BASE.replace(
                'Version="1.0.1" PublicationDate="2024-01-01T00:00:00Z" ModelVersion="1.0.1"',
                'Version="1.00.1" PublicationDate="2024-01-01T00:00:00Z" ModelVersion="1.0.1"',
            ),
        )
        result = CliRunner().invoke(main, ["diff", str(old), str(new)])
        assert result.exit_code == 0, result.output
        assert "BP-Namespace-VersionDrift" not in result.output
        assert "BP-Namespace-ModelVersionSyntax" not in result.output
        assert "Namespace changes" not in result.output

    def test_a_model_version_that_is_not_semver_is_a_hidden_advisory(self, tmp_path) -> None:
        # §12.41 forbids leading zeros in the field consumers compare, so a padded ModelVersion
        # is a finding even though it states the same release as the display Version.
        old = write(tmp_path, "old.xml", BASE)
        new = write(
            tmp_path,
            "new.xml",
            BASE.replace('ModelVersion="1.0.1"', 'ModelVersion="1.00.1"'),
        )
        shown = CliRunner().invoke(main, ["diff", str(old), str(new)])
        assert shown.exit_code == 0, shown.output
        assert "BP-Namespace-ModelVersionSyntax" in shown.output
        assert "BP-Namespace-VersionDrift" not in shown.output

        hidden = CliRunner().invoke(main, ["diff", str(old), str(new), "--no-advisory"])
        assert "BP-Namespace-ModelVersionSyntax" not in hidden.output
        assert "Advisory:" not in hidden.output
        assert "Namespace changes" in hidden.output

    def test_breaking_report_still_shows_an_identifier_move(self, tmp_path) -> None:
        old = write(tmp_path, "old.xml", BASE)
        new = write(tmp_path, "new.xml", BASE.replace('NodeId="ns=1;i=2500"', 'NodeId="ns=1;i=2510"'))
        # §3.2.17: renumbering is breaking, so --breaking must not hide it (gap 22).
        result = CliRunner().invoke(
            main, ["diff", str(old), str(new), "--breaking", "--fail-on-breaking"]
        )
        assert result.exit_code == 1, result.output
        assert "Renumbered nodes" in result.output
        assert "BP-NodeId-Immutable" in result.output

    def test_release_verdict_blocks_a_breaking_extension_of_a_published_namespace(self, tmp_path) -> None:
        old = write(tmp_path, "old.xml", BASE)
        new = write(
            tmp_path,
            "new.xml",
            BASE.replace('<Field Name="Done" Value="2" />', '<Field Name="Done" Value="2" />\n      <Field Name="Paused" Value="3" />'),
        )
        # §11.3: the published URI carries breaking findings, so the release must move to V2/.
        published_manifest(tmp_path, [Machinery_NS])
        result = CliRunner().invoke(
            main,
            ["diff", str(old), str(new), "--release-verdict", "--baseline", str(tmp_path)],
        )
        assert result.exit_code == 1, result.output
        assert "Release verdict" in result.output
        assert "Not reusable" in result.output
        assert "Machinery/V2/" in result.output

    def test_release_verdict_passes_a_compatible_extension(self, tmp_path) -> None:
        old = write(tmp_path, "old.xml", BASE)
        new = write(
            tmp_path,
            "new.xml",
            BASE.replace("<DisplayName>JobDataType</DisplayName>", "<DisplayName>Job Data Type</DisplayName>"),
        )
        published_manifest(tmp_path, [Machinery_NS])
        result = CliRunner().invoke(
            main,
            ["diff", str(old), str(new), "--release-verdict", "--baseline", str(tmp_path)],
        )
        assert result.exit_code == 0, result.output
        assert "Release: Reusable" in result.output

        result = CliRunner().invoke(
            main,
            ["diff", str(old), str(new), "--json", "--release-verdict", "--baseline", str(tmp_path)],
        )
        assert result.exit_code == 0, result.output
        payload = json.loads(result.output)
        assert payload["release"]["reusable"] is True
        assert payload["release"]["baseline"] == str(tmp_path / "manifest.json")

    def test_release_verdict_without_a_baseline_invents_no_judgement(self, tmp_path) -> None:
        old = write(tmp_path, "old.xml", BASE)
        new = write(
            tmp_path,
            "new.xml",
            BASE.replace('<Field Name="Done" Value="2" />', '<Field Name="Done" Value="2" />\n      <Field Name="Paused" Value="3" />'),
        )
        # Nothing states whether the URI was published: the report says so, and the flag alone
        # does not gate — --fail-on-breaking is the gate for an unconfirmed release.
        result = CliRunner().invoke(main, ["diff", str(old), str(new), "--release-verdict"])
        assert result.exit_code == 0, result.output
        assert "baseline unknown" in result.output
        gated = CliRunner().invoke(
            main, ["diff", str(old), str(new), "--release-verdict", "--fail-on-breaking"]
        )
        assert gated.exit_code == 1, gated.output

    def test_release_verdict_reads_a_bundle_manifest(self, tmp_path) -> None:
        old = write(tmp_path, "old.xml", BASE)
        new = write(
            tmp_path,
            "new.xml",
            BASE.replace('<Field Name="JobId" DataType="String" />', '<Field Name="JobId" DataType="Int32" />'),
        )
        published_manifest(tmp_path, [Machinery_NS])
        result = CliRunner().invoke(
            main, ["diff", str(old), str(new), "--release-verdict", "--baseline", str(tmp_path)]
        )
        assert result.exit_code == 1, result.output
        assert "BP-BreakingInReleasedNamespace" in result.output

    def test_missing_baseline_reports_error(self, tmp_path) -> None:
        old = write(tmp_path, "old.xml", BASE)
        result = CliRunner().invoke(
            main, ["diff", str(old), str(old), "--release-verdict", "--baseline", tmp_path / "nope.json"]
        )
        assert result.exit_code == 1
        assert "baseline not found" in result.output

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

    def test_output_file_creates_the_directory_it_lives_in(self, tmp_path) -> None:
        old = write(tmp_path, "old.xml", BASE)
        new = write(tmp_path, "new.xml", BASE.replace("</UANodeSet>", ADDED_TYPE + "</UANodeSet>"))
        out = tmp_path / "dist" / "reports" / "diff.html"
        result = CliRunner().invoke(main, ["diff", str(old), str(new), "--html", "-o", str(out)])
        assert result.exit_code == 0, result.output
        assert out.read_text(encoding="utf-8").startswith("<!DOCTYPE html>")


class TestBundleInput:
    """A diff side may be a split-export bundle: its namespace files are one model."""

    def bundle(self, tmp_path: Path, files: dict[str, str]) -> Path:
        """A split-export bundle: namespace files plus the manifest that names them."""
        directory = tmp_path / "bundle"
        directory.mkdir()
        for name, text in files.items():
            (directory / name).write_text(text, encoding="utf-8")
        (directory / "manifest.json").write_text(
            json.dumps(
                {
                    "export_mode": "split",
                    "namespaces": [{"output_file": name} for name in files],
                }
            ),
            encoding="utf-8",
        )
        return directory

    def test_diff_of_a_bundle_is_the_model_its_merged_form_would_be(self, tmp_path) -> None:
        bundle = self.bundle(tmp_path, {"Machinery.xml": BASE})
        old = write(tmp_path, "old.xml", BASE.replace("</UANodeSet>", REMOVED_TYPE + "</UANodeSet>"))
        for side in (bundle, bundle / "manifest.json"):
            result = CliRunner().invoke(main, ["diff", str(old), str(side)])
            assert result.exit_code == 0, result.output
            assert "LegacyMachineType" in result.output

    def test_a_bundle_that_spells_one_release_twice_reports_nothing(self, tmp_path) -> None:
        # A split-export bundle whose display Version is padded and whose ModelVersion is SemVer
        # describes one release: comparing the bundle with itself must find nothing (gap: the
        # release drift rule read spellings, not releases).
        spelling = BASE.replace(
            'Version="1.0.1" PublicationDate="2024-01-01T00:00:00Z" ModelVersion="1.0.1"',
            'Version="1.04.0" PublicationDate="2024-01-01T00:00:00Z" ModelVersion="1.4.0"',
        )
        bundle = self.bundle(tmp_path, {"Machinery.xml": spelling})
        result = CliRunner().invoke(main, ["diff", str(bundle), str(bundle)])
        assert result.exit_code == 0, result.output
        assert "No semantic differences found" in result.output
        assert "Advisory" not in result.output

    def test_a_bundle_without_a_manifest_is_an_explicit_failure(self, tmp_path) -> None:
        directory = tmp_path / "bundle"
        directory.mkdir()
        (directory / "Machinery.xml").write_text(BASE, encoding="utf-8")
        old = write(tmp_path, "old.xml", BASE)
        result = CliRunner().invoke(main, ["diff", str(old), str(directory)])
        assert result.exit_code == 1
        assert "without manifest.json" in spaced(result.output)

    def test_a_manifest_naming_files_that_are_not_there_is_an_explicit_failure(
        self, tmp_path
    ) -> None:
        bundle = self.bundle(tmp_path, {"Machinery.xml": BASE})
        (bundle / "Machinery.xml").unlink()
        old = write(tmp_path, "old.xml", BASE)
        result = CliRunner().invoke(main, ["diff", str(old), str(bundle)])
        assert result.exit_code == 1
        assert "names files that are not there" in spaced(result.output)

    def test_a_conflicting_bundle_reports_a_merge_conflict_instead_of_aborting(
        self, tmp_path
    ) -> None:
        # Two files of one bundle declare the same identifier differently (§3.2.17).  The
        # review needs the diff, so the merge keeps the first declaration and names the loss.
        kept = (
            '<UANodeSet xmlns="http://opcfoundation.org/UA/">'
            '<UAObject NodeId="i=99" BrowseName="A"/></UANodeSet>'
        )
        dropped = (
            '<UANodeSet xmlns="http://opcfoundation.org/UA/">'
            '<UAVariable NodeId="i=99" BrowseName="B"/></UANodeSet>'
        )
        bundle = self.bundle(tmp_path, {"one.xml": kept, "two.xml": dropped})
        old = write(tmp_path, "old.xml", kept)
        result = CliRunner().invoke(main, ["diff", str(old), str(bundle)])
        assert result.exit_code == 0, result.output
        assert "No semantic differences found." not in result.output
        assert "Advisory: 1" in result.output
        assert "BP-Merge-Conflict" in result.output
        assert "one.xml" in result.output and "two.xml" in result.output

        gated = CliRunner().invoke(main, ["diff", str(old), str(bundle), "--fail-on-advisory"])
        assert gated.exit_code == 1, gated.output

        waived = CliRunner().invoke(
            main, ["diff", str(old), str(bundle), "--fail-on-advisory", "--waive", "BP-Merge-Conflict"]
        )
        assert waived.exit_code == 0, waived.output
        assert "BP-Merge-Conflict" in waived.output and "waived" in waived.output

        hidden = CliRunner().invoke(main, ["diff", str(old), str(bundle), "--no-advisory"])
        assert hidden.exit_code == 0, hidden.output
        assert "BP-Merge-Conflict" not in hidden.output

        payload = json.loads(
            CliRunner().invoke(main, ["diff", str(old), str(bundle), "--json"]).output
        )
        conflict = next(entry for entry in payload["advisory_changes"] if entry["rule_id"] == "BP-Merge-Conflict")
        assert conflict["severity"] == "advisory"
        assert conflict["clause"] == "3.2.17"


class TestScoping:
    """`--namespace`, `--exclude-namespace` and `--node-class` filter both sides."""

    FOREIGN = "http://example.org/Other/"
    ISA_URI = "http://example.org/ISA95-JOBCONTROL_V2/"
    ISA_ALIAS = "ISA95-JOBCONTROL"

    def pair(self, tmp_path: Path) -> tuple[Path, Path]:
        """Old and new that differ by one added DataType and one added Object in another namespace."""
        foreign = '  <UAObject NodeId="nsu=http://example.org/Other/;i=1" BrowseName="OtherThing"/>\n'
        old = write(tmp_path, "old.xml", BASE)
        new = write(
            tmp_path,
            "new.xml",
            BASE.replace("</UANodeSet>", ADDED_TYPE + foreign + "</UANodeSet>"),
        )
        return old, new

    def aliased_pair(self, tmp_path: Path) -> tuple[Path, Path]:
        """New declares a namespace whose ``Alias`` is *not* the tail of its URI."""
        entry = f'    <Uri Alias="{self.ISA_ALIAS}">{self.ISA_URI}</Uri>\n'
        node = f'  <UAObject NodeId="nsu={self.ISA_URI};i=1" BrowseName="JobControlType"/>\n'
        old = write(tmp_path, "old.xml", BASE)
        new = write(
            tmp_path,
            "new.xml",
            BASE.replace("</NamespaceUris>", entry + "  </NamespaceUris>").replace(
                "</UANodeSet>", node + "</UANodeSet>"
            ),
        )
        return old, new

    def test_unscoped_report_shows_every_namespace_and_class(self, tmp_path) -> None:
        old, new = self.pair(tmp_path)
        result = CliRunner().invoke(main, ["diff", str(old), str(new)])
        assert result.exit_code == 0, result.output
        assert "MachineHealthType" in result.output
        assert "OtherThing" in result.output

    def test_namespace_filter_accepts_a_uri_and_its_alias(self, tmp_path) -> None:
        old, new = self.pair(tmp_path)
        for spelling in (Machinery_NS, "Machinery", "machinery"):
            result = CliRunner().invoke(main, ["diff", str(old), str(new), "--namespace", spelling])
            assert result.exit_code == 0, result.output
            assert "MachineHealthType" in result.output, spelling
            assert "OtherThing" not in result.output, spelling

        result = CliRunner().invoke(main, ["diff", str(old), str(new), "--namespace", self.FOREIGN])
        assert result.exit_code == 0, result.output
        assert "OtherThing" in result.output
        assert "MachineHealthType" not in result.output

    def test_declared_alias_is_a_filter_spelling(self, tmp_path) -> None:
        # The report names a namespace by its declared Alias, so that spelling must filter too.
        old, new = self.aliased_pair(tmp_path)
        for spelling in (self.ISA_ALIAS, "isa95-jobcontrol", "ISA95-JOBCONTROL_V2", self.ISA_URI):
            scoped = CliRunner().invoke(main, ["diff", str(old), str(new), "--namespace", spelling])
            assert scoped.exit_code == 0, scoped.output
            assert "JobControlType" in scoped.output, spelling
            assert "MachineHealthType" not in scoped.output, spelling

    def test_namespace_filter_naming_nothing_fails(self, tmp_path) -> None:
        # Filtering both sides to nothing would print "No semantic differences found." and exit 0.
        old, new = self.pair(tmp_path)
        result = CliRunner().invoke(main, ["diff", str(old), str(new), "--namespace", "NoSuchModel"])
        assert result.exit_code == 1
        assert "matches no namespace in either side" in spaced(result.output)
        assert "NoSuchModel" in result.output

    def test_excluding_a_namespace_nothing_declares_is_only_a_notice(self, tmp_path) -> None:
        # Dropping a namespace that is not there changes nothing; the run must still report.
        old, new = self.pair(tmp_path)
        result = CliRunner().invoke(main, ["diff", str(old), str(new), "--exclude-namespace", "Nope"])
        assert result.exit_code == 0, result.output
        assert "MachineHealthType" in result.output
        assert "matches no namespace in either side" in result.output

    def test_excluded_namespace_is_reported_nowhere(self, tmp_path) -> None:
        old, new = self.pair(tmp_path)
        result = CliRunner().invoke(main, ["diff", str(old), str(new), "--exclude-namespace", "Other"])
        assert result.exit_code == 0, result.output
        assert "OtherThing" not in result.output
        assert "http://example.org/Other/" not in result.output

    def test_a_filtered_namespace_cannot_state_release_metadata(self, tmp_path) -> None:
        old = write(tmp_path, "old.xml", BASE)
        new = write(
            tmp_path,
            "new.xml",
            BASE.replace(
                'Version="1.0.1" PublicationDate="2024-01-01T00:00:00Z" ModelVersion="1.0.1"',
                'Version="1.0.2" PublicationDate="2024-06-01T00:00:00Z" ModelVersion="1.0.2"',
            ),
        )
        scoped = CliRunner().invoke(main, ["diff", str(old), str(new), "--exclude-namespace", "Machinery"])
        assert scoped.exit_code == 0, scoped.output
        assert "No semantic differences found." in scoped.output
        assert "Namespace changes" not in scoped.output

    def test_node_class_filter_keeps_only_that_class(self, tmp_path) -> None:
        old, new = self.pair(tmp_path)
        objects = CliRunner().invoke(main, ["diff", str(old), str(new), "--node-class", "Object"])
        assert objects.exit_code == 0, objects.output
        assert "MachineHealthType" not in objects.output
        assert "OtherThing" in objects.output

        types = CliRunner().invoke(main, ["diff", str(old), str(new), "--node-class", "objecttype"])
        assert types.exit_code == 0, types.output
        assert "MachineHealthType" in types.output
        assert "OtherThing" not in types.output

    def test_unknown_node_class_is_an_explicit_failure(self, tmp_path) -> None:
        old, new = self.pair(tmp_path)
        result = CliRunner().invoke(main, ["diff", str(old), str(new), "--node-class", "Nope"])
        assert result.exit_code == 1
        assert "unknown node class" in result.output
        assert "objecttype" in result.output


def test_server_qualified_identifier_containing_a_pipe_survives_the_diff() -> None:
    """``|`` is legal inside a string identifier, so the identity key must not split on it.

    The engine keys nodes by ``(namespaceUri, identifier, serverUri)`` and hands that key to
    target lookups as text.  A ``srv=`` identifier whose string value contains ``|`` used to be
    read back as a different identifier — the report then named a target that is not in the
    model.
    """
    vendor = "urn:vendor/"
    server = "urn:server/"
    first = replace(
        make_object("Part|Axis", 9001),
        node_id=ExpandedNodeId(NodeId.parse("s=part|axis"), vendor, server),
    )
    second = replace(
        make_object("Other|Axis", 9002),
        node_id=ExpandedNodeId(NodeId.parse("s=other|axis"), vendor, server),
    )
    holder = make_object("Holder", 9003, refs=(Reference(target=first.node_id, type_name="HasComponent"),))
    moved = replace(holder, references=(Reference(target=second.node_id, type_name="HasComponent"),))

    old = diff_models(build_model([first, second, holder]), build_model([first, second, moved]))
    entry = {item.label: item for item in old.modified}["Holder"]
    removed = next(item for item in entry.changes if item.kind == "removed")
    added = next(item for item in entry.changes if item.kind == "added")
    assert "Part|Axis" in removed.subject
    assert "srv=urn:server/" in removed.subject
    assert "s=part|axis" in removed.subject
    assert "Other|Axis" in added.subject


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


class TestStructureFieldRules:
    """§3.2.5 and §2.2: fields are a contract, so renames and duplicates stay visible."""

    def _diff(self, number: int, old: Definition, new: Definition) -> SemanticDiff:
        return diff_models(
            build_model([make_data_type("JobInfo", number, definition=old)]),
            build_model([make_data_type("JobInfo", number, definition=new)]),
        )

    def test_field_rename_is_breaking(self) -> None:
        old = structure_of(
            StructureField(name="JobId", data_type=node_id(12)),
            StructureField(name="State", data_type=node_id(6)),
        )
        new = structure_of(
            StructureField(name="JobIdentifier", data_type=node_id(12)),
            StructureField(name="State", data_type=node_id(6)),
        )
        diff = self._diff(9600, old, new)
        # §2.2: the field name is what an encoder and generated code address, so a rename breaks.
        assert diff.breaking
        assert diff.modified[0].rule_id == "BP-Field-Renamed"
        assert [change.kind for change in diff.modified[0].changes] == ["renamed"]
        assert "Renamed: JobId → JobIdentifier" in console_text(diff)

    def test_rename_guess_is_advisory(self) -> None:
        old = structure_of(
            StructureField(name="First", data_type=node_id(12)),
            StructureField(name="Second", data_type=node_id(12)),
        )
        new = structure_of(
            StructureField(name="Alpha", data_type=node_id(12)),
            StructureField(name="Beta", data_type=node_id(12)),
        )
        diff = self._diff(9610, old, new)
        # Two candidates cannot be proven to be renames, so this is a hint to check, not a gate.
        assert not diff.breaking
        assert diff.modified[0].severity == Severity.ADVISORY
        assert diff.modified[0].rule_id == "BP-Field-Rename-Guess"

    def test_rename_that_also_changes_the_type_stays_removal_and_addition(self) -> None:
        old = structure_of(StructureField(name="JobId", data_type=node_id(12)))
        new = structure_of(StructureField(name="JobIdentifier", data_type=node_id(9)))
        diff = self._diff(9620, old, new)
        # Folding the type change into a rename would hide it, so both findings stay (§3.2.5).
        assert {change.rule_id for change in diff.breaking} == {
            "BP-StructureField-Removed",
            "BP-StructureField-Added",
        }
        assert all(change.kind != "renamed" for change in diff.modified[0].changes)

    def test_duplicate_field_names_are_advisory(self) -> None:
        doubled = structure_of(
            StructureField(name="Progress", data_type=node_id(6)),
            StructureField(name="Progress", data_type=node_id(12)),
        )
        diff = self._diff(9630, structure_of(StructureField(name="Progress", data_type=node_id(6))), doubled)
        assert "BP-Duplicate-Member" in {change.rule_id for change in diff.modified[0].changes}
        duplicate = next(change for change in diff.modified[0].changes if change.kind == "duplicate_member")
        assert duplicate.severity == Severity.ADVISORY
        assert "field Progress (2 occurrences)" in duplicate.new

    def test_duplicate_members_are_reported_even_without_a_change(self) -> None:
        doubled = structure_of(
            StructureField(name="State", data_type=node_id(6)),
            StructureField(name="State", data_type=node_id(12)),
        )
        model = build_model([make_data_type("JobInfo", 9640, definition=doubled)])
        diff = diff_models(model, model)
        # A published model that carries two members of one name is worth a hint (§2.2).
        assert diff.modified[0].severity == Severity.ADVISORY
        assert diff.modified[0].rule_id == "BP-Duplicate-Member"

    def test_duplicate_enum_values_are_advisory(self) -> None:
        definition = Definition(
            kind="Enum",
            enum_values=(EnumValue(name="Idle", value=0), EnumValue(name="Idle", value=1)),
        )
        model = build_model([make_data_type("JobStateEnum", 9650, definition=definition)])
        diff = diff_models(model, model)
        assert diff.modified[0].rule_id == "BP-Duplicate-Member"

    def test_allow_sub_types_turned_off_is_breaking(self) -> None:
        field = StructureField(
            name="Payload", data_type=node_id(6), metadata=FieldMetadata(allow_sub_types=True)
        )
        strict = replace(field, metadata=FieldMetadata(allow_sub_types=False))
        diff = self._diff(9660, structure_of(field), structure_of(strict))
        assert diff.breaking
        assert diff.modified[0].rule_id == "BP-Field-AllowSubTypes-Changed"
        assert [change.subject for change in diff.modified[0].changes] == ["Payload AllowSubTypes"]

    def test_allow_sub_types_turned_on_is_information(self) -> None:
        field = StructureField(
            name="Payload", data_type=node_id(6), metadata=FieldMetadata(allow_sub_types=False)
        )
        open_field = replace(field, metadata=FieldMetadata(allow_sub_types=True))
        diff = self._diff(9670, structure_of(field), structure_of(open_field))
        # Widening a field accepts more values; nobody written against the old model breaks.
        assert not diff.breaking
        assert not diff.advisory
        assert diff.modified[0].rule_id == "BP-Field-AllowSubTypes-Changed"

    def test_max_string_length_reduction_is_breaking(self) -> None:
        field = StructureField(
            name="JobId", data_type=node_id(12), metadata=FieldMetadata(max_string_length=10)
        )
        shorter = replace(field, metadata=FieldMetadata(max_string_length=4))
        diff = self._diff(9680, structure_of(field), structure_of(shorter))
        assert diff.breaking
        assert diff.modified[0].rule_id == "BP-Field-MaxStringLength-Reduced"

    def test_max_string_length_appearing_is_breaking(self) -> None:
        field = StructureField(name="JobId", data_type=node_id(12))
        limited = replace(field, metadata=FieldMetadata(max_string_length=20))
        diff = self._diff(9690, structure_of(field), structure_of(limited))
        assert diff.breaking
        assert diff.modified[0].rule_id == "BP-Field-MaxStringLength-Reduced"

    def test_max_string_length_increase_is_information(self) -> None:
        field = StructureField(
            name="JobId", data_type=node_id(12), metadata=FieldMetadata(max_string_length=4)
        )
        longer = replace(field, metadata=FieldMetadata(max_string_length=40))
        diff = self._diff(9700, structure_of(field), structure_of(longer))
        assert not diff.breaking
        assert not diff.advisory
        assert diff.modified[0].rule_id == "BP-Field-MaxStringLength-Raised"


class TestRenamedNodes:
    """A node that came back under another BrowseName is a rename, not add plus remove."""

    def test_renamed_node_is_paired_as_a_rename(self) -> None:
        old = make_variable("Counter", 7970, data_type=node_id(6))
        new = replace(old, node_id=node_id(7971), browse_name=QualifiedName(name="Count"))
        diff = diff_models(build_model([old]), build_model([new]))
        assert not diff.removed
        assert not diff.added
        assert len(diff.renumbered) == 1
        entry = diff.renumbered[0]
        assert entry.confidence == "heuristic"
        assert entry.rule_id == "BP-NodeId-Immutable"
        assert [change.rule_id for change in entry.changes] == ["BP-Node-Renamed"]
        assert "Renamed: Counter → Count" in console_text(diff)

    def test_unrelated_nodes_are_not_paired_as_renames(self) -> None:
        old = make_variable("Counter", 7980, data_type=node_id(6))
        other = make_variable("Alarm", 7981, data_type=node_id(12))
        diff = diff_models(build_model([old]), build_model([other]))
        assert len(diff.removed) == 1
        assert len(diff.added) == 1
        assert not diff.renumbered


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

    def test_attribute_changes_name_the_attribute_they_touched(self) -> None:
        old = make_variable("Count", 7950, data_type=node_id(6))
        new = replace(old, attributes={"AccessLevel": "None|Input|Output", "Historizing": "true"})
        diff = diff_models(build_model([old]), build_model([new]))
        # §3.2.17 splits allowed attributes into one finding each, named by the attribute.
        assert [change.subject for change in diff.modified[0].changes] == [
            "AccessLevel",
            "Historizing",
        ]
        assert {change.rule_id for change in diff.modified[0].changes} == {
            "BP-Attributes-ServerSpecific"
        }
        assert not diff.breaking
        assert not diff.advisory

    def test_extension_change_is_advisory(self) -> None:
        old = replace(make_object_type("MachineType", 7951), extensions_xml=("<Extension>a</Extension>",))
        new = replace(old, extensions_xml=("<Extension>b</Extension>",))
        diff = diff_models(build_model([old]), build_model([new]))
        assert not diff.breaking
        assert diff.modified[0].severity == Severity.ADVISORY
        assert diff.modified[0].rule_id == "BP-Extensions-Changed"
        assert [change.subject for change in diff.modified[0].changes] == ["Extensions"]

    def test_extension_whitespace_is_not_a_change(self) -> None:
        old = replace(make_object_type("MachineType", 7952), extensions_xml=("<Extension>a</Extension>",))
        new = replace(old, extensions_xml=("<Extension>   a   </Extension>",))
        diff = diff_models(build_model([old]), build_model([new]))
        assert diff.is_empty

    def test_role_permission_change_is_information(self) -> None:
        old = replace(make_object_type("MachineType", 7953), role_permissions_xml=("<RolePermissions/>",))
        new = replace(old, role_permissions_xml=("<RolePermissions><Role/></RolePermissions>",))
        diff = diff_models(build_model([old]), build_model([new]))
        assert not diff.breaking
        assert not diff.advisory
        assert diff.modified[0].rule_id == "BP-RolePermissions-Changed"

    def test_value_dependency_that_resolves_elsewhere_is_advisory(self) -> None:
        range_node = make_variable("EURange", 7954, data_type=node_id(6))
        other = make_variable("OtherRange", 7955, data_type=node_id(6))
        old = replace(
            make_variable("Level", 7956, data_type=node_id(6)),
            value_dependencies=(("EURange", range_node.node_id),),
        )
        new = replace(old, value_dependencies=(("EURange", other.node_id),))
        diff = diff_models(build_model([range_node, other, old]), build_model([range_node, other, new]))
        modified = {entry.label: entry for entry in diff.modified}
        assert modified["Level"].severity == Severity.ADVISORY
        assert [change.subject for change in modified["Level"].changes] == ["ValueDependencies"]
        assert not diff.breaking

    def test_unresolved_value_dependency_is_not_a_change(self) -> None:
        external = ExpandedNodeId.parse("nsu=urn:external;i=9")
        old = replace(make_variable("Level", 7957, data_type=node_id(6)),
                      value_dependencies=(("EURange", external),))
        new = replace(old, value_dependencies=(("EURange", external),))
        diff = diff_models(build_model([old]), build_model([new]))
        assert diff.is_empty

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
        # §3.2.11: the argument's DataType *is* the method signature, so the method entry
        # carries the break once instead of also naming the argument variable.
        assert {change.rule_id for change in diff.breaking} == {"BP-MethodSignature-Immutable"}
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

    @staticmethod
    def _argument(number: int, refs: tuple[Reference, ...] = ()) -> UAVariable:
        return replace(
            make_variable("InputArguments", number, data_type=node_id(24136), refs=refs),
            role=NodeRole.ARGUMENT,
            parent_node_id=node_id(number - 1),
        )

    @staticmethod
    def _described(method_number: int) -> tuple[UAMethod, UAVariable, UANode]:
        """A method, its argument and the description node hanging off that argument (§5)."""
        method = make_method("StartJob", method_number)
        description = make_object("InputArgumentsDescription", method_number + 2)
        described = TestMethodSignature._argument(
            method_number + 1,
            (Reference(target=node_id(method_number + 2), type_name="HasArgumentDescription"),),
        )
        return method, described, description

    def test_argument_description_addition_is_information(self) -> None:
        method, described, description = self._described(7963)
        diff = diff_models(
            build_model([method, self._argument(7964)]),
            build_model([method, described, description]),
        )
        # §5: a HasArgumentDescription documents an argument the method already has, so the
        # call contract is untouched and the release stays green.
        assert not diff.breaking
        entry = next(item for item in diff.modified if item.label == "InputArguments")
        assert [(change.kind, change.area) for change in entry.changes] == [("added", "reference")]
        assert [verdict.rule_id for verdict in entry.all_verdicts()] == ["BP-MethodArg-Metadata"]
        assert [verdict.severity for verdict in entry.all_verdicts()] == [Severity.INFO]

    def test_argument_description_removal_is_information(self) -> None:
        method, described, description = self._described(7963)
        diff = diff_models(
            build_model([method, described, description]),
            build_model([method, self._argument(7964)]),
        )
        # Only the documentation a browser could follow disappeared (§5); the argument stays.
        assert not diff.breaking
        entry = next(item for item in diff.modified if item.label == "InputArguments")
        assert [verdict.rule_id for verdict in entry.all_verdicts()] == ["BP-MethodArg-Metadata"]
        assert [verdict.severity for verdict in entry.all_verdicts()] == [Severity.INFO]

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


CONSUMER = """<?xml version="1.0" encoding="utf-8"?>
<UANodeSet xmlns="http://opcfoundation.org/UA/2011/03/UANodeSet.xsd">
  <NamespaceUris>
    <Uri>http://opcfoundation.org/UA/Machinery/</Uri>
  </NamespaceUris>
  <UAObjectType NodeId="ns=1;i=3000" BrowseName="1:PumpStatusInstance" SymbolicName="PumpStatusInstance">
    <DisplayName>PumpStatusInstance</DisplayName>
    <References>
      <Reference ReferenceType="HasInterface">ns=1;i=2100</Reference>
    </References>
  </UAObjectType>
</UANodeSet>
"""


class TestImpactIdentifiers:
    def test_impact_reports_the_identifier_of_every_consumer(self) -> None:
        model = build_model(
            [make_data_type("JobInfo", 6001), make_variable("Job", 6002, data_type=node_id(6001))]
        )
        # A label cannot be resolved: the same BrowseName in two namespaces is two nodes.
        assert impacted(model, node_id(6001)) == (node_id(6002),)

    def test_json_separates_identifiers_from_labels(self) -> None:
        old_model = build_model(
            [make_data_type("JobInfo", 6001), make_variable("Job", 6002, data_type=node_id(6001))]
        )
        new_model = build_model([make_variable("Job", 6002, data_type=node_id(6001))])
        diff = diff_models(old_model, new_model)
        apply_impact(diff, old_model, new_model)
        entry = json.loads(json_text(diff))["removed_nodes"][0]
        assert entry["label"] == "JobInfo"
        assert entry["impacted_nodes"] == [node_id(6002).as_xml()]
        assert entry["impacted_labels"] == ["Job"]
        assert entry["impact_count"] == 1

    def test_inheritance_is_followed_for_the_near_generations_only(self) -> None:
        def subtype(name: str, number: int, parent: int):
            return make_object_type(
                name, number, refs=(Reference(target=node_id(parent), type_name="HasSubtype"),)
            )

        model = build_model(
            [
                make_object_type("Base", 7000),
                subtype("Generation1", 7001, 7000),
                subtype("Generation2", 7002, 7001),
                subtype("Generation3", 7003, 7002),
                subtype("Generation4", 7004, 7003),
            ]
        )
        assert dependents(model, node_id(7000)) == ("Generation1", "Generation2", "Generation3")
        assert dependents(model, node_id(7000), inheritance_depth=10) == (
            "Generation1",
            "Generation2",
            "Generation3",
            "Generation4",
        )

    def test_impact_limit_lists_a_bounded_list_but_counts_every_node(self, tmp_path) -> None:
        old_model, new_model, diff = diff_texts(tmp_path, BASE, remove_node(BASE, "ns=1;i=2100"))
        apply_impact(diff, old_model, new_model)
        assert total_impact(diff) == 5
        diff.impact_limit = 2
        text = console_text(diff, impact=True)
        assert "Impact: MSSControllerInstance" in text
        assert "... and 3 more" in text
        removed = json.loads(json_text(diff))["removed_nodes"]
        entry = next(e for e in removed if e["label"] == "IMachineStatus")
        assert len(entry["impacted_nodes"]) == 2
        assert len(entry["impacted_labels"]) == 2
        assert entry["impact_count"] == 5

    def test_context_counts_consumers_outside_the_two_files(self, tmp_path) -> None:
        old = write(tmp_path, "old.xml", BASE)
        new = write(tmp_path, "new.xml", remove_node(BASE, "ns=1;i=2100"))
        consumer = write(tmp_path, "consumer.xml", CONSUMER)
        runner = CliRunner()
        plain = runner.invoke(
            main, ["diff", str(old), str(new), "--impact", "--json", "-o", str(tmp_path / "a.json")]
        )
        assert plain.exit_code == 0
        without = json.loads((tmp_path / "a.json").read_text(encoding="utf-8"))["removed_nodes"]
        baseline = next(e for e in without if e["label"] == "IMachineStatus")
        assert baseline["impact_count"] == 5

        widened = runner.invoke(
            main,
            [
                "diff",
                str(old),
                str(new),
                "--impact",
                "--context",
                str(consumer),
                "--json",
                "-o",
                str(tmp_path / "b.json"),
            ],
        )
        assert widened.exit_code == 0
        with_context = json.loads((tmp_path / "b.json").read_text(encoding="utf-8"))["removed_nodes"]
        widened_entry = next(e for e in with_context if e["label"] == "IMachineStatus")
        assert widened_entry["impact_count"] == 6
        assert "PumpStatusInstance" in widened_entry["impacted_labels"]

    def test_context_file_must_exist(self, tmp_path) -> None:
        old = write(tmp_path, "old.xml", BASE)
        new = write(tmp_path, "new.xml", BASE)
        missing = tmp_path / "gone.xml"
        runner = CliRunner()
        result = runner.invoke(main, ["diff", str(old), str(new), "--context", str(missing)])
        assert result.exit_code != 0
        assert "no nodeset file at" in result.output


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
