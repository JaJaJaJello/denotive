"""Predict Shapes: one picture redrawn as editable DeNotive pieces, for DeNotive's own "Predict Shapes" command.

    python main.py shapes picture.png --out result.json [--engine ai --provider claude_app --model opus]
                                                         [--target x y w h]

The pieces come from Claude (or whichever AI DeNotive's Co-Investigator settings name) or from the built-in trained
model. If the AI can't be used (signed out, usage limit, no usable reply), the built-in model steps in. Either
way the exact lines, areas and frames are then traced from the picture's own pixels (refine.py). With --target
the pieces are placed over that rectangle of the slide (where the picture sits) instead of a 1280 x 720 page.

The result file: {"engine": what made the pieces, "fallback": why the AI wasn't used (or null),
"items": [DeNotive slide items], "count": n}. Picture pieces (a heatmap's image) are saved next to the result and
named by "file"; DeNotive adds them to its library. Progress goes to stdout as "STATUS ..." lines.
"""
import base64
import io
import json
import re
import sys
import time
from pathlib import Path

from PIL import Image

from . import tokens as T

SYSTEM = """You turn a picture of a figure into editable vector pieces for a slide editor.

The picture is a 1280 x 720 pixel slide. Positions are pixels on it: x to the right, y down.
Reply with only a JSON array of pieces, in painting order (things behind come first). Each piece is one of:

{"t":"shape","kind":K,"x0":..,"y0":..,"x1":..,"y1":..,"fill":C or null,"stroke":C or null,"sw":W,"size":S,"text":""}
    A box. K is one of rect, round, ellipse, triangle, diamond, hexagon, parallelogram, star, arrow, chevron,
    callout, cross. "text" is any words written inside it, "size" their size.
{"t":"line","x0":..,"y0":..,"x1":..,"y1":..,"color":C,"sw":W,"dash":"solid" or "dash" or "dot","a1":bool,"a2":bool}
    A straight line: axes, tick marks, frame sides, legend samples, arrows. a1/a2: an arrowhead at the start/end.
{"t":"text","x0":..,"y0":..,"x1":..,"y1":..,"color":C,"size":S,"bold":bool,"rot":0 or 90 or 270,"text":"..."}
    One line of text. The box is what you see (a rotated label's box is tall and thin; 90 reads bottom to top).
    Write subscripts, superscripts and Greek letters as Unicode: Li₂W₂O₇, cm⁻¹, 2θ, λ, °C.
{"t":"path","stroke":C or null,"fill":C or null,"sw":W,"dash":"solid" or "dash" or "dot","closed":bool,"pts":[[x,y],...]}
    A polyline or polygon: plot lines (open, stroked), filled areas and bands (closed, filled). Use as many points
    as the shape needs: a plot line with sharp peaks needs a point at the top and the foot of every peak.
{"t":"image","x0":..,"y0":..,"x1":..,"y1":..}
    A region that can't be drawn with shapes (a heatmap's image, a photo, a colour bar). It will be cut out of the
    picture, so only give where it is.

C is a colour "#rrggbb". W is a line width in px. S is a text size in px (capital letters are about 0.7 S tall).
Positions will be compared with the picture pixel by pixel, so be as exact as you can. Give every label, tick
mark and tick number. Reply with the JSON array only."""


def status(msg):
    print("STATUS " + msg, flush=True)


# ---------------------------------------------------------------------- an AI's reply -> specs
def _colour(c, default=None):
    if not isinstance(c, str):
        return default
    c = c.strip()
    if re.fullmatch(r"#[0-9a-fA-F]{6}", c):
        return c.lower()
    if re.fullmatch(r"#[0-9a-fA-F]{3}", c):
        return "#" + "".join(ch * 2 for ch in c[1:]).lower()
    return default


def _num(v, default):
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def parse(text):
    """The JSON array in a reply (a cut-off reply is closed after its last whole piece)."""
    t = re.sub(r"```(?:json)?", "", text or "")
    a = t.find("[")
    if a < 0:
        return []
    for cand in (t[a:t.rfind("]") + 1], t[a:t.rfind("}") + 1] + "]"):
        try:
            v = json.loads(cand)
            return v if isinstance(v, list) else []
        except ValueError:
            continue
    return []


