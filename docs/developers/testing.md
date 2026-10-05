# Test philosophy

## What we are testing

`just-makeit` is a code generator. Its only output is files on disk. The test
suite has one job: **verify that every CLI flag and command produces the right
files with the right content** — and that the TOML round-trip (generate →
store → reconstruct) is lossless.

There is no runtime library to unit-test. There is no database. The test
surface is: _given these inputs, what did the generator write?_

______________________________________________________________________

## Layers

### Layer 1 — Content correctness (`test_new.py`, `test_object_helpers.py`, `test_method.py`, …)

These tests call the Python `run()` functions directly (no subprocess) against
a `tmp_path`, then assert specific strings appear (or don't appear) in the
generated files.

**What they catch:** wrong placeholder substitution, missing files, bad
C/Python content, naming regressions.

**What they miss:** interactions between flags that are only visible after a
real compile.

**Auto-updated?** No. When you add a flag or change template output, you must
add or update assertions here. Two safety nets are automatic: the
`test_no_unreplaced_placeholders` tests reject any `<<…>>` that survived
template rendering, and `_init._write` — the one function every generator
writes a file through — refuses to write a bare `<<slot>>` at all (gh-1199).

### Layer 2 — TOML round-trip (`test_toml_roundtrip.py`)

Strategy: scaffold a project via CLI, run `just-makeit script`, replay the
emitted commands into a fresh directory, compare the two `just-makeit.toml`
files byte-for-byte.

**What they catch:** flags that are stored in TOML but not re-emitted by
`script` (or vice versa) — for a flag a `TestXxxRoundTrip` test actually
exercises, and only in the file the comparison reads.

**What they miss:** flags that were never added to `_config.py` in the first
place (can't round-trip what was never stored).

**Auto-updated?** Partially. The comparison is automatic once a test exercises
the flag. Adding a new flag requires a new `test_<flag>_round_trip` method.

### Layer 3 — CLI dispatch (`test_cli.py`)

In-process calls to `_cli.main()` through `tests/_jmrun.run_cli`, which
isolates argv, cwd and `SystemExit` per call (gh-1374). Tests every command,
flag spelling, error message, and exit code.

**What they catch:** CLI parsing bugs, flag name typos, wrong error messages.

**Auto-updated?** Partly. New flags need new test methods here, but
documenting them does not: `tests/test_cli_flag_docs.py` derives the flag set
from the parsers and fails on any flag missing from `jm --help` or from the
reference docs.

### Layer 4 — Example end-to-end (`test_examples.py`, parametrized)

Every example under `src/just_makeit/examples/` that has a `test.py` is
discovered automatically and run as a parametrized test case. `test.py` must
export a `run(root: Path) -> None` function that scaffolds the project,
patches in a real implementation, compiles it with CMake, and exercises the
result.

**What they catch:** scaffolding + CMake + compile + C test + Python
integration failures. The only tests that prove generated code actually
compiles and runs correctly.

**Auto-updated?** Discovery is automatic — drop a `test.py` and it runs.
`test_all_examples_have_test_py` enforces that every example has one. The
_content_ of `test.py` is written by hand.

**Runs under:** `make test-examples`, not `make test` (see
[Running the suite](#running-the-suite)). cmake, a C compiler and numpy must be
available; without one the test skips, and a skip the suite has not agreed to
fails the run (see [Skips are failures](#skips-are-failures)) — locally as
well as in CI.

### Layer 5 — Framework-specific behaviour (`test_pytest_framework.py`)

Dedicated tests for the `--pytest` / `--pytest-benchmark` flags: TOML
storage, generated file content, round-trip, and the critical
`test_still_uses_unittest` regression guard.

**What they catch:** regressions in test-runner selection. This layer was
added _after_ the bug where the default Makefile invoked pytest instead of
unittest — the bug would have been caught immediately had these tests
existed earlier.

______________________________________________________________________

## What is NOT automatically tested

| Gap                                                       | Risk                                        | Mitigation                                                                             |
| --------------------------------------------------------- | ------------------------------------------- | -------------------------------------------------------------------------------------- |
| New flag added to CLI but not to `_config.py`             | Flag silently dropped on `jm script` replay | Add a round-trip test                                                                  |
| New flag added but Makefile/template content not asserted | Wrong content ships                         | Add a content assertion in the relevant `test_*.py`                                    |
| `make test` runner choice                                 | Shipped wrong once (v0.11.0)                | `TestMakeTestRunner` in `test_new.py` now covers both Makefile variants and both modes |
| Help text completeness                                    | New flags invisible in `--help`             | `tests/test_cli_flag_docs.py`, derived from the parsers                                |
| Windows-specific template paths                           | Wrong on Windows only                       | `Examples (windows-latest, clang-cl)` in `ci.yml`, which feeds `CI passed` (gh-1368)   |
| `--impl` / `--replace`                                    | Intentionally not stored in TOML            | Tested in `TestImplCLI` in `test_cli.py`                                               |

______________________________________________________________________

## Rules for contributors

### Adding a new flag

1. Wire it into `_config.py` (`from_new` / `add_component` / save/load).
1. Wire it into `_script.py` so it re-emits.
1. Add a content assertion in the relevant `test_*.py` (what does the
    generated file actually contain?).
1. Add a round-trip test in `test_toml_roundtrip.py`.
1. Add a CLI test in `test_cli.py` (flag accepted, stored, error on bad value).

### Adding a new example

1. Create `src/just_makeit/examples/<name>/.steps/` and `assemble.py`. The
    `README.md` is generated from `.steps/` by `assemble.py`, never written by
    hand (`make format` and the commit hook re-assemble it).
1. Create `src/just_makeit/examples/<name>/test.py` with `run(root: Path)`.
    The parametrized runner in `test_examples.py` picks it up automatically.
1. `test_all_examples_have_test_py` will fail until `test.py` exists — this
    is intentional.
1. Add the example to `GALLERY` in `scripts/copy_examples.py` and to the
    `nav:` in `mkdocs.yml`; the docs build fails until both agree.

### Changing a template

1. Run the full suite (`make test`, then `make test-examples`).
1. The `test_no_unreplaced_placeholders` test catches stray `<<…>>`.
1. Add or update content assertions for any new/changed output strings.

### Changing the `make test` target

`TestMakeTestRunner` in `test_new.py` asserts:

- Default (no `--pytest`): `unittest discover` present, `pytest src/` absent,
    no orphan tab-only recipe line.
- With `--pytest`: `pytest src/` present, `unittest discover` absent.

Both the CMake `Makefile` and the `--build-system make` `Makefile` are covered. Update
these assertions if you intentionally change the runner logic.

______________________________________________________________________

## Running the suite

Through `make`, which is the single source of truth for how the suite runs
(`make help` lists the targets; `make -n <target>` shows the command):

```sh
make test               # the default suite
make test-fast          # stops at the first failure; not test's file set
make test-examples      # every example end to end, plus PROJECT_ENV_TESTS
make test-examples EXAMPLES_K=running_stats   # narrow with pytest -k
make test-all           # both
```

For one test node, activate the project venv and call pytest on it:

```sh
source .venv/bin/activate
pytest tests/test_new.py::TestMakeTestRunner
```

### Two environments

`make test` runs pytest under `uv run --no-project`, so the suite exercises the
installed-package path, and it ignores every file in the Makefile's
`PROJECT_ENV_TESTS`. Those are the tests that need the project's dev tools
(ruff, mypy, clang-format, cmake-lint, …) or a real build — the examples among
them — and `make test-examples` runs them in the project env, where those tools
are visible. A test that asks the environment for a dev tool belongs on that
list; in the isolated env it would only ever skip (gh-1442). `make test` also
runs the `stale_project` example, whose golden records what an upgrading
project sees.

cmake-lint is the exception to "visible in the project env". cmakelang runs
under its own pinned Python whatever Python the suite uses, because the
newest Python it works on is older than the newest one jm is tested on
(gh-1930). The Makefile's `CMAKE_LINT` is that command, and the recipe hands it
to the tests. A test that lints generated CMake therefore fails when run
outside make: run it with
`make test-examples PROJECT_ENV_TESTS=tests/test_cmake_lint.py`, or narrow
the examples with `EXAMPLES_K=`.

### Skips are failures

`tests/conftest.py` fails the run on any skip whose reason is not in
`_ALLOWED_SKIPS` — a short list of things no maintainer can fix (missing
hardware, a platform's linker). A missing tool is not on it: install the tool,
or move the test onto `PROJECT_ENV_TESTS`.

### Proving a new test

A test is proven by sabotaging the fix it guards and watching it go red,
through `scripts/sabotage.py` rather than a hand edit — it refuses a sabotage
that proves nothing and restores the file afterwards. Usage and the reasons
for each refusal: `CLAUDE.md`, "Proving a gate", and the script's docstring.
