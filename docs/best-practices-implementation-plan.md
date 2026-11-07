# Best-Practices Alignment — Implementation Plan

Self-contained work plan for aligning the semantic diff with the OPC UA best-practices whitepaper
OPC 11030. Written so it can be executed in a later session without re-deriving the current code.

**Status: executed.** Work packages WP 1-15 are implemented (severity table, metadata and release
verdict, deprecation, ConformanceUnits/Profiles, impact identifiers, lint pass, waivers), so the plan
is kept as the provenance record for the `WP n` numbers cited in code comments, not as pending work.
Section 0 describes the *pre-implementation* state of the code — its line numbers are historical and
do not point at today's files. What the code does per clause today is tracked in
[best-practices-alignment.md](best-practices-alignment.md); what is still open is
[semantic-diff.md §5](semantic-diff.md).

* Analysis that produced this plan: [best-practices-alignment.md](best-practices-alignment.md)
* Source text of the paper: `docs/OPC-11030-v1.03.03.md` (v1.03.03, published 2026-03-23) — cited below
  as `§3.2.5` etc. It is a whitepaper ("highly recommended"), **not** a conformance spec: every finding
  is advisory, and the paper is republished more often than the base specs, so rule ids must be
  version-tolerant (never hardcode a clause number into a user-facing error message only).
* Roadmap context: [semantic-diff.md §5/§7/§8](semantic-diff.md) — gap ids 1-25 and priorities P0-P3.

## 0. Ground truth of the current implementation (verified, with locations)

All paths relative to `src/nodeset_linker/`.

**`diff/model.py`** (296 lines) — the report contract:
* `CHANGE_LABELS` (16-36): `node_class, browse_name, display_name, description, symbolic_name,
  inverse_name, categories, attributes, documentation, modelling_rule, is_abstract, data_type,
  value_rank, array_dimensions, value, definition_kind, field_order, alternative_order`.
* `Change(area, kind, subject, old, new, breaking=False)` (44-83) + `describe()` + `as_json()`
  (JSON keys: `area, kind, subject, old, new, breaking, text`).
* `NodeEntry(label, node_id, node_class, namespace, breaking, impact)` (86-106).
* `NodeModification(..., changes, impact)` (109-137); `breaking` = `any(change.breaking)`.
* `RenumberedNode(label, old_node_id, new_node_id, node_class, namespace, changes, impact)`
  (140-176); `breaking` = `any(change.breaking)` — **the identifier move itself carries no flag**.
* `BreakingChange(label, node_id, reason, affected)` (179-195) — `reason` is rendered text, no rule id.
* `NamespaceChange(alias, uri, nodes, references, datatypes)` (198-234) — totals only.
* `SemanticDiff(added, removed, modified, renumbered, namespaces, breaking)` + `summary()` +
  `as_json()` (237-291); `json_text()` (294).

**`diff/engine.py`** (890 lines) — classification:
* `Identity = (namespace_uri, identifier)` (56-57); `ModelIndex.of()` (104-118), `label()` (123-136),
  `alias()` (138-140).
* `HARD_REFERENCES = {HasSubtype, HasTypeDefinition, HasInterface, HasEncoding, GeneratesEvent}` (60-62);
  `SOFT_REFERENCES = {Organizes, HasDescription}` (63-64); `FIELD_METADATA_LABELS` = symbolic_name,
  value_rank, array_dimensions, max_string_length, allow_sub_types (67-73);
  `DOCUMENT_LOCAL_ATTRIBUTES` = NodeId, BrowseName, DataType, SymbolicName, ParentNodeId, Handle,
  TypeId, TargetId, DefinitionType (80-92); `_semantic_attributes()` filters those (≈860-890).
* `diff_models()` ≈140-190; `_pair_renumbering()` 190-234: groups removed/added by
  `(namespace, browse_name.name, node_class)` and `zip()`s sorted identities — blind, no confidence.
* `_entry(node, index, *, breaking)` 265-274; `_removal_breaking()` 275-296: types / `NodeRole.ENCODING` /
  `NodeRole.PLACEHOLDER` → breaking, else `_is_explicitly_mandatory(node)` (needs Mandatory or
  MandatoryPlaceholder *and* `mandatory_level_of(node) is MandatoryLevel.MANDATORY`; UA namespace → False).
* `_node_changes()` 298-364: `node_class` breaking, `browse_name` (qualified name incl. namespace)
  breaking, `display_name`/`description`/`documentation` non-breaking, **`symbolic_name` breaking (321)**,
  **`inverse_name` breaking (322)**, `categories` non-breaking, generic `attributes` non-breaking (339-347),
  `is_abstract` → `breaking=new_node.is_abstract` (348-357), then `_modelling_rule_change` (367-380,
  tightening = Optional→Mandatory), `_variable_changes` (383-431: `data_type`, `value_rank`,
  `array_dimensions` breaking; `value` non-breaking), `_definition_changes` (433-477, only for
  `UADataType`), `_reference_changes` (717-770).
