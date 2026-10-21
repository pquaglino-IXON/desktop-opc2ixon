"""Application entry point: wires the profile window and the browser window."""

from __future__ import annotations

import logging
import sys

from PySide6.QtWidgets import QApplication

from . import APP_NAME, __version__
from .diagnostics import install_excepthook, setup_logging
from .profiles import Profile, migrate_from_old_name
from .ui.browser_window import BrowserWindow
from .ui.connect_flow import ConnectFlow
from .ui.profile_window import ProfileWindow


class Controller:
    def __init__(self):
        self.profiles = ProfileWindow()
        self.profiles.browsed.connect(self.show_browser)
        self.browser: BrowserWindow | None = None
        self._password = ""
        self._flow = None

    def start(self):
        self.profiles.show()

    def show_browser(self, profile: Profile, root, password: str):
        self._password = password
        old = self.browser
        self.browser = BrowserWindow(profile, root)
        self.browser.back_requested.connect(self.back)
        self.browser.refresh_requested.connect(self.refresh)
        self.browser.start_node_chosen.connect(self.profiles.set_start_node)
        self.browser.confirm_close = self.profiles.maybe_save
        if old is not None:
            # keep check states and edited values across a refresh
            self.browser.restore(old.snapshot())
            self.browser.setGeometry(old.geometry())
            old.confirm_close = None
            old.close()
            old.deleteLater()
        self.profiles.hide()
        self.browser.show()

    def back(self):
        if self.browser:
            self.browser.confirm_close = None  # going back to the profiles, not quitting
            self.browser.close()
            self.browser.deleteLater()
            self.browser = None
        self.profiles.show()

    def refresh(self):
        if not self.browser:
            return
        prof = self.browser.profile  # the values used for this browse (start node kept up to date)

        def done(root):
            if root is not None:
                self.show_browser(prof, root, self._password)

        self._flow = ConnectFlow(self.browser, prof, self._password, True, done)
        self._flow.start()


def _selftest(endpoint: str, policy: str = "None", mode: str = "None") -> int:
    """Hidden check used after a build:
    ``<app> --selftest opc.tcp://host:port [Basic256Sha256 SignAndEncrypt]``.

    Connects (anonymous), browses and prints the number of variables found.
    """
    import asyncio
    import logging

    from .opc.browser import browse
    from .opc.client import connect, probe

    async def run():
        p = Profile(endpoint=endpoint, security_policy=policy, security_mode=mode)
        pr = await probe(p)
        cl = await connect(p, "", pr.selected.certificate if pr.selected else None)
        try:
            return await browse(cl, p)
        finally:
            await cl.disconnect()

    try:
        root = asyncio.run(run())
    except Exception as e:
        logging.getLogger("opc2ixon").exception("Self test failed")
        print(f"SELFTEST FAILED: {e}", flush=True)
        return 1
    n = sum(1 for x in root.iter_all() if x.exportable)
    print(f"SELFTEST OK: {n} exportable variables", flush=True)
    return 0


def main() -> int:
    setup_logging()
    install_excepthook()
    try:
        summary = migrate_from_old_name()
        if summary:
            logging.getLogger("opc2ixon").info(summary)
    except Exception:
        logging.getLogger("opc2ixon").warning("Migration from the old name failed", exc_info=True)
    if len(sys.argv) >= 3 and sys.argv[1] == "--selftest":
        return _selftest(*sys.argv[2:5])
    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setApplicationVersion(__version__)
    ctl = Controller()
    ctl.start()
    return app.exec()
