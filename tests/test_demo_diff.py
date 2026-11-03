"""The two showcase configurations demonstrate a single breaking datatype change."""

from __future__ import annotations

import json
from pathlib import Path

from click.testing import CliRunner
from lxml.etree import XMLSchema, parse
from tests.conftest import NODESET_SCHEMA, UA_NS

from nodeset_linker.cli.main import main
from nodeset_linker.config.schema import ExportConfig, load_config
from nodeset_linker.diff import apply_impact, diff_models, total_impact
from nodeset_linker.models.identifiers import ExpandedNodeId
from nodeset_linker.pipeline import LinkerPipeline
from nodeset_linker.repository.model import load_nodesets
from nodeset_linker.validator.semantic import SemanticValidator

ROOT = Path(__file__).parent.parent
DEMO = ROOT / "examples" / "demo3"


def test_showcase_diff_demo(tmp_path) -> None:
    configs = [load_config(DEMO / f"config_{name}.yaml") for name in ("original", "modified")]
    original_config, modified_config = configs
    assert original_config.model_dump(exclude={"nodesets", "export"}) == (
        modified_config.model_dump(exclude={"nodesets", "export"})
    )
    different_inputs = [
        (old, new)
        for old, new in zip(original_config.nodesets, modified_config.nodesets, strict=True)
        if old != new
    ]
    assert different_inputs == [(
        "examples/nodesets/ShowCaseMachineTool.xml",
        "examples/demo2/reduced/ShowCaseMachineTool-Modified.xml",
    )]

    schema = XMLSchema(parse(str(NODESET_SCHEMA)))
    models = []
    outputs = []
    for config in configs:
        assert config.export is not None and config.export.mode == "merged"
        output = tmp_path / Path(config.export.file).name
        pipeline = LinkerPipeline(config.model_copy(update={
            "nodesets": [str(ROOT / path) for path in config.nodesets],
            "export": ExportConfig(file=str(output)),
        }))
        result = pipeline.run()
        assert result.validation.ok
        assert len(result.retained) == 1110
        assert schema.validate(parse(str(output))), str(schema.error_log)
        model = load_nodesets([output])
        assert SemanticValidator(model, source=result.model, external=result.external).validate().ok
        assert set(model.nodes) == result.retained
        assert load_nodesets([
            output, ROOT / "examples" / "nodesets" / "Opc.Ua.NodeSet2.xml",
        ]).unresolved_targets() == []
        assert output.read_bytes() == (ROOT / config.export.file).read_bytes()
        models.append(model)
        outputs.append(str(output))

    original, modified = models
    comparison = diff_models(original, modified)
    apply_impact(comparison, original, modified)
    assert original.nodes.keys() == modified.nodes.keys()
    assert len(comparison.modified) == len(comparison.breaking) == 1
    assert not comparison.added and not comparison.removed and not comparison.renumbered
    change = comparison.modified[0]
    assert change.node_id == ExpandedNodeId.parse(
        "nsu=http://umati.org/UA/ShowCaseMachineTool/;i=6002"
    )
    assert change.label == "ProductInstanceUri"
    datatype = next(item for item in change.changes if item.kind == "data_type")
    assert (datatype.old, datatype.new, datatype.breaking) == (
        "String (i=12)", "Double (i=11)", True,
    )
    assert len(change.changes) == 1
    assert original.nodes[change.node_id].data_type.namespace == UA_NS
    assert len(change.impact) == total_impact(comparison) == 52
    assert {"Identification", "ShowCaseMachineTool"} <= set(change.impact)

    runner = CliRunner()
    result = runner.invoke(main, ["diff", *outputs, "--json", "--impact"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    committed = json.loads((DEMO / "diff.json").read_text(encoding="utf-8"))
    assert payload["summary"] == committed["summary"]
    assert payload["modified_nodes"] == committed["modified_nodes"]
    assert payload["breaking_changes"] == committed["breaking_changes"]
    result = runner.invoke(main, ["diff", *outputs, "--impact", "--fail-on-breaking"])
    assert result.exit_code == 1
    assert "ProductInstanceUri" in result.output
