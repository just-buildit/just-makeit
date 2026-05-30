# Template gallery

## Two generalists, fully customizable via CLI

`just-makeit` has **two generalist templates** — one per CLI verb —
and every aspect of what each one produces is controllable through
CLI flags **or** by hand-authoring TOML fragments. The CLI is the
recommended path (presets, validators, errors); TOML editing is a
fully supported alternative. Every preset page on this gallery shows
both forms so the two paths stay interchangeable.

1. **`jm object NAME`** — materializes a C struct + Python class.
    - state struct + getters / setters
    - `create` / `destroy` / `reset` lifecycle
    - `step()` (inline, hot-path) + `steps()` (batched)
    - CPython binding (`_ext.c`)
    - CTest smoke test
    - Python benchmark
1. **`jm function FN --module MOD`** — materializes pure C + Python
    module-level functions.
    - C function declaration in `<mod>_core.h`
    - C function body stub in `<mod>_core.c`
    - Python module-level binding in `<mod>_ext.c`
    - type stub (`.pyi`)
    - pytest stub

## Common customization patterns, directly usable as templates

This gallery names and documents the common customization patterns —
the **presets**. Each preset is a known-good combination of CLI flags
that produces a specific shape; the page shows the command, the TOML
fragment that command writes (under `objects/NAME.toml` or
`modules/NAME.toml`), and the resulting scaffold. Use a preset as a
template by:

- **Running the preset command** — you get the documented scaffold,
    ready to fill in.
- **Hand-authoring the TOML fragment** — paste the fragment shown on
    each page into `objects/NAME.toml` (or `modules/NAME.toml`) and
    run `jm apply`. Same result as the CLI command.
- **Copying source files from the page** — the generated `_core.c`,
    header, and binding shown on each page are real output you can
    paste and adapt by hand if you prefer.
- **Passing the flags directly** — `--preset NAME` is shorthand;
    the underlying flag combination always works too.

Presets aren't a separate code path. They're documented names for
combinations of customizations that come up often. CLI, TOML, and
copy-by-hand are three doors to the same scaffold.

Preset names describe **what the component does to data**, not what
domain you're working in. A "processor" is any 1:1 input→output
transform — a DSP filter, a Q15→float converter, and a CSV row
re-encoder all fit. Each preset page lists concrete examples across
domains so you can recognise your shape.

## Object presets (customizations of `jm object`)

| Preset        | Flag combination                                                             | Concrete examples                                                | Page                                         |
| ------------- | ---------------------------------------------------------------------------- | ---------------------------------------------------------------- | -------------------------------------------- |
| **processor** | (identity — the default; no flags needed)                                    | DSP filter, Q15→float, running-average smoother, byte-to-token   | [`jm object NAME`](processor.md)             |
| **blockwise** | `--arg-type "T[]" --return-type "T[]"`                                       | FFT, overlap-save filter, CSV batch transformer, image kernel    | [`jm object NAME --blockwise`](blockwise.md) |
| **generator** | `--arg-type void`                                                            | NCO, LFSR, counter, UUID, queue drainer, tokenizer               | [`jm object NAME --generator`](generator.md) |
| **consumer**  | `--return-type void`                                                         | running mean, integrator, checksum, log writer, metric reporter  | [`jm object NAME --consumer`](consumer.md)   |
| **reader**    | `--no-step --init-param filepath:"const char *"` (drive via `jm method ...`) | file reader, CSV row reader, WAV/PNG loader, TCP socket consumer | [`jm object NAME --reader`](reader.md)       |

## Function (`jm function`)

| Verb         | What it produces                                                     | Concrete examples                                             | Page                                         |
| ------------ | -------------------------------------------------------------------- | ------------------------------------------------------------- | -------------------------------------------- |
| **function** | Free, module-level C function exposed to Python (no class, no state) | unit conversion, lookup, CRC, format detector, pure transform | [`jm function FN --module MOD`](function.md) |

Customizations on `jm function` (no preset names — the flags speak
for themselves): `--out-param` for writable output buffers,
`--out-type` for allocate-and-return ndarrays, `--result-field` for
record-returning shapes, `--inline` for header-only emission.

## Variable-output is a capability, not a preset

Need *variable-output* (zero or more outputs per call — peak detector,
event finder, syllable boundary detector)? It composes with any preset
that has output:

```sh
jm method <obj> <verb> --variable-output --max-out N \
    --result-field idx:size_t --result-field magnitude:float
```

This adds an extra method to the generalist; it doesn't replace it.

## How to read each page

Every preset page has the same five sections:

1. **Customization** — the exact flag combination and what part of the
    generalist it shapes.
1. **What you get** — the generated files (`_core.h`, `_core.c`,
    `_ext.c` highlights, the test, the Python `.pyi`). Real output, not
    pseudocode. Because the generalist is the same, the diff between
    pages is small and focused.
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

Honest state as of 0.13.23 (verified by running each command in a
clean temp dir, building, and testing):

| Preset      | CLI today                                                  | Green today                    | Phase 3a goal                                              |
| ----------- | ---------------------------------------------------------- | ------------------------------ | ---------------------------------------------------------- |
| `processor` | `jm new --object NAME ...`                                 | ✅ build + 8 pytest pass       | `--preset processor` (no-op alias)                         |
| `blockwise` | (not implementable today)                                  | ❌ renderer rejects `T[]` IO   | `--preset blockwise` + renderer support for `T[]` IO       |
| `generator` | `jm new --object NAME --arg-type void ...`                 | ✅ build + tests pass          | `--preset generator`                                       |
| `consumer`  | `jm new --object NAME --return-type void ...`              | ❌ `_ext.c` arg-count mismatch | `--preset consumer` + fix `NAME_destroy` codegen           |
| `reader`    | two-step: `jm new` then `jm object NAME --no-step ...`     | ❌ `_ext.c` arg-count mismatch | `--preset reader` (single-step; auto-adds read/seek/close) |
| `function`  | `jm new` + `jm module` + `jm function FN --module MOD ...` | ✅ build + ctest pass          | (already shipped; no preset needed)                        |

The named `--preset NAME` shorthand is a convenience layer arriving
in Phase 3a. Where the underlying flag combination works today,
running it produces the same scaffold.

**Known foot-guns blocking "all green from day one"** (verified
2026-05-30 on 0.13.23):

- **`consumer` and `reader`**: generated `_ext.c` declares
    `NAME_destroy(NAMEObject *self, PyObject *)` (2 args) but calls
    `NAME_destroy(self->handle)` (1 arg). Affects every component
    with `--return-type void` or `--no-step`. Build fails. Tracked
    for 0.14.
- **`blockwise`**: renderer's `_CTYPE_META[return_type]` lookup
    throws `KeyError` on array types like `float _Complex[]`, so the
    preset is unreachable via CLI *or* hand-authored TOML. The
    preset page documents the design intent for review.
- **`reader`**: `--no-step` isn't accepted by `jm new --object`,
    only by the separate `jm object` verb — so the reader preset
    needs the two-step form today. The `--reader` shorthand (Phase
    3a) absorbs both `--no-step` and the follow-up `jm method`
    declarations for `read` / `seek` / `close`.

If a page's command produces a non-green scaffold and isn't already
flagged above, that's a foot-gun to fix — open an issue.
