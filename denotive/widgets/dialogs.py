"""Export options and settings dialogs."""
import os
import zipfile
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QApplication, QButtonGroup, QCheckBox, QComboBox, QDialog, QFileDialog, QHBoxLayout,
                               QProgressDialog, QRadioButton, QSpinBox, QVBoxLayout)

from .. import context
from ..settings import settings
from ..slide.items import PAGE_SIZES
from .common import hsep, label, push_button, section_label


class ExportDialog(QDialog):
    def __init__(self, kind, title, has_selection=False, parent=None):
        super().__init__(parent)
        self.kind = kind
        names = {"pdf": "Export PDF", "pptx": "Export PowerPoint", "print": "Print"}
        self.setWindowTitle(names[kind])
        self.setMinimumWidth(460)
        v = QVBoxLayout(self)
        v.setContentsMargins(20, 18, 20, 16)
        v.setSpacing(10)
        v.addWidget(label(names[kind], "Title"))
        v.addWidget(label(f"{title} — every evidence card becomes one slide.", "Muted", wrap=True))
        v.addWidget(hsep())
        self.overview = QCheckBox("Start with a title page showing the whole board (strings and all)")
        self.overview.setChecked(True)
        v.addWidget(self.overview)
        v.addWidget(section_label("Slide order"))
        self.order = QComboBox()
        self.order.addItem("Board position (left→right, top→bottom)", "position")
        self.order.addItem("Date created (oldest first)", "created")
        self.order.addItem("Date updated (oldest first)", "updated")
        v.addWidget(self.order)
        self.only_sel = QCheckBox("Only the selected evidence")
        self.only_sel.setVisible(has_selection)
        v.addWidget(self.only_sel)
        if kind == "pptx":
            v.addWidget(section_label("PowerPoint style"))
            self.editable = QRadioButton("Editable — real text boxes, pictures, shapes and lines")
            self.images = QRadioButton("Pictures — each slide as an exact image")
            self.editable.setChecked(True)
            g = QButtonGroup(self)
            g.addButton(self.editable)
            g.addButton(self.images)
            v.addWidget(self.editable)
            v.addWidget(self.images)
            v.addWidget(label("Notes and string connections are added as speaker notes.", "Hint", wrap=True))
        h = QHBoxLayout()
        h.addStretch(1)
        c = push_button("Cancel")
        c.clicked.connect(self.reject)
        ok = push_button("Print…" if kind == "print" else "Export…", "export", primary=True)
        ok.clicked.connect(self.accept)
        h.addWidget(c)
        h.addWidget(ok)
        v.addSpacing(6)
        v.addLayout(h)

    def options(self):
        return {"overview": self.overview.isChecked(), "order": self.order.currentData(),
                "only_selected": self.only_sel.isChecked(),
                "editable": getattr(self, "editable", None) is None or self.editable.isChecked()}


