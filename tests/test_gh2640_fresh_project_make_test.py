"""gh-2640: a project straight from `jm new` passes its own ``make test``.

``jm new hello demo && cd demo && make test`` failed on a tree nobody had
touched: with no component there was no Python test, and unittest on Python
3.12+ exits 5 when it runs none (ctest's "No tests were found" exits 0). The
``--pytest`` recipe hid the same hole the other way, by passing pytest's
exit 5 -- an empty suite that passes proves nothing.

`jm new` now writes ``src/<pkg>/tests/test_project.py`` (`_new.SMOKE_TEST`):
the package imports, and its ``__version__`` is the manifest's ``[project]
version``. Both copies of the version are rows of `_projversion`'s one
table, so ``jm config version`` writes them and ``jm status`` reports them.
The test is born owned, like each component's (gh-1489). Its name is the
one reserved table no component may take, which `_config.validate_name` now
refuses -- ``jm object project`` used to replace ``[project]`` and exit 0.

GATE: every shape of a fresh project -- cmake, ``--pytest``, the ``make``
      backend, an empty module -- runs ``make test`` to exit 0 having run
      at least one Python test. Counted from the runner's own summary, so
      the gate holds on Pythons older than 3.12 too, where an empty
      unittest run still exits 0.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from _compilers import default_cc

from _jmrun import run_cli
from just_makeit import _config as C
from just_makeit import _new, _projversion

_MAKE_ENV = {**os.environ, "PYTHON": Path(sys.executable).as_posix()}

_HAVE_CMAKE = bool(shutil.which("cmake")) and default_cc() is not None


def _new_project(tmp_path: Path, *args: str) -> Path:
    r = run_cli("new", "p", *args, cwd=tmp_path)
    assert r.returncode == 0, r.stdout + r.stderr
    return tmp_path / "p"


def _tests_run(out: str) -> int:
    """How many Python tests the runner says it ran: unittest's ``Ran N``
    or pytest's ``N passed``. Zero when neither summary is there."""
    m = re.search(r"^Ran (\d+) tests? in", out, re.M) or re.search(
        r"\b(\d+) passed\b", out
    )
    return int(m.group(1)) if m else 0


@pytest.mark.parametrize(
    "args",
    [
        pytest.param((), id="cmake"),
        pytest.param(("--pytest",), id="cmake-pytest"),
        pytest.param(("--module", "m"), id="cmake-empty-module"),
        pytest.param(("--build-system", "make"), id="make"),
    ],
)
def test_a_fresh_project_passes_its_own_make_test(tmp_path, args):
    """The issue's trigger, per shape, through the project's own Makefile."""
    if "make" not in args and not _HAVE_CMAKE:
        pytest.fail("the cmake shapes need cmake and a C compiler")
    root = _new_project(tmp_path, *args)
    r = subprocess.run(
        ["make", "test"],
        cwd=root,
        env=_MAKE_ENV,
        capture_output=True,
        text=True,
    )
    out = r.stdout + r.stderr
    assert r.returncode == 0, out[-4000:]
    assert _tests_run(out) >= 1, out[-4000:]


def test_the_pytest_recipe_does_not_pass_an_empty_run(tmp_path):
    """pytest's exit 5 is no longer read as a pass in either recipe."""
    root = _new_project(tmp_path, "--pytest")
    mk = (root / "Makefile").read_text(encoding="utf-8")
    assert "-m pytest src/ -v" in mk, mk
    for passed_five in ("-eq 5", "in(0,5)"):
        assert passed_five not in mk, mk


def test_the_smoke_test_holds_the_package_to_the_manifest(tmp_path):
    """Both copies are `_projversion`'s: none drifts on a fresh project, and
    ``jm config version`` moves both, so ``make test`` stays green."""
    root = _new_project(tmp_path)
    test = root / _new.smoke_test_rel("p")
    init = root / "src" / "p" / "__init__.py"
    assert 'MANIFEST_VERSION = "0.1.0"' in test.read_text("utf-8")
    assert '__version__ = "0.1.0"' in init.read_text("utf-8")
    assert _projversion.drift(root, C.load(root)) == []

    r = run_cli("config", "version", "0.2.0", cwd=root)
    assert r.returncode == 0, r.stdout + r.stderr
    assert 'MANIFEST_VERSION = "0.2.0"' in test.read_text("utf-8")
    assert '__version__ = "0.2.0"' in init.read_text("utf-8")
    assert _projversion.drift(root, C.load(root)) == []


def _run_smoke_test(root: Path) -> "tuple[int, str]":
    """Run the project's Python suite in place; (exit code, output)."""
    r = subprocess.run(
        [sys.executable, "-m", "unittest", "discover", "-s", "src", "-v"],
        cwd=root,
        capture_output=True,
        text=True,
    )
    return r.returncode, r.stdout + r.stderr


def _apply(root: Path) -> str:
    r = run_cli("apply", cwd=root)
    assert r.returncode == 0, r.stdout + r.stderr
    return r.stdout + r.stderr


def _status_check(root: Path) -> int:
    return run_cli("status", "--check", cwd=root).returncode


def test_a_fresh_project_runs_the_literal_check(tmp_path):
    """`jm new` writes a literal, so the test holds it to the manifest."""
    root = _new_project(tmp_path)
    code, out = _run_smoke_test(root)
    assert code == 0, out
    assert "test_version_is_the_manifest_version" in out, out
    assert _tests_run(out) == 1, out


