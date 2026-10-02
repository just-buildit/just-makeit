"""Array dispatch scaffolds that build, and refuse bad input by name.

Three defects in one init path, each measured on a BUILT extension:

- gh-1825: dtype dispatch (``real_type`` / ``real_create_fn``) beside a
  defaulted array (``default = "[]"``) did not compile. The dispatch block
  called the constructor inside itself, before the defaulted array declared
  ``sync_arr`` / ``sync_len``::

      disp_ext.c:118:97: error: 'sync_arr' undeclared

- gh-1826: ``None`` or a 2-D array for the dispatched param raised
  ``SystemError: ... returned a result with an exception set``. The dtype
  probe (``PyArray_CheckFromAny``) failed and left its error set, and the
  complex acquisition then succeeded anyway (numpy makes ``None`` a 0-d nan).

- gh-1827: the scaffold never declared or stubbed ``real_create_fn`` or an
  ``optional`` array's ``create_fn``, so an untouched tree failed with an
  implicit declaration of the named function.

Every test here scaffolds through the CLI, on both faces (a standalone object
and a module object), builds, and calls the extension in a child Python. The
``-Werror`` half -- the same shapes building warning-clean untouched under gcc
and clang -- is in ``tests/test_preset_build.py``'s sweep.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import sysconfig
from pathlib import Path

import pytest

from _jmrun import run_cli

_NO_TOOLCHAIN = shutil.which("cmake") is None or not any(
    shutil.which(c) for c in ("cc", "gcc", "clang")
)

#: The dispatch object: complex taps by default, float32 taps select
#: `wp_disp_create_real`, and a defaulted array declared AFTER it (gh-1825).
DISPATCH = """
[disp]
arg_type = "void"
return_type = "void"
no_state = "true"
no_step = "true"

[[disp.init_params]]
name = "taps"
type = "float _Complex[]"
real_type = "float[]"
real_create_fn = "wp_disp_create_real"

