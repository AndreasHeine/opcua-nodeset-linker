"""Hierarchy rules of the Best Practices paper (§3.2.3, §3.2.9, §3.2.10, §3.2.13, §3.2.15).

These findings depend on where a node sits in a type tree, not on the node itself: a new type
above an existing one, an Interface that adds obligations, or a declaration that changed its
type definition.
"""

from __future__ import annotations

import dataclasses

from tests.conftest import (
    Machinery_NS,
    enum_definition,
    make_data_type,
    make_object_type,
    make_reference_type,
    make_variable,
    node_id,
    structure_definition,
)

from nodeset_linker.diff import Severity, diff_models
from nodeset_linker.models.identifiers import ExpandedNodeId, QualifiedName
from nodeset_linker.models.node import Reference
from nodeset_linker.models.node_class import ModellingRule
from nodeset_linker.repository.model import build_model


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
