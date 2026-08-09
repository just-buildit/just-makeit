"""Each xdist worker must build in an environment no peer can write to.

gh-879 was a CI flake that read as a real regression: a generated project's
compile died on ``numpy/arrayobject.h: No such file or directory``, on
``ubuntu-latest`` only, against a different test every time.

The cause was a shared environment, not a missing package. ``uv run
--no-project`` declines to install *this* project but still discovers ``.venv``
and exports ``VIRTUAL_ENV``, so every xdist worker inherited one site-packages
— and the end-to-end tests write to it. Each generated project's ``make`` runs
the gh-824 header guard, which repairs an absent include directory with ``pip
install --force-reinstall numpy``; run concurrently, the worker doing the
repair rips the headers out from under every worker mid-compile, whose guards
then fire in turn. One transient miss cascaded, which is exactly why the victim
moved around.

This file is the gate. It asserts the property directly — a worker's
``VIRTUAL_ENV`` is private — rather than trying to observe a race, because a
test that reproduces a race only fails some of the time and a gate that fails
some of the time is not a gate.

Both branches assert something real, so this never reports a vacuous pass: with
xdist the isolation must be in place, and without it the no-op must be the
*deliberate* no-op (one process cannot race itself) rather than provisioning
that silently failed.
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path

import conftest

REPO_VENV = Path(__file__).parent.parent / ".venv"


def _worker() -> str:
    """This xdist worker's id, or '' when running in a single process."""
    return os.environ.get("PYTEST_XDIST_WORKER", "")


def test_worker_env_is_private_to_this_worker():
    """Under xdist, VIRTUAL_ENV must be this worker's own, not the repo's."""
    worker = _worker()
    if not worker:
        assert conftest._worker_env_root is None, (
            "no xdist worker id, yet an isolated environment was provisioned "
            "— the no-op branch is meant to be the deliberate single-process "
            "case, not an accident"
        )
        return

    venv = os.environ.get("VIRTUAL_ENV", "")
    assert venv, "VIRTUAL_ENV is unset inside an xdist worker"

    resolved = Path(venv).resolve()
    assert resolved != REPO_VENV.resolve(), (
        f"worker {worker} is building in the repo's own .venv ({resolved}). "
        f"Every worker writing to one environment is gh-879: a concurrent "
        f"`pip install --force-reinstall numpy` from the gh-824 guard removes "
        f"the headers another worker is compiling against."
    )
    assert resolved.is_dir(), f"{resolved} is not a directory"
    assert worker in str(resolved), (
        f"worker {worker}'s environment {resolved} is not keyed to it, so two "
        f"workers could share it"
    )


def test_both_consumers_agree_on_the_worker_env():
    """VIRTUAL_ENV and JUST_BUILDIT_PYTHON must name one environment.

    They are read by the two consumers that matter — ``uv pip install`` and
    the generated Makefile — so setting only one would isolate installs while
    leaving builds pointed at the shared environment, which is half a fix and
    reads as a whole one.
    """
    if not _worker():
        assert (
            "JUST_BUILDIT_PYTHON" not in os.environ
            or Path(os.environ["JUST_BUILDIT_PYTHON"]).exists()
        )
        return

    # Compared unresolved throughout: a venv's `bin/python` is a symlink to
    # the interpreter it was built from, so resolving it always lands outside
    # the venv and would make this assert something false.
    venv = Path(os.environ["VIRTUAL_ENV"])

    jbp = Path(os.environ["JUST_BUILDIT_PYTHON"])
    assert jbp.exists(), f"JUST_BUILDIT_PYTHON does not exist: {jbp}"
    assert venv in jbp.parents, f"{jbp} is outside {venv}"


