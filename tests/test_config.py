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
