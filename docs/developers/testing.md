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
add or update assertions here. The `test_no_unreplaced_placeholders` test is
the only fully automatic safety net — it rejects any `<<…>>` that survived
template rendering.

### Layer 2 — TOML round-trip (`test_toml_roundtrip.py`)

Strategy: scaffold a project via CLI, run `just-makeit script`, replay the
emitted commands into a fresh directory, compare the two `just-makeit.toml`
files byte-for-byte.

**What they catch:** flags that are stored in TOML but not re-emitted by
`script` (or vice versa). A new flag that is wired into `_config.py` and
`_script.py` is automatically exercised here — but only if a corresponding
`TestXxxRoundTrip` test actually exercises that flag.

**What they miss:** flags that were never added to `_config.py` in the first
place (can't round-trip what was never stored).

**Auto-updated?** Partially. The comparison is automatic once a test exercises
the flag. Adding a new flag requires a new `test_<flag>_round_trip` method.

### Layer 3 — CLI dispatch (`test_cli.py`)

Subprocess calls to the real CLI entry point. Tests every command, flag
spelling, error message, and exit code.

**What they catch:** CLI parsing bugs, flag name typos, wrong error messages.

**Auto-updated?** No. New flags need new test methods. The help-text tests
(`test_help_command`) are a weak canary — they assert known commands appear
but do not assert that new flags appear.

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

**Skip conditions:** cmake, a C compiler, and numpy must be on PATH. In CI
(`jm-install-deps` runs before the test suite) these are always present, so
examples never skip in CI.

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
| Help text completeness                                    | New flags invisible in `--help`             | `test_help_mentions_flag` in `test_cli.py` (parametrized, covers every flag)           |
| Windows-specific template paths                           | Only exercised in Docker CI                 | Docker Windows job (`docker.yml`)                                                      |
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

1. Create `src/just_makeit/examples/<name>/assemble.py` and `README.md`.
1. Create `src/just_makeit/examples/<name>/test.py` with `run(root: Path)`.
    The parametrized runner in `test_examples.py` picks it up automatically.
1. `test_all_examples_have_test_py` will fail until `test.py` exists — this
    is intentional.

### Changing a template

1. Run the full suite (`uv run pytest tests/ -v`).
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

```sh
# All tests (fast — no cmake required for most)
uv run pytest tests/ -v

# Only the make-test regression
uv run pytest tests/test_new.py::TestMakeTestRunner -v

# Only example end-to-end (requires cmake + C compiler + numpy)
uv run pytest tests/test_examples.py -v

# One specific example
uv run pytest tests/test_examples.py -k running_stats -v
```
