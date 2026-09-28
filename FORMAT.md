# PaintMaskAnimator プロジェクトフォーマット仕様 (`.pman`)

このドキュメントは `.pman` プロジェクトファイルの**唯一の正**です。PC版
(PySide6) と iPad版フォーク (Swift + Rust) の両方がこの仕様に従うことで、
同じファイルを相互に読み書きできます。実装を変えるときは、まずこの仕様を
更新してください。

参照実装: [`paintmaskanimator/project_io.py`](paintmaskanimator/project_io.py)
（`read_project_archive` / `write_project_archive`）、
メタデータ生成は [`main_window.py`](paintmaskanimator/main_window.py) の
`build_project_metadata`。

---

## 1. コンテナ

- `.pman` は **ZIP アーカイブ**（`ZIP_DEFLATED`, compresslevel 6 で書き出し）。
- **暗号化 ZIP は非対応**（読み込み時に拒否する）。
- エントリ構成:
  - `project.json` — UTF-8 の JSON メタデータ（必須、1個）。
  - `layers/layer_{LL}/frame_{FFFFFF}.png` — レイヤーセルごとの PNG。
    - `LL` = レイヤー番号 4桁ゼロ詰め（`0000` 始まり）。
    - `FFFFFF` = フレーム番号 6桁ゼロ詰め（`000000` 始まり）。
    - 例: `layers/layer_0002/frame_000015.png`
- パス規約（読み込み時に検証・強制）:
  - 画像参照は**相対パスのみ**。絶対パス・`..` を含むもの・`.png` 以外は拒否。
  - エントリ名の重複は拒否。

### サイズ上限（`constants.py` の値。両実装で揃えること）

| 項目 | 定数 |
|---|---|
| 展開後アーカイブ合計バイト | `MAX_PROJECT_ARCHIVE_BYTES` |
| メタデータバイト | `MAX_PROJECT_METADATA_BYTES` |
| 画像1枚のバイト | `MAX_PROJECT_IMAGE_BYTES` |
| フレーム数 | `MAX_PROJECT_FRAMES` |
| レイヤー数 | `MAX_PROJECT_LAYERS` |
| レイヤーセル総数 | `MAX_PROJECT_LAYER_CELLS` |
| 展開後総画素数 | `MAX_PROJECT_DECODED_PIXELS` |

---

## 2. `project.json` トップレベル

```jsonc
{
  "format": "PaintMaskAnimatorProject",   // 固定。これ以外は拒否
  "format_version": 1,
  "mask_format": 2,                        // §4 参照。読み込み時の画素解釈を決める
  "application_version": 100,
  "canvas": { "width": 1280, "height": 720 },  // 1..16384
  "fps": 24,
  "timeline_mode": "...",
  "current_frame": 0,
  "active_layer_index": 0,
  "colors": { "main": "#RRGGBB", "sub": "#RRGGBB", "mode": "...", "background": "#RRGGBB" },
  "display": { /* オニオンスキン等の表示状態。§5 */ },
  "pressure": { "enabled": true, "minimum": 0.0, "maximum": 1.0, "curve": 1.0,
                "points": [[0.0,0.0],[1.0,1.0]] },
  "frames": [ /* §3 */ ]
}
```

> 互換の旧形式名 `"OekakiAnimationProject"` は**廃止**。現行は
> `"PaintMaskAnimatorProject"` のみ受理する。

### ロードに必須 vs. 状態復元用

- **必須（欠けるとロード不能）**: `format`, `canvas.width/height`, `frames`
  （非空リスト）、各レイヤーの `image` 参照。
- **状態復元用（欠けても既定値で継続）**: `fps`, `colors`, `display`,
  `pressure`, `current_frame`, `active_layer_index`, `timeline_mode` など。
  未知キーは無視してよいが、**保存時は保持（round-trip）することを推奨**。

---

## 3. `frames` と `layers`

`frames` は非空の配列。全フレームで**レイヤー数が一致**していること
（不一致は拒否）。

```jsonc
{
  "duration": 1,          // 1..MAX_PROJECT_FRAMES
  "layers": [
    {
      "name": "A",
      "image": "layers/layer_0000/frame_000000.png",  // 必須
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

- レイヤーが空になった場合、ローダは空白レイヤー1枚で補完する。
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

---

## 4. 画素規約（**互換の最重要ポイント**）

- 各 PNG は内部的に **premultiplied alpha（乗算済みアルファ、ARGB32 相当）**
  として扱う。Swift/Metal 側の合成でここを取り違えると縁が汚れる。
- **`#FFFFFF`（白）＝消しゴム**。白で描いた領域は alpha=0 として保存する。
  アプリ全体でこの規約に従う。
- 読み込み時、PNG のサイズは `canvas.width × canvas.height` と一致必須
  （不一致は拒否）。

### `mask_format`

| 値 | 意味 |
|---|---|
| `2`（現行 `CURRENT_MASK_FORMAT`） | 消去領域は **alpha=0** で格納。 |
| `1` または欠落 | 消去領域を不透明 `#FFFFFF` の擬似透明として格納。**ロード時に「白→透明」マイグレーションが必要**。 |

- マイグレーション（`mask_format < 2`）は、**`is_paper` と `is_draft` を
  除く**全レイヤーに適用する（用紙・下書きは白画素をそのまま保持）。
- 参照: `white_to_transparent_qimage`（[`imaging.py`](paintmaskanimator/imaging.py)）。
- **書き出しは常に `mask_format = 2`。** format 1 は読み込み互換のためだけに
  存在する。

---

## 5. `display`（表示状態・オニオンスキン）

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

---

## 6. 相互運用テスト（フォーク実装時の受け入れ基準）

1. **round-trip**: PC版で作った `.pman` を iPad版で開き、無変更で保存 →
   `project.json` の意味的差分がなく、全 PNG の画素が一致すること。
2. **画素一致**: 白＝消しゴム、premultiplied alpha、mask_format=2 の解釈が
   両実装で bit 単位に近い一致をすること（golden ファイルで検証）。
3. **format 1 マイグレーション**: 旧 mask_format=1 のファイルを両実装で開き、
   白→透明結果が一致すること。
4. **拒否系**: 暗号化 ZIP、`..` を含むパス、サイズ超過、レイヤー数不一致、
   `format` 不一致 を両実装が拒否すること。

`tests/` に PC版の characterization テストがあるので、golden `.pman` は
そこから抽出するとよい。
