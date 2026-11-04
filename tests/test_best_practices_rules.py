"""Hierarchy rules of the Best Practices paper (§3.2.3, §3.2.9, §3.2.10, §3.2.13, §3.2.15).

These findings depend on where a node sits in a type tree, not on the node itself: a new type
above an existing one, an Interface that adds obligations, or a declaration that changed its
type definition.  Namespace release metadata (§11.1, §11.2, §11.3) belongs to the same class of
rules: it says what a release *is*, without changing the information model.  The release verdict
(§3.3, §11.3) then answers the one question a release manager has: may this NamespaceUri be
reused, or must the release publish a versioned one?
"""

from __future__ import annotations

import dataclasses
import json

import pytest
from tests.conftest import (
    UA_NS,
    Machinery_NS,
    enum_definition,
    make_data_type,
    make_object_type,
    make_reference_type,
    make_variable,
    node_id,
    structure_definition,
)

from nodeset_linker.diff import (
    Baseline,
    Severity,
    console_text,
    diff_models,
    html_text,
    json_text,
    load_baseline,
    markdown_text,
    release_text,
    release_verdict,
)
from nodeset_linker.models.identifiers import ExpandedNodeId, QualifiedName
from nodeset_linker.models.namespace import ModelInfo, Namespace
from nodeset_linker.models.node import Reference
from nodeset_linker.models.node_class import ModellingRule
from nodeset_linker.repository.model import SemanticModel, build_model, load_nodesets


def replace(node, **changes):
    """A copy of a model node with one attribute changed."""
    return dataclasses.replace(node, **changes)


def in_machinery(node):
    """A copy of a model node in the companion namespace rather than the OPC UA namespace."""
    return replace(node, node_id=node_id(int(node.node_id.identifier.value), Machinery_NS))


def subtype_of(node: object, parent: object) -> object:
    """A copy of a type that declares its parent as an inverse HasSubtype."""
    reference = Reference(target=_id(parent), type_name="HasSubtype", is_forward=False)
    return replace(node, references=(reference,))


def _id(node: object) -> ExpandedNodeId:
    return node.node_id  # type: ignore[union-attr]


def _rule_ids(entry) -> set[str]:
    return {change.rule_id for change in entry.changes}


class TestHierarchyPlacement:
    """§3.2.9: subtypes are welcome, new types inside the hierarchy are not."""

    def test_inserted_supertype_advises(self) -> None:
        old_parent = in_machinery(
            make_object_type(
                "MachineType",
                2200,
                refs=(Reference(target=node_id(58), type_name="HasSubtype", is_forward=False),),
            )
        )
        holder = in_machinery(
            subtype_of(make_object_type("MachineGroupType", 2150), old_parent)
        )
        new_parent = replace(
            old_parent,
            references=(Reference(target=_id(holder), type_name="HasSubtype", is_forward=False),),
        )
        diff = diff_models(build_model([old_parent]), build_model([new_parent, holder]))
        # A new type above MachineType is a modelling decision, but it adds nothing.
        assert not diff.breaking
        assert {change.kind for change in diff.modified[0].changes} == {"inserted"}
        assert _rule_ids(diff.modified[0]) == {"BP-Hierarchy-Insertion"}

    def test_new_type_inside_event_hierarchy_is_breaking(self) -> None:
        events = make_object_type("BaseEventType", 823)
        alarm = in_machinery(subtype_of(make_object_type("AlarmType", 2400), events))
        insert = in_machinery(subtype_of(make_object_type("AlarmConditionBase", 2450), events))
        moved = replace(
            alarm,
            references=(Reference(target=_id(insert), type_name="HasSubtype", is_forward=False),),
        )
        diff = diff_models(
            build_model([events, alarm]),
            build_model([events, moved, insert]),
        )
        # §3.2.9 forbids new types inside the event hierarchy: filtering breaks for consumers.
        assert diff.breaking
        assert {change.rule_id for change in diff.breaking} == {"BP-EventHierarchy-Insertion"}
        assert "AlarmType" in diff.breaking[0].label

    def test_inserted_type_without_base_stays_information(self) -> None:
        parent = in_machinery(make_object_type("MachineType", 2200))
        child = in_machinery(subtype_of(make_object_type("MachineHealthType", 2600), parent))
        diff = diff_models(build_model([parent]), build_model([parent, child]))
        assert not diff.breaking
        assert diff.added[0].severity == Severity.ADVISORY
        assert diff.added[0].rule_id == "BP-Subtype-Inserted"


