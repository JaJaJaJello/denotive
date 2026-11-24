"""Specs <-> DeNotive slide pieces, drawn by DeNotive itself (so a training picture and its answer always agree)."""
import os
import sys
from pathlib import Path

DENOTIVE = Path(__file__).resolve().parents[2]  # ...\DeNotive (the app's own code)
if str(DENOTIVE) not in sys.path:
    sys.path.insert(0, str(DENOTIVE))

_app = None


def ensure_app(offscreen=True):
    """DeNotive's drawing code needs a Qt application. Offscreen unless something must reach the clipboard."""
    global _app
    if offscreen:
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        os.environ.setdefault("QT_QPA_FONTDIR", r"C:\Windows\Fonts")
    from PySide6.QtWidgets import QApplication
    _app = QApplication.instance() or QApplication([])
    return _app


DEFAULT_FONT = "Arial"
CANVAS_W = 1280.0


def _metrics(text, size, bold, font):
    if _app is None:
        ensure_app()  # fonts can't be measured before Qt is running
    from PySide6.QtGui import QFont, QFontMetricsF
    f = QFont(font)
    f.setPixelSize(max(1, int(round(size))))
    f.setBold(bool(bold))
    fm = QFontMetricsF(f)
    return fm.horizontalAdvance(text) * 1.04 + 8, fm.height() + 4  # same slack the PDF import uses


def text_box(text, size, bold=False, rot=0, font=DEFAULT_FONT, x=0.0, y=0.0, ha="left", va="top"):
    """The visible box of a label placed at (x, y) with the given alignment, as (x0, y0, x1, y1)."""
    w, h = _metrics(text, size, bold, font)
    bw, bh = (h, w) if rot in (90, 270) else (w, h)
    x0 = x - (bw / 2 if ha == "center" else bw if ha == "right" else 0)
    y0 = y - (bh / 2 if va == "middle" else bh if va == "bottom" else 0)
    return x0, y0, x0 + bw, y0 + bh


def _contrast(fill):
    if not fill:
        return "#1d1f24"
    from .tokens import hex_rgb
    r, g, b = hex_rgb(fill)
    return "#1d1f24" if (0.299 * r + 0.587 * g + 0.114 * b) > 150 else "#ffffff"


def denotive_grad(s):
    """A spec's gradient in DeNotive's own form (what the gradient tool saves), or None."""
    g = s.get("grad")
    if not g or not s.get("fill"):
        return None
    return {"c": [0.5, 0.5], "ang": float(g["ang"]), "a": float(g["a"]), "b": float(g["b"]), "c1": s["fill"],
            "c2": g["c2"]}


def to_items(specs, font=DEFAULT_FONT):
    """DeNotive item dicts (the same shape the evidence editor saves and copies) from specs."""
    import html
    from denotive.slide.items import SlidePath
    items = []
    for z, s in enumerate(specs, start=1):
        t = s["t"]
        d = None
        if t == "raw":  # finished DeNotive pieces (shapes traced from the picture), kept together at this depth
            n = len(s["items"])
            for j, it in enumerate(s["items"]):
                items.append(dict(it, z=z + j / (n + 1), id=f"v{z}r{j}", group=f"trace{z}"))
            continue
        if t == "shape":
            d = {"type": "shape", "shape": s["kind"], "x": s["x0"], "y": s["y0"], "w": max(2.0, s["x1"] - s["x0"]),
                 "h": max(2.0, s["y1"] - s["y0"]), "fill": s.get("fill"), "stroke": s.get("stroke"),
                 "sw": float(s.get("sw") or 0) if s.get("stroke") else 0, "radius": 14, "font": font,
                 "size": s.get("size", 20), "color": _contrast(s.get("fill")), "plain": s.get("text", "")}
        elif t == "line":
            d = {"type": "line", "x": 0, "y": 0, "x1": s["x0"], "y1": s["y0"], "x2": s["x1"], "y2": s["y1"],
                 "stroke": s.get("color") or "#000000", "sw": s.get("sw", 2), "dash": s.get("dash", "solid"),
                 "a1": bool(s.get("a1")), "a2": bool(s.get("a2")), "stick": False}
        elif t == "text":
            rot = int(s.get("rot") or 0)
            need_w, h = _metrics(s["text"], s["size"], s.get("bold"), font)
            if rot in (90, 270):
                w = max(need_w, s["y1"] - s["y0"])
                cx, cy = (s["x0"] + s["x1"]) / 2, (s["y0"] + s["y1"]) / 2
                x, y = cx - w / 2, cy - h / 2
            else:
                w = max(need_w, s["x1"] - s["x0"])
                x, y = s["x0"], s["y0"]
            style = "font-weight:700" if s.get("bold") else "font-weight:400"
            d = {"type": "text", "x": x, "y": y, "w": w, "h": h, "minh": 8, "pad": 0, "rot": rot, "font": font,
                 "size": s["size"], "color": s.get("color") or "#000000",
                 "html": f'<span style="{style}">{html.escape(s["text"])}</span>'}
        elif t == "path":
            pts = s["pts"]
            segs = [["M", pts[0][0], pts[0][1]]] + [["L", x, y] for x, y in pts[1:]]
            if s.get("closed"):
                segs.append(["Z"])
            d = SlidePath.from_segments(segs, fill=s.get("fill"), stroke=s.get("stroke"),
                                        sw=float(s.get("sw") or 1), dash=s.get("dash", "solid"), rule="nonzero",
                                        cap="round")
        if d is not None and t in ("shape", "path") and denotive_grad(s):
            d["grad"] = denotive_grad(s)
        if d is not None:
            d["z"] = z
            d["id"] = f"v{z}"
            items.append(d)
    return items


