"""
test_examples.py — end-to-end pytest runner for examples/.

Discovery: any examples/<name>/test.py with a run(root: Path) function.
New examples are picked up automatically — just drop in a test.py.

Skip conditions (checked once, applied to all examples):
  - cmake not on PATH
  - no C compiler (cc, gcc, or clang)
  - numpy not importable
"""

import importlib.util
import shutil
import subprocess
from pathlib import Path

import pytest

from just_makeit._build import run_generated_pytest

# Formatting rules are cmake-format's responsibility.
_CMAKE_LINT_DISABLED = ["C0301", "C0307"]

EXAMPLES_DIR = (
    Path(__file__).parent.parent / "src" / "just_makeit" / "examples"
)


def _all_example_dirs():
    """All subdirs of examples/ that contain an assemble.py (i.e. are examples)."""
    return sorted(p.parent for p in EXAMPLES_DIR.glob("*/assemble.py"))


def _discover_examples():
    return sorted(p.parent for p in EXAMPLES_DIR.glob("*/test.py"))


def test_all_examples_have_test_py():
    missing = [
        d.name for d in _all_example_dirs() if not (d / "test.py").exists()
    ]
    assert not missing, (
        f"Example(s) missing test.py: {missing}\n"
        "Add a test.py with a run(root: Path) -> None function. "
        "See examples/README.md for the pattern."
    )


def _load_run(example_dir: Path):
    spec = importlib.util.spec_from_file_location(
        f"example_{example_dir.name}", example_dir / "test.py"
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.run


# ── skip guard ────────────────────────────────────────────────────────────────


def _skip_reason():
    if not shutil.which("cmake"):
        return "cmake not found"
    if not any(shutil.which(c) for c in ("cc", "gcc", "clang")):
        return "no C compiler found"
    try:
        import numpy  # noqa: F401
    except ImportError:
        return "numpy not importable"
    return None


_SKIP = _skip_reason()


# ── parametrized test ─────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "example_dir",
    _discover_examples(),
    ids=[p.name for p in _discover_examples()],
)
def test_example(example_dir, tmp_path):
    if _SKIP:
        pytest.skip(_SKIP)
    run = _load_run(example_dir)
    run(tmp_path)
    _cmake_lint_check(tmp_path)
    _generated_pytest_check(tmp_path)


def _generated_pytest_check(root: Path) -> None:
    """Run each generated project's OWN pytest suite (gh-1089).

    The walkthrough above asserts the steps produced what they should. This
    asserts the thing a *user* is left holding actually passes its own tests,
    which is a different question and had exactly one home: the Docker image
    build, which is not a required check. It was red on `main` for 14
    consecutive runs, caught nothing, and the release was the first thing to
    stop.

    `run()` scaffolds into *root*, sometimes more than one project, so every
    directory holding a `just-makeit.toml` is checked. An unbuilt scaffold has
    no extension and is skipped by `run_generated_pytest` — see there for why
    that is a skip and not a failure.
    """
    projects = [p.parent for p in root.rglob("just-makeit.toml")]
    for proj in sorted(projects):
        assert run_generated_pytest(proj), (
            f"the generated project at {proj.relative_to(root)} does not pass"
            " its own pytest suite"
        )


def _cmake_lint_check(root: Path) -> None:
    """Run cmake-lint on all CMakeLists.txt under *root*, skip if not on PATH."""
    if not shutil.which("cmake-lint"):
        return
    cmake_files = list(root.rglob("CMakeLists.txt"))
    if not cmake_files:
        return
    r = subprocess.run(
        ["cmake-lint", "--disabled-codes"]
        + _CMAKE_LINT_DISABLED
        + ["--"]
        + [str(f) for f in cmake_files],
        capture_output=True,
        text=True,
        timeout=600,
    )
    assert r.returncode == 0, (
        f"cmake-lint found violations in generated project:\n{r.stdout}"
    )
