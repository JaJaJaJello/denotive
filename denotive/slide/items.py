"""Objects that live on an evidence slide. Every item round-trips through a plain dict (to_dict / item_from_dict).

Geometry convention: pos() is the top-left of the unrotated box, (w, h) its size, and rotation happens about the
box centre. Font sizes are stored in pixels of the slide (a 1280 px wide slide is 13.33 in, so pt = px * 0.75).
"""
import math
import uuid

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QPainter, QPainterPath, QPainterPathStroker, QPen, QTextDocument, QTextOption
from PySide6.QtWidgets import QGraphicsItem, QGraphicsObject, QGraphicsTextItem

from .. import context, geometry

DASHES = {"solid": Qt.SolidLine, "dash": Qt.DashLine, "dot": Qt.DotLine, "dashdot": Qt.DashDotLine}
DEFAULT_FONT = "Segoe UI"
MONO_FONT = "Consolas"
PAGE_SIZES = {"16:9": (1280, 720), "4:3": (1024, 768), "1:1": (900, 900), "A4 portrait": (794, 1123),
              "Letter portrait": (816, 1056), "Tall note": (900, 1600)}


def short_id():
    return uuid.uuid4().hex[:12]


def make_pen(color, width, dash="solid", cap=Qt.RoundCap):
    if not color or not width or width <= 0:
        return QPen(Qt.NoPen)
    return QPen(QColor(color), width, DASHES.get(dash, Qt.SolidLine), cap, Qt.RoundJoin)


def html_to_plain(html: str) -> str:
    d = QTextDocument()
    d.setHtml(html or "")
    return d.toPlainText()


class RichText(QGraphicsTextItem):
    """Text child of a text box or shape. Only takes the mouse while it is being edited."""

    def __init__(self, owner):
        super().__init__(owner)
        self.owner = owner
        self.setAcceptedMouseButtons(Qt.NoButton)
        self.setAcceptHoverEvents(False)
        # focusable only while being edited, otherwise a click on the box would focus it (dotted outline)
        self.setFlag(QGraphicsItem.ItemIsFocusable, False)
        self.document().setDocumentMargin(2)
        self.document().contentsChanged.connect(owner.on_text_changed)
        self.highlighter = None

    def set_code(self, on):
        from ..widgets.highlight import CodeHighlighter
        if on and self.highlighter is None:
            self.highlighter = CodeHighlighter(self.document(), "py", dark=True)
        elif not on and self.highlighter is not None:
            self.highlighter.setDocument(None)
            self.highlighter = None

    def keyPressEvent(self, e):
        if e.key() == Qt.Key_Escape:
            sc = self.scene()
            if sc is not None and hasattr(sc, "stop_text_edit"):
                sc.stop_text_edit()
                return
        if e.key() == Qt.Key_Tab and self.owner.code:
            self.textCursor().insertText("    ")
            return
        super().keyPressEvent(e)


class SlideItem(QGraphicsObject):
    TYPE = "item"
    MIN = 6.0
    KEEP_ASPECT = False

    def __init__(self, d):
        super().__init__()
        self.uid = d.get("id") or short_id()
        self.w = float(d.get("w", 200))
        self.h = float(d.get("h", 120))
        self.locked = False
        self.setFlags(QGraphicsItem.ItemIsSelectable | QGraphicsItem.ItemIsMovable |
                      QGraphicsItem.ItemSendsGeometryChanges)
        self.setAcceptHoverEvents(True)
        self.setPos(float(d.get("x", 0)), float(d.get("y", 0)))
        self.setTransformOriginPoint(self.w / 2, self.h / 2)
        self.setRotation(float(d.get("rot", 0) or 0))
        self.setOpacity(float(d.get("opacity", 1.0)))
        self.setZValue(float(d.get("z", 0)))
        self.setVisible(not d.get("hidden", False))
        self.set_locked(bool(d.get("locked", False)))

    # -- geometry
    def rect(self):
        return QRectF(0, 0, self.w, self.h)

    def margin(self):
        return 2.0

    def boundingRect(self):
        m = self.margin()
        return self.rect().adjusted(-m, -m, m, m)

    def set_size(self, w, h):
        self.prepareGeometryChange()
        self.w = max(self.MIN, float(w))
        self.h = max(self.MIN, float(h))
        self.layout_children()
        self.setTransformOriginPoint(self.w / 2, self.h / 2)
        self.update()

    def layout_children(self):
        pass

    def set_locked(self, v):
        self.locked = v
        self.setFlag(QGraphicsItem.ItemIsMovable, not v)

    # -- serialisation
    def base(self):
        return {"id": self.uid, "type": self.TYPE, "x": round(self.x(), 2), "y": round(self.y(), 2),
                "w": round(self.w, 2), "h": round(self.h, 2), "rot": round(self.rotation(), 3),
                "opacity": round(self.opacity(), 3), "z": self.zValue(), "locked": self.locked,
                "hidden": not self.isVisible()}

    def to_dict(self):
        return self.base()

    def text_content(self):
        return ""

    def label(self):
        return self.TYPE.title()

    def itemChange(self, change, value):
        if change == QGraphicsItem.ItemPositionChange:
            sc = self.scene()
            if sc is not None and hasattr(sc, "snap_position"):
                return sc.snap_position(self, value)
        return super().itemChange(change, value)

    def paint_shadow(self, p, path):
        p.save()
        p.setPen(Qt.NoPen)
        for i, a in ((1, 34), (3, 22), (6, 12)):
            p.setBrush(QColor(0, 0, 0, a))
            p.drawPath(path.translated(i * 0.6, i))
        p.restore()

    def on_text_changed(self):
        pass


