"""Compare pma-core's PSD reader with Qt's PSD import (psd-tools ``composite``).

Qt の「PSDを読み込む」（``main_window_import.psd_dialog``）は psd-tools で最上位の
レイヤーごとに（グループは子ごとに）``composite(viewport=文書全体, force=True)`` を
呼んで絵にする。Tauri の画面は pma-core の ``import::psd::read_psd`` で同じ絵を作る。
psd-tools で作った PSD を両方で読み、列の名前・表示・不透明度と各コマの RGBA を比べる。
浮動小数の丸めの違いで 1 ずれることがあるので、色は不透明度が 0 でない画素だけを
差 1 まで、不透明度は差 1 まで許す。
"""

import io

import numpy as np
import pytest

psd_tools = pytest.importorskip("psd_tools")
# 基準は配布した 0.8.0 の psd-tools（pyproject の dev で固定）。Python 3.10 には入らない。
if psd_tools.__version__ != "1.26.1":
    pytest.skip(f"psd-tools {psd_tools.__version__} は 0.8.0 の 1.26.1 と合成が違う", allow_module_level=True)
from PIL import Image  # noqa: E402
from psd_tools import PSDImage  # noqa: E402
from psd_tools.api.layers import Group, PixelLayer  # noqa: E402
from psd_tools.constants import BlendMode, Compression  # noqa: E402

from pma_native.pma_native import import_composite_psd, import_read_psd  # noqa: E402

W, H = 12, 9


def _qt_import(data):
    """``psd_dialog`` の読み込み部分（Qt の画像に直す前の RGBA）。"""
    psd = PSDImage.open(io.BytesIO(data))
    viewport = (0, 0, psd.width, psd.height)
    tracks = []
    skipped = 0
    for top in psd:
        frame_layers = list(top) if top.is_group() else [top]
        images = []
        for layer in frame_layers:
            try:
                rendered = layer.composite(viewport=viewport, force=True)
            except (ValueError, KeyError, IndexError, TypeError, OSError, RuntimeError, AttributeError):
                rendered = None
            if rendered is None:
                skipped += 1
                continue
            images.append(np.asarray(rendered.convert("RGBA"), dtype=np.uint8))
        if images:
            tracks.append((
                str(top.name or "Layer"),
                bool(top.is_visible()),
                max(0.0, min(1.0, float(top.opacity) / 255.0)),
                images,
            ))
        else:
            skipped += 1
    return psd.width, psd.height, tracks, skipped


def _gradient(seed, w, h, mode="RGBA"):
    rng = np.random.default_rng(seed)
    channels = {"RGBA": 4, "LA": 2}[mode]
    pixels = rng.integers(0, 256, size=(h, w, channels), dtype=np.uint8)
    # 透明・不透明の画素を混ぜる。
    pixels[0, :, -1] = 0
    pixels[-1, :, -1] = 255
    return Image.fromarray(pixels, mode)


def _assert_same(data):
    expected = _qt_import(data)
    width, height, tracks, skipped = import_read_psd(data)
    assert (width, height) == expected[:2]
    assert skipped == expected[3]
    assert [(n, v) for n, v, _o, _i in tracks] == [(n, v) for n, v, _o, _i in expected[2]]
    for (name, _v, opacity, images), (_n, _ev, e_opacity, e_images) in zip(tracks, expected[2]):
        assert opacity == pytest.approx(e_opacity, abs=1e-6), name
        assert len(images) == len(e_images), name
        for index, (got, want) in enumerate(zip(images, e_images)):
            got = np.frombuffer(got, dtype=np.uint8).reshape(height, width, 4).astype(int)
            want = want.astype(int)
            alpha = np.abs(got[..., 3] - want[..., 3])
            assert alpha.max() <= 1, (name, index)
            shown = (got[..., 3] > 0) & (want[..., 3] > 0)
            colour = np.abs(got[..., :3] - want[..., :3])[shown]
            assert colour.size == 0 or colour.max() <= 1, (name, index, colour.max())


def _save(psd):
    buffer = io.BytesIO()
    psd.save(buffer)
    return buffer.getvalue()


