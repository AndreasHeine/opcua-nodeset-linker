# Semantic Diff — Extended Documentation, Gaps, and Roadmap

This document describes the current `diff` functionality in detail, analyses its gaps and
limitations — especially what happens as the number of companion specifications grows and new
specification releases arrive — and proposes improvements and future features for the Linker.

Reference implementation: `src/nodeset_linker/diff/` (`engine.py`, `model.py`, `impact.py`,
`render.py`), CLI command `diff` in `src/nodeset_linker/cli/main.py`.

---

## 1. What the semantic diff is

`nodeset-linker diff old.xml new.xml` answers the question *"what changed in the information
model?"* — not *"what changed in the XML file"*. Both inputs are parsed into
`SemanticModel` objects (repository layer) and compared on **semantic identity**; an input is a
NodeSet file or a split-export bundle (a directory with `manifest.json`), whose namespace files
are merged into the one model they stand for:

```
Identity = (namespaceUri, identifier, serverUri)
```

`serverUri` is the third part because OPC 10000-6 makes it part of the node's identity: an
identifier qualified with a `ServerUri` points into *another server's address space*. An
unqualified identifier is stored as `serverUri=None`, which is what a document written for its
own server produces (`srv=0`), so re-exporting a file never invents a difference.

Consequences of this design (tests in `tests/test_diff.py`, `TestIdentityNotXml`):

* reordered nodes, whitespace, comments: **no change**;
* a reassigned `NamespaceIndex` (e.g. Machinery moves from index 1 to 2 behind a filler
  namespace): **no change**;
* `DataType="String"` vs `DataType="i=12"`: **no change** — datatype attributes are compared
  through resolved identifiers, not raw XML spellings;
* a node whose identifier changed while namespace, server, BrowseName and NodeClass stayed equal
  is reported as a **renumbered node**, not as an addition plus a removal;
* a node that gains, loses or changes a `ServerUri`: **a removal plus an addition** — it is a
  different node, not the same one spelled differently.

Nothing in `diff/` looks at XML. The module sits above `repository/` and `graph/`; it consumes
`SemanticModel` and `SemanticGraph` and never re-parses documents.

## 2. How the engine works (`diff/engine.py`)

### 2.1 Indexing

`ModelIndex.of(model)` builds a dictionary keyed by `(namespaceUri, identifier, serverUri)` —
the same triple `ExpandedNodeId` compares on, so a model that contains both `srv=1;ns=1;i=5` and
`srv=2;ns=1;i=5` keeps both nodes instead of letting the last one win.

### 2.2 Set algebra and renumbering pairing

Three sets are computed: `gone` (old minus new), `fresh` (new minus old), `shared`
(intersection). Then `_pair_renumbering()` groups `gone` and `fresh` by the *move key*
`(namespace, serverUri, BrowseName, NodeClass)` and pairs members of matching groups positionally
(`zip`, sorted by identifier). A paired node becomes a `RenumberedNode` carrying both
identifiers plus its own content changes; unpaired members stay as plain removals/additions.

The pairing also yields a moved-identifier map (`old identifier -> new identifier`) that every
later comparison resolves through (`_resolve`, `_resolved_key`, `_same_target`): a reference, a
base type, an interface set or a method declaration that points at a renumbered target is the
same target. A move is therefore reported once, on the node itself, and does not also read as a
detached child, a hierarchy move, a datatype change or a method signature change. Removed-side
labels are mapped back to the old identifier so the report still names what the consumer knew.

### 2.3 Per-node comparison (`_node_changes`)

For every shared (or paired renumbered) node the engine compares, in order:

| Area | Compared | Severity when changed |
| --- | --- | --- |
| node | `NodeClass` | breaking (`BP-NodeClass-Immutable`, §3.2.17) |
| node | `BrowseName` (namespace-qualified) | breaking (`BP-BrowseName-Immutable`, §3.2.17) |
| node | `DisplayName`, `Description`, `Documentation` | information — text may change, its meaning may not (§3.2.17) |
| node | `SymbolicName` | advisory (`BP-SymbolicName-Identity`, §2.1.2): generated code keeps the old symbol |
| node | `InverseName` | advisory (`BP-InverseName-Meaning`, §3.2.17) |
| node | `Categories` | information (`BP-Categories-Metadata`, §3.2.17) |
| node | residual semantic XML attributes (`_semantic_attributes`, i.e. everything except the document-local identity spellings in `DOCUMENT_LOCAL_ATTRIBUTES`) | information (`BP-Attributes-ServerSpecific`, §3.2.17) |
| node | `Symmetric` of a ReferenceType | breaking (`BP-ReferenceType-Symmetric-Immutable`, §3.2.14) — judged on its own, never as an attribute edit |
| node | `IsAbstract` | breaking only when it becomes `true` (`BP-IsAbstract-Narrowing`); information when a type becomes concrete |
| node | Modelling rule (`Mandatory`/`Optional`/`MandatoryPlaceholder`/`OptionalPlaceholder`) | breaking only when Optional → Mandatory (`BP-ModellingRule-Tightening`, §3.2.1); information the other way |
| node | Variable/VariableType `DataType`, `ValueRank`, `ArrayDimensions` | breaking (§3.2.12) |
| node | serialized `<Value>` (whitespace-normalized) | information (`BP-Value-InstanceData`, §3.2.17): instance data is not the contract |
| node | `RolePermissions` | information (`BP-RolePermissions-Changed`): which role may access the node changed, the node itself stays (§3.2.17) |
| node | `<Extensions>` content, normalized per extension so XML formatting is not payload | advisory (`BP-Extensions-Changed`): the payload travels with the node, so consumers reading it must be told (§3.2.17) |
| node | identifiers a serialized `<Value>` points at (`value_dependencies`, resolved while parsing) | advisory (`BP-ValueDependency-Changed`): the value now means another node (§3.2.17, §3.2.12) |
| definition | Structure fields, Enum values, Union alternatives, OptionSet values | see the list below |
| reference | added/removed `<Reference>` keyed by `(reference-type identity, target identity, direction)`, counted by multiplicity | by category, see `_reference_category` below |

Definition diffs (`_definition_changes`):

