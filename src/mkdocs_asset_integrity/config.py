"""Configuration schema for mkdocs-asset-integrity."""

from __future__ import annotations

from mkdocs.config import base
from mkdocs.config import config_options as c


class AssetIntegrityConfig(base.Config):
    """User-facing options, all optional with safe defaults."""

    enabled = c.Type(bool, default=True)
    """Master switch. Set to false to keep the plugin installed but inert."""

    algorithm = c.Choice(
        ("sha256", "sha384", "sha512"),
        default="sha384",
    )
    """Hash algorithm. sha384 is the de-facto SRI default; sha512 is stronger."""

    cache_busting = c.Type(bool, default=True)
    """Emit content-hashed filenames for assets that can be renamed safely."""

    subresource_integrity = c.Type(bool, default=True)
    """Emit `integrity=` (and `crossorigin=`) on eligible tags."""

    hash_length = c.Type(int, default=8)
    """Number of hex chars used in a hashed filename. Ignored when not hashing."""

    include_theme_assets = c.Type(bool, default=False)
    """Also rename theme-provided assets (not just your own).

    Off by default and you probably want to keep it that way: themes such as
    Material already ship hashed filenames, and theme JavaScript often loads
    chunks by a name baked into its bundle, so renaming those files breaks the
    site at runtime with no build-time error. Theme assets still get
    ``integrity`` attributes either way.
    """

    extra_assets = c.Optional(c.Type((str, list)))
    """Additional asset paths (relative to docs_dir) to fingerprint.

    Useful for assets referenced from inside Markdown or raw HTML, which MkDocs
    does not track in `extra_css` / `extra_javascript`.
    """

    exclude = c.Optional(c.Type((str, list)))
    """Glob patterns (fnmatch) of site-relative paths to leave untouched."""

    fingerprint_pages = c.Type(bool, default=False)
    """Also rewrite assets referenced from rendered page HTML.

    Off by default: it rewrites markup that authors wrote by hand.
    """

    report = c.Type(bool, default=False)
    """Log a one-line summary of what was hashed, skipped and why."""

    fail_on_error = c.Type(bool, default=False)
    """Turn unexpected per-asset failures into a hard build error."""
