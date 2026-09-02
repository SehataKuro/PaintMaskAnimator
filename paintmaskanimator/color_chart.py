"""Editable colour-chart window and Paint Mask Group (``.pmag``) data.

The chart is deliberately separate from the live used-colour list.  It stores
planned parent/child relationships and role tags, then applies only the colours
that actually exist in the current document when the user asks it to.
"""
from __future__ import annotations

import copy
import json
import math
import re
from pathlib import Path

from PySide6.QtCore import QEvent, QMimeData, QPointF, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QDrag, QPainter, QPen
from PySide6.QtWidgets import (
    QColorDialog,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QToolButton,
    QVBoxLayout,
    QWidget,
)


PMAG_FORMAT = "PaintMaskGroup"
PMAG_VERSION = 1
MAX_CHART_TILES = 1024
MAX_CHART_CHILDREN = 4096
MAX_TAGS = 1024
TAG_MIME = "application/x-paintmaskanimator-color-tag"


def _hex_color(value):
    color = QColor(str(value or ""))
    if not color.isValid():
        return None
    return color.name(QColor.NameFormat.HexRgb).upper()


def _tag(value):
    return str(value or "").strip()[:32]


def _unique_id(value, used, fallback_index):
    base = re.sub(r"[^A-Za-z0-9_-]+", "_", str(value or "")).strip("_")
    base = base[:64] or f"group_{fallback_index}"
    candidate = base
    suffix = 2
    while candidate in used:
        candidate = f"{base}_{suffix}"
        suffix += 1
    used.add(candidate)
    return candidate


def empty_color_chart():
    return {
        "format": PMAG_FORMAT,
        "format_version": PMAG_VERSION,
        "groups": [],
        "parent_tags": [],
        "tag_library": ["Base"],
        "tag_colors": {"Base": "#FFFFFF"},
        "group_names": {},
        "tag_order": [],
        "tiles": [],
    }


