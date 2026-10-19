# How it works

## The ordering problem

MkDocs runs plugin events in a fixed sequence, and the sequence decides what
each plugin is allowed to see:

| Event | When | What we do |
| --- | --- | --- |
| `on_config` | after config load | validate options |
| `on_files` | after the file list is built, before any HTML | **decide every final filename and hash** |
| `on_post_page` | per page, after rendering | rewrite URLs, inject `integrity` |
| `on_post_build` | once, after everything | move files, rewrite stylesheet bytes |

The obvious design — "rename the files at the end, in `on_post_build`" — does
not work. By then every page has already been rendered with the old names,
and you would have to string-patch HTML you can no longer parse reliably.

So names are decided in `on_files`, from the source bytes, and then applied
twice: to the config (so themes render the hashed names without any template
work) and to the filesystem (so those names actually exist).

The sequence inside `on_files` is:

1. read every renameable asset's source bytes;
2. order the stylesheets so that every `@import` target is processed before
   the file that imports it;
3. for each, in that order: rewrite its `@import` targets to the final names,
   *then* hash the resulting bytes;
4. record the old→new name map and the SRI map;
5. rewrite `config.extra_css` / `config.extra_javascript`.

Then `on_post_page` relativizes the URLs for each page's depth, and
`on_post_build` writes the rewritten stylesheets and moves the files.

## Why `@import` is rewritten before hashing

This one is subtle and it was a real bug, caught only by opening the built
site in a browser.

Consider `main.css` containing `@import url("base.css")`. Renaming
`base.css` to `base.54d3bb45.css` means `main.css` has to change too, or the
import dangles. The naive order is:

1. hash `main.css` on its original bytes,
2. rename the file,
3. patch the `@import` inside it.

Step 3 changes the file's bytes *after* step 1 hashed them. The `integrity`
attribute no longer describes what is served, and the browser does what it
promises to do:

```
Failed to find a valid digest in the 'integrity' attribute for resource
'.../main.490ea01d.css' with computed SHA-384 integrity '...'.
The resource has been blocked.
```

Every stylesheet in the chain is discarded. The page renders unstyled, and no
build-time check notices — the HTML is valid, the filenames are correct, and
only a browser can tell you the hashes disagree.

The fix is to make "hash what you ship" true by construction: rewrite the
imports first, then hash. Because a stylesheet's bytes depend on its imports'
final names, and those names depend on *their* imports' names, the files have
to be processed in dependency order — leaves before parents:

```python
ready = sorted(p for p, d in remaining.items() if d <= emitted)
```

Cycles (`a.css` importing `b.css` importing `a.css`) would otherwise loop
forever. They are common enough in hand-written CSS that crashing is not an
acceptable answer, so the cycle is broken deterministically and reported:

```
WARNING - asset-integrity: @import cycle involving assets/a.css, assets/b.css;
one stylesheet may reference a stale name
```

## Why paths are relativized per page

`extra_css: [assets/x.css]` becomes `assets/x.<hash>.css` for a root page and
`../../assets/x.<hash>.css` for a page two levels deep. MkDocs normally
handles this with `normalize_url`, but that helper knows nothing about hashed
names, so this plugin does the arithmetic itself:

```python
relativize("assets/x.abc.css", "guide/deep/")  # -> "../../assets/x.abc.css"
```

References that escape the site root (`../../../etc/passwd`) are refused
rather than clamped. Silently turning an escape attempt into a valid path
would produce a link that resolves to the wrong file.

## What gets an integrity attribute

Only `<script>` and `<link>` tags, and only for local URLs. External URLs are
never touched: the plugin cannot hash bytes it does not have, and rewriting a
CDN reference would break the site outright.

For `<link>`, only these `rel` values are considered: `stylesheet`,
`preload`, `modulepreload`, `icon`. A `<link rel="canonical">` has an `href`
but nothing to verify.

The SRI table is keyed by original path, because that is what the rendered
HTML contains — but assets we renamed appear in the HTML under their *new*
name. Both keys are therefore populated, so a renamed asset does not lose its
integrity attribute. That was a real bug, now pinned by
`test_hashed_assets_also_get_integrity`.

## Interoperability

Plugin event handlers are sorted by descending `mkdocs_priority`, and this
plugin declares `mkdocs_priority = 100` so its `on_post_page` runs before
minifiers. That is a correctness requirement, not politeness: an integrity
attribute describes exact bytes, and a plugin that rewrites those bytes
afterwards invalidates it.

It also tolerates unquoted attributes. Minifiers strip quotes, producing
`href=assets/app.js`; an attribute parser that only understands `href="..."`
would see no URL at all and skip the tag — quietly dropping SRI exactly when
a minifier is present.

## Source map repair

Renaming `app.js` orphans its `//# sourceMappingURL=app.js.map` comment, which
breaks devtools. The comment is rewritten to the new name instead of being
dropped, so debugging a hashed build still works.

## Determinism

The hash is a pure function of file bytes and the configured algorithm, so two
builds of the same input produce identical filenames. There is no timestamp,
build counter, or random salt anywhere in the naming path — which means
`Cache-Control: immutable` is safe, and a rebuild that changes nothing
produces no new URLs.
