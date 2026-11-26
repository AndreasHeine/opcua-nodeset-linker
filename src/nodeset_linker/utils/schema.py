"""Runtime validation against the bundled OPC Foundation UANodeSet schema."""

from __future__ import annotations

from functools import lru_cache
from importlib.resources import as_file, files

from lxml.etree import XMLSchema

from nodeset_linker.utils.errors import ExportError
from nodeset_linker.utils.xml import parse_fragment, parse_xml


@lru_cache(maxsize=1)
def _nodeset_schema() -> XMLSchema:
    resource = files("nodeset_linker").joinpath("data", "UANodeSet.xsd")
    with as_file(resource) as path:
        return XMLSchema(parse_xml(path))


def validate_nodeset_document(document: bytes) -> None:
    """Raise when serialized output is not valid against the bundled NodeSet XSD."""
    root = parse_fragment(document.decode("utf-8"))
    schema = _nodeset_schema()
    if not schema.validate(root):
        errors = "; ".join(error.message for error in schema.error_log)
        raise ExportError(f"serialized NodeSet is not schema-valid: {errors}")
