"""Optional-dependency degrade paths.

Pillow and psd-tools are declared optional (see requirements.txt): the app must
keep working with them absent, only disabling the PIL/PSD-backed features. CI
installs them, so the degrade branches would otherwise never be exercised. These
tests force the "missing library" state by patching the module-level handles to
``None`` — exactly the value ``optional_deps`` binds when the import fails."""
from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402
from PySide6.QtGui import QColor, QImage  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from paintmaskanimator import imaging, optional_deps  # noqa: E402
from paintmaskanimator.canvas import PaintCanvas  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def _solid_qimage(color=None):
    color = QColor(10, 20, 30, 255) if color is None else color
    image = QImage(4, 3, QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(color)
    return image


def test_optional_deps_binds_none_when_optional_imports_fail():
    # The whole degrade design hinges on these being ``None``-able sentinels.
    assert "PILImage" in vars(optional_deps)
    assert "PSDImage" in vars(optional_deps)


def test_qimage_to_pil_rgba_returns_none_without_pillow(qapp, monkeypatch):
    monkeypatch.setattr(imaging, "PILImage", None)
    assert imaging.qimage_to_pil_rgba(_solid_qimage()) is None


def test_qimage_to_pil_rgba_roundtrips_with_pillow(qapp):
    if imaging.PILImage is None:
        pytest.skip("Pillow not installed in this environment")
    source = _solid_qimage(QColor(200, 100, 50, 255))
    pil = imaging.qimage_to_pil_rgba(source)
    assert pil is not None
    restored = imaging.pil_rgba_to_qimage(pil)
    assert restored.pixelColor(0, 0) == QColor(200, 100, 50, 255)


def test_image_import_falls_back_to_qt_reader_without_pillow(
    qapp, monkeypatch, tmp_path
):
    # Force the "Pillow missing" state so the PIL fallback branch is skipped and
    # the Qt reader alone must decode the file.
    monkeypatch.setattr(optional_deps, "PILImage", None)
    import paintmaskanimator.canvas_image_import as cii

    monkeypatch.setattr(cii, "PILImage", None)

    png = tmp_path / "sample.png"
    _solid_qimage(QColor(1, 2, 3, 255)).save(str(png))

    canvas = PaintCanvas()
    try:
        image, error = canvas._read_image_file(str(png))
        assert error == ""
        assert image is not None
        assert not image.isNull()
        assert (image.width(), image.height()) == (4, 3)
    finally:
        canvas.deleteLater()
