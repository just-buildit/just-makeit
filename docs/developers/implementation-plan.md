# Implementation plan — the road to "no TOML for common cases"

Status: **Phases 0–2 shipped; sacred/glue apply, `jm regenerate`, and
the `--impl` line-range form landed in v0.14.** This document is the
coordination point for the work the design docs sketch out: the
[decision tree](../decision-tree.md), the
[template gallery](../templates/index.md), and the
[bind design](bind-design.md). The interactive wizard was **cut** (retired
before shipping; the design doc lives in git history). Read the
design docs for the *why*; this one is the *what, in what order, how do
we know we're done*.

______________________________________________________________________

## North-star goal

A user who knows DSP can ship a Python C extension without ever
learning **(a)** the just-makeit TOML schema, **(b)** the CPython
binding ABI, or **(c)** the CMake details. They pick a preset, type
one CLI command, open `_core.c`, replace the `/* TODO */` markers, and
`jm build && jm test`. Everything else — the manifest, the binding,
the CMakeLists, the tests, the bench, the type stub — is generated and
stays in sync.

This goal does **not** retire TOML. The manifest stays as the on-disk
truth and `jm apply` keeps materialising from it under the sacred/glue
contract; advanced users who prefer hand-authoring TOML are
first-class. What changes is that the CLI becomes a *complete*
alternative — every common feature reachable through TOML is also
reachable through a flag, and the templates document the type allowlist
for each slot so neither path surprises.

______________________________________________________________________

## What stays

The load-bearing primitives nothing replaces:

- **Renderer** (`_render.py` + the templates under
    `src/just_makeit/templates/`). Already covers every shape we
    ship; both the TOML flow and `jm bind` feed it the same
    `context: dict[str, str]`.
- **Context builders** (`_context/_state.py`, `_methods.py`, `_step.py`,
    `_sample.py`, `_parse.py`). `make_state_ctx`, `make_methods_ctx`,
    `make_step_ctx`, `make_sample_ctx`, `make_properties_ctx`,
    `make_perf_ctx`. These are the canonical translation from "what
    the user wants" into "what the renderer needs."
- **Type registry** (`_types._CTYPE_META`, `_ARRAY_DTYPE`). The
    accepted-types vocabulary, now documented per slot in
    [`docs/types.md`](../types.md).
- **TOML as the manifest format.** `just-makeit.toml` is the
    durable description; `jm apply` materialises from it. Every flag
    added in this plan still round-trips through TOML.
- **Existing CLI verbs**: `new`, `object`, `module`, `method`,
    `property`, `function`, `add`, `perf`, `apply`, `regenerate`,
    `remove`, `build`, `test`, `bench`, `app`, `script`, `upgrade`.
    Behaviour is preserved for every flag they accept today.
- **The gallery shapes** as the canonical taxonomy of components.
    Every new CLI flag and every `jm bind` parse case routes through
    one of them.

______________________________________________________________________

## What goes

Things we are committing to retire from the user-facing surface. They
keep working in TOML for as long as the schema needs them; they stop
being the *recommended* path the moment their CLI counterpart ships.

- **TOML-only knobs without CLI parity**: `out_type`, `out_divisor`,
    `variable_output`, `result_fields`, `multi_output`, `init_params`,
    `create_impl` / `reset_impl` / `destroy_impl`, `extra_link_libs`
    *(per-component)*, `extra_include_dirs`, `extra_types`,
    `find_packages`, `pkg_modules`, `c_deps`, `result_fields`. Every
    one gets a flag in Phase 2.
- **`just-makeit.toml`-first explanations in onboarding docs.** The
    decision tree, template gallery, and quick reference become the
    landing pages; `docs/configuration.md` stays as a reference for
    advanced users but stops being a prerequisite for getting started.
- **`jm wizard` entirely.** Cut, not deferred. The preset pages plus
    single-shot CLI cover the same need without a parallel interactive
    surface to learn, test, and document.
- **Implicit fall-throughs.** When the user passes a type a slot
    doesn't accept, the CLI errors with the slot's full allowlist (and
    a link to the relevant gallery page). No more "silently compiled,
    failed at runtime" surprises.

______________________________________________________________________

## What changes

Refactors that aren't strictly additive:

- **Shared type-slot validator.** A new helper —
    `_types.validate_slot(slot: str, type_str: str) -> None` — used by
    every CLI handler and the bind parser. Single error format:
    `"--state field type 'const char *' is not legal here.   Allowed: float, double, int, ... See docs/types.md#state-variable-types."`
