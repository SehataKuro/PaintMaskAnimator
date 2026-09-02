# 貢献の手引き

PaintMaskAnimator への貢献を歓迎します。

## ライセンスについて

本プロジェクトは **Apache License 2.0** で公開されています
（[`LICENSE`](LICENSE)）。

本プロジェクトへ提出された貢献は、**Apache License 2.0 の条件で提供された
ものとみなします**（inbound = outbound）。これは Apache License 2.0 第5条に
定められている扱いで、別途の同意書（CLA）は求めていません。

著作権はあなたに帰属したままです。Apache License 2.0 はサブライセンスを
許可しているため、貢献を受けたあとも本プロジェクトを任意の条件で提供でき、
そのために追加の手続きは不要です。

## 開発環境

```bash
python -m venv .venv
.venv/Scripts/activate
pip install -e ".[dev,full]"
```

## 提出前の確認

```bash
ruff check paintmaskanimator
pyright
python scripts/update_translations.py --check
pytest
```

## MainWindow に機能を足すとき

機能は `main_window_<topic>.py` の **コントローラ**として書き、`MainWindow.__init__`
で `self.<name> = XxxController(self)` として持たせます。コントローラは
コンストラクタでウィンドウを受け取り、必要なものは `self.window.canvas` のように
辿ります。

```python
class TweenController:
    def __init__(self, window: "MainWindow"):
        self.window = window

    def enable(self, visual_row, key_column):
        self.window.canvas.push_doc_undo()
```

**ミックスインを新しく足さないでください。** 以前は18個のミックスインが1つの
`MainWindow` に合成されており、無関係な機能が同じ `self` を共有していました。
兄弟同士が同じメソッド名を静かに奪い合え、どの属性をどれが提供しているのかも
型チェッカーからは見えませんでした。

例外は、Qt がウィンドウ自身に対して呼ぶもの（`eventFilter` などのオーバーライド）と、
ウィンドウのウィジェットを組み立てるものだけです。`UIBuildMixin` /
`InputMixin` / `DockingMixin` がこれに当たり、ミックスインのまま残しています。

ダイアログやウィジェットが親を辿って機能を探す（`getattr(self.parent(), "...")`）
書き方は避けてください。この結合はどのツールからも見えず、所有側を移動・改名すると
例外にならず静かに壊れます。必要なものは引数で渡します。

## UI文字列と翻訳

UIに表示される文字列は `tr()` で囲みます。原文は日本語のままなので、囲むだけでは
表示は一切変わりません（未翻訳の `tr()` は原文をそのまま返します）。

```python
from .i18n import tr

QMessageBox.critical(self, tr("PSD書き出し"), tr("PSDを書き出せませんでした。"))
```

補間は **翻訳したあとに、名前付きで** 行ってください。訳文では語順が変わるため、
f-string で先に埋め込むと翻訳できなくなります。

```python
tr("{count}個のキーフレームを書き出しました。").format(count=exported)
```

囲んではいけないもの: ログメッセージ、設定キー、ファイル形式の識別子、
`QObject.setObjectName()` に渡す名前（ドックの `objectName` は保存された
ワークスペース配置の識別子なので、翻訳すると既存のレイアウトが壊れます）。

文字列を追加・変更したら、カタログを再生成してコミットしてください。CIは
`--check` で鮮度を検証します。

```bash
python scripts/update_translations.py
```

## パッケージングを変更する場合

配布物には LGPL の PySide6 / Qt Advanced Docking System を同梱しています。
**one-folder ビルドをやめる、Qt を静的リンクする、改ざん検知を入れる、といった
変更は LGPL 違反になります。** 制約の詳細は
[`THIRD_PARTY_LICENSES.md`](THIRD_PARTY_LICENSES.md) の「LGPL compliance」節を
参照してください。

## プルリクエスト

- 1つのプルリクエストは1つの目的に絞ってください。
- 挙動を変更する場合はテストを添えてください。
- ユーザーから見える変更は [`CHANGELOG.md`](CHANGELOG.md) の「未リリース」節に
  追記してください。
- コミットメッセージ・コメント・ドキュメントは日本語で構いません。
