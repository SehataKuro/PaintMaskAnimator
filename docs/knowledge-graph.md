# 構造グラフの更新

公式サイトの [構造グラフ](https://sehatakuro.github.io/PaintMaskAnimator/understand/) は、
[Understand-Anything](https://github.com/Egonex-AI/Understand-Anything) が作った
`.ua/knowledge-graph.json` をそのまま表示しています。グラフは CI では作りません。
要約やレイヤー分けを LLM が書くため、CI で回すと API キーが要るからです。
誰か 1 人が手元の Claude Code で作り直してコミットし、`main` に push すると
`pages.yml` がサイトを置き換えます。

## いつ更新するか

モジュールを足した・消した・分けたなど、構造が変わったときです。
中身の修正だけなら更新しなくてもかまいません。
グラフは `.ua/meta.json` に記録したコミットからの差分だけを解析し直すので、
間が空いても困りませんが、空くほど 1 回の解析は長くなります。

## 更新の手順

1. **通常のチェックアウトの `main` で Claude Code を起動する。**
   worktree の中で実行すると、プラグインが出力先をメインのチェックアウトに
   切り替えるので、最初からそちらで実行します。

   ```powershell
   cd D:\Documents\GitHub\PaintMaskAnimator
   git switch main
   git pull
   claude
   ```

2. **`/understand` を実行する。**

   ```
   /understand
   ```

   言語は初回に `.ua/config.json` へ `ja` が保存されているので、`--language ja` は
   付けなくても日本語になります。前回からの差分を調べ、構造が変わっていなければ
   LLM を使わずに終わります。

3. **`.ua/` をコミットして push する。**

   ```powershell
   git add .ua
   git commit -m "chore(understand): 構造グラフを更新"
   git push
   ```

   作業用の `.ua/intermediate/`・`.ua/tmp/`・`.ua/diff-overlay.json` は
   `.gitignore` で除いてあるので、`git add .ua` で問題ありません。
   `.ua/.understandignore`（解析から外すファイルの指定）はコミットします。

4. **公開を確かめる。** push すると Actions の Release site が動き、数分で
   [構造グラフ](https://sehatakuro.github.io/PaintMaskAnimator/understand/) が新しくなります。

## 全部作り直すとき

差分の積み重ねでおかしくなったときや、`.understandignore` を大きく変えたときは
全ファイルを解析し直します。125 モジュールすべてを読むので時間がかかります。

```
/understand --full
```

## プラグインを更新したとき

サイトのダッシュボードは、グラフを書いたプラグインと同じ版のものを使う必要があります。
プラグインを新しくしたら、入っている版のコミットを調べて、
[`pages.yml`](../.github/workflows/pages.yml) の `Check out the Understand-Anything dashboard`
の `ref` をそれに合わせてください。

```powershell
git -C "$env:USERPROFILE\.claude\plugins\marketplaces\understand-anything" rev-parse HEAD
```

いまは 2.9.8（`4e6d3299017a7432da15957874992837a315c5b4`）に合わせてあります。

## 初めての環境で準備する

新しいパソコンや、ほかの人が更新するときだけ必要です。

1. **Claude Code（ターミナル版）を入れる。** デスクトップアプリとは別物です。

   ```powershell
   irm https://claude.ai/install.ps1 | iex
   ```

2. **`claude` が見つからないと言われたら PATH に足す。** 足したあとはターミナルを
   開き直さないと反映されません。

   ```powershell
   [Environment]::SetEnvironmentVariable("Path", [Environment]::GetEnvironmentVariable("Path", "User") + ";$env:USERPROFILE\.local\bin", "User")
   ```

3. **`claude` の中でプラグインを入れる。** 1 行ずつ、終わるのを待ってから次を入力します。
   まとめて貼り付けると 1 つのコマンドとして読まれて失敗します。

   ```
   /plugin marketplace add Egonex-AI/Understand-Anything
   ```

   ```
   /plugin install understand-anything
   ```

プラグインはユーザー単位で入るので、一度入れればどのリポジトリでも使えます。
