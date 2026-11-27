//! ブラシ・消しゴムの線を、Qt の画面（`QPainter`、アンチエイリアスなし）と同じ画素で描く。
//!
//! - `stroke`：押してから離すまでの 1 本のストローク（手ぶれ補正・筆圧・選択範囲・
//!   マスク色・透明部分の保護）。途中の見た目と Undo の差分を返す。
//! - `segment`：1 区間の線と筆先の形（画素単位のカーソル）。
//! - `pressure`・`stabilizer`：筆圧カーブと手ぶれ補正の計算。
//! - `shapes`：直線ツール・図形ツール・投げ縄塗りの形。
//! - `raster`・`stroker`：Qt のラスターエンジン（`QOutlineMapper`・`QRasterizer`・
//!   `QStroker`・`QCosmeticStroker`）と同じ演算順の塗りと線の輪郭。

mod arc;
pub mod mask;
pub mod pressure;
mod qt;
pub mod raster;
pub mod segment;
pub mod shapes;
pub mod stabilizer;
pub mod stroke;
pub mod stroker;

pub use mask::Mask;
pub use qt::Point;
pub use raster::{FillRule, Path, PathElement};
pub use stroke::{
    BrushSettings, BrushStroke, MaskColorFilter, PointerSample, Selection, StrokeResult, UndoPatch,
    ERASER_RGB,
};
