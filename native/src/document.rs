use crate::image::PyRgbaImage;
use pma_core::document::access::{ArtworkSlot, ImageField};
use pma_core::document::drawing_commands::DrawingImageGroup;
use pma_core::document::model::{CellNumber, Document, FrameId, TrackId};
use pma_core::document::{comparison, graph, v2};
use pma_core::image::{Rect, RgbaImage};
use pma_core::project::payload::decode_v2_plan;
use pyo3::{exceptions::PyValueError, prelude::*, types::PyBytes};

type PositionKeys = Vec<(u64, u64)>;
type PyDrawingImageGroup<'py> = (Vec<(usize, usize)>, PyRef<'py, PyRgbaImage>);
type LegacyInstallOutput = (
    usize,
    PositionKeys,
    Vec<(u64, u64, i64)>,
    PositionKeys,
    Vec<u64>,
);
type PlacementOutput = (
    String,
    usize,
    PositionKeys,
    PositionKeys,
    PositionKeys,
    Option<PyCoreDocument>,
);

/// Python の文書窓口が所有するコア文書。
#[pyclass(module = "pma_native", name = "_CoreDocument")]
pub(crate) struct PyCoreDocument(Document);

fn value_error(error: impl ToString) -> PyErr {
    PyValueError::new_err(error.to_string())
}