def normalize_color_chart(data):
    """Return a bounded canonical chart, accepting every old PMAG variant."""
    source = dict(data or {}) if isinstance(data, dict) else {}
    raw_colors = source.get("tag_colors", {})
    tag_colors = {"Base": "#FFFFFF"}
    if isinstance(raw_colors, dict):
        for raw_name, raw_color in list(raw_colors.items())[:MAX_TAGS]:
            name = _tag(raw_name)
            color = _hex_color(raw_color)
            if name and color:
                tag_colors[name] = color

    raw_library = source.get("tag_library", [])
    library = []
    if isinstance(raw_library, list):
        for value in raw_library[:MAX_TAGS]:
            name = _tag(value)
            if name and name not in library:
                library.append(name)
    if "Base" in library:
        library.remove("Base")
    library.insert(0, "Base")

    raw_order = source.get("tag_order", [])
    order = []
    if isinstance(raw_order, list):
        for value in raw_order[:MAX_TAGS]:
            name = _tag(value)
            if name and name.casefold() != "base" and name not in order:
                order.append(name)

    raw_tiles = source.get("tiles")
    tiles = []
    used_ids = set()
    total_children = 0
    if isinstance(raw_tiles, list):
        for index, raw_tile in enumerate(raw_tiles[:MAX_CHART_TILES], 1):
            if not isinstance(raw_tile, dict):
                continue
            raw_parent = raw_tile.get("parent")
            if raw_parent is None:
                raw_parent = {}
            if not isinstance(raw_parent, dict):
                continue
            parent_color = _hex_color(raw_parent.get("color"))
            placeholder = bool(raw_tile.get("placeholder")) or parent_color is None
            parent_tag = "" if placeholder else (_tag(raw_parent.get("tag")) or "Base")
            children = []
            seen_children = set()
            raw_children = raw_tile.get("children", [])
            if not isinstance(raw_children, list):
                raw_children = []
            for raw_child in raw_children:
                if total_children >= MAX_CHART_CHILDREN:
                    break
                if not isinstance(raw_child, dict):
                    continue
                child_color = _hex_color(raw_child.get("color"))
                child_tag = _tag(raw_child.get("tag"))
                if not child_color or child_color in seen_children:
                    continue
                if not placeholder and child_color == parent_color:
                    continue
                children.append({"color": child_color, "tag": child_tag})
                seen_children.add(child_color)
                total_children += 1
            if placeholder and not children:
                continue
            tile_id = _unique_id(raw_tile.get("id"), used_ids, index)
            tiles.append({
                "id": tile_id,
                "name": str(raw_tile.get("name", "")).strip()[:64],
                "parent": {"color": None if placeholder else parent_color, "tag": parent_tag},
                "placeholder": placeholder,
                "children": children,
            })
    else:
        # Convert the original flat relationship representation.
        raw_groups = source.get("groups", [])
        if not isinstance(raw_groups, list):
            raw_groups = []
        raw_parent_tags = source.get("parent_tags", [])
        if not isinstance(raw_parent_tags, list):
            raw_parent_tags = []
        parent_tags = {}
        for entry in raw_parent_tags[:MAX_CHART_TILES]:
            if not isinstance(entry, dict):
                continue
            parent = _hex_color(entry.get("parent"))
            name = _tag(entry.get("tag"))
            if parent and name:
                parent_tags[parent] = name
        raw_names = source.get("group_names", {})
        group_names = raw_names if isinstance(raw_names, dict) else {}
        by_parent = {}
        parent_order = []
        for entry in raw_groups[:MAX_CHART_CHILDREN]:
            if not isinstance(entry, dict):
                continue
            parent = _hex_color(entry.get("parent"))
            child = _hex_color(entry.get("child"))
            if not parent or not child or parent == child:
                continue
            if parent not in by_parent:
                by_parent[parent] = []
                parent_order.append(parent)
            if child not in {item["color"] for item in by_parent[parent]}:
                by_parent[parent].append({"color": child, "tag": _tag(entry.get("tag"))})
        standalone = source.get("standalone", [])
        if not isinstance(standalone, list):
            standalone = []
        for parent in parent_order:
            tile_id = _unique_id("", used_ids, len(tiles) + 1)
            tiles.append({
                "id": tile_id,
                "name": str(group_names.get(parent, "")).strip()[:64],
                "parent": {"color": parent, "tag": parent_tags.get(parent, "Base")},
                "placeholder": False,
                "children": by_parent[parent],
            })
        for entry in standalone[:MAX_CHART_TILES - len(tiles)]:
            if not isinstance(entry, dict):
                continue
            color = _hex_color(entry.get("color"))
            if not color:
                continue
            tile_id = _unique_id("", used_ids, len(tiles) + 1)
            tiles.append({
                "id": tile_id,
                "name": "",
                "parent": {"color": color, "tag": _tag(entry.get("tag")) or "Base"},
                "placeholder": False,
                "children": [],
            })

    groups = []
    parent_tags_out = []
    group_names_out = {}
    seen_parent_tags = set()
    for tile in tiles:
        parent = tile["parent"]["color"]
        parent_tag = tile["parent"]["tag"]
        if parent:
            if tile["name"]:
                group_names_out[parent] = tile["name"]
            if parent_tag and parent not in seen_parent_tags:
                parent_tags_out.append({"parent": parent, "tag": parent_tag})
                seen_parent_tags.add(parent)
            for child in tile["children"]:
                groups.append({
                    "child": child["color"],
                    "parent": parent,
                    "tag": child["tag"],
                })
        if parent_tag and parent_tag not in library:
            library.append(parent_tag)
        for child in tile["children"]:
            name = child["tag"]
            if name and name not in library:
                library.append(name)

    for name in list(tag_colors):
        if name not in library:
            library.append(name)
    for name in library:
        tag_colors.setdefault(name, "#FFFFFF" if name == "Base" else "#58667A")
    for name in library:
        if name.casefold() != "base" and name not in order:
            order.append(name)

    return {
        "format": PMAG_FORMAT,
        "format_version": PMAG_VERSION,
        "groups": groups,
        "parent_tags": parent_tags_out,
        "tag_library": library[:MAX_TAGS],
        "tag_colors": {name: tag_colors[name] for name in library[:MAX_TAGS]},
        "group_names": group_names_out,
        "tag_order": order[:MAX_TAGS],
        "tiles": tiles,
    }


def merge_color_charts(existing, current):
    """Append new palette relationships without losing earlier colour variants."""
    old = normalize_color_chart(existing)
    new = normalize_color_chart(current)
    merged = copy.deepcopy(old)
    by_parent = {
        tile["parent"]["color"]: tile
        for tile in merged["tiles"]
        if not tile["placeholder"] and tile["parent"]["color"]
    }
    for source_tile in new["tiles"]:
        parent = source_tile["parent"]["color"]
        target = by_parent.get(parent) if parent else None
        if target is None:
            added = copy.deepcopy(source_tile)
            added["id"] = _unique_id(
                added.get("id"), {tile["id"] for tile in merged["tiles"]},
                len(merged["tiles"]) + 1,
            )
            merged["tiles"].append(added)
            if parent:
                by_parent[parent] = added
            continue
        existing_children = {child["color"] for child in target["children"]}
        for child in source_tile["children"]:
            if child["color"] not in existing_children:
                target["children"].append(copy.deepcopy(child))
                existing_children.add(child["color"])
        if source_tile["parent"]["tag"]:
            target["parent"]["tag"] = source_tile["parent"]["tag"]
        if source_tile["name"]:
            target["name"] = source_tile["name"]
    merged["tag_library"] = list(dict.fromkeys(
        [*old["tag_library"], *new["tag_library"]]
    ))
    merged["tag_colors"] = {**old["tag_colors"], **new["tag_colors"]}
    merged["tag_order"] = list(dict.fromkeys(
        [*old["tag_order"], *new["tag_order"]]
    ))
    return normalize_color_chart(merged)


