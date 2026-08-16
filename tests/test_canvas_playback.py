import os
from types import SimpleNamespace

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QRectF  # noqa: E402
from PySide6.QtGui import QColor, QImage  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from paintmaskanimator.canvas_playback import PlaybackMixin  # noqa: E402


@pytest.fixture(scope="session")
def qapp():
    return QApplication.instance() or QApplication([])


def _layer(color="red", *, content=False, exposure=1, visible=True, opacity=1.0):
    image = QImage(2, 1, QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(QColor(color))
    return SimpleNamespace(
        has_content=content,
        exposure=exposure,
        visible=visible,
        opacity=opacity,
        image=image,
    )


class _Playback(PlaybackMixin):
    # The frames are ``SimpleNamespace`` stand-ins rather than real ``Frame``
    # objects, so the annotation from ``CanvasMembers`` is deliberately widened.
    frames: list

    def __init__(self):
        self.frames = []
        self.current_frame = 0
        self.active_layer_index = 0
        self.flip_horizontal = False
        self.silhouette_non_background = False
        self._playback_active = False
        self._playback_resolved_keys = []
        self._playback_frame_cache = {}
        self._playback_cache_limit = 2
        self.updates = 0
        self.resolutions = []
        self.display_calls = []

    def update(self):
        self.updates += 1

    def width(self):
        return 800

    def height(self):
        return 600

    def resolve_key_frame(self, frame, layer):
        self.resolutions.append((frame, layer))
        return frame

    def _display_layer_image(self, layer, layer_index):
        self.display_calls.append((layer, layer_index))
        return layer.image


def test_key_map_handles_empty_and_ragged_layers():
    playback = _Playback()
    playback._build_playback_key_map()
    assert playback._playback_resolved_keys == []

    playback.frames = [
        SimpleNamespace(layers=[_layer(content=True, exposure=3), _layer(content=True)]),
        SimpleNamespace(layers=[_layer()]),
        SimpleNamespace(layers=[_layer()]),
    ]
    playback._build_playback_key_map()
    assert playback._playback_resolved_keys == [[0, 0], [0, None], [0, None]]


def test_set_playback_active_rebuilds_and_clears_state():
    playback = _Playback()
    playback.frames = [SimpleNamespace(layers=[_layer(content=True)])]
    playback._playback_frame_cache["stale"] = object()
    playback.set_playback_active(True)
    assert playback._playback_active
    assert playback._playback_frame_cache == {}
    assert playback._playback_resolved_keys == [[0]]

    playback.set_playback_active(False)
    assert playback._playback_resolved_keys == []
    assert playback.updates == 2


def test_advance_ignores_empty_document_and_clamps_steps():
    playback = _Playback()
    playback.playback_advance()
    assert playback.updates == 0

    playback.frames = [object(), object(), object()]
    playback.current_frame = 1
    playback.playback_advance(0)
    assert playback.current_frame == 2
    playback.playback_advance(5)
    assert playback.current_frame == 1
    assert playback.updates == 2


def test_cache_dimensions_preserve_small_and_scale_large_targets():
    playback = _Playback()
    assert playback._playback_cache_dimensions(QRectF(0, 0, 100, 50)) == (100, 50)
    assert playback._playback_cache_dimensions(QRectF(0, 0, 4000, 2000)) == (1000, 500)
    assert playback._playback_cache_dimensions(QRectF(0, 0, 0, 0)) == (1, 1)


def test_frame_image_rejects_invalid_index_and_skips_layers(qapp):
    playback = _Playback()
    assert playback.playback_frame_image(0, QRectF(0, 0, 2, 1)) is None

    playback.frames = [SimpleNamespace(layers=[_layer(visible=False), _layer("blue")])]
    playback._playback_resolved_keys = [[0, None]]
    image = playback.playback_frame_image(0, QRectF(0, 0, 2, 1))
    assert image is not None
    assert not image.isNull()
    assert playback.display_calls == []


def test_frame_image_uses_fallback_resolution_flip_and_cache(qapp):
    playback = _Playback()
    layer = _layer("red", visible=True)
    # Distinguish the left and right pixels so mirroring is observable.
    layer.image.setPixelColor(0, 0, QColor("red"))
    layer.image.setPixelColor(1, 0, QColor("blue"))
    playback.frames = [SimpleNamespace(layers=[layer])]
    playback.flip_horizontal = True

    first = playback.playback_frame_image(0, QRectF(0, 0, 2, 1))
    assert playback.resolutions == [(0, 0)]
    assert first is not None
    assert first.pixelColor(0, 0) == QColor("blue")
    assert first.pixelColor(1, 0) == QColor("red")

    second = playback.playback_frame_image(0, QRectF(0, 0, 2, 1))
    assert second is first
    assert playback.resolutions == [(0, 0)]


def test_frame_cache_evicts_oldest_entry(qapp):
    playback = _Playback()
    playback._playback_cache_limit = 1
    playback.frames = [SimpleNamespace(layers=[_layer(content=True)])]
    playback._playback_resolved_keys = [[0]]
    playback.playback_frame_image(0, QRectF(0, 0, 2, 1))
    first_key = next(iter(playback._playback_frame_cache))

    playback.playback_frame_image(0, QRectF(0, 0, 3, 1))
    assert len(playback._playback_frame_cache) == 1
    assert first_key not in playback._playback_frame_cache
