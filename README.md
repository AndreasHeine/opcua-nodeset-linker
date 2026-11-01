# OPC UA NodeSet Linker

A **semantic dependency linker and tree shaker for OPC UA Information Models**.

It loads one or more `UANodeSet` XML files, builds the *semantic* graph of the
information model, computes the dependency closure of user-selected root nodes, drops
everything unreachable, and writes a reduced NodeSet that stays semantically valid for its
declared dependencies — while explaining **why each dependency-reached node exists**. Models
listed under `external:` are intentionally not bundled; the importing application must provide them.

It is deliberately **not** an XML minifier: XML is treated as a serialization format
only. All decisions are made on the semantic model.

## Install

```bash
python -m venv .venv
.venv\Scripts\activate      # Windows  (source .venv/bin/activate on Linux/macOS)
pip install -e .[dev]
```

Requires Python ≥ 3.12.

## Quick start

```bash
nodeset-linker analyze examples/config.yaml     # reports only, writes nothing
nodeset-linker link   examples/config.yaml      # reduces and writes the NodeSet
nodeset-linker link   examples/config.yaml -o out/other.xml   # override the output path
nodeset-linker explain --node JobStateEnum examples/config.yaml
nodeset-linker stats  out/reduced.xml
nodeset-linker validate out/reduced.xml                          # semantic checks only
nodeset-linker validate out/reduced.xml --schema tests/data/schema/UANodeSet.xsd
```

## Configuration (YAML, validated with Pydantic)

```yaml
nodesets:
  - tests/data/ua_base.xml
  - tests/data/machinery.xml

mode: STRICT            # STRICT | OPTIONAL_AWARE | RUNTIME | CUSTOM

roots:
  - nodeid=nsu=http://opcfoundation.org/UA/Machinery/;i=2200
  - browseName=IMachineStatus
  - typeDefinition=MSSControllerType
  - namespaceUri=http://opcfoundation.org/UA/Machinery/

output: out/reduced.xml

external:               # models the consumer provides — referenced, never shipped
  - http://opcfoundation.org/UA/DI/

validate_before_export: true   # default — exporter check; pipeline validation is always on
mark_namespace_subsets: true   # default — reduced namespaces get IsNamespaceSubset=true

custom:                 # only used with mode: CUSTOM
  retain:
    - nodeClass=DataType
    - namespace=*Machinery*      # namespace rules match the namespace URI, not an alias
  exclude:
    - browseName=MachineDiagnostics
```

Keys of a custom rule are `namespace`, `browseName`, `nodeClass` and `modellingRule`; values may
contain globs, an unknown key is rejected. `retain:` rules are checked before `exclude:` rules, so
a retain rule naming the same node as an exclude rule wins. A retain rule can also bring in nodes
the original roots never reached; their own dependency closures are then included.

An `exclude:` rule only drops nodes that are droppable — a hard dependency (a base type, a
mandatory placeholder, an explicitly `Mandatory` node) is never removed by a rule.
Legal links to omitted optional children are pruned. An exclusion that breaks a required
non-navigation dependency blocks export; policy-dropped required targets are repaired instead.

Root selectors accept `nodeid=`, `expandedNodeId=`, `browseName=`, `namespaceUri=`,
`typeDefinition=` and globs (`browseName=*Type`); selector keywords are case-insensitive. A bare
identifier (`nsu=http://opcfoundation.org/UA/Machinery/;i=2200`, `i=24`) is also accepted. Note that
`ns=<index>` resolves through the namespace table of the *merged* model, where the first loaded
document wins for a shared index — prefer the URI form.

## Retention modes

| Mode | Behaviour |
| --- | --- |
| `STRICT` | Keeps every dependency required by OPC UA semantics (mandatory *and* optional). |
| `OPTIONAL_AWARE` | Same retention as `STRICT`; the mandatory/optional split is classified in the reports in every mode. |
| `RUNTIME` | Omits Optional/OptionalPlaceholder ownership branches unless explicitly selected or needed elsewhere. Prunes their links; preserves mandatory declarations and required dependencies. |
| `CUSTOM` | User-defined `retain:` / `exclude:` rules; retain is checked first, hard dependencies and user exclusions are never repaired. |

## What is preserved

