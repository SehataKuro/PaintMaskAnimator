"""ゴミ取り／塗り抜けの純粋アルゴリズム。

`main_window_line_ops.remove_dust_fill_surrounding`（メニュー実行）と
取り込みパイプラインのゴミ取りステージが同じ実装を共有するために、
ウィジェット状態から切り離した形で切り出したもの。RGBA配列を直接
書き換え、変更した画素数を返す。
"""
import numpy as np

from . import imaging

DUST_MODE = "ゴミ取り"
FILL_MODE = "塗り抜け"


def iter_components(mask):
    """4方向連結成分を (画素リスト, 外周に接するか) で列挙する。"""
    pending = np.asarray(mask, dtype=bool).copy()
    candidate_y, candidate_x = np.nonzero(pending)
    height, width = pending.shape

    for y0, x0 in zip(candidate_y, candidate_x):
        y0 = int(y0)
        x0 = int(x0)
        if not pending[y0, x0]:
            continue

        stack = [(x0, y0)]
        pending[y0, x0] = False
        component = []
        touches_edge = False

        while stack:
            x, y = stack.pop()
            component.append((y, x))
            if x == 0 or y == 0 or x == width - 1 or y == height - 1:
                touches_edge = True

            if x > 0 and pending[y, x - 1]:
                pending[y, x - 1] = False
                stack.append((x - 1, y))
            if x + 1 < width and pending[y, x + 1]:
                pending[y, x + 1] = False
                stack.append((x + 1, y))
            if y > 0 and pending[y - 1, x]:
                pending[y - 1, x] = False
                stack.append((x, y - 1))
            if y + 1 < height and pending[y + 1, x]:
                pending[y + 1, x] = False
                stack.append((x, y + 1))

        yield component, touches_edge


def _pseudo_white(pixels):
    """透明画素と純白を同じ「白背景」として扱うマスク。"""
    return (
        (pixels[:, :, 3] == 0)
        | np.all(pixels[:, :, :3] == 255, axis=2)
    )


def _fill_enclosed_holes(pixels, max_area, selected_colors):
    """外周につながらない小さな白領域を、周囲の多数色で埋める。"""
    height, width = pixels.shape[:2]
    pseudo_white = _pseudo_white(pixels)

    starts = []
    starts.extend((int(x), 0) for x in np.flatnonzero(pseudo_white[0, :]))
    if height > 1:
        starts.extend(
            (int(x), height - 1)
            for x in np.flatnonzero(pseudo_white[-1, :])
        )
    if width > 1:
        starts.extend(
            (0, int(y)) for y in np.flatnonzero(pseudo_white[:, 0])
        )
        starts.extend(
            (width - 1, int(y))
            for y in np.flatnonzero(pseudo_white[:, -1])
        )

    outside = (
        imaging.scanline_connected_region(pseudo_white, starts)
        if starts
        else np.zeros_like(pseudo_white)
    )
    holes = pseudo_white & ~outside

    changed = 0
    for component, _touches_edge in iter_components(holes):
        area = len(component)
        if area == 0 or area > max_area:
            continue

        border_positions = set()
        for y, x in component:
            for ny in range(max(0, y - 1), min(height, y + 2)):
                for nx in range(max(0, x - 1), min(width, x + 2)):
                    if (ny != y or nx != x) and not pseudo_white[ny, nx]:
                        border_positions.add((ny, nx))

        if not border_positions:
            continue

        border_yx = np.asarray(tuple(border_positions), dtype=np.int32)
        border_rgb = pixels[border_yx[:, 0], border_yx[:, 1], :3]

        if selected_colors:
            keep = np.zeros(len(border_rgb), dtype=bool)
            for selected_color in selected_colors:
                keep |= np.all(
                    border_rgb == np.asarray(selected_color, dtype=np.uint8),
                    axis=1,
                )
            border_rgb = border_rgb[keep]
            if border_rgb.size == 0:
                continue

        packed = (
            (border_rgb[:, 0].astype(np.uint32) << 16)
            | (border_rgb[:, 1].astype(np.uint32) << 8)
            | border_rgb[:, 2].astype(np.uint32)
        )
        values, counts = np.unique(packed, return_counts=True)
        selected = int(values[int(np.argmax(counts))])
        fill = np.asarray(
            [(selected >> 16) & 255, (selected >> 8) & 255, selected & 255],
            dtype=np.uint8,
        )
        coordinates = np.asarray(component, dtype=np.int32)
        cy = coordinates[:, 0]
        cx = coordinates[:, 1]
        pixels[cy, cx, :3] = fill
        pixels[cy, cx, 3] = 255
        changed += area
    return changed


def _remove_dust(pixels, max_area, selected_colors, removal_rgba):
    """小さな色点を白へ変更する。"""
    height, width = pixels.shape[:2]
    pseudo_white = _pseudo_white(pixels)
    rgb = pixels[:, :, :3]
    removal_mask = np.zeros((height, width), dtype=bool)

    if selected_colors:
        # 選択色ごとに独立判定する。
        # 青1pxが黒に接していても青成分は1pxとして消える。
        for selected_color in selected_colors:
            color_mask = ~pseudo_white & np.all(
                rgb == np.asarray(selected_color, dtype=np.uint8),
                axis=2,
            )
            for component, _edge in iter_components(color_mask):
                if 0 < len(component) <= max_area:
                    coordinates = np.asarray(component, dtype=np.int32)
                    removal_mask[coordinates[:, 0], coordinates[:, 1]] = True
    else:
        # 未選択時は、白背景から独立した小さな色塊を削除。
        for component, touches_edge in iter_components(~pseudo_white):
            if not touches_edge and 0 < len(component) <= max_area:
                coordinates = np.asarray(component, dtype=np.int32)
                removal_mask[coordinates[:, 0], coordinates[:, 1]] = True

    changed = int(np.count_nonzero(removal_mask))
    if changed:
        pixels[removal_mask] = np.asarray(removal_rgba, dtype=np.uint8)
    return changed


def despeckle_pixels(
    pixels,
    mode=DUST_MODE,
    max_area=4,
    selected_colors=None,
    removal_rgba=(255, 255, 255, 255),
):
    """RGBA配列を破壊的に処理し、変更した画素数を返す。

    ``removal_rgba`` はゴミを消した跡に書き込む色。白背景の素材では白、
    透明背景の素材では ``(0, 0, 0, 0)`` を渡すことで、確定パレットに
    無い色を持ち込まずに済む。
    """
    height, width = pixels.shape[:2]
    if width <= 0 or height <= 0:
        return 0
    max_area = max(1, int(max_area))
    selected_colors = (
        [tuple(int(value) for value in color) for color in selected_colors]
        if selected_colors
        else []
    )
    if mode == FILL_MODE:
        return _fill_enclosed_holes(pixels, max_area, selected_colors)
    return _remove_dust(pixels, max_area, selected_colors, removal_rgba)


def despeckle_image(
    image,
    mode=DUST_MODE,
    max_area=4,
    selected_colors=None,
    removal_rgba=(255, 255, 255, 255),
):
    """QImageに対してゴミ取り／塗り抜けを行い、(新画像, 変更画素数) を返す。"""
    if image is None or image.isNull():
        return image, 0
    pixels = imaging.qimage_rgba_array(image)
    changed = despeckle_pixels(
        pixels,
        mode=mode,
        max_area=max_area,
        selected_colors=selected_colors,
        removal_rgba=removal_rgba,
    )
    if not changed:
        return image, 0
    return imaging.rgba_array_to_qimage(pixels), changed
