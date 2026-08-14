"""Binary-preserving mask transformation (inverse mapping + area coverage).

The TP-quality transform used to project every color mask *forward* through the
deformation (``QPainter.setTransform`` / per-mask triangle rasterization) and
then rebuild a binary image from blurred grayscale masks.  Forward mapping skips
or double-writes output pixels depending on the rotation angle, and the
blur-then-``> 0`` threshold inflated every shape by the blur radius, so rotated
edges came out as irregular noise instead of a clean staircase.

This module replaces that with the standard approach for binary raster
resampling:

1. **Inverse mapping** - every *output* pixel is projected back into source
   space, so no output pixel is ever skipped or written twice.
2. **Area coverage** - each output pixel is probed at ``n x n`` subsample
   positions and each color counts how many probes it won.
3. **Majority resolution** - the color covering most of the pixel takes it
   whole.  For a two-region boundary that is exactly a 50% area threshold, and
   the result is strictly binary: no intermediate colors, no partial alpha.

Everything here is pure NumPy: no Qt, no Pillow.  The functions operate on a
*label image* (``uint16``, 0 = outside/background, 1..N = color ids) instead of
one grayscale mask per color, which also makes the cost independent of the
palette size.
"""
from __future__ import annotations

import math

import numpy as np

#: Subsample probes per axis.  4 -> 16 probes per output pixel, which resolves
#: coverage in 1/16 steps: enough to place a 50% boundary within a pixel.
DEFAULT_SUBSAMPLES = 4

#: Output rows processed per band.  Bounds peak memory: a band allocates
#: ``band_rows * width * subsamples^2`` entries rather than the whole image.
DEFAULT_BAND_ROWS = 64


def homography_matrix(source_points, target_points):
    """Return the 3x3 matrix mapping ``source_points`` onto ``target_points``.

    Points are ``(x, y)`` pairs.  Four correspondences are required.  Raises
    ``numpy.linalg.LinAlgError`` for degenerate quads.
    """
    rows = []
    values = []
    for (x, y), (u, v) in zip(source_points, target_points):
        x, y, u, v = float(x), float(y), float(u), float(v)
        rows.append([x, y, 1, 0, 0, 0, -u * x, -u * y])
        values.append(u)
        rows.append([0, 0, 0, x, y, 1, -v * x, -v * y])
        values.append(v)
    solved = np.linalg.solve(
        np.asarray(rows, dtype=np.float64),
        np.asarray(values, dtype=np.float64),
    )
    matrix = np.empty((3, 3), dtype=np.float64)
    matrix.flat[:8] = solved
    matrix[2, 2] = 1.0
    return matrix


def _probe_coordinates(start, stop, subsamples):
    """Probe centers, in output-pixel units, for the range ``[start, stop)``."""
    step = 1.0 / float(subsamples)
    offsets = (np.arange(subsamples, dtype=np.float64) + 0.5) * step
    return (
        np.arange(start, stop, dtype=np.float64)[:, None] + offsets[None, :]
    ).reshape(-1)


