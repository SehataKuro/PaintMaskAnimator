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
pytest
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
