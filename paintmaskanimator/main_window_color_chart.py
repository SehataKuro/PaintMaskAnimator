"""Main-window integration for the persistent colour chart."""
from typing import TYPE_CHECKING

import json
from pathlib import Path
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QFileDialog, QMessageBox
from .i18n import tr
from .color_chart import (
    empty_color_chart,
    merge_color_charts,
    normalize_color_chart,
    read_pmag,
    write_pmag,
)
from .logging_setup import get_logger


if TYPE_CHECKING:
    from .main_window import MainWindow

log = get_logger(__name__)


class ColorChartController:
    """Owned by ``MainWindow`` as ``window.color_chart_ops``.

    A collaborator rather than a mixin -- see main_window_export.py for why.
    """

    def __init__(self, window: "MainWindow"):
        self.window = window

    @staticmethod
    def _normalize(data):
        return normalize_color_chart(data)

    @staticmethod
    def _merge(existing, current):
        return merge_color_charts(existing, current)

    @staticmethod
    def _chart_rgb(value):
        color = QColor(str(value or ""))
        if not color.isValid():
            return None
        return color.red(), color.green(), color.blue()

    def set_data(self, chart):
        self.window.color_chart_data = normalize_color_chart(chart)
        self.window.color_chart.set_chart(self.window.color_chart_data)

    def clear(self):
        self.set_data(empty_color_chart())

    def on_edited(self, chart):
        self.window.color_chart_data = normalize_color_chart(chart)

    def _current_palette(self):
        child_tags = getattr(self.window.palette, "child_tags", {})
        parent_tags = getattr(self.window.palette, "parent_tags", {})
        tag_library = getattr(self.window.palette, "tag_library", ["Base"])
        tag_colors = getattr(self.window.palette, "tag_colors", {"Base": "#FFFFFF"})
        tag_order = getattr(self.window.palette, "tag_order", [])
        order = [
            self.window.palette._rgb_key(color) for color in self.window.palette.colors
            if self.window.palette._rgb_key(color) != self.window.palette.background_rgb
        ]
        parents = []
        for rgb in order:
            if rgb in self.window.palette.child_to_parent.values() and rgb not in parents:
                parents.append(rgb)
        for parent in self.window.palette.child_to_parent.values():
            if parent not in parents:
                parents.append(parent)

        tiles = []
        for index, parent in enumerate(parents, 1):
            children = [
                child for child in order
                if self.window.palette.child_to_parent.get(child) == parent
            ]
            children.extend(
                child for child, value in self.window.palette.child_to_parent.items()
                if value == parent and child not in children
            )
            tiles.append({
                "id": f"group_{index}",
                "name": "",
                "parent": {
                    "color": "#{:02X}{:02X}{:02X}".format(*parent),
                    "tag": parent_tags.get(parent, "Base") or "Base",
                },
                "placeholder": False,
                "children": [
                    {
                        "color": "#{:02X}{:02X}{:02X}".format(*child),
                        "tag": child_tags.get(child, ""),
                    }
                    for child in children
                ],
            })

        grouped = set(self.window.palette.child_to_parent)
        grouped.update(self.window.palette.child_to_parent.values())
        for rgb in order:
            tag = parent_tags.get(rgb, "")
            if rgb in grouped or not tag:
                continue
            tiles.append({
                "id": f"group_{len(tiles) + 1}",
                "name": "",
                "parent": {
                    "color": "#{:02X}{:02X}{:02X}".format(*rgb),
                    "tag": tag,
                },
                "placeholder": False,
                "children": [],
            })
        return normalize_color_chart({
            "format": "PaintMaskGroup",
            "format_version": 1,
            "tiles": tiles,
            "tag_library": list(tag_library),
            "tag_colors": dict(tag_colors),
            "tag_order": list(tag_order),
        })

    def capture(self):
        current = self._current_palette()
        if not current["tiles"]:
            QMessageBox.information(
                self.window,
                tr("カラーチャート"),
                tr("親子付けされた使用色がありません。\n"
                "使用色を別の色の中央へドロップして親子を作成してください。"),
            )
            return False
        before = normalize_color_chart(self.window.color_chart_data)
        before_count = len(before["groups"])
        chart = merge_color_charts(before, current)
        added = max(0, len(chart["groups"]) - before_count)
        self.set_data(chart)
        self.window.statusBar().showMessage(
            tr("現在の親子付けをカラーチャートへ登録しました（新規 {added}組）。").format(added=added),
            3000,
        )
        return True

    def apply(self):
        chart = normalize_color_chart(self.window.color_chart_data)
        if not chart["tiles"]:
            QMessageBox.information(self.window, tr("カラーチャート"), tr("適用するチャートがありません。"))
            return False

        available = {
            self.window.palette._rgb_key(color) for color in self.window.palette.colors
            if self.window.palette._rgb_key(color) != self.window.palette.background_rgb
        }
        child_to_parent = {}
        child_tags = {}
        parent_tags = {}
        desired_order = []
        rank = {tag: index for index, tag in enumerate(chart["tag_order"])}

        def append_order(rgb):
            if rgb in desired_order:
                desired_order.remove(rgb)
            desired_order.append(rgb)

        for tile in chart["tiles"]:
            parent = self._chart_rgb(tile["parent"].get("color"))
            parent_available = (
                not tile.get("placeholder")
                and parent is not None
                and parent in available
            )
            if parent_available:
                append_order(parent)
                parent_tag = str(tile["parent"].get("tag", ""))
                if parent_tag:
                    parent_tags[parent] = parent_tag
            children = sorted(tile["children"], key=lambda child: (
                rank.get(child.get("tag", ""), len(rank) + 1),
                str(child.get("tag", "")).casefold(),
                child.get("color", ""),
            ))
            for child in children:
                child_rgb = self._chart_rgb(child.get("color"))
                if child_rgb is None or child_rgb not in available:
                    continue
                append_order(child_rgb)
                child_tag = str(child.get("tag", ""))
                if parent_available and child_rgb != parent:
                    child_to_parent[child_rgb] = parent
                    if child_tag:
                        child_tags[child_rgb] = child_tag
                elif child_tag:
                    # NULL/absent parent: keep the colour standalone while
                    # retaining its role metadata.
                    parent_tags[child_rgb] = child_tag

        for color in self.window.palette.colors:
            rgb = self.window.palette._rgb_key(color)
            if rgb != self.window.palette.background_rgb and rgb not in desired_order:
                desired_order.append(rgb)

        self.window.palette.child_to_parent = child_to_parent
        self.window.palette.child_tags = child_tags
        self.window.palette.parent_tags = parent_tags
        self.window.palette.tag_library = list(chart["tag_library"])
        self.window.palette.tag_colors = dict(chart["tag_colors"])
        self.window.palette.tag_order = list(chart["tag_order"])

        by_rgb = {
            self.window.palette._rgb_key(color): QColor(color)
            for color in self.window.palette.colors
        }
        self.window.palette.colors = [QColor(*self.window.palette.background_rgb)] + [
            by_rgb[rgb] for rgb in desired_order if rgb in by_rgb
        ]
        self.window.palette._normalize_groups()
        self.window.palette._reorder_children_under_parents()
        self.window.palette._reapply_row_order()
        self.window.palette._refresh_all_group_displays()
        self.window.palette._refresh_used_color_styles()
        self.window.palette._emit_preview()
        self.window.palette.selectedColorsChanged.emit(set(self.window.palette.selected_rgbs))
        self.window.statusBar().showMessage(
            tr("現在の画像に存在する色へカラーチャートを適用しました。"),
            3000,
        )
        return True

    def save_pmag(self):
        chart = normalize_color_chart(self.window.color_chart_data)
        if not chart["tiles"] and not self.capture():
            return False
        path, _ = QFileDialog.getSaveFileName(
            self.window,
            tr("カラーチャートを保存"),
            "color_chart.pmag",
            "Paint Mask Group (*.pmag)",
        )
        if not path:
            return False
        if not path.lower().endswith(".pmag"):
            path = str(Path(path).with_suffix(".pmag"))
        try:
            write_pmag(path, self.window.color_chart_data)
        except (OSError, ValueError, TypeError) as exc:
            log.error("PMAG save failed: %s", exc, exc_info=True)
            QMessageBox.critical(self.window, tr("PMAG保存"), tr("保存できませんでした。\n\n{exc}").format(exc=exc))
            return False
        self.window.statusBar().showMessage(tr("PMAGを保存しました：{Path}").format(Path=Path(path).name), 3000)
        return True

    def load_pmag(self, path=None):
        if path is None:
            path, _ = QFileDialog.getOpenFileName(
                self.window,
                tr("カラーチャートを読み込み"),
                "",
                "Paint Mask Group (*.pmag)",
            )
        if not path:
            return False
        try:
            chart = read_pmag(path)
        except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
            log.error("PMAG load failed: %s", exc, exc_info=True)
            QMessageBox.critical(self.window, tr("PMAG読込"), tr("読み込めませんでした。\n\n{exc}").format(exc=exc))
            return False
        self.set_data(chart)
        self.window._set_color_chart_visible(True)
        self.window.statusBar().showMessage(tr("PMAGを読み込みました：{Path}").format(Path=Path(path).name), 3000)
        return True
