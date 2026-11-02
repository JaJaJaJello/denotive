"""Timeline: every piece of evidence (and optionally files/notes) as a dated, grouped list with a live preview."""
import time

from PySide6.QtCore import QRectF, QSize, Qt
from PySide6.QtGui import QColor, QFont, QFontMetrics, QPainter, QPainterPath
from PySide6.QtWidgets import (QAbstractItemView, QComboBox, QHBoxLayout, QListWidget, QListWidgetItem,
                               QPlainTextEdit, QSplitter, QStackedWidget, QStyle, QStyledItemDelegate, QVBoxLayout,
                               QWidget)

from .. import context, theme
from ..slide.preview import SlidePreview
from .common import date_group, human_size, label, nice_time, push_button

ROLE = Qt.UserRole


class FeedDelegate(QStyledItemDelegate):
    def sizeHint(self, option, index):
        d = index.data(ROLE)
        return QSize(option.rect.width(), 40 if d.get("_header") else 86)

    def paint(self, p, option, index):
        d = index.data(ROLE)
        p.save()
        p.setRenderHint(QPainter.Antialiasing)
        r = option.rect
        if d.get("_header"):
            p.setFont(theme.ui_font(11.5, QFont.Bold))
            p.setPen(QColor(theme.MUTED))
            p.drawText(r.adjusted(14, 10, -14, 0), Qt.AlignLeft | Qt.AlignVCenter, d["_header"].upper())
            p.setFont(theme.ui_font(11))
            p.setPen(QColor(theme.FAINT))
            p.drawText(r.adjusted(14, 10, -16, 0), Qt.AlignRight | Qt.AlignVCenter, str(d.get("_count", "")))
            p.restore()
            return
        card = QRectF(r).adjusted(8, 3, -8, -3)
        sel = option.state & QStyle.State_Selected
        hov = option.state & QStyle.State_MouseOver
        p.setPen(Qt.NoPen)
        if sel:
            p.setBrush(QColor(theme.SURFACE3))
        elif hov:
            p.setBrush(QColor(theme.SURFACE2))
        else:
            p.setBrush(QColor(theme.SURFACE))
        p.drawRoundedRect(card, 10, 10)
        if sel:
            p.setBrush(QColor(theme.ACCENT))
            p.drawRoundedRect(QRectF(card.x(), card.y() + 12, 3, card.height() - 24), 1.5, 1.5)
        th = QRectF(card.x() + 10, card.y() + 9, 104, card.height() - 18)
        kind = d["kind"]
        clip = QPainterPath()
        clip.addRoundedRect(th, 6, 6)
        p.save()
        p.setClipPath(clip)
        pm = None
        thumb = d.get("thumb") or (d.get("data") or {}).get("thumb")
        if thumb and context.images:
            pm = context.images.get(thumb, 256)
        if pm is not None:
            s = max(th.width() / pm.width(), th.height() / pm.height())
            w, h = pm.width() * s, pm.height() * s
            p.drawPixmap(QRectF(th.center().x() - w / 2, th.center().y() - h / 2, w, h), pm, QRectF(pm.rect()))
        else:
            bg = {"evidence": "#2a2e36", "file": "#1f2a2c", "sticky": "#4a4630", "note": "#262a31",
                  "shape": "#262a3a", "frame": "#262a31"}.get(kind, "#2a2e36")
            p.fillRect(th, QColor(bg))
            if kind == "file":
                ext = (d.get("data") or {}).get("ext", "")
                p.setPen(QColor(theme.file_color(ext)))
                p.setFont(theme.icon_font(26))
                p.drawText(th, Qt.AlignCenter, theme.GLYPHS.get(theme.file_glyph(ext), ""))
            elif kind in ("sticky", "note"):
                p.setPen(QColor("#d8d2a0" if kind == "sticky" else theme.MUTED))
                p.setFont(theme.icon_font(24))
                p.drawText(th, Qt.AlignCenter, theme.GLYPHS["sticky"])
        p.restore()
        x = th.right() + 12
        w = card.right() - x - 12
        # title + time
        p.setFont(theme.ui_font(11))
        p.setPen(QColor(theme.MUTED))
        ts = d.get("_ts") or d.get("updated")
        tstr = nice_time(ts)
        tw = QFontMetrics(p.font()).horizontalAdvance(tstr)
        p.drawText(QRectF(card.right() - tw - 12, card.y() + 9, tw, 20), Qt.AlignRight | Qt.AlignVCenter, tstr)
        f = theme.ui_font(13.5, QFont.DemiBold)
        p.setFont(f)
        p.setPen(QColor(theme.TEXT))
        title = d.get("title") or {"file": "File", "sticky": "Sticky note", "note": "Note"}.get(kind, "Untitled")
        p.drawText(QRectF(x, card.y() + 8, w - tw - 10, 22), Qt.AlignLeft | Qt.AlignVCenter,
                   QFontMetrics(f).elidedText(title, Qt.ElideRight, int(w - tw - 10)))
        # snippet
        text = (d.get("text") or "").replace("\n", "  ")
        if text.startswith(title):
            text = text[len(title):].strip()
        if kind == "file":
            text = (d.get("data") or {}).get("path", "")
        p.setFont(theme.ui_font(12))
        p.setPen(QColor("#a2a7b2"))
        p.drawText(QRectF(x, card.y() + 31, w, 20), Qt.AlignLeft | Qt.AlignVCenter,
                   QFontMetrics(p.font()).elidedText(text or "—", Qt.ElideRight, int(w)))
        # breadcrumb
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(d.get("project_color") or theme.ACCENT))
        p.drawRoundedRect(QRectF(x, card.y() + 60, 8, 8), 2, 2)
        p.setFont(theme.ui_font(11))
        p.setPen(QColor(theme.FAINT))
        crumb = f"{d.get('project_name', '')} › {d.get('board_name', '')}"
        vn = int(d.get("vnum") or 0)
        if vn > 1:
            crumb += f"   ·   v{vn}"
        p.drawText(QRectF(x + 14, card.y() + 54, w - 14, 20), Qt.AlignLeft | Qt.AlignVCenter,
                   QFontMetrics(p.font()).elidedText(crumb, Qt.ElideRight, int(w - 14)))
        p.restore()


