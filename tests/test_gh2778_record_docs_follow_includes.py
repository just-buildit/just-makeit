"""A record's field docs come from the header that declares its struct (gh-2778).

A ``single`` record whose ``result_fields`` row has no ``doc`` falls back to
its struct's member doc (gh-1300). For a module function (gh-2755) that
fallback read only the module's own header. doppler's ``ccsds`` module
returns ``dp_ccsds_415_scene_t``, declared in ``ccsds/ccsds_415.h``, which
``ccsds_core.h`` includes: every field rendered undocumented, in the
``.pyi`` and at runtime, whichever of the three Doxygen member styles the
struct used.

An object's header already followed its project includes (gh-724); the
module's loader carried its own copy of the struct-doc merge and did not.
Both now read through ``_object._struct_member_blocks``.

GATE. The issue's shape, built through the real tool: a module function and
a method, each returning a struct declared in a second header that the
module header (and the object header) includes, one field per doc style
plus one whose manifest ``doc`` must win over the header's. Every field's
doc is asserted in the ``.pyi`` and in the compiled type's ``__doc__``.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from _compilers import default_cc
from _jmrun import run_cli


def _skip_reason() -> "str | None":
    if not shutil.which("cmake"):
        return "cmake not found"
    if default_cc() is None:
        return "no C compiler found"
    try:
        import numpy  # noqa: F401
    except ImportError:
        return "numpy not importable"
    return None


_SKIP = _skip_reason()

#: The struct's own header: one field per member-doc style the issue tried,
#: and a fourth documented here AND in the manifest. The angle include is a
#: system header, never followed.
SCENE_H = """\
#ifndef P_CCSDS_CCSDS_415_H
#define P_CCSDS_CCSDS_415_H

#include <stddef.h>

/** The scene's derived numbers. */
typedef struct {
  /** Chips per second. */
  double chip_rate;
  double ratio;  /**< Samples per chip, doubled. */
  double start_ppm;  ///< Starting offset, ppm.
  double rate_ppm_s;  /**< Header doc the manifest overrides. */
} dp_ccsds_415_scene_t;

#endif
"""

#: field -> the doc it must carry.
DOCS = {
    "chip_rate": "Chips per second.",
    "ratio": "Samples per chip, doubled.",
    "start_ppm": "Starting offset, ppm.",
    "rate_ppm_s": "Drift, ppm per second.",
}

FIELDS = """\
result_fields = [
  { name = "chip_rate", type = "double" },
  { name = "ratio", type = "double" },
  { name = "start_ppm", type = "double" },
  { name = "rate_ppm_s", type = "double", doc = "Drift, ppm per second." },
]
"""

FRAGMENT = (
    '''\
[module.ccsds]
objects = ["fir"]

[[module.ccsds.functions]]
name = "ccsds415_scene"
return_type = "dp_ccsds_415_scene_t"
single = true
record_name = "Ccsds415Scene"
impl = """
dp_ccsds_415_scene_t s = {fc / spc, spc * 2.0, start_hz / fc * 1e6, -1.5};
return s;
"""
params = [
  { name = "fc", type = "double" },
  { name = "spc", type = "double" },
  { name = "start_hz", type = "double" },
]
'''
    + FIELDS
    + """
[[fir.methods]]
name = "scene"
return_type = "dp_ccsds_415_scene_t"
single = true
record_name = "FirScene"
"""
    + FIELDS
)

INCLUDE = '#include "p/ccsds/ccsds_415.h"\n'
PYI = "src/p/ccsds/ccsds.pyi"


def _include(path: Path) -> None:
    """Make *path* include the scene header, after its first include."""
    text = path.read_text("utf-8")
    at = text.index("#include")
    path.write_text(text[:at] + INCLUDE + text[at:], "utf-8")


@pytest.fixture(scope="module")
def applied(tmp_path_factory) -> Path:
    base = tmp_path_factory.mktemp("gh2778")
    r = run_cli("new", "p", "--no-c-prefix", cwd=base)
    assert r.returncode == 0, r.stderr
    dest = base / "p"
    frag = base / "frag.toml"
    frag.write_text(FRAGMENT, encoding="utf-8")
    r = run_cli("apply", str(frag), cwd=dest)
    assert r.returncode == 0, r.stdout + r.stderr
    inc = dest / "native/inc/p"
    (inc / "ccsds/ccsds_415.h").write_text(SCENE_H, "utf-8")
    # The module header includes the struct's header, as doppler's does;
    # the object header too, so the method's record can name the type.
    _include(inc / "ccsds/ccsds_core.h")
    _include(inc / "fir/fir_core.h")
    # The glue is re-derived from the headers the author has now written.
    r = run_cli("apply", cwd=dest)
    assert r.returncode == 0, r.stdout + r.stderr
    return dest


@pytest.fixture(scope="module")
def built(applied):
    if _SKIP:
        pytest.skip(_SKIP)
    build = applied / "build"
    for cmd in (
        [
            "cmake",
            "-S",
            str(applied),
            "-B",
            str(build),
            f"-DPython3_EXECUTABLE={sys.executable}",
        ],
        ["cmake", "--build", str(build)],
    ):
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
        assert r.returncode == 0, f"{cmd[:2]}:\n{r.stdout}\n{r.stderr}"
    return applied


def _stub_class(pyi: str, name: str) -> str:
    start = pyi.index(f"class {name}(")
    end = pyi.find("\nclass ", start + 1)
    return pyi[start : end if end > 0 else len(pyi)]


@pytest.mark.parametrize("record", ["Ccsds415Scene", "FirScene"])
def test_the_stub_documents_every_field(applied, record):
    cls = _stub_class((applied / PYI).read_text("utf-8"), record)
    for field, doc in DOCS.items():
        assert doc in cls, (field, doc, cls)
    assert "Header doc the manifest overrides." not in cls, cls


@pytest.mark.parametrize("record", ["Ccsds415Scene", "FirScene"])
def test_the_runtime_documents_every_field(built, record):
    code = (
        "import p.ccsds as m\n"
        f"t = m.{record}\n"
        f"for f in {list(DOCS)!r}:\n"
        "    print(f, '=', getattr(t, f).__doc__)\n"
    )
    r = subprocess.run(
        [sys.executable, "-c", code],
        cwd=built,
        env={**os.environ, "PYTHONPATH": str(built / "src")},
        capture_output=True,
        text=True,
        timeout=300,
    )
    assert r.returncode == 0, r.stdout + r.stderr
    assert r.stdout.splitlines() == [f"{f} = {d}" for f, d in DOCS.items()], (
        r.stdout
    )


def test_the_function_call_still_returns_the_record(built):
    r = subprocess.run(
        [
            sys.executable,
            "-c",
            "import p.ccsds as m; print(m.ccsds415_scene(1e6, 4.0, 10.0))",
        ],
        cwd=built,
        env={**os.environ, "PYTHONPATH": str(built / "src")},
        capture_output=True,
        text=True,
        timeout=300,
    )
    assert r.returncode == 0, r.stdout + r.stderr
    assert "chip_rate=250000.0" in r.stdout, r.stdout
