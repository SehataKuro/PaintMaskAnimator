# 画面を Rust にする判断のための試作

[docs/design-principles.md](../docs/design-principles.md) の「画面まで Rust にするかの判断」に挙げた
4点を、2つの方式の小さな試作で確かめた記録です。どちらもアプリには組み込みません。

| 試作 | 画面 | ドッキング |
|---|---|---|
| [egui-dock](egui-dock) | egui 0.37（eframe、wgpu） | egui_dock 0.22 |
| [tauri-dock](tauri-dock) | Tauri 2.12（macOS は WKWebView、Windows は WebView2） | dockview-core 8.9.1 |

どちらも同じ配置（ツール・カラー／キャンバス 1920×1080／レイヤー・スクリプト／タイムライン）と、
同じ確認の手順を持ちます。egui 0.37 は Rust 1.95 を要るので、試作ごとに `rust-toolchain.toml` を置きました
（本体の 1.92 は変えていません）。

## 動かし方

```sh
cd prototypes/egui-dock && cargo run --release
cd prototypes/tauri-dock && ./fetch-dockview.sh && cargo run --release
```

確認用の動かし方：

| 確認 | egui-dock | tauri-dock |
|---|---|---|
| ドッキングの台本と録画 | `PMA_DEMO_OUT=<dir>`（毎フレームを PNG で保存） | `PMA_DEMO_OUT=<dir>`（WKWebView の写しを保存） |
| ブラシの重さ | `--bench`（計算のみ）、`--latency`（1フレームに4回の移動を1200フレーム） | `PMA_LATENCY=1` |
| 筆圧の経路 | `--pen-selftest` | `PMA_PEN_SELFTEST=1` |
| 日本語入力の経路 | `--ime-native`（OS の窓口）、`--ime-selftest`（egui の事件） | `PMA_IME_SELFTEST=1` |

今の Qt 版の比較は `QT_QPA_PLATFORM=offscreen .venv/bin/python prototypes/bench/qt_brush_latency.py`。

台本も自己確認も、自分のアプリの中で入力を作ります。ほかのアプリや画面・キーボードの操作は奪いません。
動画は ffmpeg で PNG の列から作りました（`prototypes/videos/`、管理外）。

## 結果（2026-11-26、macOS 26・Apple Silicon、ペンタブレットは無し）

### 1. 筆圧（macOS と Windows）

| | macOS | Windows |
|---|---|---|
| egui | **そのままでは取れない**。winit 0.30 はタブレットの筆圧を渡さない（iOS のタッチとトラックパッドだけ）。NSEvent のローカルモニターで読む部品を足し、タブレットの形の事件（subtype＝tabletPoint、筆圧 0.1→1.0→0.1）で線の太さが筆圧に従うことを確かめた（`線：62 区間、筆圧 0.10〜1.00`）。 | 未確認。winit は渡さないので、WM_POINTER か Wintab を別に読む部品が要る。 |
| Tauri | **WKWebView は渡さない**。同じタブレットの形の事件を窓に渡すと、JavaScript には `pointerType=mouse`、`pressure=0.00` で届いた。egui と同じく Rust 側で NSEvent を読み、画面へ送る必要がある。 | 未確認。WebView2（Chromium）は Windows Ink のペンを PointerEvent の筆圧として渡す作りだが、実機で確かめていない。 |

実機のペンでの確認はまだです（この Mac に無い）。どちらの方式も macOS では自前の部品が要ります。

### 2. ブラシの遅れ（1920×1080）

| | 1区間の計算 | 1フレームの CPU | フレームの間隔（1フレームに4回の移動） |
|---|---|---|---|
| 今の Qt 版 | 649µs（95% 934µs） | 描画 0.68ms（＋区間×4 ≒ 3.3ms） | — |
| egui | 1.7µs（計算のみ）／3µs（テクスチャの部分更新込み） | 0.91ms（95% 1.19ms） | 16.7ms（95% 17.5ms） |
| Tauri | 約136µs（平均。WebKit の時計は 1ms 刻み） | 0〜1ms | 17.0ms（95% 18.0ms） |

どちらも 60Hz の画面の1フレームに十分収まり、今の Qt 版より計算は軽いです。入力から画面に出るまでの
本当の遅れ（垂直同期や合成の待ち）は、高速度カメラなどの道具が無いので測っていません。

### 3. 日本語入力（レイヤー名・スクリプト）

IME が使う macOS の窓口（NSTextInputClient の setMarkedText＝変換中、insertText＝確定）を、
文字を受け取るビューに直接呼んで確かめました。

- egui：変換中の文字が欄に出て（「線画せんが」）、確定で「線画線画」になった。表示は1フレーム遅れて出る。
- Tauri：変換中「線画せんが」→「線画線画」、確定で「線画線画」。compositionstart／update／end も出た。
  WebKit の標準の入力なので、候補窓の位置なども Safari と同じ。

キーボードと IME を実際に使っての確認（候補窓の位置、変換中の下線の表示）はまだです。

### 4. フロート・ドッキング・タブ分割

動画：`prototypes/videos/egui-dock-docking.mp4`、`prototypes/videos/tauri-dockview-docking.mp4`。
どちらも、右に分割 → タブとしてまとめる → フロート → 浮いた窓を動かす → 下に分割して戻す →
左に分割 → 区切りで大きさを変える、まで同じ手順でできました。

違い：

- egui_dock：ドロップ先は十字のボタン（Qt Advanced Docking System と同じ型）。ボタンの外で離すとフロート。
  浮いた窓は題名の帯が無く、タブの帯の空いた所を掴んで動かす。
- dockview：ドロップ先はパネルの端の帯（端に寄せると分割、中央でタブ）。フロートは Shift を押してタブを掴む。
  浮いた窓は題名の帯で動かす。別の OS の窓に出す（ポップアウト）機能もあるが、Tauri では未確認。
- **どちらも浮いた窓は本体の窓の中**です。今の Qt（ADS）のように、別の OS の窓として別の画面へ出すことは
  この試作ではできていません。egui は複数の viewport、Tauri は別の窓とつなげば作れる見込みですが、未確認です。

## まとめ

| | 筆圧 | ブラシ | 日本語入力 | ドッキング |
|---|---|---|---|---|
| egui + egui_dock | macOS は自前の部品で可（実機未確認）、Windows は部品が要る | ○ | ○（OS の窓口まで） | ○（浮いた窓は本体の中） |
| Tauri + dockview | macOS は自前の部品が要る、Windows は WebView2 しだい（未確認） | ○ | ○（WebKit の標準） | ○（浮いた窓は本体の中） |

4点のうち、筆圧の実機での確認と、浮いた窓を別の OS の窓に出すことが残っています。
これが済むまでは、設計方針どおり画面は Qt のままにします。
