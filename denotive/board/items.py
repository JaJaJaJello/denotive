"""Things pinned to a board: evidence cards, attached files, notes, stickies, shapes, frames and free lines.

Each graphics item mirrors one row of the `items` table (self.row). Geometry edits are reported back to the
BoardScene, which records them as undoable commands and persists them.
"""
import math
from datetime import datetime

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import (QBrush, QColor, QFont, QFontMetricsF, QLinearGradient, QPainter, QPainterPath,
                           QPainterPathStroker, QPen, QTextLayout, QTextOption)
from PySide6.QtWidgets import QGraphicsItem, QGraphicsObject, QGraphicsTextItem, QStyleOptionGraphicsItem

from .. import context, geometry, theme

PAD = 18.0  # bounding slack for shadows, pins and selection; every decoration is capped to fit inside it
PIN_MAX = 40.0  # largest pin (scene units) so a zoomed-out pin never paints outside the item
STACK_ANGLES = (-3.2, 2.6, -1.6)
STACK_TINTS = ("#fff4a8", "#ffe7a0", "#fff8c4")


def evidence_card_height(w, aspect):
    k = w / 300.0
    return k * (70 + 280 * aspect)


def card_metrics(w, aspect):
    k = w / 300.0
    m = 10 * k
    tw = w - 2 * m
    return k, m, QRectF(m, m, tw, tw * aspect)


_wrap_cache = {}


def wrap_lines(text, font, width, max_lines):
    """Word-wraps text to at most max_lines, eliding the last line. Cached by content."""
    key = (text, font.family(), font.pixelSize(), font.weight(), int(width), max_lines)
    hit = _wrap_cache.get(key)
    if hit is not None:
        return hit
    fm = QFontMetricsF(font)
    layout = QTextLayout(text, font)
    opt = QTextOption()
    opt.setWrapMode(QTextOption.WrapAtWordBoundaryOrAnywhere)
    layout.setTextOption(opt)
    layout.beginLayout()
    lines = []
    while True:
        line = layout.createLine()
        if not line.isValid():
            break
        line.setLineWidth(width)
        lines.append(text[line.textStart():line.textStart() + line.textLength()])
    layout.endLayout()
    if len(lines) > max_lines:
        rest = "".join(lines[max_lines - 1:]).replace("\n", " ")
        lines = lines[:max_lines - 1] + [fm.elidedText(rest, Qt.ElideRight, width)]
    lines = [ln.rstrip("\n") for ln in lines]
    if len(_wrap_cache) > 4000:
        _wrap_cache.clear()
    _wrap_cache[key] = lines
    return lines


def short_date(ts):
    if not ts:
        return ""
    d = datetime.fromtimestamp(ts)
    now = datetime.now()
    t = d.strftime("%I:%M %p").lstrip("0")
    if d.date() == now.date():
        return f"Today, {t}"
    if d.year == now.year:
        return d.strftime("%b %d").replace(" 0", " ") + f", {t}"
    return d.strftime("%b %d, %Y").replace(" 0", " ")


def lod_of(painter, option):
    return QStyleOptionGraphicsItem.levelOfDetailFromTransform(painter.worldTransform())