- **Presets become first-class.** Each gallery shape gets a top-level
    `--preset` flag on `jm object` (and `jm function` is its own verb).
    The existing `--no-step` / `--arg-type void` combos still work;
    the preset is a labelled bundle. Working presets: `processor`
    (default), `generator`, `consumer`, `reader`. `blockwise` is
    excluded — array return (`T[] -> T[]`) is unsupported and now
    errors cleanly. Variable-output is a capability flag, not a preset.
- **`jm bind` becomes a top-level workflow, not just a debug tool.**
    Bind goes from "parse for the processor shape" to "parse for every
    working shape, with `--check` running in CI on every example."

______________________________________________________________________

## Phases

### Phase 0 — fixes and conventions (shipped, v0.13.22)

Done. Closed seven bugs (gh-65, gh-66, gh-68, gh-69, gh-70, gh-71,
gh-72), added `extra_include_dirs` and `--out-param`, wrote the
[decision tree](../decision-tree.md). Establishes the type-slot
conventions the rest of the work consumes.

### Phase 1 — gallery and bind MVP (shipped)

Done:

- [Template gallery](../templates/index.md) — preset pages with
    Concrete-types tables (`Accepts | Rejects | Default`).
- [bind design](bind-design.md).
- Type-slot docs ([`docs/types.md`](../types.md)) covering all five
    slots with explicit allowlists.
- `jm bind` MVP for the processor shape — byte-identical round-trip.
- `running_stats` uses `jm bind` end-to-end.

### Phase 2 — CLI parity for every TOML field (shipped, v0.13.23)

Every common TOML-only knob got a flag. Each row was one PR.

| New flag                               | TOML it replaces                                         | Affects                                 |
| -------------------------------------- | -------------------------------------------------------- | --------------------------------------- |
| `--init-param name:T[:D]`              | `[[obj.init_params]]`                                    | `jm object`, `jm method`                |
| `--out-type T`                         | `out_type` (methods + functions)                         | `jm method`, `jm function`              |
| `--out-divisor N`                      | `out_divisor` (methods)                                  | `jm method`                             |
| `--variable-output` + `--max-out N`    | `variable_output = true`, sibling `_max_out` declaration | `jm object`, `jm method`                |
| `--multi-output T,T,...`               | `multi_output`                                           | `jm method`                             |
| `--result-field name:T` (repeatable)   | `result_fields = [...]`                                  | `jm method`, `jm function`              |
| `--impl SLOT::FN` / `--impl file::N:M` | `create_impl` / `reset_impl` / `destroy_impl` / `impl`   | `jm object`, `jm method`, `jm function` |
| `--find-package NAME`                  | `[project] find_packages`                                | `jm new`                                |
| `--pkg-module NAME`                    | `[project] pkg_modules`                                  | `jm new`                                |
| `--c-dep DIR`                          | `[project] c_deps`                                       | `jm new`                                |
| `--extra-include-dirs DIR`             | per-component / per-module `extra_include_dirs`          | `jm object`, `jm module`                |
| `--extra-types NAME,NAME`              | `[module] extra_types`                                   | `jm module`                             |

Also in this phase: the shared `validate_slot()` helper, applied to
every existing and new flag, with regression tests for every slot's
rejection cases.

Success bar: a one-page table in `docs/configuration.md` listing every
TOML field has a "Reachable via CLI" column, and every row is `✓`.

### Phase 3 — the presets, bind for all of them

Two threads run in parallel; each delivers one piece of the
"pick a shape, scaffold it" experience.

Thread 3a: **Presets.** (shipped — see the working presets below)

- [x] `--preset processor` (alias for current defaults; documented)
- [ ] `--preset blockwise` — **excluded.** Array return
    (`T[] -> T[]`) is unsupported; the CLI errors cleanly on it. Array
    *input* (`--arg-type "T[]"`) works.
- [x] `--preset generator` — `--arg-type void`; emits a `steps(n)`
    generator skeleton (void-arg defaults to a complex return).
- [x] `--preset consumer` — `--return-type void`; emits an accumulator
    skeleton.
- [x] `--preset reader` — `--no-step --init-param filepath:"const char *"`
    plus `read()` / `seek()` / `close()` methods registered with
    `open()`/`stat()` already wired.

Variable-output (event-emitter shape) is a capability, not a preset:
`--variable-output --max-out N` with repeatable `--result-field name:T`
on any output-producing preset.

**→ Current focus.**

Thread 3b: **Bind expansion.**

