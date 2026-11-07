"""Repository layer: the semantic information model, independent of documents.

Multiple NodeSet files are merged into one :class:`SemanticModel`.  Merge conflicts
are reported instead of silently overwritten, and cross-namespace references become
edges in the semantic graph — which is how cross-namespace dependencies are detected
automatically.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass, field, replace
from pathlib import Path

from nodeset_linker.models.identifiers import ExpandedNodeId
from nodeset_linker.models.namespace import DocumentInfo, ModelInfo, Namespace
from nodeset_linker.models.node import Reference, UANode, referenced_identifiers
from nodeset_linker.models.node_class import NodeClass
from nodeset_linker.parser.nodeset import parse_nodeset
from nodeset_linker.utils.errors import ParseError


class DuplicateNodeError(ParseError):
    """The same identifier appears in two source documents."""

    def __init__(self, node_id: ExpandedNodeId, paths: list[str]) -> None:
        super().__init__(f"duplicate node {node_id} in: {', '.join(paths)}")
        self.node_id = node_id
        self.paths = paths


@dataclass(frozen=True, slots=True)
class MergeConflict:
    """Two documents declare the same identifier with *different* semantics.

    A merge that tolerates the conflict keeps the first declaration and records what was
    dropped, so a run can report the fact instead of aborting: the retained model is
    incomplete on purpose, and the reader has to know which document lost.
    """

    node_id: ExpandedNodeId
    #: The two documents whose declarations disagree, sorted.  Documents that declared the
    #: same semantics as the kept one are not named: they did not conflict.
    paths: tuple[str, ...]
    #: The document whose declaration was kept.
    kept_source: str
    #: The document whose declaration was dropped.
    rejected_source: str
    browse_name: str
    node_class: str

    def describe(self) -> str:
        """One line naming the node, both documents, and which one won."""
        label = self.browse_name or self.node_id.identifier.as_xml()
        return (
            f"{label} [{self.node_class}, {self.node_id.identifier.as_xml()}] is declared "
            f"inconsistent in {' and '.join(self.paths)}; kept {self.kept_source}, "
            f"dropped {self.rejected_source}"
        )


@dataclass(slots=True)
class SemanticModel:
    """The merged semantic information model — the single modelling truth."""

    nodes: dict[ExpandedNodeId, UANode] = field(default_factory=dict)
    namespaces: dict[str, Namespace] = field(default_factory=dict)
    documents: list[DocumentInfo] = field(default_factory=list)
    models: dict[str, ModelInfo] = field(default_factory=dict)
    #: Which document contributed each node — a merge conflict names these two paths.
    sources: dict[ExpandedNodeId, str] = field(default_factory=dict)
    #: Every document that declared each identifier, in the order they were read.  A tolerated
    #: re-declaration of the same semantics counts here too; only the conflicting pair is named
    #: in a :class:`MergeConflict`.
    declarations: dict[ExpandedNodeId, list[str]] = field(default_factory=dict)
    #: Conflicts a tolerant merge kept going through (`load_nodesets(tolerate_duplicates=True)`).
    conflicts: list[MergeConflict] = field(default_factory=list)

    def _declared_by(self, node_id: ExpandedNodeId, source: str) -> None:
        """Record that ``source`` declares ``node_id`` — including tolerated duplicates."""
        declared = self.declarations.setdefault(node_id, [])
        if source not in declared:
            declared.append(source)

    def add(self, node: UANode, source: str, *, tolerate: bool = False) -> None:
        """Add a node; the same identifier in two documents is a merge conflict.

        Repeated *identical* definitions are tolerated (a companion specification may
        re-declare an OPC UA base node), because they describe the same semantics.  A
        *different* declaration raises :class:`DuplicateNodeError` unless ``tolerate`` is set,
        which keeps the first declaration and records a :class:`MergeConflict` instead — a
        release review should still see the conflict, it must not lose the whole diff to it.
        Both names report exactly the documents that declared this identifier, never a document
        that merely took part in the same merge.
        """
        existing = self.nodes.get(node.node_id)
        if existing is not None:
            self._declared_by(node.node_id, source)
            if _same_semantics(existing, node):
                return
            declared = self.declarations[node.node_id]
            kept_source = self.sources.get(node.node_id) or next(
                (path for path in declared if path != source), source
            )
            paths = tuple(sorted({kept_source, source}))
            if tolerate:
                self.conflicts.append(
                    MergeConflict(
                        node_id=node.node_id,
                        paths=paths,
                        kept_source=kept_source,
                        rejected_source=source,
                        browse_name=existing.browse_name.name,
                        node_class=existing.node_class.value,
                    )
                )
                return
            raise DuplicateNodeError(node.node_id, list(paths))
        self.nodes[node.node_id] = node
        self.sources[node.node_id] = source
        self.declarations[node.node_id] = [source]

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

    def namespace_dependencies(
        self, retained: set[ExpandedNodeId]
    ) -> dict[str, list[str]]:
        """Direct namespace uses in serialized identifiers and qualified browse names.

        Includes external targets, but neither transitive closure nor unused model-header
        requirements. Edges describe usage, including inverse references, not load order.
        """
        dependencies: dict[str, set[str]] = {}
        for node in self.iter_nodes():
            if node.node_id not in retained:
                continue
            owner = node.node_id.namespace
            used = dependencies.setdefault(owner, set())
            used.update(identifier.namespace for identifier in referenced_identifiers(node))
            if node.browse_name.namespace_uri:
                used.add(node.browse_name.namespace_uri)
            used.discard(owner)
        return {uri: sorted(used) for uri, used in sorted(dependencies.items())}


def load_nodesets(paths: list[Path | str], *, tolerate_duplicates: bool = False) -> SemanticModel:
    """Load and merge NodeSet files into one semantic model.

    ``tolerate_duplicates`` is the graceful form a release review needs: an inconsistent
    re-declaration of one identifier no longer aborts the whole run, it keeps the first
    declaration and lands in :attr:`SemanticModel.conflicts`, which the diff reports as an
    advisory finding.  A reduction does *not* use it — exporting from a model that silently
    dropped a declaration would publish a model nobody declared.
    """
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
            model.add(node, info.source_path, tolerate=tolerate_duplicates)
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
