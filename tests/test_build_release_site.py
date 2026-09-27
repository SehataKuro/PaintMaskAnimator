"""Tests for scripts/build_release_site.py (no network)."""
import importlib.util
import json
from pathlib import Path

_SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "build_release_site.py"
_spec = importlib.util.spec_from_file_location("build_release_site", _SCRIPT)
assert _spec is not None and _spec.loader is not None
site = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(site)


def _release(tag, published, **extra):
    return {
        "tag_name": tag,
        "published_at": published,
        "draft": False,
        "prerelease": False,
        "body": "",
        "assets": [
            {"name": f"PaintMaskAnimator-Setup-{tag[1:]}.exe",
             "browser_download_url": f"https://x/{tag}/setup.exe", "size": 52_428_800},
            {"name": f"PaintMaskAnimator-{tag[1:]}-macOS.dmg",
             "browser_download_url": f"https://x/{tag}/app.dmg", "size": 83_886_080},
        ],
        **extra,
    }


def test_load_releases_merges_pages_and_skips_drafts_and_prereleases():
    page1 = [_release("v0.6.4", "2026-08-23T00:00:00Z"), _release("v0.7.0", "2026-10-01T00:00:00Z", draft=True)]
    page2 = [_release("v0.6.5", "2026-09-27T00:00:00Z"), _release("v0.7.0-rc", "2026-09-30T00:00:00Z", prerelease=True)]
    text = json.dumps(page1) + "\n" + json.dumps(page2)

    releases = site.load_releases(text)

    assert [r["tag_name"] for r in releases] == ["v0.6.5", "v0.6.4"]


def test_pick_asset_prefers_setup_exe():
    release = {"assets": [
        {"name": "PaintMaskAnimator.exe", "browser_download_url": "https://x/app.exe"},
        {"name": "PaintMaskAnimator-Setup-1.0.exe", "browser_download_url": "https://x/setup.exe"},
    ]}
    assert site.pick_asset(release, ".exe")["browser_download_url"] == "https://x/setup.exe"
    assert site.pick_asset(release, ".dmg") is None


def test_render_notes_joins_wrapped_bullets_and_escapes_html():
    notes = (
        "### 追加\n"
        "\n"
        "- **書き出しを追加した。** ファイル › 書き出し… から\n"
        "  `.xdts` を <選べる>。\n"
        "- [リンク](https://example.com) の文字だけ残す\n"
    )

    rendered = site.render_notes(notes)

    assert "<h4>追加</h4>" in rendered
    assert (
        "<li><strong>書き出しを追加した。</strong> ファイル › 書き出し… から"
        "<code>.xdts</code> を &lt;選べる&gt;。</li>"
    ) in rendered
    assert "<li>リンク の文字だけ残す</li>" in rendered
    assert rendered.count("<ul>") == 1


def test_render_page_fills_latest_downloads_and_history():
    template = "{{ status }}|{{ buttons }}|{{ history }}|{{ repo_url }}"
    releases = [_release("v0.6.5", "2026-09-27T01:00:00Z", body="- 修正"), _release("v0.6.4", "2026-08-23T00:00:00Z")]

    page = site.render_page(template, releases)

    assert "最新版 v0.6.5（2026-09-27）" in page
    assert 'data-platform="windows" href="https://x/v0.6.5/setup.exe"' in page
    assert 'data-platform="macos" href="https://x/v0.6.5/app.dmg"' in page
    assert "50 MB" in page
    assert page.count('class="release"') == 2
    assert page.count("最新</span>") == 1
    assert "<li>修正</li>" in page
    assert site.REPO_URL in page


def test_render_versions_lists_every_release_with_its_installers():
    releases = [_release("v0.6.5", "2026-09-27T01:00:00Z"), _release("v0.6.4", "2026-08-23T00:00:00Z")]
    releases[1]["assets"] = releases[1]["assets"][:1]  # Windows only

    rows = site.render_versions(releases)

    assert rows.count("<tr>") == 2
    assert 'href="https://x/v0.6.4/setup.exe" aria-label="v0.6.4 Windows 版をダウンロード">ダウンロード</a>' in rows
    assert '<td class="none">—</td>' in rows
    assert '<a href="history.html#v0.6.4">変更内容</a>' in rows
    assert rows.count("最新</span>") == 1


def test_render_page_without_releases():
    page = site.render_page("{{ status }}{{ buttons }}{{ history }}", [])
    assert "まだリリースが公開されていません。" in page


def test_build_writes_site(tmp_path):
    out = tmp_path / "_site"
    site.build(json.dumps([_release("v0.6.5", "2026-09-27T00:00:00Z")]), out)

    assert (out / "logo.png").exists()
    assert (out / "style.css").exists()
    assert (out / "logo-anim.js").exists()
    assert (out / "scroll.js").exists()
    assert (out / ".nojekyll").exists()
    for name in ("index.html", "history.html", "help.html", "versions.html"):
        html = (out / name).read_text(encoding="utf-8")
        assert "{{" not in html, name
    index = (out / "index.html").read_text(encoding="utf-8")
    assert "v0.6.5" in index
    # The platform script must not be swallowed by a preceding // comment.
    assert "\n      (function () {" in index
    assert 'class="release"' in (out / "history.html").read_text(encoding="utf-8")
    assert "<tr>" in (out / "versions.html").read_text(encoding="utf-8")
    assert 'href="versions.html"' in index
    assert (out / "images" / "screenshot.png").exists()
    assert not (out / "_partials").exists()


def test_render_page_fills_partials_that_use_values():
    partials = {"footer": '<a href="{{ repo_url }}">GitHub</a>'}
    page = site.render_page("<main></main>{{ footer }}", [], partials)
    assert page == f'<main></main><a href="{site.REPO_URL}">GitHub</a>'


def test_build_versions_stylesheet_and_script_urls(tmp_path):
    out = tmp_path / "_site"
    site.build(json.dumps([_release("v0.6.5", "2026-09-27T00:00:00Z")]), out)

    index = (out / "index.html").read_text(encoding="utf-8")
    help_page = (out / "help.html").read_text(encoding="utf-8")
    for page in (index, help_page):
        assert 'href="style.css?v=' in page
        assert 'src="scroll.js?v=' in page
        assert 'href="style.css"' not in page
    assert 'src="logo-anim.js?v=' in index


def test_version_assets_changes_with_the_file(tmp_path):
    (tmp_path / "style.css").write_text("a{}", encoding="utf-8")
    first = site.version_assets('<link href="style.css">', tmp_path)
    (tmp_path / "style.css").write_text("b{}", encoding="utf-8")
    second = site.version_assets('<link href="style.css">', tmp_path)
    assert first != second
