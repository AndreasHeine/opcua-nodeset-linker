"""Turn UANodeSet XML into the semantic domain model.

XML is treated strictly as a serialization format: tags are read once, converted to
domain objects, and never consulted again.  ``NamespaceIndex`` attributes are resolved
to namespace URIs immediately, so identity survives document merging and reduction.

The reader accepts the schema-conformant serialization defined by OPC 10000-6 — the
``http://opcfoundation.org/UA/2011/03/UANodeSet.xsd`` namespace, an ``Aliases`` table,
``<References><Reference ReferenceType="HasProperty">i=46</Reference></References>``,
``Definition``/``Field``, ``Models`` as ``ModelTableEntry`` — plus the older attribute
form this project used before (``TargetId``/``TypeId`` attributes, ``DefinitionType``).
Files written by this linker are schema-valid, so they parse back with the same reader.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any, Self

from lxml.etree import XMLSyntaxError, _Element, tostring
from lxml.etree import parse as xml_parse

from nodeset_linker.models.identifiers import ExpandedNodeId, IdentifierKind, NodeId, QualifiedName
from nodeset_linker.models.namespace import (
    DocumentInfo,
    ModelInfo,
    Namespace,
    alias_from_uri,
)
from nodeset_linker.models.namespace_metadata import NAMESPACE_METADATA_TYPE_ID
from nodeset_linker.models.node import (
    NODE_CLASS_BY_XML_TAG,
    Definition,
    EnumValue,
    FieldMetadata,
    OptionSetValue,
    Reference,
    StructureField,
    UADataType,
    UAInterface,
    UAMethod,
    UANode,
    UAObjectType,
    UAReferenceType,
    UAVariable,
    UAVariableType,
    UAView,
    UnionAlternative,
    is_placeholder,
)
from nodeset_linker.models.node_class import ModellingRule, NodeClass, NodeRole
from nodeset_linker.models.standard import STANDARD_DATA_TYPE_IDS as CANONICAL_DATA_TYPE_IDS
from nodeset_linker.utils.errors import ParseError

UA_NAMESPACE = "http://opcfoundation.org/UA/"

REFERENCE_TYPE_NODE_ID = 37

# Modelling rule *nodes* of the OPC UA namespace (OPC 10000-8 Annex B).  290/387 were the
# ids this project used before; they stay accepted as a legacy spelling of the same rules.
MANDATORY_RULE_IDS = {"78": ModellingRule.MANDATORY, "462": ModellingRule.MANDATORY_PLACEHOLDER}
OPTIONAL_RULE_IDS = {"80": ModellingRule.OPTIONAL, "463": ModellingRule.OPTIONAL_PLACEHOLDER}
LEGACY_RULE_IDS = {"290": ModellingRule.MANDATORY, "387": ModellingRule.OPTIONAL}
RULE_IDS: Mapping[str, ModellingRule] = {
    **MANDATORY_RULE_IDS,
    **OPTIONAL_RULE_IDS,
    **LEGACY_RULE_IDS,
}

#: Standard reference types, so alias-less documents can still be classified.
STANDARD_REFERENCE_TYPE_IDS: Mapping[str, int] = {
    "HasChild": 34,
    "Organizes": 35,
    "HasEventSource": 36,
    "HasModellingRule": 37,
    "HasEncoding": 38,
    "HasDescription": 39,
    "HasTypeDefinition": 40,
    "GeneratesEvent": 41,
    "Aggregates": 44,
    "HasSubtype": 45,
    "HasProperty": 46,
    "HasComponent": 47,
    "HasNotifier": 48,
    "HasOrderedComponent": 49,
    "FromState": 51,
    "ToState": 52,
    "HasCause": 53,
    "HasEffect": 54,
    "HasHistoricalConfiguration": 56,
    "HasSubStateMachine": 117,
    "HasArgumentDescription": 129,
    "HasOptionalInputArgumentDescription": 131,
    "AlwaysGeneratesEvent": 3065,
    "HasTrueSubState": 9004,
    "HasFalseSubState": 9005,
    "HasCondition": 9006,
    "HasGuard": 15112,
    "HasDictionaryEntry": 17597,
    "HasInterface": 17603,
    "HasAddIn": 17604,
}

#: Standard datatypes, so ``DataType="String"`` works without an ``Aliases`` table.
STANDARD_DATA_TYPE_IDS: Mapping[str, int] = {
    **CANONICAL_DATA_TYPE_IDS,
    # Legacy input spellings remain accepted, but are never canonical display names.
    "DataType": 25,
    "Enum": 29,
    "Message": 332,
}


def _local(tag: object) -> str:
    """Strip the XML namespace, so both namespace spellings of a UANodeSet read alike.

    Comments and processing instructions carry a callable tag, not a string; they match
    nothing and are skipped.
    """
    if not isinstance(tag, str):
        return ""
    return tag.rsplit("}", 1)[-1]


#: Names that mark an encoding node: a datatype ``…Encoding`` or a standard
#: ``Default Binary``/``Default XML``/``Default JSON`` object.
ENCODING_NAME_SUFFIXES = ("Encoding", "Binary", "Xml", "XML", "Json", "JSON")


def _is_encoding_name(*names: str | None) -> bool:
    """Whether any of a node's names spells an encoding."""
    return any(
        name is not None and name.endswith(suffix)
        for name in names
        for suffix in ENCODING_NAME_SUFFIXES
    )


