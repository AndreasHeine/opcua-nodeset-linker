"""Application pipeline: load → graph → roots → closure → retention → validate → export.

This is the only place where the layers meet.  The CLI talks to this; everything below
it stays independent of XML and of the other layers.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from nodeset_linker.config.schema import LinkerConfig
from nodeset_linker.explain.explainer import Explainer
from nodeset_linker.exporter.nodeset import export
from nodeset_linker.exporter.split import export_split, split_output_files
from nodeset_linker.graph.engine import SemanticGraph
from nodeset_linker.models.identifiers import ExpandedNodeId
from nodeset_linker.policies.registry import build_policy
from nodeset_linker.reports.data import (
    DependencyReportData,
    NamespaceReportData,
    Statistics,
    build_dependency_report,
    build_namespace_report,
    compute_statistics,
)
from nodeset_linker.repository.model import SemanticModel, load_nodesets
from nodeset_linker.resolver.closure import Closure, DependencyResolver
from nodeset_linker.resolver.roots import RootResolver, RootSelector
from nodeset_linker.retention.engine import RetentionEngine, prune_navigation
from nodeset_linker.utils.errors import ResolutionError, ValidationError
from nodeset_linker.validator.semantic import SemanticValidator, ValidationReport


@dataclass(slots=True)
class LinkResult:
    """Everything one linking run produced."""

    model: SemanticModel
    graph: SemanticGraph
    closure: Closure
    retained: set[ExpandedNodeId] = field(default_factory=set)
    statistics: Statistics | None = None
    dependency_report: DependencyReportData | None = None
    namespace_report: NamespaceReportData | None = None
    validation: ValidationReport | None = None
    output_path: Path | None = None
    external: frozenset[str] = frozenset()
    """ModelUris whose declarations this run left out while keeping the references."""
    output_files: dict[str, Path] = field(default_factory=dict)
    manifest_path: Path | None = None

    def explainer(self) -> Explainer:
        return Explainer(self.model, self.closure)


class LinkerPipeline:
    """Runs the whole reduction for one configuration."""

    def __init__(self, config: LinkerConfig) -> None:
        self._config = config
        self._model: SemanticModel | None = None

    def load(self) -> SemanticModel:
        if self._model is None:
            self._model = load_nodesets(list(self._config.nodesets))
        return self._model

    def run(self, *, export_output: bool = True) -> LinkResult:
        model = self.load()
        graph = SemanticGraph.from_model(model)
        external = self._external_namespaces(model)
        external_ids = {
            node_id for node_id in model.nodes if node_id.namespace in external
        }

        selectors = [RootSelector.parse(text) for text in self._config.roots]
        roots = RootResolver(model).resolve(selectors)
        self._reject_external_roots(roots, external)
        labels = {root: _label(model, root) for root in roots}

        custom_rules = self._config.custom_rules()
        custom_roots = [
            node.node_id
            for node in model.iter_nodes()
            if node.node_id.namespace not in external
            and next((rule.include for rule in custom_rules if rule.matches(node)), False)
            and node.node_id not in roots
        ]
        labels.update(
            {node_id: f"custom retain: {_label(model, node_id)}" for node_id in custom_roots}
        )
        closure = DependencyResolver(
            graph, external=external, include_optional=self._config.mode != "RUNTIME"
        ).resolve([*roots, *custom_roots], labels)
        closure.roots = tuple(roots)

        engine = RetentionEngine(
            policy=build_policy(self._config.mode, custom_rules),
            custom_rules=custom_rules,
        )
        retained: set[ExpandedNodeId] = set()
        for node in model.iter_nodes():
            if engine.decide(
                node,
                retained_by_closure=node.node_id in closure.ids(),
                is_root=(
                    node.node_id in roots or node.node_id in custom_roots
                    or node.node_id in closure.navigation_only
                ),
            ):
                retained.add(node.node_id)
        # An externalized model is never shipped — not even when its nodes survived the
        # closure, because the dependency it represents is stated in the output header.
        retained -= external_ids

        export_model = prune_navigation(
            model, graph, retained, external, navigation_only=closure.navigation_only
        )
        engine.repair(
            SemanticGraph.from_model(export_model),
            retained,
            repairable=set(engine.dropped) - set(engine.excluded) - external_ids,
        )
        export_model = prune_navigation(
            model, graph, retained, external, navigation_only=closure.navigation_only
        )

        effective = Closure(
            retained={
                node_id: closure.retained[node_id]
                for node_id in retained
                if node_id in closure.ids()
            },
            roots=tuple(roots),
            navigation_only=closure.navigation_only & retained,
        )
        statistics = compute_statistics(model, effective, retained=retained, external=external)
        dependency_report = build_dependency_report(model, effective)
        namespace_report = build_namespace_report(export_model, effective, external=external)
        split = self._config.export is not None and self._config.export.mode == "split"
        if split:
            namespace_report.output_files = split_output_files(export_model, retained)

        validation = SemanticValidator(
            _subset_model(export_model, retained), source=model, external=external
        ).validate()
        if not validation.ok:
            # A retention choice that breaks the model must never be exported; report it
            # instead of producing a broken NodeSet.
            raise ValidationError(validation.summary(), validation)
        output_path: Path | None = None
        output_files: dict[str, Path] = {}
        manifest_path: Path | None = None
        destination = self._config.output
        if self._config.export is not None:
            destination = (
                self._config.export.directory if split else self._config.export.file
            )
        if export_output and destination and split:
            output_files = export_split(
                export_model,
                retained,
                destination,
                validate=self._config.validate_before_export,
                mark_namespace_subsets=self._config.mark_namespace_subsets,
                external=external,
            )
            output_path = Path(destination)
            manifest_path = output_path / "manifest.json"
        elif export_output and destination:
            output_path = export(
                export_model,
                retained,
                destination,
                validate=self._config.validate_before_export,
                mark_namespace_subsets=self._config.mark_namespace_subsets,
                external=external,
            )

        return LinkResult(
            model=model,
            graph=graph,
            closure=effective,
            retained=retained,
            statistics=statistics,
            dependency_report=dependency_report,
            namespace_report=namespace_report,
            validation=validation,
            output_path=output_path,
            external=external,
            output_files=output_files,
            manifest_path=manifest_path,
        )

    def analyze(self) -> LinkResult:
        """Report-only run: nothing is exported."""
        return self.run(export_output=False)

    def _external_namespaces(self, model: SemanticModel) -> frozenset[str]:
        """The configured ModelUris, resolved to the namespace URIs the model actually uses.

        A dependency is addressed by its stable URI — never by a document-local namespace
        index, which means nothing once the namespaces are re-numbered on export.  The
        spelling is matched against what the loaded documents declare, so a ModelUri without
        the conventional trailing slash still resolves; a URI nothing declares stays as
        written, because a dependency can be externalized without being loaded.
        """
        declared = {uri.rstrip("/"): uri for uri in [*model.namespaces, *model.models]}
        resolved: list[str] = []
        for uri in self._config.external:
            resolved.append(declared.get(uri.rstrip("/"), uri))
        return frozenset(resolved)

    @staticmethod
    def _reject_external_roots(roots: list[ExpandedNodeId], external: frozenset[str]) -> None:
        """A model the output does not ship cannot be a root of the reduction.

        Rooting inside an externalized model would ask for declarations that are never
        written; that is a configuration mistake, not a reduction.
        """
        for root in roots:
            if root.namespace in external:
                raise ResolutionError(
                    f"root {root} belongs to externalized model {root.namespace}, "
                    "whose declarations are not exported; a root cannot live in an "
                    "external dependency"
                )


def _subset_model(model: SemanticModel, keep: set[ExpandedNodeId]) -> SemanticModel:
    """Model restricted to the retained nodes — what validation checks."""
    subset = SemanticModel(
        namespaces=dict(model.namespaces),
        documents=list(model.documents),
        models=dict(model.models),
    )
    subset.nodes = {node_id: node for node_id, node in model.nodes.items() if node_id in keep}
    return subset


def _label(model: SemanticModel, node_id: ExpandedNodeId) -> str:
    node = model.get(node_id)
    return node.browse_name.name if node else str(node_id)
