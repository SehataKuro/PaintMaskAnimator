<p align="center">
  <img src="docs/assets/logo.png" alt="PaintMaskAnimator (PMA)" width="180">
</p>

# PaintMaskAnimator

[English](README.en.md) | 日本語

**[公式サイト](https://sehatakuro.github.io/PaintMaskAnimator/)** — ダウンロード・更新履歴・過去のバージョン・ヘルプ

[![CI](https://github.com/SehataKuro/PaintMaskAnimator/actions/workflows/ci.yml/badge.svg)](https://github.com/SehataKuro/PaintMaskAnimator/actions/workflows/ci.yml)
[![Release](https://img.shields.io/badge/release-v0.6.5-blue)](https://github.com/SehataKuro/PaintMaskAnimator/releases)
[![Python](https://img.shields.io/badge/python-3.10%20%7C%203.11%20%7C%203.12-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![PySide6](https://img.shields.io/badge/PySide6-6.5%2B-41CD52?logo=qt&logoColor=white)](https://doc.qt.io/qtforpython/)
[![Platform](https://img.shields.io/badge/platform-Windows%20%7C%20macOS-lightgrey)](https://github.com/SehataKuro/PaintMaskAnimator/releases)
[![License](https://img.shields.io/badge/license-Apache--2.0-green)](LICENSE)

アニメーションの仕上げ（彩色）のためのペイント / マスクアニメーションツールです。
略称は **PMAn**（ピーマン）。

## ダウンロード

[**Releases**](https://github.com/SehataKuro/PaintMaskAnimator/releases) から最新版を
ダウンロードしてください。

| OS | ファイル | 対応 |
| --- | --- | --- |
| Windows | `PaintMaskAnimator-Setup-<版>.exe` | 64 ビット版 Windows |
| macOS | `PaintMaskAnimator-<版>-macOS.dmg` | Apple Silicon（M1 以降）。Intel Mac は非対応 |

インストール後は、アプリ内の更新確認から新しい版を取得できます。
0.6.4 以前を使っている場合はアプリ内の更新が届かないため、一度だけ Releases から
入れ直してください。

### macOS で開けないとき

Apple の公証を受けていないため、初回だけ macOS が起動を止めます。

1. アプリを「アプリケーション」フォルダへドラッグし、一度開いて警告を閉じます。
2. **システム設定 › プライバシーとセキュリティ** の下の方にある
   「"PaintMaskAnimator" は…ブロックされました」の横の **このまま開く** を押します。

ターミナルからは次のコマンドでも解除できます。

```bash
xattr -dr com.apple.quarantine /Applications/PaintMaskAnimator.app
```

## 主な機能

- フレームとレイヤーによるペイント・マスクアニメーションの編集、タイムライン、
  オニオンスキン、トゥイーン
- 使用色パネル：色を親子付け・タグ・フォルダーで整理し、置換・統合・減色ができる
- カラーチャート（`.pmag`）で色の親子関係を管理し、現在の画像へ適用
- 読み込み：画像・連番、PSD、CLIP STUDIO のアニメーション（`.clip`）、
  タイムシート（XDTS / TDTS）、タイムリマップの貼り付け
- 書き出し：PNG / TGA 連番＋CSV、PSD、MP4、XDTS、カットフォルダー
  （セル画像とタイムシートをまとめて書き出し）
- 自動保存とクラッシュからの復元、パネル配置のカスタマイズ、ライト / ダークテーマ
- 表示言語：日本語・英語（**表示 › 言語 / Language**。既定は OS の言語）
- Python スクリプトによるアクションの追加（下記）

プロジェクトファイル（`.pman`）の形式は [`FORMAT.md`](FORMAT.md) にまとめています。

## ソースから実行する

Python 3.10 以上が必要です。

```bash
pip install -r requirements.txt
python PaintMaskAnimator.py        # または python -m paintmaskanimator
```

必須の依存は PySide6、PySide6-QtAds、numpy です。Pillow と psd-tools を入れると、
PSD などの読み込み・書き出しが有効になります。

## Python アクション

アクションパネルには、Python スクリプトでボタンを追加できます。タブ左端の
**☰ › スクリプトを編集** でアプリ内エディタが開きます。

```python
def register_actions(panel, window):
    panel.add_action(
        "my.unique.action",
        "My Action",
        lambda: window.status_bar.showMessage("Done", 3000),
        tooltip="Optional help text",
    )
```

API リファレンスと組み込みアクションの一覧は [`docs/actions.md`](docs/actions.md)、
サンプルは [`examples/actions/hello_status.py`](examples/actions/hello_status.py) に
あります。アクションは通常の Python コードとして実行されるので、信頼できる提供元の
ものだけを入れてください。

## 開発に参加する

開発環境の準備、提出前のチェック、翻訳、リリース手順は
[`CONTRIBUTING.md`](CONTRIBUTING.md) にまとめています。

```bash
pip install -e ".[dev,full]"
python scripts/preflight.py
```

コードは [`paintmaskanimator/`](paintmaskanimator/) 以下の Python パッケージです。
リポジトリ直下の `PaintMaskAnimator.py` は起動用の薄いランチャーです。

| モジュール | 役割 |
| --- | --- |
| `document.py` | プロジェクト状態モデル（フレーム / レイヤー / カーソル） |
| `imaging.py`、`geometry.py`、`colors.py`、`color_ops.py` | Qt に依存しないアルゴリズム |
| `project_io.py` | プロジェクトファイルの保存・読み込み |
| `canvas.py`、`timeline.py`、`toolpanel.py`、`main_window.py` など | UI（PySide6 ウィジェット） |
| `main_window_<topic>.py` | 機能ごとのコントローラ（`window.export` など） |
| `i18n.py` | 翻訳の読み込みと `tr()` |

## プロジェクトの由来

PaintMaskAnimator は、[小嶋慶祐](https://x.com/kkeisuke220)が v0.5 まで単独で
開発しました。v0.5 以降は、小嶋慶祐と上甲愛士の 2 名で共同開発しています。

当初から一貫している狙いは次のとおりです。

- アニメーション仕上げソフトの新しい選択肢
- 動画作業にも対応できる描画能力
- CLIP STUDIO PAINT のような操作感を持つタイムライン

## ライセンス

[Apache License 2.0](LICENSE) のもとで配布しています。帰属表示は
[`NOTICE`](NOTICE) を参照してください。

- 商用・非商用を問わず、利用・改変・再配布ができます。改変版をクローズドソースの
  製品として配布することもできます。求められるのは、ライセンス表記と `NOTICE` の
  保持、変更点の明示です（第4条）。
- 本ソフトウェアで作った作品（画像・アニメーション・プロジェクトファイル）は
  あなたのものです。ライセンスは作品には及びません。
- 商標の使用は許諾していません（第6条）。「PaintMaskAnimator」と「PMAn」は、
  派生物の名称には使わないでください。
- PySide6（LGPLv3）や Qt Advanced Docking System（LGPL-2.1）などを同梱しています。
  配布物やパッケージングを変える場合は、
  [`THIRD_PARTY_LICENSES.md`](THIRD_PARTY_LICENSES.md) の「LGPL compliance」節を
  確認してください。
- 貢献は Apache License 2.0 の条件で提供されたものとみなします（第5条）。
  CLA は不要です。
