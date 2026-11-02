"""使用色フォルダーを使った影素材の書き出し（Issue #21）。

影色を使用色フォルダーにまとめておけば、撮影へ渡す素材を自動で作れる。
1枚のセルから3種類を取り出す。

* ノーマル単色塗り … 絵のある画素をすべて1色で塗ったもの（通常のベタ）
* 影単色 … フォルダーの色の画素だけを1色で塗ったもの
* 影のマスク … フォルダーの色を白、それ以外を黒にした不透明な画像

Qt には依存しない素の numpy 配列として扱う。
"""
import numpy as np

#: 書き出す素材の種類（フォルダー名にも使う）。
NORMAL = "normal"
SHADOW = "shadow"
SHADOW_MASK = "shadow_mask"
KINDS = (NORMAL, SHADOW, SHADOW_MASK)

#: 既定の塗り色。撮影で扱いやすい原色にしておく。
DEFAULT_NORMAL_COLOR = (255, 255, 255)
DEFAULT_SHADOW_COLOR = (0, 0, 255)


def shadow_mask_of(rgba, shadow_rgbs):
    """フォルダーの色が置かれている画素を真にしたマスク。"""
    rgba = np.asarray(rgba, dtype=np.uint8)
    mask = np.zeros(rgba.shape[:2], dtype=bool)
    opaque = rgba[:, :, 3] > 0
    for rgb in shadow_rgbs or ():
        target = np.asarray(
            [int(channel) for channel in rgb[:3]], dtype=np.uint8
        )
        mask |= opaque & np.all(rgba[:, :, :3] == target, axis=2)
    return mask


def flat_normal(rgba, color=DEFAULT_NORMAL_COLOR):
    """絵のある画素をすべて ``color`` で塗った、透明背景の画像。"""
    rgba = np.asarray(rgba, dtype=np.uint8)
    result = np.zeros_like(rgba)
    opaque = rgba[:, :, 3] > 0
    result[:, :, :3][opaque] = np.asarray(color[:3], dtype=np.uint8)
    result[:, :, 3][opaque] = 255
    return result


def flat_shadow(rgba, shadow_rgbs, color=DEFAULT_SHADOW_COLOR):
    """フォルダーの色の画素だけを ``color`` で塗った、透明背景の画像。"""
    rgba = np.asarray(rgba, dtype=np.uint8)
    result = np.zeros_like(rgba)
    mask = shadow_mask_of(rgba, shadow_rgbs)
    result[:, :, :3][mask] = np.asarray(color[:3], dtype=np.uint8)
    result[:, :, 3][mask] = 255
    return result


def shadow_matte(rgba, shadow_rgbs):
    """影を白、それ以外を黒にした不透明な画像（マスク）。"""
    rgba = np.asarray(rgba, dtype=np.uint8)
    mask = shadow_mask_of(rgba, shadow_rgbs)
    result = np.zeros_like(rgba)
    result[:, :, 3] = 255
    result[:, :, :3][mask] = 255
    return result


def materials(rgba, shadow_rgbs, *, normal_color=DEFAULT_NORMAL_COLOR,
              shadow_color=DEFAULT_SHADOW_COLOR):
    """3種類をまとめて作る。``{種類: RGBA 配列}``。"""
    return {
        NORMAL: flat_normal(rgba, normal_color),
        SHADOW: flat_shadow(rgba, shadow_rgbs, shadow_color),
        SHADOW_MASK: shadow_matte(rgba, shadow_rgbs),
    }
