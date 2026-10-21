"""Mapping from OPC UA built-in data types to IXON variable types."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class IxonType:
    type: str  # bool | int | float | str
    width: int | None = None
    signed: bool | None = None

    def label(self) -> str:
        if self.type == "int":
            return f"int {self.width} {'signed' if self.signed else 'unsigned'}"
        if self.type == "float":
            return f"float {self.width}"
        return self.type


# ns=0 numeric ids of the OPC UA built-in data types
BOOLEAN, SBYTE, BYTE, INT16, UINT16, INT32, UINT32, INT64, UINT64 = range(1, 10)
FLOAT, DOUBLE, STRING, DATETIME, GUID, BYTESTRING = range(10, 16)
LOCALIZEDTEXT = 21
STRUCTURE = 22
BASEDATATYPE = 24
NUMBER, INTEGER, UINTEGER, ENUMERATION = 26, 27, 28, 29

BUILTIN_NAMES = {
    1: "Boolean", 2: "SByte", 3: "Byte", 4: "Int16", 5: "UInt16", 6: "Int32", 7: "UInt32",
    8: "Int64", 9: "UInt64", 10: "Float", 11: "Double", 12: "String", 13: "DateTime",
    14: "Guid", 15: "ByteString", 16: "XmlElement", 17: "NodeId", 18: "ExpandedNodeId",
    19: "StatusCode", 20: "QualifiedName", 21: "LocalizedText", 22: "Structure",
    23: "DataValue", 24: "BaseDataType", 25: "DiagnosticInfo", 26: "Number", 27: "Integer",
    28: "UInteger", 29: "Enumeration",
}

_MAP: dict[int, IxonType] = {
    BOOLEAN: IxonType("bool"),
    SBYTE: IxonType("int", 8, True),
    BYTE: IxonType("int", 8, False),
    INT16: IxonType("int", 16, True),
    UINT16: IxonType("int", 16, False),
    INT32: IxonType("int", 32, True),
    UINT32: IxonType("int", 32, False),
    INT64: IxonType("int", 64, True),
    UINT64: IxonType("int", 64, False),
    FLOAT: IxonType("float", 32),
    DOUBLE: IxonType("float", 64),
    STRING: IxonType("str"),
    LOCALIZEDTEXT: IxonType("str"),
    ENUMERATION: IxonType("int", 32, True),  # enums are transmitted as Int32
}

# Abstract types: the real type is only known from the value's variant type
ABSTRACT_TYPES = {BASEDATATYPE, NUMBER, INTEGER, UINTEGER}


def map_builtin(builtin_id: int | None) -> IxonType | None:
    """Return the IXON type for a (resolved) built-in data type id, or None if unsupported."""
    if builtin_id is None:
        return None
    return _MAP.get(builtin_id)


def builtin_name(builtin_id: int | None) -> str:
    if builtin_id is None:
        return "?"
    return BUILTIN_NAMES.get(builtin_id, f"i={builtin_id}")