def normalise(items):
    """Pieces as the rest of the vectorizer writes them, with anything unreadable left out."""
    out = []
    for s in items:
        if not isinstance(s, dict):
            continue
        t = s.get("t")
        try:
            if t in ("shape", "text", "image"):
                x0, y0, x1, y1 = (float(s[k]) for k in ("x0", "y0", "x1", "y1"))
                x0, x1 = min(x0, x1), max(x0, x1)
                y0, y1 = min(y0, y1), max(y0, y1)
            if t == "shape":
                out.append({"t": "shape", "kind": s.get("kind") if s.get("kind") in T.SHAPES else "rect",
                            "x0": x0, "y0": y0, "x1": x1, "y1": y1, "fill": _colour(s.get("fill")), "grad": None,
                            "stroke": _colour(s.get("stroke")), "sw": _num(s.get("sw"), 1.0),
                            "size": _num(s.get("size"), 20.0), "text": str(s.get("text") or "")})
            elif t == "text":
                # the prompt counts turns the mathematical way (90 = reads bottom to top); DeNotive turns clockwise
                r = int(_num(s.get("rot"), 0)) % 360
                rot = {90: 270, 270: 90}.get(r, 0)
                out.append({"t": "text", "x0": x0, "y0": y0, "x1": x1, "y1": y1,
                            "color": _colour(s.get("color"), "#000000"), "size": max(6.0, _num(s.get("size"), 20.0)),
                            "bold": bool(s.get("bold")), "rot": rot, "text": str(s.get("text") or "")})
            elif t == "line":
                out.append({"t": "line", **{k: float(s[k]) for k in ("x0", "y0", "x1", "y1")},
                            "color": _colour(s.get("color"), "#000000"), "sw": _num(s.get("sw"), 1.0),
                            "dash": s.get("dash") if s.get("dash") in T.DASHES else "solid",
                            "a1": bool(s.get("a1")), "a2": bool(s.get("a2"))})
            elif t == "path":
                pts = [(float(p[0]), float(p[1])) for p in s.get("pts") or []
                       if isinstance(p, (list, tuple)) and len(p) >= 2]
                if len(pts) >= 2:
                    out.append({"t": "path", "stroke": _colour(s.get("stroke")), "fill": _colour(s.get("fill")),
                                "grad": None, "sw": _num(s.get("sw"), 1.0),
                                "dash": s.get("dash") if s.get("dash") in T.DASHES else "solid",
                                "closed": bool(s.get("closed")), "pts": pts})
            elif t == "image":
                out.append({"t": "image", "x0": x0, "y0": y0, "x1": x1, "y1": y1})
        except (KeyError, TypeError, ValueError):
            continue
    return out


def ai_config(provider, model):
    """DeNotive's settings for that provider (its address, key), with the chosen model. The picture always goes."""
    from denotive.ai.providers import role_config
    for role in ("coinv", "notes"):
        cfg = role_config(role)
        if cfg.get("provider") == provider:
            break
    else:
        cfg = {"provider": provider, "url": ""}
    return dict(cfg, provider=provider, model=model or cfg.get("model", ""), images=True)


def ask_ai(source, provider, model, timeout=360):
    """Specs from the AI, or raises with the reason it couldn't give any."""
    from denotive.ai.providers import make
    cfg = ai_config(provider, model)
    prov = make(cfg)
    buf = io.BytesIO()
    source.resize((1280, 720), Image.LANCZOS).save(buf, "PNG")
    picture = base64.b64encode(buf.getvalue()).decode()
    started = time.time()
    text = prov.stream(SYSTEM, [{"role": "user", "text": "Here's the figure. Write its pieces.",
                                 "images": [("image/png", picture)]}],
                       lambda piece: None, lambda: time.time() - started > timeout, max_tokens=16000)
    if time.time() - started > timeout:
        raise RuntimeError("it took too long to answer")
    specs = normalise(parse(text))
    if not specs:
        raise RuntimeError("its reply had no pieces in it")
    return specs


