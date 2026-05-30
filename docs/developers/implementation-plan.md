# Implementation plan — the road to "no TOML for common cases"

> **Partially superseded by [`cli-redesign.md`](cli-redesign.md).**
> Phases 0-2 (shipped) are as documented. Phase 3 is being reshaped
> around single-shot CLI + immutable objects + finite preset set
> (wizard cut). Phase 4 unchanged. The "What stays / what goes" lists
> and "north-star" prose below predate the redesign and need a
> rewrite; the per-phase checklists below the supersession line
> further down are still the canonical task tracker.

Status: **plan locked, partial implementation in flight on
`feat/jm-bind-mvp`.** This document is the single coordination point
for the work the four design docs sketch out: the
[decision tree](../decision-tree.md), the
[template gallery](../templates/index.md), the
[CLI redesign](cli-redesign.md), and the
[bind design](bind-design.md). Read those four for the *why*; this one
is the *what, in what order, how do we know we're done*.

______________________________________________________________________

## North-star goal

A user who knows DSP can ship a Python C extension without ever
learning **(a)** the just-makeit TOML schema, **(b)** the CPython
binding ABI, or **(c)** the CMake details. They pick a template, type
one CLI command (or run the wizard), open `_core.c`, replace the
`/* TODO */` markers, and `jm build && jm test`. Everything else — the
manifest, the binding, the CMakeLists, the tests, the bench, the
Python wrapper — is generated and stays in sync.

This goal does **not** retire TOML. The CLI and TOML are both
first-class authoring paths. The CLI is recommended (presets,
validators, helpful errors); TOML editing is fully supported for
power users. The CLI becomes a *complete* alternative — every feature
reachable through TOML is also reachable through a flag — but TOML
doesn't get hidden in onboarding either. Per the CLI redesign
([`cli-redesign.md`](cli-redesign.md)), components are stored as
per-component TOML fragments under `objects/` and `modules/`; the
central `just-makeit.toml` carries only project-level config.

______________________________________________________________________

## What stays

The load-bearing primitives nothing replaces:

- **Renderer** (`_render.py` + the 49 templates under
    `src/just_makeit/templates/`). Already covers every shape we
    ship; both the TOML flow and `jm bind` feed it the same
    `context: dict[str, str]`.
- **Context builders** (`_context/_state.py`, `_methods.py`,
    `_step.py`, `_sample.py`, `_parse.py`).
- **Type registry** (`_types._CTYPE_META`, `_ARRAY_DTYPE`). The
    accepted-types vocabulary, documented per slot in
    [`docs/types.md`](../types.md).
- **TOML as the manifest format**, now as per-component fragments
    (`objects/NAME.toml`, `modules/NAME.toml`) plus the central
    `just-makeit.toml` for project + module collation. Every CLI flag
    still round-trips through TOML.
- **Project-level verbs**: `new`, `build`, `test`, `bench`,
    `dry-run`, `app`, `script`, `config`, `upgrade`, `apply`, `perf`.
- **Per-component verbs**: `object`, `module`, `function`, `method`,
    `property`, `add`, `remove`. **Now safe under the sacred-files
    rule** — additive operations regenerate glue and create new sacred
    files; existing sacred files (with user `/* TODO */` code) are
    never overwritten.
- **The six gallery presets** as the canonical taxonomy of object
    shapes (processor, blockwise, generator, consumer, reader) plus
    `jm function` as its own generalist verb.

______________________________________________________________________

## What goes

Things we are committing to retire because they're foot-guns or
unnecessary:

- **`--impl SLOT::file::funcname` (and `--impl file::funcname`).**
    The lift mechanism silently produces broken scaffolds when
    signatures don't match (the renderer concatenates default body +
    lifted body). Bodies live in `_core.c`; users edit them in place.
    Deprecation in 0.14, removal in 0.15.
- **`--replace old::new`.** Co-conspirator with `--impl`; same
    fate.
- **`jm wizard`** — never shipped; cut for maintenance burden and
    the small finite pattern set.
- **`jm split-objects`** — per-component fragments become the default
    layout; opt-in retires.
- **The legacy `_impl.py` module** — gone with `--impl`.
- **Implicit fall-throughs.** When the user passes a type a slot
    doesn't accept, the CLI errors with the slot's full allowlist (and
    a link to the relevant gallery page).

______________________________________________________________________

## What changes

Refactors that aren't strictly additive:

