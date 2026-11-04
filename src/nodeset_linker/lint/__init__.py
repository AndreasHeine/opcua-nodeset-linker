"""Best-practice lint of OPC 11030 over one semantic model.

The checks judge the model, not the XML: naming (§2), StatusCode ownership (§4), structure
encoding limits (§7.9), recursive declarations (§7.10), text (§13) and audit events (§15).
They report advisory and information findings only — a lint pass never gates a build.
"""

from __future__ import annotations

from nodeset_linker.lint.checks import run
from nodeset_linker.lint.report import LintFinding, LintReport
from nodeset_linker.lint.rules import LINT_RULES, LintRule
from nodeset_linker.repository.model import SemanticModel

__all__ = ["LINT_RULES", "LintFinding", "LintReport", "LintRule", "lint_model"]


def lint_model(model: SemanticModel) -> LintReport:
    """Run every best-practice check over a loaded model."""
    return LintReport(findings=run(model))
