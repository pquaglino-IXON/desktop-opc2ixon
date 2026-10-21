"""Run asyncua coroutines in a background thread so the UI never freezes."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable

from PySide6.QtCore import QThread, Signal
from PySide6.QtWidgets import QProgressDialog, QWidget

from ..opc.browser import BrowseCancelled
from ..opc.client import ConnectionError_

log = logging.getLogger("opc2ixon.worker")


class AsyncWorker(QThread):
    succeeded = Signal(object)
    failed = Signal(str)
    cancelled_sig = Signal()
    progress = Signal(str)

    def __init__(self, factory: Callable[[AsyncWorker], Awaitable], parent=None):
        super().__init__(parent)
        self._factory = factory
        self._cancel = False

    def cancel(self):
        self._cancel = True

    def is_cancelled(self) -> bool:
        return self._cancel

    def run(self):
        try:
            result = asyncio.run(self._factory(self))
        except BrowseCancelled:
            log.info("Cancelled by the user")
            self.cancelled_sig.emit()
            return
        except (ConnectionError_, ValueError) as e:
            log.debug("Failed: %s", e)  # reported by the caller
            self.failed.emit(str(e))
            return
        except Exception as e:  # unexpected
            log.exception("Unexpected error in background task")
            self.failed.emit(f"Unexpected error – {e.__class__.__name__}: {e}")
            return
        if self._cancel:
            self.cancelled_sig.emit()
        else:
            self.succeeded.emit(result)


def run_with_progress(
    parent: QWidget,
    title: str,
    factory: Callable[[AsyncWorker], Awaitable],
    on_success: Callable[[object], None],
    on_error: Callable[[str], None],
) -> AsyncWorker:
    """Start a worker showing a modal busy dialog with a Cancel button."""
    dlg = QProgressDialog(title, "Cancel", 0, 0, parent)
    dlg.setWindowTitle(title)
    dlg.setMinimumDuration(300)
    dlg.setMinimumWidth(380)
    dlg.setAutoClose(False)
    dlg.setAutoReset(False)
    worker = AsyncWorker(factory, parent)
    worker.progress.connect(dlg.setLabelText)
    dlg.canceled.connect(worker.cancel)

    def done():
        dlg.close()
        dlg.deleteLater()

    worker.succeeded.connect(lambda r: (done(), on_success(r)))
    worker.failed.connect(lambda m: (done(), on_error(m)))
    worker.cancelled_sig.connect(done)
    worker.finished.connect(worker.deleteLater)
    worker.start()
    dlg.show()
    return worker
