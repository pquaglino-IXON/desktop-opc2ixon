"""OPC UA connection handling: endpoints probe, security, certificates, authentication."""

from __future__ import annotations

import hashlib
import socket
from dataclasses import dataclass
from pathlib import Path

import logging

from asyncua import Client, ua
from asyncua.crypto import security_policies as sp
from cryptography import x509

from ..profiles import Profile, pki_dir

log = logging.getLogger("opc2ixon.opc")

POLICY_CLASSES = {
    "Basic256Sha256": sp.SecurityPolicyBasic256Sha256,
    "Aes128_Sha256_RsaOaep": sp.SecurityPolicyAes128Sha256RsaOaep,
    "Aes256_Sha256_RsaPss": sp.SecurityPolicyAes256Sha256RsaPss,
    "Basic256": sp.SecurityPolicyBasic256,
    "Basic128Rsa15": sp.SecurityPolicyBasic128Rsa15,
}
MODES = {
    "None": ua.MessageSecurityMode.None_,
    "Sign": ua.MessageSecurityMode.Sign,
    "SignAndEncrypt": ua.MessageSecurityMode.SignAndEncrypt,
}

APPLICATION_URI = f"urn:{socket.gethostname()}:opc2ixon"


class ConnectionError_(Exception):
    """User friendly connection error."""


def first_cert(der: bytes) -> bytes:
    """Servers sometimes send the certificate followed by its chain: keep the first one."""
    if len(der) > 4 and der[0] == 0x30 and der[1] == 0x82:
        return der[: int.from_bytes(der[2:4], "big") + 4]
    return der


def thumbprint(der: bytes) -> str:
    return hashlib.sha1(der).hexdigest().upper()


def describe_cert(der: bytes) -> str:
    try:
        c = x509.load_der_x509_certificate(der)
        return (
            f"Subject: {c.subject.rfc4514_string()}\n"
            f"Issuer: {c.issuer.rfc4514_string()}\n"
            f"Valid: {c.not_valid_before_utc:%Y-%m-%d} → {c.not_valid_after_utc:%Y-%m-%d}\n"
            f"SHA1: {thumbprint(der)}"
        )
    except Exception:
        return f"SHA1: {thumbprint(der)}"


# ---- trust store -------------------------------------------------------
def trusted_dir() -> Path:
    p = pki_dir() / "trusted"
    p.mkdir(parents=True, exist_ok=True)
    return p


def is_trusted(der: bytes) -> bool:
    return (trusted_dir() / f"{thumbprint(der)}.der").exists()


def trust(der: bytes) -> None:
    (trusted_dir() / f"{thumbprint(der)}.der").write_bytes(der)


def client_cert_paths() -> tuple[Path, Path]:
    d = pki_dir() / "own"
    return d / "client_cert.der", d / "client_key.pem"


async def ensure_client_certificate() -> tuple[Path, Path]:
    """Create (once) the self-signed application certificate of this tool."""
    cert, key = client_cert_paths()
    c = Client("opc.tcp://localhost:4840")
    c.application_uri = APPLICATION_URI
    await c.setup_self_signed_certificate(
        key, cert, subject_attrs={"organizationName": "opc2ixon"}
    )
    return cert, key


# ---- probe -------------------------------------------------------------
@dataclass
class EndpointInfo:
    url: str
    policy: str
    mode: str
    certificate: bytes


@dataclass
class ProbeResult:
    endpoints: list[EndpointInfo]
    selected: EndpointInfo | None  # endpoint matching the profile, None if not offered

    def summary(self) -> str:
        lines = [f"• {e.policy} / {e.mode}" for e in self.endpoints]
        return "\n".join(sorted(set(lines)))


def _policy_name(uri: str) -> str:
    return uri.rsplit("#", 1)[-1]


def _mode_name(mode: ua.MessageSecurityMode) -> str:
    return {1: "None", 2: "Sign", 3: "SignAndEncrypt"}.get(int(mode), str(mode))


async def probe(profile: Profile) -> ProbeResult:
    """List the server endpoints (always over an unsecured channel, as per spec)."""
    client = Client(profile.endpoint, timeout=profile.timeout_s)
    client.application_uri = APPLICATION_URI
    try:
        eps = await client.connect_and_get_server_endpoints()
    except Exception as e:
        log.warning("GetEndpoints failed on %s", profile.endpoint, exc_info=True)
        raise ConnectionError_(f"Cannot reach {profile.endpoint}:\n{_err(e)}") from e
    infos = [
        EndpointInfo(
            e.EndpointUrl,
            _policy_name(e.SecurityPolicyUri),
            _mode_name(e.SecurityMode),
            first_cert(e.ServerCertificate or b""),
        )
        for e in eps
    ]
    selected = None
    for i in infos:
        if i.policy == profile.security_policy and i.mode == profile.security_mode:
            selected = i
            break
    if profile.security_policy == "None" and profile.security_mode == "None" and selected is None:
        # some servers list "None" only with mode None
        selected = next((i for i in infos if i.policy == "None"), None)
    return ProbeResult(infos, selected)


# ---- connect -----------------------------------------------------------
async def connect(profile: Profile, password: str, server_cert: bytes | None) -> Client:
    """Return a connected client. Caller must ``await client.disconnect()``."""
    client = Client(profile.endpoint, timeout=profile.timeout_s)
    client.application_uri = APPLICATION_URI
    client.name = "opc2ixon"
    client.description = "opc2ixon"

    if profile.security_policy != "None" and profile.security_mode != "None":
        policy = POLICY_CLASSES.get(profile.security_policy)
        if policy is None:
            raise ConnectionError_(f"Unknown security policy {profile.security_policy}")
        if not server_cert:
            raise ConnectionError_("The server did not provide a certificate for this endpoint.")
        cert, key = await ensure_client_certificate()
        await client.set_security(
            policy, str(cert), str(key), server_certificate=server_cert, mode=MODES[profile.security_mode]
        )

    if profile.auth == "username":
        client.set_user(profile.username)
        client.set_password(password)
    elif profile.auth == "certificate":
        if not profile.user_cert_path or not profile.user_key_path:
            raise ConnectionError_("Select the user certificate and private key in the Security tab.")
        await client.load_client_certificate(profile.user_cert_path)
        await client.load_private_key(Path(profile.user_key_path), password or None)

    try:
        await client.connect()
    except Exception as e:
        log.warning("Connect failed on %s", profile.endpoint, exc_info=True)
        raise ConnectionError_(_explain(e, profile)) from e
    return client


def _err(e: BaseException) -> str:
    msg = str(e) or e.__class__.__name__
    return msg


def _explain(e: BaseException, profile: Profile) -> str:
    msg = _err(e)
    low = msg.lower()
    hints = []
    if "badidentitytoken" in low or "baduseraccessdenied" in low:
        hints.append("Check username/password or user certificate.")
    if "badsecuritychecksfailed" in low or "badcertificate" in low:
        hints.append(
            "The server rejected this application's certificate. On Siemens/Beckhoff PLCs the "
            "client certificate must be trusted on the PLC: use 'Export client certificate' in "
            "the Security tab and add it to the PLC's trusted certificates, then retry."
        )
    if "timeout" in low or isinstance(e, TimeoutError):
        hints.append("Timeout: check address, port and firewall.")
    return msg + ("\n\n" + "\n".join(hints) if hints else "")
