# Why this plugin exists

## The problem, precisely

Static site generators face one awkward fact: the URL is the cache key. If
`assets/custom.css` is the URL today and after your next deploy, the browser
has no reason to fetch it again. It will keep the copy it has — for as long as
its `Cache-Control` header says — while happily fetching the new HTML that
references it.

The result is a page whose content is current and whose styling is not. It is
one of the least pleasant bugs to receive a report about, because it depends
on *when the visitor last looked*, which makes it irreproducible on your own
machine.

The standard fix is to make the URL a function of the content: rename
`custom.css` to `custom.901d2a0f.css`. Then changing the file changes the URL,
the cache misses naturally, and you can serve everything with
`Cache-Control: immutable` forever.

## Why MkDocs does not already do this

It has been requested. [Issue #1979](https://github.com/mkdocs/mkdocs/issues/1979)
was opened in February 2020 and has 34 comments. In it you can watch the
problem get rediscovered by different people in different forms:

- one user shipped new content but old styling, and only found it by inspecting
  the browser cache — the CSS had a one-month expiry;
- another added new pages that were invisible to search, because the browser
  was still using a stale `search_index.json`;
- a third pointed out that disabling caching is not an answer for a file that
  is megabytes large.

[PR #3018](https://github.com/mkdocs/mkdocs/pull/3018) implements cache busting
and is still open. A maintainer recommended waiting for MkDocs 1.5.0; as of
1.6.1, `extra_css` is still emitted verbatim with no fingerprint.

## Why the existing workaround is not enough

`mkdocs-minify-plugin` gained a `cache_safe` option in 2022. Reading its
source explains both its value and its edges:

```python
def _minified_asset(self, file_name, file_type, file_hash):
    hash_part = f".{file_hash[:6]}" if file_hash else ""
    min_part = ".min" if self.config[f"minify_{file_type}"] else ""
    return file_name.replace(f".{file_type}", f"{hash_part}{min_part}.{file_type}")
```

and the loop that uses it skips anything not explicitly listed:

```python
for i, extra_item in enumerate(config[extra]):
    file_path = str(extra_item)
    if file_path not in files_to_minify:
        continue
```

So:

1. **You must remember every asset.** `css_files` and `js_files` default to
   `None`; leave them unset and `cache_safe: true` quietly does nothing. A
   new stylesheet added six months later is unhashed and nobody notices.
2. **It is coupled to minification.** You are installing a minifier to get
   a fingerprint.
3. **It only produces a filename.** Nothing verifies that the bytes the CDN
   serves are the bytes you built.

## Why integrity attributes matter too

Renaming solves staleness. It does not solve *substitution*. If anything
between your origin and the visitor can rewrite `custom.901d2a0f.css` — a
misconfigured CDN, a compromised dependency host, a captive portal — the
filename still matches and the browser loads it without complaint.

[Subresource Integrity](https://developer.mozilla.org/docs/Web/Security/Subresource_Integrity)
closes that: the `integrity` attribute carries a base64 hash of the exact
bytes, and the browser refuses the resource if they differ. It costs one
attribute and is checked before the file is executed or applied.

Support is universal in current browsers (Chrome 45+, Firefox 43+, Safari 10+,
Edge 79+). Because SRI requires a CORS check for cross-origin resources, the
plugin also emits `crossorigin="anonymous"` — without it, a same-origin-looking
asset served through a CDN fails the check.

## The gap this fills

As of this writing, the MkDocs plugin catalog lists 233 plugins. Searching
their names and descriptions for `cache`, `fingerprint`, `hash`, `integrity`
and `sri` returns **no plugin at all** other than the `cache_safe` option
inside the minifier. The upstream issue and PR are both still open.

That is the gap: a single, dependency-free plugin that fingerprints your
assets and emits verifiable integrity attributes, without requiring you to
maintain a parallel list of filenames and without dragging in a minifier.
