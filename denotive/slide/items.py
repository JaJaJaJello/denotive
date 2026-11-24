"""Objects that live on an evidence slide. Every item round-trips through a plain dict (to_dict / item_from_dict).

Geometry convention: pos() is the top-left of the unrotated box, (w, h) its size, and rotation happens about the
box centre. Font sizes are stored in pixels of the slide (a 1280 px wide slide is 13.33 in, so pt = px * 0.75).
"""
import math
import uuid

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import (QAbstractTextDocumentLayout, QBrush, QColor, QFont, QGradient, QLinearGradient, QPainter,
                           QPainterPath, QPainterPathStroker, QPalette, QPen, QTextDocument, QTextOption, QTransform)
from PySide6.QtWidgets import QGraphicsItem, QGraphicsObject, QGraphicsTextItem

from .. import context, geometry

DASHES = {"solid": Qt.SolidLine, "dash": Qt.DashLine, "dot": Qt.DotLine, "dashdot": Qt.DashDotLine}
DEFAULT_FONT = "Segoe UI"
MONO_FONT = "Consolas"
PAGE_SIZES = {"16:9": (1280, 720), "4:3": (1024, 768), "1:1": (900, 900), "A4 portrait": (794, 1123),
              "Letter portrait": (816, 1056), "Tall note": (900, 1600)}
# An evidence can hold several pages stacked top to bottom: page k starts at y = k * (h + PAGE_GAP).
PAGE_GAP = 40.0
GAP_COLOR = "#cdd1d8"


def doc_pages(doc) -> int:
    return max(1, int(doc.get("pages", 1) or 1))


def doc_height(doc) -> float:
    """Height of the whole evidence: every page plus the gaps between them."""
    n = doc_pages(doc)
    return n * float(doc.get("h", 720)) + (n - 1) * PAGE_GAP


def doc_aspect(doc) -> float:
    return doc_height(doc) / float(doc.get("w", 1280))


def _y_extent(d):
    """Top and bottom of an item dict on the page, allowing for rotation."""
    y = float(d.get("y", 0))
    if d.get("type") == "line":
        a, b = float(d.get("y1", 0)), float(d.get("y2", 0))
        return y + min(a, b), y + max(a, b)
    w, h = float(d.get("w", 0)), float(d.get("h", 0))
    t = math.radians(float(d.get("rot", 0) or 0))
    half = (abs(w * math.sin(t)) + abs(h * math.cos(t))) / 2
    cy = y + h / 2
    return cy - half, cy + half


