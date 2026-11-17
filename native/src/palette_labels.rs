use std::thread;

use pma_core::palette_labels::{feature, Feature};
use pyo3::prelude::*;
use pyo3::types::PyBytes;

/// 画素範囲を従来と同じ幅で分け、最近傍の番号と距離を並列に求める。
fn nearest_colors(
    features: &[Feature],
    visible: &[u8],
    palette: &[Feature],
    threads: usize,
) -> (Vec<i16>, Vec<f32>) {
    let total = features.len();
    let mut labels = vec![-1; total];
    let mut distances = vec![f32::INFINITY; total];
    let chunk = total.div_ceil(threads.max(1)).max(1);
    thread::scope(|scope| {
        let mut label_rest: &mut [i16] = &mut labels;
        let mut distance_rest: &mut [f32] = &mut distances;
        let mut start = 0;
        while start < total {
            let rows = chunk.min(total - start);
            let (label_part, next_labels) = label_rest.split_at_mut(rows);
            let (distance_part, next_distances) = distance_rest.split_at_mut(rows);
            label_rest = next_labels;
            distance_rest = next_distances;
            let offset = start;
            scope.spawn(move || {
                pma_core::palette_labels::nearest_colors_into(
                    &features[offset..offset + rows],
                    &visible[offset..offset + rows],
                    palette,
                    label_part,
                    distance_part,
                );
            });
            start += rows;
        }
    });
    (labels, distances)
}

/// 色面を種にして、画素ごとのパレットの番号（未確定は -1）を決める。
fn surface_guided_labels(
    rgb: &[u8],
    width: usize,
    height: usize,
    visible: &[u8],
    palette: &[u8],
    spatial_refinement: bool,
    threads: usize,
) -> Vec<i16> {
    let palette_features: Vec<Feature> = palette
        .chunks_exact(3)
        .map(|color| feature([color[0], color[1], color[2]]))
        .collect();
    let features: Vec<Feature> = rgb
        .chunks_exact(3)
        .map(|pixel| feature([pixel[0], pixel[1], pixel[2]]))
        .collect();
    let (nearest, nearest_distance) =
        nearest_colors(&features, visible, &palette_features, threads);
    if !spatial_refinement || palette_features.is_empty() {
        return nearest;
    }

    pma_core::palette_labels::refine_labels(
        rgb,
        width,
        height,
        visible,
        &features,
        &palette_features,
        &nearest,
        &nearest_distance,
    )
}

/// `color_ops._surface_guided_palette_labels_numpy` と同じパレット番号を返す。
/// 各番号を i16 のネイティブバイト順に並べた Python バイト列にする。
#[pyfunction(name = "surface_guided_palette_labels")]
pub fn py_surface_guided_palette_labels<'py>(
    py: Python<'py>,
    rgb: &[u8],
    width: usize,
    height: usize,
    visible: &[u8],
    palette: &[u8],
    spatial_refinement: bool,
) -> PyResult<Bound<'py, PyBytes>> {
    let invalid = |message: &str| pyo3::exceptions::PyValueError::new_err(message.to_string());
    let total = width
        .checked_mul(height)
        .ok_or_else(|| invalid("image is too large"))?;
    if total.checked_mul(3) != Some(rgb.len()) {
        return Err(invalid("rgb size does not match width x height"));
    }
    if visible.len() != total {
        return Err(invalid("visible size does not match width x height"));
    }
    if !palette.len().is_multiple_of(3) {
        return Err(invalid("expected packed rgb palette colors"));
    }
    if palette.len() / 3 > i16::MAX as usize {
        return Err(invalid("palette has too many colors"));
    }
    let threads = thread::available_parallelism()
        .map(|value| value.get())
        .unwrap_or(1);
    let labels = py.detach(|| {
        surface_guided_labels(
            rgb,
            width,
            height,
            visible,
            palette,
            spatial_refinement,
            threads,
        )
    });
    let bytes: Vec<u8> = labels
        .iter()
        .flat_map(|value| value.to_ne_bytes())
        .collect();
    Ok(PyBytes::new(py, &bytes))
}

#[cfg(test)]
mod tests {
    use super::*;

    /// 赤・緑・青。色相が 120 度ずつ離れる。
    fn primaries() -> Vec<u8> {
        vec![255, 0, 0, 0, 255, 0, 0, 0, 255]
    }

    #[test]
    fn threads_do_not_change_the_result() {
        // 色相を少しずつ変えた帯。最近傍の判定が画素ごとに分かれる。
        let mut rgb = Vec::new();
        for index in 0..256u16 {
            rgb.extend_from_slice(&[(index % 256) as u8, (index * 3 % 256) as u8, 40]);
        }
        let visible = vec![1u8; 256];
        let single = pma_core::palette_labels::surface_guided_labels(
            &rgb,
            16,
            16,
            &visible,
            &primaries(),
            true,
        );
        let many = surface_guided_labels(&rgb, 16, 16, &visible, &primaries(), true, 7);
        assert_eq!(single, many);
    }
}
