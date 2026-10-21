"""Demo OPC UA server for testing the browser without a PLC.

    python tools/demo_server.py                # anonymous, opc.tcp://localhost:4840
    python tools/demo_server.py --users        # requires username demo / password demo
    python tools/demo_server.py --port 4841

Offers the endpoints None, Basic256Sha256 Sign and Basic256Sha256 SignAndEncrypt.
The address space mimics Siemens (string NodeIds with quotes) and Beckhoff
(arrays with element nodes, structures with member nodes).
"""

from __future__ import annotations

import argparse
import asyncio
import datetime
import logging
import socket
import uuid
from pathlib import Path

from asyncua import Server, ua
from asyncua.crypto.cert_gen import setup_self_signed_certificate
from asyncua.crypto.permission_rules import User, UserRole
from cryptography.x509.oid import ExtendedKeyUsageOID

HERE = Path(__file__).resolve().parent
PKI = HERE / ".demo_pki"


class DemoUsers:
    def get_user(self, iserver, username=None, password=None, certificate=None):
        if username == "demo" and password == "demo":
            return User(role=UserRole.User, name="demo")
        return None


async def build(port: int, users: bool) -> Server:
    server = Server(user_manager=DemoUsers() if users else None)
    await server.init()
    uri = f"urn:{socket.gethostname()}:opc2ixon:DemoServer"
    await server.set_application_uri(uri)
    server.set_server_name("opc2ixon demo OPC UA server")
    server.set_endpoint(f"opc.tcp://0.0.0.0:{port}/")
    server.set_security_policy(
        [
            ua.SecurityPolicyType.NoSecurity,
            ua.SecurityPolicyType.Basic256Sha256_Sign,
            ua.SecurityPolicyType.Basic256Sha256_SignAndEncrypt,
        ]
    )
    if users:
        server.set_identity_tokens([ua.UserNameIdentityToken])
    PKI.mkdir(exist_ok=True)
    key, cert = PKI / "server_key.pem", PKI / "server_cert.der"
    await setup_self_signed_certificate(
        key, cert, uri, socket.gethostname(), [ExtendedKeyUsageOID.SERVER_AUTH], {}
    )
    await server.load_certificate(str(cert))
    await server.load_private_key(str(key))

    ns = await server.register_namespace("urn:ixon:demo")
    objects = server.nodes.objects

    # --- Siemens like: PLC_1 / DataBlocksGlobal / DB_Motor -----------------------
    plc = await objects.add_object(ua.NodeId("PLC", ns), ua.QualifiedName("PLC_1", ns))
    dbs = await plc.add_folder(ua.NodeId("DataBlocksGlobal", ns), ua.QualifiedName("DataBlocksGlobal", ns))
    db = await dbs.add_object(ua.NodeId('"DB_Motor"', ns), ua.QualifiedName("DB_Motor", ns))

    def sid(name):
        return ua.NodeId(f'"DB_Motor"."{name}"', ns)

    v = await db.add_variable(sid("Running"), ua.QualifiedName("Running", ns), True, ua.VariantType.Boolean)
    v = await db.add_variable(sid("Speed"), ua.QualifiedName("Speed", ns), 1450.5, ua.VariantType.Float)
    eu = ua.EUInformation()
    eu.NamespaceUri = "http://www.opcfoundation.org/UA/units/un/cefact"
    eu.UnitId = 5066068
    eu.DisplayName = ua.LocalizedText("rpm")
    eu.Description = ua.LocalizedText("revolutions per minute")
    await v.add_property(ns, "EngineeringUnits", eu)
    v = await db.add_variable(sid("Temperature"), ua.QualifiedName("Temperature", ns), 42.0, ua.VariantType.Double)
    eu2 = ua.EUInformation()
    eu2.DisplayName = ua.LocalizedText("°C")
    await v.add_property(ns, "EngineeringUnits", eu2)
    for name, vt, val in [
        ("Mode", ua.VariantType.SByte, -1),
        ("Status", ua.VariantType.Byte, 3),
        ("Counter Int", ua.VariantType.Int16, -12),
        ("Counter UInt", ua.VariantType.UInt16, 12),
        ("Total DInt", ua.VariantType.Int32, 123456),
        ("Total UDInt", ua.VariantType.UInt32, 123456),
        ("Energy LInt", ua.VariantType.Int64, 10**12),
        ("Energy ULInt", ua.VariantType.UInt64, 10**12),
        ("Recipe Name", ua.VariantType.String, "Recipe A"),
        ("Last Start", ua.VariantType.DateTime, datetime.datetime.now(datetime.timezone.utc)),
        ("Raw Data", ua.VariantType.ByteString, b"\x01\x02"),
    ]:
        await db.add_variable(sid(name), ua.QualifiedName(name, ns), val, vt)

    # write-only / not readable variable
    v = await db.add_variable(sid("Secret"), ua.QualifiedName("Secret", ns), 0, ua.VariantType.Int32)
    await v.write_attribute(ua.AttributeIds.AccessLevel, ua.DataValue(ua.Variant(0, ua.VariantType.Byte)))
    await v.write_attribute(ua.AttributeIds.UserAccessLevel, ua.DataValue(ua.Variant(0, ua.VariantType.Byte)))

    # --- Beckhoff like: PLC1 / MAIN ----------------------------------------------
    tc = await objects.add_object(ua.NodeId("PLC1", ns), ua.QualifiedName("PLC1", ns))
    main = await tc.add_object(ua.NodeId("PLC1.MAIN", ns), ua.QualifiedName("MAIN", ns))
    await main.add_variable(ua.NodeId("PLC1.MAIN.bStart", ns), ua.QualifiedName("bStart", ns), False)
    await main.add_variable(
        ua.NodeId("PLC1.MAIN.nCount", ns), ua.QualifiedName("nCount", ns), 5, ua.VariantType.Int16
    )
    # numeric NodeId
    await main.add_variable(ua.NodeId(5001, ns), ua.QualifiedName("fPressure", ns), 2.5, ua.VariantType.Float)
    # guid NodeId (not supported by IXON)
    await main.add_variable(
        ua.NodeId(uuid.UUID("12345678-1234-1234-1234-123456789abc"), ns),
        ua.QualifiedName("GuidVar", ns), 1, ua.VariantType.Int32,
    )
    # array with element nodes
    arr = await main.add_variable(
        ua.NodeId("PLC1.MAIN.aTemp", ns), ua.QualifiedName("aTemp", ns), [20.0, 21.0, 22.0], ua.VariantType.Float
    )
    await arr.write_value_rank(1)
    await arr.write_array_dimensions([3])
    for i in range(3):
        await arr.add_variable(
            ua.NodeId(f"PLC1.MAIN.aTemp[{i}]", ns), ua.QualifiedName(f"aTemp[{i}]", ns), 20.0 + i, ua.VariantType.Float
        )
    # array without element nodes
    arr2 = await main.add_variable(
        ua.NodeId("PLC1.MAIN.aRaw", ns), ua.QualifiedName("aRaw", ns), [1, 2, 3, 4], ua.VariantType.Int32
    )
    await arr2.write_value_rank(1)
    # structure with member nodes
    st = await main.add_variable(
        ua.NodeId("PLC1.MAIN.stAxis", ns),
        ua.QualifiedName("stAxis", ns),
        ua.Variant(None, ua.VariantType.Null),
        datatype=ua.NodeId(ua.ObjectIds.Structure),
    )
    await st.add_variable(ua.NodeId("PLC1.MAIN.stAxis.fPos", ns), ua.QualifiedName("fPos", ns), 10.5, ua.VariantType.Double)
    await st.add_variable(ua.NodeId("PLC1.MAIN.stAxis.bHomed", ns), ua.QualifiedName("bHomed", ns), True)
    await st.add_variable(ua.NodeId("PLC1.MAIN.stAxis.sName", ns), ua.QualifiedName("sName", ns), "X axis")

    # long names: identifiers longer than 64 characters (shortened as head---tail-code)
    longdb = await dbs.add_object(
        ua.NodeId('"DB_Production_Line_Number_3_Station_Welding_Robot_Left"', ns),
        ua.QualifiedName("DB_Production_Line_Number_3_Station_Welding_Robot_Left", ns),
    )
    for name in ("Axis_X_Position_Actual_Value", "Axis_Y_Position_Actual_Value", "Axis_Z_Position_Actual_Value"):
        await longdb.add_variable(
            ua.NodeId(f'"DB_Production_Line_Number_3_Station_Welding_Robot_Left"."{name}"', ns),
            ua.QualifiedName(name, ns), 0.0, ua.VariantType.Double,
        )

    # many variables to test performance
    bulk = await objects.add_folder(ua.NodeId("Bulk", ns), ua.QualifiedName("Bulk", ns))
    for g in range(10):
        grp = await bulk.add_folder(ua.NodeId(f"Bulk.G{g}", ns), ua.QualifiedName(f"Group {g}", ns))
        for k in range(50):
            await grp.add_variable(
                ua.NodeId(f"Bulk.G{g}.V{k}", ns), ua.QualifiedName(f"Value {k}", ns), float(k), ua.VariantType.Float
            )
    return server


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=4840)
    ap.add_argument("--users", action="store_true", help="require username demo / password demo")
    args = ap.parse_args()
    logging.basicConfig(level=logging.WARNING)
    server = await build(args.port, args.users)
    async with server:
        print(f"Demo server running on opc.tcp://localhost:{args.port}  (Ctrl+C to stop)")
        while True:
            await asyncio.sleep(1)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
