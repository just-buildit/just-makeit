"""Stub conformance gate: every importable symbol has a matching stub.

jm generates a `.pyi` for every object/module, but nothing verified it agreed
with the compiled extension. The only stub tests were `ast.parse` (syntax) and
per-feature substring asserts, so the same class of bug kept recurring one
issue at a time (gh-446, gh-519, gh-527, gh-529, gh-530, gh-543 were all stub
defects). This gate closes the class: for each emit-path shape it scaffolds a
minimal module, builds it, and runs `mypy.stubtest`, which imports the `.so`,
walks every public symbol, and fails on anything importable-but-unstubbed or
any stub that disagrees with the runtime.

Two mechanics matter:

* **Isolation.** stubtest imports the whole package, and a scaffold's generated
  `tests/` / `benchmarks/` carry an unguarded `import pytest` and (for
  pytest-benchmark projects) type errors that abort mypy's build before it can
  compare anything. So each case copies just `<leaf>.<so>` + `<leaf>.pyi` into a
  clean directory and points stubtest there.
* **Interpreter match.** The extension is built with
  `-DPython3_EXECUTABLE={sys.executable}` so the `.so` imports under the same
  interpreter that runs stubtest.

The whole gate self-skips when cmake / a C compiler / numpy / mypy is absent,
mirroring `tests/test_examples.py`.
"""

from __future__ import annotations

import contextlib
import io
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from just_makeit._function import run as function_run
from just_makeit._method import run as method_run
from just_makeit._module import run as module_run
from just_makeit._new import run as new_run
from just_makeit._object import run as object_run
from just_makeit._property import run as property_run


# ── skip guard ──────────────────────────────────────────────────────────────


def _skip_reason() -> str | None:
    if not shutil.which("cmake"):
        return "cmake not found"
    if not any(shutil.which(c) for c in ("cc", "gcc", "clang")):
        return "no C compiler found"
    try:
        import numpy  # noqa: F401
    except ImportError:
        return "numpy not importable"
    try:
        import mypy.stubtest  # noqa: F401
    except ImportError:
        return "mypy not importable"
    return None


_SKIP = _skip_reason()


# ── harness ─────────────────────────────────────────────────────────────────


def _q(fn, *a, **k):
    """Call a jm command entry point with its scaffold chatter suppressed."""
    with contextlib.redirect_stdout(io.StringIO()):
        return fn(*a, **k)


def _build(root: Path) -> None:
    build = root / "build"
    for cmd in (
        [
            "cmake",
            "-S",
            str(root),
            "-B",
            str(build),
            f"-DPython3_EXECUTABLE={sys.executable}",
        ],
        ["cmake", "--build", str(build)],
    ):
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
        assert r.returncode == 0, (
            f"{cmd[0]} {cmd[1]} failed:\n{r.stdout}\n{r.stderr}"
        )


def _stubtest(so_dir: Path, leaf: str) -> list[str]:
    """Isolate <leaf>.so + <leaf>.pyi and return stubtest's error lines."""
    sos = list(so_dir.glob(f"{leaf}.*.so"))
    assert sos, f"no built {leaf}.*.so in {so_dir}"
    pyi = so_dir / f"{leaf}.pyi"
    assert pyi.exists(), f"no {leaf}.pyi in {so_dir}"

    iso = Path(tempfile.mkdtemp())
    shutil.copy2(sos[0], iso / sos[0].name)
    shutil.copy2(pyi, iso / pyi.name)

    r = subprocess.run(
        [sys.executable, "-m", "mypy.stubtest", leaf],
        capture_output=True,
        text=True,
        timeout=600,
        cwd=str(iso),
        env={**os.environ, "PYTHONPATH": str(iso), "MYPYPATH": str(iso)},
    )
    # Both stubtest's own `error:` lines and the underlying `<file>: error:`
    # lines it prints when the stub is not even mypy-valid ("not checking stubs
    # due to mypy build errors") count as failures.
    return [ln for ln in r.stdout.splitlines() if "error:" in ln]


