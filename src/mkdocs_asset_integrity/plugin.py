"""mkdocs-asset-integrity: content hashing + Subresource Integrity for MkDocs.

Why this plugin exists
----------------------
MkDocs fingerprints *nothing* of its own. Files you list in ``extra_css`` and
``extra_javascript`` keep their original names forever, so a browser that
cached ``assets/custom.css`` on Monday keeps serving that copy on Friday after
you changed it. Upstream has tracked this since 2020 (mkdocs#1979) and the
pull request (mkdocs#3018) is still open. The commonly suggested workaround,
``mkdocs-minify-plugin`` with ``cache_safe: true``, only acts on files you
enumerate one by one in ``css_files`` / ``js_files`` -- which default to
``None``, so the option silently does nothing until you duplicate your asset
list -- and it never emits ``integrity`` attributes at all.

This plugin closes both halves, and it draws one deliberate line: it
fingerprints *your* assets, not the theme's. Themes that already hash their
own files (Material ships ``main.342714a4.min.css``) would end up with a
double hash, and theme JavaScript often loads chunks by a name baked into the
bundle, so renaming it breaks the site at runtime. Theme assets still receive
``integrity`` attributes, which is safe and useful.
"""

from __future__ import annotations

import fnmatch
import logging
import os
import posixpath
import re
import shutil

from mkdocs.exceptions import BuildError
from mkdocs.plugins import BasePlugin

from .config import AssetIntegrityConfig
from .core import (
    RENAMEABLE_EXTENSIONS,
    digest,
    fingerprint_name,
    is_local,
    iter_css_imports,
    rewrite_css_imports_by_path,
    rewrite_html,
    split_suffix,
)

log = logging.getLogger("mkdocs.plugins.asset_integrity")

__version__ = "0.1.0"

# A name that already looks content-hashed, e.g. `main.342714a4.min.css`.
_ALREADY_HASHED = re.compile(r"\.[0-9a-f]{8,32}\.[A-Za-z0-9]+$")


def looks_fingerprinted(path: str) -> bool:
    """True when the filename already carries a content hash."""
    return bool(_ALREADY_HASHED.search(posixpath.basename(path)))


