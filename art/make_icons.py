"""Regenerate web/icons/ from the app-icon artwork.

    python3 art/make_icons.py   (needs Pillow)

Source: icon-source-gemini.jpeg (Gemini, 2026-10-20), recolored from its
navy (#244477) to #3478f6. Pixels on the navy-to-white line (background,
anti-aliased edges, road, calendar ink) keep their position on the new
blue-to-white line; wheels and the red dots are off that line and untouched.

Outputs: icon-512/192 (full art), icon-tight-16/32/64/128 (cropped to the
truck so it stays readable at tab size), apple-touch-icon (full bleed; iOS
rounds it itself).
"""
import math
from pathlib import Path

from PIL import Image, ImageDraw

HERE = Path(__file__).parent
OUT = HERE.parent / "web" / "icons"
SRC_BLUE, BLUE, WHITE = (36, 68, 119), (0x34, 0x78, 0xF6), (255, 255, 255)
OFF_LINE = 28  # farther than this from the navy-white line = not part of the blue


def recolor(img):
    d = [w - n for w, n in zip(WHITE, SRC_BLUE)]
    dd = sum(x * x for x in d)
    px, out = img.load(), img.copy()
    po = out.load()
    for y in range(img.height):
        for x in range(img.width):
            c = px[x, y]
            t = min(1, max(0, sum((ci - ni) * di for ci, ni, di in zip(c, SRC_BLUE, d)) / dd))
            if math.dist(c, [n + t * di for n, di in zip(SRC_BLUE, d)]) < OFF_LINE:
                po[x, y] = tuple(round(b + t * (w - b)) for b, w in zip(BLUE, WHITE))
    return out


def rounded(img, frac=0.195):
    mask = Image.new("L", img.size, 0)
    ImageDraw.Draw(mask).rounded_rectangle([0, 0, img.width - 1, img.height - 1],
                                           radius=int(img.width * frac), fill=255)
    img = img.convert("RGBA")
    img.putalpha(mask)
    return img


def art_bbox(img):
    px, m = img.load(), int(img.width * 0.04)
    art = lambda c: sum(abs(a - b) for a, b in zip(c[:3], BLUE)) > 90
    xs = [x for x in range(m, img.width - m, 4) if any(art(px[x, y]) for y in range(m, img.height - m, 8))]
    ys = [y for y in range(m, img.height - m, 4) if any(art(px[x, y]) for x in range(m, img.width - m, 8))]
    return min(xs), min(ys), max(xs), max(ys)


def main():
    src = recolor(Image.open(HERE / "icon-source-gemini.jpeg").convert("RGB"))
    full = rounded(src)
    for n in (512, 192):
        full.resize((n, n), Image.LANCZOS).save(OUT / f"icon-{n}.png")
    inset = src.width // 7  # drop the source's rounded corners; iOS rounds its own
    src.crop((inset, inset, src.width - inset, src.height - inset)).resize(
        (180, 180), Image.LANCZOS).save(OUT / "apple-touch-icon.png")

    x0, y0, x1, y1 = art_bbox(src)
    side = int(max(x1 - x0, y1 - y0) * 1.2)
    cx, cy = (x0 + x1) // 2, (y0 + y1) // 2
    # Paste only what lies inside the source's own rounded square, so its
    # corners (white, plus JPEG fringe) never reach the tight crop.
    shape = Image.new("L", src.size, 0)
    ImageDraw.Draw(shape).rounded_rectangle([0, 0, src.width - 1, src.height - 1],
                                            radius=int(src.width * 0.195) + 24, fill=255)
    box = (cx - side // 2, cy - side // 2, cx + side // 2, cy + side // 2)
    tile = Image.new("RGB", (side, side), BLUE)
    tile.paste(src.crop(box), (0, 0), shape.crop(box))
    tight = rounded(tile, 0.2)
    for n in (16, 32, 64, 128):
        tight.resize((n, n), Image.LANCZOS).save(OUT / f"icon-tight-{n}.png")


if __name__ == "__main__":
    main()