* `_structure_changes()` 478-547: added field `breaking=False`; removed field
  `breaking=not old_field.is_optional`; field `data_type` breaking; `IsOptional` change
  `breaking=not new_field.is_optional`; metadata via `FIELD_METADATA_LABELS`; `_order_change(..., kind="field_order")` breaking. Members are keyed **by name**, so duplicates collapse silently.
* `_union_changes()` 548-604: alternative removed breaking, added non-breaking, `alternative_order` breaking.
* `_value_member_changes()` 607-654 (Enum + OptionSet, keyed by name): added `breaking=False` (618-626),
  removed breaking, numeric change breaking; `_member_text_changes()` 657-674 non-breaking.
* `_metadata_changes()` 676-716; `_reference_changes()` 717-770: removal
  `_reference_removal_breaking(name, target, index)` (771-781): SOFT → False, HARD → True, ownership
  references → `_is_explicitly_mandatory(target)`, anything else → True. **Additions always
  `breaking=False`.**
* `_namespace_changes()` 782-818: `NamespaceTotals(nodes, references, datatypes)` only.
* `_breaking_changes()` 819-860: removed entries + all changes with `breaking=True` in `modified`
  and `renumbered` → `BreakingChange`.

**`diff/impact.py`** (89 lines): `apply_impact()`, `total_impact()`, `_dependents()` (72-89) returns
`node_label()` **strings**; `STRUCTURAL_ONLY = {DependencyKind.NAMESPACE}`; phantom vertices skipped.

**`diff/render.py`**: `console_text/markdown_text/html_text(diff, *, impact=False, breaking_only=False)`;
`_sections()` 128-183 (Summary, added, removed, modified, renumbered, namespaces, breaking);
`--breaking` hides additions and renumbering entirely (135, 152).

**`cli/main.py` `diff` command** 182-247: flags `--json --markdown --html --impact --breaking
--fail-on-breaking --output`; loads one file per side (`load_nodesets([old])`), `apply_impact()` when
`impact or breaking_only or fail_on_breaking`, exits 1 if `fail_on_breaking and result.breaking`.

**Models available for the new rules**:
* `models/node.py`: `UANode` with `attributes: Mapping[str,str]`, `role_permissions_xml: tuple[str,...]`
  (138), `extensions_xml` (kept by the parser, never diffed), `modelling_rule`, `is_abstract`,
  `categories`, `symbolic_name`, `inverse_name`, `display_name`, `description`, `documentation`,
  `data_type`, `value_rank`, `array_dimensions`, `value_xml`, `value_dependencies`,
  `definition: Definition | None` for `UADataType` (`fields`, `union_alternatives`, enum/optionset values),
  `mandatory_level_of()`, `MandatoryLevel`, `is_optional` on fields.
* `models/node_class.py`: `NodeClass` (Object, Variable, Method, ObjectType, VariableType, DataType,
  ReferenceType, View, Interface), `NodeRole` (abstract_type, base_type, structure, enum, union,
  option_set, encoding, interface, method, argument, placeholder, namespace_metadata, view, instance,
  type_definition), `ModellingRule` (Mandatory, MandatoryPlaceholder, Optional, OptionalPlaceholder).
* `models/namespace.py`: `Namespace(uri, alias, index_in_document, version, publication_date,
  xml_schema_uri, model_version)`; `ModelInfo(namespace_uri, model_name, xml_schema_uri, version,
  publication_date, model_version, required_models, required_model_entries)`; `DocumentInfo(source_path,
  namespaces, models, server_uris)`; `alias_from_uri()`, `is_ua_namespace()`.
* `repository/model.py`: `SemanticModel(nodes: dict[ExpandedNodeId, UANode], namespaces: dict[str, Namespace],
  documents: list[DocumentInfo], models: dict[str, ModelInfo])`; `add()` raises `DuplicateNodeError`;
  `load_nodesets()` merges namespaces with `_merge_namespace()` (richest value wins, no conflict warning)
  and keeps the **first** `ModelInfo` per URI; helpers `nodes_in_namespace()`, browse-name/display-name lookups.
* `graph/engine.py`: `STANDARD_REFERENCE_TYPES` (31-42), `OWNERSHIP_REFERENCES` (44-47) =
  `{HasComponent, HasProperty, HasOrderedComponent, HasArgumentDescription,
  HasOptionalInputArgumentDescription}`, `FORWARD_DEPENDENCIES` (48-50), `SemanticGraph.from_model()`,
  `in_edges()` returning `EdgeMetadata.dependency_kind`.
* `validator/semantic.py`: `Finding(code, message, …)` (31-40), `ValidationReport` (44-56) with
  `ok`/`summary`; checks are structural only (reference targets, reference-type declarations, type
  definitions, datatype references, reference-type hierarchy, subtype hierarchy + cycles, encodings,
  namespace links, external dependencies, subset markers). **No naming/best-practice checks.**
* Tests: `tests/test_diff.py` (`test_synthetic_structure_field_removal_is_breaking`,
  `test_display_name_change_is_not_breaking`, `test_enum_definition_value_change_is_breaking`);
  release fixtures in `examples/demo3`, `examples/demo4`.

## 1. Target design: severities and rule ids (do this first)

