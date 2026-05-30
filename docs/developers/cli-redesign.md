# CLI redesign — sacred files, CLI + TOML, no wizard, no impl lift

Status: **proposal**, written 2026-05-30 during the v0.13.23
retrospective; revised three times. Not yet implemented. Supersedes
the additive-CLI body-splicing model that `--impl` / `--replace` and
the wizard implied.

______________________________________________________________________

## Principles

1. **Files with user content are sacred.** Any file the renderer
    emits with a `/* TODO */` marker that the user is meant to
    fill in is sacred — `jm apply` never overwrites it once it
    exists. Pure-glue files (no user content) are jm-managed and
    freely regenerated.
1. **Every valid CLI invocation produces a scaffold that compiles
    and passes `jm build && jm test` on day one** — as long as the
    user hasn't started modifying files. Foot-guns are bugs.
1. **The templates are the product.** A curated set of inspectable,
    useable-as-is preset pages. Users either copy from a page by
    hand, run the CLI, or edit TOML directly.
1. **A component is a self-contained unit.** Spec fragment + source
    files. Copying a component means copying its fragment and its
    source dirs into another project; no central-manifest surgery.
1. **CLI and TOML are both first-class authoring paths.** CLI is
    recommended (presets, validators, errors) but TOML editing is
    fully supported. Gallery pages show both forms.
1. **Iteration is the user's deliberate act.** Spec changes are
    automatic; regenerating a file is not. To refresh a file, the
    user removes it. `git` is the preservation layer.
1. **The pattern set is finite.** If a new pattern is needed,
    maintainers add a preset.

______________________________________________________________________

## What changes

### Per-component TOML fragments are the default layout

Each component's spec lives in its own fragment file. The central
`just-makeit.toml` shrinks to project-level config and module-level
collation.

```
my_dsp/
├── just-makeit.toml         # [project] only (+ module collation)
├── objects/
│   ├── my_filter.toml       # standalone object spec
│   └── my_nco.toml          # standalone object spec
├── modules/
│   ├── io.toml              # module config + functions
│   └── filter.toml          # module config + object roster
├── native/
│   ├── inc/my_filter/...    # owned by my_filter component
│   └── src/my_filter/...
└── src/my_dsp/
    └── my_filter.pyi
```

A component is the union of `objects/NAME.toml` (or
`modules/NAME.toml`) plus its `native/inc/NAME/`, `native/src/NAME/`,
and `src/<pkg>/NAME.pyi`. **Copying a component = copying the
fragment + the source dirs into another project, then `jm apply`.**

`jm apply` reads `just-makeit.toml`, then every fragment under
`objects/` and `modules/`, materializing the union. The order of
fragments doesn't matter; the renderer ingests them all before
emitting files.

The existing `jm split-objects` verb retires (no longer needed; the
fragment layout is the default). `jm migrate-to-fragments` converts
legacy central-TOML projects on first command.

### Authoring vs materialization

Two distinct phases, each with two valid paths:

| Phase           | Path A (CLI)                               | Path B (TOML)           |
| --------------- | ------------------------------------------ | ----------------------- |
| Author spec     | `jm object NAME --state ...`               | Edit `just-makeit.toml` |
| Materialize     | `jm apply` (auto-run after CLI verbs)      | `jm apply`              |
| Detect drift    | `jm status`                                | `jm status`             |
| Regenerate file | `rm file && jm apply` (or `jm regenerate`) | `rm file && jm apply`   |

The CLI is the recommended authoring path because it does validation,
emits helpful errors, applies presets, and writes idiomatic TOML.
The TOML path is supported because some users prefer hand-authoring
or need a knob the CLI hasn't yet exposed.

### `jm apply` honours sacred files, regenerates glue

Two classes of files:

**Sacred** — files the user fills in. The renderer creates them with
`/* TODO */` markers; `jm apply` never overwrites them once they
exist. To refresh, the user `rm`s the file (and `git stash`es their
edits first).

