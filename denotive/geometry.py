"""Shape outlines, arrow heads and string curves shared by the board, the slide editor and the icons."""
import math

from PySide6.QtCore import QPointF, QRectF
from PySide6.QtGui import QPainterPath, QPolygonF

SHAPES = [
    ("rect", "Rectangle"), ("round", "Rounded rectangle"), ("ellipse", "Ellipse"),
    ("triangle", "Triangle"), ("diamond", "Diamond"), ("hexagon", "Hexagon"),
    ("parallelogram", "Parallelogram"), ("star", "Star"), ("arrow", "Block arrow"),
    ("chevron", "Chevron"), ("callout", "Callout"), ("cross", "Plus"),
]
SHAPE_NAMES = dict(SHAPES)


def shape_path(kind: str, r: QRectF, radius: float = 14.0) -> QPainterPath:
    p = QPainterPath()
    x, y, w, h = r.x(), r.y(), r.width(), r.height()
    if kind == "rect":
        p.addRect(r)
    elif kind == "round":
        rad = max(0.0, min(radius, w / 2, h / 2))
        p.addRoundedRect(r, rad, rad)
    elif kind == "ellipse":
        p.addEllipse(r)
    elif kind == "triangle":
        p.addPolygon(QPolygonF([QPointF(x + w / 2, y), QPointF(x + w, y + h), QPointF(x, y + h)]))
        p.closeSubpath()
    elif kind == "diamond":
        p.addPolygon(QPolygonF([QPointF(x + w / 2, y), QPointF(x + w, y + h / 2),
                                QPointF(x + w / 2, y + h), QPointF(x, y + h / 2)]))
        p.closeSubpath()
    elif kind == "hexagon":
        d = min(w * 0.25, h * 0.6)
        p.addPolygon(QPolygonF([QPointF(x + d, y), QPointF(x + w - d, y), QPointF(x + w, y + h / 2),
                                QPointF(x + w - d, y + h), QPointF(x + d, y + h), QPointF(x, y + h / 2)]))
        p.closeSubpath()
    elif kind == "parallelogram":
        d = min(w * 0.22, h * 0.8)
        p.addPolygon(QPolygonF([QPointF(x + d, y), QPointF(x + w, y), QPointF(x + w - d, y + h), QPointF(x, y + h)]))
        p.closeSubpath()
    elif kind == "star":
        cx, cy = x + w / 2, y + h / 2
        pts = []
        for i in range(10):
            a = -math.pi / 2 + i * math.pi / 5
            k = 1.0 if i % 2 == 0 else 0.42
            pts.append(QPointF(cx + math.cos(a) * w / 2 * k, cy + math.sin(a) * h / 2 * k + h * 0.04))
        p.addPolygon(QPolygonF(pts))
        p.closeSubpath()
    elif kind == "arrow":
        hw = min(w * 0.42, h)
        t = h * 0.28
        p.addPolygon(QPolygonF([QPointF(x, y + t), QPointF(x + w - hw, y + t), QPointF(x + w - hw, y),
                                QPointF(x + w, y + h / 2), QPointF(x + w - hw, y + h),
                                QPointF(x + w - hw, y + h - t), QPointF(x, y + h - t)]))
        p.closeSubpath()
    elif kind == "chevron":
        d = min(w * 0.3, h * 0.7)
        p.addPolygon(QPolygonF([QPointF(x, y), QPointF(x + w - d, y), QPointF(x + w, y + h / 2),
                                QPointF(x + w - d, y + h), QPointF(x, y + h), QPointF(x + d, y + h / 2)]))
        p.closeSubpath()
    elif kind == "callout":
        body_h = h * 0.78
        rad = max(0.0, min(radius, w / 2, body_h / 2))
        body = QPainterPath()
        body.addRoundedRect(QRectF(x, y, w, body_h), rad, rad)
        tail = QPainterPath()
        tail.addPolygon(QPolygonF([QPointF(x + w * 0.18, y + body_h - 1), QPointF(x + w * 0.38, y + body_h - 1),
                                   QPointF(x + w * 0.12, y + h)]))
        tail.closeSubpath()
        p = body.united(tail)
    elif kind == "cross":
        t = min(w, h) * 0.32
        cxl, cyl = x + (w - t) / 2, y + (h - t) / 2
        a = QPainterPath()
        a.addRect(QRectF(cxl, y, t, h))
        b = QPainterPath()
        b.addRect(QRectF(x, cyl, w, t))
        p = a.united(b)
    else:
        p.addRect(r)
    return p


