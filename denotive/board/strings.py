"""The red string: a sagging, labelled connection pinned between two board items."""
import math

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QFontMetricsF, QPainter, QPainterPath, QPainterPathStroker, QPen
from PySide6.QtWidgets import QGraphicsItem, QGraphicsObject

from .. import geometry, theme
from .items import lod_of

STRING_Z_TOP = 1_000_000.0
STRING_Z_BEHIND = -50_000.0
DEFAULT_COLOR = "#d7263d"


def string_pen(color, width, style="solid"):
    pen = QPen(color, width, Qt.DashLine if style == "dashed" else Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin)
    if style == "dashed":
        pen.setDashPattern([3.2, 2.4])
    return pen


def paint_string(p, path, color, width, style, lod, glow=None):
    """Twine look: drop shadow, body, and a thin highlight along the top."""
    c = QColor(color)
    if glow is not None:
        g = QColor(glow)
        g.setAlpha(110)
        p.setPen(QPen(g, width + min(7 / max(lod, 0.05), 30.0), Qt.SolidLine, Qt.RoundCap))
        p.drawPath(path)
    if lod > 0.8:
        p.setPen(string_pen(QColor(0, 0, 0, 70), width + 0.6, style))
        p.drawPath(path.translated(1.6, 2.6))
    p.setPen(string_pen(c, width, style))
    p.drawPath(path)
    if lod > 0.7 and style != "dashed":
        hi = c.lighter(150)
        hi.setAlpha(120)
        p.setPen(QPen(hi, max(0.6, width * 0.28), Qt.SolidLine, Qt.RoundCap))
        p.drawPath(path.translated(-0.35, -0.5))