Reduction uses directional dependencies by default: a datatype does not pull other variables
using it, a type/interface does not pull its other instances, and a base does not pull unrelated
subtypes. Parents and folders needed for navigation are kept without expanding their unrelated
children. Namespace metadata is retained explicitly. See
[Dependency Resolution](docs/dependency-resolution.md) for the precise rules and limitations.

* selected root nodes and their full transitive closure
* inheritance chains (`HasSubtype`) — a broken chain is never produced
* interfaces (`HasInterface`), type definitions (`HasTypeDefinition`)
* data types incl. `StructureDefinition`, `EnumDefinition`, `UnionDefinition`, `OptionSet`
  with fields, nested structures, enums and base types
* `DataTypeField` attributes and child metadata, including `SymbolicName`, `ValueRank`,
  `ArrayDimensions`, `MaxStringLength`, `AllowSubTypes`, `IsOptional`, `Value`, `DisplayName`
  and `Description`; schema-default values are preserved semantically but omitted from the
  output, even if the source explicitly wrote them
* qualified browse names: a `BrowseName="1:Foo"` is resolved against the namespace table of
  *its own* document and stored as that namespace URI, so the name keeps its namespace after
  reduction even when the node's NodeId lives in a different one
* server-qualified identifiers (`ServerUris` / `srv=<index>`): the table position is resolved
  to the server URI at parse time and re-indexed into the written `ServerUris` table
* method signatures: `InputArguments` / `OutputArguments` and their argument datatypes
* Binary / XML / JSON encodings of retained custom data types — the standard
  `Default Binary` / `Default XML` / `Default JSON` objects of the UA namespace too
* namespace URIs, models, required models and their versions/publication dates
* references into an externalized model (`external:`) — its declarations are deliberately *not* in
  the file, see *External dependencies*
* namespace metadata objects (`NamespaceMetadataType`) with `NamespaceUri`,
  `NamespaceVersion` and `IsNamespaceSubset`
* cross-namespace dependencies (detected automatically)

NamespaceIndexes are **never used as identity**; node identity is
`(namespaceUri, identifier, serverUri)`. The parser resolves indexes to URIs, though a
`QualifiedName` retains its source index as a hint alongside the resolved URI. Index 0 is
**always** the OPC UA namespace — an
identifier without an `ns=` prefix means namespace 0 to every importer, so a custom namespace is
never given index 0, even when no OPC UA node was retained. Every used non-UA namespace (NodeId
namespaces, reference targets *and* the namespaces of qualified browse names) is numbered from 1
in URI order, and every identifier and browse name is rewritten to that index; servers are
numbered the same way in a `ServerUris` table.

An `ns=<index>` or `srv=<index>` the document does not declare is a parse error, not a silent
fallback to 0 or to the OPC UA namespace.

Loading two documents that declare the *same* identifier differently fails with
`DuplicateNodeError`; identical re-declarations of one node are merged.

## Reduced namespaces are marked as subsets

OPC 10000-5 defines no attribute for "this file is a subset" — the flag is the
`IsNamespaceSubset` property of the namespace's metadata object. So the exporter *rewrites
that property*: a namespace that lost nodes is written with `IsNamespaceSubset=true`. For a
namespace not reduced by this run, the source value is left unchanged. If the metadata object
of a reduced namespace was not retained, nothing is invented and the namespace report says so (warning
`NAMESPACE_METADATA_MISSING`). Set `mark_namespace_subsets: false` to keep source values.

## External dependencies (`external:`)

A model can be *needed* without being *shipped*: the application importing the reduced file already
has the OPC UA namespace, or a standard model such as DI. Listing its ModelUri under `external:`
tells the linker to leave that model's node declarations out of the written document while keeping
everything that points at it. Entries are namespace URIs (`http://opcfoundation.org/UA/DI/`); a
namespace index or an identifier (`ns=1`, `i=1000`, `index:2`) is rejected by the config, because an
index is local to one document.

What the run does with such a model:

* every node of it is omitted from the export, **even when the dependency closure reached it**
* a root selector that matches one of its nodes is rejected — a root cannot live in a dependency the
  file does not ship (`root … belongs to externalized model …`)
* every reference to it is written: `<Reference>` targets, the `DataType`, `ParentNodeId` and
  `MethodDeclarationId` attributes, `HasTypeDefinition`, a custom `ReferenceType`, and the `DataType`
  of structure fields and union alternatives. Nothing is silently dropped; the identifiers are
  rewritten to the namespace index the output gives that model (`ns=2;i=1000`)
