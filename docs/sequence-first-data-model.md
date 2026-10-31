# データの本体を連番にする（設計）

[Issue #31](https://github.com/SehataKuro/PaintMaskAnimator/issues/31) の設計です。
データの本体を **レイヤーごとの連番（絵番号 → セル）** にし、タイムラインは
「どのコマにどの絵番号を出すか」の **表（シート）** として扱うようにします。

実装前に方針を固めるための文書です。段取りと、先に決めておくことを載せます。

---

## 1. なぜやるのか（速くするためではない）

先に書いておくと、**この変更で速くなる部分はほとんどありません。**

本文が目的に挙げていた「同じ絵番号が何コマにも出てくるとき、同じ絵を何度も
処理しない」は、すでに実現しています。

- [`frame_scope.resolve_cells`](../paintmaskanimator/frame_scope.py) が保持セルを
  実体キーへ畳み、同じセルを1度しか返さない
- 同ファイルの `_group_shared_images` が `id(layer.image)` で画像実体を共有する
  セルをまとめ、1回処理して結果を全員へ配る
- 保存も `project_io.PngCache` が同じ画像を1回しか書かない

塗り・使用色の集計・保存・一括適用・串刺し塗りは、いま既に絵ごとに1回です。
**やる理由は保守です。**

### 1.1 同じことを2つの仕組みで支えている

いまの「同じ番号は同じ絵」は、2つの仕組みの釣り合いで成り立っています。

| 仕組み | すること | 場所 |
|---|---|---|
| `coalesce_numbered_images` | 同じ番号のセルに同じ `QImage` を持たせる（共有を作る） | `canvas_timeline_ops.py` |
| `ensure_editable_key` | 保持セルへ描くと、そのコマを独立したキーへ分ける（共有を壊す） | `canvas_key_frame.py` |

どちらも必要なのですが、「いま共有されているか」はデータを見ても分からず、
`id(layer.image)` が同じかどうかで判定するしかありません。描画・取り込み・
Undo・トゥイーンのどこかで片方だけ差し替わると、同じ番号が別の絵を指す状態が
静かにできます。`sync_numbered_image_from_cell` はこれを繕うための関数です。

### 1.2 表示の設定がセルごとに重複している

`name` / `visible` / `opacity` / `is_paper` / `is_draft` / `alpha_locked` /
`color_filter_enabled` / `color_filter_rgb` は、本来レイヤーの設定なのに
`Layer`（＝1コマ分のセル）が各自で持っています。そのため
`main_window_layer_ops._copy_display_properties` が、セルを作るたび・
コマを動かすたびに9個の値を写して回っています。写し忘れた経路があれば、
そのコマだけ不透明度が違う、といった状態になります。

### 1.3 シート操作が「バラして組み直す」になっている

タイムリマップの反映（`_apply_time_remap_states_to_layer`）は、対象範囲を
いったん全部空セルに戻してから、連続する同じ番号を露出へ圧縮し直しています。
本体が連番なら、シートの番号列を差し替えるだけで済みます。XDTS の読み込みも
同じです。

### 1.4 Rust へ渡しやすくなる

[設計方針](design-principles.md)の「将来は Rust へ置き換えていく」では、
Rust の部品を「画像（配列）を受け取って結果を返す」形に保つと決めています。
連番は画面から切り離した素のデータなので、レイヤー1本ぶんをまとめて渡せます。

タイムラインのメタデータは**すでに Rust にあります**（§4）。この変更は、
その続きにあたります。

---

## 2. 目指す形

### 2.1 データ

```
Document
  tracks: [Track]            # レイヤー。表示の設定はここだけが持つ
  sheet:  [[SheetEntry]]     # コマ × レイヤー。どの絵を出すか
  fps, playback_range, ...

Track                        # = いまの「レイヤー」
  name, visible, opacity, alpha_locked,
  is_paper, is_draft,
  color_filter_enabled, color_filter_rgb
  cells: {絵番号: Cell}      # ← データの本体

Cell                         # = 1枚の絵
  image: QImage
  initial_image: QImage | None
  cell_name: str | None

SheetEntry                   # コマ1つ分の指定
  number: int | BLANK | EMPTY
  tween: dict | None         # キーのセルに付く。中割りは tween_member
```

要点は3つです。

- **絵は `Track.cells` にしか無い。** シートは番号を指すだけ。共有を作る／壊す
  仕組みは要らなくなり、`coalesce_numbered_images` と
  `sync_numbered_image_from_cell` は消えます。
- **表示の設定は `Track` だけが持つ。** `_copy_display_properties` は消えます。
- **露出は持たない。** 同じ番号が続いていることが露出です。`exposure` フィールドと
  `_shift_timeline_layer_left` / `_shift_timeline_layer_right` の
  「明示コマを動かして隙間を作る／詰める」処理は、シートの配列操作になります。

`BLANK` は空セル（○＝いまの `is_blank_key`）、`EMPTY` は未使用セルです。
いまは `has_content` / `is_blank_key` / `sequence_only` の3つのフラグの
組み合わせで表していて、両方 True のような不正な組み合わせが作れてしまいます
（`ensure_editable_key` にその修復コードがあります）。1つの値にします。

### 2.2 連番保管セル（`_sequence_archive`）は無くなる

いまは「シートに並べていない絵」を `canvas._sequence_archive` が別に持って
います。本体が連番になれば、**シートから指されていない番号**がそれです。
特別扱いは要りません。保存形式の `sequence_archive` セクションも消えます。

### 2.3 処理を回す順番

串刺し塗りの追跡のように、**動きの順**が大事な処理があります。設計方針で
「リテイクで足した中割りは末尾の番号」と決めているため、A3 と A4 の間に A11 が
入ることがあり、番号順に回すと動きの順とずれます。

そこで、連番を回す口を2つに分けます。

| 口 | 順番 | 使うところ |
|---|---|---|
| `Track.cells_by_number()` | 絵番号の昇順 | 保存、書き出し、使用色の集計 |
| `Document.cells_in_sheet_order(track)` | シートで最初に出てくる順 | 串刺し塗り、自動塗りの追跡、オニオン |

どちらを使うかは呼び出し側が選びます。既定を決めて間違えると、ずれに気づき
にくいためです。

### 2.4 用紙・下書きレイヤー

用紙レイヤーと下書きレイヤーは番号を持たない「1枚もの」です。`Track` に
`single_cell: Cell | None` を持たせ、`cells` は使いません。シートからは
参照しません（全コマで同じ絵が出ます）。

---

## 3. 変わらないもの

- **タイムラインに残すもの**：再生、オニオンスキン（前後のコマ）、空セル（○）、
  トゥイーンの指定。いずれもコマの並びが前提なので、シート側のデータです。
- **セル番号の決まり**：1から始まる整数だけ。新しい絵は末尾の番号。
  撮影済みのカットでは正規化しない（[設計方針](design-principles.md)）。
- **画素の規約**：premultiplied alpha、`#FFFFFF` は消しゴム（alpha=0）。
  `mask_format` は変えません。

---

## 4. タイムラインのメタデータは既に Rust にある

段取りの前に、実装を進めるうえで外せない前提です。

`has_content` / `is_blank_key` / `exposure` / `sequence_number` /
`sequence_only` / `cell_name` の6つは、**すでに Rust が持っています**
（[`native/src/timeline.rs`](../native/src/timeline.rs) の `TimelineCell`）。
`Layer` のこれらの属性は、[`timeline_metadata.py`](../paintmaskanimator/timeline_metadata.py)
が付けたプロパティで、Python 側に写しを持ちません。露出の解決
（どのコマがどのキーを表示するか）も Rust の `resolve()` が行います。
経緯は [display-performance.md](display-performance.md) にあります。

これは §2 の形にとって**追い風**です。

- `TimelineCell` の並び（`rows: &[Vec<TimelineCell>]`）は、既に §2.1 の
  `sheet` そのものです。作るのではなく、**絞る**作業になります
  （`SheetEntry` は `number | BLANK | EMPTY` と `tween` だけ）。
- 露出の解決が Rust にあるので、`exposure` を「同じ番号の連続」へ変える影響は
  `resolve()` の中に収まります。
- 設計方針の「将来は Rust へ置き換えていく」で、次段階は「構造編集と変更通知の
  境界を Rust へ移す」と書かれています。シートを Rust が持つのは、その線上です。

**このため**

- Phase 3（シートを参照の表にする）は、Python だけで閉じません。
  `native/src/timeline.rs` と `timeline_metadata.py` を同時に変えます。
- `Cell`（絵）は Rust へ渡しません。画像は Qt 側に残す、という既存の切り分け
  （「image payloads never cross this boundary」）をそのまま守ります。
  Rust が持つのはシートと番号だけです。
- Rust 側の変更には Rust のテスト（`native/src/timeline.rs` の `mod tests`）も
  足します。

---

## 5. 段取り

一度に入れ替えません。**既存の操作を変えずに中身だけ移す**段階を先に置き、
保存形式の変更は最後にします。

### Phase 0：いまの振る舞いを固定する（済）

移行の前に、いまの結果をテストで固定しました。
[`tests/test_sequence_model_characterization.py`](../tests/test_sequence_model_characterization.py)
に、移行で静かに壊れやすい5つを置いてあります。

- 同じ番号のセルが同じ絵を指すこと（`coalesce_numbered_images`）
- 保持セルへ描いたときの編集先（`ensure_editable_key`）
- 一括処理が同じ絵を1回しか処理しないこと（`frame_scope`）
- タイムリマップの往復（シート → AE テキスト → シート）で並びが変わらないこと
- 表示の設定がセルごとに重複していること（Phase 2 で書き換える）

このファイルは「いまこう動く」を書き留めたもので、理想の仕様ではありません。
意図して動きを変える段階では、テストごと書き換えます（何が変わるのかを
差分として見えるようにするのが狙いです）。

#### 基準値

[`scripts/benchmark_sequence_model.py`](../scripts/benchmark_sequence_model.py)
で測ります。**速くするための変更ではない**ので、これは遅くなっていないことを
確かめるためのものです。600コマ・4レイヤー・1レイヤーあたり60枚
（露出10コマ）・1280×720 での測定値：

| 測るもの | Phase 0 | Phase 1 | Phase 2 |
|---|---|---|---|
| `coalesce_numbered_images` | 0.343 ms | 0.377 ms | 0.315 ms |
| `resolve_cells`（カット全体） | 7.628 ms | 7.739 ms | 7.922 ms |
| `resolve_key_frame`（索引を捨ててから） | 1.415 ms | 1.254 ms | 1.384 ms |
| シーク（13コマおき） | 0.144 ms | 0.144 ms | 0.147 ms |
| カット全体の対象セル数 | 240 | 240 | 240 |

いずれも誤差の範囲で、遅くなっていません（2026-11-01 測定）。

対象セル数が 240（＝60枚 × 4レイヤー）で、コマ×レイヤーの 2400 ではないことが、
§1 の「重複処理はすでに畳まれている」の裏づけです。

### Phase 1：絵を `Cell` にする（表からは見えない・済）

`Cell`（1枚の絵）を `Layer` と画像のあいだに入れました。共有は
「同じ `Cell` を指しているか」というデータになり、`id(layer.image)` を見る
必要がなくなります。

- `Layer.image` は `Cell` 越しの読み書きになり、**差し替えても共有が切れません**。
  これまでは `layer.image = 新しい画像` のたびに共有が静かに外れていました。
- 結ぶのは `share_cell_with`、切るのは `detach_cell`。
  `coalesce_numbered_images` と `sync_numbered_image_from_cell` は前者を使い、
  シート操作（`_clear_timeline_layer_cell`）は後者を使います。
- `Layer.clone()` は自分の `Cell` を持ちます。Undo のスナップショットが
  あとの編集で汚れないようにするためです。

この段階では**振る舞いを変えません**。`coalesce_numbered_images` と
`sync_numbered_image_from_cell` はまだ残します（共有を結ぶ役として要るため）。
消えるのは、シートが参照の表になる Phase 3 です。

`Cell` が持つのは `image` だけにしました。色塗り前の初期状態
（`initial_image`）はセル単位で復元する Undo があり、いま共有へ寄せると
Undo の復元結果が変わるためです。Phase 4 でまとめます。

保存形式は変えていません。

**テスト**：[`tests/test_sequence_cells.py`](../tests/test_sequence_cells.py)

### Phase 2：表示の設定を `Track` へ移す（済）

8個の表示設定（`name` / `visible` / `opacity` / `is_paper` / `is_draft` /
`alpha_locked` / `color_filter_enabled` / `color_filter_rgb`）を `Track` が
レイヤーごとに1つ持つようにしました。同じレイヤーのセルは1つの `Track` を
共有します。

- **どのコマから変えてもレイヤー全体に効きます。** 1コマだけ不透明度が違う、
  という状態は作れなくなりました。
- `Track` は作るときに結びます（`_ensure_frame_count`・`add_layer`）。
  読み込みや Undo の復元のあとは `link_layer_tracks()` で結び直します。
- `Layer.clone()` は自分の `Track` を持ちます（Undo のスナップショットが
  あとの変更で汚れないようにするため）。
- `_copy_display_properties` は `share_track_with` の1行になりました。
  呼び出し側（`main_window_time_remap.py`）はそのままです。

**動きが変わったところが1つあります。** レイヤー名もレイヤーに1つなので、
「1コマだけ名前を変える」ことはできなくなりました。これまではそれができて
しまい、同じレイヤーのコマごとに名前が違う状態が作れていました。
`tests/test_initial_state_import.py` の1件が、その状態に依存していたので
直してあります。

**テスト**：`tests/test_sequence_model_characterization.py` の5番目を書き換え
（Phase 0 で「セルごとに持つ」と記録していたもの）。

### Phase 3：シートを参照の表にする

`SheetEntry` を導入し、`exposure` を消します。`_shift_timeline_layer_left` /
`_shift_timeline_layer_right` と、タイムリマップの `_apply_time_remap_states_to_layer`
を、配列操作へ置き換えます。`_sequence_archive` を廃止します。

ここで**「この位置だけ別の絵にする」操作をはっきり用意します**（§6.2）。

### Phase 4：Undo の単位を移す

`LayerUndo` / `LayerRegionUndo` / `LayerTilesUndo` の `(frame, layer)` を
`(track, number)` にします。`DocUndo`（全体スナップショット）はシートと
連番の両方を持ちます。

副産物として、**Undo のメモリが減ります。** いまは10コマに出ている絵を
直すと、`frame_scope` が畳むので実際には1枚ぶんですが、`ScopedBatchUndo` は
セルの一覧を持っています。連番になれば番号1つです。

### Phase 5：保存形式を上げる

`format_version` を 2 にし、連番＋シートの形で書き出します。読み込みは 1 と 2 の
両方を受けます。あわせて [`FORMAT.md`](../FORMAT.md) を 2 の仕様へ書き換えます。

ほかに `.pman` を読む実装は無いので（§6.1）、ここで待つものはありません。

---

## 6. 決めたこと

### 6.1 保存形式：ほかに読む実装はない（決着済み）

[`FORMAT.md`](../FORMAT.md) は「PC版と iPad版フォーク（Swift + Rust）の
**唯一の正**」と書かれていますが、**iPad 版はまだ作っていません。**
`FORMAT.md` の第6章も「フォーク実装時の受け入れ基準」として、これから作る
実装への条件という書き方になっています。

したがって `format_version` を上げても、読めなくなる実装はありません。
**Phase 5 は Phase 4 が終わり次第そのまま進められます。** 待つ理由はありません。

**このため**

- Phase 5 では `FORMAT.md` を `format_version` 2 の仕様へ書き換えます。
  1 の仕様は「読み込み互換のため」として残します（`mask_format` 1 と同じ扱い）。
- 相互運用テスト（`FORMAT.md` 第6章）は、将来フォークを作るときの条件として
  そのまま残します。golden `.pman` は 2 で用意します。
- 古い `.pman` を読めることは引き続き要件です（§6.3、§7）。
  読めなくなって困るのは**利用者の既存ファイル**であって、他実装ではありません。

### 6.2 同じ番号を描き直したときの意味

本体が連番になると、**A3 を描き直せば A3 が出てくる全部のコマが変わります。**
アニメの作業としてはこれが正しい動きです。

一方いまは `ensure_editable_key` が「保持セルに描くと独立したキーに分ける」
ので、動きが変わります。

**決定：B案。** 既定は番号を直し、分けるのは明示操作にします。
セルの右クリックメニューに「この位置を新しい番号にする」（＝末尾の番号で複製）を
置きます。移行直後は、保持セルへ最初に描いたときだけ一度説明を出します。

A案（描く前に毎回尋ねる）は採りません。「同じ番号は同じ絵」という当たり前の
ことを毎回確認することになり、連番を本体にした意味が薄れるためです。

`ensure_editable_key` の「保持セルに描くと独立したキーに分ける」処理は、この
明示操作へ移します。

### 6.3 番号の無いセルの移行

古い `.pman` や、番号を付けずに描いたセルには絵番号がありません。

**決定：シートで最初に出てくる順に、既存の最大番号の次から振る。**
設計方針の「新しく足した絵には末尾の番号」と同じ考え方で、既存の番号を
動かさずに済みます。

用紙・下書きレイヤーは番号を振らず、`single_cell` にします（§2.4）。

### 6.4 Undo の粒度

**決定：`(track, number)` 単位。履歴パネルの表示は「レイヤーA の 3番」にする。**

「どのコマを直したか」ではなく「どの絵を直したか」で戻ることになります。
10コマに出ている A3 を直して Undo すると、10コマぶんが同時に戻ります。
これは §5.2 の B案と同じ考え方です。

履歴パネル（`history_panel.py`）の表示は「コマ5のレイヤーA」から
「レイヤーA の 3番」へ変えます。

---

## 7. 受け入れ条件

- [ ] Phase 0 の特性テストが、移行の各段階で通り続ける
- [ ] `coalesce_numbered_images` / `sync_numbered_image_from_cell` /
      `_copy_display_properties` / `_sequence_archive` が無くなる
- [ ] 同じ番号のセルは、どの経路で編集しても同じ絵のままになる
      （`id()` を見なくてもデータから分かる）
- [ ] タイムリマップと XDTS の反映が、シートの番号列の差し替えだけで済む
- [ ] 古い `.pman`（`format_version` 1、番号なしのセルを含む）が読め、
      読んだあと番号が振られている
- [ ] Phase 0 で測った操作が、同じか速くなっている（遅くなっていない）
- [ ] 「この位置だけ別の絵にする」操作がある

---

## 8. やらないこと

- **速度を目的にした作り込み。** §1 のとおり、重複処理はすでに畳まれています。
  この変更の中で「ついでに速くする」ことはしません。計測して遅くなっていない
  ことだけを確かめます。
- **`mask_format` の変更。** 画素の規約は触りません。
- **原画番号の対応。** 設計方針のとおり、セル番号は動画番号だけです。
