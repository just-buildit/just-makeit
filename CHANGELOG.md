# Changelog

## [Unreleased]

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
