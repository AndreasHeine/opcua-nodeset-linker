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
`SemanticModel` objects (repository layer) and compared on **semantic identity**:

```
Identity = (namespaceUri, identifier)
```

Consequences of this design (tests in `tests/test_diff.py`, `TestIdentityNotXml`):

* reordered nodes, whitespace, comments: **no change**;
* a reassigned `NamespaceIndex` (e.g. Machinery moves from index 1 to 2 behind a filler
  namespace): **no change**;
* `DataType="String"` vs `DataType="i=12"`: **no change** — datatype attributes are compared
  through resolved identifiers, not raw XML spellings;
* a node whose identifier changed while namespace, BrowseName and NodeClass stayed equal is
  reported as a **renumbered node**, not as an addition plus a removal.

Nothing in `diff/` looks at XML. The module sits above `repository/` and `graph/`; it consumes
`SemanticModel` and `SemanticGraph` and never re-parses documents.

## 2. How the engine works (`diff/engine.py`)

### 2.1 Indexing

`ModelIndex.of(model)` builds a dictionary keyed by `(namespaceUri, identifier)`. Note that
the repository keys nodes by full `ExpandedNodeId` (which also carries `ServerUri`); the diff
deliberately drops `ServerUri` from identity, so the same node written with or without a
server URI compares as identical.

### 2.2 Set algebra and renumbering pairing

Three sets are computed: `gone` (old minus new), `fresh` (new minus old), `shared`
(intersection). Then `_pair_renumbering()` groups `gone` and `fresh` by the *move key*
`(namespace, BrowseName, NodeClass)` and pairs members of matching groups positionally
(`zip`, sorted by identifier). A paired node becomes a `RenumberedNode` carrying both
identifiers plus its own content changes; unpaired members stay as plain removals/additions.

### 2.3 Per-node comparison (`_node_changes`)

For every shared (or paired renumbered) node the engine compares, in order:

| Area | Compared | Breaking when |
| --- | --- | --- |
| node | `NodeClass` | always |
| node | `BrowseName` (namespace-qualified) | always |
| node | `DisplayName`, `Description`, `Documentation` | never |
| node | `SymbolicName`, `InverseName` | always |
| node | `Categories` | never |
| node | residual semantic XML attributes (`_semantic_attributes`, i.e. everything except the document-local identity spellings in `DOCUMENT_LOCAL_ATTRIBUTES`) | never |
| node | `IsAbstract` | only when it becomes `true` |
| node | Modelling rule (`Mandatory`/`Optional`/`MandatoryPlaceholder`/`OptionalPlaceholder`) | only when Optional → Mandatory |
| node | Variable/VariableType `DataType`, `ValueRank`, `ArrayDimensions` | always |
| node | serialized `<Value>` (whitespace-normalized) | never |
| definition | Structure fields, Enum values, Union alternatives, OptionSet values | see §2.4 |
| reference | added/removed `<Reference>` keyed by `(type name, target identity, direction)` | removal: see below; **addition: never** |

Definition diffs (`_definition_changes`):

* **Structure**: fields keyed by name — added field (not breaking), removed field (breaking
  unless the field was `IsOptional`), changed field datatype (breaking), `IsOptional` →
  `false` (breaking), field metadata (`SymbolicName`, `ValueRank`, `ArrayDimensions`,
  `MaxStringLength`, `AllowSubTypes`) changes (breaking), member **order** of the common
  fields changed (breaking — it changes the encoding).
* **Union**: alternatives keyed by name — added (not breaking), removed or retyped (breaking),
  alternative order changed (breaking).
* **Enum / OptionSet**: values keyed by name — added value (not breaking), removed value
  (breaking), changed value number (breaking).
* Definition kind changed (e.g. Structure → Enum) or definition dropped: breaking.

Reference breaking classification (`_reference_removal_breaking`):

* `Organizes`, `HasDescription` → never breaking (navigation only);
* `HasSubtype`, `HasTypeDefinition`, `HasInterface`, `HasEncoding`, `GeneratesEvent` → always
  breaking;
