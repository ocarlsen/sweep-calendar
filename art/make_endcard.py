"""End card for the demo video, in the site's light theme.

    python3 art/make_endcard.py 1280 914 art/endcard.png   (needs Pillow)
"""
import sys
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont

W, H, out = int(sys.argv[1]), int(sys.argv[2]), sys.argv[3]
BG, INK, MUTED, ACCENT = "#f6f4ef", "#1c1b19", "#6b675f", "#c2410c"
SF = "/System/Library/Fonts/SFNS.ttf"


def font(size, weight):
    f = ImageFont.truetype(SF, size)
    # Axes: Width, Optical Size, GRAD, Weight. Keep normal width, size-appropriate optics.
    f.set_variation_by_axes([100, min(96, max(17, size)), 400, weight])
    return f


img = Image.new("RGB", (W, H), BG)
d = ImageDraw.Draw(img)
s = W / 1280

url = font(int(76 * s), 700)
sub = font(int(30 * s), 450)
name = font(int(34 * s), 650)

# App icon + name, centered as a group.
mark = int(64 * s)
emoji = Image.open(Path(__file__).parent.parent / "web" / "icons" / "icon-tight-128.png").convert("RGBA").resize((mark, mark), Image.LANCZOS)
label = "Sweep Calendar"
URL, SUB = "parking.ocarlsen.com", "Street-sweeping calendars for your city."
lw = d.textlength(label, font=name)
gap = int(16 * s)

# Lay out by real glyph edges: the brand row, the URL and the subtitle are
# separated by the same visible gap, and the stack is centered vertically.
def ink(text, f):
    l, t, r, b = d.textbbox((0, 0), text, font=f, anchor="ls")
    return t, b  # top/bottom relative to the baseline
url_t, url_b = ink(URL, url)
sub_t, sub_b = ink(SUB, sub)
space = int(56 * s)  # visible gap between rows
brand_h = mark
total = brand_h + space + (url_b - url_t) + space + (sub_b - sub_t)
top = (H - total) / 2
url_base = top + brand_h + space - url_t
sub_base = url_base + url_b + space - sub_t

gx = (W - (mark + gap + lw)) / 2
img.paste(emoji, (int(gx), int(top)), emoji)
d.text((gx + mark + gap, top + mark / 2), label, font=name, fill=INK, anchor="lm")
d.text((W / 2, url_base), URL, font=url, fill=INK, anchor="ms")
d.text((W / 2, sub_base), SUB, font=sub, fill=MUTED, anchor="ms")
img.save(out)
