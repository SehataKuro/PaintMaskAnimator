"""NumPy で二値マスクを膨張・収縮する。"""

import numpy as np


def dilate_numpy(mask, iterations):
    """8 近傍で ``iterations`` 回膨張させる（numpy 版）。"""
    result = mask.copy()
    for _ in range(iterations):
        source = result.copy()
        result[1:, :] |= source[:-1, :]
        result[:-1, :] |= source[1:, :]
        result[:, 1:] |= source[:, :-1]
        result[:, :-1] |= source[:, 1:]
        result[1:, 1:] |= source[:-1, :-1]
        result[:-1, :-1] |= source[1:, 1:]
        result[1:, :-1] |= source[:-1, 1:]
        result[:-1, 1:] |= source[1:, :-1]
    return result


def erode_numpy(mask, iterations):
    """8 近傍で ``iterations`` 回収縮させる（numpy 版）。"""
    result = mask.copy()
    for _ in range(iterations):
        padded = np.pad(
            result, ((1, 1), (1, 1)), mode="constant", constant_values=False
        )
        result = (
            padded[0:-2, 0:-2]
            & padded[0:-2, 1:-1]
            & padded[0:-2, 2:]
            & padded[1:-1, 0:-2]
            & padded[1:-1, 1:-1]
            & padded[1:-1, 2:]
            & padded[2:, 0:-2]
            & padded[2:, 1:-1]
            & padded[2:, 2:]
        )
    return result
