# Template gallery

Every `jm object` or `jm function` invocation produces a project shaped
to one of seven patterns — a *preset*. This gallery shows what each
preset generates *before* you run it, so you can browse, find the shape
that matches your work, and run the exact command shown at the top of
the page.

Each page is titled with the CLI line that materialises it. Run it
verbatim, then open `<comp>_core.c` and replace the `/* TODO */`
markers with your algorithm.

## Presets

| Preset       | What it produces                                                                                                 | Page                                        |
| ------------ | ---------------------------------------------------------------------------------------------------------------- | ------------------------------------------- |
| **filter**   | Sample → sample. One state struct, an inline `step()`, an array `steps()`, getters/setters, full Python binding. | [`jm object NAME`](filter.md)               |
| **block**    | Array → array. Generates the block loop and a `steps()` that drives it.                                          | [`jm object NAME --block`](block.md)        |
| **source**   | No input; produces samples on demand. Good for NCOs, generators, decoded streams.                                | [`jm object NAME --source`](source.md)      |
| **sink**     | Consumes samples; no output. Good for accumulators, file writers, integrators.                                   | [`jm object NAME --sink`](sink.md)          |
| **reader**   | Opens a file or socket; exposes `read()` / `seek()` / `close()` instead of `step()`.                             | [`jm object NAME --reader`](reader.md)      |
| **detector** | Variable-output method that emits events on demand.                                                              | [`jm object NAME --detector`](detector.md)  |
| **library**  | No class; just module-level C functions with explicit input / output arrays.                                     | [`jm function FN --module MOD`](library.md) |

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

- **Today (shipped)**: `filter` and `library` are reachable from the
    current CLI. Their pages show real generated output.
- **Proposed**: `block`, `source`, `sink`, `reader`, `detector` are
    flag additions tracked in
    [`developers/wizard-design.md`](../developers/wizard-design.md).
    Their pages show the *intended* skeletons so the design can be
    reviewed before any code is written.

A preset only ships when its page is fully worked, its CLI flag has a
regression test, and the bundled example confirms the skeleton compiles
and tests pass straight after scaffold.
