"""Step 2: tree of the variables found on the server, selection and export."""

from __future__ import annotations


from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QAction, QBrush, QColor, QGuiApplication
from PySide6.QtWidgets import (
    QCheckBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMenu,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QStyle,
    QStyledItemDelegate,
    QStyleOptionViewItem,
    QTreeWidget,
    QTreeWidgetItem,
    QTreeWidgetItemIterator,
    QVBoxLayout,
    QWidget,
)

from .. import APP_NAME
from ..export.ixon_csv import Selection
from ..opc.model import BrowseNode
from ..opc.naming import make_name
from ..profiles import Profile
from .export_dialog import ExportDialog

COL_NAME, COL_NODEID, COL_OPCTYPE, COL_IXONTYPE, COL_STRLEN, COL_FACTOR, COL_UNIT, COL_STATUS = range(8)
HEADERS = ["Name", "NodeId", "OPC UA type", "IXON type", "Str len", "Factor", "Unit", "Status"]
EDITABLE_TIP = "Double-click to edit"


def normalize_factor(text: str) -> str | None:
    """Return the factor as text with '.' decimal separator, '' for none, None if invalid."""
    t = text.strip().replace(",", ".")
    if not t:
        return ""
    try:
        float(t)
    except ValueError:
        return None
    return t
ROLE_NODE = Qt.ItemDataRole.UserRole
ROLE_KIND = Qt.ItemDataRole.UserRole + 1  # "leaf" | "group" | "info"

GREY = QBrush(QColor(150, 150, 150))


class _Delegate(QStyledItemDelegate):
    """Only 'Str len' (for strings), 'Factor' and 'Unit' are editable."""

    def createEditor(self, parent, option, index):
        item_kind = index.siblingAtColumn(0).data(ROLE_KIND)
        node: BrowseNode | None = index.siblingAtColumn(0).data(ROLE_NODE)
        if item_kind != "leaf" or node is None:
            return None
        if index.column() == COL_STRLEN:
            if node.ixon_type is None or node.ixon_type.type != "str":
                return None
            sb = QSpinBox(parent)
            sb.setRange(1, 65535)
            return sb
        if index.column() in (COL_UNIT, COL_FACTOR):
            return super().createEditor(parent, option, index)
        return None

    def setEditorData(self, editor, index):
        if isinstance(editor, QSpinBox):
            try:
                editor.setValue(int(index.data() or 255))
            except ValueError:
                editor.setValue(255)
        else:
            super().setEditorData(editor, index)

    def setModelData(self, editor, model, index):
        if isinstance(editor, QSpinBox):
            model.setData(index, str(editor.value()))
        elif index.column() == COL_FACTOR:
            value = normalize_factor(editor.text())
            if value is not None:  # ignore invalid numbers
                model.setData(index, value)
        else:
            super().setModelData(editor, model, index)