class BoardItem(QGraphicsObject):
    KIND = ""
    RESIZABLE = True
    MIN_W, MIN_H = 60.0, 30.0
    HAS_PIN = True

    def __init__(self, row):
        super().__init__()
        self.strings = []
        self.row = {}
        self.w = self.h = 0.0
        self._resize = None
        self._hover = False
        self._carry = []
        self.setFlags(QGraphicsItem.ItemIsMovable | QGraphicsItem.ItemIsSelectable |
                      QGraphicsItem.ItemSendsGeometryChanges)
        self.setAcceptHoverEvents(True)
        self.apply_row(row)

    @property
    def id(self):
        return self.row["id"]

    @property
    def data(self):
        return self.row.setdefault("data", {})

    def apply_row(self, row):
        self._carry = []
        self.prepareGeometryChange()
        self.row = row
        self.w, self.h = float(row.get("w") or 100), float(row.get("h") or 60)
        self.setPos(float(row.get("x") or 0), float(row.get("y") or 0))
        self.setZValue(float(row.get("z") or 0))
        self.on_row()
        for s in self.strings:
            s.update_path()
        self.update()

    def on_row(self):
        pass

    def current_row(self):
        r = dict(self.row)
        r.update(x=round(self.x(), 2), y=round(self.y(), 2), w=round(self.w, 2), h=round(self.h, 2), z=self.zValue())
        return r

    # -- geometry
    def rect(self):
        return QRectF(0, 0, self.w, self.h)

    def boundingRect(self):
        return self.rect().adjusted(-PAD, -PAD - 6, PAD, PAD)

    def scene_rect(self):
        return QRectF(self.pos(), self.rect().size())

    def pin_local(self):
        return QPointF(self.w / 2, 7 * max(0.6, self.w / 300))

    def pin_scene(self):
        return self.mapToScene(self.pin_local())

    def pin_visible(self):
        return self.HAS_PIN or bool(self.strings) or self._hover

    def grip_rect(self):
        lod = 1.0
        sc = self.scene()
        if sc is not None and sc.views():
            lod = max(0.05, sc.views()[0].transform().m11())
        s = 14 / lod
        return QRectF(self.w - s * 0.75, self.h - s * 0.75, s, s)

    def pin_hit(self, local):
        lod = 1.0
        sc = self.scene()
        if sc is not None and sc.views():
            lod = max(0.05, sc.views()[0].transform().m11())
        r = max(9.0, 12 / lod)
        return (local - self.pin_local()).manhattanLength() <= r

    def height_for_width(self, w):
        return None

    # -- events
    def itemChange(self, change, value):
        if change == QGraphicsItem.ItemPositionChange and self._carry and                 self.scene() is not None and self.scene().mouseGrabberItem() is self:
            d = value - self.pos()
            for it in self._carry:
                if not it.isSelected():
                    it.moveBy(d.x(), d.y())
        elif change == QGraphicsItem.ItemPositionHasChanged:
            for s in self.strings:
                s.update_path()
            sc = self.scene()
            if sc is not None and hasattr(sc, "item_moved"):
                sc.item_moved(self)
        elif change == QGraphicsItem.ItemSelectedHasChanged:
            self.update()
        return super().itemChange(change, value)

    def hoverEnterEvent(self, e):
        self._hover = True
        self.update()
        super().hoverEnterEvent(e)

    def hoverLeaveEvent(self, e):
        self._hover = False
        self.unsetCursor()
        self.update()
        super().hoverLeaveEvent(e)

    def hoverMoveEvent(self, e):
        p = e.pos()
        sc = self.scene()
        if self.RESIZABLE and self.isSelected() and self.grip_rect().contains(p):
            self.setCursor(Qt.SizeFDiagCursor)
        elif self.pin_visible() and self.pin_hit(p) or (sc is not None and getattr(sc, "tool", "") == "string"):
            self.setCursor(Qt.CrossCursor)
        else:
            self.unsetCursor()
        super().hoverMoveEvent(e)

    def mousePressEvent(self, e):
        sc = self.scene()
        if e.button() == Qt.LeftButton and sc is not None:
            p = e.pos()
            if self.RESIZABLE and self.isSelected() and self.grip_rect().contains(p) and len(sc.selectedItems()) == 1:
                self._resize = (e.scenePos(), self.w, self.h, self.current_row())
                e.accept()
                return
            if (self.pin_visible() and self.pin_hit(p)) or sc.tool == "string" or e.modifiers() & Qt.AltModifier:
                sc.begin_string(self, e.scenePos())
                e.accept()
                return
            self._carry = self.carried_items()
        super().mousePressEvent(e)

    def mouseMoveEvent(self, e):
        sc = self.scene()
        if self._resize is not None:
            start, w0, h0, _ = self._resize
            d = e.scenePos() - start
            w = max(self.MIN_W, w0 + d.x())
            h = max(self.MIN_H, h0 + d.y())
            hw = self.height_for_width(w)
            if hw is not None:
                h = hw
            self.prepareGeometryChange()
            self.w, self.h = w, h
            self.on_resized()
            for s in self.strings:
                s.update_path()
            self.update()
            e.accept()
            return
        if sc is not None and sc.string_drag_active():
            sc.update_string_drag(e.scenePos())
            e.accept()
            return
        super().mouseMoveEvent(e)

    def mouseReleaseEvent(self, e):
        sc = self.scene()
        self._carry = []
        if self._resize is not None:
            before = self._resize[3]
            self._resize = None
            if sc is not None:
                sc.commit_geometry([self], [before])
            e.accept()
            return
        if sc is not None and sc.string_drag_active():
            sc.end_string_drag(e.scenePos())
            e.accept()
            return
        self._carry = []
        super().mouseReleaseEvent(e)

    def on_resized(self):
        pass

    def carried_items(self):
        return []

    # -- shared painting
    def paint_selection(self, p, lod, radius=6.0):
        if not self.isSelected():
            return
        pen = QPen(QColor(theme.SELECT), 2.2)
        pen.setCosmetic(True)
        p.setPen(pen)
        p.setBrush(Qt.NoBrush)
        m = min(4 / max(lod, 0.05), 8.0)
        p.drawRoundedRect(self.rect().adjusted(-m, -m, m, m), radius + m, radius + m)
        if self.RESIZABLE:
            g = self.grip_rect()
            gp = QPen(QColor(theme.SELECT), 1.2)
            gp.setCosmetic(True)
            p.setPen(gp)
            p.setBrush(QColor("#ffffff"))
            p.drawEllipse(g.center(), g.width() * 0.32, g.width() * 0.32)

    def pin_size(self, lod):
        """Pin diameter in scene units: grows with the card, stays visible when zoomed out, and never pokes
        outside boundingRect (anything painted outside it isn't erased when the item moves)."""
        s = 24 * max(0.6, self.w / 300)
        if lod * s < 10:
            s = min(10 / max(lod, 0.01), PIN_MAX)
        room = self.pin_local().y() - self.boundingRect().top()
        return max(4.0, min(s, 2 * room - 2))

    def paint_pin(self, p, lod, force=False):
        if not (force or self.pin_visible()):
            return
        if self.w * lod < 40:
            return
        s = self.pin_size(lod)
        c = self.pin_local()
        color = self.data.get("pin", "#e23b3b")
        p.drawPixmap(QRectF(c.x() - s / 2, c.y() - s / 2, s, s), theme.pin_pixmap(color, 28), QRectF(0, 0, 84, 84))

    def paint_shadow(self, p, r, radius, strength=1.0):
        p.setPen(Qt.NoPen)
        for dy, grow, a in ((2, 0, 60), (5, 2, 34), (10, 5, 16)):
            p.setBrush(QColor(0, 0, 0, int(a * strength)))
            p.drawRoundedRect(r.adjusted(-grow, -grow + dy, grow, grow + dy), radius + grow, radius + grow)

    def search_text(self):
        return self.row.get("title") or ""

    def thumbs(self):
        th = self.row.get("thumb") or self.data.get("thumb")
        return [th] if th else []

    def by_coinv(self):
        return self.data.get("author") == "coinv"

    def paint_coinv_badge(self, p, lod, x, y, k=1.0):
        """A small 'CO-INVESTIGATOR' tag on notes and slides the AI wrote."""
        if self.w * lod < 90:
            return
        f = theme.ui_font(9.5 * k, QFont.Bold)
        f.setLetterSpacing(QFont.AbsoluteSpacing, 0.8 * k)
        txt = "CO-INVESTIGATOR"
        tw = QFontMetricsF(f).horizontalAdvance(txt)
        r = QRectF(x, y, tw + 26 * k, 17 * k)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor("#5b3fc4"))
        p.drawRoundedRect(r, 8.5 * k, 8.5 * k)
        p.setPen(QColor("#ffffff"))
        p.setFont(theme.icon_font(9 * k))
        p.drawText(QRectF(r.x() + 5 * k, r.y(), 12 * k, r.height()), Qt.AlignCenter, theme.GLYPHS["search"])
        p.setFont(f)
        p.drawText(QRectF(r.x() + 17 * k, r.y(), tw + 6 * k, r.height()), Qt.AlignVCenter | Qt.AlignLeft, txt)


