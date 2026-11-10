"""Hardened XML parsing — the one way this project reads XML.

lxml's default parser resolves entity definitions and loads DTDs, so a malicious
NodeSet can expand an entity bomb into memory before any validation gets a chance
to stop it.  The parser here keeps entity declarations unresolved, refuses external
DTDs and network fetches, and caps the file size before parsing.  Every reader —
the NodeSet parser, the CLI schema check and the official-nodeset script — goes
through :func:`parse_xml` so the hardening cannot be bypassed by a new call site.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from lxml.etree import XMLParser, XMLSyntaxError, fromstring, parse

from nodeset_linker.utils.errors import ParseError

#: Largest file accepted as a NodeSet or schema.  The biggest official nodeset is
#: well under this; anything larger is treated as hostile input, not as a model.
MAX_XML_BYTES = 64 * 1024 * 1024

#: Entities stay unresolved, no DTD is loaded or validated, nothing fetches the network.
SECURE_PARSER = XMLParser(
    resolve_entities=False, load_dtd=False, dtd_validation=False, no_network=True
)


def parse_xml(path: Path | str) -> Any:
    """Parse ``path`` with :data:`SECURE_PARSER`; raise ``ParseError`` above the size cap."""
    file_path = Path(path)
    try:
        size = file_path.stat().st_size
    except OSError as exc:
        raise ParseError(f"{path}: {exc}") from exc
    if size > MAX_XML_BYTES:
        raise ParseError(
            f"{path}: {size} bytes exceed the {MAX_XML_BYTES} byte limit for XML documents"
        )
    try:
        return parse(str(file_path), parser=SECURE_PARSER)
    except XMLSyntaxError as exc:
        raise ParseError(f"{path}: {exc}") from exc


def parse_fragment(text: str) -> Any:
    """Parse a serialized XML fragment with :data:`SECURE_PARSER`."""
    try:
        return fromstring(text, parser=SECURE_PARSER)
    except XMLSyntaxError as exc:
        raise ParseError(f"invalid XML fragment: {exc}") from exc