Everything below depends on it, so it lands as its own commit.

**New enum** `Severity(StrEnum)` in `diff/model.py`: `INFO`, `ADVISORY`, `BREAKING`
(`INFO` = paper allows and even recommends; `ADVISORY` = allowed but discouraged; `BREAKING` = paper
forbids / client-visible contract break).

**`Change` gains** `severity: Severity = INFO`, `rule: str | None = None`, `remedy: str | None = None`,
and `breaking` becomes a derived property:

```python
@dataclass(frozen=True, slots=True)
class Change:
    area: str
    kind: str
    subject: str | None = None
    old: str | None = None
    new: str | None = None
    severity: Severity = Severity.INFO
    rule: str | None = None
    remedy: str | None = None

    @property
    def breaking(self) -> bool:          # keeps every existing consumer working
        return self.severity is Severity.BREAKING
```

Call sites currently pass `breaking=True/False`; rewrite them to `severity=...` (mechanical, ~30 sites).

**Rule registry** — new `diff/rules.py`, one constant per rule, so ids never drift:

```python
@dataclass(frozen=True, slots=True)
class Rule:
    id: str                 # e.g. "BP-ID-MOVED"
    severity: Severity
    clause: str             # "OPC-11030 §3.2.17"
    remedy: str             # human advice, shown in reports
```

Registry (id → severity → clause → remedy). Severity `BREAKING` means "gate fails";
`ADVISORY` means "release note must mention it".

