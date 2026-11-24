"""Turns a picture of a figure into DeNotive pieces with a trained model.

    python main.py predict figure.png                 # writes figure_pieces.json and figure_rebuilt.png
    python main.py predict figure.png --clipboard     # then press Ctrl+V in DeNotive's evidence editor

The picture is fitted into a 16:9 slide (white margins), like the training figures. The pieces land on a
1280 x 720 page, the evidence editor's default. Where the model finds a picture region (a heatmap, a colour
bar, a photo), that part is cut out of your picture at its full resolution. Labels, arrows and boxes the model
found on top of it are erased from the cut-out (they're separate pieces already). If the region is a few solid
colours (stacked areas, bars) it's traced into one filled shape per colour; otherwise it becomes a picture.
Windows OCR reads the words (ocr.py), and the exact plot lines, filled areas and frames are taken from the
picture's own pixels (refine.py), with the model saying what each piece is.
"""
import json
import sys
from pathlib import Path

import torch
from PIL import Image

from . import tokens as T
from .degrade import letterbox
from .model import Vectorizer, pick_device
from .train import HERE, to_tensor

SLIDE_MIME = "application/x-denotive-slide-items"  # what DeNotive's evidence editor copies and pastes


def load(ckpt, device):
    st = torch.load(ckpt, map_location=device, weights_only=False)
    m = Vectorizer(st["preset"]).to(device)
    m.load_state_dict(st["model"])
    m.eval()
    return m, tuple(st.get("size") or (640, 360)), st.get("step", 0)


def full_res_slide(img):
    """The picture fitted into a 16:9 canvas without shrinking it, so cut-outs keep every pixel."""
    w, h = img.size
    W, H = (w, round(w * 9 / 16)) if w / h >= 16 / 9 else (round(h * 16 / 9), h)
    return letterbox(img, (W, H))[0]


def predict(model, size, image, device, temperature=0.0):
    original = Image.open(image).convert("RGB")
    boxed, *_ = letterbox(original, size)
    seq = model.write(to_tensor(boxed)[None].to(device), temperature=temperature)[0]
    return boxed, full_res_slide(original), T.decode(seq), seq


def library_store():
    """DeNotive's own library (the one in its settings), so pasted pictures are really there."""
    from denotive import context, settings
    try:
        lib = json.loads(settings.CONFIG_FILE.read_text(encoding="utf-8")).get("library")
    except (OSError, ValueError):
        lib = None
    return context.open_library(Path(lib or settings.DEFAULT_LIBRARY))


def picture_items(specs, source, stem, out, store=None, clean=True, trace=True):
    """DeNotive items for the picture pieces, cut from `source` with whatever the model found on top of them
    erased. Solid-colour regions are traced into filled shapes (grouped); the rest stay pictures, saved as PNGs
    next to the results and, with a library store, added there so they paste into DeNotive.
    Returns (items, pictures, traced) where pictures = [(z, slide box, PIL picture)] for the preview."""
    from . import cleanup
    from .render import pil_to_qimage
    items, shown, traced, n = [], [], 0, 0
    k = source.width / cleanup.CANVAS_W
    for z, s in enumerate(specs, start=1):
        if s["t"] != "image":
            continue
        box = cleanup.pixel_box(source, s)
        if box is None:
            continue
        pic = source.crop(box)
        if clean:
            pic = cleanup.erase_overlays(pic, box, k, specs, z)
        regions = cleanup.trace_flat(pic) if trace else None
        if regions:
            items += cleanup.region_items(regions, box, k, z)
            traced += len(regions)
            continue
        n += 1
        path = out / f"{stem}_picture{n}.png"
        pic.save(path)
        x0, y0, x1, y1 = (v / k for v in box)
        d = {"type": "image", "x": x0, "y": y0, "w": x1 - x0, "h": y1 - y0, "z": z, "id": f"v{z}",
             "iw": pic.width, "ih": pic.height, "crop": [0, 0, pic.width, pic.height], "name": path.name,
             "file": str(path)}
        if store is not None:
            from denotive import media
            d["asset"] = media.import_qimage(store, pil_to_qimage(pic), path.name)[0]
        items.append(d)
        shown.append((z, (x0, y0, x1, y1), pic))
    return items, shown, traced


