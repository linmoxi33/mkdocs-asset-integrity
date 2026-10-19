"""End-to-end tests: build a real MkDocs site and inspect the output.

These are the tests that actually matter. Unit tests can prove the regexes
work; only a real build proves the plugin hooks fire in the right order and
that the files on disk match the URLs in the HTML.
"""

from __future__ import annotations

import base64
import hashlib
import re
import textwrap
from pathlib import Path

import pytest

mkdocs = pytest.importorskip("mkdocs")
from mkdocs.commands.build import build
from mkdocs.config import load_config


def write(path: Path, content: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(content), encoding="utf-8")
    return path


@pytest.fixture
def site(tmp_path):
    """Create a minimal MkDocs project and return a builder callable."""

    def _build(config_yaml: str, files: dict[str, str], name: str = "site"):
        root = tmp_path / name
        docs = root / "docs"
        docs.mkdir(parents=True, exist_ok=True)
        for rel, content in files.items():
            write(docs / rel, content)
        cfg_path = write(root / "mkdocs.yml", config_yaml)
        cfg = load_config(config_file=str(cfg_path))
        cfg.site_dir = str(root / "out")
        cfg.docs_dir = str(docs)
        build(cfg)
        return root / "out"

    return _build


BASE_CONFIG = """
site_name: Test
theme:
  name: mkdocs
plugins:
  - asset-integrity
nav:
  - Home: index.md
"""

MATERIAL_CONFIG = """
site_name: Test
theme:
  name: material
plugins:
  - search
  - asset-integrity
extra_css: [assets/custom.css]
extra_javascript: [assets/app.js]
nav:
  - Home: index.md
"""


def asset_urls(html: str) -> list[str]:
    return re.findall(r'(?:href|src)="([^"]+\.(?:css|js))"', html)


def integrity_pairs(html: str) -> list[tuple[str, str]]:
    return re.findall(
        r'(?:href|src)="([^"]+\.(?:css|js))"[^>]*?integrity="([^"]+)"', html
    )


def verify_sri(path: Path, sri: str) -> bool:
    algo, expected = sri.split("-", 1)
    h = hashlib.new(algo)
    h.update(path.read_bytes())
    return base64.b64encode(h.digest()).decode() == expected


class TestFingerprinting:
    def test_extra_css_is_fingerprinted(self, site):
        out = site(
            MATERIAL_CONFIG,
            {
                "index.md": "# Hi\n",
                "assets/custom.css": "body{color:red}\n",
                "assets/app.js": "console.log(1)\n",
            },
        )
        html = (out / "index.html").read_text(encoding="utf-8")
        assert re.search(r"custom\.[0-9a-f]{8}\.css", html), "custom.css was not hashed"
        assert re.search(r"app\.[0-9a-f]{8}\.js", html), "app.js was not hashed"

    def test_hashed_files_exist_on_disk(self, site):
        out = site(
            MATERIAL_CONFIG,
            {
                "index.md": "# Hi\n",
                "assets/custom.css": "body{color:red}\n",
                "assets/app.js": "console.log(1)\n",
            },
        )
        html = (out / "index.html").read_text(encoding="utf-8")
        for url in asset_urls(html):
            assert (out / url).is_file(), f"{url} referenced but missing on disk"

    def test_original_name_is_gone(self, site):
        out = site(
            MATERIAL_CONFIG,
            {
                "index.md": "# Hi\n",
                "assets/custom.css": "body{color:red}\n",
                "assets/app.js": "console.log(1)\n",
            },
        )
        assert not (out / "assets/custom.css").exists()
        assert not (out / "assets/app.js").exists()

    def test_content_change_changes_hash(self, site, tmp_path):
        files = {
            "index.md": "# Hi\n",
            "assets/custom.css": "body{color:red}\n",
            "assets/app.js": "console.log(1)\n",
        }
        out1 = site(MATERIAL_CONFIG, files, name="a")
        h1 = re.search(r"custom\.([0-9a-f]{8})\.css", (out1 / "index.html").read_text(encoding="utf-8"))

        files2 = dict(files)
        files2["assets/custom.css"] = "body{color:blue}\n"
        out2 = site(MATERIAL_CONFIG, files2, name="b")
        h2 = re.search(r"custom\.([0-9a-f]{8})\.css", (out2 / "index.html").read_text(encoding="utf-8"))

        assert h1 and h2 and h1.group(1) != h2.group(1)

    def test_same_content_same_hash_across_builds(self, site):
        files = {
            "index.md": "# Hi\n",
            "assets/custom.css": "body{color:red}\n",
            "assets/app.js": "x\n",
        }
        a = site(MATERIAL_CONFIG, files, name="a")
        b = site(MATERIAL_CONFIG, files, name="b")
        ha = re.search(r"custom\.([0-9a-f]{8})\.css", (a / "index.html").read_text(encoding="utf-8"))
        hb = re.search(r"custom\.([0-9a-f]{8})\.css", (b / "index.html").read_text(encoding="utf-8"))
        assert ha and hb and ha.group(1) == hb.group(1)

    def test_nested_page_uses_correct_relative_path(self, site):
        cfg = MATERIAL_CONFIG.replace(
            "nav:\n  - Home: index.md", "nav:\n  - Home: index.md\n  - Deep: guide/deep/index.md"
        )
        out = site(
            cfg,
            {
                "index.md": "# Hi\n",
                "guide/deep/index.md": "# Deep\n",
                "assets/custom.css": "body{color:red}\n",
                "assets/app.js": "x\n",
            },
        )
        html = (out / "guide/deep/index.html").read_text(encoding="utf-8")
        assert re.search(r'\.\./\.\./assets/custom\.[0-9a-f]{8}\.css', html)

    def test_cache_busting_can_be_disabled(self, site):
        cfg = MATERIAL_CONFIG.replace(
            "  - asset-integrity", "  - asset-integrity:\n      cache_busting: false"
        )
        out = site(
            cfg,
            {
                "index.md": "# Hi\n",
                "assets/custom.css": "body{color:red}\n",
                "assets/app.js": "x\n",
            },
        )
        html = (out / "index.html").read_text(encoding="utf-8")
        assert "assets/custom.css" in html
        assert (out / "assets/custom.css").is_file()


