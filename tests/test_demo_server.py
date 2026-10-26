"""Integration test: browse the demo server (tools/demo_server.py)."""

import asyncio
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from demo_server import build  # noqa: E402

from opc2ixon.opc import client as opc  # noqa: E402
from opc2ixon.opc.browser import browse  # noqa: E402
from opc2ixon.profiles import Profile  # noqa: E402

PORT = 48555


async def _run(profile: Profile, users=False, password=""):
    server = await build(PORT, users)
    async with server:
        pr = await opc.probe(profile)
        cert = pr.selected.certificate if pr.selected else None
        cl = await opc.connect(profile, password, cert)
        try:
            return await browse(cl, profile)
        finally:
            await cl.disconnect()


def _flat(root):
    return {"/".join(n.path): n for n in root.iter_all()}


def test_browse_anonymous():
    p = Profile(endpoint=f"opc.tcp://127.0.0.1:{PORT}", units_from_server=True)
    nodes = _flat(asyncio.run(_run(p)))
    speed = nodes["PLC_1/DataBlocksGlobal/DB_Motor/Speed"]
    assert speed.exportable and speed.address == 'ns=2;s="DB_Motor"."Speed"' and speed.unit == "rpm"
    assert nodes["PLC1/MAIN/aTemp[1]"].exportable
    assert not nodes["PLC1/MAIN/aTemp"].exportable
    assert "without element" in nodes["PLC1/MAIN/aRaw"].unsupported_reason
    assert nodes["PLC1/MAIN/stAxis/fPos"].exportable
    assert not nodes["PLC_1/DataBlocksGlobal/DB_Motor/Last Start"].exportable
    assert nodes["PLC_1/DataBlocksGlobal/DB_Motor/Secret"].unsupported_reason == "Not readable"
    assert "NodeId" in nodes["PLC1/MAIN/GuidVar"].unsupported_reason
    assert nodes["PLC1/MAIN/fPressure"].address == "ns=2;i=5001"
    assert not any(k.startswith("Server") for k in nodes)


def test_skip_arrays_and_start_node():
    p = Profile(endpoint=f"opc.tcp://127.0.0.1:{PORT}", expand_arrays=False, start_node="ns=2;s=PLC1")
    nodes = _flat(asyncio.run(_run(p)))
    assert "MAIN/aTemp" in nodes and "MAIN/aTemp[0]" not in nodes
    assert nodes["MAIN/aTemp"].unsupported_reason.startswith("Array (skipped")


@pytest.mark.parametrize("mode", ["Sign", "SignAndEncrypt"])
def test_secure_username(mode):
    p = Profile(
        endpoint=f"opc.tcp://127.0.0.1:{PORT}",
        security_policy="Basic256Sha256",
        security_mode=mode,
        auth="username",
        username="demo",
    )
    root = asyncio.run(_run(p, users=True, password="demo"))
    assert root.count_exportable() > 500


@pytest.mark.parametrize(
    "declare, manual, expect_read",
    [
        (True, 0, 128),  # limit declared in OperationLimits -> used directly
        (False, 0, None),  # limit not declared -> automatic fallback halves the requests
        (False, 50, 50),  # manual value in the profile
    ],
)
def test_server_with_request_limit(declare, manual, expect_read):
    from asyncua.server.internal_session import InternalSession

    from opc2ixon.opc.browser import Browser

    orig = InternalSession.read, InternalSession.browse

    async def run():
        p = Profile(endpoint=f"opc.tcp://127.0.0.1:{PORT}", max_nodes_per_request=manual)
        server = await build(PORT, False, max_nodes=128, declare_limits=declare)
        async with server:
            cl = await opc.connect(p, "", None)
            try:
                b = Browser(cl, p)
                root = await b.run()
                return b, root
            finally:
                await cl.disconnect()

    try:
        b, root = asyncio.run(run())
    finally:
        InternalSession.read, InternalSession.browse = orig
    assert root.count_exportable() > 500
    assert b.read_chunk <= 128 and b.browse_chunk <= 128
    if expect_read:
        assert b.read_chunk == expect_read
