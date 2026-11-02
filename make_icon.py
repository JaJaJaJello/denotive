"""Renders assets/icon.ico + assets/icon.png for DeNotive.

Large sizes: a midnight squircle board with two pinned cards (a polaroid showing a spectrum with peaks and a
yellow sticky note) tied together by red string, and a magnifying glass over the scene.
Small sizes (<= 32 px) use a simplified mark: red string between two pins and a lens ring.
"""
import math
from pathlib import Path

import numpy as np
from PIL import Image, ImageChops, ImageDraw, ImageFilter

S = 1024


def linear_gradient(size, c0, c1, angle_deg):
    yy, xx = np.mgrid[0:size[1], 0:size[0]].astype(np.float32)
    a = math.radians(angle_deg)
    proj = xx * math.cos(a) + yy * math.sin(a)
    t = (proj - proj.min()) / max(1e-6, (proj.max() - proj.min()))
    arr = np.array(c0, np.float32) * (1 - t[..., None]) + np.array(c1, np.float32) * t[..., None]
    return Image.fromarray(arr.astype(np.uint8), "RGBA" if len(c0) == 4 else "RGB").convert("RGBA")


def radial_alpha(size, center, radius, strength):
    yy, xx = np.mgrid[0:size, 0:size].astype(np.float32)
    d = np.sqrt((xx - center[0]) ** 2 + (yy - center[1]) ** 2) / radius
    a = np.clip(1 - d, 0, 1) ** 1.6 * 255 * strength
    return Image.fromarray(a.astype(np.uint8), "L")


def squircle_mask(size, inset, n=5.0):
    yy, xx = np.mgrid[0:size, 0:size].astype(np.float32)
    r = (size - 2 * inset) / 2
    c = size / 2
    v = np.abs((xx - c) / r) ** n + np.abs((yy - c) / r) ** n
    edge = np.clip((1.0 - v) * r / 3.0 + 0.5, 0, 1)
    return Image.fromarray((edge * 255).astype(np.uint8), "L")


def shadow(mask, blur, opacity, offset=(0, 0)):
    sh = Image.new("RGBA", mask.size, (0, 0, 0, 0))
    a = mask.filter(ImageFilter.GaussianBlur(blur)).point(lambda v: int(v * opacity))
    sh.putalpha(a)
    return ImageChops.offset(sh, *offset)


def rotated_layer(draw_fn, angle, center):
    layer = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    draw_fn(layer, ImageDraw.Draw(layer))
    return layer.rotate(angle, resample=Image.BICUBIC, center=center)


def pin(img, c, r=34, color=(226, 52, 58)):
    cx, cy = c
    sh = Image.new("L", (S, S), 0)
    ImageDraw.Draw(sh).ellipse((cx - r + 8, cy - r + 14, cx + r + 8, cy + r + 14), fill=255)
    img.alpha_composite(shadow(sh, 8, 0.55))
    head = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    hd = ImageDraw.Draw(head)
    for i in range(r, 0, -1):
        t = i / r
        col = tuple(int(color[k] * (0.55 + 0.45 * (1 - t) ** 0.6) + (255 - color[k]) * 0.35 * (1 - t) ** 3)
                    for k in range(3))
        hd.ellipse((cx - i - (1 - t) * r * 0.25, cy - i - (1 - t) * r * 0.3,
                    cx + i - (1 - t) * r * 0.25, cy + i - (1 - t) * r * 0.3), fill=col + (255,))
    hd.ellipse((cx - r * 0.55, cy - r * 0.6, cx - r * 0.1, cy - r * 0.25), fill=(255, 255, 255, 200))
    img.alpha_composite(head)


def string_points(a, b, sag):
    pts = []
    for i in range(61):
        t = i / 60
        mx, my = (a[0] + b[0]) / 2, (a[1] + b[1]) / 2 + 2 * sag
        x = (1 - t) ** 2 * a[0] + 2 * (1 - t) * t * mx + t ** 2 * b[0]
        y = (1 - t) ** 2 * a[1] + 2 * (1 - t) * t * my + t ** 2 * b[1]
        pts.append((x, y))
    return pts


