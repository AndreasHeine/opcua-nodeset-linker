"""Command line interface (Click + Rich).

nodeset-linker analyze config.yaml
nodeset-linker link config.yaml
nodeset-linker explain --node JobStateEnum config.yaml
nodeset-linker stats output.xml
nodeset-linker validate output.xml [--schema tests/data/schema/UANodeSet.xsd]
nodeset-linker diff old.xml new.xml [--json | --markdown | --html] [--impact] [--breaking]
    [--advisory/--no-advisory] [--fail-on-breaking] [--fail-on-advisory]
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
from nodeset_linker.diff import (
    Severity,
    apply_impact,
    console_text,
    diff_models,
    html_text,
    json_text,
    markdown_text,
)
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
@click.option(
    "--output", "-o", type=Path, default=None,
    help="Override the output file (merged) or directory (split).",
)
def link(config: Path, output: Path | None) -> None:
    """Reduce the nodesets and write merged or namespace-split output.

    Models listed under ``external`` are referenced but not shipped: their declarations are
    left out while the references, namespace URI and ``RequiredModel`` entry stay in the
    file, so the importing application has to make those models available itself.
    """
    try:
        cfg = load_config(config)
        if output is not None:
            if cfg.export is not None:
                key = "directory" if cfg.export.mode == "split" else "file"
                cfg = cfg.model_copy(
                    update={"export": cfg.export.model_copy(update={key: str(output)})}
                )
            else:
                cfg = cfg.model_copy(update={"output": str(output)})
        result = LinkerPipeline(cfg).run()
    except LinkerError as exc:
        _fail(str(exc))

    _render_statistics(result.statistics)
    _render_external(result.external)
    if result.manifest_path is not None:
        for path in result.output_files.values():
            console.print(f"[green]wrote[/] {path}", soft_wrap=True)
        console.print(f"[green]wrote[/] {result.manifest_path}", soft_wrap=True)
    elif result.output_path is not None:
        console.print(f"[green]wrote[/] {result.output_path}", soft_wrap=True)
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


@main.command()
@click.argument("old", type=Path)
@click.argument("new", type=Path)
@click.option("--json", "as_json", is_flag=True, help="Machine-readable JSON for CI/CD.")
@click.option("--markdown", is_flag=True, help="Markdown report for a release note or PR.")
@click.option("--html", is_flag=True, help="Self-contained HTML report.")
@click.option("--impact", is_flag=True, help="Show the dependent nodes of every change.")
@click.option("--breaking", "breaking_only", is_flag=True, help="Report breaking changes only.")
@click.option(
    "--advisory/--no-advisory",
    "include_advisory",
    default=True,
    help="Show or hide the advisory findings that do not block a release.",
)
@click.option(
    "--fail-on-breaking",
    is_flag=True,
    help="Exit with code 1 when the diff contains a breaking change.",
)
@click.option(
    "--fail-on-advisory",
    is_flag=True,
    help="Exit with code 1 when the diff contains an advisory finding.",
)
@click.option("--output", "-o", type=Path, default=None, help="Write the report to a file.")
def diff(
    old: Path,
    new: Path,
    as_json: bool,
    markdown: bool,
    html: bool,
    impact: bool,
    breaking_only: bool,
    include_advisory: bool,
    fail_on_breaking: bool,
    fail_on_advisory: bool,
    output: Path | None,
) -> None:
    """Compare two semantic OPC UA models.

    The comparison is on the parsed semantic models, never on XML: document order,
    whitespace and ``NamespaceIndex`` reassignment are not differences, because a node is
    identified by ``(namespaceUri, identifier)``.  It reports added and removed nodes,
    reference changes, type-definition changes, BrowseName and DisplayName changes,
    modelling-rule changes, DataType definition changes (Structure fields, Enum values,
    Union alternatives, OptionSets) and namespace totals.
    """
    for path in (old, new):
        if not path.exists():
            _fail(f"no nodeset file at {path}")
    try:
        old_model = load_nodesets([old])
        new_model = load_nodesets([new])
    except LinkerError as exc:
        _fail(str(exc))

    result = diff_models(old_model, new_model, old_source=str(old), new_source=str(new))
    if impact or breaking_only or fail_on_breaking:
        apply_impact(result, old_model, new_model)

    if as_json:
        text = json_text(result)
    elif markdown:
        text = markdown_text(
            result, impact=impact, breaking_only=breaking_only, include_advisory=include_advisory
        )
    elif html:
        text = html_text(
            result, impact=impact, breaking_only=breaking_only, include_advisory=include_advisory
        )
    else:
        text = console_text(
            result, impact=impact, breaking_only=breaking_only, include_advisory=include_advisory
        )

    if output is not None:
        output.write_text(f"{text}\n", encoding="utf-8")
        console.print(f"[green]wrote[/] {output}")
    elif as_json or markdown or html:
        click.echo(text)
    else:
        console.print(text, markup=False, soft_wrap=True)

    if fail_on_breaking and (result.breaking or result.breaking_namespaces):
        raise SystemExit(1)
    advisory_namespaces = [
        entry for entry in result.namespaces if entry.severity is Severity.ADVISORY
    ]
    if fail_on_advisory and (result.advisory or advisory_namespaces):
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