def shape_text_rect(kind: str, r: QRectF) -> QRectF:
    """Area inside a shape that comfortably holds a label."""
    if kind in ("ellipse",):
        return r.adjusted(r.width() * 0.15, r.height() * 0.15, -r.width() * 0.15, -r.height() * 0.15)
    if kind in ("diamond", "star"):
        return r.adjusted(r.width() * 0.25, r.height() * 0.25, -r.width() * 0.25, -r.height() * 0.25)
    if kind == "triangle":
        return r.adjusted(r.width() * 0.22, r.height() * 0.42, -r.width() * 0.22, -r.height() * 0.06)
    if kind == "callout":
        return QRectF(r.x(), r.y(), r.width(), r.height() * 0.78).adjusted(8, 6, -8, -6)
    if kind in ("hexagon", "parallelogram", "chevron"):
        return r.adjusted(r.width() * 0.18, 6, -r.width() * 0.18, -6)
    if kind == "arrow":
        return r.adjusted(8, r.height() * 0.28, -r.width() * 0.3, -r.height() * 0.28)
    return r.adjusted(10, 8, -10, -8)


def arrow_head(tip: QPointF, angle_rad: float, size: float) -> QPolygonF:
    """Triangle whose tip is at `tip`, pointing along angle (radians, y down)."""
    back = angle_rad + math.pi
    spread = 0.42
    return QPolygonF([
        tip,
        QPointF(tip.x() + math.cos(back - spread) * size, tip.y() + math.sin(back - spread) * size),
        QPointF(tip.x() + math.cos(back) * size * 0.72, tip.y() + math.sin(back) * size * 0.72),
        QPointF(tip.x() + math.cos(back + spread) * size, tip.y() + math.sin(back + spread) * size),
    ])


def string_path(a: QPointF, b: QPointF, slack: float = 1.0) -> QPainterPath:
    """A string pinned at a and b that sags under gravity (more sag for long horizontal spans)."""
    dx, dy = b.x() - a.x(), b.y() - a.y()
    length = math.hypot(dx, dy)
    path = QPainterPath(a)
    if length < 1:
        path.lineTo(b)
        return path
    horiz = abs(dx) / length
    sag = min(160.0, 0.11 * length + 8.0) * (0.25 + 0.75 * horiz) * slack
    mid = QPointF((a.x() + b.x()) / 2, (a.y() + b.y()) / 2 + 2 * sag)
    path.quadTo(mid, b)
    return path


def rdp(points, eps):
    """Ramer-Douglas-Peucker polyline simplification on [(x, y), ...]."""
    if len(points) < 3:
        return list(points)
    stack = [(0, len(points) - 1)]
    keep = [False] * len(points)
    keep[0] = keep[-1] = True
    while stack:
        s, e = stack.pop()
        ax, ay = points[s]
        bx, by = points[e]
        dx, dy = bx - ax, by - ay
        norm = math.hypot(dx, dy) or 1e-9
        best, idx = -1.0, -1
        for i in range(s + 1, e):
            px, py = points[i]
            d = abs(dy * px - dx * py + bx * ay - by * ax) / norm
            if d > best:
                best, idx = d, i
        if best > eps and idx > 0:
            keep[idx] = True
            stack.append((s, idx))
            stack.append((idx, e))
    return [pt for pt, k in zip(points, keep) if k]


def smooth_path(points) -> QPainterPath:
    """Quadratic smoothing through midpoints of a polyline."""
    path = QPainterPath()
    if not points:
        return path
    path.moveTo(QPointF(*points[0]))
    if len(points) == 1:
        path.lineTo(QPointF(points[0][0] + 0.01, points[0][1]))
        return path
    if len(points) == 2:
        path.lineTo(QPointF(*points[1]))
        return path
    for i in range(1, len(points) - 1):
        cx, cy = points[i]
        nx, ny = points[i + 1]
        path.quadTo(QPointF(cx, cy), QPointF((cx + nx) / 2, (cy + ny) / 2))
    path.lineTo(QPointF(*points[-1]))
    return path
