//! 線端を調べる Zhang-Suen 細線化。各段階で同時に削る条件を numpy 版と一致させる。

pub fn thin(mask: &[u8], width: usize, height: usize, iterations: usize) -> Vec<u8> {
    if width == 0 || height == 0 {
        return Vec::new();
    }
    let stride = width + 2;
    let mut image = vec![0u8; stride * (height + 2)];
    for y in 0..height {
        for x in 0..width {
            image[(y + 1) * stride + x + 1] = u8::from(mask[y * width + x] != 0);
        }
    }
    let mut remove = Vec::new();
    for _ in 0..iterations {
        let mut changed = false;
        for step in 0..2 {
            remove.clear();
            for y in 1..=height {
                for x in 1..=width {
                    let i = y * stride + x;
                    if image[i] == 0 {
                        continue;
                    }
                    let ring = [
                        image[i - stride],
                        image[i - stride + 1],
                        image[i + 1],
                        image[i + stride + 1],
                        image[i + stride],
                        image[i + stride - 1],
                        image[i - 1],
                        image[i - stride - 1],
                    ];
                    let count: u8 = ring.iter().sum();
                    if !(2..=6).contains(&count) {
                        continue;
                    }
                    let transitions = (0..8)
                        .filter(|&j| ring[j] == 0 && ring[(j + 1) % 8] != 0)
                        .count();
                    let (p2, p4, p6, p8) = (ring[0], ring[2], ring[4], ring[6]);
                    let keep = if step == 0 {
                        (p2 & p4 & p6) | (p4 & p6 & p8)
                    } else {
                        (p2 & p4 & p8) | (p2 & p6 & p8)
                    };
                    if transitions == 1 && keep == 0 {
                        remove.push(i);
                    }
                }
            }
            changed |= !remove.is_empty();
            for &i in &remove {
                image[i] = 0;
            }
        }
        if !changed {
            break;
        }
    }
    let mut out = Vec::with_capacity(width * height);
    for y in 1..=height {
        out.extend_from_slice(&image[y * stride + 1..y * stride + 1 + width]);
    }
    out
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn does_not_remove_a_single_pixel_line() {
        let mask = [0, 1, 0, 0, 1, 0, 0, 1, 0];
        assert_eq!(thin(&mask, 3, 3, 12), mask);
    }
    #[test]
    fn zero_iterations_keeps_every_pixel() {
        assert_eq!(thin(&[1; 12], 4, 3, 0), vec![1; 12]);
    }
}
