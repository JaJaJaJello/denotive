"""Exact shapes from the picture itself, guided by the model.

The model says what each piece is (a plot line or a filled area, its colour, its dash style, roughly where it
runs) but writes at most 48 points on a coarse grid, and it's only as good as its practice figures. These
steps check its pieces against the picture's own pixels and take the exact shapes from there:

- verify_shapes: plain boxes the picture doesn't show (the colour isn't there, nor the outline) are dropped.
- fix_lines: straight lines (legend samples, axes, arrows) take the colour and dash the picture shows; coloured
  ones with nothing under them are dropped.
- trace_lines: each plot line the model found is followed pixel column by pixel column in its own colour,
  starting from the model's rough path so it stays on the right series. Then plot lines the model missed
  (dense patterns with hundreds of points and sharp peaks, which look nothing like its practice figures) are
  found straight from the picture: long thin runs of one colour. Either way the line goes through the exact
  pixels: a peak keeps its full height, gaps from dashes, labels or crossings are bridged, and the colour,
  thickness and dash style are measured from the picture. Legend samples in a found line's colour come too,
  and the model's own rough copies of a traced line are dropped.
- snap_areas: filled areas (stacked bands, shaded regions) are traced region by region with the picture's
  exact colours (see cleanup.trace_flat). Shaded, textured or gradient areas keep the model's version.
Each step keeps the model's own piece when the picture doesn't back it up.
"""
import cv2
import numpy as np

from . import cleanup
from . import render  # noqa: F401  (puts DeNotive's own code on the path)
from denotive.geometry import rdp

CANVAS_W = 1280.0


def _hex_rgb(c):
    c = (c or "#000000").lstrip("#")
    if len(c) == 8:
        c = c[2:]
    return np.array([int(c[0:2], 16), int(c[2:4], 16), int(c[4:6], 16)], dtype=np.float32)


def _hex(c):
    return "#{:02x}{:02x}{:02x}".format(*(int(max(0, min(255, round(float(v))))) for v in c))


def _dist(a, c):
    return np.sqrt(((a.astype(np.float32) - c) ** 2).sum(-1))


def _colourful(c):
    c = _hex_rgb(c) if isinstance(c, str) else c
    return float(c.max() - c.min()) > 60


