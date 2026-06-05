# three_face — one C core, three faces

Demonstrates the combined target just-makeit is architected for: a **single C
core** exposed three ways, all calling the same `gain_step()`:

| Face | Artifact | How it's reached |
|------|----------|------------------|
| Standalone **C binary CLI** | `build/gaintool` | `jm app --target c` → `add_executable` linking `gain_core` |
| **Python CLI** (console entry) | `python -m gaintool.cli` / `gaintool` on PATH | `jm app --target console` → `cli.py` + `[project.scripts]` |
| **Python module API** | `from gaintool import Gain` | native jm extension |
| (bonus) shareable script | `gaintool.py` | `jm app --target pep723` |

`gaintool` scales a stream of `float32` samples by `--gain`. The same
`gain_core.c` is compiled once as a CMake OBJECT library and linked by the
binary, the C test, and the Python extension — so all three faces are guaranteed
to behave identically (the test asserts byte-identical output).

## Run it

```sh
# the end-to-end test (scaffold → fill → build → run all three faces)
python3 src/just_makeit/examples/three_face/test.py
# or via the suite
pytest tests/test_examples.py -k three_face
```

Manually, after `test.py` builds a project:

```sh
printf '...' | ./build/gaintool --gain 2.0 > out.f32     # C binary
python -m gaintool.cli --gain 2.0 < in.f32 > out.f32     # Python CLI (run from src/)
python -c "from gaintool import Gain; print(Gain(2.0).step(1.5))"  # module
```

## What `jm` gives you vs what you fill in

`jm app` scaffolds the **plumbing**, correctly and idempotently:
- the executable target + `target_link_libraries(gaintool PRIVATE gain_core)`
- the console `cli.py` and the `[project.scripts]` entry
- the pep723 self-contained script

It does **not** fill the **logic** — `test.py` hand-writes those parts (the
`<<IMPLEMENT>>` stubs). That hand-written code is the spec for a future
spec-driven generator.

## Gaps surfaced (→ future `jm app` work)

1. **No arg-parser generation.** The C `main()` argv loop and the Python
   argparse body are `<<IMPLEMENT>>` stubs; we wrote `--gain/--input/--output`
   by hand in both languages. A `[[app.flags]]` / `[[app.commands]]` spec should
   generate a C `getopt_long` parser *and* the matching Python argparse from one
   source of truth.
2. **No I/O loop.** The read→`step()`→write loop is hand-written per face.
3. **Object-only.** `jm app` requires a `step()`-bearing object
   (`_app.py:215-226`); it can't target module-level `jm function`s.
4. **C binary gets no flags from state.** The Python console target turns
   ctor state vars into `--flags`; the C target gets a bare placeholder.
5. **Name collision.** The pep723 `gaintool.py` at the project root shadows the
   `gaintool/` package when you run from the root — the test runs the Python
   faces from `src/` to avoid it. The generator should warn, or namespace the
   script.
