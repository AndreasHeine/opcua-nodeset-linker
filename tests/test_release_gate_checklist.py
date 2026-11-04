"""§7 of the best-practices plan: the release gate run end to end on real documents."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from click.testing import CliRunner
from tests.conftest import MACHINERY

from nodeset_linker.cli.main import main

BASE = MACHINERY.read_text(encoding="utf-8")
CLOSE = "</UANodeSet>"

REFERENCE_TYPE = """  <UAReferenceType NodeId="ns=1;i=2600" BrowseName="1:HasOwner" Symmetric="{symmetric}">
    <DisplayName>HasOwner</DisplayName>
    <References>
      <Reference ReferenceType="HasSubtype" IsForward="false">i=49</Reference>
    </References>
  </UAReferenceType>
"""

OPTION_SET = """  <UADataType NodeId="ns=1;i=2700" BrowseName="1:JobFlags" SymbolicName="JobFlags">
    <DisplayName>JobFlags</DisplayName>
    <References>
      <Reference ReferenceType="HasSubtype" IsForward="false">i=11652</Reference>
    </References>
    <Definition Name="JobFlags" IsOptionSet="true">
      <Field Name="Idle" Value="0">
        <Description>{description}</Description>
      </Field>
    </Definition>
  </UADataType>
"""

OPTIONAL_NODE = """  <UAObject NodeId="ns=1;i=2301" BrowseName="1:OptionalDiagnostics" ParentNodeId="ns=1;i=2200">
    <DisplayName>OptionalDiagnostics</DisplayName>
    <References>
      <Reference ReferenceType="HasModellingRule">i=80</Reference>
      <Reference ReferenceType="HasComponent" IsForward="false">ns=1;i=2200</Reference>
    </References>
  </UAObject>
"""

SUBTYPE = """  <UADataType NodeId="ns=1;i=2800" BrowseName="1:MachineStatusExtended" SymbolicName="MachineStatusExtended">
    <DisplayName>MachineStatusExtended</DisplayName>
    <References>
      <Reference ReferenceType="HasSubtype" IsForward="false">ns=1;i=2020</Reference>
    </References>
    <Definition Name="MachineStatusExtended">
      <Field Name="Reason" DataType="String" IsOptional="true" />
    </Definition>
  </UADataType>
"""

DIAGNOSTICS_OBJECT = """  <UAObject NodeId="ns=1;i=2300" BrowseName="1:MachineDiagnostics" ParentNodeId="ns=1;i=2200">
    <DisplayName>MachineDiagnostics</DisplayName>
    <References>
      <Reference ReferenceType="HasModellingRule">i=80</Reference>
      <Reference ReferenceType="HasComponent" IsForward="false">ns=1;i=2200</Reference>
    </References>
  </UAObject>
"""

UNRELATED = """  <UAObjectType NodeId="ns=1;i=2500" BrowseName="1:UnrelatedType" SymbolicName="UnrelatedType">
    <DisplayName>UnrelatedType</DisplayName>
    <References>
      <Reference ReferenceType="HasSubtype" IsForward="false">i=58</Reference>
    </References>
  </UAObjectType>