def split_pages(doc):
    """One single-page doc per page, with each item moved onto the page(s) it sits on (for export)."""
    n = doc_pages(doc)
    if n == 1:
        return [doc]
    h = float(doc.get("h", 720))
    step = h + PAGE_GAP
    pages = [dict(doc, pages=1, items=[]) for _ in range(n)]
    for d in doc.get("items", []):
        top, bottom = _y_extent(d)
        hits = [k for k in range(n) if bottom > k * step and top < k * step + h]
        if not hits:  # parked in a gap: it belongs to the nearest page
            hits = [min(n - 1, max(0, int(((top + bottom) / 2 + PAGE_GAP / 2) // step)))]
        for k in hits:
            pages[k]["items"].append(dict(d, y=float(d.get("y", 0)) - k * step))
    by_id = {d.get("id"): d for d in doc.get("items", [])}
    for k, page in enumerate(pages):
        ids = {d.get("id") for d in page["items"]}
        for d in list(page["items"]):
            src = by_id.get(d.get("src")) if d.get("type") == "zoom" else None
            if src is not None and src.get("id") not in ids:  # magnifies a picture on another page
                page["items"].append(dict(src, y=float(src.get("y", 0)) - k * step, hidden=True))
                ids.add(src.get("id"))
    return pages


def short_id():
    return uuid.uuid4().hex[:12]


def make_pen(color, width, dash="solid", cap=Qt.RoundCap):
    if not color or not width or width <= 0:
        return QPen(Qt.NoPen)
    return QPen(QColor(color), width, DASHES.get(dash, Qt.SolidLine), cap, Qt.RoundJoin)


# ---------------------------------------------------------------------- gradient fills
# grad = {"c": [fx, fy]  centre as a fraction of the item's width / height,
#         "ang": degrees  direction of the colour change in the item's own coordinates (0 = left to right,
#                         90 = top to bottom, i.e. clockwise on screen); it turns along with the item,
#         "a", "b":       where the start and end colours sit, as distances from the centre along that
#                         direction, in units of half the box's length measured that way (so -1 and 1 put
#                         them on the box's edges when centred); before "a" it's all c1, after "b" all c2,
#         "c1", "c2":     the start and end colours}
# Everything is relative to the box, so the gradient stretches with the item when it is resized.
def grad_geom(g, w, h):
    """(centre, (dx, dy) unit direction, unit length, start point, end point) in item coordinates. The unit is
    half the box's length along the direction, so stretching the box stretches the gradient with it."""
    cx, cy = float(g["c"][0]) * w, float(g["c"][1]) * h
    t = math.radians(float(g["ang"]))
    dx, dy = math.cos(t), math.sin(t)
    L = max(1.0, (abs(w * dx) + abs(h * dy)) / 2)
    a, b = float(g["a"]) * L, float(g["b"]) * L
    return QPointF(cx, cy), (dx, dy), L, QPointF(cx + a * dx, cy + a * dy), QPointF(cx + b * dx, cy + b * dy)


def default_grad(fill, w, h, ang=90.0):
    """A gradient from the current fill to white (or to a darker shade when the fill is already pale),
    centred, top to bottom, running edge to edge."""
    c1 = QColor(fill or "#5b8cff")
    pale = 0.299 * c1.red() + 0.587 * c1.green() + 0.114 * c1.blue() > 200
    c2 = c1.darker(150) if pale else QColor("#ffffff")
    if c1.alpha() < 255:
        c2.setAlpha(c1.alpha())
    return {"c": [0.5, 0.5], "ang": float(ang), "a": -1.0, "b": 1.0, "c1": _hex(c1), "c2": _hex(c2)}


def _hex(c):
    return c.name(QColor.HexArgb) if c.alpha() < 255 else c.name()


def clean_grad(g):
    """A gradient dict with every key present and sane, or None."""
    if not isinstance(g, dict):
        return None
    try:
        c = g.get("c") or [0.5, 0.5]
        out = {"c": [float(c[0]), float(c[1])], "ang": float(g.get("ang", 90.0)) % 360,
               "a": float(g.get("a", -1.0)), "b": float(g.get("b", 1.0)),
               "c1": str(g.get("c1") or "#5b8cff"), "c2": str(g.get("c2") or "#ffffff")}
    except (TypeError, ValueError, IndexError):
        return None
    if abs(out["b"] - out["a"]) < 1e-4:
        out["b"] = out["a"] + 1e-4
    return out


def grad_brush(g, w, h):
    _, _, _, p1, p2 = grad_geom(g, w, h)
    lg = QLinearGradient(p1, p2)
    lg.setSpread(QGradient.PadSpread)  # solid start colour before the first line, end colour after the second
    lg.setColorAt(0.0, QColor(g["c1"]))
    lg.setColorAt(1.0, QColor(g["c2"]))
    return QBrush(lg)


def grad_color_at(g, t):
    """The colour at fraction t between the start (0) and end (1) lines, padded outside them."""
    t = max(0.0, min(1.0, t))
    a, b = QColor(g["c1"]), QColor(g["c2"])
    mix = QColor(round(a.red() + (b.red() - a.red()) * t), round(a.green() + (b.green() - a.green()) * t),
                 round(a.blue() + (b.blue() - a.blue()) * t), round(a.alpha() + (b.alpha() - a.alpha()) * t))
    return _hex(mix)


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
        self.group = d.get("group") or None  # items sharing a group id select and move together
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
        self._moved()

    def _moved(self):
        """Tells the slide this item's box changed, so zoom callouts and stuck line ends can follow."""
        sc = self.scene()
        if sc is not None and hasattr(sc, "item_geometry_changed"):
            sc.item_geometry_changed(self)

    def layout_children(self):
        pass

    def set_locked(self, v):
        self.locked = v
        self.setFlag(QGraphicsItem.ItemIsMovable, not v)

    # -- serialisation
    def base(self):
        d = {"id": self.uid, "type": self.TYPE, "x": round(self.x(), 2), "y": round(self.y(), 2),
             "w": round(self.w, 2), "h": round(self.h, 2), "rot": round(self.rotation(), 3),
             "opacity": round(self.opacity(), 3), "z": self.zValue(), "locked": self.locked,
             "hidden": not self.isVisible()}
        if self.group:
            d["group"] = self.group
        return d

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
        elif change in (QGraphicsItem.ItemPositionHasChanged, QGraphicsItem.ItemRotationHasChanged):
            self._moved()
        return super().itemChange(change, value)

    def fill_brush(self):
        """The fill: a gradient when the item has one, otherwise its solid colour (or nothing)."""
        if not getattr(self, "fill", None):
            return QBrush(Qt.NoBrush)
        g = getattr(self, "grad", None)
        if g:
            return grad_brush(g, self.w, self.h)
        return QBrush(QColor(self.fill))

    def set_grad(self, g):
        """Turns a gradient on (a dict) or off (None). The solid fill follows the start colour, so an item with a
        gradient always counts as filled."""
        self.grad = clean_grad(g)
        if self.grad:
            self.fill = self.grad["c1"]
        self.update()

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


def column_breaks(doc, n):
    """Splits a laid-out document into n balanced columns, only ever between lines: [(top, bottom), ...]."""
    lay = doc.documentLayout()
    lines = []
    b = doc.begin()
    while b.isValid():
        top = lay.blockBoundingRect(b).top()
        tl = b.layout()
        for i in range(tl.lineCount()):
            ln = tl.lineAt(i)
            lines.append((top + ln.y(), top + ln.y() + ln.height()))
        b = b.next()
    if not lines:
        return [(0.0, doc.size().height())]
    lines.sort()

    def pack(height):
        cols, start, end = [], lines[0][0], lines[0][0]
        for a, z in lines:
            if z - start > height + 0.01 and end > start:
                cols.append((start, end))
                start = a
            end = z
        return cols + [(start, end)]

    lo, hi = max(z - a for a, z in lines), lines[-1][1] - lines[0][0]
    for _ in range(30):  # the shortest column height that fits in n columns
        mid = (lo + hi) / 2
        lo, hi = (lo, mid) if len(pack(mid)) <= n else (mid, hi)
    return pack(hi)


class SlideText(TextBearing):
    TYPE = "text"

    def __init__(self, d):
        super().__init__(d)
        self.fill = d.get("fill")
        self.grad = clean_grad(d.get("grad")) if self.fill else None
        self.stroke = d.get("stroke")
        self.sw = float(d.get("sw", 0))
        self.radius = float(d.get("radius", 0))
        self.pad = float(d.get("pad", 6))
        self.min_h = float(d.get("minh", 24))
        self.code = bool(d.get("code", False))
        self.cols = max(1, int(d.get("cols", 1) or 1))  # text flowing in side-by-side columns
        self.col_gap = float(d.get("colgap", 24))
        self._breaks = None
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

    def columns(self):
        """How many columns to show: one while being typed in, so the cursor works as usual."""
        sc = self.scene()
        editing = sc is not None and getattr(sc, "editing", None) is self
        return 1 if self.code or editing else self.cols

    def doc_point(self, local):
        """Where a point on the box falls in the text, allowing for columns."""
        if not self._breaks:
            return local - QPointF(self.pad, self.pad)
        colw = self.text.textWidth()
        i = int(max(0, min(len(self._breaks) - 1, (local.x() - self.pad) // (colw + self.col_gap))))
        return QPointF(local.x() - self.pad - i * (colw + self.col_gap), self._breaks[i][0] + local.y() - self.pad)

    def layout_children(self):
        if not hasattr(self, "text"):
            return
        n = self.columns()
        width_cols = 1 if self.code else self.cols  # typing keeps the column width, so lines wrap the same
        inner = max(10.0, self.w - 2 * self.pad)
        self.text.setTextWidth(max(10.0, (inner - (width_cols - 1) * self.col_gap) / width_cols))
        self.text.setPos(self.pad, self.pad)
        doc_h = self.text.boundingRect().height()
        if n > 1:
            self._breaks = column_breaks(self.text.document(), n)
            doc_h = max(z - a for a, z in self._breaks) + 4
        else:
            self._breaks = None
        self.text.setVisible(n == 1)  # with columns, paint() draws the text itself
        new_h = max(self.min_h, doc_h + 2 * self.pad)
        if abs(new_h - self.h) > 0.01:
            self.prepareGeometryChange()
            self.h = new_h
            self.setTransformOriginPoint(self.w / 2, self.h / 2)
            self._moved()

    def paint(self, p, opt, widget=None):
        r = self.rect()
        sc = self.scene()
        rendering = sc is None or getattr(sc, "rendering", True)
        if self.fill or (self.stroke and self.sw > 0):
            p.setRenderHint(QPainter.Antialiasing)
            p.setPen(make_pen(self.stroke, self.sw))
            p.setBrush(self.fill_brush())
            inset = self.sw / 2 if self.stroke else 0
            p.drawRoundedRect(r.adjusted(inset, inset, -inset, -inset), self.radius, self.radius)
        if self._breaks:
            ctx = QAbstractTextDocumentLayout.PaintContext()
            ctx.palette.setColor(QPalette.Text, QColor(self.color))
            colw = self.text.textWidth()
            for i, (a, z) in enumerate(self._breaks):
                p.save()
                p.translate(self.pad + i * (colw + self.col_gap), self.pad - a)
                ctx.clip = QRectF(0, a, colw + 2, z - a)
                p.setClipRect(ctx.clip, Qt.IntersectClip)
                self.text.document().documentLayout().draw(p, ctx)
                p.restore()
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
        if self.grad and self.fill:
            d["grad"] = dict(self.grad)
        if self.cols > 1:
            d.update(cols=self.cols, colgap=self.col_gap)
        return d


class SlideShape(TextBearing):
    TYPE = "shape"

    def __init__(self, d):
        super().__init__(d)
        self.shape_kind = d.get("shape", "rect")
        self.fill = d.get("fill", "#5b8cff")
        self.grad = clean_grad(d.get("grad")) if self.fill else None
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
        p.setBrush(self.fill_brush())
        p.drawPath(path)

    def to_dict(self):
        d = self.base()
        d.update(self.text_dict())
        d.update(shape=self.shape_kind, fill=self.fill, stroke=self.stroke, sw=self.sw, dash=self.dash,
                 radius=self.radius, shadow=self.shadow)
        if self.grad and self.fill:
            d["grad"] = dict(self.grad)
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
        # an end stuck to an object: {"id": its uid, "u": x, "v": y as fractions of its box}
        self.g1, self.g2 = d.get("g1") or None, d.get("g2") or None
        self.stick = bool(d.get("stick", True))
        self.setRotation(0)
        self._sync_box()

    def glued_to(self, uid):
        return any(g and g.get("id") == uid for g in (self.g1, self.g2))

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
                 y2=round(self.p2.y(), 2), stroke=self.stroke, sw=self.sw, dash=self.dash, a1=self.a1, a2=self.a2,
                 g1=self.g1, g2=self.g2, stick=self.stick)
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


class SlideZoom(SlideItem):
    """A magnified view of part of a picture: an outline on the picture, the enlarged copy, and lines between.

    area = [u0, v0, u1, v1] is the magnified part as fractions of the picture's box, so it stays on the same
    spot of the picture when the picture is moved, resized or turned. The enlarged copy keeps its proportions."""
    TYPE = "zoom"
    KEEP_ASPECT = True

    def __init__(self, d):
        super().__init__(d)
        self.setRotation(0)
        self.src = d.get("src")
        self.area = [float(v) for v in (d.get("area") or (0.4, 0.4, 0.6, 0.6))]
        self.shape_kind = d.get("shape", "rect")
        self.stroke = d.get("stroke") or "#e5484d"
        self.sw = float(d.get("sw", 3))
        self.lines = bool(d.get("lines", True))
        self._src = None

    def label(self):
        return "Zoom-in"

    def source(self):
        sc = self.scene()
        if sc is None:
            return None
        s = self._src
        if s is None or s.scene() is not sc or s.uid != self.src:
            s = next((i for i in sc.items() if isinstance(i, SlideImage) and i.uid == self.src), None)
            self._src = s
        return s

    def area_local(self, img):
        u0, v0, u1, v1 = self.area
        return QRectF(u0 * img.w, v0 * img.h, (u1 - u0) * img.w, (v1 - v0) * img.h)

    @staticmethod
    def _radius(r):
        return min(r.width(), r.height()) * 0.12

    def lens_path(self, img=None):
        """The outline on the picture, in this item's coordinates."""
        img = img or self.source()
        if img is None:
            return QPainterPath()
        a = self.area_local(img)
        return self.mapFromScene(img.mapToScene(geometry.shape_path(self.shape_kind, a, self._radius(a))))

    def inset_path(self):
        r = self.rect()
        return geometry.shape_path(self.shape_kind, r, self._radius(r))

    def area_aspect(self):
        img = self.source()
        if img is None:
            return self.h / max(1e-6, self.w)
        a = self.area_local(img)
        return a.height() / max(1e-6, a.width())

    def magnification(self):
        img = self.source()
        return self.w / max(1e-6, self.area_local(img).width()) if img is not None else 1.0

    def set_size(self, w, h):
        super().set_size(w, float(w) * self.area_aspect())  # never stretched

    def set_area(self, area):
        self.prepareGeometryChange()
        self.area = [float(v) for v in area]
        self.set_size(self.w, self.h)

    def margin(self):
        return self.sw / 2 + 3

    def boundingRect(self):
        m = self.margin() + 2
        r = self.rect()
        lens = self.lens_path()
        if not lens.isEmpty():
            r = r.united(lens.boundingRect())
        return r.adjusted(-m, -m, m, m)

    def shape(self):
        return self.inset_path()

    def paint(self, p, opt, widget=None):
        p.setRenderHint(QPainter.Antialiasing)
        p.setRenderHint(QPainter.SmoothPixmapTransform)
        r = self.rect()
        inset = self.inset_path()
        img = self.source()
        pen = make_pen(self.stroke, self.sw)
        if img is not None:
            lens = self.lens_path(img)
            if self.lines and not lens.boundingRect().intersects(r):
                p.setPen(make_pen(self.stroke, max(1.0, self.sw * 0.6)))
                for ln in geometry.hull_bridges(lens.toFillPolygon(), inset.toFillPolygon()):
                    p.drawLine(ln)
            p.setPen(pen)
            p.setBrush(Qt.NoBrush)
            p.drawPath(lens)
        p.save()
        p.setClipPath(inset, Qt.IntersectClip)
        sc = self.scene()
        p.fillRect(r, QColor(getattr(sc, "bg", "#ffffff")))
        pm = img.pixmap() if img is not None else None
        if pm is None or pm.isNull():
            p.fillRect(r, QColor("#d9dbe0"))
            p.setPen(QColor("#7a7f8a"))
            p.drawText(r, Qt.AlignCenter, "Picture removed" if img is None else "Missing image")
        else:
            k = pm.width() / max(1.0, img.iw)
            cx, cy, cw, ch = img.crop
            u0, v0, u1, v1 = self.area
            p.drawPixmap(r, pm, QRectF((cx + u0 * cw) * k, (cy + v0 * ch) * k, (u1 - u0) * cw * k,
                                       (v1 - v0) * ch * k))
        p.restore()
        p.setPen(pen)
        p.setBrush(Qt.NoBrush)
        p.drawPath(inset)

    def to_dict(self):
        d = self.base()
        d.update(rot=0, src=self.src, area=[round(v, 5) for v in self.area], shape=self.shape_kind,
                 stroke=self.stroke, sw=self.sw, lines=self.lines)
        return d


CAPS = {"butt": Qt.FlatCap, "round": Qt.RoundCap, "square": Qt.SquareCap}


def qpath_to_d(qp):
    """A QPainterPath as path segments [["M", x, y], ["L", ...], ["C", ...], ["Z"]] (closed parts get "Z")."""
    d, i, n = [], 0, qp.elementCount()
    start = None
    while i < n:
        e = qp.elementAt(i)
        if e.isMoveTo():
            if d and start is not None and d[-1][0] != "Z" and len(d) > 1:
                last = d[-1]
                if abs(last[-2] - start[0]) < 1e-6 and abs(last[-1] - start[1]) < 1e-6:
                    d.append(["Z"])
            d.append(["M", e.x, e.y])
            start = (e.x, e.y)
            i += 1
        elif e.isLineTo():
            d.append(["L", e.x, e.y])
            i += 1
        else:  # a curve: this element and the next two hold its two handles and its end
            c2, end = qp.elementAt(i + 1), qp.elementAt(i + 2)
            d.append(["C", e.x, e.y, c2.x, c2.y, end.x, end.y])
            i += 3
    if d and start is not None and d[-1][0] != "Z" and len(d) > 1:
        last = d[-1]
        if abs(last[-2] - start[0]) < 1e-6 and abs(last[-1] - start[1]) < 1e-6:
            d.append(["Z"])
    out = []
    for k, s in enumerate(d):  # a straight hop back onto the start right before "Z" is what Z draws anyway
        nxt = d[k + 1] if k + 1 < len(d) else None
        if s[0] == "L" and nxt is not None and nxt[0] == "Z":
            m = next(t for t in reversed(out) if t[0] == "M")
            if abs(s[1] - m[1]) < 1e-6 and abs(s[2] - m[2]) < 1e-6:
                continue
        out.append(s)
    return out


def smooth_through(p, pts, closed):
    """Adds a smooth curve passing through every point to QPainterPath p (Catmull-Rom, as cubic Béziers)."""
    n = len(pts)
    p.moveTo(*pts[0])
    if n == 2:
        p.lineTo(*pts[1])
    else:
        for i in range(n if closed else n - 1):
            p0 = pts[(i - 1) % n] if (closed or i > 0) else pts[i]
            p1, p2 = pts[i], pts[(i + 1) % n]
            p3 = pts[(i + 2) % n] if (closed or i + 2 < n) else p2
            p.cubicTo(p1[0] + (p2[0] - p0[0]) / 6, p1[1] + (p2[1] - p0[1]) / 6,
                      p2[0] - (p3[0] - p1[0]) / 6, p2[1] - (p3[1] - p1[1]) / 6, p2[0], p2[1])
    if closed:
        p.closeSubpath()


class SlidePath(SlideItem):
    """A freeform vector drawing: drawn with the Freeform tool, imported from a PDF, or traced from a picture.
    Straight and curved segments, filled and/or outlined.

    d = [["M", x, y], ["L", x, y], ["C", x1, y1, x2, y2, x, y], ["Z"], ...] in the item's own coordinates at its
    natural size w0 x h0; resizing scales the drawing, the line width stays the same. Several "M" parts can
    share one item (e.g. all the markers of one plot series). With smooth on, straight parts are drawn as a
    smooth curve through their corners instead."""
    TYPE = "path"
    radius = 0.0
    shadow = False

    def __init__(self, d):
        super().__init__(d)
        self.d = d.get("d") or []
        self.w0 = float(d.get("w0") or self.w)
        self.h0 = float(d.get("h0") or self.h)
        self.fill = d.get("fill")
        self.grad = clean_grad(d.get("grad")) if self.fill else None
        self.stroke = d.get("stroke")
        self.sw = float(d.get("sw", 1))
        self.dash = d.get("dash", "solid")
        self.rule = d.get("rule", "nonzero")
        self.cap = d.get("cap", "butt")
        self.smooth = bool(d.get("smooth", False))
        self._path = None

    @classmethod
    def from_segments(cls, segs, **style):
        """A path item placed around segments given in slide coordinates."""
        xs = [v for s in segs for v in s[1::2]]
        ys = [v for s in segs for v in s[2::2]]
        x0, y0 = min(xs), min(ys)
        w0, h0 = max(1.0, max(xs) - x0), max(1.0, max(ys) - y0)
        local = [[s[0]] + [round(v - (x0 if i % 2 == 0 else y0), 3) for i, v in enumerate(s[1:])] for s in segs]
        return dict(style, type="path", x=x0, y=y0, w=w0, h=h0, w0=w0, h0=h0, d=local)

    def label(self):
        parts = self.subpaths()
        return "Path" + (f" ({len(parts)} parts)" if len(parts) > 1 else "")

    def subpaths(self):
        out = []
        for s in self.d:
            if s[0] == "M" or not out:
                out.append([])
            out[-1].append(s)
        return out

    def scale(self):
        return (self.w / self.w0 if self.w0 > 1e-6 else 1.0), (self.h / self.h0 if self.h0 > 1e-6 else 1.0)

    def path(self):
        if self._path is None:
            sx, sy = self.scale()
            p = QPainterPath()
            p.setFillRule(Qt.OddEvenFill if self.rule == "evenodd" else Qt.WindingFill)
            for part in self.subpaths():
                if self.smooth and part and all(s[0] in "MLZ" for s in part):
                    pts = [(s[1] * sx, s[2] * sy) for s in part if s[0] != "Z"]
                    closed = part[-1][0] == "Z"
                    if closed and len(pts) > 2 and abs(pts[0][0] - pts[-1][0]) + abs(pts[0][1] - pts[-1][1]) < 1e-6:
                        pts.pop()
                    if len(pts) >= 2:
                        smooth_through(p, pts, closed)
                        continue
                for s in part:
                    v = [c * (sx if i % 2 == 0 else sy) for i, c in enumerate(s[1:])]
                    if s[0] == "M":
                        p.moveTo(v[0], v[1])
                    elif s[0] == "L":
                        p.lineTo(v[0], v[1])
                    elif s[0] == "C":
                        p.cubicTo(v[0], v[1], v[2], v[3], v[4], v[5])
                    elif s[0] == "Z":
                        p.closeSubpath()
            self._path = p
        return self._path

    # ---- corners (for Edit points). Indices point into self.d; positions are in the item's own coordinates.
    def _changed(self):
        self.prepareGeometryChange()
        self._path = None
        self.update()

    def _closing_twin(self, i):
        """True when segment i ends exactly on its part's start and only "Z" follows (a curve closing the shape):
        its end is the same corner as the start, so it isn't listed or moved separately."""
        if i + 1 >= len(self.d) or self.d[i + 1][0] != "Z" or self.d[i][0] not in ("L", "C"):
            return False
        m = self.d[self._part_of(i)[0]]
        return m[0] == "M" and abs(self.d[i][-2] - m[1]) < 1e-6 and abs(self.d[i][-1] - m[2]) < 1e-6

    def vertex_indices(self):
        return [i for i, s in enumerate(self.d) if s[0] in ("M", "L", "C") and not self._closing_twin(i)]

    def curve_point(self, i, t):
        """The point at t along segment i (straight or curved), in the item's own coordinates."""
        sx, sy = self.scale()
        s = self.d[i]
        prev = self._start_of(i)
        if s[0] == "C":
            u = 1 - t
            x = u ** 3 * prev[0] + 3 * u * u * t * s[1] + 3 * u * t * t * s[3] + t ** 3 * s[5]
            y = u ** 3 * prev[1] + 3 * u * u * t * s[2] + 3 * u * t * t * s[4] + t ** 3 * s[6]
        else:
            end = self._end_of(i)
            x, y = prev[0] + (end[0] - prev[0]) * t, prev[1] + (end[1] - prev[1]) * t
        return QPointF(x * sx, y * sy)

    def _start_of(self, i):
        """Where segment i starts (unscaled)."""
        s = self.d[i - 1]
        if s[0] == "Z":  # after a Z the pen is back at that part's start
            m = self.d[self._part_of(i - 1)[0]]
            return m[1], m[2]
        return s[-2], s[-1]

    def _end_of(self, i):
        s = self.d[i]
        if s[0] == "Z":
            m = self.d[self._part_of(i)[0]]
            return m[1], m[2]
        return s[-2], s[-1]

    def bend_edge(self, i, t, local):
        """Bends edge i so that the point at t on it passes through `local`: a straight edge becomes a curve.
        Returns the (possibly new) index of the curve."""
        sx, sy = self.scale()
        if self.d[i][0] != "C":
            a, b = self._start_of(i), self._end_of(i)
            c = ["C", a[0] + (b[0] - a[0]) / 3, a[1] + (b[1] - a[1]) / 3,
                 a[0] + 2 * (b[0] - a[0]) / 3, a[1] + 2 * (b[1] - a[1]) / 3, b[0], b[1]]
            if self.d[i][0] == "Z":  # the closing edge: draw it as a curve back onto the start, then close
                self.d.insert(i, c)
            else:
                self.d[i] = c
        t = max(0.15, min(0.85, t))
        cur = self.curve_point(i, t)
        k = 3 * t * (1 - t)  # moving both handles by D moves the point at t by k * D
        dx, dy = (local.x() - cur.x()) / k / sx, (local.y() - cur.y()) / k / sy
        s = list(self.d[i])
        s[1], s[2], s[3], s[4] = s[1] + dx, s[2] + dy, s[3] + dx, s[4] + dy
        self.d[i] = s
        self._changed()
        return i

    def vertex(self, i):
        sx, sy = self.scale()
        s = self.d[i]
        return QPointF(s[-2] * sx, s[-1] * sy)

    def is_closed(self):
        return any(part[-1][0] == "Z" for part in self.subpaths() if part)

    def _part_of(self, i):
        start = max(j for j in range(i + 1) if self.d[j][0] == "M") if any(
            self.d[j][0] == "M" for j in range(i + 1)) else 0
        end = next((j for j in range(i + 1, len(self.d)) if self.d[j][0] == "M"), len(self.d))
        return start, end

    def edges(self):
        """Every outline segment: (index that ends it, start, c1, c2, end), curves with their two handles,
        straight ones with None for both; a closed part's last edge back to its start ends at its "Z"."""
        sx, sy = self.scale()
        pt = lambda x, y: QPointF(x * sx, y * sy)  # noqa: E731
        out, start, prev = [], None, None
        for i, s in enumerate(self.d):
            if s[0] == "M":
                start = prev = pt(s[1], s[2])
            elif s[0] == "L" and prev is not None:
                q = pt(s[1], s[2])
                out.append((i, prev, None, None, q))
                prev = q
            elif s[0] == "C" and prev is not None:
                q = pt(s[5], s[6])
                out.append((i, prev, pt(s[1], s[2]), pt(s[3], s[4]), q))
                prev = q
            elif s[0] == "Z" and prev is not None and start is not None:
                if abs(prev.x() - start.x()) + abs(prev.y() - start.y()) > 1e-6:
                    out.append((i, prev, None, None, start))
                prev = start
        return out

    def move_vertex(self, i, local):
        """Moves a corner to `local`; a curve's handles next to it travel along, keeping the curve's shape."""
        sx, sy = self.scale()
        nx, ny = local.x() / sx, local.y() / sy
        s = list(self.d[i])
        dx, dy = nx - s[-2], ny - s[-1]
        s[-2], s[-1] = nx, ny
        if s[0] == "C":
            s[3], s[4] = s[3] + dx, s[4] + dy
        self.d[i] = s
        if i + 1 < len(self.d) and self.d[i + 1][0] == "C":
            n = list(self.d[i + 1])
            n[1], n[2] = n[1] + dx, n[2] + dy
            self.d[i + 1] = n
        if s[0] == "M":  # a curve that closes this part ends here too
            _, end = self._part_of(i)
            j = end - 2 if end - 1 > i and self.d[end - 1][0] == "Z" else None
            if j is not None and j > i and self.d[j][0] in ("L", "C"):
                t = list(self.d[j])
                if abs(t[-2] - (nx - dx)) < 1e-6 and abs(t[-1] - (ny - dy)) < 1e-6:
                    t[-2], t[-1] = nx, ny
                    if t[0] == "C":
                        t[3], t[4] = t[3] + dx, t[4] + dy
                    self.d[j] = t
        self._changed()

    def insert_vertex(self, end, local, t=0.5):
        """A new corner on the edge that ends at index `end` (from edges()). Returns its index."""
        sx, sy = self.scale()
        x, y = local.x() / sx, local.y() / sy
        s = self.d[end]
        if s[0] == "C":  # split the curve at t, keeping its shape
            prev = self.d[end - 1]
            p0 = (prev[-2], prev[-1])
            p1, p2, p3 = (s[1], s[2]), (s[3], s[4]), (s[5], s[6])
            lerp = lambda a, b: (a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t)  # noqa: E731
            a, b, c = lerp(p0, p1), lerp(p1, p2), lerp(p2, p3)
            d, e = lerp(a, b), lerp(b, c)
            m = lerp(d, e)
            self.d[end:end + 1] = [["C", a[0], a[1], d[0], d[1], m[0], m[1]], ["C", e[0], e[1], c[0], c[1], p3[0], p3[1]]]
        else:  # a straight edge, or the closing edge back to the start ("Z")
            self.d.insert(end, ["L", x, y])
        self._changed()
        return end

    def delete_vertex(self, i):
        """Removes a corner. False when the part would be too small (a line needs 2, a shape 3)."""
        start, end = self._part_of(i)
        part = self.d[start:end]
        n = sum(1 for s in part if s[0] in ("M", "L", "C"))
        if n <= (3 if part[-1][0] == "Z" else 2):
            return False
        s = self.d[i]
        if s[0] == "M":
            nxt = self.d[i + 1]
            j = end - 2 if self.d[end - 1][0] == "Z" else None
            if j is not None and self._closing_twin(j):
                t = list(self.d[j])
                t[-2], t[-1] = nxt[-2], nxt[-1]
                self.d[j] = t
            self.d[i:i + 2] = [["M", nxt[-2], nxt[-1]]]
        else:
            if s[0] == "C" and i + 1 < len(self.d) and self.d[i + 1][0] == "C":
                n2 = list(self.d[i + 1])
                n2[1], n2[2] = s[1], s[2]  # the merged curve leaves along the removed curve's first handle
                self.d[i + 1] = n2
            del self.d[i]
        self._changed()
        return True

    def set_closed(self, on):
        out = []
        for part in self.subpaths():
            part = [s for s in part if s[0] != "Z"]
            out += part + ([["Z"]] if on and len(part) >= 2 else [])
        self.d = out
        self._changed()

    def renormalize(self):
        """After corners moved: fits the box snugly around the drawing again, without anything moving on screen
        (rotation included)."""
        sx, sy = self.scale()
        xs = [v * sx for s in self.d for v in s[1::2]]
        ys = [v * sy for s in self.d for v in s[2::2]]
        if not xs:
            return
        x0, y0 = min(xs), min(ys)
        nw, nh = max(1.0, max(xs) - x0), max(1.0, max(ys) - y0)
        self.d = [[s[0]] + [round(v * sx - x0, 3) if k % 2 == 0 else round(v * sy - y0, 3)
                            for k, v in enumerate(s[1:])] for s in self.d]
        old_c, new_c = QPointF(self.w / 2, self.h / 2), QPointF(nw / 2, nh / 2)
        turn = QTransform().rotate(self.rotation())
        pos = self.pos() + old_c - new_c + turn.map(QPointF(x0, y0) + new_c - old_c)
        self.w0, self.h0 = nw, nh
        self.set_size(nw, nh)
        self.setPos(pos)

    def set_size(self, w, h):
        self._path = None
        super().set_size(w, h)

    def margin(self):
        return self.sw / 2 + 2

    def shape(self):
        st = QPainterPathStroker()
        st.setWidth(max(8.0, self.sw + 6))
        hit = st.createStroke(self.path())
        if self.fill:
            hit.addPath(self.path())
            hit.setFillRule(Qt.WindingFill)
        return hit

    def paint(self, p, opt, widget=None):
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(make_pen(self.stroke, self.sw, self.dash, CAPS.get(self.cap, Qt.FlatCap)))
        p.setBrush(self.fill_brush())
        p.drawPath(self.path())

    def to_dict(self):
        d = self.base()
        d.update(d=self.d, w0=self.w0, h0=self.h0, fill=self.fill, stroke=self.stroke, sw=self.sw, dash=self.dash,
                 rule=self.rule, cap=self.cap)
        if self.smooth:
            d["smooth"] = True
        if self.grad and self.fill:
            d["grad"] = dict(self.grad)
        return d


ITEM_TYPES = {"text": SlideText, "shape": SlideShape, "image": SlideImage, "line": SlideLine, "stroke": SlideStroke,
              "zoom": SlideZoom, "path": SlidePath}


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
