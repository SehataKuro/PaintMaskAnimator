"""Build the static release site (GitHub Pages) from the repository's releases.

The input is the JSON printed by ``gh api repos/OWNER/REPO/releases --paginate``
(one or more concatenated arrays). Drafts and pre-releases are skipped. The
page is rendered once at build time, so visitors never hit the GitHub API and
its unauthenticated rate limit.

    python scripts/build_release_site.py --releases releases.json --output _site
"""
from __future__ import annotations

import argparse
import html
import json
import re
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SITE_DIR = ROOT / "site"
LOGO = ROOT / "docs" / "assets" / "logo.png"
REPO_URL = "https://github.com/SehataKuro/PaintMaskAnimator"

# Same selection rule as paintmaskanimator.updater.pick_installer_asset.
PLATFORMS = (
    ("windows", ".exe", "Windows", "Windows 10 / 11"),
    ("macos", ".dmg", "macOS", "macOS（Apple Silicon）"),
)


def load_releases(text: str) -> list[dict]:
    """Parse ``gh api --paginate`` output: one or more concatenated JSON arrays."""
    decoder = json.JSONDecoder()
    releases: list[dict] = []
    index = 0
    text = text.strip()
    while index < len(text):
        page, index = decoder.raw_decode(text, index)
        releases.extend(page)
        while index < len(text) and text[index].isspace():
            index += 1
    published = [
        r for r in releases
        if isinstance(r, dict) and not r.get("draft") and not r.get("prerelease")
    ]
    return sorted(published, key=lambda r: r.get("published_at") or "", reverse=True)


def pick_asset(release: dict, suffix: str) -> dict | None:
    candidates = [
        a for a in release.get("assets") or []
        if str(a.get("name", "")).lower().endswith(suffix) and a.get("browser_download_url")
    ]
    if not candidates:
        return None
    return next((a for a in candidates if "setup" in a["name"].lower()), candidates[0])


def format_size(size: int | None) -> str:
    if not size:
        return ""
    return f"{size / (1024 * 1024):.0f} MB"


def _inline(text: str) -> str:
    """Escape *text*, then apply the inline Markdown the changelog uses."""
    text = html.escape(text, quote=False)
    text = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", text)
    text = re.sub(r"`([^`]+)`", r"<code>\1</code>", text)
    text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)
    return text


def render_notes(markdown: str) -> str:
    """Render the CHANGELOG subset used in release notes to HTML.

    Supports ``###`` headings, ``- `` bullets whose text wraps onto indented
    continuation lines, and plain paragraphs. Everything is HTML-escaped.
    """
    blocks: list[tuple[str, str]] = []  # (kind, text); kind: h, li, p
    for raw in (markdown or "").splitlines():
        line = raw.rstrip()
        stripped = line.strip()
        if not stripped:
            blocks.append(("gap", ""))
        elif re.match(r"^#{2,4}\s+", stripped):
            blocks.append(("h", re.sub(r"^#{2,4}\s+", "", stripped)))
        elif stripped.startswith("- "):
            blocks.append(("li", stripped[2:]))
        elif blocks and blocks[-1][0] in ("li", "p") and line[:1].isspace():
            kind, text = blocks[-1]
            blocks[-1] = (kind, text + stripped)
        elif blocks and blocks[-1][0] == "p":
            blocks[-1] = ("p", blocks[-1][1] + stripped)
        else:
            blocks.append(("p", stripped))

    out: list[str] = []
    in_list = False
    for kind, text in blocks:
        if kind != "li" and in_list:
            out.append("</ul>")
            in_list = False
        if kind == "h":
            out.append(f"<h4>{_inline(text)}</h4>")
        elif kind == "li":
            if not in_list:
                out.append("<ul>")
                in_list = True
            out.append(f"<li>{_inline(text)}</li>")
        elif kind == "p":
            out.append(f"<p>{_inline(text)}</p>")
    if in_list:
        out.append("</ul>")
    return "\n".join(out)


def _date(release: dict) -> str:
    return (release.get("published_at") or "")[:10]


