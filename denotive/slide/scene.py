"""The slide canvas: owns the page, selection handles, smart guides, creation tools and snapshot undo."""
import json
import math

from PySide6.QtCore import QLineF, QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QImage, QPainter, QPen, QTextCursor
from PySide6.QtWidgets import QApplication, QGraphicsScene

from .. import geometry, theme
from .items import (SlideImage, SlideItem, SlideLine, SlideShape, SlideStroke, SlideText, TextBearing,
                    item_from_dict, make_pen)

HANDLE_PX = 9
HIT_PX = 8
ROT_PX = 26
SNAP_PX = 6
SURROUND = QColor("#1b1d22")
HANDLE_NAMES = ("tl", "t", "tr", "r", "br", "b", "bl", "l")
HANDLE_LOCAL = {"tl": (0, 0), "t": (0.5, 0), "tr": (1, 0), "r": (1, 0.5), "br": (1, 1), "b": (0.5, 1),
                "bl": (0, 1), "l": (0, 0.5)}


class SlideScene(QGraphicsScene):
    tool_reset = Signal()
    state_pushed = Signal()
    edit_started = Signal(object)
    edit_stopped = Signal()
    context_requested = Signal(object, object)  # screen QPoint, item or None

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
        self._erasing = False
        self.page_w, self.page_h = 1280.0, 720.0
        self.bg = "#ffffff"
        self.notes = ""
        self.load(doc or {})
        self.selectionChanged.connect(self.update)

    # ------------------------------------------------------------------ document
    def page_rect(self):
        return QRectF(0, 0, self.page_w, self.page_h)

    def load(self, doc, keep_history=False):
        self.editing = None
        for it in list(self.items()):
            if it.parentItem() is None:
                self.removeItem(it)
        self.page_w = float(doc.get("w", 1280))
        self.page_h = float(doc.get("h", 720))
        self.bg = doc.get("bg", "#ffffff")
        self.notes = doc.get("notes", "")
        m = max(self.page_w, self.page_h)
        self.setSceneRect(QRectF(-m, -m, self.page_w + 2 * m, self.page_h + 2 * m))
        for d in doc.get("items", []):
            it = item_from_dict(d)
            if it is not None:
                self.addItem(it)
        if not keep_history:
            self.history, self.hidx = [], -1
            self.push_state()
        self.update()

    def slide_items(self):
        return [it for it in self.items() if isinstance(it, SlideItem)]

    def sorted_items(self):
        return sorted(self.slide_items(), key=lambda i: i.zValue())

    def to_doc(self):
        return {"v": 1, "w": self.page_w, "h": self.page_h, "bg": self.bg, "notes": self.notes,
                "items": [it.to_dict() for it in self.sorted_items()]}

    def plain_text(self):
        parts = [it.text_content() for it in self.sorted_items()]
        if self.notes:
            parts.append(self.notes)
        return "\n".join(p for p in parts if p and p.strip())

    def set_page(self, w, h):
        self.page_w, self.page_h = float(w), float(h)
        m = max(self.page_w, self.page_h)
        self.setSceneRect(QRectF(-m, -m, self.page_w + 2 * m, self.page_h + 2 * m))
        self.update()

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
        sel = {it.uid for it in self.selectedItems() if isinstance(it, SlideItem)}
        d = json.loads(s)
        d["notes"] = self.notes
        self.load(d, keep_history=True)
        for it in self.slide_items():
            if it.uid in sel:
                it.setSelected(True)
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
        self.editing = item
        t = item.text
        t.setTextInteractionFlags(Qt.TextEditorInteraction)
        t.setAcceptedMouseButtons(Qt.LeftButton)
        t.setFlag(t.GraphicsItemFlag.ItemIsFocusable, True)
        t.setFocus(Qt.MouseFocusReason)
        c = t.textCursor()
        if select_all:
            c.select(QTextCursor.Document)
        elif scene_pos is not None:
            pos = t.document().documentLayout().hitTest(t.mapFromScene(scene_pos), Qt.FuzzyHit)
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
            it.update()
        self.push_state()
        self.edit_stopped.emit()

    # ------------------------------------------------------------------ handles
    def _single(self):
        sel = [i for i in self.selectedItems() if isinstance(i, SlideItem)]
        return sel[0] if len(sel) == 1 else None

    def handle_points(self, it):
        if isinstance(it, SlideLine):
            return {"p1": it.mapToScene(it.p1), "p2": it.mapToScene(it.p2)}
        pts = {n: it.mapToScene(QPointF(u * it.w, v * it.h)) for n, (u, v) in HANDLE_LOCAL.items()}
        pts["rot"] = it.mapToScene(QPointF(it.w / 2, -ROT_PX / max(0.05, self.view_scale)))
        if isinstance(it, SlideText):
            for n in ("t", "b"):
                pts.pop(n, None)
        return pts

    def handle_at(self, scene_pos):
        it = self._single()
        if it is None or it.locked or self.editing is not None:
            return None, None
        best, bd = None, HIT_PX / max(0.05, self.view_scale)
        for n, p in self.handle_points(it).items():
            d = QLineF(p, scene_pos).length()
            if d <= bd:
                best, bd = n, d
        return it, best

    def cursor_for(self, it, name):
        if name is None:
            return None
        if name in ("rot",):
            return Qt.CrossCursor
        if name in ("p1", "p2"):
            return Qt.SizeAllCursor
        u, v = HANDLE_LOCAL[name]
        ang = math.degrees(math.atan2(v - 0.5, u - 0.5)) + it.rotation()
        ang = (ang + 360) % 180
        if ang < 22.5 or ang >= 157.5:
            return Qt.SizeHorCursor
        if ang < 67.5:
            return Qt.SizeFDiagCursor
        if ang < 112.5:
            return Qt.SizeVerCursor
        return Qt.SizeBDiagCursor

    # ------------------------------------------------------------------ snapping
    def snap_position(self, item, new_pos):
        if not self._moving or self.rendering:
            return new_pos
        sel = self.selectedItems()
        if len(sel) != 1 or sel[0] is not item or isinstance(item, SlideLine) or \
                QApplication.keyboardModifiers() & Qt.AltModifier:
            self.guides = []
            return new_pos
        d = new_pos - item.pos()
        br = item.sceneBoundingRect().translated(d)
        m = item.margin()
        br = br.adjusted(m, m, -m, -m)
        xs = [br.left(), br.center().x(), br.right()]
        ys = [br.top(), br.center().y(), br.bottom()]
        tx = [0.0, self.page_w / 2, self.page_w]
        ty = [0.0, self.page_h / 2, self.page_h]
        for o in self.slide_items():
            if o is item or not o.isVisible():
                continue
            ob = o.sceneBoundingRect()
            om = o.margin()
            ob = ob.adjusted(om, om, -om, -om)
            tx += [ob.left(), ob.center().x(), ob.right()]
            ty += [ob.top(), ob.center().y(), ob.bottom()]
        thr = SNAP_PX / max(0.05, self.view_scale)
        best_dx, gx = None, None
        for x in xs:
            for t in tx:
                if abs(t - x) <= thr and (best_dx is None or abs(t - x) < abs(best_dx)):
                    best_dx, gx = t - x, t
        best_dy, gy = None, None
        for y in ys:
            for t in ty:
                if abs(t - y) <= thr and (best_dy is None or abs(t - y) < abs(best_dy)):
                    best_dy, gy = t - y, t
        self.guides = []
        out = QPointF(new_pos)
        if best_dx is not None:
            out.setX(out.x() + best_dx)
            self.guides.append(QLineF(gx, -10000, gx, 10000))
        if best_dy is not None:
            out.setY(out.y() + best_dy)
            self.guides.append(QLineF(-10000, gy, 10000, gy))
        self.update()
        return out

    # ------------------------------------------------------------------ painting
    def drawBackground(self, p, rect):
        page = self.page_rect()
        if self.rendering:
            if not self.transparent:
                p.fillRect(page, QColor(self.bg))
            return
        p.fillRect(rect, SURROUND)
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(Qt.NoPen)
        for i, a in ((2, 40), (6, 24), (12, 12)):
            p.setBrush(QColor(0, 0, 0, a))
            p.drawRoundedRect(page.adjusted(-i / 2, -i / 4, i / 2, i), 4, 4)
        p.fillRect(page, QColor(self.bg))

    def drawForeground(self, p, rect):
        if self.rendering:
            return
        s = max(0.05, self.view_scale)
        p.setRenderHint(QPainter.Antialiasing)
        # page edge hint (content outside is clipped on export)
        pen = QPen(QColor(255, 255, 255, 28), 1)
        pen.setCosmetic(True)
        p.setPen(pen)
        p.setBrush(Qt.NoBrush)
        p.drawRect(self.page_rect())
        # guides
        if self.guides:
            gp = QPen(QColor("#ff4fa3"), 1)
            gp.setCosmetic(True)
            p.setPen(gp)
            for g in self.guides:
                p.drawLine(g)
        # selection
        sel = [i for i in self.selectedItems() if isinstance(i, SlideItem)]
        sp = QPen(QColor(theme.SELECT), 1.4)
        sp.setCosmetic(True)
        for it in sel:
            p.setPen(sp)
            p.setBrush(Qt.NoBrush)
            if isinstance(it, SlideLine):
                p.drawPath(it.mapToScene(it.shape()))  # capsule hugging the line/arrow
                continue
            poly = it.mapToScene(it.rect())
            p.drawPolygon(poly)
        single = sel[0] if len(sel) == 1 else None
        if single is not None and self.editing is None:
            hs = HANDLE_PX / s
            pts = self.handle_points(single)
            if "rot" in pts and not single.locked:
                p.setPen(sp)
                p.drawLine(single.mapToScene(QPointF(single.w / 2, 0)), pts["rot"])
            for n, pt in pts.items():
                if single.locked:
                    break
                hp = QPen(QColor(theme.SELECT), 1.3)
                hp.setCosmetic(True)
                p.setPen(hp)
                p.setBrush(QColor("#ffffff"))
                if n == "rot":
                    p.drawEllipse(pt, hs * 0.62, hs * 0.62)
                elif n in ("p1", "p2"):
                    p.drawEllipse(pt, hs * 0.6, hs * 0.6)
                else:
                    p.drawRoundedRect(QRectF(pt.x() - hs / 2, pt.y() - hs / 2, hs, hs), hs * 0.2, hs * 0.2)
            if single.locked:
                p.setFont(theme.icon_font(12 / s))
                p.setPen(QColor(theme.SELECT))
                tl = single.mapToScene(QPointF(single.w, 0))
                p.drawText(QRectF(tl.x() + 2 / s, tl.y() - 16 / s, 16 / s, 16 / s), Qt.AlignCenter, theme.GLYPHS["lock"])
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
        h = int(round(width * self.page_h / self.page_w))
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
        for it in sel:
            it.setSelected(True)

    # ------------------------------------------------------------------ mouse
    def mousePressEvent(self, e):
        pos = e.scenePos()
        if e.button() == Qt.RightButton:
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
        if self.editing is not None:
            ed = self.editing
            if ed.contains(ed.mapFromScene(pos)):
                return super().mousePressEvent(e)
            self.stop_text_edit()
        tool = self.tool
        if tool == "select":
            it, h = self.handle_at(pos)
            if h is not None:
                self._begin_transform(it, h, pos)
                e.accept()
                return
            self._moving = True
            super().mousePressEvent(e)
            return
        self.clearSelection()
        if tool in ("text", "code"):
            self._create_text(pos, code=(tool == "code"))
            self.tool_reset.emit()
        elif tool.startswith("shape:"):
            self._create = {"kind": "shape", "shape": tool.split(":", 1)[1], "start": pos, "cur": pos}
        elif tool in ("line", "arrow"):
            self._create = {"kind": tool, "start": pos, "cur": pos}
        elif tool in ("pen", "highlighter"):
            self._create = {"kind": tool, "pts": [(pos.x(), pos.y())]}
        elif tool == "eraser":
            self._erasing = True
            self._erase_at(pos)
        e.accept()

    def mouseMoveEvent(self, e):
        pos = e.scenePos()
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
            else:
                c["cur"] = pos
            self.update()
            e.accept()
            return
        if self._erasing:
            self._erase_at(pos)
            e.accept()
            return
        super().mouseMoveEvent(e)

    def mouseReleaseEvent(self, e):
        if self._xf is not None:
            self._xf = None
            self.push_state()
            e.accept()
            return
        c = self._create
        if c is not None:
            self._create = None
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
        if self.guides:
            self.guides = []
            self.update()
        self.push_state()

    def mouseDoubleClickEvent(self, e):
        if e.button() != Qt.LeftButton:
            return super().mouseDoubleClickEvent(e)
        pos = e.scenePos()
        it = self._item_at(pos)
        if it is None and self.tool == "select" and self.page_rect().contains(pos):
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

    def _item_at(self, pos):
        for it in self.items(pos):
            while it is not None and not isinstance(it, SlideItem):
                it = it.parentItem()
            if it is not None and it.isVisible():
                return it
        return None

    # ------------------------------------------------------------------ transforms
    def _begin_transform(self, it, h, pos):
        self._xf = {"item": it, "handle": h, "start": pos, "w0": it.w, "h0": it.h, "t0": it.sceneTransform(),
                    "inv": it.sceneTransform().inverted()[0], "rot0": it.rotation(),
                    "center": it.mapToScene(QPointF(it.w / 2, it.h / 2))}

    def _update_transform(self, pos, mods):
        x = self._xf
        it, h = x["item"], x["handle"]
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
            lp = it.mapFromScene(pos)
            other = it.p2 if h == "p1" else it.p1
            if mods & Qt.ShiftModifier:
                ang = math.atan2(lp.y() - other.y(), lp.x() - other.x())
                ang = round(ang / (math.pi / 4)) * (math.pi / 4)
                L = QLineF(other, lp).length()
                lp = QPointF(other.x() + math.cos(ang) * L, other.y() + math.sin(ang) * L)
            if h == "p1":
                it.set_points(lp, it.p2)
            else:
                it.set_points(it.p1, lp)
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
        corner = len(h) == 2
        keep = it.KEEP_ASPECT != bool(mods & Qt.ShiftModifier)
        if corner and keep and w0 > 0 and h0 > 0:
            s = max((x1 - x0) / w0, (y1 - y0) / h0)
            nw, nh = w0 * s, h0 * s
            if "l" in h:
                x0 = x1 - nw
            else:
                x1 = x0 + nw
            if "t" in h:
                y0 = y1 - nh
            else:
                y1 = y0 + nh
        if mods & Qt.AltModifier and not corner:
            pass
        nw, nh = x1 - x0, y1 - y0
        center_local = QPointF((x0 + x1) / 2, (y0 + y1) / 2)
        c_scene = x["t0"].map(center_local)
        it.set_size(nw, nh)
        it.setPos(c_scene - QPointF(it.w / 2, it.h / 2))
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
            it = SlideLine({"x": a.x(), "y": a.y(), "x1": 0, "y1": 0, "x2": b.x() - a.x(), "y2": b.y() - a.y(),
                            "stroke": self.opts["line_color"], "sw": self.opts["line_w"], "a2": k == "arrow"})
            self.add(it)
            self.tool_reset.emit()
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
        sel = [i for i in self.selectedItems() if isinstance(i, SlideItem) and not i.locked]
        for it in sel:
            self.removeItem(it)
        if sel:
            self.push_state()

    def nudge(self, dx, dy):
        sel = [i for i in self.selectedItems() if isinstance(i, SlideItem) and not i.locked]
        for it in sel:
            it.moveBy(dx, dy)
        if sel:
            self.push_state()

    def duplicate(self, items=None, offset=24):
        items = items if items is not None else [i for i in self.selectedItems() if isinstance(i, SlideItem)]
        dicts = [it.to_dict() for it in sorted(items, key=lambda i: i.zValue())]
        return self.paste_dicts(dicts, offset)

    def paste_dicts(self, dicts, offset=24):
        self.clearSelection()
        z = self.next_z()
        out = []
        for d in dicts:
            d = dict(d)
            d.pop("id", None)
            d["x"] = d.get("x", 0) + offset
            d["y"] = d.get("y", 0) + offset
            d["z"] = z
            z += 1
            it = item_from_dict(d)
            if it is not None:
                self.addItem(it)
                it.setSelected(True)
                out.append(it)
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

    def _box(self, it):
        m = it.margin() if not isinstance(it, SlideLine) else 0
        r = it.sceneBoundingRect()
        if isinstance(it, SlideLine):
            return it.mapToScene(it.rect()).boundingRect()
        return r.adjusted(m, m, -m, -m)

    def align(self, how):
        sel = [i for i in self.selectedItems() if isinstance(i, SlideItem) and not i.locked]
        if not sel:
            return
        boxes = {it: self._box(it) for it in sel}
        if len(sel) == 1:
            ref = self.page_rect()
        else:
            ref = QRectF(boxes[sel[0]])
            for b in boxes.values():
                ref = ref.united(b)
        if how in ("dist_h", "dist_v") and len(sel) >= 3:
            horiz = how == "dist_h"
            order = sorted(sel, key=lambda i: boxes[i].left() if horiz else boxes[i].top())
            total = sum(boxes[i].width() if horiz else boxes[i].height() for i in order)
            span = (ref.width() if horiz else ref.height()) - total
            gap = span / (len(order) - 1)
            cur = ref.left() if horiz else ref.top()
            for it in order:
                b = boxes[it]
                if horiz:
                    it.moveBy(cur - b.left(), 0)
                    cur += b.width() + gap
                else:
                    it.moveBy(0, cur - b.top())
                    cur += b.height() + gap
        else:
            for it, b in boxes.items():
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
                it.moveBy(dx, dy)
        self.push_state()

    def add_image(self, h, iw, ih, name="", at=None):
        """Places an image asset, scaled to fit comfortably on the page."""
        maxw, maxh = self.page_w * 0.8, self.page_h * 0.8
        s = min(1.0, maxw / max(1, iw), maxh / max(1, ih))
        w, hh = iw * s, ih * s
        if at is None:
            at = QPointF((self.page_w - w) / 2, (self.page_h - hh) / 2)
        else:
            at = QPointF(at.x() - w / 2, at.y() - hh / 2)
        it = SlideImage({"x": at.x(), "y": at.y(), "w": w, "h": hh, "asset": h, "iw": iw, "ih": ih, "name": name})
        self.add(it)
        self.push_state()
        return it

    def keyPressEvent(self, e):
        if self.editing is not None:
            return super().keyPressEvent(e)
        k = e.key()
        step = 10 if e.modifiers() & Qt.ShiftModifier else 1
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