class AssetIntegrityPlugin(BasePlugin[AssetIntegrityConfig]):
    """Fingerprint static assets and add Subresource Integrity attributes."""

    # Run our `on_post_page` early, before minifiers rewrite the HTML.
    #
    # MkDocs sorts event handlers by descending `mkdocs_priority`, so a higher
    # number runs first. This matters for correctness, not politeness: an
    # integrity attribute describes the exact bytes we hashed at build time.
    # If a later plugin rewrites the asset file (mkdocs-minify-plugin does
    # exactly that in `on_post_build`), our hash no longer describes what is
    # served and every integrity check fails in the browser. Running first
    # also means we see the pre-minified markup, where attribute values are
    # reliably quoted.
    mkdocs_priority = 100

    def __init__(self) -> None:
        # site-relative original path -> site-relative hashed path
        self._url_map: dict[str, str] = {}
        # site-relative original path -> SRI string
        self._integrity: dict[str, str] = {}
        # site-relative path -> why we left it alone
        self._skipped: dict[str, str] = {}
        self._theme_assets: set[str] = set()

    # -- config helpers --------------------------------------------------

    def _patterns(self) -> list[str]:
        raw = self.config.exclude
        if raw is None:
            return []
        if isinstance(raw, str):
            return [raw]
        return [str(p) for p in raw]

    def _excluded(self, site_path: str) -> bool:
        return any(fnmatch.fnmatch(site_path, pat) for pat in self._patterns())

    def _extra_assets(self) -> list[str]:
        raw = self.config.extra_assets
        if raw is None:
            return []
        if isinstance(raw, str):
            return [raw]
        return [str(p) for p in raw]

    def _declared(self, config) -> list[str]:
        """Asset paths the user pointed at explicitly."""
        out = [str(e) for e in (config.extra_css or [])]
        out += [str(getattr(e, "path", e)) for e in (config.extra_javascript or [])]
        out += self._extra_assets()
        seen: set[str] = set()
        uniq: list[str] = []
        for p in out:
            p = p.replace("\\", "/").lstrip("/")
            if p and p not in seen:
                seen.add(p)
                uniq.append(p)
        return uniq

    # -- IO --------------------------------------------------------------

    def _read_bytes(self, path: str) -> bytes | None:
        try:
            with open(path, "rb") as fh:
                return fh.read()
        except OSError as exc:
            msg = f"cannot read {path}: {exc}"
            if self.config.fail_on_error:
                raise BuildError(msg) from exc
            log.warning("asset-integrity: %s (skipped)", msg)
            return None

    # -- MkDocs events ---------------------------------------------------

    def on_config(self, config):
        if not self.config.enabled:
            log.debug("asset-integrity: disabled by config")
            return config
        if self.config.hash_length < 4:
            log.warning(
                "asset-integrity: hash_length=%d is too short to be a useful "
                "fingerprint, using 8",
                self.config.hash_length,
            )
            self.config.hash_length = 8
        return config

    def on_files(self, files, *, config):
        """Decide every asset's final name *before* any HTML is rendered.

        Ordering is the whole trick here. ``on_post_page`` runs per page and
        ``on_post_build`` runs once at the very end, so renaming files at the
        end would leave every page pointing at a name that no longer exists.
        The mapping is therefore computed here from source bytes, and applied
        to both the config (so themes render the new names) and the filesystem
        (so the files really have those names).
        """
        if not self.config.enabled:
            return files

        self._url_map.clear()
        self._integrity.clear()
        self._skipped.clear()
        self._theme_assets.clear()
        self._rewritten: dict[str, bytes] = {}

        docs_dir = os.path.abspath(config.docs_dir)
        length = self.config.hash_length
        tracked: set[str] = set()
        pending: list[tuple[str, str, bytes]] = []

        for f in files:
            dest = f.dest_uri.replace("\\", "/")
            tracked.add(dest)

            ext = posixpath.splitext(dest)[1].lower()
            if ext not in RENAMEABLE_EXTENSIONS:
                continue

            if self._excluded(dest):
                self._skipped[dest] = "excluded by config"
                continue

            abs_src = f.abs_src_path
            if not abs_src or not os.path.isfile(abs_src):
                self._skipped[dest] = "source missing"
                continue

            is_theme = not os.path.abspath(abs_src).startswith(docs_dir + os.sep)
            if is_theme:
                self._theme_assets.add(dest)

            data = self._read_bytes(abs_src)
            if data is None:
                self._skipped[dest] = "unreadable"
                continue

            pending.append((dest, abs_src, data))

        # Resolve names in stylesheet-dependency order.
        #
        # Ordering is load-bearing twice over. First, a stylesheet's bytes
        # change when its @import targets are renamed, so its hash -- and
        # therefore its own name -- can only be computed after its imports are
        # settled. Processing leaves before parents does that exactly once per
        # file. Second, if we hashed first and patched the CSS afterwards, the
        # bytes we hashed would not be the bytes we serve, and the browser
        # would block the stylesheet with an integrity mismatch. Doing it in
        # this order makes "hash what you ship" true by construction.
        by_path = {dest: (abs_src, data) for dest, abs_src, data in pending}
        for dest in self._css_resolution_order(by_path):
            abs_src, data = by_path[dest]
            final = self._rewritten.get(dest, data)
            hex_digest, sri = digest(final, self.config.algorithm)

            if self.config.subresource_integrity:
                self._integrity[dest] = sri

            if not self.config.cache_busting:
                self._skipped.setdefault(dest, "cache_busting disabled")
                continue
            if dest in self._theme_assets and not self.config.include_theme_assets:
                self._skipped.setdefault(
                    dest, "theme asset (set include_theme_assets to hash)"
                )
                continue
            if looks_fingerprinted(dest):
                self._skipped.setdefault(dest, "filename already content-hashed")
                continue

            new_path = fingerprint_name(dest, hex_digest, length)
            if new_path == dest:
                continue
            self._url_map[dest] = new_path

            # Renaming this file changes the bytes of every stylesheet that
            # @imports it, so rewrite those now -- they are processed later.
            self._propagate_rename(dest, by_path)

        # Surface declared assets that do not exist: a typo here means a
        # silent 404 on every single page.
        for path in self._declared(config):
            if path.startswith(("http://", "https://", "//")):
                continue
            if path in tracked:
                continue
            # mkdocs-minify-plugin rewrites config.extra_css to `*.min.css`
            # during on_files, *before* we run, so the file we are told about
            # is a name that will only exist after that plugin's post_build.
            # Renaming it here would collide; we keep the original name and
            # say so plainly rather than emitting an integrity hash for bytes
            # we have not seen.
            if re.search(r"\.min\.(css|js)$", path) and path.replace(".min.", ".") in tracked:
                log.warning(
                    "asset-integrity: %r is produced by mkdocs-minify-plugin at "
                    "post_build, after this plugin has locked asset names. It keeps "
                    "its original name and gets no integrity attribute. Either drop "
                    "the minifier's css_files/js_files entries and let this plugin "
                    "own fingerprinting, or accept unhashed assets.",
                    path,
                )
                continue
            log.warning(
                "asset-integrity: %r is declared in config but was not found "
                "in docs_dir; its URL is left unchanged",
                path,
            )

        self._apply_to_config(config)

        # After `_apply_to_config`, pages reference the *hashed* names, so the
        # SRI table must answer to both spellings. Without this the very
        # assets we renamed would silently lose their integrity attribute.
        for old, new in self._url_map.items():
            if old in self._integrity:
                self._integrity[new] = self._integrity[old]

        if self.config.report:
            log.info(
                "asset-integrity: %d renamed, %d hashed for SRI, %d skipped",
                len(self._url_map),
                len(self._integrity),
                len(self._skipped),
            )
        return files

    def _css_resolution_order(self, by_path: dict[str, tuple[str, bytes]]) -> list[str]:
        """Order assets so that every ``@import`` target precedes its importer.

        Cycles are possible (a.css imports b.css imports a.css) and are common
        enough in hand-written CSS that crashing would be unacceptable. When a
        cycle is detected the remaining files are appended in a stable order;
        their imported URLs may stay one generation stale, which we surface as
        a warning rather than a silent wrong answer.
        """
        css = {p: d for p, (_a, d) in by_path.items() if p.endswith(".css")}
        deps: dict[str, set[str]] = {}
        for path, data in css.items():
            targets: set[str] = set()
            try:
                text = data.decode("utf-8")
            except UnicodeDecodeError:
                deps[path] = set()
                continue
            if "@import" in text:
                base_dir = posixpath.dirname(path)
                for url in iter_css_imports(text):
                    if not is_local(url):
                        continue
                    target, _, _ = split_suffix(url)
                    resolved = posixpath.normpath(posixpath.join(base_dir, target))
                    if resolved in css and resolved != path:
                        targets.add(resolved)
            deps[path] = targets

        ordered: list[str] = []
        emitted: set[str] = set()
        remaining = dict(deps)

        while remaining:
            ready = sorted(p for p, d in remaining.items() if d <= emitted)
            if not ready:
                # Cycle: break it deterministically at the smallest path.
                ready = [min(remaining)]
                log.warning(
                    "asset-integrity: @import cycle involving %s; "
                    "one stylesheet may reference a stale name",
                    ", ".join(sorted(remaining)),
                )
            for p in ready:
                ordered.append(p)
                emitted.add(p)
                remaining.pop(p, None)

        # Non-CSS assets keep their discovery order, after all CSS.
        ordered.extend(p for p in by_path if not p.endswith(".css"))
        return ordered

    def _propagate_rename(self, renamed: str, by_path: dict[str, tuple[str, bytes]]) -> None:
        """Rewrite ``@import`` statements in stylesheets that reference ``renamed``.

        Called the moment a stylesheet's final name is known, so that its
        importers -- not yet hashed -- see the correct target string.
        """
        for path, (_abs, data) in by_path.items():
            if not path.endswith(".css"):
                continue
            current = self._rewritten.get(path, data)
            try:
                text = current.decode("utf-8")
            except UnicodeDecodeError:
                continue
            if "@import" not in text:
                continue
            updated, n = rewrite_css_imports_by_path(text, path, self._url_map)
            if n:
                self._rewritten[path] = updated.encode("utf-8")

    def _apply_to_config(self, config) -> None:
        """Rewrite extra_css / extra_javascript to the hashed names.

        Doing this instead of string-patching rendered HTML means every theme
        benefits, including ones this plugin has never seen.
        """
        if not self._url_map:
            return

        config.extra_css = [
            self._url_map.get(str(e), str(e)) for e in (config.extra_css or [])
        ]

        new_js = []
        for entry in config.extra_javascript or []:
            path = str(getattr(entry, "path", entry))
            mapped = self._url_map.get(path)
            if mapped is None:
                new_js.append(entry)
            elif hasattr(entry, "path"):
                entry.path = mapped
                new_js.append(entry)
            else:
                new_js.append(mapped)
        config.extra_javascript = new_js

    def on_post_page(self, output, *, page, config):
        if not self.config.enabled:
            return output
        if not (self.config.cache_busting or self.config.subresource_integrity):
            return output

        new_output, changed, sri = rewrite_html(
            output,
            url_map=self._url_map,
            integrity_map=self._integrity,
            base_url=page.url,
            add_sri=self.config.subresource_integrity,
        )
        if self.config.report and (changed or sri):
            log.debug(
                "asset-integrity: %s: %d url(s) rewritten, %d integrity added",
                page.file.src_uri,
                changed,
                sri,
            )
        return new_output

    def on_post_build(self, *, config):
        """Write rewritten stylesheets and move files to their hashed names.

        The ``@import`` repair already happened in memory during ``on_files``
        (that is what makes the SRI hashes correct), so here we only have to
        persist those bytes and rename the files.
        """
        if not self.config.enabled:
            return
        if not self._url_map and not self._rewritten:
            return

        site_dir = config.site_dir
        moved: list[tuple[str, str]] = []
        written = 0

        # Files whose bytes changed but whose name did not (a stylesheet that
        # imports a renamed one) still need their new contents on disk. This
        # must run *before* the move loop: after a move, the "old" path is
        # already gone and writing to it would recreate a duplicate.
        for path, data in self._rewritten.items():
            if path in self._url_map:
                continue  # handled by the move below, which carries the bytes
            abs_path = os.path.join(site_dir, path.replace("/", os.sep))
            if not os.path.isfile(abs_path):
                continue
            try:
                with open(abs_path, "wb") as fh:
                    fh.write(data)
                written += 1
            except OSError as exc:
                msg = f"cannot write rewritten {path}: {exc}"
                if self.config.fail_on_error:
                    raise BuildError(msg) from exc
                log.warning("asset-integrity: %s", msg)

        for old, new in self._url_map.items():
            src = os.path.join(site_dir, old.replace("/", os.sep))
            dst = os.path.join(site_dir, new.replace("/", os.sep))
            if not os.path.isfile(src):
                # Never copied (excluded, or a dirty build), or already moved.
                continue
            try:
                os.makedirs(os.path.dirname(dst), exist_ok=True)
                if os.path.exists(dst):
                    os.remove(dst)
                if old in self._rewritten:
                    with open(dst, "wb") as fh:
                        fh.write(self._rewritten[old])
                    os.remove(src)
                    written += 1
                else:
                    shutil.move(src, dst)
                moved.append((old, new))
            except OSError as exc:
                msg = f"cannot rename {old} -> {new}: {exc}"
                if self.config.fail_on_error:
                    raise BuildError(msg) from exc
                log.warning("asset-integrity: %s", msg)

        sm = self._fix_sourcemap_comments(config, moved)

        if self.config.report:
            log.info(
                "asset-integrity: %d file(s) renamed, %d rewritten, "
                "%d sourcemap reference(s) repaired",
                len(moved),
                written,
                sm,
            )

    def _fix_sourcemap_comments(self, config, moved: list[tuple[str, str]]) -> int:
        """Keep ``//# sourceMappingURL=`` pointing at a file that still exists.

        Renaming ``app.js`` orphans its ``.map`` comment; rewriting the comment
        is cheaper than dropping devtools support.
        """
        if not moved:
            return 0
        pattern = re.compile(rb"(sourceMappingURL=)([\w./-]+\.map)")
        fixed = 0
        for old, new in moved:
            if not new.endswith((".js", ".mjs", ".css")):
                continue
            abs_path = os.path.join(config.site_dir, new.replace("/", os.sep))
            try:
                with open(abs_path, "rb") as fh:
                    data = fh.read()
            except OSError:
                continue
            if b"sourceMappingURL=" not in data:
                continue

            def _sub(m: re.Match[bytes], old=old, new=new) -> bytes:
                target = m.group(2).decode("ascii", "replace")
                key = posixpath.normpath(
                    posixpath.join(posixpath.dirname(old), target)
                )
                mapped = self._url_map.get(key)
                if not mapped:
                    return m.group(0)
                return m.group(1) + posixpath.basename(mapped).encode("ascii")

            new_data = pattern.sub(_sub, data)
            if new_data != data:
                try:
                    with open(abs_path, "wb") as fh:
                        fh.write(new_data)
                    fixed += 1
                except OSError:
                    pass
        return fixed
