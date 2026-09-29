"""シナリオ: 連番を読み込んでタイムシートを差し替える。

「3枚の連番画像を読み込む → After Effects の Time Remap を貼り付ける →
コマ順が入れ替わる」という取り込み〜再構成の流れを通す。
連番読み込みが作るソースバンクにタイムリマップが正しく当たるかを見る。
"""
from __future__ import annotations

import pytest
from PySide6.QtGui import QColor, QImage

from paintmaskanimator import constants
from paintmaskanimator.errors import OperationError

pytestmark = pytest.mark.e2e

# 1コマ目が3番、2コマ目が2番、3コマ目が1番の絵になる（逆再生）。
REVERSED_TIME_REMAP = """Adobe After Effects 8.0 Keyframe Data

\tUnits Per Second\t24

Time Remap
\tFrame\tseconds
\t0\t0.0833333
\t1\t0.0416666
\t2\t0

End of Keyframe Data
"""


@pytest.fixture
def sequence_paths(tmp_path):
    """1枚ごとに違う色で塗った連番PNGを作る。"""
    paths = []
    for index, rgb in enumerate(((255, 0, 0), (0, 255, 0), (0, 0, 255)), 1):
        image = QImage(
            constants.CANVAS_WIDTH,
            constants.CANVAS_HEIGHT,
            QImage.Format.Format_ARGB32,
        )
        image.fill(QColor(*rgb))
        path = tmp_path / f"cut_{index:04d}.png"
        assert image.save(str(path))
        paths.append(path)
    return paths


def test_imported_sequence_becomes_numbered_key_frames(app, sequence_paths):
    app.import_image_sequence(sequence_paths)

    layer_index = app.canvas.active_layer_index
    assert app.sequence_numbers(layer_index)[:3] == [1, 2, 3]
    assert app.frame_count >= 3


def test_time_remap_reverses_the_sequence(app, sequence_paths):
    app.import_image_sequence(sequence_paths)
    layer_index = app.canvas.active_layer_index

    app.apply_time_remap(REVERSED_TIME_REMAP)

    assert app.sequence_numbers(layer_index)[:3] == [3, 2, 1]
    assert not app.messages.of_level("critical")


def test_time_remap_rejects_unrecognised_text(app, sequence_paths):
    app.import_image_sequence(sequence_paths)

    with pytest.raises(OperationError):
        app.apply_time_remap("これは時間表ではありません")
