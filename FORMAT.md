# PaintMaskAnimator プロジェクトフォーマット仕様 (`.pman`)

このドキュメントは `.pman` プロジェクトファイルの**唯一の正**です。PC版
(PySide6) と将来の iPad版フォーク (Swift + Rust) は、この仕様に従います。
実装を変えるときは、この仕様も更新してください。

参照実装は [`project_io.py`](paintmaskanimator/project_io.py) の `load_project` /
`write_project_snapshot` と [`project_v2.py`](paintmaskanimator/project_v2.py) の
`build_payload` / `decode_payload`。UI状態の生成は `main_window.build_project_metadata`、
復元は `main_window_project_io.prepare_ui_state` が担当します。

## 1. コンテナと版の振り分け

- `.pman` は ZIP アーカイブ。必須の `project.json` は UTF-8 JSON で、保存時は
  `ZIP_DEFLATED`。PNG は `ZIP_STORED` で保存します。読込はどちらも受理します。
- `format` は `"PaintMaskAnimatorProject"` のみ。旧名 `"OekakiAnimationProject"` は受理しません。
- `format_version` は bool を除く整数。欠落時は 1、読込対応は **1 と 2**、保存は **2 のみ**。
  0・負数・型違いは拒否します。3 以上は PNG を読む前に更新が必要と案内し、
  `saved_app_version` が文字列なら案内に含めます。v2 で保存したものは旧アプリでは開けません。
- **`mask_format` は画素の版であり、`format_version` とは独立です**（§5）。
- 暗号化 ZIP、重複エントリ名、上限超過を拒否します。v2 は未参照エントリも含め、
  絶対パス・`..`・バックスラッシュ・コロンを含む名前を拒否します。
  PNG 参照は ZIP 内の安全な相対パスで拡張子 `.png`（大文字小文字不問）。
  reader は安全な非正規名も受理し、writer は次の正規名へ統一します。

| 内容 | 保存名（桁数は最小幅） |
|---|---|
| 番号付き画像（未配置・用紙・下書きも共通） | `layers/layer_LLLL/cell_NNNNNN.png` |
| その初期状態 | `initial/layers/layer_LLLL/cell_NNNNNN.png` |
| 番号を持たないローカル状態等の例外 | `compatibility/layer_LLLL/position_CCCCCC.png` |
| その初期状態 | `initial/compatibility/layer_LLLL/position_CCCCCC.png` |

Track番号 L は0始まり、絵番号 N は1始まり、位置 C は0始まりです。
同一Track・同一番号は画像1枚と任意の初期状態1枚。別番号は画素が同じでも別名です。

`thumbnail.png` は任意。保存時のコマを白背景で表示どおりに合成し、長辺512px以下へ
縮小します。**ZIP先頭・STORED・汎用フラグ bit3=0（データ記述子なし）**を守ります。
Finder / Explorer の拡張はオフセット0のローカルヘッダーから読みます。
プロジェクト読込では使いません。背景自動保存は現在サムネイルを付けません。

### 上限と検証

| 項目 | 定数・値 |
|---|---|
| 展開後ZIP合計（未参照・thumbnailを含む） | `MAX_PROJECT_ARCHIVE_BYTES` = 8 GiB |
| project.json | `MAX_PROJECT_METADATA_BYTES` = 16 MiB |
| PNG 1枚 | `MAX_PROJECT_IMAGE_BYTES` = 512 MiB |
| 位置数 N・番号・各 duration / length | `MAX_PROJECT_FRAMES` = 10000 |
| Track数 T | `MAX_PROJECT_LAYERS` = 256 |
| 構造・画像数の上限 | `MAX_PROJECT_LAYER_CELLS` = 100000 |
| 展開後画素 | `MAX_PROJECT_DECODED_PIXELS` = 2 × 1024³ 画素 |

