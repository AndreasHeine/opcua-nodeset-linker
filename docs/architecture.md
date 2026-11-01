# Architecture — OPC UA NodeSet Linker

The project is a **semantic dependency linker and tree shaker for OPC UA Information
Models**. It is deliberately *not* an XML minifier: `UANodeSet` XML is a serialization
format, never modelling truth. Every decision is made on the semantic model; XML is read
once at the edge of the system and written once at the other edge.

## The one rule that shapes everything

**NamespaceIndex is never used as identity.** Identity everywhere in the codebase is
`(namespaceUri, identifier)` — an `ExpandedNodeId`, with an optional `serverUri` when the
identifier is qualified against another server. A `QualifiedName` carries the URI of the
*namespace of the name*, which may differ from the namespace of the node it belongs to.
Namespace indexes exist only inside a document, they differ between documents, and using them
internally would make merging two NodeSets impossible.

Indexes do appear twice, in ways that are worth knowing about:

* the parser turns a document-local `ns=<index>` id *and* a `BrowseName="<index>:<name>"` into a
  URI *while reading*, against the namespace table of that very document, so identity and export
  use the URI rather than the index. `QualifiedName` retains the source index as a hint as well as
  its resolved URI; an index the document does not declare is a `ParseError`, never a silent
  fallback;
* the same happens for `srv=<index>`: the position in the document's `ServerUris` table becomes a
  server URI on the identifier (`srv=0` means "the server this document was written for" and is
  stored unqualified);
* a root selector `ns=<index>` is resolved against the **merged** namespace table, not
  against one document — see *Dependency Resolution*, §1, for the load-order trap this creates.

Consequences:

* merging several files works by URI, not by index;
* a reduced document re-indexes namespaces in sorted URI order, always starting at 1: index 0 is
   the OPC UA namespace for *every* importer, so a custom namespace never takes index 0 even when
   no OPC UA node was retained (see "Export and determinism");
* exporting the same keep set twice produces byte-identical XML (verified: two `link` runs of
  `examples/config.yaml` give the same SHA-256).

## Layer model

Dependencies flow inward for everything except the exporter, which sits **outside** the
semantic layers rather than at the inner XML boundary: `exporter/nodeset.py` imports
`repository.model.SemanticModel` and `validator.semantic.SemanticValidator` and runs the
validator itself inside `export()` when `validate=True` (the default). So the exporter may
import the repository and the validator; the validator does not import the exporter.

The graph engine really has no knowledge of XML, files or indexes — it receives domain
objects and returns domain objects.

```
        cli/          config/          exporter/      (outermost: human interface + output)
          |             |                |   \
          |             |                |    validator/   (exporter validates what it writes)
        pipeline.py ─────────────────────┘
          |
    ┌-----+-----┐
    |           |
  resolver/   retention/          policies/         (decisions: which nodes, why)
    |           |
  graph/     repository/          validator/        (semantic structures + integrity)
    |           |
  models/   parser/                               (domain objects; XML read at the edge)
    |
  utils/                        (text helpers, error types)
```

