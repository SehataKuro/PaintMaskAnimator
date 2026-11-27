#!/bin/sh
# dockview-core（MIT）の配布物を npm から取り直し、ui/vendor/ に置く。版を上げるときだけ使う。
# 取り込んだ版：8.9.1（sha256 5f72344284be160db830a00c26abeb6e865ef29325c0abb9fa4743c9a564c2ad）
set -eu
cd "$(dirname "$0")"
version=${1:-8.9.1}
tmp=$(mktemp -d)
(cd "$tmp" && npm pack "dockview-core@$version" --silent >/dev/null && tar xzf "dockview-core-$version.tgz")
cp "$tmp/package/dist/dockview-core.min.js" ui/vendor/
cp "$tmp/package/LICENCE.md" ui/vendor/dockview-LICENCE.md
rm -rf "$tmp"
shasum -a 256 ui/vendor/dockview-core.min.js
