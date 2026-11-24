"""Reads the words in a figure with Windows' built-in OCR engine and puts them in the model's text boxes.

The model is good at where text is (box, size, colour, which way it reads) but can't make out the letters at
the size it sees pictures. OCR is the opposite: it reads letters well but knows nothing about the figure. So:
  1. OCR reads the whole picture once (enlarged, so small tick labels are legible) and reports every word with
     its position.
  2. Each word goes to the model's text box (or shape) that holds it. Smaller boxes choose first, and a box only
     reaches one line of text tall, so one oversized box can't swallow every label around it.
  3. Rotated labels (y axes) are cut out, turned upright and read on their own.
  4. Text boxes with no words in them are dropped (they were guesses); words no box claimed become new text
     pieces, so labels the model missed still come in.
Windows' OCR is the same engine as the Snipping Tool's text actions; one PowerShell call reads everything.
"""
import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path

import numpy as np
from PIL import Image, ImageOps

SCRIPT = Path(__file__).with_name("winocr.ps1")
CANVAS_W = 1280.0


ELEMENTS = set("""H He Li Be B C N O F Ne Na Mg Al Si P S Cl Ar K Ca Sc Ti V Cr Mn Fe Co Ni Cu Zn Ga Ge As Se Br Kr
Rb Sr Y Zr Nb Mo Tc Ru Rh Pd Ag Cd In Sn Sb Te I Xe Cs Ba La Ce Pr Nd Pm Sm Eu Gd Tb Dy Ho Er Tm Yb Lu Hf Ta W Re Os
Ir Pt Au Hg Tl Pb Bi Po At Rn Fr Ra Ac Th Pa U Np Pu Am Cm Bk Cf Es Fm Md No Lr Rf Db Sg Bh Hs Mt Ds Rg Cn Nh Fl Mc
Lv Ts Og""".split())
SUB = str.maketrans("0123456789", "₀₁₂₃₄₅₆₇₈₉")
MOLECULES = {("H", "2"), ("N", "2"), ("O", "2"), ("O", "3"), ("F", "2"), ("Cl", "2"), ("Br", "2"), ("I", "2"),
             ("S", "8"), ("P", "4"), ("C", "60"), ("C", "70")}
_PART = re.compile(r"([A-Z][a-z]?)((?:[1-9]\d*|0)(?:\.\d+)?)?")


def _formula_parts(token):
    """[(element, count), ...] when the whole token reads as a chemical formula, else None."""
    pos, parts = 0, []
    while pos < len(token):
        m = _PART.match(token, pos)
        if not m or m.group(1) not in ELEMENTS or (m.group(2) or "").startswith("0"):
            return None
        parts.append((m.group(1), m.group(2) or ""))
        pos = m.end()
    return parts or None


def _formula(token):
    """A token OCR read from a chemical formula, put right: O for 0 where that makes a real formula (W03 -> WO3,
    Nb205 -> Nb2O5), W for VV, and the counts as subscripts (WO₃). Other tokens come back unchanged."""
    core = token.strip("()[],;:")
    if len(core) < 2 or not any(c.isupper() for c in core) or not any(c.isdigit() or c.isupper() for c in core[1:]):
        return token
    base = core.replace("VV", "W")
    plain = _formula_parts(base)
    if plain and len(plain) == 1 and plain[0] in MOLECULES:  # C60 is buckminsterfullerene, not C6O
        return token.replace(core, plain[0][0] + plain[0][1].translate(SUB))
    zeros = [i for i, c in enumerate(base) if c == "0"][:8]
    best = None
    for mask in range(1 << len(zeros)):
        chars = list(base)
        for b, i in enumerate(zeros):
            if mask >> b & 1:
                chars[i] = "O"
        parts = _formula_parts("".join(chars))
        if parts and (best is None or len(parts) > len(best)):
            best = parts
    if not best:
        return token
    if len(best) < 2:  # one element: only familiar molecules (O2, H2, Cl2, S8, C60), not codes like B28 or H65
        e, n = best[0]
        if (e, n) not in MOLECULES:
            return token
    fixed = "".join(e + n.translate(SUB) for e, n in best)
    return token.replace(core, fixed)