class EvidenceCard(BoardItem):
    KIND = "evidence"
    MIN_W = 150.0

    def on_row(self):
        self.aspect = float(self.data.get("aspect", 0.5625))
        self.h = evidence_card_height(self.w, self.aspect)

    def boundingRect(self):
        # the stacked older versions are rotated and offset, so they reach further out on big cards
        m = max(PAD, 24 * self.w / 300)
        return self.rect().adjusted(-m, -m - 6, m, m)

    def height_for_width(self, w):
        return evidence_card_height(w, self.aspect)

    def pin_local(self):
        return QPointF(self.w / 2, 9 * self.w / 300)

    def thumb(self):
        return self.row.get("thumb")

    def paint(self, p, option, widget=None):
        lod = lod_of(p, option)
        k, m, tr = card_metrics(self.w, self.aspect)
        r = self.rect()
        disp_w = self.w * lod
        p.setRenderHint(QPainter.Antialiasing, disp_w > 60)
        paper = QColor(self.data.get("color") or "#f6f4ee")
        dark = paper.lightness() < 110
        # older versions peek out underneath like a stack of post-its
        n_old = min(3, max(0, int(self.row.get("vnum") or 1) - 1))
        if n_old and 40 < disp_w < 110:
            # far away: show the stack as plain offset sheets (no rotation)
            p.setPen(Qt.NoPen)
            for i in range(n_old, 0, -1):
                p.setBrush(QColor(STACK_TINTS[i - 1]))
                p.drawRect(r.translated(3 * i * k, 3 * i * k))
        elif n_old and disp_w >= 110:
            c = r.center()
            for i in range(n_old, 0, -1):
                p.save()
                p.translate(c)
                p.rotate(STACK_ANGLES[i - 1])
                p.translate(-c)
                off = 3 * i * k
                sheet = r.adjusted(off * 0.6, off, off * 0.6, off)
                p.setPen(Qt.NoPen)
                p.setBrush(QColor(0, 0, 0, 40))
                p.drawRoundedRect(sheet.translated(0, 3 * k), 3 * k, 3 * k)
                p.setBrush(QColor(STACK_TINTS[i - 1]))
                p.drawRoundedRect(sheet, 3 * k, 3 * k)
                p.restore()
        if disp_w > 110:
            self.paint_shadow(p, r, 3 * k)
        elif disp_w > 30:
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(0, 0, 0, 70))
            p.drawRect(r.translated(0, 4 * k))
        p.setPen(Qt.NoPen)
        p.setBrush(paper)
        if disp_w > 110:
            p.drawRoundedRect(r, 3 * k, 3 * k)
        else:
            p.drawRect(r)
        # slide thumbnail
        th = self.thumb()
        size = 256 if tr.width() * lod < 220 else 1024
        pm = context.images.get(th, size) if (th and context.images) else None
        if pm is None and th and context.images:
            pm = context.images.peek(th, 1024 if size == 256 else 256)
            if pm is None and getattr(self.scene(), "export_mode", False):
                pm = context.images.get_sync(th, 1024)
        if pm is not None:
            p.setRenderHint(QPainter.SmoothPixmapTransform, disp_w < 900)
            p.drawPixmap(tr, pm, QRectF(pm.rect()))
        else:
            p.setBrush(QColor("#e4e2dc") if not dark else QColor("#3a3f49"))
            p.drawRect(tr)
        if disp_w > 60:
            p.setPen(QPen(QColor(0, 0, 0, 38), 1 / max(lod, 0.05)))
            p.setBrush(Qt.NoBrush)
            p.drawRect(tr)
        if disp_w >= 110:
            ink = QColor("#f1f2f4") if dark else QColor("#1d1f26")
            sub = QColor("#a9aeb8") if dark else QColor("#6d717b")
            f = theme.ui_font(14 * k, QFont.DemiBold)
            f.setPixelSize(max(1, int(round(14 * k))))
            title = self.row.get("title") or "Untitled evidence"
            y = tr.bottom() + 8 * k
            lines = wrap_lines(title, f, tr.width(), 2)
            p.setFont(f)
            p.setPen(ink)
            lh = 18 * k
            for i, ln in enumerate(lines):
                p.drawText(QRectF(m, y + i * lh, tr.width(), lh + 2 * k), Qt.AlignLeft | Qt.AlignVCenter, ln)
            fs = theme.ui_font(11 * k)
            p.setFont(fs)
            p.setPen(sub)
            yb = self.h - 20 * k
            p.drawText(QRectF(m, yb, tr.width() * 0.7, 14 * k), Qt.AlignLeft | Qt.AlignVCenter,
                       short_date(self.row.get("updated")))
            vn = int(self.row.get("vnum") or 1)
            if vn > 1:
                txt = f"v{vn}"
                fb = theme.ui_font(10.5 * k, QFont.Bold)
                p.setFont(fb)
                bw = QFontMetricsF(fb).horizontalAdvance(txt) + 12 * k
                br = QRectF(self.w - m - bw, yb - 1 * k, bw, 16 * k)
                p.setPen(Qt.NoPen)
                p.setBrush(QColor(theme.ACCENT))
                p.drawRoundedRect(br, 8 * k, 8 * k)
                p.setPen(QColor("#ffffff"))
                p.drawText(br, Qt.AlignCenter, txt)
        if self.by_coinv():
            self.paint_coinv_badge(p, lod, m + 6 * k, m + 6 * k, k)
        self.paint_pin(p, lod)
        self.paint_selection(p, lod, 3 * k)


