//! 変形の形の計算（ハンドル・メッシュの曲面・四角形の射影）。
//!
//! Python の `canvas_transform_geometry.py`・`canvas_transform_mask.py` のメッシュの部分・
//! `geometry.py` と同じ式・同じ演算順で書く。座標はレイヤーの画素座標。

use crate::compose::painter::Transform;

/// 点（x, y）。
pub type Pt = (f64, f64);

/// 実数の矩形 (左, 上, 幅, 高さ)。
pub type RectF = (f64, f64, f64, f64);

/// 8 元連立一次方程式を部分ピボットの消去法で解く（`numpy.linalg.solve`）。
/// 特異なら None。
#[allow(clippy::needless_range_loop)]
fn solve8(mut a: [[f64; 8]; 8], mut b: [f64; 8]) -> Option<[f64; 8]> {
    for k in 0..8 {
        let mut pivot = k;
        for i in k + 1..8 {
            if a[i][k].abs() > a[pivot][k].abs() {
                pivot = i;
            }
        }
        if a[pivot][k] == 0.0 {
            return None;
        }
        a.swap(k, pivot);
        b.swap(k, pivot);
        let reciprocal = 1.0 / a[k][k];
        for i in k + 1..8 {
            a[i][k] *= reciprocal;
        }
        for j in k + 1..8 {
            let factor = a[k][j];
            for i in k + 1..8 {
                a[i][j] -= a[i][k] * factor;
            }
        }
    }
    for j in 0..8 {
        let value = b[j];
        for i in j + 1..8 {
            b[i] -= value * a[i][j];
        }
    }
    for j in (0..8).rev() {
        b[j] /= a[j][j];
        let value = b[j];
        for i in 0..j {
            b[i] -= value * a[i][j];
        }
    }
    b.iter().all(|v| v.is_finite()).then_some(b)
}

/// 4 点の対応から射影の 8 係数 (a, b, c, d, e, f, g, h) を求める
/// （`geometry.quad_homography`・`mask_transform.homography_matrix`）。
pub fn homography(source: &[Pt; 4], target: &[Pt; 4]) -> Option<[f64; 8]> {
    let mut a = [[0.0; 8]; 8];
    let mut b = [0.0; 8];
    for (index, (&(x, y), &(u, v))) in source.iter().zip(target).enumerate() {
        a[index * 2] = [x, y, 1.0, 0.0, 0.0, 0.0, -u * x, -u * y];
        b[index * 2] = u;
        a[index * 2 + 1] = [0.0, 0.0, 0.0, x, y, 1.0, -v * x, -v * y];
        b[index * 2 + 1] = v;
    }
    solve8(a, b)
}

/// 大きさ `width × height` の元の画像を `points`（左上から時計回り）へ写す QTransform
/// （`_project_transform_source` の行列）。解けなければ恒等。
pub fn quad_transform(width: f64, height: f64, points: &[Pt]) -> Transform {
    let source = [(0.0, 0.0), (width, 0.0), (width, height), (0.0, height)];
    let target = [points[0], points[1], points[2], points[3]];
    match homography(&source, &target) {
        Some([a, b, c, d, e, f, g, h]) => Transform::new(a, d, g, b, e, h, c, f, 1.0),
        None => Transform::identity(),
    }
}

/// 3×3 の行列の逆（`numpy.linalg.inv`）。特異なら None。
#[allow(clippy::needless_range_loop)]
pub fn invert3(m: &[f64; 9]) -> Option<[f64; 9]> {
    let mut a = [[m[0], m[1], m[2]], [m[3], m[4], m[5]], [m[6], m[7], m[8]]];
    let mut inv = [[1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]];
    for k in 0..3 {
        let mut pivot = k;
        for i in k + 1..3 {
            if a[i][k].abs() > a[pivot][k].abs() {
                pivot = i;
            }
        }
        if a[pivot][k] == 0.0 {
            return None;
        }
        a.swap(k, pivot);
        inv.swap(k, pivot);
        for i in 0..3 {
            if i == k {
                continue;
            }
            let factor = a[i][k] / a[k][k];
            for j in 0..3 {
                a[i][j] -= factor * a[k][j];
                inv[i][j] -= factor * inv[k][j];
            }
        }
    }
    let mut out = [0.0; 9];
    for i in 0..3 {
        for j in 0..3 {
            out[i * 3 + j] = inv[i][j] / a[i][i];
        }
    }
    out.iter().all(|v| v.is_finite()).then_some(out)
}

