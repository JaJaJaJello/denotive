"""Small reusable widgets: icon buttons, colour pickers, toasts, separators, flow layout."""
from datetime import datetime, timedelta

from PySide6.QtCore import QEasingCurve, QPoint, QPropertyAnimation, QRect, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import (QColorDialog, QFrame, QGraphicsOpacityEffect, QGridLayout, QHBoxLayout, QLabel,
                               QLayout, QPushButton, QSizePolicy, QToolButton, QVBoxLayout, QWidget)

from .. import theme


def shift_as_ctrl(e):
    """Qt only treats Ctrl-click as 'add to selection'; make Shift-click do the same."""
    from PySide6.QtGui import QMouseEvent
    mods = e.modifiers()
    if mods & Qt.ShiftModifier and not mods & Qt.ControlModifier:
        return QMouseEvent(e.type(), e.position(), e.globalPosition(), e.button(), e.buttons(),
                           mods | Qt.ControlModifier)
    return e


def tool_button(icon_name, tip="", checkable=False, size=18, text=None, parent=None, color=theme.TEXT):
    b = QToolButton(parent)
    b.setIcon(theme.icon(icon_name, size, color))
    b.setIconSize(QSize(size, size))
    b.setCheckable(checkable)
    b.setCursor(Qt.PointingHandCursor)
    if text:
        b.setText(text)
        b.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        b.setObjectName("TextTool")
    if tip:
        b.setToolTip(tip)
    return b


def push_button(text, icon_name=None, primary=False, name=None, size=16):
    b = QPushButton(text)
    if icon_name:
        b.setIcon(theme.icon(icon_name, size, "#ffffff" if primary else theme.TEXT))
        b.setIconSize(QSize(size, size))
    if primary:
        b.setObjectName("Primary")
    elif name:
        b.setObjectName(name)
    b.setCursor(Qt.PointingHandCursor)
    return b


def vsep(h=22):
    f = QFrame()
    f.setObjectName("Separator")
    f.setFixedSize(1, h)
    return f


def hsep():
    f = QFrame()
    f.setObjectName("Separator")
    f.setFixedHeight(1)
    return f


def label(text, name=None, wrap=False):
    lab = QLabel(text)
    if name:
        lab.setObjectName(name)
    lab.setWordWrap(wrap)
    return lab


def section_label(text):
    return label(text.upper(), "SectionLabel")


def nice_time(ts, with_time=True):
    if not ts:
        return ""
    d = datetime.fromtimestamp(ts)
    today = datetime.now().date()
    t = d.strftime("%I:%M %p").lstrip("0")
    if d.date() == today:
        return f"Today {t}" if with_time else "Today"
    if d.date() == today - timedelta(days=1):
        return f"Yesterday {t}" if with_time else "Yesterday"
    if (today - d.date()).days < 7:
        return d.strftime("%A") + (f" {t}" if with_time else "")
    fmt = "%b %d" if d.year == today.year else "%b %d, %Y"
    return d.strftime(fmt).replace(" 0", " ") + (f", {t}" if with_time else "")


def date_group(ts):
    d = datetime.fromtimestamp(ts).date()
    today = datetime.now().date()
    days = (today - d).days
    if days <= 0:
        return "Today"
    if days == 1:
        return "Yesterday"
    if days < 7:
        return "Previous 7 days"
    if days < 30:
        return "Previous 30 days"
    if d.year == today.year:
        return d.strftime("%B")
    return d.strftime("%B %Y")


def human_size(n):
    if n is None:
        return ""
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    return ""