* **Structure**: fields keyed by name — an added field, a removed field (an `IsOptional` one
  too) and a retyped field are all breaking (§3.2.5, §3.2.16); a renamed field is breaking
  (`BP-Field-Renamed`), and a pairing that is only a guess is advisory
  (`BP-Field-Rename-Guess`, §2.2); an `IsOptional` that changed is breaking (§3.2.5: the
  encoded field list changes); field metadata (`SymbolicName`, `ValueRank`, `ArrayDimensions`)
  is breaking, while `AllowSubTypes` and `MaxStringLength` are judged by direction — turning
  `AllowSubTypes` off or shrinking a limit rejects values a consumer already sent (§3.2.5), the
  other direction only widens and is information; the **order** of the common fields is
  advisory (`BP-FieldOrder-Encoding`, §2.2): field names are the contract, a positional XML
  encoder reads by position; two members carrying one name are advisory (`BP-Duplicate-Member`).
  Text on a member (`DisplayName`, `Description`) is information (`BP-Member-Text`, §3.2.17).
* **Union**: alternatives keyed by name — added, removed or retyped are breaking (§3.2.6);
  re-ordered alternatives are advisory (`BP-AlternativeOrder-Encoding`, §3.2.6).
* **Enum / OptionSet**: values keyed by name — an added value, a removed value and a changed
  number are all breaking (§3.2.4, §3.2.7, §3.2.8): a consumer holding the old set cannot
  decode the new one.
* Definition kind changed (e.g. Structure → Enum) or definition dropped: breaking (§3.2.17).

Reference severity (`_reference_category`, judged by `verdict`):

* `Organizes`, `HasDescription` → navigation only: information in either direction.
* `HasSubtype`, `HasTypeDefinition`, `HasInterface`, `HasEncoding`, `GeneratesEvent` →
  contract references: removing one breaks (§3.2.16, §3.2.14), adding one is information.
* ownership references (`HasComponent`, `HasProperty`, …) → breaking when the *target* is an
  explicitly declared `Mandatory`/`MandatoryPlaceholder` declaration, in either direction
  (§3.2.1, §11.2.1, §3.2.16); an Optional target is information when added and breaking when
  removed (§3.2.16). OPC UA namespace targets are never read as mandatory here.
* `HasArgumentDescription`, `HasOptionalInputArgumentDescription` → documentation about an
  argument (§5): information in both directions, because the call contract stays as it was.
* `IsDeprecated` → judged by the deprecation rules (§14.2); members of a ConformanceUnit or
  Profile by the conformance rules (§12.1, §12.2).
* any other/unknown reference name → breaking by default.
Removal of a whole node is breaking when the node is a type, an encoding, a placeholder, or
an explicitly mandatory node (`_removal_base_verdict`). `_is_explicitly_mandatory` intentionally
never treats OPC-UA-namespace nodes as mandatory, because Part 5 declares no modelling rules
there.

`--reduced-export` carves out a Linker export (§11.2): when the new file declares a namespace
as a `RequiredModel` but ships no nodes of it, the nodes that left are reported as
`BP-Declaration-Externalized` information instead of as removals. The option is explicit, not
a guess — a namespace the export neither ships nor declares is still a real removal (§3.2.16).

### 2.4 Namespace totals and release metadata

`NamespaceChange` reports per namespace URI: node count, reference count, datatype count,
old → new with delta, plus the release metadata of that namespace — `NamespaceUri`, alias,
`Version`, `PublicationDate`, `XmlSchemaUri`, `ModelVersion`, and the `RequiredModel` set with
the version each dependency claims. A namespace is listed when its totals changed **or** when
its metadata changed, so a release that only bumped a version still shows up. The entry also
counts the nodes that carry an `IsDeprecated` reference (§14.2): a release that deprecates
inside a NamespaceUri it keeps is advised, because that URI can never drop those nodes again
(§14.3).

Version claims are compared on their **numeric core**, not on their spelling. `Version` is a
display string — Annex F.2 of OPC 10000-6 states it is "not intended for programmatic
comparisons" — while `ModelVersion` is the `SemanticVersionString` consumers do compare (§12.41),
so a companion spec spells one release twice: `Version="1.05.03"` beside `ModelVersion="1.5.3"`.
A re-spelled release is therefore not a release move. Only two claims that describe *different*
releases are reported, as `BP-Namespace-VersionDrift` (advisory, §11.1): it reads as a note to
review by hand and never gates a release with `--fail-on-breaking`. A `ModelVersion` that is not
a valid `SemanticVersionString` — leading zeros, missing segments — is a separate advisory,
`BP-Namespace-ModelVersionSyntax` (§12.41), because it is the field the comparison depends on.
A `RequiredModel` claim is read through its `ModelVersion`, falling back to the display `Version`
only where a document states no SemVer (Annex F.2 states a minimum version as SemVer). Values
nobody can parse stay compared as the strings they are.

Namespace findings stay on the namespace entry, not in the flat breaking list (§2.5): they
describe a release, not a node, and there is no node id to attribute them to.

### 2.5 Breaking list

`_breaking_changes()` flattens every breaking `Change` and every breaking removal into a
flat `BreakingChange` list with a human reason string; `NodeModification.breaking` and
`RenumberedNode.breaking` are derived from their changes.

`apply_waivers(diff, waivers, accept_breaking=False)` (`diff/waivers.py`) runs after impact and
**never removes a finding**: it rebuilds the verdicts with `waived=True` and a `waived_by` string,
and the report keeps severity, rule id and a `[waived]` marker. Only the gate consults the markers
(`SemanticDiff.blocking_breaking()`, `blocking_advisory()` and their namespace counterparts). INFO
and ADVISORY findings can be waived by rule, by namespace (URI or alias) or by node (BrowseName or
identifier); a BREAKING finding cannot be waived, and `--accept-breaking` marks every breaking
finding instead. The release verdict keeps reading `breaking`, so acknowledging a break never makes
a published `NamespaceUri` reusable (§11.3).

### 2.6 Impact (`diff/impact.py`)

`apply_impact(diff, old_model, new_model, *, context=(), inheritance_depth=3)` builds a
`SemanticGraph` from **both** models and walks the *reverse* transitive closure over dependency
edges, excluding `DependencyKind.NAMESPACE` edges (`Organizes`, `ParentNodeId`) — folders are
browsing structure, not a dependency:

* removed nodes: measured in the **old** model (their consumers still exist there);
* modified/renumbered nodes: measured in the **new** model (the contract consumers must live
  with); renumbered additionally unions the old-model dependents;
* `context` adds consumer models — a sibling companion spec, or the spec whose nodes this release
  externalized — so consumers living outside the two compared files are counted, not skipped;
