"""Semantic diff: compare two information models, not two XML files.

The diff reads the semantic model objects produced by the repository layer and reports
what changed in the *information model*: added and removed nodes, changed references,
changed type definitions, namespace totals and namespace release metadata (§11.1, §11.2,
§11.3), dependency impact and whether a change is breaking.  XML ordering, formatting,
whitespace and ``NamespaceIndex`` never reach the comparison — identity is
``(namespaceUri, identifier)``.
"""

from __future__ import annotations

from nodeset_linker.diff.engine import diff_models, node_label
from nodeset_linker.diff.impact import (
    INHERITANCE_DEPTH_LIMIT,
    apply_impact,
    dependents,
    impacted,
    total_impact,
)
from nodeset_linker.diff.model import (
    DEFAULT_IMPACT_LIMIT,
    BreakingChange,
    Change,
    MetadataDelta,
    NamespaceChange,
    NodeEntry,
    NodeModification,
    RenumberedNode,
    SemanticDiff,
    json_text,
)
from nodeset_linker.diff.release import (
    Baseline,
    NamespaceVerdict,
    ReleaseVerdict,
    load_baseline,
    release_text,
    release_verdict,
)
from nodeset_linker.diff.render import console_text, html_text, markdown_text
from nodeset_linker.diff.rules import PAPER, RuleContext, RuleVerdict, Severity, verdict
from nodeset_linker.diff.waivers import (
    Waiver,
    WaiverFileError,
    WaiverOutcome,
    WaiverSet,
    apply_waivers,
    load_waivers,
)

__all__ = [
    "DEFAULT_IMPACT_LIMIT",
    "INHERITANCE_DEPTH_LIMIT",
    "PAPER",
    "Baseline",
    "BreakingChange",
    "Change",
    "MetadataDelta",
    "NamespaceChange",
    "NamespaceVerdict",
    "NodeEntry",
    "NodeModification",
    "ReleaseVerdict",
    "RenumberedNode",
    "RuleContext",
    "RuleVerdict",
    "SemanticDiff",
    "Severity",
    "Waiver",
    "WaiverFileError",
    "WaiverOutcome",
    "WaiverSet",
    "apply_impact",
    "apply_waivers",
    "console_text",
    "dependents",
    "diff_models",
    "html_text",
    "impacted",
    "json_text",
    "load_baseline",
    "load_waivers",
    "markdown_text",
    "node_label",
    "release_text",
    "release_verdict",
    "total_impact",
    "verdict",
]
