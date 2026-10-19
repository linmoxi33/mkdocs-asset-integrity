"""Unit tests for the pure helpers in `core`."""

from __future__ import annotations

import base64
import hashlib

import pytest

from mkdocs_asset_integrity.core import (
    digest,
    fingerprint_name,
    is_local,
    iter_asset_refs,
    relativize,
    resolve_path,
    rewrite_css_imports,
    rewrite_html,
    split_suffix,
)


class TestDigest:
    @pytest.mark.parametrize("algo", ["sha256", "sha384", "sha512"])
    def test_sri_matches_manual_computation(self, algo):
        data = b"body{color:red}"
        _, sri = digest(data, algo)
        expected = base64.b64encode(hashlib.new(algo, data).digest()).decode()
        assert sri == f"{algo}-{expected}"

    def test_sri_prefix_is_algorithm(self):
        _, sri = digest(b"x", "sha384")
        assert sri.startswith("sha384-")

    def test_hex_digest_is_stable(self):
        a, _ = digest(b"same", "sha256")
        b, _ = digest(b"same", "sha256")
        assert a == b

    def test_different_bytes_differ(self):
        a, _ = digest(b"one", "sha384")
        b, _ = digest(b"two", "sha384")
        assert a != b

    def test_unknown_algorithm_rejected(self):
        with pytest.raises(ValueError, match="unsupported"):
            digest(b"x", "md5")


class TestIsLocal:
    @pytest.mark.parametrize(
        "url",
        [
            "assets/app.js",
            "./app.js",
            "../up.css",
            "a/b/c.css",
            "app.js?v=1",
        ],
    )
    def test_local(self, url):
        assert is_local(url) is True

    @pytest.mark.parametrize(
        "url",
        [
            "https://cdn.example.com/a.js",
            "http://example.com/a.css",
            "//cdn.example.com/a.js",
            "data:text/css,body{}",
            "#anchor",
            "mailto:a@b.c",
            "javascript:void(0)",
            "",
        ],
    )
    def test_external(self, url):
        assert is_local(url) is False


class TestSplitSuffix:
    def test_plain(self):
        assert split_suffix("a/b.css") == ("a/b.css", "", "")

    def test_query(self):
        assert split_suffix("a.css?v=1") == ("a.css", "?v=1", "")

    def test_fragment(self):
        assert split_suffix("a.css#x") == ("a.css", "", "#x")

    def test_both(self):
        assert split_suffix("a.css?v=1#x") == ("a.css", "?v=1", "#x")


class TestFingerprintName:
    def test_inserts_hash_before_extension(self):
        assert fingerprint_name("assets/app.js", "abcdef0123456789") == "assets/app.abcdef01.js"

    def test_respects_length(self):
        assert fingerprint_name("app.js", "abcdef0123456789", 4) == "app.abcd.js"

    def test_preserves_query_and_fragment(self):
        assert fingerprint_name("a.css?v=1#x", "abcdef01") == "a.abcdef01.css?v=1#x"

    def test_no_extension(self):
        assert fingerprint_name("LICENSE", "abcdef01") == "LICENSE.abcdef01"

    def test_nested_path_preserved(self):
        assert fingerprint_name("a/b/c/d.css", "abcdef01") == "a/b/c/d.abcdef01.css"

    def test_hyphenated_and_dotted_names(self):
        # Only the final extension is treated as the extension.
        assert fingerprint_name("x/y.min.css", "abcdef01") == "x/y.min.abcdef01.css"


class TestResolvePath:
    def test_relative_to_root_page(self):
        assert resolve_path("", "assets/app.js") == "assets/app.js"

    def test_relative_to_nested_page(self):
        assert resolve_path("guide/deep/", "../assets/app.js") == "guide/assets/app.js"

    def test_absolute_site_path(self):
        assert resolve_path("guide/", "/assets/app.js") == "assets/app.js"

    def test_rejects_escape_above_root(self):
        assert resolve_path("", "../../etc/passwd") is None

    def test_percent_decoding(self):
        assert resolve_path("", "assets/my%20file.css") == "assets/my file.css"


class TestRelativize:
    def test_root_page(self):
        assert relativize("assets/app.abc.js", "") == "assets/app.abc.js"

    def test_nested_page(self):
        assert relativize("assets/app.abc.js", "guide/deep/") == "../../assets/app.abc.js"

    def test_one_level(self):
        assert relativize("assets/x.css", "guide/") == "../assets/x.css"


class TestIterAssetRefs:
    def test_finds_script_and_link(self):
        html = (
            '<link rel="stylesheet" href="a.css">'
            '<script src="b.js"></script>'
        )
        refs = list(iter_asset_refs(html))
        assert [r.url for r in refs] == ["a.css", "b.js"]
        assert [r.tag for r in refs] == ["link", "script"]

    def test_skips_external(self):
        html = '<script src="https://cdn/x.js"></script>'
        assert list(iter_asset_refs(html)) == []

    def test_skips_non_stylesheet_links(self):
        html = '<link rel="canonical" href="index.html">'
        assert list(iter_asset_refs(html)) == []

    def test_single_quotes(self):
        html = "<script src='a.js'></script>"
        assert [r.url for r in iter_asset_refs(html)] == ["a.js"]

    def test_reports_line_numbers(self):
        html = "<div>\n</div>\n<script src=\"a.js\"></script>"
        assert next(iter(iter_asset_refs(html))).lineno == 3

    def test_case_insensitive_tag(self):
        html = '<SCRIPT SRC="a.js"></SCRIPT>'
        assert len(list(iter_asset_refs(html))) == 1