def render_large():
    img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    tile = squircle_mask(S, 56)
    img.alpha_composite(shadow(tile, 26, 0.55, (0, 22)))

    bg = linear_gradient((S, S), (24, 30, 54, 255), (9, 11, 20, 255), 70)
    for center, radius, col, op in [((160, 980), 760, (229, 72, 77), 0.42), ((960, 120), 640, (91, 110, 255), 0.45),
                                    ((520, 520), 520, (40, 60, 110), 0.35)]:
        bloom = Image.new("RGBA", (S, S), col + (0,))
        bloom.putalpha(radial_alpha(S, center, radius, op))
        bg.alpha_composite(bloom)
    # dotted board
    dots = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    dd = ImageDraw.Draw(dots)
    for y in range(64, S, 44):
        for x in range(64, S, 44):
            dd.ellipse((x - 3, y - 3, x + 3, y + 3), fill=(255, 255, 255, 26))
    bg.alpha_composite(dots)
    tile_img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    tile_img.paste(bg, (0, 0), tile)

    # ---- card A: polaroid with a spectrum
    a_center = (372, 380)

    def card_a(layer, d):
        x0, y0, x1, y1 = 190, 205, 555, 560
        d.rounded_rectangle((x0, y0, x1, y1), radius=14, fill=(246, 244, 238, 255))
        px0, py0, px1, py1 = x0 + 24, y0 + 24, x1 - 24, y0 + 255
        d.rectangle((px0, py0, px1, py1), fill=(22, 30, 52, 255))
        pts = []
        for i in range(120):
            t = i / 119
            xv = px0 + 10 + t * (px1 - px0 - 20)
            yv = 0.06 + 0.78 * math.exp(-((t - 0.32) / 0.035) ** 2) + 0.45 * math.exp(-((t - 0.58) / 0.05) ** 2) \
                + 0.25 * math.exp(-((t - 0.8) / 0.04) ** 2)
            pts.append((xv, py1 - 18 - yv * (py1 - py0 - 40)))
        d.line(pts, fill=(255, 196, 92, 255), width=9, joint="curve")
        for i in range(3):
            d.rounded_rectangle((x0 + 28, y1 - 74 + i * 20, x1 - 60 - i * 70, y1 - 64 + i * 20), radius=5,
                                fill=(60, 64, 76, 210 - i * 50))
    la = rotated_layer(card_a, 9, a_center)
    tile_img.alpha_composite(shadow(la.getchannel("A"), 20, 0.55, (10, 22)))
    tile_img.alpha_composite(la)

    # ---- card B: yellow sticky note
    b_center = (690, 560)

    def card_b(layer, d):
        x0, y0, x1, y1 = 545, 420, 845, 715
        fold = 52
        d.polygon([(x0, y0), (x1, y0), (x1, y1 - fold), (x1 - fold, y1), (x0, y1)], fill=(255, 226, 92, 255))
        d.polygon([(x1, y1 - fold), (x1 - fold, y1 - fold), (x1 - fold, y1)], fill=(214, 176, 40, 255))
        for i in range(4):
            d.rounded_rectangle((x0 + 34, y0 + 78 + i * 46, x1 - 54 - (i % 2) * 60, y0 + 92 + i * 46), radius=6,
                                fill=(120, 96, 20, 150))
    lb = rotated_layer(card_b, -7, b_center)
    tile_img.alpha_composite(shadow(lb.getchannel("A"), 20, 0.55, (10, 22)))
    tile_img.alpha_composite(lb)

    # pin positions (rotated with their cards)
    def rot(p, c, deg):
        a = math.radians(-deg)
        dx, dy = p[0] - c[0], p[1] - c[1]
        return (c[0] + dx * math.cos(a) - dy * math.sin(a), c[1] + dx * math.sin(a) + dy * math.cos(a))
    pa = rot((372, 232), a_center, 9)
    pb = rot((695, 448), b_center, -7)
    pc = (205, 780)

    # ---- red string
    for (p0, p1, sag) in ((pa, pb, 46), (pc, pa, 20)):
        pts = string_points(p0, p1, sag)
        sh = Image.new("L", (S, S), 0)
        ImageDraw.Draw(sh).line([(x + 6, y + 12) for x, y in pts], fill=255, width=14, joint="curve")
        tile_img.alpha_composite(shadow(sh, 6, 0.6))
        dr = ImageDraw.Draw(tile_img)
        dr.line(pts, fill=(205, 30, 46, 255), width=13, joint="curve")
        dr.line([(x - 1.5, y - 2.5) for x, y in pts], fill=(255, 120, 128, 160), width=4, joint="curve")
    pin(tile_img, (int(pa[0]), int(pa[1])))
    pin(tile_img, (int(pb[0]), int(pb[1])))
    pin(tile_img, pc, 30)

    # ---- magnifying glass
    lc, lr = (612, 708), 158
    hand = Image.new("L", (S, S), 0)
    hdraw = ImageDraw.Draw(hand)
    ang = math.radians(42)
    h0 = (lc[0] + math.cos(ang) * (lr + 10), lc[1] + math.sin(ang) * (lr + 10))
    h1 = (lc[0] + math.cos(ang) * (lr + 210), lc[1] + math.sin(ang) * (lr + 210))
    hdraw.line([h0, h1], fill=255, width=62)
    hdraw.ellipse((h1[0] - 31, h1[1] - 31, h1[0] + 31, h1[1] + 31), fill=255)
    ring = Image.new("L", (S, S), 0)
    ImageDraw.Draw(ring).ellipse((lc[0] - lr - 26, lc[1] - lr - 26, lc[0] + lr + 26, lc[1] + lr + 26), fill=255)
    glass_shadow = ImageChops.lighter(hand, ring)
    tile_img.alpha_composite(shadow(glass_shadow, 22, 0.6, (14, 26)))
    # lens: brighten + slight blue tint (magnified look)
    lens = Image.new("L", (S, S), 0)
    ImageDraw.Draw(lens).ellipse((lc[0] - lr, lc[1] - lr, lc[0] + lr, lc[1] + lr), fill=255)
    zoom = tile_img.crop((lc[0] - lr * 0.8, lc[1] - lr * 0.8, lc[0] + lr * 0.8, lc[1] + lr * 0.8)).resize(
        (2 * lr, 2 * lr), Image.LANCZOS)
    zl = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    zl.paste(zoom, (lc[0] - lr, lc[1] - lr))
    tint = Image.new("RGBA", (S, S), (170, 205, 255, 42))
    zl.alpha_composite(tint)
    zl.putalpha(ImageChops.multiply(zl.getchannel("A"), lens))
    tile_img.alpha_composite(zl)
    # handle (dark, with a gold ferrule)
    hl = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    hld = ImageDraw.Draw(hl)
    hld.line([h0, h1], fill=(28, 24, 26, 255), width=58)
    hld.ellipse((h1[0] - 29, h1[1] - 29, h1[0] + 29, h1[1] + 29), fill=(28, 24, 26, 255))
    f0 = (lc[0] + math.cos(ang) * (lr + 14), lc[1] + math.sin(ang) * (lr + 14))
    f1 = (lc[0] + math.cos(ang) * (lr + 62), lc[1] + math.sin(ang) * (lr + 62))
    hld.line([f0, f1], fill=(214, 168, 72, 255), width=66)
    hld.line([(h0[0] + 70, h0[1] + 60), (h1[0] - 20, h1[1] - 18)], fill=(80, 72, 76, 255), width=10)
    tile_img.alpha_composite(hl)
    # rim
    rim = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    rd = ImageDraw.Draw(rim)
    rd.ellipse((lc[0] - lr - 24, lc[1] - lr - 24, lc[0] + lr + 24, lc[1] + lr + 24), outline=(196, 150, 60, 255),
               width=30)
    rd.ellipse((lc[0] - lr - 24, lc[1] - lr - 24, lc[0] + lr + 24, lc[1] + lr + 24), outline=(250, 214, 130, 255),
               width=8)
    rd.arc((lc[0] - lr + 26, lc[1] - lr + 26, lc[0] + lr - 26, lc[1] + lr - 26), 200, 262,
           fill=(255, 255, 255, 190), width=16)
    tile_img.alpha_composite(rim)

    # gloss + rim light
    gloss = Image.new("RGBA", (S, S), (255, 255, 255, 0))
    gm = Image.new("L", (S, S), 0)
    ImageDraw.Draw(gm).ellipse((-220, -780, S + 220, 400), fill=36)
    gm = ImageChops.multiply(gm, tile)
    gloss.putalpha(gm)
    tile_img.alpha_composite(gloss)
    rimm = ImageChops.subtract(tile, squircle_mask(S, 62))
    rl = Image.new("RGBA", (S, S), (255, 255, 255, 0))
    rl.putalpha(rimm.point(lambda v: int(v * 0.3)))
    tile_img.alpha_composite(rl)
    tile_img.putalpha(ImageChops.multiply(tile_img.getchannel("A"), tile))
    img.alpha_composite(tile_img)
    return img


