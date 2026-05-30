# Template gallery

Every `jm object` or `jm function` invocation produces a project shaped
to one of six patterns — a *preset*. This gallery shows what each
preset generates *before* you run it, so you can browse, find the shape
that matches your work, and run the exact command shown at the top of
the page.

Each page is titled with the CLI line that materialises it. Run it
verbatim, then open `<comp>_core.c` and replace the `/* TODO */`
markers with your algorithm.

Preset names describe **what the component does to data**, not what
domain you're working in. A "processor" is any 1:1 input→output
transform — a DSP filter, a Q15→float converter, and a CSV row
re-encoder all fit. Each preset page lists concrete examples across
domains so you can recognise your shape.

## Presets

| Preset        | Data-flow shape            | Concrete examples                                                | Page                                         |
| ------------- | -------------------------- | ---------------------------------------------------------------- | -------------------------------------------- |
| **processor** | input → output (1:1)       | DSP filter, Q15→float, running-average smoother, byte-to-token   | [`jm object NAME`](processor.md)             |
| **blockwise** | array input → array output | FFT, overlap-save filter, CSV batch transformer, image kernel    | [`jm object NAME --blockwise`](blockwise.md) |
| **generator** | () → output                | NCO, LFSR, counter, UUID, queue drainer, tokenizer               | [`jm object NAME --generator`](generator.md) |
| **consumer**  | input → ()                 | running mean, integrator, checksum, log writer, metric reporter  | [`jm object NAME --consumer`](consumer.md)   |
| **reader**    | external source → output   | file reader, CSV row reader, WAV/PNG loader, TCP socket consumer | [`jm object NAME --reader`](reader.md)       |
| **function**  | free C function (no class) | unit conversion, lookup, CRC, format detector, pure transform    | [`jm function FN --module MOD`](function.md) |

Need *variable-output* (zero or more outputs per call — peak detector,
event finder, syllable boundary detector)? That's a capability flag,
not its own preset — add `--variable-output --max-out N` to any
preset that has output and declare per-event fields with
repeatable `--result-field name:T`.

## How to read each page

Every preset page has the same five sections:

1. **Command** — the exact `jm` invocation. Copy, paste, run.
1. **What you get** — the generated files (`_core.h`, `_core.c`,
    `_ext.c` highlights, the test, the Python `.pyi`). Real output, not
    pseudocode.
1. **What you fill in** — the `/* TODO */` line(s) and what the
    finished body would look like for a typical algorithm of that
    shape.
1. **Python usage** — what `import` + call sites look like once
    `jm build` is done.
1. **Concrete types** — the allowlist for each slot the preset exposes
    (`--arg-type`, `--state`, `--init-param`, etc.). Rows link back to
    the master [Type slots](../types.md) page so you can cross-reference.
    A type that isn't in a slot's row is rejected by the CLI and by
    `jm bind`.

## Status

**Goal**: every preset's command produces a scaffold that compiles
and passes `jm build && jm test` immediately. Fill in the
`/* TODO */` body with your logic; everything around it stays green.

Honest state as of 0.13.23 (verified by running each preset's
command in a clean temp dir with a realistic snake_case name like
`my_filter`, building, and testing):

| Preset      | CLI today                                                   | Green today                                    | Phase 3a goal                                              |
| ----------- | ----------------------------------------------------------- | ---------------------------------------------- | ---------------------------------------------------------- |
| `processor` | `jm new --object my_filter ...`                             | ✅ build + 8 pytest pass                       | `--preset processor` (no-op alias)                         |
| `blockwise` | (not implementable today)                                   | ❌ renderer `KeyError` on `T[]` IO             | `--preset blockwise` + renderer support for `T[]` IO       |
| `generator` | `jm new --object my_nco --arg-type void ...`                | ✅ build + tests pass                          | `--preset generator`                                       |
| `consumer`  | `jm new --object my_acc --return-type void ...`             | ✅ build + 7 pytest pass                       | `--preset consumer`                                        |
| `reader`    | two-step: `jm new` then `jm object iq_reader --no-step ...` | ⚠ builds green; pytest fails on `NULL` default | `--preset reader` (single-step; auto-adds read/seek/close) |
| `function`  | `jm new` + `jm module` + `jm function FN --module MOD ...`  | ✅ build + ctest pass                          | (already shipped; no preset needed)                        |

**Known foot-guns blocking "green from day one" on 0.13.23** (verified
2026-05-30):

- **`blockwise`** (#86): renderer's `_CTYPE_META[return_type]` lookup
    throws `KeyError` on array types like `float _Complex[]`, so the
    preset is unreachable via CLI *or* hand-authored TOML. The
    preset page documents the design intent for review.
- **`reader`** (#88): generated pytest uses C `NULL` for `const char *`
    init-param defaults (e.g. `filepath:const char *`), which is
    undefined in Python. Build is green; auto-generated tests fail
    with `NameError: name 'NULL' is not defined`.
- **`reader`** CLI ergonomics: `--no-step` isn't accepted by
    `jm new --object`, only by the separate `jm object` verb — so
    the reader preset needs the two-step form today. Phase 3a's
    `--preset reader` consolidates this.
- **Placeholder-name collision** (#85): components named identically
    in snake_case and PascalCase (e.g. `NAME`, `name`) trigger a
    `<comp>_ext.c` symbol collision in lifecycle methods. Does **not**
    affect real-world snake_case names like `my_filter` or
    `iq_reader`. Mostly a gotcha for users who copy `NAME` verbatim
    from these pages.

A preset only ships when its page is fully worked, its CLI flag has a
regression test, and the bundled example confirms the skeleton compiles
and tests pass straight after scaffold.
