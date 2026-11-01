# Dependency Resolution — OPC UA NodeSet Linker

The shaker answers one question: *which nodes must exist so that the selected roots are a
semantically complete OPC UA model?* The answer is a dependency closure plus a retention
decision, and every retained node carries the reason it exists.

## 1. Root selection

`RootResolver` turns configuration strings into node ids. Supported selectors:

| Selector | Meaning |
| --- | --- |
| `nodeid=<id>` / bare `<id>` | One node by NodeId (`i=`, `s=`, `g=`, `b=`, `nsu=`, `ns=`). |
| `expandedNodeId=<id>` | Same, explicit ExpandedNodeId. |
| `browseName=<name or glob>` | All nodes whose BrowseName matches; `*Type` style globs allowed. |
| `namespaceUri=<uri>` | Every node declared in that namespace (exact URI or a URI suffix match). |
| `typeDefinition=<name>` | The type itself **and** every instance declaring it via `HasTypeDefinition`. |

Selectors that match nothing raise `ResolutionError`; duplicates are deduplicated.

**`ns=<index>` is resolved against the merged namespace table, not against one document.**
`RootResolver._resolve_index_marker` (`resolver/roots.py:118-127`) walks the merged namespaces
and returns the first one whose `index_in_document` equals the number; merging keeps the
index of the document that declared the namespace first (`repository/model.py:125-135`). So if
two loaded documents both declare index 1, `ns=1;i=…` can only reach one namespace's nodes —
verified with two documents each declaring index 1: loading A then B resolves `ns=1;i=900` to
A and *matches nothing* for `ns=1;i=901`, while loading B then A does the opposite. Load order
therefore changes which roots are selected. Use `namespaceUri=` instead.

`ExpandedNodeId.parse("ns=1;i=5")` yields the index-marker form `nsu=index:1;i=5`, which is not
a model identity — only `RootResolver` turns it into a URI, so passing a parsed `ns=` id
directly to the closure raises "root node is not part of the model".

## 2. Closure traversal

`DependencyResolver` walks the semantic graph breadth-first from each root with a visited set,
so cycles terminate and the result is deterministic (first path wins). Each edge carries a
`DependencyKind`, which decides how it is followed:

| Kind | Followed forward | Followed backward | Why |
| --- | --- | --- | --- |
| INHERITANCE (`HasSubtype` on objects/variables/views) | yes | **never** | A subtype must have its base; a base does not need its subtypes. Official documents declare the base with `IsForward="false"`: that edge runs *from the subtype to the base*, so it is followed forward from the subtype and skipped in the backward direction. A document that instead writes `HasSubtype` forward *from a base* does pull its subtypes — the skip is about the inverted spelling, not about the reference type. |
| DATATYPE (datatype `HasSubtype`, variable `DataType`, structure fields, union alternatives, `GeneratesEvent`) | yes | **yes** | Fields, `DataType` attributes and datatype subtype chains are followed in both directions. Consequence: a root enum pulls the structure that declares it as a field, and a retained datatype base pulls every subtype declared in the loaded documents (see below). |
| STRUCTURAL (`HasComponent`, `HasProperty`, `HasTypeDefinition`, `HasOrderedComponent`, custom refs) | yes | yes | Components belong to their parent and parents belong to declared components; inverted references are real modelling dependencies. |
| INTERFACE (`HasInterface`) | yes | **yes** | An instance declaring an interface needs the interface node, and an interface root pulls the instances that declare it. |
| ENCODING (`HasEncoding`) | yes | **yes** | A retained custom datatype keeps its Binary/XML/JSON encodings, and an encoding root pulls the datatype it encodes. |
| NAMESPACE (`Organizes`) | yes | yes | Views and organisational structure stay coherent; namespace metadata is reachable through the `Namespaces` folder. |
| OPTIONAL (`HasDescription`) | yes | yes | Kept conservatively; retention policy decides afterwards. |
| METHOD | — | — | Declared (`models/dependency.py:24`) but **never assigned anywhere**: `STANDARD_REFERENCE_TYPES` has no method mapping, so a `UAMethod` reaches its parent through a STRUCTURAL `HasComponent` edge. The dependency report for `examples/config.yaml` accordingly has no `method` category. |
| REFERENCETYPE | yes | — | After the traversal, every reference type a retained node uses by *name* joins the closure and brings its own bases. Types of the OPC UA namespace are deliberately not pulled: they arrive with the standard namespace every importer loads, and pulling them would undo most of the reduction. |

