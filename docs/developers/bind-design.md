# `jm bind` — design notes

> This is a contributor-facing design record, not user documentation. For
> how to use the shipped `jm bind` command, see the
> [porting guide](../porting-guide.md#path-a-you-have-the-header-jm-bind).

Status: **shipped (MVP + Real phases; see [Phased rollout](#phased-rollout)
below).** `jm bind` reads a hand-written `<comp>_core.h` and synthesises
`<comp>_ext.c` from it, with no TOML and no prior `jm` history — every
shape below is implemented and covered by CI (`jm bind --check` runs on
every bundled example). Only the **Robust** phase (a libclang fallback for
headers the regex parser can't handle) remains open. This note is kept as
the design record for why the contract is shaped the way it is, and as the
tracking doc for that remaining phase. Sibling to the
[template gallery](../templates/index.md). The sacred/glue contract that
`jm apply` ships (see [declarative-scaffolding.md](declarative-scaffolding.md))
is what makes this safe: `_ext.c` is a glue file, regenerated from the
source of truth without ever clobbering a hand-written `_core.c`.

______________________________________________________________________

## Premise

The template gallery describes a fixed contract: every preset says what
the state struct looks like, what the lifecycle trio is named, which
verbs the component exposes. If a user codes to that contract by hand
— in a fresh checkout, or in an existing C library they want to expose
to Python — the binding is mechanically derivable. There's nothing
left to ask.

`jm bind <component>` reads `<component>_core.h`, recognises the
template shape it follows, and writes a `<component>_ext.c` (plus a
matching `.pyi`) that matches. No `just-makeit.toml`, no `jm new` required.
Composes cleanly with hand-rolled or imported C code. It does not scaffold
a test file — see [Open questions](#open-questions).

This is the asymmetry that makes it cheap: `_ext.c` only needs to know
five things, all present in a well-formed header.

| What the renderer needs     | Where it lives in `<comp>_core.h`                       |
| --------------------------- | ------------------------------------------------------- |
| State struct name + fields  | `typedef struct { ... } <comp>_state_t;`                |
| Constructor signature       | `<comp>_state_t *<comp>_create(...)`                    |
| `step()` / `steps()` shapes | inline `<comp>_step(...)`, declared `<comp>_steps(...)` |
| Getter / setter pairs       | `<comp>_get_<field>()` / `<comp>_set_<field>()`         |
| Extra methods               | every other `<comp>_<verb>(...)` declared in the header |

The rendering side already exists — `_render.render_module_ext_*` and
the per-template `_core.c` skeletons. `jm bind` only needs the
*front-end*: parse the header into the same context dict that
`make_state_ctx` / `make_methods_ctx` produce today.

______________________________________________________________________

## Phased rollout

| Phase      | Scope                                                                                                                                                                                                                                                            | Status                 |
| ---------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ---------------------- |
| **MVP**    | Regex header parser; scalar state fields only; one preset per call. Emits a working `_ext.c` for any project whose `_core.h` follows the template contract.                                                                                                      | **Shipped**            |
| **Real**   | Init_params recognition (ctor args that aren't state fields); output-param detection (`out` / `output` / `dst` / `dest` naming + `_max_out` pairing); opaque state; arbitrary custom verbs. Covers the working presets (processor, generator, consumer, reader). | **Shipped** (Phase 3b) |
| **Robust** | Replace regex with libclang AST. Handles preprocessor macros, typedef chains, declarations split across lines.                                                                                                                                                   | Open                   |

`jm bind --check` (the CI-parity gate) shipped alongside MVP — it never
needed libclang, only a deterministic re-render to diff against. Each phase
was shippable on its own and the surface only ever grew.

______________________________________________________________________

## The template contract (what makes a header reflectable)

The parser doesn't infer shape from arbitrary C. It assumes the file
follows the contract every gallery template already describes. Making
the contract formal turns each `templates/*.md` page into a sufficient
spec for `jm bind`.

A reflectable header must:

1. **Define a state struct** at file scope:
    ```c
    typedef struct { /* scalar fields */ } <comp>_state_t;
    ```
    Or, for opaque state, forward-declare it (definition in the `.c`).
    Forward declarations bind to a handle-only Python class.
1. **Declare the lifecycle trio** with the canonical names:
    ```c
    <comp>_state_t *<comp>_create(...);
    void            <comp>_destroy(<comp>_state_t *);
    void            <comp>_reset(<comp>_state_t *);
    ```
    `_reset` is optional but conventional.
1. **Declare per-field accessors** for any field the Python class
    should expose:
    ```c
    <ctype> <comp>_get_<field>(const <comp>_state_t *);
    void    <comp>_set_<field>(<comp>_state_t *, <ctype> val);
    ```
    Bind emits one Python property per matching pair. Getter-only
    pairs bind to a read-only property.
1. **Name output array params** `out`, `output`, `dst`, or `dest`.
    These get `T *` (writable, allocated by Python). Every other
    pointer param is treated as input (`const T *`).
1. **Pair variable-output methods** with a sibling `_max_out`:
    ```c
    size_t <comp>_<verb>_max_out(<comp>_state_t *);
    size_t <comp>_<verb>(<comp>_state_t *, ..., T *out);
    ```
    The pair is recognised by name + signature; bind allocates a
    sized buffer once at `__init__` and returns a zero-copy ndarray
    view on each call.

Every other `<comp>_*` declaration in the header is treated as a
custom method on the Python class. Module-level functions (not
prefixed with `<comp>_`) bind as free functions on the parent module.

The `generator`, `consumer`, `reader`, and `function` template pages
each describe the contract above by example — the formal version above
is just the union of what those pages already require. Variable-output
components (the event-emitter shape) follow the same contract plus a
sibling `<comp>_<verb>_max_out()` declaration. (`blockwise` is
excluded — array return is unsupported.)

The **type allowlist** the parser enforces per slot is the same one
documented in [`docs/types.md`](../types.md), referenced from the
"Concrete types" section of every template page. Parser and CLI share
that single source of truth — a `const char *` that isn't legal as a
state field is a parse error in `jm bind`, not just a "won't compile"
surprise.

______________________________________________________________________

## Parser strategy

### MVP — regex on a single pass

The header conventions above are line-shaped. Five regexes (state
struct, lifecycle trio, getter/setter, `step` / `steps`, every other
`<comp>_*`) plus a tiny type-mini-parser (the same `_CTYPE_META` keys
already in `_types.py`) cover the shipped templates. False negatives
on weird formatting are fine for an MVP — they fall through to the
"sorry, can't parse this header; fall back to TOML" path.

```
parse_header(path: Path) -> HeaderShape:
    text = path.read_text()
    state = match_state_struct(text)
    ctor  = match_ctor(text, comp=state.name)
    step  = match_step(text, comp=state.name)
    gs    = match_getter_setter_pairs(text, comp=state.name, fields=state.fields)
    extra = match_remaining_decls(text, comp=state.name, claimed=...)
    return HeaderShape(state, ctor, step, gs, extra)
```

`HeaderShape` is then translated into the same dict shape
`make_state_ctx` / `make_methods_ctx` produce today, fed through
`_render.render_module_ext_*`, and written to disk.

### Real — convention-driven semantics

The harder semantic moves are still rule-based, just past lexical
recognition:

- **Init_params**: any ctor param whose name does *not* match a state
    field becomes an init_param. The ctor body in `.c` is assumed to
    initialise the state via `<comp>_set_<field>()` calls (or direct
    struct assignment).
- **Output params**: a pointer param named `out` / `output` / `dst` /
    `dest` is writable; any other pointer param is input.
- **Variable output**: if `<comp>_<verb>_max_out` exists alongside
    `<comp>_<verb>`, the verb binds as a variable-output method.
- **Opaque state**: state struct present in header as forward decl
    only — no `{ ... }` body. Skip getter/setter discovery; emit a
    handle-only Python class.

### Robust — libclang

When the regex pass fails (decl split across lines, macros expand to
the return type, typedef chain hides the int width), fall back to
libclang. Same `HeaderShape` output; the only thing that changes is
how it's populated. libclang is added as an optional dependency
(`extras = ["bind-robust"]`) so the MVP install stays slim.

______________________________________________________________________

## `--check` mode

```sh
jm bind <component> --check
```

Reads `<component>_core.h`, runs the parse, renders what `<comp>_ext.c`
*should* look like, and diffs against the version on disk. Non-zero
exit on drift. This is the CI hook: the binding stays consistent with
the header without anyone having to re-run `jm`.

A passing `--check` in CI means "the C and the binding agree" — which
is exactly the property `_function.run` enforces today by emitting both
files from the same context.

______________________________________________________________________

## Preservation

`<comp>_ext.c` is a **glue file** under the sacred/glue contract that
`jm apply` now ships: glue is regenerated from the source of truth on
every run, never hand-edited. `jm bind` follows the same rule —
re-running it re-derives `_ext.c` from the header wholesale.

So the workflow is:

1. Run `jm bind foo` to derive `foo_ext.c` from `foo_core.h`.
1. Edit `foo_core.h` to add another method, and `foo_core.c` to
    implement it. `_core.c` is **sacred** — bind never touches it.
1. Re-run `jm bind foo` — the new method gets a binding; the
    hand-written `_core.c` body is untouched.

This is the same split `jm apply` enforces: glue (`_ext.c`, `.pyi`,
`CMakeLists.txt`) regenerates; sacred (`_core.c`) is the user's.

______________________________________________________________________

## Composition with the rest of the toolchain

- **Decoupled from `just-makeit.toml`.** `jm bind` reads the header
    and nothing else. It does not write to the manifest; it does not
    require `jm new` to have run.
- **TOML is *one* front-end, header is another.** Both produce the
    same context dict; the renderer doesn't know which one emitted it.
- **Imported libraries become bindable.** Drop a vendored
    `vendor/libfoo/foo.h` into your project; `jm bind` against it and
    you have Python access. (Names must follow the template contract;
    pure third-party headers usually don't, but a thin shim of
    convention-named declarations does.)
- **Composes with `--impl`.** A header bound by `jm bind` pairs
    naturally with `--impl file::funcname` (or `--impl file::N:M` to
    lift a line range) to fill the `_core.c` bodies from existing C.

______________________________________________________________________

## Acceptance

The MVP + Real phases shipped once all three held true — and still do,
guarded in CI:

1. Each working preset's generated `_core.h` can be fed to `jm bind`
    and produce an `_ext.c` byte-identical to (or semantically
    equivalent to) what the original scaffold emitted.
1. `jm bind --check` runs in CI for every bundled example and
    passes on every commit.
1. At least one bundled example uses `jm bind` end-to-end — author
    `_core.h` and `_core.c` by hand, then `jm bind` to materialise the
    binding.

The Robust (libclang) phase remains open; its bar is the same kind of
proof — hand-import a small third-party C library (e.g. a single header
from a DSP project) via a shim and use it from Python in one session,
covering a header the regex parser can't.

______________________________________________________________________

## Open questions

- **Module shape vs. component shape.** `jm bind <module>` for a
    multi-component module — does it scan every `<mod>_*_core.h` in
    `native/inc/` and emit the module aggregator? Probably yes;
    needs a flag to scope.
- **Test scaffolding.** Should `jm bind` also synthesise
    `test_<comp>_core.c` and the pytest? The headers already describe
    the surface; getter/setter round-trip tests fall out for free.
    Leaning yes; opt out with `--no-tests`.
- **Stability under future preset additions.** Each new preset adds a
    paragraph to the contract. Versioning the contract (a comment in
    `_core.h` like `// jm-bind: contract-1`) would let `jm bind` warn
    when a header is using older conventions.
- **`blockwise` is out of scope.** Array RETURN
    (`T[] -> T[]`) is not yet supported by the renderer — the CLI now
    errors cleanly on it rather than emitting a broken scaffold — so
    there is no `blockwise` shape for bind to target. Array INPUT
    (`arg-type T[]`) works and binds fine.