v2 は `1 <= N <= 10000`、`1 <= T <= 256`、構造slot数 `S=N×T <= 100000`。
bank総数 B と local_states 総数 L は `B+L <= 100000`。S と B は合算しません。
positions / pending / 各 compatibility リストはTrack内で1位置1件、columnは `0..N-1`。
drawing_states は同Trackのbank番号を一度ずつ参照するため総数は B 以下です。
ZIPエントリ数は未参照も含め200002以下。正規writerの最大数は `2×(B+L)+2` です。

構造の型違い、必須欠落、重複番号、存在しない番号参照、sheet長不一致、未知の状態値を
拒否します。v2 は **JSONの同一オブジェクト内の重複キーも、階層を問わず拒否**します。
整数欄は bool を受理しません。JSON内の浮動小数は未知キーも含め有限である必要があります。
未知キーは無視してよく、現UIは再保存時の保持を保証しません。

PNG は IHDR の寸法をデコード前に確認し、全画像が canvas と一致する必要があります。
通常・initial・local を共通のPNG bytes辞書で数え、異なるbytesの種類数×幅×高さを
画素予算に算入します。同じbytesの複数参照は1回。thumbnailは画素予算から除外します。
v2 writerも一時ZIPを同じreaderで検査してから置き換え、失敗時は元ファイルを保ちます。
v1 は旧decoderの受理範囲・画素移行を維持します（付録A）。

## 2. v2 `project.json` トップレベル

```jsonc
{
  "format": "PaintMaskAnimatorProject",
  "format_version": 2,
  "mask_format": 2,
  "created_app_version": null,
  "saved_app_version": "0.7.3",
  "canvas": {"width": 1280, "height": 720},
  "fps": 24,
  "playback_range": [0, null],
  "timeline_mode": "sheet",
  "memo": "",
  "clip_studio_source": null,
  "current_frame": 0,
  "active_layer_index": 0,
  "colors": {"main": "#000000", "sub": "#FF0000", "mode": "main", "background": "#FFFFFF"},
  "display": {},
  "pressure": {"enabled": true, "minimum": 0.0, "maximum": 1.0, "curve": 1.0,
               "points": [[0.0, 0.0], [1.0, 1.0]]},
  "used_color_categories": [],
  "color_chart": {},
  "frame_durations": [1, 1, 1, 1, 1],
  "tracks": [/* §3 */]
}
```

必須は format / canvas.width,height / frame_durations / tracks。
`format_version` 欠落はv1へ振り分けるため、v2には明記します。
canvas は整数1..16384。frame_durations は非空配列、各値は整数1..10000です。
配列の長さが編集文書の位置数 N で、全Trackのsheet長も N です。
番号の連続区間を duration に畳みません。durations は従来の動画等の時間軸を保持します。

その他は状態復元用です。created/saved_app_version は任意文字列、欠落・非文字列は不明。
上書き・名前を付けて保存でもcreatedは維持し、不明ならnull。savedは保存時のアプリ版です。
current_frame / active_layer_index は省略時0、整数0..10000を受理し、文書内へクランプします。
fpsはUIで1..60（既定24）。memoはカット袋・シートのメモ文字列（既定空）。
playback_rangeは0始まりの `[開始, 終端]`、終端は**排他**、nullは末尾まで。
clip_studio_sourceはCLIP STUDIO取込元情報、used_color_categoriesは色分類、
color_chartは色見本の状態で、UIがそれぞれ復元・正規化します。
colors は `#RRGGBB` の main/sub/background と mode。display は§6。
pressureは保存しますが、UI openでは環境設定の筆圧設定を優先し、ファイルから適用しません。
timeline_modeも記録しますが、UI openはpending配置後にsheetへ固定します。
Undo/Redoと履歴分岐は復元せず、開いた状態を保存済みとして扱います。

## 3. Track・cells・sheet・positions

