"""Trash: restore deleted projects, boards and items, or remove them for good."""
from PySide6.QtCore import QSize, Qt
from PySide6.QtWidgets import (QHBoxLayout, QListWidget, QListWidgetItem, QMessageBox, QVBoxLayout, QWidget)

from .. import context, theme
from .common import label, nice_time, push_button

ICONS = {"project": "folder", "board": "board", "evidence": "evidence", "file": "doc", "note": "tool_text",
         "sticky": "sticky", "shape": "shape_round", "frame": "tool_frame"}


class TrashPage(QWidget):
    def __init__(self, main):
        super().__init__()
        self.main = main
        self.setObjectName("Page")
        v = QVBoxLayout(self)
        v.setContentsMargins(40, 30, 40, 30)
        v.setSpacing(12)
        h = QHBoxLayout()
        h.addWidget(label("Trash", "BigTitle"))
        h.addStretch(1)
        self.restore_b = push_button("Restore", "restore")
        self.restore_b.clicked.connect(self.restore)
        self.purge_b = push_button("Delete forever", "delete", name="Danger")
        self.purge_b.clicked.connect(self.purge)
        self.empty_b = push_button("Empty trash", "delete", name="Danger")
        self.empty_b.clicked.connect(self.empty_trash)
        for b in (self.restore_b, self.purge_b, self.empty_b):
            h.addWidget(b)
        v.addLayout(h)
        v.addWidget(label("Deleted things wait here until you empty the trash. Restoring an item also restores the "
                          "strings that were cut with it.", "Muted", wrap=True))
        self.list = QListWidget()
        self.list.setSelectionMode(QListWidget.ExtendedSelection)
        self.list.itemSelectionChanged.connect(self._sel)
        v.addWidget(self.list, 1)

    def refresh(self):
        self.list.clear()
        for d in context.store.trash():
            where = f"  ·  {d['where']}" if d["where"] else ""
            li = QListWidgetItem(theme.icon(ICONS.get(d["type"], "doc"), 18, theme.MUTED),
                                 f"{d['name']}     —  {d['type']}{where}  ·  deleted {nice_time(d['deleted'])}")
            li.setData(Qt.UserRole, d)
            li.setSizeHint(QSize(0, 36))
            self.list.addItem(li)
        self.empty_b.setEnabled(self.list.count() > 0)
        self._sel()

    def _sel(self):
        has = bool(self.list.selectedItems())
        self.restore_b.setEnabled(has)
        self.purge_b.setEnabled(has)

    def restore(self):
        st = context.store
        for li in self.list.selectedItems():
            d = li.data(Qt.UserRole)
            if d["type"] == "project":
                st.restore_project(d["id"])
            elif d["type"] == "board":
                st.restore_board(d["id"])
            else:
                st.restore_item(d["id"])
        self.refresh()
        self.main.refresh_sidebar()
        self.main.reload_board()
        self.main.toast("Restored")

    def purge(self):
        sel = self.list.selectedItems()
        if not sel or QMessageBox.question(self, "Delete forever",
                                           f"Permanently delete {len(sel)} thing(s)? This can't be undone.") \
                != QMessageBox.Yes:
            return
        st = context.store
        for li in sel:
            d = li.data(Qt.UserRole)
            st.purge("project" if d["type"] == "project" else "board" if d["type"] == "board" else "item", d["id"])
        st.gc_assets()
        self.refresh()

    def empty_trash(self):
        if QMessageBox.question(self, "Empty trash", "Permanently delete everything in the trash?") != QMessageBox.Yes:
            return
        context.store.empty_trash()
        self.refresh()
        self.main.toast("Trash emptied")
