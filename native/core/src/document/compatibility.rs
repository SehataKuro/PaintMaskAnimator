use serde_json::Value;

use super::model::{Cell, CellNumber, Position, Reference, TrackSettings};

/// 旧連番保管に含まれていた画像以外の属性。Python の ArchiveHint に対応する。
#[derive(Clone, Debug)]
pub struct ArchiveHint {
    /// 旧データに絵として記録されたか。
    pub has_content: bool,
    /// 旧データに空キーとして記録されたか。
    pub is_blank_key: bool,
    /// 旧保存の尺。
    pub exposure: usize,
    /// 配置待ちだったか。
    pub pending: bool,
    /// 補間キー設定。
    pub tween: Option<Value>,
    /// 中割りの所属。
    pub tween_member: Option<String>,
}

/// Python の LegacyLayerV1 に対応する旧入力スロットの DTO。
#[derive(Clone, Debug)]
pub struct LegacySlot {
    /// 旧入力の列設定。列へ統合する前に保持する。
    pub settings: TrackSettings,
    /// 旧入力の位置属性と尺。
    pub state: ArchiveHint,
    /// 旧入力の連番。絵のない位置にも存在しうる。
    pub number: Option<CellNumber>,
    /// 旧入力の画像とセル属性。
    pub artwork: Option<Cell>,
}

/// 旧入力のキー境界と尺。Python の V1Position に対応する。
#[derive(Clone, Debug)]
pub struct LegacyPosition {
    /// 旧入力スロット。旧入力に位置がなければ None。
    pub legacy: Option<LegacySlot>,
    /// 旧キーの開始。
    pub key_start: bool,
    /// 旧空キーの開始。
    pub blank_start: bool,
    /// 旧データの配置待ち。
    pub pending: bool,
    /// v2 投影の長さとは別に保存する元入力の長さ。
    pub input_length: Option<usize>,
}

/// 旧投影の有効性を検査する、Python の位置署名に対応する値。
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct PositionGuard {
    /// スロットの参照。
    pub reference: Reference,
    /// 空キー・未使用に保持された番号を含む連番。
    pub number: Option<CellNumber>,
    /// 番号なしの位置が絵として記録されたか。
    pub unnumbered_content: bool,
    /// 配置待ち。
    pub pending: bool,
    /// 空キー開始。
    pub blank_start: bool,
}

impl PositionGuard {
    /// Python の _dense_import に使う位置署名を採取する。
    pub fn capture(position: &Position) -> Self {
        Self {
            reference: position.reference,
            number: position.number(),
            unnumbered_content: position.compatibility.unnumbered_content,
            pending: position.pending,
            blank_start: position.blank_start,
        }
    }

    /// 取り込み時から署名が変わっていないかを返す。
    pub fn matches(self, position: &Position) -> bool {
        self == Self::capture(position)
    }
}

/// Python の _dense_import に対応する表示・画像解決の二種類の参照。
#[derive(Clone, Copy, Debug)]
pub struct DenseImport {
    /// 取り込み時の位置署名。
    pub guard: PositionGuard,
    /// シート上の参照。
    pub sheet: Reference,
    /// 画像解決用の参照。
    pub resolved: Reference,
}

/// Python の _v2_resolve_source に対応する画像解決の参照元例外。
#[derive(Clone, Copy, Debug)]
pub struct ResolvedSource {
    /// 取り込み時の位置署名。
    pub guard: PositionGuard,
    /// 取り込み時のコマの表示インデックス。
    pub column: usize,
    /// 取り込み時のフレーム数。
    pub frame_count: usize,
    /// 参照元の表示インデックス。解決先なしなら None。
    pub source_column: Option<usize>,
}

/// ライブの位置から独立して保持する互換情報。
#[derive(Clone, Debug, Default)]
pub struct PositionCompatibility {
    /// 空キー・未使用・配置待ちのスロットに保持された番号。
    pub retained_number: Option<CellNumber>,
    /// 番号なしの位置が絵として記録されたか。
    pub unnumbered_content: bool,
    /// バンクに接続しないスロットの局所画像と属性。
    pub local: Option<Cell>,
    /// 原入力の旧位置。
    pub legacy: Option<LegacyPosition>,
    /// 取り込み時の密な参照。
    pub dense_import: Option<DenseImport>,
    /// v2 入力の長さ例外が元入力に由来するか。
    pub length_imported: Option<bool>,
    /// 画像解決の参照元例外。
    pub resolved_source: Option<ResolvedSource>,
}