@pytest.mark.parametrize("compression", [Compression.RAW, Compression.RLE, Compression.ZIP])
def test_plain_layers(compression):
    psd = PSDImage.new("RGBA", (W, H))
    for i in range(3):
        layer = PixelLayer.frompil(
            _gradient(i, 7, 5), psd, f"L{i}", top=i, left=2 * i, compression=compression
        )
        layer.opacity = [255, 128, 40][i]
        psd.append(layer)
    psd[1].visible = False
    _assert_same(_save(psd))


def test_groups_blend_and_clipping():
    psd = PSDImage.new("RGBA", (W, H))
    psd.append(PixelLayer.frompil(_gradient(10, W, H), psd, "bg"))
    group = Group.new(psd, "douga")
    for i in range(4):
        group.append(PixelLayer.frompil(_gradient(20 + i, 8, 6), group, f"c{i}", top=i, left=i))
    group[1].blend_mode = BlendMode.MULTIPLY
    group[2].opacity = 100
    group[3].visible = False
    clip = PixelLayer.frompil(_gradient(30, 6, 6), psd, "shadow", top=2, left=3)
    psd.append(clip)
    clip.clipping = True
    base = PixelLayer.frompil(_gradient(31, 9, 7), psd, "base", top=1, left=1)
    psd.append(base)
    shade = PixelLayer.frompil(_gradient(32, 5, 5), psd, "shade", top=2, left=2)
    psd.append(shade)
    shade.clipping = True
    shade.blend_mode = BlendMode.MULTIPLY
    _assert_same(_save(psd))


def test_nested_groups_and_masks():
    psd = PSDImage.new("RGBA", (W, H))
    outer = Group.new(psd, "outer")
    inner = Group.new(outer, "inner")
    inner.append(PixelLayer.frompil(_gradient(40, W, H), inner, "a"))
    screen = PixelLayer.frompil(_gradient(41, 6, 4), inner, "b", top=3, left=4)
    inner.append(screen)
    screen.blend_mode = BlendMode.SCREEN
    inner.opacity = 150
    inner.blend_mode = BlendMode.NORMAL
    outer.append(PixelLayer.frompil(_gradient(42, 5, 5), outer, "c", top=1, left=1))
    masked = PixelLayer.frompil(_gradient(43, W, H), psd, "masked")
    psd.append(masked)
    masked.create_mask(Image.fromarray(np.tile(np.arange(0, 256, 22, dtype=np.uint8), (H, 1))[:, :W], "L"))
    _assert_same(_save(psd))


def test_grayscale_document():
    psd = PSDImage.new("LA", (W, H))
    psd.append(PixelLayer.frompil(_gradient(50, 8, 8, "LA"), psd, "gray", top=1, left=2))
    _assert_same(_save(psd))


def test_pass_through_and_fill_opacity():
    psd = PSDImage.new("RGBA", (W, H))
    outer = Group.new(psd, "outer")
    outer.append(PixelLayer.frompil(_gradient(60, W, H), outer, "base"))
    passing = Group.new(outer, "pass")
    passing.blend_mode = BlendMode.PASS_THROUGH
    multiply = PixelLayer.frompil(_gradient(61, 7, 7), passing, "mul", top=1, left=2)
    passing.append(multiply)
    multiply.blend_mode = BlendMode.MULTIPLY
    multiply.fill_opacity = 90
    filled = Group.new(outer, "filled")
    filled.append(PixelLayer.frompil(_gradient(62, 6, 6), filled, "x", top=2, left=2))
    filled.fill_opacity = 120
    overlay = PixelLayer.frompil(_gradient(63, 5, 8), outer, "ov", top=0, left=5)
    outer.append(overlay)
    overlay.blend_mode = BlendMode.OVERLAY
    _assert_same(_save(psd))



