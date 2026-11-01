# Reduction Example — Two NodeSets, Cross-Namespace Dependencies

This walkthrough shows exactly how a reduction happens, using the two example files in
`tests/data/`. Both are written in the official serialization (`UANodeSet.xsd`, namespace
`http://opcfoundation.org/UA/2011/03/UANodeSet.xsd`), so the numbers below are what a real
importer would see. Every number here was produced by running the tools on these files.

| File | Namespace(s) | Nodes | Content |
| --- | --- | --- | --- |
| `tests/data/ua_base.xml` | `http://opcfoundation.org/UA/` (index 0) | 53 | reference type hierarchy, datatype hierarchy incl. `Argument`, object/variable type hierarchy (`BaseObjectType`, `FolderType`, `NamespaceMetadataType`, `BaseVariableType`/`BaseDataVariableType`/`PropertyType`), `Root`/`Objects`/`Types`/`Namespaces` folders, UA namespace metadata object with its three properties, machine types (`BaseMachineType`, `MachineType`, `MSSControllerType`, `JobManagementType`, `UnrelatedMachineType`) and their variables |
| `tests/data/machinery.xml` | `http://opcfoundation.org/UA/Machinery/` (index 1) | 18 | Machinery datatypes and enums, encodings, `MachineStatusStructure`, interface, instance, method plus method arguments, Optional `MachineDiagnostics`, view, `UnrelatedType`, namespace metadata object with its three properties |

Both files are treated the same way — there is no "standard nodeset stays intact" rule. They
are merged into one semantic model of **71 nodes**, and reduction applies to every node in it.

## The configuration

```yaml
nodesets:
  - tests/data/ua_base.xml
  - tests/data/machinery.xml
mode: STRICT
roots:
  - browseName=MSSControllerType                    # a type declared in ua_base.xml
  - nodeid=nsu=http://opcfoundation.org/UA/Machinery/;i=2200   # an instance in machinery.xml
output: out/reduced.xml
```

Two roots, two different files. Note the second selector addresses the node by **URI**, not by
`ns=1` — indexes are per-document and would be ambiguous across two files (`ns=1` in a config
file resolves through the document-local index table, `namespaceUri=` through the URI).
`validate_before_export` and `mark_namespace_subsets` default to `true`.

## Step 1: merge (parser → repository)

The parser turns XML into domain objects; ids that carry no namespace prefix become the UA
namespace URI, and `<Uri>` table entries only matter inside their own document. The repository
merges both sets keyed by `(namespaceUri, identifier)` and models keyed by namespace URI:

```
ua_base.xml      → 53 nodes in http://opcfoundation.org/UA/
machinery.xml    → 18 nodes in http://opcfoundation.org/UA/Machinery/
merged model     → 71 nodes, namespaces {UA, Machinery}, unresolved targets: []
```

Cross-file references are already present in the sources and become graph edges:

| Source | Reference | Target file |
| --- | --- | --- |
| `ua_base.xml` CurrentJob | `DataType="nsu=http://opcfoundation.org/UA/Machinery/;i=2001"` | machinery.xml |
| `ua_base.xml` JobHistory | `DataType="nsu=http://opcfoundation.org/UA/Machinery/;i=2020"` | machinery.xml |
| `machinery.xml` JobDataType | `<Reference ReferenceType="HasSubtype" IsForward="false">i=22</Reference>` | ua_base.xml |
| `machinery.xml` MSSControllerInstance | `<Reference ReferenceType="HasTypeDefinition">i=1002</Reference>` | ua_base.xml |
| `machinery.xml` namespace metadata object | `<Reference ReferenceType="HasComponent" IsForward="false">i=11715</Reference>` | ua_base.xml |

The last one is how namespace metadata stays reachable: the metadata object hangs under the
`Namespaces` folder of the OPC UA namespace, which lives in the *other* file. The UA metadata
object (`i=15957`) declares the same reversed `HasComponent` to `i=11715` inside `ua_base.xml`.

## Step 2: semantic graph (graph layer)

Edges come from declared `<Reference>` elements plus the implicit ones XML never writes as
references — a variable's `DataType` attribute, structure fields, union alternatives. The two
files become one graph; edges crossing namespaces are ordinary edges:

```
CurrentJob(ua_base) --DataType--> JobDataType(machinery)          [kind DATATYPE]
JobDataType(machinery) --HasSubtype--> Structure(ua_base)          [kind DATATYPE]
MSSControllerInstance(machinery) --HasTypeDefinition--> MSSControllerType(ua_base)   [STRUCTURAL]
OPCUAMachineryNamespaceMetadata(machinery) --HasComponent--> Namespaces(ua_base)     [STRUCTURAL]
MachineryView(machinery) --Organizes--> MSSControllerInstance(machinery)             [NAMESPACE]
```

An edge always goes from the node that declares the reference to its target, whatever
`IsForward` says. Official documents name their base with `IsForward="false"` — the forward
direction of `HasSubtype` runs from base to subtype — and the linker reads both spellings as
"this node needs that node".

`HasSubtype` on a *datatype* is classified DATATYPE rather than INHERITANCE, so abstract bases
(`BaseDataType`, `Enumeration`) stay traversable; `HasSubtype` on object types is INHERITANCE.

A `HasModellingRule` reference is not an edge: the parser reads it into the node's modelling-rule
attribute (`Optional` on `Diagnostics` i=80, `Mandatory` on `CurrentJob` i=78), and the exporter
writes it back as a reference element.

## Step 3: closure (resolver) — BFS from both roots

Direction rules decide what gets pulled in. Only **INHERITANCE is forward-only**: `_drain`
skips backward traversal for inheritance edges and follows every other kind in both directions.

| Edge kind | forward | backward | effect here |
| --- | --- | --- | --- |
| INHERITANCE (`HasSubtype` on object types) | yes, to bases | never | bases are required; `BaseMachineType` (i=1000) and `BaseObjectType` (i=58) are retained as bases, but their other subtypes — `UnrelatedMachineType` (i=2000, inherits `BaseMachineType`) and `UnrelatedType` (ns=1;i=2500, inherits `BaseObjectType`) — are **not** pulled |
| DATATYPE (`DataType` attribute, fields, alternatives, `HasSubtype` on datatypes) | yes | yes | a variable's datatype pulls its base chain *and* the subtypes declared in the loaded documents: reaching `String` reaches `DataType`, and through `DataType` also `Structure` |
| STRUCTURAL (`HasComponent`, `HasProperty`, `HasTypeDefinition`, custom) | yes | yes | inverted references are real dependencies: `Diagnostics` points *up* to its parent, so reaching it reaches nothing new — but reaching `MSSControllerType` reaches `Diagnostics` |
| INTERFACE (`HasInterface`) | yes | yes | `IMachineStatus` is pulled from the instance; reversed, an interface pulls the instances declaring it |
| ENCODING (`HasEncoding`) | yes | yes | `JobDataType` pulls its two encodings; reversed, an encoding pulls the datatype it encodes |
| NAMESPACE (`Organizes`) | yes | yes | 4 hops: `Namespaces → Root`, `Root → Objects`, `Root → Types` (reversed `Organizes`), and `MachineryView → MSSControllerInstance` |
| REFERENCETYPE | pull of types a retained node uses by name | — | only reference types outside the UA namespace join; UA types such as `HasProperty` come with the base namespace every importer loads, and none of them is needed here |

BFS records the *first* path that reached a node; that is what `nodeset-linker explain` prints
(as a chain of browse names — the forward/backward flag lives on the step, not in the printed
line). Result — **54 retained**:

```
root MSSControllerType (ua_base.xml) → 46 nodes
  MachineType        <- MSSControllerType    [HasSubtype, inheritance]
  BaseMachineType    <- MachineType          [HasSubtype, inheritance]
  MachineName        <- MachineType          [HasProperty, structural]
  JobManagementType  <- MachineType          [HasComponent, structural]
  CurrentJob         <- JobManagementType    [HasComponent, structural]
  JobHistory         <- CurrentJob           [HasProperty, structural]
  Diagnostics        <- MSSControllerType    [HasComponent, structural, Optional rule i=80]
  String             <- Diagnostics          [DataType, datatype]
  JobDataType        <- String               [Field:JobId, backward]     ← UA → Machinery
  JobStateEnum       <- JobDataType          [Field:State, datatype]
  JobStageEnum       <- JobDataType          [Field:Progress, datatype]
  DataType           <- String               [HasSubtype, datatype]
  Structure          <- DataType             [HasSubtype, backward, datatype]
  BaseDataType, Enumeration, Number, Integer, UInteger, Int32, UInt32, Boolean,
  LocalizedText, NodeId, Argument, BaseStructureEncodingType, MachineStatusStructure,
  JobDataTypeBinary / JobDataTypeXml          [datatype / encoding hops over the same chain]
  NamespaceUri, NamespaceVersion, IsNamespaceSubset, metadata objects   [structural hops]
  Namespaces         <- metadata objects     [HasComponent, backward, structural]
  Root, Objects, Types                       [Organizes, backward]

root MSSControllerInstance (machinery.xml) → 8 nodes
  MSSControllerInstance                      (selected root)
  IMachineStatus       <- HasInterface       [interface]
  BaseObjectType       <- IMachineStatus     [HasSubtype, inheritance]   ← UA
  StartJob             <- HasComponent       [structural]
  InputArguments / OutputArguments <- StartJob  [HasProperty, structural]
  MachineDiagnostics   <- HasComponent       [structural]
  MachineryView        <- Organizes (backward)  [namespace]
  MSSControllerType    — already retained under the other root
```