def render_small(px):
    s = 512
    tile = squircle_mask(s, 16, n=4.2)
    bg = linear_gradient((s, s), (34, 42, 74, 255), (12, 14, 26, 255), 60)
    img = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    img.paste(bg, (0, 0), tile)
    d = ImageDraw.Draw(img)
    a, b = (120, 150), (392, 236)
    pts = string_points(a, b, 30)
    d.line(pts, fill=(236, 52, 64, 255), width=34, joint="curve")
    for c in (a, b):
        d.ellipse((c[0] - 48, c[1] - 48, c[0] + 48, c[1] + 48), fill=(236, 52, 64, 255))
        d.ellipse((c[0] - 28, c[1] - 30, c[0] - 2, c[1] - 6), fill=(255, 190, 190, 255))
    lc, lr = (230, 330), 100
    d.ellipse((lc[0] - lr, lc[1] - lr, lc[0] + lr, lc[1] + lr), outline=(250, 214, 130, 255), width=38)
    ang = math.radians(42)
    h0 = (lc[0] + math.cos(ang) * (lr + 10), lc[1] + math.sin(ang) * (lr + 10))
    h1 = (lc[0] + math.cos(ang) * (lr + 130), lc[1] + math.sin(ang) * (lr + 130))
    d.line([h0, h1], fill=(250, 214, 130, 255), width=54)
    return img.resize((px, px), Image.LANCZOS)


