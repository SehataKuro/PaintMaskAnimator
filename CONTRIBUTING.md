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

CIと同じチェック（ruff・pyright・バージョン表記・翻訳カタログ・テスト）を
まとめて実行します。

```bash
python scripts/preflight.py          # テストを含むすべて
python scripts/preflight.py --quick  # テスト以外（数秒）
```

push のたびに自動で実行するには、クローンごとに一度だけ次を実行します。
通常の push では `--quick` を、リリースタグの push ではテストとリリース用の
確認を含むすべてを実行します。

```bash
git config core.hooksPath .githooks
```

テストは設定フォルダーを一時フォルダーへ隔離し、想定外のモーダルダイアログが
開いたら待たずに失敗します（ルートの `conftest.py`）。ダイアログを開くことが
仕様のテストでは、`QMessageBox.question` などを monkeypatch してください。

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

**表示テキストで分岐しないでください。** `combo.currentText() == "多角形"` のような
比較は、翻訳した瞬間に例外も出さず静かに成立しなくなります。値と表示は分けます。

```python
self.shape_type.addItem(tr("多角形"), "polygon")   # 表示 / 値
...
if self.shape_type.currentData() == "polygon":
```

**モジュールやクラスの定義位置で `tr()` を呼ばないでください。** そこはインポート時
に評価されるため、翻訳の読み込みより前に走って原文が焼き付きます。関数にして
呼び出し時に翻訳します（`theme.accent_presets()`、`toolpanel.tool_label()`、
`undo_entries.translate_history_label()` が実例です）。引数のデフォルト値も同じ
理由で不可で、ruff の `B008` が検出します。

文字列を追加・変更したら、カタログを再生成してコミットしてください。CIは
`--check` で鮮度を検証します。

```bash
python scripts/update_translations.py
```

## リリース

1. `CHANGELOG.md` の「未リリース」節に、前回のリリース以降のユーザーから
   見える変更がすべて載っていることを確認します。リリースノートはこの節から
   作られます。

   ```bash
   git log --oneline --no-merges $(git describe --tags --abbrev=0)..HEAD
   ```

2. バージョン表記（`pyproject.toml`・README のバッジ・`CHANGELOG.md` の見出しと
   リンク）をまとめて更新します。

   ```bash
   python scripts/bump_version.py 0.6.5
   ```

3. リリース用の確認を通してからコミットし、タグを push します。
   リリースワークフローも同じ確認を最初に行います。

   ```bash
   python scripts/preflight.py --release 0.6.5
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
