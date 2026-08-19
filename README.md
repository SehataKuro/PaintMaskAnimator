# PaintMaskAnimator

[English](README.en.md) | 日本語

PySide6 で作られたペイント / マスクアニメーションツールです。

## 関連リンク

- [ダウンロードページ](https://jokomanato.com/paintmaskanimator/downloads/)
- [機能検証マップ（各コミットのテスト結果から自動更新）](https://jokomanato.com/paintmaskanimator/)

ページの閲覧には Basic 認証が必要です。

- ユーザー名: `guest`
- パスワード: `6eCKEq`

## プロジェクトの由来と開発思想

このリポジトリは、[小嶋慶祐さん](https://x.com/kkeisuke220)から譲り受けた
PaintMaskAnimator v0.5 をもとに開発しているフォークです。オリジナル版の作者は
小嶋慶祐さんであり、本リポジトリの現メンテナーがゼロから作成したものではありません。

オリジナル版は、次のような開発思想で制作されていたと伺っています。

- PaintMan に代わり得るアニメーション仕上げソフト
- 動画作業にも対応できる描画能力
- CLIP STUDIO PAINT のような操作感を持つタイムライン

本フォークでも、この方向性を受け継ぎながら開発を続けています。

## 必要環境

- Python 3.10 以上
- PySide6、PySide6-QtAds、numpy（必須）／Pillow、psd-tools（任意。追加の読み込み・書き出し機能が有効になります）

```bash
pip install -r requirements.txt
```

## 実行

```bash
python PaintMaskAnimator.py
```

またはモジュールとして：

```bash
python -m paintmaskanimator
```

## 開発

```bash
pip install -r requirements-dev.txt
QT_QPA_PLATFORM=offscreen python scripts/run_tests.py -q
```

テストはヘッドレス（オフスクリーン Qt）で実行されます。CI は push のたびに実行されます。

## プロジェクト構成

アプリケーションは [`paintmaskanimator/`](paintmaskanimator/) 以下の Python パッケージです：

| モジュール | 役割 |
| --- | --- |
| `document.py` | プロジェクト状態モデル（フレーム / レイヤー / カーソル、スナップショット） |
| `imaging.py`、`geometry.py`、`colors.py`、`color_ops.py` | Qt に依存しない純粋なアルゴリズム |
| `project_io.py` | プロジェクト（`.zip`）の保存 / 読み込みシリアライズ |
| `canvas.py`、`timeline.py`、`toolpanel.py`、`main_window.py` など | UI レイヤー（PySide6 ウィジェット） |

リポジトリ直下の `PaintMaskAnimator.py` は薄いランチャーです。

## Python アクション

アクションパネルは、Python スクリプトからカスタムボタンを追加できます。アクション
パネルのタブ左端の **☰ → スクリプトを編集** でアプリ内エディタが開き、スクリプトの作成・
編集・再読み込みが行えます。各スクリプトは次の関数を公開します：

```python
def register_actions(panel, window):
    panel.add_action(
        "my.unique.action",
        "My Action",
        lambda: window.status_bar.showMessage("Done", 3000),
        tooltip="Optional help text",
    )
```

書き方・API リファレンス・組み込みアクションの一覧・トラブルシューティングは
[`docs/actions.md`](docs/actions.md) にまとめています。サンプルは
[`examples/actions/hello_status.py`](examples/actions/hello_status.py) を参照して
ください。アクションスクリプトは通常の Python コードなので、信頼できる提供元から
のみインストールしてください。