```jsonc
{
  "name": "A", "visible": true, "opacity": 1.0,
  "is_paper": false, "is_draft": false,
  "color_filter_enabled": false, "color_filter_rgb": null,
  "cells": [
    {"number": 3, "image": "layers/layer_0000/cell_000003.png",
     "initial": "initial/layers/layer_0000/cell_000003.png", "cell_name": "動画3"},
    {"number": 9, "image": "layers/layer_0000/cell_000009.png"}
  ],
  "sheet": [3, 3, "BLANK", "BLANK", null],
  "positions": [{"column": 2, "blank_start": true}, {"column": 3, "blank_start": true}],
  "pending": [], "drawing_states": []
}
```

tracks は非空配列。表示属性は任意で、nameは通常のレイヤー既定名、visible=true、
opacity=1.0、is_paper/is_draft/color_filter_enabled=false、color_filter_rgb=null。
nameは文字列、各フラグはbool、opacityは有限数を0..1へクランプします。
RGBはnullまたは0..255の整数3要素配列。**alpha_lockedは保存せず、open時false**です。
用紙と下書きも他のTrackと同じ構造で、番号・○・EMPTYを持てます。single_cellはありません。

cells と sheet は必須配列。cells は空可で、全bankを番号昇順に保存します。
numberはTrack内で一意の整数1..10000、imageは必須PNG参照、initialは任意PNG参照またはnull、
cell_nameは任意文字列またはnull（ともに既定null）。未配置bankも同じcellsへ保存し、
配置待ちやhintの有無からbankを推測しません。v2 openは番号を詰め直しません。

sheet は全Trackで長さ N。値は同Trackのcellsに存在する正整数（絵）、`"BLANK"`（○）、
null（EMPTY）のいずれか。0・負数・bool・数値文字列は番号ではありません。
BLANK/EMPTY自体は絵番号を持ちません。隣接同番号は1区間で、旧内容キー境界やexposureは
本体に保存しません。離れた同番号も同じCellを参照します。

positions は任意（既定[]）。columnで位置を指定し、blank_start（既定false）、tween、
tween_member（ともに既定null）を持ちます。blank_start=trueはsheetのBLANK上、または
同じcolumnにkindがBLANKのpendingがある場合だけ許可し、
隣接する○の開始を区別します。tweenは番号ではなく位置の属性です。

### あとから直せるトゥイーン

```jsonc
{
  "id": "3f2a…", "length": 6, "mode": "free", "reverse": false,
  "start_points": [[0.0, 0.0]], "final_points": [[1.0, 1.0]],
  "mesh_cols": 4, "mesh_rows": 4, "mesh_reference_points": []
}
```

idは区間識別文字列、lengthはキーを含む2以上の長さ、modeはfreeまたはmesh、
座標はキャンバス座標です。キーの後ろlength-1位置は、同じidのtween_memberを持つ絵です。
中割り画像はbankにもあるため、再生・書出しはtween情報を必要としません。
readerの `sanitize_spec` はid/lengthを変換し、空id・2未満の長さ・座標配列の型違い等で
設定をnullへ落とします。未知modeはfree、reverse=false、座標=[]、meshの行列=4が既定です。
tween_memberはnullか文字列。位置の並びが崩れた場合、`group_length` が区間として扱わず、
PNGは通常の絵として残します。区間崩れだけで文書全体を拒否しません。

## 4. pending・drawing_states・限定的な compatibility

### pending（配置待ち）

`{"column": 1, "number": 9, "kind": "CELL", "length": 2}` の配列、既定[]。
column / kindは必須、kindはCELL/BLANK/EMPTY。numberはnull（欠落も同じ）またはbank参照、
lengthは整数1..10000（既定1）。同位置は1件、別位置の同番号は重複保持します。
番号無しpendingを勝手に採番しません。sheetの見え方と独立した配置意図です。
低水準loadは自動配置せず、UI openが従来どおり配置します。内部tokenは読込時に新規生成します。

### drawing_states（明示的な描画状態）

```json
{"number": 9, "has_content": true, "is_blank_key": false, "length": 1,
 "pending": false, "tween": null, "tween_member": null}
```

