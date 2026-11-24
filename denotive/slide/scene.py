"""The slide canvas: owns the pages, selection handles, smart guides, groups, creation tools and snapshot undo."""
import json
import math

from PySide6.QtCore import QLineF, QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QImage, QPainter, QPainterPath, QPen, QTextCursor
from PySide6.QtWidgets import QApplication, QGraphicsScene

from .. import geometry, theme
from .items import (GAP_COLOR, PAGE_GAP, SlideImage, SlideItem, SlideLine, SlidePath, SlideShape, SlideStroke,
                    SlideText, SlideZoom, TextBearing, default_grad, grad_geom, item_from_dict, make_pen, short_id)

HANDLE_PX = 9
HIT_PX = 8
ROT_PX = 26
SNAP_PX = 7
SURROUND = QColor("#1b1d22")
GUIDE = QColor("#ff4fa3")
HANDLE_NAMES = ("tl", "t", "tr", "r", "br", "b", "bl", "l")
HANDLE_LOCAL = {"tl": (0, 0), "t": (0.5, 0), "tr": (1, 0), "r": (1, 0.5), "br": (1, 1), "b": (0.5, 1),
                "bl": (0, 1), "l": (0, 0.5)}
GROUP = "group"  # stands in for the item when a whole group's handles are grabbed
# where a line end can stick to an object: corners, edge middles and the centre (fractions of its box)
ANCHORS = ((0, 0), (0.5, 0), (1, 0), (1, 0.5), (1, 1), (0.5, 1), (0, 1), (0, 0.5), (0.5, 0.5))
ANCHOR_PX = 11
GLUED = QColor("#2fb36d")
AREA_HANDLES = {"a_tl": (0, 0), "a_tr": (1, 0), "a_br": (1, 1), "a_bl": (0, 1)}
GRAD_TYPES = (SlideShape, SlideText, SlidePath)  # things with a fill that can be a gradient
GRAD_RING_PX = 22  # how far the gradient circle stands off the item's farthest corner


def _seg_dist(p, a, b):
    """Distance from point p to the segment a-b."""
    vx, vy = b.x() - a.x(), b.y() - a.y()
    L2 = vx * vx + vy * vy
    t = 0.0 if L2 < 1e-9 else max(0.0, min(1.0, ((p.x() - a.x()) * vx + (p.y() - a.y()) * vy) / L2))
    return math.hypot(p.x() - a.x() - t * vx, p.y() - a.y() - t * vy)


def _no_snap():
    return bool(QApplication.keyboardModifiers() & Qt.AltModifier)


