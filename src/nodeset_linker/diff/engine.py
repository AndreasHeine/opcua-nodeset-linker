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
from nodeset_linker.repository.model import SemanticModel

#: Diff identity: namespace URI plus local identifier.  An index is a document property.
Identity = tuple[str, NodeId]

#: Removing one of these references breaks a contract instead of only navigation.
HARD_REFERENCES = frozenset(
    {"HasSubtype", "HasTypeDefinition", "HasInterface", "HasEncoding", "GeneratesEvent"}
)
#: Removing one of these is navigation-only and therefore never breaking.
SOFT_REFERENCES = frozenset({"Organizes", "HasDescription"})

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
        """Readable name of a referenced node; a node outside the model keeps its id."""
        node = self.node(node_id)
        if node is not None:
            return _label(node)
        if node_id.namespace == UA_NAMESPACE_URI:
            # The OPC UA namespace is implied in every NodeSet, so `i=12` reads better here.
            return node_id.identifier.as_xml()
        return node_id.as_xml()

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
        added=[_entry(node, new_index, breaking=False) for node in added_nodes],
        removed=[
            _entry(node, old_index, breaking=_removal_breaking(node)) for node in removed_nodes
        ],
        modified=_modified_nodes(shared, old_index, new_index),
        renumbered=renumbered,
        namespaces=_namespace_changes(old_index, new_index),
    )
    diff.breaking = _breaking_changes(diff)
    return diff


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


def _entry(node: UANode, index: ModelIndex, *, breaking: bool) -> NodeEntry:
    return NodeEntry(
        label=_label(node),
        node_id=node.node_id,
        node_class=node.node_class.value,
        namespace=index.alias(node.node_id.namespace),
        breaking=breaking,
    )


def _removal_breaking(node: UANode) -> bool:
    """A removed type, encoding or placeholder, or an explicitly mandatory node."""
    if node.node_class.is_type or node.role in {NodeRole.ENCODING, NodeRole.PLACEHOLDER}:
        return True
    return _is_explicitly_mandatory(node)


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
                breaking=True,
            )
        )
    old_name = _qualified_name(old_node.browse_name, old)
    new_name = _qualified_name(new_node.browse_name, new)
    if old_name != new_name:
        changes.append(
            Change(area="node", kind="browse_name", old=old_name, new=new_name, breaking=True)
        )
    for attribute, kind, breaking in (
        ("display_name", "display_name", False),
        ("description", "description", False),
        ("documentation", "documentation", False),
        ("symbolic_name", "symbolic_name", True),
        ("inverse_name", "inverse_name", True),
    ):
        old_value = _text(getattr(old_node, attribute, None))
        new_value = _text(getattr(new_node, attribute, None))
        if old_value != new_value:
            changes.append(
                Change(area="node", kind=kind, old=old_value, new=new_value, breaking=breaking)
            )
    if old_node.categories != new_node.categories:
        changes.append(
            Change(
                area="node",
                kind="categories",
                old=_joined(old_node.categories),
                new=_joined(new_node.categories),
            )
        )
    if _semantic_attributes(old_node) != _semantic_attributes(new_node):
        changes.append(
            Change(
                area="node",
                kind="attributes",
                old=_spell_attributes(_semantic_attributes(old_node)) or None,
                new=_spell_attributes(_semantic_attributes(new_node)) or None,
            )
        )
    if old_node.is_abstract != new_node.is_abstract:
        changes.append(
            Change(
                area="node",
                kind="is_abstract",
                old=str(old_node.is_abstract),
                new=str(new_node.is_abstract),
                breaking=new_node.is_abstract,
            )
        )
    rule = _modelling_rule_change(old_node, new_node)
    if rule is not None:
        changes.append(rule)
    changes.extend(_variable_changes(old_node, new_node, old, new))
    changes.extend(_definition_changes(old_node, new_node, old, new))
    changes.extend(_reference_changes(old_node, new_node, old, new))
    return changes