class SwatchPopup(QFrame):
    picked = Signal(object)  # str colour or None

    def __init__(self, colors, allow_none=False, current=None, parent=None):
        super().__init__(parent, Qt.Popup | Qt.FramelessWindowHint | Qt.NoDropShadowWindowHint)
        self.setObjectName("Overlay")
        self.setAttribute(Qt.WA_TranslucentBackground, False)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(10, 10, 10, 10)
        lay.setSpacing(8)
        grid = QGridLayout()
        grid.setSpacing(6)
        cols = 8
        for i, c in enumerate(colors):
            b = QToolButton()
            b.setObjectName("Swatch")
            b.setFixedSize(22, 22)
            b.setCheckable(True)
            b.setChecked(bool(current) and QColor(current).name() == QColor(c).name())
            b.setStyleSheet(f"QToolButton#Swatch {{ background: {c}; }}")
            b.setToolTip(c)
            b.clicked.connect(lambda _=False, col=c: self._pick(col))
            grid.addWidget(b, i // cols, i % cols)
        lay.addLayout(grid)
        row = QHBoxLayout()
        if allow_none:
            nb = QPushButton("None")
            nb.setObjectName("Ghost")
            nb.clicked.connect(lambda: self._pick(None))
            row.addWidget(nb)
        cb = QPushButton("Custom…")
        cb.setObjectName("Ghost")
        cb.clicked.connect(lambda: self._custom(current))
        row.addWidget(cb)
        row.addStretch(1)
        lay.addLayout(row)

    def _pick(self, c):
        self.picked.emit(c)
        self.close()

    def _custom(self, current):
        self.hide()
        c = QColorDialog.getColor(QColor(current or "#ffffff"), self.parentWidget(), "Pick a colour",
                                  QColorDialog.ShowAlphaChannel)
        if c.isValid():
            self.picked.emit(c.name(QColor.HexArgb) if c.alpha() < 255 else c.name())
        self.close()


class ColorButton(QToolButton):
    """Shows the current colour (optionally under a glyph); click opens a swatch popup."""
    colorChanged = Signal(object)

    def __init__(self, color="#ffffff", allow_none=False, glyph=None, tip="", colors=None, parent=None):
        super().__init__(parent)
        self._color = color
        self.allow_none = allow_none
        self.glyph = glyph
        self.colors = colors or theme.SWATCHES
        self.setToolTip(tip)
        self.setFixedSize(34, 30)
        self.setCursor(Qt.PointingHandCursor)
        self.clicked.connect(self._open)

    def color(self):
        return self._color

    def setColor(self, c, emit=False):
        self._color = c
        self.update()
        if emit:
            self.colorChanged.emit(c)

    def _open(self):
        pop = SwatchPopup(self.colors, self.allow_none, self._color, self.window())
        pop.picked.connect(lambda c: self.setColor(c, emit=True))
        pop.adjustSize()
        g = self.mapToGlobal(QPoint(0, self.height() + 4))
        pop.move(g)
        pop.show()

    def paintEvent(self, e):
        super().paintEvent(e)
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        if self.glyph:
            p.setFont(theme.icon_font(15))
            p.setPen(QColor(theme.TEXT))
            p.drawText(QRect(0, 1, self.width(), self.height() - 9), Qt.AlignCenter, theme.GLYPHS.get(self.glyph, ""))
            r = QRect(8, self.height() - 8, self.width() - 16, 4)
        else:
            r = QRect(7, 7, self.width() - 14, self.height() - 14)
        if self._color is None:
            p.setPen(QPen(QColor(theme.MUTED), 1.2))
            p.setBrush(Qt.NoBrush)
            p.drawRoundedRect(r, 3, 3)
            p.setPen(QPen(QColor(theme.ACCENT), 1.5))
            p.drawLine(r.bottomLeft(), r.topRight())
        else:
            p.setPen(QPen(QColor(255, 255, 255, 50), 1))
            p.setBrush(QColor(self._color))
            p.drawRoundedRect(r, 3, 3)
        p.end()


class SwatchRow(QWidget):
    """A row of round colour chips; used for string/card colours."""
    picked = Signal(str)

    def __init__(self, named_colors, size=22, parent=None):
        super().__init__(parent)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(6)
        self.buttons = {}
        for name, c in named_colors:
            b = QToolButton()
            b.setObjectName("Swatch")
            b.setFixedSize(size, size)
            b.setCheckable(True)
            b.setAutoExclusive(False)
            b.setToolTip(name)
            b.setStyleSheet(f"QToolButton#Swatch {{ background: {c}; border-radius: {size // 2}px; }}")
            b.clicked.connect(lambda _=False, col=c: self._pick(col))
            lay.addWidget(b)
            self.buttons[c.lower()] = b
        lay.addStretch(1)

    def set_current(self, c):
        for k, b in self.buttons.items():
            b.setChecked(bool(c) and k == c.lower())

    def _pick(self, c):
        self.set_current(c)
        self.picked.emit(c)


class Toast(QLabel):
    def __init__(self, parent):
        super().__init__(parent)
        self.setObjectName("Toast")
        self.hide()
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self._fade)
        self._fx = QGraphicsOpacityEffect(self)
        self.setGraphicsEffect(self._fx)
        self._anim = QPropertyAnimation(self._fx, b"opacity", self)
        self._anim.setDuration(260)
        self._anim.setEasingCurve(QEasingCurve.OutCubic)
        self._anim.finished.connect(self._done)

    def show_message(self, text, ms=2200):
        self.setText(text)
        self.adjustSize()
        par = self.parentWidget()
        self.move((par.width() - self.width()) // 2, par.height() - self.height() - 36)
        self._anim.stop()
        self._fx.setOpacity(1.0)
        self.show()
        self.raise_()
        self._timer.start(ms)

    def _fade(self):
        self._anim.setStartValue(1.0)
        self._anim.setEndValue(0.0)
        self._anim.start()

    def _done(self):
        if self._fx.opacity() < 0.05:
            self.hide()


class FlowLayout(QLayout):
    def __init__(self, parent=None, spacing=8):
        super().__init__(parent)
        self._items = []
        self._sp = spacing
        self.setContentsMargins(0, 0, 0, 0)

    def addItem(self, item):
        self._items.append(item)

    def count(self):
        return len(self._items)

    def itemAt(self, i):
        return self._items[i] if 0 <= i < len(self._items) else None

    def takeAt(self, i):
        return self._items.pop(i) if 0 <= i < len(self._items) else None

    def expandingDirections(self):
        return Qt.Orientations(0)

    def hasHeightForWidth(self):
        return True

    def heightForWidth(self, w):
        return self._do(QRect(0, 0, w, 0), True)

    def setGeometry(self, rect):
        super().setGeometry(rect)
        self._do(rect, False)

    def sizeHint(self):
        return self.minimumSize()

    def minimumSize(self):
        s = QSize()
        for it in self._items:
            s = s.expandedTo(it.minimumSize())
        return s

    def _do(self, rect, test):
        x, y, line_h = rect.x(), rect.y(), 0
        for it in self._items:
            sz = it.sizeHint()
            nx = x + sz.width() + self._sp
            if nx - self._sp > rect.right() and line_h > 0:
                x = rect.x()
                y += line_h + self._sp
                nx = x + sz.width() + self._sp
                line_h = 0
            if not test:
                it.setGeometry(QRect(QPoint(x, y), sz))
            x = nx
            line_h = max(line_h, sz.height())
        return y + line_h - rect.y()


class ClickLabel(QLabel):
    clicked = Signal()

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton:
            self.clicked.emit()
        super().mousePressEvent(e)


def clear_layout(lay):
    while lay.count():
        it = lay.takeAt(0)
        w = it.widget()
        if w is not None:
            w.setParent(None)
            w.deleteLater()
        elif it.layout() is not None:
            clear_layout(it.layout())


def expanding():
    w = QWidget()
    w.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
    return w
