# 画面を Rust にする判断のための試作

[docs/design-principles.md](../docs/design-principles.md) の「画面の方式：Tauri に決めた」に挙げた
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
| 別の OS の窓に出す台本と録画 | `PMA_POPOUT_OUT=<dir>`（窓ごとの写しと位置） | `PMA_POPOUT_OUT=<dir>`（同じ） |
| ブラシの重さ | `--bench`（計算のみ）、`--latency`（1フレームに4回の移動を1200フレーム） | `PMA_LATENCY=1` |
| 筆圧の経路 | `--pen-selftest` | `PMA_PEN_SELFTEST=1` |
| 日本語入力の経路 | `--ime-native`（OS の窓口）、`--ime-selftest`（egui の事件） | `PMA_IME_SELFTEST=1` |

別の窓の録画は、窓ごとの写しを画面の上の位置に並べて動画にする：
`.venv/bin/python prototypes/bench/compose_popout.py <dir> <出力の dir>` のあと ffmpeg。

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
- egui_dock・dockview とも、浮いた窓（フロート）は本体の窓の中です。別の OS の窓に出すのは次の「5.」で確かめました。

### 5. 別の OS の窓に出す（今の Qt の ADS と同じ使い方）

動画：`prototypes/videos/egui-popout-os-windows.mp4`、`prototypes/videos/tauri-popout-os-windows.mp4`。
どちらも、パネルを別の OS の窓に出す → もう1つの画面（外部モニター）へ動かす → その窓の中の欄に文字を入れる →
2つ目も出す → 「メインの窓に戻す」で戻す → 外へ出した窓をメインの窓の上まで動かして止めると戻る、までできました。
台本の終わりの近くで NSApplication の窓の一覧を取り、OS の窓が3枚あり、別の窓は外部モニター「Mi Monitor」の上で、
メインの窓（内蔵の画面）の外にあることを確かめています。

- egui：egui_dock には無いので自前で作った（`egui-dock/src/popout.rs`、台本を除いて約240行）。
  タブの右クリックの「別の窓に出す」でタブを DockState から外し、deferred viewport（自分の OS の窓）に描く。
  戻すときは、出す前に同じ葉（leaf）にいたタブの隣へ入れる。その葉が空いて消えていたら、キャンバスの右に列を作る
  （キャンバスの葉だけを分けるので、新しい列はタイムラインの上までの高さになり、もとの全高の列とは形が違う）
  （egui_dock の NodeIndex は葉が消えると付け直されるので、位置は覚えられない。Qt の ADS のように
  もとの分割の形まで戻すには、配置を自前で覚える作りが要る）。**パネルの中身は窓をまたいで共有する必要がある**ので、
  レイヤー名・スクリプトは `Arc<Mutex<…>>` に移した（キャンバスなどほかのパネルは、同じ作りに直すまで出せない）。
  immediate viewport は写し（Screenshot）が取れないので deferred にした。
- Tauri：dockview の popout（`window.open`）を、Tauri の `on_new_window` で同じ WKWebView の設定を持つ窓として開く
  （設定が違うと開いた側から中身を動かせない）。パネルの DOM が別の窓へ移るだけで、JavaScript の状態は
  メインの窓の1つのまま。窓を閉じると dockview がもとの場所に戻す。
- メインの窓の上で止めたら戻す、はどちらも自前（窓の位置を毎フレーム見る）。一度メインの窓の外へ完全に出した
  窓だけを対象にした（出したばかりの窓がメインの窓に重なっていても戻らないように）。Qt の ADS のような
  ドロップ先の表示（上下左右に分割して戻す）は、どちらも作っていない。
- 台本は窓を OS の「窓を動かす」命令で動かした。人が題名の帯を掴んで動かす操作は、画面を奪うので試していない
  （OS が窓を動かすので、同じ位置の変化になるはず）。
- **別の窓の中での入力は OS を通していない**。egui は台本の事件をその窓の egui に直接入れ、Tauri はメインの窓の
  JavaScript から欄の値を書き換えた。別の窓のキーボードの焦点と日本語入力（IME）は確かめていない。
- Tauri（tao）は macOS で full-size content view を使うので、窓の外枠と中身に同じ位置を返し、WKWebView も
  窓全体（題名の帯の下まで）に広がる。ページは WebKit が題名の帯（32pt）の下から並べる。窓だけを撮った写し
  （`screencapture -l`）でタブの列が帯の下に見えることを確かめ、録画の合成もそのように置いた。
- Windows では未確認（egui は winit の複数の窓、Tauri は WebView2 の `window.open` で、同じ作りになる見込み）。

## まとめ

| | 筆圧 | ブラシ | 日本語入力 | ドッキング |
|---|---|---|---|---|
| egui + egui_dock | macOS は自前の部品で可（実機未確認）、Windows は部品が要る | ○ | ○（OS の窓口まで） | ○（別の OS の窓は自前。パネルの状態を窓をまたいで共有する作りが要る） |
| Tauri + dockview | macOS は自前の部品が要る、Windows は WebView2 しだい（未確認） | ○ | ○（WebKit の標準） | ○（別の OS の窓は dockview の popout ＋ on_new_window） |

別の OS の窓に出すことは、どちらも macOS で確かめられました。残るのは、筆圧の実機（ペンタブレット）での確認と、
Windows での確認です。これが済むまでは、設計方針どおり画面は Qt のままにします。

この結果から、画面は Tauri で作ることにしました（2026-11-27）。
