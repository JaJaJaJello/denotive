"""Makes the training set: random figures drawn by DeNotive, each saved as a clean picture plus its exact answer.

    python main.py generate --count 60000

Pictures go to <out>/img/*.png (960 x 540, clean; blur and JPEG damage are added later, while training, so the
same picture can be shown at every level of damage). <out>/index.jsonl holds one line per picture: the file,
the token sentence the model should write for it, and how many pieces it has.
"""
import json
import math
import os
import random
import sys
import time
from pathlib import Path

from . import tokens as T
from .render import ensure_app, measured, qimage_to_pil, text_box

FONTS = ("Arial", "Segoe UI", "Calibri", "Times New Roman", "Verdana", "Tahoma", "Cambria", "Consolas")
WORDS = ("Temperature", "Intensity", "Counts", "Time", "Pressure", "Sample", "Peak", "Phase", "Signal", "Model",
         "Fit", "Data", "Error", "Input", "Output", "Detector", "Beam", "Lens", "Source", "Control", "Result",
         "Energy", "Voltage", "Current", "Strain", "Stress", "Depth", "Rate", "Yield", "Ratio", "Mass", "Flow",
         "Step", "Stage", "Filter", "Buffer", "Cell", "Batch", "Test", "Trial", "Group", "Region", "Layer", "Grain",
         "Anneal", "Heat", "Cool", "Mix", "Scan", "Image", "Map", "Plot", "Run", "Day", "Week", "Load", "Field")
UNITS = ("(K)", "(s)", "(a.u.)", "(deg)", "(mm)", "(MPa)", "(%)", "(eV)", "(V)", "(mA)", "(nm)", "(°C)", "(min)",
         "(Hz)", "(cm⁻¹)", "(counts)")
PALETTE = [T.dcolor(T.V.tok("color", i)) for i in range(1, 217)]


def _lum(c):
    r, g, b = T.hex_rgb(c)
    return (0.299 * r + 0.587 * g + 0.114 * b) / 255


def _sat(c):
    r, g, b = T.hex_rgb(c)
    return (max(r, g, b) - min(r, g, b)) / 255


DARK = [c for c in PALETTE if _lum(c) < 0.3]
LIGHT = [c for c in PALETTE if _lum(c) > 0.8]
VIVID = [c for c in PALETTE if _sat(c) >= 0.6 and 0.25 < _lum(c) < 0.75]
GREYS = [c for c in PALETTE if _sat(c) == 0]
MID = [c for c in PALETTE if 0.3 <= _lum(c) <= 0.8]


def _mix(c, other, t):
    a, b = T.hex_rgb(c), T.hex_rgb(other)
    return T.snap_color("#{:02x}{:02x}{:02x}".format(*(round(x + (y - x) * t) for x, y in zip(a, b))))


GRAD_ANGLES = (90, 90, 270, 0, 180, 45, 135, 225, 315)  # top-to-bottom is the most common, then sideways
GRAD_LO = [o for o in T.OFFS if -1.4 <= o <= -0.2]
GRAD_HI = [o for o in T.OFFS if 0.2 <= o <= 1.4]


def grad_style(rng):
    """How a gradient runs (direction, where its two lines sit, how the second colour is chosen), so several
    pieces can share one look, like the bars of one chart."""
    ang = rng.choice(GRAD_ANGLES) if rng.random() < 0.75 else rng.choice(T.ANGS)
    if rng.random() < 0.6:
        a, b = -1.0, 1.0  # edge to edge, the default in most programs
    else:
        a, b = rng.choice(GRAD_LO), rng.choice(GRAD_HI)
    return ang, a, b, rng.choice(("white", "white", "lighter", "darker", "other"))


def make_grad(rng, fill, style=None):
    """A gradient from `fill` to a second colour the model can say, or None if it would look solid."""
    ang, a, b, how = style or grad_style(rng)
    c2 = {"white": "#ffffff", "lighter": _mix(fill, "#ffffff", 0.6), "darker": _mix(fill, "#000000", 0.5),
          "other": rng.choice(PALETTE)}[how]
    if c2 == fill:
        c2 = _mix(fill, "#000000" if _lum(fill) > 0.5 else "#ffffff", 0.6)
    if c2 == fill:
        return None
    return {"c2": c2, "ang": float(ang), "a": a, "b": b}


def word(rng, cap=True):
    r = rng.random()
    if r < 0.7:
        w = rng.choice(WORDS)
    elif r < 0.85:
        w = "".join(rng.choice("abcdefghijklmnopqrstuvwxyz") for _ in range(rng.randint(2, 8)))
    else:
        w = rng.choice("ABCDEFGHIJKLMNOPQRSTUVWXYZ") + str(rng.randint(1, 99))
    return w.capitalize() if cap else w.lower()


def phrase(rng, lo=1, hi=3):
    n = rng.randint(lo, hi)
    out = [word(rng, i == 0) for i in range(n)]
    if rng.random() < 0.3:
        out.append(rng.choice(UNITS))
    return " ".join(out)[: T.MAX_CHARS]


def number(rng, v, step):
    if step >= 1 and float(v).is_integer():
        return str(int(v))
    g = f"{step:g}"
    digits = len(g.split(".")[1]) if "." in g else 0
    s = f"{v:.{digits}f}"
    return s.replace("-", "−") if rng.random() < 0.5 else s


# The shape of the picture a figure came from (width / height) and how often each turns up. Real pictures are
# fitted into the 16:9 slide with white margins (see predict), so the practice figures are too.
FRAMES = ((16 / 9, 0.35), (4 / 3, 0.20), (3 / 2, 0.10), (1.0, 0.12), (3 / 4, 0.08), (2.0, 0.07), (3.0, 0.04),
          (9 / 16, 0.04))