def _children(element: _Element, name: str) -> list[_Element]:
    return [child for child in element if _local(child.tag) == name]


def _child(element: _Element, name: str) -> _Element | None:
    for child in element:
        if _local(child.tag) == name:
            return child
    return None


def ua_number(number: int) -> ExpandedNodeId:
    return ExpandedNodeId.local(NodeId.numeric_id(number), UA_NAMESPACE)


class NamespaceResolver:
    """Maps document-local namespace and server indexes onto URIs."""

    def __init__(self, namespaces: Sequence[Namespace], server_uris: Sequence[str] = ()) -> None:
        self._by_index: dict[int, str] = {
            ns.index_in_document: ns.uri for ns in namespaces if ns.index_in_document is not None
        }
        self._servers: tuple[str, ...] = tuple(server_uris)

    def uri_for_index(self, index: int) -> str | None:
        if index == 0:
            return UA_NAMESPACE  # index 0 is the OPC UA namespace in every document
        return self._by_index.get(index)

    def server_uri_for_index(self, index: int) -> str | None:
        """``srv=0`` means the server the document was written for: no qualification."""
        if index == 0:
            return None
        if 1 <= index <= len(self._servers):
            return self._servers[index - 1]
        return None

    def resolve(self, text: str, default_namespace: str = UA_NAMESPACE) -> ExpandedNodeId:
        """Resolve an XML identifier string to identity.

        An identifier without a namespace prefix belongs to the OPC UA namespace: that is
        how official NodeSets refer to their bases (``i=49``, ``ParentNodeId="i=2004"``).
        An ``ns=<index>`` or ``srv=<index>`` that the document did not declare is an error,
        never a silent fallback to namespace 0 — guessing would point the identifier at the
        wrong namespace and make the reduced file claim a node that does not exist.
        """
        namespace_uri: str | None = None
        server_uri: str | None = None
        local_parts: list[str] = []
        for part in text.split(";"):
            if part.startswith("nsu="):
                namespace_uri = part[4:]
            elif part.startswith("ns="):
                raw = part[3:]
                if not raw.isdigit():
                    raise ParseError(f"identifier {text!r} has a non-numeric ns= index")
                resolved = self.uri_for_index(int(raw))
                if resolved is None:
                    raise ParseError(
                        f"identifier {text!r} uses namespace index {raw}, which this document"
                        " does not declare in NamespaceUris"
                    )
                namespace_uri = resolved
            elif part.startswith("srv="):
                raw = part[4:]
                if raw.isdigit():
                    resolved_server = self.server_uri_for_index(int(raw))
                    if resolved_server is None:
                        raise ParseError(
                            f"identifier {text!r} uses server index {raw}, which this document"
                            " does not declare in ServerUris"
                        )
                    server_uri = resolved_server
                else:
                    server_uri = raw  # some project files spell the ApplicationUri directly
            elif part:
                local_parts.append(part)
        if not local_parts:
            raise ParseError(f"identifier without local part: {text!r}")
        return ExpandedNodeId(
            identifier=NodeId.parse(";".join(local_parts)),
            namespace_uri=namespace_uri or default_namespace,
            server_uri=server_uri,
        )