| id | severity | clause | applies when |
| --- | --- | --- | --- |
| `BP-ID-MOVED` | BREAKING | §3.2.17 (Base/NodeId) | identifier changed for a paired node |
| `BP-CLASS-CHANGED` | BREAKING | §3.2.17 (Base/NodeClass) | NodeClass changed |
| `BP-BROWSE-NAME-CHANGED` | BREAKING | §3.2.17 (Base/BrowseName) | BrowseName (incl. its namespace) changed |
| `BP-DATATYPE-CHANGED` | BREAKING | §3.2.12 | Variable/VariableType `DataType` changed (subtype refinement included) |
| `BP-VALUE-RANK-CHANGED` | BREAKING | §3.2.12 | `ValueRank` changed |
| `BP-ARRAY-DIMENSIONS-CHANGED` | BREAKING | §3.2.12 | `ArrayDimensions` changed |
| `BP-IS-ABSTRACT-TIGHTENED` | BREAKING | §3.2.17 | `IsAbstract` false → true |
| `BP-IS-ABSTRACT-LOOSED` | INFO | §3.2.17 | `IsAbstract` true → false |
| `BP-MODELLING-RULE-TIGHTENED` | BREAKING | §3.2.1, §11.2.1 | Optional → Mandatory (incl. placeholder pair) |
| `BP-MODELLING-RULE-LOOSED` | ADVISORY | §3.2.16 spirit | Mandatory → Optional |
| `BP-SYMMETRIC-CHANGED` | BREAKING | §3.2.17 (ReferenceType/Symmetric) | `Symmetric` changed |
| `BP-INVERSE-NAME-CHANGED` | ADVISORY | §3.2.17 remark | `InverseName` changed (meaning can't be verified) |
| `BP-SYMBOLIC-NAME-CHANGED` | ADVISORY | §3.2.17 remark | `SymbolicName` changed (code generation) |
| `BP-ATTRIBUTES-CHANGED` | INFO | §3.2.17 (may-change rows) | AccessLevel, Historizing, WriteMask, MinimumSamplingInterval, EventNotifier, ContainsNoLoops, categories |
| `BP-ROLE-PERMISSIONS-CHANGED` | INFO | §3.2.17 | `role_permissions_xml` changed |
| `BP-EXTENSIONS-CHANGED` | ADVISORY | §3.2.17 spirit | `<Extensions>` content changed |
| `BP-ID-ADDED-MANDATORY` | BREAKING | §3.2.1, §11.2.1 | ownership reference added to a Mandatory instance declaration |
| `BP-ID-ADDED-OPTIONAL` | INFO | §3.2.2, §11.2.1 | ownership reference added to an Optional instance declaration |
| `BP-INTERFACE-ADDED` | INFO | §3.2.3 | `HasInterface` added **and** the interface adds nothing mandatory |
| `BP-INTERFACE-ADDED-MANDATORY` | BREAKING | §3.2.3 | `HasInterface` added while the interface brings new mandatory declarations |
| `BP-TYPEDEFINITION-MOVED` | BREAKING | §3.2.13 | `HasTypeDefinition` changed to a non-subtype |
| `BP-TYPEDEFINITION-REFINED` | INFO | §3.2.13 | changed to a subtype that adds no mandatory declaration and no DataType refinement |
| `BP-ENUM-VALUE-ADDED` | BREAKING | §3.2.4, §11.2.3 | enum value added |
| `BP-ENUM-VALUE-REMOVED` | BREAKING | §3.2.4 | enum value removed |
| `BP-ENUM-VALUE-NUMBER-CHANGED` | BREAKING | §3.2.4 | numeric value of an enum entry changed |
| `BP-FIELD-ADDED` | BREAKING | §3.2.5, §11.2.4 | structure field added (even optional) |
| `BP-FIELD-REMOVED` | BREAKING | §3.2.5 | field removed (optional or mandatory) |
| `BP-FIELD-RENAMED` | BREAKING | §3.2.5 | field identity changed (today invisible as add+remove) |
| `BP-FIELD-DATATYPE-CHANGED` | BREAKING | §3.2.5 | field `DataType` / ValueRank / ArrayDimensions changed |
| `BP-FIELD-OPTIONALITY-TIGHTENED` | BREAKING | §3.2.5 spirit | `IsOptional` true → false |
| `BP-FIELD-ORDER-CHANGED` | ADVISORY | encoding order (paper silent) | field order changed |
| `BP-UNION-ALT-ADDED` | BREAKING | §3.2.6, §11.2.4 | union alternative added |
| `BP-UNION-ALT-REMOVED` | BREAKING | §3.2.6 | union alternative removed |
| `BP-UNION-ALT-ORDER-CHANGED` | ADVISORY | §3.2.6 spirit | alternative order changed |
| `BP-OPTIONSET-BIT-ADDED` | BREAKING | §3.2.7, §3.2.8, §11.2.5 | OptionSet entry added |
| `BP-OPTIONSET-BIT-REMOVED` | BREAKING | §3.2.7, §3.2.8 | OptionSet entry removed |
| `BP-OPTIONSET-BIT-NUMBER-CHANGED` | BREAKING | §3.2.7 | bit value changed |
| `BP-OPTIONSET-TEXT-CHANGED` | INFO | §3.2.7, §3.2.8 | entry DisplayName/Description text changed |
| `BP-METHOD-SIGNATURE-CHANGED` | BREAKING | §3.2.11 | argument added/removed or its DataType changed |
| `BP-METHOD-ARG-METADATA` | INFO | §3.2.11 | `HasArgumentDescription` metadata added (EngineeringUnits etc.) |
| `BP-METHOD-ARG-TEXT-CHANGED` | INFO | §3.2.11 | argument description text changed |
| `BP-NODE-REMOVED` | BREAKING | §3.2.16 | any node removed (type, encoding, placeholder, instance declaration, standardized instance, optional declaration) |
| `BP-HIERARCHY-INSERTION` | ADVISORY | §3.2.9, §3.2.10 | a new type inserted between existing types |
| `BP-EVENT-HIERARCHY-INSERTION` | BREAKING | §3.2.9 | insertion inside the EventType hierarchy |
| `BP-SUPERTYPE-INCONSISTENT` | BREAKING | §3.2.10 | new supertype lacks fields/values its subtypes already had |
| `BP-RT-NEW-IN-HIERARCHY` | BREAKING | §3.2.15 | new ReferenceType placed inside an existing hierarchy |
| `BP-RT-HIERARCHY-MOVED` | BREAKING | §3.2.15 | hierarchical ↔ non-hierarchical ReferenceType |
| `BP-DEPRECATION-ADDED` | INFO | §14.2 | `IsDeprecated` reference added |
| `BP-DEPRECATION-REMOVED` | ADVISORY | §14.2 | `IsDeprecated` reference removed |
| `BP-TYPE-REPLACED` | ADVISORY | §11.2.6 | old type deprecated/removed + one new type, name without version suffix |
| `BP-METADATA-CHANGED` | INFO | §11.1, §11.2 | version / publication date / model version / schema URI / RequiredModel changed |
| `BP-METADATA-CONFLICT` | ADVISORY | §11.1 | two documents declare different metadata for the same namespace |
| `BP-BREAKING-IN-RELEASED-NAMESPACE` | BREAKING | §14.3.1, §3.3, §11.3 | breaking changes while the NamespaceUri stays unchanged |
| `BP-NAMESPACE-BUMP` | INFO | §11.3 | NamespaceUri extended with `V<major>/` |
| `BP-NEW-NS-WITHOUT-CU` | ADVISORY | §11.3 | new NamespaceUri without new ConformanceUnits/Profiles |
| `BP-CU-ADDED` | INFO | §12.1 | ConformanceUnit added |
| `BP-CU-REMOVED` | ADVISORY | §12.1, §12.2 | ConformanceUnit/Profile removed (certification changes) |
| `BP-CU-DEPENDENCY-CHANGED` | ADVISORY | §12.1 | CU's required nodes/profile membership changed |

Waiving: `--waive BP-<id>` (repeatable) and an optional waiver file (see WP 15). Only `ADVISORY` and
`INFO` may be waived; `BREAKING` requires an explicit acknowledgement flag so a silent pass stays impossible.

## 2. Work packages

Each WP is one commit, independently testable. Order is dependency order, not importance.

### WP 1 — Severity + rule registry (foundation)
* Add `Severity`, `Rule`, `diff/rules.py`; extend `Change`, `NodeEntry`, `RenumberedNode`,
  `BreakingChange` (`BreakingChange` gains `rule`, `severity`, `remedy`, keeps `reason`).
* `SemanticDiff` gains `advisory: list[BreakingChange]`-style buckets, or simply keep one flat list
  with severity and let render group. Recommended: `diff.findings: list[Finding]` where
  `Finding(label, node_id, rule, severity, remedy, affected)`, and keep `diff.breaking` as a
  filtered view for compatibility.
* JSON: add `severity`, `rule`, `remedy` to change objects; add `summary.findings_by_severity`; keep
  every existing key unchanged (preview tier + CI consumers must not break).
* Render: `_sections()` groups into Breaking / Advisory / Information; `--breaking` becomes
  "breaking only" but must **not** hide renumbering or additions any more — add `--no-advisory`
  instead of relying on `--breaking` for suppression (fixes semantic-diff gap 22).
* CLI: `--fail-on-breaking` unchanged; add `--fail-on-advisory`.
* Acceptance: existing tests still pass; JSON keys additive; one new test asserting a
  `display_name` change is `severity=info`, `rule=BP-ATTRIBUTES-CHANGED`… and that
  `breaking` still reports False.

### WP 2 — Identifier move is breaking (gap 4)
* `_pair_renumbering` (190-234): append a `Change(area="identity", kind="identifier_moved",
  old=old_id, new=new_id, severity=BREAKING, rule=BP-ID-MOVED, remedy="keep published identifiers;
  publish a new NamespaceUri instead")` to every `RenumberedNode`, regardless of content changes.
* Add `confidence` to the pairing (exact when group sizes are 1:1, heuristic otherwise) and render it,
  because the heuristic can mis-pair (gap 3).
* `_breaking_changes` already iterates `renumbered`, so the finding appears automatically.
* Acceptance: fixture where only NodeIds change → `breaking_changes >= n`, exit code 1 with
  `--fail-on-breaking`.

### WP 3 — Additions can break (gap 5)
* `_reference_changes` (717-770): on addition, classify by reference name:
  * ownership references (`OWNERSHIP_REFERENCES` from `graph.engine`) → look at the **target node in
    the new model**: if its role is an instance declaration and `mandatory_level_of(target) is
    MANDATORY` → `BP-ID-ADDED-MANDATORY` BREAKING; if OPTIONAL → `BP-ID-ADDED-OPTIONAL` INFO.
  * `HasInterface` → run WP 8's interface check (new mandatory declarations reachable through the
    interface → `BP-INTERFACE-ADDED-MANDATORY`).
  * `HasTypeDefinition` → WP 8's refinement rule.
  * `HasSubtype` / `HasEncoding` addition stays INFO unless WP 8's hierarchy-insertion rule applies.
  * `HasArgumentDescription` addition on a Method → `BP-METHOD-ARG-METADATA` INFO.