class FileCard(BoardItem):
    KIND = "file"
    MIN_W, MIN_H = 200.0, 110.0

    def pin_local(self):
        return QPointF(self.w / 2, 0)

    def paint(self, p, option, widget=None):
        lod = lod_of(p, option)
        r = self.rect()
        disp_w = self.w * lod
        p.setRenderHint(QPainter.Antialiasing, disp_w > 60)
        d = self.data
        vdoc = self.row.get("vdoc") or {}
        ext = d.get("ext", "")
        accent = QColor(theme.file_color(ext))
        if disp_w > 30:
            self.paint_shadow(p, r, 10, 1.3)
        p.setPen(QPen(QColor(255, 255, 255, 22), 1 / max(lod, 0.05)))
        p.setBrush(QColor("#1f232b"))
        p.drawRoundedRect(r, 10, 10)
        # accent stripe
        clip = QPainterPath()
        clip.addRoundedRect(r, 10, 10)
        p.save()
        p.setClipPath(clip)
        p.fillRect(QRectF(0, 0, 5, self.h), accent)
        p.restore()
        if disp_w < 90:
            self.paint_pin(p, lod)
            self.paint_selection(p, lod, 10)
            return
        # header
        ic = QRectF(16, 14, 34, 34)
        bg = QColor(accent)
        bg.setAlpha(46)
        p.setPen(Qt.NoPen)
        p.setBrush(bg)
        p.drawRoundedRect(ic, 8, 8)
        p.setPen(accent.lighter(130))
        p.setFont(theme.icon_font(18))
        p.drawText(ic, Qt.AlignCenter, theme.GLYPHS.get(theme.file_glyph(ext), theme.GLYPHS["doc"]))
        f = theme.ui_font(14, QFont.DemiBold)
        p.setFont(f)
        p.setPen(QColor("#eef0f3"))
        name = d.get("name") or "file"
        tx = ic.right() + 10
        tw = self.w - tx - 14
        p.drawText(QRectF(tx, 12, tw, 20), Qt.AlignLeft | Qt.AlignVCenter,
                   QFontMetricsF(f).elidedText(name, Qt.ElideMiddle, tw))
        fs = theme.ui_font(11)
        p.setFont(fs)
        p.setPen(QColor("#8d929e"))
        path = d.get("path") or ""
        p.drawText(QRectF(tx, 32, tw, 16), Qt.AlignLeft | Qt.AlignVCenter,
                   QFontMetricsF(fs).elidedText(path, Qt.ElideMiddle, tw))
        # body preview
        body = QRectF(14, 58, self.w - 28, self.h - 58 - 32)
        if body.height() > 16:
            thumb = d.get("thumb")
            preview = vdoc.get("preview") or d.get("preview") or ""
            if thumb:
                pm = context.images.get(thumb, 512) if context.images else None
                if pm is None and getattr(self.scene(), "export_mode", False):
                    pm = context.images.get_sync(thumb, 512)
                if pm is not None and not pm.isNull():
                    s = min(body.width() / pm.width(), body.height() / pm.height())
                    tw2, th2 = pm.width() * s, pm.height() * s
                    p.setRenderHint(QPainter.SmoothPixmapTransform)
                    p.drawPixmap(QRectF(body.x() + (body.width() - tw2) / 2, body.y() + (body.height() - th2) / 2,
                                        tw2, th2), pm, QRectF(pm.rect()))
            elif preview:
                p.setPen(Qt.NoPen)
                p.setBrush(QColor("#171a20"))
                p.drawRoundedRect(body, 6, 6)
                fm_font = QFont(theme.MONO_FONT)
                fm_font.setPixelSize(11)
                p.setFont(fm_font)
                p.setPen(QColor("#aeb4c0"))
                lh = 14.5
                inner = body.adjusted(9, 7, -9, -6)
                p.save()
                p.setClipRect(inner)
                for i, ln in enumerate(preview.splitlines()):
                    y = inner.y() + i * lh
                    if y > inner.bottom():
                        break
                    p.drawText(QRectF(inner.x(), y, inner.width() + 400, lh), Qt.AlignLeft | Qt.AlignVCenter, ln)
                p.restore()
                fade = QLinearGradient(0, body.bottom() - 26, 0, body.bottom())
                fade.setColorAt(0, QColor(23, 26, 32, 0))
                fade.setColorAt(1, QColor(23, 26, 32, 255))
                p.setPen(Qt.NoPen)
                p.setBrush(QBrush(fade))
                p.drawRect(QRectF(body.x(), body.bottom() - 26, body.width(), 26))
            else:
                p.setPen(QColor(255, 255, 255, 40))
                p.setFont(theme.icon_font(min(48, body.height() * 0.6)))
                p.drawText(body, Qt.AlignCenter, theme.GLYPHS.get(theme.file_glyph(ext), theme.GLYPHS["doc"]))
        # footer
        p.setFont(theme.ui_font(11))
        p.setPen(QColor("#8d929e"))
        from ..widgets.common import human_size
        size = vdoc.get("size", d.get("size"))
        bits = [human_size(size)] if size is not None else []
        mt = vdoc.get("mtime", d.get("mtime"))
        if mt:
            bits.append("modified " + short_date(mt))
        p.drawText(QRectF(16, self.h - 26, self.w - 120, 18), Qt.AlignLeft | Qt.AlignVCenter, "  ·  ".join(bits))
        chip = "snapshot" if vdoc.get("asset") else "linked"
        vn = int(self.row.get("vnum") or 1)
        if vn > 1:
            chip += f" v{vn}"
        cf = theme.ui_font(10, QFont.DemiBold)
        p.setFont(cf)
        cw = QFontMetricsF(cf).horizontalAdvance(chip) + 14
        cr = QRectF(self.w - 14 - cw, self.h - 25, cw, 17)
        p.setPen(Qt.NoPen)
        cbg = QColor(accent)
        cbg.setAlpha(55)
        p.setBrush(cbg)
        p.drawRoundedRect(cr, 8.5, 8.5)
        p.setPen(accent.lighter(140))
        p.drawText(cr, Qt.AlignCenter, chip)
        self.paint_pin(p, lod)
        self.paint_selection(p, lod, 10)


class InlineText(QGraphicsTextItem):
    """Temporary editor for a note, sticky, shape label or frame title."""

    def __init__(self, owner, text, font, color, width, center):
        super().__init__(owner)
        self.owner = owner
        self.setPlainText(text)
        self.setFont(font)
        self.setDefaultTextColor(QColor(color))
        self.setTextWidth(width)
        opt = QTextOption(Qt.AlignHCenter if center else Qt.AlignLeft)
        opt.setWrapMode(QTextOption.WrapAtWordBoundaryOrAnywhere)
        self.document().setDefaultTextOption(opt)
        self.setTextInteractionFlags(Qt.TextEditorInteraction)
        self.document().contentsChanged.connect(owner.on_edit_changed)
        self._done = False

    def focusOutEvent(self, e):
        super().focusOutEvent(e)
        if not self._done and e.reason() != Qt.PopupFocusReason:
            self._done = True
            self.owner.finish_edit()

    def keyPressEvent(self, e):
        if e.key() == Qt.Key_Escape or (e.key() in (Qt.Key_Return, Qt.Key_Enter) and e.modifiers() & Qt.ControlModifier):
            if not self._done:
                self._done = True
                self.owner.finish_edit()
            return
        super().keyPressEvent(e)


