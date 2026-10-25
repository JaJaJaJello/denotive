"""Colors, stylesheet and icons (glyphs from Segoe Fluent Icons plus a few hand-drawn ones)."""
import math

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import (QBrush, QColor, QFont, QFontDatabase, QIcon, QPainter, QPainterPath, QPen, QPixmap,
                           QRadialGradient)

from . import geometry

BG = "#0f1115"
PANEL = "#14161b"
SURFACE = "#1a1d23"
SURFACE2 = "#22262e"
SURFACE3 = "#2c313a"
BORDER = "#262a33"
BORDER2 = "#343944"
TEXT = "#e9eaee"
MUTED = "#8c919d"
FAINT = "#5d626e"
ACCENT = "#e5484d"
ACCENT_HOVER = "#f0595e"
ACCENT_DIM = "#3c1b20"
SELECT = "#5b8cff"
GOLD = "#f5b544"
COINV = "#b69cff"        # the Co-Investigator's colour
COINV_DIM = "#2a2340"
COINV_PAPER = "#e6defd"  # paper for notes it writes on the board
OK = "#3dd68c"

UI_FONT = "Segoe UI Variable Text"
MONO_FONT = "Consolas"

# palettes offered by the colour pickers
SWATCHES = ["#ffffff", "#f6f4ee", "#fff3a3", "#ffd6a5", "#ffadad", "#ffc6ff", "#bdb2ff", "#a0c4ff",
            "#9bf6ff", "#caffbf", "#1d1f24", "#2b2f38", "#5b6170", "#9aa0ad",
            "#e5484d", "#f76b15", "#f5b544", "#3dd68c", "#12a594", "#0090ff", "#5b8cff", "#8e4ec6",
            "#d6409f", "#000000"]
STRING_COLORS = [("Red", "#d7263d"), ("Crimson", "#9e1b32"), ("Orange", "#f76b15"), ("Yellow", "#f5c400"),
                 ("Green", "#2fb36d"), ("Teal", "#12a594"), ("Blue", "#2f7cf6"), ("Purple", "#8e4ec6"),
                 ("White", "#f2f2f2"), ("Black", "#15161a")]
CARD_COLORS = [("Paper", "#f6f4ee"), ("White", "#ffffff"), ("Post-it", "#fff09a"), ("Peach", "#ffd2b0"),
               ("Rose", "#ffc4cc"), ("Lilac", "#dcd0ff"), ("Sky", "#c6e3ff"), ("Mint", "#c9f2d6"),
               ("Graphite", "#2a2e36")]
PROJECT_COLORS = ["#e5484d", "#f76b15", "#f5b544", "#3dd68c", "#12a594", "#0090ff", "#5b8cff",
                  "#8e4ec6", "#d6409f", "#9aa0ad"]

GLYPHS = {
    "add": "", "delete": "", "edit": "", "save": "", "search": "",
    "back": "", "forward": "", "chevdown": "", "chevright": "", "more": "",
    "settings": "", "folder": "", "folderopen": "", "doc": "", "page": "",
    "copy": "", "paste": "", "cut": "", "undo": "", "redo": "",
    "zoomin": "", "zoomout": "", "fit": "", "image": "", "font": "",
    "bold": "", "italic": "", "underline": "", "strike": "",
    "alignleft": "", "aligncenter": "", "alignright": "", "bullets": "",
    "link": "", "pin": "", "print": "", "share": "", "download": "",
    "upload": "", "calendar": "", "clock": "", "history": "", "tag": "",
    "filter": "", "sort": "", "view": "", "hide": "", "lock": "",
    "unlock": "", "highlighter": "", "ink": "", "eraser": "", "crop": "",
    "rotate": "", "code": "", "camera": "", "home": "", "library": "",
    "refresh": "", "attach": "", "flag": "", "bulb": "", "layers": "",
    "grid": "", "list": "", "close": "", "check": "", "info": "",
    "star": "", "sticky": "", "newwindow": "", "openfile": "", "pen": "",
    "brush": "", "square": "", "table": "", "export": "", "pdf": "",
    "slides": "", "fontcolor": "", "cursor": "", "relationship": "",
    "snip": "", "selectall": "", "restore": "", "palette": "",
    "up": "", "down": "", "chart": "", "terminal": "", "menu": "",
    "sync": "", "trash": "", "timeline": "",
}

