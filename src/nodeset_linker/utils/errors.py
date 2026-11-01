"""Errors raised by the linker."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover
    from nodeset_linker.validator.semantic import ValidationReport


class LinkerError(Exception):
    """Base class for all linker failures."""


class ParseError(LinkerError):
    """The source document cannot be turned into a semantic model."""


class ResolutionError(LinkerError):
    """A dependency or root selector could not be resolved."""


class ValidationError(LinkerError):
    """The resulting information model is semantically invalid."""

    def __init__(self, message: str, findings: ValidationReport | None = None) -> None:
        super().__init__(message)
        self.findings = findings


class ExportError(LinkerError):
    """Export was refused (typically because validation failed)."""
