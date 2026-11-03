# Usage Guide

This guide walks through a complete reduction using the two small NodeSets included with
the repository. They are demonstration fixtures, not a substitute for testing your own
models and target OPC UA stack.

## 1. Install

From the repository root, create and activate a virtual environment, then install the project:

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"
```

The CLI command is `nodeset-linker`. If PowerShell does not find it, confirm that the
virtual environment is activated.

## 2. Choose NodeSets and roots

The example configuration is [examples/config.yaml](../examples/config.yaml):

```yaml
nodesets:
  - tests/data/ua_base.xml
  - tests/data/machinery.xml

mode: STRICT

roots:
  - browseName=MSSControllerType
  - nodeid=nsu=http://opcfoundation.org/UA/Machinery/;i=2200

output: out/reduced.xml
```

The two input documents declare 71 nodes in total. The roots select a controller type and a
Machinery instance. The second root uses a namespace URI rather than `ns=1`: namespace indexes
belong to individual documents and may differ between NodeSets.

For your own model, replace `nodesets` with paths to your source files and choose roots that
identify the nodes your application needs. Common selectors include `browseName=`, `nodeid=`,
`namespaceUri=`, and `typeDefinition=`. A `typeDefinition=` selector includes the type and
instances that declare it.

## 3. Preview the reduction

Run analysis first. This reports what would be retained without writing an output file:

```powershell
nodeset-linker analyze examples/config.yaml
```

For the included example, the statistics report shows:

```text
original_nodes: 71
retained_nodes: 51
removed_nodes: 20
reduction_percent: 28.2
```

With external dependencies, `external_nodes` is shown separately and is not included in
`removed_nodes`. The counts satisfy
`original_nodes = retained_nodes + removed_nodes + external_nodes`, and the reduction percentage
is `removed_nodes / (original_nodes - external_nodes) * 100` (zero for an empty baseline).
Retained counts include nodes added by CUSTOM rules; removed counts include successful exclusions.
Externalization alone does not count as reduction.

Review the namespace report, dependency origins, and validation findings before linking. If
the result does not contain the functionality you need, adjust the roots or retention mode
and analyze again.

To find out why a particular node is in the result (or why it was removed), ask for an
explanation:

```powershell
nodeset-linker explain --node JobStateEnum examples/config.yaml
```

## 4. Write the reduced NodeSet

Once the preview looks right, create the output:

```powershell
nodeset-linker link examples/config.yaml
```

The example writes `out/reduced.xml`. You can override the configured output path:

```powershell
nodeset-linker link examples/config.yaml --output out/controller.xml
```

The linker validates the reduced semantic model before export. An error blocks the write;
warnings are reported but do not by themselves prevent export.

### Split namespace export

Replace `output:` with an `export:` block to select the output strategy. These settings are
mutually exclusive; old configurations keep their existing behavior.

```yaml
export:
  mode: split
  directory: out

external:
  - http://opcfoundation.org/UA/
```

For the small fixtures, choose a Machinery-owned root such as `browseName=JobDataType`
when externalizing UA. A root whose own NodeId belongs to an external model is rejected.
This configuration produces `out/Machinery.xml` and `out/manifest.json`.
The complete example is [examples/config_split.yaml](../examples/config_split.yaml).

Use an absent or empty output directory. A non-empty directory is refused, including one
from an earlier export; select a fresh directory rather than relying on stale-file cleanup.
Documents and the manifest are staged together before publication. No existing files are
overwritten by split export.

```powershell
nodeset-linker analyze examples/config_split.yaml
nodeset-linker link examples/config_split.yaml --output out/split
```

`analyze` reports planned filenames without creating files. `--output` overrides the
directory in split mode, and continues to override the file in merged mode. To use the new
configuration shape for merged output:

```yaml
export:
  mode: merged
  file: out/reduced.xml
```

The defaults within an `export:` block are `mode: merged`, `file: out/reduced.xml`, or
`directory: out` when `mode: split` is selected. Neither setting changes the semantic
reduction. Each retained node is written exactly once according to its NodeId namespace
URI, not the source filename or BrowseName namespace. Empty and externalized namespaces
produce no XML file. Retained UA declarations produce `UA.xml` when UA is not externalized.

Filenames use sanitized URI tails (for example `Machinery/Jobs/` becomes `Jobs.xml`).
Windows device names are escaped, long names are shortened, and case-insensitive
collisions receive deterministic URI-hash suffixes. The URI remains the authoritative
identity; consumers should read the manifest rather than infer identity from filenames.

The manifest has this shape:

```json
{
  "export_mode": "split",
  "namespaces": [
    {
      "uri": "http://opcfoundation.org/UA/Machinery/",
      "output_file": "Machinery.xml"
    }
  ],
  "dependencies": {
    "http://opcfoundation.org/UA/Machinery/": [
      "http://opcfoundation.org/UA/"
    ]
  },
  "external_models": ["http://opcfoundation.org/UA/"]
}
```

Dependencies are direct uses of other namespaces in retained references, reference types,
typed attributes, datatype fields, tracked value identifiers, or qualified BrowseNames.
They include external targets but not transitive closure or unused source-header
requirements. Inverse references can create cycles: this is not a guaranteed load order.
The namespace report exposes the same URI-keyed `namespace_dependencies` and a URI-to-file
`output_files` mapping, while retaining the existing count and dependency fields.

Each XML file has its own namespace/server index tables, relevant aliases, an owned model
entry and relevant `RequiredModel` entries, preserving known version information. Namespace
metadata objects and subset properties remain ordinary nodes: they are preserved only
when retained and are never copied to another namespace or synthesized by split export.

The `validate` CLI command still validates a single XML file, not a bundle. Validate each
file against the XSD, and validate the combined semantic model with the Python API:

```python
import json
from pathlib import Path

