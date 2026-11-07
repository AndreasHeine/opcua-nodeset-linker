# Best-Practices Alignment — OPC 11030 vs. the implementation

This is the reconciliation artifact for [Best-Practices Implementation Plan](best-practices-implementation-plan.md).
For every clause of *Best Practices for Companion Models* (OPC 11030 v1.03.03, vendored as
[OPC-11030-v1.03.03.md](OPC-11030-v1.03.03.md)) it states what the code actually does and whether
that is `aligned`, `stricter`, `looser`, or `not checked`.

The two rule tables are the ground truth and this page is generated against them, never against
memory:

* `src/nodeset_linker/diff/rules.py` — `_RULES`, one verdict per change kind the diff engine emits
  (`src/nodeset_linker/diff/engine.py` produces the kind; the table supplies severity, rationale and
  the stable slug).
* `src/nodeset_linker/lint/rules.py` — `LINT_RULES`, one rule per *how a model is written* check,
  run by `lint_model()` and by `validate --best-practices`.

A slug is stable and the clause is provenance only: the paper is republished more often than the
base specifications, so `BP-BrowseName-Immutable` never encodes "§3.2.17" of one revision.

## Verdict vocabulary

| Verdict | Meaning |
| --- | --- |
| **aligned** | the implementation reaches the conclusion the paper states |
| **stricter** | the implementation flags more than the paper demands (never silently less) |
| **looser** | the implementation flags less than the paper demands; listed as a gap in [Semantic Diff](semantic-diff.md) §5 |
| **not checked** | no rule exists for the clause; listed as a gap in [Semantic Diff](semantic-diff.md) §5 |

## §2 Naming, unique names, file name

| Clause | Implementation | Verdict |
| --- | --- | --- |
| §2.1.2 PascalCase, acronyms as words | `BROWSE_NAME_NOT_PASCAL_CASE` → `BP-Naming-PascalCase` (advisory) | aligned |
| §2.1.2 special characters need a `SymbolicName` | `BROWSE_NAME_SPECIAL_CHARS_WITHOUT_SYMBOLIC_NAME` → `BP-Naming-SymbolicName`; a changed SymbolicName in a diff is `BP-SymbolicName-Identity` (advisory, §2.1.2/§3.2.17) | aligned |
| §2.1.3 `Type`/`DataType` suffixes; enums no suffix or `Enum` | `TYPE_SUFFIX_MISSING` / `TYPE_SUFFIX_FORBIDDEN` → `BP-Naming-Suffix` | aligned |
| §2.1.3 ReferenceType is a verb; InverseName is the inverse | `REFERENCE_TYPE_NAME_NOT_VERB`, `REFERENCE_INVERSE_NAME_SPELLING`; an InverseName on a symmetric base is `REFERENCE_INVERSE_ON_SYMMETRIC_BASE` and in a diff `BP-ReferenceType-Symmetric-Immutable` is breaking (§3.2.14) | aligned |
| §2.1.4 unique BrowsePaths, uniqueness once case is ignored, names not reused across namespaces | `BROWSE_PATH_CASE_COLLISION`, `TYPE_NAME_DUPLICATE_IN_NAMESPACE`, `BROWSE_NAME_USED_IN_TWO_NAMESPACES` | aligned |
| §2.2 structure/union field names PascalCase and unique | `FIELD_NAME_NOT_PASCAL_CASE`, `FIELD_NAME_DUPLICATE`; a diff reports `BP-Duplicate-Member` (advisory) | aligned |
| §2.3 enum and OptionSet value names PascalCase and unique | `ENUM_VALUE_NOT_PASCAL_CASE`, `ENUM_VALUE_NAME_DUPLICATE` | aligned |
| §2.4 file name convention | `FILENAME_CONVENTION` → `BP-Filename-Convention`; a renamed file is `BP-Filename-Convention` in the diff too | aligned |
| §2.5 NamespaceUri template, no %-escaped characters | `NAMESPACE_URI_TEMPLATE`, `NAMESPACE_URI_MISSING_TRAILING_SLASH`; in a diff `BP-NamespaceUri-Changed` is breaking, `BP-Namespace-Alias` is information | aligned |
| §2.6 method argument names PascalCase and unique | `ARGUMENT_NAME_NOT_PASCAL_CASE`, `ARGUMENT_NAME_DUPLICATE` | aligned |

