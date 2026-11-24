"""Tidies up picture regions after the model has read a figure.

1. erase_overlays: anything the model found drawn on top of a picture region (labels, arrows, boxes, lines)
   is erased from the cut-out and filled in from its surroundings (OpenCV inpainting), so it exists only once,
   as its own editable piece, not also baked into the picture underneath.
2. trace_flat: a picture region made of a few solid colours (stacked areas, bars, filled shapes) is traced
   into one filled polygon per colour, like Inkscape's Trace Bitmap. Shaded or textured regions (a real
   heatmap, a photo) are left as pictures.

Works in the cut-out's own pixels. `k` is the source picture's pixels per slide pixel (source width / 1280).
"""
import cv2
import numpy as np

from .tokens import hex_rgb

CANVAS_W = 1280.0


def pixel_box(source, s):
    """A picture piece's box in the source picture's pixels (left, top, right, bottom), or None if tiny."""
    k = source.width / CANVAS_W
    box = (max(0, round(s["x0"] * k)), max(0, round(s["y0"] * k)),
           min(source.width, round(s["x1"] * k)), min(source.height, round(s["y1"] * k)))
    if box[2] - box[0] < 4 or box[3] - box[1] < 4:
        return None
    return box


# ---------------------------------------------------------------------- 1. erasing what's drawn on top
def _local(box, k, x, y):
    return int(round(x * k - box[0])), int(round(y * k - box[1]))


def _text_pixels(arr, rect, color):
    """Inside a label's box: the pixels that belong to the letters rather than to what's behind them."""
    x0, y0, x1, y1 = rect
    sub = arr[y0:y1, x0:x1].astype(np.int32)
    if sub.size == 0:
        return None
    ring = np.concatenate([sub[0], sub[-1], sub[:, 0], sub[:, -1]])
    bg = np.median(ring, 0)
    d_bg = np.sqrt(((sub - bg) ** 2).sum(-1))
    d_tc = np.sqrt(((sub - np.array(hex_rgb(color))) ** 2).sum(-1)) if color else d_bg + 1e9
    m = (d_bg > 40) & ((d_tc < d_bg) | (d_tc < 80))
    if m.mean() < 0.01:  # the model's colour guess was off: take whatever stands out from the background
        m = d_bg > 40
    return m


def overlay_mask(arr, box, k, specs, z):
    """255 where pieces painted after picture number `z` cover it."""
    h, w = arr.shape[:2]
    mask = np.zeros((h, w), np.uint8)
    pad = max(3, round(5 * k))
    for zz, s in enumerate(specs, start=1):
        if zz <= z or s["t"] == "image":
            continue
        t = s["t"]
        if t in ("text", "shape"):
            a, b = _local(box, k, s["x0"], s["y0"])
            c, d = _local(box, k, s["x1"], s["y1"])
            if c < 0 or d < 0 or a >= w or b >= h:
                continue
            if t == "text":
                # a label is one line: never let an oversized box rub out more than a line of its own text
                line = 1.5 * float(s.get("size", 20)) * k
                long = (len((s.get("text") or "").strip()) or 1) * 0.8 * float(s.get("size", 20)) * k + line
                if int(s.get("rot") or 0) % 360 in (90, 270):
                    mid = (a + c) / 2
                    a, c = max(a, int(mid - line / 2)), min(c, int(mid + line / 2))
                    d = min(d, int(b + long))
                else:
                    d, c = min(d, int(b + line)), min(c, int(a + long))
                rect = (max(0, a - pad), max(0, b - pad), min(w, c + pad), min(h, d + pad))
                m = _text_pixels(arr, rect, s.get("color"))
                if m is not None:
                    mask[rect[1]:rect[3], rect[0]:rect[2]][m] = 255
            else:
                th = -1 if s.get("fill") else max(3, round((float(s.get("sw") or 1) + 3) * k))
                if s.get("kind") == "ellipse":
                    cv2.ellipse(mask, (((a + c) / 2, (b + d) / 2), (abs(c - a), abs(d - b)), 0), 255, th)
                else:
                    cv2.rectangle(mask, (a, b), (c, d), 255, th)
        elif t == "line":
            p, q = _local(box, k, s["x0"], s["y0"]), _local(box, k, s["x1"], s["y1"])
            sw = float(s.get("sw") or 1)
            cv2.line(mask, p, q, 255, max(3, round((sw * 2 + 3) * k)))
            head = round(max(14.0, sw * 4.5) * k) + 3  # arrowheads are wider than the line
            if s.get("a2"):
                cv2.circle(mask, q, head, 255, -1)
            if s.get("a1"):
                cv2.circle(mask, p, head, 255, -1)
        elif t == "path":
            pts = np.array([_local(box, k, x, y) for x, y in s["pts"]], np.int32)
            if len(pts) < 2:
                continue
            if s.get("fill") and s.get("closed"):
                cv2.fillPoly(mask, [pts], 255)
            cv2.polylines(mask, [pts], bool(s.get("closed")), 255,
                          max(3, round((float(s.get("sw") or 1) * 2 + 3) * k)))
    if mask.any():
        mask = cv2.dilate(mask, np.ones((3, 3), np.uint8), iterations=max(1, round(1.5 * k)))
    return mask