class _VariableTree(QTreeWidget):
    """Tree with fast checking.

    * press on a checkbox and drag over other rows: they all get the same state ("paint")
    * click the checkbox of a highlighted row: all highlighted rows get the new state
    * Space: toggle all highlighted rows
    """

    def __init__(self, window: BrowserWindow):
        super().__init__()
        self._win = window
        self._paint_state: Qt.CheckState | None = None
        self._painted: set[int] = set()

    def _check_rect_hit(self, pos) -> QTreeWidgetItem | None:
        """Return the item whose checkbox is under ``pos`` (viewport coordinates)."""
        index = self.indexAt(pos)
        if not index.isValid() or index.column() != COL_NAME:
            return None
        item = self.itemFromIndex(index)
        if item is None or not (item.flags() & Qt.ItemFlag.ItemIsUserCheckable):
            return None
        opt = QStyleOptionViewItem()
        try:
            self.initViewItemOption(opt)
        except Exception:
            pass
        opt.rect = self.visualRect(index)
        getter = getattr(self, "itemDelegateForIndex", None) or self.itemDelegate
        getter(index).initStyleOption(opt, index)
        rect = self.style().subElementRect(QStyle.SubElement.SE_ItemViewItemCheckIndicator, opt, self)
        rect = rect.adjusted(-3, -3, 3, 3)  # a bit of tolerance
        return item if rect.contains(pos) else None

    def _start_paint(self, item: QTreeWidgetItem):
        current = item.checkState(COL_NAME)
        self._paint_state = Qt.CheckState.Unchecked if current == Qt.CheckState.Checked else Qt.CheckState.Checked
        if item.isSelected() and len(self.selectedItems()) > 1:
            targets = self.selectedItems()
        else:
            targets = [item]
        self._painted = {id(t) for t in targets}
        self._win.set_checked(targets, self._paint_state)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and not event.modifiers():
            item = self._check_rect_hit(event.position().toPoint())
            if item is not None:
                self._start_paint(item)
                event.accept()
                return
        super().mousePressEvent(event)

    def mouseDoubleClickEvent(self, event):
        # a fast second click on a checkbox must toggle again, not be swallowed
        if event.button() == Qt.MouseButton.LeftButton:
            item = self._check_rect_hit(event.position().toPoint())
            if item is not None:
                self._start_paint(item)
                event.accept()
                return
        super().mouseDoubleClickEvent(event)

    def mouseMoveEvent(self, event):
        if self._paint_state is not None:
            index = self.indexAt(event.position().toPoint())
            item = self.itemFromIndex(index) if index.isValid() else None
            if item is not None and id(item) not in self._painted and (
                item.flags() & Qt.ItemFlag.ItemIsUserCheckable
            ):
                self._painted.add(id(item))
                self._win.set_checked([item], self._paint_state)
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if self._paint_state is not None:
            self._paint_state = None
            self._painted = set()
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Space and self.state() != QTreeWidget.State.EditingState:
            self._win.toggle_highlighted()
            event.accept()
            return
        super().keyPressEvent(event)


