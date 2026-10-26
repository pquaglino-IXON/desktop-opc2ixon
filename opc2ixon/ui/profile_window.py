"""Step 1: choose / edit a profile, then connect."""

from __future__ import annotations

import asyncio
import shutil
from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QDoubleValidator
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QSplitter,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from .. import APP_NAME, __version__
from ..opc import client as opc
from ..profiles import AUTH_MODES, SECURITY_MODES, SECURITY_POLICIES, Profile, ProfileStore
from ..diagnostics import show_about, show_error
from .connect_flow import ConnectFlow

AUTH_LABELS = {"anonymous": "Anonymous", "username": "Username / password", "certificate": "User certificate"}


def _path_row(edit: QLineEdit, on_browse) -> QWidget:
    w = QWidget()
    h = QHBoxLayout(w)
    h.setContentsMargins(0, 0, 0, 0)
    h.addWidget(edit, 1)
    b = QPushButton("Browse…")
    b.clicked.connect(on_browse)
    h.addWidget(b)
    return w


def _form(w: QWidget) -> QFormLayout:
    """Form whose fields use all the available width (also on macOS)."""
    f = QFormLayout(w)
    f.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
    f.setLabelAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
    return f


def normalize_endpoint(text: str) -> str:
    """Add the opc.tcp:// scheme when the user typed only host[:port][/path]."""
    t = text.strip()
    if not t:
        return t
    if "://" not in t:
        t = "opc.tcp://" + t
    elif not t.lower().startswith("opc.tcp://"):
        scheme, rest = t.split("://", 1)
        if scheme.lower() in ("opc", "tcp", "opctcp", "opc-tcp"):
            t = "opc.tcp://" + rest
    return t