- `native/src/<obj>/<obj>_core.c` (per object: step + lifecycle bodies)
- `native/src/<mod>/<fn>.c` (one per function in a module — see
    [Per-function source files](#per-function-source-files-modules))
- Anything else with a `/* TODO */` marker emitted on first creation

**Glue** — pure boilerplate, no user content. `jm apply` regenerates
freely whenever TOML changes; no user work to lose.

- `native/inc/<comp>/<comp>_core.h` (declarations only)
- `native/src/<comp>/<comp>_ext.c` (Python binding)
- `native/tests/test_<comp>_core.c` (CTest smoke test)
- `src/<pkg>/<comp>.pyi` (type stub)
- `CMakeLists.txt` fragments (build targets)

The distinction is by content, not by name. A file is sacred if
losing it would lose user work.

### Per-function source files (modules)

A module with multiple functions doesn't pile them into one
`<mod>_core.c`. Each function gets its own sacred source file:

```
native/src/io/
├── q15_to_float.c         # sacred — body for q15_to_float
├── magnitude_db.c         # sacred — body for magnitude_db
├── io_core.h              # glue — declarations of every function
└── io_ext.c               # glue — Python module binding
```

Adding a new function:

1. `jm function magnitude_db --module io ...` updates `modules/io.toml`
    and runs `jm apply`.
1. `jm apply` notices the new function in TOML.
1. It writes a fresh `native/src/io/magnitude_db.c` (didn't exist
    before — created).
1. It regenerates `io_core.h` and `io_ext.c` (glue — TOML drives them
    entirely).
1. Existing sacred files (`q15_to_float.c` and any prior function
    `.c`s) are untouched.

No `rm` step needed. The sacred-file rule still holds: every file
with user content is preserved.

The same pattern applies to objects living inside a module
(`--module filter`): each object has its own sacred `_core.c`; the
module's `_ext.c` and `_core.h` are glue.

### `jm status` reports drift

```sh
jm status
```

Reads TOML and compares to disk. Reports per file:

```
my_filter
  _core.h     up to date
  _core.c     stale  (TOML says state.cutoff exists; file doesn't carry it)
  _ext.c      stale  (TOML says method `detect` exists; file doesn't expose it)
  test_*.c    up to date
```

The user chooses what to refresh. `jm status --diff` shows
hypothetical contents for the stale files.

### `jm regenerate <component>` convenience

```sh
jm regenerate my_filter
```

Equivalent to `rm` of every file `my_filter` owns + `jm apply`.
Interactive by default (prompts before each delete); `--force` skips
prompts. Always advise `git stash` first.

### CLI verbs are additive — and safe

Additive verbs (`jm method`, `jm property`, `jm add --state`) update
TOML and run `jm apply`. Under the sacred/glue split, this is
seamless:

- **New components/methods/functions** → new sacred files created,
    glue regenerated, no conflict.
- **Existing components** → their sacred `_core.c` files stay
    untouched (no signature change forced on user-written bodies);
    glue files refresh so the new method/property is wired up.

A user who runs `jm method NAME foo --return-type T` on an existing
object sees:

```
TOML updated: [[NAME.methods]] foo
jm apply: regenerated 3 glue files (_core.h, _ext.c, .pyi)
          NAME_core.c is sacred — add the foo() body when ready
```

The user adds the body to `NAME_core.c` at their leisure. Until they
do, the header declares `NAME_foo()` but no implementation links —
a clean linker error, not a silent runtime bug.

### No `--impl`, no `--replace`

Both were footguns. `--impl SLOT::file::funcname` concatenated lifted
bodies with default bodies, producing broken scaffolds when
signatures didn't match. `--replace old::new` patched lifted bodies
inline, compounding the foot-gun.

Bodies live in `_core.c`. Users edit them in place. If they want to
reuse code from another file, they `cp` or `git cherry-pick` —
external machinery beats in-CLI lifting.

### No wizard

Cut in the previous round. Maintenance burden, hidden surface, the
pattern set is too small. Documented in
[`wizard-design.md`](wizard-design.md) (retired).

### What goes away

- `--impl SLOT::file::funcname` (and `--impl file::funcname`)
- `--replace old::new`
- `_impl.py` module
- `jm wizard` (already cut)
- `jm split-objects` — fragments are the default; no opt-in needed.

### What comes back / stays

- `jm new <project>` — fresh project. No conflict possible.
- `jm object NAME …` — single-shot creation when fresh; updates TOML
    - runs `jm apply` (which skips existing files) when extending.
- `jm function FN --module MOD …` — same.
- `jm method` / `jm property` / `jm add` — additive verbs that mutate
    TOML and run `jm apply`. Safe because of sacred-files.
- `jm apply` — promoted to first-class verb. Creates missing files.
- `jm status` — **new.** Reports drift between TOML and files.
- `jm regenerate <component>` — **new.** `rm` + `jm apply` with
    confirmation.
- `jm remove <kind> <name>` — keeps shrinking the TOML; user removes
    files manually (or via `jm regenerate`).
- `jm perf` — project-wide retrofit. Operates on TOML; subsequent
    `jm apply` honours it. Sacred files mean if `_core.c` exists, the
    `JM_HOT` annotation in `_ext.c` still goes into effect on a fresh
    `_ext.c` (delete to refresh).
- `jm build` / `jm test` / `jm bench` / `jm dry-run` — read-only.
- `jm bind` — header-driven; orthogonal.
- `jm config` — project-level config (version, build system, etc.).
- `jm script` — emit the CLI history that built the project.

### What lives on the CLI

The CLI handles the **simple / common case**: presets, basic
single-component scaffolding, top-level customization, and small-count
repeatables. Anything with non-trivial repetition (multi-method
components, many properties, complex result-field combinations) goes
to TOML — repeating CLI flags is awful UX and no one does it.

`jm object` accepts:

| Flag                                                 | Repeatable                 | Notes                                |
| ---------------------------------------------------- | -------------------------- | ------------------------------------ |
| `--preset NAME`                                      | no                         | expands to a known-good flag bundle. |
| `--state name:type[:default]`                        | yes (small count expected) | (already on `jm object`)             |
| `--init-param name:type[:default]`                   | yes (small count expected) | (already on `jm object`)             |
| `--arg-type T` / `--return-type T`                   | no                         | (already on `jm object`)             |
| `--no-step` / `--no-state` / `--mutable`             | no                         | (already on `jm object`)             |
| `--perf`                                             | no                         | (already on `jm object`)             |
| `--extra-include-dirs DIR` / `--extra-link-libs LIB` | yes                        | (already on `jm object`)             |
| `--class-name NAME`                                  | no                         | (already on `jm object`)             |

**Explicitly NOT added to `jm object` as repeatable flags:**

- `--method name:return=T:arg=T:param=...` — the grammar-heavy
    multi-attribute repeatable. Reaches "no one wants to type this"
    fast. Use `jm method` for one-off additions; use TOML for
    multi-method components.
- `--property name:type[:writable]` — same logic.
- `--variable-output` / `--max-out N` / `--result-field name:T` —
    only make sense in the scope of a specific method, so they live
    on `jm method` (per-call), not on `jm object`.

### Standalone additive verbs (one-off use)

For adding a single thing to an existing component:

- `jm method NAME verb …` — adds one method.
- `jm property NAME field …` — adds one property.
- `jm add --state name:type[:default]` — adds one state field.

These mutate the component's fragment TOML and run `jm apply`. Safe
under the sacred-files rule (glue regenerates; user code untouched).

**For multiple methods/properties at once: edit the TOML fragment
directly.** That's what TOML is for. The CLI doesn't try to be
a TOML editor; the TOML doesn't try to be a CLI surface.

______________________________________________________________________

## Migration

### 0.13 → 0.14 deprecations

In `0.14.0`:

- `--impl SLOT::file::funcname` (and the older `--impl file::funcname`)
    and `--replace old::new` emit a deprecation warning. The renderer
    stops splicing; impl bodies are no-ops. Users edit `_core.c`
    directly.
- `jm apply`'s skip-existing behaviour becomes the only mode.
- `jm status` ships.

In `0.15.0`:

- `--impl` and `--replace` flags are removed.
- `_impl.py` is deleted.

### `jm script` emits the simplified form

After `jm script` in 0.14+: the emitted shell script uses only
single-shot CLI invocations (no `--impl`, no `--replace`). Existing
projects that used those flags get a migration note.

______________________________________________________________________

## Known foot-guns to fix in 0.14

Verified by spot-checking each preset in a clean temp dir on
0.13.23 (2026-05-30):

1. **`_ext.c` codegen mismatch for `--return-type void` and
    `--no-step` objects.** Generated `_ext.c` declares
    `NAME_destroy(NAMEObject *self, PyObject *)` (2 args) but calls
    `NAME_destroy(self->handle)` (1 arg). Build fails for the
    `consumer` and `reader` presets. Root cause in
    `_ext.c`'s `tp_dealloc` template; doesn't get triggered for
    standard (return-type-non-void, has-step) objects.
1. **Array `arg_type` / `return_type` not in `_CTYPE_META`.** The
    renderer's `make_sample_ctx(arg_type, return_type)` does
    `_CTYPE_META[return_type]` directly, so `"float _Complex[]"`
    throws `KeyError`. The `blockwise` preset is unreachable via
    CLI *or* hand-authored TOML. Fix lands with `--preset blockwise`
    in Phase 3a.
1. **`--no-step` not accepted by `jm new --object`.** Forces the
    reader preset to a two-step workflow (`jm new` then
    `jm object NAME --no-step ...`). Phase 3a's `--preset reader`
    consolidates this into one command.
1. **`--impl SLOT::file::funcname` body splicing.** Renderer
    concatenates default body + lifted body without signature
    validation, producing broken scaffolds when the lifted body's
    args don't match the spec-driven signature. Fix is removal
    (see "What goes away").
1. **Follow-up `jm property` / `jm method` regenerate header with
    stale ctor signature.** When a component has both `--state` and
    `--init-param`, the initial scaffold honours `--init-param`
    correctly (gh-69 fix in 0.13.22). But subsequent additive
    commands regenerate `_core.h` using state-driven ctor params,
    diverging from the (sacred) `_core.c` ctor signature. Build
    breaks. Fix is the sacred-files split: header is glue and
    regenerates fully from current TOML on every `jm apply` — the
    drift the bug exploits goes away.

______________________________________________________________________

## Open questions

1. **`jm bind` interaction.** Bind reads a hand-written `_core.h` and
    synthesises `_ext.c`. Under sacred-files, bind's effect on
    existing `_ext.c` is: never. Document bind as a "fresh project"
    tool, or extend it to also respect sacred-files.
1. **Auto-run `jm apply`?** Should `jm object NAME ...` automatically
    run `jm apply` afterward, or require a separate step? Pros of
    auto: matches today's "one command = one scaffold" UX. Cons:
    hides the materialization step, which is now first-class.
1. **`jm regenerate` UX.** Should it default to interactive
    confirmation per-file, or a `--list` preview then a single
    confirm? Interactive feels safer but is friction-heavy.

(The previously open `--method` syntax / method-scoped sub-flag
scoping questions are gone: multi-method components are authored
in TOML; the CLI doesn't grow grammar-heavy repeatable flags.)

______________________________________________________________________

## Acceptance bar

The redesign ships when:

- [ ] `jm apply` skips every existing file; no flag overrides this.
- [ ] `jm status` reports drift accurately for every preset.
- [ ] Every preset page in `docs/templates/` shows three columns —
    CLI command, TOML written, files created. All three round-trip.
- [ ] `--impl` / `--replace` are gone; the in-flight foot-gun
    (concatenated default + lifted body) can no longer occur.
- [ ] Migration: `jm script` output uses only single-shot CLI; old
    scripts get a deprecation note pointing at this doc.

______________________________________________________________________

## What this means for the gallery

Each preset page in `docs/templates/` becomes:

1. **Specialization** — the flag bundle (named preset).
1. **Command** — the CLI invocation.
1. **TOML written** — the manifest fragment the CLI produces. Users
    who prefer to hand-author can paste this directly into
    `just-makeit.toml` and run `jm apply`.
1. **Files created** — every file the renderer writes, with content.
    Inspectable, copyable, paste-able by hand.
1. **What you fill in** — the `/* TODO */` markers in `_core.c`.
1. **Extending** — repeat flags on the initial command for richer
    scaffolds, OR run additive verbs (with a clear "files are sacred"
    note explaining that follow-up changes need `rm` to take effect).
1. **Python usage** — `import` + call sites.
1. **Concrete types** — the allowlist per slot.

Removing `--impl` and showing the TOML fragment on every page are the
two changes that bring the existing gallery pages into line with this
redesign.