* reverse `DependencyKind.INHERITANCE` edges are followed for at most `inheritance_depth` hops
  (`INHERITANCE_DEPTH_LIMIT = 3`), so removing a base type reports the near generations instead
  of the whole subtype forest; other dependency kinds are never depth-limited;
* each breaking entry gets an `affected` count (graph chosen by node existence).

Impact is reported as **identifiers**: `entry.impact_ids` holds `ExpandedNodeId`s sorted by their
XML form, which is what `impacted(model, node_id)` returns. `entry.impact` keeps the node *labels*
(BrowseName/DisplayName strings, deduplicated and sorted) for human reports; a label alone cannot
be resolved, because the same BrowseName in two namespaces is two nodes. Phantom vertices (targets
not in any loaded graph) are still skipped as *sources*, and `total_impact(diff)` counts the
distinct impacted **identifiers**.

### 2.7 Rendering (`diff/render.py`)

Console, Markdown and HTML are generated from one shared `Section`/`Entry` structure, and
`Change.describe()` is the single place the +/- lines are produced — so text, Markdown, HTML
and JSON can never disagree. JSON (`diff/model.py::json_text`) is the machine contract for
CI. A run that used waiver flags inserts a *Waivers* section right after the summary: the
`--accept-breaking` acknowledgement, how many findings were waived, and the waivers that were
ignored because they named a breaking rule.

HTML additionally folds the node sections (`Added`, `Removed`, `Modified`, `Renumbered`) into one
collapsed `<details>` block per NamespaceUri, carrying the node count, and only when more than one
namespace is involved. Console and Markdown stay flat. A report is not "empty" just because no
node changed: advisory findings — a merge conflict, for instance — still produce a report, while
`--no-advisory` hides them, including the advisory rule a namespace entry cites; that entry's
totals and metadata deltas stay, because they are facts rather than findings.

## 3. CLI surface

```
nodeset-linker diff old.xml new.xml [--json | --markdown | --html] [--impact]
                    [--impact-limit N] [--context FILE ...]
                    [--breaking] [--advisory/--no-advisory]
                    [--namespace URI_OR_ALIAS ...] [--exclude-namespace URI_OR_ALIAS ...]
                    [--node-class CLASS ...]
                    [--fail-on-breaking] [--fail-on-advisory] [--waive RULE_ID ...]
                    [--waiver-file FILE] [--accept-breaking]
                    [--release-verdict] [--baseline DIR|manifest.json] [-o FILE]
```

* exactly **two inputs**; each is a NodeSet file, or a split-export **bundle** — a directory
  holding `manifest.json`, or that manifest itself — whose namespace files are merged into one
  `SemanticModel` (`_bundle_files` + `load_nodesets(..., tolerate_duplicates=True)`);
* an inconsistent re-declaration inside one side does not abort the run: the first declaration is
  kept and the diff reports an advisory `BP-Merge-Conflict` naming both documents (§3.2.17);
* impact is computed whenever `--impact`, `--breaking`, `--fail-on-breaking` or `--context` is
  given;
* `--impact-limit N` (default 50) caps how many impacted nodes an entry *lists*; the reported
  count stays exact, and a truncated list says how many more there are;
* `--context FILE` is opt-in and repeatable: each consumer model is loaded and its dependency
  edges join the impact walk, so externalized specs and sibling companion specs count instead of
  being silently undercounted. Without it, behaviour is exactly as before.
* `--namespace` / `--exclude-namespace` (repeatable, URI or alias) and `--node-class` (repeatable)
  scope *both* models, so a filtered namespace or class is never reported — and the release
  metadata of a filtered namespace is filtered with it (§11.1). An unknown node class is a usage
  error that exits 1, and so does a `--namespace` neither side declares — filtering both models
  to nothing would print "No semantic differences found." and exit 0; an `--exclude-namespace`
  that matches nothing drops nothing and is noted on stderr;
* `--fail-on-breaking` exits 1 on a BREAKING finding only; `--fail-on-advisory` is a separate,
  opt-in gate; advisory and info findings never gate by default; missing files, a directory
  without a manifest, a manifest naming files that are not there and parse errors exit 1;
* `--waive RULE_ID` (repeatable) and `--waiver-file FILE` mark INFO/ADVISORY findings as waived —
  the file holds `{rule, namespace?, node?, reason}` entries, a JSON array or `{"waivers": [...]}`,
  and a bad file is a usage error that exits 1. A waiver naming a breaking rule is reported as
  ignored and changes nothing; `--accept-breaking` marks every breaking finding, prints the
  acknowledgement in the report header and opens `--fail-on-breaking`. The report and the JSON still
  list every finding; only the gate reads the markers, and the release verdict is unaffected.
* `--release-verdict` answers "may each NamespaceUri be reused?" (§3.3, §11.3) and exits 1 when
  a published URI carries breaking findings; `--baseline` is a directory or `manifest.json`
  listing the published URIs — without it the verdict reports "baseline unknown" instead of
  guessing, and gating stays `--fail-on-breaking`'s job;
* format flags are exclusive in practice (`--json` wins over `--markdown`/`--html`);
* `-o` writes the report, creating the parent directory when it does not exist yet.

The CI gate documented in README/usage guide:

```yaml
- run: nodeset-linker diff old.xml new.xml --json --fail-on-breaking
- run: nodeset-linker diff old.xml new.xml --release-verdict --baseline dist/split/
```

## 4. JSON contract

```json
{
  "schema_version": "1.0",
  "old": "…", "new": "…",
  "summary": {"old_nodes": …, "new_nodes": …, "added_nodes": …, "removed_nodes": …,
              "modified_nodes": …, "renumbered_nodes": …, "breaking_changes": …,
              "advisory_changes": …, "advisory_nodes": …, "info_nodes": …,
              "namespace_changes": …, "identical": false, "waived_changes": …},
  "added_nodes": [ { "label", "node_id", "namespace", "namespace_uri", "node_class", "breaking",
                     "severity", "rule_id", "impacted_nodes", "impacted_labels", "impact_count" } ],
  "removed_nodes": [ …same… ],
  "modified_nodes": [ { …, "changes": [ {"area","kind","subject","old","new","breaking","text",
                                        "severity","rule_id","rationale","clause"} ] } ],
  "renumbered_nodes": [ { …, "old_node_id", "new_node_id", … } ],
  "namespace_changes": [ { "namespace","uri","nodes":{"old","new","delta"}, "severity",
                           "rule_id","verdicts":[ … ],
                           "metadata":[ {"attribute","old","new","severity","rule_id",
                                         "rationale","clause","note"} ] } ],
  "breaking_changes": [ { "label","node_id","reason","affected_nodes","breaking":true } ],
  "release": { "reusable", "baseline", "recommendation",
               "namespaces": [ {"uri","reusable","bumped","moved_from","published",
                                "recommendation","breaking","verdicts","metadata"} ] },
  "waivers": { "accepted_breaking": …, "waived_changes": …, "ignored": [ … ] }
}
```

