"""Rust の部品（``pma_native``）への入口。

重い画像計算を Rust で行う（``docs/design-principles.md``「将来は Rust へ
置き換えていく」）。部品が入っていない環境、または環境変数
``PMA_NO_NATIVE=1`` のときは ``None`` を返し、呼び出し側は numpy 版で計算する。
どちらでも結果は同じになることを、テストで突き合わせている。

マスクは numpy の bool 配列で受け渡す。bool 配列は1画素1バイト（0 か 1）
なので、コピーせずにそのまま Rust へ渡せる。
"""
from __future__ import annotations

import os

import numpy as np

from .logging_setup import get_logger

log = get_logger(__name__)

try:  # pragma: no cover - 入っているかどうかは環境しだい
    import pma_native as _native
except ImportError:  # pragma: no cover
    _native = None


def available() -> bool:
    """Rust の部品を使うか。"""
    return _native is not None and os.environ.get("PMA_NO_NATIVE") != "1"


def _as_bytes(mask):
    return np.ascontiguousarray(mask, dtype=bool).tobytes()


def _as_mask(data, height, width):
    # 返ってきた bytes は読み取り専用なので、呼び出し側が書き換えられるよう複製する。
    return np.frombuffer(data, dtype=bool).reshape(height, width).copy()


def flood_fill(passable, seeds):
    """4近傍で ``seeds`` からつながる ``passable`` の領域。使えなければ None。"""
    if not available():
        return None
    height, width = passable.shape
    seeds = [(int(x), int(y)) for x, y in seeds]
    assert _native is not None
    return _as_mask(
        _native.flood_fill(_as_bytes(passable), width, height, seeds), height, width
    )


def dilate(mask, iterations):
    """8近傍で ``iterations`` 回膨張させる。使えなければ None。"""
    if not available():
        return None
    height, width = mask.shape
    assert _native is not None
    return _as_mask(
        _native.dilate(_as_bytes(mask), width, height, int(iterations)), height, width
    )


def erode(mask, iterations):
    """8近傍で ``iterations`` 回収縮させる（範囲外は偽）。使えなければ None。"""
    if not available():
        return None
    height, width = mask.shape
    assert _native is not None
    return _as_mask(
        _native.erode(_as_bytes(mask), width, height, int(iterations)), height, width
    )