class TextBearing(SlideItem):
    """Shared behaviour for items that carry a RichText child."""
    code = False

    def init_text(self, d, default_color, default_px, center=False):
        self.color = d.get("color", default_color)
        self.font_family = d.get("font", DEFAULT_FONT)
        self.font_px = float(d.get("size", default_px))
        self.text = RichText(self)
        self.text.setDefaultTextColor(QColor(self.color))
        f = QFont(self.font_family)
        f.setPixelSize(int(round(self.font_px)))
        self.text.document().setDefaultFont(f)
        if center:
            opt = QTextOption(Qt.AlignHCenter)
            opt.setWrapMode(QTextOption.WrapAtWordBoundaryOrAnywhere)
            self.text.document().setDefaultTextOption(opt)
        else:
            opt = QTextOption()
            opt.setWrapMode(QTextOption.WrapAtWordBoundaryOrAnywhere)
            self.text.document().setDefaultTextOption(opt)
        self._loading = True
        if d.get("html"):
            self.text.setHtml(d["html"])
        elif d.get("plain"):
            self.text.setPlainText(d["plain"])
        self._loading = False

    def set_text_color(self, c):
        self.color = c
        self.text.setDefaultTextColor(QColor(c))

    def set_default_font(self, family=None, px=None):
        if family:
            self.font_family = family
        if px:
            self.font_px = float(px)
        f = QFont(self.font_family)
        f.setPixelSize(int(round(self.font_px)))
        self.text.document().setDefaultFont(f)
        self.layout_children()

    def text_dict(self):
        return {"html": self.text.toHtml() if self.text.document().characterCount() > 1 else "",
                "color": self.color, "font": self.font_family, "size": self.font_px}

    def text_content(self):
        return self.text.toPlainText()

    def on_text_changed(self):
        if getattr(self, "_loading", False):
            return
        self.layout_children()
        self.update()
        sc = self.scene()
        if sc is not None:
            sc.update()


