"""Namespace-owned documents for one validated retained semantic graph."""

from __future__ import annotations

import hashlib
import json
import re
from collections import Counter
from pathlib import Path
from tempfile import TemporaryDirectory

from nodeset_linker.exporter.nodeset import NodeSetExporter, _subset_model
from nodeset_linker.models.identifiers import ExpandedNodeId
from nodeset_linker.models.namespace import alias_from_uri
from nodeset_linker.repository.model import SemanticModel
from nodeset_linker.utils.errors import ExportError
from nodeset_linker.validator.semantic import SemanticValidator

_RESERVED_NAMES = {
    "CON", "PRN", "AUX", "NUL",
    *(f"COM{number}" for number in range(1, 10)),
    *(f"LPT{number}" for number in range(1, 10)),
}


def split_output_files(
    model: SemanticModel, retained: set[ExpandedNodeId]
) -> dict[str, str]:
    """Map owned namespace URIs to deterministic, Windows-safe relative filenames."""
    missing = retained - model.nodes.keys()
    if missing:
        raise ExportError(f"retained node not found in model: {min(map(str, missing))}")
    bases: dict[str, str] = {}
    for uri in sorted({node_id.namespace for node_id in retained}):
        base = re.sub(r"[^A-Za-z0-9._-]+", "_", alias_from_uri(uri) or "namespace")
        base = base[:80].strip(".") or "namespace"
        if base.split(".")[0].upper() in _RESERVED_NAMES:
            base = f"_{base}"
        bases[uri] = base
    counts = Counter(base.casefold() for base in bases.values())
    occupied = {f"{base}.xml".casefold() for base in bases.values()}
    files: dict[str, str] = {}
    for uri, base in bases.items():
        name = f"{base}.xml"
        if counts[base.casefold()] > 1:
            suffix = hashlib.sha256(uri.encode("utf-8")).hexdigest()[:12]
            name = f"{base}-{suffix}.xml"
            number = 2
            while name.casefold() in occupied:
                name = f"{base}-{suffix}-{number}.xml"
                number += 1
        occupied.add(name.casefold())
        files[uri] = name
    return files


def _manifest_entry(model: SemanticModel, uri: str, filename: str) -> dict[str, str]:
    """What this bundle publishes under one namespace URI.

    The release metadata belongs in the bundle (§11.1): the next release reads it back as its
    baseline and can tell whether this URI was already published (§11.3).
    """
    entry = {"uri": uri, "output_file": filename}
    namespace = model.namespaces.get(uri)
    if namespace is not None:
        for key, value in (
            ("alias", namespace.alias),
            ("version", namespace.version),
            ("publication_date", namespace.publication_date),
            ("model_version", namespace.model_version),
            ("xml_schema_uri", namespace.xml_schema_uri),
        ):
            if value:
                entry[key] = value
    return entry


def export_split(
    model: SemanticModel,
    keep: set[ExpandedNodeId],
    directory: Path | str,
    *,
    validate: bool = True,
    mark_namespace_subsets: bool = True,
    external: frozenset[str] = frozenset(),
) -> dict[str, Path]:
    """Validate the bundle, then publish namespace files and a manifest together.

    The destination must be absent or empty. Serialization happens in a temporary sibling
    directory so a failed build cannot leave a partial bundle or a success-shaped manifest.
    """
    shipped = {node_id for node_id in keep if node_id.namespace not in external}
    files = split_output_files(model, shipped)
    if validate:
        report = SemanticValidator(
            _subset_model(model, shipped), source=model, external=external
        ).validate()
        if not report.ok:
            raise ExportError(f"export blocked: {report.summary()}")
    out = Path(directory)
    try:
        if out.is_symlink() or (
            out.exists() and (not out.is_dir() or next(out.iterdir(), None) is not None)
        ):
            raise ExportError(f"split export requires an absent or empty directory: {out}")
        out.parent.mkdir(parents=True, exist_ok=True)
        with TemporaryDirectory(prefix=".nodeset-linker-", dir=out.parent) as temporary:
            bundle = Path(temporary) / "bundle"
            bundle.mkdir()
            for uri, filename in files.items():
                NodeSetExporter(
                    model,
                    shipped,
                    mark_namespace_subsets=mark_namespace_subsets,
                    external=external,
                    document_namespace=uri,
                ).write(bundle / filename)
            manifest = {
                "export_mode": "split",
                "namespaces": [
                    _manifest_entry(model, uri, filename) for uri, filename in files.items()
                ],
                "dependencies": model.namespace_dependencies(shipped),
                "external_models": sorted(external),
            }
            (bundle / "manifest.json").write_text(
                json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
            )
            if out.exists():
                out.rmdir()
            bundle.rename(out)
    except OSError as exc:
        raise ExportError(f"could not write split export to {out}: {exc}") from exc
    return {uri: out / filename for uri, filename in files.items()}