The severities here are advisory/information by design: a naming rule is not a modelling error and
never changes the exit code (see README, *Validation*).

## §3 Changes to a released model

The diff engine classifies a change kind, `_RULES` gives the verdict. Severities below are the
values in that table.

| Clause | Change kinds → rule | Severity | Verdict |
| --- | --- | --- | --- |
| §3.2.1 mandatory contract tightened | `modelling_rule` → `BP-ModellingRule-Tightening`; `reference_mandatory_child_added` → `BP-Reference-MandatoryChild-Added`; `interface_mandatory_added` → `BP-Interface-Mandatory-Added`; `node_added_mandatory` → `BP-Declaration-Added-Mandatory` | breaking / advisory | aligned |
| §3.2.2 optional added | `modelling_rule_relaxed` → `BP-ModellingRule-Relaxing`; `node_added` → `BP-Declaration-Added`; `reference_added`, `reference_optional_child_added`, `reference_soft_added`, `reference_hard_added` → `BP-Reference-Added` / `BP-Reference-OptionalChild-Added` / `BP-Reference-Navigation-Added` / `BP-Reference-Contract-Added`; `subtype_inserted`, `hierarchy_inserted` | info / advisory | aligned |
| §3.2.3 interfaces | `interface_added` (advisory), `interface_removed` (breaking), `interface_mandatory_added` (breaking) | mixed | aligned |
| §3.2.4 enum values | `enum_value_added` / `enum_value_changed` / `enum_value_removed` → `BP-EnumValue-*` | breaking | aligned |
| §3.2.5 structure fields, `AllowSubTypes`, `MaxStringLength` | `field_added` / `field_changed` / `field_removed` / `field_renamed` → `BP-StructureField-*`; `member_metadata` → `BP-Member-Metadata`; `allow_sub_types_tightened` (breaking) / `allow_sub_types_relaxed` (info); `max_string_length_reduced` (breaking) / `max_string_length_raised` (info); a guessed rename is `BP-Field-Rename-Guess` (advisory) instead of a silent add+remove | mixed | aligned |
| §3.2.6 union alternatives | `alternative_added` / `alternative_changed` / `alternative_removed` → `BP-UnionAlternative-*`; order-only moves → `BP-AlternativeOrder-Encoding` (advisory) | mixed | aligned |
| §3.2.7 OptionSet bits | `option_set_bit_added` / `option_set_bit_changed` / `option_set_bit_removed` → `BP-OptionSetBit-*` | breaking | aligned |
| §3.2.9 hierarchy moves and insertions | `hierarchy_move` → `BP-Hierarchy-Move` (breaking); `event_hierarchy_inserted` → `BP-EventHierarchy-Insertion` (breaking); `subtype_inserted`, `hierarchy_inserted` → advisory | mixed | aligned |
| §3.2.10 supertype consistency, enum under a non-enum base | `supertype_incomplete` → `BP-Supertype-Inconsistent`, `supertype_enum_conflict` → `BP-Supertype-EnumConflict` | breaking | aligned |
| §3.2.11 method signature | `method_signature` → `BP-MethodSignature-Immutable`; argument metadata reference changes → `BP-MethodArg-Metadata` (information) | breaking | aligned |
| §3.2.12 datatype, `ValueRank`, `ArrayDimensions` | `data_type` → `BP-DataType-Immutable`, `value_rank`, `array_dimensions` | breaking | aligned |
| §3.2.13 `HasTypeDefinition` moved or refined | `typedefinition_moved` (breaking), `typedefinition_refined` (info) | mixed | aligned |
| §3.2.14 symmetric reference types | `symmetric_changed` → `BP-ReferenceType-Symmetric-Immutable` | breaking | aligned |
| §3.2.15 ReferenceType hierarchy | `rt_hierarchy_moved`, `rt_new_in_hierarchy` → `BP-RT-*` | breaking | aligned |
| §3.2.16 removing nodes | `node_removed` → `BP-Declaration-Removed`; an optional declaration is still breaking (`BP-Declaration-Removed-Optional`); removal of a standard OPC UA declaration is information; reference removals per category (`BP-Reference-Contract-Removed`, `BP-Reference-MandatoryChild-Removed`, `BP-Reference-OptionalChild-Removed`, `BP-Reference-Removed`) | breaking / info | aligned |
| §3.2.17 identifiers, names, node class, metadata attributes | `identifier_move` → `BP-NodeId-Immutable`, `browse_name`, `node_class`, `node_renamed`; documentation, DisplayName, Description, InverseName, categories, `IsAbstract`, value, extensions, RolePermissions, server-specific attributes are information or advisory; two documents declaring one identifier inconsistently are reported as `BP-Merge-Conflict` (advisory) naming both, and a renamed reference type is a finding on that declaration rather than a removal plus an addition on every node pointing at it | mixed | aligned |
| §3.3 a breaking change in a namespace already published | `breaking_in_released_namespace` → `BP-BreakingInReleasedNamespace`, plus the release verdict (`--release-verdict`) | breaking | aligned |

