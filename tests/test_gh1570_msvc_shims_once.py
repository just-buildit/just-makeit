"""gh-1570: two jm packages' ``clib_common.h`` in one MSVC-ABI unit.

``clib_common.h`` carries, on the MSVC ABI only, ``static inline`` shims for
the complex functions the UCRT spells on structs (``jm_cabsf`` and the rest,
gh-1368). Each package ships its own copy under its own include guard, so a
consumer including two jm libraries -- gh-1583's side-by-side install --
defined every shim twice and failed to compile. Linux and macOS never see
it: there the block is a bare ``#include <complex.h>``. The clang-cl leg
found it the first time it ran gh-1583's consumer test.

This holds the fix on every leg: the two headers are compiled together by
clang with ``_MSC_VER`` defined, which takes the MSVC branch on any host. It
needs no MSVC: the shims use only clang builtins and ``<math.h>``.

GATE: a translation unit including two jm packages' ``clib_common.h`` on
      the MSVC branch compiles.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from _compilers import find_compiler
from just_makeit import _incpath as INC
from just_makeit._new import run as new_run


def test_two_packages_shims_compile_in_one_unit(tmp_path: Path) -> None:
    includes, inc_dirs = [], []
    for pkg in ("alpha", "beta"):
        proj = tmp_path / pkg
        new_run(pkg, proj, object_names=["gain"])
        spelled = INC.include("clib_common.h", proj)
        header = proj / INC.INC_DIR / spelled
        # Armed: the header carries the shims the MSVC branch defines, so
        # compiling it there is what this test is about.
        assert "jm_cabsf" in header.read_text(encoding="utf-8"), header
        includes.append(f'#include "{spelled}"\n')
        inc_dirs.append(f"-I{proj / INC.INC_DIR}")
    tu = tmp_path / "both.c"
    tu.write_text(
        "".join(includes)
        + "float f(float _Complex z) { return cabsf(z) + cargf(z); }\n",
        encoding="utf-8",
    )
    cc = find_compiler("clang")
    assert cc, "clang is on every CI leg; it is what takes the MSVC branch"
    r = subprocess.run(
        [cc, "-fsyntax-only", "-D_MSC_VER=1930", *inc_dirs, str(tu)],
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert r.returncode == 0, r.stderr