def tidy(text):
    """Common OCR slips in figure labels: chemical formulas, and °C read as "oc"."""
    words = [_formula(w) for w in text.split()]
    out = " ".join(words)
    out = re.sub(r"(?<=[,\s(])[oO0°][cC]\b", "°C", out)
    # diffraction labels: OCR reads 2θ as "29"/"20", ° as "o" and λ as "X"
    out = re.sub(r"\b2[90]\b(?=\s*,?\s*(?:\(\s*deg|\(\s*°|°|[oO]\b|\(\s*[XλA]\s*=))", "2θ", out)
    out = re.sub(r"(?<=2θ,)\s*[oO]\b", " °", out)
    out = re.sub(r"\b[XA]\s*=?\s*(?=[\d.]+\s*Å)", "λ = ", out)
    return out


def _score(text):
    """How much a reading looks like real text (letters and digits count, odd symbols count against)."""
    good = sum(c.isalnum() for c in text)
    odd = sum(1 for c in text if not (c.isalnum() or c in " ,.()%-+/°:;=[]"))
    return good - 3 * odd


def available():
    return sys.platform == "win32" and SCRIPT.exists()


def run_ocr(images):
    """OCR results for PIL pictures: [[(word, x0, y0, x1, y1, line number), ...], ...] in each picture's pixels."""
    with tempfile.TemporaryDirectory() as tmp:
        files = []
        for n, img in enumerate(images):
            path = Path(tmp) / f"p{n:04d}.png"
            img.save(path)
            files.append(str(path))
        lst = Path(tmp) / "files.txt"
        lst.write_text("\n".join(files), encoding="utf-8-sig")
        run = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File",
                              str(SCRIPT), "-List", str(lst)], capture_output=True, timeout=600,
                             creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    if run.returncode != 0:
        raise RuntimeError(run.stderr.decode("utf-8", "replace").strip()[:300] or "Windows OCR failed")
    try:
        found = json.loads(run.stdout.decode("utf-8-sig", "replace") or "[]")
    except ValueError as e:
        raise RuntimeError(f"unexpected OCR output: {e}")
    if isinstance(found, dict):
        found = [found]
    out = []
    for entry in found:
        words = []
        for li, line in enumerate(entry.get("lines") or []):
            for w in line.get("words") or []:
                if w.get("text"):
                    words.append((w["text"], w["x"], w["y"], w["x"] + w["w"], w["y"] + w["h"], li))
        out.append(words)
    return out


def _upright(source, s, k):
    """A rotated label cut out, turned to read left to right, padded and enlarged."""
    pad = 0.18 * (s["x1"] - s["x0"])
    box = (max(0, round((s["x0"] - pad) * k)), max(0, round((s["y0"] - pad) * k)),
           min(source.width, round((s["x1"] + pad) * k)), min(source.height, round((s["y1"] + pad) * k)))
    if box[2] - box[0] < 3 or box[3] - box[1] < 3:
        return None
    img = source.crop(box).convert("RGB").rotate(-90 if int(s.get("rot") or 0) % 360 == 270 else 90, expand=True)
    scale = max(1.0, min(8.0, 56.0 / max(1, img.height)))
    if scale > 1.01:
        img = img.resize((round(img.width * scale), round(img.height * scale)), Image.LANCZOS)
    return ImageOps.expand(img, border=24, fill=img.getpixel((0, 0)))