class TestSubresourceIntegrity:
    def test_integrity_present_and_correct(self, site):
        out = site(
            MATERIAL_CONFIG,
            {
                "index.md": "# Hi\n",
                "assets/custom.css": "body{color:red}\n",
                "assets/app.js": "console.log(1)\n",
            },
        )
        html = (out / "index.html").read_text(encoding="utf-8")
        pairs = integrity_pairs(html)
        assert pairs, "no integrity attributes were emitted"
        for url, sri in pairs:
            assert verify_sri(out / url, sri), f"wrong SRI for {url}"

    def test_hashed_assets_also_get_integrity(self, site):
        # Regression: the SRI table is keyed by the original path while the
        # HTML references the hashed path. Renamed assets must not lose SRI.
        out = site(
            MATERIAL_CONFIG,
            {
                "index.md": "# Hi\n",
                "assets/custom.css": "body{color:red}\n",
                "assets/app.js": "x\n",
            },
        )
        html = (out / "index.html").read_text(encoding="utf-8")
        for url, _ in integrity_pairs(html):
            if "custom." in url or "app." in url:
                return
        pytest.fail("fingerprinted assets lost their integrity attribute")

    def test_crossorigin_added(self, site):
        out = site(
            MATERIAL_CONFIG,
            {"index.md": "# Hi\n", "assets/custom.css": "a{}\n", "assets/app.js": "x\n"},
        )
        html = (out / "index.html").read_text(encoding="utf-8")
        assert 'crossorigin="anonymous"' in html

    def test_sha256_algorithm(self, site):
        cfg = MATERIAL_CONFIG.replace(
            "  - asset-integrity", "  - asset-integrity:\n      algorithm: sha256"
        )
        out = site(
            cfg, {"index.md": "# Hi\n", "assets/custom.css": "a{}\n", "assets/app.js": "x\n"}
        )
        html = (out / "index.html").read_text(encoding="utf-8")
        assert "sha256-" in html
        for url, sri in integrity_pairs(html):
            assert verify_sri(out / url, sri)

    def test_sri_can_be_disabled_independently(self, site):
        cfg = MATERIAL_CONFIG.replace(
            "  - asset-integrity",
            "  - asset-integrity:\n      subresource_integrity: false",
        )
        out = site(
            cfg, {"index.md": "# Hi\n", "assets/custom.css": "a{}\n", "assets/app.js": "x\n"}
        )
        html = (out / "index.html").read_text(encoding="utf-8")
        assert "integrity=" not in html
        # Fingerprinting still happened.
        assert re.search(r"custom\.[0-9a-f]{8}\.css", html)

    def test_external_urls_never_get_integrity(self, site):
        cfg = MATERIAL_CONFIG.replace(
            "extra_javascript: [assets/app.js]",
            "extra_javascript:\n  - assets/app.js\n  - https://cdn.example.com/x.js",
        )
        out = site(
            cfg, {"index.md": "# Hi\n", "assets/custom.css": "a{}\n", "assets/app.js": "x\n"}
        )
        html = (out / "index.html").read_text(encoding="utf-8")
        assert "https://cdn.example.com/x.js" in html
        m = re.search(r'<script[^>]*cdn\.example\.com[^>]*>', html)
        assert m and "integrity=" not in m.group(0)