class QuadMapper:
    """Inverse map for the free / scale / rotate transform (a planar quad).

    ``target_quad`` are the four destination corners in output-image
    coordinates, in the same order as the source rectangle corners
    (top-left, top-right, bottom-right, bottom-left).
    """

    def __init__(self, target_quad, source_width, source_height):
        self.source_width = int(source_width)
        self.source_height = int(source_height)
        source_quad = [
            (0.0, 0.0),
            (float(source_width), 0.0),
            (float(source_width), float(source_height)),
            (0.0, float(source_height)),
        ]
        target = [(float(p[0]), float(p[1])) for p in target_quad[:4]]
        # Solve destination -> source directly: the inverse map is what the
        # renderer needs, and solving it straight avoids inverting a matrix
        # that may be near-singular in one direction only.
        self.matrix = homography_matrix(target, source_quad)

    def map_band(self, width, y0, y1, subsamples):
        # Each coordinate is separable - ``m0*x + m1*y + m2`` - so the per-axis
        # terms are computed once as vectors and only combined when the 2-D
        # probe grid is finally needed.  That keeps one full-size temporary per
        # coordinate instead of one per term.
        probe_x = _probe_coordinates(0, width, subsamples)
        probe_y = _probe_coordinates(y0, y1, subsamples)
        matrix = self.matrix

        def combine(row):
            along_x = (matrix[row, 0] * probe_x).astype(np.float32)
            along_y = (matrix[row, 1] * probe_y + matrix[row, 2]).astype(
                np.float32
            )
            return along_x[None, :] + along_y[:, None]

        source_x = combine(0)
        source_y = combine(1)
        if abs(matrix[2, 0]) < 1e-12 and abs(matrix[2, 1]) < 1e-12:
            # Affine (scale / rotate / shear): the perspective divisor is
            # constant, so skip the division across the whole grid.
            scale = float(matrix[2, 2])
            if abs(scale) < 1e-12:
                return source_x, source_y, np.zeros(source_x.shape, dtype=bool)
            if abs(scale - 1.0) > 1e-12:
                source_x /= scale
                source_y /= scale
            return source_x, source_y, True
        denominator = combine(2)
        # A zero denominator is the projective horizon; mark those probes
        # invalid rather than letting the division produce infinities.
        safe = np.abs(denominator) > 1e-12
        np.copyto(denominator, 1.0, where=~safe)
        source_x /= denominator
        source_y /= denominator
        return source_x, source_y, safe

    def forward(self, source_x, source_y):
        """Map source positions to output positions (the inverse direction)."""
        try:
            matrix = np.linalg.inv(self.matrix)
        except np.linalg.LinAlgError:
            return None, None
        denominator = (
            matrix[2, 0] * source_x + matrix[2, 1] * source_y + matrix[2, 2]
        )
        denominator = np.where(np.abs(denominator) > 1e-12, denominator, np.nan)
        return (
            (matrix[0, 0] * source_x + matrix[0, 1] * source_y + matrix[0, 2])
            / denominator,
            (matrix[1, 0] * source_x + matrix[1, 1] * source_y + matrix[1, 2])
            / denominator,
        )


