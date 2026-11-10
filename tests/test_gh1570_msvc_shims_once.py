"""gh-1570: two jm packages' ``clib_common.h`` in one MSVC-ABI unit.

``clib_common.h`` carries, on the MSVC ABI only, ``static inline`` shims for
the complex functions the UCRT spells on structs (``jm_cabsf`` and the rest,
gh-1368). Each package ships its own copy under its own include guard, so a
consumer including two jm libraries -- gh-1583's side-by-side install --
defined every shim twice and failed to compile. Linux and macOS never see
it: there the block is a bare ``#include <complex.h>``. The clang-cl leg
found it the first time it ran gh-1583's consumer test.

The two packages need not come from one jm. Each shim is therefore guarded
by itself, with the remap that routes the C99 name to it: one guard over
the whole set would let an older package included first hide a shim only a
newer one defines, leaving that name on the UCRT's struct-typed function.

This holds the fix on every leg: the headers are compiled together by clang
with ``_MSC_VER`` defined, which takes the MSVC branch on any host. It
needs no MSVC: the shims use only clang builtins and ``<math.h>``.

GATE: a translation unit including two jm packages' ``clib_common.h`` on
      the MSVC branch compiles.
GATE: when the package included first lacks a shim the second has, the
      unit still gets that shim and its remap.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

from _compilers import find_compiler
from just_makeit import _incpath as INC
from just_makeit._new import run as new_run

#: A shim as the header spells it: its own guard, the function, the remap.
_SHIM = re.compile(r"#ifndef JM_SHIM_(?P<name>\w+)\n.*?\n#endif\n", re.DOTALL)


def _packages(root: Path) -> "list[tuple[Path, str]]":
    """Two scaffolded packages: each header's path and its include line."""
    out = []
    for pkg in ("alpha", "beta"):
        proj = root / pkg
        new_run(pkg, proj, object_names=["gain"])
        spelled = INC.include("clib_common.h", proj)
        out.append((proj / INC.INC_DIR / spelled, spelled))
    return out


def _compile(root: Path, packages, body: str) -> "subprocess.CompletedProcess":
    tu = root / "both.c"
    tu.write_text(
        "".join(f'#include "{spelled}"\n' for _, spelled in packages) + body,
        encoding="utf-8",
    )
    cc = find_compiler("clang")
    assert cc, "clang is on every CI leg; it is what takes the MSVC branch"
    inc_dirs = [f"-I{root / pkg / INC.INC_DIR}" for pkg in ("alpha", "beta")]
    return subprocess.run(
        [cc, "-fsyntax-only", "-D_MSC_VER=1930", *inc_dirs, str(tu)],
        capture_output=True,
        text=True,
        timeout=120,
    )


def test_two_packages_shims_compile_in_one_unit(tmp_path: Path) -> None:
    packages = _packages(tmp_path)
    for header, _ in packages:
        # Armed: the header carries the shims the MSVC branch defines.
        assert "jm_cabsf" in header.read_text(encoding="utf-8"), header
    r = _compile(
        tmp_path,
        packages,
        "float f(float _Complex z) { return cabsf(z) + cargf(z); }\n",
    )
    assert r.returncode == 0, r.stderr


@pytest.mark.parametrize("shim", ["CABSF", "CEXP"])
def test_an_older_package_first_hides_no_shim(tmp_path: Path, shim) -> None:
    """An older jm stands in as a header with one shim deleted, included
    FIRST. The unit names the shim and requires the C99 name remapped."""
    packages = _packages(tmp_path)
    older, _ = packages[0]
    text = older.read_text(encoding="utf-8")
    found = {m.group("name"): m for m in _SHIM.finditer(text)}
    assert shim in found, sorted(found)
    m = found[shim]
    older.write_text(text[: m.start()] + text[m.end() :], encoding="utf-8")
    name = shim.lower()
    r = _compile(
        tmp_path,
        packages,
        f"#ifndef {name}\n#error {name} is not remapped\n#endif\n"
        f"void *p = (void *)&jm_{name};\n",
    )
    assert r.returncode == 0, r.stderr
