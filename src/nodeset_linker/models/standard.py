"""Canonical namespace-zero datatype names (OPC 10000-5, clause 12).

These are address-space NodeIds, not Variant encoding tags: for example i=22 is
Structure and i=24 is BaseDataType, not ExtensionObject and Variant.
"""

from collections.abc import Mapping

STANDARD_DATA_TYPE_IDS: Mapping[str, int] = {
    "Boolean": 1,
    "SByte": 2,
    "Byte": 3,
    "Int16": 4,
    "UInt16": 5,
    "Int32": 6,
    "UInt32": 7,
    "Int64": 8,
    "UInt64": 9,
    "Float": 10,
    "Double": 11,
    "String": 12,
    "DateTime": 13,
    "Guid": 14,
    "ByteString": 15,
    "XmlElement": 16,
    "NodeId": 17,
    "ExpandedNodeId": 18,
    "StatusCode": 19,
    "QualifiedName": 20,
    "LocalizedText": 21,
    "Structure": 22,
    "DataValue": 23,
    "BaseDataType": 24,
    "DiagnosticInfo": 25,
    "Number": 26,
    "Integer": 27,
    "UInteger": 28,
    "Enumeration": 29,
}

STANDARD_DATA_TYPE_NAMES: Mapping[str, str] = {
    f"i={number}": name for name, number in STANDARD_DATA_TYPE_IDS.items()
}
