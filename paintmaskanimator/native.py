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


def line_flow_scores(target, guide, block, base_y, base_x, reach):
    """位置合わせの一致数。古い部品しか入っていなければ numpy へ戻す。"""
    if not available() or not hasattr(_native, "line_flow_scores"):
        return None
    assert _native is not None
    height, width = target.shape
    result = _native.line_flow_scores(
        _as_bytes(target), _as_bytes(guide), width, height, int(block),
        np.ascontiguousarray(base_y, dtype=np.int64).tobytes(),
        np.ascontiguousarray(base_x, dtype=np.int64).tobytes(), int(reach),
    )
    side = 2 * reach + 1
    return np.frombuffer(result, dtype=np.uint32).reshape(
        side * side, height // block, width // block,
    ).astype(np.float64)


def _as_bytes(mask):
    return np.ascontiguousarray(mask, dtype=bool).tobytes()


def thin(mask, iterations):
    """線端検出用の細線化。古い部品・部品なしでは numpy へ戻す。"""
    if not available() or not hasattr(_native, "thin"):
        return None
    assert _native is not None
    height, width = mask.shape
    return _as_mask(_native.thin(_as_bytes(mask), width, height, int(iterations)), height, width)


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


#: :func:`label_regions` で番号を付けない画素の値。
INVALID_CODE = 0xFFFFFFFF


def label_regions(codes):
    """同じ値で4近傍につながった画素に1からの番号を付ける。使えなければ None。

    :param codes: uint32 の2次元配列。:data:`INVALID_CODE` の画素は番号 0。
    :returns: ``(番号の int32 配列, 領域の数)``。
    """
    if not available():
        return None
    assert _native is not None
    if not hasattr(_native, "label_regions"):
        # 古い部品が入っている環境（開発中など）では numpy 版を使う。
        return None
    height, width = codes.shape
    data, count = _native.label_regions(
        np.ascontiguousarray(codes, dtype=np.uint32).tobytes(), width, height
    )
    labels = np.frombuffer(data, dtype=np.uint32).reshape(height, width)
    return labels.astype(np.int32), int(count)
