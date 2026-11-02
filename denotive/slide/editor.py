"""The evidence editor: a PowerPoint-style slide editor shown full-page when adding or editing evidence."""
import json
import time
from pathlib import Path

from PySide6.QtCore import QPointF, QRectF, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import (QColor, QDesktopServices, QFont, QImage, QKeySequence, QPainter, QPen, QShortcut,
                           QTextBlockFormat, QTextCharFormat, QTextCursor, QTextFormat, QTextListFormat)
from PySide6.QtWidgets import (QApplication, QCheckBox, QComboBox, QDialog, QDoubleSpinBox, QFileDialog,
                               QFontComboBox, QFrame, QGraphicsView, QHBoxLayout, QLineEdit, QListWidget,
                               QListWidgetItem, QMenu, QMessageBox, QPlainTextEdit, QScrollArea, QSlider, QSpinBox,
                               QSplitter, QToolButton, QVBoxLayout, QWidget)

from .. import context, geometry, media, textmarks, theme
from ..widgets.common import ColorButton, hsep, shift_as_ctrl, label, push_button, section_label, tool_button, vsep
from .items import (PAGE_SIZES, SlideImage, SlideItem, SlideLine, SlideShape, SlideStroke, SlideText, TextBearing,
                    doc_aspect)
from .scene import SlideScene

SLIDE_MIME = "application/x-denotive-slide-items"
IMAGE_FILTER = "Images (*.png *.jpg *.jpeg *.gif *.bmp *.tif *.tiff *.webp);;All files (*)"
IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".bmp", ".tif", ".tiff", ".webp"}


# ---------------------------------------------------------------------- text formatting helpers
def _each_fragment(cursor, fn):
    """Calls fn(fmt) for every fragment touched by the cursor selection and writes the format back."""
    doc = cursor.document()
    start, end = cursor.selectionStart(), cursor.selectionEnd()
    edit = QTextCursor(doc)
    edit.beginEditBlock()
    block = doc.findBlock(start)
    while block.isValid() and block.position() <= end:
        it = block.begin()
        while not it.atEnd():
            frag = it.fragment()
            a = max(frag.position(), start)
            b = min(frag.position() + frag.length(), end)
            if a < b:
                c = QTextCursor(doc)
                c.setPosition(a)
                c.setPosition(b, QTextCursor.KeepAnchor)
                fmt = frag.charFormat()
                fn(fmt)
                c.setCharFormat(fmt)
            it += 1
        block = block.next()
    edit.endEditBlock()


def set_px(fmt, px):
    fmt.clearProperty(QTextFormat.FontPointSize)
    fmt.setProperty(QTextFormat.FontPixelSize, int(round(px)))


def fmt_px(fmt, default):
    if fmt.hasProperty(QTextFormat.FontPixelSize):
        return fmt.intProperty(QTextFormat.FontPixelSize)
    if fmt.hasProperty(QTextFormat.FontPointSize):
        return fmt.doubleProperty(QTextFormat.FontPointSize) * 96 / 72
    return default


class SlideView(QGraphicsView):
    crop_requested = Signal(object)
    files_dropped = Signal(list, QPointF)
    image_dropped = Signal(QImage, QPointF)
    text_dropped = Signal(str, QPointF)
    key_command = Signal(str)
    page_changed = Signal(int)

    def __init__(self, scene):
        super().__init__(scene)
        self.setRenderHints(QPainter.Antialiasing | QPainter.SmoothPixmapTransform | QPainter.TextAntialiasing)
        self.setFrameShape(QFrame.NoFrame)
        self.setDragMode(QGraphicsView.RubberBandDrag)
        self.setRubberBandSelectionMode(Qt.IntersectsItemShape)
        self.setTransformationAnchor(QGraphicsView.AnchorUnderMouse)
        self.setResizeAnchor(QGraphicsView.AnchorViewCenter)
        self.setViewportUpdateMode(QGraphicsView.FullViewportUpdate)
        self.setAcceptDrops(True)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.StrongFocus)
        self.fit_mode = True
        self._pan = None
        self.verticalScrollBar().valueChanged.connect(self._track_page)
        scene.page_added.connect(lambda k: QTimer.singleShot(0, lambda: self.show_page(k)))

    def request_crop(self, item):
        self.crop_requested.emit(item)

    def fit(self):
        """Fits the page in view (with several pages, the one you're looking at)."""
        sc = self.scene()
        r = sc.page_rects()[min(sc.current_page, sc.pages - 1)]
        m = max(r.width(), r.height()) * 0.04
        self.fitInView(r.adjusted(-m, -m, m, m), Qt.KeepAspectRatio)
        sc.view_scale = self.transform().m11()
        self.fit_mode = True

    def show_page(self, k):
        sc = self.scene()
        sc.current_page = max(0, min(k, sc.pages - 1))
        if self.fit_mode:
            self.fit()
        else:
            self.centerOn(sc.page_rects()[sc.current_page].center())
        self._track_page()

    def _track_page(self, *_):
        sc = self.scene()
        if sc is None:
            return
        c = self.mapToScene(self.viewport().rect().center())
        k = sc.page_at(c.y())
        if k != sc.current_page:
            sc.current_page = k
        self.page_changed.emit(sc.current_page)

    def zoom_by(self, f):
        cur = self.transform().m11()
        f = max(0.05, min(8.0, cur * f)) / cur
        self.scale(f, f)
        self.scene().view_scale = self.transform().m11()
        self.fit_mode = False

    def resizeEvent(self, e):
        super().resizeEvent(e)
        if self.fit_mode:
            self.fit()

    def showEvent(self, e):
        super().showEvent(e)
        if self.fit_mode:
            QTimer.singleShot(0, self.fit)

    def wheelEvent(self, e):
        if e.modifiers() & Qt.ControlModifier:
            self.zoom_by(1.0015 ** e.angleDelta().y())
            e.accept()
            return
        super().wheelEvent(e)

    def mousePressEvent(self, e):
        if e.button() == Qt.MiddleButton:
            self._pan = e.position()
            self.viewport().setCursor(Qt.ClosedHandCursor)
            e.accept()
            return
        self.setFocus(Qt.MouseFocusReason)
        if e.button() == Qt.LeftButton and self.scene().tool == "select":
            e = shift_as_ctrl(e)
        super().mousePressEvent(e)

    def mouseMoveEvent(self, e):
        if self._pan is not None:
            d = e.position() - self._pan
            self._pan = e.position()
            self.horizontalScrollBar().setValue(self.horizontalScrollBar().value() - int(d.x()))
            self.verticalScrollBar().setValue(self.verticalScrollBar().value() - int(d.y()))
            return
        sc = self.scene()
        sp = self.mapToScene(e.position().toPoint())
        over_add = sc.add_page_rect().contains(sp)
        if over_add != sc.hover_add:
            sc.hover_add = over_add
            sc.update(sc.add_page_rect().adjusted(-4, -4, 4, 4))
        if not e.buttons():
            if over_add:
                self.viewport().setCursor(Qt.PointingHandCursor)
            elif sc.tool == "select":
                it, h = sc.handle_at(sp)
                cur = sc.cursor_for(it, h) if h else None
                if cur is not None:
                    self.viewport().setCursor(cur)
                else:
                    self.viewport().unsetCursor()
        super().mouseMoveEvent(e)

    def mouseReleaseEvent(self, e):
        if self._pan is not None and e.button() == Qt.MiddleButton:
            self._pan = None
            self.viewport().unsetCursor()
            return
        if e.button() == Qt.LeftButton and self.scene().tool == "select":
            e = shift_as_ctrl(e)
        super().mouseReleaseEvent(e)

    ARROWS = {Qt.Key_Left: (-1, 0), Qt.Key_Right: (1, 0), Qt.Key_Up: (0, -1), Qt.Key_Down: (0, 1)}

    def keyPressEvent(self, e):
        sc = self.scene()
        action = textmarks.key_action(e)
        if action:
            self.key_command.emit(action)
            e.accept()
            return
        if sc.editing is None and e.key() in self.ARROWS and sc.selected():
            # nudge the selection one pixel (ten with Shift); never scroll the view instead
            dx, dy = self.ARROWS[e.key()]
            step = 10 if e.modifiers() & Qt.ShiftModifier else 1
            sc.nudge(dx * step, dy * step)
            e.accept()
            return
        if sc.editing is None and e.modifiers() & Qt.ControlModifier:
            cmds = {Qt.Key_C: "copy", Qt.Key_X: "cut", Qt.Key_V: "paste", Qt.Key_D: "duplicate",
                    Qt.Key_A: "selectall", Qt.Key_Z: "undo", Qt.Key_Y: "redo", Qt.Key_G: "group"}
            if e.key() in cmds:
                cmd = cmds[e.key()]
                if e.modifiers() & Qt.ShiftModifier:
                    cmd = {"undo": "redo", "group": "ungroup"}.get(cmd, cmd)
                self.key_command.emit(cmd)
                e.accept()
                return
        if sc.editing is None and e.key() in (Qt.Key_PageDown, Qt.Key_PageUp) and sc.pages > 1:
            self.show_page(sc.current_page + (1 if e.key() == Qt.Key_PageDown else -1))
            e.accept()
            return
        super().keyPressEvent(e)

    # drag and drop of images / files / text
    def dragEnterEvent(self, e):
        md = e.mimeData()
        if md.hasUrls() or md.hasImage() or md.hasText():
            e.acceptProposedAction()

    def dragMoveEvent(self, e):
        e.acceptProposedAction()

    def dropEvent(self, e):
        md = e.mimeData()
        pos = self.mapToScene(e.position().toPoint())
        if md.hasUrls() and any(u.isLocalFile() for u in md.urls()):
            self.files_dropped.emit([u.toLocalFile() for u in md.urls() if u.isLocalFile()], pos)
        elif md.hasImage():
            self.image_dropped.emit(QImage(md.imageData()), pos)
        elif md.hasText():
            self.text_dropped.emit(md.text(), pos)
        e.acceptProposedAction()