class NodeSetParser:
    """Parse one UANodeSet XML document into immutable domain objects."""

    def __init__(self) -> None:
        self.namespaces: list[Namespace] = []
        self.server_uris: tuple[str, ...] = ()
        self.models: tuple[ModelInfo, ...] = ()
        self.nodes: list[UANode] = []
        self.aliases: dict[str, ExpandedNodeId] = {}
        self.source_path: str | None = None
        self._resolver: NamespaceResolver = NamespaceResolver(())

    @classmethod
    def parse_file(cls, path: Path | str) -> Self:
        return cls().parse_path(Path(path))

    def parse_path(self, path: Path) -> Self:
        try:
            root = xml_parse(str(path)).getroot()
        except XMLSyntaxError as exc:
            raise ParseError(f"{path}: {exc}") from exc
        if _local(root.tag) != "UANodeSet":
            raise ParseError(f"{path}: root element {root.tag!r}, expected UANodeSet")

        self.source_path = str(path)
        self.namespaces = self._parse_namespaces(root)
        self.server_uris = self._parse_server_uris(root)
        self._resolver = NamespaceResolver(self.namespaces, self.server_uris)
        resolver = self._resolver
        self.aliases = self._parse_aliases(root, resolver)
        self.models = self._parse_models(root)
        self._attach_model_version_info()
        self.nodes = [
            self._parse_node(el, resolver)
            for el in root
            if _local(el.tag) in NODE_CLASS_BY_XML_TAG
        ]
        return self

    def document_info(self) -> DocumentInfo:
        if self.source_path is None:
            raise ParseError("parser has not parsed a document yet")
        return DocumentInfo(
            source_path=self.source_path,
            namespaces=tuple(self.namespaces),
            models=self.models,
            server_uris=self.server_uris,
        )

    def _parse_namespaces(self, root: _Element) -> list[Namespace]:
        """Read ``NamespaceUris``.

        Index 0 is always the OPC UA namespace: a document that does not list it gets it
        implicitly, and a listed URI table starts at index 1.  Documents that spell out
        ``UriIndex`` (the older form) keep their explicit indexes.
        """
        uri_el = _child(root, "NamespaceUris")
        entries: list[Namespace] = []
        if uri_el is not None:
            for position, child in enumerate(uri_el):
                if _local(child.tag) != "Uri":
                    continue
                uri = (child.text or "").strip()
                raw_index = child.get("UriIndex")
                index = (
                    int(raw_index)
                    if raw_index is not None
                    else (position if position == 0 and uri == UA_NAMESPACE else position + 1)
                )
                entries.append(
                    Namespace(uri=uri, alias=child.get("Alias"), index_in_document=index)
                )
        if not any(entry.uri == UA_NAMESPACE for entry in entries):
            entries.insert(0, Namespace(uri=UA_NAMESPACE, index_in_document=0))
        return entries

    def _parse_server_uris(self, root: _Element) -> tuple[str, ...]:
        """Read ``ServerUris``.

        OPC 10000-6 states that the ``ServerIndex`` of an ``ExpandedNodeId`` identifies an
        element of this table and that the first index is 1; index 0 is the server the
        document itself was written for, so it needs no entry.
        """
        table = _child(root, "ServerUris")
        if table is None:
            return ()
        entries = [(child.text or "").strip() for child in _children(table, "Uri")]
        return tuple(text for text in entries if text)

    def _parse_aliases(
        self, root: _Element, resolver: NamespaceResolver
    ) -> dict[str, ExpandedNodeId]:
        aliases: dict[str, ExpandedNodeId] = {}
        table = _child(root, "Aliases")
        if table is None:
            return aliases
        for alias_el in _children(table, "Alias"):
            name = alias_el.get("Alias")
            text = (alias_el.text or "").strip()
            if not name or not text:
                continue
            aliases[name] = resolver.resolve(text)
        return aliases

    def _parse_models(self, root: _Element) -> tuple[ModelInfo, ...]:
        """Read ``Models`` as the ``ModelTableEntry`` list defined by OPC 10000-6."""
        infos: list[ModelInfo] = []
        models_el = _child(root, "Models")
        if models_el is not None:
            for model in _children(models_el, "Model"):
                uri = model.get("ModelUri") or ""
                if not uri:
                    legacy = _child(model, "NamespaceUri")
                    if legacy is not None:
                        uri = (legacy.text or "").strip()
                    else:
                        uri = model.get("NamespaceUri") or ""
                if not uri.startswith("http"):
                    name = model.get("ModelName") or ""
                    raise ParseError(
                        f"Model {name!r} has no ModelUri/NamespaceUri URI: {uri!r}"
                    )
                name = model.get("ModelName") or alias_from_uri(uri) or ""
                entries: list[ModelInfo] = []
                for entry in _children(model, "RequiredModel"):
                    entry_uri = entry.get("ModelUri") or (entry.text or "").strip()
                    if not entry_uri:
                        continue
                    entries.append(
                        ModelInfo(
                            namespace_uri=entry_uri,
                            model_name=alias_from_uri(entry_uri) or "",
                            xml_schema_uri=entry.get("XmlSchemaUri"),
                            version=entry.get("Version"),
                            publication_date=entry.get("PublicationDate"),
                            model_version=entry.get("ModelVersion"),
                        )
                    )
                for entry in _children(model, "RequiredModels"):
                    # A pre-schema spelling that listed the URIs as text.
                    entry_uri = (entry.text or "").strip()
                    if entry_uri:
                        entries.append(
                            ModelInfo(
                                namespace_uri=entry_uri,
                                model_name=alias_from_uri(entry_uri) or "",
                            )
                        )
                infos.append(
                    ModelInfo(
                        namespace_uri=uri,
                        model_name=name,
                        xml_schema_uri=model.get("XmlSchemaUri"),
                        version=model.get("Version"),
                        publication_date=model.get("PublicationDate"),
                        model_version=model.get("ModelVersion"),
                        required_models=tuple(entry.namespace_uri for entry in entries),
                        required_model_entries=tuple(entries),
                    )
                )
        for metadata in _children(root, "NamespaceMetadata"):
            # A pre-schema spelling some projects used to state a namespace version.
            uri = metadata.get("Uri") or ""
            if not uri.startswith("http"):
                continue
            infos.append(
                ModelInfo(
                    namespace_uri=uri,
                    model_name=alias_from_uri(uri) or "",
                    version=metadata.get("Version"),
                    publication_date=metadata.get("PublicationDate"),
                )
            )
        return tuple(infos)

    def _attach_model_version_info(self) -> None:
        """Version information comes from the ``ModelTableEntry`` (OPC 10000-6/5)."""
        by_uri = {info.namespace_uri: info for info in self.models}
        updated: list[Namespace] = []
        for namespace in self.namespaces:
            info = by_uri.get(namespace.uri)
            updated.append(
                Namespace(
                    uri=namespace.uri,
                    alias=namespace.alias or (info.model_name if info else None),
                    index_in_document=namespace.index_in_document,
                    version=namespace.version or (info.version if info else None),
                    publication_date=(
                        namespace.publication_date
                        or (info.publication_date if info else None)
                    ),
                    xml_schema_uri=(
                        namespace.xml_schema_uri or (info.xml_schema_uri if info else None)
                    ),
                    model_version=namespace.model_version or (info.model_version if info else None),
                )
            )
        self.namespaces = updated

    def _parse_node(self, element: _Element, resolver: NamespaceResolver) -> UANode:
        tag = _local(element.tag)
        if tag not in NODE_CLASS_BY_XML_TAG:
            raise ParseError(f"unsupported element {tag!r}")
        node_class = NODE_CLASS_BY_XML_TAG[tag]
        node_id = resolver.resolve(element.get("NodeId") or "")
        browse_name = self._parse_browse_name(element.get("BrowseName") or "", resolver)
        references, modelling_rule = self._parse_references(element, resolver)
        definition = (
            self._parse_definition(element) if node_class is NodeClass.DATA_TYPE else None
        )
        type_definition = next(
            (ref.target for ref in references if ref.type_name == "HasTypeDefinition"), None
        )
        role = self._derive_role(
            node_class, references, browse_name, element, definition, type_definition
        )

        common: dict[str, Any] = {
            "node_id": node_id,
            "browse_name": browse_name,
            "node_class": node_class,
            "display_name": self._text(_child(element, "DisplayName")),
            "description": self._text(_child(element, "Description")),
            "references": references,
            "modelling_rule": modelling_rule,
            "is_abstract": (element.get("IsAbstract") or "false").lower() == "true",
            "attributes": {key: (value or "") for key, value in element.attrib.items()},
            "symbolic_name": element.get("SymbolicName"),
            "parent_node_id": self._optional_id(element.get("ParentNodeId")),
            "method_declaration_id": self._optional_id(element.get("MethodDeclarationId")),
            "categories": tuple(
                text for text in (self._text(el) for el in _children(element, "Category")) if text
            ),
            "documentation": self._text(_child(element, "Documentation")),
            "value_xml": self._serialize(_child(element, "Value")),
            "value_dependencies": self._value_dependencies(_child(element, "Value")),
            "extensions_xml": self._serialized_children(element, "Extensions", "Extension"),
            "role_permissions_xml": self._serialized_children(
                element, "RolePermissions", "RolePermission"
            ),
            "inverse_name": self._text(_child(element, "InverseName")),
        }
        match node_class:
            case NodeClass.DATA_TYPE:
                return UADataType(
                    role=role,
                    definition=definition,
                    is_encoding=role is NodeRole.ENCODING,
                    **common,
                )
            case NodeClass.VARIABLE:
                return UAVariable(role=role, **self._variable_kwargs(element), **common)
            case NodeClass.VARIABLE_TYPE:
                return UAVariableType(role=role, **self._variable_kwargs(element), **common)
            case NodeClass.OBJECT_TYPE:
                return UAObjectType(role=role, **common)
            case NodeClass.REFERENCE_TYPE:
                return UAReferenceType(role=role, **common)
            case NodeClass.INTERFACE:
                return UAInterface(role=role, **common)
            case NodeClass.VIEW:
                return UAView(role=role, **common)
            case NodeClass.METHOD:
                return UAMethod(role=role, **common)
            case NodeClass.OBJECT:
                return UANode(role=role, **common)

    def _parse_browse_name(self, text: str, resolver: NamespaceResolver) -> QualifiedName:
        """Resolve a ``BrowseName`` onto the namespace its index points at.

        A qualified name ``1:Foo`` names *Foo inside the namespace at index 1*; the index is
        table position, so it only means something inside this document.  Resolving it now
        keeps the name attached to the URI across a merge, and the exporter writes the index
        of the *export* table instead of guessing it from the node's own NodeId.
        """
        head, sep, name = text.partition(":")
        if sep and head.isdigit():
            index = int(head)
            uri = resolver.uri_for_index(index)
            if uri is None:
                raise ParseError(
                    f"BrowseName {text!r} uses namespace index {head},"
                    " which this document does not declare in NamespaceUris"
                )
            return QualifiedName(name=name, namespace_index=index, namespace_uri=uri)
        return QualifiedName(name=text)

    def _parse_references(
        self, element: _Element, resolver: NamespaceResolver
    ) -> tuple[tuple[Reference, ...], ModellingRule]:
        """Read references plus the modelling rule carried as a ``HasModellingRule``.

        The OPC UA serialization writes every reference as
        ``<References><Reference ReferenceType="…">target</Reference></References>``.  A
        modelling rule is the same construct pointing at a rule node; it becomes a node
        *attribute* here, so it never creates a graph edge.
        """
        refs: list[Reference] = []
        rule = ModellingRule.NO_RULE
        for refs_el in _children(element, "References"):
            if refs_el.get("TargetId") or refs_el.get("TypeId") or refs_el.get("ReferenceType"):
                refs.append(self._legacy_reference(refs_el, resolver))
                continue
            for ref_el in _children(refs_el, "Reference"):
                raw_type = ref_el.get("ReferenceType") or ""
                target_text = (ref_el.text or "").strip()
                if not raw_type:
                    raise ParseError(
                        f"Reference without ReferenceType/TypeId: target {target_text!r}"
                    )
                if not target_text:
                    raise ParseError(f"Reference {raw_type!r} without target")
                reference_type, type_name = self._reference_type(raw_type, None, resolver)
                target = resolver.resolve(target_text)
                parsed_rule = self._modelling_rule(reference_type, type_name, target)
                if parsed_rule is not None:
                    rule = parsed_rule
                    continue
                refs.append(
                    Reference(
                        target=target,
                        reference_type=reference_type,
                        type_name=type_name,
                        is_forward=(ref_el.get("IsForward") or "true").lower() != "false",
                    )
                )
        legacy_rule_el = _child(element, "ModellingRule")
        if legacy_rule_el is not None:
            rule_id = resolver.resolve(legacy_rule_el.get("Id") or "").identifier.value
            rule = RULE_IDS.get(rule_id, ModellingRule.NO_RULE)
        return tuple(refs), rule

    def _legacy_reference(self, element: _Element, resolver: NamespaceResolver) -> Reference:
        """The pre-schema attribute form: attributes instead of element content."""
        raw_type = element.get("ReferenceType") or ""
        type_id = element.get("TypeId")
        if type_id is None and not raw_type:
            raise ParseError(f"reference without ReferenceType/TypeId: {element.get('TargetId')!r}")
        reference_type, type_name = self._reference_type(raw_type, type_id, resolver)
        target_text = element.get("TargetId") or ""
        if not target_text:
            raise ParseError(f"reference {type_name or raw_type!r} without TargetId")
        return Reference(
            target=resolver.resolve(target_text),
            reference_type=reference_type,
            type_name=type_name,
            is_forward=(element.get("IsForward") or "true").lower() != "false",
        )

    def _reference_type(
        self, raw_type: str, type_id: str | None, resolver: NamespaceResolver
    ) -> tuple[ExpandedNodeId | None, str]:
        """Resolve a reference type onto its identifier and browse name.

        XML names may be aliases from the document table, plain browse names, or an
        identifier.  Numeric ids without a name are completed after merging, when the
        reference type declaration itself is known.
        """
        node_id: ExpandedNodeId | None = resolver.resolve(type_id) if type_id else None
        name = ""
        if raw_type:
            aliased = self.aliases.get(raw_type)
            if aliased is not None:
                node_id = aliased
                name = raw_type
            elif "=" in raw_type:
                node_id = node_id or resolver.resolve(raw_type)
            else:
                name = raw_type
                number = STANDARD_REFERENCE_TYPE_IDS.get(raw_type)
                if number is not None:
                    node_id = ua_number(number)
        if node_id is None and name:
            number = STANDARD_REFERENCE_TYPE_IDS.get(name)
            node_id = ua_number(number) if number is not None else None
        if node_id is not None:
            if node_id.identifier.kind is IdentifierKind.STRING:
                number = STANDARD_REFERENCE_TYPE_IDS.get(node_id.identifier.value)
                if number is not None:
                    node_id = ua_number(number)
                elif not name:
                    name = node_id.identifier.value
            elif not name:
                number = node_id.identifier.numeric
                if number is not None:
                    name = _name_of_standard_reference(number)
        return node_id, name

    def _modelling_rule(
        self,
        reference_type: ExpandedNodeId | None,
        type_name: str,
        target: ExpandedNodeId,
    ) -> ModellingRule | None:
        """A ``HasModellingRule`` reference is a node attribute, never a graph edge."""
        if type_name != "HasModellingRule" and not (
            reference_type is not None
            and reference_type.identifier.numeric == REFERENCE_TYPE_NODE_ID
        ):
            return None
        return RULE_IDS.get(target.identifier.value)

    def _optional_id(self, raw: str | None) -> ExpandedNodeId | None:
        return self._resolve_id(raw) if raw else None

    def _resolve_id(self, raw: str | None) -> ExpandedNodeId | None:
        """Resolve an identifier that may be an alias name (``DataType="String"``)."""
        if not raw:
            return None
        if "=" in raw:
            return self._resolver.resolve(raw)
        aliased = self.aliases.get(raw)
        if aliased is not None:
            return aliased
        number = STANDARD_DATA_TYPE_IDS.get(raw) or STANDARD_REFERENCE_TYPE_IDS.get(raw)
        return ua_number(number) if number is not None else None

    def _serialized_children(self, element: _Element, wrapper: str, child: str) -> tuple[str, ...]:
        """Serialise opaque child elements (extensions, role permissions) verbatim."""
        parent = _child(element, wrapper)
        if parent is None:
            return ()
        return tuple(
            serialized
            for serialized in (self._serialize(el) for el in _children(parent, child))
            if serialized
        )

    def _variable_kwargs(self, element: _Element) -> dict[str, Any]:
        rank_raw = element.get("ValueRank")
        dims_raw = element.get("ArrayDimensions")
        return {
            "data_type": self._resolve_id(element.get("DataType")),
            "value_rank": int(rank_raw) if rank_raw is not None else None,
            "array_dimensions": tuple(part for part in (dims_raw or "").split(",") if part),
        }

    def _derive_role(
        self,
        node_class: NodeClass,
        references: tuple[Reference, ...],
        browse_name: QualifiedName,
        element: _Element,
        definition: Definition | None,
        type_definition: ExpandedNodeId | None,
    ) -> NodeRole:
        if is_placeholder(browse_name.name):
            return NodeRole.PLACEHOLDER
        if (
            type_definition is not None
            and type_definition.identifier.numeric == NAMESPACE_METADATA_TYPE_ID
        ):
            return NodeRole.NAMESPACE_METADATA
        if (element.get("SymbolicName") or "").endswith("NamespaceMetadata"):
            return NodeRole.NAMESPACE_METADATA
        if browse_name.name.startswith("UA_NamespaceMetadata"):
            return NodeRole.NAMESPACE_METADATA
        if browse_name.name in {"InputArguments", "OutputArguments"}:
            return NodeRole.ARGUMENT
        if node_class in {NodeClass.DATA_TYPE, NodeClass.OBJECT} and _is_encoding_name(
            browse_name.name,
            element.get("SymbolicName"),
            self._text(_child(element, "DisplayName")),
        ):
            # Encoding nodes come in two spellings: a datatype named "…Encoding" and the
            # standard "Default Binary"/"Default XML" objects newer files point at.
            return NodeRole.ENCODING
        if node_class is NodeClass.DATA_TYPE:
            if browse_name.name.endswith("Encoding"):
                return NodeRole.ENCODING
            if definition is not None:
                return {
                    "Structure": NodeRole.STRUCTURE,
                    "Enum": NodeRole.ENUM,
                    "Union": NodeRole.UNION,
                    "OptionSet": NodeRole.OPTION_SET,
                }.get(definition.kind, NodeRole.INSTANCE)
            if element.get("IsAbstract") == "true" or browse_name.name.endswith(
                ("Type", "Enumeration")
            ):
                return NodeRole.BASE_TYPE
            return NodeRole.INSTANCE
        if node_class is NodeClass.REFERENCE_TYPE:
            if element.get("IsAbstract") == "true":
                return NodeRole.ABSTRACT_TYPE
            return NodeRole.BASE_TYPE
        if node_class in {NodeClass.OBJECT_TYPE, NodeClass.VARIABLE_TYPE}:
            if element.get("IsAbstract") == "true":
                return NodeRole.ABSTRACT_TYPE
            return NodeRole.TYPE_DEFINITION
        if node_class is NodeClass.INTERFACE:
            return NodeRole.INTERFACE
        if node_class is NodeClass.METHOD:
            return NodeRole.METHOD
        if node_class is NodeClass.VIEW:
            return NodeRole.VIEW
        return NodeRole.INSTANCE

    def _parse_definition(self, element: _Element) -> Definition | None:
        """Read a datatype definition in both the schema form and the older spelling.

        The schema form is ``<Definition Name="…" IsUnion IsOptionSet><Field …/></Definition>``:
        enum values are fields with a ``Value``, structure fields carry a ``DataType``.
        """
        def_el = _child(element, "Definition")
        if def_el is None:
            return None
        fields_el = _children(def_el, "Field")
        legacy_kind = def_el.get("DefinitionType")
        is_union = (def_el.get("IsUnion") or "false").lower() == "true"
        is_option_set = (def_el.get("IsOptionSet") or "false").lower() == "true"
        enum_only = bool(fields_el) and all(
            el.get("Value") is not None and el.get("DataType") is None for el in fields_el
        )
        kind = legacy_kind or (
            "OptionSet"
            if is_option_set
            else "Union" if is_union else "Enum" if enum_only else "Structure"
        )
        fields = tuple(
            StructureField(
                name=el.get("Name") or "",
                data_type=self._resolve_id(el.get("DataType")) or ua_number(24),
                description=self._text(_child(el, "Description")),
                display_name=self._text(_child(el, "DisplayName")),
                is_optional=(el.get("IsOptional") or "false").lower() == "true",
                metadata=self._field_metadata(el),
            )
            for el in fields_el
            if el.get("DataType") is not None or kind in {"Structure", "Union"}
        )
        enum_values = (
            tuple(
                EnumValue(
                    name=el.get("Name") or "",
                    value=int(el.get("Value") or 0),
                    description=self._text(_child(el, "Description")),
                    display_name=self._text(_child(el, "DisplayName")),
                    metadata=self._field_metadata(el),
                )
                for el in (
                    _children(def_el, "EnumeratedValues")
                    if legacy_kind == "Enum"
                    else [el for el in fields_el if el.get("Value") is not None]
                )
            )
            if kind == "Enum"
            else ()
        )
        option_values = (
            tuple(
                OptionSetValue(
                    name=el.get("Name") or "",
                    value=int(el.get("Value") or 0),
                    description=self._text(_child(el, "Description")),
                    display_name=self._text(_child(el, "DisplayName")),
                    metadata=self._field_metadata(el),
                )
                for el in (
                    _children(def_el, "Values")
                    if legacy_kind == "OptionSet"
                    else [el for el in fields_el if el.get("Value") is not None]
                )
            )
            if kind == "OptionSet"
            else ()
        )
        alternatives = (
            tuple(
                UnionAlternative(
                    name=el.get("Name") or "",
                    data_type=self._resolve_id(el.get("DataType")) or ua_number(24),
                    description=self._text(_child(el, "Description")),
                    display_name=self._text(_child(el, "DisplayName")),
                    metadata=self._field_metadata(el),
                )
                for el in (
                    _children(def_el, "Alternatives")
                    if legacy_kind == "Union"
                    else [el for el in fields_el if el.get("DataType") is not None]
                )
            )
            if kind == "Union"
            else ()
        )
        return Definition(
            kind=kind,
            fields=fields,
            enum_values=enum_values,
            union_alternatives=alternatives,
            option_set_values=option_values,
            name=def_el.get("Name") or None,
            symbolic_name=def_el.get("SymbolicName") or None,
        )

    def _value_dependencies(
        self, value: _Element | None
    ) -> tuple[tuple[str, ExpandedNodeId], ...]:
        dependencies: dict[str, ExpandedNodeId] = {}
        if value is not None:
            for element in value.iter():
                if _local(element.tag) not in {"TypeId", "DataType"}:
                    continue
                identifier = _child(element, "Identifier")
                text = self._text(identifier)
                if text is not None:
                    resolved = self._resolve_id(text)
                    if resolved is not None:
                        dependencies[text] = resolved
        return tuple(dependencies.items())

    def _field_metadata(self, element: _Element) -> FieldMetadata:
        """The optional ``DataTypeField`` attributes, kept as they were stated.

        Schema defaults are not recorded: a field without ``ValueRank`` is a scalar, and
        writing ``ValueRank="-1"`` back would add noise without adding meaning.
        """
        rank_raw = element.get("ValueRank")
        dims_raw = element.get("ArrayDimensions")
        max_raw = element.get("MaxStringLength")
        return FieldMetadata(
            symbolic_name=element.get("SymbolicName"),
            value_rank=int(rank_raw) if rank_raw is not None else None,
            array_dimensions=tuple(part for part in (dims_raw or "").split(",") if part),
            max_string_length=int(max_raw) if max_raw is not None else None,
            allow_sub_types=(element.get("AllowSubTypes") or "false").lower() == "true",
        )

    @staticmethod
    def _text(element: _Element | None) -> str | None:
        if element is None:
            return None
        text = str(element.text or "")
        return text.strip() if text.strip() else None

    @staticmethod
    def _serialize(element: _Element | None) -> str | None:
        """Keep an opaque XML fragment verbatim — values and extensions are data, not tags."""
        if element is None:
            return None
        serialized = tostring(element)
        return str(serialized.decode("utf-8"))


def _name_of_standard_reference(number: int) -> str:
    for name, identifier in STANDARD_REFERENCE_TYPE_IDS.items():
        if identifier == number:
            return name
    return ""


def parse_nodeset(path: Path | str) -> tuple[list[UANode], DocumentInfo]:
    """Parse a NodeSet file and return its nodes plus document metadata."""
    parser = NodeSetParser.parse_file(path)
    return parser.nodes, parser.document_info()
