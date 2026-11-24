"""Choosing which page of a PDF to bring in as editable shapes and text."""
from PySide6.QtCore import Qt
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import QCheckBox, QDialog, QHBoxLayout, QLabel, QSpinBox, QVBoxLayout

from .common import label, push_button

PREVIEW_W = 420


class PdfPageDialog(QDialog):
    def __init__(self, path, count, parent=None, show_group=True):
        super().__init__(parent)
        from pathlib import Path
        self.path = path
        self.setWindowTitle("Import PDF as editable shapes")
        lay = QVBoxLayout(self)
        lay.addWidget(label(Path(path).name, "Muted"))
        self.preview = QLabel()
        self.preview.setAlignment(Qt.AlignCenter)
        self.preview.setMinimumSize(PREVIEW_W, int(PREVIEW_W * 0.75))
        self.preview.setStyleSheet("background:#ffffff;border:1px solid #3a3f4b;border-radius:4px")
        lay.addWidget(self.preview, 1)
        row = QHBoxLayout()
        row.addWidget(label("Page", "Muted"))
        self.page = QSpinBox()
        self.page.setRange(1, max(1, count))
        self.page.setSuffix(f" of {count}")
        self.page.valueChanged.connect(self._show)
        row.addWidget(self.page)
        row.addStretch(1)
        lay.addLayout(row)
        self.group = QCheckBox("Keep the pieces together as a group (Ctrl+Shift+G takes them apart)")
        self.group.setChecked(True)
        self.group.setVisible(show_group)
        lay.addWidget(self.group)
        hint = label("Lines, fills and text drawn by a program (charts, diagrams, slides) come in as separate "
                     "editable pieces. Scanned pages only hold a picture, so they come in as that picture.",
                     "Muted", wrap=True)
        lay.addWidget(hint)
        btns = QHBoxLayout()
        btns.addStretch(1)
        cancel = push_button("Cancel")
        cancel.clicked.connect(self.reject)
        ok = push_button("Import", primary=True)
        ok.clicked.connect(self.accept)
        btns.addWidget(cancel)
        btns.addWidget(ok)
        lay.addLayout(btns)
        self._show()

    def _show(self):
        from .. import pdfimport
        try:
            img = pdfimport.render_page(self.path, self.index(), PREVIEW_W * 2)
        except Exception:
            self.preview.setText("(no preview)")
            return
        pm = QPixmap.fromImage(img)
        pm.setDevicePixelRatio(2.0)
        if pm.height() / 2 > PREVIEW_W:
            pm = pm.scaledToHeight(PREVIEW_W * 2, Qt.SmoothTransformation)
            pm.setDevicePixelRatio(2.0)
        self.preview.setPixmap(pm)

    def index(self):
        return self.page.value() - 1


def choose_page(parent, path, show_group=True):
    """(page index, keep grouped) for a PDF, or None if cancelled or the file can't be read.

    A one-page PDF needs no choosing, so no dialog shows for it."""
    from PySide6.QtWidgets import QMessageBox
    from .. import pdfimport
    try:
        n = pdfimport.page_count(path)
    except Exception as e:
        QMessageBox.warning(parent, "Import PDF", f"This PDF can't be opened:\n{e}")
        return None
    if n == 1:
        return 0, True
    d = PdfPageDialog(path, n, parent, show_group)
    if d.exec() != QDialog.Accepted:
        return None
    return d.index(), d.group.isChecked()