/// 等間隔の格子点（`geometry.regular_grid_points`）。
pub fn regular_grid_points(rect: RectF, cols: usize, rows: usize) -> Vec<Pt> {
    let cols = cols.max(2);
    let rows = rows.max(2);
    let width = rect.2.max(0.0);
    let height = rect.3.max(0.0);
    let mut points = Vec::with_capacity(cols * rows);
    for gy in 0..rows {
        for gx in 0..cols {
            points.push((
                rect.0 + width * gx as f64 / (cols - 1) as f64,
                rect.1 + height * gy as f64 / (rows - 1) as f64,
            ));
        }
    }
    points
}

/// メッシュの基準格子（`_regular_mesh_reference_points`）。画素の中心どうしを結ぶので、
/// 幅は元の画像の幅 − 1。
pub fn mesh_reference_points(
    rect: RectF,
    cols: usize,
    rows: usize,
    source: Option<(usize, usize)>,
) -> Vec<Pt> {
    let cols = cols.max(2);
    let rows = rows.max(2);
    let (pixel_width, pixel_height) = match source {
        Some((w, h)) => ((w as f64 - 1.0).max(0.0), (h as f64 - 1.0).max(0.0)),
        None => ((rect.2 - 1.0).max(0.0), (rect.3 - 1.0).max(0.0)),
    };
    let mut points = Vec::with_capacity(cols * rows);
    for gy in 0..rows {
        for gx in 0..cols {
            points.push((
                rect.0 + pixel_width * gx as f64 / (cols - 1) as f64,
                rect.1 + pixel_height * gy as f64 / (rows - 1) as f64,
            ));
        }
    }
    points
}

/// 制御点を通る Catmull-Rom（`geometry.mesh_catmull_scalar`）。
pub fn catmull(p0: f64, p1: f64, p2: f64, p3: f64, t: f64) -> f64 {
    let t = t.clamp(0.0, 1.0);
    let t2 = t * t;
    let t3 = t2 * t;
    0.5 * (2.0 * p1
        + (-p0 + p2) * t
        + (2.0 * p0 - 5.0 * p1 + 4.0 * p2 - p3) * t2
        + (-p0 + 3.0 * p1 - 3.0 * p2 + p3) * t3)
}

