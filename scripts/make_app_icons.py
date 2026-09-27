"""Render the application icon from the PMA mark in docs/assets/logo.png.

The mark (three petals in a grey ring) is cropped from the logo -- the same
rows site/logo-anim.js uses (``MARK_SRC``) -- centred on a transparent square
with a little margin, and written as:

- ``paintmaskanimator/assets/app_icon.png``  window / Dock icon at runtime
- ``packaging/app.ico``                      Windows exe and installer
- ``packaging/app.icns``                     macOS .app bundle

The outputs are committed, so this only needs re-running when the logo changes:

    python scripts/make_app_icons.py
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
LOGO = ROOT / "docs" / "assets" / "logo.png"
PNG_OUT = ROOT / "paintmaskanimator" / "assets" / "app_icon.png"
ICO_OUT = ROOT / "packaging" / "app.ico"
ICNS_OUT = ROOT / "packaging" / "app.icns"

# The mark occupies these rows of logo.png (the "PMA" lettering is below).
# Keep in sync with MARK_SRC in site/logo-anim.js.
MARK_BOX = (0, 10, 390, 348)
# Transparent margin around the mark, as a fraction of the icon side. macOS
# icons sit inside roughly this inset, so the mark matches its neighbours.
MARGIN = 0.06
SIZE = 1024

ICO_SIZES = [(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)]


def render_icon(size: int = SIZE) -> Image.Image:
    mark = Image.open(LOGO).convert("RGBA").crop(MARK_BOX)
    bbox = mark.getbbox()
    if bbox:
        mark = mark.crop(bbox)
    inner = round(size * (1.0 - 2 * MARGIN))
    scale = inner / max(mark.width, mark.height)
    mark = mark.resize(
        (max(1, round(mark.width * scale)), max(1, round(mark.height * scale))),
        Image.Resampling.LANCZOS,
    )
    icon = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    icon.alpha_composite(
        mark, ((size - mark.width) // 2, (size - mark.height) // 2)
    )
    return icon


def main() -> None:
    icon = render_icon()
    PNG_OUT.parent.mkdir(parents=True, exist_ok=True)
    icon.resize((512, 512), Image.Resampling.LANCZOS).save(PNG_OUT, optimize=True)
    icon.save(ICO_OUT, sizes=ICO_SIZES)
    icon.save(ICNS_OUT)
    for path in (PNG_OUT, ICO_OUT, ICNS_OUT):
        print(f"wrote {path.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