`BaseObjectType` is attributed to `MSSControllerInstance`, not to `MSSControllerType`: BFS
reaches it through `IMachineStatus` before the `MachineType → BaseMachineType` chain gets to
it, and the first path wins. `Argument` likewise belongs to the type root
(`Diagnostics → String → Argument [Field:Name]`) even though the instance's
`InputArguments`/`OutputArguments` also point at it — hence the instance origin lists exactly
those 8 nodes above.

`JobDataType` is *not* recorded through `CurrentJob`: both edges exist, but the shorter
backward hop over `String` (`JobDataType`'s `Field:JobId` has datatype `String`, i=12) reached
it first. `Structure` comes from `String → DataType → Structure`, not from `JobDataType`.

Dependency kinds over the 54 retained nodes' edges: `structural 45`, `datatype 41`,
`inheritance 10`, `namespace 4`, `encoding 2`, `interface 1` — **103 semantic edges**.
`nodeset-linker stats out/reduced.xml` reports **82**: the exporter serializes only declared
references, so the 21 implicit edges (12 `DataType` attributes, 9 structure fields) never
become XML `<Reference>` elements (`exporter/nodeset.py` writes `node.references` plus the
modelling rule, nothing else). The file physically contains 86 `<Reference>` elements: the
four modelling-rule references are attributes the exporter re-emits, and the parser absorbs
them again on reload, so the reloaded model counts 82 reference edges.

## Step 4: retention (STRICT keeps everything reachable)

STRICT drops nothing, so removal = "not reachable from either root":

| Removed | File | Why |
| --- | --- | --- |
| `References`, `NonHierarchicalReferences`, `HierarchicalReferences`, `HasChild`, `Organizes`, `HasEventSource`, `HasModellingRule`, `HasEncoding`, `HasTypeDefinition`, `GeneratesEvent`, `Aggregates`, `HasSubtype`, `HasProperty`, `HasComponent`, `HasInterface` (15 UA reference types) | ua_base.xml | a reference *type declaration* is not a dependency; references carry a name or an id that an importer resolves from the standard namespace, so pulling them would undo the reduction |
| `UnrelatedMachineType` (i=2000) | ua_base.xml | it inherits `BaseMachineType` (i=1000), which *is* retained — but inheritance is never followed downward, so a retained base does not pull its subtypes, and no root reaches it |
| `UnrelatedType` (ns=1;i=2500) | machinery.xml | same reasoning, against `BaseObjectType` (i=58) |

**71 → 54 nodes, −17, reduction 23.9 %.** Per namespace: UA 37 of 53 declared, Machinery 17 of
18 declared; the report lists one cross-namespace dependency (Machinery → UA).

## Step 5: validation blocks anything incomplete

The pipeline validates the retained subset **before** exporting (`pipeline.py`): reference
targets, reference type declarations, `HasTypeDefinition` class agreement, datatype and field
datatypes, ReferenceType hierarchy, subtype chains including cycles, encoding targets,
namespace links, subset markers.

An exclusion that breaks the model never produces a file. `exclude: [browseName=Diagnostics]`
leaves `MSSControllerType`'s `HasComponent` dangling (`REF_TARGET_MISSING`), and the CLI exits
1 without writing:

```
error: 1 error(s), 0 warning(s)
```

(`ExportError("export blocked: …")` is a different path: it exists only when
`export(..., validate=True)` is called directly, which the CLI pipeline does not rely on —
the pipeline already raised `ValidationError(validation.summary())`.) Had `Structure` been
dropped, `JobDataType` would report `SUBTYPE_BASE_MISSING`. A *warning* does not block:
`REQUIRED_MODEL_MISSING` says a required model was not loaded, `NAMESPACE_METADATA_MISSING`
that a reduced namespace lost its metadata object, `NAMESPACE_NOT_DECLARED` that a used
namespace is not in the Uri table.

## Step 6: export (exporter only)

Namespace indexes are assigned here and nowhere else: UA stays index 0, Machinery gets index 1
(remaining URIs sorted), so ids stay recognisable. Nodes are emitted sorted by assigned id,
which makes the output byte-identical for the same keep set. Aliases are emitted sorted by
identifier:

```xml
  <Aliases>
    <Alias Alias="Organizes">i=35</Alias>
    <Alias Alias="HasModellingRule">i=37</Alias>
    <Alias Alias="HasEncoding">i=38</Alias>
    <Alias Alias="HasTypeDefinition">i=40</Alias>
    <Alias Alias="HasSubtype">i=45</Alias>
    <Alias Alias="HasProperty">i=46</Alias>
    <Alias Alias="HasComponent">i=47</Alias>
    <Alias Alias="HasInterface">i=17603</Alias>
  </Aliases>
```

The header keeps only the namespaces the file still uses — UA needs no `<Uri>` entry — and the
`Models` table carries the version information, including the required model:

```xml
  <Models>
    <Model ModelUri="http://opcfoundation.org/UA/" XmlSchemaUri="http://opcfoundation.org/UA/2008/02/Types.xsd"
           Version="1.05.07" PublicationDate="2026-07-30T00:00:00Z" ModelVersion="1.5.7"/>
    <Model ModelUri="http://opcfoundation.org/UA/Machinery/" XmlSchemaUri="http://opcfoundation.org/UA/Machinery/Types.xsd"
           Version="1.0.1" PublicationDate="2024-01-01T00:00:00Z" ModelVersion="1.0.1">
      <RequiredModel ModelUri="http://opcfoundation.org/UA/" XmlSchemaUri="http://opcfoundation.org/UA/2008/02/Types.xsd"
                     Version="1.05.07" PublicationDate="2026-07-30T00:00:00Z" ModelVersion="1.5.7"/>
    </Model>
  </Models>
```

`JobDataType` is written as it was declared — content the exporter copies verbatim, including
`Category` and `Documentation`, which sit between `DisplayName` and `References` in the schema.
Its datatype dependency on `String` appears only inside `Definition`, not as a reference:

```xml
  <UADataType NodeId="ns=1;i=2001" BrowseName="1:JobDataType" SymbolicName="JobDataType">
    <DisplayName>JobDataType</DisplayName>
    <Category>Machinery</Category>
    <Documentation>https://reference.opcfoundation.org/Machinery/v105/docs/5.3</Documentation>
    <References>
      <Reference ReferenceType="HasSubtype" IsForward="false">i=22</Reference>
      <Reference ReferenceType="HasEncoding">ns=1;i=2010</Reference>
      <Reference ReferenceType="HasEncoding">ns=1;i=2011</Reference>
    </References>
    <Definition Name="JobDataType">
      <Field Name="JobId" DataType="i=12"/>
      <Field Name="State" DataType="ns=1;i=2002"/>
      <Field Name="Progress" DataType="ns=1;i=2003" IsOptional="true"/>
    </Definition>
  </UADataType>
```

Nothing outside the schema is invented — no `<NamespaceMetadata>` header element, no
`UriIndex`/`Alias` attributes, no `DefinitionType`; ids of namespace 0 are written bare (`i=22`).
Field attributes are written only when the source stated them: `JobId` above carries `DataType`
and nothing else, while a definition that declares `SymbolicName`, `ValueRank`,
`ArrayDimensions`, `MaxStringLength` or `AllowSubTypes` gets them back on reload —
`tests/test_compliance.py` round-trips structure, union, enum and option-set fields with all of
them, and compares the definition before and after export. An identifier qualified against
another server (`srv=2;ns=1;i=200`) survives the same round trip with a re-indexed `ServerUris`
table rather than a dropped qualifier.

**Both namespaces were reduced, so both are marked as subsets.** The flag lives on the metadata
object, exactly as OPC 10000-5 defines it, and the exporter rewrites the property value:

```xml
  <UAVariable NodeId="ns=1;i=15005" BrowseName="IsNamespaceSubset" DataType="i=1" ParentNodeId="ns=1;i=15001">
    <DisplayName>IsNamespaceSubset</DisplayName>
    <References>
      <Reference ReferenceType="HasTypeDefinition">i=68</Reference>
      <Reference ReferenceType="HasProperty" IsForward="false">ns=1;i=15001</Reference>
    </References>
    <Value xmlns="http://opcfoundation.org/UA/2011/03/UANodeSet.xsd">
      <Boolean xmlns="http://opcfoundation.org/UA/2008/02/Types.xsd">true</Boolean>
    </Value>
  </UAVariable>
```

Reloading `out/reduced.xml` yields 54 nodes and **zero unresolved targets** — self-contained for
everything it declares, though it was assembled from two files. `nodeset-linker validate` reports
no semantic findings, and the same command with `--schema` also checks the XML against the
vendored official `UANodeSet.xsd` (`tests/data/schema/UANodeSet.xsd`, Model Version 1.05.04,
PublicationDate 2024-12-01) — see `tests/test_schema_compliance.py` and `tests/test_cli.py`. A
schema match says the file is readable XML of the right shape; it is not a claim of full OPC UA
conformance.

## Same input, different mode

| Config | Roots | Retained | Written references | Notes |
| --- | --- | --- | --- | --- |
| `examples/config.yaml` (STRICT) | type + instance | 54 (−17, 23.9 %) | 82 | keeps the Optional `Diagnostics` / `MachineDiagnostics`; UA and Machinery both subsets (`IsNamespaceSubset=true`) |
| `examples/config_runtime.yaml` (RUNTIME) | `typeDefinition=MSSControllerType` | 53 (−18, 25.4 %) | 81 | drops the Optional `MachineDiagnostics`, which nothing references anymore; the Optional `Diagnostics` variable is *repaired* back because the retained `MSSControllerType` still points at it |
| `examples/config_custom.yaml` (CUSTOM) | `namespaceUri=http://opcfoundation.org/UA/Machinery/` + `retain: nodeClass=DataType`, `exclude: browseName=MachineDiagnostics, browseName=MachineryView` | 55 (−16, 22.5 %) | 83 | a namespace URI selector makes **every** Machinery node a root, and roots can never be dropped — so both exclude rules are inert here and the retain rule adds nothing (every UA datatype was already reachable). `UnrelatedType` survives because it is a root; the Machinery namespace stays complete (its source `IsNamespaceSubset=false` is left unchanged), only UA is reduced (`true`) |

Excluding something the model actually references is refused instead of exported: the same
custom config with `exclude: [browseName=Diagnostics]` prints
`error: 1 error(s), 0 warning(s)` and exits 1 with no output file.

`OPTIONAL_AWARE` reduces to exactly the STRICT result (54 nodes, 82 written references). Its
retention policy is inherited from `StrictPolicy`; the mandatory/optional split the reports show
is computed by `mandatory_level_of` in `reports/data.py` for *every* mode, and
`OptionalAwarePolicy.classify` is never called by the pipeline — so the mode adds nothing to the
printed reports.

## Reproduce it

```bash
nodeset-linker analyze examples/config.yaml    # 71 → 54 nodes, −17, 23.9 %
nodeset-linker link    examples/config.yaml    # wrote out/reduced.xml, validation passed
nodeset-linker explain --node JobDataType examples/config.yaml
#  JobDataType retained because:
#    MSSControllerType -> Diagnostics -> String -> JobDataType
#    (origin: MSSControllerType, dependency kind: datatype)
nodeset-linker explain --node JobStateEnum examples/config.yaml
#  JobStateEnum retained because:
#    MSSControllerType -> Diagnostics -> String -> JobDataType -> JobStateEnum
#    (origin: MSSControllerType, dependency kind: datatype)
nodeset-linker explain --node BaseObjectType examples/config.yaml
#  BaseObjectType retained because:
#    MSSControllerInstance -> IMachineStatus -> BaseObjectType
#    (origin: MSSControllerInstance, dependency kind: inheritance)
nodeset-linker explain --node UnrelatedType examples/config.yaml       # removed - not reachable
nodeset-linker explain --node UnrelatedMachineType examples/config.yaml # removed - not reachable
nodeset-linker stats  out/reduced.xml      # nodes 54, namespaces 2, edges 82
nodeset-linker validate out/reduced.xml    # semantic checks only: validation passed - no findings
nodeset-linker validate out/reduced.xml --schema tests/data/schema/UANodeSet.xsd
#  schema validation passed (…UANodeSet.xsd) + validation passed - no findings
python scripts/check_official_nodesets.py  # the real UA/DI/Machinery/IA files, same checks;
#  prints each file's SHA-256 and its declared Model version, and fails if a *source* fails
python scripts/check_official_nodesets.py --ref 1.05.05   # use a separate cache for this ref
```