任意配列（既定[]）。numberは必須のbank参照、Track内重複禁止。
その他の既定は上例のとおり（ArchiveHint相当）。フラグはbool、lengthは整数1..10000、
tween/memberは位置属性と同じ解釈です。明示hintの存在も保持し、未配置だからという理由で
hintを増やしません。画像と表示設定は含みません。v1読込時に失われた属性を推測復元しません。

### compatibility（任意オブジェクト）

通常の番号列から得られない配置・尺・解決差分と、**現在のlive local**だけを保存します。
旧DTOの全保存や旧hidden PNGの保管場所ではありません。各リストは任意、column重複禁止です。

- `placement_keys`: `{column, kind, number, length}` の配列。非pendingの旧明示キーが
  通常の番号区間と違うTrackだけ保存します。kindはCELL/BLANK、CELLのnumberはbank参照、
  BLANKのnumberはnull、lengthは整数1..10000。欠落時はsheetとblank_startとpendingから導出。
  同番号途中の旧キーもpendingの「最後の明示キー+1」配置に必要ですが、露出境界にはしません。
- `length_overrides`: `{column, length, guard, input_length?}`。
  **lengthは旧投影の保存長、input_lengthは有効なguardの下での入力末尾尺**です。
  いずれも整数1..10000、input_length欠落時はlength。
  guardは `{kind, number, pending, blank_start, imported}`。
  kind/numberはその位置のsheet参照、pendingはpending有無、blank_startは位置属性と一致必須。
  importedはboolで、未変更入力由来の尺を使えるかを表します。
  旧import署名の一致・互換マーカーが有効な間だけ尺へ反映し、編集で失効したものを保存時に
  guardを更新して復活させません。出力尺は文書長に有効なcolumn+length/input_lengthを加味します。
  7位置でも尺8となる例を保ち、文書を8位置へ伸ばしたり単一export_durationに置換したりしません。
- `resolved_overrides`: `{column, value, source_column}`。valueとsource_columnは必須で、
  両方null（画像なし）またはbank番号と編集元位置です。source_columnは文書内で、
  その位置がvalueの番号を持つ絵またはpendingである必要があります。
  writerは再構成後の同じTrackのsheet/pendingでこの条件を満たす元sourceを保持します。
  満たさない場合だけ、同番号の実在位置のうち元source以下の最大column、
  なければ最小columnへ正規化します。ただし元sourceの画像と同番号のbank Cellの画像が
  同じ場合だけ寄せます。表示が変わる場合や候補がない場合は元sourceを残し、
  読み戻し検証で保存を拒否します。
  sheetから導く通常の画像解決と違う位置だけ保存します。同じ画像でも編集元の違いを保持します。
- `local_states`: `{column, retained_number, image?, initial?, cell_name?}`。
  ○/EMPTYのlocalと番号無しpendingを保持します。番号付きの絵/pendingとは併用しません。
  retained_numberはnull（既定）または整数1..10000で、bank参照ではありません。
  残存番号に描き始めてもその番号を使わず、従来どおり末尾の新番号になります。
  image欠落は透明画像を生成し、initial/cell_nameはnull既定。
  writerは**全透明・initialなし・名前なしの既定localだけPNG参照を省略**します。
  retained_numberと位置情報は省きません。非既定画像・initial・名前は保存します。
  過去の `_v1_position.legacy` だけに残る別PNGは収集しません。

## 5. 画素規約（版とは独立した互換契約）

PNGをデコードした画像は内部で **premultiplied alpha（乗算済みアルファ）** として扱います。
PNGファイル自体のチャンネル表現と、合成用メモリの乗算済み表現を混同しないでください。
通常の描画で白は消しゴムとしてalpha=0になります。用紙・下書きや取込画像の白を
無条件に消す契約ではありません。PNG寸法はcanvasと一致必須です。

| mask_format | 画素解釈 |
|---|---|
| 2（現行） | 消去領域をalpha=0で格納 |
| 1または欠落 | 旧擬似透明の不透明白をロード時に透明へ移行 |