from nodeset_linker.repository.model import load_nodesets
from nodeset_linker.validator.semantic import SemanticValidator

directory = Path("out/split")
manifest = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
paths = [directory / entry["output_file"] for entry in manifest["namespaces"]]
model = load_nodesets(paths)
report = SemanticValidator(
    model, external=frozenset(manifest["external_models"])
).validate()
assert report.ok, report.errors
```

Use the manifest's explicit external set when checking the bundle: missing sibling
declarations must not be accepted as external dependencies. To require
`model.unresolved_targets() == []`, load all external provider NodeSets alongside the split
outputs. That method deliberately reports external targets as unresolved when providers
have not been loaded. Always test importing with the intended OPC UA stack; a stack that
loads documents sequentially may need dependency ordering or multi-pass loading.

## 5. Validate and inspect the output

Run semantic validation and also provide the NodeSet XSD to check the XML structure:

```powershell
nodeset-linker validate out/reduced.xml --schema tests/data/schema/UANodeSet.xsd
```

The schema check confirms that the document matches that XSD; it does not certify complete
OPC UA conformance. The validator also has a defined scope and does not check every
specification rule. Test the exported file with the OPC UA application or server that will
consume it.

Inspect the exported node and namespace counts with:

```powershell
nodeset-linker stats out/reduced.xml
```

## 6. Compare two model versions

`diff` answers "what changed in this information model" on the semantic model, not on the XML.
Compare a vendor release against its predecessor, or a reduced export against its source:

```powershell
nodeset-linker diff out/reduced.xml tests\data\machinery.xml --impact
nodeset-linker diff release-1.4.xml release-1.5.xml --json --fail-on-breaking
```

Because identity is `(namespaceUri, identifier)`, reordering the file, re-exporting it, or letting
the exporter reassign `NamespaceIndex` values reports `No semantic differences found.`; a node
whose identifier changed alone appears under *Renumbered nodes*.

Datatype changes show readable names with identifiers, for example
`DataType: String (i=12) -> Double (i=11)`. Loaded BrowseNames (or DisplayNames) take
precedence; canonical namespace-zero datatype names are available offline when Core is
externalized. Unknown identifiers stay as identifiers rather than receiving guessed names.
Custom datatype identifiers retain their namespace URIs, so identically named datatypes
from different models remain distinguishable. The same labels are used for variables,
variable types, structure fields, and union alternatives in console, Markdown, HTML,
and JSON reports (including JSON `old`/`new` strings and breaking-change reasons).
Datatype attributes are compared through their resolved identifiers, not again as raw XML
attribute spellings: changing `String` to the equivalent `i=12` is not a semantic change.
Reference targets use the same name-and-identifier labels, with an `(inverse)` suffix
for inverse references, including when a standard datatype target is externalized.

The report separates added, removed, modified and renumbered nodes, changes per node (references,
BrowseName, DisplayName, Description, modelling rule, datatype, Structure fields, Enum and
OptionSet values, Union alternatives), namespace totals, and the breaking subset:

```
Summary
Nodes: 18 → 18
Added: 1   Removed: 1   Modified: 0
Breaking changes: 1
Total Impact: 1 node

Removed nodes
  JobDataType [DataType, ns=Machinery, i=2001]
    Impact: MachineStatusStructure

Breaking changes
  JobDataType [i=2001]: DataType removed
    Affects 1 node
```

`--impact` names the nodes that depend on each change by walking the dependency graph backwards;
`--breaking` prints only the changes a consumer could fail on. `--markdown` and `--html` render
the same report for a pull request or a release note, and `-o <file>` writes it.
`--fail-on-breaking` exits 1 when any breaking change is present, so a pipeline can gate on it:

```yaml
- run: nodeset-linker diff old.xml new.xml --json --fail-on-breaking
```

## Optional: declare an external dependency

If the consuming application already provides a model such as the OPC UA Core model or DI,
you can configure its ModelUri under `external:` so its declarations are not included in the
export. References to that model remain, and the consuming application must load the external
model as well.

```yaml
external:
  - http://opcfoundation.org/UA/DI/
```

Only externalize models that the consumer really supplies. The linker does not invent a
missing `RequiredModel` declaration when the source metadata does not contain one; check the
external-dependency diagnostics and confirm the exported file works with the consuming stack.
See [Dependency Resolution](dependency-resolution.md#6-externalized-dependencies-external) for details.

## Retention modes

Use `STRICT` when you want to keep reachable optional as well as mandatory nodes. `RUNTIME`
can omit explicit optional declarations. `OPTIONAL_AWARE` keeps the same nodes as `STRICT`
but classifies dependencies in reports. `CUSTOM` applies configured `retain:` and `exclude:`
rules. See [the README](../README.md#retention-modes) for their behavior and constraints.

## More detail

- [Reduction Example](reduction-example.md) traces the example through merge, graph
  construction, dependency closure, retention, validation, and export.
- [Dependency Resolution](dependency-resolution.md) describes selectors, traversal rules,
  retention, validation, and external models.
- [Architecture](architecture.md) documents the implementation layers and data model.
- [the README](../README.md#semantic-diff-diff) lists which diff changes count as breaking.