class MeshMapper:
    """Inverse map for the mesh transform, from the dense curved grid.

    The dense grid is rasterized triangle by triangle in *output* space; the
    barycentric weights of each covered output probe give its source position.
    That is already a backward map, so unlike the quad case there is nothing to
    invert - but it must be rasterized rather than evaluated analytically.

    ``dense_points`` are ``(x, y)`` pairs in output-image coordinates, laid out
    row-major over ``dense_cols x dense_rows``.
    """

    def __init__(
        self, dense_points, dense_cols, dense_rows, source_width, source_height
    ):
        self.points = np.asarray(dense_points, dtype=np.float64).reshape(-1, 2)
        self.dense_cols = int(dense_cols)
        self.dense_rows = int(dense_rows)
        self.source_width = int(source_width)
        self.source_height = int(source_height)

    def map_band(self, width, y0, y1, subsamples):
        out_width = int(width) * int(subsamples)
        out_height = (int(y1) - int(y0)) * int(subsamples)
        source_x = np.zeros((out_height, out_width), dtype=np.float64)
        source_y = np.zeros((out_height, out_width), dtype=np.float64)
        valid = np.zeros((out_height, out_width), dtype=bool)

        scale = float(subsamples)
        # Output-space triangle vertices scaled into probe coordinates, with the
        # band origin removed so each band indexes from 0.
        points = self.points * scale
        origin_y = float(y0) * scale
        cols, rows = self.dense_cols, self.dense_rows
        span_x = max(1, cols - 1)
        span_y = max(1, rows - 1)
        last_x = float(self.source_width - 1)
        last_y = float(self.source_height - 1)

        def raster(target_indices, source_triangle):
            triangle = points[list(target_indices)]
            target_x = triangle[:, 0]
            target_y = triangle[:, 1] - origin_y
            min_x = max(0, int(math.floor(float(target_x.min()))))
            max_x = min(out_width - 1, int(math.ceil(float(target_x.max()))))
            min_y = max(0, int(math.floor(float(target_y.min()))))
            max_y = min(out_height - 1, int(math.ceil(float(target_y.max()))))
            if max_x < min_x or max_y < min_y:
                return
            denominator = (
                (target_y[1] - target_y[2]) * (target_x[0] - target_x[2])
                + (target_x[2] - target_x[1]) * (target_y[0] - target_y[2])
            )
            if abs(denominator) < 1e-8:
                return
            yy, xx = np.mgrid[min_y:max_y + 1, min_x:max_x + 1]
            # Probe centers sit half a subsample step inside the cell.
            xx = xx + 0.5
            yy = yy + 0.5
            weight0 = (
                (target_y[1] - target_y[2]) * (xx - target_x[2])
                + (target_x[2] - target_x[1]) * (yy - target_y[2])
            ) / denominator
            weight1 = (
                (target_y[2] - target_y[0]) * (xx - target_x[2])
                + (target_x[0] - target_x[2]) * (yy - target_y[2])
            ) / denominator
            weight2 = 1.0 - weight0 - weight1
            inside = (
                (weight0 >= -1e-6) & (weight1 >= -1e-6) & (weight2 >= -1e-6)
            )
            if not np.any(inside):
                return
            mapped_x = (
                weight0 * source_triangle[0][0]
                + weight1 * source_triangle[1][0]
                + weight2 * source_triangle[2][0]
            )
            mapped_y = (
                weight0 * source_triangle[0][1]
                + weight1 * source_triangle[1][1]
                + weight2 * source_triangle[2][1]
            )
            region_x = source_x[min_y:max_y + 1, min_x:max_x + 1]
            region_y = source_y[min_y:max_y + 1, min_x:max_x + 1]
            region_valid = valid[min_y:max_y + 1, min_x:max_x + 1]
            # The grid is anchored on source pixel *centers*; shift to the
            # area convention the sampler uses (pixel n spans [n, n+1)).
            region_x[inside] = mapped_x[inside] + 0.5
            region_y[inside] = mapped_y[inside] + 0.5
            region_valid[inside] = True

        for grid_y in range(rows - 1):
            top = last_y * grid_y / span_y
            bottom = last_y * (grid_y + 1) / span_y
            row_base = grid_y * cols
            for grid_x in range(cols - 1):
                left = last_x * grid_x / span_x
                right = last_x * (grid_x + 1) / span_x
                index = row_base + grid_x
                raster(
                    (index, index + 1, index + cols + 1),
                    ((left, top), (right, top), (right, bottom)),
                )
                raster(
                    (index, index + cols + 1, index + cols),
                    ((left, top), (right, bottom), (left, bottom)),
                )
        return source_x, source_y, valid

    def forward(self, source_x, source_y):
        """Map source positions to output positions.

        The dense grid samples the source on a regular lattice, so the forward
        direction is a bilinear lookup in that grid - no inversion needed.
        """
        cols, rows = self.dense_cols, self.dense_rows
        span_x = max(1, cols - 1)
        span_y = max(1, rows - 1)
        last_x = max(1e-9, float(self.source_width - 1))
        last_y = max(1e-9, float(self.source_height - 1))
        grid_x = np.clip(
            np.asarray(source_x, dtype=np.float64) / last_x * span_x,
            0.0, span_x,
        )
        grid_y = np.clip(
            np.asarray(source_y, dtype=np.float64) / last_y * span_y,
            0.0, span_y,
        )
        x0 = np.minimum(grid_x.astype(np.int64), cols - 2)
        y0 = np.minimum(grid_y.astype(np.int64), rows - 2)
        fraction_x = (grid_x - x0)[..., None]
        fraction_y = (grid_y - y0)[..., None]
        points = self.points.reshape(rows, cols, 2)
        top = (
            points[y0, x0] * (1.0 - fraction_x) + points[y0, x0 + 1] * fraction_x
        )
        bottom = (
            points[y0 + 1, x0] * (1.0 - fraction_x)
            + points[y0 + 1, x0 + 1] * fraction_x
        )
        mapped = top * (1.0 - fraction_y) + bottom * fraction_y
        return mapped[..., 0], mapped[..., 1]


def _sample_labels(label_image, source_x, source_y, valid):
    """Nearest-neighbour label lookup; probes outside the source read 0.

    ``valid`` may be a scalar ``True`` when the mapping cannot fail.
    """
    height, width = label_image.shape
    x = np.floor(source_x, out=source_x).astype(np.int32)
    y = np.floor(source_y, out=source_y).astype(np.int32)
    inside = (x >= 0) & (x < width) & (y >= 0) & (y < height)
    if valid is not True:
        inside &= valid
    np.clip(x, 0, width - 1, out=x)
    np.clip(y, 0, height - 1, out=y)
    # One flat gather beats 2-D fancy indexing: the index arithmetic stays in
    # int32 and NumPy takes a single pass over the label image.
    y *= width
    y += x
    labels = np.take(label_image.reshape(-1), y)
    labels[~inside] = 0
    return labels