Only INHERITANCE is filtered out in the backward direction (`resolver/closure.py:143-145`).
Verified over `tests/data/ua_base.xml` + `tests/data/machinery.xml`:

* root `browseName=JobStateEnum` pulls `JobDataType` through the reversed `Field:State` edge;
* root `browseName=IMachineStatus` pulls `MSSControllerInstance`, the instance that declares it;
* root `browseName=JobDataTypeBinary` pulls `JobDataType` through the reversed `HasEncoding` edge;
* root `i=24` (`BaseDataType`) keeps **54 of 71** nodes, because the datatype subtype edges run
  backward — while root `i=58` (`BaseObjectType`) keeps **only itself**.

Each visited node records a `RetentionReason`: the root it came from, the dependency path with
reference types, and the dependency kind of the last hop. Roots record an empty path.

Example — why `JobStateEnum` survives when `MSSControllerType` is selected. The recorded first
path (what `RetentionReason.describe()` prints):

```
MSSControllerType [HasComponent] -> Diagnostics [HasComponent] -> String [DataType]
                  -> JobDataType [Field:JobId, backward] -> JobStateEnum [Field:State]
```

`JobDataType` is reached from its *field* `JobId` (String), not from `JobStateEnum`; the step
into `JobStateEnum` is the field named `State`. Two presentation quirks: `nodeset-linker
explain` prints only the browse-name chain (`MSSControllerType -> Diagnostics -> String ->
JobDataType -> JobStateEnum`) plus the last dependency kind, and `describe()` labels the origin
hop with the *first* hop's reference type, so the first bracket is not the edge leaving the
root.

## 3. Retention decision

The closure says *what is reachable*; the policy says *what may be dropped*. For every node in
the model the engine evaluates, in this order:

1. **Custom rules** (first match wins) — a user rule sets the decision; rules can only narrow
   or widen retention of *droppable* nodes, because step 3 overrides them for hard ones.
   Excluding a hard node such as `JobDataType` has no effect.
2. **Base policy** — `STRICT` keeps everything reachable; `OPTIONAL_AWARE` retains exactly the
   same set (it subclasses `STRICT`) and adds a `classify()` helper; `RUNTIME` drops explicit
   `Optional` nodes; `CUSTOM` follows STRICT where no rule matches.
3. **Hard-dependency override** — roots, nodes whose role is a hard dependency (base type,
   abstract type, structure/enum/union/option set, encoding, interface, type definition,
   placeholder), and nodes declared `Mandatory` or `MandatoryPlaceholder` are never dropped
   (`retention/engine.py:105-107, 156-166`). Types cannot be shaken out of a model by a policy
   or a custom rule.
4. **Outside the closure** — nothing is kept unless a `retain:` rule matched it.

Nodes outside the closure and not retained are removed; they are what "reduction" means here.
Nodes added only by a custom `retain:` rule are retained for export but have no closure
`RetentionReason`; the current `explain` command reports them as not reachable.

Two details that read differently from the mode names:

* **`RUNTIME` never drops an `OptionalPlaceholder`.** `NodeRole.PLACEHOLDER` is a hard
  dependency (`models/node_class.py:60-73`), so step 3 overrides the policy even though
  `RuntimePolicy.should_retain` says no. Verified with a parsed `OptionalPlaceholder` node
  (i=463, browse name containing `_Placeholder`): RUNTIME keeps it, drop list empty.
* **mandatory/optional classification is reported in every mode, not only OPTIONAL_AWARE.**
  The reports call `mandatory_level_of()` directly (`reports/data.py:110-139`);
  `OptionalAwarePolicy.classify` (`retention/policies.py:27-39`) is never invoked by `src`.
  The `examples/config.yaml` run reports mandatory 52 / optional 2 in STRICT *and* in
  OPTIONAL_AWARE, with identical retention (54 nodes in both).

In RUNTIME the example run drops `Diagnostics` and `MachineDiagnostics`; `Diagnostics` is
immediately brought back by repair, so 53 of 71 nodes survive (STRICT: 54).

## 4. Repair, and why user exclusions are never repaired

A retained node must not keep a reference to a dropped target. Two cases:

