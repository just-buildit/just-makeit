# Why zensical

## Background

The docs site was originally built with [zensical](https://zensical.org), then
briefly migrated to raw mkdocs-material during 0.19.29, and immediately switched
back. This document records why.

## The mkdocs 2.0 situation

MkDocs 1.x is unmaintained. MkDocs 2.0 — its successor — rewrites the core and
**removes the plugin system entirely**. That decision breaks mkdocs-material,
mkdocstrings, and every other plugin the ecosystem depends on. From the Material
team's own announcement:

> MkDocs 2.0 is incompatible with Material for MkDocs … existing `mkdocs.yml`
> files will currently not work with MkDocs 2.0. There is no migration path for
> existing projects.

Reference: <https://squidfunk.github.io/mkdocs-material/blog/2026/02/18/mkdocs-2.0/>

The same post recommends **zensical** as the maintained successor for MkDocs 1.x
sites using mkdocs-material.

## What zensical is

Zensical is a static-site generator that wraps mkdocs-material and maintains the
MkDocs 1.x plugin contract. It is developed by the same team that maintains
mkdocs-material.

Key properties for this project:

- Reads `mkdocs.yml` directly — no `zensical.toml` required.
- Supports the full mkdocs-material plugin surface (mkdocstrings, superfences,
  tabbed content, admonitions, etc.).
- `zensical build` / `zensical serve` are drop-in replacements for
  `mkdocs build` / `mkdocs serve`.

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
importable at config-parse time — mkdocs resolves `!!python/name:` entries in
`mkdocs.yml` during startup, before the build begins.

## The config file

`mkdocs.yml` is the single source of truth for the docs configuration. There is
no `zensical.toml`. Zensical reads `mkdocs.yml` natively, so the config is also
valid for any tool that remains compatible with mkdocs-material on MkDocs 1.x.

## Dev dependency

```toml
# pyproject.toml [dependency-groups.dev]
"zensical>=0.0.29; python_version >= '3.10'",
"mkdocstrings-python>=2.0.3; python_version >= '3.10'",
```

Zensical requires Python 3.10+, so both entries are gated. The 3.9 dev-dep floor
remains resolvable.

## CI

`.github/workflows/docs.yml` runs:

```yaml
- name: Build docs site
  env:
    PYTHONPATH: ${{ github.workspace }}
  run: uv run --no-project --with "zensical>=0.0.29" --with "mkdocstrings-python>=2.0" zensical build --clean
```

The `PYTHONPATH` env var serves the same role as the local `PYTHONPATH=.`
prefix.