class BrowserWindow(QMainWindow):
    back_requested = Signal()
    refresh_requested = Signal()
    start_node_chosen = Signal(str, str)  # profile name, node id

    def __init__(self, profile: Profile, root: BrowseNode):
        super().__init__()
        self.profile = profile
        self.root = root
        self._updating = False
        self.setWindowTitle(f"{APP_NAME} – {profile.name}")
        self.resize(1250, 760)

        central = QWidget()
        self.setCentralWidget(central)
        v = QVBoxLayout(central)

        # ---- top bar ------------------------------------------------------------
        top = QHBoxLayout()
        back = QPushButton("◀ Profiles")
        back.clicked.connect(self.back_requested.emit)
        top.addWidget(back)
        info = QLabel(f"<b>{profile.name}</b> · {profile.endpoint}")
        info.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        top.addWidget(info, 1)
        refresh = QPushButton("⟳ Refresh")
        refresh.clicked.connect(self.refresh_requested.emit)
        top.addWidget(refresh)
        v.addLayout(top)

        # ---- filter bar ---------------------------------------------------------
        bar = QHBoxLayout()
        self.filter = QLineEdit()
        self.filter.setPlaceholderText("Filter by name or NodeId…")
        self.filter.setClearButtonEnabled(True)
        self.filter.textChanged.connect(self._apply_filter)
        bar.addWidget(self.filter, 1)
        self.btn_all = QPushButton("Check all")
        self.btn_all.clicked.connect(lambda: self._bulk("all"))
        self.btn_none = QPushButton("Uncheck all")
        self.btn_none.clicked.connect(lambda: self._bulk("none"))
        self.btn_inv = QPushButton("Invert")
        self.btn_inv.clicked.connect(lambda: self._bulk("invert"))
        for b in (self.btn_all, self.btn_none, self.btn_inv):
            bar.addWidget(b)
        exp = QPushButton("Expand all")
        exp.clicked.connect(lambda: self.tree.expandAll())
        col = QPushButton("Collapse all")
        col.clicked.connect(lambda: self.tree.collapseAll())
        bar.addWidget(exp)
        bar.addWidget(col)
        self.show_unsupported = QCheckBox("Show unsupported")
        self.show_unsupported.setChecked(True)
        self.show_unsupported.toggled.connect(self._apply_filter)
        bar.addWidget(self.show_unsupported)
        v.addLayout(bar)

        # ---- tree -------------------------------------------------------------------
        self.tree = _VariableTree(self)
        self.tree.setHeaderLabels(HEADERS)
        self.tree.setSelectionMode(QTreeWidget.SelectionMode.ExtendedSelection)
        self.tree.itemSelectionChanged.connect(self._update_counts)
        self.tree.setUniformRowHeights(True)
        self.tree.setAlternatingRowColors(True)
        self.tree.setItemDelegate(_Delegate(self.tree))
        self.tree.setEditTriggers(
            QTreeWidget.EditTrigger.DoubleClicked | QTreeWidget.EditTrigger.EditKeyPressed
        )
        self.tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self._context_menu)
        self.tree.itemChanged.connect(self._on_item_changed)
        h = self.tree.header()
        h.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        v.addWidget(self.tree, 1)
        tip = QLabel(
            "Tips: click a checkbox and drag over the others to check/uncheck many at once · "
            "highlight rows with drag, Shift+click or Ctrl/⌘+click, then press Space or click one of "
            "their checkboxes to toggle all of them · right-click for more."
        )
        tip.setStyleSheet("color: gray")
        tip.setWordWrap(True)
        v.addWidget(tip)

        # ---- bottom -------------------------------------------------------------
        bottom = QHBoxLayout()
        self.count_label = QLabel()
        bottom.addWidget(self.count_label, 1)
        self.export_btn = QPushButton("Export CSV…  ▶")
        self.export_btn.setDefault(True)
        self.export_btn.clicked.connect(self._export)
        bottom.addWidget(self.export_btn)
        v.addLayout(bottom)

        self._populate()
        # set by the controller: asks to save unsaved profile changes before quitting
        self.confirm_close = None

    def closeEvent(self, event):
        if self.confirm_close is not None and not self.confirm_close():
            event.ignore()
            return
        event.accept()

    # ---- building ---------------------------------------------------------------
    def _populate(self):
        self._updating = True
        self.tree.clear()
        self._leaves: list[QTreeWidgetItem] = []
        self._groups: list[QTreeWidgetItem] = []  # in creation order (parents first)
        self.server_addresses: set[str] = set()
        for child in self.root.children:
            self._add(self.tree.invisibleRootItem(), child)
        # groups: checkbox only if they contain selectable variables
        for g in reversed(self._groups):
            if self._has_checkable(g):
                g.setFlags(g.flags() | Qt.ItemFlag.ItemIsUserCheckable)
                g.setCheckState(COL_NAME, Qt.CheckState.Checked)
        self._updating = False
        self.tree.expandToDepth(1)
        for c, w in [(COL_NAME, 340), (COL_NODEID, 280), (COL_OPCTYPE, 110), (COL_IXONTYPE, 120), (COL_STRLEN, 60), (COL_FACTOR, 70), (COL_UNIT, 60)]:
            self.tree.setColumnWidth(c, w)
        self._update_counts()

    def _add(self, parent: QTreeWidgetItem, node: BrowseNode):
        if node.node_class == "Variable" and node.address:
            self.server_addresses.add(node.address)
        if node.children:
            g = self._make_item(parent, node, "group")
            self._groups.append(g)
            if node.exportable:  # a variable that also has children: add itself as first child
                leaf = self._make_item(g, node, "leaf", label=f"{node.display_name} (value)")
                self._leaves.append(leaf)
            for c in node.children:
                self._add(g, c)
        else:
            kind = "leaf" if node.exportable else "info"
            it = self._make_item(parent, node, kind)
            if kind == "leaf":
                self._leaves.append(it)

    def _make_item(self, parent, node: BrowseNode, kind: str, label: str | None = None) -> QTreeWidgetItem:
        it = QTreeWidgetItem(parent)
        it.setText(COL_NAME, label or node.display_name)
        it.setData(COL_NAME, ROLE_NODE, node)
        it.setData(COL_NAME, ROLE_KIND, kind)
        flags = Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable
        if node.node_class == "Variable":
            it.setText(COL_NODEID, node.node_id)
            it.setText(COL_OPCTYPE, node.opc_type)
            it.setText(COL_IXONTYPE, node.ixon_type.label() if node.ixon_type and kind == "leaf" else "")
        else:
            it.setText(COL_NODEID, node.node_id)
            it.setForeground(COL_NODEID, GREY)
        if kind == "leaf":
            flags |= Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsEditable
            if node.ixon_type and node.ixon_type.type == "str":
                it.setText(COL_STRLEN, str(self.profile.default_str_len))
            it.setText(COL_UNIT, node.unit)
            it.setText(COL_FACTOR, self.profile.default_factor)
            for c in (COL_STRLEN, COL_FACTOR, COL_UNIT):
                it.setToolTip(c, EDITABLE_TIP)
            it.setCheckState(COL_NAME, Qt.CheckState.Checked)  # everything selected by default
            it.setToolTip(COL_NAME, make_name(node.path, self.profile.name_separator))
        if node.unsupported_reason and kind != "leaf":
            it.setText(COL_STATUS, node.unsupported_reason)
            it.setToolTip(COL_NAME, node.unsupported_reason)
            if kind == "info" or not node.children:
                for c in range(len(HEADERS)):
                    it.setForeground(c, GREY)
            else:
                it.setForeground(COL_STATUS, GREY)
        it.setFlags(flags)
        return it

    def _has_checkable(self, item: QTreeWidgetItem) -> bool:
        for i in range(item.childCount()):
            c = item.child(i)
            kind = c.data(COL_NAME, ROLE_KIND)
            if kind == "leaf" or (kind == "group" and c.flags() & Qt.ItemFlag.ItemIsUserCheckable):
                return True
        return False

    # ---- check state propagation ----------------------------------------------
    @staticmethod
    def _checkable(item: QTreeWidgetItem) -> bool:
        return bool(item.flags() & Qt.ItemFlag.ItemIsUserCheckable)

    def _on_item_changed(self, item: QTreeWidgetItem, column: int):
        if self._updating or column != COL_NAME or not self._checkable(item):
            return
        self._updating = True
        try:
            state = item.checkState(COL_NAME)
            if item.data(COL_NAME, ROLE_KIND) == "group":
                if state == Qt.CheckState.PartiallyChecked:
                    state = Qt.CheckState.Checked
                    item.setCheckState(COL_NAME, state)
                self._set_descendants(item, state)
            self._refresh_ancestors(item.parent())
        finally:
            self._updating = False
        self._update_counts()

    def _set_descendants(self, item: QTreeWidgetItem, state: Qt.CheckState):
        for i in range(item.childCount()):
            c = item.child(i)
            if self._checkable(c):
                c.setCheckState(COL_NAME, state)
                self._set_descendants(c, state)

    def _group_state(self, item: QTreeWidgetItem):
        checked = unchecked = False
        for i in range(item.childCount()):
            c = item.child(i)
            if not self._checkable(c):
                continue
            s = c.checkState(COL_NAME)
            if s == Qt.CheckState.Checked:
                checked = True
            elif s == Qt.CheckState.Unchecked:
                unchecked = True
            else:
                checked = unchecked = True
        if checked and unchecked:
            return Qt.CheckState.PartiallyChecked
        return Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked

    def _refresh_ancestors(self, item: QTreeWidgetItem | None):
        while item is not None:
            if self._checkable(item):
                item.setCheckState(COL_NAME, self._group_state(item))
            item = item.parent()

    def _refresh_all_groups(self):
        for g in reversed(self._groups):  # children before parents
            if self._checkable(g):
                g.setCheckState(COL_NAME, self._group_state(g))

    def set_checked(self, items: list[QTreeWidgetItem], state: Qt.CheckState):
        """Set the check state of several items (groups propagate to their children)."""
        if state == Qt.CheckState.PartiallyChecked:
            state = Qt.CheckState.Checked
        self._updating = True
        try:
            for it in items:
                if not self._checkable(it):
                    continue
                it.setCheckState(COL_NAME, state)
                if it.data(COL_NAME, ROLE_KIND) == "group":
                    self._set_descendants(it, state)
            self._refresh_all_groups()
        finally:
            self._updating = False
        self._update_counts()

    def toggle_highlighted(self):
        """Space: if any highlighted row is unchecked, check all of them, otherwise uncheck."""
        items = [i for i in self.tree.selectedItems() if self._checkable(i)]
        if not items:
            return
        any_unchecked = any(i.checkState(COL_NAME) != Qt.CheckState.Checked for i in items)
        self.set_checked(items, Qt.CheckState.Checked if any_unchecked else Qt.CheckState.Unchecked)

    # ---- bulk selection / filter --------------------------------------------
    def _filter_active(self) -> bool:
        return bool(self.filter.text().strip())

    def _bulk(self, op: str):
        self._updating = True
        try:
            for it in self._leaves:
                if not self._visible(it):
                    continue
                if op == "all":
                    s = Qt.CheckState.Checked
                elif op == "none":
                    s = Qt.CheckState.Unchecked
                else:
                    s = (
                        Qt.CheckState.Unchecked
                        if it.checkState(COL_NAME) == Qt.CheckState.Checked
                        else Qt.CheckState.Checked
                    )
                it.setCheckState(COL_NAME, s)
            self._refresh_all_groups()
        finally:
            self._updating = False
        self._update_counts()

    @staticmethod
    def _visible(it: QTreeWidgetItem) -> bool:
        while it is not None:
            if it.isHidden():
                return False
            it = it.parent()
        return True

    def _apply_filter(self):
        text = self.filter.text().strip().lower()
        show_unsup = self.show_unsupported.isChecked()

        def visit(item: QTreeWidgetItem, parent_match: bool) -> bool:
            node: BrowseNode = item.data(COL_NAME, ROLE_NODE)
            kind = item.data(COL_NAME, ROLE_KIND)
            hay = f"{make_name(node.path, '.')} {node.node_id}".lower()
            own_match = bool(text) and text in hay
            match = parent_match or not text or own_match
            if kind == "group":
                visible = False
                for i in range(item.childCount()):
                    if visit(item.child(i), parent_match or own_match):
                        visible = True
            elif kind == "info":
                visible = show_unsup and match
            else:
                visible = match
            item.setHidden(not visible)
            return visible

        root = self.tree.invisibleRootItem()
        for i in range(root.childCount()):
            visit(root.child(i), False)
        if text:
            self.tree.expandAll()
        suffix = " (filtered)" if text else ""
        self.btn_all.setText("Check all" + suffix)
        self.btn_none.setText("Uncheck all" + suffix)
        self.btn_inv.setText("Invert" + suffix)
        self._update_counts()

    def _update_counts(self):
        sel = sum(1 for it in self._leaves if it.checkState(COL_NAME) == Qt.CheckState.Checked)
        unsupported = sum(
            1 for n in self.root.iter_all() if n.node_class == "Variable" and not n.exportable and not n.children
        )
        txt = f"{len(self._leaves)} variables · <b>{sel} checked</b> · {unsupported} unsupported"
        hl = len(self.tree.selectedItems())
        if hl:
            txt += f" · {hl} rows highlighted (Space = check/uncheck them)"
        self.count_label.setText(txt)
        self.export_btn.setEnabled(sel > 0)

    # ---- context menu ----------------------------------------------------------
    def _context_menu(self, pos):
        it = self.tree.itemAt(pos)
        if it is None:
            return
        node: BrowseNode = it.data(COL_NAME, ROLE_NODE)
        menu = QMenu(self)
        hl = [i for i in self.tree.selectedItems() if self._checkable(i)]
        if hl:
            a_chk = QAction(f"Check highlighted ({len(hl)})", menu)
            a_chk.triggered.connect(lambda: self.set_checked(hl, Qt.CheckState.Checked))
            a_unchk = QAction(f"Uncheck highlighted ({len(hl)})", menu)
            a_unchk.triggered.connect(lambda: self.set_checked(hl, Qt.CheckState.Unchecked))
            menu.addAction(a_chk)
            menu.addAction(a_unchk)
            menu.addSeparator()
        a_copy = QAction("Copy NodeId", menu)
        a_copy.triggered.connect(lambda: QGuiApplication.clipboard().setText(node.node_id))
        menu.addAction(a_copy)
        if it.data(COL_NAME, ROLE_KIND) == "group":
            a_start = QAction("Use as start node in this profile", menu)
            a_start.triggered.connect(lambda: self._use_as_start(node))
            menu.addAction(a_start)
        menu.exec(self.tree.viewport().mapToGlobal(pos))

    def _use_as_start(self, node: BrowseNode):
        self.profile.start_node = node.node_id
        self.start_node_chosen.emit(self.profile.name, node.node_id)
        if (
            QMessageBox.question(
                self,
                "Start node saved",
                f"Start node set to {node.node_id} in profile '{self.profile.name}'.\n"
                "Paths and identifiers will now start below this node.\n\nRefresh now?",
            )
            == QMessageBox.StandardButton.Yes
        ):
            self.refresh_requested.emit()

    # ---- export ------------------------------------------------------------------
    def snapshot(self) -> dict[str, tuple]:
        """Check state and edited values per address (used to survive a Refresh)."""
        out = {}
        for it in self._leaves:
            node: BrowseNode = it.data(COL_NAME, ROLE_NODE)
            out[node.address] = (
                it.checkState(COL_NAME), it.text(COL_STRLEN), it.text(COL_FACTOR), it.text(COL_UNIT)
            )
        return out

    def restore(self, snap: dict[str, tuple]):
        self._updating = True
        try:
            for it in self._leaves:
                node: BrowseNode = it.data(COL_NAME, ROLE_NODE)
                v = snap.get(node.address)
                if v is None:
                    continue  # new variable on the server: keep the default (selected)
                state, strlen, factor, unit = v
                it.setCheckState(COL_NAME, state)
                if strlen:
                    it.setText(COL_STRLEN, strlen)
                it.setText(COL_FACTOR, factor)
                it.setText(COL_UNIT, unit)
            self._refresh_all_groups()
        finally:
            self._updating = False
        self._update_counts()

    def selections(self) -> list[Selection]:
        out = []
        for it in self._leaves:
            if it.checkState(COL_NAME) != Qt.CheckState.Checked:
                continue
            node: BrowseNode = it.data(COL_NAME, ROLE_NODE)
            try:
                strlen = int(it.text(COL_STRLEN) or self.profile.default_str_len)
            except ValueError:
                strlen = self.profile.default_str_len
            factor = normalize_factor(it.text(COL_FACTOR)) or ""
            out.append(Selection(node, strlen, it.text(COL_UNIT).strip(), factor))
        return out

    def _export(self):
        dlg = ExportDialog(
            self,
            self.selections(),
            self.profile,
        )
        dlg.exec()


def iter_items(tree: QTreeWidget):
    it = QTreeWidgetItemIterator(tree)
    while it.value():
        yield it.value()
        it += 1