_icon_font = None


def icon_font(px: float) -> QFont:
    global _icon_font
    if _icon_font is None:
        fams = QFontDatabase.families()
        _icon_font = "Segoe Fluent Icons" if "Segoe Fluent Icons" in fams else "Segoe MDL2 Assets"
    f = QFont(_icon_font)
    f.setPixelSize(max(1, int(px)))
    return f


def ui_font(px: float, weight=QFont.Normal, family=None) -> QFont:
    f = QFont()
    f.setFamilies([family or UI_FONT, "Segoe UI"])
    f.setPixelSize(max(1, int(round(px))))
    f.setWeight(weight)
    return f


# ---------------------------------------------------------------- hand-drawn icons
def _pen(p, color, w):
    pen = QPen(QColor(color), w, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin)
    p.setPen(pen)
    p.setBrush(Qt.NoBrush)


def _draw_shape_icon(kind):
    def draw(p, s, color):
        _pen(p, color, s * 0.075)
        m = s * 0.16
        r = QRectF(m, m + s * 0.06, s - 2 * m, s - 2 * m - s * 0.12)
        if kind in ("ellipse", "star", "diamond", "hexagon", "cross"):
            r = QRectF(m, m, s - 2 * m, s - 2 * m)
        p.drawPath(geometry.shape_path(kind, r, radius=s * 0.14))
    return draw


def _draw_text_tool(p, s, color):
    p.setPen(QColor(color))
    f = ui_font(s * 0.78, QFont.DemiBold, "Segoe UI")
    p.setFont(f)
    p.drawText(QRectF(0, -s * 0.04, s, s), Qt.AlignCenter, "T")


def _draw_line_tool(p, s, color):
    _pen(p, color, s * 0.08)
    p.drawLine(QPointF(s * 0.2, s * 0.8), QPointF(s * 0.8, s * 0.2))


def _draw_arrow_tool(p, s, color):
    _pen(p, color, s * 0.08)
    a, b = QPointF(s * 0.2, s * 0.8), QPointF(s * 0.78, s * 0.22)
    p.drawLine(a, b)
    p.setBrush(QColor(color))
    p.drawPolygon(geometry.arrow_head(QPointF(s * 0.84, s * 0.16), -math.pi / 4, s * 0.34))


def _draw_string_tool(p, s, color):
    a, b = QPointF(s * 0.2, s * 0.3), QPointF(s * 0.8, s * 0.38)
    path = geometry.string_path(a, b, 1.6)
    _pen(p, "#e5484d", s * 0.08)
    p.drawPath(path)
    p.setPen(Qt.NoPen)
    for pt in (a, b):
        p.setBrush(QColor("#e5484d"))
        p.drawEllipse(pt, s * 0.12, s * 0.12)
        p.setBrush(QColor(255, 255, 255, 170))
        p.drawEllipse(QPointF(pt.x() - s * 0.035, pt.y() - s * 0.035), s * 0.035, s * 0.035)


def _draw_hand_tool(p, s, color):
    _pen(p, color, s * 0.075)
    c = s / 2
    L = s * 0.36
    for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
        tip = QPointF(c + dx * L, c + dy * L)
        p.drawLine(QPointF(c, c), tip)
        ang = math.atan2(dy, dx)
        for sgn in (-1, 1):
            p.drawLine(tip, QPointF(tip.x() - math.cos(ang + sgn * 0.7) * s * 0.13,
                                    tip.y() - math.sin(ang + sgn * 0.7) * s * 0.13))


