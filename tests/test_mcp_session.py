"""MCP のヘッドレスセッション（``paintmaskanimator.mcp.session``）のテスト。

``mcp`` パッケージを入れていなくても動く層なので、サーバーを起動せずに
「何が見えて、何が書けるか」だけをここで固定する。
"""
from __future__ import annotations

import pytest
from PySide6.QtGui import QColor, QImage
from PySide6.QtWidgets import QApplication

from paintmaskanimator import constants, project_io
from paintmaskanimator.mcp.session import ProjectSession, ReadOnlyError
from paintmaskanimator.models import make_frame


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def _metadata(**extra):
    data = {
        "format": "PaintMaskAnimatorProject",
        "format_version": 1,
        "canvas": {
            "width": int(constants.CANVAS_WIDTH),
            "height": int(constants.CANVAS_HEIGHT),
        },
        "current_frame": 0,
        "active_layer_index": 0,
        "frames": [],
    }
    data.update(extra)
    return data


def draw_outline(layer, origin=(4, 4), size=8):
    """白地に閉じた黒枠を1つ描く。枠の内側が1つの閉領域になる。"""
    layer.image.fill(QColor("white"))
    x0, y0 = origin
    x1, y1 = x0 + size, y0 + size
    black = QColor("black")
    for x in range(x0, x1):
        layer.image.setPixelColor(x, y0, black)
        layer.image.setPixelColor(x, y1 - 1, black)
    for y in range(y0, y1):
        layer.image.setPixelColor(x0, y, black)
        layer.image.setPixelColor(x1 - 1, y, black)
    layer.has_content = True


@pytest.fixture
def project(qapp, tmp_path):
    frames = [make_frame(), make_frame()]
    for frame in frames:
        draw_outline(frame.layers[0])
    frames[0].layers[0].name = "A"
    frames[1].duration = 3
    path = tmp_path / "sample.pma"
    project_io.write_project_archive(
        path,
        _metadata(color_chart={"tag_library": ["Base", "肌"]}),
        frames,
    )
    return path


def opened(project, **kwargs):
    session = ProjectSession(**kwargs)
    session.open(project)
    return session


def test_describe_reports_structure(project):
    session = opened(project)
    info = session.describe()

    assert info["frame_count"] == 2
    assert info["layer_count"] == 1
    assert info["layers"][0]["name"] == "A"
    assert info["total_duration"] == 4
    assert info["writable"] is False
    assert info["canvas"]["width"] == constants.CANVAS_WIDTH


def test_list_frames_exposes_timeline_attributes(project):
    session = opened(project)
    rows = session.list_frames()

    assert [row["index"] for row in rows] == [0, 1]
    assert rows[1]["duration"] == 3
    layer = rows[0]["layers"][0]
    assert layer["visible"] is True
    assert layer["has_content"] is True
    assert layer["key_frame"] == 0


def test_color_chart_comes_back_normalised(project):
    session = opened(project)
    chart = session.color_chart()

    assert "肌" in chart["tag_library"]
    assert chart["tag_library"][0] == "Base", "Base は常に先頭へ寄せられる"


def test_render_png_produces_a_decodable_image(project):
    session = opened(project)

    data = session.render_png(0)

    image = QImage.fromData(data, "PNG")
    assert not image.isNull()
    assert image.width() == constants.CANVAS_WIDTH
    assert image.pixelColor(4, 4) == QColor("black")


def test_render_png_honours_max_size(project):
    session = opened(project)

    image = QImage.fromData(session.render_png(0, max_size=320), "PNG")

    assert max(image.width(), image.height()) == 320


def test_list_regions_finds_the_inside_of_the_outline(project):
    session = opened(project)

    regions = session.list_regions(0, 0, min_area=4)

    inside = [r for r in regions if r.bbox == (5, 5, 6, 6)]
    assert inside, f"枠の内側が1領域として出るはず: {[r.bbox for r in regions]}"
    assert inside[0].area == 36
    assert inside[0].color is None, "未塗りの領域は color=None"
    # 種はその領域に含まれるので、そのまま塗りに渡せる。
    x, y = inside[0].seed
    assert 5 <= x < 11 and 5 <= y < 11


