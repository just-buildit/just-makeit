# The object-of-objects pattern: capsule, composer & handle generators

A comprehensive guide to `kind = "capsule"`, `kind = "composer"`, and
`kind = "handle"` — the generators that turn jm from a *one-off binding
scaffolder* into a *templating engine that composes objects of objects*. From one
declarative manifest jm emits a self-contained extension module whose ergonomic
API lives **in the `.so`** (no forced pure-Python wrapper), with an optional
take-it-or-leave-it `.pyi`, JSON (de)serialization, and a command-line tool —
leaving only the DSP kernels and resource logic hand-written.

______________________________________________________________________

## Index

- [1. Executive summary](#1-executive-summary)
    - [1.1 The problem](#11-the-problem)
    - [1.2 The solution](#12-the-solution)
    - [1.3 What it enables](#13-what-it-enables)
    - [1.4 The feature set at a glance](#14-the-feature-set-at-a-glance)
- [2. Mental model](#2-mental-model)
    - [2.1 Three shapes: capsule, composer, handle](#21-three-shapes-capsule-composer-handle)
    - [2.2 The generated-vs-hand-written seam](#22-the-generated-vs-hand-written-seam)
    - [2.3 The enum SSOT](#23-the-enum-ssot)
- [3. The capsule generator (`kind = "capsule"`)](#3-the-capsule-generator-kind-capsule)
    - [3.1 What it generates](#31-what-it-generates)
    - [3.2 Manifest schema](#32-manifest-schema)
    - [3.3 Capsule mechanics & lifetime](#33-capsule-mechanics-lifetime)
    - [3.4 Worked example: `ddc_fn`](#34-worked-example-ddc_fn)
- [4. The composer generator (`kind = "composer"`)](#4-the-composer-generator-kind-composer)
    - [4.1 Overview: the four OO types](#41-overview-the-four-oo-types)
    - [4.2 Manifest schema](#42-manifest-schema)
    - [4.3 The source type](#43-the-source-type)
    - [4.4 The segment type](#44-the-segment-type)
    - [4.5 The timeline type](#45-the-timeline-type)
    - [4.6 The composer type](#46-the-composer-type)
    - [4.7 JSON faces (generated vs delegated)](#47-json-faces-generated-vs-delegated)
    - [4.8 The CLI face](#48-the-cli-face)
    - [4.9 apply materialization](#49-apply-materialization)
- [5. The handle generator (`kind = "handle"`)](#5-the-handle-generator-kind-handle)
    - [5.1 What it generates](#51-what-it-generates)
    - [5.2 The decoded-getter property](#52-the-decoded-getter-property)
    - [5.3 RAII, optional backends & the UAF rule](#53-raii-optional-backends-the-uaf-rule)
    - [5.4 Worked example: doppler's transport layer](#54-worked-example-dopplers-transport-layer)
    - [5.5 Zero-binding save/restore (gh-565)](#55-zero-binding-save-restore-gh-565)
- [6. Lifecycle & memory invariants](#6-lifecycle-memory-invariants)
- [7. Validation discipline: reference-first](#7-validation-discipline-reference-first)
- [8. Manifest reference](#8-manifest-reference)
- [9. When to use which](#9-when-to-use-which)
- [10. Gaps & roadmap](#10-gaps-roadmap)
- [11. Appendix: the build arc](#11-appendix-the-build-arc)

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

### 1.4 The feature set at a glance

| Issue  | Feature             | Role                                           |
| ------ | ------------------- | ---------------------------------------------- |
| gh-285 | `[[enum]]` SSOT     | one string-enum table feeding every face       |
| gh-286 | `kind = "capsule"`  | free-functions-over-`PyCapsule` skeleton       |
| gh-287 | `kind = "composer"` | OO types + JSON + CLI, built on the capsule    |
| gh-306 | `kind = "handle"`   | one typed class over an opaque resource handle |

______________________________________________________________________

## 2. Mental model

### 2.1 Three shapes: capsule, composer, handle

All three expose opaque hand-C state rather than copying it into Python. They
differ in the surface they present:

- A **capsule module** presents *free functions*. State is a `PyCapsule` handle
    passed as the first argument: `state = <backing>_create(...); y = <backing>_execute(state, x, out); <backing>_destroy(state)`. This is the lift of a hand-written
    "functions-over-a-capsule" extension.

- A **composer module** presents *CPython types*. The backing
    `<backing>_state_t *` lives **inside** the `Composer` object (no
    PyCapsule); the user manipulates `Synth`/`Segment`/`Timeline`
    objects and calls methods. The composer is "an object (the Composer) made of
    objects (Segments, each made of Synths)" — hence *object of objects*.

- A **handle module** presents *one CPython type over a single opaque resource
    handle* — a file writer, a socket, a clock, a session. It is the
    **intersection** of the other two: the capsule's opaque backing and lifecycle,
    wearing the composer's typed-class face (constructor, methods, properties,
    context-manager). It is the *RAII resource* shape, distinct from the capsule's
    free functions and the composer's object-of-objects.

### 2.2 The generated-vs-hand-written seam

The dividing line is deliberate and stable:

| Generated by jm                                                                     | Hand-written (stays in `_core.c` / app-side)           |
| ----------------------------------------------------------------------------------- | ------------------------------------------------------ |
| Enum int↔string tables (the SSOT)                                                   | The accumulation kernel                                |
| Capsule mechanics + `execute`/`reset`/`destroy`/`get_`/`set_` + GIL + numpy marshal | The per-source resolution (e.g. shared noise floor)    |
| Source/segment marshalling + a `bytes` buffer                                       | The synthesis kernels                                  |
| The `Synth`/`Segment`/`Timeline`/`Composer` types + factories                       | The writer / socket / clock C *logic* (BLUE/SigMF/zmq) |
| The `Plan`/`SampleClock`/`StreamSink` **handle types** (over that C logic)          | —                                                      |
| JSON to/from, the CLI face, CMake, `.pyi`                                           | —                                                      |

The rule of thumb: **jm generates everything that is a mechanical projection of
the manifest; you hand-write only what encodes the algorithm** — the DSP kernels
*and* the resource logic (a file writer, a socket). The handle generator moved
the transport *binding* across the seam: doppler's `SampleClock` and
`StreamSink` types are now generated, leaving only the socket/clock C behind
them hand-written.

### 2.3 The enum SSOT

A top-level `[[enum]]` block names a string-enum once; the order **is** the C
int (append-only), unless the enum names its C constants with `enumerators`
([Named enums](types.md#when-the-c-values-are-not-0n-1-enumerators)):

```toml
[[enum]]
name = "wfm_type"
values = ["tone", "noise", "pn", "bpsk", "qpsk", "chirp", "bits"]
```

The composer generator emits one C table per referenced enum
(`_enum_wfm_type[]`), **only** for the enums the module's fields actually
reference, plus a shared `_enum_index()` lookup whenever some face parses a
name back to its int (gh-1863). Every face — type-attribute
validation, JSON ser/de, CLI choice flags — resolves names↔ints through these
tables. There is no second copy to drift against.

______________________________________________________________________

## 3. The capsule generator (`kind = "capsule"`)

### 3.1 What it generates

For a capsule module, `jm apply` materializes three glue files:

- `native/src/<cname>/<cname>_ext.c` — the binding (`<cname>` is the module
    id with dots as underscores: `dsp.mix` → `dsp_mix`): capsule wrapper struct, a
    use-after-destroy guard, `<backing>_create`, a variable-output `execute`
    (exact-dtype numpy in/out, **zero-copy `out[:n]` view**, optional GIL
    release), bare void methods (`reset`), `destroy`, and `get_`/`set_`
    accessors; plus the `PyMethodDef` table and `PyInit`.
- `native/src/<cname>/CMakeLists.txt` — a Python-extension target linking the
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
header       = "doppler/ddc/ddc_core.h"   # backing API header (override)
depends_on   = [{ name = "ddc", link = true }]   # cores linked onto the .so
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

- `backing` is the symbol prefix and the `<backing>_state_t` it wraps. When
    it names a jm component, the symbols follow that component's C stem, so
    under `[project] c_prefix = "dp"` a `backing = "lo"` calls `dp_lo_create`
    and wraps `dp_lo_state_t`; a hand-written core (`ddcr`) is spelled
    exactly as written. The header path, capsule name and Python function
    names always use `backing` as written (gh-1685). The same holds for a
    composer's `backing`.
- `package` lets the `.so`/`.pyi` build into a *sibling* package directory
    (doppler's former `ddc_fn` built into the `ddc` package so `doppler.ddc`
    could `from .ddc_fn import ddcr_*`). When unset, the module's own path is
    used.
- `capsule_name` is the `PyCapsule` name string; the default is
    `<pkg>.<module>.<backing>_state`.
- `depends_on` entries with `link = true` add each `<name>_core` to the `.so`'s
    link line (CMake does not pull OBJECT-lib objects transitively into a final
    `.so`, so the link must be direct).
- `depends_on` entries with `test_only = true` link the component's C test and
    bench and **nothing else** — not the core's `PUBLIC` link line, the `.so`,
    the aggregate library, or the public core header. For a component whose test
    round-trips through a sibling (a reader that writes the captures it reads
    back), this keeps the manifest from asserting a dependency the shipped
    artifact does not have. It wins over `link = true` if both are set.

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

Historical: doppler's `ddc_fn` (the functional DDCR down-converter) was the
pilot; doppler has since retired it. Migrating
it from a 400-line hand-written `no_generate` extension to `kind = "capsule"`
deleted the hand code; `jm apply` regenerated a byte-equivalent binding that
compiled clean, passed all existing tests, and kept the
`from doppler.ddc import ddcr_*` re-exports working.

______________________________________________________________________

## 4. The composer generator (`kind = "composer"`)

### 4.1 Overview: the four OO types

A composer module emits three CPython types **into the `.so`**, plus a
fourth (`Timeline`) when `[module.X.timeline] type_name` is set:

- **`Synth`** (the *source* type) — one source's configuration (waveform
    fields, enums and an optional `bytes` pattern), plus factory functions
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
backing      = "wfm_compose"     # dp_wfm_compose_state_t + dp_wfm_compose_*
package      = "wfm"
header       = "doppler/wfm/wfm_compose.h"
depends_on   = [{ name = "wfm_compose", link = true }]   # and the other cores
extra_link_libs = ["wfm_cjson", "m"]

[module.wfm_compose.source]
object    = "wfm_synth"
struct    = "wfm_source_t"                 # the C struct the type wraps
type_name = "Synth"                        # the Python class name
fields = [
  { name = "type", type = "int", enum = "wfm_type", default = "tone" },
  { name = "freq", type = "double", default = "0.0" },
  { name = "bits", type = "uint8_t*", bytes = true },
  # ... one row per source field
]

[module.wfm_compose.segment]
type_name = "Segment"
struct    = "wfm_segment_t"
fields = [
  { name = "fs", type = "double", default = "1e6" },
  { name = "num_samples", type = "size_t", default = "1024" },
  { name = "off_samples", type = "size_t", default = "0" },
]

[module.wfm_compose.timeline]
type_name = "Timeline"                     # set it to emit a Timeline type

[module.wfm_compose.oo]
factories          = ["tone", "noise", "pn", "bpsk", "qpsk", "chirp", "bits"]
discriminant       = "type"                # the enum field a factory presets
composer_type_name = "Composer"

[module.wfm_compose.json]
enabled = true                             # generate to_json/from_json/from_file

[module.wfm_compose.cli]
enabled = true                             # opt-in c-face command-line tool
name    = "wfmgen"

# gh-1190: a method jm cannot express. The body lives in the hand-written
# native/src/wfm_compose/wfm_compose_ext_extra.c, which jm `#include`s after
# the generated types and never modifies; this row is what makes it reachable
# and typed. `type` picks which of the module's types it lands on (default:
# the composer type).
[[module.wfm_compose.extra_methods]]
name    = "draws"
fn      = "Composer_draws"
flags   = "METH_NOARGS"                    # default METH_NOARGS
doc     = "Per-instance draw records."
returns = "list[dict[str, object]]"        # raw Python, for the .pyi
```

jm forward-declares each row's `fn` above the method table that names it, with
the signature its `flags` imply. `METH_NOARGS`, `METH_O` and `METH_VARARGS`
take `(PyObject *self, PyObject *arg)`, and `METH_KEYWORDS` adds a
`PyObject *kwds`; `METH_FASTCALL` rows get CPython's fastcall signature. So
define the function with exactly that signature, `self`
as a `PyObject *` too. A declared row always includes the file, so it can be
written before or after the `apply` that declares it; if it is missing, the
build fails naming it. The `composer_seams` example builds one end to end.
`apply` refuses a row with no `name` or `fn`, an `fn` given two different
`flags`, and an `fn` the generated `<cname>_ext.c` (or its seam header)
already declares — `Composer_dealloc`, a getter, `PyInit_<leaf>` — since the
row's prototype would conflict with jm's own (gh-2005).

An ordinary object takes the same key, row for row, minus `type` (it has one
type): see
[`[[<component>.extra_methods]]`](configuration.md#componentextra_methods-entries)
(gh-1997).

`extra_methods` is the composer's escape hatch, and it is deliberately **not**
spelled `methods`: on a `kind = "handle"` or `kind = "capsule"` module that
word means "generate the wrapper from this signature", while here the wrapper
already exists and only needs a row. A composer `methods` table is reported as
an unknown key naming the tables it is valid on (an object, a handle module, a
capsule module).

The **`fields`** list is the keystone: one ordered list of
`{name, type, enum?, default?, bytes?}` per source/segment determines the C
struct marshalling, the type's getset slots, the JSON shape, and the CLI flags —
all from a single declaration. (`type` is the C type; `enum` tags a field as a
string-enum resolved through the SSOT; `bytes` marks an owned byte buffer.)

**A field's docstring comes from the header.** Each row is a member of the
table's `struct`, so the member's own doc (a trailing `/**< ... */` or a
`/** ... */` block above it) is the field's documentation, on the `.pyi` and
on the runtime getset doc (`help(Synth.freq)`). A row's `doc` key is the
fallback for a member the header leaves undocumented. jm reads the headers the
binding includes (the module's `header` and whatever project headers it
includes) and asks only the field's own struct, so a same-named member of
another struct never documents it. An owned buffer is looked up by its
pointer member, or by the first step of a `c_ptr` path. If a row's `doc` and
the member's doc both exist and differ, `jm status` lists the row under `DOC`
and `jm status --check` fails (gh-1703).

A `bytes` field is **owned**: jm generates the coercion from a Python `bytes`,
the getter, the setter, the `free` in `dealloc`, the deep-copy when a source is
rebuilt from a resolved segment, and both halves of the JSON codec. By default
it lives in two flat members of the source struct, `<name>` and `n_<name>`.

`c_ptr` / `c_len` say otherwise, and may name a path into a nested struct
(gh-1184) — so a project whose C already has a type for the thing can carry
that type instead of flattening its parameters into the source:

```toml
# `wfm_seq_t` names a run of bits however it was produced (LITERAL / PN /
# GOLD, with the generator's poly and seed), so the source carries one
# instead of ten flat fields per sequence.
{ name = "sync", type = "uint8_t*", bytes = true,
  c_ptr = "sync.bits", c_len = "sync.len" },
```

Naming `c_ptr` also makes the two sites that *own* the buffer cast — the
attach, which needs a `uint8_t **`, and the `free`. A relocated member is
commonly `const`-qualified, because the type it belongs to is written for the
borrowing consumer while the source is the owner.

`coerce = "bit_pattern"` widens what the field takes from Python: `bytes`, a
sequence of ints (a list, a NumPy array; any nonzero is a 1), or a `str`. jm
stores one bit per byte. By default jm reads a `str` itself: `0`/`1` digits,
or `0x` hex expanded MSB first.

A project that already has a text form for a bit pattern names its own reader
with `coerce_str_fn` (gh-1709), so the `str` face uses that grammar instead of
a second one from jm:

```toml
{ name = "payload", type = "uint8_t*", bytes = true,
  coerce = "bit_pattern", coerce_str_fn = "pat_parse" },
```

```c
/* Declared by jm in <cname>_bridge.h; written by the project. */
size_t pat_parse(const char *text, uint8_t *out, size_t max_out,
                 const char **why);
```

jm calls it twice. The first call passes `out = NULL, max_out = 0` to get the
size, and the second passes a buffer of that many bytes to fill. Each call
returns the number of bits it read, one per byte. A return of 0 is a refusal,
and `*why` becomes the `ValueError` message. The empty string is refused only
if the project's reader refuses it. This happens on every face that takes text
for the field: the constructor, the property setter, a segment's
single-source keywords, and the c-face CLI's `--<field>` flag, which exits 2
with the reason. A refused value leaves the field as it was. A `bytes` value
or an int sequence is taken as before. The key belongs to one field, so a
sibling `bit_pattern` field without it keeps jm's grammar. When every
`bit_pattern` field names a reader, jm emits no grammar of its own, and
fields naming the same reader share one generated helper (gh-1736). On a
field that does not coerce, the key would have no effect, so jm refuses it.

An **owned-pointer field** (gh-1711) holds a pointer to a host-owned
description object, such as a frame description a source is framed by. The
source owns a **copy** of it, not a reference to a Python object:
`Composer.segments` rebuilds sources from the kernel's structs and
`from_json` builds one from text, and neither has a Python object to keep
alive. The field names the pointed-to type and four host functions:

```toml
[[module.wfm_compose.source.fields]]
name   = "frame"
object = "frame.FrameDesc"   # resolves type, capsule, header and .pyi class
# ...or, for a pointer no jm object publishes (the gh-790 spelling):
# type = "wfm_frame_desc_t *"; capsule = "p.frame.desc"; header = "..."
c_ptr     = "frame"                        # optional; default <name>
copy_fn   = "dp_wfm_frame_desc_copy"
free_fn   = "dp_wfm_frame_desc_free"
parse_fn  = "dp_wfm_frame_desc_from_json"
format_fn = "dp_wfm_frame_desc_to_json"
parse_why = true   # optional: parse_fn names its refusal (gh-1735)
```

```c
/* Declared by jm in <cname>_bridge.h; written by the project. T is the
 * pointee: the member may be `const T *`, but the source owns a `T *`. */
T    *copy_fn(const T *);     /* borrowed -> owned; NULL on failure  */
void  free_fn(T *);           /* NULL-safe                           */
T    *parse_fn(const char *); /* text -> owned; NULL on refusal      */
char *format_fn(const T *);   /* owned text, released with free()    */
```

All four are required together, because each face calls one of them.

`parse_why = true` (gh-1735) declares the reason-naming reader instead, the
shape of `[X.json] from_json_why` (gh-1706):
`T *parse_fn(const char *, const char **why)`. On a refusal the reader
points `*why` at a static sentence naming the cause; every face that reads
text passes it and reports that sentence -- the constructor keyword and the
setter raise `ValueError(<sentence>)`, the generic `from_json` / `from_file`
raise it too, and the CLI prints `bad --<name> TEXT: <sentence>`. A reader
that refuses without writing one keeps the generic message. It is a switch:
a function name there is refused at load.

Fields may share a host function, and the bridge header declares it once.
A C function has one prototype, so every key naming it must agree on it:
two fields naming one `parse_fn` with `parse_why` on only one of them, or
one `copy_fn` over two `type`s, are refused naming the function, both
fields and both prototypes (gh-1739), as is any other seam colliding with
one of these names.

`object` and `capsule` are both optional, so a field bound only from its text
form is valid; writing both is refused, as is a key of another field shape
(`bytes`, `enum`, `default`, `c_len`, ...), a `type` that is not a pointer,
and the field on a segment.

| face                       | behaviour                                                                                                                                                                                            |
| -------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| constructor kwarg / setter | `None` clears it. A capsule, or an object with `._capsule`, is passed to `copy_fn`; a `str` to `parse_fn`, and a refusal raises `ValueError`. The old value is freed only once the new one is built. |
| getter                     | the text from `format_fn`, or `None`. It round-trips through the setter.                                                                                                                             |
| dealloc                    | `free_fn`                                                                                                                                                                                            |
| `Composer(...)`            | shallow, like a `bytes` field: the backing `create` deep-copies the value                                                                                                                            |
| `Composer.segments`        | each rebuilt source owns a copy from `copy_fn`, so nothing is freed twice                                                                                                                            |
| generic `to_json`          | `format_fn`'s text, nested as a JSON value when it parses as JSON and a string otherwise; a NULL pointer writes nothing                                                                              |
| generic `from_json`        | either spelling, through `parse_fn`; a value the host refuses fails the whole record, and the record's teardown calls `free_fn`. A delegated `to_json_fn` / `from_json_fn` is unaffected             |
| c-face CLI                 | `--<name> TEXT` through `parse_fn`, freed after `create`; text the host refuses exits 2                                                                                                              |
| `.pyi`                     | the keyword and the property setter take `FrameDesc \| str \| None` (`object \| str \| None` for a bare `capsule`, `str \| None` for text only); the property reads `str \| None`                    |

The getter returns text rather than a `FrameDesc` by constraint: jm cannot
build a host class from a raw pointer in a separately compiled `.so`, the
same ABI hazard `object` references avoid elsewhere. The text form is the one
generic value that round-trips through the setter and the record.

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

#### Straight-C seams (gh-998)

A source can hand work back to the project as plain C, with no CPython in it:

- **`[module.X.source.generates]`** gives the source type its own
    `step()` / `steps()` / `reset()`, delegating to a composed generator
    object (`generator`). The project writes the bridge that builds one from
    a source: `<state_t> *bridge_fn(const <struct> *, double fs)`. The other
    keys (`state_type`, `steps_fn`, `step_fn`, `reset_fn`, `destroy_fn`,
    `header`, `output_type`) default to the generator's own C stem and header,
    and `float _Complex`. A NULL from `bridge_fn` raises `RuntimeError`,
    unless the optional `bridge_error_fn`,
    `const char *fn(const <struct> *, double fs)`, returns a reason, which is
    raised as `ValueError(reason)` (gh-1307).
- **`[[module.X.source.computed]]`** (`name`, `type`, `fn`, `doc`) is a
    read-only property computed in C on every read:
    `<type> fn(const <struct> *)`.

jm writes the prototype of every seam (these, a field's `coerce_str_fn` and
an owned-pointer field's four host functions) into a generated
`native/inc/<pkg>/<cname>/<cname>_bridge.h`, which the binding includes. The
file is written only when at least one seam exists, so a C test or benchmark
includes it instead of re-declaring a signature jm owns. The
[composer_seams example](examples/composer_seams.md) builds one end to end.

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

The backing provides four functions, spelled through its C stem (§3.2):

- `<state_t> *<backing>_create(const <seg> *segs, size_t n, int repeat, int continuous)`
    (or the `create_fn` below);
- `size_t <backing>_execute(<state_t> *, float _Complex *out, size_t max)`;
- `const <seg> *<backing>_segments(const <state_t> *, size_t *n, int *repeat, int *continuous)`;
- `void <backing>_destroy(<state_t> *)`.

A `[module.X.composer]` ergonomics table adds optional in-`.so` conveniences so
no hand-Python wraps the composer: `stream = true` generates
`stream(block=4096)` — an iterator that drains `execute()` into blocks — and
`to_dict = true` generates `to_dict()` (the resolved composition as a plain
nested dict, the generic primitive any sidecar format is built from). With a
`realtime = {clock_create, pace, destroy, header}` sub-table the iterator also
**paces to an `fs`-Hz clock in C** (`for blk in c.stream(4096, realtime=1e6):`),
so a project drops its hand-written `paced()` helper (gh-317).

The transient `<segment_struct>[]` **aliases** each source's `bits` pointer;
`<backing>_create` deep-copies (see [§6](#6-lifecycle-memory-invariants)), so
the transient arrays are freed straight after and ownership stays with the
`Synth` objects.

The create every face calls is `<backing>_create` unless the module names
another: `[module.X] create_fn = "fn"` (gh-1758), with the same four
arguments. It is the author's name, used exactly as written -- a
`c_prefix` never respells it -- and the `Composer` constructor, the
generated `from_json` / `from_file` and the C CLI's flag path all call it.

A create that can say **why** it refused a composition declares it with
`[module.X] create_why = true` (gh-1755), the constructor's form of
`[X.json] from_json_why` ([§4.7](#47-json-faces-generated-vs-delegated)):
the create -- `create_fn`, or the `<backing>_create` default -- takes a
trailing `const char **why`,

```c
<state_t> *fn(const <segment_struct> *segs, size_t n, int repeat,
              int continuous, const char **why);
```

and every face passes it: the constructor raises `ValueError(<the reason>)`,
the generated `from_json` / `from_file` raise it instead of
`invalid composer spec`, and the C CLI's flag path prints it. A refusal that
writes no reason keeps the old `ValueError("<fn> failed")`. A backing that
keeps a plain four-argument create for its own C callers names the
reason-naming one in `create_fn` (doppler: `create_fn = "dp_wfm_compose_create_why"`). A `create_why` that is not `true`/`false` is
refused at load.

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

A delegated reader that can say **why** it refused declares it per factory
(gh-1706): `from_json_why = true` means `from_json_fn` is
`<state_t> *fn(const char *json, const char **why)`, and `from_file_why = true` the same for `from_file_fn`. The generated factory passes the address
of a `NULL`-initialised local, and a refusal raises
`ValueError(<the reason>)`; a refusal that writes none keeps the old
`ValueError("<fn> failed")` / `OSError("<fn> failed")`. The C CLI's
`--from-file` passes it too and prints it. Both keys belong to the delegated
mode only -- the generated reader has no C factory to ask -- and `apply`
refuses them without `to_json_fn`. The same carriage on a module function is
`why = true` ([`--why`](commands/extend.md#just-makeit-function)).

### 4.8 The CLI face

With `[module.X.cli] enabled = true` the composer gets an opt-in **standalone C
command-line tool** (`render_cli`): a pure-C `main()` — no Python — that

- builds the composer from **source/segment-field flags** (`--type`, `--freq`,
    `--num_samples`, …) or from a JSON spec via `--from-file`, which calls the
    project's C reader `[X.json] from_file_fn` (default
    `<backing>_from_file(const char *path)`; under `from_file_why` it takes a
    trailing `const char **why`). jm does not generate that reader, and the
    generated JSON ser/de lives in the extension only, so a composer with the
    CLI enabled supplies it;
- takes `--out FILE`, `--repeat` and `--continuous`;
- streams samples in the chosen wire format, reusing **`jm app`'s output axes**
    verbatim (`--sample_type` / `--file-type` / `--endian`);
- validates enum flags against the SSOT `_enum_*` tables — no hand-written flag
    tables.

The CMake `add_executable` target is emitted **outside** the `BUILD_PYTHON`
guard (it is a C tool, not a Python module).

### 4.9 apply materialization

`jm apply` routes a composer module to the composer materializer (no
object-group scaffold). It writes `<cname>_ext.c` (the assembled module: enum
tables + the source, segment and composer types, the timeline type when
declared, the factory table and `PyInit`), `CMakeLists.txt`, `<leaf>.pyi`,
(when enabled) `<cname>_cli.c`, and (when the source declares a seam, §4.3)
`native/inc/<pkg>/<cname>/<cname>_bridge.h`, then splices the top-level
`add_subdirectory`. These are glue: `jm apply` reconciles them on every run and
`jm status --check` guards them. The manifest round-trips through `save`/`load`
so a project is reproducible from the manifest plus the hand-written kernels
alone.

______________________________________________________________________

## 5. The handle generator (`kind = "handle"`)

### 5.1 What it generates

A handle module is the **intersection** of the capsule and composer generators
(§2.1): the capsule's opaque hand-C backing and lifecycle, wearing the composer's
typed-class face. Where a capsule presents free functions and a composer presents
an object of objects, a handle presents **one typed `PyTypeObject` over a single
opaque resource handle**.

`jm apply` materializes the same three glue files as a capsule —
`native/src/<cname>/<cname>_ext.c`, `CMakeLists.txt`, `<leaf>.pyi` — recognized at
all **four** dispatch sites in `_apply.py` (the import block, the materialize
dispatch, the `_mods_need_update` exclusion filter, and `_sync_aggregates` glue
reconciliation; miss any and `jm apply` / `jm status --check` break silently).

The generated type carries:

- a **constructor** — either `create_fn` (allocates and returns the handle) or,
    for an init-in-place C API, `init_fn` (jm `malloc`s `sizeof(handle_type)`,
    calls `init_fn(self->h, …)`, and `free`s on close; gh-315). It coerces
    `create_args` — enum-string→index via the SSOT, `os.fspath` for a `path` arg,
    a borrowed `(const void *, size_t)` for a `bytes` arg (`y#`, gh-565),
    scalar casts — and runs an optional conditional `create_post` setter.
    A NULL return raises `RuntimeError: "<create_fn> failed"` unless the module
    declares `create_error` / `create_error_message` (gh-514), which is worth
    doing: a handle module is the shape that opens external resources, and
    "no such file, unrecognised container, or an unsupported format" tells the
    caller what to fix where an internal C symbol name does not;
- **methods** mapping `name → fn(self->h, …)`, in six shapes: scalar args
    (honoring `default` / keyword args, gh-319); an array-in arg (numpy-marshaled
    like the capsule path), optionally followed by trailing scalars
    (`send(iq, fs, fc)`, gh-308); an int-in→array-out shape returning an
    **independent** numpy-owned array; an **array-in + writable array-out**
    execute (`execute(x, out)` → the zero-copy `out[:n_out]` view, gh-311),
    which may itself carry trailing scalars for a control port
    (`execute_ctrl(x, out, rate, freq=0.0)` → `fn(h, in, n_in, rate, freq, out, max_out)`, gh-582 — note `out` stays *second* in Python so it remains
    required ahead of any defaulted scalar); a
    scalar/string-args → handle-length array-out shape (`out_len_fn` sizes the
    result); and a scalar/string-args → handle-length **`bytes`** shape
    (`returns = "bytes"` + `out_len_fn`: a temp buffer filled by
    `fn(self->h, …, void *out)`, copied into an immutable `bytes` — `save()`, the
    write half of §5.5 save/restore, gh-565);
- **module-level factories** (§5.5) — alternate constructors that build a
    *fresh* handle from a blob or file;
- **decoded-getter properties** (§5.2), including **writable** scalar
    properties — the genuinely new code;
- the **RAII protocol** (§5.3): an always-generated idempotent `close()` and a
    `tp_dealloc` that closes a forgotten handle, plus `__enter__`/`__exit__` when
    `context_manager` is set;
- an optional **weak-symbol backend guard** (§5.3).

Almost everything is reused: the enum SSOT tables + `_enum_index` (composer), the
scalar format-char machinery and numpy marshaling (capsule / `_types`), and the
context-manager + idempotent-close pattern (composer). The only genuinely new C
is the decoded-getter property and the weak-symbol guard.

### 5.2 The decoded-getter property

A composer getset reads a struct field directly; a handle property decodes the
output of a **shared C getter**. One getter `fn(self->h, &tmp)` fills an
out-struct; each declared property decodes one named field with a transform:

| transform | example                                                     |
| --------- | ----------------------------------------------------------- |
| plain     | `_to_py(tmp.frac)`                                          |
| `enum`    | `_enum_<e>[tmp.idx]` → string (the SSOT)                    |
| `scale`   | `tmp.ns * 1e-9`                                             |
| `expr`    | verbatim C: `tmp.peak > 0 ? 20*log10(tmp.peak) : -INFINITY` |

An `expr` may also read a constructor value stashed into the object struct
(`self->sample_type >= 2`). A getter marked `cache = true` is resolved **once** in
`tp_init` (fixed metadata — a reader's sample rate); otherwise it is called
**live** on each access (a running clock's counters).

Three variations cover the C APIs that don't fill a struct (gh-311/gh-314):

- a getter whose `out` is a **scalar** C type returns by value
    (`tmp = fn(self->h)`) and its single field decodes `tmp` directly;
- each field may instead name its **own** scalar getter via `getter = "T fn(h)"`
    (no shared `fn`/`out`), so a project drops the hand-C struct shim that bundled
    per-property getters into a `*_stats_t` purely to fit the decode;
- a field that also names a `writable_fn` becomes a **read/write** property — the
    getset gains a `(setter)` that coerces the value (`PyArg_Parse`) and calls
    `set_fn(self->h, v)`.

```toml
[[module.wfm_writer.getters]]
fn = "wfm_writer_stats"
out = "wfm_writer_stats_t"
cache = false
[[module.wfm_writer.getters.fields]]
name = "clip_fraction"
from = "frac"
type = "double"
[[module.wfm_writer.getters.fields]]
name = "peak_dbfs"
type = "double"
expr = "tmp.peak > 0 ? 20*log10(tmp.peak) : -INFINITY"
```

### 5.3 RAII, optional backends & the UAF rule

`close()` (calling `close_fn`, default `<backing>_close`) and `tp_dealloc` are
always generated: `close()` is idempotent
(`if (!self->closed) { close_fn(self->h); self->closed = 1; }`) and `tp_dealloc`
closes a still-open handle, so even a forgotten handle releases. Setting
`context_manager` additionally emits `__enter__`/`__exit__` (the latter calls
`close()`), so a `with` block releases cleanly too. A POSIX-only backend is
declared `optional_backend = "<symbol>"`: the symbol is a weak extern, and
`tp_init` raises `NotImplementedError` when it resolves to NULL.

The use-after-free rule (§6) applies to `tp_init`: a `path` arg crosses as a
borrowed `PyBytes` (from `PyUnicode_FSConverter`); the backing `*_open` copies it,
so the borrow is `Py_DECREF`'d only **after** `create_fn` returns. A method
returning data from a grow-on-demand buffer returns an independent numpy-owned
array, never a dangling view.

### 5.4 Worked example: doppler's transport layer

The handle generator existed to retire doppler's hand-written `wfmcompose_py.c`
(~960 lines of CPython — the four transport types plus segment-tuple parsing and
free functions that moved to jm module functions). `Writer`, `Reader`, `ZmqSink`,
and `SampleClock` were
**one archetype — a capsule-backed resource handle — instantiated four times**
over the existing `wfm_writer.c` / `wfm_reader.c` / `wfm_sink.c` C API (whose
`wfm_reader_info()` already filled a struct, ideal for the decoded-getter path).
`Reader` used `cache = true` info getters; `Writer` / `ZmqSink` exposed their
stats as **per-field scalar getters** (gh-314, no `*_stats_t` shim);
`SampleClock` was built **in place** via `init_fn` (gh-315, no create/destroy
shim); `ZmqSink` / `SampleClock` used the weak-symbol guard (POSIX-only);
`ZmqSink.send(iq, fs, fc)` was the array+scalar method shape. doppler has
since moved `Writer` / `Reader` to object modules and renamed `ZmqSink` to
`StreamSink`. The validation was reference-first (§7): the first real compile of
generated handle output — scaffold → `jm apply` → compile + a real C backing →
import → exercise — caught a codegen bug a string-assertion missed, and now
guards the marshaling end-to-end in CI.

### 5.5 Zero-binding save / restore (gh-565)

A handle often wants to persist and reconstruct — a "prepare once, materialize
many" plan that skips an expensive setup on reload. jm generates the whole
round-trip, no `_ext.c` and no Python: a handle serializes to `bytes` and a
module-level factory rebuilds a fresh handle from that blob (or a file).

**Write — `returns = "bytes"`.** A method whose output length comes from the
handle declares an `out_len_fn`; jm sizes the blob with it, fills a temp buffer
via `fn(self->h, …, void *out)`, copies the bytes into an immutable `bytes`
object, and frees the temp. Because the return is an owned copy there is no
aliasing — none of the array shapes' deferred-free / view machinery applies.

```toml
[[module.wfm_plan.methods]]
name = "save"
fn = "wfm_plan_save"           # size_t (const h*, void *out) -> bytes written
out_len_fn = "wfm_plan_save_bytes"   # size_t (const h*)
returns = "bytes"
```

**Restore — `[[module.<name>.factories]]`.** A factory is a *module-level*
function (not a method, not a classmethod) that parses an init-param, calls an
alternate `create_fn` to build a **fresh** handle, wraps it in the module's typed
class, and returns it. The primary constructor is untouched and the type stays
`@final`.

```toml
[[module.wfm_plan.factories]]
name = "PlanFromBlob"
create_fn = "wfm_plan_restore"      # h* (const void *blob, size_t n)
init_params = [{ name = "blob", type = "bytes" }]

[[module.wfm_plan.factories]]
name = "PlanFromFile"
create_fn = "wfm_plan_load"         # h* (const char *path)
init_params = [{ name = "path", type = "path" }]
```

```python
from doppler.wfm import Plan, PlanFromBlob, PlanFromFile

blob = p.save()                 # bytes
p2 = PlanFromBlob(blob)         # a fresh, independent Plan
p3 = PlanFromFile("plan.bin")   # ditto, from a file
```

| Factory key   | Notes                                                      |
| ------------- | ---------------------------------------------------------- |
| `name`        | The module-level function name (e.g. `PlanFromBlob`)       |
| `create_fn`   | Alternate constructor `h* (…)` that returns a fresh handle |
| `init_params` | `{name, type}` — a `bytes` blob, a `path`, or scalars      |

> **Restore semantics.** A factory `tp_alloc`s the instance and **bypasses
> `tp_init`**, so any *expr-stashed* init scalars (Python-side `create_args`
> referenced by an `expr` getter, §5.2) stay zero — the blob reconstructs the
> handle wholesale and those Python values aren't available on restore.
> `cache = true` getters **are** resolved from the rebuilt handle. If a factory
> needs a stashed scalar for an `expr` getter, pass it as an extra `init_param`.

______________________________________________________________________

## 6. Lifecycle & memory invariants

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

## 7. Validation discipline: reference-first

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

## 8. Manifest reference

**Shared (capsule, composer & handle):**

| key               | meaning                                                                                                               |
| ----------------- | --------------------------------------------------------------------------------------------------------------------- |
| `kind`            | `"capsule"`, `"composer"`, or `"handle"`                                                                              |
| `backing`         | symbol prefix; wraps `<backing>_state_t`, calls `<backing>_*`                                                         |
| `package`         | package dir the `.so`/`.pyi` build into (default: module path)                                                        |
| `header`          | backing C API header, included verbatim (default `<pkg>/<backing>/<backing>_core.h`; spell an override `"<pkg>/..."`) |
| `depends_on`      | `[{name, link=true}, …]` — cores linked onto the `.so`                                                                |
| `extra_link_libs` | non-core link targets (e.g. `"m"`, a vendored json lib)                                                               |
| `doc`             | the module's docstring, on its `m_doc` and its re-export `__init__.py` (gh-645)                                       |
| `platforms`       | the platforms the module's extension is built on (gh-1463)                                                            |

Module-level `[[module.X.functions]]` are a plain module's. On any of the three
kinds the table is refused with a warning, and `jm function --module` on one
exits naming the plain-module route. Each face reads only the tables listed for
it below; one that belongs to another face (a handle `properties`, a capsule
`getters`, a composer `init_params`, ...) warns and names the table that face
reads instead.

A capsule's `init_params` and `properties`, and a handle's `create_args`,
factory `init_params` and getter `fields`, each cross as one C scalar. A
`type` there that is an array (`"float[]"`) or a spelling jm does not know is
refused before anything is written, with one `error:` line naming the module,
the table and the row (gh-2009). A constructor array is supported on an
object's [`[[<object>.init_params]]`](configuration.md#objectinit_params).

A composer's `settings`, `segment.fields`, `source.computed`, source fields
and serializer `params` convert each value by its declared `type`, through
the same conversion an object's property and method use: a `double` keeps
its fraction, a `bool` reads back as a `bool`, a complex keeps both parts,
and the `.pyi` says so (gh-2035). The same refusal covers them. Beyond it,
a `settings`, `segment.fields` or `source.computed` row holds a number, so a
string `type` is refused there; and a complex segment field is refused when
the JSON or CLI face is on, since each carries a segment field as one real
number.

**Capsule only:** `capsule_name` (the `PyCapsule` name string; default
`<pkg>.<module>.<backing>_state`), `[[module.X.init_params]]`
(`name`/`type`), `[[module.X.methods]]` (`name`, `arg_type?`,
`return_type?`, `caller_out?`, `nogil?`), `[[module.X.properties]]`
(`name`/`type`/`writable?`).

**Composer only:**

| table / key         | meaning                                                                                                                                                                                                                                                                                                                                          |
| ------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| `create_fn`         | the create every face calls, `<state_t> *fn(segs, n, repeat, continuous)`; default `<backing>_create`, used as written (gh-1758)                                                                                                                                                                                                                 |
| `create_why`        | the create takes a trailing `const char **why`; every face that creates raises or prints its reason (gh-1755)                                                                                                                                                                                                                                    |
| `[X.source]`        | `object`, `struct`, `type_name`, `fields[]`; optional `computed[]` and `generates` (the straight-C seams, §4.3) and `ranged`                                                                                                                                                                                                                     |
| `[X.segment]`       | `type_name`, `struct`, `fields[]`; optional `sources_member`/`count_member` (default `sources`/`n_sources`), `flat_sources` (a one-source segment proxies that source's fields as read-only attributes), `ranged`                                                                                                                                |
| `ranged`            | on `[X.source]` / `[X.segment]`: `[{name, flag}, …]`, fields that also take a `(lo, hi)` pair, redrawn uniformly each repeat; the struct carries a `<name>_hi` companion                                                                                                                                                                         |
| `[X.timeline]`      | `type_name` (the `Timeline` type is emitted only when it is set)                                                                                                                                                                                                                                                                                 |
| `[X.oo]`            | `factories[]`, `discriminant`, `composer_type_name`                                                                                                                                                                                                                                                                                              |
| `[[X.serializers]]` | an extra delegated serializer `{name, fn, returns?, params?[], header?}`: a `<Composer>.<name>(<params>)` method calling the project's C `fn(<params>, segs, n)` over the resolved segments, for a domain wire format jm does not generate (gh-317)                                                                                              |
| `[X.json]`          | `enabled`; optional `to_json_fn`/`from_json_fn`/`from_file_fn`/`to_json_trailing`/`from_json_why`/`from_file_why` (delegation), `header`/`include_dir` (generated path)                                                                                                                                                                          |
| `[X.composer]`      | `stream`, `to_dict`; optional `realtime = {clock_create, pace, destroy, header}` to pace `stream()` in C (gh-317)                                                                                                                                                                                                                                |
| `[[X.settings]]`    | a post-construction setting `{name, setter_fn, getter_fn, type, enum?}` — a scalar the backing exposes through a setter/getter pair and that is set once, after `create_fn` returns and before the first `execute()`. Becomes a constructor kwarg AND a read/write attribute; a string-valued one resolves through its `[[enum]]` SSOT (gh-1126) |
| `[X.cli]`           | `enabled`, `name`                                                                                                                                                                                                                                                                                                                                |

A **field** entry (`source.fields`/`segment.fields`):
`{ name, type, enum?, default?, bytes?, complex?, c_ptr?, c_len?, coerce?, coerce_str_fn?, aliases?, doc? }`
— one declaration drives the marshalling, the type slots, the JSON shape, and
the CLI flag. `complex` marks an owned complex64 array, as `bytes` marks an
owned byte buffer; `c_ptr` / `c_len` relocate either into a nested struct
(§4.2); `coerce = "bit_pattern"` and `coerce_str_fn` widen what a `bytes`
field takes (§4.2); `aliases` are constructor keywords accepted in place of
the field's name; `doc` is the fallback docstring for a member its header
leaves undocumented. A source field may instead be an owned pointer (§4.2,
gh-1711):
`{ name, object? | type + capsule? + header?, c_ptr?, copy_fn, free_fn, parse_fn, format_fn, parse_why? }`.

**Handle only:**

| table / key                             | meaning                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                        |
| --------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| `handle_type`                           | the opaque C handle type (default `<backing>_t`)                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                               |
| `type_name`                             | the generated CPython class name (`Plan`)                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                      |
| `create_fn`                             | the backing constructor; `create_args[]` are `{name, type, enum?, default?, kwonly?, doc?}` (`type = "path"` → `os.fspath`)                                                                                                                                                                                                                                                                                                                                                                                                                                                    |
| `init_fn`                               | init-in-place ctor over a caller-allocated struct (jm mallocs + frees); mutually exclusive with `create_fn` (gh-315)                                                                                                                                                                                                                                                                                                                                                                                                                                                           |
| `[[X.create_post]]`                     | conditional post-create setter `{fn, when?, arg?}`                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                             |
| `create_error` / `create_error_message` | exception + message raised when `create_fn` returns NULL; undeclared → `RuntimeError: "<create_fn> failed"` (gh-514)                                                                                                                                                                                                                                                                                                                                                                                                                                                           |
| `[[X.methods]]`                         | `{name, fn, args[], returns?, nogil?, error?, error_message?, status_return?, out_len_fn?, doc?}` — scalar (args honor `default`); array-in (+ trailing scalars); int-in→array-out; array-in + a `writable=true` array-out execute (gh-311/319); `out_len_fn` sizes a handle-length array or `bytes` result (§5.5)                                                                                                                                                                                                                                                             |
| `[[X.factories]]`                       | module-level alternate constructors `{name, create_fn, init_params[]}` that build a fresh handle (§5.5, gh-565)                                                                                                                                                                                                                                                                                                                                                                                                                                                                |
| `close_returns`                         | `"int"`: `close()` raises `RuntimeError` on a non-zero `close_fn` return; `tp_dealloc` still ignores it                                                                                                                                                                                                                                                                                                                                                                                                                                                                        |
| `serializable`                          | `true`: `state_bytes()` / `get_state()` / `set_state()` over the handle, from the backing's `<backing>_state_bytes` / `_get_state` / `_set_state` (gh-403)                                                                                                                                                                                                                                                                                                                                                                                                                     |
| `capsule` (or `capsule_name`)           | publish a borrowed `_capsule` property lending the handle's pointer, so another component can take it as a capsule parameter (gh-794)                                                                                                                                                                                                                                                                                                                                                                                                                                          |
| `error` / `error_message` (on a method) | over an `int` `returns`, a non-zero rc raises `error` with `error_message` (the rc appended) instead of crossing as an int; undeclared message → `"<fn> failed"`. The `.pyi` says `-> None` and documents a numpy `Raises` section, from the same pair the binding raises with (gh-565/gh-1111/gh-1116). **Needs a status return**: on an array or `bytes` result the C return is the payload length, so there is no rc to check and the declaration is refused (gh-1118); with an array argument jm reads the return as a count unless the method says `status_return = true` |
| `status_return` (on a method)           | `true`: the `int` `returns` is a status, as on an object method: non-zero raises `error` (`ValueError` when undeclared) and the method returns `None`. The one way an array-argument method such as `send(iq, fs, fc)` raises on a refused block; refused over a non-integer `returns` or an array / `bytes` result (gh-2186)                                                                                                                                                                                                                                                  |
| `[[X.getters]]`                         | a shared struct getter `{fn, out, cache?, fields[]}`, or per-field scalar getters (each field a `getter`); field `{name, from?, type, enum?, scale?, expr?, getter?, writable_fn?}` (gh-311/314)                                                                                                                                                                                                                                                                                                                                                                               |
| `close_fn`                              | the idempotent `close()` / `tp_dealloc` destructor (always generated; default `<backing>_close`)                                                                                                                                                                                                                                                                                                                                                                                                                                                                               |
| `context_manager`                       | *also* emit `__enter__`/`__exit__` (`__exit__` calls `close()`)                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                |
| `optional_backend`                      | a weak-symbol backend; absent → `NotImplementedError`                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                          |

______________________________________________________________________

## 9. When to use which

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

- Reach for **`kind = "handle"`** when the natural API is *one typed object over
    an opaque resource handle* with RAII — a file writer, a socket sink, a sample
    clock, a session: a constructor, a few methods, read-only decoded-from-a-getter
    properties, and a `with`-block / `close()`. It's the typed-class counterpart to
    a capsule (which gives the same backing as flat free functions instead).

- Stay with a plain object-group module / `jm app` when there is a single
    object with a simple scalar/blockwise/generator I/O shape and no composition.

______________________________________________________________________

## 10. Gaps & roadmap

- **JSON delegation vs generation.** The generated JSON ser/de is generic and
    SSOT-driven. A project that must reproduce a *domain-specific* wire schema
    byte-for-byte (conditional field emission, bespoke layouts) uses the
    `json.to_json_fn` delegation hatch — at the cost of keeping that one
    hand-written serializer (and its enum copy).
- **The last enum copy.** The CLI's `--from-file` always calls a backing C
    reader (`[X.json] from_file_fn`, default `<backing>_from_file`), and that
    reader keeps its own enum table. Collapsing the
    final copy to zero needs a *standalone* generated C ser/de (enum tables +
    to/from JSON over the backing structs) shared by both the extension and the
    CLI — a deferrable follow-up.
- **CLI faces.** The c-face CLI is generated; console/pep723 faces are not (the
    OO `Composer` already gives Python users the API directly).

______________________________________________________________________

## 11. Appendix: the build arc

| Issue / PR | Slice                                                        |
| ---------- | ------------------------------------------------------------ |
| gh-285     | `[[enum]]` SSOT                                              |
| gh-286     | capsule generator; `ddc_fn` pilot                            |
| gh-287     | composer: schema + `Synth`/`Segment`                         |
| gh-287     | composer: `Timeline`/`Composer` + JSON faces + `Segment.add` |
| gh-287     | composer: `jm apply` materialization                         |
| gh-287     | composer: generic SSOT-driven JSON ser/de                    |
| gh-287     | composer: generic c-face CLI generator                       |
| gh-306     | handle generator: typed class, decoded-getters, RAII         |
| gh-308     | handle: array+scalar method args + real-compile CI harness   |
| gh-311     | handle: array-in→writable-array-out execute + writable prop  |
| gh-314     | handle: per-field scalar getters (drop the struct shim)      |
| gh-315     | handle: `init_fn` init-in-place constructor                  |
| gh-319     | handle: keyword / default args on methods                    |
| gh-318     | module functions: stateless `variable_output` (self-sizing)  |
| gh-317     | composer: realtime-paced `stream()` (in-`.so` pacing)        |

Each slice was validated by compiling and running the generated C against a real
project and comparing byte-for-byte to the hand-written reference, culminating in
the full `manifest → apply → build → import → samples` chain in situ.
