"""A reduced export that externalizes a namespace is not a removal (§11.2, §3.2.16)."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from click.testing import CliRunner
from tests.conftest import MACHINERY, Machinery_NS, make_object, node_id

from nodeset_linker.cli.main import main
from nodeset_linker.diff.engine import diff_models
from nodeset_linker.models.namespace import ModelInfo, Namespace
from nodeset_linker.models.node import UANode
from nodeset_linker.repository.model import SemanticModel, build_model

VENDOR_NS = "http://vendor.example.com/ACME/"
OTHER_NS = "http://vendor.example.com/Other/"


def _in(namespace: str, number: int, browse: str) -> UANode:
    """A conftest builder always targets the OPC UA namespace; a companion node does not."""
    return replace(make_object(browse, number), node_id=node_id(number, namespace))


def _source(nodes: list[UANode], namespaces: dict[str, Namespace]) -> SemanticModel:
    return build_model(nodes, namespaces)


def _export(nodes: list[UANode], *, required: tuple[str, ...]) -> SemanticModel:
    """A document that ships `nodes` and declares `required` as models it does not ship."""
    model = build_model(nodes, {VENDOR_NS: Namespace(uri=VENDOR_NS)})
    model.models[VENDOR_NS] = ModelInfo(
        namespace_uri=VENDOR_NS,
        model_name="ACME",
        required_models=required,
    )
    return model


class TestReducedExport:
    def test_externalized_namespace_is_information(self) -> None:
        old = _source([_in(Machinery_NS, 9001, "Job")], {Machinery_NS: Namespace(uri=Machinery_NS)})
        new = _export([_in(VENDOR_NS, 9010, "Pump")], required=(Machinery_NS,))
        diff = diff_models(old, new, reduced_export=True)
        assert not diff.breaking
        assert [
            finding.rule_id for finding in diff.removed[0].verdicts
        ] == ["BP-Declaration-Externalized"]

    def test_without_the_option_a_removal_still_breaks(self) -> None:
        old = _source([_in(Machinery_NS, 9001, "Job")], {Machinery_NS: Namespace(uri=Machinery_NS)})
        new = _export([_in(VENDOR_NS, 9010, "Pump")], required=(Machinery_NS,))
        diff = diff_models(old, new)
        assert [entry.rule_id for entry in diff.breaking] == ["BP-Declaration-Removed-Optional"]

    def test_only_declared_required_models_are_carved_out(self) -> None:
        """A namespace the export neither ships nor declares is a real removal (§3.2.16)."""
        old = _source(
            [_in(Machinery_NS, 9001, "Job"), _in(OTHER_NS, 9002, "Pump")],
            {Machinery_NS: Namespace(uri=Machinery_NS), OTHER_NS: Namespace(uri=OTHER_NS)},
        )
        new = _export([_in(VENDOR_NS, 9010, "Valve")], required=(Machinery_NS,))
        diff = diff_models(old, new, reduced_export=True)
        assert [entry.label for entry in diff.breaking] == ["Pump"]


_EXPORT_XML = """<?xml version="1.0" encoding="utf-8"?>
<UANodeSet xmlns="http://opcfoundation.org/UA/2011/03/UANodeSet.xsd">
  <NamespaceUris>
    <Uri>http://vendor.example.com/ACME/</Uri>
  </NamespaceUris>
  <Models>
    <Model ModelUri="http://vendor.example.com/ACME/">
      <RequiredModel ModelUri="http://opcfoundation.org/UA/Machinery/"/>
    </Model>
  </Models>
  <UAObject NodeId="ns=1;i=9001" BrowseName="1:Pump">
    <DisplayName>Pump</DisplayName>
  </UAObject>
</UANodeSet>
"""


class TestReducedExportCli:
    def test_flag_carves_out_an_export_of_a_required_model(self, tmp_path: Path) -> None:
        export = tmp_path / "export.xml"
        export.write_text(_EXPORT_XML, encoding="utf-8")
        plain = CliRunner().invoke(main, ["diff", str(MACHINERY), str(export), "--fail-on-breaking"])
        assert plain.exit_code == 1, plain.output
        carved = CliRunner().invoke(
            main,
            ["diff", str(MACHINERY), str(export), "--reduced-export", "--fail-on-breaking"],
        )
        assert carved.exit_code == 0, carved.output