fn shared(images: &[PyRef<'_, PyRgbaImage>]) -> Vec<RgbaImage> {
    images.iter().map(|image| image.share()).collect()
}

fn graph_output<V: ToString>((value, images): (V, Vec<RgbaImage>)) -> (String, Vec<PyRgbaImage>) {
    (
        value.to_string(),
        images.into_iter().map(PyRgbaImage::from).collect(),
    )
}

fn cell_number(number: i64) -> PyResult<CellNumber> {
    CellNumber::new(number).map_err(value_error)
}

fn artwork_slot(bank: bool, first: u64, second: i64) -> PyResult<ArtworkSlot> {
    Ok(if bank {
        ArtworkSlot::Bank(TrackId(first), cell_number(second)?)
    } else {
        let track = u64::try_from(second).map_err(value_error)?;
        ArtworkSlot::Local(FrameId(first), TrackId(track))
    })
}

#[pymethods]
impl PyCoreDocument {
    /// v2 の JSON と復号済み画像から文書を作る。画素バッファは共有する。
    #[staticmethod]
    fn from_v2(
        py: Python<'_>,
        payload_json: &str,
        names: Vec<String>,
        images: Vec<PyRef<'_, PyRgbaImage>>,
    ) -> PyResult<Self> {
        let artwork: Vec<_> = images.iter().map(|image| image.share()).collect();
        py.detach(|| {
            let payload = v2::parse_json(payload_json).map_err(value_error)?;
            let plan =
                decode_v2_plan(&payload, &names).map_err(|error| value_error(error.reason))?;
            v2::from_plan(&payload, &plan, &artwork)
                .map(Self)
                .map_err(value_error)
        })
    }

    /// Python の文書グラフ（document_core.graph_payload の JSON）と画像の列から文書を作る。
    #[staticmethod]
    fn from_graph(
        py: Python<'_>,
        graph_json: &str,
        images: Vec<PyRef<'_, PyRgbaImage>>,
    ) -> PyResult<Self> {
        let artwork = shared(&images);
        py.detach(|| {
            let graph = v2::parse_json(graph_json)?;
            Document::from_graph(&graph, &artwork)
        })
        .map(Self)
        .map_err(value_error)
    }

    /// 中身を文書グラフから作り直す。窓口が持つこの文書への参照は保たれる。失敗時は変えない。
    fn replace_from_graph(
        &mut self,
        py: Python<'_>,
        graph_json: &str,
        images: Vec<PyRef<'_, PyRgbaImage>>,
    ) -> PyResult<()> {
        let artwork = shared(&images);
        self.0 = py
            .detach(|| {
                let graph = v2::parse_json(graph_json)?;
                Document::from_graph(&graph, &artwork)
            })
            .map_err(value_error)?;
        Ok(())
    }

    /// from_graph と同じ形の JSON と、添字で参照する共有画像の列を返す。
    fn to_graph(&self, py: Python<'_>) -> PyResult<(String, Vec<PyRgbaImage>)> {
        let (json, images) = py.detach(|| {
            let (text, images) = self.0.to_graph_text();
            (text, images)
        });
        Ok((json, images.into_iter().map(PyRgbaImage::from).collect()))
    }

    /// 構造の命令を一つ当て、Python の ChangeSet と同じ種類名・フィールドの JSON を返す。
    ///
    /// `operation` は比較テストと同じ ["命令名", 引数...] の JSON。失敗時は文書を変えない。
    fn apply_command(&mut self, py: Python<'_>, operation: &str) -> PyResult<String> {
        let operations = v2::parse_json(&format!("[{operation}]")).map_err(value_error)?;
        let document = &mut self.0;
        py.detach(|| comparison::apply_operations(document, &operations))
            .map_err(value_error)
    }

    /// 空画像番号を挿入し、ChangeSet の JSON を返す。
    fn insert_sequence_blank(
        &mut self,
        py: Python<'_>,
        layer: usize,
        after_number: i64,
        image: PyRef<'_, PyRgbaImage>,
    ) -> PyResult<String> {
        let image = image.share();
        py.detach(|| {
            self.0
                .insert_sequence_blank(layer, after_number, Some(image))
        })
        .map(comparison::command_notices)
        .map_err(value_error)
    }

    /// 位置の画像と内容の印を入力順に差し替え、ChangeSet の JSON を返す。
    fn replace_drawing_image(
        &mut self,
        frame: usize,
        layer: usize,
        image: PyRef<'_, PyRgbaImage>,
        has_content: bool,
    ) -> PyResult<String> {
        self.0
            .replace_drawing_image(frame, layer, image.share(), has_content)
            .map(comparison::command_notices)
            .map_err(value_error)
    }

    /// 一括差し替えを当て、入力順の ChangeSet の JSON を返す。
    fn replace_drawing_images(&mut self, groups: Vec<PyDrawingImageGroup<'_>>) -> PyResult<String> {
        let images: Vec<_> = groups.iter().map(|(_, image)| image.share()).collect();
        let groups: Vec<_> = groups
            .iter()
            .zip(&images)
            .map(|((positions, _), image)| DrawingImageGroup { positions, image })
            .collect();
        self.0
            .replace_drawing_images(&groups)
            .map(comparison::command_notices)
            .map_err(value_error)
    }

    /// 別の文書 source の列 source_layer を列 index へ挿入する（restore_inserted_track）。
    ///
    /// apply_command と同じ形の JSON を返す。失敗時は文書を変えない。
    fn insert_track_from(
        &mut self,
        source: PyRef<'_, PyCoreDocument>,
        source_layer: usize,
        index: usize,
        active: usize,
    ) -> PyResult<String> {
        comparison::insert_track_from(&mut self.0, &source.0, source_layer, index, active)
            .map_err(value_error)
    }

    /// 番号 number の絵（cell はバンクの一枚の JSON、画像は images の添字）を
    /// column・layer に貼る（paste_sheet_cell）。
    ///
    /// apply_command と同じ形の JSON を返す。失敗時は文書を変えない。
    fn paste_sheet_cell(
        &mut self,
        column: usize,
        layer: usize,
        number: i64,
        cell: &str,
        images: Vec<PyRef<'_, PyRgbaImage>>,
        length: usize,
    ) -> PyResult<String> {
        let cell = v2::parse_json(cell).map_err(value_error)?;
        comparison::paste_sheet_cell(
            &mut self.0,
            (column, layer),
            number,
            &cell,
            &shared(&images),
            length,
        )
        .map_err(value_error)
    }

    /// 内容の無い column・layer の位置を、新しい番号 number の絵（画像は image）にする
    /// （make_position_editable の空の位置の場合）。block は (空キーか, 先頭, 長さ)。
    ///
    /// apply_command と同じ形の JSON を返す。失敗時は文書を変えない。
    fn make_empty_position_editable(
        &mut self,
        column: usize,
        layer: usize,
        number: i64,
        block: Option<(bool, usize, usize)>,
        image: PyRef<'_, PyRgbaImage>,
    ) -> PyResult<String> {
        let image = shared(&[image]).pop().expect("一枚渡した");
        comparison::make_empty_position_editable(&mut self.0, (column, layer), number, block, image)
            .map_err(value_error)
    }

    /// 配置待ちの所属を列へ戻し、付け直した位置の ID を返す。
    fn link_layer_tracks(&mut self) -> Vec<(u64, u64)> {
        self.0
            .link_layer_tracks()
            .into_iter()
            .map(|(frame, track)| (frame.0, track.0))
            .collect()
    }

    /// Python の restore と同じ値で、写しの文書（snapshot）を戻す。
    fn restore_from(&mut self, snapshot: PyRef<'_, PyCoreDocument>) {
        self.0.restore_from(&snapshot.0);
    }

    /// 履歴の復元で一つの位置の内容の印を戻す。失敗時は文書を変えない。
    fn restore_position_content(
        &mut self,
        frame: usize,
        layer: usize,
        has_content: bool,
    ) -> PyResult<()> {
        self.0
            .restore_position_content(frame, layer, has_content)
            .map_err(value_error)
    }

    /// 命令が作る空画像の雛形を設定する（Python の blank_model_image() の画像）。
    fn set_blank_image(&mut self, image: PyRef<'_, PyRgbaImage>) {
        self.0.set_blank_image(image.share());
    }

    /// 指定したコマの行を to_graph と同じ JSON で、並びと次の ID とともに返す。
    fn frames_graph(&self, frames: Vec<u64>) -> PyResult<(String, Vec<PyRgbaImage>)> {
        let frames: Vec<_> = frames.into_iter().map(FrameId).collect();
        self.0
            .frames_tracks_graph_text(&frames, self.0.track_order())
            .map(graph_output)
            .map_err(value_error)
    }

    /// frames_graph と同じ形で、各行の位置を指定した列（tracks の順）のものだけ返す。
    fn frames_tracks_graph(
        &self,
        frames: Vec<u64>,
        tracks: Vec<u64>,
    ) -> PyResult<(String, Vec<PyRgbaImage>)> {
        let frames: Vec<_> = frames.into_iter().map(FrameId).collect();
        let tracks: Vec<_> = tracks.into_iter().map(TrackId).collect();
        self.0
            .frames_tracks_graph_text(&frames, &tracks)
            .map(graph_output)
            .map_err(value_error)
    }

    /// 文書の写しを返す。画素バッファは書き込みまで共有する（Undo の記録用）。
    fn copy(&self) -> Self {
        Self(self.0.clone())
    }

    /// 参照元の表示インデックスを (列数, コマ優先順の i64 のバイト列) で返す。
    ///
    /// Python の resolve_timeline と同じ値（pma-core の resolve_sources）。
    fn resolve_sources<'py>(&self, py: Python<'py>) -> PyResult<(usize, Bound<'py, PyBytes>)> {
        let (width, sources) = py
            .detach(|| self.0.resolve_sources())
            .map_err(value_error)?;
        let bytes: Vec<u8> = sources.into_iter().flat_map(i64::to_ne_bytes).collect();
        Ok((width, PyBytes::new(py, &bytes)))
    }

    /// to_graph の並び・次の ID・尺と、位置・列の値の指紋を返す（pma-core の graph_fingerprints）。
    ///
    /// (コマ ID の並び, 列 ID の並び, (次のコマ ID, 次の列 ID), 尺の並び, 位置の指紋の
    /// u64 の bytes, 列の指紋の u64 の bytes)。位置はコマ優先で frame_order × track_order。
    #[allow(clippy::type_complexity)]
    fn graph_fingerprints<'py>(
        &self,
        py: Python<'py>,
    ) -> (
        Vec<u64>,
        Vec<u64>,
        (u64, u64),
        Vec<i64>,
        Bound<'py, PyBytes>,
        Bound<'py, PyBytes>,
    ) {
        let prints = py.detach(|| self.0.graph_fingerprints());
        let bytes = |values: &[u64]| -> Vec<u8> {
            values.iter().copied().flat_map(u64::to_ne_bytes).collect()
        };
        (
            prints.frame_order.iter().map(|id| id.0).collect(),
            prints.track_order.iter().map(|id| id.0).collect(),
            prints.next_ids,
            prints.durations,
            PyBytes::new(py, &bytes(&prints.positions)),
            PyBytes::new(py, &bytes(&prints.tracks)),
        )
    }

    /// 表示位置 column・表示列 track を含む保持の範囲を (空キーか, 頭, 長さ) で返す。
    ///
    /// Python の timeline_block_at と同じ（pma-core の timeline_block_at）。未使用なら None。
    fn timeline_block_at(&self, column: usize, track: usize) -> Option<(bool, usize, usize)> {
        self.0.timeline_block_at(column, track)
    }

    /// コマ ID と列 ID を表示順で返す。
    fn order(&self) -> (Vec<u64>, Vec<u64>) {
        (
            self.0.frame_order().iter().map(|id| id.0).collect(),
            self.0.track_order().iter().map(|id| id.0).collect(),
        )
    }

    /// 現在コマと選択列の表示インデックスを返す。
    fn selection_index(&self) -> (Option<usize>, Option<usize>) {
        let (frame, track) = self.0.selection();
        (self.0.frame_index(frame), self.0.track_index(track))
    }

    /// 現在コマと選択列を表示インデックスで設定する。
    fn set_selection_index(&mut self, frame: usize, track: usize) -> PyResult<()> {
        self.0
            .set_selection_index(frame, track)
            .map_err(value_error)
    }

    /// 一つの位置を to_graph の位置と同じ JSON と画像列で返す。
    fn position_graph(&self, frame: u64, track: u64) -> PyResult<(String, Vec<PyRgbaImage>)> {
        self.0
            .position_graph_text(FrameId(frame), TrackId(track))
            .map(graph_output)
            .map_err(value_error)
    }

    /// 一つの位置を from_graph の位置と同じ JSON で置き換える。
    fn set_position_graph(
        &mut self,
        frame: u64,
        track: u64,
        value: &str,
        images: Vec<PyRef<'_, PyRgbaImage>>,
    ) -> PyResult<()> {
        let value = v2::parse_json(value).map_err(value_error)?;
        self.0
            .set_position_graph(FrameId(frame), TrackId(track), &value, &shared(&images))
            .map_err(value_error)
    }

    /// コマの長さ変更の Undo：コマ数を frame_count に縮め、列 layer の column から後ろの位置を
    /// 記録 references（JSON と画像列）の値に戻す（restore_exposure_positions）。
    ///
    /// (timeline_revision を進める回数, 値が変わり得る位置のコマ ID, 局所の絵を入れ替えた
    /// 位置のコマ ID) を返す。失敗時は文書を変えない。
    fn restore_exposure(
        &mut self,
        layer: usize,
        column: usize,
        frame_count: usize,
        references: &str,
        images: Vec<PyRef<'_, PyRgbaImage>>,
    ) -> PyResult<(usize, Vec<u64>, Vec<u64>)> {
        let value = v2::parse_json(references).map_err(value_error)?;
        let references =
            graph::exposure_references_value(&value, &shared(&images)).map_err(value_error)?;
        let result = self
            .0
            .restore_exposure_positions(layer, column, frame_count, &references)
            .map_err(value_error)?;
        let ids = |frames: Vec<FrameId>| frames.into_iter().map(|frame| frame.0).collect();
        Ok((result.revisions, ids(result.frames), ids(result.replaced)))
    }

    /// 配置待ちの番号を置き、窓口の更新に必要な通知と位置 ID を返す。
    fn place_pending_entries(
        &mut self,
        py: Python<'_>,
        cursor: usize,
    ) -> PyResult<PlacementOutput> {
        let result = py
            .detach(|| self.0.place_pending_entries_with_updates(cursor))
            .map_err(value_error)?;
        let keys = |positions: std::collections::BTreeSet<(FrameId, TrackId)>| {
            positions
                .into_iter()
                .map(|(frame, track)| (frame.0, track.0))
                .collect()
        };
        Ok((
            comparison::command_notices(result.changes),
            result.revisions,
            keys(result.changed),
            keys(result.replaced),
            keys(result.cleared),
            result.before_trim.map(Self),
        ))
    }

    /// 接続済みのコマを再公開し、位置とバンクの窓口の更新情報を返す。
    fn reinstall_legacy_frames(&mut self) -> PyResult<LegacyInstallOutput> {
        let result = self.0.reinstall_legacy_frames().map_err(value_error)?;
        Ok((
            result.revisions,
            result
                .changed
                .into_iter()
                .map(|(frame, track)| (frame.0, track.0))
                .collect(),
            result
                .moved
                .into_iter()
                .map(|(frame, track, number)| (frame.0, track.0, number.get()))
                .collect(),
            result
                .cleared
                .into_iter()
                .map(|(frame, track)| (frame.0, track.0))
                .collect(),
            result.banks.into_iter().map(|track| track.0).collect(),
        ))
    }

    /// 別の Rust 文書の状態に差し替え、画像窓口が持つこの文書への参照を保つ。
    fn replace_from_document(&mut self, source: PyRef<'_, Self>) {
        self.0 = source.0.clone();
    }

    /// 列の設定・バンク・旧連番保管を JSON と画像列で返す。
    fn track_graph(&self, track: u64) -> PyResult<(String, Vec<PyRgbaImage>)> {
        self.0
            .track_graph(TrackId(track))
            .map(graph_output)
            .map_err(value_error)
    }

    /// 列の表示設定を JSON で置き換える。
    fn set_track_settings_graph(&mut self, track: u64, value: &str) -> PyResult<()> {
        let value = v2::parse_json(value).map_err(value_error)?;
        self.0
            .set_track_settings_graph(TrackId(track), &value)
            .map_err(value_error)
    }

    /// バンクの一枚を JSON で置き換える。"null" なら取り除く。
    fn set_cell_graph(
        &mut self,
        track: u64,
        number: i64,
        value: &str,
        images: Vec<PyRef<'_, PyRgbaImage>>,
    ) -> PyResult<()> {
        let value = v2::parse_json(value).map_err(value_error)?;
        self.0
            .set_cell_graph(
                TrackId(track),
                cell_number(number)?,
                &value,
                &shared(&images),
            )
            .map_err(value_error)
    }

    /// 旧連番保管の一件を JSON で置き換える。"null" なら取り除く。
    fn set_hint_graph(&mut self, track: u64, number: i64, value: &str) -> PyResult<()> {
        let value = v2::parse_json(value).map_err(value_error)?;
        self.0
            .set_hint_graph(TrackId(track), cell_number(number)?, &value)
            .map_err(value_error)
    }

    /// コマの尺を設定する。
    fn set_frame_duration(&mut self, frame: u64, duration: i64) -> PyResult<()> {
        self.0
            .set_frame_duration(FrameId(frame), duration)
            .map_err(value_error)
    }

    /// 保存用 JSON と、ZIP に書く順のパス・共有画像を返す。
    fn project_v2(
        &self,
        py: Python<'_>,
        metadata_json: &str,
    ) -> PyResult<(String, Vec<(String, PyRgbaImage)>)> {
        let (json, pngs) = py
            .detach(|| {
                let metadata = v2::parse_json(metadata_json)?;
                let projection = v2::project(&self.0, &metadata)?;
                let json = projection.ordered_json(metadata_json)?;
                Ok::<_, pma_core::Error>((json, projection.pngs))
            })
            .map_err(value_error)?;
        Ok((
            json,
            pngs.into_iter()
                .map(|entry| (entry.path, entry.image.into()))
                .collect(),
        ))
    }
}

