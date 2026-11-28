//! 文書のデータと操作。

/// タイムラインの枠と画像参照の解決。
pub mod timeline;

/// 旧形式と v2 の読み込みで保持する位置情報。
pub mod compatibility;
/// 文書、列、セル、およびシートの所有モデル。
pub mod model;
/// 新しい文書（Qt 版の「新規作成」と起動直後の文書）。
pub mod new_document;
/// v2 の読込計画と文書の変換。
pub mod v2;

/// Python の文書グラフと文書の相互変換。
pub mod graph;
pub mod install_commands;
/// 旧形式（format_version 1）の入力を文書にする。
pub mod legacy_v1;

/// Python の文書窓口が使う ID で引く読み書き。
pub mod access;

mod v2_placement;

/// 構造操作が発行する変更一覧。
pub mod changes;

/// 文書の構造と描画を更新する操作。
pub mod commands;
/// Undo の履歴の上限と分岐の判断。
pub mod history;

/// コマ操作とタイムリマップの入力。
pub mod frame_commands;

/// 画素と初期画像を確定する描画操作。
pub mod drawing_commands;

/// コマの長さ変更の Undo で列の位置を記録の値に戻す操作。
pub mod exposure_commands;

/// シート位置の編集と複数区間移動の入力。
pub mod position_commands;

mod v2_order;

/// 読み込んだアニメーション（CLIP STUDIO）で新しい文書を作る。
pub mod animation_commands;
/// 読み込んだ絵を新しい列として入れる操作。
pub mod import_commands;
pub mod initial_state;
mod number_commands;
/// 配置待ちの番号をシートへ移す操作と窓口への更新情報。
pub mod pending_commands;
/// タイムシートに書くコマ数。
mod sheet_length;
mod stage_commands;
mod tween_commands;

/// タイムラインの画面（Tauri）が使う判断。
pub mod timeline_panel;

/// 比較テストのための操作列適用。
pub mod comparison;
