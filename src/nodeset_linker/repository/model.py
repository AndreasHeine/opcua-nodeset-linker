"""Repository layer: the semantic information model, independent of documents.

Multiple NodeSet files are merged into one :class:`SemanticModel`.  Merge conflicts
are reported instead of silently overwritten, and cross-namespace references become
edges in the semantic graph — which is how cross-namespace dependencies are detected
automatically.
"""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from dataclasses import dataclass, field, replace
from pathlib import Path

from nodeset_linker.models.identifiers import ExpandedNodeId
from nodeset_linker.models.namespace import DocumentInfo, ModelInfo, Namespace
from nodeset_linker.models.node import Reference, UANode
from nodeset_linker.models.node_class import NodeClass
from nodeset_linker.parser.nodeset import parse_nodeset
from nodeset_linker.utils.errors import ParseError


class DuplicateNodeError(ParseError):
    """The same identifier appears in two source documents."""

    def __init__(self, node_id: ExpandedNodeId, paths: list[str]) -> None:
        super().__init__(f"duplicate node {node_id} in: {', '.join(paths)}")
        self.node_id = node_id
        self.paths = paths


@dataclass(slots=True)
class SemanticModel:
    """The merged semantic information model — the single modelling truth."""

    nodes: dict[ExpandedNodeId, UANode] = field(default_factory=dict)
    namespaces: dict[str, Namespace] = field(default_factory=dict)
    documents: list[DocumentInfo] = field(default_factory=list)
    models: dict[str, ModelInfo] = field(default_factory=dict)

    def add(self, node: UANode, source: str) -> None:
        """Add a node; the same identifier in two documents is a merge conflict.

        Repeated *identical* definitions are tolerated (a companion specification may
        re-declare an OPC UA base node), because they describe the same semantics.
        """
        existing = self.nodes.get(node.node_id)
        if existing is not None:
            if _same_semantics(existing, node):
                return
            paths = sorted({source, *(doc.source_path for doc in self.documents)})
            raise DuplicateNodeError(node.node_id, paths)
        self.nodes[node.node_id] = node

    def iter_nodes(self) -> Iterator[UANode]:
        """Iterate every node of the merged model."""
        return iter(self.nodes.values())

    def nodes_in_namespace(self, namespace_uri: str) -> Iterator[UANode]:
        return (node for node in self.iter_nodes() if node.node_id.namespace == namespace_uri)

    def get(self, node_id: ExpandedNodeId) -> UANode | None:
        return self.nodes.get(node_id)

    def find_by_browse_name(self, name: str) -> list[UANode]:
        return [node for node in self.iter_nodes() if node.browse_name.name == name]

    def find_by_display_name(self, name: str) -> list[UANode]:
        return [node for node in self.iter_nodes() if node.display_name == name]

    def referenced_namespaces(self) -> set[str]:
        """Every namespace referenced by any node, including foreign ones."""
        found = {node.node_id.namespace for node in self.iter_nodes()}
        found.update(
            ref.target.namespace for node in self.iter_nodes() for ref in node.references
        )
        return found

    def unresolved_targets(self) -> list[tuple[ExpandedNodeId, ExpandedNodeId]]:
        """(source, missing target) pairs — references pointing outside the model."""
        missing: list[tuple[ExpandedNodeId, ExpandedNodeId]] = []
        for node in self.iter_nodes():
            for ref in node.references:
                if ref.target not in self.nodes:
                    missing.append((node.node_id, ref.target))
        return missing

    def node_count(self) -> int:
        return len(self.nodes)


def load_nodesets(paths: Sequence[Path | str]) -> SemanticModel:
    """Load and merge NodeSet files into one semantic model."""
    model = SemanticModel()
    for path in paths:
        nodes, info = parse_nodeset(path)
        for namespace in info.namespaces:
            known = model.namespaces.get(namespace.uri)
            if known is None:
                model.namespaces[namespace.uri] = namespace
            else:
                model.namespaces[namespace.uri] = _merge_namespace(known, namespace)
        for node in nodes:
            model.add(node, info.source_path)
        for declared in info.models:
            known_model = model.models.get(declared.namespace_uri)
            if known_model is None or declared.version:
                model.models[declared.namespace_uri] = declared
        model.documents.append(info)
    resolve_reference_types(model)
    return model


def build_model(
    nodes: list[UANode], namespaces: dict[str, Namespace] | None = None
) -> SemanticModel:
    """Build a semantic model from domain objects (used by tests and embedders)."""
    model = SemanticModel(namespaces=dict(namespaces or {}))
    for node in nodes:
        model.nodes[node.node_id] = node
    resolve_reference_types(model)
    return model


def _merge_namespace(known: Namespace, incoming: Namespace) -> Namespace:
    """Keep the richest declaration of a namespace seen across documents."""
    return Namespace(
        uri=known.uri,
        alias=incoming.alias or known.alias,
        index_in_document=known.index_in_document,
        version=incoming.version or known.version,
        publication_date=incoming.publication_date or known.publication_date,
        xml_schema_uri=incoming.xml_schema_uri or known.xml_schema_uri,
        model_version=incoming.model_version or known.model_version,
    )


def resolve_reference_types(model: SemanticModel) -> int:
    """Fill the reference type name or id that a source document left out.

    Alias-less documents name references but carry no identifier; documents that write
    identifiers only give no name.  Once every document is merged, both sides can be
    completed from the reference type declarations themselves.
    """
    names_by_id = {
        node.node_id: node.browse_name.name
        for node in model.iter_nodes()
        if node.node_class is NodeClass.REFERENCE_TYPE
    }
    ids_by_name = {name: node_id for node_id, name in names_by_id.items()}
    changed = 0
    for node_id, node in list(model.nodes.items()):
        rebuilt: list[Reference] = []
        dirty = False
        for ref in node.references:
            name = ref.type_name
            reference_type = ref.reference_type
            if not name and reference_type is not None:
                name = names_by_id.get(reference_type, "")
                if name:
                    dirty = True
            elif reference_type is None and name:
                reference_type = ids_by_name.get(name)
                if reference_type is not None:
                    dirty = True
            rebuilt.append(replace(ref, type_name=name, reference_type=reference_type))
        if dirty:
            model.nodes[node_id] = node.with_references(tuple(rebuilt))
            changed += 1
    return changed


def _same_semantics(a: UANode, b: UANode) -> bool:
    """True when two declarations of the same identifier describe the same node."""
    return (
        a.node_class is b.node_class
        and a.browse_name == b.browse_name
        and a.is_abstract == b.is_abstract
        and set(a.reference_of("HasSubtype")) == set(b.reference_of("HasSubtype"))
    )