def _modelling_rule_change(old_node: UANode, new_node: UANode) -> Change | None:
    if old_node.modelling_rule is new_node.modelling_rule:
        return None
    tightening = (
        mandatory_level_of(old_node) is MandatoryLevel.OPTIONAL
        and mandatory_level_of(new_node) is MandatoryLevel.MANDATORY
    )
    return Change(
        area="node",
        kind="modelling_rule",
        old=_text(old_node.modelling_rule.value),
        new=_text(new_node.modelling_rule.value),
        breaking=tightening,
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
                breaking=True,
            )
        )
    if old_node.value_rank != new_node.value_rank:
        changes.append(
            Change(
                area="node",
                kind="value_rank",
                old=_text(old_node.value_rank),
                new=_text(new_node.value_rank),
                breaking=True,
            )
        )
    if old_node.array_dimensions != new_node.array_dimensions:
        changes.append(
            Change(
                area="node",
                kind="array_dimensions",
                old=_joined(old_node.array_dimensions),
                new=_joined(new_node.array_dimensions),
                breaking=True,
            )
        )
    if _normalized(old_node.value_xml) != _normalized(new_node.value_xml):
        changes.append(
            Change(
                area="node",
                kind="value",
                old=_normalized(old_node.value_xml) or None,
                new=_normalized(new_node.value_xml) or None,
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
                breaking=new_definition is None,
            )
        ]
    if old_definition.kind != new_definition.kind:
        return [
            Change(
                area="definition",
                kind="definition_kind",
                old=old_definition.kind,
                new=new_definition.kind,
                breaking=True,
            )
        ]
    if new_definition.kind == "Structure":
        return _structure_changes(old_definition.fields, new_definition.fields, old, new)
    if new_definition.kind == "Union":
        return _union_changes(
            old_definition.union_alternatives, new_definition.union_alternatives, old, new
        )
    if new_definition.kind == "Enum":
        return _value_member_changes(old_definition.enum_values, new_definition.enum_values)
    if new_definition.kind == "OptionSet":
        return _value_member_changes(
            old_definition.option_set_values, new_definition.option_set_values
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
                    breaking=False,
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
                    breaking=not old_field.is_optional,
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
                    breaking=True,
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
                    breaking=not new_field.is_optional,
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
                    breaking=False,
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
                    breaking=True,
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
                    breaking=True,
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
) -> list[Change]:
    """Enum and OptionSet values: identity is the value name, a changed number breaks clients."""
    old_by_name = {value.name: value for value in old_values}
    new_by_name = {value.name: value for value in new_values}
    changes: list[Change] = []
    for name in sorted(set(old_by_name) | set(new_by_name)):
        old_value = old_by_name.get(name)
        new_value = new_by_name.get(name)
        if old_value is None and new_value is not None:
            changes.append(
                Change(
                    area="definition",
                    kind="added",
                    subject=f"{name} = {new_value.value}",
                    breaking=False,
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
                    breaking=True,
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
                    breaking=True,
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
                )
            )
    return changes


def _metadata_changes(name: str, old_meta: FieldMetadata, new_meta: FieldMetadata) -> list[Change]:
    """ValueRank, ArrayDimensions, MaxStringLength, AllowSubTypes: all part of the contract."""
    changes: list[Change] = []
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
                breaking=True,
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
            breaking=True,
        )
    ]


def _reference_changes(
    old_node: UANode, new_node: UANode, old: ModelIndex, new: ModelIndex
) -> list[Change]:
    """Added and removed references, keyed by reference type, target identity and direction."""
    old_refs = {_reference_key(ref) for ref in old_node.references}
    new_refs = {_reference_key(ref) for ref in new_node.references}
    changes: list[Change] = []
    for key in sorted(old_refs - new_refs):
        name, target, is_forward = key
        changes.append(
            Change(
                area="reference",
                kind="removed",
                subject=f"{name or 'Reference'} -> {_target_label(old, target, is_forward)}",
                old=name,
                breaking=_reference_removal_breaking(name, target, old),
            )
        )
    for key in sorted(new_refs - old_refs):
        name, target, is_forward = key
        changes.append(
            Change(
                area="reference",
                kind="added",
                subject=f"{name or 'Reference'} -> {_target_label(new, target, is_forward)}",
                new=name,
                breaking=False,
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
    node = _lookup(index, identity_text)
    if node is None:
        return identity_text.partition("|")[1]
    return _label(node) if is_forward else f"{_label(node)} (inverse)"


def _reference_removal_breaking(name: str, target: str, index: ModelIndex) -> bool:
    if name in SOFT_REFERENCES:
        return False
    if name in HARD_REFERENCES:
        return True
    node = _lookup(index, target)
    if node is not None and name in OWNERSHIP_REFERENCES:
        return _is_explicitly_mandatory(node)
    return True


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
    entries: list[BreakingChange] = []
    for entry in diff.removed:
        if entry.breaking:
            entries.append(
                BreakingChange(
                    label=entry.label,
                    node_id=entry.node_id,
                    reason=f"{entry.node_class} removed",
                )
            )
    changed: list[NodeModification | RenumberedNode] = [*diff.modified, *diff.renumbered]
    for modification in changed:
        for change in modification.changes:
            if not change.breaking:
                continue
            entries.append(
                BreakingChange(
                    label=modification.label,
                    node_id=modification.node_id,
                    reason=change.describe(),
                )
            )
    return entries


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
    """The leftover XML attributes that are not identity spellings."""
    return {
        key: value
        for key, value in node.attributes.items()
        if key not in DOCUMENT_LOCAL_ATTRIBUTES
    }


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