/// 文書のセルの画像を指す窓口。RgbaImage と同じ読み書きの口を持ち、書き込みは文書に届く。
///
/// PmaImage の `_image` に入れて使う。指す絵が無くなっていれば ValueError。
#[pyclass(module = "pma_native", name = "_CellImage")]
pub(crate) struct PyCellImage {
    document: Py<PyCoreDocument>,
    slot: ArtworkSlot,
    field: ImageField,
}

impl PyCellImage {
    fn with<T>(&self, py: Python<'_>, read: impl FnOnce(&RgbaImage) -> T) -> PyResult<T> {
        let document = self
            .document
            .bind(py)
            .try_borrow()
            .map_err(|_| value_error("文書を書き換え中です"))?;
        document
            .0
            .artwork_image(self.slot, self.field)
            .map(read)
            .ok_or_else(|| value_error("文書にない絵の画像"))
    }
}

#[pymethods]
impl PyCellImage {
    /// 文書・絵・画像の種類を指定して窓口を作る。bank なら (列 ID, 番号)、局所なら (コマ ID, 列 ID)。
    #[new]
    fn new(
        document: Py<PyCoreDocument>,
        bank: bool,
        first: u64,
        second: i64,
        initial: bool,
    ) -> PyResult<Self> {
        Ok(Self {
            document,
            slot: artwork_slot(bank, first, second)?,
            field: if initial {
                ImageField::Initial
            } else {
                ImageField::Image
            },
        })
    }

