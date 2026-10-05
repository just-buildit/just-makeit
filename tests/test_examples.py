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
from pathlib import Path

import pytest

import _cmakelint
import _downstream_gates
from just_makeit._build import run_generated_pytest
from test_gh1287_nested_block_comment import (
    assert_no_nested_block_comments,
)

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
        'See docs/developers/testing.md, "Adding a new example".'
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
    # gh-1930: through the Makefile's CMAKE_LINT -- cmakelang under its
    # pinned Python -- and a crash is reported as one, not as findings.
    _cmakelint.check(tmp_path.rglob("CMakeLists.txt"), "generated project")
    # gh-1287: the widest corpus jm has for a property of the C it renders.
    # The scanner lives with its own sabotage tests next to the bug it was
    # written for; this is the second corpus it runs over, not a second
    # implementation of it. Every example is swept, so a shape jm learns to
    # emit is covered as soon as an example exercises it.
    assert_no_nested_block_comments(tmp_path)
    _generated_pytest_check(tmp_path)
    # gh-1443 Gate A: last, because a re-apply is one of its checks and must
    # not change what the checks above were run against.
    _downstream_gates.check(example_dir.name, tmp_path)


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