AREA = T.CANVAS_W * T.CANVAS_H


def pick_frame(rng):
    """Where on the slide the figure sits. The figure is laid out on its own canvas of W x H (the same area as
    the slide, so text and lines keep sensible sizes whatever the shape) and then shrunk by k to fit the slide,
    centred like a fitted picture. Sometimes there's extra white space around it, as in a loose screenshot,
    and sometimes it sits off-centre."""
    r, acc, aspect = rng.random(), 0.0, FRAMES[0][0]
    for a, p in FRAMES:
        acc += p
        if r <= acc:
            aspect = a
            break
    W, H = math.sqrt(AREA * aspect), math.sqrt(AREA / aspect)
    k = min(T.CANVAS_W / W, T.CANVAS_H / H)
    if rng.random() < 0.35:
        k *= rng.uniform(0.6, 0.95)
    spare_x, spare_y = T.CANVAS_W - W * k, T.CANVAS_H - H * k
    if rng.random() < 0.75:
        ox, oy = spare_x / 2, spare_y / 2
    else:
        ox, oy = rng.uniform(0, spare_x), rng.uniform(0, spare_y)
    return {"W": W, "H": H, "k": k, "ox": ox, "oy": oy, "aspect": aspect}


FULL = {"W": T.CANVAS_W, "H": T.CANVAS_H, "k": 1.0, "ox": 0.0, "oy": 0.0, "aspect": 16 / 9}


def snap_size(px):
    return min(T.SIZES, key=lambda s: abs(s - px))


def snap_width(w):
    return min(T.WIDTHS, key=lambda s: abs(s - w))


class Fig:
    """Collects pieces by layer, so things that belong behind are painted first. Generators draw on the figure's
    own W x H canvas; every piece is moved onto the slide here, with text sizes and line widths snapped to ones
    the model can say (so the answer stays exact)."""

    def __init__(self, rng, font, frame=None):
        self.rng, self.font = rng, font
        f = frame or FULL
        self.W, self.H, self.k, self.ox, self.oy = f["W"], f["H"], f["k"], f["ox"], f["oy"]
        self.layers = {k: [] for k in ("back", "mid", "front", "marks", "text")}

    def P(self, x, y):
        return self.ox + x * self.k, self.oy + y * self.k

    def add(self, layer, spec):
        s = dict(spec)
        if s["t"] in ("shape", "line"):
            s["x0"], s["y0"] = self.P(s["x0"], s["y0"])
            s["x1"], s["y1"] = self.P(s["x1"], s["y1"])
        elif s["t"] == "path":
            s["pts"] = [self.P(x, y) for x, y in s["pts"]]
        elif s["t"] == "image":
            s["x0"], s["y0"] = self.P(s["x0"], s["y0"])
            s["x1"], s["y1"] = self.P(s["x1"], s["y1"])
        if "sw" in s:
            s["sw"] = snap_width(float(s["sw"] or 0) * self.k)
        if s["t"] == "shape":
            s["size"] = snap_size(float(s.get("size", 20)) * self.k)
        self.layers[layer].append(s)

    def text(self, s, x, y, size, color="#000000", bold=False, rot=0, ha="left", va="top", layer="text"):
        s = s[: T.MAX_CHARS]
        if not s.strip():
            return
        size = snap_size(size * self.k)
        px, py = self.P(x, y)
        x0, y0, x1, y1 = text_box(s, size, bold, rot, self.font, px, py, ha, va)
        self.layers[layer].append({"t": "text", "x0": x0, "y0": y0, "x1": x1, "y1": y1, "color": color,
                                   "size": size, "bold": bold, "rot": rot, "text": s})

    def specs(self):
        out = []
        for k in ("back", "mid", "front", "marks", "text"):
            out += self.layers[k]
        return out


def _series(rng, n):
    xs = [i / (n - 1) for i in range(n)]
    kind = rng.choice(("sines", "peaks", "peaks", "walk", "line", "decay", "steps"))
    if kind == "sines":
        terms = [(rng.uniform(0.5, 4), rng.uniform(0, 6.3), rng.uniform(0.2, 1)) for _ in range(rng.randint(1, 3))]
        ys = [sum(a * math.sin(f * 6.28 * x + p) for f, p, a in terms) for x in xs]
    elif kind == "peaks":
        pk = [(rng.uniform(0.05, 0.95), rng.uniform(0.01, 0.06), rng.uniform(0.3, 1)) for _ in range(rng.randint(1, 4))]
        base = rng.uniform(0, 0.15)
        ys = [base + sum(a * math.exp(-((x - c) / w) ** 2) for c, w, a in pk) for x in xs]
    elif kind == "walk":
        v, ys = 0.0, []
        for _ in xs:
            v += rng.gauss(0, 1)
            ys.append(v)
    elif kind == "line":
        a, b = rng.uniform(-1, 1), rng.uniform(-1, 1)
        ys = [a * x + b + rng.gauss(0, 0.04) for x in xs]
    elif kind == "decay":
        k = rng.uniform(1, 8)
        ys = [math.exp(-k * x) + rng.gauss(0, 0.01) for x in xs]
    else:
        lv, ys = rng.random(), []
        for _ in xs:
            if rng.random() < 0.15:
                lv = rng.random()
            ys.append(lv)
    lo, hi = min(ys), max(ys)
    span = (hi - lo) or 1.0
    a, b = sorted((rng.uniform(0.0, 0.45), rng.uniform(0.55, 1.0)))
    return xs, [a + (y - lo) / span * (b - a) for y in ys]


