# E2Eシナリオテスト

実際のアプリと同じ経路で `MainWindow` を起動し、`QTest` で実マウス／キーイベントを
ウィジェットに注入して「ユーザーの一連の作業」を通すテスト置き場。
単体テストが個々の関数を守るのに対し、ここは *画面から手を動かして完了するまで* を守る。

## 実行

```bash
python scripts/run_tests.py tests/e2e -q
```

CI は `tests/` 全体を回すので、追加したシナリオは自動的にCIの対象になる。
`-m e2e` でシナリオだけを選ぶこともできる（`-m "not e2e"` で除外）。
実行は `QT_QPA_PLATFORM=offscreen`（`conftest.py` が設定）のヘッドレスで、
実ウィンドウは開かない。

## 書き方の約束

- テスト本文は `app`（`AppDriver`）のメソッドだけで書く。
  ウィジェットの内部APIや座標変換を直接触るのは `driver.py` の中だけにする。
  そうすることで、UIの実装が変わってもシナリオ本文は書き換えずに済む。
- 検証は次の3軸のどれかに寄せる。
  - **ドキュメント状態**: `app.pixel(...)`、`app.frame_count`、`app.undo_depth()`
  - **エクスポート成果物**: 書き出したファイルを実際に開いて確かめる
  - **UI状態**: `app.messages`、`app.status_message()`、アクションの活性状態
- 新しい操作が必要になったら `AppDriver` にメソッドを1つ足す。

## いま通しているシナリオ

| ファイル | 通している流れ |
| --- | --- |
| `test_draw_and_save_scenario.py` | 描く → 名前を付けて保存 → 新規作成 → 開き直す |
| `test_animation_export_scenario.py` | 2コマ描く → 連番PNG＋CSV書き出し |
| `test_undo_scenario.py` | 描く → 元に戻す → やり直す |
| `test_selection_transform_scenario.py` | 矩形選択 → 自由変形で移動 → 確定／取り消し |
| `test_used_color_scenario.py` | 2色で描く → 使用色パネルに出る → 表示フィルター |
| `test_tween_scenario.py` | 露出3コマのキー → トゥイーン有効化 → 変形確定 |
| `test_time_remap_scenario.py` | 連番読み込み → AE Time Remap 適用でコマ順入れ替え |
| `test_pressure_scenario.py` | 筆圧ペンで描く（線幅が筆圧で変わる） |

## モーダルダイアログの扱い

ヘッドレスE2Eが固まる原因はほぼモーダルダイアログなので、`conftest.py` で一括制御している。

- `QMessageBox` の静的呼び出し（`information` / `warning` / `critical` / `question`）は
  記録して既定応答を返す。内容は `app.messages` で検証できる。
- `QDialog.exec()` は既定で `AssertionError` にする。開くことが仕様のダイアログは
  `AppDriver.accept_dialog(DialogClass)` で明示的に許可する。
  つまり **想定外の確認ダイアログが出た時点でテストは失敗する** ——
  これは待ち時間ではなく不具合として扱う。
- ファイルダイアログは `AppDriver` 側で固定応答に差し替える
  （`save_project_as` / `open_project` / `export_key_sequence`）。
- 確認ダイアログの応答は、呼び出し側が提示したボタン群から肯定側（Ok / Yes）を選ぶ。
  Ok/Cancel と Yes/No が混在しているため、固定応答だとシナリオが黙って中断する。

## タブレット入力

`QTest` はタブレットイベントを合成できないので、`AppDriver.tablet_stroke()` が
`QTabletEvent` を直接組み立てて `canvas.tabletEvent()` へ渡す。筆圧に依存する
挙動（線幅など）はこちらで書く。