class TextHolder(BoardItem):
    """Base for items that show an editable plain-text label."""
    CENTER = False
    PADDING = 10.0
    AUTO_HEIGHT = False
    editor = None

    def text(self):
        return self.data.get("text", "")

    def text_font(self):
        f = QFont(self.data.get("font") or theme.UI_FONT)
        f.setPixelSize(int(self.data.get("size", 18)))
        f.setBold(bool(self.data.get("bold", False)))
        f.setItalic(bool(self.data.get("italic", False)))
        return f

    def text_color(self):
        return self.data.get("tcolor") or "#e9eaee"

    def text_rect(self):
        p = self.PADDING
        return self.rect().adjusted(p, p, -p, -p)

    def on_row(self):
        if self.AUTO_HEIGHT:
            self.fit_height()

    def on_resized(self):
        if self.AUTO_HEIGHT:
            self.fit_height()

    def height_for_width(self, w):
        if not self.AUTO_HEIGHT:
            return None
        return self._text_height(w)

    def _text_height(self, w):
        fm = QFontMetricsF(self.text_font())
        t = self.text() or "Text"
        br = fm.boundingRect(QRectF(0, 0, max(10.0, w - 2 * self.PADDING), 1e6),
                             int(Qt.TextWordWrap | (Qt.AlignHCenter if self.CENTER else Qt.AlignLeft)), t)
        return max(self.MIN_H, br.height() + 2 * self.PADDING)

    def fit_height(self):
        self.h = self._text_height(self.w)

    def paint_text(self, p, lod):
        if self.editor is not None or self.w * lod < 30:
            return
        t = self.text()
        r = self.text_rect()
        p.setFont(self.text_font())
        if not t:
            if self.KIND == "note":
                p.setPen(QColor(150, 155, 165, 160))
                p.drawText(r, Qt.AlignLeft | Qt.AlignTop, "Double-click to type")
            return
        if self.w * lod < 60:
            p.setPen(Qt.NoPen)
            c = QColor(self.text_color())
            c.setAlpha(70)
            p.setBrush(c)
            for i in range(min(4, int(r.height() / 14))):
                p.drawRect(QRectF(r.x(), r.y() + i * 14 + 3, r.width() * (0.9 - 0.15 * (i % 3)), 6))
            return
        p.setPen(QColor(self.text_color()))
        align = Qt.AlignHCenter if (self.CENTER or self.data.get("align") == "center") else Qt.AlignLeft
        valign = Qt.AlignVCenter if self.CENTER else Qt.AlignTop
        p.drawText(r, int(align | valign | Qt.TextWordWrap), t)

    def begin_edit(self):
        if self.editor is not None:
            return
        r = self.text_rect()
        center = self.CENTER or self.data.get("align") == "center"
        self.editor = InlineText(self, self.text(), self.text_font(), self.text_color(), r.width(), center)
        self.editor.setPos(r.x() - 4, r.y() - 4 if not self.CENTER else r.y())
        self._place_editor()
        self.editor.setFocus(Qt.MouseFocusReason)
        c = self.editor.textCursor()
        c.select(c.SelectionType.Document)
        self.editor.setTextCursor(c)
        self.update()

    def _place_editor(self):
        if self.editor is None:
            return
        r = self.text_rect()
        if self.CENTER:
            eh = self.editor.boundingRect().height()
            self.editor.setPos(r.x() - 4, r.y() + max(0.0, (r.height() - eh) / 2))

    def on_edit_changed(self):
        if self.editor is None:
            return
        if self.AUTO_HEIGHT:
            nh = max(self.MIN_H, self.editor.boundingRect().height() + 2 * self.PADDING - 8)
            if abs(nh - self.h) > 0.5:
                self.prepareGeometryChange()
                self.h = nh
                for s in self.strings:
                    s.update_path()
        self._place_editor()
        self.update()

    def finish_edit(self):
        ed = self.editor
        if ed is None:
            return
        text = ed.toPlainText()
        self.editor = None
        ed.setParentItem(None)
        sc = self.scene()
        if sc is not None:
            sc.removeItem(ed)
            sc.commit_text(self, text)
        self.update()

    def mouseDoubleClickEvent(self, e):
        if e.button() == Qt.LeftButton:
            self.begin_edit()
            e.accept()
            return
        super().mouseDoubleClickEvent(e)

    def search_text(self):
        return self.text()


class NoteItem(TextHolder):
    KIND = "note"
    HAS_PIN = False
    AUTO_HEIGHT = True
    MIN_H = 30.0
    PADDING = 8.0

    def text_color(self):
        return self.data.get("tcolor") or "#eceef2"

    def paint(self, p, option, widget=None):
        lod = lod_of(p, option)
        p.setRenderHint(QPainter.Antialiasing)
        fill = self.data.get("fill")
        if fill:
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(fill))
            p.drawRoundedRect(self.rect(), 6, 6)
        elif self._hover and not self.isSelected():
            pen = QPen(QColor(255, 255, 255, 50), 1, Qt.DashLine)
            pen.setCosmetic(True)
            p.setPen(pen)
            p.setBrush(Qt.NoBrush)
            p.drawRoundedRect(self.rect(), 6, 6)
        self.paint_text(p, lod)
        self.paint_pin(p, lod)
        self.paint_selection(p, lod, 6)