def _axes(fig, rng, plot, xticks=True, grid=True):
    x0, y0, x1, y1 = plot
    ink = rng.choice(DARK)
    sw = rng.choice((1.0, 1.5, 2.0))
    if rng.random() < 0.6:
        fig.add("front", {"t": "shape", "kind": "rect", "x0": x0, "y0": y0, "x1": x1, "y1": y1, "fill": None,
                          "stroke": ink, "sw": sw, "size": 20, "text": ""})
    else:
        fig.add("front", {"t": "line", "x0": x0, "y0": y1, "x1": x1, "y1": y1, "color": ink, "sw": sw,
                          "dash": "solid", "a1": False, "a2": rng.random() < 0.15})
        fig.add("front", {"t": "line", "x0": x0, "y0": y1, "x1": x0, "y1": y0, "color": ink, "sw": sw,
                          "dash": "solid", "a1": False, "a2": rng.random() < 0.15})
    size = rng.choice((16, 18, 20, 24))
    grid = grid and rng.random() < 0.35  # (never behind a picture or filled bands, where it can't be seen)
    gcol = rng.choice(LIGHT)
    # y ticks
    ny = rng.randint(3, 6)
    step = rng.choice((0.1, 0.2, 0.25, 0.5, 1, 2, 5, 10, 20, 50, 100))
    start = rng.choice((0, 0, step * rng.randint(-3, 5)))
    for i in range(ny + 1):
        y = y1 - (y1 - y0) * i / ny
        fig.add("front", {"t": "line", "x0": x0 - 8, "y0": y, "x1": x0, "y1": y, "color": ink, "sw": sw,
                          "dash": "solid", "a1": False, "a2": False})
        if grid and 0 < i < ny:
            fig.add("back", {"t": "line", "x0": x0, "y0": y, "x1": x1, "y1": y, "color": gcol, "sw": 1.0,
                             "dash": rng.choice(("solid", "dash")), "a1": False, "a2": False})
        fig.text(number(rng, start + step * i, step), x0 - 12, y, size, ink, ha="right", va="middle")
    if xticks:
        nx = rng.randint(3, 7)
        step = rng.choice((0.5, 1, 2, 5, 10, 20, 50, 100, 200))
        start = rng.choice((0, step * rng.randint(0, 6)))
        for i in range(nx + 1):
            x = x0 + (x1 - x0) * i / nx
            fig.add("front", {"t": "line", "x0": x, "y0": y1, "x1": x, "y1": y1 + 8, "color": ink, "sw": sw,
                              "dash": "solid", "a1": False, "a2": False})
            if grid and 0 < i < nx:
                fig.add("back", {"t": "line", "x0": x, "y0": y0, "x1": x, "y1": y1, "color": gcol, "sw": 1.0,
                                 "dash": "solid", "a1": False, "a2": False})
            fig.text(number(rng, start + step * i, step), x, y1 + 12, size, ink, ha="center")
    lab = rng.choice((20, 24, 28))
    if rng.random() < 0.85:
        fig.text(phrase(rng, 1, 2), (x0 + x1) / 2, y1 + 18 + size + 6, lab, ink, ha="center")
    if rng.random() < 0.85:
        fig.text(phrase(rng, 1, 2), x0 - 70 - rng.randint(0, 20), (y0 + y1) / 2, lab, ink, rot=270, ha="center",
                 va="middle")
    if rng.random() < 0.6:
        fig.text(phrase(rng, 1, 4), (x0 + x1) / 2, y0 - 12, rng.choice((24, 28, 32, 40)), ink,
                 bold=rng.random() < 0.4, ha="center", va="bottom")
    return ink, size


def _plot_box(rng, fig):
    W, H = fig.W, fig.H
    return (W * rng.uniform(0.12, 0.2), H * rng.uniform(0.11, 0.21), W * rng.uniform(0.77, 0.95),
            H * rng.uniform(0.75, 0.83))