Two deliberate choices worth stating:

* `--breaking` reports only breaking findings. Breaking **additions** therefore appear under
  *Breaking changes*, not under *Added nodes* — the Added section is not a breaking section.
* Waivers never delete a finding and never waive a BREAKING finding (`diff/waivers.py`,
  `WaiverSet.decide`); `--accept-breaking` acknowledges it and the release verdict still says
  "publish a new namespace". That is stricter than the paper asks, and it is on purpose.

## §4 StatusCodes

`STATUS_CODE_DEFINED_OUTSIDE_BASE` → `BP-StatusCode-NotAllowed` (advisory): additional StatusCodes
in a companion or vendor namespace are reported. **aligned**.

## §5 Method arguments

`HasArgumentDescription` / `HasOptionalInputArgumentDescription` changes are information in both
directions (`BP-MethodArg-Metadata`), because the call contract is unchanged. **aligned**.

## §7.9 and §7.10 Structure limits and recursion

`STRUCTURE_OPTIONAL_FIELDS_LIMIT` (≤ 32 optional fields), `STRUCTURE_SUBTYPES_WITH_OPTIONAL_FIELDS`
(`AllowSubTypes` plus optional fields in one structure), and
`MANDATORY_SELF_CONTAINED_DECLARATION` (a self-containing declaration must not be Mandatory or
MandatoryPlaceholder). **aligned**.

## §11 Namespace and model metadata

| Clause | Implementation | Verdict |
| --- | --- | --- |
| §11.1/§11.2 model metadata, `RequiredModel`, subset marking | namespace-level findings: `BP-Namespace-Added`, `BP-Namespace-Removed`, `BP-Namespace-Bump`, `BP-Namespace-Version`, `BP-Namespace-VersionDrift`, `BP-Namespace-ModelVersion`, `BP-Namespace-PublicationDate`, `BP-Namespace-XmlSchema`, `BP-Namespace-RequiredModel`, `BP-Namespace-MetadataConflict`, `BP-Namespace-ModelPrecedence` | aligned |
| Version claims judged on the release, not the spelling (OPC 10000-6 Annex F.2, OPC 10000-5 §12.41) | `Version` is display-only and `ModelVersion` is the comparable `SemanticVersionString`, so `models/version.py` compares both on their numeric core: a padded `1.04.0` beside a SemVer `1.4.0` is one release and reports nothing, `BP-Namespace-VersionDrift` (advisory) fires only on two claims naming different releases, an invalid `ModelVersion` is `BP-Namespace-ModelVersionSyntax` (advisory), and a `RequiredModel` minimum version is read through its `ModelVersion` | aligned |
| §11.2 externalized declarations of a Linker export | `--reduced-export` reads a `RequiredModel` that ships no nodes as `BP-Declaration-Externalized` (information) instead of a removal | aligned |
| §11.2.6 a type definition replaced by a new one | `type_replaced` → `BP-Type-Replaced` (advisory), `type_replaced_numbered` → `BP-Type-Replaced-Numbered` (information) when the replacement carries the version-suffixed name the paper recommends | aligned |
| §11.3 new NamespaceUri for a breaking change | `namespace_uri_changed` → breaking; the release verdict answers *reusable* vs *publish `V2/`*; `namespace_without_cu` → `BP-NewNamespace-WithoutCU` (advisory) | aligned |

