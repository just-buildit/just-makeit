# examples/

Each subdirectory is a self-contained worked example that walks through scaffolding, implementing, building, and extending a just-makeit project.

## Structure of an example

```
examples/
  my_example/
    README.md          ← assembled tutorial (generated — do not edit directly)
    assemble.py        ← weaves .steps/*.md + embedded files into README.md
    test.py            ← end-to-end pytest driver (required)
    .steps/
      00_header.md     ← intro section
      01_scaffold.sh   ← just-makeit new ... invocation
      01_scaffold.md   ← prose for the scaffold step
      02_patch.py      ← applies the implementation to the generated stub
      02_implement.md  ← prose describing what to implement
      03_build.sh      ← cmake + make commands
      03_build.md      ← prose for the build step
      ...              ← additional steps: add state, perf, demo, etc.
```

### `.steps/` naming convention

Files are sorted and assembled in lexicographic order.  Use a two-digit prefix and a short slug:

| Suffix | Treated as |
|--------|-----------|
| `.md`  | prose, assembled into README.md |
| `.sh`  | shell script, embedded verbatim in a `sh` fence |
| `.py`  | Python script or patch, embedded in a `python` fence |
| `.c` / `.h` | C source, embedded in a `c` fence |

Only `.md` files are included in the assembled README.  Other files are embedded inside fences using the ```` ```{filename} ```` syntax (see below).

### Embedding file content in prose

In a `.md` step file, reference another file in `.steps/` with:

    ```{02_patch.py}
    ```

`assemble.py` replaces this with the file's content inside a language-tagged fence (language inferred from extension).

### Regenerating README.md

```sh
python3 examples/my_example/assemble.py
```

Run this after editing any `.steps/` file.  CI checks that README.md is up to date:

```sh
python3 examples/my_example/assemble.py --check
```

---

## Adding a new example

1. **Create the directory** and copy `assemble.py` from an existing example (it is identical across all examples).

2. **Write your `.steps/` files** following the naming convention above.

3. **Write `test.py`** — this is required for the example to be tested automatically.

### `test.py` contract

`test.py` must export a `run(root: Path) -> None` function.  `root` is a temporary directory; your test creates the project inside it.  Raise `AssertionError` (or any exception) on failure.

```python
# examples/my_example/test.py
import subprocess, sys, tempfile
from pathlib import Path

HERE = Path(__file__).parent
STEPS = HERE / ".steps"

def _cmd(args, cwd):
    r = subprocess.run(args, cwd=cwd, capture_output=True, text=True)
    if r.returncode != 0:
        raise AssertionError(
            f"Command failed: {' '.join(str(a) for a in args)}\n"
            f"stdout:\n{r.stdout}\nstderr:\n{r.stderr}"
        )

def run(root: Path) -> None:
    from just_makeit._new import run as jm_new

    jm_new("my_example", root / "my_example", component="my_comp",
           state_vars=[("gain", "double", "1.0")])
    proj = root / "my_example"

    _cmd([sys.executable, str(STEPS / "02_patch.py")], cwd=proj)

    _cmd(["cmake", "-B", "build", "-S", ".",
          "-DCMAKE_BUILD_TYPE=Release",
          f"-DPython3_EXECUTABLE={sys.executable}"], cwd=proj)
    _cmd(["cmake", "--build", "build", "--parallel", "4"], cwd=proj)
    _cmd(["ctest", "--test-dir", "build", "--output-on-failure"], cwd=proj)

if __name__ == "__main__":
    with tempfile.TemporaryDirectory() as tmp:
        run(Path(tmp))
    print("my_example: PASSED")
```

Key rules:
- Use `just_makeit` Python API (`_new.run`, `_add.run`, `_init.run`, `_perf.run`) for scaffolding calls — faster and doesn't require `just-makeit` on PATH.
- Use `subprocess` only for cmake/make/ctest (things that must run as real processes).
- Use `sys.executable` for all Python subprocess calls so the right venv is used.
- Use `str(STEPS / "script.py")` to reference patch scripts — paths are absolute so they work from any cwd.
- The test must be idempotent: a fresh `tmp_path` is supplied each run.

### Running example tests

```sh
# All examples
make test-examples

# One example
pytest tests/test_examples.py::test_example[fir_filter] -v

# Directly (no pytest)
python3 examples/fir_filter/test.py
```

Tests are automatically skipped when `cmake` or a C compiler is not available.