[[disp.init_params]]
name = "sync"
type = "uint8_t[]"
default = "[]"
"""

_MODULE = '\n[module.m]\nobjects = ["disp"]\n'

FACES = {"standalone": "wp", "module": "wp.m"}


def _jm(*args, cwd: Path) -> None:
    r = run_cli(*args, cwd=cwd)
    assert r.returncode == 0, f"jm {' '.join(args)}\n{r.stdout}\n{r.stderr}"


def _build(root: Path) -> Path:
    """Configure and build *root* for THIS interpreter; the package dir."""
    build = root / "build"
    for cmd in (
        ["cmake", "-S", str(root), "-B", str(build)]
        + [f"-DPython3_EXECUTABLE={sys.executable}"],
        ["cmake", "--build", str(build)],
    ):
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
        assert r.returncode == 0, r.stdout[-4000:] + r.stderr[-4000:]
    ext = sysconfig.get_config_var("EXT_SUFFIX")
    so = sorted(root.rglob(f"*{ext}"))
    assert so, "no extension module was built"
    return root / "src"


def _outcomes(src: Path, module: str, cases: str) -> dict:
    """Run each ``name: expr`` in *cases* against *module*'s ``Disp``.

    A child process, because the extension is built for this interpreter
    but must be imported fresh -- and a SystemError must not be able to take
    the test process with it.
    """
    script = (
        "import json, sys\n"
        f"sys.path.insert(0, {str(src)!r})\n"
        "import numpy as np\n"
        f"from {module} import Disp\n"
        f"cases = {{{cases}}}\n"
        "out = {}\n"
        "for name, call in cases.items():\n"
        "    try:\n"
        "        call()\n"
        "        out[name] = 'ok'\n"
        "    except Exception as e:\n"
        "        out[name] = type(e).__name__ + ': ' + str(e)\n"
        "print(json.dumps(out))\n"
    )
    r = subprocess.run(
        [sys.executable, "-c", script], capture_output=True, text=True
    )
    assert r.returncode == 0, r.stderr[-3000:]
    return json.loads(r.stdout.strip().splitlines()[-1])


@pytest.mark.slow
@pytest.mark.skipif(_NO_TOOLCHAIN, reason="needs cmake and a C compiler")
@pytest.mark.parametrize("face", sorted(FACES))
def test_dispatch_beside_a_defaulted_array(face, tmp_path):
    """gh-1825 compiles, gh-1826 refuses by name, and the dtype routes.

    The real constructor's stub is made to fail, which is the only way to
    see from Python which constructor ran: a float32 array must reach it,
    and everything else -- a list of floats included -- must not.
    """
    root = tmp_path / "wp"
    _jm("new", "wp", str(root), cwd=tmp_path)
    manifest = root / "just-makeit.toml"
    with manifest.open("a", encoding="utf-8") as f:
        f.write((_MODULE if face == "module" else "") + DISPATCH)
    _jm("apply", cwd=root)

    core = root / "native" / "src" / "disp" / "disp_core.c"
    text = core.read_text(encoding="utf-8")
    # gh-1827: the scaffold stubbed it. Its marker names the dispatch.
    marker = (
        "    /* <<IMPLEMENT: initialise state when taps arrives as a float"
        " array (dtype dispatch) >> */\n"
    )
    assert text.count(marker) == 1, text
    core.write_text(
        text.replace(marker, "    free(obj);\n    return NULL;\n"),
        encoding="utf-8",
    )

    got = _outcomes(
        _build(root),
        FACES[face],
        """
        "complex64": lambda: Disp(np.zeros(3, np.complex64)),
        "complex64 + sync": lambda: Disp(
            np.zeros(3, np.complex64), sync=np.ones(4, np.uint8)
        ),
        "float list": lambda: Disp([1.0, 2.0]),
        "float32": lambda: Disp(np.zeros(3, np.float32)),
        "float32 + sync": lambda: Disp(
            np.zeros(3, np.float32), sync=np.ones(4, np.uint8)
        ),
        "None": lambda: Disp(None),
        "2-D float32": lambda: Disp(np.zeros((2, 3), np.float32)),
        "2-D complex64": lambda: Disp(np.zeros((2, 3), np.complex64)),
        "0-d float32": lambda: Disp(np.float32(1.0)),
        """,
    )
    assert not [k for k, v in got.items() if v.startswith("SystemError")], got
    for ok in ("complex64", "complex64 + sync", "float list"):
        assert got[ok] == "ok", (ok, got)
    for real in ("float32", "float32 + sync"):
        assert got[real].startswith("MemoryError"), (real, got)
    for bad in ("None", "2-D float32", "2-D complex64", "0-d float32"):
        assert got[bad] == "ValueError: taps must be a 1-D array", (bad, got)


@pytest.mark.slow
@pytest.mark.skipif(_NO_TOOLCHAIN, reason="needs cmake and a C compiler")
@pytest.mark.parametrize("face", sorted(FACES))
def test_optional_array_scaffold_builds_and_constructs(face, tmp_path):
    """gh-1827: an `optional` array's `create_fn`, through the CLI alone.

    Untouched, the tree builds and both constructors run: the scaffold
    declared `wp_disp_create_taps` in the header and stubbed it in
    `_core.c`, as it does create().
    """
    root = tmp_path / "wp"
    _jm("new", "wp", str(root), cwd=tmp_path)
    mod = ["--module", "m"] if face == "module" else []
    if mod:
        _jm("module", "m", cwd=root)
    _jm(
        "object", "disp", *mod, "--no-state", "--no-step",
        "--arg-type", "void", "--return-type", "void",
        "--init-param", "taps:float[]:optional:wp_disp_create_taps",
        "--init-param", "k:int:0",
        cwd=root,
    )  # fmt: skip
    header = root / "native" / "inc" / "wp" / "disp" / "disp_core.h"
    assert "wp_disp_create_taps(size_t taps_len, const float *taps" in (
        header.read_text(encoding="utf-8")
    )
    got = _outcomes(
        _build(root),
        FACES[face],
        """
        "omitted": lambda: Disp(k=2),
        "supplied": lambda: Disp(taps=np.zeros(3, np.float32), k=2),
        "None": lambda: Disp(taps=None),
        """,
    )
    assert got == {"omitted": "ok", "supplied": "ok", "None": "ok"}, got