* **Policy drop** — if a retained node still references the policy-dropped node, the engine
  brings the target back (`retention/engine.py:120-154`). Dropping an Optional component is
  legal only when nothing points at it anymore; the walk follows the *declared* references of
  retained nodes, so a component that only declares an inverted `HasComponent` at its parent
  (the official spelling) is not restored — that is why `MachineDiagnostics` stays gone while
  `Diagnostics` comes back. The repairs are applied but **not surfaced**: `pipeline.py:90` calls
  `engine.repair` and discards the returned list, so no report or CLI output mentions a repair;
  a repair only shows up as a higher retained-node count.
* **User exclusion** — an explicit `exclude:` rule is intent, so it is *never* silently
  repaired. If the exclusion breaks the model, validation reports it and export is blocked.
  The concrete findings exist on the exception's `.findings` attribute (a `ValidationReport`),
  but the CLI prints only the summary line — for an exclusion that breaks two references:
  `error: 2 error(s), 0 warning(s)`. Reading the findings needs the library API, not the CLI.

## 5. Validation before export

`SemanticValidator` checks the reduced subset with nine core checks: reference targets present,
reference type declarations (`REFERENCE_TYPE_MISSING` error when a model's own type was removed;
`REFERENCE_TYPE_UNKNOWN` warning when a reference name resolves to no identifier),
`HasTypeDefinition` class agreement, datatype references and structure-field/union-alternative
datatypes present, ReferenceType hierarchy, subtype hierarchy with class agreement and cycles,
encoding targets (the standard `Default Binary/XML/JSON` objects count), namespace links
(`NAMESPACE_NOT_DECLARED` warning for a namespace used but not declared, `REQUIRED_MODEL_MISSING`
warning for a required model that is not loaded), and namespace subset markers
(`NAMESPACE_METADATA_MISSING` warning).

A separate external-dependency diagnostic pass reports external models that are unused,
undeclared, unloaded, or missing version metadata.

Errors block the run: `pipeline.run()` raises `ValidationError` before the exporter is reached.
Warnings do not — `NAMESPACE_METADATA_MISSING` in particular is a warning, and a namespace whose
metadata object was dropped still exports.

The cycle check is partial: `_has_subtype_cycle` (`validator/semantic.py:267-279`) walks
`bases[0]` only. A node with two `HasSubtype` bases whose cycle runs through the *second* base
passes validation — verified: `CycleA` inheriting `CycleB` and `CycleC`, with `CycleC`
inheriting `CycleA`, reports no error, while a two-node cycle written through the first base
reports `SUBTYPE_CYCLE`.

## 6. Externalized dependencies (`external:`)

`external:` lists the ModelUris the *consumer* provides. It is not a retention rule: the policy
still decides about every other namespace, and the externalized ones are taken out afterwards
(`pipeline.py:69-105`). The configured URIs are resolved against the URIs the loaded documents
declare, so a trailing slash or a missing one names the same model; an entry that is a namespace
index or an identifier (`ns=1`, `i=1000`, `index:2`) is rejected by the config itself.

Three things follow in the pipeline: a root selector that matches a node of an externalized model
raises `ResolutionError` — a root cannot live in a dependency that is not shipped; the model's nodes
leave the retained set even though the closure reached them (`retained -= external_ids`); and they
are never repaired back in, because a reference to them is meant to point *outside* the file.

The validator gets the same set. `_resolve()` resolves through the exported subset first and falls
back to the loaded source **only for an externalized namespace**, so `REF_TARGET_MISSING`,
`REFERENCE_TYPE_MISSING`, `DATATYPE_MISSING`, `STRUCT_FIELD_DATATYPE_MISSING`,
`UNION_ALTERNATIVE_DATATYPE_MISSING`, `SUBTYPE_BASE_MISSING` and `ENCODING_MISSING` accept an
out-of-file target there and nowhere else. A namespace that is used but not declared stays a
`NAMESPACE_NOT_DECLARED` warning for everyone except an externalized URI, and
`REQUIRED_MODEL_MISSING` says *"which is externalized and not loaded — the importing application has
to make it available"* instead of the ordinary "not loaded".

Four warnings describe the dependency itself, one per external URI:

| Code | Meaning |
| --- | --- |
| `EXTERNAL_MODEL_UNUSED` | the model was configured as external but nothing in the run references it |
| `EXTERNAL_MODEL_NOT_DECLARED` | it is referenced, but no `RequiredModel` states the dependency; the linker does not invent one, so a later validation reports the missing declaration |
| `EXTERNAL_MODEL_NOT_LOADED` | its declarations were not read, so they could not be checked |
| `EXTERNAL_MODEL_VERSION_UNKNOWN` | no `Version` is known, so the output states the dependency without version metadata |