def _draw_frame_tool(p, s, color):
    pen = QPen(QColor(color), s * 0.07, Qt.DashLine, Qt.RoundCap, Qt.RoundJoin)
    pen.setDashPattern([2.2, 1.8])
    p.setPen(pen)
    p.setBrush(Qt.NoBrush)
    p.drawRoundedRect(QRectF(s * 0.12, s * 0.3, s * 0.76, s * 0.56), s * 0.06, s * 0.06)
    p.setPen(Qt.NoPen)
    p.setBrush(QColor(color))
    p.drawRoundedRect(QRectF(s * 0.12, s * 0.12, s * 0.42, s * 0.16), s * 0.05, s * 0.05)


def _draw_evidence(p, s, color):
    _pen(p, color, s * 0.07)
    p.drawRoundedRect(QRectF(s * 0.16, s * 0.2, s * 0.68, s * 0.66), s * 0.06, s * 0.06)
    p.drawRect(QRectF(s * 0.26, s * 0.32, s * 0.48, s * 0.28))
    p.drawLine(QPointF(s * 0.28, s * 0.72), QPointF(s * 0.6, s * 0.72))
    p.setPen(Qt.NoPen)
    p.setBrush(QColor("#e5484d"))
    p.drawEllipse(QPointF(s * 0.5, s * 0.2), s * 0.1, s * 0.1)


def _arrange(kind):
    def draw(p, s, color):
        c = QColor(color)
        back = QRectF(s * 0.14, s * 0.14, s * 0.46, s * 0.46)
        front = QRectF(s * 0.4, s * 0.4, s * 0.46, s * 0.46)
        _pen(p, color, s * 0.065)
        filled_front = kind in ("front", "forward")
        if filled_front:
            p.drawRoundedRect(back, s * 0.05, s * 0.05)
            p.setBrush(c)
            p.drawRoundedRect(front, s * 0.05, s * 0.05)
        else:
            p.setBrush(c)
            p.drawRoundedRect(back, s * 0.05, s * 0.05)
            p.setBrush(QColor(0, 0, 0, 0))
            p.setPen(QPen(c, s * 0.065))
            p.drawRoundedRect(front, s * 0.05, s * 0.05)
        if kind in ("forward", "backward"):
            p.setPen(Qt.NoPen)
            p.setBrush(c)
            y = s * 0.1 if kind == "forward" else s * 0.9
            d = 1 if kind == "backward" else -1
            p.drawPolygon([QPointF(s * 0.78, y), QPointF(s * 0.68, y - d * s * 0.12), QPointF(s * 0.88, y - d * s * 0.12)])
    return draw


def _align(kind):
    def draw(p, s, color):
        _pen(p, color, s * 0.07)
        p.setBrush(QColor(color))
        if kind in ("left", "hcenter", "right"):
            x = {"left": s * 0.18, "hcenter": s * 0.5, "right": s * 0.82}[kind]
            p.drawLine(QPointF(x, s * 0.1), QPointF(x, s * 0.9))
            p.setPen(Qt.NoPen)
            for y, w in ((s * 0.24, s * 0.56), (s * 0.58, s * 0.36)):
                x0 = {"left": x + s * 0.06, "hcenter": x - w / 2, "right": x - s * 0.06 - w}[kind]
                p.drawRoundedRect(QRectF(x0, y, w, s * 0.18), s * 0.03, s * 0.03)
        elif kind in ("top", "vcenter", "bottom"):
            y = {"top": s * 0.18, "vcenter": s * 0.5, "bottom": s * 0.82}[kind]
            p.drawLine(QPointF(s * 0.1, y), QPointF(s * 0.9, y))
            p.setPen(Qt.NoPen)
            for x, h in ((s * 0.24, s * 0.56), (s * 0.58, s * 0.36)):
                y0 = {"top": y + s * 0.06, "vcenter": y - h / 2, "bottom": y - s * 0.06 - h}[kind]
                p.drawRoundedRect(QRectF(x, y0, s * 0.18, h), s * 0.03, s * 0.03)
        elif kind == "dist_h":
            p.setPen(Qt.NoPen)
            for x in (s * 0.12, s * 0.42, s * 0.72):
                p.drawRoundedRect(QRectF(x, s * 0.3, s * 0.16, s * 0.4), s * 0.03, s * 0.03)
        elif kind == "dist_v":
            p.setPen(Qt.NoPen)
            for y in (s * 0.12, s * 0.42, s * 0.72):
                p.drawRoundedRect(QRectF(s * 0.3, y, s * 0.4, s * 0.16), s * 0.03, s * 0.03)
    return draw


