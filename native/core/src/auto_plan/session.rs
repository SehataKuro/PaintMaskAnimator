//! 自動塗りが使う通常バケツの領域（Python の `bucket_fill.BucketSession.compact` と
//! `CompactFill`）。
use std::cell::OnceCell;
use std::collections::{HashMap, VecDeque};
use std::rc::Rc;

use crate::fill::region::{Enclosed, FillOptions};
use crate::fill::session::{BucketState, Category, PrepareOptions};
use crate::fill::target::TargetColor;
use crate::reference_check::Bounds;
use crate::regions::dilate;
use crate::tools::bucket::mask_bounds;
use crate::Error;

use super::cell::{any, count, crop, Key};

/// 自動塗りで使う通常バケツの設定（`bucket_fill.BucketOptions` のうち効くもの）。
#[derive(Clone, Debug, PartialEq)]
pub struct AutoBucketOptions {
    /// 角で接する塗り残しの幅の上限（0 なら拾わない）。
    pub island_max_width: usize,
    /// 線の隙間を仮想的に閉じる。
    pub close_gap: bool,
    pub gap_width: usize,
    /// 端の手前で途切れた線を延ばす。
    pub extend_lines: bool,
    pub extend_length: usize,
    /// 端まで開いた領域は塗らない。
    pub require_closed: bool,
}

impl Default for AutoBucketOptions {
    fn default() -> Self {
        Self {
            island_max_width: 0,
            close_gap: false,
            gap_width: 4,
            extend_lines: false,
            extend_length: 150,
            require_closed: false,
        }
    }
}

impl AutoBucketOptions {
    fn prepare(&self, island_max_width: usize) -> PrepareOptions {
        PrepareOptions {
            target_color: TargetColor::All(None),
            extend_lines: self.extend_lines,
            extend_length: self.extend_length.max(1),
            close_gap: self.close_gap,
            gap_width: self.gap_width.max(1),
            island_replacements: Vec::new(),
            fill: FillOptions {
                adjacent: true,
                require_closed: self.require_closed,
                island_max_width,
                include_masks: false,
                include_background_mask: false,
                mask_colors: Vec::new(),
                enclosed: Enclosed::None,
            },
        }
    }
}

/// 外接矩形の中だけで持つバケツの結果（`CompactFill`）。
pub struct Fill {
    pub reason: &'static str,
    /// 開始点の色（背景は None）。
    pub target: Category,
    pub bounds: Option<Bounds>,
    pub local: Option<Vec<u8>>,
    runs: OnceCell<Vec<(i64, i64, i64)>>,
    size: OnceCell<usize>,
}

impl Fill {
    pub(crate) fn new(
        reason: &'static str,
        target: Category,
        bounds: Option<Bounds>,
        local: Option<Vec<u8>>,
    ) -> Self {
        Self {
            reason,
            target,
            bounds,
            local,
            runs: OnceCell::new(),
            size: OnceCell::new(),
        }
    }

    pub fn ok(&self) -> bool {
        self.reason == "ok"
    }

    /// 結果の矩形（呼ぶのは ok のときだけ）。
    pub(crate) fn rect(&self) -> Bounds {
        self.bounds.unwrap_or((0, 0, 0, 0))
    }

    pub(crate) fn mask(&self) -> &[u8] {
        self.local.as_deref().unwrap_or(&[])
    }

    /// 画素数。
    pub(crate) fn size(&self) -> usize {
        *self.size.get_or_init(|| count(self.mask()))
    }

