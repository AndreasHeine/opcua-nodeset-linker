"""Command line interface (Click + Rich).

nodeset-linker analyze config.yaml
nodeset-linker link config.yaml
nodeset-linker explain --node JobStateEnum config.yaml
nodeset-linker stats output.xml
nodeset-linker validate output.xml [--schema tests/data/schema/UANodeSet.xsd]
"""

from __future__ import annotations

import json
from pathlib import Path

import click
from lxml.etree import XMLSchema, parse
from rich.console import Console
from rich.json import JSON
from rich.panel import Panel
from rich.tree import Tree

from nodeset_linker.config.schema import load_config
from nodeset_linker.explain.explainer import Explainer
from nodeset_linker.pipeline import LinkerPipeline
from nodeset_linker.reports.data import DependencyReportData, Statistics
from nodeset_linker.repository.model import load_nodesets
from nodeset_linker.utils.errors import LinkerError
from nodeset_linker.validator.semantic import (
    ValidationReport,
    declared_external_namespaces,
    validate_model,
)

console = Console()


@click.group()
def main() -> None:
    """OPC UA NodeSet Linker - semantic dependency linker and tree shaker."""


@main.command()
@click.argument("config", type=Path)
def analyze(config: Path) -> None:
    """Report the reduction without writing anything."""
    try:
        result = LinkerPipeline(load_config(config)).analyze()
    except LinkerError as exc:
        _fail(str(exc))

    _render_statistics(result.statistics)
    _render_external(result.external)
    if result.namespace_report is not None:
        console.print(Panel(JSON(result.namespace_report.as_json()), title="Namespace report"))
    if result.dependency_report is not None:
        _render_dependency_tree(result.dependency_report)
    if result.validation is not None:
        _render_validation(result.validation)


@main.command()
@click.argument("config", type=Path)
@click.option("--output", "-o", type=Path, default=None, help="Override the output path.")
def link(config: Path, output: Path | None) -> None:
    """Reduce the nodesets and write a NodeSet.

    Models listed under ``external`` are referenced but not shipped: their declarations are
    left out while the references, namespace URI and ``RequiredModel`` entry stay in the
    file, so the importing application has to make those models available itself.
    """
    cfg = load_config(config)
    if output is not None:
        cfg = cfg.model_copy(update={"output": str(output)})
    try:
        result = LinkerPipeline(cfg).run()
    except LinkerError as exc:
        _fail(str(exc))

    _render_statistics(result.statistics)
    _render_external(result.external)
    if result.output_path is not None:
        console.print(f"[green]wrote[/] {result.output_path}")
    else:
        console.print("[yellow]no output path configured; nothing written[/]")


@main.command()
@click.argument("config", type=Path)
@click.option("--node", "-n", "node", required=True, help="BrowseName, DisplayName or NodeId.")
def explain(config: Path, node: str) -> None:
    """Explain why a node is retained (or why it was removed)."""
    try:
        cfg = load_config(config)
        result = LinkerPipeline(cfg).analyze()
        explanation = Explainer(result.model, result.closure).explain(node)
    except LinkerError as exc:
        _fail(str(exc))

    if explanation.retained:
        tree = Tree(f"[bold green]{explanation.node_label}[/] retained because:")
        for hop in explanation.hops or []:
            tree.add(hop)
        console.print(tree)
    else:
        console.print(
            f"[red]{explanation.node_label}[/] removed - not reachable from any selected root"
        )


@main.command()
@click.argument("output", type=Path)
def stats(output: Path) -> None:
    """Show node statistics of a produced NodeSet."""
    model = load_nodesets([output])
    console.print(
        JSON(
            json.dumps(
                {
                    "nodes": model.node_count(),
                    "namespaces": len(model.namespaces),
                    "edges": sum(len(node.references) for node in model.iter_nodes()),
                },
                indent=2,
            )
        ),
    )


@main.command()
@click.argument("output", type=Path)
@click.option(
    "--schema",
    type=Path,
    default=None,
    help="Also validate the XML against a UANodeSet.xsd (e.g. tests/data/schema/UANodeSet.xsd).",
)
def validate(output: Path, schema: Path | None) -> None:
    """Validate a produced NodeSet: semantics, and the XSD when ``--schema`` is given.

    Without ``--schema`` this is *semantic* validation only — reference targets, type
    hierarchies, encodings, namespace links and subset markers.  Schema conformance is a
    separate check and is only claimed when a schema was actually supplied.

    A ``RequiredModel`` whose declarations are not in the file is read as an external
    dependency: references into it are accepted, references into any other namespace that
    the file does not contain are errors.
    """
    failures = 0
    if schema is not None:
        failures += _validate_schema(output, schema)
    model = load_nodesets([output])
    report = validate_model(model, external=declared_external_namespaces(model))
    _render_validation(report)
    if not report.ok:
        failures += len(report.errors)
    if failures:
        raise SystemExit(1)


def _validate_schema(output: Path, schema: Path) -> int:
    """Validate the document against an XSD; returns the number of schema errors."""
    if not schema.exists():
        console.print(f"[bold red]error:[/] schema not found: {schema}")
        raise SystemExit(1)
    try:
        document = parse(str(output))
    except Exception as exc:  # any unreadable document is a validation failure
        console.print(f"[bold red]error:[/] cannot read {output}: {exc}")
        return 1
    xsd = XMLSchema(parse(str(schema)))
    if xsd.validate(document):
        console.print(f"[green]schema validation passed[/] ({schema})")
        return 0
    console.print(f"[red]schema errors in {output}[/]")
    for error in xsd.error_log:
        console.print(f"  [red]line {error.line_number}: {error.message}[/]")
    return 1


def _render_statistics(statistics: Statistics | None) -> None:
    """Print the reduction statistics as a JSON panel."""
    if statistics is None:
        return
    console.print(Panel(JSON(statistics.as_json()), title="Statistics"))


def _render_external(external: frozenset[str]) -> None:
    """The models this run referenced but did not ship — and what that means downstream.

    A declared dependency is not a bundled one: the file states the model and its version,
    the importing application still has to load it.
    """
    if not external:
        return
    lines = [f"[cyan]{uri}[/]" for uri in sorted(external)]
    lines.append("[dim]declarations not in the file; the importer must provide them[/]")
    console.print(Panel("\n".join(lines), title="External dependencies"))


def _render_dependency_tree(report: DependencyReportData) -> None:
    tree = Tree("[bold]dependency origins[/]")
    for origin, nodes in report.origins.items():
        branch = tree.add(f"[bold cyan]{origin}[/] -> {len(nodes)} node(s)")
        for name in nodes[:20]:
            branch.add(name)
        if len(nodes) > 20:
            branch.add(f"... {len(nodes) - 20} more")
    console.print(tree)


def _render_validation(report: ValidationReport) -> None:
    if report.errors:
        console.print(f"[red]{len(report.errors)} error(s)[/]")
        for finding in report.errors:
            console.print(f"  [red]{finding}[/]")
    if report.warnings:
        console.print(f"[yellow]{len(report.warnings)} warning(s)[/]")
        for finding in report.warnings:
            console.print(f"  [yellow]{finding}[/]")
    if report.errors:
        return
    if report.warnings:
        console.print("[green]validation passed with warnings[/]")
    else:
        console.print("[green]validation passed - no findings[/]")


def _fail(message: str) -> None:
    """Report a failure and exit with code 1."""
    console.print(f"[bold red]error:[/] {message}")
    raise SystemExit(1)
