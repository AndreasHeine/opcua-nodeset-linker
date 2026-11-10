"""Export a reduced, schema-valid UANodeSet.

The output is written in the serialization defined by OPC 10000-6: the
``http://opcfoundation.org/UA/2011/03/UANodeSet.xsd`` namespace, a ``UriTable`` without
attributes (namespace index 0 is the OPC UA namespace, the listed URIs start at 1), an
``Aliases`` table for the reference types used, ``Models`` as ``ModelTableEntry``,
``<References><Reference ReferenceType="…">target</Reference></References>``, and
``Definition``/``Field`` for datatypes.  Anything the schema does not define — a custom
``NamespaceMetadata`` element, attribute-form references, ``DefinitionType`` — is never
written, so an importer that validates against the official schema accepts the file.

NamespaceIndexes exist *only* here: they are assigned deterministically from the
namespace URIs actually used by the retained nodes — NodeIds, reference targets *and*
qualified browse names — and every identifier is rewritten to the new index.  A browse name
is qualified with the namespace of the *name*, never with the index of the node's NodeId.
Servers are re-indexed the same way through a ``ServerUris`` table.  The export refuses to
run when validation reported errors, so an invalid NodeSet can never be written.

A model declared as an *external* dependency is referenced but never shipped: its node
declarations are left out even where they were in the dependency closure, while every
identifier pointing into it survives — as a reference, as a ``DataType``, and inside a
``Definition`` — rewritten to the index the output gives that namespace.  The OPC UA
namespace is the special case: its index is always 0, so externalising it writes bare
identifiers and adds no ``Uri`` entry.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from lxml.etree import Element, SubElement, tostring
from lxml.etree import indent as xml_indent

from nodeset_linker.graph.engine import SemanticGraph
from nodeset_linker.models.identifiers import UA_NAMESPACE_URI, ExpandedNodeId, IdentifierKind
from nodeset_linker.models.namespace import NODESET_XML_NAMESPACE, ModelInfo, Namespace
from nodeset_linker.models.namespace_metadata import (
    marked_namespaces,
    metadata_object_in,
    subset_property,
    with_subset_value,
)
from nodeset_linker.models.node import (
    NODE_CLASS_BY_XML_TAG,
    Definition,
    FieldMetadata,
    Reference,
    UADataType,
    UANode,
    referenced_identifiers,
)
from nodeset_linker.models.node_class import ModellingRule, NodeClass
from nodeset_linker.repository.model import SemanticModel
from nodeset_linker.retention.engine import repair_navigation
from nodeset_linker.utils.errors import ExportError
from nodeset_linker.utils.xml import parse_fragment
from nodeset_linker.validator.semantic import SemanticValidator

if TYPE_CHECKING:  # pragma: no cover
    from nodeset_linker.resolver.closure import Closure

UA_NAMESPACE = UA_NAMESPACE_URI

# ``UAInterface`` is a node class of OPC 10000-3 but has no element in the UANodeSet
# schema; interfaces are serialized as object types, which is what the Nodeset files do.
XML_TAG_FOR_CLASS: dict[str, str] = {
    node_class.value: tag for tag, node_class in NODE_CLASS_BY_XML_TAG.items()
}
XML_TAG_FOR_CLASS[NodeClass.INTERFACE.value] = "UAObjectType"

#: Attributes every node class may carry; ``UANode`` declares them once in the schema.
COMMON_ATTRIBUTES = frozenset(
    {"WriteMask", "UserWriteMask", "AccessRestrictions", "HasNoPermissions", "ReleaseStatus"}
)

#: Attributes the schema allows per node class; anything else in a source document is
#: a project-local spelling and is not written out.
PASSTHROUGH_ATTRIBUTES: dict[str, frozenset[str]] = {
    "Object": COMMON_ATTRIBUTES | {"EventNotifier", "DesignToolOnly"},
    "Variable": COMMON_ATTRIBUTES | {
        "DesignToolOnly",
        "AccessLevel",
        "UserAccessLevel",
        "MinimumSamplingInterval",
        "Historizing",
    },
    "Method": COMMON_ATTRIBUTES | {"DesignToolOnly", "Executable", "UserExecutable"},
    "View": COMMON_ATTRIBUTES | {"DesignToolOnly", "ContainsNoLoops", "EventNotifier"},
    "ObjectType": COMMON_ATTRIBUTES,
    "VariableType": COMMON_ATTRIBUTES | {"IsAbstract"},
    "DataType": COMMON_ATTRIBUTES | {"IsAbstract", "Purpose"},
    "ReferenceType": COMMON_ATTRIBUTES | {"IsAbstract", "Symmetric"},
    "Interface": COMMON_ATTRIBUTES | {"IsAbstract"},
}

INSTANCE_CLASSES = {
    NodeClass.OBJECT,
    NodeClass.VARIABLE,
    NodeClass.METHOD,
    NodeClass.VIEW,
}
TYPE_CLASSES = {
    NodeClass.OBJECT_TYPE,
    NodeClass.VARIABLE_TYPE,
    NodeClass.DATA_TYPE,
    NodeClass.REFERENCE_TYPE,
    NodeClass.INTERFACE,
}

MODELLING_RULE_IDS = {
    ModellingRule.MANDATORY: 78,
    ModellingRule.OPTIONAL: 80,
    ModellingRule.MANDATORY_PLACEHOLDER: 462,
    ModellingRule.OPTIONAL_PLACEHOLDER: 463,
}
MODELLING_RULE_TYPE_ID = 37


class NamespaceIndexAssigner:
    """Assigns export indexes to namespace URIs deterministically.

    Index 0 is *always* the OPC UA namespace, per OPC 10000-6: an identifier without a
    ``ns=`` prefix means namespace 0, so a custom namespace may never take index 0 even
    when the OPC UA namespace itself was not retained.  The namespaces that are used are
    numbered from 1 in URI order, so the same retained set always produces the same
    document, and the ``NamespaceUris`` table starts at index 1.
    """

    def __init__(self, namespaces: dict[str, Namespace]) -> None:
        self._namespaces = namespaces
        uris = sorted(uri for uri in namespaces if uri != UA_NAMESPACE)
        self._index: dict[str, int] = {UA_NAMESPACE: 0}
        for offset, uri in enumerate(uris, start=1):
            self._index[uri] = offset

    def index_of(self, namespace_uri: str) -> int:
        if namespace_uri not in self._index:
            raise ExportError(f"namespace {namespace_uri} has no export index")
        return self._index[namespace_uri]

    def indices(self) -> dict[str, int]:
        return dict(self._index)


def _assign_ids(
    nodes: list[UANode], assigner: NamespaceIndexAssigner, servers: dict[str, int]
) -> dict[ExpandedNodeId, tuple[int, str, int | None]]:
    """Map every retained identifier to (namespace index, xml local part, server index)."""
    mapping: dict[ExpandedNodeId, tuple[int, str, int | None]] = {}
    for node in nodes:
        index = assigner.index_of(node.node_id.namespace)
        server = servers.get(node.node_id.server_uri or "")
        mapping[node.node_id] = (index, node.node_id.identifier.as_xml(), server)
    return mapping


class NodeSetExporter:
    """Serialise the retained semantic model into a schema-valid UANodeSet document."""

    def __init__(
        self,
        model: SemanticModel,
        keep: set[ExpandedNodeId],
        *,
        mark_namespace_subsets: bool = True,
        external: frozenset[str] = frozenset(),
        document_namespace: str | None = None,
    ) -> None:
        """Keep bundle-wide resolution while optionally writing only one namespace."""
        self._model = model
        self._external = frozenset(external)
        self._document_namespace = document_namespace
        # An externalized model is never shipped, even when its nodes survived the closure.
        self._keep = {node_id for node_id in keep if node_id.namespace not in self._external}
        self._subset_flags = marked_namespaces(model, self._keep)
        rewrites = self._subset_rewrites() if mark_namespace_subsets else {}
        self._nodes = [
            rewrites.get(node.node_id, node)
            for node in model.iter_nodes()
            if node.node_id in self._keep
            and (document_namespace is None or node.node_id.namespace == document_namespace)
        ]
        self._server_indices: dict[str, int] = {}
        self._dropped: list[str] = []

    @property
    def dropped(self) -> list[str]:
        """Identifiers omitted while serialising because their target is not shipped.

        The raw exporter may legitimately write a hand-picked subset with boundary links
        pruned; the safe :func:`export` refuses the write instead of losing data silently.
        """
        return list(self._dropped)

    def _record_drop(self, node: UANode, target: ExpandedNodeId, kind: str) -> None:
        self._dropped.append(f"{kind} of {node.browse_name.name} -> {target.as_xml()}")

    def _is_external(self, node_id: ExpandedNodeId) -> bool:
        return node_id.namespace in self._external

    def _subset_rewrites(self) -> dict[ExpandedNodeId, UANode]:
        """The ``IsNamespaceSubset`` properties this export has to state.

        OPC 10000-5 makes the flag a property of the namespace metadata object: the file
        header cannot say it, and inventing an attribute there would break schema
        validation.  A namespace that lost nodes is therefore marked by rewriting the
        property value — and only when that property survived the reduction.
        """
        rewrites: dict[ExpandedNodeId, UANode] = {}
        for uri, (subset, marker_present) in self._subset_flags.items():
            if not subset or not marker_present:
                continue
            obj = metadata_object_in(self._model, uri)
            if obj is None:
                continue
            property_node = subset_property(self._model, obj)
            if property_node is None or property_node.node_id not in self._keep:
                continue
            rewrites[property_node.node_id] = with_subset_value(property_node, True)
        return rewrites

    def build_document(self) -> bytes:
        used_uris = {node.node_id.namespace for node in self._nodes}
        # Every identifier this document points at, references and embedded identifiers
        # alike: an externalized model needs a namespace entry so its references resolve.
        used_uris.update(
            identifier.namespace
            for node in self._nodes
            for identifier in referenced_identifiers(node)
        )
        # A browse name is qualified against the namespace *of the name*, which may be a
        # namespace none of the retained NodeIds lives in; it still needs an export index.
        used_uris.update(
            node.browse_name.namespace_uri
            for node in self._nodes
            if node.browse_name.namespace_uri
        )
        namespaces = {uri: self._namespace(uri) for uri in used_uris}
        assigner = NamespaceIndexAssigner(namespaces)
        servers = self._server_table()
        self._server_indices = servers
        ids = _assign_ids(self._nodes, assigner, servers)

        root = Element("UANodeSet", xmlns=NODESET_XML_NAMESPACE)
        uris = SubElement(root, "NamespaceUris")
        ordered = sorted(assigner.indices().items(), key=lambda entry: entry[1])
        for uri, _index in ordered:
            if uri == UA_NAMESPACE:
                continue  # index 0 is the OPC UA namespace; official documents omit it
            SubElement(uris, "Uri").text = uri
        if not list(uris):
            root.remove(uris)  # a document that only uses namespace 0 needs no Uri table

        server_el = SubElement(root, "ServerUris")
        for server_uri in sorted(servers, key=lambda uri: servers[uri]):
            SubElement(server_el, "Uri").text = server_uri
        if not list(server_el):
            root.remove(server_el)

        models_el = SubElement(root, "Models")
        written = False
        infos: list[ModelInfo] = list(self._model.models.values())
        if self._document_namespace is not None:
            # A split document states only the model its *source* declared; a namespace
            # without a ModelTableEntry contributes no entry — inventing a version-less
            # one would claim metadata the source never stated.
            declared = self._model.models.get(self._document_namespace)
            infos = [] if declared is None else [declared]
        for info in sorted(infos, key=lambda m: m.namespace_uri):
            if info.namespace_uri not in namespaces:
                continue  # a namespace that contributed no node contributes no model entry
            if info.namespace_uri in self._external:
                continue  # an externalized model is a dependency, not part of this document
            attrs = {"ModelUri": info.namespace_uri}
            for name, value in (
                ("XmlSchemaUri", info.xml_schema_uri),
                ("Version", info.version),
                ("PublicationDate", info.publication_date),
                ("ModelVersion", info.model_version),
            ):
                if value:
                    attrs[name] = value
            model_el = SubElement(models_el, "Model", **attrs)
            written = True
            required_models = set(info.required_models)
            if self._document_namespace is not None:
                required_models.update(
                    self._model.namespace_dependencies(self._keep).get(
                        self._document_namespace, []
                    )
                )
            ordered_required = (
                info.required_models
                if self._document_namespace is None else sorted(required_models)
            )
            for required in ordered_required:
                if required not in namespaces:
                    continue
                SubElement(model_el, "RequiredModel", **self._required_attrs(info, required))
        if not written:
            root.remove(models_el)

        aliases = SubElement(root, "Aliases")
        alias_ids = self._alias_table()
        for number, name in sorted(alias_ids.items()):
            SubElement(aliases, "Alias", Alias=name).text = f"i={number}"
        if not alias_ids:
            root.remove(aliases)

        for node in sorted(self._nodes, key=lambda n: ids[n.node_id][:2]):
            root.append(self._serialise(node, ids, alias_ids, assigner))

        xml_indent(root)
        document = tostring(root, encoding="utf-8", pretty_print=True)
        return bytes(document)

    def _server_table(self) -> dict[str, int]:
        """The ``ServerUris`` table for the servers this subset references.

        OPC 10000-6 numbers the table from 1 (index 0 is the server the document was
        written for), so a qualified identifier keeps pointing at the same server after
        reduction instead of silently becoming a local one.
        """
        uris: set[str] = set()
        for node in self._nodes:
            if node.node_id.server_uri:
                uris.add(node.node_id.server_uri)
            for identifier in referenced_identifiers(node):
                if identifier.server_uri:
                    uris.add(identifier.server_uri)
        ordered = sorted(uris)
        return {uri: index for index, uri in enumerate(ordered, start=1)}

    def write(self, path: Path | str) -> Path:
        out = Path(path)
        if out.parent and not out.parent.exists():
            out.parent.mkdir(parents=True)
        out.write_bytes(self.build_document())
        return out

    def _namespace(self, uri: str) -> Namespace:
        """Declared metadata if known; otherwise the URI is still written out."""
        declared = self._model.namespaces.get(uri)
        alias = declared.alias if declared else None
        if alias is not None and alias.lower() == uri.split("/")[-1].lower():
            alias = None  # a redundant alias adds nothing to the document
        return declared or Namespace(uri=uri, alias=alias)

    def _required_attrs(self, info: ModelInfo, uri: str) -> dict[str, str]:
        """A ``ModelTableEntry`` for a dependency, with version metadata when it is known.

        A model this document does not ship is known through the loaded model or through
        the ``RequiredModel`` child that states it; ``NamespaceUris`` carries no version,
        so an unknown version is left out rather than invented.
        """
        known = self._model.models.get(uri) or next(
            (entry for entry in info.required_model_entries if entry.namespace_uri == uri),
            None,
        )
        attrs = {"ModelUri": uri}
        if known is None:
            return attrs
        for name, value in (
            ("XmlSchemaUri", known.xml_schema_uri),
            ("Version", known.version),
            ("PublicationDate", known.publication_date),
            ("ModelVersion", known.model_version),
        ):
            if value:
                attrs[name] = value
        return attrs

    def _id_of(
        self,
        node_id: ExpandedNodeId,
        ids: dict[ExpandedNodeId, tuple[int, str, int | None]],
        assigner: NamespaceIndexAssigner,
    ) -> str | None:
        """The identifier as written here: bundle nodes, externalized models, or nothing.

        An identifier pointing at an externalized model is *always* written; silently
        dropping it would leave the output without the dependency it still relies on.
        A retained target may live in a sibling document. It still uses this document's
        namespace and server indexes; only its declaration belongs in the sibling file.
        """
        mapped = ids.get(node_id)
        if mapped is not None:
            return _id_xml(*mapped)
        if self._is_external(node_id) or (
            self._document_namespace is not None and node_id in self._keep
        ):
            index = assigner.index_of(node_id.namespace)
            server = self._server_indices.get(node_id.server_uri or "")
            return _id_xml(index, node_id.identifier.as_xml(), server)
        return None

    def _alias_table(self) -> dict[int, str]:
        """Aliases for the reference types this document uses — as official files do.

        Only identifiers in the OPC UA namespace are aliased; a model's own reference
        type is written with its re-indexed identifier, which resolves inside this file.
        """
        aliases: dict[int, str] = {}
        for node in self._nodes:
            if node.modelling_rule is not ModellingRule.NO_RULE:
                aliases[MODELLING_RULE_TYPE_ID] = "HasModellingRule"
            for ref in node.references:
                reference_type = ref.reference_type
                if reference_type is None or reference_type.namespace != UA_NAMESPACE:
                    continue
                if reference_type.identifier.kind is not IdentifierKind.NUMERIC:
                    continue
                number = int(reference_type.identifier.value)
                name = ref.type_name or self._browse_name(reference_type)
                if name:
                    aliases.setdefault(number, name)
        return aliases

    def _browse_name(self, node_id: ExpandedNodeId) -> str:
        node = self._model.get(node_id)
        return node.browse_name.name if node else ""

    def _serialise(
        self,
        node: UANode,
        ids: dict[ExpandedNodeId, tuple[int, str, int | None]],
        aliases: dict[int, str],
        assigner: NamespaceIndexAssigner,
    ) -> Element:
        index, local, server = ids[node.node_id]
        element = Element(
            XML_TAG_FOR_CLASS[node.node_class.value], NodeId=_id_xml(index, local, server)
        )
        # A BrowseName is qualified with the namespace *the name belongs to*, re-indexed into
        # this document; a name in the OPC UA namespace is index 0 and is written bare, as
        # official documents do.  The node's own NodeId index is never used for a browse name.
        browse = node.browse_name
        browse_uri = browse.namespace_uri
        if browse_uri and browse_uri != UA_NAMESPACE and browse_uri in assigner.indices():
            element.set("BrowseName", f"{assigner.index_of(browse_uri)}:{browse.name}")
        elif ":" in browse.name:
            element.set("BrowseName", f"0:{browse.name}")
        else:
            element.set("BrowseName", browse.name)
        if node.symbolic_name:
            element.set("SymbolicName", node.symbolic_name)
        if node.node_class in TYPE_CLASSES and node.is_abstract:
            element.set("IsAbstract", "true")

        data_type = getattr(node, "data_type", None)
        if data_type is not None and node.node_class in {
            NodeClass.VARIABLE,
            NodeClass.VARIABLE_TYPE,
        }:
            target = self._id_of(data_type, ids, assigner)
            if target is None:
                self._record_drop(node, data_type, "data type")
            else:
                element.set("DataType", target)
        value_rank = getattr(node, "value_rank", None)
        if value_rank is not None:
            element.set("ValueRank", str(value_rank))
        array_dimensions = getattr(node, "array_dimensions", ())
        if array_dimensions:
            element.set("ArrayDimensions", ",".join(array_dimensions))

        if node.node_class in INSTANCE_CLASSES and node.parent_node_id is not None:
            parent = self._id_of(node.parent_node_id, ids, assigner)
            if parent is None:
                self._record_drop(node, node.parent_node_id, "parent")
            else:
                element.set("ParentNodeId", parent)
        if node.node_class is NodeClass.METHOD and node.method_declaration_id is not None:
            declaration = self._id_of(node.method_declaration_id, ids, assigner)
            if declaration is None:
                self._record_drop(node, node.method_declaration_id, "method declaration")
            else:
                element.set("MethodDeclarationId", declaration)

        allowed = PASSTHROUGH_ATTRIBUTES[node.node_class.value]
        for name, value in node.attributes.items():
            if name in allowed and value:
                element.set(name, value)

        if node.display_name:
            SubElement(element, "DisplayName").text = node.display_name
        if node.description:
            SubElement(element, "Description").text = node.description
        for category in node.categories:
            SubElement(element, "Category").text = category
        if node.documentation:
            SubElement(element, "Documentation").text = node.documentation

        references = self._serialise_references(node, ids, aliases, assigner)
        if references:
            references_el = SubElement(element, "References")
            for child in references:
                references_el.append(child)

        # RolePermissions and Extensions are single elements holding a list, not one element
        # per entry — the schema declares each of them with minOccurs 0 only.
        if node.role_permissions_xml:
            permissions_el = SubElement(element, "RolePermissions")
            for serialized in node.role_permissions_xml:
                permissions_el.append(parse_fragment(serialized))
        if node.extensions_xml:
            extensions_el = SubElement(element, "Extensions")
            for serialized in node.extensions_xml:
                extensions_el.append(parse_fragment(serialized))

        # ``Value`` is only legal on variables (OPC 10000-6); a stray value on another
        # node class is a source-document mistake and must not be written verbatim.
        if node.value_xml is not None and node.node_class in {
            NodeClass.VARIABLE,
            NodeClass.VARIABLE_TYPE,
        }:
            value = parse_fragment(node.value_xml)
            dependencies = dict(node.value_dependencies)
            for field in value.iter():
                if field.tag.rsplit("}", 1)[-1] not in {"TypeId", "DataType"}:
                    continue
                for identifier in field:
                    if identifier.tag.rsplit("}", 1)[-1] != "Identifier":
                        continue
                    dependency = dependencies.get((identifier.text or "").strip())
                    if dependency is not None:
                        target = self._id_of(dependency, ids, assigner)
                        if target is None:
                            raise ExportError(f"value dependency {dependency} was removed")
                        identifier.text = target
            element.append(value)
        # ``InverseName`` belongs to reference types only; on any other class it would
        # make the written document fail the schema.
        if node.inverse_name and node.node_class is NodeClass.REFERENCE_TYPE:
            SubElement(element, "InverseName").text = node.inverse_name

        if isinstance(node, UADataType) and node.definition is not None:
            self._serialise_definition(element, node, node.definition, ids, assigner)
        return element

    def _serialise_references(
        self,
        node: UANode,
        ids: dict[ExpandedNodeId, tuple[int, str, int | None]],
        aliases: dict[int, str],
        assigner: NamespaceIndexAssigner,
    ) -> list[Element]:
        children: list[Element] = []
        for ref in node.references:
            target = self._id_of(ref.target, ids, assigner)
            if target is None:
                self._record_drop(node, ref.target, "reference")
                continue
            attrs: dict[str, str] = {
                "ReferenceType": self._reference_type_xml(ref, ids, aliases, assigner)
            }
            if not ref.is_forward:
                attrs["IsForward"] = "false"
            child = Element("Reference", **attrs)
            child.text = target
            children.append(child)
        if node.modelling_rule is not ModellingRule.NO_RULE and node.modelling_rule is not None:
            rule_type = aliases.get(MODELLING_RULE_TYPE_ID, "HasModellingRule")
            child = Element("Reference", ReferenceType=rule_type)
            child.text = f"i={MODELLING_RULE_IDS[node.modelling_rule]}"
            children.append(child)
        return children

    def _reference_type_xml(
        self,
        ref: Reference,
        ids: dict[ExpandedNodeId, tuple[int, str, int | None]],
        aliases: dict[int, str],
        assigner: NamespaceIndexAssigner,
    ) -> str:
        """The ``ReferenceType`` attribute: an alias when one exists, else an identifier."""
        reference_type = ref.reference_type
        if reference_type is None:
            if not ref.type_name:
                raise ExportError(
                    f"reference to {ref.target.as_xml()} has no reference type; "
                    "nothing is invented here — declare the ReferenceType or name the reference"
                )
            return ref.type_name
        if reference_type.namespace == UA_NAMESPACE:
            if reference_type.identifier.kind is IdentifierKind.NUMERIC:
                number = int(reference_type.identifier.value)
                return aliases.get(number) or reference_type.identifier.as_xml()
            return reference_type.identifier.as_xml()
        target = self._id_of(reference_type, ids, assigner)
        if target is not None:
            return target
        return self._foreign_id_xml(reference_type)

    def _foreign_id_xml(self, node_id: ExpandedNodeId) -> str:
        """An identifier that is not part of this subset: the URI is written out, not an index.

        The namespace URI survives, so an importer can still resolve it; only the server is
        re-indexed, because ``srv=`` is a table position in *this* document.
        """
        prefix = ""
        server = self._server_indices.get(node_id.server_uri or "")
        if server is not None:
            prefix += f"srv={server};"
        if node_id.namespace_uri is not None:
            prefix += f"nsu={node_id.namespace_uri};"
        return prefix + node_id.identifier.as_xml()

    @staticmethod
    def _field_attributes(name: str, metadata: FieldMetadata, **extra: str) -> dict[str, str]:
        """``DataTypeField`` attributes, stated only where the source stated them.

        The schema gives every attribute a default, so re-writing defaults would change
        nothing but the bytes; keeping only what the source said keeps the file minimal and
        the semantics identical.
        """
        attrs = {"Name": name, **extra}
        if metadata.symbolic_name:
            attrs["SymbolicName"] = metadata.symbolic_name
        if metadata.value_rank is not None and metadata.value_rank != -1:
            attrs["ValueRank"] = str(metadata.value_rank)
        if metadata.array_dimensions:
            attrs["ArrayDimensions"] = ",".join(metadata.array_dimensions)
        if metadata.max_string_length:
            attrs["MaxStringLength"] = str(metadata.max_string_length)
        if metadata.allow_sub_types:
            attrs["AllowSubTypes"] = "true"
        return attrs

    def _serialise_definition(
        self,
        element: Element,
        node: UANode,
        definition: Definition,
        ids: dict[ExpandedNodeId, tuple[int, str, int | None]],
        assigner: NamespaceIndexAssigner,
    ) -> None:
        attrs = {"Name": definition.name or node.browse_name.name}
        if definition.symbolic_name:
            attrs["SymbolicName"] = definition.symbolic_name
        if definition.kind == "Union":
            attrs["IsUnion"] = "true"
        if definition.kind == "OptionSet":
            attrs["IsOptionSet"] = "true"
        def_el = SubElement(element, "Definition", **attrs)

        for field in definition.fields:
            target = self._id_of(field.data_type, ids, assigner)
            if target is None:
                continue
            field_attrs = self._field_attributes(
                field.name, field.metadata, DataType=target
            )
            if field.is_optional:
                field_attrs["IsOptional"] = "true"
            field_el = SubElement(def_el, "Field", **field_attrs)
            if field.display_name:
                SubElement(field_el, "DisplayName").text = field.display_name
            if field.description:
                SubElement(field_el, "Description").text = field.description
        for enum_value in definition.enum_values:
            value_el = SubElement(
                def_el,
                "Field",
                **NodeSetExporter._field_attributes(
                    enum_value.name, enum_value.metadata, Value=str(enum_value.value)
                ),
            )
            if enum_value.display_name:
                SubElement(value_el, "DisplayName").text = enum_value.display_name
            if enum_value.description:
                SubElement(value_el, "Description").text = enum_value.description
        for alternative in definition.union_alternatives:
            target = self._id_of(alternative.data_type, ids, assigner)
            if target is None:
                continue
            alternative_el = SubElement(
                def_el,
                "Field",
                **self._field_attributes(
                    alternative.name, alternative.metadata, DataType=target
                ),
            )
            if alternative.display_name:
                SubElement(alternative_el, "DisplayName").text = alternative.display_name
            if alternative.description:
                SubElement(alternative_el, "Description").text = alternative.description
        for option_value in definition.option_set_values:
            value_el = SubElement(
                def_el,
                "Field",
                **NodeSetExporter._field_attributes(
                    option_value.name, option_value.metadata, Value=str(option_value.value)
                ),
            )
            if option_value.display_name:
                SubElement(value_el, "DisplayName").text = option_value.display_name
            if option_value.description:
                SubElement(value_el, "Description").text = option_value.description


def _id_xml(index: int, local: str, server_index: int | None = None) -> str:
    """Namespace 0 is written without a prefix, exactly as official NodeSets do.

    ``srv=<index>`` is a position in this document's ``ServerUris`` table, so it is written
    whenever the identifier is qualified against another server.
    """
    prefix = f"srv={server_index};" if server_index else ""
    return prefix + local if index == 0 else f"{prefix}ns={index};{local}"


def export(
    model: SemanticModel,
    keep: set[ExpandedNodeId],
    path: Path | str,
    *,
    validate: bool = True,
    mark_namespace_subsets: bool = True,
    external: frozenset[str] = frozenset(),
) -> Path:
    """Validate then write the reduced NodeSet; validation failure blocks export.

    ``external`` declares the models whose declarations this file does not ship.  Their
    nodes are never written, but every identifier pointing at them is, and validation only
    accepts targets in those namespaces.

    ``validate=False`` skips the semantic pass, but identifiers whose targets are not
    shipped still refuse the write — skipping validation must never become silent data
    loss in the written file.
    """
    declared = frozenset(external)
    shipped = {node_id for node_id in keep if node_id.namespace not in declared}
    subset = _subset_model(model, shipped)
    if validate:
        report = SemanticValidator(subset, source=model, external=declared).validate()
        if not report.ok:
            raise ExportError(f"export blocked: {report.summary()}")
    exporter = NodeSetExporter(
        model, shipped, mark_namespace_subsets=mark_namespace_subsets, external=declared
    )
    exporter.build_document()
    if exporter.dropped:
        raise ExportError(
            "export blocked: identifiers cannot be written: " + "; ".join(exporter.dropped)
        )
    return exporter.write(path)


def export_closure(
    model: SemanticModel, closure: Closure, path: Path | str, external: frozenset[str] = frozenset()
) -> Path:
    """Export exactly the nodes of a dependency closure."""
    keep = {node_id for node_id in closure.ids() if node_id.namespace not in external}
    prepared = repair_navigation(
        model, SemanticGraph.from_model(model), keep, external,
        navigation_only=closure.navigation_only,
    )
    return export(prepared, keep, path, external=external)


def _subset_model(
    model: SemanticModel, keep: set[ExpandedNodeId], external: frozenset[str] = frozenset()
) -> SemanticModel:
    """A model containing only the nodes this document ships — what validation checks."""
    subset = SemanticModel(
        namespaces=dict(model.namespaces),
        documents=list(model.documents),
        models=dict(model.models),
    )
    subset.nodes = {
        node_id: node
        for node_id, node in model.nodes.items()
        if node_id in keep and node_id.namespace not in external
    }
    return subset
