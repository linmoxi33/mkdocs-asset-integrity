"""Pure helpers: hashing, filename fingerprinting, HTML tag rewriting.

Everything here is deliberately free of MkDocs imports so it can be unit
tested without building a site.
"""

from __future__ import annotations

import base64
import hashlib
import logging
import posixpath
import re
from dataclasses import dataclass
from typing import Iterable
from urllib.parse import unquote, urlsplit

log = logging.getLogger("mkdocs.plugins.asset_integrity")

# Algorithms permitted by the SRI spec.
SRI_ALGORITHMS = ("sha256", "sha384", "sha512")

# Extensions that are safe to rename.
#
# Deliberately narrow. Fonts and images are *not* here: their URLs live inside
# CSS `url()` and inside hand-written Markdown/HTML that MkDocs gives us no
# reliable hook to rewrite, so renaming them silently breaks the site. JSON is
# absent too -- `search_index.json` is fetched by a hard-coded string inside
# the theme bundle. Only .css/.js/.mjs are referenced from places we can see
# and rewrite with confidence.
RENAMEABLE_EXTENSIONS = frozenset({".css", ".js", ".mjs"})

# Tags we are willing to add `integrity=` to.
SRI_TAGS = frozenset({"script", "link"})


@dataclass(frozen=True)
class AssetRef:
    """A reference to an asset found somewhere in the site."""

    url: str
    """The URL exactly as it appears in the document."""

    tag: str
    """Lower-cased tag name: 'script', 'link', ..."""

    lineno: int
    """Best-effort line number, for error messages. 0 when unknown."""


def digest(data: bytes, algorithm: str = "sha384") -> tuple[str, str]:
    """Return ``(hex_digest, sri_value)`` for ``data``.

    ``sri_value`` is the base64 form the browser expects in an ``integrity``
    attribute, prefixed with the algorithm name.
    """
    if algorithm not in SRI_ALGORITHMS:
        raise ValueError(
            f"unsupported SRI algorithm {algorithm!r}; expected one of {list(SRI_ALGORITHMS)}"
        )
    h = hashlib.new(algorithm)
    h.update(data)
    raw = h.digest()
    return h.hexdigest(), f"{algorithm}-{base64.b64encode(raw).decode('ascii')}"


def is_local(url: str) -> bool:
    """True when the URL points at a file we own.

    Absolute URLs, protocol-relative URLs, data: URIs and anchors are external
    and must never be rewritten: we cannot hash bytes we do not have, and
    rewriting them would break the site.
    """
    if not url:
        return False
    lowered = url.strip().lower()
    if lowered.startswith(("http://", "https://", "//", "data:", "mailto:", "tel:", "#", "javascript:")):
        return False
    parts = urlsplit(url)
    return not (parts.scheme or parts.netloc)


def split_suffix(url: str) -> tuple[str, str, str]:
    """Split a URL into ``(path, query, fragment)`` keeping ``?`` and ``#``.

    ``a/b.css?v=1#x`` -> ``('a/b.css', '?v=1', '#x')``
    """
    path, query, fragment = url, "", ""
    if "#" in path:
        path, _, frag = path.partition("#")
        fragment = "#" + frag
    if "?" in path:
        path, _, q = path.partition("?")
        query = "?" + q
    return path, query, fragment


def fingerprint_name(url: str, hex_digest: str, length: int = 8) -> str:
    """Insert a short content hash before the extension.

    ``assets/app.js`` -> ``assets/app.<hash>.js``

    Uses ``length`` hex characters. If the name already carries what looks like
    a fingerprint of the same shape we still produce a fresh one; idempotency
    is handled a level up by mapping old->new, never by re-hashing.
    """
    path, query, fragment = split_suffix(url)
    directory, filename = posixpath.split(path)
    stem, dot, ext = filename.rpartition(".")
    if not dot:
        # No extension: append the hash so the name still changes.
        new_name = f"{filename}.{hex_digest[:length]}"
    else:
        new_name = f"{stem}.{hex_digest[:length]}.{ext}"
    joined = posixpath.join(directory, new_name) if directory else new_name
    return joined + query + fragment


def resolve_path(base_url: str, url: str) -> str | None:
    """Resolve ``url`` relative to the directory of ``base_url``.

    Returns a site-root-relative POSIX path, or ``None`` when the reference
    escapes the site root (``../../../etc/passwd``) -- those we refuse to
    touch rather than silently clamping, which would corrupt the link.
    """
    path, _, _ = split_suffix(url)
    if path.startswith("/"):
        candidate = path.lstrip("/")
    else:
        base_dir = posixpath.dirname(base_url)
        candidate = posixpath.normpath(posixpath.join(base_dir, path))
        if candidate == ".." or candidate.startswith("../"):
            return None
        if candidate == ".":
            return ""
    return unquote(candidate)