"""


def edit(text: str, anchor: str, replacement: str) -> str:
    """One anchored XML edit; an anchor that is not there is a broken test, not a passing gate."""
    assert anchor in text, f"anchor not in fixture: {anchor!r}"
    return text.replace(anchor, replacement, 1)


def add(text: str, block: str) -> str:
    """Append a node block before the document closes."""
    assert CLOSE in text
    return text.replace(CLOSE, f"{block}{CLOSE}", 1)


def run_gate(tmp_path: Path, old: str, new: str) -> tuple[int, dict]:
    """Run the gate exactly as CI does, and read back the JSON it wrote."""
    before = tmp_path / "old.xml"
    after = tmp_path / "new.xml"
    report = tmp_path / "report.json"
    before.write_text(old, encoding="utf-8")
    after.write_text(new, encoding="utf-8")
    result = CliRunner().invoke(
        main,
        [
            "diff",
            str(before),
            str(after),
            "--fail-on-breaking",
            "--json",
            "--output",
            str(report),
        ],
    )
    assert result.exit_code in (0, 1), result.output
    return result.exit_code, json.loads(report.read_text(encoding="utf-8"))


def severities(payload: dict) -> dict[str, str]:
    """Every rule id the report named, mapped to the severity it carried."""
    found: dict[str, str] = {}
    for entry in [*payload["breaking_changes"], *payload["advisory_changes"]]:
        found[entry["rule_id"]] = entry["severity"]
    for key in ("added_nodes", "removed_nodes", "modified_nodes", "renumbered_nodes"):
        for entry in payload[key]:
            for verdict in entry.get("verdicts", []):
                found[verdict["rule_id"]] = verdict["severity"]
    for change in payload["namespace_changes"]:
        for delta in change["metadata"]:
            found[delta["rule_id"]] = delta["severity"]
    return found


#: §7: these must make ``--fail-on-breaking`` fail.
BREAKING_CASES = [
    (
        "identifier move",
        BASE,
        edit(BASE, 'NodeId="ns=1;i=2002"', 'NodeId="ns=1;i=2052"'),
        "BP-NodeId-Immutable",
    ),
    (
        "enum value addition",
        BASE,
        edit(
            BASE,
            '      <Field Name="Done" Value="2" />',
            '      <Field Name="Done" Value="2" />\n      <Field Name="Aborted" Value="3" />',
        ),
        "BP-EnumValue-Added",
    ),
    ("optional declaration removal", BASE, edit(BASE, DIAGNOSTICS_OBJECT, ""),
     "BP-Declaration-Removed-Optional"),
    (
        "structure field addition",
        BASE,
        edit(
            BASE,
            '      <Field Name="Job" DataType="ns=1;i=2001" />',
            '      <Field Name="Job" DataType="ns=1;i=2001" />\n      <Field Name="Operator" DataType="String" />',
        ),
        "BP-StructureField-Added",
    ),
    ("method signature change", BASE, edit(BASE, 'ArrayDimensions="0"', 'ArrayDimensions="1"'),
     "BP-MethodSignature-Immutable"),
    ("Symmetric change",
     add(BASE, REFERENCE_TYPE.replace("{symmetric}", "true")),
     add(BASE, REFERENCE_TYPE.replace("{symmetric}", "false")),
     "BP-ReferenceType-Symmetric-Immutable"),
    ("hierarchy move", BASE,
     edit(BASE, '<Reference ReferenceType="HasSubtype" IsForward="false">i=29</Reference>',
          '<Reference ReferenceType="HasSubtype" IsForward="false">i=22</Reference>'),
     "BP-Hierarchy-Move"),
]

#: §7: these must stay green; they may only raise an advisory or an information.
GREENING_CASES = [
    ("optional declaration addition", BASE, add(BASE, OPTIONAL_NODE), "BP-Declaration-Added"),
    ("DisplayName and Description edit", BASE,
     edit(BASE, "<DisplayName>JobStateEnum</DisplayName>",
          "<DisplayName>Job state</DisplayName>\n    <Description>State a job is in</Description>"),
     "BP-DisplayName-Meaning"),
    ("SymbolicName edit", BASE, edit(BASE, 'SymbolicName="JobStateEnum"', 'SymbolicName="JobStateEnumV102"'),
     "BP-SymbolicName-Identity"),
    ("AccessLevel and Historizing edit", BASE,
     edit(BASE, 'NodeId="ns=1;i=2202" BrowseName="InputArguments"',
          'NodeId="ns=1;i=2202" BrowseName="InputArguments" AccessLevel="CurrentRead" Historizing="true"'),
     "BP-Attributes-ServerSpecific"),
    ("OptionSet text fix",
     add(BASE, OPTION_SET.replace("{description}", "not running")),
     add(BASE, OPTION_SET.replace("{description}", "idle, nothing running")),
     "BP-Member-Text"),
    ("subtype addition", BASE, add(BASE, SUBTYPE), "BP-Subtype-Inserted"),
    ("Interface addition without new mandatory declarations", BASE,
     edit(BASE, UNRELATED, UNRELATED.replace(
         '      <Reference ReferenceType="HasSubtype" IsForward="false">i=58</Reference>',
         '      <Reference ReferenceType="HasSubtype" IsForward="false">i=58</Reference>\n      <Reference ReferenceType="HasInterface">ns=1;i=2100</Reference>',
     )),
     "BP-Interface-Added"),
    ("metadata-only version bump", BASE,
     edit(BASE, 'Version="1.0.1" PublicationDate="2024-01-01T00:00:00Z" ModelVersion="1.0.1"',
          'Version="1.0.2" PublicationDate="2024-06-01T00:00:00Z" ModelVersion="1.0.2"'),
     "BP-Namespace-ModelVersion"),
]


@pytest.mark.parametrize("case, old, new, rule_id", BREAKING_CASES)
def test_the_gate_fails_on_a_breaking_change(
    tmp_path: Path, case: str, old: str, new: str, rule_id: str
) -> None:
    exit_code, payload = run_gate(tmp_path, old, new)
    assert exit_code == 1, case
    assert severities(payload)[rule_id] == "breaking", case


@pytest.mark.parametrize("case, old, new, rule_id", GREENING_CASES)
def test_the_gate_stays_green_on_a_compatible_change(
    tmp_path: Path, case: str, old: str, new: str, rule_id: str
) -> None:
    exit_code, payload = run_gate(tmp_path, old, new)
    assert exit_code == 0, f"{case}: {payload['breaking_changes']}"
    assert severities(payload)[rule_id] in {"advisory", "info"}, case


def test_metadata_only_bump_reports_no_breaking_finding(tmp_path: Path) -> None:
    """A version bump touches release metadata only (§11.1), never a node contract."""
    exit_code, payload = run_gate(
        tmp_path,
        BASE,
        edit(BASE, 'Version="1.0.1" PublicationDate="2024-01-01T00:00:00Z" ModelVersion="1.0.1"',
             'Version="1.0.2" PublicationDate="2024-06-01T00:00:00Z" ModelVersion="1.0.2"'),
    )
    assert exit_code == 0
    assert payload["breaking_changes"] == []
    assert payload["summary"]["modified_nodes"] == 0


def test_release_verdict_names_the_paper_remedy(tmp_path: Path) -> None:
    """§11.3: a breaking change inside a published NamespaceUri must move to V2/."""
    manifest = tmp_path / "manifest.json"
    manifest.write_text(
        json.dumps(
            {"namespaces": [{"uri": "http://opcfoundation.org/UA/Machinery/", "version": "1.0.1"}]}
        ),
        encoding="utf-8",
    )
    before = tmp_path / "old.xml"
    after = tmp_path / "new.xml"
    before.write_text(BASE, encoding="utf-8")
    after.write_text(edit(BASE, 'NodeId="ns=1;i=2002"', 'NodeId="ns=1;i=2052"'), encoding="utf-8")
    result = CliRunner().invoke(
        main, ["diff", str(before), str(after), "--release-verdict", "--baseline", str(manifest)]
    )
    assert result.exit_code == 1, result.output
    assert "Not reusable" in result.output
    assert "V2/" in result.output
