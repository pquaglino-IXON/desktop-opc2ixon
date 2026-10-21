"""Logging, unexpected-error handling and diagnostic info.

Log file (rotating, max 3 x 1 MB):
  Windows: %LOCALAPPDATA%\\opc2ixon\\Logs\\opc2ixon.log
  macOS:   ~/Library/Logs/opc2ixon/opc2ixon.log
  Linux:   ~/.local/state/opc2ixon/log/opc2ixon.log
"""

from __future__ import annotations

import logging
import platform
import sys
import threading
import traceback
from logging.handlers import RotatingFileHandler
from pathlib import Path

from platformdirs import user_log_dir

from . import APP_ID, APP_NAME, DISCLAIMER, __version__

log = logging.getLogger("opc2ixon")


def log_dir() -> Path:
    p = Path(user_log_dir(APP_ID, appauthor=False))
    p.mkdir(parents=True, exist_ok=True)
    return p


def log_file() -> Path:
    return log_dir() / "opc2ixon.log"


def setup_logging() -> None:
    handler = RotatingFileHandler(log_file(), maxBytes=1_000_000, backupCount=2, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s"))
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(logging.INFO)
    if sys.stderr is not None and not getattr(sys, "frozen", False):
        console = logging.StreamHandler()
        console.setLevel(logging.ERROR)
        root.addHandler(console)
    logging.getLogger("asyncua").setLevel(logging.WARNING)
    # in packaged (windowed) apps stdout/stderr may be missing: send them to the log
    if sys.stdout is None or sys.stderr is None:
        stream = open(log_dir() / "console.log", "a", encoding="utf-8", buffering=1)  # noqa: SIM115
        sys.stdout = sys.stdout or stream
        sys.stderr = sys.stderr or stream
    log.info("=" * 60)
    log.info("%s %s started", APP_NAME, __version__)
    log.info("%s", _environment().replace("\n", " | "))


def _versions() -> dict[str, str]:
    out = {}
    try:
        import PySide6
        from PySide6.QtCore import qVersion

        out["PySide6"] = PySide6.__version__
        out["Qt"] = qVersion()
    except Exception:
        pass
    try:
        from importlib.metadata import version

        out["asyncua"] = version("asyncua")
    except Exception:
        pass
    return out


def _environment() -> str:
    lines = [
        f"App: {APP_NAME} {__version__}" + (" (packaged)" if getattr(sys, "frozen", False) else ""),
        f"OS: {platform.platform()} ({platform.machine()})",
        f"Python: {platform.python_version()}",
    ]
    lines += [f"{k}: {v}" for k, v in _versions().items()]
    return "\n".join(lines)


def tail_log(lines: int = 150) -> str:
    try:
        text = log_file().read_text(encoding="utf-8", errors="replace").splitlines()
        return "\n".join(text[-lines:])
    except OSError:
        return "(log not available)"


def diagnostic_info(extra: str = "") -> str:
    parts = ["=== opc2ixon – diagnostic info ===", _environment(), f"Log file: {log_file()}"]
    if extra:
        parts += ["", "=== Error ===", extra]
    parts += ["", "=== Last log lines ===", tail_log()]
    return "\n".join(parts)


# ---- dialogs ---------------------------------------------------------------------
def show_error(parent, title: str, message: str, details: str = "") -> None:
    """Error box with 'Copy diagnostic info' and 'Open log folder' buttons."""
    from PySide6.QtCore import QUrl
    from PySide6.QtGui import QDesktopServices, QGuiApplication
    from PySide6.QtWidgets import QMessageBox

    box = QMessageBox(parent)
    box.setIcon(QMessageBox.Icon.Critical)
    box.setWindowTitle(title)
    box.setText(message)
    if details:
        box.setDetailedText(details)
    copy_btn = box.addButton("Copy diagnostic info", QMessageBox.ButtonRole.ActionRole)
    folder_btn = box.addButton("Open log folder", QMessageBox.ButtonRole.ActionRole)
    box.addButton(QMessageBox.StandardButton.Ok)
    while True:
        box.exec()
        clicked = box.clickedButton()
        if clicked is copy_btn:
            QGuiApplication.clipboard().setText(diagnostic_info(f"{title}\n{message}\n{details}".strip()))
            copy_btn.setText("Copied ✓")
            continue  # keep the box open
        if clicked is folder_btn:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(log_dir())))
            continue
        break


def show_about(parent) -> None:
    from PySide6.QtCore import QUrl
    from PySide6.QtGui import QDesktopServices, QGuiApplication
    from PySide6.QtWidgets import QMessageBox

    box = QMessageBox(parent)
    box.setWindowTitle(f"About {APP_NAME}")
    box.setText(f"<b>{APP_NAME}</b> {__version__}")
    box.setInformativeText(
        "Browse an OPC UA server and export variables as an IXON Cloud CSV.\n\n"
        f"{DISCLAIMER}\n\n"
        f"{_environment()}\n\nLog file:\n{log_file()}"
    )
    copy_btn = box.addButton("Copy diagnostic info", QMessageBox.ButtonRole.ActionRole)
    folder_btn = box.addButton("Open log folder", QMessageBox.ButtonRole.ActionRole)
    box.addButton(QMessageBox.StandardButton.Close)
    while True:
        box.exec()
        clicked = box.clickedButton()
        if clicked is copy_btn:
            QGuiApplication.clipboard().setText(diagnostic_info())
            copy_btn.setText("Copied ✓")
            continue
        if clicked is folder_btn:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(log_dir())))
            continue
        break


# ---- unexpected errors ---------------------------------------------------------------
_in_handler = False


def _excepthook(exc_type, exc, tb):
    global _in_handler
    if issubclass(exc_type, KeyboardInterrupt):
        sys.__excepthook__(exc_type, exc, tb)
        return
    details = "".join(traceback.format_exception(exc_type, exc, tb))
    log.error("Unexpected error:\n%s", details)
    if _in_handler:
        return
    _in_handler = True
    try:
        from PySide6.QtWidgets import QApplication

        if QApplication.instance() is not None and threading.current_thread() is threading.main_thread():
            show_error(
                QApplication.activeWindow(),
                "Unexpected error",
                f"Something went wrong: {exc_type.__name__}: {exc}\n\n"
                "The application can usually keep running. If the problem persists, use "
                "'Copy diagnostic info' and send it to the person who provided this tool "
                "(IXON Support does not handle it).",
                details,
            )
    except Exception:
        log.exception("Error while showing the error dialog")
    finally:
        _in_handler = False


def install_excepthook() -> None:
    sys.excepthook = _excepthook

    def thread_hook(args):
        if args.exc_type is SystemExit:
            return
        log.error(
            "Unexpected error in thread %s:\n%s",
            getattr(args.thread, "name", "?"),
            "".join(traceback.format_exception(args.exc_type, args.exc_value, args.exc_traceback)),
        )

    threading.excepthook = thread_hook
