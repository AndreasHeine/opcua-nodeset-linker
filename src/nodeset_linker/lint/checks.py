"""Best-practice checks over one semantic model.

Every check reads domain objects, never XML: a NodeSet is a serialization, and the model is the
modelling truth.  Checks that judge a whole namespace (filename, NamespaceUri) look at the
document and namespace metadata instead of at nodes.
"""

from __future__ import annotations

import re
from collections import deque
from pathlib import Path

from nodeset_linker.lint.report import LintFinding, finding
from nodeset_linker.lint.rules import LINT_RULES, LintRule
from nodeset_linker.models.identifiers import UA_NAMESPACE_URI, ExpandedNodeId
from nodeset_linker.models.namespace import alias_from_uri
from nodeset_linker.models.node import UANode
from nodeset_linker.models.node_class import ModellingRule, NodeClass, NodeRole
from nodeset_linker.repository.model import SemanticModel

#: References that build a BrowsePath (§2.1.4) — children of a type definition or a method.
PATH_EDGE_NAMES = frozenset(
    {
        "HasComponent",
        "HasProperty",
        "HasObject",
        "HasVariable",
        "HasChild",
        "HasOrderedComponent",
        "HasArgument",
    }
)
#: Containment only: arguments are not part of a recursive containment chain (§7.10).
CONTAINMENT_NAMES = PATH_EDGE_NAMES - {"HasArgument"}
#: Rules that force every server to instantiate a declaration (§7.10).
_MANDATORY_RULES = frozenset({ModellingRule.MANDATORY, ModellingRule.MANDATORY_PLACEHOLDER})
#: Nodes a BrowsePath is rooted at, and the names that must be unique in a namespace (§2.1.4).
TYPE_DEFINITION_CLASSES = frozenset(
    {NodeClass.OBJECT_TYPE, NodeClass.VARIABLE_TYPE, NodeClass.INTERFACE}
)
TYPE_NAME_CLASSES = TYPE_DEFINITION_CLASSES | {NodeClass.DATA_TYPE, NodeClass.REFERENCE_TYPE}
#: Relationship names that start a ReferenceType BrowseName (§2.1.3).
REFERENCE_VERB_PREFIXES = frozenset(
    {
        "Has",
        "Is",
        "Organizes",
        "Aggregates",
        "Contains",
        "Connects",
        "Calls",
        "Causes",
        "Effects",
        "Generates",
        "References",
        "Overrides",
    }
)
#: Names that may not carry a type suffix (§2.1.3).
ENUM_LIKE_ROLES = frozenset({NodeRole.ENUM, NodeRole.OPTION_SET})
#: The limit a Structure encoding imposes on optional fields (§7.9).
MAX_OPTIONAL_FIELDS = 32
#: Text attributes that carry BrowseNames in prose (§13.3), as (attribute, label).
TEXT_ATTRIBUTES = (
    ("display_name", "DisplayName"),
    ("description", "Description"),
    ("documentation", "Documentation"),
)

_SAFE_CHARS = re.compile(r"[A-Za-z0-9_]+")
_ACRONYM = re.compile(r"[A-Z]{2,}")
_INTERFACE_PREFIX = re.compile(r"^I[A-Z][a-z]")
_FIRST_WORD = re.compile(r"[A-Z][a-z0-9]*")
_PLACEHOLDER_NAME = re.compile(r"^<[^>]*>$")
_PERCENT_ESCAPE = re.compile(r"%[0-9A-Fa-f]{2}")
#: A NamespaceIndex written into text a server will re-index differently (§13.3).
_NAMESPACE_INDEX_IN_TEXT = re.compile(r"(?<![\w])\d+:[A-Za-z]")
_STATUS_CODE_SUFFIX = re.compile(r"StatusCode$")
_AUDIT_EVENT_PREFIX = "Audit"
_OPC_FOUNDATION_PREFIX = "http://opcfoundation.org/UA/"
_BASE_STATUS_CODE_NAME = "StatusCode"


def _rule(code: str) -> LintRule:
    return LINT_RULES[code]


def _is_companion(node: UANode) -> bool:
    """Whether the node belongs to a companion or vendor namespace, not to OPC UA core."""
    return node.node_id.namespace != UA_NAMESPACE_URI


