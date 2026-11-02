"""In-app viewer for attached files: syntax-highlighted source/text, images, or file details."""
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QFont, QPixmap, QTextCharFormat, QTextCursor
from PySide6.QtWidgets import (QDialog, QHBoxLayout, QLabel, QLineEdit, QPlainTextEdit, QScrollArea, QTextEdit,
                               QVBoxLayout)

from .. import context, evidence, textextract, theme
from .common import human_size, label, nice_time, push_button
from .highlight import CodeHighlighter


class FileViewer(QDialog):
    def __init__(self, item_id, version=None, parent=None):
        super().__init__(parent)
        st = context.store
        self.item = st.item(item_id)
        self.version = version or st.current_version(item_id)
        d = self.item["data"]
        self.setWindowTitle(d.get("name") or "File")
        self.resize(1000, 760)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(16, 14, 16, 14)
        lay.setSpacing(10)
        head = QHBoxLayout()
        ic = QLabel()
        ic.setPixmap(theme.icon_pixmap(theme.file_glyph(d.get("ext")), 26, theme.file_color(d.get("ext"))))
        head.addWidget(ic)
        col = QVBoxLayout()
        col.setSpacing(0)
        col.addWidget(label(d.get("name") or "", "Title"))
        p = label(d.get("path") or "", "Muted")
        p.setTextInteractionFlags(Qt.TextSelectableByMouse)
        col.addWidget(p)
        head.addLayout(col, 1)
        self.src_label = label("", "Hint")
        head.addWidget(self.src_label)
        lay.addLayout(head)

        bar = QHBoxLayout()
        self.b_snap = push_button("Snapshot", "history", name="Chip")
        self.b_snap.setCheckable(True)
        self.b_disk = push_button("Current file on disk", "doc", name="Chip")
        self.b_disk.setCheckable(True)
        self.b_snap.clicked.connect(lambda: self.show_source("snapshot"))
        self.b_disk.clicked.connect(lambda: self.show_source("disk"))
        bar.addWidget(self.b_snap)
        bar.addWidget(self.b_disk)
        bar.addStretch(1)
        self.find = QLineEdit()
        self.find.setPlaceholderText("Find in file…")
        self.find.setFixedWidth(240)
        self.find.returnPressed.connect(self._find_next)
        self.find.textChanged.connect(self._highlight_all)
        bar.addWidget(self.find)
        lay.addLayout(bar)

        self.text = QPlainTextEdit()
        self.text.setReadOnly(True)
        self.text.setLineWrapMode(QPlainTextEdit.NoWrap)
        f = QFont(theme.MONO_FONT)
        f.setPointSizeF(10)
        self.text.setFont(f)
        self.text.setStyleSheet("QPlainTextEdit { background: #15171c; border-radius: 8px; }")
        self.img = QLabel()
        self.img.setAlignment(Qt.AlignCenter)
        self.img_scroll = QScrollArea()
        self.img_scroll.setWidget(self.img)
        self.img_scroll.setWidgetResizable(True)
        self.info = label("", "Muted", wrap=True)
        self.info.setAlignment(Qt.AlignCenter)
        lay.addWidget(self.text, 1)
        lay.addWidget(self.img_scroll, 1)
        lay.addWidget(self.info, 1)
        self.highlighter = None

        bot = QHBoxLayout()
        op = push_button("Open in default app", "openfile")
        op.clicked.connect(self._open)
        rv = push_button("Show in folder", "folderopen")
        rv.clicked.connect(lambda: evidence.reveal(self.item["data"].get("path")))
        bot.addWidget(op)
        bot.addWidget(rv)
        bot.addStretch(1)
        close = push_button("Close", primary=True)
        close.clicked.connect(self.accept)
        bot.addWidget(close)
        lay.addLayout(bot)

        snap = st.asset_path((self.version or {}).get("doc", {}).get("asset"))
        self.snap_path = snap if snap and snap.exists() else None
        disk = Path(d.get("path") or "")
        self.disk_path = disk if disk.is_file() else None
        self.b_snap.setEnabled(self.snap_path is not None)
        self.b_disk.setEnabled(self.disk_path is not None)
        self.show_source("snapshot" if self.snap_path else "disk")

    def _open(self):
        if self.mode == "disk" and self.disk_path:
            evidence.open_path(self.disk_path)
        else:
            p = evidence.snapshot_copy(self.item["id"], self.version)
            if p:
                evidence.open_path(p)

    def show_source(self, mode):
        self.mode = mode
        self.b_snap.setChecked(mode == "snapshot")
        self.b_disk.setChecked(mode == "disk")
        path = self.snap_path if mode == "snapshot" else self.disk_path
        v = self.version or {}
        if mode == "snapshot":
            self.src_label.setText(f"Snapshot v{v.get('num', 1)} · captured {nice_time(v.get('created'))}")
        else:
            self.src_label.setText("Live file on disk")
        self.text.hide()
        self.img_scroll.hide()
        self.info.hide()
        ext = (self.item["data"].get("ext") or "").lower()
        if path is None:
            self.info.setText("This file isn't available (no snapshot was stored and the original is missing).")
            self.info.show()
            return
        if ext in evidence.IMAGE_EXTS:
            pm = QPixmap(str(path))
            if not pm.isNull():
                self.img.setPixmap(pm.scaled(1600, 1600, Qt.KeepAspectRatio, Qt.SmoothTransformation)
                                   if max(pm.width(), pm.height()) > 1600 else pm)
                self.img_scroll.show()
                return
        content = textextract.extract(path)
        if content:
            if self.highlighter is not None:
                self.highlighter.setDocument(None)
                self.highlighter = None
            self.text.setPlainText(content)
            if ext in ("py", "pyw", "ipynb", "m", "jl", "r", "c", "cpp", "h", "js", "ts", "java", "cs", "rs", "go",
                       "sh", "ps1", "bat"):
                self.highlighter = CodeHighlighter(self.text.document(), ext, dark=True)
            self.text.show()
            self._highlight_all(self.find.text())
            return
        size = path.stat().st_size if path.exists() else 0
        self.info.setText(f"No preview for this kind of file ({human_size(size)}).\nUse “Open in default app”.")
        self.info.show()

    def _find_next(self):
        t = self.find.text()
        if not t:
            return
        if not self.text.find(t):
            c = self.text.textCursor()
            c.movePosition(QTextCursor.Start)
            self.text.setTextCursor(c)
            self.text.find(t)

    def _highlight_all(self, t):
        sels = []
        if t and len(t) >= 2:
            doc = self.text.document()
            c = QTextCursor(doc)
            fmt = QTextCharFormat()
            fmt.setBackground(QColor(229, 72, 77, 110))
            n = 0
            while n < 2000:
                c = doc.find(t, c)
                if c.isNull():
                    break
                s = QTextEdit.ExtraSelection()
                s.cursor = c
                s.format = fmt
                sels.append(s)
                n += 1
        self.text.setExtraSelections(sels)
