"""Data model produced by the browser and consumed by the UI / exporter."""

from __future__ import annotations

from dataclasses import dataclass, field

from .type_mapping import IxonType


@dataclass
class BrowseNode:
    node_id: str  # NodeId as text, e.g. ns=3;s="DB1"."Speed"
    display_name: str
    path: list[str]  # path elements relative to the start node
    node_class: str  # "Object" | "Variable"
    address: str | None = None  # IXON address (ns=X;s=... / ns=X;i=...), None if not representable
    opc_type: str = ""  # OPC UA data type name (for display)
    ixon_type: IxonType | None = None
    is_array: bool = False
    unsupported_reason: str | None = None  # None -> can be exported
    unit: str = ""
    children: list[BrowseNode] = field(default_factory=list)

    @property
    def exportable(self) -> bool:
        return (
            self.node_class == "Variable"
            and self.unsupported_reason is None
            and self.ixon_type is not None
            and self.address is not None
        )

    def iter_all(self):
        yield self
        for c in self.children:
            yield from c.iter_all()

    def count_exportable(self) -> int:
        return sum(1 for n in self.iter_all() if n.exportable)