def test_layers_without_transparency_channel():
    """色だけの（不透明度チャンネルのない）3チャンネルのレイヤー。psd-tools は形を
    文書全体の 1 とみなす（Qt と同じ絵になるか）。合成モードとグループ・単独の両方。"""
    rng = np.random.default_rng(41)
    psd = PSDImage.new("RGB", (W, H))
    psd.append(PixelLayer.frompil(_gradient(40, W, H), psd, "bg"))
    group = Group.new(psd, "g")
    for i, mode in enumerate([BlendMode.NORMAL, BlendMode.MULTIPLY, BlendMode.OVERLAY, BlendMode.SCREEN]):
        pixels = rng.integers(0, 256, size=(4 + i, 5, 3), dtype=np.uint8)
        layer = PixelLayer.frompil(Image.fromarray(pixels, "RGB"), group, f"rgb{i}", top=i, left=i + 1)
        layer.blend_mode = mode
        group.append(layer)
    psd.append(group)
    solo = PixelLayer.frompil(
        Image.fromarray(rng.integers(0, 256, size=(3, 6, 3), dtype=np.uint8), "RGB"),
        psd, "solo", top=2, left=3,
    )
    psd.append(solo)
    _assert_same(_save(psd))


def _assert_same_composite(data):
    """サブビューの表示（``subview._read_image`` の ``PSDImage.composite()``）と比べる。"""
    psd = PSDImage.open(io.BytesIO(data))
    want = np.asarray(psd.composite().convert("RGBA"), dtype=np.uint8).astype(int)
    width, height, got = import_composite_psd(data)
    assert (width, height) == (psd.width, psd.height)
    got = np.frombuffer(got, dtype=np.uint8).reshape(height, width, 4).astype(int)
    assert np.abs(got[..., 3] - want[..., 3]).max() <= 1
    shown = (got[..., 3] > 0) & (want[..., 3] > 0)
    colour = np.abs(got[..., :3] - want[..., :3])[shown]
    assert colour.size == 0 or colour.max() <= 1, colour.max()


def test_subview_composite_of_layers_and_groups():
    psd = PSDImage.new("RGBA", (W, H))
    psd.append(PixelLayer.frompil(_gradient(70, W, H), psd, "bg"))
    group = Group.new(psd, "g")
    group.append(PixelLayer.frompil(_gradient(71, 8, 6), group, "a", top=1, left=1))
    multiply = PixelLayer.frompil(_gradient(72, 6, 6), group, "b", top=2, left=3)
    group.append(multiply)
    multiply.blend_mode = BlendMode.MULTIPLY
    hidden = PixelLayer.frompil(_gradient(73, W, H), psd, "hidden")
    psd.append(hidden)
    hidden.visible = False
    _assert_same_composite(_save(psd))


def test_subview_composite_grayscale():
    psd = PSDImage.new("LA", (W, H))
    psd.append(PixelLayer.frompil(_gradient(74, 8, 8, "LA"), psd, "gray", top=1, left=2))
    _assert_same_composite(_save(psd))


@pytest.mark.parametrize("mode", ["RGBA", "LA"])
def test_subview_composite_merged_transparency(mode):
    """レイヤーのない文書（``has_transparency`` が真）は合成画像の余分の面を透明度にして、
    白地を引く（``_remove_white_background``。LA はグレースケールのとき）。"""
    _assert_same_composite(_save(PSDImage.frompil(_gradient(75, W, H, mode))))


def test_subview_composite_negative_layer_count():
    """レイヤー数が負なら、レイヤーがあっても余分の面が透明度（psd-tools の ``has_transparency``）。"""
    psd = PSDImage.new("RGBA", (W, H))
    image = _gradient(76, W, H)
    psd.append(PixelLayer.frompil(image, psd, "a"))
    psd = PSDImage.open(io.BytesIO(_save(psd)))
    psd._record.image_data.set_data([band.tobytes() for band in image.split()], psd._record.header)
    info = psd._record.layer_and_mask_information.layer_info
    info.layer_count = -abs(info.layer_count)
    buffer = io.BytesIO()
    psd._record.write(buffer)
    assert psd_tools.api.utils.has_transparency(PSDImage.open(io.BytesIO(buffer.getvalue())))
    _assert_same_composite(buffer.getvalue())
