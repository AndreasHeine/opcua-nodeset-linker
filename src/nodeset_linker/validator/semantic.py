"""Semantic validation of the reduced information model.

Validation runs on domain objects, never on XML: it checks that the *semantic* model is
correct after reduction.  Any error blocks export — an invalid NodeSet is never emitted.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from nodeset_linker.graph.engine import forward_subtype_declarations
from nodeset_linker.models.identifiers import UA_NAMESPACE_URI, ExpandedNodeId
from nodeset_linker.models.namespace_metadata import marked_namespaces
from nodeset_linker.models.node import UADataType, UANode, referenced_identifiers
from nodeset_linker.models.node_class import NodeClass, NodeRole
from nodeset_linker.repository.model import SemanticModel
from nodeset_linker.utils.errors import ValidationError

#: Reference types that legitimately have no base of their own (OPC 10000-3 clause 6.2).
REFERENCE_TYPE_ROOTS = frozenset(
    {"References", "NonHierarchicalReferences", "HierarchicalReferences", "HasReferenceType"}
)

#: Standard encoding objects accepted as HasEncoding targets (OPC 10000-6).
DEFAULT_ENCODING_NAMES = frozenset({"Default Binary", "Default XML", "Default JSON"})

#: Namespace URIs / ModelUris whose declarations the document deliberately does not ship.
ExternalSet = frozenset[str]


@dataclass(slots=True)
class Finding:
    """One validation finding."""

    code: str
    message: str
    node_id: ExpandedNodeId | None = None

    def __str__(self) -> str:
        where = f" [{self.node_id}]" if self.node_id else ""
        return f"{self.code}: {self.message}{where}"


@dataclass(slots=True)
class ValidationReport:
    """Findings split into errors (block export) and warnings."""

    errors: list[Finding] = field(default_factory=list)
    warnings: list[Finding] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors

    def summary(self) -> str:
        return f"{len(self.errors)} error(s), {len(self.warnings)} warning(s)"


class SemanticValidator:
    """Checks the semantic invariants of a reduced model.

    ``source`` is the *unreduced* model.  With it, validation can also judge what the
    reduction did to whole namespaces (subset marking) instead of only what survived.

    ``external`` names the models whose declarations the document deliberately omits.
    A reference may then point outside this document — but *only* into a model listed
    here, and never into a namespace that was simply dropped by a retention rule.
    """

    def __init__(
        self,
        model: SemanticModel,
        source: SemanticModel | None = None,
        external: ExternalSet = frozenset(),
    ) -> None:
        self._model = model
        self._source = source
        self._external = frozenset(external)
        self._forward_bases = forward_subtype_declarations(model)

    def validate(self) -> ValidationReport:
        report = ValidationReport()
        self._check_reference_targets(report)
        self._check_reference_type_declarations(report)
        self._check_type_definitions(report)
        self._check_datatype_references(report)
        self._check_reference_type_hierarchy(report)
        self._check_subtype_hierarchy(report)
        self._check_encoding_references(report)
        self._check_namespace_links(report)
        self._check_namespace_subset_markers(report)
        self._check_external_dependencies(report)
        return report

    def _is_external(self, node_id: ExpandedNodeId) -> bool:
        """Whether an identifier lives in a model this document does not ship."""
        return node_id.namespace in self._external

    def _resolve(self, node_id: ExpandedNodeId) -> UANode | None:
        """A node of the reduced model, or of the source when the target is external.

        An externalized model that *was* loaded can still be checked: a datatype field
        pointing at it must point at a datatype, even though the declaration is not in
        the output.
        """
        node = self._model.get(node_id)
        if node is None and self._source is not None and self._is_external(node_id):
            return self._source.get(node_id)
        return node

    def _check_reference_targets(self, report: ValidationReport) -> None:
        for node in self._model.iter_nodes():
            for ref in node.references:
                if ref.target in self._model.nodes:
                    continue
                if self._is_external(ref.target):
                    continue  # declared external: the consumer supplies that model
                report.errors.append(
                    Finding(
                        "REF_TARGET_MISSING",
                        f"reference {ref.type_name} points to unknown node {ref.target}",
                        node.node_id,
                    )
                )
            for _, target in node.value_dependencies:
                if target not in self._model.nodes and not self._is_external(target):
                    report.errors.append(
                        Finding("VALUE_DEPENDENCY_MISSING", f"value needs {target}", node.node_id)
                    )
            if (
                node.method_declaration_id is not None
                and self._resolve(node.method_declaration_id) is None
                and not self._is_external(node.method_declaration_id)
            ):
                report.errors.append(
                    Finding(
                        "METHOD_DECLARATION_MISSING",
                        f"method declaration {node.method_declaration_id} was removed",
                        node.node_id,
                    )
                )

    def _check_reference_type_declarations(self, report: ValidationReport) -> None:
        """A written reference must resolve to a reference type the importer can find.

        Types of the OPC UA namespace come with the base namespace every importer loads,
        so they need no declaration here.  A model's own reference type does: a file that
        uses ``ConnectsTo`` but drops its declaration cannot be imported on its own.
        """
        for node in self._model.iter_nodes():
            for ref in node.references:
                if ref.reference_type is None:
                    if ref.type_name:
                        report.warnings.append(
                            Finding(
                                "REFERENCE_TYPE_UNKNOWN",
                                f"reference {ref.type_name} has no resolvable type identifier",
                                node.node_id,
                            )
                        )
                    continue
                if ref.reference_type.namespace == UA_NAMESPACE_URI:
                    continue
                if ref.reference_type not in self._model.nodes:
                    if self._is_external(ref.reference_type):
                        continue  # the type comes with the model that is externalized
                    report.errors.append(
                        Finding(
                            "REFERENCE_TYPE_MISSING",
                            f"reference {ref.type_name or ref.reference_type} needs its type "
                            f"{ref.reference_type}, which was removed",
                            node.node_id,
                        )
                    )

    def _check_type_definitions(self, report: ValidationReport) -> None:
        for node in self._model.iter_nodes():
            definition = node.type_definition
            if definition is None:
                continue
            target = self._resolve(definition)
            if target is None:
                continue  # reported by REF_TARGET_MISSING, or an external target we cannot inspect
            expected: NodeClass | None = None
            match node.node_class:
                case NodeClass.OBJECT:
                    expected = NodeClass.OBJECT_TYPE
                case NodeClass.VARIABLE:
                    expected = NodeClass.VARIABLE_TYPE
                case NodeClass.METHOD:
                    expected = NodeClass.METHOD
                case NodeClass.OBJECT_TYPE | NodeClass.VARIABLE_TYPE:
                    expected = None
            if expected is not None and target.node_class is not expected:
                report.errors.append(
                    Finding(
                        "TYPEDEF_CLASS_MISMATCH",
                        f"{node.browse_name.name} declares type definition "
                        f"{target.browse_name.name} of class {target.node_class.value}, "
                        f"expected {expected.value}",
                        node.node_id,
                    )
                )

    def _check_datatype_references(self, report: ValidationReport) -> None:
        for node in self._model.iter_nodes():
            data_type = getattr(node, "data_type", None)
            if data_type is not None:
                target = self._resolve(data_type)
                if target is None:
                    if self._is_external(data_type):
                        continue  # the datatype comes with the externalized model
                    report.errors.append(
                        Finding(
                            "DATATYPE_MISSING",
                            f"datatype {data_type} is not in the model",
                            node.node_id,
                        )
                    )
                    continue
                if target.node_class is not NodeClass.DATA_TYPE:
                    report.errors.append(
                        Finding(
                            "DATATYPE_CLASS_MISMATCH",
                            f"datatype reference {data_type} resolves to a "
                            f"{target.node_class.value} node",
                            node.node_id,
                        )
                    )

            definition = getattr(node, "definition", None)
            if isinstance(node, UADataType) and definition is not None:
                for field in definition.fields:
                    if field.data_type not in self._model.nodes and not self._is_external(
                        field.data_type
                    ):
                        report.errors.append(
                            Finding(
                                "STRUCT_FIELD_DATATYPE_MISSING",
                                f"field {field.name} of {node.browse_name.name} "
                                f"needs datatype {field.data_type}",
                                node.node_id,
                            )
                        )
                for alternative in definition.union_alternatives:
                    if (
                        alternative.data_type not in self._model.nodes
                        and not self._is_external(alternative.data_type)
                    ):
                        report.errors.append(
                            Finding(
                                "UNION_ALTERNATIVE_DATATYPE_MISSING",
                                f"alternative {alternative.name} of {node.browse_name.name} "
                                f"needs datatype {alternative.data_type}",
                                node.node_id,
                            )
                        )

    def _check_reference_type_hierarchy(self, report: ValidationReport) -> None:
        for node in self._model.iter_nodes():
            if node.node_class is not NodeClass.REFERENCE_TYPE:
                continue
            bases = self._bases_of(node)
            for parent in bases:
                target = self._resolve(parent)
                if target is not None and target.node_class is not NodeClass.REFERENCE_TYPE:
                    report.errors.append(
                        Finding(
                            "REFTYPE_BASE_CLASS_MISMATCH",
                            f"reference type {node.browse_name.name} "
                            f"has non-reference-type base {parent}",
                            node.node_id,
                        )
                    )
            if not bases and node.browse_name.name not in REFERENCE_TYPE_ROOTS:
                report.errors.append(
                    Finding(
                        "REFTYPE_NO_BASE",
                        f"reference type {node.browse_name.name} has no HasSubtype base",
                        node.node_id,
                    )
                )

    def _bases_of(self, node: UANode) -> list[ExpandedNodeId]:
        """The bases a node names, in the direction official documents write them.

        A subtype states its base with a *reversed* ``HasSubtype`` reference
        (``IsForward="false"`), because the forward direction runs from base to subtype.
        A forward reference is declared by the base and names a subtype instead.
        """
        bases = [
            ref.target for ref in node.references
            if ref.type_name == "HasSubtype" and not ref.is_forward
        ]
        bases.extend(self._forward_bases.get(node.node_id, []))
        return list(dict.fromkeys(bases))

    def _check_subtype_hierarchy(self, report: ValidationReport) -> None:
        for node in self._model.iter_nodes():
            if not node.node_class.is_type:
                continue
            bases = self._bases_of(node)
            if node.node_class is NodeClass.INTERFACE and not bases:
                continue  # interfaces are standalone by definition
            for base in bases:
                target = self._resolve(base)
                if target is None:
                    if self._is_external(base):
                        continue  # the base comes with the externalized model
                    report.errors.append(
                        Finding(
                            "SUBTYPE_BASE_MISSING",
                            f"{node.browse_name.name} inherits {base}, which was removed",
                            node.node_id,
                        )
                    )
                    continue
                if target.node_class is not node.node_class:
                    report.errors.append(
                        Finding(
                            "SUBTYPE_CLASS_MISMATCH",
                            f"{node.browse_name.name} ({node.node_class.value}) inherits "
                            f"{target.browse_name.name} ({target.node_class.value})",
                            node.node_id,
                        )
                    )
            if self._has_subtype_cycle(node):
                report.errors.append(
                    Finding(
                        "SUBTYPE_CYCLE",
                        f"inheritance cycle through {node.browse_name.name}",
                        node.node_id,
                    )
                )

    def _has_subtype_cycle(self, start: UANode) -> bool:
        # Inheritance is a graph, not a chain: a node may declare several bases (and
        # bases can be declared from either side), so a cycle may hide behind *any* of
        # them.  A shared "visited" set keeps diamonds linear; only a node on the
        # current path means a cycle.
        visited: set[ExpandedNodeId] = set()

        def walk(node_id: ExpandedNodeId, stack: set[ExpandedNodeId]) -> bool:
            if node_id in stack:
                return True
            if node_id in visited:
                return False
            visited.add(node_id)
            node = self._resolve(node_id)
            if node is None:
                return False
            stack.add(node_id)
            cyclic = any(walk(base, stack) for base in self._bases_of(node))
            stack.discard(node_id)
            return cyclic

        return walk(start.node_id, set())

    def _check_encoding_references(self, report: ValidationReport) -> None:
        for node in self._model.iter_nodes():
            if not isinstance(node, UADataType) or node.role is NodeRole.ENCODING:
                continue
            encodings = [ref.target for ref in node.references if ref.type_name == "HasEncoding"]
            for encoding_id in encodings:
                encoding = self._resolve(encoding_id)
                if encoding is None:
                    if self._is_external(encoding_id):
                        continue  # the encoding comes with the externalized model
                    report.errors.append(
                        Finding(
                            "ENCODING_MISSING",
                            f"{node.browse_name.name} declares encoding {encoding_id}, "
                            "which was removed",
                            node.node_id,
                        )
                    )
                    continue
                if not isinstance(encoding, UADataType):
                    # Newer documents point HasEncoding at the standard Default Binary /
                    # Default XML / Default JSON objects instead of a datatype per type.
                    is_standard_object = (
                        encoding.node_class is NodeClass.OBJECT
                        and encoding.browse_name.name in DEFAULT_ENCODING_NAMES
                    )
                    if not is_standard_object:
                        report.errors.append(
                            Finding(
                                "ENCODING_CLASS_MISMATCH",
                                f"encoding {encoding_id} of {node.browse_name.name} "
                                "is not an Encoding node",
                                node.node_id,
                            )
                        )
                    continue
                if not encoding.is_encoding:
                    report.errors.append(
                        Finding(
                            "ENCODING_CLASS_MISMATCH",
                            f"encoding {encoding_id} of {node.browse_name.name} "
                            "is not an Encoding node",
                            node.node_id,
                        )
                    )

    def _check_namespace_links(self, report: ValidationReport) -> None:
        used = self._model.referenced_namespaces()
        declared = set(self._model.namespaces)
        for uri in sorted(used - declared - self._external):
            report.warnings.append(
                Finding("NAMESPACE_NOT_DECLARED", f"namespace {uri} is used but not declared")
            )
        for info in self._model.models.values():
            for required in info.required_models:
                if required not in self._model.models:
                    wording = (
                        "which is externalized and not loaded — the importing application "
                        "has to make it available"
                        if required in self._external
                        else "which is not loaded"
                    )
                    report.warnings.append(
                        Finding(
                            "REQUIRED_MODEL_MISSING",
                            f"model {info.namespace_uri} requires {required}, {wording}",
                        )
                    )

    def _check_external_dependencies(self, report: ValidationReport) -> None:
        """What the document states about the models it does not ship.

        Externalizing a model is a promise to the consumer, so validation reports the
        promise instead of implying the dependency came along in the file: the namespace
        must actually be referenced, the document should name it as a ``RequiredModel``,
        and what is known about its version is stated — otherwise this is reported as
        missing metadata rather than silently treated as bundled.
        """
        if not self._external:
            return
        stated = {uri for info in self._model.models.values() for uri in info.required_models}
        source = self._source or self._model
        referenced: set[str] = set()
        for node in self._model.iter_nodes():
            referenced.update(identifier.namespace for identifier in referenced_identifiers(node))
        for uri in sorted(self._external):
            if uri not in referenced:
                report.warnings.append(
                    Finding(
                        "EXTERNAL_MODEL_UNUSED",
                        f"external model {uri} is declared but nothing in this document "
                        "references it",
                    )
                )
                continue
            if uri not in stated:
                report.warnings.append(
                    Finding(
                        "EXTERNAL_MODEL_NOT_DECLARED",
                        f"namespace {uri} is referenced and externalized, but no "
                        "RequiredModel entry states the dependency; the file only carries "
                        "the URI in NamespaceUris",
                    )
                )
            if not any(node.node_id.namespace == uri for node in source.iter_nodes()):
                report.warnings.append(
                    Finding(
                        "EXTERNAL_MODEL_NOT_LOADED",
                        f"external model {uri} was not loaded, so its declarations could not "
                        "be checked and its nodes are relied on from the consumer",
                    )
                )
            version, _date = self._known_metadata_for(uri)
            if version is None:
                report.warnings.append(
                    Finding(
                        "EXTERNAL_MODEL_VERSION_UNKNOWN",
                        f"external model {uri} has no known Version, so the output states the "
                        "dependency without version metadata",
                    )
                )

    def _known_metadata_for(self, uri: str) -> tuple[str | None, str | None]:
        """The version and publication date this document knows about a model, if any.

        A model whose declarations are not in the file is known through the loaded model
        or through the ``RequiredModel`` child that states it — a plain ``NamespaceUris``
        entry carries no version information in ``UANodeSet.xsd``.
        """
        info = self._model.models.get(uri)
        if info is not None and info.version:
            return info.version, info.publication_date
        for model in self._model.models.values():
            for entry in model.required_model_entries:
                if entry.namespace_uri == uri and entry.version:
                    return entry.version, entry.publication_date
        namespace = self._model.namespaces.get(uri)
        if namespace is not None and namespace.version:
            return namespace.version, namespace.publication_date
        return None, None

    def _check_namespace_subset_markers(self, report: ValidationReport) -> None:
        """A namespace that lost nodes must say so — but only if it can.

        ``IsNamespaceSubset`` is a property of the namespace metadata object, so a
        reduction that drops that object leaves the flag unstated.  That is not an
        invalid file, but the export would silently look like a complete namespace.
        """
        if self._source is None:
            return
        for uri, (subset, marker_present) in sorted(
            marked_namespaces(self._source, set(self._model.nodes)).items()
        ):
            if subset and not marker_present:
                report.warnings.append(
                    Finding(
                        "NAMESPACE_METADATA_MISSING",
                        f"namespace {uri} was reduced but its NamespaceMetadata object, "
                        "and with it IsNamespaceSubset, was dropped",
                    )
                )

    def assert_valid(self) -> ValidationReport:
        report = self.validate()
        if not report.ok:
            raise ValidationError(
                f"reduced model is semantically invalid: {report.summary()}", report
            )
        return report


def declared_external_namespaces(model: SemanticModel) -> ExternalSet:
    """The models a document depends on but declares no node declarations for.

    A ``RequiredModel`` entry states a dependency the file itself does not ship, so its
    namespace may legitimately hold reference targets.  Nothing outside such a declared
    dependency may be referenced: an identifier that points at a namespace neither
    declared here nor required stays an error.
    """
    shipped = {node_id.namespace for node_id in model.nodes}
    required = {uri for info in model.models.values() for uri in info.required_models}
    return frozenset(uri for uri in required if uri not in shipped)


def validate_model(
    model: SemanticModel, external: ExternalSet = frozenset()
) -> ValidationReport:
    """Validate a semantic model (used by CLI and tests).

    ``external`` is the set of models the document deliberately does not ship; the CLI
    derives it from the file itself with :func:`declared_external_namespaces`.
    """
    return SemanticValidator(model, external=external).validate()
