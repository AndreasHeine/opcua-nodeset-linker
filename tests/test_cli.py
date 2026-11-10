"""CLI tests using click's runner."""

from __future__ import annotations

import json
from pathlib import Path

from click.testing import CliRunner
from tests.conftest import DATA, NODESET_SCHEMA

from nodeset_linker.cli.main import main

CONFIG = """nodesets:\n  - {base}\n  - {machinery}\nroots:\n  - browseName=MSSControllerType\noutput: {output}\nmode: STRICT\n"""


def config_file(tmp_path: Path) -> Path:
    path = tmp_path / "config.yaml"
    path.write_text(
        CONFIG.format(
            base=(DATA / "ua_base.xml").as_posix(),
            machinery=(DATA / "machinery.xml").as_posix(),
            output=(tmp_path / "reduced.xml").as_posix(),
        ),
        encoding="utf-8",
    )
    return path


class TestCli:
    def test_analyze_reports_statistics(self, tmp_path) -> None:
        result = CliRunner().invoke(main, ["analyze", str(config_file(tmp_path))])
        assert result.exit_code == 0, result.output
        assert "original_nodes" in result.output
        assert not (tmp_path / "reduced.xml").exists()

    def test_link_writes_output(self, tmp_path) -> None:
        result = CliRunner().invoke(main, ["link", str(config_file(tmp_path))])
        assert result.exit_code == 0, result.output
        assert (tmp_path / "reduced.xml").exists()
        assert "wrote" in result.output

    def test_link_output_override(self, tmp_path) -> None:
        result = CliRunner().invoke(
            main, ["link", str(config_file(tmp_path)), "--output", str(tmp_path / "other.xml")]
        )
        assert result.exit_code == 0, result.output
        assert (tmp_path / "other.xml").exists()

    def test_explain_shows_chain(self, tmp_path) -> None:
        result = CliRunner().invoke(
            main, ["explain", "--node", "JobStateEnum", str(config_file(tmp_path))]
        )
        assert result.exit_code == 0, result.output
        assert "JobStateEnum" in result.output
        assert "retained because" in result.output

    def test_explain_removed_node(self, tmp_path) -> None:
        result = CliRunner().invoke(
            main, ["explain", "--node", "UnrelatedType", str(config_file(tmp_path))]
        )
        assert result.exit_code == 0, result.output
        assert "removed" in result.output

    def test_explain_unknown_node_fails(self, tmp_path) -> None:
        result = CliRunner().invoke(main, ["explain", "--node", "Nope", str(config_file(tmp_path))])
        assert result.exit_code == 1
        assert "error:" in result.output

    def test_stats_of_produced_nodeset(self, tmp_path) -> None:
        CliRunner().invoke(main, ["link", str(config_file(tmp_path))])
        result = CliRunner().invoke(main, ["stats", str(tmp_path / "reduced.xml")])
        assert result.exit_code == 0, result.output
        assert '"nodes"' in result.output

    def test_validate_passes(self, tmp_path) -> None:
        CliRunner().invoke(main, ["link", str(config_file(tmp_path))])
        result = CliRunner().invoke(main, ["validate", str(tmp_path / "reduced.xml")])
        assert result.exit_code == 0, result.output
        assert "validation passed" in result.output

    def test_validate_without_schema_does_not_claim_schema(self, tmp_path) -> None:
        CliRunner().invoke(main, ["link", str(config_file(tmp_path))])
        result = CliRunner().invoke(main, ["validate", str(tmp_path / "reduced.xml")])
        assert result.exit_code == 0, result.output
        assert "schema" not in result.output

    def test_validate_with_schema_checks_both(self, tmp_path) -> None:
        CliRunner().invoke(main, ["link", str(config_file(tmp_path))])
        result = CliRunner().invoke(
            main,
            ["validate", str(tmp_path / "reduced.xml"), "--schema", str(NODESET_SCHEMA)],
        )
        assert result.exit_code == 0, result.output
        assert "schema validation passed" in result.output
        assert "validation passed" in result.output

    def test_validate_reports_schema_violation_as_failure(self, tmp_path) -> None:
        CliRunner().invoke(main, ["link", str(config_file(tmp_path))])
        broken = tmp_path / "broken.xml"
        broken.write_text(
            (tmp_path / "reduced.xml")
            .read_text(encoding="utf-8")
            .replace("<UANodeSet", '<UANodeSet Bogus="1"', 1),
            encoding="utf-8",
        )
        result = CliRunner().invoke(main, ["validate", str(broken), "--schema", str(NODESET_SCHEMA)])
        assert result.exit_code == 1
        assert "schema errors" in result.output

    def test_validate_missing_schema_fails(self, tmp_path) -> None:
        CliRunner().invoke(main, ["link", str(config_file(tmp_path))])
        result = CliRunner().invoke(
            main, ["validate", str(tmp_path / "reduced.xml"), "--schema", tmp_path / "nope.xsd"]
        )
        assert result.exit_code == 1
        assert "schema not found" in result.output

    def test_invalid_config_reports_error(self, tmp_path) -> None:
        bad = tmp_path / "bad.yaml"
        bad.write_text("nodesets: []\nroots:\n  - browseName=X\n", encoding="utf-8")
        result = CliRunner().invoke(main, ["analyze", str(bad)])
        assert result.exit_code == 1
        assert "error:" in result.output

    def test_analyze_with_modelling_rule_custom(self, tmp_path) -> None:
        """End-to-end: a ``modellingRule=`` rule must survive decisions over rule-less nodes."""
        config = tmp_path / "custom.yaml"
        config.write_text(
            "nodesets:\n"
            f"  - {(DATA / 'ua_base.xml').as_posix()}\n"
            f"  - {(DATA / 'machinery.xml').as_posix()}\n"
            "roots:\n  - browseName=MSSControllerType\n"
            "mode: CUSTOM\n"
            "custom:\n  retain:\n    - modellingRule=Mandatory\n",
            encoding="utf-8",
        )
        result = CliRunner().invoke(main, ["analyze", str(config)])
        assert result.exit_code == 0, result.output
        assert "original_nodes" in result.output

    def test_bundle_manifest_cannot_escape_the_bundle(self, tmp_path) -> None:
        """Manifest entries are data, not trust: paths outside the bundle directory fail."""
        bundle = tmp_path / "bundle"
        bundle.mkdir()
        (bundle / "machinery.xml").write_text(
            (DATA / "machinery.xml").read_text(encoding="utf-8"), encoding="utf-8"
        )
        secret = tmp_path / "secret.xml"
        secret.write_text((DATA / "ua_base.xml").read_text(encoding="utf-8"), encoding="utf-8")

        for escape in ("../secret.xml", secret.as_posix()):
            (bundle / "manifest.json").write_text(
                json.dumps({"namespaces": [{"namespaceUri": "u", "output_file": escape}]}),
                encoding="utf-8",
            )
            result = CliRunner().invoke(main, ["diff", str(bundle), str(bundle)])
            assert result.exit_code == 1, result.output
            assert "outside the bundle" in result.output

    def test_bundle_manifest_inside_the_bundle_still_loads(self, tmp_path) -> None:
        bundle = tmp_path / "bundle"
        bundle.mkdir()
        (bundle / "machinery.xml").write_text(
            (DATA / "machinery.xml").read_text(encoding="utf-8"), encoding="utf-8"
        )
        (bundle / "manifest.json").write_text(
            json.dumps({"namespaces": [{"namespaceUri": "u", "output_file": "machinery.xml"}]}),
            encoding="utf-8",
        )
        result = CliRunner().invoke(main, ["diff", str(bundle), str(bundle)])
        assert result.exit_code == 0, result.output

    def test_stats_and_validate_report_bad_files_without_tracebacks(self, tmp_path) -> None:
        broken = tmp_path / "broken.xml"
        broken.write_text("<UANodeSet", encoding="utf-8")  # truncated document
        for command in ("stats", "validate"):
            result = CliRunner().invoke(main, [command, str(broken)])
            assert result.exit_code == 1, result.output
            assert "error:" in result.output
            assert "Traceback" not in result.output

    def test_corrupt_schema_fails_the_validate_cleanly(self, tmp_path) -> None:
        broken_schema = tmp_path / "broken.xsd"
        broken_schema.write_text("<xs:schema xmlns:xs=", encoding="utf-8")  # truncated XSD
        result = CliRunner().invoke(
            main,
            ["validate", str(DATA / "ua_base.xml"), "--schema", str(broken_schema)],
        )
        assert result.exit_code == 1, result.output
        assert "cannot read schema" in result.output
        assert "Traceback" not in result.output
