"""Shared fixtures for the test suite."""

from __future__ import annotations

from pathlib import Path

import pytest

from nodeset_linker.models.identifiers import ExpandedNodeId, NodeId, QualifiedName
from nodeset_linker.models.node import (
    Definition,
    EnumValue,
    Reference,
    StructureField,
    UADataType,
    UAMethod,
    UANode,
    UAObjectType,
    UAReferenceType,
    UAVariable,
)
from nodeset_linker.models.node_class import ModellingRule, NodeClass, NodeRole
from nodeset_linker.repository.model import SemanticModel, load_nodesets

DATA = Path(__file__).parent / "data"
UA_BASE = DATA / "ua_base.xml"
MACHINERY = DATA / "machinery.xml"
LEGACY = DATA / "legacy_namespace.xml"
NODESET_SCHEMA = DATA / "schema" / "UANodeSet.xsd"

UA_NS = "http://opcfoundation.org/UA/"
Machinery_NS = "http://opcfoundation.org/UA/Machinery/"


def node_id(number: int, namespace: str = UA_NS) -> ExpandedNodeId:
    return ExpandedNodeId(identifier=NodeId.numeric_id(number), namespace_uri=namespace)


def spaced(text: str) -> str:
    """Console output with line breaks collapsed.

    A CLI message that names a path is printed at the console width, so it wraps at a column
    that depends on how long the temp directory it runs in is — the same phrase is one line
    locally and two lines on CI.  Assertions on such a message compare this collapsed form.
    """
    return " ".join(text.split())


@pytest.fixture
def ua_base() -> Path:
    return UA_BASE


@pytest.fixture
def machinery() -> Path:
    return MACHINERY


@pytest.fixture
def legacy() -> Path:
    return LEGACY


@pytest.fixture
def nodeset_schema() -> Path:
    """The official UANodeSet schema exported documents are validated against."""
    return NODESET_SCHEMA


@pytest.fixture
def merged_model() -> SemanticModel:
    return load_nodesets([UA_BASE, MACHINERY])


def make_object(browse: str, number: int, refs: tuple[Reference, ...] = ()) -> UANode:
    return UANode(
        node_id=node_id(number),
        browse_name=QualifiedName(name=browse),
        node_class=NodeClass.OBJECT,
        role=NodeRole.INSTANCE,
        references=refs,
    )


def make_object_type(browse: str, number: int, refs: tuple[Reference, ...] = ()) -> UAObjectType:
    return UAObjectType(
        node_id=node_id(number),
        browse_name=QualifiedName(name=browse),
        node_class=NodeClass.OBJECT_TYPE,
        role=NodeRole.TYPE_DEFINITION,
        references=refs,
    )


def make_data_type(
    browse: str, number: int, refs: tuple[Reference, ...] = (), definition: Definition | None = None
) -> UADataType:
    return UADataType(
        node_id=node_id(number),
        browse_name=QualifiedName(name=browse),
        node_class=NodeClass.DATA_TYPE,
        role=NodeRole.STRUCTURE if definition else NodeRole.BASE_TYPE,
        references=refs,
        definition=definition,
    )


def make_reference_type(browse: str, number: int, refs: tuple[Reference, ...] = ()) -> UAReferenceType:
    return UAReferenceType(
        node_id=node_id(number),
        browse_name=QualifiedName(name=browse),
        node_class=NodeClass.REFERENCE_TYPE,
        role=NodeRole.BASE_TYPE,
        references=refs,
    )


def make_variable(
    browse: str,
    number: int,
    data_type: ExpandedNodeId | None = None,
    refs: tuple[Reference, ...] = (),
    rule: ModellingRule = ModellingRule.NO_RULE,
) -> UAVariable:
    return UAVariable(
        node_id=node_id(number),
        browse_name=QualifiedName(name=browse),
        node_class=NodeClass.VARIABLE,
        role=NodeRole.INSTANCE,
        references=refs,
        data_type=data_type,
        modelling_rule=rule,
    )


def make_method(browse: str, number: int, refs: tuple[Reference, ...] = ()) -> UAMethod:
    return UAMethod(
        node_id=node_id(number),
        browse_name=QualifiedName(name=browse),
        node_class=NodeClass.METHOD,
        role=NodeRole.METHOD,
        references=refs,
    )


def enum_definition(values: tuple[str, ...]) -> Definition:
    return Definition(
        kind="Enum", enum_values=tuple(EnumValue(name=name, value=index) for index, name in enumerate(values))
    )


def structure_definition(fields: tuple[tuple[str, ExpandedNodeId], ...]) -> Definition:
    return Definition(
        kind="Structure",
        fields=tuple(StructureField(name=name, data_type=data_type) for name, data_type in fields),
    )
