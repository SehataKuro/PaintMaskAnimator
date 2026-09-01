import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtGui import QColor
from PySide6.QtWidgets import QApplication

from paintmaskanimator.color_chart import (
    ColorChartPanel,
    move_chart_color,
    normalize_color_chart,
    read_pmag,
    write_pmag,
)
from paintmaskanimator.main_window import MainWindow


def _app():
    return QApplication.instance() or QApplication([])


def _chart():
    return normalize_color_chart({
        "format": "PaintMaskGroup",
        "tag_library": ["Base", "影", "HI"],
        "tag_colors": {
            "Base": "#ffffff", "影": "#334455", "HI": "#ddeeff"
        },
        "tag_order": ["HI", "影"],
        "tiles": [
            {
                "id": "character", "name": "人物",
                "parent": {"color": "#1e46c8", "tag": "Base"},
                "children": [
                    {"color": "#dc281e", "tag": "影"},
                    {"color": "#1eb446", "tag": "HI"},
                ],
            },
            {
                "id": "null_group", "name": "親なし",
                "parent": {"color": None, "tag": ""},
                "placeholder": True,
                "children": [{"color": "#9646be", "tag": "影"}],
            },
        ],
    })


def test_pmag_round_trip_keeps_tiles_tags_and_null_parent(tmp_path):
    chart = _chart()
    path = tmp_path / "chart.pmag"

    write_pmag(path, chart)
    restored = read_pmag(path)

    assert restored["format"] == "PaintMaskGroup"
    assert restored["tag_order"] == ["HI", "影"]
    assert restored["tiles"][0]["name"] == "人物"
    assert restored["tiles"][1]["placeholder"]
    assert restored["tiles"][1]["parent"]["color"] is None


def test_moving_parent_leaves_null_group_and_moving_child_outside_creates_group():
    chart = _chart()
    moved, changed = move_chart_color(
        chart,
        {"group_id": "character", "role": "parent", "index": -1},
        {"group_id": "null_group", "role": "parent", "index": -1},
    )
    assert changed
    old_group = next(tile for tile in moved["tiles"] if tile["id"] == "character")
    target = next(tile for tile in moved["tiles"] if tile["id"] == "null_group")
    assert old_group["placeholder"]
    assert target["parent"]["color"] == "#1E46C8"

    detached, changed = move_chart_color(
        moved,
        {"group_id": "character", "role": "child", "index": 0},
        None,
    )
    assert changed
    assert any(tile["parent"]["color"] == "#DC281E" for tile in detached["tiles"])


def test_chart_panel_builds_tag_tiles_and_keeps_base_first():
    app = _app()
    panel = ColorChartPanel()
    try:
        panel.set_chart(_chart())
        panel.show()
        app.processEvents()
        panel.tile_canvas.grab()

        assert panel.summary.text() == "2グループ・3子色"
        assert panel.tag_list.item(0).text() == "Base"
        assert len(panel.tile_canvas._color_hits) == 5
        assert panel.tile_canvas.groups[1]["placeholder"]
    finally:
        panel.close()
        app.processEvents()


def test_apply_chart_uses_only_colors_in_current_document(
    tmp_path, monkeypatch
):
    app = _app()
    monkeypatch.setenv("APPDATA", str(tmp_path))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    monkeypatch.setattr(MainWindow, "_maybe_restore_autosave", lambda self: None)
    window = MainWindow()
    try:
        red = (220, 40, 30)
        blue = (30, 70, 200)
        purple = (150, 70, 190)
        unregistered = (9, 8, 7)
        window.palette.set_colors([
            QColor(*red), QColor(*blue), QColor(*purple), QColor(*unregistered)
        ])
        chart = _chart()
        chart["tiles"].append({
            "id": "not_in_image", "name": "",
            "parent": {"color": "#123456", "tag": "Base"},
            "placeholder": False, "children": [],
        })
        window.set_color_chart_data(chart)

        assert window.apply_color_chart()
        assert window.palette.child_to_parent[red] == blue
        assert purple not in window.palette.child_to_parent
        assert window.palette.parent_tags[purple] == "影"
        assert (18, 52, 86) not in window.palette.source_buttons
        assert unregistered in window.palette.source_buttons
        assert window.build_project_metadata()["color_chart"]["tiles"]
    finally:
        window.close()
        app.processEvents()


def test_color_chart_is_embedded_in_pman_and_restored(tmp_path, monkeypatch):
    app = _app()
    monkeypatch.setenv("APPDATA", str(tmp_path))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    monkeypatch.setattr(MainWindow, "_maybe_restore_autosave", lambda self: None)
    window = MainWindow()
    project_path = tmp_path / "chart_round_trip.pman"
    try:
        window.set_color_chart_data(_chart())

        assert window.write_project(project_path)
        window.clear_color_chart()
        assert not window.color_chart_data["tiles"]

        assert window.open_project(project_path)
        assert len(window.color_chart_data["tiles"]) == 2
        assert window.color_chart_data["tiles"][0]["name"] == "人物"
        assert window.color_chart_data["tiles"][1]["placeholder"]
    finally:
        window.close()
        app.processEvents()
