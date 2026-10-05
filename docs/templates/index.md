# Template gallery

Every `jm object` or `jm function` invocation produces a project shaped
to one of a handful of patterns — a *preset*. This gallery shows what
each preset generates *before* you run it, so you can browse, find the
shape that matches your work, and run the exact command shown at the top
of the page.

Each page is titled with the CLI line that materialises it. Run it
verbatim, then fill in the marked body: the inline `step()` in
`<comp>_core.h` for processor, generator and consumer; the
`<<IMPLEMENT>>` stubs in `<comp>_core.c` for blockwise, and for the
methods you add to a reader; and `native/src/<mod>/<fn>.c` for a
function.

Preset names describe **what the component does to data**, not what
domain you're working in. A "processor" is any 1:1 input→output
transform — a DSP filter, a Q15→float converter, and a CSV row
re-encoder all fit. Each preset page lists concrete examples across
domains so you can recognise your shape.

## Presets

| Preset        | Data-flow shape            | Concrete examples                                                | Page                                                |
| ------------- | -------------------------- | ---------------------------------------------------------------- | --------------------------------------------------- |
| **processor** | input → output (1:1)       | DSP filter, Q15→float, running-average smoother, byte-to-token   | [`jm object NAME --preset processor`](processor.md) |
| **generator** | () → output                | NCO, LFSR, counter, UUID, queue drainer, tokenizer               | [`jm object NAME --preset generator`](generator.md) |
| **consumer**  | input → ()                 | running mean, integrator, checksum, log writer, metric reporter  | [`jm object NAME --preset consumer`](consumer.md)   |
| **reader**    | external source → output   | file reader, CSV row reader, WAV/PNG loader, TCP socket consumer | [`jm object NAME --preset reader`](reader.md)       |
| **function**  | free C function (no class) | unit conversion, lookup, CRC, format detector, pure transform    | [`jm function FN --module MOD`](function.md)        |
| **blockwise** | array input → array output | FFT, overlap-save filter, CSV batch transformer, image kernel    | [`jm object NAME --preset blockwise`](blockwise.md) |

`--preset NAME` is shorthand for the underlying flag bundle (e.g.
`--preset generator` expands to `--arg-type void`); you can always pass
those flags directly instead.

`blockwise` (array-in → array-out) uses `--preset blockwise`. The
default element type is `float _Complex`; override with explicit
`--arg-type` and `--return-type` flags. See the
[blockwise page](blockwise.md) for the generated C/Python shapes and the
plan-once/execute-many FFTW pattern.

Need *variable-output* (zero or more outputs per call — peak detector,
event finder, syllable boundary detector)? That's a capability flag,
not its own preset — add `--variable-output --max-out N` to `jm object`
and it generates a `run()` method returning a variable-length array.
For per-event records, add a method instead —
`jm method NAME detect --result-field idx:size_t --result-field mag:float --return-type my_event_t`
— and declare `my_event_t` in the `_core.h`.

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

All six presets — `processor`, `generator`, `consumer`, `reader`,
`blockwise`, and `function` — build and test green straight from
scaffold. Fill in the `/* TODO */` body with your logic; everything
around it stays green. Use a real snake_case component name
(`my_filter`, `iq_reader`) when you run these; the examples use `NAME`
only as a placeholder.

## Refreshing a scaffold

The `_core.c` you edit is **sacred** — `jm apply` never overwrites it, and
the additive verbs (`jm method`, computed `jm property`) only *inject* a new
declaration and *append* a fresh stub, leaving your existing bodies intact.
Adding state with `jm add` is the exception: it rebuilds the object from the
manifest, so keep your body in `impl`/`create_impl` so it survives. To
rebuild a component from the manifest, run `jm regenerate <component>`. It
splices your hand-written bodies back in by name (best-effort), and
`--discard` throws them away for a clean scaffold; `git stash` first either
way. See [Type slots](../types.md) for the full sacred/glue contract.
