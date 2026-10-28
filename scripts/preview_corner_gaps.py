"""Generate reproducible before/actual/expected PNGs for corner gap regression cases."""
from pathlib import Path
import runpy
import sys

import numpy as np
from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from paintmaskanimator import bucket_fill  # noqa: E402


def main():
    cases = runpy.run_path(str(ROOT / "tests/test_bucket_corner_gaps.py"))["CASES"]
    output = ROOT / "build_verify/corner_gaps"
    output.mkdir(parents=True, exist_ok=True)
    font = ImageFont.truetype("C:/Windows/Fonts/arial.ttf", 19)
    sheet = Image.new("RGB", (1240, len(cases) * 370 + 50), "#ffffff")
    draw = ImageDraw.Draw(sheet)
    for column, title in enumerate(("BEFORE", "ACTUAL", "EXPECTED")):
        draw.text((20 + column * 410, 12), title, font=font, fill="#202020")
    for row, (name, build) in enumerate(cases.items()):
        region, target, expected = build()
        actual = bucket_fill.collect_small_islands(region, target, 1)
        np.testing.assert_array_equal(actual, expected, err_msg=name)
        for column, (title, painted) in enumerate(
            (("before", region), ("actual", actual), ("expected", expected))
        ):
            rgb = np.empty((*target.shape, 3), dtype=np.uint8)
            rgb[:] = (221, 158, 181)
            rgb[bucket_fill.dilate(target, 1)] = (0, 0, 0)
            rgb[target] = (248, 245, 230)
            rgb[painted] = (6, 196, 204)
            image = Image.fromarray(rgb)
            image.save(output / f"{name}_{title}.png")
            scale = min(400 / image.width, 320 / image.height)
            image = image.resize(
                (int(image.width * scale), int(image.height * scale)),
                Image.Resampling.NEAREST,
            )
            x, y = 20 + column * 410, 50 + row * 370
            draw.text((x, y), name.replace("_", " "), font=font, fill="#202020")
            sheet.paste(image, (x, y + 30))
    path = output / "comparison.png"
    sheet.save(path)
    focus = Image.new("RGB", (830, 780), "#ffffff")
    focus_draw = ImageDraw.Draw(focus)
    focus_draw.text((20, 10), "BEFORE", font=font, fill="#202020")
    focus_draw.text((430, 10), "AFTER", font=font, fill="#202020")
    focus_top = 50 + list(cases).index("long_slit_and_protected_faces") * 370
    focus.paste(sheet.crop((0, focus_top, 830, focus_top + 370)), (0, 40))
    focus_top = 50 + list(cases).index("slit_continuation") * 370
    focus.paste(sheet.crop((0, focus_top, 830, focus_top + 370)), (0, 410))
    focus.save(output / "corrected_before_after.png")
    print(path)


if __name__ == "__main__":
    main()