class TestRewriteHtml:
    def test_rewrites_url(self):
        html = '<link rel="stylesheet" href="assets/a.css">'
        out, changed, _ = rewrite_html(
            html, url_map={"assets/a.css": "assets/a.abc.css"}, integrity_map={}, base_url=""
        )
        assert 'href="assets/a.abc.css"' in out
        assert changed == 1

    def test_adds_integrity_and_crossorigin(self):
        html = '<link rel="stylesheet" href="assets/a.css">'
        out, _, added = rewrite_html(
            html,
            url_map={},
            integrity_map={"assets/a.css": "sha384-XYZ"},
            base_url="",
        )
        assert 'integrity="sha384-XYZ"' in out
        assert 'crossorigin="anonymous"' in out
        assert added == 1

    def test_does_not_duplicate_crossorigin(self):
        html = '<script src="a.js" crossorigin="use-credentials"></script>'
        out, _, _ = rewrite_html(
            html, url_map={}, integrity_map={"a.js": "sha384-XYZ"}, base_url=""
        )
        assert out.count("crossorigin=") == 1
        assert "use-credentials" in out

    def test_rewrites_relative_for_nested_page(self):
        html = '<script src="../../assets/a.js"></script>'
        out, _, _ = rewrite_html(
            html,
            url_map={"assets/a.js": "assets/a.abc.js"},
            integrity_map={},
            base_url="guide/deep/",
        )
        assert 'src="../../assets/a.abc.js"' in out

    def test_preserves_query_string(self):
        html = '<script src="a.js?v=2"></script>'
        out, _, _ = rewrite_html(
            html, url_map={"a.js": "a.abc.js"}, integrity_map={}, base_url=""
        )
        assert 'src="a.abc.js?v=2"' in out

    def test_leaves_external_untouched(self):
        html = '<script src="https://cdn/x.js"></script>'
        out, changed, added = rewrite_html(html, url_map={}, integrity_map={}, base_url="")
        assert out == html
        assert (changed, added) == (0, 0)

    def test_drops_stale_integrity_when_file_was_renamed(self):
        # We renamed the file, so the author's hash is now provably wrong.
        html = '<script src="a.js" integrity="sha384-OLD"></script>'
        out, _, _ = rewrite_html(
            html,
            url_map={"a.js": "a.abc.js"},
            integrity_map={},
            base_url="",
        )
        assert "integrity=" not in out

    def test_keeps_author_integrity_for_untouched_file(self):
        # We did not rename it and did not compute a hash, so the author's
        # value may well be correct; stripping it would be overreach.
        html = '<script src="a.js" integrity="sha384-OLD"></script>'
        out, _, _ = rewrite_html(html, url_map={}, integrity_map={}, base_url="")
        assert 'integrity="sha384-OLD"' in out

    def test_add_sri_false_skips_integrity(self):
        html = '<link rel="stylesheet" href="a.css">'
        out, _, added = rewrite_html(
            html, url_map={}, integrity_map={"a.css": "sha384-X"}, base_url="", add_sri=False
        )
        assert "integrity=" not in out
        assert added == 0

    def test_idempotent_on_second_pass(self):
        html = '<link rel="stylesheet" href="assets/a.css">'
        once, _, _ = rewrite_html(
            html, url_map={"assets/a.css": "assets/a.abc.css"}, integrity_map={"assets/a.css": "sha384-X"}, base_url=""
        )
        # Second pass sees the already-hashed URL. It must not double-rewrite,
        # and it must not stack a second integrity attribute.
        twice, changed2, _ = rewrite_html(
            once, url_map={"assets/a.css": "assets/a.abc.css"}, integrity_map={"assets/a.css": "sha384-X"}, base_url=""
        )
        assert changed2 == 0
        assert twice == once
        assert twice.count("integrity=") == 1
        assert "a.abc.abc" not in twice

    def test_malformed_html_does_not_crash(self):
        for html in ["<script src=", "<link href='a.css'", "<<<>>>", "<script>"]:
            rewrite_html(html, url_map={}, integrity_map={}, base_url="")

    def test_self_closing_tag_gets_attr_before_slash(self):
        html = '<link rel="stylesheet" href="a.css" />'
        out, _, _ = rewrite_html(
            html, url_map={}, integrity_map={"a.css": "sha384-X"}, base_url=""
        )
        assert 'integrity="sha384-X"' in out
        assert out.rstrip().endswith("/>") or "/>" in out


class TestCssImports:
    def test_rewrites_basename(self):
        css = '@import url("base.css");'
        out, n = rewrite_css_imports(css, {"base.css": "base.abc.css"})
        assert out == '@import url("base.abc.css");'
        assert n == 1

    def test_rewrites_bare_form(self):
        css = '@import "base.css";'
        out, n = rewrite_css_imports(css, {"base.css": "base.abc.css"})
        assert "base.abc.css" in out
        assert n == 1

    def test_leaves_unknown_alone(self):
        css = '@import url("other.css");'
        out, n = rewrite_css_imports(css, {"base.css": "base.abc.css"})
        assert out == css
        assert n == 0

    def test_preserves_relative_prefix(self):
        css = '@import url("../shared/base.css");'
        out, _ = rewrite_css_imports(css, {"base.css": "base.abc.css"})
        assert '../shared/base.abc.css' in out

    def test_no_import_returns_early(self):
        css = "body{color:red}"
        out, n = rewrite_css_imports(css, {"a.css": "b.css"})
        assert (out, n) == (css, 0)
