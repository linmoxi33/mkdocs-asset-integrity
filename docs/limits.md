# Limits

This page exists because a tool that only documents what it does well is not
trustworthy. These are the things this plugin does *not* solve, with the
reasoning, so you can decide whether they matter for your site.

## `search_index.json` is not fingerprinted

This is the most requested case in the upstream issue and this plugin does not
handle it.

The theme fetches the search index by a path baked into its JavaScript bundle:

```js
// inside the theme's bundle.js, not something MkDocs exposes
fetch("search/search_index.json")
```

Renaming the file on disk produces a 404 that no build step can see, and
search silently stops working. Fixing it properly means editing the theme's
bundle or patching `search`'s template — both of which are theme-specific and
fragile. A maintainer described this as needing "a totally separate" solution.

**Workaround that does work:** set a short `Cache-Control` on
`search/search_index.json` specifically at your CDN or origin, and cache
everything else immutably. The file is large, so this is a real trade-off, but
it is honest about what it costs.

## Fonts and images are not renamed

Renaming `logo.png` to `logo.abc123.png` breaks every reference to it that
lives somewhere this plugin cannot see:

- `url(...)` inside CSS, which would need full CSS parsing to rewrite safely;
- raw `<img>` tags in Markdown;
- Open Graph / Twitter card meta tags pointing at absolute URLs.

The failure mode is a broken image on every page, which is worse than a stale
image on some. So these extensions are excluded by design. Only `.css`, `.js`
and `.mjs` are fingerprinted.

## Theme assets are not renamed by default

See `include_theme_assets`. Material already ships
`main.342714a4.min.css`, so hashing it again gives a double hash, and theme
bundles sometimes load chunks by name at runtime. Renaming those files breaks
the site with no build error — the exact class of bug this plugin is supposed
to prevent. `include_theme_assets: true` exists for people who have checked
their theme, but the default is deliberately conservative.

## Assets referenced only from Markdown are not fingerprinted

If you write `<link rel="stylesheet" href="assets/x.css">` inside a page's
Markdown, MkDocs does not track that file as an asset, so it never appears in
the `on_files` list and this plugin never sees it.

The `extra_assets` option covers this: list the path there and it gets
fingerprinted. The plugin will not guess, because a wrong guess means a broken
link on one page.

## No integrity for external URLs

A CDN reference cannot be hashed locally. If you need SRI on third-party
scripts, add the attribute by hand with the vendor's published hash — and note
that this pins you to an exact version, which is usually the point.

## Renaming is a moving operation, not a copy

Files are moved within `site_dir`, not duplicated. Anything that reads
`site/` after the build sees only the hashed names. If you have a post-build
step that hard-codes `assets/app.js`, it will not find it — which is the
correct behaviour, but worth knowing before it surprises you in CI.

## `mkdocs serve` live reload

Hashed filenames change while you edit, so a live-reload session reloads the
page rather than trying to hot-swap assets. This is fine in practice; the
plugin is meant for builds, and there is no `--dirty` special-casing.

## Not a security boundary

Subresource Integrity protects against *substitution* of a file between your
build and the visitor's browser. It does not protect against a compromised
build machine, a malicious plugin, or an attacker who can change both the file
and the HTML that references it. It raises the cost of one specific attack; it
is not a substitute for transport security.

## Minimum hashed length

`hash_length` below 4 is raised to 8. With 2 hex characters there are only 256
possible names, so a site with a few hundred assets would hit collisions
routinely — which would silently negate the entire mechanism.

## Python version floor

Requires Python 3.8+ and MkDocs 1.4+. The plugin uses MkDocs' `config_class`
API, which was introduced in 1.4; on older versions it would need the
deprecated `config_scheme` path.