* `_value_member_changes` (607-654): added enum/OptionSet entry → BREAKING (`BP-ENUM-VALUE-ADDED` /
  `BP-OPTIONSET-BIT-ADDED`).
* `_structure_changes` (478-547) / `_union_changes` (548-604): added field/alternative → BREAKING.
* Acceptance: one fixture per kind; optional instance-declaration addition must stay INFO (§3.2.2).

### WP 4 — Removals must be broader (gap 20 + §3.2.16)
* `_removal_breaking` (275-296): every removed node → BREAKING with a sub-rule that explains which
  kind of node it was (`BP-NODE-REMOVED`). Do **not** keep the "optional ⇒ not breaking" branch:
  §3.2.16 forbids removing optional InstanceDeclarations too.
* Keep UA-namespace nodes (`is_ua_namespace`) out of this: those are base-model nodes a companion
  spec re-declares for context — removing them from a *reduced* model is a linker operation, not a
  spec change. Confirm this distinction with a test, because `diff` is also used on reduced exports.
* `IsDeprecated` removal → ADVISORY, not BREAKING (§14.2): special-case before the generic branch.
* Acceptance: fixture removing an Optional variable → breaking; removing a UA base node → no finding.

### WP 5 — Structure field rules + duplicate-name visibility
* Removed optional field → BREAKING (today `breaking=not old_field.is_optional`).
* Detect rename instead of add+remove: pair removed/added fields by name similarity or by position in
  the field list when one side has exactly one removal and one addition at the same index →
  `BP-FIELD-RENAMED`.
* Duplicate members: `_value_member_changes` and `_structure_changes` key by name; when a name occurs
  more than once, emit an ADVISORY `BP-DUPLICATE-MEMBER` (paper §2 forbids duplicates) instead of
  collapsing them. Implement by counting occurrences before building the dicts.
* `allow_sub_types` / `max_string_length` metadata changes: keep BREAKING (encoding change), but give
  them their own rule ids (`BP-FIELD-ALLOW-SUBTYPES-CHANGED`, `BP-FIELD-MAX-STRING-LENGTH-DECREASED`
  — the latter BREAKING only when it decreases; increases → INFO per §7.9 reasoning).

