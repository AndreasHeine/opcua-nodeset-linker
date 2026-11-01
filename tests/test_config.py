"""Configuration tests: YAML validated by Pydantic."""

from __future__ import annotations

import pytest

from nodeset_linker.config.schema import LinkerConfig, load_config
from nodeset_linker.utils.errors import ParseError


def write(tmp_path, text: str) -> str:  # type: ignore[no-untyped-def]
    path = tmp_path / "config.yaml"
    path.write_text(text, encoding="utf-8")
    return str(path)


class TestConfig:
    def test_minimal_config(self, tmp_path) -> None:
        config = load_config(
            write(tmp_path, "nodesets:\n  - a.xml\nroots:\n  - browseName=MachineType\n")
        )
        assert config.mode == "STRICT"
        assert config.validate_before_export is True

    def test_mode_must_be_known(self, tmp_path) -> None:
        with pytest.raises(ParseError, match="invalid configuration"):
            load_config(write(tmp_path, "nodesets:\n  - a.xml\nroots:\n  - browseName=X\nmode: NOPE\n"))

    def test_roots_required(self, tmp_path) -> None:
        with pytest.raises(ParseError):
            load_config(write(tmp_path, "nodesets:\n  - a.xml\n"))

    def test_yaml_syntax_error_reported(self, tmp_path) -> None:
        with pytest.raises(ParseError, match="expected a YAML mapping"):
            load_config(write(tmp_path, "- just\n- a\n- list\n"))

    def test_custom_rules_translate(self) -> None:
        config = LinkerConfig(
            nodesets=["a.xml"],
            mode="CUSTOM",
            roots=["browseName=X"],
            custom={"retain": ["namespace=Machinery"], "exclude": ["browseName=*Diagnostics*"]},
        )
        rules = config.custom_rules()
        assert rules[0].include is True
        assert rules[0].namespace == "Machinery"
        assert rules[1].include is False
        assert rules[1].browse_name == "*Diagnostics*"

    def test_invalid_custom_rule_line(self) -> None:
        config = LinkerConfig(
            nodesets=["a.xml"], mode="CUSTOM", roots=["browseName=X"], custom={"retain": ["nonsense"]}
        )
        with pytest.raises(ParseError, match="expected key=value"):
            config.custom_rules()

    def test_unknown_custom_rule_key_is_rejected(self) -> None:
        config = LinkerConfig(
            nodesets=["a.xml"],
            mode="CUSTOM",
            roots=["browseName=X"],
            custom={"exclude": ["typeDefinition=Foo"]},
        )
        with pytest.raises(ParseError, match="supported keys"):
            config.custom_rules()

    def test_output_override(self) -> None:
        config = LinkerConfig(nodesets=["a.xml"], roots=["browseName=X"], output="out.xml")
        updated = config.model_copy(update={"output": "other.xml"})
        assert updated.output == "other.xml"


class TestExportBlock:
    """``export:`` chooses *how* the reduction is written; ``output:`` stays the legacy target."""

    def config(self, **export: str) -> LinkerConfig:
        return LinkerConfig(
            nodesets=["a.xml"], roots=["browseName=X"], export=export  # type: ignore[arg-type]
        )

    def test_merged_export_names_one_file(self) -> None:
        config = self.config(mode="merged", file="out/reduced.xml")
        assert config.export is not None
        assert config.export.mode == "merged"
        assert config.export_plan() == ("merged", "out/reduced.xml")

    def test_split_export_names_a_directory(self) -> None:
        config = self.config(mode="split", directory="out/")
        assert config.export_plan() == ("split", "out/")

    def test_merged_export_rejects_a_directory(self, tmp_path) -> None:
        with pytest.raises(ParseError, match="export.mode=merged writes one NodeSet"):
            load_config(
                write(tmp_path, "nodesets:\n  - a.xml\nroots:\n  - browseName=X\nexport:\n  mode: merged\n  directory: out/\n")
            )

    def test_split_export_rejects_a_file(self, tmp_path) -> None:
        with pytest.raises(ParseError, match="export.mode=split writes one NodeSet per namespace"):
            load_config(
                write(tmp_path, "nodesets:\n  - a.xml\nroots:\n  - browseName=X\nexport:\n  mode: split\n  file: out.xml\n")
            )

    def test_export_needs_a_target(self, tmp_path) -> None:
        with pytest.raises(ParseError, match="needs export.file"):
            load_config(write(tmp_path, "nodesets:\n  - a.xml\nroots:\n  - browseName=X\nexport:\n  mode: merged\n"))
        with pytest.raises(ParseError, match="needs export.directory"):
            load_config(write(tmp_path, "nodesets:\n  - a.xml\nroots:\n  - browseName=X\nexport:\n  mode: split\n"))

    def test_blank_target_rejected(self, tmp_path) -> None:
        with pytest.raises(ParseError, match="export target must be a path"):
            load_config(
                write(tmp_path, "nodesets:\n  - a.xml\nroots:\n  - browseName=X\nexport:\n  mode: split\n  directory: '   '\n")
            )

    def test_unknown_export_mode_rejected(self, tmp_path) -> None:
        with pytest.raises(ParseError, match="invalid configuration"):
            load_config(
                write(tmp_path, "nodesets:\n  - a.xml\nroots:\n  - browseName=X\nexport:\n  mode: merged-split\n  file: out.xml\n")
            )

    def test_export_block_wins_over_legacy_output(self) -> None:
        config = LinkerConfig(
            nodesets=["a.xml"],
            roots=["browseName=X"],
            output="legacy.xml",
            export={"mode": "split", "directory": "out/"},  # type: ignore[arg-type]
        )
        assert config.export_plan() == ("split", "out/")

    def test_legacy_output_still_exports_merged(self) -> None:
        config = LinkerConfig(nodesets=["a.xml"], roots=["browseName=X"], output="out/reduced.xml")
        assert config.export_plan() == ("merged", "out/reduced.xml")

    def test_no_target_means_no_export(self) -> None:
        config = LinkerConfig(nodesets=["a.xml"], roots=["browseName=X"])
        assert config.export_plan() is None

    def test_split_export_falls_back_to_legacy_output(self) -> None:
        config = LinkerConfig(
            nodesets=["a.xml"],
            roots=["browseName=X"],
            output="out/",
            export={"mode": "split"},  # type: ignore[arg-type]
        )
        assert config.export_plan() == ("split", "out/")