class StickyItem(TextHolder):
    KIND = "sticky"
    MIN_W, MIN_H = 90.0, 70.0
    PADDING = 16.0

    def text_color(self):
        return self.data.get("tcolor") or "#2b2a22"

    def text_rect(self):
        p = self.PADDING
        return self.rect().adjusted(p, p + (32 if self.by_coinv() else 8), -p, -p)

    def pin_local(self):
        return QPointF(self.w / 2, 9)

    def paint(self, p, option, widget=None):
        lod = lod_of(p, option)
        p.setRenderHint(QPainter.Antialiasing, self.w * lod > 50)
        fill = QColor(self.data.get("fill") or "#fff09a")
        fold = min(26.0, self.w * 0.14)
        path = QPainterPath()
        path.moveTo(0, 0)
        path.lineTo(self.w, 0)
        path.lineTo(self.w, self.h - fold)
        path.lineTo(self.w - fold, self.h)
        path.lineTo(0, self.h)
        path.closeSubpath()
        if self.w * lod > 30:
            p.setPen(Qt.NoPen)
            for dy, a in ((3, 50), (7, 26), (12, 12)):
                p.setBrush(QColor(0, 0, 0, a))
                p.drawPath(path.translated(dy * 0.3, dy))
        g = QLinearGradient(0, 0, 0, self.h)
        g.setColorAt(0, fill.lighter(104))
        g.setColorAt(1, fill.darker(104))
        p.setPen(Qt.NoPen)
        p.setBrush(QBrush(g))
        p.drawPath(path)
        tri = QPainterPath()
        tri.moveTo(self.w, self.h - fold)
        tri.lineTo(self.w - fold, self.h - fold)
        tri.lineTo(self.w - fold, self.h)
        tri.closeSubpath()
        p.setBrush(fill.darker(122))
        p.drawPath(tri)
        if self.by_coinv():
            self.paint_coinv_badge(p, lod, self.PADDING, 18)
        self.paint_text(p, lod)
        self.paint_pin(p, lod)
        self.paint_selection(p, lod, 2)


class ShapeItem(TextHolder):
    KIND = "shape"
    CENTER = True
    HAS_PIN = False
    MIN_W, MIN_H = 30.0, 24.0

    def text_color(self):
        return self.data.get("tcolor") or "#ffffff"

    def text_rect(self):
        return geometry.shape_text_rect(self.data.get("shape", "round"), self.rect())

    def path(self):
        sw = float(self.data.get("sw", 0) or 0)
        i = sw / 2
        return geometry.shape_path(self.data.get("shape", "round"), self.rect().adjusted(i, i, -i, -i), 14)

    def shape(self):
        p = self.path()
        if not self.data.get("fill"):
            s = QPainterPathStroker()
            s.setWidth(12)
            return s.createStroke(p)
        return p

    def paint(self, p, option, widget=None):
        lod = lod_of(p, option)
        p.setRenderHint(QPainter.Antialiasing)
        fill = self.data.get("fill", "#5b8cff")
        stroke = self.data.get("stroke")
        sw = float(self.data.get("sw", 0) or 0)
        path = self.path()
        if fill and self.w * lod > 30:
            p.setPen(Qt.NoPen)
            for dy, a in ((2, 40), (6, 18)):
                p.setBrush(QColor(0, 0, 0, a))
                p.drawPath(path.translated(0, dy))
        pen = QPen(QColor(stroke), sw, Qt.DashLine if self.data.get("dash") == "dash" else Qt.SolidLine,
                   Qt.RoundCap, Qt.RoundJoin) if (stroke and sw > 0) else QPen(Qt.NoPen)
        p.setPen(pen)
        p.setBrush(QColor(fill) if fill else Qt.NoBrush)
        p.drawPath(path)
        self.paint_text(p, lod)
        self.paint_pin(p, lod)
        self.paint_selection(p, lod, 4)


class FrameItem(TextHolder):
    """A labelled region. Dragging it carries along everything inside."""
    KIND = "frame"
    HAS_PIN = False
    MIN_W, MIN_H = 160.0, 120.0
    PADDING = 14.0

    def text(self):
        return self.data.get("title", "")

    def text_font(self):
        return theme.ui_font(20, QFont.DemiBold)

    def text_color(self):
        return self.data.get("color") or "#9aa0ad"

    def text_rect(self):
        return QRectF(16, 12, max(60.0, self.w - 32), 30)

    def pin_local(self):
        return QPointF(self.w / 2, 0)

    def header_rect(self):
        return QRectF(0, 0, self.w, 52)

    def shape(self):
        # only the header and the border are grabbable so items inside stay clickable
        outer = QPainterPath()
        outer.addRoundedRect(self.rect(), 16, 16)
        inner = QPainterPath()
        inner.addRect(self.rect().adjusted(14, 52, -14, -14))
        return outer.subtracted(inner)

    def carried_items(self):
        sc = self.scene()
        if sc is None:
            return []
        mine = self.scene_rect()
        out = []
        for it in getattr(sc, "items_by_id", {}).values():
            if it is self:
                continue
            if mine.contains(it.scene_rect()):
                out.append(it)
        return out

    def paint(self, p, option, widget=None):
        lod = lod_of(p, option)
        p.setRenderHint(QPainter.Antialiasing)
        c = QColor(self.text_color())
        fill = QColor(c)
        fill.setAlpha(16)
        border = QColor(c)
        border.setAlpha(110)
        pen = QPen(border, 1.6, Qt.DashLine)
        pen.setCosmetic(True)
        pen.setDashPattern([6, 4])
        p.setPen(pen)
        p.setBrush(fill)
        p.drawRoundedRect(self.rect(), 16, 16)
        hb = QColor(c)
        hb.setAlpha(36)
        hdr = QPainterPath()
        hdr.addRoundedRect(QRectF(0, 0, self.w, 52), 16, 16)
        clip = QPainterPath()
        clip.addRect(QRectF(0, 0, self.w, 52))
        p.setPen(Qt.NoPen)
        p.setBrush(hb)
        p.drawPath(hdr.intersected(clip))
        if self.editor is None:
            t = self.text() or "Untitled frame"
            f = self.text_font()
            band = 40.0
            if lod < 0.5:
                # grow the title when zoomed out so it stays readable, but never past the frame itself
                band = min(40 * 0.5 / max(lod, 0.02), self.h - 12)
                f.setPixelSize(int(max(8, min(220, band * 0.55))))
            p.setFont(f)
            p.setPen(c.lighter(130))
            p.drawText(QRectF(18, 6, self.w - 36, band), Qt.AlignLeft | Qt.AlignVCenter,
                       QFontMetricsF(f).elidedText(t, Qt.ElideRight, self.w - 36))
        self.paint_pin(p, lod)
        self.paint_selection(p, lod, 16)


