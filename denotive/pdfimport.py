"""Turns a PDF page's own drawing into editable evidence items, like Inkscape's PDF import.

SVG files work the same way: Qt draws the SVG into a vector PDF in memory, which is then read like any other.

A PDF made by a program (matplotlib, Origin, Excel, a journal) is a list of drawing instructions. Each becomes
a DeNotive item: curves and fills become exact vector paths, text becomes editable text boxes (one per line),
pictures become pictures. Consecutive drawings in the same style join into one item, so a plot series' line or
markers come out as one piece that can be recoloured, hidden or moved on its own. Scanned pages contain only a
picture, so they come in as that picture.
"""
import ctypes
import html
import io
import math
import re

import pypdfium2 as pdfium
import pypdfium2.raw as pr
from PySide6.QtCore import QPointF
from PySide6.QtGui import QFont, QFontDatabase, QFontMetricsF, QImage, QTransform

from .slide.items import SlidePath, short_id

ITALIC_FLAG = 1 << 6  # PDF font descriptor flags
SERIF_FLAG = 1 << 1
FIXED_FLAG = 1 << 0


SVG_EXTS = (".svg", ".svgz")


def is_svg(path):
    return str(path).lower().endswith(SVG_EXTS)


GENERIC_FONTS = {"sans-serif": "Arial", "serif": "Times New Roman", "monospace": "Consolas",
                 "cursive": "Comic Sans MS", "fantasy": "Impact", "system-ui": "Segoe UI"}
SVG_NS = "http://www.w3.org/2000/svg"


def _pick_font(families):
    """The first font in a CSS font list that this computer has (Qt's SVG drawing only tries the first)."""
    have = {f.lower(): f for f in QFontDatabase.families()}
    for name in families.split(","):
        name = name.strip().strip("'\"").strip()
        if name.lower() in have:
            return have[name.lower()]
        if name.lower() in GENERIC_FONTS:
            return GENERIC_FONTS[name.lower()]
    return "Arial"


def _expand_font(value):
    """CSS "font: italic bold 18px/1.2 Arial" as separate settings (Qt's SVG drawing ignores the shorthand)."""
    m = re.match(r"\s*(?P<pre>(?:(?:italic|oblique|normal|bold|bolder|lighter|small-caps|\d{3})\s+)*)"
                 r"(?P<size>[\d.]+(?:px|pt|em|%)?)(?:\s*/\s*\S+)?\s+(?P<fam>.+?)\s*$", value)
    if not m:
        return None
    pre = m.group("pre").split()
    out = [f"font-size: {m.group('size')}", f"font-family: '{_pick_font(m.group('fam'))}'"]
    if "italic" in pre or "oblique" in pre:
        out.append("font-style: italic")
    if "bold" in pre or "bolder" in pre or any(p.isdigit() and int(p) >= 600 for p in pre):
        out.append("font-weight: bold")
    return "; ".join(out)


def _fix_fonts(css):
    css = re.sub(r"(?<![-\w])font\s*:\s*([^;}\"]+)", lambda m: _expand_font(m.group(1)) or m.group(0), css)
    return re.sub(r"font-family\s*:\s*([^;}\"]+)", lambda m: f"font-family: '{_pick_font(m.group(1))}'", css)

XLINK = "{http://www.w3.org/1999/xlink}href"
_SVG_CACHE = {}


def _css_color(text, opacity=1.0):
    """A CSS colour (#hex, a name, rgb(...)) with an opacity, as DeNotive's #RRGGBB / #AARRGGBB."""
    from PySide6.QtGui import QColor
    text = (text or "#000000").strip()
    m = re.match(r"rgba?\(\s*([\d.]+%?)\s*,\s*([\d.]+%?)\s*,\s*([\d.]+%?)(?:\s*,\s*([\d.]+))?\s*\)", text)
    if m:
        vals = [float(v[:-1]) * 2.55 if v.endswith("%") else float(v) for v in m.groups()[:3]]
        c = QColor(*(max(0, min(255, round(v))) for v in vals))
        if m.group(4):
            opacity *= float(m.group(4))
    else:
        c = QColor(text)
        if not c.isValid():
            c = QColor("#000000")
    c.setAlphaF(max(0.0, min(1.0, c.alphaF() * opacity)))
    return c.name(QColor.HexArgb) if c.alpha() < 255 else c.name()


def _num(v, default):
    if v is None:
        return default
    v = v.strip()
    try:
        return float(v[:-1]) / 100 if v.endswith("%") else float(re.sub(r"[a-z]+$", "", v))
    except ValueError:
        return default