| Package | Responsibility | Knows about XML? |
| --- | --- | --- |
| `models/` | Fully typed domain objects: `UANode`, subclasses, `ExpandedNodeId`, `QualifiedName`, `FieldMetadata` (the per-field attributes of a datatype definition), `NodeClass`, `NodeRole`, `ModellingRule`, `DependencyKind`, `MandatoryLevel`, namespace/model metadata including the `ServerUris` table of a document, namespace-metadata helpers. | No |
| `utils/` | Glob matching, file reading, the error hierarchy (`ParseError`, `ResolutionError`, `ExportError`, `ValidationError`). | File IO only |
| `parser/` | Reads `UANodeSet` XML into domain objects; resolves `nsu=`/`ns=`/bare ids and `srv=` ids to URIs, resolves a qualified `BrowseName` against the document's own namespace table, and reads every `DataTypeField` attribute (`SymbolicName`, `ValueRank`, `ArrayDimensions`, `MaxStringLength`, `AllowSubTypes`, `Value`, `IsOptional`). Undeclared `ns=`/`srv=` indexes are `ParseError`s. The *only* place XML is interpreted. | Yes (read) |
| `repository/` | Merged `SemanticModel`: node registry keyed by URI identity, namespace/model metadata, duplicate detection, lookup helpers, unresolved-target detection, reference-type name/id completion. | No |
| `graph/` | `networkx.MultiDiGraph` over `ExpandedNodeId`, edge metadata `{reference_type, dependency_kind, is_forward, mandatory}`, implicit semantic edges, induced subgraphs. | No |
| `resolver/` | Root selectors (`nodeid=`, `browseName=`, `namespaceUri=`, `typeDefinition=`, globs) and the transitive dependency closure with per-node reasons. Nodes added only by a custom `retain:` rule are outside that closure. | No |
| `retention/` | Retention engine + policies (STRICT / OPTIONAL_AWARE / RUNTIME / CUSTOM), custom rules, repair of policy-dropped targets. `repair()` returns the brought-back nodes; the pipeline discards that list. | No |
| `policies/` | Registry mapping a mode name to a policy; documented seam for Conformance Unit / Profile / Facet / Feature Set policies. | No |
| `validator/` | Nine core semantic checks plus external-dependency diagnostics over a (possibly reduced) model. Errors stop a run; warnings do not. | No |
| `exporter/` | Writes the retained subset as a self-contained `UANodeSet`; assigns namespace indexes and the `ServerUris` table here and nowhere else, re-indexes qualified browse names from the URI of the name, writes the `DataTypeField` attributes it kept, and runs the validator again when `export(validate=True)` is called directly. | Yes (write) |
| `reports/` | Statistics, dependency report, namespace report, validation text. | No |
| `explain/` | Answers "why is this node retained" with the concrete dependency path. | No |
| `config/` | Pydantic-validated YAML configuration; custom rule translation. | File IO only |
| `cli/` | Click commands `analyze`, `link`, `explain`, `stats`, `validate`; rich rendering. | No |
| `pipeline.py` | The single ordered composition of all layers. | No |

In a normal CLI run validation happens twice: `pipeline.run()` validates the retained set
and raises `ValidationError` before the exporter is reached, and `export()` validates the
same subset again when `validate=True`. Turning `validate_before_export` off skips the
exporter's check; the pipeline's check is unconditional.

## Domain model

Nodes are frozen `@dataclass(slots=True)` objects — immutable, but **not hashable**. The base
node carries `attributes: Mapping[str, str]` (`models/node.py:127`), and the parser fills it
with a plain `dict` (`parser/nodeset.py:456`), so `hash(node)` on a loaded node raises
`unhashable type: 'dict'`. Nothing needs hashed nodes: every registry, graph vertex and keep
set is keyed by the `ExpandedNodeId` identity, which *is* hashable.
Custom constructors use two-argument `super(Class, self)` because zero-argument `super()`
is incompatible with slotted dataclasses on Python 3.12.

* `UANode` — base for objects; subclasses `UAObject`, `UAVariable`, `UAMethod`, `UAObjectType`,
  `UAVariableType`, `UADataType`, `UAReferenceType`, `UAView`, `UAInterface` carry only the
  attributes their node class actually has (`DataType`/`ValueRank`/`ArrayDimensions` for
  variables, `Definition` for datatypes).
* `NodeRole` classifies *semantic meaning*: base type, abstract type, structure, enum, union,
  option set, encoding, interface, type definition, placeholder, argument, instance, method,
  view, and `NAMESPACE_METADATA`. The parser derives `NAMESPACE_METADATA` from a
  `HasTypeDefinition` pointing at `NamespaceMetadataType` (i=11616), a `SymbolicName` ending in
  `NamespaceMetadata`, or a `UA_NamespaceMetadata*` browse name
  (`parser/nodeset.py:679-685`); the subset-marker logic and the `NAMESPACE_METADATA_MISSING`
  warning depend on it.
  `is_hard_dependency` lists the roles that may never be dropped (abstract type, base type,
  structure, enum, union, option set, encoding, interface, type definition, placeholder).
  `is_droppable` exists (`models/node_class.py:76`) but the implementation never calls it — it
  has exactly one occurrence in the repo, its own definition. Retention decides with
  `is_hard_dependency` plus the modelling rule instead (`retention/engine.py:156-168`).
* `ModellingRule` covers Mandatory (i=78), Optional (i=80), MandatoryPlaceholder (i=462),
  OptionalPlaceholder (i=463) and `NO_RULE`; the pre-schema ids 290/387 are still read as the
  same two rules. In XML a rule is a reference, not an attribute:
  `<Reference ReferenceType="HasModellingRule">i=80</Reference>`. Absence of a rule is **not**
  optionality: OPC UA instance declarations without a rule are mandatory, so
  `mandatory_level_of()` returns `MANDATORY` unless the document explicitly says Optional.
