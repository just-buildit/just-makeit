"""gh-2411: pytest and pytest-benchmark are resolved as a pair, capped.

pytest 9.2.0 dropped ``Node._nodeid``; pytest-benchmark 5.3.0 still reads it.
Both resolved unpinned in jm's own CI and in every generated project, so every
benchmark died at setup. The cap lives in `_pins.PYTEST`; jm's Makefile and
pyproject cannot import it, so they carry the same literal, and this holds
them all together:

1. each place jm resolves pytest names the SAME cap (the Makefile's test
   runners, the dev group and the lock, the ``pip install`` in the Makefile
   and CI that jm generates, the hint ``jm bench`` prints);
2. registration-free: any file that names pytest-benchmark as a REQUIREMENT
   (``--with``, a dependency string, a ``pip install``) must also hold the
   capped pytest, so a new place that pairs them fails the day it is written;
3. each cap sits beside the condition for removing it.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).parent.parent
sys.path.insert(0, str(REPO / "src"))

from just_makeit import _new, _pins  # noqa: E402

CAP = _pins.PYTEST  # "pytest<9.2"

#: pytest-benchmark named as something to INSTALL, not as prose.
_REQUIREMENT = re.compile(
    r"--with\s+['\"]?pytest-benchmark|"
    r"^\s*['\"]pytest-benchmark['\"]\s*,|"
    r"(?:pip|uv)\s+(?:pip\s+)?(?:install|add)\b[^\n]*pytest-benchmark",
    re.M,
)
#: pytest named at the cap, in any quoting.
_CAPPED = re.compile(re.escape(CAP) + r"|_pins\.PYTEST")

_SKIP_DIRS = {
    ".git", ".venv", "build", "dist", "node_modules", "__pycache__",
    "changelog.d", ".claude", "site",
}  # fmt: skip
_SKIP_FILES = {"CHANGELOG.md", "uv.lock"}
_SUFFIXES = {".py", ".md", ".toml", ".yml", ".yaml", ".sh", ".mk", ""}


def _files():
    for path in REPO.rglob("*"):
        rel = path.relative_to(REPO)
        if (
            not path.is_file()
            or any(part in _SKIP_DIRS for part in rel.parts)
            or path.name in _SKIP_FILES
            or path.suffix not in _SUFFIXES
        ):
            continue
        if path.suffix == "" and path.name not in {"Makefile"}:
            continue
        yield path


def test_every_file_that_installs_pytest_benchmark_also_caps_pytest():
    """Registration-free over the repo."""
    pairs, bad = [], []
    for path in _files():
        text = path.read_text(encoding="utf-8", errors="replace")
        if path.name == "test_gh2411_pytest_cap.py":
            continue
        if _REQUIREMENT.search(text) is None:
            continue
        pairs.append(path.relative_to(REPO).as_posix())
        if _CAPPED.search(text) is None:
            bad.append(path.relative_to(REPO).as_posix())
    # Armed: the walk found the places it is about.
    assert {"Makefile", "pyproject.toml"} <= set(pairs), pairs
    assert not bad, (
        f"installs pytest-benchmark without capping pytest ({CAP!r}), "
        f"gh-2411: {bad}"
    )


def test_the_cap_is_an_upper_bound_below_the_release_that_broke_it():
    """The other checks compare against `_pins.PYTEST`, so this is the one
    that does not: a cap emptied to a bare ``pytest`` would agree with
    itself everywhere."""
    assert CAP == "pytest<9.2", CAP


def test_the_makefile_runners_cap_pytest():
    text = (REPO / "Makefile").read_text(encoding="utf-8")
    deps = re.search(r"^PYTEST_DEPS\s*=(.*?)(?=^\S)", text, re.M | re.S)
    assert deps, "PYTEST_DEPS moved"
    assert f"--with '{CAP}'" in deps.group(1), deps.group(1)
    # ...and no runner re-adds a bare pytest beside it
    assert not re.search(r"--with pytest(?![-\w<])", text), (
        "a Makefile runner resolves an uncapped pytest"
    )


def test_the_dev_group_and_the_lock_cap_pytest():
    py = (REPO / "pyproject.toml").read_text(encoding="utf-8")
    assert f'"{CAP}"' in py, "pyproject.toml's dev group lost the cap"
    lock = (REPO / "uv.lock").read_text(encoding="utf-8")
    spec = CAP.removeprefix("pytest")
    assert f'{{ name = "pytest", specifier = "{spec}" }}' in lock, (
        "uv.lock does not agree with pyproject.toml's cap (uv lock)"
    )


@pytest.mark.parametrize("pytest_", [True])
def test_a_generated_makefile_installs_the_capped_pytest(pytest_):
    ctx = _new._make_project_ctx("p", pytest_=pytest_)
    assert _pins.PYTEST_SHELL in ctx["ensure_pytest_unix"], ctx
    assert _pins.PYTEST_DQ in ctx["ensure_pytest_win"], ctx


def test_the_generated_ci_installs_the_capped_pytest(tmp_path):
    from _jmrun import run_cli

    assert run_cli("new", "q", "--pytest", cwd=tmp_path).returncode == 0
    assert run_cli("ci", cwd=tmp_path / "q").returncode == 0
    text = (tmp_path / "q/.github/workflows/ci.yml").read_text("utf-8")
    assert f"numpy {_pins.PYTEST_DQ}" in text, text


def test_the_bench_hint_names_the_capped_pair():
    src = (REPO / "src/just_makeit/_bench.py").read_text(encoding="utf-8")
    i = src.index("pytest-benchmark not installed")
    assert "_pins.PYTEST_SHELL" in src[i : i + 300]


@pytest.mark.parametrize(
    "rel",
    [
        "Makefile",
        "pyproject.toml",
        "src/just_makeit/_pins.py",
        "src/just_makeit/examples/full_workflow/README.md",
    ],
)
def test_each_cap_sits_beside_the_condition_for_lifting_it(rel):
    lines = (REPO / rel).read_text(encoding="utf-8").splitlines()
    pins = [i for i, ln in enumerate(lines) if CAP in ln]
    assert pins, f"{rel} does not hold the cap"
    cond = re.compile(r"REMOVE WHEN|lift the cap", re.I)
    for i in pins:
        near = "\n".join(lines[max(0, i - 14) : i + 2])
        if cond.search(near) and "pytest-benchmark" in near:
            return
    pytest.fail(f"{rel}: the cap has no removal condition within 14 lines")
