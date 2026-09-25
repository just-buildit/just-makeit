## Module function, reexports, and an app face

Three more features round out the project:

- **Module-level function** — `lerp(a, b, t)` is a free function in the `dsp`
  module (not an object): `from kitchen_sink.dsp import lerp`.

- **Reexported `no_generate` sibling** — `dsp_fn` is a *hand-written* CPython
  extension (jm only wires its `add_subdirectory`; the `.c`, CMakeLists, and
  `.pyi` are yours). It builds into the `dsp` package dir, and
  `[module.dsp] reexports = { dsp_fn = ["db10"] }` folds its `db10` into
  `dsp/__init__.py` — so `from kitchen_sink.dsp import db10` just works. This is
  the same pattern doppler uses for its functional `ddc_fn` API.

- **App face** — `jm app --target console --object gain --name dsp_cli`
  generates an `argparse` CLI over the `gain` bindings and wires it into
  `[project.scripts]`.

`test.py` builds the project and runs CTest over the C tests, then drives the
Python bindings through `smoke.py` — so the whole combination is compiled and
exercised on every CI run. The `dsp_cli` app face is checked for its
`[project.scripts]` wiring rather than executed; the `three_face` example
covers running a generated app.
