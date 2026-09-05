"""MCP サーバーのツール定義のテスト。

``mcp`` は任意依存なので、未導入の環境ではまるごとスキップする。ここで見るのは
「ツールが登録され、MCP の呼び出し経路を通って session まで届くか」まで。処理の
中身の正しさは ``test_mcp_session.py`` が持つ。
"""
from __future__ import annotations

import asyncio
import base64
import json

import pytest
from PySide6.QtGui import QColor, QGuiApplication, QImage

from paintmaskanimator import constants, project_io
from paintmaskanimator.models import make_frame

pytest.importorskip("mcp", reason="MCP サーバーは任意依存 (extras: mcp)")

from paintmaskanimator.mcp.server import build_server  # noqa: E402
from paintmaskanimator.mcp.session import ProjectSession  # noqa: E402

EXPECTED_TOOLS = {
    "open_project",
    "describe_project",
    "list_frames",
    "get_color_chart",
    "render_frame",
    "list_regions",
    "apply_region_colors",
    "save_project",
}


def run(coro):
    """テストごとにイベントループを立てる。pytest-asyncio を足さずに済ませる。"""
    return asyncio.run(coro)


def blocks_of(result):
    """``call_tool`` の戻りから内容ブロックの並びを取り出す。

    SDK の版で ``CallToolResult`` だったりブロックの並びだったりするので、
    ここで吸収してテスト本体を版に依存させない。
    """
    content = getattr(result, "content", None)
    if content is not None:
        return content
    return result[0] if isinstance(result, tuple) else result


def text_of(result):
    return blocks_of(result)[0].text


@pytest.fixture(scope="module")
def gui_app():
    return QGuiApplication.instance() or QGuiApplication(["tests"])


@pytest.fixture
def project(gui_app, tmp_path):
    """白地に閉じた黒枠を1つ描いた1コマのプロジェクト。枠の内側が 6x6 の閉領域。"""
    frames = [make_frame()]
    layer = frames[0].layers[0]
    layer.image.fill(QColor("white"))
    black = QColor("black")
    for x in range(4, 12):
        layer.image.setPixelColor(x, 4, black)
        layer.image.setPixelColor(x, 11, black)
    for y in range(4, 12):
        layer.image.setPixelColor(4, y, black)
        layer.image.setPixelColor(11, y, black)
    layer.has_content = True
    path = tmp_path / "sample.pma"
    project_io.write_project_archive(
        path,
        {
            "format": "PaintMaskAnimatorProject",
            "format_version": 1,
            "canvas": {
                "width": int(constants.CANVAS_WIDTH),
                "height": int(constants.CANVAS_HEIGHT),
            },
            "current_frame": 0,
            "active_layer_index": 0,
            "frames": [],
        },
        frames,
    )
    return path


def server_for(project, *, allow_write=False):
    session = ProjectSession(allow_write=allow_write)
    return build_server(session, project=project), session


def test_every_tool_is_registered(project):
    server, _session = server_for(project)

    names = {tool.name for tool in run(server.list_tools())}

    assert EXPECTED_TOOLS <= names


def test_describe_project_round_trips_through_the_tool_call(project):
    server, _session = server_for(project)

    payload = json.loads(text_of(run(server.call_tool("describe_project", {}))))

    assert payload["frame_count"] == 1
    assert payload["writable"] is False


def test_render_frame_returns_a_png_image_block(project):
    server, _session = server_for(project)

    result = run(server.call_tool("render_frame", {"frame": 0, "max_size": 320}))

    image_block = next(
        block for block in blocks_of(result) if getattr(block, "type", None) == "image"
    )
    assert image_block.mime_type == "image/png"
    image = QImage.fromData(base64.b64decode(image_block.data), "PNG")
    assert not image.isNull()
    assert max(image.width(), image.height()) == 320


def test_list_regions_exposes_seeds_that_apply_region_colors_accepts(project):
    server, session = server_for(project, allow_write=True)

    listed = json.loads(
        text_of(run(server.call_tool("list_regions", {"frame": 0, "min_area": 4})))
    )
    inside = next(region for region in listed["regions"] if region["area"] == 36)
    assert inside["painted"] is False

    report = json.loads(
        text_of(
            run(
                server.call_tool(
                    "apply_region_colors",
                    {
                        "assignments": [
                            {
                                "frame": 0,
                                "layer": 0,
                                "seed": inside["seed"],
                                "color": "#3355FF",
                            }
                        ]
                    },
                )
            )
        )
    )

    assert report["applied"][0]["pixels"] == 36
    x, y = inside["seed"]
    assert session.frames[0].layers[0].image.pixelColor(x, y) == QColor("#3355FF")


def test_write_tools_are_refused_on_a_read_only_server(project):
    server, _session = server_for(project, allow_write=False)

    payload = json.loads(
        text_of(
            run(
                server.call_tool(
                    "apply_region_colors",
                    {
                        "assignments": [
                            {"frame": 0, "layer": 0, "seed": [8, 8], "color": "#3355FF"}
                        ]
                    },
                )
            )
        )
    )

    # 例外にせず理由を返す。AI 側が「なぜ書けなかったか」を読めるようにするため。
    assert "読み取り専用" in payload["error"]
    assert payload["writable"] is False