class TestSupertypeContract:
    """§3.2.10: a supertype has to carry everything its subtypes already promise."""

    def test_new_supertype_must_carry_subtype_fields(self) -> None:
        job = in_machinery(
            subtype_of(
                make_data_type(
                    "JobDataType",
                    2002,
                    definition=structure_definition(
                        (("JobId", node_id(6)), ("State", node_id(2003)), ("Progress", node_id(9)))
                    ),
                ),
                make_data_type("DataTypeHolder", 24),
            )
        )
        base = in_machinery(
            make_data_type("JobBase", 2010, definition=structure_definition((("JobId", node_id(6)),)))
        )
        moved = replace(
            job,
            references=(Reference(target=_id(base), type_name="HasSubtype", is_forward=False),),
        )
        diff = diff_models(build_model([job]), build_model([moved, base]))
        assert diff.breaking
        assert "BP-Supertype-Inconsistent" in {change.rule_id for change in diff.breaking}
        reasons = [change.reason for change in diff.breaking]
        assert any("does not define field State" in reason for reason in reasons)
        assert any("does not define field Progress" in reason for reason in reasons)

    def test_new_supertype_must_match_field_contracts(self) -> None:
        job = in_machinery(
            subtype_of(
                make_data_type(
                    "JobDataType",
                    2002,
                    definition=structure_definition((("JobId", node_id(8)),)),
                ),
                make_data_type("DataTypeHolder", 24),
            )
        )
        base = in_machinery(
            make_data_type("JobBase", 2010, definition=structure_definition((("JobId", node_id(6)),)))
        )
        moved = replace(
            job,
            references=(Reference(target=_id(base), type_name="HasSubtype", is_forward=False),),
        )
        diff = diff_models(build_model([job]), build_model([moved, base]))
        assert diff.breaking
        assert any("field JobId as" in change.reason for change in diff.breaking)

    def test_new_supertype_must_carry_enum_values(self) -> None:
        level = in_machinery(
            subtype_of(
                make_data_type("LevelEnum", 2002, definition=enum_definition(("Low", "VeryHigh", "High"))),
                make_data_type("DataTypeHolder", 24),
            )
        )
        base = in_machinery(
            make_data_type("LevelBaseEnum", 2010, definition=enum_definition(("Low", "High")))
        )
        moved = replace(
            level,
            references=(Reference(target=_id(base), type_name="HasSubtype", is_forward=False),),
        )
        diff = diff_models(build_model([level]), build_model([moved, base]))
        assert "BP-Supertype-Inconsistent" in {change.rule_id for change in diff.breaking}
        assert any(
            "does not define enumeration value VeryHigh" in change.reason
            for change in diff.breaking
        )
        # §3.2.10: numeric values of a supertype must stay out of the way of its subtypes.
        assert "BP-Supertype-EnumConflict" in {change.rule_id for change in diff.breaking}
        assert any("High: supertype 1, subtype 2" in change.reason for change in diff.breaking)

    def test_subtype_members_the_supertype_carryes_are_green(self) -> None:
        level = in_machinery(
            subtype_of(
                make_data_type("LevelEnum", 2002, definition=enum_definition(("Low", "High"))),
                make_data_type("DataTypeHolder", 24),
            )
        )
        base = in_machinery(
            make_data_type("LevelBaseEnum", 2010, definition=enum_definition(("Low", "High", "VeryHigh")))
        )
        moved = replace(
            level,
            references=(Reference(target=_id(base), type_name="HasSubtype", is_forward=False),),
        )
        diff = diff_models(build_model([level]), build_model([moved, base]))
        assert not diff.breaking


