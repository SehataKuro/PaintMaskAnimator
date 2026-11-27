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
/// 書き出し（カットフォルダー・タイムシート・連番・影素材・サムネイル）。
pub mod export;
pub mod fill;
pub mod flow_color;
/// ストレート RGBA8888 の画像と矩形操作。
pub mod image;
/// 線・色のメニューの操作を 1 枚の層の画素にかける。
pub mod image_ops;
/// 読み込み（画像の置き方・カットフォルダー・CLIP の合成）。
pub mod import;
pub mod line_flow;
pub mod line_noise;
pub mod local_shift;
pub mod map_colors;
pub mod mask_transform;
pub mod palette;
pub mod palette_labels;
pub mod project;
pub mod reference_check;
/// 見本との違い・塗り間違いのチェック。
pub mod reference_scan;
pub mod regions;
/// 画素の選択範囲と、その中の編集。
pub mod selection;
pub mod thin;
pub mod tiled_color;
pub mod tone_curve;
/// 画面のツール操作（バケツ・串刺し塗り）。
pub mod tools;

pub use error::Error;
