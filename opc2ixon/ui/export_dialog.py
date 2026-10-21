"""Step 3: preview of the CSV rows and save."""

from __future__ import annotations

import logging
from pathlib import Path

from PySide6.QtCore import QUrl
from PySide6.QtGui import QBrush, QColor, QDesktopServices
from PySide6.QtWidgets import (
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

from ..diagnostics import show_error
from ..export.ixon_csv import HEADER, Selection, build_export, write_csv
from ..profiles import Profile

log = logging.getLogger("opc2ixon.export")

RENAMED = QColor(255, 240, 190)
SHORTENED = QColor(214, 234, 255)


class ExportDialog(QDialog):
    def __init__(self, parent, selections: list[Selection], profile: Profile):
        super().__init__(parent)
        self.setWindowTitle("Export IXON variables CSV")
        self.resize(1150, 620)
        self.profile = profile
        self.rows = build_export(selections, profile.name_separator, profile.default_factor)

        v = QVBoxLayout(self)
        n_ren = sum(1 for r in self.rows if r.renamed)
        n_short = sum(1 for r in self.rows if r.shortened)
        text = f"<b>{len(self.rows)} variables</b>"
        if n_short:
            text += (
                f" · <span style='background:#d6eaff'>{n_short} identifiers longer than 64 characters "
                "were shortened</span>"
            )
        if n_ren:
            text += (
                f" · <span style='background:#fff0be'>{n_ren} duplicate identifiers got a code "
                "suffix</span>"
            )
        if n_short or n_ren:
            text += " &nbsp;(hover the Identifier for details)"
        v.addWidget(QLabel(text))
        note = QLabel(
            "When importing, IXON adds the new variables and skips those whose Identifier already exists."
        )
        note.setStyleSheet("color: gray")
        v.addWidget(note)

        t = QTableWidget(len(self.rows), len(HEADER))
        t.setHorizontalHeaderLabels(HEADER)
        t.verticalHeader().setVisible(False)
        t.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        for i, er in enumerate(self.rows):
            for j, val in enumerate(er.row.as_list()):
                item = QTableWidgetItem(val)
                if j == 0 and (er.renamed or er.shortened):
                    item.setBackground(QBrush(RENAMED if er.renamed else SHORTENED))
                    item.setToolTip("\n".join(er.notes))
                t.setItem(i, j, item)
        t.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        t.horizontalHeader().setStretchLastSection(True)
        v.addWidget(t, 1)

        btns = QHBoxLayout()
        btns.addStretch(1)
        cancel = QPushButton("Cancel")
        cancel.clicked.connect(self.reject)
        btns.addWidget(cancel)
        save = QPushButton("Save as…")
        save.setDefault(True)
        save.clicked.connect(self._save_as)
        btns.addWidget(save)
        v.addLayout(btns)

    def _save_as(self):
        safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in self.profile.name)
        path, _ = QFileDialog.getSaveFileName(
            self, "Save IXON variables CSV", str(Path.home() / f"variables_{safe}.csv"), "CSV (*.csv)"
        )
        if not path:
            return
        if not path.lower().endswith(".csv"):
            path += ".csv"
        path = Path(path)
        try:
            write_csv(path, [r.row for r in self.rows])
        except OSError as e:
            log.warning("Save failed %s: %s", path, e)
            show_error(self, "Save failed", f"Cannot write {path}:\n{e}")
            return
        log.info(
            "CSV saved: %s (%d rows, %d shortened, %d deduplicated)",
            path, len(self.rows), sum(r.shortened for r in self.rows), sum(r.renamed for r in self.rows),
        )
        box = QMessageBox(self)
        box.setWindowTitle("CSV saved")
        box.setText(f"{len(self.rows)} variables saved to\n{path}")
        open_btn = box.addButton("Open folder", QMessageBox.ButtonRole.ActionRole)
        box.addButton(QMessageBox.StandardButton.Ok)
        box.exec()
        if box.clickedButton() is open_btn:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(path.parent)))
        self.accept()
