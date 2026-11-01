# Demo 1: ShowCaseMachineTool

Reduce the eight supplied NodeSets around the `ShowCaseMachineTool` instance using
[config.yaml](config.yaml). The source instance is declared in
[ShowCaseMachineTool.xml](nodesets/ShowCaseMachineTool.xml) with `NodeId="ns=1;i=5003"`.
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
nodeset-linker analyze demo1\config.yaml
```

This writes no output file. Inspect the statistics, namespace report, dependencies, and
validation findings before exporting.

With the eight supplied files and current configuration, the preview produces:

| Measure | Value |
| --- | --- |
| Loaded nodes | 6,655 |
| Retained nodes | 1,669 |
| Not exported | 4,986 |
| Reduction | 74.9% |
| External Core nodes not exported | 4,956 |
| Other unreachable nodes not exported | 30 |
| Semantic validation during analysis | 0 errors, 0 warnings |

The configuration loads the NodeSets under `demo1\nodesets` and the showcase instance file.
It uses the compatible Core, Machinery/Jobs, and ISA95 JobControl files to resolve and check
dependencies, but lists Core as `external:` so its node declarations are not exported. The other reachable
dependencies are retained. The reduction is measured against all 6,655 nodes loaded from all
eight sources; it is not a claim that only 1,669 nodes are needed for every application
scenario.

To inspect the root's retention reason:

```powershell
nodeset-linker explain --node "nsu=http://umati.org/UA/ShowCaseMachineTool/;i=5003" demo1\config.yaml
```

## 3. Export

```powershell
nodeset-linker link demo1\config.yaml
```

The output is `demo1\reduced.xml`. The output folder is created automatically.
You can choose another output path:

```powershell
nodeset-linker link demo1\config.yaml --output demo1\out\machine-tool.xml
```

`STRICT` keeps reachable mandatory and optional declarations. Reduced namespace subset
properties are marked when their metadata is retained. Semantic errors block export;
warnings do not.

## 4. Validate the written file

```powershell
nodeset-linker stats demo1\reduced.xml
nodeset-linker validate demo1\reduced.xml --schema tests\data\schema\UANodeSet.xsd
```

For the current inputs, the exported file has 1,669 nodes, 8 namespaces, and 5,501 explicit
references. The XML passes the repository's XSD. When validating the reduced file by itself,
semantic validation reports 0 errors and 12 Core dependency warnings: the externalized Core
declarations are not available to that standalone check. This differs from `analyze`, which
loads all eight input NodeSets and reports 0 errors and 0 warnings for the current
configuration.

Schema validation is not OPC UA certification. The asyncua import test below is a separate
check against that server implementation.

## External dependencies

The input set includes Core, DI, IA, Machinery, Machinery/Jobs, MachineTool, ISA95 JobControl,
and the showcase model. Only Core is listed under `external`: its source file is loaded so
the linker can inspect and validate dependencies, but Core node declarations are omitted from
the reduced output.

| External ModelUri | Requirement declared by the showcase source |
| --- | --- |
| `http://opcfoundation.org/UA/` | Core 1.05.03, publication date 2023-12-15 |

The Core source NodeSet makes these declarations available to `analyze`, so the current
analysis reports no findings. The exported file does not contain Core nodes; standalone
validation therefore warns about the external Core dependency. Neither result proves that a
consuming application has a compatible Core version.

Before importing the reduced file, ensure the consuming server provides compatible Core
nodes. An initialized asyncua server provides Core nodes, but you must still check version
compatibility.
`RequiredModel` metadata does not download or install dependencies automatically.

DI, IA, Machinery, and MachineTool declarations reached by the closure are included in this
output. Avoid importing their full files separately into the same server without checking for
duplicate NodeIds. Machinery/Jobs and ISA95 JobControl are also bundled in this current
configuration. If your server already provides any of these models, add their ModelUris to
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

Regenerate the reduced file after changing the configuration, then run:

```powershell
nodeset-linker link demo1\config.yaml
python demo1\server.py --check-only
python demo1\server.py
```

`--check-only` imports the file and checks the `ShowCaseMachineTool` node without opening a
network endpoint. Stop the running server with Ctrl+C. The importer helper in `server.py`
checks external requirements (Core) and avoids asking asyncua to pre-load requirements that
are already bundled in this same NodeSet.

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
