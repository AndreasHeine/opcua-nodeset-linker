"""The lint report: findings that carry a severity, a rule slug and paper provenance.

A finding reuses the validation ``Finding`` (code, message, node) and adds the severity of the
diff rule it belongs to, so a lint pass and a diff report speak the same vocabulary.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from nodeset_linker.diff.rules import PAPER, Severity
from nodeset_linker.lint.rules import LintRule
from nodeset_linker.models.identifiers import ExpandedNodeId
from nodeset_linker.validator.semantic import Finding


@dataclass(slots=True)
class LintFinding(Finding):
    """A best-practice finding: a validation finding plus its severity and rule."""

    severity: Severity = Severity.ADVISORY
    rule_id: str = ""
    clause: str | None = None

    def __str__(self) -> str:
        where = f" [{self.node_id}]" if self.node_id else ""
        return f"{self.code}: {self.message}{where} ({self.rule_id})"


def finding(
    rule: LintRule, message: str, node_id: ExpandedNodeId | None = None
) -> LintFinding:
    """Build one finding from a rule, so the severity and slug never drift apart."""
    return LintFinding(
        code=rule.code,
        message=message,
        node_id=node_id,
        severity=rule.severity,
        rule_id=rule.rule_id,
        clause=rule.clause,
    )


@dataclass(slots=True)
class LintReport:
    """Findings of one lint pass, split by severity.

    Lint never gates a build: an advisory or information finding says *look at this*, not *this
    broke*.  A structurally invalid model is reported by semantic validation, not here.
    """

    findings: list[LintFinding] = field(default_factory=list)
    paper: str = PAPER

    @property
    def ok(self) -> bool:
        """Whether the pass can be shipped: it always can, by design."""
        return not any(item.severity.blocks_release for item in self.findings)

    def by_severity(self, severity: Severity) -> list[LintFinding]:
        return [item for item in self.findings if item.severity is severity]

    def summary(self) -> str:
        advisory = len(self.by_severity(Severity.ADVISORY))
        info = len(self.by_severity(Severity.INFO))
        return f"{advisory} advisory finding(s), {info} information finding(s)"

    def as_json(self) -> dict[str, Any]:
        return {
            "paper": self.paper,
            "summary": {
                "findings": len(self.findings),
                "advisory": len(self.by_severity(Severity.ADVISORY)),
                "info": len(self.by_severity(Severity.INFO)),
            },
            "findings": [
                {
                    "code": item.code,
                    "message": item.message,
                    "node": item.node_id.as_xml() if item.node_id else None,
                    "severity": item.severity.value,
                    "rule_id": item.rule_id,
                    "clause": item.clause,
                }
                for item in self.findings
            ],
        }