class SlideScene(QGraphicsScene):
    tool_reset = Signal()
    state_pushed = Signal()
    edit_started = Signal(object)
    edit_stopped = Signal()
    context_requested = Signal(object, object)  # screen QPoint, item or None
    page_added = Signal(int)
    notice = Signal(str)  # a short message for the status toast
    pick_done = Signal()

    def __init__(self, doc=None, parent=None):
        super().__init__(parent)
        self.rendering = False
        self.transparent = False
        self.image_max_dim = 2560
        self.view_scale = 1.0
        self.tool = "select"
        self.opts = {"pen_color": "#e5484d", "pen_w": 4.0, "hl_color": "#ffe14d", "hl_w": 22.0,
                     "line_color": "#1d1f24", "line_w": 4.0, "shape_fill": "#5b8cff", "text_color": "#1d1f24",
                     "text_px": 24.0, "font": "Segoe UI"}
        self.editing = None
        self.guides = []
        self.history, self.hidx = [], -1
        self._xf = None
        self._create = None
        self._moving = False
        self._drag = None
        self._targets = None
        self._erasing = False
        self._syncing = False
        self._prev_sel = set()
        self.hover_add = False
        self._carrying = set()  # items moving together right now: their stuck lines move with them anyway
        self._anchor_hint = None  # (item, (u, v) or None): sticking points to show while drawing a line
        self.pick = None  # (line, "g1" | "g2") while waiting for a click on the object to attach to
        self.grad_item = None  # the item whose gradient the Gradient tool is aiming
        self.ff = None  # the Freeform tool's corners so far
        self.pe = None  # editing a path's corners: {"item", "sel": index of the picked corner, "drag"}
        self._gx = None  # a gradient handle being dragged
        self.ctx_pos = QPointF()
        self.current_page = 0
        self.page_w, self.page_h = 1280.0, 720.0
        self.pages = 1
        self.bg = "#ffffff"
        self.notes = ""
        self.load(doc or {})
        self.selectionChanged.connect(self._sync_groups)
        self.selectionChanged.connect(self.update)

    # ------------------------------------------------------------------ document & pages
    def page_top(self, k):
        return k * (self.page_h + PAGE_GAP)

    def page_rects(self):
        return [QRectF(0, self.page_top(k), self.page_w, self.page_h) for k in range(self.pages)]

    def total_h(self):
        return self.pages * self.page_h + (self.pages - 1) * PAGE_GAP

    def page_rect(self):
        """The whole evidence: every page and the gaps between them."""
        return QRectF(0, 0, self.page_w, self.total_h())

    def page_at(self, y):
        """Index of the page nearest to scene y."""
        k = int((y + PAGE_GAP / 2) // (self.page_h + PAGE_GAP))
        return min(self.pages - 1, max(0, k))

    def page_containing(self, pos):
        for k, r in enumerate(self.page_rects()):
            if r.contains(pos):
                return k
        return None

    def page_of(self, it):
        return self.page_at(self._box(it).center().y())

    def page_center(self, k=None):
        r = self.page_rects()[self.current_page if k is None else min(k, self.pages - 1)]
        return r.center()

    def _update_scene_rect(self):
        m = max(self.page_w, self.page_h)
        self.setSceneRect(QRectF(-m, -m, self.page_w + 2 * m, self.total_h() + 2 * m))

    def load(self, doc, keep_history=False):
        self.editing = None
        for it in list(self.items()):
            if it.parentItem() is None:
                self.removeItem(it)
        self.page_w = float(doc.get("w", 1280))
        self.page_h = float(doc.get("h", 720))
        self.pages = max(1, int(doc.get("pages", 1) or 1))
        self.current_page = min(self.current_page, self.pages - 1)
        self.bg = doc.get("bg", "#ffffff")
        self.notes = doc.get("notes", "")
        self._update_scene_rect()
        for d in doc.get("items", []):
            it = item_from_dict(d)
            if it is not None:
                self.addItem(it)
        if not keep_history:
            self.history, self.hidx = [], -1
            self.push_state()
        self._prev_sel = set()
        self.update()

    def slide_items(self):
        return [it for it in self.items() if isinstance(it, SlideItem)]

    def sorted_items(self):
        return sorted(self.slide_items(), key=lambda i: i.zValue())

    def to_doc(self):
        return {"v": 1, "w": self.page_w, "h": self.page_h, "pages": self.pages, "bg": self.bg, "notes": self.notes,
                "items": [it.to_dict() for it in self.sorted_items()]}

    def plain_text(self):
        parts = [it.text_content() for it in self.sorted_items()]
        if self.notes:
            parts.append(self.notes)
        return "\n".join(p for p in parts if p and p.strip())

    def set_page(self, w, h):
        w, h = float(w), float(h)
        if self.pages > 1 and h != self.page_h:
            # keep everything on the page it was on
            for it in self.slide_items():
                k = self.page_of(it)
                if k:
                    it.moveBy(0, k * (h - self.page_h))
        self.page_w, self.page_h = w, h
        self._update_scene_rect()
        self.update()

    def add_page(self, after=None):
        """Adds an empty page below page `after` (default: at the end) and returns its index."""
        k = self.pages if after is None else min(after + 1, self.pages)
        step = self.page_h + PAGE_GAP
        for it in self.slide_items():
            if self.page_of(it) >= k:
                it.moveBy(0, step)
        self.pages += 1
        self._update_scene_rect()
        self.push_state()
        self.update()
        self.page_added.emit(k)
        return k

    def delete_page(self, k):
        """Removes page k and everything on it; the pages below move up."""
        if self.pages <= 1:
            return False
        self.stop_text_edit()
        step = self.page_h + PAGE_GAP
        for it in self.slide_items():
            pk = self.page_of(it)
            if pk == k:
                self.removeItem(it)
            elif pk > k:
                it.moveBy(0, -step)
        self.pages -= 1
        self.current_page = min(self.current_page, self.pages - 1)
        self._update_scene_rect()
        self.push_state()
        self.update()
        return True

    def item_by_uid(self, uid):
        return next((i for i in self.slide_items() if i.uid == uid), None) if uid else None

    def item_geometry_changed(self, it):
        """Keeps zoom callouts on their picture and stuck line ends on their objects as things move."""
        for o in self.slide_items():
            if isinstance(o, SlideZoom) and (o is it or o.src == it.uid):
                o.prepareGeometryChange()
                o.update()
            elif isinstance(o, SlideLine) and o is not it and o not in self._carrying and o.glued_to(it.uid):
                self._follow(o)

    def glue_point(self, g):
        t = self.item_by_uid(g.get("id")) if g else None
        if t is None:
            return None
        return t.mapToScene(QPointF(float(g["u"]) * t.w, float(g["v"]) * t.h))

    @staticmethod
    def _stand_off(anchor, other, g):
        """An end with a gap stops that far short of its spot, still aimed straight at it."""
        gap = float((g or {}).get("gap") or 0)
        v = anchor - other
        length = math.hypot(v.x(), v.y())
        if gap <= 0 or length < 1e-6:
            return anchor
        gap = min(gap, length * 0.9)
        return anchor - v * (gap / length)

    def _follow(self, line):
        if not line.stick:
            return
        a, b = self.glue_point(line.g1), self.glue_point(line.g2)
        if a is None and b is None:
            return
        a = a if a is not None else line.mapToScene(line.p1)
        b = b if b is not None else line.mapToScene(line.p2)
        line.set_points(line.mapFromScene(self._stand_off(a, b, line.g1)),
                        line.mapFromScene(self._stand_off(b, a, line.g2)))

    def _unglue_moved(self, moved):
        """A line dragged away from the objects its ends were stuck to lets go of them."""
        uids = {i.uid for i in moved}
        for it in moved:
            if isinstance(it, SlideLine):
                for k in ("g1", "g2"):
                    g = getattr(it, k)
                    if g and g.get("id") not in uids:
                        setattr(it, k, None)

    def _glue_targets(self, exclude):
        return [o for o in self.slide_items() if not isinstance(o, SlideLine) and o.isVisible() and o not in exclude]

    def anchor_at(self, pos, exclude=()):
        """The nearest corner / edge middle / centre of an object within reach: (item, u, v, point), or None."""
        if _no_snap():
            return None
        reach = ANCHOR_PX / max(0.05, self.view_scale)
        best = None
        for o in self._glue_targets(exclude):
            for u, v in ANCHORS:
                pt = o.mapToScene(QPointF(u * o.w, v * o.h))
                d = QLineF(pt, pos).length()
                if d <= reach and (best is None or d < best[0]):
                    best = (d, o, u, v, pt)
        return best[1:] if best else None

    def _hint(self, pos, exclude=()):
        """Which object's sticking points to show: the one being snapped to, else the one under the pointer."""
        a = self.anchor_at(pos, exclude)
        if a is not None:
            return a[0], (a[1], a[2])
        for o in self.items(pos):
            while o is not None and not isinstance(o, SlideItem):
                o = o.parentItem()
            if o is not None and not isinstance(o, SlideLine) and o.isVisible() and o not in exclude:
                return o, None
        return None

    def start_pick(self, line, key):
        """The next click on an object attaches that end of the line to it, right where the end is now."""
        self.pick = (line, key)

    def _finish_pick(self, pos):
        line, key = self.pick
        self.pick = None
        target = None
        for o in self.items(pos):
            while o is not None and not isinstance(o, SlideItem):
                o = o.parentItem()
            if o is not None and o is not line and not isinstance(o, SlideLine) and o.isVisible():
                target = o
                break
        which = "Start" if key == "g1" else "End"
        if target is None or line.scene() is not self:
            self.notice.emit(f"{which} not attached: click an object to attach it to")
        else:
            end = line.mapToScene(line.p1 if key == "g1" else line.p2)
            lp = target.mapFromScene(end)
            setattr(line, key, {"id": target.uid, "u": round(lp.x() / max(1e-6, target.w), 5),
                                "v": round(lp.y() / max(1e-6, target.h), 5)})
            line.stick = True
            self.push_state()
            self.notice.emit(f"{which} attached to {target.label()} — it follows when that moves")
        self.pick_done.emit()
        self.update()

    # ------------------------------------------------------------------ zoom-in callouts
    def picture_under(self, rect, exclude=()):
        """The topmost picture under the middle of rect."""
        for o in self.items(rect.center()):
            while o is not None and not isinstance(o, SlideItem):
                o = o.parentItem()
            if isinstance(o, SlideImage) and o.isVisible() and o not in exclude:
                return o
        return None

    def make_zoom(self, img, area, shape="rect", stroke=None, sw=3.0):
        """Magnifies the part of `img` under the scene rect `area` in a new box placed beside the picture."""
        local = img.mapFromScene(area).boundingRect()
        u0, u1 = sorted((max(0.0, min(1.0, local.left() / img.w)), max(0.0, min(1.0, local.right() / img.w))))
        v0, v1 = sorted((max(0.0, min(1.0, local.top() / img.h)), max(0.0, min(1.0, local.bottom() / img.h))))
        if (u1 - u0) * img.w < 4 or (v1 - v0) * img.h < 4:  # a click: a square a quarter of the picture wide
            c = img.mapFromScene(area.center())
            half = min(img.w, img.h) * 0.125
            u0, u1 = max(0.0, (c.x() - half) / img.w), min(1.0, (c.x() + half) / img.w)
            v0, v1 = max(0.0, (c.y() - half) / img.h), min(1.0, (c.y() + half) / img.h)
        lw, lh = (u1 - u0) * img.w, (v1 - v0) * img.h
        lens = img.mapToScene(QRectF(u0 * img.w, v0 * img.h, lw, lh)).boundingRect()
        page = self.page_rects()[self.page_at(lens.center().y())]
        m = max(1.2, min(2.5, page.width() * 0.42 / lw, page.height() * 0.6 / lh))
        box = self._place_inset(lens, self._box(img), lw * m, lh * m, page)
        z = SlideZoom({"x": box.x(), "y": box.y(), "w": box.width(), "h": box.height(), "src": img.uid,
                       "area": [u0, v0, u1, v1], "shape": shape, "stroke": stroke or "#e5484d", "sw": sw})
        self.add(z)
        self.push_state()
        return z

    @staticmethod
    def _place_inset(lens, pic, w, h, page):
        """Beside the picture (right, left, below, above), inside the page, clear of the area it magnifies."""
        gap = 28
        cands = [QRectF(pic.right() + gap, lens.center().y() - h / 2, w, h),
                 QRectF(pic.left() - gap - w, lens.center().y() - h / 2, w, h),
                 QRectF(lens.center().x() - w / 2, pic.bottom() + gap, w, h),
                 QRectF(lens.center().x() - w / 2, pic.top() - gap - h, w, h)]

        def inside(r):
            return QRectF(min(max(r.x(), page.left() + 8), page.right() - 8 - r.width()),
                          min(max(r.y(), page.top() + 8), page.bottom() - 8 - r.height()), r.width(), r.height())

        for r in cands:
            if page.contains(r):
                return r
        for r in map(inside, cands):
            if not r.intersects(lens):
                return r
        corner = QRectF(page.right() - 8 - w, page.top() + 8, w, h)
        return corner if not corner.intersects(lens) else QRectF(page.left() + 8, page.top() + 8, w, h)

    def punch_zoom(self, shape):
        """Punch & Zoom: the area under a shape drawn on a picture becomes a zoom-in callout of that shape."""
        img = self.picture_under(self._box(shape), (shape,))
        if img is None:
            return None
        area = self._box(shape)
        kind = getattr(shape, "shape_kind", "rect")
        stroke = shape.stroke if getattr(shape, "stroke", None) and getattr(shape, "sw", 0) else None
        sw = shape.sw if stroke else 3.0
        self.removeItem(shape)
        return self.make_zoom(img, area, kind, stroke, sw)

    def next_z(self):
        zs = [it.zValue() for it in self.slide_items()]
        return (max(zs) + 1) if zs else 1

    def add(self, it, select=True):
        if it.zValue() == 0:
            it.setZValue(self.next_z())
        self.addItem(it)
        if select:
            self.clearSelection()
            it.setSelected(True)
        return it

    # ------------------------------------------------------------------ undo
    def snapshot(self):
        d = self.to_doc()
        d.pop("notes", None)
        return json.dumps(d, sort_keys=True)

    def push_state(self):
        s = self.snapshot()
        if self.hidx >= 0 and self.history[self.hidx] == s:
            return False
        del self.history[self.hidx + 1:]
        self.history.append(s)
        if len(self.history) > 250:
            self.history.pop(0)
        self.hidx = len(self.history) - 1
        self.state_pushed.emit()
        return True

    def _restore(self, s):
        self.ff = self.pe = None  # undo / redo swap in new items
        sel = {it.uid for it in self.selectedItems() if isinstance(it, SlideItem)}
        d = json.loads(s)
        d["notes"] = self.notes
        self.load(d, keep_history=True)
        self._syncing = True
        for it in self.slide_items():
            if it.uid in sel:
                it.setSelected(True)
        self._syncing = False
        self._prev_sel = set(self.selected())
        self.state_pushed.emit()

    def can_undo(self):
        return self.hidx > 0

    def can_redo(self):
        return self.hidx < len(self.history) - 1

    def undo(self):
        self.stop_text_edit()
        if self.can_undo():
            self.hidx -= 1
            self._restore(self.history[self.hidx])

    def redo(self):
        self.stop_text_edit()
        if self.can_redo():
            self.hidx += 1
            self._restore(self.history[self.hidx])

    # ------------------------------------------------------------------ text editing
    def start_text_edit(self, item, scene_pos=None, select_all=False):
        if not isinstance(item, TextBearing) or item.locked:
            return
        if self.editing is item:
            return
        self.stop_text_edit()
        dp = item.doc_point(item.mapFromScene(scene_pos)) if scene_pos is not None and hasattr(item, "doc_point") \
            else None
        self.editing = item
        item.layout_children()  # a column box shows as one column while typing
        t = item.text
        t.setTextInteractionFlags(Qt.TextEditorInteraction)
        t.setAcceptedMouseButtons(Qt.LeftButton)
        t.setFlag(t.GraphicsItemFlag.ItemIsFocusable, True)
        t.setFocus(Qt.MouseFocusReason)
        c = t.textCursor()
        if select_all:
            c.select(QTextCursor.Document)
        elif scene_pos is not None:
            pos = t.document().documentLayout().hitTest(dp if dp is not None else t.mapFromScene(scene_pos),
                                                        Qt.FuzzyHit)
            c.setPosition(max(0, pos))
        else:
            c.movePosition(QTextCursor.End)
        t.setTextCursor(c)
        self.clearSelection()
        item.setSelected(True)
        item.update()
        self.edit_started.emit(item)

    def stop_text_edit(self):
        it = self.editing
        if it is None:
            return
        self.editing = None
        t = it.text
        c = t.textCursor()
        c.clearSelection()
        t.setTextCursor(c)
        t.setTextInteractionFlags(Qt.NoTextInteraction)
        t.setAcceptedMouseButtons(Qt.NoButton)
        t.clearFocus()
        t.setFlag(t.GraphicsItemFlag.ItemIsFocusable, False)
        if isinstance(it, SlideText) and not it.text_content().strip() and not it.fill and it.scene() is self:
            self.removeItem(it)
        else:
            it.layout_children()  # back into columns
            it.update()
            if it.group and it.isSelected():
                self._sync_groups()  # bring the rest of its group back into the selection
        self.push_state()
        self.edit_stopped.emit()

    # ------------------------------------------------------------------ groups
    def selected(self):
        return [i for i in self.selectedItems() if isinstance(i, SlideItem)]

    def groups(self):
        out = {}
        for it in self.slide_items():
            if it.group:
                out.setdefault(it.group, []).append(it)
        return {g: m for g, m in out.items() if len(m) > 1}

    def _sync_groups(self):
        """Selecting part of a group selects all of it; Ctrl/Shift-clicking a member of a selected group drops it."""
        if self._syncing:
            return
        sel = set(self.selected())
        if self.editing is None:
            mods = QApplication.keyboardModifiers()
            adding = bool(mods & (Qt.ControlModifier | Qt.ShiftModifier))
            self._syncing = True
            try:
                for members in self.groups().values():
                    n = sum(m in sel for m in members)
                    if 0 < n < len(members):
                        was_full = all(m in self._prev_sel for m in members)
                        on = not (was_full and adding)
                        for m in members:
                            m.setSelected(on)
            finally:
                self._syncing = False
            sel = set(self.selected())
        self._prev_sel = sel

    def selected_group(self):
        """The group id when the selection is exactly one whole group."""
        sel = self.selected()
        if len(sel) < 2 or not sel[0].group:
            return None
        gid = sel[0].group
        if all(i.group == gid for i in sel) and len(sel) == len(self.groups().get(gid, ())):
            return gid
        return None

    def group_selected(self):
        sel = self.selected()
        if len(sel) < 2:
            return False
        old = {i.group for i in sel if i.group}
        gid = short_id()
        for it in self.slide_items():
            if it in sel or (it.group and it.group in old):
                it.group = gid
        self._prev_sel = set(self.selected())
        self.push_state()
        self.update()
        return True

    def ungroup_selected(self):
        gids = {i.group for i in self.selected() if i.group}
        if not gids:
            return False
        for it in self.slide_items():
            if it.group in gids:
                it.group = None
        self.push_state()
        self.update()
        return True

    # ------------------------------------------------------------------ handles
    def _single(self):
        sel = self.selected()
        return sel[0] if len(sel) == 1 else None

    def _union(self, items):
        r = QRectF()
        for it in items:
            b = self._box(it)
            r = b if r.isNull() else r.united(b)
        return r

    def handle_points(self, it):
        if it is GROUP:
            r = self._union(self.selected())
            return {n: QPointF(r.x() + u * r.width(), r.y() + v * r.height()) for n, (u, v) in HANDLE_LOCAL.items()}
        if isinstance(it, SlideLine):
            return {"p1": it.mapToScene(it.p1), "p2": it.mapToScene(it.p2)}
        if isinstance(it, SlideZoom):
            pts = {n: it.mapToScene(QPointF(u * it.w, v * it.h)) for n, (u, v) in HANDLE_LOCAL.items() if len(n) == 2}
            img = it.source()
            if img is not None:
                a = it.area_local(img)
                pts.update({n: img.mapToScene(QPointF(a.x() + u * a.width(), a.y() + v * a.height()))
                            for n, (u, v) in AREA_HANDLES.items()})
            return pts
        pts = {n: it.mapToScene(QPointF(u * it.w, v * it.h)) for n, (u, v) in HANDLE_LOCAL.items()}
        pts["rot"] = it.mapToScene(QPointF(it.w / 2, -ROT_PX / max(0.05, self.view_scale)))
        if isinstance(it, SlideText):
            for n in ("t", "b"):
                pts.pop(n, None)
        return pts

    def handle_at(self, scene_pos):
        if self.editing is not None:
            return None, None
        it = self._single()
        if it is None:
            if self.selected_group() is None or any(i.locked for i in self.selected()):
                return None, None
            it = GROUP
        elif it.locked:
            return None, None
        best, bd = None, HIT_PX / max(0.05, self.view_scale)
        for n, p in self.handle_points(it).items():
            d = QLineF(p, scene_pos).length()
            if d <= bd:
                best, bd = n, d
        if best is None and isinstance(it, SlideZoom) and it.mapToScene(it.lens_path()).contains(scene_pos):
            best = "area"  # drag the outline on the picture to magnify a different part
        return it, best

    def cursor_for(self, it, name):
        if name is None:
            return None
        if name in ("rot",):
            return Qt.CrossCursor
        if name in ("p1", "p2", "area"):
            return Qt.SizeAllCursor
        if name in AREA_HANDLES:
            return Qt.SizeFDiagCursor if name in ("a_tl", "a_br") else Qt.SizeBDiagCursor
        u, v = HANDLE_LOCAL[name]
        ang = math.degrees(math.atan2(v - 0.5, u - 0.5)) + (0 if it is GROUP else it.rotation())
        ang = (ang + 360) % 180
        if ang < 22.5 or ang >= 157.5:
            return Qt.SizeHorCursor
        if ang < 67.5:
            return Qt.SizeFDiagCursor
        if ang < 112.5:
            return Qt.SizeVerCursor
        return Qt.SizeBDiagCursor

    # ------------------------------------------------------------------ gradient tool
    def grad_target(self):
        """The item whose gradient circle shows: the one clicked with the Gradient tool (or the one selected item)."""
        if self.tool != "gradient" or self.editing is not None:
            return None
        it = self.grad_item
        if it is None or it.scene() is not self or not it.isSelected():
            it = self._single()
        if isinstance(it, GRAD_TYPES) and it.grad and it.fill and not it.locked and it.isVisible():
            return it
        return None

    def start_gradient(self, it):
        """Aims the Gradient tool at an item, giving it a gradient made from its fill if it hasn't one yet.
        False if this kind of item can't have one."""
        if not isinstance(it, GRAD_TYPES) or it.locked:
            return False
        self.grad_item = it
        if not it.grad:
            it.set_grad(default_grad(it.fill or self.opts["shape_fill"], it.w, it.h))
            self.push_state()
        self.update()
        return True

    def grad_parts(self, it):
        """The gradient circle in scene coordinates: centre, radius, the two edge lines (end, end, grip) and the
        turning knob on the ring."""
        g = it.grad
        s = max(0.05, self.view_scale)
        c, (dx, dy), L, _, _ = grad_geom(g, it.w, it.h)
        R = max(math.hypot(c.x() - x, c.y() - y) for x, y in ((0, 0), (it.w, 0), (0, it.h), (it.w, it.h)))
        R += GRAD_RING_PX / s
        nx, ny = -dy, dx
        out = {"c": it.mapToScene(c), "R": R, "knob": it.mapToScene(QPointF(c.x() + R * dx, c.y() + R * dy))}
        for k in ("a", "b"):
            o = float(g[k]) * L
            half = max(math.sqrt(max(R * R - o * o, 0.0)), 26 / s)  # a chord of the circle (kept visible outside it)
            m = QPointF(c.x() + o * dx, c.y() + o * dy)
            grip = 0.55 * half  # partway along the line, clear of the centre dot
            out[k] = (it.mapToScene(QPointF(m.x() - half * nx, m.y() - half * ny)),
                      it.mapToScene(QPointF(m.x() + half * nx, m.y() + half * ny)),
                      it.mapToScene(QPointF(m.x() - grip * nx, m.y() - grip * ny)))
        return out

    def grad_hit(self, pos):
        """Which part of the gradient circle is under pos: "c" centre, "a"/"b" an edge line, "ring", or None."""
        it = self.grad_target()
        if it is None:
            return None
        gp = self.grad_parts(it)
        tol = HIT_PX / max(0.05, self.view_scale)
        if QLineF(gp["c"], pos).length() <= tol * 1.1:
            return "c"
        for k in ("b", "a"):
            if QLineF(gp[k][2], pos).length() <= tol * 1.2:
                return k
        for k in ("b", "a"):
            if _seg_dist(pos, gp[k][0], gp[k][1]) <= tol * 0.75:
                return k
        if QLineF(gp["knob"], pos).length() <= tol * 1.3 or abs(QLineF(gp["c"], pos).length() - gp["R"]) <= tol:
            return "ring"
        return None

    def grad_cursor(self, part):
        return {"c": Qt.SizeAllCursor, "a": Qt.SplitHCursor, "b": Qt.SplitHCursor,
                "ring": Qt.OpenHandCursor}.get(part)

    def _begin_grad(self, it, part, pos):
        loc = it.mapFromScene(pos)
        c = grad_geom(it.grad, it.w, it.h)[0]
        self._gx = {"item": it, "part": part, "g0": dict(it.grad, c=list(it.grad["c"])),
                    "a0": math.atan2(loc.y() - c.y(), loc.x() - c.x())}
        for v in self.views():
            if part == "ring":
                v.viewport().setCursor(Qt.ClosedHandCursor)

    def _update_grad(self, pos, mods):
        x = self._gx
        it, part = x["item"], x["part"]
        g = dict(x["g0"], c=list(x["g0"]["c"]))
        loc = it.mapFromScene(pos)
        s = max(0.05, self.view_scale)
        free = bool(mods & Qt.AltModifier)
        if part == "c":
            fx, fy = loc.x() / max(1.0, it.w), loc.y() / max(1.0, it.h)
            if not free and math.hypot(loc.x() - it.w / 2, loc.y() - it.h / 2) <= SNAP_PX / s:
                fx, fy = 0.5, 0.5  # back on the middle
            g["c"] = [round(fx, 5), round(fy, 5)]
        elif part in ("a", "b"):
            c, (dx, dy), L, _, _ = grad_geom(g, it.w, it.h)
            o = (loc.x() - c.x()) * dx + (loc.y() - c.y()) * dy
            if not free:  # snaps to the item's edges (as seen along the direction) and to the centre
                spots = [0.0] + [(x0 - c.x()) * dx + (y0 - c.y()) * dy
                                 for x0, y0 in ((0, 0), (it.w, 0), (0, it.h), (it.w, it.h))]
                near = min(spots, key=lambda v: abs(v - o))
                if abs(near - o) <= SNAP_PX / s:
                    o = near
            other = g["b" if part == "a" else "a"] * L
            if abs(o - other) < 2:  # the two edges never quite meet
                o = other + (2 if part == "b" else -2) * (1 if g["b"] >= g["a"] else -1)
            g[part] = round(o / L, 5)
        elif part == "ring":
            c = grad_geom(g, it.w, it.h)[0]
            a = math.atan2(loc.y() - c.y(), loc.x() - c.x())
            ang = x["g0"]["ang"] + math.degrees(a - x["a0"])
            if mods & Qt.ShiftModifier:
                ang = round(ang / 15.0) * 15.0
            g["ang"] = round(ang % 360, 3)
        it.set_grad(g)
        self.update()

    def _end_grad(self):
        self._gx = None
        for v in self.views():
            v.viewport().setCursor(Qt.PointingHandCursor)
        self.push_state()

    def recentre_grad(self, it):
        if it is not None and it.grad:
            it.set_grad(dict(it.grad, c=[0.5, 0.5]))
            self.push_state()
            self.update()

    def _draw_grad(self, p, it, s):
        gp = self.grad_parts(it)
        c, R = gp["c"], gp["R"]
        halo = QPen(QColor(0, 0, 0, 120), 3.4)
        halo.setCosmetic(True)
        main = QPen(QColor(theme.SELECT), 1.6)
        main.setCosmetic(True)
        p.setBrush(Qt.NoBrush)
        for pen in (halo, main):
            p.setPen(pen)
            p.drawEllipse(c, R, R)
            for k in ("a", "b"):
                p.drawLine(gp[k][0], gp[k][1])
        dot = QPen(QColor(theme.SELECT), 1.2, Qt.DotLine)
        dot.setCosmetic(True)
        p.setPen(dot)
        p.drawLine(c, gp["knob"])  # which way the colours run
        p.setPen(QPen(QColor("#ffffff"), 1.4))
        p.setBrush(QColor(theme.SELECT))
        p.drawEllipse(gp["knob"], 6.5 / s, 6.5 / s)
        edge = QPen(QColor("#ffffff"), 1.6)
        edge.setCosmetic(True)
        for k, col in (("a", it.grad["c1"]), ("b", it.grad["c2"])):
            m = gp[k][2]
            r = 6.5 / s
            p.setPen(halo)
            p.setBrush(Qt.NoBrush)
            p.drawRoundedRect(QRectF(m.x() - r, m.y() - r, 2 * r, 2 * r), r * 0.3, r * 0.3)
            p.setPen(edge)
            p.setBrush(QColor(col))
            p.drawRoundedRect(QRectF(m.x() - r, m.y() - r, 2 * r, 2 * r), r * 0.3, r * 0.3)
        p.setPen(main)
        p.setBrush(QColor("#ffffff"))
        p.drawEllipse(c, 5.5 / s, 5.5 / s)
        p.drawLine(QPointF(c.x() - 2.5 / s, c.y()), QPointF(c.x() + 2.5 / s, c.y()))
        p.drawLine(QPointF(c.x(), c.y() - 2.5 / s), QPointF(c.x(), c.y() + 2.5 / s))

    # ------------------------------------------------------------------ smart guides
    def _box(self, it):
        """The item's own outline as an upright scene rectangle (no shadow or selection slack)."""
        return it.mapToScene(it.rect()).boundingRect()

    def _snap_targets(self, exclude):
        """Edges and centres of every page and every other visible item, as (value, extent) pairs per axis."""
        tx, ty = [], []
        rects = list(self.page_rects())
        rects += [self._box(o) for o in self.slide_items() if o not in exclude and o.isVisible()]
        for r in rects:
            for x in (r.left(), r.center().x(), r.right()):
                tx.append((x, r))
            for y in (r.top(), r.center().y(), r.bottom()):
                ty.append((y, r))
        return tx, ty

    def _targets_for(self, exclude):
        if self._targets is None:
            self._targets = self._snap_targets(set(exclude))
        return self._targets

    @staticmethod
    def _best(values, targets, thr):
        best = None
        for v in values:
            for t, _ in targets:
                d = t - v
                if abs(d) <= thr and (best is None or abs(d) < abs(best)):
                    best = d
        return best

    def _snap_rect(self, r, exclude, xs=None, ys=None):
        """Offset (dx, dy) lining up an edge or centre of r with a page or another item; sets the guides.

        xs / ys choose which of r's x / y positions may snap (default: both edges and the centre)."""
        if _no_snap():
            self.guides = []
            return 0.0, 0.0
        tx, ty = self._targets_for(exclude)
        thr = SNAP_PX / max(0.05, self.view_scale)
        xs = [r.left(), r.center().x(), r.right()] if xs is None else xs
        ys = [r.top(), r.center().y(), r.bottom()] if ys is None else ys
        dx = self._best(xs, tx, thr)
        dy = self._best(ys, ty, thr)
        self._set_guides(r.translated(dx or 0.0, dy or 0.0), [x + (dx or 0.0) for x in xs] if dx is not None else [],
                         [y + (dy or 0.0) for y in ys] if dy is not None else [], tx, ty)
        return dx or 0.0, dy or 0.0

    def _set_guides(self, r, xs, ys, tx, ty):
        """Pink guide lines running from the moved box to everything it now lines up with."""
        ext = 10 / max(0.05, self.view_scale)
        g = []
        for v in {round(x, 2) for x in xs}:
            hits = [e for t, e in tx if abs(t - v) < 0.5]
            if hits:
                top = min([r.top()] + [e.top() for e in hits])
                bottom = max([r.bottom()] + [e.bottom() for e in hits])
                g.append(QLineF(v, top - ext, v, bottom + ext))
        for v in {round(y, 2) for y in ys}:
            hits = [e for t, e in ty if abs(t - v) < 0.5]
            if hits:
                left = min([r.left()] + [e.left() for e in hits])
                right = max([r.right()] + [e.right() for e in hits])
                g.append(QLineF(left - ext, v, right + ext, v))
        self.guides = g
        self.update()

    def snap_point(self, pt, exclude=()):
        """A point (line end, shape corner) pulled onto nearby edges and centres."""
        dx, dy = self._snap_rect(QRectF(pt, pt), exclude, [pt.x()], [pt.y()])
        return QPointF(pt.x() + dx, pt.y() + dy)

    def snap_position(self, item, new_pos):
        """Called as Qt drags the selection: snaps the whole selection's outline, the same way for every item."""
        if not self._moving or self.rendering:
            return new_pos
        if self._drag is None:
            sel = [i for i in self.selected() if not i.locked]
            if item not in sel:
                return new_pos
            self._drag = {"start": {i: QPointF(i.pos()) for i in sel}, "box": self._union(sel),
                          "raw": None, "d": QPointF()}
            self._carrying = set(sel)
            self._targets = None
            self._targets_for(sel)
        dr = self._drag
        start = dr["start"].get(item)
        if start is None:
            return new_pos
        raw = new_pos - start
        if dr["raw"] is not None and raw == dr["raw"]:
            return start + dr["d"]  # another item of the same drag step
        dr["raw"] = QPointF(raw)
        dx, dy = self._snap_rect(dr["box"].translated(raw), ())
        dr["d"] = QPointF(raw.x() + dx, raw.y() + dy)
        return start + dr["d"]

    def _end_drag(self):
        if self._drag is not None:
            self._unglue_moved(set(self._drag["start"]))
        self._drag = None
        self._targets = None
        self._carrying = set()
        if self._anchor_hint is not None:
            self._anchor_hint = None
            self.update()
        if self.guides:
            self.guides = []
            self.update()

    # ------------------------------------------------------------------ painting
    def drawBackground(self, p, rect):
        if self.rendering:
            if not self.transparent:
                if self.pages > 1:
                    p.fillRect(self.page_rect(), QColor(GAP_COLOR))
                for page in self.page_rects():
                    p.fillRect(page, QColor(self.bg))
            return
        p.fillRect(rect, SURROUND)
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(Qt.NoPen)
        for page in self.page_rects():
            if not page.adjusted(-20, -20, 20, 20).intersects(rect):
                continue
            for i, a in ((2, 40), (6, 24), (12, 12)):
                p.setBrush(QColor(0, 0, 0, a))
                p.drawRoundedRect(page.adjusted(-i / 2, -i / 4, i / 2, i), 4, 4)
            p.fillRect(page, QColor(self.bg))

    def add_page_rect(self):
        """The '+ Add page' button drawn under the last page."""
        s = max(0.05, self.view_scale)
        w, h = 132 / s, 30 / s
        return QRectF(self.page_w / 2 - w / 2, self.total_h() + 16 / s, w, h)

    def drawForeground(self, p, rect):
        if self.rendering:
            return
        s = max(0.05, self.view_scale)
        p.setRenderHint(QPainter.Antialiasing)
        # page edge hints (content outside a page is clipped on export) and page numbers
        pen = QPen(QColor(255, 255, 255, 28), 1)
        pen.setCosmetic(True)
        p.setBrush(Qt.NoBrush)
        for k, page in enumerate(self.page_rects()):
            p.setPen(pen)
            p.drawRect(page)
            if self.pages > 1:
                p.setFont(theme.ui_font(11.5 / s))
                p.setPen(QColor(theme.MUTED))
                p.drawText(QRectF(page.x(), page.y() - 22 / s, page.width(), 20 / s), Qt.AlignLeft | Qt.AlignBottom,
                           f"Page {k + 1} of {self.pages}")
        # + Add page
        ar = self.add_page_rect()
        p.setPen(QPen(QColor(theme.SELECT if self.hover_add else "#3a3f4b"), 1.2 / s))
        p.setBrush(QColor("#2a2e38" if self.hover_add else "#22252d"))
        p.drawRoundedRect(ar, ar.height() / 2, ar.height() / 2)
        p.setPen(QColor(theme.TEXT if self.hover_add else theme.MUTED))
        p.setFont(theme.ui_font(12.5 / s))
        p.drawText(ar, Qt.AlignCenter, "+  Add page")
        # sticking points of the object a line end is near
        hint = self._anchor_hint
        if hint is not None and hint[0].scene() is self:
            tgt, uv = hint
            ap = QPen(QColor(theme.SELECT), 1.2)
            ap.setCosmetic(True)
            p.setPen(ap)
            for u, v in ANCHORS:
                on = uv == (u, v)
                p.setBrush(QColor(theme.SELECT) if on else QColor("#ffffff"))
                p.drawEllipse(tgt.mapToScene(QPointF(u * tgt.w, v * tgt.h)), (5.5 if on else 3.5) / s,
                              (5.5 if on else 3.5) / s)
        # guides
        if self.guides:
            gp = QPen(GUIDE, 1)
            gp.setCosmetic(True)
            p.setPen(gp)
            for g in self.guides:
                p.drawLine(g)
        # selection: a box around each selected group, an outline around everything else
        sel = self.selected()
        sp = QPen(QColor(theme.SELECT), 1.4)
        sp.setCosmetic(True)
        member = QPen(QColor(91, 140, 255, 150), 1, Qt.DashLine)
        member.setCosmetic(True)
        groups = {}
        for it in sel:
            if it.group:
                groups.setdefault(it.group, []).append(it)
        groups = {g: m for g, m in groups.items() if len(m) > 1}
        p.setBrush(Qt.NoBrush)
        for it in sel:
            if self.pe is not None and it is self.pe["item"]:
                continue  # its corners are shown instead
            grouped = it.group in groups
            p.setPen(member if grouped else sp)
            if isinstance(it, SlideLine):
                p.drawPath(it.mapToScene(it.shape()))  # capsule hugging the line/arrow
            else:
                p.drawPolygon(it.mapToScene(it.rect()))
        p.setPen(sp)
        for members in groups.values():
            p.drawRect(self._union(members))
        hs = HANDLE_PX / s
        gt = self.grad_target()
        if gt is not None:
            self._draw_grad(p, gt, s)
        elif self.pe is not None:
            self._draw_points(p, s)
        elif self.editing is None:
            single = sel[0] if len(sel) == 1 else None
            target = single if single is not None else (GROUP if self.selected_group() else None)
            locked = single.locked if single is not None else any(i.locked for i in sel)
            if target is not None:
                pts = self.handle_points(target)
                if "rot" in pts and not locked:
                    p.setPen(sp)
                    p.drawLine(single.mapToScene(QPointF(single.w / 2, 0)), pts["rot"])
                if not locked:
                    hp = QPen(QColor(theme.SELECT), 1.3)
                    hp.setCosmetic(True)
                    p.setPen(hp)
                    p.setBrush(QColor("#ffffff"))
                    for n, pt in pts.items():
                        if n == "rot":
                            p.drawEllipse(pt, hs * 0.62, hs * 0.62)
                        elif n in ("p1", "p2"):
                            stuck = getattr(single, "g1" if n == "p1" else "g2", None)
                            p.setBrush(GLUED if stuck else QColor("#ffffff"))  # green: stuck to an object
                            p.drawEllipse(pt, hs * 0.6, hs * 0.6)
                            p.setBrush(QColor("#ffffff"))
                        elif n in AREA_HANDLES:
                            p.drawEllipse(pt, hs * 0.45, hs * 0.45)
                        else:
                            p.drawRoundedRect(QRectF(pt.x() - hs / 2, pt.y() - hs / 2, hs, hs), hs * 0.2, hs * 0.2)
            if isinstance(single, SlideLine):  # an end kept short of its spot: show the spot it aims at
                for key, end in (("g1", single.p1), ("g2", single.p2)):
                    g = getattr(single, key)
                    spot = self.glue_point(g) if g and g.get("gap") else None
                    if spot is not None:
                        dp = QPen(GLUED, 1.2, Qt.DashLine)
                        dp.setCosmetic(True)
                        p.setPen(dp)
                        p.drawLine(single.mapToScene(end), spot)
                        p.setBrush(GLUED)
                        p.drawEllipse(spot, hs * 0.38, hs * 0.38)
            if single is not None and single.locked:
                p.setFont(theme.icon_font(12 / s))
                p.setPen(QColor(theme.SELECT))
                tl = single.mapToScene(QPointF(single.w, 0))
                p.drawText(QRectF(tl.x() + 2 / s, tl.y() - 16 / s, 16 / s, 16 / s), Qt.AlignCenter, theme.GLYPHS["lock"])
        if self.ff is not None:
            self._draw_freeform(p, s)
        # creation previews
        c = self._create
        if c is not None:
            if c["kind"] == "shape":
                r = self._drag_rect(c)
                p.setPen(make_pen(theme.SELECT, 1.5 / s))
                p.setBrush(QColor(91, 140, 255, 40))
                p.drawPath(geometry.shape_path(c["shape"], r, 16))
            elif c["kind"] in ("line", "arrow"):
                p.setPen(make_pen(self.opts["line_color"], self.opts["line_w"]))
                p.drawLine(c["start"], self._line_end(c))
            elif c["kind"] == "zoom":
                zp = QPen(QColor(theme.SELECT), 1.5, Qt.DashLine)
                zp.setCosmetic(True)
                p.setPen(zp)
                p.setBrush(QColor(91, 140, 255, 30))
                p.drawRect(self._drag_rect(c))
            elif c["kind"] in ("pen", "highlighter"):
                hl = c["kind"] == "highlighter"
                col = QColor(self.opts["hl_color" if hl else "pen_color"])
                if hl:
                    col.setAlphaF(0.38)
                p.setPen(QPen(col, self.opts["hl_w" if hl else "pen_w"], Qt.SolidLine,
                              Qt.FlatCap if hl else Qt.RoundCap, Qt.RoundJoin))
                p.drawPath(geometry.smooth_path(c["pts"]))

    def _drag_rect(self, c):
        a, b = c["start"], c["cur"]
        r = QRectF(a, b).normalized()
        if QApplication.keyboardModifiers() & Qt.ShiftModifier:
            m = max(r.width(), r.height())
            r = QRectF(a.x() if b.x() >= a.x() else a.x() - m, a.y() if b.y() >= a.y() else a.y() - m, m, m)
        return r

    def _line_end(self, c):
        a, b = c["start"], c["cur"]
        if QApplication.keyboardModifiers() & Qt.ShiftModifier:
            ang = math.atan2(b.y() - a.y(), b.x() - a.x())
            ang = round(ang / (math.pi / 4)) * (math.pi / 4)
            L = QLineF(a, b).length()
            return QPointF(a.x() + math.cos(ang) * L, a.y() + math.sin(ang) * L)
        return b

    # ------------------------------------------------------------------ rendering
    def render_image(self, width=960, scale_to=None) -> QImage:
        self.stop_text_edit()
        w = int(width)
        h = int(round(width * self.total_h() / self.page_w))
        img = QImage(w, h, QImage.Format_ARGB32_Premultiplied)
        img.fill(QColor(self.bg))
        self.render_into(img, QRectF(0, 0, w, h))
        return img

    def render_into(self, device, target: QRectF, painter=None):
        sel = self.selectedItems()
        self.clearSelection()
        old_md = self.image_max_dim
        if target.width() > 1600:
            self.image_max_dim = 4096
        self.rendering = True
        own = painter is None
        if own:
            painter = QPainter(device)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setRenderHint(QPainter.SmoothPixmapTransform)
        painter.setRenderHint(QPainter.TextAntialiasing)
        self.render(painter, target, self.page_rect(), Qt.KeepAspectRatio)
        if own:
            painter.end()
        self.rendering = False
        self.image_max_dim = old_md
        self._syncing = True
        for it in sel:
            it.setSelected(True)
        self._syncing = False

    # ------------------------------------------------------------------ mouse
    def mousePressEvent(self, e):
        pos = e.scenePos()
        if e.button() == Qt.RightButton:
            self.ctx_pos = QPointF(pos)
            if self.editing is not None and self.editing.contains(self.editing.mapFromScene(pos)):
                return super().mousePressEvent(e)
            self.stop_text_edit()
            under = self._item_at(pos)
            if under is not None and not under.isSelected():
                self.clearSelection()
                under.setSelected(True)
            self.context_requested.emit(e.screenPos(), under)
            e.accept()
            return
        if e.button() != Qt.LeftButton:
            return super().mousePressEvent(e)
        if self.pick is not None:
            self._finish_pick(pos)
            e.accept()
            return
        if self.add_page_rect().contains(pos):
            self.stop_text_edit()
            self.add_page()
            e.accept()
            return
        if self.editing is not None:
            ed = self.editing
            if ed.contains(ed.mapFromScene(pos)):
                return super().mousePressEvent(e)
            self.stop_text_edit()
        tool = self.tool
        self._targets = None
        if self.pe is not None:
            if self.pe_press(pos):
                e.accept()
                return
            self.stop_point_edit()  # clicked away from the corners: back to normal
        if tool == "freeform":
            self.ff_press(pos, e.modifiers())
            e.accept()
            return
        if tool == "gradient":
            part = self.grad_hit(pos)
            if part is not None:
                self._begin_grad(self.grad_target(), part, pos)
            else:
                it = self._item_at(pos)
                self.clearSelection()
                self.grad_item = None
                if it is not None:
                    it.setSelected(True)
                    if not self.start_gradient(it):
                        self.notice.emit("Gradients work on shapes, text boxes and imported drawings")
            self.update()
            e.accept()
            return
        if tool == "select":
            it, h = self.handle_at(pos)
            if h is not None:
                self._begin_transform(it, h, pos)
                e.accept()
                return
            self._moving = True
            self._drag = None
            super().mousePressEvent(e)
            return
        self.clearSelection()
        if tool in ("text", "code"):
            self._create_text(pos, code=(tool == "code"))
            self.tool_reset.emit()
        elif tool.startswith("shape:"):
            pos = self.snap_point(pos)
            self._create = {"kind": "shape", "shape": tool.split(":", 1)[1], "start": pos, "cur": pos}
        elif tool in ("line", "arrow"):
            a = self.anchor_at(pos)
            g1 = {"id": a[0].uid, "u": a[1], "v": a[2]} if a else None
            pos = a[3] if a else self.snap_point(pos)
            self._create = {"kind": tool, "start": pos, "cur": pos, "g1": g1, "g2": None}
        elif tool == "zoom":
            self._create = {"kind": "zoom", "start": pos, "cur": pos}
        elif tool in ("pen", "highlighter"):
            self._create = {"kind": tool, "pts": [(pos.x(), pos.y())]}
        elif tool == "eraser":
            self._erasing = True
            self._erase_at(pos)
        e.accept()

    def mouseMoveEvent(self, e):
        pos = e.scenePos()
        if self.pe is not None and self.pe["drag"] is not None:
            self.pe_move(pos)
            e.accept()
            return
        if self.tool == "freeform" and self.ff is not None:
            self.ff_move(pos, e.buttons())
            e.accept()
            return
        if self._gx is not None:
            self._update_grad(pos, e.modifiers())
            e.accept()
            return
        if self._xf is not None:
            self._update_transform(pos, e.modifiers())
            e.accept()
            return
        c = self._create
        if c is not None:
            if c["kind"] in ("pen", "highlighter"):
                last = c["pts"][-1]
                if math.hypot(pos.x() - last[0], pos.y() - last[1]) * self.view_scale >= 1.5:
                    c["pts"].append((pos.x(), pos.y()))
            elif c["kind"] in ("line", "arrow") and e.modifiers() & Qt.ShiftModifier:
                c["cur"] = pos
                c["g2"] = None
                self.guides = []
                self._anchor_hint = None
            elif c["kind"] in ("line", "arrow"):
                a = self.anchor_at(pos)
                c["g2"] = {"id": a[0].uid, "u": a[1], "v": a[2]} if a else None
                c["cur"] = a[3] if a else self.snap_point(pos)
                if a:
                    self.guides = []
                self._anchor_hint = self._hint(pos)
            elif c["kind"] == "zoom":
                c["cur"] = pos
            else:
                c["cur"] = self.snap_point(pos)
            self.update()
            e.accept()
            return
        if self._erasing:
            self._erase_at(pos)
            e.accept()
            return
        if self.tool in ("line", "arrow") and not e.buttons():
            hint = self._hint(pos)
            if hint != self._anchor_hint:
                self._anchor_hint = hint
                self.update()
        elif self._anchor_hint is not None and self._xf is None:
            self._anchor_hint = None
            self.update()
        super().mouseMoveEvent(e)

    def mouseReleaseEvent(self, e):
        if self.pe is not None and self.pe["drag"] is not None:
            self.pe_release()
            e.accept()
            return
        if self.tool == "freeform":
            self.ff_release(e.scenePos())
            e.accept()
            return
        if self._gx is not None:
            self._end_grad()
            e.accept()
            return
        if self.tool == "gradient":
            e.accept()
            return
        if self._xf is not None:
            x, self._xf = self._xf, None
            if x["handle"] in ("p1", "p2") and "glue" in x:  # sticks to the corner/middle it was dropped on
                setattr(x["item"], "g1" if x["handle"] == "p1" else "g2", x.get("glue"))
            self._end_drag()
            self.push_state()
            e.accept()
            return
        c = self._create
        if c is not None:
            self._create = None
            self._end_drag()
            self._finish_create(c)
            self.update()
            e.accept()
            return
        if self._erasing:
            self._erasing = False
            self.push_state()
            e.accept()
            return
        super().mouseReleaseEvent(e)
        self._moving = False
        self._end_drag()
        self.push_state()

    def mouseDoubleClickEvent(self, e):
        if e.button() != Qt.LeftButton:
            return super().mouseDoubleClickEvent(e)
        pos = e.scenePos()
        if self.tool == "freeform":
            if self.ff is not None:
                pts = self.ff["pts"]  # the second click of the double-click added a corner on top of the last
                if len(pts) >= 2 and QLineF(pts[-1], pts[-2]).length() * max(0.05, self.view_scale) <= 4:
                    pts.pop()
                self.ff_finish(True)
            e.accept()
            return
        if self.tool == "gradient":
            if self.grad_hit(pos) == "c":
                self.recentre_grad(self.grad_target())
            e.accept()
            return
        it = self._item_at(pos)
        if isinstance(it, SlidePath) and self.tool == "select":
            self.start_point_edit(it)
            e.accept()
            return
        if it is None and self.tool == "select" and self.page_containing(pos) is not None:
            # double-clicking empty page starts a text box right there
            self.clearSelection()
            self._create_text(pos)
            e.accept()
            return
        if isinstance(it, TextBearing):
            self.start_text_edit(it, pos)
            e.accept()
            return
        if isinstance(it, SlideImage):
            for v in self.views():
                if hasattr(v, "request_crop"):
                    v.request_crop(it)
            e.accept()
            return
        super().mouseDoubleClickEvent(e)

    # ------------------------------------------------------------------ freeform shapes
    def ff_point(self, pos, mods):
        """Where a click puts the next corner: snapped to edges and centres (Alt: free); Shift keeps the new
        side at 45-degree steps."""
        ff = self.ff
        if ff and ff["pts"] and mods & Qt.ShiftModifier:
            a = ff["pts"][-1]
            dx, dy = pos.x() - a.x(), pos.y() - a.y()
            ang = round(math.atan2(dy, dx) / (math.pi / 4)) * (math.pi / 4)
            r = math.hypot(dx, dy)
            return QPointF(a.x() + r * math.cos(ang), a.y() + r * math.sin(ang))
        if mods & Qt.AltModifier:
            return QPointF(pos)
        return self.snap_point(pos)

    def ff_press(self, pos, mods=Qt.NoModifier):
        s = max(0.05, self.view_scale)
        ff = self.ff
        if ff is None:
            self.clearSelection()
            self.ff = {"pts": [self.ff_point(pos, mods)], "press": QPointF(pos), "drag": False, "scribble": True,
                       "hover": QPointF(pos), "smooth": False}
            self.update()
            return
        if len(ff["pts"]) >= 3 and QLineF(ff["pts"][0], pos).length() * s <= HIT_PX + 2:
            self.ff_finish(True)  # clicked the first corner again: close the shape
            return
        ff["pts"].append(self.ff_point(pos, mods))
        ff.update(press=QPointF(pos), drag=False, scribble=False)
        self.update()

    def ff_move(self, pos, buttons=Qt.NoButton):
        ff = self.ff
        if ff is None:
            return
        s = max(0.05, self.view_scale)
        if buttons & Qt.LeftButton:
            if not ff["drag"] and QLineF(ff["press"], pos).length() * s > 4:
                ff["drag"], ff["from"] = True, len(ff["pts"]) - 1
            if ff["drag"] and QLineF(ff["pts"][-1], pos).length() * s >= 2:
                ff["pts"].append(QPointF(pos))
        ff["hover"] = QPointF(pos)
        self.update()

    def ff_release(self, pos=None):
        ff = self.ff
        if ff is None or not ff["drag"]:
            return
        s = max(0.05, self.view_scale)
        k = ff.get("from", 0)
        drawn = geometry.rdp([(q.x(), q.y()) for q in ff["pts"][k:]], 1.2 / s)  # keep only the corners that matter
        ff["pts"] = ff["pts"][:k] + [QPointF(x, y) for x, y in drawn]
        ff["drag"] = False
        if ff["scribble"]:  # drawn in one stroke from the start: a freehand shape, closed and smoothed
            ff["smooth"] = True
            self.ff_finish(True)
            return
        self.update()

    def ff_undo_point(self):
        if self.ff is None:
            return
        self.ff["pts"].pop()
        if not self.ff["pts"]:
            self.ff = None
        self.update()

    def ff_finish(self, close):
        """Turns the corners drawn so far into a path: a filled shape when closed (3+ corners), else an open
        line (2+). Returns the new item, or None."""
        ff, self.ff = self.ff, None
        self.guides = []
        self.update()
        if ff is None:
            return None
        s = max(0.05, self.view_scale)
        pts = []
        for q in ff["pts"]:
            if not pts or QLineF(pts[-1], q).length() * s > 1.5:
                pts.append(q)
        if close and len(pts) > 3 and QLineF(pts[0], pts[-1]).length() * s <= 2:
            pts.pop()  # the last corner sits on the first
        if len(pts) < 3:
            close = False
        if len(pts) < 2:
            return None
        segs = [["M", pts[0].x(), pts[0].y()]] + [["L", q.x(), q.y()] for q in pts[1:]]
        if close:
            segs.append(["Z"])
            style = dict(fill=self.opts["shape_fill"], stroke=None, sw=0, cap="round")
        else:
            style = dict(fill=None, stroke=self.opts["line_color"], sw=self.opts["line_w"], cap="round")
        d = SlidePath.from_segments(segs, dash="solid", rule="nonzero", smooth=ff["smooth"], **style)
        it = item_from_dict(d)
        self.add(it)
        self.push_state()
        self.tool_reset.emit()
        return it

    def _draw_freeform(self, p, s):
        ff = self.ff
        pts = list(ff["pts"])
        if not ff["drag"] and ff.get("hover") is not None:
            pts.append(ff["hover"])
        path = QPainterPath(pts[0])
        for q in pts[1:]:
            path.lineTo(q)
        if len(pts) >= 3:
            fill = QColor(self.opts["shape_fill"])
            fill.setAlpha(70)
            p.setPen(Qt.NoPen)
            p.setBrush(fill)
            p.drawPath(path)
        pen = QPen(QColor(theme.SELECT), 1.5)
        pen.setCosmetic(True)
        p.setPen(pen)
        p.setBrush(Qt.NoBrush)
        p.drawPath(path)
        hs = HANDLE_PX / s
        closing = len(ff["pts"]) >= 3 and ff.get("hover") is not None and \
            QLineF(ff["pts"][0], ff["hover"]).length() * s <= HIT_PX + 2
        for k, q in enumerate(ff["pts"]):
            big = k == 0 and closing
            p.setBrush(QColor(theme.SELECT) if big else QColor("#ffffff"))
            r = hs * (0.62 if big else 0.36)
            p.drawEllipse(q, r, r)

    # ------------------------------------------------------------------ editing a path's corners
    def to_path(self, it):
        """Swaps a shape, line or pen stroke for the same drawing as a freeform path (same look, same place), so its
        corners and edges can be edited. A shape's text moves into its own text box, grouped with the outline.
        Returns the path item, or None for things that have no outline to edit."""
        from .items import qpath_to_d
        if isinstance(it, SlidePath):
            return it
        if it.locked or not isinstance(it, (SlideShape, SlideLine, SlideStroke)):
            return None
        base = it.to_dict()
        keep = {k: base[k] for k in ("opacity", "z", "group") if k in base}
        if isinstance(it, SlideShape):
            segs = qpath_to_d(geometry.shape_path(it.shape_kind, QRectF(0, 0, it.w, it.h), it.radius))
            d = dict(type="path", x=it.x(), y=it.y(), w=it.w, h=it.h, w0=it.w, h0=it.h, d=segs,
                     rot=it.rotation(), fill=it.fill, stroke=it.stroke, sw=it.sw if it.stroke else 0,
                     dash=it.dash, cap="butt", rule="nonzero")
            if getattr(it, "grad", None) and it.fill:
                d["grad"] = dict(it.grad)
        elif isinstance(it, SlideLine):
            a, b = it.mapToScene(it.p1), it.mapToScene(it.p2)
            d = SlidePath.from_segments([["M", a.x(), a.y()], ["L", b.x(), b.y()]], fill=None, stroke=it.stroke,
                                        sw=it.sw, dash=it.dash, cap="round", rule="nonzero")
            if it.a1 or it.a2:
                self.notice.emit("Arrowheads aren't kept once a line becomes an editable path")
        else:
            pts = [it.mapToScene(QPointF(u * it.w, v * it.h)) for u, v in it.pts]
            if len(pts) < 2:
                return None
            segs = [["M", pts[0].x(), pts[0].y()]] + [["L", q.x(), q.y()] for q in pts[1:]]
            color = QColor(it.color)
            if it.hl:
                color.setAlphaF(0.38)
            d = SlidePath.from_segments(segs, fill=None, stroke=color.name(QColor.HexArgb if it.hl else QColor.HexRgb),
                                        sw=it.sw, dash="solid", cap="butt" if it.hl else "round", rule="nonzero",
                                        smooth=True)
        d.update(keep, id=short_id())
        text = it.text_content().strip() if isinstance(it, SlideShape) else ""
        if text:  # the words get a text box of their own, centred where they were, moving with the outline
            d["group"] = d.get("group") or short_id()
        self._syncing = True
        self.removeItem(it)
        path = item_from_dict(d)
        self.addItem(path)
        path.renormalize()
        if text:
            box = SlideText({"x": it.x(), "y": it.y(), "w": it.w, "h": 24, "minh": 8, "rot": it.rotation(),
                             "font": it.font_family, "size": it.font_px, "color": it.color,
                             "html": it.text.toHtml(), "z": float(d.get("z", 0)) + 0.5, "group": d["group"]})
            c = box.text.textCursor()
            c.select(QTextCursor.Document)
            fmt = c.blockFormat()
            fmt.setAlignment(Qt.AlignHCenter)
            c.mergeBlockFormat(fmt)
            box.layout_children()
            box.setPos(it.x(), it.y() + (it.h - box.h) / 2)
            self.addItem(box)
        self._syncing = False
        self.push_state()
        return path

    def start_point_edit(self, it):
        if it is not None and not isinstance(it, SlidePath):
            it = self.to_path(it)
        if not isinstance(it, SlidePath) or it.locked or not it.vertex_indices():
            return False
        self.ff = None
        self._syncing = True
        self.clearSelection()
        it.setSelected(True)
        self._syncing = False
        self._prev_sel = {it}
        self.pe = {"item": it, "sel": None, "drag": None}
        self.update()
        return True

    def stop_point_edit(self):
        if self.pe is not None:
            self.pe = None
            self.update()

    def pe_vertex_at(self, pos):
        it, s = self.pe["item"], max(0.05, self.view_scale)
        best = None
        for i in it.vertex_indices():
            dist = QLineF(it.mapToScene(it.vertex(i)), pos).length() * s
            if dist <= HIT_PX and (best is None or dist < best[0]):
                best = (dist, i)
        return best[1] if best else None

    def pe_edge_at(self, pos):
        """(edge index, t, point on it in item coordinates) for the outline under pos, or None."""
        it, s = self.pe["item"], max(0.05, self.view_scale)
        q = it.mapFromScene(pos)
        best = None
        for end, a, c1, c2, b in it.edges():
            steps = 24 if c1 is not None else 1
            prev, pt0 = a, 0.0
            for k in range(1, steps + 1):
                t = k / steps
                if c1 is None:
                    cur = QPointF(a.x() + (b.x() - a.x()) * t, a.y() + (b.y() - a.y()) * t)
                else:
                    u = 1 - t
                    cur = (a * (u * u * u) + c1 * (3 * u * u * t) + c2 * (3 * u * t * t) + b * (t * t * t))
                seg = QLineF(prev, cur)
                L2 = seg.dx() ** 2 + seg.dy() ** 2
                f = 0.0 if L2 < 1e-9 else max(0.0, min(1.0, ((q.x() - prev.x()) * seg.dx() +
                                                            (q.y() - prev.y()) * seg.dy()) / L2))
                foot = QPointF(prev.x() + seg.dx() * f, prev.y() + seg.dy() * f)
                dist = QLineF(foot, q).length() * s
                if best is None or dist < best[0]:
                    best = (dist, end, pt0 + (t - pt0) * f, foot)
                prev, pt0 = cur, t
        if best is None or best[0] > 7:
            return None
        return best[1], best[2], best[3]

    def pe_press(self, pos):
        """True when the click was about the corners (picked one, or added one on an edge)."""
        i = self.pe_vertex_at(pos)
        if i is not None:
            self.pe["sel"] = i
            self.pe["drag"] = {"kind": "corner", "moved": False}
            self.update()
            return True
        hit = self.pe_edge_at(pos)
        if hit is None:
            return False
        end, t, q = hit
        self.pe["drag"] = {"kind": "edge", "moved": False, "end": end, "t": t, "at": q, "press": QPointF(pos)}
        self.update()
        return True

    def pe_move(self, pos):
        pe = self.pe
        if pe is None or pe["drag"] is None:
            return
        it, drag = pe["item"], pe["drag"]
        if drag["kind"] == "edge":
            if not drag["moved"] and QLineF(drag["press"], pos).length() * max(0.05, self.view_scale) < 3:
                return  # still a click
            pe["sel"] = None
            drag["end"] = it.bend_edge(drag["end"], drag["t"], it.mapFromScene(pos))
        elif pe["sel"] is not None:
            it.move_vertex(pe["sel"], it.mapFromScene(pos))
        drag["moved"] = True
        self.update()

    def pe_release(self):
        pe = self.pe
        if pe is None or pe["drag"] is None:
            return
        drag, pe["drag"] = pe["drag"], None
        it = pe["item"]
        if drag["kind"] == "edge" and not drag["moved"]:  # a click on an edge: a new corner right there
            pe["sel"] = it.insert_vertex(drag["end"], drag["at"], drag["t"])
            self.push_state()
        elif drag["moved"]:
            it.renormalize()
            self.push_state()
        self.update()

    def pe_delete(self):
        pe = self.pe
        if pe is None or pe["sel"] is None:
            return False
        if not pe["item"].delete_vertex(pe["sel"]):
            self.notice.emit("A shape needs at least three corners, a line two")
            return False
        pe["sel"] = None
        pe["item"].renormalize()
        self.push_state()
        self.update()
        return True

    def _draw_points(self, p, s):
        it = self.pe["item"]
        if it.scene() is not self:
            self.pe = None
            return
        pen = QPen(QColor(theme.SELECT), 1.2, Qt.DashLine)
        pen.setCosmetic(True)
        p.setPen(pen)
        p.setBrush(Qt.NoBrush)
        for end, a, c1, c2, b in it.edges():
            p.drawLine(it.mapToScene(a), it.mapToScene(b))
        hs = HANDLE_PX / s * 0.8
        solid = QPen(QColor(theme.SELECT), 1.3)
        solid.setCosmetic(True)
        p.setPen(solid)
        for i in it.vertex_indices():
            q = it.mapToScene(it.vertex(i))
            p.setBrush(QColor(theme.SELECT) if i == self.pe["sel"] else QColor("#ffffff"))
            p.drawRect(QRectF(q.x() - hs / 2, q.y() - hs / 2, hs, hs))

    def _item_at(self, pos):
        for it in self.items(pos):
            while it is not None and not isinstance(it, SlideItem):
                it = it.parentItem()
            if it is not None and it.isVisible():
                return it
        return None

    # ------------------------------------------------------------------ transforms
    def _begin_transform(self, it, h, pos):
        if isinstance(it, SlideZoom) and (h == "area" or h in AREA_HANDLES):
            self._xf = {"item": it, "handle": h, "start": pos, "area0": list(it.area)}
            return
        if it is GROUP:
            members = self.selected()
            self._xf = {"item": GROUP, "handle": h, "box0": self._union(members),
                        "members": [(m, self._box(m), QPointF(m.pos()), m.w, m.h,
                                     QPointF(m.mapToScene(m.p1)) if isinstance(m, SlideLine) else None,
                                     QPointF(m.mapToScene(m.p2)) if isinstance(m, SlideLine) else None)
                                    for m in members]}
            return
        self._xf = {"item": it, "handle": h, "start": pos, "w0": it.w, "h0": it.h, "t0": it.sceneTransform(),
                    "inv": it.sceneTransform().inverted()[0], "rot0": it.rotation(),
                    "center": it.mapToScene(QPointF(it.w / 2, it.h / 2))}

    def _resize_box(self, x0, y0, x1, y1, w0, h0, h, keep, origin, exclude):
        """Applies a handle drag to a box (in the box's own frame), snapping the moving edges to guides."""
        m = 8.0
        corner = len(h) == 2
        ox, oy = origin.x(), origin.y()
        if not (corner and keep):
            # snap whichever edges this handle moves
            xs = [ox + x0] if "l" in h else [ox + x1] if "r" in h else []
            ys = [oy + y0] if "t" in h else [oy + y1] if "b" in h else []
            dx, dy = self._snap_rect(QRectF(ox + x0, oy + y0, x1 - x0, y1 - y0), exclude, xs, ys) \
                if (xs or ys) else (0.0, 0.0)
            if "l" in h:
                x0 = min(x0 + dx, x1 - m)
            elif "r" in h:
                x1 = max(x1 + dx, x0 + m)
            if "t" in h:
                y0 = min(y0 + dy, y1 - m)
            elif "b" in h:
                y1 = max(y1 + dy, y0 + m)
            return x0, y0, x1, y1
        s = max((x1 - x0) / w0, (y1 - y0) / h0)
        nw, nh = w0 * s, h0 * s
        x0, x1 = (x1 - nw, x1) if "l" in h else (x0, x0 + nw)
        y0, y1 = (y1 - nh, y1) if "t" in h else (y0, y0 + nh)
        # keep the proportions but still let the moving vertical (or else horizontal) edge snap
        ex = ox + (x0 if "l" in h else x1)
        dx, _ = self._snap_rect(QRectF(ox + x0, oy + y0, nw, nh), exclude, [ex], [])
        if dx:
            nw = max(m, nw - dx if "l" in h else nw + dx)
        else:
            ey = oy + (y0 if "t" in h else y1)
            _, dy = self._snap_rect(QRectF(ox + x0, oy + y0, nw, nh), exclude, [], [ey])
            if dy:
                nw = max(m, (nh - dy if "t" in h else nh + dy) * w0 / h0)
        nh = nw * h0 / w0
        x0, x1 = (x1 - nw, x1) if "l" in h else (x0, x0 + nw)
        y0, y1 = (y1 - nh, y1) if "t" in h else (y0, y0 + nh)
        return x0, y0, x1, y1

    def _update_transform(self, pos, mods):
        x = self._xf
        it, h = x["item"], x["handle"]
        if it is GROUP:
            self._update_group_transform(pos, mods)
            return
        if "area0" in x:
            self._update_zoom_area(pos)
            return
        if h == "rot":
            c = x["center"]
            ang = math.degrees(math.atan2(pos.y() - c.y(), pos.x() - c.x())) + 90
            if mods & Qt.ShiftModifier:
                ang = round(ang / 15) * 15
            else:
                for snap in (0, 90, 180, 270, 360, -90):
                    if abs(ang - snap) < 3:
                        ang = snap
            it.setRotation(ang % 360)
            self.update()
            return
        if h in ("p1", "p2"):
            key = "g1" if h == "p1" else "g2"
            g = getattr(it, key)
            anchor = self.glue_point(g) if g and mods & Qt.ControlModifier else None
            if anchor is not None:  # Ctrl-drag a stuck end: stays stuck, but stops this far short of its spot
                gap = QLineF(anchor, pos).length()
                x["glue"] = dict(g, gap=round(gap, 1) if gap * self.view_scale >= 4 else 0)
                setattr(it, key, x["glue"])
                self._follow(it)
                self.guides = []
                self._anchor_hint = None
                self.update()
                return
            lp = it.mapFromScene(pos)
            other = it.p2 if h == "p1" else it.p1
            x["glue"] = None
            if mods & Qt.ShiftModifier:
                ang = math.atan2(lp.y() - other.y(), lp.x() - other.x())
                ang = round(ang / (math.pi / 4)) * (math.pi / 4)
                L = QLineF(other, lp).length()
                lp = QPointF(other.x() + math.cos(ang) * L, other.y() + math.sin(ang) * L)
                self.guides = []
                self._anchor_hint = None
            else:
                a = self.anchor_at(pos, (it,)) if it.stick else None
                if a:  # onto a corner / edge middle / centre: it will stick there
                    lp = it.mapFromScene(a[3])
                    x["glue"] = {"id": a[0].uid, "u": a[1], "v": a[2]}
                    self.guides = []
                else:
                    lp = it.mapFromScene(self.snap_point(pos, (it,)))
                self._anchor_hint = self._hint(pos, (it,)) if it.stick else None
            if h == "p1":
                it.set_points(lp, it.p2)
            else:
                it.set_points(it.p1, lp)
            og = getattr(it, "g2" if h == "p1" else "g1")
            spot = self.glue_point(og) if og and og.get("gap") else None
            if spot is not None:  # the other end keeps aiming at its spot from the new angle
                tip = it.mapFromScene(self._stand_off(spot, it.mapToScene(lp), og))
                it.set_points(lp, tip) if h == "p1" else it.set_points(tip, lp)
            self.update()
            return
        lp = x["inv"].map(pos)
        w0, h0 = x["w0"], x["h0"]
        x0, y0, x1, y1 = 0.0, 0.0, w0, h0
        m = 8.0
        if "l" in h:
            x0 = min(lp.x(), x1 - m)
        if "r" in h:
            x1 = max(lp.x(), x0 + m)
        if "t" in h:
            y0 = min(lp.y(), y1 - m)
        if "b" in h:
            y1 = max(lp.y(), y0 + m)
        keep = isinstance(it, SlideZoom) or it.KEEP_ASPECT != bool(mods & Qt.ShiftModifier)
        upright = abs(x["rot0"] % 360) < 0.01
        if upright:
            x0, y0, x1, y1 = self._resize_box(x0, y0, x1, y1, w0, h0, h, keep and w0 > 0 and h0 > 0,
                                              x["t0"].map(QPointF(0, 0)), (it,))
        elif len(h) == 2 and keep and w0 > 0 and h0 > 0:
            s = max((x1 - x0) / w0, (y1 - y0) / h0)
            nw, nh = w0 * s, h0 * s
            x0, x1 = (x1 - nw, x1) if "l" in h else (x0, x0 + nw)
            y0, y1 = (y1 - nh, y1) if "t" in h else (y0, y0 + nh)
        nw, nh = x1 - x0, y1 - y0
        center_local = QPointF((x0 + x1) / 2, (y0 + y1) / 2)
        c_scene = x["t0"].map(center_local)
        it.set_size(nw, nh)
        it.setPos(c_scene - QPointF(it.w / 2, it.h / 2))
        self.update()

    def _update_zoom_area(self, pos):
        """Moves or resizes the magnified part of the picture (the outline drawn on it)."""
        x = self._xf
        it, h = x["item"], x["handle"]
        img = it.source()
        if img is None:
            return
        u0, v0, u1, v1 = x["area0"]
        lp, sp = img.mapFromScene(pos), img.mapFromScene(x["start"])
        if h == "area":
            du = max(-u0, min(1 - u1, (lp.x() - sp.x()) / img.w))
            dv = max(-v0, min(1 - v1, (lp.y() - sp.y()) / img.h))
            it.set_area([u0 + du, v0 + dv, u1 + du, v1 + dv])
        else:
            fu, fv = max(0.0, min(1.0, lp.x() / img.w)), max(0.0, min(1.0, lp.y() / img.h))
            mu, mv = 6 / img.w, 6 / img.h
            if h in ("a_tl", "a_bl"):
                u0 = min(fu, u1 - mu)
            else:
                u1 = max(fu, u0 + mu)
            if h in ("a_tl", "a_tr"):
                v0 = min(fv, v1 - mv)
            else:
                v1 = max(fv, v0 + mv)
            it.set_area([u0, v0, u1, v1])
        self.update()

    def _update_group_transform(self, pos, mods):
        """Scales every member of a group with the group's box."""
        x = self._xf
        h, b0 = x["handle"], x["box0"]
        members = x["members"]
        w0, h0 = max(1e-6, b0.width()), max(1e-6, b0.height())
        lx, ly = pos.x() - b0.x(), pos.y() - b0.y()
        x0, y0, x1, y1 = 0.0, 0.0, w0, h0
        m = 8.0
        if "l" in h:
            x0 = min(lx, x1 - m)
        if "r" in h:
            x1 = max(lx, x0 + m)
        if "t" in h:
            y0 = min(ly, y1 - m)
        if "b" in h:
            y1 = max(ly, y0 + m)
        keep = any(mm[0].KEEP_ASPECT for mm in members) != bool(mods & Qt.ShiftModifier)
        x0, y0, x1, y1 = self._resize_box(x0, y0, x1, y1, w0, h0, h, keep and b0.width() > 1 and b0.height() > 1,
                                          b0.topLeft(), [mm[0] for mm in members])
        sx = (x1 - x0) / w0 if b0.width() > 1 else 1.0
        sy = (y1 - y0) / h0 if b0.height() > 1 else 1.0
        nx, ny = b0.x() + x0, b0.y() + y0

        def mp(pt):
            return QPointF(nx + (pt.x() - b0.x()) * sx, ny + (pt.y() - b0.y()) * sy)

        for it, box, pos0, iw, ih, p1, p2 in members:
            if isinstance(it, SlideLine):
                a, b = mp(p1), mp(p2)
                it.set_points(a - it.pos(), b - it.pos())
                continue
            r = it.rotation() % 180
            ksx, ksy = (sy, sx) if 45 <= r < 135 else (sx, sy)
            c = mp(box.center())
            it.set_size(iw * ksx, ih * ksy)
            it.setPos(c - QPointF(it.w / 2, it.h / 2))
        self.update()

    # ------------------------------------------------------------------ creation
    def _create_text(self, pos, code=False, text=None):
        o = self.opts
        if code:
            d = {"x": pos.x(), "y": pos.y(), "w": 560, "h": 60, "font": "Consolas", "size": 18,
                 "color": "#d4d4d4", "fill": "#1e1f24", "radius": 8, "pad": 14, "code": True}
        else:
            d = {"x": pos.x(), "y": pos.y() - o["text_px"] * 0.7, "w": 420, "h": 30, "font": o["font"],
                 "size": o["text_px"], "color": o["text_color"]}
        if text:
            d["plain"] = text
        it = SlideText(d)
        self.add(it)
        if text is None:
            self.start_text_edit(it)
        else:
            self.push_state()
        return it

    def _finish_create(self, c):
        k = c["kind"]
        if k == "shape":
            r = self._drag_rect(c)
            if r.width() * self.view_scale < 6 and r.height() * self.view_scale < 6:
                r = QRectF(c["start"].x() - 110, c["start"].y() - 70, 220, 140)
            fill = self.opts["shape_fill"]
            dark = QColor(fill).lightness() < 150
            it = SlideShape({"x": r.x(), "y": r.y(), "w": max(10, r.width()), "h": max(10, r.height()),
                             "shape": c["shape"], "fill": fill, "color": "#ffffff" if dark else "#1d1f24"})
            self.add(it)
            self.tool_reset.emit()
        elif k in ("line", "arrow"):
            a, b = c["start"], self._line_end(c)
            if QLineF(a, b).length() * self.view_scale < 6:
                b = QPointF(a.x() + 220, a.y())
            shift = QApplication.keyboardModifiers() & Qt.ShiftModifier
            it = SlideLine({"x": a.x(), "y": a.y(), "x1": 0, "y1": 0, "x2": b.x() - a.x(), "y2": b.y() - a.y(),
                            "stroke": self.opts["line_color"], "sw": self.opts["line_w"], "a2": k == "arrow",
                            "g1": c.get("g1"), "g2": None if shift else c.get("g2")})
            self._anchor_hint = None
            self.add(it)
            self.tool_reset.emit()
        elif k == "zoom":
            r = self._drag_rect(c)
            img = self.picture_under(r)
            self.tool_reset.emit()
            if img is None:
                self.notice.emit("Drag over part of a picture to zoom into it")
                return
            self.make_zoom(img, r)
            return
        elif k in ("pen", "highlighter"):
            pts = c["pts"]
            if len(pts) == 1:
                pts = [pts[0], (pts[0][0] + 0.5, pts[0][1] + 0.5)]
            pts = geometry.rdp(pts, 0.6 / max(0.05, self.view_scale))
            hl = k == "highlighter"
            it = SlideStroke.from_points(pts, self.opts["hl_color" if hl else "pen_color"],
                                         self.opts["hl_w" if hl else "pen_w"], hl)
            it.setZValue(self.next_z())
            self.addItem(it)
        self.push_state()

    def _erase_at(self, pos):
        r = 6 / max(0.05, self.view_scale)
        for it in self.items(QRectF(pos.x() - r, pos.y() - r, 2 * r, 2 * r)):
            if isinstance(it, SlideStroke) and not it.locked:
                self.removeItem(it)

    # ------------------------------------------------------------------ commands
    def delete_selected(self):
        if self.editing is not None:
            return
        sel = [i for i in self.selected() if not i.locked]
        gone = {i.uid for i in sel}
        sel += [z for z in self.slide_items() if isinstance(z, SlideZoom) and z.src in gone and z not in sel]
        for it in sel:
            self.removeItem(it)
        for ln in self.slide_items():  # lines stuck to something deleted stay where they are, free
            if isinstance(ln, SlideLine):
                for k in ("g1", "g2"):
                    if (getattr(ln, k) or {}).get("id") in gone:
                        setattr(ln, k, None)
        if sel:
            self.push_state()

    def split_path(self, it):
        """Breaks an imported path into one item per separate part (e.g. each marker of a plot series)."""
        parts = [p for p in it.subpaths() if len(p) > 1]
        if len(parts) < 2 or len(parts) > 3000:
            return []
        sx = it.w / it.w0 if it.w0 > 1e-6 else 1.0
        sy = it.h / it.h0 if it.h0 > 1e-6 else 1.0
        base = it.to_dict()
        style = {k: base[k] for k in ("fill", "stroke", "sw", "dash", "rule", "cap")}
        dicts = []
        for part in parts:
            segs = [[s[0]] + [v * (sx if i % 2 == 0 else sy) for i, v in enumerate(s[1:])] for s in part]
            d = SlidePath.from_segments(segs, **style)  # still in the original item's own coordinates
            centre = it.mapToScene(QPointF(d["x"] + d["w"] / 2, d["y"] + d["h"] / 2))  # rotation included
            d.update(id=short_id(), x=centre.x() - d["w"] / 2, y=centre.y() - d["h"] / 2, rot=base["rot"],
                     opacity=base["opacity"], group=base.get("group"), locked=False, hidden=False)
            dicts.append(d)
        self._syncing = True
        self.clearSelection()
        z0 = it.zValue()
        self.removeItem(it)
        out = []
        for k, d in enumerate(dicts):
            d["z"] = z0 + k / (len(dicts) + 1)  # stays at the same depth among the other items
            new = item_from_dict(d)
            if new is not None:
                self.addItem(new)
                new.setSelected(True)
                out.append(new)
        self._syncing = False
        self._prev_sel = set(self.selected())
        self.selectionChanged.emit()
        self.push_state()
        return out

    def nudge(self, dx, dy):
        sel = [i for i in self.selected() if not i.locked]
        self._carrying = set(sel)
        for it in sel:
            it.moveBy(dx, dy)
        self._carrying = set()
        self._unglue_moved(sel)
        if sel:
            self.push_state()
        return bool(sel)

    def duplicate(self, items=None, offset=24):
        items = items if items is not None else self.selected()
        dicts = [it.to_dict() for it in sorted(items, key=lambda i: i.zValue())]
        return self.paste_dicts(dicts, offset)

    def paste_dicts(self, dicts, offset=24):
        self._syncing = True
        self.clearSelection()
        z = self.next_z()
        out = []
        new_groups = {}
        ids = {d.get("id"): short_id() for d in dicts if d.get("id")}
        for d in dicts:
            d = dict(d)
            d["id"] = ids.get(d.get("id")) or short_id()
            if d.get("group"):  # copies form their own group, apart from the originals
                d["group"] = new_groups.setdefault(d["group"], short_id())
            if d.get("type") == "zoom" and d.get("src") in ids:  # a copied picture keeps its copied zoom
                d["src"] = ids[d["src"]]
            for k in ("g1", "g2"):  # line ends stay stuck only to objects copied along with them
                g = d.get(k)
                d[k] = dict(g, id=ids[g["id"]]) if g and g.get("id") in ids else None
            d["x"] = d.get("x", 0) + offset
            d["y"] = d.get("y", 0) + offset
            d["z"] = z
            z += 1
            it = item_from_dict(d)
            if it is not None:
                self.addItem(it)
                it.setSelected(True)
                out.append(it)
        self._syncing = False
        self._prev_sel = set(self.selected())
        self.selectionChanged.emit()
        if out:
            self.push_state()
        return out

    def reorder(self, how):
        items = self.sorted_items()
        sel = [i for i in items if i.isSelected()]
        if not sel:
            return
        rest = [i for i in items if not i.isSelected()]
        if how == "front":
            order = rest + sel
        elif how == "back":
            order = sel + rest
        else:
            order = list(items)
            rng = range(len(order) - 2, -1, -1) if how == "forward" else range(1, len(order))
            for i in rng:
                j = i + 1 if how == "forward" else i - 1
                if order[i].isSelected() and not order[j].isSelected():
                    order[i], order[j] = order[j], order[i]
        for z, it in enumerate(order, 1):
            it.setZValue(z)
        self.push_state()

    def set_order(self, ordered_items):
        for z, it in enumerate(ordered_items, 1):
            it.setZValue(z)
        self.push_state()

    def align(self, how):
        sel = [i for i in self.selected() if not i.locked]
        if not sel:
            return
        # a group lines up as one block
        units, seen = [], set()
        for it in sel:
            if it.group and it.group in self.groups():
                if it.group in seen:
                    continue
                seen.add(it.group)
                units.append([m for m in sel if m.group == it.group])
            else:
                units.append([it])
        boxes = [self._union(u) for u in units]
        if len(units) == 1:
            ref = self.page_rects()[self.page_at(boxes[0].center().y())]
        else:
            ref = QRectF(boxes[0])
            for b in boxes:
                ref = ref.united(b)

        def move(unit, dx, dy):
            self._carrying = set(sel)
            for it in unit:
                it.moveBy(dx, dy)
            self._carrying = set()

        if how in ("dist_h", "dist_v") and len(units) >= 3:
            horiz = how == "dist_h"
            order = sorted(range(len(units)), key=lambda i: boxes[i].left() if horiz else boxes[i].top())
            total = sum(boxes[i].width() if horiz else boxes[i].height() for i in order)
            span = (ref.width() if horiz else ref.height()) - total
            gap = span / (len(order) - 1)
            cur = ref.left() if horiz else ref.top()
            for i in order:
                b = boxes[i]
                if horiz:
                    move(units[i], cur - b.left(), 0)
                    cur += b.width() + gap
                else:
                    move(units[i], 0, cur - b.top())
                    cur += b.height() + gap
        else:
            for unit, b in zip(units, boxes):
                dx = dy = 0.0
                if how == "left":
                    dx = ref.left() - b.left()
                elif how == "hcenter":
                    dx = ref.center().x() - b.center().x()
                elif how == "right":
                    dx = ref.right() - b.right()
                elif how == "top":
                    dy = ref.top() - b.top()
                elif how == "vcenter":
                    dy = ref.center().y() - b.center().y()
                elif how == "bottom":
                    dy = ref.bottom() - b.bottom()
                move(unit, dx, dy)
        self._unglue_moved(sel)
        self.push_state()

    def picture_of(self, items, scale=2.0):
        """A see-through picture of just these items (for apps that can't take them as shapes)."""
        dicts = [it.to_dict() for it in items]
        ids = {d["id"] for d in dicts}
        srcs = {d.get("src") for d in dicts if d.get("type") == "zoom"}
        extra = [dict(o.to_dict(), hidden=True) for o in self.slide_items() if o.uid in srcs and o.uid not in ids]
        sc = SlideScene({"w": self.page_w, "h": self.page_h, "pages": self.pages, "bg": self.bg,
                         "items": dicts + extra})
        sc.transparent = True
        sc.rendering = True
        r = QRectF()
        for it in sc.slide_items():
            if it.isVisible():
                r = r.united(it.sceneBoundingRect())
        img = QImage(max(1, int(r.width() * scale)), max(1, int(r.height() * scale)),
                     QImage.Format_ARGB32_Premultiplied)
        img.fill(Qt.transparent)
        p = QPainter(img)
        p.setRenderHint(QPainter.Antialiasing)
        p.setRenderHint(QPainter.SmoothPixmapTransform)
        p.setRenderHint(QPainter.TextAntialiasing)
        sc.render(p, QRectF(0, 0, img.width(), img.height()), r)
        p.end()
        return img

    def add_image(self, h, iw, ih, name="", at=None):
        """Places an image asset, scaled to fit comfortably on the page in view."""
        maxw, maxh = self.page_w * 0.8, self.page_h * 0.8
        s = min(1.0, maxw / max(1, iw), maxh / max(1, ih))
        w, hh = iw * s, ih * s
        c = self.page_center() if at is None else at
        it = SlideImage({"x": c.x() - w / 2, "y": c.y() - hh / 2, "w": w, "h": hh, "asset": h, "iw": iw, "ih": ih,
                         "name": name})
        self.add(it)
        self.push_state()
        return it

    def keyPressEvent(self, e):
        if self.editing is not None:
            return super().keyPressEvent(e)
        k = e.key()
        step = 10 if e.modifiers() & Qt.ShiftModifier else 1
        if self.ff is not None and k in (Qt.Key_Return, Qt.Key_Enter, Qt.Key_Backspace, Qt.Key_Delete):
            if k in (Qt.Key_Return, Qt.Key_Enter):
                self.ff_finish(True)
            else:
                self.ff_undo_point()
            e.accept()
            return
        if self.pe is not None and k in (Qt.Key_Delete, Qt.Key_Backspace, Qt.Key_Return, Qt.Key_Enter):
            if k in (Qt.Key_Return, Qt.Key_Enter):
                self.stop_point_edit()
            else:
                self.pe_delete()
            e.accept()
            return
        if k in (Qt.Key_Delete, Qt.Key_Backspace):
            self.delete_selected()
        elif k == Qt.Key_Left:
            self.nudge(-step, 0)
        elif k == Qt.Key_Right:
            self.nudge(step, 0)
        elif k == Qt.Key_Up:
            self.nudge(0, -step)
        elif k == Qt.Key_Down:
            self.nudge(0, step)
        elif k in (Qt.Key_Return, Qt.Key_Enter, Qt.Key_F2):
            it = self._single()
            if isinstance(it, TextBearing):
                self.start_text_edit(it, select_all=False)
        elif k == Qt.Key_Escape:
            self.clearSelection()
        else:
            return super().keyPressEvent(e)
        e.accept()
