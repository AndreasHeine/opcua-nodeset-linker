# Demo 4: Breaking datatype, modelling-rule, removal and enum changes

This demo changes standard Companion Specification declarations, not the showcase
instance. Both configurations use the original showcase and the same dependency inputs,
except for Machinery and MachineTool:

| Configuration | Machinery input | MachineTool input | Output |
| --- | --- | --- | --- |
| [config_original.yaml](config_original.yaml) | [Original NodeSet](../nodesets/Opc.Ua.Machinery.NodeSet2.xml) | [Original NodeSet](../nodesets/Opc.Ua.MachineTool.NodeSet2.xml) | [original.xml](original.xml) |
| [config_modified.yaml](config_modified.yaml) | [Modified NodeSet](../nodesets/Opc.Ua.Machinery.NodeSet2-Modified.xml) | [Modified NodeSet](../nodesets/Opc.Ua.MachineTool.NodeSet2-Modified.xml) | [modified.xml](modified.xml) |

The modified inputs are **deliberately incompatible local test fixtures**, not official
revisions of the Companion Specifications. The original sources are unchanged. The earlier
Machinery/Jobs mutation is not loaded by either configuration: the export reduces those
enums away, so it would never reach the diff.

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

A third edit drops an *optional* declaration entirely. The modified Machinery input has no
`ManufacturerUri` variable and no `HasProperty` reference to it:

```text
nsu=http://opcfoundation.org/UA/Machinery/;i=6007
ManufacturerUri: declaration removed (MachineIdentificationType lost its HasProperty)
```

Removing an optional declaration is still breaking (`BP-Declaration-Removed-Optional`,
`BP-Reference-OptionalChild-Removed`): consumers that browsed the optional property now
find nothing. Being optional removes the promise a server must keep, not the promise a
reader may already rely on.

A fourth edit adds a value to an enum in the modified MachineTool input:

```text
nsu=http://opcfoundation.org/UA/MachineTool/;i=62
ProcessIrregularity: + NotApplicable = 4
EnumValues: ArrayDimensions 4 -> 5
```

An added enum value is breaking (`BP-EnumValue-Added`): existing readers that switch on the
value set now meet a value they do not know, and the `EnumValues` list grows with it
(`BP-ArrayDimensions-Immutable`). MachineTool is chosen because its enums survive the
reduction; the Machinery/Jobs enums do not.

## No extra root is needed

Both configurations select **only** the original showcase root:

```yaml
roots:
  - nodeid=nsu=http://umati.org/UA/ShowCaseMachineTool/;i=5003
```

The showcase already depends on the Machinery Identification type hierarchy, so its
closure naturally retains `MachineIdentificationType` and both properties in STRICT mode.
No artificial datatype root or custom retention rule is needed. Regenerate the outputs
and run the diff below to verify that both edits survive the normal reduction.

## 1. Run both reductions

Use Python 3.12 or newer and an environment with the repository installed (see
[installation](../../README.md#development-install)). Run from the **repository root**:

```powershell
nodeset-linker analyze examples\demo4\config_original.yaml
nodeset-linker analyze examples\demo4\config_modified.yaml
nodeset-linker link examples\demo4\config_original.yaml
nodeset-linker link examples\demo4\config_modified.yaml
```

Both load 6,655 nodes; the original retains 1,110 and the modified retains 1,109, because
the removed declaration is no longer part of the closure. Core's 4,956 declarations remain
external, and 589 / 590 non-external declarations are removed (about 35%). The merged
outputs are included in this folder and can be regenerated in place.

## 2. Compare the models

```powershell
nodeset-linker diff examples\demo4\original.xml examples\demo4\modified.xml --impact
```

Expected results:

| Measure | Result |
| --- | --- |
| Original / modified nodes | 1,110 / 1,109 |
| Added / removed / renumbered nodes | 0 / 1 / 0 |
| Modified nodes | 5, including `ProductInstanceUri` (i=6015) and `Location` (i=6029) |
| Breaking changes | 6: datatype, modelling rule, declaration removal, reference removal, enum value, array dimensions |
| Advisory | 3 namespace-metadata findings |
| Impact | 494 dependent nodes |

The changed declarations are displayed as:

```text
ProductInstanceUri [Variable, ns=Machinery, i=6015]
  DataType: String (i=12) -> Double (i=11)
Location [Variable, ns=Machinery, i=6029]
  Modelling rule: Optional -> Mandatory
MachineryItemIdentificationType [ObjectType, ns=Machinery]
  - HasProperty -> ManufacturerUri (ns=Machinery, i=6007)
ManufacturerUri [Variable, ns=Machinery, i=6007]
  Variable removed
ProcessIrregularity [DataType, ns=MachineTool, i=62]
  + NotApplicable = 4
EnumValues [Variable, ns=MachineTool, i=190]
  ArrayDimensions: 4 -> 5
```

The diff rules classify all six findings as breaking. The updated `EnumValues` variable
also carries *information* verdicts, because instance data and server-specific attributes
are edits the paper does not treat as contract breaks.

Impact includes `MachineIdentificationType` and `ShowCaseMachineTool`, demonstrating how a
changed standard type contract reaches downstream consumers. The removed optional property
pulls the widest set, so the four edits reach overlapping dependents and total 494 distinct
dependent nodes rather than the sum of the per-finding counts. The
[JSON report](diff.json) contains the impacted-label list per finding.

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