def erase_overlays(img, box, k, specs, z):
    """The cut-out with everything drawn on top of it erased and filled in from around it."""
    from PIL import Image
    arr = np.asarray(img.convert("RGB"))
    mask = overlay_mask(arr, box, k, specs, z)
    if not mask.any():
        return img
    radius = max(3, round(4 * k))
    out = cv2.inpaint(np.ascontiguousarray(arr[:, :, ::-1]), mask, radius, cv2.INPAINT_TELEA)
    return Image.fromarray(out[:, :, ::-1].copy())


# ---------------------------------------------------------------------- 2. tracing solid colours
def _hex(c):
    r, g, b = (int(round(float(v))) for v in c)
    return f"#{max(0, min(255, r)):02x}{max(0, min(255, g)):02x}{max(0, min(255, b)):02x}"


def _palette(cols, max_colors, merge=20.0):
    """The solid colours in a set of flat-area pixels: busy colour bins, nearby ones merged, or None if they
    don't account for nearly all the pixels (shading) or there are too many (detail)."""
    cols = cols.astype(np.int64)
    if len(cols) < 50:
        return None
    keys = (cols[:, 0] // 8) * 1024 + (cols[:, 1] // 8) * 32 + cols[:, 2] // 8
    counts = np.bincount(keys, minlength=32768)
    sums = np.stack([np.bincount(keys, weights=cols[:, c], minlength=32768) for c in range(3)], 1)
    centers, weights = [], []
    for b in np.argsort(counts)[::-1]:
        n = counts[b]
        if n < max(20, 0.0015 * len(cols)):  # thin bands still pass: 0.15% of the flat area is enough
            break
        c = sums[b] / n
        for i, cc in enumerate(centers):
            if np.sqrt(((c - cc) ** 2).sum()) < merge:
                centers[i] = (cc * weights[i] + c * n) / (weights[i] + n)
                weights[i] += n
                break
        else:
            centers.append(c)
            weights.append(n)
    if not centers or len(centers) > max_colors:
        return None
    centers = np.array(centers, np.float32)
    near = np.sqrt(((cols[:, None, :].astype(np.float32) - centers[None]) ** 2).sum(-1)).min(1)
    if (near < merge).mean() < 0.97:
        return None
    return centers


def trace_flat(img, max_colors=24, min_share=0.0015, page_share=0.02, unknown=None):
    """[(colour, share of the area, [polygon, ...]), ...] biggest first, or None when the region has shading
    or texture and should stay a picture. Polygons are in the cut-out's pixels; holes come as extra
    polygons (fill them even-odd). White covering at least `page_share` of the region is the page showing
    through (left empty); less than that is a speck and takes the colour around it. Pixels marked `unknown`
    (under labels drawn on top) take the colour of the nearest region."""
    a = np.asarray(img.convert("RGB"))
    h, w = a.shape[:2]
    if h < 8 or w < 8:
        return None
    known = np.ones((h, w), bool) if unknown is None else ~unknown
    if known.mean() < 0.3:
        return None
    sm = cv2.medianBlur(a, 5 if min(h, w) > 60 else 3)  # JPEG speckle and anti-aliasing out of the way
    gray = cv2.cvtColor(sm, cv2.COLOR_RGB2GRAY)
    mag = cv2.magnitude(cv2.Sobel(gray, cv2.CV_32F, 1, 0), cv2.Sobel(gray, cv2.CV_32F, 0, 1))
    flat = (mag < 8) & known
    # Solid fills are flat almost everywhere (only their boundaries change colour): 90-97% of a stacked area
    # chart, even shrunk and blurred. A heatmap's shading and faint lines leave only 55-75% flat.
    if flat.sum() < 0.85 * known.sum() or ((mag > 60) & known).sum() > 0.10 * known.sum():
        return None
    # a smooth colour ramp (a colour bar) is flat pixel to pixel too, but needs dozens of colours to cover it;
    # solid fills need a handful even when shrunk and blurred
    q = (sm[flat] // 8).astype(np.int64)
    counts = np.sort(np.bincount(q[:, 0] * 1024 + q[:, 1] * 32 + q[:, 2]))[::-1]
    if np.searchsorted(np.cumsum(counts) / max(1, counts.sum()), 0.95) + 1 > 24:
        return None
    centers = _palette(sm[flat], max_colors)
    if centers is None:
        return None
    px = sm.reshape(-1, 3).astype(np.float32)
    lab = np.empty(len(px), np.uint8)
    for i in range(0, len(px), 200000):
        lab[i:i + 200000] = ((px[i:i + 200000, None, :] - centers[None]) ** 2).sum(-1).argmin(1)
    lab = cv2.medianBlur(lab.reshape(h, w), 5)  # tidy the fringes along each boundary
    shares = np.bincount(lab.ravel(), minlength=len(centers)) / lab.size
    white = (centers > 246).all(1)
    page = white & (shares >= page_share)             # a real white area (above a chart that stops short of 100 %)
    keep = (shares >= min_share) & ~white
    lost = (~keep[lab] & ~page[lab]) | ~known         # specks, smudges left where a label was: no colour of their own
    min_area = max(12.0, 0.0004 * h * w)
    for ci in np.flatnonzero(keep):                   # islands too small to become shapes would leave holes
        n, cc, stats, _ = cv2.connectedComponentsWithStats((lab == ci).astype(np.uint8), connectivity=4)
        small = np.flatnonzero(stats[1:, cv2.CC_STAT_AREA] < min_area) + 1
        if len(small):
            lost |= np.isin(cc, small)
    if lost.any() and (~lost).any():
        from scipy import ndimage
        _, (iy, ix) = ndimage.distance_transform_edt(lost, return_indices=True)
        lab = lab[iy, ix]                             # each takes the colour of the nearest proper region
    regions = []
    kernel = np.ones((5, 5) if min(h, w) > 300 else (3, 3), np.uint8)
    for ci, col in enumerate(centers):
        if not keep[ci]:
            continue
        m = (lab == ci).astype(np.uint8) * 255
        share = float((m > 0).mean())
        m = cv2.dilate(m, kernel)  # a hair of overlap, so neighbouring colours leave no seams
        contours, _ = cv2.findContours(m, cv2.RETR_CCOMP, cv2.CHAIN_APPROX_SIMPLE)
        polys = []
        for cnt in contours:
            area, length = cv2.contourArea(cnt), cv2.arcLength(cnt, True)
            if area < max(12.0, 0.0004 * h * w):
                continue
            if 2 * area / max(1.0, length) < max(4.0, 0.006 * min(h, w)):
                continue  # only a few pixels thick on average: a frame line or an edge, not a filled area
            p = cv2.approxPolyDP(cnt, 1.5, True).reshape(-1, 2)  # edges move at most 1.5 px: no gaps open up
            if abs(cv2.contourArea(p.reshape(-1, 1, 2))) > 1.3 * area + 50:
                p = cnt.reshape(-1, 2)  # simplifying would bulge the shape: keep every point
            if len(p) >= 3:
                polys.append(p)
        if polys:
            regions.append((_hex(col), share, polys))
    regions.sort(key=lambda r: -r[1])  # big areas first, so smaller ones sit on top of them
    return regions or None


def region_items(regions, box, k, z):
    """DeNotive filled-path items for traced regions, grouped so the figure's body moves as one piece."""
    from denotive.slide.items import SlidePath
    items, n = [], len(regions)
    for i, (col, _, polys) in enumerate(regions):
        segs = []
        for p in polys:
            pts = [((box[0] + float(x)) / k, (box[1] + float(y)) / k) for x, y in p]
            segs.append(["M", pts[0][0], pts[0][1]])
            segs += [["L", x, y] for x, y in pts[1:]]
            segs.append(["Z"])
        d = SlidePath.from_segments(segs, fill=col, stroke=None, sw=0, dash="solid", rule="evenodd", cap="butt")
        d.update(z=z + i / (n + 1), id=f"v{z}t{i}", group=f"trace{z}")
        items.append(d)
    return items
