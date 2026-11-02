"""Search palette (Ctrl+K / Ctrl+F). Scoped to everything, a project, a board or a single piece of evidence."""
from PySide6.QtCore import QEvent, QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QFontMetrics, QPainter, QTextDocument
from PySide6.QtWidgets import (QAbstractItemView, QCheckBox, QFrame, QGraphicsDropShadowEffect, QHBoxLayout, QLabel,
                               QLineEdit, QListWidget, QListWidgetItem, QStyle, QStyledItemDelegate, QVBoxLayout)

from .. import context, theme
from .common import label, push_button

ROLE = Qt.UserRole
KIND_ICON = {"evidence": "evidence", "file": "doc", "note": "sticky", "string": "tool_string", "board": "board",
             "project": "folder"}
KIND_NAME = {"evidence": "Evidence", "file": "File", "note": "Note", "string": "String", "board": "Board",
             "project": "Project"}


def _rich(text, color_hi="#ff7a7e"):
    """Converts \\x01…\\x02 match markers into highlighted HTML."""
    import html
    out = html.escape(text or "")
    out = out.replace("\x01", f"<b style='color:{color_hi}'>").replace("\x02", "</b>")
    return out.replace("\n", " ")


class ResultDelegate(QStyledItemDelegate):
    def sizeHint(self, option, index):
        d = index.data(ROLE)
        if d.get("_header"):
            return QSize(option.rect.width(), 30)
        return QSize(option.rect.width(), 62)

    def paint(self, p, option, index):
        d = index.data(ROLE)
        p.save()
        p.setRenderHint(QPainter.Antialiasing)
        r = QRectF(option.rect)
        if d.get("_header"):
            p.setFont(theme.ui_font(11, QFont.Bold))
            p.setPen(QColor(theme.FAINT))
            p.drawText(r.adjusted(14, 6, -14, 0), Qt.AlignLeft | Qt.AlignVCenter, d["_header"].upper())
            p.restore()
            return
        if option.state & QStyle.State_Selected:
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(theme.SURFACE3))
            p.drawRoundedRect(r.adjusted(4, 1, -4, -1), 8, 8)
        elif option.state & QStyle.State_MouseOver:
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(theme.SURFACE2))
            p.drawRoundedRect(r.adjusted(4, 1, -4, -1), 8, 8)
        kind = d["kind"]
        ic = QRectF(r.x() + 14, r.y() + 13, 34, 34)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(theme.SURFACE2))
        p.drawRoundedRect(ic, 8, 8)
        p.drawPixmap(ic.adjusted(8, 8, -8, -8).toRect(), theme.icon_pixmap(KIND_ICON.get(kind, "doc"), 18, theme.TEXT))
        x = ic.right() + 12
        w = r.right() - x - 14
        # meta (right)
        meta = KIND_NAME.get(kind, kind)
        if d.get("ver") and kind in ("evidence", "file"):
            meta += f" · v{d['ver']}" + (" (older)" if d.get("old") else "")
        if d.get("hits", 1) > 1:
            meta += f" · {d['hits']} versions match"
        p.setFont(theme.ui_font(11))
        mw = QFontMetrics(p.font()).horizontalAdvance(meta)
        p.setPen(QColor(theme.ACCENT_HOVER if d.get("old") else theme.FAINT))
        p.drawText(QRectF(r.right() - mw - 16, r.y() + 8, mw, 20), Qt.AlignRight | Qt.AlignVCenter, meta)
        # title
        title = d.get("title_marked") or d.get("title") or ""
        if kind == "string":
            title = f"String on “{title}”"
        doc = QTextDocument()
        doc.setDefaultFont(theme.ui_font(13.5, QFont.DemiBold))
        doc.setDefaultStyleSheet(f"body {{ color: {theme.TEXT}; }}")
        doc.setHtml(f"<body>{_rich(title)}</body>")
        doc.setTextWidth(w - mw - 10)
        p.save()
        p.translate(x - 4, r.y() + 6)
        p.setClipRect(QRectF(0, 0, w - mw - 6, 24))
        doc.drawContents(p, QRectF(0, 0, w - mw - 6, 24))
        p.restore()
        # snippet
        snip = d.get("snip") or ""
        if snip:
            doc2 = QTextDocument()
            doc2.setDefaultFont(theme.ui_font(12))
            doc2.setDefaultStyleSheet("body { color: #a2a7b2; }")
            doc2.setHtml(f"<body>{_rich(snip.strip())}</body>")
            doc2.setTextWidth(10000)
            p.save()
            p.translate(x - 4, r.y() + 27)
            p.setClipRect(QRectF(0, 0, w * 0.68, 22))
            doc2.drawContents(p, QRectF(0, 0, w * 0.68, 22))
            p.restore()
        crumb = " › ".join(v for v in (d.get("project_name"), d.get("board_name")) if v)
        if crumb:
            p.setFont(theme.ui_font(11))
            p.setPen(QColor(theme.FAINT))
            cx = x + (w * 0.7 if snip else 0)
            cw = w * (0.3 if snip else 1.0)
            p.drawText(QRectF(cx, r.y() + 29, cw, 20), (Qt.AlignRight if snip else Qt.AlignLeft) | Qt.AlignVCenter,
                       QFontMetrics(p.font()).elidedText(crumb, Qt.ElideLeft, int(cw)))
        p.restore()


