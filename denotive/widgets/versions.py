"""Version history / quick look for an evidence card or attached file: browse, restore or branch an edit."""
from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import (QDialog, QFileDialog, QHBoxLayout, QListWidget, QListWidgetItem, QPlainTextEdit,
                               QSplitter, QVBoxLayout, QWidget)

from .. import context, evidence, theme
from ..slide.preview import SlidePreview
from .common import human_size, label, nice_time, push_button


class VersionsDialog(QDialog):
    edit_version = Signal(str, str)  # item id, version id
    restored = Signal(str)           # item id

    def __init__(self, item_id, select_version=None, parent=None, quick=False):
        super().__init__(parent)
        st = context.store
        self.item_id = item_id
        self.item = st.item(item_id)
        self.versions = list(reversed(st.versions(item_id)))
        self.kind = self.item["kind"]
        self.setWindowTitle(("Quick look — " if quick else "History — ") + (self.item.get("title") or ""))
        self.resize(1180, 760)
        root = QHBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        split = QSplitter(Qt.Horizontal)
        left = QWidget()
        ll = QVBoxLayout(left)
        ll.setContentsMargins(14, 14, 8, 14)
        ll.addWidget(label(f"{len(self.versions)} version{'s' if len(self.versions) != 1 else ''}", "SectionLabel"))
        self.list = QListWidget()
        self.list.setIconSize(QSize(112, 63))
        self.list.setSpacing(2)
        for v in self.versions:
            cur = v["id"] == self.item.get("ver")
            txt = f"v{v['num']}{'  · current' if cur else ''}\n{nice_time(v['created'])}\n{(v['title'] or '')[:40]}"
            li = QListWidgetItem(txt)
            li.setData(Qt.UserRole, v["id"])
            if v.get("thumb"):
                pm = context.images.get_sync(v["thumb"], 256)
                if pm is not None and not pm.isNull():
                    ic = QIcon(pm)
                    ic.addPixmap(pm, QIcon.Selected)
                    li.setIcon(ic)
            elif self.kind == "file":
                li.setIcon(theme.icon(theme.file_glyph(self.item["data"].get("ext")), 32,
                                      theme.file_color(self.item["data"].get("ext"))))
            self.list.addItem(li)
        self.list.currentRowChanged.connect(self._show)
        ll.addWidget(self.list, 1)
        split.addWidget(left)

        right = QWidget()
        rl = QVBoxLayout(right)
        rl.setContentsMargins(8, 14, 16, 14)
        rl.setSpacing(8)
        self.h_title = label("", "Title", wrap=True)
        self.h_meta = label("", "Muted")
        rl.addWidget(self.h_title)
        rl.addWidget(self.h_meta)
        self.preview = SlidePreview(bg="#101216")
        self.file_box = QPlainTextEdit()
        self.file_box.setReadOnly(True)
        self.file_box.setStyleSheet("QPlainTextEdit { font-family: Consolas; font-size: 10pt; background: #15171c; }")
        rl.addWidget(self.preview, 4)
        rl.addWidget(self.file_box, 4)
        self.notes = QPlainTextEdit()
        self.notes.setReadOnly(True)
        self.notes.setMaximumHeight(120)
        rl.addWidget(self.notes, 1)
        btns = QHBoxLayout()
        self.b_edit = push_button("Edit from this version", "edit")
        self.b_edit.clicked.connect(self._edit)
        self.b_restore = push_button("Restore as current", "restore")
        self.b_restore.clicked.connect(self._restore)
        self.b_export = push_button("Export PNG", "download")
        self.b_export.clicked.connect(self._export)
        self.b_open = push_button("Open this snapshot", "openfile")
        self.b_open.clicked.connect(self._open_snapshot)
        self.b_view = push_button("View contents", "view")
        self.b_view.clicked.connect(self._view_contents)
        for b in (self.b_edit, self.b_restore, self.b_export, self.b_open, self.b_view):
            btns.addWidget(b)
        btns.addStretch(1)
        close = push_button("Close", primary=True)
        close.clicked.connect(self.accept)
        btns.addWidget(close)
        rl.addLayout(btns)
        split.addWidget(right)
        split.setSizes([330, 850])
        root.addWidget(split)

        is_ev = self.kind == "evidence"
        self.preview.setVisible(is_ev)
        self.b_edit.setVisible(is_ev)
        self.b_export.setVisible(is_ev)
        self.file_box.setVisible(not is_ev)
        self.b_open.setVisible(not is_ev)
        self.b_view.setVisible(not is_ev)
        idx = 0
        for i, v in enumerate(self.versions):
            if v["id"] == select_version:
                idx = i
        self.list.setCurrentRow(idx)

    def current(self):
        i = self.list.currentRow()
        return self.versions[i] if 0 <= i < len(self.versions) else None

    def _show(self, i):
        v = self.current()
        if v is None:
            return
        cur = v["id"] == self.item.get("ver")
        self.h_title.setText(v["title"] or "Untitled")
        self.h_meta.setText(f"Version {v['num']} of {len(self.versions)} · {nice_time(v['created'])}"
                            + ("  ·  current" if cur else "") + (f"  ·  {v['note']}" if v.get("note") else ""))
        self.b_restore.setEnabled(not cur)
        if self.kind == "evidence":
            self.preview.set_doc(v["doc"])
            notes = v["doc"].get("notes", "")
            self.notes.setPlainText(notes)
            self.notes.setVisible(bool(notes))
        else:
            d = v["doc"]
            self.file_box.setPlainText(d.get("preview") or "(no text preview)")
            self.notes.setPlainText(f"{d.get('path', '')}\n{human_size(d.get('size'))} · modified "
                                    f"{nice_time(d.get('mtime'))} · "
                                    f"{'snapshot stored' if d.get('asset') else 'linked only (no snapshot)'}")
            self.b_open.setEnabled(bool(d.get("asset")))

    def _edit(self):
        v = self.current()
        if v:
            self.edit_version.emit(self.item_id, v["id"])
            self.accept()

    def _restore(self):
        v = self.current()
        if not v:
            return
        context.store.add_version(self.item_id, v["title"], v["doc"], v["text"], v["thumb"],
                                  note=f"restored from v{v['num']}")
        self.restored.emit(self.item_id)
        self.accept()

    def _export(self):
        v = self.current()
        if not v:
            return
        path, _ = QFileDialog.getSaveFileName(self, "Export slide", f"{v['title'] or 'evidence'} v{v['num']}.png",
                                              "PNG image (*.png)")
        if path:
            from ..slide.scene import SlideScene
            sc = SlideScene(v["doc"])
            sc.render_image(2560).save(path)
            sc.deleteLater()

    def _open_snapshot(self):
        v = self.current()
        p = evidence.snapshot_copy(self.item_id, v)
        if p:
            evidence.open_path(p)

    def _view_contents(self):
        from .fileviewer import FileViewer
        FileViewer(self.item_id, self.current(), self).exec()
