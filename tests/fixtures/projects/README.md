# Phase 5-6 A: v1 compatibility captures

生成元は利用者指定の現行 a49a0b5。人工画像のみ（4×3、外部素材なし）。
`v1/*.pman` は現行 `project_io.write_project_archive` の実出力。
`unnumbered-mask1` だけは出力後に mask_format を 1 にし、歴史的な白→透明変換の入力とする。
全ファイル format_version=1、その他 mask_format=2。ZIP/PNG bytes の一致は比較しない。

通常の pytest はファイルを一切生成・更新しない。期待 JSON は現行アプリを実行して
事前採取した literal であり、実行時の旧実装を比較 oracle にしない。
保存後は必ず開き直した状態を観測する。D で writer が v2 に変わっても操作テストは同じ期待を使う。
`read_install` は自動配置前、`mode_placement` は明示モード変更後、`ui_open` は UI open 後。
続いて保存・open、追加 pending→再配置→保存・open、削除→Undo→Redo→保存・open、
bank の再呼び出し→Undo、末尾で描画→Undo→Redo を別状態に固定する。

## 入力ケース

| stem | 固定した入力 |
|---|---|
| unnumbered-mask1 | 無番号、保持、initial、cell_name、旧白変換 |
| numbered-paper-draft | 用紙と下書きの複数番号/○/EMPTY、alpha lock 入力、opacity/filter |
| pending-collision-7-6 / 2-2 | test_sheet_readers の同名ケースに対応する6位置、番号1長4、位置2別キー、位置5 pending2 |
| hidden-retained | ○残存12、ローカルinitial/name、隣接○開始 |
| archive-conflict | 配置済み3とarchive3の画像/initial/name競合、未配置9 |
| legacy-attribute-conflict | 同番号の画像/initial/nameおよび表示属性の競合、cursor=1（archive-conflict は2） |
| pending-none | None番号の配置待ち |
| pending-duplicate | 異なる位置の同番号pending2 |
| blank-self-collision | ○2とpending○2の自己衝突 |
| missing | 直前番号の無いpending2、番号8の旧長4 |
| all-unplaced | 内容キーなし、bank番号1,8,12（UI open の1,2,3への正規化） |
| tween-initial | tween/member、initial/name、同番号の位置指定 |
| placement-key-boundary | 同番号1の旧明示キー2つとpending9、後続配置先 |

全画像に透明（透明fillケース）・半透明RGBA(20,80,160,128)・不透明白を含む。
通常・用紙・下書きそれぞれの白と半透明を観測できる。

## 画素 hash と観測

QImage を `Format_RGBA8888_Premultiplied` に変換し、上から下、各行左から右、
R,G,B,A の4 bytes/画素を連結した SHA-256。各行 `width * 4` bytes のみを採り、
`bytesPerLine` の padding は除外する。サイズも別に記録する。PNG 圧縮・Qt cacheKey・
CPU の ARGB メモリ順序に依存しない。合成は各文書位置 `composite(i, white=False)`。
bank全件とinitial、live各位置画像/initial/name、sheet、blank開始、resolve元、span、
tween、Track8設定、hint全属性、pending（column/number/kind/旧length）、未配置番号、durations、出力尺/全行、
UI設定・dirty・Undo/Redo型を記録する。パスやZIP時刻、アプリの保存版は意味比較に含めない。

## 明示的な再採取

リポジトリ直下で以下を実行する（旧v1の writer が存在する採取元のみ）。

```sh
QT_QPA_PLATFORM=offscreen .venv/bin/python tests/fixtures/projects/regenerate.py --update
```

`--update` が無ければエラー終了する。v1 は以降の移行段階で再生成しない。
期待値だけの意図的な更新には `--update --expectations-only` を使えるが、
差異を必ず人間がレビューし、v2 の新しい結果による一括置換をしない。
libEGL が無い環境では指定の互換ライブラリディレクトリを LD_LIBRARY_PATH に設定する。
