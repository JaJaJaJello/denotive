"""BoardView: pan/zoom, board backgrounds, creation-tool previews, drag-and-drop, and a minimap overlay."""
import math
import random

from PySide6.QtCore import QPoint, QPointF, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QBrush, QColor, QImage, QPainter, QPen, QPixmap, QPolygonF, QTransform
from PySide6.QtWidgets import QGraphicsView, QWidget

from .. import geometry, theme
from ..widgets.common import shift_as_ctrl
from .items import BoardItem, FrameItem, InlineText
from .strings import StringItem

BACKGROUNDS = [("midnight", "Midnight dots"), ("slate", "Slate grid"), ("cork", "Cork board"),
               ("blueprint", "Blueprint"), ("paper", "Paper"), ("plain", "Plain dark")]
BG_COLORS = {"midnight": "#0f1217", "slate": "#15171c", "cork": "#a77b4f", "blueprint": "#0d2746",
             "paper": "#f2f0e9", "plain": "#111318"}
CREATE_TOOLS = ("text", "sticky", "frame", "line")
_cork = None


def cork_tile():
    """Procedural, seamlessly tiling cork texture (generated once)."""
    global _cork
    if _cork is not None:
        return _cork
    n = 384
    img = QImage(n, n, QImage.Format_RGB32)
    img.fill(QColor("#a77b4f"))
    p = QPainter(img)
    p.setRenderHint(QPainter.Antialiasing)
    rnd = random.Random(7)
    p.setPen(Qt.NoPen)
    for _ in range(5200):
        x, y = rnd.uniform(0, n), rnd.uniform(0, n)
        r = rnd.uniform(0.6, 3.4)
        tone = rnd.random()
        if tone < 0.45:
            c = QColor(92, 60, 32, rnd.randint(60, 150))
        elif tone < 0.8:
            c = QColor(196, 152, 104, rnd.randint(60, 140))
        else:
            c = QColor(60, 38, 20, rnd.randint(70, 160))
        p.setBrush(c)
        for ox in (-n, 0, n):
            for oy in (-n, 0, n):
                if -6 < x + ox < n + 6 and -6 < y + oy < n + 6:
                    p.drawEllipse(QPointF(x + ox, y + oy), r * rnd.uniform(0.7, 1.3), r)
    p.end()
    _cork = QPixmap.fromImage(img)
    return _cork


