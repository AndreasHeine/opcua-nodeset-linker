# Demo 4: Breaking datatype and modelling-rule changes in Machinery Identification

This demo changes a standard Companion Specification declaration, not the showcase
instance. Both configurations use the original showcase and the same dependency inputs,
except for Machinery:

| Configuration | Machinery input | Output |
| --- | --- | --- |
| [config_original.yaml](config_original.yaml) | [Original NodeSet](../nodesets/Opc.Ua.Machinery.NodeSet2.xml) | [original.xml](original.xml) |
| [config_modified.yaml](config_modified.yaml) | [Modified NodeSet](../nodesets/Opc.Ua.Machinery.NodeSet2-Modified.xml) | [modified.xml](modified.xml) |

The modified input is a **deliberately incompatible local test fixture**, not an official
revision of the Companion Specification. The original source is unchanged. The earlier
Machinery/Jobs mutation is not loaded by either configuration.

## What changed?

The mandatory `ProductInstanceUri` property of `MachineIdentificationType` has this
stable NodeId:

```text
nsu=http://opcfoundation.org/UA/Machinery/;i=6015
```

In the modified Machinery input, this property's `DataType` changes:

```text
ProductInstanceUri: String (i=12) -> Double (i=11)
Parent: MachineIdentificationType (Machinery i=1012)
```

The names and standard identifiers match the OPC UA reference:
[String (i=12)](https://reference.opcfoundation.org/specs/OPC-10000-5/12.2.11.md) and
[Double (i=11)](https://reference.opcfoundation.org/specs/OPC-10000-5/12.2.9.2.md).
Those identifiers belong to Core, while the changed property's NodeId belongs to Machinery.

Unlike [demo3](../demo3/README.md), this edits the standard type declaration rather than
the instance's property. The showcase instance input remains unchanged, including its
String-valued `ProductInstanceUri`. Changing the type contract to Double makes the
fixture intentionally incompatible; it is not deployment-ready.

The second edit tightens the `Location` property of the same
`MachineIdentificationType`:

```text
nsu=http://opcfoundation.org/UA/Machinery/;i=6029
Location: Modelling rule Optional -> Mandatory
```

Its `HasModellingRule` target changes from `i=80` (Optional) to `i=78` (Mandatory).
Existing implementations that legitimately omitted Location must now provide it, so
this is a breaking type-contract change even though its datatype remains String.
The existing diff policy classifies Optional-to-Mandatory tightening as breaking;
the reverse Mandatory-to-Optional change is not classified as breaking by that policy.

## No extra root is needed

Both configurations select **only** the original showcase root:

```yaml
roots:
  - nodeid=nsu=http://umati.org/UA/ShowCaseMachineTool/;i=5003
```

The showcase already depends on the Machinery Identification type hierarchy, so its
closure naturally retains `MachineIdentificationType` and both properties in STRICT mode.
No artificial datatype root or custom retention rule is needed. The test verifies that both edits survive
the normal reduction in both configurations.

## 1. Run both reductions

Use Python 3.12 or newer and an environment with the repository installed (see
[installation](../../README.md#development-install)). Run from the **repository root**:

```powershell
nodeset-linker analyze examples\demo4\config_original.yaml
nodeset-linker analyze examples\demo4\config_modified.yaml
nodeset-linker link examples\demo4\config_original.yaml
nodeset-linker link examples\demo4\config_modified.yaml
```

Both load 6,655 nodes and retain 1,110. Core's 4,956 declarations remain external,
and 589 non-external declarations are removed (34.7%). The merged outputs are
included in this folder and can be regenerated in place.

## 2. Compare the models

```powershell
nodeset-linker diff examples\demo4\original.xml examples\demo4\modified.xml --impact
```

Expected results:

| Measure | Result |
| --- | --- |
| Original / modified nodes | 1,110 / 1,110 |
| Added / removed / renumbered nodes | 0 / 0 / 0 |
| Modified nodes | 2: ProductInstanceUri (i=6015) and Location (i=6029), both Machinery |
| Breaking changes | 2: datatype change and Optional-to-Mandatory tightening |
| Impact | 165 dependent labels |

The changed declarations are displayed as:

```text
ProductInstanceUri [Variable, ns=Machinery, i=6015]
  DataType: String (i=12) -> Double (i=11)
Location [Variable, ns=Machinery, i=6029]
  Modelling rule: Optional -> Mandatory
```

The existing diff rules classify both edits as breaking. Impact includes
`MachineIdentificationType` and `ShowCaseMachineTool`, demonstrating how a changed standard
type contract reaches downstream consumers. The 165 labels are potential graph dependents,
not 165 edited nodes or a guarantee of application failures. Both edits reach overlapping
dependents, so their combined impact is 165 unique labels, not 330. The
[JSON report](diff.json) contains the complete impacted-label list.

## 3. Generate analysis reports and test the CI gate

The generated [Markdown report](diff.md) and [JSON report](diff.json) are included:

```powershell
nodeset-linker diff examples\demo4\original.xml examples\demo4\modified.xml --impact --markdown --output examples\demo4\diff.md
nodeset-linker diff examples\demo4\original.xml examples\demo4\modified.xml --impact --json --output examples\demo4\diff.json
nodeset-linker diff examples\demo4\original.xml examples\demo4\modified.xml --impact --fail-on-breaking
$LASTEXITCODE
```

The last diff command returns **1**, as expected for a breaking change.

## Validation scope

```powershell
nodeset-linker validate examples\demo4\original.xml --schema tests\data\schema\UANodeSet.xsd
nodeset-linker validate examples\demo4\modified.xml --schema tests\data\schema\UANodeSet.xsd
```

Both pass the current semantic validator and repository XSD checks. Core is external,
so standalone validation can report external dependency warnings. The current validator
does not check all inherited property/instance datatype compatibility rules.
XSD validity likewise does not establish semantic or Companion Specification conformance.
This intentionally incompatible fixture illustrates why a passing validation result is
not a substitute for checking breaking changes and the intended OPC UA consumer's requirements.
