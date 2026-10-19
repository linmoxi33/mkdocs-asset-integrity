"""Interoperability with plugins people commonly install alongside this one.

The interesting failures in this space are never in isolated builds -- they
show up when a second plugin also wants to own the same asset files.
"""

from __future__ import annotations

import base64
import hashlib
import re

import pytest

pytest.importorskip("mkdocs")
from mkdocs.commands.build import build
from mkdocs.config import load_config

pytestmark = pytest.mark.filterwarnings("ignore")


def verify_sri(path, sri: str) -> bool:
    algo, expected = sri.split("-", 1)
    h = hashlib.new(algo)
    h.update(path.read_bytes())
    return base64.b64encode(h.digest()).decode() == expected


def pairs(html: str):
    """Integrity pairs, tolerating unquoted URLs produced by minifiers."""
    return [
        (m.group(1) or m.group(2), m.group(3))
        for m in re.finditer(
            r'(?:href|src)=(?:"([^"]+)"|([^\s>]+))[^>]*?integrity="([^"]+)"', html
        )
    ]


def make_site(root, config_yaml: str, files: dict[str, str]):
    docs = root / "docs"
    docs.mkdir(parents=True, exist_ok=True)
    for rel, content in files.items():
        p = docs / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
    cfg_path = root / "mkdocs.yml"
    cfg_path.write_text(config_yaml, encoding="utf-8")
    cfg = load_config(config_file=str(cfg_path))
    cfg.site_dir = str(root / "out")
    cfg.docs_dir = str(docs)
    build(cfg)
    return root / "out"


def test_minify_theme_compat(tmp_path):
    """minify's HTML pass strips attribute quotes; SRI must still be intact."""
    pytest.importorskip("mkdocs_minify_plugin")
    out = make_site(
        tmp_path,
        """
        site_name: Combo
        theme:
          name: material
        plugins:
          - search
          - minify:
              minify_html: true
          - asset-integrity
        nav:
          - Home: index.md
        """,
        {"index.md": "# Hi\n\nSome text.\n"},
    )
    html = (out / "index.html").read_text(encoding="utf-8")
    got = pairs(html)
    assert got, "integrity attributes vanished when minify_html ran"
    for url, sri in got:
        assert verify_sri(out / url, sri), f"SRI mismatch for {url}"


def test_minify_owned_files_get_clear_warning(tmp_path, caplog):
    """When the minifier owns a file we cannot fingerprint it -- say so.

    mkdocs-minify-plugin rewrites extra_css to `*.min.css` during on_files and
    only creates that file at post_build, i.e. after we have locked names.
    The correct behaviour is to keep the original name, skip the integrity
    attribute (we never saw the bytes), and tell the user exactly why.
    """
    pytest.importorskip("mkdocs_minify_plugin")
    with caplog.at_level("WARNING"):
        out = make_site(
            tmp_path,
            """
            site_name: Combo
            theme:
              name: material
            plugins:
              - search
              - minify:
                  minify_css: true
                  css_files: [assets/site.css]
              - asset-integrity
            extra_css: [assets/site.css]
            nav:
              - Home: index.md
            """,
            {"index.md": "# Hi\n", "assets/site.css": "body {\n  color: #123;\n}\n"},
        )
    # The page must still reference a file that exists.
    html = (out / "index.html").read_text(encoding="utf-8")
    urls = [m.group(1) or m.group(2) for m in re.finditer(r'href=(?:"([^"]+\.css)"|([^\s>]+\.css))', html)]
    assert urls
    for u in urls:
        assert (out / u).is_file(), f"{u} referenced but missing"
    assert any("mkdocs-minify-plugin" in r.message for r in caplog.records), (
        "the minifier conflict must be reported, not silently swallowed"
    )


def test_priority_runs_before_default_plugins():
    """Our post_page handler must sort ahead of priority-0 plugins."""
    from mkdocs_asset_integrity import AssetIntegrityPlugin

    assert AssetIntegrityPlugin.mkdocs_priority > 0


def test_no_plugins_but_search(tmp_path):
    """The plugin must not depend on Material being installed."""
    out = make_site(
        tmp_path,
        """
        site_name: Plain
        theme:
          name: mkdocs
        plugins:
          - asset-integrity
        extra_css: [css/extra.css]
        extra_javascript: [js/extra.js]
        nav:
          - Home: index.md
        """,
        {
            "index.md": "# Hi\n",
            "css/extra.css": "body{color:#333}\n",
            "js/extra.js": "console.log(1)\n",
        },
    )
    html = (out / "index.html").read_text(encoding="utf-8")
    assert re.search(r"extra\.[0-9a-f]{8}\.css", html)
    assert re.search(r"extra\.[0-9a-f]{8}\.js", html)
    # Only local references are ours to check; the mkdocs theme legitimately
    # points at a CDN, and external URLs must never be rewritten.
    local = [
        u
        for u in re.findall(r'(?:href|src)="([^"]+\.(?:css|js))"', html)
        if not u.startswith(("http://", "https://", "//"))
    ]
    assert local
    for url in local:
        assert (out / url).is_file(), f"{url} referenced but missing"


def test_custom_dir_asset(tmp_path):
    """Assets placed in theme.custom_dir must still be handled."""
    root = tmp_path
    custom = root / "overrides"
    (custom / "assets").mkdir(parents=True, exist_ok=True)
    (custom / "assets" / "override.css").write_text("body{margin:0}\n", encoding="utf-8")
    out = make_site(
        root,
        """
        site_name: Override
        theme:
          name: material
          custom_dir: overrides
        plugins:
          - asset-integrity
        extra_css: [assets/override.css]
        nav:
          - Home: index.md
        """,
        {"index.md": "# Hi\n"},
    )
    html = (out / "index.html").read_text(encoding="utf-8")
    for url, sri in pairs(html):
        assert verify_sri(out / url, sri)