* ownership references (`HasComponent`, `HasProperty`, …) → breaking only if the *target* is
  explicitly declared `Mandatory`/`MandatoryPlaceholder` (and not in the OPC UA namespace);
* any other/unknown reference name → breaking by default.

Removal of a whole node is breaking when the node is a type, an encoding, a placeholder, or
an explicitly mandatory node (`_removal_breaking`). `_is_explicitly_mandatory` intentionally
never treats OPC-UA-namespace nodes as mandatory, because Part 5 declares no modelling rules
there.

### 2.4 Namespace totals and release metadata

`NamespaceChange` reports per namespace URI: node count, reference count, datatype count,
old → new with delta, plus the release metadata of that namespace — `NamespaceUri`, alias,
`Version`, `PublicationDate`, `XmlSchemaUri`, `ModelVersion`, and the `RequiredModel` set with
the version each dependency claims. A namespace is listed when its totals changed **or** when
its metadata changed, so a release that only bumped a version still shows up. The entry also
counts the nodes that carry an `IsDeprecated` reference (§14.2): a release that deprecates
inside a NamespaceUri it keeps is advised, because that URI can never drop those nodes again
(§14.3).

Namespace findings stay on the namespace entry, not in the flat breaking list (§2.5): they
describe a release, not a node, and there is no node id to attribute them to.

### 2.5 Breaking list

`_breaking_changes()` flattens every breaking `Change` and every breaking removal into a
flat `BreakingChange` list with a human reason string; `NodeModification.breaking` and
`RenumberedNode.breaking` are derived from their changes.

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
CI.

## 3. CLI surface

```
nodeset-linker diff old.xml new.xml [--json | --markdown | --html] [--impact]
                    [--impact-limit N] [--context FILE ...]
                    [--breaking] [--advisory/--no-advisory]
                    [--fail-on-breaking] [--fail-on-advisory]
                    [--release-verdict] [--baseline DIR|manifest.json] [-o FILE]
```

* exactly **two files**; each is loaded as a single-node `SemanticModel` via
  `load_nodesets([path])`;
* impact is computed whenever `--impact`, `--breaking`, `--fail-on-breaking` or `--context` is
  given;
* `--impact-limit N` (default 50) caps how many impacted nodes an entry *lists*; the reported
  count stays exact, and a truncated list says how many more there are;
* `--context FILE` is opt-in and repeatable: each consumer model is loaded and its dependency
  edges join the impact walk, so externalized specs and sibling companion specs count instead of
  being silently undercounted. Without it, behaviour is exactly as before.
* `--fail-on-breaking` exits 1 on a BREAKING finding only; `--fail-on-advisory` is a separate,
  opt-in gate; advisory and info findings never gate by default; missing files and parse errors
  exit 1;
* `--release-verdict` answers "may each NamespaceUri be reused?" (§3.3, §11.3) and exits 1 when
  a published URI carries breaking findings; `--baseline` is a directory or `manifest.json`
  listing the published URIs — without it the verdict reports "baseline unknown" instead of
  guessing, and gating stays `--fail-on-breaking`'s job;
* format flags are exclusive in practice (`--json` wins over `--markdown`/`--html`);
* `-o` writes the report; parent directories are **not** created.

The CI gate documented in README/usage guide:

```yaml
- run: nodeset-linker diff old.xml new.xml --json --fail-on-breaking
- run: nodeset-linker diff old.xml new.xml --release-verdict --baseline dist/split/
```

## 4. JSON contract

```json
{
  "old": "…", "new": "…",
  "summary": {"old_nodes": …, "new_nodes": …, "added_nodes": …, "removed_nodes": …,
              "modified_nodes": …, "renumbered_nodes": …, "breaking_changes": …,
              "namespace_changes": …, "identical": false},
  "added_nodes": [ { "label", "node_id", "namespace", "namespace_uri", "node_class", "breaking",
                     "impacted_nodes", "impacted_labels", "impact_count" } ],
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
                                "recommendation","breaking","verdicts","metadata"} ] }
}
```

