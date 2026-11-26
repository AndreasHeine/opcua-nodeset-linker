"""Stable semantic identifiers.

Internal identity is *never* the ``NamespaceIndex``: an index is a property of one
particular XML document and changes when nodesets are merged or reduced.  Identity
here is ``(namespace_uri, local_part)``, which survives every merge and export.
``NamespaceIndex`` is materialised only during export (:mod:`nodeset_linker.exporter`).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Self
from urllib.parse import quote, unquote

UA_NAMESPACE_URI = "http://opcfoundation.org/UA/"
_URI_SAFE = ":/?#[]@!$&'()*+,=%"


def encode_uri(uri: str) -> str:
    """Encode a URI for a NodeId string, escaping the semicolon delimiter."""
    return quote(uri, safe=_URI_SAFE)


def decode_uri(uri: str) -> str:
    """Decode a URI from a NodeId string."""
    return unquote(uri)


class IdentifierKind(Enum):
    """How the local part of a node identifier is expressed."""

    NUMERIC = "numeric"
    STRING = "string"
    GUID = "guid"
    OPAQUE = "opaque"


_NUMERIC = re.compile(r"^i=(\d+)$")
_STRING = re.compile(r"^s=(.*)$", re.DOTALL)
_GUID = re.compile(r"^g=([0-9a-fA-F-]{36})$")
_OPAQUE = re.compile(r"^b=(.+)$")


@dataclass(frozen=True, slots=True)
class NodeId:
    """Identifier local to one namespace."""

    kind: IdentifierKind
    value: str

    @property
    def numeric(self) -> int | None:
        return int(self.value) if self.kind is IdentifierKind.NUMERIC else None

    @classmethod
    def numeric_id(cls, number: int) -> Self:
        if number < 0:
            raise ValueError("numeric NodeId must be non-negative")
        return cls(IdentifierKind.NUMERIC, str(number))

    @classmethod
    def string_id(cls, text: str) -> Self:
        return cls(IdentifierKind.STRING, text)

    @classmethod
    def guid_id(cls, guid: str) -> Self:
        return cls(IdentifierKind.GUID, guid.lower())

    @classmethod
    def parse(cls, text: str) -> Self:
        """Parse ``i=…``, ``s=…``, ``g=…`` or ``b=…``."""
        if (m := _NUMERIC.match(text)):
            return cls(IdentifierKind.NUMERIC, m.group(1))
        if (m := _STRING.match(text)):
            return cls(IdentifierKind.STRING, m.group(1))
        if (m := _GUID.match(text)):
            return cls(IdentifierKind.GUID, m.group(1).lower())
        if (m := _OPAQUE.match(text)):
            return cls(IdentifierKind.OPAQUE, m.group(1))
        raise ValueError(f"unrecognised NodeId syntax: {text!r}")

    def as_xml(self) -> str:
        """Serialise back to the OPC UA XML attribute form."""
        match self.kind:
            case IdentifierKind.NUMERIC:
                return f"i={self.value}"
            case IdentifierKind.STRING:
                return f"s={self.value}"
            case IdentifierKind.GUID:
                return f"g={self.value}"
            case IdentifierKind.OPAQUE:
                return f"b={self.value}"

    def __str__(self) -> str:
        return self.as_xml()


@dataclass(frozen=True, slots=True)
class ExpandedNodeId:
    """Namespace-qualified identifier; the internal identity of every node.

    ``namespace_uri`` is ``None`` only for identifiers that carry no namespace at
    all (rare in NodeSets; they are treated as OPC UA namespace 0 on export).
    """

    identifier: NodeId
    namespace_uri: str | None = UA_NAMESPACE_URI
    server_uri: str | None = None

    @property
    def namespace(self) -> str:
        return self.namespace_uri or UA_NAMESPACE_URI

    @classmethod
    def local(cls, identifier: NodeId, namespace_uri: str) -> Self:
        return cls(identifier=identifier, namespace_uri=namespace_uri)

    @classmethod
    def parse(cls, text: str) -> Self:
        """Parse a standard ExpandedNodeId string form.

        ``ns=<index>`` is accepted for input convenience and resolved to a URI by
        the repository; it is never stored as identity.  A bare identifier means the
        OPC UA namespace. Index-based server qualification is retained as an unresolved
        marker because resolving it requires the document's ServerUris table.
        """
        parts = text.split(";")
        namespace_uri: str | None = UA_NAMESPACE_URI
        server_uri: str | None = None
        local_parts: list[str] = []
        for part in parts:
            if part.startswith("nsu="):
                namespace_uri = decode_uri(part[4:])
            elif part.startswith("ns="):
                namespace_uri = f"index:{part[3:]}"  # marker, resolved by repository
            elif part.startswith("svr="):
                raw = part[4:]
                if not raw.isdigit():
                    raise ValueError(f"server index is not numeric: {raw!r}")
                server_uri = f"index:{raw}" if int(raw) else None
            elif part.startswith("svu="):
                server_uri = decode_uri(part[4:])
            elif part.startswith("srv="):
                # Accept the non-standard spelling emitted by older linker versions.
                raw = part[4:]
                server_uri = f"index:{raw}" if raw.isdigit() else decode_uri(raw)
            else:
                local_parts.append(part)
        if not local_parts:
            raise ValueError(f"ExpandedNodeId has no local part: {text!r}")
        return cls(
            identifier=NodeId.parse(";".join(local_parts)),
            namespace_uri=namespace_uri,
            server_uri=server_uri,
        )

    def as_xml(self) -> str:
        prefix = ""
        if self.server_uri is not None:
            if self.server_uri.startswith("index:"):
                prefix += f"svr={self.server_uri[6:]};"
            else:
                prefix += f"svu={encode_uri(self.server_uri)};"
        if self.namespace_uri is not None and self.namespace_uri != UA_NAMESPACE_URI:
            prefix += f"nsu={encode_uri(self.namespace_uri)};"
        return prefix + self.identifier.as_xml()

    def __str__(self) -> str:
        return self.as_xml()


@dataclass(frozen=True, slots=True)
class QualifiedName:
    """Browse name: a namespace-local name plus the namespace it belongs to.

    ``namespace_index`` is the index as written in the source document; a parser resolves
    it against that document's ``NamespaceUris`` table and stores the URI in
    ``namespace_uri``.  Identity and matching use the name only; the *URI* — never the
    index — decides how a name is qualified when a document is written, because indexes
    change when nodesets are merged or reduced.
    """

    name: str
    namespace_index: int | None = field(default=None)
    namespace_uri: str | None = field(default=None)

    @property
    def is_qualified(self) -> bool:
        """Whether the name belongs to a namespace of its own (not the OPC UA one)."""
        return self.namespace_uri is not None and self.namespace_uri != UA_NAMESPACE_URI

    def __str__(self) -> str:
        # Qualified by the namespace *URI*, never the source-document index: indexes
        # change when nodesets are merged or reduced, the URI does not.  A name in the
        # OPC UA namespace (or with no namespace at all) spells bare.
        if self.is_qualified and self.namespace_uri is not None:
            return f"{self.namespace_uri}:{self.name}"
        return self.name
