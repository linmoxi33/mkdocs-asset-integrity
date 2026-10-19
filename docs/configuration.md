# Configuration

All options are optional. The defaults are what most sites want, so the
shortest useful config is:

```yaml
plugins:
  - asset-integrity
```

## Options

### `enabled`

:material-tag: default `true`

Master switch. Setting it to `false` leaves the plugin installed but inert,
which is handy for bisecting a build problem without uninstalling anything.

```yaml
plugins:
  - asset-integrity:
      enabled: false
```

### `algorithm`

:material-tag: default `sha384`

One of `sha256`, `sha384`, `sha512`. These are the three the SRI specification
permits; anything else is rejected when the config is loaded, not at build time.

`sha384` is the conventional default. `sha512` is marginally stronger and
produces noticeably longer attributes; `sha256` is shorter. There is no
meaningful security difference at these sizes, so pick for attribute length.

```yaml
plugins:
  - asset-integrity:
      algorithm: sha512
```

### `cache_busting`

:material-tag: default `true`

Rename assets to embed a content hash:

```
assets/app.js  ->  assets/app.dd54f36f.js
```

The hash is computed from the file's bytes. Same bytes, same name — so
builds are reproducible. Change one byte and the name changes.

Set to `false` if you only want integrity attributes on stable filenames.

### `subresource_integrity`

:material-tag: default `true`

Emit `integrity="..."` on `<link>` and `<script>` tags, plus
`crossorigin="anonymous"` if the tag does not already carry a `crossorigin`.

Existing `crossorigin` values are never overwritten: if you set
`crossorigin="use-credentials"` by hand, it stays.

Set to `false` to fingerprint without adding integrity attributes.

### `hash_length`

:material-tag: default `8`

Number of hexadecimal characters in the filename. 8 characters (32 bits) is
plenty to avoid collisions within a site; values below 4 are silently raised
to 8, since a 2-character hash collides constantly and would defeat the point.

Note that this only affects the *filename*. The `integrity` attribute always
carries the full digest.

### `include_theme_assets`

:material-tag: default `false`

Also rename files that come from the theme rather than from `docs_dir`.

Leave this `false` unless you know your theme. Material already ships
`main.342714a4.min.css`, so hashing it again yields
`main.342714a4.<new-hash>.min.css`; worse, some theme JavaScript loads chunks
by a name baked into its bundle, and renaming those files breaks the site at
runtime with no build error. Theme assets still receive integrity attributes
either way.

### `extra_assets`

:material-tag: default `[]`

Additional paths, relative to `docs_dir`, to fingerprint. MkDocs only tracks
assets you declare in `extra_css` / `extra_javascript`; if a stylesheet is
referenced from inside Markdown instead, list it here.

```yaml
plugins:
  - asset-integrity:
      extra_assets:
        - assets/print.css
        - assets/vendor/chart.js
```

### `exclude`

:material-tag: default `[]`

`:material-tag:` fnmatch globs matched against site-relative paths. Excluded
files keep their name and get no integrity attribute.

```yaml
plugins:
  - asset-integrity:
      exclude:
        - assets/legacy/*.css
        - "**/*.min.js"
```

Exclusion is applied *before* any naming decision, so an excluded file is
never renamed and never referenced by a name it does not have.

### `report`

:material-tag: default `false`

Log a one-line summary: how many assets were renamed, hashed, and skipped.
Useful the first time you enable the plugin, and in CI logs when you want to
confirm it actually ran.

```
INFO - asset-integrity: 2 renamed, 46 hashed for SRI, 38 skipped
INFO - asset-integrity: 2 file(s) renamed, 1 rewritten, 0 sourcemap reference(s) repaired
```

### `fail_on_error`

:material-tag: default `false`

By default a single unreadable file produces a warning and is skipped, so a
build never fails because of one odd asset. Set this to `true` in CI if you
would rather a broken asset break the build than ship silently unhashed.

## Interaction with mkdocs-minify-plugin

Both plugins want to name your CSS and JS. If you configure the minifier's
`css_files` / `js_files`, it rewrites those paths to `*.min.css` during
`on_files` and only creates the files at `on_post_build` — after this plugin
has already locked names. This plugin detects that case, keeps the original
name, skips the integrity attribute (it never saw the bytes), and warns:

```
WARNING - asset-integrity: 'assets/site.min.css' is produced by
mkdocs-minify-plugin at post_build, after this plugin has locked asset names.
```

Two clean ways to combine them:

1. **Let this plugin own fingerprints.** Drop `css_files` / `js_files` from the
   minifier config (leave `minify_html: true` if you want HTML minification).
   You lose CSS/JS minification but keep both fingerprints and SRI.
2. **Let the minifier own them.** Configure only `minify_html`, and set
   `cache_busting: false` here so this plugin adds integrity attributes to
   whatever filenames end up in the page.

## Verified example

```yaml
site_name: My Docs
theme:
  name: material
plugins:
  - search
  - asset-integrity:
      algorithm: sha384
      report: true
extra_css:
  - assets/extra.css
extra_javascript:
  - assets/app.js
```

Build output for `assets/extra.css`:

```html
<link rel="stylesheet" href="assets/extra.b08a46c4.css"
      integrity="sha384-eUpCKmDFrHvHCwCwZDDwCuoSsBTIjEqBeYuRDCH2VkkCJTPqWfMFbaLTsNuJfEbX"
      crossorigin="anonymous">
```

On a page nested two directories deep, the same asset is referenced as
`../../assets/extra.b08a46c4.css`, with the identical integrity value.
