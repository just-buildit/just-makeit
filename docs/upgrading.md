# Upgrading an existing project

When a new version of `just-makeit` ships features that change the project
scaffold — new files, new `just-makeit.toml` keys, new build targets — existing
projects do not automatically get those additions. The upgrade system handles
this safely and idempotently.

______________________________________________________________________

## How it works

Every `just-makeit.toml` carries a `schema` version number:

```toml
[project]
name    = "my_dsp"
version = "0.1.0"
schema  = "7"
```

When `just-makeit` itself is updated, `CURRENT_SCHEMA` advances (it is `7`
currently). If your project's schema is behind, `just-makeit` will remind
you whenever you run a command that modifies the project:

```
warning: project schema is v4, current is v7.
Run 'just-makeit upgrade' to get new features.
```

Running `just-makeit upgrade` applies every pending migration in order, then
updates `schema` in `just-makeit.toml`.

______________________________________________________________________

## Running the upgrade

From your project root:

```sh
# 1. adopt jm's new clib_common.h -- take the render as-is; do not
#    hand-edit it. `status --check` names it as OUTDATED.
# 2. re-spell the component C. clib_common.h is EXCLUDED on purpose: jm's
#    render is already right, and its comment quotes the old spelling, so
#    a blanket pass would rewrite that prose into a false statement.
files=$(grep -rl 'float complex\|double complex' \
          native/inc native/src native/tests native/benchmarks \
        | grep -v '/clib_common\.h$')
[ -n "$files" ] && perl -pi -e '
    s/\blong double complex\b/long double _Complex/g;
    s/\bfloat complex\b/float _Complex/g;
    s/\bdouble complex\b/double _Complex/g;' $files
```

Then rebuild and run your tests. `complex` typed by **you** is still accepted
everywhere jm reads a type — in `just-makeit.toml`, on the CLI, and in a header
`jm bind` parses; it is resolved to `_Complex` before anything is rendered.

There is no `jm` command that performs this migration (gh-1248).

No TOML changes, no `jm upgrade` required.

______________________________________________________________________

______________________________________________________________________

## For project maintainers

If you ship a library built on `just-makeit` and your users upgrade jm
independently, remind them to run `just-makeit upgrade` after updating the
tool. The warning printed by commands like `just-makeit object` is there to
catch this automatically.