/// メッシュの格子座標 (grid_x, grid_y) の位置（`_mesh_curve_point`）。基準格子からの
/// ずれだけを滑らかに補い、基準格子の位置は双線形で補う。
pub fn mesh_curve_point(
    points: &[Pt],
    reference: &[Pt],
    cols: usize,
    rows: usize,
    grid_x: f64,
    grid_y: f64,
) -> Pt {
    if points.is_empty() || cols < 2 || rows < 2 || reference.len() != cols * rows {
        return (0.0, 0.0);
    }
    let grid_x = grid_x.clamp(0.0, (cols - 1) as f64);
    let grid_y = grid_y.clamp(0.0, (rows - 1) as f64);
    let cell_x = (cols - 2).min(grid_x.floor() as usize);
    let cell_y = (rows - 2).min(grid_y.floor() as usize);
    let local_x = grid_x - cell_x as f64;
    let local_y = grid_y - cell_y as f64;
    let displacement = |row: i64, col: i64| {
        let row = row.clamp(0, rows as i64 - 1) as usize;
        let col = col.clamp(0, cols as i64 - 1) as usize;
        let index = row * cols + col;
        (
            points[index].0 - reference[index].0,
            points[index].1 - reference[index].1,
        )
    };
    let (cx, cy) = (cell_x as i64, cell_y as i64);
    let mut horizontal = [(0.0, 0.0); 4];
    for (slot, row) in (cy - 1..cy + 3).enumerate() {
        let p0 = displacement(row, cx - 1);
        let p1 = displacement(row, cx);
        let p2 = displacement(row, cx + 1);
        let p3 = displacement(row, cx + 2);
        horizontal[slot] = (
            catmull(p0.0, p1.0, p2.0, p3.0, local_x),
            catmull(p0.1, p1.1, p2.1, p3.1, local_x),
        );
    }
    let h = &horizontal;
    let dx = catmull(h[0].0, h[1].0, h[2].0, h[3].0, local_y);
    let dy = catmull(h[0].1, h[1].1, h[2].1, h[3].1, local_y);
    let r00 = reference[cell_y * cols + cell_x];
    let r10 = reference[cell_y * cols + cell_x + 1];
    let r01 = reference[(cell_y + 1) * cols + cell_x];
    let r11 = reference[(cell_y + 1) * cols + cell_x + 1];
    let base_x = (1.0 - local_y) * ((1.0 - local_x) * r00.0 + local_x * r10.0)
        + local_y * ((1.0 - local_x) * r01.0 + local_x * r11.0);
    let base_y = (1.0 - local_y) * ((1.0 - local_x) * r00.1 + local_x * r10.1)
        + local_y * ((1.0 - local_x) * r01.1 + local_x * r11.1);
    (base_x + dx, base_y + dy)
}

/// 細かく分けた曲面の格子（`_curved_mesh_points`）。(点, 横の数, 縦の数)。
pub fn curved_mesh_points(
    points: &[Pt],
    reference: &[Pt],
    cols: usize,
    rows: usize,
    subdivisions: usize,
) -> (Vec<Pt>, usize, usize) {
    let subdivisions = subdivisions.max(2);
    let dense_cols = (cols - 1) * subdivisions + 1;
    let dense_rows = (rows - 1) * subdivisions + 1;
    let mut dense = Vec::with_capacity(dense_cols * dense_rows);
    for dy in 0..dense_rows {
        let grid_y = dy as f64 / subdivisions as f64;
        for dx in 0..dense_cols {
            let grid_x = dx as f64 / subdivisions as f64;
            dense.push(mesh_curve_point(
                points, reference, cols, rows, grid_x, grid_y,
            ));
        }
    }
    (dense, dense_cols, dense_rows)
}

/// 変形の外周（`transform_outer_polygon`）。四角形は 4 点、メッシュは曲面の外周。
pub fn outer_polygon(
    mesh: bool,
    points: &[Pt],
    reference: &[Pt],
    cols: usize,
    rows: usize,
) -> Vec<Pt> {
    if points.is_empty() {
        return Vec::new();
    }
    if !mesh {
        return points.iter().take(4).copied().collect();
    }
    let cols = cols.max(2);
    let rows = rows.max(2);
    if points.len() != cols * rows {
        return Vec::new();
    }
    let sub = 8usize;
    let curve = |gx: f64, gy: f64| mesh_curve_point(points, reference, cols, rows, gx, gy);
    let mut outline = Vec::new();
    for step in 0..(cols - 1) * sub + 1 {
        outline.push(curve(step as f64 / sub as f64, 0.0));
    }
    for step in 1..(rows - 1) * sub + 1 {
        outline.push(curve((cols - 1) as f64, step as f64 / sub as f64));
    }
    for step in (0..(cols - 1) * sub).rev() {
        outline.push(curve(step as f64 / sub as f64, (rows - 1) as f64));
    }
    for step in (1..(rows - 1) * sub).rev() {
        outline.push(curve(0.0, step as f64 / sub as f64));
    }
    outline
}

/// 点の重心（`transform_center`）。
pub fn center(points: &[Pt]) -> Pt {
    if points.is_empty() {
        return (0.0, 0.0);
    }
    let n = points.len() as f64;
    (
        points.iter().map(|p| p.0).sum::<f64>() / n,
        points.iter().map(|p| p.1).sum::<f64>() / n,
    )
}