    /// 指す絵の画像があるか。文書を書き換え中なら ValueError。
    fn exists(&self, py: Python<'_>) -> PyResult<bool> {
        let document = self
            .document
            .bind(py)
            .try_borrow()
            .map_err(|_| value_error("文書を書き換え中です"))?;
        Ok(document.0.artwork_image(self.slot, self.field).is_some())
    }

    /// 画像の幅を返す。
    #[getter]
    fn width(&self, py: Python<'_>) -> PyResult<usize> {
        self.with(py, RgbaImage::width)
    }

    /// 画像の高さを返す。
    #[getter]
    fn height(&self, py: Python<'_>) -> PyResult<usize> {
        self.with(py, RgbaImage::height)
    }

    /// 画像の現在の世代を返す。
    #[getter]
    fn generation(&self, py: Python<'_>) -> PyResult<u64> {
        self.with(py, RgbaImage::generation)
    }

    /// 画像の札（Qt 属性の表の番号）を返す。
    #[getter]
    fn tag(&self, py: Python<'_>) -> PyResult<u32> {
        self.with(py, RgbaImage::tag)
    }

    /// 画素を共有する独立した RgbaImage を返す。
    fn copy(&self, py: Python<'_>) -> PyResult<PyRgbaImage> {
        self.with(py, |image| PyRgbaImage::from(image.clone()))
    }

