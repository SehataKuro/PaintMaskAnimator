"""Sparse sheets must not resolve every exposure during key navigation."""
from types import SimpleNamespace

from paintmaskanimator.canvas_timeline_ops import TimelineStructureMixin
from paintmaskanimator.timeline import TimelineWidget


def test_sparse_key_scan_matches_spans_without_resolving_each_column(monkeypatch):
    frames = []
    for column in range(300):
        layer = SimpleNamespace(
            has_content=3 <= column < 80 or 280 <= column < 300,
            is_blank_key=80 <= column < 103,
            blank_start=column == 80,
            sequence_number=1 if column < 80 else 2,
            sequence_only=column == 140,
        )
        frames.append(SimpleNamespace(layers=[layer]))
    expected = [
        column for column in range(len(frames))
        if TimelineWidget.timeline_span_at(frames, 0, column)[1] == column
    ]

    def unexpected_resolution(*args):
        raise AssertionError("Key navigation must scan explicit keys directly")

    monkeypatch.setattr(TimelineWidget, "timeline_span_at", unexpected_resolution)
    canvas = SimpleNamespace(frames=frames, active_layer_index=0)
    assert TimelineStructureMixin.timeline_step_columns(canvas) == expected == [3, 80, 280]
    assert TimelineStructureMixin.timeline_step_columns(canvas, -1) == []
    assert TimelineStructureMixin.timeline_step_columns(canvas, 1) == []
