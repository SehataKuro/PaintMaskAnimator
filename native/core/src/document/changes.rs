//! Document の操作が発行する変更一覧。

use super::model::CellNumber;
use crate::image::Rect;

/// Python の TrackStructure.operation に対応する列の操作。
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum TrackOperation {
    /// 列の挿入。
    Insert,
    /// 列の削除。
    Remove,
    /// 列の複製。
    Duplicate,
    /// 連続する列の統合。
    Merge,
    /// 列の並べ替え。
    Move,
}

/// Python の FrameStructure.operation に対応するコマの操作。
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum FrameOperation {
    /// コマの伸長。
    Extend,
    /// コマの複製。
    Duplicate,
    /// 末尾の切り詰め。
    Trim,
    /// 履歴からの復元。
    Restore,
}

/// Python の ChangeSet と同じ発行順の変更。
pub type ChangeSet = Vec<Change>;

/// 操作の結果を、表示順のインデックスで通知する。
#[derive(Clone, Debug, PartialEq, Eq)]
pub enum Change {
    /// 前後の表示順で影響を受ける列。
    TrackStructure {
        /// 列の操作。
        operation: TrackOperation,
        /// 操作前の列。
        before: Vec<usize>,
        /// 操作後の列。
        after: Vec<usize>,
    },
    /// 列設定の変更。
    TrackProperties {
        /// 対象列。
        indices: Vec<usize>,
        /// Python の属性名。入力順。
        fields: Vec<&'static str>,
    },
    /// 選択列の移動。
    Selection {
        /// 操作後の選択列。
        layer_index: usize,
    },
    /// コマ数の変更。
    FrameStructure {
        /// コマの操作。
        operation: FrameOperation,
        /// 変更前のコマ数。
        before: usize,
        /// 変更後のコマ数。
        after: usize,
        /// 最初に影響を受けるコマ。
        start: usize,
    },
    /// ある列でシート参照が変わった半開区間。
    SheetSpans {
        /// 対象列。
        layer_index: usize,
        /// 変更区間の先頭。
        start: usize,
        /// 変更区間の末尾の次。
        stop: usize,
    },
    /// 画素の変更。矩形なしは画像全体の差し替え。
    DrawingPixels {
        /// 対象列。
        layer_index: usize,
        /// 対象の絵番号。
        number: Option<CellNumber>,
        /// 位置指定時のコマ。
        frame: Option<usize>,
        /// 画像座標での変更矩形。
        rects: Vec<Rect>,
    },
    /// 絵の属性の変更。
    DrawingAttributes {
        /// 対象列。
        layer_index: usize,
        /// 対象の絵番号。
        number: Option<CellNumber>,
        /// 位置指定時のコマ。
        frame: Option<usize>,
        /// 変更された属性名。
        fields: Vec<&'static str>,
    },
    /// 選択コマの移動。
    Cursor {
        /// 操作後のコマ。
        frame: usize,
        /// 選択変更の通知を発行するか。
        notify: bool,
    },
}