- **Sacred-files rule + per-function source files in modules.**
    `jm apply` never overwrites a sacred file (one with user
    `/* TODO */` content). Each function in a module gets its own
    sacred `.c` file so growing a module never touches existing
    sacred files. Module-level glue (`_ext.c`, `_core.h`, `.pyi`,
    test) regenerates freely.
- **Shared type-slot validator.** A new helper —
    `_types.validate_slot(slot: str, type_str: str) -> None` — used
    by every CLI handler and the bind parser. Single error format.
- **Presets become first-class.** Each gallery shape gets a top-level
    `--preset` flag on `jm object` (and `jm function` stays its own
    verb). The existing `--no-step` / `--arg-type void` combos still
    work; the preset is a labelled bundle expanding to a flag
    combination. Variable-output is a capability flag, not a preset.
- **`jm bind` becomes a top-level workflow, not just a debug tool.**
    Bind goes from "parse for the processor shape" to "parse for all
    five object shapes, with `--check` running in CI on every example."
- **`jm status` ships.** New verb: reports drift between TOML
    fragments and the materialised files.

______________________________________________________________________

## Phases

### Phase 0 — fixes and conventions (shipped, v0.13.22)

Done. Closed seven bugs (gh-65, gh-66, gh-68, gh-69, gh-70, gh-71,
gh-72), added `extra_include_dirs` and `--out-param`, wrote the
[decision tree](../decision-tree.md). Establishes the type-slot
conventions the rest of the work consumes.

### Phase 1 — gallery and bind MVP (in flight, `feat/jm-bind-mvp`)

What's in:

- [Template gallery](../templates/index.md) — six preset pages with
    Concrete-types tables (`Accepts | Rejects | Default`).
- [Wizard design](wizard-design.md) and
    [bind design](bind-design.md).
- Type-slot docs ([`docs/types.md`](../types.md)) covering all five
    slots with explicit allowlists.
- `jm bind` MVP for the processor shape — byte-identical round-trip;
    1619 tests pass.

What's left in this phase:

- [ ] Open `feat/jm-bind-mvp` as the Phase-1 PR.
- [ ] Get one bundled example using `jm bind` end-to-end (hand-write
    `<comp>_core.h` / `<comp>_core.c`, then `jm bind` to materialise
    the binding). `running_stats` is the obvious target — small,
    one-state-field, fits the processor shape.

Success bar: PR merged, the processor row of the bind acceptance
table below is green.

### Phase 2 — CLI parity for every TOML field

The biggest behaviour change. Every TOML-only knob gets a flag. Each
bullet is one PR; the order is cheapest first.

| New flag                             | TOML it replaces                                         | Affects                                 |
| ------------------------------------ | -------------------------------------------------------- | --------------------------------------- |
| `--init-param name:T[:D]`            | `[[obj.init_params]]`                                    | `jm object`, `jm method`                |
| `--out-type T`                       | `out_type` (methods + functions)                         | `jm method`, `jm function`              |
| `--out-divisor N`                    | `out_divisor` (methods)                                  | `jm method`                             |
| `--variable-output` + `--max-out N`  | `variable_output = true`, sibling `_max_out` declaration | `jm object`, `jm method`                |
| `--multi-output T,T,...`             | `multi_output`                                           | `jm method`                             |
| `--result-field name:T` (repeatable) | `result_fields = [...]`                                  | `jm method`, `jm function`              |
| `--impl SLOT::FN`                    | `create_impl` / `reset_impl` / `destroy_impl` / `impl`   | `jm object`, `jm method`, `jm function` |
| `--find-package NAME`                | `[project] find_packages`                                | `jm new`                                |
| `--pkg-module NAME`                  | `[project] pkg_modules`                                  | `jm new`                                |
| `--c-dep DIR`                        | `[project] c_deps`                                       | `jm new`                                |
| `--extra-include-dirs DIR`           | per-component / per-module `extra_include_dirs`          | `jm object`, `jm module`                |
| `--extra-types NAME,NAME`            | `[module] extra_types`                                   | `jm module`                             |

Also in this phase: the shared `validate_slot()` helper, applied to
every existing and new flag, with regression tests for every slot's
rejection cases.

Success bar: a one-page table in `docs/configuration.md` listing every
TOML field has a "Reachable via CLI" column, and every row is `✓`.

### Phase 3 — single-shot CLI redesign + presets + bind expansion

Three threads. The CLI redesign ([`cli-redesign.md`](cli-redesign.md))
is load-bearing — until it lands, the gallery's "one command =
one component" promise can't be kept. The wizard thread originally
planned for 3c is **cut**; see [`wizard-design.md`](wizard-design.md)
for the retirement note.

