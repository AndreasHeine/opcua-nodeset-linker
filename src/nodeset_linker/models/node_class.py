"""Node categories of the OPC UA information model."""

from __future__ import annotations

from enum import Enum


class NodeClass(Enum):
    """The twelve node classes defined by OPC UA Part 3 (incl. ``UAInterface``)."""

    OBJECT = "Object"
    VARIABLE = "Variable"
    METHOD = "Method"
    OBJECT_TYPE = "ObjectType"
    VARIABLE_TYPE = "VariableType"
    DATA_TYPE = "DataType"
    REFERENCE_TYPE = "ReferenceType"
    VIEW = "View"
    INTERFACE = "Interface"

    @property
    def is_type(self) -> bool:
        return self in {
            NodeClass.OBJECT_TYPE,
            NodeClass.VARIABLE_TYPE,
            NodeClass.DATA_TYPE,
            NodeClass.REFERENCE_TYPE,
            NodeClass.INTERFACE,
        }

    @property
    def is_instance(self) -> bool:
        return self in {NodeClass.OBJECT, NodeClass.VARIABLE, NodeClass.METHOD, NodeClass.VIEW}


class NodeRole(Enum):
    """Semantic role a node plays inside the model.

    Derived from the node's own attributes, never from XML structure.  Roles drive
    retention and reporting.
    """

    ABSTRACT_TYPE = "abstract_type"
    BASE_TYPE = "base_type"
    STRUCTURE = "structure"
    ENUM = "enum"
    UNION = "union"
    OPTION_SET = "option_set"
    ENCODING = "encoding"
    INTERFACE = "interface"
    METHOD = "method"
    ARGUMENT = "argument"
    PLACEHOLDER = "placeholder"
    NAMESPACE_METADATA = "namespace_metadata"
    VIEW = "view"
    INSTANCE = "instance"
    TYPE_DEFINITION = "type_definition"

    @property
    def is_hard_dependency(self) -> bool:
        """Roles that OPC UA semantics require: they may never be dropped."""
        return self in {
            NodeRole.ABSTRACT_TYPE,
            NodeRole.BASE_TYPE,
            NodeRole.STRUCTURE,
            NodeRole.ENUM,
            NodeRole.UNION,
            NodeRole.OPTION_SET,
            NodeRole.ENCODING,
            NodeRole.INTERFACE,
            NodeRole.TYPE_DEFINITION,
            NodeRole.PLACEHOLDER,
        }

    @property
    def is_droppable(self) -> bool:
        """Roles a retention mode may drop: instances, arguments and views.

        A node with one of these roles is only kept because something references it; its
        own existence is not part of a type contract.  Types never belong here — dropping
        one would break inheritance, datatype or encoding chains.
        """
        return self in {NodeRole.INSTANCE, NodeRole.ARGUMENT, NodeRole.METHOD, NodeRole.VIEW}


class ModellingRule(Enum):
    """Modelling rules attached to instance/derived nodes."""

    MANDATORY = "Mandatory"
    MANDATORY_PLACEHOLDER = "MandatoryPlaceholder"
    OPTIONAL = "Optional"
    OPTIONAL_PLACEHOLDER = "OptionalPlaceholder"
    NO_RULE = None
