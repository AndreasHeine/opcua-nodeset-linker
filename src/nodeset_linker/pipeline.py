"""Application pipeline: load → graph → roots → closure → retention → validate → export.

This is the only place where the layers meet.  The CLI talks to this; everything below
it stays independent of XML and of the other layers.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from nodeset_linker.config.schema import ExportMode, LinkerConfig
from nodeset_linker.explain.explainer import Explainer
from nodeset_linker.exporter.nodeset import export, export_split, split_file_names
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
    namespace_alias,
)
from nodeset_linker.repository.model import SemanticModel, load_nodesets
from nodeset_linker.resolver.closure import Closure, DependencyResolver
from nodeset_linker.resolver.roots import RootResolver, RootSelector
from nodeset_linker.retention.engine import RetentionEngine
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
    output_files: list[Path] = field(default_factory=list)
    """Every file this run wrote: one NodeSet for a merged export, one per namespace for split."""
    manifest_path: Path | None = None
    """The ``manifest.json`` of a split export; None for a merged one."""
    manifest: dict[str, object] | None = None
    """The parsed manifest: which file ships which namespace and which namespaces it needs."""
    export_mode: ExportMode = "merged"
    """How the retained nodes were written out — the reduction is the same in both modes."""
    external: frozenset[str] = frozenset()
    """ModelUris whose declarations this run left out while keeping the references."""

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

        closure = DependencyResolver(graph).resolve(roots, labels)

        engine = RetentionEngine(
            policy=build_policy(self._config.mode, self._config.custom_rules()),
            custom_rules=self._config.custom_rules(),
        )
        retained: set[ExpandedNodeId] = set()
        for node in model.iter_nodes():
            if engine.decide(
                node,
                retained_by_closure=node.node_id in closure.ids(),
                is_root=node.node_id in roots,
            ):
                retained.add(node.node_id)
        # An externalized model is never shipped — not even when its nodes survived the
        # closure, because the dependency it represents is stated in the output header.
        retained -= external_ids

        # Dropping a node must not leave a retained reference pointing at it.  Nodes the
        # policy dropped are repaired; nodes a user excluded on purpose stay gone, and an
        # exclusion that breaks the model is reported by validation below.  Nodes of an
        # externalized model are never repaired back in: they are referenced, not shipped.
        engine.repair(
            graph,
            retained,
            repairable=set(engine.dropped) - set(engine.excluded) - external_ids,
        )

        effective = Closure(
            retained={
                node_id: closure.retained[node_id]
                for node_id in retained
                if node_id in closure.ids()
            },
            roots=tuple(roots),
        )
        statistics = compute_statistics(model, effective, external=external)
        dependency_report = build_dependency_report(model, effective)
        namespace_report = build_namespace_report(model, effective, external=external)

        validation = SemanticValidator(
            _subset_model(model, retained), source=model, external=external
        ).validate()
        if not validation.ok:
            # A retention choice that breaks the model must never be exported; report it
            # instead of producing a broken NodeSet.
            raise ValidationError(validation.summary(), validation)
        output_path: Path | None = None
        output_files: list[Path] = []
        manifest_path: Path | None = None
        manifest: dict[str, object] | None = None
        plan = self._config.export_plan()
        export_mode: ExportMode = plan[0] if plan else "merged"
        if namespace_report is not None and plan is not None:
            # Even a report-only run can say where each namespace *would* be written.
            namespace_report.output_files = self._planned_output_files(
                plan, model, retained, namespace_report
            )
        if export_output and plan is not None:
            mode, target = plan
            if mode == "merged":
                output_path = export(
                    model,
                    retained,
                    target,
                    validate=self._config.validate_before_export,
                    mark_namespace_subsets=self._config.mark_namespace_subsets,
                    external=external,
                )
                output_files = [output_path]
            else:
                split = export_split(
                    model,
                    retained,
                    target,
                    validate=self._config.validate_before_export,
                    mark_namespace_subsets=self._config.mark_namespace_subsets,
                    external=external,
                )
                output_files = sorted(split.files.values())
                manifest_path = split.manifest_path
                manifest = split.manifest
                if namespace_report is not None:
                    namespace_report.output_files = {
                        namespace_alias(model, uri): path.name
                        for uri, path in sorted(split.files.items())
                    }

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
            output_files=output_files,
            manifest_path=manifest_path,
            manifest=manifest,
            export_mode=export_mode,
            external=external,
        )

    def analyze(self) -> LinkResult:
        """Report-only run: nothing is exported."""
        return self.run(export_output=False)

    def _planned_output_files(
        self,
        plan: tuple[ExportMode, str],
        model: SemanticModel,
        retained: set[ExpandedNodeId],
        report: NamespaceReportData,
    ) -> dict[str, str]:
        """The file each shipped namespace is written to — named, but not necessarily written.

        A merged export puts every namespace in one document; a split export gives each
        namespace its own file, keyed by the same namespace name the report counts under.
        """
        mode, target = plan
        if mode == "merged":
            name = Path(target).name
            return {alias: name for alias in report.counts}
        names = split_file_names(model, {node_id.namespace for node_id in retained})
        return {namespace_alias(model, uri): name for uri, name in names.items()}

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
