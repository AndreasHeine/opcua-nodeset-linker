# Demo 1: ShowCaseMachineTool

Reduce the eight supplied NodeSets around the `ShowCaseMachineTool` instance using
[config.yaml](config.yaml). The source instance is declared in
[ShowCaseMachineTool.xml](../nodesets/ShowCaseMachineTool.xml) with `NodeId="ns=1;i=5003"`.
Its stable identifier, used as the configuration's only root, is:

```text
nsu=http://umati.org/UA/ShowCaseMachineTool/;i=5003
```

Namespace indexes are document-local, so using the namespace URI avoids ambiguity when
merging these files.

## 1. Install

Run these commands in PowerShell from the **repository root**, not from this folder.
The configuration's file paths are resolved against your current working directory.
Python 3.12 or newer is required.

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"
```

If you already have an environment with the project installed, activate that environment
instead. The commands below use its `nodeset-linker` entry point.

## 2. Preview the reduction

```powershell
nodeset-linker analyze examples\demo1\config.yaml
```

This writes no output file. Inspect the statistics, namespace report, dependencies, and
validation findings before exporting.

With the eight supplied files and current configuration (`STRICT`, showcase root,
Core externalized), the precise reduction algorithm produces:

| Measure | Value |
| --- | --- |
| Loaded nodes | 6,655 |
| External Core nodes | 4,956 |
| Non-external input nodes | 1,699 |
| Retained/exported nodes | 1,110 |
| Actually removed nodes | 589 |
| Reduction | 34.7% |

The configuration loads the shared NodeSets under `examples\nodesets` and the showcase
instance file.
It uses the compatible Core, Machinery/Jobs, and ISA95 JobControl files to resolve and check
dependencies. Core's 4,956 loaded declarations are reported separately, not counted as
removed. The percentage uses only the 1,699 non-external input nodes:
`589 / (6655 - 4956) * 100 = 34.7%`.
The counts partition the input: `6655 = 1110 + 589 + 4956`.
These figures describe this root selection, not every application's requirements.

For comparison, with no external models configured, STRICT retains 2,242 nodes and
removes 4,413 (66.3%); RUNTIME retains 1,252 and removes 5,403 (81.2%). Those percentages
include reduction within Core and use all 6,655 input nodes as the baseline.

To inspect the root's retention reason:

```powershell
nodeset-linker explain --node "nsu=http://umati.org/UA/ShowCaseMachineTool/;i=5003" examples\demo1\config.yaml
```

## 3. Export

```powershell
nodeset-linker link examples\demo1\config.yaml
```

The output is `examples\demo1\reduced.xml`. The output folder is created automatically.
You can choose another output path:

```powershell
nodeset-linker link examples\demo1\config.yaml --output examples\demo1\out\machine-tool.xml
```

`STRICT` keeps reachable mandatory and optional declarations. Reduced namespace subset
properties are marked when their metadata is retained. Semantic errors block export;
warnings do not.

## 4. Validate the written file

```powershell
nodeset-linker stats examples\demo1\reduced.xml
nodeset-linker validate examples\demo1\reduced.xml --schema tests\data\schema\UANodeSet.xsd
```

The current configuration writes 1,110 node declarations. The exported files pass the
repository's XSD and reload semantic checks. External
dependency warnings depend on which models are externalized and which source metadata
is available to the standalone validation command.

Schema validation is not OPC UA certification. The asyncua import test below is a separate
check against that server implementation.

## External dependencies

The input set includes Core, DI, IA, Machinery, Machinery/Jobs, MachineTool, ISA95 JobControl,
and the showcase model. Configure Core under `external:` when importing into an initialized
asyncua server: it already provides Core declarations. Keep the Core source loaded so
the linker can inspect and validate the dependency locally.

| External ModelUri | Requirement declared by the showcase source |
| --- | --- |
| `http://opcfoundation.org/UA/` | Core 1.05.03, publication date 2023-12-15 |

With Core externalized, the exported file does not contain Core nodes; standalone validation
can warn about that dependency. Analysis/import tests do not certify Core version compatibility.

Before importing the reduced file, ensure the consuming server provides compatible Core
nodes. An initialized asyncua server provides Core nodes, but you must still check version
compatibility.
`RequiredModel` metadata does not download or install dependencies automatically.

DI, IA, Machinery, and MachineTool declarations reached by the closure are included in this
output. Avoid importing their full files separately into the same server without checking for
duplicate NodeIds. Machinery/Jobs and ISA95 JobControl are also bundled in this current
STRICT configuration. RUNTIME can omit completely unused models and their RequiredModel
entries. If your server already provides any bundled models, add their ModelUris to
`external` and regenerate the output. Keep their source files under `nodesets` if you want
the linker to check their declarations locally.

Remove Core from `external` only if you intend to export its declarations too. Do not use the
small Core demonstration fixture from the test suite as a replacement for the official Core
NodeSet.

## Import into an asyncua server

The demo server imports `reduced.xml`, verifies that the showcase root is present, and
listens on the local-only endpoint `opc.tcp://127.0.0.1:4840/demo1/`.

Install the project's optional asyncua dependency if needed:

```powershell
python -m pip install -e ".[asyncua]"
```

Enable `external: [http://opcfoundation.org/UA/]` in the configuration before importing;
otherwise the initialized server rejects duplicate Core NodeIds.
Regenerate the reduced file after changing the configuration, then run:

```powershell
nodeset-linker link examples\demo1\config.yaml
python examples\demo1\server.py --check-only
python examples\demo1\server.py
```

`--check-only` imports the file and checks the `ShowCaseMachineTool` node without opening a
network endpoint. Stop the running server with Ctrl+C. The importer helper in `server.py`
checks external requirements (Core) and avoids asking asyncua to pre-load requirements that
are already bundled in this same NodeSet.

The current STRICT output was successfully imported into asyncua: **1,110 nodes imported**
and the `ShowCaseMachineTool` root (`i=5003`) verified. The server assigned its namespace
index as 8 in that run; the index is server-local, not a fixed identity.
The server then listened at `opc.tcp://127.0.0.1:4840/demo1/`.

The demo disables asyncua's automatic Python class generation for custom data types. The
importer currently cannot generate a class for the bundled nested `OutputInfo` OptionSet
field. asyncua logs warnings for data types it cannot classify while importing. The nodes
and their datatype attributes are still imported, but this test does not prove that every
custom structured value can be encoded/decoded or that methods have application callbacks.

## Troubleshooting

- **Input file not found:** run from the repository root.
- **CLI not found:** activate the environment where the project was installed.
- **Validation error:** inspect `analyze` output and correct the inputs or configuration;
  do not disable validation to force a broken export.
- **Import failure:** check external model versions, missing dependencies, duplicate nodes,
  and importer support for the exported values and datatype definitions.

See the [general usage guide](../docs/usage-guide.md) for other selectors and retention modes.
