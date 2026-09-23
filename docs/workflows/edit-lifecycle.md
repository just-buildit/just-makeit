# The edit lifecycle: author → apply/regenerate → implement → test → iterate

`just-makeit.toml` is the manifest. Every CLI verb (`object`, `method`,
`add`, …) writes to it, then materializes files. You can also edit the TOML by
hand. Either way, `jm apply` renders the whole project from the manifest into
a scratch tree and reconciles it into yours — and what it does to each file
depends on who owns it. That is the **sacred/glue contract**.

## Who owns each file

| Kind               | What `apply` does                                                                                                                                                            | Examples                                                                                                                                                              |
| ------------------ | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **Yours** (sacred) | Creates it once. After that it only **adds** what the manifest declares and the file lacks — a declaration in `_core.h`, a stub in `_core.c`. Never rewrites what you wrote. | `<comp>_core.h` (the state struct and inline `step()`), `<comp>_core.c`, your C and Python tests and benchmarks, `objects/<comp>.toml`, `pyproject.toml`, `README.md` |
| **jm's** (glue)    | Rewrites it from the manifest on every run; `status --check` fails when it drifts. Never hand-edit it.                                                                       | `<comp>_ext.c`, `src/<pkg>/<comp>.pyi`, `native/inc/<pkg>.h`, a component's `CMakeLists.txt`                                                                          |
| **Shared**         | Splices jm's marked blocks in and keeps everything else.                                                                                                                     | the root `CMakeLists.txt`, `src/<pkg>/__init__.py`, a module object's `<mod>_ext_<obj>.c`                                                                             |
| **Versioned**      | jm's content, but written once. A newer jm ships a newer one, which `jm status` reports as OUTDATED; you adopt it deliberately ([Upgrading](../upgrading.md)).               | `Makefile`, `CMakePresets.json`, `clib_common.h`, `jm_test.h`, `jm_bench.h`, `bootstrap.toml`, `.gitignore`                                                           |
| **Derived**        | Rewritten, but from *your* files rather than the manifest.                                                                                                                   | `native/tests/test_<comp>_symbols.c` — links every function the binding calls, so a declared-but-undefined one fails `make test` by name                              |

The full tree, with every file tagged: [Project layout](layout-and-api.md#project-layout-full).

Four of these have more to them:

- **The root `CMakeLists.txt`** keeps your targets, so a later jm's fix to
    the part of the template outside its marked blocks never reaches it.
    `jm status` names each one it lacks under `ROOT CMAKE`, with what breaks
    without it, and `jm status --diff` shows the file against today's render
    to merge from.
- **A component's `CMakeLists.txt`** is regenerated, but a rule the manifest
    cannot express survives: an extra source in `<comp>_core`'s
    `add_library` or a per-source property leaves the file untouched, and an
    `if(VAR) … endif()` block is carried across. Anything else goes in
    `<dir>_extra.cmake` beside it, which the file includes and jm never
    writes.
- **`<comp>_core.c`** is never rewritten by `apply`. A *structural* change
    rebuilds it with `jm regenerate`, which lifts your hand-written bodies out
    and splices them back in by function name (`--discard` for a clean reset
    instead).
- **A module object's `<mod>_ext_<obj>.c`** is shared so it can hold
    hand-written bindings — which also means a later jm's fix to a wrapper
    never reaches it. `fragment = "generated"` makes it jm's, and
    `jm adopt <obj>` makes that switch only when nothing of yours would be
    lost ([Who owns a module's binding fragment](../configuration.md#who-owns-a-modules-binding-fragment)).
- **The scaffolded Python test** `src/<pkg>/tests/test_<comp>.py` is born
    **jm's**: its first line is `# jm:generated test_<comp>.py`, and while
    that line is there `apply` rewrites the file, so a constructor that
    gains a parameter reaches the test instead of leaving it calling the old
    signature. Delete the line and the file is yours: jm never writes it
    again — so delete it *before* adding a test of your own, or `apply`
    replaces the file (`status --check` shows the difference first). A
    project scaffolded before this has no such line, and its tests stay
    yours.

## The loop

The additive verbs never touch an existing body in place — they only inject
what's missing:

- `jm method`, computed `jm property`, and `jm function` are **additive** —
    they inject one declaration into `_core.h` and append a fresh stub to
    `_core.c`. Existing bodies are never touched. A field-backed
    `jm property --field` injects one struct member directly.
- `jm add` (adding state) is **structural** — it writes `[[obj.state]]` to the
    manifest, then rebuilds the object via the regenerate path with a clean
    reset (`--discard`), so it does discard hand-written `_core.c` bodies and
    the inline `step()` body in `_core.h` (see below) — same for
    `jm remove --state`.
- `jm apply` injects any TOML-declared declaration missing from `_core.h` and
    keeps the struct + `step()` sacred. A state-field change or a signature
    change is structural → `jm regenerate`.

So the flow is:

1. **Author** — run a CLI verb, or hand-edit `just-makeit.toml`.
1. **Apply / regenerate** — `jm apply` refreshes the glue and injects missing
    declarations; a structural change (new state field, changed signature)
    needs `jm regenerate` to rebuild the object.
1. **Implement** — fill in `step()` in `_core.h`, and a new method's body
    in `_core.c`.
1. **Test** — `make test`.
1. **Iterate** — back to step 1.

What you own is the manifest, your kernels (`step()` in the header,
everything else in `_core.c`) and your tests; the C↔Python seam is jm's.

When you change a *signature* in TOML (an arg type, a method's return type),
or add a state field, the structure of the object changed — rebuild it from
the manifest with `jm regenerate`:

```sh
git stash                    # safety net — see below
just-makeit regenerate gain  # deletes every file 'gain' owns, re-runs apply
```

`regenerate` deletes every file the component owns and rebuilds it from the
manifest, then asks for a single confirmation (`--force` skips it). Unlike
`jm remove`, it leaves the manifest untouched — it is the deliberate-rebuild
half of the contract. By default it lifts your hand-written `_core.c`/
`_core.h` bodies (create/destroy/reset/`step()`/getters/setters/methods) out
before deleting the files, and splices them back into the freshly generated
ones by function name — `--discard` skips that and does a clean reset
instead. The lift/splice is best-effort (a changed signature, e.g. a new
parameter, means the fresh body wins instead), so `git stash` first is still
good practice, not a requirement. Works for standalone and module objects.

## Lifting an existing C body with `--impl`

When the algorithm already exists in another `.c` file, `--impl` lifts it into
the generated stub instead of having you paste it:

```sh
just-makeit object gain --arg-type float --return-type float \
    --state gain:float:1.0 \
    --impl legacy/dsp.c::apply_gain
```

`--impl file::funcname` injects the body of `funcname`. `--impl file::N:M`
lifts source lines `N..M` (inclusive, 1-based) instead — useful when there is
no clean function to name; out-of-bounds or inverted ranges error cleanly.
`--replace old::new` applies string substitutions before injection (e.g.
renaming a struct field). The same keys exist in TOML: `impl`, `impl_file`
(`"path::funcname"` or `"path::N:M"`), `create_impl`, `reset_impl`,
`destroy_impl`. Because `_core.c` is sacred, lifting is safe — apply never
clobbers what you injected.
