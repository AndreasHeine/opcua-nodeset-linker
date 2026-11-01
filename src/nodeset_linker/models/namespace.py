"""Namespace-level metadata of a loaded information model."""

from __future__ import annotations

from dataclasses import dataclass

from nodeset_linker.models.identifiers import UA_NAMESPACE_URI

#: The XML namespace the NodeSet serialization is defined in (OPC 10000-6,
#: ``UANodeSet.xsd``).  Documents written in the older ``http://opcfoundation.org/UA/``
#: namespace are still read, but only this one is written.
NODESET_XML_NAMESPACE = "http://opcfoundation.org/UA/2011/03/UANodeSet.xsd"


@dataclass(frozen=True, slots=True)
class Namespace:
    """A namespace as declared by a NodeSet (URI, alias, index *in that document*)."""

    uri: str
    alias: str | None = None
    index_in_document: int | None = None
    version: str | None = None
    publication_date: str | None = None
    xml_schema_uri: str | None = None
    model_version: str | None = None


@dataclass(frozen=True, slots=True)
class ModelInfo:
    """A ``Model`` declaration (a *ModelTableEntry* of the UANodeSet schema)."""

    namespace_uri: str
    model_name: str = ""
    xml_schema_uri: str | None = None
    version: str | None = None
    publication_date: str | None = None
    model_version: str | None = None
    required_models: tuple[str, ...] = ()
    """Namespace URIs of the models this model depends on."""

    required_model_entries: tuple[ModelInfo, ...] = ()
    """The ``RequiredModel`` children as written, including their version metadata.

    A dependency may point at a model this document does not declare any nodes for, so
    the child entry — not a loaded model — is the only place its version claim lives.
    """


@dataclass(frozen=True, slots=True)
class DocumentInfo:
    """Non-node metadata of one source document."""

    source_path: str
    namespaces: tuple[Namespace, ...] = ()
    models: tuple[ModelInfo, ...] = ()
    server_uris: tuple[str, ...] = ()
    """The ``ServerUris`` table: entry *n* (1-based) is the ``ServerIndex`` of ``srv=<n>``."""


def alias_from_uri(uri: str) -> str | None:
    """Derive a readable alias (``Machinery``) from a namespace URI."""
    tail = uri.rstrip("/").split("/")[-1]
    return tail or None


def is_ua_namespace(uri: str) -> bool:
    return uri == UA_NAMESPACE_URI