class SlideText(TextBearing):
    TYPE = "text"

    def __init__(self, d):
        super().__init__(d)
        self.fill = d.get("fill")
        self.stroke = d.get("stroke")
        self.sw = float(d.get("sw", 0))
        self.radius = float(d.get("radius", 0))
        self.pad = float(d.get("pad", 6))
        self.min_h = float(d.get("minh", 24))
        self.code = bool(d.get("code", False))
        self.init_text(d, "#1d1f24", 24)
        if self.code:
            self.text.set_code(True)
        self.layout_children()

    def label(self):
        t = self.text_content().strip().splitlines()
        return ("Code: " if self.code else "Text: ") + (t[0][:28] if t else "(empty)")

    def set_size(self, w, h):
        self.min_h = max(12.0, float(h))
        super().set_size(w, h)

    def layout_children(self):
        if not hasattr(self, "text"):
            return
        self.text.setTextWidth(max(10.0, self.w - 2 * self.pad))
        self.text.setPos(self.pad, self.pad)
        doc_h = self.text.boundingRect().height()
        new_h = max(self.min_h, doc_h + 2 * self.pad)
        if abs(new_h - self.h) > 0.01:
            self.prepareGeometryChange()
            self.h = new_h
            self.setTransformOriginPoint(self.w / 2, self.h / 2)

    def paint(self, p, opt, widget=None):
        r = self.rect()
        sc = self.scene()
        rendering = sc is None or getattr(sc, "rendering", True)
        if self.fill or (self.stroke and self.sw > 0):
            p.setRenderHint(QPainter.Antialiasing)
            p.setPen(make_pen(self.stroke, self.sw))
            p.setBrush(QColor(self.fill) if self.fill else Qt.NoBrush)
            inset = self.sw / 2 if self.stroke else 0
            p.drawRoundedRect(r.adjusted(inset, inset, -inset, -inset), self.radius, self.radius)
        if not rendering:
            editing = getattr(sc, "editing", None) is self
            empty = self.text.document().isEmpty()
            if editing or (empty and not self.fill):
                pen = QPen(QColor(91, 140, 255, 150) if editing else QColor(140, 145, 160, 140), 1, Qt.DashLine)
                pen.setCosmetic(True)
                p.setPen(pen)
                p.setBrush(Qt.NoBrush)
                p.drawRect(r)
            if empty and not editing:
                p.setPen(QColor(140, 145, 160))
                f = QFont(DEFAULT_FONT)
                f.setPixelSize(int(max(12, min(self.font_px, 28))))
                p.setFont(f)
                p.drawText(r.adjusted(self.pad + 2, self.pad, 0, 0), Qt.AlignLeft | Qt.AlignTop,
                           "Code…" if self.code else "Type something…")

    def to_dict(self):
        d = self.base()
        d.update(self.text_dict())
        d.update(fill=self.fill, stroke=self.stroke, sw=self.sw, radius=self.radius, pad=self.pad,
                 minh=self.min_h, code=self.code)
        return d


class SlideShape(TextBearing):
    TYPE = "shape"

    def __init__(self, d):
        super().__init__(d)
        self.shape_kind = d.get("shape", "rect")
        self.fill = d.get("fill", "#5b8cff")
        self.stroke = d.get("stroke")
        self.sw = float(d.get("sw", 0))
        self.dash = d.get("dash", "solid")
        self.radius = float(d.get("radius", 16))
        self.shadow = bool(d.get("shadow", False))
        self.init_text(d, d.get("color", "#ffffff"), 22, center=True)
        self.layout_children()

    def label(self):
        t = self.text_content().strip().splitlines()
        return geometry.SHAPE_NAMES.get(self.shape_kind, "Shape") + (f": {t[0][:22]}" if t else "")

    def path(self):
        inset = self.sw / 2 if (self.stroke and self.sw) else 0
        return geometry.shape_path(self.shape_kind, self.rect().adjusted(inset, inset, -inset, -inset), self.radius)

    def margin(self):
        return self.sw + (8 if self.shadow else 2)

    def shape(self):
        p = self.path()
        if not self.fill:
            s = QPainterPathStroker()
            s.setWidth(max(8.0, self.sw + 6))
            return s.createStroke(p)
        return p

    def layout_children(self):
        if not hasattr(self, "text"):
            return
        tr = geometry.shape_text_rect(self.shape_kind, self.rect())
        self.text.setTextWidth(max(10.0, tr.width()))
        doc_h = self.text.boundingRect().height()
        self.text.setPos(tr.x(), tr.y() + max(0.0, (tr.height() - doc_h) / 2))

    def paint(self, p, opt, widget=None):
        p.setRenderHint(QPainter.Antialiasing)
        path = self.path()
        if self.shadow:
            self.paint_shadow(p, path)
        p.setPen(make_pen(self.stroke, self.sw, self.dash))
        p.setBrush(QColor(self.fill) if self.fill else Qt.NoBrush)
        p.drawPath(path)

    def to_dict(self):
        d = self.base()
        d.update(self.text_dict())
        d.update(shape=self.shape_kind, fill=self.fill, stroke=self.stroke, sw=self.sw, dash=self.dash,
                 radius=self.radius, shadow=self.shadow)
        return d