# ---------------------------------------------------------------------- crop dialog
class CropCanvas(QWidget):
    def __init__(self, pixmap, iw, ih, crop):
        super().__init__()
        self.pm = pixmap
        self.iw, self.ih = iw, ih
        self.crop = QRectF(*crop)
        self.aspect = None
        self.drag = None
        self.setMinimumSize(640, 440)
        self.setMouseTracking(True)

    def _geom(self):
        m = 24
        avail = QRectF(m, m, self.width() - 2 * m, self.height() - 2 * m)
        s = min(avail.width() / self.iw, avail.height() / self.ih)
        w, h = self.iw * s, self.ih * s
        return s, QPointF(avail.x() + (avail.width() - w) / 2, avail.y() + (avail.height() - h) / 2)

    def to_w(self, r):
        s, o = self._geom()
        return QRectF(o.x() + r.x() * s, o.y() + r.y() * s, r.width() * s, r.height() * s)

    def to_img(self, p):
        s, o = self._geom()
        return QPointF((p.x() - o.x()) / s, (p.y() - o.y()) / s)

    def set_aspect(self, a):
        self.aspect = a
        if a:
            c = self.crop.center()
            w = self.crop.width()
            h = w / a
            if h > self.ih:
                h = self.ih
                w = h * a
            if w > self.iw:
                w = self.iw
                h = w / a
            self.crop = QRectF(c.x() - w / 2, c.y() - h / 2, w, h)
            self._clamp_move()
        self.update()

    def _clamp_move(self):
        r = self.crop
        dx = max(0 - r.left(), 0) + min(self.iw - r.right(), 0)
        dy = max(0 - r.top(), 0) + min(self.ih - r.bottom(), 0)
        self.crop = r.translated(dx, dy)

    def _handles(self):
        r = self.to_w(self.crop)
        return {"tl": r.topLeft(), "tr": r.topRight(), "bl": r.bottomLeft(), "br": r.bottomRight(),
                "t": QPointF(r.center().x(), r.top()), "b": QPointF(r.center().x(), r.bottom()),
                "l": QPointF(r.left(), r.center().y()), "r": QPointF(r.right(), r.center().y())}

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setRenderHint(QPainter.SmoothPixmapTransform)
        p.fillRect(self.rect(), QColor("#111216"))
        full = self.to_w(QRectF(0, 0, self.iw, self.ih))
        p.drawPixmap(full, self.pm, QRectF(self.pm.rect()))
        cr = self.to_w(self.crop)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(0, 0, 0, 150))
        for r in (QRectF(full.left(), full.top(), full.width(), cr.top() - full.top()),
                  QRectF(full.left(), cr.bottom(), full.width(), full.bottom() - cr.bottom()),
                  QRectF(full.left(), cr.top(), cr.left() - full.left(), cr.height()),
                  QRectF(cr.right(), cr.top(), full.right() - cr.right(), cr.height())):
            p.drawRect(r)
        p.setPen(QPen(QColor(255, 255, 255, 90), 1))
        for i in (1, 2):
            x = cr.left() + cr.width() * i / 3
            y = cr.top() + cr.height() * i / 3
            p.drawLine(QPointF(x, cr.top()), QPointF(x, cr.bottom()))
            p.drawLine(QPointF(cr.left(), y), QPointF(cr.right(), y))
        p.setPen(QPen(QColor("#ffffff"), 2))
        p.setBrush(Qt.NoBrush)
        p.drawRect(cr)
        p.setBrush(QColor("#ffffff"))
        p.setPen(QPen(QColor(theme.SELECT), 1.5))
        for pt in self._handles().values():
            p.drawRoundedRect(QRectF(pt.x() - 5, pt.y() - 5, 10, 10), 2, 2)
        p.setPen(QColor(theme.MUTED))
        p.drawText(QRectF(0, self.height() - 22, self.width(), 20), Qt.AlignCenter,
                   f"{int(self.crop.width())} × {int(self.crop.height())} px")
        p.end()

    def mousePressEvent(self, e):
        pos = e.position()
        for n, pt in self._handles().items():
            if (pt - pos).manhattanLength() < 12:
                self.drag = (n, QRectF(self.crop), self.to_img(pos))
                return
        if self.to_w(self.crop).contains(pos):
            self.drag = ("move", QRectF(self.crop), self.to_img(pos))
        else:
            ip = self.to_img(pos)
            self.crop = QRectF(ip, ip)
            self.drag = ("br", QRectF(self.crop), ip)

    def mouseMoveEvent(self, e):
        pos = e.position()
        if self.drag is None:
            cur = Qt.ArrowCursor
            for n, pt in self._handles().items():
                if (pt - pos).manhattanLength() < 12:
                    cur = {"tl": Qt.SizeFDiagCursor, "br": Qt.SizeFDiagCursor, "tr": Qt.SizeBDiagCursor,
                           "bl": Qt.SizeBDiagCursor, "t": Qt.SizeVerCursor, "b": Qt.SizeVerCursor,
                           "l": Qt.SizeHorCursor, "r": Qt.SizeHorCursor}[n]
                    break
            else:
                if self.to_w(self.crop).contains(pos):
                    cur = Qt.SizeAllCursor
            self.setCursor(cur)
            return
        n, r0, start = self.drag
        ip = self.to_img(pos)
        ip = QPointF(min(max(ip.x(), 0), self.iw), min(max(ip.y(), 0), self.ih))
        if n == "move":
            self.crop = r0.translated(ip - start)
            self._clamp_move()
        else:
            x0, y0, x1, y1 = r0.left(), r0.top(), r0.right(), r0.bottom()
            if "l" in n:
                x0 = ip.x()
            if "r" in n:
                x1 = ip.x()
            if "t" in n:
                y0 = ip.y()
            if "b" in n:
                y1 = ip.y()
            r = QRectF(QPointF(x0, y0), QPointF(x1, y1)).normalized()
            if self.aspect and len(n) == 2:
                w = max(r.width(), r.height() * self.aspect)
                h = w / self.aspect
                ax = r0.right() if "l" in n else r0.left()
                ay = r0.bottom() if "t" in n else r0.top()
                r = QRectF(ax - w if "l" in n else ax, ay - h if "t" in n else ay, w, h)
                r = r.intersected(QRectF(0, 0, self.iw, self.ih))
            self.crop = r
        self.update()

    def mouseReleaseEvent(self, e):
        self.drag = None
        if self.crop.width() < 4 or self.crop.height() < 4:
            self.crop = QRectF(0, 0, self.iw, self.ih)
        self.update()