    /// 行ごとのラン `(行, 開始, 終端)`（`native.sample_region` の `_native_runs`）。
    pub(crate) fn runs(&self) -> &[(i64, i64, i64)] {
        self.runs.get_or_init(|| {
            let (x0, y0, x1, _) = self.rect();
            let width = x1 - x0;
            let mut runs = Vec::new();
            if width == 0 {
                return runs;
            }
            for (y, row) in self.mask().chunks_exact(width).enumerate() {
                let mut x = 0;
                while x < width {
                    if row[x] == 0 {
                        x += 1;
                        continue;
                    }
                    let start = x;
                    while x < width && row[x] != 0 {
                        x += 1;
                    }
                    runs.push(((y + y0) as i64, (start + x0) as i64, (x + x0) as i64));
                }
            }
            runs
        })
    }
}

#[derive(Clone, Copy, PartialEq, Eq, Hash)]
enum ResultKey {
    Label(Category, u32),
    Point(Category, usize, usize),
}

/// 共有する画素（`BucketState` が読む）。
struct Pixels(Rc<Vec<u8>>);

impl AsRef<[u8]> for Pixels {
    fn as_ref(&self) -> &[u8] {
        &self.0
    }
}

/// 変更しない絵で何度もクリックするときの共通前処理（`BucketSession(indexed=True)`）。
pub struct Session {
    state: BucketState<Pixels>,
    width: usize,
    height: usize,
    island_max_width: usize,
    require_closed: bool,
    results: HashMap<ResultKey, Rc<Fill>>,
}

impl Session {
    pub fn new(
        pixels: Rc<Vec<u8>>,
        width: usize,
        height: usize,
        options: &AutoBucketOptions,
        island_max_width: usize,
    ) -> Result<Self, Error> {
        let state = BucketState::new(
            Pixels(pixels),
            width,
            height,
            options.prepare(island_max_width),
            true,
        )
        .map_err(Error::Invalid)?;
        Ok(Self {
            state,
            width,
            height,
            island_max_width,
            require_closed: options.require_closed,
            results: HashMap::new(),
        })
    }

    /// `(x, y)` を種にした領域（`compact`）。
    pub fn compact(&mut self, x: usize, y: usize) -> Result<Rc<Fill>, Error> {
        if x >= self.width || y >= self.height {
            return Ok(Rc::new(Fill::new("out_of_bounds", None, None, None)));
        }
        let category = self.state.category(x, y).map_err(Error::Invalid)?;
        let at = y * self.width + x;
        let passable = {
            let prepared = self.state.prepare(category).map_err(Error::Invalid)?;
            prepared.passable.as_ref().is_some_and(|p| p[at] != 0)
        };
        let key = if passable {
            let index = self.state.index(category).map_err(Error::Invalid)?;
            ResultKey::Label(category, label_at(&index.labels, at))
        } else {
            ResultKey::Point(category, x, y)
        };
        if let Some(result) = self.results.get(&key) {
            return Ok(result.clone());
        }
        let result = match self.compact_indexed(x, y, category, passable)? {
            Some(result) => result,
            None => self.compute(x, y)?,
        };
        let result = Rc::new(result);
        self.results.insert(key, result.clone());
        Ok(result)
    }

    fn compute(&mut self, x: usize, y: usize) -> Result<Fill, Error> {
        let outcome = self.state.compute(x, y, None).map_err(Error::Invalid)?;
        let Some((bounds, region)) = outcome.bounds.zip(outcome.region) else {
            return Ok(Fill::new(outcome.reason, outcome.category, None, None));
        };
        if outcome.reason == "ok" {
            return Ok(Fill::new(
                outcome.reason,
                outcome.category,
                Some(bounds),
                Some(region),
            ));
        }
        // ok でない結果は、領域の外接矩形へ切り詰める（`CompactFill.from_result`）。
        let (bw, bh) = (bounds.2 - bounds.0, bounds.3 - bounds.1);
        Ok(match mask_bounds(&region, bw, bh) {
            Some(tight) => {
                let local = crop(&region, bw, tight);
                Fill::new(
                    outcome.reason,
                    outcome.category,
                    Some((
                        bounds.0 + tight.0,
                        bounds.1 + tight.1,
                        bounds.0 + tight.2,
                        bounds.1 + tight.3,
                    )),
                    Some(local),
                )
            }
            None => Fill::new(outcome.reason, outcome.category, None, None),
        })
    }