class SlideImage(SlideItem):
    TYPE = "image"
    KEEP_ASPECT = True

    def __init__(self, d):
        super().__init__(d)
        self.asset = d.get("asset")
        self.iw = float(d.get("iw", self.w))
        self.ih = float(d.get("ih", self.h))
        self.crop = list(d.get("crop") or [0, 0, self.iw, self.ih])
        self.radius = float(d.get("radius", 0))
        self.stroke = d.get("stroke")
        self.sw = float(d.get("sw", 0))
        self.shadow = bool(d.get("shadow", False))
        self.name = d.get("name", "")

    def label(self):
        return "Image" + (f": {self.name[:24]}" if self.name else "")

    def margin(self):
        return self.sw + (8 if self.shadow else 2)

    def pixmap(self):
        sc = self.scene()
        md = getattr(sc, "image_max_dim", 2560)
        return context.images.get_sync(self.asset, md) if context.images else None

    def paint(self, p, opt, widget=None):
        r = self.rect()
        p.setRenderHint(QPainter.Antialiasing)
        p.setRenderHint(QPainter.SmoothPixmapTransform)
        path = QPainterPath()
        path.addRoundedRect(r, self.radius, self.radius)
        if self.shadow:
            self.paint_shadow(p, path)
        pm = self.pixmap()
        p.save()
        if self.radius > 0:
            p.setClipPath(path)
        if pm is None or pm.isNull():
            p.fillRect(r, QColor("#d9dbe0"))
            p.setPen(QColor("#7a7f8a"))
            p.drawText(r, Qt.AlignCenter, "Missing image")
        else:
            k = pm.width() / max(1.0, self.iw)
            cx, cy, cw, ch = self.crop
            p.drawPixmap(r, pm, QRectF(cx * k, cy * k, cw * k, ch * k))
        p.restore()
        if self.stroke and self.sw > 0:
            p.setPen(make_pen(self.stroke, self.sw))
            p.setBrush(Qt.NoBrush)
            i = self.sw / 2
            p.drawRoundedRect(r.adjusted(i, i, -i, -i), self.radius, self.radius)

    def set_crop(self, crop):
        """Applies a new crop rectangle (image pixels) keeping the on-slide scale and the visible region in place."""
        old = self.crop
        scale = self.w / max(1e-6, old[2])
        nx = self.x() + (crop[0] - old[0]) * scale
        ny = self.y() + (crop[1] - old[1]) * scale
        self.crop = [float(v) for v in crop]
        self.set_size(crop[2] * scale, crop[3] * scale)
        self.setPos(nx, ny)

    def to_dict(self):
        d = self.base()
        d.update(asset=self.asset, iw=self.iw, ih=self.ih, crop=[round(v, 2) for v in self.crop], radius=self.radius,
                 stroke=self.stroke, sw=self.sw, shadow=self.shadow, name=self.name)
        return d