保存は常に `mask_format=2`。v2 readerは整数1..10000を受理し、2以上は現行解釈です。
`mask_format < 2` の白→透明はpaper/draftを除外します。
v2ではbank・initial・localの全PNGに同じ規則を使います。
v1のinitialは旧挙動どおり白変換せず、sequence_archiveの判定にはdraftだけを使います。
この読込互換の違いも維持します。`format_version=1` はmask_format=1の意味ではなく、
v1でもmask_format=2があります。移行の参照は `imaging.white_to_transparent_qimage`。

## 6. `display`（表示状態・オニオンスキン）

ロードには必須でないが round-trip 対象。主なキー:

`silhouette_non_background`, `onion_skin`, `onion_previous_count`,
`onion_next_count`, `onion_previous_opacity`, `onion_next_opacity`,
`onion_previous_levels`(int配列), `onion_next_levels`(int配列),
`onion_center_percent`, `onion_previous_color`, `onion_next_color`,
`onion_previous_color_enabled`, `onion_next_color_enabled`,
`onion_selected_colors_only`, `onion_previous_shift_x/y`,
`onion_previous_rotation`, `onion_previous_scale`,
`onion_next_shift_x/y`, `onion_next_rotation`, `onion_next_scale`,
`onion_tu_tb_scale`。

色は `"#RRGGBB"` 文字列。


表示状態は任意で、欠落時は現UIの既定値を使います。UI openで筆圧・timeline_modeを
ファイルどおりに戻さない点は§2のとおりです。

## 7. 相互運用テスト（将来フォークの受け入れ基準）

1. `tests/fixtures/projects/v1/` と `v2/` の固定ファイルを両版で読み、expectedの
   独立した意味JSONと正規画素hashへ一致させます。生成・hash規約は同フォルダREADMEを参照。
   format_versionの1/2とmask_formatの1/2を別々に試験します。
2. v2 goldenのbasic-bank-sheet / pending-compat / paper-draft / tween-initial-displayで、
   全bank・initial/name・未配置番号、全sheet位置・隣接○開始・EMPTY、表示属性、durations、
   pending重複/None、hint、placement_keys、保存長/入力末尾尺のguard、resolveの編集元、
   local画像/残存番号、tween位置を確認します。全位置の合成画素も比較します。
3. 低水準load（pending自動配置前）とUI open（自動配置後）の期待を分け、
   v2 open→保存→openでそれぞれの意味・画素を維持します。PNG圧縮・JSONキー順・ZIP時刻の
   バイト一致は求めません。v2番号は詰めず、alpha lock解除等のUI方針も含めて確認します。
4. v1→v2→v2でも旧無番号採番、paper/draftの白、旧pending配置先と末尾外尺を維持します。
   v1のraw hidden PNGや旧書式そのものの再生成は要件ではありません。
5. 暗号化・重複ZIP名/JSONキー・危険パス・不正構造/番号参照・非有限数・上限超過を拒否し、
   未対応format_versionをPNG前に拒否します。thumbnailの先頭STORED/bit3=0も確認します。

実機相互運用はフォーク実装時に検証します。PC側の意味往復試験はtestsに置き、
通常pytestではgoldenを再生成しません。

## 付録A. v1（読み込み互換のため）

通常保存はv2です。この付録は既存ファイルを読むための仕様で、v1保存の選択肢を
利用者へ提供するものではありません。固定v1素材と旧hashを保守する
`legacy_fixture_*` helperはテスト専用です。

トップレベルの共通状態は§2と同じですが、frame_durations / tracksの代わりに
非空framesと任意sequence_archive（既定[]）を持ちます。
`format_version`は1または欠落、`mask_format`は独立で1/2があります。
canvas欠落時の旧既定は1280×720で、各辺1..16384を検査します。
framesの位置数、各duration、レイヤー数、全フレームのレイヤー数一致、
frame層セル数とarchive件数の合計に旧上限を適用します。
JSON重複キー拒否などv2固有の厳格化をv1へ遡及しません。

### frames と layers


`frames` は非空の配列。全フレームで**レイヤー数が一致**していること
（不一致は拒否）。