Keys are added, never renamed or dropped: the boolean `breaking` stays for existing consumers,
while severity comes as `severity`/`rule_id`/`rationale`/`clause` per change plus a `verdicts`
list per entry; `paper` names the whitepaper the severities derive from, and `advisory_changes`
mirrors `breaking_changes` for findings that never gate. `namespace` is the document-local alias
while `namespace_uri` is the URI the node lives in (§11.1). `release` appears **only** when the
caller asked for a verdict (`--release-verdict` or `--baseline`). A finding a waiver or
`--accept-breaking` marked carries `"waived": true` and `"waived_by"` — on the change, the entry
verdict, the namespace metadata delta and the breaking entry — and `waived_changes` counts them;
the severity itself never changes, so a waived breaking finding still reads `severity: "breaking"`.
The top-level `waivers` block appears **only** when `--waive`, `--waiver-file` or
`--accept-breaking` was used, and `ignored` lists waivers that named a breaking rule, which cannot
be waived. `schema_version` states which shape the payload is: keys are **added** only, so a
consumer that does not know a key ignores it, and a CI job can pin the version it reads. The
split-export `manifest.json` carries its own `schema_version` for the same reason.
`impacted_nodes` and `impacted_labels` only appear after impact was applied.
`impacted_nodes` lists XML identifiers (`nsu=http://opcfoundation.org/UA/Machinery/;i=2200`) and
`impacted_labels` the human names; both are truncated to `impact_limit`, while `impact_count` is
always the exact number of distinct impacted identifiers.

---

## 5. Gaps and limitations of the current implementation

Gaps that the OPC UA best-practices whitepaper (OPC 11030) also asserts — forbidden additions,
removal of optional nodes, namespace versioning, deprecation — are mapped clause by clause in
[`OPC-11030-v1.03.03.md`](OPC-11030-v1.03.03.md) and closed by
[`best-practices-implementation-plan.md`](best-practices-implementation-plan.md); a finding carries
the `clause` it comes from, so the mapping lives in the report rather than in a separate document.

### 5.1 Identity-level gaps

1. **`ServerUri` is dropped from diff identity** *(closed)*. `identity()` keys by
   `(namespaceUri, identifier, serverUri)`, so a node that gains, loses or changes a
   `ServerUri` reads as a removal plus an addition, and the renumbering pairing no longer pairs
   across servers.
2. **Server-qualified nodes can collide inside one model** *(closed)*. `ModelIndex.nodes` is
   keyed by the full triple, so a model containing `srv=1;ns=1;i=5` and `srv=2;ns=1;i=5` keeps
   both nodes instead of letting the last one win.
3. **The renumbering heuristic pairs blindly** *(partly closed)*. Grouping by
   `(namespace, serverUri, BrowseName, NodeClass)` and `zip`-pairing sorted identifiers still
   mis-pairs a genuine removal plus an unrelated new node that happens to share namespace,
   BrowseName and class (typical when a companion spec replaces a type while adding a sibling).
   What is closed: a node *renamed* **and** renumbered is no longer an unrelated removal plus
   addition — `_pair_renames` pairs the pair that differs only in BrowseName, and only when
   exactly one candidate matches, so an ambiguous group stays a removal plus an addition rather
   than a guess. Every pairing states how sure it is: `confidence: "exact"` only when one
   removed and one added node share the key, otherwise `"heuristic"` — the report says "paired
   by guess" and JSON carries `confidence`, so a reader can check a guess by hand.
4. **Renumbering is never breaking** *(closed)*. `_pair_renumbering` states the move itself as an
   entry verdict (`identifier_move` → `BP-NodeId-Immutable`, §3.2.17), so a release that
   renumbers NodeIds fails `--fail-on-breaking` with no extra flag — the change every external
   consumer, generated client and companion-spec cross-reference pointing at the *old* NodeId
   breaks on. Content changes ride along on the same entry, and references, hierarchy and
   datatype targets resolve through the paired move instead of reading as detached children.
5. **Reference additions are never breaking** *(closed)*. `_reference_changes` judges an addition
   by its category, exactly like a removal: attaching an explicitly `Mandatory` component is
   breaking (`BP-Reference-MandatoryChild-Added`, §3.2.1, §11.2.1), while an addition that only
   widens the model is information (§3.2.2) — a contract reference (`HasTypeDefinition`,
   `HasEncoding`, `GeneratesEvent`) or a navigation one (`Organizes`, `HasDescription`).
6. **Reference identity uses the type *name*** *(closed)*. `_reference_key` keys by the
   identifier of the reference type (`_identity_text(ref.reference_type)`), falling back to the
   name only when the document gives no identifier. `HasEncoding` and `i=38` are therefore one
   reference, and a renamed reference type declaration is reported on that declaration
   (`BP-BrowseName-Immutable`) instead of as a removal plus an addition on every node that
   points at it. The name is still what the report shows; when a document spells only an
   identifier, the declaration supplies the name.
7. **Reference multiplicity is invisible** *(closed)*. Keys go into a `Counter`, so a reference
   declared twice in the old model and once in the new one is reported, with the count in the
   subject (`HasComponent -> TaggedMember (2 -> 1)`).

### 5.2 Content-level gaps

8. **`<Extensions>` and RolePermissions are not compared** *(closed)*. `_node_changes` reads
   both: `RolePermissions` as information (`BP-RolePermissions-Changed` — the access model
   changed, the node itself stays) and `<Extensions>` content as advisory
   (`BP-Extensions-Changed` — the payload travels with the node, so consumers reading it must be
   told, §3.2.17). Extension content is normalized per extension, so indentation between tags is
   spelling, not payload.
9. **`<Value>` is compared as whitespace-normalized text only** *(partly closed)*. What is
   closed: the identifiers a value points at — `TypeId` / `DataType` with an `<Identifier>`
   inside an ExtensionObject — are extracted while parsing, an unresolvable spelling is a
   `ParseError` instead of a silently dropped dependency, and they are diffed semantically as
   `ValueDependencies` (`BP-ValueDependency-Changed`, advisory, §3.2.17, §3.2.12): the value now
   means another node. Encoding-relevant edits are breaking where they are contract — an enum or
   OptionSet number, a Structure field type, a Union alternative (§3.2.4 - §3.2.8). What stays:
   the raw text of a `<Value>` is compared whitespace-normalized and reads as instance data
   (`BP-Value-InstanceData`, §3.2.17), which is the paper's stance for instance values; nothing
   yet checks a value against the `DataType` of its node (gap 11).
