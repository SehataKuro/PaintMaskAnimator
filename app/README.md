# app — PaintMaskAnimator の画面（Tauri）

0.9.0 で Qt の画面（`paintmaskanimator/`）と置き換える画面です。方針は
[docs/design-principles.md](../docs/design-principles.md) の「0.9.0 で Tauri に移る」にあります。

画面は `ui/` の HTML と JavaScript（組み立ての道具は使わない）、中身は Rust です。
計算とデータは `pma-core`（`native/core`）をこれから使います。

## 今あるもの

- 窓とキャンバス（筆圧で太さが変わる線を描くだけ）
- ペンの筆圧・傾きを画面へ送る部品（[`src/pen.rs`](src/pen.rs)）
  - macOS：WKWebView は筆圧を JavaScript に渡さないので、NSEvent のローカルモニターで読み、
    `pen` の事件として送る。画面は macOS ではこの列だけで線を描く。
  - Windows：WebView2 の PointerEvent の筆圧をそのまま使う。
- 色のパネル：カラーサークル・カラースライダー・使用色・カラーチャートの窓
  （[`src/colors.rs`](src/colors.rs)、`ui/color_*.js`・`ui/used_colors.js`）
  - 状態（描画色・使用色の並び・親子・フォルダー・表示・鍵・選択・置換色・チャート）は Rust の
    `Session` にあり、計算は pma-core の `used_colors`。画面は `palette-changed`・`current-color`・
    `chart-changed`・`display-changed` の事件で表示を合わせる。
  - 使用色の選択と鍵で、ブラシの描ける範囲を絞る（#104）。
  - スポイトは、WKWebView に EyeDropper が無いので、画面ではなくキャンバスの色を拾う。

## 動かし方

```bash
cd app && cargo run --release
```

筆圧の経路の自己確認（macOS）。ペンの形の事件と、事件の列を通るマウスの事件を自分の窓に入れ、
線が筆圧どおりに描けたかを調べて終わります。ほかのアプリや画面の操作は奪いません。

```bash
cd app && PMA_PEN_SELFTEST=1 cargo run --release
```

開いている作品（文書と履歴）は Rust の `Session`（[`src/session.rs`](src/session.rs)）に一つだけあり、
キャンバスもタイムラインもそれを読み書きします。文書が変わると Rust が `document-changed` の事件を
送り、キャンバスとタイムラインはそれを受けて表示を合わせます。取り消しはアプリ全体で一つです。

確かめ用の見本の文書（.pman を渡さないとき）と、再生の速さの自己確認。再生して、キャンバスが
何 fps で今のコマを表示できたかを報告して終わります。

```bash
cd app && PMA_TIMELINE_SAMPLE=48 PMA_TIMELINE_SAMPLE_SIZE=1920x1080 \
  PMA_PLAYBACK_SELFTEST=96 cargo run --release
```

## アプリの外枠（メニュー・環境設定など）

Qt 版の MainWindow のうちパネルの外の部分は、Rust の [`src/shell.rs`](src/shell.rs)（と menu.rs・
prefs.rs・shortcuts.rs・autosave.rs・papers.rs・i18n.rs）と、画面の `ui/shell.js`（と
dialogs.js・preferences.js・shortcuts.js・history_panel.js・i18n.js・theme.css）にあります。

- メニューは OS のメニュー（tauri::menu）。項目は Qt と同じ並び・言葉・キー。選ぶと
  `menu-action` の事件で画面へ送り、画面が処理する。まだ移していない操作は `pma-action` の
  事件を窓へ出し、どのパネルも受けなければ「まだ移していません」と知らせる。
- 設定はアプリの設定のフォルダーの `config.json`。初めての起動で Qt 版の設定を写す
  （ワークスペースは Qt の配置の形のまま写し、選ぶと今の画面の並びへ読み替える）。確かめるときは `PMA_CONFIG_DIR` で別の
  フォルダーにできる。
- 英語の表 `i18n/en.json` は `paintmaskanimator_en.ts` から作る
  （`PMA_UPDATE_I18N=1 cargo test i18n_catalogue`）。

## まだ確かめていないこと

- 実機のペンタブレット（Wacom など）で、筆圧・傾き・消しゴム側が取れるか（macOS・Windows）
- Windows の WebView2 で、PointerEvent に筆圧が来るか

実機で確かめるときは、`cargo run --release` で開き、キャンバスに線を描いてください。
下の帯に、入力の種類（ペン・消しゴム・マウス）と筆圧・傾きが出ます。