def doc_of(items):
    return {"v": 1, "w": 1280, "h": 720, "bg": "#ffffff", "notes": "", "items": items}


def render(specs, width=960, font=DEFAULT_FONT, source=None):
    """A QImage of the figure, drawn by DeNotive. Picture pieces without pixels of their own are cut from
    `source` (a PIL picture laid out like the slide), if given."""
    ensure_app()
    from denotive.slide.scene import SlideScene
    sc = SlideScene(doc_of(to_items(specs, font)))
    add_pictures(sc, specs, source)
    return sc.render_image(width)


def pil_to_qimage(img):
    from PySide6.QtGui import QImage
    img = img.convert("RGB")
    return QImage(img.tobytes(), img.width, img.height, img.width * 3, QImage.Format_RGB888).copy()


def crop_from(source, s):
    """The part of `source` (a PIL picture laid out like the slide, any size) under a picture piece."""
    k = source.width / CANVAS_W
    box = (max(0, round(s["x0"] * k)), max(0, round(s["y0"] * k)),
           min(source.width, round(s["x1"] * k)), min(source.height, round(s["y1"] * k)))
    if box[2] - box[0] < 2 or box[3] - box[1] < 2:
        return None
    return source.crop(box)


def add_pictures(scene, specs, source=None):
    """Puts each picture piece into a DeNotive scene at its place in the painting order: its own pixels when
    the generator made it, otherwise the same area cut out of `source`."""
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QPixmap, QTransform
    from PySide6.QtWidgets import QGraphicsPixmapItem
    for z, s in enumerate(specs, start=1):
        if s["t"] != "image":
            continue
        w, h = s["x1"] - s["x0"], s["y1"] - s["y0"]
        pic = s.get("pixels")
        if pic is None and source is not None:
            pic = crop_from(source, s)
        if pic is None or w < 1 or h < 1:
            continue
        q = pil_to_qimage(pic)
        it = QGraphicsPixmapItem(QPixmap.fromImage(q))
        it.setTransformationMode(Qt.SmoothTransformation if s.get("smooth", True) else Qt.FastTransformation)
        it.setTransform(QTransform.fromScale(w / q.width(), h / q.height()))
        it.setPos(s["x0"], s["y0"])
        it.setZValue(z)
        scene.addItem(it)


def measured(specs, font=DEFAULT_FONT):
    """The specs with every box replaced by where DeNotive actually put it (text boxes grow to fit, etc.), and
    the DeNotive scene with everything in it, pictures included."""
    ensure_app()
    from PySide6.QtCore import QRectF
    from denotive.slide.scene import SlideScene
    sc = SlideScene(doc_of(to_items(specs, font)))
    by_id = {it.uid: it for it in sc.slide_items()}
    out = []
    for z, s in enumerate(specs, start=1):
        s = dict(s)
        it = by_id.get(f"v{z}")  # to_items names each piece after its place in the list
        if it is not None and s["t"] in ("shape", "text"):
            r = it.mapRectToScene(QRectF(0, 0, it.w, it.h))
            s.update(x0=r.left(), y0=r.top(), x1=r.right(), y1=r.bottom())
        out.append(s)
    add_pictures(sc, out)
    return out, sc


def qimage_to_pil(img):
    from PIL import Image
    from PySide6.QtGui import QImage
    img = img.convertToFormat(QImage.Format_RGB888)
    w, h = img.width(), img.height()
    ptr = img.constBits()
    data = bytes(ptr)[: img.bytesPerLine() * h]
    return Image.frombuffer("RGB", (w, h), data, "raw", "RGB", img.bytesPerLine(), 1).copy()