def _draw_textbox(p, s, color):
    pen = QPen(QColor(color), s * 0.06, Qt.DashLine)
    pen.setDashPattern([2, 1.6])
    p.setPen(pen)
    p.drawRect(QRectF(s * 0.1, s * 0.18, s * 0.8, s * 0.64))
    p.setPen(QColor(color))
    p.setFont(ui_font(s * 0.5, QFont.DemiBold, "Segoe UI"))
    p.drawText(QRectF(0, 0, s, s), Qt.AlignCenter, "A")


def _draw_board(p, s, color):
    _pen(p, color, s * 0.07)
    p.drawRoundedRect(QRectF(s * 0.1, s * 0.16, s * 0.8, s * 0.68), s * 0.08, s * 0.08)
    p.setPen(Qt.NoPen)
    p.setBrush(QColor(color))
    p.drawRoundedRect(QRectF(s * 0.2, s * 0.28, s * 0.22, s * 0.18), s * 0.03, s * 0.03)
    p.drawRoundedRect(QRectF(s * 0.56, s * 0.52, s * 0.22, s * 0.18), s * 0.03, s * 0.03)
    _pen(p, color, s * 0.05)
    p.drawLine(QPointF(s * 0.31, s * 0.37), QPointF(s * 0.67, s * 0.61))


def _star(p, cx, cy, r, color):
    path = QPainterPath()
    pts = []
    for i in range(8):
        a = -math.pi / 2 + i * math.pi / 4
        k = r if i % 2 == 0 else r * 0.28
        pts.append(QPointF(cx + math.cos(a) * k, cy + math.sin(a) * k))
    path.moveTo(pts[0])
    for pt in pts[1:]:
        path.lineTo(pt)
    path.closeSubpath()
    p.setPen(Qt.NoPen)
    p.setBrush(QColor(color))
    p.drawPath(path)


def _draw_sparkle(p, s, color):
    _star(p, s * 0.45, s * 0.55, s * 0.36, color)
    _star(p, s * 0.8, s * 0.2, s * 0.14, color)


def _draw_coinv(p, s, color):
    """Magnifying glass with a spark in the lens: the Co-Investigator."""
    _pen(p, color, s * 0.085)
    c = QPointF(s * 0.42, s * 0.42)
    p.drawEllipse(c, s * 0.27, s * 0.27)
    p.drawLine(QPointF(s * 0.62, s * 0.62), QPointF(s * 0.86, s * 0.86))
    _star(p, c.x(), c.y(), s * 0.16, "#b69cff")


CUSTOM = {
    "sparkle": _draw_sparkle, "coinv": _draw_coinv,
    "tool_text": _draw_text_tool, "tool_line": _draw_line_tool, "tool_arrow": _draw_arrow_tool,
    "tool_string": _draw_string_tool, "tool_hand": _draw_hand_tool, "tool_frame": _draw_frame_tool,
    "evidence": _draw_evidence, "textbox": _draw_textbox, "board": _draw_board,
    "front": _arrange("front"), "back": _arrange("back"), "forward": _arrange("forward"),
    "backward": _arrange("backward"),
    "align_left": _align("left"), "align_hcenter": _align("hcenter"), "align_right": _align("right"),
    "align_top": _align("top"), "align_vcenter": _align("vcenter"), "align_bottom": _align("bottom"),
    "dist_h": _align("dist_h"), "dist_v": _align("dist_v"),
}
for _k, _n in geometry.SHAPES:
    CUSTOM["shape_" + _k] = _draw_shape_icon(_k)