class TimelinePage(QWidget):
    def __init__(self, main):
        super().__init__()
        self.main = main
        self.setObjectName("Page")
        self.rows = []
        self._busy = False
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        top = QWidget()
        top.setObjectName("TopBar")
        tl = QVBoxLayout(top)
        tl.setContentsMargins(20, 14, 20, 12)
        tl.setSpacing(10)
        h = QHBoxLayout()
        self.title = label("Timeline", "BigTitle")
        h.addWidget(self.title)
        h.addSpacing(10)
        self.subtitle = label("", "Muted")
        h.addWidget(self.subtitle)
        h.addStretch(1)
        tl.addLayout(h)
        f = QHBoxLayout()
        f.setSpacing(8)
        self.project = QComboBox()
        self.project.setMinimumWidth(170)
        self.project.currentIndexChanged.connect(self._project_changed)
        self.board = QComboBox()
        self.board.setMinimumWidth(170)
        self.board.currentIndexChanged.connect(self.reload)
        self.sort = QComboBox()
        self.sort.addItem("Recently updated", "updated")
        self.sort.addItem("Recently created", "created")
        self.sort.currentIndexChanged.connect(self.reload)
        self.range = QComboBox()
        for txt, days in (("Any time", 0), ("Last 24 hours", 1), ("Last 7 days", 7), ("Last 30 days", 30),
                          ("Last 90 days", 90)):
            self.range.addItem(txt, days)
        self.range.currentIndexChanged.connect(self.reload)
        self.group = QComboBox()
        self.group.addItem("Group by date", "date")
        self.group.addItem("Group by project", "project")
        self.group.addItem("Group by board", "board")
        self.group.addItem("No grouping", "none")
        self.group.currentIndexChanged.connect(self.reload)
        for w in (self.project, self.board, self.sort, self.range, self.group):
            f.addWidget(w)
        f.addSpacing(8)
        self.kind_btns = {}
        for key, txt in (("evidence", "Evidence"), ("file", "Files"), ("note", "Notes")):
            b = push_button(txt, name="Chip")
            b.setCheckable(True)
            b.setChecked(key == "evidence")
            b.clicked.connect(self.reload)
            f.addWidget(b)
            self.kind_btns[key] = b
        f.addStretch(1)
        tl.addLayout(f)
        root.addWidget(top)

        split = QSplitter(Qt.Horizontal)
        self.list = QListWidget()
        self.list.setItemDelegate(FeedDelegate(self.list))
        self.list.setMouseTracking(True)
        self.list.setVerticalScrollMode(QAbstractItemView.ScrollPerPixel)
        self.list.setUniformItemSizes(False)
        self.list.currentItemChanged.connect(self._show)
        self.list.itemDoubleClicked.connect(lambda li: self._open(li.data(ROLE)))
        self.list.setStyleSheet("QListWidget { padding: 6px; } QListWidget::item { padding: 0; }"
                                "QListWidget::item:selected, QListWidget::item:hover { background: transparent; }")
        split.addWidget(self.list)
        self.list.verticalScrollBar().valueChanged.connect(lambda _: self.list.viewport().update())
        context.images.loaded.connect(lambda _: self.list.viewport().update())

        detail = QWidget()
        dl = QVBoxLayout(detail)
        dl.setContentsMargins(18, 16, 22, 16)
        dl.setSpacing(8)
        self.d_crumb = label("", "Muted")
        self.d_title = label("", "Title", wrap=True)
        self.d_meta = label("", "Hint")
        dl.addWidget(self.d_crumb)
        dl.addWidget(self.d_title)
        dl.addWidget(self.d_meta)
        self.stack = QStackedWidget()
        self.preview = SlidePreview(bg="#0f1115")
        self.file_text = QPlainTextEdit()
        self.file_text.setReadOnly(True)
        self.file_text.setStyleSheet("QPlainTextEdit { font-family: Consolas; font-size: 10pt; background: #15171c; }")
        self.stack.addWidget(self.preview)
        self.stack.addWidget(self.file_text)
        dl.addWidget(self.stack, 3)
        self.d_text = QPlainTextEdit()
        self.d_text.setReadOnly(True)
        self.d_text.setMaximumHeight(160)
        dl.addWidget(self.d_text, 1)
        bl = QHBoxLayout()
        self.b_open = push_button("Open on board", "board", primary=True)
        self.b_open.clicked.connect(lambda: self._open(self._cur()))
        self.b_edit = push_button("Edit", "edit")
        self.b_edit.clicked.connect(self._edit)
        self.b_hist = push_button("History", "history")
        self.b_hist.clicked.connect(self._history)
        for b in (self.b_open, self.b_edit, self.b_hist):
            bl.addWidget(b)
        bl.addStretch(1)
        dl.addLayout(bl)
        self.empty = label("Nothing here yet.\nEvidence you add to any board shows up here, newest first.", "Muted",
                           wrap=True)
        self.empty.setAlignment(Qt.AlignCenter)
        dl.addWidget(self.empty)
        split.addWidget(detail)
        split.setSizes([560, 640])
        split.setStretchFactor(0, 1)
        split.setStretchFactor(1, 1)
        root.addWidget(split, 1)

    # ------------------------------------------------------------------ scope
    def open_scope(self, project_id=None, board_id=None):
        st = context.store
        if board_id and not project_id:
            b = st.board(board_id)
            project_id = b["project_id"] if b else None
        self._busy = True
        self.project.clear()
        self.project.addItem("All projects", None)
        for p in st.projects():
            self.project.addItem(theme.color_dot_icon(p["color"]), p["name"], p["id"])
        i = self.project.findData(project_id) if project_id else 0
        self.project.setCurrentIndex(max(0, i))
        self._fill_boards(board_id)
        self._busy = False
        self.reload()

    def _fill_boards(self, board_id=None):
        st = context.store
        pid = self.project.currentData()
        self.board.blockSignals(True)
        self.board.clear()
        self.board.addItem("All boards", None)
        if pid:
            for b in st.boards(pid):
                self.board.addItem(theme.icon("board", 14, theme.MUTED), b["name"], b["id"])
        self.board.setEnabled(bool(pid))
        i = self.board.findData(board_id) if board_id else 0
        self.board.setCurrentIndex(max(0, i))
        self.board.blockSignals(False)

    def _project_changed(self):
        if self._busy:
            return
        self._fill_boards()
        self.reload()

    # ------------------------------------------------------------------ data
    def reload(self):
        if self._busy:
            return
        st = context.store
        pid = self.project.currentData()
        bid = self.board.currentData()
        sort = self.sort.currentData()
        days = self.range.currentData()
        since = time.time() - days * 86400 if days else None
        kinds = []
        if self.kind_btns["evidence"].isChecked():
            kinds.append("evidence")
        if self.kind_btns["file"].isChecked():
            kinds.append("file")
        if self.kind_btns["note"].isChecked():
            kinds += ["note", "sticky", "shape"]
        if not kinds:
            kinds = ["evidence"]
            self.kind_btns["evidence"].setChecked(True)
        rows = st.feed(project_id=pid, board_id=bid, kinds=tuple(kinds), sort=sort, since=since)
        if self.kind_btns["note"].isChecked():
            rows = [r for r in rows if r["kind"] not in ("note", "shape") or (r.get("text") or "").strip()]
        self.rows = rows
        scope = "all projects"
        if bid:
            scope = f"{self.board.currentText()}"
        elif pid:
            scope = self.project.currentText()
        self.subtitle.setText(f"{len(rows)} item{'s' if len(rows) != 1 else ''} · {scope}")
        mode = self.group.currentData()
        self.list.clear()
        groups, order = {}, []
        for r in rows:
            r["_ts"] = r["created"] if sort == "created" else r["updated"]
            if mode == "date":
                g = date_group(r["_ts"])
            elif mode == "project":
                g = r["project_name"]
            elif mode == "board":
                g = f"{r['project_name']} › {r['board_name']}"
            else:
                g = ""
            if g not in groups:
                groups[g] = []
                order.append(g)
            groups[g].append(r)
        for g in order:
            if g:
                hi = QListWidgetItem()
                hi.setData(ROLE, {"_header": g, "_count": len(groups[g])})
                hi.setFlags(Qt.NoItemFlags)
                self.list.addItem(hi)
            for r in groups[g]:
                li = QListWidgetItem()
                li.setData(ROLE, r)
                self.list.addItem(li)
        has = bool(rows)
        self.empty.setVisible(not has)
        for w in (self.d_crumb, self.d_title, self.d_meta, self.stack, self.d_text, self.b_open, self.b_edit,
                  self.b_hist):
            w.setVisible(has)
        for i in range(self.list.count()):
            if not self.list.item(i).data(ROLE).get("_header"):
                self.list.setCurrentRow(i)
                break

    def _cur(self):
        li = self.list.currentItem()
        d = li.data(ROLE) if li else None
        return None if (d is None or d.get("_header")) else d

    def _show(self, *_):
        d = self._cur()
        if d is None:
            return
        st = context.store
        self.d_crumb.setText(f"{d['project_name']}  ›  {d['board_name']}")
        self.d_title.setText(d.get("title") or "Untitled")
        vn = int(d.get("vnum") or 0)
        meta = f"Created {nice_time(d['created'])}  ·  updated {nice_time(d['updated'])}"
        if vn > 1:
            meta += f"  ·  {vn} versions"
        self.d_meta.setText(meta)
        kind = d["kind"]
        self.b_edit.setVisible(kind == "evidence")
        self.b_hist.setVisible(kind in ("evidence", "file"))
        if kind == "evidence":
            v = st.current_version(d["id"])
            self.stack.setCurrentWidget(self.preview)
            self.preview.set_doc(v["doc"] if v else {})
            txt = (v or {}).get("text") or ""
            self.d_text.setPlainText(txt)
            self.d_text.setVisible(bool(txt.strip()))
        elif kind == "file":
            v = st.current_version(d["id"])
            doc = (v or {}).get("doc") or {}
            self.stack.setCurrentWidget(self.file_text)
            self.file_text.setPlainText(doc.get("preview") or "(no preview)")
            self.d_text.setPlainText(f"{d['data'].get('path', '')}\n{human_size(doc.get('size'))}")
            self.d_text.setVisible(True)
        else:
            self.stack.setCurrentWidget(self.file_text)
            self.file_text.setPlainText(d.get("text") or "")
            self.d_text.setVisible(False)

    def _open(self, d):
        if d and not d.get("_header"):
            self.main.open_board(d["board_id"], focus_item=d["id"])

    def _edit(self):
        d = self._cur()
        if d:
            self.main.open_editor_item(d["id"])

    def _history(self):
        d = self._cur()
        if not d:
            return
        from .versions import VersionsDialog
        dlg = VersionsDialog(d["id"], None, self)
        dlg.edit_version.connect(lambda iid, vid: self.main.open_editor_item(iid, vid))
        dlg.restored.connect(lambda _: self.reload())
        dlg.exec()
