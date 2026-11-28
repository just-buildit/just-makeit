"""gh-2564 / gh-1902 / gh-2578 / gh-2063 / gh-2577: every app face works.

`jm app` writes one face per target (``_app.TARGETS``) over one source: an
object, a module function, or a set of subcommands. Five issues were one
class -- a face that read its source from a second place, not the one
answer the package itself is generated from:

- gh-2564: the C face built its ``create()`` call from its own list of the
  constructor's flags, which skipped an array init param, so the call
  lost the pointer and the length and did not compile. It now reads the
  ``c_create_seeds`` the smoke test's call is built from.
- gh-1902 / gh-2578: an object app read the object's module off the row's
  ``--module`` alone, so a module object's app run without it imported
  the class from the package root, which does not re-export it.
- gh-2063: the class was named ``_to_title(component)``, not its
  ``class_name``; a function app spelled the module id where the module
  declares ``package``, and ``from .dsp.filters import`` inside
  ``dsp/filters/``.
- gh-2577: a multi-command app was not ruff-clean.

GATE: one project carrying every source shape -- a standalone object, a
      ``class_name`` override, an array init param, a module object (with
      a view of it registered in another module), a module declaring
      ``package``, a dotted module, a function in each module, and a
      multi-command app. Every source the MANIFEST declares (each
      component, each module function; read off it, no list here) gets an
      app for every target in ``_app.TARGETS``. Each C app is BUILT and
      run, each Python face is run (which imports what it names and
      constructs or calls it), and ruff -- jm's pinned one, with isort --
      must report nothing over any Python app. A target with no runner
      here fails, so a new face cannot ship ungated.

Needs the project environment (ruff), so it is in ``PROJECT_ENV_TESTS``.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

import _downstream_gates as G
from _cmake import PYTHON3_ARG
from _compilers import default_cc
from _jmrun import run_cli

from just_makeit import _app
from just_makeit import _config as C

pytestmark = pytest.mark.skipif(
    shutil.which("cmake") is None or default_cc() is None,
    reason="needs cmake and a C compiler",
)

_SCALAR = ("--arg-type", "float", "--return-type", "float")
_FN = ("--param", "a:float", "--return-type", "float")
#: The multi-command app's commands, and the one each run invokes.
_COMMANDS = ("--command", "run", "--command", "go")

_SETUP = [
    ("object", "s", *_SCALAR),
    ("object", "o", "--class-name", "Renamed", *_SCALAR),
    ("object", "corr", *_SCALAR, "--init-param", "gain:float:1.0",
     "--init-param", "taps:float[]:[]"),
    ("module", "filter"),
    ("object", "biquad", "--module", "filter", *_SCALAR),
    ("function", "hx", "--module", "filter", *_FN),
    ("module", "acquire"),
    ("view", "biquad", "Peek", "--module", "acquire",
     "--create-fn", "biquad_create_peek"),
    ("module", "mod"),
    ("object", "pk", "--module", "mod", *_SCALAR),
    ("function", "gx", "--module", "mod", *_FN),
    ("module", "dsp.filters"),
    ("object", "fir", "--module", "dsp.filters", *_SCALAR),
    ("function", "fx", "--module", "dsp.filters", *_FN),
]  # fmt: skip


def _ok(root: Path, *argv: str) -> str:
    r = run_cli(*argv, cwd=root)
    assert r.returncode == 0, (argv, r.stdout, r.stderr)
    return r.stdout


def _sources(cfg: dict) -> "list[tuple[str, tuple[str, ...], list[str]]]":
    """``(tag, jm app source flags, run arguments)`` for every app source
    the manifest declares, plus the multi-command app.

    No ``--module`` anywhere: the app reads the module off the manifest
    (gh-1902). An object app runs on empty input, which constructs the
    object; a function app is given every parameter; a command app runs
    its first command.
    """
    out = [(c, ("--object", c), []) for c in C.components(cfg)]
    for mod in C.modules(cfg):
        for fn in C.module_functions(cfg, mod):
            args = [a for p in fn["params"] for a in (f"--{p['name']}", "1")]
            out.append((fn["name"], ("--function", fn["name"]), args))
    out.append(("cmds", _COMMANDS, [_COMMANDS[1]]))
    return out


def _run(argv: "list[str]", cwd: Path, **kw) -> "subprocess.CompletedProcess":
    return subprocess.run(
        argv,
        cwd=cwd,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        timeout=300,
        **kw,
    )


def _py_env(root: Path) -> dict:
    return {**os.environ, "PYTHONPATH": str(root / "src")}


@pytest.fixture(scope="module")
def project(tmp_path_factory: pytest.TempPathFactory) -> Path:
    tmp = tmp_path_factory.mktemp("gh2564")
    _ok(tmp, "new", "p")
    root = tmp / "p"
    for argv in _SETUP:
        _ok(root, *argv)
    # gh-523: a module whose Python lands in another package. No CLI flag
    # declares it, so the manifest is edited as an author does.
    cfg = C.load(root)
    cfg["module"]["mod"]["package"] = "other"
    C.save(root, cfg)
    _ok(root, "apply")
    # The C and PEP 723 faces, one app each per source; the console face
    # is one per package, so it is driven one app at a time below.
    for target in ("c", "pep723"):
        for tag, flags, _ in _sources(C.load(root)):
            _ok(root, "app", "--target", target, *flags,
                "--name", f"{target}_{tag}")  # fmt: skip
    build = root / "build"
    for argv in (
        ["cmake", "-S", str(root), "-B", str(build), PYTHON3_ARG,
         "-DCMAKE_BUILD_TYPE=Release"],
        ["cmake", "--build", str(build), "--parallel", "2"],
    ):  # fmt: skip
        r = _run(argv, root)
        if r.returncode != 0:
            # Reported by the test, as a FAILED one: an app that does not
            # compile is the finding, not a fixture error (gh-2564).
            (root / _BUILD_LOG).write_text(
                r.stdout[-4000:] + r.stderr[-4000:], encoding="utf-8"
            )
            break
    return root


#: Where the fixture leaves a failed build's output for the test to report.
_BUILD_LOG = "gh2564-build.log"


def _ruff_findings(root: Path, files: "list[Path]") -> "list[str]":
    """jm's pinned ruff over the project, reported for *files* only."""
    rels = {f.relative_to(root).as_posix() for f in files}
    check = G._ruff(root, "check", "--extend-select", "I",
                    "--output-format=concise")  # fmt: skip
    out = [
        ln
        for ln in check.splitlines()
        if (m := G._RUFF.match(ln)) and m.group(1) in rels
    ]
    fmt = G._ruff(root, "format", "--check")
    out += [f"would reformat {f}" for f in G._REFORMAT.findall(fmt)]
    return [ln for ln in out if any(r in ln for r in rels)]


