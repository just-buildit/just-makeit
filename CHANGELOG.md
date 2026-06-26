# Changelog

## [Unreleased]

### Fixed

- **`jm apply` now honours `[project.bench] block_sizes` (gh-393)** — applying a
    new object replays the scaffold into a temp project, but the temp manifest
    only carried `version` and `[[enum]]`, not `[project.bench]`. So a project
    that configured e.g. `block_sizes = [65536]` (#390) still got the default
    `_1k` + `_64k` suite reintroduced whenever `jm apply` materialised an object
    — the exact drift #390 removes on the `jm object` path. `_apply._replay` now
    carries `[project.bench]` into the temp manifest alongside the enum SSOT.

## [0.19.33] — 2026-06-25

### Added

- **Configurable benchmark block sizes (gh-390)** — the generated Python
    benchmarks (`bench_<obj>.py`) timed `steps()` at a hardcoded `_1k` and
    `_64k`. A project that standardizes on a different set (e.g. dropping the
    small-block suite, since 1 k blocks are dominated by call overhead) had to
    hand-delete the `_1k` functions after every `jm apply`, which `apply` then
    fought. Block sizes are now declarative:

    ```toml
    [project.bench]
    block_sizes = [65536]   # default: [1024, 65536]
    ```

    jm emits one `BLOCK_<label>` constant and one `test_bench_steps_<label>`
    function per configured size (labels: `1024 → 1k`, `65536 → 64k`,
    powers-of-1024 collapse to a `k`/`m`/`g` suffix, anything else the literal
    integer). Sizes are de-duplicated, sorted, and non-positive entries
    dropped; an unset/empty table falls back to `[1024, 65536]`, so existing
    scaffolds are **byte-identical**. Only the Python benches honour this — the
    C `bench_<obj>_core.c` uses a single fixed block and is unchanged.

## [0.19.32] — 2026-06-23

### Fixed

- **inline-function Doxygen now extracted (gh-385)** — a function *defined*
    inline in the header (body in `{ … }` rather than a `;`-terminated
    prototype — e.g. a `JM_FORCEINLINE` block kernel or a `step()` body) was
    never matched by `extract_doc_blocks`, so its `@brief`/`@code` were dropped
    and the method/function fell back to a name stub. `_BLOCK_THEN_DECL_RE` /
    `_DECL_NAME_RE` now accept `{` as well as `;` after the parameter list; the
    `(…)`-before-terminator requirement keeps `typedef struct { … }` from
    false-matching.

- **`variable_output` method with an element `arg_type` rendered a scalar
    `.pyi` input (gh-385)** — the documented blockwise shape
    (`--arg-type 'float _Complex' --variable-output`) consumes a *block*: the
    generated binding parses a numpy array (`PyArray_FROM_OTF`) and the output
    already renders as `NDArray`, but the stub annotated `x: complex`,
    contradicting the API jm itself emits. `_obj_stub` now renders
    `x: NDArray[<dtype>]` for a `variable_output` method whose `arg_type` is a
    non-array element type.

    Together these fix a hand-bound inline block method (e.g. a CIC `decimate`):
    it went from `def decimate(self, x: complex)` + `"""Decimate."""` to
    `def decimate(self, x: NDArray[np.complex64]) -> NDArray[np.complex64]` with
    the full header brief + `@code` doctest.

## [0.19.31] — 2026-06-23

### Fixed

- **module free-function `.pyi` docstrings now synthesize from header Doxygen
    (gh-384)** — object methods derive their stub docstring (brief + Parameters +
    a runnable numpy `Examples` doctest from `@code`) from the sacred
    `<obj>_core.h`, but module-level free functions (`[[module.X.functions]]`)
    fell back to a name-derived one-liner (e.g. `kaiser_enbw` →
    `"""Kaiser enbw."""`), dropping the brief and the `@code` doctest even when
    the header carried them. New `_object._load_module_doc_blocks()` parses
    `<module>_core.h` for free-function Doxygen; `_stubs.make_module_pyi()`
    threads it through via the project `root` (no `cfg` mutation), and a new
    shared `_numpy_doc_lines(…, indent=)` renders methods (8-space) and free
    functions (4-space) identically. A function with **no** header block (a fresh
    scaffold injects a declaration only) keeps the historical one-line stub, so a
    manifest-only rebuild is unchanged — zero `.pyi` churn for projects without
    function Doxygen. Re-applying a project with documented module functions now
    surfaces their `@code` examples in the `.pyi`, where `pytest   --doctest-glob='*.pyi'` exercises them.

## [0.19.30] — 2026-06-21

### Added

- **numpy-style docstrings in handle `.pyi` stubs (gh-374)** — `render_pyi` in
    `_handle.py` now emits a class-level `Parameters` docstring sourced from the
    manifest, surfacing default values and enum choices that were previously
    invisible behind `= ...` in the stub. Methods get one-liner summaries with
    actual defaults inlined (e.g. `send(x, fc=0.0) -> int`); properties show
    enum choices; RAII methods (`open`, `close`, `__enter__`, `__exit__`) get
    static one-liners. Three new helpers — `_pyi_arg_ann`, `_pyi_class_docstring`,
    `_pyi_prop_doc` — cover all cases.

- **numpy-style docstrings in composer `.pyi` stubs (gh-375)** — the same
    docstring treatment now applies to `_composer.py`. `Synth`, `Segment`, and
    `Composer` classes get `Parameters` blocks with defaults and enum choices;
    factory functions (`tone`, `noise`, etc.) get one-liner summaries. New helper
    `_pyi_doc_lines` mirrors the handle pattern.

### Fixed

- **`size_t` (and other `parse_type`) defaults silently zeroed in constructor
    (gh-377)** — the `ctor_scalars` loop in `make_state_ctx` always initialised
    the `_raw` local for `parse_type` scalars (e.g. `unsigned long long` for
    `size_t`) from `parse_zero`, ignoring the state variable's declared default.
    Fixed: use the declared default when it is a valid initializer for the
    `parse_type`. `Py_complex` struct initializers (whose `parse_zero` starts
    with `{`) fall back to `parse_zero` correctly, fixing the accumulator example
    which uses a `double _Complex` state variable.

- **`Install jbx` CI step 404** — the `_JBS` base URL in `ci.yml` and
    `artifact.yml` now points to the stable Pages CDN
    (`https://just-buildit.github.io`) instead of the raw.githubusercontent path
    that 404s after the just-bashit repo reorganised its source tree.

## [0.19.29] — 2026-06-19

### Changed

- **Docs site migrated from zensical to mkdocs-material with animated terminal
    demos** — the landing page now features two `termynal` animated terminal
    widgets: an install-script walkthrough and a `jm new` / `make` / `make test`
    session with real ANSI-matched colors (bold green for passing tests, cyan for
    hints, blue for cmake copy steps). A custom `termynal_fence.py` superfences
    formatter enables ```` ```termynal ``` ```` blocks with `{g}`, `{G}`, `{c}`, `{b}`,
    `{y}`, `{mark}`, `{d}` inline color markup anywhere in the docs. Global
    `.jm-*` CSS utility classes (`jm-green`, `jm-cyan`, `jm-blue`, `jm-yellow`,
    `jm-amber`, `jm-dim`) make the same palette available to prose. The
    `mkdocs.yml` replaces `zensical.toml`; zensical reads `mkdocs.yml` directly
    as a drop-in (no `zensical.toml` required), so the build tool stays
    `zensical>=0.0.29` with `mkdocstrings-python>=2.0` alongside it. MkDocs 1.x
    is now unmaintained and MkDocs 2.0 removes the plugin system, breaking
    mkdocs-material entirely; the Material team recommends zensical as the
    maintained successor for MkDocs 1.x sites
    (https://squidfunk.github.io/mkdocs-material/blog/2026/02/18/mkdocs-2.0/).

## [0.19.28] — 2026-06-19

### Added

- **Module functions: `--check-return` raises on a non-zero status (gh-363)** —
    a `jm function` over a C function that returns an `int` status (0 = success)
    can mark it `--check-return` (manifest `check_return = true`); the generated
    binding captures the result, raises `RuntimeError` on a non-zero code, and
    returns `None` on success — a "succeeds or raises" surface. It is the
    module-function analog of the handle generator's `close_returns` and composes
    with the gh-353 `path`/`enum` args (the path borrow is released before the
    check). Requires an integer `--return-type`; rejects array/result outputs.
    Lets an I/O helper like a path + string-enum header writer be a generated
    function that still raises on failure, instead of hand-written C.

## [0.19.27] — 2026-06-19

### Added

- **Module functions accept `path` and `enum` argument types (gh-353)** —
    `jm function --param p:path` takes a `str | os.PathLike` (coerced via
    `os.fspath` → `PyUnicode_FSConverter`, presented to C as `const char *`),
    and `jm function --param sample_type:enum:stype[=cf32]` takes a choice
    string validated against a `[[enum]]` SSOT and passed to C as the enum's
    `int` index. Both reuse the machinery the handle/composer generators already
    had; the borrowed-`PyBytes` file-handler coercion is now a single shared
    pattern (`_coerce`) the handle generator and the module-function generator
    both route through, so they cannot drift. Unblocks generating I/O helpers
    like a path + string-enum writer as a `jm function` instead of hand C.

### Fixed

- **Installer hardening** — the sourced `install.sh` one-liner no longer shows a
    `[-- path]` placeholder (pasted verbatim it became the venv directory and
    broke CMake's path parsing); both `install.sh` and `install-deps` now accept
    and ignore a bare `--` separator, rebuild the venv with `--clear` so a reused
    directory created by a different Python can't corrupt it, and fail loudly if
    `numpy` or `just-makeit` don't import after install (#352).

## [0.19.26] — 2026-06-18

### Fixed

- **Handle codegen gaps from the doppler#179 review (gh-178)** — four fixes to
    the generators, batched:
    - **Realtime `stream()` paces with the GIL held (review #1)** —
        `Composer.stream(realtime=fs)` slept the per-block pace under the GIL,
        freezing every other Python thread (e.g. a `ZmqSink` consumer). The
        blocking pace now runs inside `Py_BEGIN_ALLOW_THREADS` /
        `Py_END_ALLOW_THREADS`, matching a `nogil` handle method.
    - **`close()` discards `close_fn`'s status code (review #5)** — a handle
        whose destructor reports an `int` rc (e.g. `wfm_writer_close` patches the
        BLUE `data_size` on close and can fail on a short write) silently dropped
        it. A new `close_returns` key makes the generated `close()` / `__exit__`
        capture the rc and raise `RuntimeError` on a non-zero result; the handle
        is still torn down and marked closed before raising (one-shot, no
        double-free). `tp_dealloc` stays silent — a destructor must not raise.
    - **A no-default create-arg parsed as optional (review #6)** — the handle
        `tp_init` emitted an all-optional `|...` format, so a required arg with no
        manifest `default` (`ZmqSink(endpoint)`) parsed as `NULL` and crashed.
        Required args now precede the `|`. A handle method's trailing scalars
        (the `send(iq, fs, fc=…)` shape) likewise parse with keywords + defaults
        instead of positional-only, so a `default` is honoured.
    - **Re-`__init__()` leaks the prior handle (review #9)** — a second
        `__init__()` on a live object overwrote `self->h` without releasing the
        old handle. `tp_init` now tears the prior handle down (mirroring
        `tp_dealloc`) before rebuilding; a no-op on the first construction.

## [0.19.25] — 2026-06-18

### Fixed

- **Composer serializer header `#include` (gh-343)** — a
    `[[module.X.serializers]]` delegated serializer (gh-317, e.g. `to_sigmf`)
    generated a correct call to the project's C serializer `fn`, but
    `<module>_ext.c` never `#include`-d the header that declares it, so the TU
    failed to compile (an implicit declaration whose `int` result decays to the
    `char *` return). A serializer entry now takes an optional `header` key,
    `#include`-d in the generated binding — mirroring `composer.realtime.header`.
    Headers are deduped, in declaration order; absent the key nothing is emitted.
    Closes the test gap that let it through: a new compile probe builds jm's
    *actual emitted* include + a generated-style call with implicit declarations
    a hard error (the gh-343 miscompile), alongside the compiler-free unit gate.

## [0.19.24] — 2026-06-18

### Fixed

- **Reexport reconcile no longer corrupts a mixed hand/generated
    `__init__.py` (gh-342, regression from gh-329/0.19.23)** — three breakages
    when a reconciled module's `__init__.py` mixed generated reexports with hand
    content: (1) the prune stripped `extra_types` (declared public types jm
    emits into the `from .<module> import …` line) from both the import and
    `__all__`; (2) a line-based stale-sweep sheared the opener off an adjacent
    multi-line hand `from .x import ( … )`, leaving an orphaned body
    (`IndentationError`); (3) it deleted hand-added reexports of non-manifest
    siblings. `extra_types` are now in the prune's keep-set, and the stale-line
    sweep is removed entirely — the reconcile only ever rewrites the statements
    it owns (the module's own import line and the manifest's *current* reexport
    lines, both multi-line-safe), never deleting a line it cannot prove it
    generated (the `# noqa: E402` marker is not jm-exclusive). A fully-removed
    reexport sibling now leaves a stale line for the user to delete rather than
    risking hand content. The gh-329 prune of a removed object from the module's
    own line (and `status --check` catching it) is retained.

## [0.19.23] — 2026-06-18

### Fixed

- **Stale reexports pruned, so `jm status --check` catches the drift
    (gh-329)** — the module `__init__.py` reexport merge was purely additive: a
    name removed from `[module.X].objects` or from a `reexports` list stayed on
    its `from .<sub> import …` line, and a whole reexported sibling dropped from
    the manifest left its import line behind — an `ImportError` at runtime that
    `status --check` reported as OK (status replays `apply`, which never pruned).
    The merge is now authoritative: the module's own import line and each
    reexport line carry exactly the manifest's current names (surviving order
    kept, removed names dropped), stale glue lines for siblings no longer
    reexported are swept, and duplicate generated import lines collapse. User
    content — wrapper classes, hand-written imports without the `# noqa: E402`
    glue marker — is preserved (the gh#1 contract). Because `status --check`
    diffs the pruning `apply` against disk, a stale reexport now surfaces as
    drift instead of silently passing the gate.
- **`jm apply` no longer silently promotes a dangling object fragment
    (gh-327)** — an object section listed in no `[module.X].objects` was treated
    as a standalone object and given its own `.so`. When an object was *removed*
    from its module but its `objects/<obj>.toml` fragment (and `native/src/<obj>/`
    dir) was left behind, apply scaffolded a standalone module *over* it,
    overwriting any hand-written `<obj>_core` lib (doppler's `ddcr_core` composed
    vendored sources — apply clobbered its CMakeLists). apply now distinguishes
    the two on disk — a real standalone object's `native/src/<obj>/CMakeLists.txt`
    builds its own extension (`Python3_add_library(<obj> MODULE …)`), a module
    object's carries only the `<obj>_core` OBJECT lib — and **errors** on a
    dangling former-module fragment (naming the object and how to resolve it)
    rather than clobbering. A genuine standalone object, or a fresh one with no
    native dir yet, materializes unchanged.
- **`jm apply` honors `variable_output` / `out_size` for module functions
    (gh-335)** — #318's self-sizing output was dropped on the `apply` replay:
    `_function.run` never accepted the two fields, so the re-saved manifest entry
    lost them and the regenerated binding fell into the plain-`out_type` path
    (`out` inserted first, `_dim` sized from `1` / the first array's length, the
    `out_size` expression ignored), under-allocating the array → the C kernel
    overran it (heap corruption / segfault). `_function.run` now persists both
    fields, `apply`'s replay forwards them, and `fn_c_decl` / `fn_c_stub` append
    `out` LAST (matching the binding's call) instead of after the array params.
    The `jm function` CLI also gains `--variable-output` / `--out-size` so these
    can be authored without hand-editing the manifest.

## [0.19.22] — 2026-06-18

### Added

- **Composer delegated serializers (gh-317 / gh-313)** — a
    `[[module.X.serializers]]` table generates additional serializer methods on a
    composer: each is a `<Composer>.<name>(<params>) -> str` that coerces its
    leading scalar/enum params (enums validate to the SSOT string) and delegates
    to the project's C serializer `fn(<params>, segs, n)` over the resolved
    segments. The sanctioned mechanism for **domain wire formats jm generates
    none of** (SigMF, BLUE, …) — generalizing the one-off `to_json_fn` hatch into
    a first-class, documented carve-out. The generated SSOT `to_json` (gh-287)
    stays the default for the regular case.

### Fixed

- **`kind = "handle"` per-field scalar getter (gh-326)** — `tmp` was typed as the
    field's decoded `type` rather than the getter function's return type, so a
    derived `expr` whose result type differs from the backing accessor (a `bool`
    property over a `double` getter) truncated the value and missed
    `<stdbool.h>`. `tmp` now uses the getter's return type via an optional field
    `returns` (default the field type), and the extension always includes
    `<stdbool.h>`.

## [0.19.21] — 2026-06-18

### Added

- **Stateless `variable_output` module functions (gh-318)** — a module function
    may allocate its OWN self-sized 1-D output, distinct from sizing to an input
    array's length or a caller `out = true` buffer. `variable_output = true` +
    `out_type = "<elem>"` allocates a 1-D array whose length is `out_size` (a
    verbatim-C expr over the args + array `<name>_len`s, e.g.
    `"wfm_rrc_ntaps(sps, span)"` or `"x_len * factor"`); `out` is appended last to
    the call; a void fn returns the full allocation, a `size_t`-returning fn trims
    to the count. Keeps a helper like `rrc_taps(beta, sps, span) -> ndarray`
    zero-Python.
- **Realtime-paced composer `stream()` (gh-317)** — a `[module.X.composer]   realtime = {clock_create, pace, destroy, header}` sub-table paces the
    generated `stream()` iterator to an `fs`-Hz clock **in the `.so`**
    (`for blk in c.stream(4096, realtime=1e6): …`), so a project drops its
    hand-written `paced()` helper. The iterator holds an opaque clock created
    lazily on the first block and destroyed with the iterator; off (and the plain
    `stream()` byte-for-byte unchanged) when the sub-table is absent.

## [0.19.20] — 2026-06-18

### Added

`kind = "handle"` gains the shapes a streaming/resource handle needs beyond the
initial I/O archetype — each driven by the doppler transport adoption and proven
end-to-end by a real compile (a second `init_fn` backing now joins the toy
`ringbuf` in the build harness).

- **Caller-buffer execute method, shape (d) (gh-311)** — an array-in arg plus a
    `writable = true` array arg with an array `returns` marshals a borrowed input
    and a writable **exact-dtype** output (no silent cast), calls
    `fn(h, in, n_in, out, max_out)` under optional `nogil`, and returns the
    zero-copy `out[:n_out]` view (which pins the caller's array). Mirrors the
    capsule execute on the typed handle.
- **Writable scalar property (gh-311)** — a scalar (return-by-value) getter whose
    field names a `writable_fn` emits the getset `(setter)` slot, coercing the
    value via `PyArg_Parse` and calling `set_fn(self->h, v)`.
- **Per-field scalar getters (gh-314)** — a getters table whose fields each name
    their own `getter = "T fn(h)"` (no shared `fn`/`out`), so a project drops the
    hand-C struct shim that existed only to bundle scalar getters into the
    one-struct-getter decode. The same `enum` / `scale` / `expr` transform menu
    applies (the scalar is `tmp`).
- **Init-in-place constructor (gh-315)** — `init_fn = "void init_fn(T*, args…)"`
    over a caller-allocated struct: jm `malloc`s `sizeof(<handle_type>)`, calls
    `init_fn`, and `free`s on `close` / `tp_dealloc` (after an optional `close_fn`
    finalizer). Mutually exclusive with `create_fn`; drops the malloc/init/free
    shim around a public struct.
- **Keyword / default args on handle methods (gh-319)** — scalar-arg methods parse
    with `PyArg_ParseTupleAndKeywords` (a `|` before the defaulted tail) and
    register `METH_VARARGS | METH_KEYWORDS`, so `m(on=True)` and a `default` both
    work instead of forcing positional calls.

## [0.19.19] — 2026-06-17

### Added

The `kind = "handle"` generator (gh-306) — a typed CPython class over an
**opaque hand-C resource handle** (a file writer, socket, clock, session). It is
the *intersection* of the `kind = "capsule"` generator (opaque backing +
lifecycle + numpy marshaling) and the `kind = "composer"` generator (typed-class
face): one `PyTypeObject` with a constructor, methods, decoded-from-a-getter
properties, and an RAII (`close()` / context-manager) protocol. Like the other
generators it materializes a self-contained `.so` from the manifest alone, with
a take-it-or-leave-it `.pyi`, and carries a non-waveform `ringbuf` example
proving zero domain coupling.

- **The typed handle class** — `tp_init` coerces `create_args` (enum-string →
    index via the `[[enum]]` SSOT, `os.fspath` for a `path` arg, scalar casts),
    calls the backing `create_fn`, and runs an optional conditional `create_post`
    setter. Methods map `name → fn(self->h, …)`: scalar args, an array-in arg
    (numpy-marshaled like the capsule path), or an int-in → independent
    numpy-owned array-out.
- **Decoded-getter properties** — one shared C getter fills an out-struct; each
    property decodes a named field with a `plain` / `enum` / `scale` / verbatim-C
    `expr` transform. A `cache = true` getter is resolved once in `tp_init`.
- **Array + trailing scalar methods (gh-308)** — a method with an array arg
    followed by scalars (`send(iq, fs, fc)`) marshals the array and threads the
    scalars through to `fn(h, in, n, …)`; more than one array arg fails loud.
- **Real-compile CI harness** — `test_handle_build.py` scaffolds, runs
    `jm apply`, compiles the generated binding against a real C backing, imports
    it, and exercises the type — the first real compile of handle output in CI.

### Fixed

- **`cache = true` handle getters** were never resolved in the constructor (the
    cache fetch was defined but not emitted into `tp_init`), so a cached property
    returned a zero-initialized value instead of the getter's output. `tp_init`
    now resolves every `cache = true` getter after `create_fn` / `create_post`.

## [0.19.18] — 2026-06-17

### Added

Round 3 of the `kind = "composer"` generator (gh-287) — generic "object of
objects" ergonomics generated **into the `.so`**, so a bare import gives the
full typed OO surface and a project hand-writes only its algorithm. Every
feature is driven by the `source.fields` / `segment.fields` + `[[enum]]` SSOT
and carries a non-waveform test proving zero domain coupling.

- **Source standalone generation (`source.generates`)** — a source type
    generates samples on its own (`Synth(...).steps(n)` / `.step()` / `.reset()`)
    by delegating to a composed generator built once from the source struct by a
    project-provided straight-C `bridge_fn`. jm emits the plumbing (cached lazy
    handle, variable-output `steps()`, scalar `step()`, `reset()`); the bridge is
    pure C, no CPython.
- **Field aliases + `bit_pattern` coercion** — generated into `tp_init` so a
    project drops its hand-written ctor wrapper: `aliases = [...]` lets a kwarg
    stand in for the canonical field (both-given raises `TypeError`); the
    `bit_pattern` coercion accepts a 0/1 pattern as bytes, a binary/hex string,
    or a sequence of ints.
- **Generated `stream()` iterator** — `[module.X.composer] stream = true` emits
    `<Composer>.stream(block=4096)`, an internal iterator that drains the
    composer's own `execute()` into blocks (empty block → `StopIteration`), so a
    project drops its hand-written `for blk in c.stream(n):` wrapper.
- **Flat single-source `Segment` accessors** — setting `flat_sources = true` in
    the `[module.X.segment]` table proxies a single-source segment's source
    fields as read-only attributes (`seg.freq` → `seg.sources[0].freq`); a
    multi-source segment
    raises `AttributeError`. Names come from `source.fields`; a collision with a
    segment-level attribute is skipped (the segment's own attribute wins).
- **Subclass-friendly `from_json` / `from_file` + generic `to_dict()`** — the
    alternate constructors allocate via `cls` (`tp_alloc(type, 0)`) so a
    `Composer` subclass round-trips through them instead of being downcast;
    `[module.X.composer] to_dict = true` generates `Composer.to_dict()` returning
    the resolved composition as a plain nested dict — the generic introspection
    primitive any sidecar metadata format (SigMF, BLUE, …) is built from in
    Python. jm generates none of those formats itself.

## [0.19.17] — 2026-06-17

### Added

- **Composer OO types are subclassable (gh-287)** — the generated
    `Synth`/`Segment`/`Timeline`/`Composer` types now carry
    `Py_TPFLAGS_BASETYPE`, so a project can keep the ergonomic generated types in
    the `.so` and add domain-specific conveniences via a thin Python subclass
    (e.g. `.steps()`, pattern-string sugar, a `.stream()` generator) instead of
    hand-rolling a parallel OO layer. Subclasses still flow through the generated
    `Composer`, which type-checks inputs with the subclass-accepting
    `PyObject_TypeCheck`. Strictly additive — the flag only opens subclassing;
    direct use is unchanged.

## [0.19.16] — 2026-06-16

### Added

- **`[[enum]]` single-source-of-truth for string enums (gh-285)** — a named
    top-level `[[enum]]` declares an ordered value set once; a parameter refers
    to it with `type = "enum:<name>"` instead of inlining
    `string_enum:a,b,c` on every face that touches it. The reference resolves to
    the equivalent `string_enum:` spec on the codegen read path (`init_params()`,
    the one choke point every consumer reads), so choice flags, `.pyi` stubs, and
    the C enum index are unchanged, while the manifest keeps the `enum:` reference
    verbatim on disk — exactly one place the value list lives. Value order **is**
    the C integer value (append-only, never reorder); an undeclared reference
    raises a clear error. `[[enum]]` is manifest-owned (like `[project]`/`[app]`),
    gated behind schema 7 (additive and opt-in — inline `string_enum:` keeps
    working; the version bump alone is the migration). This is the keystone for
    the generated capsule (#286) and composer (#287) work.
- **`kind = "capsule"` module generator (gh-286)** — a generated CPython
    extension that exposes its C state as **free functions over an opaque
    `PyCapsule`** instead of a `PyTypeObject` per object, for functional/
    procedural C APIs that don't fit the class model. A capsule module declares
    a `backing` (`<backing>_state_t` + `<backing>_create`/`_destroy`), its
    `init_params`, `methods` (numpy-in → caller-owned numpy-view-out, with an
    optional `nogil` GIL release across the kernel), and `properties`
    (`get_`/`set_` accessors); `jm apply` generates the `<module>_ext.c` capsule
    mechanics (the use-after-destroy guard, marshaling, a zero-copy `out[:n_out]`
    view), the module `CMakeLists.txt`, and a `.pyi` stub, and `jm status   --check` covers them. The kernel bodies stay hand-written in
    `<backing>_core.c` (sacred). doppler's `ddc_fn` is the reference adopter:
    it drops `no_generate` for `kind = "capsule"` with a byte-identical link
    list. This is the runtime skeleton the composer (#287) builds on.
- **`kind = "composer"` module generator (gh-287)** — turns jm from a
    one-binding-per-struct scaffolder into a templating engine that **composes
    objects of objects**. A composer declares `source`/`segment`/`timeline`/`oo`
    sub-tables and generates, into the `.so`, the full ergonomic OO surface as
    real CPython types: a source config type (`Synth` — keyword `tp_init`,
    per-field getset, enum-validated assignment, factory functions) with
    `Segment.sum`/`Segment.add`, a `Timeline` sequence, and a `Composer` that
    drives the backing `<backing>_compose_*` kernel (`execute`/`compose` with
    zero-copy complex64 slices + GIL release, `segments`/`repeat`/`continuous`
    reflection, context-manager `close`). JSON faces (`to_json`/`from_json`/
    `from_file`) are **generated from the `source`/`segment` fields + the
    `[[enum]]` SSOT** (one `_enum_*` table, no per-face duplication), with
    hand-serializer delegation as an opt-in escape hatch for exact wire-compat.
    An opt-in `[module.X.cli]` emits a generic pure-C `main()` that builds from
    field flags or `--from-file` and streams via `jm app`'s output axes
    (`--sample_type`/`--file-type`/`--endian`). `jm apply` materializes the
    `_ext.c`/`CMakeLists.txt`/`.pyi` (+ optional `_cli.c`); `jm status --check`
    covers them. Kernels stay hand-written in `<backing>_core.c` (sacred).
    Validated byte-exact against doppler's `compose.py` across every spec shape.

## [0.19.15] — 2026-06-15

### Changed

- **`depends_on` is flattened transitively when rendering per-object
    `CMakeLists.txt` (gh-280)** — a CMake OBJECT library doesn't propagate its
    objects through transitive PUBLIC linking, so every core a
    `test_<obj>_core` / `bench_<obj>_core` (and the module `.so`) ultimately
    pulls in must appear **directly** on its link line. Previously each object
    had to hand-list the full transitive closure on its own `depends_on`
    (redundant with the graph jm already holds, and silently stale — inserting a
    level mid-chain turned every downstream object's closure into a build-time
    `undefined reference`, not a manifest error). jm now walks the `depends_on`
    graph (cycle-guarded, deduped, direct-first) and emits the closure itself,
    so an object declares only its **direct** deps: `depends_on = ["corr_core"]`
    yields `corr_core` + the transitively-reached `fft_core` on every link line.
    Applies to the standalone, module non-collocated, collocated, and module
    `.so` link paths. Projects already listing full closures are unaffected
    (the walk dedupes to the same set).

### Added

- **`record_module` for a single-record method's structseq `__module__`
    (gh-261)** — a `single = true` result-fields method generates a
    `PyStructSequence` whose `__module__` was hard-wired to the C component name
    (`type(r).__module__ == "tonemeas"`), not the package it's imported from
    (`"doppler.measure"`); `record_name` (gh-257) only controls the final
    segment. A method can now set `record_module = "…"` in the manifest (or
    `jm method … --record-module my_pkg.dsp`) to qualify the structseq's
    `__module__` / `repr` with the project's import path. Round-trips through
    `jm apply`; unset → component-qualified as before (byte-identical).

## [0.19.13] — 2026-06-15

### Fixed

- **A `required` init-param with no `default` generated failing smoke tests /
    doctests for validating constructors (gh-273)** — `required` (gh-266) is most
    useful for params that have no sane default and *are* validated (sample rate,
    span, size). But jm seeded the generated `.pyi` construction doctest, the C
    smoke (`create(0, …)` → `CHECK(obj != NULL)`), and the pytest case with the
    type's **zero**, which such a constructor rejects — so a fresh scaffold was
    red (`MemoryError: …_create returned NULL`, `1 failed` under
    `--doctest-glob='*.pyi'`). jm has no valid value to seed, so the generated
    tests now **defer** instead of asserting: the `.pyi` omits the construction
    doctest, the C smoke treats a NULL return as a skip (prints a note, returns
    0\) rather than `CHECK`-ing it, and the pytest case is skipped (a `setUp`
    `skipTest`, or a module `pytestmark` for the pure-pytest file) with a note to
    pass valid arguments. Declaring a `default` **as well** (`required = true`
    with `default = "…"`) keeps the param a mandatory positional but seeds the
    smoke/doctest with that value — the supported way to get a runnable example
    for a validated required param. Fully-seedable objects are byte-identical.

### Fixed

- **The gh-271 per-object CMakeLists reconcile clobbered hand-owned build rules
    (gh-275, regression in 0.19.11)** — 0.19.11 re-rendered a module object's
    `native/src/<obj>/CMakeLists.txt` from the manifest, which silently dropped
    bespoke build wiring the manifest cannot express: extra `add_library`
    sources (vendored `.c` compiled straight into `<obj>_core`),
    `set_source_files_properties`, and a hand-added `target_link_libraries(...   PUBLIC m)`. In doppler, `fft_core` compiles in pocketfft + PFFFT this way,
    so every FFT consumer broke with `undefined reference`. The reconcile now
    detects a **hand-owned** per-object CMakeLists — one carrying extra
    `add_library` sources, `set_source_files_properties`, or an
    `add_custom_command`/`add_custom_target` — and leaves it untouched (and
    `status --check` reports it as up to date), while pure-jm objects still get
    the gh-271 `depends_on` reconcile.

## [0.19.11] — 2026-06-15

### Fixed

- **`jm apply` didn't reconcile a per-object `CMakeLists.txt` when its
    `depends_on` changed (gh-271)** — for a non-collocated multi-object module
    (doppler `measure` = `tonemeas`/`imdmeas`/`nprmeas`), changing an existing
    object's `depends_on` updated the module `.so` aggregator but left the
    object's own `native/src/<obj>/CMakeLists.txt` stale: the new dep was
    missing from its `_core` PUBLIC, `test_<obj>_core`, and `bench_<obj>_core`
    link lines, so the C test failed to link (`undefined reference`). `jm apply`
    only ever *added* link lines, so once the `target_link_libraries(<obj>_core   PUBLIC …)` block already existed the change was dropped — and
    `jm status --check` missed the drift because it observes the same skipped
    reconcile. Apply now reconciles the per-object file from the canonical
    render (picking up added *and* removed deps on every link line) while
    preserving component `extra_include_dirs` and user `if(VAR)` external-library
    blocks; `status --check` now flags the stale link line as drift.

## [0.19.10] — 2026-06-14

### Added

- **`[project] c_style` — emit C in the project's house style (gh-265)** —
    jm emits its own canonical 4-space C, so a project with a different
    committed style (doppler, jm's poster-child, uses GNU 2-space) had to run
    `clang-format` over the generated `native/**` fragments by hand after every
    `jm apply` — a documented, every-apply manual step. Setting
    `c_style = "clang-format"` (or `jm new --c-style clang-format`, which also
    seeds a `.clang-format`) makes jm run that pass itself after every mutating
    command, so emitted code already matches the committed `.clang-format`.
    Off by default → output is byte-identical for existing projects. A missing
    `clang-format` binary is a soft failure (a warning; the command still
    succeeds).
- **`required` init-param flag (gh-266)** — block-I/O objects had all-optional
    init params (a `|kkk` PyArg format), so `Component(0, 0, 0)` returned NULL
    and surfaced a late, opaque `MemoryError`; there was no declarative way to
    mark an init param mandatory (doppler `CLAUDE.md:124`). A scalar init-param
    declared `required = true` (CLI `--init-param name:type:required`) now parses
    as a positional **before** the PyArg `|`, so omitting it raises a clear
    `TypeError` at construction instead. Required scalars are hoisted ahead of
    the defaulted params in the constructor, the `.pyi` (no default, required
    first), and the docstring; the generated smoke test seeds them with the
    type's zero. Round-trips through `jm apply` / the manifest. Array
    init-params are already required positionals, so `required` is rejected for
    them. Default off → output unchanged for existing projects.

## [0.19.9] — 2026-06-14

### Fixed

- **`nogil` was silently ignored on `single`-record methods (gh-261)** — a
    `single` result_fields method that also set `nogil = true` generated a
    binding that called the by-value kernel with the GIL **held** (no
    `Py_BEGIN_ALLOW_THREADS`), so a pure-C record sweep (doppler's
    `tonemeas.analyze`) couldn't scale thread-per-shard. The single-record
    binding now wraps the kernel call in `Py_BEGIN/END_ALLOW_THREADS` when
    `nogil` is set — hoisting the numpy array fetch above the block and keeping
    the input's `Py_DECREF` under the GIL after — matching the `variable_output`
    `nogil` path. Non-`nogil` output is byte-identical.

### Added

- **`functions_in_core` — module functions in one TU (gh-247)** — a module's
    free functions are generated one `.c` per function by default. Flag a module
    `functions_in_core = true` (or `jm module <m> --functions-in-core`) to append
    every function body to the shared `<m>_core.c` instead — so `static` helpers
    live once, the module is a single translation unit, and CMake lists only
    `<m>_core.c`. Off by default, so existing projects are byte-identical. The
    flag round-trips through `jm apply` (which previously resurrected the
    per-function files).

## [0.19.8] — 2026-06-14

### Added

- **`record_name` for single-record methods (gh-257)** — a `--single` method's
    public record type defaulted to the name derived from its C `--return-type`
    (`tone_meas_t` → `ToneMeas`). You can now pick the name explicitly with
    `jm method … --single --record-name ToneMetrics` or `record_name = "…"` in
    the manifest, so the `PyStructSequence` is named independently of the C
    struct.

### Fixed

- **Manifest-authored `single` + scalar param defaults were dropped by
    `jm apply` (gh-257)** — gh-244 wired `jm method --single` /
    `--param x:T=default` via the CLI + codegen, but the manifest→apply
    round-trip that regenerated projects use never read them back, so authoring
    those keys in TOML was silently ignored. `_apply` now forwards `single` and
    3-tuple params (with defaults); `_stubs` types a `single` record as
    `tuple[...]` and renders param defaults; and the single-record
    `PyStructSequence` binding threads scalar method params (the
    `analyze(x, lo, hi, …, guard_hz=0.0)` shape) into both the parse block and
    the by-value kernel call.
- **`jm apply`/`save` silently dropped unknown method keys (gh-257)** — the
    manifest write pass enumerated only the method keys it knew, so any
    hand-authored key (such as `record_name`) never survived a
    `save()`→`load()` round-trip. The serializer now preserves any scalar method
    key it doesn't explicitly handle (transient `_`-prefixed and list/table keys
    excepted), so manifest-authored keys round-trip. Zero churn — jm only writes
    known keys, so existing manifests are byte-identical.

## [0.19.7] — 2026-06-14

### Fixed

- **`depends_on { link = true }` broke a collocated module-object's own
    test/bench (gh-254)** — when an object's name equals its module's (collocated,
    e.g. doppler's `ddc`/`ddcr`), the `.so` aggregator and the object-core
    test/bench share one `CMakeLists.txt` that `jm apply`/`jm object`
    regenerates. `link=true` linked the dependency core onto the `.so` but
    dropped it from `test_<obj>_core`/`bench_<obj>_core`, so an object whose
    `_core.c` **composes** a sibling failed to link (`undefined reference`).
    `link=true` is now **additive** for the collocated path: the dependency
    `<dep>_core` is linked directly onto the object's own test/bench (and PUBLIC
    on its `_core`) **and** the `.so` — matching the non-collocated behaviour. No
    change when an object has no `depends_on`.

### Added

- **Controllable overrides through the SIMD `JM_DEFINE_STEPS` macro (gh-240)** —
    the perf-path macro gains a `JM_DEFINE_STEPS_EX(fn, …, CPARAMS, CARGS)` form
    that threads a controllable override into the generated `fn_steps()`
    signature, the scalar tail call, and the `fn_step_batch()` SIMD call. Plain
    `JM_DEFINE_STEPS` now forwards to it with empty suffixes, so existing perf
    code (e.g. the `fir_filter` example) expands byte-identically. This closes
    the last gap of the optional/default-parameters epic: a controllable field
    now works with hand-written SIMD `steps()`, not just the generated
    plain-loop. Usage: `JM_DEFINE_STEPS_EX(comp, comp_state_t, float, L, B, C,   (, float gain), (, gain))`.

- **Controllable overrides on every `step()`/`steps()` shape (gh-240)** —
    `controllable = true` now works on **all** non-perf shapes: scalar→scalar,
    scalar→void sinks, void-arg generators and ticks, array-input `step()`, and
    blockwise array→array. For a void-input generator (`step() -> y`) the
    override is the only argument, so `step()` flips from `METH_NOARGS` to a
    positional-optional `step([gain])` when a field is controllable;
    non-controllable generators/sinks are byte-for-byte unchanged. The control
    param threads through both `step()` (positional) and `steps()` (keyword)
    consistently, in delegate and non-delegate modes. Complex scalars and
    `--no-step` are rejected at generation with a clear error. Retrofitting a
    controllable field onto an existing object needs `jm regenerate` (it changes
    the sacred `comp_step()`/`comp_steps()` signature), not `jm apply`.

- **Controllable `step()` overrides + `out=` keyword unification (gh-240)** —
    the `controllable = true` flag now also reaches **`step()`** on the
    scalar→scalar shape. `step(x)` reads the live field; `step(x, override)`
    overrides it for that one sample, **positionally only** (`PyArg_ParseTuple   "f|f"`, never `METH_KEYWORDS`) because the parse is paid per-sample — a
    keyword call would cost ~3.4× the call. The field threads through *both*
    `step()` and `steps()` consistently (they share the per-sample algorithm),
    in delegate and non-delegate modes. Measured: the omit path is
    indistinguishable from a non-controllable baseline (~0 ns), passing the
    override adds ~2 ns. Folded in: every built-in `steps()` now parses with
    `PyArg_ParseTupleAndKeywords`, so `steps(x, out=buf)` works as a keyword
    everywhere — not only when a field is controllable. Scope: blockwise
    array→array and scalar→scalar, real-scalar (float/int) fields; array-input
    `step()`, void-arg generators/sinks, and complex scalars are rejected at
    generation with a clear error (deferred follow-ups). The `.pyi` types
    `step()`'s overrides positional-only (trailing `/`) and `steps()`'s as
    keyword args.

- **Controllable `steps()` overrides (`controllable = true`, gh-240)** — a state
    field flagged `controllable = true` in the manifest becomes an optional,
    keyword-capable per-call override on the object's `steps()`. Omitting it
    reads the live field (`obj.steps(x)` uses `self->gain`); passing it overrides
    for that block only (`obj.steps(x, gain=2.0)` or positionally
    `obj.steps(x, None, 2.0)`), never mutating the field. The flag threads the
    param into the C `comp_steps(state, in, n, out, gain)` signature (the one
    declared, intentional change to the sacred core) and sources it
    `arg-if-provided else self->field`; the binding moves to
    `PyArg_ParseTupleAndKeywords` (`METH_VARARGS | METH_KEYWORDS`) so the parse
    amortizes over the block. Scope: the blockwise array-in / array-out
    `steps()` shape with real-scalar (float/int) fields; other shapes and complex
    scalars are rejected at generation with a clear error. Declaration is
    TOML-first and round-trips through `jm apply` / `jm regenerate`. This
    completes the optional/default-parameters epic (`step()` per-sample control
    params remain deferred — see `docs/arguments.md`).

## [0.19.6] — 2026-06-14

### Added

- **Single named-record method returns (`jm method … --single`, gh-244)** — a
    `result_fields` method can now return **one named record** instead of a
    `list[tuple]`. With `--single`, the C kernel returns the record struct by
    value (`<return_type> method(state, …)`), and the binding unpacks it into a
    `PyStructSequence` — named attribute access *and* tuple unpacking:
    `r = obj.analyze(x); r.snr, r.enob; snr, *_ = r`. The structseq type is
    created lazily and cached in the translation unit (no module-init/aggregator
    changes). The `--return-type` of a `result_fields` method now also accepts a
    user record struct (previously rejected by the scalar allowlist). The `.pyi`
    types it as `tuple[…]` of the field types (named-attribute typing via a
    full `NamedTuple` stub is a possible refinement).

### Fixed

- **`size_t` (and other `parse_type`) init-param defaults were dropped (gh-244)**
    — a `parse_type` init param parses into a `<parse_type> <name>_raw`
    intermediate (size_t via the `K`-format `unsigned long long`), and its
    declared `default` must seed that `_raw` local. The generator used only the
    rarely-set `default_raw`, so an integer default like `n = 8192` silently
    initialised to `0` — the constructor then built with `n=0` (NULL →
    `MemoryError`) or a wrong value (e.g. `pad=0`). `double`/`float` defaults
    took a different branch and were unaffected. The `_raw` local now seeds from
    `default_raw`, then the plain `default`, then the type's zero.

## [0.19.5] — 2026-06-14

### Added

- **Optional `jm method` params with defaults; named methods are now
    keyword-capable (gh-240)** — `jm method obj m --param gain:double=1.0` makes
    `gain` an optional arg (`obj.m(x)` uses the default, `obj.m(x, 2.0)` /
    `obj.m(x, gain=2.0)` overrides). As part of this, named methods with params
    moved from positional-only (`METH_VARARGS` + `PyArg_ParseTuple`) to
    positional-or-keyword (`METH_VARARGS | METH_KEYWORDS` +
    `PyArg_ParseTupleAndKeywords`), matching functions and constructors — keyword
    capability is ~free when callers pass positionally. Defaulted params go after
    the `|`, the C local is seeded to the default, and the `.pyi` shows
    `name: type = default`. Same rules as the function feature: defaults must
    follow required params; plain scalars only. (`steps()` defaults follow next.)

- **Optional `jm function` params with defaults (gh-240)** — a scalar param may
    declare a default: `jm function fn --param gain:double=1.0`. The param
    becomes optional — `fn(x)` uses the default, `fn(x, 2.0)` / `fn(x, gain=2.0)`
    overrides. The default goes after the `|` in the binding's
    `PyArg_ParseTupleAndKeywords` format with the C local seeded to it, and shows
    in the `.pyi` as `gain: float = 1.0`. Defaulted params must follow required
    ones (the PyArg `|` rule == Python's "no required after default"); only plain
    scalars take defaults (arrays / `out=` / complex stay required). The
    omit-the-default path is ~free (see [Arguments](arguments.md)). Named-method
    and `steps()` defaults follow next.

- **`jm function` bindings are positional-or-keyword (gh-238)** — a generated
    module-level function with parameters now accepts keyword arguments
    (`dsp.scale_add(x=x, out=out, gain=2.0)`), matching constructors and named
    methods. The binding emits `METH_VARARGS | METH_KEYWORDS` +
    `PyArg_ParseTupleAndKeywords` with a `kwlist` of the parameter names; a
    no-parameter function stays `METH_NOARGS`. Keyword *capability* is
    near-free (~0–5 ns/call) when callers still pass positionally — the keyword
    match cost (~12–25 ns/arg) is paid only when keywords are actually used. The
    per-sample hot path (`step()`/`steps()`) stays positional-only.

- **New guide: [Arguments: positional vs keyword](arguments.md)** — documents
    what parsing jm emits where, the measured cost of each parsing style
    (including default/optional arguments), and the project-wide rule:
    positional-only for the `step()`/`steps()` hot path, positional-or-keyword
    for constructors, methods, and functions.

### Changed

- **Examples track doppler 0.15.1 and the Docker image drops `g++`.** The
    `nco_tone` / `kitchen_sink` examples bump their pinned doppler auto-download
    from 0.13.2 to **0.15.1**, whose `doppler::doppler-static` archive is
    **C++-free** (C99 pocketfft; the ZMQ/stream layer split into the optional
    `doppler::stream` targets). Linking it now resolves with `-lm` alone, so
    `docker/Dockerfile.examples-linux` no longer installs `g++` for the slim
    image. (Supersedes the examples half of the now-closed #229, whose
    `--step-delegates-to-steps` feature already shipped via #228.)

## [0.19.4] — 2026-06-13

### Documented

- **Polymorphic constructor dispatch is already supported (gh-224)** — selecting
    the C constructor from an array init-param's dtype or presence does not need
    a new `init_variants` table; the existing `[[<comp>.init_params]]` fields
    express both shapes. `real_type` + `real_create_fn` give dtype dispatch
    (e.g. float32 taps → `fir_create_real`, complex64 taps → `fir_create`);
    `optional = true` + `create_fn` give presence dispatch (e.g. a supplied
    `bank` → `Resampler_create_custom`, omitted → `Resampler_create`). Added a
    dtype-dispatch codegen test to lock the generated form.
- **Multi-return packing is already supported (gh-223)** — returning more than
    one array, or a list of fixed-shape records, needs no new
    `return_type = "tuple(...)"` / `list_of` syntax. A **tuple of arrays** (e.g.
    NCO `steps_u32_ovf(n) -> (uint32 phase[], uint8 overflow[])`) is a
    `variable_output` method with `multi_output = ["uint8_t"]`; the binding
    packs the outputs with `PyTuple_Pack`. A **list of fixed-shape records**
    (e.g. a detector `push` returning `(lag, peak_mag, noise_est, test_stat)`
    tuples) is `result_fields = [...]` + `max_results_param`, with `return_type`
    naming the C record struct; the binding fills a bounded buffer and returns a
    `list` of `Py_BuildValue` tuples. Added a test locking both exact shapes.

### Added

- **`depends_on` can own the link line (gh-225)** — a dependency entry may now
    be a table `{ name = "fir", link = true }` instead of a bare string. With
    `link = true`, jm adds the dependency's `<name>_core` directly to the
    consuming target's `target_link_libraries` (the standalone component's or
    the module's `.so`), so its symbols resolve in the built extension without a
    manual `extra_link_libs` entry *and* a hand-edited `target_link_libraries`
    in CMakeLists — and `jm status --check` now covers that link. A bare-string
    dependency is unchanged (header include + objects into the aggregate lib,
    no `.so` link). The link goes directly on the consuming target, not PUBLIC
    on `<comp>_core`, because CMake does not propagate an OBJECT lib's objects
    transitively into a `.so` (gh-160).

### Fixed

- **`jm app` exe-target name collision (gh-184)** — `jm app --target c --name X`
    over a project that already has a module or component target named `X` (e.g.
    `--name wfmgen` against a `wfmgen` module) emitted `add_executable(X …)`,
    which CMake rejected with *"another target with the same name already
    exists."* The app now uses a distinct exe target id (`X_app`) with
    `set_target_properties(X_app PROPERTIES OUTPUT_NAME X)` so the built binary
    stays `X`; non-colliding names are unchanged. Detection runs on the
    user-facing `--name`, so re-running `jm app` / `jm apply` is idempotent (no
    `X_app_app`). This completes gh-184 (parts 1 & 2 shipped in 0.16.0).

- **`jm function --out-param` confirmed end-to-end (gh-221)** — a writable array
    output param (`out = true`, → non-const `T *` + `NPY_ARRAY_WRITEABLE`) can be
    declared from the CLI via `--out-param name:type[]`, threaded through
    `_function.run()` as a 3-tuple, and persisted to the manifest. The CLI parser
    (since 0.13.22) and renderer (gh-197) already supported this; added a
    round-trip regression test covering the CLI/`run`/manifest/C seam and tidied
    the now-misleading `list[tuple[str, str]]` param type hints to admit the
    `(name, type, is_out)` form.

## [0.19.3] — 2026-06-13

### Added

- **`jm object --step-delegates-to-steps` (gh-208)** — generates the scalar
    `step()` as a thin delegator to `steps()` (`step(x) { T y;   comp_steps(state, &x, &y, 1); return y; }`) instead of inlining a separate
    per-sample body. The per-sample algorithm then lives in exactly one place
    (`steps()`), so `step()` and `steps()` are **byte-identical by
    construction** — closing a real `-ffast-math` divergence on FMA targets
    (arm64/Apple Silicon) where an inlined scalar `step()` contracted
    `a*b + c` into an FMA while a vectorized `steps()` did not. Recorded as
    `step_delegates_to_steps = true` and round-tripped by `jm script`/`apply`.
    Applies to scalar / void-arg objects (the flag is rejected for `--no-step`,
    `--variable-output`, and array `--arg-type`/`--return-type`, which already
    centralise the algorithm in `steps()`). In delegate mode the user's
    `steps()` must not call `step()` or use `JM_DEFINE_STEPS` (its scalar
    fallback calls `step()`) — that would recurse. Guarantees `step() ==   steps(.., 1)`, not `step()`-loop `== steps(N)` (chunk-invariance of a
    vectorized `steps()` remains the project's responsibility).

## [0.19.2] — 2026-06-13

### Added

- **`out=` buffer for named `batch` methods (gh-222)** — a named 1:1-rate
    `--batch` method now accepts an optional `out=` buffer:
    `y = obj.process(x, out=buf)` (or positionally) writes in place and returns
    `buf`, else allocates a fresh array as before. The buffer is validated
    C-contiguous, writeable, dtype-matching, and length-equal to the input
    (or `count`). Always available — no knob — matching the built-in
    `steps(x, out=)` path that already covers fixed-size objects. (The built-in
    `steps()` for scalar→scalar / blockwise objects already had `out=`; this
    closes the gap for named batch variants.)
- **`out=` buffer for `variable_output` methods (gh-219)** — single-output
    `variable_output` execute methods now accept an optional `out=` keyword:
    `y = obj.execute(x, out=buf)` fills the caller's array (zero allocation),
    returns a view of the filled prefix pinned to *their* buffer, and is
    therefore safe to retain — parity with the blockwise `steps(x, out=)` path.
    A `obj.<verb>_max_out()` sibling is generated so callers can size the
    buffer (`buf = np.empty(obj.execute_max_out(), dtype=...)`). The buffer is
    validated as C-contiguous, writeable, dtype-matching, and `>= max_out`.
    Multi-output and multi-param execute keep their positional-only signatures.

### Fixed

- **Use-after-free in the `variable_output` zero-copy default (gh-219)** — the
    grow-on-demand internal buffer was `realloc`'d in place, so a previously
    returned array (which pins `self`, not the buffer) could end up aliasing
    freed memory after a grow. The grow path now allocates a fresh buffer and
    *retires* the old one to a per-method freelist freed at dealloc, so retained
    views stay valid. The fixed-block streaming hot path is unaffected (no
    growth after warmup means nothing is ever retired).
- **Module-function `out = true` array params emitted `const T *` (gh-197)** —
    a writable output param on a `[[module.<m>.functions]]` entry rendered a
    `const T *` pointer (and a `discards 'const' qualifier` build warning)
    because the renderer projected params to `(name, type)` tuples, dropping the
    `out`/`mutable` flags before they reached the pointer-qualifier decision.
    They are now threaded through as full dicts, matching standalone-object
    method params.

## [0.19.1] — 2026-06-11

### Added

- **Nested module subpackages** — a module id may now be **dotted**
    (`jm module dsp.filters`, `jm object fir --module dsp.filters`), nesting the
    extension under `src/<pkg>/dsp/filters/` so it imports as
    `from pkg.dsp.filters import Fir`. Arbitrary depth is supported. The single
    name's three roles are split by `_config.module_paths`: the leaf
    (`filters`) drives `PyInit_`/`.m_name`/the `.so` basename; the cname
    (`dsp_filters`) keeps the native `CMakeLists`/`add_subdirectory` machinery
    flat and unique; the pypath (`dsp/filters`) places the Python package, with
    plain `__init__.py` markers created for the intermediate packages. Flat
    (dotless) modules render byte-for-byte unchanged. `jm status`/`apply`/
    `remove` are nesting-aware (removal prunes now-empty parent packages).

## [0.19.0] — 2026-06-10

### Added

- **`just-makeit --version` / `-V`** — the universal idiom now works as an alias
    for the `version` command when given as the first argument (previously only
    the bare `version` subcommand printed the version).

### Changed

- **Windows CMake boilerplate is opt-in per project** (gh-213) — jm emitted the
    MinGW runtime-DLL `if(WIN32 …)` block into every component / module
    `CMakeLists.txt`, untested boilerplate the drift gate froze in place. It is
    now gated on `[project] platforms` (default `["linux", "macos"]`): off by
    default, opted in with `jm new --windows`, and `jm status --check` treats
    its absence as correct. An existing project drops the per-component blocks
    on its next `jm apply` once `windows` is not in `platforms` — which unblocks
    projects (like doppler) that deliberately dropped Windows. The single
    configure-time `libwinpthread` copy in the top `CMakeLists.txt` stays (a
    harmless no-op off Windows).

### Removed

- **Windows CI and tooling** — jm no longer tests Windows. The MSVC path was
    never exercised (jm emits CPython for MinGW/GCC) and the MinGW CI leg was
    flaky infra more than signal. Dropped the `windows-latest` legs from the CI
    / release / install-deps matrices, the `jm-examples-windows` Docker image
    and its `windows` job, and `install-deps.ps1` / the `_run_ps1` path.

## [0.18.1] — 2026-06-10

### Added

- **`--streamable` objects get a generated `stream()` / `__iter__`** (gh-201) —
    a blockwise object (`execute(block) -> array`) or source
    (`steps(n) -> array`) grows `obj.stream(block, *, count=None, on_block=None)`
    and
    `__iter__`, so callers write `for blk in obj.stream(4096): ...` instead of
    the hand-rolled drain loop. It is pure cross-cutting glue: a C iterator type
    drives the object's `variable_output` method (else built-in `steps`) block
    by block, stopping on a drained (empty) block or when `count` is reached.
    `on_block(block)` fires **after** each block is consumed — the seam a
    downstream wraps for pacing/back-pressure. `--stream-block N` sets the
    default block used by `__iter__`. Non-streamable objects are unchanged.
- **`--streamable` now works for module objects too** (gh-203) — an object
    inside a shared-`.so` module (`jm object --module <mod> --streamable`) gets
    the same `stream()` / `__iter__` as a standalone, rendered into its
    per-object section, with the iterator type readied per object in the
    module's `PyInit_`. The shared `<module>.pyi` grows the stub under the
    streamable class; non-streamable siblings are byte-identical.
- **`--async-stream` adds `async for` to a streamable object** (gh-206) — opt-in
    on top of `--streamable`, the generated iterator also implements
    `__aiter__` / `__anext__` (and the object becomes async-iterable), so
    `async for blk in obj.stream(4096): ...` and `async for blk in obj: ...`
    work alongside the sync forms. `__anext__` offloads the producer step to
    the running loop's default executor, so a `nogil` producer lets the event
    loop run while the kernel works; on drain it raises `StopAsyncIteration`.
    All in C — no Python wrapper class. Plain `--streamable` objects are
    byte-identical (no async glue unless asked).

### Fixed

- **`--variable-output` method with an array (`T[]`) return type now compiles.**
    `--return-type "float _Complex[]" --variable-output` rendered the invalid
    `float complex[] *out` into `_core.h` / `_core.c` / `_ext.c` (and an
    `NPY_FLOAT` dtype fallback). The output buffer holds *elements*, so the
    `[]` is now stripped to the element type everywhere the buffer field,
    `*out` param, `sizeof()`, and NumPy enum render.

## [0.18.0] — 2026-06-08

### Added

- **`@code` blocks become runnable `Examples` doctests** — a `@code … @endcode`
    block in a header's Doxygen is rendered into the generated `.pyi` as a
    numpy-style `Examples` section the doctest gate then runs.

### Changed

- **Multi-line Doxygen prose renders as flowing paragraphs** (no per-source-line
    double-spacing); grouped + wrapped.
- **Built-in `reset` / `step` / `steps` derive their docstring from the header
    `@brief`** when written, matching extra-methods/properties; jm's own scaffold
    `@brief`s are filtered so fresh-scaffold ↔ manifest-rebuild stays idempotent.

## [0.17.1] — 2026-06-07

### Fixed

- **Class-docstring construction example honours the init_params ctor** (the #69
    contract). For an object that declares **both** `init_params` and scalar
    `--state` vars (state is then hidden/bridged), the generated `.pyi` "Create
    with defaults" example was built from the **state** vars — wrong arity, and
    wrong types for a `string_enum:` param (e.g. `Synth(0, 8, 0, 1.0, 0.0)` →
    `TypeError`, failing a doctest gate). It now constructs from the init_params
    as **keyword arguments** (`Synth(type="tone", fs=1000000.0, …)`,
    string_enum defaults quoted), which is order-independent against the binding's
    parse order, and the **Parameters** section documents the init_params. The
    "reset restores defaults" demo is **skipped** for init_params ctors, whose
    custom `create_impl` may derive state from a param and keep it across `reset()`
    (e.g. a waveform `type`). Regression test in `test_stubs.py`.

### Docs

- `docs/commands/app.md`: document the cf32 output axes — `--sample_type`
    (0.16.0) and `--file_type` / `--endian` / `--record` (0.17.0).

## [0.17.0] — 2026-06-06

### Added

- **`jm app` output axes: `--file-type`, `--endian`, `--record`** (gh-193). A
    cf32 generator/blockwise app — the stream that already gets `--sample_type` —
    now also generates, byte-identically across all three faces
    (c / console / pep723):

    - `--file-type raw|csv` — raw interleaved I/Q (today's default) or a text
        `I,Q` line per sample (`%0.9f` for cf32, `%0.17g` for cf64, `%d` for the
        integer types);
    - `--endian le|be` — big-endian reverses each element's bytes (raw only; csv
        is text, endian-agnostic);
    - `--record FILE` — a JSON record of the fully-resolved run (every flag after
        defaulting, choice flags rendered as their chosen string) so a capture is
        reproducible from its sidecar.

    The C side adds `jm_write_block` / `jm_elem_size` beside `jm_convert_block`,
    plus per-choice `jm_choices_*` name tables for the record; the Python faces
    pack via numpy (`.byteswap()` for big-endian) and `json.dump`. Richer
    containers (BLUE, SigMF, zmq) stay application-side — they need sample-rate /
    segment / transport context that a generic generator can't know. Tested in
    `test_app_gen.py` (`test_output_axes_c` / `test_output_axes_python`, plus the
    updated `--sample_type` assertions).

## [0.16.4] — 2026-06-06

### Fixed

- **`_dump` is idempotent for heredoc bodies** (gh-192). `impl`/`create_impl`/
    `reset_impl`/`destroy_impl`/`init_post_parse`/multi-line `doc` were emitted
    as `key = """\n{body}\n"""`; TOML load keeps the trailing newline, so each
    `C.save`/re-dump grew the body by a blank line. With `[app]` now persisted
    via `C.save` (0.16.3), every app op re-dumped fragments and the generated
    step/impl gained a blank line per reconcile → a freshly-applied project read
    as perpetually "stale". Bodies are now `strip("\n")`-normalised before
    emission, so load→dump is stable. Regression test in
    `test_dump_impl_roundtrip.py`.

## [0.16.3] — 2026-06-06

### Fixed

- **`[app]` persists in the manifest for split-layout projects** (gh-190).
    `save()` treated `app` like a component, so in a split-layout project
    (`include = ["objects/*.toml", …]`) it routed `[app]` to `objects/app.toml`
    and `jm apply` couldn't re-materialise the faces (the CMake app block was
    dropped on the next reconcile). `save()` now keeps `app` in the manifest
    (like `project`), and `_dump` emits the owning `module` for an object app so
    the console/pep723 scoping (gh-187) round-trips. `_dump`/`_write_doc`
    already emitted `[app]`; this completes flat + split parity.

## [0.16.2] — 2026-06-06

### Fixed

- **The pep723 face imports a module object from its subpackage** (gh-187,
    follow-up to 0.16.1). `app_pep723.py` hard-coded
    `from <pkg> import <Component>`, but a module object's class lives at
    `<pkg>.<module>`. A new `import_pkg` context key (`<pkg>.<module>` for module
    objects, `<pkg>` otherwise) drives the import; `package` still names the pip
    distribution for the dependency line. All three faces (c / console / pep723)
    of a module-object app now produce byte-identical output.

## [0.16.1] — 2026-06-06

### Fixed

- **Object apps now link their `depends_on` cores + libm** (gh-187). `jm app`
    emitted `target_link_libraries(<app> PRIVATE <obj>_core)`, but an OBJECT
    library doesn't propagate its PUBLIC link deps' objects to a consuming
    executable — so an app over an object with `depends_on` (or that uses
    `math.h`) failed to link (`undefined reference to lo_create / log10`). The
    app link line now names the object's own core, each `depends_on` core, and a
    Windows-safe conditional `m`.
- **Module-object/function console faces are scoped to their subpackage**
    (gh-187). The console target wrote `src/<pkg>/cli.py` + `<pkg>.cli:main`,
    which collides when the package already has a `cli` submodule. A module app
    now writes `src/<pkg>/<module>/cli.py` + `<pkg>.<module>.cli:main` (the
    `[app]` record carries the module so it round-trips through `jm apply`).

## [0.16.0] — 2026-06-06

### Added

- **`jm app` generates real CLI tools** (gh-184). Constructor flags are now
    derived from `init_params` (the `no_state`/awgn/ddc pattern), mirroring the
    #69 core contract, instead of only `--state` ctor vars — so a generator like
    `synth_create(type, fs, freq, snr, seed)` gets a flag per arg and the app
    calls `create()` correctly. A cf32 generator/blockwise app gains a built-in
    `--sample_type cf32|cf64|ci32|ci16|ci8` choice flag that converts each block
    to the chosen interleaved-I/Q wire type on write (byte-identical across the
    C / console / pep723 faces). Adds a generic choice-flag mechanism
    (`jm_parse_<name>` in C, `argparse(choices=…)` in Python) and a real
    `--help`/`-h` on the C face. The `[app]` record now round-trips through
    `jm apply` (no longer reset to `<project>`/first-object).
- **jm-version skew warning** (gh-183). `[project].jm_version` records the
    generating jm version (stamped by `jm new`, and monotonically by `jm apply` —
    an older CLI never downgrades the record). Every mutating command and
    `jm status` warn when the running CLI differs, turning the silent
    "stale CLI emits old-format glue" footgun into an immediate, actionable
    message.

## [0.15.9] — 2026-06-06

### Fixed

- **A `no_step` object's generated bench now declares `obj`** via its void
    create (gh-181). The bench template always emits `<comp>_destroy(obj)`, but
    for a `no_step` object with no init params the create was left as a TODO
    comment → `obj` undeclared → the bench failed to compile. Since
    `<comp>_create()` (a void create) is callable, the bench now emits it. The
    `kitchen_sink` example drops its workaround. Surfaced by that example.

## [0.15.8] — 2026-06-06

### Fixed

- **A `--batch` method now generates the 1:1-rate block C signature** —
    `void <comp>_<name>(<comp>_state_t *state, const <in> *in, size_t n,   <out> *out)` (or `(state, size_t n, <out> *out)` for a void `arg_type`).
    `_build_method_prototype` / `_methods_c_stub_fixed` had no batch handling, so
    the stub fell through to the scalar `(state, T x)` shape while the generated
    binding called the 4-arg form → `too many arguments to function` compile
    error. Both the prototype and the stub now emit the batch shape (gh-179).
    Surfaced by the `kitchen_sink` example.

## [0.15.7] — 2026-06-06

### Fixed

- **`C.save` / `_dump` now round-trips custom C bodies** — `impl`,
    `create_impl`, `reset_impl`, `destroy_impl`. `_dump` emitted none of them, so
    re-saving a manifest (e.g. setting a `[project]` key via `C.save` after
    writing a fragment) **silently dropped hand-written C bodies**, leaving a
    bare `calloc` constructor (→ NULL opaque fields → crashes). The bodies are
    now written as heredocs *before* any `[[component.*]]` sub-table, so TOML
    re-parses them onto the component. Surfaced by the `kitchen_sink` example.

## [0.15.6] — 2026-06-06

### Fixed

- **`depends_on` now links the dependency's OBJECT lib into the dependent
    object's own test/bench executables**, not just the aggregating Python
    extension. The dependent's `_core.c` calls the dependency's functions (e.g.
    a sibling's `create()` via `create_impl`), so without the dep lib its
    generated `ctest`/bench targets failed to link. The dep cores are now folded
    into the object-core `target_link_libraries` (PUBLIC + test/bench), in both
    fresh `jm object` generation and the `jm apply` surgical path (gh-174
    follow-up). Surfaced by the `kitchen_sink` integration example.

### Docs

- Corrected `porting-guide.md` / `declarative-scaffolding.md`: `depends_on` is
    set in TOML; there is no `--depends-on` CLI flag (the examples claimed one).

## [0.15.5] — 2026-06-06

### Fixed

- **`jm apply` now propagates a module object's component-level
    `extra_link_libs` / `extra_include_dirs` to its OBJECT-core CMakeLists
    (gh-174).** A non-collocated module object's `native/src/<obj>/CMakeLists.txt`
    is glue `apply` never re-rendered, so component-level
    `[<obj>] extra_link_libs` declared after creation left the
    `<<extra_link_on_object_core>>` slot empty (only `jm object` filled it).
    `apply` now *surgically* injects `target_link_libraries(<obj>_core PUBLIC   …)` and an extra `target_include_directories(<obj>_core PUBLIC …)` — adding
    only the component wiring, leaving the module-level test/bench link block
    untouched (idempotent; a no-op when there are no component-level settings).

## [0.15.4] — 2026-06-06

### Fixed

- **`depends_on` auto-include (0.15.3) no longer injects a broken `#include`
    for a bare link-target dependency.** A `depends_on` entry can name a
    component (`lfsr` → `lfsr/lfsr_core.h`, which exists) **or** a bare
    OBJECT-library link target (`lo_core` → `lo_core/lo_core_core.h`, which does
    not). 0.15.3 emitted the include unconditionally, so a project that lists
    link targets in `depends_on` (e.g. doppler's `ddc`) got an `#include` to a
    nonexistent header and failed to compile. The include is now emitted only
    when the dependency's header actually exists (`_dep_header_includes`), in
    both the fresh-generation and `jm apply` paths. Regression tests added to
    `tests/test_depends_on_includes.py`.

## [0.15.3] — 2026-06-06

### Added

- **`depends_on` auto-includes the dependency's header (gh-170).** A component
    that declares `depends_on = ["lfsr"]` is already linked against
    `lfsr_core`; now its `<comp>_core.h` also gains `#include   "lfsr/lfsr_core.h"`, so an opaque field of the dependency's type (e.g.
    `lfsr_state_t *`) compiles without a manual edit — "if jm links it, it
    includes it". The include is generated for fresh objects (standalone and
    module) and injected idempotently into existing headers on `jm apply`,
    placed among the other `#include`s ahead of the state struct.

### Fixed

- **`mutable` is honored as a synonym for `out` on a module-function array
    param.** `[[module.X.functions.params]]` with `mutable = true` now emits a
    writable `T *name` (not `const T *name`), matching how the user marks a
    buffer the function writes through. Threaded through the `jm apply` replay
    and canonicalised to `out` on a manifest re-dump. New
    `tests/test_depends_on_includes.py`.

### Added

- **`nogil` method flag — generate a GIL-released kernel call.** A
    `variable_output` execute method declared with `nogil = true` (or
    `jm method … --nogil`) generates its binding with the pure-C kernel wrapped
    in `Py_BEGIN_ALLOW_THREADS` / `Py_END_ALLOW_THREADS`, so a thread-per-shard
    worker (one object + output buffer per thread) scales across cores instead
    of serialising on the GIL. The numpy buffer accessors
    (`PyArray_DATA`/`PyArray_SIZE`) are **hoisted into locals before** the
    block — no Python C-API runs while the GIL is dropped — and the buffer
    realloc / error path stays above it, under the GIL.

    Opt-in by design: releasing the GIL is sound only when the object is not
    shared across threads concurrently (one object per stream), which jm can't
    verify. Replaces hand-patching `Py_BEGIN_ALLOW_THREADS` into the generated
    `_ext` binding — the GIL release is now declarative and regenerable. v1
    covers the `variable_output` execute shapes (single and multi-output);
    object-level `step`/`steps` is a follow-up. New
    `tests/test_method_nogil.py`.

### Fixed

- **`jm apply` no longer fights a hand-tuned function prototype (gh-169).** A
    module-function decl the user decorated with `JM_RESTRICT` (perf) or where a
    `const` was dropped on a mutable buffer param was treated as a *different*
    declaration than the one apply generates, so apply replaced it (clobbering
    the qualifiers) or — in some header layouts — appended a second,
    conflicting prototype that failed to compile. `_inject_decls_into_core_h`
    now compares prototypes modulo those decorative qualifiers
    (`_normalize_decl`), so an already-present decl is left untouched
    (idempotent) while a genuine signature change still replaces as before.
    Regression: `tests/test_apply_decl_qualifiers.py`.

### Added

- **`[module.X] reexports` — fold a sibling's symbols into a module's
    `__init__.py`.** A module subpackage's generated `__init__.py` re-exports
    its own C-extension types/functions; `reexports` additionally pulls names
    from a *sibling* extension in the same package — typically a `no_generate`
    module whose binding and `.pyi` are hand-written (e.g. a PyCapsule
    functional API) — into both the import block and `__all__`. Declared as an
    inline table mapping submodule → names:

    ```toml
    [module.ddc]
    objects = ["ddc", "ddcr"]
    reexports = { ddc_fn = ["ddcr_create", "ddcr_execute", "ddcr_destroy"] }
    ```

    This keeps the glue **hands-off**: the re-exports regenerate from the
    manifest on every `jm apply` instead of being a hand-edit that apply would
    clobber. Imports and `__all__` are single-line canonical — matching jm's
    existing `__init__.py` glue — so adding the key never reflows a project's
    other modules. Both generation paths (`jm object`/`method` and `jm apply`'s
    merge) and both manifest writers (tomlkit and the `_dump` fallback) are
    covered. New `tests/test_module_reexports.py`.

### Fixed

- **`jm apply` leaked the `<<extra_link_on_object_core>>` placeholder** for a
    collocated object (object name == module name) with module
    `extra_link_libs`. The gh-160 OBJECT-core PUBLIC-link slot was filled on the
    `jm object` path but not when `apply` rebuilds the collocated CMakeLists, so
    the literal template token reached the generated file and broke the CMake
    build. `apply` now resolves it (regression: `tests/test_apply_collocated_   cmake.py`).

## [0.15.0] — 2026-06-05

### Added

- **`jm app` generates working CLI faces** — the "one C core, three faces"
    pattern. For a scalar `step(x)->y` object, all three targets (`c`, `console`,
    `pep723`) emit a real argument parser **and** a read→`step()`→write I/O loop
    — no hand-editing. The C `strtof`/argv parser and the Python `argparse` are
    generated from the same object model. Ctor state vars become `--flags` wired
    into `create()`; extra flags via `--flag name:type[:default[:help]]` persist
    as `[[app.flags]]` and appear in both parsers.
- **Full object-shape coverage** for `jm app`: blockwise (`T[]->U[]`, block-
    streaming `steps`), consumer (`T->void`, no output side), and generator
    (`void->T`, synthetic `--count`, no input side), in addition to scalar.
- **`jm app --function <name> [--module m]`** — generate a CLI over a
    module-level function: flags map to its scalar params, call it once, print
    the result.
- **`jm app --command name[:help]`** / manifest `[[app.commands]]` — multi-
    command CLIs: a C `argv[1]` dispatch with per-command flag-parsing handlers
    and a Python `argparse` subparsers tree (command bodies are stubs).
- New bundled examples: **`three_face`** (one core → C binary + Python CLI +
    module) and **`app_shapes`** (blockwise/consumer/generator/function/
    subcommand apps, built and run end-to-end).

### Fixed

- Cross-module `extra_link_libs` now propagate to the object's OBJECT library
    (PUBLIC) and the aggregating Python extension, not just test/bench targets —
    fixes `ImportError: undefined symbol` for module objects depending on another
    module's core (gh-160).
- Functions-only modules no longer emit a trailing space in the generated
    module `CMakeLists.txt` (cmake-lint C0303).

## [0.14.12] — 2026-06-05

### Added

- **Support for Python 3.9 and 3.10** — the minimum supported version is now
    **3.9**, down from 3.11. `pyproject.toml` and project config are parsed with
    stdlib `tomllib` on 3.11+ and the `tomli` backport on 3.9/3.10 (added as a
    conditional dependency `tomli; python_version < "3.11"`). The floor is 3.9
    rather than 3.8 because the required `tomlkit` dependency needs ≥3.9.

### Changed

- Modules now use `from __future__ import annotations` so `str | None` /
    `list[str]`-style annotations don't evaluate at runtime on 3.9/3.10.
- Scaffolding templates emit projects targeting `>=3.9` (generated
    `pyproject.toml`, README, and CI matrix), so `jm new` / `jm example` output
    runs across the supported range.
- CI, release, and artifact matrices now test Python 3.9–3.14. Dev-only docs
    tooling (`zensical`, `mkdocstrings-python`) is gated to ≥3.10.

## [0.14.11] — 2026-06-04

### Added

- **`jm apply` refreshes the runtime `__doc__` of module-object binding
    fragments — the safe successor to the reverted 0.14.8/0.14.9.** Header
    Doxygen edits already reach the `.pyi` (0.14.6); now they also reach the C
    runtime docs (`help(Obj.method)`, `Obj.__doc__`) that live in the sacred
    `<mod>_ext_<obj>.c` fragments. A new post-sync pass (`_docsync`) renders a
    *reference* fragment in memory and transplants **only the docstring
    string-literals** — the `PyMethodDef` doc slot, the `PyGetSetDef` doc
    field, and `.tp_doc` — matched by Python entry name.

    Unlike 0.14.8/0.14.9, which re-rendered the whole fragment from the
    manifest and silently dropped hand-written bindings the manifest can't
    express, this transplant touches *only* doc slots whose entry name also
    appears in the reference. Every function body and every non-manifest
    binding (custom getters/setters, list-returning accessors, bespoke
    constructors) is left byte-for-byte identical — preservation is a
    structural guarantee, not a body-splice. Idempotent: a second `apply`
    produces no diff. `*_extra.c` and `no_generate` modules are never touched.

    New `tests/test_apply_fragment_docs.py` covers runtime-doc refresh, the
    non-manifest-binding survival regression, body preservation, idempotence,
    and unit tests for the `_docsync` splicer.

## [0.14.10] — 2026-06-04

### Fixed

- **Reverted the 0.14.8/0.14.9 `apply` fragment-doc refresh — it could drop
    hand-written bindings.** Those releases re-rendered per-object
    `<mod>_ext_<obj>.c` fragments on every `apply` to push derived docstrings
    into the runtime bindings. But fragments legitimately hold hand-written
    bindings that aren't in the manifest (custom property getters/setters,
    list-returning accessors, bespoke constructors); re-rendering from the
    manifest **silently dropped** them, breaking the build/behavior. `apply`
    again leaves per-object fragments untouched (the pre-0.14.8 contract).

    The header-derived docstrings still populate the `.pyi` stubs (0.14.6) — the
    surface IDEs, type-checkers, and `--doctest-glob` consume. Refreshing the
    **runtime** `__doc__` for module objects needs a future refresh that
    preserves non-manifest hand-written bindings; **0.14.8 and 0.14.9 are
    superseded — upgrade to 0.14.10.**

## [0.14.9] — 2026-06-04

### Fixed

- **The 0.14.8 fragment doc-refresh now preserves hand-written constructors.**
    0.14.8's `apply` doc-refresh re-rendered module fragments via the usual body
    preservation, which deliberately regenerates `_init`/`_dealloc` from template
    — clobbering objects whose constructor was hand-written into the fragment
    (mismatched `_create` argument counts → broken build). A doc-only refresh is
    not a structural change, so it now restores `_init`/`_dealloc` bodies too
    (`_restore_c_function_bodies(..., include_infra=True)`); only the generated
    `PyMethodDef`/`tp_doc`/`PyGetSetDef` docstrings change. Buffer-structure
    changes still arrive via `jm method`/`jm regenerate`, which regenerate
    `_init`/`_dealloc` as before. (0.14.8 is otherwise superseded; upgrade.)

## [0.14.8] — 2026-06-04

### Fixed

- **`jm apply` now refreshes module-object binding docstrings.** Since 0.14.6,
    derived Doxygen docstrings reached a module's `.pyi` but not its per-object
    `<mod>_ext_<obj>.c` binding fragments (`_sync_aggregates` reconciles the
    aggregator/`.pyi`/CMake, not the fragments), so `help(Obj.method)` /
    `__doc__` / `tp_doc` / property docs showed the stale fallback while the stub
    was correct. `apply` now re-renders each module's fragments on the real tree
    after wiring reconciliation, refreshing `PyMethodDef` / `tp_doc` /
    `PyGetSetDef` docs while **preserving hand-written wrapper bodies** (and never
    touching `*_extra.c`). Idempotent: a re-apply on an up-to-date tree is a
    no-op.

    Not preserved across regeneration (unchanged contract — put such code in
    `*_extra.c` or `_core.c`): edits inside `*_init`/`*_dealloc`, and bespoke
    helper functions not implied by the manifest.

    Standalone-object bindings are unaffected (already synced via `<comp>_ext.c`);
    deriving docstrings into a *standalone* binding remains a separate gap.

## [0.14.7] — 2026-06-03

### Fixed

- **Class docstring no longer emits an unrunnable construction example.** When
    a constructor argument has no scalar literal (an array/no-default param,
    rendered `...`), the synthesized `Examples` block used to emit
    `>>> obj = X(...)`, which raises `TypeError` under `doctest`. jm now omits
    the `Examples` block for such objects instead of shipping a broken example,
    so generated `.pyi` doctests run clean. Scalar-constructible objects keep
    their runnable example.

## [0.14.6] — 2026-06-03

### Added

- **Python docstrings derived from `_core.h` Doxygen.** jm now reads the
    hand-written Doxygen (`@brief`, `@param`, `@return`) in the sacred
    `<obj>_core.h` and synthesizes numpy-style docstrings for methods,
    properties, and the class — in both the generated `.pyi` stubs and the C
    bindings (`PyMethodDef`, `PyGetSetDef` doc, `tp_doc`). The header is the
    single source of truth; C-only params (`state`, `x_len`, `out`, `max_out`)
    are dropped from the Python `Parameters`. Falls back to the prior
    name-based stub when no Doxygen is present, and jm's own scaffold templates
    (`Create a <obj> instance.`, `Get current <field>.`, …) are treated as
    boilerplate (not derived) so a manifest-only rebuild stays idempotent.
- **`doc` key / `--doc` flag** on `jm method` and `jm property` (mirrors the
    existing `jm function --doc`). Precedence: TOML `doc` > header `@brief` >
    name fallback. Params/returns still come from the header; synthesized
    doctest examples are preserved.
- **`_docstring.extract_doctests()`** + a doctest well-formedness gate in the
    test suite: every synthesized doctest must parse and construct its object.

## [0.14.5] — 2026-06-03

### Fixed

- **`jm bench --check` matched benchmarks by name, colliding duplicates**
    (gh-141 follow-up). `_compare_reports` keyed the baseline/current maps by the
    bare pytest-benchmark `name`, which repeats across modules (e.g. several
    `test_bench_execute_64k` in different `bench_*.py`). The baseline kept only
    one entry, so unrelated benchmarks were cross-compared — a 1 µs bench against
    a 300 µs baseline reported a bogus +34000% regression. Now keyed by the
    unique `fullname` (`file.py::test[param]`), falling back to `name` for the C
    side (whose `comp::method` names are already unique). Each result carries an
    unambiguous `id`.
- **`jm bench --check` compared `mean` (noisy).** Switched the regression
    metric to `stats.min` — pytest-benchmark's stable best-case statistic — so a
    scheduler-jitter spike in `mean` no longer reads as a regression. Falls back
    to `mean` when `min` is absent.

## [0.14.4] — 2026-06-03

### Added

- **`pass_capacity` for `variable_output` methods** (gh-138). A
    `variable_output` method lowers to the 4-arg C form `size_t fn(state, in,   n_in, out)`. Some C APIs defensively take a trailing `size_t max_out`
    output capacity (e.g. to forward it to a downstream resampler). Setting
    `pass_capacity = true` on a `[[obj.methods]]` entry (CLI `--pass-capacity`)
    emits the 5-arg form consistently across the `_core.h` prototype, the
    `_core.c` stub, and the ext-binding call (which passes the buffer-capacity
    field jm already maintains for grow-on-demand).

- **`jm status` CI drift gate — `--allow` / `--json` / `--diff` / `--check`**
    (gh-140). `status` already builds a throwaway `apply` and diffs it against
    the tree; these options surface that result. `--allow PATH` (repeatable) and
    `[project] status_allow` mark known-accepted deviations (exact path or
    fnmatch glob) that are reported but not counted; `--json` emits a structured
    report; `--diff` prints a unified diff per stale file; `--check` prints a
    one-line summary. The exit code now counts only non-allowed drift.

- **`jm bench --check` perf-regression gate** (gh-141). Compares the current
    run against a baseline snapshot and exits non-zero on regression beyond
    `--threshold` (default 10%). `--baseline TAG` selects the baseline (default:
    most recent committed snapshot); `--allow NAME` exempts a benchmark; `--json`
    emits the comparison. Benchmarks whose baseline mean is below a 500 ns noise
    floor are reported but never fail; a missing baseline is reported and skipped.

### Fixed

- **`jm apply` re-injected a conflicting prototype for a multi-line
    declaration** (gh-137; the unresolved `jm apply` half of gh-118/gh-120).
    `_inject_decls_into_core_h` matched only single-line prototypes when
    deciding whether to replace an existing declaration, so a declaration
    wrapped across lines (e.g. a 5-arg `variable_output *_execute(..., out,   max_out)`) was missed and the generated decl appended as a second,
    conflicting declaration. A multi-line fallback now replaces it in place.
    The "preserve existing decl" skip-set is also recognised as an *interactive*
    safety net only: during `jm apply` replay the manifest is authoritative
    (`from_apply`), so a redefinition is no longer skipped.

- **Array `--arg-type "T[]"` rendered malformed C** (gh-139). A
    `variable_output` / `batch` method given an array `--arg-type` lowered its
    block input using the full array display, emitting the invalid
    `const float complex[] *in` in the prototype, the ext cast, and the bench
    buffer. The block input now uses the array's element type.

## [0.14.3] — 2026-06-02

### Fixed

- **`jm apply` — `float64[M]` leaked into generated `_core.h`** (gh-128).
    `fn_c_decl` and `fn_c_stub` now call `parse_out_type()` to resolve numpy
    dtype+size annotations to their underlying C type before emitting the
    declaration (`float64[M] *out` → `double *out`).

- **`_core_core` double suffix in root `CMakeLists.txt`** (gh-130). The module
    object path was missing the `_core`-suffix strip that the standalone path
    already applied; `depends_on` entries ending in `_core` now get
    `$<TARGET_OBJECTS:foo_core>`, not `$<TARGET_OBJECTS:foo_core_core>`.

- **Duplicate `reset()` stub in `.pyi`** when user declares
    `[[methods]] name = "reset"` (gh-131). Added `builtin_reset_pyi` context
    key: `make_state_ctx` supplies the default stub; `make_methods_ctx` blanks
    it when `user_has_reset`. The `component.pyi` template uses
    `<<builtin_reset_pyi>>`; `_stubs._obj_stub` skips the built-in lines when
    the extra-methods list already contains `reset`.

- **`extra_link_libs` stripped from test/bench `target_link_libraries` on
    `jm apply`** (gh-132). `CMakeLists_object_core.cmake` test/bench targets now
    include `<<extra_link_libs_block>>`; module object path sets the block in
    ctx; collocated objects receive the module-level block.

- **Non-inline `extern` declaration appended after `static inline` definition**
    (gh-133). `_inject_decls_into_core_h` now skips injecting a bare prototype
    when the header already contains a `static inline` / `static JM_FORCEINLINE`
    definition of the same function — preventing the C11 §6.7.4¶7 linkage
    conflict that caused multiple-definition linker errors.

### Docs

- New troubleshooting entry: *"Generated header has `const T *` on a parameter
    that my function writes into"* — explains the `out = true` TOML key and
    `--out-param` CLI flag (gh-129).

## [0.14.2] — 2026-06-02

### Added

- **`jm method --varargs`** — scaffolds an open-ended `(*args, **kwargs)`
    Python binding for a named method. The binding lives in a sacred
    `<comp>_<name>_core.c` file compiled directly into the Python DSO (not
    the pure-C OBJECT library), so it has full access to `<Python.h>` while
    the C core stays header-clean. `CMakeLists.txt` is patched automatically
    to include the new source. The `.pyi` stub gets `*args: Any, **kwargs:   Any) -> Any` and benchmarking is skipped for varargs methods. A new
    `varargs_method` end-to-end example walks through a `configure(**kwargs)`
    use case backed by `PyArg_ParseTupleAndKeywords`.

- **`jm app --argc-argv`** — generates a C `main()` that exposes `argc` /
    `argv` via an `if (argc > 1)` block with an `<<IMPLEMENT>>` placeholder,
    instead of the default `(void)argc; (void)argv;` suppression.

## [0.14.1] — 2026-05-31

### Added

- **Blockwise preset (`--preset blockwise`).** Scaffolds a void,
    array-in/array-out processor: `void comp_steps(state, const T *in, n,   U *out)` in C and `NDArray → NDArray` with an optional `out=` buffer in
    Python. Removes the three previous blockers (CLI rejection of
    `--return-type "T[]"`, `ValueError` in `_sample.py`, and exclusion from
    `_PRESETS`). Pass-through stub compiles and passes CTest on day one
    (gh-86).

- **`jm bind` Phase 3b — full shape coverage.** The parser now handles all
    working API shapes in addition to scalar-in/scalar-out processors:
    getter/setter pairs become Python properties; custom scalar methods become
    Python methods; functions declared alongside a `_max_out` sibling become
    variable-output methods; opaque forward-declared state and
    constructor-inferred `init_params` are parsed and reflected into the
    binding.

### Docs

- Seven new example pages: `opaque_counter`, `delay_line`,
    `declarative_scaffold`, `accumulator`, `jm_function`, `jm_app`,
    `nco_tone`; `examples/index.md` reorganised into four section tables.
- Roadmap trimmed to current reality; dead developer docs removed; `jm   bind`, `jm status`, `--fragments`/`--no-fragments`, and v0.14 upgrade
    notes documented accurately.

## [0.14.0] — 2026-05-31

### Changed

- **The mutating verbs no longer splice your sacred files — "your code is
    sacred", honestly.** The old verbs re-rendered `<obj>_core.c` /
    `<obj>_core.h` from templates and grafted your hand-written bodies back
    with a fragile brace/regex splicer that could silently drop user code.
    That splicer (`_preserve_core_bodies`) is retired. The contract is now
    enforced mechanically:

    - **Additive** verbs (`jm method`, `jm property` for a computed or
        field-backed property, `jm function`) inject a declaration into
        `_core.h` and append a stub to `_core.c` — existing bodies, the state
        struct, and the inline `step()` are never re-rendered.
    - **Structural** changes (`jm add`, `jm remove state`) author the manifest
        then rebuild the object via the `jm regenerate` path (delete + apply).
        This discards hand-written `_core.c` bodies, so keep your algorithm in
        the TOML `impl` / `create_impl` (or `git stash`) and it is re-asserted
        on the rebuild. `jm add` / `jm remove state` take `--force` to skip the
        rebuild confirmation.
    - `jm remove <method|property>` regenerates the glue and leaves the
        orphaned `_core.c` body / `_core.h` declaration for you with a
        "delete by hand" note (the `jm remove function` pattern).
    - `jm apply` injects any missing TOML-declared declaration into `_core.h`
        and keeps the struct + `step()` sacred; a state-field or signature
        change is structural and reaches the body via `jm regenerate`.

    The merged behaviour is the same lifecycle the docs already teach —
    **author → apply/regenerate → implement → test → iterate** — you just can
    no longer lose code to a mis-fired splice. This changes the on-disk
    side effects of `jm add` / `jm remove state` (they now rebuild), hence the
    minor version bump.

### Fixed

- **Collocated module + module function no longer fails to compile.** When a
    module shares its name with one of its objects (e.g. `jm module fft` +
    `jm object fft --module fft`) and also has a module-level function, the
    generated `<mod>_ext.c` and the object fragment both defined a
    `PyMethodDef Fft_methods[]` table — a duplicate symbol once the aggregator
    `#include`d the fragment. The module-level table is now named
    `<mod>_module_methods`, so it can never collide with an object's
    `<Component>_methods`.

## [0.13.24] — 2026-05-30

### Changed

- **Module-level functions now live in their own sacred `.c` file.**
    `jm function <name> --module <mod>` writes the stub to
    `native/src/<mod>/<name>.c` (each including `<mod>_core.h` and holding a
    single definition) instead of appending into the shared `<mod>_core.c`.
    The module's CMakeLists compiles every such file into the module's OBJECT
    library. `jm remove function` deletes that file and strips the orphaned
    declaration from `<mod>_core.h`. `--inline` functions are unchanged (they
    stay as a `static inline` in the header with no `.c` file). This is a
    clean break — projects scaffolded by older versions are not auto-migrated.
- **`jm new` now defaults to the per-component fragment layout.** A new
    project's manifest carries only `[project]` plus `include` globs; objects
    route to `objects/<name>.toml` and modules to `modules/<name>.toml`. Pass
    `--no-fragments` for the legacy single-manifest layout. `--fragments` is
    kept as a deprecated no-op. The merged config every consumer sees is
    identical between layouts, so this only changes on-disk file shape.

### Docs

- Documented `jm ci`, `jm migrate-to-fragments`, and `jm new --fragments` in
    the command reference, quick-reference, and declarative-scaffolding pages
    (they shipped earlier but predated the v0.14 docs overhaul).

## [0.13.23] — 2026-05-30

### Added

CLI parity for every common TOML field — Phase 2 of the
[implementation plan](docs/developers/implementation-plan.md). Eleven
new flags across `jm new`, `jm object`, `jm module`, `jm method`, and
`jm function`. Every flag round-trips through TOML; existing TOML
authors are unaffected.

- **`jm object` / `jm method`**

    - **`--init-param name:type[:default]`** (gh PR #74) — composes
        with `--state` so a user-facing constructor signature is
        distinct from internal state. The old gate that rejected the
        pair is gone; `init_params` drive the ctor, `state` stays
        internal (manage with `--impl create::...`).
    - **`--max-out N`** (gh PR #75) — sibling stub
        `<comp>_<verb>_max_out()`; composes with `--variable-output`
        for variable-rate output (event-emitter shape).
    - **`--extra-include-dirs DIR`** (per-component, repeatable;
        gh PR #78) — CMake include path mirroring the existing
        per-module flag.
    - **`--impl SLOT::file::funcname`** (gh PR #80) — `SLOT` is
        `create`, `reset`, or `destroy`; lifts the body into the
        corresponding lifecycle slot. The bare two-part form
        (`file::funcname`) still lifts the step body.

- **`jm method` / `jm function`**

    - **`--result-field name:T`** (repeatable; gh PR #79) — appends a
        scalar field to a returned record list. Mirrors TOML
        `result_fields = [{name, type}, ...]`.
    - **`--out-type T`** on `jm function` (gh PR #76) — return a fresh
        ndarray of `T`; size from the first array param's length, or
        the first integer scalar param when there's no array param.
        (Already shipped on `jm method`.)

- **`jm new`**

    - **`--find-package NAME`** (repeatable; gh PR #77) — CMake
        `find_package(NAME REQUIRED)`; persisted to `[project] find_packages`.
    - **`--pkg-module NAME`** (repeatable; gh PR #77) — pkg-config
        module via `pkg_check_modules`; persisted to `[project] pkg_modules`.
    - **`--c-dep DIR`** (repeatable; gh PR #77) — vendored C
        subdirectory under `native/src/DIR` (no Python wrapper);
        persisted to `[project] c_deps`.

- **`jm module`**

    - **`--extra-include-dirs DIR`** (repeatable; gh PR #78)
    - **`--extra-link-libs TARGET`** (repeatable; gh PR #78)
    - **`--extra-types NAME`** (repeatable; gh PR #78) — hand-written
        Python type registered in `PyInit_<mod>` alongside generated types.

### Docs

- **Complete CLI ↔ TOML mapping** — every TOML key the schema accepts
    is listed in [`docs/configuration.md`](docs/configuration.md) with
    its CLI flag, status, and notes. ~66 keys are reachable through the
    CLI; ~15 stay TOML-only by design (`opaque`, `no_ctor`, `roles`,
    `buf_field`/`expr`, `init_post_parse_impl`, `default_raw` /
    `real_type`, `no_generate`, `max_results` / `max_results_param`).
- **Template gallery reframed around generic data-flow shapes.**
    Preset names are now domain-agnostic — `processor`, `blockwise`,
    `generator`, `consumer`, `reader`, `function`. The previous names
    (`filter`, `block`, `source`, `sink`, `library`) leaned DSP-y;
    each new name describes *what the component does to data* without
    importing domain vocabulary. The `detector` preset is gone — the
    variable-output / event-emitter shape is a capability flag
    (`--variable-output --max-out N` with repeatable `--result-field`)
    on any output-producing preset, not its own preset. Each gallery
    page now leads with cross-domain examples; the worked algorithm in
    each page stays as one concrete instance (a filter on
    `processor.md`, an FFT on `blockwise.md`, etc.).

## [0.13.22] — 2026-05-29

### Added

- **`extra_include_dirs` config** (gh-66) — counterpart to `extra_link_libs`
    for `target_include_directories`. Declare in `[module.X]` or `[component]`
    sections of `just-makeit.toml`; CMake variables (`${DOPPLER_INCLUDE_DIR}`)
    are honoured. The OBJECT library carries the dirs as `PUBLIC` so the
    Python extension, CTest, and benchmark targets inherit them transitively.

### Fixed

- **Module function `impl` blocks now materialize into `<mod>_core.c`**
    (gh-68). Previously `jm apply` wrote the body to the throwaway temp tree
    but `_sync_missing` skipped the existing (empty) real `<mod>_core.c`, so
    function declarations and bodies were silently dropped. The fix splices
    module-level core sources via body preservation, so both the impl body
    and the header declaration land in the real project.
- **Methods with `out_type` and a scalar param size the output correctly**
    (gh-65). When no array param is present, the wrapper now uses the first
    integer scalar param as the buffer length instead of falling back to
    `0` (which produced empty `(0,)`-shaped arrays).
- **TOML boolean flags honoured everywhere** (gh-71). `no_step = true`,
    `no_state = true`, `mutable = true`, `perf = true`, etc. (the natural
    form for hand-authored fragments) are now treated identically to the
    canonical string form `no_step = "true"` jm writes. Previously the
    readers did `== "true"` which silently rejected the boolean form, so
    `no_step = true` still emitted `step()` and `steps()`.
- **Properties no longer duplicate state-field struct members** (gh-70). A
    `[[properties]]` entry with `field = true` and a name matching an
    existing state field now generates only the Python accessor — the
    struct member stays single. Previously the duplicate field tripped a
    compile error.
- **`init_params` honoured alongside `state`** (gh-69). When both are
    declared, the constructor signature (C and Python) is now driven by
    `init_params`; state fields remain in the struct with getters/setters
    but are no longer exposed as constructor parameters. Use `create_impl`
    to initialise the state from the user-facing parameters.
- **Array params can opt out of `const`** (gh-72). Module functions with
    output buffers now honour `out = true` on the param definition,
    generating `T *name` instead of `const T *name` in both the header
    declaration and the implementation stub. The CLI gains an
    `--out-param name:type[]` flag mirroring `--param`. Previously every
    array param was hard-coded `const`, so any function trying to write
    to an output buffer hit `assignment of read-only location`.

### Docs

- **Decision tree** (`docs/decision-tree.md`) — a flat "where do I start"
    lookup: Step 1 (no project? `jm new`), Step 2 (what are you adding?),
    three sub-decisions (object shape, method output, external deps), an
    "I want… → do…" table, and an explicit list of TOML-only features
    that the CLI can't reach.
- **`jm wizard` design sketch** (`docs/developers/wizard-design.md`) —
    proposal-only design note for an interactive companion to the decision
    tree that would emit a shell script + TOML fragment + impl snippets
    from a single guided session. Not implemented.

## [0.13.21] — 2026-05-29

### Added

- **`jm app` command** — scaffold a shippable standalone application from
    any existing component. Three targets via `--target`:

    - `c` — generates `native/src/app/<name>.c` with a `main()` that calls
        your component's `create`/`step`/`destroy` lifecycle, and appends an
        `add_executable` target to `CMakeLists.txt`. Build with
        `make && ./build/<name>`.
    - `console` — generates `src/<pkg>/cli.py` with argparse boilerplate
        where every constructor parameter becomes a `--flag`, and updates
        `[project.scripts]` in `pyproject.toml`. Install with `pip install -e .`
        and run with `<name> --help`.
    - `pep723` — generates `<name>.py` in the project root with an embedded
        `# /// script` dependency block. Distributable as a single file;
        runs anywhere with `uv run <name>.py --help` without a full install.

    All targets write a `[app]` section to `just-makeit.toml` so
    `jm apply` can regenerate the scaffold. `--object` and `--name` default
    to the first component and project name respectively. 25 new tests.

- **Three new bundled examples** covering previously undocumented commands:

    - `jm_app` — scaffolding verification for all three `jm app` targets.
    - `jm_function` — full end-to-end for module-level C functions (`jm   function`), including a `--inline` static-inline variant, cmake
        build, and Python smoke test.
    - `jm_remove` — file and TOML state verification across all five `jm   remove` surfaces: method, property, function, state field, and
        entire object.

    Run any of them with `jm example jm_app` etc.

- **`jm app` in CLI help** — `jm help` now lists the command with flags
    and usage examples.

- **`docs/commands/app.md`** — reference page for all three `jm app`
    targets with usage, generated-file tables, and the TOML `[app]` record.

- **Quick-reference table** — three `jm app` rows added to
    `docs/quick-reference.md`.

## [0.13.20] — 2026-05-28

### Fixed

- **TOML comment preservation** — `save()` now round-trips `[project]` and
    `[module.X]` sections through `tomlkit` so user-written comments (file-level,
    section-header, and inline key comments) survive every `jm add` / `jm method`
    / `jm property` mutation. Component sections (`[[comp.state]]` repeated tables)
    are still rebuilt from `_dump()` — comment preservation inside repeated-table
    arrays is impractical with tomlkit. Adds `tomlkit>=0.15` as a runtime
    dependency.

## [0.13.19] — 2026-05-28

### Added

- **`no_ctor = true` flag on `[[<comp>.state]]` entries** — exclude a state
    field from the C `create()` signature and the Python `__init__` kwlist while
    keeping it in the struct, getters/setters, and `reset()`. Fields marked
    `no_ctor` are silently initialised to their TOML `default` value inside
    `create_assignments` (or overridden by `create_impl` when present). Use
    this when `create_impl` manages a field internally and you don't want Python
    callers to supply it. TOML-only — no `--state` CLI syntax.

    ```toml
    [ring]
    arg_type    = "float"
    return_type = "float"
    mutable     = "true"
    create_impl = """
    obj->size = size;
    obj->buf  = calloc(size, sizeof(float));
    if (!obj->buf) { free(obj); return NULL; }
    obj->idx  = 0;
    obj->sum  = 0.0f;
    """
    destroy_impl = """
    free(state->buf);
    """

    [[ring.state]]
    name = "buf";  type = "float *"; opaque = true

    [[ring.state]]
    name = "size"; type = "size_t"; default = "16"   # ← Python kwarg

    [[ring.state]]
    name = "idx";  type = "size_t"; default = "0"; no_ctor = true  # hidden

    [[ring.state]]
    name = "sum";  type = "float";  default = "0.0f"; no_ctor = true  # hidden
    ```

    Python: `Ring(size=16)` — `idx` and `sum` are invisible to callers.
    C: `ring_create(size_t size)` — only `size` in the signature.

### Fixed

- **Module objects with `opaque` or `no_ctor` state fields** — `add_component`
    now preserves `opaque = true` and `no_ctor = true` flags when writing the
    in-memory cfg used by `_regenerate_module`. Previously, both flags were
    silently lost after `add_component` rewrote the state list, causing the
    module ext fragment (`*_ext_<comp>.c`) to be generated with wrong struct
    fields and create() signatures. Standalone objects were unaffected.
    Also fixes `_dump()` to emit `opaque = true` and `no_ctor = true` when
    writing state entries, and to not emit `default =` for opaque fields
    that have no default. (gh#57)

## [0.13.18] — 2026-05-27

### Added

- **`opaque = true` flag on `[[<comp>.state]]` entries** — declare pointer,
    handle, or any-C-type state fields directly in TOML without hand-editing
    `_core.h`. Opaque fields are emitted into the state struct verbatim with
    no auto-getter/setter, no constructor parameter, no kwarg, and no reset
    assignment — Python sees nothing of them. Lifecycle is the user's
    responsibility via `create_impl` (required — validator enforces) and
    `destroy_impl` (recommended; pairs with the 0.13.17 feature).

    ```toml
    [fft]
    no_state     = "true"
    create_impl  = """
    obj->scratch = fftwf_malloc(sizeof(float _Complex) * n);
    obj->plan    = fftwf_plan_dft_1d(n, obj->scratch, obj->scratch,
                                     FFTW_FORWARD, FFTW_ESTIMATE);
    """
    destroy_impl = """
    if (state->plan) fftwf_destroy_plan(state->plan);
    fftwf_free(state->scratch);
    """

    [[fft.state]]
    name   = "scratch"
    type   = "float _Complex *"
    opaque = true

    [[fft.state]]
    name   = "plan"
    type   = "fftwf_plan"
    opaque = true
    ```

    `jm apply` raises before any side effects when an opaque field is
    declared without `create_impl` / `create_impl_file`. Opaque fields are
    TOML-only — no `--state` CLI syntax.

- **`opaque_counter` and `delay_line` examples** — minimal and realistic
    end-to-end demos of opaque state fields, both built and tested in CI.

## [0.13.17] — 2026-05-27

### Added

- **`destroy_impl` TOML key** — splice a custom teardown body into
    `<comp>_destroy()` before the trailing `free(state)`. Closes the loop on
    the `create_impl` / `reset_impl` story shipped in 0.13.16 — objects that
    allocate auxiliary resources (heap buffers, file handles, child objects)
    in `create_impl` can now release them declaratively in the same TOML file.

    ```toml
    [buf]
    destroy_impl = """
    if (state->log) fclose(state->log);
    free(state->scratch);
    """
    ```

    `destroy_impl_file = "legacy.c::buf_destroy"` lifts an existing function
    body from a file. `destroy_impl` / `destroy_impl_file` are mutually
    exclusive. Same TOML ordering rule: keys must precede any
    `[[comp.state]]` arrays in the section.

## [0.13.16] — 2026-05-27

### Added

- **`create_impl` and `reset_impl` TOML keys** — add custom C bodies for
    `<comp>_create()` and `<comp>_reset()` directly in TOML, bypassing the
    generated field-assignment code. Use these when scaffolded assignments are
    insufficient: parameter validation, lookup tables, computed masks, or
    anything that can't be expressed as plain field copies.

    Place the keys in the `[comp]` section *before* any `[[comp.state]]` arrays
    (TOML requires this — keys must precede sub-table arrays in the same section):

    ```toml
    [lfsr]
    arg_type = "void"
    return_type = "uint8_t"
    create_impl = """
    if (initial_state == 0) return NULL;
    state->initial_state = initial_state;
    state->state = initial_state;
    """
    reset_impl = """
    state->state = state->initial_state;
    """

    [[lfsr.state]]
    name = "initial_state"
    type = "uint64_t"
    default = "0"
    ```

    `create_impl_file = "path/to/file.c::funcname"` and
    `reset_impl_file = "path/to/file.c::funcname"` variants are also supported
    for lifting bodies from existing C files (analogous to `impl_file` for
    step). Each pair is mutually exclusive.

    **Note:** inside `create_impl`, the freshly allocated struct pointer is
    named `obj` (not `state`). Use `obj->field = value;` to initialise fields.
    The parameter names come directly from state field names; `obj` avoids a
    collision when a field is also named `state`. (gh#51)

### Fixed

- **`<comp>_create()` local pointer renamed from `state` to `obj`** — the
    generated create function now uses `obj` for the freshly `calloc`'d struct
    pointer so that a state field named `state` no longer causes a C compiler
    redeclaration error (`uint64_t state` parameter vs.
    `lfsr_state_t *state` local). Generated `create_assignments` lines
    (`obj->field = value;`) are updated accordingly. (gh#51 follow-up)

- **Scalar setter parameter renamed from `<name>` to `val`** — the generated
    `<comp>_set_<name>()` functions now use `val` as the new-value parameter so
    that a field named `state` no longer causes a redeclaration error in the
    setter (`<comp>_state_t *state` vs `uint64_t state`). The generated
    getter/setter implementations are also excluded from `_preserve_core_bodies`
    preservation so future signature changes apply cleanly on regeneration.
    (gh#51 follow-up)

- **`variable_output` buffer grows automatically at call time** — the
    pre-allocated output buffer for `variable_output` methods previously used a
    fixed-at-construction size, causing a heap overflow when `<comp>_max_out()`
    returns 0 (placeholder) and the caller passes a scalar count `n` larger than
    the 1-element fallback. The wrapper now tracks `_<name>_buf_cap` alongside
    the buffer pointer and uses `realloc` to grow the allocation whenever the
    requested output count exceeds the current capacity. For
    `params`-driven methods with scalar-only arguments (e.g. `steps(uint32_t n)`),
    the fallback size is now `(size_t)n` instead of `1`, so the very first call
    allocates enough memory even when `max_out()` is not yet implemented.
    (gh#51 follow-up)

## [0.13.15] — 2026-05-27

### Fixed

- **`out_type` now respected for `variable_output` methods** — when a method
    declares `variable_output = true` and sets `out_type = "uint8_t"` (or any
    scalar type), the output buffer parameter, pre-allocated field, and
    `PyArray_SimpleNewFromData` call now all use `out_type` as the element type.
    Previously `out_type` was silently ignored and `return_type` (defaulting to
    `float _Complex`) was used instead, producing wrong C signatures and wrong
    NumPy dtypes. (gh#49)

- **`jm apply` method replay uses `void` as default `arg_type`** — when a
    `[[obj.methods]]` entry omits `arg_type`, the replay in `jm apply` now
    defaults to `"void"` (matching the CLI default) instead of `"float _Complex"`,
    so methods with only `params` no longer receive a spurious
    `const float complex *in, size_t n_in` input parameter. (gh#49)

## [0.13.14] — 2026-05-26

### Added

- **`py_return_type` key for method stubs** — add `py_return_type = "list[tuple[int, float]]"` to a `[[obj.methods]]` entry to override the
    auto-derived Python return annotation in the `.pyi` stub. Useful when a
    method returns a custom C struct whose Python representation cannot be
    inferred from the `return_type` field alone. (gh#26)

### Fixed

- **`jm remove` warns when `no_step` object's `_core.c` holds user code** —
    for `no_step = true` objects the algorithm lives in `_core.c`, not the
    header. `jm remove` now checks `_core.c` for the `/* <<IMPLEMENT: */`
    placeholder; its absence signals that the user has replaced the scaffold,
    and a stderr warning is emitted before the file is deleted. (gh#46)

- **`jm remove` warns when `_core.h` holds hand-written step()** — if the
    `/* TODO: implement */` stub in the generated `_core.h` has been replaced
    with real algorithm code, `jm remove` and `jm remove --force` now print a
    warning to stderr so the user knows they are about to permanently destroy
    their implementation. (gh#41)

- **`jm apply <fragment>` succeeds when fragment already under include glob**
    — running `jm apply` a second time on a fragment already present in the
    `objects/` directory (and covered by the `include` glob) no longer raises
    a "duplicate section" error; the conflict check is skipped for fragments
    that are already wired in. (gh#42)

- **`string_enum` parameter docstring uses `str` instead of `Any`** — the
    numpy-style docstring for `string_enum:…` init parameters now emits the
    correct type (`str`, not `Any`). (gh#26)

## [0.13.13] — 2026-05-25

### Added

- **`extra_types` TOML key — declarative `PyInit_` registration** — declare
    `extra_types = ["MyType"]` under `[module.X]` to automatically emit
    `PyType_Ready(&MyTypeType)` and `PyModule_AddObject(...)` calls in the
    generated `PyInit_<module>` function. Hand-written types in `*_extra.c`
    files no longer require manual patching after every `jm apply`. jm-owned
    types are registered first; extra types follow in declaration order. (gh#28)

### Changed

- **Coverage reporting via Codecov** — CI now runs `pytest --cov` on every
    push/PR and uploads results to Codecov (tokenless OIDC for public repos).
    Coverage badge added to README.

### Documentation

- **Branch workflow documented** — `docs/developers/START_HERE.md` now covers
    the `feat/`/`fix/`/`docs/`/`chore/` branch convention, PR requirements, and
    CI-green-before-merge rule.
- **Quick-reference additions** — three new rows in the Advanced table:
    scalar-sized output arrays (`out_type = "dtype[param]"`), extra link libs,
    and extra types.

## [0.13.12] — 2026-05-25

### Added

- **`extra_link_libs` for module CMakeLists** — declare
    `extra_link_libs = ["resamp_core", "m"]` under `[module.X]` in
    `just-makeit.toml` to inject additional `target_link_libraries` entries
    into the generated module CMakeLists. Useful when a module's C code
    depends on a pre-existing OBJECT or INTERFACE library not owned by
    just-makeit. `jm apply` propagates the setting correctly through
    re-materialisation. (gh#27)

- **`out_type = "dtype[param]"` scalar-sized output arrays** — `jm function`
    now accepts `out_type = "float64[M]"` (or any numpy dtype name), where
    `M` is the name of a scalar C parameter that provides the output array
    length at runtime. The generated binding allocates `npy_intp _dim = M`
    and passes `(double *)PyArray_DATA(...)` to the C function. The `.pyi`
    stub emits `NDArray[np.float64]` as the return type. (gh#29)

### Fixed

- **`jm apply` preserves `*_extra.c` files** — hand-written
    `<mod>_ext_extra.c` and `<mod>_ext_<obj>_extra.c` files are now seeded
    into the temporary replay directory before `_regenerate_module()` runs, so
    they appear in the regenerated aggregator `#include` list and survive
    `jm apply` without being dropped. (gh#28)

## [0.13.11] — 2026-05-25

### Fixed

- **`string_enum` init-params emit `Literal[...]`** instead of `Any` in
    `.pyi` stubs; `from typing import Literal` is added to the stub header
    automatically when needed. Enum default values are quoted strings in both
    the `__init__` signature and the numpy-style docstring. (gh#26)
- **`out_type` on module-level functions emits `NDArray[...]`** instead of
    `None` in `.pyi` stubs; numpy is also imported when a function uses
    `out_type` but no object uses arrays. (gh#26)
- **`result_fields` methods emit `list[tuple[t1, t2, ...]]`** with per-field
    Python type annotations derived from the C field types, instead of the
    untyped `list[tuple]`. (gh#26)
- **Array init-param docstrings emit `NDArray[...]`** instead of `Any` for
    both 1-D and 2-D array types. (gh#26)
- **2-D array init-params (`float[][]`) map to `NDArray[np.float32]`** in
    stubs instead of `Any`. (gh#26)

## [0.13.10] — 2026-05-25

### Added

- **Optional array init-param** — `[[comp.init_params]]` entries may now set
    `optional = true` (with `create_fn = "Alt_create"`) on any array type.
    When the caller supplies the kwarg, `create_fn` is called with the array
    dimensions, a const pointer to its data, and any scalar params; when
    omitted, `<comp>_create` is called with scalars only. 1-D arrays pass
    `(len, ptr, scalars…)`; 2-D arrays pass `(dim0, dim1, ptr, scalars…)` and
    include an `ndim == 2` guard. CLI spelling:
    `--init-param bank:float[][]:optional:Alt_create`. Covers the Resampler
    use-case where a polyphase bank may or may not be user-supplied. (gh#25)

## [0.13.9] — 2026-05-24

### Added

- **Per-object ext fragments** — each object in a module now generates its
    own `<module>_ext_<obj>.c` fragment file. The thin aggregator
    `<module>_ext.c` `#include`s all fragments and owns only the
    `PyModuleDef` and `PyInit_`. Adding a new object no longer rewrites
    sibling objects' hand-edited code. Migration from a monolithic ext is
    automatic on the next `jm` command. (gh#20)
- **`_extra.c` convention** — if `<module>_ext_extra.c` or
    `<module>_ext_<obj>_extra.c` exist on disk, the aggregator includes them
    automatically. jm never creates or modifies `*_extra.c` files, making them
    safe for hand-written Python types with no TOML representation. (gh#24)
- **`inline = true` on module-level functions** — `jm function foo --module m --inline` emits a `static inline` body stub directly into `<module>_core.h`
    instead of a forward declaration in the header plus definition in
    `_core.c`. Ideal for pure, stateless functions that should be inlined at
    every call site. (gh#23)
- **Dtype-dispatched constructors** — `real_type` / `real_create_fn` fields
    on `init_params` array entries emit a dtype probe at construction time:
    if the incoming array matches `real_type`, `real_create_fn` is called
    instead of the default `<comp>_create`. Covers the common DSP pattern of
    a filter that has both a real-tap and a complex-tap variant. (gh#22)

### Fixed

- **PascalCase object names** — `_to_title()` now preserves internal
    capitalisation (e.g. `my_NCO` → `MyNCO`). Previously `str.title()` lower-
    cased all characters after the first, mangling names like `NCO`. (gh#19)
- **Module `target_sources` for new objects** — adding an object to an
    existing module now inserts `target_sources(…)` independently of
    `add_subdirectory`, so the object is compiled even when the subdirectory
    entry already existed. (gh#21)
- **`static int` body preservation** — the ext-file body extractor now
    matches `static int` functions (init, traverse) in addition to
    `static PyObject *`, preventing hand-patched `tp_init` / `tp_traverse`
    implementations from being overwritten on module regeneration. (gh#20)

## [0.13.8] — 2026-05-24

### Added

- **`jb.toml` generation** — `jm new` now emits a `jb.toml` alongside
    `just-makeit.toml`, pre-populated with dev system dependencies for
    apt, pacman, brew, dnf, zypper, apk, and msys2. Run
    `jbx install-deps -g dev` immediately after scaffolding to install
    build deps without any manual configuration.

### Changed

- CI uses `jbx install-deps` (reads from `jb.toml`) for system
    dependencies on all platforms, replacing inline `apt-get`/`brew`
    blocks.
- Added `examples` CI job that verifies `jb.toml` generation and runs
    the full scaffold workflow end-to-end on Ubuntu and macOS.

### Fixed

- `make test-examples` failed with `ModuleNotFoundError: No module named 'just_makeit'` when run via `uv run --no-project`. Fixed with
    a dedicated `PYTEST_EXAMPLES` invocation that includes the local
    project environment.

## [0.13.7] — 2026-05-23

### Fixed

- `sliding_power` bundled example missing `__main__` block — `jm example sliding_power` exited immediately with no output instead of building and
    testing the project.

## [0.13.6] — 2026-05-22

### Fixed

- **`--no-state` PascalCase name collision (gh#9)** — all Python-layer C
    wrapper functions (`dealloc`, `new`, `init`, `destroy`, `enter`, `exit`,
    methods array, `TypeObject`) now use a `{Component}Obj_` prefix (e.g.
    `ResamplerObj_destroy`) instead of `{Component}_`. Previously, when the
    object name was PascalCase, the generated wrapper function names collided
    with identically-named functions declared in the user's included C header,
    causing a link error. The Python import API (`PyModule_AddObject`) still
    uses the undecorated name so the user-facing class name is unchanged.
- **`variable_output` `malloc(0)` heap corruption (gh#17)** — when
    `{comp}_{method}_max_out()` returns 0 at construction (e.g. a FIR filter
    whose output size is input-dependent), the output buffer is now left `NULL`
    instead of calling `malloc(0)`. The first Python call re-queries `max_out()`
    and falls back to the input length `n` if it still returns 0, then
    allocates. All subsequent calls take the pre-allocated zero-copy path.
- **`c_deps` CMake ordering (gh#16)** — `add_subdirectory` blocks for
    `c_deps` entries were appended after component blocks, so any
    `target_sources(… TARGET_OBJECTS:dep_core)` reference appeared before
    the target definition. They are now prepended.
- `jm apply <fragment>` now honours `module = "X"` inside a component
    section: the component is wired into `[module.X].objects` in the
    manifest and materialised as a module object (sharing the module's
    `_ext.c`, no standalone extension). Previously the directive was
    silently ignored and the object was scaffolded as standalone.
    The `module` annotation is also preserved through subsequent
    `C.save()` mutations (`_dump`'s `scalar_keys` now includes `"module"`).
- Windows / MinGW parallel-build race when a project has more than one
    standalone object: each component's CMakeLists attached a POST_BUILD
    step that copied `libwinpthread-1.dll` into the shared
    `PYTHON_PACKAGE_DIR`; `mingw32-make --parallel` ran them concurrently
    and one copy would fail with "no such file or directory" while the
    other was writing the same file. The copy is now done once at
    configure time in the top-level CMakeLists.txt (where the package
    directory is already known); per-component CMakeLists keep their
    `-static-libgcc` link option but no longer do the copy.
- Property getter declaration now emitted into `_core.h` for module
    objects (gh#8) — previously missing, causing a compile error when
    the getter was called from outside the translation unit.

### Added

- **`c_deps`** — new `[project]` TOML key (gh#12). List C-only dependency
    subdirectories; `jm apply` emits `add_subdirectory(native/src/<dep>)`
    blocks prepended before all component blocks. No Python scaffolding is
    generated for these entries.
- **`no_generate`** — new `[module.X]` flag (gh#12). When `no_generate = "true"`, `jm apply` wires the module into the root `CMakeLists.txt` but
    skips all scaffolding — useful for hand-written modules that share the
    CMake build tree.
- **`depends_on`** — new per-component list (gh#13). `jm object name --depends-on dep` (or `depends_on = ["dep"]` in TOML) emits transitive
    `target_sources(… TARGET_OBJECTS:dep_core)` entries before the
    component's own CMake entry, so the Python extension links the C objects
    it needs without a separate shared library per dependency.
- **`jm apply` bench retrofit (gh#14)** — `apply` now appends a missing
    `bench_{comp}_core` CMake target to each component's
    `native/src/<comp>/CMakeLists.txt` when one isn't already present.
    Idempotent; existing projects gain C benchmark targets without a
    manual edit.
- **`jm apply --only=NAME` (gh#15)** — restrict wiring regeneration to a
    single named component. Aggregate files (`__init__.py`, root
    `CMakeLists.txt`, umbrella header) are still updated; only the named
    component's per-file output is regenerated. Speeds up `apply` on large
    projects.

## [0.13.5] — 2026-05-19

### Fixed

- `jm apply` now reconciles the wiring files that tie components into a
    project — without this, an apply-materialized project's top
    `CMakeLists.txt` never gained `add_subdirectory(native/src/<obj>)`, the
    umbrella header never gained the component include, and the package
    `__init__.py` never gained `from .<obj> import <Obj>`. The project
    scaffolded but did not actually build the component. Reconciled files:
    top `CMakeLists.txt` (sentinel-section replacement, user content
    outside the Components / Modules regions preserved), umbrella header,
    package `__init__.py` (uses the existing `_splice_init_py` so user
    content survives), module subpackage `__init__.py` (uses
    `_merge_module_init` so user wrapper classes survive), and per-module
    `<mod>_ext.c` / `<mod>/CMakeLists.txt` / `<mod>.pyi`.
- `_init.run` (the path `jm new --object X` takes) now inserts
    `add_subdirectory(native/src/X)` into the `# ── Components` sentinel
    section instead of appending at the end of the manifest — matches
    what `_object.run` already does, and makes `jm apply` idempotent
    against projects scaffolded via either path.
- Windows docker smoke-test PowerShell quoting — `(scaffold-only)` was
    being parsed as a function call instead of part of the literal
    message; switched to `-f` format with single-quoted templates.

### Added

- `declarative_scaffold/test.py` now asserts the agc extension actually
    built (`agc*.so` / `.pyd` present in `src/demo/`). Without this, a
    cmake build that skipped the agc target silently passed ctest with
    zero registered tests.
- Four `TestApplyReconcilesAggregates` regression tests covering the
    top-CMakeLists sentinel splice, umbrella include, package
    `__init__.py` import, and preservation of user CMake content outside
    the sentinel regions.

## [0.13.4] — 2026-05-19

### Fixed

- A module-level function with no parameters generated a binding with
    `(void)args;` while its parameter was `Py_UNUSED(args)` — an
    undeclared-identifier compile error. Any project with a zero-arg
    module function failed to build.
- Module `__init__.py` corruption (gh#5, #6) — when a formatter
    (ruff / black) reflowed a long single-line import into the
    parenthesized multi-line form, a subsequent `jm property` /
    `jm method` regeneration treated the `(` as an import name and wrote
    `from .dsp import (, A, B  # noqa: E402` plus a leftover `)` block —
    a `SyntaxError`. The merge regex now accepts both forms and collapses
    back to a single canonical line.

### Added

- **`declarative_scaffold` example** — bundled end-to-end demo of the
    schema-6 workflow: one TOML fragment with inline `impl` and
    `{placeholder}` interpolation → `jm apply` → cmake + ctest green;
    plus a `jm split-objects` round-trip on a legacy single-file project.
    Run with `just-makeit example declarative_scaffold`.
- **`just-makeit split-objects`** — migrate a single-file project to the
    split layout: every top-level `[obj]` section moves out of the
    manifest into `objects/<obj>.toml`, and the manifest gains
    `include = ["objects/*.toml"]`. `[project]` and `[module.X]` stay in
    the manifest. Idempotent — running on an already-split project is a
    no-op. The loaded merged cfg is byte-identical before and after.
- **Split-TOML save routing** — `_config.save()` now re-derives
    provenance from disk and routes each section back to the file that
    owns it. A mutation on a split-layout project (`jm method`,
    `jm property`, `jm remove`, …) updates only that section's fragment
    file; the manifest and sibling fragments are byte-for-byte
    unchanged. `[project]` / `[module.X]` always live in the manifest.
    A new object on a split-layout project is written to a new
    `objects/<name>.toml`; an emptied fragment is deleted. Single-file
    projects are unaffected. User comments inside fragments are not yet
    preserved across save (we still use the deterministic `_dump`
    writer); tomlkit-based comment preservation can layer on later.
- **Inline `impl` / `impl_file` in object and method TOML sections** —
    consumed by `jm apply`. `impl = '''…'''` is a TOML literal heredoc
    carrying a C body; `impl_file = "path::funcname"` lifts the body from
    an existing C file (the existing `--impl` semantics, but declared in
    the TOML). Both forms accept Python-f-string-style `{placeholder}`
    interpolation against the object context — `{component}`,
    `{Component}`, `{module}`, `{Module}`, `{arg_type}`, `{return_type}`
    (plus `{method}` on methods, `{function}` on functions). Unknown
    placeholders and literal C braces (`{0}`, `{ … }`) pass through
    untouched, so no escape friction. An optional `replace = {...}` table
    applies string substitutions on top, layering with the existing
    `--replace` mechanism. `impl` and `impl_file` are mutually exclusive
    and validated before any side effects.
- **Split per-object TOMLs** (schema 6) — the manifest's new
    `include = ["objects/*.toml"]` key pulls in fragments containing
    one or more `[obj]` sections (and optionally `[[module.X.functions]]`
    extensions). `_config.load()` resolves the includes and merges them
    into the single dict every consumer already expects — backward
    compatible: a manifest without `include` behaves exactly as today.
    Duplicate-object across fragments errors with a specific remedy
    (`jm remove object X` first, or rename in the fragment).
- **`just-makeit apply <fragment>`** — compose-fragment path: copies the
    fragment into `objects/`, adds it to the manifest's `include` set,
    then materializes. Phase 2 (provenance + multi-file save) will let
    mutating commands write back to fragment files; for now mutations
    still target the manifest.
- **`just-makeit apply`** — materialize a project from its
    `just-makeit.toml`: generate every file each object / module / function
    in the manifest implies. Add-only — it creates missing files and never
    overwrites or deletes, so it is safe to run repeatedly. Makes a project
    reproducible from its manifest (plus any hand-written `*_core.c` /
    `*_core.h`) alone.
- **`just-makeit remove`** — the explicit, destructive counterpart to the
    additive commands. `remove object` / `remove module` delete the
    generated files, strip the `CMakeLists.txt` / umbrella-header /
    package `__init__.py` wiring, and drop the `just-makeit.toml` section;
    `remove method` / `remove property` / `remove function` drop the TOML
    entry and regenerate the affected `ext.c` / `core.h` / `.pyi`. Prompts
    for confirmation unless `--force` is given.

## [0.13.3] — 2026-05-19

### Fixed

- `jm property --field` on an object that has `init_params` no longer
    reverts the generated `create()` prototype to `(void)`. The header was
    regenerated without the params while the implementation kept them,
    producing a conflicting-types compile error (gh#4).
- `jm module <name>` no longer writes a syntactically invalid
    `__init__.py`. A freshly-created module emitted
    `from .<module> import` with an empty name list — a `SyntaxError` —
    leaving the module unimportable until the first object was added. The
    import line is now added only once an object or function exists.

### Changed

- just-makeit now builds with the `just-buildit` backend instead of
    `hatchling`, dogfooding the backend its own scaffolded projects use.
    Requires `just-buildit >= 0.3.6` (the `pure` build option).

## [0.13.2] — 2026-05-19

### Added

- **`just-makeit bench`** now builds and runs C *and* Python benchmarks,
    trims the raw per-iteration arrays (`stats.data` / `runtimes`) that
    bloat pytest-benchmark JSON by 1000x+, and writes dated snapshots to
    `benchmarks/history/`. New flags: `--tag`, `--c-only`, `--python-only`.
- **`BUILD_PYTHON`** CMake option in generated projects — guards the
    Python extension targets so the C library can build without Python.

### Changed

- `jm object` / `property` / `method` / `add` now preserve hand-written
    `core.c` / `core.h` function bodies across regeneration, matching the
    existing `ext.c` behaviour.
- The generated `Makefile` `bench` target delegates to `just-makeit bench`. Schema 4 → 5: `jm upgrade` rewrites the bench target in
    existing projects' Makefiles.

## [0.13.1] — 2026-05-18

### Fixed

- **`full_workflow` example**: `unittest discover` picked up `test_ema.py`
    (pytest-style) on systems without pytest installed, causing the artifact
    smoke test to fail. Step 8 now targets `test_gain.py` directly.

## [0.13.0] — 2026-05-18

### Added

- **`just-makeit bench`**: build and run C benchmarks, then display a
    pytest-benchmark-style ASCII table with per-benchmark min/max/mean/stddev/
    median/IQR and MSa/s throughput. On subsequent runs a **Δ vs prev** column
    appears automatically, showing throughput change vs the previous run.
    Results are saved to `.benchmarks/c/<comp>.json`.

- **`jm_bench.h`**: header-only C library dropped into `native/benchmarks/`
    on every new project. Each timing section records per-round elapsed times;
    `jm_bench_write_json()` at the end of `main()` writes
    `bench_<comp>_core.json` in **pytest-benchmark-compatible JSON** format
    (min/max/mean/stddev/median/q1/q3/iqr/ops/total/rounds/iterations) so C
    and Python bench results can be compared directly.

- **Schema 4 migration**: `just-makeit upgrade` now drops `jm_bench.h` and
    regenerates bench C files to use per-round timing for all existing projects.

- **Per-method bench timing**: each named extra method added with
    `just-makeit method` gets its own per-round timing block in the C bench
    file. Methods can be excluded with `--no-bench`.

### Fixed

- `NO_STEP_BENCH_C` template: unresolved `<<bench_create_stmt>>` placeholder
    when scaffolding `--no-step` objects that have no `--init-param` arguments
    (e.g. the `stream_chunker` example). `make_state_ctx` now always provides
    `bench_create_stmt` and `bench_destroy_stmt`, emitting a `/* TODO */`
    comment when `c_create_args` is empty so the bench file compiles cleanly.

______________________________________________________________________

## [0.12.1] — 2026-05-17

### Fixed

- `full_workflow` example: guard the step-7 pytest invocation with an
    availability check and skip gracefully when pytest is not installed,
    matching the same pattern used by the pytest-benchmark and coverage steps.

______________________________________________________________________

## [0.12.0] — 2026-05-17

### Added

- **`just-makeit upgrade`**: schema-versioned migration system for existing
    projects. Running `just-makeit upgrade` from a project root applies all
    pending migrations idempotently — existing user-edited files are never
    overwritten, and `just-makeit.toml` keys are only added, never removed.
    Mutating commands (`object`, `module`, `method`, `property`, `function`,
    `add`) now warn on stderr when the project schema is behind `CURRENT_SCHEMA`.

- **Schema 1 → 2 migration**: adds documentation scaffolding (`zensical.toml`,
    `docs/index.md`, `docs/api.md`) to projects created before v0.12.0. New
    projects scaffold these files automatically.

______________________________________________________________________

## [0.11.5] — 2026-05-16

### Fixed

- `artifact.yml`: add `pip install pytest` before the two
    `pytest --doctest-modules` steps — pytest is not present in the artifact
    CI environment and must be installed explicitly.

______________________________________________________________________

## [0.11.4] — 2026-05-16

### Fixed

- `artifact.yml`: replaced bare `pytest` with `python3 -m pytest` in two
    doctest-modules steps — `pytest` binary is not on PATH in the artifact CI
    environment.

______________________________________________________________________

## [0.11.3] — 2026-05-16

### Fixed

- `dsp_toolkit` example `test.py` was calling `python -m pytest` directly;
    replaced with `python -m unittest discover` to match the default
    `make test` behaviour for projects scaffolded without `--pytest`.

______________________________________________________________________

## [0.11.2] — 2026-05-16

### Docs

- Deleted stale `PLAN.md` and `scaffold_feedback.md`.
- `roadmap.md`: added v0.7–v0.11 shipped milestones; replaced speculative
    "Ideas" section with a problems-first "What we're thinking about next".
- `perf.md`: added narrative openings for each tool explaining when to reach
    for it.
- `customization.md`: added regeneration ownership table and typical
    post-scaffold workflow.
- New pages: `troubleshooting.md`, `faq.md`, `glossary.md`.
- Tone pass: `index.md` (pain-first opener, reframed design principles),
    `workflow.md` (scenario preambles), `c-library.md` (payoff-first opening).
- Split 700-line `commands.md` into three focused pages:
    `commands/scaffold.md`, `commands/extend.md`, `commands/build.md`.
- `pure.md`: moved quick-reference table to the top of the page.
- `commands/build.md`: added concrete `just-makeit.toml` → `script` output
    example.

______________________________________________________________________

## [0.11.1] — 2026-05-15

### Fixed

- **`make test` now uses `unittest` by default**: projects scaffolded without
    `--pytest` were incorrectly generating a `make test` target that invoked
    pytest. The default runner is now `python -m unittest discover`; pytest is
    only used when `--pytest` was passed to `just-makeit new`.

______________________________________________________________________

## [0.11.0] — 2026-05-15

### Added

- **`--pytest` and `--pytest-benchmark` flags for `just-makeit new` and
    `just-makeit object`**: scaffold pure pytest test files (no unittest shim)
    and `pytest-benchmark` bench files. Both flags are stored in
    `just-makeit.toml` and inherited by subsequent `object`, `add`, and `init`
    commands from the project config.

- **Auto-generated Python test suites, benchmarks, and numpy-style `.pyi`
    stubs**: generated pytest suites cover `create`, `step`, `steps`,
    getters/setters, reset, context manager, and destroy with doctest examples
    and type-annotated signatures. Bench files exercise both single-step and
    block-processing throughput using stdlib `time.perf_counter` (default) or
    `pytest-benchmark` (with `--pytest-benchmark`).

- **`--batch` flag for `just-makeit method`**: generates a 1:1-rate array
    transform — C stub `(state, const in_t *in, size_t n, out_t *out)` and a
    Python wrapper that allocates the output array per call. Previously this
    pattern required writing the Python glue manually.

- **New generated file `cmake/<project>-config.cmake.in`**: every
    `just-makeit new` project now scaffolds a proper CMake package config
    wrapper with `@PACKAGE_INIT@`, enabling relocatable `find_package` support
    after `cmake --install`.

### Fixed

- **Generated pkg-config file used absolute paths**: `cmake/<project>.pc.in`
    used `@CMAKE_INSTALL_FULL_LIBDIR@` and `@CMAKE_INSTALL_FULL_INCLUDEDIR@`,
    baking the install prefix in at configure time. This broke DESTDIR staging
    and installs to non-default prefixes. Now uses relocatable
    `${exec_prefix}/@CMAKE_INSTALL_LIBDIR@` and
    `${prefix}/@CMAKE_INSTALL_INCLUDEDIR@`.

- **CMake config install lacked `@PACKAGE_INIT@`**: `install(EXPORT ... FILE ...-config.cmake)` made the targets file serve as the config file, omitting
    the `PACKAGE_PREFIX_DIR` setup that `CMakePackageConfigHelpers` provides.
    Consumers using `find_package` after a DESTDIR-staged or prefix-changed
    install would get the build-tree prefix. The install section now uses
    `configure_package_config_file` with `@PACKAGE_INIT@`; the targets file is
    correctly named `<project>-targets.cmake`.

### Docs

- **CLI help and all docs now consistent with all implemented flags**:
    `--batch` (method), `--pytest`, `--pytest-benchmark`, and `--mutable` (new)
    were implemented but missing from `--help`, `docs/cli.md`,
    `docs/commands.md`, and `README.md`. All four locations are now in sync.

- **`docs/commands.md` method narrative updated**: the 1:1-rate array section
    now leads with `--batch` (the automated path) and moves manual glue writing
    to a secondary note.

- **pkg-config & CMake package config guide** added to `docs/developers/`
    covering both toolchain ecosystems, install layout, common pitfalls,
    relocatability, and platform notes (Debian multiarch, macOS, MSYS2/Windows).

______________________________________________________________________

## [0.10.9] — 2026-05-13

### Fixed

- **`array_processing` example — leftover `my_conv` directory**: the example
    scaffolds `my_conv` for a structural assertion then deletes it, so it no
    longer appears as an unbuilt project in the Docker examples directory and
    fails the `.pyd`-presence smoke test.

## [0.10.8] — 2026-05-13

### Fixed

- **Windows — generated Makefile `SHELL`**: switched from `SHELL = sh.exe` to
    `SHELL = cmd.exe` on `Windows_NT`. MinGW's `sh.exe` is present in the
    distribution but its MSYS2 DLL dependencies are not on `PATH` inside a
    Windows container, so invoking it raised "The system cannot find the path
    specified." The `test` target and the dependency-check lines in
    `$(BUILD_DIR)/CMakeCache.txt` are now written with OS-specific `ifeq` blocks:
    Windows uses `2>nul` redirects and a Python one-liner to handle pytest exit
    code 5 (no tests collected); non-Windows keeps the original POSIX shell forms.
    `NPROC` and `PYTHON` variable defaults also have Windows-specific branches
    (`NPROC ?= 4`; `python` instead of `python3`).

## [0.10.7] — 2026-05-13

### Fixed

- **Generated pytest — void-input generators**: `test_step_runs`,
    `test_steps_shape_dtype`, `test_context_manager`, and `test_destroy` now
    emit `obj.step()` (no argument) and `obj.steps(64)` (integer count) for
    objects scaffolded with `--arg-type void`. Previously all four tests
    passed a value to `step()` and an ndarray to `steps()`, which caused
    `TypeError` at runtime for generator objects.

## [0.10.6] — 2026-05-13

### Fixed

- **Windows — generated Makefile `SHELL`**: both Makefile templates now use
    `SHELL = sh.exe` on `Windows_NT` instead of `SHELL = /bin/sh`, so
    `mingw32-make` uses MinGW's bundled `sh.exe` rather than falling back to
    `cmd.exe`. Without this, the `test` target's `ret=$$?; [ $$ret -eq 0 ]`
    syntax failed with `'[' is not recognized as an internal or external command`.

## [0.10.5] — 2026-05-12

### Fixed

- **Docker / rootless containers**: `jm-install-deps` and `just-makeit install-deps` no longer call `sudo` when running as root (e.g. inside a
    Docker `RUN` step). Previously, `install-deps.sh` hardcoded `sudo` in every
    package-manager invocation; it now omits `sudo` when `id -u` returns 0.
- **Windows — `.pyd` import after `pip install -e .`**: test fixtures for
    `fir_filter`, `running_stats`, and `sliding_correlator` now pass
    `PYTHON=sys.executable` to `make`, ensuring CMake links the extension against
    the same Python SOABI that the test runner uses. Without this, MinGW's
    `sh.exe` could resolve `python3` to a different interpreter and produce a
    `.pyd` with a mismatched SOABI tag.
- **Windows — iqfile binary read corruption**: `07_demo.py` now opens the q15
    file with `O_BINARY` so the Windows UCRT does not perform CR/LF translation
    on binary sample data.
- **Uninitialized field-backed properties**: the generated `_core.c` now uses
    `calloc` instead of `malloc` in the `create()` function. Fields added after
    initial scaffolding via `jm property --field` are now guaranteed to be
    zero-initialised rather than containing heap garbage.

______________________________________________________________________

## [0.10.4] — 2026-05-12

### Added

- **PyMethodDef doctests**: all generated `ml_doc` strings now contain working
    Python doctests (exact scalar values for scalar returns, shape/dtype checks
    for array returns). Run them via `pytest --doctest-modules`.
- **Doxygen scaffolding**: `jm new` now writes a `Doxyfile` configured for the
    generated project so `doxygen` works out of the box.
- **`jm-run-tests` entry point**: `jm-run-tests` (bundled with the PyPI
    package) installs and runs the test suite via `uv run`, replacing per-CI
    ad-hoc install commands in all workflows.
- **`jm-install-deps` in all CI workflows**: `ci.yml` and `release.yml` now
    use the packaged `jm-install-deps` / `jm-run-tests` entry points instead of
    inline shell incantations.
- **Dynamic example discovery**: `_EXAMPLES` is now assembled at import time by
    walking `examples/` for subdirs that contain a `test.py`, so new examples are
    picked up without editing `_example.py`.

### Fixed

- **Windows — CMake Python3 detection**: the generated `Makefile` now derives
    `Python3_EXECUTABLE` via `sys.executable` (normalised to POSIX slashes with
    `pathlib`) instead of `which python3`. Git Bash's `which` returns a
    Unix-style path that Windows CMake 4.3 cannot execute.
- **Windows — example integration tests fully pass**: the generated `Makefile`
    now forces `-G "MinGW Makefiles"` when `OS=Windows_NT` so CMake picks `gcc`
    instead of MSVC (which rejects C99 `float complex`). Test fixtures no longer
    skip on Windows; `./demo` → `demo.exe`, DLL directory prepended to `PATH` at
    runtime, `-Wl,-rpath` omitted on Windows, and `pytest-benchmark` moved from
    required to optional deps in the generated `pyproject.toml` so `uv pip install -e .` no longer tries to overwrite the locked `pytest.exe` in the
    test runner venv.

______________________________________________________________________

## [0.10.3] — 2026-05-12

### Fixed

- **Gap #1 — `__init__.py` preservation**: `_regenerate_module` now merges new
    class/function exports into an existing `__init__.py` instead of
    overwriting it. User-written wrapper classes, docstrings, and other content
    below the re-export line are left untouched. Also handles the empty-import
    initial state (file created by `jm module` before any objects are added).
- **Gap #2 — `batch` flag persistence**: `batch = true` is now written to
    `just-makeit.toml` so regenerated methods use array-input wrappers
    (`METH_VARARGS`) rather than reverting to `METH_NOARGS`. The `--batch` flag
    is also wired through the CLI `method` command.
- **Gap #3 — C body preservation on regeneration**: `_regenerate_module` now
    extracts existing `static PyObject *` function bodies before overwriting
    `module_ext.c` and splices them back in afterwards. Brace-counting (not
    regex) handles parameters with nested parens such as `Py_UNUSED(ignored)`.
- **Gap #4 — `--no-step` in mixed modules**: objects scaffolded with
    `--no-step` no longer emit `step`/`steps` wrappers when co-resident in a
    module with objects that do have a step.
- **Gap #5 — phantom `module_core.h` include**: `module_ext.c` no longer
    unconditionally includes `<module>/<module>_core.h`. The include is emitted
    only when module-level functions are present (they are the only consumers
    of that header).
- **Gap #6 — CMakeLists external lib block propagation**: when a new object is
    added to a module, `if(VAR) target_link_libraries/target_include_directories … endif()` blocks found in sibling CMakeLists files are copied and adapted for
    the new component automatically.

### Added

- **+11 tests** (716 total): `tests/test_module_gaps.py` covering all six gaps.

______________________________________________________________________

## [0.10.2] — 2026-05-11

### Added

- **`--no-state` for `jm object`**: suppress the default auto-generated state
    variable, constructor arguments, and getter/setter scaffolding. Emits
    `<<IMPLEMENT>>` stubs in the C struct body and `create()`/`destroy()`/`reset()`
    so you fill in the real domain-specific constructor signature by hand.
    Mutually exclusive with `--state`. All downstream commands (`jm method`,
    `jm property`) detect the flag from TOML and regenerate correctly.
- **`--no-step` for `jm object`**: suppress `step()` and `steps()` from all C
    and Python output. Lifecycle scaffolding (`create`, `destroy`, `reset`) is
    still generated. The bench stub becomes a minimal printf with no volatile
    sink. Use for objects whose interface is entirely via named `jm method` calls
    (e.g. FIR filters, decimators, block processors).
- **`--out-type TYPE` and `--out-divisor N` for `jm method`**: allocate an
    output array per call and pass `*out` to the C stub automatically. The array
    length is `in_len / out_divisor`. Use `--out-divisor 2` for CI8/CI16/CI32
    inputs where two raw bytes form one complex output sample.
- **+21 tests** (705 total): `--arg-type T[]` standalone and in-module (21).

### Fixed

- **`--arg-type T[]` without `--return-type`** now correctly defaults to
    `void` for both standalone and in-module objects. Previously, omitting
    `--return-type` caused an internal error (the array element type was
    propagated as the return type, which is not a scalar and raised a
    `ValueError`/`KeyError` during template rendering). The fix affects four
    sites: `make_sample_ctx` (default logic), `_init.py` and `_object.py`
    (`make_step_ctx` call), and `_config.py` (TOML persistence — the bug also
    caused the wrong value to be written to `just-makeit.toml`, which broke
    module regeneration on reload).

______________________________________________________________________

## [0.10.1] — 2026-05-11

### Added

- **Type stubs** (`__init__.pyi`): every module subpackage now ships a
    generated `.pyi` alongside its `__init__.py`, kept in sync by every
    `object`, `method`, `property`, and `function` command. Standalone objects
    already had `.pyi` files; module objects now do too. Type maps: `float` /
    `double` → `float`, `*_Complex` → `complex`, arrays → `NDArray[np.dtype]`.
- **`--arg-type type[]` for objects**: objects whose primary operation
    processes a whole buffer in one call (decimators, packet framers, block
    codecs) can now declare their input as an array type. The C step receives
    `(const elem_t *x, size_t x_len)`; the Python wrapper uses
    `PyArray_FROM_OTF`; `steps()` is not generated (the primary op already takes
    a buffer). Supported element types: all scalar types accepted by `--arg-type`.
- **`install.sh` bootstrap**: `curl`-pipeable installer requiring no pre-existing
    tools (no uv, no pip). Detects Python ≥ 3.11, installs cmake + C compiler via
    the system package manager, creates a venv, and pip-installs just-makeit +
    numpy. Served from GitHub Pages for a short URL:
    `. <(curl -fsSL https://just-buildit.github.io/just-makeit/install.sh)`
    Sourcing via `. <(curl ...)` auto-activates the venv in the current shell.
    Supports `--check`, `--force`, and a custom venv path argument.
- **Example tests hardened**: all 8 bundled examples now assert type stubs are
    generated with correct signatures. `array_processing` adds pattern 5
    demonstrating `--arg-type type[]`. `test_readme_assembled.py` blocks stale
    example READMEs in CI.
- **Artifact CI tests all examples**: `artifact.yml` now runs
    `just-makeit example <name>` for every bundled example after PyPI install,
    testing the exact TL;DR shown in each README.
- **+8 tests** (668 total): README assembled checks (8).

______________________________________________________________________

## [0.10.0] — 2026-05-11

### Added

- **`--return-type void` for objects** (`jm new` / `jm object`): sink and
    side-effect objects are now fully supported. The generated bench compiles
    cleanly — no `volatile void` or `sizeof(void)`. `steps()` drops the output
    array parameter; Python `step()` / `steps()` return `None`.
- **Array parameters for methods and functions** (`--param name:type[]`):
    `jm method` and `jm function` now accept numpy array inputs. The C stub
    receives `(const elem_t *name, size_t name_len)`; the Python wrapper
    generates `PyArray_FROM_OTF` + `Py_DECREF` automatically. Supported element
    types: all fixed-width integer types, float, double, float \_Complex,
    double \_Complex. Mixed scalar + array params are supported in a single call.
- **CLI help overhauled**: `just-makeit help` now documents void return types,
    array `--param` syntax with elem-type list, and real-world examples
    (`execute_ctrl`, `apply_window`, sink/generator objects).
- **+41 tests** (612 total): void return (10), method array param (11),
    function array param (11), CLI void return (4), CLI array param (6),
    help content (9).

______________________________________________________________________

## [0.9.13] — 2026-05-11

### Added

- **`just-makeit example <name>`**: run any bundled example end-to-end in a
    temporary directory — no `git clone` required (`uvx just-makeit install-deps && just-makeit example fir_filter`). All 8 examples are now shipped inside
    the wheel under `just_makeit/examples/`.
- **Bench template DCE fix**: `(void)step(obj)` in warmup and step-timing loops
    is now `volatile <<return_ctype>> _sink = step(obj)` so the compiler cannot
    dead-code-eliminate the measured loop at any optimisation level. Applies to
    both the stateful-object bench and the pure-function bench.
- **Example TL;DR blocks updated**: all 8 example READMEs now show the
    `just-makeit example <name>` one-liner instead of the `git clone + python3 test.py` form.

______________________________________________________________________

## [0.9.12] — 2026-05-11

### Fixed

- **Duplicate getter/setter declarations in `_core.h`**: when a `--state`
    variable also had a `jm property` added for it, both `make_state_ctx` and
    `make_properties_ctx` emitted declarations for the same C functions.
    `make_properties_ctx` now skips `property_decls` for any name already
    covered by `make_state_ctx`.

______________________________________________________________________

## [0.9.11] — 2026-05-11

### Fixed

- **Generated test scaffold**: `ALMOST_EQ_C(a, b, tol)` macro double-evaluated
    `a`, silently calling a stateful `step()` twice for complex-returning objects
    (LO, NCO, any `--return-type "float _Complex"`). Replaced with
    `_almost_eq_c` / `_almost_eq` static inline functions and thin macro wrappers
    so each argument is evaluated exactly once.
- **Release checklist**: GitHub Release step was missing; `release.yml` only
    publishes to PyPI and does not create GitHub Releases automatically.

______________________________________________________________________

## [0.9.10] — 2026-05-11

### Fixed

- **`property --field` on module objects** now updates `obj_core.h` in
    addition to regenerating `module_ext.c`; previously the struct field
    was silently omitted from the header.

______________________________________________________________________

## [0.9.9] — 2026-05-10

### Added

- **`jm-install-deps --check`** (`-Check` on Windows): reports what is already
    installed and what will be installed without making any changes; exits 1 if
    anything is missing, 0 if all present.
- **Prerequisites section** added to all seven example READMEs showing
    `jm-install-deps --check` / `jm-install-deps` / `source` workflow.

### Fixed

- **Windows: `uv tool install .` crash** — switched `just-makeit`'s own build
    backend from `just-buildit` to `hatchling`; `just-buildit` called
    `_python_link_flags()` unconditionally, which raises on Windows when the
    Python import library is absent (GitHub Actions hosted tool cache).
- **Windows: example build tests** — `test_examples.py` now skips on `win32`
    (MSVC rejects C99 `float complex`, same reason as `TestNewBuild`).
- **Windows: `UnicodeEncodeError` on cp1252 consoles** — replaced all Unicode
    arrows (`→`) with ASCII `->` in `_cli.py` (`_USAGE`), `_templates.py`
    (generated `README` and `*_core.h` lifecycle comment), and helper scripts.
- **`test_perf.py`**: bare `write_text()` replaced with `write_text(encoding="utf-8")`.

______________________________________________________________________

## [0.9.7] — 2026-05-10

### Added

- **`--arg-type void`** on `new --object` and `object` commands: generate a
    no-input (source/generator) object whose `step()` signature is
    `T comp_step(const comp_state_t *state)` with no input parameter, and whose
    `steps()` block processor is `void comp_steps(state, T *out, size_t n)`.
- **`method --multi-output T`** now wires secondary out-pointer parameters into
    the C stub declaration *and* the Python wrapper: stack-allocates each extra
    output, calls C with `&outN`, returns a `PyTuple_Pack` tuple.
- **`property --field`** on the `property` command: declares `T pname;` as a
    struct field in `comp_state_t` and auto-implements the getter as
    `return state->pname` and setter as `state->pname = v` — no manual
    `<<IMPLEMENT>>` stubs needed. Computed (non-`--field`) properties are
    unchanged.

### Fixed

- `clib_common.h` now installed to the include prefix alongside component
    headers; previously excluded by CMake install rules, causing `fatal error: 'clib_common.h' file not found` when compiling external C consumers.
- `just-makeit add` now preserves field-backed property struct fields and
    method declarations when regenerating `_core.h` and `ext.c`.

______________________________________________________________________

## [0.9.6] — 2026-05-10

### Added

- `steps(x, out=buf)` zero-copy path: when an output buffer is passed to
    `steps()`, the C function writes directly into it and returns the same Python
    object (no allocation). `out` is accepted by all stateful objects and
    pure-struct types.
- `array_processing` example: four array-processing patterns (auto-generated
    `steps()`, fixed-output method, variable-output method, multi-output method).

### Fixed

- `_perf.py`: `static inline → JM_FORCEINLINE JM_HOT` upgrade now patches
    `_core.h` (where the inline step lives).
- `_init.py`: `arg_type` / `return_type` now persisted to `just-makeit.toml`
    for standalone objects; previously the default `float _Complex` was reloaded
    when `just-makeit method` re-rendered `_core.h`.
- Step stub in `_core.h` restored to `const` state pointer (placeholder body
    does not mutate state), matching example patch-script expectations.
- `make_methods_ctx`: variable-output declarations now include the input
    parameter (`const arg_t *in, size_t n_in`) and multi-output extra params
    when `arg_type != void`.
- `fir_filter` and `sliding_correlator` example patch scripts: insertion-point
    regex updated to match the `steps_c_decl` docstring anchor; `steps()` body
    regex fixed (`void\s+` not `void\s*\n`).
- `test_steps_out_param` template: uses separate `obj1`/`obj2` instances so
    the stateful-filter comparison is valid across both calls.

______________________________________________________________________

## [0.9.5] — 2026-05-10

### Added

- **`just-makeit method <name>`** — add a named execute method to an object:
    scalar fixed-output (`return_type scalar_fn(state, arg x)`), variable-output
    (`size_t fn(state, [in, n_in,] ret *out)` with pre-allocated Python buffer),
    and multi-output (tuple of zero-copy NumPy views).
- **`just-makeit property <name>`** — add a named computed property backed by a
    C function; getter auto-registered in the Python type's `tp_getset`.
- **`--array-arg name:dtype`** on `object` / `new --object` — declares a
    constructor parameter that is a NumPy array (any NumPy dtype string accepted);
    C side receives a typed pointer, Python side passes an ndarray.
- **Function commands** — `just-makeit function` scaffolds a standalone
    pure-function extension (no state, no lifecycle) for simple numeric operations.

______________________________________________________________________

## [0.9.4] — 2026-05-10

### Fixed

- `artifact.yml` biquad spectral test: same `t/512` and `reset()` bugs fixed in v0.9.3 for the local test were also present in the CI smoke test.

______________________________________________________________________

## [0.9.3] — 2026-05-10

### Added

- `jm-install-deps` console script: detects OS, installs cmake + C compiler via system package manager, creates a venv at `/tmp/jm-venv` (configurable), installs numpy and just-makeit into it.
- `jm-docker-e2e` console script: runs the full artifact smoke test in a clean Docker container (mirrors `artifact.yml`).
- Both scripts are bundled in the wheel (`just_makeit/scripts/`) and exposed as proper `[project.scripts]` entry points.

### Fixed

- Generated `COMPONENT_TEST_C`: getter/setter checks now run before `step()` is called, so state-mutating `step()` implementations (e.g. running_stats incrementing `n`) no longer cause false failures.
- `filter_module` example smoke test: biquad spectral test used normalized time (`t/512`) instead of sample indices, placing both lo and hi signals well below the filter cutoff. Fixed to use sample indices so the stopband test is meaningful.
- `filter_module` example smoke test: `reset()` resets all state vars (including coefficients) to type defaults, not constructor values — fixed test to use a fresh `Biquad` instance for the stopband check instead of calling `reset()`.
- All example `test.py` files updated from `component=` to `object_name=` keyword arg.
- `pyproject.toml` pytest config: removed stale `--ignore` flags; all tests (including cmake-build example tests) now run in the default suite.
- `filter_module` README: added "What you'll need" prerequisites, `ctest` step in build instructions.

______________________________________________________________________

## [0.9.2] — 2026-05-10

### Fixed

- Generated `Makefile` now auto-installs `pytest` if not present (same pattern as numpy), so `make test` works in bare environments such as the post-release artifact smoke test.
- Removed the broken `unittest discover` fallback from both Makefile templates. When pytest exits 5 (no tests collected — normal for the module workflow) `make test` now succeeds cleanly; any other non-zero exit propagates as a real failure.
- Removed `2>/dev/null` from the pytest invocation so failures are visible.

______________________________________________________________________

## [0.9.1] — 2026-05-10

### Fixed

- `artifact.yml` post-release smoke tests were failing due to stale `--component` / `just-makeit init` CLI references carried over from v0.8.x. All references updated to `--object` / `just-makeit object` to match the v0.9.0 CLI.

______________________________________________________________________

## [0.9.0] — 2026-05-10

### Breaking

- `just-makeit init` removed. Use `just-makeit object <name>` (standalone,
    own `.so`) or `just-makeit object <name> --module <mod>` (grouped into a
    module subpackage).
- `new --component name` renamed to `new --object name`.
- `add --component name` renamed to `add --object name`.

At the C level nothing changes — the generated `_core.h`, `_core.c`, OBJECT
library, and Python binding are identical. Only the CLI surface is unified:
`object` is now the single command for adding any Python type, standalone or
in-module.

### Docs

- README, workflow, commands, pure, perf, customization, c-library, and all
    example docs updated to use `object`/`--object` throughout.
- Quickstart restructured: **Standalone object** and **Module subpackage** are
    now clearly labelled separate paths.
- Commands table split `object` into two rows: one for standalone (no
    `--module`), one for in-module (with `--module`).

______________________________________________________________________

## [0.8.4] — 2026-05-10

### Added

- `just-makeit new <project> --module <name>` — scaffold one or more empty
    extension modules in the same command as the project. `--module` is
    repeatable: `--module osc --module env` scaffolds both modules at once.
    Equivalent to running `just-makeit module` separately for each name.

### Docs

- README: commands table and quickstart updated for `new --module`
- `docs/commands.md`: `--module` argument documented under `new`
- `docs/workflow.md`: Scenario 3 updated to use `new --module filter`

______________________________________________________________________

## [0.8.3] — 2026-05-10

### Fixed

- Generated `Makefile` `test` target now treats pytest exit code 5 ("no tests
    collected") as success rather than falling through to the `unittest discover`
    fallback. Module-only projects (no standalone components) have no
    `src/<pkg>/tests/` directory, so the fallback always failed.

______________________________________________________________________

## [0.8.2] — 2026-05-10

### Fixed

- Test: `TestNewScaffoldOnly.test_no_component_files` updated to allow
    the `native/src/<project>_lib.c` stub introduced in 0.8.1 — checks for
    absence of component *directories* rather than the directory itself.

______________________________________________________________________

## [0.8.1] — 2026-05-10

### Fixed

- `just-makeit new` now generates `native/src/<project>_lib.c` (a version stub),
    and `CMakeLists.txt` references it instead of `""`. The empty-string source was
    rejected by CMake on macOS with AppleClang 17 (`No SOURCES given to target`).
- `just-makeit object` now patches `target_sources(<pkg>_lib PRIVATE $<TARGET_OBJECTS:<comp>_core>)` into the root `CMakeLists.txt` alongside the
    existing `add_subdirectory` patch. Previously, module-only projects built an
    empty `lib<pkg>.so`; now all object cores are wired in, enabling `cmake --install`,
    pkg-config, and CMake `find_package` for module-based projects.
- CI: `artifact.yml` PyPI propagation retry extended from 10 to 20 × 30 s (10 min);
    `artifact.yml` adds C library install + pkg-config/find_package consumer steps for
    the `filter_module` workflow.

______________________________________________________________________

## [0.8.0] — 2026-05-09

### Added

- **`just-makeit module <name>`** — scaffold a named Python extension
    module (subpackage `.so`) that groups multiple types. Creates
    `native/src/<name>/<name>_ext.c`, `CMakeLists.txt`, and
    `src/<pkg>/<name>/__init__.py`; records `[module.<name>]` in
    `just-makeit.toml`.
- **`just-makeit object <name> [--module <name>]`** — add a Python type
    to an existing module. Generates the full C library scaffold
    (`_core.h`, `_core.c`, test, bench, OBJECT-only `CMakeLists.txt`)
    then fully regenerates the module's `_ext.c`, `CMakeLists.txt`, and
    `__init__.py` from the complete object list. `--module` is inferred
    when only one module exists. Supports all flags from `init`:
    `--state`, `--pure`, `--arg-type`, `--return-type`, `--perf`.
- Module `_ext.c` is always regenerated from scratch — never patched —
    so adding a third type never disturbs existing ones.
- Types within a module may have different `--arg-type`/`--return-type`
    (e.g. `Fir` processes `float complex`, `Biquad` processes `float`).

### Fixed

- Generated C tests now use a `CHECK(cond)` macro counter instead of
    `assert()`. Failures print `FAIL file:line expr` and exit nonzero —
    no silent pass under `-DNDEBUG`.
- Generated `CMakeLists.txt`: test and bench targets now link `-lm`,
    preventing linker failures on projects that use `<math.h>`.

### Docs

- `docs/commands.md`: `module` and `object` command reference added.
- `examples/filter_module/`: complete walkthrough — `Fir` (complex) +
    `Biquad` (real) in a single `filter` module, with end-to-end
    `test.py` covering scaffold, patch, build, ctest, and spectral checks.
- `artifact.yml`: filter_module scaffold + verify + build + smoke test
    block added to the release artifact CI.

______________________________________________________________________

## [0.7.0] — 2026-05-09

### Added

- Generated project `README.md` now includes a Requirements section
    listing Python 3.11+, CMake ≥ 3.16, a C99 compiler, NumPy, and
    pkg-config with per-platform install commands.
- `docs/c-library.md` — dedicated end-user guide for installing and
    consuming the generated C library: prerequisites, build + install,
    pkg-config and CMake find_package usage, rpath options, and
    verification steps.

### Fixed

- `just-makeit init` now uses `target_sources(… PRIVATE $<TARGET_OBJECTS:…>)`
    to wire component OBJECT libraries into the combined shared library.
    The previous `target_link_libraries` approach produced an empty
    `lib<project>.so` on some CMake versions.
- Generated `.pc` file now has the correct `includedir` when installing
    to a non-default prefix: cmake is reconfigured with
    `CMAKE_INSTALL_PREFIX` before `cmake --install`, ensuring
    `configure_file` regenerates the `.pc` with the right paths.

### Docs

- `docs/workflow.md`: C library section updated with correct cmake
    install sequence, split pkg-config invocation, `--as-needed` note,
    and link to `docs/c-library.md`.

______________________________________________________________________

## [0.6.9] — 2026-05-09

### Changed

- CI: `artifact.yml` C library section consolidated from five steps to
    two: **Install C library** (reconfigure + install) and **Verify C
    consumers** (pkg-config + CMake find_package in a single step).

______________________________________________________________________

## [0.6.8] — 2026-05-09

### Fixed

- CI: `artifact.yml` pkg-config consumer now splits `--cflags` and
    `--libs` with the source file between them. Ubuntu's `--as-needed`
    linker default silently drops a shared library that appears before
    the object referencing it, causing undefined-reference errors.

______________________________________________________________________

## [0.6.7] — 2026-05-09

### Fixed

- Generated `libmy_project.so` now actually contains the component
    symbols. `just-makeit init` was appending
    `target_link_libraries(…_lib PRIVATE …_core)` to link OBJECT files
    into the combined shared library, which is unreliable across CMake
    versions and produces an empty `.so`. Replaced with
    `target_sources(…_lib PRIVATE $<TARGET_OBJECTS:…_core>)`, the
    canonical approach supported since CMake 3.1.

______________________________________________________________________

## [0.6.6] — 2026-05-09

### Fixed

- CI: `artifact.yml` C library install now reconfigures cmake with the
    target prefix (`-DCMAKE_INSTALL_PREFIX=...`) before `cmake --install`,
    so the generated `.pc` file has the correct `includedir` rather than
    the default `/usr/local` baked in at build time.

______________________________________________________________________

## [0.6.5] — 2026-05-09

### Fixed

- CI: `artifact.yml` pkg-config step now `export`s `PKG_CONFIG_PATH`
    before the `$(pkg-config ...)` subshell expansion; the previous
    inline prefix only applied to `gcc`, not to the subshell.

______________________________________________________________________

## [0.6.4] — 2026-05-09

### Changed

- CI: `artifact.yml` PyPI propagation wait replaced with a retry loop
    (10 × 30 s, up to 5 min) so the smoke test never fails on slow PyPI
    indexing.

### Docs

- `docs/roadmap.md`: v0.6.2 and v0.6.3 shipped sections added;
    `just-makeit ci --provider github|woodpecker` added to ideas.
- `README.md`: `dsp_toolkit` description updated to reflect
    `__init__.py` auto-splice (gap is fixed, not demonstrated).

______________________________________________________________________

## [0.6.3] — 2026-05-09

### Changed

- CI: `artifact.yml` rewritten around `fir_filter` example — real algorithm,
    array + complex state, `just-makeit perf`, impulse response assertion in
    the C consumer, multi-component `__init__.py` splice check.

______________________________________________________________________

## [0.6.2] — 2026-05-09

### Changed

- CI: post-publish smoke tests extracted into dedicated `artifact.yml`
    (triggered via `workflow_run` on Release); `release.yml` now handles
    `test → build → publish` only.

______________________________________________________________________

## [0.6.1] — 2026-05-09

### Added

- `examples/dsp_toolkit` — two-component library (Gain + EMA) that walks
    through the full multi-component workflow end-to-end and verifies the
    `__init__.py` auto-splice in CI.
- `docs/workflow.md` rewritten around two end-to-end scenarios: standalone
    extension and multi-component package.

### Fixed

- `just-makeit init` now automatically splices the new component's import and
    `__all__` entry into the existing `src/<pkg>/__init__.py` instead of leaving
    it untouched. Handles missing `__all__`, multi-line `__all__`, and user
    additions; idempotent.
- Generated `pyproject.toml` lists `pytest-benchmark` as a runtime dependency
    (moved from `[project.optional-dependencies]`) so `pip install .` provides
    everything needed to run `make bench`.
- `JM_UNROLL` comment in `jm_perf.h` corrected: it is a directive (obeyed
    unconditionally), not an advisory hint like `JM_HOT` or `JM_LIKELY`.

______________________________________________________________________

## [0.6.0] — 2026-05-08

### Added

- `--arg-type TYPE` and `--return-type TYPE` flags on `just-makeit new` and
    `just-makeit init` — generated `step()` and `fn()` signatures are no longer
    hardcoded to `float _Complex`. Both flags accept any supported C scalar type:
    `float`, `double`, `float _Complex`, `double _Complex`.
- Generated Python bindings, `.pyi` stubs, C tests, benchmarks, and NumPy
    `steps()` loops all derive their types from the declared `arg_type` /
    `return_type` — no manual edits needed after scaffolding.
- `arg_type` and `return_type` fields persisted in `just-makeit.toml`; read
    back by `just-makeit add` so regenerated files stay consistent.
- `make_sample_ctx(arg_type, return_type)` in `_templates.py` — single source
    of truth for all type-derived template keys (`<<arg_ctype>>`, `<<return_ctype>>`,
    `<<in_np_enum>>`, `<<out_np_dtype>>`, `<<step_parse_block>>`, etc.).
- `examples/sliding_power` — end-to-end example using `--return-type float`
    since signal power is real-valued; demonstrates that `step()` need not return
    the same type it receives.

______________________________________________________________________

## [0.5.0] — 2026-05-08

### Added

- `jm_simd.h` — width-portable SIMD operation macros included automatically
    with `--perf`. Provides `JM_VEC_F32`/`JM_VEC_F64` types and `JM_ZERO_`,
    `JM_SPLAT_`, `JM_LOAD_`, `JM_STORE_`, `JM_ADD_`, `JM_MUL_`, `JM_FMA_`,
    `JM_MAC_`, `JM_HSUM_` macro families, plus `jm_dot_f32`/`jm_dot_f64` helpers.
    ISA tier selected at compile time: AVX-512F → AVX2+FMA → scalar fallback.
    Write `step_batch()` once; the compiler picks the best vector width.
- New macros added to `jm_perf.h`: `JM_UNROLL(n)`, `JM_ASSUME_ALIGNED(ptr, n)`,
    `JM_PREFETCH(ptr, rw, loc)` — loop unroll hint, alignment assertion for
    auto-vectorisation, and software prefetch.

______________________________________________________________________

## [0.4.0] — 2026-05-08

### Added

- **C library distribution** — each component's `_core.c` now compiles as a
    CMake OBJECT library (`<comp>_core` OBJECT) and links into *both* the Python
    DSO and a combined `lib<project>.so`. One compilation, two consumers.
- `lib<project>.so` target in the top-level `CMakeLists.txt`, accumulating all
    component OBJECT targets. `just-makeit init` patches
    `target_sources(${PROJECT_NAME}_lib …)` alongside the existing
    `add_subdirectory` patch.
- `cmake/<project>.pc.in` — pkg-config template; `cmake --install` makes
    `gcc $(pkg-config --cflags --libs my-project) main.c` work out of the box.
- `cmake/<project>-config.cmake.in` — CMake `find_package` template for C/C++
    consumers; exposes `my_project::my_project` imported target.
- `native/inc/<project>.h` — umbrella header that `#include`s all component
    headers; the installed library exposes exactly one include path.
- `install()` rules for the shared library, all headers, pkg-config file, and
    CMake config package.

______________________________________________________________________

## [0.3.0] — 2026-05-08

### Added

- `--pure` flag on `just-makeit new` and `just-makeit init` — generates a
    **stateless** component where the caller supplies all parameters per call.
    Style is auto-detected from the state variable types:
    - **Scalar-only state** → *scalar* style: params passed per call as function
        arguments. The Python module exports `<comp>(x, **params)` and
        `<comp>_steps(arr, **params)`; a `.steps` attribute is attached to the
        function in `__init__.py` so `<comp>.steps(arr)` also works.
    - **Any array state** → *struct* style: caller-managed `<comp>_params_t`
        struct with heap-alloc helper `_params_create()` (uses `calloc`; comment
        shows `aligned_alloc` for SIMD), `_params_free()`, and `_params_init()` for
        stack/custom allocation. Python exposes a `<Component>` callable class
        (`obj(x)` via `tp_call`, `obj.steps(arr)`, context-manager support).
- Both pure styles ship with: C test, C benchmark, Python benchmark, `.pyi`
    stub, and pytest test — all regenerated on `just-makeit add --state`.
- `examples/fir_filter` Step 8: pure FIR variant demonstrating struct-style
    caller-managed params, multiple independent channels, and stack allocation.
- `pure` field persisted in `just-makeit.toml`; read back by `just-makeit add`
    to select the correct template set when state variables change.

## [0.2.0] — 2026-05-08

### Added

- `just-makeit perf` command — upgrades an existing project to use performance
    annotations in-place: writes `jm_perf.h`, patches `step()` with
    `JM_FORCEINLINE JM_HOT`, records `perf = true` in `just-makeit.toml`.
    Never touches user-written function bodies. Idempotent.
- `JM_DEFINE_STEPS` macro in `jm_perf.h` — stamps out `<fn>_steps()` from
    three clearly separated concerns: `LENGTH` (history depth, algorithm),
    `BATCH` (SIMD width, parallelism), `CHUNK` (scratch-buffer fill, tuning).
    Eliminates hand-written outer dispatch loops.
- `sliding_correlator` example — demonstrates `JM_DEFINE_STEPS` is
    algorithm-agnostic using complex cross-correlation (`Σ conj(ref[k])·x[n-k]`).
    `step_batch()` is a compiler-vectorizable scalar loop; no explicit SIMD
    intrinsics required.
- `docs/perf.md` — reference guide covering `just-makeit perf`, all
    `jm_perf.h` macros, and `JM_DEFINE_STEPS` with generic + FIR examples.

### Changed

- `examples/fir_filter` Step 7: reworked to use `just-makeit perf` on the
    existing `my_fir` project instead of scaffolding a new `my_fir_perf` copy.
    The `step()` implementation from Step 2 is preserved — no copy-paste.
- `JM_DEFINE_STEPS` parameter renamed `taps` → `LENGTH` to reflect its
    generic meaning (history depth), separating it from the FIR-specific `TAPS`
    concept. FIR example now defines `FIR_TAPS = 16` and
    `FIR_LENGTH = FIR_TAPS - 1`.

______________________________________________________________________

## [0.1.2] — 2026-05-06

### Fixed

- `--basic` Makefile: numpy include path now resolved as a shell subcommand at recipe execution time, fixing `numpy/arrayobject.h: No such file or directory` when numpy is installed during the build
- `__version__` now derived from installed package metadata instead of a hardcoded string

### Docs

- `docs/index.md`: corrected stale `just-makeit init` references to `just-makeit new`

______________________________________________________________________

## [0.1.1] — 2026-05-06

### Added

- `--basic` flag on `just-makeit new` — plain `cc` + `sysconfig` build with no CMake or build directory; stored as `build = "make"` in `just-makeit.toml`; `just-makeit init` patches the Makefile automatically for additional components

### Fixed

- `--basic` Makefile: numpy auto-installed via pip before compile; `NP_INC`/`INC` use lazy `=` so the include path is resolved after installation
- `just-makeit new` done hint now correctly includes `cd <project> &&` prefix
- `make test` falls back to `python -m unittest discover` when pytest is not installed
- Generated `__init__.py` no longer contains an unrunnable doctest
- `examples/gain/`: `#pragma once` replaced with C99 include guards; Makefile synced with template fixes
- PyPI README: `examples/gain/` link changed to absolute GitHub URL

______________________________________________________________________

## [0.1.0] — 2026-05-05

### Added

- `just-makeit new <project>` — scaffold a complete project (CMakeLists.txt, Makefile, pyproject.toml, README, .gitignore, common headers)
- `just-makeit init <component>` — add a C extension component to an existing project
- `just-makeit add --state` — add state variables to an existing component
- `just-makeit build` — configure + build C, then package a wheel via just-buildit
- `just-makeit config` — show or edit project configuration
- Multi-component project support: each component gets its own `native/src/<comp>/CMakeLists.txt`; `just-makeit init` appends `add_subdirectory` to the top-level CMakeLists
- Generated code: C99 lifecycle pattern (create / step / steps / reset / destroy), getter/setter pairs, NumPy `steps()` binding
- pytest + CTest test generation covering create, step, steps, getters/setters, reset, context manager, and destroy
- pytest → `unittest discover` fallback in generated Makefile (no pytest required)
- numpy auto-install in generated Makefile (`pip install numpy` if missing before cmake)
- just-buildit PEP 517 backend wired in generated `pyproject.toml`
- C99 include guards throughout (no `#pragma once`)