/// 回転のつまみの位置（`transform_rotation_handle`）。上の辺の中点から、重心と反対側へ
/// 画面で 36 画素の所。
pub fn rotation_handle(mesh: bool, points: &[Pt], cols: usize, zoom: f64) -> Option<Pt> {
    if points.is_empty() {
        return None;
    }
    let (p0, p1) = if mesh {
        let cols = cols.max(2);
        if points.len() < cols {
            return None;
        }
        (points[0], points[cols - 1])
    } else if points.len() == 4 {
        (points[0], points[1])
    } else {
        return None;
    };
    let mid = ((p0.0 + p1.0) / 2.0, (p0.1 + p1.1) / 2.0);
    let c = center(points);
    let (dx, dy) = (mid.0 - c.0, mid.1 - c.1);
    let mut length = dx.hypot(dy);
    if length == 0.0 {
        length = 1.0;
    }
    let distance = 36.0 / zoom.max(0.01);
    Some((
        mid.0 + dx / length * distance,
        mid.1 + dy / length * distance,
    ))
}

/// いちばん近いハンドル（画面で 14 画素以内）。無ければ None（`nearest_transform_handle`）。
pub fn nearest_handle(points: &[Pt], point: Pt, zoom: f64) -> Option<usize> {
    let radius = 14.0 / zoom.max(0.01);
    let mut best: Option<(usize, f64)> = None;
    for (index, handle) in points.iter().enumerate() {
        let d = (handle.0 - point.0).powi(2) + (handle.1 - point.1).powi(2);
        if best.is_none_or(|(_, b)| d < b) {
            best = Some((index, d));
        }
    }
    best.filter(|&(_, d)| d <= radius * radius).map(|(i, _)| i)
}

fn fuzzy_equal(a: f64, b: f64) -> bool {
    (a - b).abs() * 1_000_000_000_000.0 <= a.abs().min(b.abs())
}

/// `QPolygonF::containsPoint(pt, Qt::OddEvenFill)`。
pub fn polygon_contains(polygon: &[Pt], pt: Pt) -> bool {
    let Some(&first) = polygon.first() else {
        return false;
    };
    let mut winding = 0i64;
    let mut isect = |p1: Pt, p2: Pt| {
        let (mut x1, mut y1, mut x2, mut y2) = (p1.0, p1.1, p2.0, p2.1);
        let mut dir = 1;
        if fuzzy_equal(y1, y2) {
            return;
        } else if y2 < y1 {
            std::mem::swap(&mut x1, &mut x2);
            std::mem::swap(&mut y1, &mut y2);
            dir = -1;
        }
        let y = pt.1;
        if y >= y1 && y < y2 {
            let x = x1 + ((x2 - x1) / (y2 - y1)) * (y - y1);
            if x <= pt.0 {
                winding += dir;
            }
        }
    };
    let mut last = first;
    for &point in &polygon[1..] {
        isect(last, point);
        last = point;
    }
    let same = |a: f64, b: f64| {
        if a == 0.0 || b == 0.0 {
            (a - b).abs() <= 1e-12
        } else {
            fuzzy_equal(a, b)
        }
    };
    if !(same(last.0, first.0) && same(last.1, first.1)) {
        isect(last, first);
    }
    winding % 2 != 0
}

/// 点の並びを `center` のまわりに `angle`（ラジアン）回す。
pub fn rotate_points(points: &[Pt], center: Pt, angle: f64) -> Vec<Pt> {
    let (cs, sn) = (angle.cos(), angle.sin());
    points
        .iter()
        .map(|p| {
            (
                center.0 + (p.0 - center.0) * cs - (p.1 - center.1) * sn,
                center.1 + (p.0 - center.0) * sn + (p.1 - center.1) * cs,
            )
        })
        .collect()
}