def relativize(target: str, base_url: str) -> str:
    """Express site-relative ``target`` as a URL relative to ``base_url``."""
    base_dir = posixpath.dirname(base_url)
    return posixpath.relpath(target, base_dir or ".")


_CSS_IMPORT_RE = re.compile(
    r"""@import\s+(?:url\(\s*)?["']?(?P<url>[^"')\s]+)["']?\s*\)?\s*;""",
)


def iter_css_imports(css: str) -> Iterable[str]:
    """Yield URLs referenced by ``@import`` in a stylesheet.

    Only ``@import`` is followed. Plain ``url(...)`` references are left
    alone: rewriting them would require resolving font/image paths, and those
    files are not renamed by this plugin anyway.
    """
    for m in _CSS_IMPORT_RE.finditer(css):
        yield m.group("url")


def rewrite_css_imports(css: str, mapping: dict[str, str]) -> tuple[str, int]:
    """Point ``@import`` statements at renamed stylesheets.

    ``mapping`` maps the *old basename* to the *new basename*. Prefer
    :func:`rewrite_css_imports_by_path` when the stylesheet tree has
    same-named files in different directories.
    """
    if "@import" not in css:
        return css, 0

    changed = 0

    def _sub(m: re.Match[str]) -> str:
        nonlocal changed
        url = m.group("url")
        base = posixpath.basename(url)
        new = mapping.get(base)
        if not new:
            return m.group(0)
        changed += 1
        prefix = url[: len(url) - len(base)]
        return m.group(0).replace(url, prefix + new, 1)

    return _CSS_IMPORT_RE.sub(_sub, css), changed


def rewrite_css_imports_by_path(
    css: str, css_path: str, path_map: dict[str, str]
) -> tuple[str, int]:
    """Rewrite ``@import`` targets using site-relative path resolution.

    ``path_map`` maps an old site-relative path to its new one. The replacement
    is expressed relative to ``css_path``'s directory, so a stylesheet in
    ``a/b/`` that imports ``../c/x.css`` keeps working after both files move.
    """
    if "@import" not in css:
        return css, 0

    css_dir = posixpath.dirname(css_path)
    changed = 0

    def _sub(m: re.Match[str]) -> str:
        nonlocal changed
        url = m.group("url")
        if not is_local(url):
            return m.group(0)
        path, query, fragment = split_suffix(url)
        target = posixpath.normpath(posixpath.join(css_dir, path))
        new_target = path_map.get(target)
        if new_target is None:
            return m.group(0)
        new_url = posixpath.relpath(new_target, css_dir or ".") + query + fragment
        changed += 1
        return m.group(0).replace(url, new_url, 1)

    return _CSS_IMPORT_RE.sub(_sub, css), changed


_ATTR_RE = re.compile(
    r"""(?P<name>[a-zA-Z_:][-a-zA-Z0-9_:.]*)\s*=\s*(?:(?P<q>["'])(?P<val_q>.*?)(?P=q)|(?P<val_u>[^\s"'>]+))""",
    re.DOTALL,
)

_TAG_RE = re.compile(
    r"<(?P<tag>script|link)\b(?P<attrs>[^>]*)>",
    re.IGNORECASE | re.DOTALL,
)


def _parse_attrs(attr_blob: str) -> dict[str, str]:
    """Parse attributes, tolerating unquoted values.

    Minifiers routinely strip quotes (``href=assets/a.css``). An attribute
    parser that only understands quoted values silently sees no URL at all,
    which makes SRI disappear exactly when a minifier runs before us.
    """
    out: dict[str, str] = {}
    for m in _ATTR_RE.finditer(attr_blob):
        val = m.group("val_q")
        out[m.group("name").lower()] = val if val is not None else m.group("val_u")
    return out


def _attr_url(tag: str, attrs: dict[str, str]) -> str | None:
    if tag == "script":
        return attrs.get("src")
    # link: only stylesheets and friends carry meaningful URLs for SRI.
    rel = attrs.get("rel", "").lower().split()
    if rel and not ({"stylesheet", "preload", "modulepreload", "icon"} & set(rel)):
        return None
    return attrs.get("href")