    /// 全画素を不変の bytes で返す。
    fn rgba_bytes<'py>(&self, py: Python<'py>) -> PyResult<Bound<'py, PyBytes>> {
        // 画素バッファの参照だけを取り、文書の借用は bytes を作る前に手放す。
        let image = self.with(py, RgbaImage::clone)?;
        Ok(PyBytes::new(py, image.rgba()))
    }

    /// 矩形内の画素を行優先の bytes で返す。
    fn read_rect<'py>(
        &self,
        py: Python<'py>,
        x: usize,
        y: usize,
        width: usize,
        height: usize,
    ) -> PyResult<Bound<'py, PyBytes>> {
        let rect = Rect {
            x,
            y,
            width,
            height,
        };
        let pixels = self
            .with(py, |image| image.copy_rgba_rect(rect))?
            .map_err(value_error)?;
        Ok(PyBytes::new(py, &pixels))
    }

    /// 矩形内の画素を行優先の bytes で上書きする。文書の絵に直接書く。
    fn write_rect(
        &self,
        py: Python<'_>,
        x: usize,
        y: usize,
        width: usize,
        height: usize,
        pixels: &Bound<'_, PyBytes>,
    ) -> PyResult<()> {
        let rect = Rect {
            x,
            y,
            width,
            height,
        };
        let mut document = self
            .document
            .bind(py)
            .try_borrow_mut()
            .map_err(|_| value_error("文書を使用中です"))?;
        document
            .0
            .artwork_image_mut(self.slot, self.field)
            .ok_or_else(|| value_error("文書にない絵の画像"))?
            .write_rgba(rect, pixels.as_bytes())
            .map_err(value_error)
    }
}