    fn compact_indexed(
        &mut self,
        x: usize,
        y: usize,
        category: Category,
        passable: bool,
    ) -> Result<Option<Fill>, Error> {
        if self.island_max_width != 0 || !passable {
            return Ok(None);
        }
        let (width, height) = (self.width, self.height);
        let require_closed = self.require_closed;
        let gaps = self
            .state
            .prepare(category)
            .map_err(Error::Invalid)?
            .gap
            .clone();
        let index = self.state.index(category).map_err(Error::Invalid)?;
        let label = label_at(&index.labels, y * width + x);
        let bounds = box_at(&index.boxes, label as usize);
        let (x0, y0, x1, y1) = bounds;
        let labels = |b: Bounds| -> Vec<u8> {
            let mut out = Vec::with_capacity((b.2 - b.0) * (b.3 - b.1));
            for yy in b.1..b.3 {
                for xx in b.0..b.2 {
                    out.push(u8::from(label_at(&index.labels, yy * width + xx) == label));
                }
            }
            out
        };
        let local = labels(bounds);
        if require_closed && (x0 == 0 || y0 == 0 || x1 == width || y1 == height) {
            return Ok(Some(Fill::new(
                "open_region",
                category,
                Some(bounds),
                Some(local),
            )));
        }
        if let Some(gaps) = gaps {
            let around = (
                x0.saturating_sub(1),
                y0.saturating_sub(1),
                width.min(x1 + 1),
                height.min(y1 + 1),
            );
            let nearby = crop(&gaps, width, around);
            if any(&nearby) {
                let area = labels(around);
                let (aw, ah) = (around.2 - around.0, around.3 - around.1);
                let grown = dilate(&area, aw, ah, 1);
                if grown.iter().zip(&nearby).any(|(g, n)| g & n != 0) {
                    return Ok(None); // 仮想境界の回収が要る面は共通バケツへ戻す。
                }
            }
        }
        Ok(Some(Fill::new("ok", category, Some(bounds), Some(local))))
    }
}

fn label_at(labels: &[u8], pixel: usize) -> u32 {
    u32::from_ne_bytes(labels[pixel * 4..pixel * 4 + 4].try_into().expect("label"))
}

fn box_at(boxes: &[u8], label: usize) -> Bounds {
    let n = std::mem::size_of::<usize>();
    let at = |i: usize| {
        usize::from_ne_bytes(
            boxes[(label * 4 + i) * n..(label * 4 + i + 1) * n]
                .try_into()
                .expect("bounds"),
        )
    };
    (at(0), at(1), at(2), at(3))
}

/// 同じ画素・同じ設定のセッションを使い回す（`ReferenceRegions`）。最大4つ。
pub(crate) struct ReferenceSessions {
    sessions: VecDeque<(Key, Session)>,
}

impl ReferenceSessions {
    pub(crate) fn new() -> Self {
        Self {
            sessions: VecDeque::new(),
        }
    }

    #[allow(clippy::too_many_arguments)]
    pub(crate) fn compact(
        &mut self,
        key: Key,
        pixels: &Rc<Vec<u8>>,
        width: usize,
        height: usize,
        options: &AutoBucketOptions,
        x: usize,
        y: usize,
    ) -> Result<Rc<Fill>, Error> {
        let position = self.sessions.iter().position(|(k, _)| *k == key);
        let mut entry = match position {
            Some(position) => self.sessions.remove(position).expect("session"),
            None => (
                key,
                Session::new(pixels.clone(), width, height, options, 0)?,
            ),
        };
        let result = entry.1.compact(x, y);
        self.sessions.push_back(entry);
        while self.sessions.len() > 4 {
            self.sessions.pop_front();
        }
        result
    }
}
