"""Command line interface (Click + Rich).

nodeset-linker analyze config.yaml
nodeset-linker link config.yaml
nodeset-linker explain --node JobStateEnum config.yaml
nodeset-linker stats output.xml
nodeset-linker validate output.xml [--schema tests/data/schema/UANodeSet.xsd] [--best-practices]
nodeset-linker diff old.xml new.xml [--json | --markdown | --html] [--impact] [--breaking]
    [--advisory/--no-advisory] [--impact-limit N] [--context <file>...] [--breaking]
    [--namespace <uri>...] [--exclude-namespace <uri>...] [--node-class <class>...]
    [--fail-on-breaking] [--fail-on-advisory]
    [--waive <rule-id>] [--waiver-file <path>] [--accept-breaking]
    [--release-verdict] [--baseline <dir-or-manifest.json>]

Each diff side is a NodeSet file or a split-export bundle (a directory with ``manifest.json``).
``--namespace`` and ``--exclude-namespace`` take a NamespaceUri or the alias the report shows; a
name neither side declares is an error rather than an empty report.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from pathlib import Path
from typing import NoReturn

import click
from lxml.etree import XMLSchema, parse
from rich.console import Console
from rich.json import JSON
from rich.panel import Panel
from rich.tree import Tree

from nodeset_linker.config.schema import load_config
from nodeset_linker.diff import (
    DEFAULT_IMPACT_LIMIT,
    Severity,
    WaiverFileError,
    WaiverSet,
    apply_impact,
    apply_waivers,
    console_text,
    diff_models,
    html_text,
    json_text,
    load_baseline,
    markdown_text,
    release_verdict,
)
from nodeset_linker.explain.explainer import Explainer
from nodeset_linker.lint import LintReport, lint_model
from nodeset_linker.models.namespace import alias_from_uri
from nodeset_linker.models.node_class import NodeClass
from nodeset_linker.pipeline import LinkerPipeline
from nodeset_linker.reports.data import DependencyReportData, Statistics
from nodeset_linker.repository.model import SemanticModel, load_nodesets
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
@click.option(
    "--best-practices",
    is_flag=True,
    help="Also report OPC 11030 best practices; advisory findings never fail the command.",
)
def validate(output: Path, schema: Path | None, best_practices: bool) -> None:
    """Validate a produced NodeSet: semantics, and the XSD when ``--schema`` is given.

    Without ``--schema`` this is *semantic* validation only — reference targets, type
    hierarchies, encodings, namespace links and subset markers.  Schema conformance is a
    separate check and is only claimed when a schema was actually supplied.

    A ``RequiredModel`` whose declarations are not in the file is read as an external
    dependency: references into it are accepted, references into any other namespace that
    the file does not contain are errors.

    ``--best-practices`` adds the OPC 11030 lint pass on top of the semantic findings.  Its
    findings are advisory and information only, so they are printed but never change the exit
    code: a lint finding says *look at this*, not *this broke*.
    """
    failures = 0
    if schema is not None:
        failures += _validate_schema(output, schema)
    model = load_nodesets([output])
    report = validate_model(model, external=declared_external_namespaces(model))
    _render_validation(report)
    if best_practices:
        _render_lint(lint_model(model))
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
@click.option(
    "--impact-limit",
    type=int,
    default=DEFAULT_IMPACT_LIMIT,
    show_default=True,
    help="How many impacted nodes an entry lists; the reported count stays exact.",
)
@click.option(
    "--context",
    "context_paths",
    multiple=True,
    type=Path,
    help="Consumer models whose dependencies are counted as well — a sibling companion spec "
    "or the spec whose nodes the release externalized.",
)
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
@click.option(
    "--waive",
    "waive_rules",
    multiple=True,
    metavar="RULE_ID",
    help="Waive findings of this rule id; repeatable, advisory and information only.",
)
@click.option(
    "--waiver-file",
    type=Path,
    default=None,
    help="JSON waiver file: {rule, namespace?, node?, reason}; advisory and information only.",
)
@click.option(
    "--accept-breaking",
    is_flag=True,
    help="Acknowledge breaking findings: they stay in the report and the gate does not fail.",
)
@click.option(
    "--release-verdict",
    "show_release",
    is_flag=True,
    help="Answer the release question: may each NamespaceUri be reused? (§3.3, §11.3)",
)
@click.option(
    "--baseline",
    type=Path,
    default=None,
    help="Directory or manifest.json listing the NamespaceUris already published; without it "
    "the verdict says 'baseline unknown' instead of guessing.",
)
@click.option(
    "--reduced-export",
    is_flag=True,
    help="The new file is a Linker export: nodes whose namespace it declares as a RequiredModel "
    "were externalized (§11.2), not removed (§3.2.16), so they are information.",
)
@click.option(
    "--namespace",
    "include_namespaces",
    multiple=True,
    metavar="URI_OR_ALIAS",
    help="Report only this NamespaceUri (or its alias, the declared ``Alias`` or the URI tail); "
    "repeatable. Filtering applies to both sides, so a filtered namespace reports nothing "
    "instead of looking like a removal. A name neither side declares is an error, not an "
    "empty report.",
)
@click.option(
    "--exclude-namespace",
    "exclude_namespaces",
    multiple=True,
    metavar="URI_OR_ALIAS",
    help="Drop this NamespaceUri (or its alias) from both sides; repeatable. A name neither "
    "side declares is noted on stderr.",
)
@click.option(
    "--node-class",
    "node_classes",
    multiple=True,
    metavar="CLASS",
    help="Report only this NodeClass (Variable, ObjectType, DataType, Method, Object, "
    "ReferenceType, View, VariableType, Interface); repeatable.",
)
@click.option("--output", "-o", type=Path, default=None, help="Write the report to a file.")
def diff(
    old: Path,
    new: Path,
    as_json: bool,
    markdown: bool,
    html: bool,
    impact: bool,
    impact_limit: int,
    context_paths: tuple[Path, ...],
    breaking_only: bool,
    include_advisory: bool,
    fail_on_breaking: bool,
    fail_on_advisory: bool,
    waive_rules: tuple[str, ...],
    waiver_file: Path | None,
    accept_breaking: bool,
    show_release: bool,
    baseline: Path | None,
    reduced_export: bool,
    include_namespaces: tuple[str, ...],
    exclude_namespaces: tuple[str, ...],
    node_classes: tuple[str, ...],
    output: Path | None,
) -> None:
    """Compare two semantic OPC UA models.

    The comparison is on the parsed semantic models, never on XML: document order,
    whitespace and ``NamespaceIndex`` reassignment are not differences, because a node is
    identified by ``(namespaceUri, identifier, serverUri)``.  It reports added and removed
    nodes, reference changes, type-definition changes, BrowseName and DisplayName changes,
    modelling-rule changes, DataType definition changes (Structure fields, Enum values,
    Union alternatives, OptionSets), namespace totals and namespace release metadata.

    Each side may be one NodeSet file *or a split-export bundle* — a directory holding
    ``manifest.json`` (or that manifest itself): its namespace files are merged into one
    model, so a namespace-split release compares as the model it replaced.

    ``--namespace``, ``--exclude-namespace`` and ``--node-class`` scope the comparison. They
    filter *both* models, so a namespace or node class left out is simply not reported, and
    the release metadata of a filtered namespace is filtered with it (§11.1).  A namespace
    filter names the URI or the alias the report shows; a name neither side declares fails
    instead of printing an empty report.

    An inconsistent re-declaration of one identifier in the inputs does not abort the diff:
    the merge keeps the first declaration and the report states the conflict as an advisory
    ``BP-Merge-Conflict`` finding — a release review needs the diff, not a crash.

    ``--release-verdict`` answers the question the best-practices paper actually asks (§3.3,
    §11.3): may this NamespaceUri be reused, or must the release publish a versioned one?  It
    needs to know what was published, so ``--baseline`` points at a bundle manifest or a
    registry of published NamespaceUris; without one the verdict says so rather than guessing,
    and a not-reusable verdict exits with code 1.

    ``--waive`` and ``--waiver-file`` accept *advisory* and *information* findings: the finding
    stays in the report with its severity and rule id, marked ``waived``, and only the gate
    stops counting it.  A breaking finding cannot be waived; ``--accept-breaking`` acknowledges
    it, prints that acknowledgement in the report header and leaves the release verdict alone,
    because accepting a breaking change never makes a published NamespaceUri reusable (§11.3).
    """
    for path in (old, new):
        if not path.exists():
            _fail(f"no nodeset file at {path}")
    old_model = _load_model(_bundle_files(old, "old"), "old")
    new_model = _load_model(_bundle_files(new, "new"), "new")
    _validate_namespace_filters([old_model, new_model], include_namespaces, exclude_namespaces)
    old_model = _scoped(old_model, include_namespaces, exclude_namespaces, node_classes)
    new_model = _scoped(new_model, include_namespaces, exclude_namespaces, node_classes)

    result = diff_models(
        old_model,
        new_model,
        old_source=str(old),
        new_source=str(new),
        reduced_export=reduced_export,
    )
    result.impact_limit = impact_limit
    if context_paths:
        for path in context_paths:
            if not path.exists():
                _fail(f"no nodeset file at {path}")
        context = [_load_model(_bundle_files(path, "context"), "context") for path in context_paths]
    else:
        context = []
    if impact or breaking_only or fail_on_breaking or context:
        apply_impact(result, old_model, new_model, context=context)

    outcome = None
    if waive_rules or waiver_file is not None or accept_breaking:
        try:
            waiver_set = WaiverSet.from_cli(waive_rules, waiver_file)
        except WaiverFileError as exc:
            _fail(str(exc))
        outcome = apply_waivers(result, waiver_set, accept_breaking=accept_breaking)

    release = None
    if show_release or baseline is not None:
        try:
            registry = None if baseline is None else load_baseline(baseline)
        except ValueError as exc:
            _fail(str(exc))
        release = release_verdict(result, registry)

    if as_json:
        text = json_text(
            result,
            release=None if release is None else release.as_json(),
            waivers=None if outcome is None else outcome.as_json(),
        )
    elif markdown:
        text = markdown_text(
            result,
            impact=impact,
            breaking_only=breaking_only,
            include_advisory=include_advisory,
            release=release,
            waivers=outcome,
        )
    elif html:
        text = html_text(
            result,
            impact=impact,
            breaking_only=breaking_only,
            include_advisory=include_advisory,
            release=release,
            waivers=outcome,
        )
    else:
        text = console_text(
            result,
            impact=impact,
            breaking_only=breaking_only,
            include_advisory=include_advisory,
            release=release,
            waivers=outcome,
        )

    if output is not None:
        # A report written into a report directory: the directory is created, so a CI job
        # that asks for `dist/diff.html` does not fail on a path that does not exist yet.
        if output.parent and not output.parent.exists():
            output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(f"{text}\n", encoding="utf-8")
        console.print(f"[green]wrote[/] {output}")
    elif as_json or markdown or html:
        click.echo(text)
    else:
        console.print(text, markup=False, soft_wrap=True)

    # WP 15: the gate reads unwaived findings only.  A waiver or --accept-breaking marks a
    # finding rather than deleting it, so the report keeps the fact and the reason.
    if fail_on_breaking and (result.blocking_breaking() or result.blocking_namespaces()):
        click.echo("Returning exit code 1: blocking breaking changes found.", err=True)
        raise SystemExit(1)
    if fail_on_advisory and (
        result.blocking_advisory() or result.blocking_advisory_namespaces()
    ):
        click.echo("Returning exit code 1: blocking advisory findings found.", err=True)
        raise SystemExit(1)
    if release is not None and not release.reusable:
        # The release question itself is the gate (§11.3): a published URI may not be extended
        # with breaking findings, so the release has to publish a versioned namespace.
        click.echo("Returning exit code 1: the release verdict is not reusable.", err=True)
        raise SystemExit(1)


def _bundle_files(path: Path, label: str) -> list[Path]:
    """The NodeSet files one diff input stands for.

    A file is itself; a directory (or a ``manifest.json``) is a split-export bundle, whose
    namespace files together are the model.  Manifest order is used, so a bundle compares as
    the same model its merged form would be.
    """
    manifest = (
        path / "manifest.json"
        if path.is_dir()
        else path if path.name == "manifest.json"
        else None
    )
    if manifest is None:
        return [path]
    if not manifest.is_file():
        _fail(f"{label}: {path} is a directory without manifest.json — a bundle needs one")
    try:
        payload = json.loads(manifest.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        _fail(f"{label}: {manifest} is not valid JSON: {exc}")
    entries = payload.get("namespaces", []) if isinstance(payload, dict) else []
    files = [
        manifest.parent / str(entry["output_file"])
        for entry in entries
        if isinstance(entry, dict) and entry.get("output_file")
    ]
    if not files:
        _fail(f"{label}: {manifest} lists no namespace files")
    missing = [str(file) for file in files if not file.is_file()]
    if missing:
        _fail(f"{label}: {manifest} names files that are not there: {', '.join(missing)}")
    return files


def _load_model(paths: Sequence[Path], label: str) -> SemanticModel:
    """Load one diff side, tolerating an inconsistent re-declaration.

    A duplicate identifier with different semantics would otherwise abort the whole diff
    (`DuplicateNodeError`).  The tolerant merge keeps the first declaration and records the
    conflict, which the diff reports as an advisory finding — the review keeps its diff.
    """
    try:
        return load_nodesets(list(paths), tolerate_duplicates=True)
    except LinkerError as exc:
        _fail(f"{label}: {exc}")


def _scoped(
    model: SemanticModel,
    include_namespaces: tuple[str, ...],
    exclude_namespaces: tuple[str, ...],
    node_classes: tuple[str, ...],
) -> SemanticModel:
    """A copy of the model limited to what the reviewer asked to see.

    Namespace filters accept a NamespaceUri or the alias the report shows.  Excluding a
    namespace removes its release metadata too, so a scoped report cannot state a version of a
    namespace it does not show (§11.1).  Filters that address nothing are rejected by
    :func:`_validate_namespace_filters` before this runs, so a scoped model is never empty
    because of a typo.
    """
    wanted = {_spelling(name) for name in include_namespaces}
    unwanted = {_spelling(name) for name in exclude_namespaces}
    classes = _node_class_filter(node_classes)
    kept: set[str] = set()
    for uri in _namespace_uris(model):
        spellings = _namespace_spellings(model, uri)
        if wanted and not spellings & wanted:
            continue
        if spellings & unwanted:
            continue
        kept.add(uri)
    nodes = {
        node_id: node
        for node_id, node in model.nodes.items()
        if node_id.namespace in kept and (classes is None or node.node_class in classes)
    }
    return SemanticModel(
        nodes=nodes,
        namespaces={uri: info for uri, info in model.namespaces.items() if uri in kept},
        documents=list(model.documents),
        models={uri: info for uri, info in model.models.items() if uri in kept},
        sources={node_id: source for node_id, source in model.sources.items() if node_id in nodes},
        declarations={
            node_id: declared
            for node_id, declared in model.declarations.items()
            if node_id in nodes
        },
        conflicts=[conflict for conflict in model.conflicts if conflict.node_id in nodes],
    )


def _namespace_uris(model: SemanticModel) -> set[str]:
    """The namespaces a filter can address: the declared ones plus those nodes live in."""
    return {*model.namespaces, *(node.node_id.namespace for node in model.iter_nodes())}


def _namespace_spellings(model: SemanticModel, uri: str) -> set[str]:
    """Every name a namespace filter matches for this URI.

    The report names a namespace by its declared ``Alias`` when the document carries one and by
    the URI tail otherwise (:meth:`nodeset_linker.diff.engine.ModelIndex.alias`), so both
    spellings filter — a declared alias that differs from the URI tail is otherwise invisible
    to ``--namespace``.
    """
    spellings = {_spelling(uri), _spelling(alias_from_uri(uri) or "")}
    declared = model.namespaces.get(uri)
    if declared is not None and declared.alias:
        spellings.add(_spelling(declared.alias))
    return spellings


def _validate_namespace_filters(
    models: Sequence[SemanticModel],
    include_namespaces: tuple[str, ...],
    exclude_namespaces: tuple[str, ...],
) -> None:
    """Reject a namespace filter that addresses nothing on either side.

    Filters apply to both models, so a name nothing declares scopes *both* to an empty model:
    the report would say "No semantic differences found." and exit 0, which reads as a clean
    release even under ``--fail-on-breaking``.  An include filter is therefore a hard error; an
    exclude filter that matches nothing drops nothing, so it is only a notice on stderr.
    """
    if not include_namespaces and not exclude_namespaces:
        return
    names = {
        spelling
        for model in models
        for uri in _namespace_uris(model)
        for spelling in _namespace_spellings(model, uri)
    }
    unmatched = [name for name in include_namespaces if _spelling(name) not in names]
    if unmatched:
        _fail(
            f"--namespace matches no namespace in either side: {', '.join(unmatched)}; "
            "a filter that matches nothing would report nothing"
        )
    stray = sorted({name for name in exclude_namespaces if _spelling(name) not in names})
    if stray:
        click.echo(
            f"--exclude-namespace matches no namespace in either side: {', '.join(stray)}",
            err=True,
        )


def _spelling(name: str) -> str:
    """A namespace name as a filter compares it: case-insensitive, trimmed."""
    return name.strip().casefold()


def _node_class_filter(names: tuple[str, ...]) -> frozenset[NodeClass] | None:
    """The node classes a filter keeps, or ``None`` when no filter was given."""
    if not names:
        return None
    valid = {member.value.lower(): member for member in NodeClass}
    classes: set[NodeClass] = set()
    for name in names:
        member = valid.get(_spelling(name))
        if member is None:
            _fail(
                f"unknown node class {name!r}; known classes: "
                f"{', '.join(sorted(valid))}"
            )
        classes.add(member)
    return frozenset(classes)


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


def _render_lint(report: LintReport) -> None:
    """Print the best-practice findings of a lint pass, never as errors.

    The severity buckets are the ones the diff report uses: *advisory* and *information*.  A
    lint finding is not a failure, so nothing here touches the exit code.
    """
    console.print(f"[bold]best practices[/] - {report.summary()}")
    for severity, colour in ((Severity.ADVISORY, "yellow"), (Severity.INFO, "blue")):
        for item in report.by_severity(severity):
            console.print(f"  [{colour}]{item}[/]")
    if not report.findings:
        console.print("[green]no best-practice findings[/]")


def _fail(message: str) -> NoReturn:
    """Report a failure and exit with code 1."""
    console.print(f"[bold red]error:[/] {message}")
    raise SystemExit(1)