def _is_pascal(name: str) -> bool:
    """PascalCase of §2.1.2: words run together, each starting upper-case.

    Acronyms are written as words (``PortMacAddress``, not ``MACAddress``), digits are allowed
    (``UInt32``), and the single leading ``I`` of an interface name is not an acronym.
    """
    if not _SAFE_CHARS.fullmatch(name):
        return False
    words = name[1:] if _INTERFACE_PREFIX.match(name) else name
    return words[:1].isupper() and not _ACRONYM.search(words)


def _first_word(name: str) -> str | None:
    match = _FIRST_WORD.match(name)
    return match.group() if match else None


def _parents(
    model: SemanticModel, edge_names: frozenset[str]
) -> dict[ExpandedNodeId, list[ExpandedNodeId]]:
    """Map each node to the nodes that contain it, following ``edge_names`` backwards."""
    parents: dict[ExpandedNodeId, list[ExpandedNodeId]] = {}
    for node in model.iter_nodes():
        for reference in node.references:
            if reference.type_name in edge_names and reference.is_forward:
                parents.setdefault(reference.target, []).append(node.node_id)
    return parents


def _subtype_ancestors(model: SemanticModel, node: UANode) -> list[UANode]:
    """The base types of a node, as far as the loaded model resolves them."""
    seen: set[ExpandedNodeId] = set()
    ancestors: list[UANode] = []
    current = node
    while True:
        target = next(
            (
                reference.target
                for reference in current.references
                if reference.type_name == "HasSubtype" and reference.is_forward
            ),
            None,
        )
        if target is None or target in seen:
            return ancestors
        parent = model.get(target)
        if parent is None:
            return ancestors
        seen.add(target)
        ancestors.append(parent)
        current = parent


def _browse_name_findings(model: SemanticModel) -> list[LintFinding]:
    """Naming of BrowseNames (§2.1.2 - §2.1.3); argument names are judged under §2.6 instead."""
    found: list[LintFinding] = []
    for node in model.iter_nodes():
        if not _is_companion(node) or _PLACEHOLDER_NAME.match(node.browse_name.name):
            continue
        name = node.browse_name.name
        special = not _SAFE_CHARS.fullmatch(name)
        if special:
            if node.symbolic_name is None:
                found.append(
                    finding(
                        _rule("BROWSE_NAME_SPECIAL_CHARS_WITHOUT_SYMBOLIC_NAME"),
                        f"BrowseName '{name}' has characters no generated identifier can carry",
                        node.node_id,
                    )
                )
        elif not _is_pascal(name) and node.role is not NodeRole.ARGUMENT:
            found.append(
                finding(
                    _rule("BROWSE_NAME_NOT_PASCAL_CASE"),
                    f"BrowseName '{name}' is not PascalCase",
                    node.node_id,
                )
            )
        if not special:
            found.extend(_suffix_findings(node))
        found.extend(_reference_name_findings(model, node))
        found.extend(_text_findings(node))
    return found


def _suffix_findings(node: UANode) -> list[LintFinding]:
    """The suffix a BrowseName should or should not carry (§2.1.3)."""
    name = node.browse_name.name
    if node.node_class in {NodeClass.OBJECT_TYPE, NodeClass.VARIABLE_TYPE}:
        if name.endswith("Type"):
            return []
        return [
            finding(
                _rule("TYPE_SUFFIX_MISSING"),
                f"BrowseName '{name}' of an {node.node_class.value} should end with 'Type'",
                node.node_id,
            )
        ]
    if node.node_class is not NodeClass.DATA_TYPE:
        return []
    if node.role in {NodeRole.STRUCTURE, NodeRole.UNION} and not name.endswith("DataType"):
        return [
            finding(
                _rule("TYPE_SUFFIX_MISSING"),
                f"BrowseName '{name}' of a structured DataType should end with 'DataType'",
                node.node_id,
            )
        ]
    if node.role in ENUM_LIKE_ROLES and name.endswith(("DataType", "Type")):
        return [
            finding(
                _rule("TYPE_SUFFIX_FORBIDDEN"),
                f"BrowseName '{name}' is an enumeration: use no suffix, or 'Enum'",
                node.node_id,
            )
        ]
    simple = node.role is NodeRole.BASE_TYPE and not node.is_abstract
    if simple and name.endswith(("DataType", "Type", "Enum")):
        return [
            finding(
                _rule("TYPE_SUFFIX_FORBIDDEN"),
                f"BrowseName '{name}' is a simple DataType: a subtype of a built-in has no suffix",
                node.node_id,
            )
        ]
    return []