class BoardView(QGraphicsView):
    zoom_changed = Signal(float)
    context_menu = Signal(QPoint, QPointF)
    create_requested = Signal(str, QPointF, QPointF)
    dropped = Signal(object, QPointF)
    viewport_changed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setRenderHints(QPainter.Antialiasing | QPainter.SmoothPixmapTransform | QPainter.TextAntialiasing)
        self.setViewportUpdateMode(QGraphicsView.SmartViewportUpdate)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setFrameShape(QGraphicsView.NoFrame)
        self.setDragMode(QGraphicsView.RubberBandDrag)
        self.setRubberBandSelectionMode(Qt.IntersectsItemShape)
        self.setTransformationAnchor(QGraphicsView.AnchorUnderMouse)
        self.setResizeAnchor(QGraphicsView.AnchorViewCenter)
        self.setCacheMode(QGraphicsView.CacheBackground)
        self.setAcceptDrops(True)
        self.setMouseTracking(True)
        self.setContextMenuPolicy(Qt.NoContextMenu)
        self.background = "midnight"
        self.tool = "select"
        self.wheel_zooms = True
        self._pan = None
        self._right = None
        self._space = False
        self._create = None
        self.horizontalScrollBar().valueChanged.connect(lambda _: self.viewport_changed.emit())
        self.verticalScrollBar().valueChanged.connect(lambda _: self.viewport_changed.emit())

    # ------------------------------------------------------------------ zoom
    def zoom(self):
        return self.transform().m11()

    def set_zoom(self, z, anchor_center=True):
        cur = self.zoom()
        z = max(0.03, min(5.0, z))
        if anchor_center:
            old = self.transformationAnchor()
            self.setTransformationAnchor(QGraphicsView.AnchorViewCenter)
            self.scale(z / cur, z / cur)
            self.setTransformationAnchor(old)
        else:
            self.scale(z / cur, z / cur)
        self.resetCachedContent()
        self.zoom_changed.emit(self.zoom())
        self.viewport_changed.emit()

    def zoom_by(self, f, anchor_center=True):
        self.set_zoom(self.zoom() * f, anchor_center)

    def fit_rect(self, r, max_zoom=1.0):
        if r.isNull() or r.width() < 1:
            self.set_zoom(1.0)
            self.centerOn(0, 0)
            return
        m = 80 / max(self.zoom(), 0.05)
        r = r.adjusted(-m, -m, m, m)
        vw, vh = max(50, self.viewport().width()), max(50, self.viewport().height())
        z = min(vw / r.width(), vh / r.height(), max_zoom)
        self.setTransform(QTransform.fromScale(z, z))
        self.resetCachedContent()
        self.centerOn(r.center())
        self.zoom_changed.emit(self.zoom())
        self.viewport_changed.emit()

    def view_state(self):
        c = self.mapToScene(self.viewport().rect().center())
        return {"cx": round(c.x(), 1), "cy": round(c.y(), 1), "zoom": round(self.zoom(), 4)}

    def restore_state(self, st):
        z = max(0.03, min(5.0, float(st.get("zoom", 1.0))))
        self.setTransform(QTransform.fromScale(z, z))
        self.resetCachedContent()
        self.centerOn(QPointF(st.get("cx", 0), st.get("cy", 0)))
        self.zoom_changed.emit(self.zoom())

    def visible_scene_rect(self):
        return self.mapToScene(self.viewport().rect()).boundingRect()

    def wheelEvent(self, e):
        mods = e.modifiers()
        pd = e.pixelDelta()
        ad = e.angleDelta()
        if mods & Qt.ControlModifier or (self.wheel_zooms and pd.isNull() and not mods & Qt.ShiftModifier):
            dy = ad.y() or ad.x()
            self.zoom_by(1.0018 ** dy, anchor_center=False)
        else:
            dx, dy = (pd.x(), pd.y()) if not pd.isNull() else (ad.x() / 2, ad.y() / 2)
            if mods & Qt.ShiftModifier and dx == 0:
                dx, dy = dy, 0
            self.horizontalScrollBar().setValue(self.horizontalScrollBar().value() - int(dx))
            self.verticalScrollBar().setValue(self.verticalScrollBar().value() - int(dy))
        e.accept()

    # ------------------------------------------------------------------ mouse
    def _editing_text(self):
        sc = self.scene()
        return sc is not None and isinstance(sc.focusItem(), InlineText)

    def set_tool(self, tool):
        self.tool = tool
        self.setDragMode(QGraphicsView.RubberBandDrag if tool == "select" else QGraphicsView.NoDrag)
        cur = {"select": None, "hand": Qt.OpenHandCursor, "string": Qt.CrossCursor, "text": Qt.IBeamCursor}
        c = cur.get(tool, Qt.CrossCursor)
        if c is None:
            self.viewport().unsetCursor()
        else:
            self.viewport().setCursor(c)

    def _begin_pan(self, pos):
        self._pan = QPointF(pos)
        self.viewport().setCursor(Qt.ClosedHandCursor)

    def _end_pan(self):
        self._pan = None
        self.set_tool(self.tool)
        if self._space:
            self.viewport().setCursor(Qt.OpenHandCursor)

    def mousePressEvent(self, e):
        pos = e.position()
        if e.button() == Qt.MiddleButton:
            self._begin_pan(pos)
            e.accept()
            return
        if e.button() == Qt.RightButton:
            self._right = [QPointF(pos), False]
            e.accept()
            return
        if e.button() == Qt.LeftButton:
            if self._space or self.tool == "hand":
                self._begin_pan(pos)
                e.accept()
                return
            sp = self.mapToScene(pos.toPoint())
            if self.tool in CREATE_TOOLS or self.tool.startswith("shape:"):
                over = self.itemAt(pos.toPoint())
                if self.tool == "line" or not isinstance(over, (BoardItem, StringItem)) or \
                        isinstance(over, FrameItem):
                    self.scene().clearSelection()
                    self._create = {"tool": self.tool, "start": sp, "cur": sp}
                    e.accept()
                    return
            if self.tool == "select":
                e = shift_as_ctrl(e)
        super().mousePressEvent(e)

    def mouseMoveEvent(self, e):
        pos = e.position()
        if self._right is not None and not self._right[1]:
            if (pos - self._right[0]).manhattanLength() > 5:
                self._right[1] = True
                self._begin_pan(self._right[0])
        if self._pan is not None:
            d = pos - self._pan
            self._pan = QPointF(pos)
            self.horizontalScrollBar().setValue(self.horizontalScrollBar().value() - round(d.x()))
            self.verticalScrollBar().setValue(self.verticalScrollBar().value() - round(d.y()))
            e.accept()
            return
        if self._create is not None:
            self._create["cur"] = self.mapToScene(pos.toPoint())
            self.viewport().update()
            e.accept()
            return
        super().mouseMoveEvent(e)

    def mouseReleaseEvent(self, e):
        if e.button() == Qt.RightButton and self._right is not None:
            panned = self._right[1]
            self._right = None
            if panned:
                self._end_pan()
            else:
                self.context_menu.emit(e.globalPosition().toPoint(), self.mapToScene(e.position().toPoint()))
            e.accept()
            return
        if self._pan is not None and e.button() in (Qt.MiddleButton, Qt.LeftButton):
            self._end_pan()
            e.accept()
            return
        if self._create is not None and e.button() == Qt.LeftButton:
            c, self._create = self._create, None
            self.viewport().update()
            self.create_requested.emit(c["tool"], c["start"], c["cur"])
            e.accept()
            return
        if e.button() == Qt.LeftButton and self.tool == "select":
            e = shift_as_ctrl(e)
        super().mouseReleaseEvent(e)

    key_handler = None

    def keyPressEvent(self, e):
        if self._editing_text():
            return super().keyPressEvent(e)
        if e.key() == Qt.Key_Space and not e.isAutoRepeat():
            self._space = True
            self.viewport().setCursor(Qt.OpenHandCursor)
            e.accept()
            return
        if self.key_handler is not None and self.key_handler(e):
            e.accept()
            return
        super().keyPressEvent(e)

    def keyReleaseEvent(self, e):
        if e.key() == Qt.Key_Space and not e.isAutoRepeat() and self._space:
            self._space = False
            self.set_tool(self.tool)
            e.accept()
            return
        super().keyReleaseEvent(e)

    def focusOutEvent(self, e):
        self._space = False
        super().focusOutEvent(e)

    # ------------------------------------------------------------------ drag & drop
    def dragEnterEvent(self, e):
        md = e.mimeData()
        if md.hasUrls() or md.hasImage() or md.hasText() or md.hasFormat("application/x-denotive-board-ref"):
            e.acceptProposedAction()
        else:
            super().dragEnterEvent(e)

    def dragMoveEvent(self, e):
        e.acceptProposedAction()

    def dropEvent(self, e):
        self.dropped.emit(e.mimeData(), self.mapToScene(e.position().toPoint()))
        e.acceptProposedAction()

    # ------------------------------------------------------------------ painting
    def drawBackground(self, p, rect):
        bg = self.background
        p.fillRect(rect, QColor(BG_COLORS.get(bg, "#0f1217")))
        z = self.zoom()
        if bg == "cork":
            tile = cork_tile()
            brush = QBrush(tile)
            brush.setTransform(QTransform.fromScale(1.0, 1.0))
            p.fillRect(rect, brush)
            return
        if bg == "plain":
            return
        if bg in ("midnight", "paper"):
            step = 28.0
            while step * z < 14:
                step *= 4
            col = QColor(255, 255, 255, 26) if bg == "midnight" else QColor(0, 0, 0, 40)
            pen = QPen(col, max(1.6, 2.4 * min(1.0, z)) if z > 0.3 else 1.6)
            pen.setCosmetic(True)
            pen.setCapStyle(Qt.RoundCap)
            p.setPen(pen)
            x0 = math.floor(rect.left() / step) * step
            y0 = math.floor(rect.top() / step) * step
            pts = []
            y = y0
            while y < rect.bottom():
                x = x0
                while x < rect.right():
                    pts.append(QPointF(x, y))
                    x += step
                y += step
                if len(pts) > 40000:
                    break
            p.drawPoints(QPolygonF(pts))
            return
        # grid styles
        minor, major = (40.0, 200.0) if bg == "slate" else (24.0, 120.0)
        cmin = QColor(255, 255, 255, 10 if bg == "slate" else 22)
        cmaj = QColor(255, 255, 255, 22 if bg == "slate" else 44)
        for step, col in ((minor, cmin), (major, cmaj)):
            if step * z < 8:
                continue
            pen = QPen(col, 1)
            pen.setCosmetic(True)
            p.setPen(pen)
            x = math.floor(rect.left() / step) * step
            while x < rect.right():
                p.drawLine(QPointF(x, rect.top()), QPointF(x, rect.bottom()))
                x += step
            y = math.floor(rect.top() / step) * step
            while y < rect.bottom():
                p.drawLine(QPointF(rect.left(), y), QPointF(rect.right(), y))
                y += step

    def drawForeground(self, p, rect):
        c = self._create
        if c is None:
            return
        p.setRenderHint(QPainter.Antialiasing)
        pen = QPen(QColor(theme.SELECT), 1.5)
        pen.setCosmetic(True)
        p.setPen(pen)
        p.setBrush(QColor(91, 140, 255, 36))
        tool = c["tool"]
        a, b = c["start"], c["cur"]
        if tool == "line":
            p.drawLine(a, b)
        elif tool in ("frame",) or tool.startswith("shape:"):
            r = QRectF(a, b).normalized()
            if tool == "frame":
                p.drawRoundedRect(r, 16, 16)
            else:
                p.drawPath(geometry.shape_path(tool.split(":", 1)[1], r, 14))