* `Definition` models Structure fields, Enum values, Union alternatives and OptionSet values,
  each with a `FieldMetadata` holding supported attributes (`SymbolicName`, `ValueRank`,
  `ArrayDimensions`, `MaxStringLength`, `AllowSubTypes`) plus `DisplayName`, `Description`,
  `Value` and `IsOptional`. Schema defaults are normalized to their semantic values and omitted
  from output; an explicitly stated default is therefore not preserved as literal XML attribute
  presence. These are domain objects, not XML children.

## Graph engine

Vertices are keyed by `ExpandedNodeId`; the payload lives in a separate `_NodeData` dict so
networkx attribute dicts hold only edge metadata. A `MultiDiGraph` is required because the
same node pair can be connected by several references (`HasComponent` plus a custom
reference, or forward and inverted directions).

Two kinds of edges are created:

1. **Declared references** — every `<Reference>` element, classified through
   `STANDARD_REFERENCE_TYPES` (`HasSubtype` → INHERITANCE, `HasEncoding` → ENCODING,
   `HasInterface` → INTERFACE, `HasDescription` → OPTIONAL, `Organizes` → NAMESPACE, …); a
   reference whose name is not in that table becomes STRUCTURAL. An edge always goes from the
   node that declares the reference to its target, whatever `IsForward` says: official
   documents name their base with `IsForward="false"`, which reads as "I need that node", and
   both spellings are accepted.
   A datatype's `HasSubtype` edge is classified **DATATYPE** rather than INHERITANCE
   (`graph/engine.py:85-88`). That is not a cosmetic choice: backward traversal skips only
   INHERITANCE (`resolver/closure.py:143-145`), so **a retained datatype base pulls every
   subtype declared in the loaded documents.** Verified over `tests/data/ua_base.xml` +
   `tests/data/machinery.xml`: a closure rooted at `BaseDataType` (i=24) keeps 54 of the 71
   nodes, while a closure rooted at `BaseObjectType` (i=58) keeps just that one node. The
   shaker only shrinks along object/variable inheritance chains, not along datatype ones.
   `DependencyKind.METHOD` is declared (`models/dependency.py:24`) but never assigned; a
   `UAMethod` reaches its parent through a STRUCTURAL `HasComponent` edge.
2. **Implicit semantic edges** — dependencies the XML does not express as references: a
   variable's `DataType` attribute, each structure field, each union alternative. They become
   DATATYPE edges so the closure follows them; otherwise a reduced model would silently lose
   the datatypes a variable needs.

## Export and determinism

`NamespaceIndexAssigner` collects every URI the written document has to speak about: the
namespaces of the retained NodeIds, the namespaces of their reference targets, **and** the
namespaces of qualified browse names. Index 0 is always the OPC UA namespace — an identifier
without an `ns=` prefix means namespace 0 to every importer, so handing index 0 to a custom
namespace would silently re-point its ids there. Even a subset that retains no OPC UA node
numbers its namespaces from 1, and the OPC UA URI is absent from `NamespaceUris` only because
nothing in the document refers to it. Servers are collected the same way: every identifier that
carries a server URI (node id, reference target or reference type) goes into a `ServerUris` table
in URI order, and `srv=<index>` is written from that table (`exporter/nodeset.py:278,347-353`).

Nodes are emitted sorted by assigned id, references and definitions in declaration order, and
no `LastModified` is written: the same keep set yields byte-identical XML.

The export strategy is a second, separate decision, and it is entirely inside the exporter.
`export_split()` (`exporter/nodeset.py`) partitions the keep set by the namespace each node's own
identifier carries — `node_id.namespace`, never a document-local index — and builds one document
per namespace. Because identity is `(namespace URI, LocalPart)` and indexes are per document, the
per-document `NamespaceIndexAssigner` is built from *that document's* used URIs only: a file gets a
contiguous table starting at 1 (`JobControl.xml` says JobControl=1, Machinery=2), and identifiers
pointing at a namespace another file ships are written with that file's local index instead of
being dropped. `<Model>` is scoped to the namespace the file ships, and namespaces it only points
at are appended as `<RequiredModel>` children when the metadata does not already state them. No
node is duplicated, merged or moved, and no dependency is inlined: the same keep set produces the
same semantic graph, spread over files. The manifest is derived from the existing namespace
dependency analysis, and the export reloads the written files to prove the count matches and every
cross-file reference resolves — a split that loses a node or breaks a reference raises
`ExportError` instead of reaching disk. The merged path is untouched: `export()` and
`export_closure()` keep writing one document exactly as before.

