//! 文書のデータと操作。

/// タイムラインの枠と画像参照の解決。
pub mod timeline;

/// 旧形式と v2 の読み込みで保持する位置情報。
pub mod compatibility;
/// 文書、列、セル、およびシートの所有モデル。
pub mod model;
/// v2 の読込計画と文書の変換。
pub mod v2;

/// Python の文書グラフと文書の相互変換。
pub mod graph;
pub mod install_commands;

/// Python の文書窓口が使う ID で引く読み書き。
pub mod access;

mod v2_placement;

/// 構造操作が発行する変更一覧。
pub mod changes;
/// 文書の構造と描画を更新する操作。
pub mod commands;

/// コマ操作とタイムリマップの入力。
pub mod frame_commands;

/// 画素と初期画像を確定する描画操作。
pub mod drawing_commands;

/// コマの長さ変更の Undo で列の位置を記録の値に戻す操作。
pub mod exposure_commands;

/// シート位置の編集と複数区間移動の入力。
pub mod position_commands;

mod v2_order;

mod number_commands;
/// 配置待ちの番号をシートへ移す操作と窓口への更新情報。
pub mod pending_commands;
mod tween_commands;

/// 比較テストのための操作列適用。
pub mod comparison;