class TestReferenceTypeHierarchy:
    """§3.2.15: ReferenceTypes may be added below, never inserted or re-parented."""

    def test_hierarchical_flip_is_breaking(self) -> None:
        hierarchical = make_reference_type("HierarchicalReference", 824)
        plain = in_machinery(make_reference_type("HasUnsortedReference", 900))
        old = in_machinery(
            subtype_of(make_reference_type("HasOwner", 7300), hierarchical)
        )
        new = replace(
            old,
            references=(Reference(target=_id(plain), type_name="HasSubtype", is_forward=False),),
        )
        diff = diff_models(
            build_model([hierarchical, old]),
            build_model([hierarchical, plain, new]),
        )
        assert diff.breaking
        assert {change.rule_id for change in diff.breaking} == {"BP-RT-Hierarchy-Moved"}

    def test_reference_type_inside_hierarchy_is_breaking(self) -> None:
        parent = in_machinery(make_reference_type("HasChild", 7350))
        owner = in_machinery(subtype_of(make_reference_type("HasOwner", 7300), parent))
        inserted = in_machinery(subtype_of(make_reference_type("HasOwnedJob", 7360), parent))
        diff = diff_models(
            build_model([parent, owner]),
            build_model([parent, owner, inserted]),
        )
        # The new type sits between HasChild and HasOwner, so filtering changes for consumers.
        assert diff.breaking
        assert {entry.rule_id for entry in diff.added} == {"BP-RT-NewInHierarchy"}
        assert diff.added[0].severity == Severity.BREAKING

    def test_leaf_reference_type_is_allowed(self) -> None:
        parent = in_machinery(make_reference_type("HasChild", 7350))
        added = in_machinery(subtype_of(make_reference_type("HasTag", 7360), parent))
        diff = diff_models(build_model([parent]), build_model([parent, added]))
        # §3.2.15 allows a ReferenceType at the bottom of the hierarchy.
        assert not diff.breaking
        assert diff.added[0].severity == Severity.ADVISORY


class TestInterfaceObligations:
    """§3.2.3: adding an Interface is fine only while it adds no mandatory declarations."""

    def _interface(self, rule: ModellingRule) -> tuple:
        declaration = in_machinery(
            replace(make_variable("Status", 2101, rule=rule), parent_node_id=node_id(2100, Machinery_NS))
        )
        interface = in_machinery(
            make_object_type(
                "HasReportingType",
                2100,
                refs=(Reference(target=_id(declaration), type_name="HasComponent"),),
            )
        )
        return interface, declaration

    def test_interface_without_mandatory_declarations_is_green(self) -> None:
        interface, declaration = self._interface(ModellingRule.OPTIONAL)
        machine = in_machinery(make_object_type("MachineType", 2200))
        with_interface = replace(
            machine,
            references=(Reference(target=_id(interface), type_name="HasInterface"),),
        )
        diff = diff_models(
            build_model([interface, declaration, machine]),
            build_model([interface, declaration, with_interface]),
        )
        assert not diff.breaking
        assert diff.modified[0].severity == Severity.ADVISORY

    def test_interface_with_mandatory_declarations_is_breaking(self) -> None:
        interface, declaration = self._interface(ModellingRule.MANDATORY)
        machine = in_machinery(make_object_type("MachineType", 2200))
        with_interface = replace(
            machine,
            references=(Reference(target=_id(interface), type_name="HasInterface"),),
        )
        diff = diff_models(
            build_model([interface, declaration, machine]),
            build_model([interface, declaration, with_interface]),
        )
        assert diff.breaking
        assert {change.rule_id for change in diff.breaking} == {"BP-Interface-Mandatory-Added"}
        assert "makes Status mandatory" in diff.breaking[0].reason