The writer produces the schema form of OPC 10000-6 and nothing else — `NamespaceUris` and
`ServerUris` with plain `<Uri>` entries (indexes are the table positions, index 0 never listed),
`Models` with `ModelUri`, `XmlSchemaUri`, `Version`, `PublicationDate`, `ModelVersion`, `Aliases`
for the UA reference types referenced by name, ids of namespace 0 written bare, browse names
qualified with the index of *the namespace the name belongs to* (`exporter/nodeset.py:393-405`),
and interfaces as `UAObjectType` (the schema has no `UAInterface` element). Three details are worth
stating exactly:

* a **`Model` entry** is written only for a namespace the retained nodes actually use, but its
  **declared `RequiredModel` children are emitted as loaded** — a required model whose namespace
  is not in `NamespaceUris` still appears (`exporter/nodeset.py:241-262`). Verified: loading A
  (whose source metadata requires B) and B and keeping only A writes `<Uri>…/A/</Uri>` and a
  `<RequiredModel ModelUri="…/B/">` that nothing in the document resolves. The linker does not
  invent a `RequiredModel` when the source metadata lacks it.
* an **externalized model** (`external:`) contributes no nodes and no `Model` entry of its own; its
  URI is written to `NamespaceUris` only because something references it, and its dependency is
  preserved as a `<RequiredModel>` child only when existing model metadata declares that
  requirement, with the version metadata the source declared
  (`exporter/nodeset.py:310-331`). An identifier pointing at it is written through the index that
  namespace got — everywhere an identifier appears, not only in `<Reference>`: `DataType`,
  `ParentNodeId`, `MethodDeclarationId`, the `ReferenceType` attribute, and the `DataType` of
  structure fields and union alternatives (`exporter/nodeset.py:334-354`). Silently dropping such an
  identifier would leave the file relying on a model it never mentions. Namespace 0 keeps index 0,
  so externalizing the OPC UA namespace writes no `<Uri>` entry and bare ids (`i=11715`).
* `IsAbstract` is written **only when the source declared it** (`exporter/nodeset.py:407`). An
  interface that did not say so is exported as `<UAObjectType … BrowseName="1:ILegacy">` with
  no `IsAbstract` attribute — as `tests/data/legacy_namespace.xml` does.

Older spellings are read but never written, so an importer that validates against
`UANodeSet.xsd` accepts the file. What the reader still accepts silently: rule ids 290/387 and
a `<ModellingRule Id="…">` element, attribute-form references (`ReferenceType`/`TypeId`/
`TargetId`), `UriIndex`/`Alias` on `<Uri>` entries, a `<NamespaceMetadata>` header element,
`DefinitionType`/`EnumeratedValues` in definitions, a `<UAInterface>` element, and a `srv=` part
that is a URI rather than a table index (`srv=http://…;ns=1;i=5`) — the written form always uses
the index. A `ns=` or `srv=` index the document does not declare is *not* accepted silently: it
raises `ParseError`.

Reduced namespaces are marked by rewriting the `IsNamespaceSubset` property of their
`NamespaceMetadataType` object (OPC 10000-5) to `true`; for a namespace not reduced by this run,
the source property value is left unchanged. If the metadata object or its property was not
retained nothing is invented — the
validator warns and the namespace report says the marker is missing. **A reduced namespace is
exactly what gets exported**; nothing blocks on incompleteness as such. What blocks is a
validation **error**: `pipeline.run()` raises `ValidationError` before the exporter runs
(`pipeline.py:121-125`), and `ExportError` comes only from `exporter/nodeset.py` when
`export(validate=True)` is called directly (`exporter/nodeset.py:656-659`). A namespace whose
metadata object was dropped is a warning and exports fine — `out/reduced.xml` marks both
namespaces `IsNamespaceSubset=true` and validates with no findings.

## Extensibility seam

Because retention consumes `NodeRole`, `ModellingRule` and `DependencyKind` — never XML — new
policy kinds plug into `policies/registry.py` without touching parser, graph or exporter:
Conformance Unit policies, Profile policies, Facet policies, Feature Set policies. The graph
engine stays untouched for all of them.
