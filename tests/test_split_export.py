"""Split export tests: the same reduction, written as one NodeSet per owning namespace.

Nothing here re-checks the reduction itself — that is ``tests/test_export.py`` and the pipeline
tests.  These tests hold the *export strategy* to its promises: every retained node lives in
exactly one file, a reference that crosses a namespace still crosses it, and the files loaded
together reload as the same semantic graph.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from lxml.etree import parse as xml_parse
from tests.conftest import DATA, UA_NS, Machinery_NS, node_id

from nodeset_linker.config.schema import load_config
from nodeset_linker.exporter.nodeset import (
    MANIFEST_FILE_NAME,
    NODESET_XML_NAMESPACE,
    export,
    export_split,
    split_file_names,
)
from nodeset_linker.models.identifiers import ExpandedNodeId
from nodeset_linker.pipeline import LinkerPipeline
from nodeset_linker.repository.model import load_nodesets
from nodeset_linker.utils.errors import ExportError

JOBCONTROL = DATA / "jobcontrol.xml"
Job_NS = "http://example.org/UA/JobControl/"

MODEL = load_nodesets([DATA / "ua_base.xml", DATA / "machinery.xml", JOBCONTROL])
ALL = {node.node_id for node in MODEL.iter_nodes()}
REDUCED = ALL - {node_id(2500, Machinery_NS)}
CORE = frozenset({UA_NS})


def root_of(path: Path) -> Any:
    return xml_parse(str(path)).getroot()


def uri_table(path: Path) -> list[str]:
    """The ``NamespaceUris`` entries of a file, in index order (index 0 is always UA)."""
    table = root_of(path).find(f"{{{NODESET_XML_NAMESPACE}}}NamespaceUris")
    return [] if table is None else [str(element.text) for element in table]


def declared_ids(path: Path) -> list[str]:
    """The ``NodeId`` attributes this file declares — its own nodes, nothing it references."""
    return [
        str(element.get("NodeId"))
        for element in root_of(path)
        if element.tag.startswith(f"{{{NODESET_XML_NAMESPACE}}}UA")
    ]


def shipped_nodes(
    keep: set[ExpandedNodeId], *, external: frozenset[str] = CORE
) -> set[ExpandedNodeId]:
    """The nodes a split export writes: a node of an externalized model is only referenced."""
    return {node_id for node_id in keep if node_id.namespace not in external}


def manifest_file_entries(manifest: dict[str, object]) -> dict[str, dict[str, Any]]:
    entries = manifest["files"]
    assert isinstance(entries, dict)
    return {str(stem): dict(entry) for stem, entry in entries.items()}  # type: ignore[arg-type]


def split_config(tmp_path: Path, *, mode: str, target: Path, roots: str) -> Path:
    """A configuration in a temp folder; the nodesets are the repo fixtures, UA is externalized.

    The OPC UA core is externalized because that is the usual release case: a reduced
    NodeSet ships its own namespaces and points at the core the server already provides.
    """
    config = tmp_path / "config.yaml"
    config.write_text(
        "\n".join(
            [
                "nodesets:",
                f"  - {DATA / 'ua_base.xml'}",
                f"  - {DATA / 'machinery.xml'}",
                f"  - {JOBCONTROL}",
                "mode: STRICT",
                f"roots: [{roots}]",
                "external: [http://opcfoundation.org/UA/]",
                "export:",
                f"  mode: {mode}",
                f"  {'directory' if mode == 'split' else 'file'}: {target}",
            ]
        ),
        encoding="utf-8",
    )
    return config


class TestSplitExportGeneration:
    def test_split_writes_one_node_set_per_namespace(self, tmp_path: Path) -> None:
        model = load_nodesets([DATA / "ua_base.xml", DATA / "machinery.xml"])
        result = export_split(
            model, {node.node_id for node in model.iter_nodes()}, tmp_path, external=CORE
        )
        assert set(result.files) == {Machinery_NS}
        assert result.files[Machinery_NS].name == "Machinery.xml"
        assert result.manifest_path == tmp_path / MANIFEST_FILE_NAME
        assert (tmp_path / "Machinery.xml").exists()
        # An externalized core is referenced by the file, never shipped as a file of its own.
        assert not (tmp_path / "UA.xml").exists()

    def test_split_ships_one_file_per_namespace_of_the_reduction(self, tmp_path: Path) -> None:
        result = export_split(MODEL, REDUCED, tmp_path, external=CORE)
        assert sorted(path.name for path in result.files.values()) == ["JobControl.xml", "Machinery.xml"]

    def test_split_keeps_the_merged_reduction_unchanged(self, tmp_path: Path) -> None:
        """Same retained graph, different strategy: the split files reload as the merged document."""
        model = load_nodesets([DATA / "ua_base.xml", DATA / "machinery.xml"])
        keep = {node.node_id for node in model.iter_nodes()}
        merged = load_nodesets([export(model, keep, tmp_path / "merged.xml")])
        split = load_nodesets(sorted(export_split(model, keep, tmp_path / "split").files.values()))
        assert merged.node_count() == split.node_count() == len(keep)
        assert {node.node_id for node in merged.iter_nodes()} == {node.node_id for node in split.iter_nodes()}

    def test_split_files_are_plain_schema_shaped_documents(self, tmp_path: Path) -> None:
        result = export_split(MODEL, REDUCED, tmp_path)
        for path in result.files.values():
            assert root_of(path).tag == f"{{{NODESET_XML_NAMESPACE}}}UANodeSet"
            text = path.read_text(encoding="utf-8")
            assert "UriIndex" not in text
            assert "DefinitionType" not in text
            assert "IsNamespaceSubset=" not in text

    def test_split_deterministic(self, tmp_path: Path) -> None:
        first = export_split(MODEL, REDUCED, tmp_path / "a")
        second = export_split(MODEL, REDUCED, tmp_path / "b")
        for uri, path in first.files.items():
            assert path.read_bytes() == second.files[uri].read_bytes()
        assert first.manifest == second.manifest


class TestNodeOwnership:
    def test_every_retained_node_is_written_exactly_once(self, tmp_path: Path) -> None:
        result = export_split(MODEL, REDUCED, tmp_path)
        shipped = shipped_nodes(REDUCED, external=frozenset())
        written = [id_ for path in result.files.values() for id_ in declared_ids(path)]
        assert len(written) == len(shipped)
        assert len(set(written)) == len(written)

    def test_a_node_is_never_written_into_a_foreign_file(self, tmp_path: Path) -> None:
        """Ownership is the namespace of the identifier: each file declares nodes of its own URI."""
        result = export_split(MODEL, REDUCED, tmp_path, external=CORE)
        for uri, path in result.files.items():
            uris = [UA_NS, *uri_table(path)]
            for id_ in declared_ids(path):
                index = int(id_.split("ns=")[1].split(";")[0]) if id_.startswith("ns=") else 0
                assert uris[index] == uri, f"{id_} was written into {path.name}"

    def test_no_node_is_moved_to_another_namespace(self, tmp_path: Path) -> None:
        result = export_split(MODEL, REDUCED, tmp_path)
        reloaded = load_nodesets(sorted(result.files.values()))
        assert {node.node_id.namespace for node in reloaded.iter_nodes()} == {
            node_id.namespace for node_id in REDUCED
        }

    def test_a_namespace_is_never_merged_into_its_neighbour(self, tmp_path: Path) -> None:
        result = export_split(MODEL, REDUCED, tmp_path, external=CORE)
        assert len(result.files) == len({nid.namespace for nid in shipped_nodes(REDUCED)})

    def test_duplicate_ownership_is_refused(self) -> None:
        """Two files claiming the same nodes is an ownership change, so it is refused."""
        from nodeset_linker.exporter.nodeset import _assert_ownership

        with pytest.raises(ExportError, match="ownership"):
            _assert_ownership(
                {Machinery_NS: REDUCED, Job_NS: REDUCED},
                shipped_nodes(REDUCED),
            )

    def test_split_refuses_a_broken_subset(self, tmp_path: Path) -> None:
        """The same guard a merged export applies: a subset that breaks inheritance is never written."""
        keep = {node.node_id for node in MODEL.iter_nodes() if node.browse_name.name == "UnrelatedMachineType"}
        with pytest.raises(ExportError, match="export blocked"):
            export_split(MODEL, keep - {node_id(1000)}, tmp_path)


class TestCrossNamespaceReferences:
    def test_foreign_datatype_is_referenced_not_copied(self, tmp_path: Path) -> None:
        result = export_split(MODEL, REDUCED, tmp_path, external=CORE)
        job = result.files[Job_NS]
        assert 'DataType="ns=2;i=2001"' in job.read_text(encoding="utf-8")
        # The Machinery datatype stays in its own file: referenced here, declared there.
        assert "ns=2;i=2001" not in declared_ids(job)
        assert "ns=1;i=2001" in declared_ids(result.files[Machinery_NS])
        assert Machinery_NS in uri_table(job)

    def test_a_file_ships_only_its_own_model_entry(self, tmp_path: Path) -> None:
        result = export_split(MODEL, REDUCED, tmp_path, external=CORE)
        text = result.files[Job_NS].read_text(encoding="utf-8")
        assert '<Model ModelUri="http://example.org/UA/JobControl/"' in text
        assert '<Model ModelUri="http://opcfoundation.org/UA/Machinery/"' not in text
        assert '<RequiredModel ModelUri="http://opcfoundation.org/UA/Machinery/"' in text

    def test_references_resolve_when_the_files_load_together(self, tmp_path: Path) -> None:
        result = export_split(MODEL, REDUCED, tmp_path)
        reloaded = load_nodesets(sorted(result.files.values()))
        assert reloaded.unresolved_targets() == []
        assert reloaded.node_count() == len(REDUCED)

    def test_a_single_split_file_is_not_self_contained(self, tmp_path: Path) -> None:
        """The guarantee is combined reloadability — a lone file may point at a sibling file."""
        result = export_split(MODEL, REDUCED, tmp_path, external=CORE)
        lone = load_nodesets([result.files[Job_NS]])
        assert any(target.namespace == Machinery_NS for _source, target in lone.unresolved_targets())


class TestManifestGeneration:
    def test_manifest_describes_the_export(self, tmp_path: Path) -> None:
        result = export_split(MODEL, REDUCED, tmp_path, external=CORE)
        manifest = result.manifest
        assert manifest["export_mode"] == "split"
        assert manifest["namespaces"] == ["JobControl", "Machinery"]
        assert manifest["namespace_uris"] == {"JobControl": Job_NS, "Machinery": Machinery_NS}
        assert manifest["dependencies"] == {"JobControl": ["Machinery"]}
        assert manifest["external_models"] == [UA_NS]

    def test_manifest_dependencies_are_the_namespace_dependency_report(self, tmp_path: Path) -> None:
        config = split_config(
            tmp_path,
            mode="split",
            target=tmp_path / "out",
            roots="namespaceUri=http://example.org/UA/JobControl/",
        )
        result = LinkerPipeline(load_config(config)).run()
        report = result.namespace_report
        assert report is not None
        assert {(source, target) for source, target in report.cross_namespace_edges} == {
            ("JobControl", "Machinery")
        }
        assert result.manifest["dependencies"] == {"JobControl": ["Machinery"]}
        assert result.export_mode == "split"
        assert result.manifest_path == tmp_path / "out" / MANIFEST_FILE_NAME
        assert report.output_files == {"JobControl": "JobControl.xml", "Machinery": "Machinery.xml"}

    def test_namespace_report_names_the_file_each_namespace_was_written_to(self, tmp_path: Path) -> None:
        config = split_config(
            tmp_path,
            mode="split",
            target=tmp_path / "out",
            roots="namespaceUri=http://opcfoundation.org/UA/Machinery/",
        )
        result = LinkerPipeline(load_config(config)).run()
        report = result.namespace_report
        assert report is not None
        payload = json.loads(report.as_json())
        assert payload["output_file"] == {
            "JobControl": "JobControl.xml",
            "Machinery": "Machinery.xml",
        }
        assert {path.name for path in result.output_files} == {"JobControl.xml", "Machinery.xml"}
        assert payload["external_models"] == [UA_NS]
        assert result.manifest_path == tmp_path / "out" / "manifest.json"

    def test_analyze_reports_the_files_a_split_export_would_write(self, tmp_path: Path) -> None:
        config = split_config(
            tmp_path,
            mode="split",
            target=tmp_path / "out",
            roots="namespaceUri=http://example.org/UA/JobControl/",
        )
        result = LinkerPipeline(load_config(config)).analyze()
        report = result.namespace_report
        assert report is not None
        assert report.output_files == {"JobControl": "JobControl.xml", "Machinery": "Machinery.xml"}
        assert not (tmp_path / "out").exists()

    def test_manifest_node_counts_are_the_file_contents(self, tmp_path: Path) -> None:
        result = export_split(MODEL, REDUCED, tmp_path, external=CORE)
        entries = manifest_file_entries(result.manifest)
        shipped = shipped_nodes(REDUCED)
        assert sum(int(entry["nodes"]) for entry in entries.values()) == len(shipped)
        assert {stem: entry["path"] for stem, entry in entries.items()} == {
            "JobControl": "JobControl.xml",
            "Machinery": "Machinery.xml",
        }

    def test_manifest_orders_namespaces_by_file_name(self, tmp_path: Path) -> None:
        namespaces = export_split(MODEL, ALL, tmp_path).manifest["namespaces"]
        assert isinstance(namespaces, list)
        assert namespaces == sorted(namespaces, key=str.lower)

    def test_file_names_are_unique(self) -> None:
        names = split_file_names(MODEL, [Job_NS, Machinery_NS, "http://example.org/UA/Machinery/"])
        assert len(set(names.values())) == len(names)
        assert set(names.values()) == {"JobControl.xml", "Machinery.xml", "Machinery-2.xml"}


class TestSplitReloadability:
    def test_reload_check_blocks_an_export_that_changes_the_node_count(self, tmp_path: Path) -> None:
        """A file that lost its nodes would not reload as the same graph, and that is refused."""
        from nodeset_linker.exporter.nodeset import _reload_split_export

        result = export_split(MODEL, REDUCED, tmp_path)
        written = sorted(result.files.values())
        written[0].write_text(
            '<UANodeSet xmlns="http://opcfoundation.org/UA/2011/03/UANodeSet.xsd"/>', encoding="utf-8"
        )
        with pytest.raises(ExportError, match="ownership"):
            _reload_split_export(written, len(REDUCED), frozenset())

    def test_reload_check_blocks_references_that_do_not_resolve(self, tmp_path: Path) -> None:
        """Without the sibling file, a reference into it is a target nothing declares."""
        from nodeset_linker.exporter.nodeset import _reload_split_export

        result = export_split(MODEL, REDUCED, tmp_path, external=CORE)
        machinery_nodes = len(declared_ids(result.files[Machinery_NS]))
        (tmp_path / "Machinery.xml").unlink()
        with pytest.raises(ExportError, match="do not resolve"):
            _reload_split_export(
                [tmp_path / "JobControl.xml"], len(shipped_nodes(REDUCED)) - machinery_nodes, CORE
            )

    def test_split_marks_a_reduced_namespace_as_subset(self, tmp_path: Path) -> None:
        result = export_split(MODEL, REDUCED, tmp_path, external=CORE)
        entries = manifest_file_entries(result.manifest)
        assert {stem: bool(entry["subset"]) for stem, entry in entries.items()} == {
            "JobControl": False,
            "Machinery": True,
        }

    def test_manifest_is_json(self, tmp_path: Path) -> None:
        result = export_split(MODEL, REDUCED, tmp_path)
        assert json.loads(result.manifest_path.read_text(encoding="utf-8")) == result.manifest