The exporter omits the nodes and the model's own `Models` entry, keeps the namespace URI in
`NamespaceUris` when something references it, and preserves a `<RequiredModel>` child only when
the loaded model metadata already declares that dependency, with the `XmlSchemaUri` / `Version` /
`PublicationDate` / `ModelVersion` the *source* stated. Missing declarations are reported rather
than invented, and version metadata never goes on `<Uri>`, which the schema forbids. Every
identifier pointing at an external namespace is written through the index that namespace got in the
output (`ns=2;i=1000`), including identifiers that are not references: the `DataType`,
`ParentNodeId` and `MethodDeclarationId` attributes, the `ReferenceType` attribute, and the
`DataType` of structure fields and union alternatives.

The OPC UA namespace is a dependency but not the same case: index 0 is reserved for it, so
externalizing it writes no `<Uri>` entry, leaves its identifiers bare (`i=11715`) and states it only
as `<RequiredModel ModelUri="http://opcfoundation.org/UA/">` only when existing model metadata
declares the requirement. Its namespaces are also never marked
as subsets — they are not part of the file.

## 7. Export

Only after validation: namespaces are re-indexed deterministically, always starting at 1 — index
0 is reserved for the OPC UA namespace, because an id without an `ns=` prefix means namespace 0
to every importer. Namespace 0 therefore never appears in `NamespaceUris`, and a subset that
retains no OPC UA node still writes `ns=1;i=…`. NodeIds get their indexes (`i=24` bare for
namespace 0), qualified browse names get the index of *the namespace the name belongs to*
(`BrowseName="1:JobDataType"`), and identifiers qualified against another server are re-indexed
into a written `ServerUris` table (`srv=1;ns=1;i=2001`). Modelling rules become
`<Reference ReferenceType="HasModellingRule">i=78</Reference>`, datatype fields keep the
attributes the source stated, aliases and the model table are written in the schema form of
OPC 10000-6, and reduced namespaces get `IsNamespaceSubset=true` on their metadata property.
Interfaces have no schema element, so they are serialized as `UAObjectType` — with `IsAbstract`
only if the source declared it. Legacy spellings are read silently and never written: rule ids
290/387, `<ModellingRule Id="…">`, attribute-form references with `TypeId`/`TargetId`,
`UriIndex`/`Alias` on `<Uri>` entries, `<NamespaceMetadata>` header elements, `DefinitionType`,
`<UAInterface>` elements, and a `srv=` part spelled as a URI instead of a table index. An index
that the document does not declare is *not* read silently — `ns=4` against a two-entry
`NamespaceUris` table raises `ParseError` rather than resolving to namespace 0.

The document is self-contained in the sense that reloading it yields zero unresolved targets —
unless a dependency was externalized, in which case reloading it shows exactly the targets of that
model and nothing else. It validates against `UANodeSet.xsd`. `Models` entries are filtered to the
namespaces still used, and `RequiredModel` children declared by the source metadata are written as
loaded — a required model whose namespace is not in `NamespaceUris` still appears, which is how a
dependency that is not shipped is stated. The linker does not invent a missing `RequiredModel`.

Steps 1–6 above are the same for both export modes; only the writing changes. `export:` selects the
strategy in the exporter alone: `merged` writes the one document described here, `split` writes one
document per namespace the reduction ships, decided by the namespace of each node's own identifier.
Nothing in the resolution chain learns about files — the closure, retention, repair and validation
all run on the same keep set, and the split then hands that keep set to per-document writers. A
retained node appears in exactly one file, dependencies stay references rather than copies, and each
file states the namespaces it needs as `RequiredModel`. The guarantee is not that one file is
self-contained but that the files loaded *together* reload as the same graph: the exporter verifies
the node count and every cross-file reference before the run is considered written, and
`manifest.json` records the file of each namespace plus its dependencies.

## Success criteria the design satisfies

smallest semantically valid model containing the selected roots · inherited base types kept ·
interfaces kept · datatypes incl. nested structures/enums kept · encodings kept · cross-namespace
dependencies kept · OPC UA correctness enforced (errors only; warnings pass) · output
schema-conformant for other importers · reduced namespaces marked as subsets · a model configured
as an external dependency is referenced but never shipped · full traceability
via `RetentionReason` paths and reports. The "smallest" claim holds along object inheritance,
not along datatype subtype chains: a retained datatype base pulls every subtype the loaded
documents declare.