def icon_pixmap(name: str, size: int, color: str, scale: int = 2) -> QPixmap:
    pm = QPixmap(size * scale, size * scale)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    p.setRenderHint(QPainter.TextAntialiasing)
    s = size * scale
    if name in CUSTOM:
        CUSTOM[name](p, s, color)
    else:
        p.setFont(icon_font(s * 0.8))
        p.setPen(QColor(color))
        p.drawText(QRectF(0, 0, s, s), Qt.AlignCenter, GLYPHS.get(name, name))
    p.end()
    pm.setDevicePixelRatio(scale)
    return pm


_cache = {}


def icon(name: str, size: int = 18, color: str = TEXT, active: str = "#ffffff") -> QIcon:
    key = (name, size, color, active)
    ic = _cache.get(key)
    if ic is None:
        ic = QIcon()
        ic.addPixmap(icon_pixmap(name, size, color), QIcon.Normal, QIcon.Off)
        ic.addPixmap(icon_pixmap(name, size, "#4a4e58"), QIcon.Disabled, QIcon.Off)
        ic.addPixmap(icon_pixmap(name, size, active), QIcon.Active, QIcon.Off)
        ic.addPixmap(icon_pixmap(name, size, active), QIcon.Normal, QIcon.On)
        ic.addPixmap(icon_pixmap(name, size, active), QIcon.Active, QIcon.On)
        _cache[key] = ic
    return ic


def color_dot_icon(color: str, size: int = 14, ring: bool = False) -> QIcon:
    key = ("dot", color, size, ring)
    ic = _cache.get(key)
    if ic is None:
        pm = QPixmap(size * 2, size * 2)
        pm.fill(Qt.transparent)
        p = QPainter(pm)
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(QPen(QColor(255, 255, 255, 60), 1.5) if ring else Qt.NoPen)
        p.setBrush(QColor(color))
        p.drawEllipse(QRectF(3, 3, size * 2 - 6, size * 2 - 6))
        p.end()
        pm.setDevicePixelRatio(2)
        ic = QIcon(pm)
        _cache[key] = ic
    return ic


_pin_cache = {}


def pin_pixmap(color: str = "#e23b3b", px: int = 26) -> QPixmap:
    """Glossy push-pin head, rendered once per colour and size."""
    key = (color, px)
    pm = _pin_cache.get(key)
    if pm is not None:
        return pm
    s = px * 3
    pm = QPixmap(s, s)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    c = QColor(color)
    r = s * 0.34
    center = QPointF(s / 2, s / 2)
    # shadow
    p.setPen(Qt.NoPen)
    p.setBrush(QColor(0, 0, 0, 80))
    p.drawEllipse(QPointF(center.x() + s * 0.06, center.y() + s * 0.09), r, r)
    g = QRadialGradient(QPointF(center.x() - r * 0.35, center.y() - r * 0.4), r * 1.5)
    g.setColorAt(0.0, c.lighter(165))
    g.setColorAt(0.35, c)
    g.setColorAt(1.0, c.darker(190))
    p.setBrush(QBrush(g))
    p.drawEllipse(center, r, r)
    p.setBrush(QColor(255, 255, 255, 190))
    p.drawEllipse(QPointF(center.x() - r * 0.38, center.y() - r * 0.42), r * 0.26, r * 0.2)
    p.end()
    pm.setDevicePixelRatio(3)
    _pin_cache[key] = pm
    return pm


