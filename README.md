# OPC UA NodeSet Linker

A **semantic dependency linker and tree shaker for OPC UA Information Models**.

[![Publish to PyPI](https://github.com/AndreasHeine/opcua-nodeset-linker/actions/workflows/publish.yml/badge.svg)](https://github.com/AndreasHeine/opcua-nodeset-linker/actions/workflows/publish.yml) [![Dependency Graph](https://github.com/AndreasHeine/opcua-nodeset-linker/actions/workflows/dependabot/update-graph/badge.svg)](https://github.com/AndreasHeine/opcua-nodeset-linker/actions/workflows/dependabot/update-graph) [![CI](https://github.com/AndreasHeine/opcua-nodeset-linker/actions/workflows/ci.yml/badge.svg)](https://github.com/AndreasHeine/opcua-nodeset-linker/actions/workflows/ci.yml) [![Coverage](https://codecov.io/gh/AndreasHeine/opcua-nodeset-linker/branch/main/graph/badge.svg)](https://codecov.io/gh/AndreasHeine/opcua-nodeset-linker)

It loads one or more `UANodeSet` XML files, builds the *semantic* graph of the
information model, computes the dependency closure of user-selected root nodes, drops
everything unreachable, and writes a reduced NodeSet that stays semantically valid for its
declared dependencies — while explaining **why each dependency-reached node exists**. Models
listed under `external:` are intentionally not bundled; the importing application must provide them.

It is deliberately **not** an XML minifier: XML is treated as a serialization format
only. All decisions are made on the semantic model.

## Install

Install the published package from PyPI:

```bash
python -m pip install opcua-nodeset-linker
```

The optional `asyncua` extra installs the OPC UA stack used by the demo server
(`examples/demo1/server.py`), which serves a reduced NodeSet so you can check the result against a
real server. The library itself does not depend on it:

```bash
python -m pip install "opcua-nodeset-linker[asyncua]"
```

Requires Python ≥ 3.12.

### Development install

```bash
python -m venv .venv
.venv\Scripts\activate      # Windows  (source .venv/bin/activate on Linux/macOS)
python -m pip install -e ".[dev]"
```

## Quick start

```bash
nodeset-linker analyze examples/config.yaml     # reports only, writes nothing
nodeset-linker link   examples/config.yaml      # reduces and writes the NodeSet
nodeset-linker link   examples/config.yaml -o out/other.xml   # override the output path
nodeset-linker explain --node JobStateEnum examples/config.yaml
nodeset-linker stats  out/reduced.xml
nodeset-linker validate out/reduced.xml                          # semantic checks only
nodeset-linker validate out/reduced.xml --schema tests/data/schema/UANodeSet.xsd
nodeset-linker validate out/reduced.xml --best-practices        # OPC 11030 findings, never gating
nodeset-linker diff old.xml new.xml                              # what changed semantically
nodeset-linker diff old.xml new.xml --json --fail-on-breaking    # CI gate
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

### Merged or split export

The legacy `output:` setting remains supported. Alternatively, replace it with:

```yaml
export:
  mode: merged
  file: out/reduced.xml
```

To keep retained declarations in separate namespace-owned NodeSets, use:

```yaml
export:
  mode: split
  directory: out
```

Split export writes one XML file per namespace with retained nodes, plus `manifest.json`.
It changes serialization only: reduction, retention and semantic validation are unchanged.
Nodes are never duplicated or reassigned; cross-file references remain intact. External
models are still supplied by the consumer and do not get output files. Namespace 0 gets its
own `UA.xml` when its declarations are retained rather than externalized.

The destination must be absent or empty; split export refuses non-empty directories.
`--output` overrides the file in merged mode or the directory in split mode. An omitted
destination defaults to `out/reduced.xml` for an `export:` merged block and `out` for split.
Without either `output:` or `export:`, linking continues to write nothing. Configuring both
is an error.

The manifest identifies namespaces by URI, maps them to safe, deterministic filenames, and
lists direct namespace uses (including external dependencies). See the
[Usage Guide](docs/usage-guide.md#split-namespace-export) for the manifest format and bundle
validation. [Demo 2](examples/demo2/README.md) applies split export to demo1's eight
ShowCaseMachineTool NodeSets and includes the seven generated namespace files.
[Demo 3](examples/demo3/README.md) reduces original and modified showcase inputs with
two configurations, then demonstrates semantic diff, impact analysis, and a breaking-change gate.
[Demo 4](examples/demo4/README.md) changes a standard Machinery Identification
property's datatype and tightens another property's modelling rule from Optional to
Mandatory, both naturally retained by the showcase root.

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
  namespaces still present, and `<RequiredModel>` children only for dependencies the written file
  actually uses: a requirement whose namespace is neither shipped nor referenced is omitted rather
  than presented as a dependency nothing in the document resolves
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

`--best-practices` adds the lint pass over OPC 11030 *Best Practices for Companion Models*: naming
(§2.1 - §2.6), BrowsePath uniqueness once case is ignored (§2.1.4), the file name and
`NamespaceUri` conventions (§2.4, §2.5), StatusCode definitions outside the base specification
(§4), the optional-field limit and the AllowSubTypes/optional combination of a Structure (§7.9),
mandatory self-containing declarations (§7.10), `NamespaceIndex` values written into text (§13.3),
and extra fields on audit-event subtypes (§15). Findings are advisory or information and carry the
rule slug the diff report uses as well, so `BP-Naming-PascalCase` names one rule in both places.
None of them change the exit code: a best-practice violation is not a modelling error.

The written file is checked against the official schema in `tests/data/schema/UANodeSet.xsd`
(`tests/test_schema_compliance.py`, and `tests/test_compliance.py` compares the semantics of a
subset before and after export). `scripts/check_official_nodesets.py` runs the real OPC UA, DI,
Machinery and IA nodesets through the same checks: it downloads from a ref you can pin with
`--ref`, prints each file's SHA-256 and its declared `Model` version/publication date plus the
schema version, and exits non-zero if *any* source fails the schema or the semantic checks — a
source that does not validate is a failed run, not a footnote. Schema validation alone is not
OPC UA conformance: it says the XML matches `UANodeSet.xsd`, nothing more.

## Semantic diff (`diff`)

```bash
nodeset-linker diff old.xml new.xml                    # human-readable report on stdout
nodeset-linker diff old.xml new.xml --impact           # which nodes depend on the changes
nodeset-linker diff old.xml new.xml --impact-limit 20  # how many impacted nodes to list
nodeset-linker diff old.xml new.xml --context consumer.xml  # count consumers outside these files
nodeset-linker diff old.xml new.xml --breaking         # only what a consumer could break on
nodeset-linker diff old.xml new.xml --no-advisory      # drop advisory findings from the report
nodeset-linker diff old.xml new.xml --reduced-export   # the new file is a Linker export
nodeset-linker diff old.xml new-dir/                   # a split-export bundle is one model
nodeset-linker diff old.xml new.xml --namespace Machinery      # report one namespace only
nodeset-linker diff old.xml new.xml --exclude-namespace UA     # drop a namespace from both sides
nodeset-linker diff old.xml new.xml --node-class ObjectType    # report one node class only
nodeset-linker diff old.xml new.xml --json             # machine-readable
nodeset-linker diff old.xml new.xml --markdown         # PR comment
nodeset-linker diff old.xml new.xml --html -o diff.html
nodeset-linker diff old.xml new.xml --fail-on-breaking # exit 1 when a breaking change exists
nodeset-linker diff old.xml new.xml --fail-on-advisory # gate advisory findings too
nodeset-linker diff old.xml new.xml --waive BP-Namespace-VersionDrift  # accept one finding
nodeset-linker diff old.xml new.xml --waiver-file waivers.json         # accept findings in bulk
nodeset-linker diff old.xml new.xml --release-verdict --baseline manifest.json
```

The comparison runs on the **semantic models**, never on XML. Node identity is
`(namespaceUri, identifier, serverUri)`, so reordered documents, re-formatted output, comments,
and a reassigned `NamespaceIndex` produce `No semantic differences found.` — a node that only lost
its identifier is reported separately as a *renumbered* node, not as add plus remove, while a node
that gains or loses a `ServerUri` is a different node and reports as add plus remove.

Each side is either one NodeSet file or a **split-export bundle**: a directory holding
`manifest.json` (or that manifest itself). Its namespace files are merged into one model, so a
namespace-split release compares against the model it replaced without being merged by hand first.

`--namespace`, `--exclude-namespace` and `--node-class` scope the report. They filter *both*
sides, so a namespace or node class left out is simply not reported rather than looking like a
removal, and the release metadata of a filtered namespace is filtered with it (§11.1). A
namespace filter takes the NamespaceUri or the alias the report shows (the declared `Alias`, or
the tail of the URI); a name neither side declares fails instead of printing an empty report.

The report lists added nodes, removed nodes, and per-node modifications: BrowseName, DisplayName
and localization, Description, references (`HasSubtype`, `HasComponent`, `HasInterface`,
`HasTypeDefinition`, `HasEncoding`, `Organizes`, …), modelling rules
(`Mandatory` / `Optional` / `MandatoryPlaceholder` / `OptionalPlaceholder`), variable datatype,
`ValueRank` and `ArrayDimensions`, and datatype definitions — Structure fields, Enum and OptionSet
values, Union alternatives, field metadata and member order. Namespace totals are reported per
namespace:

```
Machinery (http://opcfoundation.org/UA/Machinery/)
  Nodes: 18 → 19 (+1)
  References: 30 → 32 (+2)
  DataTypes: 6 → 6
```

An inconsistent re-declaration of one identifier inside one input does not abort the run: the
merge keeps the first declaration and the report names both documents as an advisory
`BP-Merge-Conflict` finding (§3.2.17 — one identifier is one declaration). A reduction never
tolerates a conflict; only a diff does, because a review needs the comparison, not a crash.

The HTML report folds each node section into one collapsible block per namespace, so a release
spanning dozens of namespaces stays readable; console and Markdown stay flat.

`--impact` walks the dependency graph backwards over the structural edges — the same graph the
linker uses, so folders and `Organizes` navigation are excluded — and prints the dependent nodes
plus a total. Removed nodes are measured in the old model, where their consumers still exist;
modified and renumbered nodes are measured in the new model. Added nodes report no impact: nothing
depends on them yet. `--context <file>` adds consumer models whose dependencies are counted too —
the spec whose nodes this release externalized, or a sibling companion spec.

```
Removed nodes
  JobDataType [DataType, ns=Machinery, i=2001]
    Impact: MachineStatusStructure

Breaking changes
  JobDataType [i=2001]: DataType removed
    Affects 1 node
```

Every finding carries a **severity** and a **rule id**, so a report says which rule fired and
whether it blocks a release:

| Severity | What it means | Gate by default |
| --- | --- | --- |
| **BREAKING** | a consumer that imports the old model could fail | `--fail-on-breaking` exits 1 |
| **ADVISORY** | a best-practice rule worth reviewing before shipping | no gate; `--fail-on-advisory` opts in |
| **INFO** | a fact about the change, nothing to fix | no gate |

**Breaking** — identifier moved (`BP-NodeId-Immutable`), node renamed or retyped
(`BP-Node-Renamed`, `BP-NodeClass-Immutable`), BrowseName or SymbolicName changed
(`BP-BrowseName-Immutable`), declaration removed including an optional one
(`BP-Declaration-Removed`, `BP-Declaration-Removed-Optional`), datatype of a variable changed
(`BP-DataType-Immutable`), hierarchy moved (`BP-Hierarchy-Move`, `BP-TypeDefinition-Moved`,
`BP-RT-Hierarchy-Moved`), modelling rule tightened (`BP-ModellingRule-Tightening`), method
signature changed (`BP-MethodSignature-Immutable`), enum or OptionSet value added, changed or
removed (`BP-EnumValue-*`, `BP-OptionSetBit-*`), Structure field added, changed or removed
(`BP-StructureField-*`, `BP-Member-*`), Union alternative changed (`BP-UnionAlternative-*`),
`ValueRank`/`ArrayDimensions` changed (`BP-ValueRank-Immutable`, `BP-ArrayDimensions-Immutable`),
`Symmetric` changed (`BP-ReferenceType-Symmetric-Immutable`), contract or mandatory reference
changed (`BP-Reference-Contract-Removed`, `BP-Reference-MandatoryChild-*`,
`BP-Reference-OptionalChild-Removed`, `BP-Reference-Removed`), `NamespaceUri` changed
(`BP-NamespaceUri-Changed`), `IsAbstract` narrowed, `MaxStringLength` reduced, or a namespace
that is already published carrying breaking findings (`BP-BreakingInReleasedNamespace`).

**Advisory** — an optional declaration made mandatory (`BP-Declaration-Added-Mandatory`), a
subtype or hierarchy insertion (`BP-Subtype-Inserted`, `BP-Hierarchy-Insertion`), an interface
added without new mandatory declarations (`BP-Interface-Added`), member order relative to an
encoding (`BP-FieldOrder-Encoding`, `BP-AlternativeOrder-Encoding`), a replaced type
(`BP-Type-Replaced`), namespace metadata drift (`BP-Namespace-VersionDrift` on two version
claims that name *different* releases, `BP-Namespace-ModelVersionSyntax` on a `ModelVersion` that
is not valid SemVer, `BP-Namespace-MetadataConflict`, `BP-Namespace-ModelPrecedence`), naming and
file-name
conventions (`BP-Naming-PascalCase`, `BP-Filename-Convention`), Conformance Unit and profile
changes (`BP-CU-*`, `BP-Profile-Removed`), deprecation without a new namespace, a guessed field
rename (`BP-Field-Rename-Guess`), a duplicate member, `InverseName`, extensions and value
dependencies, and an identifier two documents declare inconsistently (`BP-Merge-Conflict`).

**INFO** — text a consumer reads but never parses (`BP-DisplayName-Meaning`,
`BP-Description-Meaning`, `BP-Documentation-Meaning`, `BP-Member-Text`), a SymbolicName that only
re-identifies the node (`BP-SymbolicName-Identity`), server-specific attributes and instance data
(`BP-Attributes-ServerSpecific`, `BP-Value-InstanceData`), a modelling rule relaxed
(`BP-ModellingRule-Relaxing`), navigation and optional references added or removed
(`BP-Reference-Navigation-*`, `BP-Reference-OptionalChild-Added`), an added declaration, subtype
or CU (`BP-Declaration-Added`, `BP-Interface-Added` in a compatible extension, `BP-CU-Added`),
and namespace metadata (`BP-Namespace-Version`, `BP-Namespace-PublicationDate`,
`BP-Namespace-ModelVersion`, `BP-Namespace-RequiredModel`, `BP-Namespace-XmlSchema`,
`BP-Namespace-Bump`, `BP-Namespace-Alias`).

Removing an ordinary optional component, or an `Organizes`/`HasDescription` reference, is not
breaking; neither is a metadata-only version bump. A lint finding is never BREAKING: a
best-practice violation is advice, not a modelling error.

The JSON form is the CI contract; every entry carries its URI identity and, with `--impact`
applied, how many nodes depend on it:

```json
{
  "schema_version": "1.0",
  "old": "old.xml", "new": "new.xml",
  "summary": {"old_nodes": 18, "new_nodes": 18, "added_nodes": 1, "removed_nodes": 1,
              "modified_nodes": 0, "renumbered_nodes": 0, "breaking_changes": 1,
              "advisory_changes": 2, "advisory_nodes": 1, "info_nodes": 3,
              "namespace_changes": 1, "identical": false, "waived_changes": 0},
  "added_nodes": [], "removed_nodes": [], "modified_nodes": [],
  "renumbered_nodes": [], "namespace_changes": [], "breaking_changes": [],
  "advisory_changes": []
}
```

Keys are only ever added, never dropped or renamed, and `"schema_version"` says which shape a
consumer is reading. A split-export bundle's `manifest.json` carries its own `"schema_version"`
for the same reason. A waived finding gains `"waived": true` and
`"waived_by"`; a run that used waiver flags adds the top-level `"waivers"` block
(`accepted_breaking`, `waived_changes`, `ignored`). Without waiver flags nothing extra appears.

CI gate:

```yaml
- run: nodeset-linker diff old.xml new.xml --json --fail-on-breaking
```

Waivers keep a finding visible and mark it; only the gate consults the marker. INFO and ADVISORY
findings can be waived, BREAKING cannot:

```bash
nodeset-linker diff old.xml new.xml --waive BP-Naming-PascalCase        # one rule, no reason
nodeset-linker diff old.xml new.xml --waiver-file waivers.json          # scoped, with reasons
nodeset-linker diff old.xml new.xml --accept-breaking --fail-on-breaking  # acknowledged release
```

`waivers.json` is a JSON array (or `{"waivers": [...]}`) of `{rule, namespace?, node?, reason}`;
`namespace` matches the URI or alias, `node` the BrowseName or identifier. A waiver naming a
breaking rule is reported as ignored, and `--accept-breaking` marks every breaking finding and
prints the acknowledgement in the report header — it opens the gate but never makes a published
`NamespaceUri` reusable. `--release-verdict --baseline manifest.json` answers whether consumers
can keep importing the namespace; a breaking change there means publishing a new namespace under
`V2/` (OPC 11030 §11.3).

Missing files and unparsable nodesets are reported and exit 1, like every other command.

## Behaviour changes

Changes to how the tool reports, compared with earlier releases — per-release notes live in
[CHANGELOG.md](CHANGELOG.md):

- Severity is a three-bucket model (BREAKING / ADVISORY / INFO) with a rule id and a paper-grounded
  rationale on every finding; the old flat "breaking" list is gone.
- `--fail-on-breaking` gates on BREAKING only; ADVISORY and INFO never gate unless
  `--fail-on-advisory` is passed. Lint findings from `--best-practices` are ADVISORY/INFO and never
  change an exit code.
- A moved identifier is reported once as `BP-NodeId-Immutable`, not as remove plus add, and its
  children stay attached; a renumbered release now fails `--fail-on-breaking`.
- Namespace metadata findings (`BP-Namespace-Version`, `BP-Namespace-VersionDrift`, …) are reported
  on the namespace entry; a metadata-only version bump is INFO.
- Version claims are compared on their numeric core, not their spelling: OPC spells one release
  twice (`Version="1.04.0"` display, `ModelVersion="1.4.0"` SemVer — OPC 10000-6 Annex F.2, OPC
  10000-5 §12.41), so a re-spelled release reports nothing and `BP-Namespace-VersionDrift` only
  fires on two claims naming different releases. It stays a manual-review hint and never gates
  `--fail-on-breaking`.
- JSON keys are additive only: new severity counts, `waived`/`waived_by` and the `waivers` block.
- `--waive`, `--waiver-file` and `--accept-breaking` mark findings without removing them.

## Architecture

See `docs/architecture.md`, `docs/dependency-resolution.md` and
`docs/reduction-example.md` — a complete walkthrough of one reduction across two NodeSets
with cross-namespace dependencies, node by node.
For a practical start-to-finish workflow, see the [Usage Guide](docs/usage-guide.md).
Concept documents that are not implemented yet live in `docs/semantic-diff.md` (diff gaps and
roadmap) and `docs/ui-concept.md` (proposed browser-driven, guided, mobile-first interface —
preview tier in JS, exported artifact and CI verdict from the Python engine).
The diff's breaking-change rules derive from the OPC UA best-practices whitepaper (OPC 11030,
source text in `docs/OPC-11030-v1.03.03.md`); each finding cites its clause, and
`docs/best-practices-alignment.md` reconciles every clause of that paper against the implemented
verdict (aligned / stricter / looser / not checked), while
`docs/best-practices-implementation-plan.md` is the work-plan for closing the gaps it found. A
clickable mockup of that interface: `docs/ui-mockup-browser.html` (a wizard you can drive entirely
in the browser: import real `UANodeSet` files, configure roots/mode/external models, analyse,
explain, diff, download the reduced NodeSet — simplified against the Python engine).
`docs/UI-Mockup1.png` … `docs/UI-Mockup7.png` are the screens of that wizard, one per step.

```
nodeset_linker/          # the package; tests live at the repository root
  cli/        config/     parser/      repository/   graph/       resolver/
  retention/  policies/   validator/   exporter/     reports/     explain/
  models/     utils/      pipeline.py
  diff/       # semantic model comparison, sits above repository/ and graph/
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

The automated suite uses dedicated fixtures under `tests/data`, not the editable files
under `examples`. Run the demo commands documented in each example manually when needed;
demo configurations, generated outputs, and server helpers are not test dependencies.

GitHub Actions runs the test suite with coverage on Python 3.12 and 3.13, and runs Ruff
and mypy on Python 3.12. The XML coverage report is available as a workflow artifact and is
uploaded to Codecov from Python 3.13. Configure the `CODECOV_TOKEN` repository secret, or enable
tokenless uploads for this public repository in Codecov; the badge updates after the first
successful upload.
