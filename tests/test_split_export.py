"""Split export preserves ownership and the complete merged export graph."""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

import pytest
from click.testing import CliRunner
from lxml.etree import XMLSchema, fromstring, parse, tostring
from tests.conftest import DATA, NODESET_SCHEMA, UA_NS, Machinery_NS, node_id
from tests.test_external_dependencies import (
    ALPHA_NS,
    DI_FIXTURE,
    DI_NS,
    alpha_fixture,
    model_entries,
    write,
)

from nodeset_linker.cli.main import console, main
from nodeset_linker.config.schema import ExportConfig, LinkerConfig, load_config
from nodeset_linker.exporter import NodeSetExporter, export, export_split, split_output_files
from nodeset_linker.models.identifiers import QualifiedName
from nodeset_linker.models.node import UANode, referenced_identifiers
from nodeset_linker.pipeline import LinkerPipeline
from nodeset_linker.repository.model import SemanticModel, load_nodesets
from nodeset_linker.utils.errors import ExportError
from nodeset_linker.validator.semantic import SemanticValidator


def configuration(**overrides) -> LinkerConfig:  # type: ignore[no-untyped-def]
    return LinkerConfig.model_validate({
        "nodesets": [str(DATA / "ua_base.xml"), str(DATA / "machinery.xml")],
        "roots": ["browseName=MSSControllerType"],
        **overrides,
    })


def assert_equivalent(merged: SemanticModel, split: SemanticModel) -> None:
    def normalized(node: UANode) -> UANode:
        indexed = {"NodeId", "BrowseName", "DataType", "ParentNodeId", "MethodDeclarationId"}
        value = node.value_xml
        if value is not None:
            element = fromstring(value)
            dependencies = dict(node.value_dependencies)
            for child in element.iter():
                identifier = dependencies.get((child.text or "").strip())
                if identifier is not None:
                    child.text = identifier.as_xml()
            value = tostring(element, method="c14n").decode("utf-8")
        return replace(
            node,
            browse_name=replace(node.browse_name, namespace_index=None),
            attributes={key: value for key, value in node.attributes.items() if key not in indexed},
            value_xml=value,
            value_dependencies=tuple(sorted(
                {(identifier.as_xml(), identifier) for _, identifier in node.value_dependencies},
                key=lambda entry: entry[0],
            )),
        )

    assert merged.nodes.keys() == split.nodes.keys()
    for identifier, original in merged.nodes.items():
        actual = split.nodes[identifier]
        assert set(referenced_identifiers(original)) == set(referenced_identifiers(actual))
        assert normalized(original) == normalized(actual), str(identifier)


@pytest.mark.parametrize("mode", ["STRICT", "OPTIONAL_AWARE", "RUNTIME", "CUSTOM"])
@pytest.mark.parametrize("external", [[], [UA_NS]])
def test_pipeline_partition_equivalence(tmp_path, mode, external) -> None:
    options = {
        "mode": mode,
        "external": external,
        "roots": ["browseName=JobDataType"] if external else ["browseName=MSSControllerType"],
        "custom": {"retain": ["browseName=UnrelatedType"]} if mode == "CUSTOM" else None,
    }
    merged = LinkerPipeline(configuration(
        **options, export={"mode": "merged", "file": str(tmp_path / "merged.xml")}
    )).run()
    directory = tmp_path / "split"
    result = LinkerPipeline(configuration(
        **options, export={"mode": "split", "directory": str(directory)}
    )).run()
    assert result.retained == merged.retained
    assert result.validation.ok
    assert result.output_path == directory
    assert result.manifest_path == directory / "manifest.json"
    expected_uris = {identifier.namespace for identifier in result.retained}
    assert set(result.output_files) == expected_uris
    if external:
        assert {path.name for path in directory.iterdir()} == {"Machinery.xml", "manifest.json"}

    identifiers = []
    xsd = XMLSchema(parse(str(NODESET_SCHEMA)))
    for uri, path in result.output_files.items():
        assert xsd.validate(parse(str(path))), str(xsd.error_log)
        document = load_nodesets([path])
        assert {identifier.namespace for identifier in document.nodes} == {uri}
        assert set(model_entries(path)) == {uri}
        identifiers.extend(document.nodes)
    assert len(identifiers) == len(set(identifiers)) == len(result.retained)
    assert set(identifiers) == result.retained
    reloaded = load_nodesets(list(result.output_files.values()))
    assert_equivalent(load_nodesets([merged.output_path]), reloaded)
    report = SemanticValidator(reloaded, source=result.model, external=frozenset(external)).validate()
    assert report.ok, report.errors
    providers = [DATA / "ua_base.xml"] if external else []
    assert load_nodesets([*result.output_files.values(), *providers]).unresolved_targets() == []

    manifest = json.loads(result.manifest_path.read_text(encoding="utf-8"))
    assert manifest["dependencies"] == result.namespace_report.namespace_dependencies
    assert {entry["uri"]: entry["output_file"] for entry in manifest["namespaces"]} == (
        result.namespace_report.output_files
    )
    assert manifest["external_models"] == external


