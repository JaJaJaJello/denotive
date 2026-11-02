"""Right-hand panel on the board: details and styling for whatever is selected."""
from pathlib import Path

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (QCheckBox, QComboBox, QFrame, QGridLayout, QHBoxLayout, QLabel, QLineEdit,
                               QListWidget, QListWidgetItem, QScrollArea, QSpinBox, QVBoxLayout, QWidget)

from .. import context, geometry, theme
from ..widgets.common import (ClickLabel, ColorButton, SwatchRow, clear_layout, hsep, human_size, label, nice_time,
                              push_button, section_label, tool_button)
from .items import BoardLinkCard, EvidenceCard, FileCard, FrameItem, LineItem, TextHolder
from .view import BACKGROUNDS

STICKY_COLORS = [("Yellow", "#fff09a"), ("Orange", "#ffcf8a"), ("Pink", "#ffb3c7"), ("Lilac", "#d9c6ff"),
                 ("Blue", "#a8d4ff"), ("Mint", "#b8f0cf"), ("White", "#f6f6f4"), ("Slate", "#3a3f4a")]
SHAPE_FILLS = [("Blue", "#5b8cff"), ("Red", "#e5484d"), ("Orange", "#f76b15"), ("Amber", "#f5b544"),
               ("Green", "#2fb36d"), ("Teal", "#12a594"), ("Purple", "#8e4ec6"), ("Graphite", "#2c313a"),
               ("White", "#f4f4f2")]
TEXT_COLORS = [("Light", "#eceef2"), ("Dark", "#1d1f26"), ("Red", "#ff6b70"), ("Amber", "#f5b544"),
               ("Green", "#3dd68c"), ("Blue", "#7aa2ff"), ("Purple", "#c29bff")]