```jsonc
{
  "duration": 1,          // 1..MAX_PROJECT_FRAMES
  "layers": [
    {
      "name": "A",
      "image": "layers/layer_0000/frame_000000.png",  // 必須
      "initial": null,                // null か色塗り前の初期状態PNGへの参照（任意）
      "visible": true,
      "opacity": 1.0,                 // 0.0..1.0 にクランプ
      "is_paper": false,              // 用紙レイヤー（白→透明移行の対象外）
      "has_content": false,
      "exposure": 1,                  // 1..MAX_PROJECT_FRAMES
      "is_blank_key": false,          // 未指定時は has_content と exposure から推定
      "sequence_number": null,        // null か 1..MAX_PROJECT_FRAMES（絵番号）
      "sequence_only": false,
      "is_draft": false,              // 下書き（白→透明移行の対象外）
      "tween": null,                  // null かトゥイーンの設定（キーのセルだけ、下記）
      "tween_member": null,           // null かトゥイーンの id（中割りのセル）
      "color_filter_enabled": false,
      "color_filter_rgb": null        // null か [R,G,B]
    }
  ]
}
```

- レイヤー配列が空の場合は拒否します。
- `initial` は通常画像と同じキャンバスサイズのPNG。通常セルは
  `initial/layer_0000/frame_000000.png`、連番保管セル（`sequence_archive`）は
  `initial/sequence_archive/layer_0000/cell_000001.png` に保存する。
  保管セルの各エントリにも同じ任意フィールド `initial` を持ち、未指定なら初期状態なし。
- `is_blank_key` 未指定時の推定: `has_content == false` かつ `exposure > 1`
  なら `true`。
- `tween` / `tween_member`（任意）: あとから直せるトゥイーン。中割りの絵は
  通常のセルと同じく各コマの PNG に入っていて、再生や書き出しにはこの 2 つは
  不要。キーのセルの `tween` は次の形で、中割りのセル（キーの直後から
  `length - 1` コマ、それぞれ `exposure == 1`）の `tween_member` に同じ `id` を持つ。
  並びが崩れていたり値が不正だったりする場合は、通常のセルとして扱う。

  ```jsonc
  {
    "id": "3f2a…",          // 区間を見分けるための文字列
    "length": 6,            // キーを含むコマ数（2 以上）
    "mode": "free",         // "free"（自由変形）か "mesh"（メッシュ変形）
    "reverse": false,       // 逆生成（キー側が変形後、末尾が元の形）
    "start_points": [[x, y], …],   // 変形前の制御点（キャンバス座標）
    "final_points": [[x, y], …],   // 変形後の制御点
    "mesh_cols": 4, "mesh_rows": 4,
    "mesh_reference_points": [[x, y], …]
  }
  ```

各layerは任意の `cell_name`（nullまたは名前文字列）も持ちます。
alpha_lockedは旧入力でも無視し、開くとfalseです。
無番号の通常描画は読込後に採番し、同番号の画像・initial・名前は既存のcursor/競合解決規則で
Trackのbankへ集約します。内容キーが全く無いv1のUI openは従来どおり番号を詰めます。

### sequence_archive

各要素は必須の `layer_index`（0始まりの存在する層）、`sequence_number`（1..10000）、
`image` を持ち、同じ層・番号の重複を拒否します。
任意属性は `initial` / `cell_name`（null既定）、`name`、`visible`（true）、`opacity`（1.0）、
`has_content`（true）、`is_blank_key`（false）、`is_draft`（false）、
`color_filter_enabled`（false）、`color_filter_rgb`（null）。
通常画像名は `sequence_archive/layer_LLLL/cell_NNNNNN.png`、
initialは `initial/sequence_archive/layer_LLLL/cell_NNNNNN.png`。
旧archiveにはexposure / sequence_only / tween / tween_memberを保存していません。
読込時はpaper=falseとして扱い、mask_format=1の白変換除外はdraftだけです。
archiveと配置済みの同番号は別の絵として残さず、旧競合解決を経て同じbankへ統合します。
