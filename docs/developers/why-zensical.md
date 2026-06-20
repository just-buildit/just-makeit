# Why zensical

## Background

The docs site was originally built with [zensical](https://zensical.org), then
briefly migrated to raw mkdocs-material during 0.19.29, and immediately switched
back. This document records why.

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

## Plugins and extensions proven to work in this project

The following are active in `mkdocs.yml` and verified working under zensical
0.0.29 as of 0.19.29:

**Plugins**

| Plugin | Version | Notes |
|--------|---------|-------|
| `search` | bundled | full-text search |
| `mkdocstrings` | `mkdocstrings-python>=2.0` | Python API autodoc; `show_source: false` |

**Markdown extensions**

| Extension | Notes |
|-----------|-------|
| `admonition` | `!!! note`, `!!! tip`, etc. |
| `attr_list` | `{ .class }` on block and inline elements |
| `def_list` | definition lists |
| `footnotes` | `[^1]` footnotes |
| `md_in_html` | markdown inside `<div markdown>` blocks |
| `tables` | GFM-style pipes |
| `toc` | `permalink: true` |
| `pymdownx.details` | collapsible `??? note` blocks |
| `pymdownx.emoji` | `:material-*:` icon shortcodes via twemoji |
| `pymdownx.highlight` | syntax highlighting with anchor line numbers |
| `pymdownx.inlinehilite` | inline `#!python code` highlighting |
| `pymdownx.snippets` | `--8<--` file includes |
| `pymdownx.superfences` | custom fences (see below) |
| `pymdownx.tabbed` | `=== "Tab"` content tabs (`alternate_style: true`) |

**Custom superfences** (both require `PYTHONPATH=.` at build time)

| Fence name | Handler | Notes |
|------------|---------|-------|
| `mermaid` | `pymdownx.superfences.fence_code_format` | diagram blocks |
| `termynal` | `termynal_fence.termynal_fence` | animated terminal widget |

## How we use it

```sh
# local build
PYTHONPATH=. uv run --no-project \
  --with "zensical>=0.0.29" \
  --with "mkdocstrings-python>=2.0" \
  zensical build --clean

# live reload
PYTHONPATH=. uv run --no-project \
  --with "zensical>=0.0.29" \
  --with "mkdocstrings-python>=2.0" \
  zensical serve
```

Or just `make docs` / `make docs-serve`.

`PYTHONPATH=.` is required so that `termynal_fence.py` (at the project root) is
importable at config-parse time — zensical resolves `!!python/name:` entries in
`mkdocs.yml` during startup, before the build begins.

## Dev dependencies

```toml
# pyproject.toml [dependency-groups.dev]
"zensical>=0.0.29; python_version >= '3.10'",
"mkdocstrings-python>=2.0.3; python_version >= '3.10'",
```

Zensical requires Python 3.10+, so both entries are gated to keep the 3.9
dev-dep floor resolvable.

## CI

`.github/workflows/docs.yml` runs:

```yaml
- name: Build docs site
  env:
    PYTHONPATH: ${{ github.workspace }}
  run: |
    uv run --no-project \
      --with "zensical>=0.0.29" \
      --with "mkdocstrings-python>=2.0" \
      zensical build --clean
```

`PYTHONPATH` is set as an environment variable rather than a prefix because
GitHub Actions does not expand shell variable prefixes in the `run:` block the
same way a login shell does.