class SearchOverlay(QFrame):
    activated = Signal(dict)

    def __init__(self, parent):
        super().__init__(parent)
        self.setObjectName("Overlay")
        self.hide()
        fx = QGraphicsDropShadowEffect(self)
        fx.setBlurRadius(48)
        fx.setOffset(0, 18)
        fx.setColor(QColor(0, 0, 0, 170))
        self.setGraphicsEffect(fx)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(14, 12, 14, 12)
        lay.setSpacing(8)
        row = QHBoxLayout()
        ic = QLabel()
        ic.setPixmap(theme.icon_pixmap("search", 20, theme.MUTED))
        row.addWidget(ic)
        self.edit = QLineEdit()
        self.edit.setObjectName("BigSearch")
        self.edit.setPlaceholderText("Search words, files, labels…  (use \"quotes\" for exact phrases)")
        self.edit.textChanged.connect(lambda _: self._timer.start())
        self.edit.installEventFilter(self)
        row.addWidget(self.edit, 1)
        row.addWidget(label("Esc", "Hint"))
        lay.addLayout(row)
        chips = QHBoxLayout()
        chips.setSpacing(6)
        chips.addWidget(label("In", "Muted"))
        self.scope_btns = {}
        for key in ("all", "project", "board", "item"):
            b = push_button("", name="Chip")
            b.setCheckable(True)
            b.clicked.connect(lambda _=False, k=key: self.set_scope(k))
            chips.addWidget(b)
            self.scope_btns[key] = b
        chips.addStretch(1)
        lay.addLayout(chips)
        chips = QHBoxLayout()
        chips.setSpacing(6)
        chips.addWidget(label("Show", "Muted"))
        self.kind_btns = {}
        for key, txt in (("evidence", "Evidence"), ("file", "Files"), ("note", "Notes"), ("string", "Strings")):
            b = push_button(txt, name="Chip")
            b.setCheckable(True)
            b.setChecked(True)
            b.clicked.connect(self.run)
            chips.addWidget(b)
            self.kind_btns[key] = b
        chips.addStretch(1)
        self.old = QCheckBox("Older versions")
        self.old.setChecked(True)
        self.old.setToolTip("Also search the stacked older versions of evidence and file snapshots")
        self.old.toggled.connect(self.run)
        chips.addWidget(self.old)
        lay.addLayout(chips)
        self.list = QListWidget()
        self.list.setItemDelegate(ResultDelegate(self.list))
        self.list.setMouseTracking(True)
        self.list.setVerticalScrollMode(QAbstractItemView.ScrollPerPixel)
        self.list.setStyleSheet("QListWidget::item, QListWidget::item:selected, QListWidget::item:hover "
                                "{ background: transparent; padding: 0; }")
        self.list.itemActivated.connect(self._activate)
        self.list.itemClicked.connect(self._activate)
        lay.addWidget(self.list, 1)
        self.status = label("", "Hint")
        lay.addWidget(self.status)
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(110)
        self._timer.timeout.connect(self.run)
        self.scope = "all"
        self.ctx = {}

    # ------------------------------------------------------------------ scope
    def open(self, scope="all", project=None, board=None, item=None):
        """project/board/item are (id, name) tuples for the scopes that make sense right now."""
        self.ctx = {"project": project, "board": board, "item": item}
        for key, b in self.scope_btns.items():
            if key == "all":
                b.setText("Everything")
                b.setVisible(True)
            else:
                v = self.ctx.get(key)
                b.setVisible(v is not None)
                if v is not None:
                    prefix = {"project": "Project", "board": "Board", "item": "Evidence"}[key]
                    name = v[1] if len(v[1]) <= 30 else v[1][:29] + "…"
                    b.setText(f"{prefix}: {name}")
        if scope != "all" and self.ctx.get(scope) is None:
            scope = "all"
        self.set_scope(scope, run=False)
        self.place()
        self.show()
        self.raise_()
        self.edit.setFocus()
        self.edit.selectAll()
        self.run()

    def set_scope(self, key, run=True):
        self.scope = key
        for k, b in self.scope_btns.items():
            b.setChecked(k == key)
        if run:
            self.run()

    def place(self):
        par = self.parentWidget()
        w = min(860, par.width() - 80)
        h = min(640, int(par.height() * 0.78))
        self.setGeometry((par.width() - w) // 2, 64, w, h)

    # ------------------------------------------------------------------ query
    def run(self):
        if not self.isVisible():
            return
        text = self.edit.text().strip()
        self.list.clear()
        if not text:
            self.status.setText("Type to search titles, slide text, notes, string labels and the contents of "
                                "attached files.")
            return
        kinds = [k for k, b in self.kind_btns.items() if b.isChecked()]
        if self.scope == "all":
            kinds += ["board", "project"]
        scope = ("all", None)
        if self.scope in ("project", "board", "item") and self.ctx.get(self.scope):
            scope = (self.scope, self.ctx[self.scope][0])
        res = context.store.search(text, scope, kinds or None, include_old=self.old.isChecked())
        groups = {}
        for r in res:
            groups.setdefault(r["kind"], []).append(r)
        order = ["evidence", "file", "note", "string", "board", "project"]
        for k in order:
            if k not in groups:
                continue
            hi = QListWidgetItem()
            hi.setData(ROLE, {"_header": f"{KIND_NAME[k]}{'s' if len(groups[k]) != 1 else ''} · {len(groups[k])}"})
            hi.setFlags(Qt.NoItemFlags)
            self.list.addItem(hi)
            for r in groups[k]:
                li = QListWidgetItem()
                li.setData(ROLE, r)
                self.list.addItem(li)
        n = len(res)
        self.status.setText(f"{n} result{'s' if n != 1 else ''}" + (" (showing the best 400)" if n >= 400 else "")
                            + "   ·   ↑↓ to move, Enter to open")
        self._select_next(0, 1)

    def _select_next(self, start, step):
        i = start
        while 0 <= i < self.list.count():
            if not self.list.item(i).data(ROLE).get("_header"):
                self.list.setCurrentRow(i)
                return
            i += step

    def _activate(self, li):
        d = li.data(ROLE) if li else None
        if not d or d.get("_header"):
            return
        self.hide()
        self.activated.emit(d)

    def eventFilter(self, obj, e):
        if obj is self.edit and e.type() == QEvent.KeyPress:
            k = e.key()
            if k == Qt.Key_Escape:
                self.hide()
                return True
            if k in (Qt.Key_Down, Qt.Key_Up):
                step = 1 if k == Qt.Key_Down else -1
                self._select_next(self.list.currentRow() + step, step)
                return True
            if k in (Qt.Key_Return, Qt.Key_Enter):
                self._activate(self.list.currentItem())
                return True
        return super().eventFilter(obj, e)

    def keyPressEvent(self, e):
        if e.key() == Qt.Key_Escape:
            self.hide()
            return
        super().keyPressEvent(e)
