"""Damage that mimics a screenshot of a small, compressed copy of a figure. It grows as training goes on.

At damage level 0 the picture is clean. Towards 1, more and more of this happens, in the order it does in real
life:
  1. the figure only exists as a smaller copy (shrunk by up to 4x),
  2. that copy was saved as a JPEG (quality down to ~15),
  3. it was shown enlarged on screen (blocky or smeared upscaling) and screenshotted,
  4. a bit of extra blur (screen scaling, a phone photo of a screen),
  5. the screenshot ends up at the model's input size,
  6. and was itself saved as a JPEG.
"""
import io
import random

from PIL import Image, ImageEnhance, ImageFilter

RESAMPLE = (Image.BILINEAR, Image.BICUBIC, Image.LANCZOS, Image.BOX)


def _jpeg(img, quality):
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=int(quality), subsampling=2 if quality < 80 else 0)
    buf.seek(0)
    return Image.open(buf).convert("RGB")


def degrade(img, level, rng=None, out_size=(640, 360)):
    """A damaged copy of a clean RGB picture. level 0..1."""
    rng = rng or random.Random()
    level = max(0.0, min(1.0, float(level)))
    w, h = img.size
    if level > 0.02:
        # 1. a smaller original
        f = 1.0 + rng.uniform(0.0, 3.0) * level
        if f > 1.05:
            small = img.resize((max(16, round(w / f)), max(9, round(h / f))), rng.choice(RESAMPLE))
        else:
            small = img
        # 2. saved as a JPEG
        if rng.random() < 0.3 + 0.7 * level:
            small = _jpeg(small, rng.uniform(95 - 80 * level, 95))
        # 3. shown bigger on screen
        up = Image.NEAREST if rng.random() < 0.15 * level else rng.choice((Image.BILINEAR, Image.BICUBIC))
        img = small.resize((w, h), up) if small.size != (w, h) else small
        # 4. extra blur
        sigma = rng.uniform(0.0, 1.8) * level
        if sigma > 0.15:
            img = img.filter(ImageFilter.GaussianBlur(sigma))
        # a screen's colours are never quite the same
        if rng.random() < 0.5 * level:
            img = ImageEnhance.Contrast(img).enhance(rng.uniform(1 - 0.2 * level, 1 + 0.1 * level))
            img = ImageEnhance.Brightness(img).enhance(rng.uniform(1 - 0.08 * level, 1 + 0.05 * level))
    # 5. the model's input size
    img = img.resize(out_size, rng.choice((Image.BILINEAR, Image.BOX, Image.LANCZOS)))
    # 6. the screenshot saved as a JPEG
    if level > 0.02 and rng.random() < 0.8 * level:
        img = _jpeg(img, rng.uniform(95 - 55 * level, 98))
    return img


def letterbox(img, out_size=(640, 360), fill=(255, 255, 255)):
    """Fits any picture into the slide's 16:9 shape (white margins), as the model expects.
    Returns (picture, scale, x offset, y offset) so positions can be mapped back to the original."""
    W, H = out_size
    img = img.convert("RGB")
    s = min(W / img.width, H / img.height)
    nw, nh = max(1, round(img.width * s)), max(1, round(img.height * s))
    canvas = Image.new("RGB", out_size, fill)
    ox, oy = (W - nw) // 2, (H - nh) // 2
    canvas.paste(img.resize((nw, nh), Image.LANCZOS), (ox, oy))
    return canvas, s, ox, oy


def preview(clean, path, levels=(0.0, 0.25, 0.5, 0.75, 1.0), seed=3, out_size=(640, 360)):
    """One strip showing the same figure at several damage levels."""
    tiles = [degrade(clean, lv, random.Random(seed), out_size) for lv in levels]
    sheet = Image.new("RGB", (out_size[0], (out_size[1] + 8) * len(tiles)), (128, 128, 128))
    for k, t in enumerate(tiles):
        sheet.paste(t, (0, k * (out_size[1] + 8)))
    sheet.save(path)
    return path