* its namespace URI stays in `<NamespaceUris>`. If the loaded model metadata already declares
  it as a required model and the namespace remains used, the matching `<RequiredModel>` is kept
  under the models that reference
  it, with `Version`, `PublicationDate`, `XmlSchemaUri` and `ModelVersion` only when known. The
  linker does not invent a missing `RequiredModel`; it reports `EXTERNAL_MODEL_NOT_DECLARED`.
  A version is never invented; an unknown one is reported as `EXTERNAL_MODEL_VERSION_UNKNOWN`
* its namespace is not marked as a subset — it is not part of the file
* reports keep it apart: `external_nodes` in the statistics, `external_models` and
  `external_references` in the namespace report, and `analyze`/`link` print an
  *External dependencies* panel naming the URIs

Statistics distinguish actual reduction from externalization:
`original_nodes = retained_nodes + removed_nodes + external_nodes`.
`retained_nodes` counts the final nodes to be exported, including CUSTOM retain additions.
`removed_nodes` counts non-external nodes removed by reachability, policy or successful custom
exclusions; external declarations are counted only in `external_nodes`.
`reduction_percent = removed_nodes / (original_nodes - external_nodes) * 100`
(zero when no non-external input nodes exist). Externalization alone is therefore 0% reduction.
Only loaded declarations contribute to `external_nodes`; declaring an unloaded external model
does not change the counts. The JSON field is omitted when its count is zero.

The OPC UA namespace is a dependency, but not the same case as DI. Index 0 is reserved for it, so
`external: ["http://opcfoundation.org/UA/"]` writes **no `<Uri>` entry** for it and leaves its
identifiers unqualified (`i=11715`, `ReferenceType="HasSubtype"`); the dependency is stated as
`<RequiredModel ModelUri="http://opcfoundation.org/UA/">` only when existing model metadata
declares that requirement. An ordinary model gets a `<Uri>` entry and an index of its own.
Version metadata never goes on `<Uri>` — the schema allows no attributes there.

`external:` is not an exclude rule and not a reduction mode, and it does not relax validation. A
reference into a namespace that is neither contained in the file nor listed as external is still an
error, and an explicit `exclude:` rule that breaks the model still fails the run. A model that is
referenced but never declared as a `RequiredModel` is reported as `EXTERNAL_MODEL_NOT_DECLARED`; the
linker does not invent that declaration, so the file it produces legitimately fails a later
`validate`. `EXTERNAL_MODEL_NOT_LOADED` says the model's own declarations were never read, so they
could not be checked.

Declaring a dependency does **not** make the importing application independent of that model: it has
to load the required model itself, together with this file. The OPC UA specification states the
dependency in `RequiredModel`, but there is no guarantee that every importer resolves it on its own.
The file says what it needs — it cannot supply it.

## Export format (spec conformant)

The written document is a plain `UANodeSet` in
`http://opcfoundation.org/UA/2011/03/UANodeSet.xsd` — no project-local attributes:

* `<NamespaceUris><Uri>` for every namespace the file actually uses, numbered from 1 (index 0 is
  the OPC UA namespace and is never listed), and `<ServerUris><Uri>` when a retained identifier is
  qualified against another server
* `<Models>` with `ModelUri`, `XmlSchemaUri`, `Version`, `PublicationDate`, `ModelVersion` for the
  namespaces still present, and the `<RequiredModel>` children as they were loaded
* `<Aliases>` for the UA reference types the file refers to by name, sorted by identifier
* node ids of namespace 0 bare (`i=24`), others as `ns=1;i=2001`, server-qualified as
  `srv=1;ns=1;i=2001`
* browse names qualified with the index of *the name's* namespace (`BrowseName="1:JobDataType"`),
  bare when the name belongs to the OPC UA namespace
* modelling rules as `<Reference ReferenceType="HasModellingRule">i=78</Reference>`
* interfaces as `UAObjectType` — `UAInterface` has no schema element; `IsAbstract="true"` is kept
  from the source node, not invented
* bases written the official way: `<Reference ReferenceType="HasSubtype" IsForward="false">`

Older spellings are still *read* (`UriIndex`/`Alias` attributes, `<NamespaceMetadata>` header
elements, attribute-form `<References ReferenceType="…" TargetId="…" TypeId="…">`, the element form
`<ModellingRule Id="…">`, the legacy rule ids 290/387, `DefinitionType`, `UAInterface`), but never
written. Because reduced files keep references into namespace 0, importers load them together with
the standard nodeset — a file that dropped `i=11715` or a base type would not import cleanly, and
validation blocks that. A file that externalized a model needs that model loaded the same way.