def test_path_discovery_is_left_alone():
    """`python3` off PATH must NOT be this worker's venv.

    Isolating the *writes* is the goal; isolating interpreter discovery for
    every subprocess in the suite is not, and doing it breaks the build.

    Some tests invoke ``cmake`` directly rather than through the generated
    Makefile, so they pass no ``-DPython3_EXECUTABLE`` and CMake finds an
    interpreter itself, off PATH. Point that at the worker venv — whose numpy
    is reachable only through the inherited ``.pth`` — and ``FindPython3``'s
    NumPy probe does not honour it, so configure dies with ``Could NOT find
    Python3 (missing: Python3_NumPy_INCLUDE_DIRS NumPy)``.

    That is not hypothetical: prepending PATH is what a first cut of this fix
    did, and it turned CI red on ``ubuntu-24.04-arm, 3.14`` in
    ``test_c_style.py::test_formatted_project_builds`` while passing locally.
    """
    if not _worker():
        return

    venv = Path(os.environ["VIRTUAL_ENV"])
    found = subprocess.run(
        ["python3", "-c", "import sys; print(sys.executable)"],
        capture_output=True,
        text=True,
    )
    assert found.returncode == 0, found.stderr
    on_path = Path(found.stdout.strip())
    assert venv not in on_path.parents, (
        f"`python3` off PATH resolves into this worker's venv ({on_path}). "
        f"CMake discovers its interpreter that way when a test invokes it "
        f"directly, and this venv's numpy is only reachable via a .pth that "
        f"FindPython3's NumPy probe ignores."
    )


def test_installing_tests_run_the_interpreter_they_installed_into():
    """A file that installs with uv must not then run `sys.executable`.

    Isolation cuts both ways and only one of them is wanted. These tests
    install a scaffolded project with uv — which targets ``VIRTUAL_ENV``, now
    the worker's — and then run it. Running the *parent* interpreter there
    fails with ``ModuleNotFoundError``, because the package is in the worker's
    site-packages, not the parent's. A first cut of this fix left it that way
    and broke all 27 end-to-end tests on 8 of 8 runs.

    The tempting bridge does not work and is worth naming, since it looks
    correct: putting the worker's site-packages on ``PYTHONPATH`` adds the
    directory to ``sys.path``, but ``.pth`` files are only executed for
    directories ``site`` registers — and an editable install *is* a ``.pth``
    redirect, so the import still fails.

    Scanned from source rather than pinned to today's two files, so a new
    end-to-end test file is covered the day it is written.
    """
    offenders = []
    for path in sorted(Path(__file__).parent.glob("test_*.py")):
        text = path.read_text()
        if '"uv", "pip", "install"' not in text:
            continue
        if "JUST_BUILDIT_PYTHON" not in text:
            offenders.append(path.name)

    assert not offenders, (
        f"{', '.join(offenders)} shell out to `uv pip install` but never "
        f"consult JUST_BUILDIT_PYTHON, so they install into one environment "
        f"and run another. Use "
        f"`os.environ.get('JUST_BUILDIT_PYTHON') or sys.executable` (gh-879)."
    )


def test_inheritance_covers_every_sys_path_entry_not_just_purelib():
    """Every package directory must be inherited, not only ``purelib``.

    This is the one that has to fail on a developer's machine, because the bug
    it guards only *manifests* on CI. uv assembles the run environment from
    layers. With a ``.venv`` present it installs ``--with`` packages straight
    into it, so ``purelib`` happens to name everything and inheriting it alone
    looks correct. With no ``.venv`` — CI, always — the base is an empty
    ephemeral venv under ``builds-v0/`` and the packages sit in a separate
    ``archive-v0/`` overlay reachable only through ``sys.path``.

    So the purelib-only version passed every local run and inherited *nothing*
    on CI: 14 failures across two shapes, ``Could NOT find Python3 (missing:
    Python3_NumPy_INCLUDE_DIRS NumPy)`` wherever a test compiled.

    Feeding a synthetic ``sys.path`` makes that testable anywhere, instead of
    waiting for a machine laid out the other way.
    """
    with tempfile.TemporaryDirectory() as base:
        overlay = Path(base) / "archive-v0" / "site-packages"
        ephemeral = Path(base) / "builds-v0" / "site-packages"
        overlay.mkdir(parents=True)
        ephemeral.mkdir(parents=True)

        got = conftest._inheritable_paths(
            ["", str(ephemeral), str(overlay), str(Path(base) / "nonexistent")]
        )

    assert str(overlay) in got, (
        "the overlay directory was dropped. Under `uv run` with no .venv that "
        "is where numpy lives, so inheriting only purelib yields a worker "
        "environment with no numpy at all."
    )
    assert str(ephemeral) in got
    assert all(Path(p).name != "nonexistent" for p in got), (
        "a path that does not exist was inherited"
    )


