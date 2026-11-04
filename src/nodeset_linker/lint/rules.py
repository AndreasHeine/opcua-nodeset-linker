"""Lint rules for a single information model — how a model is *written*, not how it changed.

The checks come from the OPC UA whitepaper *Best Practices for Companion Models*
(OPC 11030): naming (§2), StatusCodes (§4), structure and containment limits (§7.9, §7.10),
BrowseNames in text (§13.3) and audit events (§15).  Like the diff rules, every rule carries a
stable slug and the clause is only a provenance hint.

A lint finding is never ``BREAKING``: naming and limits do not break a consumer that already
reads the published model.  A structurally invalid model is not a lint finding either — that is
semantic validation, which blocks export.
"""

from __future__ import annotations

from dataclasses import dataclass

from nodeset_linker.diff.rules import Severity


@dataclass(frozen=True, slots=True)
class LintRule:
    """One lint rule: code, severity, stable slug, rationale and paper clause."""

    code: str
    severity: Severity
    rationale: str
    rule_id: str
    clause: str | None = None


LINT_RULES: dict[str, LintRule] = {
    "BROWSE_NAME_NOT_PASCAL_CASE": LintRule(
        "BROWSE_NAME_NOT_PASCAL_CASE",
        Severity.ADVISORY,
        "A BrowseName that is not PascalCase reads badly in clients and in generated code "
        "(§2.1.2); acronyms are written as words, e.g. PortMacAddress, not MACAddress.",
        "BP-Naming-PascalCase",
        "2.1.2",
    ),
    "BROWSE_NAME_SPECIAL_CHARS_WITHOUT_SYMBOLIC_NAME": LintRule(
        "BROWSE_NAME_SPECIAL_CHARS_WITHOUT_SYMBOLIC_NAME",
        Severity.ADVISORY,
        "A BrowseName with characters beyond letters, digits or '_' needs an optional "
        "SymbolicName (§2.1.2), otherwise code generation has no identifier to emit.",
        "BP-Naming-SymbolicName",
        "2.1.2",
    ),
    "TYPE_SUFFIX_MISSING": LintRule(
        "TYPE_SUFFIX_MISSING",
        Severity.ADVISORY,
        "ObjectTypes, VariableTypes and structured DataTypes should be suffixed with Type or "
        "DataType (§2.1.3); the suffix tells a reader which node is a type definition.",
        "BP-Naming-Suffix",
        "2.1.3",
    ),
    "TYPE_SUFFIX_FORBIDDEN": LintRule(
        "TYPE_SUFFIX_FORBIDDEN",
        Severity.ADVISORY,
        "Enumerations should carry no suffix or 'Enum', and simple DataTypes no suffix at all "
        "(§2.1.3); a DataType suffix on an enum reads as a structure.",
        "BP-Naming-Suffix",
        "2.1.3",
    ),
    "REFERENCE_TYPE_NAME_NOT_VERB": LintRule(
        "REFERENCE_TYPE_NAME_NOT_VERB",
        Severity.ADVISORY,
        "A ReferenceType describes the relationship from source to target (§2.1.3): use a verb, "
        "or a noun prefixed with a verb such as HasComponent.",
        "BP-Naming-ReferenceVerb",
        "2.1.3",
    ),
    "REFERENCE_INVERSE_NAME_SPELLING": LintRule(
        "REFERENCE_INVERSE_NAME_SPELLING",
        Severity.ADVISORY,
        "The InverseName should be the inverse of the BrowseName (§2.1.3), e.g. ComponentOf for "
        "HasComponent — not the forward name again and not a verb-prefixed inverse.",
        "BP-Naming-InverseName",
        "2.1.3",
    ),
    "REFERENCE_INVERSE_ON_SYMMETRIC_BASE": LintRule(
        "REFERENCE_INVERSE_ON_SYMMETRIC_BASE",
        Severity.ADVISORY,
        "An InverseName belongs to asymmetric References only (§2.1.3): a subtype of a fully "
        "symmetric hierarchy that declares one changes the semantics of the base type (§3.2.14).",
        "BP-Reference-Symmetric",
        "2.1.3",
    ),
    "FIELD_NAME_NOT_PASCAL_CASE": LintRule(
        "FIELD_NAME_NOT_PASCAL_CASE",
        Severity.ADVISORY,
        "Structure fields and union alternatives are PascalCase in a UANodeSet (§2.2), even when "
        "the specification text uses lower camel case.",
        "BP-Naming-PascalCase",
        "2.2",
    ),
    "ENUM_VALUE_NOT_PASCAL_CASE": LintRule(
        "ENUM_VALUE_NOT_PASCAL_CASE",
        Severity.ADVISORY,
        "Enumeration and OptionSet value names are PascalCase (§2.3); the names appear in "
        "MultiState documentation and in generated code.",
        "BP-Naming-PascalCase",
        "2.3",
    ),
    "ARGUMENT_NAME_NOT_PASCAL_CASE": LintRule(
        "ARGUMENT_NAME_NOT_PASCAL_CASE",
        Severity.ADVISORY,
        "Method arguments are PascalCase (§2.6), like every other name a client reads.",
        "BP-Naming-PascalCase",
        "2.6",
    ),
    "ARGUMENT_NAME_DUPLICATE": LintRule(
        "ARGUMENT_NAME_DUPLICATE",
        Severity.ADVISORY,
        "Every Argument of a Method must have a unique name (§2.6); two arguments of the same "
        "name cannot be told apart by a client.",
        "BP-Naming-ArgumentUnique",
        "2.6",
    ),
    "FIELD_NAME_DUPLICATE": LintRule(
        "FIELD_NAME_DUPLICATE",
        Severity.ADVISORY,
        "Each field of a Structure and each alternative of a Union must have a unique name "
        "(§2.2); a duplicated name cannot be encoded or read back.",
        "BP-Naming-UniqueMember",
        "2.2",
    ),
    "ENUM_VALUE_NAME_DUPLICATE": LintRule(
        "ENUM_VALUE_NAME_DUPLICATE",
        Severity.ADVISORY,
        "Each value of an Enum must have a unique name (§2.3); a client reading a value back as "
        "text cannot tell the two apart.",
        "BP-Naming-UniqueMember",
        "2.3",
    ),
    "TYPE_NAME_DUPLICATE_IN_NAMESPACE": LintRule(
        "TYPE_NAME_DUPLICATE_IN_NAMESPACE",
        Severity.ADVISORY,
        "BrowseNames of type definitions shall be unique inside a namespace (§2.1.4); a client "
        "that resolves a name cannot know which type it meant.",
        "BP-Naming-UniqueType",
        "2.1.4",
    ),
    "BROWSE_PATH_CASE_COLLISION": LintRule(
        "BROWSE_PATH_CASE_COLLISION",
        Severity.ADVISORY,
        "Servers may treat BrowseNames case-insensitively (§2.1.4), so BrowsePaths must stay "
        "unique without case: 'Id' and 'ID' on one type are one path, not two.",
        "BP-Naming-BrowsePath",
        "2.1.4",
    ),
    "BROWSE_NAME_USED_IN_TWO_NAMESPACES": LintRule(
        "BROWSE_NAME_USED_IN_TWO_NAMESPACES",
        Severity.ADVISORY,
        "Clients display only the string part of a BrowseName (§2.1.4), so a name used in two "
        "namespaces is ambiguous in every report, selector and impact list.",
        "BP-Naming-NameAcrossNamespaces",
        "2.1.4",
    ),
    "NAMESPACE_URI_TEMPLATE": LintRule(
        "NAMESPACE_URI_TEMPLATE",
        Severity.ADVISORY,
        "A NamespaceUri follows http(s)://host/path/ and %-escaped characters are strongly "
        "discouraged (§2.5).",
        "BP-NamespaceUri-Convention",
        "2.5",
    ),
    "NAMESPACE_URI_MISSING_TRAILING_SLASH": LintRule(
        "NAMESPACE_URI_MISSING_TRAILING_SLASH",
        Severity.INFO,
        "The NamespaceUri template ends with '/' (§2.5, §11.3); a URI without it reads as a file "
        "path and breaks the versioned-namespace spelling.",
        "BP-NamespaceUri-Convention",
        "2.5",
    ),
    "FILENAME_CONVENTION": LintRule(
        "FILENAME_CONVENTION",
        Severity.ADVISORY,
        "A companion NodeSet is named Opc.Ua.<ShortName>.NodeSet2.xml and a vendor NodeSet ends "
        "with NodeSet2.xml (§2.4); tools and the repository look for that spelling.",
        "BP-Filename-Convention",
        "2.4",
    ),
    "STATUS_CODE_DEFINED_OUTSIDE_BASE": LintRule(
        "STATUS_CODE_DEFINED_OUTSIDE_BASE",
        Severity.ADVISORY,
        "Additional StatusCodes are not allowed in a companion or vendor namespace (§4): only the "
        "base specification defines them; say more with DiagnosticInfo instead.",
        "BP-StatusCode-NotAllowed",
        "4",
    ),
    "STRUCTURE_OPTIONAL_FIELDS_LIMIT": LintRule(
        "STRUCTURE_OPTIONAL_FIELDS_LIMIT",
        Severity.ADVISORY,
        "A Structure carries at most 32 optional fields (§7.9); beyond that the encoding of "
        "optional fields is not guaranteed to work.",
        "BP-Structure-OptionalFieldsLimit",
        "7.9",
    ),
    "STRUCTURE_SUBTYPES_WITH_OPTIONAL_FIELDS": LintRule(
        "STRUCTURE_SUBTYPES_WITH_OPTIONAL_FIELDS",
        Severity.ADVISORY,
        "Allowing subtypes and optional fields cannot be combined in one Structure (§7.9): group "
        "them into separate substructures, e.g. OptionalFields and SubtypableFields.",
        "BP-Structure-AllowSubTypesWithOptional",
        "7.9",
    ),
    "MANDATORY_SELF_CONTAINED_DECLARATION": LintRule(
        "MANDATORY_SELF_CONTAINED_DECLARATION",
        Severity.ADVISORY,
        "A recursive InstanceDeclaration must not be Mandatory or MandatoryPlaceholder (§7.10): "
        "a server could never instantiate it; use Optional and let implementations bound the "
        "depth.",
        "BP-Recursive-Mandatory",
        "7.10",
    ),
    "NAMESPACE_INDEX_IN_TEXT": LintRule(
        "NAMESPACE_INDEX_IN_TEXT",
        Severity.ADVISORY,
        "Text in a UANodeSet shows up in a server where NamespaceIndex values differ (§13.3): "
        "write the BrowseName without its index, or name the namespace URI explicitly.",
        "BP-Text-NamespaceIndex",
        "13.3",
    ),
    "AUDIT_EVENT_SUBTYPE_ADDS_FIELDS": LintRule(
        "AUDIT_EVENT_SUBTYPE_ADDS_FIELDS",
        Severity.ADVISORY,
        "A subtype of an audit event should not add fields (§15): clients processing audit "
        "events cannot handle extensions, and the base events already carry the information.",
        "BP-AuditEvent-ExtraFields",
        "15",
    ),
}