def chart(rng, font, frame=None):
    fig = Fig(rng, font, frame)
    plot = _plot_box(rng, fig)
    x0, y0, x1, y1 = plot
    ink, size = _axes(fig, rng, plot)
    cols = rng.sample(VIVID, k=4)
    names = []
    for k in range(rng.choice((1, 1, 2, 2, 3, 4))):
        n = rng.randint(8, T.MAX_POINTS - 2)
        xs, ys = _series(rng, n)
        pts = [(x0 + (x1 - x0) * x, y1 - (y1 - y0) * y) for x, y in zip(xs, ys)]
        style = rng.random()
        col = cols[k]
        dash = rng.choice(("solid", "solid", "solid", "dash", "dot"))
        if style < 0.75 and k == 0 and rng.random() < 0.2:  # an area chart: shaded down to the axis
            shade = col if rng.random() < 0.5 else _mix(col, "#ffffff", 0.5)
            area = pts + [(pts[-1][0], y1), (pts[0][0], y1)]
            fig.add("mid", {"t": "path", "stroke": None, "fill": shade, "sw": 1.0, "dash": "solid", "closed": True,
                            "pts": area, "grad": make_grad(rng, shade, (90, rng.choice((-1.0, -0.8, -0.6)),
                                                                        rng.choice((0.6, 0.8, 1.0)), "white"))
                            if rng.random() < 0.7 else None})
        if style < 0.75:
            fig.add("mid", {"t": "path", "stroke": col, "fill": None, "sw": rng.choice((1.5, 2.0, 3.0, 4.0)),
                            "dash": dash, "closed": False, "pts": pts})
        if style > 0.55:
            kind = rng.choice(("ellipse", "rect", "triangle", "diamond"))
            r = rng.choice((5, 6, 7, 8))
            every = max(1, n // rng.randint(5, 10))
            for (px, py) in pts[::every][:10]:
                fig.add("marks", {"t": "shape", "kind": kind, "x0": px - r, "y0": py - r, "x1": px + r, "y1": py + r,
                                  "fill": col, "stroke": rng.choice((None, None, ink)), "sw": 1.0, "size": 20,
                                  "text": ""})
        names.append((col, dash))
    if len(names) > 1 and rng.random() < 0.7:
        lx, ly = (x1 - 230, y0 + 16) if rng.random() < 0.5 else (x0 + 20, y0 + 16)
        h = 16 + 30 * len(names)
        if rng.random() < 0.7:
            fig.add("front", {"t": "shape", "kind": "rect", "x0": lx - 10, "y0": ly - 8, "x1": lx + 210,
                              "y1": ly + h - 8, "fill": "#ffffff", "stroke": rng.choice(GREYS[1:-1] or GREYS),
                              "sw": 1.0, "size": 20, "text": ""})
        for i, (col, dash) in enumerate(names):
            y = ly + 14 + 30 * i
            fig.add("front", {"t": "line", "x0": lx, "y0": y, "x1": lx + 44, "y1": y, "color": col, "sw": 2.0,
                              "dash": dash, "a1": False, "a2": False})
            fig.text(phrase(rng, 1, 2)[:16], lx + 54, y, size, ink, va="middle")
    return fig


def bars(rng, font, frame=None):
    fig = Fig(rng, font, frame)
    plot = _plot_box(rng, fig)
    x0, y0, x1, y1 = plot
    ink, size = _axes(fig, rng, plot, xticks=False)
    n = rng.randint(2, 8)
    same = rng.random() < 0.5
    col = rng.choice(VIVID)
    cols = rng.sample(VIVID, k=n) if len(VIVID) >= n else [col] * n
    slot = (x1 - x0) / n
    bw = slot * rng.uniform(0.45, 0.8)
    gstyle = grad_style(rng) if rng.random() < 0.3 else None
    for i in range(n):
        cx = x0 + slot * (i + 0.5)
        top = y1 - (y1 - y0) * rng.uniform(0.1, 0.95)
        fill = col if same else cols[i]
        fig.add("mid", {"t": "shape", "kind": "rect", "x0": cx - bw / 2, "y0": top, "x1": cx + bw / 2, "y1": y1,
                        "fill": fill, "grad": make_grad(rng, fill, gstyle) if gstyle else None,
                        "stroke": rng.choice((None, None, ink)), "sw": 1.0, "size": 20, "text": ""})
        fig.text(word(rng)[:10], cx, y1 + 12, size, ink, ha="center")
    return fig


def _clip(cx, cy, hw, hh, tx, ty):
    """Where the line from a box's centre towards (tx, ty) leaves the box."""
    dx, dy = tx - cx, ty - cy
    if abs(dx) < 1e-6 and abs(dy) < 1e-6:
        return cx, cy
    s = min(hw / abs(dx) if dx else 1e9, hh / abs(dy) if dy else 1e9)
    return cx + dx * s, cy + dy * s


def diagram(rng, font, frame=None):
    fig = Fig(rng, font, frame)
    W, H = fig.W, fig.H
    top = 140 if rng.random() < 0.6 else 60
    cols = rng.randint(2, max(2, min(4, int(W / 280))))
    rows = rng.randint(1, max(1, min(3, int((H - top) / 180))))
    cells = [(c, r) for c in range(cols) for r in range(rows)]
    rng.shuffle(cells)
    nodes = []
    cw, ch = (W - 80) / cols, (H - 40 - top) / rows
    fill_style = rng.random()
    base = rng.choice(VIVID)
    gstyle = grad_style(rng) if rng.random() < 0.3 else None
    for (c, r) in cells[: rng.randint(2, min(7, len(cells)))]:
        w, h = rng.uniform(0.45, 0.8) * cw, rng.uniform(0.35, 0.7) * ch
        w, h = min(w, 300), min(h, 160)
        cx = 40 + cw * (c + 0.5) + rng.uniform(-0.1, 0.1) * cw
        cy = top + ch * (r + 0.5) + rng.uniform(-0.1, 0.1) * ch
        kind = rng.choice(T.SHAPES[:7] + ("rect", "round", "round", "ellipse"))
        fill = base if fill_style < 0.3 else rng.choice(VIVID + LIGHT) if fill_style < 0.8 else None
        stroke = rng.choice(DARK) if (fill is None or rng.random() < 0.4) else None
        fig.add("back", {"t": "shape", "kind": kind, "x0": cx - w / 2, "y0": cy - h / 2, "x1": cx + w / 2,
                         "y1": cy + h / 2, "fill": fill, "grad": make_grad(rng, fill, gstyle) if gstyle and fill
                         else None, "stroke": stroke, "sw": rng.choice((1.5, 2.0, 3.0)),
                         "size": rng.choice((18, 20, 24, 28)),
                         "text": phrase(rng, 1, 2)[:18] if rng.random() < 0.85 else ""})
        nodes.append((cx, cy, w / 2, h / 2))
    ink = rng.choice(DARK)
    for _ in range(rng.randint(len(nodes) - 1, len(nodes) + 2)):
        a, b = rng.sample(nodes, 2)
        p = _clip(a[0], a[1], a[2] + 6, a[3] + 6, b[0], b[1])
        q = _clip(b[0], b[1], b[2] + 6, b[3] + 6, a[0], a[1])
        fig.add("front", {"t": "line", "x0": p[0], "y0": p[1], "x1": q[0], "y1": q[1], "color": ink,
                          "sw": rng.choice((1.5, 2.0, 3.0, 4.0)), "dash": rng.choice(("solid", "solid", "dash")),
                          "a1": rng.random() < 0.15, "a2": rng.random() < 0.8})
    if top > 100:
        fig.text(phrase(rng, 2, 4), W / 2, 50, rng.choice((24, 28, 32, 40)), ink, bold=rng.random() < 0.5,
                 ha="center", va="middle")
    return fig


# ---------------------------------------------------------------------- colour maps (no matplotlib needed)
CMAPS = {
    "viridis": ("#440154", "#482878", "#3e4989", "#31688e", "#26828e", "#1f9e89", "#35b779", "#6ece58", "#b5de2b",
                "#fde725"),
    "plasma": ("#0d0887", "#46039f", "#7201a8", "#9c179e", "#bd3786", "#d8576b", "#ed7953", "#fb9f3a", "#fdca26",
               "#f0f921"),
    "inferno": ("#000004", "#1b0c41", "#4a0c6b", "#781c6d", "#a52c60", "#cf4446", "#ed6925", "#fb9b06", "#f7d13d",
                "#fcffa4"),
    "magma": ("#000004", "#180f3d", "#440f76", "#721f81", "#9e2f7f", "#cd4071", "#f1605d", "#fd9668", "#feca8d",
              "#fcfdbf"),
    "cividis": ("#00224e", "#35456c", "#666970", "#948e77", "#c8b866", "#fee838"),
    "jet": ("#00007f", "#0000ff", "#007fff", "#00ffff", "#7fff7f", "#ffff00", "#ff7f00", "#ff0000", "#7f0000"),
    "turbo": ("#30123b", "#4662d7", "#36aaf9", "#1ae4b6", "#72fe5e", "#c8ef34", "#faba39", "#f66b19", "#ca2a04",
              "#7a0403"),
    "gray": ("#000000", "#ffffff"),
    "coolwarm": ("#3b4cc0", "#7396f5", "#b0cbfc", "#dddddd", "#f6b69b", "#e8775f", "#b40426"),
    "Blues": ("#f7fbff", "#c6dbef", "#6baed6", "#2171b5", "#08306b"),
}
HEAT_MAPS = ("viridis", "plasma", "plasma", "inferno", "magma", "cividis", "jet", "turbo", "gray", "coolwarm")
BAND_MAPS = ("viridis", "viridis", "plasma", "cividis", "turbo", "coolwarm", "Blues", "magma")


def lut(name, n=256):
    """n x 3 colours along a colour map."""
    import numpy as np
    pts = np.array([T.hex_rgb(c) for c in CMAPS[name]], dtype=float)
    xs = np.linspace(0, 1, len(pts))
    t = np.linspace(0, 1, n)
    return np.stack([np.interp(t, xs, pts[:, k]) for k in range(3)], 1).round().astype(np.uint8)


def map_colors(rng, name, k):
    """k colours spread along a colour map, snapped to ones the model can say."""
    table = lut(name)
    lo, hi = rng.uniform(0.0, 0.15), rng.uniform(0.85, 1.0)
    pos = [lo + (hi - lo) * i / max(1, k - 1) for i in range(k)]
    if rng.random() < 0.3:
        pos.reverse()
    return [T.snap_color("#{:02x}{:02x}{:02x}".format(*table[int(p * 255)])) for p in pos]


ELEMENTS = ("Li", "Na", "K", "Rb", "Mg", "Ca", "Sr", "Ba", "Ti", "Zr", "Nb", "Ta", "W", "Mo", "V", "Cr", "Mn", "Fe",
            "Co", "Ni", "Cu", "Zn", "Al", "Ga", "In", "Si", "Ge", "Sn", "Pb", "Bi", "La", "Ce", "Y", "Sc", "B", "C",
            "N", "P", "S", "Se", "Cl", "F")
SUB = str.maketrans("0123456789+-", "₀₁₂₃₄₅₆₇₈₉₊₋")


def formula(rng):
    """A made-up chemical formula with subscripts, like Li₂W₂O₇ or Nb₆.₇W₁₀.₃O₄₇."""
    parts = []
    for el in rng.sample(ELEMENTS, rng.randint(1, 3)):
        n = rng.choice(("", "", "2", "3", "4", "5", "6", "8", "10", str(round(rng.uniform(0.2, 12), 1))))
        parts.append(el + n.translate(SUB))
    if rng.random() < 0.8:
        parts.append("O" + rng.choice(("", "2", "3", "4", "5", "6", "7", "8", "12", "47")).translate(SUB))
    s = "".join(parts)
    if rng.random() < 0.12:
        s = rng.choice(("α-", "β-", "γ-", "δ-")) + s
    return s[: T.MAX_CHARS]


def label_text(rng):
    return formula(rng) if rng.random() < 0.65 else phrase(rng, 1, 2)[:14]


# ---------------------------------------------------------------------- stacked areas (phase fractions etc.)
def _fractions(rng, n, k):
    import numpy as np
    xs = np.linspace(0, 1, n)
    rows = []
    for _ in range(k):
        if rng.random() < 0.55:  # something that appears and/or disappears along the axis
            a, b = sorted((rng.uniform(-0.4, 0.9), rng.uniform(0.1, 1.4)))
            y = 1 / (1 + np.exp(-(xs - a) / rng.uniform(0.01, 0.1))) / (1 + np.exp((xs - b) / rng.uniform(0.01, 0.1)))
        else:
            y = 0.5 + 0.5 * np.sin(xs * rng.uniform(1, 6) + rng.uniform(0, 6.3))
        y = y * rng.uniform(0.2, 1.0) + sum(rng.uniform(0, 0.3) * np.exp(-((xs - rng.random()) / rng.uniform(0.03, 0.15))
                                                                        ** 2) for _ in range(rng.randint(0, 2)))
        rows.append(np.clip(y, 0, None) + 0.004)
    Y = np.array(rows)
    if rng.random() < 0.6:
        Y = Y / Y.sum(0)                                          # fractions adding up to 100 %
    else:
        Y = Y / Y.sum(0).max() * rng.uniform(0.6, 0.95)
    return xs, Y


def stacked(rng, font, frame=None):
    import numpy as np
    fig = Fig(rng, font, frame)
    plot = _plot_box(rng, fig)
    x0, y0, x1, y1 = plot
    ink, size = _axes(fig, rng, plot, grid=False)
    k = rng.randint(2, 7)
    n = rng.randint(8, 22)
    xs, Y = _fractions(rng, n, k)
    cum = np.cumsum(Y, 0)
    cols = map_colors(rng, rng.choice(BAND_MAPS), k)
    px = [x0 + (x1 - x0) * x for x in xs]

    def py(v):
        return y1 - (y1 - y0) * float(v)

    edge = rng.choice((None, None, None, "#ffffff", ink))
    for j in range(k):
        top = [(px[i], py(cum[j][i])) for i in range(n)]
        if j == 0:
            bottom = [(px[-1], y1), (px[0], y1)]
        else:
            bottom = [(px[i], py(cum[j - 1][i])) for i in reversed(range(n))]
        fig.add("mid", {"t": "path", "stroke": edge, "fill": cols[j], "sw": 1.0, "dash": "solid", "closed": True,
                        "pts": top + bottom})
    style = rng.random()
    lab = rng.choice((16, 18, 20, 24))
    if style < 0.6:  # names written inside the bands, or pointed at with an arrow when a band is thin
        for j in range(k):
            if rng.random() < 0.15:
                continue
            thick = [cum[j][i] - (cum[j - 1][i] if j else 0) for i in range(n)]
            i = max(range(n), key=lambda q: thick[q])
            mid = py((cum[j][i] + (cum[j - 1][i] if j else 0)) / 2)
            name = label_text(rng)
            color = "#ffffff" if _lum(cols[j]) < 0.55 else "#000000"
            if thick[i] * (y1 - y0) * fig.k > lab * fig.k * 1.6:
                fig.text(name, px[i], mid, lab, color, ha="center", va="middle")
            elif thick[i] > 0.005 and rng.random() < 0.7:
                fig.add("marks", {"t": "line", "x0": px[i], "y0": mid - 36, "x1": px[i], "y1": mid - 4,
                                  "color": color if color == "#ffffff" else ink, "sw": 1.5, "dash": "solid",
                                  "a1": False, "a2": True})
                fig.text(name, px[i], mid - 40, lab, "#ffffff" if color == "#ffffff" else ink, ha="center",
                         va="bottom")
    elif style < 0.85:  # a legend: colour squares and names
        lx, ly = (x1 + 16, y0) if rng.random() < 0.5 else (x0 + 16, y0 + 12)
        if lx > x1 and fig.W - x1 < 150:
            lx, ly = x0 + 16, y0 + 12
        for j in range(k):
            yy = ly + j * (lab + 12)
            fig.add("front", {"t": "shape", "kind": "rect", "x0": lx, "y0": yy, "x1": lx + lab, "y1": yy + lab,
                              "fill": cols[j], "stroke": None, "sw": 1.0, "size": 20, "text": ""})
            fig.text(label_text(rng), lx + lab + 8, yy + lab / 2, lab, ink, va="middle")
    return fig


# ---------------------------------------------------------------------- heatmaps (imshow of an in situ scan etc.)
def _field(rng, h, w):
    """Made-up 2-D data: an in situ diffraction scan (peaks drifting, appearing and vanishing over time), smooth
    blobs, or a noisy texture."""
    import numpy as np
    nr = np.random.default_rng(rng.randrange(1 << 30))
    kind = rng.choice(("insitu", "insitu", "insitu", "blobs", "texture"))
    yy, xx = np.mgrid[0:h, 0:w].astype(float)
    if kind == "insitu":
        t = 1 - yy / max(1, h - 1)  # time runs upwards, like origin="lower"
        Z = 1 + nr.normal(0, 0.05, (h, w))
        for _ in range(rng.randint(3, 14)):
            drift = rng.uniform(-0.03, 0.03) if rng.random() < 0.75 else rng.uniform(-0.12, 0.12)  # mostly upright
            pos = rng.uniform(0, w) + drift * w * t
            if rng.random() < 0.3:  # a phase change: the peak jumps
                tj = rng.random()
                pos = pos + (t > tj) * rng.uniform(-0.05, 0.05) * w
            on, off = sorted((rng.uniform(-0.3, 0.8), rng.uniform(0.3, 1.4)))
            amp = rng.uniform(0.3, 5) / (1 + np.exp(-(t - on) / 0.02)) / (1 + np.exp((t - off) / 0.02))
            width = rng.uniform(0.6, 3.0) * w / 300
            Z += amp * np.exp(-((xx - pos) / width) ** 2) * (1 + nr.normal(0, 0.15, (h, 1)))
    elif kind == "blobs":
        Z = nr.normal(0, 0.03, (h, w))
        for _ in range(rng.randint(2, 9)):
            cx, cy = rng.uniform(0, w), rng.uniform(0, h)
            sx, sy = rng.uniform(0.05, 0.4) * w, rng.uniform(0.05, 0.4) * h
            Z += rng.uniform(-1, 1) * np.exp(-((xx - cx) / sx) ** 2 - ((yy - cy) / sy) ** 2)
    else:
        Z = nr.normal(0, 1, (h, w))
        for _ in range(rng.randint(1, 4)):  # cheap smoothing
            Z = (Z + np.roll(Z, 1, 0) + np.roll(Z, -1, 0) + np.roll(Z, 1, 1) + np.roll(Z, -1, 1)) / 5
    lo, hi = np.percentile(Z, rng.uniform(0, 5)), np.percentile(Z, rng.uniform(97, 100))
    Z = np.clip((Z - lo) / max(1e-9, hi - lo), 0, 1)
    if rng.random() < 0.3:
        Z = Z ** rng.uniform(0.4, 0.8)
    return Z, (lo, hi)


def _picture(Z, name):
    from PIL import Image
    table = lut(name)
    return Image.fromarray(table[(Z * 255).astype("uint8")], "RGB")


def heatmap(rng, font, frame=None):
    import numpy as np
    fig = Fig(rng, font, frame)
    x0, y0, x1, y1 = _plot_box(rng, fig)
    bar = rng.random() < 0.75
    if bar:  # room for a colour bar on the right
        bw = rng.uniform(18, 34)
        x1 = min(x1, fig.W - bw - 110)
    plot = (x0, y0, x1, y1)
    cmap = rng.choice(HEAT_MAPS)
    Z, (lo, hi) = _field(rng, rng.randint(40, 300), rng.randint(60, 480))
    fig.add("mid", {"t": "image", "x0": x0, "y0": y0, "x1": x1, "y1": y1, "pixels": _picture(Z, cmap),
                    "smooth": rng.random() < 0.6})
    ink, size = _axes(fig, rng, plot, grid=False)
    if bar:
        gap = rng.uniform(16, 40)
        bx0, bx1 = x1 + gap, x1 + gap + bw
        by0, by1 = (y0, y1) if rng.random() < 0.7 else (y0 + 0.1 * (y1 - y0), y1 - 0.1 * (y1 - y0))
        strip = np.linspace(1, 0, 256)[:, None].repeat(4, 1)
        fig.add("mid", {"t": "image", "x0": bx0, "y0": by0, "x1": bx1, "y1": by1, "pixels": _picture(strip, cmap),
                        "smooth": True})
        if rng.random() < 0.7:
            fig.add("front", {"t": "shape", "kind": "rect", "x0": bx0, "y0": by0, "x1": bx1, "y1": by1, "fill": None,
                              "stroke": ink, "sw": 1.0, "size": 20, "text": ""})
        nt = rng.randint(3, 7)
        step = rng.choice((0.1, 0.2, 0.5, 1, 2, 5, 10, 100, 1000))
        start = rng.choice((0, step * rng.randint(0, 5)))
        for i in range(nt + 1):
            y = by1 - (by1 - by0) * i / nt
            fig.add("front", {"t": "line", "x0": bx1, "y0": y, "x1": bx1 + 6, "y1": y, "color": ink, "sw": 1.0,
                              "dash": "solid", "a1": False, "a2": False})
            fig.text(number(rng, start + step * i, step), bx1 + 10, y, size, ink, va="middle")
        if rng.random() < 0.5:
            fig.text(phrase(rng, 1, 2)[:20], bx1 + 70, (by0 + by1) / 2, rng.choice((16, 18, 20)), ink,
                     rot=rng.choice((90, 270)), ha="center", va="middle")
    # things drawn on top of the picture: labels, arrows, marked regions, contour lines
    for _ in range(rng.choice((0, 0, 1, 1, 2, 3))):
        r = rng.random()
        col = rng.choice(("#ffffff", "#ffffff", "#000000", "#ffff00", "#ff0000", "#00ffff"))
        cx, cy = rng.uniform(x0 + 30, x1 - 30), rng.uniform(y0 + 30, y1 - 30)
        if r < 0.4:
            fig.text(label_text(rng), cx, cy, rng.choice((16, 18, 20, 24)), col, bold=rng.random() < 0.4,
                     ha="center", va="middle")
        elif r < 0.6:
            fig.add("marks", {"t": "line", "x0": cx, "y0": cy, "x1": cx + rng.uniform(-90, 90),
                              "y1": cy + rng.uniform(-90, 90), "color": col, "sw": rng.choice((1.5, 2.0, 3.0)),
                              "dash": "solid", "a1": False, "a2": True})
        elif r < 0.8:
            w, h = rng.uniform(30, 160), rng.uniform(30, 120)
            fig.add("marks", {"t": "shape", "kind": rng.choice(("rect", "ellipse", "round")), "x0": cx - w / 2,
                              "y0": cy - h / 2, "x1": cx + w / 2, "y1": cy + h / 2, "fill": None, "stroke": col,
                              "sw": rng.choice((1.5, 2.0, 3.0)), "dash": "solid", "size": 20, "text": ""})
        elif r < 0.9:
            fig.add("marks", {"t": "line", "x0": cx, "y0": y0, "x1": cx, "y1": y1, "color": col, "sw": 1.5,
                              "dash": rng.choice(("dash", "dot")), "a1": False, "a2": False})
        else:
            rad = rng.uniform(20, 90)
            m = rng.randint(10, 24)
            pts = [(cx + rad * rng.uniform(0.7, 1.0) * math.cos(6.283 * i / m),
                    cy + rad * 0.7 * rng.uniform(0.7, 1.0) * math.sin(6.283 * i / m)) for i in range(m)]
            fig.add("marks", {"t": "path", "stroke": col, "fill": None, "sw": 1.5, "dash": "solid", "closed": True,
                              "pts": pts})
    return fig


def doodle(rng, font, frame=None):
    fig = Fig(rng, font, frame)
    W, H = fig.W, fig.H
    for _ in range(rng.randint(2, 12)):
        r = rng.random()
        if r < 0.35:
            x, y = rng.uniform(20, W - 180), rng.uniform(20, H - 120)
            w, h = rng.uniform(30, 360), rng.uniform(30, 260)
            fill = rng.choice(PALETTE) if rng.random() < 0.75 else None
            fig.add("back", {"t": "shape", "kind": rng.choice(T.SHAPES), "x0": x, "y0": y, "x1": min(W - 10, x + w),
                             "y1": min(H - 10, y + h), "fill": fill,
                             "grad": make_grad(rng, fill) if fill and rng.random() < 0.3 else None,
                             "stroke": rng.choice(PALETTE) if (fill is None or rng.random() < 0.3) else None,
                             "sw": rng.choice(T.WIDTHS), "size": rng.choice(T.SIZES[:8]),
                             "text": phrase(rng, 1, 2)[:14] if rng.random() < 0.3 else ""})
        elif r < 0.6:
            fig.add("front", {"t": "line", "x0": rng.uniform(10, W - 10), "y0": rng.uniform(10, H - 10),
                              "x1": rng.uniform(10, W - 10), "y1": rng.uniform(10, H - 10), "color": rng.choice(DARK + VIVID),
                              "sw": rng.choice(T.WIDTHS), "dash": rng.choice(T.DASHES), "a1": rng.random() < 0.2,
                              "a2": rng.random() < 0.4})
        elif r < 0.8:
            cx, cy, rad = rng.uniform(100, W - 100), rng.uniform(100, H - 100), rng.uniform(30, 160)
            n = rng.randint(3, 12)
            pts = [(cx + rad * rng.uniform(0.5, 1) * math.cos(6.283 * i / n),
                    cy + rad * rng.uniform(0.5, 1) * math.sin(6.283 * i / n)) for i in range(n)]
            filled = rng.random() < 0.6
            fill = rng.choice(PALETTE) if filled else None
            fig.add("mid", {"t": "path", "stroke": rng.choice(DARK + VIVID) if not filled or rng.random() < 0.4
                            else None, "fill": fill, "sw": rng.choice(T.WIDTHS), "dash": "solid", "closed": True,
                            "pts": pts, "grad": make_grad(rng, fill) if fill and rng.random() < 0.3 else None})
        else:
            fig.text(phrase(rng, 1, 4), rng.uniform(20, max(40, W - 280)), rng.uniform(20, H - 60), rng.choice(T.SIZES[2:10]),
                     rng.choice(DARK + VIVID), bold=rng.random() < 0.3,
                     rot=rng.choice((0, 0, 0, 0, 90, 270)))
    return fig


KINDS = ((chart, 0.30), (bars, 0.11), (stacked, 0.15), (heatmap, 0.15), (diagram, 0.18), (doodle, 0.11))


def make(seed):
    """One random figure: (specs measured by DeNotive, the DeNotive scene, kind name, frame)."""
    rng = random.Random(seed)
    font = rng.choice(FONTS) if rng.random() < 0.6 else "Arial"
    r, acc = rng.random(), 0.0
    for fn, p in KINDS:
        acc += p
        if r <= acc:
            break
    frame = pick_frame(rng)
    specs = fn(rng, font, frame).specs()
    # keep the sentence short enough for the model
    while len(T.encode(specs)) > T.MAX_TOKENS and specs:
        specs.pop(rng.randrange(len(specs)))
    specs, scene = measured(specs, font)
    return specs, scene, fn.__name__, frame


def _work(args):
    out, i, seed, width = args
    specs, scene, kind, frame = make(seed)
    img = qimage_to_pil(scene.render_image(width))
    name = f"img/{i:07d}.png"
    img.save(out / name, optimize=False)
    return {"i": i, "file": name, "kind": kind, "aspect": round(frame["aspect"], 3), "scale": round(frame["k"], 3),
            "n": len(specs), "tokens": T.encode(specs)}


def _init():
    ensure_app()


def run(out, count, workers, seed=1, width=960):
    out = Path(out)
    (out / "img").mkdir(parents=True, exist_ok=True)
    index = out / "index.jsonl"
    done = set()
    if index.exists():
        for line in index.open(encoding="utf-8"):
            try:
                done.add(json.loads(line)["i"])
            except (ValueError, KeyError):
                pass
    todo = [(out, i, seed * 1_000_003 + i, width) for i in range(count) if i not in done]
    print(f"Making {len(todo)} figures in {out} ({len(done)} already there) with {workers} worker(s)...", flush=True)
    t0 = time.time()
    with index.open("a", encoding="utf-8") as f:
        if workers <= 1:
            _init()
            results = map(_work, todo)
        else:
            import multiprocessing as mp
            pool = mp.Pool(workers, initializer=_init)
            results = pool.imap_unordered(_work, todo, chunksize=8)
        for k, rec in enumerate(results, 1):
            f.write(json.dumps(rec) + "\n")
            if k % 200 == 0 or k == len(todo):
                f.flush()
                rate = k / max(1e-6, time.time() - t0)
                print(f"  {k}/{len(todo)}  ({rate:.1f} per second, about {(len(todo) - k) / max(rate, 1e-6) / 60:.0f}"
                      f" min left)", flush=True)
    meta = {"vocab": T.V.size, "nb": T.NB, "width": width, "count": count}
    (out / "meta.json").write_text(json.dumps(meta, indent=1), encoding="utf-8")
    print("Done.", flush=True)


def main(argv=None):
    import argparse
    ap = argparse.ArgumentParser(prog="main.py generate", description=__doc__.splitlines()[0])
    ap.add_argument("--out", default=str(Path(__file__).resolve().parents[1] / "data"))
    ap.add_argument("--count", type=int, default=60000)
    ap.add_argument("--workers", type=int, default=max(1, (os.cpu_count() or 4) - 2))
    ap.add_argument("--seed", type=int, default=1)
    a = ap.parse_args(argv)
    run(a.out, a.count, a.workers, a.seed)


if __name__ == "__main__":
    main(sys.argv[1:])
