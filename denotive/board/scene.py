"""BoardScene: the infinite canvas for one board.

Every change goes through commit(before, after): lists of ("item" | "conn", row) records. `after` is written to
the store and mirrored into the scene; undo simply applies `before`. Rows are re-read from the store after each
write so version bookkeeping (titles, thumbnails) is never clobbered by an older snapshot.
"""
import time
from collections import defaultdict

from PySide6.QtCore import QRectF, Qt, QTimer, Signal
from PySide6.QtWidgets import QGraphicsScene

from .. import context
from ..store import new_id
from .items import KIND_CLASSES, BoardItem, FrameItem, LineItem, TextHolder
from .strings import STRING_Z_BEHIND, STRING_Z_TOP, StringItem, TempString

BOARD_MIME = "application/x-denotive-board-items"
BOARDREF_MIME = "application/x-denotive-board-ref"  # a board dragged out of the sidebar
FRAME_Z_BASE = -100000.0


class BoardScene(QGraphicsScene):
    changed = Signal()
    undo_changed = Signal()
    label_edit = Signal(object)
    open_item = Signal(object)

    def __init__(self, board):
        super().__init__()
        self.board = board
        self.bid = board["id"]
        self.items_by_id = {}
        self.strings_by_id = {}
        self.undo_stack, self.redo_stack = [], []
        self.tool = "select"
        self.strings_on_top = (board.get("data") or {}).get("strings_on_top", True)
        self.string_color = (board.get("data") or {}).get("string_color", "#d7263d")
        self._sdrag = None
        self._press_geo = None
        self._thumb_users = defaultdict(set)
        self._changed_timer = QTimer()
        self._changed_timer.setSingleShot(True)
        self._changed_timer.setInterval(80)
        self._changed_timer.timeout.connect(self.changed.emit)
        self.setSceneRect(QRectF(-250000, -250000, 500000, 500000))
        self.setItemIndexMethod(QGraphicsScene.BspTreeIndex)
        context.images.loaded.connect(self._thumb_loaded)
        self.load()

    def teardown(self):
        try:
            context.images.loaded.disconnect(self._thumb_loaded)
        except (RuntimeError, TypeError):
            pass
        self._changed_timer.stop()

    # ------------------------------------------------------------------ building
    def load(self):
        st = context.store
        for row in st.items(self.bid):
            self._add_item(row)
        for c in st.connections(self.bid):
            self._add_string(c)

    def _track_thumb(self, it):
        for th in it.thumbs():
            self._thumb_users[th].add(it.id)

    def _add_item(self, row):
        cls = KIND_CLASSES.get(row["kind"])
        if cls is None:
            return None
        it = cls(row)
        self.addItem(it)
        self.items_by_id[row["id"]] = it
        self._track_thumb(it)
        return it

    def _add_string(self, row):
        a, b = self.items_by_id.get(row["src"]), self.items_by_id.get(row["dst"])
        if a is None or b is None or a is b:
            return None
        s = StringItem(row, a, b, self.strings_on_top)
        self.addItem(s)
        self.strings_by_id[row["id"]] = s
        return s

    def _remove_item(self, it):
        for s in list(it.strings):
            self._remove_string(s)
        self.items_by_id.pop(it.id, None)
        if it.scene() is self:
            self.removeItem(it)

    def _remove_string(self, s):
        s.detach()
        self.strings_by_id.pop(s.id, None)
        if s.scene() is self:
            self.removeItem(s)

    def _thumb_loaded(self, h):
        for iid in self._thumb_users.get(h, ()):
            it = self.items_by_id.get(iid)
            if it is not None:
                it.update()

    def item_moved(self, it):
        self._changed_timer.start()

    def set_strings_on_top(self, on):
        self.strings_on_top = on
        for s in self.strings_by_id.values():
            s.setZValue(STRING_Z_TOP if on else STRING_Z_BEHIND)

    # ------------------------------------------------------------------ commands
    def _sync_item(self, row):
        if row is None:
            return
        it = self.items_by_id.get(row["id"])
        if row.get("deleted") or row["board_id"] != self.bid:
            if it is not None:
                self._remove_item(it)
            return
        if it is None:
            self._add_item(row)
        else:
            it.apply_row(row)
            self._track_thumb(it)

    def _sync_conn(self, row):
        if row is None:
            return
        s = self.strings_by_id.get(row["id"])
        if row.get("deleted"):
            if s is not None:
                self._remove_string(s)
            return
        if s is None:
            self._add_string(row)
        else:
            s.apply_row(row)

    def _apply(self, recs):
        st = context.store
        with st.tx():
            for kind, row in recs:
                if kind == "item":
                    st.put_item(row)
                else:
                    st.put_conn(row)
        for kind, row in recs:
            if kind == "item":
                self._sync_item(st.item(row["id"]))
        for kind, row in recs:
            if kind == "conn":
                self._sync_conn(st.conn(row["id"]))
        self.changed.emit()

    def commit(self, before, after, label=""):
        self._apply(after)
        self.undo_stack.append((before, after, label))
        if len(self.undo_stack) > 400:
            self.undo_stack.pop(0)
        self.redo_stack.clear()
        self.undo_changed.emit()

    def undo(self):
        if not self.undo_stack:
            return None
        before, after, label = self.undo_stack.pop()
        self._apply(before)
        self.redo_stack.append((before, after, label))
        self.undo_changed.emit()
        return label

    def redo(self):
        if not self.redo_stack:
            return None
        before, after, label = self.redo_stack.pop()
        self._apply(after)
        self.undo_stack.append((before, after, label))
        self.undo_changed.emit()
        return label

    # convenience builders -------------------------------------------------
    def add_rows(self, rows, conns=(), select=True, label="Add"):
        t = time.time()
        before = [("item", dict(r, deleted=t)) for r in rows] + [("conn", dict(c, deleted=t)) for c in conns]
        after = [("item", r) for r in rows] + [("conn", c) for c in conns]
        self.commit(before, after, label)
        if select:
            self.clearSelection()
            for r in rows:
                it = self.items_by_id.get(r["id"])
                if it is not None:
                    it.setSelected(True)
        return [self.items_by_id.get(r["id"]) for r in rows]

    def add_existing(self, item_id, select=True):
        """Registers an item that was already written to the store (e.g. by the evidence editor)."""
        row = context.store.item(item_id)
        if row is None:
            return None
        return self.add_rows([row], select=select, label="Add evidence")[0]

    def refresh_item(self, item_id):
        row = context.store.item(item_id)
        self._sync_item(row)
        self.changed.emit()
        return self.items_by_id.get(item_id)

    def delete(self, items=(), strings=()):
        t = time.time()
        before, after, conns = [], [], {}
        for it in items:
            before.append(("item", dict(it.row)))
            after.append(("item", dict(it.current_row(), deleted=t)))
            for s in it.strings:
                conns[s.id] = s
        for s in strings:
            conns[s.id] = s
        for s in conns.values():
            before.append(("conn", dict(s.row)))
            after.append(("conn", dict(s.row, deleted=t)))
        if before:
            self.commit(before, after, "Delete")

    def commit_geometry(self, items, befores):
        self.commit([("item", b) for b in befores], [("item", it.current_row()) for it in items], "Resize")

    def update_items(self, items, fn, label="Style"):
        """fn(row) mutates a copy of each item's row (usually its data dict)."""
        before, after = [], []
        for it in items:
            before.append(("item", dict(it.row, data=dict(it.data))))
            r = it.current_row()
            r["data"] = dict(r.get("data") or {})
            fn(r)
            after.append(("item", r))
        if after:
            self.commit(before, after, label)

    def update_strings(self, strings, label=None, **data):
        before, after = [], []
        for s in strings:
            before.append(("conn", dict(s.row, data=dict(s.data))))
            r = dict(s.row, data=dict(s.data, **data), updated=time.time())
            if label is not None:
                r["label"] = label
            after.append(("conn", r))
        if after:
            self.commit(before, after, "Edit string")

    def commit_text(self, item, text):
        key = "title" if item.KIND == "frame" else "text"
        old = dict(item.row, data=dict(item.data))
        if (old["data"].get(key) or "") == text:
            if item.KIND == "note" and getattr(item, "fresh", False) and not text.strip():
                self._discard_fresh(item)
            else:
                item.apply_row(item.row)
            return
        if item.KIND == "note" and getattr(item, "fresh", False) and not text.strip():
            self._discard_fresh(item)
            return
        new = item.current_row()
        new["data"] = dict(new["data"], **{key: text})
        new["updated"] = time.time()
        if isinstance(item, TextHolder) and item.AUTO_HEIGHT:
            new["h"] = item._text_height(item.w) if text else item.MIN_H
        item.fresh = False
        self.commit([("item", old)], [("item", new)], "Edit text")

    def _discard_fresh(self, item):
        """A brand-new text note left empty disappears without leaving an undo step."""
        t = time.time()
        if self.undo_stack and any(r[1]["id"] == item.id for r in self.undo_stack[-1][1]):
            self.undo_stack.pop()
        self._apply([("item", dict(item.current_row(), deleted=t))])
        self.undo_changed.emit()

    def reorder(self, items, how):
        items = [i for i in items if not isinstance(i, FrameItem)]
        if not items:
            return
        others = [i for i in self.items_by_id.values() if not isinstance(i, FrameItem)]
        zs = [i.zValue() for i in others] or [0]
        before = [("item", dict(i.row)) for i in items]
        after = []
        base = (max(zs) + 1) if how == "front" else (min(zs) - len(items) - 1)
        for n, it in enumerate(sorted(items, key=lambda i: i.zValue())):
            after.append(("item", dict(it.current_row(), z=base + n)))
        self.commit(before, after, "Arrange")

    def move_items_to(self, items, board_id):
        st = context.store
        ids = {it.id for it in items}
        t = time.time()
        with st.tx():
            for it in items:
                st.put_item(it.current_row())
                st.move_item_to_board(it.id, board_id)
                for s in list(it.strings):
                    other = s.row["dst"] if s.row["src"] == it.id else s.row["src"]
                    if other in ids:
                        st.put_conn(dict(s.row, board_id=board_id))
                    else:
                        st.put_conn(dict(s.row, deleted=t))
        for it in list(items):
            self._remove_item(it)
        self.undo_stack.clear()
        self.redo_stack.clear()
        self.undo_changed.emit()
        self.changed.emit()

    # ------------------------------------------------------------------ copy / paste
    def copy_payload(self, items):
        st = context.store
        ids = {it.id for it in items}
        out = {"items": [], "conns": []}
        for it in items:
            r = it.current_row()
            r.pop("vdoc", None)
            r.pop("thumb", None)
            entry = {"row": r}
            if it.KIND in ("evidence", "file"):
                v = st.current_version(it.id)
                if v:
                    entry["version"] = {k: v[k] for k in ("title", "doc", "text", "thumb", "note")}
            out["items"].append(entry)
        for s in self.strings_by_id.values():
            if s.row["src"] in ids and s.row["dst"] in ids:
                out["conns"].append(dict(s.row))
        return out

    def paste_payload(self, payload, at=None, offset=30.0):
        st = context.store
        entries = payload.get("items") or []
        if not entries:
            return []
        xs = [e["row"]["x"] for e in entries]
        ys = [e["row"]["y"] for e in entries]
        if at is not None:
            dx, dy = at.x() - min(xs), at.y() - min(ys)
        else:
            dx = dy = offset
        t = time.time()
        idmap, rows, versions = {}, [], {}
        z = st.max_z(self.bid) + 1
        for e in sorted(entries, key=lambda e: e["row"].get("z", 0)):
            r = dict(e["row"])
            nid = new_id()
            idmap[r["id"]] = nid
            r.update(id=nid, board_id=self.bid, x=r["x"] + dx, y=r["y"] + dy, created=t, updated=t,
                     deleted=None, ver=None, vnum=0)
            if r["kind"] != "frame":
                r["z"] = z
                z += 1
            rows.append(r)
            if e.get("version"):
                versions[nid] = e["version"]
        with st.tx():
            for r in rows:
                if r["id"] in versions:
                    st.put_item(dict(r, deleted=t))
                    v = versions[r["id"]]
                    st.add_version(r["id"], v["title"], v["doc"], v["text"], v["thumb"], v.get("note"))
        conns = []
        for c in payload.get("conns") or []:
            if c["src"] in idmap and c["dst"] in idmap:
                conns.append(dict(c, id=new_id(), board_id=self.bid, src=idmap[c["src"]], dst=idmap[c["dst"]],
                                  created=t, updated=t, deleted=None))
        return self.add_rows(rows, conns, label="Paste")

    # ------------------------------------------------------------------ strings
    def string_drag_active(self):
        return self._sdrag is not None

    def target_at(self, pos, exclude=None):
        for it in self.items(pos):
            if isinstance(it, BoardItem) and it is not exclude and not isinstance(it, LineItem):
                return it
        return None

    def begin_string(self, item, pos):
        t = TempString(item.pin_scene())
        self.addItem(t)
        t.set_end(pos)
        self._sdrag = {"src": item, "temp": t, "target": None}

    def update_string_drag(self, pos):
        d = self._sdrag
        if d is None:
            return
        tgt = self.target_at(pos, d["src"])
        if tgt is not d["target"]:
            if d["target"] is not None:
                d["target"]._hover = False
                d["target"].update()
            d["target"] = tgt
            if tgt is not None:
                tgt._hover = True
                tgt.update()
        d["temp"].set_end(tgt.pin_scene() if tgt is not None else pos)

    def end_string_drag(self, pos):
        d = self._sdrag
        self._sdrag = None
        if d is None:
            return
        self.removeItem(d["temp"])
        if d["target"] is not None:
            d["target"]._hover = False
            d["target"].update()
        tgt = self.target_at(pos, d["src"])
        if tgt is None:
            return
        src = d["src"]
        for s in src.strings:
            if {s.row["src"], s.row["dst"]} == {src.id, tgt.id}:
                self.clearSelection()
                s.setSelected(True)
                return
        row = context.store.new_conn(self.bid, src.id, tgt.id, "", {"color": self.string_color})
        self.commit([("conn", dict(row, deleted=time.time()))], [("conn", row)], "Connect")
        s = self.strings_by_id.get(row["id"])
        if s is not None:
            self.clearSelection()
            s.setSelected(True)
            self.label_edit.emit(s)

    def connect_items(self, a, b, label=""):
        row = context.store.new_conn(self.bid, a.id, b.id, label, {"color": self.string_color})
        self.commit([("conn", dict(row, deleted=time.time()))], [("conn", row)], "Connect")
        return self.strings_by_id.get(row["id"])

    # ------------------------------------------------------------------ mouse
    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton and self.tool == "select":
            self._press_geo = {iid: (it.x(), it.y()) for iid, it in self.items_by_id.items()}
        super().mousePressEvent(e)

    def mouseReleaseEvent(self, e):
        super().mouseReleaseEvent(e)
        geo, self._press_geo = self._press_geo, None
        if geo is None:
            return
        moved = []
        for iid, (x, y) in geo.items():
            it = self.items_by_id.get(iid)
            if it is not None and (abs(it.x() - x) > 0.01 or abs(it.y() - y) > 0.01):
                moved.append((it, x, y))
        if moved:
            before = [("item", dict(it.current_row(), x=x, y=y)) for it, x, y in moved]
            after = [("item", it.current_row()) for it, _, _ in moved]
            self.commit(before, after, "Move")

    def mouseDoubleClickEvent(self, e):
        if e.button() == Qt.LeftButton:
            for it in self.items(e.scenePos()):
                if isinstance(it, BoardItem):
                    if it.KIND in ("evidence", "file", "boardlink"):
                        self.open_item.emit(it)
                        e.accept()
                        return
                    break
        super().mouseDoubleClickEvent(e)

    # ------------------------------------------------------------------ queries
    def content_rect(self):
        r = QRectF()
        for it in self.items_by_id.values():
            r = r.united(it.scene_rect()) if not r.isNull() else it.scene_rect()
        return r

    def selected_board_items(self):
        return [i for i in self.selectedItems() if isinstance(i, BoardItem)]

    def selected_strings(self):
        return [i for i in self.selectedItems() if isinstance(i, StringItem)]