### WP 6 — Attribute table instead of the blanket bucket (§3.2.17)
* Split `_semantic_attributes()` (≈860-890) output into (a) identity attributes (already handled as
  model fields), (b) forbidden attributes: `Symmetric` → BREAKING `BP-SYMMETRIC-CHANGED`,
  (c) allowed attributes → INFO `BP-ATTRIBUTES-CHANGED` with the attribute name in `subject`
  (`AccessLevel`, `Historizing`, `MinimumSamplingInterval`, `WriteMask`, `AccessRestrictions`,
  `EventNotifier`, `ContainsNoLoops`, `Executable`).
* Compare `role_permissions_xml` (138) → INFO `BP-ROLE-PERMISSIONS-CHANGED`; compare `extensions_xml`
  → ADVISORY `BP-EXTENSIONS-CHANGED` (normalise whitespace/XML like `value_xml` does).
* Demote `symbolic_name` (321) and `inverse_name` (322) to ADVISORY with remedy text.
* Keep `value` INFO; optionally add semantic value-dependency diff (`value_dependencies`) as
  ADVISORY when a resolved identifier inside a value changed (gap 9).

### WP 7 — Method signature finding (§3.2.11)
* New helper `_method_signature_change(old_node, new_node, old_index, new_index)` producing **one**
  `Change(area="method", kind="signature", severity=BREAKING, rule=BP-METHOD-SIGNATURE-CHANGED,
  remedy="define a new Method with a new BrowseName, e.g. Refresh2 (§3.2.11)")`.
* Signature = ordered list of `(argument name, data_type, value_rank, array_dimensions)` from the
  argument nodes reachable via ownership references (`HasArgumentDescription` / argument role
  `NodeRole.ARGUMENT`), not from raw references.
* Suppress the low-level argument add/remove findings when the signature finding fires, so a report
  is not noisy; argument description text changes stay INFO (`BP-METHOD-ARG-TEXT-CHANGED`).
* Note: `_definition_changes` (433-477) currently only runs for `UADataType`; Methods must be handled
  here instead.

### WP 8 — Hierarchy-aware classification (uses `SemanticGraph` / HasSubtype edges)
* Build subtype maps from both models: `{node_id: supertype_id(s)}` from `HasSubtype`.
* **Hierarchy insertion** (§3.2.9/§3.2.10): a node whose supertype changed to a *newly added* type →
  ADVISORY `BP-HIERARCHY-INSERTION`; if the affected chain reaches the UA `BaseEventType` (look up by
  BrowseName in the UA namespace via `SemanticModel` browse-name lookup, do not hardcode identifiers) →
  BREAKING `BP-EVENT-HIERARCHY-INSERTION`.
* **Supertype consistency** (§3.2.10): for a new supertype, compare its fields/enum values against
  every existing subtype — a supertype missing a field/value the subtypes already had → BREAKING
  `BP-SUPERTYPE-INCONSISTENT`; enum numeric conflicts → BREAKING.
* **ReferenceType placement** (§3.2.15): new ReferenceType whose supertype is not a new type →
  BREAKING `BP-RT-NEW-IN-HIERARCHY`; hierarchical ↔ non-hierarchical flip → BREAKING `BP-RT-HIERARCHY-MOVED`.
* **Interface additions** (§3.2.3): walk the interface's declaration subtree (reuse the closure/graph,
  which already follows ownership edges) and check whether it brings mandatory declarations the
  ObjectType did not already have.
* **TypeDefinition refinement** (§3.2.13): subtype relation holds AND subtype adds no mandatory
  declaration AND (for Variables) does not refine `DataType` → INFO `BP-TYPEDEFINITION-REFINED`;
  otherwise BREAKING `BP-TYPEDEFINITION-MOVED`.

### WP 9 — Namespace and model metadata diff (§11.1, §11.2; gaps 12-14)
* Extend `NamespaceChange` (model.py 198-234) with metadata pairs: `version`, `publication_date`,
  `model_version`, `xml_schema_uri`, `alias`, and `required_models` (`ModelInfo` from
  `repository/model.py:39`, plus `required_model_entries` version claims).
* New `_metadata_changes(old_model, new_model)` comparing `SemanticModel.namespaces` and
  `SemanticModel.models` (both `dict[str, ...]`, keyed by URI — easy to diff).
* Report metadata-only namespace changes too (today a namespace appears only if nodes changed).
* `_merge_namespace()` conflict → ADVISORY finding, and same for the first-`ModelInfo`-wins behaviour.
* JSON: add a `metadata` block per namespace change; render as a `_namespace_line` extension.

### WP 10 — Release gate: "may this NamespaceUri be reused?" (§3.3, §11.3, §14.3.1)
* New module `diff/release.py`:
  ```python
  @dataclass(frozen=True, slots=True)
  class ReleaseVerdict:
      reusable: bool
      namespaces: tuple[NamespaceVerdict, ...]   # uri, breaking findings, metadata delta, bumped?
      recommendation: str                        # "publish V2/" etc.
  ```
* Rule: for each namespace, if any BREAKING finding exists and the NamespaceUri did **not** change
  (or only changed in a non-major way), emit `BP-BREAKING-IN-RELEASED-NAMESPACE` and recommend
  extending the URI with `V<major>/` (§11.3: only the major version belongs in the URI).