if __name__ == "__main__":
    big = render_large()
    root = Path(__file__).parent
    out = root / "assets"
    out.mkdir(exist_ok=True)
    frames = {sz: (render_small(sz) if sz <= 32 else big.resize((sz, sz), Image.LANCZOS))
              for sz in (16, 20, 24, 32, 40, 48, 64, 96, 128, 256)}
    frames[256].save(out / "icon.ico", format="ICO", sizes=[(k, k) for k in frames],
                     append_images=[frames[k] for k in frames if k != 256])
    big.resize((512, 512), Image.LANCZOS).save(out / "icon.png")
    sheet = Image.new("RGBA", (900, 420), (32, 32, 36, 255))
    x = 20
    for sz in (256, 128, 64, 48, 32, 24, 16):
        sheet.alpha_composite(frames[sz], (x, 150 - sz // 2))
        x += sz + 24
    light = Image.new("RGBA", (900, 120), (243, 243, 243, 255))
    x = 20
    for sz in (64, 48, 32, 24, 16):
        light.alpha_composite(frames[sz], (x, 60 - sz // 2))
        x += sz + 24
    sheet.paste(light, (0, 300))
    (root / "build_bin").mkdir(exist_ok=True)
    sheet.save(root / "build_bin" / "icon_preview.png")
    print("ok")