def _reference_name_findings(model: SemanticModel, node: UANode) -> list[LintFinding]:
    """The verb spelling of a ReferenceType and its InverseName (§2.1.3)."""
    if node.node_class is not NodeClass.REFERENCE_TYPE:
        return []
    name = node.browse_name.name
    found: list[LintFinding] = []
    if _first_word(name) not in REFERENCE_VERB_PREFIXES:
        found.append(
            finding(
                _rule("REFERENCE_TYPE_NAME_NOT_VERB"),
                f"BrowseName '{name}' of a ReferenceType should describe the relationship from "
                "source to target — a verb, or a noun prefixed with one",
                node.node_id,
            )
        )
    inverse = node.inverse_name
    if inverse is None:
        return found
    bad_inverse = not _is_pascal(inverse) or inverse == name or _first_word(inverse) in (
        REFERENCE_VERB_PREFIXES
    )
    if bad_inverse:
        found.append(
            finding(
                _rule("REFERENCE_INVERSE_NAME_SPELLING"),
                f"InverseName '{inverse}' should be the inverse of '{name}', e.g. ComponentOf",
                node.node_id,
            )
        )
    ancestors = _subtype_ancestors(model, node)
    if ancestors and not any(ancestor.inverse_name for ancestor in ancestors):
        found.append(
            finding(
                _rule("REFERENCE_INVERSE_ON_SYMMETRIC_BASE"),
                f"'{name}' declares an InverseName although its base hierarchy is symmetric",
                node.node_id,
            )
        )
    return found


def _text_findings(node: UANode) -> list[LintFinding]:
    """NamespaceIndexes written into text a server will re-index (§13.3)."""
    found: list[LintFinding] = []
    for attribute, label in TEXT_ATTRIBUTES:
        text = getattr(node, attribute, None)
        if text is not None and _NAMESPACE_INDEX_IN_TEXT.search(text):
            found.append(
                finding(
                    _rule("NAMESPACE_INDEX_IN_TEXT"),
                    f"{label} text names a NamespaceIndex; write the BrowseName without it",
                    node.node_id,
                )
            )
    return found


def _definition_findings(model: SemanticModel) -> list[LintFinding]:
    """Names and encoding limits of a structured, enum, union or option-set definition.

    Field names (§2.2), enum value names (§2.3) and the optional-field limits (§7.9) are judged
    here because a definition is the only place they exist.
    """
    found: list[LintFinding] = []
    for node in model.iter_nodes():
        definition = getattr(node, "definition", None)
        if definition is None or not _is_companion(node):
            continue
        members = [field.name for field in definition.fields]
        members += [alternative.name for alternative in definition.union_alternatives]
        for member in (*definition.fields, *definition.union_alternatives):
            found.extend(
                _member_name(member.name, member.metadata.symbolic_name, node.node_id, "field")
            )
        values = [value.name for value in (*definition.enum_values, *definition.option_set_values)]
        for value in (*definition.enum_values, *definition.option_set_values):
            found.extend(
                _member_name(value.name, value.metadata.symbolic_name, node.node_id, "enum")
            )
        if len(set(members)) < len(members):
            found.append(
                finding(
                    _rule("FIELD_NAME_DUPLICATE"),
                    "structure members are not unique: " + ", ".join(_duplicates(members)),
                    node.node_id,
                )
            )
        if len(set(values)) < len(values):
            found.append(
                finding(
                    _rule("ENUM_VALUE_NAME_DUPLICATE"),
                    "enumeration values are not unique: " + ", ".join(_duplicates(values)),
                    node.node_id,
                )
            )
        found.extend(_structure_limits(node))
    return found


def _duplicates(names: list[str]) -> list[str]:
    return sorted({name for name in names if names.count(name) > 1})


def _member_name(
    name: str, symbolic_name: str | None, node_id: ExpandedNodeId, kind: str
) -> list[LintFinding]:
    """One member name of a definition, judged by §2.2 (fields) or §2.3 (values)."""
    if _PLACEHOLDER_NAME.match(name):
        return []
    rule = "FIELD_NAME_NOT_PASCAL_CASE" if kind == "field" else "ENUM_VALUE_NOT_PASCAL_CASE"
    if not _SAFE_CHARS.fullmatch(name):
        if symbolic_name is None:
            return [
                finding(
                    _rule("BROWSE_NAME_SPECIAL_CHARS_WITHOUT_SYMBOLIC_NAME"),
                    f"{kind} name '{name}' has characters no generated identifier can carry",
                    node_id,
                )
            ]
        return []
    if _is_pascal(name):
        return []
    return [finding(_rule(rule), f"{kind} name '{name}' is not PascalCase", node_id)]


