"""The board page: toolbar, canvas with overlays, inspector, context menus, clipboard and drag-and-drop."""
import json
import math
import time
from pathlib import Path

from PySide6.QtCore import QMimeData, QPointF, QRectF, Qt, QTimer, QVariantAnimation, Signal
from PySide6.QtGui import QColor, QImage, QPen
from PySide6.QtWidgets import (QApplication, QFileDialog, QFrame, QGraphicsRectItem, QHBoxLayout, QLabel, QLineEdit,
                               QMenu, QMessageBox, QToolButton, QVBoxLayout, QWidget)

from .. import context, evidence, geometry, theme
from ..widgets.common import label, push_button, tool_button, vsep
from .formatbar import FormatBar
from .inspector import BoardInspector
from .items import BoardItem, BoardLinkCard, EvidenceCard, FileCard, FrameItem, LineItem, TextHolder
from .scene import BOARD_MIME, BOARDREF_MIME, BoardScene
from .strings import StringItem
from .view import BACKGROUNDS, BoardView, Minimap

TOOLS = [("select", "cursor", "Select (V)"), ("hand", "tool_hand", "Pan (H) — or right-drag / Space-drag"),
         ("string", "tool_string", "Red string (S) — drag from one card to another"),
         ("text", "tool_text", "Text (T)"), ("sticky", "sticky", "Sticky note (N)")]
TOOLS2 = [("line", "tool_arrow", "Arrow / line (L)"), ("frame", "tool_frame", "Frame (F) — group a region")]
KEYS = {Qt.Key_V: "select", Qt.Key_H: "hand", Qt.Key_S: "string", Qt.Key_T: "text", Qt.Key_N: "sticky",
        Qt.Key_L: "line", Qt.Key_F: "frame"}


class CanvasHost(QWidget):
    resized = Signal()

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self.resized.emit()


class LabelEdit(QLineEdit):
    cancelled = Signal()

    def keyPressEvent(self, e):
        if e.key() == Qt.Key_Escape:
            self.cancelled.emit()
            return
        super().keyPressEvent(e)


