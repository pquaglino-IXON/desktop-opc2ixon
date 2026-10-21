"""Read / write IXON Cloud variable CSV files.

Format (from an IXON export)::

    Identifier,Name,Address,Type,Width,Signed,Max string length,Factor,Unit
    f-64,F64,ns=2;s=F64,float,64,,,,
    i-16,i16,ns=2;s=i16,int,16,True,,,
    ricetta,Ricetta,ns=2;s=Ricetta,str,,,20,,

Comma separated, UTF-8 without BOM, LF line endings.
"""

from __future__ import annotations

import csv
import io
from dataclasses import dataclass, field
from pathlib import Path

from ..opc.model import BrowseNode
from ..opc.naming import assign_identifier, make_identifier, make_name

HEADER = ["Identifier", "Name", "Address", "Type", "Width", "Signed", "Max string length", "Factor", "Unit"]


@dataclass
class CsvRow:
    identifier: str = ""
    name: str = ""
    address: str = ""
    type: str = ""
    width: str = ""
    signed: str = ""
    max_string_length: str = ""
    factor: str = ""
    unit: str = ""

    def as_list(self) -> list[str]:
        return [
            self.identifier, self.name, self.address, self.type, self.width,
            self.signed, self.max_string_length, self.factor, self.unit,
        ]


@dataclass
class ExportRow:
    row: CsvRow
    renamed: bool = False  # identifier got a code suffix to stay unique
    shortened: bool = False  # identifier was longer than 64 characters and was cut
    full_identifier: str = ""
    notes: list[str] = field(default_factory=list)


def write_csv(path: str | Path, rows: list[CsvRow]) -> None:
    buf = io.StringIO()
    w = csv.writer(buf, delimiter=",", lineterminator="\n", quoting=csv.QUOTE_MINIMAL)
    w.writerow(HEADER)
    for r in rows:
        w.writerow(r.as_list())
    Path(path).write_text(buf.getvalue(), encoding="utf-8", newline="")


def row_from_node(
    node: BrowseNode,
    identifier: str,
    name: str,
    str_len: int | str,
    factor: str = "",
    unit: str = "",
) -> CsvRow:
    t = node.ixon_type
    assert t is not None and node.address is not None
    return CsvRow(
        identifier=identifier,
        name=name,
        address=node.address,
        type=t.type,
        width=str(t.width) if t.type in ("int", "float") and t.width else "",
        signed=("True" if t.signed else "False") if t.type == "int" else "",
        max_string_length=str(str_len) if t.type == "str" else "",
        factor=factor,
        unit=unit,
    )


@dataclass
class Selection:
    """A variable selected in the UI, with the per-row overrides."""

    node: BrowseNode
    str_len: int
    unit: str
    factor: str | None = None  # None -> profile default


def build_export(
    selections: list[Selection],
    name_separator: str = ".",
    default_factor: str = "",
) -> list[ExportRow]:
    """One row per selected variable, with unique identifiers.

    IXON adds the variables of an imported CSV and skips those whose identifier already
    exists, so the file can always contain the full selection.
    """
    result: list[ExportRow] = []
    taken: set[str] = set()
    for s in selections:
        base = make_identifier(s.node.path)
        ident, shortened, dedup = assign_identifier(base, s.node.address or base, taken)
        row = row_from_node(
            s.node,
            ident,
            make_name(s.node.path, name_separator),
            s.str_len,
            default_factor if s.factor is None else s.factor,
            s.unit,
        )
        er = ExportRow(row, renamed=dedup, shortened=shortened, full_identifier=base)
        if shortened:
            er.notes.append(f"shortened to 64 characters, full identifier: {base}")
        if dedup:
            er.notes.append(f"identifier '{base}' already used by another variable, code added")
        result.append(er)
    return result
