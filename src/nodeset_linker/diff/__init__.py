"""Semantic diff: compare two information models, not two XML files.

The diff reads the semantic model objects produced by the repository layer and reports
what changed in the *information model*: added and removed nodes, changed references,
changed type definitions, namespace totals, dependency impact and whether a change is
breaking.  XML ordering, formatting, whitespace and ``NamespaceIndex`` never reach the
comparison — identity is ``(namespaceUri, identifier)``.
"""

from __future__ import annotations

from nodeset_linker.diff.engine import diff_models, node_label
from nodeset_linker.diff.impact import apply_impact, dependents, total_impact
from nodeset_linker.diff.model import (
    BreakingChange,
    Change,
    NamespaceChange,
    NodeEntry,
    NodeModification,
    RenumberedNode,
    SemanticDiff,
    json_text,
)
from nodeset_linker.diff.render import console_text, html_text, markdown_text

__all__ = [
    "BreakingChange",
    "Change",
    "NamespaceChange",
    "NodeEntry",
    "NodeModification",
    "RenumberedNode",
    "SemanticDiff",
    "apply_impact",
    "console_text",
    "dependents",
    "diff_models",
    "html_text",
    "json_text",
    "markdown_text",
    "node_label",
    "total_impact",
]
