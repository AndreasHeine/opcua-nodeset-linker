"""Configuration: YAML input validated with Pydantic."""

from __future__ import annotations

from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field, field_validator
from pydantic import ValidationError as PydanticValidationError

from nodeset_linker.retention.engine import CustomRule
from nodeset_linker.utils.errors import ParseError

Mode = Literal["STRICT", "OPTIONAL_AWARE", "RUNTIME", "CUSTOM"]


class CustomRulesConfig(BaseModel):
    """``retain:`` / ``exclude:`` sections of a custom policy."""

    retain: list[str] = Field(default_factory=list)
    exclude: list[str] = Field(default_factory=list)


class LinkerConfig(BaseModel):
    """Top-level configuration of one linking run."""

    nodesets: list[str] = Field(min_length=1)
    mode: Mode = "STRICT"
    roots: list[str] = Field(min_length=1)
    output: str | None = None
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
