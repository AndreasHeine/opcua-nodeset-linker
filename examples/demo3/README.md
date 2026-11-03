# Demo 3: Semantic diff of two showcase instances

Run the same reduction twice, changing only the showcase instance input, then compare
the resulting semantic models. The shared companion NodeSets remain under
[examples/nodesets](../nodesets/).

| Configuration | Showcase input | Merged output |
| --- | --- | --- |
| [config_original.yaml](config_original.yaml) | [Original instance](../nodesets/ShowCaseMachineTool.xml) | [original.xml](original.xml) |
| [config_modified.yaml](config_modified.yaml) | [Your modified instance](../nodesets/ShowCaseMachineTool-Modified.xml) | [modified.xml](modified.xml) |

Both use the same seven dependency inputs, `STRICT` retention, externalized OPC UA
Core, and the same root:

```text
nsu=http://umati.org/UA/ShowCaseMachineTool/;i=5003
```

The modified input is the edited namespace-owned file from demo2, now stored with the
shared inputs. It replaces the
original showcase input; do not load both versions together, because they declare the
same NodeIds. The other demo2 outputs are not loaded: dependencies come from the shared
source NodeSets.

## 1. Install

Use Python 3.12 or newer and run all commands from the **repository root**:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"
```

## 2. Run the two configurations

```powershell
nodeset-linker analyze examples\demo3\config_original.yaml
nodeset-linker analyze examples\demo3\config_modified.yaml
nodeset-linker link examples\demo3\config_original.yaml
nodeset-linker link examples\demo3\config_modified.yaml
```

Both runs load 6,655 nodes and retain 1,110. Core's 4,956 declarations remain external;
589 non-external declarations are removed (34.7% reduction). The generated merged
outputs are included in this folder. Unlike split export, these merged files can be
regenerated in place.

Re-exporting both inputs through the same pipeline normalizes their XML serialization
and document-local namespace indexes before comparison. This avoids distracting
source-versus-export spelling differences: the original input is a full source file,
while the modified input was serialized by demo2's exporter.

## 3. Analyze semantic differences and impact

```powershell
nodeset-linker diff examples\demo3\original.xml examples\demo3\modified.xml --impact
```

The current result is:

| Measure | Result |
| --- | --- |
| Original / modified node counts | 1,110 / 1,110 |
| Added / removed / renumbered nodes | 0 / 0 / 0 |
| Modified nodes | 1 |
| Breaking changes | 1 |
| Impact | 52 dependent node labels |

The changed variable is:

```text
nsu=http://umati.org/UA/ShowCaseMachineTool/;i=6002
ProductInstanceUri: DataType String (i=12) -> Double (i=11)
```

Changing the datatype changes the variable's contract, so the existing diff engine
classifies it as breaking. The report states
`DataType: String (i=12) -> Double (i=11)`: readable names are shown alongside stable
identifiers, even though Core is externalized. The datatype edit is reported once,
not repeated under generic XML attributes.

The standard names match the OPC UA reference:
[String, i=12](https://reference.opcfoundation.org/specs/OPC-10000-5/12.2.11.md) and
[Double, i=11](https://reference.opcfoundation.org/specs/OPC-10000-5/12.2.9.2.md).

`--impact` walks reverse semantic dependency edges. It reports 52 dependent labels,
including `Identification` and `ShowCaseMachineTool`. These are potential graph
dependents, not a claim that 52 nodes were edited or that every application will fail.
The [JSON report](diff.json) contains the full impacted-label list.

**Intentional incompatibility:** the modified input still carries a `<String>` value
for `ProductInstanceUri` although its declared datatype is now Double. This demo keeps
your edit intact to illustrate a breaking contract change. The repository's semantic
validator and XSD checks pass within their existing scope; neither certifies runtime
value/datatype compatibility or full Companion Specification conformance. Do not treat
the modified model as deployment-ready or import it into a production server unchanged.

## 4. Generate reports

The included [Markdown analysis](diff.md) and [JSON analysis](diff.json) are generated
by the existing CLI:

```powershell
nodeset-linker diff examples\demo3\original.xml examples\demo3\modified.xml --impact --markdown --output examples\demo3\diff.md
nodeset-linker diff examples\demo3\original.xml examples\demo3\modified.xml --impact --json --output examples\demo3\diff.json
```

Optional browser-readable output:

```powershell
nodeset-linker diff examples\demo3\original.xml examples\demo3\modified.xml --impact --html --output out\demo3-diff.html
```

Create `out` first if it does not exist; the report command does not create parent
directories. Regenerate reports after changing either input.

## 5. Exercise a CI breaking-change gate

```powershell
nodeset-linker diff examples\demo3\original.xml examples\demo3\modified.xml --impact --fail-on-breaking
$LASTEXITCODE
```

The expected exit code is **1** because of the detected datatype change. A normal diff
command without `--fail-on-breaking` succeeds with exit code 0 even when it finds changes.

To check the exported document structure and the validator's supported semantic checks:

```powershell
nodeset-linker validate examples\demo3\original.xml --schema tests\data\schema\UANodeSet.xsd
nodeset-linker validate examples\demo3\modified.xml --schema tests\data\schema\UANodeSet.xsd
```

Core is external, so standalone checks may report external-dependency warnings. See the
[usage guide](../../docs/usage-guide.md) for the scope of validation and semantic diff.
