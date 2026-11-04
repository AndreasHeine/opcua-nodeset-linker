"""WP 15: waivers accept findings without deleting them.

A waiver marks a finding (``waived``, ``waived_by``) and never changes its severity; only the
release gate stops counting it.  Breaking findings cannot be waived - ``--accept-breaking``
acknowledges them, and that acknowledgement never makes a published NamespaceUri reusable.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from click.testing import CliRunner
from tests.conftest import MACHINERY

from nodeset_linker.cli.main import main
from nodeset_linker.diff.waivers import Waiver, WaiverFileError, WaiverSet

BASE = MACHINERY.read_text(encoding="utf-8")
MACHINERY_NS = "http://opcfoundation.org/UA/Machinery/"
MODEL = (
    '<Model ModelUri="http://opcfoundation.org/UA/Machinery/" '
    'XmlSchemaUri="http://opcfoundation.org/UA/Machinery/Types.xsd" '
    'Version="1.0.1" PublicationDate="2024-01-01T00:00:00Z" ModelVersion="1.0.1">'
)


def edit(text: str, anchor: str, replacement: str) -> str:
    """One anchored XML edit; a missing anchor is a broken fixture, not a passing test."""
    assert anchor in text, f"anchor not in fixture: {anchor!r}"
    return text.replace(anchor, replacement, 1)


def broken_pair() -> tuple[str, str]:
    """A moved identifier plus metadata-only drift plus two DisplayName edits.

    The move is breaking, the drift is advisory and information, the DisplayName edits are
    information - enough severity buckets for a waiver to have something to accept, refuse and
    narrow.
    """
    moved = edit(BASE, 'NodeId="ns=1;i=2002"', 'NodeId="ns=1;i=2052"')
    bumped = edit(
        moved,
        MODEL,
        MODEL.replace(
            'Version="1.0.1" PublicationDate="2024-01-01T00:00:00Z" ModelVersion="1.0.1"',
            'Version="1.0.2" PublicationDate="2024-06-01T00:00:00Z" ModelVersion="1.0.1"',
        ),
    )
    renamed_wording = edit(
        bumped,
        "<DisplayName>JobStageEnum</DisplayName>",
        "<DisplayName>Job step</DisplayName>",
    )
    return BASE, edit(
        renamed_wording,
        "<DisplayName>MachineStatusStructure</DisplayName>",
        "<DisplayName>Machine status</DisplayName>",
    )


def write_pair(tmp_path: Path) -> tuple[Path, Path]:
    old, new = broken_pair()
    before = tmp_path / "old.xml"
    after = tmp_path / "new.xml"
    before.write_text(old, encoding="utf-8")
    after.write_text(new, encoding="utf-8")
    return before, after


def report(tmp_path: Path, *flags: str) -> dict:
    """One JSON report written by the CLI, plus its exit code."""
    report_path = tmp_path / "report.json"
    before, after = write_pair(tmp_path)
    outcome = CliRunner().invoke(
        main, ["diff", str(before), str(after), "--json", "--output", str(report_path), *flags]
    )
    assert outcome.exit_code in (0, 1), outcome.output
    return json.loads(report_path.read_text(encoding="utf-8"))


def markdown(tmp_path: Path, *flags: str) -> tuple[str, int]:
    """One Markdown report written by the CLI, with the exit code it produced."""
    report_path = tmp_path / "report.md"
    before, after = write_pair(tmp_path)
    outcome = CliRunner().invoke(
        main, ["diff", str(before), str(after), "--markdown", "--output", str(report_path), *flags]
    )
    return report_path.read_text(encoding="utf-8"), outcome.exit_code


def waiver_file(tmp_path: Path, entries: list[dict]) -> str:
    path = tmp_path / "waivers.json"
    path.write_text(json.dumps(entries), encoding="utf-8")
    return str(path)


def test_waive_removes_an_advisory_from_the_gate_but_not_the_report(tmp_path: Path) -> None:
    text, gate = markdown(tmp_path, "--fail-on-advisory")
    assert gate == 1, "an advisory finding fails --fail-on-advisory by design"
    assert "BP-Namespace-VersionDrift" in text

    waived_text, waived_gate = markdown(
        tmp_path, "--fail-on-advisory", "--waive", "BP-Namespace-VersionDrift"
    )
    assert waived_gate == 0, "a waived advisory must not gate the release"
    assert "BP-Namespace-VersionDrift" in waived_text, "the finding stays visible after a waiver"
    assert "[waived]" in waived_text
    assert "Waivers" in waived_text
    assert "finding(s) waived" in waived_text


def test_waive_marks_a_metadata_delta_in_json(tmp_path: Path) -> None:
    document = report(tmp_path, "--waive", "BP-Namespace-Version")
    assert document["summary"]["waived_changes"] > 0
    deltas = [delta for entry in document["namespace_changes"] for delta in entry["metadata"]]
    waived = [delta for delta in deltas if delta.get("waived")]
    assert waived, "the metadata delta must carry the marker"
    assert {delta["waived_by"] for delta in waived} == {"--waive BP-Namespace-Version"}
    assert all(delta["severity"] == "info" for delta in waived), "severity is never re-derived"
    assert all("waived" not in delta for delta in deltas if not delta.get("waived"))


def test_waiver_file_scopes_a_node_finding_to_one_node(tmp_path: Path) -> None:
    path = waiver_file(
        tmp_path,
        [
            {
                "rule": "BP-DisplayName-Meaning",
                "node": "JobStageEnum",
                "reason": "wording approved by the companion owner",
            },
            {
                "rule": "BP-Namespace-Version",
                "namespace": MACHINERY_NS,
                "reason": "drift accepted for this release",
            },
        ],
    )
    document = report(tmp_path, "--waiver-file", path)
    verdicts = [verdict for entry in document["modified_nodes"] for verdict in entry["verdicts"]]
    waived = [verdict for verdict in verdicts if verdict.get("waived")]
    assert len(waived) == 1, "a node-scoped waiver must not cover the whole model"
    assert waived[0]["waived_by"] == "wording approved by the companion owner"

    deltas = [delta for entry in document["namespace_changes"] for delta in entry["metadata"]]
    assert any(delta.get("waived_by") == "drift accepted for this release" for delta in deltas)


def test_a_breaking_finding_cannot_be_waived(tmp_path: Path) -> None:
    document = report(tmp_path, "--waive", "BP-NodeId-Immutable", "--fail-on-breaking")
    assert document["breaking_changes"], "the move is still reported"
    assert all("waived" not in entry for entry in document["breaking_changes"])
    assert document["waivers"]["accepted_breaking"] is False
    ignored = document["waivers"]["ignored"]
    assert any("BP-NodeId-Immutable" in line and "--accept-breaking" in line for line in ignored)

    text, gate = markdown(
        tmp_path, "--fail-on-breaking", "--waive", "BP-NodeId-Immutable"
    )
    assert gate == 1
    assert "waiver ignored" in text


def test_accept_breaking_opens_the_gate_and_is_printed(tmp_path: Path) -> None:
    document = report(tmp_path, "--fail-on-breaking", "--accept-breaking")
    assert document["breaking_changes"], "an acknowledged breaking change is still a finding"
    assert document["breaking_changes"][0]["waived"] is True
    assert document["breaking_changes"][0]["waived_by"] == "--accept-breaking"
    assert document["waivers"]["accepted_breaking"] is True
    assert document["summary"]["breaking_changes"] > 0, "the severity bucket keeps its count"

    text, gate = markdown(tmp_path, "--fail-on-breaking", "--accept-breaking")
    assert gate == 0
    assert "BREAKING changes acknowledged with --accept-breaking" in text


def test_acknowledging_breaking_never_makes_a_published_namespace_reusable(tmp_path: Path) -> None:
    manifest = tmp_path / "manifest.json"
    manifest.write_text(
        json.dumps({"namespaces": [{"uri": MACHINERY_NS, "version": "1.0.1"}]}), encoding="utf-8"
    )
    document = report(
        tmp_path, "--release-verdict", "--baseline", str(manifest), "--accept-breaking"
    )
    assert document["release"]["reusable"] is False, "§11.3: a published URI stays not reusable"
    assert any(entry["reusable"] is False for entry in document["release"]["namespaces"])

    before, after = write_pair(tmp_path)
    outcome = CliRunner().invoke(
        main,
        [
            "diff",
            str(before),
            str(after),
            "--release-verdict",
            "--baseline",
            str(manifest),
            "--accept-breaking",
            "--fail-on-breaking",
        ],
    )
    assert outcome.exit_code == 1, "the release verdict is the gate, not the acknowledgement"


def test_no_waiver_flags_leave_the_report_as_it_was(tmp_path: Path) -> None:
    document = report(tmp_path)
    assert "waivers" not in document
    assert document["summary"]["waived_changes"] == 0
    assert all("waived" not in entry for entry in document["breaking_changes"])
    assert all("waived" not in entry for entry in document["advisory_changes"])
    text, _ = markdown(tmp_path)
    assert "Waivers" not in text


@pytest.mark.parametrize(
    ("entries", "expected"),
    [
        ([{"namespace": MACHINERY_NS}], "'rule' is required"),
        ([{"rule": "BP-Namespace-Version", "node": 5}], "'node' must be a string"),
        ([{"rule": "BP-Namespace-Version", "scope": "all"}], "unknown keys"),
        ([{"rule": "BP-Namespace-Version"}, "not an object"], "expected an object"),
    ],
)
def test_waiver_file_reports_what_is_wrong(
    tmp_path: Path, entries: list[object], expected: str
) -> None:
    before, after = write_pair(tmp_path)
    path = tmp_path / "waivers.json"
    path.write_text(json.dumps(entries), encoding="utf-8")
    outcome = CliRunner().invoke(
        main, ["diff", str(before), str(after), "--waiver-file", str(path)]
    )
    assert outcome.exit_code == 1
    assert expected in outcome.output


def test_waiver_file_must_be_an_array_or_a_waivers_object(tmp_path: Path) -> None:
    before, after = write_pair(tmp_path)
    for body, expected in (
        ('{"rule": "BP-Namespace-Version"}', "must hold a JSON array"),
        ("not json at all", "not valid JSON"),
    ):
        path = tmp_path / "waivers.json"
        path.write_text(body, encoding="utf-8")
        outcome = CliRunner().invoke(
            main, ["diff", str(before), str(after), "--waiver-file", str(path)]
        )
        assert outcome.exit_code == 1
        assert expected in outcome.output


def test_waiver_object_shape_is_parsed_from_the_file(tmp_path: Path) -> None:
    path = tmp_path / "waivers.json"
    path.write_text(json.dumps({"waivers": [{"rule": "BP-Namespace-Version"}]}), encoding="utf-8")
    waiver_set = WaiverSet.from_cli(("BP-Namespace-PublicationDate",), path)
    assert waiver_set.rules() == ("BP-Namespace-PublicationDate", "BP-Namespace-Version")
    assert waiver_set.find("BP-Namespace-Version", uri=MACHINERY_NS, alias="Machinery") is not None
    assert waiver_set.find("BP-Namespace-ModelVersion", uri=MACHINERY_NS) is None

    scoped = Waiver(rule="BP-Namespace-Version", namespace="Other")
    assert scoped.matches_namespace(MACHINERY_NS, "Machinery") is False
    assert scoped.matches_namespace("http://example.invalid/UA/Other/", "Other") is True

    narrow = Waiver(rule="BP-DisplayName-Meaning", node="JobStageEnum")
    assert narrow.matches_node("JobStageEnum", "ns=1;i=2003", "i=2003")
    assert not narrow.matches_node("MachineStatusStructure", "ns=1;i=2020", "i=2020")
    with pytest.raises(WaiverFileError):
        Waiver.from_json({"rule": "  "}, where="waivers.json[0]")