def _ink(source, k, box):
    """The colour of the letters in a box of the picture (whatever stands out from its background)."""
    x0, y0, x1, y1 = (round(v * k) for v in box)
    x0, y0 = max(0, x0), max(0, y0)
    x1, y1 = min(source.width, max(x0 + 1, x1)), min(source.height, max(y0 + 1, y1))
    px = list(source.crop((x0, y0, x1, y1)).convert("RGB").getdata())
    if not px:
        return "#000000"
    ring = sorted(px, key=lambda c: sum(c))[len(px) // 2]  # roughly the background
    far = [c for c in px if sum(abs(a - b) for a, b in zip(c, ring)) > 120]
    if not far:
        return "#000000"
    far.sort(key=lambda c: sum(abs(a - b) for a, b in zip(c, ring)))
    c = far[len(far) * 3 // 4]
    return "#{:02x}{:02x}{:02x}".format(*c)


def apply(source, specs, log=print):
    """The specs with the model's guessed words replaced by what OCR reads (see the module notes)."""
    if not available():
        log("  (Windows OCR isn't available here: keeping the model's own guesses at the text)")
        return specs
    k = source.width / CANVAS_W
    up = max(1.0, min(4.0, 2400.0 / source.width))  # enlarge small pictures so tick labels are legible
    whole = source.convert("RGB")
    if up > 1.05:
        whole = whole.resize((round(source.width * up), round(source.height * up)), Image.LANCZOS)
    W, H = whole.size
    cw = whole.rotate(-90, expand=True)  # labels reading bottom to top (y axes) come out upright
    ccw = whole.rotate(90, expand=True)  # labels reading top to bottom (colour bars)
    # OCR reads small areas better than one big busy picture: also read overlapping tiles, each enlarged
    src = source.convert("RGB")
    nx, ny = (3 if src.width > 1.2 * src.height else 2), 2
    tw, th = src.width / nx, src.height / ny
    tiles = []
    for ty in range(ny):
        for tx in range(nx):
            x0, y0 = max(0, int(tx * tw - 0.15 * tw)), max(0, int(ty * th - 0.15 * th))
            x1, y1 = min(src.width, int((tx + 1) * tw + 0.15 * tw)), min(src.height, int((ty + 1) * th + 0.15 * th))
            ts = max(1.0, min(4.0, 1600.0 / max(1, x1 - x0)))
            tile = src.crop((x0, y0, x1, y1))
            if ts > 1.05:
                tile = tile.resize((round(tile.width * ts), round(tile.height * ts)), Image.LANCZOS)
            tiles.append((tile, x0, y0, ts))
    try:
        found = run_ocr([whole, cw, ccw] + [t[0] for t in tiles])
    except (RuntimeError, OSError, subprocess.TimeoutExpired) as e:
        log(f"  (Windows OCR didn't run: {e}; keeping the model's own guesses at the text)")
        return specs
    f = 1.0 / (k * up)  # OCR pixels to slide units
    words = [{"t": t, "x0": a * f, "y0": b * f, "x1": c * f, "y1": d * f, "used": False}
             for t, a, b, c, d, li in found[0]]
    for (tile, ox, oy, ts), ws in zip(tiles, found[3:]):
        words += [{"t": t, "x0": (a / ts + ox) / k, "y0": (b / ts + oy) / k, "x1": (c / ts + ox) / k,
                   "y1": (d / ts + oy) / k, "used": False} for t, a, b, c, d, li in ws]
    pictures = [s for s in specs if s["t"] == "image"]
    # vertical labels: runs of words from the two turned readings, mapped back onto the picture
    vertical = []
    for words_v, rot in ((found[1], 270), (found[2], 90)):
        lines = {}
        for t, a, b, c, d, li in words_v:
            lines.setdefault(li, []).append((t, a, b, c, d))
        for ws in lines.values():
            ws.sort(key=lambda w: w[1])
            text = " ".join(w[0] for w in ws)
            a, b = min(w[1] for w in ws), min(w[2] for w in ws)
            c, d = max(w[3] for w in ws), max(w[4] for w in ws)
            if rot == 270:  # turned clockwise: (x, y) here was (y, H - x) on the picture
                x0, x1, y0, y1 = b, d, H - c, H - a
            else:  # turned anticlockwise: (x, y) here was (W - y, x)
                x0, x1, y0, y1 = W - d, W - b, a, c
            vertical.append({"t": text, "rot": rot, "x0": x0 * f, "y0": y0 * f, "x1": x1 * f, "y1": y1 * f,
                             "score": _score(text), "used": False})
    # read the wrong way up, a label gives worse text than the right way: keep the better reading of each spot
    vertical.sort(key=lambda v: -v["score"])
    kept = []
    for v in vertical:
        if v["score"] < 3 or len(v["t"].replace(" ", "")) < 2:
            continue
        overlap = any(min(v["x1"], o["x1"]) > max(v["x0"], o["x0"]) and min(v["y1"], o["y1"]) > max(v["y0"], o["y0"])
                      for o in kept)
        tall = (v["y1"] - v["y0"]) > 1.5 * (v["x1"] - v["x0"])
        if not overlap and tall:
            kept.append(v)
    vertical = kept

    def overlap(a, b):
        w = min(a["x1"], b["x1"]) - max(a["x0"], b["x0"])
        h = min(a["y1"], b["y1"]) - max(a["y0"], b["y0"])
        if w <= 0 or h <= 0:
            return 0.0
        small = min((a["x1"] - a["x0"]) * (a["y1"] - a["y0"]), (b["x1"] - b["x0"]) * (b["y1"] - b["y0"]))
        return w * h / max(1e-6, small)

    # the same words read several times (whole picture, tiles): keep the best reading of each spot
    words.sort(key=lambda w: -_score(w["t"]))
    merged = []
    for w in words:
        plain_mark = re.fullmatch(r"[=%+\-/:;,.°()\[\]<>~±×]+", w["t"]) is not None  # "=", "%", "(": keep
        if (_score(w["t"]) < 1 and not plain_mark) or any(overlap(w, o) > 0.5 for o in merged):
            continue
        if any(overlap(w, v) > 0.5 for v in vertical):
            continue  # part of a vertical label, misread sideways
        merged.append(w)
    words = sorted(merged, key=lambda w: ((w["y0"] + w["y1"]) / 2, w["x0"]))
    line, last, last_h = -1, None, 0.0
    for w in words:  # lines by position: words whose middles sit at about the same height
        cy, h = (w["y0"] + w["y1"]) / 2, w["y1"] - w["y0"]
        if last is None or abs(cy - last) > 0.5 * max(h, last_h):  # a small "o" or "°" stays on its line
            line += 1
            last, last_h = cy, h
        w["line"] = line

    def inside_picture(w):
        cx, cy = (w["x0"] + w["x1"]) / 2, (w["y0"] + w["y1"]) / 2
        return any(p["x0"] <= cx <= p["x1"] and p["y0"] <= cy <= p["y1"] for p in pictures)

    # horizontal text boxes and shapes claim the words inside them, smaller boxes first
    def region(s):
        if s["t"] == "shape":
            return s["x0"], s["y0"], s["x1"], s["y1"]
        line_h = 1.3 * float(s.get("size", 20))  # a label is one line: don't let a tall box reach further
        pad = 0.3 * line_h
        return s["x0"] - pad, s["y0"] - pad, s["x1"] + pad, s["y0"] + min(s["y1"] - s["y0"], line_h) + pad

    rotated = {i for i, s in enumerate(specs) if s["t"] == "text" and int(s.get("rot") or 0) % 360 in (90, 270)}
    claimers = [i for i, s in enumerate(specs) if (s["t"] == "text" and i not in rotated) or
                (s["t"] == "shape" and (s.get("text") or "").strip())]
    claimers.sort(key=lambda i: (region(specs[i])[2] - region(specs[i])[0]) * (region(specs[i])[3] - region(specs[i])[1]))
    text_of, at = {}, {}
    for i in claimers:
        x0, y0, x1, y1 = region(specs[i])
        mine = [w for w in words if not w["used"] and x0 <= (w["x0"] + w["x1"]) / 2 <= x1
                and y0 <= (w["y0"] + w["y1"]) / 2 <= y1]
        if len(mine) > 1:  # keep the run of words nearest where the box starts; the rest stay for others
            mine.sort(key=lambda w: (w["line"], w["x0"]))
            runs, cur = [], [mine[0]]
            for w in mine[1:]:
                h = max(1.0, w["y1"] - w["y0"], cur[-1]["y1"] - cur[-1]["y0"])
                if w["line"] == cur[-1]["line"] and w["x0"] - cur[-1]["x1"] < 1.5 * h:
                    cur.append(w)
                else:
                    runs.append(cur)
                    cur = [w]
            runs.append(cur)
            sx, sy = specs[i]["x0"], specs[i]["y0"]
            mine = min(runs, key=lambda r: abs(r[0]["x0"] - sx) + abs(r[0]["y0"] - sy))
        if mine and specs[i]["t"] == "text":
            line_h = 1.3 * float(specs[i].get("size", 20))
            if abs(mine[0]["x0"] - specs[i]["x0"]) + abs(mine[0]["y0"] - specs[i]["y0"]) > 4 * line_h:
                mine = []  # the words are far from where this box starts: not its words
        if mine and specs[i]["t"] == "text" and len({w["line"] for w in mine}) == 1:
            # a label is the whole run of words on its line, even where the model's box stops short (or a
            # smaller box would otherwise take a word out of its middle)
            same = sorted([w for w in words if w["line"] == mine[0]["line"] and (not w["used"] or w in mine)],
                          key=lambda w: w["x0"])
            ids = [next(j for j, o in enumerate(same) if o is w) for w in mine]
            lo, hi = min(ids), max(ids)

            def close(a, b):
                return b["x0"] - a["x1"] < 1.5 * max(1.0, a["y1"] - a["y0"], b["y1"] - b["y0"])
            while lo > 0 and close(same[lo - 1], same[lo]):
                lo -= 1
            while hi < len(same) - 1 and close(same[hi], same[hi + 1]):
                hi += 1
            mine = same[lo:hi + 1]
        for w in mine:
            w["used"] = True
        mine.sort(key=lambda w: (w["line"], w["x0"]))
        text_of[i] = " ".join(w["t"] for w in mine)
        if mine and specs[i]["t"] == "text":  # put the label where OCR saw it
            at[i] = (min(w["x0"] for w in mine), min(w["y0"] for w in mine), max(w["x1"] for w in mine),
                     max(w["y1"] for w in mine))
    turned = {}
    for i in rotated:  # the model's rotated labels take the vertical reading they sit on (and its direction)
        s = specs[i]
        cx = (s["x0"] + s["x1"]) / 2
        pad = 0.6 * float(s.get("size", 20))
        hits = [v for v in vertical if not v["used"] and v["x0"] - pad <= cx <= v["x1"] + pad
                and min(s["y1"], v["y1"]) > max(s["y0"], v["y0"])]
        if hits:
            v = max(hits, key=lambda v: v["score"])
            v["used"] = True
            text_of[i], turned[i] = v["t"], v
    number = r"[−\-–]?\d+(?:[.,]\d+)?%?"
    read_numbers = [w for w in words if re.fullmatch(number, w["t"])]

    def value(t):
        try:
            return float(t.replace("−", "-").replace("–", "-").replace(",", ".").rstrip("%"))
        except ValueError:
            return None

    def fits(pos, vals, at, v):
        """Does value v at position `at` belong on the axis whose numbers sit at pos with values vals? Ticks are
        evenly spaced (or evenly spaced in powers of ten), so the axis predicts what belongs anywhere on it."""
        pos, vals = np.array(pos, float), np.array(vals, float)
        if len(set(pos.round(1))) < 2 or len(set(vals)) < 2:
            return False
        for logged in (False, True):
            if logged:
                if (vals <= 0).any() or v <= 0:
                    return False
                vals, v = np.log10(vals), np.log10(v)
            a, b = np.polyfit(pos, vals, 1)
            step = np.median(np.diff(np.sort(np.unique(vals)))) if len(set(vals)) > 1 else abs(vals[0]) or 1
            if np.abs(a * pos + b - vals).max() <= 0.15 * step:  # the read numbers sit on this scale
                return abs(a * at + b - v) <= 0.3 * step
        return False

    def tick_like(g):
        """A tick number OCR missed lines up with at least two numbers it did read, in the same row (an x axis)
        or the same column (a y axis), and has the value that belongs at its place on that axis. It doesn't sit
        on top of anything OCR read either."""
        v = value(g["text"].strip())
        if v is None:
            return False
        sz = float(g.get("size", 20))
        cx, cy = (g["x0"] + g["x1"]) / 2, (g["y0"] + g["y1"]) / 2
        if any(min(g["x1"], w["x1"]) > max(g["x0"], w["x0"]) and min(g["y1"], w["y1"]) > max(g["y0"], w["y0"])
               for w in words):
            return False
        row = [w for w in read_numbers if abs((w["y0"] + w["y1"]) / 2 - cy) < 0.6 * sz and value(w["t"]) is not None]
        col = [w for w in read_numbers if (abs((w["x0"] + w["x1"]) / 2 - cx) < sz or abs(w["x1"] - g["x1"]) < sz)
               and value(w["t"]) is not None]
        if len(row) >= 2 and fits([(w["x0"] + w["x1"]) / 2 for w in row], [value(w["t"]) for w in row], cx, v):
            return True
        return len(col) >= 2 and fits([(w["y0"] + w["y1"]) / 2 for w in col], [value(w["t"]) for w in col], cy, v)

    out, dropped = [], 0
    for i, s in enumerate(specs):
        if s["t"] == "text":
            t = text_of.get(i, "")
            if not t and re.fullmatch(r"[−\-0-9.,]+", (s.get("text") or "").strip()):
                # OCR couldn't make it out, but tick numbers are predictable: keep the model's guess, in a box
                # sized to the number (the model's own box can be far too big), if it lines up with the
                # numbers OCR did read
                sz = float(s.get("size", 20))
                n = len(s["text"].strip())
                if int(s.get("rot") or 0) % 360 in (90, 270):
                    s = dict(s, x1=min(s["x1"], s["x0"] + 1.4 * sz), y1=min(s["y1"], s["y0"] + 0.7 * sz * n + 6))
                else:
                    s = dict(s, x1=min(s["x1"], s["x0"] + 0.7 * sz * n + 6), y1=min(s["y1"], s["y0"] + 1.4 * sz))
                if tick_like(s):
                    out.append(s)
                else:
                    dropped += 1
                continue
            if not t:
                dropped += 1
                continue
            s = dict(s, text=tidy(t)[:200])
            if i in at:
                x0, y0, x1, y1 = at[i]
                ocr_size = max(8.0, (y1 - y0) * 1.05)
                size = float(s.get("size", ocr_size))
                if not 0.6 * ocr_size <= size <= 1.6 * ocr_size:
                    size = ocr_size
                s.update(x0=x0 - 2, y0=y0 - 0.25 * size, x1=x1 + 2, y1=y1 + 0.1 * size, size=size)
            if i in turned:  # trust where OCR found it and which way it reads
                v = turned[i]
                s.update(rot=v["rot"], x0=v["x0"] - 2, y0=v["y0"] - 2, x1=v["x1"] + 2, y1=v["y1"] + 2)
        elif s["t"] == "shape" and i in text_of:
            s = dict(s, text=tidy(text_of[i])[:200])
        out.append(s)
    # words nobody claimed: new text pieces, one per run of words close together on one line
    added = 0
    left = [w for w in words if not w["used"] and not inside_picture(w)]
    left.sort(key=lambda w: (w["line"], w["x0"]))
    runs = []
    for w in left:
        r = runs[-1] if runs else None
        h = max(w["y1"] - w["y0"], (r[-1]["y1"] - r[-1]["y0"]) if r else 0.0)  # the taller of the two words
        if r and r[-1]["line"] == w["line"] and w["x0"] - r[-1]["x1"] < 1.5 * h:
            r.append(w)
        else:
            runs.append([w])
    for r in runs:
        x0, y0 = min(w["x0"] for w in r), min(w["y0"] for w in r)
        x1, y1 = max(w["x1"] for w in r), max(w["y1"] for w in r)
        size = max(8.0, (y1 - y0) * 1.05)
        out.append({"t": "text", "x0": x0 - 2, "y0": y0 - 0.25 * size, "x1": x1 + 2, "y1": y1 + 0.1 * size,
                    "color": _ink(source, k, (x0, y0, x1, y1)), "size": size, "bold": False, "rot": 0,
                    "text": tidy(" ".join(w["t"] for w in r))[:200]})
        added += 1
    for v in vertical:  # vertical labels the model missed
        if v["used"] or any(p["x0"] <= (v["x0"] + v["x1"]) / 2 <= p["x1"] and p["y0"] <= (v["y0"] + v["y1"]) / 2
                            <= p["y1"] for p in pictures):
            continue
        size = max(8.0, (v["x1"] - v["x0"]) * 1.05)
        out.append({"t": "text", "x0": v["x0"] - 2, "y0": v["y0"] - 2, "x1": v["x1"] + 2, "y1": v["y1"] + 2,
                    "color": _ink(source, k, (v["x0"], v["y0"], v["x1"], v["y1"])), "size": size, "bold": False,
                    "rot": v["rot"], "text": tidy(v["t"])[:200]})
        added += 1
    log(f"  OCR read {len(words)} words: {len([t for t in text_of.values() if t])} of the model's text boxes"
        f" filled, {dropped} empty ones dropped, {added} labels added that the model missed")
    return out
