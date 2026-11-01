"""Root selection: turn user expressions into concrete root node ids."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

from nodeset_linker.models.identifiers import ExpandedNodeId
from nodeset_linker.models.node import UANode
from nodeset_linker.repository.model import SemanticModel
from nodeset_linker.utils.errors import ResolutionError
from nodeset_linker.utils.text import matches_glob

SelectorKind = Literal[
    "nodeid",
    "expandednodeid",
    "browseName",
    "namespaceUri",
    "typeDefinition",
    "glob",
]

_SELECTOR = re.compile(r"^(?P<kind>[A-Za-z]+)=(?P<value>.+)$")

_KINDS: dict[str, SelectorKind] = {
    "nodeId": "nodeid",
    "nodeid": "nodeid",
    "expandedNodeId": "expandednodeid",
    "expandednodeid": "expandednodeid",
    "browseName": "browseName",
    "browsename": "browseName",
    "namespaceUri": "namespaceUri",
    "namespaceuri": "namespaceUri",
    "typeDefinition": "typeDefinition",
    "typedefinition": "typeDefinition",
}


@dataclass(frozen=True, slots=True)
class RootSelector:
    """One parsed root expression, e.g. ``browseName=MachineType``."""

    kind: SelectorKind
    value: str

    @classmethod
    def parse(cls, text: str) -> RootSelector:
        stripped = text.strip()
        if stripped.startswith(("nsu=", "ns=", "i=", "s=", "g=", "b=")):
            return cls(kind="nodeid", value=stripped)
        match = _SELECTOR.match(stripped)
        if match is None:
            raise ResolutionError(f"cannot parse root selector: {text!r}")
        kind = match.group("kind")
        normalised = _KINDS.get(kind)
        if normalised is None:
            raise ResolutionError(f"unknown root selector kind: {kind!r}")
        return cls(kind=normalised, value=match.group("value"))



class RootResolver:
    """Resolves root selectors against the semantic model."""

    def __init__(self, model: SemanticModel) -> None:
        self._model = model

    def resolve(self, selectors: list[RootSelector]) -> list[ExpandedNodeId]:
        roots: list[ExpandedNodeId] = []
        for selector in selectors:
            found = self._one(selector)
            if not found:
                raise ResolutionError(f"root selector matched nothing: {selector.value!r}")
            roots.extend(found)
        return _dedupe(roots)

    def _one(self, selector: RootSelector) -> list[ExpandedNodeId]:
        if selector.kind in {"nodeid", "expandednodeid"}:
            node_id = ExpandedNodeId.parse(selector.value)
            resolved = self._resolve_index_marker(node_id)
            return [resolved] if resolved in self._model.nodes else []
        if selector.kind == "browseName":
            return [n.node_id for n in self._match_browse(selector.value)]
        if selector.kind == "namespaceUri":
            uri = selector.value
            return [
                n.node_id
                for n in self._model.iter_nodes()
                if n.node_id.namespace == uri or n.node_id.namespace.endswith(uri)
            ]
        if selector.kind == "typeDefinition":
            return [n.node_id for n in self._match_type_definition(selector.value)]
        raise ResolutionError(f"unsupported root selector kind: {selector.kind!r}")

    def _match_browse(self, pattern: str) -> list[UANode]:
        if "*" in pattern or "?" in pattern:
            return [
                n
                for n in self._model.iter_nodes()
                if matches_glob(pattern, n.browse_name.name)
            ]
        return self._model.find_by_browse_name(pattern)

    def _match_type_definition(self, pattern: str) -> list[UANode]:
        """The type itself plus every instance that declares it as type definition."""
        types = self._match_browse(pattern)
        type_ids = {t.node_id for t in types}
        matched: list[UANode] = list(types)
        seen = {node.node_id for node in matched}
        for node in self._model.iter_nodes():
            definition = node.type_definition
            if definition is not None and definition in type_ids and node.node_id not in seen:
                matched.append(node)
                seen.add(node.node_id)
        return matched

    def _resolve_index_marker(self, node_id: ExpandedNodeId) -> ExpandedNodeId:
        """``ns=<index>`` from a config file refers to the document-local index."""
        uri = node_id.namespace_uri
        if uri is None or not uri.startswith("index:"):
            return node_id
        index = int(uri.removeprefix("index:"))
        for namespace in self._model.namespaces.values():
            if namespace.index_in_document == index:
                return ExpandedNodeId(identifier=node_id.identifier, namespace_uri=namespace.uri)
        raise ResolutionError(f"namespace index {index} is not declared by any loaded nodeset")


def _dedupe(ids: list[ExpandedNodeId]) -> list[ExpandedNodeId]:
    seen: dict[ExpandedNodeId, None] = {}
    for node_id in ids:
        seen.setdefault(node_id, None)
    return list(seen)