class SettingsDialog(QDialog):
    def __init__(self, main):
        super().__init__(main)
        self.main = main
        self.setWindowTitle("Settings")
        self.setMinimumWidth(560)
        v = QVBoxLayout(self)
        v.setContentsMargins(22, 18, 22, 16)
        v.setSpacing(10)
        v.addWidget(label("Settings", "Title"))
        v.addWidget(section_label("Library"))
        st = context.store
        self.lib = label(str(st.root), "Muted", wrap=True)
        self.lib.setTextInteractionFlags(Qt.TextSelectableByMouse)
        v.addWidget(self.lib)
        h = QHBoxLayout()
        op = push_button("Open folder", "folderopen")
        op.clicked.connect(lambda: os.startfile(str(st.root)))
        ch = push_button("Switch library…", "library")
        ch.clicked.connect(self.switch_library)
        bk = push_button("Back up now…", "download")
        bk.clicked.connect(self.backup)
        for b in (op, ch, bk):
            h.addWidget(b)
        h.addStretch(1)
        v.addLayout(h)
        v.addWidget(label("Everything lives in this folder: one database plus an assets folder. A dated copy of the "
                          "database is kept in its backups folder each day.", "Hint", wrap=True))
        v.addWidget(hsep())
        v.addWidget(section_label("Co-Investigator (AI)"))
        h_ai = QHBoxLayout()
        from ..ai.providers import describe, role_config
        h_ai.addWidget(label(f"Co-Investigator: {describe(role_config('coinv'))}   ·   "
                             f"Notes: {describe(role_config('notes'))}", "Muted"))
        h_ai.addStretch(1)
        ai_b = push_button("AI settings…", "settings")
        ai_b.clicked.connect(lambda: (self.main.open_ai_settings(), self.close()))
        h_ai.addWidget(ai_b)
        v.addLayout(h_ai)
        v.addWidget(hsep())
        v.addWidget(section_label("Behaviour"))
        self.wheel = QCheckBox("Mouse wheel zooms the board (Ctrl+wheel always zooms; touchpads pan)")
        self.wheel.setChecked(bool(settings.get("wheel_zooms", True)))
        v.addWidget(self.wheel)
        self.minimap = QCheckBox("Show the minimap on boards")
        self.minimap.setChecked(bool(settings.get("show_minimap", True)))
        v.addWidget(self.minimap)
        h2 = QHBoxLayout()
        h2.addWidget(label("New evidence page size", "Muted"))
        self.page = QComboBox()
        self.page.addItems(list(PAGE_SIZES))
        self.page.setCurrentText(settings.get("page_size", "16:9"))
        h2.addWidget(self.page)
        h2.addStretch(1)
        v.addLayout(h2)
        h3 = QHBoxLayout()
        h3.addWidget(label("Keep a snapshot copy of attached files up to", "Muted"))
        self.limit = QSpinBox()
        self.limit.setRange(0, 4000)
        self.limit.setSuffix(" MB")
        self.limit.setValue(int(settings.get("snapshot_limit_mb", 50)))
        h3.addWidget(self.limit)
        h3.addStretch(1)
        v.addLayout(h3)
        v.addWidget(label("Snapshots preserve the exact script or data file you used, even if you later change "
                          "or delete the original. Bigger files are linked by path only.", "Hint", wrap=True))
        v.addWidget(hsep())
        h4 = QHBoxLayout()
        gc = push_button("Clean up unused files", "refresh")
        gc.clicked.connect(self.cleanup)
        h4.addWidget(gc)
        h4.addStretch(1)
        ok = push_button("Done", primary=True)
        ok.clicked.connect(self.accept)
        h4.addWidget(ok)
        v.addLayout(h4)

    def accept(self):
        settings["wheel_zooms"] = self.wheel.isChecked()
        settings["show_minimap"] = self.minimap.isChecked()
        settings["page_size"] = self.page.currentText()
        settings["snapshot_limit_mb"] = self.limit.value()
        settings.save()
        self.main.apply_settings()
        super().accept()

    def switch_library(self):
        d = QFileDialog.getExistingDirectory(self, "Choose or create a library folder", str(context.store.root))
        if not d:
            return
        self.main.switch_library(d)
        self.lib.setText(d)

    def cleanup(self):
        n = context.store.gc_assets()
        self.main.toast(f"Removed {n} unused file{'s' if n != 1 else ''}")

    def backup(self):
        st = context.store
        default = Path.home() / "Documents" / f"DeNotive backup {datetime.now():%Y-%m-%d %H%M}.zip"
        path, _ = QFileDialog.getSaveFileName(self, "Back up library", str(default), "Zip archive (*.zip)")
        if not path:
            return
        tmp = st.root / "backups" / "_snapshot.db"
        st.backup_to(tmp)
        files = [p for p in (st.root / "assets").rglob("*") if p.is_file()]
        prog = QProgressDialog("Backing up…", "Cancel", 0, len(files) + 1, self)
        prog.setWindowModality(Qt.WindowModal)
        try:
            with zipfile.ZipFile(path, "w", zipfile.ZIP_STORED) as z:
                z.write(tmp, "denotive.db")
                for i, f in enumerate(files):
                    if prog.wasCanceled():
                        break
                    z.write(f, str(f.relative_to(st.root)))
                    if i % 20 == 0:
                        prog.setValue(i)
                        QApplication.processEvents()
        finally:
            prog.close()
            tmp.unlink(missing_ok=True)
        self.main.toast("Backup saved")
