"""Render the macOS .dmg window background (1x and @2x).

The look follows the site's hero (site/style.css, site/logo-anim.js): the pale
hero background with blue and red glows, the dot-art PMA lockup, and dot-art
line work in the logo's red / blue / green.

packaging/macos/logo-dots.png is the site's hero logo captured one pixel per
dot (the finished canvas drawn by logo-anim.js). The output PNGs are committed
next to it, so this only needs to be re-run when the design changes. It uses
the Hiragino font that ships with macOS, so run it on a Mac:

    python scripts/make_dmg_background.py

The layout must match packaging/macos/dmg_settings.py (window size and icon
positions).
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageCms, ImageDraw, ImageFilter, ImageFont

ROOT = Path(__file__).resolve().parent.parent
MAC_DIR = ROOT / "packaging" / "macos"
LOGO_DOTS = MAC_DIR / "logo-dots.png"

# Window content size in points; keep in sync with dmg_settings.py.
WIDTH, HEIGHT = 660, 490
APP_POS = (170, 225)
APPS_POS = (490, 225)

# One logo / line-art dot, in points. 1.5pt is a whole 3px on Retina.
DOT = 1.5

# site/style.css (light scheme).
HERO_BG = (243, 244, 247)
HERO_SOFT = (61, 66, 76)
HERO_MUTED = (106, 111, 122)
HERO_LINE = (218, 220, 225)
GLOW_A = ((47, 128, 237), 0.12)  # --glow-a
GLOW_B = ((232, 56, 61), 0.08)  # --glow-b
# site/logo-anim.js: petal palette and light-scheme line art.
RED = (232, 56, 61)
BLUE = (47, 128, 237)
GREEN = (60, 176, 90)
LINE = (29, 31, 36)

FONT_BOLD = "/System/Library/Fonts/ヒラギノ角ゴシック W6.ttc"
FONT_REGULAR = "/System/Library/Fonts/ヒラギノ角ゴシック W3.ttc"

# Smooth parts (glows, text) are drawn oversized and downsampled.
SUPERSAMPLE = 4


def _glow(img: Image.Image, centre, radii, colour, alpha: float) -> None:
    """A soft radial glow like CSS radial-gradient(... transparent 60%)."""
    w, h = img.size
    layer = Image.new("RGB", (w, h), colour)
    mask = Image.new("L", (w, h), 0)
    cx, cy = centre
    rx, ry = radii
    ImageDraw.Draw(mask).ellipse(
        (cx - rx * 0.6, cy - ry * 0.6, cx + rx * 0.6, cy + ry * 0.6),
        fill=round(255 * alpha),
    )
    mask = mask.filter(ImageFilter.GaussianBlur(min(rx, ry) * 0.35))
    img.paste(layer, (0, 0), mask)


def _smooth_layer(s: int) -> Image.Image:
    """Background and text, at `s` pixels per point."""
    w, h = WIDTH * s, HEIGHT * s
    img = Image.new("RGB", (w, h), HERO_BG)
    # Same placement as the .hero background, scaled to this window.
    _glow(img, (0.15 * w, -0.10 * h), (0.9 * w, 0.75 * h), *GLOW_A)
    _glow(img, (0.90 * w, 0.10 * h), (0.6 * w, 0.6 * h), *GLOW_B)

    draw = ImageDraw.Draw(img)
    sub = ImageFont.truetype(FONT_BOLD, 13 * s)
    hint = ImageFont.truetype(FONT_REGULAR, 11 * s)
    draw.text(
        (WIDTH / 2 * s, 140 * s),
        "アイコンを Applications へドラッグしてインストール",
        font=sub, fill=HERO_SOFT, anchor="mm",
    )
    y = 318
    draw.line(
        ((WIDTH / 2 - 150) * s, y * s, (WIDTH / 2 + 150) * s, y * s),
        fill=HERO_LINE, width=s,
    )
    draw.text(
        (WIDTH / 2 * s, 336 * s),
        "初回に開けないときは下のファイルをご覧ください",
        font=hint, fill=HERO_MUTED, anchor="mm",
    )
    return img


def _arrow_dots() -> Image.Image:
    """A dot-art arrow: flat red -> blue -> green fill, dark line art.

    Like the site's logo, the line art is the ring of dots just outside the
    fill.
    """
    length, shaft, head_len, head = 96, 10, 26, 34
    w, h = length + 2, head + 2
    fill = [[False] * w for _ in range(h)]
    mid = h / 2
    for x in range(1, length + 1):
        if x <= length - head_len:
            half = shaft / 2
        else:
            # Head: a triangle narrowing to the tip.
            half = head / 2 * (length + 1 - x) / head_len
        for y in range(h):
            if abs(y + 0.5 - mid) < half:
                fill[y][x] = True

    img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    px = img.load()
    assert px is not None
    body = length - head_len
    for y in range(h):
        for x in range(w):
            if fill[y][x]:
                # Three flat bands, one per petal colour, ending in green.
                band = min(2, x * 3 // (body + 6))
                px[x, y] = (*(RED, BLUE, GREEN)[band], 255)
            elif any(
                0 <= y + dy < h and 0 <= x + dx < w and fill[y + dy][x + dx]
                for dy, dx in ((-1, 0), (1, 0), (0, -1), (0, 1))
            ):
                px[x, y] = (*LINE, 255)
    return img


def _paste_dots(img: Image.Image, dots: Image.Image, centre, px_per_dot: float) -> None:
    size = (round(dots.width * px_per_dot), round(dots.height * px_per_dot))
    scaled = dots.resize(size, Image.Resampling.NEAREST)
    x = round(centre[0] - size[0] / 2)
    y = round(centre[1] - size[1] / 2)
    img.paste(scaled, (x, y), scaled)


def render(scale: int) -> Image.Image:
    # Dot art needs whole device pixels per dot, so draw it at 2x and let the
    # 1x image be a plain downscale of the 2x one.
    s = 2
    img = _smooth_layer(s * SUPERSAMPLE).resize(
        (WIDTH * s, HEIGHT * s), Image.Resampling.LANCZOS
    )
    px = DOT * s
    logo = Image.open(LOGO_DOTS).convert("RGBA")
    _paste_dots(img, logo, (WIDTH / 2 * s, 78 * s), px)
    arrow = _arrow_dots()
    _paste_dots(
        img, arrow, ((APP_POS[0] + APPS_POS[0]) / 2 * s, (APP_POS[1] - 6) * s), px
    )
    if scale == s:
        return img
    return img.resize((WIDTH * scale, HEIGHT * scale), Image.Resampling.LANCZOS)


def main() -> None:
    # Tag the PNGs as sRGB (the site's colours); untagged, Finder renders
    # them visibly duller.
    srgb = ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB")).tobytes()
    for scale, name in ((1, "dmg-background.png"), (2, "dmg-background@2x.png")):
        path = MAC_DIR / name
        render(scale).save(
            path, dpi=(72 * scale, 72 * scale), optimize=True, icc_profile=srgb,
        )
        print(f"wrote {path.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