class TestSafety:
    def test_theme_assets_not_renamed_by_default(self, site):
        out = site(MATERIAL_CONFIG, {"index.md": "# Hi\n", "assets/custom.css": "a{}\n", "assets/app.js": "x\n"})
        # The mkdocs default theme ships its own CSS; it must keep its name.
        theme_css = list((out / "css").glob("*.css")) if (out / "css").is_dir() else []
        for p in theme_css:
            assert not re.search(r"\.[0-9a-f]{8}\.css$", p.name), f"{p.name} was renamed"

    def test_existing_hash_not_double_hashed(self, site):
        cfg = MATERIAL_CONFIG.replace(
            "extra_css: [assets/custom.css]", "extra_css: [assets/custom.1234abcd.css]"
        )
        out = site(
            cfg,
            {
                "index.md": "# Hi\n",
                "assets/custom.1234abcd.css": "a{}\n",
                "assets/app.js": "x\n",
            },
        )
        html = (out / "index.html").read_text(encoding="utf-8")
        assert "custom.1234abcd.css" in html
        assert not re.search(r"custom\.1234abcd\.[0-9a-f]{8}\.css", html)

    def test_exclude_pattern_respected(self, site):
        cfg = MATERIAL_CONFIG.replace(
            "  - asset-integrity", "  - asset-integrity:\n      exclude: ['assets/custom.css']"
        )
        out = site(
            cfg, {"index.md": "# Hi\n", "assets/custom.css": "a{}\n", "assets/app.js": "x\n"}
        )
        html = (out / "index.html").read_text(encoding="utf-8")
        assert "assets/custom.css" in html
        assert (out / "assets/custom.css").is_file()

    def test_import_chain_is_repaired(self, site):
        cfg = MATERIAL_CONFIG.replace(
            "extra_css: [assets/custom.css]",
            "extra_css: [assets/main.css, assets/base.css]",
        )
        out = site(
            cfg,
            {
                "index.md": "# Hi\n",
                "assets/main.css": '@import url("base.css");\nbody{color:red}\n',
                "assets/base.css": "h1{font-size:2em}\n",
                "assets/app.js": "x\n",
            },
        )
        main = next((out / "assets").glob("main.*.css"))
        css = main.read_text(encoding="utf-8")
        imported = re.search(r'@import url\("([^"]+)"\)', css).group(1)
        assert (out / "assets" / imported).is_file(), f"@import points at missing {imported}"

    def test_importing_stylesheet_keeps_valid_integrity(self, site):
        """Regression: rewriting @import after hashing broke every SRI check.

        The browser refuses a stylesheet whose bytes do not match its
        integrity attribute. If we hash first and patch the @import afterwards,
        the served bytes differ from the hashed bytes and the whole cascade is
        blocked. Verified in a real browser: console showed
        "Failed to find a valid digest ... The resource has been blocked".
        """
        cfg = MATERIAL_CONFIG.replace(
            "extra_css: [assets/custom.css]",
            "extra_css: [assets/main.css, assets/base.css]",
        )
        out = site(
            cfg,
            {
                "index.md": "# Hi\n",
                "assets/main.css": '@import url("base.css");\nbody{color:red}\n',
                "assets/base.css": "h1{font-size:2em}\n",
                "assets/app.js": "x\n",
            },
        )
        html = (out / "index.html").read_text(encoding="utf-8")
        pairs = integrity_pairs(html)
        assert pairs
        for url, sri in pairs:
            assert verify_sri(out / url, sri), (
                f"integrity for {url} does not match the bytes on disk; "
                "the browser would block this asset"
            )

    def test_import_chain_three_deep(self, site):
        cfg = MATERIAL_CONFIG.replace(
            "extra_css: [assets/custom.css]",
            "extra_css: [assets/a.css, assets/b.css, assets/c.css]",
        )
        out = site(
            cfg,
            {
                "index.md": "# Hi\n",
                "assets/a.css": '@import url("b.css");\na{}\n',
                "assets/b.css": '@import url("c.css");\nb{}\n',
                "assets/c.css": "c{}\n",
                "assets/app.js": "x\n",
            },
        )
        html = (out / "index.html").read_text(encoding="utf-8")
        for url, sri in integrity_pairs(html):
            assert verify_sri(out / url, sri), f"SRI broken for {url}"
        # And every import target must exist.
        for css_file in (out / "assets").glob("*.css"):
            for imp in re.findall(r'@import url\("([^"]+)"\)', css_file.read_text(encoding="utf-8")):
                assert (out / "assets" / imp).is_file(), f"{css_file.name} -> missing {imp}"

    def test_circular_import_does_not_hang_or_crash(self, site):
        cfg = MATERIAL_CONFIG.replace(
            "extra_css: [assets/custom.css]",
            "extra_css: [assets/a.css, assets/b.css]",
        )
        out = site(
            cfg,
            {
                "index.md": "# Hi\n",
                "assets/a.css": '@import url("b.css");\na{}\n',
                "assets/b.css": '@import url("a.css");\nb{}\n',
                "assets/app.js": "x\n",
            },
        )
        assert (out / "index.html").is_file()

    def test_missing_declared_asset_warns_but_builds(self, site):
        cfg = MATERIAL_CONFIG.replace(
            "extra_css: [assets/custom.css]", "extra_css: [assets/nope.css]"
        )
        out = site(cfg, {"index.md": "# Hi\n", "assets/app.js": "x\n"})
        assert (out / "index.html").is_file()

    def test_disabled_plugin_is_inert(self, site):
        cfg = MATERIAL_CONFIG.replace(
            "  - asset-integrity", "  - asset-integrity:\n      enabled: false"
        )
        out = site(
            cfg, {"index.md": "# Hi\n", "assets/custom.css": "a{}\n", "assets/app.js": "x\n"}
        )
        html = (out / "index.html").read_text(encoding="utf-8")
        assert "assets/custom.css" in html
        assert "integrity=" not in html

    def test_build_is_deterministic(self, site):
        files = {"index.md": "# Hi\n", "assets/custom.css": "a{}\n", "assets/app.js": "x\n"}
        a = site(MATERIAL_CONFIG, files, name="a")
        b = site(MATERIAL_CONFIG, files, name="b")
        ha = (a / "index.html").read_text(encoding="utf-8")
        hb = (b / "index.html").read_text(encoding="utf-8")
        # Only absolute paths differ, if anything; the asset markup must match.
        assert integrity_pairs(ha) == integrity_pairs(hb)


