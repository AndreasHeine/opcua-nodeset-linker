"""Domain objects for OPC UA nodes — immutable, strongly typed.

The XML document is a serialization format, not modelling truth.  Everything the
system reasons about lives in these objects.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from types import MappingProxyType
from typing import Self

from nodeset_linker.models.dependency import MandatoryLevel
from nodeset_linker.models.identifiers import ExpandedNodeId, QualifiedName
from nodeset_linker.models.node_class import ModellingRule, NodeClass, NodeRole

_EMPTY_ATTRIBUTES: Mapping[str, str] = MappingProxyType({})


@dataclass(frozen=True, slots=True)
class Reference:
    """A semantic reference between two nodes.

    ``is_forward`` describes the direction as written in the source document; the
    graph engine normalises inverted references so traversal is direction-independent.

    ``reference_type`` is the *identifier* of the reference type; ``type_name`` is its
    browse name (the alias used in XML).  Either side may be missing in a source
    document — an alias-less file gives only names, a numeric id gives only ids — and
    the repository fills the missing side once all documents are merged.
    """

    target: ExpandedNodeId
    reference_type: ExpandedNodeId | None = None
    type_name: str = ""
    is_forward: bool = True


@dataclass(frozen=True, slots=True)
class FieldMetadata:
    """The optional attributes a ``DataTypeField`` may carry (OPC 10000-6, *DataTypeField*).

    All of them default to the schema default, so a field that does not state them keeps
    the meaning it had before: ``ValueRank`` -1 (scalar), ``ArrayDimensions`` empty,
    ``MaxStringLength`` 0 (unbounded), ``AllowSubTypes`` false.
    """

    symbolic_name: str | None = None
    value_rank: int | None = None
    array_dimensions: tuple[str, ...] = ()
    max_string_length: int | None = None
    allow_sub_types: bool = False


@dataclass(frozen=True, slots=True)
class StructureField:
    """One field of a ``StructureDefinition``."""

    name: str
    data_type: ExpandedNodeId
    description: str | None = None
    display_name: str | None = None
    is_optional: bool = False
    metadata: FieldMetadata = field(default_factory=FieldMetadata)


@dataclass(frozen=True, slots=True)
class EnumValue:
    """One value of an ``EnumDefinition``."""

    name: str
    value: int
    description: str | None = None
    display_name: str | None = None
    metadata: FieldMetadata = field(default_factory=FieldMetadata)


@dataclass(frozen=True, slots=True)
class UnionAlternative:
    """One alternative of a ``UnionDefinition``."""

    name: str
    data_type: ExpandedNodeId
    description: str | None = None
    display_name: str | None = None
    metadata: FieldMetadata = field(default_factory=FieldMetadata)


@dataclass(frozen=True, slots=True)
class OptionSetValue:
    """One bit value of an ``OptionSetDefinition``."""

    name: str
    value: int
    description: str | None = None
    display_name: str | None = None
    metadata: FieldMetadata = field(default_factory=FieldMetadata)


@dataclass(frozen=True, slots=True)
class Definition:
    """The structured definition attached to a data type node."""

    kind: str
    fields: tuple[StructureField, ...] = ()
    enum_values: tuple[EnumValue, ...] = ()
    union_alternatives: tuple[UnionAlternative, ...] = ()
    option_set_values: tuple[OptionSetValue, ...] = ()
    name: str | None = None
    symbolic_name: str | None = None


@dataclass(frozen=True, slots=True)
class UANode:
    """Base representation of any OPC UA node."""

    node_id: ExpandedNodeId
    browse_name: QualifiedName
    node_class: NodeClass
    role: NodeRole
    display_name: str | None = None
    description: str | None = None
    references: tuple[Reference, ...] = ()
    modelling_rule: ModellingRule = ModellingRule.NO_RULE
    is_abstract: bool = False
    attributes: Mapping[str, str] = _EMPTY_ATTRIBUTES
    symbolic_name: str | None = None
    parent_node_id: ExpandedNodeId | None = None
    method_declaration_id: ExpandedNodeId | None = None
    categories: tuple[str, ...] = ()
    documentation: str | None = None
    value_xml: str | None = None
    """Serialised ``<Value>`` element; kept verbatim so values survive a reduction."""
    extensions_xml: tuple[str, ...] = ()
    role_permissions_xml: tuple[str, ...] = ()
    inverse_name: str | None = None

    @property
    def type_definition(self) -> ExpandedNodeId | None:
        for ref in self.references:
            if ref.type_name == "HasTypeDefinition":
                return ref.target
        return None

    def reference_of(self, type_name: str) -> tuple[ExpandedNodeId, ...]:
        return tuple(r.target for r in self.references if r.type_name == type_name)

    def with_references(self, references: tuple[Reference, ...]) -> Self:
        return replace(self, references=references)

    def with_value(self, value_xml: str | None) -> Self:
        """A copy carrying a different serialised ``<Value>`` element."""
        return replace(self, value_xml=value_xml)


@dataclass(frozen=True, slots=True)
class UAObject(UANode):
    """``UAObject`` instance."""

    def __init__(self, **kwargs: object) -> None:
        kwargs.setdefault("node_class", NodeClass.OBJECT)
        kwargs.setdefault("role", NodeRole.INSTANCE)
        super(UAObject, self).__init__(**kwargs)  # type: ignore[arg-type]


@dataclass(frozen=True, slots=True)
class UAVariable(UANode):
    """``UAVariable`` instance; carries its semantic datatype."""

    data_type: ExpandedNodeId | None = field(default=None, kw_only=True)
    value_rank: int | None = field(default=None, kw_only=True)
    array_dimensions: tuple[str, ...] = field(default=(), kw_only=True)

    @property
    def is_argument(self) -> bool:
        return self.role is NodeRole.ARGUMENT


@dataclass(frozen=True, slots=True)
class UAMethod(UANode):
    """``UAMethod`` instance."""

    def __init__(self, **kwargs: object) -> None:
        kwargs.setdefault("node_class", NodeClass.METHOD)
        kwargs.setdefault("role", NodeRole.METHOD)
        super(UAMethod, self).__init__(**kwargs)  # type: ignore[arg-type]


@dataclass(frozen=True, slots=True)
class UAObjectType(UANode):
    """``UAObjectType`` — a type definition of an object."""

    def __init__(self, **kwargs: object) -> None:
        kwargs.setdefault("node_class", NodeClass.OBJECT_TYPE)
        kwargs.setdefault("role", NodeRole.TYPE_DEFINITION)
        super(UAObjectType, self).__init__(**kwargs)  # type: ignore[arg-type]


@dataclass(frozen=True, slots=True)
class UAVariableType(UANode):
    """``UAVariableType`` — a type definition of a variable."""

    data_type: ExpandedNodeId | None = field(default=None, kw_only=True)
    value_rank: int | None = field(default=None, kw_only=True)
    array_dimensions: tuple[str, ...] = field(default=(), kw_only=True)


@dataclass(frozen=True, slots=True)
class UADataType(UANode):
    """``UADataType`` — including abstract base types and structured types."""

    definition: Definition | None = field(default=None, kw_only=True)
    is_encoding: bool = field(default=False, kw_only=True)


@dataclass(frozen=True, slots=True)
class UAReferenceType(UANode):
    """``UAReferenceType`` — standard or custom reference type."""

    def __init__(self, **kwargs: object) -> None:
        kwargs.setdefault("node_class", NodeClass.REFERENCE_TYPE)
        kwargs.setdefault("role", NodeRole.BASE_TYPE)
        super(UAReferenceType, self).__init__(**kwargs)  # type: ignore[arg-type]


@dataclass(frozen=True, slots=True)
class UAView(UANode):
    """``UAView`` — an organised subset of the address space."""

    def __init__(self, **kwargs: object) -> None:
        kwargs.setdefault("node_class", NodeClass.VIEW)
        kwargs.setdefault("role", NodeRole.VIEW)
        super(UAView, self).__init__(**kwargs)  # type: ignore[arg-type]


@dataclass(frozen=True, slots=True)
class UAInterface(UANode):
    """``UAInterface`` — an interface type (OPC UA Part 3 interfaces)."""

    def __init__(self, **kwargs: object) -> None:
        kwargs.setdefault("node_class", NodeClass.INTERFACE)
        kwargs.setdefault("role", NodeRole.INTERFACE)
        super(UAInterface, self).__init__(**kwargs)  # type: ignore[arg-type]


@dataclass(frozen=True, slots=True)
class NamespaceMetadata(UANode):
    """Namespace metadata node (``UA_NamespaceMetadata_*``)."""

    namespace_uri: str = field(default="", kw_only=True)
    version: str | None = field(default=None, kw_only=True)
    publication_date: str | None = field(default=None, kw_only=True)


NODE_CLASS_BY_XML_TAG: Mapping[str, NodeClass] = MappingProxyType(
    {
        "UAObject": NodeClass.OBJECT,
        "UAVariable": NodeClass.VARIABLE,
        "UAMethod": NodeClass.METHOD,
        "UAObjectType": NodeClass.OBJECT_TYPE,
        "UAVariableType": NodeClass.VARIABLE_TYPE,
        "UADataType": NodeClass.DATA_TYPE,
        "UAReferenceType": NodeClass.REFERENCE_TYPE,
        "UAView": NodeClass.VIEW,
        "UAInterface": NodeClass.INTERFACE,
    }
)


def is_placeholder(browse_name: str) -> bool:
    """Placeholder nodes carry ``_Placeholder`` in their browse name."""
    return "_Placeholder" in browse_name


def mandatory_level_of(node: UANode) -> MandatoryLevel:
    """Whether a node is mandatory per its modelling rule.

    Only an explicit ``Optional``/``OptionalPlaceholder`` declaration makes a node
    optional; the absence of a rule does not.  The OPC UA namespace itself declares no
    rules, so nodes from it are treated as mandatory unless they say otherwise — this
    keeps derived models consistent with their bases in every retention mode.
    """
    if node.modelling_rule in {ModellingRule.OPTIONAL, ModellingRule.OPTIONAL_PLACEHOLDER}:
        return MandatoryLevel.OPTIONAL
    return MandatoryLevel.MANDATORY


def referenced_identifiers(node: UANode) -> tuple[ExpandedNodeId, ...]:
    """Every identifier the serialised XML of this node points at.

    A NodeSet embeds identifiers in more places than ``<Reference>`` elements: the
    ``DataType`` and ``ParentNodeId`` attributes, ``MethodDeclarationId``, the type
    definition, the ``ReferenceType`` attribute of a reference, and the ``DataType`` of
    structure fields and union alternatives.  Export, validation and reporting all read
    this one list, so no embedded identifier can be overlooked.
    """
    found: list[ExpandedNodeId] = []
    for ref in node.references:
        found.append(ref.target)
        if ref.reference_type is not None:
            found.append(ref.reference_type)
    for attribute in ("data_type", "parent_node_id", "method_declaration_id", "type_definition"):
        value = getattr(node, attribute, None)
        if value is not None:
            found.append(value)
    definition = getattr(node, "definition", None)
    if isinstance(node, UADataType) and definition is not None:
        found.extend(field.data_type for field in definition.fields)
        found.extend(alternative.data_type for alternative in definition.union_alternatives)
    return tuple(dict.fromkeys(found))
