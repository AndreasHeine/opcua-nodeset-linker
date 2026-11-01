"""Semantic dependency categories carried on graph edges."""

from __future__ import annotations

from enum import Enum


class MandatoryLevel(Enum):
    """How strongly a dependency is required by OPC UA semantics."""

    MANDATORY = "mandatory"
    OPTIONAL = "optional"


class DependencyKind(Enum):
    """Categories of semantic dependency; they drive retention, reporting and
    (later) conformance rules without touching the graph engine.
    """

    INHERITANCE = "inheritance"
    STRUCTURAL = "structural"
    DATATYPE = "datatype"
    INTERFACE = "interface"
    METHOD = "method"
    ENCODING = "encoding"
    REFERENCETYPE = "referencetype"
    NAMESPACE = "namespace"
    OPTIONAL = "optional"
