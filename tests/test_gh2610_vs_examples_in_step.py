"""gh-2610: the two Visual Studio examples, one venv route each, in step.

The ``visual_studio`` example offered two ways to make the project's
``.venv`` in one step -- pip, and "with uv, ``uv venv`` then ``uv pip install
numpy``" -- and its test ran neither as written. A user mixing the routes
ended with a uv venv, which has no pip, and a pip line that failed in it. It
is now two examples, ``visual_studio_pip`` and ``visual_studio_uv``, each
with exactly one set of venv lines, which CI's Windows job runs as written.

Two folders invite drift, so this holds them together, registration-free:

- every file the examples hold is **byte-identical** between them, except
  the few that are the difference (:data:`DIFFER`) -- and each of those must
  in fact differ, so the allow-list cannot keep a file that has stopped
  being one;
- each example's venv step is exactly its route's lines in :data:`ROUTES`,
  so a pip line on the uv page, or ``uv venv`` back on the pip page, fails
  here on every platform, not only where the lines run as written;
- every other place that tells a reader how to make the ``.venv`` gives the
  same lines from the same table: ``docs/windows.md``, and the refusal the
  root ``CMakeLists.txt`` prints when a preset's ``.venv`` is missing --
  which is also configured here, against a fresh scaffold, so the refusal
  is what a reader sees rather than CMake's bare ``Could NOT find Python3``.

GATE: the Visual Studio examples differ only in their venv step, each gives
      one route's lines, and the docs and the CMake refusal give the same.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from _jmrun import run_cli

ROOT = Path(__file__).resolve().parent.parent
EXAMPLES = ROOT / "src" / "just_makeit" / "examples"
WINDOWS_DOC = ROOT / "docs" / "windows.md"
ROOT_CMAKE = (
    ROOT
    / "src"
    / "just_makeit"
    / "templates"
    / "cmake"
    / "CMakeLists_top.cmake"
)

#: The venv lines of each route, as PowerShell: the example named
#: ``visual_studio_<route>`` runs exactly these in its ``03_venv.ps1``.
ROUTES = {
    "pip": (
        "py -m venv .venv",
        ".venv\\Scripts\\python -m pip install numpy",
    ),
    "uv": ("uv sync",),
}

#: What the old single page offered, which no page may offer again.
RETIRED = ("uv venv", "uv pip install numpy")

#: The files that ARE the difference between the two examples.
DIFFER = {
    "README.md",  # assembled from the two below
    ".steps/00_header.md",  # the example's name, its route, its twin
    ".steps/03_venv.md",
    ".steps/03_venv.ps1",
}


def _examples() -> "dict[str, Path]":
    return {
        p.name.removeprefix("visual_studio_"): p
        for p in sorted(EXAMPLES.glob("visual_studio_*"))
        if (p / "test.py").is_file()
    }


def _files(d: Path) -> "dict[str, bytes]":
    return {
        p.relative_to(d).as_posix(): p.read_bytes()
        for p in sorted(d.rglob("*"))
        if p.is_file() and "__pycache__" not in p.parts
    }


def test_one_example_per_route():
    assert set(_examples()) == set(ROUTES)
    assert not (EXAMPLES / "visual_studio").exists()


def test_the_examples_differ_only_in_the_venv_step():
    (a_name, a), (b_name, b) = _examples().items()
    fa, fb = _files(a), _files(b)
    assert set(fa) == set(fb), set(fa) ^ set(fb)
    same = sorted(f for f in fa if f not in DIFFER and fa[f] != fb[f])
    assert not same, (
        f"{a_name} and {b_name} must share these files byte for byte; edit"
        f" both: {same}"
    )
    stale = sorted(f for f in DIFFER if fa.get(f) == fb.get(f))
    assert not stale, f"DIFFER names files that no longer differ: {stale}"


@pytest.mark.parametrize("route", sorted(ROUTES))
def test_each_venv_step_is_its_routes_lines(route):
    ps1 = _examples()[route] / ".steps" / "03_venv.ps1"
    lines = tuple(
        ln.strip()
        for ln in ps1.read_text(encoding="utf-8").splitlines()
        if ln.strip() and not ln.lstrip().startswith("#")
    )
    assert lines == ROUTES[route]


def test_windows_doc_gives_both_routes_and_neither_retired_one():
    text = WINDOWS_DOC.read_text(encoding="utf-8")
    for route, lines in ROUTES.items():
        for line in lines:
            assert line in text, f"docs/windows.md lacks {route}: {line!r}"
        assert f"examples/visual_studio_{route}.md" in text
    for old in RETIRED:
        assert old not in text, f"docs/windows.md still says {old!r}"
    assert "examples/visual_studio.md" not in text


def test_the_cmake_refusal_gives_both_routes():
    text = ROOT_CMAKE.read_text(encoding="utf-8")
    for lines in ROUTES.values():
        for line in lines:
            assert line in text, line


def test_a_missing_venv_is_refused_at_configure(tmp_path):
    """The preset's ``.venv`` before it exists: what Visual Studio does
    when the folder is opened first."""
    cmake = shutil.which("cmake")
    assert cmake, "cmake is needed: `make test` runs where it is"
    r = run_cli("new", "rv", "--object", "g", cwd=tmp_path)
    assert r.returncode == 0, r.stdout + r.stderr
    proj = tmp_path / "rv"
    missing = proj / ".venv" / "bin" / "python"
    c = subprocess.run(
        [
            cmake,
            "-S",
            str(proj),
            "-B",
            str(tmp_path / "b"),
            f"-DPython3_EXECUTABLE={missing.as_posix()}",
        ],
        capture_output=True,
        text=True,
    )
    said = " ".join((c.stdout + c.stderr).split())
    assert c.returncode != 0
    assert "Could NOT find Python3" not in said, said
    for words in (
        "which does not exist",
        "uv sync",
        "-m pip install numpy",
        "Delete Cache and Reconfigure",
    ):
        assert words in said, (words, said)
