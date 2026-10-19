# Changelog

## 0.1.0

First release.

### Added

- Content-hash fingerprinting for `.css`, `.js` and `.mjs` assets, with the
  hash derived from file bytes so builds are reproducible.
- Subresource Integrity attributes on `<link>` and `<script>`, with
  `crossorigin="anonymous"` added when a tag does not already have a
  `crossorigin` value.
- Per-page relative URL recomputation, so nested pages keep working.
- `@import` chain repair in stylesheet-dependency order, performed **before**
  hashing so integrity attributes stay valid. Circular imports are detected,
  reported, and broken rather than hanging.
- Source map comment repair after renaming.
- Config: `enabled`, `algorithm`, `cache_busting`, `subresource_integrity`,
  `hash_length`, `include_theme_assets`, `extra_assets`, `exclude`, `report`,
  `fail_on_error`.
- Explicit detection and warning when `mkdocs-minify-plugin` claims the same
  files, instead of silently shipping unhashed or unverifiable assets.
- Tolerance for unquoted HTML attributes, so HTML minifiers running in the
  same build do not cause integrity attributes to disappear.

### Fixed during development

Two bugs worth recording, because both were invisible to a passing test suite
and only surfaced when the built site was opened in a real browser:

- **Integrity mismatch on stylesheets with `@import`.** Hashing happened before
  the import was rewritten, so the served bytes differed from the hashed bytes
  and Chromium blocked the file:
  `Failed to find a valid digest in the 'integrity' attribute`.
  Now the import is rewritten first and the final bytes are hashed.
- **Renamed assets lost their integrity attribute.** The SRI table was keyed by
  the original path only, while the HTML referenced the hashed path. Both keys
  are now populated.

### Known gaps

- `search_index.json` is not fingerprinted (the theme fetches it by a hard-coded
  path). See [Limits](limits.md).
- Fonts and images are not renamed, to avoid breaking `url()` references.
