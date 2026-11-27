#!/bin/sh
# dockview-core（MIT）の配布物を npm から取り、ui/ に置く。試作で使った版に固定する。
set -eu
cd "$(dirname "$0")"
tmp=$(mktemp -d)
(cd "$tmp" && npm pack dockview-core@8.9.1 --silent >/dev/null && tar xzf dockview-core-8.9.1.tgz)
cp "$tmp/package/dist/dockview-core.min.js" ui/
cp "$tmp/package/LICENCE.md" ui/dockview-LICENCE.md
rm -rf "$tmp"
