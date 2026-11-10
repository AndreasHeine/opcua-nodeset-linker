"""Namespace metadata objects — the *address space* side of namespace bookkeeping.

OPC 10000-5 clause 6.3.13 defines ``NamespaceMetadataType``: an Object that is a
component of the ``Namespaces`` Object, carrying ``NamespaceUri``,
``NamespaceVersion``, ``NamespacePublicationDate``, ``IsNamespaceSubset``,
``StaticNodeIdTypes``, ``StaticNumericNodeIdRange`` and ``StaticStringNodeIdPattern``.

``IsNamespaceSubset`` is a node property, **not** an attribute of the UANodeSet XML
schema (OPC 10000-6): the file header only carries version information through the
``ModelTableEntry`` in ``Models``.  So a reduced document marks a subset by writing the
property value — this module is the single place that knows how.
"""

from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING

from nodeset_linker.models.identifiers import UA_NAMESPACE_URI, ExpandedNodeId, NodeId
from nodeset_linker.models.namespace import NODESET_XML_NAMESPACE
from nodeset_linker.models.node import UANode, UAVariable
from nodeset_linker.models.node_class import NodeClass, NodeRole

if TYPE_CHECKING:  # pragma: no cover
    from nodeset_linker.repository.model import SemanticModel

TYPES_XML_NAMESPACE = "http://opcfoundation.org/UA/2008/02/Types.xsd"

NAMESPACE_METADATA_TYPE_ID = 11616
SUBSET_PROPERTY = "IsNamespaceSubset"
NAMESPACE_URI_PROPERTY = "NamespaceUri"
VERSION_PROPERTY = "NamespaceVersion"
PUBLICATION_DATE_PROPERTY = "NamespacePublicationDate"

BOOLEAN_DATA_TYPE = ExpandedNodeId.local(NodeId.numeric_id(1), UA_NAMESPACE_URI)

PARENT_REFERENCE_TYPES = ("HasComponent", "HasProperty", "Organizes")


def is_namespace_metadata(node: UANode) -> bool:
    """Whether a node is a ``NamespaceMetadataType`` object."""
    if node.role is NodeRole.NAMESPACE_METADATA:
        return True
    typedef = node.type_definition
    if typedef is not None and typedef.identifier.numeric == NAMESPACE_METADATA_TYPE_ID:
        # The id 11616 belongs to the OPC UA namespace; the same number in a vendor
        # namespace is a custom type, not namespace metadata (§6.3.13).
        return typedef.namespace == UA_NAMESPACE_URI
    if node.node_class is NodeClass.OBJECT and (node.symbolic_name or "").endswith(
        "NamespaceMetadata"
    ):
        return True
    return node.browse_name.name.startswith("UA_NamespaceMetadata")


def namespace_of_metadata(obj: UANode) -> str:
    """The namespace a metadata object describes.

    OPC 10000-5 uses the namespace URI as BrowseName of the object; a document may also
    name it after the model, in which case the object's own namespace is the answer.
    """
    name = obj.browse_name.name
    if name.startswith("http"):
        return name
    return obj.node_id.namespace


def metadata_objects(model: SemanticModel, namespace_uri: str) -> list[UANode]:
    """Metadata objects describing *namespace_uri*, declared anywhere in the model."""
    found: list[UANode] = []
    for node in model.iter_nodes():
        if node.node_class is not NodeClass.OBJECT or not is_namespace_metadata(node):
            continue
        if namespace_of_metadata(node) == namespace_uri or node.node_id.namespace == namespace_uri:
            found.append(node)
    return found


def metadata_object_in(model: SemanticModel, namespace_uri: str) -> UANode | None:
    """The metadata object a *reduced* document can write the subset flag into."""
    candidates = metadata_objects(model, namespace_uri)
    return candidates[0] if candidates else None


def property_node(model: SemanticModel, obj: UANode, browse_name: str) -> UANode | None:
    """A property of a metadata object, linked by parent reference or ``ParentNodeId``."""
    for node in model.iter_nodes():
        if node.node_class is not NodeClass.VARIABLE or node.browse_name.name != browse_name:
            continue
        if node.parent_node_id == obj.node_id:
            return node
        for kind in PARENT_REFERENCE_TYPES:
            if obj.node_id in node.reference_of(kind):
                return node
    return None


def subset_property(model: SemanticModel, obj: UANode) -> UAVariable | None:
    property_node_ = property_node(model, obj, SUBSET_PROPERTY)
    return property_node_ if isinstance(property_node_, UAVariable) else None


def namespace_is_subset(model: SemanticModel, retained: set[ExpandedNodeId], uri: str) -> bool:
    """True when fewer nodes of a namespace are retained than the model declares.

    "Declared" is what the loaded documents contain for that namespace — the largest
    set the linker can see.  A document that already shipped a subset of a namespace is
    reported as *not* further reduced, because nothing was removed from it.
    """
    declared = sum(1 for node in model.iter_nodes() if node.node_id.namespace == uri)
    kept = sum(1 for node_id in retained if node_id.namespace == uri)
    return kept < declared


def subset_flags(model: SemanticModel, retained: set[ExpandedNodeId]) -> dict[str, bool]:
    """Subset status of every namespace the reduced document uses."""
    used = {node_id.namespace for node_id in retained}
    return {uri: namespace_is_subset(model, retained, uri) for uri in sorted(used)}


def marked_namespaces(
    model: SemanticModel, retained: set[ExpandedNodeId]
) -> dict[str, tuple[bool, bool]]:
    """``uri -> (is_subset, marker_present)``.

    ``marker_present`` says whether a metadata object survived the reduction; without
    one the subset cannot be announced inside the document, only in the report.
    """
    flags = subset_flags(model, retained)
    marked: dict[str, tuple[bool, bool]] = {}
    for uri, subset in flags.items():
        obj = metadata_object_in(model, uri)
        present = obj is not None and obj.node_id in retained
        marked[uri] = (subset, present)
    return marked


def subset_value_xml(subset: bool) -> str:
    """A serialized ``<Value>`` element stating the subset flag.

    The value of a variable is a child of ``Value``, in the datatype namespace; writing
    the ``Boolean`` directly onto the variable element would not validate.
    """
    boolean = f'<Boolean xmlns="{TYPES_XML_NAMESPACE}">{str(bool(subset)).lower()}</Boolean>'
    return f'<Value xmlns="{NODESET_XML_NAMESPACE}">{boolean}</Value>'


def with_subset_value(node: UAVariable, subset: bool) -> UAVariable:
    """A copy of the ``IsNamespaceSubset`` property carrying the computed value."""
    marked = node.with_value(subset_value_xml(subset))
    if marked.data_type is None:
        return replace(marked, data_type=BOOLEAN_DATA_TYPE)
    return marked