class BoardInspector(QScrollArea):
    def __init__(self, page):
        super().__init__()
        self.page = page
        self.setObjectName("Inspector")
        self.setWidgetResizable(True)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setFrameShape(QFrame.NoFrame)
        self.setFixedWidth(290)
        body = QWidget()
        body.setObjectName("Inspector")
        self.v = QVBoxLayout(body)
        self.v.setContentsMargins(16, 14, 16, 16)
        self.v.setSpacing(10)
        self.setWidget(body)
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(40)
        self._timer.timeout.connect(self._rebuild)

    @property
    def scene(self):
        return self.page.scene

    def refresh(self):
        self._timer.start()

    # ------------------------------------------------------------------ build
    def _rebuild(self):
        clear_layout(self.v)
        sc = self.scene
        if sc is None:
            self.v.addStretch(1)
            return
        items = sc.selected_board_items()
        strings = sc.selected_strings()
        if strings and not items:
            self._strings(strings)
        elif len(items) == 1:
            it = items[0]
            if isinstance(it, EvidenceCard):
                self._evidence(it)
            elif isinstance(it, FileCard):
                self._file(it)
            elif isinstance(it, LineItem):
                self._line([it])
            elif isinstance(it, BoardLinkCard):
                self._boardlink(it)
            else:
                self._text_items([it])
        elif items:
            self._multi(items)
        else:
            self._board()
        self.v.addStretch(1)

    def _add(self, w):
        self.v.addWidget(w)
        return w

    def _row(self, *ws):
        h = QHBoxLayout()
        h.setSpacing(6)
        for w in ws:
            h.addWidget(label(w, "Muted") if isinstance(w, str) else w)
        h.addStretch(1)
        self.v.addLayout(h)
        return h

    # ------------------------------------------------------------------ board
    def _board(self):
        st = context.store
        b = st.board(self.scene.bid)
        p = st.project(b["project_id"]) if b else None
        n_ev = sum(1 for i in self.scene.items_by_id.values() if i.KIND == "evidence")
        n_f = sum(1 for i in self.scene.items_by_id.values() if i.KIND == "file")
        n_b = sum(1 for i in self.scene.items_by_id.values() if i.KIND == "boardlink")
        n_s = len(self.scene.strings_by_id)
        if st.is_map(b):
            self._add(section_label("Project map"))
            self._add(label(p["name"] if p else "", "Title", wrap=True))
            self._add(label(f"{n_b} boards · {n_s} strings", "Muted"))
            self._add(label("Every board in this project appears here as a case file. Drag between their red "
                            "pins to tie strings, label how they relate, and double-click a board to open it.",
                            "Muted", wrap=True))
            tl = push_button("Project timeline", "timeline")
            tl.clicked.connect(lambda: self.page.main.open_timeline(project_id=b["project_id"]))
            self._add(tl)
        else:
            self._add(section_label("Board"))
            self._add(label(b["name"] if b else "", "Title", wrap=True))
            self._add(label(f"in {p['name']}" if p else "", "Muted"))
            self._add(label(f"{n_ev} evidence · {n_f} files · {n_s} strings"
                            + (f" · {n_b} linked boards" if n_b else ""), "Muted", wrap=True))
            pm = push_button("Project map", "board")
            pm.setToolTip("See how this board connects to the rest of the project")
            pm.clicked.connect(lambda: self.page.main.open_project_map(b["project_id"]))
            self._add(pm)
        if b:
            self._add(label(f"Created {nice_time(b['created'])}", "Hint"))
        self._add(hsep())
        self._add(section_label("Background"))
        grid = QGridLayout()
        grid.setSpacing(6)
        for i, (key, name) in enumerate(BACKGROUNDS):
            btn = push_button(name, name="Chip")
            btn.setCheckable(True)
            btn.setChecked(self.page.view.background == key)
            btn.clicked.connect(lambda _=False, k=key: (self.page.set_background(k), self.refresh()))
            grid.addWidget(btn, i // 2, i % 2)
        self.v.addLayout(grid)
        self._add(hsep())
        self._add(section_label("Strings"))
        cb = QCheckBox("Draw strings over cards")
        cb.setChecked(self.scene.strings_on_top)
        cb.toggled.connect(self.page.set_strings_on_top)
        self._add(cb)
        self._add(label("New string colour", "Muted"))
        sw = SwatchRow(theme.STRING_COLORS, 20)
        sw.set_current(self.scene.string_color)
        sw.picked.connect(self.page.set_string_color)
        self._add(sw)
        self._add(hsep())
        self._add(section_label("Quick start"))
        self._add(label("• Right-click the board → Add evidence\n• Drag from a red pin to another card to tie a "
                        "string\n• Drop files, scripts or screenshots straight onto the board\n• Ctrl+V pastes "
                        "screenshots as new evidence\n• Right-drag or Space-drag to pan, wheel to zoom", "Muted",
                        wrap=True))

    # ------------------------------------------------------------------ evidence
    def _evidence(self, it):
        st = context.store
        self._add(section_label("Evidence"))
        thumb = ClickLabel()
        thumb.setCursor(Qt.PointingHandCursor)
        thumb.setToolTip("Quick look")
        th = it.thumb()
        pm = context.images.get_sync(th, 1024) if th else None
        if pm is not None and not pm.isNull():
            thumb.setPixmap(pm.scaledToWidth(258, Qt.SmoothTransformation))
        thumb.clicked.connect(lambda: self.page.quick_look(it))
        self._add(thumb)
        t = label(it.row.get("title") or "Untitled", "Title", wrap=True)
        self._add(t)
        vn = int(it.row.get("vnum") or 1)
        self._add(label(f"v{vn} · updated {nice_time(it.row.get('updated'))}", "Muted"))
        self._add(label(f"Created {nice_time(it.row.get('created'))}", "Hint"))
        h = QHBoxLayout()
        e = push_button("Edit", "edit", primary=True)
        e.setToolTip("Edit — saving stacks a new version on top")
        e.clicked.connect(lambda: self.page.edit_evidence(it))
        hb = push_button("History", "history")
        hb.clicked.connect(lambda: self.page.show_history(it))
        h.addWidget(e)
        h.addWidget(hb)
        self.v.addLayout(h)
        self._linked(it)
        self._add(hsep())
        self._add(section_label("Card"))
        sw = SwatchRow(theme.CARD_COLORS, 20)
        sw.set_current(it.data.get("color") or "#f6f4ee")
        sw.picked.connect(lambda c: self.scene.update_items([it], lambda r: r["data"].__setitem__("color", c)))
        self._add(sw)
        self._add(label("Pin colour", "Muted"))
        pins = SwatchRow([("Red", "#e23b3b"), ("Amber", "#f5a524"), ("Green", "#2fb36d"), ("Blue", "#2f7cf6"),
                          ("Purple", "#8e4ec6"), ("Black", "#2b2d33"), ("White", "#f2f2f2")], 18)
        pins.set_current(it.data.get("pin") or "#e23b3b")
        pins.picked.connect(lambda c: self.scene.update_items([it], lambda r: r["data"].__setitem__("pin", c)))
        self._add(pins)
        self._connections(it)
        v = st.current_version(it.id)
        if v and v.get("text"):
            self._add(hsep())
            self._add(section_label("Text"))
            self._add(label(v["text"][:600] + ("…" if len(v["text"]) > 600 else ""), "Muted", wrap=True))

    def _linked(self, it):
        """Lists the other places this evidence/file is pinned, with a way to make this one independent."""
        places = context.store.linked_places(it.id)
        if not places:
            return
        self._add(hsep())
        self._add(section_label(f"Linked copies · {len(places) + 1} places"))
        self._add(label("Editing any of them edits all of them.", "Hint", wrap=True))
        lst = QListWidget()
        lst.setFixedHeight(min(150, 34 * len(places) + 8))
        lst.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        lst.setTextElideMode(Qt.ElideRight)
        here = self.page.board["id"]
        for pl in places:
            where = "This board" if pl["board_id"] == here else f"{pl['project']} › {pl['board']}"
            li = QListWidgetItem(theme.icon("link", 16, theme.SELECT), where)
            li.setData(Qt.UserRole, (pl["board_id"], pl["id"]))
            lst.addItem(li)

        def go(li):
            bid, iid = li.data(Qt.UserRole)
            if bid == here:
                self.page.focus_item(iid)
            else:
                self.page.main.open_board(bid, focus_item=iid)
        lst.itemClicked.connect(go)
        self._add(lst)
        un = push_button("Unlink this one", "link")
        un.setToolTip("Give this one its own copy of the history; from now on its edits stay separate")
        un.clicked.connect(lambda: self.page.unlink(it))
        self._add(un)

    def _connections(self, it):
        if not it.strings:
            return
        self._add(hsep())
        self._add(section_label(f"Strings ({len(it.strings)})"))
        lst = QListWidget()
        lst.setFixedHeight(min(200, 34 * len(it.strings) + 8))
        lst.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        lst.setTextElideMode(Qt.ElideRight)
        for s in it.strings:
            other = s.dst if s.src is it else s.src
            arrow = "→" if s.src is it else "←"
            name = other.search_text() or other.data.get("title") or other.KIND
            txt = f"{arrow} {name[:34]}" + (f"   “{s.row.get('label')}”" if s.row.get("label") else "")
            li = QListWidgetItem(theme.color_dot_icon(s.color().name()), txt)
            li.setData(Qt.UserRole, other.id)
            lst.addItem(li)
        lst.itemClicked.connect(lambda li: self.page.focus_item(li.data(Qt.UserRole)))
        self._add(lst)

    # ------------------------------------------------------------------ board links
    def _boardlink(self, it):
        info = it.info or {}
        b = info.get("board") or {}
        p = info.get("project") or {}
        self._add(section_label("Linked board"))
        self._add(label(b.get("name") or "Missing board", "Title", wrap=True))
        self._add(label(f"in {p.get('name', '')}", "Muted"))
        if info.get("gone"):
            self._add(label("This board was deleted. Restore it from the Trash to bring it back.", "Muted", wrap=True))
        else:
            self._add(label(f"{info.get('evidence', 0)} evidence · {info.get('files', 0)} files · "
                            f"{info.get('strings', 0)} strings", "Muted"))
            self._add(label(f"Updated {nice_time(info.get('last'))}", "Hint"))
            h = QHBoxLayout()
            o = push_button("Open board", "newwindow", primary=True)
            o.clicked.connect(lambda: self.page.open_linked(it))
            t = push_button("Timeline", "timeline")
            t.clicked.connect(lambda: self.page.main.open_timeline(board_id=it.linked_id()))
            h.addWidget(o)
            h.addWidget(t)
            self.v.addLayout(h)
        self._connections(it)

    # ------------------------------------------------------------------ files
    def _file(self, it):
        d = it.data
        vdoc = it.row.get("vdoc") or {}
        self._add(section_label("Attached file"))
        h = QHBoxLayout()
        ic = QLabel()
        ic.setPixmap(theme.icon_pixmap(theme.file_glyph(d.get("ext")), 28, theme.file_color(d.get("ext"))))
        h.addWidget(ic)
        h.addWidget(label(d.get("name") or "", "Title", wrap=True), 1)
        self.v.addLayout(h)
        p = label(d.get("path") or "", "Muted", wrap=True)
        p.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self._add(p)
        exists = Path(d.get("path") or "").exists()
        info = f"{human_size(vdoc.get('size'))} · modified {nice_time(vdoc.get('mtime'))}"
        self._add(label(info, "Muted"))
        vn = int(it.row.get("vnum") or 1)
        snap = "Snapshot stored" if vdoc.get("asset") else "Linked only (too large or a program)"
        self._add(label(f"{snap} · v{vn} · captured {nice_time(it.row.get('updated'))}", "Hint", wrap=True))
        if not exists:
            self._add(label("⚠ The original file has moved or been deleted." +
                            (" The snapshot is still available." if vdoc.get("asset") else ""), "Muted", wrap=True))
        g = QGridLayout()
        g.setSpacing(6)
        btns = [("Open", "openfile", lambda: self.page.open_file(it)),
                ("View", "view", lambda: self.page.view_file(it)),
                ("Show in folder", "folderopen", lambda: self.page.reveal_file(it)),
                ("Update snapshot", "refresh", lambda: self.page.update_snapshot(it)),
                ("History", "history", lambda: self.page.show_history(it))]
        for i, (t, ic_name, fn) in enumerate(btns):
            b = push_button(t, ic_name)
            b.clicked.connect(fn)
            g.addWidget(b, i // 2, i % 2)
        self.v.addLayout(g)
        self._linked(it)
        self._connections(it)

    # ------------------------------------------------------------------ text-ish items
    def _text_items(self, items):
        sc = self.scene
        first = items[0]
        kinds = {i.KIND for i in items}
        title = {"note": "Text", "sticky": "Sticky note", "shape": "Shape", "frame": "Frame"}.get(first.KIND, "Item")
        self._add(section_label(title if len(kinds) == 1 else f"{len(items)} items"))

        def upd(fn, label_="Style"):
            sc.update_items(items, fn, label_)

        if first.KIND == "frame":
            ed = QLineEdit(first.data.get("title", ""))
            ed.setPlaceholderText("Frame title")
            ed.editingFinished.connect(lambda: sc.commit_text(first, ed.text()) if ed.text() != first.text() else None)
            self._add(ed)
            self._add(label("Colour", "Muted"))
            sw = SwatchRow([(n, c) for n, c in theme.STRING_COLORS if n not in ("Black",)] + [("Grey", "#9aa0ad")], 20)
            sw.set_current(first.data.get("color") or "#9aa0ad")
            sw.picked.connect(lambda c: upd(lambda r: r["data"].__setitem__("color", c)))
            self._add(sw)
            self._add(label("Dragging a frame carries everything fully inside it.", "Hint", wrap=True))
            return
        # text style
        size = QSpinBox()
        size.setRange(8, 400)
        size.setSuffix(" px")
        size.setValue(int(first.data.get("size", 18)))
        size.setKeyboardTracking(False)

        def set_size(v):
            def fn(r):
                r["data"]["size"] = v
                it = sc.items_by_id.get(r["id"])
                if isinstance(it, TextHolder) and it.AUTO_HEIGHT:
                    old = it.data.get("size")
                    it.data["size"] = v
                    r["h"] = it._text_height(it.w)
                    it.data["size"] = old
            upd(fn)
        size.valueChanged.connect(set_size)

        def style(on, action, key):
            if first.editor is not None:  # typing: just the selected words
                first.editor.format(action)
            else:
                upd(lambda r: r["data"].__setitem__(key, on))

        def set_list(action):
            if first.editor is not None:
                first.editor.format(action)
                return

            def fn(r):
                lines = range((r["data"].get("text") or "").count("\n") + 1)
                whole = [[i, action] for i in lines]
                r["data"]["lists"] = [] if (r["data"].get("lists") or []) == whole else whole
            upd(fn)

        marks = []
        for icon, action, key, tip in (("bold", "b", "bold", "Bold"), ("italic", "i", "italic", "Italic"),
                                       ("underline", "u", "underline", "Underline"),
                                       ("strike", "s", "strike", "Strikethrough")):
            b = tool_button(icon, tip, True, 16)
            b.setFocusPolicy(Qt.NoFocus)  # a click must not end the typing
            b.setChecked(bool(first.data.get(key)))
            b.clicked.connect(lambda on, a=action, k=key: style(on, a, k))
            marks.append(b)
        for icon, action, tip in (("bullets", "bullet", "Bulleted list"), ("list", "number", "Numbered list")):
            b = tool_button(icon, tip, False, 16)
            b.setFocusPolicy(Qt.NoFocus)
            b.clicked.connect(lambda _=False, a=action: set_list(a))
            marks.append(b)
        center = tool_button("aligncenter", "Centre text", True, 16)
        center.setChecked(first.data.get("align") == "center" or first.KIND == "shape")
        center.setEnabled(first.KIND != "shape")
        center.clicked.connect(lambda on: upd(lambda r: r["data"].__setitem__("align", "center" if on else "left")))
        self._row("Size", size, center)
        self._row(*marks)
        self._add(label("Text colour", "Muted"))
        tc = SwatchRow(TEXT_COLORS, 18)
        tc.set_current(first.data.get("tcolor") or first.text_color())
        tc.picked.connect(lambda c: upd(lambda r: r["data"].__setitem__("tcolor", c)))
        self._add(tc)
        font = QComboBox()
        font.addItems(["Clean", "Handwriting", "Marker", "Mono"])
        fams = {"Clean": theme.UI_FONT, "Handwriting": "Segoe Print", "Marker": "Ink Free", "Mono": theme.MONO_FONT}
        rev = {v: k for k, v in fams.items()}
        font.setCurrentText(rev.get(first.data.get("font") or theme.UI_FONT, "Clean"))
        font.currentTextChanged.connect(lambda t: upd(lambda r: r["data"].__setitem__("font", fams[t])))
        self._row("Font", font)
        if first.KIND == "sticky":
            self._add(label("Paper", "Muted"))
            sw = SwatchRow(STICKY_COLORS, 20)
            sw.set_current(first.data.get("fill") or "#fff09a")
            sw.picked.connect(lambda c: upd(lambda r: r["data"].__setitem__("fill", c)))
            self._add(sw)
        if first.KIND == "note":
            self._add(label("Background", "Muted"))
            bg = ColorButton(first.data.get("fill"), allow_none=True, tip="Background")
            bg.colorChanged.connect(lambda c: upd(lambda r: r["data"].__setitem__("fill", c)))
            self._row(bg)
        if first.KIND == "shape":
            kind = QComboBox()
            for k, n in geometry.SHAPES:
                kind.addItem(theme.icon("shape_" + k, 16), n, k)
            kind.setCurrentIndex(max(0, kind.findData(first.data.get("shape", "round"))))
            kind.currentIndexChanged.connect(
                lambda _: upd(lambda r: r["data"].__setitem__("shape", kind.currentData())))
            self._row("Shape", kind)
            self._add(label("Fill", "Muted"))
            sw = SwatchRow(SHAPE_FILLS, 20)
            sw.set_current(first.data.get("fill") or "#5b8cff")
            sw.picked.connect(lambda c: upd(lambda r: r["data"].__setitem__("fill", c)))
            self._add(sw)
            stroke = ColorButton(first.data.get("stroke"), allow_none=True, tip="Border")
            sw_spin = QSpinBox()
            sw_spin.setRange(0, 30)
            sw_spin.setValue(int(first.data.get("sw", 0) or 0))
            sw_spin.setSuffix(" px")

            def set_stroke(c):
                def fn(r):
                    r["data"]["stroke"] = c
                    if c and not r["data"].get("sw"):
                        r["data"]["sw"] = 3
                upd(fn)
            stroke.colorChanged.connect(set_stroke)
            sw_spin.valueChanged.connect(lambda v: upd(lambda r: r["data"].__setitem__("sw", v)))
            nofill = QCheckBox("No fill")
            nofill.setChecked(not first.data.get("fill"))
            nofill.toggled.connect(lambda on: upd(lambda r: r["data"].__setitem__("fill", None if on else "#5b8cff")))
            self._row("Border", stroke, sw_spin, nofill)

    # ------------------------------------------------------------------ lines
    def _line(self, items):
        sc = self.scene
        it = items[0]
        self._add(section_label("Line"))
        sw = SwatchRow(TEXT_COLORS, 18)
        sw.set_current(it.data.get("color") or "#e9eaee")
        sw.picked.connect(lambda c: sc.update_items(items, lambda r: r["data"].__setitem__("color", c)))
        self._add(sw)
        w = QSpinBox()
        w.setRange(1, 40)
        w.setValue(int(it.data.get("sw", 3)))
        w.setSuffix(" px")
        w.valueChanged.connect(lambda v: sc.update_items(items, lambda r: r["data"].__setitem__("sw", v)))
        dash = QComboBox()
        dash.addItems(["solid", "dash", "dot"])
        dash.setCurrentText(it.data.get("dash", "solid"))
        dash.currentTextChanged.connect(lambda t: sc.update_items(items, lambda r: r["data"].__setitem__("dash", t)))
        self._row("Width", w, dash)
        a1 = QCheckBox("Start arrow")
        a1.setChecked(bool(it.data.get("a1")))
        a1.toggled.connect(lambda on: sc.update_items(items, lambda r: r["data"].__setitem__("a1", on)))
        a2 = QCheckBox("End arrow")
        a2.setChecked(bool(it.data.get("a2", True)))
        a2.toggled.connect(lambda on: sc.update_items(items, lambda r: r["data"].__setitem__("a2", on)))
        self._row(a1, a2)

    # ------------------------------------------------------------------ strings
    def _strings(self, strings):
        sc = self.scene
        s = strings[0]
        self._add(section_label("String" if len(strings) == 1 else f"{len(strings)} strings"))
        if len(strings) == 1:
            a = s.src.search_text() or s.src.data.get("title") or s.src.KIND
            b = s.dst.search_text() or s.dst.data.get("title") or s.dst.KIND
            self._add(label(f"{a[:40]}\n↕\n{b[:40]}", "Muted", wrap=True))
            ed = QLineEdit(s.row.get("label") or "")
            ed.setPlaceholderText("Label (e.g. “causes”, “same peak?”)")
            ed.editingFinished.connect(
                lambda: sc.update_strings([s], label=ed.text()) if ed.text() != (s.row.get("label") or "") else None)
            self._add(ed)
        self._add(label("Colour", "Muted"))
        sw = SwatchRow(theme.STRING_COLORS, 20)
        sw.set_current(s.color().name())
        sw.picked.connect(lambda c: sc.update_strings(strings, color=c))
        self._add(sw)
        style = QComboBox()
        style.addItem("Solid", "solid")
        style.addItem("Dashed (tentative)", "dashed")
        style.setCurrentIndex(0 if s.data.get("style", "solid") == "solid" else 1)
        style.currentIndexChanged.connect(lambda _: sc.update_strings(strings, style=style.currentData()))
        self._row("Style", style)
        direc = QComboBox()
        for txt, key in (("No arrow", "none"), ("Arrow →", "fwd"), ("Arrow ←", "back"), ("Both ↔", "both")):
            direc.addItem(txt, key)
        direc.setCurrentIndex(max(0, direc.findData(s.data.get("dir", "none"))))
        direc.currentIndexChanged.connect(lambda _: sc.update_strings(strings, dir=direc.currentData()))
        self._row("Direction", direc)
        thick = QSpinBox()
        thick.setRange(1, 12)
        thick.setValue(int(round(s.width())))
        thick.setSuffix(" px")
        thick.valueChanged.connect(lambda v: sc.update_strings(strings, width=float(v)))
        slack = QComboBox()
        for txt, val in (("Taut", 0.15), ("Natural", 1.0), ("Loose", 1.8)):
            slack.addItem(txt, val)
        cur = float(s.data.get("slack", 1.0))
        slack.setCurrentIndex(0 if cur < 0.5 else 2 if cur > 1.4 else 1)
        slack.currentIndexChanged.connect(lambda _: sc.update_strings(strings, slack=slack.currentData()))
        self._row("Width", thick, slack)
        d = push_button("Delete string" if len(strings) == 1 else "Delete strings", "delete", name="Danger")
        d.clicked.connect(lambda: sc.delete(strings=strings))
        self._add(d)

    # ------------------------------------------------------------------ multiple
    def _multi(self, items):
        self._add(section_label(f"{len(items)} selected"))
        kinds = {}
        for i in items:
            kinds[i.KIND] = kinds.get(i.KIND, 0) + 1
        self._add(label(", ".join(f"{n} {k}" for k, n in kinds.items()), "Muted"))
        g = QHBoxLayout()
        for name, how, tip in (("align_left", "left", "Align left"), ("align_hcenter", "hcenter", "Align centre"),
                               ("align_right", "right", "Align right"), ("align_top", "top", "Align top"),
                               ("align_vcenter", "vcenter", "Align middle"), ("align_bottom", "bottom", "Align bottom")):
            b = tool_button(name, tip, False, 18)
            b.clicked.connect(lambda _=False, h=how: self.page.align(h))
            g.addWidget(b)
        g.addStretch(1)
        self.v.addLayout(g)
        g2 = QHBoxLayout()
        for name, how, tip in (("dist_h", "dist_h", "Distribute horizontally"),
                               ("dist_v", "dist_v", "Distribute vertically")):
            b = tool_button(name, tip, False, 18)
            b.clicked.connect(lambda _=False, h=how: self.page.align(h))
            g2.addWidget(b)
        tidy = tool_button("grid", "Tidy into a grid", False, 18)
        tidy.clicked.connect(self.page.tidy_grid)
        g2.addWidget(tidy)
        g2.addStretch(1)
        self.v.addLayout(g2)
        linkable = [i for i in items if not isinstance(i, LineItem)]
        if len(linkable) == 2:
            c = push_button("Tie a string between them", "tool_string")
            c.clicked.connect(lambda: self.page.connect_selected())
            self._add(c)
        fr = push_button("Wrap in a frame", "tool_frame")
        fr.clicked.connect(self.page.wrap_in_frame)
        self._add(fr)
        d = push_button("Delete", "delete", name="Danger")
        d.clicked.connect(self.page.delete_selected)
        self._add(d)
        texty = [i for i in items if isinstance(i, TextHolder) and not isinstance(i, FrameItem)]
        if texty and len(texty) == len(items):
            self._add(hsep())
            self._text_items(texty)