class TestTypeDefinition:
    """§3.2.13: an InstanceDeclaration may refine to a subtype only when nothing is added."""

    def _variable(self, type_definition: int) -> tuple:
        holder = in_machinery(make_object_type("MachineType", 2200))
        subtype = in_machinery(subtype_of(make_object_type("MachineHealthType", 2600), holder))
        variable = in_machinery(
            replace(
                make_variable("Health", 3001, data_type=node_id(6)),
                references=(
                    Reference(target=node_id(type_definition, Machinery_NS), type_name="HasTypeDefinition"),
                ),
            )
        )
        return holder, subtype, variable

    def test_refining_to_a_subtype_is_information(self) -> None:
        holder, subtype, old = self._variable(2200)
        new = replace(
            old,
            references=(Reference(target=_id(subtype), type_name="HasTypeDefinition"),),
        )
        diff = diff_models(
            build_model([holder, subtype, old]),
            build_model([holder, subtype, new]),
        )
        assert not diff.breaking
        assert {change.rule_id for change in diff.modified[0].changes} == {"BP-TypeDefinition-Refined"}
        assert diff.modified[0].changes[-1].verdict.severity == Severity.INFO

    def test_moving_to_an_unrelated_type_is_breaking(self) -> None:
        holder, subtype, old = self._variable(2200)
        unrelated = in_machinery(make_object_type("UnrelatedType", 2500))
        new = replace(
            old,
            references=(Reference(target=_id(unrelated), type_name="HasTypeDefinition"),),
        )
        diff = diff_models(
            build_model([holder, subtype, unrelated, old]),
            build_model([holder, subtype, unrelated, new]),
        )
        assert diff.breaking
        assert "BP-TypeDefinition-Moved" in {change.rule_id for change in diff.breaking}

    def test_refinement_that_adds_a_mandatory_declaration_is_breaking(self) -> None:
        holder, subtype, old = self._variable(2200)
        required = in_machinery(
            replace(
                make_variable("Status", 2601, rule=ModellingRule.MANDATORY),
                parent_node_id=node_id(2600, Machinery_NS),
            )
        )
        richer_subtype = replace(
            subtype,
            references=(
                Reference(target=_id(holder), type_name="HasSubtype", is_forward=False),
                Reference(target=_id(required), type_name="HasComponent"),
            ),
        )
        new = replace(
            old,
            references=(Reference(target=_id(richer_subtype), type_name="HasTypeDefinition"),),
        )
        diff = diff_models(
            build_model([holder, subtype, old]),
            build_model([holder, richer_subtype, required, new]),
        )
        assert "BP-TypeDefinition-Moved" in {entry.rule_id for entry in diff.added} | {
            change.rule_id for change in diff.breaking
        }

    def test_refinement_that_narrows_the_datatype_is_breaking(self) -> None:
        holder, subtype, old = self._variable(2200)
        tags = in_machinery(make_data_type("TagList", 2010))
        short_tags = in_machinery(subtype_of(make_data_type("ShortTagList", 2011), tags))
        narrowed = replace(old, data_type=_id(tags))
        new = replace(
            narrowed,
            data_type=_id(short_tags),
            references=(Reference(target=_id(subtype), type_name="HasTypeDefinition"),),
        )
        diff = diff_models(
            build_model([holder, subtype, tags, narrowed]),
            build_model([holder, subtype, tags, short_tags, new]),
        )
        # §3.2.13: refining the type definition may not also refine the DataType (§3.2.12).
        assert "BP-TypeDefinition-Moved" in {change.rule_id for change in diff.breaking}

    def test_parent_node_move_stays_breaking(self) -> None:
        holder = in_machinery(make_object_type("MachineType", 2200))
        other = in_machinery(make_object_type("OtherType", 2700))
        old = in_machinery(
            replace(make_variable("Health", 3001), parent_node_id=_id(holder))
        )
        new = replace(old, parent_node_id=_id(other))
        diff = diff_models(
            build_model([holder, other, old]),
            build_model([holder, other, new]),
        )
        assert diff.breaking
        assert {change.rule_id for change in diff.breaking} == {"BP-Hierarchy-Move"}