* Detect the bump pattern `…/<spec>/V<major>/` (regex on the URI tail) → INFO `BP-NAMESPACE-BUMP`.
* Baseline registry: `--baseline <dir-or-manifest>` states which NamespaceUris were already published
  (a small JSON/YAML file listing uri + version + publication date). Without a baseline, the verdict
  says "baseline unknown" and only reports the metadata delta — never invent a verdict.
* CLI: `diff` gains `--baseline` and `--release-verdict`; exit code 1 when the verdict is not reusable.
* This is the paper's actual question; it must be visible in the report summary, not buried in
  per-node findings.

### WP 11 — Deprecation awareness (§14.2, §11.2.6)
* Detect `IsDeprecated` references (target = deprecated Object) → INFO finding on both sides.
* Detect the `NamespaceMetadata` deprecation objects (`NodeRole.NAMESPACE_METADATA` + BrowseName like
  `<SpecName>Version1_02`) and report them as release information.
* Detect "old type removed/deprecated + one new type with the same base name": if the new BrowseName
  lacks the version suffix (§11.2.6 recommends `DeviceType2`) → ADVISORY `BP-TYPE-REPLACED`.
* Add ADVISORY "prefer a new NamespaceUri over deprecation for companion specs" (§14.3) when
  deprecations appear while the NamespaceUri stays unchanged.

### WP 12 — ConformanceUnits and Profiles (§12, §11.3)
* Identify CU/Profile nodes by BrowseName in the spec namespace (`ConformanceUnit`, `Profile`,
  `ConformanceUnitList`/`ProfileList` shapes — resolve by BrowseName, not by hardcoded ids).
* Diff: CU added → INFO; CU removed → ADVISORY `BP-CU-REMOVED`; CU dependencies (which nodes/types it
  requires) changed → ADVISORY `BP-CU-DEPENDENCY-CHANGED`; Profile membership changes → ADVISORY.
* New NamespaceUri without any new CU/Profile → ADVISORY `BP-NEW-NS-WITHOUT-CU` (§11.3 requires them).
* Note: this also fixes "removing a CU is invisible" (plain object removal today reports non-breaking).

### WP 13 — Impact by identifier (gap 16)
* `_dependents()` (impact.py 72-89) returns `tuple[ExpandedNodeId, ...]`; keep labels for display by
  rendering from the graph, and add `impacted_nodes` as XML identifiers in JSON plus
  `impacted_labels` for humans.
* Add depth/limit controls: `--impact-limit N` (default 50 shown, count always exact) and exclude
  reverse edges of `DependencyKind.INHERITANCE` beyond a depth of 3 for base-type removals so the
  report stays readable (gap 18).
* Cross-spec context: `--baseline`/`--context <files>` loads consumer models so impact counts external
  consumers instead of undercounting (gap 17). Keep it opt-in; default behaviour unchanged.

### WP 14 — Best-practice lint pass (single model, not a diff)
* New package `src/nodeset_linker/lint/` with `LintReport` reusing `validator.Finding` (code, message)
  plus a `severity` field; wire into the existing `validate` CLI command as `--best-practices`.
* Checks (paper clause → code):
  * §2.1 PascalCase for BrowseNames; §2.1 `Type`/`VariableType` suffix for ObjectTypes/VariableTypes;
    `DataType` suffix for structured types; no suffix or `Enum` for enumerations; verb/`Has` prefix for
    ReferenceTypes; InverseName only for asymmetric types.
  * §2.2 InstanceDeclaration BrowsePath unique case-insensitively (`Id` vs `ID` collision).
  * §2.1.4 same BrowseName string in more than one namespace → ADVISORY (this also degrades
    `browseName=` root selectors and impact labels).
  * §2.3 special characters require an optional `SymbolicName`.
  * §2.4 filename convention `Opc.Ua.<ShortName>.NodeSet2.xml` (importer-side warning).
  * §2.5 NamespaceUri template (OPC Foundation vs vendor) — supports WP 10.
  * §4 no StatusCode nodes in a non-OPC-Foundation namespace.
  * §7.9 ≤32 optional fields per structure; `AllowSubTypes` must not be combined with optional fields.
  * §7.10 Mandatory/MandatoryPlaceholder not allowed on self-contained InstanceDeclarations
    (recursion) — reuse the closure's visited set to detect self-containment.
  * §13.2/§13.3 `Description`/`DisplayName` text must not contain `NamespaceIndex:` prefixes — regex
    `(?<![\w])\d+:[A-Za-z]` over text attributes; also relevant after export, because export drops XML
    indexes but never rewrites text.
  * §15 subtypes of audit-event types must not add fields.
* Lint findings are never `BREAKING` unless the model is structurally invalid; they are ADVISORY/INFO.

### WP 15 — Waivers and configuration
* `--waive <rule-id>` (repeatable) and `--waiver-file <path>`: list of `{rule, namespace?, node?, reason}`.
* Only INFO/ADVISORY waivable; BREAKING needs `--accept-breaking` (explicit acknowledgement, printed in
  the report header so a PR reviewer sees it).
* Store waivered findings in JSON (`"waived": true, "waived_by": ...`) so CI dashboards can group.

