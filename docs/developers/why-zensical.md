# Why zensical — configuration & how-to

## Background

The docs site was originally built with [zensical](https://zensical.org), then
briefly migrated to raw mkdocs-material during 0.19.29, and immediately switched
back. This document records why, and serves as the authoritative how-to for
anyone maintaining the site.

## The mkdocs 2.0 situation

MkDocs 1.x is unmaintained. MkDocs 2.0 — its successor — rewrites the core and
removes the plugin system entirely. That decision breaks mkdocs-material,
mkdocstrings, and every other plugin the ecosystem depends on. From the Material
team's own announcement (February 2026):

> MkDocs 2.0 is incompatible with Material for MkDocs … existing `mkdocs.yml`
> files will currently not work with MkDocs 2.0. There is no migration path for
> existing projects.

Reference: <https://squidfunk.github.io/mkdocs-material/blog/2026/02/18/mkdocs-2.0/>

The same post recommends **zensical** as the maintained successor for MkDocs 1.x
sites using mkdocs-material.

## What zensical is

Zensical is a static-site generator developed by the mkdocs-material team. It
maintains the MkDocs 1.x plugin and extension contract so that existing
`mkdocs.yml` files work without modification.

- Reads `mkdocs.yml` directly — no `zensical.toml` required.
- `zensical build` / `zensical serve` are drop-in replacements for the
  corresponding `mkdocs` commands.

---

## Building and serving

```sh
# one-off build into site/
uv run --group dev zensical build --clean

# live-reload dev server (hot-reload on save)
uv run --group dev zensical serve
```

Or just `make docs` / `make docs-serve` — the Makefile targets set
`ZENSICAL_RUN = uv run --group dev` and run `scripts/copy_examples.py` first to
pull example READMEs into `docs/examples/`.

## Dev dependencies

```toml
# pyproject.toml [dependency-groups.dev]
"zensical>=0.0.29; python_version >= '3.10'",
"mkdocstrings-python>=2.0.3; python_version >= '3.10'",
```

Both are gated to `>= '3.10'` because zensical requires Python 3.10+. The 3.9
dev-dep floor stays resolvable.

## CI

`.github/workflows/docs.yml` runs:

```yaml
- name: Build docs site
  run: uv run --group dev zensical build --clean
```

No `PYTHONPATH` needed — just-makeit is installed editably into the venv so
`just_makeit._termynal_fence` is importable like any other package module.

---

## Full `mkdocs.yml` walkthrough

### Site metadata

```yaml
site_name: just-makeit
site_description: Python C extensions the easy way.
site_url: https://just-buildit.github.io/just-makeit/
repo_url: https://github.com/just-buildit/just-makeit
copyright: "Copyright &copy; 2026 just-buildit contributors — MIT License"

docs_dir: docs    # source markdown
site_dir: site    # built HTML output (gitignored)
```

### Navigation

```yaml
nav:
  - Home: index.md
  - Section name:
    - Page title: path/to/page.md
```

`nav` defines the sidebar order and titles exactly. Any `.md` file in `docs/`
that is **not** listed in `nav` is still built but won't appear in the sidebar
or be linked from any navigation element — zensical will warn about it. Pages
listed in `nav` but missing on disk produce a build error.

### Theme

```yaml
theme:
  name: material
  logo: assets/logo.svg
  favicon: assets/logo.svg
  language: en
  features:
    - announce.dismiss          # dismissible top announcement bar
    - content.code.annotate     # (1) callout annotations in code blocks
    - content.code.copy         # copy-to-clipboard button on code blocks
    - content.code.select       # line-range select in code blocks
    - content.footnote.tooltips # hover-preview footnotes
    - content.tabs.link         # sync === Tab === selections across the page
    - content.tooltips          # hover tooltips via title="" attributes
    - navigation.footer         # prev/next page links at the bottom
    - navigation.indexes        # section index pages (section/index.md)
    - navigation.instant        # SPA navigation — no full page reload
    - navigation.instant.prefetch  # hover-prefetch links
    - navigation.path           # breadcrumb trail under the page title
    - navigation.prune          # collapse unvisited sidebar sections
    - navigation.top            # "back to top" button
    - navigation.tracking       # update URL fragment on scroll
    - search.highlight          # highlight search terms on the target page
```

!!! note "navigation.instant and JavaScript"
    With `navigation.instant` active, `DOMContentLoaded` fires only once — on
    the initial hard load. Subsequent page navigations swap content without a
    full reload. Any JS that initialises widgets must subscribe to the
    mkdocs-material `document$` observable instead:

    ```js
    if (typeof document$ !== 'undefined') {
        document$.subscribe(init);  // fires on every navigation
    } else {
        document.addEventListener('DOMContentLoaded', init);  // fallback
    }
    ```

    See `docs/assets/termynal-init.js` for a real example.

### Color palette

```yaml
palette:
  - scheme: slate       # dark mode (default)
    primary: indigo
    accent: blue
    toggle:
      icon: material/weather-sunny
      name: Switch to light mode
  - scheme: default     # light mode
    primary: indigo
    accent: blue
    toggle:
      icon: material/weather-night
      name: Switch to dark mode
```

Toggle icons must be `material/*` or `fontawesome/*` icon paths from the
bundled icon sets. `lucide/*` is **not** available.

### Fonts

```yaml
font:
  text: Inter
  code: JetBrains Mono
```

Loaded from Google Fonts at build time; cached by the browser.

### Extra assets

```yaml
extra_css:
  - assets/mermaid-zoom.css     # click-to-zoom for mermaid diagrams
  - assets/termynal.css         # base terminal widget styles (from termynal.py 0.14.0)
  - assets/termynal-colors.css  # ANSI color spans + jm-card + jm-* utilities

extra_javascript:
  - assets/mermaid-zoom.js      # zoom overlay logic
  - assets/termynal.js          # termynal widget library (from termynal.py 0.14.0)
  - assets/termynal-init.js     # re-init on navigation.instant page changes
```

All paths are relative to `docs/`. Files must physically exist there — zensical
does not auto-bundle anything not listed here.

!!! warning "Plugin asset injection does not work"
    Some plugins (e.g. the official `termynal` mkdocs plugin) attempt to inject
    extra CSS/JS at build time via `on_config` hooks. Zensical does not propagate
    these dynamic additions to the rendered HTML. Always list assets explicitly
    in `extra_css` / `extra_javascript`.

### Social links

```yaml
extra:
  social:
    - icon: fontawesome/brands/github
      link: https://github.com/just-buildit/just-makeit
    - icon: fontawesome/brands/python
      link: https://pypi.org/project/just-makeit/
```

---

## Plugins

### `search`

Built-in full-text search. No configuration needed.

### `mkdocstrings`

Pulls Python docstrings into Markdown via `:::` directives.

```yaml
plugins:
  - mkdocstrings:
      handlers:
        python:
          options:
            show_source: false
```

Usage in a `.md` file:

```markdown
::: just_makeit._render.render
```

Requires `mkdocstrings-python>=2.0` as a dev dep. Handler options are documented
at <https://mkdocstrings.github.io/python/>.

---

## Markdown extensions

### Standard extensions

| Extension | What it enables |
|-----------|----------------|
| `admonition` | `!!! note "Title"`, `!!! tip`, `!!! warning`, etc. |
| `attr_list` | `{ .class #id key=val }` on blocks and inline spans |
| `def_list` | `Term\n:   Definition` definition lists |
| `footnotes` | `[^1]` inline references, rendered at page bottom |
| `md_in_html` | Markdown inside `<div markdown>` and `<span markdown>` |
| `tables` | GFM pipe tables |
| `toc` | Auto heading IDs; `permalink: true` adds a ¶ link |

### PyMdown extensions

| Extension | Config | What it enables |
|-----------|--------|----------------|
| `pymdownx.details` | — | `??? note` collapsible admonitions |
| `pymdownx.emoji` | `twemoji` index, `to_svg` generator | `:material-*:` `:fontawesome-*:` shortcodes |
| `pymdownx.highlight` | `anchor_linenums: true`, `line_spans: __span`, `pygments_lang_class: true` | Fenced code syntax highlighting, line anchors |
| `pymdownx.inlinehilite` | — | `#!python inline` syntax highlighting |
| `pymdownx.snippets` | — | `--8<-- "path/to/file"` file includes |
| `pymdownx.superfences` | custom fences below | Fenced blocks with custom renderers |
| `pymdownx.tabbed` | `alternate_style: true` | `=== "Tab A"` content tabs |

### Custom superfences

```yaml
pymdownx.superfences:
  custom_fences:
    - name: mermaid
      class: mermaid
      format: !!python/name:pymdownx.superfences.fence_code_format
    - name: termynal
      class: termynal
      format: !!python/name:just_makeit._termynal_fence.termynal_fence
```

`!!python/name:` is a YAML tag that resolves to a Python callable at
config-parse time. The module must be importable in the build environment — this
is why `just-makeit` is installed as an editable dev dep rather than invoked via
`uv run --no-project`.

To add a new custom fence, write a function with this signature:

```python
def my_fence(source, language, css_class, options, md, **kwargs) -> str:
    """Return an HTML string."""
    ...
```

Install it somewhere importable (or add it to `_termynal_fence.py`), then add
a `custom_fences` entry referencing it.

---

## Termynal blocks — markup reference

Fenced blocks with ` ```termynal ` (or `~~~termynal`) are rendered as animated
terminal widgets by `src/just_makeit/_termynal_fence.py`.

### Line types

| Prefix | Rendered as |
|--------|------------|
| `$ ` | Typed input (animated character-by-character) |
| `# ` | Comment line (no typing animation; dim style) |
| _(blank line)_ | Vertical gap spacer |
| _(anything else)_ | Static output line |

### Color markup

Wrap any fragment of any line with `{tag}...{/tag}`:

| Tag | Color | Typical use |
|-----|-------|-------------|
| `{g}` | Bold green | `Done!`, `passed`, cmake `Linking` |
| `{G}` | Green (normal weight) | cmake `Building ...` progress lines |
| `{c}` | Bold cyan | jm command hints, file paths |
| `{b}` | Bold blue | cmake `Copy extension module` |
| `{y}` | Bold yellow | warnings, version highlights |
| `{mark}` | Amber | installer arrows (`-->`, `==>`) |
| `{d}` | Dim gray | comment/separator lines |

Tags can span only a single line and do not nest. Everything outside a tag is
HTML-escaped automatically.

### Example

````markdown
```termynal
$ just-makeit new my_dsp --object engine
  {c}create{/c}  native/inc/engine/engine_core.h
  {c}create{/c}  native/src/engine/engine_core.c
  {c}create{/c}  native/src/engine/engine_ext.c
{g}Done!{/g}  cd my_dsp && make && make test

$ cd my_dsp && make && make test
{G}[100%] Linking C shared module engine.so{/G}
{g}100% tests passed{/g}, 0 tests failed out of 1
```
````

### Widget behavior

- The widget auto-plays when it scrolls into view (via `IntersectionObserver`
  inside `termynal.js`).
- `data-ty-typeDelay="40"` controls ms per character (typed lines).
- `data-ty-lineDelay="400"` controls the pause between lines.
- `data-ty-macos` adds the macOS traffic-light button chrome above the terminal.

### Why `jm-termy` and not `termy`

The official `termynal.js` library auto-initialises every `.termy` element on
script load. Our `termynal-init.js` also initialises terminal widgets via the
`document$` subscription (needed for `navigation.instant` SPA reloads). Using
`.termy` for both would cause double-initialisation — the animation would start,
reach the first pause, then restart from the beginning.

The superfence therefore emits `class="jm-termy"`, which the library's
auto-init selector (`.termy`) ignores. Only `termynal-init.js` handles
`.jm-termy` elements, exactly once per page load. The base CSS from `termynal.css`
applies via the `[data-termynal]` attribute selector and is unaffected by the
class name.

---

## Anchor slug rules

Zensical/mkdocs-material generates heading IDs with these rules:

1. Lowercase the heading text.
2. Strip all characters that are not alphanumeric, hyphens, or spaces.
3. Replace spaces (including spaces around punctuation) with a single `-`.

Practical consequences:

| Heading text | Generated anchor |
|---|---|
| `## Get it` | `#get-it` |
| `### Now — write it in C` | `#now-write-it-in-c` |
| `### Opaque state fields — pointers and handles` | `#opaque-state-fields-pointers-and-handles` |
| `## \`jm function\` --module mod` | `#jm-function-module-mod` |
| `## 3. The capsule generator (\`kind = "capsule"\`)` | `#3-the-capsule-generator-kind-capsule` |

Key gotcha: an em dash (`—`) with surrounding spaces becomes **a single `-`**,
not `--`. Double CLI flag dashes (`--module`) also collapse to single `-` because
`-` adjacent to a stripped character merges. When a link breaks with an "anchor
does not exist" warning, grep the built `site/` HTML for the actual `id="..."`.

---

## Adding a new page

1. Create `docs/path/to/page.md`.
2. Add it to `nav:` in `mkdocs.yml` at the right position.
3. If it lives under a section with an `index.md`, the section page is that
   `index.md` — do not create a duplicate.
4. Run `make docs-serve` and check the sidebar.

Pages not in `nav` build successfully but are unreachable from navigation; pages
in `nav` without a matching file fail the build.

---

## Assets reference

| File | Source | Purpose |
|------|--------|---------|
| `docs/assets/termynal.css` | termynal.py 0.14.0 | Terminal widget base styles (`[data-termynal]`, macOS chrome, font) |
| `docs/assets/termynal.js` | termynal.py 0.14.0 | `Termynal` class + auto-init for `.termy` elements |
| `docs/assets/termynal-colors.css` | hand-written | ANSI color spans (`.jm-termy .ty-*`), `.jm-card`, `.jm-*` utilities |
| `docs/assets/termynal-init.js` | hand-written | `document$`-aware init for `.jm-termy[data-termynal]` |
| `docs/assets/mermaid-zoom.css` | hand-written | Click-to-zoom overlay for mermaid diagrams |
| `docs/assets/mermaid-zoom.js` | hand-written | Zoom overlay logic |
| `docs/assets/logo.svg` | hand-drawn | Site logo and favicon |

To update `termynal.css` / `termynal.js` to a newer `termynal.py` release:

```sh
pip install "termynal>=0.14"
python -c "import termynal, pathlib; print(pathlib.Path(termynal.__file__).parent)"
# copy termynal.css and termynal.js from that directory to docs/assets/
```

## CSS utility classes

These classes are defined in `termynal-colors.css` and can be used anywhere in
`.md-typeset` prose via `attr_list`:

| Class | Color |
|-------|-------|
| `.jm-green` | Bold `#4ade80` (green) |
| `.jm-green-dim` | `#86efac` (muted green) |
| `.jm-cyan` | Bold `#22d3ee` (cyan) |
| `.jm-blue` | Bold `#60a5fa` (blue) |
| `.jm-yellow` | Bold `#facc15` (yellow) |
| `.jm-amber` | `#fb923c` (amber/orange) |
| `.jm-dim` | `#6b7280` (dim gray) |

Usage:

```markdown
**`just-makeit new`**{ .jm-green } scaffolds a project in seconds.
```

### `.jm-card`

A bordered, shadowed content card:

```html
<div class="jm-card" markdown>
<span class="jm-card-title">Card title</span>

Card body — full markdown rendered inside.
</div>
```

Requires `md_in_html` extension (already enabled).
