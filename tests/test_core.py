from pathlib import Path

from opc2ixon.export.ixon_csv import HEADER, Selection, build_export, write_csv
from opc2ixon.opc.model import BrowseNode
from opc2ixon.opc.naming import assign_identifier, make_identifier, make_unique
from opc2ixon.opc.type_mapping import IxonType

REF = Path(__file__).resolve().parents[1] / "riferimenti" / "variables_esempio.csv"


def test_identifier_matches_ixon_examples():
    assert make_identifier(["F64"]) == "f-64"
    assert make_identifier(["ui64"]) == "ui-64"
    assert make_identifier(["i16"]) == "i-16"
    assert make_identifier(["Humidity"]) == "humidity"
    assert make_identifier(["PLC_1", '"DB_Motor"', "Speed 1"]) == "plc-1-db-motor-speed-1"
    assert make_identifier(["MAIN", "aTemp[3]"]) == "main-atemp-3"
    assert make_identifier(["Temperatura °C", "Zona è"]) == "temperatura-c-zona-e"


def test_unique():
    taken = {"a"}
    assert make_unique("a", taken) == "a-2"
    assert make_unique("a", taken) == "a-3"


def _node(path, t, addr):
    return BrowseNode(addr, path[-1], path, "Variable", address=addr, ixon_type=t)


def test_build_and_write(tmp_path):
    n1 = _node(["DB", "Speed"], IxonType("float", 32), "ns=3;s=DB.Speed")
    n2 = _node(["DB", "Count"], IxonType("int", 16, True), "ns=3;s=DB.Count")
    n3 = _node(["DB", "Name"], IxonType("str"), "ns=3;s=DB.Name")
    n4 = _node(["DB.Speed"], IxonType("bool"), "ns=3;i=7")  # same identifier as n1
    rows = build_export([Selection(n, 255, "") for n in (n1, n2, n3, n4)], ".", "")
    ids = [r.row.identifier for r in rows]
    assert ids[:3] == ["db-speed", "db-count", "db-name"]
    assert ids[3].startswith("db-speed-") and len(ids[3]) == len("db-speed-") + 6 and rows[3].renamed
    assert rows[1].row.signed == "True" and rows[1].row.width == "16"
    assert rows[2].row.max_string_length == "255" and rows[2].row.width == ""
    out = tmp_path / "x.csv"
    write_csv(out, [r.row for r in rows])
    data = out.read_bytes()
    assert not data.startswith(b"\xef\xbb\xbf") and b"\r\n" not in data
    lines = data.decode("utf-8").splitlines()
    assert lines[0] == ",".join(HEADER)
    assert lines[2] == "db-count,DB.Count,ns=3;s=DB.Count,int,16,True,,,"


def test_same_header_as_ixon_reference():
    if REF.exists():
        assert REF.read_text(encoding="utf-8").splitlines()[0] == ",".join(HEADER)


def test_normalize_endpoint_and_factor():
    from opc2ixon.ui.profile_window import normalize_endpoint
    from opc2ixon.ui.browser_window import normalize_factor

    assert normalize_endpoint("192.168.1.10:4840") == "opc.tcp://192.168.1.10:4840"
    assert normalize_endpoint(" opc.tcp://plc:4840/x ") == "opc.tcp://plc:4840/x"
    assert normalize_endpoint("tcp://plc:4840") == "opc.tcp://plc:4840"
    assert normalize_factor("0,1") == "0.1" and normalize_factor("") == "" and normalize_factor("abc") is None


def test_factor_per_selection():
    n = _node(["A"], IxonType("float", 32), "ns=2;s=A")
    rows = build_export([Selection(n, 255, "", "0.01")], ".", "10")
    assert rows[0].row.factor == "0.01"
    rows = build_export([Selection(n, 255, "")], ".", "10")
    assert rows[0].row.factor == "10"


def test_long_identifiers_are_shortened_stably():
    long_path = ["PLC_1", "DataBlocksGlobal", "DB_Production_Line_Number_3", "Station_Welding_Robot_Left",
                 "Axis_X", "Position_Actual_Value"]
    base = make_identifier(long_path)
    assert len(base) > 64
    a, short, dedup = assign_identifier(base, "ns=3;s=A", set())
    assert short and not dedup and len(a) <= 64
    assert a.startswith("plc-1-datablocksglobal---") and "position-actual-value-" in a
    assert a.count("---") == 1 and a == "plc-1-datablocksglobal---axis-x-position-actual-value-" + a[-6:]
    # stable: same key -> same identifier, regardless of what else is in the file
    b, _, _ = assign_identifier(base, "ns=3;s=A", {"x", "y", "z"})
    assert a == b
    # same long path but different NodeId -> different identifier
    c, _, _ = assign_identifier(base, "ns=3;s=B", {a})
    assert c != a and len(c) <= 64
    # 64 characters exactly: unchanged
    exact = "a" * 64
    assert assign_identifier(exact, "k", set())[0] == exact


def test_no_identifier_over_64_in_export():
    nodes = [
        _node(["Very Long Folder Name Number %d" % i, "Another Very Long Sub Folder", "Variable %d" % i],
              IxonType("bool"), f"ns=2;s=V{i}")
        for i in range(200)
    ]
    rows = build_export([Selection(n, 255, "") for n in nodes])
    ids = [r.row.identifier for r in rows]
    assert len(set(ids)) == len(ids) and max(map(len, ids)) <= 64
    assert all("---" in i for i in ids)


def test_too_many_operations_stops_with_clear_error():
    import asyncio

    import pytest
    from asyncua import ua

    from opc2ixon.opc.browser import Browser
    from opc2ixon.opc.client import ConnectionError_
    from opc2ixon.profiles import Profile

    calls = []

    class FakeUaClient:
        async def read_attributes(self, nids, attr):
            calls.append(len(nids))
            raise ua.uaerrors.BadTooManyOperations()

    class FakeClient:
        uaclient = FakeUaClient()

    b = Browser(FakeClient(), Profile())
    with pytest.raises(ConnectionError_) as e:
        asyncio.run(b._read_many([ua.NodeId(i, 2) for i in range(1000)], ua.AttributeIds.Value))
    assert calls == [500, 250, 125, 62, 31, 15, 7, 3, 1]  # always smaller, stops at 1
    assert "Max nodes per request" in str(e.value)

    calls.clear()

    class OtherError(FakeUaClient):
        async def read_attributes(self, nids, attr):
            calls.append(len(nids))
            raise ua.uaerrors.BadTimeout()

    FakeClient.uaclient = OtherError()
    with pytest.raises(ua.uaerrors.BadTimeout):
        asyncio.run(Browser(FakeClient(), Profile())._read_many([ua.NodeId(1, 2)] * 10, ua.AttributeIds.Value))
    assert calls == [10]  # other errors: no retry