10. **Method signatures had no dedicated diff** *(closed)*. `_method_changes` states one
    `BP-MethodSignature-Immutable` break per method when argument count, order, `DataType`,
    `ValueRank`, `ArrayDimensions`, the spelled argument list or `MethodDeclarationId` move
    (§3.2.11); the argument variable stays silent for those, so the same break is not stated
    twice. Text on an argument and a `HasArgumentDescription` reference that appears or
    disappears are information (§5): they document an argument, they do not change what a
    caller passes.
11. **No value-vs-datatype consistency check.** A variable whose declared `DataType` changes
    while its `<Value>` still carries the old type (demo3's intentional incompatibility) is
    reported as a datatype change, but nothing flags the value/type mismatch itself.

### 5.3 Metadata-level gaps (release-relevant)

12. **Model/namespace version metadata is reported.** `NamespaceChange` carries the release
    metadata of a namespace next to its totals: `Version`, `PublicationDate`, `XmlSchemaUri`,
    `ModelVersion` and alias (OPC 11030 §11.1, §11.2). A metadata-only release move — a version
    bump with no node change — now appears as an *information* entry, so a release note can name
    the fact without gating the release. A changed `NamespaceUri` is breaking (§11.3): consumers
    resolve the model under the old URI and find nothing. Version claims are compared on their
    numeric core (§2.4): a padded display `Version` beside a SemVer `ModelVersion` is one release,
    not a contradiction (OPC 10000-6 Annex F.2, §12.41), so `BP-Namespace-VersionDrift` only fires
    on two claims that name different releases, and an invalid `ModelVersion` is reported as
    `BP-Namespace-ModelVersionSyntax`. What stays open: a namespace that states no `ModelVersion`
    at all is not judged, although Annex F.2 requires one for `UANodeSets` conforming to spec
    1.05.03 or later.
13. **Required-model dependencies and aliases are visible.** The `RequiredModel` set and the
    version each dependency claims are compared per namespace, and an alias change is reported
    as document-local information (namespaces are addressed by URI, not by alias). A namespace
    that vanishes while another appears under the same alias with identical node totals is read
    as a *moved* URI; a namespace under a genuinely new URI — the recommended `…/V2/` spelling —
    stays an added/removed pair and is never read as a rename.
14. **Namespace metadata conflicts across documents are reported.** `_merge_namespace` still
    keeps the richest value per field, but the diff now reports an advisory naming both
    declarations when two loaded documents disagree on namespace metadata, or when a namespace
    carries `Model` entries in more than one document — the merge keeps one spelling, so the
    published file can state a version no document claimed on its own (§11.2).
15. **Duplicate detection is only partial** *(partly closed)*. `SemanticModel.add` tolerates
    "same semantics" re-declarations based on `node_class`, `browse_name`, `is_abstract` and
    `HasSubtype` targets only, so two companion specs that re-declare a shared UA base node with
    different descriptions or datatype fields still merge into one declaration. What is closed: a
    diff no longer dies on the conflict. `load_nodesets(..., tolerate_duplicates=True)` — the form
    the diff uses — keeps the first declaration, records a `MergeConflict` naming both documents,
    and the report states it as `BP-Merge-Conflict` (§3.2.17). A reduction keeps raising
    `DuplicateNodeError`, because exporting a model that dropped a declaration publishes a contract
    nobody declared.

### 5.4 Impact-level gaps

16. **Impact reported labels, not identifiers** *(closed)*. `_dependents` now returns
    `ExpandedNodeId`s, `entry.impact_ids` holds them and `total_impact` counts distinct
    identifiers; labels remain available as `impact`/`impacted_labels` for reports only, so the
    same BrowseName in two namespaces is no longer collapsed.
17. **Impact excluded externalized and cross-spec consumers** *(closed)*. `apply_impact` and
    `--context` take consumer models — externalized Core, DI, a sibling companion spec — and walk
    their graphs too, so those consumers are counted instead of undercounted. It stays opt-in:
    without `--context` the walk is scoped to the two loaded files, as before. Phantom vertices are
    still not treated as impacted *nodes*, because a reference target nobody declares is not a
    consumer.
18. **Impact direction was uniform** *(closed)*. Reverse `DependencyKind.INHERITANCE` edges are
    now followed for at most `INHERITANCE_DEPTH_LIMIT` (3) hops, so a base-type removal reports the
    near generations rather than the whole subtype forest. Component and interface chains are still
    followed fully: they are real consumers, not generations.
19. **Impact is expensive.** `SemanticGraph.from_model()` is built twice per run, and a BFS is
    run per changed node/entry; nothing is cached or shared across entries. `--context` adds one
    graph per consumer file, so a wide fan-out run pays for every consumer model it loads.

### 5.5 Classification gaps

20. **Breaking rules were hardcoded and binary** *(closed)*. Findings carry a severity
    (BREAKING / ADVISORY / INFO) and a rule id; `--waive`, `--waiver-file` and `--accept-breaking`
    are the waiver mechanism (§2.5). Severity is judged per rule with context, so an enum value
    addition or a custom reference removal is classified by what the paper says, not by a fixed
    table. `CHANGE_LABELS` covers node-kind changes; definition `added/removed/changed` changes fall
    through `describe()`'s generic branch.
21. **The breaking verdict was computed but never justified per rule** *(closed)*. Every finding
    reports `rule_id`, `rationale` and `clause`, so a CI dashboard groups by rule and a waiver
    references a stable identifier. `BreakingChange.reason` is still the rendered text beside it.
22. **`--breaking` hides additions completely** *(closed)*. Additions can carry breaking verdicts
    now, and they reach the Breaking changes section, which `--breaking` keeps: a new
    ReferenceType placed inside the existing hierarchy (§3.2.15, `BP-RT-NewInHierarchy`), a
    Mandatory component attached to an existing parent (§3.2.1, §11.2.1), a supertype that does
    not carry what its subtypes already promised (§3.2.10), an Interface that brings new
    mandatory declarations (§3.2.3). What `--breaking` still leaves out is the *Added nodes*
    section itself, on purpose: an addition nothing can break on is exactly what the flag means
    by "not interesting", and the tightening is stated on the node it tightens rather than on the
    node that appeared.

### 5.6 Scaling with a growing number of companion specs

23. **Diff is two files only** *(closed)*. Each side is a NodeSet file **or a bundle**: a
    directory holding `manifest.json`, or that manifest itself. Its namespace files are loaded in
    manifest order into one model, so directory-vs-directory, bundle-vs-file and
    bundle-vs-bundle all compare the models they stand for; a manifest naming files that are not
    there is an explicit failure.
24. **Manual merging of two release trees can fail wholesale** *(closed)*. A diff loads its sides
    with `tolerate_duplicates=True`: an inconsistent re-declaration keeps the first declaration and
    becomes an advisory `BP-Merge-Conflict` finding naming both documents (§3.2.17), so loading
    more specs cannot make the comparison unusable. Reduction still aborts on a conflict by design.
25. **No per-spec/per-namespace scoping** *(partly closed)*. `--namespace`, `--exclude-namespace`
    and `--node-class` filter both models — a namespace or class left out is not reported, and a
    filtered namespace's release metadata is filtered with it (§11.1). A namespace filter also
    matches the declared `Alias` when it differs from the URI tail, and a `--namespace` neither
    side declares exits 1 instead of reporting nothing. Browse-name globs,
    `--ua-baseline off` and a drill-down of what changed inside a namespace whose totals moved are
    still open.
26. **Performance scales poorly.** Parsing both models is O(total nodes); impact is
    O(changed nodes × graph). For a registry-scale comparison (UA.xml ~5k nodes plus many
    companion specs, thousands of changed nodes between releases), the repeated graph builds
    and per-node BFS dominate runtime and memory; there is no incremental/cached mode.
    The quadratic hotspots inside the engine are gone: lint duplicate grouping, method
    argument lookup (a per-side index keyed by method identity) and namespace subset
    marking each make a single pass.  Remaining known gaps, *not* fixed here: the split
    export re-scans the shared model for every document, and the validator makes several
    full passes over the model instead of one grouped pass.
27. **Report output does not scale** *(partly closed)*. Impact lists truncate at
    `--impact-limit` names per entry (default 50) and say how many more there are; the flat
    breaking list grows linearly; there is no paging, no per-node drill-down and no CSV export.
    What is closed: the HTML report groups each node section into one collapsible
    `<details>` block per namespace (flat when only one namespace is involved), so a release
    spanning dozens of namespaces stays readable. Console and Markdown stay flat on purpose —
    a terminal and a PR comment have no fold to open.

### 5.7 Release-workflow gaps

28. **Baseline is publication-only, not a model snapshot.** A release verdict now reads a
    publication registry (`--baseline <dir|manifest.json>`), which the split export writes
    itself (§11.1), and answers whether each NamespaceUri may be reused (§3.3, §11.3). It does
    *not* store a previous model or fingerprint: every comparison still re-parses both full
    models, and there is no multi-release aggregation (v1.3 → v1.4 → v1.5) or history yet.
29. **Diff does not validate** *(partly closed)*. Neither side is semantically validated before
    comparison, so a malformed new model can still be diffed and read as identical. What is
    closed: parsing is strict about identifiers a document spells out — a `DataType`,
    `ParentNodeId`, `MethodDeclarationId` or an identifier inside a `<Value>` that neither the
    `Aliases` table nor a standard OPC UA name resolves is a `ParseError`, not an invented
    default, so a degraded model cannot silently compare equal. What stays open: a `<Reference>`
    target that names nothing remains an unresolved vertex, and no validator runs before the
    comparison (`--validate`, §7.4 item 19).
30. **JSON has no versioning** *(closed)*. The payload carries `"schema_version": "1.0"`, and
    keys are additive only — a consumer ignores keys it does not know and can pin the version.
    The split-export manifest got its own `schema_version` for the same reason. A published JSON
    schema for CI to validate against is still open.
31. **Label resolution is offline-limited.** Standard namespace-0 datatypes fall back to the
    built-in table (`STANDARD_DATA_TYPE_NAMES`); everything else shows bare identifiers when the
    provider model is externalized. For companion specs referencing many external models,
    reports degrade to identifiers with no hint that a provider could resolve them.

### 5.8 Minor correctness/usability nits

32. Sort keys compare identifiers as strings, so numeric ids sort lexically (`i=9` before
    `i=10`); ordering is stable but not intuitive for NodeId ranges.
33. `--json --impact` is supported, but `--json` alone never carries impact; there is no flag
    that says "JSON with impact" explicitly.
34. `-o` did not create parent directories *(closed)*: a report written to `dist/diff.html`
    creates the directory instead of failing late.
35. `total_impact` merged impact across *all* entries via label deduplication *(closed)*. Impact
    is held as `impact_ids` (`ExpandedNodeId`s) and `total_impact` deduplicates identifiers, so one
    BrowseName used in two namespaces is two impacted nodes again; labels stay report-only (§5.4
    gap 16), and every entry's `impact_count` stays exact regardless of what a report lists.

---

## 6. What changes as companion specs grow and new releases arrive

A concrete projection of the gaps above onto the realistic workflow (many specs, registry-wide
release comparison):

* **Noise grows with the number of namespaces** *(partly addressed)*: UA-namespace nodes are
  ~5k, so a registry-wide release diff still lists thousands of entries. `--namespace`,
  `--exclude-namespace` and `--node-class` cut the report down, and the HTML report folds each
  node section into one collapsible block per namespace — console and Markdown stay flat.
* **Impact stays exact but stays expensive** *(partly closed)*: impact is counted by identifier,
  `--context` loads the provider models of externalized specs so sibling consumers count instead of
  being undercounted, and reverse inheritance edges stop after 3 hops instead of walking the whole
  subtype forest. What grows with the input is still the walk itself — two graph builds plus a BFS
  per changed node (gaps 19, 26).
* **Renumbered releases now fail the CI gate** (gap 4 closed) — the move is reported once as
  `BP-NodeId-Immutable`, and references, hierarchy and datatype resolve through it, so the
  riskiest release change reads as one finding rather than as detached-child noise.
* **Version metadata moves now reach the report** (gap 12 closed), and `--release-verdict` says
  whether a NamespaceUri may be reused (gap 28 partly closed) — but only when a baseline states
  what was published; without one the report names the fact rather than judging the release.
* **One inconsistent declaration no longer aborts everything** (gap 24 closed) — the diff keeps
  the first declaration and reports the lost one as `BP-Merge-Conflict`, so loading more specs
  cannot make the comparison fail.
* **Runtime grows quadratically-ish** (gap 26) — impact per changed node over a growing graph.

## 7. Suggested improvements

### 7.1 Correctness (highest value first)

1. **Make identifier moves breaking by default** *(implemented)*: a paired move is breaking
   through `BP-NodeId-Immutable`, so a renumbered release fails the gate with no extra flag and
   no opt-in. A rename on the same identifier is a BrowseName change (§3.2.17); a rename *plus*
   a renumber is paired by `_pair_renames` and reported as one renamed node when exactly one
   candidate matches, and left as a removal plus an addition when the pairing would be a guess.
2. **Classify contract-tightening additions** *(implemented)*: attaching an explicitly Mandatory
   component is breaking (§3.2.1, §11.2.1), a new ReferenceType inside the existing hierarchy is
   breaking (§3.2.15), a supertype that promises less than its subtypes already did is breaking
   (§3.2.10), and an Interface that brings new mandatory declarations is breaking (§3.2.3).
   Additions that only widen stay information (§3.2.2) — `HasTypeDefinition`, `HasEncoding`,
   `GeneratesEvent`, `Organizes` — because nothing that existed disappeared.
3. **Key references by identifier, not alias** *(implemented)*: `_reference_key` and
   `_resolved_key` use the identifier of the reference type, with the name only as a label and
   only as a fallback when a document names a type without giving its identifier.
4. **Bring `ServerUri` into identity** *(implemented)*: identity and the renumbering key are
   `(namespaceUri, identifier, serverUri)`, so re-qualifying a node against another server reads
   as a removal plus an addition; `ModelIndex.nodes` is keyed by the full triple, so two
   server-qualified nodes of one identifier no longer overwrite each other. An
   `--ignore-server-uri` mode is unnecessary — the server belongs to the identity.
5. **Diff `<Extensions>`, RolePermissions and `value_dependencies` semantically** *(implemented)*:
   extensions are compared content-normalized (advisory), role permissions as information, and
   the identifiers a serialized value points at as resolved dependencies (advisory, §3.2.17).
   Still open: a value-vs-datatype consistency finding — a `DataType` move that leaves a `<Value>`
   carrying the old type (demo3's intentional mismatch) should surface as its own rule rather than
   as two separate information changes.
6. **Add a method-signature diff area** *(implemented)*: one change per method when argument
   count, order, datatype, rank, array dimensions or `MethodDeclarationId` change, attributed
   to the method node (§3.2.11).

### 7.2 Release metadata

7. **Report version and dependency metadata changes** *(implemented)*: per namespace, `Version`,
   `PublicationDate`, `XmlSchemaUri`, `ModelVersion`, alias, and the set (and versions) of
   `RequiredModel` entries — old → new, one severity per attribute. This is what a companion-spec
   release note needs. Version claims are compared on their numeric core (§2.4): a release that
   only re-spells its `Version` is not a release move, and a `RequiredModel` minimum version is
   read as the `ModelVersion` that Annex F.2 defines it to be.
8. **Warn on conflicting namespace metadata across documents** *(implemented)*: the richer-value
   merge stays, and the conflict is reported as an advisory finding in the diff — deduplicated per
   release rather than per spelling, so documents that only spell `1.04.0` and `1.4.0` declare the
   same claim (§11.2).
9. **Compare model-level conformance metadata** *(implemented for ConformanceUnits and Profiles,
   §12.1/§12.2)*: the declarations are found by BrowseName in the companion namespace, never by
   hardcoded identifiers, so an added unit or profile is information, a removed one is an advisory
   naming the lost test scope, and a changed member list is one advisory
   `BP-CU-DependencyChanged`. A new NamespaceUri that declares neither is advised (§11.3). Facets
   and Feature-Sets remain open:
   §7.5.

### 7.3 Scaling for many companion specs

10. **Bundle-aware diff** *(implemented)*: each side is a NodeSet file or a split-export bundle —
    a directory with `manifest.json`, or that manifest itself — whose namespace files are merged
    into one model in manifest order, so directory-vs-directory, bundle-vs-file and bundle-vs-bundle
    all work. A duplicate-conflict inside a side became a reported `BP-Merge-Conflict` advisory
    instead of aborting the run. A `--old-dir`/`--new-dir` spelling is unnecessary: a directory path
    is the bundle.
11. **Scoping filters** *(partly implemented)*: `--namespace <uri-or-alias>`,
    `--exclude-namespace <uri-or-alias>` and `--node-class <class>` filter both models, repeatable.
    An alias is the declared `Alias` or the URI tail, whichever the report shows; a `--namespace`
    neither side declares exits 1 rather than printing an empty report, and an
    `--exclude-namespace` that matches nothing only notes on stderr.
    Still open: browse-name globs and `--ua-baseline off` (drop namespace-0 noise in registry-wide
    diffs).
12. **Impact by identifier + context** *(implemented)*: JSON impact emits `ExpandedNodeId`s
    (`impacted_nodes`) and keeps labels for humans (`impacted_labels`); `--context <paths…>` loads
    context models — the providers of externalized specs — so consumers in sibling specs are
    counted, and `--impact-limit N` bounds what a report lists while the count stays exact.
    Reverse inheritance edges stop after 3 hops. What is still open is the shared multi-source BFS
    of item 13: each context file adds a graph build.
13. **Performance**: build the two graphs once, index reverse edges once, compute a shared
    multi-source BFS; cache parsed models and graphs (e.g. content-hash keyed) for repeated
    release comparisons; optionally fingerprint namespaces and skip unchanged ones before
    node-level comparison.
14. **Grouped, scalable reports** *(partly implemented)*: the HTML report groups each node section
    by namespace inside a collapsible `<details>` block carrying the node count, so a release
    spanning many namespaces stays readable. Still open: HTML anchors, Markdown release-note mode
    (breaking first, non-breaking collapsed), CSV export and a `--summary-only` mode.

### 7.4 Classification and CI usability

15. **Severity levels and rule ids** *(implemented)*: BREAKING / ADVISORY / INFO plus stable
    rule identifiers (`BP-NodeId-Immutable`, `BP-EnumValue-Added`, …) in JSON so CI can group,
    gate per rule and write stable waivers. The identifiers are slugs, not clause numbers, so a
    later whitepaper revision does not rename them.
16. **Waiver file** *(implemented)*: `--waive RULE_ID` and `--waiver-file FILE` hold
    `{rule, namespace?, node?, reason}` entries as JSON — a committed file, not YAML, so the same
    artifact a PR comment quotes can be read by the CLI. INFO and ADVISORY findings are waivable;
    breaking ones are not, because waiving one would silently let a published `NamespaceUri` carry a
    break (§11.3). A reviewer who accepts a breaking release says so with `--accept-breaking`, which
    marks every breaking finding and prints the acknowledgement. A waiver never deletes a finding:
    the report, the JSON and the release verdict keep showing it, marked `waived`.
17. **Exit-code semantics**: distinct codes for "no change" (0), "non-breaking change" (0 with
    `--fail-on-change` → 1), "breaking" (1), "could not compare" (2) — so pipelines can tell
    "identical" from "could not load".
18. **JSON schema version field** *(implemented)*: the payload states `"schema_version": "1.0"`
    and keys are additive only, so CI can pin the version it reads. Publishing a JSON schema CI can
    validate against remains open.
19. **Optional pre-diff validation** (`--validate`): run the semantic validator on both models
    first; a malformed new model is a clear failure, not a degraded "identical" result.

### 7.5 Linker-wide improvements and future features

20. **Baseline & release pipeline** *(partly implemented)*: `nodeset-linker diff --baseline
    <dir|manifest.json>` reads the published NamespaceUris and `--release-verdict` gates a
    release that reuses a published URI (§11.3). Still open: fingerprints per release,
    multi-release aggregation (`v1.3…v1.5`) and per-spec changelog generation
    (`--release-notes`).
21. **`diff` of pipeline results**: `nodeset-linker diff --config old.yaml --config new.yaml`
    comparing what two linker configurations *would produce* (root/external/mode changes shown
    as retained-set differences). High usability value: today diff can only compare written
    files, not configuration decisions.
22. **CU / Profile / Facet / Feature-Set policies** *(partly implemented)*: the diff side is done —
     ConformanceUnit and Profile declarations are resolved by BrowseName in the companion namespace,
     their addition is information, and their removal or changed member list is an advisory
     (`BP-CU-Removed`, `BP-Profile-Removed`, `BP-CU-DependencyChanged`), so removing a CU is no
     longer invisible. Retention still wants the documented
    `policies/registry.py` seam — retention driven by a Conformance Unit ("keep everything the
    CU declares"), together with the Facet and Feature-Set shapes.
    A CU member that a release drops stays breaking by §3.2.16; what the loss means for
     certification is the separate advisory (§12.1).
23. **Semantic conformance checks as diff findings**: encoding completeness (new datatype
    without `Default Binary/XML/JSON`), subtype-chain completeness (added type whose base is
    missing), `RequiredModel` claims that resolve to nothing, `IsNamespaceSubset` drift between
    a source file and its reduced export.
24. **Deprecation awareness** *(implemented for `IsDeprecated`, §14.2)*: an added or removed
    `IsDeprecated` reference is reported, an `IsDeprecated` target whose BrowseName names a
    version (`<SpecName>Version1_02`, §14.3.2) is reported as release information on both sides,
    a type that replaces exactly one removed or deprecated type is reported with the version
    suffix it should carry (§11.2.6), and deprecations inside a kept NamespaceUri are advised
    (§14.3). Still open: `Categories` carrying a deprecation marker as a non-breaking removal
    candidate.
25. **Bundle validation CLI**: `nodeset-linker validate --bundle out/split/` validating the
    split-export bundle + manifest together (today only single files validate; the usage guide
    shows a manual Python snippet).
26. **Explain × diff**: `explain --node X --diff old.xml new.xml` — "why did this node change /
    disappear between releases", reusing the explainer over both models.
27. **Interactive/HTML drill-down**: HTML report with per-node dependency trees and impact
    paths (not just counts), so release reviewers can trace *why* 165 nodes are affected
    (demo4) instead of trusting a number.
28. **PR integration**: a first-party GitHub Action wrapping `diff --json --fail-on-breaking`
    and posting the Markdown report as a PR comment, with waiver support (§7.4).
29. **Streaming/async import integration**: expose `diff_models` as a stable public API with
    typed exports (currently `nodeset_linker.diff.__all__` is usable but undocumented for
    embedders), plus optional async loading for registry-scale inputs.

## 8. Prioritization suggestion

| Priority | Item | Why |
| --- | --- | --- |
| P0 | Identifier-move is breaking (§7.1.1) | The release gate currently misses the riskiest change. |
| P0 | Bundle-aware diff + graceful merge degradation (§7.3.10) | Blocks real multi-spec release workflows as soon as specs grow. |
| P1 | Version/RequiredModel metadata diff (§7.2.7) | Release notes and compatibility decisions depend on it. |
| P1 | Rule ids + severity + waivers (§7.4.15–16) | Makes CI gating precise instead of all-or-nothing. |
| P1 | Release verdict over a published namespace (§7.5.20) *(implemented)* | A release that reuses a published NamespaceUri silently breaks consumers (§11.3). |
| P1 | Impact by NodeId + context loading (§7.3.12) *(done)* | Impact numbers become trustworthy across specs. |
| P2 | Scoping filters, grouped reports, performance (§7.3.11/13/14) | Usability at registry scale. |
| P2 | ServerUri, Extensions/RolePermissions, value-dependency diffs (§7.1.4–5) | Correctness for vendor/server-qualified models. |
| P3 | CU/Profile *retention* policies, release fingerprints and aggregation, PR action (§7.5) | Longer-term roadmap on the existing seams. |

## 9. Related documents

* [README — Semantic diff](../README.md#semantic-diff-diff) — user-facing summary and breaking rules
* [Usage Guide §6](usage-guide.md#6-compare-two-model-versions) — practical workflow
* [Architecture](architecture.md) — layers, identity rule, extensibility seam
* [OPC 11030 v1.03.03](OPC-11030-v1.03.03.md) — the best-practices whitepaper itself; every finding
  cites the clause it derives from (`rule_id` + `clause` in JSON)
* [Best-practices implementation plan](best-practices-implementation-plan.md) — the work packages,
  rule-id registry, tests and open decisions for closing the alignment gaps
* [UI Concept](ui-concept.md) — proposed browser-driven wizard over these layers (preview tier in JS,
  authoritative diff/export from the Python engine)
* [Demo 3](../examples/demo3/README.md) / [Demo 4](../examples/demo4/README.md) — diff + impact + CI gate on showcase instances