def _svg_gradients(root, q):
    """Gradient fills swapped for stand-in colours (one per element), so they can be found again after Qt has
    drawn the SVG into a PDF. Returns {stand-in colour: gradient}. Radial gradients become a solid in-between
    colour; so does everything past 36 gradients."""
    defs = {g.get("id"): g for g in root.iter() if g.tag in (q("linearGradient"), q("radialGradient")) and g.get("id")}

    def chain(g):
        seen, out = set(), [g]
        while True:
            ref = (out[-1].get(XLINK) or out[-1].get("href") or "").lstrip("#")
            if not ref or ref in seen or ref not in defs:
                return out
            seen.add(ref)
            out.append(defs[ref])

    def stops(g):
        for x in chain(g):
            st = [s for s in x if s.tag == q("stop")]
            if st:
                out = []
                for s in st:
                    css = {k.strip(): v.strip() for k, v in
                           (kv.split(":", 1) for kv in (s.get("style") or "").split(";") if ":" in kv)}
                    out.append((_num(s.get("offset"), 0.0),
                                _css_color(css.get("stop-color") or s.get("stop-color"),
                                           _num(css.get("stop-opacity") or s.get("stop-opacity"), 1.0))))
                return out
        return []

    def attr(g, name, default):
        for x in chain(g):
            if x.get(name) is not None:
                return x.get(name)
        return default

    found, k = {}, 0
    for el in root.iter():
        style = el.get("style") or ""
        m = re.search(r"fill\s*:\s*url\(\s*['\"]?#([^)'\"\s]+)['\"]?\s*\)", style)
        gid = m.group(1) if m else None
        if gid is None:
            m2 = re.match(r"\s*url\(\s*['\"]?#([^)'\"\s]+)['\"]?\s*\)", el.get("fill") or "")
            gid = m2.group(1) if m2 else None
        if gid not in defs:
            continue
        g = defs[gid]
        st = stops(g)
        if not st:
            continue
        c1, c2 = st[0][1], st[-1][1]
        if g.tag == q("linearGradient") and k < 36 and c1 != c2:
            stand_in = "#{:02x}fb0d".format(3 + 7 * k)
            k += 1
            found[stand_in] = {"c1": c1, "c2": c2, "units": attr(g, "gradientUnits", "objectBoundingBox"),
                               "x1": _num(attr(g, "x1", None), 0.0), "y1": _num(attr(g, "y1", None), 0.0),
                               "x2": _num(attr(g, "x2", None), 1.0), "y2": _num(attr(g, "y2", None), 0.0)}
        else:
            from PySide6.QtGui import QColor
            a, b = QColor(c1), QColor(c2)
            mix = QColor((a.red() + b.red()) // 2, (a.green() + b.green()) // 2, (a.blue() + b.blue()) // 2,
                         (a.alpha() + b.alpha()) // 2)
            stand_in = mix.name(QColor.HexArgb) if mix.alpha() < 255 else mix.name()
        if m:
            el.set("style", style[:m.start()] + f"fill: {stand_in}" + style[m.end():])
        else:
            el.set("fill", stand_in)
    for el in root.iter():  # gradient outlines: the first colour
        for name in ("stroke",):
            m = re.match(r"\s*url\(\s*['\"]?#([^)'\"\s]+)", el.get(name) or "")
            if m and m.group(1) in defs:
                st = stops(defs[m.group(1)])
                el.set(name, st[0][1] if st else "#000000")
        style = el.get("style") or ""
        m = re.search(r"stroke\s*:\s*url\(\s*['\"]?#([^)'\"\s]+)['\"]?\s*\)", style)
        if m and m.group(1) in defs:
            st = stops(defs[m.group(1)])
            el.set("style", style[:m.start()] + f"stroke: {st[0][1] if st else '#000000'}" + style[m.end():])
    return found


def _apply_svg_gradients(items, info, to_slide_svg):
    """Gives the pieces drawn in stand-in colours their real gradient (DeNotive's own: circle and two lines)."""
    found = info.get("grads") or {}
    if not found:
        return
    from PySide6.QtGui import QColor

    def match(fill):
        c = QColor(fill or "")
        for key, g in found.items():
            k = QColor(key)
            if c.isValid() and abs(c.red() - k.red()) <= 2 and abs(c.green() - k.green()) <= 2 and \
                    abs(c.blue() - k.blue()) <= 2:
                return g
        return None

    for d in items:
        if d.get("type") != "path":
            continue
        g = match(d.get("fill"))
        if g is None:
            continue
        w, h = max(1.0, d["w"]), max(1.0, d["h"])
        if g["units"] == "userSpaceOnUse":
            p1, p2 = to_slide_svg(g["x1"], g["y1"]), to_slide_svg(g["x2"], g["y2"])
            p1, p2 = (p1[0] - d["x"], p1[1] - d["y"]), (p2[0] - d["x"], p2[1] - d["y"])
        else:
            p1, p2 = (g["x1"] * w, g["y1"] * h), (g["x2"] * w, g["y2"] * h)
        dx, dy = p2[0] - p1[0], p2[1] - p1[1]
        length = math.hypot(dx, dy)
        d["fill"] = g["c1"]
        if length < 1e-6:
            continue
        ux, uy = dx / length, dy / length
        half = max(1.0, (abs(w * ux) + abs(h * uy)) / 2)
        a = ((p1[0] - w / 2) * ux + (p1[1] - h / 2) * uy) / half
        b = ((p2[0] - w / 2) * ux + (p2[1] - h / 2) * uy) / half
        d["grad"] = {"c": [0.5, 0.5], "ang": math.degrees(math.atan2(uy, ux)) % 360, "a": a, "b": b,
                     "c1": g["c1"], "c2": g["c2"]}




def _svg_data(path, markers=False):
    """(bytes, info): the SVG tidied so Qt draws it the way browsers do (font lists become one installed font;
    letters placed one by one, like matplotlib's maths text 2θ and cm⁻¹, become separately placed texts). With
    markers, gradient fills become stand-in colours recorded in info["grads"] (for importing as pieces)."""
    import os
    key = (os.path.abspath(str(path)), markers)
    stamp = os.path.getmtime(path)
    if key in _SVG_CACHE and _SVG_CACHE[key][0] == stamp:
        return _SVG_CACHE[key][1]
    out = _svg_prepare(path, markers)
    _SVG_CACHE[key] = (stamp, out)
    return out


def _svg_prepare(path, markers):
    import gzip
    import xml.etree.ElementTree as ET
    raw = open(path, "rb").read()
    if raw[:2] == b"\x1f\x8b":
        raw = gzip.decompress(raw)
    try:
        for prefix, uri in (("", SVG_NS), ("xlink", "http://www.w3.org/1999/xlink"),
                            ("inkscape", "http://www.inkscape.org/namespaces/inkscape"),
                            ("sodipodi", "http://sodipodi.sourceforge.net/DTD/sodipodi-0.dtd"),
                            ("rdf", "http://www.w3.org/1999/02/22-rdf-syntax-ns#"),
                            ("dc", "http://purl.org/dc/elements/1.1/"), ("cc", "http://creativecommons.org/ns#")):
            ET.register_namespace(prefix, uri)
        root = ET.fromstring(raw)
    except ET.ParseError:
        return raw, {}
    q = lambda tag: f"{{{SVG_NS}}}{tag}"  # noqa: E731
    vb = [_num(v, 0.0) for v in (root.get("viewBox") or "").replace(",", " ").split()]
    if len(vb) != 4 or vb[2] <= 0 or vb[3] <= 0:
        vb = [0.0, 0.0, _num(root.get("width"), 100.0), _num(root.get("height"), 100.0)]
    info = {"vb": vb, "grads": _svg_gradients(root, q) if markers else {}}
    for el in root.iter():
        if el.get("style") and "font" in el.get("style"):
            el.set("style", _fix_fonts(el.get("style")))
        if el.get("font-family"):
            el.set("font-family", _pick_font(el.get("font-family")))
        if el.tag == q("style") and el.text:
            el.text = _fix_fonts(el.text)
    for parent in list(root.iter()):
        for k, text in enumerate(list(parent)):
            if text.tag != q("text"):
                continue
            spans = [c for c in text if c.tag == q("tspan") and (c.get("x") is not None or c.get("y") is not None)]
            if not spans:
                continue
            pieces = []
            for span in spans:
                if not (span.text or "").strip():
                    continue
                t = ET.Element(q("text"), {a: v for a, v in text.attrib.items() if a not in ("x", "y", "dx", "dy")})
                t.attrib.update({a: v for a, v in span.attrib.items() if a != "style"})
                t.set("x", span.get("x", text.get("x", "0")))
                t.set("y", span.get("y", text.get("y", "0")))
                style = "; ".join(s for s in (text.get("style"), span.get("style")) if s)
                if style:
                    t.set("style", style)
                t.text = span.text
                pieces.append(t)
            idx = list(parent).index(text)
            parent.remove(text)
            for j, t in enumerate(pieces):
                parent.insert(idx + j, t)
    return ET.tostring(root, encoding="utf-8"), info


def _svg_renderer(path, markers=False):
    from PySide6.QtCore import QByteArray
    from PySide6.QtSvg import QSvgRenderer
    r = QSvgRenderer(QByteArray(_svg_data(path, markers)[0]))
    if not r.isValid():
        raise ValueError("this SVG can't be read")
    return r


def _svg_size(r):
    """The SVG's size in points (its viewBox, or its stated size; at most 2000 pt on the long side)."""
    box = r.viewBoxF()
    w, h = (box.width(), box.height()) if box.width() > 0 and box.height() > 0 else \
        (r.defaultSize().width(), r.defaultSize().height())
    w, h = max(1.0, float(w)), max(1.0, float(h))
    k = min(1.0, 2000.0 / max(w, h))
    return w * k, h * k


def svg_pdf(path):
    """The SVG drawn into a one-page vector PDF (bytes): paths stay paths, text stays text."""
    from PySide6.QtCore import QBuffer, QByteArray, QIODevice, QMarginsF, QRectF, QSizeF
    from PySide6.QtGui import QPageLayout, QPageSize, QPainter, QPdfWriter
    r = _svg_renderer(path, markers=True)
    w, h = _svg_size(r)
    data = QByteArray()
    buf = QBuffer(data)
    buf.open(QIODevice.WriteOnly)
    wr = QPdfWriter(buf)
    wr.setPageSize(QPageSize(QSizeF(w, h), QPageSize.Point))
    wr.setPageMargins(QMarginsF(0, 0, 0, 0), QPageLayout.Point)
    wr.setResolution(72)  # one painter unit = one point
    p = QPainter(wr)
    r.render(p, QRectF(0, 0, w, h))
    p.end()
    buf.close()
    return bytes(data)


def svg_image(path, width=2400):
    """The SVG as a crisp picture, `width` pixels wide (for inserting it as a plain picture)."""
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QPainter
    r = _svg_renderer(path)
    w, h = _svg_size(r)
    img = QImage(int(width), max(1, int(round(width * h / w))), QImage.Format_ARGB32_Premultiplied)
    img.fill(Qt.transparent)
    p = QPainter(img)
    p.setRenderHint(QPainter.Antialiasing)
    r.render(p)
    p.end()
    return img


def _open(path):
    """A PDF document for a PDF file, or for an SVG file (converted on the way)."""
    if is_svg(path):
        return pdfium.PdfDocument(svg_pdf(path))
    return pdfium.PdfDocument(str(path))


def page_count(path):
    pdf = _open(path)
    try:
        return len(pdf)
    finally:
        pdf.close()


def page_size(path, index):
    """(width, height) of a page in points."""
    pdf = _open(path)
    try:
        return pdf[index].get_size()
    finally:
        pdf.close()


def render_page(path, index, width=480):
    """A picture of the page for choosing which one to import."""
    pdf = _open(path)
    try:
        page = pdf[index]
        pw, _ = page.get_size()
        img = page.render(scale=width / max(1.0, pw)).to_pil()
        buf = io.BytesIO()
        img.save(buf, "PNG")
        return QImage.fromData(buf.getvalue())
    finally:
        pdf.close()


# ---------------------------------------------------------------------- helpers
def _matrix(obj):
    m = pr.FS_MATRIX()
    if not pr.FPDFPageObj_GetMatrix(obj, ctypes.byref(m)):
        return (1.0, 0.0, 0.0, 1.0, 0.0, 0.0)
    return (m.a, m.b, m.c, m.d, m.e, m.f)


def _compose(outer, inner):
    """The matrix that applies inner, then outer."""
    a, b, c, d, e, f = outer
    A, B, C, D, E, F = inner
    return (a * A + c * B, b * A + d * B, a * C + c * D, b * C + d * D, a * E + c * F + e, b * E + d * F + f)


def _apply(m, x, y):
    a, b, c, d, e, f = m
    return a * x + c * y + e, b * x + d * y + f


def _color(getter, obj):
    r, g, b, a = (ctypes.c_uint() for _ in range(4))
    if not getter(obj, ctypes.byref(r), ctypes.byref(g), ctypes.byref(b), ctypes.byref(a)):
        return None
    if a.value == 0:
        return None
    if a.value < 255:
        return f"#{a.value:02x}{r.value:02x}{g.value:02x}{b.value:02x}"  # Qt's #AARRGGBB
    return f"#{r.value:02x}{g.value:02x}{b.value:02x}"


_families = None


def _family(raw_name, flags):
    """A font this computer has that best matches a PDF font name (subset prefixes and PostScript styles removed)."""
    global _families
    if _families is None:
        _families = {f.lower().replace(" ", ""): f for f in QFontDatabase.families()}
    name = re.sub(r"^[A-Z]{6}\+", "", raw_name or "")
    base = re.split(r"[-,]", name)[0]
    base = re.sub(r"(PSMT|PS|MT)$", "", base)
    key = base.lower().replace(" ", "")
    if key in _families:
        return _families[key]
    low = name.lower()
    if "helvetica" in low or "arial" in low or "sans" in low:
        return "Arial"
    if any(k in low for k in ("courier", "mono", "cmtt", "consol")) or flags & FIXED_FLAG:
        return "Consolas"
    if any(k in low for k in ("times", "cmr", "lmroman", "roman", "serif", "nimbusrom", "garamond", "cmmi", "cmsy",
                              "stix", "palatino", "georgia")) or flags & SERIF_FLAG:
        return "Times New Roman"
    if "calibri" in low:
        return "Calibri"
    return "Arial"


# ---------------------------------------------------------------------- the page's drawings
def _path_item(obj, m, to_slide, scale):
    fillmode, stroke = ctypes.c_int(), ctypes.c_int()
    pr.FPDFPath_GetDrawMode(obj, ctypes.byref(fillmode), ctypes.byref(stroke))
    if fillmode.value == pr.FPDF_FILLMODE_NONE and not stroke.value:
        return None  # only used for clipping
    segs, bez = [], []
    x, y = ctypes.c_float(), ctypes.c_float()
    for k in range(pr.FPDFPath_CountSegments(obj)):
        seg = pr.FPDFPath_GetPathSegment(obj, k)
        pr.FPDFPathSegment_GetPoint(seg, ctypes.byref(x), ctypes.byref(y))
        sx, sy = to_slide(*_apply(m, x.value, y.value))
        kind = pr.FPDFPathSegment_GetType(seg)
        if kind == pr.FPDF_SEGMENT_MOVETO:
            segs.append(["M", sx, sy])
        elif kind == pr.FPDF_SEGMENT_LINETO:
            segs.append(["L", sx, sy])
        elif kind == pr.FPDF_SEGMENT_BEZIERTO:
            bez += [sx, sy]
            if len(bez) == 6:
                segs.append(["C"] + bez)
                bez = []
        if pr.FPDFPathSegment_GetClose(seg):
            segs.append(["Z"])
    if not segs or segs[0][0] != "M":
        return None
    fill = _color(pr.FPDFPageObj_GetFillColor, obj) if fillmode.value != pr.FPDF_FILLMODE_NONE else None
    line = _color(pr.FPDFPageObj_GetStrokeColor, obj) if stroke.value else None
    if not fill and not line:
        return None
    width = ctypes.c_float(1.0)
    pr.FPDFPageObj_GetStrokeWidth(obj, ctypes.byref(width))
    a, b, c, d, _, _ = m
    sw = max(0.5, width.value * math.sqrt(abs(a * d - b * c)) * scale) if line else 0
    dash = "solid"
    n = pr.FPDFPageObj_GetDashCount(obj)
    if line and n > 0:
        arr = (ctypes.c_float * n)()
        pr.FPDFPageObj_GetDashArray(obj, arr, n)
        dash = "dot" if arr[0] * scale <= sw * 1.6 else "dash"
    cap = {0: "butt", 1: "round", 2: "square"}.get(pr.FPDFPageObj_GetLineCap(obj), "butt")
    rule = "evenodd" if fillmode.value == pr.FPDF_FILLMODE_ALTERNATE else "nonzero"
    return SlidePath.from_segments(segs, fill=fill, stroke=line, sw=round(sw, 2), dash=dash, rule=rule, cap=cap)


def _image_item(pdf, page, obj, m, to_slide, store):
    from . import media
    if store is None:  # a dry run (the self-test) leaves the library alone
        return None
    corners = [to_slide(*_apply(m, u, v)) for u, v in ((0, 0), (1, 0), (0, 1), (1, 1))]
    xs, ys = [c[0] for c in corners], [c[1] for c in corners]
    w, h = max(xs) - min(xs), max(ys) - min(ys)
    if w < 1 or h < 1:
        return None
    raw = pr.FPDFImageObj_GetRenderedBitmap(pdf.raw, page.raw, obj)  # with its mask, the way it's shown
    if not raw:
        return None
    pil = pdfium.PdfBitmap.from_raw(raw).to_pil()
    buf = io.BytesIO()
    pil.save(buf, "PNG")
    img = QImage.fromData(buf.getvalue())
    if img.isNull():
        return None
    a, iw, ih = media.import_qimage(store, img)
    return {"type": "image", "x": min(xs), "y": min(ys), "w": w, "h": h, "asset": a, "iw": iw, "ih": ih,
            "crop": [0, 0, iw, ih], "name": "PDF picture"}


def _walk(pdf, page, objs, m, to_slide, scale, store, out, stats):
    for obj in objs:
        kind = pr.FPDFPageObj_GetType(obj)
        mm = _compose(m, _matrix(obj))
        if kind == pr.FPDF_PAGEOBJ_FORM:
            _walk(pdf, page, (pr.FPDFFormObj_GetObject(obj, j) for j in range(pr.FPDFFormObj_CountObjects(obj))),
                  mm, to_slide, scale, store, out, stats)
        elif kind == pr.FPDF_PAGEOBJ_PATH:
            d = _path_item(obj, mm, to_slide, scale)
            if d is not None:
                out.append(d)
        elif kind == pr.FPDF_PAGEOBJ_IMAGE:
            d = _image_item(pdf, page, obj, mm, to_slide, store)
            if d is not None:
                out.append(d)
        elif kind == pr.FPDF_PAGEOBJ_SHADING:
            stats["skipped"] += 1  # gradients aren't brought over


def _merge_runs(items):
    """Consecutive paths drawn in the same style join up: one plot series = one item."""
    out = []
    for d in items:
        prev = out[-1] if out else None
        style = ("path", d.get("fill"), d.get("stroke"), d.get("sw"), d.get("dash"), d.get("rule"), d.get("cap"))
        if prev is not None and d.get("type") == "path" and prev.get("_style") == style \
                and len(prev["_segs"]) + len(d["d"]) < 20000:
            prev["_segs"] += _absolute(d)
            continue
        if d.get("type") == "path":
            d = dict(d, _style=style, _segs=_absolute(d))
        out.append(d)
    result = []
    for d in out:
        if d.get("type") == "path":
            style = {k: d[k] for k in ("fill", "stroke", "sw", "dash", "rule", "cap")}
            result.append(SlidePath.from_segments(d["_segs"], **style))
        else:
            result.append(d)
    return result


def _absolute(d):
    return [[s[0]] + [v + (d["x"] if i % 2 == 0 else d["y"]) for i, v in enumerate(s[1:])] for s in d["d"]]


# ---------------------------------------------------------------------- the page's text
def _text_items(textpage, to_slide, scale):
    """One text box per line of text. A line keeps its mixed styles (an italic symbol, a bold word, a smaller
    superscript) as styled pieces, so it stays one editable box. Where a line starts and ends is decided from
    the letters' positions, not from PDFium's guesses, which split labels whose letters are placed one by one."""
    tp = textpage.raw
    box = pr.FS_RECTF()
    ox, oy = ctypes.c_double(), ctypes.c_double()
    flags = ctypes.c_int()
    cm = pr.FS_MATRIX()
    buf = ctypes.create_string_buffer(256)
    # 1. every visible letter, with where it sits along and across its own baseline direction
    chars, pending = [], False
    for i in range(pr.FPDFText_CountChars(tp)):
        ch = chr(pr.FPDFText_GetUnicode(tp, i) or 32)
        obj = pr.FPDFText_GetTextObject(tp, i)
        if obj and pr.FPDFTextObj_GetTextRenderMode(obj) == pr.FPDF_TEXTRENDERMODE_INVISIBLE:
            continue  # the hidden text layer of a scanned page: the picture already shows it
        if ch in "\r\n\x02\x00":
            continue
        if ch.isspace():
            pending = True
            continue
        pr.FPDFText_GetLooseCharBox(tp, i, ctypes.byref(box))  # the letter's full advance, not just its ink
        pr.FPDFText_GetCharOrigin(tp, i, ctypes.byref(ox), ctypes.byref(oy))
        # the letter's own matrix holds the real size and direction (the font size alone ignores page scaling)
        pr.FPDFText_GetMatrix(tp, i, ctypes.byref(cm))
        size = (pr.FPDFText_GetFontSize(tp, i) or 10.0) * (math.hypot(cm.c, cm.d) or 1.0)
        n = pr.FPDFText_GetFontInfo(tp, i, buf, len(buf), ctypes.byref(flags))
        font = buf.raw[:max(0, n - 1)].decode("utf-8", "replace") if n else ""
        weight = pr.FPDFText_GetFontWeight(tp, i)
        cr, cg, cb, ca = (ctypes.c_uint() for _ in range(4))
        pr.FPDFText_GetFillColor(tp, i, ctypes.byref(cr), ctypes.byref(cg), ctypes.byref(cb), ctypes.byref(ca))
        angle = math.atan2(cm.b, cm.a)  # counter-clockwise, as on the page
        cos, sin = math.cos(angle), math.sin(angle)
        along, across = ox.value * cos + oy.value * sin, -ox.value * sin + oy.value * cos
        low = font.lower()
        chars.append({"ch": ch, "along": along, "across": across, "size": size, "angle": angle, "font": font,
                      "end": along + (abs(box.right - box.left) if abs(sin) < 0.5 else abs(box.top - box.bottom)),
                      "flags": flags.value, "color": f"#{cr.value:02x}{cg.value:02x}{cb.value:02x}",
                      "bold": weight >= 600 or "bold" in low or "black" in low or "heavy" in low,
                      "italic": bool(flags.value & ITALIC_FLAG) or "italic" in low or "oblique" in low,
                      "space": pending, "i": len(chars), "obj": ctypes.cast(obj, ctypes.c_void_p).value})
        pending = False
    # a letter's width is best measured to the next letter of the same run (true at any angle; a turned letter's
    # box is wider than the letter)
    for a, b in zip(chars, chars[1:]):
        step = b["along"] - a["along"]
        if a["obj"] and a["obj"] == b["obj"] and abs(a["angle"] - b["angle"]) < 0.02 and 0 < step < 1.5 * a["size"] \
                and not b["space"]:
            a["end"] = a["along"] + step
    # 2. lines, in the order the letters were drawn
    lines, cur = [], None
    for c in chars:
        joins = False
        if cur is not None and abs(c["angle"] - cur["angle"]) < 0.02:
            big, small = max(c["size"], cur["size"]), min(c["size"], cur["size"])
            gap, rise = c["along"] - cur["end"], c["across"] - cur["across"]
            reach = 0.6 if small < 0.9 * big else 0.3  # smaller letters may sit higher or lower (super/subscripts)
            joins = -0.5 * big <= gap <= 1.3 * big and abs(rise) <= reach * big
        if not joins:
            cur = {"angle": c["angle"], "across": c["across"], "size": c["size"], "start": c["along"], "end": c["along"],
                   "chars": []}
            lines.append(cur)
        cur["chars"].append(c)
        cur["start"] = min(cur["start"], c["along"])
        cur["end"] = max(cur["end"], c["end"])

    # 3. pieces drawn out of order rejoin their line: a superscript drawn after the closing bracket, or a
    #    line drawn in two goes
    def belongs(a, b):
        if abs(a["angle"] - b["angle"]) >= 0.02:
            return False
        bsize = max(c["size"] for c in b["chars"])
        rise = sum(c["across"] for c in b["chars"]) / len(b["chars"]) - a["across"]
        near = b["start"] <= a["end"] + 1.3 * a["size"] and b["end"] >= a["start"] - 1.3 * a["size"]
        if bsize < 0.9 * a["size"]:  # smaller letters just above or below: super/subscripts
            return near and abs(rise) <= 0.6 * a["size"] and b["start"] >= a["start"] - 0.5 * a["size"]
        return near and abs(bsize - a["size"]) < 0.1 * a["size"] and abs(rise) <= 0.15 * a["size"]

    merged = True
    while merged:
        merged = False
        for la in lines:
            for lb in lines:
                if la is not lb and la["chars"] and lb["chars"] and belongs(la, lb):
                    la["chars"] += lb["chars"]
                    lb["chars"] = []
                    la["start"], la["end"] = min(la["start"], lb["start"]), max(la["end"], lb["end"])
                    merged = True
    lines = [ln for ln in lines if ln["chars"]]
    # 4. each line's letters left to right, with spaces where the gaps are, grouped into styled pieces
    for line in lines:
        cs = sorted(line["chars"], key=lambda c: c["along"])
        size = max(c["size"] for c in cs)
        base = [c["across"] for c in cs if c["size"] >= 0.9 * size]
        base = sorted(base)[len(base) // 2]
        segs, prev = [], None
        for c in cs:
            rise = c["across"] - base
            v = ""
            if c["size"] < 0.9 * size:
                v = "super" if rise > 0.15 * size else "sub" if rise < -0.1 * size else ""
            key = (c["font"], round(c["size"] * 2) / 2, c["color"], c["bold"], c["italic"], v)
            if segs and prev is not None:
                gap, em, need = c["along"] - prev["end"], min(c["size"], prev["size"]), 0.22
                if v and v == segs[-1]["v"]:
                    em, need = size, 0.5  # inside a super/subscript (cm⁻¹): spaces are rare, gaps are font slack
                elif v != segs[-1]["v"]:
                    em, need = size, 0.3  # into or out of one: judged by the line's own letters
                # PDFium's own space guesses follow drawing order: trust them only between letters drawn one after
                # the other, and never inside a super/subscript
                flagged = c["space"] and c["i"] == prev["i"] + 1 and not (v and v == segs[-1]["v"])
                if (gap > need * em or flagged) and not segs[-1]["text"].endswith(" "):
                    segs[-1]["text"] += " "
            if segs and segs[-1]["key"] == key:
                segs[-1]["text"] += c["ch"]
            else:
                segs.append({"key": key, "text": c["ch"], "font": c["font"], "flags": c["flags"], "size": c["size"],
                             "color": c["color"], "bold": c["bold"], "italic": c["italic"], "v": v})
            prev = c
        cos, sin = math.cos(line["angle"]), math.sin(line["angle"])
        first = cs[0]["along"]
        line.update(segs=segs, size=size, origin=(first * cos - base * sin, first * sin + base * cos))
    items = []
    for line in lines:
        segs = line["segs"]
        if segs:
            segs[-1]["text"] = segs[-1]["text"].rstrip()
        for sg in segs:  # some PDF writers label a minus sign as a soft hyphen, which would vanish
            sg["text"] = re.sub("\xad(?=[0-9.,])", "\u2212", sg["text"]).replace("\xad", "-")
        segs = [sg for sg in segs if sg["text"]]
        if not segs:
            continue
        main = max(segs, key=lambda sg: len(sg["text"]) * (0.3 if sg["v"] else 1.0))  # the line's own style
        base_px = max(4.0, line["size"] * scale)
        width, ascent, height, spans = 0.0, 0.0, 0.0, []
        for sg in segs:
            family = _family(sg["font"], sg["flags"])
            px = max(4.0, (line["size"] if sg["v"] else sg["size"]) * scale)  # Qt shrinks super/subscripts itself
            qf = QFont(family)
            qf.setPixelSize(max(1, int(round(px * (0.66 if sg["v"] else 1.0)))))
            qf.setBold(sg["bold"])
            qf.setItalic(sg["italic"])
            fm = QFontMetricsF(qf)
            width += fm.horizontalAdvance(sg["text"])
            if not sg["v"]:
                ascent = max(ascent, fm.ascent())
            height = max(height, fm.height())
            css = (f"font-family:'{family}';font-size:{int(round(px))}px;"
                   f"font-weight:{700 if sg['bold'] else 400};font-style:{'italic' if sg['italic'] else 'normal'};"
                   f"color:{sg['color']}" + (f";vertical-align:{sg['v']}" if sg["v"] else ""))
            spans.append(f'<span style="{css}">{html.escape(sg["text"])}</span>')
        if not ascent:
            qf = QFont(_family(main["font"], main["flags"]))
            qf.setPixelSize(max(1, int(round(base_px))))
            ascent = QFontMetricsF(qf).ascent()
        w = width * 1.06 + 10  # a little slack so the line never wraps
        h = height + 4
        rot = -math.degrees(line["angle"])
        px_origin = QPointF(*to_slide(*line["origin"]))
        local = QPointF(2.0, 2.0 + ascent)  # where the first letter's baseline starts inside the box
        centre = QPointF(w / 2, h / 2)
        pos = px_origin - centre - QTransform().rotate(rot).map(local - centre)
        items.append({"type": "text", "x": round(pos.x(), 2), "y": round(pos.y(), 2), "w": round(w, 1),
                      "h": round(h, 1), "minh": round(h, 1), "rot": round(rot, 2) % 360, "pad": 0,
                      "font": _family(main["font"], main["flags"]),
                      "size": round(max(4.0, main["size"] * scale), 2), "color": main["color"],
                      "html": "".join(spans)})
    return items


# ---------------------------------------------------------------------- putting it together
def page_items(path, index, store, box):
    """Items for page `index`, fitted into `box` (QRectF in slide coordinates). Returns (items, notes)."""
    pdf = _open(path)
    try:
        page = pdf[index]
        pw, ph = page.get_size()
        scale = min(box.width() / pw, box.height() / ph)
        ox = box.x() + (box.width() - pw * scale) / 2
        oy = box.y() + (box.height() - ph * scale) / 2

        def to_slide(x, y):
            return ox + x * scale, oy + (ph - y) * scale

        stats = {"skipped": 0}
        drawn = []
        objs = (pr.FPDFPage_GetObject(page.raw, i) for i in range(pr.FPDFPage_CountObjects(page.raw)))
        _walk(pdf, page, objs, (1.0, 0.0, 0.0, 1.0, 0.0, 0.0), to_slide, scale, store, drawn, stats)
        # a white sheet behind everything is just the page itself
        page_area = pw * ph * scale * scale
        drawn = [d for d in drawn if not (d.get("type") == "path" and not d.get("stroke") and
                                          (d.get("fill") or "").lower() in ("#ffffff", "#fefefe") and
                                          d["w"] * d["h"] >= 0.9 * page_area)]
        items = _merge_runs(drawn)
        if is_svg(path):
            info = _svg_data(path, True)[1]
            vx, vy, vw, vh = info.get("vb") or (0.0, 0.0, pw, ph)
            kk = pw / vw if vw else 1.0

            def to_slide_svg(x, y):  # SVG user units (as drawn onto the PDF page) to slide
                return ox + (x - vx) * kk * scale, oy + (y - vy) * kk * scale
            _apply_svg_gradients(items, info, to_slide_svg)
        items += _text_items(page.get_textpage(), to_slide, scale)
        z = 1
        for d in items:
            d["z"] = z
            d["id"] = short_id()
            z += 1
        notes = []
        if stats["skipped"]:
            notes.append(f"{stats['skipped']} gradient fill(s) were left out")
        if not items:
            notes.append("this page has no drawings or text to bring over")
        return items, notes
    finally:
        pdf.close()


def page_doc(path, index, store, width=1280.0):
    """A whole evidence slide for one PDF page, sized like the page."""
    from PySide6.QtCore import QRectF
    pw, ph = page_size(path, index)
    h = width * ph / pw
    items, notes = page_items(path, index, store, QRectF(0, 0, width, h))
    return {"v": 1, "w": width, "h": round(h, 1), "bg": "#ffffff", "notes": "", "items": items}, notes
