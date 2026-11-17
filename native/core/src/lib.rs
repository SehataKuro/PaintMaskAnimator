//! PaintMaskAnimator の計算とデータ。Python・Qt・ファイル・スレッドに触れない。
//! 規約は issue #96 §2。
pub mod auto_paint;
pub mod display;
pub mod error;
pub mod line_flow;
pub mod line_noise;
pub mod map_colors;
pub mod mask_transform;
pub mod palette_labels;
pub mod project;
pub mod regions;
pub mod thin;

pub use error::Error;