def test_bare_node_without_name_still_classifies() -> None:
    """The hierarchy rules must not crash on nodes that carry no BrowseName."""
    node = replace(
        make_object_type("MachineType", 2200),
        browse_name=QualifiedName(name="MachineType"),
    )
    orphan = replace(
        node,
        references=(
            Reference(target=node_id(99999, Machinery_NS), type_name="HasSubtype", is_forward=False),
        ),
    )
    diff = diff_models(build_model([node]), build_model([orphan]))
    assert {change.rule_id for change in diff.breaking} == {"BP-Hierarchy-Move"}


def namespace(uri: str = Machinery_NS, **metadata: str) -> Namespace:
    """Namespace metadata as the Model table of a release declares it."""
    return Namespace(uri=uri, **metadata)


def model_of(nodes: list[object], uri: str = Machinery_NS, **metadata: str) -> SemanticModel:
    """An in-memory model whose namespace carries release metadata."""
    return build_model(list(nodes), {uri: namespace(uri, **metadata)})


def diff_metadata(old_metadata: dict[str, str], new_metadata: dict[str, str]):
    """Diff of two releases that differ only in their namespace metadata."""
    node = in_machinery(make_object_type("MachineType", 2200))
    return diff_models(
        model_of([node], **old_metadata),
        model_of([node], **new_metadata),
    )


def namespace_document(uri: str, version: str) -> str:
    """A nodeset that declares one namespace and one Model entry, without any nodes."""
    return (
        '<?xml version="1.0" encoding="utf-8"?>'
        '<UANodeSet xmlns="http://opcfoundation.org/UA/2011/03/UANodeSet.xsd">'
        f"<NamespaceUris><Uri>{uri}</Uri></NamespaceUris>"
        f'<Models><Model ModelUri="{uri}" Version="{version}" /></Models>'
        "</UANodeSet>"
    )