def why(e):
    """A short reason for the note DeNotive shows."""
    from denotive.ai.providers import NeedsLogin
    msg = str(e)
    low = msg.lower()
    if isinstance(e, NeedsLogin) or "sign in" in low or "log in" in low or "login" in low or "authenticate" in low:
        return "it isn't signed in"
    if "limit" in low or "429" in low or "credit" in low or "quota" in low:
        return "its usage limit was reached"
    first = msg.split("\n")[0].strip().rstrip(".")
    if first[:1].isupper() and not first[:2].isupper():
        first = first[0].lower() + first[1:]
    return first[:160] or type(e).__name__


# ---------------------------------------------------------------------- text sizes from the picture
def _turn_right_way(source, specs, ocr):
    """Which way each vertical label reads: OCR reads it turned both ways, and the reading closer to the AI's
    words wins (AIs often mix up bottom-to-top and top-to-bottom)."""
    import difflib
    k = source.width / 1280.0
    idx = [i for i, s in enumerate(specs) if s["t"] == "text" and int(s.get("rot") or 0) % 360 in (90, 270)
           and len(re.sub(r"\W", "", s.get("text") or "")) >= 3]
    if not idx:
        return specs
    blank = Image.new("RGB", (32, 32), "white")
    imgs = [ocr._upright(source, dict(specs[i], rot=r), k) or blank for i in idx for r in (270, 90)]
    try:
        found = ocr.run_ocr(imgs)
    except Exception:  # noqa: BLE001  (directions stay as the AI gave them)
        return specs
    out = list(specs)
    for n, i in enumerate(idx):
        want = re.sub(r"\s+", " ", specs[i]["text"]).strip().lower()
        score = [difflib.SequenceMatcher(None, want, " ".join(w[0] for w in found[2 * n + j]).lower()).ratio()
                 for j in (0, 1)]
        best = 270 if score[0] >= score[1] else 90
        if max(score) >= 0.5 and abs(score[0] - score[1]) >= 0.2 and best != int(specs[i].get("rot") or 0):
            out[i] = dict(specs[i], rot=best)
    return out


def fit_text(source, specs):
    """An AI places labels well but tends to make them too small: OCR measures how big each one really is and
    where it sits (the AI's words stay)."""
    from . import ocr
    if not ocr.available():
        return specs
    specs = _turn_right_way(source, specs, ocr)
    k = source.width / 1280.0
    up = max(1.0, min(4.0, 2400.0 / source.width))
    whole = source.convert("RGB")
    if up > 1.05:
        whole = whole.resize((round(source.width * up), round(source.height * up)), Image.LANCZOS)
    try:
        found = ocr.run_ocr([whole])[0]
    except Exception:  # noqa: BLE001  (sizes stay as the AI gave them)
        return specs
    f = 1.0 / (k * up)
    words = [{"x0": a * f, "y0": b * f, "x1": c * f, "y1": d * f, "used": False} for _, a, b, c, d, _ in found]
    out = []
    for s in specs:
        if s["t"] != "text" or int(s.get("rot") or 0) % 360:
            out.append(s)
            continue
        h = s["y1"] - s["y0"]
        px, py = 0.3 * h, 0.6 * h
        mine = [w for w in words if not w["used"] and s["x0"] - px <= (w["x0"] + w["x1"]) / 2 <= s["x1"] + px
                and s["y0"] - py <= (w["y0"] + w["y1"]) / 2 <= s["y1"] + py]
        if not mine:
            out.append(s)
            continue
        x0, y0 = min(w["x0"] for w in mine), min(w["y0"] for w in mine)
        x1, y1 = max(w["x1"] for w in mine), max(w["y1"] for w in mine)
        size = max(8.0, (y1 - y0) * 1.05)
        if not 0.6 <= size / max(1.0, float(s.get("size", 20))) <= 2.5:
            out.append(s)
            continue
        for w in mine:
            w["used"] = True
        out.append(dict(s, x0=x0 - 2, y0=y0 - 0.25 * size, x1=max(x1 + 2, x0 - 2 + (s["x1"] - s["x0"])),
                        y1=y1 + 0.1 * size, size=size))
    return out