class BoardPage(QWidget):
    def __init__(self, main):
        super().__init__()
        self.main = main
        self.setObjectName("Page")
        self.scene = None
        self.board = None
        self.last_shape = "round"
        self._label_target = None
        self._build()

    # ------------------------------------------------------------------ layout
    def _build(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        top = QWidget()
        top.setObjectName("TopBar")
        tl = QHBoxLayout(top)
        tl.setContentsMargins(12, 7, 10, 7)
        tl.setSpacing(4)
        self.proj_dot = QLabel()
        tl.addWidget(self.proj_dot)
        self.proj_name = QToolButton()
        self.proj_name.setObjectName("TextTool")
        self.proj_name.setStyleSheet(f"color: {theme.MUTED};")
        self.proj_name.setToolTip("Open the project map")
        self.proj_name.clicked.connect(lambda: self.main.open_project_map(self.board["project_id"]))
        tl.addWidget(self.proj_name)
        tl.addWidget(label("›", "Faint"))
        self.name_edit = QLineEdit()
        self.name_edit.setObjectName("TitleEdit")
        self.name_edit.setMinimumWidth(120)
        self.name_edit.setMaximumWidth(320)
        self.name_edit.editingFinished.connect(self._rename)
        tl.addWidget(self.name_edit)
        tl.addSpacing(10)
        tl.addWidget(vsep())
        self.tool_btns = {}
        for key, ic, tip in TOOLS:
            tl.addWidget(self._tool_btn(key, ic, tip))
        self.shape_btn = tool_button("shape_round", "Shape (R)", True)
        self.shape_btn.setPopupMode(QToolButton.MenuButtonPopup)
        self.shape_btn.clicked.connect(lambda: self.set_tool("shape:" + self.last_shape))
        sm = QMenu(self.shape_btn)
        for k, n in geometry.SHAPES:
            sm.addAction(theme.icon("shape_" + k, 16), n, lambda kk=k: self._pick_shape(kk))
        self.shape_btn.setMenu(sm)
        tl.addWidget(self.shape_btn)
        for key, ic, tip in TOOLS2:
            tl.addWidget(self._tool_btn(key, ic, tip))
        tl.addWidget(vsep())
        add = push_button("Evidence", "add", primary=True)
        add.setToolTip("Add evidence (E) — opens the slide editor")
        add.clicked.connect(lambda: self.new_evidence(self.free_spot(self.view_center())))
        tl.addWidget(add)
        att = tool_button("attach", "Attach files or scripts…", text="Attach")
        att.clicked.connect(self.attach_dialog)
        tl.addWidget(att)
        tl.addStretch(1)
        self.undo_b = tool_button("undo", "Undo (Ctrl+Z)")
        self.undo_b.clicked.connect(self.undo)
        self.redo_b = tool_button("redo", "Redo (Ctrl+Y)")
        self.redo_b.clicked.connect(self.redo)
        tl.addWidget(self.undo_b)
        tl.addWidget(self.redo_b)
        tl.addWidget(vsep())
        tlb = tool_button("timeline", "This board's evidence as a dated list")
        tlb.clicked.connect(self._open_timeline)
        tl.addWidget(tlb)
        sb = tool_button("search", "Search this board (Ctrl+F)")
        sb.clicked.connect(lambda: self.main.open_search("board"))
        tl.addWidget(sb)
        ex = tool_button("export", "Export / print", text="Export")
        ex.setPopupMode(QToolButton.InstantPopup)
        em = QMenu(ex)
        em.addAction(theme.icon("pdf", 16), "PDF…", lambda: self.main.export_board(self.board["id"], "pdf"))
        em.addAction(theme.icon("slides", 16), "PowerPoint (.pptx)…",
                     lambda: self.main.export_board(self.board["id"], "pptx"))
        em.addAction(theme.icon("print", 16), "Print…", lambda: self.main.export_board(self.board["id"], "print"))
        em.addSeparator()
        em.addAction(theme.icon("image", 16), "Board as image (PNG)…", self.export_png)
        ex.setMenu(em)
        tl.addWidget(ex)
        more = tool_button("more", "Board options")
        more.setPopupMode(QToolButton.InstantPopup)
        self.more_menu = QMenu(more)
        self.more_menu.aboutToShow.connect(self._fill_more)
        more.setMenu(self.more_menu)
        tl.addWidget(more)
        self.coinv_btn = tool_button("coinv", "Co-Investigator — ask an AI what it makes of this board", True,
                                     color=theme.COINV)
        self.coinv_btn.clicked.connect(lambda on: self.show_side("coinv" if on else None))
        tl.addWidget(self.coinv_btn)
        self.insp_btn = tool_button("menu", "Show / hide details panel (I)", True)
        self.insp_btn.setChecked(True)
        self.insp_btn.clicked.connect(lambda on: self.show_side("details" if on else None))
        tl.addWidget(self.insp_btn)
        root.addWidget(top)

        body = QHBoxLayout()
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(0)
        self.canvas = CanvasHost()
        cl = QVBoxLayout(self.canvas)
        cl.setContentsMargins(0, 0, 0, 0)
        self.view = BoardView(self.canvas)
        self.view.key_handler = self.handle_key
        cl.addWidget(self.view)
        body.addWidget(self.canvas, 1)
        self.inspector = BoardInspector(self)
        body.addWidget(self.inspector)
        from ..widgets.coinvestigator import CoInvestigatorPanel
        self.coinv = CoInvestigatorPanel(self)
        self.coinv.setFixedWidth(400)
        self.coinv.hide()
        body.addWidget(self.coinv)
        root.addLayout(body, 1)

        # overlays
        self.zoom_box = QFrame(self.canvas)
        self.zoom_box.setObjectName("Floating")
        zl = QHBoxLayout(self.zoom_box)
        zl.setContentsMargins(4, 3, 4, 3)
        zl.setSpacing(2)
        zo = tool_button("zoomout", "Zoom out (Ctrl+-)", size=15)
        zo.clicked.connect(lambda: self.view.zoom_by(1 / 1.25))
        self.zoom_lbl = QToolButton()
        self.zoom_lbl.setText("100%")
        self.zoom_lbl.setFixedWidth(52)
        self.zoom_lbl.setPopupMode(QToolButton.InstantPopup)
        zm = QMenu(self.zoom_lbl)
        zm.addAction("Fit everything  (Ctrl+0)", self.fit)
        for z in (0.25, 0.5, 1.0, 1.5, 2.0):
            zm.addAction(f"{int(z * 100)}%", lambda zz=z: self.view.set_zoom(zz))
        self.zoom_lbl.setMenu(zm)
        zi = tool_button("zoomin", "Zoom in (Ctrl+=)", size=15)
        zi.clicked.connect(lambda: self.view.zoom_by(1.25))
        fit = tool_button("fit", "Fit everything (Ctrl+0)", size=15)
        fit.clicked.connect(self.fit)
        self.mm_btn = tool_button("grid", "Minimap (M)", True, size=15)
        self.mm_btn.clicked.connect(lambda on: self.minimap.setVisible(on))
        for w in (zo, self.zoom_lbl, zi, fit, self.mm_btn):
            w.setFocusPolicy(Qt.NoFocus)
            zl.addWidget(w)
        self.view.zoom_changed.connect(lambda z: self.zoom_lbl.setText(f"{round(z * 100)}%"))
        self.minimap = Minimap(self.view, self.canvas)
        self.format_bar = FormatBar(self.view)
        from ..settings import settings
        show_mm = bool(settings.get("show_minimap", True))
        self.minimap.setVisible(show_mm)
        self.mm_btn.setChecked(show_mm)
        self.empty = QLabel(self.canvas)
        self.empty.setAlignment(Qt.AlignCenter)
        self.empty.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.empty.setStyleSheet(f"color: {theme.FAINT}; font-size: 12pt;")
        self.empty.setText("<div style='font-size:20pt; color:#8c919d; font-weight:600'>An empty board</div>"
                           "<br>Right-click → <b>Add evidence</b> · drop files, scripts or screenshots here"
                           "<br>Ctrl+V pastes a screenshot · drag between red pins to tie a string")
        self.lost_btn = push_button("Nothing in view — back to the board", "fit", primary=True)
        self.lost_btn.setParent(self.canvas)
        self.lost_btn.hide()
        self.lost_btn.clicked.connect(lambda: self.fit(max_zoom=1.0))
        self._lost_timer = QTimer(self)
        self._lost_timer.setSingleShot(True)
        self._lost_timer.setInterval(150)
        self._lost_timer.timeout.connect(self._check_lost)
        self.view.viewport_changed.connect(self._lost_timer.start)
        self.label_editor = LabelEdit(self.canvas)
        self.label_editor.setPlaceholderText("Label this string… (Enter to save, Esc to skip)")
        self.label_editor.setFixedWidth(300)
        self.label_editor.hide()
        self.label_editor.returnPressed.connect(self._commit_label)
        self.label_editor.cancelled.connect(self._cancel_label)
        self.canvas.resized.connect(self._canvas_resized)

        self.view.context_menu.connect(self.context_menu)
        self.view.create_requested.connect(self._create)
        self.view.dropped.connect(self.handle_drop)

    def _tool_btn(self, key, ic, tip):
        b = tool_button(ic, tip, True)
        b.setFocusPolicy(Qt.NoFocus)
        b.clicked.connect(lambda _=False, k=key: self.set_tool(k))
        self.tool_btns[key] = b
        return b

    def _canvas_resized(self):
        w, h = self.canvas.width(), self.canvas.height()
        self.zoom_box.adjustSize()
        self.zoom_box.move(w - self.zoom_box.width() - 14, h - self.zoom_box.height() - 14)
        self.minimap.move(14, h - self.minimap.height() - 14)
        self.empty.setGeometry(0, 0, w, h)
        self.lost_btn.adjustSize()
        self.lost_btn.move((w - self.lost_btn.width()) // 2, 18)
        self.zoom_box.raise_()
        self.minimap.raise_()
        self.lost_btn.raise_()

    # ------------------------------------------------------------------ board lifecycle
    def is_map(self):
        return context.store.is_map(self.board)

    def open_board(self, board_id, focus_item=None, fit=False):
        st = context.store
        b = st.board(board_id)
        if b is None:
            return False
        if st.is_map(b):
            st.sync_project_map(board_id)
        self.save_view_state()
        old = self.scene
        self.board = b
        self.format_bar.attach(None)
        self.scene = BoardScene(b)
        self.view.setScene(self.scene)
        if old is not None:
            old.teardown()
            old.deleteLater()
        self.view.background = b.get("background") or "midnight"
        self.view.resetCachedContent()
        self.view.viewport().update()
        sc = self.scene
        sc.selectionChanged.connect(self.inspector.refresh)
        sc.label_edit.connect(self.edit_label)
        sc.text_editing.connect(self.format_bar.attach)
        sc.text_cursor_moved.connect(self.format_bar.refresh)
        sc.open_item.connect(self._open_item)
        sc.undo_changed.connect(self._update_undo)
        sc.changed.connect(self._scene_changed)
        self.coinv.set_board(b)
        p = st.project(b["project_id"])
        self.proj_dot.setPixmap(theme.color_dot_icon(p["color"] if p else "#888", 12).pixmap(12, 12))
        self.proj_name.setText(p["name"] if p else "")
        self.name_edit.setText(b["name"])
        self.name_edit.setReadOnly(st.is_map(b))
        self.name_edit.setToolTip("The project map shows every board in this project. Tie strings between boards "
                                  "to show how they relate." if st.is_map(b) else "Click to rename")
        self.set_tool("select")
        self._update_undo()
        self.inspector.refresh()
        self._update_empty()
        QTimer.singleShot(0, lambda: self._restore_view(b, focus_item, fit))
        return True

    def _restore_view(self, b, focus_item, fit=False):
        if self.scene is None:
            return
        if focus_item and focus_item in self.scene.items_by_id:
            self.focus_item(focus_item)
        elif fit or not b.get("view"):
            self.fit(max_zoom=1.0)
        else:
            self.view.restore_state(b["view"])
            if self._is_lost():
                self.fit(max_zoom=1.0)
        self.minimap.rebuild()
        self._check_lost()
        self.view.setFocus()

    def _is_lost(self):
        """True when nothing on the board is on screen (panned away, or zoomed out to a speck)."""
        sc = self.scene
        if sc is None or not sc.items_by_id:
            return False
        content = sc.content_rect()
        if not self.view.visible_scene_rect().intersects(content):
            return True
        return max(content.width(), content.height()) * self.view.zoom() < 40

    def _check_lost(self):
        lost = self._is_lost()
        if lost != self.lost_btn.isVisible():
            self.lost_btn.setVisible(lost)
            if lost:
                self._canvas_resized()

    def _open_timeline(self):
        if self.is_map():
            self.main.open_timeline(project_id=self.board["project_id"])
        else:
            self.main.open_timeline(board_id=self.board["id"])

    def save_view_state(self):
        if self.board is not None and self.scene is not None:
            try:
                context.store.update_board(self.board["id"], touch=False, view=self.view.view_state())
            except Exception:
                pass

    def close_board(self):
        self.save_view_state()
        if self.scene is not None:
            self.view.setScene(None)
            self.scene.teardown()
            self.scene.deleteLater()
        self.scene = None
        self.board = None

    def _scene_changed(self):
        self.minimap.schedule()
        self._update_empty()
        self._lost_timer.start()
        self.main.board_changed()

    def _update_empty(self):
        self.empty.setVisible(self.scene is not None and not self.scene.items_by_id)

    def _update_undo(self):
        sc = self.scene
        self.undo_b.setEnabled(bool(sc and sc.undo_stack))
        self.redo_b.setEnabled(bool(sc and sc.redo_stack))
        if sc and sc.undo_stack:
            self.undo_b.setToolTip(f"Undo {sc.undo_stack[-1][2].lower()} (Ctrl+Z)")

    def _rename(self):
        t = self.name_edit.text().strip()
        if self.board and t and t != self.board["name"] and not self.is_map():
            context.store.update_board(self.board["id"], name=t)
            self.board["name"] = t
            self.main.refresh_sidebar()

    # ------------------------------------------------------------------ tools
    def set_tool(self, tool):
        if self.scene is not None:
            self.scene.tool = "string" if tool == "string" else ("select" if tool == "select" else tool)
        self.view.set_tool(tool)
        for k, b in self.tool_btns.items():
            b.setChecked(k == tool)
        self.shape_btn.setChecked(tool.startswith("shape:"))

    def _pick_shape(self, k):
        self.last_shape = k
        self.shape_btn.setIcon(theme.icon("shape_" + k, 18))
        self.set_tool("shape:" + k)

    def view_center(self):
        return self.view.mapToScene(self.view.viewport().rect().center())

    def free_spot(self, center, w=300.0, h=240.0):
        """Nearest point to `center` where a new card won't land on top of existing ones."""
        if self.scene is None:
            return center
        rects = [it.scene_rect() for it in self.scene.items_by_id.values() if not isinstance(it, FrameItem)]

        def clear(c):
            r = QRectF(c.x() - w / 2 - 24, c.y() - h / 2 - 24, w + 48, h + 48)
            return not any(r.intersects(o) for o in rects)
        if clear(center):
            return center
        for ring in range(1, 40):
            n = ring * 8
            for k in range(n):
                a = 2 * math.pi * k / n
                c = QPointF(center.x() + math.cos(a) * ring * 110, center.y() + math.sin(a) * ring * 80)
                if clear(c):
                    return c
        return center

    def fit(self, max_zoom=1.5):
        if self.scene is None:
            return
        r = self.scene.content_rect()
        sel = self.scene.selected_board_items()
        if sel:
            r = QRectF()
            for it in sel:
                r = it.scene_rect() if r.isNull() else r.united(it.scene_rect())
        self.view.fit_rect(r, max_zoom)

    # ------------------------------------------------------------------ creation
    def _create(self, tool, a, b):
        st = context.store
        bid = self.board["id"]
        r = QRectF(a, b).normalized()
        small = r.width() * self.view.zoom() < 8 and r.height() * self.view.zoom() < 8
        sticky_tool = QApplication.keyboardModifiers() & Qt.ShiftModifier
        rows, edit = [], False
        if tool == "text":
            rows.append(st.new_item(bid, "note", a.x(), a.y() - 18, 300, 40, {"text": "", "size": 20}))
            edit = True
        elif tool == "sticky":
            rows.append(st.new_item(bid, "sticky", a.x() - 110, a.y() - 24, 220, 200,
                                    {"text": "", "fill": "#fff09a", "size": 18, "font": "Segoe Print"}))
            edit = True
        elif tool.startswith("shape:"):
            if small:
                r = QRectF(a.x() - 100, a.y() - 60, 200, 120)
            rows.append(st.new_item(bid, "shape", r.x(), r.y(), r.width(), r.height(),
                                    {"shape": tool.split(":", 1)[1], "fill": "#5b8cff", "text": "", "size": 18}))
        elif tool == "frame":
            if small:
                r = QRectF(a.x() - 320, a.y() - 30, 640, 420)
            rows.append(st.new_item(bid, "frame", r.x(), r.y(), max(160, r.width()), max(120, r.height()),
                                    {"title": "New frame", "color": "#9aa0ad"}))
        elif tool == "line":
            d = b - a
            if math.hypot(d.x(), d.y()) * self.view.zoom() < 6:
                d = QPointF(200, 0)
            rows.append(st.new_item(bid, "line", a.x(), a.y(), abs(d.x()), abs(d.y()),
                                    {"x2": d.x(), "y2": d.y(), "color": "#e9eaee", "sw": 3, "a2": True}))
        if not rows:
            return
        items = self.scene.add_rows(rows, label="Add " + rows[0]["kind"])
        if not sticky_tool:
            self.set_tool("select")
        it = items[0]
        if edit and isinstance(it, TextHolder):
            it.fresh = it.KIND == "note"
            self.view.setFocus()
            it.begin_edit()

    def new_evidence(self, pos):
        self.main.open_editor_new(self.board["id"], pos)

    def after_editor(self, result):
        """Called when the evidence editor closes, to show the new/updated card."""
        if self.scene is None or result.get("board_id") != self.board["id"]:
            return
        iid = result.get("item_id")
        if result.get("created") and iid:
            it = self.scene.add_existing(iid)
            if it is not None:
                self.pulse(it)
        elif result.get("saved") and iid:
            it = self.scene.refresh_item(iid)
            if it is not None:
                self.scene.clearSelection()
                it.setSelected(True)
                self.pulse(it)
        self.inspector.refresh()

    def attach_dialog(self):
        paths, _ = QFileDialog.getOpenFileNames(self, "Attach files, scripts or programs", "", "All files (*)")
        if paths:
            self.drop_paths(paths, self.free_spot(self.view_center(), 340, 250), force_files=True)

    def drop_paths(self, paths, pos, force_files=False):
        ids = []
        n = len(paths)
        cols = max(1, int(math.ceil(math.sqrt(n))))
        shift = QApplication.keyboardModifiers() & Qt.ShiftModifier
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            for i, p in enumerate(paths):
                c = QPointF(pos.x() + (i % cols) * 360, pos.y() + (i // cols) * 280)
                ext = Path(p).suffix.lower().lstrip(".")
                if ext in evidence.IMAGE_EXTS and not (force_files or shift):
                    iid = evidence.evidence_from_image_file(self.board["id"], c, p)
                else:
                    iid = evidence.attach_file(self.board["id"], c, p)
                if iid:
                    ids.append(iid)
        finally:
            QApplication.restoreOverrideCursor()
        if ids:
            rows = [context.store.item(i) for i in ids]
            self.scene.add_rows([r for r in rows if r], label="Add files")
            self.main.toast(f"Added {len(ids)} item{'s' if len(ids) != 1 else ''}")

    def handle_drop(self, md, pos):
        if self.scene is None:
            return
        if md.hasFormat(BOARDREF_MIME):
            self.add_board_link(bytes(md.data(BOARDREF_MIME)).decode(), pos)
        elif md.hasFormat(BOARD_MIME):
            self.scene.paste_payload(json.loads(bytes(md.data(BOARD_MIME)).decode()), at=pos)
        elif md.hasUrls() and any(u.isLocalFile() for u in md.urls()):
            self.drop_paths([u.toLocalFile() for u in md.urls() if u.isLocalFile()], pos)
        elif md.hasImage():
            self._add_image_evidence(QImage(md.imageData()), pos)
        elif md.hasText() and md.text().strip():
            self._add_text_note(md.text(), pos)

    def _add_image_evidence(self, img, pos):
        if img.isNull():
            return
        iid = evidence.evidence_from_qimage(self.board["id"], pos, img, time.strftime("Screenshot %b %d, %I:%M %p"))
        it = self.scene.add_existing(iid)
        if it is not None:
            self.pulse(it)
        self.main.toast("Screenshot pinned as new evidence")

    def _add_text_note(self, text, pos):
        st = context.store
        row = st.new_item(self.board["id"], "sticky", pos.x() - 130, pos.y() - 100, 260, 220,
                          {"text": text.strip()[:5000], "fill": "#fff09a", "size": 16, "font": "Segoe Print"})
        self.scene.add_rows([row], label="Paste text")

    # ------------------------------------------------------------------ clipboard
    def copy(self, cut=False):
        items = self.scene.selected_board_items()
        if not items:
            return
        payload = self.scene.copy_payload(items)
        md = QMimeData()
        md.setData(BOARD_MIME, json.dumps(payload).encode())
        md.setText("\n\n".join(t for t in (i.search_text() for i in items) if t))
        QApplication.clipboard().setMimeData(md)
        if cut:
            self.scene.delete(items)
        self.main.toast(f"{'Cut' if cut else 'Copied'} {len(items)} item{'s' if len(items) != 1 else ''}")

    def paste(self, at=None):
        md = QApplication.clipboard().mimeData()
        pos = at or self.free_spot(self.view_center())
        if md.hasFormat(BOARD_MIME):
            self.scene.paste_payload(json.loads(bytes(md.data(BOARD_MIME)).decode()), at=at)
        elif md.hasImage():
            self._add_image_evidence(QImage(md.imageData()), pos)
        elif md.hasUrls() and any(u.isLocalFile() for u in md.urls()):
            self.drop_paths([u.toLocalFile() for u in md.urls() if u.isLocalFile()], pos)
        elif md.hasText() and md.text().strip():
            self._add_text_note(md.text(), pos)

    def duplicate(self):
        items = self.scene.selected_board_items()
        if items:
            self.scene.paste_payload(self.scene.copy_payload(items), offset=40)

    # ------------------------------------------------------------------ keyboard
    def handle_key(self, e):
        if self.scene is None:
            return False
        k = e.key()
        mods = e.modifiers()
        sc = self.scene
        if mods & Qt.ControlModifier:
            shift = mods & Qt.ShiftModifier
            if k == Qt.Key_Z:
                self.redo() if shift else self.undo()
            elif k == Qt.Key_Y:
                self.redo()
            elif k == Qt.Key_C:
                self.copy()
            elif k == Qt.Key_X:
                self.copy(cut=True)
            elif k == Qt.Key_V:
                self.paste()
            elif k == Qt.Key_D:
                self.duplicate()
            elif k == Qt.Key_A:
                for it in list(sc.items_by_id.values()) + list(sc.strings_by_id.values()):
                    it.setSelected(True)
            elif k == Qt.Key_0:
                self.fit()
            elif k == Qt.Key_1:
                self.view.set_zoom(1.0)
            elif k in (Qt.Key_Equal, Qt.Key_Plus):
                self.view.zoom_by(1.25)
            elif k == Qt.Key_Minus:
                self.view.zoom_by(1 / 1.25)
            else:
                return False
            return True
        if mods & Qt.AltModifier:
            return False
        if k in KEYS:
            self.set_tool(KEYS[k])
        elif k == Qt.Key_R:
            self.set_tool("shape:" + self.last_shape)
        elif k == Qt.Key_E:
            self.new_evidence(self.free_spot(self.view_center()))
        elif k == Qt.Key_M:
            self.mm_btn.click()
        elif k == Qt.Key_I:
            self.insp_btn.click()
        elif k in (Qt.Key_Delete, Qt.Key_Backspace):
            self.delete_selected()
        elif k in (Qt.Key_Return, Qt.Key_Enter, Qt.Key_F2):
            sel = sc.selected_board_items()
            strings = sc.selected_strings()
            if len(sel) == 1:
                it = sel[0]
                if isinstance(it, EvidenceCard):
                    self.edit_evidence(it)
                elif isinstance(it, FileCard):
                    self.open_file(it)
                elif isinstance(it, TextHolder):
                    it.begin_edit()
                elif isinstance(it, BoardLinkCard):
                    self.open_linked(it)
            elif strings:
                self.edit_label(strings[0])
        elif k == Qt.Key_Escape:
            if self.view.tool != "select":
                self.set_tool("select")
            else:
                sc.clearSelection()
        elif k in (Qt.Key_Left, Qt.Key_Right, Qt.Key_Up, Qt.Key_Down):
            step = 10 if mods & Qt.ShiftModifier else 1
            dx = {Qt.Key_Left: -step, Qt.Key_Right: step}.get(k, 0)
            dy = {Qt.Key_Up: -step, Qt.Key_Down: step}.get(k, 0)
            items = sc.selected_board_items()
            if items:
                before = [("item", dict(i.row)) for i in items]
                for i in items:
                    i.moveBy(dx, dy)
                sc.commit(before, [("item", i.current_row()) for i in items], "Nudge")
        else:
            return False
        return True

    def undo(self):
        if self.scene is not None:
            lab = self.scene.undo()
            if lab:
                self.main.toast(f"Undid {lab.lower()}", 1200)
            self.inspector.refresh()

    def redo(self):
        if self.scene is not None:
            lab = self.scene.redo()
            if lab:
                self.main.toast(f"Redid {lab.lower()}", 1200)
            self.inspector.refresh()

    # ------------------------------------------------------------------ item actions
    def _open_item(self, it):
        if isinstance(it, EvidenceCard):
            self.edit_evidence(it)
        elif isinstance(it, FileCard):
            self.open_file(it)
        elif isinstance(it, BoardLinkCard):
            self.open_linked(it)

    # ------------------------------------------------------------------ board links
    def open_linked(self, it):
        info = it.info or {}
        if not info or info.get("gone"):
            self.main.toast("That board was deleted — restore it from the Trash")
            return
        self.main.open_board(it.linked_id(), fit=True)

    def add_board_link(self, board_id, pos):
        st = context.store
        b = st.board(board_id)
        if b is None or self.scene is None:
            return
        if board_id == self.board["id"]:
            self.main.toast("That's this board")
            return
        for it in self.scene.items_by_id.values():
            if isinstance(it, BoardLinkCard) and it.linked_id() == board_id and self.is_map():
                self.focus_item(it.id)
                self.main.toast(f"“{b['name']}” is already on this map")
                return
        row = st.new_item(self.board["id"], "boardlink", pos.x() - 160, pos.y() - 115, 320, 230, {"board_id": board_id})
        it = self.scene.add_rows([row], label="Link board")[0]
        if it is not None:
            self.pulse(it)
            self.main.toast(f"Linked “{b['name']}” — tie strings to it, double-click to jump there")

    def _link_menu(self, menu, pos):
        st = context.store
        sub = menu.addMenu(theme.icon("board", 16), "Link a board here")
        for p in st.projects():
            for b in st.boards(p["id"]):
                if b["id"] == self.board["id"] or (self.is_map() and b["project_id"] == self.board["project_id"]):
                    continue
                sub.addAction(theme.color_dot_icon(p["color"]), f"{p['name']} › {b['name']}",
                              lambda bid=b["id"]: self.add_board_link(bid, pos))
        if sub.isEmpty():
            sub.setEnabled(False)

    def rename_linked(self, it):
        from PySide6.QtWidgets import QInputDialog
        b = (it.info or {}).get("board")
        if not b:
            return
        name, ok = QInputDialog.getText(self, "Rename board", "Board name:", text=b["name"])
        if ok and name.strip():
            context.store.update_board(b["id"], name=name.strip())
            self.scene.refresh_item(it.id)
            self.main.refresh_sidebar()

    def edit_evidence(self, it, version_id=None):
        self.main.open_editor_item(it.id, version_id)

    def quick_look(self, it):
        self.show_history(it, quick=True)

    def show_history(self, it, quick=False, version_id=None):
        from ..widgets.versions import VersionsDialog
        d = VersionsDialog(it.id, version_id, self, quick=quick)
        d.edit_version.connect(lambda iid, vid: self.main.open_editor_item(iid, vid))
        d.restored.connect(lambda iid: (self.scene.refresh_item(iid), self.main.toast("Restored as a new version"),
                                        self.inspector.refresh()))
        d.exec()

    def open_file(self, it):
        p = Path(it.data.get("path") or "")
        if p.exists():
            evidence.open_path(p)
            return
        snap = evidence.snapshot_copy(it.id)
        if snap:
            self.main.toast("Original is missing — opened the stored snapshot")
            evidence.open_path(snap)
        else:
            self.main.toast("That file can't be found")

    def view_file(self, it):
        from ..widgets.fileviewer import FileViewer
        FileViewer(it.id, None, self).exec()

    def reveal_file(self, it):
        if not evidence.reveal(it.data.get("path") or ""):
            snap = evidence.snapshot_copy(it.id)
            if snap:
                evidence.reveal(snap)

    def update_snapshot(self, it):
        r = evidence.update_file_snapshot(it.id)
        if r == "updated":
            self.scene.refresh_item(it.id)
            self.main.toast("File changed — stacked a new snapshot version")
        elif r == "same":
            self.main.toast("File is unchanged since the last snapshot")
        else:
            self.main.toast("Original file not found")
        self.inspector.refresh()

    def focus_item(self, item_id, zoom=True):
        it = self.scene.items_by_id.get(item_id) if self.scene else None
        if it is None:
            return
        self.scene.clearSelection()
        it.setSelected(True)
        if zoom and self.view.zoom() < 0.7:
            self.view.set_zoom(0.9)
        self.view.centerOn(it.scene_rect().center())
        self.pulse(it)

    def pulse(self, it):
        """Brief glow around an item so the eye can find it."""
        r = it.scene_rect().adjusted(-12, -12, 12, 12)
        ring = QGraphicsRectItem(r)
        ring.setZValue(2_000_000)
        ring.setBrush(Qt.NoBrush)
        self.scene.addItem(ring)
        anim = QVariantAnimation(self)
        anim.setStartValue(0.0)
        anim.setEndValue(1.0)
        anim.setDuration(900)

        def step(v):
            c = QColor(theme.ACCENT)
            c.setAlphaF(max(0.0, 1.0 - v))
            pen = QPen(c, 4 + 10 * v)
            pen.setCosmetic(True)
            ring.setPen(pen)
            g = 10 * v
            ring.setRect(r.adjusted(-g, -g, g, g))
        anim.valueChanged.connect(step)
        anim.finished.connect(lambda: ring.scene() and ring.scene().removeItem(ring))
        anim.start(QVariantAnimation.DeleteWhenStopped)

    def delete_selected(self):
        sc = self.scene
        items = sc.selected_board_items()
        strings = sc.selected_strings()
        if self.is_map():
            kept = [i for i in items if isinstance(i, BoardLinkCard)]
            if kept:
                items = [i for i in items if not isinstance(i, BoardLinkCard)]
                self.main.toast("Every board stays on its project map — delete boards from the sidebar")
        if not items and not strings:
            return
        sc.delete(items, strings)
        n = len(items) + len(strings)
        self.main.toast(f"Deleted {n} item{'s' if n != 1 else ''} · Ctrl+Z to undo")

    def connect_selected(self):
        items = [i for i in self.scene.selected_board_items() if not isinstance(i, LineItem)]
        if len(items) == 2:
            s = self.scene.connect_items(items[0], items[1])
            if s is not None:
                self.scene.clearSelection()
                s.setSelected(True)
                self.edit_label(s)

    def align(self, how):
        sc = self.scene
        items = [i for i in sc.selected_board_items()]
        if len(items) < 2:
            return
        boxes = {i: i.scene_rect() for i in items}
        ref = QRectF(boxes[items[0]])
        for b in boxes.values():
            ref = ref.united(b)
        before = [("item", dict(i.row)) for i in items]
        if how in ("dist_h", "dist_v"):
            horiz = how == "dist_h"
            order = sorted(items, key=lambda i: boxes[i].left() if horiz else boxes[i].top())
            total = sum(boxes[i].width() if horiz else boxes[i].height() for i in order)
            gap = ((ref.width() if horiz else ref.height()) - total) / max(1, len(order) - 1)
            cur = ref.left() if horiz else ref.top()
            for it in order:
                b = boxes[it]
                it.moveBy(cur - b.left(), 0) if horiz else it.moveBy(0, cur - b.top())
                cur += (b.width() if horiz else b.height()) + gap
        else:
            for it, b in boxes.items():
                dx = {"left": ref.left() - b.left(), "hcenter": ref.center().x() - b.center().x(),
                      "right": ref.right() - b.right()}.get(how, 0)
                dy = {"top": ref.top() - b.top(), "vcenter": ref.center().y() - b.center().y(),
                      "bottom": ref.bottom() - b.bottom()}.get(how, 0)
                it.moveBy(dx, dy)
        sc.commit(before, [("item", i.current_row()) for i in items], "Align")

    def tidy_grid(self):
        sc = self.scene
        items = [i for i in sc.selected_board_items() if not isinstance(i, (FrameItem, LineItem))]
        if len(items) < 2:
            return
        items.sort(key=lambda i: (round(i.y() / 200), i.x()))
        cols = int(math.ceil(math.sqrt(len(items))))
        x0 = min(i.x() for i in items)
        y0 = min(i.y() for i in items)
        gap = 48
        colw = [0.0] * cols
        rows_h = {}
        for n, it in enumerate(items):
            colw[n % cols] = max(colw[n % cols], it.w)
            rows_h[n // cols] = max(rows_h.get(n // cols, 0), it.h)
        before = [("item", dict(i.row)) for i in items]
        y = y0
        for r in range(len(rows_h)):
            x = x0
            for c in range(cols):
                n = r * cols + c
                if n >= len(items):
                    break
                items[n].setPos(x, y)
                x += colw[c] + gap
            y += rows_h[r] + gap
        sc.commit(before, [("item", i.current_row()) for i in items], "Tidy")

    def wrap_in_frame(self):
        items = self.scene.selected_board_items()
        if not items:
            return
        r = QRectF()
        for it in items:
            r = it.scene_rect() if r.isNull() else r.united(it.scene_rect())
        r = r.adjusted(-40, -80, 40, 40)
        row = context.store.new_item(self.board["id"], "frame", r.x(), r.y(), r.width(), r.height(),
                                     {"title": "New frame", "color": "#9aa0ad"})
        it = self.scene.add_rows([row], label="Add frame")[0]
        if it is not None:
            self.view.setFocus()
            it.begin_edit()

    # ------------------------------------------------------------------ string labels
    def edit_label(self, s):
        if s.scene() is not self.scene:
            return
        self._label_target = s
        mid = s.path.pointAtPercent(0.5)
        vp = self.view.mapFromScene(mid)
        pt = self.view.viewport().mapTo(self.canvas, vp)
        ed = self.label_editor
        ed.setText(s.row.get("label") or "")
        ed.move(int(pt.x() - ed.width() / 2), int(pt.y() - ed.height() / 2))
        ed.show()
        ed.raise_()
        ed.setFocus()
        ed.selectAll()

    def _commit_label(self):
        s = self._label_target
        self._label_target = None
        self.label_editor.hide()
        if s is not None and s.scene() is self.scene:
            t = self.label_editor.text().strip()
            if t != (s.row.get("label") or ""):
                self.scene.update_strings([s], label=t)
        self.view.setFocus()
        self.inspector.refresh()

    def _cancel_label(self):
        self._label_target = None
        self.label_editor.hide()
        self.view.setFocus()

    # ------------------------------------------------------------------ board options
    def set_background(self, key):
        self.view.background = key
        self.view.resetCachedContent()
        self.view.viewport().update()
        context.store.update_board(self.board["id"], touch=False, background=key)
        self.board["background"] = key

    def set_strings_on_top(self, on):
        self.scene.set_strings_on_top(on)
        data = dict(self.board.get("data") or {}, strings_on_top=on)
        context.store.update_board(self.board["id"], touch=False, data=data)
        self.board["data"] = data

    def set_string_color(self, c):
        self.scene.string_color = c
        data = dict(self.board.get("data") or {}, string_color=c)
        context.store.update_board(self.board["id"], touch=False, data=data)
        self.board["data"] = data
        sel = self.scene.selected_strings()
        if sel:
            self.scene.update_strings(sel, color=c)

    def show_side(self, which):
        """Right-hand side: 'details' (inspector), 'coinv' (Co-Investigator) or None (hidden)."""
        self.inspector.setVisible(which == "details")
        self.coinv.setVisible(which == "coinv")
        self.insp_btn.setChecked(which == "details")
        self.coinv_btn.setChecked(which == "coinv")
        if which == "coinv":
            self.coinv.refresh_model()
            self.coinv.input.setFocus()

    def ask_coinv(self, question=None):
        self.show_side("coinv")
        if question:
            self.coinv.ask(question)

    def _fill_more(self):
        m = self.more_menu
        m.clear()
        m.addAction(theme.icon("coinv", 16, theme.COINV), "Ask the Co-Investigator…", self.ask_coinv)
        m.addAction(theme.icon("copy", 16), "Copy case file (for any AI)", self.coinv.copy_case)
        m.addAction(theme.icon("download", 16), "Save case file…", self.coinv.save_case)
        m.addSeparator()
        if not self.is_map():
            m.addAction(theme.icon("edit", 16), "Rename board",
                        lambda: (self.name_edit.setFocus(), self.name_edit.selectAll()))
        bg = m.addMenu(theme.icon("palette", 16), "Background")
        for key, name in BACKGROUNDS:
            a = bg.addAction(name, lambda k=key: self.set_background(k))
            a.setCheckable(True)
            a.setChecked(self.view.background == key)
        a = m.addAction("Draw strings over cards", lambda: self.set_strings_on_top(not self.scene.strings_on_top))
        a.setCheckable(True)
        a.setChecked(self.scene.strings_on_top)
        m.addSeparator()
        m.addAction(theme.icon("timeline", 16), "Project timeline" if self.is_map() else "Board timeline",
                    self._open_timeline)
        m.addAction(theme.icon("fit", 16), "Fit everything", self.fit)
        if not self.is_map():
            m.addAction(theme.icon("board", 16), "Project map",
                        lambda: self.main.open_project_map(self.board["project_id"]))
            m.addSeparator()
            m.addAction(theme.icon("delete", 16), "Delete board…", lambda: self.main.delete_board(self.board["id"]))

    def export_png(self):
        from ..export import render_board_image
        path, _ = QFileDialog.getSaveFileName(self, "Export board image", f"{self.board['name']}.png",
                                              "PNG image (*.png)")
        if path:
            img = render_board_image(self.scene, self.view.background, 4000)
            img.save(path)
            self.main.toast("Board image exported")

    # ------------------------------------------------------------------ context menus
    def context_menu(self, gpos, spos):
        sc = self.scene
        if sc is None:
            return
        target = None
        for it in sc.items(spos):
            if isinstance(it, (BoardItem, StringItem)):
                target = it
                break
        if target is not None and not target.isSelected():
            sc.clearSelection()
            target.setSelected(True)
        m = QMenu(self)
        sel = sc.selected_board_items()
        strings = sc.selected_strings()
        if target is None:
            m.addAction(theme.icon("evidence", 16), "Add evidence here", lambda: self.new_evidence(spos)) \
                .setShortcut("E")
            m.addAction(theme.icon("tool_text", 16), "Add text", lambda: self._create("text", spos, spos))
            m.addAction(theme.icon("sticky", 16), "Add sticky note", lambda: self._create("sticky", spos, spos))
            sh = m.addMenu(theme.icon("shape_round", 16), "Add shape")
            for k, n in geometry.SHAPES:
                sh.addAction(theme.icon("shape_" + k, 16), n, lambda kk=k: self._create("shape:" + kk, spos, spos))
            m.addAction(theme.icon("tool_frame", 16), "Add frame", lambda: self._create("frame", spos, spos))
            m.addAction(theme.icon("attach", 16), "Attach files…", self._attach_at(spos))
            self._link_menu(m, spos)
            m.addSeparator()
            m.addAction(theme.icon("paste", 16), "Paste", lambda: self.paste(spos))
            m.addAction(theme.icon("selectall", 16), "Select all",
                        lambda: [i.setSelected(True) for i in list(sc.items_by_id.values())])
            m.addAction(theme.icon("fit", 16), "Fit everything", self.fit)
            bg = m.addMenu(theme.icon("palette", 16), "Background")
            for key, name in BACKGROUNDS:
                a = bg.addAction(name, lambda k=key: (self.set_background(k), self.inspector.refresh()))
                a.setCheckable(True)
                a.setChecked(self.view.background == key)
        elif isinstance(target, StringItem):
            ss = strings or [target]
            m.addAction(theme.icon("edit", 16), "Edit label", lambda: self.edit_label(target))
            col = m.addMenu(theme.icon("palette", 16), "Colour")
            for n, c in theme.STRING_COLORS:
                col.addAction(theme.color_dot_icon(c), n, lambda cc=c: sc.update_strings(ss, color=cc))
            stl = m.addMenu("Style")
            stl.addAction("Solid", lambda: sc.update_strings(ss, style="solid"))
            stl.addAction("Dashed (tentative)", lambda: sc.update_strings(ss, style="dashed"))
            d = m.addMenu("Direction")
            for txt, key in (("No arrow", "none"), ("Arrow →", "fwd"), ("Arrow ←", "back"), ("Both ↔", "both")):
                d.addAction(txt, lambda kk=key: sc.update_strings(ss, dir=kk))
            m.addSeparator()
            m.addAction(theme.icon("delete", 16), "Delete string", lambda: sc.delete(strings=ss))
        else:
            it = target
            if isinstance(it, EvidenceCard) and len(sel) == 1:
                m.addAction(theme.icon("edit", 16), "Edit (stack a new version)", lambda: self.edit_evidence(it))
                m.addAction(theme.icon("view", 16), "Quick look", lambda: self.quick_look(it))
                m.addAction(theme.icon("history", 16), f"Version history ({int(it.row.get('vnum') or 1)})",
                            lambda: self.show_history(it))
                col = m.addMenu(theme.icon("palette", 16), "Card colour")
                for n, c in theme.CARD_COLORS:
                    col.addAction(theme.color_dot_icon(c), n,
                                  lambda cc=c: sc.update_items([it], lambda r: r["data"].__setitem__("color", cc)))
                m.addAction(theme.icon("download", 16), "Export slide as PNG…", lambda: self._export_card(it))
                m.addSeparator()
            elif isinstance(it, FileCard) and len(sel) == 1:
                m.addAction(theme.icon("openfile", 16), "Open", lambda: self.open_file(it))
                m.addAction(theme.icon("view", 16), "View contents", lambda: self.view_file(it))
                m.addAction(theme.icon("folderopen", 16), "Show in folder", lambda: self.reveal_file(it))
                m.addAction(theme.icon("refresh", 16), "Update snapshot from original", lambda: self.update_snapshot(it))
                m.addAction(theme.icon("history", 16), "Snapshot history", lambda: self.show_history(it))
                m.addSeparator()
            elif isinstance(it, TextHolder) and len(sel) == 1:
                m.addAction(theme.icon("edit", 16), "Edit text", lambda: (self.view.setFocus(), it.begin_edit()))
                m.addSeparator()
            elif isinstance(it, BoardLinkCard) and len(sel) == 1:
                m.addAction(theme.icon("newwindow", 16), "Open board", lambda: self.open_linked(it))
                m.addAction(theme.icon("timeline", 16), "Board timeline",
                            lambda: self.main.open_timeline(board_id=it.linked_id()))
                m.addAction(theme.icon("edit", 16), "Rename board…", lambda: self.rename_linked(it))
                m.addSeparator()
            linkable = [i for i in sel if not isinstance(i, LineItem)]
            if len(linkable) == 2:
                m.addAction(theme.icon("tool_string", 16), "Tie a string between these", self.connect_selected)
            m.addAction(theme.icon("copy", 16), "Duplicate", self.duplicate).setShortcut("Ctrl+D")
            m.addAction(theme.icon("copy", 16), "Copy", self.copy).setShortcut("Ctrl+C")
            m.addAction(theme.icon("cut", 16), "Cut", lambda: self.copy(cut=True)).setShortcut("Ctrl+X")
            m.addAction(theme.icon("front", 16), "Bring to front", lambda: sc.reorder(sel, "front"))
            m.addAction(theme.icon("back", 16), "Send to back", lambda: sc.reorder(sel, "back"))
            if len(sel) > 1:
                m.addAction(theme.icon("tool_frame", 16), "Wrap in a frame", self.wrap_in_frame)
                m.addAction(theme.icon("grid", 16), "Tidy into a grid", self.tidy_grid)
            mv = m.addMenu(theme.icon("forward", 16), "Move to board")
            if self.is_map() and any(isinstance(i, BoardLinkCard) for i in sel):
                mv.setEnabled(False)
            for p in context.store.projects():
                for b in context.store.boards(p["id"]):
                    if b["id"] == self.board["id"]:
                        continue
                    mv.addAction(f"{p['name']} › {b['name']}", lambda bid=b["id"]: self._move_to(sel, bid))
            if mv.isEmpty():
                mv.setEnabled(False)
            m.addSeparator()
            if not (self.is_map() and all(isinstance(i, BoardLinkCard) for i in sel)):
                m.addAction(theme.icon("delete", 16), "Delete", self.delete_selected).setShortcut("Del")
        m.exec(gpos)

    def _attach_at(self, pos):
        def go():
            paths, _ = QFileDialog.getOpenFileNames(self, "Attach files, scripts or programs", "", "All files (*)")
            if paths:
                self.drop_paths(paths, pos, force_files=True)
        return go

    def _move_to(self, items, board_id):
        b = context.store.board(board_id)
        if QMessageBox.question(self, "Move items", f"Move {len(items)} item(s) to “{b['name']}”? Strings to items "
                                "that stay here will be cut.") != QMessageBox.Yes:
            return
        self.scene.move_items_to(items, board_id)
        self.main.refresh_sidebar()
        self.main.toast(f"Moved to {b['name']}")

    def _export_card(self, it):
        v = context.store.current_version(it.id)
        if not v:
            return
        path, _ = QFileDialog.getSaveFileName(self, "Export slide", f"{v['title'] or 'evidence'}.png",
                                              "PNG image (*.png)")
        if path:
            from ..slide.scene import SlideScene
            s = SlideScene(v["doc"])
            s.render_image(2560).save(path)
            s.deleteLater()
            self.main.toast("Slide exported")