class SlideLine(SlideItem):
    TYPE = "line"

    def __init__(self, d):
        super().__init__(d)
        self.p1 = QPointF(float(d.get("x1", 0)), float(d.get("y1", 0)))
        self.p2 = QPointF(float(d.get("x2", 200)), float(d.get("y2", 0)))
        self.stroke = d.get("stroke", "#1d1f24")
        self.sw = float(d.get("sw", 4))
        self.dash = d.get("dash", "solid")
        self.a1 = bool(d.get("a1", False))
        self.a2 = bool(d.get("a2", False))
        self.setRotation(0)
        self._sync_box()

    def label(self):
        return "Arrow" if (self.a1 or self.a2) else "Line"

    def _sync_box(self):
        r = QRectF(self.p1, self.p2).normalized()
        self.w, self.h = max(1.0, r.width()), max(1.0, r.height())

    def set_points(self, p1, p2):
        self.prepareGeometryChange()
        self.p1, self.p2 = QPointF(p1), QPointF(p2)
        self._sync_box()
        self.update()

    def rect(self):
        return QRectF(self.p1, self.p2).normalized()

    def head(self):
        return max(10.0, self.sw * 3.2)

    def boundingRect(self):
        m = self.sw + self.head() + 4
        return self.rect().adjusted(-m, -m, m, m)

    def shape(self):
        path = QPainterPath(self.p1)
        path.lineTo(self.p2)
        s = QPainterPathStroker()
        s.setWidth(max(12.0, self.sw + 8))
        s.setCapStyle(Qt.RoundCap)
        return s.createStroke(path)

    def set_size(self, w, h):
        r = self.rect()
        sx = w / max(1e-6, r.width()) if r.width() > 1 else 1
        sy = h / max(1e-6, r.height()) if r.height() > 1 else 1
        self.set_points(QPointF(self.p1.x() * sx, self.p1.y() * sy), QPointF(self.p2.x() * sx, self.p2.y() * sy))

    def paint(self, p, opt, widget=None):
        p.setRenderHint(QPainter.Antialiasing)
        a, b = QPointF(self.p1), QPointF(self.p2)
        ang = math.atan2(b.y() - a.y(), b.x() - a.x())
        hs = self.head()
        sa, sb = QPointF(a), QPointF(b)
        if self.a1:
            sa = QPointF(a.x() + math.cos(ang) * hs * 0.7, a.y() + math.sin(ang) * hs * 0.7)
        if self.a2:
            sb = QPointF(b.x() - math.cos(ang) * hs * 0.7, b.y() - math.sin(ang) * hs * 0.7)
        p.setPen(make_pen(self.stroke, self.sw, self.dash))
        p.drawLine(sa, sb)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(self.stroke))
        if self.a2:
            p.drawPolygon(geometry.arrow_head(b, ang, hs))
        if self.a1:
            p.drawPolygon(geometry.arrow_head(a, ang + math.pi, hs))

    def to_dict(self):
        d = self.base()
        d.update(rot=0, x1=round(self.p1.x(), 2), y1=round(self.p1.y(), 2), x2=round(self.p2.x(), 2),
                 y2=round(self.p2.y(), 2), stroke=self.stroke, sw=self.sw, dash=self.dash, a1=self.a1, a2=self.a2)
        return d


class SlideStroke(SlideItem):
    TYPE = "stroke"

    def __init__(self, d):
        super().__init__(d)
        self.pts = d.get("pts", [])
        self.color = d.get("color", "#e5484d")
        self.sw = float(d.get("sw", 4))
        self.hl = bool(d.get("hl", False))
        self._path = None

    def label(self):
        return "Highlighter" if self.hl else "Ink"

    @classmethod
    def from_points(cls, pts, color, sw, hl):
        xs = [x for x, _ in pts]
        ys = [y for _, y in pts]
        x0, y0 = min(xs), min(ys)
        w, h = max(1.0, max(xs) - x0), max(1.0, max(ys) - y0)
        norm = [[round((x - x0) / w, 5), round((y - y0) / h, 5)] for x, y in pts]
        return cls({"x": x0, "y": y0, "w": w, "h": h, "pts": norm, "color": color, "sw": sw, "hl": hl})

    def set_size(self, w, h):
        self._path = None
        super().set_size(w, h)

    def path(self):
        if self._path is None:
            self._path = geometry.smooth_path([(u * self.w, v * self.h) for u, v in self.pts])
        return self._path

    def margin(self):
        return self.sw / 2 + 2

    def shape(self):
        s = QPainterPathStroker()
        s.setWidth(max(10.0, self.sw + 6))
        s.setCapStyle(Qt.RoundCap)
        return s.createStroke(self.path())

    def paint(self, p, opt, widget=None):
        p.setRenderHint(QPainter.Antialiasing)
        c = QColor(self.color)
        if self.hl:
            c.setAlphaF(0.38)
        pen = QPen(c, self.sw, Qt.SolidLine, Qt.FlatCap if self.hl else Qt.RoundCap, Qt.RoundJoin)
        p.setPen(pen)
        p.setBrush(Qt.NoBrush)
        p.drawPath(self.path())

    def to_dict(self):
        d = self.base()
        d.update(pts=self.pts, color=self.color, sw=self.sw, hl=self.hl)
        return d


ITEM_TYPES = {"text": SlideText, "shape": SlideShape, "image": SlideImage, "line": SlideLine, "stroke": SlideStroke}


def item_from_dict(d):
    cls = ITEM_TYPES.get(d.get("type"))
    return cls(d) if cls else None


def doc_plain_text(doc) -> str:
    parts = []
    for d in doc.get("items", []):
        if d.get("html"):
            parts.append(html_to_plain(d["html"]))
        elif d.get("plain"):
            parts.append(d["plain"])
    if doc.get("notes"):
        parts.append(doc["notes"])
    return "\n".join(p for p in parts if p.strip())