- [ ] Parse methods (`<comp>_<verb>(...)` declarations).
- [ ] Parse init_params (ctor params that aren't state fields).
- [ ] Parse output-param naming (`out` / `output` / `dst` / `dest`).
- [ ] Parse variable-output (`_max_out` sibling pairing).
- [ ] Parse opaque state (forward decl in header, definition in `.c`).
- [ ] `jm bind --check` runs in CI for every bundled example.

Thread 3c (Wizard) was **cut** in the v0.13.23 retrospective for
maintenance burden and the small finite pattern set. The gallery's
preset pages + CLI commands cover the same need without a parallel
interactive surface.

Success bar:

- Every preset, run with the documented CLI command, produces a
    project where `jm build && jm test` passes immediately.
- `jm bind --check` is green on every bundled example.

### Phase 4 — third-party importability, libclang fallback, docs reordering

Polish and proof. By the end of this phase the project's pitch
becomes "write your DSP in C; we make it Python."

- [ ] Add a bundled example that imports a real third-party header
    (a single-file DSP library, shimmed to follow the contract) via
    `jm bind`. Pick something small and well-known.
- [ ] libclang fallback for `jm bind` when regex parsing fails.
    Optional dependency (`pip install just-makeit[bind-robust]`).
- [ ] Re-landscape `docs/` so the decision tree and template gallery
    are the onboarding path. `docs/configuration.md` stays but moves
    to a reference section.
- [ ] Contract-versioning comment in `_core.h` (`// jm-bind:   contract-1`) so bind can warn cleanly on older shapes.

Success bar: a new user opens `docs/`, picks `--preset reader`, runs
the CLI directly, and ships a working Python extension in under five
minutes without opening any TOML file.

______________________________________________________________________

## Bind acceptance matrix

Living table. A row goes green when:

1. The preset's CLI flag works (`jm object NAME --preset X` produces
    the canonical scaffold).
1. `jm bind NAME` against the resulting `_core.h` produces an
    `_ext.c` byte-identical to the scaffolded one.
1. `jm test` passes against the bound binding.
1. `jm bind --check` is wired into the bundled example's CI.

| Preset      | Phase 1 (MVP)                            | Phase 3 (full) |
| ----------- | ---------------------------------------- | -------------- |
| `processor` | byte-identical ✅ (single + multi-field) | —              |
| `blockwise` | excluded (array return unsupported)      | excluded       |
| `generator` | n/a                                      | pending        |
| `consumer`  | n/a                                      | pending        |
| `reader`    | n/a                                      | pending        |
| `function`  | n/a                                      | pending        |

______________________________________________________________________

## Risks

- **Convention rigidity.** The contract `jm bind` depends on
    (`<comp>_state_t`, lifecycle trio naming, output-param naming)
    will feel limiting to users importing existing C. Mitigation:
    document an *escape hatch* — `--annotate FUNC:OUT:name` on
    `jm bind` to override naming heuristics per call without changing
    the source.
- **TOML-only features keep accruing.** Easy to add an experimental
    knob to TOML "just for now." Mitigation: every TOML key added
    after Phase 2 requires a parallel CLI flag in the same PR.

______________________________________________________________________

## Explicit non-goals

- **Replacing `jm apply` with bind.** Bind reads C; apply reads
    TOML. Both stay; they're two front-ends to one renderer.
- **Any interactive surface.** No wizard, no TUI, no GUI. The CLI
    plus the inspectable preset pages are the whole interface.
- **Cross-language bindings.** Rust / C++ wrappers stay future work.
    Bind producing CPython only.
- **Schema migrations beyond what `jm upgrade` already does.** No
    breaking changes to TOML in this arc.
- **Performance tuning.** The renderer's `<<...>>` substitution is
    already negligible; bind's regex pass is microseconds. Worry
    later.

______________________________________________________________________

## How to use this document

- **Reviewing a PR**: identify which phase + which bullet it lands.
    PR description should cite the phase number and the success-bar
    line it advances.
- **Adding a new TOML key**: don't. Open an issue describing the
    need; the response is either "do this with existing keys" or "add
    a parallel CLI flag in the same PR."
- **Adding a new preset**: it's an extension of Phase 3 thread 3a.
    Add a gallery page first (Concrete types table mandatory), then
    the CLI flag, then the bind parse rule.
- **Marking work done**: tick the relevant checkbox; update the bind
    acceptance matrix.

The matrix and the per-phase checklists are the canonical "are we
done" signals — not commit counts, not lines of code, not "the design
is complete." Done means user-visible behaviour matches the success
bar.