def preview(items, pictures, width=960):
    """The final pieces drawn by DeNotive, exactly as they'd paste (pictures from their cleaned cut-outs)."""
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QPixmap, QTransform
    from PySide6.QtWidgets import QGraphicsPixmapItem
    from denotive.slide.scene import SlideScene
    from .render import doc_of, pil_to_qimage, qimage_to_pil
    sc = SlideScene(doc_of([dict(d) for d in items if d["type"] != "image"]))
    for z, (x0, y0, x1, y1), pic in pictures:
        q = pil_to_qimage(pic)
        it = QGraphicsPixmapItem(QPixmap.fromImage(q))
        it.setTransformationMode(Qt.SmoothTransformation)
        it.setTransform(QTransform.fromScale((x1 - x0) / q.width(), (y1 - y0) / q.height()))
        it.setPos(x0, y0)
        it.setZValue(z)
        sc.addItem(it)
    return qimage_to_pil(sc.render_image(width))


def main(argv=None):
    import argparse
    ap = argparse.ArgumentParser(prog="main.py predict", description=__doc__.splitlines()[0])
    ap.add_argument("images", nargs="+")
    ap.add_argument("--ckpt", default=str(HERE / "runs" / "default" / "best.pt"))
    ap.add_argument("--out", default=None, help="folder for the results (default: next to each picture)")
    ap.add_argument("--clipboard", action="store_true", help="put the (last) result on the clipboard for DeNotive")
    ap.add_argument("--temperature", type=float, default=0.0, help="0 = most likely answer; ~0.7 = try variations")
    ap.add_argument("--no-clean", action="store_true", help="keep labels drawn on pictures in the cut-outs")
    ap.add_argument("--no-trace", action="store_true", help="keep solid-colour regions as pictures, don't trace")
    ap.add_argument("--no-ocr", action="store_true", help="keep the model's own guesses at the words")
    ap.add_argument("--no-refine", action="store_true",
                    help="keep the model's own (rougher) plot lines and filled areas instead of tracing them")
    ap.add_argument("--device", default="auto")
    a = ap.parse_args(argv)

    from .render import ensure_app, to_items
    ensure_app(offscreen=not a.clipboard)
    device = pick_device(a.device)
    model, size, step = load(a.ckpt, device)
    print(f"Model from step {step} ({a.ckpt}) on {device}.", flush=True)
    last, last_specs = None, []
    for n, path in enumerate(a.images):
        p = Path(path)
        out = Path(a.out) if a.out else p.parent
        out.mkdir(parents=True, exist_ok=True)
        boxed, source, specs, seq = predict(model, size, p, device, a.temperature)
        if not a.no_ocr:  # the model places text well but can't read it: Windows OCR reads each box
            from .ocr import apply as read_text
            specs = read_text(source, specs)
        if not a.no_refine:  # exact shapes from the picture's pixels: plot lines first, then filled areas
            from .refine import refine
            specs = refine(source, specs)
        to_clip = a.clipboard and n == len(a.images) - 1
        has_pics = any(s["t"] == "image" for s in specs)
        store = library_store() if to_clip and has_pics else None
        pics, pictures, traced = picture_items(specs, source, p.stem, out, store, not a.no_clean, not a.no_trace)
        items = sorted(to_items(specs) + pics, key=lambda d: d["z"])
        (out / f"{p.stem}_pieces.json").write_text(json.dumps(items, indent=1, ensure_ascii=False), encoding="utf-8")
        rebuilt = preview(items, pictures, 960)
        sheet = Image.new("RGB", (2 * 960 + 8, rebuilt.height), (110, 110, 110))
        sheet.paste(source.resize((960, rebuilt.height)), (0, 0))
        sheet.paste(rebuilt, (968, 0))
        sheet.save(out / f"{p.stem}_rebuilt.png")
        kinds = {}
        for s in specs:
            kinds[s["t"]] = kinds.get(s["t"], 0) + 1
        extra = (f", {traced} traced colour regions" if traced else "") + \
            (f", {len(pictures)} picture(s)" if pictures else "")
        print(f"{p.name}: {len(specs)} pieces {kinds}{extra} ({len(seq)} tokens) -> "
              f"{out / (p.stem + '_rebuilt.png')}", flush=True)
        last, last_specs = items, specs
    if a.clipboard and last is not None:
        from PySide6.QtCore import QMimeData
        from PySide6.QtWidgets import QApplication
        md = QMimeData()
        md.setData(SLIDE_MIME, json.dumps([{k: v for k, v in d.items() if k != "file"} for d in last]).encode())
        md.setText("\n".join(s.get("text", "") for s in last_specs if s.get("text")))
        QApplication.clipboard().setMimeData(md)
        QApplication.processEvents()
        if sys.platform == "win32":
            import ctypes
            ctypes.windll.ole32.OleFlushClipboard()  # hand the data to Windows so it outlives this program
        print("On the clipboard: open an evidence slide in DeNotive and press Ctrl+V.", flush=True)


if __name__ == "__main__":
    main(sys.argv[1:])
