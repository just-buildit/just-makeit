# The edit lifecycle: author → apply/regenerate → implement → test → iterate

`just-makeit.toml` is the manifest. Every CLI verb (`object`, `method`,
`add`, …) writes to it, then materializes files. You can also edit the TOML by
hand. The verbs treat your files differently — this is the
**sacred/glue contract**:

| File                   | Class                                                                                                                                                                                                      |
| ---------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `<comp>_ext.c`         | **Glue** — always regenerated from the manifest; never hand-edited                                                                                                                                         |
| `src/<pkg>/<comp>.pyi` | **Glue** — always regenerated                                                                                                                                                                              |
| `CMakeLists.txt`       | **Glue** — always regenerated                                                                                                                                                                              |
| `<comp>_core.c`        | **Sacred** — created once; a structural change rebuilds it via `jm regenerate`, which lifts your hand-written bodies out and splices them back in by function name (`--discard` for a clean reset instead) |
| `<comp>_core.h`        | The state struct + inline `step()` are **sacred**; method/property *declarations* refresh from the TOML                                                                                                    |

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
1. **Implement** — fill in the new `step()`/`steps()`/method body in `_core.c`.
1. **Test** — `make test`.
1. **Iterate** — back to step 1.

You only ever own `_core.c` and the TOML.

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