class LineItem(BoardItem):
    """A free line/arrow on the board. Its endpoints are draggable when selected."""
    KIND = "line"
    RESIZABLE = False
    HAS_PIN = False

    def on_row(self):
        d = self.data
        self.p2 = QPointF(float(d.get("x2", self.w)), float(d.get("y2", 0)))

    def pin_visible(self):
        return False

    def rect(self):
        return QRectF(QPointF(0, 0), self.p2).normalized()

    def boundingRect(self):
        sw = float(self.data.get("sw", 3))
        m = sw * 4 + 14
        return self.rect().adjusted(-m, -m, m, m)

    def shape(self):
        path = QPainterPath(QPointF(0, 0))
        path.lineTo(self.p2)
        s = QPainterPathStroker()
        s.setWidth(max(14.0, float(self.data.get("sw", 3)) + 10))
        s.setCapStyle(Qt.RoundCap)
        return s.createStroke(path)

    def scene_rect(self):
        return self.rect().translated(self.pos())

    def current_row(self):
        r = super().current_row()
        r["data"] = dict(self.data, x2=round(self.p2.x(), 2), y2=round(self.p2.y(), 2))
        rr = self.rect()
        r["w"], r["h"] = max(1.0, rr.width()), max(1.0, rr.height())
        return r

    def _end_hit(self, local):
        lod = 1.0
        sc = self.scene()
        if sc is not None and sc.views():
            lod = max(0.05, sc.views()[0].transform().m11())
        r = 10 / lod
        if (local - QPointF(0, 0)).manhattanLength() <= r:
            return "a"
        if (local - self.p2).manhattanLength() <= r:
            return "b"
        return None

    def hoverMoveEvent(self, e):
        if self.isSelected() and self._end_hit(e.pos()):
            self.setCursor(Qt.SizeAllCursor)
        else:
            self.unsetCursor()

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton and self.isSelected():
            end = self._end_hit(e.pos())
            if end:
                self._resize = (end, self.current_row())
                e.accept()
                return
        if e.button() == Qt.LeftButton:
            sc = self.scene()
            if sc is not None and sc.tool == "string":
                e.ignore()
                return
        QGraphicsObject.mousePressEvent(self, e)

    def mouseMoveEvent(self, e):
        if self._resize is not None:
            end = self._resize[0]
            sp = e.scenePos()
            self.prepareGeometryChange()
            if end == "b":
                self.p2 = sp - self.pos()
            else:
                old_b = self.pos() + self.p2
                self.setPos(sp)
                self.p2 = old_b - sp
            self.update()
            e.accept()
            return
        QGraphicsObject.mouseMoveEvent(self, e)

    def mouseReleaseEvent(self, e):
        if self._resize is not None:
            before = self._resize[1]
            self._resize = None
            sc = self.scene()
            if sc is not None:
                sc.commit_geometry([self], [before])
            e.accept()
            return
        QGraphicsObject.mouseReleaseEvent(self, e)

    def paint(self, p, option, widget=None):
        lod = lod_of(p, option)
        p.setRenderHint(QPainter.Antialiasing)
        d = self.data
        color = QColor(d.get("color") or "#e9eaee")
        sw = float(d.get("sw", 3))
        a, b = QPointF(0, 0), self.p2
        ang = math.atan2(b.y() - a.y(), b.x() - a.x())
        hs = max(10.0, sw * 3.4)
        sa, sb = QPointF(a), QPointF(b)
        if d.get("a1"):
            sa = QPointF(a.x() + math.cos(ang) * hs * 0.7, a.y() + math.sin(ang) * hs * 0.7)
        if d.get("a2", True):
            sb = QPointF(b.x() - math.cos(ang) * hs * 0.7, b.y() - math.sin(ang) * hs * 0.7)
        room = sw * 4 + 14  # boundingRect margin
        if self.isSelected():
            glow = QColor(theme.SELECT)
            glow.setAlpha(90)
            gp = QPen(glow, sw + min(8 / max(lod, 0.05), 2 * room - sw - 4), Qt.SolidLine, Qt.RoundCap)
            p.setPen(gp)
            p.drawLine(a, b)
        style = {"dash": Qt.DashLine, "dot": Qt.DotLine}.get(d.get("dash"), Qt.SolidLine)
        p.setPen(QPen(color, sw, style, Qt.RoundCap, Qt.RoundJoin))
        p.drawLine(sa, sb)
        p.setPen(Qt.NoPen)
        p.setBrush(color)
        if d.get("a2", True):
            p.drawPolygon(geometry.arrow_head(b, ang, hs))
        if d.get("a1"):
            p.drawPolygon(geometry.arrow_head(a, ang + math.pi, hs))
        if self.isSelected():
            s = min(6 / max(lod, 0.05), room - 4)
            hp = QPen(QColor(theme.SELECT), 1.4)
            hp.setCosmetic(True)
            p.setPen(hp)
            p.setBrush(QColor("#ffffff"))
            p.drawEllipse(a, s, s)
            p.drawEllipse(b, s, s)