class TestNamespaceMetadata:
    """§11.1, §11.2, §11.3: release metadata belongs to the release, not to the model."""

    def test_metadata_only_release_move_is_information(self) -> None:
        diff = diff_metadata({"version": "1.0.1"}, {"version": "1.0.2"})
        # No node changed, yet the release moved — that is a fact a release note needs.
        assert diff.is_empty
        entry = diff.namespaces[0]
        assert entry.metadata[0].describe() == "Version: 1.0.1 → 1.0.2"
        assert entry.rule_id == "BP-Namespace-Version"
        assert entry.severity is Severity.INFO
        assert not diff.breaking and not diff.advisory

    def test_moved_namespace_uri_is_breaking(self) -> None:
        node = in_machinery(make_object_type("MachineType", 2200))
        moved_uri = "http://opcfoundation.org/UA2/Machinery/"
        moved = replace(node, node_id=node_id(2200, moved_uri))
        diff = diff_models(model_of([node]), model_of([moved], uri=moved_uri))
        # §11.3: consumers resolve the model under the old URI and find nothing.
        assert diff.breaking_namespaces
        entry = diff.breaking_namespaces[0]
        assert entry.metadata[0].attribute == "uri"
        assert entry.rule_id == "BP-NamespaceUri-Changed"

    def test_new_namespace_with_a_version_suffix_is_not_a_rename(self) -> None:
        node = in_machinery(make_object_type("MachineType", 2200))
        bumped_uri = "http://opcfoundation.org/UA/Machinery/V2/"
        bumped = replace(node, node_id=node_id(2200, bumped_uri))
        diff = diff_models(model_of([node]), model_of([bumped], uri=bumped_uri))
        # §11.3 recommends exactly this spelling: a new namespace, not a renamed one.
        assert not diff.breaking_namespaces
        rules = {entry.rule_id for entry in diff.namespaces}
        assert rules == {"BP-Namespace-Added", "BP-Namespace-Removed"}

    def test_alias_change_is_document_local(self) -> None:
        diff = diff_metadata({"alias": "Machinery"}, {"alias": "Machine"})
        entry = diff.namespaces[0]
        assert entry.rule_id == "BP-Namespace-Alias"
        assert entry.severity is Severity.INFO

    def test_schema_uri_change_is_reported(self) -> None:
        diff = diff_metadata(
            {"xml_schema_uri": "http://opcfoundation.org/UA/Machinery/Types.xsd"},
            {"xml_schema_uri": "http://opcfoundation.org/UA/Machinery/TypesV2.xsd"},
        )
        assert diff.namespaces[0].rule_id == "BP-Namespace-XmlSchema"

    def test_required_model_version_change_is_reported(self) -> None:
        node = in_machinery(make_object_type("MachineType", 2200))
        old = model_of([node])
        new = model_of([node])
        old.models[Machinery_NS] = ModelInfo(
            namespace_uri=Machinery_NS,
            required_models=(UA_NS,),
            required_model_entries=(ModelInfo(namespace_uri=UA_NS, version="1.05.07"),),
        )
        new.models[Machinery_NS] = ModelInfo(
            namespace_uri=Machinery_NS,
            required_models=(UA_NS,),
            required_model_entries=(ModelInfo(namespace_uri=UA_NS, version="1.05.08"),),
        )
        diff = diff_models(old, new)
        deltas = diff.namespaces[0].metadata
        assert {delta.verdict.rule_id for delta in deltas} == {"BP-Namespace-ModelVersion"}
        assert deltas[0].note == UA_NS

    def test_added_dependency_is_reported(self) -> None:
        node = in_machinery(make_object_type("MachineType", 2200))
        old = model_of([node])
        old.models[Machinery_NS] = ModelInfo(namespace_uri=Machinery_NS, required_models=(UA_NS,))
        new = model_of([node])
        new.models[Machinery_NS] = ModelInfo(
            namespace_uri=Machinery_NS, required_models=(UA_NS, "http://opcfoundation.org/UA/DI/")
        )
        diff = diff_models(old, new)
        deltas = diff.namespaces[0].metadata
        assert {delta.verdict.rule_id for delta in deltas} == {"BP-Namespace-RequiredModel"}
        assert "DI" in deltas[0].new

    def test_version_and_model_version_drift_advises(self) -> None:
        diff = diff_metadata(
            {"version": "1.0.1", "model_version": "1.0.1"},
            {"version": "1.0.2", "model_version": "1.0.1"},
        )
        entry = diff.namespaces[0]
        assert "BP-Namespace-VersionDrift" in {delta.verdict.rule_id for delta in entry.metadata}
        assert entry.severity is Severity.ADVISORY
        assert not diff.breaking

    def test_conflicting_metadata_across_documents_advises(self, tmp_path) -> None:
        first = tmp_path / "a.xml"
        second = tmp_path / "b.xml"
        first.write_text(namespace_document(Machinery_NS, "1.0.1"), encoding="utf-8")
        second.write_text(namespace_document(Machinery_NS, "1.0.2"), encoding="utf-8")
        model = load_nodesets([first, second])
        diff = diff_models(model, model)
        # The merged release states a version neither document claimed on its own.
        rules = {delta.verdict.rule_id for delta in diff.namespaces[0].metadata}
        assert rules == {"BP-Namespace-MetadataConflict", "BP-Namespace-ModelPrecedence"}
        assert not diff.breaking

    def test_namespace_metadata_reaches_the_json_report(self) -> None:
        payload = json.loads(json_text(diff_metadata({"version": "1.0.1"}, {"version": "1.0.2"})))
        entry = payload["namespace_changes"][0]
        assert entry["severity"] == "info"
        assert entry["rule_id"] == "BP-Namespace-Version"
        assert entry["metadata"][0]["rule_id"] == "BP-Namespace-Version"
        assert entry["nodes"] == {"old": 1, "new": 1, "delta": 0}

    def test_namespace_metadata_reaches_the_console_report(self) -> None:
        text = console_text(diff_metadata({"version": "1.0.1"}, {"version": "1.0.2"}))
        assert "Version: 1.0.1 → 1.0.2" in text
        assert "BP-Namespace-Version" not in text


