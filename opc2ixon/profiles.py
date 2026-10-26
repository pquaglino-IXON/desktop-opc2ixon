"""Connection / browse / export profiles, stored as JSON files in the user config folder."""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, fields
from pathlib import Path

from platformdirs import user_config_dir

from . import APP_ID

SECURITY_POLICIES = [
    "None",
    "Basic256Sha256",
    "Aes128_Sha256_RsaOaep",
    "Aes256_Sha256_RsaPss",
    "Basic256",
    "Basic128Rsa15",
]
SECURITY_MODES = ["None", "Sign", "SignAndEncrypt"]
AUTH_MODES = ["anonymous", "username", "certificate"]


def config_dir() -> Path:
    p = Path(user_config_dir(APP_ID, appauthor=False))
    p.mkdir(parents=True, exist_ok=True)
    return p


def profiles_dir() -> Path:
    p = config_dir() / "profiles"
    p.mkdir(parents=True, exist_ok=True)
    return p


def pki_dir() -> Path:
    p = config_dir() / "pki"
    p.mkdir(parents=True, exist_ok=True)
    return p


@dataclass
class Profile:
    name: str = "New profile"
    # Connection
    endpoint: str = "opc.tcp://192.168.1.10:4840"
    timeout_s: float = 10.0
    # Security
    security_policy: str = "None"
    security_mode: str = "None"
    auth: str = "anonymous"
    username: str = ""
    remember_password: bool = False
    user_cert_path: str = ""
    user_key_path: str = ""
    # Browse
    start_node: str = ""  # empty -> Objects folder
    ignore_ns0: bool = True
    expand_arrays: bool = True
    max_depth: int = 25
    max_nodes_per_request: int = 0  # 0 = automatic (server limits + fallback)
    # Export
    default_str_len: int = 255
    units_from_server: bool = False
    default_factor: str = ""
    name_separator: str = "."

    # ---- serialization -------------------------------------------------
    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> Profile:
        known = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in data.items() if k in known})

    def copy(self) -> Profile:
        return Profile.from_dict(self.to_dict())

    # ---- password (system keychain) ------------------------------------
    def _keyring_key(self) -> str:
        return f"{self.endpoint}|{self.username}"

    def load_password(self) -> str:
        try:
            import keyring

            return keyring.get_password(APP_ID, self._keyring_key()) or ""
        except Exception:
            return ""

    def save_password(self, password: str) -> None:
        try:
            import keyring

            if password:
                keyring.set_password(APP_ID, self._keyring_key(), password)
            else:
                keyring.delete_password(APP_ID, self._keyring_key())
        except Exception:
            pass


def _safe_filename(name: str) -> str:
    s = re.sub(r"[^A-Za-z0-9._ -]+", "_", name).strip() or "profile"
    return s + ".json"


class ProfileStore:
    def __init__(self, folder: Path | None = None):
        self.folder = folder or profiles_dir()
        self.folder.mkdir(parents=True, exist_ok=True)

    def list(self) -> list[Profile]:
        out = []
        for f in sorted(self.folder.glob("*.json")):
            try:
                out.append(Profile.from_dict(json.loads(f.read_text(encoding="utf-8"))))
            except Exception:
                continue
        return sorted(out, key=lambda p: p.name.lower())

    def path_for(self, name: str) -> Path:
        return self.folder / _safe_filename(name)

    def save(self, profile: Profile, old_name: str | None = None) -> None:
        if old_name and old_name != profile.name:
            self.delete(old_name)
        self.path_for(profile.name).write_text(
            json.dumps(profile.to_dict(), indent=2, ensure_ascii=False), encoding="utf-8"
        )

    def delete(self, name: str) -> None:
        p = self.path_for(name)
        if p.exists():
            p.unlink()

    def exists(self, name: str) -> bool:
        return self.path_for(name).exists()

    @staticmethod
    def export_to(profile: Profile, path: Path) -> None:
        Path(path).write_text(json.dumps(profile.to_dict(), indent=2, ensure_ascii=False), encoding="utf-8")

    @staticmethod
    def import_from(path: Path) -> Profile:
        return Profile.from_dict(json.loads(Path(path).read_text(encoding="utf-8")))


# ---- migration from the old application name -----------------------------------------
OLD_APP_ID = "IXON-OPC-Browser"  # name used up to the rename to opc2ixon


def migrate_from_old_name() -> str | None:
    """Copy profiles, trusted server certificates and remembered passwords from the folder
    of the old application name. Runs once; the old folder is left untouched.
    Returns a short summary for the log, or None if there was nothing to do.
    """
    import logging
    import shutil

    new = config_dir()
    marker = new / ".migrated_from_old_name"
    old = Path(user_config_dir(OLD_APP_ID, appauthor=False))
    if marker.exists() or not old.is_dir():
        return None
    n_prof = n_cert = n_pw = 0
    try:
        for f in sorted((old / "profiles").glob("*.json")):
            dest = profiles_dir() / f.name
            if not dest.exists():
                shutil.copy2(f, dest)
                n_prof += 1
        trusted_old = old / "pki" / "trusted"
        if trusted_old.is_dir():
            trusted_new = pki_dir() / "trusted"
            trusted_new.mkdir(parents=True, exist_ok=True)
            for f in trusted_old.glob("*.der"):
                if not (trusted_new / f.name).exists():
                    shutil.copy2(f, trusted_new / f.name)
                    n_cert += 1
        try:
            import keyring

            for prof in ProfileStore().list():
                if not prof.remember_password:
                    continue
                key = prof._keyring_key()
                pw = keyring.get_password(OLD_APP_ID, key)
                if pw and not keyring.get_password(APP_ID, key):
                    keyring.set_password(APP_ID, key, pw)
                    n_pw += 1
        except Exception:
            logging.getLogger("opc2ixon").warning("Passwords not migrated", exc_info=True)
    finally:
        marker.write_text(f"migrated from {old}\n", encoding="utf-8")
    return (
        f"Migrated from {old}: {n_prof} profiles, {n_cert} trusted server certificates, "
        f"{n_pw} remembered passwords"
    )