class BoardLinkCard(BoardItem):
    """Stands for a whole board: one per board on a project map, or a cross-reference placed on any board.
    Double-click jumps to the board; tie strings to it like any other card."""
    KIND = "boardlink"
    MIN_W, MIN_H = 220.0, 170.0
    TAB_H = 22.0

    def on_row(self):
        self.info = context.store.board_info(self.data.get("board_id")) if context.store else None

    def linked_id(self):
        return self.data.get("board_id")

    def thumbs(self):
        return list((self.info or {}).get("thumbs") or [])

    def pin_local(self):
        return QPointF(self.w / 2, self.TAB_H + 7)

    def search_text(self):
        return ((self.info or {}).get("board") or {}).get("name", "")

    def paint(self, p, option, widget=None):
        lod = lod_of(p, option)
        disp_w = self.w * lod
        p.setRenderHint(QPainter.Antialiasing, disp_w > 50)
        info = self.info or {}
        gone = not info or info.get("gone")
        board = info.get("board") or {}
        proj = info.get("project") or {}
        color = QColor(proj.get("color") or theme.ACCENT)
        if gone:
            color = QColor("#5d626e")
        body = QRectF(0, self.TAB_H, self.w, self.h - self.TAB_H)
        tab = QRectF(16, 0, min(170.0, self.w * 0.55), self.TAB_H + 14)
        if disp_w > 30:
            self.paint_shadow(p, body, 12, 1.2)
        p.setPen(Qt.NoPen)
        p.setBrush(color.darker(150))
        p.drawRoundedRect(tab, 8, 8)
        p.setBrush(QColor("#232832"))
        p.setPen(QPen(QColor(color.red(), color.green(), color.blue(), 120), 1.2))
        p.drawRoundedRect(body, 12, 12)
        clip = QPainterPath()
        clip.addRoundedRect(body, 12, 12)
        p.save()
        p.setClipPath(clip)
        p.fillRect(QRectF(0, body.y(), self.w, 4), color)
        p.restore()
        if disp_w < 90:
            self.paint_pin(p, lod)
            self.paint_selection(p, lod, 12)
            return
        # folder tab label
        sc = self.scene()
        here = getattr(sc, "board", None) or {}
        tab_text = "BOARD" if proj.get("id") == here.get("project_id") else (proj.get("name") or "BOARD").upper()
        f = theme.ui_font(10.5, QFont.Bold)
        f.setLetterSpacing(QFont.AbsoluteSpacing, 1.0)
        p.setFont(f)
        p.setPen(QColor(255, 255, 255, 210))
        p.drawText(QRectF(tab.x() + 12, 1, tab.width() - 20, self.TAB_H - 2), Qt.AlignLeft | Qt.AlignVCenter,
                   QFontMetricsF(f).elidedText(tab_text, Qt.ElideRight, tab.width() - 20))
        # mosaic of the board's latest evidence
        m = 12.0
        mos = QRectF(m, body.y() + m + 2, self.w - 2 * m, max(20.0, body.height() * 0.5))
        mclip = QPainterPath()
        mclip.addRoundedRect(mos, 7, 7)
        p.save()
        p.setClipPath(mclip)
        p.fillRect(mos, QColor("#151820"))
        th = self.thumbs()
        cells = []
        if len(th) == 1:
            cells = [mos]
        elif len(th) == 2:
            cells = [QRectF(mos.x(), mos.y(), mos.width() / 2 - 1, mos.height()),
                     QRectF(mos.center().x() + 1, mos.y(), mos.width() / 2 - 1, mos.height())]
        elif len(th) == 3:
            hw = mos.width() / 2
            cells = [QRectF(mos.x(), mos.y(), hw - 1, mos.height()),
                     QRectF(mos.x() + hw + 1, mos.y(), hw - 1, mos.height() / 2 - 1),
                     QRectF(mos.x() + hw + 1, mos.center().y() + 1, hw - 1, mos.height() / 2 - 1)]
        elif th:
            hw, hh = mos.width() / 2, mos.height() / 2
            cells = [QRectF(mos.x() + (i % 2) * (hw + 1), mos.y() + (i // 2) * (hh + 1), hw - 1, hh - 1)
                     for i in range(4)]
        export = getattr(sc, "export_mode", False)
        for cell, h in zip(cells, th):
            pm = context.images.get(h, 256) if context.images else None
            if pm is None and export:
                pm = context.images.get_sync(h, 256)
            if pm is None or pm.isNull():
                p.fillRect(cell, QColor("#2a2f39"))
                continue
            s = max(cell.width() / pm.width(), cell.height() / pm.height())
            tw, tht = pm.width() * s, pm.height() * s
            p.save()
            p.setClipRect(cell, Qt.IntersectClip)
            p.setRenderHint(QPainter.SmoothPixmapTransform)
            p.drawPixmap(QRectF(cell.center().x() - tw / 2, cell.center().y() - tht / 2, tw, tht), pm,
                         QRectF(pm.rect()))
            p.restore()
        if not th:
            p.setPen(QColor(255, 255, 255, 40))
            p.setFont(theme.icon_font(min(40.0, mos.height() * 0.5)))
            p.drawText(mos, Qt.AlignCenter, theme.GLYPHS["pin"])
        p.restore()
        # name and counts
        y = mos.bottom() + 9
        tf = theme.ui_font(16, QFont.DemiBold)
        p.setFont(tf)
        p.setPen(QColor("#8c919d") if gone else QColor("#f1f2f4"))
        name = board.get("name") or "Missing board"
        p.drawText(QRectF(m + 2, y, self.w - 2 * m - 30, 24), Qt.AlignLeft | Qt.AlignVCenter,
                   QFontMetricsF(tf).elidedText(name, Qt.ElideRight, self.w - 2 * m - 30))
        p.setFont(theme.icon_font(14))
        p.setPen(color.lighter(130))
        p.drawText(QRectF(self.w - m - 24, y, 24, 24), Qt.AlignCenter, theme.GLYPHS["newwindow"])
        sf = theme.ui_font(11.5)
        p.setFont(sf)
        p.setPen(QColor("#9aa0ab"))
        if gone:
            meta = "This board was deleted"
        else:
            meta = f"{info.get('evidence', 0)} evidence · {info.get('files', 0)} files · {info.get('strings', 0)} strings"
        p.drawText(QRectF(m + 2, y + 24, self.w - 2 * m, 18), Qt.AlignLeft | Qt.AlignVCenter,
                   QFontMetricsF(sf).elidedText(meta, Qt.ElideRight, self.w - 2 * m))
        if not gone and y + 60 < self.h:
            p.setFont(theme.ui_font(11))
            p.setPen(QColor("#6d727e"))
            p.drawText(QRectF(m + 2, y + 42, self.w - 2 * m, 16), Qt.AlignLeft | Qt.AlignVCenter,
                       "updated " + short_date(info.get("last")))
        self.paint_pin(p, lod)
        self.paint_selection(p, lod, 12)


KIND_CLASSES = {"evidence": EvidenceCard, "file": FileCard, "note": NoteItem, "sticky": StickyItem,
                "shape": ShapeItem, "frame": FrameItem, "line": LineItem, "boardlink": BoardLinkCard}
