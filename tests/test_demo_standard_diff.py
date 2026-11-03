"""Naturally retained Machinery datatype and modelling-rule edits trigger the diff gate."""

from __future__ import annotations

import json
from pathlib import Path

from click.testing import CliRunner
from lxml.etree import XMLSchema, parse
from tests.conftest import NODESET_SCHEMA

from nodeset_linker.cli.main import main
from nodeset_linker.config.schema import ExportConfig, load_config
from nodeset_linker.diff import apply_impact, diff_models, total_impact
from nodeset_linker.models.identifiers import ExpandedNodeId
from nodeset_linker.models.node_class import ModellingRule
from nodeset_linker.pipeline import LinkerPipeline
from nodeset_linker.repository.model import load_nodesets

ROOT = Path(__file__).parent.parent
DEMO = ROOT / "examples" / "demo4"
TARGET = ExpandedNodeId.parse("nsu=http://opcfoundation.org/UA/Machinery/;i=6015")
RULE_TARGET = ExpandedNodeId.parse("nsu=http://opcfoundation.org/UA/Machinery/;i=6029")


def test_standard_nodeset_breaking_change_demo(tmp_path) -> None:
    configs = [load_config(DEMO / f"config_{name}.yaml") for name in ("original", "modified")]
    original_config, modified_config = configs
    assert original_config.model_dump(exclude={"nodesets", "export"}) == (
        modified_config.model_dump(exclude={"nodesets", "export"})
    )
    assert [
        (old, new) for old, new in zip(
            original_config.nodesets, modified_config.nodesets, strict=True
        ) if old != new
    ] == [(
        "examples/nodesets/Opc.Ua.Machinery.NodeSet2.xml",
        "examples/nodesets/Opc.Ua.Machinery.NodeSet2-Modified.xml",
    )]
    assert original_config.roots == [
        "nodeid=nsu=http://umati.org/UA/ShowCaseMachineTool/;i=5003",
    ]
    schema = XMLSchema(parse(str(NODESET_SCHEMA)))
    models = []
    outputs = []
    for config in configs:
        output = tmp_path / Path(config.export.file).name
        resolved = config.model_copy(update={
            "nodesets": [str(ROOT / path) for path in config.nodesets],
            "export": ExportConfig(file=str(output)),
        })
        result = LinkerPipeline(resolved).run()
        assert result.validation.ok
        assert len(result.retained) == 1110
        assert TARGET in result.retained
        assert RULE_TARGET in result.retained
        assert schema.validate(parse(str(output))), str(schema.error_log)
        assert output.read_bytes() == (ROOT / config.export.file).read_bytes()
        reloaded = load_nodesets([output])
        assert set(reloaded.nodes) == result.retained
        assert load_nodesets([
            output, ROOT / "examples" / "nodesets" / "Opc.Ua.NodeSet2.xml",
        ]).unresolved_targets() == []
        models.append(reloaded)
        outputs.append(str(output))

    original, modified = models
    assert original.nodes.keys() == modified.nodes.keys()
    assert original.nodes[TARGET].data_type == ExpandedNodeId.parse("i=12")
    assert modified.nodes[TARGET].data_type == ExpandedNodeId.parse("i=11")
    assert original.nodes[TARGET].parent_node_id == ExpandedNodeId.parse(
        "nsu=http://opcfoundation.org/UA/Machinery/;i=1012"
    )
    comparison = diff_models(original, modified)
    apply_impact(comparison, original, modified)
    assert original.nodes[RULE_TARGET].modelling_rule is ModellingRule.OPTIONAL
    assert modified.nodes[RULE_TARGET].modelling_rule is ModellingRule.MANDATORY
    assert len(comparison.modified) == len(comparison.breaking) == 2
    changes = {entry.node_id: entry for entry in comparison.modified}
    assert set(changes) == {TARGET, RULE_TARGET}
    change = changes[TARGET]
    assert change.node_id == TARGET and change.label == "ProductInstanceUri"
    assert {"MachineIdentificationType", "ShowCaseMachineTool"} <= set(change.impact)
    assert len(change.impact) == 165
    assert [(item.kind, item.old, item.new, item.breaking) for item in change.changes] == [
        ("data_type", "String (i=12)", "Double (i=11)", True),
    ]
    rule_change = changes[RULE_TARGET]
    assert rule_change.label == "Location"
    assert {"MachineIdentificationType", "ShowCaseMachineTool"} <= set(rule_change.impact)
    assert any(
        (item.kind, item.old, item.new, item.breaking)
        == ("modelling_rule", "Optional", "Mandatory", True)
        for item in rule_change.changes
    )
    assert any(
        "Modelling rule: Optional" in entry.reason for entry in comparison.breaking
    )
    assert len(rule_change.impact) == total_impact(comparison) == 165
    assert not comparison.added and not comparison.removed and not comparison.renumbered
    runner = CliRunner()
    result = runner.invoke(main, ["diff", *outputs, "--json", "--impact"])
    assert result.exit_code == 0, result.output
    report = json.loads(result.output)
    saved = json.loads((DEMO / "diff.json").read_text(encoding="utf-8"))
    for key in ("summary", "modified_nodes", "breaking_changes"):
        assert report[key] == saved[key]
    result = runner.invoke(main, ["diff", *outputs, "--fail-on-breaking"])
    assert result.exit_code == 1
    assert "String (i=12)" in result.output
    assert "Optional" in result.output and "Mandatory" in result.output