def _probe_axis(labels, subsamples):
    """Reshape ``(H*n, W*n)`` probes into ``(H, W, n*n)`` per-pixel probes."""
    high_rows, high_cols = labels.shape
    rows = high_rows // subsamples
    cols = high_cols // subsamples
    return (
        labels.reshape(rows, subsamples, cols, subsamples)
        .transpose(0, 2, 1, 3)
        .reshape(rows, cols, subsamples * subsamples)
    )


def _majority(values):
    """Per-row winner of ``values`` (shape ``(M, S)``), lowest value wins ties.

    Returns ``(winner, count)``.  Implemented by sorting each row and measuring
    run lengths, which is O(S log S) with S = 16 and fully vectorized.
    """
    count = values.shape[1]
    order = np.sort(values, axis=1)
    positions = np.arange(count)
    starts_run = np.empty(order.shape, dtype=bool)
    starts_run[:, 0] = True
    np.not_equal(order[:, 1:], order[:, :-1], out=starts_run[:, 1:])
    ends_run = np.empty(order.shape, dtype=bool)
    ends_run[:, :-1] = starts_run[:, 1:]
    ends_run[:, -1] = True
    run_start = np.maximum.accumulate(
        np.where(starts_run, positions, 0), axis=1
    )
    run_end = np.minimum.accumulate(
        np.where(ends_run, positions, count - 1)[:, ::-1], axis=1
    )[:, ::-1]
    run_length = run_end - run_start + 1
    # argmax returns the *first* maximum, and rows are sorted ascending, so ties
    # resolve to the smallest value - i.e. the highest-priority rank.
    best = np.argmax(run_length, axis=1)
    rows = np.arange(order.shape[0])
    return order[rows, best], run_length[rows, best]


def _area_scale(mapper, source_width, source_height):
    """Rough output/source length ratio, measured at the middle of the source."""
    center_x = source_width / 2.0
    center_y = source_height / 2.0
    span = max(1.0, min(source_width, source_height) / 8.0)
    xs = np.array([center_x, center_x + span, center_x])
    ys = np.array([center_y, center_y, center_y + span])
    mapped_x, mapped_y = mapper.forward(xs, ys)
    if mapped_x is None or not np.all(np.isfinite(mapped_x)):
        return 1.0
    edge_x = (mapped_x[1] - mapped_x[0], mapped_y[1] - mapped_y[0])
    edge_y = (mapped_x[2] - mapped_x[0], mapped_y[2] - mapped_y[0])
    area = abs(edge_x[0] * edge_y[1] - edge_x[1] * edge_y[0])
    return math.sqrt(area) / span


def _splat_label(label_image, mapper, label, out_width, out_height):
    """Output pixels hit by forward-mapping every source pixel of ``label``.

    Coverage alone cannot keep a line that shrinks below one pixel: its area
    falls under every threshold and the line dissolves into dashes.  Mapping the
    source pixels *forward* fixes that, because a connected run of source pixels
    lands on output pixels at most one step apart, so the line stays connected
    however far it is shrunk.  Forward mapping leaves holes when enlarging,
    which is exactly the case where coverage already does the right thing - so
    this is only used on a shrink, and only as a union with the coverage result.
    """
    source_y, source_x = np.nonzero(label_image == label)
    if source_y.size == 0:
        return None
    mapped_x, mapped_y = mapper.forward(
        source_x.astype(np.float64) + 0.5, source_y.astype(np.float64) + 0.5
    )
    if mapped_x is None:
        return None
    finite = np.isfinite(mapped_x) & np.isfinite(mapped_y)
    x = np.floor(mapped_x[finite]).astype(np.int64)
    y = np.floor(mapped_y[finite]).astype(np.int64)
    inside = (x >= 0) & (x < out_width) & (y >= 0) & (y < out_height)
    hit = np.zeros((out_height, out_width), dtype=bool)
    hit[y[inside], x[inside]] = True
    return hit


