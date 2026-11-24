"""A DeNotive slide written as a sentence of tokens (what the model reads and writes), and back again.

A figure is a list of "specs", one per piece, in painting order (things behind come first):
    {"t": "shape", "kind", "x0", "y0", "x1", "y1", "fill", "grad", "stroke", "sw", "size", "text"}
    {"t": "line",  "x0", "y0", "x1", "y1", "color", "sw", "dash", "a1", "a2"}
    {"t": "text",  "x0", "y0", "x1", "y1", "color", "size", "bold", "rot", "text"}
    {"t": "path",  "stroke", "fill", "grad", "sw", "dash", "closed", "pts": [(x, y), ...]}
    {"t": "image", "x0", "y0", "x1", "y1"}   a picture region (a heatmap, a photo, a colour bar): the model only
                                            says where it is; predict cuts that part out of the input picture
Positions are on a 1280 x 720 slide. Boxes are what you see (a rotated label's box is tall and thin).

"grad" is None for a solid fill, or a gradient from the fill colour to a second colour:
    {"c2": end colour, "ang": direction in degrees (0 = left to right, 90 = top to bottom),
     "a", "b": where the start and end lines sit, measured from the box's centre along that direction in units
               of half the box's length that way (-1 and 1 = the box's edges)}
That's DeNotive's own gradient (the circle-and-two-lines tool) with the circle centred: moving the centre along
the direction is the same as moving both lines, so the centre needs no tokens of its own.

Each spec becomes a fixed pattern of tokens, so the model only ever has to pick from a short list of
sensible next tokens (see Grammar), and any sentence it writes can be turned back into DeNotive pieces.
"""
import math

CANVAS_W, CANVAS_H = 1280.0, 720.0
NB = 320                                   # position steps across (and down) the slide: 4 px across, 2.25 down
LEVELS = (0, 51, 102, 153, 204, 255)       # 6 shades per channel = 216 colours
SHAPES = ("rect", "round", "ellipse", "triangle", "diamond", "hexagon", "parallelogram", "star", "arrow",
          "chevron", "callout", "cross")
DASHES = ("solid", "dash", "dot")
SIZES = (12, 14, 16, 18, 20, 24, 28, 32, 40, 48, 56, 64)          # text size in px on the slide
WIDTHS = (0.75, 1.0, 1.5, 2.0, 3.0, 4.0, 5.0, 6.0, 8.0, 10.0)     # line widths in px
ROTS = (0, 90, 270)
ANGS = tuple(range(0, 360, 15))                                   # gradient directions
OFFS = tuple(round(-2.0 + 0.2 * i, 1) for i in range(21))          # gradient line positions, -2 .. 2
CHARS = tuple(chr(c) for c in range(32, 127)) + tuple("°±µθλσΔΩαβγ−·×→←") + \
    tuple("⁰¹²³⁴⁵⁶⁷⁸⁹⁺⁻₀₁₂₃₄₅₆₇₈₉₊₋")  # superscript and subscript digits: cm⁻¹, Li₂W₂O₇
MAX_CHARS = 40
MAX_POINTS = 48
MAX_TOKENS = 960

PAD, BOS, EOS, SHAPE, LINE, TEXT, PATH, ENDT, ENDP, NO, YES, GRAD, IMAGE = range(13)
SPECIAL = ("<pad>", "<bos>", "<eos>", "<shape>", "<line>", "<text>", "<path>", "<endt>", "<endp>", "<no>", "<yes>",
           "<grad>", "<image>")

# what follows each kind of piece: (name, kind of token)
SCHEMA = {
    SHAPE: (("kind", "kind"), ("x0", "x"), ("y0", "y"), ("x1", "x"), ("y1", "y"), ("fill", "color"),
            ("grad", "gradopt"), ("stroke", "color"), ("sw", "width"), ("size", "size"), ("text", "chars")),
    LINE: (("x0", "x"), ("y0", "y"), ("x1", "x"), ("y1", "y"), ("color", "color"), ("sw", "width"),
           ("dash", "dash"), ("a1", "bool"), ("a2", "bool")),
    TEXT: (("x0", "x"), ("y0", "y"), ("x1", "x"), ("y1", "y"), ("color", "color"), ("size", "size"),
           ("bold", "bool"), ("rot", "rot"), ("text", "chars")),
    PATH: (("stroke", "color"), ("fill", "color"), ("grad", "gradopt"), ("sw", "width"), ("dash", "dash"),
           ("closed", "bool"), ("pts", "points")),
    IMAGE: (("x0", "x"), ("y0", "y"), ("x1", "x"), ("y1", "y")),
}
GRAD_FIELDS = (("c2", "color"), ("ang", "ang"), ("ga", "off"), ("gb", "off"))  # after a <grad> token


