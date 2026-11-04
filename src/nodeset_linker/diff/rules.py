"""Best-practices rules: the single source of every diff verdict.

The verdicts come from the OPC UA whitepaper *Best Practices for Companion Models*
(OPC 11030).  Each rule has a stable slug (``rule_id``) that never depends on the clause
numbering of a given paper revision — the paper is republished more often than the base
specifications, so the clause is only a provenance hint, never the whole message a user
reads.  The ``rationale`` says *why* a consumer breaks or what to do instead.

Three severities:

``BREAKING``
    a consumer of the published model can break; only these gate ``--fail-on-breaking``.
``ADVISORY``
    allowed by the paper but worth a second look; never gates a build.
``INFO``
    reporting only — documentation, metadata, totals.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from enum import Enum
from typing import Any

from nodeset_linker.models.node_class import ModellingRule

#: The paper the rationales are grounded in; shown in reports so a verdict can be checked.
PAPER = "OPC 11030 v1.03.03 — Best Practices for Companion Models"


class Severity(Enum):
    """How strongly a change should be acted on."""

    BREAKING = "breaking"
    ADVISORY = "advisory"
    INFO = "info"

    @property
    def blocks_release(self) -> bool:
        """Only a breaking change may fail a build or block a release."""
        return self is Severity.BREAKING


@dataclass(frozen=True, slots=True)
class RuleVerdict:
    """The verdict of one rule: severity, reason and stable slug."""

    severity: Severity
    rationale: str
    rule_id: str
    clause: str | None = None

    @property
    def breaking(self) -> bool:
        return self.severity.blocks_release

    def as_json(self) -> dict[str, Any]:
        return {
            "rule_id": self.rule_id,
            "severity": self.severity.value,
            "rationale": self.rationale,
            "clause": self.clause,
            "paper": PAPER,
        }


@dataclass(frozen=True, slots=True)
class RuleContext:
    """What a rule may look at besides the kind of change.

    ``member`` names the kind of definition member (``field``, ``enum_value``,
    ``option_set_value``, ``union_alternative``, ``argument``, ``member_text``,
    ``member_metadata``); ``reference`` names the reference category; ``tightened`` says a
    contract got narrower; ``metadata_attribute`` names the semantic attribute of a member
    that changed; ``rename_confidence`` says how sure a pairing guess is.
    """

    member: str | None = None
    reference: str | None = None
    mandatory_target: bool = False
    tightened: bool = False
    relaxed: bool = False
    now_abstract: bool | None = None
    modelling_rule: ModellingRule | None = None
    symmetric_flip: bool = False
    metadata_attribute: str | None = None
    rename_confidence: str = "exact"


#: Fallback for anything the table below does not know: reporting, never gating.
_NEUTRAL = RuleVerdict(
    Severity.INFO,
    "Reported for information; nothing here changes the contract of the model.",
    "BP-Change-Neutral",
)

#: One entry per change kind.  The kind is what the diff engine produced; the severity and
#: rationale below are what the paper says about it.
_RULES: dict[str, RuleVerdict] = {
    "node_class": RuleVerdict(
        Severity.BREAKING,
        "The NodeClass of a published node shall not change (§3.2.17): a consumer that "
        "browses it as one class now reads something else.",
        "BP-NodeClass-Immutable",
        "3.2.17",
    ),
    "browse_name": RuleVerdict(
        Severity.BREAKING,
        "The BrowseName of a published node shall not change (§3.2.17): browse paths and "
        "symbolic names resolve to the old name. Publish a new namespace instead (§11.3).",
        "BP-BrowseName-Immutable",
        "3.2.17",
    ),
    "display_name": RuleVerdict(
        Severity.INFO,
        "DisplayName may change as long as the meaning stays the same (§3.2.17); clients "
        "that render it see new text, nothing else.",
        "BP-DisplayName-Meaning",
        "3.2.17",
    ),
    "description": RuleVerdict(
        Severity.INFO,
        "Description may change as long as the meaning stays the same (§3.2.17); it is "
        "documentation, not contract.",
        "BP-Description-Meaning",
        "3.2.17",
    ),
    "documentation": RuleVerdict(
        Severity.INFO,
        "Documentation is text for humans and carries no contract (§3.2.17).",
        "BP-Documentation-Meaning",
        "3.2.17",
    ),
    "symbolic_name": RuleVerdict(
        Severity.ADVISORY,
        "SymbolicName changed (§2.1.2): BrowseName stays the identity, but generated code "
        "and codenames are spelled after SymbolicName, so consumers keep the old symbol. "
        "Regenerate the code and keep SymbolicName and BrowseName aligned.",
        "BP-SymbolicName-Identity",
        "2.1.2",
    ),
    "inverse_name": RuleVerdict(
        Severity.ADVISORY,
        "InverseName may change as long as the meaning stays the same and it stays unique "
        "in the namespace (§3.2.17); browse paths that read the inverse name show new text.",
        "BP-InverseName-Meaning",
        "3.2.17",
    ),
    "categories": RuleVerdict(
        Severity.INFO,
        "Categories are search metadata and carry no contract (§3.2.17).",
        "BP-Categories-Metadata",
        "3.2.17",
    ),
    "attributes": RuleVerdict(
        Severity.INFO,
        "Server-side attributes changed; check §3.2.17 for attributes that shall not change.",
        "BP-Attributes-ServerSpecific",
        "3.2.17",
    ),
    "role_permissions": RuleVerdict(
        Severity.INFO,
        "RolePermissions changed (§3.2.17): which role may access the node changes, the node "
        "itself stays — review the access model.",
        "BP-RolePermissions-Changed",
        "3.2.17",
    ),
    "extensions": RuleVerdict(
        Severity.ADVISORY,
        "Extension content changed (§3.2.17): the payload travels with the node, so consumers "
        "that read it have to be told.",
        "BP-Extensions-Changed",
        "3.2.17",
    ),
    "value_dependency": RuleVerdict(
        Severity.ADVISORY,
        "A value dependency now resolves to another node (§3.2.17, §3.2.12): the value of the "
        "Variable means something else.",
        "BP-ValueDependency-Changed",
        "3.2.17",
    ),
    "modelling_rule": RuleVerdict(
        Severity.BREAKING,
        "A declaration that was Optional became Mandatory (§3.2.1): consumers must now "
        "expect the component to exist.",
        "BP-ModellingRule-Tightening",
        "3.2.1",
    ),
    "modelling_rule_relaxed": RuleVerdict(
        Severity.INFO,
        "A declaration that was Mandatory became Optional (§3.2.2): consumers that relied "
        "on its presence may now find it missing.",
        "BP-ModellingRule-Relaxing",
        "3.2.2",
    ),
    "is_abstract_abstract": RuleVerdict(
        Severity.BREAKING,
        "A type became abstract (§3.2.17): instances of it are no longer allowed, so any "
        "existing instance is now invalid.",
        "BP-IsAbstract-Narrowing",
        "3.2.17",
    ),
    "is_abstract_concrete": RuleVerdict(
        Severity.INFO,
        "A type became non-abstract (§3.2.17): allowing instances only widens the model.",
        "BP-IsAbstract-Relaxing",
        "3.2.17",
    ),
    "data_type": RuleVerdict(
        Severity.BREAKING,
        "The DataType of a published Variable or InstanceDeclaration shall not change "
        "(§3.2.12): readers decode the value with the old type.",
        "BP-DataType-Immutable",
        "3.2.12",
    ),
    "value_rank": RuleVerdict(
        Severity.BREAKING,
        "ValueRank shall not change (§3.2.12): a scalar consumer now sees an array, or the "
        "other way round.",
        "BP-ValueRank-Immutable",
        "3.2.12",
    ),
    "array_dimensions": RuleVerdict(
        Severity.BREAKING,
        "ArrayDimensions shall not change (§3.2.12): consumers sized for the old array "
        "cannot read the new one.",
        "BP-ArrayDimensions-Immutable",
        "3.2.12",
    ),
    "value": RuleVerdict(
        Severity.INFO,
        "A Value of an instance changed (§3.2.17): instance data is not part of the model "
        "contract.",
        "BP-Value-InstanceData",
        "3.2.17",
    ),
    "definition_kind": RuleVerdict(
        Severity.BREAKING,
        "The DataTypeDefinition of a structured type shall not change (§3.2.17): Structure, "
        "Union, Enum and OptionSet encode differently.",
        "BP-DefinitionKind-Immutable",
        "3.2.17",
    ),
    "field_order": RuleVerdict(
        Severity.ADVISORY,
        "The fields of a Structure were re-ordered (§2.2): field names are the contract, but "
        "an XML-based encoder reads fields by position, so check consumers that parse the "
        "XML schema.",
        "BP-FieldOrder-Encoding",
        "2.2",
    ),
    "alternative_order": RuleVerdict(
        Severity.ADVISORY,
        "The alternatives of a Union were re-ordered (§3.2.6): each alternative keeps its "
        "DataType, but a positional encoder reads the alternative at the old position.",
        "BP-AlternativeOrder-Encoding",
        "3.2.6",
    ),
    "node_removed": RuleVerdict(
        Severity.BREAKING,
        "A node that was published is gone (§3.2.16): consumers that browsed or read it "
        "find nothing. Removing an optional InstanceDeclaration is equally not allowed.",
        "BP-Declaration-Removed",
        "3.2.16",
    ),
    "node_removed_optional": RuleVerdict(
        Severity.BREAKING,
        "An optional InstanceDeclaration was removed (§3.2.16): a consumer that browsed the "
        "optional declaration now finds nothing, even though it was optional. Publish a new "
        "namespace instead (§11.3).",
        "BP-Declaration-Removed-Optional",
        "3.2.16",
    ),
    "node_removed_standard": RuleVerdict(
        Severity.INFO,
        "A node of the OPC UA namespace was dropped from this document; it is not a "
        "declaration of the companion model and stays available through the base model.",
        "BP-Declaration-Removed-Standard",
        "3.2.16",
    ),
    "node_added": RuleVerdict(
        Severity.INFO,
        "A declaration was added (§3.2.2): existing consumers keep working.",
        "BP-Declaration-Added",
        "3.2.2",
    ),
    "identifier_move": RuleVerdict(
        Severity.BREAKING,
        "The NodeId of a published node changed (§3.2.17): identifiers shall not change, and "
        "a consumer that resolves the node by identifier now reaches nothing or the wrong "
        "node. Add new identifiers instead of renumbering, and publish a new namespace for a "
        "breaking change (§11.3).",
        "BP-NodeId-Immutable",
        "3.2.17",
    ),
    "hierarchy_move": RuleVerdict(
        Severity.BREAKING,
        "A type was moved inside its hierarchy (§3.2.9, §3.2.10, §3.2.13): inherited "
        "declarations and browse paths change for every consumer.",
        "BP-Hierarchy-Move",
        "3.2.9",
    ),
    "interface_added": RuleVerdict(
        Severity.ADVISORY,
        "An Interface was added to a published type (§3.2.3): the type now carries the "
        "interface's mandatory declarations, so consumers that browse the type see more and "
        "generated code gains the interface. Removing an Interface from a published type is "
        "the breaking direction.",
        "BP-Interface-Added",
        "3.2.3",
    ),
    "interface_removed": RuleVerdict(
        Severity.BREAKING,
        "An Interface was removed from a published type (§3.2.3): consumers that expect the "
        "interface and its mandatory declarations no longer find them on the type.",
        "BP-Interface-Removed",
        "3.2.3",
    ),
    "node_added_mandatory": RuleVerdict(
        Severity.ADVISORY,
        "A Mandatory declaration was added (§3.2.1): every instance of the type must now "
        "carry it, so consumers that browsed the type before see a new required component. "
        "Say so in the release notes.",
        "BP-Declaration-Added-Mandatory",
        "3.2.1",
    ),
    "subtype_inserted": RuleVerdict(
        Severity.ADVISORY,
        "A new type was inserted inside an existing hierarchy (§3.2.9, §3.2.10): consumers "
        "that browse the parent now see an extra level.",
        "BP-Subtype-Inserted",
        "3.2.9",
    ),
    "method_signature": RuleVerdict(
        Severity.BREAKING,
        "The signature of a method changed (§3.2.11): arguments or their types are part of "
        "the call contract, so callers break.",
        "BP-MethodSignature-Immutable",
        "3.2.11",
    ),
    "node_renamed": RuleVerdict(
        Severity.BREAKING,
        "A published node was renamed (§3.2.17, §2.1.2): browse paths, symbolic names and "
        "generated code still carry the old name. Use a new namespace (§11.3).",
        "BP-Node-Renamed",
        "3.2.17",
    ),
    "deprecation_added": RuleVerdict(
        Severity.INFO,
        "A node was marked deprecated (§14.2, §14.3.2): consumers keep working and learn which "
        "release deprecated it.",
        "BP-Deprecation-Added",
        "14.2",
    ),
    "deprecation_removed": RuleVerdict(
        Severity.ADVISORY,
        "The IsDeprecated reference was removed (§14.2): the node reads as current again while "
        "consumers may still treat it as deprecated.",
        "BP-Deprecation-Removed",
        "14.2",
    ),
    "reference_hard_removed": RuleVerdict(
        Severity.BREAKING,
        "A contract reference was removed (§3.2.16, §3.2.14): subtype, type-definition, "
        "interface, encoding or event links are what consumers rely on.",
        "BP-Reference-Contract-Removed",
        "3.2.16",
    ),
    "reference_soft_removed": RuleVerdict(
        Severity.INFO,
        "A navigation-only reference was removed (§3.2.16): browsing changes, reading does "
        "not.",
        "BP-Reference-Navigation-Removed",
        "3.2.16",
    ),
    "reference_mandatory_child_removed": RuleVerdict(
        Severity.BREAKING,
        "A Mandatory component was detached (§3.2.16): consumers must expect that component.",
        "BP-Reference-MandatoryChild-Removed",
        "3.2.16",
    ),
    "reference_optional_child_removed": RuleVerdict(
        Severity.BREAKING,
        "An Optional component was detached (§3.2.16): detaching an InstanceDeclaration is a "
        "breaking change even where it was optional, because consumers that browsed it now "
        "find nothing.",
        "BP-Reference-OptionalChild-Removed",
        "3.2.16",
    ),
    "reference_other_removed": RuleVerdict(
        Severity.BREAKING,
        "A reference was removed (§3.2.16): whatever followed it no longer reaches the "
        "target.",
        "BP-Reference-Removed",
        "3.2.16",
    ),
    "reference_added": RuleVerdict(
        Severity.INFO,
        "A reference was added (§3.2.2): navigation widens, nothing that existed disappeared.",
        "BP-Reference-Added",
        "3.2.2",
    ),
    "reference_mandatory_child_added": RuleVerdict(
        Severity.BREAKING,
        "A Mandatory component was attached (§3.2.1, §11.2.1): every instance of the parent must "
        "now carry it, so consumers built on the old instance shape break.",
        "BP-Reference-MandatoryChild-Added",
        "3.2.1",
    ),
    "reference_optional_child_added": RuleVerdict(
        Severity.INFO,
        "An Optional component was attached (§3.2.2): consumers may browse it, none has to.",
        "BP-Reference-OptionalChild-Added",
        "3.2.2",
    ),
    "reference_hard_added": RuleVerdict(
        Severity.INFO,
        "A contract reference was added (§3.2.2): consumers see more than before, and nothing "
        "that existed disappeared. Check the hierarchy it inserts (§3.2.9).",
        "BP-Reference-Contract-Added",
        "3.2.2",
    ),
    "reference_soft_added": RuleVerdict(
        Severity.INFO,
        "A navigation-only reference was added (§3.2.2): browsing changes, reading does not.",
        "BP-Reference-Navigation-Added",
        "3.2.2",
    ),
    "member_added": RuleVerdict(
        Severity.INFO,
        "A definition member was added; nothing existing changed.",
        "BP-Member-Added",
        None,
    ),
    "member_removed": RuleVerdict(
        Severity.BREAKING,
        "A definition member was removed; consumers that read it find nothing.",
        "BP-Member-Removed",
        None,
    ),
    "member_changed": RuleVerdict(
        Severity.BREAKING,
        "A definition member changed; its encoding or meaning changed for consumers.",
        "BP-Member-Changed",
        None,
    ),
    "member_text": RuleVerdict(
        Severity.INFO,
        "Only the text of a definition member changed; the member itself is unchanged.",
        "BP-Member-Text",
        "3.2.17",
    ),
    "member_metadata": RuleVerdict(
        Severity.BREAKING,
        "A semantic attribute of a definition member changed; it is part of the contract.",
        "BP-Member-Metadata",
        "3.2.5",
    ),
    "field_renamed": RuleVerdict(
        Severity.BREAKING,
        "A field of a Structure was renamed (§3.2.5, §2.2): the encoded field name changed, "
        "so consumers that address it by name or symbol break. Use a new namespace (§11.3).",
        "BP-Field-Renamed",
        "3.2.5",
    ),
    "field_rename_guess": RuleVerdict(
        Severity.ADVISORY,
        "A field disappeared and another appeared in its place, which reads like a rename "
        "(§2.2); the pairing is a guess, so check it by hand before treating it as one.",
        "BP-Field-Rename-Guess",
        "2.2",
    ),
    "duplicate_member": RuleVerdict(
        Severity.ADVISORY,
        "A definition carries two members with the same name (§2.2, §2.3): consumers and "
        "generated code cannot tell them apart, and a name-keyed comparison sees only one.",
        "BP-Duplicate-Member",
        "2.2",
    ),
    "allow_sub_types_tightened": RuleVerdict(
        Severity.BREAKING,
        "AllowSubTypes turned off (§3.2.5): values that carried a subtype of the field's "
        "DataType are now rejected, so consumers that passed one break.",
        "BP-Field-AllowSubTypes-Changed",
        "3.2.5",
    ),
    "allow_sub_types_relaxed": RuleVerdict(
        Severity.INFO,
        "AllowSubTypes turned on (§3.2.5): the field now also accepts subtypes, which widens "
        "the model without breaking a consumer.",
        "BP-Field-AllowSubTypes-Changed",
        "3.2.5",
    ),
    "max_string_length_reduced": RuleVerdict(
        Severity.BREAKING,
        "MaxStringLength got smaller or appeared (§3.2.5): a value that was valid before is "
        "now out of range, so consumers that sent it break.",
        "BP-Field-MaxStringLength-Reduced",
        "3.2.5",
    ),
    "max_string_length_raised": RuleVerdict(
        Severity.INFO,
        "MaxStringLength got larger or disappeared (§3.2.5): the field accepts more, which "
        "widens the model without breaking a consumer.",
        "BP-Field-MaxStringLength-Raised",
        "3.2.5",
    ),
    "symmetric_changed": RuleVerdict(
        Severity.BREAKING,
        "Symmetric of a ReferenceType shall not change (§3.2.14): the meaning of the "
        "reference flips between symmetric and asymmetric.",
        "BP-ReferenceType-Symmetric-Immutable",
        "3.2.14",
    ),
    "namespace_uri_changed": RuleVerdict(
        Severity.BREAKING,
        "The NamespaceUri of a published namespace changed (§11.3): consumers resolve the "
        "model under the old URI and find nothing. A breaking change needs a new namespace.",
        "BP-NamespaceUri-Changed",
        "11.3",
    ),
    "namespace_added": RuleVerdict(
        Severity.INFO,
        "A namespace appeared (§3.3, §11.3): a new companion model or a new version of one.",
        "BP-Namespace-Added",
        "11.3",
    ),
    "namespace_removed": RuleVerdict(
        Severity.ADVISORY,
        "A namespace disappeared (§3.3): consumers that resolve it find nothing.",
        "BP-Namespace-Removed",
        "3.3",
    ),
    "namespace_alias_changed": RuleVerdict(
        Severity.INFO,
        "The namespace alias changed; it is a document-local spelling, consumers resolve the "
        "URI (§2.5).",
        "BP-Namespace-Alias",
        "2.5",
    ),
    "namespace_version_changed": RuleVerdict(
        Severity.INFO,
        "The namespace version changed (§11.3): the release identity moved, not the model.",
        "BP-Namespace-Version",
        "11.3",
    ),
    "namespace_publication_date_changed": RuleVerdict(
        Severity.INFO,
        "The publication date changed (§11.3): release metadata only.",
        "BP-Namespace-PublicationDate",
        "11.3",
    ),
    "namespace_model_version_changed": RuleVerdict(
        Severity.INFO,
        "The ModelVersion of the required base model changed (§11.3): the companion now "
        "expects a different OPC UA release.",
        "BP-Namespace-ModelVersion",
        "11.3",
    ),
    "namespace_xml_schema_uri_changed": RuleVerdict(
        Severity.INFO,
        "The XmlSchemaUri changed (§11.3): XML-based consumers resolve a different schema.",
        "BP-Namespace-XmlSchema",
        "11.3",
    ),
    "namespace_required_model_changed": RuleVerdict(
        Severity.INFO,
        "A RequiredModel entry changed (§3.2 / OPC 10000-6 ModelTableEntry): the companion "
        "declares a different dependency.",
        "BP-Namespace-RequiredModel",
        "11.3",
    ),
    "namespace_version_drift": RuleVerdict(
        Severity.ADVISORY,
        "Version and ModelVersion disagree (§11.3): the release says one base revision, the "
        "metadata another.",
        "BP-Namespace-VersionDrift",
        "11.3",
    ),
    "naming_convention": RuleVerdict(
        Severity.ADVISORY,
        "A name does not follow the naming rules of the paper (§2.1.2, §2.2, §2.3, §2.6): "
        "PascalCase names and the Type/DataType/Enum suffixes keep generated code readable.",
        "BP-Naming-PascalCase",
        "2.1.2",
    ),
    "filename_convention": RuleVerdict(
        Severity.ADVISORY,
        "The file is not named Opc.Ua.<ShortName>.NodeSet2.xml (§2.4): tools and the "
        "repository expect that spelling.",
        "BP-Filename-Convention",
        "2.4",
    ),
}

#: Definition members are diffed as ``added`` / ``removed`` / ``changed``; the member kind
#: decides which rule applies.
_MEMBER_KINDS: dict[tuple[str, str], str] = {
    ("field", "added"): "field_added",
    ("field", "removed"): "field_removed",
    ("field", "changed"): "field_changed",
    ("enum_value", "added"): "enum_value_added",
    ("enum_value", "removed"): "enum_value_removed",
    ("enum_value", "changed"): "enum_value_changed",
    ("option_set_value", "added"): "option_set_bit_added",
    ("option_set_value", "removed"): "option_set_bit_removed",
    ("option_set_value", "changed"): "option_set_bit_changed",
    ("union_alternative", "added"): "alternative_added",
    ("union_alternative", "removed"): "alternative_removed",
    ("union_alternative", "changed"): "alternative_changed",
    ("member_text", "changed"): "member_text",
    ("member_metadata", "changed"): "member_metadata",
}

_MEMBER_VERDICTS: dict[str, RuleVerdict] = {
    "field_added": RuleVerdict(
        Severity.BREAKING,
        "A field was added to a Structure (§3.2.5): the encoding of every value of this type "
        "changes, so a consumer written against the old field list cannot read the new "
        "values. Add a subtype or publish a new namespace (§3.3, §11.3).",
        "BP-StructureField-Added",
        "3.2.5",
    ),
    "field_removed": RuleVerdict(
        Severity.BREAKING,
        "A field of a Structure was removed (§3.2.5): consumers that read it find nothing.",
        "BP-StructureField-Removed",
        "3.2.5",
    ),
    "field_changed": RuleVerdict(
        Severity.BREAKING,
        "A field of a Structure changed its DataType (§3.2.5): the encoding of the field "
        "changed for every consumer.",
        "BP-StructureField-Changed",
        "3.2.5",
    ),
    "enum_value_added": RuleVerdict(
        Severity.BREAKING,
        "An enum value was added (§3.2.4): the value range of the type changes, generated "
        "code is incomplete, and a consumer that rejects unknown values refuses the new "
        "ones. Use a new namespace (§11.3).",
        "BP-EnumValue-Added",
        "3.2.4",
    ),
    "enum_value_removed": RuleVerdict(
        Severity.BREAKING,
        "An enum value was removed (§3.2.4): a value a consumer may have received is no "
        "longer defined.",
        "BP-EnumValue-Removed",
        "3.2.4",
    ),
    "enum_value_changed": RuleVerdict(
        Severity.BREAKING,
        "An enum value changed its number (§3.2.4): the transmitted value means something "
        "else now.",
        "BP-EnumValue-Changed",
        "3.2.4",
    ),
    "option_set_bit_added": RuleVerdict(
        Severity.BREAKING,
        "An OptionSet bit was added (§3.2.7, §3.2.8): the set of bits a consumer may receive "
        "changes, and generated code does not know the new bit. Use a new namespace "
        "(§11.3).",
        "BP-OptionSetBit-Added",
        "3.2.7",
    ),
    "option_set_bit_removed": RuleVerdict(
        Severity.BREAKING,
        "An OptionSet bit was removed (§3.2.7, §3.2.8): a bit a consumer may have set is no "
        "longer defined.",
        "BP-OptionSetBit-Removed",
        "3.2.7",
    ),
    "option_set_bit_changed": RuleVerdict(
        Severity.BREAKING,
        "An OptionSet bit changed its value (§3.2.7, §3.2.8): the bit means something else "
        "now.",
        "BP-OptionSetBit-Changed",
        "3.2.7",
    ),
    "alternative_added": RuleVerdict(
        Severity.BREAKING,
        "An alternative was added to a Union (§3.2.6): the encoding gains a position, so a "
        "consumer written against the old alternatives cannot decode the new value. Use a new "
        "namespace (§11.3).",
        "BP-UnionAlternative-Added",
        "3.2.6",
    ),
    "alternative_removed": RuleVerdict(
        Severity.BREAKING,
        "An alternative of a Union was removed (§3.2.6): a value a consumer may have sent "
        "is no longer defined.",
        "BP-UnionAlternative-Removed",
        "3.2.6",
    ),
    "alternative_changed": RuleVerdict(
        Severity.BREAKING,
        "An alternative of a Union changed its DataType (§3.2.6): the encoding of that "
        "alternative changed.",
        "BP-UnionAlternative-Changed",
        "3.2.6",
    ),
}
_RULES.update(_MEMBER_VERDICTS)
# An IsDeprecated reference is judged by the deprecation rules, not by the generic reference
# rules (§14.2): adding it informs, removing it leaves consumers reading a stale marker.
_RULES["reference_deprecation_removed"] = _RULES["deprecation_removed"]
_RULES["reference_deprecation_added"] = _RULES["deprecation_added"]

#: Member rules keyed by (member, kind).  Every member change is a contract change (§3.2.4,
#: §3.2.5, §3.2.6, §3.2.7); only member text is documentation.
_MEMBER_RULES: dict[tuple[str, str], RuleVerdict] = {
    ("field", "added"): _MEMBER_VERDICTS["field_added"],
    ("field", "removed"): _MEMBER_VERDICTS["field_removed"],
    ("field", "changed"): _MEMBER_VERDICTS["field_changed"],
    ("enum_value", "added"): _MEMBER_VERDICTS["enum_value_added"],
    ("enum_value", "removed"): _MEMBER_VERDICTS["enum_value_removed"],
    ("enum_value", "changed"): _MEMBER_VERDICTS["enum_value_changed"],
    ("option_set_value", "added"): _MEMBER_VERDICTS["option_set_bit_added"],
    ("option_set_value", "removed"): _MEMBER_VERDICTS["option_set_bit_removed"],
    ("option_set_value", "changed"): _MEMBER_VERDICTS["option_set_bit_changed"],
    ("union_alternative", "added"): _MEMBER_VERDICTS["alternative_added"],
    ("union_alternative", "removed"): _MEMBER_VERDICTS["alternative_removed"],
    ("union_alternative", "changed"): _MEMBER_VERDICTS["alternative_changed"],
    ("member_text", "changed"): _RULES["member_text"],
    ("member_metadata", "changed"): _RULES["member_metadata"],
}


def verdict(kind: str, context: RuleContext | None = None) -> RuleVerdict:
    """The verdict for one change, given what the change actually was."""
    ctx = context or RuleContext()
    if ctx.metadata_attribute == "allow_sub_types":
        # Turning AllowSubTypes off rejects values; turning it on only widens the model.
        tightened = _RULES["allow_sub_types_tightened"]
        relaxed = _RULES["allow_sub_types_relaxed"]
        return tightened if ctx.tightened else relaxed
    if ctx.metadata_attribute == "max_string_length":
        # A smaller limit rejects values a consumer already sent (§3.2.5).
        reduced = _RULES["max_string_length_reduced"]
        raised = _RULES["max_string_length_raised"]
        return reduced if ctx.tightened else raised
    if kind == "renamed":
        proven = _RULES["field_renamed"]
        guess = _RULES["field_rename_guess"]
        return proven if ctx.rename_confidence == "exact" else guess
    if kind == "duplicate_member":
        return _RULES["duplicate_member"]
    if ctx.member is not None and (rule := _MEMBER_RULES.get((ctx.member, kind))) is not None:
        return rule
    if kind == "is_abstract":
        return (
            _RULES["is_abstract_abstract"]
            if ctx.now_abstract
            else _RULES["is_abstract_concrete"]
        )
    if kind == "modelling_rule":
        return _RULES["modelling_rule"] if ctx.tightened else _RULES["modelling_rule_relaxed"]
    if ctx.reference is not None:
        if kind == "removed":
            return _RULES[f"reference_{ctx.reference}_removed"]
        if kind == "added":
            return _RULES.get(f"reference_{ctx.reference}_added", _RULES["reference_added"])
    if kind == "attributes" and ctx.symmetric_flip:
        return _RULES["symmetric_changed"]
    return _RULES.get(kind, _NEUTRAL)


def relaxed_rule(verdict_value: RuleVerdict) -> RuleVerdict:
    """Rewrite a verdict that only relaxes a contract; used where a change widens the model."""
    return replace(verdict_value, severity=Severity.INFO)