## Explainability

Every node reached by dependency closure carries a `RetentionReason` with the concrete
dependency path:

```
JobStateEnum retained because:
  MSSControllerType -> MachineType -> JobManagementType -> CurrentJob -> JobDataType -> JobStateEnum
  (origin: MSSControllerType, dependency kind: datatype)
```

The path is the first breadth-first route that reached the node. Inverse-only child declarations
can be traversed from their parent, but a field datatype never pulls its users.
CUSTOM-retained nodes also have closure reasons and their dependencies are traversed.

## Validation

Linking validates the reduced subset against the loaded sources with nine core semantic checks:
reference targets, reference type declarations, type definitions, datatype references,
ReferenceType hierarchy, subtype hierarchy including cycles, encoding references (the standard
`Default Binary/XML/JSON` objects are accepted), namespace links (a required model that was not
loaded is a warning), and namespace subset markers. **Errors block the run** — an invalid NodeSet
is never written; warnings are shown and the export proceeds. A separate external-dependency
check reports whether external models are declared, loaded, used, and versioned.

A target this file does not contain is accepted **only** when it belongs to a model listed under
`external:`; a reference into any other missing namespace is an error. Four warnings describe the
dependency itself: `EXTERNAL_MODEL_UNUSED`, `EXTERNAL_MODEL_NOT_DECLARED`,
`EXTERNAL_MODEL_NOT_LOADED` and `EXTERNAL_MODEL_VERSION_UNKNOWN`.

`nodeset-linker validate <file>` checks a written file on its own: without the source documents
the subset-marker check has nothing to compare against, so that command runs the eight model
checks. A `RequiredModel` whose declarations are not in the file is read as an external dependency:
references into it are accepted, references into a namespace the file neither contains nor declares
are errors. It prints `validation passed - no findings` and exits non-zero on any error. Adding
`--schema tests/data/schema/UANodeSet.xsd` *also* validates the XML against the schema and prints
which of the two checks ran; a schema violation exits non-zero too. Without `--schema` the command
is semantic validation only and says nothing about the schema.

The written file is checked against the official schema in `tests/data/schema/UANodeSet.xsd`
(`tests/test_schema_compliance.py`, and `tests/test_compliance.py` compares the semantics of a
subset before and after export). `scripts/check_official_nodesets.py` runs the real OPC UA, DI,
Machinery and IA nodesets through the same checks: it downloads from a ref you can pin with
`--ref`, prints each file's SHA-256 and its declared `Model` version/publication date plus the
schema version, and exits non-zero if *any* source fails the schema or the semantic checks — a
source that does not validate is a failed run, not a footnote. Schema validation alone is not
OPC UA conformance: it says the XML matches `UANodeSet.xsd`, nothing more.

## Architecture

See `docs/architecture.md`, `docs/dependency-resolution.md` and
`docs/reduction-example.md` — a complete walkthrough of one reduction across two NodeSets
with cross-namespace dependencies, node by node.
For a practical start-to-finish workflow, see the [Usage Guide](docs/usage-guide.md).

```
nodeset_linker/          # the package; tests live at the repository root
  cli/        config/     parser/      repository/   graph/       resolver/
  retention/  policies/   validator/   exporter/     reports/     explain/
  models/     utils/      pipeline.py
```

Dependencies point inward. The exporter is the outermost layer — it reads the semantic model and
runs the validator, and nothing below it imports upwards. The graph engine knows nothing about
XML, files or namespace indexes — which is what makes future Conformance Unit / Profile / Facet /
Feature Set support possible without redesigning it (see `policies/registry.py`).

## Development

```bash
python -m pytest --cov-report=xml  # tests with terminal and XML coverage reports
mypy src                  # strict typing
ruff check src tests scripts   # lint
python scripts/check_official_nodesets.py            # the real OPC UA/DI/Machinery/IA nodesets, end to end
python scripts/check_official_nodesets.py --ref 1.05.05   # use a separate cache for this ref
```

GitHub Actions runs the test suite with coverage on Python 3.12 and 3.13, and runs Ruff
and mypy on Python 3.12. The XML coverage report is available as a workflow artifact.
