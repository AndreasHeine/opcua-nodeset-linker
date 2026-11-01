"""Explainability and report tests."""

from __future__ import annotations

import json

import pytest
from tests.conftest import DATA, Machinery_NS

from nodeset_linker.explain.explainer import Explainer
from nodeset_linker.graph.engine import SemanticGraph
from nodeset_linker.pipeline import LinkerPipeline
from nodeset_linker.reports.data import (
    build_dependency_report,
    build_namespace_report,
    compute_statistics,
)
from nodeset_linker.repository.model import load_nodesets
from nodeset_linker.resolver.closure import DependencyResolver
from nodeset_linker.utils.errors import ResolutionError


def closure_for_machinery():  # type: ignore[no-untyped-def]
    model = load_nodesets([DATA / "ua_base.xml", DATA / "machinery.xml"])
    graph = SemanticGraph.from_model(model)
    roots = [n.node_id for n in model.find_by_browse_name("MSSControllerType")]
    return model, DependencyResolver(graph).resolve(roots, dict.fromkeys(roots, "MSSControllerType"))


class TestExplain:
    def test_explains_datatype_chain(self) -> None:
        model, closure = closure_for_machinery()
        explanation = Explainer(model, closure).explain("JobStateEnum")
        assert explanation.retained
        assert "MSSControllerType" in explanation.reason
        assert "-> JobDataType -> JobStateEnum" in explanation.hops[0]

    def test_explains_removed_node(self) -> None:
        model, closure = closure_for_machinery()
        explanation = Explainer(model, closure).explain("UnrelatedType")
        assert not explanation.retained
        assert "removed" in explanation.render()

    def test_explain_by_nodeid(self) -> None:
        model, closure = closure_for_machinery()
        explanation = Explainer(model, closure).explain(f"nsu={Machinery_NS};i=2002")
        assert explanation.node_label == "JobStateEnum"

    def test_unknown_node_raises(self) -> None:
        model, closure = closure_for_machinery()
        with pytest.raises(ResolutionError):
            Explainer(model, closure).explain("NoSuchNode")

    def test_ambiguous_browse_name_disambiguates_by_retention(self) -> None:
        model, closure = closure_for_machinery()
        assert Explainer(model, closure).explain("MachineType").retained

    def test_explain_all_lists_every_retained_node(self) -> None:
        model, closure = closure_for_machinery()
        explanations = Explainer(model, closure).explain_all()
        assert len(explanations) == len(closure.ids())
        assert all(e.retained for e in explanations)


class TestReports:
    def test_statistics_shape(self) -> None:
        model, closure = closure_for_machinery()
        statistics = compute_statistics(model, closure)
        payload = json.loads(statistics.as_json())
        assert set(payload) == {"original_nodes", "retained_nodes", "removed_nodes", "reduction_percent"}
        assert payload["original_nodes"] == model.node_count()
        assert payload["retained_nodes"] == len(closure.ids())
        assert payload["removed_nodes"] == payload["original_nodes"] - payload["retained_nodes"]

    def test_dependency_report_contents(self) -> None:
        model, closure = closure_for_machinery()
        report = build_dependency_report(model, closure)
        payload = json.loads(report.as_json())
        assert payload["roots"] == ["MSSControllerType"]
        assert "JobStateEnum" in payload["retention_reasons"]
        assert payload["dependency_categories"]["datatype"] > 0
        assert payload["optional_nodes"]  # fixture declares Optional nodes inside the closure

    def test_namespace_report_counts_and_cross_namespace(self) -> None:
        model, closure = closure_for_machinery()
        report = build_namespace_report(model, closure)
        payload = json.loads(report.as_json())
        assert payload["nodes_per_namespace"]["Machinery"] > 0
        assert payload["cross_namespace_dependencies"][0]["from"] == "Machinery"

    def test_pipeline_reports(self) -> None:
        from nodeset_linker.config.schema import LinkerConfig

        result = LinkerPipeline(
            LinkerConfig(
                nodesets=[str(DATA / "ua_base.xml"), str(DATA / "machinery.xml")],
                roots=["browseName=MSSControllerType"],
            )
        ).analyze()
        assert result.validation is not None and result.validation.ok
        assert result.dependency_report is not None
        assert result.namespace_report is not None