class Vocab:
    def __init__(self):
        self.names = list(SPECIAL)
        self.groups = {}
        for name, n in (("kind", len(SHAPES)), ("coord", NB), ("color", 1 + len(LEVELS) ** 3),
                        ("size", len(SIZES)), ("width", len(WIDTHS)), ("dash", len(DASHES)), ("rot", len(ROTS)),
                        ("ang", len(ANGS)), ("off", len(OFFS)), ("char", len(CHARS))):
            self.groups[name] = (len(self.names), n)
            self.names += [f"{name}{i}" for i in range(n)]
        self.size = len(self.names)

    def tok(self, group, i):
        start, n = self.groups[group]
        return start + max(0, min(n - 1, int(i)))

    def val(self, group, t):
        start, n = self.groups[group]
        return t - start if start <= t < start + n else None

    def span(self, group):
        start, n = self.groups[group]
        return list(range(start, start + n))


V = Vocab()


# ---------------------------------------------------------------------- single values
def _nearest(options, v):
    return min(range(len(options)), key=lambda i: abs(options[i] - v))


def qx(x):
    return V.tok("coord", round(float(x) / CANVAS_W * (NB - 1)))


def qy(y):
    return V.tok("coord", round(float(y) / CANVAS_H * (NB - 1)))


def dx(t):
    return (V.val("coord", t) or 0) / (NB - 1) * CANVAS_W


def dy(t):
    return (V.val("coord", t) or 0) / (NB - 1) * CANVAS_H


def hex_rgb(c):
    c = c.lstrip("#")
    if len(c) == 8:  # #AARRGGBB
        c = c[2:]
    return int(c[0:2], 16), int(c[2:4], 16), int(c[4:6], 16)


def qcolor(c):
    if not c:
        return V.tok("color", 0)
    r, g, b = (_nearest(LEVELS, v) for v in hex_rgb(c))
    return V.tok("color", 1 + r * 36 + g * 6 + b)