def _tile(chart, group_id):
    return next((item for item in chart["tiles"] if item["id"] == group_id), None)


def move_chart_color(data, source_hit, target_hit=None):
    """Move one colour occurrence between chart tiles and return a new chart."""
    chart = normalize_color_chart(data)
    source = _tile(chart, source_hit.get("group_id"))
    if source is None:
        return chart, False
    role = source_hit.get("role")
    if role == "parent":
        if source.get("placeholder"):
            return chart, False
        moved = dict(source["parent"])
        if source["children"]:
            source["parent"] = {"color": None, "tag": ""}
            source["placeholder"] = True
        else:
            chart["tiles"].remove(source)
    elif role == "child":
        index = int(source_hit.get("index", -1))
        if index < 0 or index >= len(source["children"]):
            return chart, False
        moved = source["children"].pop(index)
        if source.get("placeholder") and not source["children"]:
            chart["tiles"].remove(source)
    else:
        return chart, False

    target = _tile(chart, target_hit.get("group_id")) if target_hit else None
    if target is None or target_hit is None:
        used = {item["id"] for item in chart["tiles"]}
        chart["tiles"].append({
            "id": _unique_id("", used, len(chart["tiles"]) + 1),
            "name": "",
            "parent": {
                "color": moved["color"],
                "tag": moved.get("tag") or "Base",
            },
            "placeholder": False,
            "children": [],
        })
        return normalize_color_chart(chart), True

    target_role = target_hit.get("role")
    if target_role == "parent" and target.get("placeholder"):
        target["parent"] = {
            "color": moved["color"],
            "tag": moved.get("tag") or "Base",
        }
        target["placeholder"] = False
    else:
        # A parent dropped on another group becomes a child.  Base describes a
        # parent role, so do not carry it into a child role.
        child = {
            "color": moved["color"],
            "tag": "" if moved.get("tag") == "Base" else moved.get("tag", ""),
        }
        target["children"] = [
            item for item in target["children"]
            if item["color"] != child["color"]
        ]
        if target_role == "child":
            insert_at = max(0, min(
                int(target_hit.get("index", len(target["children"]))),
                len(target["children"]),
            ))
            target["children"].insert(insert_at, child)
        else:
            target["children"].append(child)
    return normalize_color_chart(chart), True


def assign_chart_tag(data, hit, tag_name):
    chart = normalize_color_chart(data)
    tile = _tile(chart, hit.get("group_id"))
    name = _tag(tag_name)
    if tile is None or (hit.get("role") == "parent" and tile.get("placeholder")):
        return chart, False
    if hit.get("role") == "parent":
        tile["parent"]["tag"] = name or "Base"
    elif hit.get("role") == "child":
        index = int(hit.get("index", -1))
        if index < 0 or index >= len(tile["children"]):
            return chart, False
        tile["children"][index]["tag"] = name
    else:
        return chart, False
    if name and name not in chart["tag_library"]:
        chart["tag_library"].append(name)
        chart["tag_colors"][name] = "#58667A"
    return normalize_color_chart(chart), True


class ColorTagDialog(QDialog):
    def __init__(self, name="", color="#58667A", *, lock_name=False, parent=None):
        super().__init__(parent)
        self.setWindowTitle("カラーチャートのタグ")
        self._color = QColor(color)
        if not self._color.isValid():
            self._color = QColor("#58667A")
        layout = QVBoxLayout(self)
        form = QFormLayout()
        self.name_edit = QLineEdit(str(name))
        self.name_edit.setMaxLength(32)
        self.name_edit.setEnabled(not lock_name)
        self.color_button = QPushButton()
        self.color_button.clicked.connect(self._choose_color)
        self._refresh_color_button()
        form.addRow("タグ名", self.name_edit)
        form.addRow("タグ色", self.color_button)
        layout.addLayout(form)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _choose_color(self):
        color = QColorDialog.getColor(self._color, self, "タグ色")
        if color.isValid():
            self._color = color
            self._refresh_color_button()

    def _refresh_color_button(self):
        value = self._color.name(QColor.NameFormat.HexRgb).upper()
        self.color_button.setText(value)
        self.color_button.setStyleSheet(f"background:{value};")

    def values(self):
        return _tag(self.name_edit.text()), self._color.name(
            QColor.NameFormat.HexRgb
        ).upper()


