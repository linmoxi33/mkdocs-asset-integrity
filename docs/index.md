# mkdocs-asset-integrity

![mkdocs-asset-integrity](assets/cover.png)

Content-hash your MkDocs CSS and JS, and add Subresource Integrity so a CDN
cannot serve tampered bytes.

MkDocs emits `extra_css` and `extra_javascript` with the same filenames
forever. Change a stylesheet, deploy, and visitors with a warm cache keep the
old copy — current content, stale styling. Upstream has tracked this since
2020 ([#1979](https://github.com/mkdocs/mkdocs/issues/1979)) and the fix is
still an open pull request ([#3018](https://github.com/mkdocs/mkdocs/pull/3018)).

This plugin fingerprints your assets and verifies them, with no dependencies
beyond MkDocs itself:

```yaml
plugins:
  - asset-integrity
```

```html
<link rel="stylesheet" href="assets/custom.901d2a0f.css"
      integrity="sha384-kB0qD1j4VG+M7P2K8r2ywD9IAzKKvvxMonSbUrJxGrPeBmlGPxKQNPtuA4nhcApR"
      crossorigin="anonymous">
```

!!! note "Why not mkdocs-minify-plugin's `cache_safe`?"
    It only acts on files you enumerate one by one in `css_files` / `js_files`
    (both default to `None`, so it silently does nothing until you duplicate
    your asset list), and it never emits `integrity` attributes. This plugin
    handles both, and detects that conflict explicitly if you run the two
    together.

## Start here

- **[Why this exists](rationale.md)** — the problem, the upstream history, and
  why the existing workaround falls short.
- **[Configuration](configuration.md)** — every option, with defaults.
- **[How it works](internals.md)** — event ordering, `@import` resolution, and
  two bugs that only a real browser could catch.
- **[Limits](limits.md)** — what this deliberately does not do, and why.
- **[Changelog](changelog.md)**

## Install

```bash
pip install mkdocs-asset-integrity
```

Requires MkDocs 1.4+ and Python 3.8+.