def iter_asset_refs(html: str) -> Iterable[AssetRef]:
    """Yield every asset reference we may want to hash or annotate."""
    for m in _TAG_RE.finditer(html):
        tag = m.group("tag").lower()
        attrs = _parse_attrs(m.group("attrs"))
        url = _attr_url(tag, attrs)
        if not url:
            continue
        if not is_local(url):
            continue
        yield AssetRef(url=url, tag=tag, lineno=html.count("\n", 0, m.start()) + 1)


def _set_attr(attr_blob: str, name: str, value: str) -> str:
    """Set (or add) an attribute inside a raw attribute blob.

    Handles both quoted and unquoted existing values, since a minifier may
    have produced ``href=assets/a.css``. When inserting a new attribute we
    always use quotes: SRI values contain ``+`` and ``/``, and unquoted
    attributes cannot legally contain those characters.
    """
    pattern = re.compile(
        rf"""(?P<pre>\b{re.escape(name)}\s*=\s*)(?P<q>["'])(?P<val>.*?)(?P=q)""",
        re.DOTALL | re.IGNORECASE,
    )
    if pattern.search(attr_blob):
        return pattern.sub(lambda m: f'{m.group("pre")}"{value}"', attr_blob, count=1)

    unquoted = re.compile(
        rf"""(?P<pre>\b{re.escape(name)}\s*=\s*)(?P<val>[^\s"'>]+)""",
        re.IGNORECASE,
    )
    if unquoted.search(attr_blob):
        return unquoted.sub(lambda m: f'{m.group("pre")}"{value}"', attr_blob, count=1)

    # Insert before the trailing slash of a self-closing tag, if present.
    stripped = attr_blob.rstrip()
    trailing = attr_blob[len(stripped):]
    if stripped.endswith("/"):
        return f'{stripped[:-1].rstrip()} {name}="{value}" /{trailing}'
    return f'{stripped} {name}="{value}"{trailing}'


def _remove_attr(attr_blob: str, name: str) -> str:
    pattern = re.compile(
        rf"""\s*\b{re.escape(name)}\s*=\s*(?:["'][^"']*["']|[^\s>]+)""",
        re.IGNORECASE,
    )
    return pattern.sub("", attr_blob)


def rewrite_html(
    html: str,
    *,
    url_map: dict[str, str],
    integrity_map: dict[str, str],
    base_url: str,
    add_sri: bool = True,
    crossorigin: str = "anonymous",
) -> tuple[str, int, int]:
    """Rewrite asset URLs and inject integrity attributes.

    ``url_map`` maps a *site-relative* path to its new site-relative path.
    ``integrity_map`` maps a *site-relative* path to its SRI string.

    Returns ``(new_html, urls_changed, integrity_added)``.
    """
    urls_changed = 0
    integrity_added = 0

    def _replace(m: re.Match[str]) -> str:
        nonlocal urls_changed, integrity_added
        tag = m.group("tag").lower()
        attr_blob = m.group("attrs")
        attrs = _parse_attrs(attr_blob)
        url = _attr_url(tag, attrs)
        if not url or not is_local(url):
            return m.group(0)

        resolved = resolve_path(base_url, url)
        if resolved is None:
            return m.group(0)

        new_blob = attr_blob
        new_url = url

        if resolved in url_map:
            mapped = url_map[resolved]
            _, query, fragment = split_suffix(url)
            new_url = relativize(mapped, base_url) + query + fragment
            attr_name = "src" if tag == "script" else "href"
            new_blob = _set_attr(new_blob, attr_name, new_url)
            urls_changed += 1

        if add_sri and tag in SRI_TAGS:
            sri = integrity_map.get(resolved)
            if sri:
                new_blob = _set_attr(new_blob, "integrity", sri)
                # Only set crossorigin when the tag does not already carry it.
                if "crossorigin" not in {k for k in _parse_attrs(new_blob)}:
                    new_blob = _set_attr(new_blob, "crossorigin", crossorigin)
                integrity_added += 1
            elif "integrity" in attrs and resolved in url_map:
                # We renamed this file, so the author's hash is now provably
                # wrong. Dropping it beats shipping a value the browser will
                # reject. If we did *not* rename the file we leave the
                # author's integrity alone -- it may well be correct, and we
                # have no right to strip a check we did not compute.
                new_blob = _remove_attr(new_blob, "integrity")

        return f"<{m.group('tag')}{new_blob}>"

    # Guard against a pathological document making this quadratic.
    if "<script" not in html.lower() and "<link" not in html.lower():
        return html, 0, 0

    return _TAG_RE.sub(_replace, html), urls_changed, integrity_added
