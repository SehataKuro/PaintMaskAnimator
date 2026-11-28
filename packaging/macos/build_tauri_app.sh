#!/bin/sh
# Tauri の画面（app/）の配布物を macOS で作る。0.8.0（PyInstaller）と同じ形にする：
#   - PaintMaskAnimator.app（Apple Silicon。.pman の関連付けは app/Info.plist）
#   - Quick Look のサムネイル拡張を入れ、アドホック署名（公証なし。0.8.0 と同じ）
#   - PaintMaskAnimator-<版>-macOS.dmg（dmgbuild。窓の配置と「開けないときは.txt」は
#     dmg_settings.py。アプリの更新はこの名前の .dmg を探す）
#
# リポジトリの一番上で：
#   packaging/macos/build_tauri_app.sh
# 必要なもの：Rust、Node（npx で tauri-cli を使う）、Xcode のコマンドラインツール、
# dmgbuild（pip install dmgbuild）。CARGO_TARGET_DIR を決めていればそこに組む。
set -eu

# .github/workflows/release.yml の tauri-windows と同じ版にする。
TAURI_CLI_VERSION=${TAURI_CLI_VERSION:-2.12.6}

version=$(sed -n 's/^version = "\(.*\)"$/\1/p' app/Cargo.toml | head -n 1)
[ -n "$version" ] || { echo "app/Cargo.toml に version が無い" >&2; exit 1; }

# 第三者のライセンス表記（app/THIRD_PARTY_LICENSES.md。tauri.conf.json の resources で
# Contents/Resources に入る）が依存の今と合っているか。古ければ作り直してから組む。
python3 scripts/generate_third_party_licenses.py --check

(cd app && npx --yes "@tauri-apps/cli@$TAURI_CLI_VERSION" build --bundles app)

target_dir=${CARGO_TARGET_DIR:-app/target}
app="$target_dir/release/bundle/macos/PaintMaskAnimator.app"
[ -d "$app" ] || { echo "$app ができていない" >&2; exit 1; }

# 0.8.0 と同じ拡張（中で外側の .app もアドホックで署名し直す）。
python3 packaging/macos/build_thumbnail_extension.py "$app" "$version"

dmg="PaintMaskAnimator-$version-macOS.dmg"
rm -f "$dmg"
dmgbuild -s packaging/macos/dmg_settings.py -D app="$app" "PaintMaskAnimator" "$dmg"
echo "built $app"
echo "built $dmg"