class StringItem(QGraphicsObject):
    def __init__(self, row, src, dst, on_top=True):
        super().__init__()
        self.row = row
        self.src, self.dst = src, dst
        src.strings.append(self)
        dst.strings.append(self)
        self.path = QPainterPath()
        self.label_rect = QRectF()
        self._br = QRectF()
        self._hover = False
        self.setFlags(QGraphicsItem.ItemIsSelectable | QGraphicsItem.ItemUsesExtendedStyleOption)
        self.setAcceptHoverEvents(True)
        self.setZValue(STRING_Z_TOP if on_top else STRING_Z_BEHIND)
        self.update_path()

    @property
    def id(self):
        return self.row["id"]

    @property
    def data(self):
        return self.row.setdefault("data", {})

    def detach(self):
        for it in (self.src, self.dst):
            if self in it.strings:
                it.strings.remove(self)

    def apply_row(self, row):
        self.row = row
        self.update_path()

    def color(self):
        return QColor(self.data.get("color") or DEFAULT_COLOR)

    def width(self):
        return float(self.data.get("width", 2.6))

    def label_font(self):
        return theme.ui_font(13, QFont.DemiBold)

    def update_path(self):
        self.prepareGeometryChange()
        a, b = self.src.pin_scene(), self.dst.pin_scene()
        self.path = geometry.string_path(a, b, float(self.data.get("slack", 1.0)))
        label = self.row.get("label") or ""
        if label:
            mid = self.path.pointAtPercent(0.5)
            fm = QFontMetricsF(self.label_font())
            lines = label.splitlines() or [""]
            w = min(260.0, max(fm.horizontalAdvance(ln) for ln in lines) + 20)
            h = fm.height() * min(len(lines), 4) + 10
            if fm.horizontalAdvance(label) > 240:
                h = fm.boundingRect(QRectF(0, 0, 240, 1000), Qt.TextWordWrap, label).height() + 10
                h = min(h, fm.height() * 4 + 10)
            self.label_rect = QRectF(mid.x() - w / 2, mid.y() - h / 2, w, h)
        else:
            self.label_rect = QRectF()
        # room for the pins redrawn at both ends (each item caps its own pin size)
        m = 10.0 + 13.0 * max(1.0, self.src.w / 300, self.dst.w / 300)
        br = self.path.controlPointRect().adjusted(-m, -m, m, m + 6)
        if not self.label_rect.isNull():
            br = br.united(self.label_rect.adjusted(-6, -6, 6, 8))
        self._br = br
        self.update()

    def boundingRect(self):
        return self._br

    def shape(self):
        s = QPainterPathStroker()
        s.setWidth(14)
        s.setCapStyle(Qt.RoundCap)
        out = s.createStroke(self.path)
        if not self.label_rect.isNull():
            out.addRoundedRect(self.label_rect, 4, 4)
        return out

    def hoverEnterEvent(self, e):
        self._hover = True
        self.update()

    def hoverLeaveEvent(self, e):
        self._hover = False
        self.update()

    def mouseDoubleClickEvent(self, e):
        sc = self.scene()
        if sc is not None:
            sc.label_edit.emit(self)
        e.accept()

    def paint(self, p, option, widget=None):
        lod = lod_of(p, option)
        # long strings have huge bounding boxes: skip the ones whose curve doesn't cross the exposed area
        vis = option.exposedRect
        if not vis.isNull():
            m = 26.0 / max(lod, 0.05)
            grown = vis.adjusted(-m, -m, m, m)
            if not self.path.intersects(grown) and not grown.intersects(self.label_rect):
                return
        p.setRenderHint(QPainter.Antialiasing)
        glow = theme.SELECT if self.isSelected() else ("#ffffff" if self._hover else None)
        style = self.data.get("style", "solid")
        paint_string(p, self.path, self.color(), self.width(), style, lod, glow)
        # direction arrows
        d = self.data.get("dir", "none")
        if d in ("fwd", "both"):
            self._arrow(p, 0.86, False)
        if d in ("back", "both"):
            self._arrow(p, 0.14, True)
        # re-draw the pin heads on top so the string looks wrapped around them
        for it in (self.src, self.dst):
            if it.w * lod >= 40:
                c = it.pin_scene()
                s = it.pin_size(lod)
                color = it.data.get("pin", "#e23b3b")
                p.drawPixmap(QRectF(c.x() - s / 2, c.y() - s / 2, s, s), theme.pin_pixmap(color, 28),
                             QRectF(0, 0, 84, 84))
        label = self.row.get("label") or ""
        if label and lod > 0.18:
            r = self.label_rect
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(0, 0, 0, 70))
            p.drawRoundedRect(r.translated(1.5, 2.5), 4, 4)
            p.setBrush(QColor("#fbf7e9"))
            p.setPen(QPen(self.color().darker(115), 1.2))
            p.drawRoundedRect(r, 4, 4)
            p.setPen(QColor("#22232a"))
            p.setFont(self.label_font())
            p.drawText(r.adjusted(8, 4, -8, -4), Qt.AlignCenter | Qt.TextWordWrap, label)

    def _arrow(self, p, t, reverse):
        pt = self.path.pointAtPercent(t)
        ang = -math.radians(self.path.angleAtPercent(t))
        if reverse:
            ang += math.pi
        p.setPen(Qt.NoPen)
        p.setBrush(self.color())
        p.drawPolygon(geometry.arrow_head(pt, ang, 9 + self.width() * 2))


class TempString(QGraphicsObject):
    """Rubber-band string shown while dragging from a pin."""

    def __init__(self, a):
        super().__init__()
        self.a = QPointF(a)
        self.b = QPointF(a)
        self.path = QPainterPath(a)
        self.setZValue(STRING_Z_TOP + 1)
        self.setAcceptedMouseButtons(Qt.NoButton)

    def set_end(self, b):
        self.prepareGeometryChange()
        self.b = QPointF(b)
        self.path = geometry.string_path(self.a, self.b)

    def boundingRect(self):
        return self.path.controlPointRect().adjusted(-12, -12, 12, 12)

    def paint(self, p, option, widget=None):
        p.setRenderHint(QPainter.Antialiasing)
        paint_string(p, self.path, QColor(DEFAULT_COLOR), 2.6, "solid", lod_of(p, option))
        p.drawPixmap(QRectF(self.b.x() - 10, self.b.y() - 10, 20, 20), theme.pin_pixmap("#e23b3b", 28),
                     QRectF(0, 0, 84, 84))