class TagListWidget(QListWidget):
    orderChanged = Signal(object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setDragEnabled(True)
        self.setAcceptDrops(True)
        self.setDropIndicatorShown(True)
        self.setSelectionMode(QListWidget.SelectionMode.SingleSelection)

    def startDrag(self, _supported_actions):
        item = self.currentItem()
        if item is None:
            return
        mime = QMimeData()
        mime.setData(TAG_MIME, item.text().encode("utf-8"))
        drag = QDrag(self)
        drag.setMimeData(mime)
        drag.exec(Qt.DropAction.MoveAction | Qt.DropAction.CopyAction)

    def dragEnterEvent(self, event):
        if event.mimeData().hasFormat(TAG_MIME):
            event.acceptProposedAction()
            return
        super().dragEnterEvent(event)

    def dragMoveEvent(self, event):
        if event.mimeData().hasFormat(TAG_MIME):
            event.acceptProposedAction()
            return
        super().dragMoveEvent(event)

    def dropEvent(self, event):
        if not event.mimeData().hasFormat(TAG_MIME):
            super().dropEvent(event)
            return
        name = bytes(event.mimeData().data(TAG_MIME)).decode("utf-8", "replace")
        source_row = next(
            (row for row in range(self.count()) if self.item(row).text() == name),
            -1,
        )
        target_item = self.itemAt(event.position().toPoint())
        target_row = self.row(target_item) if target_item is not None else self.count()
        if source_row > 0:  # Base remains first.
            item = self.takeItem(source_row)
            if source_row < target_row:
                target_row -= 1
            self.insertItem(max(1, target_row), item)
            self.setCurrentItem(item)
            self.orderChanged.emit([
                self.item(row).text() for row in range(1, self.count())
            ])
        event.acceptProposedAction()


class ColorChartCanvas(QWidget):
    colorPicked = Signal(QColor)
    tileMoveRequested = Signal(object, object)
    tagDropRequested = Signal(object, str)
    tileTagEditRequested = Signal(object)
    groupNameChanged = Signal(str, str)

    TILE_WIDTH = 184
    TILE_HEIGHT = 112
    HEADER_HEIGHT = 24
    GAP = 8
    COLUMNS = 3

    def __init__(self, parent=None):
        super().__init__(parent)
        self.chart = empty_color_chart()
        self.groups = []
        self.zoom = 1.0
        self.scroll_area = None
        self._color_hits = []
        self._header_hits = []
        self._drag_mode = ""
        self._drag_start = QPointF()
        self._drag_last = QPointF()
        self._drag_scroll = (0, 0)
        self._drag_zoom = 1.0
        self._pressed_hit = None
        self._space_down = False
        self._ctrl_down = False
        self._name_editor = None
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setMouseTracking(True)
        self.setAcceptDrops(True)
        self._update_canvas_size()
        self._update_mode_cursor()

    def set_chart(self, chart):
        self.chart = normalize_color_chart(chart)
        rank = {name: index for index, name in enumerate(self.chart["tag_order"])}
        self.groups = []
        for tile in self.chart["tiles"]:
            children = list(enumerate(tile["children"]))
            children.sort(key=lambda pair: (
                rank.get(pair[1]["tag"], len(rank) + 1),
                pair[1]["tag"].casefold(),
                pair[1]["color"],
            ))
            self.groups.append({
                "id": tile["id"],
                "name": tile["name"],
                "parent": tile["parent"]["color"],
                "parent_tag": "未設定" if tile["placeholder"] else tile["parent"]["tag"],
                "placeholder": tile["placeholder"],
                "children": children,
            })
        self._update_canvas_size()
        self.update()

    def _base_size(self):
        count = max(1, len(self.groups))
        columns = min(self.COLUMNS, count)
        rows = max(1, int(math.ceil(count / self.COLUMNS)))
        return (
            columns * self.TILE_WIDTH + (columns + 1) * self.GAP,
            rows * (self.HEADER_HEIGHT + self.TILE_HEIGHT) + (rows + 1) * self.GAP,
        )

    def _update_canvas_size(self):
        width, height = self._base_size()
        size = QSize(max(1, math.ceil(width * self.zoom)), max(1, math.ceil(height * self.zoom)))
        self.setMinimumSize(size)
        self.resize(size)

    @staticmethod
    def _text_color(color):
        brightness = (color.red() * 299 + color.green() * 587 + color.blue() * 114) / 1000
        return QColor("#111111" if brightness >= 145 else "#FFFFFF")

    @staticmethod
    def _draw_checker(painter, rect, size=10):
        left, top = int(rect.left()), int(rect.top())
        right, bottom = int(math.ceil(rect.right())), int(math.ceil(rect.bottom()))
        for y in range(top, bottom, size):
            for x in range(left, right, size):
                color = QColor("#EEEEEE") if ((x - left) // size + (y - top) // size) % 2 == 0 else QColor("#B8B8B8")
                painter.fillRect(QRectF(x, y, size, size), color)

    def paintEvent(self, _event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, False)
        painter.scale(self.zoom, self.zoom)
        self._color_hits = []
        self._header_hits = []
        for position, group in enumerate(self.groups):
            row, column = divmod(position, self.COLUMNS)
            x = self.GAP + column * (self.TILE_WIDTH + self.GAP)
            y = self.GAP + row * (self.HEADER_HEIGHT + self.TILE_HEIGHT + self.GAP)
            header = QRectF(x, y, self.TILE_WIDTH, self.HEADER_HEIGHT)
            painter.fillRect(header, QColor("#E6E6E6"))
            painter.setPen(QColor("#777777"))
            painter.drawRect(header)
            painter.setPen(QColor("#222222"))
            painter.drawText(header.adjusted(4, 1, -4, -1), Qt.AlignmentFlag.AlignCenter, group["name"] or "（グループ名）")
            self._header_hits.append({"rect": QRectF(header), "group_id": group["id"]})

            body_y = y + self.HEADER_HEIGHT
            parent_rect = QRectF(x, body_y, self.TILE_WIDTH / 2, self.TILE_HEIGHT)
            if group["placeholder"]:
                self._draw_checker(painter, parent_rect)
                parent_color = QColor("#C8C8C8")
            else:
                parent_color = QColor(group["parent"])
                painter.fillRect(parent_rect, parent_color)
            painter.setPen(QPen(QColor(0, 0, 0, 110), 1))
            painter.drawRect(parent_rect)
            painter.setPen(QColor("#303030") if group["placeholder"] else self._text_color(parent_color))
            painter.drawText(parent_rect.adjusted(3, 3, -3, -3), Qt.AlignmentFlag.AlignCenter | Qt.TextFlag.TextWordWrap, group["parent_tag"])
            self._color_hits.append({
                "rect": QRectF(parent_rect), "group_id": group["id"],
                "role": "parent", "index": -1, "color": group["parent"] or "",
                "tag": group["parent_tag"],
            })

            children = group["children"]
            child_height = self.TILE_HEIGHT / max(1, len(children))
            for child_position, (source_index, child) in enumerate(children):
                rect = QRectF(
                    x + self.TILE_WIDTH / 2,
                    body_y + child_position * child_height,
                    self.TILE_WIDTH / 2,
                    child_height,
                )
                color = QColor(child["color"])
                painter.fillRect(rect, color)
                painter.setPen(QColor(0, 0, 0, 110))
                painter.drawRect(rect)
                painter.setPen(self._text_color(color))
                painter.drawText(rect.adjusted(3, 1, -3, -1), Qt.AlignmentFlag.AlignCenter | Qt.TextFlag.TextWordWrap, child["tag"])
                self._color_hits.append({
                    "rect": QRectF(rect), "group_id": group["id"],
                    "role": "child", "index": source_index,
                    "color": child["color"], "tag": child["tag"],
                })
        if self._drag_mode == "tile" and self._pressed_hit and self._movement() > 4:
            point = QPointF(self._drag_last.x() / self.zoom, self._drag_last.y() / self.zoom)
            preview = QRectF(point.x() - 34, point.y() - 18, 68, 36)
            color = QColor(self._pressed_hit.get("color", ""))
            if color.isValid():
                painter.setOpacity(0.72)
                painter.fillRect(preview, color)
                painter.setOpacity(1.0)
                painter.setPen(QPen(QColor("#FFFFFF"), 2))
                painter.drawRect(preview)
        painter.end()

    def _movement(self):
        return math.hypot(self._drag_last.x() - self._drag_start.x(), self._drag_last.y() - self._drag_start.y())

    def _base_position(self, position):
        return QPointF(position.x() / self.zoom, position.y() / self.zoom)

    def _hit_at(self, position):
        point = self._base_position(position)
        return next((dict(hit, rect=QRectF(hit["rect"])) for hit in reversed(self._color_hits) if hit["rect"].contains(point)), None)

    def _header_at(self, position):
        point = self._base_position(position)
        return next((hit for hit in self._header_hits if hit["rect"].contains(point)), None)

    def _update_mode_cursor(self):
        if self._drag_mode == "pan":
            cursor = Qt.CursorShape.ClosedHandCursor
        elif self._space_down and self._ctrl_down:
            cursor = Qt.CursorShape.SizeVerCursor
        elif self._space_down:
            cursor = Qt.CursorShape.OpenHandCursor
        else:
            cursor = Qt.CursorShape.CrossCursor
        self.setCursor(cursor)

    def handle_hold_key_event(self, event):
        pressed = event.type() == QEvent.Type.KeyPress
        if event.key() == Qt.Key.Key_Space:
            self._space_down = pressed
        elif event.key() == Qt.Key.Key_Control:
            self._ctrl_down = pressed
        self._update_mode_cursor()

    def keyPressEvent(self, event):
        if event.key() in (Qt.Key.Key_Space, Qt.Key.Key_Control) and not event.isAutoRepeat():
            self.handle_hold_key_event(event)
            event.accept()
            return
        super().keyPressEvent(event)

    def keyReleaseEvent(self, event):
        if event.key() in (Qt.Key.Key_Space, Qt.Key.Key_Control) and not event.isAutoRepeat():
            self.handle_hold_key_event(event)
            event.accept()
            return
        super().keyReleaseEvent(event)

    def clear_hold_keys(self):
        self._space_down = False
        self._ctrl_down = False
        self._drag_mode = ""
        self._update_mode_cursor()

    def _set_zoom(self, value, anchor=None):
        value = max(0.35, min(3.0, float(value)))
        if abs(value - self.zoom) < 1e-6:
            return
        old_zoom = self.zoom
        anchor = QPointF(anchor or QPointF(self.width() / 2, self.height() / 2))
        base_anchor = QPointF(anchor.x() / old_zoom, anchor.y() / old_zoom)
        scroll = self.scroll_area
        viewport_anchor = QPointF(anchor)
        if scroll is not None:
            viewport_anchor -= QPointF(scroll.horizontalScrollBar().value(), scroll.verticalScrollBar().value())
        self.zoom = value
        self._update_canvas_size()
        if scroll is not None:
            scroll.horizontalScrollBar().setValue(int(base_anchor.x() * value - viewport_anchor.x()))
            scroll.verticalScrollBar().setValue(int(base_anchor.y() * value - viewport_anchor.y()))
        self.update()

    def wheelEvent(self, event):
        steps = event.angleDelta().y() / 120.0
        if steps:
            self._set_zoom(self.zoom * (1.12 ** steps), event.position())
        event.accept()

    def mousePressEvent(self, event):
        self.setFocus(Qt.FocusReason.MouseFocusReason)
        self._drag_start = QPointF(event.position())
        self._drag_last = QPointF(event.position())
        self._drag_scroll = (
            self.scroll_area.horizontalScrollBar().value(),
            self.scroll_area.verticalScrollBar().value(),
        ) if self.scroll_area is not None else (0, 0)
        self._drag_zoom = self.zoom
        self._pressed_hit = self._hit_at(event.position())
        ctrl_space = self._space_down and self._ctrl_down
        if event.button() == Qt.MouseButton.MiddleButton or (event.button() == Qt.MouseButton.LeftButton and self._space_down and not ctrl_space):
            self._drag_mode = "pan"
        elif event.button() == Qt.MouseButton.LeftButton and ctrl_space:
            self._drag_mode = "zoom"
        elif event.button() == Qt.MouseButton.LeftButton and self._pressed_hit and self._pressed_hit.get("color"):
            self._drag_mode = "tile"
        else:
            self._drag_mode = "pick"
        self._update_mode_cursor()
        event.accept()

    def mouseMoveEvent(self, event):
        self._drag_last = QPointF(event.position())
        delta = self._drag_last - self._drag_start
        if self._drag_mode == "pan" and self.scroll_area is not None:
            self.scroll_area.horizontalScrollBar().setValue(int(self._drag_scroll[0] - delta.x()))
            self.scroll_area.verticalScrollBar().setValue(int(self._drag_scroll[1] - delta.y()))
        elif self._drag_mode == "zoom":
            self._set_zoom(self._drag_zoom * (1.012 ** -delta.y()), self._drag_start)
        elif self._drag_mode == "tile" and self._movement() > 4:
            try:
                self.grabMouse()
            except RuntimeError:
                pass
            self.update()
        event.accept()

    def mouseReleaseEvent(self, event):
        self._drag_last = QPointF(event.position())
        movement = self._movement()
        if self._drag_mode == "tile" and self._pressed_hit is not None:
            if movement <= 4:
                color = QColor(self._pressed_hit.get("color", ""))
                if color.isValid():
                    self.colorPicked.emit(color)
            else:
                target = self._hit_at(event.position())
                if target and all(target.get(key) == self._pressed_hit.get(key) for key in ("group_id", "role", "index")):
                    target = None
                self.tileMoveRequested.emit(dict(self._pressed_hit), target)
        elif self._drag_mode == "pick" and movement <= 4:
            hit = self._hit_at(event.position())
            if hit:
                color = QColor(hit.get("color", ""))
                if color.isValid():
                    self.colorPicked.emit(color)
        try:
            self.releaseMouse()
        except RuntimeError:
            pass
        self._drag_mode = ""
        self._pressed_hit = None
        self._update_mode_cursor()
        self.update()
        event.accept()

    def mouseDoubleClickEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            header = self._header_at(event.position())
            if header:
                self._begin_name_edit(header)
                event.accept()
                return
            hit = self._hit_at(event.position())
            if hit and hit.get("color"):
                self.tileTagEditRequested.emit(hit)
                event.accept()
                return
        super().mouseDoubleClickEvent(event)

    def _begin_name_edit(self, header):
        if self._name_editor is not None:
            self._name_editor.deleteLater()
        tile = _tile(self.chart, header["group_id"])
        if tile is None:
            return
        editor = QLineEdit(tile["name"], self)
        rect = header["rect"]
        editor.setGeometry(
            math.floor(rect.x() * self.zoom), math.floor(rect.y() * self.zoom),
            max(80, math.ceil(rect.width() * self.zoom)),
            max(22, math.ceil(rect.height() * self.zoom)),
        )
        editor.setMaxLength(64)
        self._name_editor = editor
        editor.editingFinished.connect(
            lambda group_id=header["group_id"], current=editor:
            self._finish_name_edit(group_id, current)
        )
        editor.show()
        editor.setFocus()
        editor.selectAll()

    def _finish_name_edit(self, group_id, editor):
        if self._name_editor is not editor:
            return
        self._name_editor = None
        value = editor.text().strip()[:64]
        editor.deleteLater()
        self.groupNameChanged.emit(group_id, value)

    def dragEnterEvent(self, event):
        if event.mimeData().hasFormat(TAG_MIME):
            event.acceptProposedAction()
            return
        super().dragEnterEvent(event)

    def dragMoveEvent(self, event):
        if event.mimeData().hasFormat(TAG_MIME) and self._hit_at(event.position()):
            event.acceptProposedAction()
            return
        event.ignore()

    def dropEvent(self, event):
        hit = self._hit_at(event.position())
        if event.mimeData().hasFormat(TAG_MIME) and hit:
            name = bytes(event.mimeData().data(TAG_MIME)).decode("utf-8", "replace")
            self.tagDropRequested.emit(hit, name)
            event.acceptProposedAction()
            return
        event.ignore()


class ColorChartPanel(QWidget):
    chartChanged = Signal(object)
    captureRequested = Signal()
    applyRequested = Signal()
    saveRequested = Signal()
    loadRequested = Signal()
    colorPicked = Signal(QColor)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.chart = empty_color_chart()
        self.setMinimumSize(440, 260)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(4)
        note = QLabel(
            "親子色とタグを画像から独立して保持します。"
            "タイルはドラッグで移動、ダブルクリックでタグを変更できます。"
        )
        note.setWordWrap(True)
        layout.addWidget(note)
        self.summary = QLabel("未登録")
        layout.addWidget(self.summary)

        body = QHBoxLayout()
        body.setContentsMargins(0, 0, 0, 0)
        self.tile_scroll = QScrollArea()
        self.tile_scroll.setWidgetResizable(False)
        self.tile_scroll.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.tile_canvas = ColorChartCanvas()
        self.tile_canvas.scroll_area = self.tile_scroll
        self.tile_scroll.setWidget(self.tile_canvas)
        body.addWidget(self.tile_scroll, 1)

        tag_box = QWidget()
        tag_box.setObjectName("colorChartTagManager")
        tag_box.setFixedWidth(132)
        tag_layout = QVBoxLayout(tag_box)
        tag_layout.setContentsMargins(6, 2, 2, 2)
        tag_layout.addWidget(QLabel("タグ"))
        self.tag_list = TagListWidget()
        self.tag_list.setToolTip(
            "ドラッグ：色タイルへ割り当て／一覧内で順序変更\n"
            "ダブルクリック：名前とタグ色を編集"
        )
        tag_layout.addWidget(self.tag_list, 1)
        tag_buttons = QHBoxLayout()
        add_tag = QToolButton()
        add_tag.setText("＋")
        remove_tag = QToolButton()
        remove_tag.setText("－")
        tag_buttons.addWidget(add_tag)
        tag_buttons.addWidget(remove_tag)
        tag_layout.addLayout(tag_buttons)
        body.addWidget(tag_box)
        layout.addLayout(body, 1)

        capture = QPushButton("現在の親子付けを登録")
        apply_button = QPushButton("チャートを使用色へ適用")
        file_row = QHBoxLayout()
        save_button = QPushButton("PMAG保存")
        load_button = QPushButton("PMAG読込")
        file_row.addWidget(save_button)
        file_row.addWidget(load_button)
        layout.addWidget(capture)
        layout.addWidget(apply_button)
        layout.addLayout(file_row)

        capture.clicked.connect(self.captureRequested)
        apply_button.clicked.connect(self.applyRequested)
        save_button.clicked.connect(self.saveRequested)
        load_button.clicked.connect(self.loadRequested)
        add_tag.clicked.connect(self._add_tag)
        remove_tag.clicked.connect(self._remove_tag)
        self.tag_list.itemDoubleClicked.connect(lambda item: self._edit_tag(item.text()))
        self.tag_list.orderChanged.connect(self._set_tag_order)
        self.tile_canvas.colorPicked.connect(self.colorPicked)
        self.tile_canvas.tileMoveRequested.connect(self._move_color)
        self.tile_canvas.tagDropRequested.connect(self._assign_tag)
        self.tile_canvas.tileTagEditRequested.connect(self._choose_tile_tag)
        self.tile_canvas.groupNameChanged.connect(self._rename_group)
        self.set_chart(self.chart)

    def set_chart(self, chart, *, emit=False):
        self.chart = normalize_color_chart(chart)
        self.tile_canvas.set_chart(self.chart)
        self._refresh_tags()
        child_count = sum(len(tile["children"]) for tile in self.chart["tiles"])
        self.summary.setText(
            f"{len(self.chart['tiles'])}グループ・{child_count}子色"
            if self.chart["tiles"] else "未登録"
        )
        if emit:
            self.chartChanged.emit(copy.deepcopy(self.chart))

    def _refresh_tags(self):
        current = self.tag_list.currentItem().text() if self.tag_list.currentItem() else ""
        self.tag_list.clear()
        order = ["Base", *self.chart["tag_order"]]
        order.extend(name for name in self.chart["tag_library"] if name not in order)
        for name in order:
            if name not in self.chart["tag_library"]:
                continue
            item = QListWidgetItem(name)
            color = QColor(self.chart["tag_colors"].get(name, "#58667A"))
            item.setBackground(color)
            item.setForeground(ColorChartCanvas._text_color(color))
            self.tag_list.addItem(item)
            if name == current:
                self.tag_list.setCurrentItem(item)

    def _move_color(self, source, target):
        chart, changed = move_chart_color(self.chart, source, target)
        if changed:
            self.set_chart(chart, emit=True)

    def _assign_tag(self, hit, name):
        chart, changed = assign_chart_tag(self.chart, hit, name)
        if changed:
            self.set_chart(chart, emit=True)

    def _choose_tile_tag(self, hit):
        choices = ["（タグなし）", *self.chart["tag_library"]]
        current = hit.get("tag", "")
        if current == "未設定":
            return
        index = choices.index(current) if current in choices else 0
        value, accepted = QComboBox(), False
        # QInputDialog is avoided here so the choices retain the chart order and
        # the dialog remains easy to test with ordinary Qt widgets.
        dialog = QDialog(self)
        dialog.setWindowTitle("色タイルのタグ")
        box = QVBoxLayout(dialog)
        value.addItems(choices)
        value.setCurrentIndex(index)
        box.addWidget(value)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        box.addWidget(buttons)
        accepted = dialog.exec() == QDialog.DialogCode.Accepted
        if accepted:
            self._assign_tag(hit, "" if value.currentIndex() == 0 else value.currentText())

    def _rename_group(self, group_id, name):
        chart = normalize_color_chart(self.chart)
        tile = _tile(chart, group_id)
        if tile is not None and tile["name"] != name:
            tile["name"] = str(name).strip()[:64]
            self.set_chart(chart, emit=True)

    def _set_tag_order(self, order):
        chart = normalize_color_chart(self.chart)
        chart["tag_order"] = [name for name in order if name != "Base"]
        self.set_chart(chart, emit=True)

    def _add_tag(self):
        dialog = ColorTagDialog(parent=self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        name, color = dialog.values()
        if not name:
            return
        chart = normalize_color_chart(self.chart)
        if name not in chart["tag_library"]:
            chart["tag_library"].append(name)
            if name != "Base":
                chart["tag_order"].append(name)
        chart["tag_colors"][name] = color
        self.set_chart(chart, emit=True)

    def _edit_tag(self, old_name):
        old_name = _tag(old_name)
        if not old_name:
            return
        dialog = ColorTagDialog(
            old_name,
            self.chart["tag_colors"].get(old_name, "#58667A"),
            lock_name=old_name == "Base",
            parent=self,
        )
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        new_name, color = dialog.values()
        if old_name == "Base":
            new_name = "Base"
        if not new_name:
            return
        chart = normalize_color_chart(self.chart)
        if new_name != old_name and new_name in chart["tag_library"]:
            QMessageBox.information(self, "タグ編集", "同じ名前のタグが既にあります。")
            return
        for tile in chart["tiles"]:
            if tile["parent"]["tag"] == old_name:
                tile["parent"]["tag"] = new_name
            for child in tile["children"]:
                if child["tag"] == old_name:
                    child["tag"] = new_name
        chart["tag_library"] = [new_name if name == old_name else name for name in chart["tag_library"]]
        chart["tag_order"] = [new_name if name == old_name else name for name in chart["tag_order"]]
        chart["tag_colors"].pop(old_name, None)
        chart["tag_colors"][new_name] = color
        self.set_chart(chart, emit=True)

    def _remove_tag(self):
        item = self.tag_list.currentItem()
        if item is None or item.text() == "Base":
            return
        name = item.text()
        chart = normalize_color_chart(self.chart)
        for tile in chart["tiles"]:
            if tile["parent"]["tag"] == name:
                tile["parent"]["tag"] = "Base"
            for child in tile["children"]:
                if child["tag"] == name:
                    child["tag"] = ""
        chart["tag_library"] = [value for value in chart["tag_library"] if value != name]
        chart["tag_order"] = [value for value in chart["tag_order"] if value != name]
        chart["tag_colors"].pop(name, None)
        self.set_chart(chart, emit=True)


def read_pmag(path):
    source = Path(path)
    if source.stat().st_size > 16 * 1024 * 1024:
        raise ValueError("PMAGファイルが大きすぎます。")
    payload = json.loads(source.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or payload.get("format") != PMAG_FORMAT:
        raise ValueError("PaintMaskGroup形式ではありません。")
    return normalize_color_chart(payload)


def write_pmag(path, chart):
    destination = Path(path)
    destination.write_text(
        json.dumps(normalize_color_chart(chart), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