Keys are added, never renamed or dropped: the boolean `breaking` stays for existing consumers,
while severity comes as `severity`/`rule_id`/`rationale`/`clause` per change plus a `verdicts`
list per entry; `paper` names the whitepaper the severities derive from, and `advisory_changes`
mirrors `breaking_changes` for findings that never gate. `namespace` is the document-local alias
while `namespace_uri` is the URI the node lives in (§11.1). `release` appears **only** when the
caller asked for a verdict (`--release-verdict` or `--baseline`). There is **no schema version
field**; `impacted_nodes` and `impacted_labels` only appear after impact was applied.
`impacted_nodes` lists XML identifiers (`nsu=http://opcfoundation.org/UA/Machinery/;i=2200`) and
`impacted_labels` the human names; both are truncated to `impact_limit`, while `impact_count` is
always the exact number of distinct impacted identifiers.

---

## 5. Gaps and limitations of the current implementation

Gaps that the OPC UA best-practices whitepaper (OPC 11030) also asserts — forbidden additions,
removal of optional nodes, namespace versioning, deprecation — are mapped clause by clause in
[best-practices-alignment.md](best-practices-alignment.md).

### 5.1 Identity-level gaps

1. **`ServerUri` is dropped from diff identity.** The architecture states model identity as
   `(namespaceUri, identifier, serverUri)`, but the diff keys by `(namespaceUri, identifier)`
   only. A node that moves between two servers (its identifier stays, `ServerUri` changes) is
   reported as *no change* — even though it becomes a different semantic node.
2. **Server-qualified nodes can collide inside one model.** `load_nodesets` keys nodes by full
   `ExpandedNodeId`; the diff's identity index (`ModelIndex.nodes`) collapses different
   `ServerUri`s onto the same key. The *last* node wins silently; a model containing
   `srv=1;ns=1;i=5` and `srv=2;ns=1;i=5` cannot be diffed faithfully.
3. **The renumbering heuristic pairs blindly.** Grouping by `(namespace, BrowseName, NodeClass)`
   and `zip`-pairing sorted identifiers means:
   * a node *renamed* **and** renumbered is reported as unrelated removal + addition (the move
     key includes BrowseName);
   * a genuine removal plus an unrelated new node that happens to share namespace, BrowseName
     and class (typical when a companion spec replaces a type while adding a sibling) is
     mis-paired as a "renumbering";
   * group-size mismatches pair by position with no confidence signal.
   Nothing marks the pairing as "best effort".
4. **Renumbering is never breaking.** `_pair_renumbering` only inherits content changes; the
   identifier move itself carries no `breaking` flag. Yet every external consumer, generated
   client, or companion-spec cross-reference pointing at the *old* NodeId breaks. In practice
   a release that renumbers NodeIds passes `--fail-on-breaking` — arguably the most important
   gate for a spec release.
