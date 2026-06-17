# The object-of-objects pattern: capsule & composer generators

A comprehensive guide to `kind = "capsule"` and `kind = "composer"` — the
generators that turn jm from a *one-off binding scaffolder* into a *templating
engine that composes objects of objects*. From one declarative manifest jm emits
a self-contained extension module whose ergonomic API lives **in the `.so`** (no
forced pure-Python wrapper), with an optional take-it-or-leave-it `.pyi`, JSON
(de)serialization, and a command-line tool — leaving only the DSP kernels
hand-written.

______________________________________________________________________

## Index

- [1. Executive summary](#1-executive-summary)
    - [1.1 The problem](#11-the-problem)
    - [1.2 The solution](#12-the-solution)
    - [1.3 What it enables](#13-what-it-enables)
    - [1.4 The feature trio at a glance](#14-the-feature-trio-at-a-glance)
- [2. Mental model](#2-mental-model)
    - [2.1 Two shapes: capsule and composer](#21-two-shapes-capsule-and-composer)
    - [2.2 The generated-vs-hand-written seam](#22-the-generated-vs-hand-written-seam)
    - [2.3 The enum SSOT](#23-the-enum-ssot)
- [3. The capsule generator (`kind = "capsule"`)](#3-the-capsule-generator-kind--capsule)
    - [3.1 What it generates](#31-what-it-generates)
    - [3.2 Manifest schema](#32-manifest-schema)
    - [3.3 Capsule mechanics & lifetime](#33-capsule-mechanics--lifetime)
    - [3.4 Worked example: `ddc_fn`](#34-worked-example-ddc_fn)
- [4. The composer generator (`kind = "composer"`)](#4-the-composer-generator-kind--composer)
    - [4.1 Overview: the four OO types](#41-overview-the-four-oo-types)
    - [4.2 Manifest schema](#42-manifest-schema)
    - [4.3 The source type](#43-the-source-type)
    - [4.4 The segment type](#44-the-segment-type)
    - [4.5 The timeline type](#45-the-timeline-type)
    - [4.6 The composer type](#46-the-composer-type)
    - [4.7 JSON faces (generated vs delegated)](#47-json-faces-generated-vs-delegated)
    - [4.8 The CLI face](#48-the-cli-face)
    - [4.9 apply materialization](#49-apply-materialization)
- [5. Lifecycle & memory invariants](#5-lifecycle--memory-invariants)
- [6. Validation discipline: reference-first](#6-validation-discipline-reference-first)
- [7. Manifest reference](#7-manifest-reference)
- [8. When to use which](#8-when-to-use-which)
- [9. Gaps & roadmap](#9-gaps--roadmap)
- [10. Appendix: the build arc](#10-appendix-the-build-arc)

______________________________________________________________________

## 1. Executive summary

### 1.1 The problem

A DSP algorithm is written once in C. Exposing it is where the cost piles up —
and for *composition* subsystems it explodes. A waveform composer, for example,
historically carried the same source/segment/spec model across **three
hand-written faces**:

- a C command-line tool (`wfmgen.c`, ~700 lines);
- a hand-written CPython extension over opaque capsules (~1250 lines);
- a pure-Python OO layer (`Synth`/`Segment`/`Timeline`/`Composer`, ~1200 lines).

Two failure modes follow. First, the **string-enum tables** (`"tone"`, `"bpsk"`,
…) were duplicated in **four** places, kept in sync only by tests — the number
one drift risk. Second, the ergonomic API was *forced into pure Python*, which
violates the "the C-extension type **is** the public API; `__init__.py` is
re-export only" rule and the "drop a `.so` on a target and go" goal: a bare
`.so` import gave only flat capsule functions, never `Composer`.

### 1.2 The solution

Two layered generators, each driven entirely by the manifest:

- **`kind = "capsule"`** — generate "free functions over an opaque `PyCapsule`
    state" extensions (`create` / `execute` / `reset` / `destroy` / `get_*` /
    `set_*`). The runtime skeleton.
- **`kind = "composer"`** — built on the capsule skeleton, generate the
    ergonomic **CPython OO types** (`Synth`/`Segment`/`Timeline`/`Composer` +
    factories), the JSON (de)serializer from the enum single-source-of-truth, and
    an optional command-line tool. The kernels (accumulation, synthesis, noise
    resolution) stay hand-written; everything around them is generated.

### 1.3 What it enables

- **A self-contained `.so`.** A bare `import` gives the full typed OO surface —
    no forced Python wrapper. The `.pyi` is take-it-or-leave-it.
- **One enum definition.** Every face (OO validation, JSON, CLI flags) reads the
    one `[[enum]]` table. The four-way drift risk collapses to a single source.
- **Generic, not bespoke.** Nothing is waveform-specific. The same generator
    composes *any* "object of objects" — a future DDC composer drops in with a
    manifest and zero hand-written glue.
- **Three faces from one model.** OO types, JSON spec round-trip, and a CLI tool
    all fall out of the same `source.fields`/`segment.fields` + enum SSOT.
- **Hand-write only the algorithm.** The DSP kernels remain hand-owned; the
    binding, marshalling, types, serialization, build wiring, and stubs are
    generated and reconciled by `jm apply` / guarded by `jm status --check`.

### 1.4 The feature trio at a glance

| Issue  | Feature             | Role                                        |
| ------ | ------------------- | ------------------------------------------- |
| gh-285 | `[[enum]]` SSOT     | one string-enum table feeding every face    |
| gh-286 | `kind = "capsule"`  | free-functions-over-`PyCapsule` skeleton    |
| gh-287 | `kind = "composer"` | OO types + JSON + CLI, built on the capsule |

______________________________________________________________________

## 2. Mental model

### 2.1 Two shapes: capsule and composer

Both expose C state through an **opaque `PyCapsule`** rather than copying it into
Python. They differ in the surface they present:

- A **capsule module** presents *free functions*. State is a capsule handle
    passed as the first argument: `state = create(...); y = execute(state, x, out); destroy(state)`. This is the lift of a hand-written
    "functions-over-a-capsule" extension.

- A **composer module** presents *CPython types*. The capsule lives **inside**
    the `Composer` object; the user manipulates `Synth`/`Segment`/`Timeline`
    objects and calls methods. The composer is "an object (the Composer) made of
    objects (Segments, each made of Synths)" — hence *object of objects*.

### 2.2 The generated-vs-hand-written seam

The dividing line is deliberate and stable:

| Generated by jm                                                                     | Hand-written (stays in `_core.c` / app-side)        |
| ----------------------------------------------------------------------------------- | --------------------------------------------------- |
| Enum int↔string tables (the SSOT)                                                   | The accumulation kernel                             |
| Capsule mechanics + `execute`/`reset`/`destroy`/`get_`/`set_` + GIL + numpy marshal | The per-source resolution (e.g. shared noise floor) |
| Source/segment marshalling + a `bytes` buffer                                       | The synthesis kernels                               |
| The `Synth`/`Segment`/`Timeline`/`Composer` types + factories                       | Container/transport writers (BLUE / SigMF / zmq)    |
| JSON to/from, the CLI face, CMake, `.pyi`                                           | —                                                   |

The rule of thumb: **jm generates everything that is a mechanical projection of
the manifest; you hand-write only what encodes the algorithm.**

### 2.3 The enum SSOT

A top-level `[[enum]]` block names a string-enum once; the order **is** the C
int (append-only):

```toml
[[enum]]
name = "wfm_type"
values = ["tone", "noise", "pn", "bpsk", "qpsk", "chirp", "bits"]
```

The composer generator emits one C table per referenced enum
(`_enum_wfm_type[]`) plus a shared `_enum_index()` lookup, and **only** for the
enums the module's fields actually reference. Every face — type-attribute
validation, JSON ser/de, CLI choice flags — resolves names↔ints through these
tables. There is no second copy to drift against.

______________________________________________________________________

## 3. The capsule generator (`kind = "capsule"`)

### 3.1 What it generates

For a capsule module, `jm apply` materializes three glue files:

- `native/src/<mod>/<mod>_ext.c` — the binding: capsule wrapper struct, a
    use-after-destroy guard, `<backing>_create`, a variable-output `execute`
    (exact-dtype numpy in/out, **zero-copy `out[:n]` view**, optional GIL
    release), bare void methods (`reset`), `destroy`, and `get_`/`set_`
    accessors; plus the `PyMethodDef` table and `PyInit`.
- `native/src/<mod>/CMakeLists.txt` — a Python-extension target linking the
    `link = true` dependency cores.
- `src/<pkg>/<package>/<leaf>.pyi` — a typed stub.

The kernels stay in the backing object's `_core.c`.

### 3.2 Manifest schema

```toml
[module.ddc_fn]
kind         = "capsule"
backing      = "ddcr"                     # wraps ddcr_state_t, calls ddcr_*
capsule_name = "doppler.ddc.ddcr_state"   # the PyCapsule name string
package      = "ddc"                      # .so/.pyi land in a sibling package
header       = "ddc/ddc_core.h"           # backing API header (override)
depends_on   = [{ name = "ddc", link = true }, …]   # cores linked onto the .so
extra_link_libs = ["m"]

[[module.ddc_fn.init_params]]             # -> ddcr_create(norm_freq, rate)
name = "norm_freq"
type = "double"

[[module.ddc_fn.methods]]                 # variable-output execute
name        = "execute"
arg_type    = "float[]"
return_type = "float _Complex[]"
caller_out  = true
nogil       = true

[[module.ddc_fn.properties]]              # -> ddcr_get_/set_norm_freq
name     = "norm_freq"
type     = "double"
writable = true
```

Key points:

- `backing` is the symbol prefix and the `<backing>_state_t` it wraps.
- `package` lets the `.so`/`.pyi` build into a *sibling* package directory
    (doppler's `ddc_fn` builds into the `ddc` package so `doppler.ddc` can
    `from .ddc_fn import ddcr_*`). When unset, the module's own path is used.
- `depends_on` entries with `link = true` add each `<name>_core` to the `.so`'s
    link line (CMake does not pull OBJECT-lib objects transitively into a final
    `.so`, so the link must be direct).

### 3.3 Capsule mechanics & lifetime

The generated binding wraps the state in a small struct with a `destroyed` flag:

```c
typedef struct { <backing>_state_t *state; int destroyed; } _wrap_t;
```

- `create` allocates the wrapper, calls `<backing>_create`, and returns a
    `PyCapsule` whose destructor frees the state if it was not already explicitly
    destroyed (so the GC reclaims a forgotten handle).
- `_get_wrap` raises `RuntimeError` if the handle was already destroyed — a
    clean use-after-destroy guard rather than a crash.
- `execute` requires the *exact* output dtype (no silent cast — a cast would
    write into a temp copy instead of the caller's buffer), runs the kernel with
    the GIL released when `nogil = true`, and returns a **zero-copy** `out[:n]`
    slice of the caller's buffer.

### 3.4 Worked example: `ddc_fn`

doppler's `ddc_fn` (the functional DDCR down-converter) was the pilot. Migrating
it from a 400-line hand-written `no_generate` extension to `kind = "capsule"`
deleted the hand code; `jm apply` regenerated a byte-equivalent binding that
compiled clean, passed all existing tests, and kept the
`from doppler.ddc import ddcr_*` re-exports working.

______________________________________________________________________

## 4. The composer generator (`kind = "composer"`)

### 4.1 Overview: the four OO types

A composer module emits four CPython types **into the `.so`**:

- **`Synth`** (the *source* type) — one source's configuration (waveform fields
    - enums + an optional `bytes` pattern). Plus factory functions
        (`tone()`/`bpsk()`/…).
- **`Segment`** — a list of sources summed over a span, plus segment scalars
    (`fs`/`num_samples`/`off_samples`). Built inline from one source's kwargs, or
    via the `Segment.sum(*sources, …)` classmethod.
- **`Timeline`** — an ordered, iterable run of segments played back-to-back
    (`add`/iter/`len`/subscript).
- **`Composer`** — holds the backing `<backing>_state_t`; built from a segment
    list / a single `Segment` / a `Timeline` / single-segment kwargs; `execute`,
    `compose`, `segments`/`repeat`/`continuous`, JSON, context manager.

### 4.2 Manifest schema

```toml
[module.wfm_compose]
kind         = "composer"
backing      = "wfm_compose"               # wfm_compose_state_t + wfm_compose_*
capsule_name = "doppler.wfm.compose_state"
package      = "wfm"
header       = "wfm/wfm_compose.h"
composes     = ["wfm_synth"]               # the generator source object
sample_type  = true                        # opt into the jm-app output axes
depends_on   = [{ name = "wfm_compose", link = true }, … ]
extra_link_libs = ["wfm_cjson", "m"]

[module.wfm_compose.source]
object    = "wfm_synth"
struct    = "wfm_source_t"                 # the C struct the type wraps
type_name = "Synth"                        # the Python class name
fields = [
  { name = "type", type = "int", enum = "wfm_type", default = "tone" },
  { name = "freq", type = "double", default = "0.0" },
  { name = "bits", type = "uint8_t*", bytes = true },
  …
]

[module.wfm_compose.segment]
type_name = "Segment"
struct    = "wfm_segment_t"
sources   = "multi"
fields = [
  { name = "fs", type = "double", default = "1e6" },
  { name = "num_samples", type = "size_t", default = "1024" },
  { name = "off_samples", type = "size_t", default = "0" },
]

[module.wfm_compose.timeline]
type_name = "Timeline"
loop = ["once", "repeat", "continuous"]

[module.wfm_compose.oo]
factories          = ["tone", "noise", "pn", "bpsk", "qpsk", "chirp", "bits"]
emit               = "ctypes"              # emit CPython types in the .so
discriminant       = "type"                # the enum field a factory presets
composer_type_name = "Composer"

[module.wfm_compose.json]
enabled = true                             # generate to_json/from_json/from_file

[module.wfm_compose.cli]
enabled = true                             # opt-in c-face command-line tool
name    = "wfmgen"
```

The **`fields`** list is the keystone: one ordered list of
`{name, type, enum?, default?, bytes?}` per source/segment determines the C
struct marshalling, the type's getset slots, the JSON shape, and the CLI flags —
all from a single declaration. (`type` is the C type; `enum` tags a field as a
string-enum resolved through the SSOT; `bytes` marks an owned byte buffer.)

### 4.3 The source type

`render_source_type` emits a `PyTypeObject` wrapping the backing C struct
(`source.struct`) plus a standalone `fs`:

- a keyword `tp_init` parsing every field (defaults from the manifest);
- per-field getset: **enum fields cross as validated strings** (int↔name via the
    SSOT table; an invalid value raises `ValueError` on both init *and*
    assignment), scalars as numbers, the `bytes` field as Python `bytes`;
- `dealloc` frees the owned bits buffer;
- factory module functions: each preset the `oo.discriminant` enum field to the
    factory name and forward the rest (so `tone(freq=…)` is
    `Synth(type="tone", freq=…)`).

### 4.4 The segment type

A `Segment` holds a Python list of source objects plus the segment scalars — it
needs **no backing struct** (the `wfm_segment_t[]` is built later by the
Composer), which keeps it fully generic. Two construction faces match the
hand-written original:

- inline single-source: `Segment(type="tone", num_samples=…)` forwards the
    source fields to the source type and wraps the one result;
- multi-source: `Segment.sum(*sources, num_samples=…)` (a classmethod that
    type-checks each positional source).

`Segment.add(*others) -> Timeline` sequences segments in time.

### 4.5 The timeline type

A thin sequence wrapper — `add` (chainable), `__iter__`, `__len__`, subscript —
the fluent face of the segment list the composer already sequences.

### 4.6 The composer type

`render_composer_type` is where the OO objects drive the real kernel:

- `__init__` dispatches single-segment-from-kwargs / a lone `Segment` / a
    `Timeline`-or-list, builds a **transient** `<segment_struct>[]` from the OO
    objects, and calls `<backing>_create`.
- `execute(n)` returns a zero-copy cf32 slice (GIL released across the kernel).
- `compose(block=4096)` drains a finite spec via `PyArray_Concatenate` (raises
    on a `continuous` spec).
- `segments` / `repeat` / `continuous` reflect the **resolved** spec back as
    rebuilt OO objects.
- `close` / `__enter__` / `__exit__` / `dealloc` destroy the backing state.

The transient `<segment_struct>[]` **aliases** each source's `bits` pointer;
`<backing>_create` deep-copies (see [§5](#5-lifecycle--memory-invariants)), so
the transient arrays are freed straight after and ownership stays with the
`Synth` objects.

### 4.7 JSON faces (generated vs delegated)

With `[module.X.json] enabled = true` the composer gets
`from_json`/`from_file`/`to_json`. There are two modes:

- **Generated (default)** — a generic, SSOT-driven ser/de built from
    `source.fields`/`segment.fields`. One uniform schema:
    `{version, repeat, continuous, segments: [ {<scalar fields>, sources: [{<source fields>}]} ]}`; enum fields serialize as their SSOT string, a `bytes` field as
    a JSON int array, everything else numeric. Round-trips by construction;
    reusable by any composer with zero hand-written wire code. (Uses cJSON; the
    project provides the header via `json.include_dir` and links its json lib via
    `extra_link_libs`.)
- **Delegated (escape hatch)** — set `json.to_json_fn` (plus optional
    `from_json_fn`/`from_file_fn`/`to_json_trailing`) to call a hand-written C
    serializer instead. This exists for projects that need a *specific*,
    pre-existing wire format byte-for-byte (e.g. a domain schema with conditional
    field emission that a generic generator cannot reproduce).

### 4.8 The CLI face

With `[module.X.cli] enabled = true` the composer gets an opt-in **standalone C
command-line tool** (`render_cli`): a pure-C `main()` — no Python — that

- builds the composer from **source/segment-field flags** (`--type`, `--freq`,
    `--num_samples`, …) or from a JSON spec via `--from-file`;
- streams samples in the chosen wire format, reusing **`jm app`'s output axes**
    verbatim (`--sample_type` / `--file-type` / `--endian`);
- validates enum flags against the SSOT `_enum_*` tables — no hand-written flag
    tables.

The CMake `add_executable` target is emitted **outside** the `BUILD_PYTHON`
guard (it is a C tool, not a Python module).

### 4.9 apply materialization

`jm apply` routes a composer module to the composer materializer (no
object-group scaffold). It writes `<mod>_ext.c` (the assembled module: enum
tables + the four types + factory table + `PyInit`), `CMakeLists.txt`,
`<leaf>.pyi`, and (when enabled) `<mod>_cli.c`, then splices the top-level
`add_subdirectory`. These are glue: `jm apply` reconciles them on every run and
`jm status --check` guards them. The manifest round-trips through `save`/`load`
so a project is reproducible from the manifest plus the hand-written kernels
alone.

______________________________________________________________________

## 5. Lifecycle & memory invariants

The hard-won rules that keep the generated C correct:

- **Deep-copy, then free.** `<backing>_create` makes its own copy of the
    segment list *including* each source's `bits`. So a caller (the composer
    `__init__`, the JSON parser, the CLI) may build a transient array that
    *aliases* the source buffers, call create, then free the transient — the
    composer owns its own copy.

- **Alias-then-copier ordering (the use-after-free lesson).** When you alias a
    Python object's owned buffer into a transient struct and then call a C copier,
    the owner **must stay alive until after the copier returns**. The composer
    `__init__` originally dropped the only reference to the freshly-built
    `Segment`→`Synth` chain *before* `create` ran; in the single-segment-kwargs
    path that freed the `bits` out from under create's deep-copy. The fix keeps
    the segment list alive across `create`. Generalize: *any "alias into a Python
    object's owned buffer, then call a C copier" pattern must keep the owner alive
    past the copier.*

- **Zero-copy numpy views pin the buffer's owner, not the buffer.** A returned
    `out[:n]` view keeps the array object alive; if a later call could `realloc`
    that array's data, return an independent numpy-owned array instead.

- **`bytes` ownership is single.** The source type owns `src.bits` (freed in
    `dealloc`). Rebuilt sources (from the resolved spec or JSON) deep-copy so each
    object owns its own. The CLI frees its flag-built buffer after `create`
    (create deep-copied) so the tool is Valgrind-clean; `free(NULL)` is a no-op
    when the flag is absent.

- **GIL release** across the pure-C kernel (`nogil`) is safe under the
    one-state-per-call contract: the kernel touches only this stream's state and
    the caller's buffers, references to the numpy arrays are held, and pointers
    are fetched before the block.

______________________________________________________________________

## 6. Validation discipline: reference-first

Unlike the capsule (which cloned a known-good hand-written extension), the
composer OO types had **no existing C reference** — the behaviour lived in
validated Python. So the discipline was:

- **Compile and run, don't assert-on-string.** Each generated C type was
    compiled against the project's real structs and exercised, not merely matched
    against expected substrings. (Structural string tests still exist as a
    fast, compiler-free unit gate.)
- **Byte-exact against the reference.** Output was compared sample-for-sample to
    the hand-written Python reference across every spec shape (single waveform,
    multi-segment timeline, multi-source sum, a bits pattern, a chirp), plus JSON
    round-trip and block-wise `execute == compose`.
- **The full chain in the real project.** The end-state proof ran
    `manifest → jm apply → the project's own CMake build → import → samples`, and
    for the CLI `→ build the executable → run it`, confirming byte-exactness at
    every layer.
- **Pilot link recipe.** When linking a standalone probe against a large
    project's object tree, link the `*_core` objects (minus test/bench mains and
    any core that drags an unused transport seam — use the no-op *stub* core
    instead), plus the vendored json objects, plus `-lm`. Undefined Python/numpy
    symbols resolve at import.

______________________________________________________________________

## 7. Manifest reference

**Shared (capsule & composer):**

| key               | meaning                                                                |
| ----------------- | ---------------------------------------------------------------------- |
| `kind`            | `"capsule"` or `"composer"`                                            |
| `backing`         | symbol prefix; wraps `<backing>_state_t`, calls `<backing>_*`          |
| `capsule_name`    | the `PyCapsule` name string                                            |
| `package`         | package dir the `.so`/`.pyi` build into (default: module path)         |
| `header`          | backing C API header to include (default `<backing>/<backing>_core.h`) |
| `depends_on`      | `[{name, link=true}, …]` — cores linked onto the `.so`                 |
| `extra_link_libs` | non-core link targets (e.g. `"m"`, a vendored json lib)                |

**Capsule only:** `[[module.X.init_params]]` (`name`/`type`/`default?`),
`[[module.X.methods]]` (`name`, `arg_type?`, `return_type?`, `caller_out?`,
`nogil?`), `[[module.X.properties]]` (`name`/`type`/`writable?`).

**Composer only:**

| table / key    | meaning                                                                                                                                       |
| -------------- | --------------------------------------------------------------------------------------------------------------------------------------------- |
| `composes`     | the generator source object(s) the composer reuses                                                                                            |
| `sample_type`  | opt into the jm-app output axes on the CLI                                                                                                    |
| `[X.source]`   | `object`, `struct`, `type_name`, `fields[]`                                                                                                   |
| `[X.segment]`  | `type_name`, `struct`, `sources` (`"multi"`/`"single"`), `fields[]`; optional `sources_member`/`count_member` (default `sources`/`n_sources`) |
| `[X.timeline]` | `type_name`, `loop[]`                                                                                                                         |
| `[X.oo]`       | `factories[]`, `emit` (`"ctypes"`), `discriminant`, `composer_type_name`                                                                      |
| `[X.json]`     | `enabled`; optional `to_json_fn`/`from_json_fn`/`from_file_fn`/`to_json_trailing` (delegation), `header`/`include_dir` (generated path)       |
| `[X.cli]`      | `enabled`, `name`                                                                                                                             |

A **field** entry (`source.fields`/`segment.fields`):
`{ name, type, enum?, default?, bytes? }` — one declaration drives the
marshalling, the type slots, the JSON shape, and the CLI flag.

______________________________________________________________________

## 8. When to use which

- Reach for **`kind = "capsule"`** when the natural API is *free functions over
    an opaque handle* — a streaming processor whose state you create once and feed
    blocks (a down-converter, a filter chain), especially when you want a flat
    binding that a sibling package re-exports.

- Reach for **`kind = "composer"`** when you are *composing objects of objects*
    — building a higher-level object out of a list of configured source objects,
    sequenced and serialized: waveform composition today; equally a multi-stage
    channelizer, a scenario/timeline builder over any generator object, or a DDC
    composer. If you want the ergonomic OO surface to live in the `.so` (not a
    pure-Python wrapper), plus JSON round-trip and a CLI, this is the shape.

- Stay with a plain object-group module / `jm app` when there is a single
    object with a simple scalar/blockwise/generator I/O shape and no composition.

______________________________________________________________________

## 9. Gaps & roadmap

- **JSON delegation vs generation.** The generated JSON ser/de is generic and
    SSOT-driven. A project that must reproduce a *domain-specific* wire schema
    byte-for-byte (conditional field emission, bespoke layouts) uses the
    `json.to_json_fn` delegation hatch — at the cost of keeping that one
    hand-written serializer (and its enum copy).
- **The last enum copy.** When the CLI's `--from-file`/`--record` delegate to a
    backing C JSON parser, that parser keeps its own enum table. Collapsing the
    final copy to zero needs a *standalone* generated C ser/de (enum tables +
    to/from JSON over the backing structs) shared by both the extension and the
    CLI — a deferrable follow-up.
- **CLI faces.** The c-face CLI is generated; console/pep723 faces are not (the
    OO `Composer` already gives Python users the API directly).

______________________________________________________________________

## 10. Appendix: the build arc

| Issue / PR | Slice                                                        |
| ---------- | ------------------------------------------------------------ |
| gh-285     | `[[enum]]` SSOT                                              |
| gh-286     | capsule generator; `ddc_fn` pilot                            |
| gh-287     | composer: schema + `Synth`/`Segment`                         |
| gh-287     | composer: `Timeline`/`Composer` + JSON faces + `Segment.add` |
| gh-287     | composer: `jm apply` materialization                         |
| gh-287     | composer: generic SSOT-driven JSON ser/de                    |
| gh-287     | composer: generic c-face CLI generator                       |

Each slice was validated by compiling and running the generated C against a real
project and comparing byte-for-byte to the hand-written reference, culminating in
the full `manifest → apply → build → import → samples` chain in situ.