class Minimap(QWidget):
    """Overview of every item on the board; click or drag to jump around."""

    def __init__(self, view, parent):
        super().__init__(parent)
        self.view = view
        self.setFixedSize(220, 150)
        self.setCursor(Qt.PointingHandCursor)
        self._cache = None
        self._world = QRectF()
        self._items, self._strings = [], []
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(120)
        self._timer.timeout.connect(self.rebuild)
        view.viewport_changed.connect(self.update)

    def schedule(self):
        self._timer.start()

    def _mapping(self):
        r = self._world
        if r.isNull():
            return 1.0, QPointF(0, 0)
        s = min((self.width() - 16) / r.width(), (self.height() - 16) / r.height())
        ox = (self.width() - r.width() * s) / 2 - r.left() * s
        oy = (self.height() - r.height() * s) / 2 - r.top() * s
        return s, QPointF(ox, oy)

    def rebuild(self):
        sc = self.view.scene()
        if sc is None:
            return
        world = sc.content_rect()
        vis = self.view.visible_scene_rect()
        world = world.united(vis) if not world.isNull() else vis
        m = max(world.width(), world.height()) * 0.05
        self._world = world.adjusted(-m, -m, m, m)
        self._items = []
        for it in sc.items_by_id.values():
            self._items.append((it.KIND, it.scene_rect(), it.data.get("color") or it.data.get("fill")))
        self._strings = [(s.src.pin_scene(), s.dst.pin_scene(), s.color()) for s in sc.strings_by_id.values()]
        self._render_cache()
        self.update()

    def _render_cache(self):
        """Items and strings are drawn once into a pixmap; paintEvent only adds the viewport box."""
        dpr = self.devicePixelRatioF()
        pm = QPixmap(int(self.width() * dpr), int(self.height() * dpr))
        pm.setDevicePixelRatio(dpr)
        pm.fill(Qt.transparent)
        p = QPainter(pm)
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(QPen(QColor(255, 255, 255, 30), 1))
        p.setBrush(QColor(20, 22, 27, 225))
        p.drawRoundedRect(QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5), 10, 10)
        s, o = self._mapping()

        def mp(r):
            return QRectF(r.left() * s + o.x(), r.top() * s + o.y(), max(1.5, r.width() * s), max(1.5, r.height() * s))
        colors = {"evidence": "#f6f4ee", "file": "#12a594", "sticky": "#fff09a", "note": "#9aa0ad",
                  "shape": "#5b8cff", "line": "#9aa0ad"}
        for kind, r, col in self._items:
            if kind == "frame":
                p.setPen(QPen(QColor(col or "#9aa0ad"), 1))
                p.setBrush(Qt.NoBrush)
            else:
                p.setPen(Qt.NoPen)
                p.setBrush(QColor(col or colors.get(kind, "#cccccc")) if kind in ("sticky", "shape", "evidence")
                           else QColor(colors.get(kind, "#cccccc")))
            p.drawRect(mp(r))
        for a, b, c in self._strings:
            p.setPen(QPen(c, 1))
            p.drawLine(QPointF(a.x() * s + o.x(), a.y() * s + o.y()), QPointF(b.x() * s + o.x(), b.y() * s + o.y()))
        p.end()
        self._cache = pm

    def paintEvent(self, e):
        if self._cache is None:
            return
        vis = self.view.visible_scene_rect()
        if not self._world.contains(vis):
            self._world = self._world.united(vis)
            self._render_cache()
        p = QPainter(self)
        p.drawPixmap(0, 0, self._cache)
        p.setRenderHint(QPainter.Antialiasing)
        s, o = self._mapping()
        r = QRectF(vis.left() * s + o.x(), vis.top() * s + o.y(), max(2.0, vis.width() * s), max(2.0, vis.height() * s))
        p.setClipRect(QRectF(self.rect()).adjusted(1, 1, -1, -1))
        p.setPen(QPen(QColor(theme.ACCENT), 1.5))
        p.setBrush(QColor(229, 72, 77, 30))
        p.drawRect(r)
        p.end()

    def _jump(self, pos):
        s, o = self._mapping()
        self.view.centerOn(QPointF((pos.x() - o.x()) / s, (pos.y() - o.y()) / s))

    def mousePressEvent(self, e):
        self._jump(e.position())

    def mouseMoveEvent(self, e):
        if e.buttons() & Qt.LeftButton:
            self._jump(e.position())