def _structure_limits(node: UANode) -> list[LintFinding]:
    """The optional-field limit and the subtype/optional combination (§7.9)."""
    definition = getattr(node, "definition", None)
    if definition is None or node.role is not NodeRole.STRUCTURE:
        return []
    fields = definition.fields
    optional = [field for field in fields if field.is_optional]
    found: list[LintFinding] = []
    if len(optional) > MAX_OPTIONAL_FIELDS:
        found.append(
            finding(
                _rule("STRUCTURE_OPTIONAL_FIELDS_LIMIT"),
                f"structure has {len(optional)} optional fields; at most {MAX_OPTIONAL_FIELDS} "
                "are encoded",
                node.node_id,
            )
        )
    if optional and any(field.metadata.allow_sub_types for field in fields):
        found.append(
            finding(
                _rule("STRUCTURE_SUBTYPES_WITH_OPTIONAL_FIELDS"),
                "structure combines optional fields with a field that allows subtypes; group them "
                "into separate substructures, e.g. OptionalFields and SubtypableFields",
                node.node_id,
            )
        )
    return found


def _argument_findings(model: SemanticModel) -> list[LintFinding]:
    """Argument names of a Method: PascalCase and unique (§2.6)."""
    parents = _parents(model, PATH_EDGE_NAMES)
    found: list[LintFinding] = []
    per_method: dict[ExpandedNodeId, list[str]] = {}
    for node in model.iter_nodes():
        if node.role is not NodeRole.ARGUMENT or not _is_companion(node):
            continue
        name = node.browse_name.name
        if _SAFE_CHARS.fullmatch(name) and not _is_pascal(name):
            found.append(
                finding(
                    _rule("ARGUMENT_NAME_NOT_PASCAL_CASE"),
                    f"argument name '{name}' is not PascalCase",
                    node.node_id,
                )
            )
        for method in parents.get(node.node_id, []):
            per_method.setdefault(method, []).append(name)
    for method, names in sorted(per_method.items(), key=lambda item: item[0].as_xml()):
        duplicates = _duplicates(names)
        if duplicates:
            found.append(
                finding(
                    _rule("ARGUMENT_NAME_DUPLICATE"),
                    "method arguments are not unique: " + ", ".join(duplicates),
                    method,
                )
            )
    return found


def _browse_path(
    model: SemanticModel, node: UANode, parents: dict[ExpandedNodeId, list[ExpandedNodeId]]
) -> tuple[ExpandedNodeId, list[str]] | None:
    """(type definition, BrowsePath parts) of a declaration, or None if it is not inside a type."""
    parts = [node.browse_name.name]
    seen = {node.node_id}
    current = node.node_id
    while True:
        parent_id = next(iter(parents.get(current, ())), None)
        if parent_id is None or parent_id in seen:
            return None
        parent = model.get(parent_id)
        if parent is None:
            return None
        if parent.node_class in TYPE_DEFINITION_CLASSES:
            parts.reverse()
            return parent.node_id, parts
        seen.add(parent_id)
        parts.append(parent.browse_name.name)
        current = parent_id


def _browse_path_findings(model: SemanticModel) -> list[LintFinding]:
    """BrowsePaths that collide once case is ignored (§2.1.4).

    Servers may treat BrowseNames case-insensitively, so 'Id' and 'ID' under one type are one
    path.  Paths are built from the type definition down through containment references.
    """
    parents = _parents(model, PATH_EDGE_NAMES)
    grouped: dict[tuple[ExpandedNodeId, tuple[str, ...]], dict[str, ExpandedNodeId]] = {}
    for node in model.iter_nodes():
        if not _is_companion(node):
            continue
        if node.role not in {NodeRole.INSTANCE, NodeRole.ARGUMENT, NodeRole.METHOD}:
            continue
        path = _browse_path(model, node, parents)
        if path is None:
            continue
        key = (path[0], tuple(part.lower() for part in path[1]))
        grouped.setdefault(key, {}).setdefault("/".join(path[1]), node.node_id)
    found: list[LintFinding] = []
    keys = sorted(grouped, key=lambda key: (key[0].as_xml(), key[1]))
    for key in keys:
        spellings = grouped[key]
        if len(spellings) < 2:
            continue
        names = sorted(spellings)
        found.append(
            finding(
                _rule("BROWSE_PATH_CASE_COLLISION"),
                "BrowsePaths differ only by case: " + ", ".join(names),
                spellings[names[0]],
            )
        )
    return found