Thread 3a: **Presets — named flag combinations + a shape-aware render.**

Two work items, both driven by CLI state. No separate per-preset
templates.

**(1) `--preset NAME` flag expansion.** Add a small lookup in
`_cli_object.py` (~20 lines) that maps each preset name to its flag
combination and expands it before the normal arg parser runs:

| Preset      | Expands to                                                                                         |
| ----------- | -------------------------------------------------------------------------------------------------- |
| `processor` | (no-op — default)                                                                                  |
| `blockwise` | `--arg-type "T[]" --return-type "T[]"`                                                             |
| `generator` | `--arg-type void`                                                                                  |
| `consumer`  | `--return-type void`                                                                               |
| `reader`    | `--no-step --init-param filepath:"const char *"` (plus follow-up `jm method NAME read/seek/close`) |

`--preset NAME` is shorthand; passing the underlying flags directly is
always equivalent.

**(2) Shape-aware body rendering.** Today the generalist body
template is shape-agnostic — the same `step()` stub regardless of
arg/return types. Make it shape-aware so each customization produces a
clean, idiomatic body:

- `--arg-type T[]` + `--return-type T[]` → block-loop body.
- `--arg-type void` → generator body shape.
- `--return-type void` → accumulator body shape.
- `--no-step` → skip the body entirely; let custom methods drive.

All shape selection happens in the existing render pipeline — same
generalist template, branches on flag state. No `templates/c/src/presets/`
directory, no parallel renderer.

Variable-output (event-emitter shape) is a capability, not a preset:
`--variable-output --max-out N` with repeatable `--result-field name:T`
on any output-producing preset.

Thread 3b: **Bind expansion.**

- [ ] Parse methods (`<comp>_<verb>(...)` declarations).
- [ ] Parse init_params (ctor params that aren't state fields).
- [ ] Parse output-param naming (`out` / `output` / `dst` / `dest`).
- [ ] Parse variable-output (`_max_out` sibling pairing).
- [ ] Parse opaque state (forward decl in header, definition in `.c`).
- [ ] `jm bind --check` runs in CI for every bundled example.

Thread 3c (formerly **Wizard**): **CUT.** See
[`wizard-design.md`](wizard-design.md) for the retirement note. The
pattern set is small enough that "look at a gallery page, run the
matching command" is the whole experience. An interactive wizard
would have added a parallel surface to maintain.

Thread 3c is replaced by **CLI redesign work** tracked separately in
[`cli-redesign.md`](cli-redesign.md): single-shot creation,
immutability, `--replace` overwrite, removal of `jm add` /
`jm method` / `jm property` / `jm remove <kind>`.

Success bar (Phase 3 overall):

- Every preset page in `docs/templates/` produces a green scaffold
    via a single CLI command — no follow-up `jm method` etc.
- `--preset NAME` flag ships on `jm object` and matches every preset
    page's flag combination.
- `jm bind --check` is green on every bundled example.
- The shape-aware body render produces idiomatic TODO bodies for each
    shape, all of them no-ops by default (no example math that lurks).

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
`jm wizard` *or* the CLI directly, and ships a working Python
extension in under five minutes without opening any TOML file.

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
| `blockwise` | n/a                                      | pending        |
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
- **Wizard becomes a different language.** If the wizard accumulates
    enough conditional follow-ups, the prompt tree itself becomes a
    new thing to learn. Mitigation: cap each preset's question count
    in the design — six per preset, no more.
- **TOML-only features keep accruing.** Easy to add an experimental
    knob to TOML "just for now." Mitigation: every TOML key added
    after Phase 2 requires a parallel CLI flag in the same PR.
- **Phase 2 surface area.** Twelve new flags across three commands.
    Mitigation: each flag is one PR, each PR has its regression test
    - decision-tree update + gallery-page update.

______________________________________________________________________

## Explicit non-goals

- **Replacing `jm apply` with bind.** Bind reads C; apply reads
    TOML. Both stay; they're two front-ends to one renderer.
- **A graphical UI.** Wizard is terminal-interactive; that's the only
    interactive surface this plan adds.
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
    the CLI flag, then the bind parse rule, then the wizard prompt.
- **Marking work done**: tick the relevant checkbox; update the bind
    acceptance matrix.

The matrix and the per-phase checklists are the canonical "are we
done" signals — not commit counts, not lines of code, not "the design
is complete." Done means user-visible behaviour matches the success
bar.