def dcolor(t):
    i = V.val("color", t) or 0
    if i == 0:
        return None
    i -= 1
    return "#{:02x}{:02x}{:02x}".format(LEVELS[i // 36], LEVELS[(i // 6) % 6], LEVELS[i % 6])


def snap_color(c):
    """The nearest colour the model can say."""
    return dcolor(qcolor(c))


def qsize(px):
    return V.tok("size", _nearest(SIZES, float(px)))


def qwidth(w):
    return V.tok("width", _nearest(WIDTHS, float(w or WIDTHS[0])))


def qdash(d):
    return V.tok("dash", DASHES.index(d) if d in DASHES else 0)


def qrot(r):
    r = round(float(r or 0)) % 360
    return V.tok("rot", 1 if 45 <= r < 135 else 2 if 225 <= r < 315 else 0)


def qang(a):
    return V.tok("ang", round(float(a) % 360 / 15) % len(ANGS))


def qoff(o):
    return V.tok("off", _nearest(OFFS, float(o)))


def qbool(b):
    return YES if b else NO


def qchars(text):
    return [V.tok("char", CHARS.index(c) if c in CHARS else CHARS.index("?")) for c in (text or "")[:MAX_CHARS]]


def dchars(toks):
    return "".join(CHARS[V.val("char", t)] for t in toks if V.val("char", t) is not None)


def grad_from_denotive(g, w, h):
    """A DeNotive gradient (centre anywhere) as a spec gradient (centred, start line before end line)."""
    t = math.radians(float(g["ang"]))
    ddx, ddy = math.cos(t), math.sin(t)
    L = max(1.0, (abs(w * ddx) + abs(h * ddy)) / 2)
    shift = ((float(g["c"][0]) - 0.5) * w * ddx + (float(g["c"][1]) - 0.5) * h * ddy) / L
    a, b, ang = float(g["a"]) + shift, float(g["b"]) + shift, float(g["ang"])
    if a > b:  # the same gradient seen from the other side
        a, b, ang = -a, -b, ang + 180
    return {"c2": g["c2"], "ang": ang % 360, "a": a, "b": b}


def _grad_toks(g):
    if not g:
        return [NO]
    return [GRAD, qcolor(g["c2"]), qang(g["ang"]), qoff(g["a"]), qoff(g["b"])]


# ---------------------------------------------------------------------- whole figures
def encode(specs):
    out = [BOS]
    for s in specs:
        t = s["t"]
        if t == "shape":
            out += [SHAPE, V.tok("kind", SHAPES.index(s["kind"])), qx(s["x0"]), qy(s["y0"]), qx(s["x1"]),
                    qy(s["y1"]), qcolor(s.get("fill"))]
            out += _grad_toks(s.get("grad") if s.get("fill") else None)
            out += [qcolor(s.get("stroke")), qwidth(s.get("sw")), qsize(s.get("size", 20))]
            out += qchars(s.get("text", "")) + [ENDT]
        elif t == "line":
            out += [LINE, qx(s["x0"]), qy(s["y0"]), qx(s["x1"]), qy(s["y1"]), qcolor(s.get("color")),
                    qwidth(s.get("sw")), qdash(s.get("dash")), qbool(s.get("a1")), qbool(s.get("a2"))]
        elif t == "text":
            out += [TEXT, qx(s["x0"]), qy(s["y0"]), qx(s["x1"]), qy(s["y1"]), qcolor(s.get("color")),
                    qsize(s.get("size", 20)), qbool(s.get("bold")), qrot(s.get("rot"))] + qchars(s["text"]) + [ENDT]
        elif t == "path":
            out += [PATH, qcolor(s.get("stroke")), qcolor(s.get("fill"))]
            out += _grad_toks(s.get("grad") if s.get("fill") else None)
            out += [qwidth(s.get("sw")), qdash(s.get("dash")), qbool(s.get("closed"))]
            for x, y in s["pts"][:MAX_POINTS]:
                out += [qx(x), qy(y)]
            out.append(ENDP)
        elif t == "image":
            out += [IMAGE, qx(s["x0"]), qy(s["y0"]), qx(s["x1"]), qy(s["y1"])]
    out.append(EOS)
    return out


def decode(tokens):
    """Specs from a token sentence. Broken pieces are skipped rather than failing the whole figure."""
    specs, i, n = [], 0, len(tokens)
    while i < n:
        t = tokens[i]
        i += 1
        if t == EOS:
            break
        if t not in SCHEMA:
            continue
        fields, ok = {}, True
        todo = list(SCHEMA[t])
        k = 0
        while k < len(todo):
            name, kind = todo[k]
            k += 1
            if kind in ("chars", "points"):
                end = ENDT if kind == "chars" else ENDP
                run = []
                while i < n and tokens[i] not in (end, EOS) and tokens[i] not in SCHEMA:
                    run.append(tokens[i])
                    i += 1
                if i < n and tokens[i] == end:
                    i += 1
                fields[name] = run
                continue
            if i >= n:
                ok = False
                break
            tok = tokens[i]
            i += 1
            if kind == "gradopt":
                fields[name] = tok == GRAD
                if tok == GRAD:
                    todo[k:k] = GRAD_FIELDS
                continue
            fields[name] = tok
        if not ok:
            break
        try:
            s = _spec(t, fields)
        except (TypeError, ValueError, IndexError, KeyError):
            s = None
        if s is not None:
            specs.append(s)
    return specs


def _box(f):
    x0, y0, x1, y1 = dx(f["x0"]), dy(f["y0"]), dx(f["x1"]), dy(f["y1"])
    return min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1)


def _grad(f, fill):
    if not f.get("grad") or not fill:
        return None
    c2 = dcolor(f["c2"]) or "#ffffff"
    a, b = OFFS[V.val("off", f["ga"])], OFFS[V.val("off", f["gb"])]
    if abs(b - a) < 0.05:
        b = a + 0.2
    return {"c2": c2, "ang": float(ANGS[V.val("ang", f["ang"])]), "a": a, "b": b}


def _spec(t, f):
    if t == SHAPE:
        x0, y0, x1, y1 = _box(f)
        if x1 - x0 < 2 or y1 - y0 < 2:
            return None
        fill = dcolor(f["fill"])
        return {"t": "shape", "kind": SHAPES[V.val("kind", f["kind"])], "x0": x0, "y0": y0, "x1": x1, "y1": y1,
                "fill": fill, "grad": _grad(f, fill), "stroke": dcolor(f["stroke"]),
                "sw": WIDTHS[V.val("width", f["sw"])], "size": SIZES[V.val("size", f["size"])],
                "text": dchars(f["text"])}
    if t == LINE:
        return {"t": "line", "x0": dx(f["x0"]), "y0": dy(f["y0"]), "x1": dx(f["x1"]), "y1": dy(f["y1"]),
                "color": dcolor(f["color"]) or "#000000", "sw": WIDTHS[V.val("width", f["sw"])],
                "dash": DASHES[V.val("dash", f["dash"])], "a1": f["a1"] == YES, "a2": f["a2"] == YES}
    if t == TEXT:
        text = dchars(f["text"])
        if not text.strip():
            return None
        x0, y0, x1, y1 = _box(f)
        return {"t": "text", "x0": x0, "y0": y0, "x1": x1, "y1": y1, "color": dcolor(f["color"]) or "#000000",
                "size": SIZES[V.val("size", f["size"])], "bold": f["bold"] == YES, "rot": ROTS[V.val("rot", f["rot"])],
                "text": text}
    if t == PATH:
        c = [v for v in f["pts"] if V.val("coord", v) is not None]
        pts = [(dx(c[k]), dy(c[k + 1])) for k in range(0, len(c) - 1, 2)]
        if len(pts) < 2:
            return None
        stroke, fill = dcolor(f["stroke"]), dcolor(f["fill"])
        if not stroke and not fill:
            stroke = "#000000"
        return {"t": "path", "stroke": stroke, "fill": fill, "grad": _grad(f, fill),
                "sw": WIDTHS[V.val("width", f["sw"])], "dash": DASHES[V.val("dash", f["dash"])],
                "closed": f["closed"] == YES, "pts": pts}
    if t == IMAGE:
        x0, y0, x1, y1 = _box(f)
        if x1 - x0 < 4 or y1 - y0 < 4:
            return None
        return {"t": "image", "x0": x0, "y0": y0, "x1": x1, "y1": y1}
    return None


# ---------------------------------------------------------------------- what may come next (for writing)
class Grammar:
    """Tracks where the model is in its sentence and names the set of tokens that may come next."""

    def __init__(self):
        self.fields, self.k, self.count, self.done = None, 0, 0, False

    def key(self):
        if self.done:
            return "end"
        if self.fields is None:
            return "top"
        kind = self.fields[self.k][1]
        if kind == "chars":
            return "chars" if self.count < MAX_CHARS else "endt"
        if kind == "points":
            if self.count >= 2 * MAX_POINTS:
                return "endp"
            if self.count % 2 == 1:
                return "coord"
            return "points_or_end" if self.count >= 4 else "coord"
        return "coord" if kind in ("x", "y") else kind

    def feed(self, tok):
        if self.done:
            return
        if self.fields is None:
            if tok == EOS:
                self.done = True
            elif tok in SCHEMA:
                self.fields, self.k, self.count = list(SCHEMA[tok]), 0, 0
            return
        kind = self.fields[self.k][1]
        if kind in ("chars", "points") and tok not in (ENDT, ENDP):
            self.count += 1
            return
        if kind == "gradopt" and tok == GRAD:
            self.fields[self.k + 1:self.k + 1] = GRAD_FIELDS
        self.k += 1
        self.count = 0
        if self.k >= len(self.fields):
            self.fields = None


ALLOWED = {
    "end": [PAD, EOS],
    "top": [SHAPE, LINE, TEXT, PATH, IMAGE, EOS],
    "coord": V.span("coord"),
    "kind": V.span("kind"),
    "color": V.span("color"),
    "width": V.span("width"),
    "size": V.span("size"),
    "dash": V.span("dash"),
    "rot": V.span("rot"),
    "ang": V.span("ang"),
    "off": V.span("off"),
    "gradopt": [NO, GRAD],
    "bool": [NO, YES],
    "chars": V.span("char") + [ENDT],
    "endt": [ENDT],
    "endp": [ENDP],
    "points_or_end": V.span("coord") + [ENDP],
}
