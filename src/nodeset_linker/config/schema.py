"""Configuration: YAML input validated with Pydantic."""

from __future__ import annotations

from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field, field_validator, model_validator
from pydantic import ValidationError as PydanticValidationError

from nodeset_linker.retention.engine import CustomRule
from nodeset_linker.utils.errors import ParseError

Mode = Literal["STRICT", "OPTIONAL_AWARE", "RUNTIME", "CUSTOM"]
ExportMode = Literal["merged", "split"]


class CustomRulesConfig(BaseModel):
    """``retain:`` / ``exclude:`` sections of a custom policy."""

    retain: list[str] = Field(default_factory=list)
    exclude: list[str] = Field(default_factory=list)


class ExportConfig(BaseModel):
    """``export:`` — *how* the reduced nodes are written, not which nodes survive.

    ``merged`` writes one NodeSet holding every retained node; that is the behaviour a
    plain ``output:`` setting always had.  ``split`` writes one NodeSet per namespace the
    reduction ships, plus ``manifest.json`` in the same folder.  The reduction is identical
    in both modes: a node keeps its own namespace, appears in exactly one file, and a
    cross-namespace dependency stays a *reference* — nothing is duplicated, inlined or
    merged, so the files together are the same semantic graph.
    """

    mode: ExportMode = "merged"
    file: str | None = None
    """Where a merged export writes its single NodeSet."""
    directory: str | None = None
    """Where a split export writes its NodeSets and ``manifest.json``."""

    @field_validator("file", "directory")
    @classmethod
    def _target_is_a_path(cls, value: str | None) -> str | None:
        if value is not None and not value.strip():
            raise ValueError("export target must be a path, not empty")
        return value


class LinkerConfig(BaseModel):
    """Top-level configuration of one linking run."""

    nodesets: list[str] = Field(min_length=1)
    mode: Mode = "STRICT"
    roots: list[str] = Field(min_length=1)
    output: str | None = None
    """Legacy single-output target; kept working unchanged (see :attr:`export`)."""
    export: ExportConfig | None = None
    """Export mode: one merged NodeSet, or one NodeSet per shipped namespace.

    When this is set it wins over ``output:``; when it is not, ``output:`` is a merged
    export exactly as before.
    """
    custom: CustomRulesConfig | None = None
    validate_before_export: bool = True
    mark_namespace_subsets: bool = True
    """Write ``IsNamespaceSubset=true`` for namespaces the reduction cut down.

    The flag lives on the namespace metadata object (OPC 10000-5), not in the file
    header; turning this off leaves whatever value the source document had in place.
    """

    external: list[str] = Field(default_factory=list)
    """ModelUris / namespace URIs whose node declarations are left out of the output.

    This is *not* a reduction.  The nodes of an externalized model are omitted even when
    the dependency closure pulled them in, while every reference that points at them is
    kept and the namespace URI stays in ``NamespaceUris``.  A dependency is configured by
    the stable ModelUri / namespace URI — never by a document-local namespace index — and
    the OPC UA namespace (``http://opcfoundation.org/UA/``) may be externalized like any
    other model.  Declaring a dependency does not free the consumer from it: the
    application importing the file has to make the required model available.
    """

    @field_validator("external")
    @classmethod
    def _external_are_model_uris(cls, value: list[str]) -> list[str]:
        """Only URIs count as dependencies; identifiers and indexes do not."""
        uris: list[str] = []
        for entry in value:
            stripped = entry.strip()
            if not stripped:
                raise ValueError(
                    "external dependency must be a ModelUri / namespace URI, not empty"
                )
            if stripped.lower().startswith(("ns=", "nsu=", "i=", "s=", "g=", "b=", "index:")):
                raise ValueError(
                    f"external dependency {stripped!r} is a namespace index or identifier, "
                    "which only means something inside one document; configure the ModelUri / "
                    "namespace URI instead (e.g. http://opcfoundation.org/UA/DI/)"
                )
            if "://" not in stripped:
                raise ValueError(
                    f"external dependency {stripped!r} is not a namespace URI "
                    "(e.g. http://opcfoundation.org/UA/DI/)"
                )
            uris.append(stripped)
        return list(dict.fromkeys(uris))

    @model_validator(mode="after")
    def _export_target_matches_mode(self) -> LinkerConfig:
        """Each mode must name the target it writes: a file for merged, a folder for split."""
        export = self.export
        if export is None:
            return self
        if export.mode == "merged":
            if export.directory:
                raise ValueError(
                    "export.mode=merged writes one NodeSet; set export.file, not export.directory"
                )
            if not export.file and not self.output:
                raise ValueError(
                    "export.mode=merged needs export.file (the NodeSet to write), "
                    "or the legacy output: setting"
                )
        else:
            if export.file:
                raise ValueError(
                    "export.mode=split writes one NodeSet per namespace; set export.directory, "
                    "not export.file"
                )
            if not export.directory and not self.output:
                raise ValueError(
                    "export.mode=split needs export.directory (the folder for the NodeSets "
                    "and manifest.json), or the legacy output: setting"
                )
        return self

    def export_plan(self) -> tuple[ExportMode, str] | None:
        """The export this configuration asks for: ``(mode, target)``, or None to write nothing.

        ``export:`` wins over ``output:``; a merged export without ``export.file`` and a
        split export without ``export.directory`` fall back to ``output:``, so a
        configuration written before split export existed keeps working as it was.
        """
        if self.export is None:
            return ("merged", self.output) if self.output else None
        target = (
            self.export.file or self.output
            if self.export.mode == "merged"
            else self.export.directory or self.output
        )
        return (self.export.mode, target) if target else None

    def custom_rules(self) -> list[CustomRule]:
        """Translate the YAML rules into retention rules (retain wins over exclude)."""
        if self.custom is None:
            return []
        rules = [CustomRule(include=True, **_parse_rule(line)) for line in self.custom.retain]
        rules += [CustomRule(include=False, **_parse_rule(line)) for line in self.custom.exclude]
        return rules


def load_config(path: Path | str) -> LinkerConfig:
    """Read and schema-validate a YAML configuration file."""
    try:
        raw = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise ParseError(f"{path}: {exc}") from exc
    if not isinstance(raw, dict):
        raise ParseError(f"{path}: expected a YAML mapping")
    try:
        return LinkerConfig.model_validate(raw)
    except PydanticValidationError as exc:
        raise ParseError(f"{path}: invalid configuration\n{exc}") from exc


#: Custom rule keywords, lower-cased, mapped to the ``CustomRule`` fields they fill.
RULE_KEYS: dict[str, str] = {
    "namespace": "namespace",
    "browsename": "browse_name",
    "nodeclass": "node_class",
    "modellingrule": "modelling_rule",
}


def _parse_rule(line: str) -> dict[str, str | None]:
    """``namespace=<uri glob>`` / ``browseName=*Diagnostics*`` → keyword arguments."""
    key, sep, value = line.partition("=")
    if not sep or not value:
        raise ParseError(f"invalid custom rule: {line!r} (expected key=value)")
    lowered = key.lower()
    if lowered not in RULE_KEYS:
        raise ParseError(
            f"invalid custom rule: {line!r} (supported keys: "
            f"{', '.join(sorted(RULE_KEYS.values()))}; values may contain globs)"
        )
    return {field: value if lowered == alias else None for alias, field in RULE_KEYS.items()}
