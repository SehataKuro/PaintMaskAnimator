//! PaintMaskAnimator の計算とデータ。Python・Qt・ファイル・スレッドに触れない。
//! 規約は issue #96 §2。
pub mod alpha_runs;
pub mod auto_paint;
pub mod brush;
pub mod color_stats;
pub mod colors;
/// キャンバスのレイヤー合成（オニオン・表示プレビュー・縮小表示を含む）。
pub mod compose;
pub mod display;
pub mod document;
pub mod error;
pub mod fill;
pub mod flow_color;
/// ストレート RGBA8888 の画像と矩形操作。
pub mod image;
pub mod line_flow;
pub mod line_noise;
pub mod local_shift;
pub mod map_colors;
pub mod mask_transform;
pub mod palette;
pub mod palette_labels;
pub mod project;
pub mod reference_check;
pub mod regions;
/// 画素の選択範囲と、その中の編集。
pub mod selection;
pub mod thin;
pub mod tiled_color;
pub mod tone_curve;
/// 使用色パネルの状態（親子・選択・表示・鍵・フォルダー・置換色）とカラーチャート。
pub mod used_colors;

pub use error::Error;
