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

from just_makeit._apply import run as apply_run
from just_makeit._function import run as function_run
from just_makeit._method import run as method_run
from just_makeit._module import run as module_run
from just_makeit._new import run as new_run
from just_makeit._object import run as object_run
from just_makeit._property import run as property_run
from just_makeit._view import run as view_run


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


def _mod(tmp, obj="gizmo", state=(("gain", "double", "1.0"),), **objkw):
    """Scaffold a module `widget` with one object; return (root, so_dir, leaf)."""
    d = _pkg(tmp)
    _q(new_run, "proj", d, [], [])
    _q(module_run, d, "widget", [obj])
    _q(object_run, d, obj, module="widget", state_vars=list(state), **objkw)
    return d, d / "src" / "proj" / "widget", "widget"


def _append_core(root, comp, code):
    """Append a C body to a component's sacred `_core.c`."""
    core = root / "native" / "src" / comp / f"{comp}_core.c"
    core.write_text(core.read_text() + "\n" + code + "\n")


def _append_header(root, comp, code):
    """Append a declaration to a component's `_core.h`, before the include
    guard's closing `#endif` so a sibling `_ext` fragment sees it."""
    h = root / "native" / "inc" / comp / f"{comp}_core.h"
    text = h.read_text()
    idx = text.rfind("#endif")
    h.write_text(text[:idx] + code + "\n\n" + text[idx:])


def _write_enum(root, name, values):
    from just_makeit import _config as C

    vals = ", ".join(f'"{v}"' for v in values)
    with (root / C.FILENAME).open("a", encoding="utf-8") as fh:
        fh.write(f'\n[[enum]]\nname = "{name}"\nvalues = [{vals}]\n')
    _q(apply_run, root)


# ── property kinds ──────────────────────────────────────────────────────────


def shape_module_property_field(tmp):
    d, so, leaf = _mod(tmp)
    _q(
        property_run,
        d,
        "gizmo",
        "depth",
        "widget",
        "size_t",
        False,
        field=True,
    )
    return d, so, leaf


def shape_standalone_property_expr(tmp):
    d = _pkg(tmp)
    _q(new_run, "proj", d, ["osc"], [("gain", "double", "1.0")])
    # An `--expr` property is inlined into the _ext.c getter, whose state
    # pointer is `self->handle` (not a `state` alias).
    _q(
        property_run,
        d,
        "osc",
        "hot",
        None,
        "bool",
        False,
        expr="self->handle->gain > 0.0",
    )
    return d, d / "src" / "proj", "osc"


def shape_module_property_enum(tmp):
    d, so, leaf = _mod(tmp, state=[("mode", "int", "0")])
    _write_enum(d, "mode_kind", ["slow", "fast"])
    _q(
        property_run,
        d,
        "gizmo",
        "mode",
        "widget",
        "int",
        True,
        field=True,
        enum="mode_kind",
    )
    return d, so, leaf


# ── lifecycle ───────────────────────────────────────────────────────────────


def shape_module_no_reset(tmp):
    return _mod(tmp, no_reset=True)


def shape_module_streamable(tmp):
    # A generator object (void arg) is the natural streamable shape.
    return _mod(
        tmp, arg_type="void", return_type="float _Complex", streamable=True
    )


def shape_module_async_stream(tmp):
    return _mod(
        tmp,
        arg_type="void",
        return_type="float _Complex",
        streamable=True,
        async_stream=True,
    )


def shape_module_serializable(tmp):
    d, so, leaf = _mod(tmp, serializable=True)
    # serializable generates the state-blob binding but calls a hand-written
    # triplet (gh-400) the user declares in the sacred header and implements.
    _append_header(
        d,
        "gizmo",
        "size_t gizmo_state_bytes(const gizmo_state_t *s);\n"
        "void gizmo_get_state(const gizmo_state_t *s, char *out);\n"
        "int gizmo_set_state(gizmo_state_t *s, const char *in);",
    )
    _append_core(
        d,
        "gizmo",
        "size_t gizmo_state_bytes(const gizmo_state_t *s)\n"
        "{ (void)s; return sizeof(double); }\n"
        "void gizmo_get_state(const gizmo_state_t *s, char *out)\n"
        "{ *(double *)out = s->gain; }\n"
        "int gizmo_set_state(gizmo_state_t *s, const char *in)\n"
        "{ s->gain = *(const double *)in; return 0; }",
    )
    return d, so, leaf


# ── methods ─────────────────────────────────────────────────────────────────


def shape_module_method_out_type(tmp):
    d, so, leaf = _mod(tmp)
    _q(
        method_run,
        d,
        "gizmo",
        "render",
        "widget",
        "void",
        "void",
        False,
        [],
        params=[("n", "size_t")],
        out_type="float _Complex",
    )
    return d, so, leaf


def shape_module_method_variable_output(tmp):
    d, so, leaf = _mod(tmp)
    _q(
        method_run,
        d,
        "gizmo",
        "emit",
        "widget",
        "void",
        "float _Complex",
        True,
        [],
        params=[("n", "size_t")],
    )
    # jm stubs both the kernel and <comp>_<name>_max_out() in _core.c, so the
    # scaffold links as-is (the gate imports the module, it does not call it).
    return d, so, leaf


# ── ctor / state ────────────────────────────────────────────────────────────


def shape_module_init_params_path(tmp):
    # doppler's reader shape: no_state + a path init-param.
    return _mod(
        tmp,
        state=[],
        no_state=True,
        init_params=[("path", "path", "")],
    )


def shape_module_state_plus_initparams(tmp):
    return _mod(
        tmp,
        state=[("cap", "size_t", "16")],
        init_params=[("path", "path", "")],
    )


def shape_module_view(tmp):
    # A view is a second class over the shared core, built by a create_fn.
    d, so, leaf = _mod(tmp)
    _q(view_run, d, "gizmo", "Peek", "widget", "gizmo_make_peek")
    # jm declares and stubs the create_fn body, so the scaffold links as-is
    # (the gate imports the module, it does not construct the view).
    return d, so, leaf


def shape_standalone_array_state(tmp):
    d = _pkg(tmp)
    _q(
        new_run,
        "proj",
        d,
        ["fir"],
        [("taps", "double[4]", ""), ("gain", "double", "1.0")],
    )
    return d, d / "src" / "proj", "fir"


_SHAPES = {
    "standalone_state": shape_standalone_state,
    "module_state": shape_module_state,
    "standalone_method": shape_standalone_method,
    "module_method": shape_module_method,
    "standalone_property_computed": shape_standalone_property_computed,
    "module_function": shape_standalone_function,
    # batch 2
    "module_property_field": shape_module_property_field,
    "standalone_property_expr": shape_standalone_property_expr,
    "module_property_enum": shape_module_property_enum,
    "module_no_reset": shape_module_no_reset,
    "module_streamable": shape_module_streamable,
    "module_async_stream": shape_module_async_stream,
    "module_serializable": shape_module_serializable,
    "module_method_out_type": shape_module_method_out_type,
    "module_method_variable_output": shape_module_method_variable_output,
    "module_init_params_path": shape_module_init_params_path,
    "module_state_plus_initparams": shape_module_state_plus_initparams,
    "module_view": shape_module_view,
    "standalone_array_state": shape_standalone_array_state,
}


@pytest.mark.skipif(bool(_SKIP), reason=_SKIP or "")
@pytest.mark.parametrize("name", list(_SHAPES))
def test_stub_matches_runtime(name, tmp_path):
    root, so_dir, leaf = _SHAPES[name](tmp_path)
    _check(root, so_dir, leaf)
