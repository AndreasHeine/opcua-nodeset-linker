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
retained_nodes: 54
removed_nodes: 17
reduction_percent: 23.9
```

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