## §12 ConformanceUnits and Profiles

`cu_added` / `cu_removed` / `reference_cu_dependency_added` / `reference_cu_dependency_removed` →
`BP-CU-Added`, `BP-CU-Removed`, `BP-CU-DependencyChanged`; `profile_added` / `profile_removed` →
`BP-Profile-Added`, `BP-Profile-Removed`. Removals are advisory, additions are information: a
consumer reads conformance declarations as documentation, not as a browse contract. **aligned**.

## §13.3 NamespaceIndex in text

`NAMESPACE_INDEX_IN_TEXT` → `BP-Text-NamespaceIndex`: a description or documentation string that
spells `ns=<index>` is reported, because indexes differ between servers. **aligned**.

## §14 Deprecation

`deprecation_added` / `deprecation_removed` → `BP-Deprecation-Added` / `BP-Deprecation-Removed`,
`deprecation_version` → `BP-Deprecation-Version` (§14.3.2 version metadata of a deprecation),
`deprecations_without_new_namespace` → `BP-Deprecation-Without-NewNamespace` (§14.3: deprecating
without stating a replacement namespace). Removal of a deprecated node is judged by the removal
rules, not by deprecation alone. **aligned**.

## §15 Audit events

`AUDIT_EVENT_SUBTYPE_ADDS_FIELDS` → `BP-AuditEvent-ExtraFields`: an audit-event subtype that adds
fields is reported. **aligned**.

## Where the implementation is still looser than the paper

These were the open items; each is tracked with its own entry in [Semantic Diff](semantic-diff.md)
§5. Every one of them is now closed, so no clause of the paper is unimplemented:

* identity — `ServerUri` is part of diff identity, references key by the reference-type
  *identifier* and count multiplicity (gaps 1, 2, 6, 7);
* content — `<Extensions>` and `RolePermissions` are compared, and the identifiers a serialized
  `<Value>` points at are diffed as resolved dependencies (gaps 8, 9). An identifier a document
  spells and nothing resolves is a parse error rather than an invented default (§3.2.12);
* tightening additions — a Mandatory component attach (§3.2.1, §11.2.1), a ReferenceType placed
  inside the hierarchy (§3.2.15), an inconsistent supertype (§3.2.10) and an Interface bringing
  new mandatory declarations (§3.2.3) are breaking (gaps 4, 5, 22);
* one inconsistent re-declaration no longer aborts a diff: the merge keeps the first declaration
  and the report names both documents as an advisory `BP-Merge-Conflict` (gap 24, §3.2.17). A
  reduction still refuses it, because exporting a model that dropped a declaration publishes a
  contract nobody declared;
* each diff side may be a split-export bundle — a directory with `manifest.json` — so a
  namespace-split release compares as the model it replaced (gap 23);
* `--namespace`, `--exclude-namespace` and `--node-class` scope a report, filtering both sides
  by NamespaceUri, the declared `Alias` or the URI tail (gap 25); a `--namespace` neither side
  declares exits 1 rather than printing an empty report;

What stays open in §5 is engineering, not a clause of the paper: impact cost and caching (gaps
19, 26), a value-vs-datatype consistency rule (gap 11), pre-diff validation (gap 29), browse-name
globs, CSV export and published JSON schema (gaps 25, 27, 30).
