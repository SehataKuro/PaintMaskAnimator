# MCP サーバー

PaintMaskAnimator を **MCP（Model Context Protocol）サーバー**として起動すると、
Claude Desktop や Claude Code などの AI クライアントから `.pma` プロジェクトを
読み、コマを画像として受け取り、線で閉じた領域に色を置けます。

**PMAn 側に AI は入っていません。** 推論はクライアント側にあるので、PMAn は
API キーもネットワーク接続も持ちません。ユーザーが Claude Desktop などを
サブスクリプションで使っていれば、そのまま動きます。

## 何のためにあるか

- **キャラデザ・設定画との突き合わせ**：`render_frame` でコマを画像として渡し、
  設定画と比べてパーツ抜けを指摘してもらう。
- **カラーモデルからの色の割り当て**：`list_regions` で閉領域を、
  `get_color_chart` で色と役割タグを取り、どの領域がどの色かを判断してもらって
  `apply_region_colors` で置く。

**2コマ目以降はここの仕事ではありません。** フレーム間の領域の対応付けは
アプリ側の[串刺し塗り](#串刺し塗りとの分担)が担当します。MCP 経由で毎コマ数百
領域の座標をやりとりするのは、速度・安定性・コストのどれを見ても割に合いません。

## 導入

```bash
pip install -e ".[mcp]"
```

`mcp` は任意依存です。入れなくてもアプリ本体の動作には影響しません。

## 起動

```bash
# 読み取り専用（既定）
python -m paintmaskanimator.mcp path/to/project.pma

# 書き込みを許可する
python -m paintmaskanimator.mcp --allow-write path/to/project.pma

# プロジェクトを指定せず、open_project ツールで開く
python -m paintmaskanimator.mcp
```

GUI は起動しません（`QT_QPA_PLATFORM=offscreen` の最小限の Qt だけを使います）。

## Claude Desktop への登録

設定ファイルの `mcpServers` に追加します。

```json
{
  "mcpServers": {
    "paintmaskanimator": {
      "command": "python",
      "args": ["-m", "paintmaskanimator.mcp", "/path/to/project.pma"]
    }
  }
}
```

書き込みを許可する場合は `args` に `"--allow-write"` を加えます。

## 公開しているツール

| ツール | 種別 | 内容 |
|---|---|---|
| `open_project` | 読み | `.pma` を開いて構成の概要を返す |
| `describe_project` | 読み | キャンバスサイズ・コマ数・レイヤー構成・尺の合計 |
| `list_frames` | 読み | コマごとの尺と、レイヤーの可視・不透明度・露出・セル名・保持セルの解決先 |
| `get_color_chart` | 読み | 色チャート（色・親子関係・役割タグ）。`.pmag` と同じ形 |
| `render_frame` | 読み | コマを合成した PNG 画像 |
| `list_regions` | 読み | 線で閉じた領域の一覧（種座標・面積・外接矩形・色） |
| `apply_region_colors` | **書き** | 領域に色を置く |
| `save_project` | **書き** | 保存する |

### `list_regions` と `apply_region_colors` の関係

`list_regions` が返す各領域の `seed` は、**その座標をクリックしたらバケツが塗る
範囲**を指します。領域の定義はバケツの「隣接」塗りとまったく同じ判定
（`bucket_fill.compute_fill_region`）を使っているので、一覧と実際の塗りが
食い違うことがありません。`seed` はそのまま `apply_region_colors` に渡せます。

```json
{
  "assignments": [
    {"frame": 0, "layer": 0, "seed": [120, 340], "color": "#F2D3B4"},
    {"frame": 0, "layer": 0, "seed": [156, 388], "color": "#D8A98A"}
  ]
}
```

`color` が1件でも解釈できなければ、**何も書かずに**失敗します（途中まで塗られた
状態を残さないため）。塗れなかった領域は例外にせず `skipped` として理由付きで
返します。

## 安全側の既定

AI がプロジェクトを壊さないよう、既定を安全側に寄せています。

- **起動時は読み取り専用。** `--allow-write` を付けたときだけ書き込み系が動きます。
  読み取り専用のまま書き込みツールを呼ぶと、例外ではなく理由を返します。
- **保存は別名保存が既定。** `save_project` にパスを渡さない場合は
  `<元の名前>_mcp.pma` へ書き出し、開いたファイルは上書きしません。
  元のファイルへ書きたいときは同じパスを明示します。

## 制限

- **合成はドキュメントの状態まで。** `render_frame` はレイヤーの可視・不透明度・
  保持セルの解決を反映しますが、色フィルタやマスク表示といった *画面の見え方* の
  設定は反映しません。あれはビューの状態で、GUI を起動していないここには存在しない
  ためです。
- **起動中の GUI には繋がりません。** 操作対象はファイルであり、アプリで編集中の
  プロジェクトではありません。編集中の内容に対して効かせるには、ローカル IPC と
  `main_window` 側のコマンド受け口が別途必要です（未実装）。
- **配布版バイナリからは起動できません。** Python 環境が必要です。
- **描画はできません。** ブラシや選択・変形といった描画系のロジックは
  `canvas_*.py`（QWidget 側）にあり、UI から切り離されていません。ここで公開して
  いるのはプロジェクト構造と領域単位の塗りまでです。

## 串刺し塗りとの分担

自動色塗りは3段階に分かれ、AI が担うのは最初の1つだけです。

| 段階 | 担当 |
|---|---|
| 領域抽出 | `imaging.scanline_connected_region`（アプリ本体） |
| **1コマ目の役割判定** | **AI（このサーバー経由）** |
| 2コマ目以降 | 串刺し塗り（ツールパネルの「串刺し塗り」） |
| 取りこぼしの検出 | `list_regions` で未塗り領域（`painted: false`）を数える |

串刺し塗りは動きの大きいコマや、領域が分裂・統合するところでは外れます。
「串刺しで大半を塗り、残った未塗り領域だけ拾う」という進め方を想定しています。

## 構成

- `paintmaskanimator/mcp/session.py` — プロトコル非依存の中身。`mcp` パッケージが
  なくても import でき、GUI なしで単体テストできる（`tests/test_mcp_session.py`）。
- `paintmaskanimator/mcp/server.py` — ツール定義。`mcp` が必要
  （`tests/test_mcp_server.py`、未導入ならスキップ）。
- `paintmaskanimator/mcp/__main__.py` — 起動口。