5. **Reference additions are never breaking.** Adding a `HasTypeDefinition`, a `GeneratesEvent`
   or a *mandatory* component tightens the contract of instances built against the old model,
   but `_reference_changes` marks every addition `breaking=False` (mandatory-ness is only
   checked on removal through the target's modelling rule, not on addition).
6. **Reference identity uses the type *name*.** `_reference_key` keys by `type_name` (falling
   back to the identifier only when the name is empty). If two documents resolve the same alias
   to different reference-type identifiers (or a custom reference is renamed to a standard
   alias), the change is invisible.
7. **Reference multiplicity is invisible.** Keys go into a `set`, so a duplicated reference in
   the old model that disappears in the new one reports nothing.

### 5.2 Content-level gaps

8. **`<Extensions>` and RolePermissions are not compared.** The parser keeps
   `extensions_xml` and `role_permissions_xml` on the node, but `_node_changes` never reads
   them. ExtensionObject values of nodes, role-permission declarations: silent.
9. **`<Value>` is compared as whitespace-normalized text only.** A value that changes a
   contained `Identifier` (`TypeId`/`DataType` inside an ExtensionObject) is caught only as raw
   text noise, and `value_dependencies` (the *resolved* identifiers the parser extracted) are
   not diffed semantically. Conversely a numeric enum default or encoding-relevant value edit
   is reported as *non-breaking*, although clients may depend on it.
10. **Method signatures have no dedicated diff.** `InputArguments`/`OutputArguments` changes
    appear as attribute/reference changes on the argument variables, never as one "signature
    changed" finding on the method node; `MethodDeclarationId` moves only show up as reference
    changes.
11. **No value-vs-datatype consistency check.** A variable whose declared `DataType` changes
    while its `<Value>` still carries the old type (demo3's intentional incompatibility) is
    reported as a datatype change, but nothing flags the value/type mismatch itself.

### 5.3 Metadata-level gaps (release-relevant)

12. **Model/namespace version metadata is reported.** `NamespaceChange` carries the release
    metadata of a namespace next to its totals: `Version`, `PublicationDate`, `XmlSchemaUri`,
    `ModelVersion` and alias (OPC 11030 §11.1, §11.2). A metadata-only release move — a version
    bump with no node change — now appears as an *information* entry, so a release note can name
    the fact without gating the release. A changed `NamespaceUri` is breaking (§11.3): consumers
    resolve the model under the old URI and find nothing.
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
15. **Duplicate detection is only partial.** `SemanticModel.add` tolerates "same semantics"
    re-declarations based on `node_class`, `browse_name`, `is_abstract` and `HasSubtype`
    targets only. Two companion specs that re-declare a shared UA base node with *different*
    descriptions, datatype fields or references merge silently; differing in class/name/subtype
    raises `DuplicateNodeError` and **kills the whole diff**, with no partial report.

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

20. **Breaking rules are hardcoded and binary.** There is no severity (info/warning/breaking),
    no per-change configuration, no allow-list/waiver mechanism, and removal of an enum value
    or custom reference is always breaking regardless of context. `CHANGE_LABELS` covers only
    node-kind changes; definition `added/removed/changed` changes fall through `describe()`'s
    generic branch.
21. **The breaking verdict is computed but never justified per rule.** `BreakingChange.reason`
    is a rendered text, not a rule id — CI dashboards cannot group by rule, and a waiver cannot
    reference a stable identifier.
22. **`--breaking` hides additions completely** (added nodes are never breaking), so a report
    with `--breaking` silently omits contract-tightening additions (see gap 5).

### 5.6 Scaling with a growing number of companion specs

23. **Diff is two files only.** Real companion-spec work involves multi-file inputs: split
    export produces one XML per namespace plus `manifest.json`; a spec release is typically a
    *bundle*. There is no bundle/manifest-aware diff, no directory-vs-directory diff, no
    multi-file-vs-multi-file diff — users must merge the bundle manually first.
24. **Manual merging of two release trees can fail wholesale.** Loading the old tree may merge
    fine (identical UA re-declarations tolerated), but any inconsistent re-declaration raises
    `DuplicateNodeError` and aborts before a single change is reported. As the number of loaded
    specs grows, the chance of one conflicting declaration growing, the whole diff becomes
    unusable — there is no per-file or per-namespace fallback.
25. **No per-spec/per-namespace scoping.** With dozens of namespaces, `diff` reports everything
    at once. There is no filter (`--namespace`, browse-name globs), and namespace totals do not
    reveal *what* changed inside a namespace unless the nodes changed too.
26. **Performance scales poorly.** Parsing both models is O(total nodes); impact is
    O(changed nodes × graph). For a registry-scale comparison (UA.xml ~5k nodes plus many
    companion specs, thousands of changed nodes between releases), the repeated graph builds
    and per-node BFS dominate runtime and memory; there is no incremental/cached mode.
27. **Report output does not scale.** Impact lists truncate at `--impact-limit` names per entry
    (default 50) and say how many more there are; the flat
    breaking list grows linearly; there is no grouping by namespace, no paging, no summary
    drill-down. HTML has no anchors/collapsing; Markdown release notes become unreadable for
    large diffs.

### 5.7 Release-workflow gaps

28. **Baseline is publication-only, not a model snapshot.** A release verdict now reads a
    publication registry (`--baseline <dir|manifest.json>`), which the split export writes
    itself (§11.1), and answers whether each NamespaceUri may be reused (§3.3, §11.3). It does
    *not* store a previous model or fingerprint: every comparison still re-parses both full
    models, and there is no multi-release aggregation (v1.3 → v1.4 → v1.5) or history yet.
29. **Diff does not validate.** Neither side is semantically validated before comparison; a
    malformed new model can be diffed and even reported "identical" where parsing silently
    degraded (e.g. unresolvable identifiers stay unresolved and compare as unresolved).
30. **JSON has no versioning.** The CI contract shape is unversioned; adding fields later
    (severity, rule ids, NodeIds in impact) breaks consumers with no negotiation.
31. **Label resolution is offline-limited.** Standard namespace-0 datatypes fall back to the
    built-in table (`STANDARD_DATA_TYPE_NAMES`); everything else shows bare identifiers when the
    provider model is externalized. For companion specs referencing many external models,
    reports degrade to identifiers with no hint that a provider could resolve them.

### 5.8 Minor correctness/usability nits

32. Sort keys compare identifiers as strings, so numeric ids sort lexically (`i=9` before
    `i=10`); ordering is stable but not intuitive for NodeId ranges.
33. `--json --impact` is supported, but `--json` alone never carries impact; there is no flag
    that says "JSON with impact" explicitly.
34. `-o` does not create parent directories; a wrong path fails late.
35. `total_impact` merges impact across *all* entries via label deduplication, so a namespace
    with colliding names lowers the apparent total.

---

## 6. What changes as companion specs grow and new releases arrive

A concrete projection of the gaps above onto the realistic workflow (many specs, registry-wide
release comparison):

* **Noise grows linearly**: UA-namespace nodes are ~5k; a registry-wide release diff will list
  thousands of entries in one flat report unless scoping exists.
* **Impact becomes both over- and under-counting**: over-counting through deep subtype forests
  inside the loaded tree, under-counting across unloaded sibling specs — the numbers lose
  meaning at scale.
* **Renumbered releases pass the CI gate silently** (gap 4) — the riskiest release change is
  the one the gate does not catch.
* **Version metadata moves now reach the report** (gap 12 closed), and `--release-verdict` says
  whether a NamespaceUri may be reused (gap 28 partly closed) — but only when a baseline states
  what was published; without one the report names the fact rather than judging the release.
* **One inconsistent declaration aborts everything** (gap 24) — the more specs loaded, the
  higher the chance the diff cannot run at all.
* **Runtime grows quadratically-ish** (gap 26) — impact per changed node over a growing graph.

## 7. Suggested improvements

### 7.1 Correctness (highest value first)

1. **Make identifier moves breaking by default.** Give `RenumberedNode` an explicit
   `breaking=True` for the identity move (rule `IDENTIFIER_MOVE`), optionally gated by a flag
   (`--renumbering-breaking`, default true). Also detect rename-with-same-id separately as
   `BROWSE_NAME_MOVE` so a rename + renumber reports one coherent finding instead of
   add/remove noise.
2. **Classify contract-tightening additions.** Adding a mandatory component, a
   `HasTypeDefinition`, `HasInterface` or `GeneratesEvent` should be breaking; keep purely
   optional/navigation additions non-breaking.
3. **Key references by identifier, not alias.** Use `reference_type` identifier as primary key
   (name only as label), so alias-resolution differences are visible.
4. **Bring `ServerUri` into identity** (or add an explicit `--ignore-server-uri` mode) and
   detect server-qualified re-parenting as a change. Report identity collisions inside one
   model instead of last-one-wins.
5. **Diff `<Extensions>`, RolePermissions and `value_dependencies` semantically**, and add a
   value-vs-datatype consistency finding (demo3's mismatch would surface as
   `VALUE_TYPE_MISMATCH`).
6. **Add a method-signature diff area**: one change per method when argument count/order/
   datatype changes, attributed to the method node.

### 7.2 Release metadata

7. **Report version and dependency metadata changes** *(implemented)*: per namespace, `Version`,
   `PublicationDate`, `XmlSchemaUri`, `ModelVersion`, alias, and the set (and versions) of
   `RequiredModel` entries — old → new, one severity per attribute. This is what a companion-spec
   release note needs.
8. **Warn on conflicting namespace metadata across documents** *(implemented)*: the richer-value
   merge stays, and the conflict is reported as an advisory finding in the diff.
9. **Compare model-level conformance metadata** *(implemented for ConformanceUnits and Profiles,
   §12.1/§12.2)*: the declarations are found by BrowseName in the companion namespace, never by
   hardcoded identifiers, so an added unit or profile is information, a removed one is an advisory
   naming the lost test scope, and a changed member list is one advisory
   `BP-CU-DependencyChanged`. A new NamespaceUri that declares neither is advised (§11.3). Facets
   and Feature-Sets remain open:
   §7.5.

### 7.3 Scaling for many companion specs

10. **Bundle-aware diff**: accept multi-file inputs (`--old-dir`, `--new-dir`, manifest.json
    aware), directory-vs-directory, and per-file merging with **graceful degradation** — a
    duplicate-conflict should become a reported `MERGE_CONFLICT` finding and the rest of the
    diff should still run, not abort.
11. **Scoping filters**: `--namespace <uri-or-glob>`, `--exclude-namespace`, browse-name globs,
    `--node-class`, `--ua-baseline off` (drop namespace-0 noise in registry-wide diffs).
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
14. **Grouped, scalable reports**: group changes by namespace with counts, add HTML anchors +
    collapsible sections, Markdown release-note mode (breaking first, non-breaking collapsed),
    CSV export, and a `--summary-only` mode.

### 7.4 Classification and CI usability

15. **Severity levels and rule ids** *(implemented)*: BREAKING / ADVISORY / INFO plus stable
    rule identifiers (`BP-NodeId-Immutable`, `BP-EnumValue-Added`, …) in JSON so CI can group,
    gate per rule and write stable waivers. The identifiers are slugs, not clause numbers, so a
    later whitepaper revision does not rename them.
16. **Waiver/allow-list file** (`diff-allowlist.yaml`) for *approved* breaking changes:
    `--fail-on-breaking` skips waived rules; waivers appear in the report so reviewers see
    them.
17. **Exit-code semantics**: distinct codes for "no change" (0), "non-breaking change" (0 with
    `--fail-on-change` → 1), "breaking" (1), "could not compare" (2) — so pipelines can tell
    "identical" from "could not load".
18. **JSON schema version field** (`"schema_version": 1`) plus a published schema so CI
    consumers can pin it.
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
* [Best-practices alignment](best-practices-alignment.md) — every OPC 11030 §3.2 rule vs. the current
  classification, with verdicts (`aligned` / `stricter` / `looser` / `missing`) and ranked fixes
* [OPC 11030 v1.03.03](OPC-11030-v1.03.03.md) — the best-practices whitepaper itself
* [Best-practices implementation plan](best-practices-implementation-plan.md) — the work packages,
  rule-id registry, tests and open decisions for closing the alignment gaps
* [UI Concept](ui-concept.md) — proposed browser-driven wizard over these layers (preview tier in JS,
  authoritative diff/export from the Python engine)
* [Demo 3](../examples/demo3/README.md) / [Demo 4](../examples/demo4/README.md) — diff + impact + CI gate on showcase instances