def _check(root: Path, so_dir: Path, leaf: str) -> None:
    _build(root)
    errs = _stubtest(so_dir, leaf)
    assert not errs, "stub does not match runtime:\n" + "\n".join(errs)


# ── shape builders ──────────────────────────────────────────────────────────
#
# Each returns (root, so_dir, leaf). A standalone object's module IS the
# component (so_dir=src/<pkg>/, leaf=<comp>); a module object's leaf is the
# module name (so_dir=src/<pkg>/<leaf>/).


def _pkg(tmp: Path) -> Path:
    return tmp / "proj"


def shape_standalone_state(tmp):
    d = _pkg(tmp)
    _q(new_run, "proj", d, ["osc"], [("gain", "double", "1.0")])
    return d, d / "src" / "proj", "osc"


def shape_module_state(tmp):
    d = _pkg(tmp)
    _q(new_run, "proj", d, [], [])
    _q(module_run, d, "widget", ["gizmo"])
    _q(
        object_run,
        d,
        "gizmo",
        module="widget",
        state_vars=[("gain", "double", "1.0")],
    )
    return d, d / "src" / "proj" / "widget", "widget"


def shape_standalone_method(tmp):
    d = _pkg(tmp)
    _q(new_run, "proj", d, ["osc"], [("gain", "double", "1.0")])
    _q(
        method_run,
        d,
        "osc",
        "tweak",
        None,
        "float",
        "float",
        False,
        [],
        params=[("k", "double")],
    )
    return d, d / "src" / "proj", "osc"


def shape_module_method(tmp):
    d = _pkg(tmp)
    _q(new_run, "proj", d, [], [])
    _q(module_run, d, "widget", ["gizmo"])
    _q(
        object_run,
        d,
        "gizmo",
        module="widget",
        state_vars=[("gain", "double", "1.0")],
    )
    _q(
        method_run,
        d,
        "gizmo",
        "tweak",
        "widget",
        "float",
        "float",
        False,
        [],
        params=[("k", "double")],
    )
    return d, d / "src" / "proj" / "widget", "widget"


def shape_standalone_property_computed(tmp):
    d = _pkg(tmp)
    _q(new_run, "proj", d, ["osc"], [("gain", "double", "1.0")])
    _q(property_run, d, "osc", "ready", None, "bool", False)
    # A computed property's accessor is the user's to implement (jm only
    # declares it), so supply a trivial body or the .so won't link.
    core = d / "native" / "src" / "osc" / "osc_core.c"
    core.write_text(
        core.read_text()
        + "\nbool osc_get_ready(const osc_state_t *state)\n"
        + "{ (void)state; return true; }\n"
    )
    return d, d / "src" / "proj", "osc"


def shape_standalone_function(tmp):
    d = _pkg(tmp)
    _q(new_run, "proj", d, [], [])
    _q(module_run, d, "widget", ["gizmo"])
    _q(
        object_run,
        d,
        "gizmo",
        module="widget",
        state_vars=[("gain", "double", "1.0")],
    )
    _q(
        function_run,
        d,
        "scale",
        "widget",
        params=[("x", "double")],
        return_type="double",
        impl_body="    return x * 2.0;",
    )
    return d, d / "src" / "proj" / "widget", "widget"


_SHAPES = {
    "standalone_state": shape_standalone_state,
    "module_state": shape_module_state,
    "standalone_method": shape_standalone_method,
    "module_method": shape_module_method,
    "standalone_property_computed": shape_standalone_property_computed,
    "module_function": shape_standalone_function,
}


@pytest.mark.skipif(bool(_SKIP), reason=_SKIP or "")
@pytest.mark.parametrize("name", list(_SHAPES))
def test_stub_matches_runtime(name, tmp_path):
    root, so_dir, leaf = _SHAPES[name](tmp_path)
    _check(root, so_dir, leaf)
