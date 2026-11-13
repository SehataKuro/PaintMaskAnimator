# format_version 2 golden

全4件は format_version=2 / mask_format=2。外部素材を含まない4×3の人工画像で、
リポジトリのMITライセンスに従います。v1/mask1移行は親ディレクトリの固定素材と既存試験で扱います。

| ファイル | Aの生成元ケースと追加条件 |
|---|---|
| basic-bank-sheet | archive-conflict の画像規約。初出3、未配置1/9、離れた3、隣接○開始、EMPTY、initial/name、半透明、残存12とlocal画像 |
| pending-compat | pending-duplicate / pending-none / placement-key-boundary を3 Trackに統合。位置0に有効guard付き入力尺8を追加し、保存文書7位置/尺8。UI配置でguardが失効し、4位置/尺4。None・重複・hint・resolve差分・旧キー境界 |
| paper-draft | numbered-paper-draft に用紙7を追加。用紙3/7と○/EMPTY、下書き8/9、白、opacity/filter |
| tween-initial-display | tween-initial の固定v1を移行。tween/member、initial/name、色、onion、silhouette、playback_rangeを追加 |

期待JSONは `expected/<stem>.json`。writerのJSONやPNG出力から作らず、保存前の文書を
`tests/v2_golden_support.py`で直接観測します。Aの固定v1素材と同じ人工画像・import規則を使い、移行元のbank/initial/name/hashは
Aの既存expected/read_installとも照合します。
新しい組合せはsource_snapshotに明記します。UI期待はv2 readerを通さず、sourceのcloneを
LoadedProjectとしてUIへ渡し採取します。生成されたZIPの低水準読込とUI openの両方が
独立期待に一致した場合だけ固定ファイルを書きます。既存Aの期待JSONは変更しません。

`low` はbank/initial/name、全位置/sheet/blank開始/tween/resolve/span、Track表示、
hint、pending、未配置番号、durations、出力行/尺、旧構造投影の有効scalarと有効入力尺（有効なimportがなければ投影長を既定にする）。
描画にも○にもpendingにも使われない旧投影の番号は意味を持たないためnullへ正規化します。
過去のhidden画像は比較せず、live local画像は全位置で比較します。
低水準の合成はsilhouetteを無効に固定。`ui` は自動配置後の同観測にUI設定・履歴・dirtyを含みます。

画像は `Format_RGBA8888_Premultiplied` へ変換し、上から下・左から右のRGBA各4 bytesを
SHA-256にします。行paddingは除き、sizeも記録。bank/local/initialと各位置の合成
(`composite(i, white=False)`)を比較します。PNG圧縮やARGBのCPU byte順に依存しません。
ZIP日時は2000-01-01 00:00:00固定。受入れ比較は意味と画素で、ZIP bytes一致ではありません。

通常pytestは再生成しません。リポジトリ直下で明示的に実行します。

```sh
QT_QPA_PLATFORM=offscreen .venv/bin/python tests/fixtures/projects/v2/regenerate.py --update
```

`--update`なしは引数エラーで書込みません。必要なら指定のlibEGLディレクトリを
LD_LIBRARY_PATHへ追加します。再生成の差分は必ずレビューしてください。
`tests/test_v2_golden.py`は固定JSONとの低水準/UI照合と、それぞれ2周のopen→save→open（3世代のopen）を行います。
