"""Tauri の画面（app/）の配布物に入れる app/THIRD_PARTY_LICENSES.md を作る。

配布物に入る第三者のもの：
  - リリースのバイナリに静的にリンクされる Rust の crate（macOS は aarch64-apple-darwin、
    Windows は x86_64-pc-windows-msvc。Windows のインストーラが入れるサムネイルの DLL
    packaging/windows/pman_thumbnail も）。``cargo metadata --filter-platform`` の解決から、
    通常の依存（normal）だけをたどる。build-dependencies と、組むときだけ動く proc-macro の
    crate（とその先）はバイナリに入らないので数えない。
  - app/ui/vendor の JS（dockview-core）。
  - crate の外から入るもの（Rust の標準ライブラリ、WebView2 の loader、SQLite）。

ライセンスの全文は、各 crate の配布物（cargo の registry の src、vendored なら
native/vendor）にある LICENSE*・COPYING*・NOTICE* などをそのまま載せる。同じ全文は1回だけ
載せ、使う crate を並べる。ファイルを同梱していない crate だけ、scripts/license_texts/ の
SPDX の全文に、Cargo.toml の authors（無ければ repository）を著作権者として添える。

使い方（リポジトリのどこからでも）：
    python scripts/generate_third_party_licenses.py           # 書き出す
    python scripts/generate_third_party_licenses.py --check   # 今のファイルと違えば 1 で終わる

cargo が要る（Windows の依存も解決するので、初回は crate を取りに行く）。標準ライブラリだけで動く。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUTPUT = ROOT / "app" / "THIRD_PARTY_LICENSES.md"
TEXTS = Path(__file__).resolve().parent / "license_texts"

MACOS = "macOS"
WINDOWS = "Windows"
THUMBNAIL = "Windows thumbnail DLL"

# (表示名, Cargo.toml, ターゲット)
SCOPES = [
    (MACOS, ROOT / "app" / "Cargo.toml", "aarch64-apple-darwin"),
    (WINDOWS, ROOT / "app" / "Cargo.toml", "x86_64-pc-windows-msvc"),
    (THUMBNAIL, ROOT / "packaging" / "windows" / "pman_thumbnail" / "Cargo.toml",
     "x86_64-pc-windows-msvc"),
]

# このリポジトリの中の crate のうち、第三者のもの（取り込んで手を入れたもの）。
VENDOR_DIR = ROOT / "native" / "vendor"

# 配布物を見て気をつける必要がある（コピーレフトの）ライセンス。
COPYLEFT = re.compile(r"\b(A?GPL|LGPL|MPL|EPL|CDDL|EUPL|OSL|CC-BY-SA)", re.I)
# 知っている（許容型の）ライセンスの識別子。これ以外が出たら知らせる。
KNOWN = {
    "0BSD", "Apache-2.0", "BSD-2-Clause", "BSD-3-Clause", "BSL-1.0", "CC0-1.0",
    "CDLA-Permissive-2.0", "ISC", "MIT", "MIT-0", "MPL-2.0", "Unicode-3.0",
    "Unicode-DFS-2016", "Unlicense", "Zlib",
}

# ライセンスのファイルを同梱していない crate で、全文を選ぶ順。
FALLBACK_ORDER = ["MIT", "Apache-2.0", "BSD-3-Clause", "BSL-1.0", "MPL-2.0"]
# 同梱していない crate の著作権者の表記を、同じリポジトリの姉妹 crate に合わせるもの。
COPYRIGHT_OVERRIDES = {
    # 同じリポジトリ（dropbox/rust-alloc-no-stdlib）の alloc-no-stdlib の LICENSE と同じ。
    "alloc-stdlib": "Copyright (c) 2016 Dropbox, Inc.\nAll rights reserved.",
}

LICENSE_NAME = re.compile(
    r"^(licen[cs]e|copying|copyright|notice|unlicense)([-_.][\w.-]*)?$", re.I)
NOT_TEXT = (".rs", ".toml", ".json", ".py", ".sh", ".html")


@dataclass
class Crate:
    name: str
    version: str
    license: str
    repository: str
    authors: list[str]
    manifest_dir: Path
    vendored: bool
    platforms: set[str] = field(default_factory=set)

    @property
    def key(self) -> tuple[str, str]:
        return (self.name, self.version)

    @property
    def label(self) -> str:
        return f"{self.name} {self.version}"


def cargo_metadata(manifest: Path, target: str) -> dict:
    out = subprocess.run(
        ["cargo", "metadata", "--locked", "--format-version", "1",
         "--filter-platform", target, "--manifest-path", str(manifest)],
        check=True, capture_output=True, text=True,
    )
    return json.loads(out.stdout)


def _is_first_party(pkg: dict) -> bool:
    if pkg.get("source") is not None:
        return False
    path = Path(pkg["manifest_path"]).resolve()
    try:
        path.relative_to(VENDOR_DIR)
        return False
    except ValueError:
        pass
    try:
        path.relative_to(ROOT)
        return True
    except ValueError:
        return False


def linked_packages(metadata: dict) -> list[dict]:
    """ルートから通常の依存だけをたどり、バイナリに入る第三者の package を返す。"""
    packages = {p["id"]: p for p in metadata["packages"]}
    nodes = {n["id"]: n for n in metadata["resolve"]["nodes"]}
    seen: set[str] = set()
    stack = [metadata["resolve"]["root"]]
    while stack:
        pid = stack.pop()
        if pid in seen:
            continue
        pkg = packages[pid]
        if any("proc-macro" in t["kind"] for t in pkg["targets"]):
            continue  # 組むときだけ動く。バイナリには入らない
        seen.add(pid)
        for dep in nodes[pid]["deps"]:
            if any(k["kind"] is None for k in dep["dep_kinds"]):
                stack.append(dep["pkg"])
    return [packages[p] for p in seen if not _is_first_party(packages[p])]


def collect(metadata_for=cargo_metadata) -> list[Crate]:
    crates: dict[tuple[str, str], Crate] = {}
    for platform, manifest, target in SCOPES:
        for pkg in linked_packages(metadata_for(manifest, target)):
            key = (pkg["name"], pkg["version"])
            crate = crates.get(key)
            if crate is None:
                manifest_dir = Path(pkg["manifest_path"]).parent
                crate = crates[key] = Crate(
                    name=pkg["name"], version=pkg["version"],
                    license=pkg.get("license") or "",
                    repository=pkg.get("repository") or pkg.get("homepage") or "",
                    authors=list(pkg.get("authors") or []),
                    manifest_dir=manifest_dir,
                    vendored=pkg.get("source") is None,
                )
                if pkg.get("license_file"):
                    crate.license = crate.license or f"see {pkg['license_file']}"
            crate.platforms.add(platform)
    return sorted(crates.values(), key=lambda c: (c.name, _version_key(c.version)))


def _version_key(version: str) -> tuple:
    return tuple((0, int(x), "") if x.isdigit() else (1, 0, x) for x in re.split(r"[.+-]", version))


def license_ids(expression: str) -> list[str]:
    """``MIT OR Apache-2.0`` や旧式の ``MIT/Apache-2.0`` から識別子を取り出す。"""
    return [t for t in re.split(r"[\s/()]+", expression)
            if t and t.upper() not in ("OR", "AND", "WITH")]


def license_files(crate: Crate) -> list[tuple[str, str]]:
    """crate の配布物にあるライセンスのファイル（名前, 中身）。"""
    found = []
    for entry in sorted(os.listdir(crate.manifest_dir)):
        path = crate.manifest_dir / entry
        if not LICENSE_NAME.match(entry) or entry.lower().endswith(NOT_TEXT):
            continue
        files = sorted(path.iterdir()) if path.is_dir() else [path]
        for f in files:
            if f.is_file():
                rel = f.relative_to(crate.manifest_dir).as_posix()
                found.append((rel, f.read_text(encoding="utf-8", errors="replace")))
    return found


def fallback_text(crate: Crate) -> tuple[str, str]:
    """同梱のファイルが無い crate の全文（SPDX の全文 + 著作権者）。"""
    ids = license_ids(crate.license)
    for spdx in FALLBACK_ORDER:
        if spdx in ids:
            break
    else:
        raise SystemExit(f"{crate.label}: ライセンスのファイルが無く、全文も用意していない"
                         f"（{crate.license}）")
    text = (TEXTS / f"{spdx}.txt").read_text(encoding="utf-8")
    holder = COPYRIGHT_OVERRIDES.get(crate.name)
    if holder is None and spdx in ("MIT", "BSD-3-Clause"):
        who = ", ".join(re.sub(r"\s*<[^>]*>", "", a) for a in crate.authors)
        if not who:
            # 同じリポジトリの crate は同じ表記にする（全文が1つにまとまる）。
            who = (f"the contributors to {crate.repository}" if crate.repository
                   else f"the {crate.name} authors")
        holder = f"Copyright (c) {who}"
    if holder:
        text = f"{holder}\n\n{text}"
    return spdx, text


def _normalize(text: str) -> str:
    return " ".join(text.split())


def _fence(text: str) -> str:
    longest = max((len(m) for m in re.findall(r"`+|~+", text)), default=0)
    mark = "`" * max(3, longest + 1)
    return f"{mark}text\n{text.rstrip()}\n{mark}"


def _anchor(n: int) -> str:
    return f"text-{n}"


def _platforms(crate: Crate) -> str:
    return ", ".join(p for p in (MACOS, WINDOWS, THUMBNAIL) if p in crate.platforms)


def concerns(crates: list[Crate]) -> list[str]:
    notes = []
    for c in crates:
        ids = license_ids(c.license)
        unknown = [i for i in ids if i not in KNOWN]
        if not c.license or unknown:
            notes.append(f"{c.label}: unknown licence `{c.license or '(none)'}`")
        elif COPYLEFT.search(c.license):
            notes.append(f"{c.label}: `{c.license}`")
    return notes


def dockview_version() -> str:
    script = (ROOT / "app" / "fetch-dockview.sh").read_text(encoding="utf-8")
    m = re.search(r"取り込んだ版：([\d.]+)", script)
    return m.group(1) if m else "?"


def render(crates: list[Crate]) -> str:
    # 同じ全文は1回だけ。中身（空白を詰めたもの）で束ねる。
    texts: dict[str, dict] = {}
    per_crate: dict[tuple[str, str], list[str]] = {}
    fallback: set[tuple[str, str]] = set()
    for c in crates:
        files = license_files(c)
        if not files:
            spdx, text = fallback_text(c)
            files = [(f"{spdx} (not shipped in the crate)", text)]
            fallback.add(c.key)
        ids = []
        for name, text in files:
            digest = hashlib.sha256(_normalize(text).encode()).hexdigest()
            entry = texts.setdefault(digest, {"text": text, "users": []})
            entry["users"].append((c.label, name))
            ids.append(digest)
        per_crate[c.key] = ids
    order = {d: i + 1 for i, d in enumerate(texts)}

    out = []
    w = out.append
    w("# Third-Party Licenses (PaintMaskAnimator 0.9.0 and later)")
    w("")
    w("<!-- Generated by scripts/generate_third_party_licenses.py. Do not edit by hand. -->")
    w("")
    w("PaintMaskAnimator itself is licensed under the Apache License 2.0 (see `LICENSE` and")
    w("`NOTICE`). This file lists the third-party components bundled in the Tauri")
    w("application (0.9.0 and later): the macOS `.app` (with its Quick Look extension) and")
    w("the Windows installer. Each component is under its own licence, reproduced below.")
    w("")
    w("The Rust crates are those statically linked into the release binaries, taken from")
    w("`cargo metadata` for `aarch64-apple-darwin` (macOS) and `x86_64-pc-windows-msvc`")
    w("(Windows, including the Explorer thumbnail handler `pman_thumbnail.dll`). Build-only")
    w("crates (build scripts, procedural macros) are not part of the binaries and are not")
    w("listed. Where a crate offers a choice of licences, PaintMaskAnimator may use it")
    w("under any of them; the licence files the crate ships are all reproduced.")
    w("")
    w("## Components that are not Rust crates")
    w("")
    w(f"### dockview-core {dockview_version()} (JavaScript, `ui/vendor/dockview-core.min.js`)")
    w("")
    w("- License: MIT — <https://github.com/mathuo/dockview>")
    w("")
    w(_fence((ROOT / "app" / "ui" / "vendor" / "dockview-LICENCE.md").read_text(encoding="utf-8")))
    w("")
    w("### Rust standard library")
    w("")
    w("- Copyright (c) The Rust Project Developers")
    w("- License: MIT OR Apache-2.0 — <https://github.com/rust-lang/rust>")
    w("- Linked into every Rust binary. The licence texts are the same as the MIT and")
    w("  Apache-2.0 texts reproduced below.")
    w("")
    w("### SQLite (bundled by `libsqlite3-sys`, used to read CLIP STUDIO files)")
    w("")
    w("- Public domain — <https://www.sqlite.org/copyright.html>")
    w("")
    w("### Microsoft Edge WebView2 SDK loader (Windows only)")
    w("")
    w("`webview2-com-sys` statically links `WebView2LoaderStatic.lib` from Microsoft's")
    w("WebView2 SDK (NuGet package `Microsoft.Web.WebView2`) into `PaintMaskAnimator.exe`.")
    w("")
    w(_fence((TEXTS / "WebView2-SDK.txt").read_text(encoding="utf-8")))
    w("")
    w("The Windows installer also carries Microsoft's unmodified WebView2 Runtime")
    w("bootstrapper (`MicrosoftEdgeWebview2Setup.exe`) and runs it only on PCs without the")
    w("WebView2 Runtime. It is extracted to a temporary folder and deleted after setup; it")
    w("and the runtime it installs are under Microsoft's own terms")
    w("(<https://developer.microsoft.com/microsoft-edge/webview2/>).")
    w("")
    w("### Quick Look thumbnail extension (macOS)")
    w("")
    w("`Contents/PlugIns/PmanThumbnail.appex` is PaintMaskAnimator's own code (Apache-2.0)")
    w("built with Swift; it uses only the system's frameworks and Swift runtime, which are")
    w("part of macOS and not bundled.")
    w("")
    w("## Licences that need attention")
    w("")
    noted = concerns(crates)
    if noted:
        w("The crates below are under a copyleft (file-level for MPL-2.0) or unrecognised")
        w("licence. They are used unmodified; the source code of each is available from")
        w("crates.io at the version listed (`https://crates.io/crates/<name>/<version>`) and")
        w("from the repository linked in the table.")
        w("")
        for n in noted:
            w(f"- {n}")
    else:
        w("None: every crate is under a permissive licence.")
    w("")
    w(f"## Rust crates ({len(crates)})")
    w("")
    w("| Crate | Version | License | Used in | Licence text |")
    w("|---|---|---|---|---|")
    for c in crates:
        name = f"[{c.name}]({c.repository})" if c.repository else c.name
        if c.key in fallback:
            name += "¹"
        if c.vendored:
            name += " (vendored, modified: `native/vendor`)"
        refs = " ".join(f"[{order[d]}](#{_anchor(order[d])})"
                        for d in dict.fromkeys(per_crate[c.key]))
        lic = c.license.replace("|", "\\|")
        w(f"| {name} | {c.version} | {lic} | {_platforms(c)} | {refs} |")
    w("")
    if fallback:
        w("Crates marked ¹ do not ship a licence file in their package; their text is")
        w("the standard text of the declared licence with the authors named in `Cargo.toml`.")
        w("")
    w("## Licence texts")
    for digest, entry in texts.items():
        n = order[digest]
        w("")
        w(f'<a id="{_anchor(n)}"></a>')
        w("")
        w(f"### {n}")
        w("")
        users = []
        for label, fname in entry["users"]:
            name, version = label.split(" ", 1)
            mark = "¹" if (name, version) in fallback else ""
            users.append(f"{label}{mark}" if mark else f"{label} (`{fname}`)")
        w("Used by: " + ", ".join(users))
        w("")
        w(_fence(entry["text"]))
    return "\n".join(out) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    parser.add_argument("--check", action="store_true",
                        help="書き出さずに、今のファイルと同じか確かめる")
    args = parser.parse_args(argv)
    crates = collect()
    text = render(crates)
    for n in concerns(crates):
        print(f"note: {n}", file=sys.stderr)
    if args.check:
        current = OUTPUT.read_text(encoding="utf-8").replace("\r\n", "\n") \
            if OUTPUT.exists() else ""
        if current != text:
            print(f"{OUTPUT.relative_to(ROOT)} が古い。"
                  "python scripts/generate_third_party_licenses.py で作り直す", file=sys.stderr)
            return 1
        return 0
    with open(OUTPUT, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)
    print(f"wrote {OUTPUT.relative_to(ROOT)} ({len(crates)} crates)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