def file_color(ext: str) -> str:
    ext = (ext or "").lower().lstrip(".")
    if ext in ("py", "pyw", "ipynb"):
        return "#3b82f6"
    if ext in ("m", "jl", "r", "c", "cpp", "h", "js", "ts", "java", "rs", "go", "sh", "bat", "ps1", "cs"):
        return "#8e4ec6"
    if ext in ("csv", "tsv", "xy", "xye", "chi", "qchi", "dat", "txt", "log", "json", "xml", "yaml", "yml",
               "h5", "hdf5", "nxs", "npy", "npz", "mat", "cif", "gr", "fq", "sq", "poni"):
        return "#12a594"
    if ext in ("png", "jpg", "jpeg", "gif", "bmp", "tif", "tiff", "webp", "svg"):
        return "#d6409f"
    if ext in ("pdf", "doc", "docx", "ppt", "pptx", "xls", "xlsx", "md", "rtf", "odt"):
        return "#f76b15"
    if ext in ("exe", "lnk", "msi", "app", "url"):
        return "#f5b544"
    return "#9aa0ad"


def file_glyph(ext: str) -> str:
    ext = (ext or "").lower().lstrip(".")
    if ext in ("py", "pyw", "m", "jl", "r", "c", "cpp", "h", "js", "ts", "java", "rs", "go", "cs", "ipynb"):
        return "code"
    if ext in ("sh", "bat", "ps1", "cmd"):
        return "terminal"
    if ext in ("png", "jpg", "jpeg", "gif", "bmp", "tif", "tiff", "webp", "svg"):
        return "image"
    if ext in ("csv", "tsv", "xls", "xlsx", "xy", "xye", "chi", "qchi", "dat", "gr"):
        return "chart"
    if ext == "pdf":
        return "pdf"
    if ext in ("exe", "lnk", "msi", "url"):
        return "newwindow"
    if ext == "":
        return "folder"
    return "doc"