/// メッシュの格子数を変えるとき、今の格子を双線形に引き直す（`set_transform_mesh_grid`）。
pub fn resample_mesh(
    old: &[Pt],
    old_cols: usize,
    old_rows: usize,
    cols: usize,
    rows: usize,
) -> Vec<Pt> {
    let sample = |u: f64, v: f64| {
        let x = (u * (old_cols - 1) as f64).clamp(0.0, old_cols as f64 - 1.0);
        let y = (v * (old_rows - 1) as f64).clamp(0.0, old_rows as f64 - 1.0);
        let (x0, y0) = (x.floor() as usize, y.floor() as usize);
        let (x1, y1) = ((old_cols - 1).min(x0 + 1), (old_rows - 1).min(y0 + 1));
        let (tx, ty) = (x - x0 as f64, y - y0 as f64);
        let p00 = old[y0 * old_cols + x0];
        let p10 = old[y0 * old_cols + x1];
        let p01 = old[y1 * old_cols + x0];
        let p11 = old[y1 * old_cols + x1];
        (
            (1.0 - ty) * ((1.0 - tx) * p00.0 + tx * p10.0) + ty * ((1.0 - tx) * p01.0 + tx * p11.0),
            (1.0 - ty) * ((1.0 - tx) * p00.1 + tx * p10.1) + ty * ((1.0 - tx) * p01.1 + tx * p11.1),
        )
    };
    let mut points = Vec::with_capacity(cols * rows);
    for gy in 0..rows {
        for gx in 0..cols {
            points.push(sample(
                gx as f64 / (cols - 1) as f64,
                gy as f64 / (rows - 1) as f64,
            ));
        }
    }
    points
}

/// 変形のどこかに縮小があるか（`_transform_is_reducing`）。
pub fn is_reducing(
    mesh: bool,
    points: &[Pt],
    reference: &[Pt],
    cols: usize,
    rows: usize,
    source: (usize, usize),
) -> bool {
    if points.is_empty() {
        return false;
    }
    if mesh {
        if points.len() != cols * rows || reference.len() != cols * rows {
            return false;
        }
        let mut pairs = Vec::new();
        for row in 0..rows {
            for col in 0..cols - 1 {
                let i = row * cols + col;
                pairs.push((i, i + 1));
            }
        }
        for row in 0..rows - 1 {
            for col in 0..cols {
                let i = row * cols + col;
                pairs.push((i, i + cols));
            }
        }
        return pairs.into_iter().any(|(a, b)| {
            let current = (points[b].0 - points[a].0).hypot(points[b].1 - points[a].1);
            let original = (reference[b].0 - reference[a].0).hypot(reference[b].1 - reference[a].1);
            original > 1e-8 && current < original * 0.9999
        });
    }
    if points.len() != 4 {
        return false;
    }
    let (sw, sh) = ((source.0 as f64).max(1.0), (source.1 as f64).max(1.0));
    let d = |a: Pt, b: Pt| (b.0 - a.0).hypot(b.1 - a.1);
    let (p0, p1, p2, p3) = (points[0], points[1], points[2], points[3]);
    d(p0, p1).min(d(p3, p2)) < sw * 0.9999 || d(p0, p3).min(d(p1, p2)) < sh * 0.9999
}

/// 選択の輪郭を、今の変形と同じ座標へ写す（`transformed_selection_outline`）。
pub fn transformed_outline(
    mesh: bool,
    points: &[Pt],
    reference: &[Pt],
    cols: usize,
    rows: usize,
    source_rect: RectF,
    outline: &[Pt],
) -> Vec<Pt> {
    let width = source_rect.2.max(1.0);
    let height = source_rect.3.max(1.0);
    if mesh {
        if points.len() != cols * rows {
            return outline.to_vec();
        }
        return outline
            .iter()
            .map(|p| {
                mesh_curve_point(
                    points,
                    reference,
                    cols,
                    rows,
                    (p.0 - source_rect.0) / width * (cols - 1) as f64,
                    (p.1 - source_rect.1) / height * (rows - 1) as f64,
                )
            })
            .collect();
    }
    if points.len() != 4 {
        return outline.to_vec();
    }
    let transform = quad_transform(width, height, points);
    outline
        .iter()
        .map(|p| transform.map(p.0 - source_rect.0, p.1 - source_rect.1))
        .collect()
}