def _namespace_name_findings(model: SemanticModel) -> list[LintFinding]:
    """Type names unique per namespace, and BrowseNames not shared across namespaces (§2.1.4)."""
    namespaces = {uri for uri in model.namespaces if uri != UA_NAMESPACE_URI}
    by_name: dict[str, set[str]] = {}
    types: dict[tuple[str, str], list[ExpandedNodeId]] = {}
    for node in model.iter_nodes():
        uri = node.node_id.namespace
        if uri not in namespaces:
            continue
        name = node.browse_name.name
        by_name.setdefault(name, set()).add(uri)
        if node.node_class in TYPE_NAME_CLASSES:
            types.setdefault((uri, name), []).append(node.node_id)
    found: list[LintFinding] = []
    for name, uris in sorted(by_name.items()):
        if len(uris) > 1:
            found.append(
                finding(
                    _rule("BROWSE_NAME_USED_IN_TWO_NAMESPACES"),
                    f"BrowseName '{name}' is used in {len(uris)} namespaces: "
                    + ", ".join(sorted(uris)),
                )
            )
    for (uri, name), ids in sorted(types.items()):
        if len(ids) > 1:
            found.append(
                finding(
                    _rule("TYPE_NAME_DUPLICATE_IN_NAMESPACE"),
                    f"BrowseName '{name}' names {len(ids)} types in {uri}",
                    ids[0],
                )
            )
    return found


def _status_code_findings(model: SemanticModel) -> list[LintFinding]:
    """StatusCodes are defined by the base specification only (§4)."""
    found: list[LintFinding] = []
    for node in model.iter_nodes():
        if node.node_class is not NodeClass.DATA_TYPE or not _is_companion(node):
            continue
        name = node.browse_name.name
        ancestors = _subtype_ancestors(model, node)
        derived = any(a.browse_name.name == _BASE_STATUS_CODE_NAME for a in ancestors)
        if _STATUS_CODE_SUFFIX.search(name) or derived:
            found.append(
                finding(
                    _rule("STATUS_CODE_DEFINED_OUTSIDE_BASE"),
                    f"DataType '{name}' defines StatusCode information outside the base "
                    "specification; say more with DiagnosticInfo",
                    node.node_id,
                )
            )
    return found


def _recursion_findings(model: SemanticModel) -> list[LintFinding]:
    """A recursive InstanceDeclaration must not be mandatory (§7.10).

    The containment closure of a declaration is walked with a visited set; reaching the type that
    declares it — directly through its TypeDefinition or through a nested instance — means the
    declaration is self-contained.
    """
    parents = _parents(model, CONTAINMENT_NAMES)
    found: list[LintFinding] = []
    for node in model.iter_nodes():
        if not _is_companion(node) or node.node_class not in {NodeClass.OBJECT, NodeClass.VARIABLE}:
            continue
        if node.modelling_rule not in _MANDATORY_RULES:
            continue
        enclosing = _enclosing_type(model, node, parents)
        if enclosing is None:
            continue
        if node.type_definition == enclosing or _closure_reaches(model, node, enclosing):
            found.append(
                finding(
                    _rule("MANDATORY_SELF_CONTAINED_DECLARATION"),
                    f"'{node.browse_name.name}' is mandatory inside a type that contains itself; "
                    "use Optional so a server can instantiate the type",
                    node.node_id,
                )
            )
    return found


def _enclosing_type(
    model: SemanticModel, node: UANode, parents: dict[ExpandedNodeId, list[ExpandedNodeId]]
) -> ExpandedNodeId | None:
    """The nearest type definition the declaration is contained in."""
    seen = {node.node_id}
    current = node.node_id
    while True:
        parent_id = next(iter(parents.get(current, ())), None)
        if parent_id is None or parent_id in seen:
            return None
        parent = model.get(parent_id)
        if parent is None:
            return None
        if parent.node_class in TYPE_DEFINITION_CLASSES:
            return parent.node_id
        seen.add(parent_id)
        current = parent_id


