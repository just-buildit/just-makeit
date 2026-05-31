# Template gallery

Every `jm object` or `jm function` invocation produces a project shaped
to one of a handful of patterns — a *preset*. This gallery shows what
each preset generates *before* you run it, so you can browse, find the
shape that matches your work, and run the exact command shown at the top
of the page.

Each page is titled with the CLI line that materialises it. Run it
verbatim, then open `<comp>_core.c` and replace the `/* TODO */`
markers with your algorithm.

Preset names describe **what the component does to data**, not what
domain you're working in. A "processor" is any 1:1 input→output
transform — a DSP filter, a Q15→float converter, and a CSV row
re-encoder all fit. Each preset page lists concrete examples across
domains so you can recognise your shape.

## Presets

| Preset        | Data-flow shape            | Concrete examples                                                | Page                                                 |
| ------------- | -------------------------- | ---------------------------------------------------------------- | ---------------------------------------------------- |
| **processor** | input → output (1:1)       | DSP filter, Q15→float, running-average smoother, byte-to-token   | [`jm object NAME --preset processor`](processor.md)  |
| **generator** | () → output                | NCO, LFSR, counter, UUID, queue drainer, tokenizer               | [`jm object NAME --preset generator`](generator.md)  |
| **consumer**  | input → ()                 | running mean, integrator, checksum, log writer, metric reporter  | [`jm object NAME --preset consumer`](consumer.md)    |
| **reader**    | external source → output   | file reader, CSV row reader, WAV/PNG loader, TCP socket consumer | [`jm object NAME --preset reader`](reader.md)        |
| **function**  | free C function (no class) | unit conversion, lookup, CRC, format detector, pure transform    | [`jm function FN --module MOD`](function.md)         |
| **blockwise** | array input → array output | FFT, overlap-save filter, CSV batch transformer, image kernel    | [`jm object NAME --blockwise`](blockwise.md) *(n/a)* |

`--preset NAME` is shorthand for the underlying flag bundle (e.g.
`--preset generator` expands to `--arg-type void`); you can always pass
those flags directly instead.

`blockwise` (array-in → array-out) is **not yet available** — array
*return* types aren't supported, so there is no `--preset blockwise`.
Array *input* (`--arg-type "T[]"`) works fine. See the
[blockwise page](blockwise.md) for the workaround and current status.

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

As of v0.14, `processor`, `generator`, `consumer`, and `reader` build
and test green straight from scaffold, as does the `function` preset.
`blockwise` is the one exception — array *return* types aren't
implemented yet, so it has no `--preset` and the equivalent
`--return-type "T[]"` flag errors cleanly at parse time. Use a real
snake_case component name (`my_filter`, `iq_reader`) when you run these;
the examples use `NAME` only as a placeholder.

## Refreshing a scaffold

The `_core.c` you edit is **sacred** — `jm apply` never overwrites it, and
the additive verbs (`jm method`, computed `jm property`) only *inject* a new
declaration and *append* a fresh stub, leaving your existing bodies intact.
Adding state with `jm add` is the exception: it rebuilds the object from the
manifest, so keep your body in `impl`/`create_impl` so it survives. To
deliberately throw the scaffold away and rebuild it from the manifest, run
`jm regenerate <component>` (`git stash` first — it discards your
hand-written `_core.c`). See [Type slots](../types.md) for the full
sacred/glue contract.
