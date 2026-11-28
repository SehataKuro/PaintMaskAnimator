"""レイヤーの「結合」（main_window_layer_ops.merge_rows）を pma-core の merge_layers と比べる。"""

import json
from types import SimpleNamespace

import pytest

from paintmaskanimator import main_window_layer_ops, utils
from paintmaskanimator.canvas_key_frame import KeyFrameMixin
from test_core_document_compare import _compare, _load_oracle, _notices, _project
from v2_golden_support import CASES, ROOT, V2_ROOT


class _Canvas:
    """merge_rows が読む分だけの canvas（文書は Python の正本）。"""

    resolve_key_frame = KeyFrameMixin.resolve_key_frame

    def __init__(self, document):
        self._document = document

    @property
    def frames(self):
        return self._document.frames

    @property
    def layers(self):
        return self._document.layers

    def push_doc_undo(self):
        pass


def _merge_with_qt(monkeypatch, doc, indices):
    captured = []
    monkeypatch.setattr(main_window_layer_ops, "create_counter", lambda *a, **k: None)
    monkeypatch.setattr(main_window_layer_ops, "update_counter", lambda *a, **k: None)
    monkeypatch.setattr(main_window_layer_ops, "close_counter", lambda *a, **k: None)

    def publish(canvas, changes):
        # publish_track_changes は結合のあと、旧形式のコマを入れ直す（install_legacy_frames）。
        captured.append(changes)
        canvas._document.install_legacy_frames(canvas._document.frames)

    monkeypatch.setattr(main_window_layer_ops, "publish_track_changes", publish)
    monkeypatch.setattr(main_window_layer_ops.QApplication, "setOverrideCursor", lambda *a: None)
    monkeypatch.setattr(main_window_layer_ops.QApplication, "restoreOverrideCursor", lambda *a: None)
    canvas = _Canvas(doc)
    window = SimpleNamespace(
        canvas=canvas,
        statusBar=lambda: SimpleNamespace(showMessage=lambda *a: None),
        used_color=SimpleNamespace(refresh_with_counter=lambda *a: None),
    )
    count = len(doc.layers)
    main_window_layer_ops.LayerOpsController(window).merge_rows([count - 1 - i for i in indices])
    assert len(captured) == 1
    return captured[0]


def _cases():
    paths = [V2_ROOT / f"{case}.pman" for case in CASES]
    paths += sorted((ROOT / "v1").glob("*.pman"))
    out = []
    for path in paths:
        try:
            layers = len(_load_oracle(path).document.layers)
        except Exception:  # noqa: BLE001 — 読めない見本は使わない
            continue
        if layers >= 2:
            out.append(pytest.param(path, [0, 1], id=f"{path.stem}-0-1"))
            if layers > 2:
                out.append(pytest.param(path, list(range(layers)), id=f"{path.stem}-all"))
    return out


@pytest.mark.parametrize("path, indices", _cases())
def test_merge_layers(monkeypatch, path, indices):
    loaded = _load_oracle(path)
    monkeypatch.setattr(utils, "workspace_size", lambda: (loaded.width, loaded.height))
    payload, artwork = _project(loaded)
    changes = _merge_with_qt(monkeypatch, loaded.document, indices)
    _compare(
        loaded, json.dumps(payload), artwork,
        [["merge_layers", indices]], [_notices(changes)],
    )