def test_cross_file_datatypes_custom_reference_types_and_headers(tmp_path) -> None:
    di = write(tmp_path, "di.xml", DI_FIXTURE)
    alpha = write(tmp_path, "alpha.xml", alpha_fixture())
    model = load_nodesets([di, alpha])
    keep = set(model.nodes)
    files = export_split(model, keep, tmp_path / "split", external=frozenset({UA_NS}))
    combined = load_nodesets(list(files.values()))
    merged = export(model, keep, tmp_path / "merged.xml", external=frozenset({UA_NS}))
    assert_equivalent(load_nodesets([merged]), combined)
    requirements = model_entries(files[ALPHA_NS])[ALPHA_NS]
    assert {entry["ModelUri"] for entry in requirements} == {DI_NS, UA_NS}
    assert next(entry for entry in requirements if entry["ModelUri"] == DI_NS)["Version"] == "1.0.2"
    assert model.namespace_dependencies(keep)[ALPHA_NS] == [UA_NS, DI_NS]
    missing_sibling = load_nodesets([files[ALPHA_NS]])
    assert not SemanticValidator(missing_sibling, external=frozenset({UA_NS})).validate().ok


def test_per_document_reindexing_includes_foreign_browse_names_and_servers(tmp_path) -> None:
    model = load_nodesets([DATA / "ua_base.xml", DATA / "machinery.xml"])
    owner = node_id(999000, Machinery_NS)
    original = model.find_by_browse_name("MSSControllerType")[0]
    server_id = replace(owner, server_uri="urn:server:example")
    model.nodes[server_id] = replace(
        original, node_id=server_id,
        browse_name=QualifiedName("ForeignName", namespace_uri="urn:foreign:BrowseNames"),
    )
    merged = export(model, set(model.nodes), tmp_path / "merged.xml")
    files = export_split(model, set(model.nodes), tmp_path / "split")
    reloaded = load_nodesets(list(files.values()))
    assert_equivalent(load_nodesets([merged]), reloaded)
    assert reloaded.nodes[server_id].browse_name.namespace_uri == "urn:foreign:BrowseNames"
    assert not any("BrowseNames" in filename.name for filename in files.values())


def test_subset_markers_match_merged_export(tmp_path) -> None:
    model = load_nodesets([DATA / "ua_base.xml", DATA / "machinery.xml"])
    keep = set(model.nodes) - {node_id(2500, Machinery_NS)}
    for mark in (True, False):
        merged = export(model, keep, tmp_path / f"merged-{mark}.xml", mark_namespace_subsets=mark)
        files = export_split(model, keep, tmp_path / f"split-{mark}", mark_namespace_subsets=mark)
        assert_equivalent(load_nodesets([merged]), load_nodesets(list(files.values())))


def test_namespace_filenames_are_safe_collision_free_and_deterministic() -> None:
    uris = [
        "https://one.example/Thing/", "https://two.example/thing/",
        "https://example/CON/", "https://example/../",
        "https://example/a:b?c/", "urn:custom:model",
    ]
    model = SemanticModel()
    # Filename planning only needs valid identifiers present in the registry.
    template = next(load_nodesets([DATA / "ua_base.xml"]).iter_nodes())
    for index, uri in enumerate(uris):
        identifier = node_id(index, uri)
        model.nodes[identifier] = replace(template, node_id=identifier)
    files = split_output_files(model, set(model.nodes))
    assert len({name.casefold() for name in files.values()}) == len(uris)
    assert files["https://example/CON/"] == "_CON.xml"
    assert all(Path(name).name == name and "/" not in name and "\\" not in name for name in files.values())
    model.nodes = dict(reversed(list(model.nodes.items())))
    assert split_output_files(model, set(model.nodes)) == files


def test_repeat_exports_are_byte_identical_and_empty_directory_is_supported(tmp_path) -> None:
    model = load_nodesets([DATA / "ua_base.xml", DATA / "machinery.xml"])
    first = tmp_path / "first"
    second = tmp_path / "second"
    second.mkdir()
    export_split(model, set(model.nodes), first)
    model.nodes = dict(reversed(list(model.nodes.items())))
    export_split(model, set(model.nodes), second)
    assert {p.name: p.read_bytes() for p in first.iterdir()} == {
        p.name: p.read_bytes() for p in second.iterdir()
    }


def test_refuses_nonempty_directory_without_modifying_it(tmp_path) -> None:
    model = load_nodesets([DATA / "ua_base.xml", DATA / "machinery.xml"])
    directory = tmp_path / "split"
    directory.mkdir()
    sentinel = directory / "existing.xml"
    sentinel.write_bytes(b"untouched")
    with pytest.raises(ExportError, match="absent or empty"):
        export_split(model, set(model.nodes), directory)
    assert list(directory.iterdir()) == [sentinel]
    assert sentinel.read_bytes() == b"untouched"