def in_namespace(node: object, uri: str) -> object:
    """A copy of a model node under a given NamespaceUri."""
    return replace(node, node_id=node_id(int(node.node_id.identifier.value), uri))  # type: ignore[union-attr]


def enum_release(values: tuple[str, ...], uri: str = Machinery_NS) -> SemanticModel:
    """A one-namespace release whose only content is an Enum with these values."""
    node = in_namespace(make_data_type("JobStateEnum", 2002, definition=enum_definition(values)), uri)
    return model_of([node], uri=uri)


def published(tmp_path, uris: list[str], version: str = "1.0.1") -> Baseline:
    """A bundle manifest stating which NamespaceUris a previous release published."""
    (tmp_path / "manifest.json").write_text(
        json.dumps({"namespaces": [{"uri": uri, "version": version} for uri in uris]}),
        encoding="utf-8",
    )
    return load_baseline(tmp_path)


class TestReleaseVerdict:
    """§3.3, §11.3: may this release reuse its NamespaceUri, or must it publish a new one?"""

    def test_breaking_extension_of_a_published_namespace_publishes_v2(self, tmp_path) -> None:
        diff = diff_models(enum_release(("Low", "High")), enum_release(("Low", "High", "VeryHigh")))
        release = release_verdict(diff, published(tmp_path, [Machinery_NS]))
        assert release.reusable is False
        assert "Not reusable" in release.recommendation
        assert "Machinery/V2/" in release.recommendation
        blocked = release.blocked[0]
        assert blocked.uri == Machinery_NS
        assert blocked.published is True
        assert {found.rule_id for found in blocked.verdicts} == {"BP-BreakingInReleasedNamespace"}
        assert {found.rule_id for found in blocked.breaking} == {"BP-EnumValue-Added"}
        assert "BP-BreakingInReleasedNamespace" in release_text(release)

    def test_compatible_extension_of_a_published_namespace_is_reusable(self, tmp_path) -> None:
        diff = diff_metadata({"version": "1.0.1"}, {"version": "1.0.2"})
        release = release_verdict(diff, published(tmp_path, [Machinery_NS]))
        # A metadata-only move touches no identifier (§11.1), so the URI stays.
        assert release.reusable is True
        assert release.blocked == ()
        entry = release.namespaces[0]
        assert entry.metadata[0].attribute == "version"
        assert "compatible extension" in entry.recommendation

    def test_without_a_baseline_the_verdict_invents_no_judgement(self) -> None:
        diff = diff_models(enum_release(("Low", "High")), enum_release(("Low", "High", "VeryHigh")))
        release = release_verdict(diff)
        assert release.baseline is None
        assert release.namespaces[0].published is None
        # The breaking findings are reported; whether the URI was published is not claimed.
        assert "baseline unknown" in release.namespaces[0].recommendation
        assert json.loads(json_text(diff, release=release.as_json()))["release"]["baseline"] == (
            "baseline unknown"
        )
        assert "No baseline given" in release_text(release)

    def test_a_versioned_namespace_may_carry_breaking_changes(self, tmp_path) -> None:
        bumped_uri = "http://opcfoundation.org/UA/Machinery/V2/"
        diff = diff_models(
            enum_release(("Low", "High"), bumped_uri), enum_release(("Low", "High", "VeryHigh"), bumped_uri)
        )
        release = release_verdict(diff, published(tmp_path, [Machinery_NS]))
        # §11.3: the breaking changes belong in the new namespace, the published one stays.
        assert release.reusable is True
        entry = release.namespaces[0]
        assert entry.uri == bumped_uri
        assert entry.bumped is True
        assert entry.published is False
        assert {found.rule_id for found in entry.verdicts} == {"BP-Namespace-Bump"}

    def test_breaking_changes_on_a_published_versioned_namespace_move_one_major(self, tmp_path) -> None:
        bumped_uri = "http://opcfoundation.org/UA/Machinery/V2/"
        diff = diff_models(
            enum_release(("Low", "High"), bumped_uri), enum_release(("Low", "High", "VeryHigh"), bumped_uri)
        )
        release = release_verdict(diff, published(tmp_path, [bumped_uri]))
        assert release.reusable is False
        assert "V3/" in release.recommendation

    def test_a_moved_namespace_uri_is_not_a_new_release(self, tmp_path) -> None:
        moved_uri = "http://opcfoundation.org/UA2/Machinery/"
        diff = diff_models(enum_release(("Low", "High")), enum_release(("Low", "High"), moved_uri))
        release = release_verdict(diff, published(tmp_path, [Machinery_NS]))
        # The consumers were built on the old URI; renaming it leaves them nothing to resolve.
        assert release.reusable is False
        assert "not a new release" in release.namespaces[0].recommendation

    def test_baseline_reads_a_bundle_manifest_or_a_registry(self, tmp_path) -> None:
        registry = tmp_path / "registry.json"
        registry.write_text(json.dumps([Machinery_NS]), encoding="utf-8")
        assert load_baseline(registry).published == {Machinery_NS: None}
        assert published(tmp_path, [Machinery_NS]).published == {Machinery_NS: "1.0.1"}
        for bad, message in (
            (tmp_path / "absent.json", "baseline not found"),
            (tmp_path / "broken.json", "baseline is not valid JSON"),
        ):
            if bad.name == "broken.json":
                bad.write_text("{not json", encoding="utf-8")
            with pytest.raises(ValueError) as excinfo:
                load_baseline(bad)
            assert message in str(excinfo.value)