@pytest.mark.parametrize(
    "docstring",
    ['"""p package."""', '"""p package. Exposes __version__ for callers."""'],
    ids=["plain", "docstring-mentions-it"],
)
def test_an_older_project_gains_the_test_and_its_version(tmp_path, docstring):
    """A project made before this has neither: `apply` writes the test and
    adds the ``__version__`` it reads, so the materialized test passes --
    including when a docstring only MENTIONS the name, which binds nothing.
    Reported once, not twice."""
    root = _new_project(tmp_path)
    init = root / "src" / "p" / "__init__.py"
    init.write_text(f"{docstring}\n", encoding="utf-8")
    (root / _new.smoke_test_rel("p")).unlink()

    out = _apply(root)
    assert (root / _new.smoke_test_rel("p")).is_file()
    assert init.read_text("utf-8") == (
        f'{docstring}\n\n__version__ = "0.1.0"\n'
    )
    updates = [
        ln
        for ln in out.splitlines()
        if ln.split()[:1] == ["update"] and "__init__.py" in ln
    ]
    assert len(updates) == 1, out
    assert _status_check(root) == 0
    code, out = _run_smoke_test(root)
    assert code == 0, out
    assert _tests_run(out) == 1, out


#: Ways a package computes ``__version__``: (``__init__.py``, extra files).
_COMPUTED = {
    "try-fallback": (
        '"""p package."""\n\n'
        "try:\n"
        "    from importlib.metadata import PackageNotFoundError, version\n"
        "\n"
        '    __version__ = version("p")\n'
        "except PackageNotFoundError:\n"
        '    __version__ = "unknown"\n',
        {},
    ),
    "installed-dist": (
        '"""p package."""\n\nfrom importlib.metadata import version\n\n'
        '__version__ = version("numpy")\n',
        {},
    ),
    "version-module": (
        '"""p package."""\n\nfrom ._version import __version__\n',
        {"_version.py": '__version__ = "0.1.1.dev3+gabc1234"\n'},
    ),
    "module-getattr": (
        '"""p package."""\n\n\n'
        "def __getattr__(name):\n"
        '    if name == "__version__":\n'
        '        return "0.1.1.dev3"\n'
        "    raise AttributeError(name)\n",
        {},
    ),
}


@pytest.mark.parametrize("case", sorted(_COMPUTED))
def test_a_computed_version_is_the_packages_answer(tmp_path, case):
    """A package computing ``__version__`` keeps it, and the smoke test
    then asks only that it says one -- a dev build's ``0.1.1.dev3+g...``
    and an uninstalled ``"unknown"`` are right answers, not the manifest's.
    The generated test is RUN, not read."""
    init_text, extra = _COMPUTED[case]
    root = _new_project(tmp_path)
    pkg = root / "src" / "p"
    (pkg / "__init__.py").write_text(init_text, encoding="utf-8")
    for name, body in extra.items():
        (pkg / name).write_text(body, encoding="utf-8")

    _apply(root)
    # No literal added. (A `from ._x import __version__` reads as a
    # re-export, so apply gives it the DLL preamble -- gh-1181, not this.)
    after = (pkg / "__init__.py").read_text("utf-8")
    assert '__version__ = "0.1.0"' not in after, after
    assert _projversion.version_binding(after) == _projversion.COMPUTED
    assert _status_check(root) == 0
    code, out = _run_smoke_test(root)
    assert code == 0, out
    assert "test_version_is_a_nonempty_string" in out, out
    assert _tests_run(out) == 1, out


def test_an_annotated_literal_is_a_copy_jm_keeps(tmp_path):
    """``__version__: str = ...`` is the literal case: `status` reports it
    when it disagrees, and `jm config version` writes it."""
    root = _new_project(tmp_path)
    init = root / "src" / "p" / "__init__.py"
    init.write_text(
        '"""p package."""\n\n__version__: str = "0.0.9"\n', encoding="utf-8"
    )
    drift = _projversion.drift(root, C.load(root))
    assert [v.rel for v in drift] == ["src/p/__init__.py"], drift
    r = run_cli("status", "--check", cwd=root)
    assert r.returncode == 1 and "src/p/__init__.py" in r.stdout, r.stdout

    r = run_cli("config", "version", "0.3.0", cwd=root)
    assert r.returncode == 0, r.stdout + r.stderr
    assert '__version__: str = "0.3.0"' in init.read_text("utf-8")
    assert _projversion.drift(root, C.load(root)) == []
    code, out = _run_smoke_test(root)
    assert code == 0, out
    assert "test_version_is_the_manifest_version" in out, out


@pytest.mark.parametrize("name", C.NOT_COMPONENTS)
def test_a_component_cannot_take_a_reserved_key(tmp_path, name):
    """`jm object project` replaced ``[project]`` with the object's keys;
    it is also the one component whose test would be `SMOKE_TEST`."""
    root = _new_project(tmp_path)
    before = (root / C.FILENAME).read_text("utf-8")
    r = run_cli("object", name, cwd=root)
    assert r.returncode == 1, r.stdout + r.stderr
    assert "reserved top-level key" in r.stderr, r.stderr
    assert (root / C.FILENAME).read_text("utf-8") == before
    assert not (root / "src" / "p" / f"{name}.pyi").exists()
