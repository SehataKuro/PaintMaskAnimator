"""ツールアイコンの受け入れ条件（Issue #30）。"""
import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtGui import QColor  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from paintmaskanimator import icons, theme, toolpanel  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


TOOL_IDS = [tool_id for tool_id, _label in toolpanel.TOOL_DEFINITIONS] + ["line"]


@pytest.mark.parametrize("tool_id", TOOL_IDS)
def test_every_tool_has_an_icon(qapp, tool_id):
    assert not icons.icon(tool_id).isNull()


@pytest.mark.parametrize("tool_id", TOOL_IDS)
def test_icons_follow_the_theme_colour(qapp, tool_id):
    """暗い背景で沈まないよう、前景色が固定されていないこと。"""
    light = icons.pixmap(tool_id, 24, color="#101010").toImage()
    dark = icons.pixmap(tool_id, 24, color="#f0f0f0").toImage()

    assert light != dark


@pytest.mark.parametrize("tool_id", TOOL_IDS)
def test_icons_are_rendered_for_high_dpi(qapp, tool_id):
    """200%表示でぼやけないよう、倍率ぶんの画素を持つこと。"""
    retina = icons.pixmap(tool_id, 24, device_pixel_ratio=2.0)

    assert retina.width() == 48 and retina.height() == 48
    assert retina.devicePixelRatio() == 2.0


def test_the_tools_that_looked_alike_are_now_distinct(qapp):
    """投げ縄選択と投げ縄塗りが、塗りの有無だけの違いではないこと。"""
    lasso = icons.pixmap("lasso", 32, color="#000000", accent="#000000").toImage()
    lasso_fill = icons.pixmap(
        "lasso_fill", 32, color="#000000", accent="#000000"
    ).toImage()

    assert lasso != lasso_fill
    # 塗りつぶしを無視しても形が違う（＝輪郭そのものが違う）。
    filled = sum(
        lasso_fill.pixelColor(x, y).alpha() > 0
        for x in range(32) for y in range(32)
    )
    plain = sum(
        lasso.pixelColor(x, y).alpha() > 0
        for x in range(32) for y in range(32)
    )
    assert filled > plain


def test_the_theme_palette_supplies_the_default_colour(qapp):
    expected = QColor(theme.palette()["text"]).name()
    assert (
        icons.pixmap("shape", 24).toImage()
        == icons.pixmap("shape", 24, color=expected).toImage()
    )