def test_list_regions_reports_painted_colour(project):
    session = opened(project, allow_write=True)
    session.apply_region_colors(
        [{"frame": 0, "layer": 0, "seed": [8, 8], "color": "#3355FF"}]
    )

    regions = session.list_regions(0, 0, min_area=4)

    painted = [r for r in regions if r.color == "#3355FF"]
    assert painted and painted[0].area == 36


def test_apply_region_colors_writes_only_inside_the_outline(project):
    session = opened(project, allow_write=True)

    report = session.apply_region_colors(
        [{"frame": 0, "layer": 0, "seed": [8, 8], "color": "#3355FF"}]
    )

    assert report["applied"][0]["pixels"] == 36
    assert not report["skipped"]
    layer = session.frames[0].layers[0]
    assert layer.image.pixelColor(8, 8) == QColor("#3355FF")
    assert layer.image.pixelColor(4, 4) == QColor("black"), "線は塗らない"
    # 読み込み時に白は透明へ移行される（mask_format 2）。枠の外は元のまま。
    assert layer.image.pixelColor(0, 0).alpha() == 0, "外へ漏れない"
    # 別コマは触っていない。
    assert session.frames[1].layers[0].image.pixelColor(8, 8).alpha() == 0


def test_apply_region_colors_reports_skips_without_failing(project):
    session = opened(project, allow_write=True)

    report = session.apply_region_colors(
        [
            {"frame": 0, "layer": 0, "seed": [8, 8], "color": "#3355FF"},
            # 同じ場所をもう一度。既に塗られているので同色では変化しない領域だが、
            # 判定自体は通るので applied に入る。範囲外の座標は skipped になる。
            {"frame": 0, "layer": 0, "seed": [-5, -5], "color": "#3355FF"},
        ]
    )

    assert len(report["applied"]) == 1
    assert report["skipped"][0]["reason"] == "out_of_bounds"


def test_read_only_session_refuses_to_write(project):
    session = opened(project)

    with pytest.raises(ReadOnlyError):
        session.apply_region_colors(
            [{"frame": 0, "layer": 0, "seed": [8, 8], "color": "#000000"}]
        )
    with pytest.raises(ReadOnlyError):
        session.save()


def test_bad_colour_aborts_before_any_pixel_is_written(project):
    session = opened(project, allow_write=True)
    before = session.frames[0].layers[0].image.copy()

    with pytest.raises(ValueError):
        session.apply_region_colors(
            [
                {"frame": 0, "layer": 0, "seed": [8, 8], "color": "#3355FF"},
                {"frame": 0, "layer": 0, "seed": [8, 8], "color": "not-a-colour"},
            ]
        )

    assert session.frames[0].layers[0].image == before


def test_save_defaults_to_a_new_file(project):
    session = opened(project, allow_write=True)
    original = project.read_bytes()
    session.apply_region_colors(
        [{"frame": 0, "layer": 0, "seed": [8, 8], "color": "#3355FF"}]
    )

    report = session.save()

    assert report["saved_to"].endswith("sample_mcp.pma")
    assert project.read_bytes() == original, "元のファイルは上書きしない"
    reopened = ProjectSession()
    reopened.open(report["saved_to"])
    assert reopened.frames[0].layers[0].image.pixelColor(8, 8) == QColor("#3355FF")


def test_out_of_range_indices_are_rejected(project):
    session = opened(project)

    with pytest.raises(IndexError):
        session.list_frames() and session.composite(9)
    with pytest.raises(IndexError):
        session.list_regions(0, 9)


def test_operations_require_an_open_project(qapp):
    session = ProjectSession()

    with pytest.raises(RuntimeError):
        session.describe()
