"""Connection sequence shared by 'Test connection' and 'Connect & Browse'.

1. probe the endpoints (unsecured channel, as per OPC UA spec)
2. if a secure endpoint is requested, ask the user to trust the server certificate
3. connect (and browse) in a background thread
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable

from PySide6.QtWidgets import QMessageBox, QWidget

from ..opc import client as opc
from ..opc.browser import browse
from ..profiles import Profile
from ..diagnostics import show_error
from .worker import run_with_progress

log = logging.getLogger("opc2ixon.connect")


class ConnectFlow:
    def __init__(
        self,
        parent: QWidget,
        profile: Profile,
        password: str,
        do_browse: bool,
        on_done: Callable[[object], None],
    ):
        self.parent = parent
        self.profile = profile.copy()
        self.password = password
        self.do_browse = do_browse
        self.on_done = on_done
        self._worker = None
        self._probe: opc.ProbeResult | None = None

    def start(self):
        p = self.profile
        log.info(
            "%s %s | security %s/%s | auth %s | start node %s | arrays %s",
            "Browse" if self.do_browse else "Test connection",
            p.endpoint, p.security_policy, p.security_mode, p.auth,
            p.start_node or "Objects", "expand" if p.expand_arrays else "skip",
        )
        async def probe_job(w):
            w.progress.emit(f"Contacting {self.profile.endpoint}…")
            return await opc.probe(self.profile)

        self._worker = run_with_progress(self.parent, "Connecting…", probe_job, self._after_probe, self._error)

    def _secure(self) -> bool:
        return self.profile.security_policy != "None" and self.profile.security_mode != "None"

    def _after_probe(self, pr: opc.ProbeResult):
        self._probe = pr
        log.info("Endpoints offered: %s", pr.summary().replace("\n", " "))
        server_cert = None
        if self._secure():
            if pr.selected is None:
                self._error(
                    f"The server does not offer the endpoint "
                    f"{self.profile.security_policy} / {self.profile.security_mode}.\n\n"
                    f"Available endpoints:\n{pr.summary()}"
                )
                return
            server_cert = pr.selected.certificate
            if not opc.is_trusted(server_cert):
                box = QMessageBox(self.parent)
                box.setIcon(QMessageBox.Icon.Question)
                box.setWindowTitle("Untrusted server certificate")
                box.setText("The server presented a certificate that is not trusted yet.\nDo you trust this server?")
                box.setInformativeText(opc.describe_cert(server_cert))
                trust_btn = box.addButton("Trust", QMessageBox.ButtonRole.AcceptRole)
                box.addButton(QMessageBox.StandardButton.Cancel)
                box.exec()
                if box.clickedButton() is not trust_btn:
                    log.info("Server certificate not trusted by the user")
                    return
                opc.trust(server_cert)
                log.info("Server certificate trusted: %s", opc.thumbprint(server_cert))
        elif pr.selected is None:
            self._error(
                "The server does not offer an unsecured endpoint (None / None).\n"
                "Choose a security policy in the Security tab.\n\n"
                f"Available endpoints:\n{pr.summary()}"
            )
            return

        profile, password, do_browse = self.profile, self.password, self.do_browse

        async def main_job(w):
            w.progress.emit("Opening session…")
            cl = await opc.connect(profile, password, server_cert)
            log.info("Session opened")
            try:
                if not do_browse:
                    return "ok"
                t0 = time.monotonic()
                root = await browse(
                    cl, profile, progress=lambda m, n: w.progress.emit(m), cancelled=w.is_cancelled
                )
                variables = [n for n in root.iter_all() if n.node_class == "Variable"]
                log.info(
                    "Browse done in %.1f s: %d variables, %d exportable",
                    time.monotonic() - t0, len(variables), sum(1 for n in variables if n.exportable),
                )
                return root
            finally:
                try:
                    await asyncio.wait_for(cl.disconnect(), 5)
                except Exception:
                    pass

        title = "Browsing server…" if do_browse else "Connecting…"
        self._worker = run_with_progress(self.parent, title, main_job, self._success, self._error)

    def _success(self, result):
        if not self.do_browse:
            QMessageBox.information(
                self.parent,
                "Connection OK",
                f"Connected to {self.profile.endpoint}.\n\nEndpoints offered by the server:\n"
                f"{self._probe.summary() if self._probe else ''}",
            )
        self.on_done(result)

    def _error(self, msg: str):
        log.warning("Connection failed: %s", msg)
        show_error(self.parent, "Connection failed", msg)