STYLESHEET = f"""
* {{
    font-family: "{UI_FONT}", "Segoe UI";
    font-size: 10pt;
    color: {TEXT};
    outline: none;
}}
QMainWindow, #Root, #Page {{ background: {BG}; }}
#Sidebar {{ background: {PANEL}; border-right: 1px solid {BORDER}; }}
#TopBar, #ToolBar, #Ribbon {{ background: {PANEL}; }}
#TopBar {{ border-bottom: 1px solid {BORDER}; }}
#Ribbon {{ border-bottom: 1px solid {BORDER}; }}
#Inspector {{ background: {PANEL}; border-left: 1px solid {BORDER}; }}
#Brand {{ font-size: 13.5pt; font-weight: 700; letter-spacing: 0.5px; }}
#SectionLabel {{ color: {FAINT}; font-size: 8pt; font-weight: 700; letter-spacing: 1.2px; }}
#Muted {{ color: {MUTED}; }}
#Faint {{ color: {FAINT}; }}
#Title {{ font-size: 15pt; font-weight: 650; }}
#BigTitle {{ font-size: 22pt; font-weight: 700; }}
#Crumb {{ color: {MUTED}; font-size: 10pt; }}
#CrumbStrong {{ font-size: 11pt; font-weight: 650; }}
#Hint {{ color: {FAINT}; font-size: 8.5pt; }}

QToolButton {{
    background: transparent;
    border: 1px solid transparent;
    border-radius: 7px;
    padding: 5px;
}}
QToolButton:hover {{ background: {SURFACE2}; }}
QToolButton:pressed {{ background: {SURFACE3}; }}
QToolButton:checked {{ background: {ACCENT_DIM}; border: 1px solid {ACCENT}; }}
QToolButton:disabled {{ background: transparent; }}
QToolButton::menu-indicator {{ image: none; width: 0; }}
QToolButton#TextTool {{ padding: 5px 9px; }}
QPushButton#Nav {{
    text-align: left; padding: 7px 10px; border-radius: 8px; font-size: 10pt;
    background: transparent; border: 1px solid transparent;
}}
QPushButton#Nav:hover {{ background: {SURFACE}; }}
QPushButton#Nav:checked {{ background: {SURFACE2}; border: 1px solid {BORDER2}; }}
QToolButton#Swatch {{ border-radius: 9px; padding: 0; border: 1px solid rgba(255,255,255,40); }}
QToolButton#Swatch:checked {{ border: 2px solid white; }}

QPushButton {{
    background: {SURFACE2};
    border: 1px solid {BORDER2};
    border-radius: 7px;
    padding: 6px 14px;
}}
QPushButton:hover {{ background: {SURFACE3}; }}
QPushButton:pressed {{ background: {BORDER}; }}
QPushButton:disabled {{ color: #5a5d66; }}
QPushButton:checked {{ background: {ACCENT_DIM}; border-color: {ACCENT}; }}
QPushButton::menu-indicator {{ image: none; width: 0; }}
QPushButton#Primary {{ background: {ACCENT}; border: 1px solid {ACCENT}; color: white; font-weight: 600; }}
QPushButton#Primary:hover {{ background: {ACCENT_HOVER}; }}
QPushButton#Primary:disabled {{ background: {ACCENT_DIM}; color: #9aa; }}
QPushButton#Ghost {{ background: transparent; border: 1px solid transparent; }}
QPushButton#Ghost:hover {{ background: {SURFACE2}; }}
QPushButton#Chip {{
    background: {SURFACE}; border: 1px solid {BORDER2}; border-radius: 13px; padding: 4px 12px; font-size: 9pt;
}}
QPushButton#Chip:hover {{ background: {SURFACE2}; }}
QPushButton#Chip:checked {{ background: {ACCENT_DIM}; border-color: {ACCENT}; color: white; }}
QPushButton#Danger {{ background: transparent; border: 1px solid #5a2a2e; color: #ff8a8f; }}
QPushButton#Danger:hover {{ background: #3a1d20; }}

QComboBox, QLineEdit, QSpinBox, QDoubleSpinBox, QPlainTextEdit, QTextEdit {{
    background: {SURFACE};
    border: 1px solid {BORDER2};
    border-radius: 6px;
    padding: 4px 8px;
    selection-background-color: {SELECT};
}}
QPlainTextEdit, QTextEdit {{ padding: 6px; }}
QComboBox:hover, QLineEdit:hover, QSpinBox:hover, QDoubleSpinBox:hover {{ border-color: #444a56; }}
QComboBox:focus, QLineEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus, QPlainTextEdit:focus, QTextEdit:focus
    {{ border-color: {SELECT}; }}
QLineEdit#TitleEdit {{
    background: transparent; border: 1px solid transparent; font-size: 13pt; font-weight: 650; padding: 3px 6px;
}}
QLineEdit#TitleEdit:hover {{ border-color: {BORDER2}; }}
QLineEdit#TitleEdit:focus {{ border-color: {SELECT}; background: {SURFACE}; }}
QLineEdit#SearchBox {{
    background: {SURFACE}; border: 1px solid {BORDER2}; border-radius: 9px; padding: 7px 10px 7px 32px;
    font-size: 10.5pt;
}}
QLineEdit#BigSearch {{
    background: transparent; border: none; font-size: 14pt; padding: 6px 4px;
}}
QComboBox::drop-down {{ border: none; width: 20px; }}
QComboBox QAbstractItemView {{
    background: {SURFACE}; border: 1px solid {BORDER2}; selection-background-color: {SURFACE3}; padding: 4px;
}}
QSpinBox::up-button, QSpinBox::down-button, QDoubleSpinBox::up-button, QDoubleSpinBox::down-button {{ width: 14px; }}
QCheckBox {{ spacing: 8px; }}
QCheckBox::indicator {{ width: 16px; height: 16px; border-radius: 4px; border: 1px solid {BORDER2}; background: {SURFACE}; }}
QCheckBox::indicator:checked {{ background: {ACCENT}; border-color: {ACCENT}; }}
QRadioButton {{ spacing: 8px; }}

QMenu {{
    background: {SURFACE}; border: 1px solid {BORDER2}; border-radius: 9px; padding: 6px;
}}
QMenu::item {{ padding: 7px 30px 7px 10px; border-radius: 6px; }}
QMenu::item:selected {{ background: {SURFACE3}; }}
QMenu::item:disabled {{ color: #5a5d66; }}
QMenu::separator {{ height: 1px; background: {BORDER2}; margin: 5px 6px; }}
QMenu::icon {{ padding-left: 8px; }}
QMenu::right-arrow {{ width: 8px; }}

QToolTip {{
    background: {SURFACE2}; color: {TEXT}; border: 1px solid {BORDER2}; border-radius: 6px; padding: 5px 8px;
}}
QScrollBar:vertical {{ background: transparent; width: 11px; margin: 0; }}
QScrollBar::handle:vertical {{ background: #33373f; border-radius: 4px; min-height: 30px; margin: 2px; }}
QScrollBar::handle:vertical:hover {{ background: #464b55; }}
QScrollBar:horizontal {{ background: transparent; height: 11px; margin: 0; }}
QScrollBar::handle:horizontal {{ background: #33373f; border-radius: 4px; min-width: 30px; margin: 2px; }}
QScrollBar::handle:horizontal:hover {{ background: #464b55; }}
QScrollBar::add-line, QScrollBar::sub-line {{ width: 0; height: 0; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}

QTreeView, QListView, QListWidget, QTreeWidget {{
    background: transparent; border: none; show-decoration-selected: 0;
}}
QTreeView::branch:selected, QTreeView::branch:hover {{ background: transparent; }}
QTreeView::item, QListWidget::item {{ padding: 4px 4px; border-radius: 6px; }}
QTreeView::item:hover, QListWidget::item:hover {{ background: {SURFACE}; }}
QTreeView::item:selected, QListWidget::item:selected {{ background: {SURFACE2}; color: {TEXT}; }}
QTreeView::branch {{ background: transparent; }}

QSlider::groove:horizontal {{ height: 4px; background: {BORDER2}; border-radius: 2px; }}
QSlider::sub-page:horizontal {{ background: {ACCENT}; border-radius: 2px; }}
QSlider::handle:horizontal {{
    background: white; width: 14px; height: 14px; margin: -5px 0; border-radius: 7px;
}}
QTabBar::tab {{ background: transparent; padding: 7px 14px; border-bottom: 2px solid transparent; color: {MUTED}; }}
QTabBar::tab:selected {{ color: {TEXT}; border-bottom: 2px solid {ACCENT}; }}
QTabWidget::pane {{ border: none; }}
QSplitter::handle {{ background: {BORDER}; }}
QSplitter::handle:horizontal {{ width: 1px; }}
QSplitter::handle:vertical {{ height: 1px; }}
QProgressBar {{
    background: {SURFACE}; border: 1px solid {BORDER2}; border-radius: 6px; height: 10px; text-align: center;
    font-size: 8pt;
}}
QProgressBar::chunk {{ background: {ACCENT}; border-radius: 5px; }}
QDialog {{ background: {PANEL}; }}
QFrame#Separator {{ background: {BORDER2}; }}
QFrame#Card {{ background: {SURFACE}; border: 1px solid {BORDER}; border-radius: 12px; }}
QFrame#Overlay {{ background: rgba(26, 29, 35, 245); border: 1px solid {BORDER2}; border-radius: 14px; }}
QFrame#Floating {{ background: rgba(26, 29, 35, 235); border: 1px solid {BORDER2}; border-radius: 10px; }}
#Toast {{
    background: rgba(34, 38, 46, 245); border: 1px solid {BORDER2}; border-radius: 10px;
    padding: 9px 16px; font-size: 10pt;
}}
"""