def _c(root: Path) -> "list[str]":
    bad = []
    for tag, _flags, args in _sources(C.load(root)):
        exe = root / "build" / f"c_{tag}"
        if os.name == "nt":
            exe = exe.with_suffix(".exe")
        if not exe.exists():
            bad.append(f"c_{tag}: never built")
            continue
        r = _run([str(exe), *args], root)
        if r.returncode != 0:
            bad.append(f"c_{tag} {args}: rc {r.returncode} {r.stderr}")
    return bad


def _pep723(root: Path) -> "list[str]":
    bad, files = [], []
    for tag, _flags, args in _sources(C.load(root)):
        script = root / f"pep723_{tag}.py"
        files.append(script)
        r = _run([sys.executable, str(script), *args], root,
                 env=_py_env(root))  # fmt: skip
        if r.returncode != 0:
            bad.append(f"{script.name} {args}: rc {r.returncode} {r.stderr}")
    return bad + _ruff_findings(root, files)


_ENTRY = re.compile(r'^con = "([\w.]+):main"$', re.M)


def _console(root: Path) -> "list[str]":
    """Each source's console app in turn: declared, run as its entry
    point says, ruff-checked, then removed, since a package holds one."""
    bad = []
    for tag, flags, args in _sources(C.load(root)):
        _ok(root, "app", "--target", "console", *flags, "--name", "con")
        try:
            pyproject = (root / "pyproject.toml").read_text(encoding="utf-8")
            (dotted,) = _ENTRY.findall(pyproject)
            r = _run([sys.executable, "-m", dotted, *args], root,
                     env=_py_env(root))  # fmt: skip
            if r.returncode != 0:
                bad.append(f"{tag}: {dotted} {args}: {r.stderr}")
            cli = root / "src" / Path(*dotted.split(".")).with_suffix(".py")
            bad += [f"{tag}: {f}" for f in _ruff_findings(root, [cli])]
        finally:
            _ok(root, "remove", "app", "con", "--force")
    return bad


#: How each target's apps are proven to work. Keyed by every target
#: `jm app` accepts, which `test_every_target_has_a_runner` holds.
_RUNNERS = {"c": _c, "console": _console, "pep723": _pep723}


def test_every_target_has_a_runner() -> None:
    assert set(_RUNNERS) == set(_app.TARGETS)


def test_the_fixture_has_every_source_kind(project: Path) -> None:
    """The sources are read off the manifest, so prove they hold every
    shape the docstring names -- an empty list would pass for nothing."""
    cfg = C.load(project)
    kinds = {flags[0] for _, flags, _ in _sources(cfg)}
    assert kinds == {"--object", "--function", "--command"}
    assert C.class_name(cfg, "o") == "Renamed"
    assert any(p[1].endswith("[]") for p in C.init_params(cfg, "corr"))
    assert C.module_package(cfg, "mod") == "other"
    assert C.module_of(cfg, "fir") == "dsp.filters"
    assert C.views(cfg, "biquad")


@pytest.mark.parametrize("target", _app.TARGETS)
def test_every_app_face_builds_and_runs(project: Path, target: str) -> None:
    log = project / _BUILD_LOG
    bad = (
        [f"the build failed:\n{log.read_text(encoding='utf-8')}"]
        if log.exists()
        else []
    ) + _RUNNERS[target](project)
    assert not bad, "\n".join(bad)


def test_an_app_the_binding_cannot_construct_is_refused(
    tmp_path: Path,
) -> None:
    """gh-2564: a required array has no flag and no default, so the app
    could construct nothing; and a ``--module`` that is not the object's
    names a package that does not re-export it (gh-1902)."""
    _ok(tmp_path, "new", "p")
    root = tmp_path / "p"
    _ok(root, "object", "need", *_SCALAR, "--init-param", "taps:float[]")
    _ok(root, "module", "m")
    _ok(root, "object", "b", "--module", "m", *_SCALAR)
    for argv, why in (
        (("--object", "need"), "requires constructor parameter(s) 'taps'"),
        (("--object", "b", "--module", "other"), "is in module 'm'"),
        (("--object", "need", "--module", "m"), "is in no module"),
    ):
        for target in _app.TARGETS:
            r = run_cli("app", "--target", target, *argv, cwd=root)
            assert r.returncode == 1 and why in r.stderr, (argv, r.stderr)
    assert not C.apps(C.load(root))