# ---------------------------------------------------------------------- placing the pieces where the picture is
def place(items, src, dst):
    """DeNotive items moved and scaled from rectangle src (where the picture is on the 1280 x 720 page) onto dst
    (where it is on the slide). Both are (x, y, w, h)."""
    sx, sy = dst[2] / max(1e-6, src[2]), dst[3] / max(1e-6, src[3])
    s = (sx + sy) / 2

    def X(v):
        return dst[0] + (v - src[0]) * sx

    def Y(v):
        return dst[1] + (v - src[1]) * sy
    out = []
    for d in items:
        d = dict(d)
        if d.get("type") == "line":
            ox, oy = d.get("x", 0), d.get("y", 0)
            d.update(x=0, y=0, x1=X(ox + d["x1"]), y1=Y(oy + d["y1"]), x2=X(ox + d["x2"]), y2=Y(oy + d["y2"]))
        else:
            d.update(x=X(d["x"]), y=Y(d["y"]), w=d["w"] * sx, h=d["h"] * sy)
            if "minh" in d:
                d["minh"] = d["minh"] * sy
        for key in ("sw", "size", "radius"):
            if isinstance(d.get(key), (int, float)):
                d[key] = d[key] * s
        out.append(d)
    return out


# ---------------------------------------------------------------------- the command
def main(argv=None):
    import argparse
    ap = argparse.ArgumentParser(prog="main.py shapes", description=__doc__.splitlines()[0])
    ap.add_argument("image")
    ap.add_argument("--out", required=True, help="where to write the result (JSON)")
    ap.add_argument("--engine", choices=("builtin", "ai"), default="builtin")
    ap.add_argument("--provider", default="claude_app")
    ap.add_argument("--model", default="")
    ap.add_argument("--target", type=float, nargs=4, metavar=("X", "Y", "W", "H"),
                    help="the picture's rectangle on the slide (default: a whole 1280 x 720 page)")
    ap.add_argument("--ckpt", default=None)
    a = ap.parse_args(argv)

    from .degrade import letterbox
    from .predict import full_res_slide, picture_items
    from .refine import refine
    from .render import ensure_app, to_items
    ensure_app()
    out_path = Path(a.out)
    out_dir = out_path.parent
    out_dir.mkdir(parents=True, exist_ok=True)
    original = Image.open(a.image).convert("RGB")
    source = full_res_slide(original)
    _, scale, ox, oy = letterbox(original, source.size)
    k = source.width / 1280.0
    picture = (ox / k, oy / k, original.width * scale / k, original.height * scale / k)  # on the 1280 x 720 page

    specs, engine, fallback = None, "", None
    if a.engine == "ai":
        from denotive.ai.providers import SHORT_NAMES
        engine = f"{SHORT_NAMES.get(a.provider, a.provider)} · {a.model or 'default'}"
        status(f"Asking {engine}…")
        try:
            specs = ask_ai(source, a.provider, a.model)
            status("Measuring the labels…")
            specs = fit_text(source, specs)
        except Exception as e:  # noqa: BLE001  (any failure: the built-in model steps in)
            fallback = f"{engine} couldn't be used ({why(e)})"
            status(fallback + ", so the built-in model is reading it…")
            specs = None
    if specs is None:
        import torch  # noqa: F401
        from . import ocr
        from .model import pick_device
        from .predict import load, predict
        from .train import HERE
        engine = "built-in model"
        status("Reading the picture with the built-in model…")
        device = pick_device("auto")
        model, size, _ = load(Path(a.ckpt) if a.ckpt else HERE / "runs" / "default" / "best.pt", device)
        _, _, specs, _ = predict(model, size, a.image, device)
        status("Reading the words…")
        specs = ocr.apply(source, specs, log=lambda *x: None)
    status("Tracing the exact shapes from the picture…")
    specs = refine(source, specs, log=lambda *x: None)
    stem = f"shapes_{int(time.time())}"
    pics, _, _ = picture_items(specs, source, stem, out_dir, None, True, True)
    items = sorted(to_items(specs) + pics, key=lambda d: d["z"])
    if a.target:
        items = place(items, picture, a.target)
    out_path.write_text(json.dumps({"engine": engine, "fallback": fallback, "items": items, "count": len(items)},
                                   ensure_ascii=False), encoding="utf-8")
    status(f"Done: {len(items)} pieces")


if __name__ == "__main__":
    main(sys.argv[1:])