class ProfileWindow(QMainWindow):
    browsed = Signal(object, object, str)  # (profile, root BrowseNode, password)

    def __init__(self, store: ProfileStore | None = None):
        super().__init__()
        self.store = store or ProfileStore()
        self.setWindowTitle(f"{APP_NAME} {__version__} – Profiles[*]")
        self.resize(1000, 600)
        self._current_name: str | None = None
        self._loading = False
        self._flow = None
        self._session_pw: dict[str, str] = {}  # passwords typed in this session (not remembered)
        self._snapshot: dict | None = None  # profile as saved, to detect unsaved changes
        self._snapshot_pw = ""

        splitter = QSplitter()
        self.setCentralWidget(splitter)

        # ---- left: list ------------------------------------------------------
        left = QWidget()
        lv = QVBoxLayout(left)
        lv.addWidget(QLabel("<b>Profiles</b>"))
        self.list = QListWidget()
        self.list.currentItemChanged.connect(self._on_select)
        lv.addWidget(self.list, 1)
        row1 = QHBoxLayout()
        for text, slot in [("New", self._new), ("Duplicate", self._duplicate), ("Delete", self._delete)]:
            b = QPushButton(text)
            b.clicked.connect(slot)
            row1.addWidget(b)
        lv.addLayout(row1)
        row2 = QHBoxLayout()
        for text, slot in [("Import…", self._import), ("Export…", self._export)]:
            b = QPushButton(text)
            b.clicked.connect(slot)
            row2.addWidget(b)
        lv.addLayout(row2)
        splitter.addWidget(left)

        # ---- right: form -----------------------------------------------------
        right = QWidget()
        rv = QVBoxLayout(right)
        self.tabs = QTabWidget()
        rv.addWidget(self.tabs, 1)
        self._build_connection_tab()
        self._build_security_tab()
        self._build_browse_tab()
        self._build_export_tab()

        buttons = QHBoxLayout()
        self.save_btn = QPushButton("Save")
        self.save_btn.clicked.connect(lambda: self._save(show=True))
        self.save_btn.setShortcut("Ctrl+S")
        self.test_btn = QPushButton("Test connection")
        self.test_btn.clicked.connect(lambda: self._connect(False))
        self.go_btn = QPushButton("Connect && Browse  ▶")
        self.go_btn.setDefault(True)
        self.go_btn.clicked.connect(lambda: self._connect(True))
        buttons.addWidget(self.save_btn)
        about_btn = QPushButton("About…")
        about_btn.setToolTip("Version, log file and diagnostic info")
        about_btn.clicked.connect(lambda: show_about(self))
        buttons.addWidget(about_btn)
        buttons.addStretch(1)
        buttons.addWidget(self.test_btn)
        buttons.addWidget(self.go_btn)
        rv.addLayout(buttons)
        splitter.addWidget(right)
        splitter.setSizes([240, 760])

        self._watch_changes()
        self._reload_list()

    # ---- unsaved changes -----------------------------------------------------------
    def _watch_changes(self):
        for w in (self.f_name, self.f_endpoint, self.f_user, self.f_pass, self.f_cert, self.f_key,
                  self.f_start, self.f_factor):
            w.textChanged.connect(self._update_dirty)
        for w in (self.f_policy, self.f_mode, self.f_auth, self.f_sep):
            w.currentTextChanged.connect(self._update_dirty)
        for w in (self.f_remember, self.f_ns0, self.f_arrays, self.f_units):
            w.toggled.connect(self._update_dirty)
        for w in (self.f_timeout, self.f_depth, self.f_maxnodes, self.f_strlen):
            w.valueChanged.connect(self._update_dirty)

    def _mark_clean(self):
        self._snapshot = self.current_profile().to_dict()
        self._snapshot_pw = self.f_pass.text()
        self._update_dirty()

    def is_dirty(self) -> bool:
        if self._current_name is None or self._snapshot is None:
            return False
        if self.current_profile().to_dict() != self._snapshot:
            return True
        return self.f_remember.isChecked() and self.f_pass.text() != self._snapshot_pw

    def _update_dirty(self, *_):
        if self._loading:
            return
        dirty = self.is_dirty()
        self.save_btn.setEnabled(dirty)
        self.setWindowModified(dirty)
        if self._current_name is not None and not self.f_remember.isChecked():
            self._session_pw[self._current_name] = self.f_pass.text()

    def maybe_save(self, question: str = "", allow_dont_save: bool = True) -> bool:
        """Ask to save unsaved changes. Returns False if the user cancelled."""
        if not self.is_dirty():
            return True
        box = QMessageBox(self if self.isVisible() else QApplication.activeWindow())
        box.setIcon(QMessageBox.Icon.Question)
        box.setWindowTitle("Unsaved changes")
        box.setText(f"The profile '{self._current_name}' has unsaved changes.")
        box.setInformativeText(question or "Do you want to save them?")
        save = box.addButton(QMessageBox.StandardButton.Save)
        dont = box.addButton("Don't Save", QMessageBox.ButtonRole.DestructiveRole) if allow_dont_save else None
        box.addButton(QMessageBox.StandardButton.Cancel)
        box.setDefaultButton(save)
        box.exec()
        clicked = box.clickedButton()
        if clicked is save:
            return self._save(reload=False) is not None
        if dont is not None and clicked is dont:
            return True
        return False

    def closeEvent(self, event):
        if self.maybe_save():
            event.accept()
        else:
            event.ignore()

    # ---- tabs ------------------------------------------------------------------
    def _build_connection_tab(self):
        w = QWidget()
        f = _form(w)
        self.f_name = QLineEdit()
        self.f_endpoint = QLineEdit()
        self.f_endpoint.setPlaceholderText("opc.tcp://192.168.1.10:4840  (opc.tcp:// is added automatically)")
        self.f_endpoint.editingFinished.connect(
            lambda: self.f_endpoint.setText(normalize_endpoint(self.f_endpoint.text()))
        )
        self.f_timeout = QDoubleSpinBox()
        self.f_timeout.setRange(1, 120)
        self.f_timeout.setSuffix(" s")
        f.addRow("Profile name", self.f_name)
        f.addRow("Server address", self.f_endpoint)
        f.addRow("Timeout", self.f_timeout)
        hint = QLabel(
            "Siemens S7-1500 default port: 4840 · Beckhoff TwinCAT default port: 4840\n"
            "The address can include a path, e.g. opc.tcp://host:4840/freeopcua/server/"
        )
        hint.setStyleSheet("color: gray")
        f.addRow("", hint)
        self.tabs.addTab(w, "Connection")

    def _build_security_tab(self):
        w = QWidget()
        f = _form(w)
        self.f_policy = QComboBox()
        self.f_policy.addItems(SECURITY_POLICIES)
        self.f_mode = QComboBox()
        self.f_mode.addItems(SECURITY_MODES)
        self.f_policy.currentTextChanged.connect(self._sync_security)
        self.f_auth = QComboBox()
        for a in AUTH_MODES:
            self.f_auth.addItem(AUTH_LABELS[a], a)
        self.f_auth.currentIndexChanged.connect(self._sync_security)
        self.f_user = QLineEdit()
        self.f_pass = QLineEdit()
        self.f_pass.setEchoMode(QLineEdit.EchoMode.Password)
        self.f_remember = QCheckBox("Remember password (stored in the system keychain)")
        self.f_cert = QLineEdit()
        self.f_key = QLineEdit()
        f.addRow("Security policy", self.f_policy)
        f.addRow("Security mode", self.f_mode)
        f.addRow("Authentication", self.f_auth)
        f.addRow("Username", self.f_user)
        f.addRow("Password", self.f_pass)
        f.addRow("", self.f_remember)
        f.addRow("User certificate", _path_row(self.f_cert, lambda: self._pick(self.f_cert, "Certificate (*.der *.pem *.crt)")))
        f.addRow("User private key", _path_row(self.f_key, lambda: self._pick(self.f_key, "Private key (*.pem *.key *.der)")))
        self.lbl_keypass = QLabel("For a user certificate, the password field is the private key password (optional).")
        self.lbl_keypass.setStyleSheet("color: gray")
        f.addRow("", self.lbl_keypass)
        exp = QPushButton("Export client certificate…")
        exp.setToolTip(
            "This application's own certificate. With secure connections, some PLCs (e.g. Siemens) "
            "require it to be added to the PLC's trusted certificates."
        )
        exp.clicked.connect(self._export_client_cert)
        f.addRow("Application certificate", exp)
        self.tabs.addTab(w, "Security")

    def _build_browse_tab(self):
        w = QWidget()
        f = _form(w)
        self.f_start = QLineEdit()
        self.f_start.setPlaceholderText("Objects folder (default) – or a NodeId, e.g. ns=3;s=PLC")
        self.f_ns0 = QCheckBox("Ignore namespace 0 (OPC UA server diagnostics)")
        self.f_arrays = QCheckBox("Expand arrays into single elements")
        self.f_depth = QSpinBox()
        self.f_depth.setRange(1, 100)
        self.f_maxnodes = QSpinBox()
        self.f_maxnodes.setRange(0, 10000)
        self.f_maxnodes.setSpecialValueText("Auto")
        self.f_maxnodes.setToolTip(
            "Maximum number of nodes asked to the server in one request.\n"
            "Auto: uses the limits declared by the server and automatically retries with smaller\n"
            "requests if the server answers BadTooManyOperations. Set a number only if Auto fails."
        )
        f.addRow("Start node", self.f_start)
        f.addRow("", self.f_ns0)
        f.addRow("Arrays", self.f_arrays)
        f.addRow("Max depth", self.f_depth)
        f.addRow("Max nodes per request", self.f_maxnodes)
        hint = QLabel(
            "Tip: after browsing, right-click a folder → 'Use as start node' to store it in this profile."
        )
        hint.setStyleSheet("color: gray")
        hint.setWordWrap(True)
        f.addRow("", hint)
        self.tabs.addTab(w, "Browse")

    def _build_export_tab(self):
        w = QWidget()
        f = _form(w)
        self.f_strlen = QSpinBox()
        self.f_strlen.setRange(1, 65535)
        self.f_units = QCheckBox("Read units from the server (EngineeringUnits)")
        self.f_factor = QLineEdit()
        self.f_factor.setPlaceholderText("empty = no factor (editable per variable)")
        v = QDoubleValidator()
        v.setLocale(self.locale().c())
        self.f_factor.setValidator(v)
        self.f_sep = QComboBox()
        self.f_sep.setEditable(True)
        self.f_sep.addItems([".", "/", "_", " - ", " > "])
        f.addRow("Default max string length", self.f_strlen)
        f.addRow("Units", self.f_units)
        f.addRow("Default factor", self.f_factor)
        f.addRow("Name path separator", self.f_sep)
        self.lbl_example = QLabel()
        self.lbl_example.setStyleSheet("color: gray")
        f.addRow("Example", self.lbl_example)
        self.f_sep.currentTextChanged.connect(self._update_example)
        self.tabs.addTab(w, "Export")

    def _update_example(self):
        from ..opc.naming import make_identifier, make_name

        path = ["PLC_1", "DB_Motor", "Speed 1"]
        self.lbl_example.setText(
            f"Name: {make_name(path, self.f_sep.currentText() or '.')}    Identifier: {make_identifier(path)}"
        )

    def _sync_security(self):
        none = self.f_policy.currentText() == "None"
        if none:
            self.f_mode.setCurrentText("None")
        elif self.f_mode.currentText() == "None":
            self.f_mode.setCurrentText("SignAndEncrypt")
        self.f_mode.setEnabled(not none)
        auth = self.f_auth.currentData()
        self.f_user.setEnabled(auth == "username")
        self.f_pass.setEnabled(auth in ("username", "certificate"))
        self.f_remember.setEnabled(auth in ("username", "certificate"))
        self.f_cert.setEnabled(auth == "certificate")
        self.f_key.setEnabled(auth == "certificate")
        self.lbl_keypass.setVisible(auth == "certificate")

    def _pick(self, edit: QLineEdit, flt: str):
        path, _ = QFileDialog.getOpenFileName(self, "Select file", edit.text(), flt + ";;All files (*)")
        if path:
            edit.setText(path)

    def _export_client_cert(self):
        cert, _ = asyncio.run(opc.ensure_client_certificate())
        path, _ = QFileDialog.getSaveFileName(
            self, "Export client certificate", str(Path.home() / "opc2ixon_client.der"), "Certificate (*.der)"
        )
        if path:
            shutil.copyfile(cert, path)
            QMessageBox.information(self, "Certificate exported", f"Saved to {path}")

    # ---- profile <-> form ------------------------------------------------------
    def _reload_list(self, select: str | None = None):
        self._loading = True
        self.list.clear()
        profiles = self.store.list()
        if not profiles:
            p = Profile(name="Demo server (localhost)", endpoint="opc.tcp://localhost:4840")
            self.store.save(p)
            profiles = [p]
        for p in profiles:
            it = QListWidgetItem(p.name)
            it.setData(Qt.ItemDataRole.UserRole, p.name)
            self.list.addItem(it)
        self._loading = False
        target = select or self._current_name
        row = 0
        for i in range(self.list.count()):
            if self.list.item(i).data(Qt.ItemDataRole.UserRole) == target:
                row = i
        self._current_name = None
        self.list.setCurrentRow(row)

    def _on_select(self, cur: QListWidgetItem | None, prev: QListWidgetItem | None):
        if self._loading:
            return
        if cur is None:
            return
        name = cur.data(Qt.ItemDataRole.UserRole)
        if prev is not None and self.is_dirty():
            before = self._current_name
            if not self.maybe_save("Save them before switching profile?"):
                self.list.blockSignals(True)
                self.list.setCurrentItem(prev)
                self.list.blockSignals(False)
                return
            if self._current_name != before:  # renamed while saving: rebuild the list
                self._current_name = None
                self._reload_list(select=name)
                return
        prof = next((p for p in self.store.list() if p.name == name), None)
        if prof:
            self._fill(prof)

    def _fill(self, p: Profile):
        self._loading = True  # no "dirty" updates while filling the form
        self._current_name = p.name
        self.f_name.setText(p.name)
        self.f_endpoint.setText(p.endpoint)
        self.f_timeout.setValue(p.timeout_s)
        self.f_policy.setCurrentText(p.security_policy)
        self.f_mode.setCurrentText(p.security_mode)
        self.f_auth.setCurrentIndex(max(0, AUTH_MODES.index(p.auth) if p.auth in AUTH_MODES else 0))
        self.f_user.setText(p.username)
        self.f_remember.setChecked(p.remember_password)
        self.f_pass.setText(p.load_password() if p.remember_password else self._session_pw.get(p.name, ""))
        self.f_cert.setText(p.user_cert_path)
        self.f_key.setText(p.user_key_path)
        self.f_start.setText(p.start_node)
        self.f_ns0.setChecked(p.ignore_ns0)
        self.f_arrays.setChecked(p.expand_arrays)
        self.f_depth.setValue(p.max_depth)
        self.f_maxnodes.setValue(p.max_nodes_per_request)
        self.f_strlen.setValue(p.default_str_len)
        self.f_units.setChecked(p.units_from_server)
        self.f_factor.setText(p.default_factor)
        self.f_sep.setCurrentText(p.name_separator)
        self._sync_security()
        self._update_example()
        self._loading = False
        self._mark_clean()

    def current_profile(self) -> Profile:
        return Profile(
            name=self.f_name.text().strip() or "Unnamed",
            endpoint=normalize_endpoint(self.f_endpoint.text()),
            timeout_s=self.f_timeout.value(),
            security_policy=self.f_policy.currentText(),
            security_mode=self.f_mode.currentText(),
            auth=self.f_auth.currentData(),
            username=self.f_user.text(),
            remember_password=self.f_remember.isChecked(),
            user_cert_path=self.f_cert.text().strip(),
            user_key_path=self.f_key.text().strip(),
            start_node=self.f_start.text().strip(),
            ignore_ns0=self.f_ns0.isChecked(),
            expand_arrays=self.f_arrays.isChecked(),
            max_depth=self.f_depth.value(),
            max_nodes_per_request=self.f_maxnodes.value(),
            default_str_len=self.f_strlen.value(),
            units_from_server=self.f_units.isChecked(),
            default_factor=self.f_factor.text().strip().replace(",", "."),
            name_separator=self.f_sep.currentText() or ".",
        )

    def _save(self, show=False, reload=True) -> Profile | None:
        p = self.current_profile()
        if p.name != self._current_name and self.store.exists(p.name):
            QMessageBox.warning(self, "Name in use", f"A profile named '{p.name}' already exists.")
            return None
        self.store.save(p, old_name=self._current_name)
        self._session_pw[p.name] = self.f_pass.text()
        p.save_password(self.f_pass.text() if p.remember_password else "")
        self._current_name = p.name
        self._mark_clean()
        if reload:
            self._reload_list(select=p.name)
        if show:
            self.statusBar().showMessage(f"Profile '{p.name}' saved", 3000)
        return p

    def set_start_node(self, profile_name: str, node_id: str):
        """Called from the browser window."""
        prof = next((p for p in self.store.list() if p.name == profile_name), None)
        if prof is None:
            return
        prof.start_node = node_id
        self.store.save(prof)
        if self._current_name == profile_name:
            self.f_start.setText(node_id)
            if self._snapshot is not None:  # already saved: not an unsaved change
                self._snapshot["start_node"] = node_id
            self._update_dirty()

    # ---- list actions --------------------------------------------------------
    def _unique_name(self, base: str) -> str:
        name, n = base, 2
        while self.store.exists(name):
            name = f"{base} ({n})"
            n += 1
        return name

    def _new(self):
        if not self.maybe_save():
            return
        p = Profile(name=self._unique_name("New profile"))
        self.store.save(p)
        self._current_name = None
        self._reload_list(select=p.name)
        self.tabs.setCurrentIndex(0)
        self.f_name.setFocus()
        self.f_name.selectAll()

    def _duplicate(self):
        if not self.maybe_save("Save them before duplicating? (the copy is made from the saved profile)"):
            return
        p = next((x for x in self.store.list() if x.name == self._current_name), None)
        if p is None:
            return
        q = p.copy()
        q.name = self._unique_name(p.name + " copy")
        self.store.save(q)
        self._current_name = None
        self._reload_list(select=q.name)

    def _delete(self):
        if not self._current_name:
            return
        if QMessageBox.question(self, "Delete profile", f"Delete profile '{self._current_name}'?") != (
            QMessageBox.StandardButton.Yes
        ):
            return
        self.store.delete(self._current_name)
        self._current_name = None
        self._reload_list()

    def _import(self):
        if not self.maybe_save():
            return
        path, _ = QFileDialog.getOpenFileName(self, "Import profile", "", "Profile (*.json)")
        if not path:
            return
        try:
            p = ProfileStore.import_from(Path(path))
        except Exception as e:
            show_error(self, "Import failed", f"Cannot import the profile:\n{e}")
            return
        p.name = self._unique_name(p.name)
        self.store.save(p)
        self._current_name = None
        self._reload_list(select=p.name)

    def _export(self):
        if not self.maybe_save("Save them before exporting? (the saved profile is exported)"):
            return
        p = next((x for x in self.store.list() if x.name == self._current_name), None)
        if p is None:
            return
        path, _ = QFileDialog.getSaveFileName(self, "Export profile", f"{p.name}.json", "Profile (*.json)")
        if path:
            ProfileStore.export_to(p, Path(path))  # password is never exported

    # ---- connect ---------------------------------------------------------------
    def _connect(self, do_browse: bool):
        password = self.f_pass.text()
        if not self.maybe_save(
            "Save them before connecting?\n'Don't Save' uses the values on screen for this "
            "connection only."
        ):
            return
        p = self.current_profile()  # values on screen (saved or not)
        if self._current_name and p.name != self._current_name:
            p.name = self._current_name  # unsaved rename: keep the stored name for 'Use as start node'
        if not p.endpoint.startswith("opc.tcp://"):
            QMessageBox.warning(self, "Invalid address", "The server address must start with opc.tcp://")
            return
        if p.auth == "username" and not password:
            QMessageBox.warning(self, "Password", "Enter the password in the Security tab.")
            self.tabs.setCurrentIndex(1)
            return

        def done(result):
            if do_browse and result is not None:
                self.browsed.emit(p, result, password)

        self._flow = ConnectFlow(self, p, password, do_browse, done)
        self._flow.start()