def render_buttons(release: dict) -> str:
    """Large download buttons, one per platform the release has an installer for."""
    buttons = []
    for key, suffix, short, label in PLATFORMS:
        asset = pick_asset(release, suffix)
        if asset is None:
            continue
        meta = " · ".join(p for p in (label, format_size(asset.get("size"))) if p)
        buttons.append(
            f'<a class="button" data-platform="{key}" '
            f'href="{html.escape(asset["browser_download_url"])}">'
            f'<span class="button-label">{short} 版をダウンロード</span>'
            f'<span class="button-meta">{html.escape(meta)}</span></a>'
        )
    return "\n".join(buttons)


def render_history(releases: list[dict]) -> str:
    items = []
    for i, release in enumerate(releases):
        tag = html.escape(release.get("tag_name", ""))
        links = []
        for _key, suffix, short, _label in PLATFORMS:
            asset = pick_asset(release, suffix)
            if asset:
                links.append(
                    f'<a href="{html.escape(asset["browser_download_url"])}">{short}</a>'
                )
        notes = render_notes(release.get("body") or "") or "<p>変更内容の記載はありません。</p>"
        badge = '<span class="badge">最新</span>' if i == 0 else ""
        items.append(
            f'<article class="release" id="{tag}">'
            f'<div class="release-meta"><h2>{tag}{badge}</h2>'
            f"<time>{_date(release)}</time>"
            f'<p class="release-links">{" ".join(links)}</p></div>'
            f'<div class="release-notes">{notes}</div>'
            f"</article>"
        )
    return "\n".join(items)


def render_versions(releases: list[dict]) -> str:
    """Table rows for the version list: one row per release, newest first."""
    rows = []
    for i, release in enumerate(releases):
        tag = html.escape(release.get("tag_name", ""))
        cells = []
        for _key, suffix, short, _label in PLATFORMS:
            asset = pick_asset(release, suffix)
            if asset:
                size = format_size(asset.get("size"))
                cells.append(
                    f'<td><a href="{html.escape(asset["browser_download_url"])}" '
                    f'aria-label="{tag} {short} 版をダウンロード">ダウンロード</a>'
                    f'<span class="size">{size}</span></td>'
                )
            else:
                cells.append('<td class="none">—</td>')
        badge = '<span class="badge">最新</span>' if i == 0 else ""
        rows.append(
            f"<tr><th scope=\"row\">{tag}{badge}</th>"
            f"<td><time>{_date(release)}</time></td>"
            f"{''.join(cells)}"
            f'<td><a href="history.html#{tag}">変更内容</a></td></tr>'
        )
    return "\n".join(rows)


def render_page(template: str, releases: list[dict], partials: dict[str, str] | None = None) -> str:
    if releases:
        latest = releases[0]
        version = html.escape(latest.get("tag_name", ""))
        date = _date(latest)
        status = f"最新版 {version}（{date}）"
        buttons = render_buttons(latest)
        history = render_history(releases)
        versions = render_versions(releases)
    else:
        version = date = ""
        status = "まだリリースが公開されていません。"
        buttons = ""
        history = "<p>まだありません。</p>"
        versions = ""
    values = dict(partials or {})
    values.update({
        "status": status,
        "version": version,
        "date": date,
        "buttons": buttons,
        "history": history,
        "versions": versions,
        "repo_url": REPO_URL,
    })
    # Partials may themselves use the other values, so substitute them first.
    for name in partials or {}:
        template = template.replace("{{ " + name + " }}", values[name])
    for name, value in values.items():
        template = template.replace("{{ " + name + " }}", value)
    return template


def build(releases_json: str, output: Path) -> None:
    releases = load_releases(releases_json)
    if output.exists():
        shutil.rmtree(output)
    shutil.copytree(SITE_DIR, output, ignore=shutil.ignore_patterns("*.html", "_partials"))
    shutil.copy2(LOGO, output / "logo.png")
    partials = {
        path.stem: path.read_text(encoding="utf-8")
        for path in (SITE_DIR / "_partials").glob("*.html")
    }
    for template_path in SITE_DIR.glob("*.html"):
        template = template_path.read_text(encoding="utf-8")
        page = render_page(template, releases, partials)
        (output / template_path.name).write_text(page, encoding="utf-8")
    # GitHub Pages would otherwise run Jekyll over the output.
    (output / ".nojekyll").write_text("", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--releases", required=True, type=Path,
                        help="JSON from `gh api repos/OWNER/REPO/releases --paginate`")
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    build(args.releases.read_text(encoding="utf-8"), args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
