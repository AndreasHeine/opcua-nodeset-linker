"""CLI tests using click's runner."""

from __future__ import annotations

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