def _release_json(diff, tmp_path):
    release = release_verdict(diff, published(tmp_path, [Machinery_NS]))
    return json.loads(json_text(diff, release=release.as_json()))


class TestReleaseReport:
    """The verdict belongs at the top of the report, not buried in per-node findings."""

    def test_release_key_is_added_only_when_asked(self, tmp_path) -> None:
        diff = diff_metadata({"version": "1.0.1"}, {"version": "1.0.2"})
        assert "release" not in json.loads(json_text(diff))
        payload = _release_json(diff, tmp_path)
        assert payload["release"]["reusable"] is True
        assert payload["release"]["namespaces"][0]["uri"] == Machinery_NS
        # The established contract keys stay exactly as they are.
        assert {"old", "new", "paper", "summary", "namespace_changes"} <= set(payload)

    def test_console_and_markdown_lead_with_the_verdict(self, tmp_path) -> None:
        diff = diff_models(enum_release(("Low", "High")), enum_release(("Low", "High", "VeryHigh")))
        release = release_verdict(diff, published(tmp_path, [Machinery_NS]))
        text = console_text(diff, release=release)
        assert "Release verdict" in text
        assert text.index("Release verdict") < text.index("Modified nodes")
        assert "Release: Not reusable" in text
        assert "## Release verdict" in markdown_text(diff, release=release)
        assert "Release verdict" in html_text(diff, release=release)

    def test_an_identical_release_still_answers_the_question(self, tmp_path) -> None:
        diff = diff_models(enum_release(("Low", "High")), enum_release(("Low", "High")))
        assert diff.is_empty
        release = release_verdict(diff, published(tmp_path, [Machinery_NS]))
        text = console_text(diff, release=release)
        assert "Release verdict" in text
        assert "Reusable" in text