def test_validation_and_serialization_failures_do_not_publish_partial_bundle(tmp_path) -> None:
    model = load_nodesets([DATA / "ua_base.xml", DATA / "machinery.xml"])
    directory = tmp_path / "split"
    with pytest.raises(ExportError, match="export blocked"):
        export_split(model, {model.find_by_browse_name("JobDataType")[0].node_id}, directory)
    assert not directory.exists()
    with (
        patch.object(
            NodeSetExporter, "build_document",
            side_effect=[b"<UANodeSet/>", ExportError("build failed")],
        ),
        pytest.raises(ExportError, match="build failed"),
    ):
        export_split(model, set(model.nodes), directory)
    assert not directory.exists()
    assert not list(tmp_path.glob(".nodeset-linker-*"))
    with pytest.raises(ExportError, match="retained node not found"):
        export_split(model, {node_id(999999, Machinery_NS)}, directory)


def test_analyze_plans_files_without_writing(tmp_path) -> None:
    directory = tmp_path / "split"
    result = LinkerPipeline(configuration(
        export={"mode": "split", "directory": str(directory)}
    )).analyze()
    assert not directory.exists()
    assert result.output_path is None
    assert result.output_files == {}
    assert result.namespace_report.output_files


@pytest.mark.parametrize("console_width", [40, 80])
def test_split_cli_output_override_and_errors(tmp_path, monkeypatch, console_width) -> None:
    monkeypatch.setattr(console, "width", console_width)
    config = configuration(export={"mode": "split", "directory": str(tmp_path / "unused")})
    path = tmp_path / "config.json"
    path.write_text(config.model_dump_json(), encoding="utf-8")
    directory = tmp_path / "actual"
    runner = CliRunner()
    result = runner.invoke(main, ["link", str(path), "--output", str(directory)])
    assert result.exit_code == 0, result.output
    assert (directory / "manifest.json").exists()
    assert "manifest.json" in result.output
    assert f"wrote {directory / 'manifest.json'}" in result.output.splitlines()
    assert f"wrote {directory / 'Machinery.xml'}" in result.output.splitlines()
    result = runner.invoke(main, ["link", str(path), "--output", str(directory)])
    assert result.exit_code == 1
    assert "absent or empty" in " ".join(result.output.split())
    assert not (tmp_path / "unused").exists()


def test_export_config_defaults_and_legacy_compatibility() -> None:
    assert ExportConfig(mode="split").directory == "out"
    assert ExportConfig().file == "out/reduced.xml"
    assert configuration(output="legacy.xml").output == "legacy.xml"
    assert configuration().export is None


def test_new_merged_config_matches_legacy_output(tmp_path) -> None:
    legacy = LinkerPipeline(configuration(output=str(tmp_path / "legacy.xml"))).run()
    merged = LinkerPipeline(configuration(
        export={"mode": "merged", "file": str(tmp_path / "merged.xml")}
    )).run()
    assert legacy.output_path.read_bytes() == merged.output_path.read_bytes()
    assert merged.output_files == {}
    assert merged.manifest_path is None


@pytest.mark.parametrize("options", [
    {"export": {"mode": "invalid"}},
    {"export": {"mode": "split", "file": "bad.xml"}},
    {"export": {"mode": "merged", "directory": "bad"}},
    {"export": {"mode": "split", "directory": ""}},
    {"export": {"mode": "split", "unknown": True}},
    {"export": {"mode": "split"}, "output": "legacy.xml"},
])
def test_export_config_rejects_ambiguous_or_invalid_options(options) -> None:
    with pytest.raises(ValueError):
        configuration(**options)


def test_official_demo_split_matches_merged_graph(tmp_path) -> None:
    root = Path(__file__).parent.parent
    config = load_config(root / "examples" / "demo2" / "config.yaml")
    demo1 = load_config(root / "examples" / "demo1" / "config.yaml")
    assert config.nodesets == demo1.nodesets
    assert config.roots == demo1.roots
    assert config.mode == demo1.mode
    assert config.external == demo1.external
    assert config.export.mode == "split"
    assert config.export.directory == "examples/demo2/reduced"
    # Resolve source paths independently of the test runner's working directory.
    config = config.model_copy(update={
        "nodesets": [str(root / name) for name in config.nodesets],
        "output": None,
        "export": ExportConfig(mode="split", directory=str(tmp_path / "split")),
    })
    pipeline = LinkerPipeline(config)
    result = pipeline.run()
    assert len(result.output_files) == 7
    assert {path.name for path in result.output_files.values()} == {
        "DI.xml", "IA.xml", "ISA95-JOBCONTROL_V2.xml", "Jobs.xml",
        "Machinery.xml", "MachineTool.xml", "ShowCaseMachineTool.xml",
    }
    merged = LinkerPipeline(config.model_copy(update={
        "export": ExportConfig(mode="merged", file=str(tmp_path / "merged.xml")),
    })).run()
    reloaded = load_nodesets(list(result.output_files.values()))
    assert_equivalent(load_nodesets([merged.output_path]), reloaded)
    assert set(reloaded.nodes) == result.retained == merged.retained
    assert result.namespace_report.namespace_dependencies == json.loads(
        result.manifest_path.read_text(encoding="utf-8")
    )["dependencies"]
    xsd = XMLSchema(parse(str(NODESET_SCHEMA)))
    for path in result.output_files.values():
        assert xsd.validate(parse(str(path))), str(xsd.error_log)
