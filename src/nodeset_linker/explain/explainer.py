"""Explainability: answer *why* a node is retained."""

from __future__ import annotations

from dataclasses import dataclass

from nodeset_linker.models.identifiers import ExpandedNodeId
from nodeset_linker.repository.model import SemanticModel
from nodeset_linker.resolver.closure import Closure, RetentionReason
from nodeset_linker.utils.errors import ResolutionError


@dataclass(slots=True)
class Explanation:
    """Human-readable answer for one node."""

    node_label: str
    node_id: ExpandedNodeId
    retained: bool
    reason: str | None = None
    hops: list[str] | None = None

    def render(self) -> str:
        if not self.retained:
            return f"{self.node_label}: removed (not reachable from any selected root)"
        lines = [f"{self.node_label}", "retained because:", *(self.hops or [])]
        return "\n".join(lines)


class Explainer:
    """Queries retention reasons of a computed closure."""

    def __init__(self, model: SemanticModel, closure: Closure) -> None:
        self._model = model
        self._closure = closure

    def explain(self, query: str) -> Explanation:
        node_id = self._find(query)
        reason: RetentionReason | None = self._closure.reason_for(node_id)
        if reason is None:
            return Explanation(
                node_label=self._label(node_id), node_id=node_id, retained=False, reason=None
            )
        hops = _render_path(reason)
        return Explanation(
            node_label=self._label(node_id),
            node_id=node_id,
            retained=True,
            reason=reason.describe(),
            hops=hops,
        )

    def explain_all(self) -> list[Explanation]:
        explanations: list[Explanation] = []
        for node_id, reason in sorted(
            self._closure.retained.items(), key=lambda kv: str(kv[1].origin)
        ):
            explanations.append(
                Explanation(
                    node_label=self._label(node_id),
                    node_id=node_id,
                    retained=True,
                    reason=reason.describe(),
                    hops=_render_path(reason),
                )
            )
        return explanations

    def _find(self, query: str) -> ExpandedNodeId:
        if query.startswith(("nsu=", "ns=", "i=", "s=", "g=", "b=")):
            node_id = ExpandedNodeId.parse(query)
            if node_id in self._model.nodes:
                return node_id
            raise ResolutionError(f"unknown node {query!r}")
        matches = self._model.find_by_browse_name(query) or self._model.find_by_display_name(query)
        if not matches:
            raise ResolutionError(f"unknown node {query!r}")
        if len(matches) > 1:
            retained = [m for m in matches if m.node_id in self._closure.ids()]
            if len(retained) == 1:
                return retained[0].node_id
            raise ResolutionError(
                f"{query!r} matches several nodes: "
                + ", ".join(str(m.node_id) for m in matches[:5])
                + "; disambiguate with a NodeId"
            )
        return matches[0].node_id

    def _label(self, node_id: ExpandedNodeId) -> str:
        node = self._model.get(node_id)
        return node.browse_name.name if node else str(node_id)


def _render_path(reason: RetentionReason) -> list[str]:
    """Render the dependency chain like the documented example:

    ``MSSControllerType -> JobManagementType -> CurrentJob -> JobDataType -> JobStateEnum``
    """
    names = [reason.origin_label] + [step.browse_name for step in reason.path]
    chain = " -> ".join(names)
    kind = reason.kind.value if reason.kind is not None else "root"
    return [chain, f"(origin: {reason.origin_label}, dependency kind: {kind})"]
