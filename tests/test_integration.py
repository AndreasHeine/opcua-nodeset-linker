"""Integration tests: the whole pipeline end to end."""

from __future__ import annotations

import pytest
from lxml.etree import parse as xml_parse
from tests.conftest import DATA

from nodeset_linker.config.schema import LinkerConfig
from nodeset_linker.pipeline import LinkerPipeline


def config(**overrides) -> LinkerConfig:  # type: ignore[no-untyped-def]
    defaults = {
        "nodesets": [str(DATA / "ua_base.xml"), str(DATA / "machinery.xml")],
        "roots": ["browseName=MSSControllerType"],
        "output": None,
    }
    defaults.update(overrides)
    return LinkerConfig(**defaults)  # type: ignore[arg-type]


class TestPipeline:
    def test_strict_keeps_mandatory_and_optional(self, tmp_path) -> None:
        result = LinkerPipeline(config(mode="STRICT", output=str(tmp_path / "strict.xml"))).run()
        assert result.validation is not None and result.validation.ok
        assert result.dependency_report is not None
        assert result.dependency_report.optional  # optional nodes kept in STRICT

    def test_runtime_keeps_nodes_that_still_have_references(self, tmp_path) -> None:
        """RUNTIME drops Optional nodes only when nothing points at them any more."""
        strict = LinkerPipeline(config(mode="STRICT", output=str(tmp_path / "strict.xml"))).run()
        runtime = LinkerPipeline(config(mode="RUNTIME", output=str(tmp_path / "runtime.xml"))).run()
        assert runtime.retained < strict.retained
        assert runtime.validation is not None and runtime.validation.ok

    def test_runtime_repairs_a_component_still_referenced_by_its_parent(self, tmp_path) -> None:
        """MSSControllerType keeps a HasComponent reference to Diagnostics, so it stays."""
        runtime = LinkerPipeline(config(mode="RUNTIME", output=str(tmp_path / "runtime.xml"))).run()
        assert runtime.validation is not None and runtime.validation.ok
        diagnostics = {n.node_id for n in runtime.model.iter_nodes() if n.browse_name.name == "Diagnostics"}
        assert diagnostics & runtime.retained

    def test_retention_that_breaks_the_model_is_reported_not_exported(self, tmp_path) -> None:
        """A custom exclude that leaves a dangling reference is refused, never exported."""
        from nodeset_linker.utils.errors import ValidationError

        with pytest.raises(ValidationError) as excinfo:
            LinkerPipeline(
                config(
                    mode="CUSTOM",
                    output=str(tmp_path / "bad.xml"),
                    custom={"exclude": ["browseName=Diagnostics"]},
                )
            ).run()
        assert not (tmp_path / "bad.xml").exists()
        assert excinfo.value.findings is not None
        assert any(f.code == "REF_TARGET_MISSING" for f in excinfo.value.findings.errors)

    def test_custom_excludes_an_unreferenced_optional_object(self, tmp_path) -> None:
        result = LinkerPipeline(
            config(
                mode="CUSTOM",
                output=str(tmp_path / "custom.xml"),
                custom={"exclude": ["browseName=MachineDiagnostics"]},
            )
        ).run()
        assert "MachineDiagnostics" not in {
            n.browse_name.name for n in result.model.iter_nodes() if n.node_id in result.retained
        }
        assert result.validation is not None and result.validation.ok

    def test_inheritance_chain_never_broken(self, tmp_path) -> None:
        result = LinkerPipeline(config(output=str(tmp_path / "reduced.xml"))).run()
        assert result.output_path is not None
        root = xml_parse(str(result.output_path)).getroot()
        node_ids = {el.get("NodeId") for el in root if el.tag.startswith("{http://opcfoundation.org/UA/}UA")}
        targets = {
            el.get("TargetId")
            for el in root.iter("References")
            if el.get("ReferenceType") == "HasSubtype"
        }
        assert targets <= node_ids  # every subtype base is present

    def test_exported_model_is_self_contained(self, tmp_path) -> None:
        result = LinkerPipeline(config(output=str(tmp_path / "reduced.xml"))).run()
        from nodeset_linker.repository.model import load_nodesets

        reloaded = load_nodesets([result.output_path])
        assert reloaded.unresolved_targets() == []

    def test_namespace_metadata_preserved(self, tmp_path) -> None:
        result = LinkerPipeline(config(output=str(tmp_path / "reduced.xml"))).run()
        text = result.output_path.read_text(encoding="utf-8")
        assert "http://opcfoundation.org/UA/Machinery/" in text
        assert 'Version="1.0.1"' in text

    def test_cross_namespace_dependency_kept(self, tmp_path) -> None:
        result = LinkerPipeline(config(output=str(tmp_path / "reduced.xml"))).run()
        assert result.namespace_report is not None
        assert result.namespace_report.cross_namespace_edges

    def test_analyze_does_not_write(self) -> None:
        result = LinkerPipeline(config(output="out/should-not-exist.xml")).analyze()
        assert result.output_path is None

    def test_multiple_roots_merge_closures(self) -> None:
        result = LinkerPipeline(
            config(roots=["browseName=MSSControllerType", "nodeid=nsu=http://opcfoundation.org/UA/Machinery/;i=2500"])
        ).analyze()
        assert result.dependency_report is not None
        assert len(result.dependency_report.roots) == 2

    def test_unreachable_nodes_are_removed(self) -> None:
        result = LinkerPipeline(config(roots=["browseName=JobDataType"])).analyze()
        names = {n.browse_name.name for n in result.model.iter_nodes() if n.node_id not in result.retained}
        assert "UnrelatedType" in names