class CropDialog(QDialog):
    def __init__(self, item, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Crop image")
        self.resize(980, 720)
        pm = context.images.get_sync(item.asset, 2048)
        self.canvas = CropCanvas(pm, item.iw, item.ih, item.crop)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(14, 14, 14, 14)
        top = QHBoxLayout()
        top.addWidget(label("Aspect", "Muted"))
        self.aspect = QComboBox()
        self.aspect.addItems(["Free", "Original", "1:1", "4:3", "3:2", "16:9", "9:16"])
        self.aspect.currentTextChanged.connect(self._aspect)
        top.addWidget(self.aspect)
        top.addStretch(1)
        reset = push_button("Reset", "refresh")
        reset.clicked.connect(self._reset)
        top.addWidget(reset)
        lay.addLayout(top)
        lay.addWidget(self.canvas, 1)
        bot = QHBoxLayout()
        bot.addWidget(label("Drag the handles, or drag outside the box to draw a new one.", "Hint"))
        bot.addStretch(1)
        cancel = push_button("Cancel")
        cancel.clicked.connect(self.reject)
        ok = push_button("Apply crop", "crop", primary=True)
        ok.clicked.connect(self.accept)
        bot.addWidget(cancel)
        bot.addWidget(ok)
        lay.addLayout(bot)
        self.item = item

    def _aspect(self, t):
        if t == "Free":
            self.canvas.set_aspect(None)
        elif t == "Original":
            self.canvas.set_aspect(self.item.iw / max(1, self.item.ih))
        else:
            a, b = t.split(":")
            self.canvas.set_aspect(float(a) / float(b))

    def _reset(self):
        self.canvas.crop = QRectF(0, 0, self.item.iw, self.item.ih)
        self.canvas.update()

    def result_crop(self):
        r = self.canvas.crop
        return [r.x(), r.y(), r.width(), r.height()]


# ---------------------------------------------------------------------- inspector
class Section(QFrame):
    def __init__(self, title):
        super().__init__()
        self.lay = QVBoxLayout(self)
        self.lay.setContentsMargins(14, 12, 14, 12)
        self.lay.setSpacing(8)
        self.lay.addWidget(section_label(title))

    def row(self, *widgets, stretch=True):
        h = QHBoxLayout()
        h.setSpacing(6)
        for w in widgets:
            if isinstance(w, str):
                lab = label(w, "Muted")
                lab.setMinimumWidth(54)
                h.addWidget(lab)
            else:
                h.addWidget(w)
        if stretch:
            h.addStretch(1)
        self.lay.addLayout(h)
        return h


def spin(lo, hi, step=1.0, decimals=0, suffix="", width=70):
    s = QDoubleSpinBox()
    s.setRange(lo, hi)
    s.setDecimals(decimals)
    s.setSingleStep(step)
    s.setSuffix(suffix)
    s.setFixedWidth(width)
    s.setKeyboardTracking(False)
    return s


class Inspector(QScrollArea):
    def __init__(self, page):
        super().__init__()
        self.page = page
        self.setObjectName("Inspector")
        self.setWidgetResizable(True)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setFixedWidth(300)
        self.setFrameShape(QFrame.NoFrame)
        body = QWidget()
        body.setObjectName("Inspector")
        self.v = QVBoxLayout(body)
        self.v.setContentsMargins(0, 0, 0, 0)
        self.v.setSpacing(0)
        self.setWidget(body)
        self._busy = False
        self._build()

    @property
    def scene(self):
        return self.page.scene

    def _build(self):
        # ---- slide
        s = self.sec_slide = Section("Slide")
        self.bg = ColorButton("#ffffff", tip="Background colour")
        self.bg.colorChanged.connect(self._set_bg)
        s.row("Background", self.bg)
        self.page_size = QComboBox()
        self.page_size.addItems(list(PAGE_SIZES))
        self.page_size.currentTextChanged.connect(self._set_page)
        s.row("Page", self.page_size)
        s.lay.addWidget(label("Tip: paste screenshots with Ctrl+V, or use Snip to grab part of the screen.",
                              "Hint", wrap=True))
        # ---- text
        s = self.sec_text = Section("Text")
        self.font_box = QFontComboBox()
        self.font_box.setMaximumWidth(170)
        self.font_box.currentFontChanged.connect(lambda f: self._char(lambda fmt: fmt.setFontFamilies([f.family()]),
                                                                     family=f.family()))
        self.size_box = spin(4, 400, 1, 0, " pt", 74)
        self.size_box.valueChanged.connect(lambda v: self._char(lambda fmt: set_px(fmt, v / 0.75), px=v / 0.75))
        s.row(self.font_box, self.size_box)
        self.b_bold = tool_button("bold", "Bold (Ctrl+B)", True, 16)
        self.b_bold.clicked.connect(lambda on: self._char(lambda f: f.setFontWeight(QFont.Bold if on else QFont.Normal)))
        self.b_ital = tool_button("italic", "Italic (Ctrl+I)", True, 16)
        self.b_ital.clicked.connect(lambda on: self._char(lambda f: f.setFontItalic(on)))
        self.b_under = tool_button("underline", "Underline (Ctrl+U)", True, 16)
        self.b_under.clicked.connect(lambda on: self._char(lambda f: f.setFontUnderline(on)))
        self.b_strike = tool_button("strike", "Strikethrough (Ctrl+Shift+X)", True, 16)
        self.b_strike.clicked.connect(lambda on: self._char(lambda f: f.setFontStrikeOut(on)))
        self.fg = ColorButton("#1d1f24", glyph="fontcolor", tip="Text colour")
        self.fg.colorChanged.connect(lambda c: self._char(lambda f: f.setForeground(QColor(c)), color=c))
        self.hl = ColorButton(None, allow_none=True, glyph="highlighter", tip="Highlight")
        self.hl.colorChanged.connect(
            lambda c: self._char(lambda f: f.setBackground(QColor(c)) if c else f.clearBackground()))
        for b in (self.b_bold, self.b_ital, self.b_under, self.b_strike):
            b.setFocusPolicy(Qt.NoFocus)
        s.row(self.b_bold, self.b_ital, self.b_under, self.b_strike, self.fg, self.hl)
        self.al = {}
        row = []
        for key, glyph in (("left", "alignleft"), ("center", "aligncenter"), ("right", "alignright")):
            b = tool_button(glyph, f"Align {key}", True, 16)
            b.setFocusPolicy(Qt.NoFocus)
            b.clicked.connect(lambda _=False, k=key: self._align_text(k))
            self.al[key] = b
            row.append(b)
        bl = tool_button("bullets", "Bulleted list (Ctrl+Shift+8)", False, 16)
        bl.setFocusPolicy(Qt.NoFocus)
        bl.clicked.connect(lambda: self._list(QTextListFormat.ListDisc))
        nl = tool_button("list", "Numbered list (Ctrl+Shift+7)", False, 16)
        nl.setFocusPolicy(Qt.NoFocus)
        nl.clicked.connect(lambda: self._list(QTextListFormat.ListDecimal))
        self.b_sup = tool_button("superscript", "Superscript (Ctrl+=)", True, 16)
        self.b_sup.clicked.connect(lambda: self.page.set_script("sup"))
        self.b_sub = tool_button("subscript", "Subscript (Ctrl+-)", True, 16)
        self.b_sub.clicked.connect(lambda: self.page.set_script("sub"))
        for b in (self.b_sup, self.b_sub):
            b.setFocusPolicy(Qt.NoFocus)
        s.row(*row, vsep(18), bl, nl, vsep(18), self.b_sup, self.b_sub)
        # ---- fill / border
        s = self.sec_fill = Section("Fill & border")
        self.shape_kind = QComboBox()
        for k, n in geometry.SHAPES:
            self.shape_kind.addItem(theme.icon("shape_" + k, 16), n, k)
        self.shape_kind.currentIndexChanged.connect(self._set_shape_kind)
        self.shape_row = s.row("Shape", self.shape_kind)
        self.fill = ColorButton("#5b8cff", allow_none=True, tip="Fill")
        self.fill.colorChanged.connect(lambda c: self._attr("fill", c))
        self.stroke = ColorButton(None, allow_none=True, tip="Border colour")
        self.stroke.colorChanged.connect(self._set_stroke)
        self.sw = spin(0, 60, 0.5, 1, " px", 74)
        self.sw.valueChanged.connect(lambda v: self._attr("sw", v))
        s.row("Fill", self.fill, label("Border", "Muted"), self.stroke, self.sw)
        self.dash = QComboBox()
        self.dash.addItems(["solid", "dash", "dot", "dashdot"])
        self.dash.currentTextChanged.connect(lambda t: self._attr("dash", t))
        self.radius = spin(0, 400, 1, 0, " px", 74)
        self.radius.valueChanged.connect(lambda v: self._attr("radius", v))
        s.row("Dash", self.dash, label("Corner", "Muted"), self.radius)
        self.shadow = QCheckBox("Drop shadow")
        self.shadow.toggled.connect(lambda on: self._attr("shadow", on))
        s.lay.addWidget(self.shadow)
        # ---- line
        s = self.sec_line = Section("Line")
        self.lcolor = ColorButton("#1d1f24", tip="Line colour")
        self.lcolor.colorChanged.connect(lambda c: self._attr("stroke", c, types=(SlideLine,)))
        self.lw = spin(0.5, 60, 0.5, 1, " px", 74)
        self.lw.valueChanged.connect(lambda v: self._attr("sw", v, types=(SlideLine,)))
        s.row("Colour", self.lcolor, self.lw)
        self.ldash = QComboBox()
        self.ldash.addItems(["solid", "dash", "dot", "dashdot"])
        self.ldash.currentTextChanged.connect(lambda t: self._attr("dash", t, types=(SlideLine,)))
        s.row("Dash", self.ldash)
        self.a1 = QCheckBox("Start arrow")
        self.a1.toggled.connect(lambda on: self._attr("a1", on, types=(SlideLine,)))
        self.a2 = QCheckBox("End arrow")
        self.a2.toggled.connect(lambda on: self._attr("a2", on, types=(SlideLine,)))
        s.row(self.a1, self.a2)
        # ---- ink
        s = self.sec_ink = Section("Ink")
        self.icolor = ColorButton("#e5484d", tip="Ink colour")
        self.icolor.colorChanged.connect(self._set_ink_color)
        self.iw = spin(0.5, 80, 0.5, 1, " px", 74)
        self.iw.valueChanged.connect(self._set_ink_width)
        s.row("Colour", self.icolor, self.iw)
        # ---- image
        s = self.sec_img = Section("Image")
        crop = push_button("Crop…", "crop")
        crop.clicked.connect(lambda: self.page.crop_selected())
        reset = push_button("Reset crop", "refresh")
        reset.clicked.connect(self._reset_crop)
        s.row(crop, reset)
        rep = push_button("Replace…", "image")
        rep.clicked.connect(self.page.replace_image)
        nat = push_button("Actual size", "fit")
        nat.clicked.connect(self._natural_size)
        s.row(rep, nat)
        # ---- arrange
        s = self.sec_arr = Section("Arrange")
        self.gx = spin(-9999, 9999, 1, 0, "", 64)
        self.gy = spin(-9999, 9999, 1, 0, "", 64)
        self.gw = spin(1, 9999, 1, 0, "", 64)
        self.gh = spin(1, 9999, 1, 0, "", 64)
        for sp in (self.gx, self.gy, self.gw, self.gh):
            sp.valueChanged.connect(self._set_geom)
        s.row("X", self.gx, label("Y", "Muted"), self.gy)
        s.row("W", self.gw, label("H", "Muted"), self.gh)
        self.rot = spin(-360, 360, 1, 0, "°", 64)
        self.rot.valueChanged.connect(self._set_rot)
        self.op = QSlider(Qt.Horizontal)
        self.op.setRange(5, 100)
        self.op.setFixedWidth(100)
        self.op.valueChanged.connect(self._set_opacity)
        self.op.sliderReleased.connect(self.scene_push)
        s.row("Rotate", self.rot, label("Opacity", "Muted"), self.op)
        btns = []
        for name, tip, how in (("front", "Bring to front", "front"), ("forward", "Bring forward", "forward"),
                               ("backward", "Send backward", "backward"), ("back", "Send to back", "back")):
            b = tool_button(name, tip, False, 18)
            b.setFocusPolicy(Qt.NoFocus)
            b.clicked.connect(lambda _=False, h=how: self.scene.reorder(h))
            btns.append(b)
        self.lock = tool_button("lock", "Lock position", True, 16)
        self.lock.setFocusPolicy(Qt.NoFocus)
        self.lock.clicked.connect(self._set_lock)
        self.b_group = tool_button("group", "Group (Ctrl+G)", False, 18)
        self.b_group.clicked.connect(lambda: self.page.command("group"))
        self.b_ungroup = tool_button("ungroup", "Ungroup (Ctrl+Shift+G)", False, 18)
        self.b_ungroup.clicked.connect(lambda: self.page.command("ungroup"))
        for b in (self.b_group, self.b_ungroup):
            b.setFocusPolicy(Qt.NoFocus)
        s.row(*btns, vsep(18), self.lock, vsep(18), self.b_group, self.b_ungroup)
        abtns = []
        for name, how, tip in (("align_left", "left", "Align left"), ("align_hcenter", "hcenter", "Align centre"),
                               ("align_right", "right", "Align right"), ("align_top", "top", "Align top"),
                               ("align_vcenter", "vcenter", "Align middle"), ("align_bottom", "bottom", "Align bottom"),
                               ("dist_h", "dist_h", "Distribute horizontally"),
                               ("dist_v", "dist_v", "Distribute vertically")):
            b = tool_button(name, tip + " (to page when one item is selected)", False, 18)
            b.setFocusPolicy(Qt.NoFocus)
            b.clicked.connect(lambda _=False, h=how: self.scene.align(h))
            abtns.append(b)
        s.row(*abtns[:6])
        s.row(*abtns[6:])
        # ---- layers
        s = self.sec_layers = Section("Layers")
        self.layers = QListWidget()
        self.layers.setMinimumHeight(160)
        self.layers.itemClicked.connect(self._layer_clicked)
        self.layers.setContextMenuPolicy(Qt.CustomContextMenu)
        self.layers.customContextMenuRequested.connect(self._layers_menu)
        s.lay.addWidget(self.layers)
        for sec in (self.sec_slide, self.sec_text, self.sec_fill, self.sec_line, self.sec_ink, self.sec_img,
                    self.sec_arr, self.sec_layers):
            self.v.addWidget(sec)
            if sec is not self.sec_layers:
                self.v.addWidget(hsep())
        self.v.addStretch(1)

    # ---- helpers
    def scene_push(self):
        self.scene.push_state()

    def selected(self, types=None):
        sel = [i for i in self.scene.selectedItems() if isinstance(i, SlideItem)]
        if types:
            sel = [i for i in sel if isinstance(i, types)]
        return sel

    def _attr(self, name, value, types=None):
        if self._busy:
            return
        items = self.selected(types)
        for it in items:
            if hasattr(it, name):
                if hasattr(it, "prepareGeometryChange"):
                    it.prepareGeometryChange()
                setattr(it, name, value)
                if hasattr(it, "layout_children"):
                    it.layout_children()
                it.update()
        if items:
            self.scene.push_state()
            self.scene.update()

    def _set_stroke(self, c):
        if self._busy:
            return
        items = self.selected((SlideShape, SlideText, SlideImage))
        for it in items:
            it.stroke = c
            if c and it.sw <= 0:
                it.sw = 2.0
            it.prepareGeometryChange()
            it.update()
        if items:
            self.refresh()
            self.scene.push_state()

    def _set_shape_kind(self, _):
        if self._busy:
            return
        k = self.shape_kind.currentData()
        for it in self.selected((SlideShape,)):
            it.prepareGeometryChange()
            it.shape_kind = k
            it.layout_children()
            it.update()
        self.scene.push_state()

    def _set_ink_color(self, c):
        if self._busy:
            return
        for it in self.selected((SlideStroke,)):
            it.color = c
            it.update()
        if self.scene.tool == "highlighter":
            self.scene.opts["hl_color"] = c
        else:
            self.scene.opts["pen_color"] = c
        self.scene.push_state()

    def _set_ink_width(self, v):
        if self._busy:
            return
        for it in self.selected((SlideStroke,)):
            it.prepareGeometryChange()
            it.sw = v
            it.update()
        if self.scene.tool == "highlighter":
            self.scene.opts["hl_w"] = v
        else:
            self.scene.opts["pen_w"] = v
        self.scene.push_state()

    def _set_bg(self, c):
        if c:
            self.scene.bg = c
            self.scene.update()
            self.scene.push_state()

    def _set_page(self, name):
        if self._busy or name not in PAGE_SIZES:
            return
        w, h = PAGE_SIZES[name]
        self.scene.set_page(w, h)
        self.page.view.fit()
        self.scene.push_state()

    def _text_targets(self):
        sc = self.scene
        if sc.editing is not None:
            return [sc.editing], True
        return [i for i in self.selected((TextBearing,))], False

    def _char(self, fn, family=None, px=None, color=None):
        """Applies a character format change to the text selection (when editing) or to whole text items."""
        if self._busy:
            return
        targets, editing = self._text_targets()
        if not targets:
            # nothing selected: change the defaults used for new text boxes
            if px:
                self.scene.opts["text_px"] = px
            if color:
                self.scene.opts["text_color"] = color
            if family:
                self.scene.opts["font"] = family
            return
        for it in targets:
            t = it.text
            if editing:
                c = t.textCursor()
                if c.hasSelection():
                    _each_fragment(c, fn)
                else:
                    fmt = c.charFormat()
                    fn(fmt)
                    c.setCharFormat(fmt)
                    t.setTextCursor(c)
            else:
                c = QTextCursor(t.document())
                c.select(QTextCursor.Document)
                _each_fragment(c, fn)
                if color:
                    it.set_text_color(color)
                if family or px:
                    it.set_default_font(family, px)
            it.layout_children()
            it.update()
        if editing:
            self.page.view.setFocus()
            targets[0].text.setFocus()
        else:
            self.scene.push_state()

    def _align_text(self, key):
        targets, editing = self._text_targets()
        al = {"left": Qt.AlignLeft, "center": Qt.AlignHCenter, "right": Qt.AlignRight}[key]
        for it in targets:
            c = it.text.textCursor() if editing else QTextCursor(it.text.document())
            if not editing:
                c.select(QTextCursor.Document)
            bf = QTextBlockFormat()
            bf.setAlignment(al)
            c.mergeBlockFormat(bf)
        for k, b in self.al.items():
            b.setChecked(k == key)
        if targets and not editing:
            self.scene.push_state()

    def _list(self, style):
        targets, editing = self._text_targets()
        for it in targets:
            c = it.text.textCursor() if editing else QTextCursor(it.text.document())
            if not editing:
                c.select(QTextCursor.Document)
            cur_list = c.currentList()
            if cur_list is not None and cur_list.format().style() == style:
                for i in reversed(range(cur_list.count())):
                    blk = cur_list.item(i)
                    cur_list.remove(blk)
                    bc = QTextCursor(blk)
                    bf = bc.blockFormat()
                    bf.setIndent(0)
                    bc.setBlockFormat(bf)
            else:
                lf = QTextListFormat()
                lf.setStyle(style)
                lf.setIndent(1)
                c.createList(lf)
            it.layout_children()
        if targets and not editing:
            self.scene.push_state()

    def _reset_crop(self):
        for it in self.selected((SlideImage,)):
            it.set_crop([0, 0, it.iw, it.ih])
        self.scene.push_state()

    def _natural_size(self):
        for it in self.selected((SlideImage,)):
            c = it.mapToScene(QPointF(it.w / 2, it.h / 2))
            it.set_size(it.crop[2], it.crop[3])
            it.setPos(c - QPointF(it.w / 2, it.h / 2))
        self.scene.push_state()

    def _set_geom(self):
        if self._busy:
            return
        sel = self.selected()
        if len(sel) != 1:
            return
        it = sel[0]
        it.setPos(self.gx.value(), self.gy.value())
        if not isinstance(it, SlideLine):
            it.set_size(self.gw.value(), self.gh.value())
        self.scene.push_state()
        self.refresh()

    def _set_rot(self, v):
        if self._busy:
            return
        for it in self.selected():
            if not isinstance(it, SlideLine):
                it.setRotation(v)
        self.scene.push_state()

    def _set_opacity(self, v):
        if self._busy:
            return
        for it in self.selected():
            it.setOpacity(v / 100)

    def _set_lock(self, on):
        for it in self.selected():
            it.set_locked(on)
        self.scene.update()
        self.scene.push_state()

    def _layer_clicked(self, li):
        uid = li.data(Qt.UserRole)
        for it in self.scene.slide_items():
            if it.uid == uid:
                if QApplication.keyboardModifiers() & Qt.ControlModifier:
                    it.setSelected(not it.isSelected())
                else:
                    self.scene.clearSelection()
                    it.setSelected(True)
                break

    def _toggle_visible(self, uid):
        for it in self.scene.slide_items():
            if it.uid == uid:
                it.setVisible(not it.isVisible())
                if not it.isVisible():
                    it.setSelected(False)
        self.scene.push_state()
        self.refresh_layers()

    # ---- refresh
    def refresh(self):
        self._busy = True
        try:
            sc = self.scene
            sel = self.selected()
            editing = sc.editing
            text_items = [editing] if editing else [i for i in sel if isinstance(i, TextBearing)]
            fill_items = [i for i in sel if isinstance(i, (SlideShape, SlideText, SlideImage))]
            self.sec_slide.setVisible(not sel and editing is None)
            self.sec_text.setVisible(bool(text_items) or (not sel and editing is None))
            self.sec_fill.setVisible(bool(fill_items))
            self.sec_line.setVisible(any(isinstance(i, SlideLine) for i in sel))
            self.sec_ink.setVisible(any(isinstance(i, SlideStroke) for i in sel) or
                                    (not sel and sc.tool in ("pen", "highlighter")))
            self.sec_img.setVisible(any(isinstance(i, SlideImage) for i in sel))
            self.sec_arr.setVisible(bool(sel))
            self.bg.setColor(sc.bg)
            name = next((n for n, (w, h) in PAGE_SIZES.items() if w == sc.page_w and h == sc.page_h), None)
            if name:
                self.page_size.setCurrentText(name)
            if text_items:
                it = text_items[0]
                if editing:
                    fmt = it.text.textCursor().charFormat()
                    blk = it.text.textCursor().blockFormat()
                else:
                    c = QTextCursor(it.text.document())
                    c.movePosition(QTextCursor.Start)
                    c.movePosition(QTextCursor.Right)
                    fmt = c.charFormat()
                    blk = c.blockFormat()
                fams = fmt.fontFamilies() if fmt.hasProperty(QTextFormat.FontFamilies) else None
                fam = (fams[0] if fams else None) or it.font_family
                self.font_box.setCurrentFont(QFont(fam))
                self.size_box.setValue(round(fmt_px(fmt, it.font_px) * 0.75))
                self.b_bold.setChecked(fmt.fontWeight() >= QFont.DemiBold)
                self.b_ital.setChecked(fmt.fontItalic())
                self.b_under.setChecked(fmt.fontUnderline())
                self.b_strike.setChecked(fmt.fontStrikeOut())
                self.b_sup.setChecked(fmt.verticalAlignment() == QTextCharFormat.AlignSuperScript)
                self.b_sub.setChecked(fmt.verticalAlignment() == QTextCharFormat.AlignSubScript)
                fg = fmt.foreground().color() if fmt.hasProperty(QTextFormat.ForegroundBrush) else QColor(it.color)
                self.fg.setColor(fg.name())
                a = blk.alignment()
                if isinstance(it, SlideShape) and not (blk.hasProperty(QTextFormat.BlockAlignment)):
                    a = Qt.AlignHCenter
                key = "center" if a & Qt.AlignHCenter else "right" if a & Qt.AlignRight else "left"
                for k, b in self.al.items():
                    b.setChecked(k == key)
            elif not sel:
                self.font_box.setCurrentFont(QFont(sc.opts["font"]))
                self.size_box.setValue(round(sc.opts["text_px"] * 0.75))
                self.fg.setColor(sc.opts["text_color"])
            if fill_items:
                it = fill_items[0]
                self.shape_row_visible(isinstance(it, SlideShape))
                if isinstance(it, SlideShape):
                    idx = self.shape_kind.findData(it.shape_kind)
                    self.shape_kind.setCurrentIndex(max(0, idx))
                self.fill.setVisible(not isinstance(it, SlideImage))
                self.fill.setColor(getattr(it, "fill", None))
                self.stroke.setColor(it.stroke)
                self.sw.setValue(it.sw)
                self.dash.setCurrentText(getattr(it, "dash", "solid"))
                self.radius.setValue(it.radius)
                self.shadow.setVisible(not isinstance(it, SlideText))
                self.shadow.setChecked(bool(getattr(it, "shadow", False)))
            lines = [i for i in sel if isinstance(i, SlideLine)]
            if lines:
                ln = lines[0]
                self.lcolor.setColor(ln.stroke)
                self.lw.setValue(ln.sw)
                self.ldash.setCurrentText(ln.dash)
                self.a1.setChecked(ln.a1)
                self.a2.setChecked(ln.a2)
            inks = [i for i in sel if isinstance(i, SlideStroke)]
            if inks:
                self.icolor.setColor(inks[0].color)
                self.iw.setValue(inks[0].sw)
            elif sc.tool in ("pen", "highlighter"):
                hl = sc.tool == "highlighter"
                self.icolor.setColor(sc.opts["hl_color" if hl else "pen_color"])
                self.iw.setValue(sc.opts["hl_w" if hl else "pen_w"])
            if len(sel) == 1:
                it = sel[0]
                self.gx.setValue(it.x())
                self.gy.setValue(it.y())
                self.gw.setValue(it.w)
                self.gh.setValue(it.h)
                for spn in (self.gw, self.gh):
                    spn.setEnabled(not isinstance(it, SlideLine))
                self.rot.setValue(it.rotation())
                self.op.setValue(int(it.opacity() * 100))
                self.lock.setChecked(it.locked)
            elif sel:
                self.op.setValue(int(sel[0].opacity() * 100))
            for spn in (self.gx, self.gy, self.gw, self.gh, self.rot):
                spn.setEnabled(len(sel) == 1)
            self.b_group.setEnabled(len(sel) >= 2 and sc.selected_group() is None)
            self.b_ungroup.setEnabled(any(i.group in sc.groups() for i in sel))
        finally:
            self._busy = False
        self.refresh_layers()

    def shape_row_visible(self, on):
        for i in range(self.shape_row.count()):
            w = self.shape_row.itemAt(i).widget()
            if w is not None:
                w.setVisible(on)

    def refresh_layers(self):
        self.layers.blockSignals(True)
        self.layers.clear()
        icons = {"text": "textbox", "shape": "shape_round", "image": "image", "line": "tool_line",
                 "stroke": "ink"}
        groups = self.scene.groups()
        for it in reversed(self.scene.sorted_items()):
            li = QListWidgetItem(theme.icon(icons.get(it.TYPE, "doc"), 16, theme.MUTED if it.isVisible() else theme.FAINT),
                                 ("⧉ " if it.group in groups else "") + it.label() +
                                 ("  (hidden)" if not it.isVisible() else "") + ("  🔒" if it.locked else ""))
            li.setData(Qt.UserRole, it.uid)
            self.layers.addItem(li)
            li.setSelected(it.isSelected())
        self.layers.blockSignals(False)

    def _layers_menu(self, pos):
        li = self.layers.itemAt(pos)
        if li is None:
            return
        uid = li.data(Qt.UserRole)
        m = QMenu(self)
        m.addAction(theme.icon("view", 16), "Show / hide", lambda: self._toggle_visible(uid))
        m.exec(self.layers.mapToGlobal(pos))


# ---------------------------------------------------------------------- page
class EditorPage(QWidget):
    finished = Signal(dict)  # {"board_id", "item_id", "created", "saved"}

    TOOLS = [("select", "cursor", "Select (V)", "V"), ("text", "tool_text", "Text box (T)", "T"),
             ("code", "code", "Code block", None), ("line", "tool_line", "Line (L)", "L"),
             ("arrow", "tool_arrow", "Arrow (A)", "A"), ("pen", "ink", "Pen (P)", "P"),
             ("highlighter", "highlighter", "Highlighter (H)", "H"), ("eraser", "eraser", "Eraser (E)", "E")]

    def __init__(self, main):
        super().__init__()
        self.main = main
        self.setObjectName("Page")
        self.board_id = None
        self.item_id = None
        self.version_id = None
        self.base_num = 0
        self.drop_pos = QPointF(0, 0)
        self.dirty = False
        self._await_snip = 0
        self._loading = False
        self.scene = SlideScene()
        self.view = SlideView(self.scene)
        self._build()
        self._wire_scene()
        QApplication.clipboard().dataChanged.connect(self._clipboard_changed)

    # ---- layout
    def _build(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        top = QWidget()
        top.setObjectName("TopBar")
        tl = QHBoxLayout(top)
        tl.setContentsMargins(10, 8, 12, 8)
        tl.setSpacing(8)
        self.back = tool_button("back", "Back to board (Esc)", size=18)
        self.back.clicked.connect(self.request_close)
        tl.addWidget(self.back)
        self.crumb = label("", "Crumb")
        tl.addWidget(self.crumb)
        tl.addWidget(label("›", "Faint"))
        self.title = QLineEdit()
        self.title.setObjectName("TitleEdit")
        self.title.setPlaceholderText("Untitled evidence")
        self.title.setMinimumWidth(280)
        self.title.textEdited.connect(self._mark_dirty)
        tl.addWidget(self.title, 1)
        self.ver_label = label("", "Hint")
        tl.addWidget(self.ver_label)
        self.undo_b = tool_button("undo", "Undo (Ctrl+Z)")
        self.undo_b.clicked.connect(self.undo)
        self.redo_b = tool_button("redo", "Redo (Ctrl+Y)")
        self.redo_b.clicked.connect(self.redo)
        tl.addWidget(self.undo_b)
        tl.addWidget(self.redo_b)
        tl.addWidget(vsep())
        self.cancel_b = push_button("Discard")
        self.cancel_b.setObjectName("Ghost")
        self.cancel_b.clicked.connect(self.discard)
        tl.addWidget(self.cancel_b)
        self.save_b = push_button("Save evidence", "save", primary=True)
        self.save_b.clicked.connect(lambda: self.save(True))
        tl.addWidget(self.save_b)
        self.save_more = QToolButton()
        self.save_more.setIcon(theme.icon("chevdown", 14))
        self.save_more.setPopupMode(QToolButton.InstantPopup)
        self.save_more.setToolTip("More save options")
        m = QMenu(self.save_more)
        self.act_overwrite = m.addAction(theme.icon("save", 16), "Update this version (don't stack a new one)",
                                         lambda: self.save(False))
        m.addAction(theme.icon("download", 16), "Export slide as PNG…", self.export_png)
        self.save_more.setMenu(m)
        tl.addWidget(self.save_more)
        root.addWidget(top)

        rib = QWidget()
        rib.setObjectName("Ribbon")
        rl = QHBoxLayout(rib)
        rl.setContentsMargins(10, 6, 10, 6)
        rl.setSpacing(4)
        self.tool_buttons = {}
        for key, icon_name, tip, _ in self.TOOLS[:3]:
            self._add_tool(rl, key, icon_name, tip)
        self.shape_btn = tool_button("shape_round", "Shapes (R)", True)
        self.shape_btn.setPopupMode(QToolButton.InstantPopup)
        sm = QMenu(self.shape_btn)
        for k, n in geometry.SHAPES:
            sm.addAction(theme.icon("shape_" + k, 16), n, lambda kk=k: self.set_tool("shape:" + kk))
        self.shape_btn.setMenu(sm)
        rl.addWidget(self.shape_btn)
        for key, icon_name, tip, _ in self.TOOLS[3:]:
            self._add_tool(rl, key, icon_name, tip)
        rl.addWidget(vsep())
        img = tool_button("image", "Insert picture…", text="Picture")
        img.clicked.connect(self.insert_image)
        rl.addWidget(img)
        snip = tool_button("snip", "Snip part of the screen (Win+Shift+S) and drop it in", text="Snip")
        snip.clicked.connect(self.snip)
        rl.addWidget(snip)
        tbl = tool_button("table", "Insert table…", text="Table")
        tbl.clicked.connect(self.insert_table)
        rl.addWidget(tbl)
        rl.addWidget(vsep())
        for name, tip, cmd in (("copy", "Copy (Ctrl+C)", "copy"), ("paste", "Paste (Ctrl+V)", "paste"),
                               ("delete", "Delete (Del)", "delete")):
            b = tool_button(name, tip)
            b.setFocusPolicy(Qt.NoFocus)
            b.clicked.connect(lambda _=False, c=cmd: self.command(c))
            rl.addWidget(b)
        rl.addStretch(1)
        self.page_prev = tool_button("up", "Previous page (Page Up)")
        self.page_prev.clicked.connect(lambda: self.view.show_page(self.scene.current_page - 1))
        self.page_label = label("Page 1 of 1", "Muted")
        self.page_next = tool_button("down", "Next page (Page Down)")
        self.page_next.clicked.connect(lambda: self.view.show_page(self.scene.current_page + 1))
        self.page_add = tool_button("add", "Add a page below this one — more room, shown stacked on the board",
                                    text="Page")
        self.page_add.clicked.connect(lambda: self.scene.add_page(self.scene.current_page))
        self.page_del = tool_button("delete", "Delete this page and everything on it")
        self.page_del.clicked.connect(lambda: self.delete_page(self.scene.current_page))
        for b in (self.page_prev, self.page_label, self.page_next, self.page_add, self.page_del):
            if b is not self.page_label:
                b.setFocusPolicy(Qt.NoFocus)
            rl.addWidget(b)
        rl.addWidget(vsep())
        zo = tool_button("zoomout", "Zoom out (Ctrl+wheel)")
        zo.clicked.connect(lambda: self.view.zoom_by(1 / 1.2))
        zi = tool_button("zoomin", "Zoom in")
        zi.clicked.connect(lambda: self.view.zoom_by(1.2))
        fit = tool_button("fit", "Fit page")
        fit.clicked.connect(self.view.fit)
        for b in (zo, zi, fit):
            b.setFocusPolicy(Qt.NoFocus)
            rl.addWidget(b)
        root.addWidget(rib)

        body = QHBoxLayout()
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(0)
        split = QSplitter(Qt.Vertical)
        split.addWidget(self.view)
        notes_box = QWidget()
        nl = QVBoxLayout(notes_box)
        nl.setContentsMargins(12, 8, 12, 10)
        nl.setSpacing(4)
        nh = QHBoxLayout()
        nh.addWidget(section_label("Notes · searchable, exported as speaker notes"))
        nh.addStretch(1)
        self.draft_b = QToolButton()
        self.draft_b.setText(" Draft notes")
        self.draft_b.setIcon(theme.icon("sparkle", 15, theme.COINV))
        self.draft_b.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        self.draft_b.setToolTip("Have the Co-Investigator draft notes from this slide and what it's connected to")
        self.draft_b.setStyleSheet(f"QToolButton {{ color: {theme.COINV}; }}")
        self.draft_b.clicked.connect(self.draft_notes)
        nh.addWidget(self.draft_b)
        nl.addLayout(nh)
        self._draft_run = None
        self._draft_timer = QTimer(self)
        self._draft_timer.setSingleShot(True)
        self._draft_timer.setInterval(70)
        self._draft_timer.timeout.connect(self._draft_flush)
        self.notes = QPlainTextEdit()
        self.notes.setPlaceholderText("Flesh out the details here: observations, parameters, file paths, ideas…")
        self.notes.textChanged.connect(self._notes_changed)
        nl.addWidget(self.notes)
        split.addWidget(notes_box)
        split.setStretchFactor(0, 5)
        split.setStretchFactor(1, 1)
        split.setSizes([700, 150])
        body.addWidget(split, 1)
        self.inspector = Inspector(self)
        body.addWidget(self.inspector)
        root.addLayout(body, 1)

        for key, _, _, sc in self.TOOLS:
            if sc:
                s = QShortcut(QKeySequence(sc), self.view)
                s.setContext(Qt.WidgetShortcut)
                s.activated.connect(lambda k=key: self._shortcut_tool(k))
        s = QShortcut(QKeySequence("R"), self.view)
        s.setContext(Qt.WidgetShortcut)
        s.activated.connect(lambda: self._shortcut_tool("shape:round"))
        for seq, fn in (("Ctrl+S", lambda: self.save(True)), ("Ctrl+Shift+S", lambda: self.save(False))):
            s = QShortcut(QKeySequence(seq), self)
            s.setContext(Qt.WidgetWithChildrenShortcut)
            s.activated.connect(fn)
        s = QShortcut(QKeySequence("Escape"), self.view)
        s.setContext(Qt.WidgetShortcut)
        s.activated.connect(self._escape)
        for seq, attr in (("Ctrl+B", "b_bold"), ("Ctrl+I", "b_ital"), ("Ctrl+U", "b_under")):
            s = QShortcut(QKeySequence(seq), self)
            s.setContext(Qt.WidgetWithChildrenShortcut)
            s.activated.connect(lambda a=attr: getattr(self.inspector, a).click())

    def _add_tool(self, lay, key, icon_name, tip):
        b = tool_button(icon_name, tip, True)
        b.setFocusPolicy(Qt.NoFocus)
        b.clicked.connect(lambda _=False, k=key: self.set_tool(k))
        lay.addWidget(b)
        self.tool_buttons[key] = b

    def _wire_scene(self):
        sc = self.scene
        sc.tool_reset.connect(lambda: self.set_tool("select"))
        sc.state_pushed.connect(self._state_pushed)
        sc.selectionChanged.connect(self._selection_changed)
        sc.edit_started.connect(lambda _: self._selection_changed())
        sc.edit_stopped.connect(self._selection_changed)
        sc.context_requested.connect(self._context_menu)
        self.view.crop_requested.connect(self.crop_item)
        self.view.files_dropped.connect(self._drop_files)
        self.view.image_dropped.connect(lambda img, pos: self._add_qimage(img, pos))
        self.view.text_dropped.connect(lambda t, pos: self.scene._create_text(pos, text=t))
        self.view.key_command.connect(self.command)
        self.view.page_changed.connect(self._page_changed)
        sc.state_pushed.connect(lambda: self._page_changed(sc.current_page))
        self._sel_timer = QTimer(self)
        self._sel_timer.setSingleShot(True)
        self._sel_timer.setInterval(30)
        self._sel_timer.timeout.connect(self.inspector.refresh)
        self._page_changed(0)

    # ---- open / close
    def open_new(self, board_id, pos, doc=None, title=""):
        self.board_id, self.item_id, self.version_id, self.base_num = board_id, None, None, 0
        self.drop_pos = QPointF(pos)
        if doc is None:
            from ..settings import settings
            w, h = PAGE_SIZES.get(settings.get("page_size", "16:9"), (1280, 720))
            doc = {"w": w, "h": h, "bg": "#ffffff", "items": []}
        self._load(doc, title)
        self.save_b.setText("Save evidence")
        self.act_overwrite.setVisible(False)
        self.ver_label.setText("New evidence")

    def open_item(self, item_id, version_id=None):
        st = context.store
        it = st.item(item_id)
        v = st.version(version_id) if version_id else st.current_version(item_id)
        if it is None or v is None:
            return False
        self.board_id, self.item_id = it["board_id"], item_id
        self.version_id = v["id"]
        self.base_num = it["vnum"] or v["num"]
        self._load(v["doc"], v["title"] or it["title"] or "")
        nxt = self.base_num + 1
        self.save_b.setText(f"Save as v{nxt}")
        self.act_overwrite.setVisible(version_id is None or version_id == it["ver"])
        src = f"from v{v['num']}" if v["id"] != it["ver"] else f"v{v['num']}"
        self.ver_label.setText(f"Editing {src} · saving stacks v{nxt} on top")
        return True

    def _page_changed(self, k):
        n = self.scene.pages
        self.page_label.setText(f"Page {k + 1} of {n}")
        for w in (self.page_prev, self.page_label, self.page_next, self.page_del):
            w.setVisible(n > 1)
        self.page_prev.setEnabled(k > 0)
        self.page_next.setEnabled(k < n - 1)

    def delete_page(self, k):
        if self.scene.delete_page(k):
            self.view.show_page(min(k, self.scene.pages - 1))
            self.main.toast(f"Page {k + 1} deleted — Ctrl+Z brings it back")

    def _load(self, doc, title):
        self._loading = True
        self.scene.current_page = 0
        b = context.store.board(self.board_id)
        p = context.store.project(b["project_id"]) if b else None
        self.crumb.setText(f"{p['name'] if p else ''}  ›  {b['name'] if b else ''}")
        self.scene.load(doc)
        self.title.setText(title)
        self.notes.setPlainText(doc.get("notes", ""))
        self.set_tool("select")
        self.dirty = False
        self._loading = False
        self.view.fit_mode = True
        QTimer.singleShot(0, self.view.fit)
        self.inspector.refresh()
        self._update_undo()
        self.view.setFocus()

    def _mark_dirty(self, *_):
        if not self._loading:
            self.dirty = True

    def _notes_changed(self):
        self.scene.notes = self.notes.toPlainText()
        self._mark_dirty()

    def _state_pushed(self):
        if not self._loading and self.scene.hidx > 0:
            self.dirty = True
        self._update_undo()
        self._sel_timer.start()

    def _update_undo(self):
        self.undo_b.setEnabled(self.scene.can_undo())
        self.redo_b.setEnabled(self.scene.can_redo())

    def _selection_changed(self):
        self._sel_timer.start()

    def request_close(self):
        self.scene.stop_text_edit()
        if self.dirty:
            r = QMessageBox.question(self, "Unsaved evidence", "Save your changes before leaving?",
                                     QMessageBox.Save | QMessageBox.Discard | QMessageBox.Cancel, QMessageBox.Save)
            if r == QMessageBox.Cancel:
                return
            if r == QMessageBox.Save:
                self.save(True)
                return
        self._finish(saved=False)

    def discard(self):
        self.scene.stop_text_edit()
        if self.dirty and QMessageBox.question(self, "Discard changes", "Throw away the changes to this evidence?",
                                               QMessageBox.Discard | QMessageBox.Cancel) != QMessageBox.Discard:
            return
        self._finish(saved=False)

    def _finish(self, saved, created=False):
        self.dirty = False
        self.finished.emit({"board_id": self.board_id, "item_id": self.item_id, "created": created, "saved": saved})

    def _escape(self):
        sc = self.scene
        if sc.editing is not None:
            sc.stop_text_edit()
            return
        if sc.tool != "select":
            self.set_tool("select")
        elif sc.selectedItems():
            sc.clearSelection()
        else:
            self.request_close()

    # ---- save
    def auto_title(self):
        for line in self.scene.plain_text().splitlines():
            if line.strip():
                return line.strip()[:80]
        return time.strftime("Evidence %b %d, %I:%M %p")

    def save(self, new_version=True):
        from ..board.items import evidence_card_height
        sc = self.scene
        sc.stop_text_edit()
        st = context.store
        doc = sc.to_doc()
        text = sc.plain_text()
        title = self.title.text().strip() or self.auto_title()
        thumb = media.thumb_asset(st, sc.render_image(960))
        aspect = doc_aspect(doc)  # every page, stacked
        created = False
        if self.item_id is None:
            w = 300.0 if sc.page_h / sc.page_w <= 1.05 else 230.0
            h = evidence_card_height(w, aspect)
            row = st.new_item(self.board_id, "evidence", self.drop_pos.x() - w / 2, self.drop_pos.y() - h / 2, w, h,
                              {"aspect": aspect})
            st.put_item(row)
            st.add_version(row["id"], title, doc, text, thumb)
            self.item_id = row["id"]
            created = True
        else:
            if new_version or self.version_id is None:
                st.add_version(self.item_id, title, doc, text, thumb)
            else:
                st.overwrite_version(self.version_id, title, doc, text, thumb)
            it = st.item(self.item_id)
            if it and abs(it["data"].get("aspect", 0.5625) - aspect) > 1e-3:
                it["data"]["aspect"] = aspect
                it["h"] = evidence_card_height(it["w"], aspect)
                st.put_item(it)
        self._finish(saved=True, created=created)

    # ---- AI-drafted notes
    def draft_notes(self):
        """Streams Co-Investigator notes for this slide into the notes pane (one Ctrl+Z removes them)."""
        import re as _re
        from ..ai import casefile, prompts
        from ..ai.providers import describe, role_config
        from ..ai.runner import Run, qimage_b64
        if self._draft_run is not None:
            self._draft_run.stop()
            return
        cfg = role_config("notes")
        if cfg["provider"] == "none":
            self.main.open_ai_settings()
            cfg = role_config("notes")
            if cfg["provider"] == "none":
                return
        sc = self.scene
        sc.stop_text_edit()
        title = self.title.text().strip()
        notes = self.notes.toPlainText()
        slide_text = "\n".join(t for t in (it.text_content() for it in sc.sorted_items()) if t and t.strip())
        brief = casefile.evidence_brief(title, slide_text, notes, self.item_id, self.board_id)
        system = prompts.with_persona(prompts.NOTES_SYSTEM + ("" if title else prompts.TITLE_RULE),
                                      cfg.get("persona"), notes=True)
        if cfg["provider"] == "manual":
            QApplication.clipboard().setText(f"{system}\n\n{brief}")
            if cfg.get("site"):
                QDesktopServices.openUrl(QUrl(cfg["site"]))
            self.main.toast("Prompt copied — paste it into your chatbot (with a screenshot of the slide), "
                            "then paste the reply into the notes", 5000)
            return
        msgs = [{"role": "user", "text": brief + "\n\nThe rendered slide is attached as an image.",
                 "images": [qimage_b64(sc.render_image(1280))]}]
        self._draft_title_needed = not title
        cur = self.notes.textCursor()
        cur.movePosition(QTextCursor.End)
        cur.beginEditBlock()
        cur.insertText(("\n\n" if notes.strip() else "") +
                       f"✦ Co-Investigator draft ({describe(cfg)}, {time.strftime('%b %d %Y')}):\n")
        cur.endEditBlock()
        self._draft_start = cur.position()
        self._draft_buf = ""
        self._draft_re = _re
        self.notes.setReadOnly(True)
        run = Run(cfg, system, msgs, max_tokens=1200)
        run.chunk.connect(self._draft_chunk)
        run.done.connect(self._draft_done)
        run.failed.connect(self._draft_failed)
        self._draft_run = run
        self.draft_b.setText(" Stop drafting")
        self.draft_b.setIcon(theme.icon("square", 14, theme.COINV))
        run.start()

    def _draft_chunk(self, piece):
        self._draft_buf += piece
        if not self._draft_timer.isActive():
            self._draft_timer.start()

    def _draft_split(self, text):
        """Separates an optional first title line ('TITLE: …', 'Title: …', '**Evidence title:** …') from the notes."""
        re_ = self._draft_re
        head = r"\s*[*_#]*\s*(?:evidence\s+|suggested\s+)?title\s*[*_]*\s*:"
        m = re_.match(head + r"\s*(.+?)\s*\n", text, re_.I)
        if m:
            return m.group(1).strip(" *_\"'"), text[m.end():]
        first = text.lstrip(" *_#").lower()
        if re_.match(head, text, re_.I) or ("\n" not in text and len(first) < 16 and
                                            ("evidence title".startswith(first) or "title".startswith(first[:5]))):
            return None, ""  # the title line is still arriving
        return None, text

    def _draft_flush(self):
        _, body = self._draft_split(self._draft_buf)
        cur = QTextCursor(self.notes.document())
        cur.joinPreviousEditBlock()
        cur.setPosition(self._draft_start)
        cur.movePosition(QTextCursor.End, QTextCursor.KeepAnchor)
        cur.insertText(body.lstrip("\n"))
        cur.endEditBlock()
        self.notes.moveCursor(QTextCursor.End)

    def _draft_finish(self):
        self._draft_run = None
        self._draft_timer.stop()
        self.notes.setReadOnly(False)
        self.draft_b.setText(" Draft notes")
        self.draft_b.setIcon(theme.icon("sparkle", 15, theme.COINV))

    def _draft_done(self, text):
        self._draft_buf = text or self._draft_buf
        self._draft_flush()
        t, _ = self._draft_split(self._draft_buf + "\n")
        if t and self._draft_title_needed and not self.title.text().strip():
            self.title.setText(t)
        self._draft_finish()
        self._mark_dirty()
        self.main.toast("Notes drafted by the Co-Investigator — Ctrl+Z in the notes removes them")

    def _draft_failed(self, msg, login):
        self._draft_finish()
        self.notes.undo()  # take back the header line
        box = QMessageBox(QMessageBox.Warning, "Couldn't draft notes", msg, QMessageBox.Ok, self)
        from ..ai.providers import open_sign_in, role_config, sign_in_label
        cfg = role_config("notes")
        if login:
            b = box.addButton(sign_in_label(cfg), QMessageBox.ActionRole)
        else:
            b = box.addButton("AI settings…", QMessageBox.ActionRole)
        box.exec()
        if box.clickedButton() is b:
            if login:
                open_sign_in(cfg)
            else:
                self.main.open_ai_settings()

    def export_png(self):
        path, _ = QFileDialog.getSaveFileName(self, "Export slide", (self.title.text() or "evidence") + ".png",
                                              "PNG image (*.png)")
        if path:
            self.scene.render_image(2560).save(path)
            self.main.toast("Slide exported")

    # ---- tools & commands
    def set_tool(self, tool):
        sc = self.scene
        if tool != "select":
            sc.stop_text_edit()
        sc.tool = tool
        for k, b in self.tool_buttons.items():
            b.setChecked(k == tool)
        self.shape_btn.setChecked(tool.startswith("shape:"))
        if tool.startswith("shape:"):
            self.shape_btn.setIcon(theme.icon("shape_" + tool.split(":")[1], 18))
        self.view.setDragMode(QGraphicsView.RubberBandDrag if tool == "select" else QGraphicsView.NoDrag)
        cur = {"select": None, "text": Qt.IBeamCursor, "code": Qt.IBeamCursor, "eraser": Qt.PointingHandCursor}
        c = cur.get(tool, Qt.CrossCursor)
        if c is None:
            self.view.viewport().unsetCursor()
        else:
            self.view.viewport().setCursor(c)
        self.inspector.refresh()

    def _shortcut_tool(self, key):
        if self.scene.editing is None:
            self.set_tool(key)

    def undo(self):
        self.scene.undo()
        self.inspector.refresh()

    def redo(self):
        self.scene.redo()
        self.inspector.refresh()

    def command(self, cmd):
        sc = self.scene
        if cmd == "undo":
            self.undo()
        elif cmd == "redo":
            self.redo()
        elif cmd == "copy":
            self.copy()
        elif cmd == "cut":
            self.copy()
            sc.delete_selected()
        elif cmd == "paste":
            self.paste()
        elif cmd == "duplicate":
            sc.duplicate()
        elif cmd == "selectall":
            for it in sc.slide_items():
                if it.isVisible():
                    it.setSelected(True)
        elif cmd == "delete":
            sc.delete_selected()
        elif cmd == "group":
            if sc.group_selected():
                self.main.toast("Grouped — Ctrl+Shift+G ungroups")
        elif cmd == "ungroup":
            sc.ungroup_selected()
        elif cmd in ("sup", "sub"):
            self.set_script(cmd)
        elif cmd in ("b", "i", "u", "s"):
            getattr(self.inspector, {"b": "b_bold", "i": "b_ital", "u": "b_under", "s": "b_strike"}[cmd]).click()
        elif cmd in textmarks.LIST_STYLE:
            self.inspector._list(textmarks.LIST_STYLE[cmd])
        self.inspector.refresh()

    def set_script(self, kind):
        """Superscript / subscript the selected text (or the whole of the selected text boxes); again = normal.

        With no text to change, Ctrl+= / Ctrl+- zoom like everywhere else."""
        targets, editing = self.inspector._text_targets()
        if not targets:
            self.view.zoom_by(1.2 if kind == "sup" else 1 / 1.2)
            return
        al = textmarks.ALIGN_OF[kind]
        t = targets[0].text
        if editing:
            c = t.textCursor()
            if c.hasSelection():
                c = QTextCursor(t.document())
                c.setPosition(t.textCursor().selectionStart() + 1)
        else:
            c = QTextCursor(t.document())
            c.movePosition(QTextCursor.Start)
            c.movePosition(QTextCursor.Right)
        new = QTextCharFormat.AlignNormal if c.charFormat().verticalAlignment() == al else al
        self.inspector._char(lambda f: f.setVerticalAlignment(new))
        self.inspector.refresh()

    def copy(self):
        sel = sorted([i for i in self.scene.selectedItems() if isinstance(i, SlideItem)], key=lambda i: i.zValue())
        if not sel:
            return
        from PySide6.QtCore import QMimeData
        md = QMimeData()
        md.setData(SLIDE_MIME, json.dumps([i.to_dict() for i in sel]).encode())
        txt = "\n".join(i.text_content() for i in sel if i.text_content())
        if txt:
            md.setText(txt)
        QApplication.clipboard().setMimeData(md)

    def paste(self):
        md = QApplication.clipboard().mimeData()
        sc = self.scene
        center = sc.page_center()
        top = sc.page_rects()[sc.current_page].topLeft()
        if md.hasFormat(SLIDE_MIME):
            sc.paste_dicts(json.loads(bytes(md.data(SLIDE_MIME)).decode()))
        elif md.hasImage():
            self._add_qimage(QImage(md.imageData()), None)
        elif md.hasUrls() and any(u.isLocalFile() for u in md.urls()):
            self._drop_files([u.toLocalFile() for u in md.urls() if u.isLocalFile()], center)
        elif md.hasText() and md.text().strip():
            sc._create_text(top + QPointF(sc.page_w * 0.1, sc.page_h * 0.15), text=md.text())

    def _add_qimage(self, img, pos):
        if img.isNull():
            return
        h, w, hh = media.import_qimage(context.store, img)
        self.scene.add_image(h, w, hh, "Pasted image", at=pos)
        self.set_tool("select")

    def _drop_files(self, paths, pos):
        x = 0
        for p in paths:
            if Path(p).suffix.lower() in IMAGE_EXTS:
                r = media.import_image_file(context.store, p)
                if r:
                    h, w, hh = r
                    self.scene.add_image(h, w, hh, Path(p).name, at=QPointF(pos.x() + x, pos.y() + x))
                    x += 30
            else:
                self.scene._create_text(QPointF(pos.x() + x, pos.y() + x), text=f"📎 {Path(p).name}\n{p}")
                x += 30

    def insert_image(self):
        paths, _ = QFileDialog.getOpenFileNames(self, "Insert pictures", "", IMAGE_FILTER)
        if paths:
            self._drop_files(paths, self.scene.page_center())

    def replace_image(self):
        sel = [i for i in self.scene.selectedItems() if isinstance(i, SlideImage)]
        if not sel:
            return
        path, _ = QFileDialog.getOpenFileName(self, "Replace picture", "", IMAGE_FILTER)
        if not path:
            return
        r = media.import_image_file(context.store, path)
        if not r:
            return
        h, w, hh = r
        it = sel[0]
        c = it.mapToScene(QPointF(it.w / 2, it.h / 2))
        it.asset, it.iw, it.ih, it.crop, it.name = h, w, hh, [0, 0, w, hh], Path(path).name
        nh = it.w * hh / max(1, w)
        it.set_size(it.w, nh)
        it.setPos(c - QPointF(it.w / 2, it.h / 2))
        self.scene.push_state()

    def insert_table(self):
        d = QDialog(self)
        d.setWindowTitle("Insert table")
        lay = QVBoxLayout(d)
        r = QSpinBox()
        r.setRange(1, 40)
        r.setValue(3)
        c = QSpinBox()
        c.setRange(1, 20)
        c.setValue(3)
        hdr = QCheckBox("Header row")
        hdr.setChecked(True)
        row = QHBoxLayout()
        row.addWidget(label("Rows", "Muted"))
        row.addWidget(r)
        row.addWidget(label("Columns", "Muted"))
        row.addWidget(c)
        lay.addLayout(row)
        lay.addWidget(hdr)
        ok = push_button("Insert", primary=True)
        ok.clicked.connect(d.accept)
        lay.addWidget(ok)
        if d.exec() != QDialog.Accepted:
            return
        sc = self.scene
        rows, cols = r.value(), c.value()
        cells = "".join(
            "<tr>" + "".join(
                (f"<th style='background:#eef0f4;padding:6px'>Header {j + 1}</th>" if (i == 0 and hdr.isChecked())
                 else "<td style='padding:6px'>&nbsp;</td>") for j in range(cols)) + "</tr>"
            for i in range(rows))
        html = (f"<table border='1' cellspacing='0' cellpadding='6' width='100%' "
                f"style='border-collapse:collapse;border-color:#9aa0ad'>{cells}</table>")
        top = sc.page_rects()[sc.current_page].topLeft()
        it = SlideText({"x": top.x() + sc.page_w * 0.1, "y": top.y() + sc.page_h * 0.2, "w": sc.page_w * 0.8, "h": 40,
                        "font": sc.opts["font"], "size": 20, "color": "#1d1f24", "html": html, "fill": "#ffffff",
                        "radius": 4})
        sc.add(it)
        sc.push_state()

    def snip(self):
        self._await_snip = time.time()
        QDesktopServices.openUrl(QUrl("ms-screenclip:"))
        self.main.toast("Select a region — it will drop into the slide")

    def _clipboard_changed(self):
        if not self._await_snip or not self.isVisible():
            return
        if time.time() - self._await_snip > 120:
            self._await_snip = 0
            return
        md = QApplication.clipboard().mimeData()
        if md.hasImage():
            self._await_snip = 0
            img = QImage(md.imageData())
            QTimer.singleShot(50, lambda: self._add_qimage(img, None))
            self.main.raise_and_activate()

    def crop_selected(self):
        sel = [i for i in self.scene.selectedItems() if isinstance(i, SlideImage)]
        if sel:
            self.crop_item(sel[0])

    def crop_item(self, it):
        d = CropDialog(it, self)
        if d.exec() == QDialog.Accepted:
            it.set_crop(d.result_crop())
            self.scene.push_state()

    def _context_menu(self, screen_pos, item):
        sc = self.scene
        m = QMenu(self)
        if item is not None:
            if isinstance(item, TextBearing):
                m.addAction(theme.icon("edit", 16), "Edit text", lambda: sc.start_text_edit(item))
            if isinstance(item, SlideImage):
                m.addAction(theme.icon("crop", 16), "Crop…", lambda: self.crop_item(item))
                m.addAction(theme.icon("image", 16), "Replace…", self.replace_image)
            m.addSeparator()
            m.addAction(theme.icon("cut", 16), "Cut", lambda: self.command("cut"))
            m.addAction(theme.icon("copy", 16), "Copy", lambda: self.command("copy"))
            m.addAction(theme.icon("paste", 16), "Paste", lambda: self.command("paste"))
            m.addAction(theme.icon("copy", 16), "Duplicate", lambda: self.command("duplicate"))
            m.addSeparator()
            for name, how, txt in (("front", "front", "Bring to front"), ("forward", "forward", "Bring forward"),
                                   ("backward", "backward", "Send backward"), ("back", "back", "Send to back")):
                m.addAction(theme.icon(name, 16), txt, lambda h=how: sc.reorder(h))
            m.addSeparator()
            sel = sc.selected()
            if len(sel) >= 2 and sc.selected_group() is None:
                m.addAction(theme.icon("group", 16), "Group\tCtrl+G", lambda: self.command("group"))
            if any(i.group in sc.groups() for i in sel):
                m.addAction(theme.icon("ungroup", 16), "Ungroup\tCtrl+Shift+G", lambda: self.command("ungroup"))
            m.addAction(theme.icon("unlock" if item.locked else "lock", 16), "Unlock" if item.locked else "Lock",
                        lambda: (item.set_locked(not item.locked), sc.update(), sc.push_state(),
                                 self.inspector.refresh()))
            m.addAction(theme.icon("delete", 16), "Delete", lambda: self.command("delete"))
        else:
            m.addAction(theme.icon("paste", 16), "Paste", lambda: self.command("paste"))
            m.addAction(theme.icon("image", 16), "Insert picture…", self.insert_image)
            m.addAction(theme.icon("selectall", 16), "Select all", lambda: self.command("selectall"))
            hidden = [i for i in sc.slide_items() if not i.isVisible()]
            if hidden:
                m.addAction(theme.icon("view", 16), "Show hidden items",
                            lambda: ([i.setVisible(True) for i in hidden], sc.push_state()))
            m.addSeparator()
            k = sc.page_at(sc.ctx_pos.y())
            m.addAction(theme.icon("add", 16), "Add page below", lambda: sc.add_page(k))
            if sc.pages > 1:
                m.addAction(theme.icon("delete", 16), f"Delete page {k + 1}", lambda: self.delete_page(k))
        m.exec(screen_pos)