class TestConfigValidation:
    def test_short_hash_length_is_corrected(self, site):
        cfg = MATERIAL_CONFIG.replace(
            "  - asset-integrity", "  - asset-integrity:\n      hash_length: 2"
        )
        out = site(
            cfg, {"index.md": "# Hi\n", "assets/custom.css": "a{}\n", "assets/app.js": "x\n"}
        )
        html = (out / "index.html").read_text(encoding="utf-8")
        # Falls back to 8 chars rather than emitting a 2-char fingerprint.
        assert re.search(r"custom\.[0-9a-f]{8}\.css", html)

    def test_invalid_algorithm_rejected_at_config_load(self, tmp_path, caplog):
        import logging

        root = tmp_path / "bad"
        write(root / "docs/index.md", "# x\n")
        cfg_path = write(
            root / "mkdocs.yml",
            """
            site_name: Bad
            plugins:
              - asset-integrity:
                  algorithm: md5
            """,
        )
        from mkdocs.exceptions import Abort

        with caplog.at_level(logging.ERROR, logger="mkdocs.config"), \
                pytest.raises(Abort):
            load_config(config_file=str(cfg_path))

        # The failure must name the option and the legal values, not just abort.
        messages = " ".join(r.getMessage() for r in caplog.records)
        assert "algorithm" in messages
        assert "sha384" in messages