def render_labels(
    label_image,
    mapper,
    out_width,
    out_height,
    *,
    subsamples=DEFAULT_SUBSAMPLES,
    coverage_labels=(),
    protect_labels=(),
    band_rows=DEFAULT_BAND_ROWS,
    progress=None,
):
    """Transform ``label_image`` through ``mapper`` onto an output raster.

    ``label_image`` is ``uint16`` with 0 meaning "nothing here"; label ids must
    already be ordered by priority (a lower id wins a tie).  Returns
    ``(labels, coverage)`` where ``labels`` is ``(out_height, out_width)``
    ``uint16`` and ``coverage`` maps each id in ``coverage_labels`` to a
    ``uint8`` array holding that label's true area coverage (0-255) - not a
    blurred mask, so thresholding it stays meaningful.

    Ids listed in ``protect_labels`` additionally keep their coverage ridge, so
    a line thinned below one pixel by a shrink stays connected instead of
    breaking into dashes.  Protected ids must also appear in ``coverage_labels``.
    """
    label_image = np.ascontiguousarray(label_image, dtype=np.uint16)
    out_width = max(1, int(out_width))
    out_height = max(1, int(out_height))
    subsamples = max(1, int(subsamples))
    probes = subsamples * subsamples

    result = np.zeros((out_height, out_width), dtype=np.uint16)
    coverage = {
        int(label): np.zeros((out_height, out_width), dtype=np.uint8)
        for label in coverage_labels
    }

    band_rows = max(1, int(band_rows))
    band_count = max(1, math.ceil(out_height / band_rows))
    for band_index, y0 in enumerate(range(0, out_height, band_rows)):
        y1 = min(out_height, y0 + band_rows)
        if progress is not None:
            progress(band_index, band_count)
        source_x, source_y, valid = mapper.map_band(
            out_width, y0, y1, subsamples
        )
        sampled = _sample_labels(label_image, source_x, source_y, valid)
        per_pixel = _probe_axis(sampled, subsamples)
        rows = y1 - y0
        flat = per_pixel.reshape(rows * out_width, probes)

        # Only pixels straddling a boundary need their probes counted; every
        # interior pixel has all probes on one color.  Boundaries are a small
        # fraction of any real drawing, so resolving just those is much cheaper
        # than sorting the whole band.
        low = flat.min(axis=1)
        mixed = low != flat.max(axis=1)
        winner = low
        if probes > 1 and np.any(mixed):
            winner = winner.copy()
            winner[mixed], _count = _majority(flat[mixed])
        result[y0:y1] = winner.reshape(rows, out_width)

        for label, buffer in coverage.items():
            hits = np.where(low == label, probes, 0).astype(np.uint16)
            if np.any(mixed):
                hits[mixed] = np.count_nonzero(
                    flat[mixed] == label, axis=1
                ).astype(np.uint16)
            buffer[y0:y1] = ((hits * 255) // probes).astype(
                np.uint8
            ).reshape(rows, out_width)
    if progress is not None:
        progress(band_count, band_count)

    # Only a shrink can thin a line below one pixel.  At 1:1 or larger the
    # coverage result is already complete, and splatting there would only pad
    # the edge of lines that are perfectly fine.
    shrinking = protect_labels and _area_scale(
        mapper, label_image.shape[1], label_image.shape[0]
    ) < 0.999
    for label in protect_labels if shrinking else ():
        label = int(label)
        hit = _splat_label(label_image, mapper, label, out_width, out_height)
        if hit is None:
            continue
        result[hit] = np.uint16(label)
        buffer = coverage.get(label)
        if buffer is not None:
            # Report full coverage there so the caller's line-width threshold
            # cannot drop the pixels that keep the line connected.
            buffer[hit] = 255

    return result, coverage


def labels_to_rgba(labels, colors):
    """Expand a label image into binary RGBA using ``colors`` indexed by label.

    ``colors`` is an ``(N, 4) uint8`` array whose row 0 is the background.
    Output alpha is only ever 0 or 255.
    """
    palette = np.asarray(colors, dtype=np.uint8)
    labels = np.asarray(labels)
    clipped = np.clip(labels, 0, len(palette) - 1)
    rgba = palette[clipped]
    return np.ascontiguousarray(rgba)