def _page_colour(arr):
    """The picture's background: the most common colour round its edges (a chart's biggest colour can fill
    most of the middle)."""
    H, W = arr.shape[:2]
    m = max(2, int(0.02 * min(H, W)))
    ring = np.concatenate([arr[:m].reshape(-1, 3), arr[-m:].reshape(-1, 3),
                           arr[:, :m].reshape(-1, 3), arr[:, -m:].reshape(-1, 3)])
    q = (ring // 8).astype(np.int64)
    b = np.bincount(q[:, 0] * 1024 + q[:, 1] * 32 + q[:, 2]).argmax()
    return np.median(ring[(q[:, 0] * 1024 + q[:, 1] * 32 + q[:, 2]) == b], 0).astype(np.float32)


def _mode_colour(px):
    """The most common colour among some pixels."""
    q = (px.reshape(-1, 3) // 8).astype(np.int64)
    keys = q[:, 0] * 1024 + q[:, 1] * 32 + q[:, 2]
    b = np.bincount(keys).argmax()
    return np.median(px.reshape(-1, 3)[keys == b], 0).astype(np.float32)


def _blend(px, c, page):
    """How much of colour c each pixel is, mixed with the page (t: 0 = page, 1 = c), and how far it is from
    any such mix. A thin line's pixels are mostly mixes: anti-aliasing and blur fade it into the page."""
    v = np.asarray(c, np.float32) - page
    vv = float((v ** 2).sum()) or 1.0
    d = px.astype(np.float32) - page
    t = (d @ v) / vv
    off = np.sqrt(np.maximum(0.0, (d ** 2).sum(-1) - t ** 2 * vv))
    return t, off


def _line_mask(px, c, page, tmin=0.35):
    """Pixels that are colour c, or c faded into the page by at most 1 - tmin."""
    t, off = _blend(px, c, page)
    return (t >= tmin) & (t <= 1.4) & (off < 35)


# ---------------------------------------------------------------------- checking the model's boxes and lines
def _snap_rect(arr, page, box, k):
    """The straight outline nearest a box the model drew: for each side, the row or column close by where ink
    runs along most of the side. Returns ((x0, y0, x1, y1), colour, thickness) in pixels, or None unless all
    four sides are there."""
    H, W = arr.shape[:2]
    x0, y0, x1, y1 = box
    w, h = x1 - x0, y1 - y0
    wx, wy = int(max(25 * k, 0.1 * w)), int(max(25 * k, 0.1 * h))
    ink = _dist(arr, page) > 40
    found, colours, thick = [], [], []
    for side in ("top", "bottom", "left", "right"):
        if side in ("top", "bottom"):
            pos, lo, hi = (y0 if side == "top" else y1 - 1), max(0, x0 + w // 10), min(W, x1 - w // 10)
            cand = range(max(0, pos - wy), min(H, pos + wy + 1))
            frac = np.array([ink[r, lo:hi].mean() if hi > lo else 0 for r in cand])
        else:
            pos, lo, hi = (x0 if side == "left" else x1 - 1), max(0, y0 + h // 10), min(H, y1 - h // 10)
            cand = range(max(0, pos - wx), min(W, pos + wx + 1))
            frac = np.array([ink[lo:hi, c].mean() if hi > lo else 0 for c in cand])
        good = np.flatnonzero(frac >= 0.8)  # solid: a dashed plot line running alongside isn't a side
        if not len(good):
            return None
        # the run of good rows (or columns) nearest where the model put the side
        groups = np.split(good, np.flatnonzero(np.diff(good) > 1) + 1)
        g = min(groups, key=lambda g: abs(cand[0] + g.mean() - pos))
        if len(g) <= max(6, 5 * k):
            at = cand[0] + int(round(g.mean()))
            thick.append(len(g))
        else:  # a filled area runs up to the line (bands sitting on an axis): the line is its outer edge
            outer = g.min() + 1 if side in ("top", "left") else g.max() - 1
            at = cand[0] + int(outer)
        found.append(at)
        colours.append(np.median(arr[at, lo:hi][ink[at, lo:hi]], 0) if side in ("top", "bottom")
                       else np.median(arr[lo:hi, at][ink[lo:hi, at]], 0))
    t, b, l, r = found
    if b - t < 4 or r - l < 4:
        return None
    return (l, t, r + 1, b + 1), np.median(colours, 0), float(np.median(thick)) if thick else 2.0


def verify_shapes(source, specs, log=print):
    """Drops plain boxes (no words in them) whose fill colour or outline isn't in the picture where the model
    put them: guesses, like bars drawn over a line chart it didn't understand."""
    k = source.width / CANVAS_W
    arr = np.asarray(source.convert("RGB"))
    H, W = arr.shape[:2]
    page = _page_colour(arr)
    out, dropped = [], 0
    for s in specs:
        if s["t"] != "shape" or (s.get("text") or "").strip() or s.get("grad"):
            out.append(s)
            continue
        x0, y0 = int(round(s["x0"] * k)), int(round(s["y0"] * k))
        x1, y1 = int(round(s["x1"] * k)), int(round(s["y1"] * k))
        x0, y0, x1, y1 = max(0, x0), max(0, y0), min(W, x1), min(H, y1)
        if x1 - x0 < 2 or y1 - y0 < 2:
            out.append(s)
            continue
        ok = True
        if s.get("fill"):  # a filled box is one solid colour nearly all over (labels on it aside)
            small = max(x1 - x0, y1 - y0) < 12  # a marker: round, its edges faded
            dx, dy = (0, 0) if small else ((x1 - x0) // 6, (y1 - y0) // 6)
            inner = arr[y0 + dy:y1 - dy, x0 + dx:x1 - dx].reshape(-1, 3)
            if len(inner):
                dom = _mode_colour(inner)
                solid = (_dist(inner, dom) < 40).mean() >= (0.3 if small else 0.6)
                model = _hex_rgb(s["fill"])
                bare = _dist(dom, page) < 30 and _dist(model, page) > 60  # just the page where it said a colour
                backdrop = _dist(model, page) < 30 and _dist(dom, model) >= 120  # a page-coloured panel behind it all
                ok = backdrop or (solid and not bare and _dist(dom, model) < 120)  # (not a different colour)
                if ok and 40 < _dist(dom, model) < 120:
                    s = dict(s, fill=_hex(dom))  # the model's colour was a shade off
        if x1 - x0 < 8 or y1 - y0 < 8:
            if ok:
                out.append(s)
            else:
                dropped += 1
            continue
        if ok and s.get("stroke") and float(s.get("sw") or 0) > 0 and s.get("kind", "rect") != "ellipse":
            snapped = _snap_rect(arr, page, (x0, y0, x1, y1), k)
            if snapped is not None:  # an outline all the way round: put it exactly where the picture has it
                (a, b, c, d), colour, sw = snapped
                s = dict(s, x0=a / k, y0=b / k, x1=c / k, y1=d / k, sw=round(max(0.5, sw / k), 2))
                if _dist(colour, _hex_rgb(s["stroke"])) > 60:
                    s["stroke"] = _hex(colour)
            elif not s.get("fill"):
                ok = False  # not a frame, just lines that happen to cross where its sides would be
        if ok and any(o["t"] == "shape" and o.get("kind") == s.get("kind") and not (o.get("text") or "").strip()
                      and max(abs(o[c] - s[c]) for c in ("x0", "y0", "x1", "y1")) < 4 for o in out):
            ok = False  # the same box twice (the model drew it twice; both snapped to the one in the picture)
        if ok:
            out.append(s)
        else:
            dropped += 1
    if dropped:
        log(f"  dropped {dropped} box(es) the picture doesn't show")
    return out


def fix_lines(source, specs, log=print):
    """Straight lines take the colour and dash style the picture shows along them (a legend's samples, say).
    Coloured lines with nothing under them are dropped (black and grey ones may be axes or ticks a few pixels
    off, so they stay)."""
    k = source.width / CANVAS_W
    arr = np.asarray(source.convert("RGB")).astype(np.float32)
    H, W = arr.shape[:2]
    out, fixed, dropped = [], 0, 0
    for s in specs:
        if s["t"] != "line":
            out.append(s)
            continue
        p = np.array([s["x0"], s["y0"]]) * k
        q = np.array([s["x1"], s["y1"]]) * k
        L = float(np.hypot(*(q - p)))
        if L < 8:
            out.append(s)
            continue
        nrm = np.array([-(q - p)[1], (q - p)[0]]) / L
        tol = max(2.0, float(s.get("sw") or 1) * k / 2 + 2)
        far = tol + 3 + 3 * k
        ts = np.linspace(0.1, 0.9, int(min(80, L)))
        pts = p[None] + ts[:, None] * (q - p)[None]

        def at(off):
            xy = np.rint(pts + off * nrm[None]).astype(int)
            ok = (xy[:, 0] >= 0) & (xy[:, 0] < W) & (xy[:, 1] >= 0) & (xy[:, 1] < H)
            c = np.full((len(xy), 3), np.nan, np.float32)
            c[ok] = arr[xy[ok, 1], xy[ok, 0]]
            return c
        bg = np.nanmedian(np.concatenate([at(-far), at(far)]), 0)
        if np.isnan(bg).any():
            out.append(s)
            continue
        near = np.stack([at(o) for o in np.arange(-tol, tol + 1)], 1)  # (samples, offsets, 3)
        d = np.nan_to_num(_dist(near, bg), nan=0.0)
        best = d.argmax(1)
        covered = d[np.arange(len(d)), best] > 60
        cover = covered.mean()
        if cover < 0.4:
            if _colourful(s.get("color")) and not s.get("a1") and not s.get("a2") and cover < 0.15:
                dropped += 1
                continue
            out.append(s)
            continue
        ink = np.median(near[np.arange(len(d)), best][covered], 0)
        new = dict(s)
        if cover >= 0.8:
            new["_seen"] = True  # really there: an axis, a frame side, a tick (not part of a plot line)
        if _dist(ink, _hex_rgb(s.get("color"))) > 60:
            new["color"] = _hex(ink)
        if L >= 30:  # (a tick is too short to tell dashes from where it ends)
            if cover >= 0.92 and s.get("dash", "solid") != "solid":
                new["dash"] = "solid"
            elif cover < 0.8 and s.get("dash", "solid") == "solid":
                new["dash"] = "dash"
        if any(new.get(f) != s.get(f) for f in ("color", "dash")):
            fixed += 1
        out.append(new)
    if fixed:
        log(f"  {fixed} line(s) took the colour or dash the picture shows")
    if dropped:
        log(f"  dropped {dropped} coloured line(s) the picture doesn't show")
    return out


# ---------------------------------------------------------------------- line geometry from pixel columns
def _runs(col, lo, hi):
    """Runs of set pixels in one column between rows lo and hi, as (starts, ends), or None."""
    hits = np.flatnonzero(col[lo:hi]) + lo
    if len(hits) == 0:
        return None
    cuts = np.flatnonzero(np.diff(hits) > 1)
    return np.r_[hits[0], hits[cuts + 1]], np.r_[hits[cuts], hits[-1]]


def _grow(col, s, e):
    """A run followed past the search window: a peak taller than the window is still one run."""
    while s > 0 and col[s - 1]:
        s -= 1
    while e < len(col) - 1 and col[e + 1]:
        e += 1
    return s, e


def _column_mask(m):
    """A line's pixels widened by one column each way, for following it: a steep, thin stretch fades in and
    out of neighbouring columns, which would break it into pieces column by column. A row or two missing
    between two bits of it in one column (where a steep side leaves a flat stretch) is filled in too."""
    wide = cv2.dilate(m.astype(np.uint8), np.ones((1, 3), np.uint8))
    return cv2.morphologyEx(wide, cv2.MORPH_CLOSE, np.ones((4, 1), np.uint8)) > 0


def _actual(mask, xs, S, E, x0=0):
    """Where the line's own pixels are in each column it was followed through (the widened runs reach into
    neighbouring columns' pixels). Columns with none (a gap between dashes) are left out."""
    X, S2, E2 = [], [], []
    for x, s, e in zip(xs, S, E):
        rows = np.flatnonzero(mask[int(s):int(e) + 1, x - x0])
        if len(rows):
            X.append(int(x))
            S2.append(s + rows[0])
            E2.append(s + rows[-1])
    return X, np.array(S2, np.float64), np.array(E2, np.float64)


def _points(xs, S, E, w, inset):
    """The line through the stretch of pixels it covers in each column. Neighbouring columns share the height
    where the line crosses from one to the other; a column reaching past both of its crossings holds a peak
    (or a dip), which gets a point of its own, `inset` (half the line's thickness) in from the end. So a sharp
    peak keeps its full height. A marker or blob, as tall as the columns either side of it, adds no peak."""
    n = len(xs)
    if n == 0:
        return []
    S = [float(v) for v in S]
    E = [float(v) for v in E]

    def mid(q):
        return (S[q] + E[q]) / 2

    def clamp(v, q):
        return min(max(v, S[q] + inset), E[q] - inset) if E[q] - S[q] > 2 * inset else mid(q)
    entries, exits = [None] * n, [None] * n
    for q in range(n - 1):
        if xs[q + 1] - xs[q] == 1:
            lo, hi = max(S[q], S[q + 1]), min(E[q], E[q + 1])
            if lo <= hi:
                b = (lo + hi) / 2
            elif E[q] < S[q + 1]:
                b = (E[q] + S[q + 1]) / 2
            else:
                b = (S[q] + E[q + 1]) / 2
            exits[q] = entries[q + 1] = (xs[q] + 1.0, b + 0.5)
        else:  # across a gap: leave from the side facing the next piece
            exits[q] = (xs[q] + 1.0, clamp(mid(q + 1), q) + 0.5)
            entries[q + 1] = (float(xs[q + 1]), clamp(mid(q), q + 1) + 0.5)
    tol = max(1.0, inset)
    tops, bots = [None] * n, [None] * n
    for q in range(n):  # columns reaching higher (a peak) or lower (a dip) than the columns either side
        nbs = [p for p in (q - 1, q + 1) if 0 <= p < n and abs(xs[p] - xs[q]) == 1]
        if not nbs:
            continue
        L = E[q] - S[q] + 1
        if L > 2.5 * w + 2 and len(nbs) == 2 and \
                all(min(E[q], E[p]) - max(S[q], S[p]) + 1 >= 0.8 * L for p in nbs):
            continue  # a marker or blob
        up = [S[q] - S[p] for p in nbs]  # negative: this column reaches higher
        if max(up) <= tol and min(up) < -tol:
            tops[q] = S[q] + inset
        down = [E[q] - E[p] for p in nbs]
        if min(down) >= -tol and max(down) > tol:
            bots[q] = E[q] - inset
    for q in range(n - 1):  # a peak two columns wide: its tip is where they meet, not a notch between two tips
        if xs[q + 1] - xs[q] != 1:
            continue
        for ext in (tops, bots):
            if ext[q] is not None and ext[q + 1] is not None:
                v = min(ext[q], ext[q + 1]) if ext is tops else max(ext[q], ext[q + 1])
                exits[q] = entries[q + 1] = (xs[q] + 1.0, v + 0.5)
                ext[q] = None
    pts = []
    for q in range(n):
        x = xs[q] + 0.5
        ent = entries[q][1] - 0.5 if entries[q] else None
        ext = exits[q][1] - 0.5 if exits[q] else None
        if q > 0 and xs[q] - xs[q - 1] > 1:
            pts.append(entries[q])
        if ent is None and ext is None:
            pts.append((x, mid(q) + 0.5))
            continue
        extra = [v for v in (tops[q], bots[q]) if v is not None]
        if ent is None or ext is None:  # an end of the line: it starts or stops at the far end of its column
            v = ext if ent is None else ent
            pts.append((x, (max(extra, key=lambda e: abs(e - v)) if extra else v) + 0.5))
        else:
            if len(extra) == 2 and abs(ent - extra[1]) < abs(ent - extra[0]):
                extra.reverse()
            pts += [(x, e + 0.5) for e in extra]
        if exits[q] is not None:
            pts.append(exits[q])
    return rdp(pts, 0.6)


def _tmap(arr, c, page, box):
    """How much of colour c each pixel in a box is (0 to 1), at its strongest within a pixel either way."""
    x0, y0, x1, y1 = box
    t, off = _blend(arr[y0:y1, x0:x1], c, page)
    return np.where(off < 35, np.clip(t, 0, 1), 0).astype(np.float32)


def _along(arr, pts, c, page):
    """How strongly colour c is drawn every pixel along a polyline (pixel coordinates): 0 to 1 per step, the
    strongest within a pixel or so either side of it (across the line, never along it into a dash's gap)."""
    p = np.asarray(pts, np.float64)
    if len(p) < 2:
        return np.zeros(0, np.float32)
    H, W = arr.shape[:2]
    x0, y0 = (np.floor(p.min(0)) - 3).astype(int)
    x1, y1 = (np.ceil(p.max(0)) + 4).astype(int)
    x0, y0, x1, y1 = max(0, x0), max(0, y0), min(W, x1), min(H, y1)
    tm = _tmap(arr, c, page, (x0, y0, x1, y1))
    seg = np.diff(p, axis=0)
    lens = np.hypot(*seg.T)
    keep = lens > 1e-6
    seg, a0, lens = seg[keep], p[:-1][keep], lens[keep]
    if not len(seg):
        return np.zeros(0, np.float32)
    m = np.maximum(1, np.ceil(lens)).astype(int)
    s = np.concatenate([a + np.outer(np.arange(n) / n, d) for a, d, n in zip(a0, seg, m)])
    nrm = np.repeat(np.stack([-seg[:, 1], seg[:, 0]], 1) / lens[:, None], m, axis=0)
    best = np.zeros(len(s), np.float32)
    for o in (-1.5, -0.75, 0.0, 0.75, 1.5):
        q = s + o * nrm
        ix = np.clip(np.floor(q[:, 0]).astype(int) - x0, 0, tm.shape[1] - 1)
        iy = np.clip(np.floor(q[:, 1]).astype(int) - y0, 0, tm.shape[0] - 1)
        best = np.maximum(best, tm[iy, ix])
    return best


def _style(arr, pts, c, page, xs, S, E, model_dash=None):
    """A traced line's thickness (its ink over its length: steep or jagged stretches make columns look thick)
    and dash style (from how much of its length is drawn, step by step along it)."""
    strength = _along(arr, pts, c, page)
    on = strength >= 0.5
    cover = float(on.mean()) if len(on) else 1.0
    ink = 0.0
    for x, s, e in zip(xs, S, E):
        t, off = _blend(arr[max(0, int(s) - 1):int(e) + 2, x], c, page)
        t = np.clip(t, 0, 1)[off < 35]
        ink += float(t[t > 0.1].sum())
    p = np.asarray(pts, float)
    length = float(np.hypot(*np.diff(p, axis=0).T).sum()) if len(p) > 1 else 1.0
    thick = ink / max(1.0, length * max(0.3, cover))
    runs_on, runs_off, run, last = [], [], 0, None
    for v in on:
        if v == last:
            run += 1
        else:
            if last is not None:
                (runs_on if last else runs_off).append(run)
            run, last = 1, v
    if last is not None:
        (runs_on if last else runs_off).append(run)
    gaps = [r for r in runs_off if r >= 2]
    # dashed: gaps all along it, about one per dash (a solid line has a few, where labels or other lines cross)
    period = (np.median(runs_on) if runs_on else 1) + (np.median(gaps) if gaps else 1)
    if len(gaps) < max(6, 0.3 * len(on) / max(1.0, period)):
        dash = "solid"
    elif model_dash in ("dash", "dot"):
        dash = model_dash
    else:
        dash = "dot" if np.median(runs_on or [0]) <= 2.5 * thick + 1 else "dash"
    return max(0.6, thick), dash


def _strokey(s):
    """A label of nothing but upright strokes ("11", "l", "|"): OCR's reading of a dashed line or a peak's
    sides as often as a real label."""
    import re
    return s["t"] == "text" and re.fullmatch(r"[1lI|!:;.,'`\s]+", s.get("text") or "") is not None


def _ignore_specs(specs, skip, area):
    """What else is drawn that could share a line's colour: axes, ticks, frames, labels, legend samples,
    markers. A big panel (one the line sits in) or a box the page's own colour only counts by its outline."""
    out = []
    for j, o in enumerate(specs):
        if j in skip or o["t"] not in ("line", "shape", "text") or _strokey(o):
            continue
        if o["t"] == "shape" and o.get("fill") and ((o["x1"] - o["x0"]) * (o["y1"] - o["y0"]) > area
                                                     or (_hex_rgb(o["fill"]) > 240).all()):
            o = dict(o, fill=None)
        out.append(o)
    return out


def _mask_of(arr, k, specs):
    """True where the given pieces are drawn."""
    H, W = arr.shape[:2]
    return cleanup.overlay_mask(arr.astype(np.uint8), (0, 0, W, H), k,
                                [{"t": "image", "x0": 0, "y0": 0, "x1": 0, "y1": 0}] + specs, 1) > 0


# ---------------------------------------------------------------------- the model's plot lines
def _guide(pts):
    """The model's path as y(x) (sorted by x), or None if it doubles back too much to be a plot line."""
    xs = np.array([p[0] for p in pts], dtype=np.float64)
    ys = np.array([p[1] for p in pts], dtype=np.float64)
    steps = np.diff(xs)
    if len(steps) == 0 or (steps < -1e-6).sum() > 0.2 * len(steps):
        return None
    order = np.argsort(xs, kind="stable")
    return xs[order], ys[order]


def _trace_one(arr, k, s, ignore, page):
    H, W = arr.shape[:2]
    g = _guide(s["pts"])
    if g is None:
        return None
    gx, gy = g[0] * k, g[1] * k  # source pixels
    xa, xb = int(max(0, gx[0] - 0.01 * W)), int(min(W - 1, gx[-1] + 0.01 * W))
    if xb - xa < 10:
        return None
    span = max(gy.max() - gy.min(), 0.05 * H)
    band = 0.15 * span + 6 * k + float(s.get("sw") or 1) * k * 2  # how far the true line may stray
    strip, ign = arr[:, xa:xb + 1], ignore[:, xa:xb + 1]
    # its true colour: what's near the model's colour along the model's path
    want = _hex_rgb(s["stroke"])
    samples, seen = [], 0
    for x in np.linspace(xa, xb, 80).astype(int):
        yc = float(np.interp(x, gx, gy))
        y0, y1 = int(max(0, yc - band)), int(min(H, yc + band + 1))
        col = strip[y0:y1, x - xa][~ign[y0:y1, x - xa]]
        if len(col):
            seen += 1
            samples.append(col[_dist(col, want) < 90])
    samples = np.concatenate(samples) if samples else np.zeros((0, 3), np.float32)
    if len(samples) < 20:
        # nothing of its colour anywhere along it, where the picture can be seen: the model imagined it
        return "absent" if seen >= 40 else None
    strong = _dist(samples, page)
    true = np.median(samples[strong >= np.percentile(strong, 70)], 0)  # the line's core, not its faded edges
    own = _line_mask(strip, true, page) & ~ign
    near = _column_mask(own)
    yc_all = np.interp(np.arange(xa, xb + 1), gx, gy)

    def window(x, extra=None):
        yc = yc_all[x - xa]
        lo, hi = yc - band, yc + band + 1
        if extra is not None:
            lo, hi = min(lo, extra[0]), max(hi, extra[1])
        return int(max(0, lo)), int(min(H, hi))

    # 1. how tall a column of it usually is: the run nearest the model's path
    lens = []
    for x in range(xa, xb + 1, max(1, (xb - xa) // 400)):
        r = _runs(near[:, x - xa], *window(x))
        if r is not None:
            j = int(np.argmin(np.abs((r[0] + r[1]) / 2.0 - yc_all[x - xa])))
            lens.append(r[1][j] - r[0][j] + 1)
    if len(lens) < 5:
        return None
    w = max(1.0, float(np.percentile(lens, 30)))
    reach = 4 * w + 2

    # 2. follow it: each column's run nearest to where the line just was
    xs, S, E, prev = [], [], [], None
    for x in range(xa, xb + 1):
        col = near[:, x - xa]
        r = _runs(col, *window(x, None if prev is None else (prev - reach, prev + reach + 1)))
        if r is None:
            continue
        starts, ends = r
        target = prev if prev is not None else yc_all[x - xa]
        j = int(np.argmin(np.maximum(0, np.maximum(starts - target, target - ends))))
        st, en = _grow(col, int(starts[j]), int(ends[j]))
        xs.append(x)
        S.append(st)
        E.append(en)
        if en - st + 1 > 2.5 * w + 2:  # a steep stretch: the line leaves from the far end
            prev = en if abs(st - target) <= abs(en - target) else st
        else:
            prev = (st + en) / 2.0
    if len(xs) / float(xb - xa + 1) < 0.25 or len(xs) < 3:
        return None
    if (np.abs((np.array(S) + np.array(E)) / 2 - yc_all[np.array(xs) - xa]) <= band).mean() < 0.5:
        return None  # it wandered off onto something else
    xs, S, E = _actual(own, xs, S, E, xa)
    if len(xs) < 3:
        return None
    w = max(1.0, float(np.percentile(E - S + 1, 30)))
    thick, dash = _style(arr, _points(xs, S, E, w, w / 2), true, page, xs, S, E, s.get("dash"))
    pts = _points(xs, S, E, w, thick / 2)

    def hidden(seg):  # the model's own stretch of line runs under something else (an axis, a label)
        p = np.asarray(seg, float)
        if len(p) < 2:
            return False
        d = np.concatenate([a + np.outer(np.linspace(0, 1, 6)[:-1], b - a) for a, b in zip(p[:-1], p[1:])])
        xi = np.clip(d[:, 0].astype(int), 0, W - 1)
        yi = np.clip(d[:, 1].astype(int), 0, H - 1)
        return ignore[yi, xi].mean() >= 0.6
    # where the picture can't show the line (it runs along the axis, or behind a legend), the model's version
    # stays: at either end, and across any gap in the trace
    guide = list(zip(gx, gy))
    head = [p for p in guide if p[0] < pts[0][0] - 2]
    tail = [p for p in guide if p[0] > pts[-1][0] + 2]
    filled = head if head and hidden(head + [pts[0]]) else []
    for a, b in zip(pts[:-1], pts[1:]):
        filled.append(a)
        if b[0] - a[0] > 3:
            inside = [p for p in guide if a[0] + 1 < p[0] < b[0] - 1]
            if inside and hidden([a] + inside + [b]):
                filled += inside
    filled.append(pts[-1])
    if tail and hidden([pts[-1]] + tail):
        filled += tail
    pts = filled
    return dict(s, pts=[(x / k, y / k) for x, y in pts], stroke=_hex(true), sw=round(thick / k, 2), dash=dash)


# ---------------------------------------------------------------------- plot lines the model missed
def _ink_colours(px, page, most=10, merge=45.0):
    """The main colours among some pixels: busy colour bins, nearby ones merged, and faded versions of a
    stronger colour (a line's anti-aliased edges) counted as that colour."""
    px = px.astype(np.int64)
    if len(px) < 50:
        return []
    keys = (px[:, 0] // 8) * 1024 + (px[:, 1] // 8) * 32 + px[:, 2] // 8
    counts = np.bincount(keys, minlength=32768)
    sums = np.stack([np.bincount(keys, weights=px[:, c], minlength=32768) for c in range(3)], 1)
    centres, weights = [], []
    for b in np.argsort(counts)[::-1]:
        n = counts[b]
        if n < max(30, 0.01 * len(px)):
            break
        c = sums[b] / n
        for i, cc in enumerate(centres):
            if np.sqrt(((c - cc) ** 2).sum()) < merge:
                centres[i] = (cc * weights[i] + c * n) / (weights[i] + n)
                weights[i] += n
                break
        else:
            centres.append(c)
            weights.append(n)
    keep = []
    for c in sorted(centres, key=lambda c: -_dist(np.asarray(c, np.float32), page)):  # strongest first
        c = np.asarray(c, np.float32)
        faded = False
        for kc in keep:
            t, off = _blend(c[None], kc, page)
            if 0.05 < t[0] < 1.1 and off[0] < 25:
                faded = True
                break
        if not faded:
            keep.append(c)
    return keep[:most]


def _tracks(mask, reach, k):
    """Thin runs of set pixels followed column by column into separate lines: each column's runs join the line
    they touch (or come nearest to, across small gaps). Returns [(xs, starts, ends), ...]."""
    H, W = mask.shape
    max_gap = int(max(6, 12 * k))
    active, done = [], []
    for x in range(W):
        r = _runs(mask[:, x], 0, H)
        runs = list(zip(r[0].tolist(), r[1].tolist())) if r is not None else []
        pairs = []
        for ti, t in enumerate(active):
            gap = x - t["x"][-1]
            for ri, (s, e) in enumerate(runs):
                d = max(0, s - t["e"], t["s"] - e)
                if d <= reach + 8 * max(1.0, k) * (gap - 1):
                    pairs.append((d, ti, ri))
        pairs.sort()
        used_t, used_r = set(), set()
        for d, ti, ri in pairs:
            if ti in used_t or ri in used_r:
                continue
            used_t.add(ti)
            used_r.add(ri)
            t, (s, e) = active[ti], runs[ri]
            t["x"].append(x)
            t["S"].append(s)
            t["E"].append(e)
            t["s"], t["e"] = s, e
        for ri, (s, e) in enumerate(runs):
            if ri not in used_r:
                active.append({"x": [x], "S": [s], "E": [e], "s": s, "e": e})
        still = []
        for t in active:
            (still if x - t["x"][-1] <= max_gap else done).append(t)
        active = still
    return [(t["x"], t["S"], t["E"]) for t in done + active]


def _join(tracks, reach, k):
    """Pieces of one line that the following lost and picked up again (at a crossing, say): a piece that
    starts where another ends, at the same height, carries it on."""
    max_gap = max(6, 12 * k)
    out = []
    for xs, S, E in sorted(tracks, key=lambda t: t[0][0]):
        best, bd = None, None
        for o in out:
            last = o[0][-1]
            if not last - 15 <= xs[0] <= last + max_gap:
                continue
            q = next((i for i, x in enumerate(xs) if x > last), None)
            if q is None:
                continue
            d = max(0, S[q] - o[2][-1], o[1][-1] - E[q])
            allow = reach + 8 * max(1.0, k) * (xs[q] - last - 1)
            if d > allow:  # where they overlap, did they run together (one lost the line up a steep side)?
                tail = {x: (s, e) for x, s, e in zip(o[0][-15:], o[1][-15:], o[2][-15:])}
                for x, s, e in zip(xs[:q], S[:q], E[:q]):
                    for xx in (x - 1, x, x + 1):
                        if xx in tail:
                            d = min(d, max(0, s - tail[xx][1], tail[xx][0] - e))
                allow = reach
            if d <= allow and (bd is None or d < bd):
                best, bd = (o, q), d
        if best is None:
            out.append((list(xs), list(S), list(E)))
        else:
            o, q = best
            o[0].extend(xs[q:])
            o[1].extend(S[q:])
            o[2].extend(E[q:])
    return out


def find_lines(source, specs, log=print):
    """Plot lines the model missed, straight from the picture: long thin runs of one colour (not the colour of
    a filled area: a band thin at its ends looks like a line there). Short level stretches in a plot line's
    colour are its legend sample."""
    traced = [i for i, s in enumerate(specs) if s.get("_traced")]
    areas = [_hex_rgb(s["fill"]) for s in specs if s["t"] == "path" and s.get("fill")]
    areas += [_hex_rgb(d["fill"]) for s in specs if s["t"] == "raw" for d in s["items"] if d.get("fill")]
    k = source.width / CANVAS_W
    arr = np.asarray(source.convert("RGB"))
    H, W = arr.shape[:2]
    # how wide the plot is: the model's longest level line (an x axis), or half the picture
    level = [abs(s["x1"] - s["x0"]) * k for s in specs
             if s["t"] == "line" and abs(s["y1"] - s["y0"]) * 8 < abs(s["x1"] - s["x0"])]
    plot_w = max(level + [0.5 * W])
    min_len = max(40.0, 0.3 * plot_w)
    page = _page_colour(arr)
    ink = _dist(arr, page) > 60
    # not lines: labels, filled boxes and areas, pictures. (Not the model's own lines and outlines: one of its
    # guesses lying across a real line would cut it. Frames, axes and ticks are told apart by their shape.)
    others = [s for s in specs if (s["t"] == "text" and not _strokey(s)) or (s["t"] == "path" and s.get("fill"))
              or (s["t"] == "line" and s.get("_seen"))]  # straight lines the picture confirms: axes, ticks, grid
    others += [s for s in _ignore_specs(specs, set(traced), 0.25 * W * H / k / k) if s["t"] == "shape" and s.get("fill")]
    ink &= ~_mask_of(arr, k, others)
    for s in specs:
        if s["t"] == "image":
            ink[max(0, int(s["y0"] * k)):int(s["y1"] * k) + 1, max(0, int(s["x0"] * k)):int(s["x1"] * k) + 1] = False
    # plot lines already traced: anything along them is taken
    taken = np.zeros((H, W), np.uint8)
    for i in traced:
        p = np.array([(x * k, y * k) for x, y in specs[i]["pts"]], np.int32)
        cv2.polylines(taken, [p], False, 255, int(max(3, 2 * float(specs[i].get("sw") or 1) * k + 4)))
    new, short = [], []
    gap = int(max(3, round(5 * k)))
    reach = 3 + 2 * k
    for c in _ink_colours(arr[ink], page):
        if any(_dist(c, a) < 45 for a in areas):
            continue
        raw = ink & _line_mask(arr, c, page)
        if raw.sum() < 20:
            continue
        # bridge the gaps between dashes (along the line, and up a steep stretch)
        r8 = raw.astype(np.uint8)
        closed = raw | (cv2.morphologyEx(r8, cv2.MORPH_CLOSE, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (gap, gap))) > 0) \
            | (cv2.morphologyEx(r8, cv2.MORPH_CLOSE, np.ones((2 * gap, 1), np.uint8)) > 0)
        for xs, S, E in _join(_tracks(_column_mask(closed), reach, k), reach, k):
            span = xs[-1] - xs[0] + 1
            if len(xs) < 0.5 * span:
                continue
            xs, S, E = _actual(raw, xs, S, E)
            if len(xs) < 3 or len(xs) < 0.25 * (xs[-1] - xs[0] + 1):
                continue  # followed mostly along something else, with only a few bits in this colour
            xs = np.array(xs)
            L = E - S + 1
            w = max(1.0, float(np.percentile(L, 30)))
            mids = ((S + E) / 2).astype(int)
            if (taken[mids, xs] > 0).mean() > 0.5:
                continue  # the same line again (traced already, or in a nearby shade)
            dev = np.abs(mids - np.median(mids))
            if span < min_len:  # maybe a legend sample: short, thin and level
                if span >= max(10, 8 * k) and dev.max() <= max(2, w) and np.median(L) <= 2 * w + 2:
                    short.append((xs, S, E, c))
                continue
            if np.median(L) > max(25.0, 20 * k):
                continue  # a filled area, not a line
            inner = dev[int(0.03 * len(dev)):max(1, int(0.97 * len(dev)))]  # a frame's sides are at its ends
            if np.percentile(inner, 99) <= max(2.5 * w, 12 * k):
                continue  # level, with nothing rising above tick height: a frame, an axis or a grid line
            # its own colour: the strongest of its pixels (a paler line can share a stronger one's mask)
            px = np.concatenate([arr[int(s):int(e) + 1, x][raw[int(s):int(e) + 1, x]] for x, s, e in zip(xs, S, E)])
            t, _ = _blend(px, c, page)
            own = np.median(px[t >= np.percentile(t, 80)], 0) if len(px) else c
            thick, dash = _style(arr, _points(xs, S, E, w, w / 2), own, page, xs, S, E)
            pts = _points(xs, S, E, w, thick / 2)
            cv2.polylines(taken, [np.array(pts, np.int32)], False, 255, int(max(3, 2 * thick + 4)))
            new.append({"t": "path", "stroke": _hex(own), "fill": None, "sw": round(thick / k, 2), "dash": dash,
                        "closed": False, "pts": [(x / k, y / k) for x, y in pts], "_traced": True})
    # legend samples: a short level stroke in the colour of a plot line, drawn like it
    lines = [specs[i] for i in traced] + new
    samples = []
    for xs, S, E, c in short:
        mids = (S + E) / 2
        if (taken[mids.astype(int), xs] > 0).mean() > 0.3:
            continue
        best = min(lines, key=lambda s: _dist(_hex_rgb(s["stroke"]), c), default=None)
        if best is None or _dist(_hex_rgb(best["stroke"]), c) > 60:
            continue
        y = float(np.mean(mids)) + 0.5
        samples.append({"t": "line", "x0": xs[0] / k, "y0": y / k, "x1": (xs[-1] + 1) / k, "y1": y / k,
                        "color": best["stroke"], "sw": best["sw"], "dash": best.get("dash", "solid"),
                        "a1": False, "a2": False})
    if new:
        log(f"  found {len(new)} plot line(s) the model missed ({sum(len(s['pts']) for s in new)} points)")
    if samples:
        log(f"  found {len(samples)} legend sample(s)")
    return specs + new + samples


def drop_echoes(source, specs, log=print):
    """The model's own rough copies of a traced plot line: straight lines in its colour running along it, and
    its own plot lines that couldn't be traced (the picture doesn't have them where it said) running along
    one that was. And "labels" like "11" that are OCR's reading of a traced line."""
    k = source.width / CANVAS_W
    H, W = source.height, source.width
    paths = [s for s in specs if s["t"] == "path" and s.get("_traced")]
    if not paths:
        return specs
    out, dropped = [], 0
    masks = []
    for p in paths:
        m = np.zeros((H, W), np.uint8)
        cv2.polylines(m, [np.array([(x * k, y * k) for x, y in p["pts"]], np.int32)], False, 255,
                      int(max(3, 2 * float(p.get("sw") or 1) * k + 6 * k)))
        masks.append((m, _hex_rgb(p["stroke"])))
    for s in specs:
        if s["t"] == "line" and not s.get("a1") and not s.get("a2"):
            ts = np.linspace(0, 1, 20)
            xs = np.clip(((s["x0"] + ts * (s["x1"] - s["x0"])) * k).astype(int), 0, W - 1)
            ys = np.clip(((s["y0"] + ts * (s["y1"] - s["y0"])) * k).astype(int), 0, H - 1)
            if any((m[ys, xs] > 0).mean() >= 0.6 and _dist(_hex_rgb(s.get("color")), c) < 80 for m, c in masks):
                dropped += 1
                continue
        if s["t"] == "path" and not s.get("fill") and not s.get("_traced") and len(s["pts"]) >= 2:
            p = np.asarray(s["pts"], float) * k
            seg = np.linspace(0, 1, 8)[:-1]
            pts = np.concatenate([a + np.outer(seg, b - a) for a, b in zip(p[:-1], p[1:])] + [p[-1:]])
            xs = np.clip(pts[:, 0].astype(int), 0, W - 1)
            ys = np.clip(pts[:, 1].astype(int), 0, H - 1)
            if any((m[ys, xs] > 0).mean() >= 0.7 for m, _ in masks):
                dropped += 1
                continue
        if _strokey(s):
            box = (slice(max(0, int(s["y0"] * k)), int(s["y1"] * k) + 1), slice(max(0, int(s["x0"] * k)), int(s["x1"] * k) + 1))
            if any((m[box] > 0).mean() > 0.15 for m, _ in masks):
                dropped += 1
                continue
        out.append(s)
    if dropped:
        log(f"  dropped {dropped} rough copy(ies) of traced plot lines")
    return out


def trace_lines(source, specs, log=print):
    """The model's plot lines (open stroked paths) re-traced from the picture."""
    k = source.width / CANVAS_W
    arr = np.asarray(source.convert("RGB")).astype(np.float32)
    H, W = arr.shape[:2]
    page = _page_colour(arr)
    lines = [i for i, s in enumerate(specs) if s["t"] == "path" and not s.get("fill") and s.get("stroke")
             and not s.get("closed") and len(s["pts"]) >= 2]
    out, traced, points, absent = list(specs), [], 0, set()
    if lines:
        ignore = _mask_of(arr, k, _ignore_specs(specs, set(), 0.25 * W * H / k / k))
        for i in lines:
            new = _trace_one(arr, k, specs[i], ignore, page)
            if new == "absent":
                absent.add(i)
            elif new is not None:
                out[i] = dict(new, _traced=True)
                traced.append(i)
                points += len(new["pts"])
    if traced:
        log(f"  traced {len(traced)} plot line(s) from the picture ({points} points)")
    if absent:
        log(f"  dropped {len(absent)} plot line(s) the picture doesn't have")
    return [s for i, s in enumerate(out) if i not in absent]


# ---------------------------------------------------------------------- filled areas
def _bbox(s):
    xs = [x for x, _ in s["pts"]]
    ys = [y for _, y in s["pts"]]
    return min(xs), min(ys), max(xs), max(ys)


def _grow_box(arr, box, colours, step):
    """Widens a box while its edges still run through the area's colours (the model's corners can stop
    short of the real edges); stops at a frame line or the page."""
    H, W = arr.shape[:2]
    x0, y0, x1, y1 = box

    def busy(strip):  # the area still crosses this edge (even just a peak poking out past it)
        if strip.size == 0:
            return False
        d = np.min(np.stack([_dist(strip, c) for c in colours]), 0)
        return (d < 60).mean() >= 0.08
    for _ in range(12):
        moved = False
        if y0 > 0 and busy(arr[y0, x0:x1]):
            y0, moved = max(0, y0 - step), True
        if y1 < H and busy(arr[y1 - 1, x0:x1]):
            y1, moved = min(H, y1 + step), True
        if x0 > 0 and busy(arr[y0:y1, x0]):
            x0, moved = max(0, x0 - step), True
        if x1 < W and busy(arr[y0:y1, x1 - 1]):
            x1, moved = min(W, x1 + step), True
        if not moved:
            break
    return x0, y0, x1, y1


def snap_areas(source, specs, log=print):
    """Filled areas traced from the picture: neighbouring areas (the bands of one chart) as one region."""
    k = source.width / CANVAS_W
    arr = np.asarray(source.convert("RGB"))
    page = _page_colour(arr)
    fills = [i for i, s in enumerate(specs)  # (an area the page's own colour is just background: nothing to trace)
             if s["t"] == "path" and s.get("fill") and len(s["pts"]) >= 3 and not s.get("grad")
             and _dist(_hex_rgb(s["fill"]), page) > 30]
    if not fills:
        return specs
    # group areas whose boxes touch: one chart's bands together
    groups = []
    for i in fills:
        b = _bbox(specs[i])
        for grp in groups:
            gb = grp["box"]
            if b[0] <= gb[2] and b[2] >= gb[0] and b[1] <= gb[3] and b[3] >= gb[1]:
                grp["members"].append(i)
                grp["box"] = (min(b[0], gb[0]), min(b[1], gb[1]), max(b[2], gb[2]), max(b[3], gb[3]))
                break
        else:
            groups.append({"members": [i], "box": b})
    replace, made = {}, 0
    for grp in groups:
        area = dict(zip(("x0", "y0", "x1", "y1"), grp["box"]), t="image")
        box = cleanup.pixel_box(source, area)
        if box is None:
            continue
        wanted = [_hex_rgb(specs[j]["fill"]) for j in grp["members"]]
        box = _grow_box(arr, box, wanted, int(max(2, round(3 * k))))
        area.update(x0=box[0] / k, y0=box[1] / k, x1=box[2] / k, y1=box[3] / k)
        members = set(grp["members"])
        first = min(members)
        others = [s for j, s in enumerate(specs) if j not in members]
        z = sum(1 for j in range(first) if j not in members) + 1  # where the area sits among the rest
        layered = others[:z - 1] + [area] + others[z - 1:]
        crop = source.crop(box)
        # what's drawn on top (labels, arrows, lines) is unknown ground: it takes the colour of the nearest band
        hidden = cleanup.overlay_mask(np.asarray(crop.convert("RGB")), box, k, layered, z) > 0
        regions = cleanup.trace_flat(crop, page_share=0.0, unknown=hidden)
        if not regions:
            continue  # shaded or textured: keep the model's version
        # the picture should agree with the model about the colours, or it's tracing something else
        found = np.array([_hex_rgb(c) for c, _, _ in regions])
        agree = sum(np.sqrt(((found - c) ** 2).sum(1)).min() < 80 for c in wanted)
        if agree < max(1, 0.5 * len(wanted)) or len(regions) > 2 * len(wanted) + 3:
            continue
        replace[first] = {"t": "raw", "items": cleanup.region_items(regions, box, k, 0)}
        for j in members:
            replace.setdefault(j, None)
        made += len(regions)
    if not replace:
        return specs
    out = [replace[i] if i in replace else s for i, s in enumerate(specs)]
    log(f"  traced {made} filled area(s) from the picture")
    return [s for s in out if s is not None]


def refine(source, specs, log=print):
    """All of the above, in order: check boxes and lines, trace plot lines, then filled areas."""
    specs = verify_shapes(source, specs, log)
    specs = fix_lines(source, specs, log)
    specs = trace_lines(source, specs, log)
    specs = snap_areas(source, specs, log)
    specs = drop_echoes(source, find_lines(source, specs, log), log)
    return [{k: v for k, v in s.items() if k not in ("_traced", "_seen")} for s in specs]