def _closure_reaches(model: SemanticModel, node: UANode, enclosing: ExpandedNodeId) -> bool:
    """Whether the containment closure of ``node`` comes back to its own type or to itself."""
    visited: set[ExpandedNodeId] = set()
    queue: deque[ExpandedNodeId] = deque(
        target
        for name in CONTAINMENT_NAMES
        for target in node.reference_of(name)
    )
    while queue:
        current = queue.popleft()
        if current in visited:
            continue
        visited.add(current)
        if current == enclosing or current == node.node_id:
            return True
        child = model.get(current)
        if child is None:
            continue
        if child.type_definition == enclosing:
            return True
        for name in CONTAINMENT_NAMES:
            queue.extend(child.reference_of(name))
    return False


def _audit_event_findings(model: SemanticModel) -> list[LintFinding]:
    """A subtype of an audit event should not add fields (§15)."""
    found: list[LintFinding] = []
    for node in model.iter_nodes():
        if node.node_class is not NodeClass.OBJECT_TYPE or not _is_companion(node):
            continue
        audits = _subtype_ancestors(model, node)
        names = {ancestor.browse_name.name for ancestor in audits}
        if not any(name.startswith(_AUDIT_EVENT_PREFIX) for name in names):
            continue
        fields = [
            target
            for name in CONTAINMENT_NAMES
            for target in node.reference_of(name)
            if (child := model.get(target)) is not None and child.node_class is not NodeClass.METHOD
        ]
        if fields:
            found.append(
                finding(
                    _rule("AUDIT_EVENT_SUBTYPE_ADDS_FIELDS"),
                    f"'{node.browse_name.name}' adds {len(fields)} field(s) to an audit event; "
                    "clients processing audit events cannot handle extensions",
                    node.node_id,
                )
            )
    return found


def _document_findings(model: SemanticModel) -> list[LintFinding]:
    """The file name a NodeSet should carry (§2.4)."""
    found: list[LintFinding] = []
    for document in model.documents:
        filename = Path(document.source_path).name
        if not filename.endswith("NodeSet2.xml"):
            found.append(
                finding(
                    _rule("FILENAME_CONVENTION"),
                    f"'{filename}' should end with 'NodeSet2.xml'",
                )
            )
            continue
        companions = [
            uri
            for uri in (namespace.uri for namespace in document.namespaces)
            if uri.startswith(_OPC_FOUNDATION_PREFIX) and uri != UA_NAMESPACE_URI
        ]
        for uri in companions:
            expected = f"Opc.Ua.{alias_from_uri(uri) or ''}.NodeSet2.xml"
            # The official companion files vary in case (``Opc.Ua.Di.NodeSet2.xml`` vs the
            # alias ``DI``), so the convention check compares spellings case-insensitively.
            if filename.lower() != expected.lower():
                found.append(
                    finding(
                        _rule("FILENAME_CONVENTION"),
                        f"'{filename}' should be named '{expected}' for {uri}",
                    )
                )
    return found


def _namespace_uri_findings(model: SemanticModel) -> list[LintFinding]:
    """The NamespaceUri template (§2.5)."""
    found: list[LintFinding] = []
    for namespace in sorted(model.namespaces.values(), key=lambda item: item.uri):
        uri = namespace.uri
        if uri == UA_NAMESPACE_URI:
            continue
        if not uri.startswith(("http://", "https://")) or _PERCENT_ESCAPE.search(uri):
            found.append(
                finding(
                    _rule("NAMESPACE_URI_TEMPLATE"),
                    f"NamespaceUri '{uri}' is not a plain http(s) URI without %-escaped characters",
                )
            )
        elif not uri.endswith("/"):
            found.append(
                finding(
                    _rule("NAMESPACE_URI_MISSING_TRAILING_SLASH"),
                    f"NamespaceUri '{uri}' does not end with '/'",
                )
            )
    return found


def run(model: SemanticModel) -> list[LintFinding]:
    """All best-practice findings of one model."""
    findings: list[LintFinding] = []
    checks = (
        _browse_name_findings,
        _definition_findings,
        _argument_findings,
        _browse_path_findings,
        _namespace_name_findings,
        _status_code_findings,
        _recursion_findings,
        _audit_event_findings,
        _document_findings,
        _namespace_uri_findings,
    )
    for check in checks:
        findings.extend(check(model))
    return findings
