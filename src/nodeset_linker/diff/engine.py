"""Semantic diff engine: compare model objects, never XML.

Two :class:`~nodeset_linker.repository.model.SemanticModel` instances are compared on
identity ``(namespaceUri, identifier)``.  Nothing here looks at document order, whitespace
or ``NamespaceIndex``: the repository layer already resolved indexes into namespace URIs, so
a reordered or reindexed nodeset compares as identical.

Nodes that moved to a new identifier while keeping namespace, BrowseName and node class are
reported as *renumbered* rather than as an addition plus a removal — that is what a renumbered
export actually means, and it keeps the report about the information model rather than about
the file.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Self

from nodeset_linker.diff.model import (
    BreakingChange,
    Change,
    NamespaceChange,
    NodeEntry,
    NodeModification,
    RenumberedNode,
    SemanticDiff,
)
from nodeset_linker.diff.rules import RuleContext, RuleVerdict, Severity, verdict
from nodeset_linker.graph.engine import OWNERSHIP_REFERENCES
from nodeset_linker.models.dependency import MandatoryLevel
from nodeset_linker.models.identifiers import (
    UA_NAMESPACE_URI,
    ExpandedNodeId,
    NodeId,
    QualifiedName,
)
from nodeset_linker.models.namespace import alias_from_uri
from nodeset_linker.models.node import (
    Definition,
    EnumValue,
    FieldMetadata,
    OptionSetValue,
    Reference,
    StructureField,
    UADataType,
    UANode,
    UAVariable,
    UAVariableType,
    UnionAlternative,
    mandatory_level_of,
)
from nodeset_linker.models.node_class import ModellingRule, NodeClass, NodeRole
from nodeset_linker.models.standard import STANDARD_DATA_TYPE_NAMES
from nodeset_linker.repository.model import SemanticModel

#: Diff identity: namespace URI plus local identifier.  An index is a document property.
Identity = tuple[str, NodeId]

#: Removing one of these references breaks a contract instead of only navigation.
HARD_REFERENCES = frozenset(
    {"HasSubtype", "HasTypeDefinition", "HasInterface", "HasEncoding", "GeneratesEvent"}
)
#: Removing one of these is navigation-only and therefore never breaking.
SOFT_REFERENCES = frozenset({"Organizes", "HasDescription"})
#: Where a node sits in its hierarchy is judged as a hierarchy change (§3.2.9, §3.2.10,
#: §3.2.13, §3.2.15), not as a reference change, so it is reported once.
HIERARCHY_REFERENCES = frozenset({"HasSubtype", "HasTypeDefinition", "HasInterface"})

#: Definition members whose optional attributes still carry semantic meaning.
FIELD_METADATA_LABELS: tuple[tuple[str, str], ...] = (
    ("symbolic_name", "SymbolicName"),
    ("value_rank", "ValueRank"),
    ("array_dimensions", "ArrayDimensions"),
    ("max_string_length", "MaxStringLength"),
    ("allow_sub_types", "AllowSubTypes"),
)

MEMBER_LABELS = {"display_name": "DisplayName", "description": "Description"}

#: Attributes that spell out identity in document-local form.  Their ``NamespaceIndex``
#: changes when a nodeset is merged or re-exported, so they are compared as model fields
#: (BrowseName, SymbolicName, references) instead of as raw XML text.
DOCUMENT_LOCAL_ATTRIBUTES = frozenset(
    {
        "NodeId",
        "BrowseName",
        "DataType",
        "SymbolicName",
        "ParentNodeId",
        "Handle",
        "TypeId",
        "TargetId",
        "DefinitionType",
    }
)


@dataclass(frozen=True, slots=True)
class NamespaceTotals:
    """How much one namespace contributed: nodes, references, data types."""

    nodes: int = 0
    references: int = 0
    datatypes: int = 0


@dataclass(frozen=True, slots=True)
class ModelIndex:
    """A model plus the identity-keyed node lookup the diff needs.

    ``model.nodes`` is keyed by ``ExpandedNodeId``, which also carries ``ServerUri``; the
    diff keys by ``(namespaceUri, identifier)`` only, so the same node written with or
    without a server URI is still the same node.
    """

    model: SemanticModel
    nodes: dict[Identity, UANode]

    @classmethod
    def of(cls, model: SemanticModel) -> Self:
        return cls(model=model, nodes={identity(n.node_id): n for n in model.iter_nodes()})

    def node(self, node_id: ExpandedNodeId) -> UANode | None:
        return self.nodes.get(identity(node_id))

    def label(self, node_id: ExpandedNodeId) -> str:
        """Name plus stable identifier, with offline standard datatype fallbacks."""
        node = self.node(node_id)
        identifier = (
            node_id.identifier.as_xml()
            if node_id.namespace == UA_NAMESPACE_URI and node_id.server_uri is None
            else node_id.as_xml()
        )
        name: str | None = None
        if node is not None:
            name = node.browse_name.name or node.display_name
        if not name and node_id.namespace == UA_NAMESPACE_URI and node_id.server_uri is None:
            name = STANDARD_DATA_TYPE_NAMES.get(identifier)
        return f"{name} ({identifier})" if name and name != identifier else identifier

    def alias(self, uri: str) -> str:
        namespace = self.model.namespaces.get(uri)
        if namespace and namespace.alias:
            return namespace.alias
        return alias_from_uri(uri) or uri


def identity(node_id: ExpandedNodeId) -> Identity:
    return (node_id.namespace, node_id.identifier)


def diff_models(
    old: SemanticModel,
    new: SemanticModel,
    *,
    old_source: str = "",
    new_source: str = "",
) -> SemanticDiff:
    """Compare two semantic models and report the changes of the information model."""
    old_index = ModelIndex.of(old)
    new_index = ModelIndex.of(new)

    gone = sorted(set(old_index.nodes) - set(new_index.nodes), key=_sort_key)
    fresh = sorted(set(new_index.nodes) - set(old_index.nodes), key=_sort_key)
    shared = sorted(set(old_index.nodes) & set(new_index.nodes), key=_sort_key)

    renumbered, removed_ids, added_ids = _pair_renumbering(gone, fresh, old_index, new_index)
    removed_nodes = [old_index.nodes[node_id] for node_id in removed_ids]
    added_nodes = [new_index.nodes[node_id] for node_id in added_ids]

    diff = SemanticDiff(
        old_source=old_source,
        new_source=new_source,
        old_nodes=len(old_index.nodes),
        new_nodes=len(new_index.nodes),
        added=[
            _entry(node, new_index, verdicts=(_entry_verdict(node, new_index),))
            for node in added_nodes
        ],
        removed=[
            _entry(node, old_index, verdicts=(_removal_verdict(node),)) for node in removed_nodes
        ],
        modified=_modified_nodes(shared, old_index, new_index),
        renumbered=renumbered,
        namespaces=_namespace_changes(old_index, new_index),
    )
    diff.breaking = _breaking_changes(diff)
    diff.advisory = _advisory_changes(diff)
    return diff


def _entry_verdict(node: UANode, index: ModelIndex) -> RuleVerdict:
    """The rule verdict for a node that appeared in the new model (§3.2.1, §3.2.2, §3.2.9)."""
    if _is_added_mandatory_declaration(node):
        return verdict("node_added_mandatory", RuleContext(mandatory_target=True))
    if node.node_class.is_type and _base_is_extension(node, index):
        return verdict("subtype_inserted")
    return verdict("node_added", RuleContext(modelling_rule=node.modelling_rule))


def _is_added_mandatory_declaration(node: UANode) -> bool:
    """An InstanceDeclaration that hangs off a type and is not marked Optional (§3.2.1).

    A whole type is not an InstanceDeclaration, so adding a type stays an optional addition
    even though the absence of a modelling rule makes declarations mandatory.
    """
    if not node.node_class.is_instance:
        return False
    if node.parent_node_id is None and node.type_definition is None:
        return False
    return mandatory_level_of(node) is MandatoryLevel.MANDATORY


def _base_is_extension(node: UANode, index: ModelIndex) -> bool:
    """Whether a new type hangs off a companion type of the same release — an inserted subtype."""
    base_identity = _base_type(node)
    if base_identity is None:
        return False
    if base_identity.partition("|")[0] == UA_NAMESPACE_URI:
        return False
    return _lookup(index, base_identity) is not None


def _sort_key(node_identity: Identity) -> tuple[str, str, str]:
    namespace, identifier = node_identity
    return (namespace, identifier.kind.value, identifier.value)


def _pair_renumbering(
    gone: list[Identity],
    fresh: list[Identity],
    old_index: ModelIndex,
    new_index: ModelIndex,
) -> tuple[list[RenumberedNode], list[Identity], list[Identity]]:
    """Pair removed and added nodes that kept namespace, BrowseName and node class."""
    removed_groups: dict[tuple[str, str, NodeClass], list[Identity]] = {}
    added_groups: dict[tuple[str, str, NodeClass], list[Identity]] = {}
    for node_identity in gone:
        key = _move_key(old_index.nodes[node_identity])
        removed_groups.setdefault(key, []).append(node_identity)
    for node_identity in fresh:
        key = _move_key(new_index.nodes[node_identity])
        added_groups.setdefault(key, []).append(node_identity)

    renumbered: list[RenumberedNode] = []
    paired_removed: set[Identity] = set()
    paired_added: set[Identity] = set()
    for key, removed_group in sorted(removed_groups.items()):
        added_group = added_groups.get(key, [])
        pairs = zip(
            sorted(removed_group, key=_sort_key),
            sorted(added_group, key=_sort_key),
            strict=False,
        )
        for old_id, new_id in pairs:
            paired_removed.add(old_id)
            paired_added.add(new_id)
            old_node = old_index.nodes[old_id]
            new_node = new_index.nodes[new_id]
            renumbered.append(
                RenumberedNode(
                    label=_label(new_node),
                    old_node_id=old_node.node_id,
                    new_node_id=new_node.node_id,
                    node_class=key[2].value,
                    namespace=new_index.alias(key[0]),
                    changes=_node_changes(old_node, new_node, old_index, new_index),
                    verdicts=(verdict("identifier_move"),),
                )
            )
    renumbered.sort(key=lambda entry: entry.label)
    return (
        renumbered,
        [node_identity for node_identity in gone if node_identity not in paired_removed],
        [node_identity for node_identity in fresh if node_identity not in paired_added],
    )


def _move_key(node: UANode) -> tuple[str, str, NodeClass]:
    return (node.node_id.namespace, node.browse_name.name, node.node_class)


def _modified_nodes(
    shared: list[Identity], old_index: ModelIndex, new_index: ModelIndex
) -> list[NodeModification]:
    modifications: list[NodeModification] = []
    for node_identity in shared:
        node = new_index.nodes[node_identity]
        changes = _node_changes(old_index.nodes[node_identity], node, old_index, new_index)
        if not changes:
            continue
        modifications.append(
            NodeModification(
                label=_label(node),
                node_id=node.node_id,
                node_class=node.node_class.value,
                namespace=new_index.alias(node.node_id.namespace),
                changes=changes,
            )
        )
    modifications.sort(key=lambda entry: entry.label)
    return modifications


def _entry(
    node: UANode, index: ModelIndex, *, verdicts: tuple[RuleVerdict, ...] = ()
) -> NodeEntry:
    return NodeEntry(
        label=_label(node),
        node_id=node.node_id,
        node_class=node.node_class.value,
        namespace=index.alias(node.node_id.namespace),
        verdicts=verdicts,
    )


def _removal_verdict(node: UANode) -> RuleVerdict:
    """The rule verdict for a node that vanished from the model.

    Types, encodings and placeholders are part of the published contract; so is an
    explicitly ``Mandatory`` declaration.  Nodes of the OPC UA namespace are not this
    model's declarations, and an optional declaration carries no promise.
    """
    if node.node_class.is_type or node.role in {NodeRole.ENCODING, NodeRole.PLACEHOLDER}:
        return verdict("node_removed")
    if node.node_id.namespace == UA_NAMESPACE_URI:
        return verdict("node_removed_standard")
    if _is_explicitly_mandatory(node):
        return verdict("node_removed")
    return verdict("node_removed_optional")


def _is_explicitly_mandatory(node: UANode) -> bool:
    """Only an explicit ``Mandatory``/``MandatoryPlaceholder`` declaration tightens a contract.

    The OPC UA namespace declares no rules at all, so its nodes must not be read as
    mandatory here — removing a standard folder node is not a modelling decision of the model.
    """
    if node.node_id.namespace == UA_NAMESPACE_URI:
        return False
    return (
        node.modelling_rule in {ModellingRule.MANDATORY, ModellingRule.MANDATORY_PLACEHOLDER}
        and mandatory_level_of(node) is MandatoryLevel.MANDATORY
    )


def _node_changes(
    old_node: UANode, new_node: UANode, old: ModelIndex, new: ModelIndex
) -> list[Change]:
    """Every semantic difference between two versions of one node."""
    changes: list[Change] = []
    if old_node.node_class is not new_node.node_class:
        changes.append(
            Change(
                area="node",
                kind="node_class",
                old=old_node.node_class.value,
                new=new_node.node_class.value,
                verdict=verdict("node_class"),
            )
        )
    old_name = _qualified_name(old_node.browse_name, old)
    new_name = _qualified_name(new_node.browse_name, new)
    if old_name != new_name:
        changes.append(
            Change(
                area="node",
                kind="browse_name",
                old=old_name,
                new=new_name,
                verdict=verdict("browse_name"),
            )
        )
    for attribute, kind in (
        ("display_name", "display_name"),
        ("description", "description"),
        ("documentation", "documentation"),
        ("symbolic_name", "symbolic_name"),
        ("inverse_name", "inverse_name"),
    ):
        old_value = _text(getattr(old_node, attribute, None))
        new_value = _text(getattr(new_node, attribute, None))
        if old_value != new_value:
            changes.append(
                Change(area="node", kind=kind, old=old_value, new=new_value, verdict=verdict(kind))
            )
    if old_node.categories != new_node.categories:
        changes.append(
            Change(
                area="node",
                kind="categories",
                old=_joined(old_node.categories),
                new=_joined(new_node.categories),
                verdict=verdict("categories"),
            )
        )
    old_attributes = _semantic_attributes(old_node)
    new_attributes = _semantic_attributes(new_node)
    if old_attributes != new_attributes:
        changes.append(
            Change(
                area="node",
                kind="attributes",
                old=_spell_attributes(old_attributes) or None,
                new=_spell_attributes(new_attributes) or None,
                verdict=verdict("attributes"),
            )
        )
    symmetric = _symmetric_change(old_node, new_node)
    if symmetric is not None:
        changes.append(symmetric)
    if old_node.is_abstract != new_node.is_abstract:
        changes.append(
            Change(
                area="node",
                kind="is_abstract",
                old=str(old_node.is_abstract),
                new=str(new_node.is_abstract),
                verdict=verdict("is_abstract", RuleContext(now_abstract=new_node.is_abstract)),
            )
        )
    rule = _modelling_rule_change(old_node, new_node)
    if rule is not None:
        changes.append(rule)
    changes.extend(_variable_changes(old_node, new_node, old, new))
    changes.extend(_definition_changes(old_node, new_node, old, new))
    changes.extend(_hierarchy_changes(old_node, new_node, old, new))
    changes.extend(_reference_changes(old_node, new_node, old, new))
    return changes


def _modelling_rule_change(old_node: UANode, new_node: UANode) -> Change | None:
    if old_node.modelling_rule is new_node.modelling_rule:
        return None
    tightened = (
        mandatory_level_of(old_node) is MandatoryLevel.OPTIONAL
        and mandatory_level_of(new_node) is MandatoryLevel.MANDATORY
    )
    return Change(
        area="node",
        kind="modelling_rule",
        old=_text(old_node.modelling_rule.value),
        new=_text(new_node.modelling_rule.value),
        verdict=verdict(
            "modelling_rule",
            RuleContext(
                tightened=tightened,
                relaxed=not tightened,
                modelling_rule=new_node.modelling_rule,
            ),
        ),
    )


def _variable_changes(
    old_node: UANode, new_node: UANode, old: ModelIndex, new: ModelIndex
) -> list[Change]:
    if not isinstance(old_node, UAVariable | UAVariableType) or not isinstance(
        new_node, UAVariable | UAVariableType
    ):
        return []
    changes: list[Change] = []
    if old_node.data_type != new_node.data_type:
        changes.append(
            Change(
                area="node",
                kind="data_type",
                old=old.label(old_node.data_type) if old_node.data_type else None,
                new=new.label(new_node.data_type) if new_node.data_type else None,
                verdict=verdict("data_type"),
            )
        )
    if old_node.value_rank != new_node.value_rank:
        changes.append(
            Change(
                area="node",
                kind="value_rank",
                old=_text(old_node.value_rank),
                new=_text(new_node.value_rank),
                verdict=verdict("value_rank"),
            )
        )
    if old_node.array_dimensions != new_node.array_dimensions:
        changes.append(
            Change(
                area="node",
                kind="array_dimensions",
                old=_joined(old_node.array_dimensions),
                new=_joined(new_node.array_dimensions),
                verdict=verdict("array_dimensions"),
            )
        )
    if _normalized(old_node.value_xml) != _normalized(new_node.value_xml):
        changes.append(
            Change(
                area="node",
                kind="value",
                old=_normalized(old_node.value_xml) or None,
                new=_normalized(new_node.value_xml) or None,
                verdict=verdict("value"),
            )
        )
    return changes


def _definition_changes(
    old_node: UANode, new_node: UANode, old: ModelIndex, new: ModelIndex
) -> list[Change]:
    """Diff the structured definition of a data type: Structure, Enum, Union, OptionSet."""
    if not isinstance(old_node, UADataType) or not isinstance(new_node, UADataType):
        return []
    old_definition: Definition | None = old_node.definition
    new_definition: Definition | None = new_node.definition
    if old_definition is None and new_definition is None:
        return []
    if old_definition is None or new_definition is None:
        return [
            Change(
                area="definition",
                kind="definition_kind",
                old=old_definition.kind if old_definition else None,
                new=new_definition.kind if new_definition else None,
                verdict=verdict("definition_kind"),
            )
        ]
    if old_definition.kind != new_definition.kind:
        return [
            Change(
                area="definition",
                kind="definition_kind",
                old=old_definition.kind,
                new=new_definition.kind,
                verdict=verdict("definition_kind"),
            )
        ]
    if new_definition.kind == "Structure":
        return _structure_changes(old_definition.fields, new_definition.fields, old, new)
    if new_definition.kind == "Union":
        return _union_changes(
            old_definition.union_alternatives, new_definition.union_alternatives, old, new
        )
    if new_definition.kind == "Enum":
        return _value_member_changes(
            old_definition.enum_values, new_definition.enum_values, member="enum_value"
        )
    if new_definition.kind == "OptionSet":
        return _value_member_changes(
            old_definition.option_set_values,
            new_definition.option_set_values,
            member="option_set_value",
        )
    return []


def _structure_changes(
    old_fields: Sequence[StructureField],
    new_fields: Sequence[StructureField],
    old: ModelIndex,
    new: ModelIndex,
) -> list[Change]:
    old_by_name = {field.name: field for field in old_fields}
    new_by_name = {field.name: field for field in new_fields}
    changes: list[Change] = []
    field_context = RuleContext(member="field")
    for name in sorted(set(old_by_name) | set(new_by_name)):
        old_field = old_by_name.get(name)
        new_field = new_by_name.get(name)
        if old_field is None and new_field is not None:
            changes.append(
                Change(
                    area="definition",
                    kind="added",
                    subject=name,
                    new=new.label(new_field.data_type),
                    verdict=verdict("added", field_context),
                )
            )
            continue
        if new_field is None and old_field is not None:
            changes.append(
                Change(
                    area="definition",
                    kind="removed",
                    subject=name,
                    old=old.label(old_field.data_type),
                    verdict=verdict("removed", field_context),
                )
            )
            continue
        if old_field is None or new_field is None:
            continue
        if old_field.data_type != new_field.data_type:
            changes.append(
                Change(
                    area="definition",
                    kind="changed",
                    subject=name,
                    old=old.label(old_field.data_type),
                    new=new.label(new_field.data_type),
                    verdict=verdict("changed", field_context),
                )
            )
        if old_field.is_optional != new_field.is_optional:
            changes.append(
                Change(
                    area="definition",
                    kind="changed",
                    subject=f"{name} IsOptional",
                    old=str(old_field.is_optional),
                    new=str(new_field.is_optional),
                    verdict=verdict("changed", field_context),
                )
            )
        changes.extend(_metadata_changes(name, old_field.metadata, new_field.metadata))
        changes.extend(_member_text_changes(name, old_field, new_field))
    changes.extend(
        _order_change(
            [field.name for field in old_fields],
            [field.name for field in new_fields],
            kind="field_order",
        )
    )
    return changes


def _union_changes(
    old_alternatives: Sequence[UnionAlternative],
    new_alternatives: Sequence[UnionAlternative],
    old: ModelIndex,
    new: ModelIndex,
) -> list[Change]:
    old_by_name = {alternative.name: alternative for alternative in old_alternatives}
    new_by_name = {alternative.name: alternative for alternative in new_alternatives}
    changes: list[Change] = []
    alternative_context = RuleContext(member="union_alternative")
    for name in sorted(set(old_by_name) | set(new_by_name)):
        old_alternative = old_by_name.get(name)
        new_alternative = new_by_name.get(name)
        if old_alternative is None and new_alternative is not None:
            changes.append(
                Change(
                    area="definition",
                    kind="added",
                    subject=name,
                    new=new.label(new_alternative.data_type),
                    verdict=verdict("added", alternative_context),
                )
            )
            continue
        if new_alternative is None and old_alternative is not None:
            changes.append(
                Change(
                    area="definition",
                    kind="removed",
                    subject=name,
                    old=old.label(old_alternative.data_type),
                    verdict=verdict("removed", alternative_context),
                )
            )
            continue
        if old_alternative is None or new_alternative is None:
            continue
        if old_alternative.data_type != new_alternative.data_type:
            changes.append(
                Change(
                    area="definition",
                    kind="changed",
                    subject=name,
                    old=old.label(old_alternative.data_type),
                    new=new.label(new_alternative.data_type),
                    verdict=verdict("changed", alternative_context),
                )
            )
        changes.extend(_metadata_changes(name, old_alternative.metadata, new_alternative.metadata))
        changes.extend(_member_text_changes(name, old_alternative, new_alternative))
    changes.extend(
        _order_change(
            [alternative.name for alternative in old_alternatives],
            [alternative.name for alternative in new_alternatives],
            kind="alternative_order",
        )
    )
    return changes


def _value_member_changes(
    old_values: Sequence[EnumValue | OptionSetValue],
    new_values: Sequence[EnumValue | OptionSetValue],
    *,
    member: str,
) -> list[Change]:
    """Enum and OptionSet values: identity is the value name, a changed number breaks clients."""
    old_by_name = {value.name: value for value in old_values}
    new_by_name = {value.name: value for value in new_values}
    changes: list[Change] = []
    context = RuleContext(member=member)
    for name in sorted(set(old_by_name) | set(new_by_name)):
        old_value = old_by_name.get(name)
        new_value = new_by_name.get(name)
        if old_value is None and new_value is not None:
            changes.append(
                Change(
                    area="definition",
                    kind="added",
                    subject=f"{name} = {new_value.value}",
                    verdict=verdict("added", context),
                )
            )
            continue
        if new_value is None and old_value is not None:
            changes.append(
                Change(
                    area="definition",
                    kind="removed",
                    subject=f"{name} = {old_value.value}",
                    old=f"= {old_value.value}",
                    verdict=verdict("removed", context),
                )
            )
            continue
        if old_value is None or new_value is None:
            continue
        if old_value.value != new_value.value:
            changes.append(
                Change(
                    area="definition",
                    kind="changed",
                    subject=name,
                    old=str(old_value.value),
                    new=str(new_value.value),
                    verdict=verdict("changed", context),
                )
            )
        changes.extend(_metadata_changes(name, old_value.metadata, new_value.metadata))
        changes.extend(_member_text_changes(name, old_value, new_value))
    return changes


def _member_text_changes(name: str, old_member: object, new_member: object) -> list[Change]:
    """DisplayName/Description of a definition member are documentation, never breaking."""
    changes: list[Change] = []
    for attribute, kind in (("display_name", "display_name"), ("description", "description")):
        old_text = _text(getattr(old_member, attribute, None))
        new_text = _text(getattr(new_member, attribute, None))
        if old_text != new_text:
            changes.append(
                Change(
                    area="definition",
                    kind="changed",
                    subject=f"{name} {MEMBER_LABELS[kind]}",
                    old=old_text,
                    new=new_text,
                    verdict=verdict("changed", RuleContext(member="member_text")),
                )
            )
    return changes


def _metadata_changes(name: str, old_meta: FieldMetadata, new_meta: FieldMetadata) -> list[Change]:
    """ValueRank, ArrayDimensions, MaxStringLength, AllowSubTypes: all part of the contract."""
    changes: list[Change] = []
    context = RuleContext(member="member_metadata")
    for attribute, label in FIELD_METADATA_LABELS:
        old_value = getattr(old_meta, attribute)
        new_value = getattr(new_meta, attribute)
        if old_value == new_value:
            continue
        changes.append(
            Change(
                area="definition",
                kind="changed",
                subject=f"{name} {label}",
                old=_spell_value(old_value),
                new=_spell_value(new_value),
                verdict=verdict("changed", context),
            )
        )
    return changes


def _order_change(
    old_names: Sequence[str], new_names: Sequence[str], *, kind: str
) -> list[Change]:
    """A Structure or Union re-ordered its members, which changes the encoding."""
    common = set(old_names) & set(new_names)
    old_order = [name for name in old_names if name in common]
    new_order = [name for name in new_names if name in common]
    if old_order == new_order:
        return []
    return [
        Change(
            area="definition",
            kind=kind,
            old=", ".join(old_order),
            new=", ".join(new_order),
            verdict=verdict(kind),
        )
    ]


def _hierarchy_changes(
    old_node: UANode, new_node: UANode, old: ModelIndex, new: ModelIndex
) -> list[Change]:
    """Where a node sits in its hierarchy: base type, type definition, parent, interfaces."""
    changes: list[Change] = []
    old_base = _base_type(old_node)
    new_base = _base_type(new_node)
    if old_base != new_base:
        changes.append(
            Change(
                area="hierarchy",
                kind="moved",
                subject="HasSubtype",
                old=_target_label(old, old_base, False) if old_base else None,
                new=_target_label(new, new_base, False) if new_base else None,
                verdict=verdict("hierarchy_move"),
            )
        )
    for attribute, subject in (
        ("type_definition", "HasTypeDefinition"),
        ("parent_node_id", "ParentNodeId"),
    ):
        old_value = getattr(old_node, attribute, None)
        new_value = getattr(new_node, attribute, None)
        if old_value == new_value:
            continue
        changes.append(
            Change(
                area="hierarchy",
                kind="moved",
                subject=subject,
                old=_reference_label(old, old_value),
                new=_reference_label(new, new_value),
                verdict=verdict("hierarchy_move"),
            )
        )
    old_interfaces = _interface_targets(old_node)
    new_interfaces = _interface_targets(new_node)
    for target in sorted(old_interfaces - new_interfaces):
        changes.append(
            Change(
                area="hierarchy",
                kind="removed",
                subject=f"HasInterface -> {_target_label(old, target, True)}",
                old="HasInterface",
                verdict=verdict("interface_removed"),
            )
        )
    for target in sorted(new_interfaces - old_interfaces):
        changes.append(
            Change(
                area="hierarchy",
                kind="added",
                subject=f"HasInterface -> {_target_label(new, target, True)}",
                new="HasInterface",
                verdict=verdict("interface_added"),
            )
        )
    old_children = _subtype_children(old_node)
    new_children = _subtype_children(new_node)
    for target in sorted(old_children - new_children):
        changes.append(
            Change(
                area="hierarchy",
                kind="removed",
                subject=f"HasSubtype -> {_target_label(old, target, True)}",
                old="HasSubtype",
                verdict=verdict("hierarchy_move"),
            )
        )
    for target in sorted(new_children - old_children):
        changes.append(
            Change(
                area="hierarchy",
                kind="added",
                subject=f"HasSubtype -> {_target_label(new, target, True)}",
                new="HasSubtype",
                verdict=verdict("subtype_inserted"),
            )
        )
    return changes


def _base_type(node: UANode) -> str | None:
    """Identity text of the type a node inherits from; a subtype declares it as inverse."""
    for ref in node.references:
        if _reference_name(ref) == "HasSubtype" and not ref.is_forward:
            return _identity_text(ref.target)
    return None


def _interface_targets(node: UANode) -> set[str]:
    """Identity texts of the Interfaces a type implements."""
    return {
        _identity_text(ref.target)
        for ref in node.references
        if _reference_name(ref) == "HasInterface"
    }


def _subtype_children(node: UANode) -> set[str]:
    """Identity texts of the subtypes a type declares forward (§3.2.9, §3.2.15)."""
    return {
        _identity_text(ref.target)
        for ref in node.references
        if _reference_name(ref) == "HasSubtype" and ref.is_forward
    }


def _reference_name(ref: Reference) -> str:
    return _reference_key(ref)[0]


def _reference_label(index: ModelIndex, node_id: ExpandedNodeId | None) -> str | None:
    """The target of a hierarchy attribute, named the way the report names other targets."""
    if node_id is None:
        return None
    return index.label(node_id)


def _reference_changes(
    old_node: UANode, new_node: UANode, old: ModelIndex, new: ModelIndex
) -> list[Change]:
    """Added and removed references, keyed by reference type, target identity and direction."""
    old_refs = {_reference_key(ref) for ref in old_node.references}
    new_refs = {_reference_key(ref) for ref in new_node.references}
    changes: list[Change] = []
    for name, target, is_forward in sorted(old_refs - new_refs):
        if name in HIERARCHY_REFERENCES:
            continue
        changes.append(
            Change(
                area="reference",
                kind="removed",
                subject=f"{name or 'Reference'} -> {_target_label(old, target, is_forward)}",
                old=name,
                verdict=verdict(
                    "removed", RuleContext(reference=_reference_category(name, target, old))
                ),
            )
        )
    for name, target, is_forward in sorted(new_refs - old_refs):
        if name in HIERARCHY_REFERENCES:
            continue
        changes.append(
            Change(
                area="reference",
                kind="added",
                subject=f"{name or 'Reference'} -> {_target_label(new, target, is_forward)}",
                new=name,
                verdict=verdict(
                    "added", RuleContext(reference=_reference_category(name, target, new))
                ),
            )
        )
    return changes


def _reference_key(ref: Reference) -> tuple[str, str, bool]:
    name = ref.type_name or (str(ref.reference_type) if ref.reference_type is not None else "")
    return (name, _identity_text(ref.target), ref.is_forward)


def _identity_text(node_id: ExpandedNodeId) -> str:
    return f"{node_id.namespace}|{node_id.identifier.as_xml()}"


def _lookup(index: ModelIndex, identity_text: str) -> UANode | None:
    namespace, _, local = identity_text.partition("|")
    node_id = ExpandedNodeId(identifier=NodeId.parse(local), namespace_uri=namespace)
    return index.node(node_id)


def _target_label(index: ModelIndex, identity_text: str, is_forward: bool) -> str:
    namespace, _, local = identity_text.partition("|")
    node_id = ExpandedNodeId(identifier=NodeId.parse(local), namespace_uri=namespace)
    label = index.label(node_id)
    return label if is_forward else f"{label} (inverse)"


def _reference_category(name: str, target: str, index: ModelIndex) -> str:
    """Which kind of reference this is, so the rule table can judge its removal.

    Contract references (subtype, type definition, interface, encoding, event) break a
    consumer; navigation references only change browsing; an ownership reference breaks
    when its target is an explicitly mandatory declaration.
    """
    if name in SOFT_REFERENCES:
        return "soft"
    if name in HARD_REFERENCES:
        return "hard"
    node = _lookup(index, target)
    if node is not None and name in OWNERSHIP_REFERENCES:
        return "mandatory_child" if _is_explicitly_mandatory(node) else "optional_child"
    return "other"


def _namespace_changes(old: ModelIndex, new: ModelIndex) -> list[NamespaceChange]:
    old_totals = _namespace_totals(old.model)
    new_totals = _namespace_totals(new.model)
    changes: list[NamespaceChange] = []
    for uri in sorted(set(old_totals) | set(new_totals)):
        before = old_totals.get(uri, NamespaceTotals())
        after = new_totals.get(uri, NamespaceTotals())
        if (before.nodes, before.references, before.datatypes) == (
            after.nodes,
            after.references,
            after.datatypes,
        ):
            continue
        changes.append(
            NamespaceChange(
                alias=new.alias(uri),
                uri=uri,
                nodes=(before.nodes, after.nodes),
                references=(before.references, after.references),
                datatypes=(before.datatypes, after.datatypes),
            )
        )
    return changes


def _namespace_totals(model: SemanticModel) -> dict[str, NamespaceTotals]:
    totals: dict[str, NamespaceTotals] = {}
    for node in model.iter_nodes():
        current = totals.get(node.node_id.namespace, NamespaceTotals())
        totals[node.node_id.namespace] = NamespaceTotals(
            nodes=current.nodes + 1,
            references=current.references + len(node.references),
            datatypes=current.datatypes + (1 if node.node_class is NodeClass.DATA_TYPE else 0),
        )
    return totals


def _breaking_changes(diff: SemanticDiff) -> list[BreakingChange]:
    """Every change a consumer of the model could break on — removed types, changed fields."""
    return _findings(diff, Severity.BREAKING)


def _advisory_changes(diff: SemanticDiff) -> list[BreakingChange]:
    """Best-practices findings that should be read before a release but never gate one."""
    return _findings(diff, Severity.ADVISORY)


def _findings(diff: SemanticDiff, severity: Severity) -> list[BreakingChange]:
    """Verdicts of one severity, in report order, with the rule behind each of them."""
    entries: list[BreakingChange] = []
    for nodes, action in ((diff.removed, "removed"), (diff.added, "added")):
        for entry in nodes:
            for rule in entry.verdicts:
                if rule.severity is not severity:
                    continue
                entries.append(
                    BreakingChange(
                        label=entry.label,
                        node_id=entry.node_id,
                        reason=f"{entry.node_class} {action}",
                        severity=rule.severity,
                        rule_id=rule.rule_id,
                        rationale=rule.rationale,
                        clause=rule.clause,
                    )
                )
    changed: list[NodeModification | RenumberedNode] = [*diff.modified, *diff.renumbered]
    for modification in changed:
        for rule in modification.verdicts:
            if rule.severity is not severity:
                continue
            entries.append(
                BreakingChange(
                    label=modification.label,
                    node_id=modification.node_id,
                    reason=_move_reason(modification),
                    severity=rule.severity,
                    rule_id=rule.rule_id,
                    rationale=rule.rationale,
                    clause=rule.clause,
                )
            )
        for change in modification.changes:
            if change.verdict is None or change.verdict.severity is not severity:
                continue
            entries.append(
                BreakingChange(
                    label=modification.label,
                    node_id=modification.node_id,
                    reason=change.describe(),
                    severity=change.verdict.severity,
                    rule_id=change.verdict.rule_id,
                    rationale=change.verdict.rationale,
                    clause=change.verdict.clause,
                )
            )
    return entries


def _move_reason(modification: NodeModification | RenumberedNode) -> str:
    """How an entry-level verdict reads: a renumbering shows the identifier it moved."""
    if isinstance(modification, RenumberedNode):
        old = modification.old_node_id.identifier.as_xml()
        new = modification.new_node_id.identifier.as_xml()
        return f"identifier moved: {old} → {new}"
    return "hierarchy changed"


def node_label(node: UANode) -> str:
    """Browse name, falling back to DisplayName and finally the identifier."""
    return node.browse_name.name or node.display_name or node.node_id.identifier.as_xml()


_label = node_label


def _qualified_name(name: QualifiedName, index: ModelIndex) -> str:
    """Browse name qualified by its namespace alias; the OPC UA namespace stays bare."""
    if name.namespace_uri and name.namespace_uri != UA_NAMESPACE_URI:
        return f"{index.alias(name.namespace_uri)}:{name.name}"
    return name.name


def _semantic_attributes(node: UANode) -> Mapping[str, str]:
    """The leftover XML attributes that are not identity spellings.

    Symmetric is judged on its own (§3.2.14), so it never rides along with an attribute edit
    that is only informational.
    """
    return {
        key: value
        for key, value in node.attributes.items()
        if key not in DOCUMENT_LOCAL_ATTRIBUTES and key != "Symmetric"
    }


def _symmetric_change(old_node: UANode, new_node: UANode) -> Change | None:
    """Symmetric of a ReferenceType shall not change (§3.2.14)."""
    old_value = old_node.attributes.get("Symmetric")
    new_value = new_node.attributes.get("Symmetric")
    if old_value == new_value:
        return None
    return Change(
        area="node",
        kind="attributes",
        subject="Symmetric",
        old=old_value,
        new=new_value,
        verdict=verdict("attributes", RuleContext(symmetric_flip=True)),
    )


def _spell_attributes(attributes: Mapping[str, str]) -> str:
    return ", ".join(f"{key}={value}" for key, value in sorted(attributes.items()))


def _spell_value(value: object) -> str | None:
    if isinstance(value, tuple):
        return _joined([str(item) for item in value])
    return _text(value)


def _joined(values: Sequence[str]) -> str | None:
    return ", ".join(values) if values else None


def _normalized(value: str | None) -> str:
    """Whitespace-insensitive value text — XML formatting is not a semantic change."""
    return " ".join(value.split()) if value else ""


def _text(value: object) -> str | None:
    return str(value) if value is not None else None
