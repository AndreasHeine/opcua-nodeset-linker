# Demo 2: Split ShowCaseMachineTool export

This is the namespace-split counterpart of [demo1](../demo1/README.md).
[config.yaml](config.yaml) reuses the same eight source NodeSets, showcase root,
`STRICT` retention, and externalized OPC UA Core. The input XML files remain under
[examples/nodesets](../nodesets/); there is no second copy of the source models.
Only the export strategy changes.

## 1. Install

Run all commands from the **repository root** using Python 3.12 or newer:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"
```

## 2. Preview

```powershell
nodeset-linker analyze examples\demo2\config.yaml
```

The expected reduction is identical to demo1:

| Measure | Value |
| --- | --- |
| Loaded nodes | 6,655 |
| External Core nodes | 4,956 |
| Retained/exported nodes | 1,110 |
| Actually removed nodes | 589 |
| Reduction of non-external input | 34.7% |

Analysis also reports planned output filenames and URI-keyed namespace dependencies,
without writing any files.

## 3. Export

The generated example is included in [reduced](reduced/):

```text
reduced/
  DI.xml
  IA.xml
  ISA95-JOBCONTROL_V2.xml
  Jobs.xml
  Machinery.xml
  MachineTool.xml
  ShowCaseMachineTool.xml
  manifest.json
```

Every retained node appears exactly once, in its NodeId namespace's file. References
to sibling files are preserved. Core is external, so there is no `UA.xml`.
[manifest.json](reduced/manifest.json) maps namespace URIs to files and lists direct
namespace uses, including external dependencies. These edges can contain cycles and
are not a guaranteed sequential import order.

Split export requires an absent or empty destination. Since the checked-in
[reduced](reduced/) directory is already populated, generate a fresh copy using:

```powershell
nodeset-linker link examples\demo2\config.yaml --output out\demo2
```

Choose another empty directory on subsequent runs. Without the override, the configured
destination is `examples\demo2\reduced`; exporting there is intentionally refused while
the included outputs are present. No existing files are overwritten.

## 4. Validate the bundle

Validate the included files against the repository's XSD and load them together:

```powershell
@'
import json
from pathlib import Path

from lxml.etree import XMLSchema, parse

from nodeset_linker.repository.model import load_nodesets
from nodeset_linker.validator.semantic import SemanticValidator

directory = Path("examples/demo2/reduced")
manifest = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
paths = [directory / item["output_file"] for item in manifest["namespaces"]]
schema = XMLSchema(parse("tests/data/schema/UANodeSet.xsd"))
for path in paths:
    assert schema.validate(parse(str(path))), str(schema.error_log)

parts = [load_nodesets([path]) for path in paths]
identifiers = [identifier for part in parts for identifier in part.nodes]
assert len(identifiers) == len(set(identifiers)) == 1110

bundle = load_nodesets(paths)
report = SemanticValidator(
    bundle, external=frozenset(manifest["external_models"])
).validate()
assert report.ok, report.errors

with_core = load_nodesets([
    *paths, Path("examples/nodesets/Opc.Ua.NodeSet2.xml")
])
assert with_core.unresolved_targets() == []
print(f"Validated {len(paths)} files and {len(identifiers)} unique exported nodes.")
'@ | python -
```

Change `directory` to `Path("out/demo2")` to validate a freshly generated export.
Do not treat sibling files as external when validating the bundle: that would hide
missing output declarations. Core references are expected to be unresolved until a
Core provider is loaded.

The integration test also regenerates both demo configurations and compares their
reloaded semantic nodes, references, datatype definitions, attributes, and values after
document-local namespace/server index normalization.

## Importing into a server

The consumer must provide compatible Core declarations and import all seven files.
Do not additionally import the full companion NodeSets from demo1: that can introduce
duplicate NodeIds. `RequiredModel` entries do not install dependencies automatically.

The [demo1 server](../demo1/server.py) imports a single merged file; it is not a split
bundle loader. A sequential importer may require dependency ordering or multi-pass
loading to handle cross-file references and cycles. XSD and semantic validation do
not certify compatibility with every OPC UA stack; test with the intended consumer.

See the [split export usage guide](../../docs/usage-guide.md#split-namespace-export)
for configuration defaults, metadata behavior, and manifest semantics.

[Demo 3](../demo3/README.md) uses the edited
[ShowCaseMachineTool-Modified.xml](../nodesets/ShowCaseMachineTool-Modified.xml) as an
alternative input and demonstrates semantic diff against the original showcase.
That edited file is a shared input fixture, not an output listed in this demo's manifest.