def test_worker_env_has_pip_for_the_gh824_guard():
    """The worker interpreter must be able to run `-m pip`.

    Every generated Makefile repairs an absent numpy include directory with
    ``$(PYTHON) -m pip install --force-reinstall numpy`` (gh-824). ``$(PYTHON)``
    is now this worker's interpreter, and a bare ``uv venv`` ships no pip — so
    without ``--seed`` the guard dies with ``No module named pip`` rather than
    repairing anything, 16 times in one CI job.
    """
    if not _worker():
        return

    proc = subprocess.run(
        [os.environ["JUST_BUILDIT_PYTHON"], "-m", "pip", "--version"],
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, (
        f"`-m pip` fails in the worker environment, so the gh-824 numpy "
        f"header guard cannot run:\n{proc.stderr}"
    )


def test_worker_installs_are_constrained_to_the_parents_numpy():
    """Nothing this worker installs may move numpy off the parent's release.

    The `.pth` makes the parent's numpy *importable*, which is not the same as
    binding it. uv resolves against the worker's own site-packages, so the
    first scaffolded project depending on numpy installs a fresh copy and
    takes the newest release — shadowing the inherited one the moment upstream
    publishes. The worker then compiles the extension against one release
    while `sys.executable` imports it with another.

    That is not hypothetical: numpy 2.5.2 shipped while this was in flight and
    turned CI red on a PR that touched none of it. `UV_CONSTRAINT` binds every
    install in the worker, including transitive ones, which an up-front pin of
    numpy alone would not.
    """
    if not _worker():
        return

    constraint = os.environ.get("UV_CONSTRAINT", "")
    assert constraint, (
        "UV_CONSTRAINT is unset, so a scaffolded project's install is free to "
        "pull a newer numpy than the parent and split the C-API across the "
        "compile/import boundary"
    )
    text = Path(constraint).read_text()
    assert "numpy==" in text, f"constraint file does not pin numpy: {text!r}"

    parent = subprocess.run(
        [sys.executable, "-c", "import numpy; print(numpy.__version__)"],
        capture_output=True,
        text=True,
    )
    assert parent.returncode == 0, parent.stderr
    assert f"numpy=={parent.stdout.strip()}" in text, (
        f"the constraint pins a different release than the parent imports: "
        f"{text.strip()!r} vs {parent.stdout.strip()}"
    )


def test_worker_env_inherits_the_base_numpy():
    """Isolation must not fork numpy's C-API away from `sys.executable`.

    The worker env inherits the parent's packages through a ``.pth`` written
    into its site-packages, so an untouched worker reads the very same numpy.
    That matters because the generated extension is compiled against whatever
    numpy the *build* interpreter sees and then imported by ``sys.executable``
    in the tests: two different numpy *releases* either side of that line is an
    ABI mismatch. A later "tidy up" that drops the inheritance would isolate
    correctly and break the imports, which this pins.

    ``--system-site-packages`` is not the mechanism, and cannot be: it inherits
    the base interpreter's site-packages, while under ``uv run`` the packages
    live in the project ``.venv``.
    """
    if not _worker():
        return

    script = "import numpy; print(numpy.__version__)"
    inner = subprocess.run(
        [os.environ["JUST_BUILDIT_PYTHON"], "-c", script],
        capture_output=True,
        text=True,
    )
    outer = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
    )
    assert inner.returncode == 0, inner.stderr
    assert outer.returncode == 0, outer.stderr

    # The VERSION must match, not the location. Once a scaffolded project is
    # installed, its numpy dependency lands in the worker's own site-packages,
    # so the two interpreters legitimately import different copies — that is
    # the isolation doing its job. What would break the tests is the two copies
    # being different *releases*, since the C-API they expose is what the
    # extension is compiled against on one side and imported with on the other.
    assert inner.stdout == outer.stdout, (
        f"the worker environment resolves numpy "
        f"{inner.stdout.strip()} while sys.executable resolves "
        f"{outer.stdout.strip()}. The generated extension is built against "
        f"the first and imported by the second, so their C-APIs must match."
    )