## 3. Sequencing and commit sizes

1. WP 1 alone (foundation, no behaviour change beyond severity plumbing).
2. WP 2 + WP 3 + WP 4 + WP 5 (classification correctness; these change gate outcomes → mark as a
   breaking behavioural change in the changelog and README "breaking rules" section).
3. WP 6 + WP 7 (attributes/methods).
4. WP 8 (hierarchy/graph-aware rules).
5. WP 9 + WP 10 (metadata + release verdict) — biggest new-surface commit.
6. WP 11 + WP 12 (deprecation, CU/Profiles).
7. WP 13 (impact identifiers) — touches JSON contract, ship together with a contract version note.
8. WP 14 (lint) — additive CLI surface, no diff behaviour change.
9. WP 15 (waivers) — after severities exist and the noise level is known.

## 4. Test plan

* Extend `tests/test_diff.py` with one test per rule id (synthetic models are cheap; follow the
  existing helper style) asserting `severity`, `rule`, and `breaking`.
* New `tests/test_best_practices_rules.py` for WP 8/10/11/12 (hierarchy, release verdict, deprecation, CU).
* New `tests/test_lint.py` for WP 14.
* Fixture updates: `examples/demo3` (intentional incompatibility) and `examples/demo4` (CI gate demo)
  should each exercise at least one previously-missed rule: identifier move, optional declaration
  removal, enum value addition, metadata-only change.
* Regression risk to test explicitly: diffing a **reduced** export against its source must not produce
  `BP-NODE-REMOVED` noise for nodes the linker intentionally externalized (WP 4 carve-out), and must
  not report `BP-ID-MOVED` when renumbering is the expected consequence of reduction — decide whether
  reduced exports are exempt (recommended: exempt when `ServerUri`/namespace came from the linker,
  detectable via the linker's own metadata) and encode that as an option, not as silent magic.
* JSON contract: add a golden-file test so accidental key removal is caught.

## 5. Documentation and cross-project updates (do them with the code, not later)

* `README.md` "Semantic diff" section: replace the flat breaking-rule list with severity buckets + rule ids.
* `docs/semantic-diff.md`: update §5 gap list (gaps 4, 5, 8, 10, 12-16 become "fixed"), §7/§8 roadmap
  table priorities, §4 JSON contract (new keys), §2.5 breaking list description.
* `docs/best-practices-alignment.md`: flip verdicts from `looser`/`stricter`/`missing` to `aligned`
  as each WP lands; keep the paper quotes so future readers can re-check against a newer paper version.
* `docs/ui-concept.md` §3.7 (diff screen) and §12 (mockup gaps): the preview tier must mirror the same
  rule ids and severities in JS; the gate line shows the release verdict (§10 of this plan) and groups
  findings by rule id. German surface wording: Breaking / Advisory / Information →
  "Brechend / Hinweis / Information".
* `docs/usage-guide.md` §6: add the `--baseline` / `--release-verdict` workflow.

## 6. Open decisions to settle during implementation

1. **Reduced exports vs. release models.** The diff is used on linker output as well as on spec
   releases. `BP-ID-MOVED` and `BP-NODE-REMOVED` are release rules, not export rules. Decide the
   carve-out mechanism (flag `--release` / metadata detection) before WP 2 and WP 4 ship, otherwise
   the tool becomes unusable on its own output.
2. **Severity of §3.2.16 removals.** Paper forbids every removal; making all removals BREAKING is
   correct but noisy for optional declarations. Keep BREAKING + waiver, and report the paper remedy
   ("keep the node; servers may omit it").
3. **Field/method rename detection** is heuristic. Keep it ADVISORY if confidence is low, BREAKING only
   when pairing is unambiguous (same index, same class, one removal + one addition).
4. **Baseline registry format.** JSON vs. reuse of the split-export `manifest.json` (see
   `docs/usage-guide.md`). Reusing the manifest is preferable — it already lists namespaces.
5. **OPC 11030 version pinning.** Record the paper version in the rule registry metadata
   (`clause: "OPC-11030 1.03.03 §3.2.5"`) so a newer paper can be re-compared without re-deriving rules.
6. **UA base namespace handling** in WP 4 and WP 14 (companion specs re-declare base nodes for
   context; lint must not punish that).

## 7. Verification checklist before closing the alignment

* `nodeset-linker diff old.xml new.xml --fail-on-breaking` fails on: identifier move, enum value
  addition, optional declaration removal, structure field addition, method signature change,
  `Symmetric` change, hierarchy move.
* It stays green on: optional declaration addition, DisplayName/Description edits, SymbolicName edit,
  `AccessLevel`/`Historizing` edits, OptionSet text fixes, subtype additions, Interface addition without
  new mandatory declarations, metadata-only version bumps (advisory/info only).
* `--release-verdict` reports "not reusable — publish `V2/`" for a same-namespace breaking change and
  "reusable" for a compatible extension.
* JSON consumers still parse: all previous keys present, new keys additive.
* `docs/best-practices-alignment.md` verdicts match the implemented behaviour (spot-check §2 and §3
  tables after each WP).
