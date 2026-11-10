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

import re
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from typing import Self

from nodeset_linker.diff.model import (
    BreakingChange,
    Change,
    MetadataDelta,
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
from nodeset_linker.models.namespace import ModelInfo, Namespace, alias_from_uri
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
from nodeset_linker.models.version import canonical, is_semantic, versions_equal
from nodeset_linker.repository.model import MergeConflict, SemanticModel
from nodeset_linker.validator.semantic import ExternalSet, declared_external_namespaces

#: Diff identity: namespace URI, local identifier, and the server the identifier is qualified
#: against.  An index is a document property; a server URI is part of the node's identity.
Identity = tuple[str, NodeId, str | None]

#: Removing one of these references breaks a contract instead of only navigation.
HARD_REFERENCES = frozenset(
    {"HasSubtype", "HasTypeDefinition", "HasInterface", "HasEncoding", "GeneratesEvent"}
)
#: Removing one of these is navigation-only and therefore never breaking.
SOFT_REFERENCES = frozenset({"Organizes", "HasDescription"})
#: Where a node sits in its hierarchy is judged as a hierarchy change (§3.2.9, §3.2.10,
#: §3.2.13, §3.2.15), not as a reference change, so it is reported once.
HIERARCHY_REFERENCES = frozenset({"HasSubtype", "HasTypeDefinition", "HasInterface"})
#: The deprecation marker of the paper (§14.2): adding it informs consumers, removing it
#: leaves them reading a node that was announced as deprecated.
DEPRECATION_REFERENCES = frozenset({"IsDeprecated"})
# Description references on method arguments (§5): they carry documentation, not a contract.
ARGUMENT_DESCRIPTION_REFERENCES = frozenset(
    {"HasArgumentDescription", "HasOptionalInputArgumentDescription"}
)
#: A deprecation object names the version that deprecated the node (§14.3.2), for example
#: ``<SpecName>Version1_02``; a replacement type names itself with a version suffix (§11.2.6).
DEPRECATION_OBJECT_NAME = re.compile(r"Version\d+(?:_\d+)?$")
VERSION_SUFFIX = re.compile(r"\d+$")
#: ConformanceUnits and Profiles of a companion namespace (§12.1, §12.2).  Found by BrowseName,
#: never by identifier, so the engine works without the OPC UA base nodeset loaded.
CONFORMANCE_UNIT_NAME = "ConformanceUnit"
PROFILE_NAME = "Profile"
#: The two OPC UA types that decide whether a hierarchy change sits inside a filtering
#: hierarchy (§3.2.9, §3.2.15).  Both are found by BrowseName, never by identifier, so the
#: engine works without the OPC UA base nodeset loaded.
UA_EVENT_BASE_NAME = "BaseEventType"
UA_HIERARCHICAL_REFERENCE_NAME = "HierarchicalReference"

#: Default for comparison helpers that run without a paired-move map.
_NO_MOVES: Mapping[str, str] = {}

#: Namespace metadata attribute → the rule that judges a change of it (§11.1, §11.2, §11.3).
_METADATA_RULES: Mapping[str, str] = {
    "alias": "namespace_alias_changed",
    "version": "namespace_version_changed",
    "publication_date": "namespace_publication_date_changed",
    "xml_schema_uri": "namespace_xml_schema_uri_changed",
    "model_version": "namespace_model_version_changed",
}

#: Namespace metadata whose *spelling* may differ while the claim is the same: a display
#: ``Version`` pads its segments (``1.05.03``), a ``ModelVersion`` is SemVer (``1.5.3``).  Both
#: are judged on the numeric core, so a re-spelled release is not a release move (§11.1, F.2).
_VERSION_ATTRIBUTES = frozenset({"version", "model_version"})

#: Namespace metadata that two source documents must not declare differently (§11.2); the
#: merge keeps one spelling, so a published file can carry metadata no document claimed.
_CONFLICT_ATTRIBUTES: Mapping[str, str] = {
    "version": "Version",
    "publication_date": "PublicationDate",
    "xml_schema_uri": "XmlSchemaUri",
    "model_version": "ModelVersion",
}

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

    Identity is `(namespaceUri, identifier, serverUri)` — the same triple an
    ``ExpandedNodeId`` compares on.  ``ServerUri`` is part of it on purpose: an identifier
    qualified against another server names a different node than the same identifier in this
    server's own address space, and leaving it out would make a re-qualification invisible and
    let server-qualified twins overwrite each other in the index.
    """

    model: SemanticModel
    nodes: dict[Identity, UANode]
    #: Supertypes per node and subtype children per type, read from the HasSubtype edges.
    #: The hierarchy rules need these (§3.2.9, §3.2.10, §3.2.15).
    supertypes: dict[str, tuple[str, ...]] = field(default_factory=dict)
    subtypes: dict[str, tuple[str, ...]] = field(default_factory=dict)
    #: Argument variables per method, keyed by the method's identity text.  Built once
    #: so method-signature changes do not rescan the whole model per method.
    arguments: dict[str, list[ExpandedNodeId]] = field(default_factory=dict)

    @classmethod
    def of(cls, model: SemanticModel) -> Self:
        nodes = {identity(n.node_id): n for n in model.iter_nodes()}
        parents: dict[str, tuple[str, ...]] = {}
        children: dict[str, list[str]] = {}
        arguments: dict[str, list[ExpandedNodeId]] = {}
        for node in nodes.values():
            own = _identity_text(node.node_id)
            for reference in node.references:
                if _reference_name(reference) != "HasSubtype":
                    continue
                target = _identity_text(reference.target)
                parent, child = (own, target) if reference.is_forward else (target, own)
                parents[child] = tuple(sorted({*parents.get(child, ()), parent}))
                children.setdefault(parent, []).append(child)
            if isinstance(node, UAVariable):
                # An argument hangs on its method by ParentNodeId or an inverse HasProperty;
                # record it under the method instead of scanning for it later.
                method_ids: list[ExpandedNodeId] = [
                    *([node.parent_node_id] if node.parent_node_id is not None else []),
                    *(
                        ref.target
                        for ref in node.references
                        if _reference_name(ref) == "HasProperty" and not ref.is_forward
                    ),
                ]
                for method_id in method_ids:
                    arguments.setdefault(_identity_text(method_id), []).append(node.node_id)
        return cls(
            model=model,
            nodes=nodes,
            supertypes=parents,
            subtypes={key: tuple(sorted(values)) for key, values in children.items()},
            arguments=arguments,
        )

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
    """URI identity of a node — namespace, identifier, and server qualification.

    The OPC UA identity of a node is its `NodeId` within a namespace, plus the `ServerUri` when
    the identifier points into another server's address space (OPC 10000-6). A node re-qualified
    against another server is therefore a different node, not the same one spelled differently.
    """
    return (node_id.namespace, node_id.identifier, node_id.server_uri)


def diff_models(
    old: SemanticModel,
    new: SemanticModel,
    *,
    old_source: str = "",
    new_source: str = "",
    reduced_export: bool = False,
) -> SemanticDiff:
    """Compare two semantic models and report the changes of the information model.

    ``reduced_export`` says the *new* file is a Linker export that deliberately ships less
    than the source: nodes whose namespace the export declares as a ``RequiredModel`` are
    reported as externalized (§11.2) instead of as removals (§3.2.16).  It is an explicit
    option, not a guess — nothing here is silently forgiven.
    """
    old_index = ModelIndex.of(old)
    new_index = ModelIndex.of(new)
    externalized: ExternalSet = (
        declared_external_namespaces(new) if reduced_export else frozenset()
    )

    gone = sorted(set(old_index.nodes) - set(new_index.nodes), key=_sort_key)
    fresh = sorted(set(new_index.nodes) - set(old_index.nodes), key=_sort_key)
    shared = sorted(set(old_index.nodes) & set(new_index.nodes), key=_sort_key)

    renumbered, removed_ids, added_ids = _pair_renumbering(gone, fresh, old_index, new_index)
    renamed, rename_removed, rename_added = _pair_renames(
        removed_ids, added_ids, old_index, new_index
    )
    renumbered.extend(renamed)
    renumbered.sort(key=lambda entry: entry.label)
    #: A paired move is one finding (§3.2.17).  References, hierarchies and data types that
    #: point at a renumbered node therefore compare through the move, not as detached children
    #: or datatype changes.
    moved: Mapping[str, str] = {
        _identity_text(entry.old_node_id): _identity_text(entry.new_node_id)
        for entry in renumbered
    }
    removed_ids = [identity for identity in removed_ids if identity not in rename_removed]
    added_ids = [identity for identity in added_ids if identity not in rename_added]
    removed_nodes = [old_index.nodes[node_id] for node_id in removed_ids]
    added_nodes = [new_index.nodes[node_id] for node_id in added_ids]
    replacements = _type_replacements(
        removed_nodes, _newly_deprecated(old_index, new_index, shared), added_nodes
    )

    diff = SemanticDiff(
        old_source=old_source,
        new_source=new_source,
        old_nodes=len(old_index.nodes),
        new_nodes=len(new_index.nodes),
        added=[
            _entry(
                node,
                new_index,
                verdicts=_entry_verdicts(node, new_index, old_index, replacements),
            )
            for node in added_nodes
        ],
        removed=[
            _entry(node, old_index, verdicts=_removal_verdicts(node, externalized))
            for node in removed_nodes
        ],
        modified=_modified_nodes(shared, old_index, new_index, moved),
        renumbered=renumbered,
        namespaces=_namespace_changes(old_index, new_index),
    )
    diff.breaking = _breaking_changes(diff)
    diff.advisory = _advisory_changes(diff) + _merge_conflict_findings(old, new)
    return diff


def _merge_conflict_findings(old: SemanticModel, new: SemanticModel) -> list[BreakingChange]:
    """Conflicts a tolerant merge carried through, reported rather than swallowed.

    A merge that kept going dropped one declaration of an identifier, so the model it produced
    is *incomplete on purpose*: which contract was published depends on which document won.
    The finding names both documents (§3.2.17 — one identifier, one declaration) and never
    blocks a release, because the diff itself is still trustworthy.
    """
    seen: dict[tuple[ExpandedNodeId, tuple[str, ...]], MergeConflict] = {}
    for conflict in [*old.conflicts, *new.conflicts]:
        seen.setdefault((conflict.node_id, conflict.paths), conflict)
    found = verdict("merge_conflict")
    return [
        BreakingChange(
            label=conflict.browse_name or conflict.node_id.identifier.as_xml(),
            node_id=conflict.node_id,
            reason=conflict.describe(),
            severity=found.severity,
            rule_id=found.rule_id,
            rationale=found.rationale,
            clause=found.clause,
        )
        for (_node_id, _paths), conflict in sorted(
            seen.items(), key=lambda item: (item[0][0].namespace, item[0][0].identifier.value)
        )
    ]


def _entry_verdicts(
    node: UANode,
    index: ModelIndex,
    previous: ModelIndex,
    replacements: Mapping[Identity, RuleVerdict],
) -> tuple[RuleVerdict, ...]:
    """The rule verdicts for a node that appeared in the new model (§3.2.1, §3.2.2, §3.2.9,
    §3.2.15, §11.2.6, §14.3.2)."""
    verdicts = [_base_entry_verdict(node, index, previous)]
    replacement = replacements.get(identity(node.node_id))
    if replacement is not None:
        verdicts.append(replacement)
    if node.node_class is NodeClass.OBJECT and DEPRECATION_OBJECT_NAME.search(
        node.browse_name.name
    ):
        verdicts.append(verdict("deprecation_version"))
    conformance = _conformance_role(node)
    if conformance is not None:
        verdicts.append(verdict("cu_added" if conformance == "cu" else "profile_added"))
    return tuple(verdicts)


def _base_entry_verdict(node: UANode, index: ModelIndex, previous: ModelIndex) -> RuleVerdict:
    """The contract verdict for an added node, before deprecation and replacement context."""
    if _is_added_mandatory_declaration(node):
        return verdict("node_added_mandatory", RuleContext(mandatory_target=True))
    if node.node_class is NodeClass.REFERENCE_TYPE and _reference_type_inside_hierarchy(
        node, index, previous
    ):
        return verdict("rt_new_in_hierarchy")
    if node.node_class.is_type and _base_is_extension(node, index):
        return verdict("subtype_inserted")
    return verdict("node_added", RuleContext(modelling_rule=node.modelling_rule))


def _is_deprecated(node: UANode) -> bool:
    """Whether a node carries an ``IsDeprecated`` reference (§14.2)."""
    return any(reference.type_name in DEPRECATION_REFERENCES for reference in node.references)


def _newly_deprecated(old: ModelIndex, new: ModelIndex, shared: Sequence[Identity]) -> list[UANode]:
    """The nodes this release deprecated — present in both models, marked only in the new one."""
    return [
        new.nodes[node_id]
        for node_id in shared
        if _is_deprecated(new.nodes[node_id]) and not _is_deprecated(old.nodes[node_id])
    ]


def _deprecation_counts(model: SemanticModel) -> dict[str, int]:
    """How many nodes of each namespace this model marks deprecated (§14.2)."""
    counts: Counter[str] = Counter()
    for node in model.iter_nodes():
        if _is_deprecated(node):
            counts[node.node_id.namespace] += 1
    return dict(counts)


def _conformance_role(node: UANode) -> str | None:
    """Whether a node declares a ConformanceUnit or a Profile (§12.1, §12.2)."""
    if node.node_class is not NodeClass.OBJECT or node.node_id.namespace == UA_NAMESPACE_URI:
        return None
    if node.browse_name.name == CONFORMANCE_UNIT_NAME:
        return "cu"
    if node.browse_name.name == PROFILE_NAME:
        return "profile"
    return None


def _conformance_declarations(model: SemanticModel) -> dict[str, int]:
    """How many ConformanceUnits and Profiles each namespace declares (§12.1, §12.2)."""
    counts: Counter[str] = Counter()
    for node in model.iter_nodes():
        if _conformance_role(node) is not None:
            counts[node.node_id.namespace] += 1
    return dict(counts)


def _type_replacements(
    gone: Sequence[UANode], deprecated: Sequence[UANode], added: Sequence[UANode]
) -> dict[Identity, RuleVerdict]:
    """Name the type that replaces a removed or deprecated one (§11.2.6).

    The replacement is recognised by the base name the old type had: ``MachineType`` gains a
    version suffix (``MachineType2``) or an unrelated name.  A subtype of the old type is an
    extension, not a replacement, and is skipped.
    """
    stale = [node for node in [*gone, *deprecated] if node.node_class.is_type]
    verdicts: dict[Identity, RuleVerdict] = {}
    for old_node in stale:
        base = VERSION_SUFFIX.sub("", old_node.browse_name.name)
        matches = [
            node
            for node in added
            if node.node_class is old_node.node_class
            and node.node_id.namespace == old_node.node_id.namespace
            and node.browse_name.name.startswith(base)
            and _base_type(node) != _identity_text(old_node.node_id)
        ]
        if len(matches) != 1:
            continue
        numbered = VERSION_SUFFIX.search(matches[0].browse_name.name) is not None
        verdicts[identity(matches[0].node_id)] = verdict(
            "type_replaced_numbered" if numbered else "type_replaced"
        )
    return verdicts


def _reference_type_inside_hierarchy(
    node: UANode, index: ModelIndex, previous: ModelIndex
) -> bool:
    """Whether a new ReferenceType sits between two types that already existed (§3.2.15).

    Adding a ReferenceType at the bottom of the hierarchy is allowed; adding one whose
    supertype already has other subtypes puts it inside the hierarchy, where filtering breaks.
    """
    for parent in _supertypes(index, _identity_text(node.node_id)):
        if previous.subtypes.get(parent):
            return True
    return False


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
    if _identity_parts(base_identity)[0] == UA_NAMESPACE_URI:
        return False
    return _lookup(index, base_identity) is not None


def _sort_key(node_identity: Identity) -> tuple[str, str, str, str]:
    namespace, identifier, server = node_identity
    return (namespace, server or "", identifier.kind.value, identifier.value)


def _pair_renumbering(
    gone: list[Identity],
    fresh: list[Identity],
    old_index: ModelIndex,
    new_index: ModelIndex,
) -> tuple[list[RenumberedNode], list[Identity], list[Identity]]:
    """Pair removed and added nodes that kept namespace, server, BrowseName and node class."""
    removed_groups: dict[tuple[str, str, str, NodeClass], list[Identity]] = {}
    added_groups: dict[tuple[str, str, str, NodeClass], list[Identity]] = {}
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
        # One removed and one added node under the same key is the only unambiguous move;
        # a larger group is paired by position and has to be labelled as a guess.
        confidence = "exact" if len(removed_group) == len(added_group) == 1 else "heuristic"
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
                    node_class=key[3].value,
                    namespace=new_index.alias(key[0]),
                    namespace_uri=key[0],
                    confidence=confidence,
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


def _pair_renames(
    gone: list[Identity],
    fresh: list[Identity],
    old_index: ModelIndex,
    new_index: ModelIndex,
) -> tuple[list[RenumberedNode], set[Identity], set[Identity]]:
    """Pair a removed and an added node that differ only in their BrowseName (§2.1.2).

    A node that vanished and came back with the same identifier-free shape under another name
    is a rename, not an unrelated removal plus addition.  Only an unambiguous candidate is
    paired, and the entry says so, because pairing is a guess.
    """
    renamed: list[RenumberedNode] = []
    paired_removed: set[Identity] = set()
    paired_added: set[Identity] = set()
    candidates: dict[tuple[str, str, NodeClass], list[Identity]] = {}
    for node_identity in fresh:
        node = new_index.nodes[node_identity]
        candidates.setdefault(
            (node.node_id.namespace, node.node_id.server_uri or "", node.node_class), []
        ).append(node_identity)
    for node_identity in sorted(gone, key=_sort_key):
        old_node = old_index.nodes[node_identity]
        group = candidates.get(
            (old_node.node_id.namespace, old_node.node_id.server_uri or "", old_node.node_class),
            [],
        )
        matches = [
            candidate
            for candidate in group
            if candidate not in paired_added
            and _differs_only_in_name(old_node, new_index.nodes[candidate], old_index, new_index)
        ]
        if len(matches) != 1:
            continue
        new_node = new_index.nodes[matches[0]]
        paired_removed.add(node_identity)
        paired_added.add(matches[0])
        renamed.append(
            RenumberedNode(
                label=_label(new_node),
                old_node_id=old_node.node_id,
                new_node_id=new_node.node_id,
                node_class=new_node.node_class.value,
                namespace=new_index.alias(new_node.node_id.namespace),
                namespace_uri=new_node.node_id.namespace,
                confidence="heuristic",
                changes=[
                    Change(
                        area="node",
                        kind="renamed",
                        old=old_node.browse_name.name,
                        new=new_node.browse_name.name,
                        verdict=verdict("node_renamed"),
                    )
                ],
                verdicts=(verdict("identifier_move"),),
            )
        )
    renamed.sort(key=lambda entry: entry.label)
    return renamed, paired_removed, paired_added


def _differs_only_in_name(
    old_node: UANode, new_node: UANode, old: ModelIndex, new: ModelIndex
) -> bool:
    """Whether the two nodes are identical apart from their BrowseName."""
    differences = [change.kind for change in _node_changes(old_node, new_node, old, new)]
    return differences == ["browse_name"]


def _move_key(node: UANode) -> tuple[str, str, str, NodeClass]:
    """Pairing key of a renumbered node: namespace, server, BrowseName, node class.

    Server qualification belongs to the identity, so re-qualifying a node against another
    server is not a move — it reads as a removal plus an addition.
    """
    return (
        node.node_id.namespace,
        node.node_id.server_uri or "",
        node.browse_name.name,
        node.node_class,
    )


def _modified_nodes(
    shared: list[Identity],
    old_index: ModelIndex,
    new_index: ModelIndex,
    moved: Mapping[str, str] = _NO_MOVES,
) -> list[NodeModification]:
    modifications: list[NodeModification] = []
    for node_identity in shared:
        node = new_index.nodes[node_identity]
        changes = _node_changes(old_index.nodes[node_identity], node, old_index, new_index, moved)
        if not changes:
            continue
        modifications.append(
            NodeModification(
                label=_label(node),
                node_id=node.node_id,
                node_class=node.node_class.value,
                namespace=new_index.alias(node.node_id.namespace),
                namespace_uri=node.node_id.namespace,
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
        namespace_uri=node.node_id.namespace,
        verdicts=verdicts,
    )


def _removal_verdicts(
    node: UANode, externalized: ExternalSet = frozenset()
) -> tuple[RuleVerdict, ...]:
    """The rule verdicts for a node that vanished from the model.

    Types, encodings and placeholders are part of the published contract; so is an
    explicitly ``Mandatory`` declaration.  Nodes of the OPC UA namespace are not this
    model's declarations, and an optional declaration carries no promise.  A node whose
    namespace the new document declares as a ``RequiredModel`` was externalized by the
    export (§11.2), not removed.  A removed ConformanceUnit or Profile additionally loses
    test scope (§12.1, §12.2), which is worth naming even where the removal itself is not
    what breaks a consumer.
    """
    if node.node_id.namespace in externalized:
        return (verdict("declaration_externalized"),)
    verdicts = [_removal_base_verdict(node)]
    conformance = _conformance_role(node)
    if conformance is not None:
        verdicts.append(verdict("cu_removed" if conformance == "cu" else "profile_removed"))
    return tuple(verdicts)


def _removal_base_verdict(node: UANode) -> RuleVerdict:
    """The contract verdict of a removal, before conformance context."""
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
    old_node: UANode,
    new_node: UANode,
    old: ModelIndex,
    new: ModelIndex,
    moved: Mapping[str, str] = _NO_MOVES,
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
    for key in sorted({*old_attributes, *new_attributes}):
        if old_attributes.get(key) == new_attributes.get(key):
            continue
        changes.append(
            Change(
                area="node",
                kind="attributes",
                subject=key,
                old=old_attributes.get(key),
                new=new_attributes.get(key),
                verdict=verdict("attributes"),
            )
        )
    if old_node.role_permissions_xml != new_node.role_permissions_xml:
        changes.append(
            Change(
                area="node",
                kind="attributes",
                subject="RolePermissions",
                old=_joined(old_node.role_permissions_xml) or None,
                new=_joined(new_node.role_permissions_xml) or None,
                verdict=verdict("role_permissions"),
            )
        )
    if _normalized_extension(old_node.extensions_xml) != _normalized_extension(
        new_node.extensions_xml
    ):
        changes.append(
            Change(
                area="node",
                kind="attributes",
                subject="Extensions",
                old=_joined(old_node.extensions_xml) or None,
                new=_joined(new_node.extensions_xml) or None,
                verdict=verdict("extensions"),
            )
        )
    if _value_dependencies(old_node) != _value_dependencies(new_node):
        changes.append(
            Change(
                area="node",
                kind="attributes",
                subject="ValueDependencies",
                old=_spell_dependencies(_value_dependencies(old_node)) or None,
                new=_spell_dependencies(_value_dependencies(new_node)) or None,
                verdict=verdict("value_dependency"),
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
    changes.extend(_variable_changes(old_node, new_node, old, new, moved))
    changes.extend(_method_changes(old_node, new_node, old, new, moved))
    changes.extend(_definition_changes(old_node, new_node, old, new, moved))
    changes.extend(_hierarchy_changes(old_node, new_node, old, new, moved))
    changes.extend(_reference_changes(old_node, new_node, old, new, moved))
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
    old_node: UANode,
    new_node: UANode,
    old: ModelIndex,
    new: ModelIndex,
    moved: Mapping[str, str] = _NO_MOVES,
) -> list[Change]:
    if not isinstance(old_node, UAVariable | UAVariableType) or not isinstance(
        new_node, UAVariable | UAVariableType
    ):
        return []
    if _argument_contract_changed(old_node, new_node, old, new):
        # The method entry carries this break; the argument adds no new information (§3.2.11).
        return []
    changes: list[Change] = []
    if not _same_target(old_node.data_type, new_node.data_type, moved):
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


def _argument_method(node: UANode, index: ModelIndex) -> ExpandedNodeId | None:
    """The method an argument variable is contracted with, by parent or inverse HasProperty."""
    parent = node.parent_node_id
    if parent is not None:
        holder = index.node(parent)
        if holder is not None and holder.node_class is NodeClass.METHOD:
            return parent
    for ref in node.references:
        if _reference_name(ref) != "HasProperty" or ref.is_forward:
            continue
        holder = index.node(ref.target)
        if holder is not None and holder.node_class is NodeClass.METHOD:
            return ref.target
    return None


def _argument_contract_changed(
    old_node: UANode, new_node: UANode, old: ModelIndex, new: ModelIndex
) -> bool:
    """Whether the enclosing method already reports this argument variable (§3.2.11).

    An argument's DataType, ValueRank, ArrayDimensions and value list *are* the method
    signature; reporting them on the argument as well would state the same break twice.
    """
    if not isinstance(old_node, UAVariable) or not isinstance(new_node, UAVariable):
        return False
    if old_node.role is not NodeRole.ARGUMENT or new_node.role is not NodeRole.ARGUMENT:
        return False
    if _argument_method(old_node, old) is None or _argument_method(new_node, new) is None:
        return False
    return _argument_contract(old, old_node) != _argument_contract(new, new_node)


def _method_changes(
    old_node: UANode,
    new_node: UANode,
    old: ModelIndex,
    new: ModelIndex,
    moved: Mapping[str, str] = _NO_MOVES,
) -> list[Change]:
    """§3.2.11: a method keeps its arguments, their types, ranks and array dimensions."""
    if old_node.node_class is not NodeClass.METHOD and new_node.node_class is not NodeClass.METHOD:
        return []
    changes: list[Change] = []
    old_args = _method_arguments(old, old_node)
    new_args = _method_arguments(new, new_node)
    for name in sorted(set(old_args) - set(new_args)):
        changes.append(
            Change(
                area="method",
                kind="removed",
                subject=name,
                old=old_args[name],
                verdict=verdict("method_signature"),
            )
        )
    for name in sorted(set(new_args) - set(old_args)):
        changes.append(
            Change(
                area="method",
                kind="added",
                subject=name,
                new=new_args[name],
                verdict=verdict("method_signature"),
            )
        )
    for name in sorted(set(old_args) & set(new_args)):
        if old_args[name] != new_args[name]:
            changes.append(
                Change(
                    area="method",
                    kind="changed",
                    subject=name,
                    old=old_args[name],
                    new=new_args[name],
                    verdict=verdict("method_signature"),
                )
            )
    if not _same_target(
        old_node.method_declaration_id, new_node.method_declaration_id, moved
    ):
        changes.append(
            Change(
                area="method",
                kind="changed",
                subject="MethodDeclarationId",
                old=_reference_label(old, old_node.method_declaration_id),
                new=_reference_label(new, new_node.method_declaration_id),
                verdict=verdict("method_signature"),
            )
        )
    return changes


def _method_arguments(index: ModelIndex, node: UANode) -> dict[str, str]:
    """The argument variables a method is contracted with, keyed by their BrowseName."""
    arguments: dict[str, str] = {}
    for argument_id in index.arguments.get(_identity_text(node.node_id), ()):
        argument = index.nodes.get(identity(argument_id))
        if argument is None or not isinstance(argument, UAVariable):
            continue
        arguments[argument.browse_name.name or argument_id.identifier.as_xml()] = (
            _argument_contract(index, argument)
        )
    return arguments


def _argument_contract(index: ModelIndex, argument: UAVariable) -> str:
    """What a caller must pass for one argument."""
    parts = [
        f"DataType={index.label(argument.data_type) if argument.data_type else '-'}",
        f"ValueRank={_text(argument.value_rank) or '-'}",
        f"ArrayDimensions={_joined(argument.array_dimensions) or '-'}",
    ]
    if argument.role is NodeRole.ARGUMENT:
        # The older style spells the whole argument list inside the property value.
        listed = _normalized(argument.value_xml)
        if listed:
            parts.append(f"Arguments={listed}")
    return ", ".join(parts)


def _definition_changes(
    old_node: UANode,
    new_node: UANode,
    old: ModelIndex,
    new: ModelIndex,
    moved: Mapping[str, str] = _NO_MOVES,
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
        changes = _structure_changes(old_definition.fields, new_definition.fields, old, new, moved)
    elif new_definition.kind == "Union":
        changes = _union_changes(
            old_definition.union_alternatives,
            new_definition.union_alternatives,
            old,
            new,
            moved,
        )
    elif new_definition.kind == "Enum":
        changes = _value_member_changes(
            old_definition.enum_values, new_definition.enum_values, member="enum_value"
        )
    elif new_definition.kind == "OptionSet":
        changes = _value_member_changes(
            old_definition.option_set_values,
            new_definition.option_set_values,
            member="option_set_value",
        )
    else:
        changes = []
    duplicates = _duplicate_members(new_definition)
    if duplicates:
        # A name-keyed comparison cannot see two members of one name, so they are named here.
        changes.append(
            Change(
                area="definition",
                kind="duplicate_member",
                subject=new_definition.kind,
                new=", ".join(duplicates),
                verdict=verdict("duplicate_member"),
            )
        )
    return changes


def _duplicate_members(definition: Definition) -> tuple[str, ...]:
    """Member names the definition states more than once (§2.2, §2.3).

    The occurrences are counted before the name-keyed lookups are built: a dict would keep
    the last member of a name and hide the duplicate from every comparison.
    """
    groups: tuple[tuple[str, Sequence[str]], ...] = (
        ("field", [field.name for field in definition.fields]),
        ("enum value", [value.name for value in definition.enum_values]),
        ("OptionSet bit", [bit.name for bit in definition.option_set_values]),
        ("Union alternative", [alternative.name for alternative in definition.union_alternatives]),
    )
    found: list[str] = []
    for label, names in groups:
        for name, count in sorted(Counter(names).items()):
            if count > 1:
                found.append(f"{label} {name} ({count} occurrences)")
    return tuple(found)


def _structure_changes(
    old_fields: Sequence[StructureField],
    new_fields: Sequence[StructureField],
    old: ModelIndex,
    new: ModelIndex,
    moved: Mapping[str, str] = _NO_MOVES,
) -> list[Change]:
    old_by_name = {field.name: field for field in old_fields}
    new_by_name = {field.name: field for field in new_fields}
    changes: list[Change] = []
    field_context = RuleContext(member="field")
    renames = _rename_pairs(old_fields, new_fields)
    renamed = {name for pair in renames for name in pair[:2]}
    for old_name, new_name, confidence in renames:
        gone = old_by_name[old_name]
        fresh = new_by_name[new_name]
        rename_context = RuleContext(member="field", rename_confidence=confidence)
        changes.append(
            Change(
                area="definition",
                kind="renamed",
                subject=new_name,
                old=old_name,
                new=new_name,
                verdict=verdict("renamed", rename_context),
            )
        )
        changes.extend(_metadata_changes(new_name, gone.metadata, fresh.metadata))
        changes.extend(_member_text_changes(new_name, gone, fresh))
    for name in sorted(set(old_by_name) | set(new_by_name)):
        if name in renamed:
            continue
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
        if not _same_target(old_field.data_type, new_field.data_type, moved):
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


def _rename_pairs(
    old_fields: Sequence[StructureField], new_fields: Sequence[StructureField]
) -> tuple[tuple[str, str, str], ...]:
    """Fields that vanished and reappeared under a new name, plus how sure that pairing is.

    A rename is proven only when exactly one field was removed and exactly one was added and
    they encode the same DataType; anything else is a guess a reader has to check (§2.2), and
    a pair whose DataType differs is left as a real removal plus addition instead of folded
    into a rename that would hide the type change.
    """
    new_names = {field.name for field in new_fields}
    old_names = {field.name for field in old_fields}
    gone = [field for field in old_fields if field.name not in new_names]
    fresh = [field for field in new_fields if field.name not in old_names]
    if not gone or not fresh:
        return ()
    confidence = "exact" if len(gone) == len(fresh) == 1 else "heuristic"
    return tuple(
        (old_field.name, new_field.name, confidence)
        for old_field, new_field in zip(gone, fresh, strict=False)
        if old_field.data_type == new_field.data_type
    )


def _union_changes(
    old_alternatives: Sequence[UnionAlternative],
    new_alternatives: Sequence[UnionAlternative],
    old: ModelIndex,
    new: ModelIndex,
    moved: Mapping[str, str] = _NO_MOVES,
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
        if not _same_target(old_alternative.data_type, new_alternative.data_type, moved):
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
    """ValueRank, ArrayDimensions, MaxStringLength, AllowSubTypes: all part of the contract.

    Two of them are judged by their direction: a smaller limit or a switched-off
    AllowSubTypes rejects values a consumer already sent, while the other direction only
    widens the model (§3.2.5).
    """
    changes: list[Change] = []
    for attribute, label in FIELD_METADATA_LABELS:
        old_value = getattr(old_meta, attribute)
        new_value = getattr(new_meta, attribute)
        if old_value == new_value:
            continue
        context = RuleContext(member="member_metadata", metadata_attribute=attribute)
        if attribute == "max_string_length":
            context = replace(context, tightened=_limit_tightened(old_value, new_value))
        elif attribute == "allow_sub_types":
            context = replace(context, tightened=bool(old_value) and not bool(new_value))
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


def _limit_tightened(old_value: int | None, new_value: int | None) -> bool:
    """Whether a MaxStringLength change rejects values that were valid before (§3.2.5)."""
    old_limit = old_value if old_value else None
    new_limit = new_value if new_value else None
    if new_limit is None:
        return False
    return old_limit is None or new_limit < old_limit


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
    old_node: UANode,
    new_node: UANode,
    old: ModelIndex,
    new: ModelIndex,
    moved: Mapping[str, str] = _NO_MOVES,
) -> list[Change]:
    """Where a node sits in its hierarchy: base type, type definition, parent, interfaces.

    A target that only moved identifier is resolved through the paired move, so a renumbered
    base type or interface is not reported as a hierarchy move (§3.2.9, §3.2.17).
    """
    back = {new_text: old_text for old_text, new_text in moved.items()}
    changes: list[Change] = []
    old_base = _base_type(old_node)
    new_base = _base_type(new_node)
    inserted = _inserted_supertype(new_node, old, new)
    if _resolve(old_base or "", moved) != _resolve(new_base or "", moved):
        changes.append(
            _supertype_change(old_node, new_node, old, new, old_base, new_base, inserted)
        )
        changes.extend(_supertype_gap_changes(new_node, inserted, new))
    for attribute, subject in (
        ("type_definition", "HasTypeDefinition"),
        ("parent_node_id", "ParentNodeId"),
    ):
        old_value = getattr(old_node, attribute, None)
        new_value = getattr(new_node, attribute, None)
        if old_value == new_value:
            continue
        if old_value is not None and new_value is not None and _resolve(
            _identity_text(old_value), moved
        ) == _resolve(_identity_text(new_value), moved):
            continue
        rule = (
            _type_definition_verdict(old_node, new_node, old_value, new_value, new)
            if attribute == "type_definition"
            else verdict("hierarchy_move")
        )
        changes.append(
            Change(
                area="hierarchy",
                kind="moved",
                subject=subject,
                old=_reference_label(old, old_value),
                new=_reference_label(new, new_value),
                verdict=rule,
            )
        )
    old_interfaces = {_resolve(target, moved) for target in _interface_targets(old_node)}
    new_interfaces = {_resolve(target, moved) for target in _interface_targets(new_node)}
    for target in sorted(old_interfaces - new_interfaces):
        original = back.get(target, target)
        changes.append(
            Change(
                area="hierarchy",
                kind="removed",
                subject=f"HasInterface -> {_target_label(old, original, True)}",
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
        changes.extend(_interface_gap_changes(new_node, target, new))
    old_children = {_resolve(target, moved) for target in _subtype_children(old_node)}
    new_children = {_resolve(target, moved) for target in _subtype_children(new_node)}
    for target in sorted(old_children - new_children):
        original = back.get(target, target)
        changes.append(
            Change(
                area="hierarchy",
                kind="removed",
                subject=f"HasSubtype -> {_target_label(old, original, True)}",
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


def _inserted_supertype(new_node: UANode, old: ModelIndex, new: ModelIndex) -> UANode | None:
    """The supertype a node now inherits from, when that type did not exist before (§3.2.9)."""
    new_base = _base_type(new_node)
    if new_base is None or _lookup(old, new_base) is not None:
        return None
    return _lookup(new, new_base)


def _supertype_change(
    old_node: UANode,
    new_node: UANode,
    old: ModelIndex,
    new: ModelIndex,
    old_base: str | None,
    new_base: str | None,
    inserted: UANode | None,
) -> Change:
    """A changed HasSubtype: a move inside the hierarchy, or a new type inserted above it."""
    if old_node.node_class is NodeClass.REFERENCE_TYPE:
        if _is_hierarchical(old_node, old) != _is_hierarchical(new_node, new):
            rule, kind = verdict("rt_hierarchy_moved"), "moved"
        elif inserted is not None:
            rule, kind = verdict("rt_new_in_hierarchy"), "inserted"
        else:
            rule, kind = verdict("hierarchy_move"), "moved"
    elif inserted is None:
        rule, kind = verdict("hierarchy_move"), "moved"
    elif new_base is not None and _reaches(new, new_base, UA_EVENT_BASE_NAME):
        rule, kind = verdict("event_hierarchy_inserted"), "inserted"
    else:
        rule, kind = verdict("hierarchy_inserted"), "inserted"
    return Change(
        area="hierarchy",
        kind=kind,
        subject="HasSubtype",
        old=_target_label(old, old_base, False) if old_base else None,
        new=_target_label(new, new_base, False) if new_base else None,
        verdict=rule,
    )


def _supertype_gap_changes(
    subtype: UANode, supertype: UANode | None, new: ModelIndex
) -> list[Change]:
    """What a new supertype fails to carry for an existing subtype (§3.2.10, §3.2.9)."""
    if supertype is None:
        return []
    if not isinstance(subtype, UADataType) or not isinstance(supertype, UADataType):
        return []
    label = new.label(supertype.node_id)
    changes = [
        Change(
            area="hierarchy",
            kind="supertype_incomplete",
            new=f"{label} does not define {gap}",
            verdict=verdict("supertype_incomplete"),
        )
        for gap in _missing_members(supertype, subtype, new)
    ]
    missing = _mandatory_declaration_names(subtype, new) - _mandatory_declaration_names(
        supertype, new
    )
    changes.extend(
        Change(
            area="hierarchy",
            kind="supertype_incomplete",
            new=f"{label} does not make {gap} mandatory",
            verdict=verdict("supertype_incomplete"),
        )
        for gap in sorted(missing)
    )
    changes.extend(
        Change(
            area="hierarchy",
            kind="enum_conflict",
            new=f"{label}: {clash}",
            verdict=verdict("supertype_enum_conflict"),
        )
        for clash in _enum_clashes(supertype, subtype)
    )
    return changes


#: One member of a definition, whatever kind of definition carries it.
DefinitionMember = StructureField | EnumValue | OptionSetValue | UnionAlternative


def _members(definition: Definition) -> tuple[DefinitionMember, ...]:
    """The members of a definition, whatever kind it carries."""
    if definition.kind == "Enum":
        return tuple(definition.enum_values)
    if definition.kind == "OptionSet":
        return tuple(definition.option_set_values)
    if definition.kind == "Union":
        return tuple(definition.union_alternatives)
    return tuple(definition.fields)


#: How a member of each definition kind is named in a report (§3.2.4, §3.2.5, §3.2.6, §3.2.7).
_MEMBER_KIND_LABELS = {
    "Enum": "enumeration value",
    "OptionSet": "OptionSet bit",
    "Union": "Union alternative",
    "Structure": "field",
}


def _missing_members(supertype: UADataType, subtype: UADataType, index: ModelIndex) -> list[str]:
    """Members the subtype already had that the new supertype does not define (§3.2.10)."""
    supertype_definition = supertype.definition
    subtype_definition = subtype.definition
    if supertype_definition is None or subtype_definition is None:
        return []
    if supertype_definition.kind != subtype_definition.kind:
        return []
    if subtype_definition.kind == "Structure":
        fields = {item.name: item for item in supertype_definition.fields}
        return [
            (
                f"field {item.name}"
                if item.name not in fields
                else f"field {item.name} as {_spell_field(item, index)}"
            )
            for item in subtype_definition.fields
            if item.name not in fields
            or _field_contract(fields[item.name]) != _field_contract(item)
        ]
    carried = {member.name for member in _members(supertype_definition)}
    label = _MEMBER_KIND_LABELS[subtype_definition.kind]
    return [
        f"{label} {member.name}"
        for member in _members(subtype_definition)
        if member.name not in carried
    ]


def _field_contract(
    structure_field: StructureField,
) -> tuple[ExpandedNodeId | None, int | None, tuple[str, ...]]:
    """What a field promises: its DataType plus the array contract (§3.2.10)."""
    metadata = structure_field.metadata
    return (structure_field.data_type, metadata.value_rank, metadata.array_dimensions)


def _spell_field(structure_field: StructureField, index: ModelIndex) -> str:
    """A field contract spelled out, so a report says what the subtype expected."""
    parts = [index.label(structure_field.data_type)]
    if structure_field.metadata.value_rank is not None:
        parts.append(f"ValueRank {structure_field.metadata.value_rank}")
    if structure_field.metadata.array_dimensions:
        parts.append(f"ArrayDimensions {_joined(structure_field.metadata.array_dimensions)}")
    return ", ".join(parts)


def _enum_clashes(supertype: UADataType, subtype: UADataType) -> list[str]:
    """Numeric values that make a supertype impossible to create over its subtypes (§3.2.10)."""
    supertype_definition = supertype.definition
    subtype_definition = subtype.definition
    if supertype_definition is None or subtype_definition is None:
        return []
    if supertype_definition.kind != "Enum" or subtype_definition.kind != "Enum":
        return []
    numbers = {value.name: value.value for value in supertype_definition.enum_values}
    return [
        f"{value.name}: supertype {numbers[value.name]}, subtype {value.value}"
        for value in subtype_definition.enum_values
        if value.name in numbers and numbers[value.name] != value.value
    ]


def _type_definition_verdict(
    old_node: UANode,
    new_node: UANode,
    old_value: ExpandedNodeId | None,
    new_value: ExpandedNodeId | None,
    new: ModelIndex,
) -> RuleVerdict:
    """§3.2.13: an InstanceDeclaration may move to a subtype only when that adds nothing."""
    if old_value is None or new_value is None:
        return verdict("typedefinition_moved")
    old_text = _identity_text(old_value)
    new_text = _identity_text(new_value)
    old_type = _lookup(new, old_text)
    new_type = _lookup(new, new_text)
    if old_type is None or new_type is None:
        return verdict("typedefinition_moved")
    if old_text not in _ancestors(new, new_text):
        return verdict("typedefinition_moved")
    if _mandatory_declaration_names(new_type, new) - _mandatory_declaration_names(old_type, new):
        return verdict("typedefinition_moved")
    if _refines_data_type(old_node, new_node, new):
        return verdict("typedefinition_moved")
    return verdict("typedefinition_refined")


def _refines_data_type(old_node: UANode, new_node: UANode, new: ModelIndex) -> bool:
    """Whether a Variable moved onto a subtype of its DataType (§3.2.13, §3.2.12)."""
    if not isinstance(old_node, UAVariable | UAVariableType):
        return False
    if not isinstance(new_node, UAVariable | UAVariableType):
        return False
    if old_node.data_type is None or new_node.data_type is None:
        return False
    if new_node.data_type == old_node.data_type:
        return False
    return _identity_text(old_node.data_type) in _ancestors(new, _identity_text(new_node.data_type))


def _interface_gap_changes(node: UANode, interface_target: str, new: ModelIndex) -> list[Change]:
    """Mandatory declarations an added Interface brings that the type did not have (§3.2.3)."""
    interface = _lookup(new, interface_target)
    if interface is None:
        return []
    gaps = _mandatory_declaration_names(interface, new) - _mandatory_declaration_names(node, new)
    if not gaps:
        return []
    label = new.label(interface.node_id)
    return [
        Change(
            area="hierarchy",
            kind="interface_gap",
            new=f"{label} makes {gap} mandatory",
            verdict=verdict("interface_mandatory_added"),
        )
        for gap in sorted(gaps)
    ]


def _mandatory_declaration_names(node: UANode, index: ModelIndex) -> set[str]:
    """The declarations a type requires, so a rule can tell whether one was added (§3.2.1)."""
    return _declaration_names(node, index, mandatory_only=True)


def _declaration_names(node: UANode, index: ModelIndex, *, mandatory_only: bool) -> set[str]:
    """BrowseNames of the declarations a type carries, inherited ones included (§3.2.3).

    Walks the ownership edges the semantic graph follows and climbs the HasSubtype edges, so
    a type is judged against everything its instances actually see.  Nodes of the OPC UA
    namespace declare no modelling rules and are left out.
    """
    names: set[str] = set()
    seen: set[str] = set()
    pending = [node]
    while pending:
        current = pending.pop()
        own = _identity_text(current.node_id)
        if own in seen:
            continue
        seen.add(own)
        for reference in current.references:
            name = _reference_name(reference)
            target = _identity_text(reference.target)
            if name in OWNERSHIP_REFERENCES and reference.is_forward:
                child = _lookup(index, target)
                if child is None or child.node_id.namespace == UA_NAMESPACE_URI:
                    continue
                if not mandatory_only or _is_explicitly_mandatory(child):
                    names.add(child.browse_name.name)
                pending.append(child)
            elif name == "HasSubtype" and not reference.is_forward:
                parent = _lookup(index, target)
                if parent is not None:
                    pending.append(parent)
    return names


def _supertypes(index: ModelIndex, identity_text: str) -> tuple[str, ...]:
    """Identity texts of the types a node inherits from (§3.2.9, §3.2.15)."""
    return index.supertypes.get(identity_text, ())


def _ancestors(index: ModelIndex, identity_text: str) -> set[str]:
    """Every type above a node, transitively; a cycle in the model stops at the first repeat."""
    seen: set[str] = set()
    pending = list(_supertypes(index, identity_text))
    while pending:
        current = pending.pop()
        if current in seen:
            continue
        seen.add(current)
        pending.extend(_supertypes(index, current))
    return seen


def _reaches(index: ModelIndex, identity_text: str, browse_name: str) -> bool:
    """Whether the supertype chain of a node reaches a named OPC UA type (§3.2.9, §3.2.15)."""
    for ancestor in _ancestors(index, identity_text):
        node = _lookup(index, ancestor)
        if (
            node is not None
            and node.node_id.namespace == UA_NAMESPACE_URI
            and node.browse_name.name == browse_name
        ):
            return True
    return False


def _is_hierarchical(node: UANode, index: ModelIndex) -> bool:
    """Whether a ReferenceType inherits HierarchicalReference (§3.2.15), which decides filtering."""
    return _reaches(index, _identity_text(node.node_id), UA_HIERARCHICAL_REFERENCE_NAME)


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


def _reference_label(index: ModelIndex, node_id: ExpandedNodeId | None) -> str | None:
    """The target of a hierarchy attribute, named the way the report names other targets."""
    if node_id is None:
        return None
    return index.label(node_id)


def _reference_changes(
    old_node: UANode,
    new_node: UANode,
    old: ModelIndex,
    new: ModelIndex,
    moved: Mapping[str, str] = _NO_MOVES,
) -> list[Change]:
    """Added and removed references, keyed by reference type, target identity and direction.

    The key is the *identifier* of the reference type, never its browse name: `HasEncoding`
    and `i=38` are one reference, and renaming the declaration is a finding on that declaration,
    not a removal plus an addition on every node that points at it.
    Multiplicity counts: a reference declared twice in the old model and once in the new one is
    a change, not an unchanged reference — a `set` used to hide that.
    A target that only moved identifier is resolved through the paired move: the move is a
    finding on the node itself, and must not read here as a detached child (§3.2.16, §3.2.17).
    """
    back = {new_text: old_text for old_text, new_text in moved.items()}
    old_counts = Counter(_resolved_key(ref, moved) for ref in old_node.references)
    new_counts = Counter(_resolved_key(ref, moved) for ref in new_node.references)
    old_names = {_resolved_key(ref, moved): _reference_name(ref) for ref in old_node.references}
    new_names = {_resolved_key(ref, moved): _reference_name(ref) for ref in new_node.references}
    changes: list[Change] = []
    for key in sorted(set(old_counts) | set(new_counts)):
        type_identity, target, is_forward = key
        old_name = _reference_type_label(old, type_identity, old_names.get(key, ""))
        new_name = _reference_type_label(new, type_identity, new_names.get(key, ""))
        if new_name in HIERARCHY_REFERENCES or old_name in HIERARCHY_REFERENCES:
            continue
        old_count = old_counts.get(key, 0)
        new_count = new_counts.get(key, 0)
        # A move of the target's identifier is reported once, on the node that moved.
        if old_count > new_count:
            original = back.get(target, target)
            changes.append(
                Change(
                    area="reference",
                    kind="removed",
                    subject=_reference_subject(
                        old_name, old, original, is_forward, old_count, new_count
                    ),
                    old=old_name,
                    verdict=verdict(
                        "removed",
                        RuleContext(
                            reference=_reference_category(old_name, original, old, old_node)
                        ),
                    ),
                )
            )
        elif new_count > old_count:
            changes.append(
                Change(
                    area="reference",
                    kind="added",
                    subject=_reference_subject(
                        new_name, new, target, is_forward, old_count, new_count
                    ),
                    new=new_name,
                    verdict=verdict(
                        "added",
                        RuleContext(reference=_reference_category(new_name, target, new, new_node)),
                    ),
                )
            )
            version = (
                _deprecation_target_name(new, target)
                if new_name in DEPRECATION_REFERENCES
                else None
            )
            if version is not None:
                changes.append(
                    Change(
                        area="reference",
                        kind="deprecation_version",
                        subject="Deprecation version",
                        new=version,
                        verdict=verdict("deprecation_version"),
                    )
                )
    return changes


def _reference_type_label(index: ModelIndex, type_identity: str, name: str) -> str:
    """The name a report shows for a reference type.

    A document may spell the type as an identifier without naming it; the declaration of that
    identifier supplies the name, so hierarchy and contract rules can still recognise it.
    """
    if name:
        return name
    if type_identity.startswith("name:"):
        return ""
    declaration = _lookup(index, type_identity)
    return declaration.browse_name.name if declaration is not None else ""


def _reference_subject(
    name: str, index: ModelIndex, target: str, is_forward: bool, old_count: int, new_count: int
) -> str:
    """`HasComponent -> Target`, with the multiplicity stated when a reference was duplicated."""
    subject = f"{name or 'Reference'} -> {_target_label(index, target, is_forward)}"
    if old_count > 1 or new_count > 1:
        subject += f" ({old_count} -> {new_count})"
    return subject


def _deprecation_target_name(index: ModelIndex, target: str) -> str | None:
    """The BrowseName of a deprecation object that names its version (§14.3.2)."""
    node = _lookup(index, target)
    if node is None or node.node_class is not NodeClass.OBJECT:
        return None
    return node.browse_name.name if DEPRECATION_OBJECT_NAME.search(node.browse_name.name) else None


def _reference_name(ref: Reference) -> str:
    """The name of a reference's type, as the document spelled it."""
    return ref.type_name or (str(ref.reference_type) if ref.reference_type is not None else "")


def _reference_key(ref: Reference) -> tuple[str, str, bool]:
    """Identity key of a reference: reference type identity, target identity, direction.

    Two spellings of one reference type — `ReferenceType="HasEncoding"` and
    `ReferenceType="i=38"` — are the same reference, and a renamed reference type declaration is
    a change on that declaration, not one removal plus one addition on every node pointing at
    it; both need the identifier of the type, never its browse name.  A reference that names no
    identifier at all falls back to its name, so it still pairs with itself.
    """
    type_identity = (
        _identity_text(ref.reference_type)
        if ref.reference_type is not None
        else f"name:{ref.type_name}"
    )
    return (type_identity, _identity_text(ref.target), ref.is_forward)


def _resolved_key(ref: Reference, moved: Mapping[str, str]) -> tuple[str, str, bool]:
    type_identity, target, is_forward = _reference_key(ref)
    return (type_identity, _resolve(target, moved), is_forward)


def _resolve(identity_text: str, moved: Mapping[str, str]) -> str:
    """The identifier a paired move carries now.

    A renumbered node is the same node (§3.2.17), so anything pointing at it — a reference, a
    base type, a type definition, a datatype — describes the same contract and is not a second
    change; the move is reported once, on the node.
    """
    return moved.get(identity_text, identity_text)


def _same_target(
    old_value: ExpandedNodeId | None, new_value: ExpandedNodeId | None, moved: Mapping[str, str]
) -> bool:
    """Whether two spelled identifiers name the same node once paired moves are resolved."""
    if old_value == new_value:
        return True
    if old_value is None or new_value is None:
        return False
    return _resolve(_identity_text(old_value), moved) == _resolve(_identity_text(new_value), moved)


#: Separator of the internal identity text.  An XML attribute value cannot carry a control
#: character (a document containing one is not well-formed), so it can never appear in a
#: NamespaceUri, a NodeId or a ServerUri.  ``|`` can: ``s=…`` identifiers may legally contain
#: it, and splitting on it would read back a different identifier — with a ``srv=`` part
#: present, even the identifier itself was corrupted.
_IDENTITY_SEPARATOR = "\x1f"


def _identity_text(node_id: ExpandedNodeId) -> str:
    """Identity as text: `namespace␟identifier␟server`, the third part empty when unqualified.

    The server part belongs here for the same reason it belongs to :func:`identity`: a
    server-qualified identifier is a different node, and a text without it would merge the two.
    This text is a key inside the engine only — a report spells identifiers with
    :meth:`ExpandedNodeId.as_xml`, never with this encoding.
    """
    return _IDENTITY_SEPARATOR.join(
        (node_id.namespace, node_id.identifier.as_xml(), node_id.server_uri or "")
    )


def _identity_parts(identity_text: str) -> tuple[str, str, str | None]:
    """The namespace, identifier and server an identity text encodes."""
    namespace, local, server = identity_text.split(_IDENTITY_SEPARATOR)
    return namespace, local, server or None


def _identity_node(identity_text: str) -> ExpandedNodeId:
    """The identifier an identity text describes."""
    namespace, local, server = _identity_parts(identity_text)
    return ExpandedNodeId(
        identifier=NodeId.parse(local), namespace_uri=namespace, server_uri=server
    )


def _lookup(index: ModelIndex, identity_text: str) -> UANode | None:
    return index.node(_identity_node(identity_text))


def _target_label(index: ModelIndex, identity_text: str, is_forward: bool) -> str:
    label = index.label(_identity_node(identity_text))
    return label if is_forward else f"{label} (inverse)"


def _reference_category(
    name: str, target: str, index: ModelIndex, owner: UANode | None = None
) -> str:
    """Which kind of reference this is, so the rule table can judge its removal.

    Contract references (subtype, type definition, interface, encoding, event) break a
    consumer; navigation references only change browsing; an ownership reference breaks
    when its target is an explicitly mandatory declaration; an argument description
    reference (§5) is documentation; an IsDeprecated reference is judged by the deprecation
    rules (§14.2).  Members of a ConformanceUnit or Profile are judged by the conformance
    rules (§12.1, §12.2), because they declare a test scope rather than a model contract.
    """
    if owner is not None and _conformance_role(owner) is not None:
        return "cu_dependency"
    if name in DEPRECATION_REFERENCES:
        return "deprecation"
    if name in ARGUMENT_DESCRIPTION_REFERENCES:
        # Documentation about an argument (§5), never a declaration the caller depends on.
        return "argument_metadata"
    if name in SOFT_REFERENCES:
        return "soft"
    if name in HARD_REFERENCES:
        return "hard"
    node = _lookup(index, target)
    if node is not None and name in OWNERSHIP_REFERENCES:
        return "mandatory_child" if _is_explicitly_mandatory(node) else "optional_child"
    return "other"


def _namespace_changes(old: ModelIndex, new: ModelIndex) -> list[NamespaceChange]:
    """Totals *and* release metadata per namespace (§11.1, §11.2, §11.3).

    A namespace also appears when only its metadata moved — a version bump with no node
    change is a release fact, not a modelling fact, and a release note has to name it.
    """
    old_totals = _namespace_totals(old.model)
    new_totals = _namespace_totals(new.model)
    renamed = _namespace_renames(old.model, new.model, old_totals, new_totals)
    conflicts = _metadata_conflicts(new.model)
    old_deprecated = _deprecation_counts(old.model)
    new_deprecated = _deprecation_counts(new.model)
    new_conformance = _conformance_declarations(new.model)
    uris = (
        set(old_totals)
        | set(new_totals)
        | set(old.model.namespaces)
        | set(new.model.namespaces)
    ) - set(renamed.values())
    changes: list[NamespaceChange] = []
    for uri in sorted(uris):
        source = renamed.get(uri)
        before = old_totals.get(source or uri, NamespaceTotals())
        after = new_totals.get(uri, NamespaceTotals())
        metadata = _release_metadata(uri, source, old.model, new.model)
        metadata.extend(_model_deltas(uri, source, old.model, new.model))
        metadata.extend(_version_drift(uri, new.model))
        metadata.extend(conflicts.get(uri, ()))
        metadata.extend(_deprecation_delta(uri, source, old_deprecated, new_deprecated, old.model))
        if (before.nodes, before.references, before.datatypes) == (
            after.nodes,
            after.references,
            after.datatypes,
        ) and not metadata:
            continue
        verdicts: list[RuleVerdict] = []
        if source is None and uri not in old.model.namespaces and before.nodes == 0:
            verdicts.append(verdict("namespace_added"))
            # A new namespace needs its own ConformanceUnits and Profiles (§11.3, §12.1).
            if uri != UA_NAMESPACE_URI and new_conformance.get(uri, 0) == 0:
                verdicts.append(verdict("namespace_without_cu"))
        elif source is None and uri not in new.model.namespaces and after.nodes == 0:
            verdicts.append(verdict("namespace_removed"))
        changes.append(
            NamespaceChange(
                alias=new.alias(uri),
                uri=uri,
                nodes=(before.nodes, after.nodes),
                references=(before.references, after.references),
                datatypes=(before.datatypes, after.datatypes),
                metadata=tuple(metadata),
                verdicts=tuple(verdicts),
            )
        )
    return changes


def _namespace_renames(
    old: SemanticModel, new: SemanticModel, old_totals: Mapping[str, NamespaceTotals],
    new_totals: Mapping[str, NamespaceTotals],
) -> dict[str, str]:
    """Pair a namespace that vanished with one that appeared under the same alias.

    A renamed NamespaceUri is breaking (§11.3), but the diff only sees one URI leave and
    another arrive.  A shared alias *and* identical node totals is the evidence that the
    two are the same model; without it, a new namespace stays a new namespace.
    """
    gone = {uri for uri in old.namespaces if uri not in new.namespaces}
    fresh = {uri for uri in new.namespaces if uri not in old.namespaces}
    by_alias = {_namespace_label(old.namespaces[uri]): uri for uri in gone}
    pairs: dict[str, str] = {}
    for uri in fresh:
        source = by_alias.get(_namespace_label(new.namespaces[uri]))
        if source is not None and old_totals.get(source, NamespaceTotals()) == new_totals.get(
            uri, NamespaceTotals()
        ):
            pairs[uri] = source
    return pairs


def _namespace_label(namespace: Namespace) -> str:
    return namespace.alias or alias_from_uri(namespace.uri) or namespace.uri


def _release_metadata(
    uri: str, source: str | None, old: SemanticModel, new: SemanticModel
) -> list[MetadataDelta]:
    """Namespace metadata that moved between the two releases (§11.1, §11.3)."""
    deltas: list[MetadataDelta] = []
    if source is not None:
        deltas.append(MetadataDelta("uri", verdict("namespace_uri_changed"), source, uri))
    before = old.namespaces.get(source or uri)
    after = new.namespaces.get(uri)
    if before is None or after is None:
        return deltas
    for attribute, rule_kind in _METADATA_RULES.items():
        old_value = getattr(before, attribute)
        new_value = getattr(after, attribute)
        if attribute in _VERSION_ATTRIBUTES and versions_equal(old_value, new_value):
            # One release in two spellings is not a release move (Annex F.2).
            continue
        if old_value != new_value:
            deltas.append(
                MetadataDelta(attribute, verdict(rule_kind), old_value, new_value)
            )
    return deltas


def _model_deltas(
    uri: str, source: str | None, old: SemanticModel, new: SemanticModel
) -> list[MetadataDelta]:
    """What the Model table now requires: the dependency set and the versions it claims."""
    before = old.models.get(source or uri)
    after = new.models.get(uri)
    if before is None or after is None:
        return []
    deltas: list[MetadataDelta] = []
    required_before = tuple(sorted(before.required_models))
    required_after = tuple(sorted(after.required_models))
    if required_before != required_after:
        deltas.append(
            MetadataDelta(
                "required_models",
                verdict("namespace_required_model_changed"),
                _joined(required_before),
                _joined(required_after),
            )
        )
    versions_before = {
        entry.namespace_uri: _required_claim(entry) for entry in before.required_model_entries
    }
    versions_after = {
        entry.namespace_uri: _required_claim(entry) for entry in after.required_model_entries
    }
    for dependency in sorted(set(versions_before) | set(versions_after)):
        old_version = versions_before.get(dependency)
        new_version = versions_after.get(dependency)
        if versions_equal(old_version, new_version):
            continue
        deltas.append(
            MetadataDelta(
                "required_model_version",
                verdict("namespace_model_version_changed"),
                old_version,
                new_version,
                note=dependency,
            )
        )
    return deltas


def _required_claim(entry: ModelInfo) -> str | None:
    """The version a dependency claims: the comparable ``ModelVersion``, display-only else.

    Annex F.2 states a minimum version through ``ModelVersion`` and ``PublicationDate``; the
    human readable ``Version`` is the only claim older documents carry.
    """
    return entry.model_version or entry.version


def _version_drift(uri: str, new: SemanticModel) -> list[MetadataDelta]:
    """The two version claims of one namespace (§11.1, OPC 10000-6 Annex F.2, §12.41).

    ``Version`` is a display string that Annex F.2 excludes from programmatic comparisons, while
    ``ModelVersion`` is the ``SemanticVersionString`` consumers actually compare.  A padded
    display spelling of the same release — ``1.04.0`` beside ``1.4.0`` — is therefore one release,
    not a contradiction: only a numeric disagreement is reported, as an advisory to review by
    hand.  A ``ModelVersion`` that is not valid SemVer is reported separately, because it is the
    field the comparison depends on.
    """
    namespace = new.namespaces.get(uri)
    if namespace is None:
        return []
    deltas: list[MetadataDelta] = []
    if namespace.model_version is not None and not is_semantic(namespace.model_version):
        deltas.append(
            MetadataDelta(
                "model_version",
                verdict("namespace_model_version_syntax"),
                note=(
                    f"ModelVersion {namespace.model_version} of {uri} is not a "
                    "SemanticVersionString (§12.41): consumers compare this field, so spell it "
                    "as major.minor.patch without leading zeros"
                ),
            )
        )
    if namespace.version is None or namespace.model_version is None:
        return deltas
    if versions_equal(namespace.version, namespace.model_version):
        return deltas
    return [
        MetadataDelta(
            "version",
            verdict("namespace_version_drift"),
            note=(
                f"Version {namespace.version} and ModelVersion {namespace.model_version} of {uri} "
                f"state different releases ({canonical(namespace.version)} vs "
                f"{canonical(namespace.model_version)}): review which release is meant"
            ),
        )
    ]


def _deprecation_delta(
    uri: str,
    source: str | None,
    before: Mapping[str, int],
    after: Mapping[str, int],
    old: SemanticModel,
) -> list[MetadataDelta]:
    """Deprecations added inside the NamespaceUri a release kept (§14.3).

    A release that moved to another URI or introduced one did what §14.3 asks for; a namespace
    that stays published keeps accumulating nodes clients should stop using and can never drop.
    """
    if source is not None or uri not in old.namespaces:
        return []
    gained = after.get(uri, 0) - before.get(uri, 0)
    if gained <= 0:
        return []
    return [
        MetadataDelta(
            "deprecations",
            verdict("deprecations_without_new_namespace"),
            old=str(before.get(uri, 0)),
            new=str(after.get(uri, 0)),
        )
    ]


def _metadata_conflicts(model: SemanticModel) -> dict[str, list[MetadataDelta]]:
    """Namespace and Model metadata two source documents declared differently (§11.2)."""
    namespaces: dict[str, list[Namespace]] = {}
    models: dict[str, list[ModelInfo]] = {}
    for document in model.documents:
        for namespace in document.namespaces:
            namespaces.setdefault(namespace.uri, []).append(namespace)
        for declaration in document.models:
            models.setdefault(declaration.namespace_uri, []).append(declaration)
    conflicts: dict[str, list[MetadataDelta]] = {}
    for uri, seen in sorted(namespaces.items()):
        if len(seen) < 2:
            continue
        for attribute, label in _CONFLICT_ATTRIBUTES.items():
            claims = [
                declared
                for namespace in seen
                if (declared := getattr(namespace, attribute))
            ]
            values = _distinct_claims(claims, numeric=attribute in _VERSION_ATTRIBUTES)
            if len(values) > 1:
                conflicts.setdefault(uri, []).append(
                    MetadataDelta(
                        attribute,
                        verdict("namespace_metadata_conflict"),
                        note=(
                            f"{label} of {uri} declared as {' and '.join(values)} across "
                            "documents; the merge kept one"
                        ),
                    )
                )
    for uri, declarations in sorted(models.items()):
        if len(declarations) < 2:
            continue
        values = sorted(
            {
                spelled
                for entry in declarations
                if (spelled := _spell_model(entry))
            }
        )
        if len(values) > 1:
            conflicts.setdefault(uri, []).append(
                MetadataDelta(
                    "model",
                    verdict("namespace_model_precedence"),
                    note=(
                        f"Model of {uri} declared as {' and '.join(values)} across documents; "
                        "the entry carrying a version wins"
                    ),
                )
            )
    return conflicts


def _distinct_claims(values: Sequence[str], *, numeric: bool) -> list[str]:
    """Metadata spellings that state *different* claims, one representative each.

    Version fields are deduplicated per release rather than per spelling: ``1.04.0`` and
    ``1.4.0`` are one release written twice (§11.1, Annex F.2), so documents that disagree only
    about the spelling declare the same claim and do not conflict.
    """
    if not numeric:
        return sorted(set(values))
    per_release: dict[str, list[str]] = {}
    for value in values:
        per_release.setdefault(canonical(value) or value, []).append(value)
    return sorted(min(group) for group in per_release.values())


def _spell_model(entry: ModelInfo) -> str:
    parts = [entry.namespace_uri]
    if entry.version:
        parts.append(f"Version {entry.version}")
    if entry.publication_date:
        parts.append(f"PublicationDate {entry.publication_date}")
    if entry.required_models:
        parts.append(f"RequiredModel {_joined(entry.required_models)}")
    return ", ".join(parts)


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


def _normalized_extension(values: Sequence[str]) -> tuple[str, ...]:
    """Extension content with XML formatting removed (§3.2.17).

    Indentation and padding around tags is spelling, not payload: only the content of an
    extension is compared.
    """
    return tuple("".join(value.split()) for value in values)


def _value_dependencies(node: UANode) -> tuple[tuple[str, str], ...]:
    """Where a value points: dependency name and the identifier it resolves to (§3.2.17).

    The target is spelled in the canonical XML form (`nsu=…;i=…`), which is what the parser
    resolved a document-local index into — so a dependency the linker externalized and a
    dependency a document re-indexed both keep their identity, neither reads as changed.
    """
    return tuple(
        (name, identifier.as_xml()) for name, identifier in node.value_dependencies
    )


def _spell_dependencies(dependencies: tuple[tuple[str, str], ...]) -> str:
    return ", ".join(f"{name} -> {target}" for name, target in sorted(dependencies))


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
