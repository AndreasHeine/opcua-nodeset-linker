"""The JSON contract of ``diff --json``: keys are additive, and a removed key is a bug."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from click.testing import CliRunner
from tests.conftest import MACHINERY

from nodeset_linker.cli.main import main

BASE = MACHINERY.read_text(encoding="utf-8")

#: The keys `diff --json` has always produced. Adding a key is allowed; dropping one is not.
TOP_LEVEL = frozenset(
    {
        "old",
        "new",
        "paper",
        "summary",
        "added_nodes",
        "removed_nodes",
        "modified_nodes",
        "renumbered_nodes",
        "namespace_changes",
        "breaking_changes",
        "advisory_changes",
    }
)
SUMMARY = frozenset(
    {
        "old_nodes",
        "new_nodes",
        "added_nodes",
        "removed_nodes",
        "modified_nodes",
        "renumbered_nodes",
        "breaking_changes",
        "advisory_changes",
        "advisory_nodes",
        "info_nodes",
        "namespace_changes",
        "identical",
        # WP 15: an additive count; it is 0 when no waiver ran.
        "waived_changes",
    }
)
NODE_ENTRY = frozenset({"node_id", "label", "node_class", "namespace", "severity", "verdicts"})
CHANGE_ENTRY = frozenset(
    {"label", "node_id", "reason", "affected_nodes", "breaking", "severity", "rule_id", "rationale", "clause"}
)
NAMESPACE_ENTRY = frozenset({"namespace", "uri", "severity", "rule_id", "metadata", "nodes", "references", "datatypes"})
METADATA_DELTA = frozenset(
    {"attribute", "old", "new", "severity", "rule_id", "rationale", "clause", "note"}
)
VERDICT = frozenset({"severity", "rule_id", "rationale", "clause", "paper"})


def payload(tmp_path: Path, old: str, new: str, *flags: str) -> dict:
    """One JSON report, written through the CLI so the contract covers the real output path."""
    before = tmp_path / "old.xml"
    after = tmp_path / "new.xml"
    report = tmp_path / "report.json"
    before.write_text(old, encoding="utf-8")
    after.write_text(new, encoding="utf-8")
    result = CliRunner().invoke(
        main, ["diff", str(before), str(after), "--json", "--output", str(report), *flags]
    )
    assert result.exit_code in (0, 1), result.output
    return json.loads(report.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def rich_pair() -> tuple[str, str]:
    """A pair that populates every list in the report: removal, renumbering, rename, metadata."""
    removed = BASE.replace(
        """  <UAObject NodeId="ns=1;i=2300" BrowseName="1:MachineDiagnostics" ParentNodeId="ns=1;i=2200">
    <DisplayName>MachineDiagnostics</DisplayName>
    <References>
      <Reference ReferenceType="HasModellingRule">i=80</Reference>
      <Reference ReferenceType="HasComponent" IsForward="false">ns=1;i=2200</Reference>
    </References>
  </UAObject>
""",
        "",
    )
    moved = removed.replace('NodeId="ns=1;i=2002"', 'NodeId="ns=1;i=2052"')
    renamed = moved.replace('BrowseName="1:JobStageEnum"', 'BrowseName="1:JobStepEnum"')
    bumped = renamed.replace(
        'Version="1.0.1" PublicationDate="2024-01-01T00:00:00Z" ModelVersion="1.0.1"',
        'Version="1.0.2" PublicationDate="2024-06-01T00:00:00Z" ModelVersion="1.0.2"',
    )
    added = bumped.replace(
        "</UANodeSet>",
        """  <UAObject NodeId="ns=1;i=2301" BrowseName="1:OptionalDiagnostics" ParentNodeId="ns=1;i=2200">
    <DisplayName>OptionalDiagnostics</DisplayName>
    <References>
      <Reference ReferenceType="HasModellingRule">i=80</Reference>
      <Reference ReferenceType="HasComponent" IsForward="false">ns=1;i=2200</Reference>
    </References>
  </UAObject>
</UANodeSet>""",
    )
    return BASE, added


def test_report_keeps_every_top_level_key(tmp_path: Path, rich_pair: tuple[str, str]) -> None:
    old, new = rich_pair
    document = payload(tmp_path, old, new)
    assert frozenset(document) == TOP_LEVEL
    assert frozenset(document["summary"]) == SUMMARY
    for key in ("added_nodes", "removed_nodes", "modified_nodes", "renumbered_nodes"):
        assert document[key], key
    for entry in [*document["added_nodes"], *document["removed_nodes"], *document["modified_nodes"]]:
        assert frozenset(entry) >= NODE_ENTRY, entry["label"]
        for verdict in entry["verdicts"]:
            assert frozenset(verdict) == VERDICT
    assert document["breaking_changes"]
    assert document["summary"]["info_nodes"] > 0
    assert document["summary"]["waived_changes"] == 0
    for entry in [*document["breaking_changes"], *document["advisory_changes"]]:
        assert frozenset(entry) == CHANGE_ENTRY, entry["label"]


def test_namespace_entry_keeps_totals_and_metadata(tmp_path: Path, rich_pair: tuple[str, str]) -> None:
    old, new = rich_pair
    document = payload(tmp_path, old, new)
    assert document["namespace_changes"], "the version bump must produce a namespace entry"
    entry = document["namespace_changes"][0]
    assert frozenset(entry) == NAMESPACE_ENTRY
    for part in ("nodes", "references", "datatypes"):
        assert frozenset(entry[part]) == {"old", "new", "delta"}
    assert entry["metadata"]
    for delta in entry["metadata"]:
        assert frozenset(delta) == METADATA_DELTA


def test_release_key_is_additive_and_only_on_request(tmp_path: Path, rich_pair: tuple[str, str]) -> None:
    old, new = rich_pair
    plain = payload(tmp_path, old, new)
    assert "release" not in plain

    manifest = tmp_path / "manifest.json"
    manifest.write_text(
        json.dumps(
            {"namespaces": [{"uri": "http://opcfoundation.org/UA/Machinery/", "version": "1.0.1"}]}
        ),
        encoding="utf-8",
    )
    asked = payload(tmp_path, old, new, "--release-verdict", "--baseline", str(manifest))
    assert frozenset(asked) == TOP_LEVEL | {"release"}
    assert asked["release"]["reusable"] is False


def test_impact_keys_are_additive(tmp_path: Path, rich_pair: tuple[str, str]) -> None:
    old, new = rich_pair
    document = payload(tmp_path, old, new, "--impact")
    assert frozenset(document) >= TOP_LEVEL
    for entry in [*document["added_nodes"], *document["removed_nodes"], *document["modified_nodes"]]:
        assert {"impacted_nodes", "impacted_labels", "impact_count"} <= frozenset(entry)
