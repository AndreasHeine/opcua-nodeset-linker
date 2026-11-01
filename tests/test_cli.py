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


def split_config_file(tmp_path: Path) -> Path:
    """A split-export configuration: the OPC UA core is externalized, so two files are written."""
    path = tmp_path / "split.yaml"
    path.write_text(
        "\n".join(
            [
                "nodesets:",
                f"  - {(DATA / 'ua_base.xml').as_posix()}",
                f"  - {(DATA / 'machinery.xml').as_posix()}",
                f"  - {(DATA / 'jobcontrol.xml').as_posix()}",
                "mode: STRICT",
                "roots: [namespaceUri=http://example.org/UA/JobControl/]",
                "external: [http://opcfoundation.org/UA/]",
                "export:",
                "  mode: split",
                f"  directory: {(tmp_path / 'out').as_posix()}",
            ]
        ),
        encoding="utf-8",
    )
    return path


class TestSplitExportCli:
    def test_link_split_writes_one_file_per_namespace(self, tmp_path) -> None:
        result = CliRunner().invoke(main, ["link", str(split_config_file(tmp_path))])
        assert result.exit_code == 0, result.output
        assert (tmp_path / "out" / "JobControl.xml").exists()
        assert (tmp_path / "out" / "Machinery.xml").exists()
        assert (tmp_path / "out" / "manifest.json").exists()
        assert "NodeSets" in result.output
        assert "manifest.json" in result.output

    def test_link_split_output_override_writes_the_given_folder(self, tmp_path) -> None:
        result = CliRunner().invoke(
            main, ["link", str(split_config_file(tmp_path)), "--output", str(tmp_path / "dist")]
        )
        assert result.exit_code == 0, result.output
        assert (tmp_path / "dist" / "JobControl.xml").exists()
        assert (tmp_path / "dist" / "manifest.json").exists()
        assert not (tmp_path / "out").exists()

    def test_stats_reads_a_whole_split_export(self, tmp_path) -> None:
        CliRunner().invoke(main, ["link", str(split_config_file(tmp_path))])
        result = CliRunner().invoke(main, ["stats", str(tmp_path / "out")])
        assert result.exit_code == 0, result.output
        assert '"nodesets": 2' in result.output

    def test_validate_loads_the_split_files_together(self, tmp_path) -> None:
        CliRunner().invoke(main, ["link", str(split_config_file(tmp_path))])
        result = CliRunner().invoke(main, ["validate", str(tmp_path / "out")])
        assert result.exit_code == 0, result.output
        assert "validation passed" in result.output

    def test_validate_a_split_export_against_the_schema(self, tmp_path) -> None:
        CliRunner().invoke(main, ["link", str(split_config_file(tmp_path))])
        result = CliRunner().invoke(
            main, ["validate", str(tmp_path / "out"), "--schema", str(NODESET_SCHEMA)]
        )
        assert result.exit_code == 0, result.output
        assert "schema validation passed" in result.output
        # A folder is several documents, so each pass names the file it checked.
        assert "JobControl.xml" in result.output
        assert "Machinery.xml" in result.output

    def test_a_folder_without_nodesets_fails(self, tmp_path) -> None:
        empty = tmp_path / "empty"
        empty.mkdir()
        result = CliRunner().invoke(main, ["stats", str(empty)])
        assert result.exit_code == 1
        assert "no NodeSet files found" in result.output
